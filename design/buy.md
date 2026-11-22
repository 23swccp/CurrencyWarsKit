# 购买角色

调用方指定当前商店的一个槽位。服务按最近一次观察检查条件，最多点击一次，再用购前与购后的画面证据判定结果。`buy` 尚未实现。

## 命令时序

`cw see`、`cw buy shop_1` 是拟议命令；当前 CLI 用 `screenshot` 取截图，`buy` 接口待实现。

```mermaid
sequenceDiagram
    participant AI as 调用方 AI
    participant CLI as cw CLI
    participant S as Python 常驻服务
    participant M as MaaFramework

    AI->>CLI: cw see（拟议）
    CLI->>S: 本机 HTTP · 请求观察
    S->>M: 截取游戏窗口并识别
    M-->>S: 截图与识别结果
    S->>S: 保存最近一次观察
    S-->>CLI: 观察数据
    CLI-->>AI: 输出观察 JSON

    AI->>CLI: cw buy shop_1（拟议）
    CLI->>S: 本机 HTTP · 购买请求（目标槽位 shop_1）
    S->>S: 从最近观察读取目标与购前数据
    alt 购买条件已确认
        S->>M: 执行一次购买点击并观察购后画面
        M-->>S: 可用的购后截图与识别结果
        S->>S: 判定购买结果，更新可用观察
    else 购买条件不满足或无法确认
        S->>S: 记录未点击的原因
    end
    S-->>CLI: 动作结果与可用观察
    CLI-->>AI: 输出结果 JSON
```

| 数据 | 流向 | 形态 |
|---|---|---|
| 购买意图 | AI → CLI → 服务 | 调用方给出目标槽位，经本机 HTTP 传给服务 |
| 游戏画面 | 窗口 → MaaFramework → 服务 | 截图存 `.runtime/screenshots/`，结构化观察拟在会话中保留 |
| 动作结果 | 服务 → CLI → AI | JSON，判定规则见下文 |

会话保留窗口连接、最近截图尺寸和操作锁，购买请求复用这份互斥边界。

## 商店展开状态

收起与展开两种布局的中央槽位位置和大小不同，箭头为预期的操作方向。

```mermaid
stateDiagram-v2
    state "备战 · 商店收起" as Closed
    state "备战 · 商店展开" as Open

    Closed --> Open: 打开商店，观察确认展开
    Open --> Closed: 收起商店，观察确认收起

    note right of Open
        读取 shop_1 至 shop_5
        目标与购买条件确认后可尝试购买
    end note
```

| 当前画面 | 识别依据 | 与购买的关系 |
|---|---|---|
| 商店收起 | 可见商店入口，上方无商店卡片 | 尚无可购买的商店目标，需先打开商店 |
| 商店展开 | 上方出现商店区域，入口文字变为「收起」 | 按展开布局从左到右读取 `shop_1` 至 `shop_5` |

槽位名指当前画面里的位置，具体角色以该次观察为准。购买后的界面由新观察确定。

商店展开时，`observe`、`act shop-open` 和确认成功的 `act refresh` 从返回截图中读取五格商店，槽位名从左至右为 `shop_1` 至 `shop_5`。

| 字段 | 内容 |
|---|---|
| `shop_slots` | 每格卡底名称。空位或名字未读到时为 `null` |
| `shop_stars` | 角色星级。卡顶成行的金色星标按颗数计，名单内角色无成组星标时按一星返回。其他槽位为 `null` |
| `shop_empty_slots` | 与购买后空位图案匹配的槽位列表。匹配后跳过该格的名字和星级读取 |
| `shop_goods` | 角色名单外的非空槽位。每项返回 `kind`、`name`、`price` 和 `description` |

卡底名称未匹配角色名单时，对该槽做整卡 OCR，并单独读右下角价格。整卡读到角色名后按角色处理。装备使用卡中央的方框模板确认，类型为 `equipment`。从卡面左侧起排的效果正文按从上到下拼接，类型为 `effect`。类型暂未确认时返回 `unknown`，保留已读到的名称和价格。已见装备「以太钻头」售价 4，效果商品「肖像速写」售价 3，卡面说明为「获得1个【员工投影仪】。」。

装备图标右下角带欢愉面具时额外返回 `equip_target: "aha"`，表示只能装到备战区左上的阿哈装备栏。同名装备通过标记区分，阿哈版按[阿哈装备栏](equipment-synthesis.md#阿哈装备栏)的规则处理。

1-3 的买前截图中，商店第 2 格景元和第 3 格刻律德菈均有两颗星。买后第 3 格显示中央空位图案，备战席出现二星刻律德菈。刷新检查只读金币、费用和次数，确认刷新后才读取新卡。

## 单次购买流程

```mermaid
flowchart TD
    Intent["接收单次购买意图和当前商店槽位"] --> Mutex{"取得串行操作锁？"}
    Mutex -->|否| Busy["返回 busy"]
    Mutex -->|是| Offer{"商店、角色名、实际价格及可用状态已确认？"}
    Offer -->|否| Reject["返回 failed 和原因，需要重决策时设 needs_ai"]
    Offer -->|是| Capacity{"金币足够且确认可接收角色？"}
    Capacity -->|否| Reject
    Capacity -->|是| Save["保存槽位、角色、标价、金币及相关角色状态"]
    Save --> Click["点击目标格一次"]
    Click --> Review["限时读取购后金币、目标格、角色变化和提示"]
    Review --> Outcome{"购前和购后证据"}
    Outcome -->|确认成交| Success["返回 success 和最新观察"]
    Outcome -->|游戏明确拒绝且确认未成交| Reject
    Outcome -->|结果不明| Pending["返回 uncertain 和现场"]
    Success -->|出现额外选择| AI["返回 needs_ai，等待 AI 指定"]
```

刷新和腾位售卖由 AI 另行指定。满席时能否通过自动升星购买待实机确认。购前相关状态漏读时保留未知字段，使用本次画面证据。

### 点击后判定

只点击一次，等待与购买有关的变化并设有限截止时间；检查的是商店格、金币或提示，不要求整屏静止。

购前金币 24、实际价格 3，则正常扣费后应为 21；金币减少只说明货币变化，不能单独证明买到了指定角色。

| 购后的可靠观察 | 结果 |
|---|---|
| 金币减少额等于购前价格，且目标格出现经确认的售出标记 | `success`，重读相关局势 |
| 上述证据成立，并观察到角色新增或升星 | `success`，一并返回新角色状态 |
| 金币有变化但商店格无法确认，或反之 | `uncertain`，保存证据后停止 |
| 金币变化与预期不符，或识别结果互相矛盾 | `uncertain`，返回实际观察 |
| 出现未知弹窗、离开预期界面或等待到期 | `uncertain`，停止后续操作，保留现场 |
| 游戏明确拒绝且能确认未成交 | `failed`，返回原因与最新观察，不自动重试 |

金币与商店格都没变化时同样返回 `uncertain`：画面可能未更新或识别失败。免费购买、折扣、返还金币等特殊效果是否存在、如何验证，需目标版本实机确认。

购买触发自动升星或额外选择时槽位可能变化，重新识别相关区域，出现玩法决策就停止交给 AI。购买已确认但后续选择待处理时结果可为 `success` 且 `needs_ai: true`。

### 返回示例

数字仅说明格式。`evidence` 记录本次判定依据，不替代最新 `slots` 观察。

```json
{
  "result": "success",
  "screen": "备战",
  "needs_ai": false,
  "action": "buy",
  "from": "shop_1",
  "message": "已确认购买藿藿。",
  "evidence": {"name": "藿藿", "price": 3, "gold_before": 24, "gold_after": 21, "shop_slot": "已售出"},
  "gold": 21,
  "slots": {"shop_1": null, "bench_1": {"unknown": true}}
}
```

`shop_1: null` 表示已确认该格为空；若游戏显示的是售出标记而非空格，按实际形态表达。`bench_1` 未识别时保持未知。无法确认成交时字段相同，`result` 为 `uncertain`、`needs_ai` 为 `true`，`message` 说明已点击与未能确认的内容；AI 先查看新观察或截图，必要时执行 `resync`。

### 原型参数

`post_delay: 1000` 设置点击到首次购后识别的等待时间；完成特征、截止时间以及普通购买与自动升星的差异随后续实机样例确定。
