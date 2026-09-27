"""Load reading series out of readings_log for the detectors.

Kept separate from core.py so the detectors stay pure functions with no I/O and
remain testable without a database.

One correctness note: readings_log mixes timestamp frames. Local providers stamp
naive `datetime.now()` (local time) while relayed edge readings carry tz-aware
UTC, so the same instant appears as "19:27:29" and "01:27:22+00:00". Every
timestamp is normalized through timeutil on the way out — without that, elapsed
time is wrong by the UTC offset and the staleness detector misreads a live source
as hours dead.
"""

import logging
import os
import sqlite3
from datetime import datetime, timedelta, timezone

from ..timeutil import normalize_ts
from .core import Sample

logger = logging.getLogger(__name__)


def load_series(
    db_path: str,
    *,
    window_seconds: float = 604800.0,
    now: datetime | None = None,
    sources: list[str] | None = None,
) -> dict[str, list[Sample]]:
    """Return {full_id: [Sample, ...]} for numeric readings inside the window.

    Only `value_numeric` rows are returned — detectors are statistical, and a
    non-numeric reading (a detected-object label, say) has no median.
    """
    now = now or datetime.now(timezone.utc)
    path = os.path.expanduser(db_path)
    if not os.path.exists(path):
        logger.warning("readings_log not found at %s", path)
        return {}

    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    try:
        rows = conn.execute(
            "SELECT full_id, timestamp, value_numeric FROM readings_log "
            "WHERE value_numeric IS NOT NULL"
        ).fetchall()
    except sqlite3.Error as e:
        logger.warning("could not read readings_log: %s", e)
        return {}
    finally:
        conn.close()

    cutoff = now - timedelta(seconds=window_seconds)
    wanted = set(sources) if sources else None
    series: dict[str, list[Sample]] = {}

    for row in rows:
        full_id = row["full_id"]
        if wanted is not None and full_id not in wanted:
            continue
        # Normalizing here, not at compare time, is what makes the mixed naive /
        # tz-aware storage safe for every downstream elapsed-time calculation.
        ts = normalize_ts(row["timestamp"])
        if ts < cutoff:
            continue
        series.setdefault(full_id, []).append(
            Sample(ts=ts, value=float(row["value_numeric"]))
        )

    for samples in series.values():
        samples.sort(key=lambda s: s.ts)
    return series


def known_sources(db_path: str) -> list[str]:
    """Every source with numeric readings in readings_log — the staleness registry.

    A producer that dies stops writing rows rather than writing old ones, so it
    vanishes from the recent window entirely. Detecting that requires knowing what
    *used* to report, which is what this supplies to
    `detect_all(expected_sources=...)`.

    No explicit time window: `memory.readings_max_age_days` already prunes the
    table (7 days by default), so this is bounded by retention. That is also the
    limitation — a producer dead longer than the retention period is gone from here
    too and cannot be recovered this way. The camera writer that went silent for 19
    days is exactly that case, which is why a longer-lived registry is worth having
    if staleness matters beyond the retention horizon.
    """
    path = os.path.expanduser(db_path)
    if not os.path.exists(path):
        return []
    conn = sqlite3.connect(path)
    try:
        rows = conn.execute(
            "SELECT DISTINCT full_id FROM readings_log WHERE value_numeric IS NOT NULL"
        ).fetchall()
    except sqlite3.Error:
        return []
    finally:
        conn.close()
    return sorted(r[0] for r in rows)
