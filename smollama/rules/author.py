"""Phase 4: LLM rule authoring, bounded and proposal-only.

The model's contribution is deciding *which of the infinite computable signals is
worth watching at all* — not the number. So `threshold_spec` is a closed enum of
`fit:` specs and `literal:` is deliberately absent: a model-chosen threshold is
derived from whatever it happened to see, with no access to the distribution.

Three guardrails, all structural rather than instructional, because a prompt
instruction is something a model may ignore — measured on this system, "at most 3
observations" in prompt text was ignored while `maxItems: 3` in a schema was
obeyed:

- Authored rules land `proposed`. A proposed rule covers no signals, so nothing
  becomes load-bearing without a human click or an accumulation of clean
  evaluations.
- Sources are validated against the signals the model was shown. It cannot write
  a rule for a source it never saw.
- The count is capped in code as well as in the schema.

Only *uncovered* signals ever reach the prompt — see maintenance.uncovered_signals.
"""

import json
import logging
from dataclasses import dataclass

from .fit import is_literal

logger = logging.getLogger(__name__)

DETECTORS = ("flatline", "stale", "level_shift", "trend", "envelope")
DIRECTIONS = ("above", "below", "flat", "absent")
# No literal: option. The model never picks a number.
THRESHOLD_SPECS = (
    "fit:p99_7d",
    "fit:p95_24h",
    "fit:baseline+4mad",
    "fit:baseline-4mad",
    "none",  # structural detectors (flatline, stale) have nothing to compare
)


@dataclass
class AuthorConfig:
    max_signals: int = 5          # strongest evidence only; keeps prefill small
    max_rules_per_cycle: int = 3
    num_predict: int = 512


AUTHOR_SCHEMA = {
    "type": "object",
    "properties": {
        "rules": {
            "type": "array",
            "maxItems": 3,
            "items": {
                "type": "object",
                "properties": {
                    "source": {"type": "string"},
                    "detector": {"type": "string", "enum": list(DETECTORS)},
                    "direction": {"type": "string", "enum": list(DIRECTIONS)},
                    "threshold_spec": {"type": "string", "enum": list(THRESHOLD_SPECS)},
                    "rationale": {"type": "string", "maxLength": 200},
                },
                "required": ["source", "detector", "direction", "threshold_spec",
                             "rationale"],
                "additionalProperties": False,
            },
        }
    },
    "required": ["rules"],
    "additionalProperties": False,
}

AUTHOR_SYSTEM = """You turn detected signals into monitoring rules. You are a \
configuration function, not an assistant: return only the JSON the schema requires.

A rule says "keep watching this source for this kind of change". You choose the \
source, the detector, the direction, and which fitted threshold applies — you never \
choose a number. Thresholds are resolved from the source's own history afterwards.

Use threshold_spec "none" for flatline and stale: those are structural checks (zero \
variance, or no data at all) with nothing to compare against a value.

Propose a rule only when the signal describes something worth watching \
*repeatedly*. A one-off event that needs no ongoing monitoring warrants no rule; an \
empty list is the right answer more often than not."""


def build_author_prompt(signals, *, config: AuthorConfig) -> str | None:
    """Render the strongest uncovered signals. None when there is nothing to do."""
    if not signals:
        return None
    top = sorted(signals, key=lambda s: s.score, reverse=True)[: config.max_signals]
    lines = [
        f"- source={s.source} detector={s.detector} direction={s.direction} "
        f"score={s.score}\n  {s.detail}"
        for s in top
    ]
    return (
        "Signals detected with no rule currently watching them:\n\n"
        + "\n".join(lines)
        + "\n\nPropose rules for the ones worth monitoring repeatedly."
    )


async def author_rules(signals, store, *, llm, config: AuthorConfig | None = None):
    """Ask the model to propose rules for uncovered signals. Returns created rules.

    `llm` is an async callable taking (prompt, system=, schema=, options=) and
    returning parsed JSON — injected so this is testable without a model.
    """
    config = config or AuthorConfig()
    prompt = build_author_prompt(signals, config=config)
    if prompt is None:
        return []  # nothing uncovered; do not spend a model call

    try:
        response = await llm(
            prompt,
            system=AUTHOR_SYSTEM,
            schema=AUTHOR_SCHEMA,
            options={"num_predict": config.num_predict},
        )
    except Exception as e:
        logger.warning("rule authoring call failed: %s", e)
        return []

    if isinstance(response, str):
        try:
            response = json.loads(response)
        except json.JSONDecodeError:
            logger.warning("rule authoring returned unparseable output")
            return []
    if not isinstance(response, dict):
        return []

    shown_sources = {s.source for s in signals}
    created = []

    for entry in (response.get("rules") or [])[: config.max_rules_per_cycle]:
        if not isinstance(entry, dict):
            continue
        source = entry.get("source")
        detector = entry.get("detector")
        direction = entry.get("direction")
        spec = entry.get("threshold_spec")
        rationale = (entry.get("rationale") or "").strip()

        # The model may only write rules for sources it was actually shown.
        if source not in shown_sources:
            logger.warning("rejecting authored rule for unseen source %r", source)
            continue
        if detector not in DETECTORS or direction not in DIRECTIONS:
            logger.warning("rejecting authored rule with bad detector/direction")
            continue
        if not rationale:
            logger.warning("rejecting authored rule with no rationale")
            continue
        # Belt and braces: the schema excludes literal:, but if it were bypassed a
        # model-chosen number must still not land in the store.
        if spec and is_literal(spec):
            logger.warning("rejecting authored rule with a literal threshold %r", spec)
            continue

        rule = store.propose(
            source, detector, direction,
            threshold_spec=None if spec in (None, "none") else spec,
            rationale=rationale,
        )
        created.append(rule)

    if created:
        logger.info("authored %d proposed rule(s)", len(created))
    return created
