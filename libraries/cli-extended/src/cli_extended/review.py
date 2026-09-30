"""Product-owned semantic review catalogs and generated CLI specification blocks."""

from __future__ import annotations

import argparse
import json
import os
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
    _minimum_values,
    export_cli_surface,
    render_cli_surface_json,
)
from .parser import RegisteredCli

REVIEW_SCHEMA_VERSION = 1
_REVIEW_CASE_STATES = ("pending", "active", "retired")
_REVIEW_DECISIONS = ("accept", "refuse")
SURFACE_START_MARKER = "<!-- cli-extended-surface:start -->"
SURFACE_END_MARKER = "<!-- cli-extended-surface:end -->"


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
            required = {
                "decision": decision,
                "reviewed_signature": signature,
                "rationale": rationale.strip() or None,
                "expected_exit_status": expected_status,
                "invocation": True if invocation_declared else None,
                "effects": True if effects_declared else None,
                "test_ids": test_ids or None,
            }
            missing = [name for name, value in required.items() if value is None]
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

    lines = [
        "## Generated CLI surface and semantic review",
        "",
        f"Surface schema: `{surface['schema_version']}`; review catalog schema: `{REVIEW_SCHEMA_VERSION}`.",
        "",
        "The manifest records registered syntax. The review catalog owns expected behavior, effects, rationale, and test references.",
        "",
        "### Command routes",
        "",
        "| Surface ID | Route kind | Invocation path | Nested commands | Description | Group | Behavior | Parser completeness |",
        "| --- | --- | --- | --- | --- | --- | --- | --- |",
    ]
    for route in surface["routes"]:
        path = " ".join(route["path"]) or surface["entrypoint"]["command"]
        if route.get("aliases"):
            path += " (aliases: " + ", ".join(route["aliases"]) + ")"
        nested_commands = []
        for group in route.get("subcommand_groups", []):
            label = ", ".join(group.get("subcommands", [])) or "none"
            if group.get("required"):
                label += " (required)"
            nested_commands.append(f"{group.get('destination')}: {label}")
        lines.append(
            "| " + " | ".join(
                _markdown_cell(value)
                for value in (
                    route["id"],
                    route.get("kind", "invocation"),
                    path,
                    "; ".join(nested_commands),
                    route.get("description") or "",
                    route.get("group") or "",
                    ", ".join(route.get("behavior", [])),
                    "complete" if route.get("syntax_complete") else "incomplete",
                )
            ) + " |"
        )
    lines.extend(
        (
            "",
            "### Arguments and options",
            "",
        "| Surface ID | Kind | Spelling/name | Shape | Required | Choices/default | Scope/placement | Help group |",
        "| --- | --- | --- | --- | --- | --- | --- | --- |",
        )
    )
    for route in surface["routes"]:
        for action in route.get("actions", []):
            label = "/".join(action.get("flags", ())) if action["kind"] == "option" else action["name"]
            shape = action.get("metavar")
            if action["kind"] == "option" and action.get("nargs") is not None:
                shape = {"metavar": shape, "nargs": action["nargs"]}
            if action["kind"] == "argument":
                shape = {"metavar": shape, "nargs": action["nargs"]}
            details = {
                "choices": action.get("choices"),
                "default": action.get("effective_default", action.get("default")),
                "exclusive_group": action.get("exclusive_group"),
            }
            scope = action.get("scope", "positional")
            if action.get("placement"):
                scope += "; " + ", ".join(
                    key for key, enabled in action["placement"].items() if enabled
                )
            parser_path = action.get("parser_path")
            if parser_path is not None:
                scope += "; parser " + (" ".join(parser_path) or "<entrypoint>")
            if action.get("before_nested_subcommand"):
                scope += "; before nested subcommand"
            lines.append(
                "| " + " | ".join(
                    _markdown_cell(value)
                    for value in (
                        action["id"], action["kind"], label, shape,
                        action.get("required"), details, scope,
                        action.get("help_group", ""),
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
        expected_status = case.expected_exit_status if case else ""
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
        rationale = case.rationale if case else "Add a product-owned decision and test reference."
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
    def is_negative_number(token: str) -> bool:
        return (
            len(token) > 1
            and token[0] == "-"
            and token[1:].replace(".", "", 1).isdigit()
        )

    def action_for_option(
        route: Mapping[str, Any], option: str, depth: int
    ) -> Mapping[str, Any] | None:
        path = tuple(route.get("path", ()))

        def option_is_available(action: Mapping[str, Any]) -> bool:
            parser_path = tuple(action.get("parser_path", ()))
            if depth == 0 and not path:
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
        if not surface.get("entrypoint", {}).get("allow_abbrev"):
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
            return not is_negative_number(token)

        nargs = action.get("nargs")
        if nargs == 0:
            return index + 1
        if nargs in {"...", "A..."}:
            return len(argv)
        if nargs is None:
            if inline:
                return index + 1
            return (
                index + 2
                if index + 1 < len(argv) and not is_option_boundary(index + 1)
                else index + 1
            )
        if isinstance(nargs, int):
            additional = max(nargs - 1, 0) if inline else nargs
            end = index + 1
            while end < len(argv) and end < index + 1 + additional:
                if is_option_boundary(end):
                    break
                end += 1
            return end
        if nargs == "?":
            if inline:
                return index + 1
            if index + 1 < len(argv) and not is_option_boundary(index + 1):
                return index + 2
            return index + 1
        if nargs in {"*", "+"}:
            if inline:
                return index + 1
            end = index + 1
            while end < len(argv) and not is_option_boundary(end):
                end += 1
            return end
        return index + 1

    def invocation_parts(
        argv: Sequence[str],
        route: Mapping[str, Any],
        command_positions: Sequence[int],
    ) -> tuple[
        list[tuple[int, str]],
        dict[str, list[tuple[str, tuple[str, ...], bool]]],
    ]:
        """Return parser-depth positionals and recognized options by action ID."""

        command_positions_set = set(command_positions)
        path_depth = 0
        options_enabled = {0: True}
        positionals: list[tuple[int, str]] = []
        options: dict[str, list[tuple[str, tuple[str, ...], bool]]] = {}
        index = 0
        while index < len(argv):
            if index in command_positions_set:
                path_depth += 1
                options_enabled[path_depth] = True
                index += 1
                continue
            token = argv[index]
            if token == "--" and options_enabled.get(path_depth, True):
                options_enabled[path_depth] = False
                index += 1
                continue
            option, separator, inline_value = token.partition("=")
            action = (
                action_for_option(route, option, path_depth)
                if options_enabled.get(path_depth, True)
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
                and options_enabled.get(path_depth, True)
                and not is_negative_number(token)
            ):
                index += 1
                continue
            positionals.append((path_depth, token))
            index += 1
        return positionals, options

    def positional_values_by_action(
        route: Mapping[str, Any], positionals: Sequence[tuple[int, str]]
    ) -> dict[str, tuple[str, ...]]:
        """Assign positional tokens to their declared parser-level actions."""

        path = tuple(route.get("path", ()))
        positions_by_depth: dict[int, list[str]] = {}
        for depth, token in positionals:
            positions_by_depth.setdefault(depth, []).append(token)

        values_by_action: dict[str, tuple[str, ...]] = {}
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
                minimum = int(
                    action.get("minimum_values", _minimum_values(nargs))
                )
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
                    int(later.get("minimum_values", _minimum_values(later.get("nargs"))))
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
        return values_by_action

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
                    "remaining": int(action.get("minimum_values", 1)),
                    "optional_used": False,
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
            index = positional_indexes.get(depth, 0)
            while index < len(actions):
                state = actions[index]
                action = state["action"]
                nargs = action.get("nargs")
                if state["remaining"]:
                    state["remaining"] -= 1
                    positional_indexes[depth] = index
                    return True
                if nargs == "?" and not state["optional_used"]:
                    if is_command:
                        return False
                    state["optional_used"] = True
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
                if options_enabled.get(segment, True):
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
                if options_enabled.get(segment, True)
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
                and options_enabled.get(segment, True)
                and not is_negative_number(token)
            ):
                return None
            if consumes_parent_positional(segment, False):
                index += 1
                continue
            return None
        if segment == len(accepted_by_position):
            return tuple(positions)
        return None

    findings: list[str] = []
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
        non_command_positions, option_occurrences = invocation_parts(
            case.invocation, route, command_positions or ()
        )
        positional_occurrences = positional_values_by_action(
            route, non_command_positions
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

        candidate_kind = str(candidate.get("kind", ""))
        if candidate_kind == "route-alias":
            alias = str(candidate.get("shape", {}).get("alias", ""))
            if (
                command_positions is None
                or not command_positions
                or case.invocation[command_positions[-1]] != alias
            ):
                findings.append(f"invocation for {case_id} omits its command alias")
        if candidate_kind == "minimum":
            required_arguments = candidate.get("shape", {}).get(
                "required_arguments", ()
            )
            minimum_required_values = candidate.get("shape", {}).get(
                "required_argument_values", {}
            )
            for argument_id in required_arguments:
                supplied_values = positional_occurrences.get(str(argument_id))
                required_values = int(
                    minimum_required_values.get(str(argument_id), 1)
                )
                if supplied_values is None or len(supplied_values) < required_values:
                    findings.append(
                        f"minimum invocation for {case_id} omits required positional argument(s)"
                    )
            for option_id in candidate.get("shape", {}).get("required_options", ()):
                action = next(
                    (
                        item for item in route.get("actions", ())
                        if item.get("id") == option_id and item.get("kind") == "option"
                    ),
                    None,
                )
                if action is not None and has_invalid_flag_value(action):
                    findings.append(
                        f"minimum invocation for {case_id} supplies an inline value to flag-only option {option_id}"
                    )
                active_occurrences = (
                    active_option_occurrences(action) if action is not None else []
                )
                if action is not None and not active_occurrences:
                    findings.append(
                        f"minimum invocation for {case_id} omits required option {option_id}"
                    )
                elif action is not None and any(
                    len(values) < _minimum_values(action.get("nargs"))
                    for _spelling, values, _inline in active_occurrences
                ):
                    findings.append(
                        f"minimum invocation for {case_id} omits a value for required "
                        f"option {option_id}"
                    )
            for group_id, option_ids in candidate.get("shape", {}).get(
                "required_exclusive_groups", {}
            ).items():
                group_actions = [
                    action
                    for action in route.get("actions", ())
                    if action.get("id") in option_ids and action.get("kind") == "option"
                ]
                if any(has_invalid_flag_value(action) for action in group_actions):
                    findings.append(
                        f"minimum invocation for {case_id} supplies an inline value to a flag-only option in required exclusive group {group_id}"
                    )
                present_group_actions = [
                    action
                    for action in group_actions
                    if active_option_occurrences(action)
                ]
                if not present_group_actions:
                    findings.append(
                        f"minimum invocation for {case_id} omits required exclusive "
                        f"group {group_id}"
                    )
                elif len(present_group_actions) > 1:
                    findings.append(
                        f"minimum invocation for {case_id} supplies multiple options "
                        f"for required exclusive group {group_id}"
                    )
                elif any(
                    len(values)
                    < _minimum_values(present_group_actions[0].get("nargs"))
                    for _spelling, values, _inline in active_option_occurrences(
                        present_group_actions[0]
                    )
                ):
                    findings.append(
                        f"minimum invocation for {case_id} omits a value for its "
                        f"required exclusive group {group_id}"
                    )
        if candidate_kind == "argument-choice":
            choice = candidate.get("shape", {}).get("choice")
            argument_id = candidate.get("shape", {}).get("argument_id")
            if argument_id is None and candidate.get("members"):
                argument_id = candidate["members"][0]
            supplied_values = positional_occurrences.get(str(argument_id), ())
            if str(choice) not in supplied_values:
                findings.append(f"invocation for {case_id} omits its positional choice")
        if candidate_kind == "argument-shape":
            argument_id = candidate.get("shape", {}).get("argument_id")
            if argument_id is None and candidate.get("members"):
                argument_id = candidate["members"][0]
            if not positional_occurrences.get(str(argument_id), ()):
                findings.append(f"invocation for {case_id} omits its positional value")
        for member in candidate["members"]:
            matched_action = next(
                (action for action in route.get("actions", []) if action["id"] == member),
                None,
            )
            if matched_action is None:
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
                occurrence = next(
                    (
                        (spelling, values)
                        for spelling, values, _inline in active_occurrences
                        if spelling in expected_spellings
                    ),
                    None,
                )
                if occurrence is None:
                    findings.append(
                        f"invocation for {case_id} omits its reviewed option spelling"
                    )
                    supplied_values: tuple[str, ...] = ()
                else:
                    _spelling, supplied_values = occurrence
                if candidate_kind == "option-choice":
                    choice = candidate.get("shape", {}).get("choice")
                    if str(choice) not in supplied_values:
                        findings.append(
                            f"invocation for {case_id} does not supply its reviewed option choice"
                        )
                elif active_occurrences and not any(
                    len(values) >= _minimum_values(matched_action.get("nargs"))
                    for _spelling, values, _inline in active_occurrences
                    if _spelling in expected_spellings
                ):
                    findings.append(
                        f"invocation for {case_id} omits a value for {expected_spellings[0]}"
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
        encoded_signature = json.dumps(candidate["signature"], ensure_ascii=False)
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
