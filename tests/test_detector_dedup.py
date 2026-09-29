"""Correlated-source dedup.

`mem_percent` and `mem_available_mb` are one event reported twice: when memory
fills, one rises as the other falls. On the live master both fire every cycle, on
both nodes — four signals for two events, eating four of the three narration slots.

Dedup is deliberately *not* applied inside `detect_all`. Rule evaluation must still
see every signal: a rule authored on `mem_available_mb` has to be able to record a
fire even on a cycle where `mem_percent` outscored it and took the narration slot.
"""

from datetime import datetime, timezone

from smollama.detectors import Signal, dedupe_correlated

NOW = datetime(2026, 9, 29, 12, 0, 0, tzinfo=timezone.utc)


def sig(source, detector="level_shift", direction="above", score=1.0):
    return Signal(source=source, detector=detector, score=score,
                  direction=direction, detail=f"{source} moved",
                  first_seen=NOW)


class TestGrouping:
    def test_the_memory_pair_collapses_to_one(self):
        out = dedupe_correlated([
            sig("system:mem_percent", score=3.9),
            sig("system:mem_available_mb", score=4.0, direction="below"),
        ])
        assert len(out) == 1

    def test_opposite_directions_still_collapse(self):
        """The load-bearing case: memory filling means percent up, available down.
        Keying dedup on direction would miss exactly the pair it exists for."""
        out = dedupe_correlated([
            sig("system:mem_percent", direction="above", score=4.0),
            sig("system:mem_available_mb", direction="below", score=3.0),
        ])
        assert len(out) == 1
        assert out[0].source == "system:mem_percent"

    def test_the_highest_scoring_member_survives(self):
        out = dedupe_correlated([
            sig("system:mem_percent", score=2.0),
            sig("system:mem_available_mb", score=5.0),
        ])
        assert out[0].source == "system:mem_available_mb"

    def test_the_suppressed_source_is_recorded_not_lost(self):
        """It is still part of the event, so it must reach `related_sources`."""
        out = dedupe_correlated([
            sig("system:mem_percent", score=5.0),
            sig("system:mem_available_mb", score=2.0),
        ])
        assert out[0].meta["correlated"] == ["system:mem_available_mb"]

    def test_different_nodes_are_different_events(self):
        out = dedupe_correlated([
            sig("system:mem_percent"),
            sig("jetson-nano:system:mem_percent"),
            sig("jetson-nano:system:mem_available_mb"),
        ])
        assert len(out) == 2
        assert {s.source for s in out} == {
            "system:mem_percent", "jetson-nano:system:mem_percent"
        }

    def test_different_detectors_are_different_events(self):
        """A flatline on one and a level shift on the other are two findings."""
        out = dedupe_correlated([
            sig("system:mem_percent", detector="flatline"),
            sig("system:mem_available_mb", detector="level_shift"),
        ])
        assert len(out) == 2

    def test_uncorrelated_sources_pass_through(self):
        signals = [sig("system:cpu_temp"), sig("hcsr04:distance")]
        assert len(dedupe_correlated(signals)) == 2

    def test_ungrouped_signal_carries_no_correlated_key(self):
        out = dedupe_correlated([sig("system:cpu_temp")])
        assert "correlated" not in out[0].meta

    def test_ordering_is_by_score(self):
        out = dedupe_correlated([
            sig("system:cpu_temp", score=1.0),
            sig("hcsr04:distance", score=9.0),
        ])
        assert [s.source for s in out] == ["hcsr04:distance", "system:cpu_temp"]

    def test_empty_input(self):
        assert dedupe_correlated([]) == []

    def test_does_not_mutate_its_input(self):
        original = sig("system:mem_percent", score=5.0)
        dedupe_correlated([original, sig("system:mem_available_mb", score=1.0)])
        assert "correlated" not in original.meta
