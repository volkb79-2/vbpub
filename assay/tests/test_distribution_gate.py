"""Ordinary regression coverage for the P24 registered-gate transformation.

`tools/tester-unified-gate.sh` itself only runs for real inside the
`tester-unified` container (the registered gate the controller owns) — this
project's own instructions are explicit that an implementer/reviewer must not
invoke it directly. These tests instead exercise the script's REAL shell
functions directly (sourced from the actual file, not reimplemented), using
real `git`/`pip`/venvs against small synthetic fixtures, so a regression in
the committed-clone topology, the hash-bound build closure, the placeholder-
version refusal, or the diagnostic/marker control flow is caught by the
ordinary suite the registered gate collects (`pytest tests -q ...`).

P24 (A-198-A-201): the four things proved here are exactly the four the
handoff calls out as invisible to a convenient-but-wrong implementation --
ignored residue entering the build, an unclosed build backend silently
reproducing the old `0.0.0` placeholder, a diagnostic rerun laundering a red
self-hosted lane into a zero exit, and a version mismatch between the
self-hosted lane's own emitted artifact and the wheel actually installed.
"""

from __future__ import annotations

import hashlib
import json
import os
import shlex
import shutil
import subprocess
import sys
import tomllib
from dataclasses import dataclass
from pathlib import Path

import pytest
from conftest import PROJECT_ROOT

GATE_SCRIPT = PROJECT_ROOT / "tools" / "tester-unified-gate.sh"
DISTRIBUTION = PROJECT_ROOT / "gate" / "distribution"
LOCKED_ASSETS = PROJECT_ROOT / "nyxloom-trove" / "carve-assets" / "P24"
AMBIENT_TESTER_VENV_PYTHON = "/opt/tester-venv/bin/python"


def _resolve_ambient_interpreter() -> Path:
    """The real gate image always has /opt/tester-venv; this cockpit never
    does. Mirror conftest.py's own `_build_backend_home` fallback so these
    tests exercise the identical functions in both places."""
    real = Path(AMBIENT_TESTER_VENV_PYTHON)
    return real if real.exists() else Path(sys.executable)


@pytest.fixture(scope="session")
def gate_functions(tmp_path_factory) -> Path:
    """A private copy of the gate script's FUNCTION DEFINITIONS ONLY (the
    entry-point dispatch at the bottom is dropped so sourcing it never
    auto-runs outer/inner mode). Outside the real container the hardcoded
    `/opt/tester-venv/bin/python` is swapped for this session's own ambient
    interpreter -- never written back to the tracked script, only to a
    session-scoped tmp_path copy.
    """
    source = GATE_SCRIPT.read_text(encoding="utf-8")
    marker = "# --- entry points"
    assert marker in source, "gate script no longer has the expected entry-point marker"
    body = source.split(marker, 1)[0]

    if not Path(AMBIENT_TESTER_VENV_PYTHON).exists():
        occurrences = body.count(AMBIENT_TESTER_VENV_PYTHON)
        assert occurrences == 3, (
            f"expected exactly 3 uses of {AMBIENT_TESTER_VENV_PYTHON} in the "
            f"function definitions, found {occurrences}; update this test's "
            "substitution if the script changed"
        )
        body = body.replace(AMBIENT_TESTER_VENV_PYTHON, str(_resolve_ambient_interpreter()))

    out = tmp_path_factory.mktemp("gate-functions") / "gate-functions.sh"
    out.write_text(body, encoding="utf-8")
    return out


def run_bash(
    snippet: str,
    *,
    gate_functions: Path,
    env: dict[str, str] | None = None,
    timeout: int = 60,
) -> subprocess.CompletedProcess[str]:
    script = f"set -euo pipefail\nsource '{gate_functions}'\n{snippet}\n"
    return subprocess.run(
        ["bash", "-c", script],
        capture_output=True,
        text=True,
        env=env,
        timeout=timeout,  # failsafe only; no assertion depends on elapsed time
    )


def _make_synthetic_untagged_repo(root: Path) -> None:
    """A tiny repo shaped like the real monorepo: a tracked `assay/` subdir
    (pyproject.toml + src/), no tag -- mirrors the real gate's own topology
    without depending on the live vbpub tree's size or history.
    """
    (root / "assay").mkdir()
    shutil.copyfile(PROJECT_ROOT / "pyproject.toml", root / "assay" / "pyproject.toml")
    shutil.copytree(
        PROJECT_ROOT / "src",
        root / "assay" / "src",
        ignore=shutil.ignore_patterns("__pycache__", "*.egg-info"),
    )
    subprocess.run(["git", "init", "-q", "-b", "main"], cwd=root, check=True, timeout=30)
    subprocess.run(["git", "add", "assay"], cwd=root, check=True, timeout=30)
    identity = {
        **os.environ,
        "GIT_AUTHOR_NAME": "Assay",
        "GIT_AUTHOR_EMAIL": "assay@example.invalid",
        "GIT_COMMITTER_NAME": "Assay",
        "GIT_COMMITTER_EMAIL": "assay@example.invalid",
    }
    subprocess.run(
        ["git", "commit", "-q", "-m", "untagged"], cwd=root, env=identity, check=True, timeout=30
    )


# --- static checks -----------------------------------------------------------


def test_gate_script_has_valid_bash_syntax() -> None:
    proc = subprocess.run(
        ["bash", "-n", str(GATE_SCRIPT)], capture_output=True, text=True, timeout=10
    )
    assert proc.returncode == 0, proc.stderr


def test_gate_script_passes_shellcheck_when_available() -> None:
    shellcheck = shutil.which("shellcheck")
    if shellcheck is None:
        pytest.skip("shellcheck is not installed in this environment")
    proc = subprocess.run(
        [shellcheck, str(GATE_SCRIPT)], capture_output=True, text=True, timeout=30
    )
    assert proc.returncode == 0, proc.stdout + proc.stderr


def test_gate_script_preserves_required_markers_and_hardens_the_build() -> None:
    source = GATE_SCRIPT.read_text(encoding="utf-8")
    for marker in (
        "ASSAY_GATE_PHASE=wheel-installed",
        "ASSAY_GATE_PHASE=self-hosted-lane-passed",
        "ASSAY_GATE_PHASE=independent-self-hosting-passed",
    ):
        assert f"echo '{marker}'" in source, f"missing required phase marker: {marker}"
    assert "echo 'ASSAY_REGISTERED_GATE_COMPLETE=1'" in source

    for required in (
        "--network=none",
        "--no-local",
        "--no-checkout",
        "--require-hashes",
        "--no-build-isolation",
        "--no-index",
        "--cgroup-parent=",
    ):
        assert required in source, f"missing required gate flag: {required}"

    for forbidden in ("setuptools_home", 'PYTHONPATH="$setuptools_home"'):
        assert forbidden not in source, f"stale ambient-backend route reappeared: {forbidden}"


def test_p26_installed_wheel_acceptance_has_its_test_closure_before_pytest() -> None:
    source = GATE_SCRIPT.read_text(encoding="utf-8")
    inner = source.split("run_inner() {", 1)[1].split("# --- entry points", 1)[0]

    wheel = inner.index("ASSAY_GATE_PHASE=wheel-installed")
    closure = inner.index("write_tester_closure_pth")
    purity = inner.index("require_installed_purity")
    acceptance = inner.index("ASSAY_P26_PROJECT_ROOT")
    marker = inner.index("ASSAY_GATE_PHASE=attestation-hardened")
    self_host = inner.index("run_self_hosted_lane")

    assert wheel < closure < purity < acceptance < marker < self_host


def test_pyproject_build_system_matches_the_locked_five_pin_closure() -> None:
    pyproject = tomllib.loads((PROJECT_ROOT / "pyproject.toml").read_text())
    assert tuple(pyproject["build-system"]["requires"]) == (
        "setuptools==84.0.0",
        "wheel==0.47.0",
        "setuptools-scm==10.0.5",
        "packaging==26.3",
        "vcs-versioning==2.2.4",
    )


def test_production_distribution_assets_are_byte_identical_to_locked_carve_assets() -> None:
    assert (DISTRIBUTION / "build-requirements.txt").read_bytes() == (
        LOCKED_ASSETS / "build-requirements.txt"
    ).read_bytes()
    assert (DISTRIBUTION / "build-wheelhouse-manifest.json").read_bytes() == (
        LOCKED_ASSETS / "wheelhouse-manifest.json"
    ).read_bytes()

    locked_wheels = sorted((LOCKED_ASSETS / "wheelhouse").glob("*.whl"))
    assert len(locked_wheels) == 5, locked_wheels
    for wheel in locked_wheels:
        production_wheel = DISTRIBUTION / "build-wheelhouse" / wheel.name
        assert production_wheel.read_bytes() == wheel.read_bytes()
    assert {p.name for p in (DISTRIBUTION / "build-wheelhouse").glob("*.whl")} == {
        p.name for p in locked_wheels
    }


# --- real clone / build mechanics --------------------------------------------


def test_exact_oid_clone_excludes_ignored_residue(tmp_path: Path, gate_functions: Path) -> None:
    worktree = tmp_path / "worktree"
    worktree.mkdir()
    _make_synthetic_untagged_repo(worktree)

    # Ignored residue that exists in the WORKING TREE only -- never committed,
    # exactly what a stray local build would leave behind.
    egg_info = worktree / "assay" / "src" / "assay.egg-info"
    egg_info.mkdir()
    (egg_info / "PKG-INFO").write_bytes(b"residue")
    pycache = worktree / "assay" / "src" / "assay" / "__pycache__"
    pycache.mkdir(exist_ok=True)
    (pycache / "config.cpython-314.pyc").write_bytes(b"residue")

    scratch = tmp_path / "scratch"
    scratch.mkdir()
    proc = run_bash(
        f'make_exact_oid_clone "{worktree}" "{scratch}"\n'
        f'echo "CLONE_HEAD=$(git -C "{scratch}/clone" rev-parse HEAD)"',
        gate_functions=gate_functions,
    )
    assert proc.returncode == 0, proc.stderr

    expected_oid = subprocess.run(
        ["git", "-C", str(worktree), "rev-parse", "HEAD"],
        capture_output=True,
        text=True,
        check=True,
        timeout=10,
    ).stdout.strip()
    assert f"CLONE_HEAD={expected_oid}" in proc.stdout

    clone_assay = scratch / "clone" / "assay"
    assert clone_assay.is_dir()
    assert not (clone_assay / "src" / "assay.egg-info").exists()
    assert not list((clone_assay / "src" / "assay").rglob("__pycache__"))


def test_clone_head_mismatch_is_a_hard_failure(tmp_path: Path, gate_functions: Path) -> None:
    """A worktree whose HEAD cannot be resolved (e.g. no commits yet) must
    refuse rather than silently clone something else."""
    worktree = tmp_path / "empty-worktree"
    worktree.mkdir()
    subprocess.run(["git", "init", "-q", "-b", "main"], cwd=worktree, check=True, timeout=30)

    scratch = tmp_path / "scratch"
    scratch.mkdir()
    proc = run_bash(
        f'make_exact_oid_clone "{worktree}" "{scratch}"', gate_functions=gate_functions
    )
    assert proc.returncode != 0


def test_closure_build_produces_a_real_non_placeholder_dev_identity(
    tmp_path: Path, gate_functions: Path
) -> None:
    worktree = tmp_path / "worktree"
    worktree.mkdir()
    _make_synthetic_untagged_repo(worktree)

    scratch = tmp_path / "scratch"
    scratch.mkdir()
    proc = run_bash(
        f'make_exact_oid_clone "{worktree}" "{scratch}"\n'
        f'build_offline_closure_venvs "{scratch}" "{DISTRIBUTION}"\n'
        f'wheel="$(build_one_wheel "{scratch}")"\n'
        f'version="$(require_real_wheel_version "{scratch}" "$wheel")"\n'
        f'echo "VERSION=$version"\n',
        gate_functions=gate_functions,
        timeout=120,
    )
    assert proc.returncode == 0, proc.stderr
    version_line = next(line for line in proc.stdout.splitlines() if line.startswith("VERSION="))
    version = version_line.split("=", 1)[1]
    assert version not in {"0.0.0", "0+unknown"}
    assert ".dev" in version and "+g" in version  # a real setuptools-scm dev identity


def test_ambient_only_build_without_the_closure_is_refused_as_a_placeholder(
    tmp_path: Path, gate_functions: Path
) -> None:
    """Reviewer attack: backend present ambiently + missing locked plugin --
    an ambient fallback must not save the build; the wheel it produces (the
    same documented `0.0.0` gap, A-069) must be refused, not silently shipped.
    """
    worktree = tmp_path / "worktree"
    worktree.mkdir()
    _make_synthetic_untagged_repo(worktree)

    scratch = tmp_path / "scratch"
    scratch.mkdir()
    ambient = _resolve_ambient_interpreter()
    proc = run_bash(
        f'make_exact_oid_clone "{worktree}" "{scratch}"\n'
        f'"{ambient}" -m venv "{scratch}/build-venv"\n'
        f'"{scratch}/build-venv/bin/python" -m pip install --quiet '
        f'--no-index --find-links "{DISTRIBUTION}/build-wheelhouse" '
        f'"setuptools==84.0.0" "wheel==0.47.0"\n'
        f'wheel="$(build_one_wheel "{scratch}")"\n'
        f'require_real_wheel_version "{scratch}" "$wheel"\n',
        gate_functions=gate_functions,
        timeout=120,
    )
    assert proc.returncode != 0
    assert "placeholder" in proc.stderr


# --- self-hosted lane: markers and diagnostic-laundering resistance --------


def test_self_hosted_lane_failure_is_never_laundered_into_success(
    tmp_path: Path, gate_functions: Path
) -> None:
    stub_dir = tmp_path / "stubs"
    stub_dir.mkdir()
    stub_assay = stub_dir / "assay"
    stub_assay.write_text("#!/usr/bin/env bash\necho 'stub assay: deliberately failing' >&2\nexit 7\n")
    stub_assay.chmod(0o755)
    stub_python = stub_dir / "python"
    stub_python.write_text(
        "#!/usr/bin/env bash\necho 'stub diagnostic rerun (also fails)' >&2\nexit 3\n"
    )
    stub_python.chmod(0o755)

    worktree = tmp_path / "worktree"
    (worktree / "assay").mkdir(parents=True)
    scratch = tmp_path / "scratch"
    scratch.mkdir()

    wheel = tmp_path / "assay-9.9.9-py3-none-any.whl"
    wheel.write_bytes(b"not really a wheel, but a real file to hash")

    env = {**os.environ, "PATH": f"{stub_dir}:{os.environ['PATH']}"}
    proc = run_bash(
        f'run_self_hosted_lane "{worktree}" "{scratch}" "9.9.9" "{wheel}"',
        gate_functions=gate_functions,
        env=env,
    )
    assert proc.returncode != 0
    assert "ASSAY_GATE_PHASE=self-hosted-lane-passed" not in proc.stdout
    assert "ASSAY_GATE_DIAGNOSTIC=self-hosted-lane-red" in proc.stderr


def test_self_hosted_lane_requires_the_emitted_version_to_match_the_installed_one(
    tmp_path: Path, gate_functions: Path
) -> None:
    fixture = _self_hosted_lane_fixture(tmp_path, version="9.9.9")

    matching = run_bash(
        fixture.invocation(version="9.9.9"),
        gate_functions=gate_functions,
        env=fixture.env,
    )
    assert matching.returncode == 0, matching.stderr
    assert "ASSAY_GATE_PHASE=self-hosted-lane-passed" in matching.stdout

    mismatched = run_bash(
        fixture.invocation(version="1.0.0"),
        gate_functions=gate_functions,
        env=fixture.env,
    )
    assert mismatched.returncode != 0
    assert "ASSAY_GATE_PHASE=self-hosted-lane-passed" not in mismatched.stdout
    assert "emitted assay_version" in mismatched.stderr


@dataclass(frozen=True)
class _SelfHostedLaneFixture:
    worktree: Path
    scratch: Path
    wheel: Path
    env: dict

    def invocation(self, *, version: str, wheel: Path | None = None) -> str:
        return (
            f'run_self_hosted_lane "{self.worktree}" "{self.scratch}" '
            f'"{version}" "{wheel or self.wheel}"'
        )


def _self_hosted_lane_fixture(
    tmp_path: Path, *, version: str, digest: str | None = None
) -> _SelfHostedLaneFixture:
    """A stub `assay` that writes a COMPLETE self-hosted-lane verdict.

    B018/A-327: the stub must now emit `judge_provenance` too, and by default
    it emits the honest one -- the real sha256 of the wheel file this fixture
    writes -- so `require_emitted_judge_provenance`'s positive branch is
    exercised by a value the stub did not simply echo back from the gate.
    *digest* overrides it to drive the refusal branch.

    Note the argv position: the gate now passes `--require-judge-provenance`
    before `--verdict-json`, so the artifact path the stub writes to is `$5`.
    """
    tmp_path.mkdir(parents=True, exist_ok=True)
    stub_dir = tmp_path / "stubs"
    stub_dir.mkdir(exist_ok=True)
    wheel = tmp_path / f"assay-{version}-py3-none-any.whl"
    wheel.write_bytes(f"a real file to hash, for {version}".encode())
    recorded = digest or hashlib.sha256(wheel.read_bytes()).hexdigest()

    document = json.dumps(
        {
            "assay_version": version,
            "judge_provenance": {
                "name": "assay",
                "version": version,
                "artifact": "wheel",
                "digest_algorithm": "sha256",
                "digest": recorded,
            },
        }
    )
    stub_assay = stub_dir / "assay"
    stub_assay.write_text(
        "#!/usr/bin/env bash\n"
        f"printf '%s' {shlex.quote(document)} > \"$5\"\n"
        "exit 0\n"
    )
    stub_assay.chmod(0o755)

    worktree = tmp_path / "worktree"
    (worktree / "assay").mkdir(parents=True, exist_ok=True)
    scratch = tmp_path / "scratch"
    run_venv_bin = scratch / "run-venv" / "bin"
    run_venv_bin.mkdir(parents=True, exist_ok=True)
    python = run_venv_bin / "python"
    if not python.exists():
        python.symlink_to(sys.executable)

    return _SelfHostedLaneFixture(
        worktree=worktree,
        scratch=scratch,
        wheel=wheel,
        env={**os.environ, "PATH": f"{stub_dir}:{os.environ['PATH']}"},
    )


def test_self_hosted_lane_binds_the_recorded_digest_to_the_installed_wheel(
    tmp_path: Path, gate_functions: Path
) -> None:
    """**B018/A-327's gate-level oracle.** The gate hashes the wheel itself,
    with `sha256sum`, on the host side; the artifact's recorded digest must
    equal that number. Both halves are asserted here differentially: the
    honest stub passes, and a stub recording any other digest -- a plausible
    64-hex one, not obvious garbage -- is refused naming both values."""
    honest = _self_hosted_lane_fixture(tmp_path / "honest", version="9.9.9")
    good = run_bash(
        honest.invocation(version="9.9.9"), gate_functions=gate_functions, env=honest.env
    )
    assert good.returncode == 0, good.stderr
    assert "ASSAY_GATE_PHASE=judge-provenance-bound-to-the-installed-wheel" in good.stdout

    forged = _self_hosted_lane_fixture(
        tmp_path / "forged", version="9.9.9", digest="ab" * 32
    )
    bad = run_bash(
        forged.invocation(version="9.9.9"), gate_functions=gate_functions, env=forged.env
    )
    assert bad.returncode != 0
    assert "ASSAY_GATE_PHASE=self-hosted-lane-passed" not in bad.stdout
    assert "the installed wheel's own sha256" in bad.stderr


def test_self_hosted_lane_refuses_a_verdict_carrying_no_judge_identity(
    tmp_path: Path, gate_functions: Path
) -> None:
    """The absence half. An installed wheel always identifies itself, so a
    self-hosted verdict without `judge_provenance` means the lane did not run
    the wheel this gate built -- which is the one thing self-hosting exists to
    prove."""
    stub_dir = tmp_path / "stubs"
    stub_dir.mkdir()
    stub_assay = stub_dir / "assay"
    stub_assay.write_text(
        '#!/usr/bin/env bash\nprintf \'{"assay_version": "9.9.9"}\' > "$5"\nexit 0\n'
    )
    stub_assay.chmod(0o755)
    worktree = tmp_path / "worktree"
    (worktree / "assay").mkdir(parents=True)
    scratch = tmp_path / "scratch"
    run_venv_bin = scratch / "run-venv" / "bin"
    run_venv_bin.mkdir(parents=True)
    (run_venv_bin / "python").symlink_to(sys.executable)
    wheel = tmp_path / "assay-9.9.9-py3-none-any.whl"
    wheel.write_bytes(b"a real file to hash")

    proc = run_bash(
        f'run_self_hosted_lane "{worktree}" "{scratch}" "9.9.9" "{wheel}"',
        gate_functions=gate_functions,
        env={**os.environ, "PATH": f"{stub_dir}:{os.environ['PATH']}"},
    )
    assert proc.returncode != 0
    assert "ASSAY_GATE_PHASE=self-hosted-lane-passed" not in proc.stdout
    assert "emitted no judge_provenance" in proc.stderr


def test_the_self_hosted_lane_demands_the_judge_identity_from_assay_itself(
    tmp_path: Path, gate_functions: Path
) -> None:
    """The gate does not merely CHECK the recorded identity afterwards; it
    asks for it up front, with the same flag a CIU V8 gate request would
    pass. Asserted on the invocation the stub actually receives, never read
    off the script's source."""
    stub_dir = tmp_path / "stubs"
    stub_dir.mkdir()
    argv_log = tmp_path / "argv.log"
    stub_assay = stub_dir / "assay"
    stub_assay.write_text(
        "#!/usr/bin/env bash\n"
        f'printf \'%s\\n\' "$@" > "{argv_log}"\n'
        'printf \'{"assay_version": "9.9.9"}\' > "$5"\n'
        "exit 0\n"
    )
    stub_assay.chmod(0o755)
    worktree = tmp_path / "worktree"
    (worktree / "assay").mkdir(parents=True)
    scratch = tmp_path / "scratch"
    (scratch / "run-venv" / "bin").mkdir(parents=True)
    (scratch / "run-venv" / "bin" / "python").symlink_to(sys.executable)
    wheel = tmp_path / "assay-9.9.9-py3-none-any.whl"
    wheel.write_bytes(b"a real file to hash")

    run_bash(
        f'run_self_hosted_lane "{worktree}" "{scratch}" "9.9.9" "{wheel}"',
        gate_functions=gate_functions,
        env={**os.environ, "PATH": f"{stub_dir}:{os.environ['PATH']}"},
    )
    assert argv_log.read_text().splitlines() == [
        "run",
        "tester-unified",
        "--require-judge-provenance",
        "--verdict-json",
        f"{scratch}/verdict.json",
    ]
