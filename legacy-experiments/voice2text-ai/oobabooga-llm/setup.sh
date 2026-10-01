#!/usr/bin/env bash
set -euo pipefail

# Colors for output
Y="\033[1;33m"; G="\033[1;32m"; R="\033[1;31m"; C="\033[0m"
log() { echo -e "${Y}[INFO]${C} $*"; }
ok()  { echo -e "${G}[OK]${C}   $*"; }
err() { echo -e "${R}[ERR]${C}  $*"; }

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

log "Setting up oobabooga-llm environment..."

# Create virtual environment if it doesn't exist
if [[ ! -d "venv" ]]; then
    log "Creating Python virtual environment..."
    python3 -m venv venv
fi

# Activate virtual environment
log "Activating virtual environment..."
source venv/bin/activate

# Install requirements
log "Installing Python dependencies..."
pip install --upgrade pip
pip install -r requirements.txt

# Copy environment template if needed
if [[ ! -f ".env.sample" ]] && [[ -f ".env.example" ]]; then
    log "Copying .env.example to .env.sample..."
    cp .env.example .env.sample
fi

# Run the compose initialization script
log "Running compose initialization..."
python ../ciu.py "$@"