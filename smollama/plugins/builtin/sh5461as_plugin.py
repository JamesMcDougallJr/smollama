"""SH5461AS 4-digit 7-segment LED display plugin.

Auto-detects Pi version and uses the appropriate GPIO backend
(RPi.GPIO for Pi 4 and earlier, lgpio for Pi 5).
"""

import logging
import threading
import time
from typing import Any

from smollama.plugins.base import PluginMetadata, WritePlugin
from smollama.tools.base import ToolParameter

logger = logging.getLogger(__name__)

# Segment encoding for common-anode display.
# Bit order: dp=7, g=6, f=5, e=4, d=3, c=2, b=1, a=0
# A segment is ON when the corresponding GPIO pin is LOW (active-low cathode).
# Here we store the ON bitmask; the mux loop inverts it when writing GPIO.
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

# Order of segment GPIO pins: index matches bit position 0-7 (a, b, c, d, e, f, g, dp)
_SEG_ORDER = ["a", "b", "c", "d", "e", "f", "g", "dp"]

_DEFAULT_DIGIT_PINS = [17, 27, 22, 5]
_DEFAULT_SEGMENT_PINS = {"a": 6, "b": 13, "c": 19, "d": 26, "e": 21, "f": 20, "g": 16, "dp": 12}


def _parse_text(text: str) -> list[tuple[int, bool]]:
    """Convert a display string into a 4-slot buffer.

    Each slot is (segment_byte, show_dp). Handles decimal points embedded in
    the string (e.g. "12.34" -> 4 slots with dp on slot index 1).

    Returns exactly 4 slots, right-aligned, blank-padded on the left.
    """
    text = text.strip()
    slots: list[tuple[int, bool]] = []

    i = 0
    while i < len(text) and len(slots) < 4:
        ch = text[i]
        # Check if the *next* char is a decimal point
        dp = (i + 1 < len(text) and text[i + 1] == ".")
        seg = _SEGMENTS.get(ch.upper(), _SEGMENTS[" "])
        slots.append((seg, dp))
        i += 2 if dp else 1

    # Right-align: pad left with blanks
    while len(slots) < 4:
        slots.insert(0, (_SEGMENTS[" "], False))

    return slots[:4]


class SH5461ASPlugin(WritePlugin):
    """SH5461AS 4-digit 7-segment LED display write plugin.

    Exposes a ``display_value`` tool that lets the agent show up to 4
    characters on the physical display.  A background thread handles
    the multiplexing so the caller never blocks.

    Auto-detects Pi version and uses RPi.GPIO (Pi 4) or lgpio (Pi 5).

    Wiring (default BCM pins):
      D1 (leftmost) -> BCM 17  (Pi Pin 11)   -- direct
      D2            -> BCM 27  (Pi Pin 13)   -- direct
      D3            -> BCM 22  (Pi Pin 15)   -- direct
      D4 (rightmost)-> BCM 5   (Pi Pin 29)   -- direct
      Seg a         -> BCM 6   (Pi Pin 31)   -- via 220 ohm
      Seg b         -> BCM 13  (Pi Pin 33)   -- via 220 ohm
      Seg c         -> BCM 19  (Pi Pin 35)   -- via 220 ohm
      Seg d         -> BCM 26  (Pi Pin 37)   -- via 220 ohm
      Seg e         -> BCM 21  (Pi Pin 40)   -- via 220 ohm
      Seg f         -> BCM 20  (Pi Pin 38)   -- via 220 ohm
      Seg g         -> BCM 16  (Pi Pin 36)   -- via 220 ohm
      Seg dp        -> BCM 12  (Pi Pin 32)   -- via 220 ohm
    """

    def __init__(self, config: dict[str, Any] | None = None) -> None:
        self._config: dict[str, Any] = config or {}
        self._backend: Any = None  # GPIOBackend instance, created in setup()
        self._digit_pins: list[int] = []
        self._seg_pins: list[int] = []  # ordered a-dp
        self._buffer: list[tuple[int, bool]] = [(_SEGMENTS[" "], False)] * 4
        self._running = False
        self._thread: threading.Thread | None = None

    # ------------------------------------------------------------------ #
    # PluginMetadata                                                        #
    # ------------------------------------------------------------------ #

    @property
    def metadata(self) -> PluginMetadata:
        return PluginMetadata(
            name="sh5461as",
            version="2.0.0",
            author="Smollama Team",
            description="SH5461AS 4-digit 7-segment LED display write plugin",
            dependencies=[],
            plugin_type="write",
        )

    @property
    def config_schema(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "digit_pins": {
                    "type": "array",
                    "items": {"type": "integer", "minimum": 1},
                    "minItems": 4,
                    "maxItems": 4,
                    "default": _DEFAULT_DIGIT_PINS,
                    "description": "BCM pins for D1-D4 (left to right)",
                },
                "segment_pins": {
                    "type": "object",
                    "properties": {seg: {"type": "integer"} for seg in _SEG_ORDER},
                    "default": _DEFAULT_SEGMENT_PINS,
                    "description": "BCM pin for each segment (a-g + dp)",
                },
            },
            "additionalProperties": False,
        }

    def check_dependencies(self) -> tuple[bool, str | None]:
        # Accept either GPIO library
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
        return (False, "Missing GPIO library: install RPi.GPIO or lgpio")

    # ------------------------------------------------------------------ #
    # Lifecycle                                                             #
    # ------------------------------------------------------------------ #

    def setup(self) -> None:
        from smollama.plugins.builtin.gpio_backend import create_backend

        self._backend = create_backend()

        digit_pins = self._config.get("digit_pins", _DEFAULT_DIGIT_PINS)
        seg_map = self._config.get("segment_pins", _DEFAULT_SEGMENT_PINS)

        # Build ordered segment pin list (a, b, c, d, e, f, g, dp)
        self._seg_pins = [seg_map[s] for s in _SEG_ORDER]
        self._digit_pins = list(digit_pins)

        # Digit pins: output, start LOW (digit off)
        for pin in self._digit_pins:
            self._backend.setup_output(pin, 0)

        # Segment pins: output, start HIGH (segment off -- active-low)
        for pin in self._seg_pins:
            self._backend.setup_output(pin, 1)

        self._running = True
        self._thread = threading.Thread(target=self._mux_loop, daemon=True)
        self._thread.start()

        logger.info(
            "SH5461AS initialized: digits=%s, segments=%s",
            self._digit_pins,
            dict(zip(_SEG_ORDER, self._seg_pins)),
        )

    def teardown(self) -> None:
        self._running = False
        if self._thread is not None:
            self._thread.join(timeout=1.0)
            self._thread = None

        if self._backend is not None:
            try:
                # Turn everything off before cleanup
                for pin in self._digit_pins:
                    self._backend.write(pin, 0)
                for pin in self._seg_pins:
                    self._backend.write(pin, 1)
                self._backend.cleanup()
            except Exception as e:
                logger.warning("Error during SH5461AS teardown: %s", e)
            finally:
                self._backend = None

    # ------------------------------------------------------------------ #
    # Multiplexing loop (background thread)                                #
    # ------------------------------------------------------------------ #

    def _mux_loop(self) -> None:
        backend = self._backend
        digit_pins = self._digit_pins
        seg_pins = self._seg_pins

        while self._running:
            buf = self._buffer  # snapshot reference; safe for single-writer
            for i, d_pin in enumerate(digit_pins):
                if not self._running:
                    break

                seg_byte, show_dp = buf[i]

                # All digits off
                for p in digit_pins:
                    backend.write(p, 0)

                # Write segments (active-low: segment ON -> pin LOW)
                for bit in range(7):  # a-g
                    on = bool(seg_byte & (1 << bit))
                    backend.write(seg_pins[bit], 0 if on else 1)

                # Decimal point
                backend.write(seg_pins[7], 0 if show_dp else 1)

                # Enable this digit
                backend.write(d_pin, 1)

                time.sleep(0.001)

        # All off on exit
        if self._backend is not None:
            try:
                for p in digit_pins:
                    backend.write(p, 0)
                for p in seg_pins:
                    backend.write(p, 1)
            except Exception:
                pass

    # ------------------------------------------------------------------ #
    # Tool interface                                                        #
    # ------------------------------------------------------------------ #

    @property
    def name(self) -> str:
        return "display_value"

    @property
    def description(self) -> str:
        return (
            "Display a value or short message on the 4-digit 7-segment LED. "
            "Accepts up to 4 characters: digits 0-9, letters E/H/L/P/n/r, "
            "dash '-', space ' '. Embed a decimal point directly in the string "
            "(e.g. '12.34', '3.14', '0.5 '). Text is right-aligned."
        )

    @property
    def parameters(self) -> list[ToolParameter]:
        return [
            ToolParameter(
                name="text",
                type="string",
                description=(
                    "Up to 4 characters to show. Examples: '1234', '12.34', "
                    "' Hi ', 'Err ', '----'. Decimal point counts toward the "
                    "preceding digit, not as a separate character."
                ),
                required=True,
            )
        ]

    async def execute(self, **kwargs: Any) -> str:
        text = str(kwargs.get("text", ""))
        self._buffer = _parse_text(text)
        logger.debug("SH5461AS display updated: %r", text)
        return f"Displaying: {text!r}"


# Backwards compatibility alias
SH5461ASPi5Plugin = SH5461ASPlugin
