#!/usr/bin/env python3
"""
5161AS Single-Digit 7-Segment LED Display Demo — Raspberry Pi 5 (lgpio)

Wiring (BCM pin numbers, same as Pi 4 demo):
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

Run with: uv run --group pi5 demo_pi5.py
For Pi 4 and earlier, use demo.py (RPi.GPIO) instead.
"""

import time

import lgpio

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
    """Drives the 5161AS directly via lgpio — no multiplexing for a single digit."""

    def __init__(self) -> None:
        self._h = lgpio.gpiochip_open(0)
        for pin in SEG_PINS:
            lgpio.gpio_claim_output(self._h, pin, 0)  # 0 = LOW = segment off (active-high)

    def show(self, text: str) -> None:
        """Display one character, optionally with decimal point.

        Pass a single character, optionally followed by '.' for the decimal
        point (e.g. "8" or "3." or "H").
        """
        seg_byte, dp = _parse(text)
        for bit in range(7):  # segments a–g
            on = bool(seg_byte & (1 << bit))
            lgpio.gpio_write(self._h, SEG_PINS[bit], 1 if on else 0)
        lgpio.gpio_write(self._h, SEG_PINS[7], 1 if dp else 0)

    def blank(self) -> None:
        for pin in SEG_PINS:
            lgpio.gpio_write(self._h, pin, 0)

    def close(self) -> None:
        self.blank()
        lgpio.gpiochip_close(self._h)


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
    print("5161AS demo (Pi 5/lgpio) running — Press Ctrl+C to stop.\n")

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
        print("GPIO released.")


if __name__ == "__main__":
    main()
