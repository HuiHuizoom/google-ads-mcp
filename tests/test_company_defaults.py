import json

import pytest

from team_ads_mcp import cli
from team_ads_mcp.config import CLICO_CLOUD_PROJECT, CLICO_CUSTOMER_ID, load_settings
from team_ads_mcp.errors import AdsError
from team_ads_mcp.models import Account


def test_fresh_install_targets_only_clico_without_granting_google_authorization(tmp_path):
    cfg = load_settings(tmp_path)
    account = Account(customer_id=CLICO_CUSTOMER_ID)
    assert cfg.cloud_project == CLICO_CLOUD_PROJECT
    assert cfg.auth_mode == "oauth" and not cfg.oauth_authorization_recorded
    assert cfg.auth_epoch == "unconnected"
    assert cfg.policy(account, write=True).max_daily_budget == "100"
    with pytest.raises(AdsError, match="允许列表"):
        cfg.policy(Account(customer_id="1234567890"), write=True)
    with pytest.raises(AdsError) as exc:
        cfg.limit(account, 100_000_001)
    assert exc.value.code == "BUDGET_LIMIT"
    assert not (tmp_path / "config.json").exists()
    # Instances must not share a mutable allowlist.
    cfg.accounts.clear()
    assert len(load_settings(tmp_path).accounts) == 1


@pytest.mark.parametrize(
    "saved",
    [
        {},
        {"cloud_project": "other-project", "accounts": []},
        {
            "cloud_project": CLICO_CLOUD_PROJECT,
            "accounts": [{"account": {"customer_id": CLICO_CUSTOMER_ID}, "writes": False}],
        },
    ],
)
def test_existing_policy_never_inherits_or_enables_company_defaults(tmp_path, saved):
    path = tmp_path / "config.json"
    original = json.dumps(saved)
    path.write_text(original)
    cfg = load_settings(tmp_path)
    assert cfg.cloud_project == saved.get("cloud_project", "")
    assert len(cfg.accounts) == len(saved.get("accounts", []))
    with pytest.raises(AdsError) as exc:
        cfg.policy(Account(customer_id=CLICO_CUSTOMER_ID), write=True)
    assert exc.value.code in {"WRITE_DISABLED", "ACCOUNT_NOT_ALLOWED"}
    assert path.read_text() == original


def test_explicit_configure_on_fresh_install_does_not_keep_clico_access(
    tmp_path, monkeypatch, capsys
):
    monkeypatch.setattr(
        "sys.argv",
        [
            "team-ads",
            "--state-dir",
            str(tmp_path),
            "configure",
            "--cloud-project",
            "other-project",
            "--customer-id",
            "1234567890",
        ],
    )
    cli.main()
    assert json.loads(capsys.readouterr().out)["saved"]
    cfg = load_settings(tmp_path)
    assert cfg.cloud_project == "other-project"
    assert len(cfg.accounts) == 1 and cfg.accounts[0].account.customer_id == "1234567890"
    assert not cfg.accounts[0].writes
