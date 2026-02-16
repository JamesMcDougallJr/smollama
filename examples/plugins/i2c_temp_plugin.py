"""Example I2C temperature sensor plugin for BME280/DHT22.

This plugin demonstrates how to create a custom sensor plugin for Smollama
that reads from I2C-based temperature and humidity sensors.

Installation:
    pip install smbus2

Configuration (config.yaml):
    plugins:
      custom:
        - name: i2c_temp
          enabled: true
          config:
            bus: 1              # I2C bus number (typically 1 on Raspberry Pi)
            address: 0x76       # I2C device address (0x76 or 0x77 for BME280)
            sensor_type: bme280 # or "dht22"
            poll_interval: 60   # Seconds between readings

Usage:
    smollama plugin install ./examples/plugins/i2c_temp_plugin.py
    # Add configuration to config.yaml
    smollama run
"""

import logging
from datetime import datetime
from typing import Any

from smollama.plugins.base import PluginMetadata, SensorPlugin
from smollama.readings.base import Reading

logger = logging.getLogger(__name__)


class I2CTemperatureSensor(SensorPlugin):
    """I2C temperature and humidity sensor plugin.

    Supports BME280 (temperature, humidity, pressure) and DHT22 (temperature, humidity)
    sensors via I2C interface.
    """

    def __init__(self, config: dict[str, Any] | None = None) -> None:
        """Initialize the I2C temperature sensor plugin.

        Args:
            config: Plugin configuration with bus, address, and sensor_type.
        """
        self._config = config or {}
        self._bus = None
        self._sensor_type = self._config.get("sensor_type", "bme280")

    @property
    def metadata(self) -> PluginMetadata:
        """Plugin metadata with dependencies."""
        return PluginMetadata(
            name="i2c_temp",
            version="1.0.0",
            author="Smollama Team",
            description="I2C temperature/humidity sensor (BME280, DHT22)",
            dependencies=["smbus2>=0.4.0"],
            plugin_type="sensor",
        )

    @property
    def source_type(self) -> str:
        """Return 'i2c_temp' as the source type."""
        return "i2c_temp"

    @property
    def config_schema(self) -> dict[str, Any]:
        """JSON Schema for configuration validation."""
        return {
            "type": "object",
            "properties": {
                "bus": {
                    "type": "integer",
                    "minimum": 0,
                    "maximum": 10,
                    "default": 1,
                    "description": "I2C bus number (typically 1 on Raspberry Pi)",
                },
                "address": {
                    "type": "integer",
                    "minimum": 0,
                    "maximum": 127,
                    "description": "I2C device address (e.g., 0x76 for BME280)",
                },
                "sensor_type": {
                    "type": "string",
                    "enum": ["bme280", "dht22"],
                    "default": "bme280",
                    "description": "Type of sensor",
                },
                "poll_interval": {
                    "type": "integer",
                    "minimum": 1,
                    "default": 60,
                    "description": "Seconds between readings",
                },
            },
            "required": ["bus", "address"],
        }

    def check_dependencies(self) -> tuple[bool, str | None]:
        """Check if smbus2 library is available.

        Returns:
            Tuple of (success, error_message).
        """
        try:
            import smbus2  # type: ignore[import-untyped]  # noqa: F401

            return (True, None)
        except ImportError:
            return (
                False,
                "smbus2 library not installed. Install with: pip install smbus2",
            )

    def setup(self) -> None:
        """Initialize the I2C sensor connection.

        Raises:
            ValueError: If configuration is missing required fields.
            RuntimeError: If sensor cannot be initialized.
        """
        import smbus2  # type: ignore[import-untyped]

        bus_num = self._config.get("bus", 1)
        address = self._config.get("address")

        if address is None:
            raise ValueError("I2C address not specified in config")

        logger.info(
            f"Initializing {self._sensor_type} sensor on bus {bus_num} "
            f"at address 0x{address:02X}"
        )

        try:
            self._bus = smbus2.SMBus(bus_num)

            # Verify device is present by attempting a read
            # (Actual sensor initialization would go here for real hardware)
            self._bus.read_byte(address)

            logger.info(f"Successfully initialized {self._sensor_type} sensor")

        except Exception as e:
            logger.error(f"Failed to initialize sensor: {e}")
            logger.info(
                f"Running in simulated mode (sensor at 0x{address:02X} not found)"
            )
            # Close bus if opened
            if self._bus is not None:
                try:
                    self._bus.close()
                except Exception:
                    pass
                self._bus = None

    def teardown(self) -> None:
        """Clean up I2C bus resources."""
        if self._bus is not None:
            try:
                self._bus.close()
                logger.debug("Closed I2C bus")
            except Exception as e:
                logger.error(f"Error closing I2C bus: {e}")
            finally:
                self._bus = None

    @property
    def available_sources(self) -> list[str]:
        """List available sensor readings based on sensor type.

        Returns:
            List of source IDs (e.g., ["temperature", "humidity"]).
        """
        if self._sensor_type == "bme280":
            return ["temperature", "humidity", "pressure"]
        elif self._sensor_type == "dht22":
            return ["temperature", "humidity"]
        return []

    async def read(self, source_id: str) -> Reading | None:
        """Read a specific sensor value.

        Args:
            source_id: The reading to fetch ("temperature", "humidity", "pressure").

        Returns:
            Reading object with sensor data, or None if unavailable.
        """
        if source_id not in self.available_sources:
            return None

        try:
            value = self._read_sensor_value(source_id)

            # Determine unit based on reading type
            units = {
                "temperature": "celsius",
                "humidity": "percent",
                "pressure": "hpa",
            }

            return Reading(
                source_type=self.source_type,
                source_id=source_id,
                value=value,
                timestamp=datetime.now(),
                unit=units.get(source_id),
                metadata={
                    "sensor_type": self._sensor_type,
                    "address": f"0x{self._config.get('address', 0):02X}",
                },
            )

        except Exception as e:
            logger.error(f"Failed to read {source_id}: {e}")
            return None

    async def read_all(self) -> list[Reading]:
        """Read all available sensor values.

        Returns:
            List of Reading objects for all sensor sources.
        """
        readings = []
        now = datetime.now()

        for source_id in self.available_sources:
            try:
                value = self._read_sensor_value(source_id)

                units = {
                    "temperature": "celsius",
                    "humidity": "percent",
                    "pressure": "hpa",
                }

                readings.append(
                    Reading(
                        source_type=self.source_type,
                        source_id=source_id,
                        value=value,
                        timestamp=now,
                        unit=units.get(source_id),
                        metadata={
                            "sensor_type": self._sensor_type,
                            "address": f"0x{self._config.get('address', 0):02X}",
                        },
                    )
                )

            except Exception as e:
                logger.error(f"Failed to read {source_id}: {e}")

        return readings

    def _read_sensor_value(self, source_id: str) -> float:
        """Read raw value from sensor.

        This is a simplified implementation that returns simulated data
        when hardware is not available. Replace with actual sensor reading
        code for production use.

        Args:
            source_id: The reading type to fetch.

        Returns:
            Sensor value as a float.

        Raises:
            RuntimeError: If sensor read fails.
        """
        if self._bus is None:
            # Simulated mode - return mock data
            logger.debug(f"Simulated read of {source_id}")
            if source_id == "temperature":
                return 22.5  # Mock temperature
            elif source_id == "humidity":
                return 45.0  # Mock humidity
            elif source_id == "pressure":
                return 1013.25  # Mock pressure
            return 0.0

        # Real hardware reading would go here
        # Example for BME280:
        # if self._sensor_type == "bme280":
        #     if source_id == "temperature":
        #         # Read temperature registers and convert
        #         raw = self._read_bme280_temp()
        #         return self._compensate_temperature(raw)
        #     elif source_id == "humidity":
        #         raw = self._read_bme280_humidity()
        #         return self._compensate_humidity(raw)
        #     elif source_id == "pressure":
        #         raw = self._read_bme280_pressure()
        #         return self._compensate_pressure(raw)

        # For now, return simulated data
        logger.warning(
            f"Hardware reading not implemented, returning simulated {source_id}"
        )
        if source_id == "temperature":
            return 22.5
        elif source_id == "humidity":
            return 45.0
        elif source_id == "pressure":
            return 1013.25
        return 0.0

    # Helper methods for actual BME280 sensor (commented out for reference)
    # def _read_bme280_temp(self) -> int:
    #     """Read raw temperature from BME280."""
    #     # Read temperature registers (0xFA, 0xFB, 0xFC)
    #     data = self._bus.read_i2c_block_data(self._config['address'], 0xFA, 3)
    #     return (data[0] << 12) | (data[1] << 4) | (data[2] >> 4)
    #
    # def _compensate_temperature(self, raw: int) -> float:
    #     """Convert raw temperature to Celsius using calibration data."""
    #     # BME280 compensation formula here
    #     pass
