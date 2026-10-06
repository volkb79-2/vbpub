"""W3-ZERO Z3/Z4: multi-level dependency graph, the pyproject-derived guard, and the
bootstrap doc's order.

* Levels are TRANSITIVE: a project sits above every provider in its chain, and
  ``project_order`` must be a topological order across all levels.
* pyproject is the dependency contract: a first-party requirement that is not a
  declared graph edge fails the preflight with a precise message.
* ``docs/BOOTSTRAP-FROM-ZERO.md``'s fenced ``project-order`` block must equal the
  graph's level table.
"""
from __future__ import annotations

import re
from pathlib import Path
from types import SimpleNamespace

import pytest

from cmru.config import load_forge_config
from cmru.dependencies import build_report, compute_levels, render_text

REPO_ROOT = Path(__file__).resolve().parents[2]
ORCHESTRATION = REPO_ROOT / "cmru.orchestration.toml"
DOC = REPO_ROOT / "docs" / "BOOTSTRAP-FROM-ZERO.md"


def _project(root: Path, name: str, *, requires=(), extras=None, dist: str | None = None) -> SimpleNamespace:
    project_root = root / name
    project_root.mkdir(parents=True, exist_ok=True)
    body = f'[project]\nname = "{dist or name}"\ndependencies = {list(requires)!r}\n'.replace("'", '"')
    for extra, items in (extras or {}).items():
        if "[project.optional-dependencies]" not in body:
            body += "[project.optional-dependencies]\n"
        body += f"{extra} = {list(items)!r}\n".replace("'", '"')
    (project_root / "pyproject.toml").write_text(body, encoding="utf-8")
    return SimpleNamespace(project_root=project_root, scm_dist=None, tool_dependencies=())


def _report(tmp_path, order, declared, **kwargs):
    projects = {name: _project(tmp_path, name, **kwargs.get(name, {})) for name in order}
    return build_report(repo_root=tmp_path, project_order=order, declared=declared, projects=projects)


# --- levels: a 4-level chain, plus a diamond ---------------------------------

CHAIN = {
    "lib": [],
    "core": ["lib"],
    "tool": ["core"],
    "app": ["tool", "lib"],      # skips a level: its level is still 3 (longest chain)
    "side": ["core"],
}
ORDER = ["lib", "core", "tool", "side", "app"]


def test_levels_are_the_longest_transitive_chain_and_order_is_topological_across_all_levels(tmp_path):
    report = _report(tmp_path, ORDER, CHAIN)
    assert report.ok, report.errors
    assert dict(report.levels) == {"lib": 0, "core": 1, "tool": 2, "side": 2, "app": 3}
    position = {name: index for index, name in enumerate(report.project_order)}
    # Every TRANSITIVE provider precedes its consumer, not just the direct ones.
    def closure(name: str) -> set[str]:
        seen: set[str] = set()
        stack = list(CHAIN[name])
        while stack:
            current = stack.pop()
            if current not in seen:
                seen.add(current)
                stack.extend(CHAIN[current])
        return seen
    for name in ORDER:
        assert all(position[provider] < position[name] for provider in closure(name)), name
        assert all(report.levels[provider] < report.levels[name] for provider in closure(name)), name
    text = render_text(report)
    assert "L0: lib" in text and "L1: core" in text and "L2: tool, side" in text and "L3: app" in text


def test_a_consumer_ordered_before_a_transitive_provider_fails_even_with_valid_direct_edges(tmp_path):
    # app is placed before `core`, which it only needs transitively (app -> tool -> core).
    order = ["lib", "app", "core", "tool", "side"]
    declared = {"lib": [], "core": ["lib"], "tool": ["core"], "app": ["tool"], "side": ["core"]}
    report = _report(tmp_path, order, declared)
    assert not report.ok
    assert any("'app' depends on 'tool'" in error for error in report.errors)


def test_levels_ignore_unknown_providers_and_skip_cycles():
    assert compute_levels({"a": ["ghost"]}, ["a"]) == {"a": 0}
    assert compute_levels({"a": ["b"], "b": ["a"], "c": []}, ["a", "b", "c"]) == {"c": 0}


# --- the pyproject-derived guard ------------------------------------------------

def test_a_first_party_pyproject_dependency_must_be_a_declared_edge(tmp_path):
    kwargs = {"consumer": {"requires": ["provider>=1", "requests>=2"]}}
    report = _report(tmp_path, ["provider", "consumer"], {"provider": [], "consumer": []}, **kwargs)
    assert not report.ok
    assert report.errors == (
        "'consumer' requires first-party 'provider' in pyproject.toml [project.dependencies], but "
        "orchestration.project.consumer.depends_on does not declare 'provider'",
    )  # the third-party 'requests' is not a graph concern
    declared = _report(tmp_path / "ok", ["provider", "consumer"], {"provider": [], "consumer": ["provider"]}, **kwargs)
    assert declared.ok, declared.errors
    assert any(edge.kind == "pyproject" and edge.provider == "provider" for edge in declared.edges)


def test_optional_dependency_names_count_and_self_extras_do_not(tmp_path):
    kwargs = {"consumer": {"extras": {"dev": ["Provider_Lib[x]>=1", "consumer[test]"]}},
              "provider": {"dist": "provider-lib"}}
    report = _report(tmp_path, ["provider", "consumer"], {"provider": [], "consumer": []}, **kwargs)
    assert len(report.errors) == 1
    assert "'consumer' requires first-party 'Provider_Lib' in pyproject.toml " \
           "[project.optional-dependencies].dev" in report.errors[0]


def test_unnamed_pyprojects_unparseable_requirements_and_rootless_projects_are_tolerated(tmp_path):
    unnamed = _project(tmp_path, "unnamed")
    (unnamed.project_root / "pyproject.toml").write_text(
        '[project]\ndependencies = ["", "-r other.txt", "other>=1"]\n', encoding="utf-8",
    )
    rootless = SimpleNamespace(project_root=None, scm_dist=None, tool_dependencies=())
    report = build_report(
        repo_root=tmp_path, project_order=["other", "unnamed", "rootless"],
        declared={"other": [], "unnamed": ["other"], "rootless": []},
        projects={"other": _project(tmp_path, "other"), "unnamed": unnamed, "rootless": rootless},
    )
    assert report.ok, report.errors


def test_a_malformed_pyproject_is_reported_not_swallowed(tmp_path):
    projects = {"a": _project(tmp_path, "a")}
    (projects["a"].project_root / "pyproject.toml").write_text("[project\n", encoding="utf-8")
    report = build_report(repo_root=tmp_path, project_order=["a"], declared={"a": []}, projects=projects)
    assert not report.ok and "cannot read" in report.errors[0]


def _estate_complete() -> bool:
    """The orchestration AND every project config it names are present (the isolated
    canary tree carries the orchestration file but only some of the projects)."""
    import tomllib

    if not ORCHESTRATION.exists():
        return False
    projects = tomllib.loads(ORCHESTRATION.read_text(encoding="utf-8")).get("orchestration", {}).get("project", {})
    return bool(projects) and all((REPO_ROOT / entry["config"]).is_file() for entry in projects.values())


@pytest.mark.skipif(not _estate_complete(), reason="estate projects absent (isolated canary tree)")
class TestRealEstate:
    @staticmethod
    def _forge():
        return load_forge_config(ORCHESTRATION)

    def _report(self, declared=None):
        forge = self._forge()
        return build_report(
            repo_root=forge.repo_root, project_order=forge.orchestration.project_order,
            declared=declared or forge.orchestration.dependencies, projects=forge.projects,
        )

    def test_the_estate_graph_is_green_and_has_the_expected_levels(self):
        report = self._report()
        assert report.ok, report.errors
        assert report.levels["cli-extended"] == 0
        assert report.levels["cmru"] == 1
        assert report.levels["modern-debian-tools-python-debug"] == max(report.levels.values())
        assert all(report.levels[name] == 2 for name in ("ciu", "run-gate-project", "assay", "topos"))

    def test_removing_the_cmru_to_cli_extended_edge_fails_the_preflight(self):
        declared = {name: list(deps) for name, deps in self._forge().orchestration.dependencies.items()}
        declared["cmru"] = [dep for dep in declared["cmru"] if dep != "cli-extended"]
        report = self._report(declared)
        assert not report.ok
        assert any(
            "'cmru' requires first-party 'cli-extended' in pyproject.toml [project.dependencies]" in error
            for error in report.errors
        ), report.errors

    def test_removing_the_mdt_to_cli_extended_edge_fails_the_artifact_preflight(self):
        declared = {name: list(deps) for name, deps in self._forge().orchestration.dependencies.items()}
        mdt = "modern-debian-tools-python-debug"
        declared[mdt] = [dep for dep in declared[mdt] if dep != "cli-extended"]
        assert any("'cli-extended' wheel" in error for error in self._report(declared).errors)


# --- the bootstrap doc agrees with the graph ------------------------------------

def _doc_levels(text: str) -> dict[int, list[str]]:
    block = re.search(r"```project-order\n(?P<body>.*?)```", text, re.DOTALL)
    assert block is not None, "docs/BOOTSTRAP-FROM-ZERO.md has no fenced `project-order` block"
    levels: dict[int, list[str]] = {}
    for line in block["body"].strip().splitlines():
        match = re.fullmatch(r"L(\d+): (.+)", line.strip())
        assert match, line
        levels[int(match[1])] = [name.strip() for name in match[2].split(",")]
    return levels


def _graph_levels(report) -> dict[int, list[str]]:
    grouped: dict[int, list[str]] = {}
    for name in report.project_order:
        grouped.setdefault(report.levels[name], []).append(name)
    return grouped


def _check_doc(text: str) -> None:
    """Assert the doc's fenced order equals the real estate graph and is a valid release order."""
    forge = load_forge_config(ORCHESTRATION)
    report = build_report(
        repo_root=forge.repo_root, project_order=forge.orchestration.project_order,
        declared=forge.orchestration.dependencies, projects=forge.projects,
    )
    assert report.ok
    doc = _doc_levels(text)
    assert doc == _graph_levels(report)
    # And the doc's flattened order is itself a valid release order for every declared edge.
    flattened = [name for level in sorted(doc) for name in doc[level]]
    position = {name: index for index, name in enumerate(flattened)}
    for consumer, providers in forge.orchestration.dependencies.items():
        assert all(position[provider] < position[consumer] for provider in providers), consumer


_DOC_SKIP = pytest.mark.skipif(
    not (_estate_complete() and DOC.exists()), reason="estate projects/doc absent (isolated canary tree)",
)


@_DOC_SKIP
def test_the_bootstrap_doc_order_agrees_with_the_graph():
    _check_doc(DOC.read_text(encoding="utf-8"))


@_DOC_SKIP
def test_a_doc_whose_order_disagrees_with_the_graph_is_detected():
    text = DOC.read_text(encoding="utf-8")
    swapped = text.replace("L0: cli-extended\nL1: cmru", "L0: cmru\nL1: cli-extended")
    assert swapped != text
    with pytest.raises(AssertionError):
        _check_doc(swapped)
    dropped = text.replace("L3: modern-debian-tools-python-debug\n", "")
    with pytest.raises(AssertionError):
        _check_doc(dropped)
