"""Builtin plugins for smollama."""

from smollama.plugins.builtin.gpio_plugin import GPIOSensorPlugin
from smollama.plugins.builtin.macos_temp_plugin import MacOSTempPlugin
from smollama.plugins.builtin.system_plugin import SystemSensorPlugin

__all__ = ["GPIOSensorPlugin", "MacOSTempPlugin", "SystemSensorPlugin"]
