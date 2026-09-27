"""Canonical timestamp handling for cross-node data.

**The wire contract: every producer emits time as epoch seconds (UTC).**

Producers (edge agents publishing readings, camera writers spooling frames) send
a plain float — seconds since the Unix epoch. They never send ISO strings, never
attach a local UTC offset, and never rely on the reader sharing their timezone.
The master normalizes on ingest, which makes it the single place where "what time
is it really" is decided.

Why: a producer's local offset is a property of that machine, not of the reading.
Sending ``2026-09-07T09:15:00-07:00`` from a node whose clock is set to PDT while
the master runs MDT means every consumer has to be offset-aware to avoid
computing the wrong age. Worse, timestamps are stored as TEXT in SQLite and some
retention queries compare them *lexically* — and lexical ordering only matches
chronological ordering when every string shares one offset. Mixed offsets there
silently mis-sort. Epoch on the wire plus one canonical UTC form in storage
removes both failure modes.

Storage form is ``to_utc_iso()``: ISO-8601 in UTC with a ``+00:00`` offset. It is
fixed-width and sorts lexically in chronological order, so existing string
comparisons in retention/window queries stay correct.

``normalize_ts()`` still accepts legacy ISO strings so entries produced before
this contract (e.g. frames already sitting in an edge spool) keep flowing through
after an upgrade. New producers must not depend on that path.
"""

from datetime import datetime, timezone

__all__ = ["to_epoch", "normalize_ts", "to_utc_iso", "utc_now_epoch"]


def utc_now_epoch() -> float:
    """Current time as epoch seconds — what a producer should stamp."""
    return datetime.now(timezone.utc).timestamp()


def to_epoch(value: datetime | int | float | str) -> float:
    """Convert a datetime / epoch / ISO string to epoch seconds.

    A naive ``datetime`` is interpreted in the *local* timezone, matching
    ``datetime.timestamp()`` semantics — readings built with ``datetime.now()``
    are naive local, so this is the correct reading of them.
    """
    if isinstance(value, datetime):
        return value.timestamp()
    if isinstance(value, (int, float)):
        return float(value)
    return normalize_ts(value).timestamp()


def normalize_ts(value: datetime | int | float | str | None) -> datetime:
    """Normalize any supported timestamp form to a tz-aware UTC datetime.

    Accepts epoch seconds (the wire contract), a ``datetime`` (naive is read as
    local), or an ISO-8601 string (legacy producers). Falls back to "now" when
    the value is missing or unparseable, so one bad record can't stall ingest.
    """
    if value is None:
        return datetime.now(timezone.utc)

    if isinstance(value, datetime):
        # astimezone() reads a naive datetime as local, which is what readings
        # built from datetime.now() mean; aware values just convert.
        return value.astimezone(timezone.utc)

    if isinstance(value, (int, float)):
        try:
            return datetime.fromtimestamp(float(value), tz=timezone.utc)
        except (OverflowError, OSError, ValueError):
            return datetime.now(timezone.utc)

    if isinstance(value, str):
        text = value.strip()
        # Epoch delivered as a string (e.g. survived a JSON round-trip as text)
        try:
            return datetime.fromtimestamp(float(text), tz=timezone.utc)
        except (ValueError, OverflowError, OSError):
            pass
        # Legacy ISO form; "Z" suffix predates fromisoformat's support for it
        try:
            parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
        except ValueError:
            return datetime.now(timezone.utc)
        if parsed.tzinfo is None:
            parsed = parsed.astimezone()
        return parsed.astimezone(timezone.utc)

    return datetime.now(timezone.utc)


def to_utc_iso(value: datetime | int | float | str | None) -> str:
    """Normalize to the canonical storage string: UTC ISO-8601, ``+00:00``.

    Fixed-width and lexically sortable, so ``timestamp > ?`` string comparisons
    in SQLite remain chronologically correct.
    """
    return normalize_ts(value).isoformat()
