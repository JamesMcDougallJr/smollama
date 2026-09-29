"""Tests for detect-then-narrate in the observation loop.

Implements roadmap/detector-loop-integration.md. The loop's question changes from
"find something in these readings" — measured at 0.00 detection on the model in
production — to "describe this specific deviation", and the model is not called at
all when nothing fired.

Domain passes (vision_observation) are deliberately left alone: they claim their own
sources and build their own prompts, so the detector pass covers only what no domain
claimed.
"""

from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from smollama.detectors import Signal
from smollama.memory import LocalStore, MockEmbeddings, ObservationLoop
from smollama.readings import Reading, ReadingManager
from smollama.rules import RuleStore

NOW = datetime(2026, 9, 28, 12, 0, 0, tzinfo=timezone.utc)


def signal(source="system:cpu_temp", detector="level_shift", direction="above",
           score=4.5, detail=None):
    return Signal(
        source=source, detector=detector, score=score, direction=direction,
        detail=detail or f"{source} moved to 82 against a baseline of 50",
        first_seen=NOW, value=82.0, baseline=50.0,
    )


@pytest.fixture
def store(tmp_path):
    s = LocalStore(str(tmp_path / "m.db"), "test-node", MockEmbeddings())
    s.connect()
    yield s
    s.close()


@pytest.fixture
def rules(tmp_path):
    r = RuleStore(str(tmp_path / "m.db"))
    r.connect()
    yield r
    r.close()


@pytest.fixture
def readings():
    m = MagicMock(spec=ReadingManager)
    m.read_all = AsyncMock(return_value=[
        Reading("system", "cpu_temp", 82.0, datetime.now(), "celsius"),
        Reading("system", "mem_percent", 40.0, datetime.now(), "percent"),
    ])
    return m


@pytest.fixture
def agent():
    a = MagicMock()
    a.query = AsyncMock(return_value='{"observations": [{"text": "CPU temp stepped '
                                     'to 82.0C from a 50 baseline.", "type": '
                                     '"anomaly", "confidence": 0.9, '
                                     '"related_sources": ["system:cpu_temp"]}], '
                                     '"memories": []}')
    return a


def make_loop(store, readings, agent, rules=None, **kw):
    return ObservationLoop(
        store=store, readings=readings, agent=agent,
        interval_minutes=15, lookback_minutes=60,
        rule_store=rules, use_detectors=True, **kw,
    )


def with_signals(signals):
    """Patch the detector entry points the loop calls."""
    return patch.multiple(
        "smollama.memory.observation_loop",
        load_series=MagicMock(return_value={"system:cpu_temp": []}),
        known_sources=MagicMock(return_value=["system:cpu_temp"]),
        detect_all=MagicMock(return_value=signals),
    )


class TestQuietCycleSkipsTheModel:
    """The largest win, and it needs no model at all: today every cycle costs
    35-104s whether or not anything happened."""

    @pytest.mark.asyncio
    async def test_no_signals_means_no_llm_call(self, store, readings, agent):
        loop = make_loop(store, readings, agent)
        with with_signals([]):
            await loop.run_once()
        agent.query.assert_not_called()

    @pytest.mark.asyncio
    async def test_no_signals_stores_no_observation(self, store, readings, agent):
        loop = make_loop(store, readings, agent)
        with with_signals([]):
            await loop.run_once()
        assert store.get_stats()["observations_count"] == 0

    @pytest.mark.asyncio
    async def test_readings_are_still_logged_on_a_quiet_cycle(self, store, readings, agent):
        """Skipping the model must not skip persistence — the next cycle's baselines
        depend on these rows."""
        loop = make_loop(store, readings, agent)
        with with_signals([]):
            await loop.run_once()
        assert store.get_stats()["readings_count"] > 0


class TestNarrateNotScan:
    @pytest.mark.asyncio
    async def test_prompt_contains_the_signal_detail(self, store, readings, agent):
        loop = make_loop(store, readings, agent)
        with with_signals([signal(detail="cpu_temp moved to 82 from a 50 baseline")]):
            await loop.run_once()
        prompt = agent.query.await_args[0][0]
        assert "cpu_temp moved to 82 from a 50 baseline" in prompt

    @pytest.mark.asyncio
    async def test_prompt_carries_no_detector_tag(self, store, readings, agent):
        """Measured on qwen2.5:1.5b: a "[stale/absent]" prefix in the prompt came
        back verbatim inside the stored observation text. `detail` is already
        self-contained, so the tag buys nothing and leaks."""
        loop = make_loop(store, readings, agent)
        with with_signals([signal(detector="level_shift", direction="above")]):
            await loop.run_once()
        prompt = agent.query.await_args[0][0]
        assert "[level_shift" not in prompt
        assert "level_shift/above" not in prompt

    @pytest.mark.asyncio
    async def test_prompt_is_not_a_dump_of_every_reading(self, store, readings, agent):
        """The old path listed all sources; prefill dominated cost (57s of 60s)."""
        loop = make_loop(store, readings, agent)
        with with_signals([signal()]):
            await loop.run_once()
        prompt = agent.query.await_args[0][0]
        assert "system:mem_percent" not in prompt, "unflagged source leaked in"

    @pytest.mark.asyncio
    async def test_signal_count_is_bounded(self, store, readings, agent):
        loop = make_loop(store, readings, agent, max_signals=2)
        many = [signal(source=f"s{i}", score=float(i)) for i in range(8)]
        with with_signals(many):
            await loop.run_once()
        prompt = agent.query.await_args[0][0]
        # Highest-scoring only, so the budget goes to the strongest evidence.
        assert "s7" in prompt and "s0 " not in prompt

    @pytest.mark.asyncio
    async def test_observation_is_stored(self, store, readings, agent):
        loop = make_loop(store, readings, agent)
        with with_signals([signal()]):
            await loop.run_once()
        assert store.get_stats()["observations_count"] == 1

    @pytest.mark.asyncio
    async def test_request_is_still_schema_constrained(self, store, readings, agent):
        loop = make_loop(store, readings, agent)
        with with_signals([signal()]):
            await loop.run_once()
        kwargs = agent.query.await_args.kwargs
        assert isinstance(kwargs["format"], dict)
        assert kwargs["use_tools"] is False


class TestRulePreFilter:
    @pytest.mark.asyncio
    async def test_signal_covered_by_an_active_rule_is_not_narrated(
        self, store, readings, agent, rules
    ):
        r = rules.propose("system:cpu_temp", "level_shift", "above")
        rules.promote(r.id)
        loop = make_loop(store, readings, agent, rules=rules)
        with with_signals([signal()]):
            await loop.run_once()
        agent.query.assert_not_called()

    @pytest.mark.asyncio
    async def test_uncovered_signal_still_reaches_the_model(
        self, store, readings, agent, rules
    ):
        r = rules.propose("other:source", "trend", "above")
        rules.promote(r.id)
        loop = make_loop(store, readings, agent, rules=rules)
        with with_signals([signal()]):
            await loop.run_once()
        agent.query.assert_called_once()


class TestRuleEvaluationRecording:
    """Without evaluation counts, auto-mute and auto-retire never trigger and the
    whole lifecycle is inert."""

    @pytest.mark.asyncio
    async def test_matching_rule_records_a_fire(self, store, readings, agent, rules):
        r = rules.propose("system:cpu_temp", "level_shift", "above")
        rules.promote(r.id)
        loop = make_loop(store, readings, agent, rules=rules)
        with with_signals([signal()]):
            await loop.run_once()
        got = rules.get(r.id)
        assert got.evaluations == 1
        assert got.fired_count == 1

    @pytest.mark.asyncio
    async def test_non_matching_rule_records_an_evaluation_without_a_fire(
        self, store, readings, agent, rules
    ):
        r = rules.propose("quiet:source", "trend", "above")
        rules.promote(r.id)
        loop = make_loop(store, readings, agent, rules=rules)
        with with_signals([signal()]):
            await loop.run_once()
        got = rules.get(r.id)
        assert got.evaluations == 1
        assert got.fired_count == 0

    @pytest.mark.asyncio
    async def test_evaluations_accumulate_across_cycles(
        self, store, readings, agent, rules
    ):
        r = rules.propose("quiet:source", "trend", "above")
        rules.promote(r.id)
        loop = make_loop(store, readings, agent, rules=rules)
        with with_signals([]):
            await loop.run_once()
            await loop.run_once()
            await loop.run_once()
        assert rules.get(r.id).evaluations == 3

    @pytest.mark.asyncio
    async def test_quiet_cycles_still_record_evaluations(
        self, store, readings, agent, rules
    ):
        """A rule that never fires can only be retired if quiet cycles count."""
        r = rules.propose("quiet:source", "trend", "above")
        rules.promote(r.id)
        loop = make_loop(store, readings, agent, rules=rules)
        with with_signals([]):
            await loop.run_once()
        assert rules.get(r.id).evaluations == 1

    @pytest.mark.asyncio
    async def test_proposed_rules_are_not_evaluated(self, store, readings, agent, rules):
        """A proposed rule monitors nothing, so counting evaluations against it would
        auto-promote it on evidence it never gathered."""
        r = rules.propose("system:cpu_temp", "level_shift", "above")
        loop = make_loop(store, readings, agent, rules=rules)
        with with_signals([signal()]):
            await loop.run_once()
        assert rules.get(r.id).evaluations == 0


class TestMaintenanceCadence:
    @pytest.mark.asyncio
    async def test_maintenance_does_not_run_every_cycle(
        self, store, readings, agent, rules
    ):
        loop = make_loop(store, readings, agent, rules=rules, maintenance_every=5)
        with with_signals([]), patch(
            "smollama.memory.observation_loop.apply_maintenance"
        ) as m:
            for _ in range(4):
                await loop.run_once()
            assert m.call_count == 0

    @pytest.mark.asyncio
    async def test_maintenance_runs_on_the_configured_cadence(
        self, store, readings, agent, rules
    ):
        loop = make_loop(store, readings, agent, rules=rules, maintenance_every=3)
        with with_signals([]), patch(
            "smollama.memory.observation_loop.apply_maintenance"
        ) as m:
            for _ in range(6):
                await loop.run_once()
            assert m.call_count == 2

    @pytest.mark.asyncio
    async def test_no_maintenance_without_a_rule_store(self, store, readings, agent):
        loop = make_loop(store, readings, agent, rules=None, maintenance_every=1)
        with with_signals([]), patch(
            "smollama.memory.observation_loop.apply_maintenance"
        ) as m:
            await loop.run_once()
            assert m.call_count == 0


class TestOptOut:
    @pytest.mark.asyncio
    async def test_detectors_can_be_disabled_restoring_the_old_path(
        self, store, readings, agent
    ):
        """The old scan path stays available, so this is reversible in config."""
        loop = ObservationLoop(
            store=store, readings=readings, agent=agent,
            interval_minutes=15, lookback_minutes=60, use_detectors=False,
        )
        with with_signals([]):
            await loop.run_once()
        # No signals, yet the model is still asked — that is the old behaviour.
        agent.query.assert_called_once()
        prompt = agent.query.await_args[0][0]
        assert "Current readings:" in prompt


class TestResilience:
    @pytest.mark.asyncio
    async def test_detector_failure_does_not_break_the_cycle(
        self, store, readings, agent
    ):
        """A bad detection pass must not stop readings being logged."""
        loop = make_loop(store, readings, agent)
        with patch(
            "smollama.memory.observation_loop.load_series",
            side_effect=RuntimeError("db gone"),
        ):
            await loop.run_once()
        assert store.get_stats()["readings_count"] > 0

    @pytest.mark.asyncio
    async def test_no_readings_skips_everything(self, store, agent):
        empty = MagicMock(spec=ReadingManager)
        empty.read_all = AsyncMock(return_value=[])
        loop = make_loop(store, empty, agent)
        with with_signals([signal()]):
            await loop.run_once()
        agent.query.assert_not_called()


class TestRelatedSourceSanitizing:
    """Measured on qwen2.5:1.5b under the detector path: correct observation text
    paired with an invented `related_sources` — 'system:cpu_temp' on a case that
    never mentioned it, 'system:hcsr04' for 'hcsr04:distance'. A small model will
    not reliably copy an identifier, and a wrong one misattributes the observation,
    so later keep/drop feedback lands on an unrelated source. Code knows exactly
    which sources fired, so code decides this.
    """

    def _observation(self, store):
        rows = store.get_observations_since_id(0, limit=1)
        assert rows, "no observation stored"
        return rows[0]

    @pytest.mark.asyncio
    async def test_invented_source_is_replaced_with_the_flagged_one(
        self, store, readings, agent
    ):
        agent.query = AsyncMock(return_value=(
            '{"observations": [{"text": "CPU temp stepped to 82.0 from 50.",'
            ' "type": "anomaly", "confidence": 0.9,'
            ' "related_sources": ["system:not_a_real_source"]}], "memories": []}'
        ))
        loop = make_loop(store, readings, agent)
        with with_signals([signal()]):
            await loop.run_once()
        assert self._observation(store)["related_sources"] == ["system:cpu_temp"]

    @pytest.mark.asyncio
    async def test_valid_source_is_kept(self, store, readings, agent):
        loop = make_loop(store, readings, agent)
        with with_signals([signal()]):
            await loop.run_once()
        assert self._observation(store)["related_sources"] == ["system:cpu_temp"]

    @pytest.mark.asyncio
    async def test_invalid_entries_are_dropped_from_a_mixed_list(
        self, store, readings, agent
    ):
        agent.query = AsyncMock(return_value=(
            '{"observations": [{"text": "CPU temp stepped to 82.0 from 50.",'
            ' "type": "anomaly", "confidence": 0.9,'
            ' "related_sources": ["system:cpu_temp", "system:invented"]}],'
            ' "memories": []}'
        ))
        loop = make_loop(store, readings, agent)
        with with_signals([signal()]):
            await loop.run_once()
        assert self._observation(store)["related_sources"] == ["system:cpu_temp"]

    @pytest.mark.asyncio
    async def test_missing_related_sources_is_filled_in(self, store, readings, agent):
        """The model omitting the field should not lose the attribution code has."""
        agent.query = AsyncMock(return_value=(
            '{"observations": [{"text": "CPU temp stepped to 82.0 from 50.",'
            ' "type": "anomaly", "confidence": 0.9}], "memories": []}'
        ))
        loop = make_loop(store, readings, agent)
        with with_signals([signal()]):
            await loop.run_once()
        assert self._observation(store)["related_sources"] == ["system:cpu_temp"]

    @pytest.mark.asyncio
    async def test_stale_signal_attributes_to_the_absent_source(
        self, store, readings, agent
    ):
        """A stale source has no current reading — absence is the finding, so it
        must still be the attributed source."""
        agent.query = AsyncMock(return_value=(
            '{"observations": [{"text": "person_count has not reported in 19 days.",'
            ' "type": "anomaly", "confidence": 0.9, "related_sources": []}],'
            ' "memories": []}'
        ))
        loop = make_loop(store, readings, agent)
        gone = signal(source="jetson-nano:vision:person_count", detector="stale",
                      direction="absent")
        with with_signals([gone]):
            await loop.run_once()
        assert self._observation(store)["related_sources"] == [
            "jetson-nano:vision:person_count"
        ]


class TestCorrelatedSignalsInTheLoop:
    """mem_percent and mem_available_mb fire together on every live cycle, on both
    nodes — four signals for two events, against three narration slots."""

    def _pair(self):
        return [
            signal(source="system:mem_percent", score=4.0),
            signal(source="system:mem_available_mb", score=3.0, direction="below"),
        ]

    @pytest.mark.asyncio
    async def test_only_one_of_the_pair_is_narrated(self, store, readings, agent):
        loop = make_loop(store, readings, agent)
        with with_signals(self._pair()):
            await loop.run_once()
        prompt = agent.query.await_args[0][0]
        assert "system:mem_percent" in prompt
        assert "system:mem_available_mb" not in prompt

    @pytest.mark.asyncio
    async def test_dedup_frees_a_slot_for_a_different_event(
        self, store, readings, agent
    ):
        """The point of the exercise: without dedup the pair would consume two of
        three slots and crowd out a genuinely separate finding."""
        loop = make_loop(store, readings, agent, max_signals=2)
        with with_signals(self._pair() + [
            signal(source="hcsr04:distance", detector="flatline",
                   direction="flat", score=2.0),
        ]):
            await loop.run_once()
        prompt = agent.query.await_args[0][0]
        assert "hcsr04:distance" in prompt

    @pytest.mark.asyncio
    async def test_the_suppressed_source_still_gets_attribution(
        self, store, readings, agent
    ):
        """It is part of the same event, so feedback on the observation should
        reach both sources."""
        agent.query = AsyncMock(return_value=(
            '{"observations": [{"text": "Memory use rose to 81.4 from 74.2.",'
            ' "type": "anomaly", "confidence": 0.9, "related_sources": []}],'
            ' "memories": []}'
        ))
        loop = make_loop(store, readings, agent)
        with with_signals(self._pair()):
            await loop.run_once()
        stored = store.get_observations_since_id(0, limit=1)[0]
        assert stored["related_sources"] == [
            "system:mem_available_mb", "system:mem_percent"
        ]

    @pytest.mark.asyncio
    async def test_a_rule_on_the_suppressed_source_still_records_a_fire(
        self, store, readings, agent, rules
    ):
        """Dedup is a narration decision. If it also hid the signal from rule
        evaluation, a rule on the quieter twin would look like it never fires and
        would eventually be auto-retired for inactivity it did not have."""
        r = rules.propose("system:mem_available_mb", "level_shift", "below")
        rules.promote(r.id)
        loop = make_loop(store, readings, agent, rules=rules)
        with with_signals(self._pair()):
            await loop.run_once()
        assert rules.get(r.id).fired_count == 1
