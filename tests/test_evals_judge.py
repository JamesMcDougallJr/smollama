"""Tests for the judge layer that need no network.

The calibration validator is the important one: it is what makes a judge run
trustworthy, so a bug here would silently license bad scores. Network-dependent
paths (batch submit/poll) are covered by mocking the SDK surface, not by calling
the API.
"""

import json
from unittest.mock import MagicMock, patch

import pytest

from smollama.evals.judge import (
    CALIBRATION,
    DEFAULT_JUDGE_MODEL,
    DIMENSIONS,
    JUDGE_SCHEMA,
    JudgeRequest,
    calibration_requests,
    validate_judge,
)


def perfect_calibration_scores():
    """Scores that sit inside every expected band."""
    out = {}
    for case in CALIBRATION:
        row = {d: 2 for d in DIMENSIONS}
        row["reason"] = "ok"
        for dim, (lo, hi) in case["expect"].items():
            row[dim] = lo
        out[case["id"]] = row
    return out


class TestJudgeSchema:
    def test_schema_covers_every_dimension_plus_reason(self):
        props = JUDGE_SCHEMA["properties"]
        assert set(DIMENSIONS) | {"reason"} == set(props)
        assert set(JUDGE_SCHEMA["required"]) == set(DIMENSIONS) | {"reason"}

    def test_scores_constrained_to_the_anchor_range(self):
        for dim in DIMENSIONS:
            assert JUDGE_SCHEMA["properties"][dim]["enum"] == [0, 1, 2, 3]

    def test_additional_properties_disallowed(self):
        """Required for structured outputs; also stops the judge inventing dimensions."""
        assert JUDGE_SCHEMA["additionalProperties"] is False

    def test_reason_is_required(self):
        """An unexplained score can't be audited, so the rubric demands one."""
        assert "reason" in JUDGE_SCHEMA["required"]


class TestCalibration:
    def test_calibration_covers_the_known_failure_modes(self):
        ids = {c["id"] for c in CALIBRATION}
        assert {"cal_good", "cal_restating", "cal_essay"} <= ids

    def test_restating_case_expects_low_insight(self):
        """Anchored at 1: this is gemma3:1b's observed behaviour, not a 2."""
        case = next(c for c in CALIBRATION if c["id"] == "cal_restating")
        lo, hi = case["expect"]["insight"]
        assert hi <= 1

    def test_essay_case_expects_low_concision(self):
        case = next(c for c in CALIBRATION if c["id"] == "cal_essay")
        assert case["expect"]["concision"][1] <= 1

    def test_requests_built_for_every_calibration_case(self):
        reqs = calibration_requests()
        assert len(reqs) == len(CALIBRATION)
        assert all(isinstance(r, JudgeRequest) for r in reqs)
        assert {r.key for r in reqs} == {c["id"] for c in CALIBRATION}


class TestValidateJudge:
    def test_accepts_scores_inside_every_band(self):
        ok, problems = validate_judge(perfect_calibration_scores())
        assert ok, problems

    def test_rejects_judge_that_cannot_spot_a_restatement(self):
        """The failure this guard exists for: restating scored as real insight."""
        scores = perfect_calibration_scores()
        scores["cal_restating"]["insight"] = 3
        ok, problems = validate_judge(scores)
        assert not ok
        assert any("cal_restating" in p and "insight" in p for p in problems)

    def test_rejects_judge_that_rewards_padding(self):
        scores = perfect_calibration_scores()
        scores["cal_essay"]["concision"] = 3
        ok, problems = validate_judge(scores)
        assert not ok
        assert any("cal_essay" in p for p in problems)

    def test_missing_calibration_score_invalidates(self):
        scores = perfect_calibration_scores()
        del scores["cal_good"]
        ok, problems = validate_judge(scores)
        assert not ok
        assert any("no score returned" in p for p in problems)

    def test_errored_calibration_score_invalidates(self):
        scores = perfect_calibration_scores()
        scores["cal_good"] = {"error": "unparseable"}
        ok, problems = validate_judge(scores)
        assert not ok

    def test_non_integer_score_invalidates(self):
        """A string "3" is not a valid score — the schema should prevent it, but
        the validator must not accept it if the schema was bypassed."""
        scores = perfect_calibration_scores()
        scores["cal_good"]["insight"] = "3"
        ok, problems = validate_judge(scores)
        assert not ok

    def test_reports_every_problem_not_just_the_first(self):
        scores = perfect_calibration_scores()
        scores["cal_restating"]["insight"] = 3
        scores["cal_essay"]["concision"] = 3
        ok, problems = validate_judge(scores)
        assert not ok
        assert len(problems) >= 2


class TestBatchSubmission:
    """Verify the request shape without touching the network."""

    def _fake_client(self, results):
        client = MagicMock()
        batch = MagicMock(id="batch_1")
        client.messages.batches.create.return_value = batch
        client.messages.batches.retrieve.return_value = MagicMock(
            processing_status="ended"
        )
        client.messages.batches.results.return_value = results
        return client

    def _result(self, custom_id, payload):
        block = MagicMock(type="text", text=json.dumps(payload))
        msg = MagicMock(content=[block])
        return MagicMock(custom_id=custom_id, result=MagicMock(type="succeeded", message=msg))

    def test_uses_batch_api_and_keys_by_custom_id(self):
        from smollama.evals import judge as judge_mod

        payload = {d: 2 for d in DIMENSIONS} | {"reason": "fine"}
        # Results deliberately returned out of order — must key by custom_id.
        client = self._fake_client([
            self._result("b", payload),
            self._result("a", payload),
        ])
        with patch.object(judge_mod, "_client", return_value=client):
            scores = judge_mod.judge_batch(
                [
                    JudgeRequest("a", "readings a", {"observations": []}),
                    JudgeRequest("b", "readings b", {"observations": []}),
                ],
                poll_seconds=0,
            )
        assert set(scores) == {"a", "b"}
        client.messages.batches.create.assert_called_once()

    def test_request_is_blinded_and_schema_constrained(self):
        from smollama.evals import judge as judge_mod

        client = self._fake_client([])
        with patch.object(judge_mod, "_client", return_value=client):
            judge_mod.judge_batch(
                [JudgeRequest("a", "system:cpu_temp: 82.0", {"observations": []})],
                model="claude-opus-5",
                poll_seconds=0,
            )
        sent = client.messages.batches.create.call_args.kwargs["requests"][0]
        params = sent["params"]
        assert params["model"] == "claude-opus-5"
        assert params["output_config"]["format"]["schema"] is JUDGE_SCHEMA
        body = params["messages"][0]["content"]
        # Blinding: the graded model must not be identifiable from the prompt.
        for leak in ("qwen", "gemma", "ollama", "run_"):
            assert leak not in body.lower()

    def test_failed_result_recorded_not_raised(self):
        from smollama.evals import judge as judge_mod

        bad = MagicMock(custom_id="a", result=MagicMock(type="errored"))
        client = self._fake_client([bad])
        with patch.object(judge_mod, "_client", return_value=client):
            scores = judge_mod.judge_batch(
                [JudgeRequest("a", "x", {"observations": []})], poll_seconds=0
            )
        assert scores["a"]["error"] == "errored"

    def test_unparseable_text_recorded_not_raised(self):
        """One bad row must not discard the rest of the batch."""
        from smollama.evals import judge as judge_mod

        block = MagicMock(type="text", text="not json")
        msg = MagicMock(content=[block])
        row = MagicMock(custom_id="a", result=MagicMock(type="succeeded", message=msg))
        client = self._fake_client([row])
        with patch.object(judge_mod, "_client", return_value=client):
            scores = judge_mod.judge_batch(
                [JudgeRequest("a", "x", {"observations": []})], poll_seconds=0
            )
        assert scores["a"]["error"] == "unparseable"

    def test_timeout_raises_rather_than_hanging(self):
        from smollama.evals import judge as judge_mod

        client = MagicMock()
        client.messages.batches.create.return_value = MagicMock(id="b")
        client.messages.batches.retrieve.return_value = MagicMock(
            processing_status="in_progress"
        )
        with patch.object(judge_mod, "_client", return_value=client):
            with pytest.raises(TimeoutError):
                judge_mod.judge_batch(
                    [JudgeRequest("a", "x", {})], poll_seconds=0, timeout_seconds=-1
                )


def test_default_judge_model_is_a_current_frontier_id():
    """Guards against a stale default; the judge must outclass what it grades."""
    assert DEFAULT_JUDGE_MODEL in {"claude-opus-5", "claude-fable-5"}
