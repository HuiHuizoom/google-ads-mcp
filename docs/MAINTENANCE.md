# 创建后维护

这些接口适用于允许账户中的已有 Search 资源，包括 Google Ads 后台创建的资源。使用官方 Google Ads Python SDK 33.0.0 / API v25。所有写入都是 `preview_change → submit_change → 回读`，无需独立本机审批或逐次身份验证，没有直接跳过预览的更新/删除工具。

## 先读取，再改指定字段

`list_resources` 支持 `campaigns`、`ad_groups`、`ads`、`keywords`、`campaign_criteria`、`campaign_assets`。每次显式传 `account`，可选 `campaign`、`ad_group` 过滤。默认不返回 REMOVED；超限报错，不截断冒充完整集合。其他读取和报表仍可使用只读 `run_gaql`。

下面的 ID 都是示例；使用实际读取到的完整资源名称。`account.login_customer_id` 按真实授权路径填写，直接访问时省略。

```json
{
  "account": {"customer_id": "1234567890"},
  "resource": "ads",
  "ad_group": "customers/1234567890/adGroups/31"
}
```

读取结果中的 `ad_group_ad.ad.resource_name` 标识广告本体。用 `list_resources(resource="ads", ad_resource=该值)` 查询这个广告的全部未移除关联，将全部 `ad_group_ad.resource_name` 填到下列 `expected_ad_group_ads`。漏掉关联会拒绝预览，因为同一个广告的文案修改可能影响多个关联。

```json
{
  "account": {"customer_id": "1234567890"},
  "request_key": "rsa-url-update-001",
  "change": {
    "kind": "update_ad",
    "ad_group_ad": "customers/1234567890/adGroupAds/31~41",
    "expected_ad_group_ads": ["customers/1234567890/adGroupAds/31~41"],
    "patch": {"final_urls": ["https://example.com/new-page"]}
  }
}
```

这个预览只修改落地页，保留标题、描述、固定位置、显示路径和投放状态。任何 `patch` 中**省略字段表示保留原值**，`null` 不作为清空操作。提供标题/描述列表会替换该列表，并取消该列表原有的固定位置；预览会提醒这一点。修改可能重新触发 Google 审核。

## 写入动作与准确边界

以下是 `preview_change.change.kind`。金额均为账户本币的十进制字符串，操作中还必须明确 `currency`；ROAS 是倍数，2.5 表示 250%。

| kind | 用法及范围 |
| --- | --- |
| `create_search` | 原有完整创建；系列、广告组和 RSA 都为 PAUSED |
| `create_ad_group` | `campaign, name, currency`；手动 CPC 系列还需 `cpc_bid`，自动出价时省略；可带 `keywords, ads` 一次原子创建 |
| `update_ad_group` | `ad_group, currency, patch`；修改 `name, cpc_bid`，单独 CPC 只用于标准手动 CPC |
| `create_ad` | `ad_group, ad`；3–15 个标题、2–4 个描述、HTTPS `final_urls`，可选 `path1/path2`，新广告 PAUSED |
| `update_ad` | `ad_group_ad, expected_ad_group_ads, patch`；修改 `headlines/descriptions/final_urls/path1/path2` |
| `add_keywords` | 广告组添加正向词 |
| `add_negatives` | 系列添加否定词 |
| `add_ad_group_negatives` | 广告组添加否定词 |
| `update_keyword` | `resource, currency, patch`；正向词 `status/cpc_bid/clear_cpc_bid/final_urls` |
| `replace_keyword` | `resource, keyword`；Google 将文字和匹配方式定义为不可变，使用同一原子请求移除并新建，保留状态、CPC、网址/跟踪参数和标签，资源 ID/历史统计不继承 |
| `remove_keywords` | 移除指定正向词或否定词 |
| `set_budget` | 修改日预算，必须完整声明 `expected_campaigns`；共享预算会影响全部这些系列 |
| `set_status` | 原有系列/广告组/广告关联的 PAUSED、ENABLED；正向词状态用 `update_keyword` |
| `update_campaign` | `campaign, patch`；名称、账户时区的 `start_date_time/end_date_time`、`search_partners/display_network`、`positive_geo_target_type/negative_geo_target_type`、`final_url_suffix`。已开始的系列不能改开始时间；不改渠道类型 |
| `set_bidding` | `campaign, currency, strategy`；标准 MANUAL_CPC、MAXIMIZE_CLICKS、MAXIMIZE_CONVERSIONS、MAXIMIZE_CONVERSION_VALUE；可分别带 `max_cpc`、`target_cpa`、`target_roas`。省略可选目标会清除目标；原有组合策略关联会解除，仅修改此系列 |
| `set_targeting` | `campaign, patch`；`geo_target_ids/excluded_geo_target_ids/language_ids`，只替换提供的维度，保留其他维度 |
| `set_schedule` | `campaign, schedules`；替换完整一周，每项 `day, start, end`，账户时区、15 分钟粒度、每天最多 6 段、不得重叠；跨午夜拆两天 |
| `set_device_bid` | 读取 DEVICE criterion 后传 `resource, bid_modifier`；0 排除，0.1–10 为倍数。非零调整限标准手动 CPC，避免把 Smart Bidding 不生效的系数当成已生效 |
| `remove_resource` | 移除系列/广告组/广告关联，不能重新启用；父级移除会阻止子级投放。暂停请用 `set_status`。预算保留 |
| `set_campaign_assets` | `campaign, field_type, assets`；人工 SITELINK、CALLOUT、STRUCTURED_SNIPPET，列表是本系列该类型的完整目标集合 |

## 列表替换与清空

- `set_targeting` 只替换明确传入的地区/排除地区/语言维度。`[]` 清除该维度，清空正向地区或语言可能扩大投放。未变化的 criterion 和出价系数保留，新项使用默认系数。
- `set_schedule.schedules=[]` 恢复全天投放；传列表时替换整周，未变化的时段保留原系数，新时段使用默认系数。
- `update_keyword.patch.clear_cpc_bid=true` 清除关键词单独出价以继承广告组出价；`final_urls=[]` 清除关键词专属网址。
- `update_ad.patch.path1/path2=""` 可清除显示路径，但 path2 非空时必须保留 path1。广告最终网址不能清空。
- `set_campaign_assets.assets=[]` 移除本级该类型的人工关联，**不保证停止显示从账户/广告组继承或自动生成的素材**。相同内容保留原关联及状态；修改内容创建素材并替换关联。旧素材留在素材库，其他系列正在用的内容不被改动。不提供素材库全局删除。

例如只修改语言和添加宣传信息，分别创建两个计划：

```json
{"kind":"set_targeting","campaign":"customers/1234567890/campaigns/11","patch":{"language_ids":["1000","1017"]}}
```

```json
{"kind":"set_campaign_assets","campaign":"customers/1234567890/campaigns/11","field_type":"CALLOUT","assets":[{"type":"CALLOUT","text":"Team collaboration"},{"type":"CALLOUT","text":"Free trial"}]}
```

当前每个计划对应一种动作，动作内部需要的多资源修改原子提交，不提供任意动作混合的通用批处理。后续计划在前一计划完成后重新读取预览。

## 验证范围

已验证真实 v25 SDK 序列化与 FieldMask 字段、MCP stdio 发现/调用，以及离线状态下的新增后修改、保留未修改字段、共享广告范围、关键词替换、清空目标、素材关联替换、状态漂移拦截和超时只读恢复。查询字段名称已与官方 v25 字段表核对。离线模拟不证明 Google GAQL 组合、政策或账户权限会接受；生产调用必须通过 Google `validate_only`。

2026-10-20 已完成 Google OAuth 和真实暂停 Search Campaign 的维护验收：广告组/RSA 新建后修改、关键词修改/替换/清空、两级否定词、预算、系列名称/网址后缀、地域/语言、时段、设备出价和 CALLOUT 关联。原配置已恢复，临时资源已清理。一次素材关联移除因传输错误结果不明，经用户明确授权在后台移除后，由 MCP 只读回读解除锁；不能将此记为 API 自动删除成功。完整结果与尚未验收范围见 [验收记录](VERIFICATION.md)。

当前写入流程为 `preview_change → submit_change → 远端回读`，不再要求独立本机审批窗口或逐次系统身份验证。真实投放、预算风险及账户权限仍由用户决定；连接版本可通过 `connection_status.server_version` 检查。

范围仍为 Search。PMax/购物/视频、受众写入、图片/视频素材上传、账户级/广告组级素材管理、组合出价策略本体管理、转化动作创建和事件回传配置不在本次实现中。
