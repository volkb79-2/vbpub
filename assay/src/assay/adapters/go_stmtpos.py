"""Invoke the shipped Go statement-position oracle and read its answer back.

This is the Python half of A-217's option 2: `src/assay/helpers/go/stmtpos/`
is the Go program that re-derives statement positions from SOURCE, and this
module is what runs it, checks its output and turns it into
:class:`~assay.statement_attribution.StatementBlock` values the language-free
join can consume.

# Why this is a subprocess at all

The information the correction needs — where each coverage block's own
statements BEGIN — exists only in the Go source, and the only correct way to
recover it is the segmentation `cmd/cover`'s instrumenter itself performs
(A-217: "adapt, do not invent"). A Python re-implementation of Go's parser
would be a hand-guessed oracle, which A-217 rules out explicitly: a wrong
statement position has no fail-closed direction, so it publishes a wrong
verdict rather than a loud refusal. So assay ships the real algorithm, in Go,
and runs it with the real toolchain. That subprocess boundary is what
:attr:`~assay.adapters.go.GoAdapter.external_tools` declares, and A-253's
already-built PATH preflight is what refuses a lane before it starts when no
`go` exists (this devcontainer, by policy — A-042/A-043).

# The environment is pinned, not inherited by accident

`GOPROXY=off`, `GOWORK=off`, `GOTOOLCHAIN=local` and `GOFLAGS=-mod=mod` are
set on every invocation. The helper's own `go.mod` declares no `require`
lines, so there is nothing to fetch — but "nothing to fetch" is a property of
the module today, and `GOPROXY=off` is what makes a future accidental
dependency fail loudly instead of silently reaching the network from inside a
gate container. `GOWORK=off` stops an ambient `go.work` in the repository
under judgment from pulling the helper into a workspace it has nothing to do
with; `GOTOOLCHAIN=local` stops the toolchain silently upgrading itself past
the `go` line, so the version recorded in `helpers[].identity` is the version
that ran.

# What it refuses, and why every one of them is a refusal

There is no partial answer here. A block whose statement positions could not
be derived is not "a block with no statements" — it is a block assay cannot
judge, and attributing it anyway would publish a verdict about lines that are
not the lines that ran (A-391). So: a non-zero exit, an unreadable document,
an unrecognised schema version, a missing file in the result, and a statement
list that violates :class:`StatementBlock`'s own invariants all raise.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import tempfile
from contextlib import contextmanager
from importlib.resources import files as resource_files
from pathlib import Path
from typing import Iterator, Mapping, Sequence

from ..errors import AssayError, Outcome, ReasonCode
from ..statement_attribution import StatementBlock
from .base import HelperInvocation, Remaining, StatementBlockReport

__all__ = [
    "HELPER_DIR",
    "HELPER_RESOURCES",
    "HELPER_RESOURCE_DIR",
    "OUTPUT_SCHEMA",
    "derive_statement_blocks",
]

#: Where the oracle's `.go` source and its `go.mod` live inside the installed
#: package, spelled as a filesystem path. It IS a real directory under a source
#: tree and under a wheel install; it is NOT one inside the shipped zipapp,
#: where those same files are members of a zip archive and every `is_file()`
#: on this path answers False. So this names a location; it does not promise
#: one exists. :func:`_staged_helper` is what turns either case into the real
#: directory `go run .` requires — see its docstring and A-403.
HELPER_DIR = Path(__file__).resolve().parent.parent / "helpers" / "go" / "stmtpos"

#: The oracle's files as :mod:`importlib.resources` anchors them. `helpers/` is
#: a data directory, not a package, so the anchor is the top-level `assay`
#: package and the remainder is spelled relative to it — the same shape
#: :func:`assay.verdict.schema_text` already uses for the shipped JSON Schema,
#: and the same spelling `tests/test_go_helper_is_packaged.py` asserts against
#: a real venv install.
HELPER_RESOURCE_DIR = "helpers/go/stmtpos"

#: Every file `go run .` needs in its working directory. Both are required:
#: `go run` refuses sources spread across directories, and without the
#: `go.mod` the helper is not a module at all.
HELPER_RESOURCES = ("stmtpos.go", "go.mod")

#: The `stmtpos` output-document version this module was written against,
#: pinned so a future change to the helper's shape cannot be read as the shape
#: assay expects. Must equal the helper's own `outputSchema` constant.
OUTPUT_SCHEMA = 1

#: Environment assignments forced on every invocation — see the module
#: docstring. Everything else is inherited: `go run` needs a build cache
#: (`GOCACHE`, defaulted from `HOME`) and a `PATH` to find its own tools.
_FORCED_ENV = {
    "GOPROXY": "off",
    "GOWORK": "off",
    "GOTOOLCHAIN": "local",
    "GOFLAGS": "-mod=mod",
}

#: The one message every "assay cannot supply its own oracle" refusal carries.
#: A consumer whose `go run .` fails must be told it is assay's installation
#: that is incomplete, not their repository — see A-403 for the failure this
#: message was actually delivering before the zipapp path was fixed.
_MISSING_HELPER = (
    "assay's Go statement-position oracle is missing from the installation: "
    "expected {path}. Without it a Go coverage profile's block extents cannot "
    "be resolved to statement positions at all"
)

#: Floor on the subprocess timeout. `remaining` can legitimately report a
#: small number near the end of a lane; a timeout of zero would refuse before
#: the process could start, reporting a helper failure where the real cause is
#: the lane deadline. The lane deadline still terminates the lane itself.
_MIN_TIMEOUT_SECONDS = 5.0


@contextmanager
def _staged_helper(helper_dir: Path | None) -> Iterator[Path]:
    """Yield a REAL directory holding the oracle's sources, for `go run .`.

    A-403. `go run .` takes a working directory, and under the shipped zipapp
    there is none: the helper's files are members of a zip archive, so
    :data:`HELPER_DIR` names a path that does not exist and the refusal a
    consumer got was "assay's Go statement-position oracle is missing from the
    installation" — for an oracle that was present in the artifact all along.
    That is the ONLY install path into `tester-unified-go` (A-402: the image
    inherits an interpreter but has no pip and no ensurepip), so it is the
    shape every Go consumer actually runs.

    The files are therefore read through :mod:`importlib.resources`, which
    answers identically from a source tree, a wheel and a zip archive, and
    written into a temporary directory for the duration of the call. This is
    ONE path, not a zipapp special case, deliberately: a branch taken only
    inside a zipapp would never be exercised by the registered gate (which
    installs a wheel), leaving the consumer's own path proven by nothing but
    the qualification run.

    *helper_dir*, when given, is used as-is and nothing is staged — it exists
    for the test that points at a deliberately broken installation, and
    staging a caller's own directory would defeat that test's whole subject.
    """
    if helper_dir is not None:
        if not (helper_dir / "stmtpos.go").is_file():
            raise _refuse(_MISSING_HELPER.format(path=helper_dir / "stmtpos.go"))
        yield helper_dir
        return

    anchor = resource_files("assay")
    payload: dict[str, bytes] = {}
    for name in HELPER_RESOURCES:
        resource = anchor.joinpath(f"{HELPER_RESOURCE_DIR}/{name}")
        try:
            payload[name] = resource.read_bytes()
        except (FileNotFoundError, OSError, KeyError) as exc:
            raise _refuse(
                _MISSING_HELPER.format(path=resource) + f" ({exc})"
            ) from exc

    with tempfile.TemporaryDirectory(prefix="assay-stmtpos-") as raw:
        staged = Path(raw)
        for name, data in payload.items():
            (staged / name).write_bytes(data)
        yield staged


def derive_statement_blocks(
    repo_top: Path,
    rel_paths: Sequence[str],
    *,
    remaining: Remaining | None = None,
    helper_dir: Path | None = None,
) -> StatementBlockReport:
    """Run the oracle over *rel_paths* (repo-relative, resolved against
    *repo_top*) and return their blocks plus the toolchain's own identity.

    *helper_dir* overrides where the shipped helper is looked for. It exists
    for a test that needs to point at a deliberately broken helper; production
    callers pass nothing and get the staged copy :func:`_staged_helper` writes
    from the package's own resources.

    Raises :class:`~assay.errors.AssayError` on every failure path — see the
    module docstring on why none of them may return a partial answer.
    """
    # The helper is staged FIRST, before either the inputs or the toolchain
    # are looked at: "assay's own installation cannot supply its oracle" is
    # true independently of both, and reporting it as a stale profile or a
    # missing `go` would name a cause that belongs to the caller's machine.
    with _staged_helper(helper_dir) as source_dir:
        return _derive(repo_top, rel_paths, remaining, source_dir)


def _derive(
    repo_top: Path,
    rel_paths: Sequence[str],
    remaining: Remaining | None,
    source_dir: Path,
) -> StatementBlockReport:
    """The body of :func:`derive_statement_blocks`, with the oracle's sources
    already materialised at *source_dir*. Split out only so the staging
    context manager does not indent every refusal below it."""
    # Inputs are validated BEFORE the environment is probed, deliberately: a
    # profile naming a file the tree does not have is wrong wherever it is
    # judged, and reporting that as "no Go toolchain" would fold a real
    # staleness finding into an environment fact -- AGENTS.md's "absence for
    # emptiness", one axis over. The toolchain lookup comes second, and at
    # lane level A-253's preflight has already run before either.
    abs_by_arg: dict[str, str] = {}
    ordered_args: list[str] = []
    for rel_path in rel_paths:
        absolute = (repo_top / rel_path).resolve()
        if not absolute.is_file():
            raise _refuse(
                f"the coverage artifact carries block extents for "
                f"{rel_path!r}, but that file does not exist at "
                f"{absolute} -- the profile and the working tree are not the "
                f"same revision, so its blocks cannot be resolved to "
                f"statement positions"
            )
        arg = str(absolute)
        abs_by_arg[arg] = rel_path
        ordered_args.append(arg)

    go_executable = shutil.which("go")
    if go_executable is None:
        # Belt and braces behind A-253's lane-level preflight: reaching here
        # means something bypassed it (a direct library caller), and a bare
        # FileNotFoundError from Popen would name no cause.
        raise AssayError(
            "the Go statement-position oracle needs `go` on PATH and it was "
            "not found; a Go lane cannot be judged without it",
            outcome=Outcome.NO_MEASUREMENT,
            reason_code=ReasonCode.MISSING_EXTERNAL_TOOL,
        )

    argv = [go_executable, "run", ".", *ordered_args]
    env = dict(os.environ)
    env.update(_FORCED_ENV)

    timeout = None
    if remaining is not None:
        timeout = max(_MIN_TIMEOUT_SECONDS, remaining())

    try:
        completed = subprocess.run(  # noqa: S603 - argv is fully constructed here
            argv,
            cwd=source_dir,
            env=env,
            stdin=subprocess.DEVNULL,
            capture_output=True,
            timeout=timeout,
        )
    except subprocess.TimeoutExpired as exc:
        raise AssayError(
            f"the Go statement-position oracle did not finish within "
            f"{timeout:.1f}s",
            outcome=Outcome.BUDGET_EXCEEDED,
            reason_code=ReasonCode.LANE_TIMEOUT,
        ) from exc
    except OSError as exc:
        raise _refuse(
            f"could not run the Go statement-position oracle "
            f"({go_executable}): {exc}"
        ) from exc

    if completed.returncode != 0:
        raise _refuse(
            f"the Go statement-position oracle exited "
            f"{completed.returncode}: {_tail(completed.stderr)}"
        )

    return _read_document(completed.stdout, abs_by_arg, go_executable)


def _read_document(
    stdout: bytes, abs_by_arg: Mapping[str, str], go_executable: str
) -> StatementBlockReport:
    """The helper's stdout, validated into a report.

    Split out so every check below is reachable from a unit test with a
    handcrafted document, WITHOUT a Go toolchain — the checks are assay's own
    code, so exercising them against a synthetic document is not a test double
    standing in for the external system (A-334); the external system's own
    behaviour is proven separately, against the real toolchain.
    """
    try:
        document = json.loads(stdout.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise _refuse(
            f"the Go statement-position oracle's output is not readable "
            f"JSON: {exc}"
        ) from exc

    if not isinstance(document, dict):
        raise _refuse(
            f"the Go statement-position oracle emitted a "
            f"{type(document).__name__}, not a JSON object"
        )

    schema = document.get("schema")
    if schema != OUTPUT_SCHEMA:
        raise _refuse(
            f"the Go statement-position oracle reports output schema "
            f"{schema!r}; this assay reads schema {OUTPUT_SCHEMA}. Refusing "
            f"rather than reading an unknown shape as the known one"
        )

    go_version = document.get("go_version")
    if not isinstance(go_version, str) or not go_version.strip():
        raise _refuse(
            "the Go statement-position oracle reported no toolchain version; "
            "a verdict's helpers[].identity must name the toolchain that "
            "actually produced it, never a value assay supplied itself"
        )

    files = document.get("files")
    if not isinstance(files, list):
        raise _refuse(
            "the Go statement-position oracle's output has no `files` array"
        )

    blocks_by_path: dict[str, tuple[StatementBlock, ...]] = {}
    for entry in files:
        if not isinstance(entry, dict):
            raise _refuse("a `files` entry is not a JSON object")
        arg = entry.get("path")
        rel_path = abs_by_arg.get(arg) if isinstance(arg, str) else None
        if rel_path is None:
            raise _refuse(
                f"the Go statement-position oracle reported a file "
                f"{arg!r} that was never asked for"
            )
        blocks_by_path[rel_path] = tuple(
            _read_block(raw, rel_path) for raw in entry.get("blocks") or ()
        )

    missing = sorted(set(abs_by_arg.values()) - set(blocks_by_path))
    if missing:
        raise _refuse(
            f"the Go statement-position oracle returned no result for "
            f"{missing} -- an absent file would reach the extent join as "
            f"'this file has no statement positions', which names the wrong "
            f"cause"
        )

    return StatementBlockReport(
        blocks_by_path=blocks_by_path,
        helper=HelperInvocation(
            tool="go",
            resolved_path=go_executable,
            # `go version <v>` rather than the bare `<v>` the helper reports:
            # this string is read by a human diagnosing a verdict, and the
            # version alone does not say what produced it. The VERSION half is
            # measured — `runtime.Version()` inside the helper, i.e. the
            # toolchain that actually compiled and ran it, which is a stronger
            # fact than parsing a separate `go version` invocation's stdout
            # (that would be a second process, and could be a different one).
            identity=f"go version {go_version}",
        ),
    )


def _read_block(raw: object, rel_path: str) -> StatementBlock:
    if not isinstance(raw, dict):
        raise _refuse(f"{rel_path!r}: a `blocks` entry is not a JSON object")
    try:
        return StatementBlock(
            start_line=_int(raw, "start_line", rel_path),
            start_col=_int(raw, "start_col", rel_path),
            end_line=_int(raw, "end_line", rel_path),
            end_col=_int(raw, "end_col", rel_path),
            num_stmts=_int(raw, "num_stmts", rel_path),
            stmt_lines=tuple(
                _stmt_line(n, rel_path) for n in raw.get("stmt_lines") or ()
            ),
        )
    except ValueError as exc:
        # StatementBlock's own construction-time invariants (1-based LINES,
        # non-negative columns, sorted duplicate-free statement lines). A
        # document that violates them is not a block assay may guess at.
        #
        # (A-405) The column bound is `>= 0` and not `>= 1`, which matters
        # HERE and not only in the parser: the oracle derives its positions
        # with `go/token`, so a source carrying a `//line file:line` directive
        # with no column makes the oracle emit `start_col`/`end_col` of 0 for
        # its own blocks. Run over Go's own `TestLineDup` corpus it emits six
        # such blocks (`carve-assets/P27-recarve/linedup-oracle.json`). Under
        # the old bound this refusal would have fired on the helper's own
        # correct output and blamed the helper for it.
        raise _refuse(f"{rel_path!r}: {exc}") from exc


def _stmt_line(value: object, rel_path: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise _refuse(
            f"{rel_path!r}: a `stmt_lines` entry is {value!r}, not an integer"
        )
    return value


def _int(raw: Mapping[str, object], field: str, rel_path: str) -> int:
    value = raw.get(field)
    if isinstance(value, bool) or not isinstance(value, int):
        raise _refuse(
            f"{rel_path!r}: block field {field!r} is {value!r}, not an integer"
        )
    return value


def _tail(stderr: bytes, limit: int = 400) -> str:
    text = stderr.decode("utf-8", errors="replace").strip()
    if not text:
        return "(no stderr)"
    return text[-limit:]


def _refuse(message: str) -> AssayError:
    return AssayError(
        message,
        outcome=Outcome.ERROR,
        reason_code=ReasonCode.UNREADABLE_ARTIFACT,
    )
