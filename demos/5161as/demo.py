#!/usr/bin/env python3
"""
5161AS Single-Digit 7-Segment LED Display Demo — Raspberry Pi (RPi.GPIO)

Wiring (BCM pin numbers):
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

Run with: uv run demo.py
For Pi 5, use demo_pi5.py (lgpio) instead.
"""

import time

import RPi.GPIO as GPIO

# ── Pin configuration ─────────────────────────────────────────────────────────

# Segment order: a   b   c   d   e   f   g   dp
SEG_PINS = [27, 22, 11, 8, 10, 17, 4, 9]

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
    """Drives the 5161AS directly — no multiplexing needed for a single digit."""

    def __init__(self) -> None:
        GPIO.setmode(GPIO.BCM)
        GPIO.setwarnings(False)
        for pin in SEG_PINS:
            GPIO.setup(pin, GPIO.OUT, initial=GPIO.LOW)  # LOW = segment off (active-high)

    def show(self, text: str) -> None:
        """Display one character, optionally with decimal point.

        Pass a single character, optionally followed by '.' for the decimal
        point (e.g. "8" or "3." or "H").
        """
        seg_byte, dp = _parse(text)
        for bit in range(7):  # segments a–g
            on = bool(seg_byte & (1 << bit))
            GPIO.output(SEG_PINS[bit], GPIO.HIGH if on else GPIO.LOW)
        GPIO.output(SEG_PINS[7], GPIO.HIGH if dp else GPIO.LOW)

    def blank(self) -> None:
        GPIO.output(SEG_PINS, GPIO.LOW)

    def close(self) -> None:
        self.blank()
        GPIO.cleanup(SEG_PINS)


def _parse(text: str) -> tuple[int, bool]:
    """Convert a 1-char (+ optional '.') string to (segment_byte, show_dp)."""
    text = text.strip()
    if not text:
        return (SEGMENTS[" "], False)
    ch = text[0]
    dp = len(text) > 1 and text[1] == "."
    return (SEGMENTS.get(ch.upper(), SEGMENTS[" "]), dp)


# ── Demo sequences ────────────────────────────────────────────────────────────

def demo_all_segments(disp: Display, duration: float = 1.5) -> None:
    """Show '8.' to verify every segment and the decimal point."""
    print("All segments: 8.")
    disp.show("8.")
    time.sleep(duration)


def demo_count(disp: Display, delay: float = 0.3) -> None:
    """Cycle through digits 0–9."""
    print("Counting 0–9…")
    for n in range(10):
        disp.show(str(n))
        time.sleep(delay)


def demo_letters(disp: Display, delay: float = 0.5) -> None:
    """Show each supported letter."""
    letters = ["H", "E", "L", "P", "n", "r"]
    print(f"Letters: {' '.join(letters)}")
    for ch in letters:
        disp.show(ch)
        time.sleep(delay)


def demo_decimal(disp: Display, delay: float = 0.6) -> None:
    """Show digits with decimal point."""
    values = ["3.", "1.", "0.", "9."]
    print(f"With decimal: {' '.join(values)}")
    for v in values:
        disp.show(v)
        time.sleep(delay)


def demo_flash(disp: Display, char: str, times: int = 4) -> None:
    print(f"Flash: {char!r}")
    for _ in range(times):
        disp.show(char)
        time.sleep(0.3)
        disp.blank()
        time.sleep(0.2)


# ── Entry point ───────────────────────────────────────────────────────────────

def main() -> None:
    # Verify we are not on Pi 5 — RPi.GPIO does not support the RP1 I/O chip
    try:
        with open("/proc/device-tree/model") as f:
            if "Raspberry Pi 5" in f.read():
                print("ERROR: This demo does not work on Raspberry Pi 5.")
                print("Use demo_pi5.py (lgpio) instead: uv run --group pi5 demo_pi5.py")
                raise SystemExit(1)
    except FileNotFoundError:
        pass  # not a Pi at all — let RPi.GPIO handle it

    disp = Display()
    print("5161AS demo running — Press Ctrl+C to stop.\n")

    try:
        demo_all_segments(disp)
        demo_count(disp)
        demo_decimal(disp)
        demo_letters(disp)
        demo_flash(disp, "-")

        print("Dash…")
        disp.show("-")
        time.sleep(1.0)

        disp.blank()
        time.sleep(0.5)

    except KeyboardInterrupt:
        print("\nStopped by user.")
    finally:
        disp.close()
        print("GPIO cleaned up.")


if __name__ == "__main__":
    main()
