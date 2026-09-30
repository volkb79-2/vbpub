"""Product-owned semantic review catalogs and generated CLI specification blocks."""

from __future__ import annotations

import argparse
import json
import shlex
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
                f"{location}.state must be pending, active, or retired"
            )
        if state not in {"pending", "active", "retired"}:
            raise ReviewCatalogError(f"{location}.state must be pending, active, or retired")
        decision = item.get("decision")
        if decision is not None and not isinstance(decision, str):
            raise ReviewCatalogError(f"{location}.decision must be accept or refuse")
        if decision is not None and decision not in {"accept", "refuse"}:
            raise ReviewCatalogError(f"{location}.decision must be accept or refuse")
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
    with Path(path).open("w", encoding="utf-8", newline="") as stream:
        stream.write(text)


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
    def has_option(argv: Sequence[str], flags: Sequence[str]) -> bool:
        return any(
            token == flag or token.startswith(flag + "=")
            for token in argv
            for flag in flags
        )

    def option_values(
        argv: Sequence[str], flags: Sequence[str], action: Mapping[str, Any]
    ) -> tuple[bool, tuple[str, ...]]:
        for index, token in enumerate(argv):
            for flag in flags:
                if token == flag:
                    nargs = action.get("nargs")
                    if nargs == 0:
                        return True, ()
                    if nargs is None:
                        end = min(index + 2, len(argv))
                        return True, tuple(argv[index + 1 : end])
                    if isinstance(nargs, int):
                        end = min(index + 1 + nargs, len(argv))
                        return True, tuple(argv[index + 1 : end])
                    if nargs == "?":
                        if index + 1 < len(argv) and not argv[index + 1].startswith("-"):
                            return True, (argv[index + 1],)
                        return True, ()
                    if nargs == "*" or nargs == "+":
                        end = index + 1
                        while end < len(argv) and not argv[end].startswith("-"):
                            end += 1
                        return True, tuple(argv[index + 1 : end])
                    if nargs == argparse.REMAINDER or nargs == argparse.PARSER:
                        return True, tuple(argv[index + 1 :])
                    return True, ()
                if token.startswith(flag + "="):
                    value = token[len(flag) + 1 :]
                    nargs = action.get("nargs")
                    if nargs == 0:
                        return False, ()
                    if isinstance(nargs, int) and nargs > 1:
                        end = min(index + nargs, len(argv))
                        return True, (value, *argv[index + 1 : end])
                    return True, (value,)
        return False, ()

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

        def find(segment: int, start: int) -> tuple[int, ...] | None:
            if segment == len(accepted_by_position):
                return ()
            accepted = accepted_by_position[segment]
            for index in range(start, len(argv)):
                if argv[index] in accepted:
                    following = find(segment + 1, index + 1)
                    if following is not None:
                        return (index, *following)
            return None

        return find(0, 0)

    def positional_tokens(
        argv: Sequence[str],
        route: Mapping[str, Any],
        command_positions: Sequence[int],
    ) -> list[str]:
        """Lex the reviewed argv enough to exclude known option values."""

        by_flag = {
            flag: action
            for action in route.get("actions", ())
            if action.get("kind") == "option"
            for flag in action.get("flags", ())
        }
        route_positions_set = set(command_positions)
        result: list[str] = []
        index = 0
        while index < len(argv):
            if index in route_positions_set:
                index += 1
                continue
            token = argv[index]
            if token == "--":
                result.extend(
                    item
                    for position, item in enumerate(argv[index + 1 :], start=index + 1)
                    if position not in route_positions_set
                )
                break
            option, separator, _value = token.partition("=")
            action = by_flag.get(option)
            if action is None:
                if not token.startswith("-"):
                    result.append(token)
                index += 1
                continue
            if separator:
                index += 1
                continue
            nargs = action.get("nargs")
            if nargs == 0:
                index += 1
                continue
            if nargs is None:
                index += 2
                continue
            if isinstance(nargs, int):
                index += 1 + nargs
                continue
            if nargs == "?":
                index += 1
                if index < len(argv) and not argv[index].startswith("-"):
                    index += 1
                continue
            if nargs in {"*", "+"}:
                index += 1
                while index < len(argv) and not argv[index].startswith("-"):
                    index += 1
                continue
            if nargs in {"...", "A..."}:
                break
            index += 1
        return result

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
        non_command_positions = positional_tokens(
            case.invocation, route, command_positions or ()
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
            required_value_count = (
                sum(minimum_required_values.values())
                if minimum_required_values
                else len(required_arguments)
            )
            if len(non_command_positions) < required_value_count:
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
                if action is not None and not has_option(case.invocation, action["flags"]):
                    findings.append(
                        f"minimum invocation for {case_id} omits required option {option_id}"
                    )
            for group_id, option_ids in candidate.get("shape", {}).get(
                "required_exclusive_groups", {}
            ).items():
                group_actions = [
                    action
                    for action in route.get("actions", ())
                    if action.get("id") in option_ids and action.get("kind") == "option"
                ]
                if not any(
                    has_option(case.invocation, action.get("flags", ()))
                    for action in group_actions
                ):
                    findings.append(
                        f"minimum invocation for {case_id} omits required exclusive group {group_id}"
                    )
        if candidate_kind == "argument-choice":
            choice = candidate.get("shape", {}).get("choice")
            if str(choice) not in non_command_positions:
                findings.append(f"invocation for {case_id} omits its positional choice")
        if candidate_kind == "argument-shape" and not any(
            token and not token.startswith("-") for token in non_command_positions
        ):
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
                found, supplied_values = option_values(
                    case.invocation, expected_spellings, matched_action
                )
                if not found:
                    findings.append(
                        f"invocation for {case_id} omits its reviewed option spelling"
                    )
                if candidate_kind == "option-choice":
                    choice = candidate.get("shape", {}).get("choice")
                    if str(choice) not in supplied_values:
                        findings.append(
                            f"invocation for {case_id} does not supply its reviewed option choice"
                        )
                elif len(supplied_values) < _minimum_values(matched_action.get("nargs")):
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
    Path(manifest_path).write_text(manifest_text, encoding="utf-8", newline="")
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
