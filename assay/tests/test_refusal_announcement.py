"""B053 (DA-D2 (a)+(b), controller reading DA-R1) — every refusal says WHY,
exactly once, through ONE emitter.

B053's finding: an :class:`~assay.errors.AssayError` carries a sentence that
names the file, the target or the declaration that refused, and that sentence
is thrown away the moment the error becomes a refusal
:class:`~assay.verdict.Claim` or :class:`~assay.verdict.Verdict`. The verdict
document carries only the closed ``(status, reason_code)`` pair — A-138/A-170
keep it that way, and DA-D2 (c) defers the on-the-wire ``detail`` field to the
v10 cut — so a consumer reads ``ERROR``/``BAD_LANE_CONFIG`` and has to guess.

**Why this module tests ONE emitter and not a handler at the CLI boundary.**
:mod:`assay.runner` converts an ``AssayError`` into a refusal claim or a
refusal verdict at ~15 places and RETURNS a document; nothing propagates to
:mod:`assay.cli`. A ``try`` around the ``run`` command would therefore print
for the handful of errors that DO escape and stay silent for exactly the ones
B053 filed. A-409 records the choice and names the rejected alternative.

Two halves, both proven here:

* **(a) the CLI** — the line reaches stderr, because :mod:`assay.cli` already
  passes its own ``stderr`` as the ``diagnostics`` stream.
* **(b) a library caller** — the same text reaches a caller-supplied stream
  with nothing written to the process's stderr at all.

The format is pinned per REASON CODE from :mod:`assay.errors`'s own closed
vocabulary rather than from a hand-copied list, so a code added to that
enumeration without a rendering is a failure here rather than a silent gap.
"""

from __future__ import annotations

import contextlib
import io
import json
import sys
from pathlib import Path

import pytest
from conftest import FakeAdapter, GitRepo, make_lane, make_r1_judge

from assay import runner
from assay.cli import main
from assay.errors import REASON_CODES, AssayError, Outcome, ReasonCode

ADAPTER = FakeAdapter()

#: The one line B053 asks for, as a prefix test can key on.
PREFIX = "assay: "


def _every_declared_pair() -> list[tuple[Outcome, ReasonCode]]:
    """Every ``(outcome, reason_code)`` pair :mod:`assay.errors` declares
    legal, derived from ``REASON_CODES`` itself.

    ``Outcome.PASS`` carries no reason code at all (A-051) and constructing
    an ``AssayError`` with it is a programming error, so it contributes no
    pairs — which is exactly what ``REASON_CODES[Outcome.PASS]`` being empty
    already says.
    """
    return [
        (outcome, code)
        for outcome, codes in REASON_CODES.items()
        for code in sorted(codes)
    ]


# --- the emitter itself, over the whole closed vocabulary ---------------------


def test_the_emitter_renders_every_declared_outcome_reason_pair_in_one_format():
    """DA-R1's format, verbatim: ``assay: {outcome}/{reason_code}: {message}``.

    Enumerated from ``errors.REASON_CODES`` rather than from a literal list,
    so this is a statement about the WHOLE vocabulary and not about the
    handful of codes whoever wrote the test happened to remember.
    """
    pairs = _every_declared_pair()
    assert pairs, "REASON_CODES declared no pairs at all -- the source is wrong"

    for outcome, code in pairs:
        stream = io.StringIO()
        message = f"the {code.value} sentence, naming its own cause"
        runner.announce_refusal(
            AssayError(message, outcome=outcome, reason_code=code),
            diagnostics=stream,
        )
        rendered = stream.getvalue()
        assert rendered == (
            f"assay: {outcome.value}/{code.value}: {message}\n"
        ), (outcome, code, rendered)


def test_every_reason_code_in_the_closed_enumeration_is_reachable_by_that_test():
    """The enumeration above is only a whole-vocabulary claim if every member
    of :class:`~assay.errors.ReasonCode` really appears in ``REASON_CODES``.

    Without this, a code added to the enum and forgotten in the outcome map
    would leave the test above quietly narrower than its docstring claims.
    """
    declared = {code for codes in REASON_CODES.values() for code in codes}
    assert declared == set(ReasonCode)


def test_the_emitter_writes_nothing_when_the_caller_asked_for_no_diagnostics():
    """``diagnostics=None`` is a caller who wants no diagnosis, not an error.

    Every internal call site passes the stream it was given, and most of
    :mod:`assay.runner`'s public entry points default it to ``None`` — a
    library caller that never opted in must not have text appear on some
    stream it did not name.
    """
    # No stream to assert on: the contract is that nothing is written
    # anywhere and nothing raises.
    runner.announce_refusal(
        AssayError(
            "unreadable", outcome=Outcome.ERROR, reason_code=ReasonCode.UNREADABLE_ARTIFACT
        ),
        diagnostics=None,
    )


def test_the_message_is_copied_byte_for_byte_and_not_reformatted():
    """The value of B053's line is the SENTENCE — which file, which key,
    which declaration. A helper that re-wrapped, truncated or re-cased it
    would lose precisely the part that is not already in the document."""
    message = (
        "coverage key 'src/a.py' and 'src/./a.py' normalise to the same "
        "file; assay refuses to guess which record is authoritative"
    )
    stream = io.StringIO()
    runner.announce_refusal(
        AssayError(
            message, outcome=Outcome.ERROR, reason_code=ReasonCode.UNREADABLE_ARTIFACT
        ),
        diagnostics=stream,
    )
    assert stream.getvalue().endswith(f": {message}\n")


# --- half (a): the CLI, on stderr, exactly once ------------------------------


def _refusal_lines(err: str) -> list[str]:
    """The refusal lines on a captured stderr.

    Deliberately NOT "every line starting with ``assay:``": B018's
    judge-provenance notice starts that way too and is not a refusal. A
    refusal line is the one whose second field is ``OUTCOME/REASON_CODE``.
    """
    outcomes = {o.value for o in Outcome}
    lines = []
    for line in err.splitlines():
        if not line.startswith(PREFIX):
            continue
        head = line[len(PREFIX) :].split(": ", 1)[0]
        if "/" in head and head.split("/", 1)[0] in outcomes:
            lines.append(line)
    return lines


def _r1_lane_writing(payload: str, *, base: str) -> str:
    script = f"cat > cov.json <<'EOF'\n{payload}\nEOF"
    return f"""\
schema_version = 2

[lanes.package]
scope = "S1"
rigor = ["R0", "R1"]
enforcement = "gate"
argv = ["/bin/sh", "-c", {json.dumps(script)}]
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
base = "{base}"
"""


def _seed_python_project(git_repo: GitRepo) -> str:
    (git_repo.path / "src").mkdir()
    git_repo.write(".gitignore", "cov.json\n")
    git_repo.write("src/mod.py", "def f():\n    return 1\n")
    base_rev = git_repo.commit_all("add mod.py")
    git_repo.write(
        "src/mod.py", "def f():\n    return 1\n\n\ndef g():\n    return 2\n"
    )
    git_repo.commit_all("add g")
    return base_rev


def test_a_broken_coverage_artifact_names_its_cause_on_stderr_exactly_once(
    git_repo: GitRepo,
):
    """The defect B053 filed, end to end through the installed CLI.

    The lane's own command writes a coverage artifact that is not a
    coverage-py JSON document at all. Before this fix the operator saw
    ``package: ERROR/UNREADABLE_ARTIFACT (exit 2)`` on stdout and NOTHING on
    stderr — the parser's own sentence, which names the artifact and what was
    wrong with it, was discarded when the error became the R1 claim.
    """
    base_rev = _seed_python_project(git_repo)
    lane = _r1_lane_writing("this is not JSON at all", base=base_rev)
    path = git_repo.write("assay.toml", lane)
    git_repo.commit_all("add assay.toml")

    out, err = io.StringIO(), io.StringIO()
    code = main(
        ["run", "package", "--file", str(path), "--verdict-json", "-"],
        stdout=out,
        stderr=err,
    )

    assert code == Outcome.ERROR.exit_code, err.getvalue()
    document = json.loads(out.getvalue())
    r1 = [claim for claim in document["claims"] if claim["rigor"] == "R1"]
    assert r1 and r1[0]["status"] == "ERROR", document
    lines = _refusal_lines(err.getvalue())
    # EXACTLY one: the whole point of a single emitter called once per
    # conversion is that a consumer's log does not carry the same refusal
    # twice under two spellings.
    assert len(lines) == 1, err.getvalue()
    assert lines[0].startswith("assay: ERROR/FORMAT_MISMATCH: "), lines
    # The line is not a restatement of the document: it carries the parser's
    # own sentence, which names the declared format and the two declarations
    # an operator has to choose between. None of that is on the wire.
    assert "coverage-py-json" in lines[0], lines
    assert "judge.coverage.artifact" in lines[0], lines


def test_an_unresolvable_judge_base_names_the_git_failure_exactly_once(
    git_repo: GitRepo,
):
    """A second, independently-reached conversion site (``evaluate_r1``'s own
    ``except AssayError``), so this module is not proving one code path twice.

    ``judge.base`` names a commit that does not exist, so base resolution
    fails inside R1 — not at the CLI boundary, and not in the artifact read.
    """
    _seed_python_project(git_repo)
    lane = _r1_lane_writing("{}", base="0" * 40)
    path = git_repo.write("assay.toml", lane)
    git_repo.commit_all("add assay.toml")

    out, err = io.StringIO(), io.StringIO()
    code = main(
        ["run", "package", "--file", str(path), "--verdict-json", "-"],
        stdout=out,
        stderr=err,
    )

    assert code != 0
    lines = _refusal_lines(err.getvalue())
    assert len(lines) == 1, err.getvalue()
    assert "/GIT_FAILED:" in lines[0], lines


def test_a_structural_refusal_at_the_cli_boundary_uses_the_same_one_line(
    git_repo: GitRepo,
):
    """:mod:`assay.cli`'s own outer handler — the print this emitter's format
    was taken from — now calls the emitter instead of spelling the format a
    second time. One spelling is the only reason the two cannot drift."""
    base_rev = _seed_python_project(git_repo)
    git_repo.write("assay.toml", _r1_lane_writing("{}", base=base_rev))
    git_repo.commit_all("add assay.toml")

    out, err = io.StringIO(), io.StringIO()
    code = main(
        ["run", "nonexistent", "--file", str(git_repo.path / "assay.toml")],
        stdout=out,
        stderr=err,
    )

    assert code == Outcome.ERROR.exit_code
    lines = _refusal_lines(err.getvalue())
    assert len(lines) == 1, err.getvalue()
    assert lines[0].startswith("assay: ERROR/BAD_LANE_CONFIG: "), lines
    assert "no lane named" in lines[0], lines


# --- half (b): a library caller, its own stream, no stderr -------------------


def test_a_library_caller_gets_the_same_line_on_its_own_stream_and_not_stderr(
    git_repo: GitRepo,
):
    """DA-D2 half (b), against :func:`assay.runner.run_lane` DIRECTLY — no
    CLI in the picture at all.

    The caller passes its own ``diagnostics`` stream and gets the refusal
    sentence there. The process's real ``sys.stderr`` is captured for the
    duration and must stay empty: a library that writes to the host's stderr
    behind its caller's back is the behaviour ciu's ``LaneResult`` consumer
    cannot use.
    """
    git_repo.write(".gitignore", "cov.json\n")
    git_repo.write("pkg/mod.zzz", "BASE\n")
    base_rev = git_repo.commit_all("add pkg base")
    git_repo.write("pkg/mod.zzz", "BASE\nLINE2\nLINE3\n")
    head_rev = git_repo.commit_all("add pkg head")

    judge = make_r1_judge(source_root_paths=(git_repo.path / "pkg",), base=base_rev)
    lane = make_lane(
        rigor=("R0", "R1"),
        judge=judge,
        argv=("/bin/sh", "-c", "printf 'not a coverage document' > cov.json"),
    )
    diagnostics = io.StringIO()
    real_stderr = io.StringIO()

    with contextlib.redirect_stderr(real_stderr):
        verdict = runner.run_lane(
            lane,
            commit=head_rev,
            repo=git_repo.path,
            project_root=git_repo.path,
            adapter=ADAPTER,
            assay_version="0.1.0",
            diagnostics=diagnostics,
        )

    assert verdict.outcome is Outcome.ERROR, verdict
    lines = _refusal_lines(diagnostics.getvalue())
    assert len(lines) == 1, diagnostics.getvalue()
    assert lines[0].startswith("assay: ERROR/"), lines
    assert real_stderr.getvalue() == "", real_stderr.getvalue()


def test_a_library_caller_that_names_no_stream_still_gets_a_verdict_and_no_output(
    git_repo: GitRepo,
):
    """The default. ``diagnostics`` is optional on every public entry point,
    and omitting it must change the DIAGNOSIS only — never the verdict."""
    git_repo.write(".gitignore", "cov.json\n")
    git_repo.write("pkg/mod.zzz", "BASE\n")
    base_rev = git_repo.commit_all("add pkg base")
    git_repo.write("pkg/mod.zzz", "BASE\nLINE2\nLINE3\n")
    head_rev = git_repo.commit_all("add pkg head")

    judge = make_r1_judge(source_root_paths=(git_repo.path / "pkg",), base=base_rev)
    lane = make_lane(
        rigor=("R0", "R1"),
        judge=judge,
        argv=("/bin/sh", "-c", "printf 'not a coverage document' > cov.json"),
    )
    real_stderr = io.StringIO()

    with contextlib.redirect_stderr(real_stderr):
        verdict = runner.run_lane(
            lane,
            commit=head_rev,
            repo=git_repo.path,
            project_root=git_repo.path,
            adapter=ADAPTER,
            assay_version="0.1.0",
        )

    assert verdict.outcome is Outcome.ERROR, verdict
    assert real_stderr.getvalue() == "", real_stderr.getvalue()


# --- DA-R3: the five refusals that used to carry no message at all ----------
#
# Generation 2 landed the emitter and reported (decision ask 1) that five
# refusal sites call `refuse_lane`/`refuse_all` with a bare
# `(status, reason_code)` literal and therefore printed nothing. The
# controller ruled DA-R3: the emitter's contract is a MESSAGE, not an
# exception, so each of those sites composes its sentence where the fact is
# known -- the offending paths, the two commits, the missing tool, the
# missing variable, the shard spec -- and goes through the same emitter.
# "Every refusal reachable through `assay run` prints exactly one line" then
# holds without qualification.


def _r0_lane(*, env_required: tuple[str, ...] = ()) -> str:
    required = json.dumps(list(env_required))
    passthrough = json.dumps(["PATH", *env_required])
    return f"""\
schema_version = 2

[lanes.package]
scope = "S1"
rigor = ["R0"]
enforcement = "gate"
argv = ["/bin/sh", "-c", "exit 0"]
env = {{}}
env_passthrough = {passthrough}
env_required = {required}
budget = "1m"
allow_argv_append = false
"""


def test_a_dirty_tree_names_the_uncommitted_files_on_a_higher_rigor_lane(
    git_repo: GitRepo,
):
    """DA-R3, site 1 of 5: ``_run_higher_rigor_lane``'s pre-snapshot tree
    guard. A snapshot lane measures the RESOLVED COMMIT, so an uncommitted
    file is invisible to it -- and before DA-R3 the operator was told only
    ``NO_MEASUREMENT/DIRTY_TREE`` with no hint of which file."""
    base_rev = _seed_python_project(git_repo)
    path = git_repo.write("assay.toml", _r1_lane_writing("{}", base=base_rev))
    git_repo.commit_all("add assay.toml")
    # Untracked, and deliberately NOT under `src/` -- this is the whole-repo
    # guard, not `measurability.check_dirty_tree`'s source-root-scoped one.
    git_repo.write("stray-note.txt", "uncommitted\n")

    out, err = io.StringIO(), io.StringIO()
    code = main(
        ["run", "package", "--file", str(path), "--verdict-json", "-"],
        stdout=out,
        stderr=err,
    )

    assert code == Outcome.NO_MEASUREMENT.exit_code, err.getvalue()
    lines = _refusal_lines(err.getvalue())
    assert len(lines) == 1, err.getvalue()
    assert lines[0].startswith("assay: NO_MEASUREMENT/DIRTY_TREE: "), lines
    assert "stray-note.txt" in lines[0], lines


def test_a_dirty_tree_names_the_uncommitted_files_on_a_direct_r0_lane(
    git_repo: GitRepo,
):
    """DA-R3, site 2 of 5: the SAME refusal on the direct-R0 path, which has
    its own guard and its own `refuse_lane` call (A-189: an R0-only lane
    never enters the snapshot state machine)."""
    git_repo.write("src/mod.py", "def f():\n    return 1\n")
    path = git_repo.write("assay.toml", _r0_lane())
    git_repo.commit_all("add lane")
    git_repo.write("stray-note.txt", "uncommitted\n")

    out, err = io.StringIO(), io.StringIO()
    code = main(
        ["run", "package", "--file", str(path), "--verdict-json", "-"],
        stdout=out,
        stderr=err,
    )

    assert code == Outcome.NO_MEASUREMENT.exit_code, err.getvalue()
    lines = _refusal_lines(err.getvalue())
    assert len(lines) == 1, err.getvalue()
    assert lines[0].startswith("assay: NO_MEASUREMENT/DIRTY_TREE: "), lines
    assert "stray-note.txt" in lines[0], lines


def test_a_head_that_moved_names_both_commits(git_repo: GitRepo):
    """DA-R3, site 3 of 5: ``HEAD_CHANGED``.

    Driven through :func:`assay.runner.run_lane` rather than the CLI because
    the CLI resolves *commit* from ``HEAD`` itself -- the disagreement this
    guard exists to catch is a caller's, and a library caller is the only
    one that can state it deterministically. The line must name BOTH revisions,
    because "HEAD changed" without the two values does not say which way.
    """
    git_repo.write(".gitignore", "cov.json\n")
    git_repo.write("pkg/mod.zzz", "BASE\n")
    base_rev = git_repo.commit_all("add pkg base")
    git_repo.write("pkg/mod.zzz", "BASE\nLINE2\n")
    head_rev = git_repo.commit_all("add pkg head")

    judge = make_r1_judge(source_root_paths=(git_repo.path / "pkg",), base=base_rev)
    lane = make_lane(rigor=("R0", "R1"), judge=judge)
    diagnostics = io.StringIO()

    verdict = runner.run_lane(
        lane,
        # The caller says the tree is at `base_rev`; git says `head_rev`.
        commit=base_rev,
        repo=git_repo.path,
        project_root=git_repo.path,
        adapter=ADAPTER,
        assay_version="0.1.0",
        diagnostics=diagnostics,
    )

    assert verdict.outcome is Outcome.NO_MEASUREMENT, verdict
    lines = _refusal_lines(diagnostics.getvalue())
    assert len(lines) == 1, diagnostics.getvalue()
    assert lines[0].startswith("assay: NO_MEASUREMENT/HEAD_CHANGED: "), lines
    assert base_rev in lines[0] and head_rev in lines[0], lines


def test_a_missing_external_tool_names_the_tool(git_repo: GitRepo):
    """DA-R3, site 4 of 5: ``MISSING_EXTERNAL_TOOL``.

    The adapter declares a tool that is not on ``PATH``. Which tool is the
    only actionable fact in the whole refusal, and it was the one thing the
    operator could not see.
    """
    git_repo.write("pkg/mod.zzz", "BASE\n")
    base_rev = git_repo.commit_all("add pkg base")
    git_repo.write("pkg/mod.zzz", "BASE\nLINE2\n")
    head_rev = git_repo.commit_all("add pkg head")

    judge = make_r1_judge(source_root_paths=(git_repo.path / "pkg",), base=base_rev)
    lane = make_lane(rigor=("R0", "R1"), judge=judge)
    diagnostics = io.StringIO()

    verdict = runner.run_lane(
        lane,
        commit=head_rev,
        repo=git_repo.path,
        project_root=git_repo.path,
        adapter=FakeAdapter(external_tools=("assay-no-such-tool-b053",)),
        assay_version="0.1.0",
        diagnostics=diagnostics,
    )

    assert verdict.outcome is Outcome.NO_MEASUREMENT, verdict
    lines = _refusal_lines(diagnostics.getvalue())
    assert len(lines) == 1, diagnostics.getvalue()
    assert lines[0].startswith(
        "assay: NO_MEASUREMENT/MISSING_EXTERNAL_TOOL: "
    ), lines
    assert "assay-no-such-tool-b053" in lines[0], lines


def test_a_missing_required_env_var_names_the_variable(git_repo: GitRepo):
    """DA-R3, site 5a of 5: the ``env_required`` refusal.

    Distinct from the infrastructure-env refusal generation 2 already
    covered: this one is `lane.env_required` against the passthrough source,
    decided before any Git work at all, and it shared the generic
    ``ERROR``/``BAD_LANE_CONFIG`` terminal with a dozen unrelated causes.
    """
    git_repo.write("src/mod.py", "def f():\n    return 1\n")
    path = git_repo.write(
        "assay.toml", _r0_lane(env_required=("ASSAY_B053_NO_SUCH_VAR",))
    )
    git_repo.commit_all("add lane")

    out, err = io.StringIO(), io.StringIO()
    code = main(
        ["run", "package", "--file", str(path), "--verdict-json", "-"],
        stdout=out,
        stderr=err,
    )

    assert code == Outcome.ERROR.exit_code, err.getvalue()
    lines = _refusal_lines(err.getvalue())
    assert len(lines) == 1, err.getvalue()
    assert lines[0].startswith("assay: ERROR/BAD_LANE_CONFIG: "), lines
    assert "ASSAY_B053_NO_SUCH_VAR" in lines[0], lines
    assert "env_required" in lines[0], lines


def test_a_malformed_shard_names_the_spec_it_could_not_parse(git_repo: GitRepo):
    """DA-R3, site 5b of 5: the bad-``--shard`` refusal.

    The spec the operator typed is the fact; without it the refusal is
    ``ERROR``/``BAD_LANE_CONFIG`` with nothing to correct.
    """
    git_repo.write("src/mod.py", "def f():\n    return 1\n")
    path = git_repo.write("assay.toml", _r0_lane())
    git_repo.commit_all("add lane")

    out, err = io.StringIO(), io.StringIO()
    code = main(
        [
            "run",
            "package",
            "--file",
            str(path),
            "--shard",
            "one-of-two",
            "--verdict-json",
            "-",
        ],
        stdout=out,
        stderr=err,
    )

    assert code == Outcome.ERROR.exit_code, err.getvalue()
    lines = _refusal_lines(err.getvalue())
    assert len(lines) == 1, err.getvalue()
    assert lines[0].startswith("assay: ERROR/BAD_LANE_CONFIG: "), lines
    assert "one-of-two" in lines[0], lines
    assert "--shard" in lines[0], lines


# --- DA-R4: an announcement whose claim never reaches the verdict -----------


def _equivalence_r2_lane(*, base: str, command: str) -> str:
    """A SQL R2 lane declaring an ``equivalence_artifact`` its own command
    never writes (A-279) -- the one early-R2 refusal decided BEFORE the
    lane's own command outcome is known, and therefore the only one that can
    be superseded. ``equivalence_artifact`` is a ``sql``-only key (P34/W4),
    so this lane is genuinely SQL rather than a Python lane with a SQL field.
    """
    return f"""\
schema_version = 2

[lanes.package]
scope = "S1"
rigor = ["R0", "R2"]
enforcement = "gate"
argv = ["/bin/sh", "-c", {json.dumps(command)}]
env = {{}}
env_passthrough = ["PATH"]
budget = "2m"
allow_argv_append = false

[lanes.package.isolation]
snapshot_selection = "repository"

[lanes.package.judge]
language = "sql"
source_roots = ["db"]
base = "{base}"

[lanes.package.judge.mutation]
jobs = 1
max_mutants = 5
operators = ["sql:drop-check"]
equivalence_artifact = ".assay/schema-dump.sql"
"""


def _seed_r2_project(git_repo: GitRepo) -> str:
    git_repo.write(".gitignore", ".assay/\n")
    git_repo.write("db/schema.sql", "create table a (id int);\n")
    base_rev = git_repo.commit_all("add schema.sql")
    git_repo.write(
        "db/schema.sql",
        "create table a (id int);\n"
        "create table b (id int check (id > 0));\n",
    )
    git_repo.commit_all("add table b")
    return base_rev


def test_a_surviving_early_r2_refusal_is_announced_once(git_repo: GitRepo):
    """The control for the test below: when the lane's own command PASSES,
    the early R2 refusal IS the R2 claim, so its sentence belongs on the
    stream."""
    base_rev = _seed_r2_project(git_repo)
    path = git_repo.write(
        "assay.toml", _equivalence_r2_lane(base=base_rev, command="exit 0")
    )
    git_repo.commit_all("add assay.toml")

    out, err = io.StringIO(), io.StringIO()
    code = main(
        ["run", "package", "--file", str(path), "--verdict-json", "-"],
        stdout=out,
        stderr=err,
    )

    assert code != 0, err.getvalue()
    document = json.loads(out.getvalue())
    r2 = [claim for claim in document["claims"] if claim["rigor"] == "R2"]
    assert r2 and r2[0]["reason_code"] == "EXEC_FAILED", document
    lines = _refusal_lines(err.getvalue())
    assert len(lines) == 1, err.getvalue()
    assert "equivalence_artifact" in lines[0], lines


def test_an_early_r2_refusal_the_verdict_discards_is_never_announced(
    git_repo: GitRepo,
):
    """DA-R4. The lane's own command FAILS, so ``_run_prepared_lane`` builds
    the R2 claim from the command result and throws the early claim away.

    A line about a refusal that never reaches the verdict cannot be
    reconciled with the document a consumer is holding, so the announcement
    is deferred to the point where the final claim is CHOSEN, not made where
    the early claim is built. No general buffer: only this one site defers,
    because only this one site is decided before the command outcome is
    known.
    """
    base_rev = _seed_r2_project(git_repo)
    path = git_repo.write(
        "assay.toml", _equivalence_r2_lane(base=base_rev, command="exit 7")
    )
    git_repo.commit_all("add assay.toml")

    out, err = io.StringIO(), io.StringIO()
    code = main(
        ["run", "package", "--file", str(path), "--verdict-json", "-"],
        stdout=out,
        stderr=err,
    )

    assert code != 0, err.getvalue()
    document = json.loads(out.getvalue())
    r2 = [claim for claim in document["claims"] if claim["rigor"] == "R2"]
    assert r2, document
    # The equivalence refusal is NOT the claim the document carries: the
    # command's own failure is.
    assert r2[0]["reason_code"] == "COMMAND_FAILED", document
    lines = _refusal_lines(err.getvalue())
    assert not any("equivalence_artifact" in line for line in lines), lines
