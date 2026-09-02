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
        (also recognizes `local_stack` as a preferred root key name, V8-PREP-4)
  S3.11 [deploy].landscape_id validated on the final merged global config (CIU-36)
  S3.13 ciu.user_tables validated on the final merged global config (V8-PREP-1)
  S3.14 [service.<name>] identity registry shape validated at global scope
        (V8-PREP-3, narrowed: declaration-only — type/location/description,
        no per-service realness sub-tables)

This module is standalone: it does NOT import from engine.py or deploy.py.
Engine.py / deploy.py will import this module in the Wave-3 cutover.

Public API
----------
ENV_VAR_PATTERN                  re.Pattern for $VAR / ${VAR}
expand_env_vars_or_fail(text, source) -> str
parse_toml_string(text, source)  -> dict
parse_toml(path)                 -> dict
write_rendered_toml(path, config)
scan_override_for_secrets(template_text, source)
render_jinja2_text(template_text, context) -> str
render_toml_template(path, context)  -> dict
deep_merge(base, override)       -> dict   (S3.3: tables merge, lists replace)
chain_dirs(repo_root, working_dir) -> list[Path]   (S3.3 fix of B11)
render_global_chain(working_dir, repo_root, *, write_rendered=True, environ=None) -> dict
render_stack(working_dir, global_config, preserve_state=True) -> dict
RESERVED_GLOBAL_NAMESPACES       frozenset[str]   (S3.7 — forbidden stack root keys)
RESERVED_GLOBAL_TABLES           frozenset[str]   (S3.13 — tables CIU reads at global scope)
validate_user_tables(merged)     -> None           (S3.13)
VALID_SERVICE_TYPES              frozenset[str]   (S3.14 — closed [service.<name>].type vocabulary)
validate_service_registry(merged, repo_root) -> None  (S3.14)
validate_stack_shape(stack_config) -> str          (S3.5 + S3.7)
"""

from __future__ import annotations

import os
import re
import tomllib
from pathlib import Path
from typing import Mapping

from .config_constants import (
    GLOBAL_CONFIG_DEFAULTS,
    GLOBAL_CONFIG_INSTANCE_OVERRIDES,
    GLOBAL_CONFIG_OVERRIDES,
    GLOBAL_CONFIG_RENDERED,
    SHIPPED_COMPOSE,
    STACK_CONFIG_DEFAULTS,
    STACK_CONFIG_OVERRIDES,
    STACK_CONFIG_RENDERED,
)
from .workspace_env import generated_facts_document

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
    "governance",
    "infrastructure",
})

# ---------------------------------------------------------------------------
# S3.13 (V8-PREP-1 groundwork) – tables CIU itself reads at GLOBAL scope
# ---------------------------------------------------------------------------
#
# DISTINCT from RESERVED_GLOBAL_NAMESPACES above. That set answers "which
# names may a STACK not pick as its own root key" (S3.7) — a defensive,
# broader set. This set answers a different question: "which top-level
# tables does CIU's OWN code actually read off the rendered GLOBAL config
# today". It backs `validate_user_tables`'s allowlist denominator (S3.13):
# when a consumer opts into `ciu.user_tables`, every top-level key that is
# neither here nor declared in `ciu.user_tables` is an error.
#
# Membership determined by tracing every `render_global_chain` call site and
# every place its result (or a downstream still-global-scope value derived
# from it, e.g. `profile.config`) is read with `.get(<name>)` / `[<name>]`,
# never by guessing from RESERVED_GLOBAL_NAMESPACES's membership — AND (fix
# added at review, ciu-P21) every direct reader of the RENDERED
# `GLOBAL_CONFIG_RENDERED` file that bypasses `render_global_chain` entirely
# (grep `GLOBAL_CONFIG_RENDERED` across src/ciu/*.py: the only such bypass
# is workspace_env.py's own `tomllib.load` of `ciu.global.toml`; every other
# hit is the writer in this module, a log message, or an unused import):
#   - "ciu"        engine.py (auto_connect_network, log_level via ciu.*),
#                  deploy.py (resolve_profiles' ciu.instance.*), worktree.py
#                  (ciu.worktree cap, S16.3), warn_policy.py, provisioning.py
#                  (REGISTRY_VALIDATOR_KEY lookup) — all on global-scope dicts.
#   - "deploy"     engine.py/deploy.py/config_model.py itself
#                  (_validate_deploy_landscape_id, validate_declared_features's
#                  deploy.layouts/deploy.provenance.vendor_images), hosts.py.
#   - "topology"   worktree.py's `_ref_service_port` reads
#                  `ref_global.get("topology")` on a pure global-chain render
#                  (write_rendered=False, no stack merged in).
#   - "vault"      deploy.py's `_is_vault_stack_path(profile.config, ...)` —
#                  profile.config is the profile-resolved GLOBAL config
#                  (deploy_pkg/profiles.py: `config=global_cfg` / a profile
#                  overlay over it), never a stack merge.
#   - "registry"   provisioning.py's `probe_ref`/`validate_registries`, both
#                  explicitly documented ("merged global config") and called
#                  with `profile.config`.
#   - "governance" governance.py's own docstring: "a bare top-level
#                  `[governance]` table in `ciu.global.toml`" is the BASE
#                  layer read directly off the global config.
#   - "service"    docs/SPEC.md S3.8: stacks reference the global
#                  `[service.*]` registry directly in their own TOML
#                  templates (`{{ service.infra.redis_core.redis.name }}`) —
#                  CIU's config model carries this table through to every
#                  render context; there is no per-key Python `.get()` for
#                  it (it is pass-through, not interpreted), but it is a
#                  real, currently-shipped global table, not an invented one.
#                  ciu-P22 (S3.14, V8-PREP-3 narrowed) adds the FIRST actual
#                  Python-side shape check of this table's own top-level
#                  entries (`validate_service_registry`); the per-service
#                  values nested underneath a `[service.<stack>]` entry
#                  remain pure pass-through, unread and unvalidated by CIU.
#   - "auto_generated" S3.9: build_version/build_time/uid/gid/docker_gid are
#                  computed by CIU (`engine.auto_generate_values`) and written
#                  onto the merged config every run — a consumer's OWN
#                  top-level `[auto_generated]` table would be silently
#                  clobbered by that write.
#   - "infrastructure" workspace_env.py:325 —
#                  `config.get("infrastructure", {}).get("public_fqdn", "")`,
#                  read directly off the RENDERED `ciu.global.toml` (a raw
#                  `tomllib.load`, not through `render_global_chain`) inside
#                  `_detect_public_fqdn` (S2.7/CIU-47's PUBLIC_FQDN
#                  derivation). This bypass is exactly why membership here
#                  cannot be determined from `render_global_chain` call
#                  sites alone — see the methodology note above.
#
# Explicitly EXCLUDED, despite being in RESERVED_GLOBAL_NAMESPACES, because
# grepping every call site found no code that reads a literal TOP-LEVEL
# table by that name off the global config (each is either nested under one
# of the tables above, or a STACK-scope-only concept):
#   - "consul"   only ever read nested, as `[registry.consul]`
#                (provisioning.py) — never a top-level `[consul]` table.
#   - "env"      reserved because `env` is the Jinja context key CIU injects
#                itself (`_make_render_context`); `[deploy.env.*]` is a
#                nested sub-table, not a top-level `[env]` global table.
#   - "state"    a STACK-scope reserved key (`_STACK_RESERVED`, S3.4/S3.5)
#                preserved on RE-RENDER of `ciu.toml`; never a top-level
#                table in `ciu.global.toml`.
#   - "secrets"  a STACK-scope concept only (`[<root>.secrets]`, S4.1);
#                S4.1 says global config MUST NOT contain `secrets` tables.
#
# `build` (proposal §1.14's own example lists it as a USER table) is
# deliberately absent — it is exactly the kind of consumer-domain table
# `ciu.user_tables` exists to declare, not a CIU-reserved one.
RESERVED_GLOBAL_TABLES: frozenset[str] = frozenset({
    "ciu",
    "deploy",
    "topology",
    "vault",
    "registry",
    "governance",
    "service",
    "auto_generated",
    "infrastructure",
})

# ---------------------------------------------------------------------------
# S3.2 – env-var expansion
# ---------------------------------------------------------------------------

ENV_VAR_PATTERN: re.Pattern[str] = re.compile(r"\$(\w+)|\$\{([^}]+)\}")


def _split_toml_line_at_comment(line: str) -> tuple[str, str]:
    """Return (value_part, comment_part) for one TOML line.

    Scans the line character-by-character tracking basic-string (double-quote)
    and literal-string (single-quote) state. A ``#`` encountered outside any
    quoted context starts the TOML comment; everything from that ``#`` to the
    end of the line is the comment part. A ``#`` inside a quoted string is part
    of the value, not a comment.

    Multi-line strings (triple-quoted) are intentionally not handled here because
    ``expand_env_vars_or_fail`` processes the fully-rendered post-Jinja2 text and
    the TOML multi-line quoting edge-case is extremely rare in ciu templates.
    Single-line basic/literal strings cover all known real-world ciu templates.

    Returns:
        A 2-tuple ``(value_part, comment_part)`` where ``comment_part`` is the
        ``#``-prefixed comment text (including the ``#``) or an empty string when
        the line has no comment.
    """
    in_basic = False   # inside double-quoted basic string
    in_literal = False  # inside single-quoted literal string
    i = 0
    while i < len(line):
        ch = line[i]
        if in_basic:
            if ch == "\\" and i + 1 < len(line):
                i += 2  # skip escape sequence
                continue
            if ch == '"':
                in_basic = False
        elif in_literal:
            if ch == "'":
                in_literal = False
        else:
            if ch == '"':
                in_basic = True
            elif ch == "'":
                in_literal = True
            elif ch == "#":
                return line[:i], line[i:]
        i += 1
    return line, ""


def expand_env_vars_or_fail(
    raw_text: str, source: str, *, environ: Mapping[str, str] | None = None
) -> str:
    """Expand $VAR / ${VAR} using *environ* (default ``os.environ``); fail-fast
    on missing/empty values.

    Reports ALL missing variable names in a single error (S3.2).

    TOML-aware: env-var tokens that appear inside TOML comment text (everything
    from an unquoted ``#`` to the end of the line) are NOT expanded and do NOT
    cause a missing-variable error.  A ``#`` that appears inside a quoted string
    value is correctly treated as part of the value, not as a comment delimiter.

    *environ* (S16.3): when given, expansion consults ONLY this mapping —
    never ``os.environ`` — so a candidate worktree's own identity can be
    rendered without leaking the caller's ambient identity. ``None``
    (the default) preserves every existing caller's behaviour exactly.
    """
    env_source: Mapping[str, str] = os.environ if environ is None else environ
    missing: set[str] = set()

    def _replace(match: re.Match) -> str:
        var_name = match.group(1) or match.group(2)
        value = env_source.get(var_name)
        if value is None or value == "":
            missing.add(var_name)
            return match.group(0)
        return value

    # Process line-by-line: expand only the non-comment portion of each line.
    expanded_lines: list[str] = []
    for line in raw_text.splitlines(keepends=True):
        # Strip the trailing newline(s) so the comment-split operates on the
        # visible content, then re-attach afterwards.
        eol = ""
        stripped = line
        if stripped.endswith("\r\n"):
            eol = "\r\n"
            stripped = stripped[:-2]
        elif stripped.endswith("\n"):
            eol = "\n"
            stripped = stripped[:-1]
        elif stripped.endswith("\r"):
            eol = "\r"
            stripped = stripped[:-1]

        value_part, comment_part = _split_toml_line_at_comment(stripped)
        expanded_value = ENV_VAR_PATTERN.sub(_replace, value_part)
        # Comment portion is preserved verbatim — no expansion, no error.
        expanded_lines.append(expanded_value + comment_part + eol)

    expanded = "".join(expanded_lines)

    if missing:
        missing_list = ", ".join(sorted(missing))
        raise ValueError(
            f"[ERROR] Missing required environment values in {source}: {missing_list}.\n"
            "[ERROR] Run 'ciu env generate', then export the values with "
            "'eval \"$(ciu env print)\"' before running CIU."
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


# S3.1a — sensitive key name pattern used in the secret scan
_OVERRIDE_SENSITIVE_KEY_RE: re.Pattern[str] = re.compile(
    r"password|passwd|secret|token|api_key|private_key|credential|auth_token|auth_key",
    re.IGNORECASE,
)
_OVERRIDE_TEMPLATE_REF_RE: re.Pattern[str] = re.compile(
    r"\{\{[^}]+\}\}|\$[A-Z_][A-Z0-9_]*|\$\{[^}]+\}"
)


# S3.4a / S2.4.1 — the sensitive KEY-NAME test, applied to the LAST
# `_`-separated component of a key. `api_key` and `private_key` are in S2.4.1's
# own list and both end in `key`, so `key` alone covers them; they are kept
# spelled out below only so the set reads as the spec text does.
_SECRET_SHAPED_LAST_COMPONENTS: frozenset[str] = frozenset({
    "password", "token", "secret", "credential", "passphrase", "key",
})

# The minimum literal length below which a sensitive-named key's value is not
# treated as a credential ("none", "basic", "off"). Shared with S3.1a's
# override scan so the two cannot drift apart.
SECRET_SHAPED_MIN_LENGTH = 8


def is_secret_shaped(key: str, value: object) -> bool:
    """True when *key*/*value* look like a raw credential (S3.4a / S2.4.1).

    The key test is S2.4.1's: the LAST ``_``-separated component of the key is
    one of ``password``/``token``/``secret``/``api_key``/``credential``/
    ``passphrase``/``private_key``/``key``. Anchoring on the last component
    rather than a substring search is what keeps ``token_bucket_size`` and
    ``keyboard_layout`` out of it while still catching ``vault_root_token``.

    The value test is the SAME one :func:`scan_override_for_secrets` already
    applies (they call this predicate's helpers, not a second copy): a literal
    ``str`` of at least :data:`SECRET_SHAPED_MIN_LENGTH` characters that is
    neither a ``{{ … }}``/``$VAR``/``${…}`` reference nor a ``/``-bearing path
    (a Vault KV path, a ``/run/secrets/`` mount, a file path — all of them
    POINTERS to a secret rather than the secret itself).

    Non-string values are never secret-shaped: ``initialized = true`` and
    ``retry_count = 3`` carry no credential no matter what they are called.
    """
    if key.rsplit("_", 1)[-1].lower() not in _SECRET_SHAPED_LAST_COMPONENTS:
        return False
    if not isinstance(value, str):
        return False
    if len(value) < SECRET_SHAPED_MIN_LENGTH:
        return False
    if _OVERRIDE_TEMPLATE_REF_RE.search(value):
        return False
    return "/" not in value


def find_secret_shaped_keys(table: object, prefix: str = "") -> list[str]:
    """Every dotted key path under *table* whose key/value is secret-shaped.

    Walks nested tables because ``persist:'state'`` accepts a dotted path
    (S9.4), so ``[state]`` genuinely can be more than one level deep — a scan
    that only looked at the top level would be trivially side-steppable by
    returning ``vault.root_token`` instead of ``root_token``.

    Returns key PATHS only. A caller reporting one of these must never print
    the value it found (S4.23) — that is the entire point of the finding.
    """
    if not isinstance(table, dict):
        return []
    found: list[str] = []
    for key, value in table.items():
        path = f"{prefix}{key}"
        if isinstance(value, dict):
            found.extend(find_secret_shaped_keys(value, prefix=f"{path}."))
        elif is_secret_shaped(str(key), value):
            found.append(path)
    return found


def scan_override_for_secrets(template_text: str, source: str) -> None:
    """S3.1a — refuse to render a global override that contains raw credentials.

    Checks:
    - PEM key/certificate blocks (``-----BEGIN``).
    - Keys with sensitive names (password, token, secret, …) paired with
      literal string values that are not Jinja2/env-var references.

    Raises ValueError (→ exit 2) listing every violation found.
    Only applied to the global override template (ciu.global.toml.j2).
    """
    violations: list[str] = []
    for lineno, line in enumerate(template_text.splitlines(), 1):
        if "-----BEGIN" in line:
            violations.append(f"line {lineno}: PEM private key or certificate block")
            continue
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        m = re.match(r"^([\w.]+)\s*=\s*[\"']([^\"']*)[\"']", stripped)
        if not m:
            continue
        key, value = m.group(1), m.group(2)
        if not _OVERRIDE_SENSITIVE_KEY_RE.search(key):
            continue
        if _OVERRIDE_TEMPLATE_REF_RE.search(value):
            continue  # safe: {{ env.VAR }} or $VAR reference
        if "/" in value:
            continue  # Vault path or file path reference, not a raw credential
        if len(value) < SECRET_SHAPED_MIN_LENGTH:
            continue  # short literals (e.g., "none", "basic") are not secrets
        violations.append(
            f"line {lineno}: key '{key}' has a literal string value — "
            f"use '{{{{ env.{key.upper()} }}}}' or '$ENV_VAR' instead"
        )
    if violations:
        raise ValueError(
            f"[S3.1a] {source} appears to contain hardcoded credentials:\n"
            + "\n".join(f"  {v}" for v in violations)
            + "\n[S3.1a] Global override templates must use {{ env.VAR }} or $VAR "
            "references for sensitive values. No raw credentials in tracked files."
        )


def render_jinja2_text(template_text: str, context: dict) -> str:
    """Render a Jinja2 template string with *context* and return the result.

    CIU-74: uses ``StrictUndefined`` (via a one-off ``Environment``, not the
    library-default ``Template()`` constructor) so a mistyped or missing leaf
    key raises ``jinja2.UndefinedError`` instead of silently rendering as the
    empty string. ``keep_trailing_newline=True`` preserves a template's own
    trailing newline (the ``Environment``/``Template`` default silently
    strips exactly one) since a rendered TOML/compose/configfile file having
    one fewer trailing newline than its template authored is itself a small
    silent-corruption class this fix closes at the same time.

    Every call site that exposes a ``ciu`` table to the template (S3.12) is
    responsible for keeping ``ciu.instances`` ALWAYS present (defaulting to
    ``{}``) in the context it builds — see ``_make_render_context`` here and
    ``render_compose``/``render_configfiles`` in ``composefile.py`` — so the
    S7.5b-sanctioned ``'x' in ciu.instances`` idiom keeps working under
    ``StrictUndefined`` even when nothing fans out.

    Raises jinja2.TemplateError on render failures (the caller should wrap
    with the source filename for diagnostics).
    """
    from jinja2 import Environment, StrictUndefined, TemplateError

    try:
        env = Environment(undefined=StrictUndefined, keep_trailing_newline=True)
        return env.from_string(template_text).render(**context)
    except TemplateError as exc:
        raise TemplateError(f"Jinja2 render error: {exc}") from exc


def render_toml_template(
    path: Path, context: dict, *, environ: Mapping[str, str] | None = None
) -> dict:
    """Full S3.2 pipeline for one template file.

    context should be {**config_so_far, 'env': dict(os.environ)}.
    Jinja2 render → $VAR expansion → TOML parse.

    *environ* (S16.3) is forwarded verbatim to :func:`expand_env_vars_or_fail`
    — ``None`` (the default) preserves the existing ``os.environ`` behaviour.
    """
    from jinja2 import TemplateError

    if not path.exists():
        raise FileNotFoundError(f"Template file not found: {path}")

    raw = path.read_text(encoding="utf-8")
    try:
        rendered = render_jinja2_text(raw, context)
    except TemplateError as exc:
        raise TemplateError(f"Failed to render template {path}: {exc}") from exc

    expanded = expand_env_vars_or_fail(rendered, str(path), environ=environ)
    return parse_toml_string(expanded, str(path))


def _make_render_context(
    config: dict,
    *,
    environ: Mapping[str, str] | None = None,
    ciu_context: Mapping[str, object] | None = None,
) -> dict:
    """Build the Jinja2 context: merged config + 'env' = the process environment.

    *environ* (S16.3): when given, the Jinja ``env`` context is built from this
    mapping instead of ``os.environ`` — the same override *environ* must also
    be threaded to :func:`render_toml_template` so a candidate worktree's
    template never mixes its own identity with the caller's ambient
    environment. ``None`` (the default) preserves the existing behaviour.

    *ciu_context* (S3.12 / CIU-44): deployment-selection facts
    (``selected_profiles``, ``deployed_stacks``) exposed to templates. They are
    MERGED INTO the config's own ``[ciu]`` table (workspace switches such as
    ``auto_connect_network`` already live there), never replacing it — the two
    fact keys are reserved for CIU. When *ciu_context* is ``None`` the config's
    ``ciu`` table passes through untouched; outside deployment renders the two
    reserved keys are simply absent, so a template referencing them fails
    loudly (Jinja UndefinedError) rather than silently seeing an empty
    selection.

    CIU-74 / S7.5b: ``ciu.instances`` is the one reserved fact that is the
    OPPOSITE of ``selected_profiles``/``deployed_stacks`` above — S7.5b
    sanctions ``'svc' in ciu.instances`` as the fan-out membership test, and
    that idiom must keep working even when nothing fans out. So whenever a
    ``ciu`` table is exposed at all (``ciu_context is not None``), it always
    carries an ``instances`` key, defaulting to ``{}`` when no service
    resolved a fan-out count > 1 — a defined-but-empty fact, never an
    undefined name, so the membership test stays legal under
    ``StrictUndefined`` instead of raising.
    """
    context = {**config, "env": dict(os.environ if environ is None else environ)}
    if ciu_context is not None:
        merged_ciu = dict(context.get("ciu") or {})
        merged_ciu.update(ciu_context)
        merged_ciu.setdefault("instances", {})
        context["ciu"] = merged_ciu
    return context


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

def render_global_chain(
    working_dir: Path,
    repo_root: Path,
    *,
    write_rendered: bool = True,
    environ: Mapping[str, str] | None = None,
    ciu_context: Mapping[str, object] | None = None,
) -> dict:
    """Render and merge global config from *repo_root* down to *working_dir*.

    For each directory in chain_dirs(repo_root, working_dir):
      - overrides present without defaults → ValueError (v1 rule retained)
      - defaults present → render defaults and merge
      - overrides present → secret scan (S3.1a), render, merge

    S3.1a: the global override (ciu.global.toml.j2) is committed and sparse.
    CIU never auto-creates it from defaults. If absent, defaults apply only.
    The override is scanned for raw credentials before rendering; any violation
    aborts with exit 2.

    Each template is rendered against the config merged SO FAR (v1 behaviour).
    After the committed chain, an optional sparse, gitignored
    ``ciu.global.instance.toml.j2`` at *repo_root* is rendered and merged, then
    the CIU-owned ``ciu.instance.generated.toml`` (plain TOML, never rendered)
    is merged last of all. Both are the durable local configuration layer for
    one checkout/instance and neither is searched for in parent/intermediate
    directories. Then write ciu.global.toml at repo_root; empty result →
    ValueError.

    S3.3 fix (B11): the leaf directory (working_dir) IS processed; repo_root
    is NOT processed twice.

    *write_rendered* (S16.3): ``True`` (the default) preserves every existing
    caller's behaviour — the merged result is written to
    ``<repo_root>/ciu.global.toml``. ``False`` returns the same merged mapping
    without writing anything, for a read-only policy probe (S16.3's worktree
    capacity check) that must never race or clobber the real rendered output.

    *environ* (S16.3): ``None`` (the default) preserves every existing
    caller's behaviour — every template in the chain renders against
    ``os.environ`` for both the Jinja ``env`` context and ``$VAR`` expansion.
    A supplied mapping is used for BOTH in its place at every template in the
    chain, so a worktree candidate's own identity can be rendered without
    ever consulting the calling process's ambient environment.
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
            defaults_config = render_toml_template(
                defaults_path,
                _make_render_context(
                    merged, environ=environ, ciu_context=ciu_context
                ),
                environ=environ,
            )
            merged = deep_merge(merged, defaults_config)

        if overrides_path.exists():
            raw_override = overrides_path.read_text(encoding="utf-8")
            scan_override_for_secrets(raw_override, str(overrides_path))
            overrides_config = render_toml_template(
                overrides_path,
                _make_render_context(
                    merged, environ=environ, ciu_context=ciu_context
                ),
                environ=environ,
            )
            merged = deep_merge(merged, overrides_config)

    # S3.1b — the gitignored per-checkout overlay, merged LAST. Read
    # unconditionally by exact path, with NO S16 instance-record gating, so the
    # primary/main checkout is covered identically to a worktree instance.
    instance_overrides_path = repo_root / GLOBAL_CONFIG_INSTANCE_OVERRIDES
    if instance_overrides_path.exists():
        raw_override = instance_overrides_path.read_text(encoding="utf-8")
        scan_override_for_secrets(raw_override, str(instance_overrides_path))
        instance_overrides = render_toml_template(
            instance_overrides_path,
            _make_render_context(merged, environ=environ),
            environ=environ,
        )
        merged = deep_merge(merged, instance_overrides)

    # S3.1b — the CIU-owned `[ciu.instance.generated]` identity facts
    # (CIU-60), merged at the very end of the global chain. Until ciu-P47 this
    # table was embedded in the overlay immediately above and arrived through
    # that same merge; splitting it into its own file changes WHERE the bytes
    # live, not what a template sees — `{{ ciu.instance.generated.<key> }}`
    # resolves exactly as before, from the same position in the S3.3 chain,
    # and there is still deliberately no bespoke Jinja context injection for
    # these facts anywhere.
    #
    # Merged AFTER the overlay, not before, which preserves the pre-split
    # precedence: the surgical upsert made the CIU-written table the last word
    # on those six keys inside that file, so anything an operator writes under
    # `[ciu.instance.generated]` in their own overlay is still overridden by
    # the derived fact rather than silently winning over it.
    #
    # No `scan_override_for_secrets` here, unlike the overlay: S3.1a's scan
    # exists to stop a HUMAN committing a raw credential into a hand-authored
    # override. This file has no human author — CIU writes all of it, from six
    # derived non-secret facts — so a scan could only ever produce a false
    # refusal on a repo path or hostname that happened to look secret-shaped.
    # `generated_facts_document`, NOT `read_generated_facts`: this is the merge
    # layer, and it must stay exactly as tolerant as the overlay layer it was
    # carved out of. The identity reader's extra strictness (every fact a
    # string) belongs to identity reads; enforcing it here would turn CIU-80's
    # `identity_unreadable` degradation at STEP 12 into a traceback out of the
    # render, which is the opposite of what that flag exists for. See that
    # function's own docstring for the full split.
    #
    # The ORDER of this line relative to the overlay block above is pinned by
    # `test_the_derived_fact_outranks_the_same_key_hand_written_in_the_overlay`
    # (ciu-P47 review, B4). Moving it above that block is a silent behaviour
    # change — an operator's hand-written `[ciu.instance.generated]` would then
    # shadow the derived facts — and nothing else in the suite notices.
    merged = deep_merge(merged, generated_facts_document(repo_root))

    if not merged:
        raise ValueError(
            f"[ERROR] No global configuration found. "
            f"Expected {GLOBAL_CONFIG_DEFAULTS} at repo root {repo_root}."
        )

    # S3.11 (CIU-36): validate the FINAL merged config, once, after every layer
    # (committed chain + instance overlay + generated facts) — never per chain
    # directory, so a
    # later layer that corrects an earlier bad value is honored, and an
    # overlay-set value is covered too.
    _validate_deploy_landscape_id(merged)
    # S3.13 (V8-PREP-1): same timing/reasoning as S3.11 immediately above —
    # once, on the FINAL merged config, so a later layer (or the worktree
    # overlay) can correct an earlier layer's declaration.
    validate_user_tables(merged)
    # S3.14 (V8-PREP-3 narrowed): same timing/reasoning again — the global
    # `[service.<name>]` identity registry is validated once, on the FINAL
    # merged config, so a later layer can correct an earlier declaration.
    # Needs *repo_root* (unlike the two checks above) to confirm a CIU/COMPOSE
    # entry's `location` names a real directory containing the right marker
    # file — a purely in-memory shape check cannot see that.
    validate_service_registry(merged, repo_root)

    if write_rendered:
        output_path = repo_root / GLOBAL_CONFIG_RENDERED
        write_rendered_toml(output_path, merged)
    return merged


# ---------------------------------------------------------------------------
# S3.11 – [deploy].landscape_id validation (CIU-36)
# ---------------------------------------------------------------------------

_LANDSCAPE_ID_RE: re.Pattern[str] = re.compile(r"^[a-z][a-z0-9-]{0,62}$")


def _validate_deploy_landscape_id(merged: dict) -> None:
    """S3.11 — validate ``[deploy].landscape_id`` on the FINAL merged global config.

    The key is consumer-opt-in: absence is legal. When present it MUST be a
    DNS-label-safe slug (``^[a-z][a-z0-9-]{0,62}$``) — the shared identity of
    one deployment landscape, which a consumer renders its Consul KV root
    (``dstdns/<landscape_id>/...``) and mesh ACL tags from.

    Runs once, on the fully merged config (including the instance overlay),
    never per chain directory — a leaf/override that corrects an earlier
    layer's value must be honored, and an overlay-set value is covered too.

    Raises ValueError naming the key and the pattern on violation.
    """
    deploy = merged.get("deploy")
    if not isinstance(deploy, dict):
        return
    landscape_id = deploy.get("landscape_id")
    if landscape_id is None:
        return
    if not isinstance(landscape_id, str) or not _LANDSCAPE_ID_RE.fullmatch(landscape_id):
        raise ValueError(
            f"[S3.11] [deploy].landscape_id must match '^[a-z][a-z0-9-]{{0,62}}$' "
            f"(a DNS-label-safe slug); got {landscape_id!r}."
        )


# ---------------------------------------------------------------------------
# S3.13 (V8-PREP-1 groundwork) – ciu.user_tables validation
# ---------------------------------------------------------------------------

_USER_TABLE_NAME_RE: re.Pattern[str] = re.compile(r"^[A-Za-z0-9_-]+$")


def validate_user_tables(merged: dict) -> None:
    """S3.13 (V8-PREP-1) — validate ``ciu.user_tables`` on the FINAL merged
    global config, once (same timing/reasoning as :func:`_validate_deploy_landscape_id`
    directly above: after the committed chain and the instance overlay, so a
    later layer that corrects an earlier declaration is honored).

    ``ciu.user_tables`` is consumer opt-in: absence is a complete no-op — a
    config that never declares it sees ZERO behavior change, regardless of
    what top-level tables it has. This is the additive V8-PREP-1 groundwork
    step only; the eventual V8 breaking step (deferred, not shipped here) is
    defaulting ``ciu.user_tables`` to empty so every undeclared table is
    always an error.

    When present, ``ciu.user_tables`` MUST be a list of strings, each
    matching ``^[A-Za-z0-9_-]+$`` (TOML bare-key charset), with no duplicate
    and no member already in :data:`RESERVED_GLOBAL_TABLES` (CIU already
    reserves those names — declaring one in ``ciu.user_tables`` would be
    redundant/misleading, so it is rejected rather than silently accepted).
    Each malformed condition raises its own tagged ``ValueError`` naming the
    offending member(s) collectively (never partial — mirrors
    :func:`expand_env_vars_or_fail`'s collective-error style).

    Once the declaration itself is well-formed, every top-level key in
    *merged* that is neither in :data:`RESERVED_GLOBAL_TABLES` nor listed in
    ``ciu.user_tables`` is a single collective ``ValueError`` naming every
    offending key.
    """
    ciu_table = merged.get("ciu")
    if not isinstance(ciu_table, dict):
        return
    declared = ciu_table.get("user_tables")
    if declared is None:
        return

    if not isinstance(declared, list) or not all(isinstance(m, str) for m in declared):
        raise ValueError(
            f"[S3.13] ciu.user_tables must be a list of strings; got {declared!r}."
        )

    malformed = [m for m in declared if not _USER_TABLE_NAME_RE.fullmatch(m)]
    if malformed:
        raise ValueError(
            "[S3.13] ciu.user_tables member(s) must match "
            f"'{_USER_TABLE_NAME_RE.pattern}': "
            + ", ".join(repr(m) for m in malformed)
        )

    seen: set[str] = set()
    duplicates: set[str] = set()
    for m in declared:
        if m in seen:
            duplicates.add(m)
        seen.add(m)
    if duplicates:
        raise ValueError(
            "[S3.13] ciu.user_tables has duplicate member(s): "
            + ", ".join(sorted(duplicates))
        )

    collisions = [m for m in declared if m in RESERVED_GLOBAL_TABLES]
    if collisions:
        raise ValueError(
            "[S3.13] ciu.user_tables member(s) collide with CIU's own "
            "reserved global tables: " + ", ".join(sorted(collisions))
            + ". Remove them from ciu.user_tables — CIU already reserves these names."
        )

    allowed = RESERVED_GLOBAL_TABLES | set(declared)
    unknown = sorted(k for k in merged if k not in allowed)
    if unknown:
        raise ValueError(
            "[S3.13] Unknown top-level table(s) in the global config: "
            + ", ".join(unknown)
            + ". Every top-level table must be a CIU-reserved global table "
            "(RESERVED_GLOBAL_TABLES) or declared in ciu.user_tables."
        )


# ---------------------------------------------------------------------------
# S3.14 (V8-PREP-3 narrowed groundwork) – [service.<name>] identity registry
# ---------------------------------------------------------------------------
#
# Declaration-only, per docs/CIU-V8-TESTING-GATE-PROPOSAL.md §1.15/§3.1
# (rev 1.4, commit 4440c17e — the two-level stack.service hierarchy). This
# validates ONLY the stack-level identity fields (`type`/`location`/
# `description`) sitting directly under `[service.<stack_name>]`. The
# per-service REALNESS sub-table layer described in §3.2
# (`[service.<stack>.<svc>.<level>]`: live/mock/owned-seeded/simulated) is
# deliberately NOT accepted here — see the "any other key... REJECTED"
# branch below. Accepting-and-ignoring that layer now would let a consumer
# ship configs the real V8 rewrite could not safely reinterpret without a
# silent-migration trap; refusing it instead keeps that layer free for V8
# to define on its own terms.

#: S3.14 — the only three keys accepted directly under `[service.<name>]`.
_SERVICE_ALLOWED_KEYS: frozenset[str] = frozenset({"type", "location", "description"})

#: S3.14 — stack types whose `location` is REQUIRED, and the marker file
#: `location` must contain for each (reusing config_constants.py's existing
#: filename constants rather than re-hardcoding the strings, per the
#: handoff's escalate_if).
_SERVICE_TYPE_REQUIRED_FILE: dict[str, str] = {
    "CIU": STACK_CONFIG_DEFAULTS,
    "COMPOSE": SHIPPED_COMPOSE,
}

#: S3.14 — stack types whose `location` is FORBIDDEN (the entity IS the
#: service; there is nothing on disk for CIU to deploy).
_SERVICE_TYPES_FORBIDDING_LOCATION: frozenset[str] = frozenset({"EXTERNAL", "IN_PROCESS"})

#: S3.14 — the closed `[service.<name>].type` vocabulary (proposal §1.15).
VALID_SERVICE_TYPES: frozenset[str] = frozenset(
    set(_SERVICE_TYPE_REQUIRED_FILE) | _SERVICE_TYPES_FORBIDDING_LOCATION
)


def validate_service_registry(merged: dict, repo_root: Path) -> None:
    """S3.14 (V8-PREP-3 narrowed) — validate the global `[service.<name>]`
    identity registry on the FINAL merged global config, once (same timing/
    reasoning as :func:`validate_user_tables` immediately above).

    ``[service.*]`` is consumer opt-in: absence (or an empty table) is a
    complete no-op — this function's body is not entered at all, so a
    config that never declares a `[service.<name>]` entry sees ZERO
    behavior change.

    When present, each `[service.<stack_name>]` entry MUST be a TOML table
    containing ONLY the keys in :data:`_SERVICE_ALLOWED_KEYS`
    (``type``, ``location``, ``description``). Any other key — including a
    nested table, which is exactly how the deferred per-service realness
    layer (§3.2) would appear — is REJECTED naming the stack and the
    offending key(s); this deliberately does NOT accept-and-ignore that
    layer (see the module comment above :data:`_SERVICE_ALLOWED_KEYS`).

    - ``type`` is REQUIRED and MUST be one of :data:`VALID_SERVICE_TYPES`
      (``CIU``, ``COMPOSE``, ``EXTERNAL``, ``IN_PROCESS``).
    - ``location`` is REQUIRED for ``CIU``/``COMPOSE`` and MUST name a
      repo-relative directory containing that type's marker file
      (:data:`_SERVICE_TYPE_REQUIRED_FILE`) — ``ciu.defaults.toml.j2`` for
      ``CIU``, ``docker-compose.yml`` for ``COMPOSE``. ``location`` is
      FORBIDDEN (a config error, not silently ignored) for
      ``EXTERNAL``/``IN_PROCESS`` — those types have no stack directory;
      the entity itself IS the service.
    - ``description``, when present, MUST be a string.

    Each malformed condition raises its own tagged ``ValueError`` naming the
    offending stack (mirrors :func:`validate_user_tables`'s per-condition,
    collective-error style — one clear failure per stack entry, not a single
    error covering the whole table, since each stack's shape is independent).
    """
    service_table = merged.get("service")
    if not isinstance(service_table, dict) or not service_table:
        return

    for stack_name, entry in service_table.items():
        if not isinstance(entry, dict):
            raise ValueError(
                f"[S3.14] [service.{stack_name}] must be a TOML table; "
                f"got {type(entry).__name__}."
            )

        unknown = sorted(k for k in entry if k not in _SERVICE_ALLOWED_KEYS)
        if unknown:
            raise ValueError(
                f"[S3.14] [service.{stack_name}] has unrecognized key(s): "
                + ", ".join(unknown)
                + ". Only 'type', 'location', and 'description' are accepted "
                "at this scope — the per-service realness sub-table layer "
                "(V8 proposal §3.2) is deliberately reserved, not accepted, "
                "so V8 can define it later without a silent-acceptance "
                "migration trap."
            )

        service_type = entry.get("type")
        # isinstance-guarded BEFORE the membership test: an inline-table or
        # array `type` value (e.g. `type = ["CIU"]`) is unhashable, and
        # `x not in a_frozenset` hashes `x` — an unguarded membership test
        # would raise a bare TypeError instead of this function's own
        # tagged ValueError.
        if not isinstance(service_type, str) or service_type not in VALID_SERVICE_TYPES:
            raise ValueError(
                f"[S3.14] [service.{stack_name}].type must be one of "
                f"{sorted(VALID_SERVICE_TYPES)}; got {service_type!r}."
            )

        location = entry.get("location")
        if service_type in _SERVICE_TYPES_FORBIDDING_LOCATION:
            if location is not None:
                raise ValueError(
                    f"[S3.14] [service.{stack_name}].location is FORBIDDEN "
                    f"for type {service_type!r}; got {location!r}."
                )
        else:
            if not isinstance(location, str) or not location:
                raise ValueError(
                    f"[S3.14] [service.{stack_name}].location is required "
                    f"for type {service_type!r}; got {location!r}."
                )
            required_file = _SERVICE_TYPE_REQUIRED_FILE[service_type]
            if not (Path(repo_root) / location / required_file).is_file():
                raise ValueError(
                    f"[S3.14] [service.{stack_name}].location {location!r} "
                    f"must name a directory containing {required_file!r} "
                    f"(type {service_type!r})."
                )

        description = entry.get("description")
        if description is not None and not isinstance(description, str):
            raise ValueError(
                f"[S3.14] [service.{stack_name}].description must be a "
                f"string; got {type(description).__name__}."
            )


# ---------------------------------------------------------------------------
# S3.1 / S3.4 – stack config render
# ---------------------------------------------------------------------------

def render_stack(
    working_dir: Path,
    global_config: dict,
    preserve_state: bool = True,
    ciu_context: Mapping[str, object] | None = None,
) -> dict:
    """Render stack templates into ciu.toml and return the merged stack config.

    The per-stack override mirrors the global override model exactly (S3.1a):
    ``ciu.toml.j2`` is an **optional, committed, sparse** override that CIU
    **never auto-creates** from defaults. CIU used to copy
    ``ciu.defaults.toml.j2`` → ``ciu.toml.j2`` on first run; that generated
    full intermediate then shadowed later edits to the committed defaults and
    survived ``clean`` (CIU-8). With no generated intermediate, there is nothing
    to go stale.

    Pipeline (plus S3.4 fix):
      1. Render ciu.defaults.toml.j2 against global_config context.
      2. If ciu.toml.j2 exists: secret-scan it (S3.1a), render against
         deep_merge(global, defaults), then deep-merge over defaults.
      3. S3.4: if preserve_state and a rendered ciu.toml already exists,
         carry over ONLY its top-level [state] table.
         [secrets] is explicitly NOT carried (S3.4 withdrawal).
      4. Write ciu.toml and return.

    Raises FileNotFoundError when ciu.defaults.toml.j2 is missing.
    Raises ValueError when ciu.toml.j2 contains a raw credential (S3.1a).
    """
    working_dir = Path(working_dir).resolve()
    defaults_path = working_dir / STACK_CONFIG_DEFAULTS
    overrides_path = working_dir / STACK_CONFIG_OVERRIDES
    output_path = working_dir / STACK_CONFIG_RENDERED

    if not defaults_path.exists():
        raise FileNotFoundError(
            f"{STACK_CONFIG_DEFAULTS} not found in {working_dir}"
        )

    defaults_config = render_toml_template(
        defaults_path, _make_render_context(global_config, ciu_context=ciu_context)
    )
    merged_stack: dict = defaults_config

    if overrides_path.exists():
        # Committed sparse override — same constraints as the global override
        # (S3.1a): scanned for raw credentials before rendering.
        raw_override = overrides_path.read_text(encoding="utf-8")
        scan_override_for_secrets(raw_override, str(overrides_path))
        overrides_context = _make_render_context(
            deep_merge(global_config, defaults_config), ciu_context=ciu_context
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

    V8-PREP-4 (docs/CIU-V8-TESTING-GATE-PROPOSAL.md §1.16): ``local_stack``
    is the V8-preferred stack root key name — a stack MAY name its root
    table ``[local_stack]`` instead of a directory-derived name. It requires
    NO special-casing here: ``local_stack`` is deliberately absent from
    ``RESERVED_GLOBAL_NAMESPACES`` (membership there would make it
    FORBIDDEN, the opposite of "preferred") and from ``RESERVED_GLOBAL_TABLES``
    (S3.13 — that set is global-scope, `local_stack` is a stack-scope name),
    so it flows through every check below identically to any other
    non-reserved root key and is returned exactly the same way. A stack that
    keeps its existing directory-derived root key is completely unaffected —
    its own name still hits the S3.7 collision check unchanged. Every
    downstream reader of the returned root key (secret discovery, hooks,
    configfile, governance) already takes it as an opaque string parameter,
    so it behaves identically to any other stack name.
    """
    if not isinstance(stack_config, dict):
        raise ValueError(
            "[S3.5] Stack config must be a TOML table, "
            f"got {type(stack_config).__name__}."
        )

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

    # S0 defines the root as a top-level TOML table.  Do not let a scalar or
    # list masquerade as a stack root merely because it is the only key.
    if not isinstance(stack_config[root_key], dict):
        raise ValueError(
            f"[S3.5] Stack root key '{root_key}' must be a TOML table, "
            f"got {type(stack_config[root_key]).__name__}."
        )

    # S3.4 likewise defines [state] as a table.  Its presence must not hide a
    # malformed scalar/list from the shape validator.
    if "state" in stack_config and not isinstance(stack_config["state"], dict):
        raise ValueError(
            "[S3.5] Reserved top-level key 'state' must be a TOML table, "
            f"got {type(stack_config['state']).__name__}."
        )

    if root_key in RESERVED_GLOBAL_NAMESPACES:
        raise ValueError(
            f"[S3.7] Stack root key '{root_key}' collides with a reserved global "
            f"namespace. Rename it (e.g. '{root_key}' → '{root_key}_core')."
        )

    return root_key


# ---------------------------------------------------------------------------
# Provisioning: requires / provides grammar validation
# ---------------------------------------------------------------------------

VALID_REF_KINDS = frozenset({"vault", "pg", "minio", "consul", "stack"})
_REF_RE = re.compile(
    r"(?:vault:secret/(.+)"                       # vault:secret/<path>
    r"|pg:(?:role|db|schema)/[a-zA-Z0-9_-]+"      # pg:role/<n>, pg:db/<n>, or pg:schema/<n>
    r"|minio:user/[a-zA-Z0-9_-]+"                 # minio:user/<name>
    r"|consul:token/[a-zA-Z0-9_-]+"               # consul:token/<svc>
    r"|stack:[a-zA-Z0-9_/-]+:(?:healthy|completed))"  # stack:<name>:healthy|completed
)


def validate_provisioning_ref(ref: str) -> None:
    """Validate a single typed provisioning ref string.

    Raises ValueError with a clear message if malformed.
    """
    # fullmatch is deliberate: a provisioning ref is an identifier, not a
    # prefix.  ``match`` previously accepted e.g. ``pg:role/app trailing``;
    # the later provisioning parser then rejected it, producing an avoidable
    # late failure after the supposed validation pass.
    if not _REF_RE.fullmatch(ref):
        if ":" not in ref:
            raise ValueError(
                f"[ERROR] Malformed provisioning ref {ref!r}: missing kind prefix "
                f"(expected <kind>:<selector>, e.g. vault:secret/path or pg:role/name)"
            )
        kind = ref.split(":", 1)[0]
        if kind not in VALID_REF_KINDS:
            raise ValueError(
                f"[ERROR] Unknown ref kind {kind!r} in {ref!r}. "
                f"Valid kinds: {', '.join(sorted(VALID_REF_KINDS))}"
            )
        raise ValueError(
            f"[ERROR] Malformed provisioning ref {ref!r}: does not match any valid pattern. "
            f"Examples: vault:secret/db/pass, pg:role/myuser, pg:db/mydb, pg:schema/myschema, "
            f"minio:user/worker, consul:token/myapp, stack:db-core:healthy, "
            f"stack:db-core:completed"
        )


def validate_stack_provisioning(stack_config: dict, source: str = "<unknown>") -> None:
    """Validate requires/provides lists in a stack config dict.

    Checks that:
    - requires and provides, if present, are lists of strings
    - each string matches the typed-ref grammar
    - provides_container (CIU-89), if present, is a table whose keys are each
      an exact string already in this stack's own `provides` list AND of
      kind `pg`/`minio` (the only kinds `_resolve_probe_container` is ever
      reached for), and whose values are non-empty strings (S13.2)

    Raises ValueError listing ALL violations (never partial).
    Source is used in error messages.
    """
    # Local import: `provisioning.py` never imports this module (confirmed),
    # so this is safe at module scope too, but kept function-local to match
    # this file's existing convention for a cross-module reach used by only
    # one function here (see `validate_declared_features`'s own docstring on
    # why its imports are function-local).
    from . import provisioning as provisioning_pkg

    violations: list[str] = []
    root_key = validate_stack_shape(stack_config)
    root_section = stack_config[root_key]

    for field in ("requires", "provides"):
        # S13.1: declarations live only in the single validated root table.
        # Looking through arbitrary nested tables could validate a service's
        # accidental declaration while silently ignoring the root declaration.
        val = root_section.get(field)

        if val is None:
            continue
        if not isinstance(val, list):
            violations.append(
                f"[{source}] '{field}' must be a list of strings, got {type(val).__name__}"
            )
            continue
        for i, item in enumerate(val):
            if not isinstance(item, str):
                violations.append(
                    f"[{source}] '{field}[{i}]' must be a string, got {type(item).__name__}"
                )
                continue
            try:
                validate_provisioning_ref(item)
            except ValueError as exc:
                violations.append(str(exc))

    # CIU-89 (S13.2): `provides_container` is a sibling override table, keyed
    # by an exact `provides` ref string, that names the LITERAL compose
    # service key `_resolve_probe_container` should use for that one ref
    # instead of guessing it from the providing stack's own path basename.
    # An entry for a ref the stack doesn't even declare in `provides` is a
    # config error, not silently ignored (S3 "defaults are hazards" —
    # accepting it would let a typo'd/stale override sit there looking live
    # while never actually being consulted, since _resolve_probe_container
    # only ever looks it up BY a ref that already resolved through
    # provider_index()).
    provides_container = root_section.get("provides_container")
    if provides_container is not None:
        if not isinstance(provides_container, dict):
            violations.append(
                f"[S13.2] [{source}] 'provides_container' must be a table, got "
                f"{type(provides_container).__name__}"
            )
        else:
            # Adversarial review fix: `val` from the requires/provides loop
            # above is not reused here on purpose. A `provides` entry that is
            # itself a list (e.g. `provides = [["pg:db/x"]]`) is `isinstance
            # ..., list` but unhashable — `set(declared_provides)` on it
            # raised an uncaught `TypeError` that escaped every `except
            # ValueError` handler in the call chain (engine.py's exit-code
            # mapping only catches ValueError -> 2), silently changing the
            # CLI's exit code from a clean 2 to 1 for a config `main` already
            # reports as a normal, well-worded [S13.2]/requires-provides
            # violation. Filtering to strings only is safe: a non-string
            # `provides` entry is already reported by the loop above, and a
            # `provides_container` key can never legitimately equal one
            # anyway (TOML keys are always strings).
            provides_list = root_section.get("provides")
            declared_provides = (
                {p for p in provides_list if isinstance(p, str)}
                if isinstance(provides_list, list)
                else set()
            )
            for key, value in provides_container.items():
                if not isinstance(key, str) or key not in declared_provides:
                    violations.append(
                        f"[S13.2] [{source}] 'provides_container' key {key!r} is not "
                        "in this stack's own 'provides' list"
                    )
                    continue
                if not isinstance(value, str) or not value:
                    violations.append(
                        f"[S13.2] [{source}] 'provides_container[{key!r}]' must be a "
                        f"non-empty string, got {value!r}"
                    )
                # Adversarial review fix: `_resolve_probe_container` (the
                # ONLY consumer of `provides_container`) is reached only from
                # `_probe_pg`/`_probe_minio` — an entry keyed to a
                # vault:/consul:/stack: ref is accepted here and then
                # silently never consulted by anything, exactly the
                # "looking live while never being consulted" failure this
                # validation exists to prevent for an undeclared ref. `key`
                # is already confirmed to be a string equal to a `provides`
                # entry above; if that entry is itself malformed (fails
                # `parse_ref`), the requires/provides loop above already
                # reports it — don't double-report, just skip the kind gate
                # for it.
                try:
                    ref_kind = provisioning_pkg.parse_ref(key).kind
                except ValueError:
                    continue
                if ref_kind not in ("pg", "minio"):
                    violations.append(
                        f"[S13.2] [{source}] 'provides_container' key {key!r} has kind "
                        f"{ref_kind!r} — provides_container only applies to pg:/minio: "
                        "refs; _resolve_probe_container is never consulted for any "
                        "other ref kind"
                    )

    if violations:
        raise ValueError(
            f"[ERROR] Stack provisioning validation failed for {source}:\n"
            + "\n".join(f"  {v}" for v in violations)
        )


# ---------------------------------------------------------------------------
# QOL-11 — eager S11 validation for declared layouts/exec-targets/vendor_images
# ---------------------------------------------------------------------------


def validate_declared_features(global_cfg: dict, hosts_cfg: dict) -> None:
    """Eagerly validate declared layouts (S7.5c), exec-targets (S16.7), and
    ``[deploy.provenance].vendor_images`` (S17.5) shape, on EVERY render
    path — not only when the specific feature's own command runs this run
    (QOL-11: a malformed globally-declared layout/exec-target/vendor_images
    is a real defect regardless of whether this invocation touches it).

    Reuses the existing, already-correct validators
    (``deploy_pkg.layouts.resolve_layout``,
    ``worktree.resolve_exec_targets_config``) rather than reimplementing
    their checks; each step's own exception propagates unmodified — this
    function never catches and rewraps.

    ``deploy_pkg.layouts`` is imported function-LOCAL here, not at module
    scope: ``deploy_pkg/layouts.py`` imports ``from .profiles import
    resolve_profiles`` and ``deploy_pkg/profiles.py`` imports ``from
    ..config_model import deep_merge`` — a module-scope
    ``from .deploy_pkg.layouts import resolve_layout`` in *this* module
    would be circular (``config_model -> deploy_pkg.layouts ->
    deploy_pkg.profiles -> config_model``, reaching for ``deep_merge``
    before this module has finished defining it) and would raise
    ``ImportError`` the moment ``config_model.py`` itself is first
    imported (reproduced directly while writing this). ``worktree`` is
    imported function-LOCAL too, for consistency alongside the layouts
    import above: ``worktree.py`` does ``from . import config_model`` at
    its own module scope, but only ever reads ``config_model.<name>``
    inside function bodies, never at worktree.py's own module scope — so a
    module-scope import of ``worktree`` here does not actually reproduce
    the same failure (verified), but keeping it function-local costs
    nothing and avoids relying on that being true forever.
    """
    # Step 1 (S7.5c): every declared layout must resolve cleanly. An
    # empty/absent layouts table is a no-op (zero iterations).
    from .deploy_pkg.layouts import resolve_layout

    layouts_table = global_cfg.get("deploy", {}).get("layouts", {})
    for name in layouts_table:
        resolve_layout(global_cfg, hosts_cfg, name)

    # Step 2 (S16.7): the whole [ciu.worktree.exec_targets] table, if any.
    # resolve_exec_targets_config is no-op-safe when the table is
    # absent/empty (returns {}), so this is called unconditionally.
    from . import worktree

    worktree.resolve_exec_targets_config(global_cfg)

    # Step 3 (S17.5): [deploy.provenance].vendor_images shape. Absent key is
    # a no-op. A bare string is explicitly rejected before any iteration —
    # `for v in "nginx"` would otherwise silently "validate" four
    # single-character non-empty strings.
    vendor_images = global_cfg.get("deploy", {}).get("provenance", {}).get("vendor_images")
    if vendor_images is not None:
        if not isinstance(vendor_images, list):
            raise ValueError(
                "[S17.5] [deploy.provenance] vendor_images must be a list of "
                f"non-empty strings, got {type(vendor_images).__name__}."
            )
        for i, item in enumerate(vendor_images):
            if not isinstance(item, str) or not item:
                raise ValueError(
                    f"[S17.5] [deploy.provenance] vendor_images[{i}] must be a "
                    f"non-empty string, got {item!r}."
                )
