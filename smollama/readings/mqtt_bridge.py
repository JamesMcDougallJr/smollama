"""MQTT edge-node bridge: caches incoming edge readings as a ReadingProvider."""

import json
import os
from pathlib import Path

from ..timeutil import normalize_ts, to_utc_iso
from .base import Reading, ReadingProvider

_DEFAULT_CACHE_PATH = Path.home() / ".smollama" / "mqtt_bridge_cache.json"


class MQTTBridgeProvider(ReadingProvider):
    """ReadingProvider that caches readings received from MQTT edge-node payloads.

    Edge nodes publish JSON in the form:
        {"node": "edge-01", "timestamp": 1790431442.9, "readings": [
            {"source": "system:cpu_temp", "value": 45.3, "unit": "celsius",
             "ts": 1790431442.9}
        ]}

    Both ``timestamp`` and each reading's ``ts`` are epoch seconds (UTC) — see
    smollama/timeutil.py for why producers never send ISO strings. Legacy ISO
    values are still accepted on ingest so a stale edge node keeps working.

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
            # Producers send epoch seconds; normalize_ts also accepts the legacy
            # ISO form so in-flight messages from an un-upgraded edge node still
            # land correctly. Result is always tz-aware UTC.
            ts = normalize_ts(item.get("ts"))
            metadata = {"node": node}
            if isinstance(item.get("metadata"), dict):
                metadata.update(item["metadata"])
            self._cache[cache_key] = Reading(
                source_type=node,
                source_id=source,
                value=item.get("value"),
                timestamp=ts,
                unit=item.get("unit"),
                metadata=metadata,
            )
        self._persist()

    def _persist(self) -> None:
        """Merge in-memory cache into the on-disk cache and write atomically.

        Merging (rather than overwriting) keeps another process's entries for
        nodes/sources this process hasn't heard from intact, so a restart or a
        second agent process can't wipe out the rest of the cache.
        """
        data = {}
        if self._cache_path.exists():
            try:
                data = json.loads(self._cache_path.read_text())
            except json.JSONDecodeError:
                data = {}
        # Entries merged in from disk belong to nodes this process hasn't heard
        # from, so nothing above rewrites them. Normalize their timestamps here
        # or a pre-contract entry keeps its producer-local offset forever.
        for entry in data.values():
            if isinstance(entry, dict) and "timestamp" in entry:
                entry["timestamp"] = to_utc_iso(entry["timestamp"])
        for sid, r in self._cache.items():
            data[sid] = {
                "node": r.source_type,
                "source": r.source_id,
                "value": r.value,
                "timestamp": r.timestamp.isoformat(),
                "unit": r.unit,
                "metadata": r.metadata,
            }
        self._cache_path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self._cache_path.with_name(
            f"{self._cache_path.stem}.{os.getpid()}.tmp"
        )
        tmp.write_text(json.dumps(data))
        tmp.replace(self._cache_path)

    def _load_from_file(self) -> list[Reading]:
        """Read cached readings from disk (used by dashboard process)."""
        if not self._cache_path.exists():
            return []
        try:
            data = json.loads(self._cache_path.read_text())
            readings = []
            for item in data.values():
                ts = normalize_ts(item.get("timestamp"))
                metadata = item.get("metadata") or {"node": item["node"]}
                readings.append(Reading(
                    source_type=item["node"],
                    source_id=item["source"],
                    value=item.get("value"),
                    timestamp=ts,
                    unit=item.get("unit"),
                    metadata=metadata,
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
