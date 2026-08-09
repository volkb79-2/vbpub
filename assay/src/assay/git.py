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
import shutil
import stat as stat_module
import subprocess
from dataclasses import dataclass
from pathlib import Path
from types import MappingProxyType
from typing import Mapping, Sequence

from .errors import AssayError, Outcome, ReasonCode

__all__ = [
    "dirty_paths",
    "head_rev",
    "repo_top",
    "resolve_base",
    "run",
]

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
_FIXED_CONFIG: tuple[str, ...] = (
    "-c", "core.quotePath=false",
    "-c", "core.hooksPath=/dev/null",
    "-c", "core.fsmonitor=",
    "-c", "commit.gpgSign=false",
)


def _git_failed(message: str) -> AssayError:
    return AssayError(message, outcome=Outcome.ERROR, reason_code=ReasonCode.GIT_FAILED)


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


def _resolve_repo(repo: Path, git_executable: Path) -> _ResolvedRepo:
    """Resolve *repo*'s trusted work-tree root and real git directory
    (A-173). The bootstrap ``rev-parse --absolute-git-dir`` call runs with
    the SAME sanitized :data:`_REPLACEMENT_ENV` (so no ambient ``GIT_DIR``
    can redirect it either) from ``-C <repo_top>`` — a location OUR OWN walk
    already trusts — but deliberately without ``--git-dir``/``--work-tree``,
    since resolving the git-dir correctly (including a linked-worktree
    gitfile redirect) is precisely what this call is for.
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
    proc = subprocess.run(argv, env=dict(_REPLACEMENT_ENV), capture_output=True)
    if proc.returncode != 0:
        raise _git_failed(
            f"git rev-parse --absolute-git-dir failed resolving {repo_top} "
            f"({proc.returncode}): {proc.stderr.decode('utf-8', errors='replace').strip()[:200]}"
        )
    git_dir_text = _decode_or_reject(proc.stdout, "the resolved git directory").strip()
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


def run(repo: Path, *args: str) -> str:
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
    """
    return _decode_or_reject(
        _run_bytes(repo, *args), f"the output of git {' '.join(args)}"
    )


def _run_bytes(repo: Path, *args: str) -> bytes:
    """Run a git command and return its raw stdout bytes, undecoded.

    Every caller in this module reaches git through here. Raw bytes are the
    honest boundary: what git writes is a byte stream, and both the *what
    encoding* question (:func:`_decode_or_reject`) and the *where do records
    end* question (``-z`` in :func:`dirty_paths`) are then answered
    deliberately, in one place, instead of by ``subprocess``' defaults.

    The executable and repository identity are resolved fresh on every call
    (A-173): no cross-call cache to go stale, and every command is anchored
    with explicit ``--git-dir``/``--work-tree`` — the only pair that survives
    a repository-local ``core.worktree`` redirect (verified empirically; a
    command-line ``-c core.worktree=...`` override does not).
    """
    git_executable = _resolve_git_executable()
    resolved = _resolve_repo(Path(repo), git_executable)
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
    proc = subprocess.run(argv, env=dict(_REPLACEMENT_ENV), capture_output=True)
    if proc.returncode != 0:
        raise AssayError(
            f"git {' '.join(args)} failed ({proc.returncode}): "
            f"{proc.stderr.decode('utf-8', errors='replace').strip()[:200]}",
            outcome=Outcome.ERROR,
            reason_code=ReasonCode.GIT_FAILED,
        )
    return proc.stdout


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


def repo_top(repo: Path) -> Path:
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
    return _resolve_repo(Path(repo), git_executable).repo_top


def head_rev(repo: Path) -> str:
    """The full SHA of ``HEAD``."""
    return run(repo, "rev-parse", "HEAD").strip()


def _resolve_revision(repo: Path, revision: str) -> str:
    """Validate and resolve *revision* (a caller/lane-declared string, never
    assumed to already be safe) to its full commit OID before it is used in
    any further command (A-173's "user-controlled revisions are first
    validated/resolved to full OIDs"). ``--end-of-options`` stops a
    revision spelling that happens to start with ``-`` from ever being
    parsed as a flag by the command that resolves it; the returned 40-hex
    OID cannot be mistaken for one either.
    """
    return run(repo, "rev-parse", "--verify", "--end-of-options", f"{revision}^{{commit}}").strip()


def resolve_base(repo: Path, base: str) -> str:
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
    tokens = run(repo, "rev-list", "--parents", "-n", "1", "HEAD").split()
    if len(tokens) >= 3:  # HEAD sha + >=2 parent shas
        return tokens[1]
    resolved_base = _resolve_revision(repo, base)
    return run(repo, "merge-base", "--end-of-options", resolved_base, "HEAD").strip()


def dirty_paths(repo: Path) -> tuple[str, ...]:
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
    """
    raw = _run_bytes(repo, "status", "--porcelain=v1", "-z")
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
    return tuple(sorted(paths))
