"""Separate test-only stdio subprocess. Not installed or reachable in production."""

import asyncio
import sys
from pathlib import Path

from team_ads_mcp.engine import Engine
from team_ads_mcp.server import serve

from .fakes import FakeProvider, settings

asyncio.run(serve(Engine(Path(sys.argv[1]), settings(), FakeProvider())))
