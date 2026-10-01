"""Product-owned semantic review catalogs and generated CLI specification blocks."""

from __future__ import annotations

import argparse
import json
import os
import re
import shlex
import stat
import tempfile
import tomllib
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .surface import (
    DEFAULT_MAX_CANDIDATES,
    SurfaceError,
    _ARGPARSE_CHOICE_ACTION_LABELS,
    _BUILTIN_TYPE_LABELS,
    _minimum_values,
    _route_required_baseline,
    export_cli_surface,
    render_cli_surface_json,
)
from .parser import RegisteredCli

REVIEW_SCHEMA_VERSION = 1
_REVIEW_CASE_STATES = ("pending", "active", "retired")
_REVIEW_DECISIONS = ("accept", "refuse")
SURFACE_START_MARKER = "<!-- cli-extended-surface:start -->"
SURFACE_END_MARKER = "<!-- cli-extended-surface:end -->"

_BUILTIN_CHOICE_CONVERTERS = {
    label: converter for converter, label in _BUILTIN_TYPE_LABELS.items()
}
_ARGPARSE_CHOICE_ACTIONS = frozenset(_ARGPARSE_CHOICE_ACTION_LABELS)


class ReviewCatalogError(ValueError):
    """A semantic review catalog is malformed or belongs to another CLI."""


class SurfaceSpecError(ValueError):
    """A consumer specification has no safe, unique generated region."""


@dataclass(frozen=True)
class ReviewCase:
    case_id: str
    state: str
    decision: str | None
    reviewed_signature: str | None
    rationale: str
    invocation: tuple[str, ...]
    invocation_declared: bool
    expected_exit_status: int | None
    expected_stdout_contains: str
    expected_stderr_contains: str
    effects: tuple[str, ...]
    effects_declared: bool
    test_ids: tuple[str, ...]
    retirement_reason: str


@dataclass(frozen=True)
class ReviewCatalog:
    cli_id: str
    max_candidates: int
    interaction_groups: tuple[Mapping[str, Any], ...]
    cases: tuple[ReviewCase, ...]

    @property
    def cases_by_id(self) -> dict[str, ReviewCase]:
        return {case.case_id: case for case in self.cases}


@dataclass(frozen=True)
class SurfaceReport:
    findings: tuple[str, ...] = ()

    @property
    def passed(self) -> bool:
        return not self.findings

    def render(self) -> str:
        return "\n".join(self.findings)


def _choice_values(
    action: Mapping[str, Any], values: Sequence[Any]
) -> tuple[str, tuple[Any, ...]]:
    """Model only argparse's exact, safe built-in conversions for choices.

    Consumer converters and nonstandard argparse actions are deliberately
    opaque here. Their behavior belongs in the linked real-CLI test.
    """

    action_label = action.get("action")
    if action_label is not None:
        if not isinstance(action_label, str):
            return "opaque", ()
        if action_label not in _ARGPARSE_CHOICE_ACTIONS:
            return "opaque", ()
    type_spec = action.get("type")
    if type_spec is None:
        return "modeled", tuple(values)
    converter_label = (
        type_spec.get("callable") if isinstance(type_spec, Mapping) else None
    )
    if not isinstance(converter_label, str):
        return "opaque", ()
    converter = _BUILTIN_CHOICE_CONVERTERS.get(converter_label)
    if converter is None:
        return "opaque", ()
    try:
        return "modeled", tuple(converter(value) for value in values)
    except (OverflowError, TypeError, ValueError):
        return "invalid", ()


def _choice_values_accept(
    action: Mapping[str, Any], values: Sequence[str], choice: Any
) -> bool:
    """Return whether an argv value resolves to a reviewed choice.

    Opaque consumer converters cannot be evaluated from the manifest. Their
    linked behavior tests are the acceptance oracle.
    """

    status, converted = _choice_values(action, values)
    if status == "opaque":
        return True
    return status == "modeled" and any(value == choice for value in converted)


def _choices_accept(action: Mapping[str, Any], values: Sequence[str]) -> bool:
    choices = action.get("choices")
    if not isinstance(choices, list):
        return True
    checked_values: Sequence[Any] = values
    if not values and action.get("nargs") == "?" and action.get("const") is not None:
        const = action["const"]
        if type(const) not in (str, int, float, bool):
            return True
        checked_values = (const,)
    status, converted = _choice_values(action, checked_values)
    if status == "opaque":
        return True
    if status == "invalid":
        return False
    return all(value in choices for value in converted)


def _minimum_action_values(action: Mapping[str, Any]) -> int:
    minimum = action.get("minimum_values")
    if minimum is None:
        return _minimum_values(action.get("nargs"))
    return int(minimum)


def _value_count_accepts(action: Mapping[str, Any], values: Sequence[str]) -> bool:
    nargs = action.get("nargs")
    minimum = _minimum_action_values(action)
    if len(values) < minimum:
        return False
    if nargs is None and len(values) != 1:
        return False
    if isinstance(nargs, int) and len(values) != nargs:
        return False
    return nargs != "?" or len(values) <= 1


def _value_shape_accepts(
    action: Mapping[str, Any], values: Sequence[str]
) -> bool:
    return _value_count_accepts(action, values) and _choices_accept(action, values)


def _option_occurrences_accept(
    action: Mapping[str, Any],
    occurrences: Sequence[tuple[str, tuple[str, ...], bool]],
) -> bool:
    if not occurrences:
        return False
    if action.get("nargs") == 0:
        return all(not inline and not values for _spelling, values, inline in occurrences)
    return all(
        _value_shape_accepts(action, values)
        for _spelling, values, _inline in occurrences
    )


def _nonempty_string(value: Any, *, location: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ReviewCatalogError(f"{location} must be a non-empty string")
    return value


def _reject_unknown_fields(
    value: Mapping[str, Any], allowed: set[str], *, location: str
) -> None:
    unknown = sorted(set(value) - allowed)
    if unknown:
        raise ReviewCatalogError(
            f"{location} has unknown field(s): " + ", ".join(unknown)
        )


def _string_tuple(
    value: Any,
    *,
    location: str,
    required: bool = False,
    unique: bool = True,
) -> tuple[str, ...]:
    if not isinstance(value, list):
        raise ReviewCatalogError(f"{location} must be an array of strings")
    items = tuple(_nonempty_string(item, location=location) for item in value)
    if required and not items:
        raise ReviewCatalogError(f"{location} must not be empty")
    if unique and len(items) != len(set(items)):
        raise ReviewCatalogError(f"{location} contains duplicates")
    return items


def _parse_interaction_groups(raw_groups: Any) -> tuple[Mapping[str, Any], ...]:
    if raw_groups is None:
        return ()
    if not isinstance(raw_groups, list):
        raise ReviewCatalogError("interaction_groups must be an array of tables")
    result: list[Mapping[str, Any]] = []
    group_ids: set[str] = set()
    for index, raw_group in enumerate(raw_groups):
        location = f"interaction_groups[{index}]"
        if not isinstance(raw_group, dict):
            raise ReviewCatalogError(f"{location} must be a table")
        _reject_unknown_fields(
            raw_group, {"id", "route_id", "combinations"}, location=location
        )
        group_id = _nonempty_string(raw_group.get("id"), location=f"{location}.id")
        route_id = _nonempty_string(
            raw_group.get("route_id"), location=f"{location}.route_id"
        )
        if group_id in group_ids:
            raise ReviewCatalogError(f"duplicate interaction group id {group_id!r}")
        group_ids.add(group_id)
        combination_ids: set[str] = set()
        combinations = raw_group.get("combinations", [])
        if not isinstance(combinations, list):
            raise ReviewCatalogError(f"{location}.combinations must be an array of tables")
        if not combinations:
            raise ReviewCatalogError(f"{location}.combinations must not be empty")
        for combo_index, raw_combo in enumerate(combinations):
            combo_location = f"{location}.combinations[{combo_index}]"
            if not isinstance(raw_combo, dict):
                raise ReviewCatalogError(f"{combo_location} must be a table")
            _reject_unknown_fields(
                raw_combo, {"id", "option_ids"}, location=combo_location
            )
            combo_id = _nonempty_string(
                raw_combo.get("id"), location=f"{combo_location}.id"
            )
            if "/" in group_id or "/" in combo_id:
                raise ReviewCatalogError(
                    f"{combo_location} id components must not contain '/'"
                )
            if combo_id in combination_ids:
                raise ReviewCatalogError(f"duplicate interaction combination id {combo_id!r}")
            combination_ids.add(combo_id)
            option_ids = _string_tuple(
                raw_combo.get("option_ids"),
                location=f"{combo_location}.option_ids",
                required=True,
            )
            result.append(
                {
                    "id": f"{group_id}/{combo_id}",
                    "route_id": route_id,
                    "option_ids": option_ids,
                    "group_id": group_id,
                    "combination_id": combo_id,
                }
            )
    return tuple(result)


def load_cli_review_catalog(path: str | Path) -> ReviewCatalog:
    """Load and validate versioned TOML semantic decisions."""

    try:
        raw = tomllib.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, tomllib.TOMLDecodeError) as exc:
        raise ReviewCatalogError(f"cannot read CLI review catalog {path}: {exc}") from exc
    if not isinstance(raw, dict):
        raise ReviewCatalogError("CLI review catalog root must be a table")
    _reject_unknown_fields(
        raw,
        {"schema_version", "cli_id", "max_candidates", "interaction_groups", "cases"},
        location="CLI review catalog",
    )
    schema_version = raw.get("schema_version")
    if (
        not isinstance(schema_version, int)
        or isinstance(schema_version, bool)
        or schema_version != REVIEW_SCHEMA_VERSION
    ):
        raise ReviewCatalogError(
            f"unsupported CLI review catalog schema_version {schema_version!r}"
        )
    cli_id = _nonempty_string(raw.get("cli_id"), location="cli_id")
    max_candidates = raw.get("max_candidates", DEFAULT_MAX_CANDIDATES)
    if not isinstance(max_candidates, int) or isinstance(max_candidates, bool):
        raise ReviewCatalogError("max_candidates must be a positive integer")
    if max_candidates < 1:
        raise ReviewCatalogError("max_candidates must be a positive integer")
    interactions = _parse_interaction_groups(raw.get("interaction_groups"))
    raw_cases = raw.get("cases", [])
    if not isinstance(raw_cases, list):
        raise ReviewCatalogError("cases must be an array of tables")
    cases: list[ReviewCase] = []
    seen_ids: set[str] = set()
    for index, item in enumerate(raw_cases):
        location = f"cases[{index}]"
        if not isinstance(item, dict):
            raise ReviewCatalogError(f"{location} must be a table")
        _reject_unknown_fields(
            item,
            {
                "id", "state", "decision", "reviewed_signature", "rationale",
                "invocation", "expected_exit_status", "expected_stdout_contains",
                "expected_stderr_contains", "effects", "test_ids", "retirement_reason",
            },
            location=location,
        )
        case_id = _nonempty_string(item.get("id"), location=f"{location}.id")
        if case_id in seen_ids:
            raise ReviewCatalogError(f"duplicate case id {case_id!r}")
        seen_ids.add(case_id)
        state = item.get("state", "pending")
        if not isinstance(state, str):
            raise ReviewCatalogError(
                f"{location}.state must be " + ", ".join(_REVIEW_CASE_STATES)
            )
        if state not in _REVIEW_CASE_STATES:
            raise ReviewCatalogError(
                f"{location}.state must be " + ", ".join(_REVIEW_CASE_STATES)
            )
        decision = item.get("decision")
        if decision is not None and not isinstance(decision, str):
            raise ReviewCatalogError(
                f"{location}.decision must be " + " or ".join(_REVIEW_DECISIONS)
            )
        if decision is not None and decision not in _REVIEW_DECISIONS:
            raise ReviewCatalogError(
                f"{location}.decision must be " + " or ".join(_REVIEW_DECISIONS)
            )
        signature = item.get("reviewed_signature")
        if signature is not None and not isinstance(signature, str):
            raise ReviewCatalogError(f"{location}.reviewed_signature must be a string")
        rationale = item.get("rationale", "")
        if not isinstance(rationale, str):
            raise ReviewCatalogError(f"{location}.rationale must be a string")
        invocation_declared = "invocation" in item
        invocation = _string_tuple(
            item.get("invocation", []),
            location=f"{location}.invocation",
            unique=False,
        )
        expected_status = item.get("expected_exit_status")
        if expected_status is not None and (
            not isinstance(expected_status, int) or isinstance(expected_status, bool)
        ):
            raise ReviewCatalogError(f"{location}.expected_exit_status must be an integer")
        stdout_contains = item.get("expected_stdout_contains", "")
        stderr_contains = item.get("expected_stderr_contains", "")
        if not isinstance(stdout_contains, str) or not isinstance(stderr_contains, str):
            raise ReviewCatalogError(f"{location} expected output fields must be strings")
        effects_declared = "effects" in item
        effects = _string_tuple(item.get("effects", []), location=f"{location}.effects")
        test_ids = _string_tuple(item.get("test_ids", []), location=f"{location}.test_ids")
        retirement_reason = item.get("retirement_reason", "")
        if not isinstance(retirement_reason, str):
            raise ReviewCatalogError(f"{location}.retirement_reason must be a string")
        if state == "active":
            missing = []
            if decision is None:
                missing.append("decision")
            if signature is None:
                missing.append("reviewed_signature")
            if not rationale.strip():
                missing.append("rationale")
            if expected_status is None:
                missing.append("expected_exit_status")
            if not invocation_declared:
                missing.append("invocation")
            if not effects_declared:
                missing.append("effects")
            if not test_ids:
                missing.append("test_ids")
            if missing:
                raise ReviewCatalogError(
                    f"{location} active case is missing: " + ", ".join(missing)
                )
        if state == "retired" and not retirement_reason.strip():
            raise ReviewCatalogError(
                f"{location} retired case needs a retirement_reason"
            )
        cases.append(
            ReviewCase(
                case_id=case_id,
                state=state,
                decision=decision,
                reviewed_signature=signature,
                rationale=rationale,
                invocation=invocation,
                invocation_declared=invocation_declared,
                expected_exit_status=expected_status,
                expected_stdout_contains=stdout_contains,
                expected_stderr_contains=stderr_contains,
                effects=effects,
                effects_declared=effects_declared,
                test_ids=test_ids,
                retirement_reason=retirement_reason,
            )
        )
    return ReviewCatalog(
        cli_id=cli_id,
        max_candidates=max_candidates,
        interaction_groups=interactions,
        cases=tuple(cases),
    )


def _markdown_cell(value: Any) -> str:
    text = value if isinstance(value, str) else json.dumps(
        value, sort_keys=True, ensure_ascii=False
    )
    return text.replace("|", "\\|").replace("\n", " ").replace("\r", " ")


def _case_status(
    candidate: Mapping[str, Any], cases: Mapping[str, ReviewCase]
) -> tuple[str, ReviewCase | None]:
    case = cases.get(str(candidate["id"]))
    if case is None:
        return "UNREVIEWED", None
    if case.state == "retired":
        return "REVIEW REQUIRED: retired ID is active again", case
    if case.state == "pending":
        return "PENDING", case
    if case.reviewed_signature != candidate["signature"]:
        return "REVIEW REQUIRED", case
    return str(case.decision).upper(), case


def render_cli_surface_markdown(
    surface: Mapping[str, Any], catalog: ReviewCatalog
) -> str:
    """Render the generated part of a consumer-owned CLI specification."""

    entrypoint = surface["entrypoint"]
    lines = [
        "## Generated CLI surface and semantic review",
        "",
        (
            f"Executable: `{entrypoint['command']}` via "
            f"`{entrypoint['prog']}`; built-ins: "
            + (
                ", ".join(
                    f"`{item}`" for item in entrypoint["builtins"]
                )
                or "none"
            )
            + ". Empty argv: "
            + (
                "is parsed by the single-command parser; "
                "required syntax may still reject it."
                if entrypoint["single_command"]
                and entrypoint["no_args_action"]
                else "shows help."
            )
        ),
        "",
        f"Surface schema: `{surface['schema_version']}`; review catalog schema: `{REVIEW_SCHEMA_VERSION}`.",
        "",
        "The manifest records registered syntax. The review catalog owns expected behavior, effects, rationale, and test references.",
        "",
        "### Command routes",
        "",
        "| Surface ID | Route kind | Invocation path | Invocation mode | Nested commands | Help summary | Description | Group | Behavior | Confirmation | Synopsis/usage overrides | Delegated metadata | Parser settings | Parser callback | Opaque fields | Parser completeness |",
        "| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |",
    ]
    for route in surface["routes"]:
        path = " ".join(route["path"]) or entrypoint["command"]
        if route.get("aliases"):
            path += " (aliases: " + ", ".join(route["aliases"]) + ")"
        nested_commands = []
        for group in route.get("subcommand_groups", []):
            label = ", ".join(group.get("subcommands", [])) or "none"
            if group.get("required"):
                label += " (required)"
            nested_commands.append(f"{group.get('destination')}: {label}")
        if not nested_commands and route.get("subcommands"):
            nested_commands.append(
                "delegated: " + ", ".join(route["subcommands"])
            )
        parser_settings = "; ".join(
            f"{' '.join(setting.get('parser_path', ())) or '<entrypoint>'}: "
            f"allow_abbrev={'yes' if setting.get('allow_abbrev') else 'no'}, "
            f"prefix_chars={setting.get('prefix_chars')!r}, "
            f"fromfile_prefix_chars={setting.get('fromfile_prefix_chars')!r}, "
            f"negative_number_matcher={setting.get('negative_number_matcher')!r}, "
            "has_negative_number_optionals="
            f"{'yes' if setting.get('has_negative_number_optionals') else 'no'}, "
            "negative_number_matcher_custom="
            f"{'yes' if setting.get('negative_number_matcher_custom') else 'no'}"
            for setting in route.get("parser_settings", ())
        )
        route_path = tuple(route.get("path", ()))
        if not route_path:
            invocation_mode = "entrypoint; see empty-argv behavior above"
            if route.get("single_command", False) and route.get(
                "no_args_action", False
            ):
                invocation_mode += "; single-command; empty remainder is parsed"
            elif route.get("single_command", False):
                invocation_mode += (
                    "; single-command; empty remainder shows help before parsing"
                )
        elif route["kind"] == "delegate-group":
            invocation_mode = "delegated group; child CLI parses remaining tokens"
        elif route["kind"] == "route-prefix":
            invocation_mode = "route prefix; selects a nested command"
        elif route["single_command"] and route["no_args_action"]:
            invocation_mode = "single-command; empty remainder is parsed"
        elif route["single_command"]:
            invocation_mode = (
                "single-command; empty remainder shows help before parsing"
            )
        else:
            invocation_mode = "command route; parser handles remaining tokens"
        overrides = {
            key: value
            for key, value in (
                ("verb_synopsis", route.get("synopsis_override")),
                ("parser_usage", route.get("usage_override")),
            )
            if value is not None
        }
        lines.append(
            "| " + " | ".join(
                _markdown_cell(value)
                for value in (
                    route["id"],
                    route["kind"],
                    path,
                    invocation_mode,
                    "; ".join(nested_commands),
                    route.get("summary") or "",
                    route.get("description") or "",
                    route.get("group") or "",
                    ", ".join(route.get("behavior", [])),
                    route["confirmation"],
                    overrides,
                    route.get("delegated_metadata", []),
                    parser_settings,
                    route["parser_configured_by_callback"],
                    route.get("opaque_fields", []),
                    "complete" if route.get("syntax_complete") else "incomplete",
                )
            ) + " |"
        )
    lines.extend(
        (
            "",
            "### Arguments and options",
            "",
        "| Surface ID | Kind | Spelling/name | Description | Grammar shape | Action/type/const | Required | Choices/default/exclusive rule | Scope/placement | Visibility/help group |",
        "| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |",
        )
    )
    for route in surface["routes"]:
        for action in route.get("actions", []):
            label = "/".join(action.get("flags", ())) if action["kind"] == "option" else action["name"]
            shape = {"metavar": action.get("metavar")}
            if action.get("nargs") is not None:
                shape["nargs"] = action["nargs"]
            if action.get("minimum_values") is not None:
                shape["minimum_values"] = action["minimum_values"]
            if action["kind"] == "argument":
                action_details = {
                    "action": action.get("action"),
                    "type": action.get("type"),
                    "const": action.get("const"),
                }
            elif action["kind"] == "option":
                action_details = {
                    "action": action.get("action"),
                    "type": action.get("type"),
                    "const": action.get("const"),
                }
            else:
                raise SurfaceSpecError(
                    f"unsupported surface action kind {action['kind']!r}"
                )
            exclusive_required = (
                action["exclusive_required"]
                if action["kind"] == "option"
                else False
            )
            details = {
                "choices": action.get("choices"),
                "declared_default": action.get("default"),
                "default": action.get("effective_default", action.get("default")),
                "exclusive_group": action.get("exclusive_group"),
                "exclusive_required": exclusive_required,
            }
            scope: dict[str, Any] = {"scope": action.get("scope", "positional")}
            if action.get("placement"):
                scope["placement"] = action["placement"]
            parser_path = action.get("parser_path")
            if parser_path is not None:
                scope["parser_path"] = parser_path
            if action.get("before_nested_subcommand"):
                scope["before_nested_subcommand"] = True
            visibility = {
                "hidden": action["hidden"],
                "help_group": action.get("help_group", ""),
            }
            lines.append(
                "| " + " | ".join(
                    _markdown_cell(value)
                    for value in (
                        action["id"], action["kind"], label,
                        action.get("description", ""), shape, action_details,
                        action.get("required"), details, scope, visibility,
                    )
                ) + " |"
            )
    cases = catalog.cases_by_id
    lines.extend(
        (
            "",
            "### Semantic case review",
            "",
            "The invocation is an argv token list passed to the registered CLI, excluding the executable name. Test IDs prove collection/linkage only; the normal gate proves execution and assertions.",
            "",
            "| Case ID | Dimension | Surface IDs / shape | Decision | Invocation | Expected status/output | Effects | Test IDs | Rationale/state |",
            "| --- | --- | --- | --- | --- | --- | --- | --- | --- |",
        )
    )
    candidate_ids: set[str] = set()
    for candidate in surface["candidates"]:
        candidate_id = str(candidate["id"])
        candidate_ids.add(candidate_id)
        status, case = _case_status(candidate, cases)
        invocation = (
            shlex.join(case.invocation)
            if case and case.invocation
            else "(no argv tokens)"
            if case and case.invocation_declared
            else ""
        )
        expected_status = (
            case.expected_exit_status
            if case and case.expected_exit_status is not None
            else ""
        )
        effects = (
            "; ".join(case.effects)
            if case and case.effects
            else "none"
            if case and case.effects_declared
            else ""
        )
        expected_output = (
            {
                "stdout contains": case.expected_stdout_contains,
                "stderr contains": case.expected_stderr_contains,
            }
            if case
            else ""
        )
        test_ids = ", ".join(case.test_ids) if case else ""
        rationale = (
            case.retirement_reason
            if case and case.state == "retired"
            else case.rationale
            if case
            else "Add a product-owned decision and test reference."
        )
        lines.append(
            "| " + " | ".join(
                _markdown_cell(value)
                for value in (
                    candidate_id,
                    candidate["kind"],
                    {
                        "members": candidate.get("members", []),
                        "shape": candidate.get("shape", {}),
                    },
                    status,
                    invocation,
                    {"status": expected_status, **expected_output}
                    if case
                    else expected_status,
                    effects,
                    test_ids,
                    rationale,
                )
            ) + " |"
        )
    stale = [case for case in catalog.cases if case.case_id not in candidate_ids]
    if stale:
        lines.extend(
            (
                "",
                "### Removed or stale semantic cases",
                "",
                "| Case ID | State | Prior decision | Retirement reason / disposition needed | Test IDs |",
                "| --- | --- | --- | --- | --- |",
            )
        )
        for case in stale:
            stale_state = "RETIRED" if case.state == "retired" else "STALE: disposition required"
            lines.append(
                "| " + " | ".join(
                    _markdown_cell(value)
                    for value in (
                        case.case_id,
                        stale_state,
                        case.decision or "",
                        case.retirement_reason if case.state == "retired" else case.rationale,
                        ", ".join(case.test_ids),
                    )
                ) + " |"
            )
    interaction_issues = surface.get("interaction_issues", ())
    if interaction_issues:
        lines.extend(
            (
                "",
                "### Interaction references to repair",
                "",
                "These catalog references no longer resolve against the current CLI. The semantic rows remain visible above as stale until they are updated or explicitly retired.",
                "",
            )
        )
        lines.extend(
            f"- `{_markdown_cell(issue)}`" for issue in interaction_issues
        )
    if not surface.get("syntax_complete", False):
        lines.extend(("", "**Surface inventory is incomplete:**", ""))
        lines.extend(f"- `{_markdown_cell(reason)}`" for reason in surface.get("incomplete", []))
    return "\n".join(lines).rstrip() + "\n"


def _replace_generated_region(text: str, rendered: str) -> str:
    lines = text.splitlines(keepends=True)
    start_lines = [i for i, line in enumerate(lines) if line.rstrip("\r\n") == SURFACE_START_MARKER]
    end_lines = [i for i, line in enumerate(lines) if line.rstrip("\r\n") == SURFACE_END_MARKER]
    if len(start_lines) != 1 or len(end_lines) != 1:
        raise SurfaceSpecError(
            "consumer spec must contain exactly one cli-extended surface start and end marker line"
        )
    start, end = start_lines[0], end_lines[0]
    if start >= end:
        raise SurfaceSpecError("consumer spec surface markers are reversed or nested")
    newline = "\r\n" if lines[start].endswith("\r\n") else "\n"
    body = rendered.rstrip("\r\n").replace("\r\n", "\n").replace("\n", newline)
    prefix = "".join(lines[: start + 1])
    suffix = "".join(lines[end:])
    if body:
        body += newline
    return prefix + body + suffix


def _read_text_preserving_newlines(path: str | Path) -> str:
    with Path(path).open("r", encoding="utf-8", newline="") as stream:
        return stream.read()


def _write_text_preserving_newlines(path: str | Path, text: str) -> None:
    _atomic_write_text(path, text, newline="")


def _atomic_write_text(path: str | Path, text: str, *, newline: str) -> None:
    """Replace a generated text file without exposing a partially-written file."""

    destination = Path(path).resolve()
    try:
        mode = stat.S_IMODE(destination.stat().st_mode)
    except FileNotFoundError:
        mode = 0o644

    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{destination.name}.", dir=destination.parent
    )
    temporary_path = Path(temporary_name)
    try:
        with os.fdopen(
            descriptor, "w", encoding="utf-8", newline=newline
        ) as stream:
            stream.write(text)
            stream.flush()
            os.fsync(stream.fileno())
        os.chmod(temporary_path, mode)
        os.replace(temporary_path, destination)
    finally:
        temporary_path.unlink(missing_ok=True)


def _validate_distinct_surface_paths(
    review_path: str | Path,
    manifest_path: str | Path,
    spec_path: str | Path,
) -> None:
    """Keep generated destinations from aliasing each other or the catalog."""

    entries = (
        ("review catalog", Path(review_path)),
        ("manifest", Path(manifest_path)),
        ("specification", Path(spec_path)),
    )
    resolved: list[tuple[str, Path, str]] = []
    for label, path in entries:
        try:
            if path.is_symlink() or path.exists():
                canonical_path = path.resolve(strict=True)
            else:
                canonical_path = path.parent.resolve(strict=True) / path.name
            resolved_path = os.path.normcase(str(canonical_path))
        except (OSError, RuntimeError) as exc:
            raise SurfaceSpecError(
                f"cannot resolve {label} path {str(path)!r}: {exc}"
            ) from exc
        for previous_label, previous_path, previous_resolved in resolved:
            aliases = resolved_path == previous_resolved
            if not aliases:
                try:
                    aliases = path.samefile(previous_path)
                except OSError:
                    aliases = False
            if aliases:
                raise SurfaceSpecError(
                    f"{label} and {previous_label} must resolve to distinct files"
                )
        resolved.append((label, path, resolved_path))


def _prepare(
    app: Any,
    *,
    review_path: str | Path,
    manifest_path: str | Path,
    spec_path: str | Path,
    max_candidates: int | None,
) -> tuple[ReviewCatalog, dict[str, Any], str, str, list[str]]:
    if not isinstance(app, RegisteredCli):
        raise TypeError("app must be a RegisteredCli built by CliRegistry")
    _validate_distinct_surface_paths(review_path, manifest_path, spec_path)
    catalog = load_cli_review_catalog(review_path)
    if catalog.cli_id != app.identity.command_name:
        raise ReviewCatalogError(
            f"review catalog cli_id {catalog.cli_id!r} does not match "
            f"registered executable {app.identity.command_name!r}"
        )
    limit = catalog.max_candidates if max_candidates is None else max_candidates
    surface = export_cli_surface(
        app,
        interaction_groups=catalog.interaction_groups,
        max_candidates=limit,
        _tolerate_invalid_interactions=True,
    )
    manifest_text = render_cli_surface_json(surface)
    markdown = render_cli_surface_markdown(surface, catalog)
    spec_text = _read_text_preserving_newlines(spec_path)
    new_spec_text = _replace_generated_region(spec_text, markdown)
    findings = _review_findings(surface, catalog)
    return catalog, surface, manifest_text, new_spec_text, findings


def _review_findings(
    surface: Mapping[str, Any], catalog: ReviewCatalog
) -> list[str]:
    def is_negative_number(
        token: str, route: Mapping[str, Any], depth: int
    ) -> bool:
        parser_path = tuple(route.get("path", ()))[:depth]
        settings = next(
            (
                item
                for item in route.get("parser_settings", ())
                if tuple(item.get("parser_path", ())) == parser_path
            ),
            None,
        )
        matcher = settings.get("negative_number_matcher") if settings else None
        if settings and settings.get("negative_number_matcher_custom"):
            return False
        if not isinstance(matcher, Mapping):
            return False
        pattern = matcher.get("pattern")
        flags = matcher.get("flags")
        if not isinstance(pattern, str) or not isinstance(flags, int):
            return False
        try:
            matches = re.match(pattern, token, flags) is not None
        except re.error:
            return False
        return matches and not settings.get("has_negative_number_optionals", False)

    def action_for_option(
        route: Mapping[str, Any], option: str, depth: int
    ) -> Mapping[str, Any] | None:
        path = tuple(route.get("path", ()))

        def option_is_available(action: Mapping[str, Any]) -> bool:
            parser_path = tuple(action.get("parser_path", ()))
            if not path:
                return not parser_path
            if depth == 0:
                return bool(action.get("placement", {}).get("before_verb", False))
            return parser_path == path[:depth]

        available: list[tuple[int, Mapping[str, Any]]] = []
        for action in route.get("actions", ()):
            if action.get("kind") != "option":
                continue
            parser_path = tuple(action.get("parser_path", ()))
            if not option_is_available(action):
                continue
            flags = tuple(str(flag) for flag in action.get("flags", ()))
            if option in flags:
                available.append((len(parser_path), action))
        if available:
            return max(available, key=lambda pair: pair[0])[1]
        parser_path = path[:depth]
        parser_settings = next(
            (
                settings
                for settings in route.get("parser_settings", ())
                if tuple(settings.get("parser_path", ())) == parser_path
            ),
            None,
        )
        allow_abbrev = (
            parser_settings.get("allow_abbrev")
            if parser_settings is not None
            else surface.get("entrypoint", {}).get("allow_abbrev", False)
        )
        if not allow_abbrev:
            return None
        matches = [
            (str(flag), action)
            for action in route.get("actions", ())
            if action.get("kind") == "option"
            and option_is_available(action)
            for flag in action.get("flags", ())
            if str(flag).startswith("--") and str(flag).startswith(option)
        ]
        matched_flags = {flag for flag, _action in matches}
        if len(matched_flags) != 1:
            return None
        return max(
            (
                (len(tuple(action.get("parser_path", ()))), action)
                for _flag, action in matches
            ),
            key=lambda pair: pair[0],
        )[1]

    def option_end(
        argv: Sequence[str],
        index: int,
        action: Mapping[str, Any],
        inline: bool,
        *,
        route: Mapping[str, Any],
        depth: int,
    ) -> int:
        def is_option_boundary(position: int) -> bool:
            if position >= len(argv) or argv[position] == "--":
                return True
            token = argv[position]
            option = token.partition("=")[0]
            if not token.startswith("-") or token == "-":
                return False
            if action_for_option(route, option, depth) is not None:
                return True
            return not is_negative_number(token, route, depth)

        nargs = action.get("nargs")
        if nargs == 0:
            return index + 1
        if nargs in {"...", "A..."}:
            return len(argv)
        if nargs is None:
            if inline:
                return index + 1
            return index + 2 if not is_option_boundary(index + 1) else index + 1
        if isinstance(nargs, int):
            additional = max(nargs - 1, 0) if inline else nargs
            end = index + 1
            while end < index + 1 + additional:
                if is_option_boundary(end):
                    break
                end += 1
            return end
        if nargs == "?":
            if inline:
                return index + 1
            if not is_option_boundary(index + 1):
                return index + 2
            return index + 1
        if nargs in {"*", "+"}:
            if inline:
                return index + 1
            end = index + 1
            while not is_option_boundary(end):
                end += 1
            return end
        return index + 1

    def invocation_parts(
        argv: Sequence[str],
        route: Mapping[str, Any],
        command_positions: Sequence[int],
        external_options: Sequence[Mapping[str, Any]] = (),
    ) -> tuple[
        list[tuple[int, str]],
        dict[str, list[tuple[str, tuple[str, ...], bool]]],
        list[tuple[int, str, int, tuple[str, ...], bool]],
    ]:
        """Return positional and option tokens, consuming expected foreign values."""

        command_positions_set = set(command_positions)
        external_by_flag: dict[str, Mapping[str, Any]] = {}
        for external_option in external_options:
            owner_route = routes_by_id.get(str(external_option.get("route_id")))
            external_action = next(
                (
                    action
                    for action in (owner_route or {}).get("actions", ())
                    if action.get("id") == external_option.get("id")
                    and action.get("kind") == "option"
                ),
                None,
            )
            if external_action is not None:
                for flag in external_option.get("flags", ()):
                    external_by_flag[str(flag)] = external_action
        path_depth = 0
        options_enabled = {0: True}
        positionals: list[tuple[int, str]] = []
        options: dict[str, list[tuple[str, tuple[str, ...], bool]]] = {}
        unknown_options: list[tuple[int, str, int, tuple[str, ...], bool]] = []
        index = 0
        while index < len(argv):
            if index in command_positions_set:
                path_depth += 1
                options_enabled[path_depth] = True
                index += 1
                continue
            token = argv[index]
            if token == "--" and options_enabled[path_depth]:
                options_enabled[path_depth] = False
                index += 1
                continue
            option, separator, inline_value = token.partition("=")
            action = (
                action_for_option(route, option, path_depth)
                if options_enabled[path_depth]
                else None
            )
            if action is not None:
                end = option_end(
                    argv,
                    index,
                    action,
                    bool(separator),
                    route=route,
                    depth=path_depth,
                )
                values: list[str] = []
                if action.get("nargs") != 0:
                    if separator:
                        values.append(inline_value)
                    values.extend(argv[index + 1 : end])
                options.setdefault(str(action.get("id", "")), []).append(
                    (option, tuple(values), bool(separator))
                )
                index = end
                continue
            if (
                token.startswith("-")
                and token != "-"
                and options_enabled[path_depth]
                and not is_negative_number(token, route, path_depth)
            ):
                external_action = external_by_flag.get(option)
                if external_action is None:
                    unknown_options.append((index, option, path_depth, (), bool(separator)))
                    index += 1
                    continue
                end = option_end(
                    argv,
                    index,
                    external_action,
                    bool(separator),
                    route=route,
                    depth=path_depth,
                )
                values: list[str] = []
                if external_action.get("nargs") != 0:
                    if separator:
                        values.append(inline_value)
                    values.extend(argv[index + 1 : end])
                unknown_options.append(
                    (index, option, path_depth, tuple(values), bool(separator))
                )
                index = end
                continue
            positionals.append((path_depth, token))
            index += 1
        return positionals, options, unknown_options

    def positional_values_by_action(
        route: Mapping[str, Any], positionals: Sequence[tuple[int, str]]
    ) -> tuple[dict[str, tuple[str, ...]], list[tuple[int, str]]]:
        """Assign positional tokens and retain any tokens with no declared action."""

        path = tuple(route.get("path", ()))
        positions_by_depth: dict[int, list[str]] = {}
        for depth, token in positionals:
            positions_by_depth.setdefault(depth, []).append(token)

        values_by_action: dict[str, tuple[str, ...]] = {}
        unassigned: list[tuple[int, str]] = []
        for depth in range(len(path) + 1):
            parser_path = path[:depth]
            actions = [
                action
                for action in route.get("actions", ())
                if action.get("kind") == "argument"
                and tuple(action.get("parser_path", ())) == parser_path
                and (depth == len(path) or action.get("before_nested_subcommand"))
            ]
            tokens = positions_by_depth.get(depth, [])
            cursor = 0
            for action_index, action in enumerate(actions):
                nargs = action.get("nargs")
                minimum = _minimum_action_values(action)
                if nargs is None:
                    maximum: int | None = 1
                elif isinstance(nargs, int):
                    maximum = max(0, nargs)
                elif nargs == "?":
                    maximum = 1
                elif nargs in {"*", "+", "...", "A..."}:
                    maximum = None
                else:
                    maximum = minimum

                following_minimum = sum(
                    _minimum_action_values(later)
                    for later in actions[action_index + 1 :]
                )
                available = max(0, len(tokens) - cursor)
                if maximum is None:
                    consumed = max(minimum, available - following_minimum)
                else:
                    consumed = min(
                        maximum,
                        max(minimum, available - following_minimum),
                    )
                end = min(cursor + consumed, len(tokens))
                values_by_action[str(action.get("id", ""))] = tuple(
                    tokens[cursor:end]
                )
                cursor += consumed
            unassigned.extend((depth, token) for token in tokens[cursor:])
        return values_by_action, unassigned

    def route_positions(
        argv: Sequence[str], route: Mapping[str, Any], all_routes: Sequence[Mapping[str, Any]]
    ) -> tuple[int, ...] | None:
        path = tuple(route.get("path", ()))
        if not path:
            return ()
        accepted_by_position: list[set[str]] = []
        for index, canonical in enumerate(path):
            prefix = path[: index + 1]
            prefix_route = next(
                (candidate for candidate in all_routes if tuple(candidate.get("path", ())) == prefix),
                None,
            )
            accepted = {str(canonical)}
            if prefix_route is not None:
                accepted.update(str(alias) for alias in prefix_route.get("aliases", ()))
            accepted_by_position.append(accepted)

        positional_states = {
            depth: [
                {
                    "action": action,
                    "remaining": _minimum_action_values(action),
                }
                for action in route.get("actions", ())
                if action.get("kind") == "argument"
                and action.get("before_nested_subcommand")
                and tuple(action.get("parser_path", ())) == path[:depth]
            ]
            for depth in range(len(path))
        }
        positional_indexes = {depth: 0 for depth in positional_states}

        def consumes_parent_positional(depth: int, is_command: bool) -> bool:
            actions = positional_states.get(depth, ())
            index = positional_indexes[depth]
            while index < len(actions):
                state = actions[index]
                action = state["action"]
                nargs = action.get("nargs")
                if state["remaining"]:
                    state["remaining"] -= 1
                    positional_indexes[depth] = index
                    return True
                if nargs == "?":
                    if is_command:
                        return False
                    positional_indexes[depth] = index + 1
                    return True
                if nargs in {"*", "+"}:
                    return not is_command
                if nargs in {"...", "A..."}:
                    return not is_command
                index += 1
                positional_indexes[depth] = index
            return False

        positions: list[int] = []
        segment = 0
        options_enabled = {0: True}
        index = 0
        while index < len(argv) and segment < len(accepted_by_position):
            token = argv[index]
            if token == "--":
                if options_enabled[segment]:
                    options_enabled[segment] = False
                    index += 1
                    continue
                return None
            if token in accepted_by_position[segment]:
                if consumes_parent_positional(segment, True):
                    index += 1
                    continue
                positions.append(index)
                segment += 1
                options_enabled[segment] = True
                index += 1
                continue
            option, separator, _value = token.partition("=")
            action = (
                action_for_option(route, option, segment)
                if options_enabled[segment]
                else None
            )
            if action is not None:
                index = option_end(
                    argv,
                    index,
                    action,
                    bool(separator),
                    route=route,
                    depth=segment,
                )
                continue
            if (
                token.startswith("-")
                and token != "-"
                and options_enabled[segment]
                and not is_negative_number(token, route, segment)
            ):
                return None
            if consumes_parent_positional(segment, False):
                index += 1
                continue
            return None
        if segment == len(accepted_by_position):
            return tuple(positions)
        return None

    findings: list[str] = [
        "stale interaction catalog reference: " + str(issue)
        for issue in surface.get("interaction_issues", ())
    ]
    candidates = {str(case["id"]): case for case in surface["candidates"]}
    cases = catalog.cases_by_id
    routes_by_id = {str(route["id"]): route for route in surface["routes"]}
    for case_id, candidate in candidates.items():
        case = cases.get(case_id)
        if case is None:
            findings.append(f"missing semantic review case: {case_id}")
            continue
        if case.state == "pending":
            findings.append(f"semantic review is pending: {case_id}")
            continue
        if case.state == "retired":
            findings.append(f"retired semantic case is active again: {case_id}")
            continue
        if case.reviewed_signature != candidate["signature"]:
            findings.append(f"semantic review signature changed: {case_id}")
        if not case.test_ids:
            findings.append(f"semantic review has no test IDs: {case_id}")
        if not case.invocation_declared:
            findings.append(f"semantic review has no invocation field: {case_id}")
        if not case.effects_declared:
            findings.append(f"semantic review has no effects field: {case_id}")
        if (
            candidate.get("shape", {}).get("required_arguments")
            and not case.invocation
        ):
            findings.append(f"semantic review has no concrete invocation: {case_id}")
        route = routes_by_id.get(str(candidate["route_id"]))
        if route is None:
            findings.append(f"semantic review candidate has an unknown route: {case_id}")
            continue
        command_positions = route_positions(case.invocation, route, surface["routes"])
        if command_positions is None:
            findings.append(f"invocation for {case_id} omits its command path")
        candidate_kind = str(candidate.get("kind", ""))
        candidate_shape = candidate.get("shape", {})
        external_options = (
            candidate_shape.get("external_options", ())
            if candidate_kind == "interaction"
            else ()
        )
        external_flags = {
            str(flag)
            for external_option in external_options
            for flag in external_option.get("flags", ())
        }
        non_command_positions, option_occurrences, unknown_options = invocation_parts(
            case.invocation,
            route,
            command_positions or (),
            external_options,
        )
        for _position, spelling, depth, _values, _inline in unknown_options:
            if spelling not in external_flags:
                findings.append(
                    f"invocation for {case_id} contains undeclared unknown option "
                    f"{spelling!r} at parser depth {depth}"
                )
        positional_occurrences, unassigned_positionals = positional_values_by_action(
            route, non_command_positions
        )
        for depth, token in unassigned_positionals:
            findings.append(
                f"invocation for {case_id} contains unassigned positional token "
                f"{token!r} at parser depth {depth}"
            )

        def active_option_occurrences(
            action: Mapping[str, Any],
        ) -> list[tuple[str, tuple[str, ...], bool]]:
            return [
                occurrence
                for occurrence in option_occurrences.get(str(action.get("id", "")), ())
                if not (action.get("nargs") == 0 and occurrence[2])
            ]

        def has_invalid_flag_value(action: Mapping[str, Any]) -> bool:
            return action.get("nargs") == 0 and any(
                inline
                for _spelling, _values, inline in option_occurrences.get(
                    str(action.get("id", "")), ()
                )
            )

        def add_option_value_findings(
            action: Mapping[str, Any],
            occurrences: Sequence[tuple[str, tuple[str, ...], bool]],
            *,
            case_id: str,
            option_id: str,
            context: str | None = None,
        ) -> None:
            """Explain malformed values on every occurrence, including repeats."""

            context = context or f"option {option_id}"
            minimum = _minimum_action_values(action)
            display = str((action.get("flags") or (option_id,))[0])
            if any(len(values) < minimum for _spelling, values, _inline in occurrences):
                if context.startswith(("required group ", "required option ")):
                    findings.append(
                        f"invocation for {case_id} omits a value for {context}"
                    )
                else:
                    findings.append(
                        f"invocation for {case_id} omits a value for {display}"
                    )
            if any(
                not _value_count_accepts(action, values)
                for _spelling, values, _inline in occurrences
            ):
                shape_message = (
                    f"invocation for {case_id} has an invalid value shape in {context}"
                    if context.startswith("required group ")
                    else f"invocation for {case_id} has an invalid value shape for {context}"
                )
                findings.append(shape_message)
            if any(
                not _choices_accept(action, values)
                for _spelling, values, _inline in occurrences
            ):
                findings.append(
                    f"invocation for {case_id} supplies a value outside the choices for {context}"
                )

        route_actions = list(route.get("actions", ()))
        required_baseline = _route_required_baseline(route)
        required_group_by_option = {
            str(action.get("id", "")): str(group_id)
            for group_id, group in required_baseline.get(
                "exclusive_groups", {}
            ).items()
            for action in group.get("options", ())
        }
        for action in route_actions:
            if action.get("kind") == "argument":
                argument_id = str(action.get("id", ""))
                values = positional_occurrences.get(argument_id, ())
                if not values:
                    continue
                minimum = _minimum_action_values(action)
                if not _value_count_accepts(action, values):
                    if len(values) < minimum and action.get("required"):
                        findings.append(
                            f"invocation for {case_id} omits required positional argument {argument_id}"
                        )
                    if len(values) < minimum and not action.get("required"):
                        findings.append(
                            f"invocation for {case_id} omits a value for positional argument {argument_id}"
                        )
                    findings.append(
                        f"invocation for {case_id} has an invalid positional value shape"
                    )
                if not _choices_accept(action, values):
                    if action.get("required"):
                        findings.append(
                            f"invocation for {case_id} supplies a value outside the choices for required argument {argument_id}"
                        )
                    else:
                        findings.append(
                            f"invocation for {case_id} supplies a positional value outside its choices"
                        )
            elif action.get("kind") == "option":
                option_id = str(action.get("id", ""))
                occurrences = option_occurrences.get(option_id, ())
                if not occurrences:
                    continue
                required_group_id = required_group_by_option.get(option_id)
                if has_invalid_flag_value(action):
                    if required_group_id is not None:
                        findings.append(
                            f"invocation for {case_id} supplies an inline value to a flag-only option in required exclusive group {required_group_id}"
                        )
                    else:
                        findings.append(
                            f"invocation for {case_id} supplies an inline value to flag-only option {option_id}"
                        )
                elif not _option_occurrences_accept(action, occurrences):
                    if required_group_id is not None:
                        context = f"required group {required_group_id}"
                    elif action.get("required"):
                        context = f"required option {option_id}"
                    else:
                        context = f"option {option_id}"
                    add_option_value_findings(
                        action,
                        occurrences,
                        case_id=case_id,
                        option_id=option_id,
                        context=context,
                    )
            else:
                findings.append(
                    f"invocation for {case_id} contains unsupported surface action kind {action.get('kind')!r}"
                )
        for argument in required_baseline.get("arguments", ()):
            argument_id = str(argument.get("id", ""))
            values = positional_occurrences.get(argument_id, ())
            if not values:
                findings.append(
                    f"invocation for {case_id} omits required positional argument {argument_id}"
                )
        for action in required_baseline.get("options", ()):
            option_id = str(action.get("id", ""))
            occurrences = option_occurrences.get(option_id, ())
            if not occurrences:
                findings.append(
                    f"invocation for {case_id} omits required option {option_id}"
                )

        exempt_groups: set[str] = set()
        if candidate_kind == "exclusive-conflict":
            group_id = candidate_shape.get("group_id")
            if isinstance(group_id, str):
                exempt_groups.add(group_id)
        elif candidate_kind == "interaction":
            selected_ids = {
                str(option_id)
                for option_id in candidate_shape.get("option_ids", ())
            }
            for group_id, group in required_baseline.get(
                "exclusive_groups", {}
            ).items():
                members = {
                    str(action.get("id", ""))
                    for action in group.get("options", ())
                }
                if len(selected_ids & members) > 1:
                    exempt_groups.add(str(group_id))

        for group_id, group in required_baseline.get(
            "exclusive_groups", {}
        ).items():
            if str(group_id) in exempt_groups:
                continue
            selected_group_actions = [
                action
                for action in group.get("options", ())
                if option_occurrences.get(str(action.get("id", "")), ())
            ]
            if len(selected_group_actions) != 1:
                findings.append(
                    f"invocation for {case_id} must supply exactly one option from required group {group_id}"
                )

        if candidate_kind == "minimum":
            for argument_id in candidate_shape.get("required_arguments", ()):
                if not any(
                    action.get("kind") == "argument"
                    and action.get("id") == argument_id
                    for action in route_actions
                ):
                    findings.append(
                        f"semantic review candidate {case_id} references unknown required argument {argument_id}"
                    )
            for option_id in candidate_shape.get("required_options", ()):
                if not any(
                    action.get("kind") == "option"
                    and action.get("id") == option_id
                    for action in route_actions
                ):
                    findings.append(
                        f"semantic review candidate {case_id} references unknown required option {option_id}"
                    )

        if candidate_kind == "interaction":
            shape = candidate_shape
            for argument_id in shape.get("required_arguments", ()):
                if not any(
                    action.get("kind") == "argument"
                    and action.get("id") == argument_id
                    for action in route.get("actions", ())
                ):
                    findings.append(
                        f"interaction candidate {case_id} references unknown required argument {argument_id}"
                    )
            for option_id in shape.get("required_options", ()):
                if not any(
                    action.get("kind") == "option"
                    and action.get("id") == option_id
                    for action in route.get("actions", ())
                ):
                    findings.append(
                        f"interaction candidate {case_id} references unknown required option {option_id}"
                    )
            external_ids = {
                str(option.get("id"))
                for option in shape.get("external_options", ())
            }
            for option_id in shape.get("option_ids", ()):
                option_id = str(option_id)
                if option_id in external_ids:
                    continue
                action = next(
                    (
                        item
                        for item in route.get("actions", ())
                        if item.get("id") == option_id
                        and item.get("kind") == "option"
                    ),
                    None,
                )
                if action is None:
                    findings.append(
                        f"interaction candidate {case_id} references unknown "
                        f"option {option_id}"
                    )
                    continue
                occurrences = option_occurrences.get(option_id, ())
                if not occurrences:
                    findings.append(
                        f"interaction invocation for {case_id} does not supply "
                        f"participating option {option_id}"
                    )
                    continue
                if has_invalid_flag_value(action):
                    findings.append(
                        f"interaction invocation for {case_id} supplies an inline "
                        f"value to flag-only option {option_id}"
                    )
                    continue
                if action.get("nargs") == 0:
                    continue
                if not _option_occurrences_accept(action, occurrences):
                    findings.append(
                        f"interaction invocation for {case_id} does not provide a "
                        f"valid value shape for participating option {option_id}"
                    )
            for external_option in shape.get("external_options", ()):
                flags = set(external_option.get("flags", ()))
                matching_occurrences = [
                    occurrence
                    for occurrence in unknown_options
                    if occurrence[1] in flags
                ]
                target_depth = len(tuple(route.get("path", ())))
                scoped_occurrences = [
                    occurrence
                    for occurrence in matching_occurrences
                    if occurrence[2] == target_depth
                ]
                if not scoped_occurrences:
                    findings.append(
                        f"interaction invocation for {case_id} does not supply the "
                        f"out-of-route option {external_option.get('id')} at the "
                        "target route's parser depth"
                    )
                    continue
                owner_route = routes_by_id.get(str(external_option.get("route_id")))
                external_action = next(
                    (
                        action
                        for action in (owner_route or {}).get("actions", ())
                        if action.get("id") == external_option.get("id")
                        and action.get("kind") == "option"
                    ),
                    None,
                )
                if external_action is None:
                    findings.append(
                        f"interaction candidate {case_id} references unknown "
                        f"out-of-route option {external_option.get('id')}"
                    )
                    continue
                if external_action.get("nargs") == 0:
                    if any(
                        inline
                        for _position, _spelling, _depth, _values, inline
                        in scoped_occurrences
                    ):
                        findings.append(
                            f"interaction invocation for {case_id} supplies an "
                            f"inline value to flag-only out-of-route option "
                            f"{external_option.get('id')}"
                        )
                    continue
                external_value_occurrences = [
                    (spelling, values, inline)
                    for _position, spelling, _depth, values, inline
                    in scoped_occurrences
                ]
                if not _option_occurrences_accept(
                    external_action, external_value_occurrences
                ):
                    findings.append(
                        f"interaction invocation for {case_id} does not provide a "
                        f"valid value shape for out-of-route option "
                        f"{external_option.get('id')}"
                    )
        if candidate_kind == "route-alias":
            alias = str(candidate.get("shape", {}).get("alias", ""))
            if (
                command_positions is None
                or not command_positions
                or case.invocation[command_positions[-1]] != alias
            ):
                findings.append(f"invocation for {case_id} omits its command alias")
        if candidate_kind == "argument-choice":
            choice = candidate.get("shape", {}).get("choice")
            argument_id = candidate.get("shape", {}).get("argument_id")
            if argument_id is None and candidate.get("members"):
                argument_id = candidate["members"][0]
            supplied_values = positional_occurrences.get(str(argument_id), ())
            argument = next(
                (
                    item
                    for item in route.get("actions", ())
                    if item.get("id") == argument_id
                    and item.get("kind") == "argument"
                    ),
                    None,
                )
            if argument is None:
                findings.append(
                    f"semantic review candidate {case_id} references unknown positional argument {argument_id}"
                )
            elif not _value_shape_accepts(argument, supplied_values):
                findings.append(
                    f"invocation for {case_id} has an invalid positional value shape"
                )
            if argument is not None and not _choice_values_accept(
                argument, supplied_values, choice
            ):
                findings.append(f"invocation for {case_id} omits its positional choice")
        if candidate_kind == "argument-shape":
            argument_id = candidate.get("shape", {}).get("argument_id")
            if argument_id is None and candidate.get("members"):
                argument_id = candidate["members"][0]
            supplied_values = positional_occurrences.get(str(argument_id), ())
            argument = next(
                (
                    item
                    for item in route.get("actions", ())
                    if item.get("id") == argument_id
                    and item.get("kind") == "argument"
                    ),
                    None,
                )
            if argument is None:
                findings.append(
                    f"semantic review candidate {case_id} references unknown positional argument {argument_id}"
                )
            elif not supplied_values:
                findings.append(f"invocation for {case_id} omits its positional value")
            elif not _choices_accept(argument, supplied_values):
                findings.append(
                    f"invocation for {case_id} supplies a positional value outside its choices"
                )
            elif not _value_shape_accepts(argument, supplied_values):
                findings.append(
                    f"invocation for {case_id} has an invalid positional value shape"
                )
        if candidate_kind in {"exclusive-member", "exclusive-conflict"}:
            group_id = str(candidate_shape.get("group_id", ""))
            group_actions = [
                action
                for action in route.get("actions", ())
                if action.get("kind") == "option"
                and str(action.get("exclusive_group")) == group_id
            ]
            group_occurrences = {
                str(action.get("id", "")): option_occurrences.get(
                    str(action.get("id", "")), ()
                )
                for action in group_actions
            }
            if candidate_kind == "exclusive-member":
                selected = str(candidate_shape.get("selected_option", ""))
                present = [
                    option_id
                    for option_id, occurrences in group_occurrences.items()
                    if occurrences
                ]
                if present != [selected]:
                    findings.append(
                        f"invocation for {case_id} must supply only its selected exclusive option"
                    )
            else:
                expected_ids = {str(value) for value in candidate_shape.get("options", ())}
                present = {
                    option_id
                    for option_id, occurrences in group_occurrences.items()
                    if occurrences
                }
                total_occurrences = sum(
                    len(occurrences) for occurrences in group_occurrences.values()
                )
                if present != expected_ids or total_occurrences != len(expected_ids):
                    findings.append(
                        f"invocation for {case_id} must supply each conflicting option once and no other group option"
                    )
        for member in candidate["members"]:
            matched_action = next(
                (action for action in route.get("actions", []) if action["id"] == member),
                None,
            )
            if matched_action is None:
                continue
            if candidate_kind == "interaction":
                # Local interaction options were checked in the interaction
                # branch above, where foreign and local option records are
                # handled through the same value-shape contract.
                continue
            if matched_action["kind"] == "option":
                if candidate_kind == "minimum" and not matched_action.get("required"):
                    continue
                expected_spelling = candidate.get("shape", {}).get("spelling")
                expected_spellings = (
                    [str(expected_spelling)]
                    if expected_spelling is not None
                    else matched_action["flags"]
                )
                if has_invalid_flag_value(matched_action):
                    findings.append(
                        f"invocation for {case_id} supplies an inline value to flag-only option {matched_action['id']}"
                    )
                active_occurrences = active_option_occurrences(matched_action)
                matching_occurrences = [
                    occurrence
                    for occurrence in active_occurrences
                    if occurrence[0] in expected_spellings
                ]
                if not matching_occurrences:
                    findings.append(
                        f"invocation for {case_id} omits its reviewed option spelling"
                    )
                else:
                    supplied_values = tuple(
                        value
                        for _spelling, values, _inline in matching_occurrences
                        for value in values
                    )
                    if not _option_occurrences_accept(
                        matched_action, option_occurrences.get(member, ())
                    ):
                        add_option_value_findings(
                            matched_action,
                            option_occurrences.get(member, ()),
                            case_id=case_id,
                            option_id=member,
                        )
                if candidate_kind == "option-choice":
                    choice = candidate.get("shape", {}).get("choice")
                    if not _choice_values_accept(
                        matched_action, supplied_values, choice
                    ):
                        findings.append(
                            f"invocation for {case_id} does not supply its reviewed option choice"
                        )
    for case_id, case in cases.items():
        if case_id not in candidates and case.state != "retired":
            findings.append(f"stale semantic case needs explicit retirement: {case_id}")
    if not surface.get("syntax_complete", False):
        findings.extend(
            "incomplete parser syntax: " + reason
            for reason in surface.get("incomplete", [])
        )
    return findings


def sync_cli_surface(
    app: Any,
    *,
    review_path: str | Path,
    manifest_path: str | Path,
    spec_path: str | Path,
    max_candidates: int | None = None,
) -> SurfaceReport:
    """Write only the generated manifest and the marked spec block.

    The review catalog is never written or reserialized.
    """

    _, _, manifest_text, spec_text, findings = _prepare(
        app,
        review_path=review_path,
        manifest_path=manifest_path,
        spec_path=spec_path,
        max_candidates=max_candidates,
    )
    _atomic_write_text(manifest_path, manifest_text, newline="")
    _write_text_preserving_newlines(spec_path, spec_text)
    return SurfaceReport(tuple(findings))


def check_cli_surface(
    app: Any,
    *,
    review_path: str | Path,
    manifest_path: str | Path,
    spec_path: str | Path,
    max_candidates: int | None = None,
) -> SurfaceReport:
    """Compare current registry output with committed manifest and spec text."""

    _, _, manifest_text, expected_spec, findings = _prepare(
        app,
        review_path=review_path,
        manifest_path=manifest_path,
        spec_path=spec_path,
        max_candidates=max_candidates,
    )
    try:
        committed_manifest = _read_text_preserving_newlines(manifest_path)
    except OSError:
        committed_manifest = None
    if committed_manifest != manifest_text:
        findings.append("generated CLI manifest is stale")
    try:
        committed_spec = _read_text_preserving_newlines(spec_path)
    except (OSError, SurfaceSpecError, SurfaceError, ReviewCatalogError) as exc:
        findings.append(f"cannot check generated CLI spec: {exc}")
    else:
        if committed_spec != expected_spec:
            findings.append("generated CLI spec block is stale")
    return SurfaceReport(tuple(findings))


def render_cli_review_template(
    surface: Mapping[str, Any], catalog: ReviewCatalog
) -> str:
    """Render new and changed review instructions without changing TOML."""

    known = catalog.cases_by_id
    lines: list[str] = []
    for candidate in surface.get("candidates", []):
        case_id = str(candidate["id"])
        existing = known.get(case_id)
        if existing is not None:
            if (
                existing.state == "active"
                and existing.reviewed_signature == candidate["signature"]
            ):
                continue
            reason = (
                "This retired case is active again; review it and explicitly reactivate the existing TOML row."
                if existing.state == "retired"
                else "After review, update its decision and evidence in the existing TOML row."
            )
            lines.extend(
                (
                    f"# Existing case {case_id!r} needs review.",
                    f"# Current generated signature: {candidate['signature']}",
                    f"# {reason}",
                    "# Update reviewed_signature, invocation, expected status, effects,",
                    "# rationale, and test_ids after making the semantic decision.",
                    "",
                )
            )
            continue
        encoded_id = json.dumps(case_id, ensure_ascii=False)
        encoded_signature = json.dumps(candidate["signature"])
        lines.extend(
            (
                f"# Candidate kind: {candidate.get('kind', 'unknown')}",
                f"# Route: {candidate.get('route_id', '')}",
                f"# Surface IDs: {', '.join(candidate.get('members', []))}",
                "# Add a real invocation (argv tokens excluding the executable),",
                "# expected outcome/status, effects, rationale, and exact test node IDs.",
                "[[cases]]",
                f"id = {encoded_id}",
                'state = "pending"',
                f"reviewed_signature = {encoded_signature}",
                "",
            )
        )
    return "\n".join(lines)


def assert_cli_case_tests(collected_items: Sequence[Any], catalog: ReviewCatalog) -> None:
    """Assert active review cases reference collected, correctly marked tests."""

    by_nodeid = {
        getattr(item, "nodeid", None): item
        for item in collected_items
        if isinstance(getattr(item, "nodeid", None), str)
    }
    active_cases = {case.case_id: case for case in catalog.cases if case.state == "active"}
    errors: list[str] = []
    known_cases = {case.case_id for case in catalog.cases}
    seen_case_ids: set[str] = set()
    for case in catalog.cases:
        if not case.case_id:
            errors.append("review catalog contains an empty case ID")
        if case.case_id in seen_case_ids:
            errors.append(f"review catalog contains duplicate case ID {case.case_id!r}")
        seen_case_ids.add(case.case_id)
    marked: dict[str, set[str]] = {}
    for nodeid, item in by_nodeid.items():
        iter_markers = getattr(item, "iter_markers", None)
        markers = list(iter_markers(name="cli_case")) if callable(iter_markers) else []
        for marker in markers:
            marker_args = getattr(marker, "args", ())
            if len(marker_args) != 1 or not isinstance(marker_args[0], str) or not marker_args[0]:
                errors.append(f"test {nodeid!r} has an empty or malformed cli_case marker")
                continue
            case_id = marker_args[0]
            if case_id not in known_cases:
                errors.append(f"test {nodeid!r} references unknown CLI case {case_id!r}")
                continue
            if case_id not in active_cases:
                errors.append(f"test {nodeid!r} references non-active CLI case {case_id!r}")
                continue
            if nodeid not in active_cases[case_id].test_ids:
                errors.append(
                    f"test {nodeid!r} is marked for CLI case {case_id!r} but is not listed in test_ids"
                )
            marked.setdefault(case_id, set()).add(nodeid)
    for case_id, case in active_cases.items():
        if not case.test_ids:
            errors.append(f"active CLI case {case_id!r} has no test_ids")
            continue
        if len(case.test_ids) != len(set(case.test_ids)):
            errors.append(f"active CLI case {case_id!r} has duplicate test_ids")
        for nodeid in case.test_ids:
            item = by_nodeid.get(nodeid)
            if item is None:
                errors.append(f"CLI case {case_id!r} references uncollected test {nodeid!r}")
                continue
            if nodeid not in marked.get(case_id, set()):
                errors.append(f"test {nodeid!r} lacks cli_case({case_id!r}) marker")
            if _statically_skipped(item):
                errors.append(f"CLI case {case_id!r} references a statically skipped test {nodeid!r}")
        if not marked.get(case_id):
            errors.append(f"active CLI case {case_id!r} has no collected marked test")
    if errors:
        raise AssertionError("CLI case test coverage failed:\n- " + "\n- ".join(errors))


def _statically_skipped(item: Any) -> bool:
    """Identify unconditional skips and literal-true skip conditions safely."""

    closest = getattr(item, "get_closest_marker", None)
    if not callable(closest):
        return False
    if closest("skip") is not None:
        return True
    for marker in item.iter_markers(name="skipif"):
        condition = marker.args[0] if marker.args else marker.kwargs.get("condition")
        if condition is True:
            return True
    return False
