# 转化配置写入

0.5.0 支持转化动作、账户/系列转化目标和自定义目标的创建后维护。使用已有 `preview_change → submit_change → 远端回读`，不增加审批弹窗或逐次身份验证。

**本版写的是转化配置，不是事件数据。** 不安装网站标签、不修改 GTM、不导入 GA4/Firebase 事件、不上传点击/电话转化数据、不自动切换跨账户转化跟踪所有者。创建转化动作或设置为主要目标，都不证明收到真实转化。

## 读取与影响范围

`list_resources.resource` 支持：

- `conversion_actions`：动作类型、来源、所有者、类别、主要/次要、计数、归因窗口、默认价值。
- `customer_conversion_goals`：账户默认目标。
- `campaign_conversion_goals`：系列目标，可用 `campaign` 过滤。
- `conversion_goal_configs`：系列使用账户默认目标还是独立配置，以及自定义目标关联。
- `custom_conversion_goals`：自定义目标及包含的动作。

写入前检查 `customer.conversion_tracking_setting.google_ads_conversion_customer` 为当前账户，引用动作的 `owner_customer` 也须一致。MCC 拥有的转化暂不通过当前子账户写入，接口会明确拒绝。

`expected_campaigns` 的意义是完整声明影响集合，预览/提交前都会重新核对：

| 操作 | 需要声明的集合 |
| --- | --- |
| 创建/修改/移除转化动作、修改账户默认目标 | 当前账户全部未移除系列；这是保守影响范围，不表示每个系列一定使用该动作 |
| 修改指定系列目标 | 只包含该系列 |
| 修改/移除自定义目标 | `conversion_goal_configs` 中全部引用此自定义目标的系列，没有引用则 `[]` |
| 创建尚未关联的自定义目标 | 不需要此字段 |

## 写入动作

| kind | 支持内容 |
| --- | --- |
| `create_conversion_action` | `WEBPAGE/UPLOAD_CLICKS/UPLOAD_CALLS`；名称、类别、计数、点击窗口、默认价值、主要/次要 |
| `update_conversion_action` | 创建后修改名称、ENABLED、类别、主要/次要、计数、点击/浏览窗口、默认价值 |
| `remove_conversion_action` | 移除上述支持类型的动作；仍在自定义目标内时拒绝，需先解除引用 |
| `set_conversion_goals` | 账户或系列已有目标的 `biddable`；系列切换 CUSTOMER/CAMPAIGN 来源、选择/解除自定义目标 |
| `create_custom_conversion_goal` | 名称及现有转化动作列表 |
| `update_custom_conversion_goal` | 修改名称或完整替换动作列表；须声明引用系列 |
| `remove_custom_conversion_goal` | 移除目标；尚有系列引用时拒绝，需先解除引用 |

`patch` 省略字段保留现值，显式 `null` 拒绝。`value_settings` 只更新传入的子字段，价值 0 有效。动作类型和所有者不允许修改；GA4/Firebase 等导入动作可读取、选入目标，但本版不修改其动作设置。

0.5.1 移除 `HIDDEN` 写入选项：真实 Google 预检对上述三种可写类型均返回 `BLOCKED_VALUE`，字段路径为 `conversion_action_operation.update.status`。Google 枚举存在这个状态不代表这些类型可设置它；停止记录使用 `remove_conversion_action`，不把 HIDDEN 当成暂停。`ENABLED` 也不代表已经收到事件。

账户/系列目标由 Google 根据动作的类别、来源生成，不能通过这个接口凭空创建一组 category/origin。自定义目标可以创建。自定义目标被系列采用时，其中的动作可能参与出价，即使动作原先标为次要；预览中会提醒这一点。

## 示例

新建一个次要网站注册转化动作；仍需后续单独安装标签和验证回传：

```json
{
  "account": {"customer_id": "1234567890"},
  "request_key": "conversion-create-example-001",
  "change": {
    "kind": "create_conversion_action",
    "name": "Website signup",
    "type": "WEBPAGE",
    "category": "SIGNUP",
    "primary_for_goal": false,
    "counting_type": "ONE_PER_CLICK",
    "click_through_lookback_window_days": 30,
    "value_settings": {"default_value": 0, "default_currency_code": "USD"},
    "expected_campaigns": ["customers/1234567890/campaigns/11"]
  }
}
```

修改这个动作时使用 `update_conversion_action`，提供 `conversion_action`、完整 `expected_campaigns`，以及例如 `patch: {"primary_for_goal": true}`。不传的价值/窗口等字段保留。

将一个系列切换到自己的“网站购买”目标：

```json
{
  "kind": "set_conversion_goals",
  "campaign": "customers/1234567890/campaigns/11",
  "expected_campaigns": ["customers/1234567890/campaigns/11"],
  "goals": [{"category": "PURCHASE", "origin": "WEBSITE", "biddable": true}]
}
```

只修改提供的目标，未列出的其他目标保留。恢复账户默认配置时提供 `goal_config_level: "CUSTOMER"`，省略 `goals/custom_conversion_goal`；这会清除该系列的自定义目标关联。单独解除自定义目标时用 `custom_conversion_goal: ""`，仍保持系列独立配置。

## 验证结论

0.5.1 已在真实账户通过专用对象完成以下提交和远端回读：

- `WEBPAGE/UPLOAD_CLICKS/UPLOAD_CALLS` 三种次要动作的创建、创建后修改和移除；名称、计数、点击窗口、默认价值 0、默认价值开关与嵌套币种保留均核对通过，网站动作还修改了浏览窗口。
- 新生成、没有既有动作的 `PHONE_CALL_LEAD/CALL_FROM_ADS` 账户目标设置 false → true → false；测试动作始终 secondary，没有改动六组原有账户默认目标。
- 专用暂停 Display 系列的目标设置、自定义目标关联、恢复 CUSTOMER 继承并清空自定义关联。
- 自定义目标的创建、改名、动作列表替换、被系列引用时修改、解除引用后移除。

三种动作和自定义目标已移除。Google 自动生成的账户/系列目标只有更新接口，没有删除接口；新增空目标保留为不参与出价。原业务转化、默认目标及系列配置的最终比对见 [验收记录](VERIFICATION.md)。

本轮没有将动作设为主要，也没有上传/模拟真实转化事件；`ENABLED` 测试是对已启用配置回写同值，不是停用后恢复的证明。其他类别、窗口组合、跨账户跟踪及真实投放/归因效果不在已验收范围。并发变化、影响集合拒绝、引用保护和回执丢失恢复另有离线测试，不能替代这些真实前置条件。

官方依据：[转化配置](https://developers.google.com/google-ads/api/docs/conversions/getting-started)、[系列目标](https://developers.google.com/google-ads/api/docs/conversions/goals/campaign-goals)。
