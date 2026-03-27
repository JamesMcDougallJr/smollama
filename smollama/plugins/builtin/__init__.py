"""Builtin plugins for smollama."""

from smollama.plugins.builtin.gpio_plugin import GPIOSensorPlugin
from smollama.plugins.builtin.hcsr04_plugin import HCSR04SensorPlugin
from smollama.plugins.builtin.macos_temp_plugin import MacOSTempPlugin
from smollama.plugins.builtin.system_plugin import SystemSensorPlugin

__all__ = ["GPIOSensorPlugin", "HCSR04SensorPlugin", "MacOSTempPlugin", "SystemSensorPlugin"]
