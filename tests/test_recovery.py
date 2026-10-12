import pytest

from team_ads_mcp.engine import Engine
from team_ads_mcp.errors import AdsError
from team_ads_mcp.models import PlanInput

from .fakes import ACCOUNT, BUDGET, CAMPAIGN, FakeProvider, search_input, settings
from .test_engine import approve, status_input


def test_readback_permission_failure_is_unknown_not_rejected(tmp_path, monkeypatch):
    provider = FakeProvider()
    engine = Engine(tmp_path, settings(), provider)
    plan = engine.preview(status_input())
    approve(engine, plan)
    original = provider.mutate

    def mutate(account, ops, validate_only):
        result = original(account, ops, validate_only)
        if not validate_only:

            def denied(*args, **kwargs):
                raise AdsError("GOOGLE_REJECTED", "USER_PERMISSION_DENIED")

            monkeypatch.setattr(provider, "search", denied)
        return result

    monkeypatch.setattr(provider, "mutate", mutate)
    result = engine.submit(plan["id"])
    assert result["status"] == "UNKNOWN"
    assert "receipt" in result["result"]
    with engine.ledger.db() as db:
        assert db.execute("SELECT count(*) FROM account_locks").fetchone()[0] == 1


def test_recovery_survives_restart_reauth_and_disabled_writes(tmp_path):
    provider = FakeProvider()
    config = settings()
    engine = Engine(tmp_path, config, provider)
    plan = engine.preview(PlanInput.model_validate(search_input()))
    approve(engine, plan)
    provider.failure = TimeoutError()
    provider.apply_before_failure = True
    assert engine.submit(plan["id"])["status"] == "UNKNOWN"
    provider.failure = None
    config.auth_epoch = "new-credentials"
    config.accounts[0].writes = False
    restarted = Engine(tmp_path, config, provider)
    assert restarted.reconcile(plan["id"])["status"] == "RECONCILED"
    assert len([x for x in provider.calls if not x[0]]) == 1


def test_unknown_blocks_other_operation_for_same_account(tmp_path):
    provider = FakeProvider()
    engine = Engine(tmp_path, settings(), provider)
    first = engine.preview(status_input())
    approve(engine, first)
    provider.failure = TimeoutError()
    assert engine.submit(first["id"])["status"] == "UNKNOWN"
    provider.failure = None
    second = engine.preview(status_input("second-key-001"))
    approve(engine, second)
    with pytest.raises(AdsError) as exc:
        engine.submit(second["id"])
    assert exc.value.code == "ACCOUNT_BUSY"


def test_shared_budget_missing_reference_fails_closed(tmp_path):
    provider = FakeProvider()
    provider.records["campaign_budget"][BUDGET]["reference_count"] = 2
    engine = Engine(tmp_path, settings(), provider)
    data = {
        "account": ACCOUNT,
        "request_key": "budget-missing",
        "change": {
            "kind": "set_budget",
            "budget": BUDGET,
            "currency": "USD",
            "daily_amount": "30",
            "expected_campaigns": [CAMPAIGN],
        },
    }
    with pytest.raises(AdsError) as exc:
        engine.preview(PlanInput.model_validate(data))
    assert exc.value.code == "BUDGET_IMPACT_INCOMPLETE"
