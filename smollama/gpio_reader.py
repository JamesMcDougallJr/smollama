"""GPIO reader for Raspberry Pi with mock mode support."""

import logging
import random
from dataclasses import dataclass
from typing import Callable

from .config import GPIOConfig, GPIOPinConfig

logger = logging.getLogger(__name__)

# Try to import RPi.GPIO, fall back to mock if not available
try:
    import RPi.GPIO as GPIO

    GPIO_AVAILABLE = True
except ImportError:
    GPIO_AVAILABLE = False
    logger.info("RPi.GPIO not available, will use mock mode")


@dataclass
class PinState:
    """State of a GPIO pin."""

    pin: int
    name: str
    value: int
    mode: str


EdgeCallback = Callable[[int, int], None]


class GPIOReader:
    """Reader for GPIO digital inputs."""

    def __init__(self, config: GPIOConfig):
        self.config = config
        self._mock_mode = config.mock or not GPIO_AVAILABLE
        self._pins: dict[int, GPIOPinConfig] = {}
        self._callbacks: dict[int, list[EdgeCallback]] = {}
        self._initialized = False

        # Build pin lookup
        for pin_config in config.pins:
            self._pins[pin_config.pin] = pin_config

    def setup(self) -> None:
        """Initialize GPIO pins."""
        if self._initialized:
            return

        if self._mock_mode:
            logger.info("GPIO running in mock mode")
            self._initialized = True
            return

        # Set up real GPIO
        try:
            GPIO.setmode(GPIO.BCM)
            GPIO.setwarnings(False)

            for pin_num, pin_config in self._pins.items():
                if pin_config.mode == "input":
                    GPIO.setup(pin_num, GPIO.IN, pull_up_down=GPIO.PUD_DOWN)
                    logger.debug(f"Set up pin {pin_num} ({pin_config.name}) as input")

            self._initialized = True
            logger.info(f"GPIO initialized with {len(self._pins)} pins")
        except Exception as e:
            logger.warning(f"Real GPIO setup failed ({e}), falling back to mock mode")
            self._mock_mode = True
            self._initialized = True

    def cleanup(self) -> None:
        """Clean up GPIO resources."""
        if not self._initialized:
            return

        if not self._mock_mode:
            GPIO.cleanup()

        self._initialized = False
        logger.info("GPIO cleaned up")

    def read(self, pin: int) -> int:
        """Read the current state of a pin.

        Args:
            pin: GPIO pin number (BCM numbering).

        Returns:
            Pin state: 0 (LOW) or 1 (HIGH).

        Raises:
            ValueError: If pin is not configured.
        """
        if pin not in self._pins:
            raise ValueError(f"Pin {pin} is not configured")

        if not self._initialized:
            self.setup()

        if self._mock_mode:
            # Return random value in mock mode for testing
            return random.randint(0, 1)

        return GPIO.input(pin)

    def read_by_name(self, name: str) -> int:
        """Read a pin by its configured name.

        Args:
            name: Configured name of the pin.

        Returns:
            Pin state: 0 (LOW) or 1 (HIGH).

        Raises:
            ValueError: If no pin with that name exists.
        """
        for pin_num, pin_config in self._pins.items():
            if pin_config.name == name:
                return self.read(pin_num)

        raise ValueError(f"No pin configured with name '{name}'")

    def get_pin_state(self, pin: int) -> PinState:
        """Get the full state of a pin.

        Args:
            pin: GPIO pin number.

        Returns:
            PinState with current value and config.
        """
        if pin not in self._pins:
            raise ValueError(f"Pin {pin} is not configured")

        pin_config = self._pins[pin]
        value = self.read(pin)

        return PinState(
            pin=pin,
            name=pin_config.name,
            value=value,
            mode=pin_config.mode,
        )

    def list_pins(self) -> list[PinState]:
        """List all configured pins with their current states.

        Returns:
            List of PinState objects.
        """
        if not self._initialized:
            self.setup()

        states = []
        for pin_num in self._pins:
            states.append(self.get_pin_state(pin_num))

        return states

    def add_edge_callback(
        self,
        pin: int,
        callback: EdgeCallback,
        edge: str = "both",
    ) -> None:
        """Add a callback for edge detection.

        Args:
            pin: GPIO pin number.
            callback: Function(pin, value) to call on edge.
            edge: "rising", "falling", or "both".
        """
        if pin not in self._pins:
            raise ValueError(f"Pin {pin} is not configured")

        if not self._initialized:
            self.setup()

        if pin not in self._callbacks:
            self._callbacks[pin] = []

        self._callbacks[pin].append(callback)

        if not self._mock_mode:
            edge_type = {
                "rising": GPIO.RISING,
                "falling": GPIO.FALLING,
                "both": GPIO.BOTH,
            }.get(edge, GPIO.BOTH)

            def gpio_callback(channel: int) -> None:
                value = GPIO.input(channel)
                for cb in self._callbacks.get(channel, []):
                    cb(channel, value)

            GPIO.add_event_detect(pin, edge_type, callback=gpio_callback)

        logger.debug(f"Added edge callback for pin {pin}")

    def get_pin_by_name(self, name: str) -> int | None:
        """Get pin number by configured name.

        Args:
            name: Configured name of the pin.

        Returns:
            Pin number or None if not found.
        """
        for pin_num, pin_config in self._pins.items():
            if pin_config.name == name:
                return pin_num
        return None

    @property
    def is_mock_mode(self) -> bool:
        """Check if running in mock mode."""
        return self._mock_mode

    def set_mock_mode(self, mock: bool) -> dict:
        """Toggle between mock and real GPIO mode at runtime.

        Args:
            mock: True to switch to mock mode, False for real GPIO.

        Returns:
            Dict with mock_mode, changed, and error keys.
        """
        if mock == self._mock_mode:
            return {"mock_mode": self._mock_mode, "changed": False, "error": None}

        if not mock:
            # Switching mock -> real
            if not GPIO_AVAILABLE:
                return {
                    "mock_mode": self._mock_mode,
                    "changed": False,
                    "error": "RPi.GPIO not available on this system",
                }
            try:
                GPIO.setmode(GPIO.BCM)
                GPIO.setwarnings(False)
                for pin_num, pin_config in self._pins.items():
                    if pin_config.mode == "input":
                        GPIO.setup(pin_num, GPIO.IN, pull_up_down=GPIO.PUD_DOWN)
                self._mock_mode = False
                logger.info("Switched to real GPIO mode")
                return {"mock_mode": False, "changed": True, "error": None}
            except Exception as e:
                logger.warning(f"Failed to switch to real GPIO: {e}")
                return {
                    "mock_mode": self._mock_mode,
                    "changed": False,
                    "error": str(e),
                }
        else:
            # Switching real -> mock
            try:
                GPIO.cleanup()
            except Exception:
                pass
            self._mock_mode = True
            logger.info("Switched to mock GPIO mode")
            return {"mock_mode": True, "changed": True, "error": None}

    @property
    def configured_pins(self) -> list[GPIOPinConfig]:
        """Get list of configured pins."""
        return list(self._pins.values())
