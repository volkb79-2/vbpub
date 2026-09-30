"""Dataclass contract (B129, W10 step 1; P1-suite-scope W4, CD27).

Every dataclass under ``src/assay`` is pinned, parameter by parameter and field
flag by field flag, against ``tests/fixtures/dataclass-contract.json``. Flag
mutants such as ``frozen=True`` -> ``False`` or ``kw_only`` flips would
otherwise survive R2, because no behavioural test exercises "this class can be
mutated".

Decorators are detected by the **resolved decorator object**, not by the
literal spelling ``@dataclass`` (CD27): each decorator expression (a name, an
attribute, or the ``func`` of a call) is resolved through the module's own
bindings and compared by identity with ``dataclasses.dataclass`` and, once it
exists, ``assay.records.record`` / ``assay.records.positional_record``. An alias
or a re-export therefore resolves, and this file passes before and after the
records refactor without edits.

Regenerate the fixture (a contract change is a reviewed change, committed with
the source change)::

    cd assay && PYTHONPATH=src:tests python tests/core/test_dataclass_contract.py \\
        > tests/fixtures/dataclass-contract.json
"""

from __future__ import annotations

import ast
import dataclasses
import importlib
import importlib.util
import json
from pathlib import Path

from conftest import PROJECT_ROOT

PARAMS = (
    "init",
    "repr",
    "eq",
    "order",
    "unsafe_hash",
    "frozen",
    "match_args",
    "kw_only",
    "slots",
    "weakref_slot",
)
FIXTURE = PROJECT_ROOT / "tests" / "fixtures" / "dataclass-contract.json"
SRC_ASSAY = PROJECT_ROOT / "src" / "assay"


def _available_params(cls: type) -> tuple[str, ...]:
    """Feature-detect the params this interpreter's ``_DataclassParams`` carries."""
    slots = set(getattr(type(cls.__dataclass_params__), "__slots__", ()))
    return tuple(p for p in PARAMS if p in slots)


def _module_name(path: Path) -> str:
    """``src/assay/x/y.py`` -> ``assay.x.y``; ``__init__.py`` -> the package."""
    parts = list(path.relative_to(PROJECT_ROOT / "src").with_suffix("").parts)
    if parts[-1] == "__init__":
        parts.pop()
    return ".".join(parts)


def _decorator_objects() -> dict[str, object]:
    """The objects that make a class a dataclass, keyed by the name we report."""
    objects: dict[str, object] = {"dataclass": dataclasses.dataclass}
    if importlib.util.find_spec("assay.records") is not None:
        records = importlib.import_module("assay.records")
        objects["record"] = records.record
        objects["positional_record"] = records.positional_record
    return objects


def resolve_expression(expr: ast.expr, namespace: dict[str, object]) -> object | None:
    """Resolve a decorator expression through *namespace*, or ``None``.

    A ``Name`` is a namespace lookup, an ``Attribute`` is ``getattr`` on the
    resolved value, and a ``Call`` resolves to whatever its ``func`` resolves
    to (the decorator factory).
    """
    if isinstance(expr, ast.Call):
        return resolve_expression(expr.func, namespace)
    if isinstance(expr, ast.Name):
        return namespace.get(expr.id)
    if isinstance(expr, ast.Attribute):
        owner = resolve_expression(expr.value, namespace)
        return None if owner is None else getattr(owner, expr.attr, None)
    return None


def decorator_kind(expr: ast.expr, namespace: dict[str, object]) -> str | None:
    """``"dataclass"``, ``"record"``, ``"positional_record"`` or ``None``."""
    resolved = resolve_expression(expr, namespace)
    if resolved is None:
        return None
    for kind, obj in _decorator_objects().items():
        if resolved is obj:
            return kind
    return None


def declared_in_source(
    source: str, namespace: dict[str, object]
) -> tuple[dict[str, list[str]], list[str]]:
    """``({module-level class name: decorator kinds}, [nested "lineno Name"])``.

    A class counts when at least one of its decorators resolves to a dataclass
    maker. Anything not directly in the module body is reported as nested.
    """
    tree = ast.parse(source)
    top_level = {id(node) for node in tree.body}
    declared: dict[str, list[str]] = {}
    nested: list[str] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.ClassDef):
            continue
        kinds = [
            kind
            for dec in node.decorator_list
            if (kind := decorator_kind(dec, namespace)) is not None
        ]
        if not kinds:
            continue
        if id(node) in top_level:
            declared[node.name] = kinds
        else:
            nested.append(f"{node.lineno} {node.name}")
    return declared, sorted(nested)


def _declared(path: Path, namespace: dict[str, object]) -> tuple[set[str], list[str]]:
    declared, nested = declared_in_source(path.read_text(encoding="utf-8"), namespace)
    return set(declared), [f"{path.relative_to(PROJECT_ROOT)}:{item}" for item in nested]


def _observe() -> dict:
    classes: dict[str, dict] = {}
    for path in sorted(SRC_ASSAY.rglob("*.py")):
        module = importlib.import_module(_module_name(path))
        reflected = {
            obj.__qualname__
            for obj in vars(module).values()
            if isinstance(obj, type)
            and dataclasses.is_dataclass(obj)
            and obj.__module__ == module.__name__
        }
        declared, nested = _declared(path, vars(module))
        rel = path.relative_to(PROJECT_ROOT)
        assert nested == [], f"{rel}: dataclass-decorated class not at module level: {nested}"
        assert reflected == declared, (
            f"{rel}: reflected dataclasses {sorted(reflected)} differ from the "
            f"declared ones {sorted(declared)}"
        )
        for name in sorted(declared):
            cls = getattr(module, name)
            available = _available_params(cls)
            params = {p: getattr(cls.__dataclass_params__, p) for p in available}
            class_kw_only = (
                cls.__dataclass_params__.kw_only
                if "kw_only" in available
                else (all(f.kw_only for f in dataclasses.fields(cls)) if dataclasses.fields(cls) else False)
            )
            defaults = {
                "init": True,
                "repr": True,
                "compare": True,
                "hash": None,
                "kw_only": class_kw_only,
            }
            fields: dict[str, dict] = {}
            for f in dataclasses.fields(cls):
                flags = {
                    "init": f.init,
                    "repr": f.repr,
                    "compare": f.compare,
                    "hash": f.hash,
                    "kw_only": f.kw_only,
                }
                deviation = {k: v for k, v in flags.items() if v != defaults[k]}
                if isinstance(f.default, bool):
                    deviation["default"] = f.default
                if deviation:
                    fields[f.name] = deviation
            classes[f"{module.__name__}.{cls.__qualname__}"] = {
                "params": params,
                "fields": fields,
            }
    return {"format": 1, "classes": classes}


def _restrict(expected: dict, observed: dict) -> dict:
    """The fixture with each class's params restricted to what this interpreter carries."""
    restricted = {"format": expected["format"], "classes": {}}
    for name, entry in expected["classes"].items():
        keep = set(observed["classes"].get(name, {}).get("params", entry["params"]))
        restricted["classes"][name] = {
            "params": {k: v for k, v in entry["params"].items() if k in keep},
            "fields": entry["fields"],
        }
    return restricted


def _describe_difference(expected: dict, observed: dict) -> str:
    added = sorted(set(observed["classes"]) - set(expected["classes"]))
    removed = sorted(set(expected["classes"]) - set(observed["classes"]))
    changed = []
    for name in sorted(set(observed["classes"]) & set(expected["classes"])):
        want, got = expected["classes"][name], observed["classes"][name]
        if want != got:
            keys = [k for k in ("params", "fields") if want[k] != got[k]]
            changed.append(f"{name}: differing {keys}: expected {want} observed {got}")
    return f"added {added}; removed {removed}; changed {changed}"


def test_every_dataclass_matches_its_reviewed_contract() -> None:
    observed = _observe()
    expected = _restrict(json.loads(FIXTURE.read_text(encoding="utf-8")), observed)
    assert observed == expected, _describe_difference(expected, observed)


if __name__ == "__main__":
    print(json.dumps(_observe(), indent=2, sort_keys=True))
