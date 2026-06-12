"""
Tests for src/ciu/config_model.py — CIU v2 configuration model.

Covers SPEC S3: configuration loading, chain_dirs (B11 regression), merging,
global chain render, stack render, state preservation (S3.4), and shape
validation (S3.5, S3.7).

All filesystem fixtures use tmp_path; env vars use monkeypatch.
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))

from ciu.config_model import (  # noqa: E402
    RESERVED_GLOBAL_NAMESPACES,
    chain_dirs,
    deep_merge,
    ensure_override_template,
    expand_env_vars_or_fail,
    parse_toml,
    parse_toml_string,
    render_global_chain,
    render_jinja2_text,
    render_stack,
    render_toml_template,
    validate_stack_shape,
    write_rendered_toml,
)

# ---------------------------------------------------------------------------
# expand_env_vars_or_fail
# ---------------------------------------------------------------------------


def test_expand_env_vars_substitutes_set_var(monkeypatch):
    monkeypatch.setenv("MY_VAR", "hello")
    assert expand_env_vars_or_fail("value=$MY_VAR", "test") == "value=hello"


def test_expand_env_vars_raises_on_missing(monkeypatch):
    monkeypatch.delenv("MISSING_VAR", raising=False)
    with pytest.raises(ValueError, match="MISSING_VAR"):
        expand_env_vars_or_fail("x=${MISSING_VAR}", "test.toml")


def test_expand_env_vars_raises_naming_the_var(monkeypatch):
    monkeypatch.delenv("GHOST", raising=False)
    # match= verifies the error message names the variable
    with pytest.raises(ValueError, match="GHOST"):
        expand_env_vars_or_fail("$GHOST", "src.toml")


def test_expand_env_vars_empty_value_treated_as_missing(monkeypatch):
    monkeypatch.setenv("EMPTY_VAR", "")
    with pytest.raises(ValueError, match="EMPTY_VAR"):
        expand_env_vars_or_fail("$EMPTY_VAR", "src.toml")


def test_expand_env_vars_braces_form(monkeypatch):
    monkeypatch.setenv("BRACED", "world")
    assert expand_env_vars_or_fail("${BRACED}!", "t") == "world!"


# ---------------------------------------------------------------------------
# parse_toml_string / parse_toml
# ---------------------------------------------------------------------------


def test_parse_toml_string_valid():
    result = parse_toml_string('[section]\nkey = "value"', "test")
    assert result == {"section": {"key": "value"}}


def test_parse_toml_string_syntax_error_raises():
    with pytest.raises(ValueError, match="TOML syntax error"):
        parse_toml_string("not = [valid toml [[[", "bad.toml")


def test_parse_toml_file_missing_raises(tmp_path):
    with pytest.raises(FileNotFoundError):
        parse_toml(tmp_path / "nonexistent.toml")


def test_parse_toml_file_reads_correctly(tmp_path):
    p = tmp_path / "cfg.toml"
    p.write_bytes(b'[s]\nk = 1\n')
    assert parse_toml(p) == {"s": {"k": 1}}


# ---------------------------------------------------------------------------
# write_rendered_toml + round-trip
# ---------------------------------------------------------------------------


def test_write_rendered_toml_roundtrip(tmp_path):
    cfg = {"section": {"key": "value", "num": 42}}
    out = tmp_path / "sub" / "out.toml"
    write_rendered_toml(out, cfg)
    assert out.exists()
    assert parse_toml(out) == cfg


# ---------------------------------------------------------------------------
# ensure_override_template
# ---------------------------------------------------------------------------


def test_ensure_override_template_creates_when_missing(tmp_path):
    defaults = tmp_path / "ciu-global.defaults.toml.j2"
    overrides = tmp_path / "ciu-global.toml.j2"
    defaults.write_text('[ciu]\nkey = "default"\n', encoding="utf-8")
    ensure_override_template(defaults, overrides)
    assert overrides.exists()
    assert overrides.read_text() == defaults.read_text()


def test_ensure_override_template_does_not_overwrite(tmp_path):
    defaults = tmp_path / "ciu-global.defaults.toml.j2"
    overrides = tmp_path / "ciu-global.toml.j2"
    defaults.write_text('[ciu]\nkey = "default"\n', encoding="utf-8")
    overrides.write_text('[ciu]\nkey = "custom"\n', encoding="utf-8")
    ensure_override_template(defaults, overrides)
    assert overrides.read_text() == '[ciu]\nkey = "custom"\n'


# ---------------------------------------------------------------------------
# render_jinja2_text
# ---------------------------------------------------------------------------


def test_render_jinja2_text_substitutes():
    result = render_jinja2_text("hello {{ name }}", {"name": "world"})
    assert result == "hello world"


def test_render_jinja2_text_unknown_var_renders_empty():
    # Jinja2 default: undefined renders as empty string
    result = render_jinja2_text("{{ missing }}", {})
    assert result == ""


# ---------------------------------------------------------------------------
# deep_merge (S3.3)
# ---------------------------------------------------------------------------


def test_deep_merge_scalars_replace():
    base = {"a": 1, "b": 2}
    override = {"b": 99}
    result = deep_merge(base, override)
    assert result == {"a": 1, "b": 99}


def test_deep_merge_dicts_recurse():
    base = {"a": {"x": 1, "y": 2}}
    override = {"a": {"y": 99, "z": 3}}
    result = deep_merge(base, override)
    assert result == {"a": {"x": 1, "y": 99, "z": 3}}


def test_deep_merge_lists_replace_not_concatenate():
    base = {"items": [1, 2, 3]}
    override = {"items": [4, 5]}
    result = deep_merge(base, override)
    assert result["items"] == [4, 5]


def test_deep_merge_new_key_added():
    base = {"a": 1}
    override = {"b": 2}
    result = deep_merge(base, override)
    assert result == {"a": 1, "b": 2}


def test_deep_merge_does_not_mutate_inputs():
    base = {"a": {"x": 1}}
    override = {"a": {"y": 2}}
    deep_merge(base, override)
    assert base == {"a": {"x": 1}}
    assert override == {"a": {"y": 2}}


# ---------------------------------------------------------------------------
# chain_dirs (S3.3 — B11 regression: no duplicate root, working_dir included)
# ---------------------------------------------------------------------------


def test_chain_dirs_root_equals_working_dir(tmp_path):
    """repo_root == working_dir → single-element list [root]."""
    result = chain_dirs(tmp_path, tmp_path)
    assert result == [tmp_path]


def test_chain_dirs_one_level(tmp_path):
    stack = tmp_path / "infra" / "redis"
    stack.mkdir(parents=True)
    result = chain_dirs(tmp_path, stack)
    assert result == [tmp_path, tmp_path / "infra", stack]


def test_chain_dirs_two_levels_deep_no_duplicate_root(tmp_path):
    """Regression for v1 engine.py:651 — root must appear exactly once."""
    stack = tmp_path / "cat" / "proj" / "svc"
    stack.mkdir(parents=True)
    result = chain_dirs(tmp_path, stack)
    assert result == [
        tmp_path,
        tmp_path / "cat",
        tmp_path / "cat" / "proj",
        stack,
    ]
    # Root appears exactly once (v1 bug: it appeared twice)
    assert result.count(tmp_path) == 1
    # Working dir IS included (v1 bug: it was omitted)
    assert stack in result


def test_chain_dirs_working_dir_outside_root_raises(tmp_path):
    outside = tmp_path.parent
    with pytest.raises(ValueError, match="not under repo_root"):
        chain_dirs(tmp_path, outside)


def test_chain_dirs_sibling_raises(tmp_path):
    """A sibling directory is not under repo_root."""
    sibling = tmp_path.parent / "sibling"
    sibling.mkdir(exist_ok=True)
    with pytest.raises(ValueError):
        chain_dirs(tmp_path, sibling)


# ---------------------------------------------------------------------------
# render_global_chain (S3.3 + B11 fix)
# ---------------------------------------------------------------------------


def _write_global_defaults(directory: Path, content: str) -> None:
    (directory / "ciu-global.defaults.toml.j2").write_text(content, encoding="utf-8")


def _write_global_overrides(directory: Path, content: str) -> None:
    (directory / "ciu-global.toml.j2").write_text(content, encoding="utf-8")


def test_render_global_chain_simple(tmp_path, monkeypatch):
    """Root-only global config renders and writes ciu-global.toml."""
    monkeypatch.setenv("REPO_ROOT", str(tmp_path))
    _write_global_defaults(tmp_path, '[ciu]\nenv = "test"\n')
    result = render_global_chain(tmp_path, tmp_path)
    assert result["ciu"]["env"] == "test"
    assert (tmp_path / "ciu-global.toml").exists()


def test_render_global_chain_override_wins(tmp_path, monkeypatch):
    """Override values beat defaults."""
    monkeypatch.setenv("REPO_ROOT", str(tmp_path))
    _write_global_defaults(tmp_path, '[ciu]\nenv = "default"\n')
    _write_global_overrides(tmp_path, '[ciu]\nenv = "override"\n')
    result = render_global_chain(tmp_path, tmp_path)
    assert result["ciu"]["env"] == "override"


def test_render_global_chain_leaf_dir_applied(tmp_path, monkeypatch):
    """S3.3 fix (B11): leaf directory IS applied (v1 skipped it)."""
    monkeypatch.setenv("REPO_ROOT", str(tmp_path))
    stack = tmp_path / "infra" / "vault"
    stack.mkdir(parents=True)

    # Root config
    _write_global_defaults(tmp_path, '[ciu]\nenv = "root"\n')

    # Leaf (stack) directory global config — must be applied (v1 omitted this)
    _write_global_defaults(stack, '[ciu]\nenv = "leaf"\nextra = "from_leaf"\n')

    result = render_global_chain(stack, tmp_path)
    assert result["ciu"]["env"] == "leaf"
    assert result["ciu"]["extra"] == "from_leaf"


def test_render_global_chain_mid_dir_applied(tmp_path, monkeypatch):
    """Intermediate directories between root and leaf are applied."""
    monkeypatch.setenv("REPO_ROOT", str(tmp_path))
    mid = tmp_path / "infra"
    stack = mid / "redis"
    mid.mkdir()
    stack.mkdir()

    _write_global_defaults(tmp_path, '[ciu]\nenv = "root"\n[root_only]\nv = 1\n')
    _write_global_defaults(mid, '[ciu]\nenv = "mid"\n[mid_only]\nv = 2\n')
    _write_global_defaults(stack, '[stack_only]\nv = 3\n')

    result = render_global_chain(stack, tmp_path)
    assert result["ciu"]["env"] == "mid"
    assert result["root_only"]["v"] == 1
    assert result["mid_only"]["v"] == 2
    assert result["stack_only"]["v"] == 3


def test_render_global_chain_overrides_without_defaults_raises(tmp_path, monkeypatch):
    """overrides file present without defaults → ValueError."""
    monkeypatch.setenv("REPO_ROOT", str(tmp_path))
    _write_global_defaults(tmp_path, '[ciu]\nenv = "root"\n')
    mid = tmp_path / "sub"
    mid.mkdir()
    _write_global_overrides(mid, '[ciu]\nenv = "sub"\n')
    with pytest.raises(ValueError, match="without"):
        render_global_chain(mid, tmp_path)


def test_render_global_chain_empty_raises(tmp_path, monkeypatch):
    """No global config at all → ValueError."""
    monkeypatch.setenv("REPO_ROOT", str(tmp_path))
    stack = tmp_path / "infra" / "redis"
    stack.mkdir(parents=True)
    with pytest.raises(ValueError, match="No global configuration"):
        render_global_chain(stack, tmp_path)


def test_render_global_chain_writes_global_toml(tmp_path, monkeypatch):
    """ciu-global.toml is written at repo_root."""
    monkeypatch.setenv("REPO_ROOT", str(tmp_path))
    _write_global_defaults(tmp_path, '[ciu]\nenv = "test"\n')
    render_global_chain(tmp_path, tmp_path)
    assert (tmp_path / "ciu-global.toml").exists()


def test_render_global_chain_jinja2_context_uses_merged_config(tmp_path, monkeypatch):
    """Each template is rendered against the config merged SO FAR (v1 behaviour)."""
    monkeypatch.setenv("REPO_ROOT", str(tmp_path))
    mid = tmp_path / "sub"
    mid.mkdir()

    # root sets base.x = 10
    _write_global_defaults(tmp_path, '[base]\nx = 10\n')
    # mid references it in a template expression
    _write_global_defaults(mid, '[derived]\ny = {{ base.x + 1 }}\n')

    result = render_global_chain(mid, tmp_path)
    assert result["derived"]["y"] == 11


# ---------------------------------------------------------------------------
# render_stack (S3.1 + S3.4)
# ---------------------------------------------------------------------------


def _write_stack_defaults(directory: Path, content: str) -> None:
    (directory / "ciu.defaults.toml.j2").write_text(content, encoding="utf-8")


def _write_stack_overrides(directory: Path, content: str) -> None:
    (directory / "ciu.toml.j2").write_text(content, encoding="utf-8")


def test_render_stack_basic(tmp_path):
    _write_stack_defaults(tmp_path, '[redis_core]\nenv = "test"\n')
    result = render_stack(tmp_path, {})
    assert result["redis_core"]["env"] == "test"
    assert (tmp_path / "ciu.toml").exists()


def test_render_stack_overrides_win(tmp_path):
    _write_stack_defaults(tmp_path, '[redis_core]\nenv = "default"\n')
    _write_stack_overrides(tmp_path, '[redis_core]\nenv = "override"\n')
    result = render_stack(tmp_path, {})
    assert result["redis_core"]["env"] == "override"


def test_render_stack_missing_defaults_raises(tmp_path):
    with pytest.raises(FileNotFoundError, match="ciu.defaults.toml.j2"):
        render_stack(tmp_path, {})


def test_render_stack_state_preserved_across_rerender(tmp_path):
    """S3.4: [state] from previous ciu.toml is carried into re-render."""
    _write_stack_defaults(tmp_path, '[redis_core]\nenv = "test"\n')

    # First render: no prior state
    result1 = render_stack(tmp_path, {})
    assert "state" not in result1

    # Inject state into the rendered ciu.toml manually (simulating hook output)
    import tomli_w
    existing = parse_toml(tmp_path / "ciu.toml")
    existing["state"] = {"initialized": True, "root_token": "tok123"}
    with open(tmp_path / "ciu.toml", "wb") as fh:
        tomli_w.dump(existing, fh)

    # Second render: state should be preserved
    result2 = render_stack(tmp_path, {}, preserve_state=True)
    assert result2["state"]["initialized"] is True
    assert result2["state"]["root_token"] == "tok123"


def test_render_stack_state_not_preserved_when_disabled(tmp_path):
    """preserve_state=False drops [state] from previous render."""
    _write_stack_defaults(tmp_path, '[redis_core]\nenv = "test"\n')
    import tomli_w
    prior = {"redis_core": {"env": "test"}, "state": {"initialized": True}}
    with open(tmp_path / "ciu.toml", "wb") as fh:
        tomli_w.dump(prior, fh)

    result = render_stack(tmp_path, {}, preserve_state=False)
    assert "state" not in result


def test_render_stack_secrets_not_preserved(tmp_path):
    """S3.4: [secrets] from previous ciu.toml is NOT carried into re-render."""
    _write_stack_defaults(tmp_path, '[redis_core]\nenv = "test"\n')

    import tomli_w
    prior = {
        "redis_core": {"env": "test"},
        "state": {"initialized": True},
        "secrets": {"local": {"redis_password": "super_secret"}},
    }
    with open(tmp_path / "ciu.toml", "wb") as fh:
        tomli_w.dump(prior, fh)

    result = render_stack(tmp_path, {}, preserve_state=True)
    # state carried; secrets dropped
    assert result.get("state", {}).get("initialized") is True
    assert "secrets" not in result


def test_render_stack_overrides_rendered_against_global_plus_defaults(tmp_path, monkeypatch):
    """Overrides are rendered against deep_merge(global, defaults) (v1 behaviour)."""
    global_config = {"global": {"base_port": 6379}}
    _write_stack_defaults(
        tmp_path,
        '[redis_core]\nport = {{ global.base_port }}\n',
    )
    _write_stack_overrides(
        tmp_path,
        '[redis_core]\nport = {{ redis_core.port }}\nenv = "override"\n',
    )
    result = render_stack(tmp_path, global_config)
    assert result["redis_core"]["port"] == 6379
    assert result["redis_core"]["env"] == "override"


def test_render_stack_writes_ciu_toml(tmp_path):
    _write_stack_defaults(tmp_path, '[svc]\nk = 1\n')
    render_stack(tmp_path, {})
    assert (tmp_path / "ciu.toml").exists()


def test_render_stack_env_expansion(tmp_path, monkeypatch):
    """$VAR expansion works in stack templates."""
    monkeypatch.setenv("STACK_HOST", "redis.internal")
    _write_stack_defaults(tmp_path, '[svc]\nhost = "$STACK_HOST"\n')
    result = render_stack(tmp_path, {})
    assert result["svc"]["host"] == "redis.internal"


# ---------------------------------------------------------------------------
# validate_stack_shape (S3.5 + S3.7)
# ---------------------------------------------------------------------------


def test_validate_stack_shape_single_root_ok():
    cfg = {"redis_core": {"env": "test"}}
    assert validate_stack_shape(cfg) == "redis_core"


def test_validate_stack_shape_root_plus_state_ok():
    """[state] is reserved at stack level — root + state is valid."""
    cfg = {"redis_core": {"env": "test"}, "state": {"initialized": True}}
    assert validate_stack_shape(cfg) == "redis_core"


def test_validate_stack_shape_two_roots_raises():
    """S3.5: two non-reserved top-level keys → ValueError [S3.5]."""
    cfg = {"redis_core": {}, "postgres_core": {}}
    with pytest.raises(ValueError, match=r"\[S3\.5\]"):
        validate_stack_shape(cfg)


def test_validate_stack_shape_error_lists_offending_keys():
    """S3.5 error must list the offending keys (match= checks message content)."""
    cfg = {"redis_core": {}, "postgres_core": {}, "mongo_core": {}}
    # At least one of the keys must appear in the error message
    with pytest.raises(ValueError, match=r"redis_core|postgres_core|mongo_core"):
        validate_stack_shape(cfg)


def test_validate_stack_shape_no_root_raises():
    """No non-reserved key → ValueError [S3.5]."""
    cfg = {"state": {"x": 1}}
    with pytest.raises(ValueError, match=r"\[S3\.5\]"):
        validate_stack_shape(cfg)


def test_validate_stack_shape_root_key_vault_raises():
    """S3.7: 'vault' collides with reserved global namespace."""
    cfg = {"vault": {"server": {}}}
    with pytest.raises(ValueError, match=r"\[S3\.7\]"):
        validate_stack_shape(cfg)


def test_validate_stack_shape_s3_7_error_suggests_renaming():
    """S3.7 error message must contain [S3.7] and mention 'vault_core' (rename hint)."""
    cfg = {"vault": {}}
    with pytest.raises(ValueError, match=r"vault_core"):
        validate_stack_shape(cfg)


def test_validate_stack_shape_reserved_namespaces_all_reject():
    """Every entry in RESERVED_GLOBAL_NAMESPACES must be rejected as a root key."""
    for ns in RESERVED_GLOBAL_NAMESPACES - {"state"}:
        cfg = {ns: {}}
        with pytest.raises(ValueError, match=r"\[S3\.7\]"):
            validate_stack_shape(cfg)


def test_validate_stack_shape_custom_root_not_in_reserved_ok():
    """A non-reserved root key like 'my_service' passes validation."""
    cfg = {"my_service": {"host": "localhost"}}
    assert validate_stack_shape(cfg) == "my_service"


# ---------------------------------------------------------------------------
# render_toml_template (integration of jinja2 + env-expand + toml parse)
# ---------------------------------------------------------------------------


def test_render_toml_template_full_pipeline(tmp_path, monkeypatch):
    """Full S3.2 pipeline: Jinja2 → $VAR expand → TOML parse."""
    monkeypatch.setenv("INSTANCE_HOST", "db.internal")
    tpl = tmp_path / "ciu.defaults.toml.j2"
    tpl.write_text(
        '[db]\nhost = "$INSTANCE_HOST"\nport = {{ config.port }}\n',
        encoding="utf-8",
    )
    context = {"config": {"port": 5432}, "env": dict(os.environ)}
    result = render_toml_template(tpl, context)
    assert result["db"]["host"] == "db.internal"
    assert result["db"]["port"] == 5432


def test_render_toml_template_missing_file_raises(tmp_path):
    with pytest.raises(FileNotFoundError):
        render_toml_template(tmp_path / "nonexistent.toml.j2", {})


def test_render_toml_template_missing_env_var_surfaces_name(tmp_path, monkeypatch):
    """Missing env var name appears in the error (S3.2 requirement)."""
    monkeypatch.delenv("UNDEFINED_CIU_VAR", raising=False)
    tpl = tmp_path / "bad.toml.j2"
    tpl.write_text('[s]\nv = "$UNDEFINED_CIU_VAR"\n', encoding="utf-8")
    with pytest.raises(ValueError, match="UNDEFINED_CIU_VAR"):
        render_toml_template(tpl, {})
