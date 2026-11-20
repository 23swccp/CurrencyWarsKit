import unittest
from unittest.mock import Mock, call, patch

from flow.navigation import Navigator, current_screen


class NavigationTests(unittest.TestCase):
    def navigator(self, observations):
        nav = Navigator.__new__(Navigator)
        nav.clicked = False
        nav.session = Mock()
        nav.session.capture.return_value = {"screenshot": "test.png"}
        nav.session.save_image.return_value = {"screenshot": "after-esc.png"}
        nav.session.controller.post_click_key.return_value.wait.return_value.succeeded = True
        nav.capture_image = Mock(return_value="after-esc-image")
        nav.texts = Mock(return_value=["开始对局"])
        nav.observe = Mock(side_effect=observations)
        nav.click = Mock()
        nav.opening = Mock(return_value={"screen": "投资环境", "needs_ai": True})
        return nav

    def test_home_to_standard(self):
        nav = self.navigator([(["开始货币战争】"], []), (["进入标准博弈"], ["标准博弈"])])
        nav.enter_rank = Mock(return_value={"screen": "标准博弈入口"})
        self.assertEqual(nav.run()["screen"], "标准博弈入口")
        nav.click.assert_called_once_with(1590, 973)

    def test_other_mode_selects_standard(self):
        nav = self.navigator([(["进入超频博弈"], ["标准博弈"]), (["进入标准博弈"], ["标准博弈"])])
        nav.enter_rank = Mock(return_value={"screen": "标准博弈入口"})
        self.assertEqual(nav.run()["screen"], "标准博弈入口")
        nav.click.assert_called_once_with(260, 260)

    def test_unrecognized_button_does_not_select_mode(self):
        nav = self.navigator([([], ["标准博弈"])])
        self.assertEqual(nav.run()["result"], "uncertain")
        nav.click.assert_not_called()

    def test_rank_reset_then_start(self):
        nav = self.navigator([(["开始对局"], []), (["返回最高职级", "开始对局"], []),
                              (["开始对局"], [])])
        with patch("flow.navigation.sleep") as sleep:
            result = nav.run()
        self.assertEqual(result["screen"], "投资环境")
        sleep.assert_called_once_with(5)
        nav.opening.assert_called_once_with()
        self.assertEqual(nav.click.call_args_list, [call(960, 843), call(1380, 966), call(1690, 966)])

    def test_missing_return_button_does_not_start(self):
        nav = self.navigator([(["开始对局"], []), (["开始对局"], [])])
        with patch("flow.navigation.monotonic", side_effect=[0, 9]):
            result = nav.run()
        self.assertIn("下移后", result["message"])
        self.assertEqual(result["screen"], "职级选择")
        self.assertEqual(result["screenshot"], "after-esc.png")
        nav.session.controller.post_click_key.assert_called_once_with(0x1B)
        nav.click.assert_called_once_with(960, 843)

    def test_each_navigation_timeout_escapes_once_and_returns_new_page(self):
        scenarios = [
            [(["开始货币战争"], []), ([], [])],
            [(["进入超频博弈"], ["标准博弈"]), ([], [])],
            [(["开始对局"], []), ([], [])],
        ]
        for observations in scenarios:
            with self.subTest(observations=observations):
                nav = self.navigator(observations)
                nav.texts.return_value = ["本场对局首领", "下一步"]
                with patch("flow.navigation.monotonic", side_effect=[0, 9]):
                    result = nav.run()
                self.assertEqual(result["screen"], "首领介绍")
                self.assertEqual(result["result"], "uncertain")
                nav.session.controller.post_click_key.assert_called_once_with(0x1B)
                nav.session.save_image.assert_called_once_with("after-esc-image")
                nav.opening.assert_not_called()

    def test_text_timeout_escapes_once_without_selecting_option(self):
        nav = self.navigator([])
        nav.texts.side_effect = [[], ["无法确认的文字"]]
        with patch("flow.navigation.monotonic", side_effect=[0, 9]):
            result = nav.run(1)
        self.assertEqual(result["screen"], "未知")
        self.assertEqual(result["texts"], ["无法确认的文字"])
        nav.session.controller.post_click_key.assert_called_once_with(0x1B)
        nav.click.assert_not_called()

    def test_failed_escape_is_not_repeated_or_reported_as_a_page(self):
        from game.input import InputUncertain
        nav = self.navigator([(["开始对局"], []), ([], [])])
        nav.session.controller.post_click_key.return_value.wait.return_value.succeeded = False
        with patch("flow.navigation.monotonic", side_effect=[0, 9]):
            with self.assertRaises(InputUncertain):
                nav.run()
        nav.session.controller.post_click_key.assert_called_once_with(0x1B)
        nav.capture_image.assert_not_called()

    def test_known_page_and_unknown_page_are_distinct(self):
        self.assertEqual(current_screen(["投资环境", "确认"]), "投资环境")
        self.assertEqual(current_screen(["下一步"]), "未知")

    def test_enter_standard_waits_for_rank_page(self):
        nav = self.navigator([(["进入标准博弈"], ["标准博弈"]), (["开始对局"], []),
                              (["返回最高职级", "开始对局"], []), (["开始对局"], [])])
        with patch("flow.navigation.sleep"):
            self.assertEqual(nav.run()["screen"], "投资环境")
        self.assertEqual(nav.click.call_args_list[0], call(1634, 960))

    def test_opening_stops_at_options_without_choosing(self):
        nav = self.navigator([])
        nav.wait_text = Mock(return_value="image")
        nav.read = Mock()
        nav.equipment = Mock()
        nav.session.save_image.return_value = {"screenshot": "same-frame.png"}
        with patch("flow.navigation.sleep") as sleep, patch("screens.environment.recognize", return_value={"options": [{}, {}, {}], "needs_ai": True}) as recognize:
            result = Navigator.opening(nav)
        sleep.assert_called_once_with(3)
        self.assertTrue(result["needs_ai"])
        self.assertEqual(nav.click.call_args_list, [call(1400, 984), call(960, 965)])
        recognize.assert_called_once_with("image", nav.read, nav.equipment)
        nav.session.save_image.assert_called_once_with("image")

    def test_environment_waits_for_all_cards_and_returns_one_frame(self):
        nav = self.navigator([])
        nav.read = Mock()
        nav.equipment = Mock()
        nav.capture_image.side_effect = ["partial-frame", "ready-frame"]
        ready = {"options": [{"texts": ["名称", "描述"], "equipment": [{"unknown": True}]}] * 3}
        with patch("flow.navigation.sleep"), patch("screens.environment.recognize", side_effect=[
                {"options": [{"unknown": True}] * 3},
                {"options": [{}, {}, {"unknown": True}]}, ready]) as recognize:
            result = nav.read_environment("title-frame")
        self.assertEqual(result["options"], ready["options"])
        self.assertEqual([args.args[0] for args in recognize.call_args_list],
                         ["title-frame", "partial-frame", "ready-frame"])
        nav.session.save_image.assert_called_once_with("ready-frame")
        nav.click.assert_not_called()
        nav.session.controller.post_click_key.assert_not_called()

    def test_environment_text_timeout_escapes_once_and_uses_last_frame(self):
        nav = self.navigator([])
        nav.read = Mock()
        nav.equipment = Mock()
        nav.capture_image.side_effect = ["old-frame", "transition-frame", "new-frame"]
        nav.texts.return_value = ["进入标准博弈"]
        with patch("flow.navigation.sleep"), patch("flow.navigation.monotonic", side_effect=[0, 9]), \
                patch("screens.environment.recognize", return_value={"options": [{"unknown": True}] * 3}):
            from flow.navigation import NavigationStopped
            with self.assertRaises(NavigationStopped) as stopped:
                nav.read_environment("title-frame")
        self.assertEqual(stopped.exception.result["screen"], "模式选择")
        nav.texts.assert_called_once_with("new-frame", (35, 55, 1850, 980))
        nav.session.save_image.assert_called_once_with("new-frame")
        nav.session.controller.post_click_key.assert_called_once_with(0x1B)
        nav.click.assert_not_called()

    def test_navigate_reads_current_environment_without_clicking(self):
        nav = self.navigator([([], [])])
        nav.texts.return_value = ["投资环境"]
        nav.read_environment = Mock(return_value={"screen": "投资环境", "needs_ai": True})
        self.assertEqual(nav.run()["screen"], "投资环境")
        nav.read_environment.assert_called_once_with("after-esc-image")
        nav.click.assert_not_called()
        nav.session.controller.post_click_key.assert_not_called()

    def test_escape_capture_failure_stops_without_more_input(self):
        from game.input import InputUncertain
        nav = self.navigator([(["开始对局"], []), ([], [])])
        nav.capture_image.side_effect = ["old-frame", RuntimeError("第二次截图失败")]
        with patch("flow.navigation.sleep"), patch("flow.navigation.monotonic", side_effect=[0, 9]):
            with self.assertRaisesRegex(InputUncertain, "第二次截图失败"):
                nav.run()
        nav.session.controller.post_click_key.assert_called_once_with(0x1B)
        nav.session.save_image.assert_not_called()

    def test_environment_choice_confirms_then_waits_and_dismisses_intro(self):
        nav = self.navigator([])
        nav.wait_text = Mock()
        steps = Mock()
        steps.attach_mock(nav.wait_text, "wait_text")
        steps.attach_mock(nav.click, "click")
        steps.attach_mock(nav.session.capture, "capture")
        with patch("flow.navigation.sleep") as sleep:
            steps.attach_mock(sleep, "sleep")
            result = nav.run(2)
        self.assertEqual(result["option"], 2)
        self.assertEqual(result["result"], "uncertain")
        self.assertEqual(result["screenshot"], "test.png")
        self.assertEqual(steps.mock_calls, [
            call.wait_text((850, 65, 240, 75), "投资环境"),
            call.click(960, 530),
            call.wait_text((770, 950, 385, 75), "确认"),
            call.click(960, 984),
            call.sleep(4),
            call.click(100, 1000),
            call.capture(),
        ])

    def test_environment_confirm_failure_stops_before_dismissing_intro(self):
        from game.input import InputUncertain
        nav = self.navigator([])
        nav.wait_text = Mock()
        nav.click.side_effect = [None, InputUncertain("确认点击失败")]
        with patch("flow.navigation.sleep") as sleep:
            with self.assertRaisesRegex(InputUncertain, "确认点击失败"):
                nav.run(2)
        sleep.assert_not_called()
        self.assertEqual(nav.click.call_args_list, [call(960, 530), call(960, 984)])
        nav.session.capture.assert_not_called()


if __name__ == "__main__":
    unittest.main()
