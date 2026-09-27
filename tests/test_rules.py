"""Tests for the rules layer (smollama/rules/) — Phase 2 of docs/observation-rules.md.

Still no LLM in this file. Phase 2 is the persistence, threshold-fitting, and
lifecycle machinery that an LLM will later author *into*; building it first means
rule authoring has somewhere safe to land, with retirement already working.

The phase gate: rules survive a restart, and a deliberately noisy rule auto-mutes.
"""

from datetime import datetime, timedelta, timezone

import pytest

from smollama.detectors import Signal
from smollama.rules import (
    MaintenanceConfig,
    Rule,
    RuleStore,
    apply_maintenance,
    uncovered_signals,
)
from smollama.rules.fit import FitError, resolve_threshold

NOW = datetime(2026, 9, 27, 12, 0, 0, tzinfo=timezone.utc)


@pytest.fixture
def store(tmp_path):
    s = RuleStore(str(tmp_path / "rules.db"))
    s.connect()
    yield s
    s.close()


def signal(source="system:cpu_temp", detector="level_shift", direction="above",
           score=4.0, value=82.0):
    return Signal(
        source=source, detector=detector, score=score, direction=direction,
        detail=f"{source} moved to {value}", first_seen=NOW, value=value,
    )


class TestIdentity:
    """Identity is (full_id, detector, direction) — structural, not semantic.

    'cpu_temp > 80' and 'cpu_temp >= 79.5' are semantically one rule and textually
    two; string dedup would accumulate near-duplicates.
    """

    def test_propose_creates_a_rule(self, store):
        rule = store.propose("system:cpu_temp", "level_shift", "above",
                             threshold_spec="fit:p99_7d", rationale="thermal risk")
        assert rule.id is not None
        assert rule.state == "proposed"

    def test_same_identity_updates_rather_than_duplicating(self, store):
        a = store.propose("system:cpu_temp", "level_shift", "above",
                          threshold_spec="literal:80")
        b = store.propose("system:cpu_temp", "level_shift", "above",
                          threshold_spec="literal:75", rationale="revised")
        assert a.id == b.id
        assert len(store.all_rules()) == 1
        assert store.get(a.id).threshold_spec == "literal:75"

    def test_different_direction_is_a_different_rule(self, store):
        store.propose("system:cpu_temp", "level_shift", "above")
        store.propose("system:cpu_temp", "level_shift", "below")
        assert len(store.all_rules()) == 2

    def test_different_detector_is_a_different_rule(self, store):
        store.propose("hcsr04:distance", "flatline", "flat")
        store.propose("hcsr04:distance", "stale", "absent")
        assert len(store.all_rules()) == 2


class TestLifecycle:
    def test_new_rules_start_proposed_not_active(self, store):
        """An LLM-invented rule encodes an assumption nobody approved."""
        rule = store.propose("system:cpu_temp", "trend", "above")
        assert rule.state == "proposed"
        assert store.active_rules() == []

    def test_promote_activates(self, store):
        rule = store.propose("system:cpu_temp", "trend", "above")
        store.promote(rule.id)
        assert store.get(rule.id).state == "active"
        assert len(store.active_rules()) == 1

    def test_mute_and_unmute(self, store):
        rule = store.propose("s", "trend", "above")
        store.promote(rule.id)
        store.mute(rule.id, "describes normal")
        assert store.get(rule.id).state == "muted"
        assert store.active_rules() == []
        store.promote(rule.id)
        assert store.get(rule.id).state == "active"

    def test_retire_is_a_transition_not_a_delete(self, store):
        """A wrongly retired rule makes silence nobody notices; it must be
        recoverable and must carry a reason."""
        rule = store.propose("s", "trend", "above")
        store.retire(rule.id, "source decommissioned")
        got = store.get(rule.id)
        assert got is not None, "retire deleted the row"
        assert got.state == "retired"
        assert got.state_reason == "source decommissioned"

    def test_retired_rule_can_be_resurrected(self, store):
        rule = store.propose("s", "trend", "above")
        store.retire(rule.id, "mistake")
        store.promote(rule.id)
        assert store.get(rule.id).state == "active"

    def test_state_change_requires_a_reason_for_mute_and_retire(self, store):
        rule = store.propose("s", "trend", "above")
        with pytest.raises(ValueError):
            store.mute(rule.id, "")
        with pytest.raises(ValueError):
            store.retire(rule.id, "")


class TestPersistence:
    """The phase gate: rules survive a restart."""

    def test_rules_survive_reopen(self, tmp_path):
        path = str(tmp_path / "rules.db")
        s1 = RuleStore(path)
        s1.connect()
        rule = s1.propose("system:cpu_temp", "level_shift", "above",
                          threshold_spec="literal:80", rationale="thermal")
        s1.promote(rule.id)
        s1.record_evaluation(rule.id, fired=True, now=NOW)
        s1.close()

        s2 = RuleStore(path)
        s2.connect()
        got = s2.get(rule.id)
        assert got.state == "active"
        assert got.threshold_spec == "literal:80"
        assert got.rationale == "thermal"
        assert got.fired_count == 1
        assert got.evaluations == 1
        s2.close()

    def test_counters_accumulate_across_reopen(self, tmp_path):
        path = str(tmp_path / "rules.db")
        s1 = RuleStore(path); s1.connect()
        r = s1.propose("s", "trend", "above"); s1.promote(r.id)
        for _ in range(3):
            s1.record_evaluation(r.id, fired=False, now=NOW)
        s1.close()

        s2 = RuleStore(path); s2.connect()
        s2.record_evaluation(r.id, fired=True, now=NOW)
        assert s2.get(r.id).evaluations == 4
        assert s2.get(r.id).fired_count == 1
        s2.close()


class TestEvaluationRecording:
    def test_firing_updates_counters_and_timestamp(self, store):
        r = store.propose("s", "trend", "above"); store.promote(r.id)
        store.record_evaluation(r.id, fired=True, now=NOW)
        got = store.get(r.id)
        assert got.fired_count == 1
        assert got.evaluations == 1
        assert got.last_fired is not None

    def test_not_firing_increments_evaluations_only(self, store):
        r = store.propose("s", "trend", "above"); store.promote(r.id)
        store.record_evaluation(r.id, fired=False, now=NOW)
        got = store.get(r.id)
        assert got.evaluations == 1
        assert got.fired_count == 0
        assert got.last_fired is None

    def test_fire_rate_computed(self, store):
        r = store.propose("s", "trend", "above"); store.promote(r.id)
        for i in range(10):
            store.record_evaluation(r.id, fired=(i < 8), now=NOW)
        assert store.get(r.id).fire_rate == pytest.approx(0.8)

    def test_fire_rate_is_zero_with_no_evaluations(self, store):
        r = store.propose("s", "trend", "above")
        assert store.get(r.id).fire_rate == 0.0


class TestFitResolver:
    """The LLM proposes the predicate's shape; code fits the number, and re-fits
    periodically so a rule tracks the system instead of freezing at birth."""

    def test_literal_passes_through(self):
        assert resolve_threshold("literal:80", [1, 2, 3]) == 80.0

    def test_percentile_fit(self):
        values = list(range(1, 101))  # 1..100
        assert resolve_threshold("fit:p99_7d", values) == pytest.approx(99.0, abs=1.5)
        assert resolve_threshold("fit:p95_24h", values) == pytest.approx(95.0, abs=1.5)

    def test_baseline_plus_mad(self):
        values = [50.0] * 20 + [52.0] * 20  # median 50 or 51, mad small
        got = resolve_threshold("fit:baseline+4mad", values)
        assert got > max(50.0, 51.0)

    def test_baseline_minus_mad_for_below_direction(self):
        values = [50.0] * 20 + [48.0] * 20
        got = resolve_threshold("fit:baseline-4mad", values)
        assert got < 50.0

    def test_unknown_spec_raises(self):
        with pytest.raises(FitError):
            resolve_threshold("fit:vibes", [1, 2, 3])

    def test_empty_history_raises_rather_than_guessing(self):
        """Fitting a threshold from nothing is how n=1 thresholds get created."""
        with pytest.raises(FitError):
            resolve_threshold("fit:p99_7d", [])

    def test_constant_history_still_yields_a_threshold(self):
        got = resolve_threshold("fit:baseline+4mad", [5.0] * 30)
        assert got == pytest.approx(5.0)

    def test_refit_updates_stored_threshold_and_timestamp(self, store):
        r = store.propose("s", "level_shift", "above", threshold_spec="fit:p99_7d")
        store.refit(r.id, [float(i) for i in range(1, 101)], now=NOW)
        got = store.get(r.id)
        assert got.threshold_value == pytest.approx(99.0, abs=1.5)
        assert got.threshold_fitted_at is not None


class TestDeterministicMaintenance:
    """Most retirement never reaches the LLM. These four classes are arithmetic."""

    def test_noisy_rule_is_auto_muted(self, store):
        """The phase gate. A rule firing in most evaluations describes normal."""
        r = store.propose("s", "trend", "above"); store.promote(r.id)
        for _ in range(60):
            store.record_evaluation(r.id, fired=True, now=NOW)

        actions = apply_maintenance(store, now=NOW, config=MaintenanceConfig())
        assert store.get(r.id).state == "muted"
        assert any(a.action == "mute" and a.rule_id == r.id for a in actions)
        assert "normal" in store.get(r.id).state_reason.lower()

    def test_rule_below_noise_threshold_is_left_alone(self, store):
        r = store.propose("s", "trend", "above"); store.promote(r.id)
        for i in range(60):
            store.record_evaluation(r.id, fired=(i % 10 == 0), now=NOW)
        apply_maintenance(store, now=NOW, config=MaintenanceConfig())
        assert store.get(r.id).state == "active"

    def test_dead_rule_is_retired(self, store):
        """Needs both a long life and enough evaluations, so created_at is backdated."""
        old = NOW - timedelta(days=40)
        r = store.propose("s", "trend", "above", now=old)
        store.promote(r.id, now=old)
        for _ in range(100):
            store.record_evaluation(r.id, fired=False, now=old)
        actions = apply_maintenance(store, now=NOW, config=MaintenanceConfig())
        assert store.get(r.id).state == "retired"
        assert any(a.action == "retire" for a in actions)

    def test_dead_rule_young_enough_is_kept_despite_no_fires(self, store):
        """Age is an independent condition from evaluation count."""
        r = store.propose("s", "trend", "above", now=NOW)
        store.promote(r.id, now=NOW)
        for _ in range(100):
            store.record_evaluation(r.id, fired=False, now=NOW)
        apply_maintenance(store, now=NOW, config=MaintenanceConfig())
        assert store.get(r.id).state == "active"

    def test_young_rule_with_no_fires_is_not_retired(self, store):
        """Minimum age, or a rule is retired before it has had a chance to fire."""
        r = store.propose("s", "trend", "above"); store.promote(r.id)
        for _ in range(5):
            store.record_evaluation(r.id, fired=False, now=NOW)
        apply_maintenance(store, now=NOW, config=MaintenanceConfig())
        assert store.get(r.id).state == "active"

    def test_rule_for_a_vanished_source_is_parked(self, store):
        r = store.propose("gone:sensor", "trend", "above"); store.promote(r.id)
        actions = apply_maintenance(store, now=NOW, config=MaintenanceConfig(),
                                    known_sources=["system:cpu_temp"])
        assert store.get(r.id).state == "parked"
        assert any(a.action == "park" for a in actions)

    def test_park_is_skipped_when_no_registry_supplied(self, store):
        """Absent a source registry we cannot distinguish 'gone' from 'unknown',
        and parking on a guess would silence live monitoring."""
        r = store.propose("gone:sensor", "trend", "above"); store.promote(r.id)
        apply_maintenance(store, now=NOW, config=MaintenanceConfig())
        assert store.get(r.id).state == "active"

    def test_a_noisy_proposed_rule_is_not_promoted(self, store):
        """It must not be promoted and then muted in the same pass — that is two
        state changes for one decision, and it inflates the churn metric that
        exists to detect instability. Staying proposed covers nothing."""
        r = store.propose("s", "trend", "above")
        for _ in range(60):
            store.record_evaluation(r.id, fired=True, now=NOW)
        actions = apply_maintenance(store, now=NOW, config=MaintenanceConfig())
        assert store.get(r.id).state == "proposed"
        assert not any(a.rule_id == r.id for a in actions)

    def test_auto_promote_after_clean_evaluations(self, store):
        r = store.propose("s", "trend", "above")
        for _ in range(MaintenanceConfig().auto_promote_after):
            store.record_evaluation(r.id, fired=False, now=NOW)
        actions = apply_maintenance(store, now=NOW, config=MaintenanceConfig())
        assert store.get(r.id).state == "active"
        assert any(a.action == "promote" for a in actions)

    def test_every_action_records_a_reason(self, store):
        r = store.propose("s", "trend", "above"); store.promote(r.id)
        for _ in range(60):
            store.record_evaluation(r.id, fired=True, now=NOW)
        actions = apply_maintenance(store, now=NOW, config=MaintenanceConfig())
        assert all(a.reason for a in actions)

    def test_churn_metric_reports_action_counts(self, store):
        r = store.propose("s", "trend", "above"); store.promote(r.id)
        for _ in range(60):
            store.record_evaluation(r.id, fired=True, now=NOW)
        actions = apply_maintenance(store, now=NOW, config=MaintenanceConfig())
        assert len(actions) >= 1
        assert all(hasattr(a, "action") and hasattr(a, "rule_id") for a in actions)


class TestStructuralPreFilter:
    """'Don't create a rule if one exists' is an instruction a model may ignore —
    'at most 3 observations' in a prompt was ignored while maxItems:3 was obeyed.
    So code removes covered signals; the model never sees them."""

    def test_signal_covered_by_an_active_rule_is_excluded(self, store):
        r = store.propose("system:cpu_temp", "level_shift", "above")
        store.promote(r.id)
        out = uncovered_signals([signal()], store)
        assert out == []

    def test_signal_with_no_rule_passes_through(self, store):
        out = uncovered_signals([signal()], store)
        assert len(out) == 1

    def test_proposed_rule_does_not_yet_cover(self, store):
        """A rule awaiting approval isn't monitoring anything."""
        store.propose("system:cpu_temp", "level_shift", "above")
        assert len(uncovered_signals([signal()], store)) == 1

    def test_muted_rule_does_not_cover(self, store):
        """Muted means 'this describes normal' — its signals are noise, not covered
        monitoring, so they must not be silently dropped from review either."""
        r = store.propose("system:cpu_temp", "level_shift", "above")
        store.promote(r.id)
        store.mute(r.id, "noisy")
        assert len(uncovered_signals([signal()], store)) == 1

    def test_only_the_matching_identity_is_excluded(self, store):
        r = store.propose("system:cpu_temp", "level_shift", "above")
        store.promote(r.id)
        signals = [signal(), signal(direction="below"), signal(detector="trend")]
        out = uncovered_signals(signals, store)
        assert len(out) == 2

    def test_preserves_input_order(self, store):
        signals = [signal(source="a"), signal(source="b"), signal(source="c")]
        out = uncovered_signals(signals, store)
        assert [s.source for s in out] == ["a", "b", "c"]
