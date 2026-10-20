"""MCP SDK v2 stdio entry point. Credentials are managed outside the protocol."""

from __future__ import annotations

import asyncio
import json
import logging
import re
from datetime import datetime
from typing import Literal
from zoneinfo import ZoneInfo

from mcp import types
from mcp.server.lowlevel import Server
from mcp.server.stdio import stdio_server
from pydantic import Field, ValidationError

from . import __version__
from .config import API_VERSION, load_settings, state_path
from .engine import Engine
from .errors import AdsError, safe_error
from .media import image_payload, public_payload
from .models import (
    Account,
    AssetGroupRef,
    CampaignRef,
    DateRange,
    GroupRef,
    PlanInput,
    PositiveId,
    StrictModel,
)
from .provider import GoogleProvider, literal, project_row, utc_now


class Empty(StrictModel):
    pass


class AccountInput(StrictModel):
    account: Account


class QueryInput(AccountInput):
    query: str = Field(min_length=10, max_length=20000)
    max_rows: int = Field(default=1000, ge=1, le=10000)


class FieldsInput(StrictModel):
    name: str = Field(min_length=1, max_length=100)


class ReportInput(AccountInput):
    dates: DateRange
    report: Literal["campaigns", "search_terms"] = "campaigns"


class KeywordInput(AccountInput):
    seeds: list[str] = Field(min_length=1, max_length=20)
    geo_target_ids: list[PositiveId] = Field(min_length=1, max_length=20)
    language_id: PositiveId


class OperationInput(StrictModel):
    operation_id: str = Field(pattern=r"^[a-f0-9]{32}$")


class ImageInput(StrictModel):
    path: str = Field(min_length=1, max_length=4096)


class ResourcesInput(AccountInput):
    resource: Literal[
        "campaigns",
        "ad_groups",
        "ads",
        "keywords",
        "campaign_criteria",
        "campaign_assets",
        "assets",
        "asset_groups",
        "asset_group_assets",
        "asset_group_signals",
        "audiences",
        "channel_campaigns",
        "channel_ad_groups",
        "channel_ads",
        "ad_group_criteria",
        "conversion_actions",
        "customer_conversion_goals",
        "campaign_conversion_goals",
        "conversion_goal_configs",
        "custom_conversion_goals",
    ]
    campaign: CampaignRef | None = None
    ad_group: GroupRef | None = None
    asset_group: AssetGroupRef | None = None
    asset_type: Literal["TEXT", "IMAGE", "YOUTUBE_VIDEO"] | None = None
    name_contains: str | None = Field(default=None, min_length=1, max_length=100)
    ad_resource: str | None = Field(default=None, pattern=r"^customers/[0-9]{10}/ads/[1-9][0-9]*$")
    include_removed: bool = False
    max_rows: int = Field(default=1000, ge=1, le=10000)


def create_server(engine: Engine):
    provider, settings = engine.provider, engine.settings

    def envelope(data, account=None, dates=None):
        info = provider.account(account) if account else {}
        return {
            "data": data,
            "provenance": {
                "source": provider.source,
                "api_version": API_VERSION,
                "fetched_at": utc_now(),
                "customer_id": account.customer_id if account else None,
                "login_customer_id": account.login_customer_id if account else None,
                "currency": info.get("currency_code"),
                "account_time_zone": info.get("time_zone"),
                "date_range": dates,
                "data_through": None,
                "data_through_note": "Google 未提供统一的数据完整截止时间；转化可能延迟回补",
                "missing_values": "字段缺失或空结果保持缺失，不推断为 0",
            },
        }

    def connection(_):
        project = settings.cloud_project
        return {
            "local_server": "running",
            "server_name": "clico-google-ads",
            "server_version": __version__,
            "api_version": API_VERSION,
            "transport": "stdio",
            "auth_mode": settings.auth_mode,
            "oauth_locally_recorded": settings.auth_mode == "oauth"
            and settings.oauth_authorization_recorded,
            "cloud_project_declared": project or None,
            "live_google_connection": "not_checked",
            "access_level": "unknown_manual_check_required",
            "allowed_accounts": [p.model_dump() for p in settings.accounts],
            "access_console": f"https://console.cloud.google.com/apis/api/googleads.googleapis.com/overview?project={project}",
            "next_action": "用 team-ads configure 配置本机允许账户；auth login 后调用 preflight 验证实际读取",
            "boundaries": [
                "OAuth scope、Cloud 项目访问等级、Ads 账户角色、MCC 路径分别控制不同权限",
                "当前没有实现官方访问等级查询接口；读取成功不能推断具体访问等级或写入权限",
            ],
        }

    def preflight(arg):
        settings.policy(arg.account)
        return envelope(
            {
                "account": provider.account(arg.account),
                "read_access": "verified",
                "write_access": "not_verified",
                "access_level": "unknown",
                "check_console": connection(Empty())["access_console"],
            },
            arg.account,
        )

    def hierarchy(arg):
        settings.policy(arg.account)
        query = (
            "SELECT customer_client.client_customer, customer_client.descriptive_name, "
            "customer_client.level, customer_client.manager, customer_client.currency_code, "
            "customer_client.time_zone, customer_client.status, customer_client.test_account "
            "FROM customer_client ORDER BY customer_client.level"
        )
        return envelope(provider.search(arg.account, query, 10000), arg.account)

    def gaql(arg):
        settings.policy(arg.account)
        query = arg.query.strip()
        if not re.match(r"(?is)^SELECT\s", query) or ";" in query or "--" in query or "/*" in query:
            raise AdsError("QUERY_NOT_ALLOWED", "仅支持单条 SELECT GAQL，不能含注释或分号")
        # Search RPC is read-only even if a malformed query evades this syntax guard.
        dates = {"mode": "query_defined", "query": query}
        return envelope(provider.search(arg.account, query, arg.max_rows), arg.account, dates)

    def report(arg):
        settings.policy(arg.account)
        metadata = provider.account(arg.account)
        today = datetime.now(ZoneInfo(metadata["time_zone"])).date()
        if arg.dates.end > today:
            raise AdsError("FUTURE_DATE_RANGE", "结束日期不能晚于账户时区的今天")
        metrics = "metrics.impressions, metrics.clicks, metrics.cost_micros, metrics.conversions, metrics.conversions_value"
        fields = "campaign.id, campaign.name, campaign.status, segments.date"
        resource = "campaign"
        if arg.report == "search_terms":
            resource = "search_term_view"
            fields += ", ad_group.id, search_term_view.search_term"
        query = f"SELECT {fields}, {metrics} FROM {resource} WHERE segments.date BETWEEN {literal(str(arg.dates.start))} AND {literal(str(arg.dates.end))}"
        selected_fields = f"{fields}, {metrics}".split(", ")
        rows = [
            project_row(row, selected_fields) for row in provider.search(arg.account, query, 10000)
        ]
        return envelope(
            {
                "rows": rows,
                "report": arg.report,
                "limitations": "搜索词存在隐私/报告阈值；零行不代表零花费。转化配置不证明事件回传成功。",
            },
            arg.account,
            arg.dates.model_dump(mode="json"),
        )

    def ideas(arg):
        settings.policy(arg.account)
        if any(not s.strip() or len(s) > 80 for s in arg.seeds):
            raise AdsError("INVALID_SEED", "seed 应为非空且最多 80 字符")
        return envelope(
            provider.keyword_ideas(arg.account, arg.seeds, arg.geo_target_ids, arg.language_id),
            arg.account,
            {"mode": "provider_historical_months"},
        )

    def operation_status(arg):
        plan = engine.ledger.get(arg.operation_id)
        engine.check_integrity(plan)
        return {"plan": plan, "events": engine.ledger.events(arg.operation_id)}

    def resources(arg):
        from .channels import AD_FIELDS, CHANNEL_FIELDS, CHANNEL_GROUP_FIELDS, GROUP_TARGET_FIELDS
        from .conversions import ACTION_FIELDS, CONFIG_FIELDS, CUSTOM_FIELDS
        from .maintenance import (
            ASSET_FIELDS,
            CAMPAIGN_EDIT_FIELDS,
            KEYWORD_FIELDS,
            RSA_FIELDS,
            TARGET_FIELDS,
        )
        from .planner import GROUP_FIELDS
        from .pmax import (
            ASSET_GROUP_FIELDS,
            ASSET_LIBRARY_FIELDS,
            AUDIENCE_FIELDS,
            GROUP_LINK_FIELDS,
            SIGNAL_FIELDS,
        )

        settings.policy(arg.account)
        for name in [arg.campaign, arg.ad_group, arg.ad_resource, arg.asset_group]:
            if name and name.split("/")[1] != arg.account.customer_id:
                raise AdsError("CROSS_ACCOUNT_RESOURCE", "资源属于其他账户")
        resource, fields = {
            "campaigns": ("campaign", CAMPAIGN_EDIT_FIELDS),
            "ad_groups": ("ad_group", [*GROUP_FIELDS, "ad_group.type"]),
            "ads": ("ad_group_ad", RSA_FIELDS),
            "keywords": ("ad_group_criterion", KEYWORD_FIELDS),
            "campaign_criteria": ("campaign_criterion", TARGET_FIELDS),
            "campaign_assets": ("campaign_asset", ASSET_FIELDS),
            "assets": ("asset", ASSET_LIBRARY_FIELDS),
            "asset_groups": ("asset_group", ASSET_GROUP_FIELDS),
            "asset_group_assets": ("asset_group_asset", GROUP_LINK_FIELDS),
            "asset_group_signals": ("asset_group_signal", SIGNAL_FIELDS),
            "audiences": ("audience", AUDIENCE_FIELDS),
            "channel_campaigns": ("campaign", CHANNEL_FIELDS),
            "channel_ad_groups": ("ad_group", CHANNEL_GROUP_FIELDS),
            "channel_ads": ("ad_group_ad", AD_FIELDS),
            "ad_group_criteria": ("ad_group_criterion", GROUP_TARGET_FIELDS),
            "conversion_actions": ("conversion_action", ACTION_FIELDS),
            "customer_conversion_goals": (
                "customer_conversion_goal",
                [
                    "customer_conversion_goal." + f
                    for f in ["resource_name", "category", "origin", "biddable"]
                ],
            ),
            "campaign_conversion_goals": (
                "campaign_conversion_goal",
                [
                    "campaign_conversion_goal." + f
                    for f in ["resource_name", "campaign", "category", "origin", "biddable"]
                ],
            ),
            "conversion_goal_configs": ("conversion_goal_campaign_config", CONFIG_FIELDS),
            "custom_conversion_goals": ("custom_conversion_goal", CUSTOM_FIELDS),
        }[arg.resource]
        conditions = (
            []
            if arg.include_removed
            or resource
            in {
                "asset",
                "asset_group_signal",
                "customer_conversion_goal",
                "campaign_conversion_goal",
                "conversion_goal_campaign_config",
            }
            else [f"{resource}.status != 'REMOVED'"]
        )
        if arg.campaign:
            if arg.resource in {
                "assets",
                "audiences",
                "asset_group_signals",
                "conversion_actions",
                "customer_conversion_goals",
                "custom_conversion_goals",
            }:
                raise AdsError(
                    "INVALID_RESOURCE_FILTER", "此列表不支持 campaign 过滤；信号请使用 asset_group"
                )
            if arg.resource in {"campaign_conversion_goals", "conversion_goal_configs"}:
                conditions.append(f"{resource}.campaign = {literal(arg.campaign)}")
            elif arg.resource in {"asset_groups", "asset_group_assets"}:
                conditions.append(f"asset_group.campaign = {literal(arg.campaign)}")
            else:
                conditions.append(f"campaign.resource_name = {literal(arg.campaign)}")
        if arg.asset_group:
            if arg.resource not in {"asset_groups", "asset_group_assets", "asset_group_signals"}:
                raise AdsError("INVALID_RESOURCE_FILTER", "asset_group 仅用于素材组、关联和信号")
            field = (
                "asset_group.resource_name"
                if resource == "asset_group"
                else resource + ".asset_group"
            )
            conditions.append(f"{field} = {literal(arg.asset_group)}")
        if arg.asset_type:
            if arg.resource != "assets":
                raise AdsError("INVALID_RESOURCE_FILTER", "asset_type 仅用于素材库")
            conditions.append(f"asset.type = {literal(arg.asset_type)}")
        if arg.name_contains:
            if arg.resource not in {
                "assets",
                "asset_groups",
                "audiences",
                "conversion_actions",
                "custom_conversion_goals",
                "channel_campaigns",
                "channel_ad_groups",
            }:
                raise AdsError("INVALID_RESOURCE_FILTER", "名称搜索仅用于素材库、素材组和受众")
            term = arg.name_contains.replace("[", "[[]").replace("%", "[%]").replace("_", "[_]")
            conditions.append(f"{resource}.name LIKE {literal('%' + term + '%')}")
        if arg.ad_group:
            if arg.resource not in {
                "ad_groups",
                "ads",
                "keywords",
                "channel_ad_groups",
                "channel_ads",
                "ad_group_criteria",
            }:
                raise AdsError("INVALID_RESOURCE_FILTER", "ad_group 仅用于广告组、广告、关键词列表")
            conditions.append(f"ad_group.resource_name = {literal(arg.ad_group)}")
        if arg.ad_resource:
            if arg.resource not in {"ads", "channel_ads"}:
                raise AdsError("INVALID_RESOURCE_FILTER", "ad_resource 仅用于查询广告的全部关联")
            conditions.append(f"ad_group_ad.ad.resource_name = {literal(arg.ad_resource)}")
        if arg.resource == "keywords":
            conditions.append("ad_group_criterion.type = 'KEYWORD'")
        query = f"SELECT {', '.join(dict.fromkeys(fields))} FROM {resource}"
        if conditions:
            query += " WHERE " + " AND ".join(conditions)
        return envelope(
            [project_row(row, fields) for row in provider.search(arg.account, query, arg.max_rows)],
            arg.account,
        )

    # schema, description, callable, read_only, idempotent
    registry = {
        "inspect_image_asset": (
            ImageInput,
            "读取指定本机图片，返回 sha256、尺寸、格式及可用 PMax 素材类型；不上传、不裁剪。用于 create_assets 的上传前准备。",
            lambda a: image_payload(a.path)[0],
            True,
            True,
        ),
        "connection_status": (
            Empty,
            "检查本机配置与下一步；不读取或显示凭据，也不声称已连接 Google。",
            connection,
            True,
            True,
        ),
        "discover_accounts": (
            Empty,
            "读取 Google 直接授权账户 ID；MCC 子账户见 account_hierarchy。",
            lambda _: envelope(provider.discover()),
            True,
            True,
        ),
        "select_account": (
            AccountInput,
            "校验明确的账户/MCC 路径并返回币种和时区；后续调用仍需显式携带 account，避免隐式跨账户。",
            preflight,
            True,
            True,
        ),
        "preflight": (
            AccountInput,
            "真实无写入预检：验证账户读取；Cloud API 访问等级、写角色仍需分别确认。",
            preflight,
            True,
            True,
        ),
        "account_hierarchy": (
            AccountInput,
            "查询 MCC 全部可见层级，返回 level、manager、currency 与 time_zone。",
            hierarchy,
            True,
            True,
        ),
        "field_metadata": (
            FieldsInput,
            "查询当前 API 字段可选/可过滤/兼容信息，再构建 GAQL。",
            lambda a: envelope(provider.fields(a.name)),
            True,
            True,
        ),
        "run_gaql": (
            QueryInput,
            "只读 GAQL；最多 10000 行，超限报错而不冒充完整结果。日期口径由 query 决定。",
            gaql,
            True,
            True,
        ),
        "list_resources": (
            ResourcesInput,
            "读取 Search/PMax 资源、素材和受众；Display/Demand Gen/App 使用 channel_campaigns/channel_ad_groups/channel_ads，组定向使用 ad_group_criteria。转化配置使用 conversion_actions/customer_conversion_goals/campaign_conversion_goals/conversion_goal_configs/custom_conversion_goals。默认排除 REMOVED，超限报错。",
            resources,
            True,
            True,
        ),
        "performance_report": (
            ReportInput,
            "明确起止日期的 Campaign/搜索词报表；金额 micros 为账户本币，缺失值不转成零。",
            report,
            True,
            True,
        ),
        "keyword_ideas": (
            KeywordInput,
            "按种子、地域、语言研究关键词；历史指标和预测均不代表真实转化。",
            ideas,
            True,
            True,
        ),
        "preview_change": (
            PlanInput,
            "创建及创建后维护：Search、标准 PMax、Display、Demand Gen 图片/视频、App 安装推广、素材与定位，以及转化动作/账户及系列目标/自定义目标。kind 决定动作；patch 省略保留，列表替换/清空见 schema。读取 before/影响范围并执行 Google validate_only；返回供 submit_change 提交的计划，尚未写入广告。request_key 去重。",
            engine.preview,
            False,
            True,
        ),
        "operation_status": (
            OperationInput,
            "读取变更计划、提交状态和事件账本。",
            operation_status,
            True,
            True,
        ),
        "submit_change": (
            OperationInput,
            "提交 preview_change 返回且仍有效的计划，并回读确认。提交前再次检查权限、远端状态和 Google validate_only；不弹出本机审批或身份验证窗口。超时后先 reconcile，不重复提交。",
            lambda a: engine.submit(a.operation_id),
            False,
            True,
        ),
        "reconcile_operation": (
            OperationInput,
            "只读核对远端结构并更新本地账本；不会重发写操作。没有证据时保持 UNKNOWN。",
            lambda a: engine.reconcile(a.operation_id),
            False,
            True,
        ),
    }

    async def list_tools(ctx, params):
        return types.ListToolsResult(
            tools=[
                types.Tool(
                    name=name,
                    description=desc,
                    input_schema=model.model_json_schema(),
                    annotations=types.ToolAnnotations(
                        read_only_hint=read,
                        destructive_hint=not read,
                        idempotent_hint=idem,
                        open_world_hint=True,
                    ),
                )
                for name, (model, desc, fn, read, idem) in registry.items()
            ]
        )

    async def call_tool(ctx, params):
        nonlocal settings
        try:
            # Existing sessions must observe permission and credential revocation, too.
            if isinstance(provider, GoogleProvider):
                settings = load_settings(engine.path)
                engine.settings = settings
                provider.settings = settings
            if params.name not in registry:
                raise AdsError("UNKNOWN_TOOL", "未知工具")
            model, _, handler, _, _ = registry[params.name]
            arg = model.model_validate(params.arguments or {})

            def invoke():
                if isinstance(provider, GoogleProvider):
                    with provider.request_scope():
                        return handler(arg)
                return handler(arg)

            result = public_payload(await asyncio.to_thread(invoke))
            return types.CallToolResult(
                content=[
                    types.TextContent(type="text", text=json.dumps(result, ensure_ascii=False))
                ],
                structured_content=result,
            )
        except ValidationError as exc:
            # Exclude input values: a mistaken credential pasted into an unknown field must not be echoed.
            data = AdsError(
                "INVALID_INPUT",
                "参数校验失败",
                details=[{"location": e["loc"], "type": e["type"]} for e in exc.errors()],
            ).public()
        except Exception as exc:
            data = safe_error(exc).public()
        return types.CallToolResult(
            content=[
                types.TextContent(type="text", text=json.dumps({"error": data}, ensure_ascii=False))
            ],
            structured_content={"error": data},
            is_error=True,
        )

    return Server(
        "clico-google-ads",
        version=__version__,
        on_list_tools=list_tools,
        on_call_tool=call_tool,
        instructions="先 connection_status / preflight。所有写入必须 preview_change → submit_change → 远端回读，无独立本机审批或逐次身份验证。不要把配置、PAUSED 创建或 ENABLED 当成真实曝光/转化证明。",
    )


async def serve(engine):
    server = create_server(engine)
    async with stdio_server() as (read_stream, write_stream):
        await server.run(read_stream, write_stream, server.create_initialization_options())


def make_engine():
    path = state_path()
    settings = load_settings(path)
    return Engine(path, settings, GoogleProvider(path, settings))


def main():
    # Google's SDK can log full requests on failures; suppress library logs at the process boundary.
    logging.disable(logging.CRITICAL)
    asyncio.run(serve(make_engine()))


if __name__ == "__main__":
    main()
