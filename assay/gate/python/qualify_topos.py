#!/usr/bin/env python3
"""P25 real Python-project qualification harness.

Proves that the currently installed Assay wheel (and, separately, a
clean-tagged 1.2.5 release wheel) agrees with an independent, unmodified
Topos evaluator on a pinned, disposable, prospective-consumer Topos tree.
This is qualification, not adoption: current Topos commits three absolute
``/etc/passwd`` symlink fixtures that P22 correctly refuses for any R1+ lane,
so this harness deletes exactly those three paths from its own disposable
baseline and proves the full suite answer is otherwise unchanged. It never
writes to the real Topos checkout, never imports ``assay`` from source, and
never launches Topos's own outer Docker gate.
"""

from __future__ import annotations

import argparse
import copy
import hashlib
import importlib.util
import json
import os
import shutil
import subprocess
import sys
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

# The carver-owned expected/hand-manifest evidence is read directly from the
# locked packet rather than duplicated into a second production copy -- it is
# reference material, never mutated at runtime, exactly like the fixture
# manifest's own self-hash. `qualify_topos.py` lives at `gate/python/`, two
# levels under the project root.
_PROJECT_ROOT = Path(__file__).resolve().parents[2]
# P33/A-226: the expectations move to P33's carver-supplied v5 SIBLINGS. P25's
# own `expected/*-v4-template.json` files stay frozen and unedited as the
# historical record of what P25 actually proved (A-222) -- rewriting them to
# v5 would falsify that record, claiming a merged package was accepted
# against a contract that did not exist when it was accepted. A v4->v5
# projection computed inside this harness was considered and rejected: an
# unfrozen transform is machinery to be trusted, whereas a frozen expectation
# is evidence to be checked, and the whole point of comparing against a
# locked template is that nothing in the run under test produced it.
_EXPECTED_ROOT = _PROJECT_ROOT / "nyxloom-trove" / "carve-assets" / "P33" / "expected"
_QUALIFICATION_MANIFEST = (
    _PROJECT_ROOT / "nyxloom-trove" / "carve-assets" / "P25" / "qualification-manifest.json"
)

# Verified tester-unified image facts (handoff §4) -- not defaults. A test
# harness running outside that image may monkeypatch these two module
# attributes to exercise this file's real logic locally; production code
# never branches on their value.
_TESTER_VENV_PYTHON = "/opt/tester-venv/bin/python"
_TESTER_VENV_PATH = "/opt/tester-venv/bin:/usr/local/bin:/usr/bin:/bin"

_FIXED_GIT_DATE = "2000-01-01T00:00:00+00:00"

# Disposable integrity-negative wrappers. These replace the real, locked
# `coverage-witness.py` for exactly one scenario each; they are never copied
# from `fixtures/`, only written into a throwaway scratch repository, and
# they never touch the real Topos checkout (`source_repo`).
_NO_COVERAGE_WRAPPER = '''"""P25 integrity-negative wrapper: never produces a coverage artifact."""
import subprocess
import sys


def main() -> int:
    proc = subprocess.run([sys.executable, "-m", "pytest", *sys.argv[1:]], check=False)
    return proc.returncode


if __name__ == "__main__":
    raise SystemExit(main())
'''

_DIRTY_WRAPPER = '''"""P25 integrity-negative wrapper: mutates a tracked file without committing."""
from pathlib import Path


def main() -> int:
    target = Path("topos/src/topos/_assay_probe.py")
    with target.open("a", encoding="utf-8") as stream:
        stream.write("# P25 wrapper mutation (uncommitted)\\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
'''

_HEAD_MOVE_WRAPPER = '''"""P25 integrity-negative wrapper: commits inside the snapshot, moving HEAD."""
import os
import subprocess
from pathlib import Path


def main() -> int:
    target = Path("topos/src/topos/_assay_probe.py")
    with target.open("a", encoding="utf-8") as stream:
        stream.write("# P25 wrapper HEAD move\\n")
    identity = {
        **os.environ,
        "GIT_AUTHOR_NAME": "Assay P25",
        "GIT_AUTHOR_EMAIL": "assay-p25@example.invalid",
        "GIT_COMMITTER_NAME": "Assay P25",
        "GIT_COMMITTER_EMAIL": "assay-p25@example.invalid",
    }
    subprocess.run(["git", "add", "topos/src/topos/_assay_probe.py"], check=True)
    subprocess.run(
        ["git", "commit", "-q", "-m", "P25 wrapper HEAD move"], check=True, env=identity
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
'''


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


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        while chunk := stream.read(1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()


def _git(repo: Path, *args: str) -> str:
    return _run(["git", "-C", str(repo), *args]).stdout.strip()


def _consumer_status(source_repo: Path) -> str:
    """A byte snapshot of the REAL checkout's status, taken before and after
    qualification runs, so any accidental write to it is caught (§2)."""
    return _run(["git", "-C", str(source_repo), "status", "--porcelain=v1"]).stdout


def _fixed_identity() -> dict[str, str]:
    return {
        **os.environ,
        "GIT_AUTHOR_NAME": "Assay P25",
        "GIT_AUTHOR_EMAIL": "assay-p25@example.invalid",
        "GIT_COMMITTER_NAME": "Assay P25",
        "GIT_COMMITTER_EMAIL": "assay-p25@example.invalid",
        "GIT_AUTHOR_DATE": _FIXED_GIT_DATE,
        "GIT_COMMITTER_DATE": _FIXED_GIT_DATE,
    }


def _copy_fixture(name: str, target: Path) -> None:
    target.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(FIXTURE_ROOT / name, target)


def _extract_pin(source_repo: Path, destination: Path) -> None:
    """Export the pinned ``.gitignore`` + ``topos`` tree, verify the exact
    966-entry tracked set and the exact three absolute symlinks, then delete
    exactly those three (A-202/A-203)."""
    destination.mkdir(parents=True)
    producer = subprocess.Popen(
        ["git", "-C", str(source_repo), "archive", "--format=tar", INPUT_REVISION, ".gitignore", "topos"],
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
    _, tar_error = consumer.communicate(timeout=120)
    _, git_error = producer.communicate(timeout=120)
    if producer.returncode or consumer.returncode:
        raise QualificationError(
            f"pinned Topos export failed: git={producer.returncode} {git_error!r}; "
            f"tar={consumer.returncode} {tar_error!r}"
        )

    tracked = _run(
        ["git", "-C", str(source_repo), "ls-tree", "-r", "--name-only", INPUT_REVISION, "--", "topos"]
    ).stdout.splitlines()
    if len(tracked) != TOPOS_TRACKED_COUNT:
        raise QualificationError(
            f"expected {TOPOS_TRACKED_COUNT} pinned Topos tracked entries, found {len(tracked)}"
        )

    observed_absolute: dict[str, str] = {}
    for relative in tracked:
        path = destination / relative
        if path.is_symlink() and os.path.isabs(os.readlink(path)):
            observed_absolute[relative] = os.readlink(path)
    if observed_absolute != dict(ABSOLUTE_SYMLINKS):
        raise QualificationError(
            f"the pinned absolute-symlink adoption-precondition set drifted: {observed_absolute}"
        )
    for relative in ABSOLUTE_SYMLINKS:
        (destination / relative).unlink()

    remaining_symlinks = [rel for rel in tracked if (destination / rel).is_symlink()]
    if len(remaining_symlinks) != 5:
        raise QualificationError(
            f"expected 5 retained relative Topos symlinks after the deletion, found {len(remaining_symlinks)}"
        )


def _seed_baseline(source_repo: Path, repository: Path) -> str:
    """Extract the pin, init a disposable repo, force-add the exact exported
    set (A-203: ordinary ``git add`` reinterprets the carried root
    ``.gitignore`` and silently drops four tracked fixtures), and commit with
    a fixed identity. Returns the baseline commit OID."""
    _extract_pin(source_repo, repository)
    _run(["git", "init", "-q", "-b", "main"], cwd=repository)
    (repository / ".assay").mkdir()
    (repository / ".assay" / ".gitignore").write_text("*\n!.gitignore\n", encoding="utf-8")
    _run(["git", "add", "-f", ".gitignore", ".assay/.gitignore", "topos"], cwd=repository)
    indexed = _git(repository, "ls-files").splitlines()
    if len(indexed) != BASELINE_INDEX_COUNT:
        raise QualificationError(
            f"expected {BASELINE_INDEX_COUNT} force-indexed baseline paths, found {len(indexed)}"
        )
    _run(
        ["git", "commit", "-q", "-m", "pinned Topos compatibility baseline"],
        cwd=repository,
        env=_fixed_identity(),
    )
    return _git(repository, "rev-parse", "HEAD")


def _write_lane(
    repository: Path,
    *,
    base: str,
    witness: Path,
    pytest_log: Path,
    argv_tail: Sequence[str],
    allow_excluded: bool,
    source_roots: str = "topos/src/topos",
) -> None:
    argv = [_TESTER_VENV_PYTHON, "topos/tools/assay_p25_coverage.py", *argv_tail]
    quoted_argv = ", ".join(json.dumps(item) for item in argv)
    text = f'''schema_version = 2
[lanes.topos-qualification]
scope = "S1"
rigor = ["R0", "R1"]
enforcement = "gate"
argv = [{quoted_argv}]
env = {{PYTHONPATH = "topos/src:topos", ASSAY_P25_WITNESS = {json.dumps(str(witness))}, ASSAY_P25_LOG = {json.dumps(str(pytest_log))}, PATH = {json.dumps(_TESTER_VENV_PATH)}, HOME = "/home/tester", XDG_CACHE_HOME = "/home/tester/.cache", XDG_CONFIG_HOME = "/home/tester/.config", XDG_STATE_HOME = "/home/tester/.local/state", LANG = "C.UTF-8", LC_ALL = "C.UTF-8"}}
env_passthrough = []
budget = "15m"
allow_argv_append = false
[lanes.topos-qualification.isolation]
snapshot_selection = "repository"
[lanes.topos-qualification.judge]
language = "python"
source_roots = [{json.dumps(source_roots)}]
base = {json.dumps(base)}
fail_under = 100.0
allow_excluded = {str(allow_excluded).lower()}
[lanes.topos-qualification.judge.coverage]
format = "coverage-py-json"
artifact = ".assay/topos-coverage.json"
'''
    (repository / "assay.toml").write_text(text, encoding="utf-8")


def _materialize_negative(
    source_repo: Path,
    scratch: Path,
    name: str,
    *,
    probe_fixture: str,
    test_fixture: str,
    argv_tail: Sequence[str],
    allow_excluded: bool = True,
    wrapper_fixture: str | None = "coverage-witness.py",
    wrapper_text: str | None = None,
    source_target: str = "_assay_probe.py",
    source_roots: str = "topos/src/topos",
    base_override: str | None = None,
    tag_head_as: str | None = None,
    tag_base_as: str | None = None,
) -> tuple[Path, Path, Path, str, str]:
    """Shared construction for both the five named scenarios and the
    integrity-negative matrix -- only the wrapper/root/base declaration
    varies. Returns ``(repo, witness, pytest_log, base_oid, head_oid)``."""
    root = scratch / name
    repo = root / "repo"
    witness = root / "witness.json"
    pytest_log = root / "pytest.log"
    base_oid = _seed_baseline(source_repo, repo)
    # (P33/CA8) A tag on the BASE commit, so a lane can declare a symbolic
    # base that is genuinely NOT HEAD -- the one shape `tag_head_as` cannot
    # produce, since `_check_base_is_head` already occupies that case and
    # refuses before any base is ever recorded.
    if tag_base_as is not None:
        _run(["git", "tag", tag_base_as, base_oid], cwd=repo, env=_fixed_identity())
    _copy_fixture(probe_fixture, repo / "topos/src/topos" / source_target)
    _copy_fixture(test_fixture, repo / "topos/tests/test_assay_probe.py")
    wrapper_path = repo / "topos/tools/assay_p25_coverage.py"
    wrapper_path.parent.mkdir(parents=True, exist_ok=True)
    if wrapper_text is not None:
        wrapper_path.write_text(wrapper_text, encoding="utf-8")
    else:
        assert wrapper_fixture is not None
        _copy_fixture(wrapper_fixture, wrapper_path)
    declared_base = base_override if base_override is not None else base_oid
    _write_lane(
        repo,
        base=declared_base,
        witness=witness,
        pytest_log=pytest_log,
        argv_tail=argv_tail,
        allow_excluded=allow_excluded,
        source_roots=source_roots,
    )
    _run(
        [
            "git",
            "add",
            "assay.toml",
            f"topos/src/topos/{source_target}",
            "topos/tests/test_assay_probe.py",
            "topos/tools/assay_p25_coverage.py",
        ],
        cwd=repo,
    )
    _run(["git", "commit", "-q", "-m", f"P25 {name}"], cwd=repo, env=_fixed_identity())
    head_oid = _git(repo, "rev-parse", "HEAD")
    if tag_head_as is not None:
        _run(["git", "tag", tag_head_as], cwd=repo, env=_fixed_identity())
    return repo, witness, pytest_log, base_oid, head_oid


def _topos_comparator(repository: Path, base: str, witness: Path) -> dict[str, Any]:
    """Feed the exact copied coverage bytes and the base..HEAD diff into the
    UNMODIFIED, committed ``topos/tools/coverage_gate.py`` (A-204)."""
    module_path = repository / "topos" / "tools" / "coverage_gate.py"
    spec = importlib.util.spec_from_file_location(
        f"p25_topos_coverage_gate_{id(repository)}", module_path
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    diff_text = _run(
        ["git", "-C", str(repository), "diff", "--relative", "--unified=0", base, "HEAD", "--", "topos/src/topos"]
    ).stdout
    added = module.parse_added_lines(diff_text)
    profile = json.loads(witness.read_text(encoding="utf-8"))["files"]
    verdict = module.evaluate(added, profile, "topos/src/topos", 100.0)
    return {
        "passed": verdict.passed,
        "changed_executable": verdict.changed_executable,
        "covered": verdict.covered,
        "pct": verdict.pct,
        "uncovered": {path: sorted(lines) for path, lines in sorted(verdict.uncovered.items())},
        "files_missing_coverage": verdict.files_missing_coverage,
    }


def _expected_comparator(spec: ScenarioSpec) -> dict[str, Any] | None:
    """The copied Topos evaluator's own frozen numbers for ``spec``.

    Derived from the carver-owned literal hand manifest, never from anything
    Assay produced. ``passed`` alone is NOT an oracle: Topos computes
    ``pct = 100.0`` whenever ``changed_executable == 0``, so a scenario that
    measured nothing agrees with a truthful 5/5 run on the boolean. Returns
    ``None`` for the frozen `compare_with_topos=False` exclusion-capability
    asymmetry, whose Topos result is recorded and never terminal-compared.
    """
    if not spec.compare_with_topos:
        return None
    manifest = json.loads(_QUALIFICATION_MANIFEST.read_text(encoding="utf-8"))
    entry = {
        "probe-pass.py": manifest["pass_probe"],
        "probe-missing.py": manifest["missing_probe"],
        "comment-only.py": manifest["comment_only_probe"],
    }[spec.probe_fixture]
    missing = entry.get("missing_lines") or []
    return {
        "passed": not missing,
        "changed_executable": entry["changed_executable"],
        "covered": entry["covered"],
        "uncovered": {entry["path"]: list(missing)} if missing else {},
        "files_missing_coverage": [],
    }


def _invoke(assay_executable: Path, repo: Path, artifact_path: Path) -> tuple[subprocess.CompletedProcess[str], dict[str, Any]]:
    proc = _run(
        [str(assay_executable), "run", "topos-qualification", "--verdict-json", str(artifact_path)],
        cwd=repo,
        check=False,
    )
    artifact = json.loads(artifact_path.read_text(encoding="utf-8"))
    return proc, artifact


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
    wheel = RELEASE_ROOT / RELEASE_WHEEL
    manifest = RELEASE_ROOT / "release-manifest.json"
    base_python = Path(
        _run([_TESTER_VENV_PYTHON, "-c", "import sys; print(sys.base_prefix + '/bin/python3')"]).stdout.strip()
    )
    venv = scratch / "release-venv"
    _run([str(base_python), "-m", "venv", str(venv)])
    helper = source_repo / "assay" / "gate" / "distribution" / "release_wheel.py"
    verified = _run(
        [str(venv / "bin" / "python"), str(helper), "verify", "--wheel", str(wheel), "--manifest", str(manifest)]
    )
    requirement = scratch / "release-requirement.txt"
    requirement.write_text(verified.stdout, encoding="utf-8")
    _run(
        [
            str(venv / "bin" / "python"),
            "-m",
            "pip",
            "install",
            "--no-index",
            "--require-hashes",
            "-r",
            str(requirement),
        ]
    )
    tester_site = _run(
        [_TESTER_VENV_PYTHON, "-c", "import sysconfig; print(sysconfig.get_paths()['purelib'])"]
    ).stdout.strip()
    run_site = _run(
        [str(venv / "bin" / "python"), "-c", "import sysconfig; print(sysconfig.get_paths()['purelib'])"]
    ).stdout.strip()
    (Path(run_site) / "tester_unified_site.pth").write_text(tester_site + "\n", encoding="utf-8")
    purity = _run(
        [
            str(venv / "bin" / "python"),
            "-c",
            "import json, assay; from importlib.metadata import version; "
            "print(json.dumps([version('assay'), assay.__version__, assay.__file__]))",
        ]
    )
    installed, runtime, location = json.loads(purity.stdout)
    if not (installed == runtime == RELEASE_VERSION):
        raise QualificationError(
            f"release venv version identity mismatch: installed={installed!r} "
            f"runtime={runtime!r} expected={RELEASE_VERSION!r}"
        )
    if not location.startswith(str(venv) + "/"):
        raise QualificationError(f"release assay was imported from outside its own venv: {location}")
    if location.startswith(str(source_repo / "assay" / "src") + "/"):
        raise QualificationError(f"release assay was imported from the source tree: {location}")
    return venv / "bin" / "assay", installed


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
    return _materialize_negative(
        source_repo,
        scratch,
        spec.name,
        probe_fixture=spec.probe_fixture,
        test_fixture=spec.test_fixture,
        argv_tail=spec.argv_tail,
        allow_excluded=spec.allow_excluded,
        wrapper_fixture="coverage-witness.py",
        source_target=spec.source_target,
    )


def run_scenario(
    *, source_repo: Path, scratch: Path, assay_executable: Path, assay_version: str, spec: ScenarioSpec
) -> ScenarioResult:
    """Run one scenario and compare the complete artifact and independent result."""
    repo, witness, _pytest_log, base_oid, head_oid = materialize_scenario(
        source_repo=source_repo, scratch=scratch, spec=spec
    )
    artifact_path = scratch / spec.name / "verdict.json"
    proc, artifact = _invoke(assay_executable, repo, artifact_path)

    if artifact.get("commit") != head_oid:
        raise QualificationError(f"scenario {spec.name!r}: artifact commit is not the seeded disposable HEAD")
    if artifact.get("assay_version") != assay_version:
        raise QualificationError(
            f"scenario {spec.name!r}: artifact assay_version does not match the installed owner"
        )
    if proc.returncode != artifact.get("exit_code"):
        raise QualificationError(
            f"scenario {spec.name!r}: process exit code disagrees with the artifact's own exit_code"
        )
    if proc.returncode != spec.expected_exit:
        raise QualificationError(
            f"scenario {spec.name!r}: expected exit {spec.expected_exit}, got {proc.returncode}"
        )
    if artifact.get("outcome") != spec.expected_outcome:
        raise QualificationError(
            f"scenario {spec.name!r}: expected outcome {spec.expected_outcome!r}, got {artifact.get('outcome')!r}"
        )
    if spec.expected_reason is not None and artifact.get("reason_code") != spec.expected_reason:
        raise QualificationError(
            f"scenario {spec.name!r}: expected reason_code {spec.expected_reason!r}, "
            f"got {artifact.get('reason_code')!r}"
        )

    comparator: dict[str, Any] | None = None
    witness_sha256: str | None = None
    if witness.exists():
        witness_sha256 = _sha256(witness)
        comparator = _topos_comparator(repo, base_oid, witness)
        expected_comparator = _expected_comparator(spec)
        if expected_comparator is None:
            # `compare_with_topos=False` is the frozen exclusion-capability
            # asymmetry: Topos cannot express exclusion provenance, so its
            # PASS is recorded, never compared as a terminal.
            if comparator["passed"] is not True:
                raise QualificationError(
                    f"scenario {spec.name!r}: the expected Topos exclusion-capability "
                    f"asymmetry requires a Topos PASS, got {comparator['passed']}"
                )
        else:
            observed = {field: comparator[field] for field in expected_comparator}
            if observed != expected_comparator:
                raise QualificationError(
                    f"scenario {spec.name!r}: copied Topos evaluator disagrees with the "
                    f"locked hand manifest: expected {expected_comparator}, got {observed}"
                )
    elif spec.expected_exit == 0 or spec.compare_with_topos:
        raise QualificationError(f"scenario {spec.name!r}: expected a coverage witness but none was produced")

    consumer_clean = _git(repo, "status", "--porcelain=v1") == ""
    if not consumer_clean:
        raise QualificationError(f"scenario {spec.name!r}: disposable repository is not clean after the run")

    return ScenarioResult(
        spec=spec,
        base_oid=base_oid,
        head_oid=head_oid,
        artifact=artifact,
        comparator=comparator,
        witness_sha256=witness_sha256,
        consumer_clean=consumer_clean,
    )


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
    # P33/V5-1: the comparison commit hoisted out of `judgment.r1` into the
    # shared `judgment.resolved`, which is where an R0,R2 lane can record one
    # at all.
    if normalized.get("judgment", {}).get("resolved", {}).get("base") != base_oid:
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
    normalized["judgment"]["resolved"]["base"] = "@BASE_OID@"
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


def _check_line_manifests(results: Mapping[str, ScenarioResult]) -> None:
    """Cross-check each scenario's coverage numbers against the carver-owned
    literal hand manifest (never against anything Assay itself produced)."""
    manifest = json.loads(_QUALIFICATION_MANIFEST.read_text(encoding="utf-8"))

    pass_coverage = results[PRIMARY.name].artifact["claims"][1]["coverage"]
    pass_probe = manifest["pass_probe"]
    if (pass_coverage["covered"], pass_coverage["changed_executable"]) != (
        pass_probe["covered"],
        pass_probe["changed_executable"],
    ):
        raise QualificationError("current-full-pass coverage numbers diverge from the locked hand manifest")
    if pass_coverage["excluded_lines"].get(pass_probe["path"]) != pass_probe["excluded_lines"]:
        raise QualificationError("current-full-pass excluded-line identity diverges from the locked hand manifest")

    missing_coverage = results[MISSING.name].artifact["claims"][1]["coverage"]
    missing_probe = manifest["missing_probe"]
    if missing_coverage["missing_lines"].get(missing_probe["path"]) != missing_probe["missing_lines"]:
        raise QualificationError("missing-line coverage numbers diverge from the locked hand manifest")

    comment_coverage = results[COMMENT_ONLY.name].artifact["claims"][1]["coverage"]
    comment_probe = manifest["comment_only_probe"]
    if (comment_coverage["changed_executable"], comment_coverage["covered"]) != (
        comment_probe["changed_executable"],
        comment_probe["covered"],
    ):
        raise QualificationError("comment-only coverage numbers diverge from the locked hand manifest")


def _check_missing_profile(source_repo: Path, scratch: Path, current_assay: Path, current_version: str) -> None:
    name = "missing-profile"
    repo, witness, _pytest_log, _base, _head = _materialize_negative(
        source_repo,
        scratch,
        name,
        probe_fixture=PRIMARY.probe_fixture,
        test_fixture=PRIMARY.test_fixture,
        argv_tail=MISSING.argv_tail,
        wrapper_fixture=None,
        wrapper_text=_NO_COVERAGE_WRAPPER,
    )
    artifact_path = scratch / name / "verdict.json"
    _proc, artifact = _invoke(current_assay, repo, artifact_path)
    if (artifact.get("outcome"), artifact.get("reason_code")) != ("NO_MEASUREMENT", "EMPTY_COVERAGE"):
        raise QualificationError(
            f"missing-profile scenario expected NO_MEASUREMENT/EMPTY_COVERAGE, "
            f"got {artifact.get('outcome')}/{artifact.get('reason_code')}"
        )
    if witness.exists():
        raise QualificationError("missing-profile scenario unexpectedly produced a coverage witness")
    if _git(repo, "status", "--porcelain=v1") != "":
        raise QualificationError("missing-profile scratch repository was left dirty")


def _check_dirty_consumer(source_repo: Path, scratch: Path, current_assay: Path, current_version: str) -> None:
    name = "dirty-consumer"
    repo, witness, _pytest_log, _base, _head = _materialize_negative(
        source_repo,
        scratch,
        name,
        probe_fixture=PRIMARY.probe_fixture,
        test_fixture=PRIMARY.test_fixture,
        argv_tail=MISSING.argv_tail,
    )
    (repo / "P25-STRAY-UNTRACKED-FILE").write_text("uncommitted\n", encoding="utf-8")
    artifact_path = scratch / name / "verdict.json"
    _proc, artifact = _invoke(current_assay, repo, artifact_path)
    if (artifact.get("outcome"), artifact.get("reason_code")) != ("NO_MEASUREMENT", "DIRTY_TREE"):
        raise QualificationError(
            f"dirty-consumer scenario expected NO_MEASUREMENT/DIRTY_TREE, "
            f"got {artifact.get('outcome')}/{artifact.get('reason_code')}"
        )
    if witness.exists():
        raise QualificationError("dirty-consumer scenario unexpectedly ran the lane's command")


def _check_base_is_head(source_repo: Path, scratch: Path, current_assay: Path, current_version: str) -> None:
    """A symbolic ``judge.base`` resolving to HEAD itself is only detectable
    once R1 evaluation reads the resolved base -- AFTER the lane's own
    command has already run inside the snapshot (unlike the pre-run
    dirty-tree/HEAD-changed guards, which run before any snapshot exists).
    The refused terminal, not command suppression, is the frozen oracle."""
    name = "base-is-head"
    tag = "p25-base-is-head"
    repo, _witness, _pytest_log, _base, _head = _materialize_negative(
        source_repo,
        scratch,
        name,
        probe_fixture=PRIMARY.probe_fixture,
        test_fixture=PRIMARY.test_fixture,
        argv_tail=MISSING.argv_tail,
        base_override=tag,
        tag_head_as=tag,
    )
    artifact_path = scratch / name / "verdict.json"
    _proc, artifact = _invoke(current_assay, repo, artifact_path)
    if (artifact.get("outcome"), artifact.get("reason_code")) != ("NO_MEASUREMENT", "BASE_IS_HEAD"):
        raise QualificationError(
            f"base-is-head scenario expected NO_MEASUREMENT/BASE_IS_HEAD, "
            f"got {artifact.get('outcome')}/{artifact.get('reason_code')}"
        )
    if _git(repo, "status", "--porcelain=v1") != "":
        raise QualificationError("base-is-head scenario's own consumer repository was mutated")


def _check_command_dirt(source_repo: Path, scratch: Path, current_assay: Path, current_version: str) -> None:
    name = "command-dirt"
    repo, _witness, _pytest_log, _base, head = _materialize_negative(
        source_repo,
        scratch,
        name,
        probe_fixture=PRIMARY.probe_fixture,
        test_fixture=PRIMARY.test_fixture,
        argv_tail=MISSING.argv_tail,
        wrapper_fixture=None,
        wrapper_text=_DIRTY_WRAPPER,
    )
    artifact_path = scratch / name / "verdict.json"
    _proc, artifact = _invoke(current_assay, repo, artifact_path)
    if (artifact.get("outcome"), artifact.get("reason_code")) != ("NO_MEASUREMENT", "DIRTY_TREE"):
        raise QualificationError(
            f"command-dirt scenario expected NO_MEASUREMENT/DIRTY_TREE, "
            f"got {artifact.get('outcome')}/{artifact.get('reason_code')}"
        )
    if _git(repo, "status", "--porcelain=v1") != "" or _git(repo, "rev-parse", "HEAD") != head:
        raise QualificationError("command-dirt scenario's own consumer repository was mutated")


def _check_command_head_move(source_repo: Path, scratch: Path, current_assay: Path, current_version: str) -> None:
    name = "command-head-move"
    repo, _witness, _pytest_log, _base, head = _materialize_negative(
        source_repo,
        scratch,
        name,
        probe_fixture=PRIMARY.probe_fixture,
        test_fixture=PRIMARY.test_fixture,
        argv_tail=MISSING.argv_tail,
        wrapper_fixture=None,
        wrapper_text=_HEAD_MOVE_WRAPPER,
    )
    artifact_path = scratch / name / "verdict.json"
    _proc, artifact = _invoke(current_assay, repo, artifact_path)
    if (artifact.get("outcome"), artifact.get("reason_code")) != ("NO_MEASUREMENT", "HEAD_CHANGED"):
        raise QualificationError(
            f"command-head-move scenario expected NO_MEASUREMENT/HEAD_CHANGED, "
            f"got {artifact.get('outcome')}/{artifact.get('reason_code')}"
        )
    if _git(repo, "status", "--porcelain=v1") != "" or _git(repo, "rev-parse", "HEAD") != head:
        raise QualificationError("command-head-move scenario's own consumer repository was mutated")


def _check_resolved_base_is_the_resolution_not_the_declaration(
    source_repo: Path, scratch: Path, current_assay: Path, current_version: str
) -> None:
    """(P33/CA8, A-143/A-229) `judgment.resolved.base` records the RESOLVED
    comparison commit, never the lane's own declared spelling.

    v5 collapses two independently-resolved base values into one field, so
    "which one lands here" stops being an implementation detail and becomes
    contract. Without this scenario, an implementation that simply copied the
    declared string would pass every other oracle in this package: every
    locked template substitutes a full 40-hex for `@BASE_OID@`, and
    `resolve_base` returns a full SHA unchanged, so declared and resolved are
    accidentally identical in each of them. A-143 is explicit that a fixture
    which fails to make the two genuinely different proves nothing.

    The lane therefore declares an ANNOTATED-free lightweight tag pointing at
    the base commit -- not at HEAD, which `_check_base_is_head` already owns
    and which refuses before a base is ever recorded. Declared is
    `p33-declared-base`; resolved must be the 40-hex the tag names.
    """
    name = "declared-base-is-a-tag"
    tag = "p33-declared-base"
    repo, witness, pytest_log, base, head = _materialize_negative(
        source_repo,
        scratch,
        name,
        probe_fixture=PRIMARY.probe_fixture,
        test_fixture=PRIMARY.test_fixture,
        argv_tail=PRIMARY.argv_tail,
        base_override=tag,
        tag_base_as=tag,
    )
    artifact_path = scratch / name / "verdict.json"
    _proc, artifact = _invoke(current_assay, repo, artifact_path)
    if artifact.get("outcome") != "PASS":
        raise QualificationError(
            f"the declared-base-as-tag scenario expected PASS, got "
            f"{artifact.get('outcome')}/{artifact.get('reason_code')}"
        )
    recorded = artifact.get("judgment", {}).get("resolved", {}).get("base")
    if recorded == tag:
        raise QualificationError(
            "judgment.resolved.base recorded the lane's DECLARED spelling "
            f"{tag!r} rather than the commit it resolves to; a consumer cannot "
            "re-derive a diff from a ref name that may since have moved"
        )
    if recorded != base:
        raise QualificationError(
            f"judgment.resolved.base is {recorded!r}, which is neither the "
            f"declared tag {tag!r} nor the commit it names ({base})"
        )
    if recorded == head:
        raise QualificationError(
            "judgment.resolved.base resolved to HEAD; the scenario is meant to "
            "compare against an earlier commit"
        )
    if _git(repo, "status", "--porcelain=v1") != "":
        raise QualificationError(
            "the declared-base-as-tag scenario's own consumer repository was mutated"
        )


def _check_wrong_source_root(source_repo: Path, scratch: Path, current_assay: Path, current_version: str) -> None:
    """A wrong-but-existing `judge.source_roots` must fail the whole-document
    comparison BECAUSE OF THE ROOT.

    The decoy is therefore MISSING's own construction -- same probe, test,
    wrapper and targeted argv -- with only `source_roots` changed, compared
    against MISSING's own template. Its control is the real `missing-line`
    scenario in `qualify`, which matches that template exactly; so the only
    field that can differ here is `judgment`. A decoy built from a different
    probe or argv than its template would be rejected for the argv alone and
    would prove nothing about source roots.
    """
    name = "wrong-source-root"
    repo, witness, pytest_log, base, head = _materialize_negative(
        source_repo,
        scratch,
        name,
        probe_fixture=MISSING.probe_fixture,
        test_fixture=MISSING.test_fixture,
        argv_tail=MISSING.argv_tail,
        source_roots="topos/src",
    )
    artifact_path = scratch / name / "verdict.json"
    _proc, artifact = _invoke(current_assay, repo, artifact_path)
    normalized = normalize_artifact(
        artifact,
        assay_version=current_version,
        base_oid=base,
        head_oid=head,
        witness=witness,
        pytest_log=pytest_log,
    )
    expected = json.loads(
        (_EXPECTED_ROOT / "p25-missing-v5-template.json").read_text(encoding="utf-8")
    )
    differing = sorted(key for key in set(normalized) | set(expected) if normalized.get(key) != expected.get(key))
    if not differing:
        raise QualificationError("a wrong-source-root decoy incorrectly passed the complete artifact comparison")
    if "judgment" not in differing:
        raise QualificationError(
            f"the wrong-source-root decoy was rejected without the source root being the "
            f"discriminating difference (differing fields: {differing})"
        )


def _check_universal_pass_mutation(missing_result: ScenarioResult) -> None:
    """Forge an internally-consistent PASS out of the real MISSING artifact
    and prove the whole-document comparator (not a status-only check) rejects
    it against the truthful FAIL template."""
    forged = copy.deepcopy(dict(missing_result.artifact))
    forged["outcome"] = "PASS"
    forged["exit_code"] = 0
    forged.pop("reason_code", None)
    claim = dict(forged["claims"][1])
    claim["status"] = "PASS"
    claim.pop("reason_code", None)
    coverage = dict(claim["coverage"])
    coverage["covered"] = coverage["changed_executable"]
    coverage["pct"] = 100.0
    coverage["missing_lines"] = {}
    claim["coverage"] = coverage
    forged["claims"] = [forged["claims"][0], claim]

    witness = Path(missing_result.artifact["env_effective"]["ASSAY_P25_WITNESS"])
    pytest_log = Path(missing_result.artifact["env_effective"]["ASSAY_P25_LOG"])
    try:
        compare_complete_artifact(
            actual=forged,
            template=_EXPECTED_ROOT / "p25-missing-v5-template.json",
            assay_version=forged["assay_version"],
            base_oid=missing_result.base_oid,
            head_oid=missing_result.head_oid,
            witness=witness,
            pytest_log=pytest_log,
        )
    except QualificationError:
        return
    raise QualificationError("a forged universal-PASS artifact incorrectly passed the complete artifact comparison")


def qualify(
    *,
    source_repo: Path,
    scratch: Path,
    current_assay: Path,
    current_version: str,
) -> tuple[ScenarioResult, ...]:
    """Run the current full proof, release smoke, and locked negative matrix."""
    before = _consumer_status(source_repo)

    release_assay, release_version = install_locked_release(source_repo=source_repo, scratch=scratch)

    owners = {
        PRIMARY.name: (current_assay, current_version),
        RELEASE_SMOKE.name: (release_assay, release_version),
        MISSING.name: (current_assay, current_version),
        EXCLUDED.name: (current_assay, current_version),
        COMMENT_ONLY.name: (current_assay, current_version),
    }

    results: dict[str, ScenarioResult] = {}
    for spec in SCENARIOS:
        executable, version = owners[spec.name]
        results[spec.name] = run_scenario(
            source_repo=source_repo,
            scratch=scratch,
            assay_executable=executable,
            assay_version=version,
            spec=spec,
        )

    _check_line_manifests(results)

    primary = results[PRIMARY.name]
    missing = results[MISSING.name]
    for result, template_name in (
        (primary, "p25-pass-v5-template.json"),
        (missing, "p25-missing-v5-template.json"),
    ):
        # The version and the witness/log paths come from the committed plan
        # (the owner this scenario was run with, and the deterministic scratch
        # layout `materialize_scenario` built) -- NEVER from the artifact under
        # test, which would turn `normalize_artifact`'s identity guards into
        # tautologies that can no longer fail.
        _executable, owner_version = owners[result.spec.name]
        compare_complete_artifact(
            actual=result.artifact,
            template=_EXPECTED_ROOT / template_name,
            assay_version=owner_version,
            base_oid=result.base_oid,
            head_oid=result.head_oid,
            witness=scratch / result.spec.name / "witness.json",
            pytest_log=scratch / result.spec.name / "pytest.log",
        )

    _check_missing_profile(source_repo, scratch, current_assay, current_version)
    _check_dirty_consumer(source_repo, scratch, current_assay, current_version)
    _check_base_is_head(source_repo, scratch, current_assay, current_version)
    _check_command_dirt(source_repo, scratch, current_assay, current_version)
    _check_command_head_move(source_repo, scratch, current_assay, current_version)
    _check_wrong_source_root(source_repo, scratch, current_assay, current_version)
    _check_resolved_base_is_the_resolution_not_the_declaration(
        source_repo, scratch, current_assay, current_version
    )
    _check_universal_pass_mutation(missing)

    after = _consumer_status(source_repo)
    if before != after:
        raise QualificationError("the real Topos/vbpub checkout changed during qualification")

    return tuple(results[spec.name] for spec in SCENARIOS)


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
