# PMax、素材库和创建后维护

0.4.0 增加标准非零售 Performance Max、素材组、素材库及受众/搜索主题信号。所有写入仍走 `preview_change → submit_change → 远端回读`。下面的 JSON 是 `change`；外层仍须提供 `account` 和唯一 `request_key`。资源 ID 必须来自同一账户。

## 操作范围

| 动作 | 能力 |
| --- | --- |
| `create_assets` | 上传本机图片、创建文本素材、注册已有 YouTube 视频 ID；Google 可能返回去重后的已有素材 |
| `create_pmax` | 创建独立预算、暂停的 PMax、品牌关联、地区/语言及最多 3 个暂停素材组 |
| `create_asset_group` | 在已有 PMax 中创建暂停素材组，同时关联完整的最低素材组合 |
| `update_asset_group` | 创建后修改名称、落地页、显示路径 |
| `set_asset_group_assets` | 按类型添加、替换、移除人工素材关联，复用已有素材 |
| `set_asset_group_signals` | 添加、替换、移除已有 Audience 信号和搜索主题 |
| `set_pmax_brand_assets` | 创建后替换启用品牌指南系列的商家名和 Logo |
| 已有通用动作 | PMax 名称/日期/网址后缀/最终网址扩展、预算、两种智能出价及 CPA/ROAS、地域/语言、排期；系列和素材组暂停/启用/移除 |

受众和搜索主题是 Google 的优化提示，不是严格的受众或关键词定向。`audiences` 引用已存在的 Audience 对象；本版没有 Audience 定义本身的创建/编辑接口。

PMax 不接受 Search 网络开关、手动 CPC 或最大化点击。品牌指南是否启用是 Google 的不可变字段，本版不提供旧系列品牌指南迁移。Merchant Center 商品源、商品分组、旅游及本地服务变体、转化目标配置不在本版范围。创建使用账户默认转化目标；投放前由业务负责人核对是否合适。

## 查询与素材准备

`list_resources.resource` 新增 `assets`、`asset_groups`、`asset_group_assets`、`asset_group_signals`、`audiences`。可用 `campaign`、`asset_group`、`asset_type`、`name_contains` 等适用过滤条件；不适用的组合会明确报错。

上传图片前调用：

```json
{"path":"/absolute/path/clico-square.png"}
```

这是 `inspect_image_asset` 的输入。工具返回真实格式、尺寸、字节数、SHA-256 和可能适用的素材类型，不访问 Google、不裁剪图片。上传时使用返回的 SHA-256：

```json
{
  "kind":"create_assets",
  "assets":[
    {"type":"IMAGE","path":"/absolute/path/clico-square.png","sha256":"填写检查返回的64位摘要","name":"Clico square"},
    {"type":"TEXT","text":"Explore Clico"},
    {"type":"YOUTUBE_VIDEO","youtube_video_id":"已有视频的11位ID","name":"Clico product video"}
  ]
}
```

实际请求中删除未使用的项。单张图片上限 5,120,000 字节，支持静态 PNG/JPEG/GIF；一次最多 20 个素材、图片合计最多 10,240,000 字节。提交前再次读取并校验文件，内容变更会拒绝提交。视频入口引用 YouTube 视频，不负责将 MP4 上传至 YouTube；视频可用性、时长和审核由 Google 判定。

素材可以在多个素材组中复用。替换文案/图片应创建新素材，再替换指定关联，不全局改写其他系列正在使用的素材。不提供素材库全局删除；解除关联后素材仍留在素材库。Google 去重可能忽略新素材名称，应使用写入回执中的资源 ID。

## 创建标准 PMax

先上传或查询素材，然后提供真实资源引用：

```json
{
  "kind":"create_pmax",
  "name":"Clico PMax",
  "currency":"USD",
  "daily_amount":"5",
  "bidding":"MAXIMIZE_CONVERSIONS",
  "geo_target_ids":["2840"],
  "language_ids":["1000"],
  "eu_political_advertising":"DOES_NOT_CONTAIN_EU_POLITICAL_ADVERTISING",
  "brand_assets":[
    {"asset":"customers/1234567890/assets/101","field_type":"BUSINESS_NAME"},
    {"asset":"customers/1234567890/assets/102","field_type":"LOGO"}
  ],
  "asset_groups":[{
    "name":"Main",
    "final_urls":["https://example.com/"],
    "assets":[
      {"asset":"customers/1234567890/assets/201","field_type":"HEADLINE"},
      {"asset":"customers/1234567890/assets/202","field_type":"HEADLINE"},
      {"asset":"customers/1234567890/assets/203","field_type":"HEADLINE"},
      {"asset":"customers/1234567890/assets/204","field_type":"LONG_HEADLINE"},
      {"asset":"customers/1234567890/assets/205","field_type":"DESCRIPTION"},
      {"asset":"customers/1234567890/assets/206","field_type":"DESCRIPTION"},
      {"asset":"customers/1234567890/assets/207","field_type":"MARKETING_IMAGE"},
      {"asset":"customers/1234567890/assets/208","field_type":"SQUARE_MARKETING_IMAGE"}
    ]
  }]
}
```

新系列和素材组均为 PAUSED；品牌指南启用；采用实际所在地 PRESENCE 定位，最终网址扩展默认关闭。创建使用原子请求，同时提交素材组及最低素材关联。没有提供视频时 Google 可能自动生成视频；这些设置及创建成功都不表示已经投放。

最低组合：3 个短标题、1 个长标题、2 个描述、1 张横图、1 张方图，品牌层另有 1 个商家名、至少 1 个方形 Logo。短标题最多 30 字符，长标题/描述最多 90，商家名最多 25；中文等宽字符按现有文本校验规则计数。图片最低尺寸：横图 600×314（1.91:1）、方图 300×300、Logo 128×128；可选竖图 480×600、横 Logo 512×128。Google 校验是最终依据，本地检查不等于政策审核通过。

## 创建后修改

素材组修改和搜索主题：

```json
{"kind":"update_asset_group","asset_group":"customers/1234567890/assetGroups/301","patch":{"name":"Updated group","final_urls":["https://example.com/features"],"path1":"features"}}
```

```json
{"kind":"set_asset_group_signals","asset_group":"customers/1234567890/assetGroups/301","search_themes":["AI writing","browser assistant"]}
```

```json
{"kind":"set_asset_group_signals","asset_group":"customers/1234567890/assetGroups/301","audiences":["customers/1234567890/audiences/401"]}
```

省略受众或主题维度会保留现有值；传 `[]` 清空该维度。Google 不支持原地修改信号，本接口在同一请求内按差异移除/添加。素材组专属 Audience 只能关联对应组。

替换某类素材时提供该类型的完整目标列表，其他类型和自动创建的关联保持原样：

```json
{"kind":"set_asset_group_assets","asset_group":"customers/1234567890/assetGroups/301","assets_by_field":{"HEADLINE":["customers/1234567890/assets/201","customers/1234567890/assets/202","customers/1234567890/assets/209"]}}
```

`[]` 移除该类型人工关联，但不能使素材组低于最低组合要求。相同关联保留原状态，不隐式启用已有暂停关联。

最终网址扩展创建后也可切换，其他自动化设置保留：

```json
{"kind":"update_campaign","campaign":"customers/1234567890/campaigns/501","patch":{"final_url_expansion":true}}
```

品牌替换使用 `set_pmax_brand_assets` 的完整目标品牌列表。启用品牌指南的系列把商家名和 Logo 关联到 Campaign；旧版未启用品牌指南的系列在素材组上维护这些类型。

## 证据与官方约束

真实账户验收范围见 [验收记录](VERIFICATION.md)。不能将离线测试、Google validate_only、暂停创建或 ENABLED 状态当作曝光/转化证明。

- [Google：素材组及原子创建](https://developers.google.com/google-ads/api/performance-max/asset-groups)
- [Google：受众信号和搜索主题](https://developers.google.com/google-ads/api/performance-max/asset-group-signals)
- [Google：创建 PMax 与品牌指南](https://developers.google.com/google-ads/api/performance-max/create-campaign)
- [Google：素材要求](https://developers.google.com/google-ads/api/performance-max/asset-requirements)
- [Google：自动化素材设置](https://developers.google.com/google-ads/api/docs/assets/asset-automation-settings)
