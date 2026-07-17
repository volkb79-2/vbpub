"""Guard: no Role defined-but-never-dispatched (P43).

Statically scans daemon.py + reconcile.py source text for dispatch sites
(`role=Role.<NAME>`) and asserts every Role member is either dispatched or
explicitly reserved+tracked in RESERVED_ROLES — no silent stubs. A reserved
role must also cite a backlog item that really exists, so reserving is TRACKED
deferral rather than the same silent stub under a new name.
"""

from __future__ import annotations

import re
from pathlib import Path

from nyxloom.types import RESERVED_ROLES, Role

REPO_ROOT = Path(__file__).resolve().parent.parent
DAEMON_SRC = (REPO_ROOT / "src" / "nyxloom" / "daemon.py").read_text()
RECONCILE_SRC = (REPO_ROOT / "src" / "nyxloom" / "reconcile.py").read_text()
TYPES_SRC = (REPO_ROOT / "src" / "nyxloom" / "types.py").read_text()
# PACKAGE F1 (2026-07-17): nyxloom's own backlog.md was git-mv'd to the
# numeric-prefixed 4-backlog.md (docs/spine-documents-spec.md). The literal
# 'nyxloom-trove/backlog.md' string below is a comment TAG inside types.py's
# RESERVED_ROLES block (frozen for this package), not a resolved path -- it
# stays unchanged; only the actual file read here follows the rename.
BACKLOG_SRC = (REPO_ROOT / "nyxloom-trove" / "4-backlog.md").read_text()

_DISPATCH_RE = re.compile(r"role=Role\.(\w+)")
_RESERVED_BLOCK_RE = re.compile(r"^RESERVED_ROLES.*?^\}\)", re.M | re.S)
_RESERVED_REF_RE = re.compile(r"Role\.(\w+),\s*#\s*nyxloom-trove/backlog\.md:\s*(\S+)")


def _dispatched_roles() -> set[Role]:
    names = set(_DISPATCH_RE.findall(DAEMON_SRC)) | set(_DISPATCH_RE.findall(RECONCILE_SRC))
    return {Role[name] for name in names}


def _reserved_backlog_refs() -> dict[Role, str]:
    """Map each RESERVED_ROLES member to the backlog id its comment cites."""
    block = _RESERVED_BLOCK_RE.search(TYPES_SRC)
    assert block is not None, "could not locate the RESERVED_ROLES block in types.py"
    return {Role[name]: ref for name, ref in _RESERVED_REF_RE.findall(block.group(0))}


def test_every_role_is_dispatched_or_reserved():
    dispatched = _dispatched_roles()
    for role in Role:
        assert role in dispatched or role in RESERVED_ROLES, (
            f"{role} is neither dispatched (role=Role.{role.name} in "
            f"daemon.py/reconcile.py) nor in RESERVED_ROLES — silent stub"
        )


def test_reserved_and_dispatched_roles_are_disjoint():
    assert _dispatched_roles().isdisjoint(RESERVED_ROLES)


def test_self_review_is_reserved_and_not_dispatched():
    """Non-hollow anchor: proves the scan finds SELF_REVIEW absent from
    dispatch sites, not just that RESERVED_ROLES is a subset of Role."""
    assert Role.SELF_REVIEW not in _dispatched_roles()
    assert Role.SELF_REVIEW in RESERVED_ROLES


def test_implementer_is_dispatched_and_not_reserved():
    """Non-hollow anchor: proves the scan finds a real dispatch site, not
    just that RESERVED_ROLES is disjoint from Role by construction."""
    assert Role.IMPLEMENTER in _dispatched_roles()
    assert Role.IMPLEMENTER not in RESERVED_ROLES


def test_every_reserved_role_cites_a_live_backlog_item():
    """Reserving is only legitimate if it is tracked: without this, a role can
    be parked in RESERVED_ROLES with no backlog ref and stay green forever —
    the original silent stub, relabelled."""
    refs = _reserved_backlog_refs()
    assert set(refs) == set(RESERVED_ROLES), (
        "every RESERVED_ROLES member needs a trailing "
        "`# nyxloom-trove/backlog.md: <id>` comment; missing: "
        f"{{{', '.join(sorted(r.name for r in set(RESERVED_ROLES) - set(refs)))}}}"
    )
    for role, backlog_id in refs.items():
        assert re.search(rf"^- \*\*{re.escape(backlog_id)}\b", BACKLOG_SRC, re.M), (
            f"{role} cites backlog item {backlog_id!r}, but backlog.md has no "
            f"such item — reserved-but-untracked is still a silent stub"
        )


def test_self_review_cites_the_self_review_leg_backlog_item():
    """Non-hollow anchor: proves the ref scan reads the real comment and the
    real backlog file, rather than passing on an empty ref set."""
    assert _reserved_backlog_refs()[Role.SELF_REVIEW] == "B-self-review-leg"
    assert re.search(r"^- \*\*B-self-review-leg\b", BACKLOG_SRC, re.M)
