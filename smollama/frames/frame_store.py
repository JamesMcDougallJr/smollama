"""Master-side store for CLIP-embedded camera keyframes.

Own SQLite database (separate from memory.db, WAL mode so the dashboard
process can read while the agent writes) holding one row + one sqlite-vec
embedding + one on-disk JPEG thumbnail per keyframe relayed from edge camera
nodes. Text search encodes the query with the matching CLIP text encoder and
runs KNN; without the encoder (or sqlite-vec) it falls back to LIKE search
over detectNet labels so the page still works.

The embedding dimension is fixed by the first frame ingested (recorded in
frames_meta); frames from a different CLIP model are rejected rather than
silently polluting the index.
"""

import base64
import json
import logging
import shutil
import sqlite3
import struct
import subprocess
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from .text_encoder import ClipTextEncoder

logger = logging.getLogger(__name__)

SCHEMA = """
CREATE TABLE IF NOT EXISTS frames (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    node_id TEXT NOT NULL,
    timestamp TEXT NOT NULL,
    trigger TEXT,
    labels TEXT,
    model TEXT,
    thumb_path TEXT,
    kind TEXT NOT NULL DEFAULT 'keyframe',
    window_start TEXT,
    window_end TEXT,
    meta TEXT,
    activity_category TEXT,
    activity_score REAL,
    created_at TEXT DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_frames_timestamp ON frames(timestamp);
CREATE INDEX IF NOT EXISTS idx_frames_node ON frames(node_id, timestamp);

CREATE TABLE IF NOT EXISTS frames_meta (
    key TEXT PRIMARY KEY,
    value TEXT
);

CREATE TABLE IF NOT EXISTS activity_scores (
    frame_id INTEGER NOT NULL,
    category TEXT NOT NULL,
    score REAL NOT NULL,
    is_distractor INTEGER NOT NULL DEFAULT 0,
    prompts_hash TEXT NOT NULL,
    scored_at TEXT DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (frame_id, category, prompts_hash)
);

CREATE INDEX IF NOT EXISTS idx_activity_frame ON activity_scores(frame_id);
CREATE INDEX IF NOT EXISTS idx_activity_cat ON activity_scores(category, score);
"""

# Columns added after the original release; existing DBs need an ALTER TABLE
# migration since CREATE TABLE IF NOT EXISTS won't add columns to a table
# that already exists.
_FRAME_COLUMN_MIGRATIONS = [
    ("kind", "TEXT NOT NULL DEFAULT 'keyframe'"),
    ("window_start", "TEXT"),
    ("window_end", "TEXT"),
    ("meta", "TEXT"),
    ("activity_category", "TEXT"),
    ("activity_score", "REAL"),
]

VECTOR_SCHEMA = """
CREATE VIRTUAL TABLE IF NOT EXISTS frames_vec USING vec0(
    frame_id INTEGER PRIMARY KEY,
    embedding FLOAT[{dimension}] distance_metric=cosine
);
"""


class FrameStore:
    """SQLite + sqlite-vec storage and search for camera keyframes."""

    def __init__(
        self,
        db_path: str | Path,
        thumbnail_dir: str | Path,
        text_encoder: ClipTextEncoder | None = None,
    ):
        self.db_path = Path(db_path).expanduser()
        self.thumbnail_dir = Path(thumbnail_dir).expanduser()
        self._text_encoder = text_encoder
        self._conn: sqlite3.Connection | None = None
        self._vec_available = False
        self._dimension: int | None = None

    # ==================== Lifecycle ====================

    def connect(self) -> None:
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self.thumbnail_dir.mkdir(parents=True, exist_ok=True)

        self._conn = sqlite3.connect(str(self.db_path), check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        # WAL + busy timeout: the agent writes while the dashboard process reads
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.execute("PRAGMA busy_timeout=5000")
        self._conn.executescript(SCHEMA)
        self._conn.commit()
        self._migrate()

        self._load_vec_extension()
        stored_dim = self._get_meta("dimension")
        if stored_dim:
            self._dimension = int(stored_dim)
            if self._vec_available:
                self._create_vec_table(self._dimension)

        logger.info(
            f"FrameStore connected to {self.db_path} "
            f"(vec={'yes' if self._vec_available else 'no'}, dim={self._dimension})"
        )

    def _migrate(self) -> None:
        """Add columns to a pre-existing frames table that predates them."""
        existing = {
            row["name"]
            for row in self._conn.execute("PRAGMA table_info(frames)").fetchall()
        }
        for column, ddl_type in _FRAME_COLUMN_MIGRATIONS:
            if column not in existing:
                self._conn.execute(f"ALTER TABLE frames ADD COLUMN {column} {ddl_type}")
        # Index depends on columns that may have just been added above, so it
        # can't live in the initial CREATE TABLE IF NOT EXISTS script.
        self._conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_frames_activity "
            "ON frames(kind, activity_category, activity_score)"
        )
        self._conn.commit()

    def _load_vec_extension(self) -> None:
        try:
            import sqlite_vec

            self._conn.enable_load_extension(True)
            sqlite_vec.load(self._conn)
            self._conn.enable_load_extension(False)
            self._vec_available = True
        except ImportError:
            logger.warning("sqlite-vec not available, frame search falls back to labels")
        except Exception as e:
            logger.warning(f"Failed to load sqlite-vec for frames: {e}")

    def _create_vec_table(self, dimension: int) -> None:
        self._conn.executescript(VECTOR_SCHEMA.format(dimension=dimension))
        self._conn.commit()

    def close(self) -> None:
        if self._conn:
            self._conn.close()
            self._conn = None

    def _ensure_connected(self) -> sqlite3.Connection:
        if self._conn is None:
            self.connect()
        return self._conn

    def _get_meta(self, key: str) -> str | None:
        row = self._conn.execute(
            "SELECT value FROM frames_meta WHERE key = ?", (key,)
        ).fetchone()
        return row["value"] if row else None

    def _set_meta(self, key: str, value: str) -> None:
        self._conn.execute(
            "INSERT INTO frames_meta (key, value) VALUES (?, ?) "
            "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
            (key, value),
        )

    # ==================== Ingest ====================

    def ingest_payload(self, node_id: str, data: dict) -> int | None:
        """Store one relayed MQTT frame payload; returns the frame id or None."""
        frame = data.get("frame", data)
        embedding = frame.get("embedding")
        ts = frame.get("ts")
        if not isinstance(embedding, list) or not ts:
            logger.warning(f"Ignoring malformed frame payload from {node_id}")
            return None

        thumb_jpeg = None
        if frame.get("jpeg_b64"):
            try:
                thumb_jpeg = base64.b64decode(frame["jpeg_b64"])
            except (ValueError, TypeError) as e:
                logger.warning(f"Bad thumbnail in frame from {node_id}: {e}")

        window = frame.get("window") or {}
        return self.add_frame(
            node_id=node_id,
            timestamp=ts,
            embedding=[float(x) for x in embedding],
            thumb_jpeg=thumb_jpeg,
            labels=frame.get("labels"),
            trigger=frame.get("trigger"),
            model=frame.get("model"),
            kind=frame.get("kind", "keyframe"),
            window_start=window.get("start"),
            window_end=window.get("end"),
            meta=window or None,
        )

    def add_frame(
        self,
        node_id: str,
        timestamp: str,
        embedding: list[float],
        thumb_jpeg: bytes | None = None,
        labels: list[str] | None = None,
        trigger: str | None = None,
        model: str | None = None,
        kind: str = "keyframe",
        window_start: str | None = None,
        window_end: str | None = None,
        meta: dict | None = None,
    ) -> int | None:
        """Store a frame (row + vector + thumbnail). Returns frame id or None."""
        conn = self._ensure_connected()

        # First frame fixes the index dimension; later mismatches are rejected
        if self._dimension is None:
            self._dimension = len(embedding)
            self._set_meta("dimension", str(self._dimension))
            if model:
                self._set_meta("model", model)
            if self._vec_available:
                self._create_vec_table(self._dimension)
        elif len(embedding) != self._dimension:
            logger.error(
                f"Rejecting frame from {node_id}: embedding dim {len(embedding)} "
                f"!= index dim {self._dimension} (model changed? reindex needed)"
            )
            return None

        thumb_rel = None
        if thumb_jpeg:
            ts_slug = timestamp.replace(":", "").replace("+", "p")
            thumb_rel = f"{node_id}_{ts_slug}.jpg"
            try:
                (self.thumbnail_dir / thumb_rel).write_bytes(thumb_jpeg)
            except OSError as e:
                logger.error(f"Could not write thumbnail: {e}")
                thumb_rel = None

        cursor = conn.execute(
            """
            INSERT INTO frames (
                node_id, timestamp, trigger, labels, model, thumb_path,
                kind, window_start, window_end, meta
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                node_id,
                timestamp,
                trigger,
                json.dumps(labels) if labels else None,
                model,
                thumb_rel,
                kind,
                window_start,
                window_end,
                json.dumps(meta) if meta else None,
            ),
        )
        frame_id = cursor.lastrowid

        if self._vec_available:
            packed = struct.pack(f"<{len(embedding)}f", *embedding)
            conn.execute(
                "INSERT INTO frames_vec (frame_id, embedding) VALUES (?, ?)",
                (frame_id, packed),
            )

        conn.commit()
        return frame_id

    # ==================== Search ====================

    def search_text(self, query: str, limit: int = 12) -> dict[str, Any]:
        """Search frames by natural-language query.

        Returns {"mode": "semantic"|"labels"|"recent", "results": [...]}, where
        mode reflects which path actually ran so callers can surface degraded
        search honestly.
        """
        if not query.strip():
            return {"mode": "recent", "results": self.recent(limit)}

        if self._vec_available and self._text_encoder is not None:
            try:
                query_embedding = self._text_encoder.embed(query)
                return {"mode": "semantic", "results": self._knn(query_embedding, limit)}
            except Exception as e:
                logger.warning(f"Semantic frame search failed, using labels: {e}")

        return {"mode": "labels", "results": self._label_search(query, limit)}

    _FRAME_COLUMNS = (
        "id, node_id, timestamp, trigger, labels, model, thumb_path, "
        "kind, window_start, window_end, meta, activity_category, activity_score"
    )
    _FRAME_COLUMNS_PREFIXED = ", ".join(f"f.{c.strip()}" for c in _FRAME_COLUMNS.split(","))

    def _knn(self, query_embedding: bytes, limit: int) -> list[dict[str, Any]]:
        conn = self._ensure_connected()
        cursor = conn.execute(
            f"""
            SELECT {self._FRAME_COLUMNS_PREFIXED}, v.distance
            FROM frames_vec v
            JOIN frames f ON f.id = v.frame_id
            WHERE v.embedding MATCH ? AND k = ?
            ORDER BY v.distance
            """,
            (query_embedding, limit),
        )
        return [self._row_to_dict(row) for row in cursor]

    def _label_search(self, query: str, limit: int) -> list[dict[str, Any]]:
        conn = self._ensure_connected()
        cursor = conn.execute(
            f"""
            SELECT {self._FRAME_COLUMNS}, NULL AS distance
            FROM frames
            WHERE labels LIKE ?
            ORDER BY timestamp DESC
            LIMIT ?
            """,
            (f"%{query}%", limit),
        )
        return [self._row_to_dict(row) for row in cursor]

    def recent(self, limit: int = 12) -> list[dict[str, Any]]:
        conn = self._ensure_connected()
        cursor = conn.execute(
            f"""
            SELECT {self._FRAME_COLUMNS}, NULL AS distance
            FROM frames ORDER BY timestamp DESC LIMIT ?
            """,
            (limit,),
        )
        return [self._row_to_dict(row) for row in cursor]

    def get_frame(self, frame_id: int) -> dict[str, Any] | None:
        conn = self._ensure_connected()
        row = conn.execute(
            f"""
            SELECT {self._FRAME_COLUMNS}, NULL AS distance
            FROM frames WHERE id = ?
            """,
            (frame_id,),
        ).fetchone()
        return self._row_to_dict(row) if row else None

    def recent_activity(
        self,
        hours: int = 24,
        category: str | None = None,
        min_score: float | None = None,
        include_unmatched: bool = False,
        limit: int = 50,
    ) -> list[dict[str, Any]]:
        """Windows ranked by activity_score for the review surface.

        By default only matched (non-abstained) windows are returned; pass
        include_unmatched=True to also see scored-but-abstained windows.
        """
        conn = self._ensure_connected()
        cutoff = (datetime.now(timezone.utc) - timedelta(hours=hours)).isoformat()
        clauses = ["kind = 'window'", "timestamp >= ?"]
        params: list[Any] = [cutoff]
        if not include_unmatched:
            clauses.append("activity_category IS NOT NULL")
        if category:
            clauses.append("activity_category = ?")
            params.append(category)
        if min_score is not None:
            clauses.append("activity_score >= ?")
            params.append(min_score)
        params.append(limit)
        cursor = conn.execute(
            f"""
            SELECT {self._FRAME_COLUMNS}, NULL AS distance
            FROM frames
            WHERE {" AND ".join(clauses)}
            ORDER BY activity_score IS NULL, activity_score DESC, timestamp DESC
            LIMIT ?
            """,
            params,
        )
        return [self._row_to_dict(row) for row in cursor]

    def get_activity_scores(self, frame_id: int) -> list[dict[str, Any]]:
        conn = self._ensure_connected()
        cursor = conn.execute(
            """
            SELECT category, score, is_distractor, prompts_hash, scored_at
            FROM activity_scores WHERE frame_id = ? ORDER BY score DESC
            """,
            (frame_id,),
        )
        return [dict(row) for row in cursor]

    def activity_categories(self) -> list[str]:
        conn = self._ensure_connected()
        cursor = conn.execute(
            """
            SELECT DISTINCT category FROM activity_scores
            WHERE is_distractor = 0 ORDER BY category
            """
        )
        return [row["category"] for row in cursor]

    def add_activity_scores(self, frame_id: int, result: dict[str, Any]) -> None:
        """Persist a full ActivityMatcher.score() result for a frame.

        Stores the whole per-category matrix in activity_scores (source of
        truth for later threshold tuning) and caches the argmax verdict on
        the frames row itself (source for the indexed review-list query).
        """
        conn = self._ensure_connected()
        prompts_hash = result.get("prompts_hash") or ""
        rows = [
            (frame_id, name, score, 0, prompts_hash)
            for name, score in (result.get("scores") or {}).items()
        ] + [
            (frame_id, name, score, 1, prompts_hash)
            for name, score in (result.get("distractors") or {}).items()
        ]
        conn.executemany(
            """
            INSERT INTO activity_scores
                (frame_id, category, score, is_distractor, prompts_hash)
            VALUES (?, ?, ?, ?, ?)
            ON CONFLICT(frame_id, category, prompts_hash)
                DO UPDATE SET score = excluded.score, scored_at = CURRENT_TIMESTAMP
            """,
            rows,
        )
        matched = result.get("matched")
        conn.execute(
            "UPDATE frames SET activity_category = ?, activity_score = ? WHERE id = ?",
            (
                result.get("top_category") if matched else None,
                result.get("top_score") if matched else None,
                frame_id,
            ),
        )
        conn.commit()

    def thumbnail_path(self, frame: dict[str, Any]) -> Path | None:
        if not frame.get("thumb_path"):
            return None
        path = self.thumbnail_dir / frame["thumb_path"]
        return path if path.exists() else None

    def _row_to_dict(self, row: sqlite3.Row) -> dict[str, Any]:
        distance = row["distance"]
        return {
            "id": row["id"],
            "node_id": row["node_id"],
            "timestamp": row["timestamp"],
            "trigger": row["trigger"],
            "labels": json.loads(row["labels"]) if row["labels"] else [],
            "model": row["model"],
            "thumb_path": row["thumb_path"],
            "similarity": (1 - distance) if distance is not None else None,
            "kind": row["kind"],
            "window_start": row["window_start"],
            "window_end": row["window_end"],
            "meta": json.loads(row["meta"]) if row["meta"] else None,
            "activity_category": row["activity_category"],
            "activity_score": row["activity_score"],
        }

    # ==================== Retention ====================

    def prune(
        self,
        retention_days: int,
        archive_dir: str | None = None,
        archive_command: str | None = None,
    ) -> dict[str, int]:
        """Delete frames older than the retention window, optionally archiving first.

        Archival stages thumbnails plus a metadata.jsonl into
        ``archive_dir/YYYY-MM-DD/`` and then runs ``archive_command`` (e.g. an
        rclone upload) if given. If staging or the command fails, nothing is
        deleted — losing local disk headroom is recoverable, losing frames is not.
        """
        conn = self._ensure_connected()
        cutoff = (datetime.now(timezone.utc) - timedelta(days=retention_days)).isoformat()

        rows = conn.execute(
            "SELECT id, node_id, timestamp, trigger, labels, model, thumb_path, "
            "kind, window_start, window_end, meta, activity_category, activity_score "
            "FROM frames WHERE timestamp < ?",
            (cutoff,),
        ).fetchall()
        if not rows:
            return {"pruned": 0, "archived": 0}

        archived = 0
        if archive_dir:
            try:
                archived = self._archive(rows, Path(archive_dir).expanduser())
                if archive_command:
                    subprocess.run(
                        archive_command, shell=True, check=True,
                        capture_output=True, timeout=600,
                    )
            except Exception as e:
                logger.error(f"Frame archive failed, skipping prune: {e}")
                return {"pruned": 0, "archived": 0}

        ids = [row["id"] for row in rows]
        placeholders = ",".join("?" * len(ids))
        if self._vec_available:
            conn.execute(f"DELETE FROM frames_vec WHERE frame_id IN ({placeholders})", ids)
        conn.execute(f"DELETE FROM activity_scores WHERE frame_id IN ({placeholders})", ids)
        conn.execute(f"DELETE FROM frames WHERE id IN ({placeholders})", ids)
        conn.commit()

        for row in rows:
            if row["thumb_path"]:
                try:
                    (self.thumbnail_dir / row["thumb_path"]).unlink()
                except FileNotFoundError:
                    pass
                except OSError as e:
                    logger.warning(f"Could not delete thumbnail {row['thumb_path']}: {e}")

        logger.info(f"Pruned {len(ids)} frames older than {retention_days}d "
                    f"({archived} archived)")
        return {"pruned": len(ids), "archived": archived}

    def _archive(self, rows: list[sqlite3.Row], archive_dir: Path) -> int:
        batch_dir = archive_dir / datetime.now(timezone.utc).strftime("%Y-%m-%d")
        batch_dir.mkdir(parents=True, exist_ok=True)

        with open(batch_dir / "metadata.jsonl", "a") as meta:
            for row in rows:
                meta.write(json.dumps({
                    "node_id": row["node_id"],
                    "timestamp": row["timestamp"],
                    "trigger": row["trigger"],
                    "labels": json.loads(row["labels"]) if row["labels"] else [],
                    "model": row["model"],
                    "thumb": row["thumb_path"],
                    "kind": row["kind"],
                    "window_start": row["window_start"],
                    "window_end": row["window_end"],
                    "meta": json.loads(row["meta"]) if row["meta"] else None,
                    "activity_category": row["activity_category"],
                    "activity_score": row["activity_score"],
                }) + "\n")
                if row["thumb_path"]:
                    src = self.thumbnail_dir / row["thumb_path"]
                    if src.exists():
                        shutil.copy2(src, batch_dir / row["thumb_path"])
        return len(rows)

    # ==================== Stats ====================

    def get_stats(self) -> dict[str, Any]:
        conn = self._ensure_connected()
        count = conn.execute("SELECT COUNT(*) AS c FROM frames").fetchone()["c"]
        newest = conn.execute("SELECT MAX(timestamp) AS t FROM frames").fetchone()["t"]
        oldest = conn.execute("SELECT MIN(timestamp) AS t FROM frames").fetchone()["t"]
        return {
            "frame_count": count,
            "newest": newest,
            "oldest": oldest,
            "dimension": self._dimension,
            "semantic_search": bool(
                self._vec_available
                and self._text_encoder is not None
                and self._text_encoder.available
            ),
        }
