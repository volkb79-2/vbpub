"""Guard: no Role defined-but-never-dispatched (P43).

Statically scans daemon.py + reconcile.py source text for dispatch sites
(`role=Role.<NAME>`) and asserts every Role member is either dispatched or
explicitly reserved+tracked in RESERVED_ROLES — no silent stubs.
"""

from __future__ import annotations

import re
from pathlib import Path

from nyxloom.types import RESERVED_ROLES, Role

REPO_ROOT = Path(__file__).resolve().parent.parent
DAEMON_SRC = (REPO_ROOT / "src" / "nyxloom" / "daemon.py").read_text()
RECONCILE_SRC = (REPO_ROOT / "src" / "nyxloom" / "reconcile.py").read_text()

_DISPATCH_RE = re.compile(r"role=Role\.(\w+)")


def _dispatched_roles() -> set[Role]:
    names = set(_DISPATCH_RE.findall(DAEMON_SRC)) | set(_DISPATCH_RE.findall(RECONCILE_SRC))
    return {Role[name] for name in names}


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
