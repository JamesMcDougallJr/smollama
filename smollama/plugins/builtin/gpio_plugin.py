"""GPIO sensor plugin for Raspberry Pi."""

from datetime import datetime
from typing import Any

from smollama.config import GPIOConfig
from smollama.gpio_reader import GPIOReader
from smollama.plugins.base import PluginMetadata, SensorPlugin
from smollama.readings.base import Reading


class GPIOSensorPlugin(SensorPlugin):
    """Plugin providing GPIO pin readings on Raspberry Pi.

    This plugin wraps the existing GPIOReader to provide GPIO sensor
    readings through the plugin system. It gracefully handles missing
    GPIO dependencies by falling back to mock mode.
    """

    def __init__(self, config: GPIOConfig | None = None) -> None:
        """Initialize the GPIO sensor plugin.

        Args:
            config: GPIO configuration with pin definitions.
        """
        self._config = config
        self._gpio_reader: GPIOReader | None = None

    @property
    def metadata(self) -> PluginMetadata:
        """Plugin metadata."""
        return PluginMetadata(
            name="gpio",
            version="1.0.0",
            author="Smollama Team",
            description="GPIO sensor plugin for Raspberry Pi",
            dependencies=["RPi.GPIO"],
            plugin_type="sensor",
        )

    @property
    def source_type(self) -> str:
        """Return 'gpio' as the source type."""
        return "gpio"

    @property
    def config_schema(self) -> dict[str, Any]:
        """JSON Schema for GPIO configuration."""
        return {
            "type": "object",
            "properties": {
                "mock": {"type": "boolean", "default": False},
                "pins": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {
                            "pin": {"type": "integer", "minimum": 1},
                            "name": {"type": "string"},
                            "mode": {
                                "type": "string",
                                "enum": ["input", "output"],
                            },
                        },
                        "required": ["pin", "name", "mode"],
                    },
                },
            },
            "required": ["pins"],
        }

    def check_dependencies(self) -> tuple[bool, str | None]:
        """Check if GPIO dependencies are available.

        Always returns success because we can fall back to mock mode.
        """
        try:
            import RPi.GPIO  # type: ignore[import-not-found]  # noqa: F401

            return (True, None)
        except ImportError:
            # GPIO not available, but we can run in mock mode
            return (
                True,
                None,
            )  # Still load the plugin, it will use mock mode

    def setup(self) -> None:
        """Initialize the GPIO reader."""
        if self._config is None:
            raise ValueError("GPIO config not provided")

        self._gpio_reader = GPIOReader(self._config)
        self._gpio_reader.setup()

    def teardown(self) -> None:
        """Clean up GPIO resources."""
        if self._gpio_reader is not None:
            self._gpio_reader.cleanup()
            self._gpio_reader = None

    @property
    def available_sources(self) -> list[str]:
        """List available GPIO pins as source IDs."""
        if self._gpio_reader is None:
            return []
        return [str(p.pin) for p in self._gpio_reader.configured_pins]

    async def read(self, source_id: str) -> Reading | None:
        """Read a single GPIO pin.

        Args:
            source_id: Pin number as string (e.g., "17").

        Returns:
            Reading with pin state, or None if pin not found.
        """
        if self._gpio_reader is None:
            return None

        try:
            pin = int(source_id)
            state = self._gpio_reader.get_pin_state(pin)
            return Reading(
                source_type="gpio",
                source_id=source_id,
                value=state.value,
                timestamp=datetime.now(),
                unit="boolean",
                metadata={"name": state.name, "mode": state.mode},
            )
        except (ValueError, KeyError):
            return None

    async def read_all(self) -> list[Reading]:
        """Read all configured GPIO pins.

        Returns:
            List of Reading objects for all pins.
        """
        if self._gpio_reader is None:
            return []

        readings = []
        now = datetime.now()

        for pin_state in self._gpio_reader.list_pins():
            readings.append(
                Reading(
                    source_type="gpio",
                    source_id=str(pin_state.pin),
                    value=pin_state.value,
                    timestamp=now,
                    unit="boolean",
                    metadata={"name": pin_state.name, "mode": pin_state.mode},
                )
            )

        return readings

    @property
    def is_mock_mode(self) -> bool:
        """Check if running in mock mode."""
        if self._gpio_reader is None:
            return True
        return self._gpio_reader.is_mock_mode
