# 验收记录

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
