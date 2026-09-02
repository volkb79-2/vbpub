"""
Tests for V8-PREP-3 (narrowed) — the declaration-only global
``[service.<name>]`` identity registry (ciu-P22).

Normative contract: docs/SPEC.md S3.14, docs/CONFIG.md
`[service.<name>]`.

Covers:
- O1: registry shape — `type`/`location`/`description` accepted; any other
  key (including a nested table, the deferred per-service realness layer)
  is REJECTED naming the stack and the offending key; `location` required
  for CIU/COMPOSE and must name a directory containing that type's marker
  file; `location` FORBIDDEN for EXTERNAL/IN_PROCESS; absent `[service.*]`
  is a complete no-op.
- O3: every named positive/negative shape, plus the wiring into
  `render_global_chain` (validated once, on the FINAL merged config).

The two-directional `ciu check` consistency lint (S3.15) is tested
separately in `tests/tests/test_ciu_deploy_actions.py` (it lives in
`deploy.py`, not `config_model.py`).
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))

from ciu.config_model import (  # noqa: E402
    VALID_SERVICE_TYPES,
    render_global_chain,
    validate_service_registry,
)


def _write_global_defaults(directory: Path, content: str) -> None:
    (directory / "ciu.global.defaults.toml.j2").write_text(content, encoding="utf-8")


def _make_marker_dir(root: Path, rel: str, filename: str) -> None:
    """Create *root*/*rel*/*filename* so a `location` check can find it."""
    d = root / rel
    d.mkdir(parents=True, exist_ok=True)
    (d / filename).write_text("", encoding="utf-8")


# ---------------------------------------------------------------------------
# O1/O3: absence of [service.*] is a complete no-op
# ---------------------------------------------------------------------------


def test_valid_service_types_frozenset_contents():
    assert VALID_SERVICE_TYPES == {"CIU", "COMPOSE", "EXTERNAL", "IN_PROCESS"}


def test_absent_service_table_makes_zero_filesystem_checks(tmp_path, monkeypatch):
    """Regression bar (O3): absent `[service.*]` -> zero validation calls
    made, proven with a spy on the one filesystem-touching operation this
    validator performs (`Path.is_file`), not merely 'no exception raised'.
    """
    calls: list[Path] = []
    original_is_file = Path.is_file

    def spy(self):
        calls.append(self)
        return original_is_file(self)

    monkeypatch.setattr(Path, "is_file", spy)

    merged = {"deploy": {}}
    validate_service_registry(merged, tmp_path)  # must not raise

    assert calls == []


def test_service_table_empty_dict_is_a_no_op(tmp_path, monkeypatch):
    calls: list[Path] = []
    monkeypatch.setattr(Path, "is_file", lambda self: calls.append(self) or False)

    merged = {"service": {}}
    validate_service_registry(merged, tmp_path)  # must not raise

    assert calls == []


def test_service_table_not_a_dict_is_a_no_op(tmp_path):
    merged = {"service": "not-a-table"}
    validate_service_registry(merged, tmp_path)  # must not raise


# ---------------------------------------------------------------------------
# O1/O3: valid entries for every type
# ---------------------------------------------------------------------------


def test_valid_ciu_entry_passes(tmp_path):
    _make_marker_dir(tmp_path, "infra/db-core", "ciu.defaults.toml.j2")
    merged = {
        "service": {
            "our_db_stack": {"type": "CIU", "location": "infra/db-core"},
        }
    }
    validate_service_registry(merged, tmp_path)  # must not raise


def test_valid_compose_entry_passes(tmp_path):
    _make_marker_dir(tmp_path, "opt/legacy", "docker-compose.yml")
    merged = {
        "service": {
            "legacy_stack": {"type": "COMPOSE", "location": "opt/legacy"},
        }
    }
    validate_service_registry(merged, tmp_path)  # must not raise


def test_valid_external_entry_passes(tmp_path):
    merged = {"service": {"payment_api": {"type": "EXTERNAL"}}}
    validate_service_registry(merged, tmp_path)  # must not raise


def test_valid_in_process_entry_passes(tmp_path):
    merged = {"service": {"notification_service": {"type": "IN_PROCESS"}}}
    validate_service_registry(merged, tmp_path)  # must not raise


def test_valid_entry_with_description_passes(tmp_path):
    merged = {
        "service": {
            "payment_api": {
                "type": "EXTERNAL",
                "description": "Stripe payment gateway",
            }
        }
    }
    validate_service_registry(merged, tmp_path)  # must not raise


def test_multiple_valid_entries_of_every_type_pass_together(tmp_path):
    _make_marker_dir(tmp_path, "infra/db-core", "ciu.defaults.toml.j2")
    _make_marker_dir(tmp_path, "opt/legacy", "docker-compose.yml")
    merged = {
        "service": {
            "our_db_stack": {"type": "CIU", "location": "infra/db-core"},
            "legacy_stack": {"type": "COMPOSE", "location": "opt/legacy"},
            "payment_api": {"type": "EXTERNAL"},
            "notification_service": {"type": "IN_PROCESS"},
        }
    }
    validate_service_registry(merged, tmp_path)  # must not raise


# ---------------------------------------------------------------------------
# O1/O3: entry shape rejections
# ---------------------------------------------------------------------------


def test_entry_not_a_table_raises_naming_stack(tmp_path):
    merged = {"service": {"our_db_stack": "not-a-table"}}
    with pytest.raises(ValueError, match=r"\[S3\.14\].*our_db_stack.*TOML table"):
        validate_service_registry(merged, tmp_path)


def test_unknown_key_at_stack_scope_raises_naming_stack_and_key(tmp_path):
    merged = {
        "service": {
            "payment_api": {"type": "EXTERNAL", "endpoint": "https://api.stripe.com"}
        }
    }
    with pytest.raises(ValueError, match=r"\[S3\.14\].*payment_api.*endpoint"):
        validate_service_registry(merged, tmp_path)


def test_nested_table_at_stack_scope_is_rejected_not_silently_accepted(tmp_path):
    """The one thing that must never regress: a per-service realness
    sub-table (proposal §3.2 shape: `[service.<stack>.<svc>.<level>]`) is a
    nested table under the stack entry, and MUST be REFUSED — not
    accepted-and-ignored — so V8 can define that layer later without a
    silent-acceptance migration trap.
    """
    _make_marker_dir(tmp_path, "opt/legacy", "docker-compose.yml")
    merged = {
        "service": {
            "legacy_stack": {
                "type": "COMPOSE",
                "location": "opt/legacy",
                # This is exactly proposal §3.2's realness-variant shape.
                "service1": {"port": 1234},
            }
        }
    }
    with pytest.raises(ValueError, match=r"\[S3\.14\].*legacy_stack.*service1"):
        validate_service_registry(merged, tmp_path)


def test_nested_realness_variant_under_external_is_also_rejected(tmp_path):
    """Same rejection for the EXTERNAL/IN_PROCESS shape from §3.1's own
    worked example (`[service.payment-api.live]`)."""
    merged = {
        "service": {
            "payment_api": {
                "type": "EXTERNAL",
                "live": {"endpoint": "https://api.stripe.com"},
            }
        }
    }
    with pytest.raises(ValueError, match=r"\[S3\.14\].*payment_api.*live"):
        validate_service_registry(merged, tmp_path)


# ---------------------------------------------------------------------------
# O1/O3: `type` — required, closed vocabulary
# ---------------------------------------------------------------------------


def test_missing_type_raises_naming_stack(tmp_path):
    merged = {"service": {"our_db_stack": {"location": "infra/db-core"}}}
    with pytest.raises(ValueError, match=r"\[S3\.14\].*our_db_stack.*type"):
        validate_service_registry(merged, tmp_path)


def test_unknown_type_value_raises_naming_the_closed_vocabulary(tmp_path):
    merged = {"service": {"our_db_stack": {"type": "BOGUS"}}}
    with pytest.raises(
        ValueError, match=r"\[S3\.14\].*our_db_stack.*CIU.*COMPOSE.*EXTERNAL.*IN_PROCESS"
    ):
        validate_service_registry(merged, tmp_path)


def test_non_string_type_value_is_a_tagged_error_not_a_bare_typeerror(tmp_path):
    """An inline-table/array `type` value is unhashable; the isinstance
    guard must fire BEFORE the `in VALID_SERVICE_TYPES` membership test, or
    this crashes with a bare TypeError instead of this function's own
    tagged ValueError."""
    merged = {"service": {"our_db_stack": {"type": ["CIU"]}}}
    with pytest.raises(ValueError, match=r"\[S3\.14\].*our_db_stack.*type"):
        validate_service_registry(merged, tmp_path)


# ---------------------------------------------------------------------------
# O1/O3: `location` — required for CIU/COMPOSE, forbidden for EXTERNAL/IN_PROCESS
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("service_type", ["CIU", "COMPOSE"])
def test_location_missing_for_type_requiring_it_raises(tmp_path, service_type):
    merged = {"service": {"our_stack": {"type": service_type}}}
    with pytest.raises(ValueError, match=r"\[S3\.14\].*our_stack.*location.*required"):
        validate_service_registry(merged, tmp_path)


@pytest.mark.parametrize("service_type", ["EXTERNAL", "IN_PROCESS"])
def test_location_present_for_type_forbidding_it_raises(tmp_path, service_type):
    """Negative (O1): EXTERNAL/IN_PROCESS must FORBID `location`, never
    silently permit it."""
    merged = {
        "service": {"payment_api": {"type": service_type, "location": "somewhere"}}
    }
    with pytest.raises(ValueError, match=r"\[S3\.14\].*payment_api.*location.*FORBIDDEN"):
        validate_service_registry(merged, tmp_path)


def test_location_non_string_for_ciu_raises(tmp_path):
    merged = {"service": {"our_db_stack": {"type": "CIU", "location": 123}}}
    with pytest.raises(ValueError, match=r"\[S3\.14\].*our_db_stack.*location.*required"):
        validate_service_registry(merged, tmp_path)


def test_location_empty_string_for_ciu_raises(tmp_path):
    merged = {"service": {"our_db_stack": {"type": "CIU", "location": ""}}}
    with pytest.raises(ValueError, match=r"\[S3\.14\].*our_db_stack.*location.*required"):
        validate_service_registry(merged, tmp_path)


def test_ciu_location_directory_missing_the_defaults_marker_raises(tmp_path):
    (tmp_path / "infra/db-core").mkdir(parents=True)  # dir exists, marker doesn't
    merged = {
        "service": {"our_db_stack": {"type": "CIU", "location": "infra/db-core"}}
    }
    with pytest.raises(
        ValueError, match=r"\[S3\.14\].*our_db_stack.*ciu\.defaults\.toml\.j2"
    ):
        validate_service_registry(merged, tmp_path)


def test_compose_location_directory_missing_the_compose_marker_raises(tmp_path):
    (tmp_path / "opt/legacy").mkdir(parents=True)  # dir exists, marker doesn't
    merged = {
        "service": {"legacy_stack": {"type": "COMPOSE", "location": "opt/legacy"}}
    }
    with pytest.raises(
        ValueError, match=r"\[S3\.14\].*legacy_stack.*docker-compose\.yml"
    ):
        validate_service_registry(merged, tmp_path)


def test_ciu_location_directory_does_not_exist_at_all_raises(tmp_path):
    merged = {
        "service": {"our_db_stack": {"type": "CIU", "location": "infra/nowhere"}}
    }
    with pytest.raises(
        ValueError, match=r"\[S3\.14\].*our_db_stack.*ciu\.defaults\.toml\.j2"
    ):
        validate_service_registry(merged, tmp_path)


# ---------------------------------------------------------------------------
# O1/O3: `description` — optional string
# ---------------------------------------------------------------------------


def test_description_non_string_raises(tmp_path):
    merged = {
        "service": {"payment_api": {"type": "EXTERNAL", "description": 123}}
    }
    with pytest.raises(ValueError, match=r"\[S3\.14\].*payment_api.*description"):
        validate_service_registry(merged, tmp_path)


# ---------------------------------------------------------------------------
# O3: independent per-entry validation across multiple stacks
# ---------------------------------------------------------------------------


def test_one_bad_stack_among_several_is_named_specifically(tmp_path):
    _make_marker_dir(tmp_path, "infra/db-core", "ciu.defaults.toml.j2")
    merged = {
        "service": {
            "our_db_stack": {"type": "CIU", "location": "infra/db-core"},
            "payment_api": {"type": "EXTERNAL", "location": "should-not-be-here"},
        }
    }
    with pytest.raises(ValueError, match=r"\[S3\.14\].*payment_api"):
        validate_service_registry(merged, tmp_path)


# ---------------------------------------------------------------------------
# O1: wired into render_global_chain, validated on the FINAL merged config
# ---------------------------------------------------------------------------


def test_render_global_chain_absent_service_registry_is_zero_behavior_change(tmp_path):
    _write_global_defaults(tmp_path, '[deploy]\nproject_name = "demo"\n')
    result = render_global_chain(tmp_path, tmp_path, write_rendered=False)
    assert "service" not in result


def test_render_global_chain_valid_service_registry_passes_through(tmp_path):
    _make_marker_dir(tmp_path, "infra/db-core", "ciu.defaults.toml.j2")
    _write_global_defaults(
        tmp_path,
        '[deploy]\nproject_name = "demo"\n\n'
        '[service.our_db_stack]\ntype = "CIU"\nlocation = "infra/db-core"\n',
    )
    result = render_global_chain(tmp_path, tmp_path, write_rendered=False)
    assert result["service"]["our_db_stack"]["location"] == "infra/db-core"


def test_render_global_chain_malformed_service_registry_raises(tmp_path):
    _write_global_defaults(
        tmp_path,
        '[deploy]\nproject_name = "demo"\n\n'
        '[service.our_db_stack]\ntype = "BOGUS"\n',
    )
    with pytest.raises(ValueError, match=r"\[S3\.14\].*our_db_stack"):
        render_global_chain(tmp_path, tmp_path, write_rendered=False)


def test_render_global_chain_validates_final_merged_worktree_overlay_value(tmp_path):
    """Validated on the FINAL merged config (committed chain + worktree
    overlay) — a malformed entry declared only via the worktree overlay is
    still caught."""
    _write_global_defaults(tmp_path, '[deploy]\nproject_name = "demo"\n')
    (tmp_path / "ciu.global.instance.toml.j2").write_text(
        '[service.our_db_stack]\ntype = "BOGUS"\n', encoding="utf-8"
    )
    with pytest.raises(ValueError, match=r"\[S3\.14\].*our_db_stack"):
        render_global_chain(tmp_path, tmp_path, write_rendered=False)


def test_render_global_chain_later_layer_corrects_an_earlier_bad_location(tmp_path):
    """An early layer declares a `location` that does not (yet) resolve on
    disk; the worktree overlay layer corrects it. Validation runs once on
    the FINAL value, so the corrected location is honored, not the
    earlier, broken one."""
    _make_marker_dir(tmp_path, "infra/real-db-core", "ciu.defaults.toml.j2")
    _write_global_defaults(
        tmp_path,
        '[deploy]\nproject_name = "demo"\n\n'
        '[service.our_db_stack]\ntype = "CIU"\nlocation = "infra/does-not-exist"\n',
    )
    (tmp_path / "ciu.global.instance.toml.j2").write_text(
        '[service.our_db_stack]\nlocation = "infra/real-db-core"\n',
        encoding="utf-8",
    )
    result = render_global_chain(tmp_path, tmp_path, write_rendered=False)
    assert result["service"]["our_db_stack"]["location"] == "infra/real-db-core"
