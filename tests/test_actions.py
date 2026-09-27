"""Tests for the action layer — Phase 6 of docs/observation-rules.md, dry_run only.

This is a different risk class from everything above it. Monitoring being wrong
produces noise; actuation being wrong produces a cold house or a short-cycled
compressor. Every constraint here is a safety requirement, enforced in code rather
than asked for in a prompt — prose constraints get ignored, structural ones get
obeyed.

The default is dry_run, and these tests assert that nothing executes unless the
envelope explicitly permits it *and* the kill switch is off.
"""

from datetime import datetime, timedelta, timezone

import pytest

from smollama.actions import (
    ActionEnvelope,
    ActionRequest,
    Actuator,
    OutcomeLog,
    propose_action,
)

NOW = datetime(2026, 9, 27, 12, 0, 0, tzinfo=timezone.utc)


@pytest.fixture
def envelope():
    return ActionEnvelope(
        enabled=True,
        dry_run=True,
        actuators={
            "thermostat.setpoint": Actuator(
                name="thermostat.setpoint", min_value=15.0, max_value=25.0,
                max_delta=1.0, deadband=0.3, cooldown_seconds=1800,
                max_actions_per_hour=2,
            )
        },
    )


@pytest.fixture
def log(tmp_path):
    lg = OutcomeLog(str(tmp_path / "outcomes.db"))
    lg.connect()
    yield lg
    lg.close()


def req(target="thermostat.setpoint", value=21.0, current=20.5, rule_id=1):
    return ActionRequest(
        target=target, value=value, current_value=current,
        rule_id=rule_id, reason="humidity high",
    )


class TestKillSwitch:
    def test_disabled_envelope_permits_nothing(self, envelope, log):
        envelope.enabled = False
        d = propose_action(req(), envelope, log, now=NOW)
        assert not d.permitted
        assert "disabled" in d.refusal.lower()

    def test_default_envelope_is_disabled(self):
        """Actuation must be opt-in; a fresh config must not be able to act."""
        assert ActionEnvelope().enabled is False

    def test_default_envelope_is_dry_run(self):
        assert ActionEnvelope().dry_run is True


class TestAllowList:
    def test_unlisted_actuator_is_refused(self, envelope, log):
        d = propose_action(req(target="furnace.override"), envelope, log, now=NOW)
        assert not d.permitted
        assert "not in the allow-list" in d.refusal

    def test_listed_actuator_is_permitted(self, envelope, log):
        assert propose_action(req(), envelope, log, now=NOW).permitted


class TestBounds:
    def test_value_above_max_is_refused(self, envelope, log):
        d = propose_action(req(value=30.0, current=24.9), envelope, log, now=NOW)
        assert not d.permitted
        assert "outside bounds" in d.refusal

    def test_value_below_min_is_refused(self, envelope, log):
        d = propose_action(req(value=5.0, current=15.5), envelope, log, now=NOW)
        assert not d.permitted

    def test_delta_larger_than_max_is_refused(self, envelope, log):
        """Bounds alone allow a legal-but-violent jump; max_delta is separate."""
        d = propose_action(req(value=24.0, current=16.0), envelope, log, now=NOW)
        assert not d.permitted
        assert "delta" in d.refusal.lower()

    def test_change_within_deadband_is_refused(self, envelope, log):
        """Hysteresis. Without it the loop hunts — this is why thermostats have one."""
        d = propose_action(req(value=20.6, current=20.5), envelope, log, now=NOW)
        assert not d.permitted
        assert "deadband" in d.refusal.lower()


class TestCooldownAndRateLimit:
    def test_second_action_inside_cooldown_is_refused(self, envelope, log):
        first = propose_action(req(), envelope, log, now=NOW)
        log.record(first, executed=False, now=NOW)
        d = propose_action(req(value=22.0, current=21.0), envelope, log,
                           now=NOW + timedelta(minutes=5))
        assert not d.permitted
        assert "cooldown" in d.refusal.lower()

    def test_action_after_cooldown_is_permitted(self, envelope, log):
        first = propose_action(req(), envelope, log, now=NOW)
        log.record(first, executed=False, now=NOW)
        d = propose_action(req(value=22.0, current=21.0), envelope, log,
                           now=NOW + timedelta(hours=2))
        assert d.permitted

    def test_hourly_rate_limit_is_enforced(self, envelope, log):
        envelope.actuators["thermostat.setpoint"].cooldown_seconds = 0
        for i in range(2):
            d = propose_action(req(value=21.0 + i * 0.5, current=20.5 + i * 0.5),
                               envelope, log, now=NOW + timedelta(minutes=i))
            assert d.permitted
            log.record(d, executed=False, now=NOW + timedelta(minutes=i))
        third = propose_action(req(value=22.5, current=21.5), envelope, log,
                               now=NOW + timedelta(minutes=3))
        assert not third.permitted
        assert "rate limit" in third.refusal.lower()

    def test_rate_limit_window_rolls_off(self, envelope, log):
        envelope.actuators["thermostat.setpoint"].cooldown_seconds = 0
        for i in range(2):
            d = propose_action(req(value=21.0 + i * 0.5, current=20.5 + i * 0.5),
                               envelope, log, now=NOW + timedelta(minutes=i))
            log.record(d, executed=False, now=NOW + timedelta(minutes=i))
        later = propose_action(req(value=22.5, current=21.5), envelope, log,
                               now=NOW + timedelta(hours=3))
        assert later.permitted


class TestDryRun:
    def test_dry_run_records_intent_without_executing(self, envelope, log):
        d = propose_action(req(), envelope, log, now=NOW)
        assert d.permitted
        assert d.dry_run is True
        row = log.record(d, executed=False, now=NOW)
        assert row["executed"] == 0
        assert row["dry_run"] == 1

    def test_dry_run_log_is_reviewable(self, envelope, log):
        for i in range(3):
            envelope.actuators["thermostat.setpoint"].cooldown_seconds = 0
            d = propose_action(req(value=21.0 + i * 0.5, current=20.5 + i * 0.5),
                               envelope, log, now=NOW + timedelta(minutes=i))
            log.record(d, executed=False, now=NOW + timedelta(minutes=i))
        intents = log.recent(limit=10)
        assert len(intents) == 3
        assert all(r["dry_run"] == 1 for r in intents)

    def test_refusals_are_logged_too(self, envelope, log):
        """A week of dry-run logs is the de-risking step; refusals are the
        interesting half of it."""
        d = propose_action(req(target="furnace.override"), envelope, log, now=NOW)
        log.record(d, executed=False, now=NOW)
        assert log.recent(limit=5)[0]["refusal"]


class TestOutcomeRecord:
    """Learning needs an explicit causal record, not 'observe later'."""

    def test_outcome_can_be_attached_after_the_fact(self, envelope, log):
        d = propose_action(req(), envelope, log, now=NOW)
        row = log.record(d, executed=False, now=NOW)
        log.record_outcome(row["id"], value_after=19.0,
                           observation_window_seconds=3600,
                           verdict="improved",
                           confounders="outside temp also fell")
        got = log.get(row["id"])
        assert got["value_after"] == 19.0
        assert got["verdict"] == "improved"
        assert "outside temp" in got["confounders"]

    def test_confounders_field_exists_because_attribution_is_weak(self, envelope, log):
        """You cannot A/B test a house. The schema must make that explicit rather
        than implying the correlation is causal."""
        d = propose_action(req(), envelope, log, now=NOW)
        row = log.record(d, executed=False, now=NOW)
        assert "confounders" in log.get(row["id"])

    def test_outcome_log_survives_reopen(self, tmp_path, envelope):
        path = str(tmp_path / "o.db")
        lg = OutcomeLog(path); lg.connect()
        d = propose_action(req(), envelope, lg, now=NOW)
        row = lg.record(d, executed=False, now=NOW)
        lg.close()
        lg2 = OutcomeLog(path); lg2.connect()
        assert lg2.get(row["id"])["target"] == "thermostat.setpoint"
        lg2.close()


class TestActionIsNotResolution:
    """Removing the rule after acting destroys the feedback mechanism: you can no
    longer detect that the action failed or that the condition returned."""

    def test_acting_suppresses_the_rule_for_a_cooldown_not_forever(self, envelope, log):
        d = propose_action(req(), envelope, log, now=NOW)
        row = log.record(d, executed=False, now=NOW)
        assert log.suppressed_until(rule_id=1, envelope=envelope, now=NOW) is not None
        # After the cooldown the rule evaluates again, so a returning condition is
        # caught rather than silently ignored.
        assert log.suppressed_until(
            rule_id=1, envelope=envelope, now=NOW + timedelta(hours=2)
        ) is None
        assert row["rule_id"] == 1

    def test_suppression_is_per_rule(self, envelope, log):
        d = propose_action(req(rule_id=1), envelope, log, now=NOW)
        log.record(d, executed=False, now=NOW)
        assert log.suppressed_until(rule_id=2, envelope=envelope, now=NOW) is None
