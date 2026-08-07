"""O3/O4 — self-hosting is non-circular (A-130), and a controlled universal-
PASS producer mutation is caught by an INDEPENDENT check while ``assay
verify`` wrongly accepts it (A-131).

**Mechanism (A-130).** ``nyxloom-trove/nyxloom.toml``'s gate script now
builds and installs a real ``assay`` wheel from ``{worktree}/assay``'s own
checkout FIRST (the two-environment offline recipe A-070/A-123 already
proved), prepends that venv's ``bin/`` to ``PATH``, and runs ``assay run
tester-unified --verdict-json <path>`` through THAT wheel-installed
``assay`` — never an ambient or source-tree copy (``assay.toml``'s own
``[lanes.tester-unified]`` env table drops ``PYTHONPATH``, and its argv
carries ``--override-ini=pythonpath=`` to cancel ``pyproject.toml``'s own
developer-convenience ``pythonpath = ["src"]``, confirmed empirically to
otherwise re-shadow the installed wheel regardless of ``PATH``/
``PYTHONPATH``). THIS module is then run as a SEPARATE, second step, via
``tester-unified``'s own ambient interpreter — never the scratch venv — and
is the real, independent oracle O3 needs: it reads the artifact the first
step's real, self-hosted ``assay run`` just emitted and checks it against
facts this module computes on its own (``git rev-parse HEAD`` directly, the
lane's own declared argv transcribed by hand), never against anything
``assay verify`` itself said. ``ASSAY_SELF_HOSTING_VERDICT`` names where that
artifact landed; when unset (a bare developer ``pytest`` run has produced no
such artifact) that one check skips rather than failing for an environment
reason, exactly as documented in the test itself.

Because ``tests/conftest.py`` imports ``assay`` at module level, the second
step's own interpreter needs ``assay`` importable too for collection to
succeed at all — ``nyxloom.toml``'s gate script points ``PYTHONPATH`` at the
scratch venv's own ``site-packages`` (the installed WHEEL's location,
confirmed empirically to import the wheel rather than re-shadowing with
source) for that one invocation.

**Producer mutation (A-131).** ``shutil.copytree`` the source tree at test
time (never the real, committed ``runner.py`` — mirrors P12's own A-120
isolation precedent), apply ONE surgical text edit to the COPY's
``runner.py`` forcing :func:`assay.runner.execute_command`'s FAIL branch to
report ``PASS``/``None`` instead of ``FAIL``/``COMMAND_FAILED`` regardless of
the real subprocess's exit code, build and install a wheel from that
MUTATED copy (a fresh, independent build — never the shared gate build or
``conftest.standalone``), run it against a lane whose command genuinely and
deterministically fails, and show ``assay verify`` — spawned as a real
subprocess, the same way any external consumer would invoke it — WRONGLY
accepts the resulting artifact (it is schema-valid and internally
self-consistent; the lie is baked into the claim itself, which is exactly
what makes it insidious), while an independent fact this test establishes
on its own (the underlying command's own real exit code, obtained by
running it again outside assay entirely) disagrees with what the artifact
claims. A companion test runs the IDENTICAL scenario through the real,
unmutated, session-shared wheel (:func:`conftest.standalone`) to confirm the
divergence is caused by the mutation, not by the test's own lane setup.
"""

from __future__ import annotations

import fnmatch
import importlib
import importlib.metadata
import json
import os
import shutil
import subprocess
import sys
import tomllib
from pathlib import Path

import pytest
from conftest import PROJECT_ROOT, Standalone, _build_backend_home, _clean_env, why_invalid
from jsonschema import Draft202012Validator

ENV_VAR = "ASSAY_SELF_HOSTING_VERDICT"
NYXLOOM_TOML = PROJECT_ROOT / "nyxloom-trove" / "nyxloom.toml"

#: The exact argv `assay.toml`'s `[lanes.tester-unified]` declares (A-130),
#: transcribed BY HAND rather than re-read from that file -- if either
#: drifts from the other, this test fails for a genuine reason instead of
#: silently tracking whatever the file happens to say.
DECLARED_LANE_ARGV = [
    "python",
    "-m",
    "pytest",
    "tests",
    "-q",
    "--ignore=tests/test_self_hosting.py",
    "--override-ini=pythonpath=",
]

#: The one-line, surgical mutation A-131 pins: `execute_command`'s FAIL
#: branch (a real nonzero exit) forced to report an unconditional PASS.
#: Verified once (empirically, outside the suite) that replacing ONLY the
#: top-level `assemble_verdict` rollup assignment the handoff's own
#: illustrative example names crashes instead of lying -- `Verdict.__post_init__`'s
#: own outcome-agrees-with-rollup check (unmodified, unforbidden to run)
#: rejects a claims/outcome mismatch at construction time before any
#: artifact could ever be emitted. Mutating the CLAIM's own truth here
#: instead produces a fully self-consistent, schema-valid artifact that
#: really does lie -- the realistic shape of a "universal PASS" bug, and the
#: one `assay verify`'s own internal-consistency-only contract structurally
#: cannot catch.
_MUTATION_OLD = "        outcome=Outcome.FAIL,\n        reason_code=ReasonCode.COMMAND_FAILED,\n"
_MUTATION_NEW = "        outcome=Outcome.PASS,\n        reason_code=None,\n"


def _real_head() -> str:
    """assay's own current commit, established independently of anything
    assay computes -- a real ``git`` subprocess, never :mod:`assay.git`."""
    proc = subprocess.run(
        ["git", "-C", str(PROJECT_ROOT), "rev-parse", "HEAD"],
        capture_output=True,
        text=True,
    )
    assert proc.returncode == 0, proc.stderr
    return proc.stdout.strip()


def _run_assay_verify(assay_bin: Path, artifact_path: Path) -> subprocess.CompletedProcess[str]:
    """A real, separate ``assay verify`` invocation -- the same way any
    external consumer would call it, never a direct function call into the
    same process this test runs in."""
    return subprocess.run(
        [str(assay_bin), "verify", str(artifact_path)],
        capture_output=True,
        text=True,
    )


# ============================================================================
# O3/O4 — the real, first gate step's own emitted artifact, validated
# independently (never by `assay verify` alone)
# ============================================================================


def test_the_self_hosted_lanes_own_verdict_is_independently_valid(
    validator: Draft202012Validator,
):
    path = os.environ.get(ENV_VAR)
    if not path:
        pytest.skip(
            f"{ENV_VAR} is not set -- this check only runs as "
            f"nyxloom-trove/nyxloom.toml's own SEPARATE second gate step, "
            f"reading the artifact the FIRST step's real, self-hosted "
            f"`assay run` just emitted. A bare developer `pytest` run has "
            f"produced no such artifact to read."
        )
    document = json.loads(Path(path).read_text(encoding="utf-8"))

    # 1. The SAME independent jsonschema oracle O1 uses -- never assay's own
    # reconstruction-based verify_document.
    assert why_invalid(validator, document) == [], "the real self-hosted artifact is not schema-valid"

    # 2. Facts this test establishes ON ITS OWN, never read back from the
    # artifact or from assay verify's own opinion of it.
    assert document["schema_version"] == 2
    assert document["lane"] == "tester-unified"
    assert document["commit"] == _real_head()
    assert isinstance(document["assay_version"], str) and document["assay_version"]
    assert document["argv_effective"] == DECLARED_LANE_ARGV

    # 3. The run genuinely passed. If it had not, `set -e` in nyxloom.toml's
    # own gate script would have aborted the WHOLE gate before this second
    # step ever ran -- this confirms what already had to be true, and
    # documents the exact claim shape a real pass renders.
    assert document["outcome"] == "PASS"
    assert document["exit_code"] == 0
    assert document["claims"] == [
        {"rigor": "R0", "source": "computed", "status": "PASS", "verified_by_assay": True}
    ]
    assert document["evidence"] == []

    # 4. `assay verify` (O2's secondary consumer) sees the SAME real
    # artifact, spawned as a genuine subprocess via the wheel this test's
    # own PATH still carries from nyxloom.toml's build step -- but its
    # agreement is never this test's ONLY evidence; everything above
    # already stands on its own, independent of whether this next line even
    # runs.
    assay_bin = shutil.which("assay")
    assert assay_bin is not None, (
        "assay must be resolvable via PATH -- nyxloom.toml's gate script "
        "prepends the wheel-installed scratch venv's bin/ before invoking "
        "this step"
    )
    verify_proc = _run_assay_verify(Path(assay_bin), Path(path))
    assert verify_proc.returncode == 0, verify_proc.stderr


# ============================================================================
# O4 — a coverage regression this package's OWN restructuring would
# otherwise introduce, closed deterministically
# ============================================================================


def test_the_version_fallback_still_fires_when_assay_is_not_installed(monkeypatch):
    """Before A-130, EVERY gate run imported `assay` from a bare source tree
    via `PYTHONPATH=src` with nothing ever `pip install`-ed -- so
    `src/assay/__init__.py`'s `except PackageNotFoundError: __version__ =
    "0+unknown"` branch ("running from a source tree with no install") was
    covered INCIDENTALLY, by every single test session's own startup
    import. A-130's whole point is the opposite: the self-hosted lane now
    ALWAYS runs through a genuinely installed wheel, so
    `importlib.metadata.version("assay")` never raises there anymore -- and
    that branch would silently go uncovered, exactly the kind of gap this
    package's own restructuring is responsible for, not for some other
    package to discover later. Proven here directly and deterministically
    instead of leaning on incidental environment behaviour, so it holds
    regardless of whether `assay` happens to be installed wherever this
    test itself runs.
    """
    import assay

    def raise_not_found(name: str) -> str:
        raise importlib.metadata.PackageNotFoundError(name)

    # Patches the NAMESPACE that owns `version` (`importlib.metadata`
    # itself), not a synthesized/lazy attribute -- monkeypatch restores it
    # automatically on teardown regardless of how this test exits.
    monkeypatch.setattr(importlib.metadata, "version", raise_not_found)
    try:
        reloaded = importlib.reload(assay)
        assert reloaded.__version__ == "0+unknown"
    finally:
        # `importlib.reload` mutated the process-global `assay` module
        # object itself -- monkeypatch's own teardown cannot undo THAT, so
        # it is restored explicitly, on every exit path, before
        # `importlib.metadata.version` itself is unpatched.
        importlib.reload(assay)


# ============================================================================
# O3 — a controlled universal-PASS producer mutation (A-131)
# ============================================================================


def _write_failing_lane_repo(tmp_path: Path) -> Path:
    """A real, tiny git repository declaring one R0 lane whose command
    genuinely, deterministically fails (exit 7) -- never a real pytest run;
    A-131 does not need to re-run assay's own suite to prove the point, and
    a minimal, fast, real subprocess failure is a cleaner independent fact
    than a slow one."""
    repo = tmp_path / "target"
    repo.mkdir()
    (repo / "assay.toml").write_text(
        "schema_version = 1\n\n"
        "[lanes.package]\n"
        'scope = "S1"\n'
        'rigor = ["R0"]\n'
        'enforcement = "gate"\n'
        'argv = ["/bin/sh", "-c", "exit 7"]\n'
        "env = {}\n"
        "env_passthrough = []\n"
        'budget = "1m"\n'
        "allow_argv_append = false\n",
        encoding="utf-8",
    )
    subprocess.run(["git", "init", "-q", "-b", "main", str(repo)], check=True)
    subprocess.run(
        ["git", "-C", str(repo), "config", "user.email", "assay-tests@example.com"], check=True
    )
    subprocess.run(["git", "-C", str(repo), "config", "user.name", "assay tests"], check=True)
    subprocess.run(["git", "-C", str(repo), "add", "-A"], check=True)
    subprocess.run(["git", "-C", str(repo), "commit", "-q", "-m", "seed"], check=True)
    return repo


def _build_and_install_mutated_runner(tmp_path: Path) -> Path:
    """The A-120/A-131 isolation pattern: copy the source tree, mutate the
    COPY's runner.py, build+install a WHOLLY SEPARATE wheel from it -- the
    real, committed runner.py is never touched, and this build never
    reuses/pollutes the shared session-scoped `standalone` fixture. Returns
    the mutant's own venv path."""
    source = tmp_path / "assay-src"
    source.mkdir()
    shutil.copy(PROJECT_ROOT / "pyproject.toml", source / "pyproject.toml")
    shutil.copytree(
        PROJECT_ROOT / "src",
        source / "src",
        ignore=shutil.ignore_patterns("__pycache__", "*.egg-info"),
    )

    runner_path = source / "src" / "assay" / "runner.py"
    original = runner_path.read_text(encoding="utf-8")
    assert original.count(_MUTATION_OLD) == 1, (
        "runner.py's FAIL-branch text has drifted; this mutation's own "
        "target text is no longer unique"
    )
    mutated_text = original.replace(_MUTATION_OLD, _MUTATION_NEW, 1)
    assert mutated_text.count(_MUTATION_NEW) == 1
    runner_path.write_text(mutated_text, encoding="utf-8")

    venv = tmp_path / "venv"
    base = Path(sys.base_prefix) / "bin" / "python3"
    creator = str(base if base.exists() else sys.executable)
    subprocess.run([creator, "-m", "venv", str(venv)], check=True, capture_output=True)
    python = venv / "bin" / "python"
    assert python.exists(), "the mutant's own scratch venv has no interpreter"

    wheels = tmp_path / "wheels"
    build_env = _clean_env()
    build_env["PYTHONPATH"] = str(_build_backend_home())
    built = subprocess.run(
        [
            str(python),
            "-m",
            "pip",
            "wheel",
            "--no-build-isolation",
            "--no-deps",
            "--wheel-dir",
            str(wheels),
            str(source),
        ],
        capture_output=True,
        text=True,
        env=build_env,
    )
    assert built.returncode == 0, f"mutant wheel build failed:\n{built.stdout}\n{built.stderr}"
    candidates = sorted(wheels.glob("assay-*.whl"))
    assert len(candidates) == 1, f"expected one mutant wheel, got {candidates}"

    installed = subprocess.run(
        [str(python), "-m", "pip", "install", "--no-index", str(candidates[0])],
        capture_output=True,
        text=True,
        env=_clean_env(),
    )
    assert installed.returncode == 0, (
        f"mutant wheel install failed:\n{installed.stdout}\n{installed.stderr}"
    )
    return venv


def test_a_universal_pass_producer_mutation_is_wrongly_accepted_by_verify_alone(
    tmp_path: Path, validator: Draft202012Validator
):
    repo = _write_failing_lane_repo(tmp_path)
    venv = _build_and_install_mutated_runner(tmp_path)

    proc = subprocess.run(
        [str(venv / "bin" / "assay"), "run", "package", "--verdict-json", "-"],
        capture_output=True,
        text=True,
        cwd=repo,
        env=_clean_env(),
    )
    assert proc.returncode == 0, (
        f"the MUTATED wheel must report PASS (exit 0) despite the real "
        f"command genuinely exiting 7 -- that IS the mutation, and this "
        f"assertion documents it landed:\n{proc.stdout}\n{proc.stderr}"
    )
    document = json.loads(proc.stdout)
    assert document["outcome"] == "PASS"
    assert document["claims"] == [
        {"rigor": "R0", "source": "computed", "status": "PASS", "verified_by_assay": True}
    ]

    # A REAL, complete, schema-valid, internally self-consistent artifact --
    # never a crash, never a malformed document. The lie is baked into the
    # claim itself, which is exactly why a consistency-only checker cannot
    # see it.
    assert why_invalid(validator, document) == [], (
        "the mutated artifact must stay schema-VALID -- an invalid one "
        "would be a different, easier bug than the one A-131 targets"
    )

    artifact_path = tmp_path / "artifact.json"
    artifact_path.write_text(proc.stdout, encoding="utf-8")
    verify_proc = _run_assay_verify(venv / "bin" / "assay", artifact_path)
    assert verify_proc.returncode == 0, (
        f"assay verify was expected to WRONGLY accept the mutated artifact "
        f"(O3's own negative, 'assay verify as the only validator... lets "
        f"the circular... variant pass') but rejected it:\n{verify_proc.stderr}"
    )

    # The independent fact THIS test establishes on its own -- knowledge
    # `assay verify` structurally cannot have, since it never re-runs
    # anything (A-129): the underlying command's REAL exit code, obtained by
    # running it again, completely outside assay.
    real_command = subprocess.run(["/bin/sh", "-c", "exit 7"])
    assert real_command.returncode == 7, "the fixture command's own real exit code"

    command_really_failed = real_command.returncode != 0
    artifact_claims_pass = document["outcome"] == "PASS"
    # This disagreement -- a real failure the mutated artifact reports as a
    # PASS -- IS the independent rejection this test exists to demonstrate.
    # assay verify (checked above) cannot see it; this test's own knowledge
    # of what the command actually did can.
    assert command_really_failed and artifact_claims_pass, (
        "the independent check has nothing to catch unless the mutation "
        "produced exactly this disagreement"
    )


def test_the_same_scenario_through_the_real_unmutated_wheel_correctly_reports_fail(
    standalone: Standalone, tmp_path: Path
):
    """The control: the IDENTICAL lane/command, run through the real,
    unmutated, session-shared wheel every other test module in this suite
    already trusts -- confirming the divergence above is caused by the
    mutation, never by this test's own lane setup."""
    repo = _write_failing_lane_repo(tmp_path)

    proc = standalone.run(
        "assay", "run", "package", "--file", str(repo / "assay.toml"), "--verdict-json", "-"
    )

    assert proc.returncode == 1, proc.stderr  # Outcome.FAIL.exit_code
    document = json.loads(proc.stdout)
    assert document["outcome"] == "FAIL"
    assert document["claims"] == [
        {
            "rigor": "R0",
            "source": "computed",
            "status": "FAIL",
            "reason_code": "COMMAND_FAILED",
            "verified_by_assay": True,
        }
    ]


# ============================================================================
# O4 — the gate declares only capabilities the final suite demonstrates, and
# retains the verified cgroup helper (A-133)
# ============================================================================


def test_the_gates_own_asserts_declare_only_tests_pass():
    # A-133: broadening this to changed-line-coverage/canary-verified/
    # mutation would be EXACTLY "declaring an unimplemented capability"
    # (O4's own negative) -- assay's own gate never mechanically applies R1+
    # rigor to its own diff, self-hosting or not.
    gate = tomllib.loads(NYXLOOM_TOML.read_text(encoding="utf-8"))["gates"]["tester-unified"]
    assert gate["asserts"] == ["tests-pass"]


def test_handoff_globs_are_narrowed_to_assays_own_handoffs():
    # A-133: the broader "nyxloom-trove/handoffs/*.md" also matched
    # nyxloom-trove/handoffs/README.md (a prose file with no frontmatter)
    # and tripped a real lint error.
    project = tomllib.loads(NYXLOOM_TOML.read_text(encoding="utf-8"))["project"]
    assert project["handoff_globs"] == ["nyxloom-trove/handoffs/assay-*.md"]
    assert not fnmatch.fnmatch(
        "nyxloom-trove/handoffs/README.md", project["handoff_globs"][0]
    )
    assert fnmatch.fnmatch(
        "nyxloom-trove/handoffs/assay-P14-self-hosted-conformance.md",
        project["handoff_globs"][0],
    )


def test_the_cgroup_helper_wiring_is_present_in_the_restructured_gate_script():
    # The other half of O4's negative ("bypassing the cgroup helper"),
    # checked directly against THIS package's own restructured argv --
    # tests/test_cgroup_parent.py's own
    # test_nyxloom_gate_uses_verified_value_without_a_literal_slice already
    # covers this from the pre-existing suite; restated here so it is also
    # visible from inside the module that did the restructuring.
    argv = " ".join(
        tomllib.loads(NYXLOOM_TOML.read_text(encoding="utf-8"))["gates"]["tester-unified"]["argv"]
    )
    assert "tools/cgroup-parent.sh" in argv
    assert '--cgroup-parent="$cgroup_parent"' in argv
    assert "dev-background.slice" not in argv
