#!/bin/bash
# SPDX-License-Identifier: GPL-2.0
#
# run_analysis.sh — thin wrapper, delegates to damon_cli.py
# Python-native CLI is the canonical entry point.
# This script exists for muscle-memory / path compatibility.

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
exec "$SCRIPT_DIR/venv/bin/python3" "$SCRIPT_DIR/damon_cli.py" "$@"
