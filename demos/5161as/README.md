# 5161AS — Single-Digit 7-Segment LED Display (Common Cathode)

Standalone demo for the 5161AS common-anode single-digit 7-segment display on Raspberry Pi.

---

## What It Is

The 5161AS is a 0.56-inch, single-digit 7-segment LED display in a 10-pin DIP package.

| Spec | Value |
|------|-------|
| Type | Common cathode |
| Digits | 1 |
| Segments | 7 + decimal point (8 total) |
| Package | 10-pin DIP |
| Forward voltage | ~2V per segment |
| Max segment current | 20 mA |

**Common cathode** means the − (cathode) of every LED is tied together at the common pin.
To light a segment: drive its anode pin HIGH. The common cathode is held LOW (connected to GND).

Unlike the 4-digit SH5461AS, there is no multiplexing — all segment pins drive the single digit
directly and stay on until changed. No background thread is needed.

---

## How the 10 Pins Map

```
Pin 1  → Seg e
Pin 2  → Seg d
Pin 3  → Common cathode  (GND)   ← middle pin, left side
Pin 4  → Seg c
Pin 5  → Seg dp  (decimal point)
Pin 6  → Seg b
Pin 7  → Seg a
Pin 8  → Common cathode  (GND)   ← middle pin, right side
Pin 9  → Seg f
Pin 10 → Seg g
```

> Note: pin numbering can vary between manufacturers. Verify by connecting Pin 3/8 to GND
> and driving Pin 7 HIGH through a 220Ω resistor — segment `a` (top bar) should light.

---

## Wiring to Raspberry Pi

```
Raspberry Pi         Resistor    5161AS
────────────────────────────────────────────────────────────────
Pi Pin  7 (BCM  4)  ──[220Ω]──  Pin 10  g   (middle bar)
Pi Pin  9 (GND)     ────────────  Pin  8  COM  ← no resistor
Pi Pin 11 (BCM 17)  ──[220Ω]──  Pin  9  f   (upper left)
Pi Pin 13 (BCM 27)  ──[220Ω]──  Pin  7  a   (top bar)
Pi Pin 15 (BCM 22)  ──[220Ω]──  Pin  6  b   (upper right)
Pi Pin 19 (BCM 10)  ──[220Ω]──  Pin  1  e   (lower left)
Pi Pin 21 (BCM  9)  ──[220Ω]──  Pin  5  dp  (decimal point)
Pi Pin 23 (BCM 11)  ──[220Ω]──  Pin  4  c   (lower right)
Pi Pin 24 (BCM  8)  ──[220Ω]──  Pin  2  d   (bottom bar)
Pi Pin 25 (GND)     ────────────  Pin  3  COM  ← no resistor
```

**Important rules:**
- The common cathode connects directly to GND — **no resistor**.
- Every segment anode pin (a–g, dp) needs its own **220Ω** current-limiting resistor.
- Using 3.3V GPIO with 220Ω gives roughly 6 mA per segment — safe and bright enough.
- If you want software control of the display power, wire the common cathode to a GPIO pin
  instead of GND, and set `common_pin` in the plugin config.

### Optional: GPIO-controlled common cathode

If you need to turn the display on/off in software (e.g. for power saving):

```
5161AS          Raspberry Pi
──────────────────────────────────
Pin 3/8   COM  ── BCM 24 (Pin 18) ← driven LOW to enable, HIGH to blank
```

Then set `common_pin: 24` in `config.yaml` (see Plugin section below).

### Breadboard Layout

```
   Pi Pin  7 (BCM  4)  ──[220Ω]──── Pin 10  g   ↑ top of component, left
   Pi Pin  9 (GND)     ────────────── Pin  8  COM
   Pi Pin 11 (BCM 17)  ──[220Ω]──── Pin  9  f
   Pi Pin 13 (BCM 27)  ──[220Ω]──── Pin  7  a
   Pi Pin 15 (BCM 22)  ──[220Ω]──── Pin  6  b   ↑ top of component, right
   Pi Pin 19 (BCM 10)  ──[220Ω]──── Pin  1  e   ↓ bottom of component, left
   Pi Pin 21 (BCM  9)  ──[220Ω]──── Pin  5  dp
   Pi Pin 23 (BCM 11)  ──[220Ω]──── Pin  4  c
   Pi Pin 24 (BCM  8)  ──[220Ω]──── Pin  2  d
   Pi Pin 25 (GND)     ────────────── Pin  3  COM ↓ bottom of component, right
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

**Pi 4 and earlier (RPi.GPIO):**
```bash
cd demos/5161as
uv run demo.py
```

**Pi 5 (lgpio):**
```bash
cd demos/5161as
uv run --group pi5 demo_pi5.py
```

The demo cycles through:
1. All-segments test (`8.`) — verify every segment and decimal point works
2. Count 0–9
3. Digits with decimal point (`3.`, `1.`, `0.`, `9.`)
4. Letters: H, E, L, P, n, r
5. Flash `-` on/off

Press **Ctrl+C** to stop. GPIO is cleaned up automatically.

---

## Smollama Plugin

When running smollama with the `s5161as` plugin enabled, the agent gains a
`display_digit` tool. Enable it in `config.yaml`:

```yaml
plugins:
  builtin:
    s5161as:
      enabled: true
      config:
        segment_pins: {a: 27, b: 22, c: 11, d: 8, e: 10, f: 17, g: 4, dp: 9}
        common_pin: null  # or a BCM pin number if common cathode is GPIO-controlled
```

The LLM can then call `display_digit` to show a single character or digit on the
physical display. Example prompt result:

> "The temperature is 7 degrees — I'll show that."
> → Tool call: `display_digit("7")`
> → Display shows: `7`

> "The sensor reads 3.5 — I'll display the integer part with decimal point."
> → Tool call: `display_digit("3.")`
> → Display shows: `3.`

The `display_digit` tool and the SH5461AS `display_value` tool can coexist
as long as the two displays use non-overlapping GPIO pins.

---

## Troubleshooting

| Symptom | Likely cause | Fix |
|---------|-------------|-----|
| Display is very dim | Resistors too high | Try 100Ω instead of 220Ω |
| Nothing lights up | Common cathode not grounded | Check GND connection to Pin 3 and Pin 8 |
| Wrong segment lights | Segment pin mapping off | Check your datasheet — verify by driving Pin 7 HIGH, segment `a` (top bar) should light |
| Decimal point won't light | dp pin wiring | Verify BCM 25 → 220Ω → Pin 3 |
| `RuntimeError: No access to /dev/mem` | Not in gpio group | `sudo usermod -aG gpio $USER`, then re-login |
| `lgpio.error: 'GPIO busy'` | Another process holds the pins | Stop the other process first |
