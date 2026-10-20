"""Display, Demand Gen and App install campaigns. All new entities start paused."""

from copy import deepcopy

from . import models as m
from .conversions import load_action, remove_object, verify_body
from .errors import AdsError
from .maintenance import CAMPAIGN_EDIT_FIELDS, Context
from .planner import GROUP_FIELDS, check, operation
from .pmax import ASSET_LIBRARY_FIELDS, AUDIENCE_FIELDS
from .provider import literal

CHANNEL_FIELDS = [
    *CAMPAIGN_EDIT_FIELDS,
    "campaign.advertising_channel_sub_type",
    "campaign.app_campaign_setting.app_id",
    "campaign.app_campaign_setting.app_store",
    "campaign.app_campaign_setting.bidding_strategy_goal_type",
    "campaign.selective_optimization.conversion_actions",
    "campaign.target_cpa.target_cpa_micros",
    "campaign.target_roas.target_roas",
]
CHANNEL_GROUP_FIELDS = [
    *GROUP_FIELDS,
    "ad_group.type",
    "ad_group.optimized_targeting_enabled",
    "ad_group.targeting_setting.target_restrictions",
    "ad_group.audience_setting.use_audience_grouped",
]
FORMATS = {
    "RESPONSIVE_DISPLAY": ("DISPLAY", "responsive_display_ad", "RESPONSIVE_DISPLAY_AD"),
    "DEMAND_GEN_IMAGE": ("DEMAND_GEN", "demand_gen_multi_asset_ad", "DEMAND_GEN_MULTI_ASSET_AD"),
    "DEMAND_GEN_VIDEO": (
        "DEMAND_GEN",
        "demand_gen_video_responsive_ad",
        "DEMAND_GEN_VIDEO_RESPONSIVE_AD",
    ),
    "APP": ("APP", "app_ad", "APP_AD"),
}
AD_FIELDS = [
    "ad_group_ad.resource_name",
    "ad_group_ad.ad_group",
    "ad_group_ad.status",
    "ad_group_ad.ad.resource_name",
    "ad_group_ad.ad.name",
    "ad_group_ad.ad.type",
    "ad_group_ad.ad.final_urls",
    *[
        "ad_group_ad.ad." + field + "." + sub
        for field, subs in {
            "responsive_display_ad": [
                "headlines",
                "long_headline",
                "descriptions",
                "business_name",
                "marketing_images",
                "square_marketing_images",
                "square_logo_images",
                "youtube_videos",
            ],
            "demand_gen_multi_asset_ad": [
                "headlines",
                "descriptions",
                "business_name",
                "marketing_images",
                "square_marketing_images",
                "portrait_marketing_images",
                "logo_images",
            ],
            "demand_gen_video_responsive_ad": [
                "headlines",
                "long_headlines",
                "descriptions",
                "business_name",
                "videos",
                "logo_images",
            ],
            "app_ad": ["headlines", "descriptions", "images", "youtube_videos"],
        }.items()
        for sub in subs
    ],
]
GROUP_TARGET_FIELDS = [
    "ad_group_criterion." + field
    for field in [
        "resource_name",
        "ad_group",
        "status",
        "negative",
        "type",
        "audience.audience",
        "user_list.user_list",
        "location.geo_target_constant",
        "language.language_constant",
    ]
]


def campaign(c, name):
    row = c.load("campaign", name, CHANNEL_FIELDS)
    body = row["campaign"]
    channel = body["advertising_channel_type"]
    sub = body.get("advertising_channel_sub_type", "UNSPECIFIED")
    if channel == "MULTI_CHANNEL" and sub == "APP_CAMPAIGN":
        channel = "APP"
    if channel not in {"DISPLAY", "DEMAND_GEN", "APP"}:
        raise AdsError("CHANNEL_UNSUPPORTED", "此接口用于 Display、Demand Gen、App 安装推广")
    if channel != "APP" and sub not in {"UNSPECIFIED", "UNKNOWN", ""}:
        raise AdsError("CHANNEL_SUBTYPE_UNSUPPORTED", "此广告子类型尚未接入")
    return row, channel


def group(c, name):
    row = c.load("ad_group", name, CHANNEL_GROUP_FIELDS)
    parent, channel = campaign(c, row["ad_group"]["campaign"])
    return row, parent, channel


def bid_values(c, bidding, channel, app=None):
    allowed = {
        "DISPLAY": {"MANUAL_CPC", "MAXIMIZE_CONVERSIONS", "TARGET_CPA", "TARGET_ROAS"},
        "DEMAND_GEN": {
            "MAXIMIZE_CONVERSIONS",
            "MAXIMIZE_CONVERSION_VALUE",
            "TARGET_CPA",
            "TARGET_ROAS",
        },
        "APP": {"TARGET_CPA", "TARGET_ROAS"},
    }[channel]
    if bidding.strategy not in allowed:
        raise AdsError(
            "CHANNEL_BIDDING_UNSUPPORTED",
            "该广告类型不支持此出价策略",
            details={"allowed": sorted(allowed)},
        )
    if channel == "APP":
        goal = app.goal if isinstance(app, m.AppSettings) else app["bidding_strategy_goal_type"]
        value_goal = goal == "OPTIMIZE_RETURN_ON_ADVERTISING_SPEND"
        if (bidding.strategy == "TARGET_ROAS") != value_goal:
            raise AdsError(
                "APP_GOAL_BIDDING_MISMATCH",
                "App 价值目标使用目标 ROAS，安装/操作成本目标使用目标 CPA",
            )
    field, values = {
        "MANUAL_CPC": ("manual_cpc", {"enhanced_cpc_enabled": False}),
        "MAXIMIZE_CONVERSIONS": (
            "maximize_conversions",
            {"target_cpa_micros": m.micros(bidding.target_cpa) if bidding.target_cpa else 0},
        ),
        "MAXIMIZE_CONVERSION_VALUE": (
            "maximize_conversion_value",
            {"target_roas": bidding.target_roas or 0},
        ),
        "TARGET_CPA": (
            "target_cpa",
            {"target_cpa_micros": m.micros(bidding.target_cpa)} if bidding.target_cpa else {},
        ),
        "TARGET_ROAS": (
            "target_roas",
            {"target_roas": bidding.target_roas} if bidding.target_roas else {},
        ),
    }[bidding.strategy]
    return {field: values}


def validate_app_actions(c, app):
    if len(app.conversion_actions) != len(set(app.conversion_actions)):
        raise AdsError("DUPLICATE_CONVERSION_ACTION", "App 转化动作不可重复")
    for name in app.conversion_actions:
        row = load_action(c, name)
        # Read the app identity as well; never silently use another application's events.
        details = c.load(
            "conversion_action",
            name,
            [
                "conversion_action.resource_name",
                "conversion_action.app_id",
                "conversion_action.origin",
                "conversion_action.status",
            ],
        )["conversion_action"]
        if (
            row.get("status") != "ENABLED"
            or details.get("origin") != "APP"
            or details.get("app_id") != app.app_id
        ):
            raise AdsError("APP_CONVERSION_MISMATCH", "App 优化必须引用该应用的有效 APP 转化动作")


def ad_body(c, content, channel):
    required_channel, field, _ = FORMATS[content.format]
    if required_channel != channel:
        raise AdsError("AD_CHANNEL_MISMATCH", "广告格式与系列类型不一致")
    image_keys = [
        "marketing_images",
        "square_marketing_images",
        "portrait_marketing_images",
        "logo_images",
    ]
    for key in image_keys + ["videos"]:
        for name in getattr(content, key):
            data = c.load("asset", name, ASSET_LIBRARY_FIELDS)["asset"]
            expected = "YOUTUBE_VIDEO" if key == "videos" else "IMAGE"
            if data.get("type_") != expected:
                raise AdsError("ASSET_TYPE_MISMATCH", f"{key} 需要 {expected} 素材")
            if expected == "IMAGE":
                size = data.get("image_asset", {}).get("full_size", {})
                w, h = int(size.get("width_pixels", 0)), int(size.get("height_pixels", 0))
                if not w or not h:
                    raise AdsError("IMAGE_DIMENSIONS_REQUIRED", "无法确认图片尺寸")
                if channel != "APP":
                    ratio, minimum = {
                        "marketing_images": (1.91, (600, 314)),
                        "square_marketing_images": (1, (300, 300)),
                        "portrait_marketing_images": (0.8, (480, 600)),
                        "logo_images": (1, (128, 128)),
                    }[key]
                    if abs(w / h - ratio) > 0.02 or w < minimum[0] or h < minimum[1]:
                        raise AdsError("IMAGE_DIMENSIONS_INVALID", f"{key} 图片比例或尺寸不符")
    data = {
        "headlines": [{"text": t} for t in content.headlines],
        "descriptions": [{"text": t} for t in content.descriptions],
    }
    if content.business_name:
        data["business_name"] = (
            {"text": content.business_name}
            if content.format == "DEMAND_GEN_VIDEO"
            else content.business_name
        )
    if content.long_headline:
        key = "long_headlines" if content.format == "DEMAND_GEN_VIDEO" else "long_headline"
        data[key] = (
            [{"text": content.long_headline}]
            if key.endswith("s")
            else {"text": content.long_headline}
        )
    for key in image_keys + ["videos"]:
        values = getattr(content, key)
        if not values:
            continue
        mapped = key
        if channel == "APP" and key in image_keys:
            mapped = "images"
        elif key == "videos" and channel in {"APP", "DISPLAY"}:
            mapped = "youtube_videos"
        elif key == "logo_images" and channel == "DISPLAY":
            mapped = "square_logo_images"
        data.setdefault(mapped, []).extend({"asset": v} for v in values)
    if channel == "APP" and len(data.get("images", [])) > 20:
        raise AdsError("TOO_MANY_APP_IMAGES", "App 图片合计最多 20")
    body = {field: data}
    if content.final_urls:
        body["final_urls"] = content.final_urls
    return body


def group_targets(c, channel, audience, user_lists):
    if len(user_lists) != len(set(user_lists)):
        raise AdsError("DUPLICATE_TARGET", "重复受众名单")
    if channel == "APP" and (audience or user_lists):
        raise AdsError("APP_TARGETING_MANAGED", "本版 App 安装推广不设置手动受众")
    if channel != "DEMAND_GEN" and audience:
        raise AdsError("AUDIENCE_CHANNEL_MISMATCH", "Audience 定义关联只用于 Demand Gen")
    if channel != "DISPLAY" and user_lists:
        raise AdsError("USER_LIST_CHANNEL_MISMATCH", "user_lists 直接定向只用于 Display")
    desired = []
    if audience:
        body = c.load("audience", audience, AUDIENCE_FIELDS)["audience"]
        if body.get("status") != "ENABLED" or body.get("scope") == "ASSET_GROUP":
            raise AdsError("AUDIENCE_UNAVAILABLE", "需要有效且可用于广告组的 Audience")
        desired.append({"audience": {"audience": audience}})
    for name in user_lists:
        data = c.load(
            "user_list",
            name,
            ["user_list.resource_name", "user_list.name", "user_list.account_user_list_status"],
        )["user_list"]
        if data.get("account_user_list_status") == "DISABLED":
            raise AdsError("USER_LIST_UNAVAILABLE", "受众名单已移除或不可用")
        desired.append({"user_list": {"user_list": name}})
    return desired


def add_ad(c, content, channel, parent, marker):
    body = ad_body(c, content, channel)
    body["name"] = marker
    c.ops.append(
        operation("ad_group_ad", "create", {"ad_group": parent, "status": "PAUSED", "ad": body})
    )
    verify_body(
        c,
        "ad_group_ad",
        f"ad_group_ad.ad.name = {literal(marker)} AND ad_group_ad.status != 'REMOVED'",
        {"status": "PAUSED", "ad": body},
    )


def geo_criteria(geo_ids, language_ids):
    return [
        *[{"location": {"geo_target_constant": f"geoTargetConstants/{v}"}} for v in geo_ids],
        *[{"language": {"language_constant": f"languageConstants/{v}"}} for v in language_ids],
    ]


def add_group(c, content, parent, channel, strategy, index, defaults=None):
    root = f"customers/{c.account.customer_id}"
    ref = f"{root}/adGroups/{-10 - index}"
    name = content.name + f" [tmcp-{c.plan_id}]"
    body = {"resource_name": ref, "campaign": parent, "name": name, "status": "PAUSED"}
    if channel == "DISPLAY":
        body["type_"] = "DISPLAY_STANDARD"
    if channel == "APP":
        if content.optimized_targeting or content.cpc_bid:
            raise AdsError("APP_GROUP_MANAGED", "App 不设置手动 CPC 或优化型定向开关")
    else:
        body["optimized_targeting_enabled"] = content.optimized_targeting
    if strategy == "MANUAL_CPC":
        if content.cpc_bid is None:
            raise AdsError("CPC_REQUIRED", "手动 CPC 广告组需要 cpc_bid")
        body["cpc_bid_micros"] = m.micros(content.cpc_bid)
    elif content.cpc_bid is not None:
        raise AdsError("MANUAL_CPC_REQUIRED", "自动出价广告组不能设置 CPC")
    desired = group_targets(c, channel, content.audience, content.user_lists)
    if channel == "DEMAND_GEN":
        geo_ids = content.geo_target_ids or (defaults.geo_target_ids if defaults else None)
        languages = content.language_ids or (defaults.language_ids if defaults else None)
        if not geo_ids or not languages:
            raise AdsError("GROUP_GEO_REQUIRED", "新 Demand Gen 广告组需要明确地区和语言")
        desired += geo_criteria(geo_ids, languages)
        body["audience_setting"] = {"use_audience_grouped": True}
    elif content.geo_target_ids is not None or content.language_ids is not None:
        raise AdsError("GROUP_GEO_UNSUPPORTED", "Display/App 请使用系列级地区和语言")
    if channel == "DISPLAY" and content.user_lists:
        body["targeting_setting"] = {
            "target_restrictions": [{"targeting_dimension": "AUDIENCE", "bid_only": False}]
        }
    c.ops.append(operation("ad_group", "create", body))
    expected = {k: v for k, v in body.items() if k not in {"resource_name", "campaign"}}
    condition = f"ad_group.name = {literal(name)} AND ad_group.status != 'REMOVED'"
    verify_body(c, "ad_group", condition, expected)
    for target in desired:
        c.ops.append(operation("ad_group_criterion", "create", {"ad_group": ref, **target}))
    c.checks.append(
        check(
            "ad_group_criterion",
            f"ad_group.name = {literal(name)} AND ad_group_criterion.status != 'REMOVED' "
            "AND ad_group_criterion.negative = FALSE "
            "AND ad_group_criterion.type IN ('AUDIENCE', 'USER_LIST', 'LOCATION', 'LANGUAGE')",
            GROUP_TARGET_FIELDS,
            [{"ad_group_criterion": item} for item in desired],
        )
    )
    for ad_index, content_ad in enumerate(content.ads):
        add_ad(c, content_ad, channel, ref, f"Ad [tmcp-{c.plan_id}-{index}-{ad_index}]")


def create_campaign(c, change):
    amount = m.micros(change.daily_amount)
    c.settings.limit(c.account, amount)
    name = change.name + f" [tmcp-{c.plan_id}]"
    condition = f"campaign.name = {literal(name)} AND campaign.status != 'REMOVED'"
    c.before["creation_marker"] = c.rows("campaign", ["campaign.resource_name"], condition)
    if c.before["creation_marker"]:
        raise AdsError("CREATION_ALREADY_PRESENT", "本计划标记已存在")
    root = f"customers/{c.account.customer_id}"
    budget, ref = root + "/campaignBudgets/-1", root + "/campaigns/-2"
    budget_body = {
        "resource_name": budget,
        "name": name,
        "amount_micros": amount,
        "explicitly_shared": False,
        "delivery_method": "STANDARD",
    }
    c.ops.append(operation("campaign_budget", "create", budget_body))
    body = {
        "resource_name": ref,
        "name": name,
        "status": "PAUSED",
        "campaign_budget": budget,
        "advertising_channel_type": "MULTI_CHANNEL" if change.channel == "APP" else change.channel,
        "contains_eu_political_advertising": change.eu_political_advertising,
        **bid_values(c, change.bidding, change.channel, change.app),
    }
    if change.app:
        validate_app_actions(c, change.app)
        body["advertising_channel_sub_type"] = "APP_CAMPAIGN"
        body["app_campaign_setting"] = {
            "app_id": change.app.app_id,
            "app_store": change.app.app_store,
            "bidding_strategy_goal_type": change.app.goal,
        }
        if change.app.conversion_actions:
            body["selective_optimization"] = {"conversion_actions": change.app.conversion_actions}
    c.ops.append(operation("campaign", "create", body))
    verify_body(
        c,
        "campaign_budget",
        f"campaign_budget.name = {literal(name)} AND campaign_budget.status != 'REMOVED'",
        {k: v for k, v in budget_body.items() if k != "resource_name"},
    )
    verify_body(
        c,
        "campaign",
        condition,
        {k: v for k, v in body.items() if k not in {"resource_name", "campaign_budget"}},
    )
    # Demand Gen location/language belong to each ad group, not campaign criteria.
    criteria = (
        []
        if change.channel == "DEMAND_GEN"
        else geo_criteria(change.geo_target_ids, change.language_ids)
    )
    for criterion in criteria:
        c.ops.append(operation("campaign_criterion", "create", {"campaign": ref, **criterion}))
    c.checks.append(
        check(
            "campaign_criterion",
            f"campaign.name = {literal(name)} AND campaign_criterion.status != 'REMOVED' AND campaign_criterion.type IN ('LOCATION', 'LANGUAGE')",
            [
                "campaign_criterion.location.geo_target_constant",
                "campaign_criterion.language.language_constant",
            ],
            [{"campaign_criterion": value} for value in criteria],
        )
    )
    for index, content in enumerate(change.ad_groups):
        if content.cpc_bid and m.micros(content.cpc_bid) > amount:
            raise AdsError("CPC_OVER_DAILY_BUDGET", "CPC 不能高于日预算")
        add_group(c, content, ref, change.channel, change.bidding.strategy, index, change)
    c.summary.append(
        {
            "before": None,
            "after": {
                "campaign_name": name,
                "channel": change.channel,
                "status": "PAUSED",
                "daily_amount": change.daily_amount,
            },
        }
    )
    c.warnings.append(
        "所有新系列、广告组和广告均 PAUSED。App ID/商店和系列类型创建后不可修改；配置成功不证明政策审核、投放或转化有效。"
    )


def create_group(c, change):
    parent, channel = campaign(c, change.campaign)
    if change.group.cpc_bid and m.micros(change.group.cpc_bid) > int(
        parent["campaign_budget"]["amount_micros"]
    ):
        raise AdsError("CPC_OVER_DAILY_BUDGET", "CPC 不能高于日预算")
    add_group(
        c, change.group, change.campaign, channel, parent["campaign"]["bidding_strategy_type"], 0
    )


def create_ad(c, change):
    _, _, channel = group(c, change.ad_group)
    add_ad(c, change.ad, channel, change.ad_group, f"Ad [tmcp-{c.plan_id}]")


def replace_ad(c, change):
    old = c.load("ad_group_ad", change.ad_group_ad, AD_FIELDS)["ad_group_ad"]
    _, _, channel = group(c, old["ad_group"])
    remove_object(c, "ad_group_ad", change.ad_group_ad, old)
    add_ad(c, change.ad, channel, old["ad_group"], f"Ad [tmcp-{c.plan_id}]")
    c.warnings.append("新广告为 PAUSED，旧关联移除；ID 和历史统计不继承，其他广告组中的关联保留。")


def update_group(c, change):
    old, parent, channel = group(c, change.ad_group)
    values = change.patch.model_dump(exclude_none=True)
    if channel == "APP" and set(values) - {"name"}:
        raise AdsError("APP_GROUP_MANAGED", "App 安装广告组仅维护名称和状态")
    if "cpc_bid" in values:
        if parent["campaign"]["bidding_strategy_type"] != "MANUAL_CPC":
            raise AdsError("MANUAL_CPC_REQUIRED", "自动出价不能设置广告组 CPC")
        amount = m.micros(values.pop("cpc_bid"))
        if amount > int(parent["campaign_budget"]["amount_micros"]):
            raise AdsError("CPC_OVER_DAILY_BUDGET", "CPC 不能高于日预算")
        c.settings.limit(c.account, int(parent["campaign_budget"]["amount_micros"]))
        values["cpc_bid_micros"] = amount
    if "optimized_targeting" in values:
        values["optimized_targeting_enabled"] = values.pop("optimized_targeting")
    c.update("ad_group", change.ad_group, old["ad_group"], values, list(values))


def set_bidding(c, change):
    row, channel = campaign(c, change.campaign)
    c.settings.limit(c.account, int(row["campaign_budget"]["amount_micros"]))
    old = row["campaign"]
    app = change.app_goal or old.get("app_campaign_setting")
    if change.app_goal:
        if channel != "APP":
            raise AdsError("APP_REQUIRED", "app_goal 只用于 App 系列")
        if any(
            getattr(change.app_goal, key) != old.get("app_campaign_setting", {}).get(key)
            for key in ["app_id", "app_store"]
        ):
            raise AdsError("APP_ID_IMMUTABLE", "不能修改已创建系列的 App ID 或商店")
        validate_app_actions(c, change.app_goal)
    values = bid_values(c, change.bidding, channel, app)
    paths = [f"{field}.{sub}" for field, obj in values.items() for sub in obj]
    # Setting a standard bidding strategy must explicitly leave a portfolio strategy.
    paths.append("bidding_strategy")
    if change.app_goal:
        values["app_campaign_setting"] = {"bidding_strategy_goal_type": change.app_goal.goal}
        values["selective_optimization"] = {
            "conversion_actions": change.app_goal.conversion_actions
        }
        paths += [
            "app_campaign_setting.bidding_strategy_goal_type",
            "selective_optimization.conversion_actions",
        ]
    expected = {"resource_name": change.campaign, **deepcopy(values), "bidding_strategy": ""}
    c.update(
        "campaign",
        change.campaign,
        old,
        values,
        paths,
        expected=expected,
        clear_paths={"bidding_strategy"},
    )
    c.warnings.append("出价目标修改会改变优化方式；转化配置存在不证明有有效回传。")


def set_group_targeting(c, change):
    group_row, _, channel = group(c, change.ad_group)
    if channel == "APP":
        raise AdsError("APP_TARGETING_MANAGED", "App 定向请使用系列级地区和语言")
    selected, desired = [], []
    if change.audience is not None:
        if channel != "DEMAND_GEN":
            raise AdsError("AUDIENCE_CHANNEL_MISMATCH", "audience 只用于 Demand Gen")
        if change.audience and not group_row["ad_group"].get("audience_setting", {}).get(
            "use_audience_grouped"
        ):
            raise AdsError(
                "AUDIENCE_GROUPING_REQUIRED", "该广告组未启用 grouped audience；请使用兼容的广告组"
            )
        selected.append("AUDIENCE")
        desired += group_targets(c, channel, change.audience or None, [])
    if change.user_lists is not None:
        if channel != "DISPLAY":
            raise AdsError("USER_LIST_CHANNEL_MISMATCH", "user_lists 只用于 Display")
        selected.append("USER_LIST")
        desired += group_targets(c, channel, None, change.user_lists)
    for key, typ, inner, collection in [
        ("geo_target_ids", "LOCATION", "geo_target_constant", "geoTargetConstants"),
        ("language_ids", "LANGUAGE", "language_constant", "languageConstants"),
    ]:
        values = getattr(change, key)
        if values is not None:
            if channel != "DEMAND_GEN":
                raise AdsError(
                    "GROUP_GEO_UNSUPPORTED", "本版只有 Demand Gen 接入广告组级地区和语言"
                )
            selected.append(typ)
            desired += [{typ.lower(): {inner: f"{collection}/{v}"}} for v in values]
    condition = f"ad_group_criterion.ad_group = {literal(change.ad_group)} AND ad_group_criterion.status != 'REMOVED' AND ad_group_criterion.negative = FALSE AND ad_group_criterion.type IN ({', '.join(literal(t) for t in selected)})"
    rows = c.rows("ad_group_criterion", GROUP_TARGET_FIELDS, condition)
    c.before["managed_targets"] = rows
    keys = ["audience", "user_list", "location", "language"]

    def target_key(body):
        return m.canonical({k: body[k] for k in keys if body.get(k)})

    existing = {target_key(row["ad_group_criterion"]): row["ad_group_criterion"] for row in rows}
    wanted = {target_key(body): body for body in desired}
    for row in rows:
        if target_key(row["ad_group_criterion"]) not in wanted:
            c.ops.append(
                operation(
                    "ad_group_criterion", "remove", row["ad_group_criterion"]["resource_name"]
                )
            )
    for key, body in wanted.items():
        if key not in existing:
            c.ops.append(
                operation("ad_group_criterion", "create", {"ad_group": change.ad_group, **body})
            )
    if channel == "DISPLAY" and change.user_lists:
        old = group_row["ad_group"]
        restrictions = old.get("targeting_setting", {}).get("target_restrictions", [])
        desired_restrictions = [
            r for r in restrictions if r.get("targeting_dimension") != "AUDIENCE"
        ] + [{"targeting_dimension": "AUDIENCE", "bid_only": False}]
        if restrictions != desired_restrictions:
            c.update(
                "ad_group",
                change.ad_group,
                old,
                {"targeting_setting": {"target_restrictions": desired_restrictions}},
                ["targeting_setting.target_restrictions"],
            )
    fields = [
        "ad_group_criterion." + f
        for f in [
            "audience.audience",
            "user_list.user_list",
            "location.geo_target_constant",
            "language.language_constant",
        ]
    ]
    c.checks.append(
        check(
            "ad_group_criterion",
            condition,
            fields,
            [{"ad_group_criterion": value} for value in desired],
        )
    )
    c.summary.append({"parent": change.ad_group, "before": rows, "after": desired})


def compile_channels(provider, settings, request, plan_id):
    handler = {
        "create_channel_campaign": create_campaign,
        "create_channel_ad_group": create_group,
        "create_channel_ad": create_ad,
        "replace_channel_ad": replace_ad,
        "update_channel_ad_group": update_group,
        "set_channel_bidding": set_bidding,
        "set_channel_group_targeting": set_group_targeting,
    }.get(request.change.kind)
    if handler is None:
        return None
    c = Context(provider, settings, request, plan_id)
    handler(c, request.change)
    return c.finish()
