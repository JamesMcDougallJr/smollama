#!/usr/bin/env python3
"""Standalone demo for the QAPASS 1602A LCD display via I2C backpack.

Cycles through hostname/IP, CPU temperature, and a scrolling banner.
Press Ctrl-C to stop and clear the display.

Usage:
    uv run demo.py [--address 0x27] [--port 1]
"""

import argparse
import signal
import socket
import subprocess
import sys
import time


def get_ip() -> str:
    """Get the primary IP address."""
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("8.8.8.8", 80))
        ip = s.getsockname()[0]
        s.close()
        return ip
    except OSError:
        return "no network"


def get_cpu_temp() -> str:
    """Read CPU temperature from /sys or vcgencmd."""
    try:
        with open("/sys/class/thermal/thermal_zone0/temp") as f:
            temp_c = int(f.read().strip()) / 1000.0
            return f"{temp_c:.1f}C"
    except (FileNotFoundError, ValueError):
        pass
    try:
        out = subprocess.check_output(["vcgencmd", "measure_temp"], text=True)
        # output: "temp=48.7'C"
        return out.strip().split("=")[1].replace("'C", "C")
    except (FileNotFoundError, subprocess.CalledProcessError):
        return "N/A"


def main() -> None:
    parser = argparse.ArgumentParser(description="LCD 1602A demo")
    parser.add_argument(
        "--address",
        default="0x27",
        help="I2C address (default: 0x27, also try 0x3F)",
    )
    parser.add_argument(
        "--port",
        type=int,
        default=1,
        help="I2C bus number (default: 1)",
    )
    args = parser.parse_args()

    address = int(args.address, 16) if args.address.startswith("0x") else int(args.address)

    try:
        from RPLCD.i2c import CharLCD
    except ImportError:
        print("RPLCD not installed. Run: pip install 'RPLCD[i2c]'")
        sys.exit(1)

    print(f"Connecting to LCD at I2C address 0x{address:02X} on bus {args.port}...")

    try:
        lcd = CharLCD("PCF8574", address, port=args.port, cols=16, rows=2, backlight_enabled=True)
    except Exception as e:
        print(f"Failed to open LCD: {e}")
        print("Check that I2C is enabled (raspi-config) and run 'i2cdetect -y 1' to verify address.")
        sys.exit(1)

    print("LCD connected. Press Ctrl-C to stop.")

    # Clean shutdown on SIGINT
    def _stop(sig, frame):  # type: ignore[no-untyped-def]
        lcd.clear()
        lcd.close(clear=True)
        print("\nDisplay cleared. Goodbye.")
        sys.exit(0)

    signal.signal(signal.SIGINT, _stop)

    hostname = socket.gethostname()[:16]
    screens = [
        # (line1, line2_callable_or_str)
        (hostname, get_ip),
        ("CPU Temp:", get_cpu_temp),
        ("smollama", "ready"),
    ]

    screen_index = 0
    while True:
        line1, line2_src = screens[screen_index % len(screens)]
        line2 = line2_src() if callable(line2_src) else line2_src

        lcd.clear()
        lcd.cursor_pos = (0, 0)
        lcd.write_string(line1[:16])
        lcd.cursor_pos = (1, 0)
        lcd.write_string(str(line2)[:16])

        print(f"[{line1}] [{line2}]")
        screen_index += 1
        time.sleep(2)


if __name__ == "__main__":
    main()
