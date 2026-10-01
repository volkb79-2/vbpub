"""Shared-fixture loader for the analysis tests (CD13, CD31).

``analysis/`` and ``analysis/tests/`` are packages, so pytest imports this file
as ``analysis.tests.conftest`` and never rebinds ``sys.modules["conftest"]``:
that name stays the judge's ``tests/conftest.py`` in every collection order, so
a judge test's ``from conftest import ...`` always reaches the conftest pytest
registered. The judge conftest is loaded only on demand, under the unique module
name ``assay_judge_conftest`` (never ``conftest``); no analysis test needs a
judge fixture today. ``gate/tests/support.py`` (W4) reuses that module name.
"""

from __future__ import annotations

import importlib.util
import sys

import pytest

from analysis.tests.analysis_support import PROJECT_ROOT

_NAME = "assay_judge_conftest"


@pytest.fixture(autouse=True)
def _no_ambient_color(monkeypatch):
    """Help and CLI output must not depend on the caller's terminal colour settings (Python 3.14 argparse colours --help under FORCE_COLOR)."""
    monkeypatch.delenv("FORCE_COLOR", raising=False)
    monkeypatch.delenv("PYTHON_COLORS", raising=False)
    monkeypatch.setenv("NO_COLOR", "1")


def load_judge_conftest():
    """The judge's ``tests/conftest.py`` as ``assay_judge_conftest``, loaded once."""
    loaded = sys.modules.get(_NAME)
    if loaded is not None:
        return loaded
    path = PROJECT_ROOT / "tests" / "conftest.py"
    spec = importlib.util.spec_from_file_location(_NAME, path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[_NAME] = module
    try:
        spec.loader.exec_module(module)
    except BaseException:
        del sys.modules[_NAME]
        raise
    return module
