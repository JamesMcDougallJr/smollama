"""Tests for the deterministic detector layer (smollama/detectors/).

Phase 1 of docs/observation-rules.md. No LLM anywhere in this file — that is the
point. The evaluation harness showed 1-2B models fail at open-ended scanning in
both directions (0.00 detection or 0.00 restraint), so detection moves into code
and the model's job becomes describing a signal it is handed.

The gate these must pass: fire on the two production failures that the live
observation loop never reported.
"""

from datetime import datetime, timedelta, timezone

import pytest

from smollama.detectors import (
    DetectorConfig,
    Sample,
    Signal,
    detect_all,
    detect_envelope,
    detect_flatline,
    detect_level_shift,
    detect_stale,
    detect_trend,
)
from smollama.detectors.stats import mad, median, robust_z

NOW = datetime(2026, 9, 27, 12, 0, 0, tzinfo=timezone.utc)


def series(values, *, step_seconds=30, end=NOW):
    """Evenly spaced samples ending at `end`, oldest first."""
    n = len(values)
    return [
        Sample(ts=end - timedelta(seconds=step_seconds * (n - 1 - i)), value=float(v))
        for i, v in enumerate(values)
    ]


class TestRobustStats:
    """median/MAD rather than mean/std, because std is poisoned by the very
    outlier being detected."""

    def test_median_odd_and_even(self):
        assert median([3, 1, 2]) == 2
        assert median([1, 2, 3, 4]) == 2.5

    def test_median_empty_is_none(self):
        assert median([]) is None

    def test_mad_of_constant_is_zero(self):
        assert mad([5, 5, 5, 5]) == 0.0

    def test_mad_ignores_a_single_extreme_outlier(self):
        """The property that makes this robust: one wild value barely moves MAD."""
        calm = mad([10, 10, 11, 10, 11, 10])
        spiked = mad([10, 10, 11, 10, 11, 10, 9999])
        assert spiked <= calm * 3

    def test_robust_z_scales_by_mad(self):
        base = [10, 11, 10, 11, 10, 11]
        assert robust_z(10.5, base) == pytest.approx(0.0, abs=0.7)
        assert robust_z(40, base) > 10

    def test_robust_z_on_zero_mad_is_none(self):
        """A constant baseline has no spread; z is undefined and must not be inf.
        Flatline is the detector that owns that case."""
        assert robust_z(5, [3, 3, 3, 3]) is None

    def test_robust_z_on_empty_baseline_is_none(self):
        assert robust_z(5, []) is None


class TestFlatline:
    def test_fires_on_zero_variance(self):
        s = detect_flatline(series([0.0] * 30), now=NOW, config=DetectorConfig())
        assert s is not None
        assert s.detector == "flatline"
        assert s.direction == "flat"

    def test_does_not_fire_on_varying_series(self):
        s = detect_flatline(series([10, 11, 10, 12, 11] * 6), now=NOW, config=DetectorConfig())
        assert s is None

    def test_requires_a_minimum_sample_count(self):
        """Three identical readings is not evidence of a stuck sensor."""
        s = detect_flatline(series([0.0, 0.0, 0.0]), now=NOW, config=DetectorConfig())
        assert s is None

    def test_detail_names_the_value_and_count(self):
        s = detect_flatline(series([0.0] * 30), now=NOW, config=DetectorConfig())
        assert "0.0" in s.detail and "30" in s.detail

    def test_score_grows_with_sample_count(self):
        short = detect_flatline(series([0.0] * 15), now=NOW, config=DetectorConfig())
        long = detect_flatline(series([0.0] * 400), now=NOW, config=DetectorConfig())
        assert long.score > short.score

    def test_fires_on_nonzero_constant(self):
        """A sensor pinned at 47.0 is as stuck as one pinned at 0."""
        assert detect_flatline(series([47.0] * 30), now=NOW, config=DetectorConfig())


class TestStale:
    def test_fires_when_last_sample_is_old(self):
        s = detect_stale(series([1, 2, 3], end=NOW - timedelta(hours=2)),
                         now=NOW, config=DetectorConfig())
        assert s is not None
        assert s.detector == "stale"
        assert s.direction == "absent"

    def test_does_not_fire_when_fresh(self):
        assert detect_stale(series([1, 2, 3]), now=NOW, config=DetectorConfig()) is None

    def test_empty_series_fires_as_absent(self):
        """A source expected but never seen in the window is the writer-silence case."""
        s = detect_stale([], now=NOW, config=DetectorConfig())
        assert s is not None
        assert s.detector == "stale"

    def test_score_grows_with_gap(self):
        """Both gaps must exceed stale_after_seconds, or the shorter returns None."""
        short = detect_stale(series([1], end=NOW - timedelta(minutes=30)),
                             now=NOW, config=DetectorConfig())
        long = detect_stale(series([1], end=NOW - timedelta(days=19)),
                            now=NOW, config=DetectorConfig())
        assert short is not None and long is not None
        assert long.score > short.score

    def test_detail_states_the_gap(self):
        s = detect_stale(series([1], end=NOW - timedelta(hours=3)),
                         now=NOW, config=DetectorConfig())
        assert "3" in s.detail

    def test_naive_timestamps_are_normalized_not_misread(self):
        """readings_log mixes naive-local and tz-aware UTC. A naive sample must not
        read as hours stale purely because of the missing offset."""
        naive_now = NOW.astimezone().replace(tzinfo=None)
        s = detect_stale([Sample(ts=naive_now, value=1.0)], now=NOW, config=DetectorConfig())
        assert s is None, "naive local timestamp misread as stale"


class TestLevelShift:
    def test_fires_on_step_change(self):
        baseline = [50.0] * 40
        recent = [82.0] * 4
        s = detect_level_shift(series(baseline + recent), now=NOW, config=DetectorConfig())
        assert s is not None
        assert s.detector == "level_shift"
        assert s.direction == "above"

    def test_direction_below_on_a_drop(self):
        s = detect_level_shift(series([50.0] * 40 + [20.0] * 4), now=NOW,
                               config=DetectorConfig())
        assert s.direction == "below"

    def test_does_not_fire_on_steady_series(self):
        vals = [48.0, 48.5, 49.0, 48.2, 48.8] * 10
        assert detect_level_shift(series(vals), now=NOW, config=DetectorConfig()) is None

    def test_single_prior_outlier_does_not_mask_a_real_shift(self):
        """mean/std would be inflated by the 9999 and hide the step; median/MAD won't."""
        baseline = [50.0] * 20 + [9999.0] + [50.0] * 19
        s = detect_level_shift(series(baseline + [82.0] * 4), now=NOW,
                               config=DetectorConfig())
        assert s is not None

    def test_does_not_fire_without_enough_baseline(self):
        s = detect_level_shift(series([50.0, 82.0]), now=NOW, config=DetectorConfig())
        assert s is None

    def test_detail_cites_value_and_baseline(self):
        s = detect_level_shift(series([50.0] * 40 + [82.0] * 5), now=NOW,
                               config=DetectorConfig())
        assert "82" in s.detail and "50" in s.detail

    def test_fires_when_a_constant_baseline_starts_moving(self):
        """A stuck sensor coming back to life. z is undefined (MAD=0), so this has
        its own branch — otherwise the least ambiguous shift there is goes unseen."""
        s = detect_level_shift(series([0.0] * 40 + [47.0] * 5), now=NOW,
                               config=DetectorConfig())
        assert s is not None
        assert s.direction == "above"
        assert s.meta["z"] is None  # undefined, not fabricated

    def test_constant_baseline_branch_scores_below_a_long_silence(self):
        """A 0 -> 0.1 move is unambiguous but tiny; it must not outrank a dead
        producer in the ranking the prompt is built from."""
        shift = detect_level_shift(series([0.0] * 40 + [0.1] * 5), now=NOW,
                                   config=DetectorConfig())
        silence = detect_stale(series([1], end=NOW - timedelta(days=19)),
                              now=NOW, config=DetectorConfig())
        assert shift.score < silence.score


class TestTrend:
    def test_fires_on_sustained_rise(self):
        s = detect_trend(series([float(i) for i in range(40)]), now=NOW,
                         config=DetectorConfig())
        assert s is not None
        assert s.detector == "trend"
        assert s.direction == "above"

    def test_fires_on_sustained_fall(self):
        s = detect_trend(series([float(40 - i) for i in range(40)]), now=NOW,
                         config=DetectorConfig())
        assert s.direction == "below"

    def test_does_not_fire_on_flat(self):
        assert detect_trend(series([50.0] * 40), now=NOW, config=DetectorConfig()) is None

    def test_does_not_fire_on_noise_without_direction(self):
        vals = [50.0, 51.0, 49.0, 50.5, 49.5] * 8
        assert detect_trend(series(vals), now=NOW, config=DetectorConfig()) is None


class TestEnvelope:
    def test_fires_outside_historical_range(self):
        s = detect_envelope(series([40.0, 45.0, 50.0] * 12 + [120.0]), now=NOW,
                            config=DetectorConfig())
        assert s is not None
        assert s.detector == "envelope"
        assert s.direction == "above"

    def test_does_not_fire_inside_range(self):
        s = detect_envelope(series([40.0, 45.0, 50.0] * 12 + [47.0]), now=NOW,
                            config=DetectorConfig())
        assert s is None


class TestDetectAll:
    def test_runs_every_detector_across_sources(self):
        data = {
            "hcsr04:distance": series([0.0] * 30),
            "system:cpu_temp": series([50.0] * 40 + [82.0] * 4),
        }
        signals = detect_all(data, now=NOW, config=DetectorConfig())
        by_source = {s.source for s in signals}
        assert by_source == {"hcsr04:distance", "system:cpu_temp"}
        assert "flatline" in {s.detector for s in signals if s.source == "hcsr04:distance"}

    def test_returns_signals_sorted_by_score_descending(self):
        data = {
            "a": series([0.0] * 30),
            "b": series([50.0] * 40 + [82.0] * 4),
        }
        scores = [s.score for s in detect_all(data, now=NOW, config=DetectorConfig())]
        assert scores == sorted(scores, reverse=True)

    def test_quiet_sources_produce_no_signals(self):
        data = {"system:cpu_temp": series([48.0, 48.5, 49.0, 48.2] * 10)}
        assert detect_all(data, now=NOW, config=DetectorConfig()) == []

    def test_expected_sources_absent_from_data_are_flagged_stale(self):
        """The writer-silence shape: the source vanishes rather than going old."""
        signals = detect_all(
            {"system:cpu_temp": series([48.0] * 20)},
            now=NOW,
            config=DetectorConfig(),
            expected_sources=["jetson-nano:jetson_inference:person_count"],
        )
        stale = [s for s in signals if s.detector == "stale"]
        assert len(stale) == 1
        assert stale[0].source == "jetson-nano:jetson_inference:person_count"

    def test_non_numeric_values_are_skipped_not_crashed(self):
        data = {"jetson:top_object": [Sample(ts=NOW, value=None)]}  # type: ignore[arg-type]
        assert isinstance(detect_all(data, now=NOW, config=DetectorConfig()), list)

    def test_config_thresholds_are_honoured(self):
        """A stricter z threshold suppresses a borderline shift.

        The baseline needs real spread, or MAD is 0, z is undefined, and the
        constant-baseline branch applies instead (see the test below).
        """
        baseline = [50.0, 51.0, 49.0, 50.5, 49.5] * 8
        data = {"x": series(baseline + [56.0] * 5)}
        loose = detect_all(data, now=NOW, config=DetectorConfig(level_shift_min_z=1.0))
        strict = detect_all(data, now=NOW, config=DetectorConfig(level_shift_min_z=99.0))
        assert any(s.detector == "level_shift" for s in loose)
        assert not any(s.detector == "level_shift" for s in strict)


class TestSignalShape:
    def test_signal_carries_what_a_prompt_needs(self):
        s = detect_flatline(series([0.0] * 30), now=NOW, config=DetectorConfig())
        assert isinstance(s, Signal)
        for field in ("source", "detector", "score", "direction", "detail", "first_seen"):
            assert getattr(s, field) is not None, field

    def test_detail_is_self_contained_prose(self):
        """The detail line is what gets handed to the model, so it must stand alone
        with its numbers in it — no reference to state the model can't see."""
        s = detect_level_shift(series([50.0] * 40 + [82.0] * 4), now=NOW,
                               config=DetectorConfig())
        assert len(s.detail) > 20
        assert any(ch.isdigit() for ch in s.detail)
