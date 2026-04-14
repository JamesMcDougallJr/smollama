"""DHT11 temperature and humidity sensor plugin."""

import logging
from datetime import datetime
from typing import Any

from smollama.plugins.base import PluginMetadata, ReadPlugin
from smollama.readings.base import Reading

logger = logging.getLogger(__name__)


class DHT11SensorPlugin(ReadPlugin):
    """DHT11 temperature and humidity sensor plugin.

    Uses the adafruit-circuitpython-dht library with the lgpio backend,
    which is required on Raspberry Pi 5.

    The DHT11 outputs 3.3V-compatible signals — no voltage divider needed.
    Most breakout modules include the required 10kΩ pull-up resistor on the
    data line; bare sensors need one added externally.

    Minimum read interval is 1 second. This plugin caches the last successful
    reading and returns cached values when called faster than min_interval_sec.

    Wiring (default BCM 4 = Pi Pin 7):
      DHT11 VCC  → Pi Pin 1  (3.3V) or Pin 2 (5V)
      DHT11 Data → Pi Pin 7  (BCM 4) — direct, no resistors
      DHT11 GND  → Pi Pin 9  (GND)
    """

    SOURCES = ["temperature", "humidity"]

    def __init__(self, config: dict[str, Any] | None = None) -> None:
        self._config: dict[str, Any] = config or {}
        self._sensor = None  # adafruit_dht.DHT11, deferred import
        self._last_read_time: datetime | None = None
        self._cached_temp: float | None = None
        self._cached_humidity: float | None = None

    @property
    def metadata(self) -> PluginMetadata:
        return PluginMetadata(
            name="dht11",
            version="1.0.0",
            author="Smollama Team",
            description="DHT11 temperature and humidity sensor plugin",
            dependencies=["adafruit-circuitpython-dht>=3.7", "adafruit-blinka"],
            plugin_type="read",
        )

    @property
    def source_type(self) -> str:
        return "dht11"

    @property
    def config_schema(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "pin":              {"type": "integer", "minimum": 1, "default": 4},
                "min_interval_sec": {"type": "number",  "minimum": 1.0, "default": 2.0},
            },
            "additionalProperties": False,
        }

    def check_dependencies(self) -> tuple[bool, str | None]:
        missing = []
        try:
            import adafruit_dht  # noqa: F401
        except ImportError:
            missing.append("adafruit-circuitpython-dht")
        try:
            import board  # noqa: F401
        except ImportError:
            missing.append("adafruit-blinka")
        if missing:
            return (False, f"Missing packages: {', '.join(missing)}")
        return (True, None)

    def setup(self) -> None:
        import adafruit_dht
        import board

        pin = self._config.get("pin", 4)
        board_pin = getattr(board, f"D{pin}", None)
        if board_pin is None:
            raise ValueError(f"Invalid BCM pin {pin}: board.D{pin} not found")

        self._sensor = adafruit_dht.DHT11(board_pin, use_pulseio=False)
        logger.info("DHT11 initialized on BCM%d", pin)

    def teardown(self) -> None:
        if self._sensor is not None:
            try:
                self._sensor.exit()
            except Exception as e:
                logger.warning("Error closing DHT11 sensor: %s", e)
            finally:
                self._sensor = None

    @property
    def available_sources(self) -> list[str]:
        if self._sensor is None:
            return []
        return self.SOURCES

    def _refresh_cache(self) -> bool:
        """Read hardware and update cached temperature and humidity.

        Returns True if the read succeeded, False on transient errors.
        """
        try:
            temp = self._sensor.temperature
            humidity = self._sensor.humidity
            if temp is None or humidity is None:
                return False
            self._cached_temp = float(temp)
            self._cached_humidity = float(humidity)
            self._last_read_time = datetime.now()
            return True
        except RuntimeError as e:
            # Transient read errors are normal for DHT sensors
            logger.debug("DHT11 transient read error: %s", e)
            return False
        except Exception as e:
            logger.warning("DHT11 unexpected read error: %s", e)
            return False

    async def read(self, source_id: str) -> Reading | None:
        if source_id not in self.SOURCES or self._sensor is None:
            return None

        now = datetime.now()
        interval = self._config.get("min_interval_sec", 2.0)
        cache_valid = (
            self._last_read_time is not None
            and (now - self._last_read_time).total_seconds() < interval
        )

        if not cache_valid:
            self._refresh_cache()

        if source_id == "temperature":
            value = self._cached_temp
            unit = "celsius"
        else:
            value = self._cached_humidity
            unit = "%"

        if value is None:
            return None

        return Reading(
            source_type="dht11",
            source_id=source_id,
            value=round(value, 1),
            timestamp=self._last_read_time or now,
            unit=unit,
            metadata={
                "pin": self._config.get("pin", 4),
                "temperature": self._cached_temp,
                "humidity": self._cached_humidity,
            },
        )

    async def read_all(self) -> list[Reading]:
        # Refresh cache once, then return both readings from cache
        now = datetime.now()
        interval = self._config.get("min_interval_sec", 2.0)
        cache_valid = (
            self._last_read_time is not None
            and (now - self._last_read_time).total_seconds() < interval
        )
        if not cache_valid:
            self._refresh_cache()

        readings = []
        for source_id in self.SOURCES:
            reading = await self.read(source_id)
            if reading is not None:
                readings.append(reading)
        return readings
