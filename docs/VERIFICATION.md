# 验收记录

## 2026-10-20：Display、Demand Gen 图片及转化配置真实验收，0.5.1

按用户授权，在真实账户创建前缀为 `Clico_QA050_20261020` 的专用对象，完成 **65 个实际写入计划：64 VERIFIED、1 RECONCILED**，均经真实 MCP stdio 的预览、Google 预检、提交和远端回读。14 种新增动作各有实际主路径通过；这不等于每个渠道、字段组合或出价策略都已验证。所有测试系列、广告组和广告从创建到移除始终暂停，未启用投放；App 和视频按要求留待素材齐备再测。

### 已通过的实际操作

| 范围 | 实际写入与核对内容 | 计划数 |
| --- | --- | --- |
| 渠道创建 | Display 自适应展示广告；Demand Gen 多素材图片广告；独立预算、组、广告、地域/语言均回读 | 2 |
| 广告组与广告 | 两种渠道均修改组名称/优化型定向；Display 修改 CPC；替换文案与素材组合，新建第二组及广告、向已有组加广告 | 8 |
| 预算与系列信息 | Display 5 → 6 → 5 USD/日；Demand Gen 50 → 60 → 50 USD/日；两者修改名称和网址后缀 | 6 |
| 定位与排期 | Display 系列地区/排除地区/语言、已有用户列表关联及清空；Demand Gen 组级地区/语言；两者设置并清空排期 | 8 |
| 出价与暂停状态 | Display 最大化转化、目标 CPA、目标 ROAS、恢复手动 CPC；Demand Gen 最大化转化、目标 CPA、恢复最大化转化；两者回写 PAUSED 同值 | 9 |
| 转化动作 | WEBPAGE、UPLOAD_CLICKS、UPLOAD_CALLS 三种次要动作均创建、修改、移除；网站动作另回写 ENABLED 同值 | 10 |
| 转化目标 | 新生成的独立账户目标 false → true → false；测试系列目标设置、自定义目标关联、恢复 CUSTOMER；自定义目标创建、修改两次及移除 | 10 |
| 渠道清理 | 分别移除两个渠道中的三条剩余广告关联、两个广告组及系列；原广告已在内容替换时移除 | 12 |

三种转化动作均验证名称、计数方式、点击窗口、默认价值 0 和默认价值开关，未传入的币种保持 USD；网站动作还验证浏览窗口修改。动作一直是 secondary，没有安装标签或上传事件。账户目标测试只涉及新生成、没有既有转化动作的 `PHONE_CALL_LEAD/CALL_FROM_ADS`，六组原有默认目标未改。自定义目标关联期间只用于暂停的专用 Display 系列。

读取覆盖连接预检、系列/预算、广告组、四种广告格式的查询字段、定位、自定义目标和目标来源、转化动作、GAQL 指标。部分查询字段存在其他广告格式，不等于对那些格式做了真实写入。

### 拒绝、修复及仍未验证的内容

| 项目 | 真实结果与处理 |
| --- | --- |
| Display 长标题回读 | 首次创建收到完整 Google 回执，但查询 `.long_headline.text` 被拒，操作进入 UNKNOWN。修复为选择完整 AdTextAsset 后，只读 reconcile 核对全部预期内容，变为 RECONCILED；没有重发创建或直接修改账本。 |
| 转化动作 HIDDEN 状态 | MCP 预检被拒；进一步对三种支持类型分别做无写入预检，均返回 `BLOCKED_VALUE`，位置为 `update.status`。0.5.1 去掉这个无效写入选项；移除动作的实际路径已通过。 |
| Demand Gen 目标 ROAS | Google 预检返回 `NOT_ENOUGH_CONVERSIONS`；没有发送实际写入，不计通过。 |
| Demand Gen 最大化转化价值 | 同样返回 `NOT_ENOUGH_CONVERSIONS`；当前测试系列不满足前置条件，不计通过。 |
| Demand Gen 已有 Audience 关联 | 账户没有现有 Audience，未真实写入。Display 的已有用户列表关联已实测，两者不是同一能力。 |
| App、视频 | 按用户要求暂不测试；需要真实 App 身份/适用转化动作和可用视频素材。 |
| 其他边界 | 未启用任何测试广告；未把转化改为主要；未验证真实归因、政策审核、曝光/转化效果、共享预算、多账户或所有可选字段组合。PAUSED/ENABLED 同值回写不是启停切换证据。 |

长标题修复也覆盖 Demand Gen 视频商家名的同类消息字段，但视频只有本地回归证据，不能据此算视频实测通过。旧计划只读核对保留原计划/hash 和期望值，记录 `legacy_channel_ad_text_asset_selector` 兼容规则；真实文字不一致仍拒绝确认。

### 清理及账户还原核对

北京时间 **21:18 起的最终回读**确认：

- 2 个专用系列、4 个广告组、8 个广告关联为 REMOVED；2 个非共享预算也为 REMOVED，引用数均为 0。非共享预算由 Google 随系列移除；本轮已纠正此前“预算一律保留”的说明，依据见 [Google 预算移除规则](https://developers.google.com/google-ads/api/docs/campaigns/budgets/remove-budgets)。
- 3 个测试转化动作为 REMOVED；自定义目标已移除，列表为空；两个测试系列的目标配置恢复 CUSTOMER，自定义关联为空。
- Google 自动生成的空账户目标仍存在，`PHONE_CALL_LEAD/CALL_FROM_ADS` 的 `biddable=false`。已移除系列的目标配置记录也仍可查询；这类自动资源没有删除接口，不通过清账伪造“消失”。[官方目标生命周期说明](https://developers.google.com/google-ads/api/docs/conversions/goals/overview)
- 测试前的 23 个未移除系列、10 个业务转化动作、6 个账户目标及 26 项系列目标配置，与最终同字段快照逐项一致。原 `Clico_MCP_Test_20261020` 仍 PAUSED、5 USD/日。没有创建或删除素材库图片，测试复用两张已有图片。
- 两个测试系列当日报表均返回曝光 0、点击 0、费用 0；报表有延迟，测试安全性依赖全程暂停，不能用零值证明未来归因数据。
- 账户写入锁为 0；本轮没有待确认写入、强制解锁或人工放弃的操作。

### 代码与连接验证

本地 **159 passed**；ruff/格式检查、真实 stdio smoke（0.5.1、15 工具）及 sdist/wheel 构建通过。回归覆盖完整 AdTextAsset 查询、旧计划不被改写、文字漂移仍被发现，以及 HIDDEN 在输入边界拒绝。

本轮通过独立新启动的 MCP 进程完成真实测试，修复后的服务为 **0.5.1**；当前聊天挂载连接实际查询仍为 **0.3.2**，不能称其已自动升级。重新连接 MCP 后才能加载新版 schema/代码，无需重新 OAuth；没有自动重启 Codex。

私有证据：`.state/live-v050-calls.jsonl`、`live-v050-objects.json`、`live-v050-baseline.json`、`live-v050-final.json`、`live-v050-hidden-validation.json` 及操作账本，均留在本机、不提交公开仓库。下面的 0.5.0 条目保留开发阶段历史；本节真实写入结果替代其“未提交”的状态。

## 2026-10-20：Display、Demand Gen、App 与转化配置，0.5.0

新增 14 种写入动作，共 **41 种写入、15 个 MCP 工具**。本轮完成开发、离线状态流程、真实 stdio 协议、真实账户读取及 Google 提交前预检，**没有实际提交本轮新增能力的写入**，没有创建新系列或修改现有转化配置。下表不能理解为实际投放或完整写入验收。

| 范围 | 已验证 | 未完成真实验证 |
| --- | --- | --- |
| Display 自适应广告 | SDK/离线创建与创建后维护；真实创建方案 validate_only 通过 | 实际创建、修改、移除及远端回读 |
| Demand Gen 图片广告 | SDK/离线创建与维护；真实创建方案 validate_only 通过 | 实际创建、修改、移除及远端回读；已有 Audience 关联 |
| Demand Gen 视频广告 | SDK/离线生命周期通过 | 账户没有可用 YouTube 视频素材，未做真实预检/写入 |
| App 安装推广 | SDK/离线创建、内容替换、出价/App 优化目标修改、移除 | 没有用户提供的真实 App ID；账户没有 APP 来源动作，未做真实预检/写入 |
| 转化动作 | 真实查询 10 项；网站次要 SIGNUP 创建预检通过；离线增改删、字段保留通过 | 实际创建/更新/移除及回读；当前动作未发现本版支持修改的三种类型 |
| 账户/系列目标 | 真实查询默认目标 6 项、系列目标 153 项、配置 26 项；账户目标和系列恢复继承预检通过；离线切换通过 | 实际目标变更与回读 |
| 自定义目标 | 真实查询为空；引用已有动作创建目标预检通过；离线增改删、引用保护通过 | 实际增改删和系列关联 |

真实读取还覆盖系列 23 行、广告组 53 行、广告 55 行；新字段查询通过，现有未移除系列均为 Search，因此这些读取不证明有现成 Display/Demand Gen/App 对象可用于修改。广告组 criterion 全账户查询超过默认上限，明确返回 RESULT_LIMIT；缩小到一个广告组后读取 48 行成功。没有将截断列表当完整结果。

首次 Demand Gen 预检遇到 `BUDGET_BELOW_PER_DAY_MINIMUM` 及两个 `UNKNOWN`。定向诊断确认后两项指向系列级地区/语言；已修正为广告组级创建和维护，并增加回归测试。50 USD/日的方案经真正 MCP `preview_change` 预检通过；仅生成计划，没有提交预算或产生新的投放。本轮没有改变本机预算上限。通过预检的六种方案为：Display 创建、Demand Gen 图片创建、网站转化动作创建、自定义目标创建、账户目标更新、系列恢复账户默认目标。

本地回归 **156 passed**，包含新增四种广告格式的完整离线维护、Demand Gen 定位层级、Display 定位模式保留、App 目标身份校验、金额上限、转化所有者/影响集合校验、引用移除保护、并发变化拒绝，以及三种渠道创建丢失回执后只读核对、不重复创建。真实 v25 SDK 序列化和字段 mask 检查通过；ruff、格式检查、stdio smoke（0.5.0/15 工具）、sdist/wheel 构建通过。

私有证据在本机 `.state/validation-v050.jsonl`、`.state/validation-v050-resources.json` 及操作账本，不进入公开仓库。Google 预检成功不证明审核、投放、事件回传或实际提交成功。既有 Search/PMax 的历史真实验收见下文；本轮新增能力不能沿用其 VERIFIED 结论。

本轮通过独立新启动的 0.5.0 MCP 进程验证。已运行的旧 MCP 连接仍需要关闭再开启以加载源码和 schema；无需重新做 Google OAuth，不自动重启 Codex。

## 2026-10-20：PMax 与素材原生能力，0.4.0

使用独立启动的 **0.4.0 MCP stdio 服务**，在真实账户的专用暂停 PMax 系列完成 **23 次写入，全部 VERIFIED**；另有 1 个仅预检未提交的创建计划。通过 MCP 预览、Google validate_only、提交及远端回读验证，未直接调用裸写接口。系列始终 PAUSED，没有启用投放。

| 写入动作 | 实际测试内容 | 已验证计划数 |
| --- | --- | --- |
| `create_pmax` | 独立 1 USD/日预算、暂停系列/素材组、两类品牌素材、完整普通素材、美国/英语定位 | 1 |
| `create_asset_group` | 新增第二个暂停素材组，跨组复用现有素材 | 1 |
| `update_asset_group` | 创建后修改名称、最终网址及两个显示路径 | 1 |
| `create_assets` | 上传已有公司 Logo 原文件及创建文本；Google 对图片去重返回原 ID，回执绑定验证通过 | 1 |
| `set_asset_group_assets` | 添加新标题素材关联，再定向移除 | 2 |
| `set_pmax_brand_assets` | 添加第二个 Logo，再移除该品牌关联 | 2 |
| `set_asset_group_signals` | 搜索主题添加、替换、清空 | 3 |
| `update_campaign` | 最终网址扩展开启、关闭，其他自动化设置保留 | 2 |
| `set_budget` | PMax 独立预算 1 → 2 → 1 USD/日 | 2 |
| `set_bidding` | 最大化价值/目标 ROAS，最大化转化/目标 CPA，清除 CPA | 3 |
| `set_status` | 素材组 ENABLED → PAUSED，父系列全程 PAUSED | 2 |
| `remove_resource` | 移除两个测试素材组，再移除测试系列 | 3 |

素材库、素材组、关联、信号、Audience 列表及按系列/素材组/素材类型/名称的适用过滤均返回成功；图片检查工具也经真实 MCP 调用。最终回读：新 PMax 为 REMOVED，没有未移除素材组；原 `Clico_MCP_Test_20261020` 仍 PAUSED、5 USD/日。账户锁为 0。新文本素材在关联移除后保留于素材库；复用的旧素材和独立测试预算保留，不提供全局素材/预算删除。

### 明确未实测的范围

- **Audience 信号**：查询返回 0 个现有 Audience，添加/保留/移除逻辑及 SDK 消息有离线测试，尚无真实关联写入证据。本版引用已有 Audience，不创建 Audience 定义。
- **YouTube 视频**：账户素材库没有现有视频，未擅自使用其他人的视频。已有 YouTube ID 注册有 SDK/离线测试，真实注册与关联仍需公司可用的视频 ID。
- Merchant Center 商品源、商品分组、旅游/本地服务变体、转化目标编辑、Audience 定义 CRUD、MP4 上传及图片自动裁剪均未实现，不计入本版能力。
- 地域/排期等共用接口在 Search 的既有实测仍成立，但本轮没有把所有字段组合再次在 PMax 上逐一测试；投放、政策审核及转化效果不属于结构验收结论。

遇到一次预览阶段 `READ_UNAVAILABLE`，尚未生成计划或发送写入；重做读取后成功。没有未知写入或强制解锁。

本机回归 **128 passed**；ruff 检查与格式检查通过；真实 stdio smoke 显示 0.4.0、15 个工具、27 种写入 schema；sdist/wheel 构建成功。新增离线检查覆盖素材最低组合、类型/尺寸、Google 素材去重、图片预览后变更、丢失素材回执时保持 UNKNOWN、PMax 创建超时只读恢复、保留自动素材和其他自动化设置、信号并发变化及跨账户拒绝。离线模拟不替代上面的真实证据。

开发时当前聊天旧 MCP 连接报告 0.3.2，本节所有新功能验收均通过另外启动的 0.4.0 服务完成；源码升级不会自动替换已经运行的 Python 进程或刷新客户端缓存的工具 schema。

## 2026-10-20：现有能力实际验收，0.3.4

使用新启动的 **0.3.4 MCP stdio 服务**，在真实账户中创建独立、默认暂停的 Search 验收系列。20 种现有写入动作均已实际提交并核对远端结果，累计 **49 个计划：48 VERIFIED、1 RECONCILED**。这证明下表列出的维护路径，不代表所有可选参数组合、多账户场景或实际投放效果都通过。

| 写入动作 | 实际测试内容 | 结果 |
| --- | --- | --- |
| `create_search` | 创建预算、暂停系列/广告组/RSA、关键词、地区和语言 | VERIFIED |
| `create_ad_group` | 已有系列中新建暂停广告组 | VERIFIED |
| `update_ad_group` | 创建后修改名称、CPC | VERIFIED |
| `create_ad` | 已有广告组中新建暂停 RSA | VERIFIED |
| `update_ad` | 创建后修改文案、最终网址、显示路径 | VERIFIED |
| `add_keywords` | 添加正向关键词 | VERIFIED |
| `update_keyword` | 状态、独立 CPC、专属网址及清空恢复继承 | VERIFIED |
| `replace_keyword` | 替换文字和匹配方式，保留状态、CPC、网址 | VERIFIED |
| `add_negatives` | 系列级否定词 | VERIFIED |
| `add_ad_group_negatives` | 广告组级否定词 | VERIFIED |
| `remove_keywords` | 移除正向词、两级否定词 | VERIFIED |
| `set_budget` | 独立预算 1 → 2 → 1 USD/日 | VERIFIED |
| `set_status` | 系列、广告组、广告关联分别 ENABLED → PAUSED | VERIFIED；启用系列前确认所有广告组和广告暂停，启用子级时系列暂停 |
| `update_campaign` | 名称、结束时间、搜索伙伴/展示网络、地域匹配方式、网址后缀及清空 | VERIFIED |
| `set_bidding` | 最大化点击及 CPC 上限、最大化转化及目标 CPA 设置/清空、最大化转化价值及目标 ROAS 设置/清空、恢复手动 CPC | VERIFIED |
| `set_targeting` | 包含/排除地区、语言变更及恢复 | VERIFIED |
| `set_schedule` | 两个工作日时段设置及清空 | VERIFIED |
| `set_device_bid` | 移动设备排除（0）及恢复（1） | VERIFIED |
| `set_campaign_assets` | SITELINK、CALLOUT、STRUCTURED_SNIPPET 各自创建、替换内容、解除关联 | 8 VERIFIED；1 CALLOUT 替换响应丢失后只读核对为 RECONCILED，没有重发 |
| `remove_resource` | 移除两个广告关联、两个广告组及验收系列 | 5 VERIFIED |

读取及流程工具也逐项调用：连接状态、账户发现/选择/预检、当前账户层级、六类资源列表、GAQL、字段元数据、系列/搜索词报表、预览、提交、操作状态和只读核对均有真实成功记录。账户为直接访问的非 MCC，因此层级返回成功不能代替真实 MCC 子账户测试。关键词研究实际调用仍被 Google 拒绝，错误码 `DEVELOPER_TOKEN_NOT_APPROVED`；本轮没有改变 Cloud 访问等级或提交 Basic 申请。

边界检查：非允许账户、跨账户资源、超出本机日预算上限、修改已开始系列的开始时间均被拒绝；没有提交这些请求。超预算第一次返回 `INTERNAL_ERROR`，随后直接编译和 MCP 重测均返回正确的 `BUDGET_LIMIT`，未生成计划；首个错误原因尚未确定。重复提交已完成的创建计划返回原记录，没有重新创建，移除后的验收系列仍为 REMOVED。

### 清理与最终状态

- 新验收系列及两个广告组、两个广告关联已移除；关键词和否定词在各自清理计划中回读通过，最终无活动关键词。
- 六个本级人工素材关联均为 REMOVED；六条测试素材保留在素材库、未关联，本 MCP 不提供全局素材删除。独立预算保留且验收系列已移除。
- 原 `Clico_MCP_Test_20261020` 保持 **PAUSED、平均日预算 5 USD**；本轮未修改原系列。最终回读时间为北京时间 **17:37**。
- 验收系列当日报表在北京时间 17:29 返回曝光 0、点击 0、费用 0。指标可能延迟；测试期间始终由系列或全部子级暂停阻止投放，不把零报表当作永久数据完整证明。
- 账户未解决写入锁数为 **0**。

此前 0.3.3 的无回执创建仍不能证明未执行。用户只授权处理该笔测试操作后，使用新增的受限 CLI 恢复流程将其记为 **ABANDONED_UNKNOWN（结果未知、人工放弃）**，保留原错误、计划 hash、核对证据及理由，永久禁止重发旧请求，只释放该笔锁；没有将它记为成功或取消。恢复前六类资源均为空，提交已超过 30 分钟；等待和空结果本身不构成未执行证明。北京时间 17:29 再查旧标记仍未发现迟到的暂停副本。此操作不计入上述 49 个成功核对的计划。

### 修复、回归与剩余限制

0.3.4 为只读发现、查询、字段和关键词研究加入最多三次瞬时传输失败重试；每次重新读取完整结果，不拼接失败的分页。写入及 `validate_only` 不自动重试。新增 CLI 恢复仅适用于无回执、原计划完整暂停的 Search 创建，要求具体计划 hash、风险接受参数、理由、等待期和再次远端检查，不新增普通 MCP 解锁工具。恢复的严格条件见 [写入边界](SAFETY.md)。

本轮仍出现间歇性传输失败，包括一次只读重试耗尽和一笔写入回执丢失；因此**不能宣称连接稳定性问题已经彻底解决**。写入丢失响应时先保留锁，再由只读核对确认目标状态，未自动重发写入。

未完成真实验收的特殊场景：共享预算的多系列影响、MCC/多账户路由、尚未开始系列的开始时间修改、同一广告的多个关联、关键词替换时保留标签/复杂跟踪参数，以及其他未列出的参数组合。这些只有相应离线或 SDK 覆盖，不能从主路径通过推断。

本地验证：`pytest -q` **111 passed**；`ruff check`、`ruff format --check`、`git diff --check`、无凭据 stdio smoke（0.3.4、14 工具）及 wheel/sdist 构建通过。新增回归覆盖只读重试、丢弃失败分页、权限错误及写入不重试、预算上限拒绝、受限人工恢复的条件拒绝、审计保留与永久不重发。北京时间 17:40 使用 0.3.4 再次完成账户发现/选择/预检/当前账户层级读取；关键词研究仍返回同一权限拒绝。

私有原始调用、Google 回执、回读和操作账本保留在 `.state/live-qa-20261020.jsonl`、`.state/acceptance-summary-20261020.json` 及操作数据库，不提交公开仓库。当前聊天连接最后回报 0.3.2，实际验收使用独立新进程 0.3.4；已运行连接需要关闭再开启加载新版，无需重新 Google 授权。

## 2026-10-20：扩展能力检查与连接复用，0.3.3

本轮继续使用真实 MCP stdio 验证。下表区分实际读取、Google `validate_only` 和实际提交；提交前校验通过不等于已完成真实写入验收。

| 检查 | 本轮结果 |
| --- | --- |
| 转化动作、受众名单、共享否定词列表 | GAQL 读取成功，分别返回 10、3、1 项；当前未实现这些资源的写入 |
| 标签 | GAQL 读取成功，返回空列表；当前未实现标签写入 |
| 转化动作、受众字段元数据 | 成功返回 32、33 个字段 |
| 附加链接 SITELINK、结构化摘要 STRUCTURED_SNIPPET | Google 提交前校验通过，未实际提交 |
| 切换 MAXIMIZE_CLICKS 并设置 CPC 上限、广告组状态修改 | Google 提交前校验通过，未实际提交 |
| 关键词研究 | Google 拒绝，错误码 `DEVELOPER_TOKEN_NOT_APPROVED` |
| 新建独立暂停 Search 测试系列 | 提交结果 UNKNOWN；无回执，六类资源回读均未找到，保留账户写入锁 |
| 原暂停测试系列 | 再次回读确认 PAUSED、MANUAL_CPC、平均日预算 5 USD |

已在公司 Google Cloud 控制台确认当前项目为 **Explorer**。官方 [访问等级说明](https://developers.google.com/google-ads/api/docs/api-policy/access-levels) 将 KeywordPlanIdeaService 等规划接口列为 Explorer 限制；控制台提示申请 Basic 需要品牌验证。本轮没有申请升级或修改项目权限。

新建请求出现传输错误后没有重发，没有修改账本解除锁，也没有继续提交其他实际写入。后续重新核对仍为 UNKNOWN。更改历史查询成功，但未见这笔创建记录；历史数据可能有延迟，资源与历史暂时为空均不构成“确定未执行”的证据。**此前 0.3.2 清理后的零锁状态已被本轮新 UNKNOWN 取代；本轮结束时有一笔未解决操作，后续写入被阻止。**

本轮连接诊断发现：连续三个新建服务连接的只读探测中两个成功、一个 `ServiceUnavailable`，脱敏特征包含握手失败、连接失败和 socket 关闭；同一个服务连接连续十次只读查询成功。SDK 源码确认 `get_service` 每次创建新的通道。这支持减少重复建连，但不足以确定网络根因，也不足以断言旧写入未执行。

改动：每个 MCP 工具或 CLI 操作在调用期间复用同一服务与登录路径的 SDK 客户端，让预检、校验、提交及回读使用已建立的连接；操作结束关闭连接，下次调用重新加载凭据。缓存按线程及 MCC 登录路径隔离；清理异常不能覆盖已收到的提交结果。不增加自动重试，不更改 UNKNOWN 和账户锁规则。

验证：`pytest -q` **100 passed**；`ruff check`、`ruff format --check`、stdio smoke（0.3.3、14 工具）通过。新增回归覆盖调用内复用、跨调用重新读取凭据、嵌套作用域、登录路径隔离，以及异常清理时保留原结果。真实 0.3.3 MCP 的预检、原系列回读、更改历史读取、UNKNOWN 六类资源核对、结构化摘要提交前校验均成功返回；**尚未获得 0.3.3 的真实写入成功证据，不能宣称传输问题已彻底解决。**

当前聊天连接回报 0.3.2，独立新进程回报 0.3.3。已运行连接需关闭再开启后才会加载此修复，无需重新授权 Google 或重启 Codex。私有逐次证据继续保留在本机 `.state/`。

## 2026-10-20：真实 Search 维护测试与清理，0.3.2

在用户指定的暂停测试 Campaign 上执行真实 MCP stdio 调用。所有 API 写入均先 `preview_change`，检查具体操作，再 `submit_change` 并远端回读；整个过程中 Campaign 保持 PAUSED。此次累计 **24 个计划 VERIFIED**，另有 **1 个 UNKNOWN 经用户授权的后台清理后 RECONCILED**，2 个提交前传输失败的计划为 STALE、没有发送实际写入。修复后的测试通过新启动的 MCP 子进程执行，已运行的旧连接仍需重新连接。

| 真实验证项目 | 结果 |
| --- | --- |
| 账户预检、系列/广告组/广告/关键词/定位/素材列表、GAQL、字段元数据 | 通过；下述查询兼容问题修复后重测通过 |
| 系列报表、搜索词报表 | 通过；最后成功读取分别返回 9 行、526 行，只返回所选字段 |
| 新建暂停广告组，修改名称和 CPC | VERIFIED |
| 新建暂停 RSA，修改标题、最终网址和显示路径 | VERIFIED |
| 关键词修改状态、独立 CPC、网址；替换文本和匹配方式；清空独立 CPC/网址 | VERIFIED |
| 系列级和广告组级否定词新增、移除；正向词移除 | VERIFIED |
| 日预算 5 → 6 → 5 USD | VERIFIED |
| 系列名称及最终网址后缀修改、恢复 | VERIFIED |
| 地区、语言修改及恢复；投放时段设置及清空；移动设备出价系数修改及恢复 | VERIFIED |
| CALLOUT 创建和关联替换 | VERIFIED |
| CALLOUT 关联移除 | API 请求结果 UNKNOWN；用户授权后台解除该测试关联，MCP 只读核对后 RECONCILED |
| 临时广告和广告组移除 | VERIFIED |

本轮修复：

- 广告组/广告查询不能直接关联 `campaign_budget` 字段，改为另查父 Campaign，并保留预算限制、父级状态及提交前漂移检查。
- Campaign 素材按系列过滤时，补选 `campaign.resource_name`，消除 Google 的字段兼容错误。
- 字段元数据查询移除 GAQL 不支持的 `OR`，拆为精确查询和前缀查询，并转义 `LIKE` 中的下划线。
- 固定报表只输出实际 SELECT 的字段，保留已查询的零值和缺失值，去掉未查询的 protobuf 默认值。
- 回读不匹配时明确返回“尚未匹配计划”，不再同时显示肯定的匹配提示。
- `connection_status` 提供服务名、版本，便于确认当前连接是否已更新。

清理验收：六类资源重新读取并与本轮基线比较，原 Campaign、广告组、RSA、关键词、地域和语言均保持原配置；日预算 5 USD，Campaign PAUSED，无自定义时段、无本级人工素材关联。移动设备系数显式恢复为 1.0，与原来的默认无调整等效。临时关键词、否定词、广告和广告组均为 REMOVED；素材库保留两条未关联的测试 CALLOUT，本工具不提供素材库全局删除。账户锁数为 0。

传输边界：本轮真实请求出现间歇性 `TRANSPORT_UNKNOWN`，没有足够证据确定是客户端链路还是 Google 服务端原因。后续报表重读及三次只读账户探测成功，不代表根因已修复。写入结果不明时保留锁，未自动重发写入、直接编辑账本或强制解锁；后台恢复只能证明当前状态满足清理目标，不证明最初请求执行成功。

本地验证：`uv run pytest -q` **98 passed**；`ruff check`、`ruff format --check`、真实 stdio 协议 smoke 通过（服务版本 0.3.2、14 个工具）。新增回归覆盖非法预算关联、素材过滤字段、字段查询语法、报表缺失值与默认值、回读失败提示。私有操作账本、基线和逐次调用证据保留在本机 `.state/`，不提交到公开仓库。

本轮未新增验证：实际启用投放、切换出价策略、广告政策通过后的曝光/转化、SITELINK/STRUCTURED_SNIPPET 素材类型、共享预算、多账户/MCC 写入。这些不能由本次通过项推断。

## 2026-10-20：Search 回读修复及真实预算修改，0.3.1

- Google 会让非共享预算名称跟随广告系列名称，旧版使用独立预算名查找导致误报；现按 [官方字段定义](https://developers.google.com/google-ads/api/fields/v25/campaign_budget#campaign_budget.name) 校验。
- Search 创建回读排除未请求配置的默认 DEVICE 行。旧账本通过只读 reconcile 应用明确的兼容规则，保留原计划及 hash。
- `uv run pytest -q`：**95 passed**。新增覆盖 Google 默认设备与预算改名、旧计划恢复，以及真实预算金额/地域/否定词/落地页不一致时仍保留锁。
- 真实账户验收：此前已获回执的暂停 Search 测试系列，经预算、系列、定位、广告组、关键词、RSA 六组回读全部通过，记录变为 RECONCILED；核对过程没有向 Google 发送写入。
- 按用户明确要求，随后通过 MCP 将该系列平均日预算由 **1 USD 改为 5 USD**：preview_change 的 Google validate_only 通过，submit_change 返回 VERIFIED；另行 GAQL 回读确认 `amount_micros=5000000`、系列保持 PAUSED、预算非共享且只被一个系列引用。没有启用广告。
- 本轮证明上述真实读取、旧操作核对和预算更新路径，不代表所有写入动作均已在真实账户验收。

## 2026-10-20：移除本机审批，0.3.0

本轮按用户要求删除独立审核窗口、`team-ads approve` 命令和逐次 macOS 身份验证。流程改为 `preview_change → submit_change → 远端回读`，Google OAuth 与 OS Keychain 实现保持不变。本轮未使用真实凭据、未向 Google Ads 发送写入，也未修改现有操作账本或解除已有账户锁。

| 检查 | 结果 | 证据边界 |
| --- | --- | --- |
| `uv run pytest -q` | **89 passed** | Google 响应使用离线模拟器，含真实 SDK 消息校验 |
| stdio 完整写入流程 | 通过 | 真实子进程完成预览、直接提交、回读、重复提交及创建后修改，无外部审批注入 |
| 计划与恢复约束 | 通过 | 无预览、篡改、过期、权限变化、共享影响、并发、未知结果和账户锁继续受控 |
| 旧账本兼容 | 通过 | 旧 APPROVED 计划重启后可提交；历史事件保留；过期或篡改的旧计划仍拒绝 |
| OAuth 回归 | 通过 | PKCE / loopback / 离线授权与安全存储测试保留；退出登录使待提交计划失效 |
| `ruff check` / `format --check` | 通过 | src、tests、scripts |
| 无凭据协议 smoke | 通过 | server 0.3.0，14 工具，initialize / list / call 成功，真实 Google 写入为 0 |
| CLI 帮助与参数 | 通过 | 保留 auth / show-operation / submit / reconcile，移除 approve |
| `uv lock --offline` / `uv sync --frozen` / `uv build` | 通过 | 已安装 0.3.0；wheel 与 sdist 不再包含审批 Python / Swift 组件，也不含私有状态 |
| `git diff --check` | 通过 | 无空白错误 |

已运行的 MCP 服务不会热加载此改动，需重启连接后生效。无需因为本次升级重新进行 OAuth 登录。本轮离线通过不替代新版在真实账户上的写入验收，也不代表解决了此前操作的回读差异。

## 历史记录：2026-10-12

以下是当时 0.1.0 的验收范围，其中原生审批组件现已移除，不再适用于当前流程。当时没有使用真实 Google 凭据，没有对 Google Ads 进行任何真实写入。

| 检查 | 结果 | 证据边界 |
| --- | --- | --- |
| `uv sync --frozen` / 依赖锁 | 已完成 | Python 3.12；google-ads 33.0.0；MCP 2.3.0；API v25 |
| `uv run pytest -q` | **40 passed** | 包含离线故障注入，不等于 Google 测试账户验收 |
| stdio 子进程 initialize | 通过 | 协商协议版本 2025-11-25，server 0.1.0 |
| tools/list、tools/call | 通过 | 13 工具；真实协议、无全局 MCP 配置改动 |
| 完整 Search 变更闭环 | 模拟通过 | Google 响应由离线状态模拟器提供，审批由测试边界注入 |
| SDK 请求/结果模型 | 通过 | 实际 v25 protobuf 消息构造、序列化/结果反序列化 |
| 官方字段名检查 | 61 个通过 | 从官方 v25 字段页核对静态字段可选择性；不证明任意字段组合兼容 |
| `ruff check` / `format --check` | 通过 | src、tests、scripts |
| Python 源码与示例参数 | 通过 | Search 示例由严格 schema 校验 |
| `uv build` | wheel + sdist 构建成功 | 检查 wheel 包含 approval.swift，产物无 .state/work/outputs/.venv |
| Swift 本机审批组件 | 编译通过 | AppKit + LocalAuthentication；未完成人工窗口/Touch ID/密码验收 |
| Git whitespace 检查 | 通过 | 独立目录，codex 分支，无远程 |

主要测试覆盖：账户/MCC 路径隔离、只读策略、精确 micros/币种、共享预算完整影响、重复 request_key、重复提交、并发 claim、计划过期、远端变化、权限再次预检、凭据 epoch 变化、token 刷新失败、撤销结果不明、OAuth PKCE/state、错误脱敏、部分失败、超时执行前/执行后、成功回执后回读失败、进程重启后恢复、未知结果账户锁。

可复现无凭据协议检查：

```sh
uv run python scripts/smoke_protocol.py
```

预期有 `initialize/tools_list/tools_call: passed`、`google_connected: false`、`google_mutations: 0`。此脚本使用临时空配置，不读取 OS Keychain。

当时尚待验收：真实 OAuth、账户读取和测试写入。原生审批界面现已删除，无需继续验收；真实广告启用、花费与转化效果不能由本地测试证明。
