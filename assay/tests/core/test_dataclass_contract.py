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


# ---------------------------------------------------------------------------
# W10 step 2 (I1, O2): the records are the only way to declare a dataclass.
# ---------------------------------------------------------------------------

from assay import records  # noqa: E402  (kept next to the tests that use it)

RECORD_KINDS = ("record", "positional_record")
RAW_MAKERS = (dataclasses.dataclass, dataclasses.make_dataclass)
COMMON_PARAMS = {
    "init": True,
    "repr": True,
    "eq": True,
    "order": False,
    "unsafe_hash": False,
    "match_args": True,
    "slots": False,
    "weakref_slot": False,
}
EXPECTED_PARAMS = {
    "record": {**COMMON_PARAMS, "frozen": True, "kw_only": True},
    "positional_record": {**COMMON_PARAMS, "frozen": True, "kw_only": False},
}


def dataclass_bypasses(source: str, namespace: dict[str, object]) -> list[str]:
    """``"lineno name"`` of every reference to a raw dataclass maker in *source*.

    Any ``Name`` or ``Attribute`` that resolves (through *namespace*) to
    ``dataclasses.dataclass`` or ``dataclasses.make_dataclass`` counts, whether
    it is a decorator, a call such as ``dataclass(...)(type(...))``, or a plain
    alias. An import statement binds a name but is not a reference.
    """
    found = []
    for node in ast.walk(ast.parse(source)):
        if not isinstance(node, (ast.Name, ast.Attribute)):
            continue
        resolved = resolve_expression(node, namespace)
        if any(resolved is maker for maker in RAW_MAKERS):
            found.append(f"{node.lineno} {ast.unparse(node)}")
    return sorted(found)


def declaration_violations(declared: dict[str, list[str]]) -> list[str]:
    """Classes not declared with exactly one of the two record decorators."""
    return sorted(name for name, kinds in declared.items() if len(kinds) != 1 or kinds[0] not in RECORD_KINDS)


def params_violations(kind: str, params: dict[str, object]) -> list[str]:
    """Params that differ from what the decorator *kind* is documented to set."""
    expected = EXPECTED_PARAMS[kind]
    return sorted(
        f"{name}={params[name]!r} (expected {want!r})"
        for name, want in expected.items()
        if name in params and params[name] != want
    )


def _module_sources() -> list[tuple[Path, str]]:
    return [(path, path.read_text(encoding="utf-8")) for path in sorted(SRC_ASSAY.rglob("*.py"))]


def test_no_src_module_uses_a_raw_dataclass_maker_except_records() -> None:
    offenders = []
    for path, source in _module_sources():
        if path == SRC_ASSAY / "records.py":
            continue
        module = importlib.import_module(_module_name(path))
        offenders += [f"{path.relative_to(PROJECT_ROOT)}:{item}" for item in dataclass_bypasses(source, vars(module))]
    assert offenders == []


def test_records_is_the_one_module_that_calls_the_raw_dataclass_maker() -> None:
    path = SRC_ASSAY / "records.py"
    module = importlib.import_module("assay.records")
    bypasses = dataclass_bypasses(path.read_text(encoding="utf-8"), vars(module))
    assert len(bypasses) == 2
    assert all(item.endswith(" dataclass") for item in bypasses)


def test_every_src_dataclass_carries_exactly_one_record_decorator() -> None:
    offenders = {}
    for path, source in _module_sources():
        module = importlib.import_module(_module_name(path))
        declared, _ = declared_in_source(source, vars(module))
        bad = declaration_violations(declared)
        if bad:
            offenders[str(path.relative_to(PROJECT_ROOT))] = bad
    assert offenders == {}


def test_every_src_dataclass_params_match_its_decorator() -> None:
    offenders = {}
    for path, source in _module_sources():
        module = importlib.import_module(_module_name(path))
        declared, _ = declared_in_source(source, vars(module))
        for name, kinds in declared.items():
            cls = getattr(module, name)
            params = {p: getattr(cls.__dataclass_params__, p) for p in _available_params(cls)}
            assert set(params) >= {"frozen", "kw_only"}
            bad = params_violations(kinds[0], params)
            if bad:
                offenders[f"{module.__name__}.{name}"] = bad
    assert offenders == {}


def test_the_decorators_are_resolved_by_object_not_by_spelling() -> None:
    namespace = {"dc": dataclasses, "rec": records.record, "pos": records.positional_record}
    source = (
        "@dc.dataclass(frozen=True)\nclass A:\n    x: int\n\n"
        "@rec\nclass B:\n    x: int\n\n"
        "@pos\nclass C:\n    x: int\n\n"
        "@unrelated\nclass D:\n    x: int\n"
    )
    declared, nested = declared_in_source(source, namespace)
    assert declared == {"A": ["dataclass"], "B": ["record"], "C": ["positional_record"]}
    assert nested == []
    assert decorator_kind(ast.parse("rec").body[0].value, namespace) == "record"
    assert decorator_kind(ast.parse("rec(x=1)").body[0].value, namespace) == "record"
    assert decorator_kind(ast.parse("other").body[0].value, namespace) is None
    assert decorator_kind(ast.parse("dc.nothing").body[0].value, namespace) is None
    assert resolve_expression(ast.parse("1 + 2").body[0].value, namespace) is None


def test_a_bare_dataclass_decorator_is_rejected_by_both_rules() -> None:
    namespace = {"dataclass": dataclasses.dataclass}
    source = "@dataclass(frozen=True, kw_only=True)\nclass A:\n    x: int\n"
    declared, _ = declared_in_source(source, namespace)
    assert declaration_violations(declared) == ["A"]
    assert dataclass_bypasses(source, namespace) == ["1 dataclass"]


def test_a_call_form_dataclass_with_wrong_flags_is_rejected() -> None:
    namespace = {"dataclass": dataclasses.dataclass}
    source = 'X = dataclass(frozen=False, kw_only=True)(type("X", (), {}))\n'
    declared, nested = declared_in_source(source, namespace)
    assert (declared, nested) == ({}, [])
    assert dataclass_bypasses(source, namespace) == ["1 dataclass"]


def test_a_dataclass_alias_and_make_dataclass_are_rejected() -> None:
    namespace = {"dataclasses": dataclasses, "alias": dataclasses.dataclass}
    source = "maker = alias\nother = dataclasses.make_dataclass('Y', [])\n"
    assert dataclass_bypasses(source, namespace) == ["1 alias", "2 dataclasses.make_dataclass"]
    assert dataclass_bypasses("import dataclasses\nx = dataclasses.field\n", namespace) == []


def test_a_function_local_record_is_reported_as_nested() -> None:
    namespace = {"record": records.record}
    source = "def make():\n    @record\n    class Inner:\n        x: int\n    return Inner\n"
    declared, nested = declared_in_source(source, namespace)
    assert declared == {}
    assert nested == ["3 Inner"]


def test_a_class_with_two_record_decorators_or_none_is_rejected() -> None:
    namespace = {"record": records.record, "positional_record": records.positional_record}
    source = (
        "@record\n@positional_record\nclass Both:\n    x: int\n\n"
        "@record\nclass One:\n    x: int\n"
    )
    declared, _ = declared_in_source(source, namespace)
    assert declaration_violations(declared) == ["Both"]
    assert declaration_violations({"Ghost": []}) == ["Ghost"]


def test_params_violations_names_each_wrong_flag() -> None:
    good_record = dict(EXPECTED_PARAMS["record"])
    good_positional = dict(EXPECTED_PARAMS["positional_record"])
    assert params_violations("record", good_record) == []
    assert params_violations("positional_record", good_positional) == []
    assert params_violations("record", {**good_record, "kw_only": False}) == ["kw_only=False (expected True)"]
    assert params_violations("record", {**good_record, "frozen": False}) == ["frozen=False (expected True)"]
    assert params_violations("positional_record", {**good_positional, "kw_only": True}) == [
        "kw_only=True (expected False)"
    ]
    assert params_violations("record", good_positional) == ["kw_only=False (expected True)"]
    assert params_violations("record", {**good_record, "order": True}) == ["order=True (expected False)"]


def test_both_dataclass_transform_values_are_pinned() -> None:
    expected_common = {
        "eq_default": True,
        "order_default": False,
        "frozen_default": True,
        "field_specifiers": (dataclasses.field,),
        "kwargs": {},
    }
    assert records.record.__dataclass_transform__ == {**expected_common, "kw_only_default": True}
    assert records.positional_record.__dataclass_transform__ == {**expected_common, "kw_only_default": False}


def test_record_and_positional_record_build_what_they_promise() -> None:
    @records.record
    class Keyword:
        first: int
        second: int = 2

    @records.positional_record
    class Positional:
        first: int
        second: int = 2

    keyword = Keyword(first=1)
    assert (keyword.first, keyword.second) == (1, 2)
    try:
        Keyword(1)  # type: ignore[misc]
    except TypeError:
        pass
    else:
        raise AssertionError("a record must reject positional construction")
    positional = Positional(1, 3)
    assert (positional.first, positional.second) == (1, 3)
    for instance in (keyword, positional):
        try:
            instance.first = 9  # type: ignore[misc]
        except dataclasses.FrozenInstanceError:
            pass
        else:
            raise AssertionError("a record must be frozen")
    assert records.record.__name__ == "record"
    assert records.positional_record.__name__ == "positional_record"


if __name__ == "__main__":
    print(json.dumps(_observe(), indent=2, sort_keys=True))
