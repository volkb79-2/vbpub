#!/usr/bin/env python3
"""
CIU v2 configuration loading, merging, and validation.

Implements SPEC S3 (configuration model):
  S3.1  File roles and layering
  S3.2  Render pipeline per template
  S3.3  Merge chain (root→leaf, each directory exactly once)
  S3.4  Re-render preserves only [state]; [secrets] NOT preserved
  S3.5  Stack shape: exactly one non-reserved top-level key
  S3.7  Stack root key must not collide with reserved global namespaces

This module is standalone: it does NOT import from engine.py or deploy.py.
Engine.py / deploy.py will import this module in the Wave-3 cutover.

Public API
----------
ENV_VAR_PATTERN                  re.Pattern for $VAR / ${VAR}
expand_env_vars_or_fail(text, source) -> str
parse_toml_string(text, source)  -> dict
parse_toml(path)                 -> dict
write_rendered_toml(path, config)
ensure_override_template(defaults, overrides)
render_jinja2_text(template_text, context) -> str
render_toml_template(path, context)  -> dict
deep_merge(base, override)       -> dict   (S3.3: tables merge, lists replace)
chain_dirs(repo_root, working_dir) -> list[Path]   (S3.3 fix of B11)
render_global_chain(working_dir, repo_root) -> dict
render_stack(working_dir, global_config, preserve_state=True) -> dict
RESERVED_GLOBAL_NAMESPACES       frozenset[str]   (S3.7)
validate_stack_shape(stack_config) -> str          (S3.5 + S3.7)
"""

from __future__ import annotations

import os
import re
import shutil
import tomllib
from pathlib import Path

from .config_constants import (
    GLOBAL_CONFIG_DEFAULTS,
    GLOBAL_CONFIG_OVERRIDES,
    GLOBAL_CONFIG_RENDERED,
    STACK_CONFIG_DEFAULTS,
    STACK_CONFIG_OVERRIDES,
    STACK_CONFIG_RENDERED,
)

# ---------------------------------------------------------------------------
# S3.7 – reserved global-namespace names
# ---------------------------------------------------------------------------

RESERVED_GLOBAL_NAMESPACES: frozenset[str] = frozenset({
    "ciu",
    "deploy",
    "topology",
    "registry",
    "vault",
    "consul",
    "service",
    "env",
    "state",
    "auto_generated",
    "secrets",
})

# ---------------------------------------------------------------------------
# S3.2 – env-var expansion
# ---------------------------------------------------------------------------

ENV_VAR_PATTERN: re.Pattern[str] = re.compile(r"\$(\w+)|\$\{([^}]+)\}")


def expand_env_vars_or_fail(raw_text: str, source: str) -> str:
    """Expand $VAR / ${VAR} using os.environ; fail-fast on missing/empty values.

    Reports ALL missing variable names in a single error (S3.2).
    """
    missing: set[str] = set()

    def _replace(match: re.Match) -> str:
        var_name = match.group(1) or match.group(2)
        value = os.environ.get(var_name)
        if value is None or value == "":
            missing.add(var_name)
            return match.group(0)
        return value

    expanded = ENV_VAR_PATTERN.sub(_replace, raw_text)

    if missing:
        missing_list = ", ".join(sorted(missing))
        raise ValueError(
            f"[ERROR] Missing required environment values in {source}: {missing_list}.\n"
            "[ERROR] ciu.env is authoritative. Run ciu --generate-env "
            "and source ciu.env before running CIU."
        )

    leftover = ENV_VAR_PATTERN.search(expanded)
    if leftover:
        raise ValueError(
            f"[ERROR] Unresolved environment placeholders remain in {source}: {leftover.group(0)}\n"
            "[ERROR] Ensure all required values are set in ciu.env."
        )

    return expanded


# ---------------------------------------------------------------------------
# S3.2 – TOML parsing
# ---------------------------------------------------------------------------

def parse_toml_string(toml_text: str, source: str) -> dict:
    """Parse TOML from a string; abort with file + position on syntax error."""
    try:
        return tomllib.loads(toml_text)
    except tomllib.TOMLDecodeError as exc:
        raise ValueError(
            f"[ERROR] Failed to parse TOML from {source}\n"
            f"[ERROR] TOML syntax error: {exc}"
        ) from exc


def parse_toml(path: Path) -> dict:
    """Read and parse a TOML file; raises FileNotFoundError when missing."""
    path = Path(path)
    if not path.exists():
        raise FileNotFoundError(f"TOML file not found: {path}")
    with open(path, "rb") as fh:
        return tomllib.load(fh)


# ---------------------------------------------------------------------------
# Rendering helpers
# ---------------------------------------------------------------------------

def write_rendered_toml(output_path: Path, config: dict) -> None:
    """Write a config dict to disk as TOML (atomic mkdir, then write)."""
    import tomli_w

    output_path.parent.mkdir(parents=True, exist_ok=True)
    with open(output_path, "wb") as fh:
        tomli_w.dump(config, fh)


def ensure_override_template(defaults_path: Path, overrides_path: Path) -> None:
    """Create the overrides template from defaults when it does not yet exist."""
    if overrides_path.exists():
        return
    if not defaults_path.exists():
        return
    overrides_path.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(defaults_path, overrides_path)
    print(
        f"[INFO] Created override template from defaults: {overrides_path}",
        flush=True,
    )


def render_jinja2_text(template_text: str, context: dict) -> str:
    """Render a Jinja2 template string with *context* and return the result.

    Raises jinja2.TemplateError on render failures (the caller should wrap
    with the source filename for diagnostics).
    """
    from jinja2 import Template, TemplateError

    try:
        return Template(template_text).render(**context)
    except TemplateError as exc:
        raise TemplateError(f"Jinja2 render error: {exc}") from exc


def render_toml_template(path: Path, context: dict) -> dict:
    """Full S3.2 pipeline for one template file.

    context should be {**config_so_far, 'env': dict(os.environ)}.
    Jinja2 render → $VAR expansion → TOML parse.
    """
    from jinja2 import TemplateError

    if not path.exists():
        raise FileNotFoundError(f"Template file not found: {path}")

    raw = path.read_text(encoding="utf-8")
    try:
        rendered = render_jinja2_text(raw, context)
    except TemplateError as exc:
        raise TemplateError(f"Failed to render template {path}: {exc}") from exc

    expanded = expand_env_vars_or_fail(rendered, str(path))
    return parse_toml_string(expanded, str(path))


def _make_render_context(config: dict) -> dict:
    """Build the Jinja2 context: merged config + 'env' = process environment."""
    return {**config, "env": dict(os.environ)}


# ---------------------------------------------------------------------------
# S3.3 – deep merge (tables merge recursively; scalars and lists replace)
# ---------------------------------------------------------------------------

def deep_merge(base: dict, override: dict) -> dict:
    """Return a new dict that is *base* deep-merged with *override*.

    S3.3 semantics: if both values are dicts, merge recursively; otherwise
    the override value replaces the base value (lists and scalars replace,
    NOT concatenate).
    """
    result = dict(base)
    for key, value in override.items():
        if key in result and isinstance(result[key], dict) and isinstance(value, dict):
            result[key] = deep_merge(result[key], value)
        else:
            result[key] = value
    return result


# ---------------------------------------------------------------------------
# S3.3 – chain_dirs (fixes v1 B11: double-root + omit leaf)
# ---------------------------------------------------------------------------

def chain_dirs(repo_root: Path, working_dir: Path) -> list[Path]:
    """Return the inclusive directory chain from *repo_root* down to *working_dir*.

    S3.3 fix (B11): each directory appears exactly once, ordered root→leaf,
    and *working_dir* IS included (v1 omitted it) while *repo_root* is NOT
    duplicated (v1 added it twice).

    Raises ValueError when *working_dir* is not under (or equal to)
    *repo_root*.
    """
    repo_root = Path(repo_root).resolve()
    working_dir = Path(working_dir).resolve()

    # working_dir must be equal to or a descendant of repo_root
    try:
        working_dir.relative_to(repo_root)
    except ValueError:
        raise ValueError(
            f"working_dir {working_dir!s} is not under repo_root {repo_root!s}"
        )

    # Build root→leaf path: repo_root, then each successive part to working_dir
    # working_dir.relative_to(repo_root).parts gives the steps from root to leaf
    relative_parts = working_dir.relative_to(repo_root).parts
    chain: list[Path] = [repo_root]
    current = repo_root
    for part in relative_parts:
        current = current / part
        chain.append(current)

    return chain


# ---------------------------------------------------------------------------
# S3.3 – global config chain render
# ---------------------------------------------------------------------------

def render_global_chain(working_dir: Path, repo_root: Path) -> dict:
    """Render and merge global config from *repo_root* down to *working_dir*.

    For each directory in chain_dirs(repo_root, working_dir):
      - overrides present without defaults → ValueError (v1 rule retained)
      - defaults present → ensure_override_template, render defaults, merge
      - overrides present → render against config-so-far, merge

    Each template is rendered against the config merged SO FAR (v1 behaviour).
    After the chain: write ciu.global.toml at repo_root; empty result → ValueError.

    S3.3 fix (B11): the leaf directory (working_dir) IS processed; repo_root
    is NOT processed twice.
    """
    working_dir = Path(working_dir).resolve()
    repo_root = Path(repo_root).resolve()

    dirs = chain_dirs(repo_root, working_dir)
    merged: dict = {}

    for directory in dirs:
        defaults_path = directory / GLOBAL_CONFIG_DEFAULTS
        overrides_path = directory / GLOBAL_CONFIG_OVERRIDES

        if overrides_path.exists() and not defaults_path.exists():
            raise ValueError(
                f"[ERROR] Found {GLOBAL_CONFIG_OVERRIDES} without "
                f"{GLOBAL_CONFIG_DEFAULTS} in {directory}"
            )

        if defaults_path.exists():
            ensure_override_template(defaults_path, overrides_path)
            defaults_config = render_toml_template(
                defaults_path, _make_render_context(merged)
            )
            merged = deep_merge(merged, defaults_config)

        if overrides_path.exists():
            overrides_config = render_toml_template(
                overrides_path, _make_render_context(merged)
            )
            merged = deep_merge(merged, overrides_config)

    if not merged:
        raise ValueError(
            f"[ERROR] No global configuration found. "
            f"Expected {GLOBAL_CONFIG_DEFAULTS} at repo root {repo_root}."
        )

    output_path = repo_root / GLOBAL_CONFIG_RENDERED
    write_rendered_toml(output_path, merged)
    return merged


# ---------------------------------------------------------------------------
# S3.1 / S3.4 – stack config render
# ---------------------------------------------------------------------------

def render_stack(
    working_dir: Path,
    global_config: dict,
    preserve_state: bool = True,
) -> dict:
    """Render stack templates into ciu.toml and return the merged stack config.

    Pipeline (v1 behaviour, plus S3.4 fix):
      1. Render ciu.defaults.toml.j2 against global_config context.
      2. Render ciu.toml.j2 (overrides) against deep_merge(global, defaults).
      3. Deep-merge: defaults then overrides.
      4. S3.4: if preserve_state and a rendered ciu.toml already exists,
         carry over ONLY its top-level [state] table.
         [secrets] is explicitly NOT carried (S3.4 withdrawal).
      5. Write ciu.toml and return.

    Raises FileNotFoundError when ciu.defaults.toml.j2 is missing.
    """
    working_dir = Path(working_dir).resolve()
    defaults_path = working_dir / STACK_CONFIG_DEFAULTS
    overrides_path = working_dir / STACK_CONFIG_OVERRIDES
    output_path = working_dir / STACK_CONFIG_RENDERED

    if not defaults_path.exists():
        raise FileNotFoundError(
            f"{STACK_CONFIG_DEFAULTS} not found in {working_dir}"
        )

    ensure_override_template(defaults_path, overrides_path)

    defaults_config = render_toml_template(
        defaults_path, _make_render_context(global_config)
    )
    merged_stack: dict = defaults_config

    if overrides_path.exists():
        overrides_context = _make_render_context(
            deep_merge(global_config, defaults_config)
        )
        overrides_config = render_toml_template(overrides_path, overrides_context)
        merged_stack = deep_merge(merged_stack, overrides_config)

    # S3.4: preserve [state] only; explicitly drop [secrets] (withdrawn)
    if preserve_state and output_path.exists():
        existing = parse_toml(output_path)
        state = existing.get("state")
        if isinstance(state, dict):
            merged_stack["state"] = state
        # secrets are explicitly NOT carried (S3.4)

    write_rendered_toml(output_path, merged_stack)
    return merged_stack


# ---------------------------------------------------------------------------
# S3.5 / S3.7 – stack shape validation
# ---------------------------------------------------------------------------

# S3.5: the only reserved top-level key inside stack config is 'state'
_STACK_RESERVED: frozenset[str] = frozenset({"state"})


def validate_stack_shape(stack_config: dict) -> str:
    """Validate stack config shape per S3.5 and S3.7; return the root key.

    S3.5: exactly one non-reserved top-level key ('state' is the only
    reserved key at stack level).  Violations raise ValueError listing
    the offending keys with "[S3.5]".

    S3.7: the root key must not be in RESERVED_GLOBAL_NAMESPACES.
    Violation raises ValueError with "[S3.7]" and a renaming suggestion.
    """
    non_reserved = [k for k in stack_config if k not in _STACK_RESERVED]

    if len(non_reserved) == 0:
        raise ValueError(
            "[S3.5] Stack config has no non-reserved top-level key. "
            "Expected exactly one stack root key."
        )

    if len(non_reserved) > 1:
        keys_str = ", ".join(sorted(non_reserved))
        raise ValueError(
            f"[S3.5] Stack config must have exactly one non-reserved top-level key; "
            f"found: {keys_str}"
        )

    root_key = non_reserved[0]

    if root_key in RESERVED_GLOBAL_NAMESPACES:
        raise ValueError(
            f"[S3.7] Stack root key '{root_key}' collides with a reserved global "
            f"namespace. Rename it (e.g. '{root_key}' → '{root_key}_core')."
        )

    return root_key
