"""``assay`` command line entry point.

Three subcommands ship so far:

* ``assay lanes`` (P01a) — list and validate the declared lanes. **It must not
  execute one.** A-054 governs its output contract: it renders **no verdict
  artifact**. It does not run a lane, so A-027 ("emitted on every outcome")
  does not apply, and A-028 makes emission conditional on an explicit path
  this subcommand has no flag for.
* ``assay run`` (P04, R1 wiring P17) — execute exactly one declared lane's
  ``argv`` and emit its verdict. It never discovers, selects, orders or
  retries anything (§7): the argv is the lane's, plus whatever the CALLER
  appends after a literal ``--`` (A-036) — never derived by assay itself. An
  append attempted without the lane's ``allow_argv_append`` is refused before
  the process starts (A-095, via :mod:`assay.runner`).

  This build evaluates **R0, R1, R2 and R3 for Python, R2 for SQL, and R1
  for JavaScript/TypeScript and Go** (P19 closes sol finding 1 in full for
  Python; P34/W6 adds SQL at R2 only; B036 adds JavaScript at R1 only; the
  P27 re-carve adds Go at R1 only, A-394):
  ``_built_in_registry`` is the CLI's own closed capability declaration
  (work item 2, widened by every rigor-wiring package since) — Python is
  registered at R1, R2 and R3, SQL at R2 only, JavaScript at R1 and R2
  (B046, the INGESTED path only), Go at R1 only (A-394, the P27 re-carve),
  and nothing else, so a lane declaring
  ``judge.language`` as anything but
  ``"python"``/``"sql"``/``"javascript"``/``"go"``, a SQL lane declaring R1
  or R3, a JavaScript lane declaring R3, a Go lane declaring R2 or R3, or a
  rigor level for a
  language this registry does not know at all, is refused
  (``ERROR``/``BAD_LANE_CONFIG``) before the lane's command ever runs.
  (This sentence said "JavaScript at R1 only" and "a JavaScript or Go lane
  declaring R2 or R3" until the round-1 fix round; B046 had admitted
  ``javascript`` at R2 and this copy was not updated. Corrected here for the
  same reason ``registry.py``'s two paragraphs were: the fact is
  :func:`_built_in_registry`'s, and every restatement of it has now gone
  stale at least once.) A
  declared R3 lane's own canary run happens in
  an independently-owned scratch copy of the consumer's repository
  (:func:`assay.canary.run_isolated_canary`, via
  :func:`assay.runner.run_lane`) — the consumer's real worktree is never
  staged, committed, or written to.

  :func:`assay.runner.assemble_verdict`'s own "a declared rigor level has
  no claim" guard stays where it is as the library-level backstop for a
  caller that is not this CLI; it is simply no longer the thing a real
  ``assay run`` reaches first.
* ``assay verify`` (P14, A-129) — validate a verdict-JSON artifact
  independently of how it was produced. See :mod:`assay.verify` for the full
  contract; this module only wires its parser/dispatch in, exactly like the
  other two subcommands.

All three subcommands let the typed error out of :mod:`assay.config`/
:mod:`assay.runner`/:mod:`assay.git` and map it to an exit code — the exit
code *is* the verdict (§6), and stdout is for humans.
"""

from __future__ import annotations

import argparse
import json
import os
import shlex
import sys
import tempfile
import time
from dataclasses import replace
from pathlib import Path
from collections import Counter
from typing import Any, Sequence, TextIO

from . import __version__
from . import (
    attestation,
    diff,
    git,
    isolation,
    measurability,
    mutation,
    provenance,
    registry,
    runner,
)
from .adapters.base import LanguageAdapter
from .adapters.go import GoAdapter
from .adapters.javascript import JavaScriptAdapter
from .adapters.python import PythonAdapter
from .adapters.sql import SqlAdapter
from .config import Lane, LaneFile, find_lane_file, load_lane_file, parse_duration
from .errors import AssayError, LaneConfigError, Outcome, ReasonCode
from .output import VerdictOutput, reserve_verdict_output, validate_progress_destination
from .verdict import Evidence, EvidenceDeclaration, Verdict
from .vocabulary import MUTATION_OPERATORS, WITHDRAWN_MUTATION_OPERATORS
from .verify import build_verify_parser, cmd_verify

__all__ = ["build_parser", "main"]


#: (B028/DA-R13, A-425) The bound on the ONE Git call assay makes *after* a
#: lane's own budget has already expired: the commit label the
#: ``LANE_TIMEOUT`` refusal verdict is written under
#: (:func:`_run_reserved`).
#:
#: **Why a grace at all, rather than the spent deadline or no deadline.** The
#: spent deadline cannot be reused -- it has, by construction, zero left, so
#: passing it would mean no verdict is ever written for the very case
#: ``--verdict-json`` was reserved for. No deadline at all is what A-420
#: shipped, and DA-R13 ruled it out: an unbounded ``git rev-parse`` after the
#: budget is gone contradicts the budget's single purpose -- assay never
#: hangs -- because a repository on a stalled network mount would hang the
#: refusal itself, the one code path whose whole job is to terminate.
#:
#: **Why two seconds.** This is a documented policy constant, not a
#: measurement (DESIGN-GUIDE §5 forbids inventing the latter, not stating the
#: former), and it is the same kind of decision as DA-D2's 2048-byte
#: ``detail`` bound. On a healthy repository ``git rev-parse HEAD`` completes
#: in milliseconds -- it reads one ref and exits -- so two seconds is three
#: orders of magnitude of headroom for a label read: large enough that it can
#: never be confused with "the lane's budget was too small", small enough
#: that "git is unavailable" is answered promptly rather than waited out.
#: Exceeding it is therefore evidence about Git, not about the lane.
LABEL_GRACE_SECONDS = 2.0


def _add_request_base_argument(subparser: argparse.ArgumentParser) -> None:
    """(B019/A-328) ``--request-base``, on both verbs that resolve one.

    Named for its OWNER, not for the value: ``--base`` would read as an
    override of ``judge.base`` and this is not one -- it is the other side of
    a lane's own ``judge.base_source = "request"`` declaration, and supplying
    it to a lane that did not delegate is a refusal, never a precedence
    contest. ``run`` and ``plan`` both take it because ``plan`` performs the
    identical merge-base resolution before discovering candidates, and a plan
    that silently scoped itself differently from the run it predicts would be
    worse than no plan.
    """
    subparser.add_argument(
        "--request-base",
        default=None,
        metavar="REF",
        help=(
            "the comparison base THIS gate request judges against: a ref or "
            "an already-resolved commit, resolved through the same merge-base "
            "contract judge.base uses and recorded in the verdict as "
            "judgment.resolved.base (B019). Required by a lane declaring "
            "judge.base_source = 'request', which requires changed-line "
            "judging but delegates the base identity to its invoker; refused "
            "on any other lane, because one of the two declarations would "
            "then be inert. Its absence on a delegating lane is a refusal, "
            "never a fallback to HEAD."
        ),
    )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="assay",
        description="assay — judge a change against a project's declared lanes",
    )
    parser.add_argument("--version", action="version", version=f"assay {__version__}")
    subparsers = parser.add_subparsers(dest="command", required=True)

    lanes = subparsers.add_parser(
        "lanes",
        help="list and validate the lanes declared in assay.toml",
        description=(
            "Load assay.toml, validate every declared lane, and print what was "
            "declared. Runs nothing."
        ),
    )
    lanes.add_argument(
        "--file",
        type=Path,
        default=None,
        metavar="PATH",
        help=(
            "path to assay.toml; by default assay searches upward from the "
            "current directory"
        ),
    )
    lanes.add_argument(
        "--json",
        action="store_true",
        help=(
            "write one machine-readable inventory document to stdout instead "
            "of the human-readable listing (B044): every declared lane's "
            "scope/rigor/enforcement, the coverage/mutation/canary shape, "
            "which rigor levels THIS build actually reaches for its "
            "language, and the facts a gate tool needs to preflight an "
            "environment without re-parsing assay.toml itself. Runs nothing, "
            "exactly like the text form; a lane file that fails to load "
            "exits 2 with no JSON on stdout."
        ),
    )

    run = subparsers.add_parser(
        "run",
        help="execute a declared lane's argv and emit its verdict",
        description=(
            "Execute exactly the named lane's declared argv (plus anything "
            "appended after a literal `--`, if the lane permits it) and emit "
            "a verdict. Runs the command once; does not discover, select, "
            "order or retry anything. This build evaluates R0, Python R1, "
            "Python R2, Python R3, JavaScript R1, Go R1, and SQL R2."
        ),
    )
    run.add_argument("lane", help="the lane name to run, as declared in assay.toml")
    run.add_argument(
        "--file",
        type=Path,
        default=None,
        metavar="PATH",
        help=(
            "path to assay.toml; by default assay searches upward from the "
            "current directory"
        ),
    )
    run.add_argument("--resume", action="store_true")
    run.add_argument("--operators", default=None)
    run.add_argument("--shard", default=None, metavar="INDEX/COUNT")
    _add_request_base_argument(run)

    plan = subparsers.add_parser(
        "plan",
        help="report a mutation lane's candidate plan without executing it",
        description=(
            "Discover the named mutation lane's candidates, print total and "
            "grouped counts with deterministic identities, and estimate serial "
            "and wall-clock runtime. Runs no lane command and creates no "
            "mutant snapshots."
        ),
    )
    plan.add_argument("lane", help="the mutation lane name to inspect")
    plan.add_argument("--operators", default=None)
    plan.add_argument("--shard", default=None, metavar="INDEX/COUNT")
    _add_request_base_argument(plan)
    plan.add_argument(
        "--file",
        type=Path,
        default=None,
        metavar="PATH",
        help=(
            "path to assay.toml; by default assay searches upward from the "
            "current directory"
        ),
    )
    run.add_argument(
        "--verdict-json",
        type=str,
        default=None,
        metavar="PATH",
        help=(
            "write the verdict atomically to PATH, or '-' for stdout "
            "(A-028); omit to skip artifact emission entirely"
        ),
    )
    run.add_argument(
        "--progress",
        type=str,
        default=None,
        metavar="PATH",
        help=(
            "append the R2 mutation progress NDJSON stream to PATH, one "
            "compact JSON object per line, flushed per event (B031/A-320). "
            "Opt-in and consumer-directed, exactly like --verdict-json: "
            "assay never chooses this location itself, and omitting the flag "
            "writes no progress file at all. Point it OUTSIDE the repository "
            "(or at a gitignored path) -- a progress file inside the work "
            "tree makes the next run of the same lane refuse "
            "NO_MEASUREMENT/DIRTY_TREE. Ignored by a lane that declares no R2."
        ),
    )
    run.add_argument(
        "--require-judge-provenance",
        action="store_true",
        help=(
            "refuse, before any work, unless this assay can identify the "
            "build artifact it was installed from and record its sha256 in "
            "the verdict as judge_provenance (B018). Without this flag an "
            "unidentifiable invocation -- a source checkout, an editable "
            "install -- still runs, emits no judge_provenance at all, and "
            "says so on stderr; assay never invents a digest either way. A "
            "gate that binds its evidence to a verified judge binary passes "
            "this flag."
        ),
    )

    build_verify_parser(subparsers)

    return parser


def main(
    argv: Sequence[str] | None = None,
    *,
    stdin: TextIO | None = None,
    stdout: TextIO | None = None,
    stderr: TextIO | None = None,
) -> int:
    """Run the CLI and return the process exit code."""
    inp = sys.stdin if stdin is None else stdin
    out = sys.stdout if stdout is None else stdout
    err = sys.stderr if stderr is None else stderr
    raw = list(sys.argv[1:] if argv is None else argv)
    cli_argv, appended = _split_appended_argv(raw)
    args = build_parser().parse_args(cli_argv)
    try:
        if args.command == "lanes":
            lane_file = _resolve_lane_file(args.file)
            if args.json:
                _render_lanes_json(lane_file, out)
            else:
                _render_lanes(lane_file, out)
        elif args.command == "run":
            return _cmd_run(args, appended, out, err)
        elif args.command == "plan":
            return _cmd_plan(args, out)
        elif args.command == "verify":
            return cmd_verify(args.path, stdin=inp, stderr=err)
        else:  # pragma: no cover - argparse rejects unknown subcommands first
            raise AssertionError(f"unhandled command {args.command!r}")
    except AssayError as exc:
        # (B053/A-409) The same one emitter every internal conversion site
        # now calls -- this print is where its format came from, and keeping
        # a second spelling of it here is exactly how the two would drift.
        runner.announce_refusal(exc, diagnostics=err)
        return exc.exit_code
    return Outcome.PASS.exit_code


def _split_appended_argv(raw: list[str]) -> tuple[list[str], list[str]]:
    """Split *raw* on a literal ``--`` into (CLI tokens, appended argv).

    Mirrors the convention of ``docker run``/``kubectl exec``/``npm run --``:
    everything after the FIRST ``--`` is the caller's payload, verbatim, and
    never reinterpreted by argparse. Without this split, argparse would try
    to parse the appended tokens as assay's own flags.
    """
    if "--" in raw:
        index = raw.index("--")
        return raw[:index], raw[index + 1 :]
    return raw, []


def _resolve_lane_file(path: Path | None) -> LaneFile:
    return load_lane_file(find_lane_file() if path is None else path)


def _built_in_registry() -> registry.Registry:
    """This CLI's own closed capability declaration (P17 work item 2,
    widened P18): a fresh :class:`~assay.registry.Registry`, built on
    every call rather than once at import time -- an adapter carries no
    state a test could leak between calls (AUTHORING.md §3b.B), so there
    is nothing a shared, module-level instance would buy beyond a mutable
    global to guard.

    Python is registered at R1, R2 AND R3 and nothing else: adding ``"R3"``
    to this ONE existing entry's ``rigor`` set is the whole registry change
    a Python R3 CLI pipeline needs (P18's own carried-in note, one level
    further). Naming a capability this build does not actually reach is
    exactly the failure the whole v1.1 repair series exists to remove one
    level up (the post-series review's own finding 1) -- this is that
    discipline applied to the registry itself.

    **The sentence above used to continue "-- Go has no producer path wired
    in at any rigor level yet (P22)". That is no longer true, and A-394 is
    why: ``go`` IS registered, at** ``{"R1"}`` **only.** The paragraph is
    rewritten rather than deleted because the SEQUENCING is the load-bearing
    half of that ruling, and a later reader who sees only the finished entry
    would not recover it. Registering Go was never gated on "someone got
    around to it"; it was gated on a chain that had to land FIRST, because
    :mod:`assay.coverage_parsers.go_cover` used to expand a cover block's
    whole extent into lines and call the result statement truth -- the
    conflation A-217's impossibility proof (two gofmt-clean files, one
    byte-identical profile, different statement lines) rules out
    unconditionally. A ``go`` entry added at ANY earlier point in Wave C
    would have made that reachable through this very function, which is the
    most expensive shape the A-334/A-335 honesty failure takes: a wrong
    verdict a consumer can reach by declaring a supported language. The
    chain, in the order it had to land:
    :attr:`~assay.adapters.base.LanguageAdapter.requires_statement_attribution`,
    the :meth:`~assay.adapters.base.LanguageAdapter.statement_blocks` hook
    (A-397), the :func:`assay.evaluate` refusal that makes the flag bite
    (A-392), and ``external_tools = ("go",)`` (B047 item 2). Only then this
    entry.

    **What this entry does NOT promise, and why that is not a gap.** A Go
    lane needs a real Go toolchain: the statement-position oracle is a Go
    program (A-217 -- a Python re-implementation of ``cmd/cover``'s
    segmentation is explicitly not an acceptable substitute), so an
    environment without ``go`` on PATH gets
    ``NO_MEASUREMENT``/``MISSING_EXTERNAL_TOOL`` from A-253's preflight in
    :func:`assay.runner.run_lane`, BEFORE the lane's command runs. That is
    the property that makes this entry safe everywhere rather than only
    where a toolchain happens to exist: a Go lane is either audited against
    real statement positions or cleanly refused, and there is no third state
    in which it is silently wrong. This devcontainer and the registered
    gate's own image (``tester-unified``) both have no Go and both take the
    refusal -- see ``tests/qualification/`` for where the real-toolchain
    proof lives instead (DESIGN-GUIDE §10's pattern).

    **R2 and R3 stay unregistered for Go**, which the Wave C prompt's own
    NOT-IN-SCOPE list forbids changing:
    :meth:`~assay.adapters.go.GoAdapter.generate_mutation_sites` is
    unconditionally ``"UNSUPPORTED"``, so an R2 entry would advertise a
    producer path that does not exist -- the failure this docstring's first
    paragraph is about. Both refusals are asserted as controls in
    ``tests/test_cli_run.py``
    (``test_run_refuses_go_at_r2_the_language_is_registered_r1_only`` and its
    R3 sibling), alongside the R1 test that now inverts.

    **What those two controls do NOT cover, corrected in the round-1 fix
    round.** This paragraph used to continue that a rigor level "for a
    language this registry does not know at all" was asserted as a control
    there too. It was, by the same two Go tests -- until A-394 registered
    ``go`` and they silently became registered-at-another-rigor tests, so no
    CLI-level test exercised the unknown-language branch at all. The
    adversarial round-1 review found it; one of the two is now
    ``test_run_refuses_a_language_this_registry_does_not_know_at_all``, which
    declares ``rust`` and asserts ``rust`` really is absent from the registry
    so it cannot drift the same way. The unit-level control is
    ``test_registry.py::test_an_unregistered_language_is_refused_not_defaulted``.

    **P34/W6: SQL is registered at R2 ONLY** (A-242's own sentence,
    ``SqlAdapter``'s own module docstring). That single fact is what makes
    ``SqlAdapter.has_executable_code``/``normalize_coverage_key``/
    ``statement_spans``/``inject_import_break``/``inject_uncovered_line``
    provably unreachable through this CLI: none of R0's own path, R1, or
    R3 ever resolves an adapter for a language whose one registry entry
    names only R2, so nothing here ever calls an R0/R1/R3-only method on
    it. Route (i) (§4.1) needs no ``external_tools`` entry, so this is the
    entire wiring change -- no preflight, no new config surface, one more
    entry in this one registry.

    **B036: JavaScript/TypeScript is registered at R1 ONLY**, following
    Python's own first-ship shape rather than SQL's. R2 is not registered
    because no JS/TS mutation engine exists to reach -- whether it should be
    native or should ingest an external producer's evidence is the ruling
    **B037** exists to force (:meth:`~assay.adapters.javascript.
    JavaScriptAdapter.generate_mutation_sites` is unconditionally
    ``"UNSUPPORTED"`` until then). R3 is not registered either: the two
    canary injection methods are real implementations rather than stubs, but
    a producer path is a separate claim from a method existing
    (DESIGN-GUIDE §7), and wiring one is a fast-follow, not part of B036.

    **B046 (schema v9) RESOLVED B037, and ``javascript`` is now registered at
    ``{"R1", "R2"}`` -- through the INGESTED path only.** The paragraph above
    stands as history; what changed is which of its two options was taken.
    Neither: assay ships no JS/TS mutation engine and still does not.
    :meth:`~assay.adapters.javascript.JavaScriptAdapter.generate_mutation_sites`
    is STILL unconditionally ``"UNSUPPORTED"``, and that is not an oversight
    left standing -- it is what makes this the ingested path. The lane's own
    argv runs StrykerJS inside the private snapshot, exactly as it already
    runs Vitest for R1, and assay judges the
    ``mutation-testing-report-schema`` document it wrote.

    **The runner selects native vs ingested by ``judge.mutation.format``'s
    presence, and by nothing else** -- not by the language and not by the
    artifact's content (A-007). So this registry entry says only "a
    ``javascript`` lane may declare R2 at all"; WHICH R2 it gets is the lane
    file's own declaration.

    **Which layer refuses a NATIVE ``javascript`` R2 lane** -- still refused,
    and still by the FIRST of two independent guards:

    1. :mod:`assay.config` at load time. A native R2 lane must declare a
       non-empty ``judge.mutation.operators``, while
       :data:`assay.vocabulary.MUTATION_OPERATORS_BY_LANGUAGE` has no
       ``javascript`` entry at all -- so every operator such a lane could
       spell is FOREIGN to it, and the foreign-operator guard refuses
       ``BAD_LANE_CONFIG`` naming the language. A config-valid NATIVE
       ``javascript`` R2 lane is therefore still not constructible. An
       INGESTED one declares no operators at all (they are forbidden there,
       A-360), so it never meets this guard -- which is precisely why
       registering ``{"R1", "R2"}`` here does not reopen the native path.
    2. :func:`assay.registry.get_adapter` and this entry's own ``rigor``
       frozenset, which since B046 admits R2 and so no longer refuses on this
       axis. The guarantee that a native JS R2 lane cannot run now rests on
       (1) plus ``generate_mutation_sites`` returning ``"UNSUPPORTED"``,
       which :func:`assay.mutation.run_mutation` renders as
       ``INCONCLUSIVE``/``MUTATION_UNSUPPORTED`` -- a stated absence of
       capability, never a PASS.

    R3 is still NOT registered for ``javascript``: the two canary injection
    methods are real implementations, but a producer path is a separate claim
    from a method existing (DESIGN-GUIDE §7), and B041(c)'s qualification
    harness has proven R1 only -- a real canary PAIR has never run.
    """
    return registry.new_registry(
        registry.RegistryEntry(
            adapter=PythonAdapter(), rigor=frozenset({"R1", "R2", "R3"})
        ),
        registry.RegistryEntry(adapter=SqlAdapter(), rigor=frozenset({"R2"})),
        # (B046) R2 admitted for the INGESTED path only -- see this
        # function's docstring for why that is a property of the lane's own
        # `judge.mutation.format` declaration rather than of this frozenset.
        registry.RegistryEntry(
            adapter=JavaScriptAdapter(), rigor=frozenset({"R1", "R2"})
        ),
        # (A-394, Wave C) R1 ONLY, and deliberately the LAST thing this wave
        # landed -- see this function's docstring for why the ordering is
        # load-bearing rather than tidy.
        registry.RegistryEntry(adapter=GoAdapter(), rigor=frozenset({"R1"})),
    )


#: The rigor levels THIS module resolves an adapter for, in the order tried
#: (P18, widened P19): the FIRST one a lane declares wins the lookup below,
#: but since `_built_in_registry`'s single entry per language returns the
#: identical adapter OBJECT for any of the three, which one wins is never
#: observable -- this exists only to give the tail lookup a level string to
#: pass.
_ADAPTER_BEARING_LEVELS: tuple[str, ...] = ("R1", "R2", "R3")


def _resolve_declared_adapters(lane: Lane) -> LanguageAdapter | None:
    """Check EVERY declared rigor level above R0 against this build's own
    registry, and return the adapter :func:`assay.runner.run_lane` needs
    for whichever of R1/R2 the lane declares (``None`` when neither is
    declared).

    Work item 2's "reject declared rigor above that entry's capability"
    (A-139). Checking only the literal levels this build reaches -- as
    this function's first version did for ``"R1"`` alone -- left the
    registry gate DEAD for the levels it exists to guard: a lane declaring
    ``rigor = ["R0", "R3"]`` never consulted the registry at all, so its
    command ran to completion and only THEN did
    :func:`assay.runner.assemble_verdict` refuse it for a missing R3
    claim, with the side effects already committed and no artifact
    emitted. The loop is over ``lane.rigor`` itself so a level this build
    cannot reach is refused BEFORE anything executes, whichever level it
    is.

    ``R0`` is skipped, not looked up: it needs no adapter, and
    :class:`~assay.registry.RegistryEntry` refuses to name it for exactly
    that reason.

    The adapter itself is fetched by a SECOND, explicit lookup rather than
    captured inside the loop -- capturing it there would need branching on
    which level resolved successfully, which the loop's own job (refuse or
    continue) has no other reason to do. R1 is tried before R2 before R3 in
    :data:`_ADAPTER_BEARING_LEVELS` merely for a deterministic, stable
    choice when a lane declares more than one; :func:`~assay.registry.
    get_adapter` returns the SAME adapter object regardless (one entry per
    language, not per rigor level), so this ordering is never itself
    observable.
    """
    built_in = _built_in_registry()
    for level in lane.rigor:
        if level != "R0":
            registry.get_adapter(built_in, lane.judge.language, level)
    for level in _ADAPTER_BEARING_LEVELS:
        if level in lane.rigor:
            return registry.get_adapter(built_in, lane.judge.language, level)
    return None


def _cmd_run(
    args: argparse.Namespace,
    appended: list[str],
    out: TextIO,
    err: TextIO,
    *,
    label_grace_seconds: float = LABEL_GRACE_SECONDS,
) -> int:
    lane_file = _resolve_lane_file(args.file)
    lane: Lane = lane_file.lane(args.lane)
    if getattr(args, "operators", None):
        requested = tuple(part.strip() for part in args.operators.split(",") if part.strip())
        # B034/A-326: the same refusal `config._load_mutation` gives a
        # DECLARED withdrawn operator. `--operators` is an override of that
        # declaration, so it has to close the same door -- otherwise the
        # withdrawal is enforced only for lanes that spell it in TOML.
        # (A-331) And it runs BEFORE the unknown check for the same reason
        # the loader's does: at the v8 cut these names left the catalogue,
        # so "unknown" would now swallow them and answer a stale-but-once-
        # legal spelling with the least useful of the two messages.
        withdrawn = tuple(
            name for name in requested if name in WITHDRAWN_MUTATION_OPERATORS
        )
        if withdrawn:
            raise LaneConfigError(
                f"withdrawn mutation operators: {', '.join(withdrawn)}; every "
                f"site they produced was already produced by "
                f"python:compare-swap at the same span with the same "
                f"replacement"
            )
        unknown = tuple(name for name in requested if name not in MUTATION_OPERATORS)
        if unknown or not requested:
            raise LaneConfigError(f"unknown mutation operators: {', '.join(unknown)}")
        mutation_config = replace(
            lane.judge.mutation, operators=requested
        )
        judge_config = replace(lane.judge, mutation=mutation_config)
        lane = replace(lane, judge=judge_config)
    # P21 work item 8 / A-181. The order is the contract:
    #
    #   lane config -> OUTPUT RESERVATION -> HEAD -> adapter -> command
    #
    # Lane-config failure stays earliest (a lane that will not load has no
    # destination to reserve). Everything AFTER the reservation is consumer
    # work, and a requested artifact that physically cannot exist must not be
    # discovered only once the lane's command has already run -- which is
    # what `--verdict-json <unwritable>` did before this package: a bare
    # `OSError` and exit 1, i.e. a tooling failure a consumer reads as FAIL,
    # with the side effects already committed (A-O14).
    #
    # `None` is A-028's deliberate no-artifact mode and reserves nothing:
    # the exit code alone still gates correctly, so a caller that never asked
    # for a file is never refused on account of one.
    #
    # B031/A-320 round 2 (blocker 2): `--progress` shares this same OUTPUT
    # RESERVATION step for the two mistakes visible without opening
    # anything (a directory, an empty/unparseable path) -- an unwritable
    # `--progress <destination>` used to run the whole lane and only THEN
    # surface as an unrelated `ERROR`/`GIT_FAILED`, deep inside R2
    # execution. `validate_progress_destination` does not reserve a
    # descriptor the way `reserve_verdict_output` does: the progress file is
    # opened once, later, only if the lane reaches R2, and its own writer
    # creates missing parent directories on demand -- see its docstring.
    destination: VerdictOutput | None = None
    if args.verdict_json is not None:
        destination = reserve_verdict_output(args.verdict_json, stdout=out)
    try:
        if (progress_arg := getattr(args, "progress", None)) is not None:
            validate_progress_destination(progress_arg)
        return _run_reserved(
            args,
            lane,
            lane_file,
            appended,
            destination,
            out,
            err,
            label_grace_seconds=label_grace_seconds,
        )
    finally:
        if destination is not None:
            destination.close()


def _declared_evidence(lane: Lane) -> tuple[EvidenceDeclaration, ...]:
    """The lane's own ordered Tier-3 identities, converted to
    :class:`~assay.verdict.EvidenceDeclaration` (P26/A-213). Empty when the
    lane declares none at all -- no location is ever derived.
    """
    if lane.judge is None or lane.judge.evidence is None:
        return ()
    return tuple(
        EvidenceDeclaration(source=item.source, key=item.key) for item in lane.judge.evidence
    )


def _timed_out_evidence(
    declared_evidence: tuple[EvidenceDeclaration, ...], exc: AssayError
) -> tuple[Evidence, ...]:
    """Every declared evidence identity as a payload-free
    ``BUDGET_EXCEEDED``/``LANE_TIMEOUT`` entry (A-213's atomic attestation
    timeout artifact)."""
    return tuple(
        Evidence(
            source=item.source,
            key=item.key,
            status=exc.outcome,
            verified_by_assay=False,
            reason_code=exc.reason_code,
        )
        for item in declared_evidence
    )


def _run_reserved(
    args: argparse.Namespace,
    lane: Lane,
    lane_file: LaneFile,
    appended: list[str],
    destination: "VerdictOutput | None",
    out: TextIO,
    err: TextIO,
    *,
    label_grace_seconds: float = LABEL_GRACE_SECONDS,
) -> int:
    # P26/A-212: one LaneDeadline, started here -- before HEAD is even
    # resolved -- reaches HEAD, attestation, adapter resolution, and the
    # whole of run_lane (direct R0 or higher rigor). CLI never passes
    # `deadline=None` to run_lane; the exact sequence below is the contract:
    # lane/output already reserved -> deadline -> HEAD -> attestation ->
    # adapter -> command -> emit once.
    # B018/A-327: resolved ONCE, here, before the lane deadline even starts.
    # Identity is a fact of this process, not of the run, and a consumer that
    # demanded the binding must learn it is unavailable before assay spends a
    # budget producing evidence that consumer would refuse anyway.
    judge_provenance, unidentified = provenance.identify_judge()
    if unidentified is not None:
        if getattr(args, "require_judge_provenance", False):
            raise LaneConfigError(
                f"--require-judge-provenance: this assay cannot identify the "
                f"build artifact it is running from, so no verdict it emits "
                f"could be bound to a verified judge -- {unidentified}"
            )
        # Loud, never silent (B018's own acceptance criterion): the absence is
        # announced on the diagnostics stream every time, because a consumer
        # reading only the artifact would otherwise find a field that is
        # simply not there, with nothing saying why.
        print(
            f"assay: no judge_provenance recorded -- {unidentified}; pass "
            f"--require-judge-provenance to refuse instead of proceeding",
            file=err,
        )
    deadline = runner.LaneDeadline.start(
        budget_seconds=lane.budget_seconds, monotonic=time.monotonic
    )
    # B013: `derived:` facts read rendered CIU state at the project root
    # (A-293); ciu itself gitignores `ciu.global.toml` and only ever renders
    # it there (ciu/README.md, ciu/src/ciu/scaffold.py). A lane without any
    # `derived:` declaration never opens this path -- resolve_command_plan's
    # own `has_derived` check is what refuses a *used* but unreadable source.
    infrastructure_source = (
        lane_file.project_root / "ciu.global.toml" if lane.infrastructure else None
    )
    infrastructure_environment = os.environ if lane.infrastructure else None
    # Pure, and independent of every Git fact -- resolved BEFORE the first
    # deadline-bounded call so the timeout refusal below can render the
    # lane's declared evidence identities exactly as A-213's does.
    declared_evidence = _declared_evidence(lane)
    try:
        commit = git.head_rev(lane_file.project_root, remaining=deadline.remaining)
    except AssayError as exc:
        if exc.reason_code is not ReasonCode.LANE_TIMEOUT:
            raise
        # (B028/DA-R9, SF-1) The EARLIEST place the lane-wide deadline can
        # expire, and the place R-1's `budget = "0.001s"` probe actually
        # escaped from -- measured, with the stack captured in the REPORT:
        # `cli.py` -> `git.head_rev` -> `git._run_bounded` ->
        # `LaneDeadline.remaining`. That is UPSTREAM of `run_lane` entirely,
        # so the handler around `run_lane` below cannot see it and the
        # reserved `--verdict-json` was never written.
        #
        # **The one fact that is not yet known here is the commit label** --
        # DA-R9's own contingency ("if `refuse_lane` needs a fact that is
        # unavailable before `git.repo_top`, the verdict carries what is
        # known and the REPORT records exactly which field"). It is READ,
        # never fabricated: a commit label is an IDENTITY, not a
        # measurement, `budget` bounds the lane's work rather than the
        # artifact's production (the `write_verdict`/summary tail below
        # already runs past the deadline on every timed-out lane), and an
        # invented label would be the one thing this project must never
        # emit.
        #
        # (A-425/DA-R13) The read is BOUNDED, by its own short grace rather
        # than by the lane's spent deadline -- which has zero left by
        # construction, so reusing it would mean no timed-out lane ever gets
        # the verdict `--verdict-json` reserved. A-420 shipped this call
        # unbounded and DA-R13 ruled that out: assay never hangs, and a
        # stalled mount would otherwise hang the refusal path itself. The
        # grace is expressed through the SAME `remaining=` shape every other
        # Git call uses -- `LaneDeadline` constructed directly because its
        # `start` classmethod rejects a non-positive budget, and the
        # grace-expired test sets `label_grace_seconds = 0.0` through the
        # parameter rather than stubbing anything.
        #
        # If the grace ALSO expires, no verdict is written and the one line
        # the emitter prints says the LABEL could not be read within it --
        # the operator's next move is to look at Git, not at `budget`. Any
        # OTHER Git fault re-raises the ORIGINAL timeout unchanged: a Git
        # fault must not be renamed, and a lane that cannot be labelled at
        # all is exactly the case `main()`'s handler already owns.
        grace = runner.LaneDeadline(
            expires_at=time.monotonic() + label_grace_seconds,
            monotonic=time.monotonic,
        )
        try:
            commit = git.head_rev(lane_file.project_root, remaining=grace.remaining)
        except AssayError as label_exc:
            if label_exc.reason_code is ReasonCode.LANE_TIMEOUT:
                raise AssayError(
                    f"the lane-wide deadline expired, and the commit label "
                    f"the refusal verdict must carry could not be read from "
                    f"{lane_file.project_root} within the "
                    f"{label_grace_seconds}s grace allowed for it "
                    f"(assay.cli.LABEL_GRACE_SECONDS); no verdict was "
                    f"written -- git, not the lane's budget, is what did not "
                    f"answer",
                    outcome=exc.outcome,
                    reason_code=exc.reason_code,
                ) from None
            raise exc from None
        # (B053/A-428, A-439) Announced BEFORE the artifact is built, not
        # after, because the artifact now carries the announced sentence:
        # `announce_refusal` returns the bounded copy and `refuse_lane` puts
        # it on every declared level's claim. The observable order is
        # unchanged -- `refuse_lane` writes nothing to any stream.
        detail = runner.announce_refusal(exc, diagnostics=err)
        verdict = runner.refuse_lane(
            lane,
            commit=commit,
            status=exc.outcome,
            reason_code=exc.reason_code,
            detail=detail,
            argv_append=appended,
            infrastructure_source=infrastructure_source,
            infrastructure_environment=infrastructure_environment,
            assay_version=__version__,
            judge_provenance=judge_provenance,
            evidence=_timed_out_evidence(declared_evidence, exc),
            declared_evidence=declared_evidence,
        )
        if destination is not None:
            runner.write_verdict(verdict, destination)
        if args.verdict_json != "-":
            _print_run_summary(verdict, out)
        return verdict.exit_code

    # No declaration means no loader call. Otherwise attestation_dir exists
    # by config invariant; the loader reads live project-contained input and
    # compares exact committed Git objects before adapter/command work.
    if declared_evidence:
        try:
            evidence = attestation.load_attested_evidence(
                lane_file.project_root,
                head=commit,
                declared=declared_evidence,
                project_root=lane_file.project_root,
                attestation_dir=lane.judge.attestation_dir,
                remaining=deadline.remaining,
            )
        except AssayError as exc:
            if exc.reason_code is not ReasonCode.LANE_TIMEOUT:
                raise
            # A-213: the attestation deadline is atomic. No adapter or
            # command ever launches; every declared rigor claim AND every
            # declared evidence identity becomes the SAME payload-free
            # BUDGET_EXCEEDED/LANE_TIMEOUT pair.
            # (B053/A-439) Same order, same reason, as the sibling above.
            detail = runner.announce_refusal(exc, diagnostics=err)
            verdict = runner.refuse_lane(
                lane,
                commit=commit,
                status=exc.outcome,
                reason_code=exc.reason_code,
                detail=detail,
                argv_append=appended,
                infrastructure_source=infrastructure_source,
                infrastructure_environment=infrastructure_environment,
                assay_version=__version__,
                judge_provenance=judge_provenance,
                evidence=_timed_out_evidence(declared_evidence, exc),
                declared_evidence=declared_evidence,
            )
            if destination is not None:
                runner.write_verdict(verdict, destination)
            if args.verdict_json != "-":
                _print_run_summary(verdict, out)
            return verdict.exit_code
    else:
        evidence = ()

    try:
        adapter = _resolve_declared_adapters(lane)
    except AssayError as exc:
        # A-139: HEAD is already resolved above, so this is one of work
        # item 3's "later terminal paths" and MUST emit a complete
        # artifact. Letting the typed error reach main()'s handler would
        # give a consumer the right exit code and nothing to read -- the
        # exact shape of un-auditable refusal P17 exists to remove.
        #
        # P26/A-213: adapter refusal preserves already-resolved evidence --
        # it is never permission to erase it.
        detail = runner.announce_refusal(exc, diagnostics=err)
        verdict = runner.refuse_lane(
            lane,
            commit=commit,
            status=exc.outcome,
            reason_code=exc.reason_code,
            detail=detail,
            argv_append=appended,
            infrastructure_source=infrastructure_source,
            infrastructure_environment=infrastructure_environment,
            assay_version=__version__,
            judge_provenance=judge_provenance,
            evidence=evidence,
            declared_evidence=declared_evidence,
        )
    else:
        # (B028/DA-R9, SF-1) The SECOND half of DA-D10's intent: "the reserved
        # `--verdict-json` is WRITTEN" binds wherever the lane-wide deadline
        # expires, not only where `run_lane`'s own two catches can see it.
        #
        # R-1's round-1 measurement: with `budget = "0.001s"` the deadline is
        # already spent when `run_lane` calls `git.repo_top`, which is UPSTREAM
        # of both the direct-R0 `try` and `_run_higher_rigor_lane`'s outer
        # catch. The `AssayError` reached `main()`'s handler, which prints and
        # returns the exit code having written NOTHING -- on both dispatch
        # paths, and identically on the pre-B028 build, so B028's `CHANGES.md`
        # headline was broader than what shipped.
        #
        # One handler here covers both paths, because both go through this one
        # call. Deliberately the same shape as the attestation-timeout handler
        # above (A-213): scoped to `LANE_TIMEOUT` alone -- anything else still
        # propagates, because a bug must not be laundered into a verdict --
        # and refusing through `refuse_lane`, which renders the identical
        # payload-free pair on every declared level.
        #
        # No fact is missing: `commit`, `judge_provenance`, `evidence` and
        # `declared_evidence` are all resolved ABOVE this point by the
        # P26/A-212 sequence, so this verdict carries exactly what the
        # attestation-timeout verdict carries. The one thing it cannot carry
        # is a `CommandResult` -- the command never ran, which is what
        # `NO_MEASUREMENT`/`LANE_TIMEOUT` says.
        try:
            verdict = runner.run_lane(
                lane,
                commit=commit,
                repo=lane_file.project_root,
                project_root=lane_file.project_root,
                adapter=adapter,
                assay_version=__version__,
                judge_provenance=judge_provenance,
                argv_append=appended,
                evidence=evidence,
                declared_evidence=declared_evidence,
                deadline=deadline,
                resume=getattr(args, "resume", False),
                shard=getattr(args, "shard", None),
                infrastructure_source=infrastructure_source,
                infrastructure_environment=infrastructure_environment,
                # B031/A-320: opt-in, consumer-named, absent by default.
                # Resolved against the invoking CWD (like every other CLI path
                # argument), never against the project root, and never derived
                # from the lane name.
                progress_artifact=(
                    Path(progress_arg).expanduser()
                    if (progress_arg := getattr(args, "progress", None)) is not None
                    else None
                ),
                # B019/A-328: the gate request's own comparison base, threaded
                # verbatim. `run_lane` decides whether this lane delegated to
                # it, and refuses every disagreement -- the CLI does not
                # adjudicate.
                request_base=getattr(args, "request_base", None),
                # B032/A-322: where the `environment_command` probe's refusal
                # message goes. `run_lane` returns a Verdict and carries no
                # free-text field for a cause (A-138/A-170), so B010's "refuse
                # with a clear message" needs a stream, not a reason code.
                diagnostics=err,
            )
        except AssayError as exc:
            if exc.reason_code is not ReasonCode.LANE_TIMEOUT:
                raise
            detail = runner.announce_refusal(exc, diagnostics=err)
            verdict = runner.refuse_lane(
                lane,
                commit=commit,
                status=exc.outcome,
                reason_code=exc.reason_code,
                detail=detail,
                argv_append=appended,
                infrastructure_source=infrastructure_source,
                infrastructure_environment=infrastructure_environment,
                assay_version=__version__,
                judge_provenance=judge_provenance,
                evidence=evidence,
                declared_evidence=declared_evidence,
            )
    if destination is not None:
        # Exactly once, and the summary is printed only after it succeeded:
        # a run that could not deliver the artifact it was asked for must not
        # also print a line that reads like a completed run (A-181).
        runner.write_verdict(verdict, destination)
    if args.verdict_json != "-":
        _print_run_summary(verdict, out)
    return verdict.exit_code


def _print_run_summary(verdict: Verdict, out: TextIO) -> None:
    label = verdict.outcome.value
    if verdict.reason_code is not None:
        label = f"{label}/{verdict.reason_code.value}"
    print(f"{verdict.lane}: {label} (exit {verdict.exit_code})", file=out)
    print(f"  commit: {verdict.commit}", file=out)
    print(f"  argv: {shlex.join(verdict.argv_effective or ())}", file=out)
    if verdict.argv_modified:
        print(f"    (appended: {shlex.join(verdict.argv_appended or ())})", file=out)


def _plan_candidate_id(job: mutation.MutantJob) -> str:
    return mutation.candidate_id(job)


def _cmd_plan(args: argparse.Namespace, out: TextIO) -> int:
    """Report a mutation lane's plan without executing it.

    ``--operators`` and ``--shard`` are planning-only selections. They do not
    change the lane declaration, so the same file can be planned and run with
    the matching run flag without inventing a second config surface.
    """
    lane_file = _resolve_lane_file(args.file)
    lane = lane_file.lane(args.lane)
    if lane.judge is None or lane.judge.mutation is None or "R2" not in lane.rigor:
        raise LaneConfigError(f"lane {lane.name!r} does not declare an R2 mutation judge")
    adapter = _resolve_declared_adapters(lane)
    if adapter is None:
        raise LaneConfigError(f"lane {lane.name!r} resolves no mutation adapter")

    # B019/A-328: decided once, before any snapshot -- exactly where
    # `run_lane` decides it, and by the same function, so `plan` refuses a
    # delegating lane with no --request-base (and a non-delegating lane with
    # one) on identical terms rather than discovering the mismatch mid-walk.
    base_declaration = runner.resolve_base_declaration(
        lane, getattr(args, "request_base", None)
    )

    operators = lane.judge.mutation.operators
    shard_index: int | None = None
    shard_count: int | None = None
    if args.operators:
        requested = tuple(part.strip() for part in args.operators.split(",") if part.strip())
        # B034/A-326: the same refusal `config._load_mutation` gives a
        # DECLARED withdrawn operator. `--operators` is an override of that
        # declaration, so it has to close the same door -- otherwise the
        # withdrawal is enforced only for lanes that spell it in TOML.
        # (A-331) And it runs BEFORE the unknown check for the same reason
        # the loader's does: at the v8 cut these names left the catalogue,
        # so "unknown" would now swallow them and answer a stale-but-once-
        # legal spelling with the least useful of the two messages.
        withdrawn = tuple(
            name for name in requested if name in WITHDRAWN_MUTATION_OPERATORS
        )
        if withdrawn:
            raise LaneConfigError(
                f"withdrawn mutation operators: {', '.join(withdrawn)}; every "
                f"site they produced was already produced by "
                f"python:compare-swap at the same span with the same "
                f"replacement"
            )
        unknown = tuple(name for name in requested if name not in MUTATION_OPERATORS)
        if unknown or not requested:
            raise LaneConfigError(f"unknown mutation operators: {', '.join(unknown)}")
        operators = requested
    if args.shard:
        try:
            raw_index, raw_count = args.shard.split("/", 1)
            shard_index = int(raw_index)
            shard_count = int(raw_count)
        except ValueError as exc:
            raise LaneConfigError("--shard must have the form INDEX/COUNT") from exc
        try:
            # Zero-based, matching config.py/the verdict schema/CONSUMERS.md
            # -- never `- 1`. This is a dry bounds check only (an empty
            # candidate tuple); its return value is discarded.
            mutation.select_mutation_shard((), index=shard_index, count=shard_count)
        except ValueError as exc:
            raise LaneConfigError(f"--shard {args.shard!r}: {exc}") from exc

    deadline = runner.LaneDeadline.start(
        budget_seconds=lane.budget_seconds, monotonic=time.monotonic
    )
    commit = git.head_rev(lane_file.project_root, remaining=deadline.remaining)
    repo_top = git.repo_top(lane_file.project_root, remaining=deadline.remaining)
    project_prefix = runner._resolved_project_prefix(repo_top, lane_file.project_root)
    snapshot_policy = runner._snapshot_policy_for_lane(lane)
    assert snapshot_policy is not None

    with tempfile.TemporaryDirectory(prefix="assay-plan-seed-") as raw_seed:
        seed_root = Path(raw_seed).resolve()
        spec = isolation.SnapshotSpec(
            repo_top=repo_top,
            commit=commit,
            project_prefix=project_prefix,
            scratch_root=seed_root,
            snapshot_policy=snapshot_policy,
        )
        with isolation.prepare_snapshot(spec, timeout=deadline.remaining()) as prepared:
            # B030/A-319: source roots are NOT relocated here, on purpose.
            # `_relocate_source_roots` respells `judge.source_root_paths`
            # against a MATERIALIZED snapshot's own project root, and the two
            # target resolvers below are handed
            # `snapshot_repo_top=prepared.spec.repo_top` -- the CONSUMER's
            # real repository top, since `plan` reads blobs out of the
            # prepared seed (`_read_prepared_source_text`) and never
            # materializes a snapshot at all. Relocating against a directory
            # that does not exist made
            # `resolve_mutation_targets`'s unconditional
            # `is_relative_to(root)` containment gate unsatisfiable, so every
            # lane planned as `candidate_count: 0`; a `whole_target` lane
            # failed outright naming the phantom path. The roots the gate
            # must be compared against here are the ones the lane actually
            # declares.
            source_root_paths = lane.judge.source_root_paths
            # B019/A-328: the identical declaration `run` resolves, through
            # the identical helper -- `plan` predicts a run, so a plan scoped
            # against a different base than the run it predicts would be
            # worse than emitting none.
            resolved_base = runner._resolve_declared_base(
                lane_file.project_root,
                base_declaration,
                remaining=deadline.remaining,
            )
            if lane.judge.mode == "whole_target":
                targets = runner._mutation_targets_whole(
                    prepared=prepared,
                    snapshot_repo_top=prepared.spec.repo_top,
                    project_prefix=project_prefix,
                    deadline=deadline,
                    adapter=adapter,
                    source_root_paths=source_root_paths,
                    targets=lane.judge.targets or (),
                )
            else:
                checked = measurability.check_base_is_head(
                    prepared.spec.repo_top,
                    resolved_base,
                    remaining=deadline.remaining,
                )
                diff_text = git.run(
                    prepared.spec.repo_top,
                    "diff",
                    "--unified=0",
                    checked.base_rev,
                    checked.head_rev,
                    remaining=deadline.remaining,
                )
                added = diff.parse_added_lines(diff_text)
                targets = runner._mutation_targets_from_diff(
                    added,
                    prepared=prepared,
                    deadline=deadline,
                    adapter=adapter,
                    snapshot_repo_top=prepared.spec.repo_top,
                    source_root_paths=source_root_paths,
                )
            jobs = mutation.collect_mutation_sites(
                targets,
                adapter=adapter,
                operators=operators,
                limit=lane.judge.mutation.max_mutants + 1,
            )

    if jobs == mutation.UNSUPPORTED:
        payload: dict[str, Any] = {
            "status": "unsupported",
            "reason_code": "MUTATION_UNSUPPORTED",
        }
    else:
        selected_indices = mutation.select_mutation_shard(
            [mutation.candidate_id(job) for job in jobs],
            index=0,
            count=1,
        ) if shard_index is None else mutation.select_mutation_shard(
            [mutation.candidate_id(job) for job in jobs],
            index=shard_index,
            count=shard_count,
        )
        jobs = tuple(jobs[index] for index in selected_indices)
        by_operator = Counter(job.site.operator for job in jobs)
        by_file = Counter(job.path for job in jobs)
        per_candidate = lane.judge.mutation.budget_per_candidate
        per_candidate_seconds = parse_duration(per_candidate) if per_candidate else 60.0
        serial_estimate = len(jobs) * per_candidate_seconds
        wall_estimate = serial_estimate / max(1, lane.judge.mutation.jobs)
        payload = {
            "status": "ok",
            "candidate_count": len(jobs),
            "max_mutants": lane.judge.mutation.max_mutants,
            "jobs": lane.judge.mutation.jobs,
            "shard": None if shard_index is None else f"{shard_index}/{shard_count}",
            "budget_per_candidate": per_candidate,
            "estimated_serial_seconds": round(serial_estimate, 3),
            "estimated_wall_seconds": round(wall_estimate, 3),
            "by_operator": dict(sorted(by_operator.items())),
            "by_file": dict(sorted(by_file.items())),
            "candidates": [
                {
                    "id": _plan_candidate_id(job),
                    "path": job.path,
                    "operator": job.site.operator,
                    "start_byte": job.site.start_byte,
                    "end_byte": job.site.end_byte,
                    "lineno": job.site.lineno,
                    "description": job.site.description,
                }
                for job in jobs
            ],
        }
    print(json.dumps(payload, indent=2, sort_keys=True), file=out)
    return 0


def _render_lanes(lane_file: LaneFile, out: TextIO) -> None:
    count = len(lane_file.lanes)
    print(
        f"{lane_file.path}: schema_version={lane_file.schema_version}, "
        f"{count} lane{'' if count == 1 else 's'}",
        file=out,
    )
    for name, lane in lane_file.lanes.items():
        judge = lane.judge
        judged = "none" if judge is None else (judge.language or "declared")
        print(
            f"  {name}  scope={lane.scope}  rigor={','.join(lane.rigor)}  "
            f"enforcement={lane.enforcement}  "
            f"budget={lane.budget} ({lane.budget_seconds:g}s)  "
            f"allow_argv_append={str(lane.allow_argv_append).lower()}  "
            f"judge={judged}",
            file=out,
        )
        print(f"    argv: {shlex.join(lane.argv)}", file=out)
        if judge is not None and judge.source_roots is not None:
            print(
                f"    source_roots: {', '.join(judge.source_roots)} "
                f"(relative to {lane_file.project_root})",
                file=out,
            )


#: (B044) The document's own top-level version. Bumped ONLY when an existing
#: key's MEANING changes -- adding a key (B043's `cwd`, B041's `link_paths`,
#: B045's `coverage.producer`, all `null`/`[]` here since this build does not
#: implement them yet) is additive and does not move this number, exactly as
#: `LANE_FILE_NAME`'s own `schema_version` distinguishes a meaning change from
#: an addition (A-thread in `config.py`).
LANE_INVENTORY_SCHEMA_VERSION = 1


def _render_lanes_json(lane_file: LaneFile, out: TextIO) -> None:
    """B044 -- ``assay lanes --json``: one machine-readable inventory
    document, so a gate tool (CIU stage 12, CIU-72) can learn what a project
    declared without re-parsing ``assay.toml`` itself and without asking the
    judge to run anything.

    **Every field has exactly one producer** -- the loaded ``Lane``/
    ``JudgeConfig`` (this process's own :mod:`assay.config` parse) or this
    build's own closed registry (:func:`_built_in_registry`, the identical
    object ``assay run`` resolves an adapter through) -- nothing here
    re-derives a fact from the raw TOML text a second, independent way.
    ``rigor_reachable``/``external_tools`` come from the registry entry (or
    are empty when the declared language is not registered at all -- an
    absent capability, not a refusal: unlike ``assay run``, this subcommand
    never raises for a rigor level or language this build cannot reach, so a
    gate can compare ``rigor`` against ``rigor_reachable`` itself instead of
    discovering the mismatch only when a real run refuses). Like the text
    form above, this renders nothing that would let a lane's declared argv
    run, and writes no verdict artifact (A-054).

    ``base_source`` resolves ``JudgeConfig.base_source``'s own documented
    absent-means-``"declared"`` default (A-328) rather than passing the raw
    ``None`` through -- the one place this function derives instead of
    reads, and it derives only the ALREADY-established meaning of that
    field's own absence, never a new one (A-347 records why: the whole point
    of this inventory is to let a gate tell "this lane owns its base" apart
    from "this lane delegates it" without reimplementing A-328 itself,
    which is exactly the four-copies divergence this project exists to
    close one layer up). It is ``null`` where the lane has no base concept
    at all -- no ``judge`` table, ``judge.mode == "whole_target"``, or
    neither R1 nor R2 declared -- mirroring :mod:`assay.config`'s own load
    time refusal of ``base_source`` in exactly those three shapes.

    A lane file that fails to load raises before this function is ever
    called (:func:`_resolve_lane_file` runs first in :func:`main`), so the
    existing ``except AssayError`` in :func:`main` already gives this
    subcommand its required exit 2 with an empty stdout and no partial JSON
    -- nothing below needs its own try/except for that.
    """
    built_in = _built_in_registry()
    document = {
        "inventory_schema": LANE_INVENTORY_SCHEMA_VERSION,
        "assay_version": __version__,
        "lanes": [
            _lane_inventory_entry(lane, built_in)
            for lane in lane_file.lanes.values()
        ],
    }
    print(json.dumps(document, indent=2, sort_keys=True), file=out)


def _lane_inventory_entry(lane: Lane, built_in: registry.Registry) -> dict[str, Any]:
    """One lane's own entry in :func:`_render_lanes_json`'s document."""
    judge = lane.judge
    language = judge.language if judge is not None else None
    entry = built_in.entries.get(language) if language is not None else None
    rigor_reachable = sorted(entry.rigor) if entry is not None else []
    external_tools = list(entry.adapter.external_tools) if entry is not None else []

    coverage: dict[str, Any] | None = None
    if judge is not None and judge.coverage is not None:
        coverage = {
            "format": judge.coverage.format,
            "artifact": judge.coverage.artifact,
            # (B045/schema v9) the DECLARED producer, or `null` when the
            # format allows the omission and the lane took it. Wave A shipped
            # this key as an unconditional `null` placeholder so a v9-aware
            # consumer's key set would not have to branch on which assay
            # version produced the document; Wave B wires it to the real
            # declared value. The key's MEANING is unchanged ("the declared
            # producer, or null"), so `inventory_schema` does not move
            # (A-349's own stability rule).
            "producer": judge.coverage.producer,
        }

    mutation = (
        judge.mutation.as_declared()
        if judge is not None and judge.mutation is not None
        else None
    )
    canary = (
        judge.canary.as_declared()
        if judge is not None and judge.canary is not None
        else None
    )

    base_source: str | None = None
    if (
        judge is not None
        and judge.mode != "whole_target"
        and ("R1" in lane.rigor or "R2" in lane.rigor)
    ):
        base_source = judge.base_source or "declared"

    return {
        "name": lane.name,
        "scope": lane.scope,
        "rigor": list(lane.rigor),
        "enforcement": lane.enforcement,
        "language": language,
        "rigor_reachable": rigor_reachable,
        "coverage": coverage,
        "mutation": mutation,
        "canary": canary,
        "base_source": base_source,
        "external_tools": external_tools,
        "argv0": lane.argv[0],
        "env_required": list(lane.env_required),
        "environment_command": lane.environment_command is not None,
        "infrastructure_facts": sorted(lane.infrastructure)
        if lane.infrastructure
        else [],
        "budget": lane.budget,
        # (B043/schema v9) the declared working directory, or `null` when the
        # lane declared none. `null` is the honest answer for that lane, not
        # a placeholder: `"."` would be a value the file never wrote.
        "cwd": lane.cwd,
        # (B041(b)/schema v9) the declared link_paths, `[]` when the lane
        # declared none. A non-empty list tells a gate that this lane's
        # snapshot will NOT be purely committed objects, and that the listed
        # directories must exist in the environment before the lane runs.
        "link_paths": list(lane.isolation.link_paths) if lane.isolation else [],
        "snapshot_selection": (
            lane.isolation.snapshot_selection if lane.isolation is not None else None
        ),
    }


if __name__ == "__main__":  # pragma: no cover - exercised as a subprocess
    sys.exit(main())
