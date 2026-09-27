"""Tests for LLM rule authoring and review — Phases 4 and 5.

Both phases hand a bounded, pre-filtered decision to a model and constrain the
answer with a schema. Neither lets the model pick a number, and neither lets it
activate or delete anything: authored rules land `proposed` (covering nothing) and
review decisions are state transitions with a required reason.

No network here — the model call is injected, so these test the guardrails rather
than a model's taste.
"""

from datetime import datetime, timedelta, timezone

import pytest

from smollama.detectors import Signal
from smollama.rules import MaintenanceConfig, RuleStore
from smollama.rules.author import (
    AUTHOR_SCHEMA,
    AuthorConfig,
    author_rules,
    build_author_prompt,
)
from smollama.rules.review import (
    REVIEW_SCHEMA,
    ReviewConfig,
    apply_review,
    nominate_for_review,
)

NOW = datetime(2026, 9, 27, 12, 0, 0, tzinfo=timezone.utc)


@pytest.fixture
def store(tmp_path):
    s = RuleStore(str(tmp_path / "r.db"))
    s.connect()
    yield s
    s.close()


def signal(source="system:cpu_temp", detector="level_shift", direction="above",
           score=4.5):
    return Signal(
        source=source, detector=detector, score=score, direction=direction,
        detail=f"{source} moved to 82 against a baseline of 50", first_seen=NOW,
        value=82.0, baseline=50.0,
    )


def fake_llm(payload):
    """Stand-in for the model call: returns a fixed structured payload."""
    async def _call(*args, **kwargs):
        return payload
    return _call


# ── Phase 4: authoring ──────────────────────────────────────────────────────


class TestAuthorSchema:
    def test_threshold_is_a_spec_not_a_number(self):
        """The model names the signal; the data sets the number. A free-form
        numeric threshold would be derived from n=1."""
        spec = AUTHOR_SCHEMA["properties"]["rules"]["items"]["properties"]["threshold_spec"]
        assert "enum" in spec
        # "none" is allowed for structural detectors (flatline, stale) which have
        # nothing to compare against a value; everything else must be a fitted spec.
        assert all(v == "none" or v.startswith("fit:") for v in spec["enum"]), spec["enum"]
        assert not any(v.startswith("literal:") for v in spec["enum"])
        assert "none" in spec["enum"]

    def test_detector_and_direction_are_enums(self):
        props = AUTHOR_SCHEMA["properties"]["rules"]["items"]["properties"]
        assert set(props["detector"]["enum"]) >= {"flatline", "stale", "level_shift"}
        assert set(props["direction"]["enum"]) == {"above", "below", "flat", "absent"}

    def test_rule_count_is_bounded_by_schema(self):
        assert AUTHOR_SCHEMA["properties"]["rules"]["maxItems"] >= 1
        assert AUTHOR_SCHEMA["additionalProperties"] is False

    def test_rationale_required(self):
        req = AUTHOR_SCHEMA["properties"]["rules"]["items"]["required"]
        assert "rationale" in req


class TestAuthorPrompt:
    def test_prompt_contains_only_the_given_signals(self):
        prompt = build_author_prompt([signal()], config=AuthorConfig())
        assert "system:cpu_temp" in prompt
        assert "hcsr04" not in prompt

    def test_prompt_is_empty_when_nothing_uncovered(self):
        assert build_author_prompt([], config=AuthorConfig()) is None

    def test_prompt_bounded_to_max_signals(self):
        signals = [signal(source=f"s{i}", score=float(i)) for i in range(20)]
        prompt = build_author_prompt(signals, config=AuthorConfig(max_signals=3))
        assert prompt.count("s") > 0
        # Highest-scoring only, so the budget goes to the strongest evidence.
        assert "s19" in prompt and "s0:" not in prompt


class TestAuthorRules:
    @pytest.mark.asyncio
    async def test_authored_rules_land_proposed_never_active(self, store):
        """An LLM-invented rule encodes an assumption nobody approved."""
        llm = fake_llm({"rules": [{
            "source": "system:cpu_temp", "detector": "level_shift",
            "direction": "above", "threshold_spec": "fit:p99_7d",
            "rationale": "thermal risk",
        }]})
        created = await author_rules([signal()], store, llm=llm, config=AuthorConfig())
        assert len(created) == 1
        assert store.get(created[0].id).state == "proposed"
        assert store.active_rules() == []

    @pytest.mark.asyncio
    async def test_no_signals_means_no_llm_call(self, store):
        called = False

        async def llm(*a, **k):
            nonlocal called
            called = True
            return {"rules": []}

        assert await author_rules([], store, llm=llm, config=AuthorConfig()) == []
        assert not called, "burned a model call with nothing to author about"

    @pytest.mark.asyncio
    async def test_rule_for_a_source_not_in_the_signals_is_rejected(self, store):
        """The model must not invent a source it was never shown."""
        llm = fake_llm({"rules": [{
            "source": "invented:source", "detector": "level_shift",
            "direction": "above", "threshold_spec": "fit:p99_7d",
            "rationale": "made up",
        }]})
        created = await author_rules([signal()], store, llm=llm, config=AuthorConfig())
        assert created == []
        assert store.all_rules() == []

    @pytest.mark.asyncio
    async def test_literal_threshold_is_rejected(self, store):
        """Even if the schema is bypassed, a model-chosen number must not land."""
        llm = fake_llm({"rules": [{
            "source": "system:cpu_temp", "detector": "level_shift",
            "direction": "above", "threshold_spec": "literal:80",
            "rationale": "picked a number",
        }]})
        assert await author_rules([signal()], store, llm=llm, config=AuthorConfig()) == []

    @pytest.mark.asyncio
    async def test_unparseable_response_authors_nothing(self, store):
        assert await author_rules([signal()], store, llm=fake_llm("not json"),
                                  config=AuthorConfig()) == []

    @pytest.mark.asyncio
    async def test_count_is_capped_even_if_the_model_returns_more(self, store):
        llm = fake_llm({"rules": [
            {"source": "system:cpu_temp", "detector": "level_shift",
             "direction": d, "threshold_spec": "fit:p99_7d", "rationale": "x"}
            for d in ("above", "below", "flat", "absent")
        ]})
        created = await author_rules([signal()], store, llm=llm,
                                     config=AuthorConfig(max_rules_per_cycle=2))
        assert len(created) <= 2

    @pytest.mark.asyncio
    async def test_reproposing_an_existing_identity_updates_not_duplicates(self, store):
        existing = store.propose("system:cpu_temp", "level_shift", "above")
        store.promote(existing.id)
        llm = fake_llm({"rules": [{
            "source": "system:cpu_temp", "detector": "level_shift",
            "direction": "above", "threshold_spec": "fit:p95_24h",
            "rationale": "revised",
        }]})
        await author_rules([signal()], store, llm=llm, config=AuthorConfig())
        assert len(store.all_rules()) == 1
        # Re-proposal must not silently deactivate a rule already in service.
        assert store.get(existing.id).state == "active"


# ── Phase 5: review ─────────────────────────────────────────────────────────


class TestNomination:
    """Deterministic triage picks the candidates; the LLM never sees the table."""

    def test_nominates_nothing_when_all_rules_are_healthy(self, store):
        r = store.propose("s", "trend", "above"); store.promote(r.id)
        for i in range(50):
            store.record_evaluation(r.id, fired=(i % 10 == 0), now=NOW)
        assert nominate_for_review(store, now=NOW, config=ReviewConfig()) == []

    def test_nominates_a_rule_that_fires_sometimes(self, store):
        """The ambiguous middle — neither clearly noise nor clearly dead."""
        old = NOW - timedelta(days=20)
        r = store.propose("s", "trend", "above", now=old); store.promote(r.id, now=old)
        for i in range(60):
            store.record_evaluation(r.id, fired=(i % 3 == 0), now=NOW)
        assert [n.id for n in nominate_for_review(store, now=NOW, config=ReviewConfig())] == [r.id]

    def test_respects_the_review_budget(self, store):
        old = NOW - timedelta(days=20)
        for i in range(10):
            r = store.propose(f"s{i}", "trend", "above", now=old)
            store.promote(r.id, now=old)
            for j in range(60):
                store.record_evaluation(r.id, fired=(j % 3 == 0), now=NOW)
        nominated = nominate_for_review(store, now=NOW, config=ReviewConfig(max_candidates=3))
        assert len(nominated) == 3

    def test_young_rules_are_not_nominated(self, store):
        """Minimum age, or create/review oscillation burns the budget."""
        r = store.propose("s", "trend", "above", now=NOW); store.promote(r.id, now=NOW)
        for i in range(60):
            store.record_evaluation(r.id, fired=(i % 3 == 0), now=NOW)
        assert nominate_for_review(store, now=NOW, config=ReviewConfig()) == []

    def test_only_active_rules_are_nominated(self, store):
        old = NOW - timedelta(days=20)
        r = store.propose("s", "trend", "above", now=old)  # proposed, not active
        for i in range(60):
            store.record_evaluation(r.id, fired=(i % 3 == 0), now=NOW)
        assert nominate_for_review(store, now=NOW, config=ReviewConfig()) == []


class TestReviewSchema:
    def test_decision_is_a_four_way_enum(self):
        decision = REVIEW_SCHEMA["properties"]["decisions"]["items"]["properties"]["decision"]
        assert set(decision["enum"]) == {"keep", "retune", "mute", "retire"}

    def test_reason_required(self):
        req = REVIEW_SCHEMA["properties"]["decisions"]["items"]["required"]
        assert "reason" in req and "rule_id" in req


class TestApplyReview:
    @pytest.mark.asyncio
    async def test_keep_leaves_the_rule_active(self, store):
        r = store.propose("s", "trend", "above"); store.promote(r.id)
        await apply_review(store, [r], llm=fake_llm(
            {"decisions": [{"rule_id": r.id, "decision": "keep", "reason": "still valid"}]}
        ), config=ReviewConfig(), now=NOW)
        assert store.get(r.id).state == "active"

    @pytest.mark.asyncio
    async def test_mute_records_the_model_reason(self, store):
        r = store.propose("s", "trend", "above"); store.promote(r.id)
        await apply_review(store, [r], llm=fake_llm(
            {"decisions": [{"rule_id": r.id, "decision": "mute",
                            "reason": "fires on every deploy"}]}
        ), config=ReviewConfig(), now=NOW)
        got = store.get(r.id)
        assert got.state == "muted"
        assert "deploy" in got.state_reason

    @pytest.mark.asyncio
    async def test_retune_refits_rather_than_letting_the_model_pick(self, store):
        r = store.propose("s", "level_shift", "above", threshold_spec="fit:p99_7d")
        store.promote(r.id)
        await apply_review(store, [r], llm=fake_llm(
            {"decisions": [{"rule_id": r.id, "decision": "retune", "reason": "drifted"}]}
        ), config=ReviewConfig(), now=NOW,
            history_for={"s": [float(i) for i in range(1, 101)]})
        got = store.get(r.id)
        assert got.state == "active", "retune must not change state"
        assert got.threshold_value == pytest.approx(99.0, abs=1.5)

    @pytest.mark.asyncio
    async def test_retire_of_a_rule_that_has_fired_is_refused(self, store):
        """Higher evidence bar: a wrongly retired rule makes silence nobody sees."""
        r = store.propose("s", "trend", "above"); store.promote(r.id)
        store.record_evaluation(r.id, fired=True, now=NOW)
        actions = await apply_review(store, [r], llm=fake_llm(
            {"decisions": [{"rule_id": r.id, "decision": "retire", "reason": "dunno"}]}
        ), config=ReviewConfig(), now=NOW)
        assert store.get(r.id).state == "active"
        assert any(a.action == "refused" for a in actions)

    @pytest.mark.asyncio
    async def test_retire_of_a_never_fired_rule_is_allowed(self, store):
        r = store.propose("s", "trend", "above"); store.promote(r.id)
        store.record_evaluation(r.id, fired=False, now=NOW)
        await apply_review(store, [r], llm=fake_llm(
            {"decisions": [{"rule_id": r.id, "decision": "retire",
                            "reason": "source replaced"}]}
        ), config=ReviewConfig(), now=NOW)
        assert store.get(r.id).state == "retired"

    @pytest.mark.asyncio
    async def test_decision_without_a_reason_is_refused(self, store):
        r = store.propose("s", "trend", "above"); store.promote(r.id)
        actions = await apply_review(store, [r], llm=fake_llm(
            {"decisions": [{"rule_id": r.id, "decision": "mute", "reason": ""}]}
        ), config=ReviewConfig(), now=NOW)
        assert store.get(r.id).state == "active"
        assert any(a.action == "refused" for a in actions)

    @pytest.mark.asyncio
    async def test_decision_for_a_rule_not_nominated_is_ignored(self, store):
        """The model must not reach beyond the candidates it was handed."""
        nominated = store.propose("a", "trend", "above"); store.promote(nominated.id)
        other = store.propose("b", "trend", "above"); store.promote(other.id)
        await apply_review(store, [nominated], llm=fake_llm(
            {"decisions": [{"rule_id": other.id, "decision": "retire",
                            "reason": "reaching"}]}
        ), config=ReviewConfig(), now=NOW)
        assert store.get(other.id).state == "active"

    @pytest.mark.asyncio
    async def test_no_candidates_means_no_llm_call(self, store):
        called = False

        async def llm(*a, **k):
            nonlocal called
            called = True
            return {"decisions": []}

        assert await apply_review(store, [], llm=llm, config=ReviewConfig(), now=NOW) == []
        assert not called

    @pytest.mark.asyncio
    async def test_unparseable_response_changes_nothing(self, store):
        r = store.propose("s", "trend", "above"); store.promote(r.id)
        await apply_review(store, [r], llm=fake_llm("garbage"),
                           config=ReviewConfig(), now=NOW)
        assert store.get(r.id).state == "active"
