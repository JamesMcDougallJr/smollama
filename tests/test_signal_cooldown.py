"""Cooldown on re-narrating an unchanged signal.

Measured on the live master: an uncovered signal produced one model call and one stored
observation per cycle, forever. The stuck hcsr04 flatline gave 58 near-identical
observations in 23 hours (~65s of inference each). The prompt already says "omit it if a
past observation reports the same deviation"; qwen2.5:1.5b does not honour that, so the
fix is structural.

What makes this subtle is deciding when a signal is genuinely *new* again:

- it cleared and came back                      -> new
- its direction flipped                         -> new (a real change, not a repeat)
- its score escalated materially                -> new
- the winner of a correlated pair swapped       -> NOT new (same event, other name)
- the model failed or returned garbage          -> NOT handled (never reported)
- the model answered with nothing to report     -> handled (re-asking is the waste)
"""

from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from smollama.detectors import Signal
from smollama.detectors.cooldown import SignalCooldown
from smollama.memory import LocalStore, MockEmbeddings, ObservationLoop
from smollama.readings import Reading, ReadingManager

NOW = datetime(2026, 10, 11, 12, 0, 0, tzinfo=timezone.utc)
HOUR = timedelta(hours=1)


def sig(source="system:load_avg", detector="level_shift", direction="above",
        score=3.0, **meta):
    return Signal(source=source, detector=detector, score=score, direction=direction,
                  detail=f"{source} moved", first_seen=NOW, meta=meta)


@pytest.fixture
def cd(tmp_path):
    c = SignalCooldown(str(tmp_path / "m.db"), cooldown_seconds=12 * 3600)
    c.connect()
    yield c
    c.close()


class TestSuppression:
    def test_a_first_sighting_is_allowed(self, cd):
        s = sig()
        assert cd.filter([s], now=NOW) == [s]

    def test_a_narrated_signal_is_suppressed_within_the_cooldown(self, cd):
        s = sig()
        cd.record([s], now=NOW)
        assert cd.filter([s], now=NOW + 20 * timedelta(minutes=1)) == []
        assert cd.filter([s], now=NOW + 11 * HOUR) == []

    def test_it_is_allowed_again_once_the_cooldown_elapses(self, cd):
        """A persistent problem is reminded about, not forgotten."""
        s = sig()
        cd.record([s], now=NOW)
        assert cd.filter([s], now=NOW + 13 * HOUR) == [s]

    def test_unrecorded_signals_are_not_suppressed(self, cd):
        """Only what was actually narrated enters the cooldown."""
        a, b = sig("a:one"), sig("b:two")
        cd.record([a], now=NOW)
        assert cd.filter([a, b], now=NOW + HOUR) == [b]

    def test_different_detectors_on_one_source_are_independent(self, cd):
        cd.record([sig(detector="flatline", direction="flat")], now=NOW)
        other = sig(detector="stale", direction="absent")
        assert cd.filter([other], now=NOW + HOUR) == [other]

    def test_a_zero_cooldown_disables_it(self, tmp_path):
        c = SignalCooldown(str(tmp_path / "m.db"), cooldown_seconds=0)
        c.connect()
        s = sig()
        c.record([s], now=NOW)
        assert c.filter([s], now=NOW + HOUR) == [s]
        c.close()


class TestWhenASignalIsNewAgain:
    def test_a_direction_flip_is_a_real_change(self, cd):
        cd.record([sig(direction="above")], now=NOW)
        flipped = sig(direction="below")
        assert cd.filter([flipped], now=NOW + HOUR) == [flipped]

    def test_a_material_escalation_breaks_through(self, cd):
        cd.record([sig(score=2.0)], now=NOW)
        worse = sig(score=3.1)
        assert cd.filter([worse], now=NOW + HOUR) == [worse]

    def test_a_small_wobble_in_score_does_not(self, cd):
        cd.record([sig(score=2.0)], now=NOW)
        assert cd.filter([sig(score=2.6)], now=NOW + HOUR) == []

    def test_a_signal_that_cleared_and_returned_is_new(self, cd):
        s = sig()
        cd.record([s], now=NOW)
        cd.forget_cleared(present=set())  # a cycle where it was absent
        assert cd.filter([s], now=NOW + HOUR + timedelta(minutes=20)) == [s]

    def test_still_present_signals_are_not_forgotten(self, cd):
        s = sig()
        cd.record([s], now=NOW)
        cd.forget_cleared(present={(s.source, s.detector)})
        assert cd.filter([s], now=NOW + 2 * HOUR) == []


class TestCorrelatedPairs:
    def test_the_twin_is_covered_when_the_winner_is_narrated(self, cd):
        """mem_percent and mem_available_mb are one event. If narrating one left the
        other eligible, the winner swapping next cycle would report it again."""
        winner = sig("system:mem_percent", score=4.0,
                     correlated=["system:mem_available_mb"])
        cd.record([winner], now=NOW)
        twin = sig("system:mem_available_mb", direction="below", score=4.1)
        assert cd.filter([twin], now=NOW + HOUR) == []

    def test_a_twin_that_escalates_still_breaks_through(self, cd):
        winner = sig("system:mem_percent", score=2.0,
                     correlated=["system:mem_available_mb"])
        cd.record([winner], now=NOW)
        twin = sig("system:mem_available_mb", direction="below", score=4.5)
        assert cd.filter([twin], now=NOW + HOUR) == [twin]


    def test_a_twin_is_not_pruned_on_a_cycle_where_only_its_partner_fires(self, cd):
        """Otherwise the twin's cover is lost the moment it is quiet for one cycle,
        and the next time it fires (as the new winner) the same event is reported
        again under its other name."""
        winner = sig("system:mem_percent", score=4.0,
                     correlated=["system:mem_available_mb"])
        cd.record([winner], now=NOW)
        cd.forget_cleared(present={("system:mem_percent", "level_shift")})
        twin = sig("system:mem_available_mb", direction="below", score=4.1)
        assert cd.filter([twin], now=NOW + HOUR) == []

    def test_a_correlated_group_that_is_wholly_absent_is_forgotten(self, cd):
        winner = sig("system:mem_percent", score=4.0,
                     correlated=["system:mem_available_mb"])
        cd.record([winner], now=NOW)
        cd.forget_cleared(present=set())
        assert cd.filter([winner], now=NOW + HOUR) == [winner]

    def test_the_same_metric_on_another_node_is_not_the_same_event(self, cd):
        winner = sig("system:mem_percent", score=4.0)
        cd.record([winner], now=NOW)
        other_node = sig("jetson-nano:system:mem_percent", score=4.0)
        assert cd.filter([other_node], now=NOW + HOUR) == [other_node]


class TestPersistence:
    def test_the_cooldown_survives_a_restart(self, tmp_path):
        """The service is restarted often; an in-memory cooldown would re-narrate
        everything each time."""
        path = str(tmp_path / "m.db")
        a = SignalCooldown(path); a.connect()
        s = sig(); a.record([s], now=NOW); a.close()
        b = SignalCooldown(path); b.connect()
        assert b.filter([s], now=NOW + HOUR) == []
        b.close()


# ── in the observation loop ─────────────────────────────────────────────────

GOOD = ('{"observations": [{"text": "load moved to 0.27 from 0.0.", "type": "anomaly",'
        ' "confidence": 0.9, "related_sources": []}], "memories": []}')
EMPTY = '{"observations": [], "memories": []}'


def make_loop(tmp_path, cooldown, responses, max_signals=3):
    store = LocalStore(str(tmp_path / "m.db"), "n", MockEmbeddings())
    store.connect()
    readings = MagicMock(spec=ReadingManager)
    readings.read_all = AsyncMock(return_value=[
        Reading("system", "load_avg", 0.27, datetime.now(), "")])
    agent = MagicMock()
    agent.query = AsyncMock(side_effect=responses)
    loop = ObservationLoop(
        store=store, readings=readings, agent=agent, interval_minutes=15,
        lookback_minutes=60, use_detectors=True, cooldown=cooldown,
        max_signals=max_signals,
    )
    return loop, store, agent


def cycles(per_cycle_signals):
    """Patch detection to return a different signal list on each successive cycle."""
    return patch.multiple(
        "smollama.memory.observation_loop",
        load_series=MagicMock(return_value={}),
        known_sources=MagicMock(return_value=[]),
        detect_all=MagicMock(side_effect=per_cycle_signals),
    )


class TestInTheLoop:
    @pytest.mark.asyncio
    async def test_an_unchanged_signal_is_narrated_once_not_every_cycle(self, tmp_path):
        loop, _, agent = make_loop(tmp_path, SignalCooldown(str(tmp_path / "m.db")),
                                   [GOOD, GOOD, GOOD])
        s = sig()
        with cycles([[s], [s], [s]]):
            for _ in range(3):
                await loop.run_once()
        assert agent.query.await_count == 1

    @pytest.mark.asyncio
    async def test_without_a_cooldown_it_still_narrates_every_cycle(self, tmp_path):
        loop, _, agent = make_loop(tmp_path, None, [GOOD, GOOD, GOOD])
        s = sig()
        with cycles([[s], [s], [s]]):
            for _ in range(3):
                await loop.run_once()
        assert agent.query.await_count == 3

    @pytest.mark.asyncio
    async def test_a_failed_model_call_does_not_silence_the_signal(self, tmp_path):
        """Recording before the answer would hide a signal that was never reported."""
        loop, store, agent = make_loop(tmp_path, SignalCooldown(str(tmp_path / "m.db")),
                                       [None, GOOD])
        s = sig()
        with cycles([[s], [s]]):
            await loop.run_once()
            await loop.run_once()
        assert agent.query.await_count == 2
        assert store.get_stats()["observations_count"] == 1

    @pytest.mark.asyncio
    async def test_an_unparseable_answer_does_not_silence_it_either(self, tmp_path):
        loop, _, agent = make_loop(tmp_path, SignalCooldown(str(tmp_path / "m.db")),
                                   ["not json at all", GOOD])
        s = sig()
        with cycles([[s], [s]]):
            await loop.run_once()
            await loop.run_once()
        assert agent.query.await_count == 2

    @pytest.mark.asyncio
    async def test_an_answer_with_nothing_to_report_counts_as_handled(self, tmp_path):
        """Otherwise a signal the model declines to describe is re-asked forever."""
        loop, _, agent = make_loop(tmp_path, SignalCooldown(str(tmp_path / "m.db")),
                                   [EMPTY, GOOD])
        s = sig()
        with cycles([[s], [s]]):
            await loop.run_once()
            await loop.run_once()
        assert agent.query.await_count == 1

    @pytest.mark.asyncio
    async def test_a_cleared_signal_is_narrated_again_when_it_returns(self, tmp_path):
        loop, _, agent = make_loop(tmp_path, SignalCooldown(str(tmp_path / "m.db")),
                                   [GOOD, GOOD])
        s = sig()
        with cycles([[s], [], [s]]):
            for _ in range(3):
                await loop.run_once()
        assert agent.query.await_count == 2

    @pytest.mark.asyncio
    async def test_an_escalated_signal_is_narrated_again(self, tmp_path):
        loop, _, agent = make_loop(tmp_path, SignalCooldown(str(tmp_path / "m.db")),
                                   [GOOD, GOOD])
        with cycles([[sig(score=2.0)], [sig(score=4.5)]]):
            await loop.run_once()
            await loop.run_once()
        assert agent.query.await_count == 2

    @pytest.mark.asyncio
    async def test_a_signal_cut_by_max_signals_is_narrated_next_cycle(self, tmp_path):
        """It was never reported, so it must not be treated as handled."""
        loop, _, agent = make_loop(tmp_path, SignalCooldown(str(tmp_path / "m.db")),
                                   [GOOD, GOOD], max_signals=1)
        a, b = sig("a:one", score=5.0), sig("b:two", score=4.0)
        with cycles([[a, b], [a, b]]):
            await loop.run_once()
            await loop.run_once()
        assert agent.query.await_count == 2
        assert "b:two" in agent.query.await_args_list[1][0][0]
        assert "a:one" not in agent.query.await_args_list[1][0][0]

    @pytest.mark.asyncio
    async def test_a_swapped_winner_in_a_correlated_pair_is_not_re_narrated(self, tmp_path):
        loop, _, agent = make_loop(tmp_path, SignalCooldown(str(tmp_path / "m.db")),
                                   [GOOD, GOOD])
        pct = sig("system:mem_percent", score=3.9)
        avail = sig("system:mem_available_mb", direction="below", score=4.0)
        with cycles([[pct, avail], [pct, avail]]):
            await loop.run_once()   # avail wins (4.0), pct is its twin
            # next cycle the scores swap, so the other becomes the winner
            pct2 = sig("system:mem_percent", score=4.0)
            avail2 = sig("system:mem_available_mb", direction="below", score=3.9)
        with cycles([[pct2, avail2]]):
            await loop.run_once()
        assert agent.query.await_count == 1

    @pytest.mark.asyncio
    async def test_a_failed_detection_pass_does_not_wipe_the_cooldown(self, tmp_path):
        """A failed pass returns no signals, which is indistinguishable from "all
        cleared". Treating it that way would re-narrate everything after any
        transient error."""
        loop, _, agent = make_loop(tmp_path, SignalCooldown(str(tmp_path / "m.db")),
                                   [GOOD, GOOD])
        s = sig()
        with cycles([[s], RuntimeError("db gone"), [s]]):
            for _ in range(3):
                await loop.run_once()
        assert agent.query.await_count == 1

    @pytest.mark.asyncio
    async def test_rule_evaluation_is_unaffected_by_the_cooldown(self, tmp_path):
        """The cooldown decides what is narrated, not what a rule saw."""
        from smollama.rules import RuleStore

        rules = RuleStore(str(tmp_path / "m.db")); rules.connect()
        r = rules.propose("system:load_avg", "level_shift", "above")
        rules.promote(r.id)
        loop, store, agent = make_loop(tmp_path, SignalCooldown(str(tmp_path / "m.db")),
                                       [GOOD])
        loop._rules = rules
        s = sig()
        with cycles([[s], [s]]):
            await loop.run_once()
            await loop.run_once()
        assert rules.get(r.id).fired_count == 2


# ── agent wiring ────────────────────────────────────────────────────────────

from smollama.agent import Agent  # noqa: E402
from smollama.config import Config  # noqa: E402


def agent_config(tmp_path, **memory):
    cfg = Config()
    cfg.memory.db_path = str(tmp_path / "memory.db")
    cfg.memory.embedding_provider = "mock"
    for k, v in memory.items():
        setattr(cfg.memory, k, v)
    return cfg


class TestAgentWiring:
    def test_it_is_on_by_default(self, tmp_path):
        agent = Agent(agent_config(tmp_path))
        assert agent._cooldown is not None
        assert agent._observation_loop._cooldown is agent._cooldown
        assert agent._cooldown.cooldown_seconds == 12 * 3600

    def test_the_knob_sets_the_window(self, tmp_path):
        agent = Agent(agent_config(tmp_path, observation_signal_cooldown_hours=3.0))
        assert agent._cooldown.cooldown_seconds == 3 * 3600

    def test_zero_hours_turns_it_off(self, tmp_path):
        agent = Agent(agent_config(tmp_path, observation_signal_cooldown_hours=0))
        assert agent._cooldown is None
        assert agent._observation_loop._cooldown is None

    def test_it_is_off_when_detectors_are_off(self, tmp_path):
        """It gates the detector pass, which does not exist on the scan path."""
        agent = Agent(agent_config(tmp_path, observation_use_detectors=False))
        assert agent._cooldown is None
