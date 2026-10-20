"""Google-normalized Search resources, including recovery of pre-fix ledgers."""

from copy import deepcopy

import pytest

from team_ads_mcp.engine import Engine
from team_ads_mcp.models import PlanInput, canonical, digest
from team_ads_mcp.provider import literal

from .fakes import ACCOUNT, ROOT, FakeProvider, search_input, settings


class GoogleDefaultsProvider(FakeProvider):
    def mutate(self, account, operations, validate_only):
        receipt = super().mutate(account, operations, validate_only)
        if not validate_only:
            for campaign in self.records["campaign"].values():
                if "[tmcp-" not in campaign["name"]:
                    continue
                budget = self.records["campaign_budget"][campaign["campaign_budget"]]
                if not budget["explicitly_shared"]:
                    budget["name"] = campaign["name"]
                for index, device in enumerate(["DESKTOP", "MOBILE", "TABLET"]):
                    name = ROOT + f"/campaignCriteria/102~{30000 + index}"
                    self.records["campaign_criterion"][name] = {
                        "resource_name": name,
                        "campaign": campaign["resource_name"],
                        "status": "ENABLED",
                        "type_": "DEVICE",
                        "device": {"type_": device},
                        "negative": False,
                    }
        return receipt


def legacy_applied_plan(engine):
    """Reconstruct a committed 0.3.0 plan and its pending recovery lock."""
    plan = engine.preview(PlanInput.model_validate(search_input()))
    body = plan["body"]
    old_name = "Budget" + f" [tmcp-{plan['id']}]"
    body["prepared"]["operations"][0]["campaign_budget_operation"]["create"]["name"] = old_name
    for check in body["prepared"]["checks"]:
        if check["resource"] == "campaign_budget":
            check["condition"] = f"campaign_budget.name = {literal(old_name)}"
            check["expected"][0]["campaign_budget"]["name"] = old_name
        elif check["resource"] == "campaign":
            check["expected"][0]["campaign_budget"]["name"] = old_name
        elif check["resource"] == "campaign_criterion":
            check["condition"] = check["condition"].removesuffix(
                " AND campaign_criterion.type != 'DEVICE'"
            )
    with engine.ledger.db() as db:
        db.execute(
            "UPDATE plans SET body=?,plan_hash=? WHERE id=?",
            (canonical(body), digest(body), plan["id"]),
        )
    engine.ledger.claim(plan["id"], ACCOUNT.customer_id)
    receipt = engine.provider.mutate(ACCOUNT, body["prepared"]["operations"], validate_only=False)
    engine.ledger.finish(plan["id"], "READBACK_MISMATCH", {"receipt": receipt}, release=False)
    return engine.ledger.get(plan["id"])


def test_search_creation_verifies_google_budget_name_and_default_devices(tmp_path):
    engine = Engine(tmp_path, settings(), GoogleDefaultsProvider())
    plan = engine.preview(PlanInput.model_validate(search_input()))
    assert engine.submit(plan["id"])["status"] == "VERIFIED"
    assert len([c for c in engine.provider.calls if not c[0]]) == 1


@pytest.mark.parametrize("changed", [None, "budget", "location", "negative_keyword", "ad"])
def test_legacy_recovery_preserves_plan_and_requires_full_match(tmp_path, changed):
    provider = GoogleDefaultsProvider()
    engine = Engine(tmp_path, settings(), provider)
    plan = legacy_applied_plan(engine)
    original_body = deepcopy(plan["body"])
    campaign = next(c for c in provider.records["campaign"].values() if "[tmcp-" in c["name"])
    if changed == "budget":
        provider.records["campaign_budget"][campaign["campaign_budget"]]["amount_micros"] += 1
    elif changed == "location":
        criterion = next(
            c for c in provider.records["campaign_criterion"].values() if c["type_"] == "LOCATION"
        )
        criterion["location"]["geo_target_constant"] = "geoTargetConstants/2826"
    elif changed == "negative_keyword":
        criterion = next(
            c for c in provider.records["campaign_criterion"].values() if c["type_"] == "KEYWORD"
        )
        criterion["keyword"]["text"] = "unexpected"
    elif changed == "ad":
        next(iter(provider.records["ad_group_ad"].values()))["ad"]["final_urls"] = [
            "https://example.com/unexpected"
        ]
    # Recovery stays read-only even after writes are disabled and credentials rotate.
    config = settings()
    config.accounts[0].writes = False
    config.auth_epoch = "new-login"
    result = Engine(tmp_path, config, provider).reconcile(plan["id"])
    assert result["status"] == ("RECONCILED" if changed is None else "UNKNOWN")
    assert result["body"] == original_body
    assert result["plan_hash"] == plan["plan_hash"]
    assert result["result"]["reconciliation"]["check_compatibility"] == [
        "implicit_device_criteria",
        "legacy_nonshared_budget_name",
    ]
    assert len([c for c in provider.calls if not c[0]]) == 1
    with engine.ledger.db() as db:
        count = db.execute("SELECT count(*) FROM account_locks").fetchone()[0]
    assert count == (0 if changed is None else 1)
