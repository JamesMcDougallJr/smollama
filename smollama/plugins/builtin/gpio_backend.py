"""GPIO backend abstraction for Pi 4 (RPi.GPIO) and Pi 5 (lgpio).

Provides a unified interface for GPIO output operations so that
display and actuator plugins don't need separate Pi4/Pi5 variants.
"""

import logging
from abc import ABC, abstractmethod

logger = logging.getLogger(__name__)


class GPIOBackend(ABC):
    """Abstract GPIO output operations."""

    @abstractmethod
    def setup_output(self, pin: int, initial: int) -> None:
        """Claim a pin as output with an initial value (0 or 1)."""
        pass

    @abstractmethod
    def write(self, pin: int, value: int) -> None:
        """Write a value (0 or 1) to a pin."""
        pass

    @abstractmethod
    def cleanup(self) -> None:
        """Release all claimed pins and free resources."""
        pass


class RPiGPIOBackend(GPIOBackend):
    """RPi.GPIO backend for Pi 4 and earlier."""

    def __init__(self) -> None:
        import RPi.GPIO as GPIO

        self._gpio = GPIO
        self._pins: list[int] = []
        GPIO.setmode(GPIO.BCM)
        GPIO.setwarnings(False)

    def setup_output(self, pin: int, initial: int) -> None:
        self._gpio.setup(pin, self._gpio.OUT, initial=initial)
        self._pins.append(pin)

    def write(self, pin: int, value: int) -> None:
        self._gpio.output(pin, value)

    def cleanup(self) -> None:
        if self._pins:
            try:
                self._gpio.cleanup(self._pins)
            except Exception as e:
                logger.warning("RPi.GPIO cleanup error: %s", e)
            self._pins.clear()


class LGPIOBackend(GPIOBackend):
    """lgpio backend for Pi 5."""

    def __init__(self, chip: int = 0) -> None:
        import lgpio

        self._lgpio = lgpio
        self._handle = lgpio.gpiochip_open(chip)

    def setup_output(self, pin: int, initial: int) -> None:
        self._lgpio.gpio_claim_output(self._handle, pin, initial)

    def write(self, pin: int, value: int) -> None:
        self._lgpio.gpio_write(self._handle, pin, value)

    def cleanup(self) -> None:
        if self._handle is not None:
            try:
                self._lgpio.gpiochip_close(self._handle)
            except Exception as e:
                logger.warning("lgpio cleanup error: %s", e)
            self._handle = None


def create_backend(chip: int = 0) -> GPIOBackend:
    """Auto-detect Pi version and return the appropriate GPIO backend.

    Args:
        chip: GPIO chip number (only used for lgpio on Pi 5).

    Returns:
        GPIOBackend instance for the current platform.

    Raises:
        RuntimeError: If no GPIO library is available.
    """
    from smollama.plugins.builtin.pi_platform import is_pi5

    if is_pi5():
        return LGPIOBackend(chip=chip)
    else:
        return RPiGPIOBackend()
