"""Tests for web_report.py's import-time venv-site-packages discovery.

Requires Flask (an optional dependency for this one script); skipped
entirely when it isn't installed, matching how the rest of this project
treats web_report.py as opt-in.
"""
import os
import sys

import pytest

pytest.importorskip('flask')

SCRIPTS_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, SCRIPTS_DIR)


def test_import_survives_missing_venv_dir():
    # Regression test: web_report.py used to os.listdir() its own venv/lib
    # path unconditionally at module import time, crashing with
    # FileNotFoundError whenever no local venv existed there (e.g. Flask
    # installed system-wide/into this repo's shared venv instead). This
    # repo doesn't commit a scripts/damon-analysis/venv/, so importing the
    # module at its real location already exercises that missing-dir case.
    sys.modules.pop('web_report', None)
    import web_report  # noqa: F401
    sys.modules.pop('web_report', None)
