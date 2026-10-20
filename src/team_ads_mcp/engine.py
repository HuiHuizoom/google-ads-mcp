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
from .planner import compile_plan, reconciliation_checks, verify
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
            "request": request.model_dump(mode="json", exclude_none=True),
            "binding": self.settings.binding(),
            "expires_at": (
                datetime.now(UTC) + timedelta(seconds=self.settings.plan_ttl_seconds)
            ).isoformat(),
            "prepared": compiled,
            "validation": {"source": self.provider.source, "at": utc_now(), "validate_only": True},
        }
        return self.ledger.insert(plan_id, request.request_key, input_hash, digest(body), body)

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
                "ABANDONED_UNKNOWN",
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
                    raise AdsError("REMOTE_STATE_CHANGED", "远端状态或影响范围已变化，请重新预览")
                self.provider.mutate(account, fresh["operations"], validate_only=True)
                sent = True
                response = self.provider.mutate(account, fresh["operations"], validate_only=False)
                receipt_received = True
                # Persist receipt before doing readback. Failure here still leaves IN_FLIGHT for reconciliation.
                self.ledger.finish(plan_id, "IN_FLIGHT", {"receipt": response}, release=False)
                result = verify(self.provider, account, fresh["checks"], receipt=response)
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

    def abandon_unknown_creation(self, plan_id, expected_hash, reason):
        """Explicit operator recovery; absence is NOT proof of non-execution."""
        if not re.fullmatch(r"[a-f0-9]{32}", plan_id):
            raise AdsError("INVALID_OPERATION_ID", "operation_id 必须是账本返回的 32 位标识")
        if not reason.strip() or len(reason) > 1000:
            raise AdsError("RECOVERY_REASON_REQUIRED", "必须记录人工核对和接受未知结果风险的原因")
        with open(self.path / f"submit-{plan_id}.lock", "a+") as lock:
            try:
                fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                raise AdsError("OPERATION_BUSY", "提交仍在运行，不能放弃") from None
            plan = self.ledger.get(plan_id)
            self.check_integrity(plan)
            if expected_hash != plan["plan_hash"]:
                raise AdsError("PLAN_BINDING_CHANGED", "人工确认的计划 hash 不匹配")
            if plan["status"] == "ABANDONED_UNKNOWN":
                return plan
            if (
                plan["status"] != "UNKNOWN"
                or plan["body"]["request"]["change"]["kind"] != "create_search"
                or "receipt" in (plan["result"] or {})
                or (plan["result"] or {}).get("error", {}).get("code") != "TRANSPORT_UNKNOWN"
            ):
                raise AdsError(
                    "RECOVERY_NOT_ALLOWED", "仅允许人工放弃无回执的 UNKNOWN 暂停 Search 创建"
                )
            # Only the original compiler's bounded PAUSED creation is eligible.
            allowed = {
                "campaign_budget_operation",
                "campaign_operation",
                "campaign_criterion_operation",
                "ad_group_operation",
                "ad_group_criterion_operation",
                "ad_group_ad_operation",
            }
            operations = plan["body"]["prepared"]["operations"]
            paused = {"campaign_operation", "ad_group_operation", "ad_group_ad_operation"}
            seen = set()
            for operation in operations:
                if len(operation) != 1:
                    raise AdsError("RECOVERY_NOT_ALLOWED", "计划不是受限的暂停创建")
                kind, action = next(iter(operation.items()))
                if kind not in allowed or set(action) != {"create"}:
                    raise AdsError("RECOVERY_NOT_ALLOWED", "计划包含非创建操作")
                if kind in paused:
                    seen.add(kind)
                    if action["create"].get("status") != "PAUSED":
                        raise AdsError("RECOVERY_NOT_ALLOWED", "计划存在非暂停对象")
            if seen != paused:
                raise AdsError("RECOVERY_NOT_ALLOWED", "缺少完整的暂停创建结构")
            events = self.ledger.events(plan_id)
            sent_at = next(e["at"] for e in events if e["event"] == "IN_FLIGHT")
            if (datetime.now(UTC) - datetime.fromisoformat(sent_at)).total_seconds() < 1800:
                raise AdsError(
                    "RECOVERY_TOO_EARLY", "提交后不足 30 分钟；等待和空结果都不能证明未执行"
                )
            account = Account.model_validate(plan["body"]["request"]["account"])
            checks, _ = reconciliation_checks(plan)
            evidence = verify(
                self.provider, account, checks, receipt=(plan["result"] or {}).get("receipt")
            )
            if not evidence["evidence"] or any(e["observed"] for e in evidence["evidence"]):
                raise AdsError(
                    "RECOVERY_OBJECTS_FOUND", "已发现远端对象，必须核对或定向清理，不能放弃解锁"
                )
            self.ledger.finish(
                plan_id,
                "ABANDONED_UNKNOWN",
                {
                    **(plan["result"] or {}),
                    "manual_resolution": {
                        "at": utc_now(),
                        "reason": reason,
                        "plan_hash": expected_hash,
                        "evidence": evidence,
                        "outcome": "unknown_not_cancelled",
                        "warning": "仅人工接受未知结果并解除阻塞，不证明未执行或取消；旧请求永不重发，需核查迟到的暂停对象",
                    },
                    "next_action": "核查旧标记是否迟到出现；不得重发此计划",
                },
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
            checks, corrections = reconciliation_checks(plan)
            evidence = verify(
                self.provider, account, checks, receipt=(plan["result"] or {}).get("receipt")
            )
            if corrections:
                evidence["check_compatibility"] = corrections
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
