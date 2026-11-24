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
import tomlkit


TLS_MODES = ("acme-tls", "acme-http", "acme-dns", "static", "dev")
_EMAIL = re.compile(r"[A-Za-z0-9._%+-]{1,64}@[A-Za-z0-9](?:[A-Za-z0-9.-]{0,251}[A-Za-z0-9])?")
_PROVIDER = re.compile(r"[a-z0-9][a-z0-9_-]{1,39}")
_VOLUME = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,127}")
_CGROUP_PARENT = re.compile(r"[A-Za-z0-9_./@-]{1,255}")
_ABSOLUTE_MOUNT_PATH = re.compile(r"/(?:[A-Za-z0-9_.-]+/)*[A-Za-z0-9_.-]*")
_ANSWER_KEYS = {
    "domain", "tls_mode", "acme_email", "dns_provider", "dns_wildcard",
    "http_enabled", "http_port", "https_port", "http3_enabled", "calls_enabled",
    "calls_port", "bind_ip", "ingress_external", "acme_volume_name", "cgroup_parent",
    "static_cert_base", "static_domains", "static_source",
}


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


def _deep_merge(base: dict[str, Any], overlay: Mapping[str, Any]) -> dict[str, Any]:
    result = dict(base)
    for key, value in overlay.items():
        if isinstance(value, Mapping) and isinstance(result.get(key), Mapping):
            result[key] = _deep_merge(dict(result[key]), value)
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


def _render_defaults(path: Path) -> dict[str, Any]:
    try:
        source = path.read_text(encoding="utf-8")
        rendered = jinja2.Environment(
            undefined=jinja2.StrictUndefined,
            keep_trailing_newline=True,
        ).from_string(source).render()
        return _plain(tomlkit.parse(rendered))
    except (OSError, jinja2.TemplateError, tomlkit.exceptions.ParseError) as exc:
        raise ConfigError(f"cannot load tls-edge defaults from {path}: {exc}") from exc


def load_effective_config(root: Path) -> tuple[dict[str, Any], bool]:
    defaults_path, override_path, _ = instance_paths(root)
    config = _render_defaults(defaults_path)
    has_override = os.path.lexists(override_path)
    if has_override:
        if override_path.is_symlink() or not override_path.is_file():
            raise ConfigError(f"site override must be a regular file: {override_path}")
        try:
            overlay = _plain(tomlkit.parse(override_path.read_text(encoding="utf-8")))
        except (OSError, tomlkit.exceptions.ParseError) as exc:
            raise ConfigError(f"cannot read site override {override_path}: {exc}") from exc
        config = _deep_merge(config, overlay)
    if not isinstance(config.get("tls_edge"), dict):
        raise ConfigError("tls-edge defaults must contain a [tls_edge] table")
    return config["tls_edge"], has_override


def load_answers(path: Path | None) -> dict[str, Any]:
    if path is None:
        return {}
    try:
        with path.expanduser().open("rb") as stream:
            answers = tomllib.load(stream)
    except (OSError, tomllib.TOMLDecodeError) as exc:
        raise ConfigError(f"cannot read answers file {path}: {exc}") from exc
    unknown = sorted(set(answers) - _ANSWER_KEYS)
    if unknown:
        raise ConfigError(f"unknown answer key(s) in {path}: {', '.join(unknown)}")
    return answers


def value_at(config: Mapping[str, Any], *path: str, default: Any = None) -> Any:
    current: Any = config
    for part in path:
        if not isinstance(current, Mapping) or part not in current:
            return default
        current = current[part]
    return current


def _port(value: Any, label: str) -> int:
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


def _table(config: Mapping[str, Any], name: str) -> Mapping[str, Any]:
    value = config.get(name, {})
    if not isinstance(value, Mapping):
        raise ConfigError(f"tls_edge.{name} must be a TOML table")
    return value


def validate_config(
    config: Mapping[str, Any],
    *,
    require_domain: bool = True,
    require_certificates: bool = True,
) -> list[str]:
    """Validate cross-field edge behavior and return the inbound port plan."""
    tls = _table(config, "tls")
    ports = _table(config, "ports")
    http3 = _table(config, "http3")
    calls = _table(config, "calls")
    acme = _table(config, "acme")
    static = _table(config, "static")
    mode = tls.get("mode")
    if mode not in TLS_MODES:
        raise ConfigError(f"tls.mode must be one of {', '.join(TLS_MODES)}")

    domain = config.get("domain", "")
    if require_domain:
        normalize_domain(domain)
    elif domain:
        normalize_domain(domain)

    expose_http = ports.get("expose_http")
    h3_enabled = http3.get("enabled")
    calls_enabled = calls.get("enabled")
    if not isinstance(expose_http, bool):
        raise ConfigError("ports.expose_http must be true or false")
    if not isinstance(h3_enabled, bool):
        raise ConfigError("http3.enabled must be true or false")
    if not isinstance(calls_enabled, bool):
        raise ConfigError("calls.enabled must be true or false")

    http_port = _port(ports.get("http"), "ports.http")
    https_port = _port(ports.get("https"), "ports.https")
    if expose_http and http_port == https_port:
        raise ConfigError("HTTP and HTTPS cannot publish the same TCP port")
    if mode == "acme-tls" and https_port != 443:
        raise ConfigError("acme-tls requires the public HTTPS port to be 443")
    if mode == "acme-http" and (not expose_http or http_port != 80):
        raise ConfigError("acme-http requires the public HTTP listener on port 80")

    bind_ip = ports.get("bind_ip", "")
    if not isinstance(bind_ip, str):
        raise ConfigError("ports.bind_ip must be an IP address or an empty string")
    if bind_ip:
        try:
            ipaddress.ip_address(bind_ip)
        except ValueError as exc:
            raise ConfigError("ports.bind_ip must be a valid IP address") from exc

    volume_name = acme.get("volume_name", "")
    if not isinstance(volume_name, str) or (volume_name and not _VOLUME.fullmatch(volume_name)):
        raise ConfigError("acme.volume_name must be a valid Docker volume name or empty")
    cgroup_parent = value_at(config, "runtime", "cgroup_parent", default="")
    if not isinstance(cgroup_parent, str) or (
        cgroup_parent and (
            not _CGROUP_PARENT.fullmatch(cgroup_parent) or ".." in cgroup_parent
        )
    ):
        raise ConfigError("runtime.cgroup_parent contains unsupported characters")
    cert_base = static.get("cert_base", "/etc/letsencrypt")
    if (
        not isinstance(cert_base, str)
        or not _ABSOLUTE_MOUNT_PATH.fullmatch(cert_base)
        or ".." in cert_base.split("/")
    ):
        raise ConfigError("static.cert_base must be a simple absolute path")
    if static.get("source", "bind") not in ("bind", "volume"):
        raise ConfigError("static.source must be bind or volume")
    static_domains = static.get("domains", [])
    if not isinstance(static_domains, list):
        raise ConfigError("static.domains must be an array of certificate lineages")
    for item in static_domains:
        normalize_domain(item, "static certificate lineage")

    inbound = [f"{http_port}/tcp (HTTP redirect and/or HTTP-01)"] if expose_http else []
    inbound.append(f"{https_port}/tcp (HTTPS, HTTP/1.1 and HTTP/2)")
    if h3_enabled:
        inbound.append(f"{https_port}/udp (HTTP/3 over QUIC)")
    if calls_enabled:
        calls_port = _port(calls.get("public_port"), "calls.public_port")
        if expose_http and calls_port == http_port:
            raise ConfigError("Calls TCP conflicts with the public HTTP TCP port")
        if calls_port == https_port:
            raise ConfigError("Calls TCP conflicts with the public HTTPS TCP port")
        inbound.extend((f"{calls_port}/tcp (Calls media)", f"{calls_port}/udp (Calls media)"))

    if mode == "acme-dns":
        provider = value_at(config, "acme", "dns", "provider", default="")
        if not isinstance(provider, str) or not _PROVIDER.fullmatch(provider):
            raise ConfigError("acme.dns.provider must be a valid lego provider name")
        wildcard = value_at(config, "acme", "dns", "wildcard_main", default="")
        if not isinstance(wildcard, str):
            raise ConfigError("acme.dns.wildcard_main must be a DNS name or empty")
        if wildcard:
            normalize_domain(wildcard, "acme.dns.wildcard_main")
    if mode in ("static", "dev") and require_certificates and not static_domains:
        raise ConfigError(f"tls.mode {mode} requires at least one static certificate lineage")
    email = acme.get("email", "")
    if not isinstance(email, str) or (email and not _EMAIL.fullmatch(email)):
        raise ConfigError("acme.email must be a valid email address or empty")
    return inbound


def _mutable_table(value: Any) -> bool:
    return isinstance(value, Mapping) and callable(getattr(value, "__setitem__", None))


def _set_path(document: Any, path: tuple[str, ...], value: Any) -> None:
    current = document
    for part in path[:-1]:
        if not _mutable_table(current):
            raise ConfigError(f"cannot update {'.'.join(path)}: parent is not a TOML table")
        if part not in current:
            current[part] = tomlkit.table()
        child = current[part]
        if not _mutable_table(child):
            raise ConfigError(f"cannot update {'.'.join(path)}: {part!r} is not a table")
        current = child
    if not _mutable_table(current):
        raise ConfigError(f"cannot update {'.'.join(path)}: parent is not a TOML table")
    current[path[-1]] = value


_FIELDS: dict[str, tuple[str, ...]] = {
    "domain": ("tls_edge", "domain"),
    "tls_mode": ("tls_edge", "tls", "mode"),
    "acme_email": ("tls_edge", "acme", "email"),
    "dns_provider": ("tls_edge", "acme", "dns", "provider"),
    "dns_wildcard": ("tls_edge", "acme", "dns", "wildcard_main"),
    "http_enabled": ("tls_edge", "ports", "expose_http"),
    "http_port": ("tls_edge", "ports", "http"),
    "https_port": ("tls_edge", "ports", "https"),
    "http3_enabled": ("tls_edge", "http3", "enabled"),
    "calls_enabled": ("tls_edge", "calls", "enabled"),
    "calls_port": ("tls_edge", "calls", "public_port"),
    "bind_ip": ("tls_edge", "ports", "bind_ip"),
    "ingress_external": ("tls_edge", "ingress_external"),
    "acme_volume_name": ("tls_edge", "acme", "volume_name"),
    "cgroup_parent": ("tls_edge", "runtime", "cgroup_parent"),
    "static_cert_base": ("tls_edge", "static", "cert_base"),
    "static_domains": ("tls_edge", "static", "domains"),
    "static_source": ("tls_edge", "static", "source"),
}


def updated_override(root: Path, values: Mapping[str, Any]) -> tuple[Path, str, int]:
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
    for name, value in values.items():
        if name not in _FIELDS:
            raise ConfigError(f"unsupported configuration field {name!r}")
        _set_path(document, _FIELDS[name], value)
    return override_path, tomlkit.dumps(document), mode


def updated_consumer_env(root: Path, domain: str) -> tuple[Path, str, int]:
    _, _, env_path = instance_paths(root)
    if os.path.lexists(env_path) and (env_path.is_symlink() or not env_path.is_file()):
        raise ConfigError(f"consumer environment must be a regular file: {env_path}")
    if env_path.exists():
        original = env_path.read_text(encoding="utf-8")
        mode = stat.S_IMODE(env_path.stat().st_mode)
        lines = original.splitlines()
        replaced = False
        result = []
        for line in lines:
            if line.startswith("TLS_EDGE_DOMAIN="):
                if not replaced:
                    result.append(f"TLS_EDGE_DOMAIN={domain}")
                    replaced = True
            else:
                result.append(line)
        if not replaced:
            result.append(f"TLS_EDGE_DOMAIN={domain}")
        text = "\n".join(result).rstrip("\n") + "\n"
    else:
        mode = 0o644
        text = f"TLS_EDGE_DOMAIN={domain}\n"
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
        try:
            temporary.unlink()
        except FileNotFoundError:
            pass
        raise
