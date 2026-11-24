"""CLI for configuring an installed tls-edge instance."""

from __future__ import annotations

import difflib
import re
import sys
from pathlib import Path
from typing import Any

from cli_extended import (
    CliFailure,
    CliIdentity,
    CliRegistry,
    CliRuntime,
    OptionSpec,
    VerbGroup,
    VerbSpec,
    VersionLookupError,
)

from .config import (
    check_override,
    ConfigError,
    TLS_MODES,
    atomic_write,
    load_answers,
    load_effective_config,
    load_site,
    locate_instance_root,
    migrate,
    normalize_domain,
    updated_consumer_env,
    updated_override,
    validate_config,
    value_at,
)


_PORT_TEXT = re.compile(r"[0-9]{1,5}")


def _identity() -> CliIdentity:
    return CliIdentity.from_distribution(
        name="TLS EDGE",
        distribution="tls-edge-cli",
        long_name="Configure and inspect a shared Traefik edge",
        command="tls-edge",
    )


def _root(args: Any) -> Path:
    try:
        return locate_instance_root(args.root)
    except ConfigError as exc:
        raise CliFailure(str(exc), exit_code=2) from exc


def _answer(answers: dict[str, Any], key: str, expected: type) -> Any | None:
    if key not in answers:
        return None
    value = answers[key]
    if expected is int and isinstance(value, bool):
        raise ConfigError(f"answer {key!r} must be an integer")
    if not isinstance(value, expected):
        raise ConfigError(f"answer {key!r} must be {expected.__name__}")
    return value


def _text(
    runtime: CliRuntime,
    answers: dict[str, Any],
    key: str,
    prompt: str,
    default: str | None,
    *,
    required: bool = True,
    allow_prompt: bool = True,
) -> str:
    supplied = _answer(answers, key, str)
    if supplied is not None:
        value = supplied.strip()
    elif allow_prompt:
        value = runtime.prompts.text(prompt, default=default, required=required).strip()
    else:
        value = (default or "").strip()
    if required and not value:
        raise ConfigError(f"{key} is required")
    return value


def _boolean(
    runtime: CliRuntime, answers: dict[str, Any], key: str, prompt: str, default: bool,
    *, allow_prompt: bool = True,
) -> bool:
    supplied = _answer(answers, key, bool)
    if supplied is not None:
        return supplied
    return runtime.prompts.confirm(prompt, default=default) if allow_prompt else default


def _port(
    runtime: CliRuntime,
    answers: dict[str, Any],
    key: str,
    prompt: str,
    default: int,
    *, allow_prompt: bool = True,
) -> int:
    supplied = _answer(answers, key, int)
    if supplied is not None:
        value = supplied
    elif allow_prompt:
        raw = runtime.prompts.text(prompt, default=str(default), required=True).strip()
        if not _PORT_TEXT.fullmatch(raw):
            raise ConfigError(f"{key} must be a whole number from 1 through 65535")
        value = int(raw)
    else:
        value = default
    if not 1 <= value <= 65535:
        raise ConfigError(f"{key} must be a whole number from 1 through 65535")
    return value


def _choices(
    runtime: CliRuntime,
    answers: dict[str, Any],
    key: str,
    prompt: str,
    choices: tuple[str, ...],
    default: str,
    *, allow_prompt: bool = True,
) -> str:
    supplied = _answer(answers, key, str)
    if supplied is not None:
        value = supplied
    elif allow_prompt:
        value = runtime.prompts.select(prompt, choices, default=default)
    else:
        value = default
    if value not in choices:
        raise ConfigError(f"{key} must be one of {', '.join(choices)}")
    return value


def _static_domains(value: Any, prompt_value: str | None = None) -> list[str]:
    if prompt_value is not None:
        raw_values = [part.strip() for part in prompt_value.split(",")]
    elif isinstance(value, list):
        raw_values = value
    else:
        raise ConfigError("static_domains must be a TOML array of strings")
    domains = []
    for item in raw_values:
        if not isinstance(item, str) or not item.strip():
            raise ConfigError("static_domains must contain non-empty strings")
        domains.append(normalize_domain(item, "static certificate lineage"))
    if len(set(domains)) != len(domains):
        raise ConfigError("static_domains must not contain duplicates")
    return domains


def _collect_values(
    current: dict[str, Any],
    runtime: CliRuntime,
    answers: dict[str, Any],
    *,
    allow_prompt: bool,
) -> dict[str, Any]:
    domain = normalize_domain(_text(
        runtime, answers, "domain", "Base DNS domain for consumer host names",
        value_at(current, "domain") or None,
        allow_prompt=allow_prompt,
    ))
    mode = _choices(
        runtime, answers, "tls_mode", "Certificate mode", TLS_MODES,
        value_at(current, "tls", "mode", default="acme-tls"),
        allow_prompt=allow_prompt,
    )

    values: dict[str, Any] = {
        "domain": domain,
        "tls_mode": mode,
        "http_enabled": _boolean(
            runtime, answers, "http_enabled",
            "Open an HTTP listener for HTTPS redirects and HTTP-01?",
            value_at(current, "ports", "expose_http", default=True),
            allow_prompt=allow_prompt,
        ),
        "http_port": _port(
            runtime, answers, "http_port", "Public HTTP port",
            value_at(current, "ports", "http", default=80),
            allow_prompt=allow_prompt,
        ),
        "https_port": _port(
            runtime, answers, "https_port", "Public HTTPS TCP port",
            value_at(current, "ports", "https", default=443),
            allow_prompt=allow_prompt,
        ),
        "http3_enabled": _boolean(
            runtime, answers, "http3_enabled",
            "Offer HTTP/3 over QUIC on the public HTTPS UDP port?",
            value_at(current, "http3", "enabled", default=False),
            allow_prompt=allow_prompt,
        ),
        "calls_enabled": _boolean(
            runtime, answers, "calls_enabled",
            "Publish the optional Mattermost Calls TCP+UDP media pair?",
            value_at(current, "calls", "enabled", default=False),
            allow_prompt=allow_prompt,
        ),
        "bind_ip": _text(
            runtime, answers, "bind_ip",
            "Host IP to bind (empty uses the host's dual-stack default)",
            value_at(current, "ports", "bind_ip", default=""), required=False,
            allow_prompt=allow_prompt,
        ),
        "ingress_external": _boolean(
            runtime, answers, "ingress_external",
            "Adopt an existing external ingress_public Docker network?",
            value_at(current, "ingress_external", default=False),
            allow_prompt=allow_prompt,
        ),
        "acme_volume_name": _text(
            runtime, answers, "acme_volume_name",
            "Existing ACME Docker volume to adopt (empty creates an instance-owned volume)",
            value_at(current, "acme", "volume_name", default=""), required=False,
            allow_prompt=allow_prompt,
        ),
        "cgroup_parent": _text(
            runtime, answers, "cgroup_parent",
            "Docker cgroup parent or slice (empty uses Docker's default)",
            value_at(current, "runtime", "cgroup_parent", default=""), required=False,
            allow_prompt=allow_prompt,
        ),
    }

    if values["calls_enabled"]:
        values["calls_port"] = _port(
            runtime, answers, "calls_port", "Public Calls media TCP+UDP port",
            value_at(current, "calls", "public_port", default=8443),
            allow_prompt=allow_prompt,
        )
    elif "calls_port" in answers:
        values["calls_port"] = _port(
            runtime, answers, "calls_port", "Public Calls media TCP+UDP port",
            value_at(current, "calls", "public_port", default=8443),
            allow_prompt=False,
        )

    if not mode.startswith("acme-") and "acme_email" in answers:
        raise ConfigError("answer 'acme_email' requires an ACME certificate mode")
    if mode != "acme-dns" and {"dns_provider", "dns_wildcard"}.intersection(answers):
        raise ConfigError("dns_provider and dns_wildcard require tls_mode = 'acme-dns'")
    if mode not in ("static", "dev") and {
        "static_cert_base", "static_domains", "static_source"
    }.intersection(answers):
        raise ConfigError("static certificate answers require tls_mode = 'static' or 'dev'")

    if mode.startswith("acme-"):
        values["acme_email"] = _text(
            runtime, answers, "acme_email", "ACME account email (optional)",
            value_at(current, "acme", "email", default=""), required=False,
            allow_prompt=allow_prompt,
        )
    if mode == "acme-dns":
        values["dns_provider"] = _text(
            runtime, answers, "dns_provider", "lego DNS provider name",
            value_at(current, "acme", "dns", "provider", default="cloudflare"),
            allow_prompt=allow_prompt,
        )
        values["dns_wildcard"] = _text(
            runtime, answers, "dns_wildcard", "Wildcard base domain (optional)",
            value_at(current, "acme", "dns", "wildcard_main", default=""), required=False,
            allow_prompt=allow_prompt,
        )

    if mode in ("static", "dev"):
        values["static_cert_base"] = _text(
            runtime, answers, "static_cert_base", "Certificate tree parent directory",
            value_at(current, "static", "cert_base", default="/etc/letsencrypt"),
            allow_prompt=allow_prompt,
        )
        current_domains = value_at(current, "static", "domains", default=[])
        default_domains = ",".join(current_domains) if current_domains else domain
        supplied_domains = _answer(answers, "static_domains", list)
        if supplied_domains is not None:
            values["static_domains"] = _static_domains(supplied_domains)
        elif allow_prompt:
            prompt_value = runtime.prompts.text(
                "Certificate lineage directory names, comma separated",
                default=default_domains,
                required=True,
            )
            values["static_domains"] = _static_domains(None, prompt_value)
        else:
            values["static_domains"] = _static_domains(
                current_domains if current_domains else [domain]
            )
        values["static_source"] = _choices(
            runtime, answers, "static_source", "Certificate source",
            ("bind", "volume"), value_at(current, "static", "source", default="bind"),
            allow_prompt=allow_prompt,
        )

    return values


def _port_plan(config: dict[str, Any], stack_dir: Path) -> list[str]:
    return validate_config(config, stack_dir=stack_dir)


def _load_checked_site(root: Path) -> tuple[dict[str, Any], dict[str, Any]]:
    stack_dir = root / "stack" / "ciu-stack"
    defaults, overlay, effective = load_site(stack_dir)
    overlay = migrate(overlay)
    check_override(defaults, overlay)
    return effective["tls_edge"], overlay


def _acme_settings(
    root: Path,
    current: dict[str, Any],
    overlay: dict[str, Any],
) -> dict[str, str]:
    store = value_at(current, "acme", "store", default="volume")
    path = value_at(current, "traefik", "hostdir", "acme", "path", default="")
    if (
        (root / "shared" / "acme").is_dir()
        and value_at(overlay, "tls_edge", "acme", "store") is None
    ):
        store = "hostdir"
        path = "../../shared/acme"
    values = {"acme_store": store}
    if store == "hostdir":
        values["acme_path"] = path
    return values


def _load_validated_site(
    root: Path,
    *,
    require_domain: bool = True,
) -> tuple[dict[str, Any], list[str]]:
    stack_dir = root / "stack" / "ciu-stack"
    config, _ = _load_checked_site(root)
    return config, validate_config(
        config,
        require_domain=require_domain,
        stack_dir=stack_dir,
    )


def _render_config(root: Path, values: dict[str, Any]) -> tuple[Path, str, int, Path, str, int]:
    try:
        override_path, override_text, override_mode = updated_override(root, values)
        env_path, env_text, env_mode = updated_consumer_env(root, values["domain"])
    except (OSError, ConfigError) as exc:
        raise CliFailure(str(exc), exit_code=2) from exc
    return override_path, override_text, override_mode, env_path, env_text, env_mode


def _diff(path: Path, old: str, new: str) -> str:
    return "".join(difflib.unified_diff(
        old.splitlines(keepends=True), new.splitlines(keepends=True),
        fromfile=str(path) if old else f"{path} (new)",
        tofile=str(path),
    ))


def configure(args: Any, runtime: CliRuntime) -> int:
    root = _root(args)
    try:
        current, overlay = _load_checked_site(root)
        answers = load_answers(args.answers)
        values = _collect_values(
            current, runtime, answers, allow_prompt=args.answers is None
        )
        values.update(_acme_settings(root, current, overlay))
        proposed = dict(current)
        proposed["domain"] = values["domain"]
        proposed.setdefault("tls", {})["mode"] = values["tls_mode"]
        proposed.setdefault("ports", {}).update({
            "expose_http": values["http_enabled"],
            "http": values["http_port"],
            "https": values["https_port"],
            "bind_ip": values["bind_ip"],
        })
        proposed.setdefault("http3", {})["enabled"] = values["http3_enabled"]
        proposed.setdefault("calls", {})["enabled"] = values["calls_enabled"]
        if values["calls_enabled"]:
            proposed["calls"]["public_port"] = values["calls_port"]
        proposed.setdefault("acme", {})["volume_name"] = values["acme_volume_name"]
        proposed["acme"]["store"] = values["acme_store"]
        if "acme_path" in values:
            proposed.setdefault("traefik", {}).setdefault("hostdir", {}).setdefault(
                "acme", {}
            )["path"] = values["acme_path"]
        proposed["acme"]["email"] = values.get(
            "acme_email", value_at(current, "acme", "email", default="")
        )
        proposed.setdefault("runtime", {})["cgroup_parent"] = values["cgroup_parent"]
        proposed["ingress_external"] = values["ingress_external"]
        if "dns_provider" in values:
            proposed.setdefault("acme", {}).setdefault("dns", {})["provider"] = values["dns_provider"]
            proposed["acme"]["dns"]["wildcard_main"] = values["dns_wildcard"]
        if "calls_port" in values:
            proposed["calls"]["public_port"] = values["calls_port"]
        if "static_cert_base" in values:
            proposed.setdefault("static", {}).update({
                "cert_base": values["static_cert_base"],
                "domains": values["static_domains"],
                "source": values["static_source"],
            })
        plan = _port_plan(proposed, root / "stack" / "ciu-stack")
        override_path, override_text, override_mode, env_path, env_text, env_mode = _render_config(root, values)
    except ConfigError as exc:
        raise CliFailure(str(exc), exit_code=2) from exc

    original_override = override_path.read_text(encoding="utf-8") if override_path.exists() else ""
    original_env = env_path.read_text(encoding="utf-8") if env_path.exists() else ""
    changes = _diff(override_path, original_override, override_text)
    changes += _diff(env_path, original_env, env_text)
    if not changes:
        runtime.output.primary("Configuration already matches these answers.")
        return 0

    runtime.output.primary("Proposed configuration changes:")
    runtime.output.primary(changes.rstrip())
    runtime.output.primary("Inbound firewall ports:")
    for item in plan:
        runtime.output.primary(f"  {item}")
    runtime.output.primary("Allow these ports in both the host firewall and provider firewall.")
    if args.dry_run:
        runtime.output.primary("Dry run: no files were changed.")
        return 0
    if not runtime.confirm("Write the tls-edge site configuration?"):
        runtime.output.primary("Configuration was not changed.")
        return 0

    try:
        atomic_write(override_path, override_text, override_mode)
        atomic_write(env_path, env_text, env_mode)
    except OSError as exc:
        raise CliFailure(
            f"could not finish writing configuration: {exc}; inspect {override_path} "
            f"and {env_path} before retrying",
            exit_code=2,
        ) from exc
    runtime.output.primary(f"Saved {override_path} and {env_path}.")
    runtime.output.primary(
        f"Next: cd {root / 'stack'} && sudo ./ciu check && sudo ./ciu up --dry-run; "
        "review the rendered config, then deploy with CIU."
    )
    return 0


def show(args: Any, runtime: CliRuntime) -> int:
    root = _root(args)
    try:
        config, has_override = load_effective_config(root)
        mode = value_at(config, "tls", "mode", default="unknown")
        ports = validate_config(
            config,
            require_domain=False,
            require_certificates=False,
            stack_dir=root / "stack" / "ciu-stack",
        )
    except ConfigError as exc:
        raise CliFailure(str(exc), exit_code=2) from exc
    runtime.output.primary(f"Instance: {root}")
    runtime.output.primary(f"Site override: {'present' if has_override else 'not configured'}")
    runtime.output.primary(f"Base domain: {value_at(config, 'domain', default='(unset)') or '(unset)'}")
    runtime.output.primary(f"Certificate mode: {mode}")
    runtime.output.primary(
        f"HTTP/3: {'enabled' if value_at(config, 'http3', 'enabled', default=False) else 'disabled'}"
    )
    runtime.output.primary(
        f"Calls media: {'enabled' if value_at(config, 'calls', 'enabled', default=False) else 'disabled'}"
    )
    runtime.output.primary("Inbound ports:")
    for item in ports:
        runtime.output.primary(f"  {item}")
    if not value_at(config, "domain", default=""):
        runtime.output.primary("Run `tls-edge configure` to complete the instance settings.")
    return 0


def validate(args: Any, runtime: CliRuntime) -> int:
    root = _root(args)
    try:
        _, ports = _load_validated_site(root)
    except ConfigError as exc:
        raise CliFailure(str(exc), exit_code=2) from exc
    runtime.output.primary("tls-edge configuration is valid.")
    runtime.output.primary("Inbound ports:")
    for item in ports:
        runtime.output.primary(f"  {item}")
    return 0


def build_cli() -> Any:
    identity = _identity()
    root_option = OptionSpec(
        ("--root",),
        "tls-edge instance root (normally detected from this installed CLI)",
        metavar="PATH",
        parser_kwargs={"type": Path, "default": None},
    )
    registry = CliRegistry(
        identity,
        prog="tls-edge",
        description="Configure the local Traefik edge instance; CIU owns rendering and deployment.",
        getting_started=("tls-edge configure", "tls-edge validate", "tls-edge show"),
        unexpected_exceptions="report",
    )
    registry.register(VerbSpec(
        "configure",
        description="interactively configure TLS, public ports, HTTP/3, and optional media services",
        group=VerbGroup.MODIFICATION.value,
        mutating=True,
        dry_run=True,
        dry_run_help="show proposed changes without writing the instance files",
        options=(
            root_option,
            OptionSpec(("--answers",), "read configuration answers from a TOML file", metavar="FILE",
                       parser_kwargs={"type": Path, "default": None}),
        ),
        handler=configure,
    ))
    registry.register(VerbSpec(
        "show",
        description="show the effective edge features and public firewall ports",
        group=VerbGroup.EXPLORATION.value,
        include_confirmation=False,
        options=(root_option,),
        handler=show,
    ))
    registry.register(VerbSpec(
        "validate",
        description="validate TLS, listener, and port relationships in the instance configuration",
        group=VerbGroup.EXPLORATION.value,
        include_confirmation=False,
        options=(root_option,),
        handler=validate,
    ))
    return registry.build()


def main(argv: list[str] | None = None) -> int:
    try:
        app = build_cli()
    except VersionLookupError as exc:
        print(f"[ERROR] cannot determine tls-edge-cli version: {exc}", file=sys.stderr)
        return 2
    return app.run(argv=argv, interactive_extra="tls-edge-cli[interactive]")


if __name__ == "__main__":
    raise SystemExit(main())
