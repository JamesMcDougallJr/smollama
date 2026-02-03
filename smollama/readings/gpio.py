"""GPIO reading provider wrapping the existing GPIOReader."""

from datetime import datetime

from ..gpio_reader import GPIOReader
from .base import Reading, ReadingProvider


class GPIOReadingProvider(ReadingProvider):
    """Provides readings from GPIO pins via the unified interface."""

    def __init__(self, gpio_reader: GPIOReader):
        """Initialize with an existing GPIOReader.

        Args:
            gpio_reader: Configured GPIOReader instance.
        """
        self._gpio = gpio_reader

    @property
    def source_type(self) -> str:
        """Return 'gpio' as the source type."""
        return "gpio"

    @property
    def available_sources(self) -> list[str]:
        """List available GPIO pins as source IDs."""
        return [str(p.pin) for p in self._gpio.configured_pins]

    async def read(self, source_id: str) -> Reading | None:
        """Read a single GPIO pin.

        Args:
            source_id: Pin number as string (e.g., "17").

        Returns:
            Reading with pin state, or None if pin not found.
        """
        try:
            pin = int(source_id)
            state = self._gpio.get_pin_state(pin)
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
        readings = []
        now = datetime.now()

        for pin_state in self._gpio.list_pins():
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
