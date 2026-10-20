"""Conversion configuration writes; never uploads events or changes tracking ownership."""

from copy import deepcopy

from .errors import AdsError
from .maintenance import Context
from .planner import check, operation
from .provider import literal

ACTION_FIELDS = [
    "conversion_action." + field
    for field in [
        "resource_name",
        "name",
        "status",
        "type",
        "category",
        "origin",
        "owner_customer",
        "primary_for_goal",
        "counting_type",
        "click_through_lookback_window_days",
        "view_through_lookback_window_days",
        "value_settings.default_value",
        "value_settings.default_currency_code",
        "value_settings.always_use_default_value",
    ]
]
CUSTOM_FIELDS = [
    "custom_conversion_goal." + f for f in ["resource_name", "name", "status", "conversion_actions"]
]
CONFIG_FIELDS = [
    "conversion_goal_campaign_config." + f
    for f in ["resource_name", "campaign", "goal_config_level", "custom_conversion_goal"]
]
IMPACT_FIELDS = [
    "campaign.resource_name",
    "campaign.name",
    "campaign.status",
    "campaign.advertising_channel_type",
    "campaign.bidding_strategy_type",
]


def leaves(body, prefix=""):
    for key, value in body.items():
        path = prefix + ("type" if key == "type_" else key)
        # GAQL selects these AdTextAsset messages as a whole, not their .text leaf.
        atomic = path in {
            "ad.responsive_display_ad.long_headline",
            "ad.demand_gen_video_responsive_ad.business_name",
        }
        if isinstance(value, dict) and not atomic:
            yield from leaves(value, path + ".")
        else:
            yield path


def verify_body(c, resource, condition, body):
    fields = [resource + "." + f for f in leaves(body)]
    item = check(resource, condition, fields, [{resource: body}])
    defaults = {}
    for field in leaves(body):
        value = body
        for key in field.split("."):
            value = value["type_" if key == "type" else key]
        if value in (False, 0, "", []):
            defaults[resource + "." + field] = value
    if defaults:
        item["default_values"] = defaults
    c.checks.append(item)


def create_named(c, resource, body):
    body = deepcopy(body)
    body["name"] += f" [tmcp-{c.plan_id}]"
    condition = f"{resource}.name = {literal(body['name'])} AND {resource}.status != 'REMOVED'"
    c.before["creation_marker"] = c.rows(resource, [resource + ".resource_name"], condition)
    if c.before["creation_marker"]:
        raise AdsError("CREATION_ALREADY_PRESENT", "本计划标记已有资源，先核对现有结果")
    c.ops.append(operation(resource, "create", body))
    verify_body(c, resource, condition, body)
    c.summary.append({"resource_type": resource, "before": None, "after": body})


def remove_object(c, resource, name, old):
    c.ops.append(operation(resource, "remove", name))
    c.checks.append(
        check(
            resource,
            f"{resource}.resource_name = {literal(name)} AND {resource}.status != 'REMOVED'",
            [resource + ".resource_name"],
            [],
        )
    )
    c.summary.append({"resource": name, "before": old, "after": "REMOVED"})


def impact(c, expected, campaign=None):
    condition = "campaign.status != 'REMOVED'"
    if campaign:
        condition += f" AND campaign.resource_name = {literal(campaign)}"
    rows = c.rows("campaign", IMPACT_FIELDS, condition)
    actual = sorted(row["campaign"]["resource_name"] for row in rows)
    if sorted(expected) != actual or len(expected) != len(set(expected)):
        raise AdsError(
            "CONVERSION_IMPACT_MISMATCH",
            "须明确列出影响范围内全部未移除系列",
            details={"campaigns": actual},
        )
    if campaign and not rows:
        raise AdsError("NOT_FOUND", "系列不存在或已移除")
    c.before["affected_campaigns"] = rows
    c.warnings.append("转化配置可能改变统计口径和智能出价；配置回读不证明事件已回传。")


def tracking_owner(c):
    fields = ["customer.conversion_tracking_setting.google_ads_conversion_customer"]
    rows = c.rows("customer", fields, "")
    c.before["conversion_tracking_owner"] = rows
    owner = (
        rows[0]
        .get("customer", {})
        .get("conversion_tracking_setting", {})
        .get("google_ads_conversion_customer")
        if rows
        else None
    )
    if owner != f"customers/{c.account.customer_id}":
        raise AdsError(
            "CONVERSION_OWNER_REQUIRED",
            "当前接口只管理本账户拥有的转化；请核对转换跟踪所有者，不自动切换 MCC",
            details={"conversion_customer": owner},
        )


def load_action(c, name, *, mutable=False):
    data = c.load("conversion_action", name, ACTION_FIELDS)["conversion_action"]
    if data.get("owner_customer") != f"customers/{c.account.customer_id}":
        raise AdsError("CONVERSION_OWNER_REQUIRED", "无法修改或选择其他账户拥有的转化动作")
    if mutable and data.get("type_") not in {"WEBPAGE", "UPLOAD_CLICKS", "UPLOAD_CALLS"}:
        raise AdsError(
            "CONVERSION_TYPE_UNSUPPORTED",
            "本版仅修改 WEBPAGE/UPLOAD_CLICKS/UPLOAD_CALLS；GA4、Firebase 等在来源端管理",
        )
    return data


def create_action(c, change):
    tracking_owner(c)
    impact(c, change.expected_campaigns)
    body = change.model_dump(exclude={"kind", "expected_campaigns"}, exclude_none=True)
    body["type_"] = body.pop("type")
    body["status"] = "ENABLED"
    create_named(c, "conversion_action", body)


def update_action(c, change):
    tracking_owner(c)
    impact(c, change.expected_campaigns)
    old = load_action(c, change.conversion_action, mutable=True)
    values = change.patch.model_dump(exclude_none=True)
    c.update("conversion_action", change.conversion_action, old, values, list(leaves(values)))


def remove_action(c, change):
    tracking_owner(c)
    impact(c, change.expected_campaigns)
    old = load_action(c, change.conversion_action, mutable=True)
    c.before["custom_goals"] = c.rows(
        "custom_conversion_goal", CUSTOM_FIELDS, "custom_conversion_goal.status != 'REMOVED'"
    )
    if any(
        change.conversion_action in row["custom_conversion_goal"].get("conversion_actions", [])
        for row in c.before["custom_goals"]
    ):
        raise AdsError(
            "CONVERSION_IN_CUSTOM_GOAL", "先从自定义目标中移除此转化动作，再单独预览删除"
        )
    remove_object(c, "conversion_action", change.conversion_action, old)


def set_goals(c, change):
    tracking_owner(c)
    impact(c, change.expected_campaigns, change.campaign)
    resource = "campaign_conversion_goal" if change.campaign else "customer_conversion_goal"
    fields = [resource + "." + f for f in ["resource_name", "category", "origin", "biddable"]]
    condition = f"{resource}.campaign = {literal(change.campaign)}" if change.campaign else ""
    if change.campaign:
        fields.append(resource + ".campaign")
    rows = c.rows(resource, fields, condition)
    c.before["goals"] = rows
    for target in change.goals:
        found = [
            r[resource]
            for r in rows
            if r[resource]["category"] == target.category and r[resource]["origin"] == target.origin
        ]
        if len(found) != 1:
            raise AdsError(
                "GOAL_NOT_FOUND",
                "目标由 Google 按转化动作生成，不能凭空创建",
                details={"category": target.category, "origin": target.origin},
            )
        old = found[0]
        c.update(resource, old["resource_name"], old, {"biddable": target.biddable}, ["biddable"])
    if not change.campaign:
        return
    configs = c.rows(
        "conversion_goal_campaign_config",
        CONFIG_FIELDS,
        f"conversion_goal_campaign_config.campaign = {literal(change.campaign)}",
    )
    c.before["campaign_goal_config"] = configs
    if len(configs) != 1:
        raise AdsError("GOAL_CONFIG_NOT_FOUND", "无法读取唯一系列转化配置")
    old = configs[0]["conversion_goal_campaign_config"]
    values = {"goal_config_level": change.goal_config_level or "CAMPAIGN"}
    if change.custom_conversion_goal is not None:
        if change.custom_conversion_goal:
            c.load("custom_conversion_goal", change.custom_conversion_goal, CUSTOM_FIELDS)
        values["custom_conversion_goal"] = change.custom_conversion_goal
    elif change.goal_config_level == "CUSTOMER":
        values["custom_conversion_goal"] = ""
    c.update("conversion_goal_campaign_config", old["resource_name"], old, values, list(values))


def custom_actions(c, actions):
    if len(actions) != len(set(actions)):
        raise AdsError("DUPLICATE_CONVERSION_ACTION", "自定义目标不能包含重复动作")
    for name in actions:
        load_action(c, name)


def create_custom(c, change):
    tracking_owner(c)
    custom_actions(c, change.conversion_actions)
    create_named(
        c,
        "custom_conversion_goal",
        {
            "name": change.name,
            "conversion_actions": change.conversion_actions,
        },
    )
    c.warnings.append("自定义目标被采用后，其中动作可参与出价，即使原动作是 secondary。")


def custom_impact(c, change):
    tracking_owner(c)
    old = c.load("custom_conversion_goal", change.custom_conversion_goal, CUSTOM_FIELDS)[
        "custom_conversion_goal"
    ]
    rows = c.rows(
        "conversion_goal_campaign_config",
        CONFIG_FIELDS,
        f"conversion_goal_campaign_config.custom_conversion_goal = {literal(change.custom_conversion_goal)}",
    )
    c.before["goal_references"] = rows
    actual = sorted(r["conversion_goal_campaign_config"]["campaign"] for r in rows)
    if sorted(change.expected_campaigns) != actual or len(set(actual)) != len(actual):
        raise AdsError(
            "CONVERSION_IMPACT_MISMATCH",
            "须列出引用自定义目标的全部系列",
            details={"campaigns": actual},
        )
    return old, rows


def update_custom(c, change):
    old, _ = custom_impact(c, change)
    values = change.patch.model_dump(exclude_none=True)
    if "conversion_actions" in values:
        custom_actions(c, values["conversion_actions"])
    c.update("custom_conversion_goal", change.custom_conversion_goal, old, values, list(values))


def remove_custom(c, change):
    old, rows = custom_impact(c, change)
    if rows:
        raise AdsError("CUSTOM_GOAL_IN_USE", "先将引用系列切换为其他目标，再移除自定义目标")
    remove_object(c, "custom_conversion_goal", change.custom_conversion_goal, old)


def compile_conversions(provider, settings, request, plan_id):
    handlers = {
        "create_conversion_action": create_action,
        "update_conversion_action": update_action,
        "remove_conversion_action": remove_action,
        "set_conversion_goals": set_goals,
        "create_custom_conversion_goal": create_custom,
        "update_custom_conversion_goal": update_custom,
        "remove_custom_conversion_goal": remove_custom,
    }
    handler = handlers.get(request.change.kind)
    if handler is None:
        return None
    c = Context(provider, settings, request, plan_id)
    handler(c, request.change)
    return c.finish()
