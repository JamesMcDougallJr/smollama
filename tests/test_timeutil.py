"""Tests for the producer→master timestamp contract (smollama/timeutil.py).

The contract: producers send epoch seconds (UTC); the master normalizes. These
tests pin the two properties the rest of the system leans on — that every input
form collapses to the same instant, and that the canonical storage string sorts
lexically in chronological order (SQLite compares those timestamps as TEXT).
"""

from datetime import datetime, timedelta, timezone

import pytest

from smollama.timeutil import normalize_ts, to_epoch, to_utc_iso, utc_now_epoch

# 2026-09-07T16:16:01.437644+00:00
EPOCH = 1788797761.437644


class TestNormalizeConverges:
    """Every accepted input form must resolve to the same instant."""

    @pytest.mark.parametrize(
        "value",
        [
            EPOCH,                                    # wire contract
            str(EPOCH),                               # epoch that arrived as text
            "2026-09-07T16:16:01.437644+00:00",       # legacy ISO, UTC
            "2026-09-07T09:16:01.437644-07:00",       # legacy ISO, PDT producer
            "2026-09-07T18:16:01.437644+02:00",       # legacy ISO, CEST producer
            "2026-09-07T16:16:01.437644Z",            # legacy ISO, Z suffix
            datetime.fromtimestamp(EPOCH, tz=timezone.utc),
        ],
    )
    def test_forms_agree(self, value):
        assert normalize_ts(value) == datetime.fromtimestamp(EPOCH, tz=timezone.utc)

    def test_result_is_always_utc_aware(self):
        for value in (EPOCH, "2026-09-07T09:16:01-07:00", datetime.now()):
            out = normalize_ts(value)
            assert out.tzinfo is not None
            assert out.utcoffset() == timedelta(0)

    def test_naive_datetime_read_as_local(self):
        """A naive datetime (what datetime.now() readings carry) means local time."""
        naive = datetime(2026, 9, 7, 10, 16, 1)
        assert normalize_ts(naive) == naive.astimezone(timezone.utc)


class TestFallbacks:
    """Bad input must not stall ingest — one corrupt record can't wedge a batch."""

    @pytest.mark.parametrize("value", [None, "", "garbage", "not-a-date", {}, []])
    def test_unparseable_falls_back_to_now(self, value):
        before = datetime.now(timezone.utc)
        out = normalize_ts(value)
        assert before <= out <= datetime.now(timezone.utc)

    def test_out_of_range_epoch_falls_back(self):
        assert normalize_ts(1e30).tzinfo is not None


class TestCanonicalStorageForm:
    def test_mixed_offsets_collapse_to_one_string(self):
        """The bug this contract removes: same instant, two producer offsets."""
        pdt = to_utc_iso("2026-09-07T09:16:01-07:00")
        utc = to_utc_iso("2026-09-07T16:16:01+00:00")
        assert pdt == utc == "2026-09-07T16:16:01+00:00"

    def test_lexical_order_matches_chronological(self):
        """SQLite compares these as TEXT, so the two orderings must agree."""
        stamps = [to_utc_iso(EPOCH + offset) for offset in (-3600, -1, 0, 0.5, 1, 3600)]
        assert stamps == sorted(stamps)

    def test_lexical_order_holds_across_producer_offsets(self):
        """Earlier instant from a +02:00 producer must still sort before a later
        instant from a -07:00 producer — the case that broke naive comparison."""
        earlier = to_utc_iso("2026-09-07T18:16:01+02:00")  # 16:16:01Z
        later = to_utc_iso("2026-09-07T09:20:00-07:00")    # 16:20:00Z
        assert earlier < later


class TestToEpoch:
    def test_round_trips_epoch(self):
        assert to_epoch(EPOCH) == EPOCH

    def test_aware_datetime(self):
        dt = datetime.fromtimestamp(EPOCH, tz=timezone.utc)
        assert to_epoch(dt) == pytest.approx(EPOCH)

    def test_naive_datetime_uses_local_offset(self):
        naive = datetime(2026, 9, 7, 10, 16, 1)
        assert to_epoch(naive) == naive.timestamp()

    def test_iso_string(self):
        assert to_epoch("2026-09-07T16:16:01.437644+00:00") == pytest.approx(EPOCH)

    def test_utc_now_epoch_is_a_plausible_epoch(self):
        now = utc_now_epoch()
        assert isinstance(now, float)
        assert abs(now - datetime.now(timezone.utc).timestamp()) < 5
