"""GPIO-related tools for the agent."""

from typing import Any

from ..gpio_reader import GPIOReader
from .base import Tool, ToolParameter


class ReadGPIOTool(Tool):
    """Tool for reading a GPIO pin state."""

    def __init__(self, gpio_reader: GPIOReader):
        self._gpio = gpio_reader

    @property
    def name(self) -> str:
        return "read_gpio"

    @property
    def description(self) -> str:
        pins_info = ", ".join(
            f"{p.name} (pin {p.pin})" for p in self._gpio.configured_pins
        )
        return f"Read the current state of a GPIO pin. Available pins: {pins_info}"

    @property
    def parameters(self) -> list[ToolParameter]:
        return [
            ToolParameter(
                name="pin",
                type="string",
                description="The pin to read. Can be a pin number (e.g., '17') or pin name (e.g., 'motion_sensor')",
                required=True,
            ),
        ]

    async def execute(self, pin: str, **kwargs: Any) -> dict[str, Any]:
        """Read a GPIO pin.

        Args:
            pin: Pin number or name.

        Returns:
            Dict with pin state information.
        """
        try:
            # Try as number first
            try:
                pin_num = int(pin)
            except ValueError:
                # Try as name
                pin_num = self._gpio.get_pin_by_name(pin)
                if pin_num is None:
                    return {"error": f"Unknown pin: {pin}"}

            state = self._gpio.get_pin_state(pin_num)
            return {
                "pin": state.pin,
                "name": state.name,
                "value": state.value,
                "state": "HIGH" if state.value else "LOW",
            }
        except Exception as e:
            return {"error": str(e)}


class ListGPIOTool(Tool):
    """Tool for listing all configured GPIO pins and their states."""

    def __init__(self, gpio_reader: GPIOReader):
        self._gpio = gpio_reader

    @property
    def name(self) -> str:
        return "list_gpio"

    @property
    def description(self) -> str:
        return "List all configured GPIO pins and their current states."

    @property
    def parameters(self) -> list[ToolParameter]:
        return []

    async def execute(self, **kwargs: Any) -> dict[str, Any]:
        """List all GPIO pins.

        Returns:
            Dict with list of pin states.
        """
        try:
            states = self._gpio.list_pins()
            return {
                "mock_mode": self._gpio.is_mock_mode,
                "pins": [
                    {
                        "pin": s.pin,
                        "name": s.name,
                        "value": s.value,
                        "state": "HIGH" if s.value else "LOW",
                        "mode": s.mode,
                    }
                    for s in states
                ],
            }
        except Exception as e:
            return {"error": str(e)}
