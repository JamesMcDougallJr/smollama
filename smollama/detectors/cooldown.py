"""Cooldown on re-narrating an unchanged signal.

Measured on the live master: an uncovered signal cost one model call and one stored
observation per cycle, forever. The stuck hcsr04 flatline produced 58 near-identical
observations in 23 hours. The prompt asks the model to omit a deviation a past
observation already reports, and qwen2.5:1.5b does not honour that, so the fix is
structural rather than another instruction.

The hard part is deciding when a signal is genuinely new again:

- it cleared and returned                  -> new
- its direction flipped                    -> new (a real change, not a repeat)
- its score escalated materially           -> new
- the winner of a correlated pair swapped  -> not new (one event, other name)

A signal is recorded only after the model actually answered. Recording on the way in
would silence a signal for the whole cooldown when the LLM was down, without it ever
having been reported.

Persisted in memory.db so a restart does not re-narrate everything. This gates
narration only; rule evaluation still sees every signal.
"""

import sqlite3
from datetime import datetime, timezone
from pathlib import Path

from ..timeutil import normalize_ts, to_utc_iso
from .core import correlation_group

SCHEMA = """
CREATE TABLE IF NOT EXISTS signal_narrations (
    source TEXT NOT NULL,
    detector TEXT NOT NULL,
    direction TEXT,
    last_narrated_at TEXT NOT NULL,
    last_score REAL NOT NULL,
    times_narrated INTEGER NOT NULL DEFAULT 1,
    PRIMARY KEY (source, detector)
);
"""


def _group(source: str, detector: str) -> tuple:
    """The event a (source, detector) belongs to, for pruning.

    A correlated metric shares a group with its twin, so a twin that is quiet for a
    cycle while its partner fires is not mistaken for a cleared signal.
    """
    return correlation_group(source, detector) or (source, detector)


class SignalCooldown:
    def __init__(
        self,
        db_path: str,
        cooldown_seconds: float = 12 * 3600.0,
        escalation_factor: float = 1.5,
    ):
        self.db_path = Path(db_path).expanduser()
        self.cooldown_seconds = cooldown_seconds
        self.escalation_factor = escalation_factor
        self._conn: sqlite3.Connection | None = None

    # ── connection ──────────────────────────────────────────────────────────
    def connect(self) -> sqlite3.Connection:
        if self._conn is None:
            self.db_path.parent.mkdir(parents=True, exist_ok=True)
            self._conn = sqlite3.connect(
                str(self.db_path), check_same_thread=False, timeout=10
            )
            self._conn.row_factory = sqlite3.Row
            self._conn.executescript(SCHEMA)
            self._conn.commit()
        return self._conn

    def close(self) -> None:
        if self._conn is not None:
            self._conn.commit()
            self._conn.close()
            self._conn = None

    def _c(self) -> sqlite3.Connection:
        return self.connect()

    # ── decisions ───────────────────────────────────────────────────────────
    def filter(self, signals, now: datetime | None = None) -> list:
        """The signals worth narrating now. Suppresses unchanged recent repeats."""
        if self.cooldown_seconds <= 0:
            return list(signals)
        now = now or datetime.now(timezone.utc)
        allowed = []
        for s in signals:
            row = self._c().execute(
                "SELECT direction, last_narrated_at, last_score "
                "FROM signal_narrations WHERE source = ? AND detector = ?",
                (s.source, s.detector),
            ).fetchone()
            if row is None:
                allowed.append(s)
                continue
            elapsed = (now - normalize_ts(row["last_narrated_at"])).total_seconds()
            if elapsed >= self.cooldown_seconds:
                allowed.append(s)  # a persistent problem is reminded about, not forgotten
            elif row["direction"] is not None and row["direction"] != s.direction:
                allowed.append(s)  # flipped: a real change
            elif s.score >= row["last_score"] * self.escalation_factor:
                allowed.append(s)  # materially worse
        return allowed

    def record(self, signals, now: datetime | None = None) -> None:
        """Mark signals as narrated. Call only after the model answered.

        A correlated twin is recorded too, with no direction (the pair moves in
        opposite directions), so the winner swapping next cycle is not a repeat.
        """
        if self.cooldown_seconds <= 0:
            return
        stamp = to_utc_iso(now or datetime.now(timezone.utc))
        conn = self._c()
        for s in signals:
            self._upsert(conn, s.source, s.detector, s.direction, stamp, s.score)
            for twin in s.meta.get("correlated", []) if s.meta else []:
                self._upsert(conn, twin, s.detector, None, stamp, s.score)
        conn.commit()

    @staticmethod
    def _upsert(conn, source, detector, direction, stamp, score) -> None:
        conn.execute(
            "INSERT INTO signal_narrations "
            "(source, detector, direction, last_narrated_at, last_score) "
            "VALUES (?, ?, ?, ?, ?) "
            "ON CONFLICT (source, detector) DO UPDATE SET "
            "direction = excluded.direction, "
            "last_narrated_at = excluded.last_narrated_at, "
            "last_score = excluded.last_score, "
            "times_narrated = times_narrated + 1",
            (source, detector, direction, stamp, score),
        )

    def forget_cleared(self, present: set[tuple[str, str]]) -> None:
        """Drop entries for signals that no longer fire, so a recurrence is new.

        `present` is the (source, detector) of every signal this cycle. Callers must
        not pass an empty set after a *failed* detection pass: that is
        indistinguishable from "everything cleared" and would re-narrate everything.
        """
        present_groups = {_group(src, det) for src, det in present}
        conn = self._c()
        rows = conn.execute("SELECT source, detector FROM signal_narrations").fetchall()
        stale = [
            (r["source"], r["detector"]) for r in rows
            if _group(r["source"], r["detector"]) not in present_groups
        ]
        if stale:
            conn.executemany(
                "DELETE FROM signal_narrations WHERE source = ? AND detector = ?", stale
            )
            conn.commit()
