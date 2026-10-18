"""Consolidating near-duplicate memories.

`add_memory` dedupes at write time against a 0.9 similarity bar. That cannot catch
anything already stored: a row only survived because it scored *below* 0.9 against
everything present at the time, so by construction no surviving pair is >= 0.9
(measured on the live store: max nearest-neighbour 0.900). The result was 2,466 rows
that paraphrase a few hundred facts.

So dedupe needs a second half — a sweep over what is already there, on a cadence.

The threshold was picked by reading real pairs at each similarity band rather than
from cluster counts:

    0.88-0.90  "reports zero objects/persons/activity" / "reports zero activity
               (objects, persons, pose)"              -> same fact
    0.85-0.88  "memory utilization around 80.2%" / "memory utilization (80.4%) is a
               frequently observed state"              -> same fact
    0.78-0.82  "Jetson shows low inference activity" / "Jetson temperatures around
               58.5-60.0C when inference was inactive" -> DIFFERENT facts

0.85 is therefore the default: everything above it reads as a genuine duplicate, and
below it distinct facts start merging.
"""

import pytest

from smollama.memory import LocalStore, MockEmbeddings


class FakeEmbeddings(MockEmbeddings):
    """Embeddings we control, so similarity is a property of the test, not of a model.

    Each text is mapped to an explicit unit vector; unknown texts get an orthogonal
    one so they never accidentally merge.
    """

    def __init__(self, vectors: dict[str, list[float]], dimension: int = 4):
        super().__init__(dimension=dimension)
        self._vectors = vectors
        self._fallback = 0

    def embed(self, text: str) -> bytes:
        vec = self._vectors.get(text)
        if vec is None:  # orthogonal-ish, deterministic
            self._fallback += 1
            vec = [0.0] * self._dimension
            vec[self._fallback % self._dimension] = 1.0
        return self.floats_to_bytes(list(vec))


def store_with(tmp_path, vectors, dimension=4):
    s = LocalStore(str(tmp_path / "m.db"), "n", FakeEmbeddings(vectors, dimension))
    s.connect()
    return s


def insert_raw(store, text, vector, times_confirmed=1, confidence=0.8):
    """Insert a memory bypassing add_memory's write-time dedupe.

    Not a shortcut — it reproduces how the real corpus formed. Every stored row got
    there *because* write-time dedupe let it through at 0.9, so the surviving
    population lives in the 0.85-0.90 band and could never be created by calling
    add_memory twice in a test.
    """
    conn = store._ensure_connected()
    cur = conn.execute(
        "INSERT INTO memories (text, confidence, times_confirmed, node_id) VALUES (?,?,?,?)",
        (text, confidence, times_confirmed, store.node_id),
    )
    mid = cur.lastrowid
    conn.execute(
        "INSERT INTO memories_vec (memory_id, embedding) VALUES (?, ?)",
        (mid, store._embeddings.floats_to_bytes(list(vector))),
    )
    conn.commit()
    return mid


# Similar enough for the sweep (>= 0.85) but below add_memory's 0.9 write-time bar,
# which is exactly where every surviving real memory sits.
NEAR_A = [1.0, 0.0, 0.0, 0.0]
NEAR_B = [0.87, 0.4931, 0.0, 0.0]        # cos(A, B) = 0.87
FAR = [0.0, 0.0, 1.0, 0.0]               # cos(A, FAR) = 0.0


@pytest.fixture
def store(tmp_path):
    s = store_with(tmp_path, {
        "memory is high": NEAR_A,
        "memory utilization is high": NEAR_B,
        "the jetson runs hot": FAR,
    })
    yield s
    s.close()


def rows(store):
    conn = store._ensure_connected()
    return {
        r["text"]: dict(r)
        for r in conn.execute(
            "select id, text, times_confirmed, confidence, active from memories"
        )
    }


def vec_ids(store):
    conn = store._ensure_connected()
    return {r[0] for r in conn.execute("select memory_id from memories_vec")}


class TestMerging:
    def test_near_duplicates_collapse(self, store):
        insert_raw(store, "memory is high", NEAR_A)
        insert_raw(store, "memory utilization is high", NEAR_B)
        report = store.consolidate_memories(threshold=0.85)
        assert report["merged"] == 1
        assert len(rows(store)) == 1

    def test_distinct_memories_are_left_alone(self, store):
        insert_raw(store, "memory is high", NEAR_A)
        insert_raw(store, "the jetson runs hot", FAR)
        report = store.consolidate_memories(threshold=0.85)
        assert report["merged"] == 0
        assert len(rows(store)) == 2

    def test_nothing_merges_below_the_threshold(self, store):
        insert_raw(store, "memory is high", NEAR_A)
        insert_raw(store, "memory utilization is high", NEAR_B)
        assert store.consolidate_memories(threshold=0.95)["merged"] == 0
        assert len(rows(store)) == 2

    def test_write_time_dedupe_still_handles_the_obvious_case(self, store):
        """The sweep is the second half of dedupe, not a replacement for the first."""
        store.add_memory("memory is high")
        store.add_memory("memory is high")
        assert len(rows(store)) == 1
        assert next(iter(rows(store).values()))["times_confirmed"] == 2

    def test_the_most_reinforced_row_survives(self, tmp_path):
        """The canonical phrasing is the one the system has confirmed most often."""
        s = store_with(tmp_path, {})
        weak = insert_raw(s, "weak", NEAR_A, times_confirmed=1)
        insert_raw(s, "strong", NEAR_B, times_confirmed=40)
        s.consolidate_memories(threshold=0.85)
        assert set(rows(s)) == {"strong"}
        assert weak not in vec_ids(s)
        s.close()

    def test_reinforcement_counts_are_summed_not_lost(self, tmp_path):
        """times_confirmed is the only evidence of how often a fact recurred."""
        s = store_with(tmp_path, {})
        insert_raw(s, "a", NEAR_A, times_confirmed=10)
        insert_raw(s, "b", NEAR_B, times_confirmed=4)
        s.consolidate_memories(threshold=0.85)
        assert next(iter(rows(s).values()))["times_confirmed"] == 14
        s.close()

    def test_the_survivor_keeps_the_highest_confidence(self, tmp_path):
        s = store_with(tmp_path, {})
        insert_raw(s, "a", NEAR_A, times_confirmed=9, confidence=0.4)
        insert_raw(s, "b", NEAR_B, times_confirmed=1, confidence=0.95)
        s.consolidate_memories(threshold=0.85)
        survivor = next(iter(rows(s).values()))
        assert survivor["text"] == "a"           # most reinforced wins the text
        assert survivor["confidence"] == 0.95    # ...but not at the cost of confidence
        s.close()


class TestNoOrphansOrDrift:
    def test_absorbed_rows_leave_no_vector_behind(self, store):
        """An orphaned vec row would keep matching searches for a deleted memory."""
        insert_raw(store, "memory is high", NEAR_A)
        insert_raw(store, "memory utilization is high", NEAR_B)
        store.consolidate_memories(threshold=0.85)
        assert vec_ids(store) == {r["id"] for r in rows(store).values()}

    def test_a_second_run_changes_nothing(self, store):
        """Idempotent, so running it every maintenance cycle is safe."""
        insert_raw(store, "memory is high", NEAR_A)
        insert_raw(store, "memory utilization is high", NEAR_B)
        store.consolidate_memories(threshold=0.85)
        before = rows(store)
        assert store.consolidate_memories(threshold=0.85)["merged"] == 0
        assert rows(store) == before

    def test_an_empty_store_is_a_no_op(self, store):
        assert store.consolidate_memories(threshold=0.85)["merged"] == 0

    def test_a_single_memory_is_a_no_op(self, store):
        insert_raw(store, "memory is high", NEAR_A)
        assert store.consolidate_memories(threshold=0.85)["merged"] == 0
        assert len(rows(store)) == 1

    def test_deactivated_memories_are_not_touched(self, tmp_path):
        """Deactivation is a human judgement; a sweep must not resurrect or eat it."""
        s = store_with(tmp_path, {})
        a = insert_raw(s, "a", NEAR_A)
        insert_raw(s, "b", NEAR_B)
        s.deactivate_memory(a)
        assert s.consolidate_memories(threshold=0.85)["merged"] == 0
        assert len(rows(s)) == 2
        s.close()


class TestBounds:
    def test_merges_are_capped_per_run(self, tmp_path):
        """A sweep runs inside the observation loop, so it must not stall a cycle."""
        s = store_with(tmp_path, {})
        for i in range(12):
            insert_raw(s, f"dup {i}", NEAR_A if i % 2 == 0 else NEAR_B)
        report = s.consolidate_memories(threshold=0.85, max_merges=4)
        assert report["merged"] == 4
        assert len(rows(s)) == 8
        s.close()

    def test_the_report_says_what_happened(self, store):
        insert_raw(store, "memory is high", NEAR_A)
        insert_raw(store, "memory utilization is high", NEAR_B)
        insert_raw(store, "the jetson runs hot", FAR)
        report = store.consolidate_memories(threshold=0.85)
        assert report["merged"] == 1
        assert report["groups"] == 1
        assert report["remaining"] == 2
        assert report["scanned"] == 3


class TestChaining:
    def test_merging_is_against_a_seed_not_transitive(self, tmp_path):
        """Single-linkage chaining would let A~B and B~C drag together an A and C
        that are not similar at all, which is how a sweep quietly destroys facts."""
        s = store_with(tmp_path, {})
        insert_raw(s, "a", [1.0, 0.0, 0.0, 0.0], times_confirmed=50)
        insert_raw(s, "b", [0.87, 0.4931, 0.0, 0.0])    # 0.87 to A
        insert_raw(s, "c", [0.5138, 0.8579, 0.0, 0.0])  # 0.87 to B, only 0.51 to A
        s.consolidate_memories(threshold=0.85)
        # A absorbs B. C is far from the seed, so chaining must not drag it in.
        assert set(rows(s)) == {"a", "c"}
        s.close()


# ── running on the maintenance cadence ──────────────────────────────────────

from datetime import datetime  # noqa: E402
from unittest.mock import AsyncMock, MagicMock, patch  # noqa: E402

from smollama.memory import ObservationLoop  # noqa: E402
from smollama.readings import Reading, ReadingManager  # noqa: E402


def quiet_loop(store, **kw):
    readings = MagicMock(spec=ReadingManager)
    readings.read_all = AsyncMock(return_value=[
        Reading("system", "cpu_temp", 48.0, datetime.now(), "celsius")])
    agent = MagicMock()
    agent.query = AsyncMock(return_value='{"observations": [], "memories": []}')
    return ObservationLoop(
        store=store, readings=readings, agent=agent, interval_minutes=15,
        lookback_minutes=60, use_detectors=True, **kw,
    )


def no_signals():
    return patch.multiple(
        "smollama.memory.observation_loop",
        load_series=MagicMock(return_value={}),
        known_sources=MagicMock(return_value=[]),
        detect_all=MagicMock(return_value=[]),
    )


class TestRunsOnCadence:
    @pytest.mark.asyncio
    async def test_it_does_not_run_every_cycle(self, store):
        """Consolidation reads every embedding, so it is maintenance work, not
        per-cycle work."""
        loop = quiet_loop(store, maintenance_every=5, consolidate_memories_every=5)
        with no_signals(), patch.object(
            store, "consolidate_memories", wraps=store.consolidate_memories
        ) as spy:
            for _ in range(4):
                await loop.run_once()
            assert spy.call_count == 0

    @pytest.mark.asyncio
    async def test_it_runs_on_the_configured_cadence(self, store):
        loop = quiet_loop(store, maintenance_every=3, consolidate_memories_every=3)
        with no_signals(), patch.object(
            store, "consolidate_memories", wraps=store.consolidate_memories
        ) as spy:
            for _ in range(6):
                await loop.run_once()
            assert spy.call_count == 2

    @pytest.mark.asyncio
    async def test_it_actually_merges_on_a_live_cycle(self, store):
        """End to end: duplicates in, one row out, without mocking the merge."""
        insert_raw(store, "memory is high", NEAR_A)
        insert_raw(store, "memory utilization is high", NEAR_B)
        loop = quiet_loop(store, maintenance_every=1, consolidate_memories_every=1)
        with no_signals():
            await loop.run_once()
        assert len(rows(store)) == 1

    @pytest.mark.asyncio
    async def test_zero_disables_it(self, store):
        insert_raw(store, "memory is high", NEAR_A)
        insert_raw(store, "memory utilization is high", NEAR_B)
        loop = quiet_loop(store, maintenance_every=1, consolidate_memories_every=0)
        with no_signals():
            await loop.run_once()
        assert len(rows(store)) == 2

    @pytest.mark.asyncio
    async def test_a_failure_does_not_break_the_cycle(self, store):
        """Readings must still be logged if consolidation blows up."""
        loop = quiet_loop(store, maintenance_every=1, consolidate_memories_every=1)
        with no_signals(), patch.object(
            store, "consolidate_memories", side_effect=RuntimeError("boom")
        ):
            await loop.run_once()
        assert store.get_stats()["readings_count"] > 0
