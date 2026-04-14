#!/usr/bin/env bash
#
# Restart the smollama agent in-place.
# Kills the running agent (by PID file or process search) and starts a fresh one.
#
# Usage:
#   ./scripts/restart-agent.sh [AGENT_OPTIONS]
#
# Examples:
#   ./scripts/restart-agent.sh
#   ./scripts/restart-agent.sh --skip-preflight
#   ./scripts/restart-agent.sh -v
#

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
PID_FILE="/tmp/smollama-agent.pid"

# Colours
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
RED='\033[0;31m'
RESET='\033[0m'

info()    { echo -e "${YELLOW}==> $*${RESET}"; }
success() { echo -e "${GREEN}✓ $*${RESET}"; }
error()   { echo -e "${RED}✗ $*${RESET}" >&2; exit 1; }

# --- Stop existing agent ---

stop_agent() {
  local killed=false

  # Try PID file first
  if [[ -f "$PID_FILE" ]]; then
    local pid
    pid=$(cat "$PID_FILE")
    if kill -0 "$pid" 2>/dev/null; then
      info "Stopping agent (PID: $pid)..."
      kill "$pid"
      local wait=0
      while kill -0 "$pid" 2>/dev/null && [[ $wait -lt 5 ]]; do
        sleep 1; ((wait++))
      done
      kill -0 "$pid" 2>/dev/null && kill -9 "$pid" 2>/dev/null || true
      killed=true
    fi
    rm -f "$PID_FILE"
  fi

  # Fallback: find by cmdline in case PID file is stale/missing
  local pids
  pids=$(pgrep -f "smollama run" 2>/dev/null || true)
  if [[ -n "$pids" ]]; then
    info "Stopping agent processes: $pids"
    echo "$pids" | xargs kill 2>/dev/null || true
    sleep 1
    echo "$pids" | xargs kill -9 2>/dev/null || true
    killed=true
  fi

  if [[ "$killed" == true ]]; then
    success "Agent stopped"
  else
    info "No running agent found — starting fresh"
  fi
}

# --- Start new agent ---

start_agent() {
  local smollama_cmd="uv run --project $PROJECT_ROOT smollama"

  info "Starting smollama agent..."
  cd "$PROJECT_ROOT"
  $smollama_cmd run "$@" &
  local pid=$!
  echo "$pid" > "$PID_FILE"
  success "Agent started (PID: $pid)"
  success "Logs: uv run smollama run  (or attach with: kill -USR1 $pid)"
}

stop_agent
start_agent "$@"
