"""Tests for the deterministic half of model evaluation (smollama/evals/checks.py).

These gates exist so the LLM judge is never asked to decide something code can
decide, and so a model that emits unparseable or fabricated output scores 0
without spending judge tokens. See docs/model-evaluation.md.
"""

import pytest

from smollama.evals.cases import Case
from smollama.evals.checks import (
    extract_numbers,
    run_gates,
    score_case,
    score_metrics,
)

MAX_ITEMS = 3
MAX_CHARS = 200

ANOMALY_CASE = Case(
    id="cpu_hot",
    current={"system:cpu_temp": 82.0, "system:mem_percent": 97.5},
    history={"system:cpu_temp": "20 readings, min=49.0, max=82.0, avg=71.4"},
    expect_detect=["system:cpu_temp"],
    min_observations=1,
    provenance="synthetic",
)

NORMAL_CASE = Case(
    id="steady",
    current={"system:cpu_temp": 48.5, "system:mem_percent": 42.0},
    history={"system:cpu_temp": "20 readings, min=47.9, max=49.1, avg=48.4"},
    expect_detect=[],
    min_observations=0,
    provenance="synthetic",
)


def obs(text, type_="anomaly", sources=None, confidence=0.9):
    return {
        "text": text,
        "type": type_,
        "confidence": confidence,
        "related_sources": sources if sources is not None else ["system:cpu_temp"],
    }


def response(observations, memories=None):
    return {"observations": observations, "memories": memories or []}


class TestExtractNumbers:
    """Underpins the hallucination gate, so its edge cases matter."""

    def test_finds_integers_and_floats(self):
        assert extract_numbers("cpu 82 and mem 97.5 percent") == {82.0, 97.5}

    def test_ignores_numbers_inside_identifiers(self):
        """hcsr04 / ipv6 / all-minilm:l6-v2 must not read as reported values."""
        nums = extract_numbers("hcsr04:distance reported 0.0 cm")
        assert 0.0 in nums
        assert 4.0 not in nums

    def test_handles_negatives_and_percent(self):
        assert extract_numbers("offset -7 and load 0.04") == {-7.0, 0.04}

    def test_empty_on_no_numbers(self):
        assert extract_numbers("something happened") == set()

    def test_unit_suffixed_numbers_are_not_truncated(self):
        """"82.5C" must read as 82.5, not 82 — truncation caused false gate failures."""
        assert extract_numbers("temp 82.5C and latency 32.4ms") == {82.5, 32.4}

    def test_model_names_donate_no_numbers(self):
        """all-minilm:l6-v2 and qwen2.5:1.5b must not widen the allowed set."""
        assert extract_numbers("embedded with all-minilm:l6-v2") == set()


class TestGates:
    def test_valid_response_passes_every_gate(self):
        r = response([obs("CPU temp reached 82.0 C, well above its 71.4 average.")])
        g = run_gates(r, ANOMALY_CASE, max_items=MAX_ITEMS, max_chars=MAX_CHARS)
        assert g.passed, g.failures

    def test_non_dict_response_fails_schema(self):
        g = run_gates("not json", ANOMALY_CASE, max_items=MAX_ITEMS, max_chars=MAX_CHARS)
        assert not g.passed
        assert "schema" in g.failures

    def test_missing_observations_key_fails_schema(self):
        g = run_gates({"memories": []}, ANOMALY_CASE, max_items=MAX_ITEMS, max_chars=MAX_CHARS)
        assert not g.passed
        assert "schema" in g.failures

    def test_too_many_items_fails(self):
        r = response([obs(f"CPU temp is 82.0 C, reading {i}") for i in range(4)])
        g = run_gates(r, ANOMALY_CASE, max_items=MAX_ITEMS, max_chars=MAX_CHARS)
        assert not g.passed
        assert "max_items" in g.failures

    def test_overlong_text_fails(self):
        r = response([obs("CPU temp is 82.0 C. " + "padding " * 60)])
        g = run_gates(r, ANOMALY_CASE, max_items=MAX_ITEMS, max_chars=MAX_CHARS)
        assert not g.passed
        assert "max_chars" in g.failures

    def test_bad_enum_fails(self):
        r = response([obs("CPU temp is 82.0 C", type_="pattern|anomaly")])
        g = run_gates(r, ANOMALY_CASE, max_items=MAX_ITEMS, max_chars=MAX_CHARS)
        assert not g.passed
        assert "enum" in g.failures

    def test_invented_source_fails(self):
        r = response([obs("CPU temp is 82.0 C", sources=["system:gpu_temp"])])
        g = run_gates(r, ANOMALY_CASE, max_items=MAX_ITEMS, max_chars=MAX_CHARS)
        assert not g.passed
        assert "sources_exist" in g.failures

    def test_invented_number_fails(self):
        """The hallucination gate: 91.0 never appeared in the input."""
        r = response([obs("CPU temp reached 91.0 C")])
        g = run_gates(r, ANOMALY_CASE, max_items=MAX_ITEMS, max_chars=MAX_CHARS)
        assert not g.passed
        assert "no_invented_numbers" in g.failures

    def test_input_numbers_are_accepted_in_any_written_form(self):
        """82 and 82.0 are the same value; the gate must not punish formatting."""
        r = response([obs("CPU temp reached 82 C, above the 71.4 average")])
        g = run_gates(r, ANOMALY_CASE, max_items=MAX_ITEMS, max_chars=MAX_CHARS)
        assert g.passed, g.failures

    def test_history_numbers_count_as_input(self):
        """min/max/avg from the history block are legitimately quotable."""
        r = response([obs("CPU temp rose from 49.0 to 82.0")])
        g = run_gates(r, ANOMALY_CASE, max_items=MAX_ITEMS, max_chars=MAX_CHARS)
        assert g.passed, g.failures

    def test_empty_observations_passes_gates(self):
        """Reporting nothing is valid output — restraint is scored separately."""
        g = run_gates(response([]), NORMAL_CASE, max_items=MAX_ITEMS, max_chars=MAX_CHARS)
        assert g.passed, g.failures

    def test_multiple_failures_all_reported(self):
        r = response([obs("CPU hit 91.0 C", type_="bogus", sources=["nope"])])
        g = run_gates(r, ANOMALY_CASE, max_items=MAX_ITEMS, max_chars=MAX_CHARS)
        assert {"enum", "sources_exist", "no_invented_numbers"} <= set(g.failures)


class TestMetrics:
    def test_detection_hit(self):
        r = response([obs("CPU temp reached 82.0 C")])
        m = score_metrics(r, ANOMALY_CASE)
        assert m["detection"] == 1.0

    def test_detection_miss_when_silent(self):
        """The always-silent failure mode must score zero, not be ignored."""
        m = score_metrics(response([]), ANOMALY_CASE)
        assert m["detection"] == 0.0

    def test_detection_miss_when_wrong_source(self):
        r = response([obs("Memory is at 97.5 percent", sources=["system:mem_percent"])])
        m = score_metrics(r, ANOMALY_CASE)
        assert m["detection"] == 0.0

    def test_detection_counts_source_named_in_text_without_related_sources(self):
        r = response([obs("system:cpu_temp reached 82.0 C", sources=[])])
        assert score_metrics(r, ANOMALY_CASE)["detection"] == 1.0

    def test_restraint_rewarded_on_normal_case(self):
        m = score_metrics(response([]), NORMAL_CASE)
        assert m["restraint"] == 1.0

    def test_restraint_penalised_for_reporting_on_normal_case(self):
        r = response([obs("CPU temp is 48.5 C", type_="status",
                          sources=["system:cpu_temp"])])
        m = score_metrics(r, NORMAL_CASE)
        assert m["restraint"] == 0.0

    def test_restraint_is_not_scored_on_anomaly_cases(self):
        assert score_metrics(response([]), ANOMALY_CASE).get("restraint") is None

    def test_detection_is_not_scored_on_normal_cases(self):
        assert score_metrics(response([]), NORMAL_CASE).get("detection") is None


class TestScoreCase:
    def test_gate_failure_zeroes_the_case(self):
        """No credit for well-written output that doesn't parse or fabricates."""
        r = response([obs("CPU temp reached 91.0 C")])  # invented number
        s = score_case(r, ANOMALY_CASE, max_items=MAX_ITEMS, max_chars=MAX_CHARS)
        assert s["score"] == 0.0
        assert s["gates_passed"] is False
        assert "no_invented_numbers" in s["failures"]

    def test_passing_case_carries_metrics_and_awaits_judge(self):
        r = response([obs("CPU temp reached 82.0 C, above its 71.4 average.")])
        s = score_case(r, ANOMALY_CASE, max_items=MAX_ITEMS, max_chars=MAX_CHARS)
        assert s["gates_passed"] is True
        assert s["metrics"]["detection"] == 1.0
        assert s["judge"] is None  # stage 2 not run yet

    def test_silent_on_anomaly_passes_gates_but_scores_zero_detection(self):
        """The qwen2.5:1.5b failure: valid output, no detection. Must be visible."""
        s = score_case(response([]), ANOMALY_CASE, max_items=MAX_ITEMS, max_chars=MAX_CHARS)
        assert s["gates_passed"] is True
        assert s["metrics"]["detection"] == 0.0


class TestCaseFixtures:
    """The shipped golden cases must be well-formed and include both polarities."""

    def test_builtin_cases_load(self):
        from smollama.evals.cases import load_cases

        cases = load_cases()
        assert len(cases) >= 6
        assert all(isinstance(c, Case) for c in cases)

    def test_includes_normal_and_anomalous(self):
        from smollama.evals.cases import load_cases

        cases = load_cases()
        assert any(c.min_observations == 0 for c in cases), "no restraint cases"
        assert any(c.expect_detect for c in cases), "no detection cases"

    def test_expected_sources_exist_in_their_own_inputs(self):
        """A case that expects detection of a source it never supplies is a broken case."""
        from smollama.evals.cases import load_cases

        for c in load_cases():
            for src in c.expect_detect:
                assert src in c.current or src in c.history, f"{c.id}: {src} not in input"

    def test_every_case_records_provenance(self):
        from smollama.evals.cases import load_cases

        for c in load_cases():
            assert c.provenance, f"{c.id} has no provenance"

    def test_real_production_failures_are_represented(self):
        from smollama.evals.cases import load_cases

        ids = {c.id for c in load_cases()}
        assert {"writer_silent", "stuck_sensor"} <= ids
