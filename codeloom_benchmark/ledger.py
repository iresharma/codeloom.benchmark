"""Durable integer-money reservations shared by all trials and processes."""

from __future__ import annotations

import contextlib
import secrets
import sqlite3
from decimal import Decimal, ROUND_CEILING
from pathlib import Path

MICRO = Decimal(1_000_000)


def micros(dollars) -> int:
    return int(
        (Decimal(str(dollars)) * MICRO).to_integral_value(rounding=ROUND_CEILING)
    )


class BudgetRefused(RuntimeError):
    def __init__(self, message, reason="budget_exhausted"):
        super().__init__(message)
        self.reason = reason


class Ledger:
    def __init__(
        self,
        path: Path,
        campaign_limit="45",
        stage_limits=None,
        budget_policy=None,
        call_limit_multiplier=3,
    ):
        self.path = path
        path.parent.mkdir(parents=True, exist_ok=True)
        with self.connection() as db:
            db.executescript("""
            CREATE TABLE IF NOT EXISTS settings(key TEXT PRIMARY KEY, value INTEGER NOT NULL);
            CREATE TABLE IF NOT EXISTS stages(name TEXT PRIMARY KEY, ceiling INTEGER NOT NULL);
            CREATE TABLE IF NOT EXISTS trials(id TEXT PRIMARY KEY, token TEXT UNIQUE NOT NULL,
                stage TEXT NOT NULL, ceiling INTEGER NOT NULL, active INTEGER NOT NULL DEFAULT 1);
            CREATE TABLE IF NOT EXISTS campaign_runs(kind TEXT PRIMARY KEY, root TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS calls(id TEXT PRIMARY KEY, trial_id TEXT NOT NULL,
                reserved INTEGER NOT NULL, charge INTEGER, status TEXT NOT NULL DEFAULT 'pending');
            """)
            ceiling = micros(campaign_limit)
            old = db.execute(
                "SELECT value FROM settings WHERE key='ceiling'"
            ).fetchone()
            if old and old[0] != ceiling:
                raise ValueError("Campaign ceiling cannot change on resume")
            db.execute(
                "INSERT OR IGNORE INTO settings VALUES('ceiling', ?)", (ceiling,)
            )
            for name, limit in (
                stage_limits or {"development": "8", "measured": "32", "recovery": "5"}
            ).items():
                value = micros(limit)
                old = db.execute(
                    "SELECT ceiling FROM stages WHERE name=?", (name,)
                ).fetchone()
                if old and old[0] != value:
                    raise ValueError("Stage ceiling cannot change on resume")
                db.execute("INSERT OR IGNORE INTO stages VALUES(?,?)", (name, value))
            stored = db.execute(
                "SELECT value FROM settings WHERE key='actual_spend_policy'"
            ).fetchone()
            # Older ledgers retain strict reservations until explicitly migrated.
            previous = bool(stored and stored[0])
            if budget_policy is not None:
                if budget_policy not in ("strict-reservation", "actual-spend"):
                    raise ValueError("Unknown budget policy")
                requested = budget_policy == "actual-spend"
                if (
                    requested != previous
                    and db.execute("SELECT 1 FROM calls LIMIT 1").fetchone()
                ):
                    raise ValueError(
                        "Budget policy changed; explicitly migrate the settled development ledger"
                    )
                db.execute(
                    "INSERT OR REPLACE INTO settings VALUES('actual_spend_policy',?)",
                    (int(requested),),
                )
                old = db.execute(
                    "SELECT value FROM settings WHERE key='call_limit_multiplier'"
                ).fetchone()
                if old and old[0] != call_limit_multiplier:
                    raise ValueError("Call multiplier cannot change on resume")
                if type(call_limit_multiplier) is not int or call_limit_multiplier < 1:
                    raise ValueError("Call multiplier must be a positive integer")
                db.execute(
                    "INSERT OR IGNORE INTO settings VALUES('call_limit_multiplier',?)",
                    (call_limit_multiplier,),
                )

    @staticmethod
    def _actual_policy(db):
        row = db.execute(
            "SELECT value FROM settings WHERE key='actual_spend_policy'"
        ).fetchone()
        return bool(row and row[0])

    @staticmethod
    def _multiplier(db):
        row = db.execute(
            "SELECT value FROM settings WHERE key='call_limit_multiplier'"
        ).fetchone()
        return row[0] if row else 3

    def migrate_to_actual_spend(self, call_limit_multiplier=3):
        """Owner-authorized change before calibration/measurement; never reset charges."""
        if type(call_limit_multiplier) is not int or call_limit_multiplier < 1:
            raise ValueError("Call multiplier must be a positive integer")
        with self.connection() as db:
            db.execute("BEGIN IMMEDIATE")
            if self._actual_policy(db):
                if self._multiplier(db) != call_limit_multiplier:
                    raise ValueError("Call multiplier cannot change on resume")
                return
            if (
                db.execute(
                    "SELECT 1 FROM trials WHERE active=1 OR stage!='development' LIMIT 1"
                ).fetchone()
                or db.execute(
                    "SELECT 1 FROM calls WHERE charge IS NULL LIMIT 1"
                ).fetchone()
                or db.execute("SELECT 1 FROM campaign_runs LIMIT 1").fetchone()
                or db.execute(
                    "SELECT 1 FROM settings WHERE key IN ('overrun','protocol_violation')"
                ).fetchone()
            ):
                raise ValueError(
                    "Policy migration requires settled development-only evidence before preregistration"
                )
            db.execute(
                "INSERT OR REPLACE INTO settings VALUES('actual_spend_policy',1)"
            )
            db.execute(
                "INSERT OR REPLACE INTO settings VALUES('call_limit_multiplier',?)",
                (call_limit_multiplier,),
            )

    @contextlib.contextmanager
    def connection(self):
        db = sqlite3.connect(self.path, timeout=30)
        try:
            db.execute("PRAGMA busy_timeout=30000")
            yield db
            db.commit()
        except BaseException:
            db.rollback()
            raise
        finally:
            db.close()

    def create_trial(self, trial_id: str, stage: str, limit) -> str:
        token = secrets.token_urlsafe(32)
        with self.connection() as db:
            if db.execute("SELECT 1 FROM settings WHERE key='overrun'").fetchone():
                raise BudgetRefused("Campaign halted after provider price overrun")
            if db.execute(
                "SELECT 1 FROM settings WHERE key='protocol_violation'"
            ).fetchone():
                raise BudgetRefused("Campaign halted after provider identity mismatch")
            if db.execute(
                "SELECT 1 FROM calls WHERE charge IS NULL AND status != 'pending' LIMIT 1"
            ).fetchone():
                raise BudgetRefused(
                    "Campaign halted until unknown bills are reconciled"
                )
            if not db.execute("SELECT 1 FROM stages WHERE name=?", (stage,)).fetchone():
                raise ValueError("Unknown budget stage")
            db.execute(
                "INSERT INTO trials(id,token,stage,ceiling) VALUES(?,?,?,?)",
                (trial_id, token, stage, micros(limit)),
            )
        return token

    def revoke(self, trial_id: str) -> None:
        with self.connection() as db:
            db.execute("UPDATE trials SET active=0 WHERE id=?", (trial_id,))

    @staticmethod
    def _used(db, where="", params=(), actual_only=False):
        money = (
            "COALESCE(c.charge,0)" if actual_only else "COALESCE(c.charge,c.reserved)"
        )
        return db.execute(
            f"SELECT COALESCE(SUM({money}),0) FROM calls c "
            "JOIN trials t ON t.id=c.trial_id " + where,
            params,
        ).fetchone()[0]

    def reserve(self, token: str, amount: int) -> tuple[str, str]:
        if amount <= 0:
            raise ValueError("Reservation must be positive")
        with self.connection() as db:
            db.execute("BEGIN IMMEDIATE")
            if db.execute("SELECT 1 FROM settings WHERE key='overrun'").fetchone():
                raise BudgetRefused("Campaign halted after provider price overrun")
            if db.execute(
                "SELECT 1 FROM settings WHERE key='protocol_violation'"
            ).fetchone():
                raise BudgetRefused("Campaign halted after provider identity mismatch")
            if db.execute(
                "SELECT 1 FROM calls WHERE charge IS NULL AND status != 'pending' LIMIT 1"
            ).fetchone():
                raise BudgetRefused(
                    "Campaign halted until unknown bills are reconciled"
                )
            t = db.execute(
                "SELECT id,stage,ceiling,active FROM trials WHERE token=?", (token,)
            ).fetchone()
            if not t or not t[3]:
                raise BudgetRefused("Unknown or revoked trial token", "token_denied")
            campaign = db.execute(
                "SELECT value FROM settings WHERE key='ceiling'"
            ).fetchone()[0]
            stage = db.execute(
                "SELECT ceiling FROM stages WHERE name=?", (t[1],)
            ).fetchone()[0]
            actual = self._actual_policy(db)
            for used, limit in [
                (self._used(db, actual_only=actual), campaign),
                (self._used(db, "WHERE t.stage=?", (t[1],), actual), stage),
                (self._used(db, "WHERE t.id=?", (t[0],), actual), t[2]),
            ]:
                exhausted = used >= limit if actual else used + amount > limit
                if exhausted:
                    raise BudgetRefused(
                        "Actual spending target reached"
                        if actual
                        else "Insufficient reserved budget"
                    )
            if actual and amount > self._multiplier(db) * t[2]:
                raise BudgetRefused(
                    f"Estimated call ${amount / 1_000_000:.6f} exceeds "
                    f"{self._multiplier(db)}x trial budget (${self._multiplier(db) * t[2] / 1_000_000:.6f})",
                    "call_cost_limit",
                )
            if (
                actual
                and db.execute(
                    "SELECT 1 FROM calls WHERE trial_id=? AND status='pending' LIMIT 1",
                    (t[0],),
                ).fetchone()
            ):
                raise BudgetRefused(
                    "Waiting for this trial's in-flight bill", "budget_pending"
                )
            call_id = secrets.token_hex(12)
            db.execute(
                "INSERT INTO calls(id,trial_id,reserved) VALUES(?,?,?)",
                (call_id, t[0], amount),
            )
            return call_id, t[0]

    def trial_for_token(self, token):
        with self.connection() as db:
            row = db.execute("SELECT id FROM trials WHERE token=?", (token,)).fetchone()
            return row[0] if row else None

    def pending_can_release_budget(self, token, amount):
        with self.connection() as db:
            if db.execute(
                "SELECT 1 FROM calls WHERE charge IS NULL AND status != 'pending' LIMIT 1"
            ).fetchone():
                return False
            if db.execute(
                "SELECT 1 FROM settings WHERE key IN ('overrun','protocol_violation')"
            ).fetchone():
                return False
            trial = db.execute(
                "SELECT ceiling,active,id,stage FROM trials WHERE token=?", (token,)
            ).fetchone()
            if self._actual_policy(db):
                if (
                    not trial
                    or not trial[1]
                    or amount > self._multiplier(db) * trial[0]
                ):
                    return False
                campaign = db.execute(
                    "SELECT value FROM settings WHERE key='ceiling'"
                ).fetchone()[0]
                stage = db.execute(
                    "SELECT ceiling FROM stages WHERE name=?", (trial[3],)
                ).fetchone()[0]
                return bool(
                    self._used(db, actual_only=True) < campaign
                    and self._used(db, "WHERE t.stage=?", (trial[3],), True) < stage
                    and self._used(db, "WHERE t.id=?", (trial[2],), True) < trial[0]
                    and db.execute(
                        "SELECT 1 FROM calls WHERE trial_id=? AND status='pending' LIMIT 1",
                        (trial[2],),
                    ).fetchone()
                )
            return bool(
                trial
                and trial[1]
                and amount <= trial[0]
                and db.execute(
                    "SELECT 1 FROM calls WHERE status='pending' LIMIT 1"
                ).fetchone()
            )

    def settle(self, call_id: str, dollars=None, status="complete") -> None:
        # Unknown bills keep their entire reservation, including after a process crash.
        with self.connection() as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute(
                "SELECT reserved,status FROM calls WHERE id=?", (call_id,)
            ).fetchone()
            if not row or row[1] != "pending":
                raise ValueError("Unknown or already settled call")
            charge = None if dollars is None else micros(dollars)
            if charge is not None and charge < 0:
                raise ValueError("Negative provider cost")
            db.execute(
                "UPDATE calls SET charge=?,status=? WHERE id=?",
                (charge, status, call_id),
            )
            if charge is not None and charge > row[0]:
                db.execute("UPDATE trials SET active=0")
                db.execute("INSERT OR REPLACE INTO settings VALUES('overrun',1)")
            if status in ("model_mismatch", "provider_mismatch"):
                db.execute("UPDATE trials SET active=0")
                db.execute(
                    "INSERT OR REPLACE INTO settings VALUES('protocol_violation',1)"
                )

    def unknown_calls_for_trial(self, trial_id):
        with self.connection() as db:
            return db.execute(
                "SELECT COUNT(*) FROM calls WHERE trial_id=? AND charge IS NULL",
                (trial_id,),
            ).fetchone()[0]

    def snapshot(self):
        with self.connection() as db:
            return {
                "budget_policy": "actual-spend"
                if self._actual_policy(db)
                else "strict-reservation",
                "call_limit_multiplier": self._multiplier(db),
                "actual_spent_usd": self._used(db, actual_only=True) / 1_000_000,
                "spent_or_reserved_usd": self._used(db) / 1_000_000,
                "unknown_calls": db.execute(
                    "SELECT COUNT(*) FROM calls WHERE charge IS NULL"
                ).fetchone()[0],
                "overrun": bool(
                    db.execute("SELECT 1 FROM settings WHERE key='overrun'").fetchone()
                ),
                "protocol_violation": bool(
                    db.execute(
                        "SELECT 1 FROM settings WHERE key='protocol_violation'"
                    ).fetchone()
                ),
                "trials": [
                    {
                        "id": t[0],
                        "stage": t[1],
                        "cost_usd": self._used(db, "WHERE t.id=?", (t[0],)) / 1_000_000,
                        "actual_spent_usd": self._used(
                            db, "WHERE t.id=?", (t[0],), True
                        )
                        / 1_000_000,
                    }
                    for t in db.execute(
                        "SELECT id,stage FROM trials ORDER BY id"
                    ).fetchall()
                ],
            }

    def claim_run(self, kind: str, root: Path):
        with self.connection() as db:
            db.execute("BEGIN IMMEDIATE")
            previous = db.execute(
                "SELECT root FROM campaign_runs WHERE kind=?", (kind,)
            ).fetchone()
            value = str(root.resolve())
            if previous and previous[0] != value:
                raise ValueError(
                    "This campaign already owns a measured run; resume its original directory"
                )
            db.execute("INSERT OR IGNORE INTO campaign_runs VALUES(?,?)", (kind, value))

    def active_trials(self):
        with self.connection() as db:
            return [
                row[0] for row in db.execute("SELECT id FROM trials WHERE active=1")
            ]

    def headroom(self, stage: str) -> float:
        with self.connection() as db:
            c = db.execute("SELECT value FROM settings WHERE key='ceiling'").fetchone()[
                0
            ]
            s = db.execute(
                "SELECT ceiling FROM stages WHERE name=?", (stage,)
            ).fetchone()[0]
            return (
                min(
                    c - self._used(db, actual_only=self._actual_policy(db)),
                    s
                    - self._used(
                        db, "WHERE t.stage=?", (stage,), self._actual_policy(db)
                    ),
                )
                / 1_000_000
            )
