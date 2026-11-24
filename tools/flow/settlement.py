"""整局结算：三段短 Pipeline 点完固定页面，Python 读取结算数据。"""

import re

import numpy as np
from maa.resource import Resource
from maa.tasker import Tasker
from PIL import Image

from game.input import ERRORS, InputUncertain, uncertain
from screens.preparation import ASSETS


CONTINUE = "settlement_continue.png"
NEXT = "settlement_next.png"
PAGE = "settlement_page.png"
RETURN = "settlement_return.png"
HOME = "settlement_home.png"
TEMPLATES = (CONTINUE, NEXT, PAGE, RETURN, HOME)
STRIP = (700, 850, 520, 145)
HOME_STRIP = (1380, 900, 480, 120)
SCORE = 0.85
POST_DELAY = 800
WAIT = 8000
CONTINUE_AT = (960, 895)
NEXT_AT = (960, 951)
PAGE_AT = (960, 899)
RATING = (330, 250, 340, 170)
SUMMARY_RANK = (760, 178, 390, 45)
SUMMARY_HEALTH = (1380, 178, 340, 45)
SUMMARY_LEVEL = (320, 588, 260, 50)
SUMMARY_MATERIAL = (270, 650, 480, 55)
SUMMARY_POINTS = (270, 718, 480, 50)
DAMAGE_HEALTH = (300, 182, 460, 52)
DAMAGE_ECONOMY = (740, 182, 470, 52)
DAMAGE_VALUE = (1180, 182, 460, 52)
HOME_LEVEL = (1500, 170, 260, 100)


def order(text):
    return re.sub(r"\s+", "", text)


def digits(text):
    match = re.search(r"\d[\d,]*", text)
    return int(match.group().replace(",", "")) if match else None


def node(template, roi, target=None, following=None):
    """识别按钮模板；给出点击目标时按顺序进入下一个节点。"""
    entry = {"recognition": {"type": "TemplateMatch", "param": {
        "template": [template], "roi": list(roi), "threshold": [SCORE], "method": 5}},
        "timeout": WAIT}
    if target is not None:
        entry.update(action={"type": "Click", "param": {"target": [target[0], target[1], 1, 1]}},
                     post_delay=POST_DELAY, next=[following])
    return entry


def summary_pipeline():
    """挑战成功 → 职级提升 → 整局结算页。"""
    return {"Challenge": node(CONTINUE, STRIP, CONTINUE_AT, "Promotion"),
            "Promotion": node(NEXT, STRIP, NEXT_AT, "Summary"),
            "Summary": node(PAGE, STRIP)}


def damage_pipeline():
    """整局结算页 → 伤害统计页。"""
    return {"Summary": node(PAGE, STRIP, PAGE_AT, "Damage"),
            "Damage": node(RETURN, STRIP)}


def home_pipeline():
    """伤害统计页 → 货币战争首页。"""
    return {"Damage": node(RETURN, STRIP, PAGE_AT, "Home"),
            "Home": node(HOME, HOME_STRIP)}


def tasker_for(controller):
    resource = Resource()
    for name in TEMPLATES:
        with Image.open(ASSETS / "settlement" / name) as source:
            resource.override_image(name, np.asarray(source.convert("RGB"))[:, :, ::-1].copy())
    tasker = Tasker()
    if not tasker.bind(resource, controller) or not tasker.inited:
        raise RuntimeError("结算 Pipeline 初始化失败")
    return tasker


def run(tasker, entry, pipeline):
    job = tasker.post_task(entry, pipeline).wait()
    detail = job.get()
    nodes = [(item.name, item.completed) for item in detail.nodes] if detail else None
    # 节点带动作才说明点击已发出，用于区分「没到结算页」和「点了但没到下一页」。
    clicked = detail is not None and any(item.action is not None for item in detail.nodes)
    return {"succeeded": job.succeeded, "clicked": clicked, "nodes": nodes}


def read_summary(image, reader):
    return {"rating": order(reader.text(image, "评价", RATING)),
            "rank": order(reader.text(image, "晋升职级", SUMMARY_RANK)).replace("职级晋升", ""),
            "health": digits(reader.text(image, "小队生命值", SUMMARY_HEALTH)),
            "level": digits(reader.text(image, "晋升等级", SUMMARY_LEVEL)),
            "materials": digits(reader.text(image, "获取材料", SUMMARY_MATERIAL)),
            "points": digits(reader.text(image, "晋升点", SUMMARY_POINTS))}


def read_damage(image, reader):
    return {"health": digits(reader.text(image, "小队生命值", DAMAGE_HEALTH)),
            "economy": digits(reader.text(image, "总经济", DAMAGE_ECONOMY)),
            "team_value": digits(reader.text(image, "阵容价值", DAMAGE_VALUE))}


def read_home(image, reader):
    return {"home_level": reader.integer(image, "首页晋升等级", HOME_LEVEL)}


def settle(session):
    from screens.battle import settlement_details

    reader = session.preparation_reader()
    reader.load_ocr()
    image = session.preparation_frame()
    challenge_image = image
    steps = []
    data = {}
    clicked = False
    try:
        tasker = tasker_for(session.controller)
        result = None
        for label, entry, pipeline, read in (
                ("整局结算", "Challenge", summary_pipeline(), read_summary),
                ("伤害统计", "Summary", damage_pipeline(), read_damage),
                ("货币战争首页", "Damage", home_pipeline(), read_home)):
            task = run(tasker, entry, pipeline)
            clicked = clicked or task["clicked"]
            if not task["succeeded"]:
                result = {"result": "uncertain" if clicked else "failed", "needs_ai": True,
                          "nodes": task["nodes"], "message": f"未能到达{label}页面"}
                break
            if entry == "Challenge":
                details = settlement_details(challenge_image, reader)
                challenge = session.save_image(challenge_image)
                session.battle_reader().settlement_receipt(challenge["screenshot"], details["outcome"], details["health"],
                                                           final=True)
                session.battle_reader().end_battle()
            steps.append(label)
            image = session.preparation_frame()
            data.update(read(image, reader))
            if entry == "Challenge":
                session.battle_reader().final_health(data["health"])
        if result is None:
            result = {"result": "success", "needs_ai": False, "screen": "货币战争首页",
                      "message": "已确认整局结算并回到货币战争首页"}
    except ERRORS as error:
        message = f"整局结算出错：{type(error).__name__}: {error}"
        try:
            image = session.preparation_frame()
        # 防：点击后再次截图失败，保留最近画面和错误。
        except ERRORS as capture_error:
            message += f"；补充截图失败：{type(capture_error).__name__}: {capture_error}"
        result = {"result": "uncertain", "needs_ai": True, "message": message}
    payload = {**data, **result, "action": "settle", "steps": steps}
    try:
        return {**session.save_image(image), **payload}
    except ERRORS as error:
        if clicked:
            raise InputUncertain(f"结算后保存截图失败：{type(error).__name__}: {error}") from error
        raise
