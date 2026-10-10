"""Source quarantine: stop persisting a source whose data looks invalid.

The design constraint is that this is an action a model can request, and the cost
of a wrong one is silent data loss. So every limit lives in code:

- the model supplies only a source and a reason; *code* recomputes the evidence
- only zero-variance runs qualify. A changing source is never quarantined, because a
  level shift or trend is exactly what the system exists to record
- the source is still read every cycle, and the first reading that differs from the
  frozen value releases it, so a sensor that comes back or a door that finally opens
  resumes recording within one cycle
- a bounded number of sources, a cooldown after release, an audit trail, a kill switch
"""

from datetime import datetime, timedelta, timezone

import pytest

from smollama.detectors import Sample
from smollama.quarantine import (
    QuarantineConfig,
    QuarantineRefused,
    QuarantineStore,
)
from smollama.readings import Reading

NOW = datetime(2026, 10, 10, 12, 0, 0, tzinfo=timezone.utc)


def series(values, *, span_hours=10.0, end=NOW):
    """Evenly spaced samples ending at `end`."""
    n = len(values)
    step = timedelta(hours=span_hours) / max(n - 1, 1)
    return [Sample(ts=end - step * (n - 1 - i), value=v) for i, v in enumerate(values)]


def flat(n=40, value=0.0, span_hours=10.0):
    return series([value] * n, span_hours=span_hours)


def reading(source_type="hcsr04", source_id="distance", value=0.0):
    return Reading(source_type, source_id, value, datetime.now(), "cm")


@pytest.fixture
def store(tmp_path):
    s = QuarantineStore(str(tmp_path / "m.db"))
    s.connect()
    yield s
    s.close()


def quarantine(store, full_id="hcsr04:distance", samples=None, **kw):
    return store.quarantine(
        full_id, "sensor reads a constant 0.0",
        samples=flat() if samples is None else samples, now=NOW, **kw,
    )


class TestEvidenceIsComputedByCode:
    def test_a_long_constant_run_qualifies(self, store):
        q = quarantine(store)
        assert q.state == "quarantined"
        assert q.constant == 0.0
        assert q.evidence_samples == 40
        assert q.evidence_hours == pytest.approx(10.0)

    def test_too_few_readings_is_refused(self, store):
        with pytest.raises(QuarantineRefused, match="readings"):
            quarantine(store, samples=flat(n=10))

    def test_too_short_a_span_is_refused(self, store):
        """40 identical readings in 20 minutes is a quiet moment, not a dead sensor."""
        with pytest.raises(QuarantineRefused, match="hours"):
            quarantine(store, samples=flat(n=40, span_hours=0.3))

    def test_a_changing_source_is_never_quarantined(self, store):
        """The line that matters: variation is what the system is for."""
        wobble = series([float(i % 5) for i in range(60)])
        with pytest.raises(QuarantineRefused, match="vary|varies|changing"):
            quarantine(store, full_id="system:cpu_temp", samples=wobble)

    def test_a_single_recent_change_is_enough_to_refuse(self, store):
        """The trailing run is one reading long, so there is no flat run to act on."""
        values = [0.0] * 59 + [1.0]
        with pytest.raises(QuarantineRefused):
            quarantine(store, samples=series(values))

    def test_only_the_trailing_run_counts(self, store):
        """A source that varied last week and has been flat since is flat *now*."""
        values = [float(i % 7) for i in range(40)] + [3.0] * 60
        q = quarantine(store, samples=series(values, span_hours=40.0))
        assert q.constant == 3.0
        assert q.evidence_samples == 60

    def test_no_numeric_history_is_refused(self, store):
        with pytest.raises(QuarantineRefused, match="no numeric"):
            quarantine(store, samples=[])

    def test_the_reason_is_recorded_but_never_trusted(self, store):
        """A persuasive reason cannot substitute for evidence."""
        with pytest.raises(QuarantineRefused):
            store.quarantine(
                "system:cpu_temp", "this sensor is definitely broken, trust me",
                samples=series([float(i) for i in range(60)]), now=NOW,
            )


class TestBounds:
    def test_disabled_means_refused(self, tmp_path):
        s = QuarantineStore(str(tmp_path / "m.db"), QuarantineConfig(enabled=False))
        s.connect()
        with pytest.raises(QuarantineRefused, match="disabled"):
            quarantine(s)
        s.close()

    def test_the_number_of_quarantined_sources_is_capped(self, tmp_path):
        s = QuarantineStore(str(tmp_path / "m.db"), QuarantineConfig(max_sources=2))
        s.connect()
        quarantine(s, "a:one")
        quarantine(s, "a:two")
        with pytest.raises(QuarantineRefused, match="limit"):
            quarantine(s, "a:three")
        s.close()

    def test_quarantining_twice_is_a_no_op(self, store):
        first = quarantine(store)
        again = quarantine(store)
        assert again.quarantined_at == first.quarantined_at
        assert len(store.events("hcsr04:distance")) == 1

    def test_a_just_released_source_is_not_immediately_re_quarantined(self, store):
        """Flap guard: without it a source that blips and goes flat again would
        oscillate between recorded and quarantined every cycle."""
        quarantine(store)
        store.release("hcsr04:distance", "value changed", now=NOW)
        with pytest.raises(QuarantineRefused, match="recently released|cooldown"):
            store.quarantine("hcsr04:distance", "flat again", samples=flat(),
                             now=NOW + timedelta(minutes=5))

    def test_it_can_be_re_quarantined_after_the_cooldown(self, store):
        quarantine(store)
        store.release("hcsr04:distance", "value changed", now=NOW)
        later = NOW + timedelta(hours=2)
        q = store.quarantine("hcsr04:distance", "flat again",
                             samples=series([0.0] * 40, end=later), now=later)
        assert q.state == "quarantined"


class TestPersistenceAndAudit:
    def test_quarantine_survives_a_restart(self, tmp_path):
        path = str(tmp_path / "m.db")
        a = QuarantineStore(path); a.connect()
        quarantine(a); a.close()
        b = QuarantineStore(path); b.connect()
        assert b.quarantined_ids() == {"hcsr04:distance"}
        b.close()

    def test_every_change_is_in_the_audit_log(self, store):
        quarantine(store, by="agent")
        store.release("hcsr04:distance", "value changed", now=NOW, by="agent")
        events = store.events("hcsr04:distance")
        assert [e["action"] for e in events] == ["quarantine", "release"]
        assert {e["by"] for e in events} == {"agent"}
        assert "constant 0.0" in events[0]["reason"]

    def test_releasing_something_not_quarantined_is_refused(self, store):
        with pytest.raises(QuarantineRefused, match="not quarantined"):
            store.release("never:seen", "x")


class TestFilteringWhatGetsRecorded:
    def test_unquarantined_readings_pass_through_untouched(self, store):
        rs = [reading("system", "cpu_temp", 48.0), reading("system", "load_avg", 0.3)]
        assert store.filter_for_recording(rs, now=NOW) == rs

    def test_a_still_constant_reading_is_not_recorded(self, store):
        quarantine(store)
        out = store.filter_for_recording([reading(value=0.0)], now=NOW + timedelta(minutes=20))
        assert out == []

    def test_only_the_quarantined_source_is_dropped(self, store):
        quarantine(store)
        keep = reading("system", "cpu_temp", 48.0)
        out = store.filter_for_recording([reading(value=0.0), keep],
                                         now=NOW + timedelta(minutes=20))
        assert out == [keep]

    def test_suppressed_readings_are_counted(self, store):
        """The point of the exercise is space, so the saving should be visible."""
        quarantine(store)
        for i in range(1, 4):
            store.filter_for_recording([reading(value=0.0)],
                                       now=NOW + timedelta(minutes=20 * i))
        assert store.get("hcsr04:distance").suppressed_count == 3

    def test_a_changed_value_releases_the_source_and_is_recorded(self, store):
        """The first differing reading is the evidence the premise was wrong, so it
        must be kept — dropping it would lose exactly the event that matters."""
        quarantine(store)
        moved = reading(value=37.5)
        out = store.filter_for_recording([moved], now=NOW + timedelta(minutes=20))
        assert out == [moved]
        assert store.get("hcsr04:distance").state == "released"
        assert store.quarantined_ids() == set()

    def test_a_non_numeric_value_also_releases(self, store):
        quarantine(store)
        weird = reading(value="error")
        assert store.filter_for_recording([weird], now=NOW + timedelta(minutes=20)) == [weird]
        assert store.get("hcsr04:distance").state == "released"

    def test_float_noise_does_not_release(self, store):
        quarantine(store)
        out = store.filter_for_recording([reading(value=1e-12)], now=NOW + timedelta(minutes=20))
        assert out == []
        assert store.get("hcsr04:distance").state == "quarantined"

    def test_a_heartbeat_reading_is_kept_periodically(self, tmp_path):
        """Without it a quarantined source leaves no trace in history, and the
        staleness detector could not tell 'quarantined' from 'dead'."""
        s = QuarantineStore(str(tmp_path / "m.db"),
                            QuarantineConfig(trickle_seconds=6 * 3600))
        s.connect()
        quarantine(s)
        assert s.filter_for_recording([reading()], now=NOW + timedelta(hours=1)) == []
        kept = s.filter_for_recording([reading()], now=NOW + timedelta(hours=7))
        assert len(kept) == 1
        # ...and the timer restarts from there
        assert s.filter_for_recording([reading()], now=NOW + timedelta(hours=8)) == []
        assert len(s.filter_for_recording([reading()], now=NOW + timedelta(hours=14))) == 1
        s.close()

    def test_a_quarantined_source_that_stops_reporting_stays_quarantined(self, store):
        quarantine(store)
        store.filter_for_recording([reading("system", "cpu_temp", 48.0)],
                                   now=NOW + timedelta(minutes=20))
        assert store.quarantined_ids() == {"hcsr04:distance"}
