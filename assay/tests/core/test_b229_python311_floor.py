"""B229 -- the declared ``requires-python = ">=3.11"`` floor is tested, not asserted.

The suite normally runs on a newer interpreter, so two 3.11-only failure
classes are checked statically here:

* syntax newer than 3.11 (``ast.parse(feature_version=(3, 11))``);
* the 3.11 dataclass rule: a field default whose class is unhashable
  (``__hash__ is None``, e.g. ``mappingproxy``) raises ``ValueError`` at class
  creation on 3.11 (3.12+ only rejects list/dict/set). That was the B229
  import crash; here it is evaluated on every imported dataclass.

A real 3.11 import smoke (``python:3.11-slim``) is the authoritative check and
is recorded in B229; these tests keep the two classes from regressing.
"""

from __future__ import annotations

import ast
import dataclasses
import importlib
import pkgutil
from pathlib import Path

import assay
import assay_analysis
import cowtree

_PACKAGES = (assay, assay_analysis, cowtree)


def _source_files() -> list[Path]:
    files: list[Path] = []
    for pkg in _PACKAGES:
        root = Path(pkg.__file__).parent
        files.extend(sorted(root.rglob("*.py")))
    return files


def test_all_sources_parse_with_python_311_grammar() -> None:
    files = _source_files()
    assert files
    for path in files:
        ast.parse(path.read_text(encoding="utf-8"), filename=str(path), feature_version=(3, 11))


def test_no_dataclass_field_default_is_unhashable_on_python_311() -> None:
    checked = 0
    for pkg in _PACKAGES:
        for info in pkgutil.walk_packages(pkg.__path__, pkg.__name__ + "."):
            if info.name.endswith(".__main__"):
                continue  # runs a CLI on import
            module = importlib.import_module(info.name)
            for obj in vars(module).values():
                if not (isinstance(obj, type) and dataclasses.is_dataclass(obj)):
                    continue
                if obj.__module__ != module.__name__:
                    continue
                for fld in dataclasses.fields(obj):
                    if fld.default is dataclasses.MISSING:
                        continue
                    checked += 1
                    # 3.12+ made ``mappingproxy`` define ``__hash__`` (delegating to
                    # the wrapped mapping), so the class-level 3.11 check is not
                    # observable here; hashing the instance is the equivalent probe.
                    try:
                        hash(fld.default)
                        hashable = True
                    except TypeError:
                        hashable = False
                    assert hashable, (
                        f"{obj.__qualname__}.{fld.name}: default of unhashable type "
                        f"{type(fld.default).__name__} fails class creation on Python 3.11; "
                        "use field(default_factory=...)"
                    )
    assert checked > 0
