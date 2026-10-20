import json
from unittest.mock import Mock

import pytest

from team_ads_mcp import auth
from team_ads_mcp.cli import parser
from team_ads_mcp.config import load_settings
from team_ads_mcp.engine import Engine
from team_ads_mcp.errors import AdsError

from .fakes import FakeProvider, settings
from .test_auth_provider import CLIENT
from .test_engine import status_input


@pytest.mark.parametrize("company_defaults", [False, True])
def test_login_uses_loopback_offline_pkce_and_os_store(tmp_path, monkeypatch, company_defaults):
    client = tmp_path / ("oauth-client.json" if company_defaults else "client.json")
    cfg = load_settings(tmp_path) if company_defaults else settings()
    data = json.loads(json.dumps(CLIENT))
    data["installed"]["project_id"] = cfg.cloud_project
    client.write_text(json.dumps(data))
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
    result = auth.login(tmp_path, cfg, None if company_defaults else client)
    assert factory.call_args.kwargs["autogenerate_code_verifier"] is True
    kwargs = fake_flow.run_local_server.call_args.kwargs
    assert kwargs["host"] == kwargs["bind_addr"] == "127.0.0.1"
    assert kwargs["port"] == 0 and kwargs["timeout_seconds"] == 180
    assert kwargs["access_type"] == "offline" and kwargs["prompt"] == "consent"
    assert saved and "offline-fake" not in json.dumps(result)
    persisted = load_settings(tmp_path)
    assert persisted.auth_epoch != "unconnected" and persisted.oauth_authorization_recorded
    assert persisted.cloud_project == cfg.cloud_project and persisted.accounts == cfg.accounts


def test_missing_company_client_has_setup_guidance_before_opening_browser(tmp_path, monkeypatch):
    factory = Mock()
    monkeypatch.setattr(auth.InstalledAppFlow, "from_client_config", factory)
    with pytest.raises(AdsError) as exc:
        auth.login(tmp_path, load_settings(tmp_path))
    assert exc.value.code == "OAUTH_CLIENT_REQUIRED"
    assert exc.value.details["client_file"] == str(tmp_path / "oauth-client.json")
    factory.assert_not_called()
    assert not (tmp_path / "config.json").exists()
    assert parser().parse_args(["auth", "login"]).client_file is None


def test_local_forget_invalidates_pending_plans(tmp_path, monkeypatch):
    cfg = settings()
    engine = Engine(tmp_path, cfg, FakeProvider())
    plan = engine.preview(status_input())
    monkeypatch.setattr(auth.CredentialStore, "delete", lambda _: None)
    result = auth.disconnect(tmp_path, cfg, revoke=False)
    assert not result["remote_revocation_confirmed"]
    assert result["local_credentials_removed"]
    assert load_settings(tmp_path).auth_epoch != "unconnected"
    engine.settings = load_settings(tmp_path)
    with pytest.raises(AdsError) as exc:
        engine.submit(plan["id"])
    assert exc.value.code == "PLAN_BINDING_CHANGED"
    assert not any(not v for v, _ in engine.provider.calls)


def test_cli_submits_plans_without_an_approval_command():
    cli = parser()
    args = cli.parse_args(["submit", "a" * 32])
    assert args.command == "submit"
    assert "approve" not in cli.format_help()
    with pytest.raises(SystemExit):
        cli.parse_args(["approve", "a" * 32])


def test_precommit_permission_recheck_does_not_send_write(tmp_path):
    provider = FakeProvider()
    engine = Engine(tmp_path, settings(), provider)
    plan = engine.preview(status_input())
    provider.validation_failure = AdsError("GOOGLE_REJECTED", "permission lost")
    result = engine.submit(plan["id"])
    assert result["status"] == "STALE"
    assert not any(not v for v, _ in provider.calls)
