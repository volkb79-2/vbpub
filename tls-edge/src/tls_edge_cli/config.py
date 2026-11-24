"""Load, validate, and update a tls-edge instance's CIU site override."""

from __future__ import annotations

import ipaddress
import os
import re
import stat
import sys
import tempfile
import tomllib
from collections.abc import Mapping
from pathlib import Path
from typing import Any

import jinja2


SCHEMA = 1
TLS_MODES = ("acme-tls", "acme-http", "acme-dns", "static", "dev")
_EMAIL = re.compile(r"[A-Za-z0-9._%+-]{1,64}@[A-Za-z0-9](?:[A-Za-z0-9.-]{0,251}[A-Za-z0-9])?")
_PROVIDER = re.compile(r"[a-z0-9][a-z0-9_-]{1,39}")
_VOLUME = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,127}")
_DOCKER_NETWORK = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.-]*")
_CGROUP_PARENT = re.compile(r"[A-Za-z0-9_./@-]{1,255}")
_ABSOLUTE_MOUNT_PATH = re.compile(r"/(?:[A-Za-z0-9_.-]+/)*[A-Za-z0-9_.-]*")
_TEMPLATE_MARKERS = ("{{", "{%", "{#")
_OPEN_OVERRIDE_TABLES = frozenset({
    ("tls_edge", "acme", "dns", "environment"),
    ("tls_edge", "secrets"),
    ("tls_edge", "hooks"),
})
_REQUIRED_PRE_SECRETS_HOOK = "hooks/validate_edge.py"


class ConfigError(ValueError):
    """A user configuration or instance-layout error."""


def locate_instance_root(explicit: Path | None = None) -> Path:
    """Resolve the installed instance, or use an explicit root for local work."""
    if explicit is not None:
        root = explicit.expanduser().absolute()
        _check_instance_layout(root)
        return root

    venv = Path(sys.prefix).resolve()
    release = venv.parent
    for parent in release.parents:
        current = parent / "current"
        if current.is_symlink() and current.resolve() == release:
            _check_instance_layout(parent)
            return parent
    raise ConfigError(
        "cannot locate the installed tls-edge instance from this executable; "
        "pass --root PATH"
    )


def _check_instance_layout(root: Path) -> None:
    stack = root / "stack"
    defaults = stack / "ciu-stack" / "ciu.defaults.toml.j2"
    if not stack.is_dir() or not defaults.is_file():
        raise ConfigError(
            f"{root} is not a tls-edge instance root (expected {defaults})"
        )


def _plain(value: Any) -> Any:
    if isinstance(value, Mapping):
        return {str(key): _plain(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_plain(item) for item in value]
    return value


def deep_merge(base: dict[str, Any], overlay: Mapping[str, Any]) -> dict[str, Any]:
    """Merge tables recursively and replace scalars and lists (CIU S3.3)."""
    result = dict(base)
    for key, value in overlay.items():
        if isinstance(value, Mapping) and isinstance(result.get(key), Mapping):
            result[key] = deep_merge(dict(result[key]), value)
        else:
            result[key] = _plain(value)
    return result


def instance_paths(root: Path) -> tuple[Path, Path, Path]:
    stack = root / "stack"
    return (
        stack / "ciu-stack" / "ciu.defaults.toml.j2",
        stack / "ciu-stack" / "ciu.toml.j2",
        stack / "consumer.env",
    )


def render_defaults(path: Path) -> dict[str, Any]:
    """Render and parse the shipped TOML defaults."""
    try:
        source = path.read_text(encoding="utf-8")
        rendered = jinja2.Environment(
            undefined=jinja2.StrictUndefined,
            keep_trailing_newline=True,
        ).from_string(source).render()
        return _plain(tomllib.loads(rendered))
    except (OSError, jinja2.TemplateError, tomllib.TOMLDecodeError) as exc:
        raise ConfigError(f"cannot load tls-edge defaults from {path}: {exc}") from exc


def _load_override(path: Path) -> dict[str, Any]:
    if not os.path.lexists(path):
        return {}
    if path.is_symlink() or not path.is_file():
        raise ConfigError(f"site override must be a regular file: {path}")
    try:
        text = path.read_text(encoding="utf-8")
    except (OSError, UnicodeError) as exc:
        raise ConfigError(f"cannot read site override {path}: {exc}") from exc
    for marker in _TEMPLATE_MARKERS:
        if marker in text:
            raise ConfigError(
                f"site override {path} must be plain TOML; "
                f"found templating marker {marker!r}"
            )
    try:
        return _plain(tomllib.loads(text))
    except tomllib.TOMLDecodeError as exc:
        raise ConfigError(f"cannot read site override {path}: {exc}") from exc


def load_site(
    stack_dir: Path,
) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any]]:
    """Load rendered defaults, the sparse override, and their deep merge."""
    stack_dir = Path(stack_dir)
    defaults = render_defaults(stack_dir / "ciu.defaults.toml.j2")
    overlay = _load_override(stack_dir / "ciu.toml.j2")
    effective = deep_merge(defaults, overlay)
    if not isinstance(effective.get("tls_edge"), dict):
        raise ConfigError("tls-edge defaults must contain a [tls_edge] table")
    return defaults, overlay, effective


def migrate(overlay: Mapping[str, Any]) -> Mapping[str, Any]:
    """Migrate an override to the current schema; schema 1 is identity."""
    return overlay


def _check_override_table(
    defaults: Mapping[str, Any],
    overlay: Mapping[str, Any],
    path: tuple[str, ...] = (),
) -> None:
    if path in _OPEN_OVERRIDE_TABLES:
        if (
            path == ("tls_edge", "hooks")
            and "pre_secrets" in overlay
            and (
                not isinstance(overlay["pre_secrets"], list)
                or _REQUIRED_PRE_SECRETS_HOOK not in overlay["pre_secrets"]
            )
        ):
            raise ConfigError(
                "tls_edge.hooks.pre_secrets must contain "
                f"{_REQUIRED_PRE_SECRETS_HOOK}"
            )
        return
    for key, value in overlay.items():
        key_path = (*path, str(key))
        dotted = ".".join(key_path)
        if key not in defaults:
            raise ConfigError(f"unknown override key {dotted}")
        expected = defaults[key]
        if isinstance(value, Mapping) != isinstance(expected, Mapping):
            expected_shape = "a TOML table" if isinstance(expected, Mapping) else "a scalar"
            raise ConfigError(f"override key {dotted} must be {expected_shape}")
        if isinstance(value, Mapping):
            _check_override_table(expected, value, key_path)


def check_override(defaults: Mapping[str, Any], overlay: Mapping[str, Any]) -> None:
    """Refuse unknown keys, shape mismatches, and future override schemas."""
    tls_edge = overlay.get("tls_edge")
    schema = tls_edge.get("schema", SCHEMA) if isinstance(tls_edge, Mapping) else SCHEMA
    if isinstance(schema, bool) or not isinstance(schema, int):
        raise ConfigError("site override tls_edge.schema must be an integer")
    if schema < 1:
        raise ConfigError("site override tls_edge.schema must be at least 1")
    if schema > SCHEMA:
        raise ConfigError(
            f"tls_edge.schema {schema} is newer than this release supports "
            f"(maximum {SCHEMA})"
        )
    _check_override_table(defaults, overlay)


def load_answers(path: Path | None) -> dict[str, Any]:
    """Parse a sparse answers file in the site-override shape."""
    if path is None:
        return {}
    try:
        with path.expanduser().open("rb") as stream:
            answers = tomllib.load(stream)
    except (OSError, tomllib.TOMLDecodeError) as exc:
        raise ConfigError(f"cannot read answers file {path}: {exc}") from exc
    from .fields import LEGACY_ANSWER_KEYS, legacy_answers_message

    messages: list[str] = []
    for key in sorted(set(answers) - {"tls_edge"}):
        if key in LEGACY_ANSWER_KEYS:
            messages.append(legacy_answers_message(key))
        else:
            messages.append(
                f"unknown answers key {key!r} (answers file {path}); "
                "answers use the site-override shape [tls_edge.<table>]"
            )
    if messages:
        raise ConfigError("\n".join(messages))
    return answers


def validate_answers(
    defaults: Mapping[str, Any],
    answers: Mapping[str, Any],
    *,
    answers_file: Path | None = None,
) -> None:
    """Refuse unknown answers and the open site-override tables."""
    from .fields import FIELD_BY_KEY

    if not answers:
        return
    tls_edge = answers.get("tls_edge")
    if not isinstance(tls_edge, Mapping):
        raise ConfigError("answers key 'tls_edge' must be a TOML table")

    managed_paths = {f"tls_edge.{key}" for key in FIELD_BY_KEY}
    try:
        check_override(defaults, answers)
    except ConfigError as exc:
        message = str(exc)
        prefix = "unknown override key "
        if not message.startswith(prefix):
            raise
        dotted = message.removeprefix(prefix)
        siblings = sorted(
            key for key in managed_paths
            if key.rpartition(".")[0] == dotted.rpartition(".")[0]
        )
        hint = f"; did you mean {siblings[0]}?" if len(siblings) == 1 else ""
        source = f" (answers file {answers_file})" if answers_file is not None else ""
        raise ConfigError(
            f"unknown answers key {dotted!r}{source}{hint}"
        ) from exc
    for path in _all_paths(tls_edge, ("tls_edge",)):
        dotted = ".".join(path)
        if any(path[: len(open_path)] == open_path for open_path in _OPEN_OVERRIDE_TABLES):
            raise ConfigError(
                f"answers key {dotted!r} is an open site-override table"
            )


def _all_paths(
    value: Mapping[str, Any],
    path: tuple[str, ...],
) -> list[tuple[str, ...]]:
    found: list[tuple[str, ...]] = []
    for key, item in value.items():
        child = (*path, str(key))
        found.append(child)
        if isinstance(item, Mapping):
            found.extend(_all_paths(item, child))
    return found


def normalize_port(value: Any, label: str = "port") -> int:
    """Validate a public port."""
    if isinstance(value, bool) or not isinstance(value, int) or not 1 <= value <= 65535:
        raise ConfigError(f"{label} must be an integer from 1 through 65535")
    return value


def normalize_domain(value: Any, label: str = "domain") -> str:
    if not isinstance(value, str) or not value.strip():
        raise ConfigError(f"{label} is required")
    raw = value.strip().rstrip(".")
    try:
        domain = raw.encode("idna").decode("ascii").lower()
    except UnicodeError as exc:
        raise ConfigError(f"{label} is not a valid DNS name") from exc
    if len(domain) > 253 or ":" in domain:
        raise ConfigError(f"{label} is not a valid DNS name: {value!r}")
    labels = domain.split(".")
    if any(
        not label
        or len(label) > 63
        or not re.fullmatch(r"[a-z0-9](?:[a-z0-9-]*[a-z0-9])?", label)
        for label in labels
    ):
        raise ConfigError(f"{label} is not a valid DNS name: {value!r}")
    try:
        ipaddress.ip_address(domain)
    except ValueError:
        return domain
    raise ConfigError(f"{label} must be a DNS name, not an IP address")


def normalize_email(value: Any, label: str = "acme.email") -> str:
    if not isinstance(value, str):
        raise ConfigError(f"{label} must be a valid email address or empty")
    text = value.strip()
    if text and not _EMAIL.fullmatch(text):
        raise ConfigError(f"{label} must be a valid email address or empty")
    return text


def normalize_provider(value: Any, label: str = "acme.dns.provider") -> str:
    if not isinstance(value, str):
        raise ConfigError(f"{label} must be a valid lego provider name")
    text = value.strip()
    if not _PROVIDER.fullmatch(text):
        raise ConfigError(f"{label} must be a valid lego provider name")
    return text


def normalize_volume_name(value: Any, label: str = "acme.volume_name") -> str:
    if not isinstance(value, str):
        raise ConfigError(f"{label} must be a valid Docker volume name or empty")
    text = value.strip()
    if text and not _VOLUME.fullmatch(text):
        raise ConfigError(f"{label} must be a valid Docker volume name or empty")
    return text


def normalize_bind_ip(value: Any, label: str = "ports.bind_ip") -> str:
    if not isinstance(value, str):
        raise ConfigError(f"{label} must be an IP address or an empty string")
    text = value.strip()
    if text:
        try:
            ipaddress.ip_address(text)
        except ValueError as exc:
            raise ConfigError(f"{label} must be a valid IP address") from exc
    return text


def normalize_cgroup_parent(value: Any, label: str = "runtime.cgroup_parent") -> str:
    if not isinstance(value, str):
        raise ConfigError(f"{label} contains unsupported characters")
    text = value.strip()
    if text and (not _CGROUP_PARENT.fullmatch(text) or ".." in text):
        raise ConfigError(f"{label} contains unsupported characters")
    return text


def normalize_cert_base(value: Any, label: str = "static.cert_base") -> str:
    if not isinstance(value, str):
        raise ConfigError(f"{label} must be a simple absolute path")
    text = value.strip()
    if (
        not _ABSOLUTE_MOUNT_PATH.fullmatch(text)
        or ".." in text.split("/")
    ):
        raise ConfigError(f"{label} must be a simple absolute path")
    return text


def normalize_static_domains(value: Any, label: str = "static.domains") -> list[str]:
    if not isinstance(value, list):
        raise ConfigError(f"{label} must be an array of certificate lineages")
    domains: list[str] = []
    for item in value:
        if not isinstance(item, str) or not item.strip():
            raise ConfigError(f"{label} must contain non-empty certificate lineage names")
        domains.append(normalize_domain(item, "static certificate lineage"))
    if len(set(domains)) != len(domains):
        raise ConfigError(f"{label} must not contain duplicates")
    return domains


def _table(config: Mapping[str, Any], name: str) -> Mapping[str, Any]:
    if name not in config or not isinstance(config[name], Mapping):
        raise ConfigError(f"tls_edge.{name} must be a TOML table")
    return config[name]


def _required(table: Mapping[str, Any], key: str, label: str) -> Any:
    if key not in table:
        raise ConfigError(f"{label} is required")
    return table[key]


def validate_config(
    config: Mapping[str, Any],
    *,
    require_domain: bool = True,
    require_certificates: bool = True,
    stack_dir: Path | None = None,
    for_standalone: bool = False,
) -> list[str]:
    """Validate cross-field edge behavior and return the inbound port plan."""
    tls = _table(config, "tls")
    ports = _table(config, "ports")
    _table(config, "http3")
    _table(config, "calls")
    acme = _table(config, "acme")
    static = _table(config, "static")
    mode = _required(tls, "mode", "tls.mode")
    if mode not in TLS_MODES:
        raise ConfigError(f"tls.mode must be one of {', '.join(TLS_MODES)}")

    domain = _required(config, "domain", "domain")
    if require_domain:
        normalize_domain(domain)
    elif domain:
        normalize_domain(domain)

    if "ingress_network" in config:
        ingress_network = config["ingress_network"]
        if (
            not isinstance(ingress_network, str)
            or _DOCKER_NETWORK.fullmatch(ingress_network) is None
        ):
            raise ConfigError("ingress_network must be a valid Docker network name")

    inbound = port_plan(config)
    expose_http = _required(ports, "expose_http", "ports.expose_http")
    http_port = normalize_port(
        _required(ports, "http", "ports.http"), "ports.http"
    )
    https_port = normalize_port(
        _required(ports, "https", "ports.https"), "ports.https"
    )
    if mode == "acme-tls" and https_port != 443:
        raise ConfigError("acme-tls requires the public HTTPS port to be 443")
    if mode == "acme-http" and (not expose_http or http_port != 80):
        raise ConfigError("acme-http requires the public HTTP listener on port 80")

    normalize_bind_ip(_required(ports, "bind_ip", "ports.bind_ip"))

    volume_name = _required(acme, "volume_name", "acme.volume_name")
    volume_name = normalize_volume_name(volume_name)
    store = _required(acme, "store", "acme.store")
    if not isinstance(store, str) or store not in ("volume", "hostdir"):
        raise ConfigError("acme.store must be volume or hostdir")
    if store == "hostdir":
        traefik = _table(config, "traefik")
        hostdirs = _table(traefik, "hostdir")
        entry = hostdirs.get("acme")
        if isinstance(entry, Mapping):
            path = _required(entry, "path", "tls_edge.traefik.hostdir.acme.path")
            uid = _required(entry, "uid", "tls_edge.traefik.hostdir.acme.uid")
        else:
            raise ConfigError("tls_edge.traefik.hostdir.acme must be a TOML table")
        if not isinstance(path, str) or not path:
            raise ConfigError(
                'tls_edge.traefik.hostdir.acme.path is required when '
                'tls_edge.acme.store = "hostdir"'
            )
        if "$" in path:
            raise ConfigError(
                "tls_edge.traefik.hostdir.acme.path must not contain '$'; "
                "ciu expands environment variables in hostdir paths"
            )
        if stack_dir is not None:
            candidate = Path(path)
            if not candidate.is_absolute():
                candidate = Path(stack_dir) / candidate
            stack_root = Path(stack_dir).parent.resolve()
            if candidate.resolve().is_relative_to(stack_root):
                raise ConfigError(
                    "tls_edge.traefik.hostdir.acme.path must resolve outside stack/"
                )
        if isinstance(uid, bool) or not isinstance(uid, int) or uid != 0:
            raise ConfigError(
                'tls_edge.traefik.hostdir.acme.uid must be 0 when '
                'tls_edge.acme.store = "hostdir"'
            )
        if volume_name:
            raise ConfigError(
                'tls_edge.acme.volume_name and tls_edge.acme.store = "hostdir" '
                "are mutually exclusive"
            )
        if for_standalone:
            raise ConfigError(
                'tls_edge.acme.store = "hostdir" requires ciu; '
                'the standalone renderer only supports "volume"'
            )
    cgroup_parent = normalize_cgroup_parent(
        _required(
            _table(config, "runtime"),
            "cgroup_parent",
            "runtime.cgroup_parent",
        )
    )
    cert_base = _required(static, "cert_base", "static.cert_base")
    normalize_cert_base(cert_base)
    if _required(static, "source", "static.source") not in ("bind", "volume"):
        raise ConfigError("static.source must be bind or volume")
    static_domains = normalize_static_domains(
        _required(static, "domains", "static.domains")
    )

    if mode == "acme-dns":
        provider = normalize_provider(
            _required(_table(acme, "dns"), "provider", "acme.dns.provider")
        )
        wildcard = _required(
            _table(acme, "dns"), "wildcard_main", "acme.dns.wildcard_main"
        )
        if not isinstance(wildcard, str):
            raise ConfigError("acme.dns.wildcard_main must be a DNS name or empty")
        if wildcard:
            normalize_domain(wildcard, "acme.dns.wildcard_main")
    if mode in ("static", "dev") and require_certificates and not static_domains:
        raise ConfigError(f"tls.mode {mode} requires at least one static certificate lineage")
    normalize_email(_required(acme, "email", "acme.email"))
    return [
        f"{port}/{protocol} ({purpose})"
        for port, protocol, purpose in inbound
    ]


def port_plan(config: Mapping[str, Any]) -> list[tuple[int, str, str]]:
    """Return the validated public listeners as structured port-plan rows."""
    ports = _table(config, "ports")
    http3 = _table(config, "http3")
    calls = _table(config, "calls")
    expose_http = _required(ports, "expose_http", "ports.expose_http")
    h3_enabled = _required(http3, "enabled", "http3.enabled")
    calls_enabled = _required(calls, "enabled", "calls.enabled")
    if not isinstance(expose_http, bool):
        raise ConfigError("ports.expose_http must be true or false")
    if not isinstance(h3_enabled, bool):
        raise ConfigError("http3.enabled must be true or false")
    if not isinstance(calls_enabled, bool):
        raise ConfigError("calls.enabled must be true or false")

    http_port = normalize_port(
        _required(ports, "http", "ports.http"), "ports.http"
    )
    https_port = normalize_port(
        _required(ports, "https", "ports.https"), "ports.https"
    )
    if expose_http and http_port == https_port:
        raise ConfigError("HTTP and HTTPS cannot publish the same TCP port")

    plan: list[tuple[int, str, str]] = []
    if expose_http:
        plan.append((http_port, "tcp", "HTTP redirect and/or HTTP-01"))
    plan.append((https_port, "tcp", "HTTPS, HTTP/1.1 and HTTP/2"))
    if h3_enabled:
        plan.append((https_port, "udp", "HTTP/3 over QUIC"))
    if calls_enabled:
        calls_port = normalize_port(
            _required(calls, "public_port", "calls.public_port"),
            "calls.public_port",
        )
        if expose_http and calls_port == http_port:
            raise ConfigError("Calls TCP conflicts with the public HTTP TCP port")
        if calls_port == https_port:
            raise ConfigError("Calls TCP conflicts with the public HTTPS TCP port")
        plan.extend((
            (calls_port, "tcp", "Calls media"),
            (calls_port, "udp", "Calls media"),
        ))
    return plan


def _mutable_table(value: Any) -> bool:
    return isinstance(value, Mapping) and callable(getattr(value, "__setitem__", None))


def _set_path(document: Any, path: tuple[str, ...], value: Any) -> None:
    import tomlkit

    current = document
    for part in path[:-1]:
        if part not in current:
            current[part] = tomlkit.table()
        child = current[part]
        if not _mutable_table(child):
            raise ConfigError(f"cannot update {'.'.join(path)}: {part!r} is not a table")
        current = child
    current[path[-1]] = value


def updated_override(
    root: Path,
    values: Mapping[str, Any],
    extra: Mapping[str, Any] | None = None,
) -> tuple[Path, str, int]:
    """Apply managed values and unmanaged answer leaves to the override."""
    import tomlkit
    from .fields import FIELDS, MANAGED_KEYS

    _, override_path, _ = instance_paths(root)
    if os.path.lexists(override_path) and (override_path.is_symlink() or not override_path.is_file()):
        raise ConfigError(f"site override must be a regular file: {override_path}")
    if override_path.exists():
        original = override_path.read_text(encoding="utf-8")
        mode = stat.S_IMODE(override_path.stat().st_mode)
        try:
            document = tomlkit.parse(original)
        except tomlkit.exceptions.ParseError as exc:
            raise ConfigError(f"cannot update malformed site override {override_path}: {exc}") from exc
    else:
        original = ""
        mode = 0o644
        document = tomlkit.document()
    unknown = sorted(set(values) - MANAGED_KEYS)
    if unknown:
        raise ConfigError(f"unsupported configuration field {unknown[0]!r}")
    extra = dict(extra or {})
    managed_extra = sorted(set(extra) & MANAGED_KEYS)
    if managed_extra:
        raise ConfigError(
            f"managed configuration field {managed_extra[0]!r} must use field-table values"
        )
    _set_path(document, ("tls_edge", "schema"), SCHEMA)
    for field in FIELDS:
        if field.key in values:
            _set_path(
                document,
                ("tls_edge", *field.key.split(".")),
                values[field.key],
            )
    for key in sorted(extra):
        _set_path(document, ("tls_edge", *key.split(".")), extra[key])
    return override_path, tomlkit.dumps(document), mode


def updated_consumer_env(
    root: Path,
    contract: Mapping[str, str],
) -> tuple[Path, str, int]:
    from .contract import render_env

    _, _, env_path = instance_paths(root)
    if os.path.lexists(env_path) and (env_path.is_symlink() or not env_path.is_file()):
        raise ConfigError(f"consumer environment must be a regular file: {env_path}")
    if env_path.exists():
        original = env_path.read_text(encoding="utf-8")
        mode = stat.S_IMODE(env_path.stat().st_mode)
        text = render_env(contract, original)
    else:
        mode = 0o644
        text = render_env(contract)
    return env_path, text, mode


def atomic_write(path: Path, text: str, mode: int) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary_name = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=path.parent)
    temporary = Path(temporary_name)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as stream:
            os.fchmod(stream.fileno(), mode)
            stream.write(text)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
        directory_fd = os.open(path.parent, os.O_RDONLY)
        try:
            os.fsync(directory_fd)
        finally:
            os.close(directory_fd)
    except BaseException:
        temporary.unlink(missing_ok=True)
        raise
