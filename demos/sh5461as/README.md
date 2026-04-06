# SH5461AS — 4-Digit 7-Segment LED Display

Standalone demo for the SH5461AS common-anode 4-digit 7-segment display on Raspberry Pi.

---

## What It Is

The SH5461AS is a 0.56-inch, 4-digit 7-segment LED display in a 12-pin DIP package.

| Spec | Value |
|------|-------|
| Type | Common anode |
| Digits | 4 |
| Segments | 7 + decimal point (8 total) |
| Package | 12-pin DIP |
| Forward voltage | ~2V per segment |
| Max segment current | 20 mA |

**Common anode** means the + (anode) of every LED in a digit group is tied together.
To light a segment: pull its cathode pin LOW. To select a digit: pull its anode pin HIGH.

Because all four digits share the same 8 segment wires, you must **multiplex** — rapidly
cycling through each digit in turn so the eye perceives all four as simultaneously lit.

---

## How the 12 Pins Map

```
Pin 1  → Seg e
Pin 2  → Seg d
Pin 3  → Seg dp  (decimal point)
Pin 4  → Seg c
Pin 5  → Seg g
Pin 6  → D4      (digit 4, rightmost — common anode)
Pin 7  → Seg b
Pin 8  → D3      (digit 3)
Pin 9  → D2      (digit 2)
Pin 10 → Seg f
Pin 11 → Seg a
Pin 12 → D1      (digit 1, leftmost — common anode)
```

---

## Wiring to Raspberry Pi

```
SH5461AS        Resistor    Raspberry Pi
─────────────────────────────────────────────────────────────────
Pin 12  D1  ─────────────── BCM 17  (Pin 11)   ← digit select, direct
Pin  9  D2  ─────────────── BCM 27  (Pin 13)   ← digit select, direct
Pin  8  D3  ─────────────── BCM 22  (Pin 15)   ← digit select, direct
Pin  6  D4  ─────────────── BCM  5  (Pin 29)   ← digit select, direct
Pin 11  a   ──── 220Ω ───── BCM  6  (Pin 31)
Pin  7  b   ──── 220Ω ───── BCM 13  (Pin 33)
Pin  4  c   ──── 220Ω ───── BCM 19  (Pin 35)
Pin  2  d   ──── 220Ω ───── BCM 26  (Pin 37)
Pin  1  e   ──── 220Ω ───── BCM 21  (Pin 40)
Pin 10  f   ──── 220Ω ───── BCM 20  (Pin 38)
Pin  5  g   ──── 220Ω ───── BCM 16  (Pin 36)
Pin  3  dp  ──── 220Ω ───── BCM 12  (Pin 32)
```

**Important rules:**
- Digit select pins (D1–D4) connect **directly** to GPIO — no resistors needed.
- Every segment pin (a–g, dp) needs its own **220Ω** current-limiting resistor.
- Using 3.3V GPIO with 220Ω gives roughly 6 mA per segment — safe and bright enough.
- Do not exceed 8 segments on simultaneously per digit or you risk exceeding the Pi's
  total GPIO current budget. With multiplexing only one digit is on at a time, so
  a maximum of 8 × 6 mA = 48 mA per cycle — well within limits.

### Breadboard Layout

```
Pi 3.3V is NOT used here. All power comes from the Pi's GPIO source via the segments.

   Pi Pin 11 (BCM 17) ──────────────────────────── D1
   Pi Pin 13 (BCM 27) ──────────────────────────── D2
   Pi Pin 15 (BCM 22) ──────────────────────────── D3
   Pi Pin 29 (BCM  5) ──────────────────────────── D4

   Pi Pin 31 (BCM  6) ──[220Ω]── Seg a
   Pi Pin 33 (BCM 13) ──[220Ω]── Seg b
   Pi Pin 35 (BCM 19) ──[220Ω]── Seg c
   Pi Pin 37 (BCM 26) ──[220Ω]── Seg d
   Pi Pin 40 (BCM 21) ──[220Ω]── Seg e
   Pi Pin 38 (BCM 20) ──[220Ω]── Seg f
   Pi Pin 36 (BCM 16) ──[220Ω]── Seg g
   Pi Pin 32 (BCM 12) ──[220Ω]── Seg dp

   Pi GND (Pin 6 or 9 or 14…) ──── GND rail (not connected to display directly;
                                    segments are pulled to GND through GPIO LOW)
```

---

## Segment Layout Reference

```
 ─ a ─
|     |
f     b
|     |
 ─ g ─
|     |
e     c
|     |
 ─ d ─  · dp
```

---

## Running the Demo

```bash
cd demos/sh5461as
uv run demo.py
```

The demo cycles through:
1. All-eights test (`8888`) — verify every segment works
2. Count 0–99
3. Float values (`3.14`, `2.72`, etc.)
4. Message sequences (`HELP`, `Err `, `----`)

Press **Ctrl+C** to stop. GPIO is cleaned up automatically.

---

## Smollama Plugin

When running smollama with the `sh5461as` plugin enabled, the agent gains a
`display_value` tool. Enable it in `config.yaml`:

```yaml
plugins:
  builtin:
    sh5461as:
      enabled: true
      config:
        digit_pins: [17, 27, 22, 5]
        segment_pins: {a: 6, b: 13, c: 19, d: 26, e: 21, f: 20, g: 16, dp: 12}
```

The LLM can then call `display_value` to show sensor readings, counts, or status
messages on the physical display. Example prompt result:

> "The distance sensor reads 42 cm — I'll display that."
> → Tool call: `display_value("42.0")`
> → Display shows: `42.0`

---

## Troubleshooting

| Symptom | Likely cause | Fix |
|---------|-------------|-----|
| Display is very dim | Resistors too high | Try 100Ω instead of 220Ω |
| Display flickers | Mux delay too long | Reduce `time.sleep(0.001)` in `_mux_loop` |
| One digit never lights | Digit pin wiring | Check BCM pin for that digit |
| One segment always off | Segment pin wiring or resistor missing | Check continuity |
| All segments on wrong digit | D1–D4 pin order swapped | Verify `DIGIT_PINS` list order left→right |
| `RuntimeError: No access to /dev/mem` | Not running as root or gpio group | Add user to `gpio` group: `sudo usermod -aG gpio $USER` |
