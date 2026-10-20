"""Search maintenance and shared campaign edits, compiled into atomic plans."""

from __future__ import annotations

from copy import deepcopy
from datetime import datetime
from zoneinfo import ZoneInfo

from . import models as m
from .errors import AdsError
from .planner import CAMPAIGN_FIELDS, GROUP_FIELDS, check, matches, not_removed, operation
from .provider import literal, one, query_rows

BIDDING_FIELDS = [
    "campaign.bidding_strategy",
    "campaign.manual_cpc.enhanced_cpc_enabled",
    "campaign.maximize_conversions.target_cpa_micros",
    "campaign.maximize_conversion_value.target_roas",
    "campaign.target_spend.cpc_bid_ceiling_micros",
]
CAMPAIGN_EDIT_FIELDS = [
    *CAMPAIGN_FIELDS,
    *BIDDING_FIELDS,
    "campaign.start_date_time",
    "campaign.end_date_time",
    "campaign.final_url_suffix",
    "campaign.asset_automation_settings",
    "campaign.network_settings.target_google_search",
    "campaign.network_settings.target_search_network",
    "campaign.network_settings.target_content_network",
    "campaign.geo_target_type_setting.positive_geo_target_type",
    "campaign.geo_target_type_setting.negative_geo_target_type",
]
RSA_FIELDS = [
    "ad_group_ad.resource_name",
    "ad_group_ad.ad_group",
    "ad_group_ad.status",
    "ad_group_ad.ad.resource_name",
    "ad_group_ad.ad.type",
    "ad_group_ad.ad.final_urls",
    "ad_group_ad.ad.responsive_search_ad.headlines",
    "ad_group_ad.ad.responsive_search_ad.descriptions",
    "ad_group_ad.ad.responsive_search_ad.path1",
    "ad_group_ad.ad.responsive_search_ad.path2",
]
KEYWORD_FIELDS = [
    "ad_group_criterion.resource_name",
    "ad_group_criterion.ad_group",
    "ad_group_criterion.type",
    "ad_group_criterion.status",
    "ad_group_criterion.negative",
    "ad_group_criterion.keyword.text",
    "ad_group_criterion.keyword.match_type",
    "ad_group_criterion.cpc_bid_micros",
    "ad_group_criterion.final_urls",
    "ad_group_criterion.final_mobile_urls",
    "ad_group_criterion.final_url_suffix",
    "ad_group_criterion.tracking_url_template",
    "ad_group_criterion.url_custom_parameters",
    "ad_group_criterion.labels",
]
TARGET_FIELDS = [
    "campaign_criterion.resource_name",
    "campaign_criterion.campaign",
    "campaign_criterion.type",
    "campaign_criterion.status",
    "campaign_criterion.negative",
    "campaign_criterion.location.geo_target_constant",
    "campaign_criterion.language.language_constant",
    "campaign_criterion.keyword.text",
    "campaign_criterion.keyword.match_type",
    "campaign_criterion.device.type",
    "campaign_criterion.bid_modifier",
    *[
        "campaign_criterion.ad_schedule." + f
        for f in ["day_of_week", "start_hour", "start_minute", "end_hour", "end_minute"]
    ],
]
ASSET_FIELDS = [
    "campaign.resource_name",
    "campaign_asset.resource_name",
    "campaign_asset.campaign",
    "campaign_asset.asset",
    "campaign_asset.field_type",
    "campaign_asset.status",
    "campaign_asset.source",
    "asset.resource_name",
    "asset.name",
    "asset.type",
    "asset.source",
    "asset.final_urls",
    "asset.sitelink_asset.link_text",
    "asset.sitelink_asset.description1",
    "asset.sitelink_asset.description2",
    "asset.callout_asset.callout_text",
    "asset.structured_snippet_asset.header",
    "asset.structured_snippet_asset.values",
]

RSA_DEFAULTS = {
    "ad_group_ad.ad.responsive_search_ad.path1": "",
    "ad_group_ad.ad.responsive_search_ad.path2": "",
}


def set_path(body, path, value):
    keys = path.split(".")
    for key in keys[:-1]:
        body = body.setdefault(key, {})
    body[keys[-1]] = deepcopy(value)


def resource_for(name):
    return {
        "campaigns": "campaign",
        "adGroups": "ad_group",
        "adGroupAds": "ad_group_ad",
        "adGroupCriteria": "ad_group_criterion",
        "campaignCriteria": "campaign_criterion",
    }[name.split("/")[2]]


def rsa_body(content):
    data, ad = content.model_dump(exclude_none=True), {}
    if "final_urls" in data:
        ad["final_urls"] = data.pop("final_urls")
    if data:
        ad["responsive_search_ad"] = {
            key: [{"text": s} for s in value] if key in {"headlines", "descriptions"} else value
            for key, value in data.items()
        }
    return ad


class Context:
    def __init__(self, provider, settings, request, plan_id):
        self.provider, self.settings, self.account = provider, settings, request.account
        self.change, self.plan_id = request.change, plan_id
        self.metadata = provider.account(self.account)
        if self.metadata.get("manager") or self.metadata.get("status") != "ENABLED":
            raise AdsError("ACCOUNT_NOT_ADVERTISER", "需要处于 ENABLED 状态的广告账户")
        currency = getattr(self.change, "currency", self.metadata["currency_code"])
        if currency != self.metadata["currency_code"]:
            raise AdsError("CURRENCY_MISMATCH", "金额必须使用账户币种")
        self.before = {"account": self.metadata}
        self.ops, self.checks, self.warnings, self.summary = [], [], [], []

    def rows(self, resource, fields, condition):
        return query_rows(
            self.provider, self.account, resource, list(dict.fromkeys(fields)), condition
        )

    def load(self, resource, name, fields):
        row = one(self.provider, self.account, resource, list(dict.fromkeys(fields)), name)
        not_removed(row, resource)
        self.before[name] = row
        return row

    def parent(self, name):
        resource = resource_for(name)
        fields = (
            CAMPAIGN_EDIT_FIELDS
            if resource == "campaign"
            else [*GROUP_FIELDS, "ad_group.type", *BIDDING_FIELDS]
        )
        row = self.load(resource, name, fields)
        if resource == "ad_group":
            parent = self.parent(row["ad_group"]["campaign"])
            row.update(campaign=parent["campaign"], campaign_budget=parent["campaign_budget"])
        channel = row["campaign"]["advertising_channel_type"]
        common = (
            self.change.kind
            in {
                "update_campaign",
                "set_bidding",
                "set_targeting",
                "set_schedule",
                "remove_resource",
                "set_campaign_assets",
            }
            and resource == "campaign"
        )
        if channel != "SEARCH" and not (common and channel == "PERFORMANCE_MAX"):
            raise AdsError("SEARCH_REQUIRED", "此维护操作只支持 Search；PMax 使用素材组相关操作")
        not_removed(row, "campaign")
        return row

    def manual_bid(self, row, value):
        campaign = row["campaign"]
        if campaign["bidding_strategy_type"] != "MANUAL_CPC" or campaign.get("bidding_strategy"):
            raise AdsError(
                "MANUAL_CPC_REQUIRED", "只有非组合策略的手动 CPC 系列可设置单独 CPC；先核对出价策略"
            )
        amount = m.micros(value)
        if amount > int(row["campaign_budget"]["amount_micros"]):
            raise AdsError("CPC_OVER_DAILY_BUDGET", "单次点击出价不能高于平均日预算")
        self.settings.limit(self.account, int(row["campaign_budget"]["amount_micros"]))
        return amount

    def update(self, resource, name, old, values, paths, *, expected=None, clear_paths=()):
        body = {"resource_name": name, **values}
        self.ops.append(operation(resource, "update", body, paths))
        desired = deepcopy(old)
        for path in paths:
            if path in clear_paths:
                set_path(desired, path, 0)
            else:
                node = values
                for part in path.split("."):
                    node = node[part]
                set_path(desired, path, node)
        expected = desired if expected is None else expected

        # Query selected snapshot fields only; exclude joined parents from the expected result.
        def leaves(obj, prefix=""):
            for key, value in obj.items():
                path = prefix + ("type" if key == "type_" else key)
                if isinstance(value, dict):
                    yield from leaves(value, path + ".")
                else:
                    yield path

        fields = [resource + "." + path for path in leaves(expected)]
        item = check(
            resource, f"{resource}.resource_name = {literal(name)}", fields, [{resource: expected}]
        )
        explicit_defaults = []
        for path in paths:
            value = expected
            for key in path.split("."):
                value = value[key]
            if value in (0, False, "", []):
                explicit_defaults.append(resource + "." + path)
        if explicit_defaults:
            item["cleared_paths"] = explicit_defaults
        self.checks.append(item)
        self.summary.append({"resource": name, "before": old, "after": expected})

    def finish(self):
        if not self.ops:
            raise AdsError("NO_CHANGES", "目标状态已一致，没有需要提交的变更")
        return {
            "before": self.before,
            "operations": self.ops,
            "checks": self.checks,
            "warnings": self.warnings,
            "changes": self.summary,
        }


def create_ad(c, change):
    c.parent(change.ad_group)
    condition = (
        f"ad_group_ad.ad_group = {literal(change.ad_group)} AND ad_group_ad.status != 'REMOVED'"
    )
    rows = c.rows("ad_group_ad", RSA_FIELDS, condition)
    c.before["existing_ads"] = rows
    ad = rsa_body(change.ad)
    if any(matches(row["ad_group_ad"].get("ad", {}), ad) for row in rows):
        raise AdsError("AD_EXISTS", "相同文案及网址的广告已存在")
    c.ops.append(
        operation(
            "ad_group_ad", "create", {"ad_group": change.ad_group, "status": "PAUSED", "ad": ad}
        )
    )
    new = {"ad_group_ad": {"ad_group": change.ad_group, "status": "PAUSED", "ad": ad}}
    c.checks.append(
        {
            **check("ad_group_ad", condition, RSA_FIELDS, [*rows, new]),
            "default_values": RSA_DEFAULTS,
        }
    )
    c.summary.append({"parent": change.ad_group, "before": None, "after": new})
    c.warnings.append("新广告为 PAUSED；回读核对同组完整广告集合，结果不明时不会重复创建")


def create_group(c, change):
    parent = c.parent(change.campaign)
    name = change.name + f" [tmcp-{c.plan_id}]"
    condition = f"ad_group.name = {literal(name)} AND ad_group.campaign = {literal(change.campaign)} AND ad_group.status != 'REMOVED'"
    c.before["existing_marker"] = c.rows("ad_group", GROUP_FIELDS, condition)
    if c.before["existing_marker"]:
        raise AdsError("CREATION_ALREADY_PRESENT", "已有本计划标记，请核对已有结果")
    resource = f"customers/{c.account.customer_id}/adGroups/-1"
    body = {
        "resource_name": resource,
        "name": name,
        "campaign": change.campaign,
        "status": "PAUSED",
        "type_": "SEARCH_STANDARD",
    }
    if change.cpc_bid is not None:
        body["cpc_bid_micros"] = c.manual_bid(parent, change.cpc_bid)
    elif parent["campaign"]["bidding_strategy_type"] == "MANUAL_CPC":
        raise AdsError("CPC_REQUIRED", "手动 CPC 系列新增广告组需明确 cpc_bid")
    c.ops.append(operation("ad_group", "create", body))
    expected = {k: v for k, v in body.items() if k != "resource_name"}
    c.checks.append(
        check("ad_group", condition, [*GROUP_FIELDS, "ad_group.type"], [{"ad_group": expected}])
    )
    keyword_expected, ad_expected = [], []
    for keyword in change.keywords:
        item = {
            "ad_group": resource,
            "status": "ENABLED",
            "negative": False,
            "keyword": keyword.model_dump(),
        }
        c.ops.append(operation("ad_group_criterion", "create", item))
        keyword_expected.append(
            {"ad_group_criterion": {k: v for k, v in item.items() if k != "ad_group"}}
        )
    for ad in change.ads:
        item = {"ad_group": resource, "status": "PAUSED", "ad": rsa_body(ad)}
        c.ops.append(operation("ad_group_ad", "create", item))
        ad_expected.append({"ad_group_ad": {"status": "PAUSED", "ad": item["ad"]}})
    for kind, fields, expected in [
        ("ad_group_criterion", KEYWORD_FIELDS, keyword_expected),
        ("ad_group_ad", RSA_FIELDS, ad_expected),
    ]:
        item = check(
            kind,
            f"ad_group.name = {literal(name)} AND ad_group.campaign = {literal(change.campaign)} AND {kind}.status != 'REMOVED'",
            fields,
            expected,
        )
        if kind == "ad_group_ad":
            item["default_values"] = RSA_DEFAULTS
        c.checks.append(item)
    c.summary.append({"parent": change.campaign, "before": None, "after": change.model_dump()})
    c.warnings.append("新增广告组和广告均为 PAUSED；原有系列与其他广告组保持原状态")


def update_ad(c, change):
    row = c.load("ad_group_ad", change.ad_group_ad, RSA_FIELDS)
    old = row["ad_group_ad"]
    c.parent(old["ad_group"])
    if old["ad"].get("type_") != "RESPONSIVE_SEARCH_AD":
        raise AdsError("RSA_REQUIRED", "只支持响应式搜索广告的文案与网址修改")
    ad_name = old["ad"]["resource_name"]
    condition = (
        f"ad_group_ad.ad.resource_name = {literal(ad_name)} AND ad_group_ad.status != 'REMOVED'"
    )
    all_rows = c.rows("ad_group_ad", RSA_FIELDS, condition)
    c.before["all_ad_links"] = all_rows
    if sorted(r["ad_group_ad"]["resource_name"] for r in all_rows) != sorted(
        change.expected_ad_group_ads
    ):
        raise AdsError(
            "AD_IMPACT_MISMATCH",
            "必须列出该广告当前全部 ad_group_ad 关联，防止修改共享广告时漏掉影响范围",
        )
    for link in all_rows:
        c.parent(link["ad_group_ad"]["ad_group"])
    patch = rsa_body(change.patch)
    paths = (["final_urls"] if "final_urls" in patch else []) + [
        "responsive_search_ad." + k for k in patch.get("responsive_search_ad", {})
    ]
    updated = deepcopy(old["ad"])
    for path in paths:
        value = patch
        for key in path.split("."):
            value = value[key]
        set_path(updated, path, value)
    if updated["responsive_search_ad"].get("path2") and not updated["responsive_search_ad"].get(
        "path1"
    ):
        raise AdsError("PATH1_REQUIRED", "path2 非空时需要 path1")
    c.ops.append(operation("ad", "update", {"resource_name": ad_name, **patch}, paths))
    expected = deepcopy(all_rows)
    for link in expected:
        link["ad_group_ad"]["ad"] = deepcopy(updated)
    c.checks.append(
        {**check("ad_group_ad", condition, RSA_FIELDS, expected), "default_values": RSA_DEFAULTS}
    )
    c.summary.append(
        {
            "resource": ad_name,
            "affected_links": change.expected_ad_group_ads,
            "before": old["ad"],
            "after": updated,
        }
    )
    c.warnings.append(
        "修改可能触发重新审核；提供的标题/描述列表会整体替换对应列表并取消该列表原有固定位置"
    )


def update_group(c, change):
    row = c.parent(change.ad_group)
    patch = change.patch.model_dump(exclude_none=True)
    if "cpc_bid" in patch:
        patch["cpc_bid_micros"] = c.manual_bid(row, patch.pop("cpc_bid"))
    c.update("ad_group", change.ad_group, row["ad_group"], patch, list(patch))


def load_keyword(c, name):
    resource = resource_for(name)
    fields = KEYWORD_FIELDS if resource == "ad_group_criterion" else TARGET_FIELDS
    row = c.load(resource, name, fields)[resource]
    if row.get("type_") != "KEYWORD":
        raise AdsError("NOT_KEYWORD", "此操作只适用于关键词")
    parent = c.parent(row["ad_group" if resource == "ad_group_criterion" else "campaign"])
    return resource, row, parent


def update_keyword(c, change):
    resource, old, parent = load_keyword(c, change.resource)
    if old.get("negative"):
        raise AdsError("NEGATIVE_KEYWORD_IMMUTABLE", "否定词不支持暂停/出价；请替换或移除")
    values = change.patch.model_dump(exclude_none=True)
    if "cpc_bid" in values:
        values["cpc_bid_micros"] = c.manual_bid(parent, values.pop("cpc_bid"))
    clear = values.pop("clear_cpc_bid", False)
    paths = list(values) + (["cpc_bid_micros"] if clear else [])
    if values.get("status") == "ENABLED":
        c.settings.limit(c.account, int(parent["campaign_budget"]["amount_micros"]))
    c.update(
        resource,
        change.resource,
        old,
        values,
        paths,
        clear_paths=["cpc_bid_micros"] if clear else [],
    )
    c.warnings.append("关键词启用或出价/落地页调整可能立即影响已有投放")


def keyword_collection(c, resource, parent_name):
    parent = "campaign" if resource == "campaign_criterion" else "ad_group"
    fields = TARGET_FIELDS if parent == "campaign" else KEYWORD_FIELDS
    condition = f"{resource}.{parent} = {literal(parent_name)} AND {resource}.type = 'KEYWORD' AND {resource}.status != 'REMOVED'"
    rows = c.rows(resource, fields, condition)
    c.before["keyword_collection"] = rows
    return parent, fields, condition, rows


def add_group_negatives(c, change):
    c.parent(change.ad_group)
    resource = "ad_group_criterion"
    parent, fields, condition, rows = keyword_collection(c, resource, change.ad_group)
    expected = deepcopy(rows)
    for keyword in change.keywords:
        if any(
            r[resource].get("negative")
            and r[resource]["keyword"]["text"].casefold() == keyword.text.casefold()
            and r[resource]["keyword"]["match_type"] == keyword.match_type
            for r in rows
        ):
            raise AdsError("KEYWORD_EXISTS", "否定词已存在")
        body = {parent: change.ad_group, "negative": True, "keyword": keyword.model_dump()}
        c.ops.append(operation(resource, "create", body))
        expected.append({resource: body})
    c.checks.append(check(resource, condition, fields, expected))
    c.summary.append(
        {"parent": change.ad_group, "added_negatives": [k.model_dump() for k in change.keywords]}
    )


def replace_keyword(c, change):
    resource, old, parent = load_keyword(c, change.resource)
    key = "campaign" if resource == "campaign_criterion" else "ad_group"
    _, fields, condition, rows = keyword_collection(c, resource, old[key])
    new_keyword = change.keyword.model_dump()
    if any(
        r[resource]["keyword"]["text"].casefold() == change.keyword.text.casefold()
        and r[resource]["keyword"]["match_type"] == change.keyword.match_type
        and r[resource].get("negative", False) == old.get("negative", False)
        for r in rows
    ):
        raise AdsError("KEYWORD_EXISTS", "目标关键词已存在，包括与原关键词完全相同的情况")
    body = {key: old[key], "negative": old.get("negative", False), "keyword": new_keyword}
    if not body["negative"]:
        for field in [
            "status",
            "cpc_bid_micros",
            "final_urls",
            "final_mobile_urls",
            "final_url_suffix",
            "tracking_url_template",
            "url_custom_parameters",
        ]:
            if old.get(field):
                body[field] = deepcopy(old[field])
        if body.get("status") == "ENABLED":
            c.settings.limit(c.account, int(parent["campaign_budget"]["amount_micros"]))
    c.ops.extend(
        [operation(resource, "remove", change.resource), operation(resource, "create", body)]
    )
    # Carry keyword labels to the newly allocated criterion using a temporary ID.
    if old.get("labels"):
        criterion = f"{old[key].rsplit('/', 1)[1]}~-1"
        body["resource_name"] = f"customers/{c.account.customer_id}/adGroupCriteria/{criterion}"
        c.ops[-1][resource + "_operation"]["create"] = body
        for label in old["labels"]:
            c.ops.append(
                operation(
                    "ad_group_criterion_label",
                    "create",
                    {"ad_group_criterion": body["resource_name"], "label": label},
                )
            )
    expected = [r for r in rows if r[resource]["resource_name"] != change.resource]
    item = {k: v for k, v in body.items() if k != "resource_name"}
    if old.get("labels"):
        item["labels"] = old["labels"]
    expected.append({resource: item})
    c.checks.extend(
        [
            check(resource, condition, fields, expected),
            check(
                resource,
                f"{resource}.resource_name = {literal(change.resource)} AND {resource}.status != 'REMOVED'",
                [resource + ".resource_name"],
                [],
            ),
        ]
    )
    c.summary.append(
        {"resource": change.resource, "before": old, "after": item, "method": "remove_and_create"}
    )
    c.warnings.append(
        "Google 的关键词文字/匹配方式不可原地修改；原子移除并新建，新资源不继承历史统计"
    )


def update_campaign(c, change):
    row = c.parent(change.campaign)
    if row["campaign"]["advertising_channel_type"] == "PERFORMANCE_MAX" and (
        {"search_partners", "display_network"} & change.patch.model_fields_set
    ):
        raise AdsError("PMAX_NETWORK_MANAGED", "PMax 网络由 Google 管理，不能按 Search 开关网络")
    values, paths = {}, []
    mapping = {
        "search_partners": "network_settings.target_search_network",
        "display_network": "network_settings.target_content_network",
        "positive_geo_target_type": "geo_target_type_setting.positive_geo_target_type",
        "negative_geo_target_type": "geo_target_type_setting.negative_geo_target_type",
    }
    for key, value in change.patch.model_dump(exclude_none=True).items():
        if key == "final_url_expansion":
            if row["campaign"]["advertising_channel_type"] != "PERFORMANCE_MAX":
                raise AdsError("PMAX_REQUIRED", "最终网址扩展开关仅用于 PMax")
            automation_type = "FINAL_URL_EXPANSION_TEXT_ASSET_AUTOMATION"
            settings = [
                deepcopy(item)
                for item in row["campaign"].get("asset_automation_settings", [])
                if item.get("asset_automation_type") != automation_type
            ]
            settings.append(
                {
                    "asset_automation_type": automation_type,
                    "asset_automation_status": "OPTED_IN" if value else "OPTED_OUT",
                }
            )
            values["asset_automation_settings"] = settings
            paths.append("asset_automation_settings")
            continue
        path = mapping.get(key, key)
        set_path(values, path, value)
        paths.append(path)
    combined = {**row["campaign"], **{k: v for k, v in values.items() if k.endswith("date_time")}}
    if (
        combined.get("start_date_time")
        and combined.get("end_date_time")
        and combined["start_date_time"] >= combined["end_date_time"]
    ):
        raise AdsError("INVALID_DATE_RANGE", "结束时间必须晚于开始时间（账户时区）")
    if "start_date_time" in values and values["start_date_time"] != row["campaign"].get(
        "start_date_time"
    ):
        now = datetime.now(ZoneInfo(c.metadata["time_zone"])).strftime("%Y-%m-%d %H:%M:%S")
        if row["campaign"].get("start_date_time", "9999") < now or values["start_date_time"] < now:
            raise AdsError(
                "START_ALREADY_PASSED",
                "已开始的系列不能修改开始时间；新的开始时间也不能早于当前账户时间",
            )
    c.update("campaign", change.campaign, row["campaign"], values, paths)
    c.warnings.append("网络、定位与日期修改可能改变流量和支出；日期时间采用账户时区")


def set_bidding(c, change):
    row = c.parent(change.campaign)
    if row["campaign"]["advertising_channel_type"] == "PERFORMANCE_MAX" and change.strategy not in {
        "MAXIMIZE_CONVERSIONS",
        "MAXIMIZE_CONVERSION_VALUE",
    }:
        raise AdsError("PMAX_BIDDING_REQUIRED", "PMax 只支持最大化转化或最大化转化价值")
    c.settings.limit(c.account, int(row["campaign_budget"]["amount_micros"]))
    field, values, paths = {
        "MANUAL_CPC": ("manual_cpc", {"enhanced_cpc_enabled": False}, ["enhanced_cpc_enabled"]),
        "MAXIMIZE_CLICKS": (
            "target_spend",
            {"cpc_bid_ceiling_micros": m.micros(change.max_cpc) if change.max_cpc else 0},
            ["cpc_bid_ceiling_micros"],
        ),
        "MAXIMIZE_CONVERSIONS": (
            "maximize_conversions",
            {"target_cpa_micros": m.micros(change.target_cpa) if change.target_cpa else 0},
            ["target_cpa_micros"],
        ),
        "MAXIMIZE_CONVERSION_VALUE": (
            "maximize_conversion_value",
            {"target_roas": change.target_roas or 0.0},
            ["target_roas"],
        ),
    }[change.strategy]
    if field.startswith("maximize_conversion"):
        for resource in ["customer_conversion_goal", "campaign_conversion_goal"]:
            fields = [resource + ".category", resource + ".origin", resource + ".biddable"]
            condition = (
                f"campaign_conversion_goal.campaign = {literal(change.campaign)}"
                if resource.startswith("campaign")
                else ""
            )
            c.before[resource] = c.rows(resource, fields, condition)
        c.warnings.append(
            "自动出价依赖转化目标和实际回传；预览展示目标配置，不把配置存在当成回传有效"
        )
    body = {
        "resource_name": change.campaign,
        field: {k: v for k, v in values.items() if v or isinstance(v, bool)},
    }
    # Clear any portfolio association and the optional target when it was omitted.
    update_paths = [field + "." + p for p in paths]
    if row["campaign"].get("bidding_strategy"):
        update_paths.insert(0, "bidding_strategy")
        c.warnings.append("本操作将退出当前组合出价策略，只修改这个 Campaign")
    c.ops.append(operation("campaign", "update", body, update_paths))
    expected = {
        "resource_name": change.campaign,
        "bidding_strategy": "",
        "bidding_strategy_type": "TARGET_SPEND"
        if change.strategy == "MAXIMIZE_CLICKS"
        else change.strategy,
        field: values,
    }
    fields = [
        "campaign.resource_name",
        "campaign.bidding_strategy",
        "campaign.bidding_strategy_type",
        *["campaign." + field + "." + p for p in paths],
    ]
    item = check(
        "campaign",
        f"campaign.resource_name = {literal(change.campaign)}",
        fields,
        [{"campaign": expected}],
    )
    item["cleared_paths"] = [
        "campaign.bidding_strategy",
        *["campaign." + field + "." + k for k, v in values.items() if not v],
    ]
    c.checks.append(item)
    c.summary.append({"resource": change.campaign, "before": row["campaign"], "after": expected})


def criterion_collection(c, campaign, criterion_type):
    condition = f"campaign_criterion.campaign = {literal(campaign)} AND campaign_criterion.type = {literal(criterion_type)} AND campaign_criterion.status != 'REMOVED'"
    rows = c.rows("campaign_criterion", TARGET_FIELDS, condition)
    c.before["criteria_" + criterion_type] = rows
    return condition, rows


def replace_criteria(c, campaign, condition, rows, desired, selected=lambda body: True):
    """Retain matching criteria (and their modifiers); replace only the selected dimension."""
    remaining, expected = list(desired), []
    for row in rows:
        body = row["campaign_criterion"]
        if not selected(body):
            expected.append(row)
            continue
        index = next((i for i, target in enumerate(remaining) if matches(body, target)), None)
        if index is None:
            c.ops.append(operation("campaign_criterion", "remove", body["resource_name"]))
        else:
            remaining.pop(index)
            expected.append(row)
    for item in remaining:
        body = {"campaign": campaign, **item}
        c.ops.append(operation("campaign_criterion", "create", body))
        expected.append({"campaign_criterion": body})
    item = check("campaign_criterion", condition, TARGET_FIELDS, expected)
    if "'AD_SCHEDULE'" in condition:
        item["default_values"] = {"campaign_criterion.ad_schedule.start_hour": 0}
    c.checks.append(item)
    c.summary.append({"parent": campaign, "before": rows, "after": expected})


def set_targeting(c, change):
    c.parent(change.campaign)
    data = change.patch.model_dump(exclude_none=True)
    if "geo_target_ids" in data or "excluded_geo_target_ids" in data:
        condition, rows = criterion_collection(c, change.campaign, "LOCATION")
        effective = {}
        for name, negative in [("geo_target_ids", False), ("excluded_geo_target_ids", True)]:
            effective[negative] = data.get(
                name,
                [
                    r["campaign_criterion"]["location"]["geo_target_constant"].split("/")[-1]
                    for r in rows
                    if r["campaign_criterion"].get("negative", False) == negative
                ],
            )
        if set(effective[False]) & set(effective[True]):
            raise AdsError(
                "TARGET_CONFLICT", "同一地区不能同时包含和排除；未指定的另一类定位仍会保留"
            )
        desired = [
            {"negative": negative, "location": {"geo_target_constant": "geoTargetConstants/" + id_}}
            for negative, ids in effective.items()
            for id_ in ids
        ]
        replace_criteria(c, change.campaign, condition, rows, desired)
    if "language_ids" in data:
        condition, rows = criterion_collection(c, change.campaign, "LANGUAGE")
        desired = [
            {"negative": False, "language": {"language_constant": "languageConstants/" + id_}}
            for id_ in data["language_ids"]
        ]
        replace_criteria(c, change.campaign, condition, rows, desired)
    c.warnings.append(
        "只替换已提供的定位维度；省略的维度保留。空列表会清除该维度限制，可能扩大投放范围。新 criterion 使用默认出价系数。"
    )


def set_schedule(c, change):
    c.parent(change.campaign)
    condition, rows = criterion_collection(c, change.campaign, "AD_SCHEDULE")
    minutes = {"00": "ZERO", "15": "FIFTEEN", "30": "THIRTY", "45": "FORTY_FIVE"}
    desired = []
    for slot in change.schedules:
        start_h, start_m = slot.start.split(":")
        end_h, end_m = slot.end.split(":")
        desired.append(
            {
                "negative": False,
                "ad_schedule": {
                    "day_of_week": slot.day,
                    "start_hour": int(start_h),
                    "start_minute": minutes[start_m],
                    "end_hour": int(end_h),
                    "end_minute": minutes[end_m],
                },
            }
        )
    replace_criteria(c, change.campaign, condition, rows, desired)
    c.warnings.append(
        "按账户时区替换整周时段；空列表恢复全天投放。保留的时段保留系数，新时段采用默认系数。"
    )


def set_device_bid(c, change):
    row = c.load("campaign_criterion", change.resource, TARGET_FIELDS)
    old = row["campaign_criterion"]
    if old.get("type_") != "DEVICE":
        raise AdsError("DEVICE_REQUIRED", "仅接受现有 DEVICE criterion")
    parent = c.parent(old["campaign"])
    if change.bid_modifier != 0 and (
        parent["campaign"]["bidding_strategy_type"] != "MANUAL_CPC"
        or parent["campaign"].get("bidding_strategy")
    ):
        raise AdsError(
            "DEVICE_BID_STRATEGY_UNSUPPORTED",
            "自动/组合出价对设备系数有策略相关限制；本版本仅支持系数 0 排除设备，或在手动 CPC 下调整系数",
        )
    c.update(
        "campaign_criterion",
        change.resource,
        old,
        {"bid_modifier": change.bid_modifier},
        ["bid_modifier"],
    )
    c.warnings.append("设备系数 0 表示排除此设备；非零调整可能改变有效点击出价")


def remove_resource(c, change):
    resource = resource_for(change.resource)
    if resource == "ad_group_ad":
        row = c.load(resource, change.resource, RSA_FIELDS)
        c.parent(row[resource]["ad_group"])
    else:
        row = c.parent(change.resource)
        children = [
            ("ad_group", GROUP_FIELDS),
            ("ad_group_ad", RSA_FIELDS),
            ("ad_group_criterion", KEYWORD_FIELDS),
        ]
        if row["campaign"]["advertising_channel_type"] == "PERFORMANCE_MAX":
            from .pmax import ASSET_GROUP_FIELDS

            children = [("asset_group", ASSET_GROUP_FIELDS)]
        for child, fields in children:
            if resource == "ad_group" and child == "ad_group":
                continue
            condition = f"{resource}.resource_name = {literal(change.resource)} AND {child}.status != 'REMOVED'"
            c.before["affected_" + child] = c.rows(child, fields, condition)
    c.ops.append(operation(resource, "remove", change.resource))
    c.checks.append(
        check(
            resource,
            f"{resource}.resource_name = {literal(change.resource)} AND {resource}.status != 'REMOVED'",
            [resource + ".resource_name"],
            [],
        )
    )
    c.summary.append({"resource": change.resource, "before": row[resource], "after": "REMOVED"})
    c.warnings.append(
        "移除后不能重新启用；父资源被移除后其下广告无法投放。若只是暂时停用，请改用 set_status PAUSED。移除系列不自动移除预算。"
    )


def asset_body(asset):
    data = asset.model_dump()
    kind = data.pop("type")
    if kind == "SITELINK":
        return {"final_urls": data.pop("final_urls"), "sitelink_asset": data}
    if kind == "CALLOUT":
        return {"callout_asset": {"callout_text": data["text"]}}
    return {"structured_snippet_asset": data}


def set_campaign_assets(c, change):
    c.parent(change.campaign)
    condition = f"campaign_asset.campaign = {literal(change.campaign)} AND campaign_asset.field_type = {literal(change.field_type)} AND campaign_asset.status != 'REMOVED' AND campaign_asset.source = 'ADVERTISER'"
    rows = c.rows("campaign_asset", ASSET_FIELDS, condition)
    c.before["campaign_assets"] = rows
    remaining = [asset_body(a) for a in change.assets]
    expected = []
    for row in rows:
        index = next((i for i, body in enumerate(remaining) if matches(row["asset"], body)), None)
        if index is None:
            c.ops.append(
                operation("campaign_asset", "remove", row["campaign_asset"]["resource_name"])
            )
        else:
            remaining.pop(index)
            expected.append(row)
    for index, body in enumerate(remaining):
        temp = f"customers/{c.account.customer_id}/assets/{-1 - index}"
        c.ops.append(operation("asset", "create", {"resource_name": temp, **body}))
        link = {
            "campaign": change.campaign,
            "asset": temp,
            "field_type": change.field_type,
            "status": "ENABLED",
        }
        c.ops.append(operation("campaign_asset", "create", link))
        expected.append(
            {
                "campaign_asset": {
                    "campaign": change.campaign,
                    "field_type": change.field_type,
                    "status": "ENABLED",
                    "source": "ADVERTISER",
                },
                "asset": body,
            }
        )
    c.checks.append(check("campaign_asset", condition, ASSET_FIELDS, expected))
    c.summary.append(
        {
            "parent": change.campaign,
            "field_type": change.field_type,
            "before": rows,
            "after": expected,
        }
    )
    c.warnings.append(
        "替换本 Campaign 此类型的人工添加素材关联；相同内容保留原关联及状态，变更内容通过新素材重新关联。旧素材仍留在素材库，其他系列、账户级/广告组级关联及自动素材不变。空列表移除本级此类型全部人工关联，不保证页面上不再显示继承或自动素材。"
    )


def compile_maintenance(provider, settings, request, plan_id):
    handlers = {
        "create_ad_group": create_group,
        "update_ad_group": update_group,
        "create_ad": create_ad,
        "update_ad": update_ad,
        "update_keyword": update_keyword,
        "replace_keyword": replace_keyword,
        "add_ad_group_negatives": add_group_negatives,
        "update_campaign": update_campaign,
        "set_bidding": set_bidding,
        "set_targeting": set_targeting,
        "set_schedule": set_schedule,
        "set_device_bid": set_device_bid,
        "remove_resource": remove_resource,
        "set_campaign_assets": set_campaign_assets,
    }
    handler = handlers.get(request.change.kind)
    if handler is None:
        return None
    c = Context(provider, settings, request, plan_id)
    handler(c, request.change)
    return c.finish()
