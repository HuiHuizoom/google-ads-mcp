# 本地验收记录

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
