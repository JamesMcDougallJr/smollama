"""Tier 3 action layer — Phase 6 of docs/observation-rules.md, dry-run only.

Nothing here executes. `propose_action` evaluates a request against a safety
envelope and returns a decision; running it is a separate step that is not yet
wired to any actuator. Defaults are inert: `enabled=False`, `dry_run=True`.
"""

from .envelope import (
    ActionDecision,
    ActionEnvelope,
    ActionRequest,
    Actuator,
    propose_action,
)
from .outcomes import OutcomeLog

__all__ = [
    "ActionDecision",
    "ActionEnvelope",
    "ActionRequest",
    "Actuator",
    "propose_action",
    "OutcomeLog",
]
