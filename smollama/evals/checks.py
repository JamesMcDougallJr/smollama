"""Stage-1 evaluation: everything decidable without an LLM judge.

Two jobs. **Gates** reject output that doesn't parse, breaks the schema, or
fabricates — a failure scores the case 0 and no judge tokens are spent on it.
**Metrics** score against each case's known answer.

The split exists because a judge is expensive, non-deterministic, and unnecessary
for questions like "is this valid JSON" or "does this number appear in the input".
Only genuinely subjective dimensions (insight, concision) reach stage 2 — see
docs/model-evaluation.md.
"""

import re
from dataclasses import dataclass, field
from typing import Any

VALID_TYPES = {"pattern", "anomaly", "status"}

# A number not attached to an identifier. The negative lookbehind does the work:
# it keeps "hcsr04:distance" and "all-minilm:l6-v2" from reading as reported
# values, because the digits there follow a letter. Without it, every source name
# donates spurious numbers to the allowed set and the gate stops catching
# anything.
#
# There is deliberately no trailing lookahead. Requiring a non-letter after the
# number truncated unit-suffixed values — "32.4ms" matched as 32, "82.5C" as 82 —
# which then failed the hallucination gate as an invented number. Units are the
# normal way to write a reading, so that produced false failures on correct output.
_NUMBER_RE = re.compile(r"(?<![A-Za-z0-9_:.-])(-?\d+(?:\.\d+)?)")

# Tolerance for matching a quoted number against the input. Covers 82 vs 82.0
# and one-decimal rounding of a mean; deliberately tight enough that 82 -> 91
# still fails.
_NUMBER_TOL = 0.051


@dataclass
class GateResult:
    passed: bool
    failures: list[str] = field(default_factory=list)
    detail: dict[str, str] = field(default_factory=dict)


def extract_numbers(text: str) -> set[float]:
    """Numbers a reader would understand as values, excluding those inside names."""
    out: set[float] = set()
    for match in _NUMBER_RE.finditer(text or ""):
        try:
            out.add(float(match.group(1)))
        except ValueError:
            continue
    return out


def _case_numbers(case) -> set[float]:
    """Every number legitimately quotable: current values plus history stats."""
    nums: set[float] = set()
    for value in case.current.values():
        if isinstance(value, (int, float)) and not isinstance(value, bool):
            nums.add(float(value))
        elif isinstance(value, str):
            nums |= extract_numbers(value)
    for summary in case.history.values():
        nums |= extract_numbers(str(summary))
    return nums


def _is_allowed(number: float, allowed: set[float]) -> bool:
    return any(abs(number - a) <= _NUMBER_TOL for a in allowed)


def run_gates(response: Any, case, *, max_items: int, max_chars: int) -> GateResult:
    """Apply every deterministic gate. Any failure means the case scores 0."""
    failures: list[str] = []
    detail: dict[str, str] = {}

    if not isinstance(response, dict) or not isinstance(
        response.get("observations"), list
    ):
        return GateResult(False, ["schema"], {"schema": "not an object with observations[]"})

    observations = response["observations"]

    if len(observations) > max_items:
        failures.append("max_items")
        detail["max_items"] = f"{len(observations)} > {max_items}"

    allowed_sources = case.input_sources()
    allowed_numbers = _case_numbers(case)

    for i, o in enumerate(observations):
        if not isinstance(o, dict):
            failures.append("schema")
            detail["schema"] = f"observation {i} is not an object"
            continue

        text = str(o.get("text", ""))
        if not text:
            failures.append("schema")
            detail["schema"] = f"observation {i} has no text"

        if len(text) > max_chars:
            failures.append("max_chars")
            detail["max_chars"] = f"observation {i}: {len(text)} > {max_chars}"

        if o.get("type") not in VALID_TYPES:
            failures.append("enum")
            detail["enum"] = f"observation {i}: type={o.get('type')!r}"

        for src in o.get("related_sources") or []:
            if src not in allowed_sources:
                failures.append("sources_exist")
                detail["sources_exist"] = f"observation {i}: {src!r} not in input"

        invented = [
            n for n in extract_numbers(text) if not _is_allowed(n, allowed_numbers)
        ]
        if invented:
            failures.append("no_invented_numbers")
            detail["no_invented_numbers"] = (
                f"observation {i}: {sorted(invented)} not in input"
            )

    # Dedupe while keeping first-seen order, so a repeated gate reads once.
    ordered = list(dict.fromkeys(failures))
    return GateResult(not ordered, ordered, detail)


def score_metrics(response: Any, case) -> dict[str, float | None]:
    """Score against the case's known answer.

    `detection` only applies to cases that plant an anomaly; `restraint` only to
    cases where silence is correct. Each is None on the other kind, so an average
    can never quietly blend them.
    """
    observations = []
    if isinstance(response, dict) and isinstance(response.get("observations"), list):
        observations = [o for o in response["observations"] if isinstance(o, dict)]

    metrics: dict[str, float | None] = {
        "n_observations": float(len(observations)),
        "detection": None,
        "restraint": None,
    }

    if case.is_restraint_case:
        metrics["restraint"] = 1.0 if not observations else 0.0
        return metrics

    # A source counts as flagged whether it's in related_sources or named in the
    # text — a model shouldn't lose detection credit for a formatting choice.
    flagged: set[str] = set()
    for o in observations:
        flagged.update(o.get("related_sources") or [])
        text = str(o.get("text", ""))
        for src in case.input_sources():
            if src in text:
                flagged.add(src)

    wanted = set(case.expect_detect)
    metrics["detection"] = 1.0 if wanted & flagged else 0.0
    return metrics


def score_case(
    response: Any, case, *, max_items: int, max_chars: int
) -> dict[str, Any]:
    """Full stage-1 result for one case.

    A gate failure zeroes the case outright: there is no partial credit for
    well-written output that doesn't parse or that fabricates a number.
    """
    gates = run_gates(response, case, max_items=max_items, max_chars=max_chars)
    metrics = score_metrics(response, case)

    return {
        "case_id": case.id,
        "gates_passed": gates.passed,
        "failures": gates.failures,
        "failure_detail": gates.detail,
        "metrics": metrics,
        "judge": None,  # filled in by stage 2
        "score": 0.0 if not gates.passed else None,
    }
