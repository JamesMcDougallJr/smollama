"""Tier 3 safety envelope — Phase 6, dry-run only.

A different risk class from the rest of this design. Monitoring being wrong makes
noise; actuation being wrong makes a cold house, a short-cycled compressor, or a
frozen pipe. Everything here is therefore enforced in code, never asked for in a
prompt: prose constraints get ignored, structural ones get obeyed.

Defaults are deliberately inert. `enabled=False` and `dry_run=True` mean a fresh
config cannot move anything, and nothing actuates until someone explicitly opts in
per actuator. That matters especially on this system, where `agent.py` already
auto-registers every WritePlugin's tools — the door to hardware is open, and this
is the lock.

Also load-bearing: **action is not resolution.** Acting on a rule suppresses it for
a cooldown rather than retiring it. Removing the rule because you acted destroys
the feedback mechanism that would tell you the action failed or the condition
returned.
"""

import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone

logger = logging.getLogger(__name__)


@dataclass
class Actuator:
    """Per-actuator bounds. Absolute limits and rate limits are separate concerns.

    `max_delta` exists because bounds alone permit a legal-but-violent jump: 16 -> 24
    is inside [15, 25] and still a terrible single move. `deadband` exists because a
    control loop without hysteresis hunts — it is why real thermostats have one.
    """

    name: str
    min_value: float
    max_value: float
    max_delta: float
    deadband: float = 0.0
    cooldown_seconds: float = 1800.0
    max_actions_per_hour: int = 2


@dataclass
class ActionEnvelope:
    # Two independent switches. `enabled` is the kill switch; `dry_run` is the
    # weaker "permitted but do not execute" mode. Both default to safe.
    enabled: bool = False
    dry_run: bool = True
    actuators: dict[str, Actuator] = field(default_factory=dict)


@dataclass
class ActionRequest:
    target: str
    value: float
    current_value: float
    rule_id: int | None = None
    reason: str = ""


@dataclass
class ActionDecision:
    request: ActionRequest
    permitted: bool
    dry_run: bool
    refusal: str | None = None
    actuator: Actuator | None = None


def propose_action(
    request: ActionRequest,
    envelope: ActionEnvelope,
    log,
    *,
    now: datetime | None = None,
) -> ActionDecision:
    """Evaluate a proposed action against the envelope. Never executes anything.

    Checks run cheapest-and-most-absolute first, so a refusal names the outermost
    reason rather than an incidental one.
    """
    now = now or datetime.now(timezone.utc)

    def refuse(msg: str) -> ActionDecision:
        return ActionDecision(request, False, envelope.dry_run, msg)

    if not envelope.enabled:
        return refuse("actuation is disabled (kill switch off)")

    actuator = envelope.actuators.get(request.target)
    if actuator is None:
        return refuse(f"{request.target!r} is not in the allow-list")

    if not (actuator.min_value <= request.value <= actuator.max_value):
        return refuse(
            f"value {request.value:g} outside bounds "
            f"[{actuator.min_value:g}, {actuator.max_value:g}]"
        )

    delta = abs(request.value - request.current_value)
    if delta > actuator.max_delta:
        return refuse(
            f"delta {delta:g} exceeds max_delta {actuator.max_delta:g}"
        )

    if delta < actuator.deadband:
        return refuse(
            f"change {delta:g} is inside the deadband {actuator.deadband:g}"
        )

    last = log.last_action_at(request.target, now=now)
    if last is not None and actuator.cooldown_seconds:
        elapsed = (now - last).total_seconds()
        if elapsed < actuator.cooldown_seconds:
            return refuse(
                f"cooldown: {elapsed:.0f}s since last action, "
                f"{actuator.cooldown_seconds:.0f}s required"
            )

    recent = log.count_since(request.target, seconds=3600, now=now)
    if recent >= actuator.max_actions_per_hour:
        return refuse(
            f"rate limit: {recent} actions in the last hour, "
            f"max {actuator.max_actions_per_hour}"
        )

    return ActionDecision(request, True, envelope.dry_run, None, actuator)
