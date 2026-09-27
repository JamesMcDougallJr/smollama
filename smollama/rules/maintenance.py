"""Deterministic rule lifecycle maintenance, plus the structural pre-filter.

Most retirement never needs an LLM. Four classes are pure arithmetic and run every
cycle for free:

    fires in >X% of evaluations       -> mute   (it is describing normal)
    0 fires over a long enough life   -> retire (dead rule)
    source no longer reporting        -> park   (reversible)
    enough clean evaluations          -> promote from proposed

Spending model tokens on these recreates the cost this design removes. An LLM
should only ever adjudicate the ambiguous middle: rules that fire *sometimes* and
whose meaning has become questionable.

The pre-filter is the other half. "Don't create a rule if one already exists" is an
instruction a model may ignore — measured on this system, "at most 3 observations"
in prompt text was ignored while `maxItems: 3` in a schema was obeyed. So covered
signals are removed in code and the model never sees them. Don't ask; prevent.
"""

import logging
from dataclasses import dataclass
from datetime import datetime, timezone

logger = logging.getLogger(__name__)


@dataclass
class MaintenanceConfig:
    # Mute: a rule firing this often is describing normal, not anomaly.
    noisy_fire_rate: float = 0.5
    noisy_min_evaluations: int = 40
    # Retire: never fired, and has had a fair chance to.
    dead_min_evaluations: int = 50
    dead_min_age_days: float = 30.0
    # Promote: enough clean evaluations to trust without a human click.
    auto_promote_after: int = 20


@dataclass
class MaintenanceAction:
    rule_id: int
    action: str  # mute | retire | park | promote
    reason: str


def apply_maintenance(
    store,
    *,
    now: datetime | None = None,
    config: MaintenanceConfig | None = None,
    known_sources=None,
) -> list[MaintenanceAction]:
    """Apply every deterministic lifecycle rule. Returns what it did.

    `known_sources` is the staleness registry. Without it, parking is skipped
    entirely: absent a registry we cannot distinguish "the source is gone" from
    "we weren't told about it", and parking on that guess would silence live
    monitoring.
    """
    now = now or datetime.now(timezone.utc)
    config = config or MaintenanceConfig()
    actions: list[MaintenanceAction] = []

    # Promote proposed rules that have accumulated a *clean* record. The fire-rate
    # check matters: without it, a proposed rule that fires constantly gets
    # promoted and then muted in the same pass — two state changes for one
    # decision, which inflates the churn metric that is supposed to detect
    # instability. A noisy rule simply stays proposed, where it covers nothing and
    # can be rejected on review.
    for rule in store.rules_in_state("proposed"):
        if (
            rule.evaluations >= config.auto_promote_after
            and rule.fire_rate <= config.noisy_fire_rate
        ):
            reason = (
                f"auto-promoted after {rule.evaluations} evaluations "
                f"(fire rate {rule.fire_rate:.0%})"
            )
            store.promote(rule.id, reason, now=now)
            actions.append(MaintenanceAction(rule.id, "promote", reason))

    known = set(known_sources) if known_sources is not None else None

    for rule in store.active_rules():
        # Park first: a rule for a vanished source cannot meaningfully be judged
        # noisy or dead, and parking is the reversible option.
        if known is not None and rule.full_id not in known:
            reason = f"source {rule.full_id} no longer reporting"
            store.park(rule.id, reason, now=now)
            actions.append(MaintenanceAction(rule.id, "park", reason))
            continue

        if (
            rule.evaluations >= config.noisy_min_evaluations
            and rule.fire_rate > config.noisy_fire_rate
        ):
            reason = (
                f"fired in {rule.fire_rate:.0%} of {rule.evaluations} evaluations — "
                f"describing normal rather than anomaly"
            )
            store.mute(rule.id, reason, now=now)
            actions.append(MaintenanceAction(rule.id, "mute", reason))
            continue

        if (
            rule.fired_count == 0
            and rule.evaluations >= config.dead_min_evaluations
            and rule.age_days(now) >= config.dead_min_age_days
        ):
            reason = (
                f"never fired in {rule.evaluations} evaluations over "
                f"{rule.age_days(now):.0f} days"
            )
            store.retire(rule.id, reason, now=now)
            actions.append(MaintenanceAction(rule.id, "retire", reason))

    if actions:
        logger.info("rule maintenance: %s", ", ".join(
            f"{a.action} #{a.rule_id}" for a in actions))
    return actions


def uncovered_signals(signals, store):
    """Signals with no *active* rule for their identity.

    Only active rules cover. A proposed rule isn't monitoring anything yet, and a
    muted one has been judged to describe normal — in both cases the signal still
    deserves review, so neither may silently swallow it.
    """
    covered = {r.identity for r in store.active_rules()}
    return [
        s for s in signals
        if (s.source, s.detector, s.direction) not in covered
    ]


def churn_summary(actions) -> dict[str, int]:
    """Counts per action type — the meta-metric.

    High create/retire churn means thresholds are being badly fit or judgement is
    guessing: a rule system that can detect its own instability, for free.
    """
    out: dict[str, int] = {}
    for a in actions:
        out[a.action] = out.get(a.action, 0) + 1
    return out
