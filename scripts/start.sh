#!/usr/bin/env bash
#
# Smollama Service Startup Script
#
# Ensures Ollama and MQTT services are running before starting smollama.
# Auto-starts missing services and handles cleanup on exit.
#
# Usage:
#   ./scripts/start.sh [SMOLLAMA_OPTIONS]
#
# Options:
#   All options are passed directly to 'smollama run'
#
# Examples:
#   ./scripts/start.sh                  # Start with default settings
#   ./scripts/start.sh -v               # Start with verbose logging
#   ./scripts/start.sh --host 0.0.0.0   # Start and bind to all interfaces
#   ./scripts/start.sh --log-level debug --json  # Debug mode with JSON output
#

set -euo pipefail

# Script configuration
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
OLLAMA_PORT="11434"
MQTT_PORT="1883"
TMP_DIR="/tmp/smollama-start-$$"

# Track PIDs of services we start
STARTED_OLLAMA=false
STARTED_MOSQUITTO=false
OLLAMA_PID=""
MOSQUITTO_PID=""

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
# Cleanup handler
#

cleanup() {
  local exit_code=$?

  info "Cleaning up..."

  # Only stop services that we started
  if [[ "$STARTED_OLLAMA" == true ]] && [[ -n "$OLLAMA_PID" ]]; then
    if kill -0 "$OLLAMA_PID" 2>/dev/null; then
      info "Stopping Ollama (PID: $OLLAMA_PID)..."
      kill "$OLLAMA_PID" 2>/dev/null || true
    fi
  fi

  if [[ "$STARTED_MOSQUITTO" == true ]] && [[ -n "$MOSQUITTO_PID" ]]; then
    if kill -0 "$MOSQUITTO_PID" 2>/dev/null; then
      info "Stopping Mosquitto (PID: $MOSQUITTO_PID)..."
      kill "$MOSQUITTO_PID" 2>/dev/null || true
    fi
  fi

  # Remove temporary directory
  [[ -d "$TMP_DIR" ]] && rm -rf "$TMP_DIR"

  if [[ $exit_code -eq 0 ]]; then
    success "Cleanup complete"
  fi

  exit $exit_code
}

# Register cleanup handler
trap cleanup EXIT INT TERM

#
# Service detection
#

check_ollama_running() {
  if curl -sf "http://localhost:$OLLAMA_PORT/api/version" &> /dev/null; then
    return 0
  fi
  return 1
}

check_mosquitto_running() {
  # Try multiple methods to check if MQTT is listening
  if command -v nc &> /dev/null; then
    nc -z localhost "$MQTT_PORT" 2>/dev/null && return 0
  elif command -v timeout &> /dev/null; then
    timeout 1 bash -c "cat < /dev/null > /dev/tcp/localhost/$MQTT_PORT" 2>/dev/null && return 0
  elif command -v ss &> /dev/null; then
    ss -ln | grep -q ":$MQTT_PORT " && return 0
  elif command -v lsof &> /dev/null; then
    lsof -i ":$MQTT_PORT" &> /dev/null && return 0
  fi
  return 1
}

#
# Platform detection
#

detect_init_system() {
  if command -v systemctl &> /dev/null && systemctl --version &> /dev/null; then
    echo "systemd"
  elif command -v brew &> /dev/null && [[ "$OSTYPE" == "darwin"* ]]; then
    echo "brew"
  else
    echo "manual"
  fi
}

#
# Service management - Ollama
#

start_ollama() {
  local init_system=$(detect_init_system)

  info "Starting Ollama service..."

  case "$init_system" in
    systemd)
      if systemctl is-active --quiet ollama 2>/dev/null; then
        success "Ollama already running (systemd)"
        return 0
      fi

      # Try to start via systemd
      if sudo systemctl start ollama 2>/dev/null; then
        success "Started Ollama via systemd"
        # Don't set STARTED_OLLAMA=true because systemd manages it
        return 0
      else
        warn "Could not start Ollama via systemd, trying manual start"
      fi
      ;;

    brew)
      if brew services list | grep -q "ollama.*started"; then
        success "Ollama already running (brew services)"
        return 0
      fi

      # Try to start via brew services
      if brew services start ollama 2>/dev/null; then
        success "Started Ollama via brew services"
        # Don't set STARTED_OLLAMA=true because brew manages it
        return 0
      else
        warn "Could not start Ollama via brew services, trying manual start"
      fi
      ;;
  esac

  # Manual start fallback
  if ! command -v ollama &> /dev/null; then
    error "Ollama not installed. Install with: ./scripts/install.sh"
  fi

  mkdir -p "$TMP_DIR"
  info "Starting Ollama manually..."

  # Start Ollama in background
  nohup ollama serve > "$TMP_DIR/ollama.log" 2>&1 &
  OLLAMA_PID=$!
  STARTED_OLLAMA=true

  success "Started Ollama manually (PID: $OLLAMA_PID)"
  return 0
}

wait_for_ollama() {
  info "Waiting for Ollama to be ready..."

  local retries=30
  while [[ $retries -gt 0 ]]; do
    if check_ollama_running; then
      success "Ollama is ready (http://localhost:$OLLAMA_PORT)"
      return 0
    fi
    sleep 1
    ((retries--))
  done

  error "Ollama failed to start. Check logs at: $TMP_DIR/ollama.log"
}

#
# Service management - Mosquitto
#

start_mosquitto() {
  local init_system=$(detect_init_system)

  info "Starting Mosquitto service..."

  case "$init_system" in
    systemd)
      if systemctl is-active --quiet mosquitto 2>/dev/null; then
        success "Mosquitto already running (systemd)"
        return 0
      fi

      # Try to start via systemd
      if sudo systemctl start mosquitto 2>/dev/null; then
        success "Started Mosquitto via systemd"
        # Don't set STARTED_MOSQUITTO=true because systemd manages it
        return 0
      else
        warn "Could not start Mosquitto via systemd, trying manual start"
      fi
      ;;

    brew)
      if brew services list | grep -q "mosquitto.*started"; then
        success "Mosquitto already running (brew services)"
        return 0
      fi

      # Try to start via brew services
      if brew services start mosquitto 2>/dev/null; then
        success "Started Mosquitto via brew services"
        # Don't set STARTED_MOSQUITTO=true because brew manages it
        return 0
      else
        warn "Could not start Mosquitto via brew services, trying manual start"
      fi
      ;;
  esac

  # Manual start fallback
  if ! command -v mosquitto &> /dev/null; then
    error "Mosquitto not installed. Install with: ./scripts/install.sh"
  fi

  mkdir -p "$TMP_DIR"
  info "Starting Mosquitto manually..."

  # Start Mosquitto in background (daemon mode)
  mosquitto -d -p "$MQTT_PORT" > "$TMP_DIR/mosquitto.log" 2>&1 &
  MOSQUITTO_PID=$!
  STARTED_MOSQUITTO=true

  success "Started Mosquitto manually (PID: $MOSQUITTO_PID)"
  return 0
}

wait_for_mosquitto() {
  info "Waiting for Mosquitto to be ready..."

  local retries=10
  while [[ $retries -gt 0 ]]; do
    if check_mosquitto_running; then
      success "Mosquitto is ready (localhost:$MQTT_PORT)"
      return 0
    fi
    sleep 1
    ((retries--))
  done

  error "Mosquitto failed to start. Check logs at: $TMP_DIR/mosquitto.log"
}

#
# Service verification
#

verify_services() {
  info "Verifying smollama connectivity..."

  cd "$PROJECT_ROOT"

  # Run smollama status to verify all connections
  if command -v smollama &> /dev/null; then
    if smollama status &> /dev/null; then
      success "All services are accessible"
      return 0
    else
      warn "Some services may not be accessible. Check with: smollama status"
      return 1
    fi
  else
    warn "Smollama command not found. Run: ./scripts/install.sh"
    return 1
  fi
}

#
# Main startup flow
#

main() {
  echo -e "${COLOR_BLUE}╔═══════════════════════════════════════════════════════╗${COLOR_RESET}"
  echo -e "${COLOR_BLUE}║         Smollama Service Startup                     ║${COLOR_RESET}"
  echo -e "${COLOR_BLUE}╚═══════════════════════════════════════════════════════╝${COLOR_RESET}"
  echo

  # Check and start Ollama
  if check_ollama_running; then
    success "Ollama is already running"
  else
    start_ollama
    wait_for_ollama
  fi
  echo

  # Check and start Mosquitto
  if check_mosquitto_running; then
    success "Mosquitto is already running"
  else
    start_mosquitto
    wait_for_mosquitto
  fi
  echo

  # Verify connectivity
  verify_services
  echo

  # Start smollama
  info "Starting smollama agent..."
  echo

  cd "$PROJECT_ROOT"

  # Use exec to replace the shell process with smollama
  # This ensures proper signal handling and that smollama becomes PID 1 of the script
  if command -v smollama &> /dev/null; then
    exec smollama run "$@"
  else
    # Fallback to python module
    exec python3 -m smollama run "$@"
  fi
}

# Run main function with all arguments passed through
main "$@"
