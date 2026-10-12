from __future__ import annotations

import fcntl
import re
from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import uuid4

from .config import Settings
from .errors import AdsError, safe_error
from .ledger import Ledger
from .models import Account, PlanInput, digest
from .planner import compile_plan, verify
from .provider import utc_now


class Engine:
    def __init__(self, path: Path, settings: Settings, provider):
        self.path, self.settings, self.provider = path, settings, provider
        self.ledger = Ledger(path)

    def check_integrity(self, plan):
        body = plan["body"]
        if digest(body) != plan["plan_hash"]:
            raise AdsError("PLAN_BINDING_CHANGED", "计划内容已变化")
        self.settings.policy(Account.model_validate(body["request"]["account"]))

    def check_binding(self, plan):
        self.check_integrity(plan)
        body = plan["body"]
        if body["binding"] != self.settings.binding():
            raise AdsError("PLAN_BINDING_CHANGED", "计划或本机权限/授权配置已改变，需要重新预览")
        self.settings.policy(Account.model_validate(body["request"]["account"]), write=True)

    def check_fresh(self, plan):
        self.check_binding(plan)
        if datetime.now(UTC) >= datetime.fromisoformat(plan["body"]["expires_at"]):
            raise AdsError("PLAN_EXPIRED", "计划已过期，请使用新 request_key 重新预览")

    def preview(self, request: PlanInput):
        self.settings.policy(request.account, write=True)
        input_hash = digest(request.model_dump(mode="json"))
        existing = self.ledger.by_key(request.request_key)
        if existing:
            if existing["input_hash"] != input_hash:
                raise AdsError("IDEMPOTENCY_CONFLICT", "同一个 request_key 不能用于不同输入")
            self.check_binding(existing)
            return existing
        plan_id = uuid4().hex
        compiled = compile_plan(self.provider, self.settings, request, plan_id)
        self.provider.mutate(request.account, compiled["operations"], validate_only=True)
        body = {
            "request": request.model_dump(mode="json"),
            "binding": self.settings.binding(),
            "expires_at": (
                datetime.now(UTC) + timedelta(seconds=self.settings.plan_ttl_seconds)
            ).isoformat(),
            "prepared": compiled,
            "validation": {"source": self.provider.source, "at": utc_now(), "validate_only": True},
        }
        return self.ledger.insert(plan_id, request.request_key, input_hash, digest(body), body)

    def approval_candidate(self, plan_id):
        plan = self.ledger.get(plan_id)
        self.check_fresh(plan)
        if plan["status"] != "VALIDATED":
            raise AdsError("PLAN_NOT_APPROVABLE", "只有 VALIDATED 计划可以审批")
        return plan

    def submit(self, plan_id):
        if not re.fullmatch(r"[a-f0-9]{32}", plan_id):
            raise AdsError("INVALID_OPERATION_ID", "operation_id 必须是账本返回的 32 位标识")
        # Process lock distinguishes a crashed IN_FLIGHT from a still active sender.
        with open(self.path / f"submit-{plan_id}.lock", "a+") as lock:
            try:
                fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                raise AdsError("OPERATION_BUSY", "同一计划正在提交") from None
            plan = self.ledger.get(plan_id)
            self.check_binding(plan)
            if plan["status"] in {
                "VERIFIED",
                "RECONCILED",
                "UNKNOWN",
                "READBACK_MISMATCH",
                "IN_FLIGHT",
                "REJECTED",
                "STALE",
            }:
                return plan  # Never resend consumed or ambiguous operations.
            self.check_fresh(plan)
            account = Account.model_validate(plan["body"]["request"]["account"])
            self.ledger.claim(plan_id, account.customer_id)
            sent = False
            receipt_received = False
            try:
                request = PlanInput.model_validate(plan["body"]["request"])
                fresh = compile_plan(self.provider, self.settings, request, plan_id)
                if digest(fresh) != digest(plan["body"]["prepared"]):
                    raise AdsError(
                        "REMOTE_STATE_CHANGED", "远端状态或影响范围已变化，请重新预览及审批"
                    )
                self.provider.mutate(account, fresh["operations"], validate_only=True)
                sent = True
                response = self.provider.mutate(account, fresh["operations"], validate_only=False)
                receipt_received = True
                # Persist receipt before doing readback. Failure here still leaves IN_FLIGHT for reconciliation.
                self.ledger.finish(plan_id, "IN_FLIGHT", {"receipt": response}, release=False)
                result = verify(self.provider, account, fresh["checks"])
                self.ledger.finish(
                    plan_id,
                    "VERIFIED" if result["complete"] else "READBACK_MISMATCH",
                    {"receipt": response, "readback": result},
                    release=result["complete"],
                )
            except Exception as exc:
                error = safe_error(exc)
                definitive_rejection = (
                    sent and not receipt_received and error.code == "GOOGLE_REJECTED"
                )
                status = "REJECTED" if definitive_rejection else ("UNKNOWN" if sent else "STALE")
                # Preserve a known receipt if verification failed after a successful response.
                prior = self.ledger.get(plan_id)["result"] or {}
                self.ledger.finish(
                    plan_id,
                    status,
                    {**prior, "error": error.public()},
                    release=not sent or definitive_rejection,
                )
            return self.ledger.get(plan_id)

    def reconcile(self, plan_id):
        if not re.fullmatch(r"[a-f0-9]{32}", plan_id):
            raise AdsError("INVALID_OPERATION_ID", "operation_id 必须是账本返回的 32 位标识")
        with open(self.path / f"submit-{plan_id}.lock", "a+") as lock:
            try:
                fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                raise AdsError("OPERATION_BUSY", "提交仍在运行，暂不核对") from None
            plan = self.ledger.get(plan_id)
            # Read-only recovery must remain available after credentials rotate or writes are disabled.
            self.check_integrity(plan)
            if plan["status"] not in {"UNKNOWN", "READBACK_MISMATCH", "IN_FLIGHT"}:
                return plan
            account = Account.model_validate(plan["body"]["request"]["account"])
            evidence = verify(self.provider, account, plan["body"]["prepared"]["checks"])
            prior = plan["result"] or {}
            self.ledger.finish(
                plan_id,
                "RECONCILED" if evidence["complete"] else "UNKNOWN",
                {
                    **prior,
                    "reconciliation": evidence,
                    "next_action": None
                    if evidence["complete"]
                    else "保持锁定；无结果不等于未执行，禁止重发。人工核对 Google Ads 更改历史。",
                },
                release=evidence["complete"],
            )
            return self.ledger.get(plan_id)
