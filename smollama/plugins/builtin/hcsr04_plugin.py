"""HC-SR04 ultrasonic distance sensor plugin."""

import logging
import warnings
from datetime import datetime
from typing import Any

from smollama.plugins.base import PluginMetadata, SensorPlugin
from smollama.readings.base import Reading

logger = logging.getLogger(__name__)


class HCSR04SensorPlugin(SensorPlugin):
    """HC-SR04 ultrasonic distance sensor plugin.

    Uses gpiozero's DistanceSensor with the LGPIOFactory backend,
    which is required on Raspberry Pi 5 where pigpio is unavailable.

    Wiring:
      HC-SR04 VCC  → Pi Pin 2  (5V)
      HC-SR04 GND  → Pi Pin 6  (GND)
      HC-SR04 Trig → Pi Pin 16 (BCM 23) — direct, no resistors
      HC-SR04 Echo → Pi Pin 18 (BCM 24) — via voltage divider (1kΩ + 2kΩ)
    """

    SOURCES = ["distance"]

    def __init__(self, config: dict[str, Any] | None = None) -> None:
        self._config: dict[str, Any] = config or {}
        self._sensor = None  # gpiozero.DistanceSensor, deferred import

    @property
    def metadata(self) -> PluginMetadata:
        return PluginMetadata(
            name="hcsr04",
            version="1.0.0",
            author="Smollama Team",
            description="HC-SR04 ultrasonic distance sensor plugin",
            dependencies=["gpiozero>=2.0", "lgpio>=0.2"],
            plugin_type="sensor",
        )

    @property
    def source_type(self) -> str:
        return "hcsr04"

    @property
    def config_schema(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "trig_pin":     {"type": "integer", "minimum": 1, "default": 23},
                "echo_pin":     {"type": "integer", "minimum": 1, "default": 24},
                "max_distance": {"type": "number",  "minimum": 0.1, "default": 4.0},
                "chip":         {"type": "integer", "minimum": 0, "default": 0},
            },
            "additionalProperties": False,
        }

    def check_dependencies(self) -> tuple[bool, str | None]:
        missing = []
        try:
            import gpiozero  # noqa: F401
        except ImportError:
            missing.append("gpiozero")
        try:
            import lgpio  # noqa: F401
        except ImportError:
            missing.append("lgpio")
        if missing:
            return (False, f"Missing packages: {', '.join(missing)}")
        return (True, None)

    def setup(self) -> None:
        from gpiozero import DistanceSensor
        from gpiozero.pins.lgpio import LGPIOFactory
        from gpiozero.exc import PWMSoftwareFallback

        trig_pin     = self._config.get("trig_pin",     23)
        echo_pin     = self._config.get("echo_pin",     24)
        max_distance = self._config.get("max_distance", 4.0)
        chip         = self._config.get("chip",         0)

        warnings.filterwarnings("ignore", category=PWMSoftwareFallback)

        factory = LGPIOFactory(chip=chip)
        self._sensor = DistanceSensor(
            echo=echo_pin,
            trigger=trig_pin,
            max_distance=max_distance,
            partial=True,
            pin_factory=factory,
        )
        logger.info(
            "HC-SR04 initialized: trig=BCM%d, echo=BCM%d, max_distance=%.1fm",
            trig_pin, echo_pin, max_distance,
        )

    def teardown(self) -> None:
        if self._sensor is not None:
            try:
                self._sensor.close()
            except Exception as e:
                logger.warning("Error closing HC-SR04 sensor: %s", e)
            finally:
                self._sensor = None

    @property
    def available_sources(self) -> list[str]:
        if self._sensor is None:
            return []
        return self.SOURCES

    async def read(self, source_id: str) -> Reading | None:
        if source_id != "distance" or self._sensor is None:
            return None

        raw = self._sensor.distance  # metres, or None if out of range (partial=True)
        value = round(raw * 100, 2) if raw is not None else None

        return Reading(
            source_type="hcsr04",
            source_id="distance",
            value=value,
            timestamp=datetime.now(),
            unit="cm",
            metadata={
                "trig_pin":     self._config.get("trig_pin",     23),
                "echo_pin":     self._config.get("echo_pin",     24),
                "max_distance": self._config.get("max_distance", 4.0),
                "out_of_range": raw is None,
            },
        )

    async def read_all(self) -> list[Reading]:
        reading = await self.read("distance")
        return [reading] if reading is not None else []
