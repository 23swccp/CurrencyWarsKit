# 设计图

设计页用 Mermaid 展示操作顺序、分流和结果核对。命令参数、识别区域、数据字段与实机依据保留在图旁。

| 命令 | 图 | 关注的问题 |
|---|---|---|
| 备战 | [备战阶段](preparation.md) | 确认阶段、领取奖励、观察局势、AI 决策和出战的先后顺序 |
| 右侧行动按钮 | [按钮与动作映射](actions.md) | 共用单帧 OCR、已知入口分流和未知 buff 名称 |
| `prepare`、`act continue-battle` | [统一备战入口](prepare.md) | 战后等待、确认重试、收起商店、收集与选择恢复 |
| `act sell`、`act shop-open`、`act shop-close`、`rewards` | [后台补领](prepare.md#后台补领与-ai-决策) | 出售腾位、商店暂停与恢复、前台输入互斥及补领结果 |
| `observe`、`act trail-shop-open/buy/close` | [开拓商店](trail-shop.md) | 开拓力、四列商品和付费换货核对 |
| `observe`、`act refresh` | [商店刷新](preparation.md#免费与付费刷新) | 免费次数与金币费用分读、单次点击及结果验证 |
| `observe` | [投资策略](strategy.md) | 现场读取三项、单项刷新、选择确认及后续界面 |
| `observe`、`act supply`、`act supply-refresh` | [补给选择](supply.md) | 五列选项、可选刷新、单选计数验证与备战衔接 |
| `observe`、`act armament-open`、`act armament-choose` | [武装箱](armament.md) | 识别箱卡、读取四选一与核对库存到账 |
| `observe`、`act encounter`、`act encounter-refresh` | [遭遇节点](encounter.md) | 难度和奖励读取、整体刷新、选择确认与返回备战 |
| 选择主角 | [主角与光环](protagonist.md) | 1-9、2-9 的角色拖选、两侧光环、付费刷新与完成依据 |
| 双龙吟选择（设计） | [双龙吟](double-dragons.md) | 丹恒•饮月上阵后的单选、确认和加入结果核对 |
| 共舞选择（设计） | [梦境交织](dreamweave.md) | 自动选中两张卡、通过左侧羁绊行核对结果 |
| 备战羁绊（设计） | [羁绊识别](traits.md) | 同帧读取左侧名称、人数、档位与连携图标 |
| `act start-battle`、`observe` | [战斗阶段](battle.md) | 入场确认、旗帜监测与五秒结算检查 |
| `buy` | [购买角色](buy.md) | 命令时序、商店展开状态、单次购买的检查与判定 |
| `act buy-experience --xp N` | [购买经验](experience.md) | 批量购买、逐次核对扣款与经验、部分完成时的返回 |
| 装备合成（设计） | [装备合成](equipment-synthesis.md) | 配方与产物预览、指定材料拖动、结果核验 |

这里只记设计：实现到哪一步、哪些经过实机验证，以 `PROGRESS.md` 为准。
