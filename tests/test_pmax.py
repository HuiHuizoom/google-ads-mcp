import json

import pytest
from PIL import Image
from pydantic import ValidationError

from team_ads_mcp.engine import Engine
from team_ads_mcp.errors import AdsError
from team_ads_mcp.media import image_payload, public_payload
from team_ads_mcp.models import PlanInput
from team_ads_mcp.server import create_server

from .fakes import ACCOUNT, ROOT, FakeProvider, settings


@pytest.fixture
def pmax(tmp_path):
    provider = FakeProvider()
    refs = {}
    for index, (field, texts) in enumerate(
        [
            ("HEADLINE", ["Example Product", "Explore Features", "Discover Our Service"]),
            ("LONG_HEADLINE", ["Explore the complete range of tools for your team"]),
            ("DESCRIPTION", ["Find features for your team.", "Learn about available products."]),
            ("BUSINESS_NAME", ["Example"]),
        ]
    ):
        refs[field] = []
        for offset, text in enumerate(texts):
            ref = ROOT + f"/assets/{1000 + index * 10 + offset}"
            provider.records["asset"][ref] = {
                "resource_name": ref,
                "type_": "TEXT",
                "text_asset": {"text": text},
            }
            refs[field].append(ref)
    for index, (field, size) in enumerate(
        [
            ("MARKETING_IMAGE", (1200, 628)),
            ("SQUARE_MARKETING_IMAGE", (1200, 1200)),
            ("LOGO", (128, 128)),
        ]
    ):
        ref = ROOT + f"/assets/{2000 + index}"
        provider.records["asset"][ref] = {
            "resource_name": ref,
            "type_": "IMAGE",
            "image_asset": {
                "file_size": 1000,
                "full_size": {"width_pixels": size[0], "height_pixels": size[1]},
            },
        }
        refs[field] = [ref]
    engine = Engine(tmp_path, settings(), provider)
    return engine, refs


def links(refs, fields):
    return [{"field_type": f, "asset": a} for f in fields for a in refs[f]]


def create_input(refs):
    return {
        "kind": "create_pmax",
        "name": "PMax test",
        "currency": "USD",
        "daily_amount": "5",
        "bidding": "MAXIMIZE_CONVERSIONS",
        "geo_target_ids": ["2840"],
        "language_ids": ["1000"],
        "eu_political_advertising": "DOES_NOT_CONTAIN_EU_POLITICAL_ADVERTISING",
        "brand_assets": links(refs, ["BUSINESS_NAME", "LOGO"]),
        "asset_groups": [
            {
                "name": "Group",
                "final_urls": ["https://example.com/"],
                "assets": links(
                    refs,
                    [
                        "HEADLINE",
                        "LONG_HEADLINE",
                        "DESCRIPTION",
                        "MARKETING_IMAGE",
                        "SQUARE_MARKETING_IMAGE",
                    ],
                ),
            }
        ],
    }


def preview(engine, change, key="pmax-create-001"):
    return engine.preview(PlanInput(account=ACCOUNT, change=change, request_key=key))


def execute(engine, change, key):
    plan = preview(engine, change, key)
    result = engine.submit(plan["id"])
    assert result["status"] == "VERIFIED", result["result"]
    return result


def test_pmax_complete_create_modify_signals_assets_and_remove(pmax):
    engine, refs = pmax
    execute(engine, create_input(refs), "create-pmax-001")
    provider = engine.provider
    campaign = next(
        r
        for r in provider.records["campaign"].values()
        if r["advertising_channel_type"] == "PERFORMANCE_MAX"
    )
    group = next(iter(provider.records["asset_group"].values()))
    assert campaign["status"] == group["status"] == "PAUSED"
    assert campaign["brand_guidelines_enabled"]
    assert all(
        a["field_type"] not in {"LOGO", "BUSINESS_NAME"}
        for a in provider.records["asset_group_asset"].values()
    )
    execute(
        engine,
        {
            "kind": "update_asset_group",
            "asset_group": group["resource_name"],
            "patch": {
                "name": "Updated",
                "final_urls": ["https://example.com/new"],
                "path1": "features",
            },
        },
        "update-group-001",
    )
    execute(
        engine,
        {
            "kind": "update_asset_group",
            "asset_group": group["resource_name"],
            "patch": {"path1": ""},
        },
        "clear-path-001",
    )
    audience = ROOT + "/audiences/555"
    provider.records["audience"][audience] = {
        "resource_name": audience,
        "name": "Existing",
        "scope": "CUSTOMER",
        "status": "ENABLED",
    }
    execute(
        engine,
        {
            "kind": "set_asset_group_signals",
            "asset_group": group["resource_name"],
            "audiences": [audience],
            "search_themes": ["team tools"],
        },
        "signals-add-001",
    )
    execute(
        engine,
        {
            "kind": "set_asset_group_signals",
            "asset_group": group["resource_name"],
            "search_themes": ["team software"],
        },
        "signals-replace-001",
    )
    assert any(r.get("audience") for r in provider.records["asset_group_signal"].values())
    execute(
        engine,
        {
            "kind": "set_asset_group_signals",
            "asset_group": group["resource_name"],
            "audiences": [],
            "search_themes": [],
        },
        "signals-clear-001",
    )
    assert not provider.records["asset_group_signal"]
    extra = ROOT + "/assets/3000"
    provider.records["asset"][extra] = {
        "resource_name": extra,
        "type_": "TEXT",
        "text_asset": {"text": "Another Headline"},
    }
    execute(
        engine,
        {
            "kind": "set_asset_group_assets",
            "asset_group": group["resource_name"],
            "assets_by_field": {"HEADLINE": [*refs["HEADLINE"], extra]},
        },
        "assets-add-001",
    )
    execute(
        engine,
        {
            "kind": "set_asset_group_assets",
            "asset_group": group["resource_name"],
            "assets_by_field": {"HEADLINE": refs["HEADLINE"]},
        },
        "assets-remove-001",
    )
    assert extra in provider.records["asset"]
    execute(
        engine,
        {
            "kind": "set_bidding",
            "campaign": campaign["resource_name"],
            "currency": "USD",
            "strategy": "MAXIMIZE_CONVERSION_VALUE",
            "target_roas": 2,
        },
        "bidding-pmax-001",
    )
    execute(
        engine,
        {
            "kind": "update_campaign",
            "campaign": campaign["resource_name"],
            "patch": {"name": "PMax updated"},
        },
        "campaign-edit-001",
    )
    execute(
        engine,
        {"kind": "set_status", "resource": group["resource_name"], "status": "ENABLED"},
        "group-enable-001",
    )
    execute(
        engine,
        {"kind": "set_status", "resource": group["resource_name"], "status": "PAUSED"},
        "group-pause-001",
    )
    execute(
        engine, {"kind": "remove_resource", "resource": group["resource_name"]}, "group-remove-001"
    )
    execute(
        engine,
        {"kind": "remove_resource", "resource": campaign["resource_name"]},
        "campaign-remove-001",
    )


def test_create_second_group_and_replace_brand_assets(pmax):
    engine, refs = pmax
    execute(engine, create_input(refs), "pmax-create-brand")
    campaign = next(
        r["resource_name"]
        for r in engine.provider.records["campaign"].values()
        if r["advertising_channel_type"] == "PERFORMANCE_MAX"
    )
    content = create_input(refs)["asset_groups"][0]
    content["name"] = "Second"
    execute(
        engine,
        {"kind": "create_asset_group", "campaign": campaign, "group": content},
        "pmax-second-group",
    )
    new_name = ROOT + "/assets/4000"
    engine.provider.records["asset"][new_name] = {
        "resource_name": new_name,
        "type_": "TEXT",
        "text_asset": {"text": "New Brand"},
    }
    execute(
        engine,
        {
            "kind": "set_pmax_brand_assets",
            "campaign": campaign,
            "assets": [
                {"asset": new_name, "field_type": "BUSINESS_NAME"},
                {"asset": refs["LOGO"][0], "field_type": "LOGO"},
            ],
        },
        "replace-brand-001",
    )
    assert len(engine.provider.records["asset_group"]) == 2


@pytest.mark.parametrize(
    "mode,code",
    [
        ("minimum", "ASSET_COUNT_INVALID"),
        ("wrong_type", "ASSET_TYPE_MISMATCH"),
        ("wrong_size", "ASSET_DIMENSIONS_INVALID"),
        ("brand", "BRAND_ASSETS_REQUIRED"),
    ],
)
def test_invalid_pmax_is_rejected_before_google_mutate(pmax, mode, code):
    engine, refs = pmax
    data = create_input(refs)
    if mode == "minimum":
        data["asset_groups"][0]["assets"] = data["asset_groups"][0]["assets"][1:]
    elif mode == "wrong_type":
        data["asset_groups"][0]["assets"][0]["asset"] = refs["LOGO"][0]
    elif mode == "wrong_size":
        engine.provider.records["asset"][refs["MARKETING_IMAGE"][0]]["image_asset"]["full_size"][
            "width_pixels"
        ] = 300
    else:
        data["brand_assets"] = [data["brand_assets"][0], data["brand_assets"][0]]
        code = "DUPLICATE_ASSET_LINK"
    with pytest.raises(AdsError) as exc:
        preview(engine, data)
    assert exc.value.code == code
    assert not engine.provider.calls


def test_pmax_rejects_invalid_bidding_network_and_asset_removal(pmax):
    engine, refs = pmax
    execute(engine, create_input(refs), "create-pmax-guards")
    group = next(iter(engine.provider.records["asset_group"].values()))
    for change, code in [
        (
            {
                "kind": "set_bidding",
                "campaign": group["campaign"],
                "currency": "USD",
                "strategy": "MANUAL_CPC",
            },
            "PMAX_BIDDING_REQUIRED",
        ),
        (
            {
                "kind": "update_campaign",
                "campaign": group["campaign"],
                "patch": {"search_partners": True},
            },
            "PMAX_NETWORK_MANAGED",
        ),
        (
            {
                "kind": "set_asset_group_assets",
                "asset_group": group["resource_name"],
                "assets_by_field": {"HEADLINE": []},
            },
            "ASSET_COUNT_INVALID",
        ),
    ]:
        with pytest.raises(AdsError) as exc:
            preview(engine, change, "invalid-pmax-" + code)
        assert exc.value.code == code


def test_signal_preserves_other_dimensions_and_detects_drift(pmax):
    engine, refs = pmax
    execute(engine, create_input(refs), "create-pmax-drift")
    group = next(iter(engine.provider.records["asset_group"].values()))
    request = {
        "kind": "set_asset_group_signals",
        "asset_group": group["resource_name"],
        "search_themes": ["tools"],
    }
    plan = preview(engine, request, "signal-drift-001")
    engine.provider.records["asset_group_signal"][ROOT + "/assetGroupSignals/111~222"] = {
        "resource_name": ROOT + "/assetGroupSignals/111~222",
        "asset_group": group["resource_name"],
        "search_theme": {"text": "external change"},
    }
    result = engine.submit(plan["id"])
    assert result["status"] == "STALE"
    assert result["result"]["error"]["code"] == "REMOTE_STATE_CHANGED"


def test_asset_upload_content_binding_and_receipt_identity(tmp_path):
    engine = Engine(tmp_path, settings(), FakeProvider())
    file = tmp_path / "image.png"
    Image.new("RGB", (600, 314), "blue").save(file)
    info, _ = image_payload(str(file))
    change = {
        "kind": "create_assets",
        "assets": [
            {"type": "IMAGE", "path": str(file), "sha256": info["sha256"], "name": "Test image"},
            {"type": "TEXT", "text": "Text"},
            {"type": "YOUTUBE_VIDEO", "youtube_video_id": "abcdefghijk", "name": "Test video"},
        ],
    }
    plan = preview(engine, change, "upload-assets-001")
    visible = public_payload(plan)
    assert (
        "data"
        not in visible["body"]["prepared"]["operations"][0]["asset_operation"]["create"][
            "image_asset"
        ]
    )
    assert (
        visible["body"]["prepared"]["operations"][0]["asset_operation"]["create"]["image_asset"][
            "upload"
        ]["sha256"]
        == info["sha256"]
    )
    result = engine.submit(plan["id"])
    assert result["status"] == "VERIFIED", result["result"]
    Image.new("RGB", (600, 314), "red").save(file)
    with pytest.raises(AdsError) as exc:
        preview(engine, change, "changed-file-001")
    assert exc.value.code == "IMAGE_CONTENT_CHANGED"


def test_asset_upload_lost_receipt_is_not_proven_by_matching_name(tmp_path):
    engine = Engine(tmp_path, settings(), FakeProvider())
    plan = preview(
        engine,
        {"kind": "create_assets", "assets": [{"type": "TEXT", "text": "same text"}]},
        "upload-unknown-001",
    )
    engine.provider.failure = TimeoutError()
    engine.provider.apply_before_failure = True
    assert engine.submit(plan["id"])["status"] == "UNKNOWN"
    engine.provider.failure = None
    result = engine.reconcile(plan["id"])
    assert result["status"] == "UNKNOWN"
    assert "RECEIPT_REQUIRED" in result["result"]["reconciliation"]["evidence"][0]["reason"]
    assert len([c for c in engine.provider.calls if not c[0]]) == 1


def test_cross_account_and_null_signals_rejected():
    for change in [
        {
            "kind": "set_asset_group_signals",
            "asset_group": ROOT + "/assetGroups/1",
            "audiences": ["customers/9999999999/audiences/2"],
        },
        {
            "kind": "set_asset_group_signals",
            "asset_group": ROOT + "/assetGroups/1",
            "search_themes": None,
        },
    ]:
        with pytest.raises(ValidationError):
            PlanInput(account=ACCOUNT, change=change, request_key="invalid-model-001")


async def test_new_read_surfaces_and_image_tool_are_callable(pmax, tmp_path):
    engine, refs = pmax
    execute(engine, create_input(refs), "create-pmax-tools")
    server = create_server(engine)
    handler = server.get_request_handler("tools/call")
    for resource in [
        "assets",
        "asset_groups",
        "asset_group_assets",
        "asset_group_signals",
        "audiences",
    ]:
        result = await handler.handler(
            None,
            handler.params_type(
                name="list_resources",
                arguments={"account": ACCOUNT.model_dump(), "resource": resource},
            ),
        )
        assert not result.is_error, result
    file = tmp_path / "inspect.png"
    Image.new("RGB", (128, 128), "blue").save(file)
    result = await handler.handler(
        None, handler.params_type(name="inspect_image_asset", arguments={"path": str(file)})
    )
    assert result.structured_content["compatible_field_types"] == ["LOGO"]
    assert "data" not in json.dumps(result.structured_content)


def test_pmax_url_expansion_preserves_other_automation(pmax):
    engine, refs = pmax
    execute(engine, create_input(refs), "pmax-automation-create")
    group = next(iter(engine.provider.records["asset_group"].values()))
    campaign = engine.provider.records["campaign"][group["campaign"]]
    other = {
        "asset_automation_type": "GENERATE_IMAGE_ENHANCEMENT",
        "asset_automation_status": "OPTED_OUT",
    }
    campaign["asset_automation_settings"].append(other)
    for enabled in [True, False]:
        execute(
            engine,
            {
                "kind": "update_campaign",
                "campaign": group["campaign"],
                "patch": {"final_url_expansion": enabled},
            },
            f"pmax-url-expansion-{enabled}",
        )
        settings = campaign["asset_automation_settings"]
        assert other in settings
        assert next(s for s in settings if s["asset_automation_type"].startswith("FINAL_URL"))[
            "asset_automation_status"
        ] == ("OPTED_IN" if enabled else "OPTED_OUT")


def test_image_changed_after_preview_never_writes(tmp_path):
    engine = Engine(tmp_path, settings(), FakeProvider())
    path = tmp_path / "image.png"
    Image.new("RGB", (600, 314), "blue").save(path)
    info, _ = image_payload(str(path))
    plan = preview(
        engine,
        {
            "kind": "create_assets",
            "assets": [
                {
                    "type": "IMAGE",
                    "path": str(path),
                    "sha256": info["sha256"],
                    "name": "Changed image",
                }
            ],
        },
        "image-preview-binding",
    )
    Image.new("RGB", (600, 314), "red").save(path)
    result = engine.submit(plan["id"])
    assert result["status"] == "STALE"
    assert result["result"]["error"]["code"] == "IMAGE_CONTENT_CHANGED"
    assert not any(not validation for validation, _ in engine.provider.calls)


def test_asset_dedup_uses_receipt_not_requested_name(tmp_path):
    provider = FakeProvider()
    ref = ROOT + "/assets/999"
    provider.records["asset"][ref] = {
        "resource_name": ref,
        "name": "Old name",
        "type_": "TEXT",
        "text_asset": {"text": "Existing text"},
    }
    mutate = provider.mutate

    def dedup(account, operations, validate_only):
        if validate_only:
            return mutate(account, operations, validate_only)
        return {"mutate_operation_responses": [{"asset_result": {"resource_name": ref}}]}

    provider.mutate = dedup
    engine = Engine(tmp_path, settings(), provider)
    result = execute(
        engine,
        {"kind": "create_assets", "assets": [{"type": "TEXT", "text": "Existing text"}]},
        "asset-dedup-receipt",
    )
    assert result["result"]["readback"]["complete"]
    assert len(provider.records["asset"]) == 1


def test_pmax_timeout_reconciles_without_duplicate_create(pmax):
    engine, refs = pmax
    plan = preview(engine, create_input(refs), "pmax-timeout-create")
    engine.provider.failure = TimeoutError()
    engine.provider.apply_before_failure = True
    assert engine.submit(plan["id"])["status"] == "UNKNOWN"
    engine.provider.failure = None
    assert engine.reconcile(plan["id"])["status"] == "RECONCILED"
    assert len([c for c in engine.provider.calls if not c[0]]) == 1


def test_automatic_group_assets_are_preserved(pmax):
    engine, refs = pmax
    execute(engine, create_input(refs), "pmax-auto-assets-create")
    group = next(iter(engine.provider.records["asset_group"].values()))
    ref = ROOT + "/assets/987"
    engine.provider.records["asset"][ref] = {
        "resource_name": ref,
        "type_": "TEXT",
        "text_asset": {"text": "Auto headline"},
    }
    link = ROOT + "/assetGroupAssets/123~987~2"
    engine.provider.records["asset_group_asset"][link] = {
        "resource_name": link,
        "asset_group": group["resource_name"],
        "asset": ref,
        "field_type": "HEADLINE",
        "source": "AUTOMATICALLY_CREATED",
        "status": "ENABLED",
    }
    execute(
        engine,
        {
            "kind": "set_asset_group_assets",
            "asset_group": group["resource_name"],
            "assets_by_field": {"HEADLINE": refs["HEADLINE"][:2]},
        },
        "pmax-auto-assets-replace",
    )
    assert engine.provider.records["asset_group_asset"][link]["status"] == "ENABLED"
    assert (
        sum(
            r["status"] == "ENABLED" and r["field_type"] == "HEADLINE"
            for r in engine.provider.records["asset_group_asset"].values()
        )
        == 3
    )
