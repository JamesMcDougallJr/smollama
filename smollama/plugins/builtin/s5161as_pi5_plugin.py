"""5161AS single-digit 7-segment LED display plugin — Pi 5 (lgpio) variant."""

import logging
from typing import Any

from smollama.plugins.base import PluginMetadata, ToolPlugin
from smollama.tools.base import ToolParameter

logger = logging.getLogger(__name__)

# Segment encoding for common-anode display.
# Bit order: dp=7, g=6, f=5, e=4, d=3, c=2, b=1, a=0
# A segment is ON when its GPIO pin is LOW (active-low cathode).
_SEGMENTS: dict[str, int] = {
    "0": 0b00111111,  # a b c d e f
    "1": 0b00000110,  # b c
    "2": 0b01011011,  # a b d e g
    "3": 0b01001111,  # a b c d g
    "4": 0b01100110,  # b c f g
    "5": 0b01101101,  # a c d f g
    "6": 0b01111101,  # a c d e f g
    "7": 0b00000111,  # a b c
    "8": 0b01111111,  # all
    "9": 0b01101111,  # a b c d f g
    "-": 0b01000000,  # g
    "E": 0b01111001,  # a d e f g
    "r": 0b01010000,  # e g
    "H": 0b01110110,  # b c e f g
    "L": 0b00111000,  # d e f
    "P": 0b01110011,  # a b e f g
    "n": 0b01010100,  # c e g
    " ": 0b00000000,  # blank
}

# Order of segment GPIO pins: index matches bit position 0–7 (a, b, c, d, e, f, g, dp)
_SEG_ORDER = ["a", "b", "c", "d", "e", "f", "g", "dp"]

_DEFAULT_SEGMENT_PINS = {"a": 27, "b": 22, "c": 11, "d": 8, "e": 10, "f": 17, "g": 4, "dp": 9}


def _parse_digit(text: str) -> tuple[int, bool]:
    """Parse a single-character string into (segment_byte, show_dp).

    Accepts one character optionally followed by '.' for the decimal point.
    Examples: "8" → (0b01111111, False), "3." → (0b01001111, True), "" → blank.
    """
    text = text.strip()
    if not text:
        return (_SEGMENTS[" "], False)
    ch = text[0]
    dp = len(text) > 1 and text[1] == "."
    return (_SEGMENTS.get(ch.upper(), _SEGMENTS[" "]), dp)


class S5161ASPi5Plugin(ToolPlugin):
    """5161AS single-digit 7-segment LED display plugin — Pi 5 (lgpio) variant.

    Exposes a ``display_digit`` tool that lets the agent show a single character
    on the physical display.  Writes GPIO pins directly — no multiplexing needed
    since there is only one digit.

    Requires Pi 5 (RP1 I/O chip). On Pi 4 and earlier, use S5161ASPlugin
    (RPi.GPIO) instead — selection is automatic via check_dependencies().

    Wiring (default BCM pins):
      Pi Pin  7 (BCM  4) ──[220Ω]── Component Pin 10 (g)
      Pi Pin  9 (GND)    ─────────── Component Pin  8 (COM)
      Pi Pin 11 (BCM 17) ──[220Ω]── Component Pin  9 (f)
      Pi Pin 13 (BCM 27) ──[220Ω]── Component Pin  7 (a)
      Pi Pin 15 (BCM 22) ──[220Ω]── Component Pin  6 (b)
      Pi Pin 19 (BCM 10) ──[220Ω]── Component Pin  1 (e)
      Pi Pin 21 (BCM  9) ──[220Ω]── Component Pin  5 (dp)
      Pi Pin 23 (BCM 11) ──[220Ω]── Component Pin  4 (c)
      Pi Pin 24 (BCM  8) ──[220Ω]── Component Pin  2 (d)
      Pi Pin 25 (GND)    ─────────── Component Pin  3 (COM)
    """

    def __init__(self, config: dict[str, Any] | None = None) -> None:
        self._config: dict[str, Any] = config or {}
        self._handle: int | None = None  # lgpio chip handle, deferred open
        self._seg_pins: list[int] = []  # ordered a–dp
        self._common_pin: int | None = None

    # ------------------------------------------------------------------ #
    # PluginMetadata                                                        #
    # ------------------------------------------------------------------ #

    @property
    def metadata(self) -> PluginMetadata:
        return PluginMetadata(
            name="s5161as",
            version="1.0.0",
            author="Smollama Team",
            description="5161AS single-digit 7-segment LED display — Pi 5 (lgpio) variant",
            dependencies=["lgpio>=0.2"],
            plugin_type="tool",
        )

    @property
    def config_schema(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "segment_pins": {
                    "type": "object",
                    "properties": {seg: {"type": "integer"} for seg in _SEG_ORDER},
                    "default": _DEFAULT_SEGMENT_PINS,
                    "description": "BCM pin for each segment (a–g + dp)",
                },
                "common_pin": {
                    "type": ["integer", "null"],
                    "default": None,
                    "description": (
                        "BCM pin driving the common anode. "
                        "Set to null if the common anode is tied directly to 3.3V."
                    ),
                },
            },
            "additionalProperties": False,
        }

    def check_dependencies(self) -> tuple[bool, str | None]:
        from smollama.plugins.builtin.pi_platform import is_pi5
        if not is_pi5():
            return (False, "Pi 5 only — use S5161ASPlugin (RPi.GPIO) on Pi 4 and earlier")
        try:
            import lgpio  # noqa: F401
        except ImportError:
            return (False, "Missing package: lgpio")
        return (True, None)

    # ------------------------------------------------------------------ #
    # Lifecycle                                                             #
    # ------------------------------------------------------------------ #

    def setup(self) -> None:
        import lgpio

        seg_map = self._config.get("segment_pins", _DEFAULT_SEGMENT_PINS)
        self._seg_pins = [seg_map[s] for s in _SEG_ORDER]
        self._common_pin = self._config.get("common_pin", None)

        h = lgpio.gpiochip_open(0)
        self._handle = h

        # Segment pins: output, start LOW (segment off — active-high)
        for pin in self._seg_pins:
            lgpio.gpio_claim_output(h, pin, 0)

        # Common cathode: output, start LOW (digit enabled — cathode to GND)
        if self._common_pin is not None:
            lgpio.gpio_claim_output(h, self._common_pin, 0)

        logger.info(
            "5161AS (Pi5/lgpio) initialized: segments=%s, common_pin=%s",
            dict(zip(_SEG_ORDER, self._seg_pins)),
            self._common_pin,
        )

    def teardown(self) -> None:
        if self._handle is not None:
            import lgpio
            try:
                # Blank the display
                for pin in self._seg_pins:
                    lgpio.gpio_write(self._handle, pin, 0)
                if self._common_pin is not None:
                    lgpio.gpio_write(self._handle, self._common_pin, 1)
                lgpio.gpiochip_close(self._handle)
            except Exception as e:
                logger.warning("Error during 5161AS Pi5 teardown: %s", e)
            finally:
                self._handle = None

    # ------------------------------------------------------------------ #
    # Tool interface                                                        #
    # ------------------------------------------------------------------ #

    @property
    def name(self) -> str:
        return "display_digit"

    @property
    def description(self) -> str:
        return (
            "Display a single character on the 1-digit 7-segment LED. "
            "Accepts one character: digits 0-9, letters E/H/L/P/n/r, "
            "dash '-', space ' '. Append '.' for the decimal point "
            "(e.g. '3.' shows 3 with decimal point lit)."
        )

    @property
    def parameters(self) -> list[ToolParameter]:
        return [
            ToolParameter(
                name="text",
                type="string",
                description=(
                    "One character to show, optionally followed by '.' for decimal point. "
                    "Examples: '5', '3.', 'H', '-', ' ' (blank)."
                ),
                required=True,
            )
        ]

    async def execute(self, **kwargs: Any) -> str:
        import lgpio
        h = self._handle
        text = str(kwargs.get("text", ""))
        seg_byte, show_dp = _parse_digit(text)

        # Write segments a–g (active-high: ON → 1, OFF → 0)
        for bit in range(7):
            on = bool(seg_byte & (1 << bit))
            lgpio.gpio_write(h, self._seg_pins[bit], 1 if on else 0)

        # Decimal point
        lgpio.gpio_write(h, self._seg_pins[7], 1 if show_dp else 0)

        logger.debug("5161AS (Pi5) display updated: %r", text)
        return f"Displaying: {text!r}"
