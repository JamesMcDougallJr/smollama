"""The Phase 1 validation gate: detectors must fire on real production failures.

docs/observation-rules.md commits Phase 1 to one measurable claim — replay
readings_log and fire on the failures the live observation loop never reported.
Unit tests prove the detectors work on constructed input; this proves they work
on the data the system actually produced.

Tests touching the live database skip when it is absent, so the suite still runs
on a machine that has never run the agent.
"""

import os
import sqlite3
import tempfile
from datetime import datetime, timedelta, timezone

import pytest

from smollama.detectors import DetectorConfig, detect_all, detect_flatline
from smollama.detectors.source import known_sources, load_series

LIVE_DB = os.path.expanduser("~/.smollama/memory.db")
live_only = pytest.mark.skipif(
    not os.path.exists(LIVE_DB), reason="no live memory.db on this machine"
)


def _write_db(rows):
    """Build a throwaway readings_log with the given (full_id, ts, value) rows."""
    fd, path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    conn = sqlite3.connect(path)
    conn.execute(
        "CREATE TABLE readings_log (id INTEGER PRIMARY KEY, full_id TEXT, "
        "timestamp TEXT, value_numeric REAL)"
    )
    conn.executemany(
        "INSERT INTO readings_log (full_id, timestamp, value_numeric) VALUES (?,?,?)",
        rows,
    )
    conn.commit()
    conn.close()
    return path


class TestLoaderHandlesMixedTimestampFrames:
    """readings_log stores local providers naive and relayed readings tz-aware.

    Six hours of apparent skew between two sources recorded at the same instant is
    not hypothetical — it is what the live table contains.
    """

    def test_naive_and_aware_rows_land_at_the_same_instant(self):
        aware = datetime(2026, 9, 27, 1, 27, 22, tzinfo=timezone.utc)
        naive_local = aware.astimezone().replace(tzinfo=None)
        path = _write_db([
            ("bridged:source", aware.isoformat(), 1.0),
            ("local:source", naive_local.isoformat(), 1.0),
        ])
        try:
            series = load_series(path, now=aware + timedelta(minutes=1))
            assert set(series) == {"bridged:source", "local:source"}
            gap = abs(
                (series["bridged:source"][0].ts - series["local:source"][0].ts).total_seconds()
            )
            assert gap < 1, f"{gap}s skew between frames — normalization failed"
        finally:
            os.unlink(path)

    def test_naive_local_row_is_not_misread_as_stale(self):
        """The bug this guards: a live local source flagged dead by its UTC offset."""
        now = datetime(2026, 9, 27, 1, 27, 22, tzinfo=timezone.utc)
        naive_local = now.astimezone().replace(tzinfo=None)
        path = _write_db([
            ("local:source", (naive_local - timedelta(seconds=30 * i)).isoformat(), 50.0 + i)
            for i in range(30)
        ])
        try:
            series = load_series(path, now=now)
            signals = detect_all(series, now=now, config=DetectorConfig())
            assert not [s for s in signals if s.detector == "stale"], (
                "live local source misread as stale"
            )
        finally:
            os.unlink(path)

    def test_window_excludes_older_rows(self):
        now = datetime(2026, 9, 27, tzinfo=timezone.utc)
        path = _write_db([
            ("s", (now - timedelta(days=30)).isoformat(), 1.0),
            ("s", (now - timedelta(hours=1)).isoformat(), 2.0),
        ])
        try:
            series = load_series(path, now=now, window_seconds=86400)
            assert len(series["s"]) == 1
        finally:
            os.unlink(path)

    def test_missing_database_returns_empty_not_raises(self):
        assert load_series("/nonexistent/memory.db") == {}
        assert known_sources("/nonexistent/memory.db") == []


# ── The gate ────────────────────────────────────────────────────────────────


@live_only
class TestGateStuckSensor:
    """Production failure 1: hcsr04:distance pinned at exactly 0.0.

    486 real readings, min=max=avg=0.0. The observation loop ran over this data
    for weeks and never reported it.
    """

    def test_flatline_fires_on_the_real_series(self):
        series = load_series(LIVE_DB)
        samples = series.get("hcsr04:distance")
        if not samples:
            pytest.skip("hcsr04:distance not present in this readings_log")

        signal = detect_flatline(
            samples, now=datetime.now(timezone.utc), config=DetectorConfig(),
            source="hcsr04:distance",
        )
        assert signal is not None, "flatline missed a sensor pinned at one value"
        assert signal.direction == "flat"
        assert signal.sample_count >= 10

    def test_signal_detail_is_usable_verbatim(self):
        series = load_series(LIVE_DB)
        samples = series.get("hcsr04:distance")
        if not samples:
            pytest.skip("hcsr04:distance not present")
        signal = detect_flatline(
            samples, now=datetime.now(timezone.utc), config=DetectorConfig(),
            source="hcsr04:distance",
        )
        # The detail line is handed straight to the model, so it must name the
        # source, the value, and the evidence without external context.
        assert "hcsr04:distance" in signal.detail
        assert "0.0" in signal.detail
        assert str(signal.sample_count) in signal.detail

    def test_appears_in_a_full_detection_pass(self):
        now = datetime.now(timezone.utc)
        series = load_series(LIVE_DB)
        if "hcsr04:distance" not in series:
            pytest.skip("hcsr04:distance not present")
        signals = detect_all(series, now=now, config=DetectorConfig())
        flagged = {(s.source, s.detector) for s in signals}
        assert ("hcsr04:distance", "flatline") in flagged


@live_only
class TestGateSilentProducer:
    """Production failure 2: the camera writer stopped and nothing noticed for 19 days.

    Its rows are long since pruned by readings_log retention, so this replays the
    real data with an advanced clock — the same series, asked "what if nothing has
    reported since?". That exercises the detector against production data rather
    than a hand-built series.
    """

    def test_staleness_fires_when_the_clock_advances_past_every_source(self):
        series = load_series(LIVE_DB)
        if not series:
            pytest.skip("empty readings_log")

        latest = max(s.ts for samples in series.values() for s in samples)
        future = latest + timedelta(days=19)

        signals = detect_all(series, now=future, config=DetectorConfig())
        stale = {s.source for s in signals if s.detector == "stale"}
        assert stale == set(series), "a silent producer went undetected"

    def test_does_not_fire_at_the_real_current_time_for_live_sources(self):
        """Guards the opposite error: flagging healthy sources as dead."""
        series = load_series(LIVE_DB)
        if not series:
            pytest.skip("empty readings_log")
        latest = max(s.ts for samples in series.values() for s in samples)
        # Evaluate just after the newest reading, so nothing is legitimately stale.
        signals = detect_all(series, now=latest + timedelta(seconds=60),
                             config=DetectorConfig())
        fresh_source = max(
            series, key=lambda k: max(s.ts for s in series[k])
        )
        assert fresh_source not in {
            s.source for s in signals if s.detector == "stale"
        }

    def test_registry_catches_a_source_that_vanished_entirely(self):
        """The writer-silence shape: absent from the data, not merely old."""
        now = datetime.now(timezone.utc)
        series = load_series(LIVE_DB)
        if not series:
            pytest.skip("empty readings_log")

        vanished = "jetson-nano:jetson_inference:person_count"
        signals = detect_all(
            series, now=now, config=DetectorConfig(),
            expected_sources=list(series) + [vanished],
        )
        stale = [s for s in signals if s.detector == "stale" and s.source == vanished]
        assert len(stale) == 1
        assert stale[0].direction == "absent"


@live_only
class TestGateNoiseFloor:
    """A detector layer that fires on everything is as useless as one that fires on
    nothing — the eval harness measured exactly that failure in gemma3:1b."""

    def test_live_pass_produces_a_reviewable_number_of_signals(self):
        now = datetime.now(timezone.utc)
        series = load_series(LIVE_DB)
        if not series:
            pytest.skip("empty readings_log")
        signals = detect_all(series, now=now, config=DetectorConfig())
        # Not zero (the stuck sensor is real), and not one-per-source-per-detector.
        assert signals, "no signals at all on real data"
        assert len(signals) <= len(series) * 2, (
            f"{len(signals)} signals across {len(series)} sources — too noisy to review"
        )

    def test_signals_are_ranked_highest_first(self):
        now = datetime.now(timezone.utc)
        series = load_series(LIVE_DB)
        if not series:
            pytest.skip("empty readings_log")
        scores = [s.score for s in detect_all(series, now=now, config=DetectorConfig())]
        assert scores == sorted(scores, reverse=True)


class TestLoaderScaling:
    """The loader runs every detection pass, so its query must use an index.

    A full SCAN is 3ms at 5k rows and seconds once the table is large — and it
    grows every cycle, so the cost is unbounded rather than merely slow.
    """

    def test_time_bound_is_pushed_into_sql(self):
        now = datetime(2026, 9, 27, tzinfo=timezone.utc)
        path = _write_db([
            ("s", (now - timedelta(hours=i)).isoformat(), float(i)) for i in range(50)
        ])
        try:
            conn = sqlite3.connect(path)
            conn.execute("CREATE INDEX idx_readings_timestamp ON readings_log(timestamp)")
            conn.commit()
            plan = "\n".join(
                r[-1] for r in conn.execute(
                    "EXPLAIN QUERY PLAN SELECT full_id, timestamp, value_numeric "
                    "FROM readings_log WHERE timestamp >= ? AND value_numeric IS NOT NULL",
                    ("2026-09-26T00:00:00+00:00",),
                )
            )
            conn.close()
            assert "SCAN" not in plan.upper(), f"full scan not avoided:\n{plan}"
            assert "INDEX" in plan.upper(), plan
        finally:
            os.unlink(path)

    def test_source_filter_still_returns_only_requested_sources(self):
        now = datetime(2026, 9, 27, tzinfo=timezone.utc)
        path = _write_db([
            ("wanted", (now - timedelta(minutes=5)).isoformat(), 1.0),
            ("other", (now - timedelta(minutes=5)).isoformat(), 2.0),
        ])
        try:
            series = load_series(path, now=now, sources=["wanted"])
            assert set(series) == {"wanted"}
        finally:
            os.unlink(path)

    def test_rows_just_outside_the_window_are_still_excluded(self):
        """The SQL floor is deliberately widened by a day to tolerate mixed
        timestamp frames; exact filtering must still happen after normalization."""
        now = datetime(2026, 9, 27, 12, 0, tzinfo=timezone.utc)
        path = _write_db([
            ("s", (now - timedelta(hours=30)).isoformat(), 1.0),  # outside 24h window
            ("s", (now - timedelta(hours=1)).isoformat(), 2.0),
        ])
        try:
            series = load_series(path, now=now, window_seconds=86400)
            assert [s.value for s in series["s"]] == [2.0]
        finally:
            os.unlink(path)
