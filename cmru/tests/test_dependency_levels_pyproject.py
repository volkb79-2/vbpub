"""W3-ZERO Z3/Z4: multi-level dependency graph, the pyproject-derived guard, and
cmru's checkout guard.

* Levels are TRANSITIVE: a project sits above every provider in its chain, and
  ``project_order`` must be a topological order across all levels.
* pyproject is the dependency contract: a first-party requirement that is not a
  declared graph edge fails the preflight with a precise message.

The REAL estate's own levels (``TestRealEstate``) and the
``docs/BOOTSTRAP-FROM-ZERO.md`` order oracle moved to
``estate-lint/tests/test_estate_dependency_levels.py`` (P-core-12, KI-194): they
judge the estate, not cmru. What stays here is cmru's own behaviour — the graph
engine on synthetic estates, the pyproject guard, and the checkout guard that
decides whether a missing project is a failure or an isolated fixture.
"""
from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest

from cmru.config import load_forge_config
from cmru.dependencies import build_report, compute_levels, render_text

REPO_ROOT = Path(__file__).resolve().parents[2]
ORCHESTRATION = REPO_ROOT / "cmru.orchestration.toml"
DOC = REPO_ROOT / "docs" / "BOOTSTRAP-FROM-ZERO.md"


def test_the_real_estate_config_refuses_without_the_gates_cgroup_parent(monkeypatch, capsys):
    """The declared-config contract (223e0bb05) stays pinned: no default, unset => exit 2.

    The suite-wide fixture supplies a fake value so the other real-config tests are hermetic;
    this one removes it and proves the shipped config still refuses.
    """
    monkeypatch.delenv("CGROUP_PARENT_DEV_GATES", raising=False)

    with pytest.raises(SystemExit) as refused:
        load_forge_config(ORCHESTRATION)

    assert refused.value.code == 2
    assert "CGROUP_PARENT_DEV_GATES" in capsys.readouterr().err


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


# The isolated mutation fixture is built from `git ls-files` pathspecs: it carries the orchestration
# file but only some projects, and it has NO `.git`. The real-estate checks skip ONLY on that fact
# about the tree. In a git checkout a project that went missing is a FAILURE (KI-90): one skip
# status must not stand for both "intended isolated copy" and "a real checkout lost a project".
IS_CHECKOUT = (REPO_ROOT / ".git").exists()  # a file in a worktree, a directory in a clone


def estate_violations(repo_root: Path, *, is_checkout: bool, doc: Path | None = None) -> list[str]:
    """What a FULL checkout must carry: the orchestration, every project config it names, and the
    bootstrap doc. A non-checkout tree (the isolated canary copy) is exempt: [] there."""
    import tomllib

    if not is_checkout:
        return []
    orchestration = repo_root / "cmru.orchestration.toml"
    if not orchestration.is_file():
        return ["cmru.orchestration.toml is missing from a full checkout"]
    projects = tomllib.loads(orchestration.read_text(encoding="utf-8")).get(
        "orchestration", {}).get("project", {})
    out = [] if projects else ["cmru.orchestration.toml names no projects"]
    out += [f"{name}: {entry['config']} is missing from a full checkout"
            for name, entry in sorted(projects.items()) if not (repo_root / entry["config"]).is_file()]
    if doc is not None and not doc.is_file():
        out.append(f"{doc} is missing from a full checkout")
    return out


def _estate_complete() -> bool:
    """Run the real-estate checks: always in a git checkout (where they fail loudly if a
    project is missing), never in a non-git isolated mutation fixture."""
    return IS_CHECKOUT


def test_a_full_checkout_carries_the_orchestrated_estate():
    """UNSKIPPED guard: in a git checkout every orchestrated project config and the doc exist."""
    assert estate_violations(REPO_ROOT, is_checkout=IS_CHECKOUT, doc=DOC) == []


def _fake_estate(tmp_path: Path, *, git: bool, present: dict[str, bool]) -> Path:
    root = tmp_path / ("co" if git else "iso")
    root.mkdir()
    if git:
        (root / ".git").write_text("gitdir: x\n", encoding="utf-8")
    (root / "cmru.orchestration.toml").write_text(
        "".join(f'[orchestration.project.{n}]\nconfig = "{n}/cmru.toml"\n' for n in present),
        encoding="utf-8")
    for name, here in present.items():
        if here:
            (root / name).mkdir()
            (root / name / "cmru.toml").write_text("[project]\n", encoding="utf-8")
    return root


def test_a_full_checkout_missing_a_project_fails_instead_of_skipping(tmp_path):
    root = _fake_estate(tmp_path, git=True, present={"a": True, "b": False})
    assert any(v.startswith("b:") for v in estate_violations(root, is_checkout=True))


def test_a_full_checkout_missing_the_doc_fails(tmp_path):
    root = _fake_estate(tmp_path, git=True, present={"a": True})
    assert estate_violations(root, is_checkout=True, doc=root / "nope.md")


def test_only_a_tree_without_git_is_exempt(tmp_path):
    root = _fake_estate(tmp_path, git=False, present={"a": False})
    assert estate_violations(root, is_checkout=(root / ".git").exists()) == []
    assert estate_violations(root, is_checkout=True)  # same tree as a checkout: a failure

