"""Shared machinery for the scanner-progress guard tests (B113, A-466).

Each component's ``test_*_scanner_progress_guards.py`` builds an at-risk mutant
with assay's own Python adapter, ``exec``s it as a throw-away module and drives
it under a ``sys.settrace`` line budget, so an unguarded spin becomes a
deterministic ``StepLimit`` regardless of machine speed.

Case tuple: (id, file under src/assay, function holding the site, unique
substring of the site's line within that function, operator, site description,
occurrence on that line in byte order, entry call).
"""

import ast
import sys
import types

import pytest
from conftest import PROJECT_ROOT

from assay.adapters.python import PythonAdapter

SRC = PROJECT_ROOT / "src" / "assay"
LINE_BUDGET = 200_000  # deterministic: counts executed lines, never seconds


class StepLimit(Exception):
    """The mutant executed more lines than any terminating scan of these inputs needs."""


def locate(rel, function, anchor):
    text = (SRC / rel).read_text(encoding="utf-8")
    matches = [n for n in ast.parse(text).body if isinstance(n, ast.FunctionDef) and n.name == function]
    assert len(matches) == 1, f"{rel}: expected one module-level {function}()"
    lines = text.splitlines()
    hits = [n for n in range(matches[0].lineno, matches[0].end_lineno + 1) if anchor in lines[n - 1]]
    assert len(hits) == 1, f"{rel}:{function}: anchor {anchor!r} must name exactly one line, got {hits}"
    return text, hits[0]


def mutant_module(case, monkeypatch):
    cid, rel, function, anchor, operator, description, occurrence, _ = case
    text, lineno = locate(rel, function, anchor)
    sites = sorted(
        (
            s
            for s in PythonAdapter().generate_mutation_sites(text, {lineno}, operators=(operator,), limit=1000)
            if s.lineno == lineno and s.description == description
        ),
        key=lambda s: s.start_byte,
    )
    assert len(sites) > occurrence, f"{cid}: the mutant this guard exists for is no longer generated"
    package = "assay" + ("." + rel.rsplit("/", 1)[0].replace("/", ".") if "/" in rel else "")
    name = f"{package}._b113_mutant_{cid.replace('-', '_')}"
    module = types.ModuleType(name)
    module.__package__ = package
    monkeypatch.setitem(sys.modules, name, module)  # dataclasses resolve annotations via sys.modules
    # A synthetic filename: coverage never attributes these lines to the real source file.
    exec(
        compile(
            sites[occurrence].apply(text.encode("utf-8")).decode("utf-8"),
            f"<b113 mutant {cid}>",
            "exec",
        ),
        module.__dict__,
    )
    return module


def line_budgeted(call):
    executed = 0

    def tracer(frame, event, arg):
        nonlocal executed
        if event == "line":
            executed += 1
            if executed > LINE_BUDGET:
                raise StepLimit()
        return tracer

    previous = sys.gettrace()
    sys.settrace(tracer)
    try:
        return call()
    finally:
        sys.settrace(previous)


def assert_case_is_refused(case, monkeypatch):
    module = mutant_module(case, monkeypatch)
    with pytest.raises(AssertionError, match="scanner cursor did not advance"):
        line_budgeted(lambda: case[7](module))


def assert_cases_belong(cases, allowed_paths):
    """A misfiled case would still pass; refuse it by component."""
    assert cases, "a component file must carry cases"
    foreign = sorted({case[1] for case in cases} - set(allowed_paths))
    assert not foreign, f"cases name source paths outside this component: {foreign}"


def assert_ordinary_input_terminates(cases, real_modules, refusal_types):
    """Every unmutated entry returns or raises its module's OWN declared refusal,
    never the guard's AssertionError."""
    for case in cases:
        try:
            case[7](real_modules[case[1]])
        except refusal_types:
            pass
