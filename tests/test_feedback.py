"""Tests for observation feedback — Phase 3 of docs/observation-rules.md.

The design doc names this the honest gap in everything above it: there is no
ground truth for "was this observation useful", so any automated judgement about
rule quality is optimising a proxy. One human click supplies a real label.

Deliberately built before LLM rule authoring (Phase 4) and LLM review (Phase 5),
so those have evidence to reason over rather than speculation.
"""

import pytest

from smollama.memory import LocalStore, MockEmbeddings


@pytest.fixture
def store():
    s = LocalStore(":memory:", "test-node", MockEmbeddings())
    s.connect()
    yield s
    s.close()


@pytest.fixture
def obs_id(store):
    return store.add_observation(
        text="hcsr04:distance has read exactly 0.0 for 486 readings",
        observation_type="anomaly",
        confidence=0.95,
        related_sources=["hcsr04:distance"],
    )


class TestRecordFeedback:
    def test_keep_is_recorded(self, store, obs_id):
        store.record_feedback(obs_id, "keep")
        assert store.get_feedback(obs_id) == "keep"

    def test_dismiss_is_recorded(self, store, obs_id):
        store.record_feedback(obs_id, "dismiss")
        assert store.get_feedback(obs_id) == "dismiss"

    def test_unrated_observation_returns_none(self, store, obs_id):
        assert store.get_feedback(obs_id) is None

    def test_invalid_verdict_rejected(self, store, obs_id):
        with pytest.raises(ValueError):
            store.record_feedback(obs_id, "maybe")

    def test_feedback_is_changeable(self, store, obs_id):
        """A misclick must be correctable, or people stop using the control."""
        store.record_feedback(obs_id, "dismiss")
        store.record_feedback(obs_id, "keep")
        assert store.get_feedback(obs_id) == "keep"

    def test_one_row_per_observation(self, store, obs_id):
        store.record_feedback(obs_id, "keep")
        store.record_feedback(obs_id, "dismiss")
        assert len(store.feedback_for_sources(["hcsr04:distance"])) == 1

    def test_feedback_survives_reconnect(self, tmp_path):
        path = str(tmp_path / "m.db")
        s1 = LocalStore(path, "n", MockEmbeddings()); s1.connect()
        oid = s1.add_observation("x", "anomaly", 0.9, ["a:b"])
        s1.record_feedback(oid, "keep")
        s1.close()
        s2 = LocalStore(path, "n", MockEmbeddings()); s2.connect()
        assert s2.get_feedback(oid) == "keep"
        s2.close()


class TestFeedbackBySource:
    """Rule review needs labels grouped by the source a rule watches."""

    def test_groups_verdicts_per_source(self, store):
        a = store.add_observation("a", "anomaly", 0.9, ["system:cpu_temp"])
        b = store.add_observation("b", "anomaly", 0.9, ["system:cpu_temp"])
        c = store.add_observation("c", "status", 0.9, ["hcsr04:distance"])
        store.record_feedback(a, "keep")
        store.record_feedback(b, "dismiss")
        store.record_feedback(c, "dismiss")

        rows = store.feedback_for_sources(["system:cpu_temp"])
        assert len(rows) == 2
        assert {r["verdict"] for r in rows} == {"keep", "dismiss"}

    def test_ignores_unrated_observations(self, store):
        store.add_observation("unrated", "anomaly", 0.9, ["system:cpu_temp"])
        assert store.feedback_for_sources(["system:cpu_temp"]) == []

    def test_empty_source_list_returns_nothing(self, store):
        assert store.feedback_for_sources([]) == []

    def test_keep_rate_summarises_a_source(self, store):
        for verdict in ("keep", "keep", "keep", "dismiss"):
            oid = store.add_observation("x", "anomaly", 0.9, ["system:cpu_temp"])
            store.record_feedback(oid, verdict)
        summary = store.feedback_summary("system:cpu_temp")
        assert summary["total"] == 4
        assert summary["keep"] == 3
        assert summary["keep_rate"] == pytest.approx(0.75)

    def test_summary_for_unlabelled_source_is_safe(self, store):
        summary = store.feedback_summary("never:seen")
        assert summary["total"] == 0
        assert summary["keep_rate"] is None, "must not fabricate a rate from no data"
