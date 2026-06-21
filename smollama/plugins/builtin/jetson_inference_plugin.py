"""Generic jetson-inference plugin for smollama (model-agnostic).

jetson-inference's compiled bindings only exist for the system Python (3.6),
while smollama runs under Python 3.10 — the two cannot share a process. So a
separate Python 3.6 writer (jetson-inference/python/examples/jetson_infer.py)
runs the camera + one or more inference primitives and writes the latest result
to a local JSON file. This plugin reads that file and relays each entry as a
reading, which the edge-publish loop then forwards over MQTT.

The writer/plugin boundary is a stable JSON "contract", so swapping or adding
models on the writer side requires no change here:

    {
      "ts": 1739999999.0,
      "model": "detect:ssd-mobilenet-v2,pose:resnet18-body",
      "fps": 8.4,
      "readings": [
        {"name": "activity", "value": "arms_raised", "unit": null, "metadata": {...}},
        {"name": "person_count", "value": 1, "unit": "count"},
        ...
      ]
    }

This plugin exposes one reading per ``readings[]`` entry, so ``available_sources``
is whatever the writer is currently producing. No jetson libraries are imported
here, and no images are transmitted. See docs/jetson-inference.md.
"""

import json
import os
import time
from datetime import datetime
from pathlib import Path
from typing import Any

from smollama.plugins.base import PluginMetadata, ReadPlugin
from smollama.readings.base import Reading

_DEFAULT_FILE_PATH = "~/.smollama/jetson_inference.json"
_DEFAULT_MAX_AGE_SECONDS = 30
_DEFAULT_SOURCE_TYPE = "jetson_inference"


class JetsonInferencePlugin(ReadPlugin):
    """Plugin that relays jetson-inference results produced by a separate
    Python 3.6 process (jetson_infer.py).

    Reads the latest result from a local JSON file and exposes one reading per
    entry in the ``readings[]`` contract list — so the set of sources adapts
    automatically to whatever primitives/models the writer runs. Stale results
    (older than ``max_age_seconds``) are dropped so the dashboard does not show
    frozen data after the camera stops. Images are never transmitted.
    """

    def __init__(self) -> None:
        super().__init__()
        self._path: Path | None = None
        self._max_age: float = _DEFAULT_MAX_AGE_SECONDS
        self._source_type: str = _DEFAULT_SOURCE_TYPE

    @property
    def metadata(self) -> PluginMetadata:
        return PluginMetadata(
            name="jetson_inference",
            version="1.0.0",
            author="James",
            description="Model-agnostic jetson-inference bridge (detectNet/poseNet/imageNet/... read via local file)",
            dependencies=[],
            plugin_type="read",
        )

    @property
    def source_type(self) -> str:
        return self._source_type

    @property
    def config_schema(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "file_path": {"type": "string", "default": _DEFAULT_FILE_PATH},
                "max_age_seconds": {"type": "number", "minimum": 0, "default": _DEFAULT_MAX_AGE_SECONDS},
                "source_type": {"type": "string", "default": _DEFAULT_SOURCE_TYPE},
            },
            "additionalProperties": False,
        }

    def check_dependencies(self) -> tuple[bool, str | None]:
        # No hard dependency: a missing or stale source file is handled in read_all().
        return (True, None)

    def setup(self) -> None:
        cfg = getattr(self, "_config", {}) or {}
        self._path = Path(os.path.expanduser(cfg.get("file_path", _DEFAULT_FILE_PATH)))
        self._max_age = cfg.get("max_age_seconds", _DEFAULT_MAX_AGE_SECONDS)
        self._source_type = cfg.get("source_type", _DEFAULT_SOURCE_TYPE)

    def teardown(self) -> None:
        self._path = None

    def _load_contract(self) -> dict | None:
        """Read and validate the writer's JSON contract; None if missing/stale/corrupt."""
        if self._path is None or not self._path.exists():
            return None
        try:
            data = json.loads(self._path.read_text())
        except (json.JSONDecodeError, OSError):
            return None
        ts_epoch = data.get("ts")
        try:
            ts_epoch = float(ts_epoch)
        except (ValueError, TypeError):
            return None
        if (time.time() - ts_epoch) > self._max_age:
            return None  # stale — the writer has likely stopped
        if not isinstance(data.get("readings"), list):
            return None
        data["ts"] = ts_epoch
        return data

    @property
    def available_sources(self) -> list[str]:
        data = self._load_contract()
        if not data:
            return []
        return [r.get("name") for r in data["readings"] if r.get("name")]

    async def read(self, source_id: str) -> Reading | None:
        for r in await self.read_all():
            if r.source_id == source_id:
                return r
        return None

    async def read_all(self) -> list[Reading]:
        data = self._load_contract()
        if not data:
            return []
        now = datetime.fromtimestamp(data["ts"])
        readings = []
        for entry in data["readings"]:
            name = entry.get("name")
            if not name:
                continue
            readings.append(Reading(
                source_type=self._source_type,
                source_id=name,
                value=entry.get("value"),
                timestamp=now,
                unit=entry.get("unit"),
                metadata=entry.get("metadata"),
            ))
        return readings
