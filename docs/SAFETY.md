# 写入边界与恢复

## 信任与授权

MCP 客户端可提出计划、读取计划，并直接提交已校验且仍有效的计划。预览返回账户、输入、before/after、实际 operations、警告、到期时间和 plan hash，默认 15 分钟到期。没有独立审批窗口、`approve` 命令或逐次 Touch ID / Mac 密码验证。Google OAuth 登录仍由用户在浏览器完成，凭据由 OS Keychain 管理；授权失效时需要重新登录。

OS Keychain、代码和本机私有状态目录属于同一个可信 OS 用户。连接本 MCP 的客户端在账户白名单、写入开关和预算边界内具有提交权，不再通过额外弹窗证明用户在场。计划 hash、账户路由、权限配置及授权 epoch 仍在提交前核对。当前不提供 HTTP 监听；远程多用户使用需要另行实现客户端身份验证与权限隔离。

## 提交状态机

`VALIDATED → IN_FLIGHT → VERIFIED`

- VALIDATED：本地约束、官方 SDK 结构和 Google validate_only 均已通过（离线测试会明确标注模拟来源）。
- IN_FLIGHT：账本已持久化，账户锁已取得，可能已经发送。进程崩溃后先 reconcile。
- VERIFIED：Google 回执和完整回读通过，且当前结构与计划一致；不证明政策审核、曝光、转化。
- STALE：进入提交后、发送写入之前发现远端变化或权限/校验失败，需要重新预览。过期或配置变化在进入提交前直接报错，不发送写入。
- REJECTED：Google 对写请求给出确定性拒绝。不会自动重试。
- UNKNOWN：网络、内部错误、部分失败异常或回读异常使结果不明，保留账户锁。
- READBACK_MISMATCH：有回执，但结构/内容未完全匹配，保留账户锁。
- RECONCILED：只读核对发现预期完整状态，可以解除本地锁；不宣称绝对因果归属或 exactly-once。
- ABANDONED_UNKNOWN：维护者明确接受一笔无回执暂停创建的未知结果，保留审计、永久禁止重发并只释放该笔锁；不证明未执行、成功或已取消。

旧账本的 APPROVED 状态和审批字段仅为兼容历史记录保留；尚未提交的旧计划仍须通过同样的完整性、权限、时效及远端校验。新计划不再产生 APPROVED 事件。升级不删除历史事件，也不解除 UNKNOWN / READBACK_MISMATCH 等未解决操作的账户锁。

同一 request_key 对不同输入返回冲突；同一 operation_id 不重复发送。SQLite 事务保护计划 claim，进程锁防止仍在发送时误做恢复。UNKNOWN 阻止该账户其他写入（包括经另一 MCC 路径的同一 advertiser）；可以继续读取。

## Google 边界

每个计划一次 `GoogleAdsService.Mutate`，`partial_failure=false`，关闭 SDK 自动重试。需要关联新资源时使用唯一负临时 ID，依赖对象排在引用者之前。新 Campaign、AdGroup、RSA、PMax AssetGroup 固定 PAUSED；整套 Search 创建的新关键词可以 ENABLED，但新父级都暂停。向已有启用资源添加关键词/素材或修改配置可能立即影响投放。

预算使用 Decimal 转正整数 micros；需要显式匹配账户币种。更新预算必须列全 Campaign，预算 reference_count 与可见引用数也必须一致；共享影响写进预览。提交前重读原始状态与影响集合，并再次 validate_only。

Google Ads 没有在这里可用的远端 compare-and-swap：最后一次读取和 mutate 之间，其他用户/工具仍可能并发修改。账户锁只协调本项目实例。回读不匹配会保留锁并提示核对；不能宣称消除了所有并发风险。避免同时用 Ads UI 或其他程序修改相同资源。

请求返回后先保存回执，再完整回读。整套 Search 和新增广告组使用计划 ID 名称标记定位；单独新增广告、关键词替换和素材关联通过父级集合的内容与数量核对。检查预算、Campaign 配置、地域/语言、各广告组、关键词/否定词、文案和网址等所选范围。缺一项都不记为 VERIFIED。标记被改名或外部并发修改集合可能导致恢复无法匹配，需人工核对。

Search 创建的非共享预算名称按 Google 的规则与 Campaign 名称一致；创建回读排除未在该计划中配置的 DEVICE 行，设备出价变更仍使用其专用回读。0.3.1 对旧 Search 计划提供这两项确定的回读兼容规则，在 reconciliation 证据中记录规则名；不改写原计划、hash 或 Google 资源。预算金额、地域、语言、否定词、广告组、文案和网址仍需全部匹配，才能解除旧操作的账户锁。

创建后维护使用字段级 update mask；省略 patch 字段保留原值。原地修改共享 RSA 前必须完整声明所有未移除广告关联，提交前重新检查。素材修改通过本系列重新关联实现，避免全局修改共享素材；删除资源与不可变关键词的替换都在预览中明确显示。详细的列表清空、移除和替换语义见 [创建后维护](MAINTENANCE.md)。

PMax 创建使用唯一名称标记，并回读品牌、定位、预算、系列、素材组和关联集合。替换素材/信号只操作明确给定的维度；其他类型及自动来源素材保留。素材库上传使用 Google 回执中对应 operation 的资源 ID 验证，不能按名称猜测，因为 Google 可能去重并返回已有资源。丢失回执的独立素材上传保持 UNKNOWN，即使查到同名同尺寸对象也不能视为已完成；不自动重传。已有的人工放弃 Search 创建命令不适用于这些新操作。

图片预览和提交均核对本机文件 SHA-256；预览后换文件会变为 STALE。私有计划保留实际上传内容，用于 hash 和重编译核对；MCP/CLI 输出以摘要和字节数替代图片 base64。状态目录可能包含图片和业务素材，应视为本机私有数据保管。详见 [PMax 与素材管理](PMAX.md)。

UNKNOWN 下“暂时查不到”不能证明没有创建，系统保持锁定，禁止自动重发或自动清账。若多次核对仍无法确定，使用 Google Ads 更改历史/支持渠道核对，再由维护者设计具体恢复；此版本故意没有通用 force-unlock 或重发按钮。重新登录后可以只读核对旧计划；新写入必须重新预览。

### 人工放弃一笔未知的暂停创建

0.3.4 提供受限 CLI `team-ads abandon-unknown-creation`，不向 MCP 增加解锁工具。只有维护者接受可能出现迟到暂停副本的风险、决定不再重发该笔请求时使用：

```sh
uv run team-ads abandon-unknown-creation OPERATION_ID \
  --expected-hash EXACT_PLAN_HASH \
  --reason "核对证据及明确接受未知结果风险的原因" \
  --accept-unknown-outcome
```

必须同时满足：原计划完整性及账户允许列表通过、hash 与人工核对的具体计划一致、状态为 UNKNOWN、错误为 TRANSPORT_UNKNOWN、没有任何 receipt 字段、仅包含完整且明确 PAUSED 的 Search 创建结构、没有正在运行的提交、首次提交已超过 30 分钟、再次读取的全部创建检查均未找到对象。任一条件不满足时保留原状态和锁。30 分钟是本工具的恢复门槛，不是 Google 的执行时限，也不是未执行证明。

操作保留原结果，追加 `manual_resolution` 及 ABANDONED_UNKNOWN 事件，并在同一账本事务中只释放该计划的锁。旧 request_key / operation_id 永不重新发送。之后需另行读取旧计划名称标记，核查是否迟到出现暂停副本；已放弃状态本身不会自动把结果变为成功。

## 数据与安全输出

只读搜索设置行数限制，超过上限报错而不是提供假完整结果。预定义报表要求明确起止日期并按账户时区校验；原始 GAQL 的日期范围来自 query 本身。source、fetched_at、timezone、currency 和未知 data_through 随结果返回。关键词历史指标不等于当前流量或未来效果；转化设置不是实际回传证明。

发现账户、GAQL、字段查询和关键词研究只对瞬时传输失败最多尝试三次；完整读取成功后才返回，失败的部分分页不复用。权限/授权/输入错误不因此重试，最终连接失败返回 `READ_UNAVAILABLE`。写入和提交前 `validate_only` 仍关闭自动重试。

凭据不进入 MCP 输入输出、日志、SQLite 或 Git。异常只返回稳定错误码、Google request_id 和枚举错误码，避免转发可能含 headers/token 的原始异常。Google SDK 的原始请求日志在入口禁用。数据库含账户结构/文案等业务数据，属于本机私有数据，应跟随主机备份与访问策略管理。

## 验证分层

- 已验证：真实 MCP 协议、真实锁定 SDK 消息、持久化/竞争/去重、精确金额、账户路由隔离、共享预算、刷新失败、PKCE/state、计划变化、超时未知、回读失败及恢复。
- 模拟验证：自动化回归中的 Google 读取、validate_only、mutate 和远端回读；真实 SDK 和 stdio 协议不会将模拟结果升级为真实账户证据。
- 真实账户的连接、写入与回读需单独验收；投放和转化效果不能由结构回读证明。各轮验证范围见 [验收记录](VERIFICATION.md)。
