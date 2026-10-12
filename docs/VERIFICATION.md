# 本地验收记录

核对日期：2026-10-12（Asia/Shanghai）。本轮没有使用真实 Google 凭据，没有对 Google Ads 进行任何真实写入。

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

下一阶段仍需：用户指定 Cloud 项目与测试 Ads 路径、完成 OAuth/Cloud 访问等级准备；先核对真实读取，再单独授权一条测试写入，并完成人工审批界面验收。真实广告启用、花费与转化效果不在本轮已验证范围内。
