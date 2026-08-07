"""The thin git subprocess boundary.

Every other module in this package that needs git talks to it through here —
one function that shells out (:func:`_run_bytes`, a raw-bytes boundary),
:func:`run` which decodes its output, and a handful of thin wrappers over
specific invocations. Nothing above this module ever builds a
``["git", ...]`` argv itself.

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

import subprocess
from pathlib import Path

from .errors import AssayError, Outcome, ReasonCode

__all__ = [
    "dirty_paths",
    "head_rev",
    "repo_top",
    "resolve_base",
    "run",
]

_QUOTE_PATH_OFF = ("-c", "core.quotePath=false")


def run(repo: Path, *args: str) -> str:
    """Run ``git -c core.quotePath=false -C <repo> <args>`` and return its
    stdout decoded as UTF-8.

    *repo* may be any directory inside the working tree — git itself resolves
    the repository from there, same as every wrapper in this module. Raises
    :class:`AssayError` (``ERROR`` / ``GIT_FAILED``) on a non-zero exit; the
    message carries the argv and the first 200 characters of stderr, matching
    the cited implementations. Output that is not valid UTF-8 raises the same
    typed error rather than a bare ``UnicodeDecodeError`` — see
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
    """
    proc = subprocess.run(
        ["git", *_QUOTE_PATH_OFF, "-C", str(repo), *args],
        capture_output=True,
    )
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
    regardless of which subdirectory this process was pointed at."""
    return Path(run(repo, "rev-parse", "--show-toplevel").strip()).resolve()


def head_rev(repo: Path) -> str:
    """The full SHA of ``HEAD``."""
    return run(repo, "rev-parse", "HEAD").strip()


def resolve_base(repo: Path, base: str) -> str:
    """Resolve *base* against the shape of ``HEAD``.

    A merge commit (``HEAD`` has two or more parents) resolves to its first
    parent — the merged-into branch's tip immediately before the merge, so the
    delta measured is the merge's own payload. Any other ``HEAD`` resolves to
    ``merge-base(base, HEAD)`` — the fork point, so a feature branch is judged
    against what it actually diverged from rather than *base*'s current tip
    (which may have moved since).
    """
    tokens = run(repo, "rev-list", "--parents", "-n", "1", "HEAD").split()
    if len(tokens) >= 3:  # HEAD sha + >=2 parent shas
        return tokens[1]
    return run(repo, "merge-base", base, "HEAD").strip()


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
