"""Pytest setup for the vanilla ISO patcher subproject."""

from pathlib import Path
import sys


PATCHER_ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(PATCHER_ROOT))
