# 参考与独立设计依据

核对时间：2026-10-12，Asia/Shanghai。此前所说的 “SBR” 已由用户明确为 **Adspirer**，具体参考为 [create-search-campaign](https://www.adspirer.com/docs/api-reference/google-ads/create-search-campaign)。

## 官方读取与版本

- [googleads/google-ads-mcp](https://github.com/googleads/google-ads-mcp)，HEAD `9a78cbdceebae71a6bc8483bdec333695834ef9c`。本地重新查询远程 HEAD 与已有快照一致。pyproject 版本 `0.0.5`，直接引用 v25。读取参考其账户发现、GAQL 和字段元数据思路，本项目独立实现，没有复制其源文件。
- 运行环境固定 Python 3.12、`google-ads==33.0.0`、`mcp==2.3.0`、`google-auth-oauthlib==1.5.0`；直接依赖与全部传递依赖固定在 `uv.lock`，使用 `uv sync --frozen`。
- 本机实际安装的 GoogleAdsClient 支持 v25/v24/v23，构造参数 developer_token 默认 None。实际测试构造 v25 多资源 Mutate 请求并序列化，证明 SDK 支持此形状；不证明 Google 账户政策会接受。
- [官方客户端版本表](https://developers.google.com/google-ads/api/docs/client-libs) 中 Python v25 最低版本为 31.2.0，33.6.0 是 PHP 的条目，不能混淆。
- [弃用时间表](https://developers.google.com/google-ads/api/docs/sunset-dates) 在核对日列出 v25.2 于 2026-09-23 发布，v25 系列预计 2027-08 sunset。HTTP/gRPC 版本仍写 v25。升级时单独修改依赖和 API_VERSION、查迁移说明、运行离线检查，再在授权测试账户做读取/预检/暂停创建；不自动跟随 latest。

## OAuth、Cloud 项目和 Ads 角色

[Developer Token 迁移正文](https://developers.google.com/google-ads/api/docs/api-policy/developer-token) 明确 2026-09-09 sunset，旧头暂时忽略，访问等级属于 OAuth 凭据所在项目。该页自动生成的摘要及一些旧 README 仍沿用 token 表述；以有日期的正文、安装 SDK 和实际预检为准。

[Cloud 项目设置](https://developers.google.com/google-ads/api/docs/oauth/cloud-project) 与 [访问等级](https://developers.google.com/google-ads/api/docs/api-policy/access-levels) 提供 Console 申请入口；本项目没有找到并验证能可靠查询具体 access level 的公开 API，因此返回 unknown 和人工入口，不伪造“已获批”。新项目默认 Test；生产访问取决于获授资格。OAuth 同意、Ads 用户角色、MCC 路径与 Cloud API 访问等级分别诊断。

本机 OAuth 采用 [Google Desktop 流程](https://developers.google.com/identity/protocols/oauth2/native-app)：loopback、PKCE、state、offline。Google 的 adwords scope 不提供应用级只读/读写拆分，读取限制还需账户角色与本后端白名单；不能把 OAuth 同意等同于允许所有广告写入。

## Adspirer 当前公开边界

[amekala/ads-mcp](https://github.com/amekala/ads-mcp) HEAD `68d3cacf6d3ce12809e6363cf2b62bcb4e539c06`，重新核实与已有快照一致。公开仓库描述 Google Ads 151 工具；[OpenAPI](https://www.adspirer.com/docs/api-reference/openapi.json) 的 google-ads 分类实际 51 项、所有类别共 178 项，info.version `1.0.0`。Gemini 安装包标记 `1.0.0`，Claude plugin 未提供可用版本；不能从这些值推出托管服务器的内部版本。

直接读取公开 OAuth discovery：issuer 为 `https://mcp.adspirer.com`，authorize/token/register 端点在同域，支持 PKCE S256，公开 scopes 包括 openid/profile/email、mcp.full、campaigns:read/write、performance:read、accounts:read。这是 **MCP 客户端到 Adspirer** 的权限层。Google 账户另行绑定和授权，不能将上述 scopes 当成 Google 官方 scopes。

REST 使用 Personal Access Token；文档提供 Idempotency-Key 并要求多账户显式 ID。未登录实时 MCP 工具 schema、token 保存/刷新内部实现、Google Cloud 迁移完成情况均未取得证据。不能声称知道其最新私有实现，也不依赖该服务运行。

用户给定的 Search 接口覆盖预算、广告组、RSA、关键词与定位，并提醒错误后不要自动重试。我们采纳流程意图，独立设计强类型资源、精确本币金额和持久化恢复。第三方要求“15 标题/4 描述”等不是 Google 的必需最小值；本实现按 [官方 RSA](https://developers.google.com/google-ads/api/docs/responsive-search-ads/overview) 使用 3–15 标题、2–4 描述，分别 30/90 字符单位并计算全角宽度，最终仍由 Google validate_only 检查政策。

该仓库 LICENSE/README 许可文字存在不一致，未复制代码。公开接口研究不等于获取托管服务私有源代码。

## 另外两套写入实现

| 参考 | 固定提交 | 可借鉴及不足 |
| --- | --- | --- |
| [burhan29ee/google-ads-mcp-server](https://github.com/burhan29ee/google-ads-mcp-server) | `098493fc39ace45d822a33d7478a39b836685b51` | validate_only + confirm；但 confirm 是模型参数，README 仍是旧 token 接入 |
| [get-mcp-ads/google-ads-mcp-server](https://github.com/get-mcp-ads/google-ads-mcp-server) | `1df4f0f293ac23660ace188946546f0613f52e7f` | 原子计划、plan hash、PAUSED、超时核对提示；confirm/hash 本身仍不是可信用户批准 |

研究止于这两套确实相关的实现及官方/Adspirer。采用原子 GoogleAdsService.Mutate、SDK 类型和 [Google mutate 约束](https://developers.google.com/google-ads/api/docs/mutating/best-practices)，自己补齐 OS 用户审批、持久化账本、账户锁、状态变化与回读证明。

## 未声称的能力

不是 Adspirer 完整复刻。没有私有策略算法、全平台支持、自动投放 Skills、多用户远程 HTTP、安全隔离部署或真实广告效果验证。Search 首版限定手动 CPC，以便在转化回传尚无证据时保持创建行为明确；后续自动出价/附加资产可在当前计划机制上扩展。
