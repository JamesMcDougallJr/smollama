"""System metrics reading provider using /sys and /proc files."""

from datetime import datetime

from .base import Reading, ReadingProvider


class SystemReadingProvider(ReadingProvider):
    """Provides system metrics via /sys and /proc (no psutil dependency)."""

    SOURCES = ["cpu_temp", "cpu_freq", "mem_percent", "mem_available_mb", "load_avg"]

    @property
    def source_type(self) -> str:
        """Return 'system' as the source type."""
        return "system"

    @property
    def available_sources(self) -> list[str]:
        """List available system metrics."""
        return self.SOURCES

    async def read(self, source_id: str) -> Reading | None:
        """Read a single system metric.

        Args:
            source_id: Metric name (e.g., "cpu_temp").

        Returns:
            Reading with metric value, or None if unknown metric.
        """
        readers = {
            "cpu_temp": (self._read_cpu_temp, "celsius"),
            "cpu_freq": (self._read_cpu_freq, "mhz"),
            "mem_percent": (self._read_mem_percent, "percent"),
            "mem_available_mb": (self._read_mem_available, "mb"),
            "load_avg": (self._read_load_avg, "load"),
        }

        if source_id not in readers:
            return None

        reader, unit = readers[source_id]
        return Reading(
            source_type="system",
            source_id=source_id,
            value=reader(),
            timestamp=datetime.now(),
            unit=unit,
            metadata=None,
        )

    async def read_all(self) -> list[Reading]:
        """Read all system metrics.

        Returns:
            List of Reading objects for all metrics.
        """
        readings = []
        now = datetime.now()

        readers = {
            "cpu_temp": (self._read_cpu_temp, "celsius"),
            "cpu_freq": (self._read_cpu_freq, "mhz"),
            "mem_percent": (self._read_mem_percent, "percent"),
            "mem_available_mb": (self._read_mem_available, "mb"),
            "load_avg": (self._read_load_avg, "load"),
        }

        for source_id, (reader, unit) in readers.items():
            readings.append(
                Reading(
                    source_type="system",
                    source_id=source_id,
                    value=reader(),
                    timestamp=now,
                    unit=unit,
                    metadata=None,
                )
            )

        return readings

    def _read_cpu_temp(self) -> float:
        """Read CPU temperature from /sys/class/thermal/thermal_zone0/temp.

        Returns:
            Temperature in Celsius, or 0.0 if unavailable.
        """
        try:
            with open("/sys/class/thermal/thermal_zone0/temp") as f:
                return int(f.read().strip()) / 1000.0
        except (FileNotFoundError, ValueError, PermissionError):
            return 0.0

    def _read_cpu_freq(self) -> float:
        """Read CPU frequency from scaling_cur_freq.

        Returns:
            Frequency in MHz, or 0.0 if unavailable.
        """
        try:
            with open(
                "/sys/devices/system/cpu/cpu0/cpufreq/scaling_cur_freq"
            ) as f:
                return int(f.read().strip()) / 1000.0  # kHz to MHz
        except (FileNotFoundError, ValueError, PermissionError):
            return 0.0

    def _read_mem_percent(self) -> float:
        """Parse /proc/meminfo for memory usage percentage.

        Returns:
            Memory usage as percentage, or 0.0 if unavailable.
        """
        try:
            meminfo = {}
            with open("/proc/meminfo") as f:
                for line in f:
                    parts = line.split()
                    if len(parts) >= 2:
                        meminfo[parts[0].rstrip(":")] = int(parts[1])

            total = meminfo.get("MemTotal", 1)
            available = meminfo.get("MemAvailable", 0)
            return round((1 - available / total) * 100, 1)
        except (FileNotFoundError, ValueError, KeyError, PermissionError):
            return 0.0

    def _read_mem_available(self) -> int:
        """Read available memory in MB from /proc/meminfo.

        Returns:
            Available memory in MB, or 0 if unavailable.
        """
        try:
            with open("/proc/meminfo") as f:
                for line in f:
                    if line.startswith("MemAvailable:"):
                        return int(line.split()[1]) // 1024  # kB to MB
        except (FileNotFoundError, ValueError, PermissionError):
            pass
        return 0

    def _read_load_avg(self) -> float:
        """Read 1-minute load average from /proc/loadavg.

        Returns:
            1-minute load average, or 0.0 if unavailable.
        """
        try:
            with open("/proc/loadavg") as f:
                return float(f.read().split()[0])
        except (FileNotFoundError, ValueError, PermissionError):
            return 0.0
