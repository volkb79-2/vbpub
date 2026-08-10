"""A-192's load-time rigor grammar, and O3's "before any side effect" half.

Reviewer-added. The grammar itself (``config.py``'s canonical-subsequence
check and ``uncovered-line``'s R1 prerequisite) was reachable ONLY from the
byte-locked P23 acceptance packet, which the registered ``tester-unified``
gate does not run -- so both refusals were unexercised by the suite the gate
actually measures, and ``test_config_vocabularies.py``'s own comment pointing
at "test_config_rigor.py's own grammar tests" had nothing to point at.

O3 asks for more than the exception, too: *"invalid declarations are refused
at load before Git, snapshot, output, or process side effects"*, proven with
installed sentinels rather than by inspection. A loader that validated rigor
only after resolving source roots, reserving output, or shelling out to git
would satisfy a bare ``pytest.raises`` and fail this module.
"""

from __future__ import annotations

import subprocess

import pytest
from conftest import Project, R0_LANE, set_key

from assay import git as git_module
from assay import isolation as isolation_module
from assay import safeio as safeio_module
from assay.config import LaneConfigError, load_lane_file

_R1_JUDGE = """
[lanes.package.judge]
language = "python"
source_roots = ["src"]
fail_under = 100.0
allow_excluded = false
coverage = { format = "coverage-py-json", artifact = "cov.json" }
base = "main"
"""

_R2_JUDGE = """
[lanes.package.judge]
language = "python"
source_roots = ["src"]
base = "main"
mutation = { jobs = 1, max_mutants = 3, operators = ["compare-swap"] }
"""


def _canary(mechanism: str) -> str:
    return f'canary = {{ mechanism = "{mechanism}", target = "src/mod.py" }}\n'


def _r2_and_canary(mechanism: str) -> str:
    return _R2_JUDGE + _canary(mechanism)


def _r1_r2_and_canary(mechanism: str) -> str:
    return (
        _R1_JUDGE
        + 'mutation = { jobs = 1, max_mutants = 3, operators = ["compare-swap"] }\n'
        + _canary(mechanism)
    )


#: R3's own minimal judge table -- language + source_roots + canary, and
#: nothing else (A-062 refuses config for an undeclared level, so an R0+R3
#: lane may not carry R1's or R2's fields just to reach the grammar check).
_R3_ONLY_JUDGE_HEAD = '\n[lanes.package.judge]\nlanguage = "python"\nsource_roots = ["src"]\n'


@pytest.fixture
def no_side_effects(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    """Sentinels on every boundary a load-time refusal must precede.

    Each records rather than merely exploding, so a failure names WHICH
    boundary ran instead of only that one did."""
    fired: list[str] = []

    def sentinel(name: str):
        def fire(*args, **kwargs):
            fired.append(name)
            raise AssertionError(f"{name} ran before the declaration was refused")

        return fire

    monkeypatch.setattr(git_module, "run", sentinel("git.run"))
    monkeypatch.setattr(git_module, "dirty_paths", sentinel("git.dirty_paths"))
    monkeypatch.setattr(git_module, "head_rev", sentinel("git.head_rev"))
    monkeypatch.setattr(git_module, "repo_top", sentinel("git.repo_top"))
    monkeypatch.setattr(
        isolation_module, "prepare_snapshot", sentinel("isolation.prepare_snapshot")
    )
    monkeypatch.setattr(safeio_module, "reserve_output", sentinel("safeio.reserve_output"))
    monkeypatch.setattr(subprocess, "run", sentinel("subprocess.run"))
    monkeypatch.setattr(subprocess, "Popen", sentinel("subprocess.Popen"))
    return fired


@pytest.mark.parametrize(
    ("rigor", "judge", "fragment"),
    [
        ('["R2"]', _R2_JUDGE, "R0-led ordered subsequence"),
        ('["R1"]', _R1_JUDGE, "R0-led ordered subsequence"),
        ('["R1", "R0"]', _R1_JUDGE, "R0-led ordered subsequence"),
        ('["R0", "R2", "R1"]', _r1_r2_and_canary("import-break"), "R0-led ordered subsequence"),
        ('["R0", "R3", "R2"]', _r2_and_canary("import-break"), "R0-led ordered subsequence"),
        (
            '["R0", "R1", "R3", "R2"]',
            _r1_r2_and_canary("uncovered-line"),
            "R0-led ordered subsequence",
        ),
        (
            '["R0", "R3"]',
            _R3_ONLY_JUDGE_HEAD + _canary("uncovered-line"),
            "requires declared R1",
        ),
    ],
    ids=[
        "r2-alone",
        "r1-alone",
        "r1-before-r0",
        "r2-before-r1",
        "r3-before-r2",
        "r3-before-r2-with-all-four",
        "uncovered-line-without-r1",
    ],
)
def test_an_invalid_declaration_is_refused_at_load_before_any_boundary(
    project: Project,
    no_side_effects: list[str],
    rigor: str,
    judge: str,
    fragment: str,
):
    project.file("src/mod.py", "x = 1\n")
    path = project.write(set_key(R0_LANE, "rigor", rigor) + judge)

    with pytest.raises(LaneConfigError, match=fragment):
        load_lane_file(path)

    assert no_side_effects == [], "the refusal must precede every boundary"


@pytest.mark.parametrize(
    ("rigor", "judge"),
    [
        ('["R0"]', ""),
        ('["R0", "R1"]', _R1_JUDGE),
        ('["R0", "R2"]', _R2_JUDGE),
        ('["R0", "R3"]', _R3_ONLY_JUDGE_HEAD + _canary("import-break")),
        ('["R0", "R1", "R3"]', _R1_JUDGE + _canary("uncovered-line")),
        ('["R0", "R1", "R2", "R3"]',
         _R1_JUDGE
         + 'mutation = { jobs = 1, max_mutants = 3, operators = ["compare-swap"] }\n'
         + _canary("uncovered-line")),
    ],
    ids=["r0", "r0-r1", "r0-r2", "r0-r3", "r0-r1-r3-uncovered", "all-four"],
)
def test_every_canonical_ordered_declaration_still_loads(
    project: Project, rigor: str, judge: str
):
    """The positive half that keeps the grammar from being satisfiable by
    refusing everything: each valid R0-led subsequence loads and keeps its
    declared order verbatim."""
    project.file("src/mod.py", "x = 1\n")
    lane = load_lane_file(
        project.write(set_key(R0_LANE, "rigor", rigor) + judge)
    ).lane("package")

    assert list(lane.rigor) == [level.strip(' "') for level in rigor[1:-1].split(",")]
