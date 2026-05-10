#!/usr/bin/env bash
#
# Smollama Installation Script
#
# Installs smollama and all dependencies: Python, UV, Ollama, Mosquitto,
# LLM + embedding models, and Pi-specific extras when on Raspberry Pi.
#
# Usage:
#   ./scripts/install.sh [OPTIONS]
#
# Options:
#   --minimal            Install only core dependencies (no extras); skips Ollama
#   --dev                Install with development dependencies
#   --all                Install all dependencies (default)
#   --no-llm             Skip Ollama install and model pulls (edge/sensor nodes)
#   --help, -h           Show this help message
#
# Examples:
#   ./scripts/install.sh              # Full install with all dependencies
#   ./scripts/install.sh --dev        # Development mode
#   ./scripts/install.sh --minimal    # Core only, no extras, no Ollama
#   ./scripts/install.sh --no-llm     # Full install but skip Ollama
#

set -euo pipefail

# Script configuration
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
PYTHON_MIN_VERSION="3.10"
OLLAMA_PORT="11434"
MQTT_PORT="1883"

# Installation mode
INSTALL_MODE="all"
INSTALL_LLM=true

# Set to true when no suitable system Python was found and UV manages Python instead
UV_PYTHON_MANAGED=false

# Set by detect_raspberry_pi
IS_RASPBERRY_PI=false

# Colors for output
if [[ -t 1 ]]; then
  COLOR_RESET="\033[0m"
  COLOR_RED="\033[0;31m"
  COLOR_GREEN="\033[0;32m"
  COLOR_YELLOW="\033[0;33m"
  COLOR_BLUE="\033[0;34m"
else
  COLOR_RESET=""
  COLOR_RED=""
  COLOR_GREEN=""
  COLOR_YELLOW=""
  COLOR_BLUE=""
fi

#
# Output helpers
#

error() {
  echo -e "${COLOR_RED}Error: $1${COLOR_RESET}" >&2
  exit 1
}

warn() {
  echo -e "${COLOR_YELLOW}Warning: $1${COLOR_RESET}" >&2
}

info() {
  echo -e "${COLOR_BLUE}==> $1${COLOR_RESET}"
}

success() {
  echo -e "${COLOR_GREEN}✓ $1${COLOR_RESET}"
}

#
# Help text
#

show_help() {
  sed -n '2,/^$/p' "$0" | sed 's/^# \?//'
  exit 0
}

#
# Argument parsing
#

parse_args() {
  while [[ $# -gt 0 ]]; do
    case $1 in
      --minimal)
        INSTALL_MODE="minimal"
        INSTALL_LLM=false
        shift
        ;;
      --dev)
        INSTALL_MODE="dev"
        shift
        ;;
      --all)
        INSTALL_MODE="all"
        shift
        ;;
      --no-llm)
        INSTALL_LLM=false
        shift
        ;;
      --help|-h)
        show_help
        ;;
      *)
        error "Unknown option: $1\nUse --help for usage information."
        ;;
    esac
  done
}

#
# Platform detection
#

detect_platform() {
  local os_type=""
  local pkg_manager=""

  if [[ "$OSTYPE" == "darwin"* ]]; then
    os_type="macos"
    if command -v brew &> /dev/null; then
      pkg_manager="brew"
    else
      error "macOS detected but Homebrew not found. Please install from https://brew.sh/"
    fi
  elif [[ -f /etc/debian_version ]]; then
    os_type="debian"
    pkg_manager="apt"
  elif [[ -f /etc/redhat-release ]]; then
    error "Red Hat/CentOS/Fedora not yet supported. Contributions welcome!"
  else
    error "Unsupported operating system. Supported: macOS, Debian, Ubuntu"
  fi

  echo "$os_type|$pkg_manager"
}

#
# Raspberry Pi detection
#

detect_raspberry_pi() {
  if [[ -f /proc/device-tree/model ]] && grep -qi "raspberry pi" /proc/device-tree/model 2>/dev/null; then
    IS_RASPBERRY_PI=true
    local model
    model=$(tr -d '\0' < /proc/device-tree/model 2>/dev/null || echo "Raspberry Pi")
    success "Detected Raspberry Pi: $model"
  else
    IS_RASPBERRY_PI=false
  fi
}

#
# Pi-specific system dependencies
#

install_pi_system_deps() {
  info "Installing Raspberry Pi system dependencies..."

  sudo apt-get update -qq
  sudo apt-get install -y -qq \
    build-essential \
    libgpiod2

  success "Pi system dependencies installed"
}

#
# Pi GPIO enablement
#

enable_pi_gpio() {
  info "Enabling GPIO interfaces..."

  if ! command -v raspi-config &> /dev/null; then
    warn "raspi-config not found, skipping GPIO auto-configuration"
    return 0
  fi

  # Enable SPI
  if sudo raspi-config nonint get_spi 2>/dev/null | grep -q "1"; then
    info "Enabling SPI interface..."
    sudo raspi-config nonint do_spi 0
    success "SPI enabled"
  else
    success "SPI already enabled"
  fi

  # Enable I2C
  if sudo raspi-config nonint get_i2c 2>/dev/null | grep -q "1"; then
    info "Enabling I2C interface..."
    sudo raspi-config nonint do_i2c 0
    success "I2C enabled"
  else
    success "I2C already enabled"
  fi

  # Add current user to gpio group
  if ! groups "$USER" | grep -q gpio; then
    info "Adding $USER to gpio group..."
    sudo usermod -a -G gpio "$USER"
    success "User added to gpio group"
    warn "Log out and back in for group changes to take effect"
  else
    success "User already in gpio group"
  fi
}

#
# Python version checking
#

check_python_version() {
  local python_cmd=""
  local python_version=""
  local required_version="$1"

  # Candidate binary names — versioned ones first so we pick the newest available
  local candidates=(python3.12 python3.11 python3.10 python3 python)

  # Also check common non-PATH locations
  local extra_paths=(
    "$HOME/.pyenv/shims/python3"
    "/opt/homebrew/bin/python3.12"
    "/opt/homebrew/bin/python3.11"
    "/opt/homebrew/bin/python3.10"
    "/usr/local/bin/python3.12"
    "/usr/local/bin/python3.11"
    "/usr/local/bin/python3.10"
  )

  for cmd in "${candidates[@]}" "${extra_paths[@]}"; do
    if command -v "$cmd" &> /dev/null || [[ -x "$cmd" ]]; then
      python_version=$("$cmd" --version 2>&1 | awk '{print $2}')
      if [[ "$(printf '%s\n' "$required_version" "$python_version" | sort -V | head -n1)" == "$required_version" ]]; then
        python_cmd="$cmd"
        break
      fi
    fi
  done

  echo "$python_cmd"
}

#
# UV-managed Python fallback
#

find_or_install_python_via_uv() {
  if ! command -v uv &> /dev/null; then
    return 1
  fi

  info "Looking for Python $PYTHON_MIN_VERSION via UV's managed runtimes..."

  # Check if uv already has a managed Python >= 3.10
  if uv python find "$PYTHON_MIN_VERSION" &> /dev/null 2>&1; then
    success "Found UV-managed Python $PYTHON_MIN_VERSION"
    UV_PYTHON_MANAGED=true
    return 0
  fi

  info "Installing Python $PYTHON_MIN_VERSION via UV (no system Python required)..."
  if uv python install "$PYTHON_MIN_VERSION"; then
    success "UV installed Python $PYTHON_MIN_VERSION"
    UV_PYTHON_MANAGED=true
    return 0
  fi

  return 1
}

#
# Python installation
#

install_python() {
  local os_type="$1"
  local pkg_manager="$2"

  info "Installing Python $PYTHON_MIN_VERSION or higher..."

  case "$pkg_manager" in
    brew)
      brew install python@3.11 || brew upgrade python@3.11
      ;;
    apt)
      sudo apt-get update
      sudo apt-get install -y python3 python3-pip python3-venv
      ;;
    *)
      error "Cannot install Python automatically on this system"
      ;;
  esac

  success "Python installed"
}

#
# UV installation
#

install_uv() {
  if command -v uv &> /dev/null; then
    success "UV already installed ($(uv --version))"
    return 0
  fi

  info "Installing UV (fast Python package installer)..."

  # Try official installer first
  if curl -LsSf https://astral.sh/uv/install.sh | sh; then
    # Add to PATH for current session
    export PATH="$HOME/.cargo/bin:$PATH"
    success "UV installed successfully"
    return 0
  else
    warn "UV installation via curl failed, falling back to pip"

    # Fallback to pip installation
    local python_cmd
    python_cmd=$(check_python_version "$PYTHON_MIN_VERSION")
    if [[ -n "$python_cmd" ]]; then
      "$python_cmd" -m pip install --user uv
      export PATH="$HOME/.local/bin:$PATH"

      if command -v uv &> /dev/null; then
        success "UV installed via pip"
        return 0
      fi
    fi

    warn "UV installation failed. Will use pip for package installation."
    return 1
  fi
}

#
# Package installation
#

install_packages() {
  local mode="$1"

  cd "$PROJECT_ROOT"

  info "Installing smollama packages (mode: $mode)..."

  # Determine extras to install
  local extras=""
  case "$mode" in
    minimal)
      extras=""
      ;;
    dev)
      extras="--extra dev"
      # On Pi, also include pi extras
      if [[ "$IS_RASPBERRY_PI" == true ]]; then
        extras="--extra dev --extra pi"
      fi
      ;;
    all)
      extras="--extra all"
      # On Pi, also include pi extras
      if [[ "$IS_RASPBERRY_PI" == true ]]; then
        extras="--extra all --extra pi"
      fi
      ;;
  esac

  # Try UV first, fallback to pip
  if command -v uv &> /dev/null; then
    info "Using UV for fast installation..."

    # For dev mode, use sync for editable install
    # For minimal/all modes, use tool install for global CLI availability
    if [[ "$mode" == "dev" ]]; then
      uv sync $extras
    else
      # Use uv tool install for global CLI tool installation
      local tool_package="."
      case "$mode" in
        all)
          if [[ "$IS_RASPBERRY_PI" == true ]]; then
            tool_package=".[all,pi]"
          else
            tool_package=".[all]"
          fi
          ;;
        minimal)
          tool_package="."
          ;;
      esac

      if [[ "$UV_PYTHON_MANAGED" == true ]]; then
        uv tool install --force --python "$PYTHON_MIN_VERSION" "$tool_package"
      else
        uv tool install --force "$tool_package"
      fi
    fi

    success "Packages installed with UV"
  else
    info "Using pip for installation..."
    local python_cmd
    python_cmd=$(check_python_version "$PYTHON_MIN_VERSION")
    if [[ -z "$python_cmd" ]]; then
      error "Python $PYTHON_MIN_VERSION or higher not found"
    fi

    # Determine pip extras format
    local pip_extras=""
    local pip_flags=""
    case "$mode" in
      minimal)
        pip_extras="."
        pip_flags="--user"
        ;;
      dev)
        if [[ "$IS_RASPBERRY_PI" == true ]]; then
          pip_extras=".[dev,pi]"
        else
          pip_extras=".[dev]"
        fi
        pip_flags="-e"
        ;;
      all)
        if [[ "$IS_RASPBERRY_PI" == true ]]; then
          pip_extras=".[all,pi]"
        else
          pip_extras=".[all]"
        fi
        pip_flags="--user"
        ;;
    esac

    "$python_cmd" -m pip install $pip_flags "$pip_extras"
    success "Packages installed with pip"
  fi

  # Ensure tool directories are in PATH for next steps
  export PATH="$HOME/.local/bin:$HOME/.cargo/bin:$PATH"
}

#
# Configuration setup
#

setup_config() {
  local config_file="$PROJECT_ROOT/config.yaml"
  local example_config="$PROJECT_ROOT/config.example.yaml"

  if [[ -f "$config_file" ]]; then
    success "Configuration file already exists: config.yaml"
    return 0
  fi

  if [[ ! -f "$example_config" ]]; then
    warn "Example config not found at $example_config"
    return 1
  fi

  info "Creating default configuration file..."
  cp "$example_config" "$config_file"
  success "Created config.yaml from template"
  echo "    Edit config.yaml to customize settings"
}

#
# Ollama installation
#

check_ollama() {
  if curl -s "http://localhost:$OLLAMA_PORT/api/version" &> /dev/null; then
    return 0
  fi
  return 1
}

install_ollama() {
  local os_type="$1"
  local pkg_manager="$2"

  # Check if already installed
  if command -v ollama &> /dev/null; then
    success "Ollama already installed"

    # Check if service is running
    if check_ollama; then
      success "Ollama service is running"
    else
      info "Starting Ollama service..."
      case "$pkg_manager" in
        brew)
          brew services start ollama || ollama serve &> /tmp/ollama.log &
          ;;
        *)
          ollama serve &> /tmp/ollama.log &
          ;;
      esac

      # Wait for service to start
      local retries=10
      while [[ $retries -gt 0 ]]; do
        if check_ollama; then
          success "Ollama service started"
          return 0
        fi
        sleep 1
        ((retries--))
      done
      warn "Ollama may not be running. Check with: ollama serve"
    fi
    return 0
  fi

  info "Installing Ollama..."

  case "$pkg_manager" in
    brew)
      brew install ollama
      brew services start ollama
      ;;
    apt)
      curl -fsSL https://ollama.ai/install.sh | sh
      ;;
    *)
      warn "Please install Ollama manually from https://ollama.ai/download"
      return 1
      ;;
  esac

  success "Ollama installed"

  # Wait for service to be ready
  info "Waiting for Ollama service to start..."
  local retries=30
  while [[ $retries -gt 0 ]]; do
    if check_ollama; then
      success "Ollama service is ready"
      return 0
    fi
    sleep 1
    ((retries--))
  done

  warn "Ollama service may not be running. Start with: ollama serve"
  return 1
}

pull_ollama_model() {
  local model="${1:-gemma4:e2b}"

  if ! command -v ollama &> /dev/null; then
    warn "Ollama not installed, skipping model pull"
    return 1
  fi

  # Check if model is already available
  if ollama list 2>/dev/null | grep -q "$(echo "$model" | cut -d: -f1)"; then
    success "Model $model already available"
    return 0
  fi

  info "Pulling Ollama model: $model (this may take a few minutes)..."

  local retries=3
  while [[ $retries -gt 0 ]]; do
    if ollama pull "$model"; then
      success "Model $model pulled successfully"
      return 0
    fi

    warn "Model pull failed, retrying... ($retries attempts remaining)"
    sleep 5
    ((retries--))
  done

  warn "Failed to pull model $model. You can pull it later with: ollama pull $model"
  return 1
}

#
# MQTT/Mosquitto installation
#

check_mosquitto() {
  if nc -z localhost "$MQTT_PORT" 2>/dev/null; then
    return 0
  fi
  return 1
}

install_mosquitto() {
  local os_type="$1"
  local pkg_manager="$2"

  # Check if already installed
  if command -v mosquitto &> /dev/null; then
    success "Mosquitto already installed"

    # Check if service is running
    if check_mosquitto; then
      success "Mosquitto service is running"
    else
      info "Starting Mosquitto service..."
      case "$pkg_manager" in
        brew)
          brew services start mosquitto
          ;;
        apt)
          sudo systemctl start mosquitto || mosquitto -d
          ;;
        *)
          mosquitto -d
          ;;
      esac

      sleep 2
      if check_mosquitto; then
        success "Mosquitto service started"
      else
        warn "Mosquitto may not be running. Check with: mosquitto -v"
      fi
    fi
    return 0
  fi

  info "Installing Mosquitto MQTT broker..."

  case "$pkg_manager" in
    brew)
      brew install mosquitto
      brew services start mosquitto
      ;;
    apt)
      sudo apt-get update
      sudo apt-get install -y mosquitto mosquitto-clients
      sudo systemctl enable mosquitto
      sudo systemctl start mosquitto
      ;;
    *)
      warn "Please install Mosquitto manually"
      return 1
      ;;
  esac

  success "Mosquitto installed"

  # Verify service
  sleep 2
  if check_mosquitto; then
    success "Mosquitto service is running"
  else
    warn "Mosquitto service may not be running"
  fi

  return 0
}

#
# Installation validation
#

validate_installation() {
  info "Validating installation..."

  cd "$PROJECT_ROOT"

  # Try to import smollama — prefer system Python, fall back to uv run
  local python_cmd
  python_cmd=$(check_python_version "$PYTHON_MIN_VERSION")

  if [[ -n "$python_cmd" ]]; then
    if "$python_cmd" -c "import smollama; print('OK')" &> /dev/null; then
      success "Python package import successful"
    else
      warn "Could not import smollama package via system Python"
    fi
  elif [[ "$UV_PYTHON_MANAGED" == true ]] && command -v uv &> /dev/null; then
    if uv run --python "$PYTHON_MIN_VERSION" python -c "import smollama; print('OK')" &> /dev/null; then
      success "Python package import successful (via UV-managed Python)"
    else
      warn "Could not import smollama package via UV-managed Python"
    fi
  else
    warn "No suitable Python found for import validation — skipping"
  fi

  # Check if smollama command is available
  if command -v smollama &> /dev/null; then
    local smollama_path
    smollama_path=$(command -v smollama)
    success "Smollama command is available at: $smollama_path"
  elif [[ -n "$python_cmd" ]] && "$python_cmd" -m smollama --version &> /dev/null; then
    success "Smollama module is available (use: python -m smollama)"
    warn "Command 'smollama' not in PATH. Add the tools directory to your PATH:"
    echo
    echo "    ${COLOR_YELLOW}# UV tools are typically in ~/.local/bin${COLOR_RESET}"
    echo "    ${COLOR_YELLOW}# For bash:${COLOR_RESET}"
    echo "    ${COLOR_BLUE}echo 'export PATH=\"\$HOME/.local/bin:\$PATH\"' >> ~/.bashrc${COLOR_RESET}"
    echo "    ${COLOR_BLUE}source ~/.bashrc${COLOR_RESET}"
    echo
    echo "    ${COLOR_YELLOW}# For zsh (macOS default):${COLOR_RESET}"
    echo "    ${COLOR_BLUE}echo 'export PATH=\"\$HOME/.local/bin:\$PATH\"' >> ~/.zshrc${COLOR_RESET}"
    echo "    ${COLOR_BLUE}source ~/.zshrc${COLOR_RESET}"
    echo
    echo "    ${COLOR_YELLOW}# Or open a new terminal window${COLOR_RESET}"
    echo
  else
    warn "Smollama command not found in PATH"
  fi

  return 0
}

#
# Next steps summary
#

show_next_steps() {
  echo
  echo -e "${COLOR_GREEN}═══════════════════════════════════════════════════════${COLOR_RESET}"
  echo -e "${COLOR_GREEN}  Installation Complete!${COLOR_RESET}"
  echo -e "${COLOR_GREEN}═══════════════════════════════════════════════════════${COLOR_RESET}"
  echo
  echo "Next steps:"
  echo
  echo "  1. Edit configuration (optional):"
  echo "     ${COLOR_BLUE}vim config.yaml${COLOR_RESET}"
  echo
  echo "  2. Start everything (agent + dashboard + services):"
  echo "     ${COLOR_BLUE}./scripts/start.sh${COLOR_RESET}"
  echo
  echo "  3. Check system status:"
  if command -v smollama &> /dev/null; then
    echo "     ${COLOR_BLUE}smollama status${COLOR_RESET}"
  else
    echo "     ${COLOR_BLUE}python -m smollama status${COLOR_RESET}"
  fi
  echo
  echo "  4. View available commands:"
  if command -v smollama &> /dev/null; then
    echo "     ${COLOR_BLUE}smollama --help${COLOR_RESET}"
  else
    echo "     ${COLOR_BLUE}python -m smollama --help${COLOR_RESET}"
  fi
  echo

  if [[ "$INSTALL_MODE" == "dev" ]]; then
    echo "Development mode commands:"
    echo "  ${COLOR_BLUE}pytest${COLOR_RESET}                    # Run tests"
    echo "  ${COLOR_BLUE}pytest --cov${COLOR_RESET}             # Run tests with coverage"
    echo
  fi

  echo "Useful resources:"
  echo "  Config: $PROJECT_ROOT/config.yaml"
  echo "  Dashboard: http://localhost:8080 (after starting)"
  echo
}

#
# Main installation flow
#

main() {
  echo -e "${COLOR_BLUE}╔═══════════════════════════════════════════════════════╗${COLOR_RESET}"
  echo -e "${COLOR_BLUE}║         Smollama Installation Script                 ║${COLOR_RESET}"
  echo -e "${COLOR_BLUE}╚═══════════════════════════════════════════════════════╝${COLOR_RESET}"
  echo

  # Parse arguments
  parse_args "$@"

  info "Installation mode: $INSTALL_MODE"
  echo

  # Detect platform
  info "Detecting platform..."
  local platform_info
  platform_info=$(detect_platform)
  local os_type
  os_type=$(echo "$platform_info" | cut -d'|' -f1)
  local pkg_manager
  pkg_manager=$(echo "$platform_info" | cut -d'|' -f2)
  success "Platform: $os_type ($pkg_manager)"

  # Detect Raspberry Pi
  detect_raspberry_pi
  echo

  # Pi-specific system setup
  if [[ "$IS_RASPBERRY_PI" == true ]]; then
    install_pi_system_deps
    echo
    enable_pi_gpio
    echo
  fi

  # Install UV first — it's a static binary and doesn't need Python
  install_uv
  echo

  # Check Python version (searches versioned binaries and common extra paths)
  info "Checking Python version..."
  local python_cmd
  python_cmd=$(check_python_version "$PYTHON_MIN_VERSION")

  if [[ -z "$python_cmd" ]]; then
    warn "No system Python $PYTHON_MIN_VERSION+ found"

    # UV fallback: let UV download and manage Python 3.10 for us
    if command -v uv &> /dev/null; then
      if find_or_install_python_via_uv; then
        info "UV will manage Python $PYTHON_MIN_VERSION for the smollama environment"
      else
        install_python "$os_type" "$pkg_manager"

        python_cmd=$(check_python_version "$PYTHON_MIN_VERSION")
        if [[ -z "$python_cmd" && "$UV_PYTHON_MANAGED" == false ]]; then
          error "Python installation failed or version still below $PYTHON_MIN_VERSION"
        fi
      fi
    else
      install_python "$os_type" "$pkg_manager"

      python_cmd=$(check_python_version "$PYTHON_MIN_VERSION")
      if [[ -z "$python_cmd" ]]; then
        error "Python installation failed or version still below $PYTHON_MIN_VERSION"
      fi
    fi
  fi

  if [[ -n "$python_cmd" ]]; then
    local python_version
    python_version=$("$python_cmd" --version 2>&1 | awk '{print $2}')
    success "Python $python_version ($python_cmd)"
  else
    success "Python $PYTHON_MIN_VERSION will be managed by UV"
  fi
  echo

  # Install packages
  install_packages "$INSTALL_MODE"
  echo

  # Setup configuration
  setup_config
  echo

  # Install Mosquitto (required dependency)
  install_mosquitto "$os_type" "$pkg_manager"
  echo

  # Install Ollama and pull models (skipped for edge/minimal installs)
  if [[ "$INSTALL_LLM" == true ]]; then
    install_ollama "$os_type" "$pkg_manager"
    echo
    pull_ollama_model "gemma4:e2b"
    echo
    pull_ollama_model "all-minilm:l6-v2"
    echo
  else
    info "Skipping Ollama install and model pulls (--no-llm)"
    echo
  fi

  # Validate installation
  validate_installation
  echo

  # Show next steps
  show_next_steps
}

# Run main function
main "$@"
