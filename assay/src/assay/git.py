"""The thin git subprocess boundary.

Every other module in this package that needs git talks to it through here —
one function that shells out (:func:`_run_bytes`, a raw-bytes boundary),
:func:`run` which decodes its output, and a handful of thin wrappers over
specific invocations. Nothing above this module ever builds a
``["git", ...]`` argv itself.

**P20 (A-173): Git itself is a controlled input.** ``-C <repo>`` alone is not
an identity boundary — a repository-LOCAL ``core.worktree`` redirects
``rev-parse --show-toplevel``/``status`` despite ``-C`` and even a
command-line ``-c core.worktree=...`` override (verified empirically against
a real git binary: the JIT probe's own witnessed evidence). Every substantive
command now resolves the repository identity *before* running by walking the
supplied directory's finite ancestor chain to the nearest non-symlink
``.git`` directory or regular gitfile (never asking git itself, which is
exactly the part a local config redirect can lie about) and separately
resolving the real git-dir via one sanitized bootstrap
``rev-parse --absolute-git-dir`` call, then anchors with explicit
``--git-dir=<resolved>``/``--work-tree=<resolved>`` on the real command — a
command-line ``--work-tree`` (unlike ``-c core.worktree=``) does take
precedence over local config (also verified empirically). The git executable
itself is resolved exactly once per call from the caller's own declared
``PATH`` (never a conventional fallback location), and every child receives a
closed REPLACEMENT environment carrying no ambient ``GIT_*``/``HOME``/
``XDG_*``/``PATH``/pager/replacement-ref/config-counter value — see
:data:`_REPLACEMENT_ENV`.

The non-obvious behaviours this module exists to get right, taken from the
union of the three cited sibling gates (``dstdns/scripts/coverage_gate.py``,
``nyxloom/src/nyxloom/coverage_gate.py``, ``topos/tools/coverage_gate.py``)
and verified empirically against a real git binary while writing this module:

* :func:`resolve_base` — a merge-commit ``HEAD`` (two or more parents)
  resolves to its **first parent** (the merged-into branch, pre-merge); any
  other ``HEAD`` resolves to ``merge-base(base, HEAD)`` (the feature branch's
  fork point). One command, ``git rev-list --parents -n 1 HEAD``, tells you
  which case you are in — its token count is 2 for a normal commit and ≥3 for
  a merge.
* :func:`dirty_paths` — ``git status --porcelain`` **always** reports paths
  relative to the repository's top level, never to the ``-C`` directory or the
  process cwd (unlike ``git diff --relative``). A caller that forgets this and
  treats the output as relative to whatever directory it passed will build the
  wrong absolute path the moment the repo is a monorepo and the git command
  was invoked from a subdirectory — exactly assay's own situation (A-049).
  :func:`repo_top` is what lets a caller convert correctly.

**P15 (A-067 finding 5, sol's post-series review).** Every invocation through
:func:`run`/:func:`_run_bytes` now forces ``-c core.quotePath=false`` — the
half of git's path-quoting behaviour this project can simply turn off: a
non-ASCII byte then passes through raw instead of being octal-escaped inside
a quoted string. That does NOT disable quoting for a control byte (including
a literal embedded newline), a backslash, or a double quote — git always
quotes a path containing one of those, regardless of this setting.
:func:`dirty_paths` sidesteps that remaining case entirely by reading
``git status`` in its ``-z`` (NUL-delimited) form, which never quotes a path
at all — the old display-format parsing (line-oriented, splitting a
rename's ``old -> new`` text, vulnerable to a path that legally contains
that exact substring) is gone. :mod:`assay.diff` still needs its own
C-style unquoter for the one command this module cannot give a NUL-delimited
form to (``git diff``'s full patch text has no ``-z`` mode) — see that
module's own docstring.

**Controller repair (A-134).** Turning ``core.quotePath`` off is exactly what
lets a raw non-ASCII byte reach this process, so the *decoding* side of the
boundary had to move with it: every command now goes through
:func:`_run_bytes` and one :func:`_decode_or_reject` policy, not just
:func:`dirty_paths`. Decoding via ``subprocess``' ``text=True`` would have
picked git's output apart with **the ambient locale's** codec — under a
non-UTF-8 ``LC_CTYPE`` a path assay can represent perfectly well became a
bare ``UnicodeDecodeError`` — and would additionally have applied
universal-newline translation, silently turning a bare ``\\r`` **inside a
source line** into a second ``\\n`` and shifting every changed-line number
after it. Both are refused: UTF-8 explicitly, no newline translation, and a
typed ``ERROR``/``GIT_FAILED`` for output assay genuinely cannot represent.

This module raises nothing but :class:`~assay.errors.AssayError`; there is no
locally-defined exception type here (A-091 — ``errors.py`` is outside this
package's ``scope.touch``).
"""

from __future__ import annotations

import os
import selectors
import shutil
import signal
import stat as stat_module
import subprocess
import time
from dataclasses import dataclass
from pathlib import Path
from types import MappingProxyType
from typing import Callable, Mapping, Sequence

from .errors import AssayError, Outcome, ReasonCode

__all__ = [
    "Remaining",
    "dirty_paths",
    "head_rev",
    "is_ancestor",
    "path_is_current",
    "repo_top",
    "resolve_base",
    "run",
    "tree_entry_kind",
    "verify_exact_commit",
]

#: One lane's remaining budget, sampled fresh at every boundary (P26/A-212).
#: ``None`` means "no lane deadline owns this call" -- legal only for a
#: genuine non-lane/legacy caller; every lane-owned call passes a callable.
Remaining = Callable[[], float]

#: The CLOSED replacement environment every git child receives -- REPLACES
#: the process environment entirely (never merged), so no ambient GIT_*,
#: HOME, XDG_*, PATH, pager, editor, config-counter, replacement-ref,
#: object-directory, alternate, work-tree, or repository selector crosses the
#: child boundary (A-173). ``C.UTF-8`` matches :func:`_decode_or_reject`'s own
#: explicit UTF-8 policy; a locale-dependent codec must never decide what a
#: path or patch byte means. Verified in ``tester-unified`` by the JIT probe.
_REPLACEMENT_ENV: Mapping[str, str] = MappingProxyType(
    {
        "LC_ALL": "C.UTF-8",
        "LANG": "C.UTF-8",
        "GIT_CONFIG_NOSYSTEM": "1",
        "GIT_CONFIG_SYSTEM": os.devnull,
        "GIT_CONFIG_GLOBAL": os.devnull,
        "GIT_ATTR_NOSYSTEM": "1",
        "GIT_NO_REPLACE_OBJECTS": "1",
        "GIT_TERMINAL_PROMPT": "0",
        "GIT_OPTIONAL_LOCKS": "0",
        "GIT_PAGER": "",
        "PAGER": "",
    }
)

#: Applied to every substantive command (bootstrap and real alike): disables
#: hooks, fsmonitor, and commit signing, and turns off the half of git's path
#: quoting this project can (P15/A-134's own docstring, below).
#:
#: ``core.excludesFile=`` (empty) is the REVIEW repair of an inconsistency in
#: the original set: :data:`_REPLACEMENT_ENV` already neutralises a SYSTEM or
#: GLOBAL excludes file (``GIT_CONFIG_NOSYSTEM``/``GIT_CONFIG_GLOBAL``, plus
#: the absent ``HOME``/``XDG_*`` that git's built-in default would resolve
#: through), but a repository-LOCAL ``core.excludesFile`` in ``.git/config``
#: survived all of it -- so the same untracked file was reported or hidden
#: depending only on WHICH config file named the excludes list. Verified
#: empirically against a real git binary: with a local
#: ``core.excludesFile`` naming a file containing ``*``, an untracked
#: ``leftover.bin`` vanished from ``status --porcelain -z``; with this
#: override it is reported again, while a path ignored by the repository's
#: own TRACKED ``.gitignore`` (the mechanism the declared coverage artifact's
#: exemption actually relies on -- P20 work item 6) stays correctly hidden.
#: This closes the config half of the dirty-set attack; the ``.git/info/
#: exclude`` half is repository CONTENT that no config option can reach and
#: is deliberately NOT worked around here (see this module's own
#: ``dirty_paths`` note).
_FIXED_CONFIG: tuple[str, ...] = (
    "-c", "core.quotePath=false",
    "-c", "core.hooksPath=/dev/null",
    "-c", "core.fsmonitor=",
    "-c", "commit.gpgSign=false",
    "-c", "core.excludesFile=",
)

#: The fixed ceiling on how many bytes of a git child's stdout this process
#: will retain (O4: a FIXED byte bound, never an ambient or elapsed-time
#: guess). ``subprocess.run(capture_output=True)`` buffers whatever the child
#: writes, so before this bound a hostile or merely enormous repository could
#: make a single ``diff``/``status`` allocate without limit. Deliberately
#: GENEROUS rather than tight: like AUTHORING.md 3b.A's failsafe timeout, it
#: must never be the thing that decides a verdict on a legitimate repository
#: -- it exists to make the work finite, not to judge a diff's size.
MAX_GIT_OUTPUT_BYTES = 64 * 1024 * 1024

#: stderr is only ever used to build an error message (truncated to 200
#: characters at every call site), so it needs far less headroom than stdout.
_MAX_GIT_STDERR_BYTES = 64 * 1024


def _git_failed(message: str) -> AssayError:
    return AssayError(message, outcome=Outcome.ERROR, reason_code=ReasonCode.GIT_FAILED)


def _sample_remaining(remaining: Remaining | None) -> float | None:
    """Sample *remaining* once, or ``None`` when no lane deadline applies.

    Whatever :func:`~assay.runner.LaneDeadline.remaining` raises on expiry
    (``AssayError``/``BUDGET_EXCEEDED``/``LANE_TIMEOUT``) propagates
    unmodified -- this is the ONE object every abnormal-cleanup path below
    re-raises, never a fresh exception of assay's own.
    """
    if remaining is None:
        return None
    return remaining()


def _kill_owned_group(proc: subprocess.Popen[bytes]) -> None:
    """Best-effort SIGKILL of the whole process group, regardless of whether
    the direct child has already exited (P26/A-212).

    A forked descendant can still hold stdout/stderr open after the direct
    boundary child itself has exited -- ``proc.poll() is not None`` must
    never be read as "nothing left to kill".
    """
    try:
        os.killpg(proc.pid, signal.SIGKILL)
    except ProcessLookupError:
        pass
    except OSError:
        try:
            proc.kill()
        except OSError:
            pass


def _run_bounded(argv: list[str], *, remaining: Remaining | None = None) -> tuple[int, bytes, bytes]:
    """Run *argv* with the sanitized environment and return
    ``(returncode, stdout, stderr)`` with BOTH streams bounded.

    ``subprocess.run(capture_output=True)`` -- what every call site here used
    before this review repair -- accumulates the child's whole output in
    memory, so the size of a ``diff`` or ``status`` was decided by the
    repository rather than by assay (O4's "fixed byte/path/work bounds").
    Both pipes are drained concurrently through :mod:`selectors`, which is
    what keeps a large stderr from deadlocking against a large stdout; once
    stdout passes :data:`MAX_GIT_OUTPUT_BYTES` the child is killed and the
    remaining bytes are discarded rather than retained, so the work stays
    finite instead of merely un-retained. The same rule applies to stderr:
    retaining only its prefix while continuing to drain an arbitrary producer
    would cap memory but not work. Either overflow becomes
    ``ERROR``/``GIT_FAILED``.

    **P26/A-212's callable deadline.** With *remaining* supplied, the child
    starts its own session (:data:`subprocess.Popen`'s ``start_new_session``)
    so assay owns its whole process group, not merely its direct pid.
    *remaining* is sampled before the child is ever started and again before
    every pipe-selector/exit wait -- an expiry raised there propagates as the
    SAME exception object, after this function SIGKILLs the owned group,
    drains/closes within the existing byte bounds, and reaps the direct
    child. No later bootstrap/substantive child is ever started once
    *remaining* has raised.
    """
    _sample_remaining(remaining)
    try:
        proc = subprocess.Popen(
            argv,
            env=dict(_REPLACEMENT_ENV),
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            start_new_session=True,
        )
    except OSError as exc:
        raise _git_failed(f"could not start git: {exc}") from exc
    assert proc.stdout is not None and proc.stderr is not None
    selector = selectors.DefaultSelector()
    out = bytearray()
    err = bytearray()
    overflowed: tuple[str, int] | None = None
    completed = False
    try:
        selector.register(proc.stdout, selectors.EVENT_READ, "out")
        selector.register(proc.stderr, selectors.EVENT_READ, "err")
        while selector.get_map() and overflowed is None:
            timeout = _sample_remaining(remaining)
            for key, _ in selector.select(timeout):
                chunk = key.fileobj.read1(65536)
                if not chunk:
                    selector.unregister(key.fileobj)
                    continue
                if key.data == "out":
                    out += chunk
                    if len(out) > MAX_GIT_OUTPUT_BYTES:
                        # Stop reading, stop retaining, and stop the child:
                        # draining an oversized stream would keep the WORK
                        # unbounded even once the MEMORY is not.
                        overflowed = ("standard output", MAX_GIT_OUTPUT_BYTES)
                        del out[:]
                        break
                else:
                    room = _MAX_GIT_STDERR_BYTES - len(err)
                    err += chunk[:room]
                    if len(chunk) > room:
                        overflowed = ("standard error", _MAX_GIT_STDERR_BYTES)
                        break
        while overflowed is None and proc.poll() is None:
            timeout = _sample_remaining(remaining)
            if timeout is None:
                proc.wait()
                break
            try:
                proc.wait(timeout=timeout)
            except subprocess.TimeoutExpired:
                # *remaining* alone owns expiry; a wait timeout only means
                # the deadline must be sampled (and possibly raised) again.
                continue
        if overflowed is None:
            completed = True
    finally:
        if not completed:
            _kill_owned_group(proc)
        for key in tuple(selector.get_map().values()):
            try:
                selector.unregister(key.fileobj)
            except (KeyError, ValueError):  # pragma: no cover - defensive
                pass
        selector.close()
        proc.stdout.close()
        proc.stderr.close()
        try:
            proc.wait()
        except (OSError, subprocess.SubprocessError):
            # Never mask an active deadline/overflow exception with a reap
            # failure; only surface this on an otherwise-normal completion.
            if completed:
                raise
    if overflowed is not None:
        stream, limit = overflowed
        raise _git_failed(
            f"git {' '.join(argv[-3:])} produced more than {limit} bytes on "
            f"{stream}; refusing to process an unbounded amount of repository "
            f"data from the child"
        )
    return proc.returncode, bytes(out), bytes(err)


def _resolve_git_executable() -> Path:
    """Resolve the git executable exactly once from the CALLER's own
    declared ``PATH`` — never a conventional fallback location (``/usr/bin/
    git`` and friends). Absence, or a resolved target that is not an
    absolute regular executable, is ``ERROR``/``GIT_FAILED``: a missing or
    unusable git remains a typed terminal, never another repository or a
    local configuration fallback (work item 1).
    """
    declared_path = os.environ.get("PATH")
    if not declared_path:
        raise _git_failed("no PATH is declared in the caller's environment; refusing to guess where git lives")
    found = shutil.which("git", path=declared_path)
    if found is None:
        raise _git_failed(f"git is not on the caller-declared PATH ({declared_path!r})")
    try:
        resolved = Path(found).resolve(strict=True)
        mode = resolved.stat().st_mode
    except OSError as exc:
        raise _git_failed(f"resolved git executable {found!r} could not be resolved/stat'd: {exc}") from exc
    if not resolved.is_absolute() or not stat_module.S_ISREG(mode) or not os.access(resolved, os.X_OK):
        raise _git_failed(f"resolved git {resolved} is not an absolute regular executable")
    return resolved


@dataclass(frozen=True, kw_only=True)
class _ResolvedRepo:
    """The exact repository identity every substantive command anchors to:
    a trusted work-tree root (found by OUR OWN filesystem walk, never by
    asking git — see module docstring) and its real git directory (resolved
    by one sanitized bootstrap call, since only git itself can correctly
    follow a linked-worktree gitfile)."""

    repo_top: Path
    git_dir: Path


def _nearest_git_marker(start: Path) -> Path:
    """Walk *start*'s finite ancestor chain to the nearest directory holding
    a non-symlink ``.git`` directory or regular gitfile (A-173). Never asks
    git — a repository-local ``core.worktree`` cannot lie about a fact this
    function derives purely from the filesystem. Refuses no marker found, a
    symlink marker, and a marker that is neither a directory nor a regular
    file.
    """
    candidate = start.resolve()
    while True:
        marker = candidate / ".git"
        try:
            marker_stat = marker.lstat()
        except OSError:
            marker_stat = None
        if marker_stat is not None:
            if stat_module.S_ISLNK(marker_stat.st_mode):
                raise _git_failed(f"{marker} is a symlink; refusing to treat it as a repository marker")
            if not (stat_module.S_ISDIR(marker_stat.st_mode) or stat_module.S_ISREG(marker_stat.st_mode)):
                raise _git_failed(f"{marker} is neither a directory nor a regular file; refused")
            return candidate
        parent = candidate.parent
        if parent == candidate:
            raise _git_failed(f"no .git marker found in any ancestor of {start.resolve()}")
        candidate = parent


def _resolve_repo(
    repo: Path, git_executable: Path, *, remaining: Remaining | None = None
) -> _ResolvedRepo:
    """Resolve *repo*'s trusted work-tree root and real git directory
    (A-173). The bootstrap ``rev-parse --absolute-git-dir`` call runs with
    the SAME sanitized :data:`_REPLACEMENT_ENV` (so no ambient ``GIT_DIR``
    can redirect it either) from ``-C <repo_top>`` — a location OUR OWN walk
    already trusts — but deliberately without ``--git-dir``/``--work-tree``,
    since resolving the git-dir correctly (including a linked-worktree
    gitfile redirect) is precisely what this call is for.

    *remaining* (P26/A-212) reaches this bootstrap call too: a lane deadline
    that expires here must stop before any substantive command ever starts.
    """
    repo_top = _nearest_git_marker(repo)
    argv = [
        str(git_executable),
        "--no-pager",
        "--no-optional-locks",
        *_FIXED_CONFIG,
        "-C",
        str(repo_top),
        "rev-parse",
        "--absolute-git-dir",
    ]
    returncode, stdout, stderr = _run_bounded(argv, remaining=remaining)
    if returncode != 0:
        raise _git_failed(
            f"git rev-parse --absolute-git-dir failed resolving {repo_top} "
            f"({returncode}): {stderr.decode('utf-8', errors='replace').strip()[:200]}"
        )
    git_dir_text = _decode_or_reject(stdout, "the resolved git directory").strip()
    git_dir = Path(git_dir_text)
    if not git_dir.is_absolute() or not git_dir.is_dir():
        raise _git_failed(
            f"resolved git-dir {git_dir_text!r} for {repo_top} is not an absolute, existing directory"
        )
    return _ResolvedRepo(repo_top=repo_top, git_dir=git_dir)


def _prepare_subcommand_args(args: Sequence[str]) -> tuple[str, ...]:
    """Insert command-specific hardening flags the caller may have omitted
    (work item 1): every ``diff`` invocation gets ``--no-ext-diff
    --no-textconv`` even when the caller's own *args* did not ask for it, so
    a repository-local ``diff.external``/textconv filter can never run.
    """
    if args and args[0] == "diff":
        return (args[0], "--no-ext-diff", "--no-textconv", *args[1:])
    return tuple(args)


def run(repo: Path, *args: str, remaining: Remaining | None = None) -> str:
    """Run *args* anchored to *repo*'s resolved identity and return stdout
    decoded as UTF-8.

    *repo* may be any directory inside the working tree — this module
    resolves the real repository from there itself (never trusting git's own
    ambient-influenced discovery, A-173). Raises :class:`AssayError`
    (``ERROR`` / ``GIT_FAILED``) on a non-zero exit; the message carries the
    argv and the first 200 characters of stderr, matching the cited
    implementations. Output that is not valid UTF-8 raises the same typed
    error rather than a bare ``UnicodeDecodeError`` — see
    :func:`_decode_or_reject`, and the module docstring for why the decode is
    explicit rather than ``subprocess``' locale-driven ``text=True``.

    *remaining* (P26/A-212) is the one lane deadline this call, its repository
    bootstrap, and its process group all sample from; ``None`` stays legal
    only for a genuine non-lane caller.
    """
    return _decode_or_reject(
        _run_bytes(repo, *args, remaining=remaining), f"the output of git {' '.join(args)}"
    )


def _run_raw(
    repo: Path, *args: str, remaining: Remaining | None = None
) -> tuple[int, bytes, bytes]:
    """Resolve *repo* and run *args* against it, returning the RAW
    ``(returncode, stdout, stderr)`` without interpreting a non-zero exit.

    The shared construction every git call in this module (before P22) goes
    through: fresh executable/repository resolution (A-173, no cross-call
    cache), the literal-pathspec/git-dir/work-tree anchor, and *remaining*
    forwarded to both the bootstrap and the substantive child. :func:`_run_bytes`
    is the ordinary "any nonzero exit is GIT_FAILED" sibling; the narrow P26
    attestation helpers (:func:`is_ancestor`, :func:`tree_entry_kind`,
    :func:`path_is_current`) call this directly because THEY, not a generic
    wrapper, own what a particular exit code means.
    """
    git_executable = _resolve_git_executable()
    resolved = _resolve_repo(Path(repo), git_executable, remaining=remaining)
    argv = [
        str(git_executable),
        "--no-pager",
        "--no-optional-locks",
        "--literal-pathspecs",
        f"--git-dir={resolved.git_dir}",
        f"--work-tree={resolved.repo_top}",
        *_FIXED_CONFIG,
        "-C",
        str(resolved.repo_top),
        *_prepare_subcommand_args(args),
    ]
    return _run_bounded(argv, remaining=remaining)


def _run_bytes(repo: Path, *args: str, remaining: Remaining | None = None) -> bytes:
    """Run a git command and return its raw stdout bytes, undecoded.

    Every caller in this module reaches git through here (or, for the narrow
    P26 helpers that must see a specific nonzero exit themselves, through
    :func:`_run_raw` directly). Raw bytes are the honest boundary: what git
    writes is a byte stream, and both the *what encoding* question
    (:func:`_decode_or_reject`) and the *where do records end* question
    (``-z`` in :func:`dirty_paths`) are then answered deliberately, in one
    place, instead of by ``subprocess``' defaults.
    """
    returncode, stdout, stderr = _run_raw(repo, *args, remaining=remaining)
    if returncode != 0:
        raise AssayError(
            f"git {' '.join(args)} failed ({returncode}): "
            f"{stderr.decode('utf-8', errors='replace').strip()[:200]}",
            outcome=Outcome.ERROR,
            reason_code=ReasonCode.GIT_FAILED,
        )
    return stdout


def _decode_or_reject(raw: bytes, what: str) -> str:
    """Decode git output — a whole stream, or one ``-z`` field — or refuse it.

    assay's own path contract (:mod:`assay.adapters.base`'s docstring) is
    UTF-8 throughout, so bytes this function cannot decode name something
    assay cannot represent anywhere else either. It is refused, loudly and
    typed, rather than silently replaced, mangled, or decoded with whatever
    codec the ambient locale happens to name.

    ``git status -z`` never quotes a path — this is exactly why P15 moved
    :func:`dirty_paths` to it — so for a path field the only way to arrive
    here is a real on-disk name that is not valid UTF-8.
    """
    try:
        return raw.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise AssayError(
            f"{what} is not valid UTF-8 ({raw[:200]!r}): {exc}. assay only "
            f"supports UTF-8-encoded repository paths.",
            outcome=Outcome.ERROR,
            reason_code=ReasonCode.GIT_FAILED,
        ) from exc


def repo_top(repo: Path, *, remaining: Remaining | None = None) -> Path:
    """The absolute, resolved top level of the git repository containing
    *repo* — what every ``git status --porcelain`` path is relative to,
    regardless of which subdirectory this process was pointed at.

    **P20 (A-173): never asks git.** ``rev-parse --show-toplevel`` reports a
    repository-local ``core.worktree`` redirect even from ``-C <repo>``
    (verified empirically), which is precisely the ambient fact this
    function must not trust — so it returns the same trusted work-tree root
    every other command in this module anchors to (:func:`_resolve_repo`),
    derived purely from the filesystem, never from git's own opinion of its
    worktree.
    """
    git_executable = _resolve_git_executable()
    return _resolve_repo(Path(repo), git_executable, remaining=remaining).repo_top


def head_rev(repo: Path, *, remaining: Remaining | None = None) -> str:
    """The full SHA of ``HEAD``."""
    return run(repo, "rev-parse", "HEAD", remaining=remaining).strip()


def _resolve_revision(repo: Path, revision: str, *, remaining: Remaining | None = None) -> str:
    """Validate and resolve *revision* (a caller/lane-declared string, never
    assumed to already be safe) to its full commit OID before it is used in
    any further command (A-173's "user-controlled revisions are first
    validated/resolved to full OIDs"). ``--end-of-options`` stops a
    revision spelling that happens to start with ``-`` from ever being
    parsed as a flag by the command that resolves it; the returned 40-hex
    OID cannot be mistaken for one either.
    """
    return run(
        repo, "rev-parse", "--verify", "--end-of-options", f"{revision}^{{commit}}",
        remaining=remaining,
    ).strip()


def resolve_base(repo: Path, base: str, *, remaining: Remaining | None = None) -> str:
    """Resolve *base* against the shape of ``HEAD``.

    A merge commit (``HEAD`` has two or more parents) resolves to its first
    parent — the merged-into branch's tip immediately before the merge, so the
    delta measured is the merge's own payload. Any other ``HEAD`` resolves to
    ``merge-base(base, HEAD)`` — the fork point, so a feature branch is judged
    against what it actually diverged from rather than *base*'s current tip
    (which may have moved since). *base* is a lane-declared string, so it is
    validated/resolved to a full OID (:func:`_resolve_revision`) before it
    reaches ``merge-base`` (A-173).
    """
    tokens = run(repo, "rev-list", "--parents", "-n", "1", "HEAD", remaining=remaining).split()
    if len(tokens) >= 3:  # HEAD sha + >=2 parent shas
        return tokens[1]
    resolved_base = _resolve_revision(repo, base, remaining=remaining)
    return run(
        repo, "merge-base", "--end-of-options", resolved_base, "HEAD", remaining=remaining
    ).strip()


def dirty_paths(repo: Path, *, remaining: Remaining | None = None) -> tuple[str, ...]:
    """Repo-top-relative paths of every staged, unstaged, or untracked change.

    ``git status --porcelain`` reports the index AND the worktree in one pass
    (unlike ``git diff``, which only ever shows one side), so a staged-but-
    uncommitted change is caught exactly like an unstaged one — both are
    equally invisible to a ``base..HEAD`` diff. Every record counts, whatever
    its two status letters say: staged, unstaged, and untracked (``??``) are
    not filtered differently here, because a change this function fails to
    report is a change a caller could measure past without knowing it was
    never actually seen.

    **P15 (A-067 finding 5): read via ``-z`` (NUL-delimited), never the
    display form.** Under ``-z`` git never quotes a path at all — a space, a
    tab, a non-ASCII byte, or even a literal embedded newline all appear as
    their real raw bytes, with only a NUL byte ever serving as a field
    terminator. A rename or copy record (``X``/``Y`` containing ``R``/``C``)
    is exactly TWO consecutive NUL-terminated fields — the new path, then the
    old one — never one line joined by the display string ``" -> "``, which
    the old implementation matched literally and which a real path could
    itself legally contain. Only the new path (the one still in the tree) is
    kept, matching the previous contract.

    Scoping to a set of source roots is deliberately NOT done here — see
    ``measurability.check_dirty_tree``, which does that matching by resolved
    filesystem path rather than by string, and is where the distinction
    actually needs to be correct.

    **P20 routed-review correction (A-177).** Porcelain status still consults
    ``.git/info/exclude``. Union it with ``ls-files --others`` configured with
    ONLY ``--exclude-per-directory=.gitignore``. A clean committed
    ``.gitignore`` is repository policy and may exempt the declared coverage
    artifact; info/global/system/configured excludes are not. A dirty or
    untracked ``.gitignore`` is itself returned by one of these queries. Do not
    use ``--exclude-standard`` (it re-enables the hostile sources), and do not
    report every ignored path then subtract one artifact (that discards the
    repository's committed ignore policy).

    **B017 tried ``--exclude-standard`` and was reverted (A-290, corrected).**
    The filed premise (a committed blanket ``/.assay/`` rule "also hides it")
    did not reproduce — measured identical under both flag choices. The
    change did open a real hole: ``--exclude-standard`` also honors
    ``.git/info/exclude``, which is repository-local, unversioned, and
    reported by nothing else, so a personal ignore rule there could hide a
    brand-new untracked source file from this function with no self-reporting
    trail. A-177's restriction stands.
    """
    raw = _run_bytes(repo, "status", "--porcelain=v1", "-z", remaining=remaining)
    # ``-z`` NUL-TERMINATES every record rather than separating them, so real
    # output always ends in a trailing NUL when there is at least one record,
    # and is simply zero bytes when there are none (``b"".split(b"\x00") ==
    # [b""]``) -- either way the LAST element of ``split`` is always the
    # empty field after that terminator (or the sole one), never real data.
    # An unconditional drop is therefore always correct; a guard against the
    # "last field is not empty" case would be dead code no real git output
    # can produce.
    tokens = raw.split(b"\x00")[:-1]

    paths: set[str] = set()
    index = 0
    while index < len(tokens):
        record = tokens[index]
        index += 1
        status, path = record[:2], record[3:]
        paths.add(_decode_or_reject(path, "a path reported by git status -z"))
        if b"R" in status or b"C" in status:
            # A rename/copy record's OLD path is the next NUL-terminated
            # field — it no longer exists in the tree, so it is consumed
            # here and discarded rather than reported.
            index += 1

    untracked = _run_bytes(
        repo,
        "ls-files",
        "--others",
        "--exclude-per-directory=.gitignore",
        "-z",
        "--",
        remaining=remaining,
    )
    for path in untracked.split(b"\x00")[:-1]:
        paths.add(
            _decode_or_reject(
                path, "an untracked path reported by git ls-files -z"
            )
        )
    return tuple(sorted(paths))


# --------------------------------------------------------------------------
# P26 — four narrow, sanitized attestation Git helpers (A-211)
# --------------------------------------------------------------------------
#
# Each owns exactly one raw argv/exit/output interpretation for untrusted,
# externally-supplied attestation input. All four reuse this module's
# generic boundary (:func:`_run_raw`/:func:`_run_bounded`) -- including the
# mandatory ``--literal-pathspecs`` global option -- rather than launching a
# bespoke child. *remaining* is REQUIRED (never defaulted): every caller is
# lane-owned, never a legacy non-lane helper.


def verify_exact_commit(repo: Path, oid: str, *, remaining: Remaining) -> None:
    """Require that *oid* resolves to itself as an exact commit.

    Runs sanitized ``rev-parse --verify --end-of-options <oid>^{commit}`` and
    requires the sole stripped output to be byte-for-byte *oid* itself.
    Peeling an annotated tag object (or any non-commit that still resolves)
    changes the resolved OID, so it fails this identity check even though
    the underlying ``rev-parse`` exits zero -- an attestation's declared
    identity is never itself an indirection. Raises
    ``AssayError``/``GIT_FAILED`` on any resolution failure or mismatch; the
    caller (:mod:`assay.attestation`) remaps that to the untrusted-input
    terminal.
    """
    output = run(
        repo, "rev-parse", "--verify", "--end-of-options", f"{oid}^{{commit}}",
        remaining=remaining,
    ).strip()
    if output != oid:
        raise _git_failed(
            f"{oid!r} does not resolve to itself as an exact commit "
            f"(resolved to {output!r})"
        )


def is_ancestor(repo: Path, ancestor: str, descendant: str, *, remaining: Remaining) -> bool:
    """Whether *ancestor* is *descendant* or an ancestor of it.

    Interprets only ``merge-base --is-ancestor``'s own exit code: 0 is
    ``True``, 1 is ``False``, anything else is a typed Git failure -- an
    unrelated history or a malformed/unresolvable ref exits >1 empirically
    and is never silently read as "not an ancestor".
    """
    returncode, _, stderr = _run_raw(
        repo, "merge-base", "--is-ancestor", ancestor, descendant, remaining=remaining
    )
    if returncode == 0:
        return True
    if returncode == 1:
        return False
    raise _git_failed(
        f"git merge-base --is-ancestor failed ({returncode}): "
        f"{stderr.decode('utf-8', errors='replace').strip()[:200]}"
    )


def tree_entry_kind(repo: Path, commit: str, path: str, *, remaining: Remaining) -> str | None:
    """The exact object kind of *path* at *commit* -- ``"blob"``, ``"tree"``,
    or ``None`` when absent.

    Runs literal ``ls-tree -z <commit> -- <path>`` and parses ONE raw record:
    ``<mode> SP <type> SP <oid> TAB <path> NUL``. Empty output is absence
    (``None``); more than one record, a record whose path does not match
    *path* exactly, or a type other than ``blob``/``tree`` is a typed Git
    failure -- never a display-name parser, never pathspec expansion (the
    generic boundary's ``--literal-pathspecs`` is what makes a name
    containing ``*?[]`` or a literal newline an exact identity here, not a
    glob).
    """
    returncode, stdout, stderr = _run_raw(
        repo, "ls-tree", "-z", commit, "--", path, remaining=remaining
    )
    if returncode != 0:
        raise _git_failed(
            f"git ls-tree failed ({returncode}): "
            f"{stderr.decode('utf-8', errors='replace').strip()[:200]}"
        )
    if not stdout:
        return None
    records = [record for record in stdout.split(b"\x00") if record]
    if len(records) != 1:
        raise _git_failed(
            f"git ls-tree returned {len(records)} entries for a single exact "
            f"path; expected exactly one"
        )
    meta, sep, name = records[0].partition(b"\t")
    if not sep:
        raise _git_failed("git ls-tree returned a malformed entry")
    if _decode_or_reject(name, "an ls-tree path") != path:
        raise _git_failed(
            "git ls-tree returned a path that does not match the exact query"
        )
    fields = meta.split(b" ")
    if len(fields) != 3:
        raise _git_failed("git ls-tree returned malformed entry metadata")
    kind = _decode_or_reject(fields[1], "an ls-tree object type")
    if kind not in ("blob", "tree"):
        raise _git_failed(f"git ls-tree returned an unsupported object type {kind!r}")
    return kind


def path_is_current(
    repo: Path, before: str, after: str, path: str, *, remaining: Remaining
) -> bool:
    """Whether *path* is byte-identical between *before* and *after*.

    Runs literal ``diff --quiet --exit-code --no-ext-diff --no-textconv
    <before> <after> -- <path>`` (the ``--no-ext-diff``/``--no-textconv``
    pair is :func:`_prepare_subcommand_args`'s own existing, mandatory
    insertion for every ``diff`` invocation through this boundary). Exit 0 is
    ``True`` (current), exit 1 is ``False`` (changed); anything else is a
    typed Git failure.
    """
    returncode, _, stderr = _run_raw(
        repo, "diff", "--quiet", "--exit-code", before, after, "--", path,
        remaining=remaining,
    )
    if returncode == 0:
        return True
    if returncode == 1:
        return False
    raise _git_failed(
        f"git diff --quiet failed ({returncode}): "
        f"{stderr.decode('utf-8', errors='replace').strip()[:200]}"
    )


# --------------------------------------------------------------------------
# P22 — the private committed-object boundary
# --------------------------------------------------------------------------
#
# :mod:`assay.isolation` owns the snapshot abstraction but launches no
# process of its own: everything below is the extension of P20's single
# sanitized Git owner that P22 needs, and nothing here takes a
# caller-supplied environment or a caller-chosen executable. The additions
# over the P20 surface are exactly:
#
# * an explicit git-dir/work-tree pair the CALLER already knows (a private
#   seed or snapshot has no ``.git`` marker to discover, and asking git to
#   discover one would reintroduce the ambient identity A-173 removed);
# * bounded stdin, so an object-name list can be fed to plumbing without a
#   temporary file the consumer could observe;
# * a two-process streaming relay, so a pack crosses the boundary without
#   ever being buffered whole and its compressed bytes are counted by assay
#   rather than trusted from git;
# * one monotonic deadline per public call, whose expiry kills the process
#   group assay owns; and
# * the fixed replacement identity, which is product policy rather than a
#   parameter.

#: Disabled for every P22 object read (A-185). Both accelerate object lookup
#: by trusting a derived cache file; at this trust boundary the authoritative
#: answer must come from the objects themselves.
_P22_OBJECT_CONFIG: tuple[str, ...] = (
    "-c", "core.commitGraph=false",
    "-c", "core.multiPackIndex=false",
)

#: Added to :data:`_REPLACEMENT_ENV` for every P22 child (A-185). A partial
#: clone would otherwise satisfy a read by *fetching* it, which would make
#: the "complete reachable closure" claim depend on a network peer.
_P22_ENV_EXTRA: Mapping[str, str] = MappingProxyType({"GIT_NO_LAZY_FETCH": "1"})

#: The FIXED identity of every replacement child commit (A-186). Product
#: policy, never a parameter and never the ambient environment: the same
#: base/path/bytes must produce the same OID on any host, in any checkout,
#: under any consumer's configured author. ``946684800`` is 2000-01-01Z.
_P22_REPLACEMENT_IDENTITY: Mapping[str, str] = MappingProxyType(
    {
        "GIT_AUTHOR_NAME": "Assay",
        "GIT_AUTHOR_EMAIL": "assay@invalid",
        "GIT_COMMITTER_NAME": "Assay",
        "GIT_COMMITTER_EMAIL": "assay@invalid",
        "GIT_AUTHOR_DATE": "946684800 +0000",
        "GIT_COMMITTER_DATE": "946684800 +0000",
    }
)

#: The exact message bytes of every replacement child commit (A-186).
_P22_REPLACEMENT_MESSAGE: bytes = b"assay snapshot replacement\n"

#: How many relayed pack bytes may sit in this process at once. The pack is
#: streamed producer -> assay -> consumer so its size can be COUNTED against
#: a declared ceiling; holding the whole pack to do that would reintroduce
#: exactly the unbounded buffering the ceiling exists to prevent.
_P22_RELAY_CHUNK_BYTES = 1024 * 1024


def _p22_timeout(what: str) -> AssayError:
    return AssayError(
        f"the remaining lane budget expired while {what}",
        outcome=Outcome.BUDGET_EXCEEDED,
        reason_code=ReasonCode.LANE_TIMEOUT,
    )


def _p22_limit(message: str) -> AssayError:
    return AssayError(
        message,
        outcome=Outcome.BUDGET_EXCEEDED,
        reason_code=ReasonCode.SNAPSHOT_LIMIT_EXCEEDED,
    )


class _P22Deadline:
    """One monotonic deadline per public P22 call.

    The caller supplies *remaining lane seconds* (A-160: the budget belongs
    to the whole lane, not to each subprocess). Converting it once, here,
    is what stops a duration from being handed to child after child and
    silently multiplying into N x the declared budget.
    """

    __slots__ = ("_expiry",)

    def __init__(self, seconds: float) -> None:
        self._expiry = time.monotonic() + seconds

    def remaining(self, what: str) -> float:
        left = self._expiry - time.monotonic()
        if left <= 0:
            raise _p22_timeout(what)
        return left


def _p22_env(*, identity: bool) -> dict[str, str]:
    env = dict(_REPLACEMENT_ENV)
    env.update(_P22_ENV_EXTRA)
    if identity:
        env.update(_P22_REPLACEMENT_IDENTITY)
    return env


def _p22_argv(
    git_executable: Path,
    *,
    git_dir: Path,
    work_tree: Path | None,
    args: Sequence[str],
) -> list[str]:
    argv = [
        str(git_executable),
        "--no-pager",
        "--no-optional-locks",
        "--literal-pathspecs",
        f"--git-dir={git_dir}",
    ]
    if work_tree is not None:
        argv.append(f"--work-tree={work_tree}")
    argv.extend(_FIXED_CONFIG)
    argv.extend(_P22_OBJECT_CONFIG)
    argv.extend(_prepare_subcommand_args(args))
    return argv


def _p22_spawn(
    argv: Sequence[str], *, cwd: Path, identity: bool, stdin: int
) -> subprocess.Popen[bytes]:
    """Launch one P22 child in its OWN process group.

    ``start_new_session`` is what makes a deadline enforceable: git may fork
    helpers of its own, and killing only the direct child would leave those
    running with the pipe still open. Assay owns the whole group and kills
    the group.
    """
    return subprocess.Popen(
        list(argv),
        cwd=str(cwd),
        env=_p22_env(identity=identity),
        stdin=stdin,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        start_new_session=True,
    )


def _p22_kill(proc: subprocess.Popen[bytes] | None) -> None:
    if proc is None or proc.poll() is not None:
        return
    try:
        os.killpg(proc.pid, signal.SIGKILL)
    except (OSError, ProcessLookupError):  # pragma: no cover - race with exit
        proc.kill()


def _p22_pump(
    procs: Sequence[subprocess.Popen[bytes]],
    *,
    deadline: _P22Deadline,
    what: str,
    writers: Mapping[int, bytes],
    readers: Mapping[int, Callable[[bytes], None]],
) -> None:
    """Drive a set of P22 children to completion without deadlocking.

    *writers* maps a stdin file descriptor the pump OWNS (the caller
    duplicates it and closes the ``Popen`` wrapper, so closing it here to
    signal EOF can never race a reused descriptor number) to the bytes
    destined for it; *readers* maps a stdout/stderr descriptor to a sink.
    Every pipe is non-blocking and driven from one :mod:`selectors` loop,
    because any "write it all, then read it all" ordering deadlocks the
    moment either side exceeds a pipe buffer -- which an object-name list
    and a pack both do routinely.
    """
    pending = {fd: memoryview(data) for fd, data in writers.items()}
    selector = selectors.DefaultSelector()
    try:
        for fd in pending:
            os.set_blocking(fd, False)
            selector.register(fd, selectors.EVENT_WRITE)
        for fd in readers:
            os.set_blocking(fd, False)
            selector.register(fd, selectors.EVENT_READ)
        while selector.get_map():
            events = selector.select(timeout=deadline.remaining(what))
            if not events:
                raise _p22_timeout(what)
            for key, mask in events:
                fd = key.fd
                if mask & selectors.EVENT_WRITE:
                    buffer = pending[fd]
                    try:
                        written = os.write(fd, buffer[:_P22_RELAY_CHUNK_BYTES])
                    except BlockingIOError:
                        continue
                    except BrokenPipeError:
                        selector.unregister(fd)
                        os.close(fd)
                        del pending[fd]
                        continue
                    buffer = buffer[written:]
                    if buffer:
                        pending[fd] = buffer
                    else:
                        selector.unregister(fd)
                        os.close(fd)
                        del pending[fd]
                else:
                    chunk = os.read(fd, _P22_RELAY_CHUNK_BYTES)
                    if not chunk:
                        selector.unregister(fd)
                        continue
                    readers[fd](chunk)
    finally:
        selector.close()
        for fd in pending:
            try:
                os.close(fd)
            except OSError:  # pragma: no cover - already closed
                pass
    for proc in procs:
        try:
            proc.wait(timeout=deadline.remaining(what))
        except subprocess.TimeoutExpired as exc:
            _p22_kill(proc)
            raise _p22_timeout(what) from exc


def _p22_git(
    git_executable: Path,
    *,
    git_dir: Path,
    work_tree: Path | None,
    args: Sequence[str],
    deadline: _P22Deadline,
    cwd: Path,
    stdin_bytes: bytes = b"",
    identity: bool = False,
    on_stdout: Callable[[bytes], None] | None = None,
    max_stdout: int = MAX_GIT_OUTPUT_BYTES,
) -> bytes:
    """Run one P22 plumbing command against an EXPLICIT repository.

    Unlike :func:`_run_bytes` this never discovers a repository: a private
    seed or snapshot is created by assay and its git-dir is already known,
    so discovery could only reintroduce ambient identity. Output is either
    accumulated under *max_stdout* or streamed to *on_stdout*.
    """
    argv = _p22_argv(
        git_executable, git_dir=git_dir, work_tree=work_tree, args=args
    )
    what = f"running git {' '.join(args[:2])}"
    out = bytearray()
    err = bytearray()
    overflow: list[str] = []

    def take_stdout(chunk: bytes) -> None:
        if on_stdout is not None:
            on_stdout(chunk)
            return
        out.extend(chunk)
        if len(out) > max_stdout:
            overflow.append("standard output")
            raise _git_failed(
                f"git {' '.join(args[:2])} produced more than {max_stdout} "
                f"bytes on standard output; refusing to process an unbounded "
                f"amount of repository data from the child"
            )

    def take_stderr(chunk: bytes) -> None:
        remaining = _MAX_GIT_STDERR_BYTES - len(err)
        if remaining > 0:
            err.extend(chunk[:remaining])

    proc = _p22_spawn(
        argv, cwd=cwd, identity=identity, stdin=subprocess.PIPE
    )
    assert proc.stdin is not None and proc.stdout is not None
    assert proc.stderr is not None
    stdin_fd = os.dup(proc.stdin.fileno())
    proc.stdin.close()
    try:
        _p22_pump(
            [proc],
            deadline=deadline,
            what=what,
            writers={stdin_fd: stdin_bytes},
            readers={
                proc.stdout.fileno(): take_stdout,
                proc.stderr.fileno(): take_stderr,
            },
        )
    except BaseException:
        _p22_kill(proc)
        proc.wait()
        raise
    finally:
        proc.stdout.close()
        proc.stderr.close()
    if overflow:  # pragma: no cover - take_stdout already raised
        raise _git_failed(f"git {' '.join(args[:2])} overflowed {overflow[0]}")
    if proc.returncode != 0:
        raise _git_failed(
            f"git {' '.join(args)} failed in {git_dir} ({proc.returncode}): "
            f"{err.decode('utf-8', errors='replace').strip()[:200]}"
        )
    return bytes(out)


def _p22_stream_pack(
    git_executable: Path,
    *,
    source_git_dir: Path,
    source_work_tree: Path,
    source_cwd: Path,
    seed_git_dir: Path,
    oid_payload: bytes,
    deadline: _P22Deadline,
    max_pack_bytes: int,
) -> int:
    """Stream one exact pack from source ``pack-objects`` into private
    ``index-pack``, counting its compressed bytes in transit.

    ``pack-objects`` runs WITHOUT ``--revs`` (A-185): it packs exactly the
    frozen object names on its stdin and performs no reachability
    traversal of its own, so the transferred set cannot quietly differ from
    the set assay inventoried and bounded. Assay relays the bytes rather
    than connecting the two pipes directly, because a ceiling git enforces
    is a ceiling assay has not verified.
    """
    producer_argv = _p22_argv(
        git_executable,
        git_dir=source_git_dir,
        work_tree=source_work_tree,
        args=("pack-objects", "--stdout"),
    )
    consumer_argv = _p22_argv(
        git_executable,
        git_dir=seed_git_dir,
        work_tree=None,
        args=("index-pack", "--stdin"),
    )
    producer = _p22_spawn(
        producer_argv, cwd=source_cwd, identity=False, stdin=subprocess.PIPE
    )
    consumer: subprocess.Popen[bytes] | None = None
    counted = 0
    producer_err = bytearray()
    consumer_err = bytearray()
    try:
        consumer = _p22_spawn(
            consumer_argv, cwd=seed_git_dir, identity=False, stdin=subprocess.PIPE
        )
        assert producer.stdin is not None and producer.stdout is not None
        assert producer.stderr is not None
        assert consumer.stdin is not None and consumer.stdout is not None
        assert consumer.stderr is not None
        producer_stdin = os.dup(producer.stdin.fileno())
        producer.stdin.close()
        consumer_stdin = consumer.stdin.fileno()

        def relay(chunk: bytes) -> None:
            nonlocal counted
            counted += len(chunk)
            if counted > max_pack_bytes:
                raise _p22_limit(
                    f"the transferred pack exceeded max_pack_bytes "
                    f"({max_pack_bytes}); refusing a partial snapshot"
                )
            view = memoryview(chunk)
            while view:
                written = os.write(consumer_stdin, view[:_P22_RELAY_CHUNK_BYTES])
                view = view[written:]

        def take(sink: bytearray) -> Callable[[bytes], None]:
            def take_chunk(chunk: bytes) -> None:
                remaining = _MAX_GIT_STDERR_BYTES - len(sink)
                if remaining > 0:
                    sink.extend(chunk[:remaining])

            return take_chunk

        os.set_blocking(consumer_stdin, True)
        _p22_pump(
            [producer],
            deadline=deadline,
            what="transferring the committed-object pack",
            writers={producer_stdin: oid_payload},
            readers={
                producer.stdout.fileno(): relay,
                producer.stderr.fileno(): take(producer_err),
            },
        )
        consumer.stdin.close()
        _p22_pump(
            [consumer],
            deadline=deadline,
            what="indexing the committed-object pack",
            writers={},
            readers={
                consumer.stdout.fileno(): lambda _chunk: None,
                consumer.stderr.fileno(): take(consumer_err),
            },
        )
    except BaseException:
        _p22_kill(producer)
        _p22_kill(consumer)
        producer.wait()
        if consumer is not None:
            consumer.wait()
        raise
    finally:
        producer.stdout.close()
        producer.stderr.close()
        if consumer is not None:
            if consumer.stdin is not None and not consumer.stdin.closed:
                consumer.stdin.close()
            consumer.stdout.close()
            consumer.stderr.close()
    if producer.returncode != 0:
        raise _git_failed(
            f"git pack-objects failed reading {source_git_dir} "
            f"({producer.returncode}): "
            f"{producer_err.decode('utf-8', errors='replace').strip()[:200]}"
        )
    if consumer.returncode != 0:
        raise _git_failed(
            f"git index-pack failed writing {seed_git_dir} "
            f"({consumer.returncode}): "
            f"{consumer_err.decode('utf-8', errors='replace').strip()[:200]}"
        )
    return counted


def _p22_init_private(
    git_executable: Path,
    *,
    path: Path,
    template: Path,
    bare: bool,
    deadline: _P22Deadline,
) -> None:
    """Create one private repository with an EMPTY template.

    git's stock template installs sample hooks. They are inert, but a
    snapshot that ships executable files assay never chose is not the
    "no consumer-controlled program" claim O2 makes, so the template is
    replaced by an empty directory rather than cleaned up afterwards.
    """
    args = ["init", "-q", f"--template={template}"]
    if bare:
        args.append("--bare")
    args.append(str(path))
    argv = [
        str(git_executable),
        "--no-pager",
        "--no-optional-locks",
        "--literal-pathspecs",
        *_FIXED_CONFIG,
        *_P22_OBJECT_CONFIG,
        *args,
    ]
    proc = _p22_spawn(
        argv, cwd=template, identity=False, stdin=subprocess.DEVNULL
    )
    assert proc.stdout is not None and proc.stderr is not None
    err = bytearray()
    try:
        _p22_pump(
            [proc],
            deadline=deadline,
            what="initializing a private repository",
            writers={},
            readers={
                proc.stdout.fileno(): lambda _chunk: None,
                proc.stderr.fileno(): err.extend,
            },
        )
    except BaseException:
        _p22_kill(proc)
        proc.wait()
        raise
    finally:
        proc.stdout.close()
        proc.stderr.close()
    if proc.returncode != 0:
        raise _git_failed(
            f"git init failed for {path} ({proc.returncode}): "
            f"{err.decode('utf-8', errors='replace').strip()[:200]}"
        )


@dataclass(frozen=True, kw_only=True)
class _P22Source:
    """A consumer repository proven safe to read objects from.

    ``git_dir`` is what P20 anchors commands to; ``common_dir`` is where the
    OBJECTS actually live. For a linked worktree the two differ (the former
    is ``<main>/.git/worktrees/<name>``), and every topology question below
    is a property of the common directory -- checking the per-worktree one
    would inspect a directory that holds no object store at all.
    """

    git_executable: Path
    repo_top: Path
    git_dir: Path
    common_dir: Path


def _p22_reject_external_topology(source: _P22Source, deadline: _P22Deadline) -> None:
    """Refuse every source topology whose bytes are not fully local (A-185).

    Clearing ambient ``GIT_ALTERNATE_OBJECT_DIRECTORIES`` -- which
    :data:`_REPLACEMENT_ENV` already does -- is NOT sufficient: a local
    ``objects/info/alternates`` file is repository CONTENT and still
    participates, so a "complete" closure could be assembled from bytes that
    live outside the recorded source identity. Shallow, grafted, and
    partial/promisor repositories each redefine what "reachable" means, and a
    non-SHA-1 store cannot satisfy the raw 20-byte tree grammar at all.
    """
    common = source.common_dir
    alternates = common / "objects" / "info" / "alternates"
    try:
        marker = alternates.lstat()
    except OSError:
        marker = None
    if marker is not None:
        if stat_module.S_ISLNK(marker.st_mode) or not stat_module.S_ISREG(marker.st_mode):
            raise _git_failed(
                f"{alternates} is not a regular file; refusing to read objects "
                f"through an external store"
            )
        if alternates.read_bytes().strip():
            raise _git_failed(
                f"{alternates} names an external object store; refusing a "
                f"source closure whose bytes are not fully local"
            )
    for name in ("info/grafts", "shallow"):
        if (common / name).exists():
            raise _git_failed(
                f"{common / name} exists; refusing a grafted or shallow "
                f"source whose reachable history is redefined"
            )
    object_format = _p22_git(
        source.git_executable,
        git_dir=source.git_dir,
        work_tree=source.repo_top,
        args=("rev-parse", "--show-object-format"),
        deadline=deadline,
        cwd=source.repo_top,
    )
    if _decode_or_reject(object_format, "the source object format").strip() != "sha1":
        raise _git_failed(
            "the source repository does not use the SHA-1 object format; "
            "P22's raw tree grammar and 20-byte object names do not apply"
        )
    config = _p22_git(
        source.git_executable,
        git_dir=source.git_dir,
        work_tree=source.repo_top,
        args=("config", "--list", "-z"),
        deadline=deadline,
        cwd=source.repo_top,
    )
    for record in config.split(b"\x00"):
        if not record:
            continue
        key, _, value = record.partition(b"\n")
        name = _decode_or_reject(key, "a source config key").lower()
        if name.startswith("extensions.partialclone"):
            raise _git_failed(
                "the source repository declares extensions.partialClone; "
                "refusing a partial clone whose objects may be fetched on read"
            )
        if name.startswith("remote.") and name.endswith(".promisor"):
            if _decode_or_reject(value, "a source config value").strip().lower() == "true":
                raise _git_failed(
                    f"the source repository declares a promisor remote ({name}); "
                    f"refusing a partial clone whose objects may be fetched on read"
                )


def _p22_open_source(repo_top: Path, deadline: _P22Deadline) -> _P22Source:
    """Resolve and validate the consumer repository P22 reads objects from."""
    git_executable = _resolve_git_executable()
    resolved = _resolve_repo(Path(repo_top), git_executable)
    raw_common = _p22_git(
        git_executable,
        git_dir=resolved.git_dir,
        work_tree=resolved.repo_top,
        args=("rev-parse", "--path-format=absolute", "--git-common-dir"),
        deadline=deadline,
        cwd=resolved.repo_top,
    )
    common_text = _decode_or_reject(raw_common, "the resolved common git directory").strip()
    common_dir = Path(common_text)
    if not common_dir.is_absolute() or not common_dir.is_dir():
        raise _git_failed(
            f"resolved common git-dir {common_text!r} for {resolved.repo_top} "
            f"is not an absolute, existing directory"
        )
    source = _P22Source(
        git_executable=git_executable,
        repo_top=resolved.repo_top,
        git_dir=resolved.git_dir,
        common_dir=common_dir,
    )
    _p22_reject_external_topology(source, deadline)
    return source
