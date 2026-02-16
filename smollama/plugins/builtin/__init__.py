"""Builtin plugins for smollama."""

from smollama.plugins.builtin.gpio_plugin import GPIOSensorPlugin
from smollama.plugins.builtin.system_plugin import SystemSensorPlugin

__all__ = ["GPIOSensorPlugin", "SystemSensorPlugin"]
