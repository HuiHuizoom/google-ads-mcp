"""Offline lifecycle tests. SDK wire validation is real; provider state is simulated."""

from copy import deepcopy
from uuid import uuid4

import pytest
from pydantic import ValidationError

from team_ads_mcp.engine import Engine
from team_ads_mcp.errors import AdsError
from team_ads_mcp.models import PlanInput

from .fakes import ACCOUNT, CAMPAIGN, GROUP, ROOT, FakeProvider, settings

AD = ROOT + "/adGroupAds/31~41"
KEYWORD = ROOT + "/adGroupCriteria/31~51"
DEVICE = ROOT + "/campaignCriteria/11~61"
CONTENT = {
    "final_urls": ["https://example.com"],
    "headlines": ["Team Tools", "Work Together", "Try Our Product"],
    "descriptions": ["Build your team's workflow.", "Start your next project here."],
    "path1": "tools",
    "path2": "teams",
}


@pytest.fixture
def engine(tmp_path):
    provider = FakeProvider()
    provider.records["ad_group_ad"][AD] = {
        "resource_name": AD,
        "ad_group": GROUP,
        "status": "PAUSED",
        "ad": {
            "resource_name": ROOT + "/ads/41",
            "type_": "RESPONSIVE_SEARCH_AD",
            "final_urls": CONTENT["final_urls"],
            "responsive_search_ad": {
                "headlines": [
                    {"text": t, "pinned_field": "HEADLINE_1" if i == 0 else "UNSPECIFIED"}
                    for i, t in enumerate(CONTENT["headlines"])
                ],
                "descriptions": [{"text": t} for t in CONTENT["descriptions"]],
                "path1": "tools",
                "path2": "teams",
            },
        },
    }
    provider.records["ad_group_criterion"][KEYWORD] = {
        "resource_name": KEYWORD,
        "ad_group": GROUP,
        "type_": "KEYWORD",
        "status": "PAUSED",
        "negative": False,
        "keyword": {"text": "team tools", "match_type": "EXACT"},
        "cpc_bid_micros": 800_000,
        "final_urls": ["https://example.com/keyword"],
        "tracking_url_template": "{lpurl}?source=search",
        "labels": [ROOT + "/labels/91"],
    }
    provider.records["campaign_criterion"][DEVICE] = {
        "resource_name": DEVICE,
        "campaign": CAMPAIGN,
        "type_": "DEVICE",
        "status": "ENABLED",
        "device": {"type_": "MOBILE"},
        "bid_modifier": 1.0,
    }
    campaign = provider.records["campaign"][CAMPAIGN]
    campaign.update(
        {
            "manual_cpc": {"enhanced_cpc_enabled": False},
            "start_date_time": "2020-01-01 00:00:00",
            "end_date_time": "2037-12-30 23:59:59",
            "network_settings": {
                "target_google_search": True,
                "target_search_network": False,
                "target_content_network": False,
            },
            "geo_target_type_setting": {
                "positive_geo_target_type": "PRESENCE",
                "negative_geo_target_type": "PRESENCE",
            },
        }
    )
    return Engine(tmp_path, settings(), provider)


def preview(engine, change):
    return engine.preview(PlanInput(account=ACCOUNT, request_key=uuid4().hex, change=change))


def apply(engine, change):
    plan = preview(engine, change)
    result = engine.submit(plan["id"])
    assert result["status"] == "VERIFIED", result.get("result")
    assert result["result"]["readback"]["complete"]
    return result


def test_existing_ad_partial_update_preserves_copy_pins_and_status(engine):
    original = deepcopy(engine.provider.records["ad_group_ad"][AD])
    result = apply(
        engine,
        {
            "kind": "update_ad",
            "ad_group_ad": AD,
            "expected_ad_group_ads": [AD],
            "patch": {"final_urls": ["https://example.com/new"]},
        },
    )
    current = engine.provider.records["ad_group_ad"][AD]
    assert current["ad"]["responsive_search_ad"] == original["ad"]["responsive_search_ad"]
    assert current["status"] == "PAUSED"
    op = result["body"]["prepared"]["operations"][0]["ad_operation"]
    assert op["update_mask"]["paths"] == ["final_urls"]
    apply(
        engine,
        {
            "kind": "update_ad",
            "ad_group_ad": AD,
            "expected_ad_group_ads": [AD],
            "patch": {
                "headlines": ["New Headline", "Another Headline", "A Third Headline"],
                "path2": "",
            },
        },
    )
    assert current["ad"]["responsive_search_ad"]["path1"] == "tools"
    assert current["ad"]["responsive_search_ad"]["path2"] == ""
    assert (
        current["ad"]["responsive_search_ad"]["descriptions"]
        == original["ad"]["responsive_search_ad"]["descriptions"]
    )


def test_shared_ad_requires_complete_impact_and_binds_all_links(engine):
    other_group, other_ad = ROOT + "/adGroups/32", ROOT + "/adGroupAds/32~41"
    engine.provider.records["ad_group"][other_group] = {
        **engine.provider.records["ad_group"][GROUP],
        "resource_name": other_group,
    }
    engine.provider.records["ad_group_ad"][other_ad] = {
        **deepcopy(engine.provider.records["ad_group_ad"][AD]),
        "resource_name": other_ad,
        "ad_group": other_group,
    }
    change = {
        "kind": "update_ad",
        "ad_group_ad": AD,
        "expected_ad_group_ads": [AD],
        "patch": {"path2": "new"},
    }
    with pytest.raises(AdsError) as exc:
        preview(engine, change)
    assert exc.value.code == "AD_IMPACT_MISMATCH"
    change["expected_ad_group_ads"].append(other_ad)
    apply(engine, change)
    assert (
        engine.provider.records["ad_group_ad"][other_ad]["ad"]["responsive_search_ad"]["path2"]
        == "new"
    )


def test_add_group_then_later_update_and_add_ad(engine):
    apply(
        engine,
        {
            "kind": "create_ad_group",
            "campaign": CAMPAIGN,
            "name": "New group",
            "currency": "USD",
            "cpc_bid": "1.20",
        },
    )
    new_group = next(k for k in engine.provider.records["ad_group"] if k != GROUP)
    apply(
        engine,
        {
            "kind": "update_ad_group",
            "ad_group": new_group,
            "currency": "USD",
            "patch": {"name": "Renamed", "cpc_bid": "1.50"},
        },
    )
    assert engine.provider.records["ad_group"][new_group]["cpc_bid_micros"] == 1_500_000
    assert engine.provider.records["ad_group"][new_group]["status"] == "PAUSED"
    apply(engine, {"kind": "create_ad", "ad_group": new_group, "ad": CONTENT})
    assert len(engine.provider.records["ad_group_ad"]) == 2
    with pytest.raises(AdsError) as exc:
        preview(engine, {"kind": "create_ad", "ad_group": new_group, "ad": CONTENT})
    assert exc.value.code == "AD_EXISTS"


def test_add_complete_group_atomic(engine):
    apply(
        engine,
        {
            "kind": "create_ad_group",
            "campaign": CAMPAIGN,
            "name": "Complete",
            "currency": "USD",
            "cpc_bid": "1",
            "ads": [CONTENT],
            "keywords": [{"text": "new", "match_type": "BROAD"}],
        },
    )
    assert len([call for call in engine.provider.calls if not call[0]]) == 1


def test_keyword_edit_clear_and_replace_preserves_configuration(engine):
    apply(
        engine,
        {
            "kind": "update_keyword",
            "resource": KEYWORD,
            "currency": "USD",
            "patch": {"status": "ENABLED", "cpc_bid": "1.25"},
        },
    )
    apply(
        engine,
        {
            "kind": "replace_keyword",
            "resource": KEYWORD,
            "keyword": {"text": "team software", "match_type": "PHRASE"},
        },
    )
    assert engine.provider.records["ad_group_criterion"][KEYWORD]["status"] == "REMOVED"
    new_name = next(k for k in engine.provider.records["ad_group_criterion"] if k != KEYWORD)
    new = engine.provider.records["ad_group_criterion"][new_name]
    assert new["labels"] == [ROOT + "/labels/91"]
    assert new["cpc_bid_micros"] == "1250000"
    assert new["tracking_url_template"] == "{lpurl}?source=search"
    assert new["final_urls"] == ["https://example.com/keyword"]
    apply(
        engine,
        {
            "kind": "update_keyword",
            "resource": new_name,
            "currency": "USD",
            "patch": {"clear_cpc_bid": True, "final_urls": []},
        },
    )
    assert "cpc_bid_micros" not in new
    assert new["final_urls"] == []


def test_group_negative_lifecycle_and_no_pause(engine):
    apply(
        engine,
        {
            "kind": "add_ad_group_negatives",
            "ad_group": GROUP,
            "keywords": [{"text": "free", "match_type": "PHRASE"}],
        },
    )
    name = next(k for k in engine.provider.records["ad_group_criterion"] if k != KEYWORD)
    with pytest.raises(AdsError) as exc:
        preview(
            engine,
            {
                "kind": "update_keyword",
                "resource": name,
                "currency": "USD",
                "patch": {"status": "PAUSED"},
            },
        )
    assert exc.value.code == "NEGATIVE_KEYWORD_IMMUTABLE"
    apply(
        engine,
        {
            "kind": "replace_keyword",
            "resource": name,
            "keyword": {"text": "free tools", "match_type": "BROAD"},
        },
    )
    new = next(k for k in engine.provider.records["ad_group_criterion"] if k not in {KEYWORD, name})
    apply(engine, {"kind": "remove_keywords", "resources": [new]})


def test_campaign_patch_preserves_other_settings(engine):
    apply(
        engine,
        {
            "kind": "update_campaign",
            "campaign": CAMPAIGN,
            "patch": {
                "name": "Changed",
                "search_partners": True,
                "end_date_time": "2030-12-31 23:59:59",
                "positive_geo_target_type": "PRESENCE_OR_INTEREST",
                "final_url_suffix": "utm_source=google",
            },
        },
    )
    current = engine.provider.records["campaign"][CAMPAIGN]
    assert current["network_settings"] == {
        "target_google_search": True,
        "target_search_network": True,
        "target_content_network": False,
    }
    assert current["geo_target_type_setting"]["negative_geo_target_type"] == "PRESENCE"
    assert current["status"] == "PAUSED"
    with pytest.raises(AdsError) as exc:
        preview(
            engine,
            {
                "kind": "update_campaign",
                "campaign": CAMPAIGN,
                "patch": {"start_date_time": "2030-01-01 00:00:00"},
            },
        )
    assert exc.value.code == "START_ALREADY_PASSED"


@pytest.mark.parametrize(
    "strategy,extra",
    [
        ("MANUAL_CPC", {}),
        ("MAXIMIZE_CLICKS", {}),
        ("MAXIMIZE_CLICKS", {"max_cpc": "2"}),
        ("MAXIMIZE_CONVERSIONS", {}),
        ("MAXIMIZE_CONVERSIONS", {"target_cpa": "5"}),
        ("MAXIMIZE_CONVERSION_VALUE", {}),
        ("MAXIMIZE_CONVERSION_VALUE", {"target_roas": 2.5}),
    ],
)
def test_strategy_switch_and_clear_optional_target(engine, strategy, extra):
    engine.provider.records["campaign"][CAMPAIGN]["bidding_strategy"] = (
        ROOT + "/biddingStrategies/100"
    )
    engine.provider.records["campaign"][CAMPAIGN].pop("manual_cpc")
    apply(
        engine,
        {
            "kind": "set_bidding",
            "campaign": CAMPAIGN,
            "currency": "USD",
            "strategy": strategy,
            **extra,
        },
    )
    assert not engine.provider.records["campaign"][CAMPAIGN].get("bidding_strategy")
    if extra:
        apply(
            engine,
            {"kind": "set_bidding", "campaign": CAMPAIGN, "currency": "USD", "strategy": strategy},
        )


def test_smart_bidding_rejects_manual_bid_but_allows_new_group(engine):
    apply(
        engine,
        {
            "kind": "set_bidding",
            "campaign": CAMPAIGN,
            "currency": "USD",
            "strategy": "MAXIMIZE_CONVERSIONS",
        },
    )
    with pytest.raises(AdsError) as exc:
        preview(
            engine,
            {
                "kind": "update_ad_group",
                "ad_group": GROUP,
                "currency": "USD",
                "patch": {"cpc_bid": "2"},
            },
        )
    assert exc.value.code == "MANUAL_CPC_REQUIRED"
    apply(
        engine,
        {
            "kind": "create_ad_group",
            "campaign": CAMPAIGN,
            "name": "Auto group",
            "currency": "USD",
            "ads": [CONTENT],
        },
    )


def test_targeting_replace_dimension_and_clear_preserves_other_criteria(engine):
    apply(
        engine,
        {
            "kind": "set_targeting",
            "campaign": CAMPAIGN,
            "patch": {
                "geo_target_ids": ["2840", "2124"],
                "excluded_geo_target_ids": ["21167"],
                "language_ids": ["1000"],
            },
        },
    )
    records = engine.provider.records["campaign_criterion"]
    kept = next(
        n
        for n, r in records.items()
        if r.get("location", {}).get("geo_target_constant") == "geoTargetConstants/2840"
    )
    records[kept]["bid_modifier"] = 1.5
    apply(
        engine,
        {
            "kind": "set_targeting",
            "campaign": CAMPAIGN,
            "patch": {"geo_target_ids": ["2840", "2826"]},
        },
    )
    assert records[kept]["status"] == "ENABLED" and records[kept]["bid_modifier"] == 1.5
    assert records[DEVICE]["bid_modifier"] == 1.0
    assert any(r.get("language") and r["status"] == "ENABLED" for r in records.values())
    assert any(r.get("negative") and r["status"] == "ENABLED" for r in records.values())
    apply(engine, {"kind": "set_targeting", "campaign": CAMPAIGN, "patch": {"language_ids": []}})
    assert not any(r.get("language") and r["status"] == "ENABLED" for r in records.values())
    with pytest.raises(AdsError) as exc:
        preview(
            engine,
            {"kind": "set_targeting", "campaign": CAMPAIGN, "patch": {"geo_target_ids": ["21167"]}},
        )
    assert exc.value.code == "TARGET_CONFLICT"


def test_weekly_schedule_add_change_and_clear(engine):
    apply(
        engine,
        {
            "kind": "set_schedule",
            "campaign": CAMPAIGN,
            "schedules": [{"day": "MONDAY", "start": "00:00", "end": "24:00"}],
        },
    )
    apply(
        engine,
        {
            "kind": "set_schedule",
            "campaign": CAMPAIGN,
            "schedules": [{"day": "TUESDAY", "start": "09:15", "end": "17:45"}],
        },
    )
    apply(engine, {"kind": "set_schedule", "campaign": CAMPAIGN, "schedules": []})
    assert engine.provider.records["campaign_criterion"][DEVICE]["status"] == "ENABLED"


def test_device_bid_updates_and_excludes(engine):
    apply(engine, {"kind": "set_device_bid", "resource": DEVICE, "bid_modifier": 1.5})
    apply(engine, {"kind": "set_device_bid", "resource": DEVICE, "bid_modifier": 0})
    assert engine.provider.records["campaign_criterion"][DEVICE]["bid_modifier"] == 0


@pytest.mark.parametrize("resource", [CAMPAIGN, GROUP, AD])
def test_remove_existing_resource_explains_effect(engine, resource):
    result = apply(engine, {"kind": "remove_resource", "resource": resource})
    assert any(
        "移除后不能重新启用" in warning for warning in result["body"]["prepared"]["warnings"]
    )


@pytest.mark.parametrize(
    "kind,first,second",
    [
        (
            "SITELINK",
            {"link_text": "Features", "final_urls": ["https://example.com/features"]},
            {
                "link_text": "Pricing",
                "final_urls": ["https://example.com/pricing"],
                "description1": "See our plans",
                "description2": "Choose your plan",
            },
        ),
        ("CALLOUT", {"text": "Free trial"}, {"text": "Team collaboration"}),
        (
            "STRUCTURED_SNIPPET",
            {"header": "Types", "values": ["Teams", "Projects", "Tasks"]},
            {"header": "Types", "values": ["Teams", "Workflows", "Tasks"]},
        ),
    ],
)
def test_asset_add_modify_detach_without_global_mutation(engine, kind, first, second):
    def change(items):
        return {
            "kind": "set_campaign_assets",
            "campaign": CAMPAIGN,
            "field_type": kind,
            "assets": [{"type": kind, **a} for a in items],
        }

    apply(engine, change([first]))
    original_assets = deepcopy(engine.provider.records["asset"])
    with pytest.raises(AdsError) as exc:
        preview(engine, change([first]))
    assert exc.value.code == "NO_CHANGES"
    apply(engine, change([first, second]))
    apply(engine, change([second]))
    assert all(
        engine.provider.records["asset"][key] == value for key, value in original_assets.items()
    )
    apply(engine, change([]))
    assert len(engine.provider.records["asset"]) == 2
    assert all(r["status"] == "REMOVED" for r in engine.provider.records["campaign_asset"].values())


def test_changed_post_creation_snapshot_blocks_submit(engine):
    plan = preview(
        engine,
        {
            "kind": "update_ad",
            "ad_group_ad": AD,
            "expected_ad_group_ads": [AD],
            "patch": {"path2": "new"},
        },
    )
    engine.provider.records["ad_group_ad"][AD]["ad"]["final_urls"] = [
        "https://example.com/changed-elsewhere"
    ]
    assert engine.submit(plan["id"])["status"] == "STALE"
    assert not any(not v for v, _ in engine.provider.calls)


def test_readback_ignores_unselected_proto_defaults(engine):
    # Protobuf serializes defaults for unselected enums; selecting those defaults again
    # would compare a made-up UNSPECIFIED with the real Google serving status.
    engine.provider.records["campaign"][CAMPAIGN]["serving_status"] = "SERVING"
    result = apply(
        engine, {"kind": "update_campaign", "campaign": CAMPAIGN, "patch": {"name": "Rename only"}}
    )
    fields = result["body"]["prepared"]["checks"][0]["fields"]
    assert "campaign.serving_status" not in fields
    assert engine.provider.records["campaign"][CAMPAIGN]["serving_status"] == "SERVING"


def test_optional_scalar_absent_after_clear_is_verified(engine, monkeypatch):
    original = engine.provider.mutate

    def mutate(account, operations, validate_only):
        response = original(account, operations, validate_only)
        if not validate_only:
            engine.provider.records["ad_group_ad"][AD]["ad"]["responsive_search_ad"].pop("path2")
        return response

    monkeypatch.setattr(engine.provider, "mutate", mutate)
    apply(
        engine,
        {
            "kind": "update_ad",
            "ad_group_ad": AD,
            "expected_ad_group_ads": [AD],
            "patch": {"path2": ""},
        },
    )


def test_clear_does_not_hide_a_nonempty_remote_value(engine, monkeypatch):
    original = engine.provider.mutate

    def mutate(account, operations, validate_only):
        response = original(account, operations, validate_only)
        if not validate_only:
            engine.provider.records["ad_group_ad"][AD]["ad"]["responsive_search_ad"]["path2"] = (
                "unexpected"
            )
        return response

    monkeypatch.setattr(engine.provider, "mutate", mutate)
    plan = preview(
        engine,
        {
            "kind": "update_ad",
            "ad_group_ad": AD,
            "expected_ad_group_ads": [AD],
            "patch": {"path2": ""},
        },
    )
    assert engine.submit(plan["id"])["status"] == "READBACK_MISMATCH"


def test_post_creation_removal_submits_from_preview(engine):
    plan = preview(engine, {"kind": "remove_resource", "resource": CAMPAIGN})
    assert engine.submit(plan["id"])["status"] == "VERIFIED"
    assert engine.provider.records["campaign"][CAMPAIGN]["status"] == "REMOVED"


@pytest.mark.parametrize(
    "change",
    [
        {
            "kind": "create_ad",
            "ad_group": GROUP,
            "ad": {**CONTENT, "final_urls": ["https://example.com/new"]},
        },
        {
            "kind": "replace_keyword",
            "resource": KEYWORD,
            "keyword": {"text": "new term", "match_type": "PHRASE"},
        },
        {
            "kind": "set_campaign_assets",
            "campaign": CAMPAIGN,
            "field_type": "CALLOUT",
            "assets": [{"type": "CALLOUT", "text": "Free trial"}],
        },
    ],
)
def test_ambiguous_new_write_only_reconciles_never_resends(engine, change):
    plan = preview(engine, change)
    engine.provider.failure = TimeoutError("unknown receipt")
    engine.provider.apply_before_failure = True
    assert engine.submit(plan["id"])["status"] == "UNKNOWN"
    assert engine.submit(plan["id"])["status"] == "UNKNOWN"
    engine.provider.failure = None
    assert engine.reconcile(plan["id"])["status"] == "RECONCILED"
    assert len([call for call in engine.provider.calls if not call[0]]) == 1


@pytest.mark.parametrize(
    "change",
    [
        {"kind": "update_campaign", "campaign": CAMPAIGN, "patch": {}},
        {"kind": "update_campaign", "campaign": CAMPAIGN, "patch": {"name": None}},
        {"kind": "update_campaign", "campaign": CAMPAIGN, "patch": {"status": "ENABLED"}},
        {
            "kind": "update_ad",
            "ad_group_ad": AD,
            "expected_ad_group_ads": [AD],
            "patch": {"final_urls": []},
        },
        {
            "kind": "update_ad",
            "ad_group_ad": AD,
            "expected_ad_group_ads": [AD],
            "patch": {"headlines": ["too few"]},
        },
        {
            "kind": "update_keyword",
            "resource": KEYWORD,
            "currency": "USD",
            "patch": {"cpc_bid": "1", "clear_cpc_bid": True},
        },
        {
            "kind": "set_bidding",
            "campaign": CAMPAIGN,
            "currency": "USD",
            "strategy": "MANUAL_CPC",
            "target_roas": 2,
        },
        {
            "kind": "set_targeting",
            "campaign": CAMPAIGN,
            "patch": {"geo_target_ids": ["2840"], "excluded_geo_target_ids": ["2840"]},
        },
        {
            "kind": "set_schedule",
            "campaign": CAMPAIGN,
            "schedules": [{"day": "MONDAY", "start": "09:10", "end": "10:00"}],
        },
        {
            "kind": "set_schedule",
            "campaign": CAMPAIGN,
            "schedules": [
                {"day": "MONDAY", "start": "09:00", "end": "12:00"},
                {"day": "MONDAY", "start": "11:00", "end": "13:00"},
            ],
        },
        {"kind": "set_device_bid", "resource": DEVICE, "bid_modifier": 0.01},
        {
            "kind": "create_ad_group",
            "campaign": "customers/9999999999/campaigns/11",
            "name": "Wrong",
            "currency": "USD",
        },
    ],
)
def test_invalid_intent_rejected_without_provider_write(change):
    with pytest.raises(ValidationError):
        PlanInput(account=ACCOUNT, request_key="invalid-input-123", change=change)
