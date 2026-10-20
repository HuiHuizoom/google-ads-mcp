import json
import os
import sys

import pytest
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

from team_ads_mcp import __version__
from team_ads_mcp.engine import Engine
from team_ads_mcp.server import create_server

from .fakes import ACCOUNT, FakeProvider, search_input, settings


async def test_actual_stdio_initialize_list_and_unconfigured_call(tmp_path):
    params = StdioServerParameters(
        command=sys.executable,
        args=["-m", "team_ads_mcp.server"],
        env={**os.environ, "TEAM_ADS_STATE_DIR": str(tmp_path)},
    )
    async with stdio_client(params) as (read, write), ClientSession(read, write) as session:
        init = await session.initialize()
        assert init.server_info.name == "clico-google-ads"
        listed = await session.list_tools()
        assert len(listed.tools) == 15
        assert not any("approve" == t.name for t in listed.tools)
        preview_schema = next(t.input_schema for t in listed.tools if t.name == "preview_change")
        kinds = preview_schema["properties"]["change"]["discriminator"]["mapping"]
        assert len(kinds) == 27 and "create_pmax" in kinds and "set_asset_group_signals" in kinds
        assert "省略字段保留原值" in preview_schema["$defs"]["RSAPatch"]["description"]
        status = await session.call_tool("connection_status", {})
        assert not status.is_error
        assert status.structured_content["live_google_connection"] == "not_checked"
        assert status.structured_content["server_name"] == init.server_info.name
        assert (
            status.structured_content["server_version"] == init.server_info.version == __version__
        )
        denied = await session.call_tool("preflight", {"account": ACCOUNT.model_dump()})
        assert denied.is_error
        assert denied.structured_content["error"]["code"] == "ACCOUNT_NOT_ALLOWED"
        bad = await session.call_tool("connection_status", {"token": "super-secret-value"})
        assert bad.is_error
        assert "super-secret-value" not in json.dumps(bad.model_dump())


async def test_full_protocol_preview_submit_readback(tmp_path):
    params = StdioServerParameters(
        command=sys.executable, args=["-m", "tests.fixture_server", str(tmp_path)]
    )
    async with stdio_client(params) as (read, write), ClientSession(read, write) as session:
        init = await session.initialize()
        assert "preview_change → submit_change → 远端回读" in init.instructions
        listed = await session.list_tools()
        assert len(listed.tools) == 15
        preflight = await session.call_tool("preflight", {"account": ACCOUNT.model_dump()})
        assert preflight.structured_content["provenance"]["source"] == "offline_test_simulator"
        assert preflight.structured_content["provenance"]["account_time_zone"] == "America/New_York"
        preview = await session.call_tool("preview_change", search_input())
        assert not preview.is_error, preview
        plan = preview.structured_content
        assert plan["status"] == "VALIDATED"
        submitted = await session.call_tool("submit_change", {"operation_id": plan["id"]})
        assert not submitted.is_error
        assert submitted.structured_content["status"] == "VERIFIED", submitted
        repeated = await session.call_tool("submit_change", {"operation_id": plan["id"]})
        assert repeated.structured_content["result"] == submitted.structured_content["result"]
        status = await session.call_tool("operation_status", {"operation_id": plan["id"]})
        assert status.structured_content["plan"]["status"] == "VERIFIED"
        assert not any(e["event"] == "APPROVED" for e in status.structured_content["events"])
        # Actual protocol lifecycle: discover a newly created ad, then edit it in a later plan.
        ads = await session.call_tool(
            "list_resources", {"account": ACCOUNT.model_dump(), "resource": "ads"}
        )
        assert not ads.is_error
        ad = ads.structured_content["data"][0]["ad_group_ad"]
        links = await session.call_tool(
            "list_resources",
            {
                "account": ACCOUNT.model_dump(),
                "resource": "ads",
                "ad_resource": ad["ad"]["resource_name"],
            },
        )
        change = {
            "account": ACCOUNT.model_dump(),
            "request_key": "edit-existing-ad-001",
            "change": {
                "kind": "update_ad",
                "ad_group_ad": ad["resource_name"],
                "expected_ad_group_ads": [
                    r["ad_group_ad"]["resource_name"] for r in links.structured_content["data"]
                ],
                "patch": {"final_urls": ["https://example.com/updated"]},
            },
        }
        edit = await session.call_tool("preview_change", change)
        assert not edit.is_error, edit
        plan = edit.structured_content
        edited = await session.call_tool("submit_change", {"operation_id": plan["id"]})
        assert edited.structured_content["status"] == "VERIFIED", edited
        current = await session.call_tool(
            "list_resources", {"account": ACCOUNT.model_dump(), "resource": "ads"}
        )
        assert current.structured_content["data"][0]["ad_group_ad"]["ad"]["final_urls"] == [
            "https://example.com/updated"
        ]
        isolated = await session.call_tool(
            "list_resources",
            {
                "account": ACCOUNT.model_dump(),
                "resource": "ads",
                "campaign": "customers/9999999999/campaigns/11",
            },
        )
        assert (
            isolated.is_error
            and isolated.structured_content["error"]["code"] == "CROSS_ACCOUNT_RESOURCE"
        )


@pytest.mark.parametrize("report", ["campaigns", "search_terms"])
async def test_reports_exclude_unselected_defaults_and_preserve_missing(
    tmp_path, monkeypatch, report
):
    provider = FakeProvider()

    def search(account, query, limit):
        assert "metrics.cost_micros" in query and "metrics.average_cpc" not in query
        assert ("FROM search_term_view" in query) == (report == "search_terms")
        return [
            {
                "campaign": {"id": "11", "name": "Test", "status": "PAUSED", "labels": []},
                "segments": {"date": "2026-01-01", "device": "UNSPECIFIED"},
                "metrics": {"clicks": "0", "cost_micros": "0", "average_cpc": 0.0},
                "ad_group": {"id": "31", "status": "UNSPECIFIED"},
                "search_term_view": {"search_term": "test query", "status": "UNSPECIFIED"},
            }
        ]

    monkeypatch.setattr(provider, "search", search)
    server = create_server(Engine(tmp_path, settings(), provider))
    handler = server.get_request_handler("tools/call")
    result = await handler.handler(
        None,
        handler.params_type(
            name="performance_report",
            arguments={
                "account": ACCOUNT.model_dump(),
                "dates": {"start": "2026-01-01", "end": "2026-01-01"},
                "report": report,
            },
        ),
    )
    assert not result.is_error
    row = result.structured_content["data"]["rows"][0]
    expected = {
        "campaign": {"id": "11", "name": "Test", "status": "PAUSED"},
        "segments": {"date": "2026-01-01"},
        "metrics": {"clicks": "0", "cost_micros": "0"},
    }
    if report == "search_terms":
        expected.update(ad_group={"id": "31"}, search_term_view={"search_term": "test query"})
    assert row == expected
