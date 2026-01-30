#!/usr/bin/env bash
set -euo pipefail

cd /workspaces/vbpub/ciu

pytest tests -v
