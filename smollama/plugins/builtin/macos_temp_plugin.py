"""macOS CPU temperature sensor plugin via osx-cpu-temp."""

import re
import shutil
import subprocess
import sys
from datetime import datetime
from typing import Any

from smollama.plugins.base import PluginMetadata, SensorPlugin
from smollama.readings.base import Reading


class MacOSTempPlugin(SensorPlugin):
    """Plugin providing CPU temperature on macOS via osx-cpu-temp (SMC).

    Reads from the System Management Controller using the osx-cpu-temp
    binary (brew install osx-cpu-temp). No sudo required.
    """

    SOURCES = ["cpu_temp"]

    @property
    def metadata(self) -> PluginMetadata:
        """Plugin metadata."""
        return PluginMetadata(
            name="macos_temp",
            version="1.0.0",
            author="Smollama Team",
            description="macOS CPU temperature via osx-cpu-temp (SMC)",
            dependencies=[],  # binary dep, not a Python package
            plugin_type="sensor",
        )

    @property
    def source_type(self) -> str:
        """Return 'macos_temp' as the source type."""
        return "macos_temp"

    @property
    def config_schema(self) -> dict[str, Any]:
        """JSON Schema for plugin configuration. No config needed."""
        return {
            "type": "object",
            "additionalProperties": False,
        }

    def check_dependencies(self) -> tuple[bool, str | None]:
        """Check platform and osx-cpu-temp binary availability."""
        if sys.platform != "darwin":
            return (False, "macOS only")
        if not shutil.which("osx-cpu-temp"):
            return (False, "osx-cpu-temp not found — install with: brew install osx-cpu-temp")
        return (True, None)

    def setup(self) -> None:
        """No setup required (subprocess-based, no persistent resources)."""
        pass

    def teardown(self) -> None:
        """No cleanup required."""
        pass

    @property
    def available_sources(self) -> list[str]:
        """List available sources."""
        return self.SOURCES

    async def read(self, source_id: str) -> Reading | None:
        """Read a single metric.

        Args:
            source_id: Metric name (only "cpu_temp" supported).

        Returns:
            Reading with temperature value, or None if unknown source.
        """
        if source_id != "cpu_temp":
            return None

        return Reading(
            source_type="macos_temp",
            source_id="cpu_temp",
            value=self._read_cpu_temp(),
            timestamp=datetime.now(),
            unit="celsius",
            metadata=None,
        )

    async def read_all(self) -> list[Reading]:
        """Read all available metrics.

        Returns:
            List of Reading objects.
        """
        return [
            Reading(
                source_type="macos_temp",
                source_id="cpu_temp",
                value=self._read_cpu_temp(),
                timestamp=datetime.now(),
                unit="celsius",
                metadata=None,
            )
        ]

    def _read_cpu_temp(self) -> float:
        """Read CPU temperature via osx-cpu-temp binary.

        Returns:
            Temperature in Celsius, or 0.0 if unavailable.
        """
        try:
            result = subprocess.run(
                ["osx-cpu-temp"],
                capture_output=True,
                text=True,
                timeout=2,
            )
            # output: "58.6°C\n"
            match = re.search(r"([\d.]+)", result.stdout)
            return float(match.group(1)) if match else 0.0
        except (subprocess.TimeoutExpired, FileNotFoundError, ValueError):
            return 0.0
