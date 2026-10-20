from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
from datetime import UTC, datetime, timedelta

import pytest
from pydantic import ValidationError

from team_ads_mcp.engine import Engine
from team_ads_mcp.errors import AdsError
from team_ads_mcp.models import PlanInput, canonical, digest, micros

from .fakes import ACCOUNT, BUDGET, CAMPAIGN, GROUP, FakeProvider, search_input, settings


@pytest.fixture
def engine(tmp_path):
    return Engine(tmp_path, settings(), FakeProvider())


def status_input(key="status-key-001"):
    return PlanInput(
        account=ACCOUNT,
        request_key=key,
        change={"kind": "set_status", "resource": CAMPAIGN, "status": "ENABLED"},
    )


def test_full_search_atomic_paused_and_idempotent(engine):
    request = PlanInput.model_validate(search_input())
    plan = engine.preview(request)
    assert plan["status"] == "VALIDATED"
    assert all(v for v, _ in engine.provider.calls)
    assert len(engine.provider.records["campaign"]) == 1
    assert engine.preview(request)["id"] == plan["id"]
    result = engine.submit(plan["id"])
    assert result["status"] == "VERIFIED", result
    assert result["result"]["readback"]["complete"]
    for resource in ["campaign", "ad_group", "ad_group_ad"]:
        assert all(r["status"] == "PAUSED" for r in engine.provider.records[resource].values())
    assert engine.submit(plan["id"])["status"] == "VERIFIED"
    assert len([x for x in engine.provider.calls if not x[0]]) == 1
    assert len(result["result"]["readback"]["evidence"]) == 6


def test_validated_plan_submits_without_local_approval(engine):
    plan = engine.preview(status_input())
    assert engine.submit(plan["id"])["status"] == "VERIFIED"
    assert engine.provider.records["campaign"][CAMPAIGN]["status"] == "ENABLED"
    assert [e["event"] for e in engine.ledger.events(plan["id"])] == [
        "VALIDATED",
        "IN_FLIGHT",
        "IN_FLIGHT",
        "VERIFIED",
    ]


def test_missing_preview_cannot_submit(engine):
    with pytest.raises(AdsError) as exc:
        engine.submit("a" * 32)
    assert exc.value.code == "PLAN_NOT_FOUND"
    assert not engine.provider.calls


def test_legacy_approved_plan_survives_restart(engine):
    plan = engine.preview(status_input())
    # Existing installations can have a pending plan from the removed approval flow.
    with engine.ledger.db() as db:
        db.execute(
            "UPDATE plans SET status='APPROVED',approval_hash=?,approval_method=? WHERE id=?",
            (plan["plan_hash"], "macos_device_owner_authentication", plan["id"]),
        )
        engine.ledger.event(
            db, plan["id"], "APPROVED", {"method": "macos_device_owner_authentication"}
        )
    restarted = Engine(engine.path, engine.settings, engine.provider)
    assert restarted.submit(plan["id"])["status"] == "VERIFIED"
    assert restarted.submit(plan["id"])["status"] == "VERIFIED"
    assert len([x for x in engine.provider.calls if not x[0]]) == 1
    assert len([e for e in restarted.ledger.events(plan["id"]) if e["event"] == "APPROVED"]) == 1


@pytest.mark.parametrize("status", ["VALIDATED", "APPROVED"])
def test_tampered_plan_cannot_submit(engine, status):
    plan = engine.preview(status_input())
    plan["body"]["request"]["change"]["status"] = "PAUSED"
    with engine.ledger.db() as db:
        db.execute(
            "UPDATE plans SET body=?,status=? WHERE id=?",
            (canonical(plan["body"]), status, plan["id"]),
        )
    with pytest.raises(AdsError) as exc:
        engine.submit(plan["id"])
    assert exc.value.code == "PLAN_BINDING_CHANGED"
    assert not any(not v for v, _ in engine.provider.calls)


def test_cross_account_and_route_isolation(engine):
    data = status_input().model_dump()
    data["change"]["resource"] = "customers/9999999999/campaigns/11"
    with pytest.raises(ValidationError):
        PlanInput.model_validate(data)
    data = status_input().model_dump()
    data["account"]["login_customer_id"] = "1111111111"
    with pytest.raises(AdsError) as exc:
        engine.preview(PlanInput.model_validate(data))
    assert exc.value.code == "ACCOUNT_NOT_ALLOWED"
    assert not engine.provider.calls


def test_readonly_account_blocks_write(engine):
    engine.settings.accounts[0].writes = False
    with pytest.raises(AdsError) as exc:
        engine.preview(status_input())
    assert exc.value.code == "WRITE_DISABLED"


def test_request_key_conflict(engine):
    engine.preview(status_input())
    changed = status_input()
    changed.change.status = "PAUSED"
    with pytest.raises(AdsError) as exc:
        engine.preview(changed)
    assert exc.value.code == "IDEMPOTENCY_CONFLICT"


@pytest.mark.parametrize("status", ["VALIDATED", "APPROVED"])
def test_plan_expiry(engine, status):
    plan = engine.preview(status_input())
    plan["body"]["expires_at"] = (datetime.now(UTC) - timedelta(seconds=1)).isoformat()
    with engine.ledger.db() as db:
        db.execute(
            "UPDATE plans SET body=?,plan_hash=?,status=? WHERE id=?",
            (canonical(plan["body"]), digest(plan["body"]), status, plan["id"]),
        )
    with pytest.raises(AdsError) as exc:
        engine.submit(plan["id"])
    assert exc.value.code == "PLAN_EXPIRED"
    assert not any(not v for v, _ in engine.provider.calls)


def test_remote_state_drift_invalidates_plan_without_mutation(engine):
    plan = engine.preview(status_input())
    engine.provider.records["campaign_budget"][BUDGET]["amount_micros"] = 30_000_000
    result = engine.submit(plan["id"])
    assert result["status"] == "STALE"
    assert result["result"]["error"]["code"] == "REMOTE_STATE_CHANGED"
    assert not any(not v for v, _ in engine.provider.calls)


def test_config_and_credential_epoch_invalidates_plan(engine):
    plan = engine.preview(status_input())
    engine.settings.auth_epoch = "new-login"
    with pytest.raises(AdsError) as exc:
        engine.submit(plan["id"])
    assert exc.value.code == "PLAN_BINDING_CHANGED"


def test_shared_budget_requires_full_impact(engine):
    other = CAMPAIGN.replace("/11", "/12")
    engine.provider.records["campaign"][other] = {
        **deepcopy(engine.provider.records["campaign"][CAMPAIGN]),
        "resource_name": other,
    }
    engine.provider.records["campaign_budget"][BUDGET]["explicitly_shared"] = True
    engine.provider.records["campaign_budget"][BUDGET]["reference_count"] = 2
    data = {
        "account": ACCOUNT,
        "request_key": "shared-budget-001",
        "change": {
            "kind": "set_budget",
            "budget": BUDGET,
            "currency": "USD",
            "daily_amount": "40",
            "expected_campaigns": [CAMPAIGN],
        },
    }
    with pytest.raises(AdsError) as exc:
        engine.preview(PlanInput.model_validate(data))
    assert exc.value.code == "BUDGET_IMPACT_MISMATCH"
    assert exc.value.details["campaigns"] == [CAMPAIGN, other]
    data["change"]["expected_campaigns"].append(other)
    plan = engine.preview(PlanInput.model_validate(data))
    assert plan["body"]["prepared"]["warnings"]
    assert engine.submit(plan["id"])["status"] == "VERIFIED"


@pytest.mark.parametrize(
    "change",
    [
        {
            "kind": "add_keywords",
            "ad_group": GROUP,
            "keywords": [{"text": "new product", "match_type": "EXACT"}],
        },
        {
            "kind": "add_negatives",
            "campaign": CAMPAIGN,
            "keywords": [{"text": "free", "match_type": "BROAD"}],
        },
    ],
)
def test_keywords_add_then_remove(engine, change):
    plan = engine.preview(PlanInput(account=ACCOUNT, change=change, request_key="keyword-add-01"))
    assert engine.submit(plan["id"])["status"] == "VERIFIED"
    resource = "ad_group_criterion" if change["kind"] == "add_keywords" else "campaign_criterion"
    name = next(iter(engine.provider.records[resource]))
    removal = engine.preview(
        PlanInput(
            account=ACCOUNT,
            request_key="keyword-remove-01",
            change={"kind": "remove_keywords", "resources": [name]},
        )
    )
    assert engine.submit(removal["id"])["status"] == "VERIFIED"


@pytest.mark.parametrize("apply_before", [False, True])
def test_timeout_unknown_never_resends_then_reconciles(engine, apply_before):
    plan = engine.preview(PlanInput.model_validate(search_input()))
    engine.provider.failure = TimeoutError("Bearer SECRET-MUST-NOT-LEAK")
    engine.provider.apply_before_failure = apply_before
    result = engine.submit(plan["id"])
    assert result["status"] == "UNKNOWN"
    assert "SECRET" not in canonical(result)
    assert engine.submit(plan["id"])["status"] == "UNKNOWN"
    assert len([x for x in engine.provider.calls if not x[0]]) == 1
    engine.provider.failure = None
    recovered = engine.reconcile(plan["id"])
    assert recovered["status"] == ("RECONCILED" if apply_before else "UNKNOWN")
    with engine.ledger.db() as db:
        count = db.execute("SELECT count(*) FROM account_locks").fetchone()[0]
    assert count == (0 if apply_before else 1)


def test_partial_failure_never_success(engine):
    plan = engine.preview(status_input())
    engine.provider.failure = AdsError("PARTIAL_FAILURE", "partial failure")
    assert engine.submit(plan["id"])["status"] == "UNKNOWN"


def test_readback_mismatch_keeps_lock(engine, monkeypatch):
    plan = engine.preview(status_input())
    original = engine.provider.mutate

    def mutate(account, ops, validate_only):
        result = original(account, ops, validate_only)
        if not validate_only:
            engine.provider.records["campaign"][CAMPAIGN]["status"] = "PAUSED"
        return result

    monkeypatch.setattr(engine.provider, "mutate", mutate)
    assert engine.submit(plan["id"])["status"] == "READBACK_MISMATCH"


def test_validation_failure_never_creates_plan(engine):
    engine.provider.validation_failure = AdsError("GOOGLE_REJECTED", "USER_PERMISSION_DENIED")
    with pytest.raises(AdsError):
        engine.preview(status_input())
    assert engine.ledger.by_key("status-key-001") is None


def test_concurrent_claims_only_one(engine):
    plan = engine.preview(status_input())

    def claim():
        try:
            engine.ledger.claim(plan["id"], ACCOUNT.customer_id)
            return "claimed"
        except AdsError:
            return "rejected"

    with ThreadPoolExecutor(2) as pool:
        assert sorted(pool.map(lambda _: claim(), range(2))) == ["claimed", "rejected"]


def test_exact_micros_and_currency(engine):
    assert micros("0.000001") == 1
    assert micros("25.000001") == 25_000_001
    data = search_input()
    data["change"]["currency"] = "EUR"
    with pytest.raises(AdsError) as exc:
        engine.preview(PlanInput.model_validate(data))
    assert exc.value.code == "CURRENCY_MISMATCH"
    for amount in [25.2, "-1", "1e2", "0.0000001", "NaN", "0"]:
        data["change"]["daily_amount"] = amount
        with pytest.raises(ValidationError):
            PlanInput.model_validate(data)


def test_broad_resource_or_status_injection_rejected():
    data = search_input()
    data["change"]["status"] = "ENABLED"
    with pytest.raises(ValidationError):
        PlanInput.model_validate(data)
