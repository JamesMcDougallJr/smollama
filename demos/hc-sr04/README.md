# HC-SR04 Ultrasonic Distance Sensor — Raspberry Pi 5 Demo

<p align="center">
  <img src="https://cdn.sparkfun.com/assets/parts/1/1/6/7/13959-01.jpg" width="400" alt="HC-SR04 Ultrasonic Distance Sensor"/>
</p>

A minimal, self-contained demo that reads distance from an HC-SR04 ultrasonic sensor connected to a Raspberry Pi 5, printing measurements in centimetres and inches every 500 ms.

Uses **gpiozero** + **lgpio** — the correct stack for Pi 5, where the older `pigpio` backend is unavailable.

---

## The Sensor

The **HC-SR04** is a low-cost ultrasonic ranging module that measures distance by timing a reflected sound pulse. It is one of the most widely used sensors in hobbyist electronics.

| Parameter | Value |
|---|---|
| Operating voltage | 5 V DC |
| Quiescent current | < 2 mA |
| Operating current | < 15 mA |
| Ultrasonic frequency | 40 kHz |
| Trigger input | 10 µs TTL HIGH pulse |
| Measurement range | 2 cm – 400 cm |
| Accuracy | ±3 mm |
| Effective beam angle | < 15° |
| PCB dimensions | 45 × 20 × 15 mm |
| Pins | VCC, TRIG, ECHO, GND |

**[Datasheet (PDF)](https://cdn.sparkfun.com/datasheets/Sensors/Proximity/HCSR04.pdf)** — ELECFreaks / SparkFun

---

## How It Works

The sensor measures distance using the time-of-flight of an ultrasonic pulse:

```
TRIG  _____|‾‾ 10µs ‾‾|__________________________________________________
                      ↓  fires 8 × 40 kHz ultrasonic pulses

ECHO  ________________|‾‾‾‾‾‾‾‾‾‾‾‾‾‾‾‾‾‾‾‾‾|___________________________
                       ←——— HIGH duration ———→
                         proportional to distance
```

1. Your code drives **TRIG HIGH for at least 10 µs**, then LOW.
2. The sensor fires a burst of **8 × 40 kHz pulses** from the transmitter (the left cylinder).
3. **ECHO goes HIGH** immediately after the burst is sent.
4. The receiver (right cylinder) listens for the reflected echo.
5. When the echo arrives, **ECHO goes LOW**.
6. Measure the HIGH duration on ECHO — that is the round-trip travel time.

### Distance Formula

Sound travels at approximately 343 m/s (0.0343 cm/µs) at 20 °C. Because the pulse travels *to* the target and *back*, divide by two:

```
Distance (cm) = Echo_duration_µs × 0.0343 / 2
              = Echo_duration_µs / 58.2
```

**Example:** ECHO stays HIGH for 1160 µs → 1160 / 58.2 ≈ **19.9 cm**

gpiozero handles all of this timing internally and exposes a simple `sensor.distance` property in metres.

---

## What You'll Need

| Item | Notes |
|---|---|
| Raspberry Pi (any model) | Tested on Pi 5; works on Pi 4, Pi 3, Pi Zero 2 W |
| HC-SR04 module | ~$1–3, widely available |
| Breadboard | Half-size or larger |
| 1 × 1 kΩ resistor | For Echo voltage divider |
| 1 × 2 kΩ resistor | For Echo voltage divider (or 2 × 1 kΩ in series) |
| 4 × jumper wires (F-M) | Female end to sensor pins, male to Pi header |

---

## Wiring

### Why a Voltage Divider on Echo?

The HC-SR04 runs on **5 V**, so its **ECHO pin outputs a 5 V signal**. The Raspberry Pi's GPIO pins are **3.3 V only** — applying 5 V will damage the GPIO or the SoC.

- **TRIG** is an *input* to the sensor. The Pi's 3.3 V HIGH is recognised as a valid TTL HIGH at 5 V logic — no divider needed.
- **ECHO** is an *output* from the sensor at 5 V — this needs to be stepped down before it reaches the Pi.

A simple resistor divider (1 kΩ + 2 kΩ) brings 5 V down to a safe 3.33 V:

```
Vout = 5V × R2 / (R1 + R2) = 5V × 2000 / 3000 = 3.33 V  ✓
```

### Circuit Diagram

```
HC-SR04                               Raspberry Pi 5
───────                               ────────────────
  VCC  ──────────────────────────────  Pin 2  │ 5V
  GND  ──────────────────────────────  Pin 6  │ GND
  TRIG ──────────────────────────────  Pin 16 │ BCM 23
                                              │
  ECHO ────── R1 (1 kΩ) ────┬──────── Pin 18 │ BCM 24
                             │
                          R2 (2 kΩ)
                             │
                            GND (any ground pin)
```

The junction between R1 and R2 is what connects to BCM 24 — it sits at ~3.33 V when ECHO is HIGH.

### Pi GPIO Pin Reference

| HC-SR04 Pin | Pi Physical Pin | BCM | Notes |
|---|---|---|---|
| VCC | Pin 2 | — | 5 V power |
| GND | Pin 6 | — | Ground |
| TRIG | Pin 16 | BCM 23 | Direct connection |
| ECHO | Pin 18 | BCM 24 | Via 1 kΩ + 2 kΩ voltage divider |

Pi 5 pinout reference: [pinout.xyz](https://pinout.xyz)

### Breadboard Layout

```
                ┌─────────────────────────────┐
                │         BREADBOARD          │
                │                             │
 HC-SR04 VCC ───┤── (+) rail ─────────────────┤── Pi Pin 2  (5V)
 HC-SR04 GND ───┤── (-) rail ─────────────────┤── Pi Pin 6  (GND)
 HC-SR04 TRIG───┤─────────────────────────────┤── Pi Pin 16 (BCM23)
                │                             │
 HC-SR04 ECHO───┤──[1kΩ]──┬──────────────────┤── Pi Pin 18 (BCM24)
                │         │                  │
                │        [2kΩ]               │
                │         │                  │
                │── (-) rail ────────────────┘
                └─────────────────────────────┘
```

---

## Running the Demo

This demo is self-contained with its own `pyproject.toml` — no system-wide installs needed.

### Prerequisites

- [uv](https://docs.astral.sh/uv/getting-started/installation/) installed
- Raspberry Pi with the sensor wired as above

### Run

```bash
cd demos/hc-sr04
uv run demo.py
```

`uv` will create a venv and install `gpiozero` + `lgpio` automatically on first run.

### Expected Output

```
HC-SR04 — Trig=BCM23, Echo=BCM24
Press Ctrl+C to stop.

Distance:   19.4 cm  ( 7.6 in)
Distance:   19.3 cm  ( 7.6 in)
Distance:   34.7 cm  (13.7 in)
Distance:  102.1 cm  (40.2 in)
Distance:    2.8 cm  ( 1.1 in)

Stopped.
```

Move your hand in front of the sensor to see the distance change. Readings update every 500 ms.

---

## Troubleshooting

**`lgpio.error: 'GPIO busy'`**
Another process already has the GPIO pins claimed (e.g. a smollama agent or dashboard instance). Kill the other process first:
```bash
pkill -f smollama
```

**`DistanceSensorNoEcho: no echo received`**
Nothing is in range (> 4 m) or the sensor isn't getting a clean 5 V supply. Check your wiring, especially the VCC connection. The warning is harmless — `sensor.distance` will return `None`.

**Readings are wildly inconsistent**
- Confirm the voltage divider is on ECHO, not TRIG
- Check the resistor colour bands (brown-black-red = 1 kΩ, red-black-red = 2 kΩ)
- The sensor has a ±3 mm spec but needs objects to have some reflective surface area — small or angled objects give noisier readings

**`ModuleNotFoundError: No module named 'lgpio'`**
Run via `uv run demo.py`, not bare `python demo.py`. The venv is managed by uv.

**Works on Pi 4 but not Pi 5**
Make sure you're on `gpiozero >= 2.0` and `lgpio >= 0.2`. The demo's `pyproject.toml` pins these. The older `pigpio` backend doesn't work on Pi 5 — `LGPIOFactory` is the correct choice.

---

## Using as a Smollama Plugin

This demo is also available as a smollama sensor plugin. If you want distance readings to appear in the smollama dashboard and be included in the LLM observation loop, enable the plugin in your `config.yaml`:

```yaml
plugins:
  builtin:
    hcsr04:
      enabled: true
      config:
        trig_pin: 23
        echo_pin: 24
        max_distance: 4.0
        chip: 0            # lgpio chip number, 0 on all current Pi models
```

Then run the dashboard:

```bash
uv run smollama dashboard
```

Readings will appear at `http://localhost:8080/readings` as `hcsr04:distance` (value in cm).

---

## References

- [HC-SR04 Datasheet (PDF)](https://cdn.sparkfun.com/datasheets/Sensors/Proximity/HCSR04.pdf) — ELECFreaks / SparkFun
- [HC-SR04 on Raspberry Pi — ThePiHut](https://thepihut.com/blogs/raspberry-pi-tutorials/hc-sr04-ultrasonic-range-sensor-on-the-raspberry-pi)
- [Complete Guide for HC-SR04 — RandomNerdTutorials](https://randomnerdtutorials.com/complete-guide-for-ultrasonic-sensor-hc-sr04/)
- [gpiozero DistanceSensor docs](https://gpiozero.readthedocs.io/en/stable/api_input.html#gpiozero.DistanceSensor)
- [Raspberry Pi GPIO Pinout — pinout.xyz](https://pinout.xyz)
- [Components101 — HC-SR04 pinout & overview](https://components101.com/sensors/ultrasonic-sensor-working-pinout-datasheet)
