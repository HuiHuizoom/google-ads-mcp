"""Credential-free smoke test against a real stdio subprocess. Never calls Google."""

import asyncio
import json
import os
import sys
import tempfile

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client


async def main():
    with tempfile.TemporaryDirectory(prefix="team-ads-smoke-") as state:
        params = StdioServerParameters(
            command=sys.executable,
            args=["-m", "team_ads_mcp.server"],
            env={**os.environ, "TEAM_ADS_STATE_DIR": state},
        )
        async with stdio_client(params) as (read, write), ClientSession(read, write) as session:
            initialized = await session.initialize()
            listed = await session.list_tools()
            status = await session.call_tool("connection_status", {})
            denied = await session.call_tool(
                "preflight", {"account": {"customer_id": "1234567890"}}
            )
            assert len(listed.tools) == 13 and not status.is_error
            assert (
                denied.is_error
                and denied.structured_content["error"]["code"] == "ACCOUNT_NOT_ALLOWED"
            )
            print(
                json.dumps(
                    {
                        "server": initialized.server_info.model_dump(),
                        "protocol_version": initialized.protocol_version,
                        "tools": [t.name for t in listed.tools],
                        "initialize": "passed",
                        "tools_list": "passed",
                        "tools_call": "passed",
                        "missing_account_failure": "passed",
                        "google_connected": False,
                        "google_mutations": 0,
                    },
                    ensure_ascii=False,
                    indent=2,
                )
            )


asyncio.run(main())
