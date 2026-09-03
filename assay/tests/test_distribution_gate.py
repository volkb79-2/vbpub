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
        # 4 since B024/DA-R7: `build_lint_venv` resolves the same base prefix
        # the build/run venvs are cut from, so the lint closure is built by the
        # image's own interpreter and not by whatever is first on PATH.
        occurrences = body.count(AMBIENT_TESTER_VENV_PYTHON)
        assert occurrences == 4, (
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


#: Shell preamble every `assay` stub in this module shares: resolve the
#: verdict path the way the real CLI does — the token after `--verdict-json` —
#: instead of by argv position. See `_self_hosted_lane_fixture`'s docstring.
_VERDICT_PATH_FROM_ARGV = (
    'out=""; prev=""\n'
    'for arg in "$@"; do\n'
    '  if [ "$prev" = "--verdict-json" ]; then out="$arg"; fi\n'
    '  prev="$arg"\n'
    "done\n"
    'if [ -z "$out" ]; then echo "stub: no --verdict-json in argv" >&2; exit 64; fi\n'
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

    The stub finds its output path by READING the argv for `--verdict-json`
    rather than by position. It used to write to `$5`, with a note here saying
    so — and that note had to move once already when
    `--require-judge-provenance` was added, and would have moved again when
    A-429 added `--resume --progress`. A stub whose contract is "the value
    after `--verdict-json`" is the same contract the real CLI has, and it does
    not break every time the gate's invocation grows a flag.
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
        + _VERDICT_PATH_FROM_ARGV
        + f"printf '%s' {shlex.quote(document)} > \"$out\"\n"
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
        "#!/usr/bin/env bash\n"
        + _VERDICT_PATH_FROM_ARGV
        + 'printf \'{"assay_version": "9.9.9"}\' > "$out"\nexit 0\n'
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
        + _VERDICT_PATH_FROM_ARGV
        + 'printf \'{"assay_version": "9.9.9"}\' > "$out"\n'
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
        # (A-429) the estate-wide pair, on the one `assay run` run-gate does
        # not drive. Asserted on the invocation the stub RECEIVED, so this is
        # the flags actually reaching assay, not a substring of the script.
        "--resume",
        "--progress",
        f"{scratch}/progress-tester-unified.jsonl",
        "--verdict-json",
        f"{scratch}/verdict.json",
    ]


# --- B024/DA-R7: the pyflakes lint phase and its own hash-bound closure -----


LINT_WHEELHOUSE = DISTRIBUTION / "lint-wheelhouse"
LINT_REQUIREMENTS = DISTRIBUTION / "lint-requirements.txt"
LINT_MANIFEST = DISTRIBUTION / "lint-wheelhouse-manifest.json"


@pytest.fixture(scope="session")
def lint_venv(tmp_path_factory, gate_functions: Path) -> Path:
    """A real `lint-venv` built by the gate's OWN `build_lint_venv`, from the
    committed offline wheelhouse with `--require-hashes`. Built once per
    session because every assertion below wants the same closure the gate
    installs, not a pip-resolved approximation of it."""
    scratch = tmp_path_factory.mktemp("lint-scratch")
    proc = run_bash(
        f'build_lint_venv "{scratch}" "{DISTRIBUTION}"',
        gate_functions=gate_functions,
        timeout=180,
    )
    assert proc.returncode == 0, proc.stderr
    return scratch


def test_lint_requirements_pin_the_wheels_that_are_actually_committed() -> None:
    """The pin, the manifest and the bytes on disk must agree. A wheelhouse
    whose hash line does not match its own file is a closure that will only
    fail inside the network-less container, where nobody can fetch a fix."""
    lines = [
        line.strip()
        for line in LINT_REQUIREMENTS.read_text(encoding="utf-8").splitlines()
        if line.strip() and not line.strip().startswith("#")
    ]
    assert len(lines) == 1, lines
    assert lines[0].startswith("pyflakes==3.4.0 --hash=sha256:"), lines[0]

    manifest = json.loads(LINT_MANIFEST.read_text(encoding="utf-8"))
    assert manifest["schema_version"] == 1
    entries = manifest["requirements"]
    assert len(entries) == 1, entries

    wheels = sorted(LINT_WHEELHOUSE.glob("*.whl"))
    assert [w.name for w in wheels] == [entries[0]["filename"]]

    payload = wheels[0].read_bytes()
    digest = hashlib.sha256(payload).hexdigest()
    assert digest == entries[0]["sha256"]
    assert len(payload) == entries[0]["size"]
    assert f"--hash=sha256:{digest}" in LINT_REQUIREMENTS.read_text(encoding="utf-8")


def test_the_lint_closure_is_a_third_venv_and_never_the_build_or_run_venv() -> None:
    """A-198's five-wheel build closure is an assertion about what can enter
    the wheel. A linter is neither a build input nor a runtime dependency, so
    it gets its own venv and the build closure's assertion stays exactly what
    it was."""
    source = GATE_SCRIPT.read_text(encoding="utf-8")
    lint_fn = source.split("build_lint_venv() {", 1)[1].split("\n}\n", 1)[0]

    assert "$scratch/lint-venv" in lint_fn
    assert "build-venv" not in lint_fn
    assert "run-venv" not in lint_fn
    assert "--require-hashes" in lint_fn
    assert "--no-index" in lint_fn
    assert "lint-wheelhouse" in lint_fn

    build_fn = source.split("build_offline_closure_venvs() {", 1)[1].split("\n}\n", 1)[0]
    assert "pyflakes" not in build_fn
    assert "lint" not in build_fn

    run_fn = source.split("run_lint_phase() {", 1)[1].split("\n}\n", 1)[0]
    assert "$scratch/lint-venv/bin/python" in run_fn
    # The judged bytes are the private exact-OID clone's, never the caller's
    # bind-mounted worktree.
    assert "$scratch/clone/assay/src/assay" in run_fn


def test_the_lint_phase_runs_after_the_suite_and_marks_itself() -> None:
    source = GATE_SCRIPT.read_text(encoding="utf-8")
    assert "echo 'ASSAY_GATE_PHASE=pyflakes-clean'" in source

    inner = source.split("run_inner() {", 1)[1].split("# --- entry points", 1)[0]
    self_host = inner.index("run_self_hosted_lane")
    witness = inner.index("run_independent_witness")
    lint = inner.index("run_lint_phase")
    assert self_host < witness < lint


def test_a_planted_unused_import_reddens_the_lint_phase(
    tmp_path: Path, gate_functions: Path, lint_venv: Path
) -> None:
    """The whole point of the phase. A clean tree passes and emits the marker
    exactly once; the same tree with one unused import fails, names the file
    and line, and emits NO marker -- a phase that cannot go red is wiring, not
    a gate."""
    scratch = tmp_path / "scratch"
    package = scratch / "clone" / "assay" / "src" / "assay"
    package.mkdir(parents=True)
    (package / "__init__.py").write_text("", encoding="utf-8")
    module = package / "config.py"
    module.write_text("import json\n\n\ndef load(text: str) -> object:\n    return json.loads(text)\n", encoding="utf-8")
    shutil.copytree(lint_venv / "lint-venv", scratch / "lint-venv", symlinks=True)

    clean = run_bash(f'run_lint_phase "{scratch}"', gate_functions=gate_functions)
    assert clean.returncode == 0, clean.stderr
    assert clean.stdout.count("ASSAY_GATE_PHASE=pyflakes-clean") == 1

    module.write_text(
        "import json\nimport os\n\n\ndef load(text: str) -> object:\n    return json.loads(text)\n",
        encoding="utf-8",
    )
    planted = run_bash(f'run_lint_phase "{scratch}"', gate_functions=gate_functions)
    assert planted.returncode != 0
    assert "ASSAY_GATE_PHASE=pyflakes-clean" not in planted.stdout
    assert "'os' imported but unused" in planted.stdout + planted.stderr
    assert "config.py" in planted.stdout + planted.stderr


def test_an_undefined_name_reddens_the_lint_phase(
    tmp_path: Path, gate_functions: Path, lint_venv: Path
) -> None:
    """pyflakes' whole rule set is the F-rule set, and the rule that pays for
    the phase is F821: a name that does not exist at runtime, which a test
    suite only catches on the branch that executes it."""
    scratch = tmp_path / "scratch"
    package = scratch / "clone" / "assay" / "src" / "assay"
    package.mkdir(parents=True)
    (package / "__init__.py").write_text("", encoding="utf-8")
    (package / "errors.py").write_text(
        "def refuse(reason: str) -> str:\n"
        "    if reason == 'x':\n"
        "        return REASON_TABLE[reason]\n"
        "    return reason\n",
        encoding="utf-8",
    )
    shutil.copytree(lint_venv / "lint-venv", scratch / "lint-venv", symlinks=True)

    proc = run_bash(f'run_lint_phase "{scratch}"', gate_functions=gate_functions)
    assert proc.returncode != 0
    assert "undefined name 'REASON_TABLE'" in proc.stdout + proc.stderr


def test_the_shipped_source_tree_is_pyflakes_clean(
    tmp_path: Path, gate_functions: Path, lint_venv: Path
) -> None:
    """The gate's own assertion, brought forward into the ordinary suite so a
    finding is visible in `pytest tests` instead of only after a nine-minute
    container run. Runs the identical locked pyflakes over the identical
    package the gate lints."""
    scratch = tmp_path / "scratch"
    (scratch / "clone" / "assay" / "src").mkdir(parents=True)
    (scratch / "clone" / "assay" / "src" / "assay").symlink_to(PROJECT_ROOT / "src" / "assay")
    shutil.copytree(lint_venv / "lint-venv", scratch / "lint-venv", symlinks=True)

    proc = run_bash(f'run_lint_phase "{scratch}"', gate_functions=gate_functions, timeout=180)
    assert proc.returncode == 0, proc.stdout + proc.stderr


def test_the_self_hosted_lane_is_invoked_with_resume_and_progress() -> None:
    """(A-429) The 2026-09-02 operator directive, now estate policy: EVERY
    ``assay run`` in the estate passes ``--resume --progress <path>``
    (vbpub ``AGENTS.md``; run-gate SPEC R-38 / RG-33, run-gate rev 33).

    run-gate appends both to every assay-kind lane it drives. This gate calls
    ``assay run`` itself, so it is the one invocation in the estate a
    run-gate change cannot reach — which is exactly why it is asserted here
    rather than assumed. Both flags are no-ops on this R0 lane by assay's own
    contract, and that is the point: the shape is uniform whether or not a
    lane has anything to checkpoint.

    The progress path must stay under the gate's ``$scratch``. A progress file
    written into the worktree would be an untracked path in the tree the lane
    is judging, i.e. a self-inflicted ``DIRTY_TREE``.
    """
    source = GATE_SCRIPT.read_text(encoding="utf-8")
    invocation = source.split("assay run tester-unified", 1)[1].split("; then", 1)[0]

    assert "--resume" in invocation, invocation
    assert '--progress "$scratch/progress-tester-unified.jsonl"' in invocation, (
        invocation
    )
    assert "--require-judge-provenance" in invocation, invocation
    assert '--verdict-json "$scratch/verdict.json"' in invocation, invocation
    # Exactly one such invocation: a second, unflagged one would defeat this.
    assert source.count("assay run tester-unified") == 1, source.count(
        "assay run tester-unified"
    )
