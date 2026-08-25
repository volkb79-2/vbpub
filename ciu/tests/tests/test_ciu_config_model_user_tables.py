"""
Tests for V8-PREP-1 — ``ciu.user_tables`` declaration-gated global namespace
check (additive groundwork, ciu-P21).

Normative contract: docs/SPEC.md S3.13, docs/CONFIG.md `[ciu]`.

Covers:
- O1: `RESERVED_GLOBAL_TABLES` is a distinct frozenset from
  `RESERVED_GLOBAL_NAMESPACES`; `validate_user_tables` is a no-op when
  `ciu.user_tables` is absent; when present, an unlisted top-level key is a
  single collective ValueError naming every offending key; wired into
  `render_global_chain` immediately beside `_validate_deploy_landscape_id`,
  validated on the FINAL merged config (committed chain + worktree overlay).
- O3: regression bar (absent declaration -> any top-level keys pass
  unchanged), valid declaration, RESERVED_GLOBAL_TABLES collision, and every
  malformed-declaration shape (non-list, non-string member, bad-charset
  member, duplicate).
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))

from ciu.config_model import (  # noqa: E402
    RESERVED_GLOBAL_NAMESPACES,
    RESERVED_GLOBAL_TABLES,
    render_global_chain,
    validate_user_tables,
)


def _write_global_defaults(directory: Path, content: str) -> None:
    (directory / "ciu.global.defaults.toml.j2").write_text(content, encoding="utf-8")


# ---------------------------------------------------------------------------
# O1: RESERVED_GLOBAL_TABLES is distinct from RESERVED_GLOBAL_NAMESPACES
# ---------------------------------------------------------------------------


def test_reserved_global_tables_is_a_distinct_object_from_namespaces():
    """DISTINCT frozenset, not an alias/rename — different identity."""
    assert RESERVED_GLOBAL_TABLES is not RESERVED_GLOBAL_NAMESPACES


def test_reserved_global_tables_is_a_proper_subset_with_different_membership():
    """A genuinely narrower/different set, not a copy with identical members
    (the negative constraint this oracle guards against)."""
    assert RESERVED_GLOBAL_TABLES != RESERVED_GLOBAL_NAMESPACES
    assert RESERVED_GLOBAL_TABLES < RESERVED_GLOBAL_NAMESPACES
    # Minimum honest membership per the handoff's own floor.
    assert {"deploy", "ciu"} <= RESERVED_GLOBAL_TABLES


def test_reserved_global_tables_excludes_stack_scope_only_names():
    """consul/env/state/secrets are reserved as FORBIDDEN STACK root keys
    (S3.7) but are never read as top-level GLOBAL tables — excluding them
    from RESERVED_GLOBAL_TABLES is deliberate, not an omission."""
    for name in ("consul", "env", "state", "secrets"):
        assert name not in RESERVED_GLOBAL_TABLES


def test_reserved_global_tables_excludes_build():
    """proposal §1.14's own example lists 'build' as a USER table, not a
    CIU-reserved one."""
    assert "build" not in RESERVED_GLOBAL_TABLES


def test_reserved_global_tables_excludes_local_stack():
    """local_stack (V8-PREP-4) is a stack-scope concept, not global."""
    assert "local_stack" not in RESERVED_GLOBAL_TABLES


# ---------------------------------------------------------------------------
# O1/O3: validate_user_tables — absence is a complete no-op
# ---------------------------------------------------------------------------


def test_absent_user_tables_any_top_level_keys_pass_unchanged():
    """Regression bar: no ciu.user_tables declared -> arbitrary unknown
    top-level keys are NOT validated at all."""
    merged = {"ciu": {}, "deploy": {}, "some_random_app_table": {"x": 1}}
    validate_user_tables(merged)  # must not raise


def test_no_ciu_table_at_all_is_a_no_op():
    merged = {"deploy": {}, "anything": {"x": 1}}
    validate_user_tables(merged)  # must not raise


def test_ciu_table_present_but_not_a_dict_is_a_no_op():
    """A malformed [ciu] table is somebody else's finding; this function
    only acts when it can find a genuine ciu.user_tables declaration."""
    merged = {"ciu": "not-a-table", "anything": {"x": 1}}
    validate_user_tables(merged)  # must not raise


# ---------------------------------------------------------------------------
# O1/O3: a valid declaration -> unlisted key collectively named
# ---------------------------------------------------------------------------


def test_valid_declaration_allows_listed_user_tables():
    merged = {
        "ciu": {"user_tables": ["authentik", "workflow"]},
        "deploy": {},
        "authentik": {"x": 1},
        "workflow": {"y": 2},
    }
    validate_user_tables(merged)  # must not raise


def test_valid_declaration_still_allows_reserved_global_tables():
    merged = {name: {} for name in RESERVED_GLOBAL_TABLES}
    merged["ciu"] = {"user_tables": ["authentik"]}
    merged["authentik"] = {"x": 1}
    validate_user_tables(merged)  # must not raise


def test_unknown_top_level_key_raises_naming_it():
    merged = {
        "ciu": {"user_tables": ["authentik"]},
        "deploy": {},
        "authentik": {"x": 1},
        "mystery_table": {"z": 1},
    }
    with pytest.raises(ValueError, match=r"\[S3\.13\].*mystery_table"):
        validate_user_tables(merged)


def test_multiple_unknown_top_level_keys_all_named_in_one_error():
    merged = {
        "ciu": {"user_tables": []},
        "alpha": {},
        "beta": {},
    }
    with pytest.raises(ValueError) as exc:
        validate_user_tables(merged)
    message = str(exc.value)
    assert "alpha" in message
    assert "beta" in message


# ---------------------------------------------------------------------------
# O1/O3: a declared member colliding with RESERVED_GLOBAL_TABLES
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("colliding", sorted(RESERVED_GLOBAL_TABLES))
def test_declared_member_colliding_with_reserved_global_tables_raises(colliding):
    merged = {"ciu": {"user_tables": [colliding]}}
    with pytest.raises(ValueError, match=rf"\[S3\.13\].*{colliding}"):
        validate_user_tables(merged)


# ---------------------------------------------------------------------------
# O3: malformed declaration shapes
# ---------------------------------------------------------------------------


def test_non_list_declaration_raises_tagged_error():
    merged = {"ciu": {"user_tables": "authentik"}}
    with pytest.raises(ValueError, match=r"\[S3\.13\].*list of strings"):
        validate_user_tables(merged)


def test_non_string_member_raises_tagged_error():
    merged = {"ciu": {"user_tables": ["authentik", 123]}}
    with pytest.raises(ValueError, match=r"\[S3\.13\].*list of strings"):
        validate_user_tables(merged)


def test_bad_charset_member_raises_naming_it():
    merged = {"ciu": {"user_tables": ["auth entik!"]}}
    with pytest.raises(ValueError, match=r"\[S3\.13\].*auth entik!"):
        validate_user_tables(merged)


def test_duplicate_member_raises_naming_it():
    merged = {"ciu": {"user_tables": ["authentik", "authentik"]}}
    with pytest.raises(ValueError, match=r"\[S3\.13\].*duplicate.*authentik"):
        validate_user_tables(merged)


# ---------------------------------------------------------------------------
# O1: wired into render_global_chain, validated on the FINAL merged config
# ---------------------------------------------------------------------------


def test_render_global_chain_absent_declaration_is_zero_behavior_change(tmp_path):
    """A config that never declares ciu.user_tables renders exactly as
    before this package — additional, unrelated top-level tables pass."""
    _write_global_defaults(
        tmp_path,
        '[deploy]\nproject_name = "demo"\n\n[some_consumer_table]\nkey = "value"\n',
    )
    result = render_global_chain(tmp_path, tmp_path, write_rendered=False)
    assert result["some_consumer_table"]["key"] == "value"


def test_render_global_chain_unknown_table_with_declaration_raises(tmp_path):
    _write_global_defaults(
        tmp_path,
        '[ciu]\nuser_tables = ["authentik"]\n\n'
        '[deploy]\nproject_name = "demo"\n\n'
        "[mystery_table]\nkey = 1\n",
    )
    with pytest.raises(ValueError, match=r"\[S3\.13\].*mystery_table"):
        render_global_chain(tmp_path, tmp_path, write_rendered=False)


def test_render_global_chain_declared_user_table_passes(tmp_path):
    _write_global_defaults(
        tmp_path,
        '[ciu]\nuser_tables = ["authentik"]\n\n'
        '[deploy]\nproject_name = "demo"\n\n'
        '[authentik]\nkey = 1\n',
    )
    result = render_global_chain(tmp_path, tmp_path, write_rendered=False)
    assert result["authentik"]["key"] == 1


def test_render_global_chain_validates_final_merged_worktree_overlay_value(tmp_path):
    """Validated on the FINAL merged config (committed chain + worktree
    overlay) — a table declared only via the worktree overlay is still
    subject to the same check, and a later layer correcting an earlier
    layer's declaration is honored."""
    _write_global_defaults(
        tmp_path,
        '[deploy]\nproject_name = "demo"\n',
    )
    (tmp_path / "ciu.global.worktree.toml.j2").write_text(
        '[ciu]\nuser_tables = ["authentik"]\n\n[mystery_table]\nkey = 1\n',
        encoding="utf-8",
    )
    with pytest.raises(ValueError, match=r"\[S3\.13\].*mystery_table"):
        render_global_chain(tmp_path, tmp_path, write_rendered=False)


def test_render_global_chain_later_layer_relaxes_earlier_declaration(tmp_path):
    """An early layer declares user_tables without 'authentik'; the worktree
    overlay layer extends it. Validation runs once on the FINAL value, so
    the extended declaration is honored (not the earlier, narrower one)."""
    _write_global_defaults(
        tmp_path,
        '[ciu]\nuser_tables = ["workflow"]\n\n'
        '[deploy]\nproject_name = "demo"\n\n'
        '[authentik]\nkey = 1\n',
    )
    (tmp_path / "ciu.global.worktree.toml.j2").write_text(
        '[ciu]\nuser_tables = ["workflow", "authentik"]\n',
        encoding="utf-8",
    )
    result = render_global_chain(tmp_path, tmp_path, write_rendered=False)
    assert result["authentik"]["key"] == 1
