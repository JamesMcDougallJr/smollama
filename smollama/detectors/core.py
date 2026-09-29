"""Deterministic anomaly detectors over windowed reading history.

Pure functions: samples in, Signal or None out. No LLM, no I/O, no persisted
state — which is why they are cheap enough to run every cycle and testable
without a model.

Why this layer exists: the evaluation harness measured 1-2B local models failing
open-ended scanning in both directions — one scored 0.00 detection (silent even
on a 6-sigma spike), the other 0.00 restraint (flagged steady readings). Neither
could discriminate. Detection therefore belongs in code, and the model's job
becomes describing a signal it is handed. See docs/observation-rules.md.

Each detector answers "what is *different*", never "what is *important*". The
first needs only history; the second needs domain knowledge we don't have. Every
source is its own baseline.
"""

from dataclasses import dataclass, field, replace
from datetime import datetime, timedelta, timezone

from ..timeutil import normalize_ts
from .stats import mad, median, monotonic_fraction, robust_z, slope_per_hour


@dataclass
class Sample:
    """One numeric reading. `ts` may be naive — it is normalized on use."""

    ts: datetime
    value: float


@dataclass
class Signal:
    """A candidate worth an observation. `detail` is what reaches the prompt."""

    source: str
    detector: str
    score: float
    direction: str  # above | below | flat | absent
    detail: str
    first_seen: datetime
    value: float | None = None
    baseline: float | None = None
    window_seconds: float | None = None
    sample_count: int = 0
    meta: dict = field(default_factory=dict)


@dataclass
class DetectorConfig:
    """Thresholds. Every one is a tuning decision, so none are buried in code."""

    # flatline: zero spread over at least this many samples
    flatline_min_samples: int = 10
    # stale: no reading for longer than this
    stale_after_seconds: float = 900.0
    # level_shift: the last N samples against a longer baseline.
    # Deliberately a sample COUNT, not a duration: reading cadence varies by
    # deployment (this system logs roughly every 20 minutes, so a 5-minute
    # "recent window" would contain no samples at all and the detector would
    # never fire in production).
    level_shift_recent_samples: int = 5
    level_shift_baseline_seconds: float = 604800.0  # 7 days
    level_shift_min_baseline_samples: int = 20
    level_shift_min_z: float = 4.0
    # trend: fitted slope plus a monotonic majority, so noise can't fake it
    trend_window_seconds: float = 3600.0
    trend_min_samples: int = 10
    trend_min_slope_per_hour: float = 1.0
    trend_min_monotonic_fraction: float = 0.7
    # envelope: outside the historical range by this margin, as a fraction of range
    envelope_min_samples: int = 20
    envelope_margin_fraction: float = 0.1


def _numeric(samples) -> list[Sample]:
    return [s for s in samples if isinstance(s.value, (int, float))
            and not isinstance(s.value, bool)]


def _within(samples, now: datetime, seconds: float) -> list[Sample]:
    cutoff = now - timedelta(seconds=seconds)
    return [s for s in samples if normalize_ts(s.ts) >= cutoff]


def _sorted(samples) -> list[Sample]:
    return sorted(samples, key=lambda s: normalize_ts(s.ts))


def detect_flatline(samples, *, now, config, source: str = "") -> Signal | None:
    """Zero variance over a long run — a stuck or dead sensor.

    Steady is not the same as healthy: a distance sensor pinned at exactly 0.0
    for 486 readings is reporting successfully and telling you nothing.
    """
    nums = _sorted(_numeric(samples))
    if len(nums) < config.flatline_min_samples:
        return None
    values = [s.value for s in nums]
    if mad(values) not in (0.0, None) or len(set(values)) != 1:
        return None

    constant = values[0]
    # Confidence grows with the length of the run, saturating — 400 identical
    # readings is not 40x more suspicious than 10, just more certain.
    score = min(1.0, len(nums) / 200.0) * 5.0
    span = normalize_ts(nums[-1].ts) - normalize_ts(nums[0].ts)
    hours = span.total_seconds() / 3600.0
    return Signal(
        source=source,
        detector="flatline",
        score=round(score, 2),
        direction="flat",
        detail=(
            f"{source or 'source'} has reported exactly {constant} for all "
            f"{len(nums)} readings over {hours:.1f}h — zero variance suggests a "
            f"stuck or disconnected sensor rather than a steady measurement."
        ),
        first_seen=normalize_ts(nums[0].ts),
        value=constant,
        baseline=constant,
        window_seconds=span.total_seconds(),
        sample_count=len(nums),
    )


def detect_stale(samples, *, now, config, source: str = "") -> Signal | None:
    """No recent reading — a dead producer.

    An empty series counts: a source that stops reporting *disappears* from the
    data rather than appearing old. That is the shape of the failure this system
    actually had, where the camera writer went silent and nothing noticed for 19
    days because there was no row to look stale.
    """
    nums = _sorted(_numeric(samples))
    if not nums:
        return Signal(
            source=source,
            detector="stale",
            score=5.0,
            direction="absent",
            detail=(
                f"{source or 'source'} produced no readings at all in the window — "
                f"the producer appears to have stopped."
            ),
            first_seen=now,
            sample_count=0,
        )

    last = normalize_ts(nums[-1].ts)
    gap = (now - last).total_seconds()
    if gap <= config.stale_after_seconds:
        return None

    hours = gap / 3600.0
    # Log-ish growth: a 19-day silence should outrank a 20-minute one without
    # dwarfing every other signal by four orders of magnitude.
    score = min(5.0, 1.0 + (gap / config.stale_after_seconds) ** 0.3)
    return Signal(
        source=source,
        detector="stale",
        score=round(score, 2),
        direction="absent",
        detail=(
            f"{source or 'source'} last reported {hours:.1f}h ago "
            f"(value {nums[-1].value}); expected at least every "
            f"{config.stale_after_seconds / 60:.0f} minutes."
        ),
        first_seen=last,
        value=nums[-1].value,
        window_seconds=gap,
        sample_count=len(nums),
    )


def detect_level_shift(samples, *, now, config, source: str = "") -> Signal | None:
    """Recent window sits far from its own longer baseline.

    Needs two windows. A single aggregate is a description, not a signal: "min=0
    max=0 avg=0" is only interesting beside what the source used to read.
    """
    nums = _sorted(_numeric(samples))
    if len(nums) <= config.level_shift_recent_samples:
        return None

    recent = nums[-config.level_shift_recent_samples:]
    recent_start = normalize_ts(recent[0].ts)
    baseline = [
        s for s in _within(nums[:-config.level_shift_recent_samples],
                           now, config.level_shift_baseline_seconds)
    ]
    if len(baseline) < config.level_shift_min_baseline_samples:
        return None

    recent_med = median([s.value for s in recent])
    base_vals = [s.value for s in baseline]
    base_med = median(base_vals)
    z = robust_z(recent_med, base_vals)

    if z is None:
        # Zero-spread baseline: robust_z is undefined, but a constant baseline
        # followed by a different value is the least ambiguous shift there is —
        # returning None here would miss a sensor that was pinned and then moved.
        if base_med is None or recent_med == base_med:
            return None
        z = float("inf")
        # Deliberately moderate, not maximal. The change is unambiguous but its
        # magnitude is unknowable without baseline spread, so a 0 -> 0.1 move must
        # not outrank a 19-day silence. Note a z threshold cannot gate this branch:
        # z is undefined, so `level_shift_min_z` does not apply here.
        magnitude = 3.0
        z_text = "moving off a previously constant baseline"
    elif abs(z) < config.level_shift_min_z:
        return None
    else:
        magnitude = min(abs(z) / 2.0, 5.0)
        z_text = (
            f"{abs(z):.1f} robust sigma "
            f"{'above' if recent_med > base_med else 'below'} normal"
        )

    return Signal(
        source=source,
        detector="level_shift",
        score=round(magnitude, 2),
        direction="above" if recent_med > base_med else "below",
        detail=(
            f"{source or 'source'} has moved to {recent_med:g} across its last "
            f"{len(recent)} readings, against a baseline of {base_med:g} "
            f"over {len(baseline)} prior readings ({z_text})."
        ),
        first_seen=recent_start,
        value=recent_med,
        baseline=base_med,
        window_seconds=(now - recent_start).total_seconds(),
        sample_count=len(recent),
        meta={"z": None if z == float("inf") else round(z, 2)},
    )


def detect_trend(samples, *, now, config, source: str = "") -> Signal | None:
    """Sustained drift in one direction.

    Requires both a fitted slope and a monotonic majority of steps — a slope
    alone can be produced by noise, and drift is the thing worth reporting.
    """
    window = _sorted(_within(_numeric(samples), now, config.trend_window_seconds))
    if len(window) < config.trend_min_samples:
        return None

    slope = slope_per_hour(window)
    if slope is None or abs(slope) < config.trend_min_slope_per_hour:
        return None
    if monotonic_fraction([s.value for s in window]) < config.trend_min_monotonic_fraction:
        return None

    return Signal(
        source=source,
        detector="trend",
        score=round(min(abs(slope) / config.trend_min_slope_per_hour, 5.0), 2),
        direction="above" if slope > 0 else "below",
        detail=(
            f"{source or 'source'} is {'rising' if slope > 0 else 'falling'} steadily "
            f"at {abs(slope):.2f} per hour, from {window[0].value:g} to "
            f"{window[-1].value:g} over the last "
            f"{config.trend_window_seconds / 3600:.1f}h."
        ),
        first_seen=normalize_ts(window[0].ts),
        value=window[-1].value,
        baseline=window[0].value,
        window_seconds=config.trend_window_seconds,
        sample_count=len(window),
        meta={"slope_per_hour": round(slope, 3)},
    )


def detect_envelope(samples, *, now, config, source: str = "") -> Signal | None:
    """Latest reading outside everything previously observed for this source."""
    nums = _sorted(_numeric(samples))
    if len(nums) < config.envelope_min_samples:
        return None

    latest = nums[-1]
    history = [s.value for s in nums[:-1]]
    lo, hi = min(history), max(history)
    span = hi - lo
    if span == 0:
        return None  # constant history is flatline's business

    margin = span * config.envelope_margin_fraction
    if lo - margin <= latest.value <= hi + margin:
        return None

    over = latest.value - hi if latest.value > hi else lo - latest.value
    return Signal(
        source=source,
        detector="envelope",
        score=round(min(1.0 + over / span, 5.0), 2),
        direction="above" if latest.value > hi else "below",
        detail=(
            f"{source or 'source'} read {latest.value:g}, outside its entire observed "
            f"range of {lo:g}-{hi:g} across {len(history)} prior readings."
        ),
        first_seen=normalize_ts(latest.ts),
        value=latest.value,
        baseline=hi if latest.value > hi else lo,
        sample_count=len(nums),
    )


DETECTORS = (
    detect_stale,
    detect_flatline,
    detect_level_shift,
    detect_trend,
    detect_envelope,
)


# Metrics that are two views of one event. When memory fills, `mem_percent` rises as
# `mem_available_mb` falls: two signals, one thing happening. Grouped by metric name
# only — the node prefix is compared separately, so two nodes filling memory stay two
# events.
#
# A hardcoded list is the honest cost here. It needs a line per correlated pair, and
# an unlisted pair simply reports twice, which is the current behaviour rather than a
# regression.
CORRELATED_METRICS: dict[str, str] = {
    "mem_percent": "memory",
    "mem_available_mb": "memory",
    "disk_percent": "disk",
    "disk_free_gb": "disk",
}


def _correlation_key(signal: "Signal") -> tuple | None:
    """(node prefix, group, detector) for a correlated metric, else None.

    Direction is deliberately excluded. The memory pair moves in opposite
    directions by definition, so keying on it would fail to group exactly the case
    this exists for.
    """
    prefix, _, metric = signal.source.rpartition(":")
    group = CORRELATED_METRICS.get(metric)
    if group is None:
        return None
    return (prefix, group, signal.detector)


def dedupe_correlated(signals: list["Signal"]) -> list["Signal"]:
    """Collapse signals that describe one event, keeping the highest-scoring.

    The suppressed source is preserved in `meta["correlated"]` rather than dropped:
    it is still part of the event, and the observation should be attributed to both.

    Not applied inside `detect_all`, and not before rule evaluation — a rule on the
    suppressed source must still be able to record a fire on a cycle where its twin
    took the narration slot.
    """
    ranked = sorted(signals, key=lambda s: s.score, reverse=True)
    winners: dict[tuple, Signal] = {}
    out: list[Signal] = []

    for signal in ranked:
        key = _correlation_key(signal)
        if key is None:
            out.append(signal)
            continue
        winner = winners.get(key)
        if winner is None:
            # Copy so the caller's signal (which rule evaluation still holds) is
            # not mutated by the bookkeeping below.
            winner = replace(signal, meta={**signal.meta})
            winners[key] = winner
            out.append(winner)
        else:
            winner.meta.setdefault("correlated", []).append(signal.source)

    out.sort(key=lambda s: s.score, reverse=True)
    return out


def detect_all(
    series_by_source: dict,
    *,
    now: datetime | None = None,
    config: DetectorConfig | None = None,
    expected_sources=None,
) -> list[Signal]:
    """Run every detector over every source. Highest score first.

    `expected_sources` names sources that *should* be reporting. Any that are
    missing from `series_by_source` are flagged stale — without this, a producer
    that dies is invisible, because its rows simply stop existing.
    """
    now = now or datetime.now(timezone.utc)
    config = config or DetectorConfig()

    signals: list[Signal] = []
    for source, samples in series_by_source.items():
        usable = _numeric(samples or [])
        for detector in DETECTORS:
            try:
                signal = detector(usable, now=now, config=config, source=source)
            except Exception:  # pragma: no cover - defensive
                continue
            if signal is not None:
                signals.append(signal)

    for source in expected_sources or []:
        if source not in series_by_source:
            signals.append(
                detect_stale([], now=now, config=config, source=source)
            )

    signals.sort(key=lambda s: s.score, reverse=True)
    return signals
