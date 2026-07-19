"""Edge-side frame spool: the writer→agent handoff for embedded frames.

The camera writer (e.g. the Py3.6 Jetson process) drops one ``frame_<ms>.json``
plus a sibling ``frame_<ms>.jpg`` per captured keyframe into the spool
directory, writing the .jpg first so the .json's presence marks a complete
entry. The edge agent drains the spool each publish cycle, relays entries over
MQTT, and deletes them only after a successful publish — so frames survive
master/broker outages. The spool is capped: oldest entries are dropped first
when the cap is exceeded, bounding SD-card use during long partitions.

The JSON entry contract (produced by scripts/jetson/clip_frames.py):

    {
      "ts": "2026-07-18T12:00:00-07:00",   # tz-aware ISO timestamp
      "trigger": "change" | "heartbeat",
      "model": "mobileclip_s0",
      "dim": 512,
      "embedding": [0.01, ...],             # L2-normalized floats
      "labels": ["person", "dog"]           # detectNet classes in frame
    }
"""

import base64
import json
import logging
from pathlib import Path

logger = logging.getLogger(__name__)


class SpoolEntry:
    """One complete spooled frame: its payload and the files backing it."""

    def __init__(self, json_path: Path, jpg_path: Path | None, payload: dict):
        self.json_path = json_path
        self.jpg_path = jpg_path
        self.payload = payload


class FrameSpool:
    """Reads and manages the frame spool directory on an edge node."""

    def __init__(self, spool_dir: str, max_entries: int = 500):
        self.spool_dir = Path(spool_dir).expanduser()
        self.max_entries = max_entries

    def _json_files(self) -> list[Path]:
        if not self.spool_dir.is_dir():
            return []
        return sorted(self.spool_dir.glob("frame_*.json"))

    def pop_batch(self, limit: int) -> list[SpoolEntry]:
        """Load up to ``limit`` oldest complete entries (does not delete them).

        Call remove() per entry after its publish succeeds. Corrupt entries are
        deleted on sight so they can't wedge the queue. Also enforces the spool
        cap by dropping the oldest entries beyond ``max_entries``.
        """
        files = self._json_files()

        # Enforce cap: drop oldest overflow before draining
        if len(files) > self.max_entries:
            overflow = files[: len(files) - self.max_entries]
            for path in overflow:
                logger.warning(f"Frame spool over cap, dropping {path.name}")
                self._delete_pair(path)
            files = files[len(overflow):]

        entries: list[SpoolEntry] = []
        for json_path in files:
            if len(entries) >= limit:
                break
            try:
                data = json.loads(json_path.read_text())
                if not isinstance(data.get("embedding"), list) or not data.get("ts"):
                    raise ValueError("missing embedding or ts")
            except (json.JSONDecodeError, OSError, ValueError) as e:
                logger.warning(f"Dropping corrupt spool entry {json_path.name}: {e}")
                self._delete_pair(json_path)
                continue

            jpg_path = json_path.with_suffix(".jpg")
            if jpg_path.exists():
                try:
                    data["jpeg_b64"] = base64.b64encode(jpg_path.read_bytes()).decode("ascii")
                except OSError as e:
                    logger.warning(f"Could not read thumbnail {jpg_path.name}: {e}")
                    jpg_path = None
            else:
                jpg_path = None

            entries.append(SpoolEntry(json_path, jpg_path, data))
        return entries

    def remove(self, entry: SpoolEntry) -> None:
        """Delete a published entry's files."""
        self._delete_pair(entry.json_path)

    @staticmethod
    def _delete_pair(json_path: Path) -> None:
        for path in (json_path, json_path.with_suffix(".jpg")):
            try:
                path.unlink()
            except FileNotFoundError:
                pass
            except OSError as e:
                logger.warning(f"Could not delete spool file {path}: {e}")

    def pending_count(self) -> int:
        return len(self._json_files())
