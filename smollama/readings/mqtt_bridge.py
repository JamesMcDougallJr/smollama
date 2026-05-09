"""MQTT edge-node bridge: caches incoming edge readings as a ReadingProvider."""

import json
from datetime import datetime
from pathlib import Path

from .base import Reading, ReadingProvider

_DEFAULT_CACHE_PATH = Path.home() / ".smollama" / "mqtt_bridge_cache.json"


class MQTTBridgeProvider(ReadingProvider):
    """ReadingProvider that caches readings received from MQTT edge-node payloads.

    Edge nodes publish JSON in the form:
        {"node": "edge-01", "timestamp": 1234, "readings": [
            {"source": "system:cpu_temp", "value": 45.3, "unit": "celsius", "ts": "..."}
        ]}

    Each Reading uses the node name as source_type and the original source as
    source_id, so full_id looks like "jeston-nano:system:cpu_temp" rather than
    "mqtt_edge:jeston-nano:system:cpu_temp". The provider's own source_type
    ("mqtt_edge") is only used for ReadingManager registration.

    The cache is persisted to disk so the dashboard process (separate from
    the agent) can read the latest values without an MQTT connection.
    """

    source_type = "mqtt_edge"

    def __init__(self, cache_path: Path = _DEFAULT_CACHE_PATH) -> None:
        self._cache: dict[str, Reading] = {}
        self._cache_path = cache_path

    def ingest_edge_payload(self, node: str, raw_readings: list[dict]) -> None:
        """Parse and cache an edge-node readings list, then persist to disk."""
        for item in raw_readings:
            source = item.get("source", "unknown")
            cache_key = f"{node}:{source}"
            ts_raw = item.get("ts")
            try:
                ts = datetime.fromisoformat(ts_raw) if ts_raw else datetime.now()
            except (ValueError, TypeError):
                ts = datetime.now()
            self._cache[cache_key] = Reading(
                source_type=node,
                source_id=source,
                value=item.get("value"),
                timestamp=ts,
                unit=item.get("unit"),
                metadata={"node": node},
            )
        self._persist()

    def _persist(self) -> None:
        """Write cache to disk atomically via a temp-file rename."""
        data = {
            sid: {
                "node": r.source_type,
                "source": r.source_id,
                "value": r.value,
                "timestamp": r.timestamp.isoformat(),
                "unit": r.unit,
            }
            for sid, r in self._cache.items()
        }
        self._cache_path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self._cache_path.with_suffix(".tmp")
        tmp.write_text(json.dumps(data))
        tmp.rename(self._cache_path)

    def _load_from_file(self) -> list[Reading]:
        """Read cached readings from disk (used by dashboard process)."""
        if not self._cache_path.exists():
            return []
        try:
            data = json.loads(self._cache_path.read_text())
            readings = []
            for item in data.values():
                try:
                    ts = datetime.fromisoformat(item["timestamp"])
                except (ValueError, KeyError):
                    ts = datetime.now()
                readings.append(Reading(
                    source_type=item["node"],
                    source_id=item["source"],
                    value=item.get("value"),
                    timestamp=ts,
                    unit=item.get("unit"),
                    metadata={"node": item["node"]},
                ))
            return readings
        except (json.JSONDecodeError, KeyError):
            return []

    @property
    def available_sources(self) -> list[str]:
        if self._cache:
            return list(self._cache.keys())
        return [r.source_id for r in self._load_from_file()]

    async def read(self, source_id: str) -> Reading | None:
        if self._cache:
            return self._cache.get(source_id)
        for r in self._load_from_file():
            if r.source_id == source_id:
                return r
        return None

    async def read_all(self) -> list[Reading]:
        if self._cache:
            return list(self._cache.values())
        return self._load_from_file()
