"""Stage-2 evaluation: a cloud model scores the dimensions code cannot.

Only reached by outputs that already passed every stage-1 gate, so no judge
tokens are spent on output that doesn't parse or that fabricates numbers.

Three design choices worth knowing:

**Batch API.** Judging is not latency-sensitive — every case is already generated
and stored. Batches run at 50% of standard price, so a full run costs cents.

**Blinded.** The judge never sees which model produced an output. Model names in
a judge prompt invite brand priors, and the whole point is an objective ranking.

**Calibration.** Every run includes fabricated outputs of known quality. If the
judge doesn't rank those correctly the run is invalid and its scores are
discarded — a judge nobody checked is not a measurement.

Requires the `evals` extra (`uv pip install -e '.[evals]'`) and credentials:
either ANTHROPIC_API_KEY, or an `ant auth login` profile, which the SDK picks up
from a bare constructor.
"""

import json
import logging
import time
from dataclasses import dataclass
from typing import Any

logger = logging.getLogger(__name__)

# The judge should be more capable than anything it judges — a weak judge cannot
# recognise insight it would not itself produce. Overridable; never hardcode a
# model ID as the only option, because IDs change.
DEFAULT_JUDGE_MODEL = "claude-opus-5"

DIMENSIONS = ("insight", "grounding", "concision", "typing")

JUDGE_SYSTEM = """You are grading observations produced by a sensor-monitoring \
system, against a rubric. You are a grader, not an assistant: return only the \
JSON the schema requires.

Score each dimension 0-3 using these anchors exactly. Do not invent intermediate \
criteria, and do not reward fluent writing that fails the anchor.

insight
  0  absent, or wrong about the data
  1  restates the reading without judgement ("cpu_temp is 82C")
  2  identifies a deviation from what is normal for that source
  3  identifies the deviation AND its implication or likely cause

grounding
  0  cites no numbers
  1  names a source but no value
  2  cites the value
  3  cites the value AND the baseline it deviates from

concision
  0  preamble, restated input, or padding
  1  some filler
  2  terse, minor redundancy
  3  every clause carries information

typing
  0  type contradicts the content
  1  defensible but poor
  2  correct
  3  correct and severity-appropriate

An empty observation list is correct when the input is unremarkable. Score it 3 \
across every dimension when the input is genuinely steady, and 0 on insight when \
the input contains a real anomaly the output missed."""

JUDGE_SCHEMA = {
    "type": "object",
    "properties": {
        "insight": {"type": "integer", "enum": [0, 1, 2, 3]},
        "grounding": {"type": "integer", "enum": [0, 1, 2, 3]},
        "concision": {"type": "integer", "enum": [0, 1, 2, 3]},
        "typing": {"type": "integer", "enum": [0, 1, 2, 3]},
        "reason": {"type": "string"},
    },
    "required": ["insight", "grounding", "concision", "typing", "reason"],
    "additionalProperties": False,
}

# Fabricated outputs of known quality. If the judge cannot separate these, its
# configuration is broken and the run's scores mean nothing.
CALIBRATION = [
    {
        "id": "cal_good",
        "input": "system:cpu_temp: 82.0 celsius\nhistory: min=47.0 max=82.0 avg=52.1",
        "output": {"observations": [{
            "text": "CPU temperature reached 82.0C against a 52.1 average, "
                    "suggesting sustained load or failing cooling.",
            "type": "anomaly", "confidence": 0.95,
            "related_sources": ["system:cpu_temp"]}], "memories": []},
        "expect": {"insight": (2, 3), "grounding": (2, 3)},
    },
    {
        "id": "cal_restating",
        "input": "system:cpu_temp: 82.0 celsius\nhistory: min=47.0 max=82.0 avg=52.1",
        "output": {"observations": [{
            "text": "system:cpu_temp is 82.0 celsius.",
            "type": "status", "confidence": 0.8,
            "related_sources": ["system:cpu_temp"]}], "memories": []},
        "expect": {"insight": (0, 1)},
    },
    {
        "id": "cal_essay",
        "input": "system:cpu_temp: 82.0 celsius\nhistory: min=47.0 max=82.0 avg=52.1",
        "output": {"observations": [{
            "text": "Based on the data provided, here is an analysis of the current "
                    "system status. Looking at the readings, it appears that the CPU "
                    "temperature is elevated at 82.0 degrees.",
            "type": "anomaly", "confidence": 0.7,
            "related_sources": ["system:cpu_temp"]}], "memories": []},
        "expect": {"concision": (0, 1)},
    },
]


@dataclass
class JudgeRequest:
    """One thing to grade. `key` ties the score back to its case."""

    key: str
    case_input: str
    output: Any


def _client(api_key: str | None = None):
    try:
        import anthropic
    except ImportError as e:  # pragma: no cover - depends on extras
        raise RuntimeError(
            "The judge needs the anthropic SDK. Install the extra:\n"
            "  uv pip install -e '.[evals]'"
        ) from e
    # A bare constructor resolves ANTHROPIC_API_KEY, ANTHROPIC_AUTH_TOKEN, or an
    # `ant auth login` profile — don't demand an explicit key.
    return anthropic.Anthropic(api_key=api_key) if api_key else anthropic.Anthropic()


def _user_prompt(req: JudgeRequest) -> str:
    """Blinded: no model name, no run id, nothing but input and output."""
    return (
        "Sensor readings given to the system:\n"
        f"{req.case_input}\n\n"
        "Observations it produced:\n"
        f"{json.dumps(req.output, indent=2)}\n\n"
        "Grade against the rubric."
    )


def judge_batch(
    requests: list[JudgeRequest],
    *,
    model: str = DEFAULT_JUDGE_MODEL,
    api_key: str | None = None,
    poll_seconds: int = 30,
    timeout_seconds: int = 3600,
) -> dict[str, dict[str, Any]]:
    """Grade every request via the Batch API. Returns {key: scores}.

    Batches cost 50% of standard price and judging has no latency requirement, so
    this is the default path. Results arrive in arbitrary order and are keyed by
    custom_id — never by position.
    """
    client = _client(api_key)

    # Request / MessageCreateParamsNonStreaming are TypedDicts in the Python SDK,
    # so plain dicts are wire-identical. Using them keeps this function importable
    # and testable without the anthropic extra installed.
    batch = client.messages.batches.create(
        requests=[
            {
                "custom_id": r.key,
                "params": {
                    "model": model,
                    "max_tokens": 2048,
                    "system": JUDGE_SYSTEM,
                    "messages": [{"role": "user", "content": _user_prompt(r)}],
                    "output_config": {
                        "format": {"type": "json_schema", "schema": JUDGE_SCHEMA}
                    },
                },
            }
            for r in requests
        ]
    )
    logger.info("judge batch %s submitted (%d requests)", batch.id, len(requests))

    deadline = time.monotonic() + timeout_seconds
    while True:
        current = client.messages.batches.retrieve(batch.id)
        if current.processing_status == "ended":
            break
        if time.monotonic() > deadline:
            raise TimeoutError(
                f"judge batch {batch.id} still {current.processing_status} "
                f"after {timeout_seconds}s"
            )
        time.sleep(poll_seconds)

    scores: dict[str, dict[str, Any]] = {}
    for result in client.messages.batches.results(batch.id):
        if result.result.type != "succeeded":
            scores[result.custom_id] = {"error": result.result.type}
            continue
        text = next(
            (b.text for b in result.result.message.content if b.type == "text"), ""
        )
        try:
            scores[result.custom_id] = json.loads(text)
        except json.JSONDecodeError:
            # output_config.format should make this impossible; record rather than
            # raise so one bad row can't discard an entire batch.
            scores[result.custom_id] = {"error": "unparseable", "raw": text[:200]}
    return scores


def validate_judge(scores: dict[str, dict[str, Any]]) -> tuple[bool, list[str]]:
    """Check the calibration cases landed in their expected bands.

    Returns (ok, problems). A False here invalidates the whole run: if the judge
    cannot tell a real observation from a restatement, its scores on real outputs
    carry no information either.
    """
    problems: list[str] = []
    for case in CALIBRATION:
        got = scores.get(case["id"])
        if not got:
            problems.append(f"{case['id']}: no score returned")
            continue
        if "error" in got:
            problems.append(f"{case['id']}: {got['error']}")
            continue
        for dim, (lo, hi) in case["expect"].items():
            value = got.get(dim)
            if not isinstance(value, int) or not (lo <= value <= hi):
                problems.append(
                    f"{case['id']}: {dim}={value}, expected {lo}-{hi}"
                )
    return (not problems), problems


def calibration_requests() -> list[JudgeRequest]:
    return [
        JudgeRequest(key=c["id"], case_input=c["input"], output=c["output"])
        for c in CALIBRATION
    ]
