# One-Line Install & Startup Scripts

Scripts to simplify setup and daily operation, targeting `scripts/` directory.

## `scripts/install.sh`
- **Status**: Not started
- Detect OS (Debian/Ubuntu/macOS)
- Install Python 3.10+ if missing
- `pip install -e ".[all]"`
- Pull default Ollama model (`ollama pull llama3.2:1b`)
- Copy `config.example.yaml` to `config.yaml` if not present
- Print next-steps summary

## `scripts/start.sh`
- **Status**: Not started
- Check and start Ollama (`ollama serve` in background if not running)
- Check and start MQTT broker (`mosquitto -d` if not running)
- Launch `smollama run`

## `scripts/setup-pi.sh`
- **Status**: Not started
- Enable GPIO via `raspi-config` non-interactive
- Install system deps (`libgpiod2`, `mosquitto`)
- `pip install -e ".[all,pi]"`
- Create and enable systemd service (`smollama.service`)
- Configure log rotation

## Follow-up
Once implemented, document these in the README Quick Start section.
