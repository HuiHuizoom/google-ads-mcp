"""SDK-backed offline lifecycle and failure tests; not real Google acceptance."""

from copy import deepcopy
from uuid import uuid4

import pytest
from pydantic import ValidationError

from team_ads_mcp.engine import Engine
from team_ads_mcp.errors import AdsError
from team_ads_mcp.models import PlanInput

from .fakes import ACCOUNT, CAMPAIGN, ROOT, FakeProvider, settings

LANDSCAPE = ROOT + "/assets/701"
SQUARE = ROOT + "/assets/702"
VIDEO = ROOT + "/assets/703"
AUDIENCE = ROOT + "/audiences/704"
USER_LIST = ROOT + "/userLists/705"
ACTION = ROOT + "/conversionActions/801"
CUSTOM = ROOT + "/customConversionGoals/802"
CONFIG = ROOT + "/conversionGoalCampaignConfigs/11"
GOAL = ROOT + "/campaignConversionGoals/11~1~2"
CUSTOMER_GOAL = ROOT + "/customerConversionGoals/1~2"


@pytest.fixture
def engine(tmp_path):
    p = FakeProvider()
    for name, width, height in [(LANDSCAPE, 1200, 628), (SQUARE, 1200, 1200)]:
        p.records["asset"][name] = {
            "resource_name": name,
            "type_": "IMAGE",
            "image_asset": {"full_size": {"width_pixels": width, "height_pixels": height}},
        }
    p.records["asset"][VIDEO] = {
        "resource_name": VIDEO,
        "type_": "YOUTUBE_VIDEO",
        "youtube_video_asset": {"youtube_video_id": "abcdefghijk"},
    }
    p.records["audience"][AUDIENCE] = {
        "resource_name": AUDIENCE,
        "name": "Example",
        "status": "ENABLED",
        "scope": "CUSTOMER",
    }
    p.records["user_list"][USER_LIST] = {
        "resource_name": USER_LIST,
        "name": "Visitors",
        "account_user_list_status": "ENABLED",
    }
    p.records["conversion_action"][ACTION] = {
        "resource_name": ACTION,
        "name": "Purchase",
        "status": "ENABLED",
        "type_": "WEBPAGE",
        "owner_customer": ROOT,
        "origin": "WEBSITE",
        "category": "PURCHASE",
        "primary_for_goal": True,
        "counting_type": "MANY_PER_CLICK",
        "click_through_lookback_window_days": 30,
        "value_settings": {
            "default_value": 19.0,
            "default_currency_code": "USD",
            "always_use_default_value": False,
        },
    }
    p.records["conversion_goal_campaign_config"][CONFIG] = {
        "resource_name": CONFIG,
        "campaign": CAMPAIGN,
        "goal_config_level": "CUSTOMER",
    }
    p.records["campaign_conversion_goal"][GOAL] = {
        "resource_name": GOAL,
        "campaign": CAMPAIGN,
        "category": "PURCHASE",
        "origin": "WEBSITE",
        "biddable": True,
    }
    p.records["customer_conversion_goal"][CUSTOMER_GOAL] = {
        "resource_name": CUSTOMER_GOAL,
        "category": "PURCHASE",
        "origin": "WEBSITE",
        "biddable": True,
    }
    return Engine(tmp_path, settings(), p)


def preview(engine, change, key=None):
    return engine.preview(PlanInput(account=ACCOUNT, request_key=key or uuid4().hex, change=change))


def apply(engine, change):
    plan = preview(engine, change)
    result = engine.submit(plan["id"])
    assert result["status"] == "VERIFIED", result.get("result")
    return result


def content(fmt):
    value = {
        "format": fmt,
        "headlines": ["Explore Our Product"],
        "descriptions": ["Discover useful features.", "Learn more about our product."],
    }
    if fmt != "APP":
        value.update(business_name="Example", final_urls=["https://example.com"])
    if fmt in {"RESPONSIVE_DISPLAY", "DEMAND_GEN_IMAGE"}:
        value.update(marketing_images=[LANDSCAPE], square_marketing_images=[SQUARE])
    if fmt in {"RESPONSIVE_DISPLAY", "DEMAND_GEN_VIDEO"}:
        value["long_headline"] = "Find the right product for your team"
    if fmt.startswith("DEMAND_GEN"):
        value["logo_images"] = [SQUARE]
    if fmt == "DEMAND_GEN_VIDEO":
        value["videos"] = [VIDEO]
    return value


def creation(channel, fmt=None):
    fmt = (
        fmt
        or {"DISPLAY": "RESPONSIVE_DISPLAY", "DEMAND_GEN": "DEMAND_GEN_IMAGE", "APP": "APP"}[
            channel
        ]
    )
    group = {"name": "Example group", "ads": [content(fmt)]}
    value = {
        "kind": "create_channel_campaign",
        "channel": channel,
        "name": "Acceptance",
        "currency": "USD",
        "daily_amount": "5",
        "bidding": {"strategy": "MAXIMIZE_CONVERSIONS"},
        "geo_target_ids": ["2840"],
        "language_ids": ["1000"],
        "eu_political_advertising": "DOES_NOT_CONTAIN_EU_POLITICAL_ADVERTISING",
        "ad_groups": [group],
    }
    if channel == "DISPLAY":
        value["bidding"] = {"strategy": "MANUAL_CPC"}
        group.update(cpc_bid="0.20", user_lists=[USER_LIST])
    elif channel == "DEMAND_GEN":
        group["audience"] = AUDIENCE
    else:
        value["bidding"] = {"strategy": "TARGET_CPA", "target_cpa": "0.50"}
        value["app"] = {
            "app_id": "com.example.app",
            "app_store": "GOOGLE_APP_STORE",
            "goal": "OPTIMIZE_INSTALLS_TARGET_INSTALL_COST",
        }
    return value


def receipt(result, resource):
    return [
        r[resource + "_result"]["resource_name"]
        for r in result["result"]["receipt"]["mutate_operation_responses"]
        if resource + "_result" in r
    ]


@pytest.mark.parametrize(
    "channel,fmt",
    [
        ("DISPLAY", "RESPONSIVE_DISPLAY"),
        ("DEMAND_GEN", "DEMAND_GEN_IMAGE"),
        ("DEMAND_GEN", "DEMAND_GEN_VIDEO"),
        ("APP", "APP"),
    ],
)
def test_create_and_maintain_channel_campaign(engine, channel, fmt):
    result = apply(engine, creation(channel, fmt))
    camp, budget = receipt(result, "campaign")[0], receipt(result, "campaign_budget")[0]
    group = receipt(result, "ad_group")[0]
    ad = receipt(result, "ad_group_ad")[0]
    p = engine.provider
    for resource, name in [("campaign", camp), ("ad_group", group), ("ad_group_ad", ad)]:
        assert p.records[resource][name]["status"] == "PAUSED"
    apply(
        engine,
        {
            "kind": "set_budget",
            "budget": budget,
            "currency": "USD",
            "daily_amount": "6",
            "expected_campaigns": [camp],
        },
    )
    apply(
        engine, {"kind": "update_campaign", "campaign": camp, "patch": {"name": "Updated campaign"}}
    )
    apply(
        engine,
        {"kind": "update_channel_ad_group", "ad_group": group, "patch": {"name": "Updated group"}},
    )
    new_content = content(fmt)
    new_content["headlines"] = ["Updated headline"]
    edited = apply(engine, {"kind": "replace_channel_ad", "ad_group_ad": ad, "ad": new_content})
    new_ad = receipt(edited, "ad_group_ad")[-1]
    assert p.records["ad_group_ad"][ad]["status"] == "REMOVED"
    assert p.records["ad_group_ad"][new_ad]["status"] == "PAUSED"
    # Enabling a child under a paused campaign does not make the parent serve.
    apply(engine, {"kind": "set_status", "resource": new_ad, "status": "ENABLED"})
    assert p.records["campaign"][camp]["status"] == "PAUSED"
    apply(engine, {"kind": "set_status", "resource": new_ad, "status": "PAUSED"})
    apply(
        engine,
        {
            "kind": "set_channel_bidding",
            "campaign": camp,
            "bidding": {"strategy": "TARGET_CPA", "target_cpa": "0.70"},
        },
    )
    if channel == "DEMAND_GEN":
        assert not receipt(result, "campaign_criterion")
        apply(
            engine,
            {
                "kind": "set_channel_group_targeting",
                "ad_group": group,
                "geo_target_ids": ["2124"],
                "language_ids": ["1000"],
            },
        )
    else:
        apply(
            engine,
            {
                "kind": "set_targeting",
                "campaign": camp,
                "patch": {"geo_target_ids": ["2124"], "language_ids": ["1000"]},
            },
        )
    extra = deepcopy(creation(channel, fmt)["ad_groups"][0])
    extra.pop("cpc_bid", None)
    if channel == "DEMAND_GEN":
        extra.update(geo_target_ids=["2840"], language_ids=["1000"])
    added = apply(engine, {"kind": "create_channel_ad_group", "campaign": camp, "group": extra})
    apply(engine, {"kind": "create_channel_ad", "ad_group": group, "ad": content(fmt)})
    apply(engine, {"kind": "remove_resource", "resource": receipt(added, "ad_group")[0]})
    apply(engine, {"kind": "remove_resource", "resource": camp})


def test_channel_targeting_does_not_remove_unselected_or_negative_criteria(engine):
    result = apply(engine, creation("DEMAND_GEN"))
    group = receipt(result, "ad_group")[0]
    negative = ROOT + "/adGroupCriteria/101~999"
    engine.provider.records["ad_group_criterion"][negative] = {
        "resource_name": negative,
        "ad_group": group,
        "status": "ENABLED",
        "negative": True,
        "type_": "LOCATION",
        "location": {"geo_target_constant": "geoTargetConstants/2036"},
    }
    apply(
        engine,
        {
            "kind": "set_channel_group_targeting",
            "ad_group": group,
            "geo_target_ids": ["2124"],
            "language_ids": ["1000"],
        },
    )
    assert engine.provider.records["ad_group_criterion"][negative]["status"] == "ENABLED"
    apply(engine, {"kind": "set_channel_group_targeting", "ad_group": group, "audience": ""})
    active = [
        r
        for r in engine.provider.records["ad_group_criterion"].values()
        if r["ad_group"] == group and r.get("status") != "REMOVED"
    ]
    assert any("language" in r for r in active)
    assert not any("audience" in r for r in active)


def test_display_targeting_preserves_other_restrictions_and_unchanged_criteria(engine):
    result = apply(engine, creation("DISPLAY"))
    group = receipt(result, "ad_group")[0]
    record = engine.provider.records["ad_group"][group]
    record["targeting_setting"] = {
        "target_restrictions": [
            {"targeting_dimension": "PLACEMENT", "bid_only": True},
            {"targeting_dimension": "AUDIENCE", "bid_only": True},
        ]
    }
    plan = preview(
        engine,
        {"kind": "set_channel_group_targeting", "ad_group": group, "user_lists": [USER_LIST]},
    )
    ops = plan["body"]["prepared"]["operations"]
    assert len(ops) == 1 and "ad_group_operation" in ops[0]
    result = engine.submit(plan["id"])
    assert result["status"] == "VERIFIED"
    assert record["targeting_setting"]["target_restrictions"] == [
        {"targeting_dimension": "PLACEMENT", "bid_only": True},
        {"targeting_dimension": "AUDIENCE", "bid_only": False},
    ]


def test_demand_gen_campaign_geo_rejected_and_new_group_requires_geo(engine):
    result = apply(engine, creation("DEMAND_GEN"))
    campaign = receipt(result, "campaign")[0]
    with pytest.raises(AdsError, match="set_channel_group_targeting"):
        preview(
            engine,
            {"kind": "set_targeting", "campaign": campaign, "patch": {"geo_target_ids": ["2840"]}},
        )
    with pytest.raises(AdsError, match="明确地区和语言"):
        preview(
            engine,
            {
                "kind": "create_channel_ad_group",
                "campaign": campaign,
                "group": creation("DEMAND_GEN")["ad_groups"][0],
            },
        )


def test_conversion_action_lifecycle_and_nested_patch_preservation(engine):
    result = apply(
        engine,
        {
            "kind": "create_conversion_action",
            "type": "WEBPAGE",
            "name": "Secondary signup",
            "category": "SIGNUP",
            "expected_campaigns": [CAMPAIGN],
        },
    )
    ref = receipt(result, "conversion_action")[0]
    assert engine.provider.records["conversion_action"][ref]["primary_for_goal"] is False
    apply(
        engine,
        {
            "kind": "update_conversion_action",
            "conversion_action": ACTION,
            "expected_campaigns": [CAMPAIGN],
            "patch": {
                "name": "Edited purchase",
                "primary_for_goal": False,
                "value_settings": {"default_value": 0},
            },
        },
    )
    values = engine.provider.records["conversion_action"][ACTION]["value_settings"]
    assert values == {
        "default_value": 0,
        "default_currency_code": "USD",
        "always_use_default_value": False,
    }
    apply(
        engine,
        {
            "kind": "remove_conversion_action",
            "conversion_action": ref,
            "expected_campaigns": [CAMPAIGN],
        },
    )


def test_conversion_goals_account_campaign_and_custom_lifecycle(engine):
    apply(
        engine,
        {
            "kind": "set_conversion_goals",
            "expected_campaigns": [CAMPAIGN],
            "goals": [{"category": "PURCHASE", "origin": "WEBSITE", "biddable": False}],
        },
    )
    assert engine.provider.records["customer_conversion_goal"][CUSTOMER_GOAL]["biddable"] is False
    result = apply(
        engine,
        {
            "kind": "create_custom_conversion_goal",
            "name": "Qualified purchases",
            "conversion_actions": [ACTION],
        },
    )
    custom = receipt(result, "custom_conversion_goal")[0]
    apply(
        engine,
        {
            "kind": "update_custom_conversion_goal",
            "custom_conversion_goal": custom,
            "expected_campaigns": [],
            "patch": {"name": "Updated custom goal"},
        },
    )
    apply(
        engine,
        {
            "kind": "set_conversion_goals",
            "campaign": CAMPAIGN,
            "expected_campaigns": [CAMPAIGN],
            "custom_conversion_goal": custom,
            "goals": [{"category": "PURCHASE", "origin": "WEBSITE", "biddable": True}],
        },
    )
    cfg = engine.provider.records["conversion_goal_campaign_config"][CONFIG]
    assert cfg["goal_config_level"] == "CAMPAIGN"
    with pytest.raises(AdsError, match="引用系列"):
        preview(
            engine,
            {
                "kind": "remove_custom_conversion_goal",
                "custom_conversion_goal": custom,
                "expected_campaigns": [CAMPAIGN],
            },
        )
    with pytest.raises(AdsError, match="自定义目标"):
        preview(
            engine,
            {
                "kind": "remove_conversion_action",
                "conversion_action": ACTION,
                "expected_campaigns": [CAMPAIGN],
            },
        )
    apply(
        engine,
        {
            "kind": "set_conversion_goals",
            "campaign": CAMPAIGN,
            "expected_campaigns": [CAMPAIGN],
            "goal_config_level": "CUSTOMER",
        },
    )
    apply(
        engine,
        {
            "kind": "remove_custom_conversion_goal",
            "custom_conversion_goal": custom,
            "expected_campaigns": [],
        },
    )


@pytest.mark.parametrize("reason", ["impact", "owner", "type", "missing_goal"])
def test_conversion_preconditions_fail_before_mutation(engine, reason):
    request = {
        "kind": "update_conversion_action",
        "conversion_action": ACTION,
        "expected_campaigns": [CAMPAIGN],
        "patch": {"name": "New name"},
    }
    if reason == "impact":
        request["expected_campaigns"] = []
    elif reason == "owner":
        engine.provider.metadata["conversion_tracking_setting"][
            "google_ads_conversion_customer"
        ] = "customers/9876543210"
    elif reason == "type":
        engine.provider.records["conversion_action"][ACTION]["type_"] = (
            "GOOGLE_ANALYTICS_4_PURCHASE"
        )
    else:
        request = {
            "kind": "set_conversion_goals",
            "expected_campaigns": [CAMPAIGN],
            "goals": [{"category": "SIGNUP", "origin": "WEBSITE", "biddable": True}],
        }
    with pytest.raises(AdsError):
        preview(engine, request)
    assert not engine.provider.calls


def test_conversion_remote_drift_blocks_submit(engine):
    plan = preview(
        engine,
        {
            "kind": "update_conversion_action",
            "conversion_action": ACTION,
            "expected_campaigns": [CAMPAIGN],
            "patch": {"primary_for_goal": False},
        },
    )
    engine.provider.records["conversion_action"][ACTION]["name"] = "Someone edited this"
    result = engine.submit(plan["id"])
    assert result["status"] == "STALE"
    assert all(validate for validate, _ in engine.provider.calls)


def test_new_writes_unknown_outcome_reconcile_without_resend(engine):
    plan = preview(
        engine,
        {
            "kind": "update_conversion_action",
            "conversion_action": ACTION,
            "expected_campaigns": [CAMPAIGN],
            "patch": {"primary_for_goal": False},
        },
    )
    engine.provider.failure = AdsError("TRANSPORT_UNKNOWN", "Simulated lost reply")
    engine.provider.apply_before_failure = True
    assert engine.submit(plan["id"])["status"] == "UNKNOWN"
    assert engine.reconcile(plan["id"])["status"] == "RECONCILED"
    assert len([c for c in engine.provider.calls if not c[0]]) == 1
    assert engine.submit(plan["id"])["status"] == "RECONCILED"


@pytest.mark.parametrize("channel", ["DISPLAY", "DEMAND_GEN", "APP"])
def test_channel_creation_lost_reply_reconciles_without_duplicate(engine, channel):
    plan = preview(engine, creation(channel))
    engine.provider.failure = AdsError("TRANSPORT_UNKNOWN", "Lost reply after create")
    engine.provider.apply_before_failure = True
    assert engine.submit(plan["id"])["status"] == "UNKNOWN"
    assert engine.reconcile(plan["id"])["status"] == "RECONCILED"
    assert engine.submit(plan["id"])["status"] == "RECONCILED"
    assert len([c for c in engine.provider.calls if not c[0]]) == 1


def test_app_goal_change_checks_app_identity_and_preserves_store(engine):
    result = apply(engine, creation("APP"))
    campaign = receipt(result, "campaign")[0]
    engine.provider.records["conversion_action"][ACTION].update(
        type_="FIREBASE_ANDROID_CUSTOM", origin="APP", app_id="com.example.app"
    )
    goal = {
        "app_id": "com.example.app",
        "app_store": "GOOGLE_APP_STORE",
        "goal": "OPTIMIZE_RETURN_ON_ADVERTISING_SPEND",
        "conversion_actions": [ACTION],
    }
    apply(
        engine,
        {
            "kind": "set_channel_bidding",
            "campaign": campaign,
            "bidding": {"strategy": "TARGET_ROAS", "target_roas": 2.5},
            "app_goal": goal,
        },
    )
    saved = engine.provider.records["campaign"][campaign]
    assert saved["app_campaign_setting"]["app_id"] == "com.example.app"
    assert saved["target_roas"]["target_roas"] == 2.5
    engine.provider.records["conversion_action"][ACTION]["app_id"] = "com.other.app"
    before = len(engine.provider.calls)
    with pytest.raises(AdsError, match="该应用"):
        preview(
            engine,
            {
                "kind": "set_channel_bidding",
                "campaign": campaign,
                "bidding": {"strategy": "TARGET_ROAS", "target_roas": 3},
                "app_goal": goal,
            },
        )
    assert len(engine.provider.calls) == before


def test_channel_bidding_obeys_existing_budget_limit(engine):
    result = apply(engine, creation("DISPLAY"))
    campaign, budget = receipt(result, "campaign")[0], receipt(result, "campaign_budget")[0]
    engine.provider.records["campaign_budget"][budget]["amount_micros"] = 1_000_000_000
    before = len(engine.provider.calls)
    with pytest.raises(AdsError) as error:
        preview(
            engine,
            {
                "kind": "set_channel_bidding",
                "campaign": campaign,
                "bidding": {"strategy": "TARGET_CPA", "target_cpa": "1"},
            },
        )
    assert error.value.code == "BUDGET_LIMIT"
    assert len(engine.provider.calls) == before


@pytest.mark.parametrize(
    "case", ["wrong_channel", "wrong_asset", "app_id_change", "smart_cpc", "bad_ratio"]
)
def test_channel_invalid_combinations(engine, case):
    if case in {"wrong_channel", "wrong_asset", "bad_ratio"}:
        request = creation("DISPLAY")
        if case == "wrong_channel":
            request["ad_groups"][0]["ads"][0] = content("APP")
        elif case == "wrong_asset":
            request["ad_groups"][0]["ads"][0]["marketing_images"] = [VIDEO]
        else:
            request["ad_groups"][0]["ads"][0]["marketing_images"] = [SQUARE]
    else:
        result = apply(engine, creation("APP" if case == "app_id_change" else "DEMAND_GEN"))
        request = {
            "kind": "update_channel_ad_group",
            "ad_group": receipt(result, "ad_group")[0],
            "patch": {"cpc_bid": "1"},
        }
        if case == "app_id_change":
            request = {
                "kind": "set_channel_bidding",
                "campaign": receipt(result, "campaign")[0],
                "bidding": {"strategy": "TARGET_CPA", "target_cpa": "1"},
                "app_goal": {
                    "app_id": "com.different.app",
                    "app_store": "GOOGLE_APP_STORE",
                    "goal": "OPTIMIZE_INSTALLS_TARGET_INSTALL_COST",
                },
            }
    before = len(engine.provider.calls)
    with pytest.raises(AdsError):
        preview(engine, request)
    assert len(engine.provider.calls) == before


@pytest.mark.parametrize(
    "change",
    [
        {
            "kind": "set_conversion_goals",
            "campaign": CAMPAIGN,
            "expected_campaigns": [CAMPAIGN],
            "goal_config_level": "CUSTOMER",
            "goals": [{"category": "PURCHASE", "origin": "WEBSITE", "biddable": True}],
        },
        {
            "kind": "update_conversion_action",
            "conversion_action": ACTION,
            "expected_campaigns": [CAMPAIGN],
            "patch": {"primary_for_goal": None},
        },
        {
            "kind": "create_custom_conversion_goal",
            "name": "Wrong account",
            "conversion_actions": ["customers/9999999999/conversionActions/1"],
        },
    ],
)
def test_strict_contracts(change):
    with pytest.raises(ValidationError):
        PlanInput(account=ACCOUNT, request_key=uuid4().hex, change=change)
