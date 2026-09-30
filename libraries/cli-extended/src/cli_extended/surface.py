"""Stable registry introspection and bounded CLI review dimensions.

The exported surface combines immutable registry declarations with the
argparse tree actually built by :class:`CliRegistry`. The declarations carry
product labels and stable IDs; parser actions include syntax added by custom
configuration callbacks.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from enum import Enum
from typing import Any

from .parser import (
    ArgumentSpec,
    OptionSpec,
    RegisteredCli,
    VerbSpec,
    _HelpAction,
    _VersionAction,
    _common_option_specs,
)

SURFACE_SCHEMA_VERSION = 3
DEFAULT_MAX_CANDIDATES = 512
_LIBRARY_OWNED_COMMON_OPTIONS = {
    "--help", "--version", "--log-level", "--quiet", "--debug", "--verbose",
    "--color", "--no-color", "--progress",
}
_DEFAULT_NEGATIVE_NUMBER_MATCHER = argparse.ArgumentParser(add_help=False)._negative_number_matcher


class SurfaceError(ValueError):
    """The registered CLI cannot be represented as the requested surface."""


class SurfaceLimitError(SurfaceError):
    """The requested review surface is larger than its explicit safety limit."""


def _callable_label(value: Any) -> str:
    module = getattr(value, "__module__", None)
    qualname = getattr(value, "__qualname__", None)
    if isinstance(module, str) and isinstance(qualname, str):
        return f"{module}.{qualname}"
    value_type = type(value)
    return f"{value_type.__module__}.{value_type.__qualname__}"


def _normalize(value: Any, *, path: str, opaque: list[str]) -> Any:
    """Return JSON-safe data without unstable object reprs."""

    if value is argparse.SUPPRESS:
        return {"kind": "suppressed"}
    if value is None or isinstance(value, (str, bool, int)):
        return value
    if isinstance(value, float):
        if math.isfinite(value):
            return value
        opaque.append(path)
        return {"opaque": "non-finite-float"}
    if isinstance(value, Enum):
        return _normalize(value.value, path=path, opaque=opaque)
    if isinstance(value, Mapping):
        normalized: dict[str, Any] = {}
        for key in sorted(value, key=lambda item: str(item)):
            if not isinstance(key, str):
                opaque.append(path)
                return {"opaque": _callable_label(value)}
            normalized[key] = _normalize(
                value[key], path=f"{path}.{key}", opaque=opaque
            )
        return normalized
    if isinstance(value, (tuple, list)):
        return [
            _normalize(item, path=f"{path}[{index}]", opaque=opaque)
            for index, item in enumerate(value)
        ]
    if isinstance(value, (set, frozenset)):
        normalized = [
            _normalize(item, path=f"{path}[]", opaque=opaque) for item in value
        ]
        return sorted(
            normalized,
            key=lambda item: json.dumps(
                item, separators=(",", ":"), ensure_ascii=False
            ),
        )
    if callable(value):
        label = _callable_label(value)
        if label not in {
            "builtins.str",
            "builtins.int",
            "builtins.float",
            "builtins.bool",
        }:
            opaque.append(path)
        return {"callable": label}
    opaque.append(path)
    return {"opaque": _callable_label(value)}


def _canonical_flag(flags: Sequence[str]) -> str:
    return next((flag for flag in flags if flag.startswith("--")), flags[0])


def _minimum_values(nargs: Any) -> int:
    """Return the minimum tokens an argparse action consumes when present."""

    if nargs is None:
        return 1
    if isinstance(nargs, int):
        return max(0, nargs)
    if nargs == "+" or nargs == argparse.PARSER:
        return 1
    return 0


def _spec_for_action(
    action: argparse.Action,
    specs: Sequence[OptionSpec],
) -> OptionSpec | None:
    action_flags = set(action.option_strings)
    return next((spec for spec in specs if action_flags.intersection(spec.flags)), None)


def _argument_for_action(
    action: argparse.Action,
    specs: Sequence[ArgumentSpec],
) -> ArgumentSpec | None:
    return next((spec for spec in specs if spec.name == action.dest), None)


def _mutex_groups(
    parser: argparse.ArgumentParser,
    option_specs: Sequence[OptionSpec],
    *,
    global_options: Sequence[OptionSpec] = (),
    route_id: str,
) -> dict[int, tuple[str, bool]]:
    """Map parser actions to stable exclusive-group IDs and requiredness."""

    result: dict[int, tuple[str, bool]] = {}
    for group in parser._mutually_exclusive_groups:
        actions = tuple(group._group_actions)
        declarations: set[str] = set()
        for action in actions:
            spec = _spec_for_action(action, option_specs) or _spec_for_action(
                action, global_options
            )
            if spec is not None and spec.mutually_exclusive_group is not None:
                declarations.add(spec.mutually_exclusive_group)
        if len(declarations) > 1:
            raise SurfaceError(
                f"route {route_id!r} has a parser exclusive group with conflicting labels"
            )
        if declarations:
            group_id = next(iter(declarations))
        else:
            member_names = sorted(
                _canonical_flag(action.option_strings)
                for action in actions
                if action.option_strings
            )
            digest = hashlib.sha256(
                json.dumps(member_names, separators=(",", ":")).encode("utf-8")
            ).hexdigest()[:12]
            group_id = f"parser-exclusive-{digest}"
        for action in actions:
            result[id(action)] = (str(group_id), bool(group.required))
    return result


def _group_titles(parser: argparse.ArgumentParser) -> dict[int, str]:
    result: dict[int, str] = {}
    for group in parser._action_groups:
        for action in group._group_actions:
            result[id(action)] = group.title
    return result


def _scope(
    action: argparse.Action,
    *,
    option_specs: Sequence[OptionSpec],
    global_options: Sequence[OptionSpec],
    single_command: bool,
) -> tuple[str, dict[str, bool]]:
    flags = set(action.option_strings)
    if any(flags.intersection(spec.flags) for spec in global_options):
        scope = "global"
    else:
        common_flags = {
            flag
            for spec in _common_option_specs(
                include_json=True,
                include_progress=True,
                include_confirmation=True,
            )
            for flag in spec.flags
        }
        if flags.intersection(common_flags):
            scope = "common"
        elif any(flags.intersection(spec.flags) for spec in option_specs):
            scope = "verb-local"
        else:
            scope = "custom"
    placement = {
        "before_verb": not single_command and scope in {"global", "common"},
        "after_verb": not single_command or scope in {"global", "common", "verb-local", "custom"},
        "single_command_invocation": single_command,
    }
    return scope, placement


def _effective_default(
    action: argparse.Action,
    *,
    scope: str,
    global_options: Sequence[OptionSpec],
    single_command: bool,
) -> tuple[bool, Any]:
    """Return the invocation default, resolving suppressed inherited copies.

    Verb parsers suppress defaults on repeated root/common options so parsing
    does not overwrite a value supplied before the verb. The root action still
    supplies the effective default for an absent option.
    """

    if action.dest in {"help", "version"}:
        return False, argparse.SUPPRESS
    if action.default is not argparse.SUPPRESS:
        return True, action.default
    if single_command:
        return False, argparse.SUPPRESS
    if scope == "global":
        option = _spec_for_action(action, global_options)
        if option is not None:
            value = option.parser_kwargs.get("default", None)
            return value is not argparse.SUPPRESS, value
    if scope == "common":
        # add_common_options installs root controls with argparse's None default.
        return True, None
    return False, argparse.SUPPRESS


def _safe_choice_values(
    value: Any, *, path: str, opaque: list[str]
) -> Any:
    """Normalize finite argparse choices or mark an unenumerable set opaque."""

    if value is None:
        return None
    if isinstance(value, (tuple, list)):
        values = value
    elif isinstance(value, (set, frozenset)):
        values = tuple(value)
    else:
        opaque.append(path)
        return {"opaque": _callable_label(value)}
    normalized: list[Any] = []
    for index, item in enumerate(values):
        before = len(opaque)
        normalized_item = _normalize(item, path=f"{path}[{index}]", opaque=opaque)
        if len(opaque) != before or not isinstance(
            normalized_item, (str, int, float, bool)
        ):
            if len(opaque) == before:
                opaque.append(path)
            return {"opaque": "choice-value"}
        normalized.append(normalized_item)
    if isinstance(value, (set, frozenset)):
        normalized.sort(
            key=lambda item: json.dumps(
                item, separators=(",", ":"), ensure_ascii=False
            )
        )
    return normalized


def _surface_action(
    action: argparse.Action,
    *,
    route_id: str,
    route_path: tuple[str, ...],
    parser_path: tuple[str, ...],
    option_specs: Sequence[OptionSpec],
    argument_specs: Sequence[ArgumentSpec],
    global_options: Sequence[OptionSpec],
    single_command: bool,
    group_titles: Mapping[int, str],
    mutex_groups: Mapping[int, tuple[str, bool]],
    opaque: list[str],
) -> dict[str, Any]:
    if action.option_strings:
        flags = list(action.option_strings)
        spec = _spec_for_action(action, option_specs)
        global_spec = _spec_for_action(action, global_options)
        selected_spec = spec or global_spec
        canonical = _canonical_flag(flags)
        surface_id = (
            selected_spec.surface_id
            if selected_spec is not None and selected_spec.surface_id is not None
            else _action_surface_id(
                "option", route_id, route_path, parser_path, canonical
            )
        )
        scope, placement = _scope(
            action,
            option_specs=option_specs,
            global_options=global_options,
            single_command=single_command,
        )
        group_id, group_required = mutex_groups.get(id(action), (None, False))
        value_type = getattr(action, "type", None)
        type_data = (
            _normalize(value_type, path=f"{surface_id}.type", opaque=opaque)
            if value_type is not None
            else None
        )
        default = _normalize(
            action.default, path=f"{surface_id}.default", opaque=opaque
        )
        choices = _safe_choice_values(
            action.choices, path=f"{surface_id}.choices", opaque=opaque
        )
        const = _normalize(action.const, path=f"{surface_id}.const", opaque=opaque)
        metavar = _normalize(
            action.metavar, path=f"{surface_id}.metavar", opaque=opaque
        )
        default_present, effective_default = _effective_default(
            action,
            scope=scope,
            global_options=global_options,
            single_command=single_command,
        )
        normalized_effective_default = _normalize(
            effective_default,
            path=f"{surface_id}.effective_default",
            opaque=opaque,
        )
        action_label = f"{type(action).__module__}.{type(action).__qualname__}"
        library_builtin_action = type(action) in {_HelpAction, _VersionAction}
        if type(action).__module__ != "argparse" and not library_builtin_action:
            opaque.append(f"{surface_id}.action")
        description = (
            selected_spec.description
            if selected_spec
            else None
            if action.help == argparse.SUPPRESS
            else action.help
        )
        group_title = (
            selected_spec.group
            if selected_spec is not None
            else group_titles.get(id(action), "OPTIONS")
        )
        return {
            "kind": "option",
            "id": surface_id,
            "flags": flags,
            "canonical": canonical,
            "dest": action.dest,
            "action": action_label,
            "type": type_data,
            "nargs": _normalize(
                action.nargs, path=f"{surface_id}.nargs", opaque=opaque
            ),
            "minimum_values": _minimum_values(action.nargs),
            "required": bool(action.required),
            "choices": choices,
            "default": default,
            "default_present": default_present,
            "effective_default": normalized_effective_default,
            "const": const,
            "metavar": metavar,
            "help_group": group_title,
            "exclusive_group": group_id,
            "exclusive_required": group_required,
            "scope": scope,
            "placement": placement,
            "parser_path": list(parser_path),
            "before_nested_subcommand": len(parser_path) < len(route_path),
            "hidden": action.help == argparse.SUPPRESS,
            "description": description,
            "parser_kwargs": (
                {key: _normalize(value, path=f"{surface_id}.{key}", opaque=opaque)
                 for key, value in sorted(selected_spec.parser_kwargs.items())}
                if selected_spec is not None
                else {}
            ),
        }
    spec = _argument_for_action(action, argument_specs)
    surface_id = (
        spec.surface_id
        if spec is not None and spec.surface_id is not None
        else _action_surface_id(
            "argument", route_id, route_path, parser_path, action.dest
        )
    )
    default = _normalize(action.default, path=f"{surface_id}.default", opaque=opaque)
    choices = _safe_choice_values(
        action.choices, path=f"{surface_id}.choices", opaque=opaque
    )
    metavar = _normalize(
        action.metavar if action.metavar is not None else action.dest.upper(),
        path=f"{surface_id}.metavar",
        opaque=opaque,
    )
    value_type = getattr(action, "type", None)
    type_data = (
        _normalize(value_type, path=f"{surface_id}.type", opaque=opaque)
        if value_type is not None
        else None
    )
    action_label = f"{type(action).__module__}.{type(action).__qualname__}"
    library_builtin_action = type(action) in {_HelpAction, _VersionAction}
    if type(action).__module__ != "argparse" and not library_builtin_action:
        opaque.append(f"{surface_id}.action")
    const = _normalize(action.const, path=f"{surface_id}.const", opaque=opaque)
    nargs = action.nargs
    minimum_values = _minimum_values(nargs)
    required = minimum_values > 0
    return {
        "kind": "argument",
        "id": surface_id,
        "action": action_label,
        "name": action.dest,
        "metavar": metavar,
        "nargs": _normalize(nargs, path=f"{surface_id}.nargs", opaque=opaque),
        "minimum_values": minimum_values,
        "required": required,
        "choices": choices,
        "default": default,
        "const": const,
        "type": type_data,
        "description": spec.description if spec else action.help,
        "scope": "positional",
        "hidden": action.help == argparse.SUPPRESS,
        "help_group": group_titles.get(id(action), "ARGUMENTS"),
        "parser_kwargs": (
            {
                key: _normalize(value, path=f"{surface_id}.{key}", opaque=opaque)
                for key, value in sorted(spec.parser_kwargs.items())
            }
            if spec is not None
            else {}
        ),
        "parser_path": list(parser_path),
        "before_nested_subcommand": len(parser_path) < len(route_path),
    }


def _action_surface_id(
    kind: str,
    route_id: str,
    route_path: tuple[str, ...],
    parser_path: tuple[str, ...],
    name: str,
) -> str:
    """Build a route-local ID that distinguishes shadowed parent actions."""

    owner = ""
    if parser_path != route_path:
        owner = "/parser:" + ("/".join(parser_path) or "<root>")
    return f"{kind}:{route_id}{owner}/{name}"


@dataclass(frozen=True)
class _ParserActionContext:
    """Actions and their declarations at one argparse parser depth."""

    parser_path: tuple[str, ...]
    allow_abbrev: bool
    prefix_chars: str
    fromfile_prefix_chars: str | None
    actions: tuple[argparse.Action, ...]
    option_specs: tuple[OptionSpec, ...]
    argument_specs: tuple[ArgumentSpec, ...]
    group_titles: Mapping[int, str]
    mutex_groups: Mapping[int, tuple[str, bool]]
    negative_number_matcher: Mapping[str, Any] | None = None
    has_negative_number_optionals: bool = False
    negative_number_matcher_custom: bool = False


def _negative_number_settings(
    parser: argparse.ArgumentParser,
) -> tuple[dict[str, Any] | None, bool, bool]:
    """Capture argparse's runtime negative-number boundary for one parser."""

    matcher = getattr(parser, "_negative_number_matcher", None)
    pattern = getattr(matcher, "pattern", None)
    flags = getattr(matcher, "flags", None)
    if (
        not isinstance(matcher, re.Pattern)
        or not isinstance(pattern, str)
        or not isinstance(flags, int)
    ):
        return None, bool(getattr(parser, "_has_negative_number_optionals", False)), True
    rule = {"pattern": pattern, "flags": flags}
    default_rule = {
        "pattern": _DEFAULT_NEGATIVE_NUMBER_MATCHER.pattern,
        "flags": _DEFAULT_NEGATIVE_NUMBER_MATCHER.flags,
    }
    return (
        rule,
        bool(getattr(parser, "_has_negative_number_optionals", False)),
        rule != default_rule,
    )


def _route_id(entrypoint_id: str, spec: VerbSpec | None, path: Sequence[str]) -> str:
    if spec is not None and spec.surface_id is not None:
        return spec.surface_id
    return f"route:{entrypoint_id}/" + "/".join(path) if path else f"route:{entrypoint_id}"


def _verb_metadata(spec: VerbSpec) -> dict[str, Any]:
    """Return the public, serializable contract carried by a registered verb."""

    return {
        "id": spec.surface_id,
        "name": spec.name,
        "description": spec.description,
        "summary": spec.summary,
        "group": spec.group,
        "behavior": list(spec.behavior_labels),
        "confirmation": spec.confirmation_enabled,
        "synopsis": spec.synopsis,
    }


def _describe_parser(
    parser: argparse.ArgumentParser,
    *,
    entrypoint_id: str,
    path: tuple[str, ...],
    verb_specs: tuple[VerbSpec, ...],
    route_spec: VerbSpec | None = None,
    delegated_specs: tuple[VerbSpec, ...] = (),
    global_options: Sequence[OptionSpec],
    single_command: bool,
    no_args_action: bool = False,
    incomplete: list[str],
    inherited_contexts: tuple[_ParserActionContext, ...] = (),
    inherited_parser_settings: tuple[Mapping[str, Any], ...] = (),
    nested_route: bool = False,
    child_aliases: tuple[str, ...] = (),
    child_help: str | None = None,
) -> list[dict[str, Any]]:
    selected_spec = route_spec or (verb_specs[0] if verb_specs else None)
    route_id = _route_id(
        entrypoint_id,
        selected_spec if not nested_route else None,
        path,
    )
    option_specs = tuple(
        option
        for spec in verb_specs
        for option in spec.options
    ) if not nested_route else ()
    argument_specs = tuple(
        argument
        for spec in verb_specs
        for argument in spec.arguments
    ) if not nested_route else ()
    group_titles = _group_titles(parser)
    mutex_groups = _mutex_groups(
        parser,
        option_specs,
        global_options=global_options,
        route_id=route_id,
    )
    opaque: list[str] = []
    actions: list[dict[str, Any]] = []
    local_actions: list[argparse.Action] = []
    subparser_actions: list[argparse._SubParsersAction] = []
    local_incomplete: list[str] = []
    for action in parser._actions:
        if isinstance(action, argparse._SubParsersAction):
            subparser_actions.append(action)
            continue
        local_actions.append(action)

    (
        negative_number_matcher,
        has_negative_number_optionals,
        negative_number_matcher_custom,
    ) = _negative_number_settings(parser)
    context = _ParserActionContext(
        parser_path=path,
        allow_abbrev=bool(parser.allow_abbrev),
        prefix_chars=str(parser.prefix_chars),
        fromfile_prefix_chars=parser.fromfile_prefix_chars,
        actions=tuple(local_actions),
        option_specs=option_specs,
        argument_specs=argument_specs,
        group_titles=group_titles,
        mutex_groups=mutex_groups,
        negative_number_matcher=negative_number_matcher,
        has_negative_number_optionals=has_negative_number_optionals,
        negative_number_matcher_custom=negative_number_matcher_custom,
    )
    contexts = (*inherited_contexts, context)
    parser_settings = (
        *inherited_parser_settings,
        *(
            {
                "parser_path": list(action_context.parser_path),
                "allow_abbrev": action_context.allow_abbrev,
                "prefix_chars": action_context.prefix_chars,
                "fromfile_prefix_chars": action_context.fromfile_prefix_chars,
                "negative_number_matcher": action_context.negative_number_matcher,
                "has_negative_number_optionals": (
                    action_context.has_negative_number_optionals
                ),
                "negative_number_matcher_custom": (
                    action_context.negative_number_matcher_custom
                ),
            }
            for action_context in contexts
        ),
    )
    for setting in parser_settings:
        setting_path = " ".join(setting["parser_path"]) or "<entrypoint>"
        prefix_chars = setting["prefix_chars"]
        if prefix_chars != "-":
            local_incomplete.append(
                f"{route_id}: parser {setting_path} has unsupported "
                f"prefix_chars {prefix_chars!r}"
            )
        fromfile_prefix_chars = setting.get("fromfile_prefix_chars")
        if fromfile_prefix_chars is not None:
            local_incomplete.append(
                f"{route_id}: parser {setting_path} uses unsupported "
                f"argument-file expansion via "
                f"fromfile_prefix_chars={fromfile_prefix_chars!r}"
            )
        if setting.get("negative_number_matcher_custom"):
            local_incomplete.append(
                f"{route_id}: parser {setting_path} customizes argparse's "
                "negative-number matcher"
            )
        elif not isinstance(setting.get("negative_number_matcher"), Mapping):
            local_incomplete.append(
                f"{route_id}: parser {setting_path} has an uninspectable "
                "negative-number matcher"
            )
    for action_context in contexts:
        for action in action_context.actions:
            actions.append(
                _surface_action(
                    action,
                    route_id=route_id,
                    route_path=path,
                    parser_path=action_context.parser_path,
                    option_specs=action_context.option_specs,
                    argument_specs=action_context.argument_specs,
                    global_options=global_options,
                    single_command=single_command,
                    group_titles=action_context.group_titles,
                    mutex_groups=action_context.mutex_groups,
                    opaque=opaque,
                )
            )

    if len(subparser_actions) > 1:
        local_incomplete.append(
            f"{route_id}: multiple nested subcommand groups cannot be represented"
        )

    nested_children: list[tuple[argparse.ArgumentParser, list[str], str | None]] = []
    subcommand_groups: list[dict[str, Any]] = []
    if len(subparser_actions) <= 1:
        for subparser_action in subparser_actions:
            children_by_parser: dict[
                int, tuple[argparse.ArgumentParser, list[str], str | None]
            ] = {}
            help_by_name = {
                str(choice.dest): choice.help
                for choice in subparser_action._choices_actions
            }
            for child_name, child_parser in subparser_action.choices.items():
                if not isinstance(child_parser, argparse.ArgumentParser):
                    local_incomplete.append(
                        f"{route_id}: subcommand {child_name!r} is not a parser"
                    )
                    continue
                entry = children_by_parser.setdefault(
                    id(child_parser), (child_parser, [], help_by_name.get(str(child_name)))
                )
                entry[1].append(str(child_name))
            nested_children.extend(children_by_parser.values())
            subcommand_groups.append(
                {
                    "destination": subparser_action.dest,
                    "required": bool(subparser_action.required),
                    "subcommands": [],
                }
            )

    if opaque:
        opaque = sorted(set(opaque))
    incomplete_fields = {
        field
        for field in opaque
        if field.endswith((".choices", ".default", ".const", ".nargs", ".metavar"))
    }
    local_incomplete.extend(
        f"{route_id}: cannot enumerate parser field {field}"
        for field in sorted(incomplete_fields)
    )
    incomplete.extend(local_incomplete)
    subcommands_required = any(
        bool(action.required) for action in subparser_actions
    )
    record: dict[str, Any] = {
        "id": route_id,
        "path": list(path),
        "kind": "route-prefix" if subcommands_required else "invocation",
        "single_command": single_command,
        "no_args_action": no_args_action,
        "description": (
            parser.description
            if nested_route
            else selected_spec.description
            if selected_spec
            else parser.description
        ),
        "summary": (
            child_help or parser.description
            if nested_route
            else selected_spec.summary
            if selected_spec
            else parser.description
        ),
        "group": selected_spec.group if selected_spec else None,
        "behavior": list(selected_spec.behavior_labels) if selected_spec else [],
        "confirmation": selected_spec.confirmation_enabled if selected_spec else False,
        "synopsis_override": selected_spec.synopsis if selected_spec else None,
        "usage_override": parser.usage,
        "parser_settings": list(parser_settings),
        "aliases": list(child_aliases),
        "parser_configured_by_callback": bool(
            any(spec.configure is not None for spec in verb_specs)
        ),
        "actions": actions,
        "opaque_fields": opaque,
        "syntax_complete": not local_incomplete,
        "delegated_metadata": [
            _verb_metadata(spec)
            for spec in (*delegated_specs, *verb_specs[1:])
        ],
        "subcommands": [],
        "subcommands_required": subcommands_required,
        "subcommand_groups": subcommand_groups,
    }
    records = [record]
    for child_parser, child_names, help_text in nested_children:
        child_name, *aliases = child_names
        child_path = (*path, child_name)
        child_records = _describe_parser(
            child_parser,
            entrypoint_id=entrypoint_id,
            path=child_path,
            verb_specs=verb_specs,
            route_spec=selected_spec,
            delegated_specs=delegated_specs,
            global_options=global_options,
            single_command=single_command,
            no_args_action=no_args_action,
            incomplete=incomplete,
            inherited_contexts=contexts,
            inherited_parser_settings=inherited_parser_settings,
            nested_route=True,
            child_aliases=tuple(aliases),
            child_help=help_text,
        )
        record["subcommands"].append(child_records[0]["id"])
        subcommand_groups[0]["subcommands"].append(child_records[0]["id"])
        records.extend(child_records)
    return records


def _walk_registered_cli(
    app: RegisteredCli,
    *,
    entrypoint_id: str,
    path_prefix: tuple[str, ...],
    inherited_specs: tuple[VerbSpec, ...] = (),
    inherited_globals: tuple[OptionSpec, ...] = (),
    inherited_parser_settings: tuple[Mapping[str, Any], ...] = (),
    single_command_route: str | None = None,
    incomplete: list[str],
) -> list[dict[str, Any]]:
    globals_here = (*app.global_options, *inherited_globals)
    local_incomplete: list[str] = []
    if inherited_globals:
        registered_global_flags = set(app.parser._option_string_actions)
        missing_inherited = sorted(
            flag
            for option in inherited_globals
            for flag in option.flags
            if flag not in registered_global_flags
        )
        if missing_inherited:
            reason = (
                f"{app.parser.prog}: delegated parser does not register inherited "
                "global option(s): " + ", ".join(missing_inherited)
            )
            local_incomplete.append(reason)
            incomplete.append(reason)
    if app.single_command:
        if not app.registered_verbs:
            reason = f"{app.parser.prog}: registry declarations were not retained"
            local_incomplete.append(reason)
            incomplete.append(reason)
            action_specs: tuple[VerbSpec, ...] = ()
            route_spec = inherited_specs[-1] if inherited_specs else None
            delegated_specs = inherited_specs[:-1]
        else:
            action_specs = (app.registered_verbs[0],)
            route_spec = inherited_specs[-1] if inherited_specs else app.registered_verbs[0]
            delegated_specs = (
                (*inherited_specs[:-1], app.registered_verbs[0])
                if inherited_specs
                else ()
            )
        if inherited_specs and any(
            spec.options or spec.arguments or spec.configure
            for spec in inherited_specs
        ):
            reason = (
                f"{app.parser.prog}: delegated single-command wrapper declares parser "
                "syntax that is not applied to the delegated parser"
            )
            local_incomplete.append(reason)
            incomplete.append(reason)
        path = path_prefix
        if single_command_route is None:
            route_id = _route_id(entrypoint_id, route_spec, path)
        else:
            route_id = single_command_route
        records = _describe_parser(
            app.parser,
            entrypoint_id=entrypoint_id,
            path=path,
            verb_specs=action_specs,
            route_spec=route_spec,
            delegated_specs=delegated_specs,
            global_options=globals_here,
            single_command=True,
            no_args_action=app.no_args_action,
            incomplete=incomplete,
            inherited_parser_settings=inherited_parser_settings,
        )
        if local_incomplete:
            for record in records:
                record["syntax_complete"] = False
        records[0]["id"] = route_id
        return records

    if not app.registered_verbs:
        reason = f"{app.parser.prog}: registry declarations were not retained"
        local_incomplete.append(reason)
        incomplete.append(reason)
        records = _describe_parser(
            app.parser,
            entrypoint_id=entrypoint_id,
            path=path_prefix,
            verb_specs=(),
            route_spec=inherited_specs[-1] if inherited_specs else None,
            delegated_specs=inherited_specs[:-1],
            global_options=globals_here,
            single_command=False,
            no_args_action=app.no_args_action,
            incomplete=incomplete,
            inherited_parser_settings=inherited_parser_settings,
        )
        for record in records:
            record["syntax_complete"] = False
        return records

    records: list[dict[str, Any]] = []
    (
        root_negative_number_matcher,
        root_has_negative_number_optionals,
        root_negative_number_matcher_custom,
    ) = _negative_number_settings(app.parser)
    root_parser_settings = {
        "parser_path": list(path_prefix),
        "allow_abbrev": bool(app.parser.allow_abbrev),
        "prefix_chars": str(app.parser.prefix_chars),
        "fromfile_prefix_chars": app.parser.fromfile_prefix_chars,
        "negative_number_matcher": root_negative_number_matcher,
        "has_negative_number_optionals": root_has_negative_number_optionals,
        "negative_number_matcher_custom": root_negative_number_matcher_custom,
    }
    parser_settings_for_children = (*inherited_parser_settings, root_parser_settings)
    for spec in app.registered_verbs:
        path = (*path_prefix, spec.name)
        route_id = _route_id(entrypoint_id, spec, path)
        delegate = app.delegates.get(spec.name, spec.delegate)
        if delegate is not None:
            if delegate.single_command:
                records.extend(
                    _walk_registered_cli(
                        delegate,
                        entrypoint_id=entrypoint_id,
                        path_prefix=path,
                        inherited_specs=(*inherited_specs, spec),
                        inherited_globals=globals_here,
                        inherited_parser_settings=parser_settings_for_children,
                        single_command_route=route_id,
                        incomplete=incomplete,
                    )
                )
            else:
                group_record = {
                    "id": route_id,
                    "path": list(path),
                    "kind": "delegate-group",
                    "single_command": delegate.single_command,
                    "no_args_action": delegate.no_args_action,
                    "description": spec.description,
                    "summary": spec.summary,
                    "group": spec.group,
                    "behavior": list(spec.behavior_labels),
                    "confirmation": spec.confirmation_enabled,
                    "synopsis_override": spec.synopsis,
                    "usage_override": None,
                    "parser_settings": list(parser_settings_for_children),
                    "aliases": [],
                    "parser_configured_by_callback": spec.configure is not None,
                    "actions": [],
                    "opaque_fields": [],
                    "delegated_metadata": [],
                    "subcommands": [],
                }
                if spec.options or spec.arguments or spec.configure is not None:
                    reason = (
                        f"{route_id}: delegated wrapper parser syntax is not applied to "
                        "the delegated CLI"
                    )
                    incomplete.append(reason)
                delegated_records = _walk_registered_cli(
                    delegate,
                    entrypoint_id=entrypoint_id,
                    path_prefix=path,
                    inherited_specs=(*inherited_specs, spec),
                    inherited_globals=globals_here,
                    inherited_parser_settings=parser_settings_for_children,
                    incomplete=incomplete,
                )
                group_record["subcommands"] = [
                    child["id"]
                    for child in delegated_records
                    if len(child.get("path", ())) == len(path) + 1
                ]
                group_record["syntax_complete"] = (
                    not (
                        local_incomplete
                        or spec.options
                        or spec.arguments
                        or spec.configure is not None
                    )
                    and all(
                        child["syntax_complete"]
                        for child in delegated_records
                    )
                )
                records.append(group_record)
                records.extend(delegated_records)
            continue
        parser = app.command_parsers.get(spec.name)
        if parser is None:
            incomplete.append(f"{route_id}: registered command parser is missing")
            continue
        records.extend(
            _describe_parser(
                parser,
                entrypoint_id=entrypoint_id,
                path=path,
                verb_specs=(spec,),
                delegated_specs=inherited_specs,
                global_options=globals_here,
                single_command=False,
                no_args_action=app.no_args_action,
                incomplete=incomplete,
                inherited_parser_settings=parser_settings_for_children,
            )
        )
    if local_incomplete:
        for record in records:
            record["syntax_complete"] = False
    return records


def _signature(schema_version: int, payload: Mapping[str, Any]) -> str:
    encoded = json.dumps(
        {"schema_version": schema_version, "payload": payload},
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
    ).encode("utf-8")
    return "sha256:" + hashlib.sha256(encoded).hexdigest()


def _candidate(
    *,
    case_id: str,
    route_id: str,
    kind: str,
    members: Sequence[str],
    payload: Mapping[str, Any],
    signature_context: Mapping[str, Any],
) -> dict[str, Any]:
    signature = _signature(
        SURFACE_SCHEMA_VERSION,
        {
            "route_id": route_id,
            "kind": kind,
            "members": list(members),
            "shape": dict(payload),
            "context": dict(signature_context),
        },
    )
    return {
        "id": case_id,
        "route_id": route_id,
        "kind": kind,
        "members": list(members),
        "shape": dict(payload),
        "signature": signature,
    }


def _candidate_id(route_id: str, kind: str, *parts: str) -> str:
    return "case:" + "/".join((route_id, kind, *parts))


def _generate_candidates(
    routes: Sequence[Mapping[str, Any]],
    interactions: Sequence[Mapping[str, Any]],
    *,
    max_candidates: int,
) -> list[dict[str, Any]]:
    candidates: list[dict[str, Any]] = []

    def add(candidate: dict[str, Any]) -> None:
        candidates.append(candidate)
        if len(candidates) > max_candidates:
            raise SurfaceLimitError(
                f"CLI review surface has more than {max_candidates} candidates; "
                "raise max_candidates explicitly"
            )

    route_index = {str(route["id"]): route for route in routes}

    def add_for_route(
        route: Mapping[str, Any],
        *,
        case_id: str,
        kind: str,
        members: Sequence[str],
        payload: Mapping[str, Any],
    ) -> None:
        actions_by_id = {
            str(action["id"]): action for action in route.get("actions", [])
        }
        route_context = {
            "path": route.get("path", []),
            "aliases": route.get("aliases", []),
            "kind": route.get("kind"),
            "single_command": route.get("single_command", False),
            "no_args_action": route.get("no_args_action", False),
            "synopsis_override": route.get("synopsis_override"),
            "usage_override": route.get("usage_override"),
            "behavior": route.get("behavior", []),
            "confirmation": route["confirmation"],
            "group": route.get("group"),
            "parser_settings": route.get("parser_settings", []),
            "delegated_contract": [
                {
                    key: metadata.get(key)
                    for key in (
                        "id",
                        "name",
                        "group",
                        "behavior",
                        "confirmation",
                        "synopsis",
                    )
                }
                for metadata in route.get("delegated_metadata", [])
            ],
        }
        member_shapes = []
        for member_id in sorted(set(members)):
            action = actions_by_id[member_id]
            member_shapes.append(
                {
                    key: action.get(key)
                    for key in (
                    "id", "kind", "flags", "canonical", "name", "dest",
                        "action", "type", "nargs", "minimum_values", "required", "choices",
                        "default", "default_present", "effective_default", "const", "metavar",
                        "exclusive_group", "exclusive_required", "scope", "placement",
                        "hidden",
                    )
                }
            )
        add(
            _candidate(
                case_id=case_id,
                route_id=str(route["id"]),
                kind=kind,
                members=members,
                payload=payload,
                signature_context={
                    "route": route_context,
                    "member_shapes": member_shapes,
                },
            )
        )

    for route in routes:
        route_id = str(route["id"])
        is_route_prefix = route.get("kind") in {"route-prefix", "delegate-group"}
        actions = route.get("actions", [])
        option_actions = [
            action
            for action in actions
            if action.get("kind") == "option"
            and not (
                action.get("scope") == "common"
                and set(action.get("flags", ())).issubset(_LIBRARY_OWNED_COMMON_OPTIONS)
            )
        ]
        positional_actions = [action for action in actions if action.get("kind") == "argument"]
        groups: dict[str, list[Mapping[str, Any]]] = {}
        for action in option_actions:
            group_id = action.get("exclusive_group")
            if group_id is not None:
                groups.setdefault(str(group_id), []).append(action)
        if not is_route_prefix:
            for alias in route.get("aliases", []):
                add_for_route(
                    route,
                    case_id=_candidate_id(route_id, "route-alias", str(alias)),
                    kind="route-alias",
                    members=(),
                    payload={"canonical_path": route.get("path", []), "alias": alias},
                )
        minimum_members = [
            str(action["id"])
            for action in positional_actions
            if action["required"]
        ] + [
            str(action["id"])
            for action in option_actions
            if action["required"]
            or action.get("effective_default", action.get("default"))
            not in (None, {"kind": "suppressed"})
        ] + [
            str(action["id"])
            for members in groups.values()
            if members[0].get("exclusive_required")
            for action in members
        ]
        if not is_route_prefix:
            add_for_route(
                route,
                case_id=_candidate_id(route_id, "minimum"),
                kind="minimum",
                members=minimum_members,
                payload={
                    "required_arguments": [action["id"] for action in positional_actions if action["required"]],
                    "required_argument_values": {
                        action["id"]: action.get("minimum_values", 0)
                        for action in positional_actions
                        if action["required"]
                    },
                    "required_options": [action["id"] for action in option_actions if action["required"]],
                    "defaulted_options": [
                        action["id"] for action in option_actions
                        if action.get("effective_default", action.get("default"))
                        not in (None, {"kind": "suppressed"})
                    ],
                    "required_exclusive_groups": {
                        group_id: [action["id"] for action in members]
                        for group_id, members in sorted(groups.items())
                        if members[0].get("exclusive_required")
                    },
                    },
            )
        if is_route_prefix:
            # Options and arguments declared at this parser depth also appear
            # in each executable descendant route, where they can be tested
            # with the required nested path present.
            continue
        for action in positional_actions:
            add_for_route(
                route,
                case_id=_candidate_id(route_id, "argument-shape", str(action["id"])),
                kind="argument-shape",
                members=(str(action["id"]),),
                payload={"argument_id": action["id"]},
            )
            for value in action["choices"] if isinstance(action["choices"], list) else ():
                value_key = hashlib.sha256(
                    json.dumps(value, ensure_ascii=False).encode("utf-8")
                ).hexdigest()[:10]
                add_for_route(
                    route,
                    case_id=_candidate_id(route_id, "argument-choice", str(action["id"]), value_key),
                    kind="argument-choice",
                    members=(str(action["id"]),),
                    payload={"argument_id": action["id"], "choice": value},
                )
        for action in option_actions:
            flags = action["flags"]
            for flag in flags:
                add_for_route(
                    route,
                    case_id=_candidate_id(route_id, "option-spelling", str(action["id"]), str(flag)),
                    kind="option-spelling",
                    members=(str(action["id"]),),
                    payload={"option_id": action["id"], "spelling": flag},
                )
            choices = action["choices"]
            for value in choices if isinstance(choices, list) else ():
                value_key = hashlib.sha256(
                    json.dumps(value, ensure_ascii=False).encode("utf-8")
                ).hexdigest()[:10]
                add_for_route(
                    route,
                    case_id=_candidate_id(route_id, "option-choice", str(action["id"]), value_key),
                    kind="option-choice",
                    members=(str(action["id"]),),
                    payload={"option_id": action["id"], "choice": value},
                )
        for group_id, members in sorted(groups.items()):
            for action in members:
                add_for_route(
                    route,
                    case_id=_candidate_id(route_id, "exclusive-member", group_id, str(action["id"])),
                    kind="exclusive-member",
                    members=(str(action["id"]),),
                    payload={
                            "group_id": group_id,
                            "required": bool(action["exclusive_required"]),
                            "selected_option": action["id"],
                        },
                )
            for left_index, left in enumerate(members):
                for right in members[left_index + 1 :]:
                    pair = sorted((str(left["id"]), str(right["id"])))
                    add_for_route(
                        route,
                        case_id=_candidate_id(route_id, "exclusive-conflict", group_id, *pair),
                        kind="exclusive-conflict",
                        members=pair,
                        payload={"group_id": group_id, "options": pair},
                    )
    for interaction in interactions:
        interaction_id = interaction.get("id")
        route_id = interaction.get("route_id")
        option_ids = interaction.get("option_ids", ())
        if not isinstance(interaction_id, str) or not interaction_id:
            raise SurfaceError("interaction groups need a non-empty id")
        if not isinstance(route_id, str) or route_id not in route_index:
            raise SurfaceError(f"interaction group {interaction_id!r} names an unknown route")
        route = route_index[route_id]
        if route.get("kind") != "invocation":
            raise SurfaceError(
                f"interaction group {interaction_id!r} names a non-invocable route"
            )
        known = {
            str(action["id"])
            for action in route.get("actions", [])
            if action.get("kind") == "option"
        }
        if (
            not isinstance(option_ids, (list, tuple))
            or not option_ids
            or any(not isinstance(option_id, str) or not option_id for option_id in option_ids)
        ):
            raise SurfaceError(
                f"interaction group {interaction_id!r} option_ids must be a non-empty string list"
            )
        if len(option_ids) != len(set(option_ids)):
            raise SurfaceError(
                f"interaction group {interaction_id!r} repeats an option ID"
            )
        normalized_ids = tuple(sorted(option_ids))
        missing = [option_id for option_id in normalized_ids if option_id not in known]
        if missing:
            raise SurfaceError(
                f"interaction group {interaction_id!r} names unknown options: "
                + ", ".join(missing)
            )
        add_for_route(
            route,
            case_id=f"case:{route_id}/interaction:{interaction_id}",
            kind="interaction",
            members=normalized_ids,
            payload={"interaction_id": interaction_id, "option_ids": list(normalized_ids)},
        )
    candidates.sort(key=lambda candidate: str(candidate["id"]))
    candidate_ids = [str(candidate["id"]) for candidate in candidates]
    if len(candidate_ids) != len(set(candidate_ids)):
        raise SurfaceError("CLI review candidates contain duplicate IDs")
    return candidates


def export_cli_surface(
    app: RegisteredCli,
    *,
    interaction_groups: Sequence[Mapping[str, Any]] = (),
    max_candidates: int = DEFAULT_MAX_CANDIDATES,
) -> dict[str, Any]:
    """Return a deterministic machine-readable grammar and review checklist."""

    if not isinstance(app, RegisteredCli):
        raise TypeError("app must be a RegisteredCli built by CliRegistry")
    if not isinstance(max_candidates, int) or isinstance(max_candidates, bool):
        raise TypeError("max_candidates must be an integer")
    if max_candidates < 1:
        raise ValueError("max_candidates must be positive")
    if not isinstance(interaction_groups, Sequence) or isinstance(
        interaction_groups, (str, bytes)
    ):
        raise TypeError("interaction_groups must be a sequence of mappings")
    for index, interaction in enumerate(interaction_groups):
        if not isinstance(interaction, Mapping):
            raise TypeError(f"interaction_groups[{index}] must be a mapping")
    entrypoint_id = f"entrypoint:{app.identity.command_name}"
    incomplete: list[str] = []
    routes = _walk_registered_cli(
        app,
        entrypoint_id=entrypoint_id,
        path_prefix=(),
        incomplete=incomplete,
    )
    routes.sort(key=lambda route: str(route["id"]))
    route_ids = [str(route["id"]) for route in routes]
    if len(route_ids) != len(set(route_ids)):
        raise SurfaceError("CLI surface contains duplicate route IDs")
    for route in routes:
        action_ids: list[str] = []
        for action in route.get("actions", []):
            action_ids.append(str(action["id"]))
        if len(action_ids) != len(set(action_ids)):
            raise SurfaceError(
                f"CLI route {route['id']!r} contains duplicate argument or option IDs"
            )
    candidates = _generate_candidates(
        routes,
        interaction_groups,
        max_candidates=max_candidates,
    )
    builtins = ["help"]
    if not app.single_command:
        builtins.append("help <verb>")
    builtins.extend(("version", "--help", "--version"))
    return {
        "schema_version": SURFACE_SCHEMA_VERSION,
        "entrypoint": {
            "id": entrypoint_id,
            "command": app.identity.command_name,
            "display_name": app.identity.name,
            "long_name": app.identity.long_name,
            "prog": app.parser.prog,
            "single_command": app.single_command,
            "no_args_action": app.no_args_action,
            "allow_abbrev": bool(app.parser.allow_abbrev),
            "prefix_chars": app.parser.prefix_chars,
            "fromfile_prefix_chars": app.parser.fromfile_prefix_chars,
            "builtins": builtins,
        },
        "routes": routes,
        "candidates": candidates,
        "incomplete": sorted(set(incomplete)),
        "syntax_complete": not incomplete
        and all(bool(route.get("syntax_complete")) for route in routes),
    }


def render_cli_surface_json(surface: Mapping[str, Any]) -> str:
    """Serialize a surface with stable keys, indentation, and a final newline."""

    return json.dumps(
        surface,
        sort_keys=True,
        indent=2,
        ensure_ascii=False,
        allow_nan=False,
    ) + "\n"
