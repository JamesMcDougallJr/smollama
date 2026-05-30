#!/usr/bin/env bash
#
# Smollama Startup Script
#
# Starts all smollama services: Mosquitto, Ollama, agent, and dashboard.
# Auto-starts missing dependencies and handles cleanup on exit.
#
# Usage:
#   ./scripts/start.sh [OPTIONS] [-- AGENT_OPTIONS]
#
# Options:
#   --no-dashboard       Skip starting the dashboard
#   --dashboard-port P   Dashboard port (default: 8080)
#   --help, -h           Show this help message
#
# Examples:
#   ./scripts/start.sh                        # Start everything
#   ./scripts/start.sh --no-dashboard         # Agent only
#   ./scripts/start.sh --dashboard-port 9090  # Custom dashboard port
#   ./scripts/start.sh -- -v --skip-preflight # Pass options to agent
#

set -euo pipefail

# Script configuration
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
OLLAMA_PORT="11434"
MQTT_PORT="1883"
TMP_DIR="/tmp/smollama-start-$$"
PID_FILE="/tmp/smollama-agent.pid"

# Options
START_DASHBOARD=true
DASHBOARD_PORT=8080
AGENT_ARGS=()

# Track PIDs of services/processes we start
STARTED_OLLAMA=false
STARTED_MOSQUITTO=false
OLLAMA_PID=""
MOSQUITTO_PID=""
AGENT_PID=""
DASHBOARD_PID=""

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
      --no-dashboard)
        START_DASHBOARD=false
        shift
        ;;
      --dashboard-port)
        DASHBOARD_PORT="$2"
        shift 2
        ;;
      --help|-h)
        show_help
        ;;
      --)
        shift
        AGENT_ARGS=("$@")
        break
        ;;
      *)
        # Treat unknown args as agent args
        AGENT_ARGS+=("$1")
        shift
        ;;
    esac
  done
}

#
# Cleanup handler
#

cleanup() {
  local exit_code=$?

  info "Shutting down..."

  # Kill smollama processes we started
  for pid_name in DASHBOARD_PID AGENT_PID; do
    local pid="${!pid_name}"
    if [[ -n "$pid" ]] && kill -0 "$pid" 2>/dev/null; then
      info "Stopping ${pid_name//_/ } (PID: $pid)..."
      kill "$pid" 2>/dev/null || true
      # Give it a moment to exit gracefully
      local wait_count=0
      while kill -0 "$pid" 2>/dev/null && [[ $wait_count -lt 5 ]]; do
        sleep 1
        ((wait_count++))
      done
      # Force kill if still running
      if kill -0 "$pid" 2>/dev/null; then
        kill -9 "$pid" 2>/dev/null || true
      fi
    fi
  done

  # Only stop services that we started manually
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
  local init_system
  init_system=$(detect_init_system)

  info "Starting Ollama service..."

  case "$init_system" in
    systemd)
      if systemctl is-active --quiet ollama 2>/dev/null; then
        success "Ollama already running (systemd)"
        return 0
      fi

      if sudo systemctl start ollama 2>/dev/null; then
        success "Started Ollama via systemd"
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

      if brew services start ollama 2>/dev/null; then
        success "Started Ollama via brew services"
        return 0
      else
        warn "Could not start Ollama via brew services, trying manual start"
      fi
      ;;
  esac

  # Manual start fallback
  if ! command -v ollama &> /dev/null; then
    error "Ollama not installed. Run: ./scripts/install.sh"
  fi

  mkdir -p "$TMP_DIR"
  info "Starting Ollama manually..."

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
  local init_system
  init_system=$(detect_init_system)

  info "Starting Mosquitto service..."

  case "$init_system" in
    systemd)
      if systemctl is-active --quiet mosquitto 2>/dev/null; then
        success "Mosquitto already running (systemd)"
        return 0
      fi

      if sudo systemctl start mosquitto 2>/dev/null; then
        success "Started Mosquitto via systemd"
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

      if brew services start mosquitto 2>/dev/null; then
        success "Started Mosquitto via brew services"
        return 0
      else
        warn "Could not start Mosquitto via brew services, trying manual start"
      fi
      ;;
  esac

  # Manual start fallback
  if ! command -v mosquitto &> /dev/null; then
    error "Mosquitto not installed. Run: ./scripts/install.sh"
  fi

  mkdir -p "$TMP_DIR"
  info "Starting Mosquitto manually..."

  nohup mosquitto -p "$MQTT_PORT" > "$TMP_DIR/mosquitto.log" 2>&1 &
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
# Smollama command resolution
#

resolve_smollama_cmd() {
  if command -v smollama &> /dev/null; then
    echo "smollama"
  elif command -v uv &> /dev/null && [[ -f "$PROJECT_ROOT/pyproject.toml" ]]; then
    echo "uv run --project $PROJECT_ROOT smollama"
  else
    echo "python3 -m smollama"
  fi
}

#
# Main startup flow
#

main() {
  # Check for help flag before doing anything else
  for arg in "$@"; do
    if [[ "$arg" == "--help" ]] || [[ "$arg" == "-h" ]]; then
      show_help
    fi
  done

  parse_args "$@"

  echo -e "${COLOR_BLUE}╔═══════════════════════════════════════════════════════╗${COLOR_RESET}"
  echo -e "${COLOR_BLUE}║         Smollama Startup                             ║${COLOR_RESET}"
  echo -e "${COLOR_BLUE}╚═══════════════════════════════════════════════════════╝${COLOR_RESET}"
  echo

  # Resolve the smollama command once
  local smollama_cmd
  smollama_cmd=$(resolve_smollama_cmd)
  info "Using command: $smollama_cmd"
  echo

  # Check and start Mosquitto
  if check_mosquitto_running; then
    success "Mosquitto is already running"
  else
    start_mosquitto
    wait_for_mosquitto
  fi
  echo

  # Check and start Ollama
  if check_ollama_running; then
    success "Ollama is already running"
  else
    start_ollama
    wait_for_ollama
  fi
  echo

  # Start the agent in the background
  info "Starting smollama agent..."
  cd "$PROJECT_ROOT"
  $smollama_cmd run "${AGENT_ARGS[@]}" &
  AGENT_PID=$!
  echo "$AGENT_PID" > "$PID_FILE"
  success "Agent started (PID: $AGENT_PID)"
  echo

  # Brief pause to let the agent claim GPIO before dashboard starts
  sleep 2

  # Start the dashboard
  if [[ "$START_DASHBOARD" == true ]]; then
    info "Starting smollama dashboard on port $DASHBOARD_PORT..."
    $smollama_cmd dashboard --port "$DASHBOARD_PORT" &
    DASHBOARD_PID=$!
    success "Dashboard started (PID: $DASHBOARD_PID)"
    success "Dashboard available at http://localhost:$DASHBOARD_PORT"
    echo
  fi

  # Summary
  echo -e "${COLOR_GREEN}═══════════════════════════════════════════════════════${COLOR_RESET}"
  echo -e "${COLOR_GREEN}  Smollama is running!${COLOR_RESET}"
  echo -e "${COLOR_GREEN}═══════════════════════════════════════════════════════${COLOR_RESET}"
  echo
  echo "  Agent PID:     $AGENT_PID"
  if [[ "$START_DASHBOARD" == true ]]; then
    echo "  Dashboard PID: $DASHBOARD_PID"
    echo "  Dashboard URL: http://localhost:$DASHBOARD_PORT"
  fi
  echo
  echo "  Press Ctrl+C to stop all services"
  echo

  # Wait for child processes — if either exits, the trap will clean up
  wait $AGENT_PID ${DASHBOARD_PID:+$DASHBOARD_PID} 2>/dev/null || true
}

# Run main function with all arguments passed through
main "$@"
