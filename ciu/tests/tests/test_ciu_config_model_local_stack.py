"""
Tests for V8-PREP-4 — ``local_stack`` recognized as a preferred stack root
key name (additive groundwork, ciu-P21).

Normative contract: docs/SPEC.md S3.5/S3.7, docs/CONFIG.md `[<root>]`.

Covers:
- O2: `validate_stack_shape` accepts `local_stack` as a root key, returned
  exactly like any other valid root key; `local_stack` is NOT a member of
  `RESERVED_GLOBAL_NAMESPACES` or `RESERVED_GLOBAL_TABLES`; the existing
  S3.5 "exactly one non-state top-level key" invariant is unchanged.
- O3: downstream readers of the returned root key (secret discovery,
  misplaced-directive check, configfile render) accept `local_stack`
  identically to any other name, because they take it as an opaque
  parameter; a stack with a conventional directory-derived root key is
  completely unaffected (no regression).
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))

from ciu.composefile import render_configfiles  # noqa: E402
from ciu.config_model import (  # noqa: E402
    RESERVED_GLOBAL_NAMESPACES,
    RESERVED_GLOBAL_TABLES,
    validate_stack_shape,
)
from ciu.secrets.directives import discover, find_misplaced  # noqa: E402


# ---------------------------------------------------------------------------
# O2: validate_stack_shape accepts local_stack as a recognized root key
# ---------------------------------------------------------------------------


def test_local_stack_root_key_accepted_and_returned():
    cfg = {"local_stack": {"postgres": {"port": 5432}}}
    assert validate_stack_shape(cfg) == "local_stack"


def test_local_stack_root_plus_state_still_exactly_one_key():
    """S3.5 invariant unchanged: local_stack is still exactly ONE
    non-reserved top-level key even with [state] present."""
    cfg = {"local_stack": {"postgres": {}}, "state": {"initialized": True}}
    assert validate_stack_shape(cfg) == "local_stack"


def test_local_stack_alongside_another_root_key_still_raises_s3_5():
    """S3.5 is unchanged: TWO non-reserved top-level keys is still an error,
    even when one of them is local_stack."""
    cfg = {"local_stack": {}, "other_root": {}}
    with pytest.raises(ValueError, match=r"\[S3\.5\]"):
        validate_stack_shape(cfg)


def test_local_stack_not_in_reserved_global_namespaces():
    """local_stack must NOT be forbidden (S3.7) — the opposite of what
    'preferred' means."""
    assert "local_stack" not in RESERVED_GLOBAL_NAMESPACES


def test_local_stack_not_in_reserved_global_tables():
    """local_stack is a stack-scope concept, not a global one (S3.13)."""
    assert "local_stack" not in RESERVED_GLOBAL_TABLES


def test_local_stack_non_table_root_still_rejected():
    """S3.5's TOML-table-shape check applies to local_stack identically to
    any other root key name — no special-casing."""
    cfg = {"local_stack": "not-a-table"}
    with pytest.raises(ValueError, match=r"\[S3\.5\].*str"):
        validate_stack_shape(cfg)


# ---------------------------------------------------------------------------
# O3: a conventional directory-derived root key is completely unaffected
# ---------------------------------------------------------------------------


def test_conventional_root_key_unaffected_by_local_stack_recognition():
    cfg = {"db_core": {"postgres": {"port": 5432}}}
    assert validate_stack_shape(cfg) == "db_core"


def test_conventional_root_key_still_hits_s3_7_collision_unchanged():
    """A stack that does NOT rename to local_stack still hits the existing
    S3.7 collision check exactly as before this package."""
    cfg = {"vault": {}}
    with pytest.raises(ValueError, match=r"\[S3\.7\]"):
        validate_stack_shape(cfg)


# ---------------------------------------------------------------------------
# O3: downstream readers treat the returned root key as an opaque parameter
# ---------------------------------------------------------------------------


def test_secret_discover_accepts_local_stack_root_key():
    """secret_directives.discover works with local_stack exactly like any
    other stack root key — proving it never special-cases the string."""
    config = {
        "local_stack": {
            "postgres": {
                "secrets": {
                    "postgres_pw": "GEN_TO_VAULT:shared/postgres_password",
                }
            }
        }
    }
    root_key = validate_stack_shape(config)
    specs = discover(root_key, config)
    assert len(specs) == 1
    assert specs[0].name == "postgres_pw"
    assert specs[0].table_path == "local_stack.postgres.secrets"


def test_secret_discover_local_stack_matches_conventional_root_key_shape():
    """Identical secrets-table shape under a conventional root key produces
    the identical SecretSpec shape — local_stack is not a distinct code
    path, just a different string value."""
    conventional = {
        "db_core": {
            "postgres": {
                "secrets": {"postgres_pw": "GEN_TO_VAULT:shared/postgres_password"}
            }
        }
    }
    local_stack = {
        "local_stack": {
            "postgres": {
                "secrets": {"postgres_pw": "GEN_TO_VAULT:shared/postgres_password"}
            }
        }
    }
    conv_specs = discover("db_core", conventional)
    ls_specs = discover("local_stack", local_stack)
    assert (conv_specs[0].name, conv_specs[0].kind) == (ls_specs[0].name, ls_specs[0].kind)


def test_find_misplaced_accepts_local_stack_root_key():
    """find_misplaced's stack_root_key parameter is opaque too: a secrets
    table properly nested under local_stack is not flagged."""
    config = {
        "local_stack": {
            "secrets": {"pw": "GEN_TO_VAULT:shared/pw"},
        }
    }
    assert find_misplaced(config, stack_root_key="local_stack") == []


def test_find_misplaced_flags_secrets_table_outside_local_stack():
    """A secrets table OUTSIDE local_stack is still flagged, exactly as it
    would be for any other stack_root_key value."""
    config = {
        "local_stack": {},
        "elsewhere": {"secrets": {"pw": "GEN_TO_VAULT:shared/pw"}},
    }
    violations = find_misplaced(config, stack_root_key="local_stack")
    assert any(path == "elsewhere.secrets" for path, _ in violations)


def test_render_configfiles_accepts_local_stack_root_key(tmp_path):
    """composefile's configfile discovery takes root_key as a plain dict
    lookup key — local_stack works identically to any other name."""
    assert render_configfiles(tmp_path, "local_stack", {"local_stack": {}}, lambda _: "x") == []
