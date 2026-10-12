# Team Ads MCP

团队自用、独立运行的 Google Ads MCP 后端。Python + 官方 Google Ads 客户端 + MCP stdio。无需 Adspirer 账户或托管后端。

当前状态：**本地可运行；尚未连接真实 Google Ads；尚未验证真实写入。** 只读/写入协议与故障恢复已通过离线测试，真实 Google 政策、账户角色和资源兼容性仍需授权后验收。

## 启动

```sh
cd /Users/huitong/Documents/Codex/2026-10-12/google-ads-mcp
uv sync --frozen
uv run team-ads doctor
uv run team-ads serve
```

`serve` 等待 MCP 客户端通过 stdin 发送 JSON-RPC，终端没有欢迎画面是正常的。无需凭据即可 initialize、列出工具、查看 connection_status；未授权读取会返回有恢复指引的错误。stdout 仅用于协议。

完整接入步骤见 [接入与权限](docs/CONNECT.md)，写入保障与限制见 [写入模型](docs/SAFETY.md)，参考来源见 [研究记录](docs/RESEARCH.md)。

## 能力

| 工具 | 作用 |
| --- | --- |
| `connection_status` | 本机配置、版本、下一步；不声称真实已连接 |
| `discover_accounts` | OAuth 用户直接可访问账户 |
| `select_account` / `preflight` | 检查允许列表及明确的 MCC 路径，返回账户币种/时区 |
| `account_hierarchy` | MCC 层级、manager、status、币种/时区 |
| `field_metadata` | GAQL 字段和兼容性查询 |
| `run_gaql` | 只读、有行数上限的 GAQL |
| `performance_report` | 明确日期的 Campaign / 搜索词报表 |
| `keyword_ideas` | 地域、语言、种子关键词研究 |
| `preview_change` | before/after、SDK 校验、Google validate_only、持久化计划 |
| `operation_status` | 计划、审批状态、操作账本 |
| `submit_change` | 提交独立本机审批过的具体计划并回读 |
| `reconcile_operation` | 核对不明结果；不重新写入 |

支持的变更：广告组关键词、Campaign 否定词、移除关键词、平均日预算、Campaign/AdGroup/AdGroupAd 暂停/启用，以及完整的 **PAUSED Search** 结构。创建包含独立预算、地域/语言、广告组、匹配类型明确的关键词、RSA 和可选 Campaign 否定词；一条原子 mutate。首版明确采用 Manual CPC、仅 Google Search、PRESENCE 地域定位。Search 创建最多 5 个广告组、500 个操作，不含自动素材、sitelinks、自动出价、PMax 或 Skills。

读取结果注明数据源、API 版本、获取时间、账户时区、币种和日期口径。未知的数据截止时间为 null；空结果不解释成零；转化配置存在不证明事件回传成功。

## 验证

```sh
uv run pytest -q
uv run ruff check src tests
uv run ruff format --check src tests
```

测试包含真实 MCP stdio 子进程 initialize / tools/list / tools/call、真实 v25 SDK 消息序列化、离线状态模拟、OAuth PKCE/state 与故障注入。没有偷偷使用任何已有 Google 凭据，也没有将模拟数据作为 Google 测试账户结果。

## 项目边界

本目录是独立 Git 仓库，分支 `codex/independent-ads-mcp`；没有远程仓库。凭据默认存 OS Keychain，状态及审计账本在忽略的 `.state/`，不写入源码和聊天。不修改 Codex 全局 MCP 配置、不部署、不改 Google Cloud、不触及 Invoko。

这是一台可信 macOS 主机上的团队工具，不是租户隔离服务。拥有同一 OS 用户完整文件/代码权限的进程仍属于可信边界。多人远程使用需要独立服务身份、客户端身份验证、权限存储和审批通道，不能直接把 stdio 包装成无认证 HTTP。
