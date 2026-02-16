#!/usr/bin/env bash
#
# Smollama Raspberry Pi Setup Script
#
# One-time configuration for Raspberry Pi production deployment.
# Handles GPIO enablement, system dependencies, package installation,
# systemd service creation, and log rotation.
#
# Usage:
#   sudo ./scripts/setup-pi.sh [OPTIONS]
#
# Options:
#   --no-start           Create service but don't start it
#   --user USER          Run service as specific user (default: $SUDO_USER)
#   --help, -h           Show this help message
#
# Examples:
#   sudo ./scripts/setup-pi.sh                # Full setup and start
#   sudo ./scripts/setup-pi.sh --no-start     # Setup without starting
#   sudo ./scripts/setup-pi.sh --user pi      # Run as 'pi' user
#
# Requirements:
#   - Must run with sudo
#   - Must run on Raspberry Pi hardware
#   - Requires network access for package installation
#

set -euo pipefail

# Script configuration
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
SERVICE_NAME="smollama"
SYSTEMD_SERVICE_FILE="/etc/systemd/system/${SERVICE_NAME}.service"
LOGROTATE_CONFIG="/etc/logrotate.d/${SERVICE_NAME}"
LOG_DIR="/var/log/${SERVICE_NAME}"

# Options
START_SERVICE=true
SERVICE_USER="${SUDO_USER:-pi}"

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
      --no-start)
        START_SERVICE=false
        shift
        ;;
      --user)
        SERVICE_USER="$2"
        shift 2
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
# Root check
#

check_root() {
  if [[ $EUID -ne 0 ]]; then
    error "This script must be run with sudo"
  fi
}

#
# Raspberry Pi detection
#

verify_raspberry_pi() {
  info "Verifying Raspberry Pi hardware..."

  local is_pi=false

  # Check device-tree model (most reliable)
  if [[ -f /proc/device-tree/model ]]; then
    if grep -qi "raspberry pi" /proc/device-tree/model; then
      is_pi=true
    fi
  fi

  # Fallback: check cpuinfo for BCM processor
  if [[ "$is_pi" == false ]] && [[ -f /proc/cpuinfo ]]; then
    if grep -qi "BCM" /proc/cpuinfo; then
      is_pi=true
    fi
  fi

  if [[ "$is_pi" == false ]]; then
    error "This script must run on Raspberry Pi hardware"
  fi

  local model=$(cat /proc/device-tree/model 2>/dev/null | tr -d '\0' || echo "Unknown Pi Model")
  success "Detected: $model"
}

#
# GPIO enablement
#

enable_gpio() {
  info "Enabling GPIO interfaces..."

  if ! command -v raspi-config &> /dev/null; then
    warn "raspi-config not found, skipping GPIO auto-configuration"
    return 0
  fi

  # Enable SPI
  if raspi-config nonint get_spi | grep -q "1"; then
    info "Enabling SPI interface..."
    raspi-config nonint do_spi 0
    success "SPI enabled"
  else
    success "SPI already enabled"
  fi

  # Enable I2C
  if raspi-config nonint get_i2c | grep -q "1"; then
    info "Enabling I2C interface..."
    raspi-config nonint do_i2c 0
    success "I2C enabled"
  else
    success "I2C already enabled"
  fi

  # Add user to gpio group
  if groups "$SERVICE_USER" | grep -qv gpio; then
    info "Adding $SERVICE_USER to gpio group..."
    usermod -a -G gpio "$SERVICE_USER"
    success "User added to gpio group"
    warn "User must log out and back in for group changes to take effect"
  else
    success "User already in gpio group"
  fi
}

#
# System dependencies
#

install_system_dependencies() {
  info "Installing system dependencies..."

  # Update package list
  apt-get update -qq

  # Core dependencies
  local packages=(
    python3
    python3-pip
    python3-venv
    build-essential
    libgpiod2
    mosquitto
    mosquitto-clients
    curl
    git
  )

  # Install packages
  DEBIAN_FRONTEND=noninteractive apt-get install -y -qq "${packages[@]}"

  success "System dependencies installed"
}

#
# UV and package installation
#

install_uv_and_packages() {
  info "Installing UV package manager..."

  # Install UV for the service user
  if ! sudo -u "$SERVICE_USER" bash -c 'command -v uv &> /dev/null'; then
    sudo -u "$SERVICE_USER" bash -c 'curl -LsSf https://astral.sh/uv/install.sh | sh'
    success "UV installed"
  else
    success "UV already installed"
  fi

  # Ensure UV is in PATH for subsequent commands
  local uv_path="/home/$SERVICE_USER/.cargo/bin/uv"
  if [[ ! -x "$uv_path" ]]; then
    uv_path="uv"  # Fallback to system path
  fi

  info "Installing smollama packages (production mode)..."
  cd "$PROJECT_ROOT"

  # Production install with all extras including pi
  # Note: Using non-editable install for production
  sudo -u "$SERVICE_USER" bash -c "
    export PATH=\"/home/$SERVICE_USER/.cargo/bin:\$PATH\"
    cd '$PROJECT_ROOT'
    if command -v uv &> /dev/null; then
      uv venv --python python3
      source .venv/bin/activate
      uv pip install --extra all --extra pi .
    else
      python3 -m venv .venv
      source .venv/bin/activate
      pip install '.[all,pi]'
    fi
  "

  success "Packages installed"
}

#
# System-wide executable installation
#

install_system_executable() {
  info "Installing system-wide smollama command..."

  local venv_smollama="$PROJECT_ROOT/.venv/bin/smollama"
  local system_smollama="/usr/local/bin/smollama"

  if [[ ! -f "$venv_smollama" ]]; then
    error "Smollama executable not found at $venv_smollama"
  fi

  # Create symlink
  ln -sf "$venv_smollama" "$system_smollama"
  success "Installed smollama to $system_smollama"

  # Verify installation
  if command -v smollama &> /dev/null; then
    local version=$(sudo -u "$SERVICE_USER" smollama --version 2>&1 || echo "unknown")
    success "Command verified: smollama ($version)"
  else
    warn "Smollama command not found in PATH after installation"
  fi
}

#
# Configuration setup
#

setup_configuration() {
  info "Setting up configuration..."

  local config_file="$PROJECT_ROOT/config.yaml"
  local example_config="$PROJECT_ROOT/config.example.yaml"

  if [[ -f "$config_file" ]]; then
    success "Configuration file already exists"
    return 0
  fi

  if [[ ! -f "$example_config" ]]; then
    warn "Example config not found, skipping"
    return 1
  fi

  # Copy and set ownership
  cp "$example_config" "$config_file"
  chown "$SERVICE_USER:$SERVICE_USER" "$config_file"
  success "Created config.yaml from template"
  echo "    Edit $config_file to customize settings"
}

#
# Systemd service creation
#

create_systemd_service() {
  info "Creating systemd service..."

  # Create service file
  cat > "$SYSTEMD_SERVICE_FILE" <<EOF
[Unit]
Description=Smollama - Distributed LLM coordination system
After=network.target ollama.service mosquitto.service
Wants=ollama.service mosquitto.service

[Service]
Type=simple
User=$SERVICE_USER
WorkingDirectory=$PROJECT_ROOT
Environment="PATH=/usr/local/bin:/usr/bin:/bin:/home/$SERVICE_USER/.cargo/bin"
Environment="PYTHONUNBUFFERED=1"
ExecStart=/usr/local/bin/smollama run
Restart=on-failure
RestartSec=10s
StandardOutput=journal
StandardError=journal
SyslogIdentifier=smollama

[Install]
WantedBy=multi-user.target
EOF

  success "Systemd service created"

  # Reload systemd
  systemctl daemon-reload
  success "Systemd configuration reloaded"

  # Enable service for auto-start on boot
  systemctl enable "$SERVICE_NAME"
  success "Service enabled for auto-start on boot"
}

#
# Log rotation setup
#

setup_log_rotation() {
  info "Configuring log rotation..."

  # Create log directory
  mkdir -p "$LOG_DIR"
  chown "$SERVICE_USER:$SERVICE_USER" "$LOG_DIR"

  # Create logrotate configuration
  cat > "$LOGROTATE_CONFIG" <<EOF
$LOG_DIR/*.log {
    daily
    rotate 14
    compress
    delaycompress
    missingok
    notifempty
    create 0644 $SERVICE_USER $SERVICE_USER
    sharedscripts
    postrotate
        systemctl reload $SERVICE_NAME > /dev/null 2>&1 || true
    endscript
}

# Database files (size-based rotation)
$PROJECT_ROOT/*.db {
    size 1G
    rotate 3
    compress
    missingok
    notifempty
    create 0644 $SERVICE_USER $SERVICE_USER
}
EOF

  success "Logrotate configuration created"

  # Configure journald retention (Pi-friendly limits)
  local journald_conf="/etc/systemd/journald.conf"
  if [[ -f "$journald_conf" ]]; then
    # Create drop-in directory
    mkdir -p /etc/systemd/journald.conf.d

    # Pi-friendly journal limits
    cat > /etc/systemd/journald.conf.d/smollama.conf <<EOF
[Journal]
SystemMaxUse=500M
MaxRetentionSec=30day
MaxFileSec=1day
EOF

    success "Journald retention configured (500MB, 30 days)"

    # Restart journald to apply changes
    systemctl restart systemd-journald || warn "Could not restart journald"
  fi
}

#
# Service management
#

start_service() {
  if [[ "$START_SERVICE" == false ]]; then
    info "Service created but not started (--no-start specified)"
    return 0
  fi

  info "Starting $SERVICE_NAME service..."

  # Ensure dependencies are running
  for dep in mosquitto; do
    if systemctl is-active --quiet "$dep" 2>/dev/null; then
      success "$dep is running"
    else
      info "Starting $dep..."
      systemctl start "$dep" || warn "Could not start $dep"
    fi
  done

  # Start our service
  if systemctl start "$SERVICE_NAME"; then
    success "Service started successfully"

    # Wait a moment and check status
    sleep 2
    if systemctl is-active --quiet "$SERVICE_NAME"; then
      success "Service is running"
    else
      warn "Service may not be running. Check with: systemctl status $SERVICE_NAME"
    fi
  else
    error "Failed to start service"
  fi
}

#
# Network information
#

show_network_info() {
  info "Network information..."

  # Get IP addresses
  local ip_addresses=$(hostname -I 2>/dev/null | xargs)
  if [[ -n "$ip_addresses" ]]; then
    success "IP addresses: $ip_addresses"
    echo
    echo "    Access dashboard at: http://$(echo $ip_addresses | awk '{print $1}'):8000"
  else
    warn "Could not determine IP address"
  fi
}

#
# Next steps summary
#

show_next_steps() {
  echo
  echo -e "${COLOR_GREEN}═══════════════════════════════════════════════════════${COLOR_RESET}"
  echo -e "${COLOR_GREEN}  Raspberry Pi Setup Complete!${COLOR_RESET}"
  echo -e "${COLOR_GREEN}═══════════════════════════════════════════════════════${COLOR_RESET}"
  echo

  if [[ "$START_SERVICE" == true ]]; then
    echo "Service is running!"
    echo
  fi

  echo "Management commands:"
  echo
  echo "  View status:"
  echo "    ${COLOR_BLUE}sudo systemctl status $SERVICE_NAME${COLOR_RESET}"
  echo
  echo "  Start/stop/restart:"
  echo "    ${COLOR_BLUE}sudo systemctl start $SERVICE_NAME${COLOR_RESET}"
  echo "    ${COLOR_BLUE}sudo systemctl stop $SERVICE_NAME${COLOR_RESET}"
  echo "    ${COLOR_BLUE}sudo systemctl restart $SERVICE_NAME${COLOR_RESET}"
  echo
  echo "  View logs:"
  echo "    ${COLOR_BLUE}journalctl -u $SERVICE_NAME -f${COLOR_RESET}           # Follow logs"
  echo "    ${COLOR_BLUE}journalctl -u $SERVICE_NAME -n 100${COLOR_RESET}       # Last 100 lines"
  echo "    ${COLOR_BLUE}journalctl -u $SERVICE_NAME --since today${COLOR_RESET} # Today's logs"
  echo
  echo "  Disable auto-start:"
  echo "    ${COLOR_BLUE}sudo systemctl disable $SERVICE_NAME${COLOR_RESET}"
  echo

  echo "Configuration:"
  echo "  Config file: ${COLOR_BLUE}$PROJECT_ROOT/config.yaml${COLOR_RESET}"
  echo "  Service file: ${COLOR_BLUE}$SYSTEMD_SERVICE_FILE${COLOR_RESET}"
  echo "  Log directory: ${COLOR_BLUE}$LOG_DIR${COLOR_RESET}"
  echo

  if groups "$SERVICE_USER" | grep -q gpio; then
    echo -e "${COLOR_YELLOW}Note: GPIO group changes require logout/login to take effect${COLOR_RESET}"
    echo
  fi
}

#
# Main setup flow
#

main() {
  echo -e "${COLOR_BLUE}╔═══════════════════════════════════════════════════════╗${COLOR_RESET}"
  echo -e "${COLOR_BLUE}║      Smollama Raspberry Pi Setup Script              ║${COLOR_RESET}"
  echo -e "${COLOR_BLUE}╚═══════════════════════════════════════════════════════╝${COLOR_RESET}"
  echo

  # Parse arguments
  parse_args "$@"

  # Verify prerequisites
  check_root
  verify_raspberry_pi
  echo

  # System configuration
  enable_gpio
  echo

  # Install dependencies
  install_system_dependencies
  echo

  # Install UV and packages
  install_uv_and_packages
  echo

  # Install system-wide command
  install_system_executable
  echo

  # Setup configuration
  setup_configuration
  echo

  # Create systemd service
  create_systemd_service
  echo

  # Setup log rotation
  setup_log_rotation
  echo

  # Start service
  start_service
  echo

  # Show network info
  show_network_info
  echo

  # Show next steps
  show_next_steps
}

# Run main function
main "$@"
