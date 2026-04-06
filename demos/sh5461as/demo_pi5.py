#!/usr/bin/env python3
"""
SH5461AS 4-Digit 7-Segment LED Display Demo — Raspberry Pi 5 (lgpio)

Wiring (BCM pin numbers, same as Pi 4 demo):
  D1 (leftmost) → BCM 17  (Pi Pin 11)   — direct
  D2            → BCM 27  (Pi Pin 13)   — direct
  D3            → BCM 22  (Pi Pin 15)   — direct
  D4 (rightmost)→ BCM 5   (Pi Pin 29)   — direct
  Seg a         → BCM 6   (Pi Pin 31)   — via 220Ω resistor
  Seg b         → BCM 13  (Pi Pin 33)   — via 220Ω resistor
  Seg c         → BCM 19  (Pi Pin 35)   — via 220Ω resistor
  Seg d         → BCM 26  (Pi Pin 37)   — via 220Ω resistor
  Seg e         → BCM 21  (Pi Pin 40)   — via 220Ω resistor
  Seg f         → BCM 20  (Pi Pin 38)   — via 220Ω resistor
  Seg g         → BCM 16  (Pi Pin 36)   — via 220Ω resistor
  Seg dp        → BCM 12  (Pi Pin 32)   — via 220Ω resistor

Run with: uv run --group pi5 demo_pi5.py
For Pi 4 and earlier, use demo.py (RPi.GPIO) instead.
"""

import threading
import time

import lgpio

# ── Pin configuration ─────────────────────────────────────────────────────────

DIGIT_PINS = [17, 27, 22, 5]   # D1–D4, left to right

# Segment order: a  b   c   d   e   f   g   dp
SEG_PINS    = [6, 13, 19, 26, 21, 20, 16, 12]

# ── Segment encoding (common-anode, active-low) ───────────────────────────────
# Bit 0=a, 1=b, 2=c, 3=d, 4=e, 5=f, 6=g, 7=dp
# A '1' bit means the segment is ON (GPIO pin will be pulled LOW).

SEGMENTS = {
    "0": 0b00111111,
    "1": 0b00000110,
    "2": 0b01011011,
    "3": 0b01001111,
    "4": 0b01100110,
    "5": 0b01101101,
    "6": 0b01111101,
    "7": 0b00000111,
    "8": 0b01111111,
    "9": 0b01101111,
    "-": 0b01000000,
    "E": 0b01111001,
    "r": 0b01010000,
    "H": 0b01110110,
    "L": 0b00111000,
    "P": 0b01110011,
    "n": 0b01010100,
    " ": 0b00000000,
}


# ── Display class ─────────────────────────────────────────────────────────────

class Display:
    """Drives the SH5461AS via a background multiplexing thread (lgpio)."""

    def __init__(self) -> None:
        self._buffer: list[tuple[int, bool]] = [(SEGMENTS[" "], False)] * 4
        self._running = False
        self._thread: threading.Thread | None = None

        self._h = lgpio.gpiochip_open(0)

        for pin in DIGIT_PINS:
            lgpio.gpio_claim_output(self._h, pin, 0)   # LOW = digit off
        for pin in SEG_PINS:
            lgpio.gpio_claim_output(self._h, pin, 1)   # HIGH = segment off (active-low)

    def start(self) -> None:
        self._running = True
        self._thread = threading.Thread(target=self._mux_loop, daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._running = False
        if self._thread:
            self._thread.join(timeout=1.0)
        # Blank the display
        for pin in DIGIT_PINS:
            lgpio.gpio_write(self._h, pin, 0)
        for pin in SEG_PINS:
            lgpio.gpio_write(self._h, pin, 1)
        lgpio.gpiochip_close(self._h)

    def show(self, text: str) -> None:
        """Update what is shown on the display.

        Accepts up to 4 characters.  A decimal point embedded in the string
        (e.g. "12.34") attaches to the preceding digit rather than occupying
        its own slot.  Text is right-aligned, blank-padded on the left.
        """
        self._buffer = _parse(text)

    def _mux_loop(self) -> None:
        while self._running:
            buf = self._buffer
            for i, d_pin in enumerate(DIGIT_PINS):
                if not self._running:
                    break
                seg_byte, dp = buf[i]

                # All digits off
                for p in DIGIT_PINS:
                    lgpio.gpio_write(self._h, p, 0)

                # Write segments a–g (active-low: ON → 0, OFF → 1)
                for bit in range(7):
                    on = bool(seg_byte & (1 << bit))
                    lgpio.gpio_write(self._h, SEG_PINS[bit], 0 if on else 1)

                # Decimal point
                lgpio.gpio_write(self._h, SEG_PINS[7], 0 if dp else 1)

                # Enable this digit
                lgpio.gpio_write(self._h, d_pin, 1)
                time.sleep(0.001)


def _parse(text: str) -> list[tuple[int, bool]]:
    """Convert a string to a 4-slot [(segment_byte, show_dp)] buffer."""
    text = text.strip()
    slots: list[tuple[int, bool]] = []
    i = 0
    while i < len(text) and len(slots) < 4:
        ch = text[i]
        dp = i + 1 < len(text) and text[i + 1] == "."
        seg = SEGMENTS.get(ch.upper(), SEGMENTS[" "])
        slots.append((seg, dp))
        i += 2 if dp else 1
    while len(slots) < 4:
        slots.insert(0, (SEGMENTS[" "], False))
    return slots[:4]


# ── Demo sequences ────────────────────────────────────────────────────────────

def demo_count(disp: Display, end: int = 100, delay: float = 0.05) -> None:
    """Count from 0 to end."""
    for n in range(end + 1):
        disp.show(str(n))
        time.sleep(delay)


def demo_float(disp: Display) -> None:
    """Scroll some float values."""
    values = ["3.14", "2.72", "1.41", "0.5 ", "99.9"]
    for v in values:
        disp.show(v)
        time.sleep(0.8)


def demo_message(disp: Display, msg: str, duration: float = 1.5) -> None:
    disp.show(msg)
    time.sleep(duration)


def demo_flash(disp: Display, msg: str, times: int = 4) -> None:
    for _ in range(times):
        disp.show(msg)
        time.sleep(0.3)
        disp.show("    ")
        time.sleep(0.2)


# ── Entry point ───────────────────────────────────────────────────────────────

def main() -> None:
    # Verify we are actually on Pi 5 before touching lgpio
    try:
        with open("/proc/device-tree/model") as f:
            if "Raspberry Pi 5" not in f.read():
                print("ERROR: This demo requires a Raspberry Pi 5.")
                print("Use demo.py (RPi.GPIO) on Pi 4 and earlier.")
                raise SystemExit(1)
    except FileNotFoundError:
        print("ERROR: /proc/device-tree/model not found — not a Raspberry Pi?")
        raise SystemExit(1)

    disp = Display()
    disp.start()
    print("SH5461AS demo (Pi 5/lgpio) running — Press Ctrl+C to stop.\n")

    try:
        print("Segment test: 8888")
        demo_message(disp, "8888", 1.5)

        print("Counting 0–99…")
        demo_count(disp, end=99, delay=0.04)

        print("Float values…")
        demo_float(disp)

        print("Message: 'HELP'")
        demo_message(disp, "HELP", 1.5)

        print("Flash: 'Err '")
        demo_flash(disp, "Err ")

        print("Dashes…")
        demo_message(disp, "----", 1.5)

        disp.show("done")
        time.sleep(1.0)

    except KeyboardInterrupt:
        print("\nStopped by user.")
    finally:
        disp.stop()
        print("GPIO released.")


if __name__ == "__main__":
    main()
