import hashlib
import json
from pathlib import Path
from unittest.mock import Mock

import pytest

from team_ads_mcp import approval, auth
from team_ads_mcp.config import load_settings, save_settings
from team_ads_mcp.engine import Engine
from team_ads_mcp.errors import AdsError

from .fakes import FakeProvider, settings
from .test_auth_provider import CLIENT
from .test_engine import approve, status_input


def test_login_uses_loopback_offline_pkce_and_os_store(tmp_path, monkeypatch):
    client = tmp_path / "client.json"
    client.write_text(json.dumps(CLIENT))
    cfg = settings()
    fake_flow = Mock()
    fake_flow.run_local_server.return_value = Mock(refresh_token="offline-fake")
    fake_flow.run_local_server.return_value.to_json.return_value = (
        '{"refresh_token":"offline-fake"}'
    )
    factory = Mock(return_value=fake_flow)
    monkeypatch.setattr(auth.InstalledAppFlow, "from_client_config", factory)
    monkeypatch.setattr(auth.CredentialStore, "_backend", lambda _: None)
    saved = []
    monkeypatch.setattr(auth.CredentialStore, "write", lambda _, r: saved.append(r))
    result = auth.login(tmp_path, cfg, client)
    assert factory.call_args.kwargs["autogenerate_code_verifier"] is True
    kwargs = fake_flow.run_local_server.call_args.kwargs
    assert kwargs["host"] == kwargs["bind_addr"] == "127.0.0.1"
    assert kwargs["port"] == 0 and kwargs["timeout_seconds"] == 180
    assert kwargs["access_type"] == "offline" and kwargs["prompt"] == "consent"
    assert saved and "offline-fake" not in json.dumps(result)
    assert load_settings(tmp_path).auth_epoch != "unconnected"


def test_local_forget_invalidates_pending_approvals(tmp_path, monkeypatch):
    cfg = settings()
    monkeypatch.setattr(auth.CredentialStore, "delete", lambda _: None)
    result = auth.disconnect(tmp_path, cfg, revoke=False)
    assert not result["remote_revocation_confirmed"]
    assert result["local_credentials_removed"]
    assert load_settings(tmp_path).auth_epoch != "unconnected"


def test_native_cancel_never_approves(tmp_path, monkeypatch):
    cfg = settings()
    save_settings(tmp_path, cfg)
    engine = Engine(tmp_path, cfg, FakeProvider())
    plan = engine.preview(status_input())
    monkeypatch.setattr(approval.sys, "platform", "darwin")
    source = Path(approval.__file__).with_name("approval.swift")
    source_hash = hashlib.sha256(source.read_bytes()).hexdigest()[:16]
    executable = (
        tmp_path / ("Team Ads Approval-" + source_hash + ".app") / "Contents/MacOS/TeamAdsApproval"
    )
    executable.parent.mkdir(parents=True)
    executable.touch()
    monkeypatch.setattr(
        approval.subprocess, "run", Mock(return_value=Mock(returncode=3, stdout=""))
    )
    with pytest.raises(AdsError) as exc:
        approval.approve_with_user_presence(engine, plan["id"])
    assert exc.value.code == "APPROVAL_CANCELLED"
    assert engine.ledger.get(plan["id"])["status"] == "VALIDATED"
    assert not list(tmp_path.glob("review-*.txt"))


def test_precommit_permission_recheck_does_not_send_write(tmp_path):
    provider = FakeProvider()
    engine = Engine(tmp_path, settings(), provider)
    plan = engine.preview(status_input())
    approve(engine, plan)
    provider.validation_failure = AdsError("GOOGLE_REJECTED", "permission lost")
    result = engine.submit(plan["id"])
    assert result["status"] == "STALE"
    assert not any(not v for v, _ in provider.calls)
