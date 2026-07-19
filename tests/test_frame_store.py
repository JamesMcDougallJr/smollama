"""Tests for the frame search store and edge spool."""

import base64
import json
import struct
from datetime import datetime, timedelta, timezone

import pytest

from smollama.frames import FrameSpool, FrameStore

# Tiny valid JPEG (1x1 px) for thumbnail round-trip tests
JPEG_1PX = base64.b64decode(
    "/9j/4AAQSkZJRgABAQEAAQABAAD/2wBDAAgGBgcGBQgHBwcJCQgKDBQNDAsLDBkSEw8UHRof"
    "Hh0aHBwgJC4nICIsIxwcKDcpLDAxNDQ0Hyc5PTgyPC4zNDL/wAALCAABAAEBAREA/8QAFAAB"
    "AAAAAAAAAAAAAAAAAAAAA//EABQQAQAAAAAAAAAAAAAAAAAAAAD/2gAIAQEAAD8AN//Z"
)

DIM = 8


def one_hot(index: int) -> list[float]:
    vec = [0.0] * DIM
    vec[index] = 1.0
    return vec


class FakeTextEncoder:
    """Maps query words to one-hot embeddings for deterministic KNN tests."""

    def __init__(self, mapping: dict[str, int]):
        self._mapping = mapping
        self.available = True

    def embed(self, text: str) -> bytes:
        vec = one_hot(self._mapping[text])
        return struct.pack(f"<{len(vec)}f", *vec)


@pytest.fixture
def store(tmp_path):
    store = FrameStore(
        db_path=tmp_path / "frames.db",
        thumbnail_dir=tmp_path / "thumbs",
        text_encoder=FakeTextEncoder({"cat": 0, "dog": 1}),
    )
    store.connect()
    yield store
    store.close()


def ts_days_ago(days: float) -> str:
    return (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()


class TestIngest:
    def test_add_and_recent(self, store):
        frame_id = store.add_frame(
            node_id="pipi",
            timestamp=ts_days_ago(0),
            embedding=one_hot(0),
            labels=["cat"],
            trigger="change",
            model="test-clip",
        )
        assert frame_id is not None

        recent = store.recent(5)
        assert len(recent) == 1
        assert recent[0]["node_id"] == "pipi"
        assert recent[0]["labels"] == ["cat"]
        assert recent[0]["trigger"] == "change"

    def test_ingest_payload_with_thumbnail(self, store):
        payload = {
            "node": "pipi",
            "frame": {
                "ts": ts_days_ago(0),
                "trigger": "change",
                "model": "test-clip",
                "dim": DIM,
                "embedding": one_hot(1),
                "labels": ["dog"],
                "jpeg_b64": base64.b64encode(JPEG_1PX).decode("ascii"),
            },
        }
        frame_id = store.ingest_payload("pipi", payload)
        assert frame_id is not None

        frame = store.get_frame(frame_id)
        thumb = store.thumbnail_path(frame)
        assert thumb is not None
        assert thumb.read_bytes() == JPEG_1PX

    def test_malformed_payload_rejected(self, store):
        assert store.ingest_payload("pipi", {"frame": {"ts": ts_days_ago(0)}}) is None
        assert store.ingest_payload("pipi", {"frame": {"embedding": one_hot(0)}}) is None

    def test_dimension_mismatch_rejected(self, store):
        assert store.add_frame("pipi", ts_days_ago(0), one_hot(0)) is not None
        # A different-dimension embedding (model swap) must be rejected
        assert store.add_frame("pipi", ts_days_ago(0), [1.0, 0.0]) is None
        assert len(store.recent(10)) == 1

    def test_dimension_persists_across_reconnect(self, tmp_path):
        store = FrameStore(tmp_path / "frames.db", tmp_path / "thumbs")
        store.connect()
        store.add_frame("pipi", ts_days_ago(0), one_hot(0))
        store.close()

        store2 = FrameStore(tmp_path / "frames.db", tmp_path / "thumbs")
        store2.connect()
        assert store2.add_frame("pipi", ts_days_ago(0), [1.0, 0.0]) is None
        assert store2.add_frame("pipi", ts_days_ago(0), one_hot(1)) is not None
        store2.close()


class TestSearch:
    def test_semantic_search_ranks_matching_frame_first(self, store):
        if not store._vec_available:
            pytest.skip("sqlite-vec not installed")
        store.add_frame("pipi", ts_days_ago(0.2), one_hot(0), labels=["cat"])
        store.add_frame("pipi", ts_days_ago(0.1), one_hot(1), labels=["dog"])

        result = store.search_text("cat", limit=2)
        assert result["mode"] == "semantic"
        assert result["results"][0]["labels"] == ["cat"]
        assert result["results"][0]["similarity"] == pytest.approx(1.0, abs=1e-5)
        assert result["results"][1]["similarity"] == pytest.approx(0.0, abs=1e-5)

    def test_empty_query_returns_recent(self, store):
        store.add_frame("pipi", ts_days_ago(0), one_hot(0), labels=["cat"])
        result = store.search_text("", limit=5)
        assert result["mode"] == "recent"
        assert len(result["results"]) == 1

    def test_label_fallback_without_encoder(self, tmp_path):
        store = FrameStore(tmp_path / "frames.db", tmp_path / "thumbs", text_encoder=None)
        store.connect()
        store.add_frame("pipi", ts_days_ago(0), one_hot(0), labels=["cat"])
        store.add_frame("pipi", ts_days_ago(0), one_hot(1), labels=["dog"])

        result = store.search_text("cat", limit=5)
        assert result["mode"] == "labels"
        assert len(result["results"]) == 1
        assert result["results"][0]["labels"] == ["cat"]
        store.close()


class TestRetention:
    def _add_old_and_new(self, store):
        old_id = store.add_frame(
            "pipi", ts_days_ago(40), one_hot(0), thumb_jpeg=JPEG_1PX, labels=["cat"]
        )
        new_id = store.add_frame(
            "pipi", ts_days_ago(1), one_hot(1), thumb_jpeg=JPEG_1PX, labels=["dog"]
        )
        return old_id, new_id

    def test_prune_deletes_row_vector_and_thumbnail(self, store):
        old_id, new_id = self._add_old_and_new(store)
        old_thumb = store.thumbnail_path(store.get_frame(old_id))
        assert old_thumb.exists()

        result = store.prune(retention_days=30)
        assert result["pruned"] == 1
        assert store.get_frame(old_id) is None
        assert store.get_frame(new_id) is not None
        assert not old_thumb.exists()
        if store._vec_available:
            count = store._conn.execute(
                "SELECT COUNT(*) AS c FROM frames_vec"
            ).fetchone()["c"]
            assert count == 1

    def test_prune_archives_before_deleting(self, store, tmp_path):
        self._add_old_and_new(store)
        archive = tmp_path / "archive"

        result = store.prune(retention_days=30, archive_dir=str(archive))
        assert result == {"pruned": 1, "archived": 1}

        batch_dirs = list(archive.iterdir())
        assert len(batch_dirs) == 1
        meta_lines = (batch_dirs[0] / "metadata.jsonl").read_text().splitlines()
        assert json.loads(meta_lines[0])["labels"] == ["cat"]
        jpgs = list(batch_dirs[0].glob("*.jpg"))
        assert len(jpgs) == 1

    def test_failed_archive_command_skips_prune(self, store, tmp_path):
        old_id, _ = self._add_old_and_new(store)

        result = store.prune(
            retention_days=30,
            archive_dir=str(tmp_path / "archive"),
            archive_command="false",  # always fails
        )
        assert result["pruned"] == 0
        assert store.get_frame(old_id) is not None


class TestFrameSpool:
    def write_entry(self, spool_dir, ms: int, with_jpg: bool = True, corrupt: bool = False):
        spool_dir.mkdir(parents=True, exist_ok=True)
        json_path = spool_dir / f"frame_{ms}.json"
        if corrupt:
            json_path.write_text("{not json")
        else:
            json_path.write_text(json.dumps({
                "ts": ts_days_ago(0),
                "trigger": "change",
                "model": "test-clip",
                "dim": DIM,
                "embedding": one_hot(0),
                "labels": ["cat"],
            }))
        if with_jpg:
            (spool_dir / f"frame_{ms}.jpg").write_bytes(JPEG_1PX)
        return json_path

    def test_pop_batch_reads_and_remove_deletes(self, tmp_path):
        spool_dir = tmp_path / "spool"
        self.write_entry(spool_dir, 1000)
        self.write_entry(spool_dir, 2000, with_jpg=False)

        spool = FrameSpool(str(spool_dir))
        entries = spool.pop_batch(10)
        assert len(entries) == 2
        assert base64.b64decode(entries[0].payload["jpeg_b64"]) == JPEG_1PX
        assert "jpeg_b64" not in entries[1].payload

        for entry in entries:
            spool.remove(entry)
        assert spool.pending_count() == 0
        assert list(spool_dir.iterdir()) == []

    def test_corrupt_entry_dropped(self, tmp_path):
        spool_dir = tmp_path / "spool"
        self.write_entry(spool_dir, 1000, corrupt=True)
        self.write_entry(spool_dir, 2000)

        spool = FrameSpool(str(spool_dir))
        entries = spool.pop_batch(10)
        assert len(entries) == 1
        assert not (spool_dir / "frame_1000.json").exists()

    def test_cap_drops_oldest(self, tmp_path):
        spool_dir = tmp_path / "spool"
        for ms in range(1000, 1005):
            self.write_entry(spool_dir, ms)

        spool = FrameSpool(str(spool_dir), max_entries=3)
        entries = spool.pop_batch(10)
        assert len(entries) == 3
        assert entries[0].json_path.name == "frame_1002.json"
        assert not (spool_dir / "frame_1000.json").exists()
        assert not (spool_dir / "frame_1000.jpg").exists()

    def test_batch_limit_leaves_remainder(self, tmp_path):
        spool_dir = tmp_path / "spool"
        for ms in range(1000, 1004):
            self.write_entry(spool_dir, ms)

        spool = FrameSpool(str(spool_dir))
        assert len(spool.pop_batch(2)) == 2
        assert spool.pending_count() == 4  # nothing deleted until remove()
