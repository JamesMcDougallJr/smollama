# QAPASS 1602A — 16×2 Character LCD Demo

A standalone demo that drives a QAPASS 1602A LCD display from a Raspberry Pi using an I2C backpack module. Cycles through hostname, IP address, and CPU temperature — no smollama required to run the demo.

---

## The Display

The 1602A is a 16×2 alphanumeric LCD (16 characters × 2 rows = 32 characters total) driven by the ubiquitous HD44780 controller. Nearly every hobbyist "LCD kit" uses this module.

| Property | Value |
|---|---|
| Display type | STN LCD, alphanumeric |
| Characters | 16 columns × 2 rows |
| Controller | HD44780 |
| Supply voltage | 5V DC |
| Backlight | Blue, white text |
| Dimensions | 80 × 36 × 11 mm |
| Raw interface | 16-pin parallel (4-bit or 8-bit mode) |

### I2C Backpack (PCF8574)

The raw LCD requires 11 GPIO pins in 4-bit mode — impractical on a Pi. An **I2C backpack module** (PCF8574 chip, ~£1) solders onto the back and converts the parallel interface to 2-wire I2C. Only 4 wires needed total (VCC, GND, SDA, SCL).

| Backpack chip | I2C address range | Default address |
|---|---|---|
| PCF8574 | 0x20 – 0x27 | **0x27** |
| PCF8574A | 0x38 – 0x3F | **0x3F** |

The address is set by three solder jumpers (A0, A1, A2) on the backpack. All open = highest address (0x27 or 0x3F).

---

## How It Works

1. The Pi sends I2C commands to the PCF8574
2. The PCF8574 drives the LCD's 8 data/control lines in **4-bit mode** (two nibbles per byte)
3. The HD44780 controller renders characters from its built-in CGROM character set
4. A trimmer potentiometer on the backpack sets the LCD contrast voltage (V0 pin)

The `RPLCD` Python library handles all the HD44780 timing and PCF8574 bit-packing for you.

---

## What You'll Need

| Qty | Component | Notes |
|---|---|---|
| 1 | QAPASS 1602A (or any HD44780 16×2 LCD) | |
| 1 | PCF8574 I2C backpack | Usually sold as "I2C LCD adapter module" |
| | **OR** | |
| 1 | 1602A with I2C backpack pre-soldered | Most convenient — sold as a unit |
| 4 | Female–female jumper wires | VCC, GND, SDA, SCL |

If you have a bare 1602A and a separate backpack module, solder the backpack onto the LCD's 16-pin header before wiring.

---

## Wiring

### Enable I2C on your Pi first

```bash
sudo raspi-config
# Interface Options → I2C → Enable → Finish
sudo reboot
```

### Connections

Only 4 wires. I2C pins on the Pi have built-in 1.8 kΩ pull-ups — no external resistors needed.

| LCD backpack pin | Pi header pin | BCM | Signal |
|---|---|---|---|
| VCC | Pin 2 | — | 5V supply |
| GND | Pin 6 | — | Ground |
| SDA | Pin 3 | BCM 2 | I2C data |
| SCL | Pin 5 | BCM 3 | I2C clock |

> **Use the 5V rail (Pin 2 or 4), not 3.3V.** The HD44780 controller does not reliably initialise at 3.3V. The PCF8574 I/O lines are 3.3V-tolerant, so Pi's 3.3V logic drives them correctly.

### Breadboard layout

```
Pi header                    I2C backpack module
─────────                    ───────────────────
Pin 2  (5V)  ──────────────► VCC
Pin 6  (GND) ──────────────► GND
Pin 3  (SDA) ──────────────► SDA
Pin 5  (SCL) ──────────────► SCL
```

### Verify the connection

After wiring:

```bash
i2cdetect -y 1
```

You should see `27` (or `3f`) appear in the grid:

```
     0  1  2  3  4  5  6  7  8  9  a  b  c  d  e  f
00:                         -- -- -- -- -- -- -- --
10: -- -- -- -- -- -- -- -- -- -- -- -- -- -- -- --
20: -- -- -- -- -- -- -- 27 -- -- -- -- -- -- -- --
```

If nothing appears, check wiring and confirm I2C is enabled.

### Contrast adjustment

Turn the **blue trimmer potentiometer** on the backpack with a small screwdriver until characters are clearly visible. Fully counter-clockwise usually shows solid blocks; slowly clockwise until crisp text appears.

---

## Running the Demo

```bash
cd demos/lcd-1602a
uv run demo.py
```

With a non-default I2C address:

```bash
uv run demo.py --address 0x3F
```

The display will cycle every 2 seconds through:

1. **Hostname** (top) + **IP address** (bottom)
2. `CPU Temp:` (top) + current temperature (bottom)
3. `smollama` (top) + `ready` (bottom)

Press **Ctrl-C** to stop — the display is cleared automatically on exit.

### Expected output (terminal)

```
Connecting to LCD at I2C address 0x27 on bus 1...
LCD connected. Press Ctrl-C to stop.
[pi-living-room] [192.168.1.42]
[CPU Temp:] [52.3C]
[smollama] [ready]
[pi-living-room] [192.168.1.42]
^C
Display cleared. Goodbye.
```

---

## Troubleshooting

| Symptom | Cause | Fix |
|---|---|---|
| `i2cdetect` shows no device | I2C not enabled, or wrong wiring | Run `raspi-config` → I2C → Enable; check all 4 wires |
| `OSError: [Errno 121] Remote I/O error` | Wrong I2C address | Run `i2cdetect -y 1` and use the detected address with `--address 0x3F` |
| Display on but all black blocks | Contrast too low | Turn trimmer pot clockwise until text appears |
| Display on but nothing visible | Contrast too high or backlight off | Turn trimmer counter-clockwise; check `backlight: true` in config |
| `ImportError: No module named RPLCD` | Dependency not installed | `pip install 'RPLCD[i2c]'` or `uv add RPLCD` |
| Random garbled characters on init | I2C signal noise | Use shorter jumper wires; check connections are secure |

---

## Using as a Smollama Plugin

Add to `config.yaml`:

```yaml
plugins:
  builtin:
    lcd1602:
      enabled: true
      config:
        i2c_address: 0x27   # change to 0x3F if needed
        i2c_port: 1
        cols: 16
        rows: 2
        backlight: true
```

The agent will have two new tools:

**`lcd_write`** — write text to both lines:
> "Display the current temperature on the LCD"
> → agent calls `lcd_write(line1="CPU Temp:", line2="52.3C")`

**`lcd_clear`** — clear the display:
> "Clear the LCD"
> → agent calls `lcd_clear()`

---

## References

- [QAPASS 1602A datasheet](https://componentsexplorer.com/lcd-qapass-1602a-datasheet)
- [RPLCD documentation](https://rplcd.readthedocs.io/)
- [PCF8574 datasheet](https://www.ti.com/lit/ds/symlink/pcf8574.pdf)
- [HD44780 controller datasheet](https://www.sparkfun.com/datasheets/LCD/HD44780.pdf)
