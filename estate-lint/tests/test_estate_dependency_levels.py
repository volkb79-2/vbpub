"""The REAL estate, judged by the project that owns the estate-wide check.

Moved out of `cmru/tests/test_dependency_levels_pyproject.py` by P-core-12
(KI-194): these tests read `cmru.orchestration.toml` and every project's
`pyproject.toml`, so they judge the estate, not cmru, and they turned cmru red
whenever an unrelated project changed. cmru keeps the synthetic graph tests (its
own `build_report`/`compute_levels` behaviour) and its checkout guard; the real
estate and the bootstrap-doc order live here.

The constants below are copies, not imports: a project's tests must not import
another project's test helpers. `load_forge_config` exits 2 when
``CGROUP_PARENT_DEV_GATES`` is unset (the orchestration declares it with no
default), so `estate-lint/tests/conftest.py` supplies a fake value for every test.
"""
from __future__ import annotations

import re
from pathlib import Path

import pytest

from cmru.config import load_forge_config
from cmru.dependencies import build_report

REPO_ROOT = Path(__file__).resolve().parents[2]
ORCHESTRATION = REPO_ROOT / "cmru.orchestration.toml"
DOC = REPO_ROOT / "docs" / "BOOTSTRAP-FROM-ZERO.md"

# The isolated mutation fixture is built from `git ls-files` pathspecs: it carries the orchestration
# file but only some projects, and it has NO `.git`. The real-estate checks skip ONLY on that fact
# about the tree. In a git checkout a project that went missing is a FAILURE (KI-90): one skip
# status must not stand for both "intended isolated copy" and "a real checkout lost a project".
IS_CHECKOUT = (REPO_ROOT / ".git").exists()  # a file in a worktree, a directory in a clone


def _estate_complete() -> bool:
    """Run the real-estate checks: always in a git checkout (where they fail loudly if a
    project is missing), never in a non-git isolated mutation fixture."""
    return IS_CHECKOUT


@pytest.mark.skipif(not _estate_complete(), reason="estate projects absent (isolated mutation fixture)")
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
        # Fleetd's wheel requires cli-extended (its `fleet` CLI is a registered cli-extended
        # CLI, CX-D1), so it is L1 and releases after the shared library.
        assert report.levels["fleetd"] == 1
        # Augeaswrap's endpoint CLI consumes the shared cli-extended contract, so it is L1.
        assert report.levels["augeaswrap"] == 1
        # cgprofile's wheel requires cli-extended and augeaswrap, so it follows both at L2.
        assert report.levels["cgprofile"] == 2
        assert report.levels["run-gate-project"] == 3
        assert report.levels["cmru"] == 4
        assert report.levels["nyxloom"] == 1
        assert report.levels["mdt-cockpit"] == max(report.levels.values()) == 5
        # TLS Edge's current orchestration declaration depends on cli-extended, making it L1.
        assert {name: report.levels[name] for name in ("assay", "tls-edge", "tester-base")} == {
            "assay": 0, "tls-edge": 1, "tester-base": 0,
        }
        assert set(self._forge().orchestration.dependencies["fleetd"]) == {"cli-extended"}
        # topos requires both shared CLI and endpoint wheels, so it follows the L1 providers at L2.
        assert report.levels["topos"] == 2
        # ciu consumes the cli-extended wheel (CIU-114 adoption): a real wheel edge, so L1.
        assert report.levels["ciu"] == 1
        # pwmcp's client wheel requires cli-extended (PWMCP-02, CX-D1): a real wheel edge, so L1.
        assert report.levels["pwmcp"] == 1

    def test_source_bootstrapped_release_controller_is_not_a_release_edge(self):
        forge = self._forge()
        dependencies = forge.orchestration.dependencies
        # These projects invoke CMRU as their release controller but do not
        # consume its published wheel. The controller is bootstrapped from
        # this checkout's source, so it must not force them after the CMRU
        # release.
        for project in (
            "ciu", "assay", "topos", "run-gate-project",
            "pwmcp", "tls-edge", "tester-base",
        ):
            assert "cmru" not in dependencies[project], project
        assert set(dependencies["cgprofile"]) == {"cli-extended", "augeaswrap"}
        # CMRU's own release gate, conversely, requires the stable Run-Gate
        # artifact and tester image, so it must follow Run-Gate in a fresh
        # release sequence.
        assert set(dependencies["cmru"]) == {"cli-extended", "run-gate-project"}
        assert set(dependencies["run-gate-project"]) == {"cgprofile"}
        assert set(dependencies["topos"]) == {"cli-extended", "augeaswrap"}
        order = list(forge.orchestration.project_order)
        assert order.index("augeaswrap") < order.index("topos")
        assert order.index("cgprofile") < order.index("run-gate-project")
        assert order.index("run-gate-project") < order.index("cmru")

    def test_removing_the_cmru_to_cli_extended_edge_fails_the_preflight(self):
        declared = {name: list(deps) for name, deps in self._forge().orchestration.dependencies.items()}
        declared["cmru"] = [dep for dep in declared["cmru"] if dep != "cli-extended"]
        report = self._report(declared)
        assert not report.ok
        assert any(
            "'cmru' requires first-party 'cli-extended' in pyproject.toml [project.dependencies]" in error
            for error in report.errors
        ), report.errors

    def test_removing_the_mdt_cockpit_to_cli_extended_edge_fails_the_artifact_preflight(self):
        declared = {name: list(deps) for name, deps in self._forge().orchestration.dependencies.items()}
        mdt = "mdt-cockpit"
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
    assert doc == _graph_levels(report), (
        "docs/BOOTSTRAP-FROM-ZERO.md `project-order` block is out of date with cmru.orchestration.toml "
        "(a project was added, moved or removed): copy the LEVELS section of `cmru dependencies` "
        "into that block"
    )
    # And the doc's flattened order is itself a valid release order for every declared edge.
    flattened = [name for level in sorted(doc) for name in doc[level]]
    position = {name: index for index, name in enumerate(flattened)}
    for consumer, providers in forge.orchestration.dependencies.items():
        assert all(position[provider] < position[consumer] for provider in providers), consumer


_DOC_SKIP = pytest.mark.skipif(
    not (_estate_complete() and DOC.exists()), reason="estate projects/doc absent (isolated mutation fixture)",
)


@_DOC_SKIP
def test_the_bootstrap_doc_order_agrees_with_the_graph():
    _check_doc(DOC.read_text(encoding="utf-8"))


@_DOC_SKIP
def test_a_doc_whose_order_disagrees_with_the_graph_is_detected():
    text = DOC.read_text(encoding="utf-8")
    # Swap the members of the first and third levels: a lower level then names a
    # project that the graph releases later, whatever the current membership is.
    first = re.search(r"(?m)^L0: (.*)$", text)
    third = re.search(r"(?m)^L2: (.*)$", text)
    assert first and third
    swapped = (
        text.replace(first.group(0), "L0: @@", 1)
        .replace(third.group(0), f"L2: {first.group(1)}", 1)
        .replace("L0: @@", f"L0: {third.group(1)}", 1)
    )
    assert swapped != text
    with pytest.raises(AssertionError):
        _check_doc(swapped)
    dropped = re.sub(r"(?m)^L3: .*\n", "", text)
    assert dropped != text
    with pytest.raises(AssertionError):
        _check_doc(dropped)
