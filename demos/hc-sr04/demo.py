#!/usr/bin/env python3
"""
HC-SR04 Ultrasonic Distance Sensor Demo — Pi 5 compatible

Wiring:
  HC-SR04 VCC  → Pi Pin 2  (5V)
  HC-SR04 GND  → Pi Pin 6  (GND)
  HC-SR04 Trig → Pi Pin 16 (BCM 23) — direct, no resistors
  HC-SR04 Echo → Pi Pin 18 (BCM 24) — via voltage divider (1kΩ + 2kΩ)

Run with: uv run demo.py
"""
import warnings
from gpiozero import DistanceSensor
from gpiozero.pins.lgpio import LGPIOFactory
from gpiozero.exc import PWMSoftwareFallback
from time import sleep

TRIG_PIN = 23
ECHO_PIN = 24

# Expected on Pi 5 — pigpio not available, lgpio used instead. Not an error.
warnings.filterwarnings("ignore", category=PWMSoftwareFallback)

factory = LGPIOFactory(chip=0)

with DistanceSensor(echo=ECHO_PIN, trigger=TRIG_PIN,
                    max_distance=4.0, partial=True,
                    pin_factory=factory) as sensor:
    print(f"HC-SR04 — Trig=BCM{TRIG_PIN}, Echo=BCM{ECHO_PIN}")
    print("Press Ctrl+C to stop.\n")
    try:
        while True:
            d = sensor.distance * 100
            print(f"Distance: {d:6.1f} cm  ({d / 2.54:5.1f} in)")
            sleep(0.5)
    except KeyboardInterrupt:
        print("\nStopped.")
