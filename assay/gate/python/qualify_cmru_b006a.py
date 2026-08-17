#!/usr/bin/env python3
"""WI-5 real-consumer qualification harness (B006(a) §6, oracle O6).

Proves that a real consumer -- CMRU -- can make genuine R1/R2/R3 claims
through the installed candidate Assay wheel *while Topos's tracked, deliberate
absolute-target symlink fixtures stay tracked*, using
``snapshot_selection = "repository-minus-unsafe-symlinks"`` (B006(a)) rather
than deleting anything.

This is deliberately a **disposable qualification harness**, not a permanent
CMRU lane: R2's mutation candidates come from ``base..HEAD`` filtered to the
declared source roots, so a permanent CMRU R1/R2/R3 lane would render
``INCONCLUSIVE``/``NO_MUTANTS`` (exit 5, rolling the whole verdict) on every
commit that does not touch ``cmru/src``. The one controlled head commit this
harness builds *does* touch ``cmru/src`` (``cmru/src/cmru/_b006a_probe.py``),
which is what makes the R2 claim real without redefining CMRU's own release
gate. It never writes to the real ``cmru/`` or ``topos/`` checkout, never
imports ``assay`` from source, and never launches CMRU's own outer Docker
gate.

**The stale-suite premise.** The checked-in CMRU suite at the frozen input is
not green outside Assay:
``test_release_publish_rejects_response_without_upload_coordinate`` stubs
``GitHubReleases.get_release_by_tag`` but not ``update_release``, so
``publish()`` reaches the unstubbed method and makes a real GitHub request
before it can exercise the missing-``upload_url`` refusal -- and the
registered gate runs with ``--network=none``. The harness therefore first
makes a *qualification-baseline* commit that changes no product source and
adds exactly one test double immediately after the existing
``get_release_by_tag`` assignment. Before that baseline is frozen for use,
this module mechanically requires a real, unmodified-except-for-that-double
full-suite run inside ``tester-unified`` (the same M20 preflight the carve's
own text requires) -- never inferred from a restricted cockpit.

**Why a direct isolation probe exists alongside the CLI-driven run.** The
real R0-R3 measurement is driven end-to-end through the installed ``assay``
executable (never assay's own Python API), so this module has no hook into
the live command's working directory the way an internal test's
``process_runner`` double would. To independently prove that the three
declared Topos leaves were genuinely absent from the materialised worktree
-- not merely declared in the lane and trusted -- this module additionally
drives :func:`assay.isolation.prepare_snapshot`/``materialize`` directly
(via the SAME installed wheel, imported from its own venv interpreter) with
the identical policy the lane declares, against the identical controlled
head commit, and inspects the live snapshot root before it is torn down.
This mirrors the carve's own M2/M5 measurement style.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import subprocess
import sys
from pathlib import Path
from typing import Any, Mapping, Sequence, TextIO

#: Re-frozen at the current worktree revision (the carve's own §6 named
#: ``c3b00729...``, which is many commits stale by the time WI-5 landed).
INPUT_REVISION = "ee88ca2328d99ce81046b1ce1e4bb33667093147"
CMRU_TREE = "6fbb3c2c00be81dd893dc11ad0109d14bc846556"
TOPOS_TREE = "31b88ee2ff71566afa4aa23b83ddeff5799ec855"
#: ``git ls-tree -r --name-only INPUT_REVISION | wc -l`` at the frozen input
#: -- the WHOLE repository, not just ``cmru``/``topos``: B006(a) is a
#: repository-wide policy, and the real CMRU tests read real repository-root
#: files outside ``cmru/`` (``cmru.project.sample.toml``, ``cmru.release.sh``).
TRACKED_COUNT = 3213

#: The complete set of tracked absolute-target symlinks in the repository
#: (B006(a) §6/§9 M3), in the exact strictly-ascending order
#: ``IsolationConfig`` requires. Kept tracked throughout -- never deleted --
#: which is the whole point of this harness over ``qualify_topos.py``.
UNSAFE_SYMLINK_OMISSIONS: tuple[str, ...] = (
    "topos/tests/fixtures/inspect_files/_danger/passwd_link",
    "topos/tests/fixtures/inspect_files/cgroup_escape/system.slice/ssh.service/dangerous_link/passwd_escape",
    "topos/tests/fixtures/inspect_files/cgroup_nonreg/system.slice/ssh.service/memory.current",
)
_UNSAFE_SYMLINK_TARGET = "/etc/passwd"

_TESTER_VENV_PYTHON = "/opt/tester-venv/bin/python"

_RELEASE_TEST_PATH = "cmru/tests/test_release_final_contracts.py"
_RELEASE_TEST_NODE = (
    "tests/test_release_final_contracts.py::"
    "test_release_publish_rejects_response_without_upload_coordinate"
)
_ANCHOR_LINE = '    client.get_release_by_tag = lambda tag: {"id": 7}\n'
_REPAIR_LINE = '    client.update_release = lambda *args, **kwargs: {"id": 7}\n'
#: sha256 of the pristine ``_RELEASE_TEST_PATH`` bytes at ``INPUT_REVISION``
#: (measured: ``git show INPUT_REVISION:cmru/tests/test_release_final_contracts.py
#: | sha256sum``). Frozen so drift in the SOURCE file itself is caught before
#: any insertion is attempted, rather than silently patching a moved anchor.
ORIGINAL_TEST_FILE_SHA256 = (
    "6a1dc8c17103304869f8e6ee2866b33950d9a905208cf4948019c2f14c639fc0"
)

_PROBE_MODULE_PATH = "cmru/src/cmru/_b006a_probe.py"
_PROBE_TEST_PATH = "cmru/tests/test_b006a_probe.py"
_LANE_PATH = "cmru/assay.toml"

_PROBE_MODULE_SOURCE = "def matches(value: int) -> bool:\n    return value == 7\n"
_PROBE_TEST_SOURCE = (
    "from cmru._b006a_probe import matches\n"
    "\n"
    "\n"
    "def test_matches() -> None:\n"
    "    assert matches(7)\n"
    "    assert not matches(8)\n"
)

#: Verbatim from B006(a) §6 WI-5, with ``@BASE_OID@`` substituted at commit
#: time. Do not reformat -- the harness's own differential test compares this
#: exact rendering against the file the controlled head commit carries.
_LANE_TEMPLATE = '''schema_version = 2

[lanes.cmru_b006a_qualification]
scope = "S1"
rigor = ["R0", "R1", "R2", "R3"]
enforcement = "gate"
argv = ["/opt/tester-venv/bin/python", "-m", "pytest", "tests", "-q", "--cov=src/cmru", "--cov-branch", "--cov-report=json:.assay/coverage.json"]
env = { PYTHONPATH = "src", PYTHONDONTWRITEBYTECODE = "1" }
env_passthrough = []
budget = "20m"
allow_argv_append = false

[lanes.cmru_b006a_qualification.isolation]
snapshot_selection = "repository-minus-unsafe-symlinks"
unsafe_symlink_omissions = [
  "topos/tests/fixtures/inspect_files/_danger/passwd_link",
  "topos/tests/fixtures/inspect_files/cgroup_escape/system.slice/ssh.service/dangerous_link/passwd_escape",
  "topos/tests/fixtures/inspect_files/cgroup_nonreg/system.slice/ssh.service/memory.current",
]

[lanes.cmru_b006a_qualification.judge]
language = "python"
source_roots = ["src/cmru"]
mode = "changed_lines"
base = "@BASE_OID@"
fail_under = 100.0
allow_excluded = false
require_branch = true

[lanes.cmru_b006a_qualification.judge.coverage]
format = "coverage-py-json"
artifact = ".assay/coverage.json"

[lanes.cmru_b006a_qualification.judge.mutation]
jobs = 1
max_mutants = 2
operators = ["python:compare-swap"]

[lanes.cmru_b006a_qualification.judge.canary]
mechanism = "uncovered-line"
target = "src/cmru/_b006a_probe.py"
'''

_ROOT_DEPENDENCIES = ("cmru.project.sample.toml", "cmru.release.sh")
_ORDINARY_TOPOS_FILE = "topos/src/topos/__init__.py"


class QualificationError(RuntimeError):
    """A frozen qualification premise or independent comparison failed."""


# --- the one subprocess boundary --------------------------------------------


def _run(
    argv: Sequence[str],
    *,
    cwd: Path | None = None,
    env: Mapping[str, str] | None = None,
    timeout: int = 900,
    check: bool = True,
) -> subprocess.CompletedProcess[str]:
    proc = subprocess.run(
        list(argv),
        cwd=cwd,
        env=dict(env) if env is not None else None,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        timeout=timeout,  # hang failsafe only
        check=False,
    )
    if check and proc.returncode:
        raise QualificationError(
            f"command failed ({proc.returncode}): {list(argv)!r}\n"
            f"stdout:\n{proc.stdout[-8000:]}\nstderr:\n{proc.stderr[-8000:]}"
        )
    return proc


def _git(repo: Path, *args: str) -> str:
    return _run(["git", "-C", str(repo), *args]).stdout.strip()


def _fixed_identity() -> dict[str, str]:
    return {
        **os.environ,
        "GIT_AUTHOR_NAME": "Assay B006a WI-5",
        "GIT_AUTHOR_EMAIL": "assay-b006a-wi5@example.invalid",
        "GIT_COMMITTER_NAME": "Assay B006a WI-5",
        "GIT_COMMITTER_EMAIL": "assay-b006a-wi5@example.invalid",
        "GIT_AUTHOR_DATE": "2000-01-01T00:00:00+00:00",
        "GIT_COMMITTER_DATE": "2000-01-01T00:00:00+00:00",
    }


def _consumer_status(source_repo: Path) -> str:
    """The REAL checkout's status over exactly the paths this harness reads
    from (``cmru``, ``topos``), taken before and after qualification, so any
    accidental write to either real tree is caught."""
    return _run(
        ["git", "-C", str(source_repo), "status", "--porcelain=v1", "--", "cmru", "topos"]
    ).stdout


def _failing_node_lines(*outputs: str) -> list[str]:
    lines: list[str] = []
    for output in outputs:
        for line in output.splitlines():
            if line.startswith("FAILED ") or line.startswith("ERROR "):
                lines.append(line)
    return lines


def _diff_names(repo: Path, base: str, head: str, *, pathspec: str | None = None) -> list[str]:
    args = ["diff", "--name-only", base, head]
    if pathspec is not None:
        args += ["--", pathspec]
    return _run(["git", "-C", str(repo), *args]).stdout.splitlines()


# --- input verification ------------------------------------------------------


def verify_pinned_inputs(source_repo: Path) -> None:
    """Refuse drift before creating a scratch repository or environment."""
    resolved = _run(
        ["git", "-C", str(source_repo), "rev-parse", f"{INPUT_REVISION}^{{commit}}"]
    ).stdout.strip()
    if resolved != INPUT_REVISION:
        raise QualificationError(
            f"the pinned INPUT_REVISION {INPUT_REVISION!r} is not reachable exactly "
            f"(resolved to {resolved!r})"
        )
    cmru_tree = _run(["git", "-C", str(source_repo), "rev-parse", f"{INPUT_REVISION}:cmru"]).stdout.strip()
    if cmru_tree != CMRU_TREE:
        raise QualificationError(
            f"the pinned CMRU_TREE does not match: expected {CMRU_TREE!r}, got {cmru_tree!r}"
        )
    topos_tree = _run(["git", "-C", str(source_repo), "rev-parse", f"{INPUT_REVISION}:topos"]).stdout.strip()
    if topos_tree != TOPOS_TREE:
        raise QualificationError(
            f"the pinned TOPOS_TREE does not match: expected {TOPOS_TREE!r}, got {topos_tree!r}"
        )
    pristine = _run(
        ["git", "-C", str(source_repo), "show", f"{INPUT_REVISION}:{_RELEASE_TEST_PATH}"]
    ).stdout
    observed = hashlib.sha256(pristine.encode("utf-8")).hexdigest()
    if observed != ORIGINAL_TEST_FILE_SHA256:
        raise QualificationError(
            f"{_RELEASE_TEST_PATH} at {INPUT_REVISION} drifted from the frozen "
            f"premise: sha256 {observed} != {ORIGINAL_TEST_FILE_SHA256}"
        )


# --- seeding the disposable full-repository checkout -------------------------


def _extract_full_repository(source_repo: Path, destination: Path) -> None:
    """``git archive`` the ENTIRE ``INPUT_REVISION`` tree into *destination*
    via ``tar`` -- never a ``.gitignore``-plus-``topos`` subset, because
    B006(a) is a repository-wide policy and real CMRU tests read real
    repository-root files outside ``cmru/``. Never deletes anything: unlike
    ``qualify_topos.py``, the three unsafe Topos symlinks stay tracked."""
    destination.mkdir(parents=True)
    producer = subprocess.Popen(
        ["git", "-C", str(source_repo), "archive", "--format=tar", INPUT_REVISION],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    assert producer.stdout is not None
    consumer = subprocess.Popen(
        ["tar", "-xf", "-", "-C", str(destination)],
        stdin=producer.stdout,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    producer.stdout.close()
    _, tar_error = consumer.communicate(timeout=180)
    _, git_error = producer.communicate(timeout=180)
    if producer.returncode or consumer.returncode:
        raise QualificationError(
            f"full-repository export failed: git={producer.returncode} {git_error!r}; "
            f"tar={consumer.returncode} {tar_error!r}"
        )


def seed_disposable_repository(source_repo: Path, scratch: Path) -> tuple[Path, str]:
    """Export the pinned FULL tree, init a disposable repo, force-add the
    exact set, verify the three unsafe symlinks are present and unchanged
    (never deleted), and commit with a fixed identity. Returns
    ``(repository, input_oid)``."""
    repository = scratch / "repo"
    _extract_full_repository(source_repo, repository)
    _run(["git", "init", "-q", "-b", "main"], cwd=repository)
    _run(["git", "add", "-f", "-A", "."], cwd=repository)
    indexed = _git(repository, "ls-files").splitlines()
    if len(indexed) != TRACKED_COUNT:
        raise QualificationError(
            f"expected {TRACKED_COUNT} force-indexed paths, found {len(indexed)}"
        )
    observed: dict[str, str] = {}
    for relative in UNSAFE_SYMLINK_OMISSIONS:
        path = repository / relative
        if not path.is_symlink():
            raise QualificationError(
                f"expected {relative!r} to be a tracked symlink in the disposable "
                f"seed (it must stay tracked, never deleted); found none"
            )
        observed[relative] = os.readlink(path)
    expected = {relative: _UNSAFE_SYMLINK_TARGET for relative in UNSAFE_SYMLINK_OMISSIONS}
    if observed != expected:
        raise QualificationError(f"the pinned unsafe-symlink set drifted: {observed}")
    _run(
        ["git", "commit", "-q", "-m", "B006a WI-5 frozen input: full repository checkout"],
        cwd=repository,
        env=_fixed_identity(),
    )
    return repository, _git(repository, "rev-parse", "HEAD")


# --- the qualification-baseline repair and its real-environment preflight ----


def apply_qualification_baseline_repair(repository: Path) -> str:
    """Insert exactly one test double after the existing
    ``get_release_by_tag`` assignment, changing no product source. Compares
    the complete patched bytes to 'frozen blob plus that one insertion'."""
    path = repository / _RELEASE_TEST_PATH
    original = path.read_bytes()
    if hashlib.sha256(original).hexdigest() != ORIGINAL_TEST_FILE_SHA256:
        raise QualificationError(
            f"{_RELEASE_TEST_PATH} in the disposable seed drifted from the frozen "
            f"premise bytes; the qualification-baseline repair is no longer "
            f"provably minimal"
        )
    anchor = _ANCHOR_LINE.encode("utf-8")
    if original.count(anchor) != 1:
        raise QualificationError(
            f"expected exactly one occurrence of the get_release_by_tag anchor "
            f"line in {_RELEASE_TEST_PATH}, found {original.count(anchor)}"
        )
    repair = _REPAIR_LINE.encode("utf-8")
    expected_patched = original.replace(anchor, anchor + repair, 1)
    path.write_bytes(expected_patched)
    _run(["git", "add", _RELEASE_TEST_PATH], cwd=repository)
    _run(
        [
            "git",
            "commit",
            "-q",
            "-m",
            "qualification-baseline: stub GitHubReleases.update_release for the "
            "network-none test boundary",
        ],
        cwd=repository,
        env=_fixed_identity(),
    )
    return _git(repository, "rev-parse", "HEAD")


def run_m20_preflight(repository: Path) -> subprocess.CompletedProcess[str]:
    """The required real-environment probe (B006(a) §6 WI-5's own mechanical
    BLOCK): the complete CMRU suite, unmodified except for the
    qualification-baseline repair, inside the real target interpreter."""
    return _run(
        [_TESTER_VENV_PYTHON, "-m", "pytest", "tests", "-q"],
        cwd=repository / "cmru",
        env={**os.environ, "PYTHONPATH": "src", "PYTHONDONTWRITEBYTECODE": "1"},
        check=False,
    )


def run_repaired_node(repository: Path) -> subprocess.CompletedProcess[str]:
    """Runs the repaired node ALONE, once, requiring PASS (the carve's own
    M15 differential, re-run as part of every harness invocation)."""
    return _run(
        [_TESTER_VENV_PYTHON, "-m", "pytest", "-q", _RELEASE_TEST_NODE],
        cwd=repository / "cmru",
        env={**os.environ, "PYTHONPATH": "src", "PYTHONDONTWRITEBYTECODE": "1"},
        check=False,
    )


# --- the controlled head commit ----------------------------------------------


def build_controlled_head(repository: Path, qualification_baseline_oid: str) -> str:
    """Add exactly the probe module, its test, and the generated lane; commit
    on top of the qualification baseline. Returns the controlled head oid."""
    (repository / _PROBE_MODULE_PATH).write_text(_PROBE_MODULE_SOURCE, encoding="utf-8")
    (repository / _PROBE_TEST_PATH).write_text(_PROBE_TEST_SOURCE, encoding="utf-8")
    lane_text = _LANE_TEMPLATE.replace("@BASE_OID@", qualification_baseline_oid)
    (repository / _LANE_PATH).write_text(lane_text, encoding="utf-8")
    _run(
        ["git", "add", _PROBE_MODULE_PATH, _PROBE_TEST_PATH, _LANE_PATH],
        cwd=repository,
    )
    _run(
        [
            "git",
            "commit",
            "-q",
            "-m",
            "B006a WI-5: controlled cmru/src change plus the qualification lane",
        ],
        cwd=repository,
        env=_fixed_identity(),
    )
    return _git(repository, "rev-parse", "HEAD")


# --- driving the installed wheel ---------------------------------------------


def invoke_qualification_lane(
    assay_executable: Path, repository: Path, artifact_path: Path
) -> tuple[subprocess.CompletedProcess[str], dict[str, Any]]:
    proc = _run(
        [
            str(assay_executable),
            "run",
            "cmru_b006a_qualification",
            "--file",
            str(repository / _LANE_PATH),
            "--verdict-json",
            str(artifact_path),
        ],
        cwd=repository,
        check=False,
    )
    artifact = json.loads(artifact_path.read_text(encoding="utf-8"))
    return proc, artifact


_OMISSION_PROBE_TEMPLATE = """\
import json, os, subprocess
from pathlib import Path, PurePosixPath
from assay.config import IsolationConfig
from assay.isolation import DEFAULT_SNAPSHOT_LIMITS, SnapshotSpec, prepare_snapshot

repo_top = Path({repo_top!r}).resolve(strict=True)
scratch_root = Path({scratch_root!r}).resolve(strict=True)
commit = {commit!r}
omissions = {omissions!r}
root_dependencies = {root_dependencies!r}
ordinary_topos_file = {ordinary_topos_file!r}

policy = IsolationConfig(
    snapshot_selection="repository-minus-unsafe-symlinks",
    unsafe_symlink_omissions=tuple(omissions),
)
spec = SnapshotSpec(
    repo_top=repo_top,
    commit=commit,
    project_prefix=PurePosixPath("cmru"),
    scratch_root=scratch_root,
    snapshot_policy=policy,
    limits=DEFAULT_SNAPSHOT_LIMITS,
)
result = {{}}
with prepare_snapshot(spec, timeout=180.0) as prepared:
    with prepared.materialize(timeout=180.0) as snapshot:
        root = snapshot.root
        result["omitted_absent"] = [not os.path.lexists(root / p) for p in omissions]
        result["cmru_root_present"] = all((root / dep).is_file() for dep in root_dependencies)
        result["topos_ordinary_present"] = (root / ordinary_topos_file).is_file()
        status = subprocess.run(
            ["git", "status", "--porcelain=v1"],
            cwd=str(root), capture_output=True, text=True, check=True,
        )
        result["status_clean"] = status.stdout == ""
print(json.dumps(result))
"""


def probe_snapshot_omissions(
    assay_executable: Path, repository: Path, commit: str, scratch: Path
) -> dict[str, Any]:
    """Drive :func:`assay.isolation.prepare_snapshot`/``materialize``
    directly, through the SAME installed wheel, with the SAME policy the
    qualification lane declares, and inspect the live worktree before it is
    torn down. Independent of, and in addition to, the CLI-driven run."""
    python = assay_executable.parent / "python"
    probe_scratch = scratch / "omission-probe"
    probe_scratch.mkdir(parents=True)
    script = _OMISSION_PROBE_TEMPLATE.format(
        repo_top=str(repository),
        scratch_root=str(probe_scratch),
        commit=commit,
        omissions=list(UNSAFE_SYMLINK_OMISSIONS),
        root_dependencies=list(_ROOT_DEPENDENCIES),
        ordinary_topos_file=_ORDINARY_TOPOS_FILE,
    )
    proc = _run([str(python), "-c", script])
    return json.loads(proc.stdout)


# --- checks --------------------------------------------------------------


def _claim(artifact: Mapping[str, Any], rigor: str) -> Mapping[str, Any]:
    for claim in artifact.get("claims", ()):
        if claim.get("rigor") == rigor:
            return claim
    raise QualificationError(f"no {rigor} claim recorded in the qualification artifact: {artifact}")


def check_qualification_artifact(artifact: Mapping[str, Any]) -> Mapping[str, Any]:
    """All the independent oracle checks WI-5 names, over one artifact.
    Returns the killed R2 mutant's identity, for the caller's own report."""
    if artifact.get("outcome") != "PASS":
        raise QualificationError(
            f"expected the qualification lane to PASS, got "
            f"{artifact.get('outcome')}/{artifact.get('reason_code')}"
        )
    for rigor in ("R0", "R1", "R2", "R3"):
        claim = _claim(artifact, rigor)
        if claim.get("status") != "PASS":
            raise QualificationError(f"{rigor} claim did not PASS: {claim}")

    r1_coverage = _claim(artifact, "R1").get("coverage", {})
    executable = r1_coverage.get("executable")
    if not isinstance(executable, int) or executable <= 0:
        raise QualificationError(f"R1 claim reports executable={executable!r}, expected > 0")

    mutation = _claim(artifact, "R2").get("mutation", {})
    if mutation.get("candidate_count") != 1 or mutation.get("total") != 1:
        raise QualificationError(f"R2 candidate_count/total is not exactly one: {mutation}")
    killed = mutation.get("killed", [])
    if len(killed) != 1:
        raise QualificationError(f"R2 expected exactly one killed mutant identity, got {killed}")
    for bucket in ("survived", "crashed", "budget_exceeded", "equivalent"):
        if mutation.get(bucket):
            raise QualificationError(f"R2 {bucket!r} bucket is not empty: {mutation.get(bucket)}")

    canary = _claim(artifact, "R3").get("canary", {})
    if canary.get("control_outcome") != "PASS":
        raise QualificationError(f"R3 control did not PASS: {canary}")
    if canary.get("transformed_outcome") != "FAIL":
        raise QualificationError(f"R3 transformed did not FAIL: {canary}")
    if (
        canary.get("expected_reason_code") != "UNCOVERED_LINES"
        or canary.get("observed_reason_code") != "UNCOVERED_LINES"
    ):
        raise QualificationError(f"R3 reason code mismatch: {canary}")

    expected_policy = {
        "selection": "repository-minus-unsafe-symlinks",
        "unsafe_symlink_omissions": list(UNSAFE_SYMLINK_OMISSIONS),
    }
    if artifact.get("snapshot_policy") != expected_policy:
        raise QualificationError(
            f"snapshot_policy does not match the declared omissions: "
            f"{artifact.get('snapshot_policy')} != {expected_policy}"
        )
    return killed[0]


def check_snapshot_probe(result: Mapping[str, Any]) -> None:
    omitted_absent = result.get("omitted_absent")
    if not isinstance(omitted_absent, list) or len(omitted_absent) != 3 or not all(omitted_absent):
        raise QualificationError(f"omitted-leaf probe did not observe all three leaves absent: {result}")
    if not result.get("cmru_root_present"):
        raise QualificationError(f"the CMRU root dependencies were not materialised: {result}")
    if not result.get("topos_ordinary_present"):
        raise QualificationError(f"an ordinary Topos file was not materialised: {result}")
    if not result.get("status_clean"):
        raise QualificationError(f"the probed snapshot was not clean: {result}")


# --- orchestration -------------------------------------------------------


def qualify(
    *, source_repo: Path, scratch: Path, current_assay: Path, current_version: str
) -> Mapping[str, Any]:
    before = _consumer_status(source_repo)
    verify_pinned_inputs(source_repo)

    repository, input_oid = seed_disposable_repository(source_repo, scratch)
    qualification_baseline_oid = apply_qualification_baseline_repair(repository)

    preflight = run_m20_preflight(repository)
    if preflight.returncode != 0:
        failing = _failing_node_lines(preflight.stdout, preflight.stderr)
        raise QualificationError(
            "the required tester-unified M20 preflight failed (exit "
            f"{preflight.returncode}); failing node(s):\n"
            + ("\n".join(failing) if failing else "(none identified)")
            + f"\nstdout tail:\n{preflight.stdout[-4000:]}\nstderr tail:\n{preflight.stderr[-4000:]}"
        )

    repaired = run_repaired_node(repository)
    if repaired.returncode != 0:
        raise QualificationError(
            "the repaired node did not PASS in isolation (exit "
            f"{repaired.returncode})\nstdout:\n{repaired.stdout[-4000:]}\n"
            f"stderr:\n{repaired.stderr[-4000:]}"
        )

    repair_diff = _diff_names(repository, input_oid, qualification_baseline_oid)
    if repair_diff != [_RELEASE_TEST_PATH]:
        raise QualificationError(
            f"the qualification-baseline repair touched more than the one test "
            f"file: {repair_diff}"
        )

    head_oid = build_controlled_head(repository, qualification_baseline_oid)

    src_diff = _diff_names(repository, qualification_baseline_oid, head_oid, pathspec="cmru/src")
    if src_diff != [_PROBE_MODULE_PATH]:
        raise QualificationError(
            f"the controlled head's cmru/src diff is not exactly the probe module: {src_diff}"
        )
    full_diff = sorted(_diff_names(repository, qualification_baseline_oid, head_oid))
    expected_full = sorted([_PROBE_MODULE_PATH, _PROBE_TEST_PATH, _LANE_PATH])
    if full_diff != expected_full:
        raise QualificationError(
            f"the controlled head commit changed more than declared: {full_diff}"
        )

    artifact_path = scratch / "verdict.json"
    proc, artifact = invoke_qualification_lane(current_assay, repository, artifact_path)
    if artifact.get("commit") != head_oid:
        raise QualificationError("the artifact commit is not the disposable controlled head")
    if artifact.get("assay_version") != current_version:
        raise QualificationError("the artifact assay_version does not match the installed owner")
    if proc.returncode != artifact.get("exit_code"):
        raise QualificationError(
            "the process exit code disagrees with the artifact's own exit_code"
        )
    killed_identity = check_qualification_artifact(artifact)

    probe_result = probe_snapshot_omissions(current_assay, repository, head_oid, scratch)
    check_snapshot_probe(probe_result)

    if _git(repository, "status", "--porcelain=v1") != "":
        raise QualificationError("the disposable repository was left dirty after qualification")

    after = _consumer_status(source_repo)
    if before != after:
        raise QualificationError("the real cmru/topos checkout changed during qualification")

    return {
        "input_oid": input_oid,
        "qualification_baseline_oid": qualification_baseline_oid,
        "head_oid": head_oid,
        "artifact": artifact,
        "killed_identity": killed_identity,
        "probe": probe_result,
    }


def print_receipt(result: Mapping[str, Any], *, stream: TextIO) -> None:
    """A human-readable receipt of exactly what was checked, to STDERR only
    -- ``main()``'s stdout contract is the bare marker line and nothing
    else, since the registered gate captures it via command substitution
    and compares it for exact equality."""
    artifact = result["artifact"]
    canary = _claim(artifact, "R3").get("canary")
    print("--- B006(a) WI-5 qualification receipt ---", file=stream)
    print(f"input_oid={result['input_oid']}", file=stream)
    print(f"qualification_baseline_oid={result['qualification_baseline_oid']}", file=stream)
    print(f"head_oid={result['head_oid']}", file=stream)
    print(f"outcome={artifact.get('outcome')} exit_code={artifact.get('exit_code')}", file=stream)
    for rigor in ("R0", "R1", "R2", "R3"):
        claim = _claim(artifact, rigor)
        print(f"claim[{rigor}]=status={claim.get('status')}", file=stream)
    print(f"r2_killed_identity={json.dumps(result['killed_identity'])}", file=stream)
    print(f"r3_canary={json.dumps(canary)}", file=stream)
    print(f"snapshot_policy={json.dumps(artifact.get('snapshot_policy'))}", file=stream)
    print(f"omission_probe={json.dumps(result['probe'])}", file=stream)


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="qualify_cmru_b006a.py")
    parser.add_argument("--source-repo", type=Path, required=True)
    parser.add_argument("--scratch", type=Path, required=True)
    parser.add_argument("--current-assay", type=Path, required=True)
    parser.add_argument("--current-version", required=True)
    args = parser.parse_args(argv)
    if args.scratch.exists():
        parser.error("--scratch must be absent")
    result = qualify(
        source_repo=args.source_repo,
        scratch=args.scratch,
        current_assay=args.current_assay,
        current_version=args.current_version,
    )
    print_receipt(result, stream=sys.stderr)
    print("ASSAY_B006A_CMRU_QUALIFIED=1")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
