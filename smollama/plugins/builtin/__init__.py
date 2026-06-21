"""Builtin plugins for smollama."""

from smollama.plugins.builtin.dht11_plugin import DHT11SensorPlugin
from smollama.plugins.builtin.gpio_plugin import GPIOSensorPlugin
from smollama.plugins.builtin.led_plugin import LEDPlugin
from smollama.plugins.builtin.hcsr04_plugin import HCSR04SensorPlugin
from smollama.plugins.builtin.lcd1602_plugin import LCD1602Plugin
from smollama.plugins.builtin.macos_temp_plugin import MacOSTempPlugin
from smollama.plugins.builtin.s5161as_plugin import S5161ASPlugin, S5161ASPi5Plugin
from smollama.plugins.builtin.sh5461as_plugin import SH5461ASPlugin, SH5461ASPi5Plugin
from smollama.plugins.builtin.jetson_inference_plugin import JetsonInferencePlugin
from smollama.plugins.builtin.system_plugin import SystemSensorPlugin

__all__ = [
    "DHT11SensorPlugin",
    "GPIOSensorPlugin",
    "JetsonInferencePlugin",
    "LEDPlugin",
    "HCSR04SensorPlugin",
    "LCD1602Plugin",
    "MacOSTempPlugin",
    "S5161ASPlugin",
    "S5161ASPi5Plugin",   # backwards compat alias
    "SH5461ASPlugin",
    "SH5461ASPi5Plugin",  # backwards compat alias
    "SystemSensorPlugin",
]
