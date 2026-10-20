import pytest

from team_ads_mcp.engine import Engine
from team_ads_mcp.errors import AdsError
from team_ads_mcp.models import PlanInput

from .fakes import ACCOUNT, BUDGET, CAMPAIGN, FakeProvider, search_input, settings
from .test_engine import status_input


def test_readback_permission_failure_is_unknown_not_rejected(tmp_path, monkeypatch):
    provider = FakeProvider()
    engine = Engine(tmp_path, settings(), provider)
    plan = engine.preview(status_input())
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
    provider.failure = TimeoutError()
    assert engine.submit(first["id"])["status"] == "UNKNOWN"
    provider.failure = None
    second = engine.preview(status_input("second-key-001"))
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


def unknown_creation(tmp_path, *, applied=False, age=True):
    provider = FakeProvider()
    engine = Engine(tmp_path, settings(), provider)
    plan = engine.preview(PlanInput.model_validate(search_input()))
    provider.failure = TimeoutError()
    provider.apply_before_failure = applied
    assert engine.submit(plan["id"])["status"] == "UNKNOWN"
    provider.failure = None
    if age:
        with engine.ledger.db() as db:
            db.execute(
                "UPDATE events SET at='2020-01-01T00:00:00+00:00' WHERE plan_id=? AND event='IN_FLIGHT'",
                (plan["id"],),
            )
    return provider, engine, plan


def test_explicit_unknown_creation_abandonment_preserves_unknown_and_never_resends(tmp_path):
    provider, engine, plan = unknown_creation(tmp_path)
    result = engine.abandon_unknown_creation(plan["id"], plan["plan_hash"], "operator accepts risk")
    assert result["status"] == "ABANDONED_UNKNOWN"
    assert result["result"]["error"]["code"] == "TRANSPORT_UNKNOWN"
    assert result["result"]["manual_resolution"]["outcome"] == "unknown_not_cancelled"
    assert engine.submit(plan["id"])["status"] == "ABANDONED_UNKNOWN"
    assert (
        engine.abandon_unknown_creation(plan["id"], plan["plan_hash"], "repeat")["status"]
        == "ABANDONED_UNKNOWN"
    )
    assert len([x for x in provider.calls if not x[0]]) == 1
    with engine.ledger.db() as db:
        assert db.execute("SELECT count(*) FROM account_locks").fetchone()[0] == 0
    events = engine.ledger.events(plan["id"])
    assert [e["event"] for e in events].count("ABANDONED_UNKNOWN") == 1
    next_plan = engine.preview(status_input("after-abandonment"))
    assert engine.submit(next_plan["id"])["status"] == "VERIFIED"


@pytest.mark.parametrize(
    "mode,code",
    [
        ("hash", "PLAN_BINDING_CHANGED"),
        ("early", "RECOVERY_TOO_EARLY"),
        ("applied", "RECOVERY_OBJECTS_FOUND"),
        ("receipt", "RECOVERY_NOT_ALLOWED"),
        ("reason", "RECOVERY_REASON_REQUIRED"),
        ("read_failure", "TRANSPORT_UNKNOWN"),
    ],
)
def test_unknown_abandonment_fails_closed(tmp_path, monkeypatch, mode, code):
    provider, engine, plan = unknown_creation(
        tmp_path, applied=mode == "applied", age=mode != "early"
    )
    if mode == "receipt":
        engine.ledger.finish(
            plan["id"],
            "UNKNOWN",
            {"receipt": {}, "error": {"code": "TRANSPORT_UNKNOWN"}},
            release=False,
        )
    if mode == "read_failure":

        def unavailable(*args, **kwargs):
            raise AdsError("TRANSPORT_UNKNOWN", "unavailable")

        monkeypatch.setattr(provider, "search", unavailable)
    with pytest.raises(AdsError) as exc:
        engine.abandon_unknown_creation(
            plan["id"],
            "wrong" if mode == "hash" else plan["plan_hash"],
            "" if mode == "reason" else "operator accepts risk",
        )
    assert exc.value.code == code
    assert engine.ledger.get(plan["id"])["status"] == "UNKNOWN"
    with engine.ledger.db() as db:
        assert db.execute("SELECT count(*) FROM account_locks").fetchone()[0] == 1
