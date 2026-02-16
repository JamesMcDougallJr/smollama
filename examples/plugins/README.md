# Example Plugins

This directory contains example plugins demonstrating how to extend Smollama with custom sensors and tools.

## Available Examples

### i2c_temp_plugin.py

**Type:** Sensor Plugin
**Purpose:** Read temperature and humidity from I2C sensors (BME280, DHT22)

**Features:**
- Supports BME280 (temperature, humidity, pressure)
- Supports DHT22 (temperature, humidity)
- Graceful fallback to simulated mode when hardware unavailable
- Comprehensive configuration validation
- Production-ready error handling and logging

**Installation:**
```bash
# Install the plugin
smollama plugin install ./examples/plugins/i2c_temp_plugin.py

# Install dependencies
pip install smbus2
```

**Configuration:**
```yaml
plugins:
  custom:
    - name: i2c_temp
      enabled: true
      config:
        bus: 1              # I2C bus number
        address: 0x76       # Device address (0x76 or 0x77 for BME280)
        sensor_type: bme280 # or "dht22"
        poll_interval: 60   # Seconds between readings
```

**Testing:**
```bash
# List plugins to verify installation
smollama plugin list

# Run Smollama
smollama run
```

## Creating Your Own Plugin

See the [Plugin Development Guide](../../docs/plugin-development.md) for detailed instructions on creating custom plugins.

### Quick Start

1. Copy an example plugin as a template
2. Modify the metadata (name, version, description)
3. Update the config schema for your needs
4. Implement the reading/tool logic
5. Test with `smollama plugin list` and `smollama run`

### Plugin Structure

```
my_plugin/
├── __init__.py           # Optional
├── my_plugin.py          # Main plugin code
├── requirements.txt      # Dependencies
└── README.md             # Documentation
```

## Contributing Examples

Have a useful plugin example? Submit a pull request!

Good examples:
- Demonstrate a common use case
- Include comprehensive comments
- Handle errors gracefully
- Work in simulated mode when hardware unavailable
- Include configuration examples
