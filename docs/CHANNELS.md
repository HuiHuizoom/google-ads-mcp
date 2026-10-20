# Display、Demand Gen 和 App

0.5.0 增加这三类系列的创建与创建后维护，继续使用官方 Google Ads Python SDK 33.0.0 / API v25。所有写入走 `preview_change → submit_change → 远端回读`，新系列、广告组、广告固定为 `PAUSED`。

## 支持范围

| 对象 | 本版支持 |
| --- | --- |
| Display | 标准展示系列、自适应展示广告；横图、方图、方形 Logo、已有 YouTube 视频素材 |
| Demand Gen | 多素材图片广告、响应式视频广告；已有 Audience 关联、广告组级地区/语言、优化型定向开关 |
| App | Android/iOS 的 `APP_CAMPAIGN` 安装推广；文字、图片、已有 YouTube 视频；安装、应用内操作、价值目标 |
| 共用维护 | 预算、系列名称/日期/网址后缀、广告组名称、暂停/启用、移除；按渠道维护出价、定位 |
| 创建后广告内容 | `replace_channel_ad` 提交完整新文案/素材；同一原子请求移除旧关联并创建暂停的新广告 |

**内容替换不保留旧广告 ID 和历史统计**，其他广告组中的旧广告关联保留。这里没有提供原地修改所有广告格式的承诺。系列渠道、App ID 和应用商店不允许修改；App 优化目标和选择的转化动作可通过 `set_channel_bidding` 修改。

不包含 Display 上传 HTML5/图片广告、Demand Gen 轮播/商品广告、App 互动/预注册、Merchant Center 商品源、旅游/本地服务 PMax、独立 Video 系列。Audience 定义及客户匹配名单仍不在本版写入范围。

## 读取

`list_resources` 新增：

- `channel_campaigns`：系列、预算、出价与 App 设置。
- `channel_ad_groups`：广告组、优化型定向与受众模式。
- `channel_ads`：上述四种格式的文案与素材引用。
- `ad_group_criteria`：广告组受众、用户列表、地区、语言。

这些是扩展字段列表，不自动按渠道过滤；其他渠道也可能出现。用返回的 `advertising_channel_type` / `advertising_channel_sub_type` 区分，或使用只读 GAQL。可按 `campaign` / `ad_group` 缩小适用列表；超过行数限制会明确拒绝，不返回假完整结果。

## 创建示例

以下为 `preview_change` 输入，ID、素材和文案需替换为真实值。图片来自素材库；本机图片先用已有 `inspect_image_asset` / `create_assets` 检查、上传。示例不会自行提交。

```json
{
  "account": {"customer_id": "1234567890"},
  "request_key": "display-create-example-001",
  "change": {
    "kind": "create_channel_campaign",
    "channel": "DISPLAY",
    "name": "Display test",
    "currency": "USD",
    "daily_amount": "5",
    "bidding": {"strategy": "MANUAL_CPC"},
    "geo_target_ids": ["2840"],
    "language_ids": ["1000"],
    "eu_political_advertising": "DOES_NOT_CONTAIN_EU_POLITICAL_ADVERTISING",
    "ad_groups": [{
      "name": "First group",
      "cpc_bid": "0.10",
      "ads": [{
        "format": "RESPONSIVE_DISPLAY",
        "headlines": ["Explore Our Product"],
        "long_headline": "Find the right product for your team",
        "descriptions": ["Discover useful features."],
        "business_name": "Example",
        "final_urls": ["https://example.com/"],
        "marketing_images": ["customers/1234567890/assets/701"],
        "square_marketing_images": ["customers/1234567890/assets/702"]
      }]
    }]
  }
}
```

Demand Gen 使用 `channel: "DEMAND_GEN"`，广告格式为 `DEMAND_GEN_IMAGE` 或 `DEMAND_GEN_VIDEO`；两者均需要 Logo。图片格式不接受 `long_headline`/`videos`，视频格式需要 `long_headline`/`videos`。Google 会校验账户币种、出价策略相关的预算下限，不硬编码全球统一最低额；本次 1 USD/日被拒绝，50 USD/日的图片方案已实际创建、维护并清理，始终保持暂停。

Demand Gen 创建时顶层地区/语言作为各广告组的默认值，实际写入广告组级 criterion。`ad_groups[].geo_target_ids/language_ids` 可分别覆盖默认值。向已有 Demand Gen 系列新增广告组时，这两个字段必须显式提供，避免意外扩大到所有地区/语言。

App 使用 `channel: "APP"`、广告格式 `APP`，至少两条描述，不传落地页/商家名/长标题；这些身份信息来自商店。额外提供：

```json
{
  "bidding": {"strategy": "TARGET_CPA", "target_cpa": "1"},
  "app": {
    "app_id": "com.example.app",
    "app_store": "GOOGLE_APP_STORE",
    "goal": "OPTIMIZE_INSTALLS_TARGET_INSTALL_COST"
  }
}
```

Apple 商店使用 `APPLE_APP_STORE` 和数字 App ID。应用内操作/价值目标还需 `conversion_actions`，必须是当前账户拥有、状态有效、App ID 一致的 APP 来源动作。价值目标 `OPTIMIZE_RETURN_ON_ADVERTISING_SPEND` 使用 `TARGET_ROAS`；其余本版 App 目标使用 `TARGET_CPA`。这里不会替用户创建 Firebase/GA4 事件。

## 创建后维护

下面是 `preview_change.change.kind`；每次先读取完整资源名称。

| kind | 参数与语义 |
| --- | --- |
| `create_channel_ad_group` | `campaign, group`；新增暂停广告组及广告 |
| `create_channel_ad` | `ad_group, ad`；在已有广告组添加暂停广告 |
| `replace_channel_ad` | `ad_group_ad, ad`；完整替换文案、网址、素材组合，产生新 ID |
| `update_channel_ad_group` | `ad_group, patch`；`name/cpc_bid/optimized_targeting`，App 仅接受名称 |
| `set_channel_bidding` | `campaign, bidding`；App 可附 `app_goal`，内容与创建时 `app` 相同，商店/App ID 必须保持一致 |
| `set_channel_group_targeting` | `ad_group` 加明确要改的维度；省略保留，`[]` 清空，Audience 用空字符串清空 |
| `set_budget` | 复用已有接口，完整声明共用预算的 `expected_campaigns` |
| `update_campaign` | 复用名称、日期、网址后缀等公共字段；Search 网络开关不可用于这些渠道 |
| `set_targeting` | Display/App 的系列级地区、排除地区和语言；Demand Gen 使用广告组接口 |
| `set_schedule` | Display/Demand Gen；本版不支持 App 排期 |
| `set_status` / `remove_resource` | 系列、广告组、广告关联的暂停/启用或移除 |

Display 的 `set_channel_group_targeting.user_lists` 引用已有名单并设为“定位”，保留其他维度设置；若同时开启优化型定向，Google 仍可能扩展覆盖范围。Demand Gen 使用 `audience` 引用兼容的已有 Audience，支持正向 `geo_target_ids/language_ids`；暂未接入其广告组级排除地区。只操作传入的正向维度，保留否定项及其他维度。App 通过系列级地区/语言管理，不提供手动受众定向。

Display 出价支持 `MANUAL_CPC/MAXIMIZE_CONVERSIONS/TARGET_CPA/TARGET_ROAS`；Demand Gen 支持 `MAXIMIZE_CONVERSIONS/MAXIMIZE_CONVERSION_VALUE/TARGET_CPA/TARGET_ROAS`。自动出价广告组不接收单独 CPC。ROAS 是倍数，2.5 表示 250%；可选 CPA/ROAS 省略时清除该策略的旧目标。切换标准出价会解除当前系列的组合出价关联。

## 验证结论

0.5.1 已使用专用暂停对象完成 Display 和 Demand Gen 图片广告的真实创建、预算/名称/网址后缀修改、广告组维护、新增广告组和广告、广告内容替换、地域/语言与排期设置、清空、移除及远端回读。Display 另通过现有用户列表关联/清空，以及四种出价策略切换。Demand Gen 通过最大化转化和目标 CPA；目标 ROAS、最大化转化价值被 Google 以 `NOT_ENOUGH_CONVERSIONS` 拒绝，尚无实际写入通过证据。

Display 首次创建已写入，但长标题的 `.text` GAQL 查询非法导致无法确认。0.5.1 改为选择完整 AdTextAsset；原操作通过只读核对恢复为 RECONCILED，没有再次创建。其余实际提交通过远端回读。清理前所有系列、组和广告均 PAUSED；本轮不测试启用投放，不把结构验收当成政策审核或曝光效果。

视频和 App 按用户要求待实际素材/App 身份齐备后再测；已有 Audience 关联因账户没有 Audience 尚未实测。未逐一验证每种可选图片比例、日期、字段组合、共享预算或多账户场景。App 目标变更、受众/素材检查、并发修改拒绝和回执丢失后的只读核对有离线测试。详细结果见 [验收记录](VERIFICATION.md)。

官方依据：[Display](https://developers.google.com/google-ads/api/docs/responsive-display-ads/create-responsive-display-ads)、[Demand Gen](https://developers.google.com/google-ads/api/docs/demand-gen/create-campaign)、[App](https://developers.google.com/google-ads/api/docs/app-campaigns/create-campaign)、[定向模式](https://developers.google.com/google-ads/api/docs/targeting/targeting-settings)。
