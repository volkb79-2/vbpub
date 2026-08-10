#!/usr/bin/env python3
"""P25 production-harness skeleton; copy to ``gate/python/qualify_topos.py``.

The public/private boundary and every observable value are frozen here.  TODO
bodies are implementation work; changing signatures, pins, scenarios, or
terminal comparison is not.
"""

from __future__ import annotations

import argparse
import copy
import hashlib
import json
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping, Sequence

INPUT_REVISION = "9f522a72d37b9cb5beb1939ceca1978c9fc4ef23"
TOPOS_TREE = "1bc8a51296b74e536bf60b534efb2fc938dcc389"
TOPOS_TRACKED_COUNT = 966
BASELINE_INDEX_COUNT = 965
RELEASE_VERSION = "1.2.5"
RELEASE_WHEEL = "assay-1.2.5-py3-none-any.whl"
RELEASE_SHA256 = "a0f8d28e4f6359e90616343badcf3c663eb7e2075c1a521bf9da8afd7002dc86"
ABSOLUTE_SYMLINKS: Mapping[str, str] = {
    "topos/tests/fixtures/inspect_files/_danger/passwd_link": "/etc/passwd",
    "topos/tests/fixtures/inspect_files/cgroup_escape/system.slice/ssh.service/dangerous_link/passwd_escape": "/etc/passwd",
    "topos/tests/fixtures/inspect_files/cgroup_nonreg/system.slice/ssh.service/memory.current": "/etc/passwd",
}
FIXTURE_ROOT = Path(__file__).resolve().parent / "fixtures" / "P25"
RELEASE_ROOT = Path(__file__).resolve().parent / "release" / "P25"


class QualificationError(RuntimeError):
    """A frozen qualification premise or independent comparison failed."""


@dataclass(frozen=True, kw_only=True)
class ScenarioSpec:
    name: str
    probe_fixture: str
    test_fixture: str
    source_target: str
    argv_tail: tuple[str, ...]
    allow_excluded: bool
    expected_exit: int
    expected_outcome: str
    expected_reason: str | None
    compare_with_topos: bool


@dataclass(frozen=True, kw_only=True)
class ScenarioResult:
    spec: ScenarioSpec
    base_oid: str
    head_oid: str
    artifact: Mapping[str, Any]
    comparator: Mapping[str, Any] | None
    witness_sha256: str | None
    consumer_clean: bool


PRIMARY = ScenarioSpec(
    name="current-full-pass",
    probe_fixture="probe-pass.py",
    test_fixture="test-probe-pass.py",
    source_target="_assay_probe.py",
    argv_tail=("topos/tests", "-q", "-n", "auto"),
    allow_excluded=True,
    expected_exit=0,
    expected_outcome="PASS",
    expected_reason=None,
    compare_with_topos=True,
)
RELEASE_SMOKE = ScenarioSpec(
    name="release-targeted-pass",
    probe_fixture="probe-pass.py",
    test_fixture="test-probe-pass.py",
    source_target="_assay_probe.py",
    argv_tail=("topos/tests/test_config.py", "topos/tests/test_assay_probe.py", "-q", "-n", "2"),
    allow_excluded=True,
    expected_exit=0,
    expected_outcome="PASS",
    expected_reason=None,
    compare_with_topos=True,
)
MISSING = ScenarioSpec(
    name="missing-line",
    probe_fixture="probe-missing.py",
    test_fixture="test-probe-missing.py",
    source_target="_assay_probe.py",
    argv_tail=("topos/tests/test_config.py", "topos/tests/test_assay_probe.py", "-q", "-n", "2"),
    allow_excluded=True,
    expected_exit=1,
    expected_outcome="FAIL",
    expected_reason="UNCOVERED_LINES",
    compare_with_topos=True,
)
EXCLUDED = ScenarioSpec(
    name="excluded-forbidden",
    probe_fixture="probe-pass.py",
    test_fixture="test-probe-pass.py",
    source_target="_assay_probe.py",
    argv_tail=("topos/tests/test_config.py", "topos/tests/test_assay_probe.py", "-q", "-n", "2"),
    allow_excluded=False,
    expected_exit=1,
    expected_outcome="FAIL",
    expected_reason="EXCLUDED_LINES",
    compare_with_topos=False,
)
COMMENT_ONLY = ScenarioSpec(
    name="comment-only",
    probe_fixture="comment-only.py",
    test_fixture="test-comment-only.py",
    source_target="_assay_comment.py",
    argv_tail=("topos/tests/test_config.py", "topos/tests/test_assay_probe.py", "-q", "-n", "2"),
    allow_excluded=True,
    expected_exit=0,
    expected_outcome="PASS",
    expected_reason=None,
    compare_with_topos=True,
)
SCENARIOS = (PRIMARY, RELEASE_SMOKE, MISSING, EXCLUDED, COMMENT_ONLY)


def _run(
    argv: Sequence[str],
    *,
    cwd: Path | None = None,
    timeout: int = 900,
    check: bool = True,
) -> subprocess.CompletedProcess[str]:
    proc = subprocess.run(
        list(argv),
        cwd=cwd,
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


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        while chunk := stream.read(1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()


def verify_pinned_inputs(source_repo: Path) -> None:
    """Refuse drift before creating a scratch repository or environment."""
    revision = _run(["git", "-C", str(source_repo), "rev-parse", f"{INPUT_REVISION}^{{commit}}"])
    if revision.stdout.strip() != INPUT_REVISION:
        raise QualificationError("the pinned P25 input revision is not reachable exactly")
    tree = _run(["git", "-C", str(source_repo), "rev-parse", f"{INPUT_REVISION}:topos"])
    if tree.stdout.strip() != TOPOS_TREE:
        raise QualificationError("the pinned Topos tree OID does not match")
    wheel = RELEASE_ROOT / RELEASE_WHEEL
    if _sha256(wheel) != RELEASE_SHA256:
        raise QualificationError("the locked P25 release wheel hash does not match")


def install_locked_release(*, source_repo: Path, scratch: Path) -> tuple[Path, str]:
    """Install only P24-helper-verified bytes with pip ``--require-hashes``."""
    raise NotImplementedError("TODO P25: implement the exact locked release installation recipe")


def materialize_scenario(
    *, source_repo: Path, scratch: Path, spec: ScenarioSpec
) -> tuple[Path, Path, Path, str, str]:
    """Return ``(repo, witness, pytest_log, base_oid, head_oid)``.

    Export only ``.gitignore`` plus the pinned ``topos`` tree; verify all 966
    input entries and the three exact absolute links; delete exactly those
    links; force-add the exact remaining tracked set plus ``.assay/.gitignore``
    to a deterministic baseline (965 entries); add only the frozen wrapper,
    probe, test, and complete lane to deterministic HEAD.
    """
    raise NotImplementedError("TODO P25: implement exact pinned scenario materialization")


def run_scenario(
    *, source_repo: Path, scratch: Path, assay_executable: Path, assay_version: str, spec: ScenarioSpec
) -> ScenarioResult:
    """Run one scenario and compare the complete artifact and independent result."""
    raise NotImplementedError("TODO P25: run and independently compare one scenario")


def normalize_artifact(
    document: Mapping[str, Any],
    *,
    assay_version: str,
    base_oid: str,
    head_oid: str,
    witness: Path,
    pytest_log: Path,
) -> dict[str, Any]:
    """Replace only runtime identities whose real value is checked separately."""
    normalized = copy.deepcopy(dict(document))
    if normalized.get("assay_version") != assay_version:
        raise QualificationError("artifact assay_version is not the installed version")
    if normalized.get("commit") != head_oid:
        raise QualificationError("artifact commit is not disposable HEAD")
    if normalized.get("judgment", {}).get("r1", {}).get("base") != base_oid:
        raise QualificationError("artifact judgment base is not the seeded base")
    for field in ("started", "ended"):
        if not isinstance(normalized.get(field), str) or not normalized[field]:
            raise QualificationError(f"artifact {field} is not a nonempty timestamp")
    env = normalized.get("env_declared")
    effective = normalized.get("env_effective")
    if not isinstance(env, dict) or env != effective:
        raise QualificationError("declared/effective environment mismatch")
    if env.get("ASSAY_P25_WITNESS") != str(witness):
        raise QualificationError("artifact witness path differs from the committed plan")
    if env.get("ASSAY_P25_LOG") != str(pytest_log):
        raise QualificationError("artifact pytest-log path differs from the committed plan")
    normalized["assay_version"] = "@ASSAY_VERSION@"
    normalized["commit"] = "@HEAD_OID@"
    normalized["started"] = "@STARTED@"
    normalized["ended"] = "@ENDED@"
    normalized["judgment"]["r1"]["base"] = "@BASE_OID@"
    for field in ("env_declared", "env_effective"):
        normalized[field]["ASSAY_P25_WITNESS"] = "@WITNESS_PATH@"
        normalized[field]["ASSAY_P25_LOG"] = "@PYTEST_LOG@"
    return normalized


def compare_complete_artifact(
    *,
    actual: Mapping[str, Any],
    template: Path,
    assay_version: str,
    base_oid: str,
    head_oid: str,
    witness: Path,
    pytest_log: Path,
) -> None:
    normalized = normalize_artifact(
        actual,
        assay_version=assay_version,
        base_oid=base_oid,
        head_oid=head_oid,
        witness=witness,
        pytest_log=pytest_log,
    )
    expected = json.loads(template.read_text(encoding="utf-8"))
    if normalized != expected:
        raise QualificationError("the complete v4 artifact differs from the locked hand template")


def qualify(
    *,
    source_repo: Path,
    scratch: Path,
    current_assay: Path,
    current_version: str,
) -> tuple[ScenarioResult, ...]:
    """Run the current full proof, release smoke, and locked negative matrix."""
    raise NotImplementedError("TODO P25: orchestrate both explicit Assay installations and all scenarios")


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="qualify_topos.py")
    parser.add_argument("--source-repo", type=Path, required=True)
    parser.add_argument("--scratch", type=Path, required=True)
    parser.add_argument("--current-assay", type=Path, required=True)
    parser.add_argument("--current-version", required=True)
    args = parser.parse_args(argv)
    if args.scratch.exists():
        parser.error("--scratch must be absent")
    verify_pinned_inputs(args.source_repo)
    qualify(
        source_repo=args.source_repo,
        scratch=args.scratch,
        current_assay=args.current_assay,
        current_version=args.current_version,
    )
    print("ASSAY_P25_TOPOS_QUALIFIED=1")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
