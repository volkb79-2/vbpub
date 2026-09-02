"""``assay run`` — CLI-level wiring for O1/O2/O3: executes exactly the
declared (plus permitted-appended) argv, resolves the commit via real git,
maps outcomes to exit codes, and writes the verdict artifact only when
``--verdict-json`` is given.

Fixture-level exactness (O4) lives in ``test_runner_run_lane.py`` (the real
pipeline, unit-level) and ``test_standalone.py`` (the installed wheel), both
against the same production functions this CLI path calls; this module
proves the WIRING — argument parsing, the ``--`` append convention, commit
resolution, R1 dispatch through the CLI's own built-in registry, and the
artifact-emission on/off/stdout switch — end to end through
:func:`assay.cli.main`, using a REAL git repository (:func:`conftest.git_repo`)
and a REAL child process throughout (no injected process_runner reaches this
file at all).

**P17: every lane file must be COMMITTED before ``assay run``, not merely
written.** The whole-worktree-clean requirement (work item 3, closing sol
finding 6 for R0 too: "every assay run invocation records HEAD and runs the
live tree regardless of rigor level") means an uncommitted ``assay.toml`` —
the older convention every test in this module used through P16 — now trips
``NO_MEASUREMENT``/``DIRTY_TREE`` before the lane's own command ever runs.
:func:`_write_and_commit_lane` is the one place that changed.
"""

from __future__ import annotations

import contextlib
import io
import json
import sys
from pathlib import Path

import pytest
from conftest import R0_LANE, R1_LANE, GitRepo, set_key, why_invalid
from jsonschema import Draft202012Validator

from assay import cli as cli_module
from assay import git, provenance
from assay.cli import _built_in_registry, main
from assay.config import RIGOR_LEVELS
from assay.errors import AssayError, Outcome, ReasonCode
from assay.verify import verify_document
from assay.vocabulary import WITHDRAWN_MUTATION_OPERATORS


#: (B018/A-327) The bracketing text of the one line a clean run prints on
#: stderr from a SOURCE TREE, where assay has no build artifact to hash and
#: therefore records no `judge_provenance` at all. The middle -- the specific
#: reason -- is genuinely environment-dependent (no distribution metadata at
#: all vs. metadata with no PEP 610 wheel record), so it is not pinned here;
#: the bracketing wording IS pinned, because it is what an operator reads.
JUDGE_PROVENANCE_ABSENT_PREFIX = "assay: no judge_provenance recorded -- "
JUDGE_PROVENANCE_ABSENT_SUFFIX = (
    "; pass --require-judge-provenance to refuse instead of proceeding\n"
)


def assert_stderr_is_only_the_judge_provenance_notice(err: str) -> None:
    """The successor to this module's old ``assert err == ""``.

    It is an "assay said nothing else" assertion, and it additionally proves
    B018's loudness: an unidentifiable judge is ANNOUNCED, never silently
    omitted.

    **(A-335) Which of the two correct outputs to expect is DERIVED, not
    assumed.** This helper used to require the notice unconditionally, which
    silently encoded "this suite always runs from an unidentifiable source
    checkout". That holds in a dev tree and is false under the registered
    gate, which installs a real wheel into a clean `run-venv` and runs these
    tests with that interpreter -- so the real `cmru release` pipeline went
    red while every hand-run gate stayed green. The invariant these callers
    actually care about is "assay printed nothing UNEXPECTED"; what counts as
    expected depends on whether this process has an identity, so we ask.
    """
    identity, _reason = provenance.identify_judge()
    if identity is not None:
        # An identified run has nothing to announce -- the notice exists to
        # report an absence, and emitting it here would be noise on every
        # gated run.
        assert err == "", (
            f"this process HAS a judge identity ({identity.artifact} "
            f"{identity.version}), so assay should have printed nothing, "
            f"got {err!r}"
        )
        return
    assert err.startswith(JUDGE_PROVENANCE_ABSENT_PREFIX), err
    assert err.endswith(JUDGE_PROVENANCE_ABSENT_SUFFIX), err
    assert err.count("\n") == 1, err


def _run_parser_description() -> str:
    """Exactly what a user sees from ``assay run --help`` -- captured from
    the real parser's own output, never read off the source line that
    writes it."""
    captured = io.StringIO()
    with contextlib.redirect_stdout(captured), pytest.raises(SystemExit):
        main(["run", "--help"])
    return captured.getvalue()


def run(argv: list[str]) -> tuple[int, str, str]:
    out, err = io.StringIO(), io.StringIO()
    code = main(argv, stdout=out, stderr=err)
    return code, out.getvalue(), err.getvalue()


def snapshot(root: Path) -> set[str]:
    return {str(p.relative_to(root)) for p in root.rglob("*") if p.name != ".git"}


def _write_and_commit_lane(repo: GitRepo, text: str) -> Path:
    path = repo.write("assay.toml", text)
    repo.commit_all("add assay.toml")
    return path


# --- a passing command --------------------------------------------------------


def test_run_executes_a_passing_lane_and_exits_zero(git_repo: GitRepo):
    lane = set_key(R0_LANE, "argv", '["/bin/sh", "-c", "exit 0"]')
    path = _write_and_commit_lane(git_repo, lane)

    code, out, err = run(["run", "package", "--file", str(path)])

    assert code == 0
    # B018/A-327: the ONLY thing on stderr for a clean run is the judge-
    # provenance notice, and it is here because the test suite runs from a
    # source tree -- there is no build artifact to hash, so no identity is
    # recorded and the absence is announced rather than left silent. An
    # installed wheel or zipapp prints nothing here (proved for real in
    # `test_distribution_build_release.py`).
    assert_stderr_is_only_the_judge_provenance_notice(err)
    assert "package: PASS (exit 0)" in out
    assert git_repo.head() in out


def test_run_resolves_the_commit_via_real_git(git_repo: GitRepo):
    lane = set_key(R0_LANE, "argv", '["/bin/sh", "-c", "exit 0"]')
    path = _write_and_commit_lane(git_repo, lane)

    code, out, err = run(["run", "package", "--file", str(path), "--verdict-json", "-"])

    document = json.loads(out)
    assert document["commit"] == git_repo.head()


# --- a failing command ---------------------------------------------------------


def test_run_executes_a_failing_lane_and_exits_one(git_repo: GitRepo):
    lane = set_key(R0_LANE, "argv", '["/bin/sh", "-c", "exit 7"]')
    path = _write_and_commit_lane(git_repo, lane)

    code, out, err = run(["run", "package", "--file", str(path)])

    assert code == 1
    assert "package: FAIL/COMMAND_FAILED (exit 1)" in out


# --- artifact emission: on/off/stdout (A-028) ---------------------------------


def test_run_without_verdict_json_creates_no_artifact(git_repo: GitRepo):
    lane = set_key(R0_LANE, "argv", '["/bin/sh", "-c", "exit 0"]')
    path = _write_and_commit_lane(git_repo, lane)
    before = snapshot(git_repo.path)

    run(["run", "package", "--file", str(path)])

    assert snapshot(git_repo.path) == before


def test_run_writes_the_verdict_atomically_to_the_given_path(
    git_repo: GitRepo, tmp_path: Path, validator: Draft202012Validator
):
    lane = set_key(R0_LANE, "argv", '["/bin/sh", "-c", "exit 0"]')
    path = _write_and_commit_lane(git_repo, lane)
    target = tmp_path / "verdict.json"

    code, out, err = run(
        ["run", "package", "--file", str(path), "--verdict-json", str(target)]
    )

    assert code == 0
    assert target.is_file()
    document = json.loads(target.read_text(encoding="utf-8"))
    assert document["outcome"] == "PASS"
    assert document["lane"] == "package"
    assert list(validator.iter_errors(document)) == []


def test_run_writes_verdict_json_to_stdout_when_dash(git_repo: GitRepo):
    lane = set_key(R0_LANE, "argv", '["/bin/sh", "-c", "exit 7"]')
    path = _write_and_commit_lane(git_repo, lane)

    code, out, err = run(["run", "package", "--file", str(path), "--verdict-json", "-"])

    assert code == 1
    document = json.loads(out)  # the WHOLE of stdout must parse as JSON
    assert document["outcome"] == "FAIL"
    assert document["reason_code"] == "COMMAND_FAILED"


def test_run_writes_the_verdict_to_a_file_path_on_a_non_pass_outcome_too(
    git_repo: GitRepo, tmp_path: Path
):
    # O3's own text names PASS, FAIL, ERROR, NO_MEASUREMENT and BUDGET_EXCEEDED
    # explicitly -- a writer (or a CLI gate in front of it) that only writes on
    # success would leave every adverse verdict's artifact absent, which a
    # consumer polling for the file would misread as "assay never ran" rather
    # than "assay ran and failed".
    lane = set_key(R0_LANE, "argv", '["/bin/sh", "-c", "exit 7"]')
    path = _write_and_commit_lane(git_repo, lane)
    target = tmp_path / "fail-verdict.json"

    code, out, err = run(
        ["run", "package", "--file", str(path), "--verdict-json", str(target)]
    )

    assert code == 1
    assert target.is_file(), "a non-PASS outcome must still write its artifact"
    document = json.loads(target.read_text(encoding="utf-8"))
    assert document["outcome"] == "FAIL"


# --- argv append via `--` (A-036, A-095) --------------------------------------


def test_run_rejects_an_append_without_allow_argv_append(git_repo: GitRepo, tmp_path: Path):
    marker = tmp_path / "APPEND_RAN"
    lane = set_key(R0_LANE, "argv", '["/bin/sh", "-c", \'touch "$0"\']')
    lane = set_key(lane, "allow_argv_append", "false")
    path = _write_and_commit_lane(git_repo, lane)

    code, out, err = run(["run", "package", "--file", str(path), "--", str(marker)])

    assert code == 2, "ERROR is exit 2"
    assert "package: ERROR/EXEC_FAILED (exit 2)" in out
    assert not marker.exists(), "the process must never have started"


def test_run_permits_an_append_when_allowed(git_repo: GitRepo, tmp_path: Path):
    marker = tmp_path / "APPEND_RAN"
    lane = set_key(R0_LANE, "argv", '["/bin/sh", "-c", \'touch "$0"\']')
    lane = set_key(lane, "allow_argv_append", "true")
    path = _write_and_commit_lane(git_repo, lane)

    code, out, err = run(["run", "package", "--file", str(path), "--", str(marker)])

    assert code == 0
    assert marker.exists()


# --- this build evaluates R0, Python R1 and Python R2 (P17, R2 wiring P18) ---


def _r1_lane_writing_a_marker(marker: Path) -> str:
    """An R1 lane whose command's ONLY observable effect is creating
    *marker* -- so "did the command run?" is a filesystem fact, not a
    call count on an injected seam this module deliberately never uses."""
    return set_key(R1_LANE, "argv", f'["/bin/sh", "-c", "touch {marker}"]')


def test_the_run_help_declares_exactly_the_levels_the_registry_reaches():
    """A-146. ``assay run --help``'s own last sentence is a CAPABILITY
    DECLARATION addressed to a human, and it had gone stale the moment P18
    widened the registry -- it still said "R0 and Python R1" while the
    build genuinely reached R2. Pinned to the registry itself rather than
    to a literal, so the two cannot drift apart again: the levels named in
    the prose are exactly the levels ``_built_in_registry`` declares, plus
    ``R0``, which needs no adapter and is therefore in no entry.

    Under-declaring is not harmless just because it is the safe direction:
    this project exists to remove the gap between a declared capability
    and a real one, in EITHER direction (the post-series review's own
    finding 1)."""
    entry_levels = {
        level
        for entry in _built_in_registry().entries.values()
        for level in entry.rigor
    }
    description = _run_parser_description()

    named = {level for level in RIGOR_LEVELS if level in description}

    assert named == entry_levels | {"R0"}


def test_run_evaluates_a_real_r2_pass_end_to_end(git_repo: GitRepo):
    """P18's own registry-wiring proof, the mirror of
    ``test_run_evaluates_a_real_r1_pass_end_to_end`` one rigor level over:
    a real two-commit diff introduces one ``compare-swap`` site (``x > 0``
    has exactly ONE swap target, ``>=``, per the adapter's own closed
    catalogue), a real ``/bin/sh`` command that greps the live file for the
    literal substring ``x > 0`` PASSES against the unmutated baseline and
    FAILS against the single generated mutant (whose own mutated text
    reads ``x >= 0``) -- a genuine kill, through the installed CLI, not a
    hand-asserted claim. R2 declared WITHOUT R1 alongside it, so this also
    proves R2's own independent diff-resolution path (no R1 claim to reuse
    ``on_added_resolved`` from)."""
    (git_repo.path / "src").mkdir()
    git_repo.write("src/mod.py", "def f(x):\n    return 0\n")
    base_rev = git_repo.commit_all("add mod.py")
    git_repo.write("src/mod.py", "def f(x):\n    return x > 0\n")
    git_repo.commit_all("introduce a compare-swap site")
    lane = f"""\
schema_version = 2

[lanes.package]
scope = "S1"
rigor = ["R0", "R2"]
enforcement = "gate"
argv = ["/bin/sh", "-c", "grep -q 'x > 0' src/mod.py"]
env = {{}}
env_passthrough = ["PATH"]
budget = "1m"
allow_argv_append = false

[lanes.package.isolation]
snapshot_selection = "repository"

[lanes.package.judge]
language = "python"
source_roots = ["src"]
base = "{base_rev}"

[lanes.package.judge.mutation]
jobs = 1
max_mutants = 50
operators = ["python:compare-swap"]
"""
    path = git_repo.write("assay.toml", lane)
    git_repo.commit_all("add assay.toml")

    code, out, err = run(["run", "package", "--file", str(path), "--verdict-json", "-"])

    assert code == 0, err
    document = json.loads(out)
    assert document["outcome"] == "PASS"
    assert [c["rigor"] for c in document["claims"]] == ["R0", "R2"]
    r2_claim = document["claims"][1]
    assert r2_claim["status"] == "PASS"
    # P21/A-180: `killed` is an identity list through the REAL installed
    # pipeline, so the end-to-end artifact -- not just a hand-built model --
    # carries the byte span and replacement hash of the site the suite
    # caught. The span is read off the emitted document rather than
    # hardcoded here (the fixture's own source is built in this test), but
    # its SHAPE is pinned exactly.
    mutation = r2_claim["mutation"]
    assert mutation["candidate_count"] == 1
    assert mutation["total"] == 1
    assert mutation["survived"] == []
    assert mutation["crashed"] == []
    assert mutation["budget_exceeded"] == []
    # P33/V5-3: the fifth bucket is present and empty -- a real Python run
    # proves no mutant inert, which is a different fact from not having the
    # bucket at all.
    assert mutation["equivalent"] == []
    assert len(mutation["killed"]) == 1
    killed = mutation["killed"][0]
    assert set(killed) == {
        "path",
        "lineno",
        "start_byte",
        "end_byte",
        "replacement_sha256",
        "operator",
        "description",
    }
    assert killed["operator"] == "python:compare-swap"
    assert killed["start_byte"] < killed["end_byte"]
    assert len(killed["replacement_sha256"]) == 64
    assert document["judgment"]["r2"] == {
        # B046/schema v9: the REAL producer's own value through the CLI --
        # this lane runs assay's own mutation engine, so `native` is what a
        # genuine end-to-end run puts on the wire, beside the jobs/
        # max_mutants/operators policy that only a native run has.
        "producer": "native",
        "jobs": 1,
        "max_mutants": 50,
        "operators": ["python:compare-swap"],
        # P33/V5-4: DERIVED from `kill_signal_artifact`'s absence (A-223b),
        # never declared. Every real P33 lane renders this value, because the
        # config loader refuses that field until P34 (A-227/A-230d) -- so
        # this assertion pins the only derivation this build can reach, and
        # does not pretend to witness the other branch.
        "kill_attribution": "unattributed",
        # B035/A-329: the REAL producer's own value, through the installed
        # CLI -- this lane declares no `judge.mode`, so the artifact records
        # the effective scope `_build_judgment_r2` resolved, not the absent
        # declaration. It is what makes the `base` this same document carries
        # checkable for an `R0,R2` lane at all.
        "mode": "changed_lines",
    }
    # P33/V5-1: the hoisted group. An R0,R2 lane records what it judged --
    # exactly the hole v4 had, since `judgment.r1` is absent here and there
    # was nowhere else for a language, source roots or a comparison commit
    # to live.
    resolved = document["judgment"]["resolved"]
    assert resolved["language"] == "python"
    assert resolved["source_roots"] == ["src"]
    assert resolved["base"] == base_rev
    assert "r1" not in document["judgment"]
    # A-230a: no helper ran, so the key is absent rather than an empty array.
    assert "helpers" not in document


def _r2_lane_with_two_candidates(git_repo: GitRepo) -> str:
    """A base commit plus two independent compare-swap sites, one per
    function, so `--shard 0/2`/`--shard 1/2` each own exactly one real
    candidate through the installed CLI -- never a hand-built claim."""
    (git_repo.path / "src").mkdir()
    git_repo.write("src/mod.py", "def f(x):\n    return 0\n\ndef g(y):\n    return 0\n")
    base_rev = git_repo.commit_all("add mod.py")
    git_repo.write(
        "src/mod.py", "def f(x):\n    return x > 0\n\ndef g(y):\n    return y > 0\n"
    )
    git_repo.commit_all("introduce two compare-swap sites")
    lane = f"""\
schema_version = 2

[lanes.package]
scope = "S1"
rigor = ["R0", "R2"]
enforcement = "gate"
argv = ["/bin/sh", "-c", "grep -q 'x > 0' src/mod.py"]
env = {{}}
env_passthrough = ["PATH"]
budget = "1m"
allow_argv_append = false

[lanes.package.isolation]
snapshot_selection = "repository"

[lanes.package.judge]
language = "python"
source_roots = ["src"]
base = "{base_rev}"

[lanes.package.judge.mutation]
jobs = 1
max_mutants = 50
operators = ["python:compare-swap"]
"""
    return lane


def _r2_lane_single_site(git_repo: GitRepo) -> str:
    """The exact single-candidate fixture `test_run_evaluates_a_real_r2_pass_
    end_to_end` uses -- one real, fully-killed compare-swap site, so a lane
    built from this reliably PASSes regardless of what else it declares."""
    (git_repo.path / "src").mkdir()
    git_repo.write("src/mod.py", "def f(x):\n    return 0\n")
    base_rev = git_repo.commit_all("add mod.py")
    git_repo.write("src/mod.py", "def f(x):\n    return x > 0\n")
    git_repo.commit_all("introduce a compare-swap site")
    return f"""\
schema_version = 2

[lanes.package]
scope = "S1"
rigor = ["R0", "R2"]
enforcement = "gate"
argv = ["/bin/sh", "-c", "grep -q 'x > 0' src/mod.py"]
env = {{}}
env_passthrough = ["PATH"]
budget = "1m"
allow_argv_append = false

[lanes.package.isolation]
snapshot_selection = "repository"

[lanes.package.judge]
language = "python"
source_roots = ["src"]
base = "{base_rev}"

[lanes.package.judge.mutation]
jobs = 1
max_mutants = 50
operators = ["python:compare-swap"]
"""


def test_run_refuses_an_out_of_range_shard_with_a_clean_verdict_not_a_crash(
    git_repo: GitRepo,
):
    """(B012 remediation, D-6) `select_mutation_shard`'s bounds check used to
    sit outside any try/except in `run_lane`, so an out-of-range `--shard`
    raised a bare `ValueError` that reached `main()` uncaught -- no verdict,
    a traceback instead of an exit code. It must now behave like every other
    post-HEAD-resolution refusal: a clean `BAD_LANE_CONFIG` verdict."""
    path = _write_and_commit_lane(git_repo, _r2_lane_with_two_candidates(git_repo))
    code, out, err = run(
        ["run", "package", "--shard", "5/2", "--file", str(path), "--verdict-json", "-"]
    )
    assert code != 0
    document = json.loads(out)
    assert document["outcome"] == "ERROR"
    assert document["reason_code"] == "BAD_LANE_CONFIG"


def test_run_refuses_an_out_of_range_shard_even_with_unresolvable_infrastructure(
    git_repo: GitRepo, validator: Draft202012Validator,
):
    """(B025) The bad-`--shard` refusal above is decided BEFORE
    `refuse_lane` ever touches infrastructure -- but `refuse_lane` always
    calls `resolve_command_plan` a second time to record the attempted plan
    (A-036), and a lane whose OWN infrastructure declaration is itself
    unresolvable makes that second call raise too, from inside the refusal
    path. This is the two-unrelated-causes case B025's own filing
    distinguished from the single-cause case above: the shard refusal (not
    the infrastructure failure) is still the recorded cause, and the
    artifact is written, degraded rather than absent."""
    lane = _r2_lane_with_two_candidates(git_repo) + (
        "\n[lanes.package.infrastructure]\n"
        'mynet = "required-env:MISSING_NETWORK_VAR_FOR_TEST"\n'
    )
    path = _write_and_commit_lane(git_repo, lane)
    code, out, err = run(
        ["run", "package", "--shard", "5/2", "--file", str(path), "--verdict-json", "-"]
    )
    assert code != 0
    assert "Traceback" not in err
    document = json.loads(out)
    assert why_invalid(validator, document) == []
    assert document["outcome"] == "ERROR"
    assert document["reason_code"] == "BAD_LANE_CONFIG"
    assert document["env_effective"] == {}
    assert document["env_effective_incomplete"] is True


def test_run_refuses_an_unknown_operator_with_a_clean_verdict_not_a_crash(
    git_repo: GitRepo,
):
    """(B012 remediation, D-6) The unimported `LaneConfigError` made every
    `--operators` refusal a `NameError` instead of a typed, catchable
    error."""
    path = _write_and_commit_lane(git_repo, _r2_lane_with_two_candidates(git_repo))
    code, out, err = run(
        [
            "run",
            "package",
            "--operators",
            "bogus:does-not-exist",
            "--file",
            str(path),
            "--verdict-json",
            "-",
        ]
    )
    assert code != 0
    assert "unknown mutation operators" in err
    assert out == ""  # A-181: refused before output reservation, so nothing is written


@pytest.mark.parametrize("verb", ["run", "plan"])
@pytest.mark.parametrize("withdrawn", sorted(WITHDRAWN_MUTATION_OPERATORS))
def test_operators_answers_a_withdrawn_name_by_name_not_as_unknown(
    git_repo: GitRepo, verb, withdrawn
):
    """(A-331, filling the gap the round-1 review measured) The withdrawn
    check runs BEFORE the unknown check at all THREE call sites, not just in
    the loader.

    This ordering only became observable at v8. Under v7 both names were
    still in `MUTATION_OPERATORS`, so "unknown" could not fire on them and
    either order produced the same message; A-331 deleted the spellings, and
    from that commit a stale `--operators python:enum-comparison-swap` is
    *literally* an unknown operator. Answering it with "unknown mutation
    operators" would name the wrong defect and drop the one thing the
    operator needs -- that `python:compare-swap` already covers it.

    A-331 originally claimed both CLI sites were pinned by the loader's test.
    They were not: the review reverted both reorderings and the full suite
    was byte-identical, 3354 passed either way. These are the tests that make
    the claim true, one per verb, because `run` and `plan` carry SEPARATE
    copies of this block (`cli.py:392` and `cli.py:692`) and a fix applied to
    one would otherwise leave the other silently wrong.
    """
    path = _write_and_commit_lane(git_repo, _r2_lane_with_two_candidates(git_repo))
    argv = [verb, "package", "--operators", withdrawn, "--file", str(path)]
    if verb == "run":
        argv += ["--verdict-json", "-"]
    code, out, err = run(argv)

    assert code != 0
    assert "withdrawn mutation operators" in err, err
    assert withdrawn in err, err
    assert "python:compare-swap" in err, err
    # The ordering itself. Without it the message is the generic one and the
    # two assertions above still pass on nothing useful.
    assert "unknown mutation operators" not in err, err


def test_run_records_the_executed_shard_not_the_lane_declaration(git_repo: GitRepo):
    """(B012 remediation, D-8) `judgment.r2.shard_index`/`shard_count` used
    to come from `lane.judge.mutation.shard_index`/`shard_count` -- the
    lane's static declaration, which this lane never sets and which selects
    nothing -- instead of the function's own parameters carrying the
    executed `--shard` value. A sharded verdict was therefore
    indistinguishable from a complete run."""
    path = _write_and_commit_lane(git_repo, _r2_lane_with_two_candidates(git_repo))
    code, out, err = run(
        ["run", "package", "--shard", "0/2", "--resume", "--file", str(path), "--verdict-json", "-"]
    )
    # PASS/FAIL depends on which of the two real sites this shard's
    # deterministic assignment happens to own (the oracle only greps for
    # `f`'s comparison) -- irrelevant to what this test proves. `code` in
    # {0, 1} is a real verdict either way; anything else is a crash.
    assert code in (0, 1), err
    document = json.loads(out)
    r2 = document["judgment"]["r2"]
    assert r2["shard_index"] == 0
    assert r2["shard_count"] == 2
    r2_claim = document["claims"][1]
    assert r2_claim["mutation"]["candidate_count"] == 1


def test_run_refuses_an_unresolvable_derived_fact_on_an_r0_only_lane(
    git_repo: GitRepo, validator: Draft202012Validator,
):
    """(B025 round 2, N-W1) `run_lane`'s direct R0-only path has its OWN
    `resolve_command_plan` call, independent of `_run_higher_rigor_lane`'s
    and `refuse_lane`'s own (the two round-1 sites) -- an unresolvable
    infrastructure declaration crashed uncaught here too, with no verdict
    artifact, until this round's fix. This exact lane shape --
    `docs/CONSUMERS.md`'s own "Inject infrastructure facts into an isolated
    lane" example declares `rigor = ["R0"]` -- is the DOCUMENTED shape for
    the feature, not a corner case; round 1's two tests both used R2 lanes
    and never exercised it."""
    lane = R0_LANE + (
        "\n[lanes.package.infrastructure]\n"
        'image = "derived:deploy.image"\n'
    )
    path = _write_and_commit_lane(git_repo, lane)
    code, out, err = run(["run", "package", "--file", str(path), "--verdict-json", "-"])
    assert code != 0
    assert "Traceback" not in err
    document = json.loads(out)
    assert why_invalid(validator, document) == []
    assert document["outcome"] == "ERROR"
    assert document["reason_code"] == "BAD_LANE_CONFIG"
    assert document["env_effective_incomplete"] is True


def test_run_refuses_a_missing_required_infrastructure_env_var_without_crashing(
    git_repo: GitRepo, validator: Draft202012Validator,
):
    """(B013 remediation, D-11) `cli.py` never imported `os`, so any lane
    declaring `[lanes.<name>.infrastructure]` crashed with an uncaught
    `NameError` before running at all -- proven fixed here through the
    installed CLI, not by calling `resolve_command_plan` directly the way
    the feature's own tests do: the refusal is now a clean, typed
    `AssayError` reaching `main()`'s handler, not a traceback.

    **B025, since fixed too:** this is the exact case that used to write NO
    verdict artifact despite `--verdict-json` being reserved --
    `_run_higher_rigor_lane`'s own primary `resolve_command_plan` call raised
    uncaught past `main()`'s outer handler. It now refuses through
    `refuse_lane` like every other post-HEAD-resolution refusal in this
    module, which writes a real, schema-valid artifact -- `env_effective`
    honestly `{}` and `env_effective_incomplete: true`, since the thing
    that's broken is the infrastructure declaration itself.

    **B053/A-409 (Wave D), which CHANGED this test's stderr expectation.**
    This case used to be in the "silent on stderr" bucket alongside the
    `--shard`/`--operators` refusals: a normal, non-exception `Verdict`
    return printed no `err` message at all, so the operator got exit 2 and
    `BAD_LANE_CONFIG` with no sentence saying WHICH declaration was
    unresolvable. That silence WAS the defect B053 filed, and it is gone: the
    one emitter (`runner.announce_refusal`) is now called at every site where
    an `AssayError` becomes a refusal verdict, including
    `_run_higher_rigor_lane`'s plan-resolution catch that this test reaches.
    The assertion below is therefore the NEW truth, asserted positively --
    the fact name is on the line -- rather than the old absence."""
    lane = _r2_lane_single_site(git_repo) + (
        "\n[lanes.package.infrastructure]\n"
        'mynet = "required-env:MISSING_NETWORK_VAR_FOR_TEST"\n'
    )
    path = _write_and_commit_lane(git_repo, lane)
    code, out, err = run(["run", "package", "--file", str(path), "--verdict-json", "-"])
    # B053/A-409: exactly one refusal line, in the one emitter's format, and
    # it names the offending declaration -- which is the whole point.
    refusals = [
        line for line in err.splitlines()
        if line.startswith("assay: ERROR/BAD_LANE_CONFIG: ")
    ]
    assert len(refusals) == 1, err
    assert "mynet" in refusals[0], refusals
    assert "MISSING_NETWORK_VAR_FOR_TEST" in refusals[0], refusals
    assert "Traceback" not in err
    document = json.loads(out)
    assert why_invalid(validator, document) == []
    assert document["outcome"] == "ERROR"
    assert document["reason_code"] == "BAD_LANE_CONFIG"
    assert document["env_effective"] == {}
    assert document["env_effective_incomplete"] is True
    assert code == document["exit_code"] != 0


def test_run_injects_a_resolved_required_env_infrastructure_fact(
    git_repo: GitRepo, monkeypatch: pytest.MonkeyPatch
):
    """(B013 remediation, D-11) The positive case: with the fact resolvable,
    the lane runs through the real installed CLI exactly like any other."""
    lane = _r2_lane_single_site(git_repo) + (
        "\n[lanes.package.infrastructure]\n" 'mynet = "required-env:MY_NETWORK_FOR_TEST"\n'
    )
    path = _write_and_commit_lane(git_repo, lane)
    monkeypatch.setenv("MY_NETWORK_FOR_TEST", "test-network")
    code, out, err = run(["run", "package", "--file", str(path), "--verdict-json", "-"])
    assert code == 0, err
    document = json.loads(out)
    assert document["outcome"] == "PASS"


def test_run_refuses_go_at_r2_the_language_is_registered_r1_only(
    git_repo: GitRepo, tmp_path: Path, validator: Draft202012Validator
):
    """(P33 work item 5 / A-225) v5 gives Go and SQL an operator SPELLING; it
    gives neither a producer, and this test exists so a later package cannot
    quietly change that without saying so.

    `cli._resolve_declared_adapters` refuses a declared level above R0 that
    the registry does not reach for that language, with
    `ERROR`/`BAD_LANE_CONFIG` before anything executes (A-139). The marker
    file is the proof that nothing ran.

    **P34/W6 says so explicitly, as this test's own docstring promised it
    would:** SQL is no longer unregistered -- `_built_in_registry` now names
    it at R2 (`test_run_evaluates_a_real_r2_pass_end_to_end_for_sql` below
    is the positive proof), so it is dropped from this parametrization.
    SQL's own R1/R3 refusal is
    `test_run_refuses_sql_at_r1_the_language_is_registered_r2_only` and
    `test_run_refuses_sql_at_r3_the_language_is_registered_r2_only` below.

    **RENAMED in the round-1 fix round, and the old name was the defect.**
    It was `test_run_refuses_an_r2_lane_for_an_unregistered_language`, and
    its docstring ended "Go remains here: it still has no producer path wired
    to any rigor level (P22)". A-394 registered `go` at R1, so that sentence
    became false and this lane stopped exercising the unknown-language
    branch: it takes the *registered-at-a-different-rigor* branch now, which
    is a real and worth-testing property but not the one the name claimed
    (A-399's own decoy shape, found by adversarial review round 1). This test
    KEEPS `go` on purpose -- R2 must stay refused while
    `generate_mutation_sites` is unconditionally `UNSUPPORTED` -- and says so
    in its name. The unknown-language branch is
    `test_run_refuses_a_language_this_registry_does_not_know_at_all`, which
    declares `rust`.
    """
    marker = tmp_path / "the-command-ran"
    lane = f"""\
schema_version = 2

[lanes.package]
scope = "S1"
rigor = ["R0", "R2"]
enforcement = "gate"
argv = ["/bin/sh", "-c", "touch {marker}"]
env = {{}}
env_passthrough = ["PATH"]
budget = "1m"
allow_argv_append = false

[lanes.package.isolation]
snapshot_selection = "repository"

[lanes.package.judge]
language = "go"
source_roots = ["src"]
base = "HEAD~1"

[lanes.package.judge.mutation]
jobs = 1
max_mutants = 50
operators = ["go:compare-swap"]
"""
    path = _write_and_commit_lane(git_repo, lane)
    (git_repo.path / "src").mkdir(exist_ok=True)

    code, out, err = run(["run", "package", "--file", str(path), "--verdict-json", "-"])

    assert code == 2, err
    assert not marker.exists(), (
        "the lane's command ran; the refusal must precede execution (A-139)"
    )
    document = json.loads(out)
    assert why_invalid(validator, document) == []
    assert document["outcome"] == "ERROR"
    assert document["reason_code"] == "BAD_LANE_CONFIG"


# --- P34/W6: SQL is registered at R2 only -------------------------------------


def test_run_evaluates_a_real_r2_pass_end_to_end_for_sql(git_repo: GitRepo):
    """(P34/W6) `cli._built_in_registry` now names `SqlAdapter` at R2 --
    proven the same way ``test_run_evaluates_a_real_r2_pass_end_to_end``
    proves Python's own R2 wiring: a real two-commit diff introduces one
    ``sql:drop-check`` site (the CHECK constraint is ADDED at HEAD, so its
    whole line is the changed-line target), a real ``/bin/sh`` command
    copies the live schema file to the declared ``equivalence_artifact``
    then greps it for the literal substring the mutant's own replacement
    removes -- PASSES and writes the baseline's own dump against the
    unmutated schema, FAILS with a DIFFERENT dump against the one generated
    mutant (whose own splice reads ``CHECK (true)``) -- a genuine kill
    through the installed CLI, never a hand-asserted claim, and the exact
    proof that `SqlAdapter`'s five raising methods are unreachable through
    this build: reaching a judged R2 claim at all means `has_executable_code`/
    `normalize_coverage_key`/etc. were never called, because nothing at R0/R1
    ever needed them for a language this registry declares R2-only.
    """
    (git_repo.path / "src").mkdir()
    # A-140/A-175: the declared `equivalence_artifact` must be git-ignored,
    # exactly like `cov.json` in the R1/R2 fixtures one level up -- the
    # post-command dirty check refuses the whole run if the lane's command
    # leaves an un-ignored file behind, and `.assay/schema-dump.sql` is
    # precisely that if it rides in uncovered.
    git_repo.write(".gitignore", ".assay/\n")
    git_repo.write("src/schema.sql", "CREATE TABLE t (a INT);\n")
    base_rev = git_repo.commit_all("add schema.sql")
    git_repo.write(
        "src/schema.sql",
        "CREATE TABLE t (a INT, CONSTRAINT ck CHECK (a > 0));\n",
    )
    git_repo.commit_all("add the check constraint")
    lane = f"""\
schema_version = 2

[lanes.package]
scope = "S1"
rigor = ["R0", "R2"]
enforcement = "gate"
argv = ["/bin/sh", "-c", "mkdir -p .assay && cp src/schema.sql .assay/schema-dump.sql && grep -q 'CHECK (a > 0)' src/schema.sql"]
env = {{}}
env_passthrough = ["PATH"]
budget = "1m"
allow_argv_append = false

[lanes.package.isolation]
snapshot_selection = "repository"

[lanes.package.judge]
language = "sql"
source_roots = ["src"]
base = "{base_rev}"

[lanes.package.judge.mutation]
jobs = 1
max_mutants = 50
operators = ["sql:drop-check"]
equivalence_artifact = ".assay/schema-dump.sql"
"""
    path = git_repo.write("assay.toml", lane)
    git_repo.commit_all("add assay.toml")

    code, out, err = run(["run", "package", "--file", str(path), "--verdict-json", "-"])

    # A kill is the SUITE succeeding at its job, not a lane failure: with
    # nothing survived/crashed/budget-exceeded and one real kill,
    # `judge_mutation`'s own mapping renders PASS (A-116/A-117) -- the exact
    # opposite of what a naive "any non-zero mutant exit fails the lane"
    # reading would produce.
    assert code == 0, err
    document = json.loads(out)
    assert document["outcome"] == "PASS"
    r2_claim = document["claims"][1]
    assert r2_claim["status"] == "PASS"
    mutation = r2_claim["mutation"]
    assert mutation["candidate_count"] == 1
    assert mutation["total"] == 1
    assert mutation["survived"] == []
    assert mutation["crashed"] == []
    assert mutation["budget_exceeded"] == []
    assert mutation["equivalent"] == []
    assert len(mutation["killed"]) == 1
    killed = mutation["killed"][0]
    assert killed["operator"] == "sql:drop-check"
    assert killed["path"] == "src/schema.sql"
    assert document["judgment"]["resolved"]["language"] == "sql"
    assert document["judgment"]["r2"]["equivalence_artifact"] == ".assay/schema-dump.sql"
    assert document["judgment"]["r2"]["kill_attribution"] == "unattributed"


def test_run_refuses_sql_at_r1_the_language_is_registered_r2_only(
    git_repo: GitRepo, tmp_path: Path, validator: Draft202012Validator
):
    """(P34/W6) The registry gate that refuses an entirely unknown language
    ALSO refuses a KNOWN one at a level this build never wired it to
    (A-139): SQL is registered R2 only, so R1 is `BAD_LANE_CONFIG` -- proving
    W6's `rigor=frozenset({{"R2"}})` is what makes this refusal fire, not
    merely an unregistered-language special case.

    **The comparison this docstring used to draw is gone**: it said "exactly
    like Go's own total non-registration one test up", which was true when
    written and false from A-394 onward -- `go` IS registered, at R1. The
    genuinely-unknown-language control is
    `test_run_refuses_a_language_this_registry_does_not_know_at_all`
    (`rust`); the neighbour one test up is now Go's own
    registered-at-another-rigor case, which is the same shape as this one
    rather than its opposite."""
    marker = tmp_path / "the-command-ran"
    lane = set_key(_r1_lane_writing_a_marker(marker), "language", '"sql"')
    path = _write_and_commit_lane(git_repo, lane)
    for name in ("src", "scripts"):
        (git_repo.path / name).mkdir(exist_ok=True)

    code, out, err = run(["run", "package", "--file", str(path), "--verdict-json", "-"])

    assert code == 2
    assert not marker.exists()
    document = json.loads(out)
    assert why_invalid(validator, document) == []
    assert document["outcome"] == "ERROR"
    assert document["reason_code"] == "BAD_LANE_CONFIG"
    assert [(c["rigor"], c["status"]) for c in document["claims"]] == [
        ("R0", "ERROR"),
        ("R1", "ERROR"),
    ]
    assert document.get("judgment") is None
    assert "sql" in err


def test_a_go_r1_lane_now_resolves_and_is_refused_for_the_TOOLCHAIN_not_the_registry(
    git_repo: GitRepo, tmp_path: Path, validator: Draft202012Validator
):
    """**This test INVERTED in Wave C (A-394), and the inversion is the
    point.** It used to assert ``ERROR``/``BAD_LANE_CONFIG`` -- "go is not in
    the built-in registry, so this lane is refused for being unsupported".
    ``go`` IS registered now, at ``{"R1"}``, so that refusal is gone. What is
    NOT gone is a refusal: the lane is still refused, and the change is
    entirely in WHY.

    ``NO_MEASUREMENT``/``MISSING_EXTERNAL_TOOL`` (exit 3, not 2) is A-253's
    preflight over ``GoAdapter.external_tools == ("go",)``, and it is the
    property that makes A-394's registration safe in an environment with no
    Go toolchain -- this devcontainer, and the registered gate's own
    ``tester-unified`` image, are both such environments. The distinction the
    two reason codes draw is real and worth keeping sharp:
    ``BAD_LANE_CONFIG`` says *assay cannot judge this language*, which is a
    statement about the build and is answered by shipping an adapter;
    ``MISSING_EXTERNAL_TOOL`` says *assay judges this language and this
    machine is not equipped to run it*, which is a statement about the
    environment and is answered by installing Go. Collapsing the second into
    the first would tell a consumer to wait for a release that already
    shipped.

    The two assertions that did NOT change carry most of the weight.
    ``not marker.exists()`` -- the preflight runs BEFORE the lane's command,
    so a machine without Go never executes a suite it cannot judge, and does
    not burn a CI slot to discover it. And the artifact is still complete and
    schema-valid: A-139/P17's rule is that a refusal is an auditable
    document, never a bare exit code, and a NEW refusal path inherits that
    obligation rather than being excused from it.

    R2 and R3 stay unregistered for Go and stay refused
    ``BAD_LANE_CONFIG`` -- the sibling tests in this module are the controls
    proving this inversion is scoped to R1 and did not quietly widen.

    **The lane declares ``go-cover`` since A-406, and it must.** It used to
    inherit ``R1_LANE``'s ``coverage-py-json``, which is now refused at CONFIG
    LOAD for a ``go`` lane (DA-R1: a language judged from block extents may
    only declare a format that carries them). Leaving the old format here
    would still have produced a refusal and a green test -- with exit 2 for
    the wrong reason, one layer earlier, never reaching the preflight this
    test exists to exercise. That is exactly the decoy shape A-399 named, so
    the fixture moves and the subject stays."""
    marker = tmp_path / "the-command-ran"
    lane = set_key(_r1_lane_writing_a_marker(marker), "language", '"go"')
    lane = lane.replace(
        'coverage = { format = "coverage-py-json", artifact = "cov.json" }',
        'coverage = { format = "go-cover", artifact = "cov.out" }',
    )
    path = _write_and_commit_lane(git_repo, lane)
    for name in ("src", "scripts"):
        (git_repo.path / name).mkdir(exist_ok=True)

    code, out, err = run(["run", "package", "--file", str(path), "--verdict-json", "-"])

    assert code == 3
    # The refusal precedes the command: nothing ran.
    assert not marker.exists()
    document = json.loads(out)
    assert why_invalid(validator, document) == []
    assert document["outcome"] == "NO_MEASUREMENT"
    assert document["reason_code"] == "MISSING_EXTERNAL_TOOL"
    # P16's iff-invariant: no coverage claim rendered, so no policy recorded.
    assert document.get("judgment") is None


def test_go_is_registered_at_r1_only_so_the_refusal_above_is_about_the_toolchain(
    tmp_path: Path,
):
    """The anti-vacuity control for the test above, and it is not
    redundant with it: that test would ALSO pass if ``go`` were still
    unregistered and something else happened to produce exit 3. This one
    asks the registry directly.

    It pins both halves of A-394 -- ``R1`` resolves an adapter, ``R2``/``R3``
    do not -- so a future widening of the frozenset (which the Wave C prompt's
    NOT-IN-SCOPE list forbids, because ``generate_mutation_sites`` is
    unconditionally ``UNSUPPORTED``) cannot land silently."""
    from assay.adapters.go import GoAdapter
    from assay.registry import get_adapter

    built_in = _built_in_registry()

    assert isinstance(get_adapter(built_in, "go", "R1"), GoAdapter)
    for refused in ("R2", "R3"):
        with pytest.raises(AssayError) as caught:
            get_adapter(built_in, "go", refused)
        # The message must say "registered, but not at this level" rather
        # than "unknown language": those are different remedies, and the
        # second would now be a lie.
        assert caught.value.reason_code is ReasonCode.BAD_LANE_CONFIG
        assert "['R1']" in str(caught.value)


def test_run_refuses_an_unregistered_language_with_a_resolvable_infrastructure_fact(
    git_repo: GitRepo, tmp_path: Path, validator: Draft202012Validator
):
    """(B012/B013 remediation, round-3 finding N-6) `_run_reserved`'s two
    `refuse_lane` calls (attestation `LANE_TIMEOUT` and adapter refusal --
    this one) never forwarded `infrastructure_source`/
    `infrastructure_environment`, though both are already computed a few
    lines above. On any lane declaring `[lanes.<name>.infrastructure]`,
    `refuse_lane`'s own internal plan-resolution re-triggered the SAME
    facts (this test uses a `derived:` fact that resolves cleanly, so the
    only thing that can go wrong is the missing forward) and raised
    uncaught -- no verdict artifact, despite `--verdict-json` being
    reserved. Same defect class as N-3, at the two call sites in `cli.py`
    the round-3 fix (which only touched `runner.py`) never reached.

    **Wave C (A-394) changed this test's LANGUAGE, and the reason is that
    keeping it would have silently voided the test.** It declared
    `language = "go"` purely as a convenient way to reach cli.py's
    adapter-refusal branch. Registering `go` at R1 makes that lane resolve an
    adapter, so it now takes a DIFFERENT refusal -- `runner.py`'s
    `MISSING_EXTERNAL_TOOL` preflight -- and this regression test would have
    kept passing while no longer exercising the call site the N-6 defect
    lived at. `sql` is registered at R2 ONLY (A-242), so an R1 `sql` lane is
    config-valid and registry-refused, which is exactly the state `go` used
    to supply. The subject is the infrastructure-fact forward on a
    `refuse_lane` call, not which language happens to be unregistered
    this release."""
    marker = tmp_path / "the-command-ran"
    lane = set_key(_r1_lane_writing_a_marker(marker), "language", '"sql"')
    lane += '\n[lanes.package.infrastructure]\nimg = "derived:deploy.image"\n'
    path = _write_and_commit_lane(git_repo, lane)
    for name in ("src", "scripts"):
        (git_repo.path / name).mkdir(exist_ok=True)
    (git_repo.path / "ciu.global.toml").write_text(
        "[deploy]\nimage = 'postgres:18'\n", encoding="utf-8"
    )

    code, out, err = run(["run", "package", "--file", str(path), "--verdict-json", "-"])

    assert code == 2
    assert not marker.exists()
    document = json.loads(out)
    assert why_invalid(validator, document) == []
    assert document["outcome"] == "ERROR"
    assert document["reason_code"] == "BAD_LANE_CONFIG"
    assert "sql" in err


# --- this build evaluates R0, Python R1, Python R2 and Python R3 (P19) -------
#
# R3 declared with NO R1 beside it used to be exactly the level the
# pre-A-139 gate could not see (the old ``if "R1" in lane.rigor`` guard
# skipped the registry entirely) -- P19 closes that gap for real rather
# than merely refusing it, so the CLI-level proof is now a genuine PASS,
# not a refusal.


def test_run_evaluates_a_real_r3_pass_end_to_end(git_repo: GitRepo):
    """The full CLI wiring for a Python R3 PASS: a real installed pytest
    run, in an independently-owned scratch copy this invocation owns end
    to end, proves the declared import-break canary is caught for its
    specific expected reason -- and that the consumer's own real repository
    (HEAD, index, and worktree bytes) is untouched by any of it."""
    git_repo.write("pkg/__init__.py", "")
    git_repo.write("pkg/mod.py", "def f():\n    return 1\n")
    git_repo.write(
        "tests/test_mod.py",
        "from pkg.mod import f\n\n\ndef test_f():\n    assert f() == 1\n",
    )
    git_repo.commit_all("add pkg")
    lane = f"""\
schema_version = 2

[lanes.package]
scope = "S1"
rigor = ["R0", "R3"]
enforcement = "gate"
argv = ["{sys.executable}", "-m", "pytest", "tests", "-q"]
env = {{ PYTHONDONTWRITEBYTECODE = "1" }}
env_passthrough = ["PATH"]
budget = "2m"
allow_argv_append = false

[lanes.package.isolation]
snapshot_selection = "repository"

[lanes.package.judge]
language = "python"
source_roots = ["pkg"]

[lanes.package.judge.canary]
mechanism = "import-break"
target = "pkg/mod.py"
"""
    path = git_repo.write("assay.toml", lane)
    git_repo.commit_all("add assay.toml")
    head_before = git_repo.head()

    code, out, err = run(["run", "package", "--file", str(path), "--verdict-json", "-"])

    assert code == 0, err
    document = json.loads(out)
    assert document["outcome"] == "PASS"
    assert [c["rigor"] for c in document["claims"]] == ["R0", "R3"]
    r3_claim = document["claims"][1]
    assert r3_claim["status"] == "PASS"
    assert r3_claim["canary"]["mechanism"] == "import-break"
    attempt = r3_claim["canary"]["attempts"][0]
    assert attempt["control_outcome"] == "PASS"
    assert attempt["transformed_outcome"] == "FAIL"
    assert attempt["observed_reason_code"] == "COMMAND_FAILED"
    assert document["judgment"]["r3"] == {
        "mechanism": "import-break",
        "targets": ["pkg/mod.py"],
    }
    # O2: the consumer's own repository is exactly as it was before the run.
    assert git_repo.head() == head_before
    assert git_repo.git("status", "--porcelain") == ""


def test_run_refuses_a_language_this_registry_does_not_know_at_all(
    git_repo: GitRepo, tmp_path: Path, validator: Draft202012Validator
):
    """**The unknown-language branch, at CLI level -- re-pointed at ``rust``
    in the round-1 fix round, and the re-pointing is the point.**

    This was ``test_run_refuses_an_unregistered_language_at_r3_with_a_real_
    artifact`` and it declared ``language = "go"``. When A-394 registered
    ``go``, the lane stopped exercising the branch its name claimed: it still
    refused, still ``BAD_LANE_CONFIG``, still before the command -- but
    through the *registered-at-a-different-rigor* path, not the *unknown
    language* one. A green test that no longer executes the call site it was
    written for is A-399's own "quietly become a decoy" shape, one step
    subtler than usual because no assertion had to be weakened for it to
    happen. A-399's sweep caught the R1 sibling and missed this one and its
    R2 cousin; the round-1 reviewer found both, and the consequence was that
    NO CLI-level test exercised an entirely-unknown language any more.

    ``rust`` is chosen because assay ships no Rust adapter at all -- not a
    stub, not an unregistered one -- so the day someone adds one this test
    breaks loudly instead of drifting into a decoy again. The R2 sibling
    (``test_run_refuses_go_at_r2_the_language_is_registered_r1_only``) keeps
    ``go`` deliberately and says so in its own name: the two now cover
    different branches instead of the same one twice."""
    marker = tmp_path / "the-command-ran"
    (git_repo.path / "src").mkdir(exist_ok=True)
    (git_repo.path / "src" / "mod.rs").write_text("pub fn f() {}\n", encoding="utf-8")
    lane = set_key(R0_LANE, "argv", f'["/bin/sh", "-c", "touch {marker}"]')
    lane = set_key(lane, "rigor", '["R0", "R3"]')
    lane += (
        "\n[lanes.package.isolation]\n"
        'snapshot_selection = "repository"\n'
        "\n[lanes.package.judge]\n"
        'language = "rust"\nsource_roots = ["src"]\n'
        '\n[lanes.package.judge.canary]\n'
        'mechanism = "import-break"\ntarget = "src/mod.rs"\n'
    )
    path = _write_and_commit_lane(git_repo, lane)

    code, out, err = run(["run", "package", "--file", str(path), "--verdict-json", "-"])

    assert code == 2
    assert not marker.exists()
    document = json.loads(out)
    assert why_invalid(validator, document) == []
    assert document["outcome"] == "ERROR"
    assert document["reason_code"] == "BAD_LANE_CONFIG"
    assert [(c["rigor"], c["status"]) for c in document["claims"]] == [
        ("R0", "ERROR"),
        ("R3", "ERROR"),
    ]
    assert document.get("judgment") is None
    assert "rust" in err
    # The anti-vacuity half: `rust` really is absent from the registry, so
    # this test cannot be passing for the sibling's reason.
    assert "rust" not in _built_in_registry().entries


def test_run_refuses_sql_at_r3_the_language_is_registered_r2_only(
    git_repo: GitRepo, tmp_path: Path, validator: Draft202012Validator
):
    """(P34/W6) The R3-level mirror of
    ``test_run_refuses_sql_at_r1_the_language_is_registered_r2_only``: SQL
    is registered R2 only, so R3 is refused too, the same
    ``BAD_LANE_CONFIG`` shape ``rust``'s genuinely-unknown language gets two
    tests up -- proving the registry gate still guards R3 even though this
    build's OWN `SqlAdapter` now reaches R2.

    (That neighbour used to be a ``go`` lane described as "Go's own total
    non-registration". A-394 registered ``go``, so both halves of that
    sentence went false; it was re-pointed at ``rust`` in the round-1 fix
    round.)"""
    marker = tmp_path / "the-command-ran"
    (git_repo.path / "src").mkdir(exist_ok=True)
    (git_repo.path / "src" / "schema.sql").write_text(
        "CREATE TABLE t (a INT);\n", encoding="utf-8"
    )
    lane = set_key(R0_LANE, "argv", f'["/bin/sh", "-c", "touch {marker}"]')
    lane = set_key(lane, "rigor", '["R0", "R3"]')
    lane += (
        "\n[lanes.package.isolation]\n"
        'snapshot_selection = "repository"\n'
        "\n[lanes.package.judge]\n"
        'language = "sql"\nsource_roots = ["src"]\n'
        '\n[lanes.package.judge.canary]\n'
        'mechanism = "import-break"\ntarget = "src/schema.sql"\n'
    )
    path = _write_and_commit_lane(git_repo, lane)

    code, out, err = run(["run", "package", "--file", str(path), "--verdict-json", "-"])

    assert code == 2
    assert not marker.exists()
    document = json.loads(out)
    assert why_invalid(validator, document) == []
    assert document["outcome"] == "ERROR"
    assert document["reason_code"] == "BAD_LANE_CONFIG"
    assert [(c["rigor"], c["status"]) for c in document["claims"]] == [
        ("R0", "ERROR"),
        ("R3", "ERROR"),
    ]
    assert document.get("judgment") is None
    assert "sql" in err


def test_run_evaluates_a_real_r1_pass_end_to_end(git_repo: GitRepo, tmp_path: Path):
    """The full CLI wiring for a Python R1 PASS: a real two-commit diff,
    a real (hand-written, not pytest-produced -- that is O1's own job in
    test_standalone.py) coverage-py-json artifact, and a real
    ``git merge-base`` resolving ``judge.base``."""
    (git_repo.path / "src").mkdir()
    # A-140/P20-A-175: the lane's own command writes cov.json for real below,
    # so it must be git-ignored or the post-command dirty check refuses the
    # run -- unrelated to src/mod.py's own diff, so this cannot change what
    # R1 measures.
    git_repo.write(".gitignore", "cov.json\n")
    git_repo.write("src/mod.py", "def f():\n    return 1\n")
    base_rev = git_repo.commit_all("add mod.py")
    git_repo.write(
        "src/mod.py", "def f():\n    return 1\n\n\ndef g():\n    return 2\n"
    )
    git_repo.commit_all("add g")
    # The lane's OWN command writes cov.json when it runs -- work item 4
    # means a PRE-seeded copy would be removed before the command even
    # starts, so this test's PASS only proves anything if the artifact is a
    # real product of THIS invocation, never data smuggled in beforehand.
    cov_json = json.dumps(
        {
            "files": {
                "src/mod.py": {
                    "executed_lines": [1, 2, 4, 5],
                    "missing_lines": [],
                    "excluded_lines": [],
                }
            }
        }
    )
    write_cov = f"cat > cov.json <<'EOF'\n{cov_json}\nEOF"
    lane = f"""\
schema_version = 2

[lanes.package]
scope = "S1"
rigor = ["R0", "R1"]
enforcement = "gate"
argv = ["/bin/sh", "-c", {json.dumps(write_cov)}]
env = {{}}
env_passthrough = ["PATH"]
budget = "1m"
allow_argv_append = false

[lanes.package.isolation]
snapshot_selection = "repository"

[lanes.package.judge]
language = "python"
source_roots = ["src"]
fail_under = 100.0
allow_excluded = false
coverage = {{ format = "coverage-py-json", artifact = "cov.json" }}
base = "{base_rev}"
"""
    path = git_repo.write("assay.toml", lane)
    git_repo.commit_all("add assay.toml")

    code, out, err = run(["run", "package", "--file", str(path), "--verdict-json", "-"])

    assert code == 0, err
    document = json.loads(out)
    assert document["outcome"] == "PASS"
    assert document["claims"][1]["rigor"] == "R1"
    assert document["claims"][1]["coverage"]["pct"] == 100.0
    # P33/V5-1: both facts hoisted to `judgment.resolved`, which is where an
    # R0,R2 lane can record them too. The assertions are unchanged in
    # substance -- only their address moved.
    assert document["judgment"]["resolved"]["base"] == base_rev
    # judgment.resolved.source_roots must be the DECLARED relative string
    # ("src"), never judge.source_root_paths' resolved absolute form, and
    # never the relocated absolute scratch path a snapshot run works in
    # (A-049/A-149/A-223f) -- through a real assay.toml load the two
    # genuinely differ, unlike the make_r1_judge test helper (conftest.py),
    # which stores the same absolute string in both fields and so could not
    # catch this the other way.
    assert document["judgment"]["resolved"]["source_roots"] == ["src"]
    # ...and R1 keeps only what is genuinely R1 POLICY.
    assert set(document["judgment"]["r1"]) == {
        "coverage_format",
        "coverage_artifact",
        "fail_under",
        "allow_excluded",
        # wave-1 §6 (A-260): mode/require_branch are now required and
        # therefore always present; targets stays absent in changed-line mode.
        "mode",
        "require_branch",
    }


def test_run_records_base_resolution_mode_on_a_merge_commit_head(
    git_repo: GitRepo, tmp_path: Path
):
    """(B008) The exact reported scenario: a feature branch merges its
    declared base back in before gating (`git merge origin/main`, a routine
    pre-gate sync), making HEAD a merge commit. `resolve_base` then resolves
    to the branch's own PRE-merge tip (first-parent), not a fork point --
    silently narrowing the changed-line floor to "what did the merge itself
    change" (usually nothing) instead of the branch's real accumulated work.
    This does not change that resolution; it proves the narrowing is now
    RECORDED (`judgment.resolved.base_resolution`), not silent."""
    git_repo.write(".gitignore", "cov.json\n")
    git_repo.git("checkout", "-q", "-b", "feature")
    git_repo.write("src/mod.py", "def f():\n    return 1\n")
    git_repo.commit_all("add mod.py on feature")

    cov_json = json.dumps(
        {
            "files": {
                "src/mod.py": {
                    "executed_lines": [1, 2],
                    "missing_lines": [],
                    "excluded_lines": [],
                }
            }
        }
    )
    write_cov = f"cat > cov.json <<'EOF'\n{cov_json}\nEOF"
    lane = f"""\
schema_version = 2

[lanes.package]
scope = "S1"
rigor = ["R0", "R1"]
enforcement = "gate"
argv = ["/bin/sh", "-c", {json.dumps(write_cov)}]
env = {{}}
env_passthrough = ["PATH"]
budget = "1m"
allow_argv_append = false

[lanes.package.isolation]
snapshot_selection = "repository"

[lanes.package.judge]
language = "python"
source_roots = ["src"]
fail_under = 100.0
allow_excluded = false
coverage = {{ format = "coverage-py-json", artifact = "cov.json" }}
base = "main"
"""
    # assay.toml is committed on `feature` BEFORE the merge, so the merge
    # commit itself -- not a later commit on top of it -- is the final HEAD
    # `assay run` sees, exactly matching the reported scenario.
    path = git_repo.write("assay.toml", lane)
    git_repo.commit_all("add assay.toml")
    feature_pre_merge_tip = git_repo.head()

    git_repo.git("checkout", "-q", "main")
    git_repo.write("on_main.py", "m = 1\n")
    base_tip = git_repo.commit_all("advance main")

    git_repo.git("checkout", "-q", "feature")
    git_repo.git("merge", "-q", "--no-edit", "main")
    merge_commit = git_repo.head()
    assert merge_commit != feature_pre_merge_tip  # a real merge commit exists

    code, out, err = run(["run", "package", "--file", str(path), "--verdict-json", "-"])

    assert code == 0, err
    document = json.loads(out)
    resolved = document["judgment"]["resolved"]
    # The narrowing itself, unchanged: base resolves to the branch's own
    # pre-merge tip, not base_tip and not a merge-base fork point.
    assert resolved["base"] == feature_pre_merge_tip
    assert resolved["base"] != base_tip
    # (B008) ...but it is now auditable rather than silent.
    assert resolved["base_resolution"] == "first-parent"


# --- structural failures: no verdict can even be built -----------------------


def test_run_maps_an_unknown_lane_name_to_a_clean_error(git_repo: GitRepo):
    lane = set_key(R0_LANE, "argv", '["/bin/sh", "-c", "exit 0"]')
    path = _write_and_commit_lane(git_repo, lane)

    code, out, err = run(["run", "nonexistent", "--file", str(path)])

    assert code == 2
    assert "no lane named" in err


def test_run_fails_cleanly_when_the_project_root_is_not_a_git_repo(tmp_path: Path):
    lane = set_key(R0_LANE, "argv", '["/bin/sh", "-c", "exit 0"]')
    path = tmp_path / "assay.toml"
    path.write_text(lane, encoding="utf-8")

    code, out, err = run(["run", "package", "--file", str(path)])

    assert code == 2
    assert "ERROR/GIT_FAILED" in err


# --- whole-worktree cleanliness (P17, sol finding 6) --------------------------


def test_run_refuses_a_dirty_tree_even_for_an_r0_only_lane(git_repo: GitRepo):
    """Sol finding 6's own words: "every assay run invocation records HEAD
    and runs the live tree regardless of rigor level." An uncommitted file
    OUTSIDE any declared judge (there is none, this is R0-only) still
    refuses the whole run before the command executes."""
    lane = set_key(R0_LANE, "argv", '["/bin/sh", "-c", "exit 0"]')
    path = _write_and_commit_lane(git_repo, lane)
    git_repo.write("uncommitted.txt", "dirty\n")

    code, out, err = run(["run", "package", "--file", str(path)])

    assert code == 3, "NO_MEASUREMENT is exit 3"
    assert "package: NO_MEASUREMENT/DIRTY_TREE (exit 3)" in out


# --- CLI structure -------------------------------------------------------------


def test_run_requires_a_lane_positional_argument():
    with pytest.raises(SystemExit) as excinfo:
        main(["run"])
    assert excinfo.value.code == 2


def test_an_attestation_timeout_outranks_an_adapter_that_would_refuse(
    git_repo: GitRepo, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
):
    """Reviewer combined-axis attack (new, outside the locked packet):
    *atomic attestation timeout* CROSSED WITH *adapter refusal*.

    The frozen terminal table gives these two rows separately —

    * "expiry during attestation batch": every evidence and claim is
      payload-free ``BUDGET_EXCEEDED``/``LANE_TIMEOUT``, and **no adapter or
      command** runs;
    * "adapter refusal after resolved evidence": all claims carry the adapter
      refusal.

    — but never crosses them, and the locked suite exercises the timeout only
    on a lane whose adapter resolves fine. The precedence is therefore
    untested: an implementation that resolved the adapter before the batch, or
    that let the refusal path catch the expiry, would emit a lane full of
    ``ERROR``/``BAD_LANE_CONFIG`` claims and silently lose the budget
    violation — a timeout downgraded into a config error.

    The command is a real side-effect sentinel, so "no command ran" is
    observed rather than assumed, and the adapter seam records any call at
    all: reaching it is itself the defect, independent of what it returns.
    """
    sentinel_file = tmp_path / "command-really-ran"
    lane_text = f"""\
schema_version = 2

[lanes.attested]
scope = "S1"
rigor = ["R0"]
enforcement = "gate"
argv = ["/bin/sh", "-c", "touch {sentinel_file}"]
env = {{}}
env_passthrough = ["PATH"]
budget = "5m"
allow_argv_append = false

[lanes.attested.judge]
attestation_dir = ".assay/attestations"
evidence = [
  {{source="attested",key="slow"}},
  {{source="attested",key="later"}},
]
"""
    git_repo.write(".gitignore", ".assay/\nverdict.json\n")
    git_repo.write("src/reviewed/child.py", "old\n")
    path = _write_and_commit_lane(git_repo, lane_text)
    head = git_repo.head()
    attestations = git_repo.path / ".assay/attestations"
    attestations.mkdir(parents=True, exist_ok=True)
    for key in ("slow", "later"):
        (attestations / f"{key}.json").write_text(
            json.dumps(
                {
                    "producer": "human:alice",
                    "attested_commit": head,
                    "reviewed_paths": ["src/reviewed"],
                }
            ),
            encoding="utf-8",
        )

    timeout = AssayError(
        "lane budget exhausted inside the attestation batch",
        outcome=Outcome.BUDGET_EXCEEDED,
        reason_code=ReasonCode.LANE_TIMEOUT,
    )

    def expire(*args, **kwargs):
        raise timeout

    adapter_calls: list[object] = []

    def refusing_adapter(lane):
        adapter_calls.append(lane)
        raise AssayError(
            "this build cannot reach the declared rigor",
            outcome=Outcome.ERROR,
            reason_code=ReasonCode.BAD_LANE_CONFIG,
        )

    monkeypatch.setattr(git, "verify_exact_commit", expire, raising=False)
    monkeypatch.setattr(cli_module, "_resolve_declared_adapters", refusing_adapter)

    destination = git_repo.path / "verdict.json"
    code, _, _ = run(
        ["run", "attested", "--file", str(path), "--verdict-json", str(destination)]
    )

    assert code == 4, "the budget terminal, not the adapter-refusal exit"
    document = json.loads(destination.read_text(encoding="utf-8"))
    assert verify_document(document) == [], "the artifact must still be complete v4"

    # the timeout outranks the refusal, everywhere
    assert (document["outcome"], document["reason_code"]) == (
        "BUDGET_EXCEEDED",
        "LANE_TIMEOUT",
    )
    assert [claim["status"] for claim in document["claims"]] == ["BUDGET_EXCEEDED"]
    assert [claim["reason_code"] for claim in document["claims"]] == ["LANE_TIMEOUT"]
    assert [item["key"] for item in document["evidence"]] == ["slow", "later"]
    for item in document["evidence"]:
        assert (item["status"], item["reason_code"]) == (
            "BUDGET_EXCEEDED",
            "LANE_TIMEOUT",
        )
        # payload-free: a timed-out identity never carries attested data
        assert "producer" not in item and "attested_commit" not in item
        assert "reviewed_paths" not in item
    assert "BAD_LANE_CONFIG" not in json.dumps(document), (
        "the adapter refusal must not appear anywhere: the batch expired first"
    )

    # ...and neither successor ever started
    assert adapter_calls == [], "no adapter may be resolved after batch expiry"
    assert not sentinel_file.exists(), "the lane command must never have run"
