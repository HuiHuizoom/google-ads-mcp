"""Standard Performance Max and reusable assets, using the same atomic write engine."""

from __future__ import annotations

import base64
from collections import Counter
from copy import deepcopy

from . import models as m
from .errors import AdsError
from .maintenance import CAMPAIGN_EDIT_FIELDS, Context
from .media import MAX_IMAGE_BYTES, compatible_image_fields, image_payload
from .planner import BUDGET_FIELDS, check, operation
from .provider import literal

ASSET_LIBRARY_FIELDS = [
    "asset.resource_name",
    "asset.id",
    "asset.name",
    "asset.type",
    "asset.source",
    "asset.text_asset.text",
    "asset.youtube_video_asset.youtube_video_id",
    "asset.youtube_video_asset.youtube_video_title",
    "asset.image_asset.file_size",
    "asset.image_asset.mime_type",
    "asset.image_asset.full_size.width_pixels",
    "asset.image_asset.full_size.height_pixels",
    "asset.image_asset.full_size.url",
]
PMAX_CAMPAIGN_FIELDS = [
    *CAMPAIGN_EDIT_FIELDS,
    "campaign.brand_guidelines_enabled",
    "campaign.advertising_channel_sub_type",
    "campaign.shopping_setting.merchant_id",
]
ASSET_GROUP_FIELDS = [
    "asset_group.resource_name",
    "asset_group.id",
    "asset_group.campaign",
    "asset_group.name",
    "asset_group.status",
    "asset_group.final_urls",
    "asset_group.path1",
    "asset_group.path2",
]
GROUP_LINK_FIELDS = [
    "asset_group_asset.resource_name",
    "asset_group_asset.asset_group",
    "asset_group_asset.asset",
    "asset_group_asset.field_type",
    "asset_group_asset.status",
    "asset_group_asset.source",
    "asset_group.name",
    "asset_group.campaign",
]
BRAND_LINK_FIELDS = [
    "campaign_asset.resource_name",
    "campaign_asset.campaign",
    "campaign_asset.asset",
    "campaign_asset.field_type",
    "campaign_asset.status",
    "campaign_asset.source",
]
SIGNAL_FIELDS = [
    "asset_group_signal.resource_name",
    "asset_group_signal.asset_group",
    "asset_group_signal.audience.audience",
    "asset_group_signal.search_theme.text",
    "asset_group_signal.approval_status",
    "asset_group_signal.disapproval_reasons",
]
AUDIENCE_FIELDS = [
    "audience.resource_name",
    "audience.id",
    "audience.name",
    "audience.status",
    "audience.description",
    "audience.scope",
    "audience.asset_group",
]
BRAND_TYPES = {"BUSINESS_NAME", "LOGO", "LANDSCAPE_LOGO"}
COUNTS = {
    "HEADLINE": (3, 15),
    "LONG_HEADLINE": (1, 5),
    "DESCRIPTION": (2, 5),
    "MARKETING_IMAGE": (1, 20),
    "SQUARE_MARKETING_IMAGE": (1, 20),
    "PORTRAIT_MARKETING_IMAGE": (0, 20),
    "YOUTUBE_VIDEO": (0, 15),
    "BUSINESS_NAME": (1, 1),
    "LOGO": (1, 5),
    "LANDSCAPE_LOGO": (0, 20),
}
TEXT_LIMITS = {"HEADLINE": 30, "LONG_HEADLINE": 90, "DESCRIPTION": 90, "BUSINESS_NAME": 25}


def campaign(c, name):
    row = c.load("campaign", name, PMAX_CAMPAIGN_FIELDS)
    data = row["campaign"]
    if data["advertising_channel_type"] != "PERFORMANCE_MAX":
        raise AdsError("PMAX_REQUIRED", "此操作需要 Performance Max 系列")
    if data.get("advertising_channel_sub_type") not in (None, "UNSPECIFIED", "UNKNOWN"):
        raise AdsError("STANDARD_PMAX_REQUIRED", "本接口只维护标准 PMax，不管理旅游或本地服务变体")
    if int(data.get("shopping_setting", {}).get("merchant_id", 0)):
        raise AdsError(
            "RETAIL_PMAX_UNSUPPORTED", "商品源及商品组管理尚未接入；本接口只维护标准非零售 PMax"
        )
    return row


def group(c, name):
    row = c.load("asset_group", name, ASSET_GROUP_FIELDS)
    parent = campaign(c, row["asset_group"]["campaign"])
    return row, parent


def asset(c, name):
    if name not in c.before:
        c.load("asset", name, ASSET_LIBRARY_FIELDS)
    return c.before[name]["asset"]


def validate_links(c, links, *, brand=False, guidelines=True):
    keys = [(a.field_type, a.asset) for a in links]
    if len(set(keys)) != len(keys):
        raise AdsError("DUPLICATE_ASSET_LINK", "同一类型不能重复关联同一素材")
    counts = Counter(field for field, _ in keys)
    if brand:
        if set(counts) - BRAND_TYPES:
            raise AdsError("INVALID_BRAND_ASSET", "品牌关联只接受商家名和 Logo")
        if (
            counts["BUSINESS_NAME"] != 1
            or counts["LOGO"] < 1
            or counts["LOGO"] + counts["LANDSCAPE_LOGO"] > 5
        ):
            raise AdsError(
                "BRAND_ASSETS_REQUIRED", "必须一个商家名、至少一个正方形 Logo，Logo 总数至多五个"
            )
    else:
        if guidelines and set(counts) & BRAND_TYPES:
            raise AdsError(
                "CAMPAIGN_BRAND_ASSETS_REQUIRED", "启用品牌指南时，商家名和 Logo 应关联到系列"
            )
        required = set(COUNTS) - (BRAND_TYPES if guidelines else set())
        for field in required:
            minimum, maximum = COUNTS[field]
            if not minimum <= counts[field] <= maximum:
                raise AdsError("ASSET_COUNT_INVALID", f"{field} 需要 {minimum}–{maximum} 个素材")
    for link in links:
        data = asset(c, link.asset)
        field = link.field_type
        expected_type = (
            "TEXT"
            if field in TEXT_LIMITS
            else ("YOUTUBE_VIDEO" if field == "YOUTUBE_VIDEO" else "IMAGE")
        )
        if data["type_"] != expected_type:
            raise AdsError("ASSET_TYPE_MISMATCH", f"{field} 必须使用 {expected_type} 素材")
        if expected_type == "TEXT":
            try:
                m.validate_text(data["text_asset"]["text"], TEXT_LIMITS[field])
            except ValueError:
                raise AdsError("ASSET_TEXT_INVALID", f"{field} 的文字超过长度限制或无效") from None
        elif expected_type == "IMAGE":
            image = data.get("image_asset", {})
            size = image.get("full_size", {})
            width, height = int(size.get("width_pixels", 0)), int(size.get("height_pixels", 0))
            if (
                not height
                or int(image.get("file_size", 0)) > MAX_IMAGE_BYTES
                or field not in compatible_image_fields(width, height)
            ):
                raise AdsError(
                    "ASSET_DIMENSIONS_INVALID", f"{field} 的尺寸、比例或文件大小不符合要求"
                )


def create_assets(c, change):
    total = 0
    for index, item in enumerate(change.assets):
        if item.type == "TEXT":
            body = {"text_asset": {"text": item.text}}
            expected = {"type_": "TEXT", **body}
        elif item.type == "YOUTUBE_VIDEO":
            body = {
                "name": item.name,
                "youtube_video_asset": {"youtube_video_id": item.youtube_video_id},
            }
            expected = {
                "type_": "YOUTUBE_VIDEO",
                "youtube_video_asset": body["youtube_video_asset"],
            }
        else:
            info, data = image_payload(item.path, item.sha256)
            c.before[f"image_{index}"] = info
            total += len(data)
            if total > 10_240_000:
                raise AdsError(
                    "UPLOAD_BATCH_TOO_LARGE", "单计划图片总量最多 10,240,000 字节，请分批"
                )
            body = {"name": item.name, "image_asset": {"data": base64.b64encode(data).decode()}}
            expected = {
                "type_": "IMAGE",
                "image_asset": {
                    "full_size": {"width_pixels": info["width"], "height_pixels": info["height"]}
                },
            }
        c.ops.append(operation("asset", "create", body))
        # Google can deduplicate assets and discard the new name. Bind to receipt,
        # never assume that a name or image dimensions uniquely identify an upload.
        item_check = check("asset", "", ASSET_LIBRARY_FIELDS, [{"asset": expected}])
        item_check["receipt_operation_index"] = index
        c.checks.append(item_check)
        c.summary.append({"asset_index": index, "type": item.type, "after": expected})
    c.warnings.append(
        "仅创建/复用素材库对象，不关联投放。图片内容不原地修改。Google 可能去重并返回旧 ID；无回执时不凭名称或尺寸认定上传成功。"
    )


def group_links(c, name):
    condition = (
        f"asset_group_asset.asset_group = {literal(name)} AND asset_group_asset.status != 'REMOVED'"
    )
    rows = c.rows("asset_group_asset", GROUP_LINK_FIELDS, condition)
    c.before["asset_group_assets"] = rows
    return condition, rows


def create_group_body(c, content, parent_name, guidelines, temp_index):
    validate_links(c, content.assets, guidelines=guidelines)
    name = content.name + f" [tmcp-{c.plan_id}]"
    condition = f"asset_group.name = {literal(name)} AND asset_group.campaign = {literal(parent_name)} AND asset_group.status != 'REMOVED'"
    # For a new campaign the temporary parent ID is resolved through campaign name
    # by the caller, below.
    ref = f"customers/{c.account.customer_id}/assetGroups/{temp_index}"
    body = {
        "resource_name": ref,
        "campaign": parent_name,
        "name": name,
        "status": "PAUSED",
        "final_urls": content.final_urls,
    }
    c.ops.append(operation("asset_group", "create", body))
    for link in content.assets:
        c.ops.append(
            operation(
                "asset_group_asset",
                "create",
                {
                    "asset_group": ref,
                    "asset": link.asset,
                    "field_type": link.field_type,
                    "status": "ENABLED",
                },
            )
        )
    expected = {"asset_group": {"name": name, "status": "PAUSED", "final_urls": content.final_urls}}
    c.checks.append(check("asset_group", condition, ASSET_GROUP_FIELDS, [expected]))
    c.checks.append(
        check(
            "asset_group_asset",
            condition + " AND asset_group_asset.status != 'REMOVED'",
            GROUP_LINK_FIELDS,
            [
                {
                    "asset_group_asset": {
                        "asset": a.asset,
                        "field_type": a.field_type,
                        "status": "ENABLED",
                    }
                }
                for a in content.assets
            ],
        )
    )
    c.summary.append({"parent": parent_name, "after": content.model_dump(), "status": "PAUSED"})


def create_group(c, change):
    parent = campaign(c, change.campaign)["campaign"]
    marker = change.group.name + f" [tmcp-{c.plan_id}]"
    existing = c.rows(
        "asset_group",
        ASSET_GROUP_FIELDS,
        f"asset_group.name = {literal(marker)} AND asset_group.campaign = {literal(change.campaign)} AND asset_group.status != 'REMOVED'",
    )
    c.before["existing_marker"] = existing
    if existing:
        raise AdsError("CREATION_ALREADY_PRESENT", "已有本计划素材组标记，请核对结果")
    create_group_body(
        c, change.group, change.campaign, parent.get("brand_guidelines_enabled", False), -1
    )


def create_pmax(c, change):
    amount = m.micros(change.daily_amount)
    c.settings.limit(c.account, amount)
    validate_links(c, change.brand_assets, brand=True)
    root = f"customers/{c.account.customer_id}"
    name = change.name + f" [tmcp-{c.plan_id}]"
    condition = f"campaign.name = {literal(name)}"
    c.before["existing_marker"] = c.rows("campaign", PMAX_CAMPAIGN_FIELDS, condition)
    if c.before["existing_marker"]:
        raise AdsError("CREATION_ALREADY_PRESENT", "已有本计划系列标记，请核对结果")
    budget_ref, campaign_ref = root + "/campaignBudgets/-1", root + "/campaigns/-2"
    budget = {
        "resource_name": budget_ref,
        "name": name,
        "amount_micros": amount,
        "delivery_method": "STANDARD",
        "explicitly_shared": False,
    }
    strategy_field = (
        "maximize_conversions"
        if change.bidding == "MAXIMIZE_CONVERSIONS"
        else "maximize_conversion_value"
    )
    strategy = {}
    if change.target_cpa:
        strategy["target_cpa_micros"] = m.micros(change.target_cpa)
    if change.target_roas:
        strategy["target_roas"] = change.target_roas
    body = {
        "resource_name": campaign_ref,
        "name": name,
        "campaign_budget": budget_ref,
        "status": "PAUSED",
        "advertising_channel_type": "PERFORMANCE_MAX",
        "brand_guidelines_enabled": True,
        strategy_field: strategy,
        "contains_eu_political_advertising": change.eu_political_advertising,
        "asset_automation_settings": [
            {
                "asset_automation_type": "FINAL_URL_EXPANSION_TEXT_ASSET_AUTOMATION",
                "asset_automation_status": "OPTED_OUT",
            }
        ],
        "geo_target_type_setting": {"positive_geo_target_type": "PRESENCE"},
    }
    c.ops.extend(
        [operation("campaign_budget", "create", budget), operation("campaign", "create", body)]
    )
    for link in change.brand_assets:
        c.ops.append(
            operation(
                "campaign_asset",
                "create",
                {
                    "campaign": campaign_ref,
                    "asset": link.asset,
                    "field_type": link.field_type,
                    "status": "ENABLED",
                },
            )
        )
    c.checks.extend(
        [
            check(
                "campaign_budget",
                f"campaign_budget.name = {literal(name)}",
                BUDGET_FIELDS,
                [
                    {
                        "campaign_budget": {
                            "name": name,
                            "amount_micros": amount,
                            "explicitly_shared": False,
                        }
                    }
                ],
            ),
            check(
                "campaign",
                condition,
                [
                    *PMAX_CAMPAIGN_FIELDS,
                    "campaign.contains_eu_political_advertising",
                    "campaign_budget.name",
                ],
                [
                    {
                        "campaign": {
                            k: v
                            for k, v in body.items()
                            if k not in {"resource_name", "campaign_budget", strategy_field}
                        }
                        | {"bidding_strategy_type": change.bidding}
                        | ({strategy_field: strategy} if strategy else {}),
                        "campaign_budget": {
                            "name": name,
                            "amount_micros": amount,
                            "explicitly_shared": False,
                        },
                    }
                ],
            ),
            check(
                "campaign_asset",
                condition + " AND campaign_asset.status != 'REMOVED'",
                [*BRAND_LINK_FIELDS, "campaign.name"],
                [
                    {
                        "campaign_asset": {
                            "asset": a.asset,
                            "field_type": a.field_type,
                            "status": "ENABLED",
                        }
                    }
                    for a in change.brand_assets
                ],
            ),
        ]
    )
    c.checks[-2]["selected_automation_types"] = ["FINAL_URL_EXPANSION_TEXT_ASSET_AUTOMATION"]
    target_expected = []
    for field, ids, prefix in [
        ("location", change.geo_target_ids, "geoTargetConstants"),
        ("language", change.language_ids, "languageConstants"),
    ]:
        key = "geo_target_constant" if field == "location" else "language_constant"
        for value in ids:
            criterion = {
                "campaign": campaign_ref,
                "negative": False,
                field: {key: f"{prefix}/{value}"},
            }
            c.ops.append(operation("campaign_criterion", "create", criterion))
            target_expected.append(
                {"campaign_criterion": {"negative": False, field: criterion[field]}}
            )
    target_fields = [
        "campaign.name",
        "campaign_criterion.negative",
        "campaign_criterion.location.geo_target_constant",
        "campaign_criterion.language.language_constant",
    ]
    c.checks.append(
        check(
            "campaign_criterion",
            condition
            + " AND campaign_criterion.status != 'REMOVED' AND campaign_criterion.type IN ('LOCATION', 'LANGUAGE')",
            target_fields,
            target_expected,
        )
    )
    for index, content in enumerate(change.asset_groups):
        start = len(c.checks)
        create_group_body(c, content, campaign_ref, True, -3 - index)
        # AssetGroup resource exposes campaign as an attribute; child association
        # reads are scoped by the unique group marker, which is account-global here.
        for item in c.checks[start:]:
            item["condition"] = item["condition"].replace(
                f" AND asset_group.campaign = {literal(campaign_ref)}", ""
            )
    c.warnings.append(
        "新系列和素材组均 PAUSED；采用账户默认转化目标，关闭最终网址扩展。未提供视频时 Google 可能自动生成视频。结构创建成功不代表审核、投放或转化。"
    )


def update_group(c, change):
    old, _ = group(c, change.asset_group)
    values = change.patch.model_dump(exclude_none=True)
    desired = {**old["asset_group"], **values}
    if desired.get("path2") and not desired.get("path1"):
        raise AdsError("PATH1_REQUIRED", "path2 非空时需要 path1")
    c.update("asset_group", change.asset_group, old["asset_group"], values, list(values))


def replace_links(c, resource, parent_key, parent, fields, condition, rows, desired, managed_types):
    desired_keys = {(a.field_type, a.asset) for a in desired}
    if len(desired_keys) != len(desired):
        raise AdsError("DUPLICATE_ASSET_LINK", "素材关联不可重复")
    expected, retained = [], set()
    for row in rows:
        data = row[resource]
        key = (data["field_type"], data["asset"])
        managed = data["field_type"] in managed_types and data.get("source") == "ADVERTISER"
        if managed and key not in desired_keys:
            c.ops.append(operation(resource, "remove", data["resource_name"]))
        else:
            expected.append(row)
            if key in desired_keys:
                retained.add(key)
    for field, name in sorted(desired_keys - retained):
        body = {parent_key: parent, "field_type": field, "asset": name, "status": "ENABLED"}
        c.ops.append(operation(resource, "create", body))
        expected.append({resource: body})
    c.checks.append(check(resource, condition, fields, expected))
    c.summary.append({"parent": parent, "before": rows, "after": expected})
    c.warnings.append(
        "只变更指定类型的人工素材关联；自动素材和未指定类型保留，不修改共享素材内容或其他系列的关联。"
    )


def set_group_assets(c, change):
    _, parent = group(c, change.asset_group)
    condition, rows = group_links(c, change.asset_group)
    desired = [
        m.AssetLink(asset=ref, field_type=field)
        for field, refs in change.assets_by_field.items()
        for ref in refs
    ]
    managed = set(change.assets_by_field)
    kept = [
        m.AssetLink(
            asset=r["asset_group_asset"]["asset"], field_type=r["asset_group_asset"]["field_type"]
        )
        for r in rows
        if r["asset_group_asset"]["field_type"] in COUNTS
        and (
            r["asset_group_asset"]["field_type"] not in managed
            or r["asset_group_asset"].get("source") != "ADVERTISER"
        )
    ]
    effective = {(a.field_type, a.asset): a for a in [*kept, *desired]}
    validate_links(
        c,
        list(effective.values()),
        guidelines=parent["campaign"].get("brand_guidelines_enabled", False),
    )
    replace_links(
        c,
        "asset_group_asset",
        "asset_group",
        change.asset_group,
        GROUP_LINK_FIELDS,
        condition,
        rows,
        desired,
        managed,
    )


def set_brand_assets(c, change):
    parent = campaign(c, change.campaign)["campaign"]
    if not parent.get("brand_guidelines_enabled"):
        raise AdsError("BRAND_GUIDELINES_REQUIRED", "此系列未启用品牌指南，请在素材组维护品牌素材")
    validate_links(c, change.assets, brand=True)
    condition = f"campaign_asset.campaign = {literal(change.campaign)} AND campaign_asset.status != 'REMOVED'"
    rows = c.rows("campaign_asset", BRAND_LINK_FIELDS, condition)
    c.before["campaign_assets"] = rows
    replace_links(
        c,
        "campaign_asset",
        "campaign",
        change.campaign,
        BRAND_LINK_FIELDS,
        condition,
        rows,
        change.assets,
        BRAND_TYPES,
    )


def set_signals(c, change):
    group(c, change.asset_group)
    condition = f"asset_group_signal.asset_group = {literal(change.asset_group)}"
    rows = c.rows("asset_group_signal", SIGNAL_FIELDS, condition)
    c.before["signals"] = rows
    desired = {}
    if change.audiences is not None:
        for ref in change.audiences:
            audience = c.load("audience", ref, AUDIENCE_FIELDS)["audience"]
            if audience.get("status") != "ENABLED":
                raise AdsError("AUDIENCE_UNAVAILABLE", "受众不是 ENABLED 状态")
            if (
                audience.get("scope") == "ASSET_GROUP"
                and audience.get("asset_group") != change.asset_group
            ):
                raise AdsError("AUDIENCE_SCOPE_MISMATCH", "受众仅属于另一个素材组")
        desired["audience"] = [{"audience": a} for a in change.audiences]
    if change.search_themes is not None:
        desired["search_theme"] = [{"text": t} for t in change.search_themes]
    expected = []
    pending = deepcopy(desired)
    for row in rows:
        data = row["asset_group_signal"]
        kind = next((k for k in desired if data.get(k)), None)
        if kind is None:
            expected.append(row)
        elif data[kind] in pending[kind]:
            pending[kind].remove(data[kind])
            expected.append(row)
        else:
            c.ops.append(operation("asset_group_signal", "remove", data["resource_name"]))
    for kind, values in pending.items():
        for value in values:
            body = {"asset_group": change.asset_group, kind: value}
            c.ops.append(operation("asset_group_signal", "create", body))
            expected.append({"asset_group_signal": body})
    c.checks.append(check("asset_group_signal", condition, SIGNAL_FIELDS, expected))
    c.summary.append({"parent": change.asset_group, "before": rows, "after": expected})
    c.warnings.append("信号是优化提示，不限定广告只能触达这些受众；修改信号不修改 Audience 本体。")


def group_status_or_remove(c, change):
    old, parent = group(c, change.resource)
    if change.kind == "set_status":
        if change.status == "ENABLED":
            c.settings.limit(c.account, int(parent["campaign_budget"]["amount_micros"]))
        c.update(
            "asset_group",
            change.resource,
            old["asset_group"],
            {"status": change.status},
            ["status"],
        )
        c.warnings.append("启用素材组可能产生投放；父系列暂停时仍不会投放。")
    else:
        group_links(c, change.resource)
        c.ops.append(operation("asset_group", "remove", change.resource))
        c.checks.append(
            check(
                "asset_group",
                f"asset_group.resource_name = {literal(change.resource)} AND asset_group.status != 'REMOVED'",
                ["asset_group.resource_name"],
                [],
            )
        )
        c.summary.append(
            {"resource": change.resource, "before": old["asset_group"], "after": "REMOVED"}
        )
        c.warnings.append("移除素材组不可恢复，素材库对象保留。")


def compile_pmax(provider, settings, request, plan_id):
    handlers = {
        "create_assets": create_assets,
        "create_pmax": create_pmax,
        "create_asset_group": create_group,
        "update_asset_group": update_group,
        "set_asset_group_assets": set_group_assets,
        "set_asset_group_signals": set_signals,
        "set_pmax_brand_assets": set_brand_assets,
    }
    handler = handlers.get(request.change.kind)
    if (
        request.change.kind in {"set_status", "remove_resource"}
        and "/assetGroups/" in request.change.resource
    ):
        handler = group_status_or_remove
    if handler is None:
        return None
    c = Context(provider, settings, request, plan_id)
    handler(c, request.change)
    return c.finish()
