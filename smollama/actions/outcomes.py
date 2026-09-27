"""Action and outcome log.

Every proposal is recorded — permitted or refused, executed or dry-run. The
refusals are the interesting half: a week of dry-run logs is the de-risking step
before anything is allowed to move, and what the envelope *stopped* says more about
whether the bounds are right than what it allowed.

The outcome half exists because learning needs an explicit causal record rather
than "observe later". It carries a `confounders` field on purpose: if humidity falls
after a setpoint change it may have been the thermostat, the weather, or an open
window. You cannot A/B test a house, so the schema records the ambiguity instead of
implying the correlation was causal.
"""

import logging
import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path

from ..timeutil import normalize_ts, to_utc_iso

logger = logging.getLogger(__name__)

SCHEMA = """
CREATE TABLE IF NOT EXISTS action_log (
    id                         INTEGER PRIMARY KEY AUTOINCREMENT,
    target                     TEXT NOT NULL,
    value                      REAL,
    value_before               REAL,
    value_after                REAL,
    rule_id                    INTEGER,
    reason                     TEXT,
    permitted                  INTEGER NOT NULL,
    dry_run                    INTEGER NOT NULL,
    executed                   INTEGER NOT NULL DEFAULT 0,
    refusal                    TEXT,
    created_at                 TEXT NOT NULL,
    observation_window_seconds REAL,
    verdict                    TEXT,
    confounders                TEXT
);
CREATE INDEX IF NOT EXISTS idx_action_target_time ON action_log(target, created_at);
CREATE INDEX IF NOT EXISTS idx_action_rule ON action_log(rule_id, created_at);
"""


class OutcomeLog:
    def __init__(self, db_path: str):
        self.db_path = Path(db_path).expanduser()
        self._conn: sqlite3.Connection | None = None

    def connect(self) -> sqlite3.Connection:
        if self._conn is None:
            self.db_path.parent.mkdir(parents=True, exist_ok=True)
            # check_same_thread=False because the dashboard may read the action log from FastAPI's thread pool.
            # Matches LocalStore, which has always done this; WAL mode keeps
            # readers and writers from blocking each other.
            self._conn = sqlite3.connect(str(self.db_path), check_same_thread=False)
            self._conn.row_factory = sqlite3.Row
            self._conn.execute("PRAGMA journal_mode=WAL")
            self._conn.executescript(SCHEMA)
            self._conn.commit()
        return self._conn

    def close(self) -> None:
        if self._conn is not None:
            self._conn.commit()
            self._conn.close()
            self._conn = None

    def record(self, decision, *, executed: bool, now: datetime | None = None) -> dict:
        """Record a decision. Refusals are recorded too, deliberately."""
        now = now or datetime.now(timezone.utc)
        conn = self.connect()
        cur = conn.execute(
            "INSERT INTO action_log (target, value, value_before, rule_id, reason, "
            "permitted, dry_run, executed, refusal, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                decision.request.target,
                decision.request.value,
                decision.request.current_value,
                decision.request.rule_id,
                decision.request.reason,
                1 if decision.permitted else 0,
                1 if decision.dry_run else 0,
                1 if executed else 0,
                decision.refusal,
                to_utc_iso(now),
            ),
        )
        conn.commit()
        return self.get(cur.lastrowid)

    def record_outcome(
        self,
        action_id: int,
        *,
        value_after: float | None = None,
        observation_window_seconds: float | None = None,
        verdict: str | None = None,
        confounders: str | None = None,
    ) -> None:
        """Attach the observed result. `confounders` is not optional in spirit —
        an outcome recorded without noting what else changed reads as causal
        evidence it isn't."""
        conn = self.connect()
        conn.execute(
            "UPDATE action_log SET value_after = ?, observation_window_seconds = ?, "
            "verdict = ?, confounders = ? WHERE id = ?",
            (value_after, observation_window_seconds, verdict, confounders, action_id),
        )
        conn.commit()

    def get(self, action_id: int) -> dict | None:
        row = self.connect().execute(
            "SELECT * FROM action_log WHERE id = ?", (action_id,)
        ).fetchone()
        return dict(row) if row else None

    def recent(self, limit: int = 50) -> list[dict]:
        return [
            dict(r)
            for r in self.connect().execute(
                "SELECT * FROM action_log ORDER BY created_at DESC, id DESC LIMIT ?",
                (limit,),
            )
        ]

    def last_action_at(self, target: str, *, now=None) -> datetime | None:
        """When this target was last acted on — permitted proposals only.

        A refusal is not an action, so it must not start a cooldown; otherwise one
        rejected proposal would suppress the next legitimate one.
        """
        row = self.connect().execute(
            "SELECT created_at FROM action_log WHERE target = ? AND permitted = 1 "
            "ORDER BY created_at DESC LIMIT 1",
            (target,),
        ).fetchone()
        return normalize_ts(row["created_at"]) if row else None

    def count_since(self, target: str, *, seconds: float, now=None) -> int:
        now = now or datetime.now(timezone.utc)
        floor = to_utc_iso(now - timedelta(seconds=seconds))
        row = self.connect().execute(
            "SELECT COUNT(*) AS n FROM action_log WHERE target = ? AND permitted = 1 "
            "AND created_at >= ?",
            (target, floor),
        ).fetchone()
        return row["n"] if row else 0

    def suppressed_until(self, *, rule_id: int, envelope, now=None) -> datetime | None:
        """When a rule's post-action suppression expires, or None if it is free.

        This is the mechanism behind *action is not resolution*: acting on a rule
        quiets it for a cooldown so the action has time to take effect, then it
        evaluates again. Retiring it instead would mean never learning that the
        action failed or that the condition came back.
        """
        now = now or datetime.now(timezone.utc)
        row = self.connect().execute(
            "SELECT target, created_at FROM action_log WHERE rule_id = ? "
            "AND permitted = 1 ORDER BY created_at DESC LIMIT 1",
            (rule_id,),
        ).fetchone()
        if not row:
            return None
        actuator = envelope.actuators.get(row["target"])
        if actuator is None or not actuator.cooldown_seconds:
            return None
        expiry = normalize_ts(row["created_at"]) + timedelta(
            seconds=actuator.cooldown_seconds
        )
        return expiry if expiry > now else None
