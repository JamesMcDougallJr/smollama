"""GPIO LED write plugin."""

import logging
from typing import Any

from smollama.plugins.base import ObservationHook, PluginMetadata, WritePlugin
from smollama.tools.base import ToolParameter

logger = logging.getLogger(__name__)


class LEDPlugin(WritePlugin, ObservationHook):
    """Single GPIO LED write plugin.

    Exposes a 'set_led' tool that lets the LLM turn an LED on, off, or toggle it.

    Wiring (active_high=true, the default):
      LED anode  → resistor (220Ω–1kΩ) → GPIO pin
      LED cathode → Pi GND

    Wiring (active_high=false):
      LED anode  → 3.3V
      LED cathode → resistor → GPIO pin
    """

    def __init__(self, config: dict[str, Any] | None = None) -> None:
        self._config: dict[str, Any] = config or {}
        self._backend = None
        self._current_state: bool = False

    @property
    def metadata(self) -> PluginMetadata:
        return PluginMetadata(
            name="led",
            version="1.0.0",
            author="Smollama Team",
            description="Single GPIO LED write plugin — LLM-controllable on/off/toggle",
            dependencies=[],
            plugin_type="write",
        )

    @property
    def config_schema(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "pin":         {"type": "integer", "minimum": 1},
                "active_high": {"type": "boolean", "default": True},
            },
            "required": ["pin"],
            "additionalProperties": False,
        }

    def check_dependencies(self) -> tuple[bool, str | None]:
        try:
            import lgpio  # noqa: F401
            return (True, None)
        except ImportError:
            pass
        try:
            import RPi.GPIO  # noqa: F401
            return (True, None)
        except ImportError:
            pass
        return (False, "Missing GPIO library: install lgpio or RPi.GPIO")

    def setup(self) -> None:
        from smollama.plugins.builtin.gpio_backend import create_backend

        pin = self._config["pin"]
        self._backend = create_backend()
        self._backend.setup_output(pin, 0)  # LED off at startup
        self._current_state = False
        logger.info("LED plugin initialised on BCM%d", pin)

    def teardown(self) -> None:
        if self._backend is not None:
            try:
                self._backend.write(self._config["pin"], 0)  # ensure LED off
                self._backend.cleanup()
            except Exception as e:
                logger.warning("Error during LED teardown: %s", e)
            finally:
                self._backend = None

    # --- Tool interface ---

    @property
    def name(self) -> str:
        return "set_led"

    @property
    def description(self) -> str:
        pin = self._config.get("pin", "?")
        return (
            f"Control the LED on BCM{pin}. "
            "Turn it on, turn it off, or toggle its current state."
        )

    @property
    def parameters(self) -> list[ToolParameter]:
        return [
            ToolParameter(
                name="state",
                type="string",
                description='Desired LED state: "on", "off", or "toggle".',
                required=True,
            )
        ]

    async def on_observation_begin(self) -> None:
        if self._backend is None:
            return
        pin = self._config["pin"]
        active_high = self._config.get("active_high", True)
        try:
            self._backend.write(pin, 1 if active_high else 0)
            self._current_state = True
        except Exception as e:
            logger.debug("LED on_observation_begin error: %s", e)

    async def on_observation_end(self, success: bool) -> None:
        if self._backend is None:
            return
        pin = self._config["pin"]
        active_high = self._config.get("active_high", True)
        try:
            self._backend.write(pin, 0 if active_high else 1)
            self._current_state = False
        except Exception as e:
            logger.debug("LED on_observation_end error: %s", e)

    async def execute(self, **kwargs: Any) -> str:
        state = str(kwargs.get("state", "")).strip().lower()
        active_high = self._config.get("active_high", True)
        pin = self._config["pin"]

        if state == "toggle":
            target = not self._current_state
        elif state == "on":
            target = True
        elif state == "off":
            target = False
        else:
            return f"Unknown state {state!r}. Use 'on', 'off', or 'toggle'."

        gpio_value = 1 if (target == active_high) else 0
        self._backend.write(pin, gpio_value)
        self._current_state = target

        return f"LED on BCM{pin} is now {'ON' if target else 'OFF'}."
