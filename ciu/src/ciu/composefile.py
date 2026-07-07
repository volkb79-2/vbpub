"""Compose-template rendering, secret leak prevention, overlay & configfile
generation — CIU v2.

Normative contract: docs/SPEC.md
  S4.17  overlay declares every secret as ``secrets.<name>.file`` (physical path)
         plus configfile mounts
  S4.19  ``expose_env`` secrets injected into the compose process env
  S4.20  warn on declared-but-unconsumed; abort on undeclared reference
  S4.21  compose-template render context replaces secret values with guards;
         any stringify aborts naming the secret + ``secrets:``/``/run/secrets``
  S4.22  leak scan rendered compose text for every resolved value len >= 8
  S4.23  ``--print-context``/logs render secrets as ``<secret:<name>>``
  S5     configfile mounts: render ``[<root>.<svc>.configfile.<name>]`` templates
         with ``secret('<name>')`` as the only path to a secret value
  S8.1   overlay omitted only when no secrets, no configfiles, and no
         governance injections
  S8.2   compose process env = base/os.environ + PWD + COMPOSE_PROFILES +
         expose_env secrets — nothing else
  S1.3/S1.4  bind sources handed to the daemon are physical paths
  S15    stack-wide resource governance ([<root>.governance]): overlay
         injects cgroup_parent/mem_limit/mem_reservation/blkio_config into
         every enumerated service, author-set keys winning (S15.3)

This module is standalone: it does NOT import from engine.py or deploy.py.
PyYAML is an existing dependency. ``governance.py`` is a sibling standalone
module (pure logic, no CIU imports) this module delegates governance
resolution/injection-computation to.

The consumed "materialized" interface (produced by the P4 materialize packet)
is ``dict[name, obj]`` where each *obj* exposes:
  - ``.value``  : ``str | None``    — the resolved secret value (None when the
                                      file is referenced in place, e.g. ASK_FILE)
  - ``.file``   : ``pathlib.Path``  — the (logical) secret-file path
  - ``.spec``   : ``SecretSpec``    — the parsed declaration (S4 grammar)
This module only reads those three attributes; it never imports materialize.py.

Public API
----------
SecretLeakError(RuntimeError)
SecretGuard
ConfigFileMount
guard_config(config, specs) -> dict
redact_config(config, specs) -> dict
render_compose(template_path, guarded_config) -> str
leak_scan(rendered_text, materialized) -> None
validate_consumption(compose_yaml_text, declared, *, configfile_mounts=(), hook_consumed=()) -> list[str]
render_configfiles(stack_dir, root_key, config, secret_value_fn) -> list[ConfigFileMount]
generate_overlay(stack_dir, materialized, configfile_mounts, *, repo_root, physical_root, compose_yaml_text, governance) -> Path | None
compose_process_env(specs, materialized, *, base, compose_profiles) -> dict
compose_file_args(stack_dir, overlay_path) -> list[str]
"""

from __future__ import annotations

import copy
import os
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable, Mapping

import yaml

from . import governance as governance_mod
from .config_constants import (
    CIU_COMPOSE_OUTPUT,
    MACHINE_DIR,
    OVERLAY_NAME,
    RENDERED_SUBDIR,
)
from .config_model import render_jinja2_text
from .paths import to_physical_path
from .secrets.directives import SecretSpec


# ---------------------------------------------------------------------------
# Leak error
# ---------------------------------------------------------------------------

class SecretLeakError(RuntimeError):
    """Raised when a secret value would be (or was) exposed in plaintext.

    Carries the secret *name* only — never the value (S4.22/S4.23).
    """


# ---------------------------------------------------------------------------
# SecretGuard (S4.21)
# ---------------------------------------------------------------------------

def _leak_message(name: str) -> str:
    """Standard guidance pointing the author at the sanctioned secret paths."""
    return (
        f"[S4.21] secret '{name}' must not be stringified in a compose/hook "
        f"context. Declare consumption with `secrets: [{name}]` and read it at "
        f"`/run/secrets/{name}` inside the container, or embed it in a "
        f"configfile template via `secret('{name}')` (S5.4)."
    )


class SecretGuard:
    """Stand-in for a resolved secret value in compose-template / hook contexts.

    Wraps the secret *name* (never the value). Every path by which a value
    could be turned into text — ``str()``, ``repr()``, ``format()``/f-strings,
    ``markupsafe.__html__``, and equality against a ``str`` — raises
    :class:`SecretLeakError`. In particular Jinja2's ``{{ guard }}`` output
    path calls ``__str__`` (autoescape off, the render path used by
    :func:`render_compose` and :func:`render_configfiles`); under autoescape it
    consults ``__html__`` first. Both are blocked here.

    The guard deliberately remains usable for structural access in templates
    (``{% if app.secrets.pw %}`` truthiness, attribute access on the table) —
    only *materializing it as text* aborts.
    """

    __slots__ = ("_ciu_secret_name",)

    def __init__(self, name: str) -> None:
        # Bypass any future __setattr__ guards; store the name privately.
        object.__setattr__(self, "_ciu_secret_name", name)

    @property
    def secret_name(self) -> str:
        return object.__getattribute__(self, "_ciu_secret_name")

    def _raise(self) -> "NoReturn":  # type: ignore[name-defined]  # noqa: F821
        raise SecretLeakError(_leak_message(self.secret_name))

    # --- every stringification path aborts ---
    def __str__(self) -> str:  # Jinja2 {{ x }} output (autoescape off) hits this
        self._raise()

    def __repr__(self) -> str:
        self._raise()

    def __format__(self, format_spec: str) -> str:  # f"{x}" / "{}".format(x)
        self._raise()

    def __html__(self) -> str:  # Jinja2 {{ x }} under autoescape consults this
        self._raise()

    def __bytes__(self) -> bytes:
        self._raise()

    # --- equality against a str would otherwise leak via comparison probing ---
    def __eq__(self, other: object) -> bool:
        if isinstance(other, str):
            self._raise()
        if isinstance(other, SecretGuard):
            return self.secret_name == other.secret_name
        return NotImplemented

    def __ne__(self, other: object) -> bool:
        result = self.__eq__(other)
        if result is NotImplemented:
            return result
        return not result

    def __hash__(self) -> int:
        # Hash on the name so guards remain usable as dict keys / set members
        # without leaking; this never exposes the value.
        return hash(("ciu-secret-guard", self.secret_name))


# ---------------------------------------------------------------------------
# Locating secrets-table entries via SecretSpec.table_path + name
# ---------------------------------------------------------------------------

def _table_at(config: Mapping[str, Any], dotted_path: str) -> Any:
    """Resolve ``dotted_path`` (e.g. ``redis_core.secrets``) within *config*.

    Returns the node (typically a dict) or ``None`` when any segment is missing
    or a segment is not a mapping.
    """
    node: Any = config
    for part in dotted_path.split("."):
        if not isinstance(node, Mapping) or part not in node:
            return None
        node = node[part]
    return node


def _replace_entries(config: dict, specs: Iterable[SecretSpec], make_value) -> dict:
    """Deep-copy *config*; replace each secret entry value with ``make_value(name)``.

    The entry is located via ``spec.table_path`` (the dotted path of the
    enclosing ``secrets`` table) + ``spec.name`` (the TOML key). Entries whose
    table or key are absent in *config* are skipped silently — the caller's
    spec list and config originate from the same merged config, so this only
    guards against partial fixtures.
    """
    result = copy.deepcopy(config)
    for spec in specs:
        table = _table_at(result, spec.table_path)
        if isinstance(table, dict) and spec.name in table:
            table[spec.name] = make_value(spec.name)
    return result


def guard_config(config: dict, specs: Iterable[SecretSpec]) -> dict:
    """Deep-copied *config* with every secrets-table entry replaced by a guard.

    S4.21 — the compose-template render context (and hook contexts, S9.3) see
    :class:`SecretGuard` instances in place of the directive strings/inline
    tables. Stringifying any of them aborts the run.

    The replacement targets the *entry value* (located at
    ``spec.table_path`` → ``spec.name``), so non-secret config remains readable.
    """
    return _replace_entries(config, specs, SecretGuard)


def redact_config(config: dict, specs: Iterable[SecretSpec]) -> dict:
    """Deep-copied *config* with every secrets-table entry replaced by a label.

    S4.23 — for ``--print-context`` and logs, each secret renders as the
    literal string ``<secret:<name>>``; plaintext values never appear.
    """
    return _replace_entries(config, specs, lambda name: f"<secret:{name}>")


# ---------------------------------------------------------------------------
# render_compose (S4.21)
# ---------------------------------------------------------------------------

def render_compose(template_path: Path | str, guarded_config: dict) -> str:
    """Render the compose template with the *guarded* config.

    S4.21 — context is ``{**guarded_config, 'env': dict(os.environ)}``. A
    template that materializes a guard (``{{ app.secrets.pw }}``) raises
    :class:`SecretLeakError` via the guard's ``__str__``.

    Jinja2 ``TemplateError``s surface wrapped with the source filename for
    diagnostics; ``SecretLeakError`` propagates unchanged.
    """
    from jinja2 import TemplateError

    template_path = Path(template_path)
    raw = template_path.read_text(encoding="utf-8")
    context = {**guarded_config, "env": dict(os.environ)}
    try:
        return render_jinja2_text(raw, context)
    except SecretLeakError:
        # The guard fired inside the render — propagate the precise leak error
        # instead of burying it as a generic template failure.
        raise
    except TemplateError as exc:
        # Jinja wraps the guard's SecretLeakError? It does not: the guard raises
        # a plain RuntimeError subclass that Jinja propagates as-is. Other
        # template errors are annotated with the source file.
        raise TemplateError(
            f"Failed to render compose template {template_path}: {exc}"
        ) from exc


# ---------------------------------------------------------------------------
# leak_scan (S4.22)
# ---------------------------------------------------------------------------

def leak_scan(rendered_text: str, materialized: Mapping[str, Any]) -> None:
    """Abort if any resolved secret value appears in *rendered_text* (S4.22).

    For every materialized secret, substring-search its ``.value`` in the
    rendered compose text (and overlay) — both in the raw text and in a
    whitespace-collapsed copy, so a value split across a YAML line fold
    (``>``/``|`` blocks insert ``\\n`` + indent) is still caught for values
    that themselves contain no whitespace (all generated tokens).

    Scope per S4.22 (accepted limitations, defense-in-depth only — the
    primary barrier is the S4.21 guard that keeps values out of the template
    context entirely): values shorter than 8 characters are skipped, and
    encoded forms (base64/url-encoding) are not searched. ``None`` values
    (``ASK_FILE`` referenced in place) are skipped. A hit raises
    :class:`SecretLeakError` naming the secret **only**.
    """
    collapsed = re.sub(r"\s+", "", rendered_text)
    for name, obj in materialized.items():
        value = getattr(obj, "value", None)
        if value is None:
            continue
        if not isinstance(value, str):
            value = str(value)
        if len(value) < 8:
            continue
        haystacks = (rendered_text,) if re.search(r"\s", value) else (rendered_text, collapsed)
        if any(value in h for h in haystacks):
            raise SecretLeakError(
                f"[S4.22] plaintext value of secret '{name}' leaked into the "
                f"rendered compose output. Consume it via `secrets: [{name}]` "
                f"and `/run/secrets/{name}`, or a configfile `secret('{name}')`."
            )


# ---------------------------------------------------------------------------
# validate_consumption (S4.20)
# ---------------------------------------------------------------------------

def _service_secret_names(service_block: Any) -> list[str]:
    """Extract secret names from one service's ``secrets:`` entry.

    Recognizes both compose forms (S4.17):
      - short  : ``secrets: [name, ...]``
      - long   : ``secrets: [{source: name, target: ...}, ...]``
    Unrecognized shapes are ignored (compose itself rejects them).
    """
    names: list[str] = []
    if not isinstance(service_block, Mapping):
        return names
    secrets = service_block.get("secrets")
    if not isinstance(secrets, list):
        return names
    for entry in secrets:
        if isinstance(entry, str):
            names.append(entry)
        elif isinstance(entry, Mapping) and "source" in entry:
            src = entry["source"]
            if isinstance(src, str):
                names.append(src)
    return names


def validate_consumption(
    compose_yaml_text: str,
    declared: set[str],
    *,
    configfile_mounts: Iterable["ConfigFileMount"] = (),
    hook_consumed: Iterable[str] = (),
) -> list[str]:
    """Cross-check secret consumption against *declared* names (S4.20).

    Parses *compose_yaml_text*, collects every ``services.*.secrets`` reference
    (short and long form). A reference to a name not in *declared* raises
    ``ValueError`` tagged ``[S4.20]``. Also counts secrets consumed by S5
    configfiles and secrets explicitly marked as hook-consumed. Returns the
    sorted list of declared names consumed by no channel (the caller emits the
    S4.20 warning).
    """
    doc = yaml.safe_load(compose_yaml_text)
    services = {}
    if isinstance(doc, Mapping):
        svc = doc.get("services")
        if isinstance(svc, Mapping):
            services = svc

    consumed: set[str] = set()
    for service_name, block in services.items():
        for ref in _service_secret_names(block):
            if ref not in declared:
                raise ValueError(
                    f"[S4.20] service '{service_name}' references undeclared "
                    f"secret '{ref}'. Declare it in a `secrets` table under the "
                    f"stack root key, or remove the reference."
                )
            consumed.add(ref)

    for mount in configfile_mounts:
        for name in mount.consumed_secrets:
            if name not in declared:
                raise ValueError(
                    f"[S4.20] configfile '{mount.service}.{mount.name}' references "
                    f"undeclared secret '{name}'. Declare it in a `secrets` table "
                    "under the stack root key."
                )
            consumed.add(name)

    for name in hook_consumed:
        if name not in declared:
            raise ValueError(
                f"[S4.20] hook consumption marker references undeclared secret "
                f"'{name}'. Declare it in a `secrets` table under the stack root key."
            )
        consumed.add(name)

    return sorted(declared - consumed)


# ---------------------------------------------------------------------------
# Configfile rendering (S5)
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class ConfigFileMount:
    """A rendered configfile and where it mounts (S5).

    Attributes
    ----------
    service        : the service name (``[<root>.<service>.configfile.<name>]``).
    name           : the configfile name (``<cfgname>``).
    rendered_path  : logical path of the rendered file under ``.ciu/rendered/``.
    target         : absolute container path the overlay binds it to (S5.3).
    mode           : octal mode string, default ``"0440"`` (S5.1).
    consumed_secrets: secret names read by this template via ``secret()``
                      (S4.20/S5.4).
    """

    service: str
    name: str
    rendered_path: Path
    target: str
    mode: str = "0440"
    consumed_secrets: tuple[str, ...] = ()


def _make_secret_fn(secret_value_fn, declared_names: set[str]):
    """Wrap *secret_value_fn* with the S5.4 unknown-name guard.

    Returns a ``secret(name) -> str`` callable for configfile templates. Unknown
    names (not in *declared_names*) raise ``ValueError`` tagged ``[S5.4]`` before
    *secret_value_fn* is consulted.
    """

    def secret(name: str) -> str:
        if name not in declared_names:
            raise ValueError(
                f"[S5.4] configfile template requested secret('{name}') but no "
                f"such secret is declared in the stack's `secrets` tables. "
                f"Declared: {sorted(declared_names)}."
            )
        return secret_value_fn(name)

    return secret


def render_configfiles(
    stack_dir: Path | str,
    root_key: str,
    config: dict,
    secret_value_fn,
) -> list[ConfigFileMount]:
    """Discover and render ``configfile`` sections under each service (S5).

    Discovers ``[<root_key>.<service>.configfile.<cfgname>]`` tables (keys:
    ``template``, ``target``, optional ``mode`` default ``"0440"``,
    optional ``instances`` int). Each template (path relative to *stack_dir*)
    is rendered with a Jinja2 context of the **guarded** config (secrets are NOT
    readable as ``{{ root.secrets.x }}``, S4.21 — even here) plus ``env`` and
    the explicit ``secret(name)`` function (S5.4).

    **Dynamic per-instance fan-out (§6):** when a configfile section declares
    ``instances = N`` (integer ≥ 1), ``render_configfiles`` renders the template
    *N* times — once per 1-based index — and emits *N* :class:`ConfigFileMount`
    objects, each with a unique ``name`` (``<cfgname>-<index>``) and unique
    ``service`` (``<service_name>-<index>``). Each render context additionally
    exposes ``instance_index`` (1-based int) and ``instance_id``
    (``"<service>-<index>"``) so templates can produce unique content per
    instance. Single-instance configfiles (no ``instances`` key, or
    ``instances = 1``) behave identically to before this change.

    The result is written to
    ``<stack_dir>/.ciu/rendered/<service[-index]>/<cfgname[-index]>`` (parents
    created) and chmod'd to ``mode``.

    Parameters
    ----------
    stack_dir       : the stack directory.
    root_key        : the stack root key (S3.5).
    config          : the merged stack config (UNguarded input; it is guarded
                      internally so the only secret path is ``secret()``).
    secret_value_fn : ``name -> str`` returning a resolved secret value; wrapped
                      with the S5.4 unknown-name guard before exposure.

    Returns
    -------
    list[ConfigFileMount]

    Raises
    ------
    FileNotFoundError : a referenced template does not exist.
    ValueError        : a configfile section is missing ``template``/``target``,
                        or a template calls ``secret()`` with an unknown name,
                        or ``instances`` is not a positive integer.
    """
    from .secrets.directives import discover as _discover

    stack_dir = Path(stack_dir)
    root = config.get(root_key)
    if not isinstance(root, Mapping):
        return []

    # Secrets reachable ONLY via secret(): guard the config so any
    # {{ root.secrets.x }} in a configfile template still aborts (S4.21/S5.4).
    specs = _discover(root_key, config)
    declared_names = {s.name for s in specs}
    guarded = guard_config(config, specs)
    secret_fn = _make_secret_fn(secret_value_fn, declared_names)

    rendered_root = stack_dir / MACHINE_DIR / RENDERED_SUBDIR
    mounts: list[ConfigFileMount] = []

    for service_name, service_block in root.items():
        if not isinstance(service_block, Mapping):
            continue
        configfiles = service_block.get("configfile")
        if not isinstance(configfiles, Mapping):
            continue
        for cfg_name, cfg in configfiles.items():
            if not isinstance(cfg, Mapping):
                raise ValueError(
                    f"[S5.1] configfile '{cfg_name}' of service '{service_name}' "
                    f"must be a table with `template`/`target` keys."
                )
            template_rel = cfg.get("template")
            target = cfg.get("target")
            mode = cfg.get("mode", "0440")
            instances_raw = cfg.get("instances", None)

            if not isinstance(template_rel, str) or not template_rel:
                raise ValueError(
                    f"[S5.1] configfile '{cfg_name}' of service '{service_name}' "
                    f"is missing a `template` path (relative to the stack dir)."
                )
            if not isinstance(target, str) or not target:
                raise ValueError(
                    f"[S5.1] configfile '{cfg_name}' of service '{service_name}' "
                    f"is missing a `target` container path."
                )

            # Validate + resolve instance count
            if instances_raw is None:
                instance_count = 1
                multi_instance = False
            else:
                if not isinstance(instances_raw, int) or isinstance(instances_raw, bool) or instances_raw < 1:
                    raise ValueError(
                        f"[S5.1] configfile '{cfg_name}' of service '{service_name}': "
                        f"'instances' must be a positive integer, got {instances_raw!r}."
                    )
                instance_count = instances_raw
                multi_instance = instance_count > 1

            template_path = stack_dir / template_rel
            if not template_path.exists():
                raise FileNotFoundError(
                    f"[S5.1] configfile template not found for "
                    f"'{service_name}.{cfg_name}': {template_path}"
                )

            raw = template_path.read_text(encoding="utf-8")

            # Render once per instance (1-based indexing)
            for idx in range(1, instance_count + 1):
                consumed_here: set[str] = set()

                def configfile_secret(name: str, _consumed=consumed_here) -> str:
                    value = secret_fn(name)
                    _consumed.add(name)
                    return value

                instance_id = f"{service_name}-{idx}"
                context = {
                    **guarded,
                    "env": dict(os.environ),
                    "secret": configfile_secret,
                    "instance_index": idx,
                    "instance_id": instance_id,
                }
                rendered_text = render_jinja2_text(raw, context)

                # For multi-instance: use <service>-<idx> and <cfgname>-<idx>
                if multi_instance:
                    effective_service = f"{service_name}-{idx}"
                    effective_name = f"{cfg_name}-{idx}"
                else:
                    effective_service = service_name
                    effective_name = cfg_name

                out_path = rendered_root / effective_service / effective_name
                out_path.parent.mkdir(parents=True, exist_ok=True)
                # Atomic replace (S8.4) — also the only way a SECOND run can
                # overwrite the previous 0440 rendered file without write
                # permission on it (os.replace needs only the directory).
                tmp_path = out_path.with_name(out_path.name + ".tmp")
                tmp_path.write_text(rendered_text, encoding="utf-8")
                os.chmod(tmp_path, int(mode, 8))
                os.replace(tmp_path, out_path)

                mounts.append(
                    ConfigFileMount(
                        service=effective_service,
                        name=effective_name,
                        rendered_path=out_path,
                        target=target,
                        mode=mode,
                        consumed_secrets=tuple(sorted(consumed_here)),
                    )
                )

    return mounts


# ---------------------------------------------------------------------------
# generate_overlay (S4.17 / S8.1)
# ---------------------------------------------------------------------------

_INSTANCE_SERVICE_RE_TEMPLATE = r"^{base}-([1-9][0-9]*)$"


def _compose_service_blocks(compose_yaml_text: str | None) -> dict[str, Mapping[str, Any]]:
    """Return ``{service_name: rendered_block}`` from a rendered compose document.

    Empty dict when *compose_yaml_text* is falsy/unparseable/service-less.
    Used both to enumerate service names (configfile fan-out, S5.3) and to
    read each service's author-set keys (governance precedence, S15.3).
    """
    if not compose_yaml_text:
        return {}
    doc = yaml.safe_load(compose_yaml_text)
    if not isinstance(doc, Mapping):
        return {}
    services = doc.get("services")
    if not isinstance(services, Mapping):
        return {}
    return {
        str(name): (block if isinstance(block, Mapping) else {})
        for name, block in services.items()
    }


def _compose_service_names(compose_yaml_text: str | None) -> set[str]:
    """Return service keys from a rendered compose document, if available."""
    return set(_compose_service_blocks(compose_yaml_text))


def _configfile_mount_services(base_service: str, compose_services: set[str]) -> list[str]:
    """Resolve one configfile service selector to concrete compose services.

    Exact compose service keys keep the historical behavior. If no exact key
    exists, a base selector fans out to instance-indexed keys named
    ``<base>-1``, ``<base>-2``, ... . With no rendered compose service data we
    preserve the selector as-is for backward compatibility.

    When compose service data IS available but the selector matches neither an
    exact key nor any ``<base>-<N>`` instance, the mount would target a phantom
    service (S5.3). We still preserve the selector (so compose can diagnose the
    bad name) but emit a ``[WARN]`` so the misconfiguration fails loud instead
    of silently producing a file no container receives.
    """
    if base_service in compose_services:
        return [base_service]
    if not compose_services:
        return [base_service]

    pattern = re.compile(
        _INSTANCE_SERVICE_RE_TEMPLATE.format(base=re.escape(base_service))
    )
    matches: list[tuple[int, str]] = []
    for service_name in compose_services:
        m = pattern.match(service_name)
        if m:
            matches.append((int(m.group(1)), service_name))

    if not matches:
        print(
            f"[WARN] configfile selector '{base_service}' matches no rendered "
            f"compose service: no exact key '{base_service}' and no "
            f"'{base_service}-<N>' instance keys exist (S5.3). The mount will "
            "target a phantom service and no container will receive the file. "
            "Check the [<root>.<service>.configfile.*] service name and the "
            "instance index (CIU fans out 1-based: <base>-1, <base>-2, ...).",
            flush=True,
        )
        return [base_service]
    return [service_name for _, service_name in sorted(matches)]


def generate_overlay(
    stack_dir: Path | str,
    materialized: Mapping[str, Any],
    configfile_mounts: Iterable[ConfigFileMount],
    *,
    repo_root: Path | None = None,
    physical_root: Path | None = None,
    compose_yaml_text: str | None = None,
    governance: Mapping[str, Any] | None = None,
) -> Path | None:
    """Write ``<stack>/.ciu/ciu.compose.overlay.yml`` (the overlay); S4.17/S8.1/S15.

    Builds::

        secrets:
          <name>:
            file: <physical path of the secret file>      # every materialized secret
        services:
          <service-or-expanded-instance>:
            volumes:
              - "<physical rendered path>:<target>:ro"     # per configfile mount
            cgroup_parent: ...                             # governance (S15), when enabled
            mem_limit: ...
            mem_reservation: ...
            blkio_config: {device_read_iops: [...], device_write_iops: [...]}

    All bind sources are physical paths (S1.3/S1.4) via :func:`to_physical_path`.
    The overlay NEVER contains secret values — only paths.

    *governance* is the stack's already-merged ``[<root>.governance]`` table
    (``None`` when the stack declares no such table at all — the common case,
    fully backward compatible: no computation, no log line). When present, it
    is resolved against :data:`governance.GOVERNANCE_DEFAULTS` (S15.2); when
    ``enabled`` is true, every service enumerated in *compose_yaml_text*
    (except ``exempt_services``) gets ``cgroup_parent``/``mem_limit``/
    ``mem_reservation``/``blkio_config`` injected — but only for keys the
    stack author did NOT already set on that service in the rendered base
    compose (S15.3 precedence). One summary line is always logged when
    *governance* is not ``None`` (S15.7).

    Returns the overlay path, or ``None`` when there are no secrets, no
    configfiles, **and** no governance injections (S8.1/S15 — the overlay is
    omitted only when there is nothing at all to wire).

    *repo_root* / *physical_root* are forwarded to :func:`to_physical_path`;
    when ``None`` they are read from the environment there.
    """
    configfile_mounts = list(configfile_mounts)
    compose_service_blocks = _compose_service_blocks(compose_yaml_text)
    compose_services = set(compose_service_blocks)

    governance_injections: dict[str, dict[str, Any]] = {}
    if governance is not None:
        gov_cfg = governance_mod.resolve_config(governance)
        if gov_cfg["enabled"]:
            governance_injections, gov_notes = governance_mod.build_injections(
                compose_service_blocks, gov_cfg
            )
            print("[GOVERNANCE] enabled — " + "; ".join(gov_notes), flush=True)
        else:
            print(
                "[GOVERNANCE] disabled ([<root>.governance].enabled is false)",
                flush=True,
            )

    if not materialized and not configfile_mounts and not governance_injections:
        return None

    stack_dir = Path(stack_dir)
    overlay: dict[str, Any] = {}

    if materialized:
        secrets_block: dict[str, Any] = {}
        for name, obj in materialized.items():
            phys = to_physical_path(
                Path(obj.file), repo_root=repo_root, physical_root=physical_root
            )
            secrets_block[name] = {"file": str(phys)}
        overlay["secrets"] = secrets_block

    services: dict[str, Any] = {}

    if configfile_mounts:
        for mount in configfile_mounts:
            phys = to_physical_path(
                Path(mount.rendered_path),
                repo_root=repo_root,
                physical_root=physical_root,
            )
            # Long-form mount object: colon/space-safe (the short
            # "src:dst:ro" string form mis-splits on a ':' in either path).
            volume_entry = {
                "type": "bind",
                "source": str(phys),
                "target": mount.target,
                "read_only": True,
            }
            for service_name in _configfile_mount_services(mount.service, compose_services):
                svc = services.setdefault(service_name, {})
                svc.setdefault("volumes", []).append(volume_entry)

    if governance_injections:
        for service_name, frag in governance_injections.items():
            svc = services.setdefault(service_name, {})
            svc.update(frag)

    if services:
        overlay["services"] = services

    overlay_path = stack_dir / MACHINE_DIR / OVERLAY_NAME
    overlay_path.parent.mkdir(parents=True, exist_ok=True)
    text = yaml.safe_dump(overlay, sort_keys=False, default_flow_style=False)
    # Atomic replace (S8.4): write to a temp sibling, then os.replace.
    tmp_path = overlay_path.with_name(overlay_path.name + ".tmp")
    tmp_path.write_text(text, encoding="utf-8")
    os.replace(tmp_path, overlay_path)
    return overlay_path


# ---------------------------------------------------------------------------
# compose_process_env (S8.2)
# ---------------------------------------------------------------------------

def compose_process_env(
    specs: Iterable[SecretSpec],
    materialized: Mapping[str, Any],
    *,
    base: Mapping[str, str] | None = None,
    compose_profiles: Iterable[str] | None = None,
) -> dict[str, str]:
    """Build the compose process environment (S8.2) — and nothing more.

    The result is exactly::

        dict(base or os.environ)
        + PWD = <cwd>
        + COMPOSE_PROFILES = ",".join(compose_profiles)   # only when non-empty
        + { spec.expose_env: <value> }                    # per S4.19 expose_env

    No TOML flattening, no other keys (S8.2 — flatten withdrawn). *base* is
    never mutated (a fresh dict is returned).

    The ``expose_env`` value comes from ``materialized[spec.name].value``; if a
    spec declares ``expose_env`` but is not present in *materialized* (or its
    value is ``None``), it is skipped — there is nothing to expose.
    """
    env: dict[str, str] = dict(base if base is not None else os.environ)
    env["PWD"] = os.getcwd()

    if compose_profiles is not None:
        profiles = [p for p in compose_profiles if p]
        if profiles:
            env["COMPOSE_PROFILES"] = ",".join(profiles)

    for spec in specs:
        if not spec.expose_env:
            continue
        obj = materialized.get(spec.name)
        if obj is None:
            continue
        value = getattr(obj, "value", None)
        if value is None:
            continue
        env[spec.expose_env] = value if isinstance(value, str) else str(value)

    return env


# ---------------------------------------------------------------------------
# compose_file_args (S8.1)
# ---------------------------------------------------------------------------

def compose_file_args(stack_dir: Path | str, overlay_path: Path | str | None) -> list[str]:
    """Return the ``-f`` arguments for the compose invocation (S8.1).

    Always ``['-f', 'ciu.compose.yml']``; appends
    ``['-f', '.ciu/ciu.compose.overlay.yml']`` when *overlay_path* is set
    (i.e. the stack has secrets and/or configfiles, S8.1). Paths are relative —
    the engine runs compose with ``cwd = stack_dir``.

    *stack_dir* is accepted for symmetry with the rest of the API and to allow
    future absolute-path emission; the returned paths are relative by design so
    the overlay merge works regardless of where the daemon resolves them.
    """
    args = ["-f", CIU_COMPOSE_OUTPUT]
    if overlay_path is not None:
        args += ["-f", f"{MACHINE_DIR}/{OVERLAY_NAME}"]
    return args
