# 本地接入、Cloud 项目与账户权限

核对日期：2026-10-12（Asia/Shanghai）。第一步建议使用 Google Ads 测试账户；真实账户写入和投放需要另行批准。

## 1. 用户负责的 Google 侧准备

1. 指定自己的 Google Cloud 项目，启用 Google Ads API。不要仅凭项目名字推断归属。
2. 在该项目的 Google Ads API Overview 检查/申请访问等级。Test 面向测试账户；生产读取也需要相应生产访问资格。API 服务启用、OAuth 应用授权和 API 访问等级是不同条件。
3. 配置 OAuth 同意屏幕，使用 **Desktop app** 客户端，下载本机 JSON 文件。Testing 状态需将授权用户加入测试用户；外部 Testing 授权的刷新令牌可能受 Google 的短期有效期限制。不要把密钥贴进聊天。
4. 授权 Google 用户须已被授予目标 Ads 账户访问权限，写操作需要适当 Ads 角色。经 MCC 访问时，明确 manager ID 与最终 advertiser ID，不能把 MCC 当成广告写入目标。

Cloud Console 入口：`https://console.cloud.google.com/apis/api/googleads.googleapis.com/overview?project=YOUR_PROJECT_ID`。

2026-09-09 起 Developer Token 已退役；访问等级取决于产生 OAuth 凭据的 Cloud 项目。项目配额标识不能替换 OAuth 所属项目。代码不要求 Developer Token，也不能代替 Google 自动授予访问等级。[官方迁移文档](https://developers.google.com/google-ads/api/docs/api-policy/developer-token)、[项目设置](https://developers.google.com/google-ads/api/docs/oauth/cloud-project)、[账户权限模型](https://developers.google.com/google-ads/api/docs/oauth/access-model)。

如果之后指定使用 Invoko Google Cloud/Composio：开始外部配置前先核实 Chrome 为绿色 `hey invoko` 身份，并对具体改动取得批准。本项目当前没有进行这类配置。

## 2. 配置本地白名单

以下 ID/项目名是占位示例，替换后再运行；金额字符串使用账户本币。默认只读。

```sh
uv run team-ads configure \
  --cloud-project YOUR_PROJECT_ID \
  --customer-id 1234567890 \
  --login-customer-id 9876543210
```

直接访问时省略 `--login-customer-id`。MCC 层级查询需要单独将 MCC 自身作为 `--customer-id` 加入白名单；子账户仍需分别加入。后续经充分验收要开放写入，可再次 configure 相同账户路径并加 `--allow-write --max-daily-budget 100`。

`max_daily_budget` 是**每个预算资源的平均日预算参数上限**，以账户本币计算。它不是整个账户的合计限额，也不是单日扣费硬上限。修改配置会使既有待提交计划失效。

## 3. 本机授权

```sh
uv run team-ads auth login --client-file /absolute/path/to/downloaded-desktop-client.json
```

浏览器打开 Google 授权，loopback 只监听 `127.0.0.1` 随机端口，最多等待 180 秒。使用 PKCE S256、OAuth state 与离线访问；程序检查 JSON 所属项目及官方端点。OAuth client secret 和 refresh token 写入 OS Keychain，MCP 不提供读凭据、登录或审批接口。项目文件不保存 token。下载的原始 JSON 仍由用户负责安全保管，不加入 Git。

macOS Keychain 是当前主机默认路径；若只能提供不加密的 keyring 后端，程序直接拒绝。授权刷新失败/撤销返回 `AUTH_RECONNECT_REQUIRED`，需要再次登录。重新登录会使旧审批失效；已有不明操作仍可只读核对。

已有 ADC 用户可在 configure 中指定 `--auth-mode adc`。自行用 OAuth 所属项目的客户端生成含 `https://www.googleapis.com/auth/adwords` scope 的 ADC，或使用已获 Ads 访问权的 service account ADC。程序不会替其他应用创建/覆盖 ADC。用户 ADC 可能无法提供可验证的所属项目，`cloud_project` 在此情况下只是声明；需要在 Cloud Console 核对，不能把 quota_project_id 当成 OAuth 所属证明。

本机移除：`uv run team-ads auth logout`。需要撤销 Google 授权时：`uv run team-ads auth logout --revoke`。后者是实际外部权限变更，用户应明确选择执行；网络不明时保留本地记录并提示核对。ADC 必须由其原管理工具撤销。

## 4. MCP 客户端接入

通用 stdio 配置示例（项目未自动安装）：

```json
{
  "mcpServers": {
    "team-ads": {
      "command": "/Users/huitong/Workspace/google-ads-mcp/.venv/bin/team-ads-mcp",
      "env": {
        "TEAM_ADS_STATE_DIR": "/Users/huitong/Workspace/google-ads-mcp/.state"
      }
    }
  }
}
```

务必固定绝对状态路径，避免客户端与审批终端使用不同账本/Keychain namespace。

依次调用 `connection_status` → `discover_accounts` → `preflight` → `account_hierarchy`（适用时）。`preflight` 参数：

```json
{"account":{"customer_id":"1234567890","login_customer_id":"9876543210"}}
```

验收账户名称、币种、时区、是否测试账户，与 Ads UI 对照。直接账户列表不是所有 MCC 子账户列表。select_account 不修改全局隐式选中账户，后续工具仍显式携带 account，以免多聊天串账户。

`preflight` 的读取成功只能证明这条路由的读取可用；不能确定具体 Cloud 访问等级，也不能证明写入权限。没有官方查询能力时保持 unknown，给出 console 入口；实际变更的 validate_only 才能继续验证相关写入约束。

## 5. 首条真实写入验收（另行批准后）

1. 指定明确的**测试 advertiser**，在本机为该路径开放写入。
2. 先 preview 一条否定关键词，检查 before、影响对象、Google validate_only 与到期时间。
3. 用户运行 `uv run team-ads approve OPERATION_ID`，在原生窗口审核，并完成 macOS 本机身份验证（Touch ID / 系统密码）。这是独立于 MCP 的用户操作；没有 `--yes` 或 `approved=true`。
4. MCP 调用 `submit_change`；读取 VERIFIED 或 UNKNOWN，而不是只看 API 响应。
5. 重复同一 operation_id 应返回已有结果；UNKNOWN 只调用 reconcile。
6. 再以 `examples/search-plan.json` 构建 PAUSED Search，逐项核对预算、Campaign、地域语言、广告组、关键词、RSA 和暂停状态。

原生组件源码已编译通过；自动 UI 工具未能绑定该本地应用，真实用户点击/身份验证仍需本机首次验收。当前无 Google 授权，因此没有向 Google 发送真实预检或变更。
