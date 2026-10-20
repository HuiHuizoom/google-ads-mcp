# Team Ads MCP

团队自用、独立运行的 Google Ads MCP 后端。Python + 官方 Google Ads 客户端 + MCP stdio。无需 Adspirer 账户或托管后端。

写入流程：**预览校验 → 提交 → 远端回读**。无需独立审批窗口，也不逐次要求 Touch ID / Mac 密码。Google OAuth 登录和 OS Keychain 凭据存储保留；真实账户接入与写入验收需分别核对，详见 [验收记录](docs/VERIFICATION.md)。

## 启动

```sh
cd /Users/huitong/Workspace/google-ads-mcp
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
| `list_resources` | 读取可维护资源的当前设置、完整 ID；支持按系列、广告组和共享广告过滤 |
| `performance_report` | 明确日期的 Campaign / 搜索词报表 |
| `keyword_ideas` | 地域、语言、种子关键词研究 |
| `preview_change` | before/after、SDK 校验、Google validate_only、持久化计划 |
| `operation_status` | 计划、提交状态、操作账本 |
| `submit_change` | 提交已预览校验且仍有效的计划并回读 |
| `reconcile_operation` | 核对不明结果；不重新写入 |

现在有 **14 个 MCP 工具、20 种受控写入动作**，包括创建后的维护：

| 对象 | 可执行的操作 |
| --- | --- |
| Search 系列 | 创建；修改名称、开始/结束时间、网络、地域匹配方式、网址后缀；暂停/启用；移除 |
| 预算与出价 | 调整平均日预算；手动 CPC、最大化点击、最大化转化（可选目标 CPA）、最大化转化价值（可选目标 ROAS）；修改广告组/关键词 CPC |
| 广告组 | 在已有系列中新增；修改名称/出价；暂停/启用；移除 |
| RSA 广告 | 在已有广告组中新增；修改标题、描述、落地页、显示路径；暂停/启用；移除关联 |
| 关键词/否定词 | 添加、移除、替换文字/匹配方式；正向关键词可暂停/启用、修改/清除单独 CPC 和网址；否定词支持系列及广告组级 |
| 定位与排期 | 创建后调整包含/排除地区、语言、整周投放时段；调整现有设备系数或排除设备 |
| 系列素材 | 附加链接、宣传信息、结构化摘要的添加、内容替换及关联移除 |

完整 Search 创建仍默认 Manual CPC、仅 Google Search、PRESENCE 定位，最多 5 个广告组；创建后可通过上述接口修改。新系列、广告组和 RSA 固定 PAUSED，每个计划最多 500 个操作。已有资源不要求由本 MCP 创建。具体输入、列表替换/清空语义、Google 不可修改字段的替换方式，见 [创建后维护](docs/MAINTENANCE.md)。

读取结果注明数据源、API 版本、获取时间、账户时区、币种和日期口径。未知的数据截止时间为 null；空结果不解释成零；转化配置存在不证明事件回传成功。

## 验证

```sh
uv run pytest -q
uv run ruff check src tests
uv run ruff format --check src tests
```

测试包含真实 MCP stdio 子进程 initialize / tools/list / tools/call、真实 v25 SDK 消息序列化、离线状态模拟、OAuth PKCE/state 与故障注入。没有偷偷使用任何已有 Google 凭据，也没有将模拟数据作为 Google 测试账户结果。

## 项目边界

本目录是独立 Git 仓库，没有远程仓库。凭据默认存 OS Keychain，状态及审计账本在忽略的 `.state/`，不写入源码和聊天。不修改 Codex 全局 MCP 配置、不部署、不改 Google Cloud、不触及 Invoko。

这是一台可信主机上的团队工具，不是租户隔离服务。获准连接此 MCP 的客户端可在账户配置允许的范围内提交写入，不再有独立本机人工审批。拥有同一 OS 用户完整文件/代码权限的进程仍属于可信边界。多人远程使用需要独立服务身份、客户端身份验证和权限存储，不能直接把 stdio 包装成无认证 HTTP。
