"""Compile constrained intent into one atomic GoogleAdsService.Mutate request."""

from __future__ import annotations

from copy import deepcopy

from .errors import AdsError
from .models import (
    AddKeywords,
    AddNegatives,
    CreateSearch,
    RemoveKeywords,
    SetBudget,
    SetStatus,
    micros,
)
from .provider import literal, one, query_rows

CAMPAIGN_FIELDS = [
    "campaign.resource_name",
    "campaign.name",
    "campaign.status",
    "campaign.campaign_budget",
    "campaign.advertising_channel_type",
    "campaign.bidding_strategy_type",
    "campaign_budget.amount_micros",
    "campaign_budget.explicitly_shared",
    "campaign_budget.period",
]
GROUP_FIELDS = [
    "ad_group.resource_name",
    "ad_group.name",
    "ad_group.status",
    "ad_group.cpc_bid_micros",
    "ad_group.campaign",
    *(field for field in CAMPAIGN_FIELDS if not field.startswith("campaign_budget.")),
]
BUDGET_FIELDS = [
    "campaign_budget.resource_name",
    "campaign_budget.name",
    "campaign_budget.amount_micros",
    "campaign_budget.explicitly_shared",
    "campaign_budget.reference_count",
    "campaign_budget.period",
    "campaign_budget.status",
]


def operation(resource, verb, body, mask=None):
    result = {verb: body}
    if mask:
        result["update_mask"] = {"paths": mask}
    return {resource + "_operation": result}


def check(resource, condition, fields, expected):
    return {"resource": resource, "condition": condition, "fields": fields, "expected": expected}


def keyword_data(keyword):
    return {"text": keyword.text, "match_type": keyword.match_type}


def not_removed(row, resource):
    if row[resource].get("status") == "REMOVED":
        raise AdsError("RESOURCE_REMOVED", "资源已移除")


def compile_plan(provider, settings, request, plan_id):
    from .maintenance import compile_maintenance

    prepared = compile_maintenance(provider, settings, request, plan_id)
    if prepared is None:
        prepared = compile_initial(provider, settings, request, plan_id)
    if len(prepared["operations"]) > 500:
        raise AdsError("PLAN_TOO_LARGE", "单计划最多 500 项操作，请缩小范围")
    return prepared


def compile_initial(provider, settings, request, plan_id):
    account, change = request.account, request.change
    metadata = provider.account(account)
    if metadata.get("manager"):
        raise AdsError("MANAGER_NOT_ADVERTISER", "MCC 管理账户不能作为广告写入目标，请选择子账户")
    if metadata.get("status") != "ENABLED":
        raise AdsError("ACCOUNT_NOT_ENABLED", "账户未处于 ENABLED 状态")
    if getattr(change, "currency", metadata["currency_code"]) != metadata["currency_code"]:
        raise AdsError("CURRENCY_MISMATCH", "金额币种与账户本币不一致")
    before, operations, checks, warnings = {"account": metadata}, [], [], []

    def get(resource, fields, name):
        row = one(provider, account, resource, fields, name)
        not_removed(row, resource)
        if resource in {"ad_group", "ad_group_ad"}:
            # Budgets cannot be joined from ad_group/ad_group_ad in GAQL.
            parent = one(
                provider, account, "campaign", CAMPAIGN_FIELDS, row["campaign"]["resource_name"]
            )
            not_removed(parent, "campaign")
            row.update(parent)
        return row

    if isinstance(change, SetBudget):
        before["budget"] = get("campaign_budget", BUDGET_FIELDS, change.budget)
        budget = before["budget"]["campaign_budget"]
        if budget.get("period") != "DAILY":
            raise AdsError("BUDGET_PERIOD_UNSUPPORTED", "首版仅支持 DAILY 预算")
        before["campaigns"] = query_rows(
            provider,
            account,
            "campaign",
            CAMPAIGN_FIELDS,
            f"campaign.campaign_budget = {literal(change.budget)} AND campaign.status != 'REMOVED'",
        )
        actual = sorted(r["campaign"]["resource_name"] for r in before["campaigns"])
        if int(budget.get("reference_count", -1)) != len(actual):
            raise AdsError(
                "BUDGET_IMPACT_INCOMPLETE",
                "预算引用数与可见 Campaign 数不一致，无法确认完整影响范围",
            )
        if sorted(change.expected_campaigns) != actual:
            raise AdsError(
                "BUDGET_IMPACT_MISMATCH",
                "需要明确列出全部受影响 Campaign",
                details={"campaigns": actual},
            )
        if budget.get("explicitly_shared") or len(actual) > 1:
            warnings.append("此预算为共享预算；所列全部 Campaign 都受影响")
        amount = micros(change.daily_amount)
        settings.limit(account, amount)
        operations.append(
            operation(
                "campaign_budget",
                "update",
                {"resource_name": change.budget, "amount_micros": amount},
                ["amount_micros"],
            )
        )
        checks.append(
            check(
                "campaign_budget",
                f"campaign_budget.resource_name = {literal(change.budget)}",
                ["campaign_budget.resource_name", "campaign_budget.amount_micros"],
                [{"campaign_budget": {"resource_name": change.budget, "amount_micros": amount}}],
            )
        )
    elif isinstance(change, SetStatus):
        resource = {"campaigns": "campaign", "adGroups": "ad_group", "adGroupAds": "ad_group_ad"}[
            change.resource.split("/")[2]
        ]
        fields = CAMPAIGN_FIELDS if resource == "campaign" else GROUP_FIELDS
        if resource == "ad_group_ad":
            fields = [
                "ad_group_ad.resource_name",
                "ad_group_ad.status",
                "ad_group_ad.ad.id",
                *GROUP_FIELDS,
            ]
        before["resource"] = get(resource, fields, change.resource)
        if change.status == "ENABLED":
            settings.limit(account, int(before["resource"]["campaign_budget"]["amount_micros"]))
            warnings.append("启用可能产生实际支出；ENABLED 不代表审核通过、已曝光或已转化")
        operations.append(
            operation(
                resource,
                "update",
                {"resource_name": change.resource, "status": change.status},
                ["status"],
            )
        )
        checks.append(
            check(
                resource,
                f"{resource}.resource_name = {literal(change.resource)}",
                [resource + ".resource_name", resource + ".status"],
                [{resource: {"resource_name": change.resource, "status": change.status}}],
            )
        )
    elif isinstance(change, (AddKeywords, AddNegatives)):
        negative = isinstance(change, AddNegatives)
        parent, resource = (
            ("campaign", "campaign_criterion") if negative else ("ad_group", "ad_group_criterion")
        )
        name = change.campaign if negative else change.ad_group
        before["parent"] = get(parent, CAMPAIGN_FIELDS if negative else GROUP_FIELDS, name)
        prefix = resource + "."
        fields = [
            prefix + "resource_name",
            prefix + "keyword.text",
            prefix + "keyword.match_type",
            prefix + "negative",
            prefix + "status",
        ]
        condition = f"{prefix}{parent} = {literal(name)} AND {prefix}type = 'KEYWORD' AND {prefix}status != 'REMOVED'"
        before["keywords"] = query_rows(provider, account, resource, fields, condition)
        existing = {
            (
                r[resource]["keyword"]["text"].casefold(),
                r[resource]["keyword"]["match_type"],
                r[resource].get("negative", False),
            )
            for r in before["keywords"]
        }
        for keyword in change.keywords:
            if (keyword.text.casefold(), keyword.match_type, negative) in existing:
                raise AdsError("KEYWORD_EXISTS", "部分关键词已存在，请先读取并缩小变更范围")
            body = {parent: name, "keyword": keyword_data(keyword), "negative": negative}
            if not negative:
                body["status"] = "ENABLED"
            operations.append(operation(resource, "create", body))
        # Read all existing and new keyword content; exclude resource IDs generated by Google.
        expected = [
            {
                resource: {
                    "keyword": r[resource]["keyword"],
                    "negative": r[resource].get("negative", False),
                }
            }
            for r in before["keywords"]
        ]
        expected.extend(
            {resource: {"keyword": keyword_data(k), "negative": negative}} for k in change.keywords
        )
        checks.append(
            check(
                resource,
                condition,
                [prefix + "keyword.text", prefix + "keyword.match_type", prefix + "negative"],
                expected,
            )
        )
    elif isinstance(change, RemoveKeywords):
        before["keywords"] = []
        for name in change.resources:
            resource = (
                "campaign_criterion" if "/campaignCriteria/" in name else "ad_group_criterion"
            )
            prefix = resource + "."
            row = get(
                resource,
                [
                    prefix + x
                    for x in [
                        "resource_name",
                        "type",
                        "negative",
                        "keyword.text",
                        "keyword.match_type",
                        "status",
                    ]
                ],
                name,
            )
            if row[resource].get("type_") != "KEYWORD" and row[resource].get("type") != "KEYWORD":
                raise AdsError("NOT_KEYWORD", "此接口只删除关键词 criterion")
            if resource == "campaign_criterion" and not row[resource].get("negative"):
                raise AdsError("NOT_NEGATIVE_KEYWORD", "Campaign 级删除只支持否定关键词")
            before["keywords"].append(row)
            operations.append(operation(resource, "remove", name))
            checks.append(
                check(
                    resource,
                    f"{prefix}resource_name = {literal(name)} AND {prefix}status != 'REMOVED'",
                    [prefix + "resource_name"],
                    [],
                )
            )
    elif isinstance(change, CreateSearch):
        amount = micros(change.daily_amount)
        settings.limit(account, amount)
        marker = f" [tmcp-{plan_id}]"
        # Google keeps a non-shared budget's name in sync with its campaign.
        campaign_name = budget_name = change.name + marker
        campaign = f"customers/{account.customer_id}/campaigns/-2"
        budget = f"customers/{account.customer_id}/campaignBudgets/-1"
        before["same_marker"] = query_rows(
            provider,
            account,
            "campaign",
            ["campaign.resource_name", "campaign.name"],
            f"campaign.name = {literal(campaign_name)}",
        )
        if before["same_marker"]:
            raise AdsError("CREATION_ALREADY_PRESENT", "远端已存在本计划标记，请 reconcile")
        operations.extend(
            [
                operation(
                    "campaign_budget",
                    "create",
                    {
                        "resource_name": budget,
                        "name": budget_name,
                        "amount_micros": amount,
                        "delivery_method": "STANDARD",
                        "explicitly_shared": False,
                    },
                ),
                operation(
                    "campaign",
                    "create",
                    {
                        "resource_name": campaign,
                        "name": campaign_name,
                        "status": "PAUSED",
                        "advertising_channel_type": "SEARCH",
                        "campaign_budget": budget,
                        "manual_cpc": {},
                        "contains_eu_political_advertising": change.eu_political_advertising,
                        "network_settings": {
                            "target_google_search": True,
                            "target_search_network": False,
                            "target_content_network": False,
                            "target_partner_search_network": False,
                        },
                        "geo_target_type_setting": {
                            "positive_geo_target_type": "PRESENCE",
                            "negative_geo_target_type": "PRESENCE",
                        },
                    },
                ),
            ]
        )
        checks.extend(
            [
                check(
                    "campaign_budget",
                    f"campaign_budget.name = {literal(budget_name)}",
                    [
                        "campaign_budget.name",
                        "campaign_budget.amount_micros",
                        "campaign_budget.explicitly_shared",
                        "campaign_budget.delivery_method",
                    ],
                    [
                        {
                            "campaign_budget": {
                                "name": budget_name,
                                "amount_micros": amount,
                                "explicitly_shared": False,
                                "delivery_method": "STANDARD",
                            }
                        }
                    ],
                ),
                check(
                    "campaign",
                    f"campaign.name = {literal(campaign_name)}",
                    [
                        "campaign.name",
                        "campaign.status",
                        "campaign.advertising_channel_type",
                        "campaign.bidding_strategy_type",
                        "campaign.network_settings.target_google_search",
                        "campaign.network_settings.target_search_network",
                        "campaign.network_settings.target_content_network",
                        "campaign.network_settings.target_partner_search_network",
                        "campaign.geo_target_type_setting.positive_geo_target_type",
                        "campaign.geo_target_type_setting.negative_geo_target_type",
                        "campaign.contains_eu_political_advertising",
                        "campaign_budget.name",
                    ],
                    [
                        {
                            "campaign": {
                                "name": campaign_name,
                                "status": "PAUSED",
                                "advertising_channel_type": "SEARCH",
                                "bidding_strategy_type": "MANUAL_CPC",
                                "network_settings": {
                                    "target_google_search": True,
                                    "target_search_network": False,
                                    "target_content_network": False,
                                    "target_partner_search_network": False,
                                },
                                "geo_target_type_setting": {
                                    "positive_geo_target_type": "PRESENCE",
                                    "negative_geo_target_type": "PRESENCE",
                                },
                                "contains_eu_political_advertising": change.eu_political_advertising,
                            },
                            "campaign_budget": {"name": budget_name},
                        }
                    ],
                ),
            ]
        )
        target_expectations = []
        for field, ids, resource_plural in [
            ("location", change.geo_target_ids, "geoTargetConstants"),
            ("language", change.language_ids, "languageConstants"),
        ]:
            key = "geo_target_constant" if field == "location" else "language_constant"
            for id_ in ids:
                item = {field: {key: f"{resource_plural}/{id_}"}}
                operations.append(
                    operation("campaign_criterion", "create", {"campaign": campaign, **item})
                )
                target_expectations.append({"campaign_criterion": item})
        for keyword in change.negative_keywords:
            item = {"negative": True, "keyword": keyword_data(keyword)}
            operations.append(
                operation("campaign_criterion", "create", {"campaign": campaign, **item})
            )
            target_expectations.append({"campaign_criterion": item})
        condition = f"campaign.name = {literal(campaign_name)}"
        checks.append(
            check(
                "campaign_criterion",
                condition
                + " AND campaign_criterion.status != 'REMOVED'"
                + " AND campaign_criterion.type != 'DEVICE'",
                [
                    "campaign_criterion.location.geo_target_constant",
                    "campaign_criterion.language.language_constant",
                    "campaign_criterion.negative",
                    "campaign_criterion.keyword.text",
                    "campaign_criterion.keyword.match_type",
                ],
                target_expectations,
            )
        )
        groups_expected, ads_expected, keywords_expected = [], [], []
        for index, group in enumerate(change.ad_groups):
            name = group.name + marker
            group_resource = f"customers/{account.customer_id}/adGroups/{-3 - index}"
            bid = micros(group.cpc_bid)
            if bid > amount:
                raise AdsError("CPC_OVER_DAILY_BUDGET", "广告组单次点击出价不能高于计划日预算")
            group_body = {
                "resource_name": group_resource,
                "name": name,
                "campaign": campaign,
                "status": "PAUSED",
                "type_": "SEARCH_STANDARD",
                "cpc_bid_micros": bid,
            }
            operations.append(operation("ad_group", "create", group_body))
            groups_expected.append(
                {
                    "ad_group": {
                        "name": name,
                        "status": "PAUSED",
                        "cpc_bid_micros": bid,
                        "type_": "SEARCH_STANDARD",
                    }
                }
            )
            for keyword in group.keywords:
                operations.append(
                    operation(
                        "ad_group_criterion",
                        "create",
                        {
                            "ad_group": group_resource,
                            "status": "ENABLED",
                            "negative": False,
                            "keyword": keyword_data(keyword),
                        },
                    )
                )
                keywords_expected.append(
                    {
                        "ad_group": {"name": name},
                        "ad_group_criterion": {
                            "status": "ENABLED",
                            "negative": False,
                            "keyword": keyword_data(keyword),
                        },
                    }
                )
            ad = {
                "final_urls": [group.final_url],
                "responsive_search_ad": {
                    "headlines": [{"text": x} for x in group.headlines],
                    "descriptions": [{"text": x} for x in group.descriptions],
                },
            }
            operations.append(
                operation(
                    "ad_group_ad",
                    "create",
                    {"ad_group": group_resource, "status": "PAUSED", "ad": ad},
                )
            )
            ads_expected.append(
                {"ad_group": {"name": name}, "ad_group_ad": {"status": "PAUSED", "ad": ad}}
            )
        checks.extend(
            [
                check(
                    "ad_group",
                    condition + " AND ad_group.status != 'REMOVED'",
                    [
                        "ad_group.name",
                        "ad_group.status",
                        "ad_group.cpc_bid_micros",
                        "ad_group.type",
                    ],
                    groups_expected,
                ),
                check(
                    "ad_group_criterion",
                    condition + " AND ad_group_criterion.status != 'REMOVED'",
                    [
                        "ad_group.name",
                        "ad_group_criterion.status",
                        "ad_group_criterion.negative",
                        "ad_group_criterion.keyword.text",
                        "ad_group_criterion.keyword.match_type",
                    ],
                    keywords_expected,
                ),
                check(
                    "ad_group_ad",
                    condition + " AND ad_group_ad.status != 'REMOVED'",
                    [
                        "ad_group.name",
                        "ad_group_ad.status",
                        "ad_group_ad.ad.final_urls",
                        "ad_group_ad.ad.responsive_search_ad.headlines",
                        "ad_group_ad.ad.responsive_search_ad.descriptions",
                    ],
                    ads_expected,
                ),
            ]
        )
        warnings.append("Campaign、AdGroup 和 RSA 均固定 PAUSED；创建成功不代表政策审核或投放成功")
    if len(operations) > 500:
        raise AdsError("PLAN_TOO_LARGE", "首版单计划最多 500 项操作，请缩小范围")
    if not operations:
        raise AdsError("UNSUPPORTED_CHANGE", "未实现的变更类型")
    return {"before": before, "operations": operations, "checks": checks, "warnings": warnings}


def matches(actual, expected):
    """Subset object comparison, exact array cardinality, protobuf int64 normalization."""
    if isinstance(expected, dict):
        if not isinstance(actual, dict):
            return False
        return all(
            matches(actual.get(k, False if v is False else None), v) for k, v in expected.items()
        )
    if isinstance(expected, list):
        if not isinstance(actual, list) or len(actual) != len(expected):
            return False
        remaining = list(actual)
        for item in expected:
            match = next(
                (i for i, candidate in enumerate(remaining) if matches(candidate, item)), None
            )
            if match is None:
                return False
            remaining.pop(match)
        return True
    if isinstance(expected, bool):
        return actual is expected
    return actual == expected or (isinstance(expected, int) and str(actual) == str(expected))


def reconciliation_checks(plan):
    """Adapt legacy Search readback expectations without changing the stored plan."""
    checks = deepcopy(plan["body"]["prepared"]["checks"])
    corrections = set()
    change = plan["body"]["request"]["change"]
    if change["kind"] != "create_search":
        return checks, []
    marker = f" [tmcp-{plan['id']}]"
    campaign_name, legacy_budget_name = change["name"] + marker, "Budget" + marker
    campaign_condition = f"campaign.name = {literal(campaign_name)}"
    for item in checks:
        if item["resource"] == "campaign_budget" and item["condition"] == (
            f"campaign_budget.name = {literal(legacy_budget_name)}"
        ):
            expected = item["expected"]
            if (
                len(expected) == 1
                and expected[0]["campaign_budget"].get("explicitly_shared") is False
                and expected[0]["campaign_budget"].get("name") == legacy_budget_name
            ):
                item["condition"] = f"campaign_budget.name = {literal(campaign_name)}"
                expected[0]["campaign_budget"]["name"] = campaign_name
                corrections.add("legacy_nonshared_budget_name")
        elif item["resource"] == "campaign" and item["condition"] == campaign_condition:
            for expected in item["expected"]:
                if (
                    expected.get("campaign", {}).get("name") == campaign_name
                    and expected.get("campaign_budget", {}).get("name") == legacy_budget_name
                ):
                    expected["campaign_budget"]["name"] = campaign_name
                    corrections.add("legacy_nonshared_budget_name")
        elif item["resource"] == "campaign_criterion" and item["condition"] == (
            campaign_condition + " AND campaign_criterion.status != 'REMOVED'"
        ):
            # Search creation does not configure device bids. Google adds device rows
            # which are outside the requested location/language/negative-keyword checks.
            item["condition"] += " AND campaign_criterion.type != 'DEVICE'"
            corrections.add("implicit_device_criteria")
    return checks, sorted(corrections)


def verify(provider, account, checks):
    evidence, complete = [], True
    for item in checks:
        rows = query_rows(provider, account, item["resource"], item["fields"], item["condition"])
        compared = deepcopy(rows)
        # Selected optional protobuf scalars may be absent after a field-mask clear.
        # Only explicitly declared clear paths are normalized; all other missing data fails.
        defaults = dict(item.get("default_values", {}))
        for path in item.get("cleared_paths", []):
            keys = path.split(".")
            default = item["expected"][0]
            for key in keys:
                default = default[key]
            defaults[path] = default
        for path, default in defaults.items():
            keys = path.split(".")
            if default not in (0, False, "", []):
                raise AdsError("INVALID_CLEAR_CHECK", "清除字段的期望值必须是默认值")
            for row in compared:
                node = row
                for key in keys[:-1]:
                    node = node.setdefault(key, {})
                node.setdefault(keys[-1], default)
        passed = matches(compared, item["expected"])
        complete = complete and passed
        evidence.append(
            {"resource": item["resource"], "matches_expected": passed, "observed": rows}
        )
    return {
        "complete": complete,
        "evidence": evidence,
        "meaning": (
            "当前远端结构匹配计划；不证明审核、曝光、转化或写入归因"
            if complete
            else "当前远端结构尚未匹配计划；保持锁定并继续核对，不能当作成功"
        ),
    }
