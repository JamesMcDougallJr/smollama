"""Source quarantine: stop persisting a source whose data looks invalid.

A model can request this, and a wrong request loses data silently — the same risk
class as retiring a rule, so every limit is enforced here in code rather than asked
for in a prompt.

What "quarantine" means is deliberately narrower than "stop recording". The source is
still read every cycle. Repeats of its frozen value are simply not written, and the
first reading that *differs* releases it and is recorded. So a sensor that gets
plugged in, or a door that finally opens, resumes within one cycle — no coarser than
the sampling already in place. One reading per `trickle_seconds` is still kept, so
history shows the source exists and the staleness detector can tell "quarantined"
from "dead".

Only zero-variance runs qualify. A level shift or a trend is exactly what this system
exists to record, so a changing source is refused no matter how persuasive the reason.
The model supplies a source and a reason; evidence is recomputed from stored history
and never accepted from the caller.
"""

import sqlite3
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path

from .timeutil import normalize_ts, to_utc_iso

SCHEMA = """
CREATE TABLE IF NOT EXISTS source_quarantine (
    full_id TEXT PRIMARY KEY,
    state TEXT NOT NULL,
    reason TEXT,
    detector TEXT,
    constant REAL,
    evidence_samples INTEGER,
    evidence_hours REAL,
    quarantined_by TEXT,
    quarantined_at TEXT,
    last_persisted_at TEXT,
    suppressed_count INTEGER NOT NULL DEFAULT 0,
    released_at TEXT,
    release_reason TEXT
);
CREATE TABLE IF NOT EXISTS quarantine_events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    full_id TEXT NOT NULL,
    at TEXT NOT NULL,
    action TEXT NOT NULL,
    by TEXT NOT NULL,
    reason TEXT
);
CREATE INDEX IF NOT EXISTS idx_quarantine_events_source
    ON quarantine_events (full_id, id);
"""

# Two readings this close are the same reading; anything wider means the value moved.
_SAME_VALUE_TOLERANCE = 1e-9


class QuarantineRefused(Exception):
    """A quarantine request failed a check. The message is safe to show a model."""


@dataclass
class QuarantineConfig:
    enabled: bool = True
    # A constant run must be both long (not a quiet moment) and wide (not one burst).
    min_samples: int = 30
    min_flat_hours: float = 6.0
    # Bounds. Blast radius of a mistaken model, and protection against flapping.
    max_sources: int = 10
    release_cooldown_seconds: float = 3600.0
    # One kept reading per this interval while quarantined.
    trickle_seconds: float = 6 * 3600.0
    # How much history the evidence is computed from.
    window_seconds: float = 7 * 86400.0


@dataclass
class Quarantine:
    full_id: str
    state: str
    reason: str | None = None
    detector: str | None = None
    constant: float | None = None
    evidence_samples: int | None = None
    evidence_hours: float | None = None
    quarantined_by: str | None = None
    quarantined_at: str | None = None
    last_persisted_at: str | None = None
    suppressed_count: int = 0
    released_at: str | None = None
    release_reason: str | None = None


def _is_number(value) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool)


class QuarantineStore:
    def __init__(self, db_path: str, config: QuarantineConfig | None = None):
        self.db_path = Path(db_path).expanduser()
        self.config = config or QuarantineConfig()
        self._conn: sqlite3.Connection | None = None

    # ── connection ──────────────────────────────────────────────────────────
    def connect(self) -> sqlite3.Connection:
        if self._conn is None:
            self.db_path.parent.mkdir(parents=True, exist_ok=True)
            # check_same_thread=False: the dashboard reads and releases from FastAPI's
            # thread pool while the agent writes from the event loop. The timeout
            # covers the brief write lock those two take on the shared memory.db.
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

    # ── reads ───────────────────────────────────────────────────────────────
    @staticmethod
    def _row(row) -> Quarantine:
        return Quarantine(**{k: row[k] for k in row.keys()})

    def get(self, full_id: str) -> Quarantine | None:
        row = self._c().execute(
            "SELECT * FROM source_quarantine WHERE full_id = ?", (full_id,)
        ).fetchone()
        return self._row(row) if row else None

    def quarantined(self) -> list[Quarantine]:
        rows = self._c().execute(
            "SELECT * FROM source_quarantine WHERE state = 'quarantined' "
            "ORDER BY quarantined_at"
        ).fetchall()
        return [self._row(r) for r in rows]

    def quarantined_ids(self) -> set[str]:
        return {q.full_id for q in self.quarantined()}

    def events(self, full_id: str) -> list[dict]:
        rows = self._c().execute(
            "SELECT at, action, by, reason FROM quarantine_events "
            "WHERE full_id = ? ORDER BY id",
            (full_id,),
        ).fetchall()
        return [dict(r) for r in rows]

    # ── writes ──────────────────────────────────────────────────────────────
    def _log(self, full_id: str, action: str, by: str, reason: str, stamp: str) -> None:
        self._c().execute(
            "INSERT INTO quarantine_events (full_id, at, action, by, reason) "
            "VALUES (?, ?, ?, ?, ?)",
            (full_id, stamp, action, by, reason),
        )

    def _trailing_run(self, samples) -> tuple[int, float, float | None, int]:
        """(run length, run span in hours, constant, numeric sample count).

        The *trailing* run, not the whole window: a source that varied last week and
        has been flat since is flat now, and last week's variation is irrelevant to
        whether its current readings are worth storing.
        """
        nums = sorted(
            (s for s in samples if _is_number(s.value)),
            key=lambda s: normalize_ts(s.ts),
        )
        if not nums:
            return 0, 0.0, None, 0
        constant = nums[-1].value
        run = 0
        for s in reversed(nums):
            if s.value != constant:
                break
            run += 1
        first = nums[-run]
        span = (normalize_ts(nums[-1].ts) - normalize_ts(first.ts)).total_seconds()
        return run, span / 3600.0, float(constant), len(nums)

    def quarantine(
        self,
        full_id: str,
        reason: str,
        *,
        samples,
        now: datetime | None = None,
        by: str = "agent",
    ) -> Quarantine:
        """Quarantine a source, or raise QuarantineRefused saying why not.

        `samples` is the source's stored numeric history. The caller loads it; it is
        never taken from the model. `reason` is recorded for the audit trail and has
        no bearing on the decision.
        """
        cfg = self.config
        now = now or datetime.now(timezone.utc)
        stamp = to_utc_iso(now)

        if not cfg.enabled:
            raise QuarantineRefused("quarantine is disabled")

        existing = self.get(full_id)
        if existing and existing.state == "quarantined":
            return existing  # idempotent: asking twice is not an error

        if len(self.quarantined()) >= cfg.max_sources:
            raise QuarantineRefused(
                f"limit reached: {cfg.max_sources} sources are already stopped. "
                "Resume one before stopping another."
            )

        if existing and existing.released_at:
            since = (now - normalize_ts(existing.released_at)).total_seconds()
            if since < cfg.release_cooldown_seconds:
                raise QuarantineRefused(
                    f"{full_id} was recently released ({since / 60:.0f} min ago); "
                    f"cooldown is {cfg.release_cooldown_seconds / 60:.0f} min. "
                    "A source that changed just now is not a stuck one."
                )

        run, hours, constant, total = self._trailing_run(samples)
        if total == 0:
            raise QuarantineRefused(f"no numeric history for {full_id}")
        if run < cfg.min_samples:
            if run < total:
                raise QuarantineRefused(
                    f"{full_id}'s readings vary: its latest constant run is only "
                    f"{run} reading(s). Only a source reporting one unchanging value "
                    "is stopped; a changing source is recorded."
                )
            raise QuarantineRefused(
                f"only {total} readings so far; need at least {cfg.min_samples} "
                "identical readings"
            )
        if hours < cfg.min_flat_hours:
            raise QuarantineRefused(
                f"{full_id} has been constant for only {hours:.1f} hours; need at "
                f"least {cfg.min_flat_hours:g}"
            )

        conn = self._c()
        conn.execute(
            "INSERT OR REPLACE INTO source_quarantine "
            "(full_id, state, reason, detector, constant, evidence_samples, "
            " evidence_hours, quarantined_by, quarantined_at, last_persisted_at, "
            " suppressed_count) "
            "VALUES (?, 'quarantined', ?, 'flatline', ?, ?, ?, ?, ?, ?, 0)",
            (full_id, reason, constant, run, hours, by, stamp, stamp),
        )
        self._log(full_id, "quarantine", by, reason, stamp)
        conn.commit()
        return self.get(full_id)

    def release(
        self,
        full_id: str,
        reason: str,
        *,
        now: datetime | None = None,
        by: str = "human",
    ) -> Quarantine:
        existing = self.get(full_id)
        if existing is None or existing.state != "quarantined":
            raise QuarantineRefused(f"{full_id} is not quarantined")
        stamp = to_utc_iso(now or datetime.now(timezone.utc))
        conn = self._c()
        conn.execute(
            "UPDATE source_quarantine SET state = 'released', released_at = ?, "
            "release_reason = ? WHERE full_id = ?",
            (stamp, reason, full_id),
        )
        self._log(full_id, "release", by, reason, stamp)
        conn.commit()
        return self.get(full_id)

    # ── the recording path ──────────────────────────────────────────────────
    def filter_for_recording(self, readings: list, now: datetime | None = None) -> list:
        """The readings that should be persisted this cycle.

        Every reading is still read. A quarantined source's repeat of its frozen value
        is dropped (with an occasional heartbeat kept); any other value releases the
        source and is kept, since that reading is the evidence the premise was wrong.
        """
        now = now or datetime.now(timezone.utc)
        held = {q.full_id: q for q in self.quarantined()}
        if not held:
            return readings

        kept: list = []
        conn = self._c()
        trickle = timedelta(seconds=self.config.trickle_seconds)

        for r in readings:
            q = held.get(r.full_id)
            if q is None:
                kept.append(r)
                continue

            still_constant = (
                _is_number(r.value)
                and q.constant is not None
                and abs(r.value - q.constant) <= _SAME_VALUE_TOLERANCE
            )
            if not still_constant:
                self.release(
                    r.full_id,
                    f"value changed from {q.constant:g} to {r.value!r}"
                    if q.constant is not None else f"value changed to {r.value!r}",
                    now=now, by="system",
                )
                held.pop(r.full_id, None)
                kept.append(r)
                continue

            last = normalize_ts(q.last_persisted_at) if q.last_persisted_at else None
            if last is None or now - last >= trickle:
                conn.execute(
                    "UPDATE source_quarantine SET last_persisted_at = ? "
                    "WHERE full_id = ?",
                    (to_utc_iso(now), r.full_id),
                )
                kept.append(r)
            else:
                conn.execute(
                    "UPDATE source_quarantine "
                    "SET suppressed_count = suppressed_count + 1 WHERE full_id = ?",
                    (r.full_id,),
                )

        conn.commit()
        return kept
