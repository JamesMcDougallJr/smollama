"""LCD 1602A 16x2 character display plugin via I2C backpack (PCF8574)."""

import logging
from typing import Any

from smollama.plugins.base import PluginMetadata, WritePlugin
from smollama.tools.base import Tool, ToolParameter

logger = logging.getLogger(__name__)


class LCDWriteTool(Tool):
    """Tool to write text to the LCD display."""

    def __init__(self, plugin: "LCD1602Plugin") -> None:
        self._plugin = plugin

    @property
    def name(self) -> str:
        return "lcd_write"

    @property
    def description(self) -> str:
        return (
            "Write text to the 16×2 LCD display. "
            "Each line is truncated to 16 characters. "
            "Clears the display before writing."
        )

    @property
    def parameters(self) -> list[ToolParameter]:
        return [
            ToolParameter(
                name="line1",
                type="string",
                description="Text for the top row (max 16 chars)",
                required=True,
            ),
            ToolParameter(
                name="line2",
                type="string",
                description="Text for the bottom row (max 16 chars)",
                required=False,
            ),
        ]

    async def execute(self, **kwargs: Any) -> str:
        line1 = str(kwargs.get("line1", ""))[:self._plugin._cols]
        line2 = str(kwargs.get("line2", ""))[:self._plugin._cols]

        lcd = self._plugin._lcd
        lcd.clear()
        lcd.cursor_pos = (0, 0)
        lcd.write_string(line1)
        if self._plugin._rows >= 2:
            lcd.cursor_pos = (1, 0)
            lcd.write_string(line2)

        logger.info(f"LCD: '{line1}' / '{line2}'")
        return f"Displayed: '{line1}' / '{line2}'"


class LCDClearTool(Tool):
    """Tool to clear the LCD display."""

    def __init__(self, plugin: "LCD1602Plugin") -> None:
        self._plugin = plugin

    @property
    def name(self) -> str:
        return "lcd_clear"

    @property
    def description(self) -> str:
        return "Clear the LCD display."

    @property
    def parameters(self) -> list[ToolParameter]:
        return []

    async def execute(self, **kwargs: Any) -> str:
        self._plugin._lcd.clear()
        logger.info("LCD cleared")
        return "Display cleared"


class LCD1602Plugin(WritePlugin):
    """16×2 character LCD display plugin via I2C backpack (PCF8574).

    Exposes two tools:
    - ``lcd_write(line1, line2)`` — write text to the display
    - ``lcd_clear()`` — clear the display

    Requires an I2C backpack module (PCF8574 chip) soldered to the LCD.
    Enable I2C on the Pi with ``raspi-config`` → Interface Options → I2C.
    Find the I2C address with ``i2cdetect -y 1`` (common values: 0x27 or 0x3F).
    """

    def __init__(self, config: dict[str, Any] | None = None) -> None:
        self._config = config or {}
        self._lcd: Any = None
        self._tools: list[Tool] = []
        self._cols: int = self._config.get("cols", 16)
        self._rows: int = self._config.get("rows", 2)

    @property
    def metadata(self) -> PluginMetadata:
        return PluginMetadata(
            name="lcd1602",
            version="1.0.0",
            author="Smollama Team",
            description="16×2 character LCD via I2C backpack (PCF8574)",
            dependencies=["RPLCD>=1.3"],
            plugin_type="write",
        )

    @property
    def source_type(self) -> str:
        return "lcd1602"

    @property
    def config_schema(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "i2c_address": {
                    "type": "integer",
                    "default": 0x27,
                    "description": "I2C address (0x27 for PCF8574, 0x3F for PCF8574A)",
                },
                "i2c_port": {
                    "type": "integer",
                    "default": 1,
                    "description": "I2C bus number (1 on all modern Pi)",
                },
                "cols": {"type": "integer", "default": 16},
                "rows": {"type": "integer", "default": 2},
                "backlight": {"type": "boolean", "default": True},
            },
            "additionalProperties": False,
        }

    def check_dependencies(self) -> tuple[bool, str | None]:
        try:
            import RPLCD  # noqa: F401
            import smbus2  # noqa: F401
            return (True, None)
        except ImportError as e:
            missing = str(e).split("'")[1] if "'" in str(e) else str(e)
            return (False, f"{missing} not installed — pip install 'RPLCD[i2c]'")

    def setup(self) -> None:
        from RPLCD.i2c import CharLCD

        address = self._config.get("i2c_address", 0x27)
        port = self._config.get("i2c_port", 1)
        cols = self._config.get("cols", 16)
        rows = self._config.get("rows", 2)
        backlight = self._config.get("backlight", True)

        self._cols = cols
        self._rows = rows

        self._lcd = CharLCD(
            "PCF8574",
            address,
            port=port,
            cols=cols,
            rows=rows,
            backlight_enabled=backlight,
        )
        self._tools = [LCDWriteTool(self), LCDClearTool(self)]
        logger.info(f"LCD1602 initialized at I2C address 0x{address:02X} on bus {port}")

    def teardown(self) -> None:
        if self._lcd is not None:
            try:
                self._lcd.clear()
                self._lcd.close(clear=True)
            except Exception as e:
                logger.warning(f"LCD teardown error: {e}")
            self._lcd = None
        self._tools = []

    # WritePlugin inherits from Tool; these stubs are required by the ABC.
    # This plugin overrides get_tools() to return helper instances, so these
    # are never called directly.
    @property
    def name(self) -> str:
        return "lcd1602"

    @property
    def description(self) -> str:
        return "16×2 LCD display — use lcd_write or lcd_clear tools"

    @property
    def parameters(self) -> list[ToolParameter]:
        return []

    async def execute(self, **kwargs: Any) -> str:
        raise NotImplementedError("Use lcd_write or lcd_clear tools directly")

    def get_tools(self) -> list[Tool]:
        return self._tools
