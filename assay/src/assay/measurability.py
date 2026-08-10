"""The two changed-line measurability guards: ``DIRTY_TREE`` and
``BASE_IS_HEAD``.

DESIGN-GUIDE §6's "Nailing NO MEASUREMENT" table names three causes that all
render the same ``NO_MEASUREMENT`` outcome but are three different facts:
uncommitted changes under the source roots (tree state), a resolved base that
equals ``HEAD`` (ref resolution), and a well-formed coverage artifact with
zero measured files (the measurement itself was vacuous). This module owns
the first two. The third, ``EMPTY_COVERAGE``, is P03's — it is about the
coverage *artifact*, not the tree or the ref, and this module never reads one.

Both guards here **raise** :class:`~assay.errors.AssayError` on the adverse
case and return a typed value on the clear case (A-091: no new exception type
is defined in this file). A genuinely empty delta on a clean, committed tree
(a docs-only or test-only commit) trips neither guard and is expected to reach
evaluation normally — that legitimate 0/0 pass is P05's decision, not this
module's, so this module does no diff parsing of its own.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Sequence

from . import git
from .errors import AssayError, Outcome, ReasonCode

__all__ = ["ResolvedBase", "check_base_is_head", "check_dirty_tree"]


@dataclass(frozen=True, kw_only=True)
class ResolvedBase:
    """What clearing the ``BASE_IS_HEAD`` guard produces.

    Carries both revisions the guard already resolved, so a caller (P05) never
    has to re-run :func:`assay.git.resolve_base`/:func:`assay.git.head_rev` to
    get the same answer a second time.
    """

    base_rev: str
    head_rev: str


def check_dirty_tree(
    repo: Path, source_roots: Sequence[Path], *, remaining: git.Remaining | None = None
) -> None:
    """Raise ``NO_MEASUREMENT`` / ``DIRTY_TREE`` if anything under
    *source_roots* is staged, unstaged, or untracked; otherwise return
    ``None``.

    A ``base..HEAD`` diff is committed-to-committed, so an uncommitted change
    under a source root is invisible to it: "0 changed lines" from that diff
    would mean "the diff cannot see what is actually being tested", not
    "nothing changed" (DESIGN-GUIDE §6).

    *source_roots* must already be resolved, existing, absolute directories —
    :attr:`assay.config.JudgeConfig.source_root_paths`'s own contract. This
    function trusts that and does no filesystem validation of its own.

    Membership is decided by **resolved filesystem path**
    (:meth:`pathlib.Path.is_relative_to`), never by string prefix:
    ``git status --porcelain`` paths are relative to the repository's top
    level (:func:`assay.git.repo_top` converts them to absolute), so a sibling
    directory whose name merely starts with a source root's name — ``src/foo``
    vs. ``src/foo_evil`` — is never mistaken for a change inside the root. A
    string-prefix check (``str(path).startswith(str(root))``) gets exactly
    this case wrong, because ``"...src/foo_evil"`` does start with
    ``"...src/foo"``.
    """
    top = git.repo_top(repo, remaining=remaining)
    dirty = sorted(
        rel
        for rel in git.dirty_paths(repo, remaining=remaining)
        if any((top / rel).resolve().is_relative_to(root) for root in source_roots)
    )
    if dirty:
        roots = ", ".join(str(root) for root in source_roots)
        raise AssayError(
            f"{len(dirty)} uncommitted file(s) under {roots} — the gate "
            f"diffs committed HEAD, so these are invisible to it. Commit or "
            f"stash, then re-run. Affected: {', '.join(dirty)}",
            outcome=Outcome.NO_MEASUREMENT,
            reason_code=ReasonCode.DIRTY_TREE,
        )


def check_base_is_head(
    repo: Path, base: str, *, remaining: git.Remaining | None = None
) -> ResolvedBase:
    """Resolve *base* (:func:`assay.git.resolve_base`) and raise
    ``NO_MEASUREMENT`` / ``BASE_IS_HEAD`` if it equals ``HEAD``; otherwise
    return both revisions as a :class:`ResolvedBase`.

    A resolved base identical to ``HEAD`` means there is no delta between the
    two sides being diffed, by construction — this is what ``--base main``
    resolving TO ``main`` itself produces — so any percentage computed from it
    is vacuous regardless of what it says (DESIGN-GUIDE §6). This check runs
    before any diff is parsed, so a vacuous base never reaches
    :func:`assay.diff.parse_added_lines` at all.
    """
    head = git.head_rev(repo, remaining=remaining)
    resolved = git.resolve_base(repo, base, remaining=remaining)
    if resolved == head:
        raise AssayError(
            f"resolved base ({resolved[:12]}) IS HEAD ({head[:12]}) — there "
            f"is no delta to measure. base should resolve to an ancestor of "
            f"HEAD; check that HEAD is not already the tip of that ref.",
            outcome=Outcome.NO_MEASUREMENT,
            reason_code=ReasonCode.BASE_IS_HEAD,
        )
    return ResolvedBase(base_rev=resolved, head_rev=head)
