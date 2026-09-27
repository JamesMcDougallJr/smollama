"""Persistent rule store.

Rules live in SQLite rather than a git-tracked YAML file: an LLM mutating a
versioned file is awkward, and the lifecycle counters (evaluations, fires,
last_fired) are write-heavy runtime state, not configuration.

Identity is `(full_id, detector, direction)`, enforced by a UNIQUE constraint.
That is structural on purpose — `cpu_temp > 80` and `cpu_temp >= 79.5` are
semantically one rule and textually two, so string-keyed dedup accumulates
near-duplicates. A proposal for an existing identity is an *update*, not a new
rule.

Retirement is a state transition, never a DELETE. The asymmetry that drives this:
a bad retained rule makes noise you notice, while a wrongly retired rule makes
silence you don't — which is how a dead camera writer went unreported for 19 days.

`threshold_spec` is optional because not every detector is level-based. `flatline`
and `stale` are structural — zero variance, or no data at all — and have nothing to
compare against a number. Fitting one anyway produces a technically-correct but
meaningless value (`fit:baseline+4mad` over a constant-0 series resolves to 0.0).
Thresholds belong to `level_shift`, `trend`, and `envelope`.
"""

import logging
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from ..timeutil import normalize_ts, to_utc_iso
from .fit import resolve_threshold

logger = logging.getLogger(__name__)

STATES = ("proposed", "active", "muted", "parked", "retired")

SCHEMA = """
CREATE TABLE IF NOT EXISTS rules (
    id                  INTEGER PRIMARY KEY AUTOINCREMENT,
    full_id             TEXT NOT NULL,
    detector            TEXT NOT NULL,
    direction           TEXT NOT NULL,
    threshold_spec      TEXT,
    threshold_value     REAL,
    threshold_fitted_at TEXT,
    sustain_seconds     REAL NOT NULL DEFAULT 0,
    rationale           TEXT,
    state               TEXT NOT NULL DEFAULT 'proposed',
    state_reason        TEXT,
    created_at          TEXT NOT NULL,
    updated_at          TEXT NOT NULL,
    evaluations         INTEGER NOT NULL DEFAULT 0,
    fired_count         INTEGER NOT NULL DEFAULT 0,
    last_fired          TEXT,
    last_evaluated      TEXT,
    UNIQUE (full_id, detector, direction)
);
CREATE INDEX IF NOT EXISTS idx_rules_state ON rules(state);
"""


@dataclass
class Rule:
    id: int | None
    full_id: str
    detector: str
    direction: str
    threshold_spec: str | None = None
    threshold_value: float | None = None
    threshold_fitted_at: str | None = None
    sustain_seconds: float = 0.0
    rationale: str | None = None
    state: str = "proposed"
    state_reason: str | None = None
    created_at: str | None = None
    updated_at: str | None = None
    evaluations: int = 0
    fired_count: int = 0
    last_fired: str | None = None
    last_evaluated: str | None = None

    @property
    def identity(self) -> tuple[str, str, str]:
        return (self.full_id, self.detector, self.direction)

    @property
    def fire_rate(self) -> float:
        """Fraction of evaluations in which this rule fired.

        The cheapest quality signal available: a rule firing in most evaluations
        is describing normal, not anomaly.
        """
        if not self.evaluations:
            return 0.0
        return self.fired_count / self.evaluations

    def age_days(self, now: datetime | None = None) -> float:
        if not self.created_at:
            return 0.0
        now = now or datetime.now(timezone.utc)
        return (now - normalize_ts(self.created_at)).total_seconds() / 86400.0


class RuleStore:
    def __init__(self, db_path: str):
        self.db_path = Path(db_path).expanduser()
        self._conn: sqlite3.Connection | None = None

    # ── connection ──────────────────────────────────────────────────────────
    def connect(self) -> sqlite3.Connection:
        if self._conn is None:
            self.db_path.parent.mkdir(parents=True, exist_ok=True)
            self._conn = sqlite3.connect(str(self.db_path))
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

    # ── reads ───────────────────────────────────────────────────────────────
    @staticmethod
    def _row(row) -> Rule:
        return Rule(**{k: row[k] for k in row.keys()})

    def get(self, rule_id: int) -> Rule | None:
        row = self._c().execute("SELECT * FROM rules WHERE id = ?", (rule_id,)).fetchone()
        return self._row(row) if row else None

    def find(self, full_id: str, detector: str, direction: str) -> Rule | None:
        row = self._c().execute(
            "SELECT * FROM rules WHERE full_id = ? AND detector = ? AND direction = ?",
            (full_id, detector, direction),
        ).fetchone()
        return self._row(row) if row else None

    def all_rules(self) -> list[Rule]:
        return [self._row(r) for r in self._c().execute("SELECT * FROM rules ORDER BY id")]

    def rules_in_state(self, state: str) -> list[Rule]:
        return [
            self._row(r)
            for r in self._c().execute(
                "SELECT * FROM rules WHERE state = ? ORDER BY id", (state,)
            )
        ]

    def active_rules(self) -> list[Rule]:
        return self.rules_in_state("active")

    # ── writes ──────────────────────────────────────────────────────────────
    def propose(
        self,
        full_id: str,
        detector: str,
        direction: str,
        *,
        threshold_spec: str | None = None,
        sustain_seconds: float = 0.0,
        rationale: str | None = None,
        now: datetime | None = None,
    ) -> Rule:
        """Create a rule, or update the existing rule with the same identity.

        New rules land `proposed`, never `active`: a rule encodes an assumption
        about what normal looks like, and nothing should become load-bearing
        without a human or an accumulation of clean evaluations behind it.
        """
        stamp = to_utc_iso(now or datetime.now(timezone.utc))
        existing = self.find(full_id, detector, direction)
        conn = self._c()

        if existing:
            # An update proposal, not a duplicate. State and counters are left
            # alone — re-proposing must not silently reactivate a muted rule.
            conn.execute(
                "UPDATE rules SET threshold_spec = COALESCE(?, threshold_spec), "
                "sustain_seconds = ?, rationale = COALESCE(?, rationale), "
                "updated_at = ? WHERE id = ?",
                (threshold_spec, sustain_seconds, rationale, stamp, existing.id),
            )
            conn.commit()
            return self.get(existing.id)

        cur = conn.execute(
            "INSERT INTO rules (full_id, detector, direction, threshold_spec, "
            "sustain_seconds, rationale, state, created_at, updated_at) "
            "VALUES (?, ?, ?, ?, ?, ?, 'proposed', ?, ?)",
            (full_id, detector, direction, threshold_spec, sustain_seconds,
             rationale, stamp, stamp),
        )
        conn.commit()
        return self.get(cur.lastrowid)

    def _set_state(self, rule_id: int, state: str, reason: str | None,
                   now: datetime | None = None) -> None:
        if state not in STATES:
            raise ValueError(f"unknown state {state!r}; expected one of {STATES}")
        stamp = to_utc_iso(now or datetime.now(timezone.utc))
        conn = self._c()
        conn.execute(
            "UPDATE rules SET state = ?, state_reason = ?, updated_at = ? WHERE id = ?",
            (state, reason, stamp, rule_id),
        )
        conn.commit()

    def promote(self, rule_id: int, reason: str | None = None,
                now: datetime | None = None) -> None:
        self._set_state(rule_id, "active", reason, now)

    def mute(self, rule_id: int, reason: str, now: datetime | None = None) -> None:
        """Mute a rule. A reason is required — an unexplained state change cannot
        be audited, and the reason log is the only window into whether automated
        judgement is any good."""
        if not reason:
            raise ValueError("mute requires a reason")
        self._set_state(rule_id, "muted", reason, now)

    def park(self, rule_id: int, reason: str, now: datetime | None = None) -> None:
        """Park a rule whose source has gone away — reversible, unlike retirement."""
        if not reason:
            raise ValueError("park requires a reason")
        self._set_state(rule_id, "parked", reason, now)

    def retire(self, rule_id: int, reason: str, now: datetime | None = None) -> None:
        if not reason:
            raise ValueError("retire requires a reason")
        self._set_state(rule_id, "retired", reason, now)

    def record_evaluation(self, rule_id: int, *, fired: bool,
                          now: datetime | None = None) -> None:
        stamp = to_utc_iso(now or datetime.now(timezone.utc))
        conn = self._c()
        if fired:
            conn.execute(
                "UPDATE rules SET evaluations = evaluations + 1, "
                "fired_count = fired_count + 1, last_fired = ?, last_evaluated = ? "
                "WHERE id = ?",
                (stamp, stamp, rule_id),
            )
        else:
            conn.execute(
                "UPDATE rules SET evaluations = evaluations + 1, last_evaluated = ? "
                "WHERE id = ?",
                (stamp, rule_id),
            )
        conn.commit()

    def refit(self, rule_id: int, history, now: datetime | None = None) -> float | None:
        """Re-resolve a rule's threshold from fresh history.

        Called periodically so a threshold tracks the system. Returns None when the
        spec cannot be fitted — a rule keeps its previous value rather than being
        silently assigned a guess.
        """
        rule = self.get(rule_id)
        if rule is None or not rule.threshold_spec:
            return None
        try:
            value = resolve_threshold(rule.threshold_spec, history)
        except Exception as e:
            logger.warning("could not refit rule %s (%s): %s",
                           rule_id, rule.threshold_spec, e)
            return None
        stamp = to_utc_iso(now or datetime.now(timezone.utc))
        conn = self._c()
        conn.execute(
            "UPDATE rules SET threshold_value = ?, threshold_fitted_at = ?, "
            "updated_at = ? WHERE id = ?",
            (value, stamp, stamp, rule_id),
        )
        conn.commit()
        return value
