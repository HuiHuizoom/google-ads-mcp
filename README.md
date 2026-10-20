# Clico Google Ads MCP

团队自用、独立运行的 Google Ads MCP 后端。Python + 官方 Google Ads 客户端 + MCP stdio。无需 Adspirer 账户或托管后端。

MCP 连接名为 `clico-google-ads`；命令行入口仍为 `team-ads` / `team-ads-mcp`。

写入流程：**预览校验 → 提交 → 远端回读**。无需独立审批窗口，也不逐次要求 Touch ID / Mac 密码。Google OAuth 登录和 OS Keychain 凭据存储保留；真实账户接入与写入验收需分别核对，详见 [验收记录](docs/VERIFICATION.md)。

## 启动

```sh
git clone https://github.com/HuiHuizoom/google-ads-mcp.git
cd google-ads-mcp
uv sync --frozen
uv run team-ads doctor
uv run team-ads auth login
uv run team-ads serve
```

0.5.2 起，新安装默认使用公司项目 `clico-485215` 和 Clico 广告账户 `2563778519`（直接访问、允许写入、每个预算资源的平均日预算参数上限 100 USD）。无需同事重复执行 `configure`。已有本地配置原样保留，包括只读或空账户列表。

公司 Desktop OAuth 客户端 JSON 由公司私下提供，放在 `.state/oauth-client.json`，也可通过 `auth login --client-file /本机路径/客户端.json` 指定。公开仓库不包含此文件或任何登录凭据。每台电脑分别运行 `auth login`，在 Google 页面选择账号并授权；公司配置本身不会授予 Google 访问权限。

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
| `list_resources` | 当前设置和资源 ID；包含素材库、PMax 素材组及信号，支持适用的父级/类型/名称过滤 |
| `inspect_image_asset` | 本机图片格式、尺寸、大小、SHA-256；上传前检查，不裁剪 |
| `performance_report` | 明确日期的 Campaign / 搜索词报表 |
| `keyword_ideas` | 地域、语言、种子关键词研究 |
| `preview_change` | before/after、SDK 校验、Google validate_only、持久化计划 |
| `operation_status` | 计划、提交状态、操作账本 |
| `submit_change` | 提交已预览校验且仍有效的计划并回读 |
| `reconcile_operation` | 核对不明结果；不重新写入 |

现在有 **15 个 MCP 工具、41 种受控写入动作**，包括创建后的维护：

| 对象 | 可执行的操作 |
| --- | --- |
| Search 系列 | 创建；修改名称、开始/结束时间、网络、地域匹配方式、网址后缀；暂停/启用；移除 |
| 预算与出价 | 调整平均日预算；手动 CPC、最大化点击、最大化转化（可选目标 CPA）、最大化转化价值（可选目标 ROAS）；修改广告组/关键词 CPC |
| 广告组 | 在已有系列中新增；修改名称/出价；暂停/启用；移除 |
| RSA 广告 | 在已有广告组中新增；修改标题、描述、落地页、显示路径；暂停/启用；移除关联 |
| 关键词/否定词 | 添加、移除、替换文字/匹配方式；正向关键词可暂停/启用、修改/清除单独 CPC 和网址；否定词支持系列及广告组级 |
| 定位与排期 | 创建后调整包含/排除地区、语言、整周投放时段；调整现有设备系数或排除设备 |
| 系列素材 | 附加链接、宣传信息、结构化摘要的添加、内容替换及关联移除 |
| 标准非零售 PMax | 创建暂停系列及素材组；修改预算、名称、日期、定位、排期、智能出价目标、最终网址扩展；暂停/启用/移除 |
| PMax 素材组 | 创建后修改名称、落地页、显示路径；查询、添加、替换、移除已有素材关联；修改商家名和 Logo 关联 |
| PMax 信号 | 已有 Audience 受众信号、搜索主题的查询、添加、替换、移除 |
| 素材库 | 文本创建、本机图片上传、已有 YouTube 视频 ID 注册；查询并复用已有素材 |
| Display | 标准展示系列及自适应展示广告；创建后预算、出价、广告组、文案/素材替换、定位、状态和移除 |
| Demand Gen | 图片/视频广告；创建后维护、已有 Audience、广告组级地区和语言、优化型定向 |
| App | Android/iOS 安装推广；预算、目标 CPA/ROAS、应用内优化目标、广告组及广告维护 |
| 转化配置 | 本账户网站/离线转化动作增改删；账户/系列目标设置、来源切换；自定义目标增改删 |

完整 Search 创建仍默认 Manual CPC、仅 Google Search、PRESENCE 定位，最多 5 个广告组；创建后可通过上述接口修改。新系列、广告组和 RSA 固定 PAUSED，每个计划最多 500 个操作。已有资源不要求由本 MCP 创建。具体输入、列表替换/清空语义、Google 不可修改字段的替换方式，见 [创建后维护](docs/MAINTENANCE.md)。

PMax 新系列和素材组也固定 PAUSED；素材上传不自动关联投放。PMax 输入、品牌指南、最低素材组合与创建后修改，见 [PMax 与素材管理](docs/PMAX.md)。本版不创建 Audience 定义、不上传 MP4 至 YouTube、不做图片裁剪，也不管理 Merchant Center 商品源或旅游/本地服务变体。

Display、Demand Gen、App 的具体广告格式和维护边界见 [渠道广告管理](docs/CHANNELS.md)。新对象均 PAUSED；广告内容替换会创建新 ID，旧统计不继承。转化写入范围见 [转化配置](docs/CONVERSIONS.md)：不安装埋点、不上传事件、不修改 GA4/Firebase 导入动作的设置。标签和共享否定词列表的专用写入仍未实现。

读取结果注明数据源、API 版本、获取时间、账户时区、币种和日期口径。未知的数据截止时间为 null；空结果不解释成零；转化配置存在不证明事件回传成功。

## 验证

```sh
uv run pytest -q
uv run ruff check src tests
uv run ruff format --check src tests
```

自动化测试包含真实 MCP stdio 子进程 initialize / tools/list / tools/call、真实 v25 SDK 消息序列化、离线状态模拟、OAuth PKCE/state 与故障注入，不使用已有 Google 凭据，目前 166 项通过。新增覆盖公司默认值、保留已有只读/空白名单、显式配置其他项目、缺失客户端文件指引，以及无需 configure 的登录保存流程。历史真实账户验收完成原有 20 种写入的 49 个计划（48 VERIFIED、1 RECONCILED），以及 PMax/素材的 23 次写入（全部 VERIFIED）。

0.5.1 已在专用暂停对象完成 Display、Demand Gen 图片、三种转化动作及账户/系列/自定义目标的真实主路径验收和清理：**65 个写入计划，64 VERIFIED、1 RECONCILED**。修复了 Display 长标题回读，并移除 Google 拒绝的转化 HIDDEN 写入选项。Demand Gen 目标 ROAS/最大化转化价值仍因转化不足被拒；App、视频、Audience 关联尚缺实际对象，关键词研究仍受访问等级限制。具体通过项、未通过项和清理结果见 [验收记录](docs/VERIFICATION.md)，不把结构写入成功等同于真实投放或转化回传。

## 项目边界

公开仓库：[HuiHuizoom/google-ads-mcp](https://github.com/HuiHuizoom/google-ads-mcp)，默认分支为 `main`。凭据默认存 OS Keychain，状态及审计账本在忽略的 `.state/`，不写入源码和聊天。克隆仓库包含公司默认项目/账户配置，不包含 OAuth 客户端文件或账户授权；每台主机需按 [接入与权限](docs/CONNECT.md) 获取公司客户端文件并分别登录。

这是一台可信主机上的团队工具，不是租户隔离服务。获准连接此 MCP 的客户端可在账户配置允许的范围内提交写入，不再有独立本机人工审批。拥有同一 OS 用户完整文件/代码权限的进程仍属于可信边界。多人远程使用需要独立服务身份、客户端身份验证和权限存储，不能直接把 stdio 包装成无认证 HTTP。
