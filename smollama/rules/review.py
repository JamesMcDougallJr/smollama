"""Phase 5: LLM review of nominated rules.

Deterministic triage picks the candidates; the model never sees the whole table.
Reviewing 200 rules per cycle would recreate exactly the prompt bloat this design
removes — prefill was already 57s of a 60s cycle.

The model adjudicates only the ambiguous middle: rules that fire *sometimes* and
whose meaning has become questionable. Everything decidable by arithmetic — always
firing, never firing, source gone — is handled in maintenance.py for free.

Four-way decision, because keep/retire collapses the most common right answer:

    keep | retune | mute | retire

`retune` delegates straight back to the `fit:` resolver, so even when the model
concludes the threshold is wrong it still does not pick the replacement.

Retirement carries a higher evidence bar than the other three. The asymmetry: a bad
retained rule makes noise you notice, a wrongly retired one makes silence you don't
— which is how a dead camera writer went unreported for 19 days. A rule that has
ever fired is not retired on a model's word.
"""

import json
import logging
from dataclasses import dataclass

logger = logging.getLogger(__name__)

DECISIONS = ("keep", "retune", "mute", "retire")


@dataclass
class ReviewConfig:
    max_candidates: int = 5        # the review budget
    min_age_days: float = 14.0     # or create/review oscillation burns the budget
    min_evaluations: int = 30
    # The ambiguous band: outside it, maintenance.py already has an answer.
    ambiguous_low: float = 0.05
    ambiguous_high: float = 0.5
    num_predict: int = 512


@dataclass
class ReviewAction:
    rule_id: int
    action: str  # keep | retune | mute | retire | refused
    reason: str


REVIEW_SCHEMA = {
    "type": "object",
    "properties": {
        "decisions": {
            "type": "array",
            "maxItems": 5,
            "items": {
                "type": "object",
                "properties": {
                    "rule_id": {"type": "integer"},
                    "decision": {"type": "string", "enum": list(DECISIONS)},
                    "reason": {"type": "string", "maxLength": 200},
                },
                "required": ["rule_id", "decision", "reason"],
                "additionalProperties": False,
            },
        }
    },
    "required": ["decisions"],
    "additionalProperties": False,
}

REVIEW_SYSTEM = """You review monitoring rules that fire sometimes, and decide \
whether each still makes sense. You are a maintenance function, not an assistant: \
return only the JSON the schema requires.

  keep    the rule is sound and its threshold is about right
  retune  the rule is sound but its threshold has drifted; it will be re-fitted
          from fresh history (you do not choose the new number)
  mute    the rule is describing normal operation rather than a problem
  retire  the rule no longer refers to anything meaningful — the hardware is gone,
          or the thing it watched cannot happen any more

Default to keep. Retire only when the rule's subject has genuinely ceased to exist: \
a rule that still fires occasionally on a live source is doing its job, and silence \
from a wrongly retired rule is not noticed until something breaks.

Every decision needs a reason that names the evidence."""


def nominate_for_review(store, *, now=None, config: ReviewConfig | None = None):
    """Deterministic triage: which rules are worth a model's attention.

    Only active rules, only those old enough and evaluated enough to judge, and only
    those whose fire rate sits in the ambiguous band — outside it, maintenance.py
    already has a free answer. Ordered by evaluation count so the best-evidenced
    candidates get the budget.
    """
    config = config or ReviewConfig()
    candidates = []
    for rule in store.active_rules():
        if rule.age_days(now) < config.min_age_days:
            continue
        if rule.evaluations < config.min_evaluations:
            continue
        if not (config.ambiguous_low <= rule.fire_rate <= config.ambiguous_high):
            continue
        candidates.append(rule)

    candidates.sort(key=lambda r: r.evaluations, reverse=True)
    return candidates[: config.max_candidates]


def build_review_prompt(rules, *, feedback=None) -> str | None:
    """Compact and factual, currently-relevant state only.

    Full history both bloats the prompt and biases the model toward validating what
    it is shown rather than questioning it.
    """
    if not rules:
        return None
    blocks = []
    for r in rules:
        lines = [
            f"rule_id={r.id}",
            f"  watches: {r.full_id} / {r.detector} / {r.direction}",
            f"  age: {r.age_days():.0f} days",
            f"  fired {r.fired_count} of {r.evaluations} evaluations "
            f"({r.fire_rate:.0%})",
            f"  last fired: {r.last_fired or 'never'}",
            f"  threshold: {r.threshold_spec or 'n/a (structural)'}"
            + (f" = {r.threshold_value:g}" if r.threshold_value is not None else ""),
            f"  rationale when created: {r.rationale or '(none recorded)'}",
        ]
        # Human verdicts are the only ground truth available; include them when
        # present so the decision is evidential rather than speculative.
        if feedback and r.full_id in feedback:
            f = feedback[r.full_id]
            if f.get("total"):
                lines.append(
                    f"  human feedback on observations from this source: "
                    f"{f['keep']} kept / {f['dismiss']} dismissed"
                )
        blocks.append("\n".join(lines))
    return "Rules up for review:\n\n" + "\n\n".join(blocks)


async def apply_review(
    store,
    candidates,
    *,
    llm,
    config: ReviewConfig | None = None,
    now=None,
    history_for: dict | None = None,
    feedback: dict | None = None,
) -> list[ReviewAction]:
    """Ask the model to adjudicate the nominated rules, then apply what is allowed.

    `history_for` maps source -> historical values, used to re-fit on `retune`.
    Refusals are returned as actions rather than raised, so the reason log records
    what the model wanted and why it was not honoured.
    """
    config = config or ReviewConfig()
    if not candidates:
        return []  # nothing ambiguous; do not spend a model call

    prompt = build_review_prompt(candidates, feedback=feedback)
    try:
        response = await llm(
            prompt,
            system=REVIEW_SYSTEM,
            schema=REVIEW_SCHEMA,
            options={"num_predict": config.num_predict},
        )
    except Exception as e:
        logger.warning("rule review call failed: %s", e)
        return []

    if isinstance(response, str):
        try:
            response = json.loads(response)
        except json.JSONDecodeError:
            logger.warning("rule review returned unparseable output")
            return []
    if not isinstance(response, dict):
        return []

    # The model may only act on the candidates it was handed.
    allowed = {r.id: r for r in candidates}
    actions: list[ReviewAction] = []

    for entry in response.get("decisions") or []:
        if not isinstance(entry, dict):
            continue
        rule_id = entry.get("rule_id")
        decision = entry.get("decision")
        reason = (entry.get("reason") or "").strip()

        if rule_id not in allowed:
            logger.warning("ignoring review decision for un-nominated rule %r", rule_id)
            continue
        if decision not in DECISIONS:
            continue
        if not reason:
            # An unexplained state change cannot be audited, and the reason log is
            # the only window into whether the model's judgement is any good.
            actions.append(ReviewAction(rule_id, "refused", "no reason supplied"))
            continue

        # Re-read from the store rather than trusting the passed-in object. The
        # candidate list may have been built before further evaluations landed, and
        # the retire guard below depends on fired_count being current — a stale zero
        # would let a rule that has legitimately fired be retired.
        rule = store.get(rule_id) or allowed[rule_id]

        if decision == "keep":
            actions.append(ReviewAction(rule_id, "keep", reason))

        elif decision == "mute":
            store.mute(rule_id, f"review: {reason}", now=now)
            actions.append(ReviewAction(rule_id, "mute", reason))

        elif decision == "retune":
            history = (history_for or {}).get(rule.full_id)
            if not history:
                actions.append(
                    ReviewAction(rule_id, "refused", "retune with no history to fit")
                )
                continue
            value = store.refit(rule_id, history, now=now)
            actions.append(
                ReviewAction(rule_id, "retune", f"{reason} (refitted to {value})")
            )

        elif decision == "retire":
            if rule.fired_count > 0:
                # Higher evidence bar: a rule that has legitimately fired is not
                # retired on a model's word. Downgrade to a mute proposal instead of
                # silently creating a blind spot.
                actions.append(
                    ReviewAction(
                        rule_id, "refused",
                        f"retire refused — rule has fired {rule.fired_count} times; "
                        f"model reason was: {reason}",
                    )
                )
                continue
            store.retire(rule_id, f"review: {reason}", now=now)
            actions.append(ReviewAction(rule_id, "retire", reason))

    for a in actions:
        logger.info("rule review %s #%s: %s", a.action, a.rule_id, a.reason)
    return actions
