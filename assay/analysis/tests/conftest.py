"""Shared-fixture loader for the analysis tests (CD13, CD31).

The judge's ``tests/conftest.py`` is loaded here under the unique module name
``assay_judge_conftest`` (never ``conftest``, which pytest owns per directory)
and registered in ``sys.modules``. ``gate/tests/support.py`` (W4) reuses this
loader.

Its names are served through a module ``__getattr__``. That is required, not a
convenience: when one run collects both ``tests`` and ``analysis/tests``,
pytest leaves ``sys.modules["conftest"]`` bound to whichever conftest it
imported last, and every judge test that does ``from conftest import ...``
would then resolve against THIS module. A lazy ``__getattr__`` answers those
imports without putting the judge's hooks or fixtures into this module's
``dir()``, so pytest neither registers a hook twice nor exposes a fixture to
the analysis tests that none of them asked for.
"""

from __future__ import annotations

import importlib.util
import sys

from analysis_support import PROJECT_ROOT

_NAME = "assay_judge_conftest"


def _load_judge_conftest():
    loaded = sys.modules.get(_NAME)
    if loaded is not None:
        return loaded
    path = PROJECT_ROOT / "tests" / "conftest.py"
    spec = importlib.util.spec_from_file_location(_NAME, path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[_NAME] = module
    spec.loader.exec_module(module)
    return module


_judge = _load_judge_conftest()


def __getattr__(name: str):
    if name.startswith("__"):
        raise AttributeError(name)
    return getattr(_judge, name)
