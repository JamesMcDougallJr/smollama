# One-Line Install & Startup Scripts

**Status**: ✅ Complete

Scripts to simplify setup and daily operation, targeting `scripts/` directory.

## `scripts/install.sh`
- **Status**: ✅ Complete
- ✅ Detect OS (Debian/Ubuntu/macOS with fallback for unsupported)
- ✅ Install Python 3.10+ if missing (via brew or apt)
- ✅ Install UV (fast package manager) with pip fallback
- ✅ Install packages via `uv sync` or `pip install -e ".[mode]"`
- ✅ Support for --minimal, --dev, --all modes
- ✅ Pull default Ollama model (`llama3.2:1b`) with retry logic
- ✅ Copy `config.example.yaml` to `config.yaml` if not present
- ✅ Optional Ollama and Mosquitto installation (interactive prompts)
- ✅ Installation validation and next-steps summary
- ✅ Non-interactive mode for CI/automation

## `scripts/start.sh`
- **Status**: ✅ Complete
- ✅ Check and start Ollama (systemd/brew/manual)
- ✅ Check and start MQTT broker (systemd/brew/manual)
- ✅ Wait for services to be ready with health checks
- ✅ Verify connectivity with `smollama status`
- ✅ Launch `smollama run` with argument pass-through
- ✅ Cleanup handler to stop only services started by script
- ✅ Proper signal handling (trap EXIT INT TERM)

## `scripts/setup-pi.sh`
- **Status**: ✅ Complete
- ✅ Raspberry Pi hardware detection
- ✅ Enable GPIO via `raspi-config` non-interactive (SPI, I2C)
- ✅ Install system deps (`libgpiod2`, `mosquitto`, build tools)
- ✅ Add user to gpio group
- ✅ Install UV and packages: `uv pip install --extra all --extra pi .`
- ✅ Production install (non-editable) for Pi deployment
- ✅ Create system-wide executable symlink (/usr/local/bin/smollama)
- ✅ Create and enable systemd service (`smollama.service`)
- ✅ Configure log rotation (logrotate + journald with Pi-friendly limits)
- ✅ Service management commands (start/stop/status/logs)
- ✅ Network info display (IP address, dashboard URL)

## Documentation
- ✅ README Quick Start section added with script usage examples
- ✅ All scripts include comprehensive --help documentation
- ✅ Scripts are idempotent (safe to re-run)
