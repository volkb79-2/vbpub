"""CLI for configuring an installed tls-edge instance."""

from __future__ import annotations

import difflib
import sys
from collections.abc import Mapping
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
    atomic_write,
    deep_merge,
    load_answers,
    load_site,
    locate_instance_root,
    migrate,
    SCHEMA,
    updated_consumer_env,
    updated_override,
    validate_config,
    validate_answers,
)
from .fields import FIELDS, Field, Layout, MANAGED_KEYS, value_at


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


def _contains(config: Mapping[str, Any], path: tuple[str, ...]) -> bool:
    current: Any = config
    for part in path:
        if not isinstance(current, Mapping) or part not in current:
            return False
        current = current[part]
    return True


def _plain_set(config: dict[str, Any], path: tuple[str, ...], value: Any) -> None:
    current = config
    for part in path[:-1]:
        current = current.setdefault(part, {})
    current[path[-1]] = value


def _answer_extras(answers: Mapping[str, Any]) -> dict[str, Any]:
    """Return unmanaged scalar or list leaves supplied by the answers file."""
    extras: dict[str, Any] = {}

    def visit(table: Mapping[str, Any], prefix: tuple[str, ...]) -> None:
        for key, value in table.items():
            path = (*prefix, str(key))
            if isinstance(value, Mapping):
                visit(value, path)
                continue
            dotted = ".".join(path)
            if dotted not in MANAGED_KEYS and dotted != "schema":
                extras[dotted] = value

    tls_edge = answers.get("tls_edge")
    if isinstance(tls_edge, Mapping):
        visit(tls_edge, ())
    return extras


def _typecheck(field: Field, value: Any, source: str) -> Any:
    label = f"tls_edge.{field.key}"
    if field.kind == "bool":
        if not isinstance(value, bool):
            raise ConfigError(f"{label} must be true or false ({source})")
    elif field.kind == "port":
        if isinstance(value, bool) or not isinstance(value, int):
            raise ConfigError(
                f"{label} must be an integer from 1 through 65535 ({source})"
            )
        if not 1 <= value <= 65535:
            raise ConfigError(
                f"{label} must be an integer from 1 through 65535 ({source})"
            )
    elif field.kind in ("text", "choice"):
        if not isinstance(value, str):
            raise ConfigError(f"{label} must be a string ({source})")
        value = value.strip()
        if field.required and not value:
            raise ConfigError(f"{label} is required ({source})")
        if field.kind == "choice" and value not in field.choices:
            raise ConfigError(
                f"{label} must be one of {', '.join(field.choices)} ({source})"
            )
    else:
        if not isinstance(value, list):
            raise ConfigError(f"{label} must be a list ({source})")
    if field.check is not None:
        try:
            value = field.check(value)
        except ConfigError as exc:
            raise ConfigError(f"{exc} ({source})") from exc
    return value


def _prompt_value(
    runtime: CliRuntime,
    field: Field,
    default: Any,
) -> Any:
    assert field.prompt is not None
    if field.kind == "bool":
        return runtime.prompts.confirm(field.prompt, default=bool(default))
    if field.kind == "port":
        raw = runtime.prompts.text(
            field.prompt, default=str(default), required=True
        ).strip()
        if not (raw.isascii() and raw.isdecimal()):
            raise ConfigError(
                f"tls_edge.{field.key} must be an integer from 1 through 65535 "
                "(interactive prompt)"
            )
        return int(raw)
    if field.kind == "choice":
        return runtime.prompts.select(field.prompt, field.choices, default=str(default))
    if field.kind == "list":
        prompt_default = (
            ", ".join(str(item) for item in default)
            if default
            else None
        )
        raw = runtime.prompts.text(
            field.prompt,
            default=prompt_default,
            required=True,
        )
        return [item.strip() for item in raw.split(",")]
    prompt_default = (
        str(default)
        if default or not field.required
        else None
    )
    return runtime.prompts.text(
        field.prompt,
        default=prompt_default,
        required=field.required,
    )


def _propose(
    defaults: dict[str, Any],
    overlay: dict[str, Any],
    answers: dict[str, Any],
    root: Path,
    runtime: CliRuntime,
    *,
    allow_prompt: bool,
    source: str,
) -> tuple[dict[str, Any], list[str]]:
    proposed_overlay = deep_merge(overlay, answers)
    values: dict[str, Any] = {}
    layout = Layout(
        root=root.parent,
        stack_dir=root / "ciu-stack",
        overlay=overlay,
        edge={},
    )
    for field in FIELDS:
        edge = deep_merge(defaults, proposed_overlay)["tls_edge"]
        layout = Layout(layout.root, layout.stack_dir, layout.overlay, edge)
        from_answers = _contains(answers, ("tls_edge", *field.key.split(".")))
        field_source = source if from_answers else "effective config"
        if not field.when(edge):
            if from_answers:
                raise ConfigError(
                    f"tls_edge.{field.key} applies only when {field.when_text}"
                )
            continue
        if from_answers:
            raw = value_at(proposed_overlay["tls_edge"], field.key)
        elif allow_prompt and field.prompt is not None:
            prompt_default = value_at(edge, field.key)
            if (
                not _contains(overlay, ("tls_edge", *field.key.split(".")))
                and field.initial is not None
            ):
                prompt_default = field.initial(layout)
            raw = _prompt_value(runtime, field, prompt_default)
            field_source = f"interactive prompt for tls_edge.{field.key}"
        elif (
            not _contains(overlay, ("tls_edge", *field.key.split(".")))
            and field.initial is not None
        ):
            raw = field.initial(layout)
        else:
            raw = value_at(edge, field.key)
        value = _typecheck(field, raw, field_source)
        values[field.key] = value
        _plain_set(
            proposed_overlay,
            ("tls_edge", *field.key.split(".")),
            value,
        )
    _plain_set(proposed_overlay, ("tls_edge", "schema"), SCHEMA)
    proposed = deep_merge(defaults, proposed_overlay)
    plan = validate_config(
        proposed["tls_edge"],
        stack_dir=root / "ciu-stack",
    )
    return values, plan


def _load_checked_site(
    root: Path,
) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any]]:
    stack_dir = root / "ciu-stack"
    defaults, overlay, effective = load_site(stack_dir)
    overlay = migrate(overlay)
    check_override(defaults, overlay)
    return defaults, effective["tls_edge"], dict(overlay)


def _load_validated_site(
    root: Path,
    *,
    require_domain: bool = True,
) -> tuple[dict[str, Any], list[str]]:
    stack_dir = root / "ciu-stack"
    _, config, _ = _load_checked_site(root)
    return config, validate_config(
        config,
        require_domain=require_domain,
        stack_dir=stack_dir,
    )


def _render_config(
    root: Path,
    values: dict[str, Any],
    extra: Mapping[str, Any],
) -> tuple[Path, str, int, Path, str, int]:
    try:
        override_path, override_text, override_mode = updated_override(
            root,
            values,
            extra,
        )
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
        defaults, _, overlay = _load_checked_site(root)
        answers = load_answers(args.answers)
        answers = dict(migrate(answers))
        validate_answers(defaults, answers, answers_file=args.answers)
        extra = _answer_extras(answers)
        values, plan = _propose(
            defaults,
            overlay,
            answers,
            root,
            runtime,
            allow_prompt=args.answers is None,
            source=f"answers file {args.answers}",
        )
        override_path, override_text, override_mode, env_path, env_text, env_mode = (
            _render_config(root, values, extra)
        )
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
        f"Next: cd {root} && sudo ./ciu check && sudo ./ciu up --dry-run; "
        "review the rendered config, then deploy with CIU."
    )
    return 0


def show(args: Any, runtime: CliRuntime) -> int:
    root = _root(args)
    try:
        _, config, overlay = _load_checked_site(root)
        has_override = bool(overlay)
        mode = value_at(config, "tls.mode")
        ports = validate_config(
            config,
            require_domain=False,
            require_certificates=False,
            stack_dir=root / "ciu-stack",
        )
    except ConfigError as exc:
        raise CliFailure(str(exc), exit_code=2) from exc
    runtime.output.primary(f"Stack dir: {root}")
    runtime.output.primary(f"Site override: {'present' if has_override else 'not configured'}")
    runtime.output.primary(f"Base domain: {value_at(config, 'domain') or '(unset)'}")
    runtime.output.primary(f"Certificate mode: {mode}")
    runtime.output.primary(
        f"HTTP/3: {'enabled' if value_at(config, 'http3.enabled') else 'disabled'}"
    )
    runtime.output.primary(
        f"Calls media: {'enabled' if value_at(config, 'calls.enabled') else 'disabled'}"
    )
    runtime.output.primary("Inbound ports:")
    for item in ports:
        runtime.output.primary(f"  {item}")
    if not value_at(config, "domain"):
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
        "tls-edge stack directory containing ciu-stack/ "
        "(normally detected from this installed CLI)",
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
            OptionSpec(("--answers",), "a sparse site override in [tls_edge.*] shape", metavar="FILE",
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
