"""B053 (DA-D2 (a)+(b), controller reading DA-R1) — every refusal says WHY,
exactly once, through ONE emitter.

B053's finding: an :class:`~assay.errors.AssayError` carries a sentence that
names the file, the target or the declaration that refused, and that sentence
is thrown away the moment the error becomes a refusal
:class:`~assay.verdict.Claim` or :class:`~assay.verdict.Verdict`. The verdict
document used to carry only the closed ``(status, reason_code)`` pair — A-138/
A-170 keep that pair the only MACHINE-READABLE statement in it — so a consumer
read ``ERROR``/``BAD_LANE_CONFIG`` and had to guess. Half (c), A-428, landed by
A-439, puts the same sentence in the document as ``claim.detail``, bounded and
declared-not-verified; the last section of this module is about it, and about
the one property that makes it worth having: it is a BYTE COPY of the line,
never a second composition.

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

from assay import runner, verdict
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


# --- DA-R8: the POST-command guards, on both dispatch paths -------------------
#
# R-1's round-1 BLOCKER 1. DA-R3's five sites are the PRE-run guards: they
# prove what existed BEFORE the lane's command. The post-command dirt/HEAD
# guards are a different pair of sites, one per dispatch path, and both still
# refused from a bare `(status, reason_code)` literal -- while `CHANGES.md`,
# this module's own DA-R3 section and `runner.py`'s DA-R3 comment all claimed
# "every refusal reachable through `assay run`, with no exceptions".
#
# This is the `DIRTY_TREE` an operator is LEAST able to explain: they ran it
# on a tree they know was clean, and the dirt is their own command's. So the
# sentence must blame the command and prescribe a remedy that works --
# "commit or stash" is wrong here, because the next run reproduces it.


def _r0_lane_running(command: str) -> str:
    """An R0-only lane whose command is *command* -- the direct dispatch path
    (A-189: an R0-only lane never enters the snapshot state machine)."""
    return f"""\
schema_version = 2

[lanes.package]
scope = "S1"
rigor = ["R0"]
enforcement = "gate"
argv = ["/bin/sh", "-c", {json.dumps(command)}]
env = {{}}
env_passthrough = ["PATH"]
budget = "1m"
allow_argv_append = false
"""


def test_a_command_that_dirties_the_tree_blames_the_command_on_a_direct_r0_lane(
    git_repo: GitRepo,
):
    """DA-R8, dispatch path 1: ``_finish_direct_r0_lane``'s post-command
    guard, which had no ``diagnostics`` parameter at all before this fix.

    The tree is committed clean; the lane's own command leaves
    ``leftover.txt`` behind.
    """
    path = git_repo.write(
        "assay.toml", _r0_lane_running("echo output > leftover.txt")
    )
    git_repo.commit_all("add lane")

    out, err = io.StringIO(), io.StringIO()
    code = main(
        ["run", "package", "--file", str(path), "--verdict-json", "-"],
        stdout=out,
        stderr=err,
    )

    assert code == Outcome.NO_MEASUREMENT.exit_code, err.getvalue()
    document = json.loads(out.getvalue())
    assert document["claims"][0]["reason_code"] == "DIRTY_TREE", document
    lines = _refusal_lines(err.getvalue())
    assert len(lines) == 1, err.getvalue()
    assert lines[0].startswith("assay: NO_MEASUREMENT/DIRTY_TREE: "), lines
    assert "leftover.txt" in lines[0], lines
    # The remedy must be the one that applies. "Commit or stash" is the
    # PRE-run sentence and is actively misleading here.
    assert "the lane's own command" in lines[0], lines
    assert "commit or a stash reproduces it" in lines[0], lines


def test_a_command_that_moves_head_names_both_revisions_on_a_direct_r0_lane(
    git_repo: GitRepo,
):
    """DA-R8, the other branch of the same guard: a command that COMMITS
    leaves a perfectly clean tree (A-178), so the dirt check passes and the
    HEAD comparison is what catches it. The line must name both revisions --
    "HEAD changed" without the two values does not say which way.
    """
    path = git_repo.write(
        "assay.toml", _r0_lane_running("git commit -q --allow-empty -m moved")
    )
    pre_run_head = git_repo.commit_all("add lane")

    out, err = io.StringIO(), io.StringIO()
    code = main(
        ["run", "package", "--file", str(path), "--verdict-json", "-"],
        stdout=out,
        stderr=err,
    )

    assert code == Outcome.NO_MEASUREMENT.exit_code, err.getvalue()
    post_run_head = git_repo.head()
    assert post_run_head != pre_run_head, "the command did not actually commit"
    document = json.loads(out.getvalue())
    assert document["claims"][0]["reason_code"] == "HEAD_CHANGED", document
    lines = _refusal_lines(err.getvalue())
    assert len(lines) == 1, err.getvalue()
    assert lines[0].startswith("assay: NO_MEASUREMENT/HEAD_CHANGED: "), lines
    assert pre_run_head in lines[0] and post_run_head in lines[0], lines
    assert "the lane's own command" in lines[0], lines


def _r1_lane_running(command: str, *, base: str) -> str:
    """A higher-rigor (R0+R1) lane whose command is *command* -- the snapshot
    dispatch path, where the guard runs against the materialized snapshot."""
    return f"""\
schema_version = 2

[lanes.package]
scope = "S1"
rigor = ["R0", "R1"]
enforcement = "gate"
argv = ["/bin/sh", "-c", {json.dumps(command)}]
env = {{}}
env_passthrough = ["PATH"]
budget = "2m"
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


def test_a_command_that_dirties_the_snapshot_blames_the_command_on_an_r1_lane(
    git_repo: GitRepo,
):
    """DA-R8, dispatch path 2: ``_execute_snapshot_unit``'s post-command
    guard, consumed in ``_run_prepared_lane``.

    The facts now travel out on ``SnapshotUnitResult`` beside ``post_reason``
    -- the producer has the fact, the consumer has the ``diagnostics``
    stream -- exactly as ``profile_error`` already did.
    """
    base_rev = _seed_python_project(git_repo)
    path = git_repo.write(
        "assay.toml",
        _r1_lane_running("echo output > leftover.txt", base=base_rev),
    )
    git_repo.commit_all("add assay.toml")

    out, err = io.StringIO(), io.StringIO()
    code = main(
        ["run", "package", "--file", str(path), "--verdict-json", "-"],
        stdout=out,
        stderr=err,
    )

    assert code == Outcome.NO_MEASUREMENT.exit_code, err.getvalue()
    document = json.loads(out.getvalue())
    r1 = [claim for claim in document["claims"] if claim["rigor"] == "R1"]
    assert r1 and r1[0]["reason_code"] == "DIRTY_TREE", document
    lines = _refusal_lines(err.getvalue())
    assert len(lines) == 1, err.getvalue()
    assert lines[0].startswith("assay: NO_MEASUREMENT/DIRTY_TREE: "), lines
    assert "leftover.txt" in lines[0], lines
    assert "the lane's own command" in lines[0], lines


# --- SF-6 / DA-R15: the LAST silent terminal in runner.py ------------------
#
# `_run_higher_rigor_lane`'s `except RuntimeError` replaced the highest
# higher-rigor claim with ERROR/GIT_FAILED and said nothing -- four lines
# below the `except OSError` A-421 fixed, and the last such site in the
# module (R-1 round 2 re-audited every terminal). MEASURED, and recorded in
# A-426: it is NOT reachable from `assay run` by any operator input. The only
# raiser is `isolation.prepare_snapshot`'s own programmer-error leak
# detection, which fires when assay's own code leaves a materialization open
# -- i.e. an assay bug, not a lane, a tool or a repository state. So it is
# covered at the seam instead, in two real halves:
#
#   1. `tests/test_isolation.py::test_a_leaked_materialization_raises_at_
#      context_exit` proves the exception and its sentence are real, from a
#      real repository and a real leak, with no double at all;
#   2. the test below proves the HANDLER announces, driving the same
#      exception class carrying that same real sentence in through
#      `scratch_root_factory` -- assay's OWN cleanup seam, the one A-193/
#      A-194's rule is written about and the one the timeout module already
#      uses for this purpose. A-334 governs evidence about EXTERNAL systems;
#      the subject here is assay's own except branch.

#: Verbatim from `isolation.py`'s own raise (pinned real by the companion
#: test named above), so this module cannot drift into asserting a sentence
#: no code produces.
REAL_LEAK_MESSAGE = (
    "prepare_snapshot's context closed with 1 live snapshot context(s); "
    "close every materialization first"
)


def _scratch_root_factory_leaking_on_exit():
    @contextlib.contextmanager
    def factory():
        with runner.default_scratch_root() as root:
            yield root
        raise RuntimeError(REAL_LEAK_MESSAGE)

    return factory


def test_a_cleanup_time_state_leak_says_so_instead_of_refusing_in_silence(
    git_repo: GitRepo,
):
    """DA-R15 / R-1 round 2's SF-6.

    The lane's own work COMPLETED -- there is an outcome in hand -- and only
    the cleanup then reported leaked state. That is the one occurrence this
    module owns (anything else is a genuine bug and still propagates, proven
    by the control below), and it replaces the R1 claim with
    ``ERROR``/``GIT_FAILED``. Before this fix it did so without a word, so
    ``CHANGES.md``'s "every refusal reachable through ``assay run``, with no
    exceptions" was one site short of true.
    """
    git_repo.write(".gitignore", "cov.json\n")
    git_repo.write("pkg/mod.zzz", "BASE\n")
    base_rev = git_repo.commit_all("add pkg base")
    git_repo.write("pkg/mod.zzz", "BASE\nLINE2\n")
    head_rev = git_repo.commit_all("add pkg head")

    judge = make_r1_judge(source_root_paths=(git_repo.path / "pkg",), base=base_rev)
    # A VALID artifact: the lane's own work must genuinely complete, so that
    # the only refusal in the whole run is the cleanup one under test.
    payload = (
        '{"files": {"pkg/mod.zzz": {"executed_lines": [1, 2], '
        '"missing_lines": [], "excluded_lines": []}}}'
    )
    lane = make_lane(
        rigor=("R0", "R1"),
        judge=judge,
        argv=("/bin/sh", "-c", f"printf '%s' '{payload}' > cov.json"),
    )
    diagnostics = io.StringIO()

    verdict = runner.run_lane(
        lane,
        commit=head_rev,
        repo=git_repo.path,
        project_root=git_repo.path,
        adapter=ADAPTER,
        assay_version="0.1.0",
        scratch_root_factory=_scratch_root_factory_leaking_on_exit(),
        diagnostics=diagnostics,
    )

    r1 = [claim for claim in verdict.claims if claim.rigor == "R1"]
    assert r1 and r1[0].status is Outcome.ERROR, verdict.claims
    assert r1[0].reason_code is ReasonCode.GIT_FAILED, r1[0]
    lines = _refusal_lines(diagnostics.getvalue())
    assert len(lines) == 1, diagnostics.getvalue()
    assert lines[0].startswith("assay: ERROR/GIT_FAILED: "), lines
    assert "snapshot cleanup detected leaked state" in lines[0], lines
    assert "close every materialization first" in lines[0], lines


def test_a_state_leak_with_no_outcome_in_hand_still_propagates_and_stays_silent(
    git_repo: GitRepo,
):
    """The control, and the reason the announcement sits on ONE side of the
    fork.

    With no outcome in hand the ``RuntimeError`` is a genuine programmer
    error and is re-raised to the caller with its traceback intact. No
    verdict is written, so a refusal line printed here would describe a
    document that does not exist -- and B053's contract is one line per
    refusal, not one line per exception.
    """
    git_repo.write("pkg/mod.zzz", "BASE\n")
    base_rev = git_repo.commit_all("add pkg base")
    git_repo.write("pkg/mod.zzz", "BASE\nLINE2\n")
    head_rev = git_repo.commit_all("add pkg head")

    judge = make_r1_judge(source_root_paths=(git_repo.path / "pkg",), base=base_rev)
    lane = make_lane(rigor=("R0", "R1"), judge=judge)
    diagnostics = io.StringIO()

    @contextlib.contextmanager
    def factory():
        # Raises BEFORE any lane work happens, so no outcome is ever built.
        raise RuntimeError(REAL_LEAK_MESSAGE)
        yield  # pragma: no cover - unreachable, required to make this a CM

    with pytest.raises(RuntimeError):
        runner.run_lane(
            lane,
            commit=head_rev,
            repo=git_repo.path,
            project_root=git_repo.path,
            adapter=ADAPTER,
            assay_version="0.1.0",
            scratch_root_factory=factory,
            diagnostics=diagnostics,
        )

    assert _refusal_lines(diagnostics.getvalue()) == [], diagnostics.getvalue()


# --- (c) the same sentence, on the wire: `claim.detail` -----------------------
#
# B053/DA-D2 (c), A-428, landed by A-439. Halves (a) and (b) above put the
# refusal's sentence on a STREAM. A consumer that archives the verdict and
# throws the stream away still had ``ERROR``/``BAD_LANE_CONFIG`` and a guess.
# `claim.detail` is that same sentence, in the document -- a BYTE COPY, never
# a second composition, which is the property every test below is really
# about.


def _detail_of(claim) -> str | None:
    """The claim's detail, whether it is a dataclass or a wire dict."""
    return claim["detail"] if isinstance(claim, dict) else claim.detail


def _dropped_of(claim) -> int | None:
    return (
        claim["detail_dropped_bytes"]
        if isinstance(claim, dict)
        else claim.detail_dropped_bytes
    )


def _message_of(line: str) -> str:
    """The MESSAGE half of one announced line: ``assay: {pair}: {message}``.

    Split at most twice, so a message containing ``": "`` (most of them do --
    they quote paths and declarations) survives intact.
    """
    return line.split(": ", 2)[2]


def _sole_announced_message(diagnostics: str) -> str:
    lines = _refusal_lines(diagnostics)
    assert len(lines) == 1, diagnostics
    return _message_of(lines[0])


# --- the bound itself, as a unit ---------------------------------------------


def test_a_short_message_is_carried_whole_and_drops_nothing():
    detail = verdict.refusal_detail("lane 'package': the artifact is absent")
    assert detail == verdict.RefusalDetail(
        text="lane 'package': the artifact is absent", dropped_bytes=0
    )


def test_an_empty_message_produces_no_detail_at_all():
    """A-428: absent says "no refusal produced text". An EMPTY string cannot
    say it -- `Claim` rejects one -- so absent and empty must not be two
    spellings of one state."""
    assert verdict.refusal_detail("") is None


def test_truncation_keeps_the_head_and_counts_the_bytes_it_dropped():
    """A-428, stated and now enforced: the opposite end from B014's command
    tails. A composed refusal puts the identifying facts FIRST."""
    message = "HEAD-IDENTIFIES-THE-CAUSE " + ("x" * 4000)
    detail = verdict.refusal_detail(message)

    assert detail is not None
    assert detail.text.startswith("HEAD-IDENTIFIES-THE-CAUSE ")
    assert len(detail.text.encode("utf-8")) == verdict.CLAIM_DETAIL_BYTES
    assert detail.dropped_bytes == len(message.encode("utf-8")) - len(
        detail.text.encode("utf-8")
    )
    # The retained text plus what was dropped IS the original, exactly: the
    # count is a statement about this message, not an approximation.
    assert len(detail.text.encode("utf-8")) + detail.dropped_bytes == len(
        message.encode("utf-8")
    )


def test_the_cut_lands_on_a_codepoint_boundary_and_never_inside_a_character():
    """The bound is stated in BYTES and the text is measured in bytes, so a
    multi-byte character straddling it is dropped WHOLE. A byte-slicing
    implementation would decode to U+FFFD here, or raise."""
    # 'e' + 683 three-byte characters = 1 + 2049 bytes: the character
    # covering byte 2048 begins at 2047 and must be dropped entire.
    message = "e" + "中" * 683
    assert len(message.encode("utf-8")) == 2050

    detail = verdict.refusal_detail(message)
    assert detail is not None
    assert "�" not in detail.text
    assert detail.text == "e" + "中" * 682
    assert len(detail.text.encode("utf-8")) == 2047
    assert detail.dropped_bytes == 3
    # And the result is a document this layer accepts, which is the only
    # reason the boundary walk exists.
    assert verdict.Claim(
        rigor="R1",
        source="computed",
        status=Outcome.ERROR,
        verified_by_assay=True,
        reason_code=ReasonCode.UNREADABLE_ARTIFACT,
        detail=detail.text,
        detail_dropped_bytes=detail.dropped_bytes,
    ).detail == detail.text


def test_the_emitter_returns_exactly_the_sentence_it_printed():
    """The whole point of routing `detail` through the emitter's RETURN
    value: one expression, one site, one reading of ``str(exc)``."""
    message = "coverage key 'src/a.py' and 'src/./a.py' normalise to the same file"
    stream = io.StringIO()
    detail = runner.announce_refusal(
        AssayError(
            message, outcome=Outcome.ERROR, reason_code=ReasonCode.UNREADABLE_ARTIFACT
        ),
        diagnostics=stream,
    )
    assert detail is not None
    assert detail.text == message
    assert detail.text == _sole_announced_message(stream.getvalue())


def test_the_emitter_still_returns_the_sentence_when_nothing_is_printed():
    """A caller that passed ``diagnostics=None`` asked for no line on ITS
    stream. It did not ask for a document that cannot say why it refused."""
    detail = runner.announce_refusal(
        AssayError(
            "the declared adapter 'ada' is not registered",
            outcome=Outcome.ERROR,
            reason_code=ReasonCode.BAD_LANE_CONFIG,
        ),
        diagnostics=None,
    )
    assert detail is not None
    assert detail.text == "the declared adapter 'ada' is not registered"


# --- per producing site, end to end ------------------------------------------


def test_the_r1_conversion_site_puts_its_own_sentence_on_the_r1_claim(
    git_repo: GitRepo,
):
    """Producing site 1: ``evaluate_r1``'s ``except AssayError``.

    The same scenario the FIRST test in this module drives -- a coverage
    artifact that is not a coverage-py document -- read from the DOCUMENT
    this time. Before A-439 the artifact carried
    ``ERROR``/``FORMAT_MISMATCH`` and nothing else.
    """
    base_rev = _seed_python_project(git_repo)
    path = git_repo.write("assay.toml", _r1_lane_writing("not JSON", base=base_rev))
    git_repo.commit_all("add assay.toml")

    out, err = io.StringIO(), io.StringIO()
    main(
        ["run", "package", "--file", str(path), "--verdict-json", "-"],
        stdout=out,
        stderr=err,
    )

    document = json.loads(out.getvalue())
    r1 = next(c for c in document["claims"] if c["rigor"] == "R1")
    assert r1["status"] == "ERROR"
    assert _detail_of(r1) == _sole_announced_message(err.getvalue())
    assert _dropped_of(r1) == 0
    # The R0 claim really PASSED -- it is a measurement, not a refusal -- so
    # it carries no sentence at all (A-428).
    r0 = next(c for c in document["claims"] if c["rigor"] == "R0")
    assert "detail" not in r0 and "detail_dropped_bytes" not in r0


def test_a_whole_lane_refusal_puts_one_sentence_on_every_declared_level(
    git_repo: GitRepo,
):
    """Producing site 2: ``_refuse_lane_with_plan``, through the pre-run
    dirty-tree guard.

    ONE root cause stopped the whole run, so this function renders the same
    ``(status, reason_code)`` on every declared level -- and now the same
    sentence with it. Per CLAIM, not per verdict (A-428): the field is on
    each claim, and here each claim genuinely was refused for this reason.
    """
    base_rev = _seed_python_project(git_repo)
    path = git_repo.write("assay.toml", _r1_lane_writing("{}", base=base_rev))
    git_repo.commit_all("add assay.toml")
    (git_repo.path / "STRAY-UNTRACKED").write_text("uncommitted\n", encoding="utf-8")

    out, err = io.StringIO(), io.StringIO()
    main(
        ["run", "package", "--file", str(path), "--verdict-json", "-"],
        stdout=out,
        stderr=err,
    )

    document = json.loads(out.getvalue())
    assert document["reason_code"] == "DIRTY_TREE", document
    announced = _sole_announced_message(err.getvalue())
    assert "STRAY-UNTRACKED" in announced
    assert [c["rigor"] for c in document["claims"]] == ["R0", "R1"]
    for claim in document["claims"]:
        assert _detail_of(claim) == announced, claim
        assert _dropped_of(claim) == 0, claim


def test_a_refusal_longer_than_the_bound_is_cut_on_the_wire_and_whole_on_the_stream(
    git_repo: GitRepo,
):
    """The bound is the WIRE's, not the stream's.

    The dirty-tree sentence names every affected path, so enough stray files
    push a real, reachable refusal past 2048 bytes. The operator watching
    stderr still gets the whole list; the archived document gets the head and
    an honest count of what it lost.
    """
    base_rev = _seed_python_project(git_repo)
    path = git_repo.write("assay.toml", _r1_lane_writing("{}", base=base_rev))
    git_repo.commit_all("add assay.toml")
    for i in range(140):
        (git_repo.path / f"STRAY-{i:04d}-with-a-long-enough-name").write_text(
            "x\n", encoding="utf-8"
        )

    out, err = io.StringIO(), io.StringIO()
    main(
        ["run", "package", "--file", str(path), "--verdict-json", "-"],
        stdout=out,
        stderr=err,
    )

    document = json.loads(out.getvalue())
    assert document["reason_code"] == "DIRTY_TREE", document
    announced = _sole_announced_message(err.getvalue())
    assert len(announced.encode("utf-8")) > verdict.CLAIM_DETAIL_BYTES, len(announced)

    claim = document["claims"][0]
    detail = _detail_of(claim)
    assert len(detail.encode("utf-8")) <= verdict.CLAIM_DETAIL_BYTES
    # The HEAD survived: the identifying facts every site puts first.
    assert announced.startswith(detail)
    assert _dropped_of(claim) == len(announced.encode("utf-8")) - len(
        detail.encode("utf-8")
    )
    assert _dropped_of(claim) > 0


def test_the_cleanup_replacement_site_says_which_cleanup_failed(
    git_repo: GitRepo,
):
    """Producing site 3: ``_replace_highest_higher_rigor_claim_with_git_failed``.

    B053's original complaint, one rung in: a claim that was REPLACED by
    ``ERROR``/``GIT_FAILED`` after the lane's own work had already completed.
    Payload-free (A-193/A-194) is not the same as sentence-free.
    """
    git_repo.write(".gitignore", "cov.json\n")
    git_repo.write("pkg/mod.zzz", "BASE\n")
    base_rev = git_repo.commit_all("add pkg base")
    git_repo.write("pkg/mod.zzz", "BASE\nLINE2\n")
    head_rev = git_repo.commit_all("add pkg head")

    judge = make_r1_judge(source_root_paths=(git_repo.path / "pkg",), base=base_rev)
    payload = (
        '{"files": {"pkg/mod.zzz": {"executed_lines": [1, 2], '
        '"missing_lines": [], "excluded_lines": []}}}'
    )
    lane = make_lane(
        rigor=("R0", "R1"),
        judge=judge,
        argv=("/bin/sh", "-c", f"printf '%s' '{payload}' > cov.json"),
    )
    diagnostics = io.StringIO()

    result = runner.run_lane(
        lane,
        commit=head_rev,
        repo=git_repo.path,
        project_root=git_repo.path,
        adapter=ADAPTER,
        assay_version="0.1.0",
        scratch_root_factory=_scratch_root_factory_leaking_on_exit(),
        diagnostics=diagnostics,
    )

    r1 = next(c for c in result.claims if c.rigor == "R1")
    assert r1.reason_code is ReasonCode.GIT_FAILED
    assert _detail_of(r1) == _sole_announced_message(diagnostics.getvalue())
    assert "snapshot cleanup detected leaked state" in _detail_of(r1)
    # The R0 claim beneath it completed for real and is untouched.
    r0 = next(c for c in result.claims if c.rigor == "R0")
    assert r0.status is Outcome.PASS and _detail_of(r0) is None


def test_the_direct_r0_post_command_guard_names_what_the_command_left_behind(
    git_repo: GitRepo,
):
    """Producing site 4: ``_finish_direct_r0_lane``'s post-command dirt/HEAD
    guard, on the direct R0-only path -- a different assembly from every
    higher-rigor site above (no snapshot, no `refuse_lane`)."""
    git_repo.write("pkg/mod.zzz", "BASE\n")
    head_rev = git_repo.commit_all("add pkg base")

    lane = make_lane(
        rigor=("R0",),
        argv=("/bin/sh", "-c", "printf 'left behind\\n' > COMMAND-LEFT-THIS"),
    )
    diagnostics = io.StringIO()

    result = runner.run_lane(
        lane,
        commit=head_rev,
        repo=git_repo.path,
        project_root=git_repo.path,
        adapter=ADAPTER,
        assay_version="0.1.0",
        diagnostics=diagnostics,
    )

    r0 = next(c for c in result.claims if c.rigor == "R0")
    assert r0.reason_code is ReasonCode.DIRTY_TREE
    assert _detail_of(r0) == _sole_announced_message(diagnostics.getvalue())
    assert "COMMAND-LEFT-THIS" in _detail_of(r0)
    assert _dropped_of(r0) == 0


def test_a_run_that_refused_nothing_carries_no_detail_anywhere(
    git_repo: GitRepo,
):
    """The control. A-428 puts `detail` on NON-PASS claims only, and a run
    with no refusal announced no sentence to copy."""
    git_repo.write("pkg/mod.zzz", "BASE\n")
    head_rev = git_repo.commit_all("add pkg base")

    lane = make_lane(rigor=("R0",), argv=("/bin/sh", "-c", "exit 0"))
    diagnostics = io.StringIO()

    result = runner.run_lane(
        lane,
        commit=head_rev,
        repo=git_repo.path,
        project_root=git_repo.path,
        adapter=ADAPTER,
        assay_version="0.1.0",
        diagnostics=diagnostics,
    )

    assert result.outcome is Outcome.PASS
    assert _refusal_lines(diagnostics.getvalue()) == []
    for claim in result.claims:
        assert _detail_of(claim) is None, claim
        assert _dropped_of(claim) is None, claim
    payload = result.to_dict()
    for claim in payload["claims"]:
        assert "detail" not in claim and "detail_dropped_bytes" not in claim
