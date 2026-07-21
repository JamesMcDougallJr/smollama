"""Tests for scripts/jetson/clip_frames.WindowAggregator (pure numpy, no onnxruntime)."""

import os
import sys

import pytest

np = pytest.importorskip("numpy")

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "scripts", "jetson"))

from clip_frames import WindowAggregator  # noqa: E402


def make_agg(**kwargs):
    defaults = dict(window_seconds=4.0, step_seconds=2.0, sample_interval=1.0, min_samples=2)
    defaults.update(kwargs)
    return WindowAggregator(np, **defaults)


def unit(index, dim=4):
    vec = [0.0] * dim
    vec[index] = 1.0
    return vec


class TestGating:
    def test_should_sample_requires_activity(self):
        agg = make_agg()
        assert agg.should_sample(0.0, 0) is False
        assert agg.should_sample(0.0, 1) is True

    def test_should_sample_throttles_by_interval(self):
        agg = make_agg(sample_interval=1.0)
        assert agg.should_sample(0.0, 1) is True
        agg.add_sample(0.0, "t0", unit(0), 1, [])
        assert agg.should_sample(0.5, 1) is False
        assert agg.samples_gated == 1
        assert agg.should_sample(1.0, 1) is True

    def test_idle_grace_extends_activity(self):
        agg = make_agg(idle_grace_seconds=2.0)
        assert agg.is_active(0.0, 1) is True  # sets last_active_time
        assert agg.is_active(1.5, 0) is True  # within grace
        assert agg.is_active(3.0, 0) is False  # grace expired


class TestWindowEmission:
    def test_overlapping_emission_at_step_boundaries(self):
        agg = make_agg(window_seconds=4.0, step_seconds=2.0, min_samples=1)
        for t in [0.0, 1.0, 2.0, 3.0]:
            agg.add_sample(t, "t%d" % t, unit(0), 1, ["person"])
        windows = agg.pop_ready(4.0)
        # first step boundary is at 0 + step (2.0), second at 4.0
        assert len(windows) == 2
        assert windows[0]["end_ts"] == 2.0
        assert windows[1]["end_ts"] == 4.0
        assert agg.windows_emitted == 2

    def test_min_samples_suppresses_short_windows(self):
        agg = make_agg(window_seconds=4.0, step_seconds=2.0, min_samples=3)
        agg.add_sample(0.0, "t0", unit(0), 1, [])
        agg.add_sample(1.0, "t1", unit(0), 1, [])
        windows = agg.pop_ready(2.0)
        assert windows == []
        assert agg.windows_skipped_short == 1

    def test_mean_pool_is_renormalized(self):
        agg = make_agg(window_seconds=4.0, step_seconds=2.0, min_samples=1)
        agg.add_sample(0.0, "t0", unit(0), 1, [])
        agg.add_sample(1.0, "t1", unit(1), 1, [])
        windows = agg.pop_ready(2.0)
        assert len(windows) == 1
        embedding = np.array(windows[0]["embedding"])
        expected = (np.array(unit(0)) + np.array(unit(1)))
        expected = expected / np.linalg.norm(expected)
        np.testing.assert_allclose(embedding, expected, atol=1e-6)

    def test_label_union_and_person_stats(self):
        agg = make_agg(window_seconds=4.0, step_seconds=2.0, min_samples=1)
        agg.add_sample(0.0, "t0", unit(0), 1, ["person"])
        agg.add_sample(1.0, "t1", unit(0), 3, ["dog", "person"])
        windows = agg.pop_ready(2.0)
        win = windows[0]
        assert win["labels"] == ["dog", "person"]
        assert win["person_max"] == 3
        assert win["person_mean"] == pytest.approx(2.0)

    def test_mid_window_jpeg_selected(self):
        agg = make_agg(window_seconds=4.0, step_seconds=2.0, min_samples=1)
        agg.add_sample(0.0, "t0", unit(0), 1, [], jpeg_bytes=b"early")
        agg.add_sample(1.0, "t1", unit(0), 1, [], jpeg_bytes=b"mid")
        agg.add_sample(1.9, "t2", unit(0), 1, [], jpeg_bytes=b"late")
        windows = agg.pop_ready(2.0)
        # window spans [-2, 2]; midpoint 0.0 -> sample at t=0.0 ("early") is closest
        assert windows[0]["jpeg"] == b"early"

    def test_eviction_bounds_buffer(self):
        agg = make_agg(window_seconds=4.0, step_seconds=2.0, min_samples=1)
        agg.add_sample(0.0, "t0", unit(0), 1, [])
        agg.pop_ready(2.0)
        agg.add_sample(10.0, "t10", unit(0), 1, [])
        windows = agg.pop_ready(12.0)
        # the t=0 sample should have been evicted long before t=12
        assert all(w["samples"] <= 1 for w in windows)
