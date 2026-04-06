"""Builtin plugins for smollama."""

from smollama.plugins.builtin.gpio_plugin import GPIOSensorPlugin
from smollama.plugins.builtin.hcsr04_plugin import HCSR04SensorPlugin
from smollama.plugins.builtin.macos_temp_plugin import MacOSTempPlugin
from smollama.plugins.builtin.s5161as_plugin import S5161ASPlugin
from smollama.plugins.builtin.s5161as_pi5_plugin import S5161ASPi5Plugin
from smollama.plugins.builtin.sh5461as_plugin import SH5461ASPlugin
from smollama.plugins.builtin.sh5461as_pi5_plugin import SH5461ASPi5Plugin
from smollama.plugins.builtin.system_plugin import SystemSensorPlugin

__all__ = [
    "GPIOSensorPlugin",
    "HCSR04SensorPlugin",
    "MacOSTempPlugin",
    "S5161ASPlugin",
    "S5161ASPi5Plugin",
    "SH5461ASPlugin",
    "SH5461ASPi5Plugin",
    "SystemSensorPlugin",
]
