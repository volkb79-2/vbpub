"""Expose the in-repository vmscreen package to the vm-lab unit lane."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "libraries" / "vmscreen" / "src"))
