#!/usr/bin/env bash
#
# Smollama Installation Script
#
# Automates installation of smollama and its dependencies on macOS, Debian, and Ubuntu.
# Handles Python, UV, package installation, configuration, and optional external services.
#
# Usage:
#   ./scripts/install.sh [OPTIONS]
#
# Options:
#   --minimal            Install only core dependencies (no extras)
#   --dev                Install with development dependencies
#   --all                Install all cross-platform dependencies (default)
#   --non-interactive    No prompts, use defaults for all choices
#   -n                   Short form of --non-interactive
#   --help, -h           Show this help message
#
# Examples:
#   ./scripts/install.sh                    # Interactive, all dependencies
#   ./scripts/install.sh --dev              # Development mode
#   ./scripts/install.sh --non-interactive  # Automated/CI mode
#

set -euo pipefail

# Script configuration
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
PYTHON_MIN_VERSION="3.10"
OLLAMA_PORT="11434"
MQTT_PORT="1883"

# Installation mode and flags
INSTALL_MODE="all"
NON_INTERACTIVE=false

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
      --non-interactive|-n)
        NON_INTERACTIVE=true
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
# Python version checking
#

check_python_version() {
  local python_cmd=""
  local python_version=""
  local required_version="$1"

  # Try python3 first, then python
  for cmd in python3 python; do
    if command -v "$cmd" &> /dev/null; then
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
    local python_cmd=$(check_python_version "$PYTHON_MIN_VERSION")
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
      ;;
    all)
      extras="--extra all"
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
          tool_package=".[all]"
          ;;
        minimal)
          tool_package="."
          ;;
      esac

      uv tool install --force "$tool_package"
    fi

    success "Packages installed with UV"
  else
    info "Using pip for installation..."
    local python_cmd=$(check_python_version "$PYTHON_MIN_VERSION")
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
        pip_extras=".[dev]"
        pip_flags="-e"
        ;;
      all)
        pip_extras=".[all]"
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
  local model="${1:-llama3.2:1b}"

  if ! command -v ollama &> /dev/null; then
    warn "Ollama not installed, skipping model pull"
    return 1
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

  # Check Python import
  local python_cmd=$(check_python_version "$PYTHON_MIN_VERSION")
  if [[ -z "$python_cmd" ]]; then
    error "Python validation failed"
  fi

  cd "$PROJECT_ROOT"

  # Try to import smollama
  if "$python_cmd" -c "import smollama; print('OK')" &> /dev/null; then
    success "Python package import successful"
  else
    warn "Could not import smollama package"
    return 1
  fi

  # Check if smollama command is available
  if command -v smollama &> /dev/null; then
    local smollama_path=$(command -v smollama)
    success "Smollama command is available at: $smollama_path"
  elif "$python_cmd" -m smollama --version &> /dev/null; then
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
  echo "  2. Check system status:"
  if command -v smollama &> /dev/null; then
    echo "     ${COLOR_BLUE}smollama status${COLOR_RESET}"
  else
    echo "     ${COLOR_BLUE}python -m smollama status${COLOR_RESET}"
    echo "     ${COLOR_YELLOW}(or 'smollama status' after adding ~/.local/bin to PATH)${COLOR_RESET}"
  fi
  echo
  echo "  3. Start the agent:"
  echo "     ${COLOR_BLUE}./scripts/start.sh${COLOR_RESET}"
  if command -v smollama &> /dev/null; then
    echo "     or"
    echo "     ${COLOR_BLUE}smollama run${COLOR_RESET}"
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
  echo "  README: $PROJECT_ROOT/README.md"
  echo "  Config: $PROJECT_ROOT/config.yaml"
  echo "  Roadmap: $PROJECT_ROOT/roadmap/"
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
  info "Non-interactive: $NON_INTERACTIVE"
  echo

  # Detect platform
  info "Detecting platform..."
  local platform_info=$(detect_platform)
  local os_type=$(echo "$platform_info" | cut -d'|' -f1)
  local pkg_manager=$(echo "$platform_info" | cut -d'|' -f2)
  success "Platform: $os_type ($pkg_manager)"
  echo

  # Check Python version
  info "Checking Python version..."
  local python_cmd=$(check_python_version "$PYTHON_MIN_VERSION")

  if [[ -z "$python_cmd" ]]; then
    warn "Python $PYTHON_MIN_VERSION or higher not found"

    if [[ "$NON_INTERACTIVE" == true ]]; then
      install_python "$os_type" "$pkg_manager"
    else
      read -p "Install Python $PYTHON_MIN_VERSION? (y/n) " -n 1 -r
      echo
      if [[ $REPLY =~ ^[Yy]$ ]]; then
        install_python "$os_type" "$pkg_manager"
      else
        error "Python $PYTHON_MIN_VERSION or higher is required"
      fi
    fi

    # Re-check after installation
    python_cmd=$(check_python_version "$PYTHON_MIN_VERSION")
    if [[ -z "$python_cmd" ]]; then
      error "Python installation failed or version still below $PYTHON_MIN_VERSION"
    fi
  fi

  local python_version=$("$python_cmd" --version 2>&1 | awk '{print $2}')
  success "Python $python_version ($python_cmd)"
  echo

  # Install UV
  install_uv
  echo

  # Install packages
  install_packages "$INSTALL_MODE"
  echo

  # Setup configuration
  setup_config
  echo

  # Optional: Install Ollama
  if [[ "$NON_INTERACTIVE" == true ]]; then
    install_ollama "$os_type" "$pkg_manager"
  else
    if command -v ollama &> /dev/null || check_ollama; then
      success "Ollama already available"
    else
      read -p "Install Ollama (LLM runtime)? (y/n) " -n 1 -r
      echo
      if [[ $REPLY =~ ^[Yy]$ ]]; then
        install_ollama "$os_type" "$pkg_manager"
      else
        info "Skipping Ollama installation"
        echo "    Install later from: https://ollama.ai/download"
      fi
    fi
  fi
  echo

  # Optional: Pull Ollama model
  if command -v ollama &> /dev/null; then
    if [[ "$NON_INTERACTIVE" == true ]]; then
      pull_ollama_model "llama3.2:1b"
    else
      read -p "Pull default model (llama3.2:1b)? (y/n) " -n 1 -r
      echo
      if [[ $REPLY =~ ^[Yy]$ ]]; then
        pull_ollama_model "llama3.2:1b"
      else
        info "Skipping model pull"
        echo "    Pull later with: ollama pull llama3.2:1b"
      fi
    fi
  fi
  echo

  # Optional: Install Mosquitto
  if [[ "$NON_INTERACTIVE" == true ]]; then
    install_mosquitto "$os_type" "$pkg_manager"
  else
    if command -v mosquitto &> /dev/null || check_mosquitto; then
      success "Mosquitto already available"
    else
      read -p "Install Mosquitto (MQTT broker)? (y/n) " -n 1 -r
      echo
      if [[ $REPLY =~ ^[Yy]$ ]]; then
        install_mosquitto "$os_type" "$pkg_manager"
      else
        info "Skipping Mosquitto installation"
        echo "    Install later or use remote MQTT broker"
      fi
    fi
  fi
  echo

  # Validate installation
  validate_installation
  echo

  # Show next steps
  show_next_steps
}

# Run main function
main "$@"
