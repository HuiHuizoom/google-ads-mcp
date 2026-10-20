"""SQLite compare-and-swap state machine and per-customer submission lock."""

from __future__ import annotations

import json
import os
import sqlite3
from contextlib import contextmanager
from pathlib import Path

from .errors import AdsError
from .models import canonical
from .provider import utc_now


class Ledger:
    def __init__(self, directory: Path):
        self.file = directory / "operations.sqlite3"
        if self.file.is_symlink():
            raise AdsError("UNSAFE_STATE_PATH", "账本不能是符号链接")
        fd = os.open(self.file, os.O_CREAT | os.O_RDWR, 0o600)
        os.close(fd)
        self.file.chmod(0o600)
        with self.db() as db:
            db.executescript("""
                CREATE TABLE IF NOT EXISTS plans (
                    id TEXT PRIMARY KEY, request_key TEXT UNIQUE NOT NULL,
                    input_hash TEXT NOT NULL, plan_hash TEXT NOT NULL,
                    body TEXT NOT NULL, status TEXT NOT NULL,
                    approval_hash TEXT, approval_method TEXT,
                    result TEXT, created_at TEXT NOT NULL, updated_at TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS account_locks (customer_id TEXT PRIMARY KEY, plan_id TEXT UNIQUE);
                CREATE TABLE IF NOT EXISTS events (seq INTEGER PRIMARY KEY AUTOINCREMENT,
                    plan_id TEXT NOT NULL, at TEXT NOT NULL, event TEXT NOT NULL, data TEXT NOT NULL);
            """)

    @contextmanager
    def db(self):
        conn = sqlite3.connect(self.file, timeout=10)
        conn.row_factory = sqlite3.Row
        try:
            conn.execute("PRAGMA foreign_keys=ON")
            conn.execute("BEGIN IMMEDIATE")
            yield conn
            conn.commit()
        except BaseException:
            conn.rollback()
            raise
        finally:
            conn.close()

    @staticmethod
    def decode(row):
        if row is None:
            raise AdsError("PLAN_NOT_FOUND", "找不到变更计划")
        result = dict(row)
        result["body"] = json.loads(result["body"])
        result["result"] = json.loads(result["result"]) if result["result"] else None
        return result

    def get(self, plan_id):
        with self.db() as db:
            return self.decode(db.execute("SELECT * FROM plans WHERE id=?", (plan_id,)).fetchone())

    def by_key(self, key):
        with self.db() as db:
            row = db.execute("SELECT * FROM plans WHERE request_key=?", (key,)).fetchone()
            return self.decode(row) if row else None

    def insert(self, plan_id, key, input_hash, plan_hash, body):
        with self.db() as db:
            existing = db.execute("SELECT * FROM plans WHERE request_key=?", (key,)).fetchone()
            if existing:
                if existing["input_hash"] != input_hash:
                    raise AdsError("IDEMPOTENCY_CONFLICT", "request_key 已用于不同输入")
                return self.decode(existing)
            now = utc_now()
            db.execute(
                "INSERT INTO plans(id,request_key,input_hash,plan_hash,body,status,created_at,updated_at) "
                "VALUES(?,?,?,?,?,'VALIDATED',?,?)",
                (plan_id, key, input_hash, plan_hash, canonical(body), now, now),
            )
            self.event(db, plan_id, "VALIDATED", {})
        return self.get(plan_id)

    @staticmethod
    def event(db, plan_id, event, data):
        db.execute(
            "INSERT INTO events(plan_id,at,event,data) VALUES(?,?,?,?)",
            (plan_id, utc_now(), event, canonical(data)),
        )

    def claim(self, plan_id, customer_id):
        with self.db() as db:
            row = self.decode(db.execute("SELECT * FROM plans WHERE id=?", (plan_id,)).fetchone())
            # APPROVED and the approval columns are retained only for existing ledgers.
            # Engine.submit checks integrity, permissions and expiry for both states.
            if row["status"] not in {"VALIDATED", "APPROVED"}:
                raise AdsError("PLAN_NOT_SUBMITTABLE", "计划状态不允许提交，请查看操作记录")
            try:
                db.execute("INSERT INTO account_locks VALUES(?,?)", (customer_id, plan_id))
            except sqlite3.IntegrityError:
                raise AdsError(
                    "ACCOUNT_BUSY", "该账户有提交中或结果不明的操作，请先 reconcile"
                ) from None
            db.execute(
                "UPDATE plans SET status='IN_FLIGHT',updated_at=? WHERE id=?", (utc_now(), plan_id)
            )
            self.event(db, plan_id, "IN_FLIGHT", {})

    def finish(self, plan_id, status, result, release=True):
        with self.db() as db:
            db.execute(
                "UPDATE plans SET status=?,result=?,updated_at=? WHERE id=?",
                (status, canonical(result), utc_now(), plan_id),
            )
            if release:
                db.execute("DELETE FROM account_locks WHERE plan_id=?", (plan_id,))
            self.event(db, plan_id, status, result)

    def events(self, plan_id):
        with self.db() as db:
            return [
                dict(r)
                for r in db.execute(
                    "SELECT seq,at,event,data FROM events WHERE plan_id=? ORDER BY seq", (plan_id,)
                )
            ]
