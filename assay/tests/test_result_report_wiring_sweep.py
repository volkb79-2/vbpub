"""B078 round-1 blocker 2 — every command-execution site is DECIDED about,
never merely unwired.

The round-1 defect was not a wrong decision; it was an unexamined call site.
`execute_plan` is the one place A-073's rule lives, and it is reached from
five places across three modules for four different reasons. Three of them
run the lane's own R0 command (and must consult a declared `result_report`),
two run the lane's argv to answer some other question (and must not). Nothing
in the code says which is which except the presence or absence of one keyword
argument — which is exactly the shape of thing a later edit adds a sixth
member to without noticing.

So this sweep pins it: every call site either passes `result_report=`
explicitly, or appears below with a written reason. There is no third
disposition and no way to be silent. This is the same discipline
`test_untrusted_json_parse_sweep.py` applies to untrusted JSON parses, for the
same reason — a derived sweep notices what exists, a pinned list notices what
changed.

It also answers blocker 2's question mechanically rather than by assertion: if
no execution path is unexamined, then no lane shape can declare
`result_report` and have it be silently inert, and no load-time rigor refusal
is needed.
"""

from __future__ import annotations

import ast
from pathlib import Path
from typing import NamedTuple

SOURCE_ROOT = Path(__file__).resolve().parents[1] / "src" / "assay"

#: The call sites that deliberately do NOT consult a declared report, each
#: with the reason it is not the lane's own R0 command. A site that starts
#: passing the argument, or that disappears, breaks this test — which is the
#: point.
EXCLUDED_SITES: dict[tuple[str, str], str] = {
    ("runner.py", "run_lane"): (
        "the `environment_command` probe (B010): it probes the INVOKING "
        "environment, is not the lane command, and has no report of its own"
    ),
    ("mutation.py", "_run_one"): (
        "an R2 candidate re-execution: a mutant's signal is whether the suite "
        "fails, run `jobs`-way concurrently against one declared path, so a "
        "shared report would be both a race and a re-definition of `killed`"
    ),
}

#: The sites that MUST pass it, and why each is the lane's own R0 command.
#: Listed by name so a site that stops passing the argument fails with a
#: sentence rather than a diff.
REQUIRED_SITES: dict[tuple[str, str], str] = {
    ("runner.py", "execute_command"): "the documented R0 step and public API",
    ("runner.py", "_execute_snapshot_unit"): (
        "the shared snapshot engine; it FORWARDS what its caller passed, and "
        "only `_run_prepared_lane`'s baseline call site passes anything -- "
        "this is the path every R1/R2/R3-declaring lane's R0 command takes, "
        "including RG-45's own `ui_unit` (rigor = ['R0', 'R1'])"
    ),
    ("runner.py", "run_lane"): "an R0-only lane's direct branch",
}


class Site(NamedTuple):
    module: str
    function: str
    lineno: int
    passes_result_report: bool


def _collect_sites() -> list[Site]:
    class Visitor(ast.NodeVisitor):
        def __init__(self, module: str) -> None:
            self.module = module
            self.functions: list[str] = []
            self.sites: list[Site] = []

        def visit_FunctionDef(self, node: ast.FunctionDef):
            self.functions.append(node.name)
            self.generic_visit(node)
            self.functions.pop()

        visit_AsyncFunctionDef = visit_FunctionDef  # type: ignore[assignment]

        def visit_Call(self, node: ast.Call):
            name = (
                node.func.id
                if isinstance(node.func, ast.Name)
                else node.func.attr if isinstance(node.func, ast.Attribute) else None
            )
            if name in ("execute_plan", "execute_command"):
                self.sites.append(
                    Site(
                        module=self.module,
                        function=self.functions[-1] if self.functions else "<module>",
                        lineno=node.lineno,
                        passes_result_report=any(
                            keyword.arg == "result_report" for keyword in node.keywords
                        ),
                    )
                )
            self.generic_visit(node)

    sites: list[Site] = []
    for path in sorted(SOURCE_ROOT.rglob("*.py")):
        visitor = Visitor(path.relative_to(SOURCE_ROOT).as_posix())
        visitor.visit(ast.parse(path.read_text(encoding="utf-8")))
        sites.extend(visitor.sites)
    return sites


def test_the_sweep_finds_a_plausible_population():
    """Guards the guard. A moved source root or a renamed function would make
    every assertion below pass vacuously — which is precisely how a sweep
    stays green while missing the site that matters."""
    sites = _collect_sites()
    assert len(sites) >= 5, f"implausibly few execution sites found: {sites}"
    modules = {site.module for site in sites}
    assert modules >= {"runner.py", "mutation.py", "canary.py"}, modules


def test_every_execution_site_is_decided_about():
    """The sweep itself: no call site is merely unexamined."""
    undecided = [
        site
        for site in _collect_sites()
        if not site.passes_result_report
        and (site.module, site.function) not in EXCLUDED_SITES
    ]
    assert not undecided, (
        "these command-execution sites neither pass `result_report=` nor "
        "appear in EXCLUDED_SITES with a stated reason:\n"
        + "\n".join(f"  src/assay/{s.module}:{s.lineno} (in {s.function})" for s in undecided)
        + "\n\nDecide: does this site run the LANE'S OWN R0 command (pass "
        "`lane.result_report`, or forward what the caller gave you), or does "
        "it run the lane's argv to answer some other question (pass `None` "
        "explicitly, and add it to EXCLUDED_SITES with the reason)?"
    )


def test_every_required_site_still_passes_the_argument():
    """The other direction, which the sweep above structurally cannot see: a
    site that STOPS passing the argument would silently unwire a whole lane
    shape and every other test would stay green except the one end-to-end
    case that covers it. Round 1 shipped exactly that."""
    passing = {
        (site.module, site.function)
        for site in _collect_sites()
        if site.passes_result_report
    }
    missing = {key: reason for key, reason in REQUIRED_SITES.items() if key not in passing}
    assert not missing, (
        "these sites must consult the lane's declared result_report and no "
        "longer do:\n"
        + "\n".join(f"  src/assay/{module}::{function} -- {reason}" for (module, function), reason in missing.items())
    )


def test_the_legacy_canary_pipeline_is_pinned_as_excluded_by_value():
    """`canary._run_pipeline` passes `result_report=None` — an explicit
    exclusion, which the keyword-presence sweep above reads as "decided".
    This pins the VALUE, so flipping it to `lane.result_report` (silently
    extending the tiebreak to both canary halves) fails here rather than
    nowhere."""
    source = (SOURCE_ROOT / "canary.py").read_text(encoding="utf-8")
    tree = ast.parse(source)
    values: list[str] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        name = node.func.id if isinstance(node.func, ast.Name) else None
        if name != "execute_command":
            continue
        for keyword in node.keywords:
            if keyword.arg == "result_report":
                values.append(ast.unparse(keyword.value))
    assert values == ["None"], (
        "assay.canary's pipeline must exclude the tiebreak explicitly: a "
        "canary probe's control/transform outcome answers 'did injecting this "
        "defect change the judgement', not 'did the wrapped suite pass'"
    )
