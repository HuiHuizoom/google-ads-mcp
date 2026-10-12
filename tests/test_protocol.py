import json
import os
import sys

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

from team_ads_mcp.ledger import Ledger

from .fakes import ACCOUNT, search_input


async def test_actual_stdio_initialize_list_and_unconfigured_call(tmp_path):
    params = StdioServerParameters(
        command=sys.executable,
        args=["-m", "team_ads_mcp.server"],
        env={**os.environ, "TEAM_ADS_STATE_DIR": str(tmp_path)},
    )
    async with stdio_client(params) as (read, write), ClientSession(read, write) as session:
        init = await session.initialize()
        assert init.server_info.name == "team-ads-mcp"
        listed = await session.list_tools()
        assert len(listed.tools) == 13
        assert not any("approve" == t.name for t in listed.tools)
        status = await session.call_tool("connection_status", {})
        assert not status.is_error
        assert status.structured_content["live_google_connection"] == "not_checked"
        denied = await session.call_tool("preflight", {"account": ACCOUNT.model_dump()})
        assert denied.is_error
        assert denied.structured_content["error"]["code"] == "ACCOUNT_NOT_ALLOWED"
        bad = await session.call_tool("connection_status", {"token": "super-secret-value"})
        assert bad.is_error
        assert "super-secret-value" not in json.dumps(bad.model_dump())


async def test_full_protocol_preview_approval_submit_readback(tmp_path):
    params = StdioServerParameters(
        command=sys.executable, args=["-m", "tests.fixture_server", str(tmp_path)]
    )
    async with stdio_client(params) as (read, write), ClientSession(read, write) as session:
        await session.initialize()
        listed = await session.list_tools()
        assert len(listed.tools) == 13
        preflight = await session.call_tool("preflight", {"account": ACCOUNT.model_dump()})
        assert preflight.structured_content["provenance"]["source"] == "offline_test_simulator"
        assert preflight.structured_content["provenance"]["account_time_zone"] == "America/New_York"
        preview = await session.call_tool("preview_change", search_input())
        assert not preview.is_error, preview
        plan = preview.structured_content
        assert plan["status"] == "VALIDATED"
        blocked = await session.call_tool("submit_change", {"operation_id": plan["id"]})
        assert blocked.is_error
        assert blocked.structured_content["error"]["code"] == "APPROVAL_REQUIRED"
        # Inject the test human-presence boundary outside the protocol.
        Ledger(tmp_path).approve(plan["id"], plan["plan_hash"], "test_human_presence")
        submitted = await session.call_tool("submit_change", {"operation_id": plan["id"]})
        assert not submitted.is_error
        assert submitted.structured_content["status"] == "VERIFIED", submitted
        repeated = await session.call_tool("submit_change", {"operation_id": plan["id"]})
        assert repeated.structured_content["result"] == submitted.structured_content["result"]
        status = await session.call_tool("operation_status", {"operation_id": plan["id"]})
        assert status.structured_content["plan"]["status"] == "VERIFIED"
        assert any(e["event"] == "APPROVED" for e in status.structured_content["events"])
