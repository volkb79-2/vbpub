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
