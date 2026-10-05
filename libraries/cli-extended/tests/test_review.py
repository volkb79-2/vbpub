from __future__ import annotations

import argparse
import io
import json
import sys
import types
from pathlib import Path

import pytest

from cli_extended import (
    ArgumentSpec,
    CliIdentity,
    CliRegistry,
    OptionSpec,
    ReviewCatalog,
    ReviewCase,
    ReviewCatalogError,
    SurfaceSpecError,
    VerbSpec,
    assert_cli_case_tests,
    check_cli_surface,
    export_cli_surface,
    load_cli_review_catalog,
    render_cli_review_template,
    render_cli_surface_markdown,
    sync_cli_surface,
)
from cli_extended.review import (
    SURFACE_END_MARKER,
    SURFACE_START_MARKER,
    SurfaceReport,
    _case_status,
    _choice_values_accept,
    _converted_action_values,
    _markdown_cell,
    _option_occurrences_accept,
    _parse_interaction_groups,
    _replace_generated_region,
    _review_findings,
    _statically_skipped,
    _validate_distinct_surface_paths,
    _values_satisfy_action,
    _value_count_problem,
    _value_shape_accepts,
)
from cli_extended.surface import _route_common_actions, _routes_by_path
from cli_extended.surface_cli import _load_factory

IDENTITY = CliIdentity("AUDIT", "1.0", "Audit Tool", command="audit-tool")
ROUTE_ID = "route:entrypoint:audit-tool/inspect"


def _argparse_negative_number_settings(*, parser_path=(), negative_option=False):
    parser = argparse.ArgumentParser(add_help=False)
    if negative_option:
        parser.add_argument("-1", action="store_true")
    matcher = parser._negative_number_matcher
    return parser, {
        "parser_path": list(parser_path),
        "negative_number_matcher": {
            "pattern": matcher.pattern,
            "flags": matcher.flags,
        },
        "has_negative_number_optionals": parser._has_negative_number_optionals,
        "negative_number_matcher_custom": False,
    }


INTERACTIONS = (
    {
        "id": "mode-and-dry-run/both",
        "route_id": ROUTE_ID,
        "option_ids": (
            f"option:{ROUTE_ID}/--mode",
            f"option:{ROUTE_ID}/--dry-run",
        ),
    },
)


def _build_cli():
    registry = CliRegistry(IDENTITY, prog="audit-tool", description="Audit resources.")
    registry.register(
        VerbSpec(
            "inspect",
            description="inspect one resource",
            arguments=(),
            options=(
                OptionSpec(
                    ("--mode",),
                    "select a mode",
                    parser_kwargs={"choices": ("safe", "fast"), "default": "safe"},
                ),
                OptionSpec(
                    ("--source",),
                    "read from a file",
                    mutually_exclusive_group="source",
                    mutually_exclusive_required=True,
                ),
                OptionSpec(
                    ("--inline",),
                    "read inline data",
                    mutually_exclusive_group="source",
                    mutually_exclusive_required=True,
                ),
                OptionSpec(("--dry-run",), "plan only", parser_kwargs={"action": "store_true"}),
            ),
            mutating=True,
            handler=lambda *_: 0,
        )
    )
    return registry.build()


def _choice_value(action, preferred=None):
    choices = action.get("choices")
    if preferred is not None:
        return str(preferred)
    if isinstance(choices, list) and choices:
        return str(choices[0])
    return "VALUE"


def _option_argv(action, spelling=None):
    nargs = action.get("nargs")
    minimum = action.get("minimum_values")
    if minimum is None:
        if nargs is None or nargs == "+":
            minimum = 1
        elif isinstance(nargs, int):
            minimum = max(0, nargs)
        else:
            minimum = 0
    argv = [spelling or action["flags"][0]]
    if nargs != 0:
        argv.extend(_choice_value(action) for _ in range(int(minimum)))
    return argv


def _candidate_argv(candidate, surface):
    route = next(route for route in surface["routes"] if route["id"] == candidate["route_id"])
    # Library-owned controls are named in the manifest only; the contract
    # table supplies their arity for generated argv.
    paths = _routes_by_path(surface["routes"])
    actions = {
        action["id"]: action
        for action in (*route["actions"], *_route_common_actions(route, paths))
    }
    argv = list(route["path"])
    kind = candidate["kind"]
    candidate_argument_id = (
        str(candidate.get("shape", {}).get("argument_id"))
        if kind in {"argument-shape", "argument-choice"}
        else None
    )
    if kind == "route-alias":
        argv[-1] = candidate["shape"]["alias"]
    elif kind == "minimum":
        required_options = list(candidate["shape"]["required_options"])
        for _group_id, option_ids in candidate["shape"]["required_exclusive_groups"].items():
            required_options.append(option_ids[0])
        for option_id in required_options:
            argv.extend(_option_argv(actions[option_id]))
    elif kind in {"argument-shape", "argument-choice"}:
        pass
    elif kind == "option-spelling":
        spelling = candidate["shape"]["spelling"]
        action = actions[candidate["shape"]["option_id"]]
        argv.extend(_option_argv(action, spelling))
    elif kind == "option-choice":
        action = actions[candidate["shape"]["option_id"]]
        option_argv = _option_argv(action)
        if action.get("nargs") != 0:
            if len(option_argv) == 1:
                option_argv.append(str(candidate["shape"]["choice"]))
            else:
                option_argv[1] = str(candidate["shape"]["choice"])
        argv.extend(option_argv)
    elif kind in {"exclusive-member", "exclusive-conflict", "interaction"}:
        member_ids = list(candidate["members"])
        if kind == "interaction":
            member_ids.extend(candidate["shape"].get("required_options", ()))
            for option_ids in candidate["shape"].get(
                "required_exclusive_groups", {}
            ).values():
                if option_ids:
                    member_ids.append(option_ids[0])
        for member_id in dict.fromkeys(member_ids):
            action = actions.get(member_id)
            if action is None:
                continue
            argv.extend(_option_argv(action))
        if kind == "interaction":
            for external_option in candidate["shape"].get("external_options", ()):
                owner_route = next(
                    (
                        item
                        for item in surface["routes"]
                        if item["id"] == external_option["route_id"]
                    ),
                    None,
                )
                if owner_route is None:
                    raise AssertionError(
                        f"missing route for external option {external_option['id']}"
                    )
                external_action = next(
                    (
                        action
                        for action in owner_route["actions"]
                        if action["id"] == external_option["id"]
                    ),
                    None,
                )
                if external_action is None:
                    raise AssertionError(
                        f"missing action for external option {external_option['id']}"
                    )
                argv.extend(_option_argv(external_action))
    route_actions = route.get("actions", ())
    baseline_arguments = [
        action
        for action in route_actions
        if action.get("kind") == "argument" and action.get("required")
    ]
    baseline_options = [
        action
        for action in route_actions
        if action.get("kind") == "option" and action.get("required")
    ]
    baseline_groups = {}
    for action in route_actions:
        group_id = action.get("exclusive_group")
        if action.get("kind") == "option" and group_id is not None:
            baseline_groups.setdefault(str(group_id), []).append(action)
    baseline_argument_ids = {
        str(action.get("id", "")) for action in baseline_arguments
    }
    target_argument = actions.get(candidate_argument_id) if candidate_argument_id else None
    argument_depths = {
        tuple(action.get("parser_path", ()))
        for action in route_actions
        if action.get("kind") == "argument"
        and (
            str(action.get("id", "")) in baseline_argument_ids
            or str(action.get("id", "")) == candidate_argument_id
            or (
                target_argument is not None
                and tuple(action.get("parser_path", ()))
                == tuple(target_argument.get("parser_path", ()))
            )
        )
    }
    route_path = tuple(route.get("path", ()))
    for parser_path in sorted(argument_depths, key=len, reverse=True):
        depth = len(parser_path)
        arguments = [
            action
            for action in route_actions
            if action.get("kind") == "argument"
            and tuple(action.get("parser_path", ())) == parser_path
            and (depth == len(route_path) or action.get("before_nested_subcommand"))
        ]
        target_index = next(
            (
                index
                for index, action in enumerate(arguments)
                if str(action.get("id", "")) == candidate_argument_id
            ),
            None,
        )
        positional_tokens = []
        for index, action in enumerate(arguments):
            action_id = str(action.get("id", ""))
            is_target = action_id == candidate_argument_id
            is_required = action_id in baseline_argument_ids
            if not is_target and not is_required:
                if (
                    target_index is not None
                    and index < target_index
                    and action.get("nargs") in {"?", "*", "...", "A..."}
                ):
                    choices = action.get("choices")
                    positional_tokens.append(
                        str(choices[0])
                        if isinstance(choices, list) and choices
                        else "VALUE"
                    )
                continue
            choices = action.get("choices")
            value = (
                str(candidate["shape"]["choice"])
                if is_target and kind == "argument-choice"
                else str(choices[0])
                if isinstance(choices, list) and choices
                else "RESOURCE"
            )
            count = int(action.get("minimum_values", 1))
            positional_tokens.extend(value for _ in range(max(1, count)))
        argv[depth:depth] = positional_tokens

    present_flags = {
        token.partition("=")[0]
        for token in argv
        if token.startswith("-") and token != "-"
    }
    for action in baseline_options:
        if not present_flags.intersection(action.get("flags", ())):
            option_tokens = _option_argv(action)
            if action.get("placement", {}).get("before_verb"):
                argv[0:0] = option_tokens
            else:
                argv.extend(option_tokens)
            present_flags.update(action.get("flags", ()))

    for group_id, group_actions in baseline_groups.items():
        if not group_actions[0].get("exclusive_required"):
            continue
        if (
            kind == "exclusive-conflict"
            and candidate.get("shape", {}).get("group_id") == group_id
        ):
            continue
        if any(
            present_flags.intersection(action.get("flags", ()))
            for action in group_actions
        ):
            continue
        if group_actions:
            option_tokens = _option_argv(group_actions[0])
            if group_actions[0].get("placement", {}).get("before_verb"):
                argv[0:0] = option_tokens
            else:
                argv.extend(option_tokens)
            present_flags.update(group_actions[0].get("flags", ()))
    return argv


def _case_for_candidate(
    candidate,
    invocation,
    *,
    state="active",
    decision=None,
    expected_exit_status=None,
    signature=None,
    invocation_declared=True,
    effects_declared=True,
    test_ids=("tests/test_review.py::test_review_findings_fixture",),
    retirement_reason="",
):
    return ReviewCase(
        case_id=candidate["id"],
        state=state,
        decision=(
            ("accept" if state == "active" else None)
            if decision is None
            else decision
        ),
        reviewed_signature=signature or candidate.get("signature"),
        rationale="reviewed" if state == "active" else "",
        invocation=tuple(invocation),
        invocation_declared=invocation_declared,
        expected_exit_status=(
            (0 if state == "active" else None)
            if expected_exit_status is None
            else expected_exit_status
        ),
        expected_stdout_contains="",
        expected_stderr_contains="",
        effects=(),
        effects_declared=effects_declared,
        test_ids=tuple(test_ids),
        retirement_reason=retirement_reason,
    )


def _findings_for_invocation(
    candidate,
    route,
    invocation,
    *,
    route_prefixes=(),
    allow_abbrev=False,
):
    case = _case_for_candidate(candidate, invocation)
    surface = {
        "entrypoint": {
            "command": "audit-tool",
            "allow_abbrev": allow_abbrev,
        },
        "routes": [*route_prefixes, route],
        "candidates": [candidate],
        "syntax_complete": True,
    }
    return _review_findings(
        surface, ReviewCatalog("audit-tool", 8, (), (case,))
    )


def _write_catalog(path: Path, surface, *, active=True, extra_case=None):
    lines = [
        "schema_version = 1",
        'cli_id = "audit-tool"',
        "max_candidates = 128",
        "",
    ]
    for interaction in INTERACTIONS:
        group_id, combination_id = interaction["id"].rsplit("/", 1)
        lines.extend(
            (
                "[[interaction_groups]]",
                f'id = {json.dumps(group_id)}',
                f'route_id = {json.dumps(interaction["route_id"])}',
            )
        )
        lines.append("[[interaction_groups.combinations]]")
        lines.append(f'id = {json.dumps(combination_id)}')
        lines.append(
            "option_ids = [" + ", ".join(json.dumps(item) for item in interaction["option_ids"]) + "]"
        )
        lines.append("")
    for candidate in surface["candidates"]:
        argv = _candidate_argv(candidate, surface)
        refused = candidate["kind"] == "exclusive-conflict"
        lines.extend(
            (
                "[[cases]]",
                f'id = {json.dumps(candidate["id"])}',
                f'state = "{"active" if active else "pending"}"',
                f'decision = "{"refuse" if refused else "accept"}"',
                f'reviewed_signature = {json.dumps(candidate["signature"])}',
                'rationale = "Product reviewed the expected behavior."',
                "invocation = [" + ", ".join(json.dumps(token) for token in argv) + "]",
                f"expected_exit_status = {2 if refused else 0}",
                "effects = []",
                'test_ids = ["tests/test_review.py::test_surface_check_accepts_synced_outputs"]',
                "",
            )
        )
    if extra_case is not None:
        lines.extend(extra_case)
    path.write_text("\n".join(lines), encoding="utf-8")


def _make_files(tmp_path, *, active=True, spec_bytes=None):
    app = _build_cli()
    surface = export_cli_surface(app, interaction_groups=INTERACTIONS)
    review = tmp_path / "cli-review.toml"
    manifest = tmp_path / "cli-manifest.json"
    spec = tmp_path / "SPEC.md"
    _write_catalog(review, surface, active=active)
    if spec_bytes is None:
        spec_bytes = (
            b"# Product spec\r\n\r\nBefore\r\n"
            + SURFACE_START_MARKER.encode()
            + b"\r\nold generated text\r\n"
            + SURFACE_END_MARKER.encode()
            + b"\r\n\r\nAfter\r\n"
        )
    spec.write_bytes(spec_bytes)
    return app, surface, review, manifest, spec


def test_catalog_loads_declared_empty_invocation_and_effects(tmp_path):
    app = _build_cli()
    surface = export_cli_surface(app, interaction_groups=INTERACTIONS)
    path = tmp_path / "catalog.toml"
    candidate = surface["candidates"][0]
    path.write_text(
        "\n".join(
            (
                "schema_version = 1",
                'cli_id = "audit-tool"',
                "[[cases]]",
                f'id = {json.dumps(candidate["id"])}',
                'state = "pending"',
                'rationale = ""',
                "invocation = []",
                "effects = []",
            )
        ),
        encoding="utf-8",
    )
    catalog = load_cli_review_catalog(path)

    assert catalog.cases[0].invocation == ()
    assert catalog.cases[0].invocation_declared is True
    assert catalog.cases[0].effects_declared is True


def test_review_record_types_are_immutable():
    from dataclasses import FrozenInstanceError

    case = ReviewCase(
        case_id="case:one",
        state="pending",
        decision=None,
        reviewed_signature=None,
        rationale="",
        invocation=(),
        invocation_declared=False,
        expected_exit_status=None,
        expected_stdout_contains="",
        expected_stderr_contains="",
        effects=(),
        effects_declared=False,
        test_ids=(),
        retirement_reason="",
    )
    catalog = ReviewCatalog("audit-tool", 8, (), (case,))
    report = SurfaceReport(("pending",))

    with pytest.raises(FrozenInstanceError):
        case.case_id = "case:changed"
    with pytest.raises(FrozenInstanceError):
        catalog.cli_id = "different-tool"
    with pytest.raises(FrozenInstanceError):
        report.findings = ()


def test_catalog_rejects_bad_schema_fields_and_case_records(tmp_path):
    path = tmp_path / "catalog.toml"
    invalid_documents = (
        ('schema_version = true\ncli_id = "audit-tool"', "schema_version"),
        ('schema_version = 1\ncli_id = "audit-tool"\nwrong = 1', "unknown field"),
        ('schema_version = 1\ncli_id = ""', "cli_id"),
        ('schema_version = 1\ncli_id = "audit-tool"\nmax_candidates = true', "max_candidates"),
        ('schema_version = 1\ncli_id = "audit-tool"\n[[cases]]\nid = "x"\nstate = []', "state"),
        ('schema_version = 1\ncli_id = "audit-tool"\n[[cases]]\nid = "x"\ndecision = []', "decision"),
        ('schema_version = 1\ncli_id = "audit-tool"\n[[cases]]\nid = "x"\nreviewed_signature = 1', "reviewed_signature"),
        ('schema_version = 1\ncli_id = "audit-tool"\n[[cases]]\nid = "x"\nrationale = 1', "rationale"),
        ('schema_version = 1\ncli_id = "audit-tool"\n[[cases]]\nid = "x"\ninvocation = "bad"', "invocation"),
        ('schema_version = 1\ncli_id = "audit-tool"\n[[cases]]\nid = "x"\nexpected_exit_status = true', "expected_exit_status"),
        ('schema_version = 1\ncli_id = "audit-tool"\n[[cases]]\nid = "x"\nexpected_stdout_contains = 2', "expected output"),
        ('schema_version = 1\ncli_id = "audit-tool"\n[[cases]]\nid = "x"\neffects = "bad"', "effects"),
        ('schema_version = 1\ncli_id = "audit-tool"\n[[cases]]\nid = "x"\ntest_ids = ["a", "a"]', "duplicates"),
        ('schema_version = 1\ncli_id = "audit-tool"\n[[cases]]\nid = "x"\nunknown = 1', "unknown field"),
        ('schema_version = 1\ncli_id = "audit-tool"\n[[cases]]\nid = "x"\nstate = "active"', "missing"),
        ('schema_version = 1\ncli_id = "audit-tool"\n[[cases]]\nid = "x"\nstate = "retired"', "retirement_reason"),
        ('schema_version = 1\ncli_id = "audit-tool"\nmax_candidates = 0', "max_candidates"),
        ('schema_version = 1\ncli_id = "audit-tool"\n[[interaction_groups]]\nid = "x"\nroute_id = "r"', "combinations"),
    )
    for content, message in invalid_documents:
        path.write_text(content, encoding="utf-8")
        with pytest.raises(ReviewCatalogError, match=message):
            load_cli_review_catalog(path)

    path.write_text(
        'schema_version = 1\ncli_id = "audit-tool"\nmax_candidates = 1\n',
        encoding="utf-8",
    )
    assert load_cli_review_catalog(path).max_candidates == 1

    active_cases_missing_fields = (
        (
            'schema_version = 1\ncli_id = "audit-tool"\n'
            '[[cases]]\nid = "missing-invocation"\nstate = "active"\n'
            'decision = "accept"\nreviewed_signature = "sha256:x"\n'
            'rationale = "reviewed"\nexpected_exit_status = 0\n'
            'effects = []\ntest_ids = ["test.py::test_case"]',
            "invocation",
        ),
        (
            'schema_version = 1\ncli_id = "audit-tool"\n'
            '[[cases]]\nid = "missing-effects"\nstate = "active"\n'
            'decision = "accept"\nreviewed_signature = "sha256:x"\n'
            'rationale = "reviewed"\ninvocation = []\nexpected_exit_status = 0\n'
            'test_ids = ["test.py::test_case"]',
            "effects",
        ),
    )
    for content, field in active_cases_missing_fields:
        path.write_text(content, encoding="utf-8")
        with pytest.raises(ReviewCatalogError, match=field):
            load_cli_review_catalog(path)

    path.write_text("not = [toml", encoding="utf-8")
    with pytest.raises(ReviewCatalogError, match="cannot read"):
        load_cli_review_catalog(path)
    with pytest.raises(ReviewCatalogError, match="cannot read"):
        load_cli_review_catalog(tmp_path / "missing.toml")


def test_catalog_rejects_invalid_interactions_duplicates_and_retired_missing_reason(tmp_path):
    path = tmp_path / "catalog.toml"
    documents = (
        (
            'schema_version = 1\ncli_id = "audit-tool"\n[[interaction_groups]]\nid = "g"\nroute_id = "r"\nwrong = 1',
            "unknown field",
        ),
        (
            'schema_version = 1\ncli_id = "audit-tool"\n[[interaction_groups]]\nid = "g"\nroute_id = "r"\n[[interaction_groups.combinations]]\nid = "c"\noption_ids = []',
            "must not be empty",
        ),
        (
            'schema_version = 1\ncli_id = "audit-tool"\n[[interaction_groups]]\nid = "g"\nroute_id = "r"\n[[interaction_groups.combinations]]\nid = "c"\noption_ids = ["x", "x"]',
            "duplicates",
        ),
        (
            'schema_version = 1\ncli_id = "audit-tool"\n[[cases]]\nid = "x"\nstate = "retired"\nretirement_reason = ""',
            "retirement_reason",
        ),
    )
    for content, message in documents:
        path.write_text(content, encoding="utf-8")
        with pytest.raises(ReviewCatalogError, match=message):
            load_cli_review_catalog(path)

    path.write_text(
        'schema_version = 1\ncli_id = "audit-tool"\n[[interaction_groups]]\nid = "g"\nroute_id = "r"\n[[interaction_groups.combinations]]\nid = "c"\noption_ids = ["x"]\n[[interaction_groups]]\nid = "g"\nroute_id = "r"\n[[interaction_groups.combinations]]\nid = "d"\noption_ids = ["y"]',
        encoding="utf-8",
    )
    with pytest.raises(ReviewCatalogError, match="duplicate interaction group"):
        load_cli_review_catalog(path)


def test_catalog_interaction_parser_rejects_unrepresentable_shapes():
    assert _parse_interaction_groups(None) == ()
    invalid = (
        ("bad", "array of tables"),
        (["bad"], "must be a table"),
        ([{"id": "g", "route_id": "r", "combinations": "bad"}], "combinations must be an array"),
        ([{"id": "g", "route_id": "r", "combinations": ["bad"]}], "must be a table"),
        ([{"id": "g/x", "route_id": "r", "combinations": [{"id": "c", "option_ids": ["x"]}]}], "must not contain '/'"),
        ([{"id": "g", "route_id": "r", "combinations": [{"id": "c", "option_ids": ["x"]}, {"id": "c", "option_ids": ["y"]}]}], "duplicate interaction combination"),
    )
    for value, message in invalid:
        with pytest.raises(ReviewCatalogError, match=message):
            _parse_interaction_groups(value)


def test_interaction_requires_each_local_option_with_a_valid_value_shape():
    app = _build_cli()
    surface = export_cli_surface(app, interaction_groups=INTERACTIONS)
    candidate = next(
        item for item in surface["candidates"] if item["kind"] == "interaction"
    )
    invocation = _candidate_argv(candidate, surface)

    def findings(argv):
        case = _case_for_candidate(candidate, argv)
        return _review_findings(
            {**surface, "candidates": [candidate]},
            ReviewCatalog("audit-tool", 128, (), (case,)),
        )

    assert findings(invocation) == []

    without_mode = list(invocation)
    mode_index = without_mode.index("--mode")
    del without_mode[mode_index : mode_index + 2]
    assert any(
        "does not supply participating option" in finding
        for finding in findings(without_mode)
    )

    without_mode_value = list(invocation)
    mode_index = without_mode_value.index("--mode")
    del without_mode_value[mode_index + 1]
    assert any(
        "valid value shape for participating option" in finding
        for finding in findings(without_mode_value)
    )

    invalid_mode = list(invocation)
    mode_index = invalid_mode.index("--mode")
    invalid_mode[mode_index + 1] = "unexpected"
    assert any(
        "valid value shape for participating option" in finding
        for finding in findings(invalid_mode)
    )

    inline_flag = list(invocation)
    dry_run_index = inline_flag.index("--dry-run")
    inline_flag[dry_run_index] = "--dry-run=true"
    assert any(
        "inline value to flag-only option" in finding
        for finding in findings(inline_flag)
    )


def test_cross_route_interaction_requires_the_foreign_option_and_valid_target_arguments():
    identity = CliIdentity("MONITOR", "1.0", "Task Monitor", command="monitor-task")
    registry = CliRegistry(identity, prog="monitor-task", description="Monitor tasks.")
    registry.register(
        VerbSpec(
            "show",
            description="fetch one task snapshot",
            arguments=(
                ArgumentSpec(
                    "task_uuid",
                    "task UUID",
                    parser_kwargs={"choices": ("TASK-UUID",)},
                ),
            ),
            handler=lambda *_: 0,
        )
    )
    registry.register(
        VerbSpec(
            "watch",
            description="poll until the task is terminal",
            arguments=(ArgumentSpec("task_uuid", "task UUID"),),
            options=(
                OptionSpec(
                    ("--poll",),
                    "poll interval",
                    parser_kwargs={"type": int, "choices": (5, 10)},
                ),
                OptionSpec(
                    ("--pair",),
                    "two-part window",
                    parser_kwargs={"nargs": 2, "choices": ("from", "to")},
                ),
                OptionSpec(
                    ("--dry-run",),
                    "preview only",
                    parser_kwargs={"action": "store_true"},
                ),
            ),
            handler=lambda *_: 0,
        )
    )
    app = registry.build()
    base_surface = export_cli_surface(app)
    routes = {tuple(route["path"]): route for route in base_surface["routes"]}
    show_route = routes[("show",)]
    watch_route = routes[("watch",)]
    poll_option = next(
        action for action in watch_route["actions"] if action.get("flags") == ["--poll"]
    )
    dry_run_option = next(
        action
        for action in watch_route["actions"]
        if action.get("flags") == ["--dry-run"]
    )
    pair_option = next(
        action for action in watch_route["actions"] if action.get("flags") == ["--pair"]
    )
    interaction = {
        "id": "watch-option-on-show",
        "route_id": show_route["id"],
        "option_ids": (poll_option["id"], dry_run_option["id"], pair_option["id"]),
    }
    surface = export_cli_surface(app, interaction_groups=(interaction,))
    candidate = next(
        candidate
        for candidate in surface["candidates"]
        if candidate["kind"] == "interaction"
    )
    assert candidate["shape"]["external_options"] == [
        {
            "id": dry_run_option["id"],
            "route_id": watch_route["id"],
            "path": ["watch"],
            "flags": ["--dry-run"],
            "nargs": 0,
            "minimum_values": 0,
            "choices": None,
            "action": dry_run_option["action"],
        },
        {
            "id": pair_option["id"],
            "route_id": watch_route["id"],
            "path": ["watch"],
            "flags": ["--pair"],
            "nargs": 2,
            "minimum_values": 2,
            "choices": ["from", "to"],
            "action": pair_option["action"],
        },
        {
            "id": poll_option["id"],
            "route_id": watch_route["id"],
            "path": ["watch"],
            "flags": ["--poll"],
            "nargs": None,
            "minimum_values": 1,
            "choices": [5, 10],
            "action": poll_option["action"],
        },
    ]

    def findings(invocation, *, decision="refuse", expected_exit_status=2):
        case = _case_for_candidate(
            candidate,
            invocation,
            decision=decision,
            expected_exit_status=expected_exit_status,
        )
        return _review_findings(
            {
                **surface,
                "candidates": [candidate],
            },
            ReviewCatalog("monitor-task", 8, (), (case,)),
        )

    valid_foreign = (
        "show", "TASK-UUID", "--poll", "5", "--dry-run",
        "--pair", "from", "to",
    )
    assert findings(valid_foreign) == []
    assert app.run(
        argv=list(valid_foreign),
        stderr=io.StringIO(),
    ) == 2
    assert any(
        "undeclared unknown option '--unexpected'" in item
        for item in findings(
            (*valid_foreign, "--unexpected")
        )
    )
    assert any(
        "does not supply the out-of-route option" in finding
        for finding in findings(("show", "TASK-UUID"))
    )
    assert any(
        "does not provide a valid value shape for out-of-route option" in finding
        for finding in findings(("show", "TASK-UUID", "--poll"))
    )
    assert any(
        "inline value to flag-only out-of-route option" in finding
        for finding in findings(
            ("show", "TASK-UUID", "--poll=5", "--dry-run=true")
        )
    )
    assert any(
        "omits required positional argument" in finding
        for finding in findings(
            ("show", "--poll", "5", "--dry-run", "--pair", "from", "to")
        )
    )
    assert any(
        "invalid value for required argument" in finding
        for finding in findings(
            (
                "show", "OTHER-TASK", "--poll", "5", "--dry-run",
                "--pair", "from", "to",
            )
        )
    )
    assert any(
        "valid value shape for out-of-route option" in finding
        for finding in findings(
            (
                "show", "TASK-UUID", "--poll", "7", "--dry-run",
                "--pair", "from", "to",
            )
        )
    )
    assert any(
        "valid value shape for out-of-route option" in finding
        for finding in findings(
            (
                "show", "TASK-UUID", "--poll", "5", "--dry-run",
                "--pair", "from",
            )
        )
    )
    assert any(
        "valid value shape for out-of-route option" in finding
        for finding in findings(
            (
                "show", "TASK-UUID", "--poll", "5", "--dry-run",
                "--pair", "bad", "to",
            )
        )
    )
    assert any(
        "does not provide a valid value shape for out-of-route option" in finding
        for finding in findings(("show", "TASK-UUID", "--poll", "--dry-run"))
    )
    assert any(
        "at the target route's parser depth" in finding
        for finding in findings(("--poll", "5", "show", "TASK-UUID", "--dry-run"))
    )
    assert any(
        "does not supply the out-of-route option" in finding
        for finding in findings(
            ("show", "TASK-UUID", "--", "--poll", "5", "--dry-run")
        )
    )
    unknown_required = {
        **candidate,
        "shape": {
            **candidate["shape"],
            "required_arguments": ["missing-argument"],
            "required_options": ["missing-option"],
        },
    }
    unknown_case = _case_for_candidate(
        unknown_required,
        ("show", "TASK-UUID", "--poll=5"),
        decision="refuse",
        expected_exit_status=2,
    )
    unknown_findings = _review_findings(
        {**surface, "candidates": [unknown_required]},
        ReviewCatalog("monitor-task", 8, (), (unknown_case,)),
    )
    assert any(
        "references unknown required option missing-option" in item
        for item in unknown_findings
    )
    assert any(
        "references unknown required argument missing-argument" in item
        for item in unknown_findings
    )
    unknown_local = {
        **candidate,
        "shape": {
            **candidate["shape"],
            "option_ids": [*candidate["shape"]["option_ids"], "missing-option"],
        },
    }
    unknown_local_case = _case_for_candidate(
        unknown_local,
        valid_foreign,
        decision="refuse",
        expected_exit_status=2,
    )
    unknown_local_findings = _review_findings(
        {**surface, "candidates": [unknown_local]},
        ReviewCatalog("monitor-task", 8, (), (unknown_local_case,)),
    )
    assert any(
        "references unknown option missing-option" in item
        for item in unknown_local_findings
    )

    unknown_external = {
        **candidate,
        "shape": {
            **candidate["shape"],
            "option_ids": [
                "missing-poll" if option_id == poll_option["id"] else option_id
                for option_id in candidate["shape"]["option_ids"]
            ],
            "external_options": [
                {
                    **option,
                    **(
                        {"id": "missing-poll"}
                        if option["id"] == poll_option["id"]
                        else {}
                    ),
                }
                for option in candidate["shape"]["external_options"]
            ],
        },
    }
    unknown_external_case = _case_for_candidate(
        unknown_external,
        valid_foreign,
        decision="refuse",
        expected_exit_status=2,
    )
    unknown_external_findings = _review_findings(
        {**surface, "candidates": [unknown_external]},
        ReviewCatalog("monitor-task", 8, (), (unknown_external_case,)),
    )
    assert any(
        "references unknown out-of-route option missing-poll" in item
        for item in unknown_external_findings
    )


def test_cross_route_interaction_can_document_a_valid_passthrough():
    identity = CliIdentity("MONITOR", "1.0", "Task Monitor", command="monitor-task")
    registry = CliRegistry(identity, prog="monitor-task", description="Monitor tasks.")
    registry.register(
        VerbSpec(
            "exec",
            description="run a program with forwarded arguments",
            arguments=(
                ArgumentSpec("program", "program to run"),
                ArgumentSpec(
                    "arguments",
                    "arguments passed through",
                    parser_kwargs={"nargs": argparse.REMAINDER},
                ),
            ),
            handler=lambda *_: 0,
        )
    )
    registry.register(
        VerbSpec(
            "watch",
            description="poll until the task is terminal",
            options=(OptionSpec(("--poll",), "poll interval"),),
            handler=lambda *_: 0,
        )
    )
    app = registry.build()
    routes = {
        tuple(route["path"]): route for route in export_cli_surface(app)["routes"]
    }
    exec_route = routes[("exec",)]
    watch_route = routes[("watch",)]
    poll_option = next(
        action for action in watch_route["actions"] if action.get("flags") == ["--poll"]
    )
    interaction = {
        "id": "exec-forwards-watch-like-option",
        "route_id": exec_route["id"],
        "option_ids": (poll_option["id"],),
    }
    surface = export_cli_surface(app, interaction_groups=(interaction,))
    candidate = next(
        candidate
        for candidate in surface["candidates"]
        if candidate["kind"] == "interaction"
    )
    invocation = ("exec", "tool-name", "--poll", "5")
    case = _case_for_candidate(
        candidate,
        invocation,
        decision="accept",
        expected_exit_status=0,
    )

    assert _review_findings(
        {**surface, "candidates": [candidate]},
        ReviewCatalog("monitor-task", 8, (), (case,)),
    ) == []
    assert app.run(argv=list(invocation), stderr=io.StringIO()) == 0


def test_interaction_invocation_must_satisfy_target_route_requirements():
    identity = CliIdentity("MONITOR", "1.0", "Task Monitor", command="monitor-task")
    registry = CliRegistry(identity, prog="monitor-task", description="Monitor tasks.")
    registry.register(
        VerbSpec(
            "show",
            description="fetch one task snapshot",
            arguments=(ArgumentSpec("task_uuid", "task UUID"),),
            options=(
                OptionSpec(
                    ("--config",),
                    "configuration file",
                    parser_kwargs={
                        "required": True,
                        "choices": ("file", "inline"),
                    },
                ),
                OptionSpec(
                    ("--from-file",),
                    "read config from file",
                    mutually_exclusive_group="source",
                    mutually_exclusive_required=True,
                    parser_kwargs={"choices": ("PATH",)},
                ),
                OptionSpec(
                    ("--from-inline",),
                    "read inline config",
                    mutually_exclusive_group="source",
                    mutually_exclusive_required=True,
                ),
            ),
            handler=lambda *_: 0,
        )
    )
    registry.register(
        VerbSpec(
            "watch",
            description="poll until the task is terminal",
            options=(OptionSpec(("--poll",), "poll interval"),),
            handler=lambda *_: 0,
        )
    )
    app = registry.build()
    base_surface = export_cli_surface(app)
    routes = {tuple(route["path"]): route for route in base_surface["routes"]}
    show_route = routes[("show",)]
    watch_route = routes[("watch",)]
    poll_option = next(
        action for action in watch_route["actions"] if action.get("flags") == ["--poll"]
    )
    interaction = {
        "id": "watch-option-on-show",
        "route_id": show_route["id"],
        "option_ids": (poll_option["id"],),
    }
    surface = export_cli_surface(app, interaction_groups=(interaction,))
    candidate = next(
        candidate
        for candidate in surface["candidates"]
        if candidate["kind"] == "interaction"
    )

    def findings(invocation):
        case = _case_for_candidate(
            candidate,
            invocation,
            decision="refuse",
            expected_exit_status=2,
        )
        return _review_findings(
            {**surface, "candidates": [candidate]},
            ReviewCatalog("monitor-task", 8, (), (case,)),
        )

    structurally_complete_refusal = (
        "show", "TASK-UUID", "--config", "file", "--from-file", "PATH", "--poll=5"
    )
    assert findings(structurally_complete_refusal) == []
    assert any(
        "omits required option" in finding
        for finding in findings(
            ("show", "TASK-UUID", "--from-file", "PATH", "--poll=5")
        )
    )
    assert any(
        "omits a value for required option" in finding
        for finding in findings(
            ("show", "TASK-UUID", "--config", "--from-file", "PATH", "--poll=5")
        )
    )
    assert any(
        "exactly one option from required group" in finding
        for finding in findings(
            ("show", "TASK-UUID", "--config", "file", "--poll=5")
        )
    )
    assert any(
        "exactly one option from required group" in finding
        for finding in findings(
            (
                "show", "TASK-UUID", "--config", "file", "--from-file", "PATH",
                "--from-inline", "VALUE", "--poll=5",
            )
        )
    )
    assert any(
        "omits a value for required group" in finding
        for finding in findings(
            ("show", "TASK-UUID", "--config", "file", "--from-file", "--poll=5")
        )
    )
    assert any(
        "invalid value for required group" in finding
        for finding in findings(
            (
                "show", "TASK-UUID", "--config", "file", "--from-file", "OTHER",
                "--poll=5",
            )
        )
    )
    assert any(
        "does not provide a valid value shape for out-of-route option" in finding
        for finding in findings(
            (
                "show", "TASK-UUID", "--config", "file", "--from-file", "PATH",
                "--poll", "--from-inline", "VALUE",
            )
        )
    )
    assert any(
        "invalid value for required option" in finding
        for finding in findings(
            (
                "show", "TASK-UUID", "--config", "other", "--from-file", "PATH",
                "--poll=5",
            )
            )
        )

    file_option = next(
        action
        for action in show_route["actions"]
        if action.get("flags") == ["--from-file"]
    )
    inline_option = next(
        action
        for action in show_route["actions"]
        if action.get("flags") == ["--from-inline"]
    )
    conflicting_interaction = {
        "id": "both-source-options",
        "route_id": show_route["id"],
        "option_ids": (file_option["id"], inline_option["id"]),
    }
    conflicting_surface = export_cli_surface(
        app, interaction_groups=(conflicting_interaction,)
    )
    conflicting_candidate = next(
        candidate
        for candidate in conflicting_surface["candidates"]
        if candidate["kind"] == "interaction"
    )
    conflicting_argv = (
        "show",
        "TASK-UUID",
        "--config",
        "file",
        "--from-file",
        "PATH",
        "--from-inline",
        "INLINE",
    )
    conflicting_case = _case_for_candidate(
        conflicting_candidate,
        conflicting_argv,
        decision="refuse",
        expected_exit_status=2,
    )
    conflicting_findings = _review_findings(
        {
            **conflicting_surface,
            "candidates": [conflicting_candidate],
        },
        ReviewCatalog("monitor-task", 8, (), (conflicting_case,)),
    )
    assert conflicting_findings == []
    assert app.run(argv=list(conflicting_argv), stderr=io.StringIO()) == 2


def test_catalog_loader_rejects_unrepresentable_toml_shapes(tmp_path, monkeypatch):
    path = tmp_path / "catalog.toml"
    path.write_text("ignored", encoding="utf-8")
    invalid_roots = (
        ([], "root must be a table"),
        ({"schema_version": 1, "cli_id": "audit-tool", "cases": "bad"}, "cases must be an array"),
        ({"schema_version": 1, "cli_id": "audit-tool", "cases": ["bad"]}, "cases\\[0\\] must be a table"),
        ({"schema_version": 1, "cli_id": "audit-tool", "cases": [{"id": "x"}, {"id": "x"}]}, "duplicate case id"),
        ({"schema_version": 1, "cli_id": "audit-tool", "cases": [{"id": "x", "state": "other"}]}, "state must be pending"),
        ({"schema_version": 1, "cli_id": "audit-tool", "cases": [{"id": "x", "decision": "other"}]}, "decision must be accept or refuse"),
        ({"schema_version": 1, "cli_id": "audit-tool", "cases": [{"id": "x", "retirement_reason": 2}]}, "retirement_reason must be a string"),
    )
    for raw, message in invalid_roots:
        with monkeypatch.context() as patch:
            patch.setattr("cli_extended.review.tomllib.loads", lambda _text, raw=raw: raw)
            with pytest.raises(ReviewCatalogError, match=message):
                load_cli_review_catalog(path)


def test_replace_generated_region_preserves_prefix_newlines_and_rejects_nested_markers(
    monkeypatch,
):
    text = f"prefix\n{SURFACE_START_MARKER}\nold\n{SURFACE_END_MARKER}"
    updated = _replace_generated_region(text, "new generated section\n")
    assert updated.startswith(f"prefix\n{SURFACE_START_MARKER}\n")
    assert "new generated section" in updated
    assert _replace_generated_region(
        f"{SURFACE_START_MARKER}\nold\n{SURFACE_END_MARKER}\n", "new\n"
    ).endswith(f"{SURFACE_END_MARKER}\n")
    assert _replace_generated_region(
        f"{SURFACE_START_MARKER}\nold\n{SURFACE_END_MARKER}\n", ""
    ) == f"{SURFACE_START_MARKER}\n{SURFACE_END_MARKER}\n"
    with pytest.raises(SurfaceSpecError, match="exactly one"):
        _replace_generated_region(
            f"{SURFACE_START_MARKER}\n{SURFACE_START_MARKER}\n{SURFACE_END_MARKER}\n{SURFACE_END_MARKER}",
            "new",
        )
    with pytest.raises(SurfaceSpecError, match="reversed or nested"):
        _replace_generated_region(
            f"{SURFACE_END_MARKER}\nold\n{SURFACE_START_MARKER}\n",
            "new",
        )
    monkeypatch.setattr("cli_extended.review.SURFACE_END_MARKER", SURFACE_START_MARKER)
    with pytest.raises(SurfaceSpecError, match="reversed or nested"):
        _replace_generated_region(f"{SURFACE_START_MARKER}\n", "new")


def test_markdown_cells_render_unicode_json_with_sorted_keys():
    assert _markdown_cell({"z": "last", "a": "café"}) == '{"a": "café", "z": "last"}'


def test_generated_text_writes_are_atomic_preserve_mode_and_clean_up_failures(
    tmp_path, monkeypatch
):
    import stat

    import cli_extended.review as review_module

    target = tmp_path / "SPEC.md"
    target.write_bytes(b"old\r\ntext\r\n")
    target.chmod(0o640)
    review_module._write_text_preserving_newlines(target, "new\r\ntext\r\n")
    assert target.read_bytes() == b"new\r\ntext\r\n"
    assert stat.S_IMODE(target.stat().st_mode) == 0o640
    assert list(tmp_path.glob(".SPEC.md.*")) == []

    original = target.read_bytes()

    def fail_replace(_temporary, _destination):
        raise OSError("replace refused")

    monkeypatch.setattr(review_module.os, "replace", fail_replace)
    with pytest.raises(OSError, match="replace refused"):
        review_module._write_text_preserving_newlines(target, "partial\n")
    assert target.read_bytes() == original
    assert list(tmp_path.glob(".SPEC.md.*")) == []


def test_generated_text_write_leaves_target_unchanged_if_temp_creation_fails(
    tmp_path, monkeypatch
):
    import cli_extended.review as review_module

    target = tmp_path / "cli-surface.json"
    target.write_text("old\n", encoding="utf-8")
    original = target.read_bytes()

    def fail_mkstemp(**_kwargs):
        raise OSError("temp creation refused")

    monkeypatch.setattr(review_module.tempfile, "mkstemp", fail_mkstemp)
    with pytest.raises(OSError, match="temp creation refused"):
        review_module._atomic_write_text(target, "partial\n", newline="")
    assert target.read_bytes() == original
    assert list(tmp_path.iterdir()) == [target]


def test_surface_markdown_exposes_nested_routes_stale_rows_and_incomplete_inventory():
    candidate = {
        "id": "case:inspect/run",
        "route_id": "route:inspect",
        "signature": "sha256:new",
        "kind": "minimum",
        "members": ["option:parent", "argument:child"],
        "shape": {"values": [1, 2]},
    }
    pending_candidate = {
        "id": "case:pending",
        "route_id": "route:entrypoint:audit-tool",
        "signature": "sha256:pending",
        "kind": "minimum",
        "members": [],
        "shape": {},
    }
    retired = ReviewCase(
        case_id=candidate["id"],
        state="retired",
        decision="refuse",
        reviewed_signature="sha256:old",
        rationale="old route",
        invocation=(),
        invocation_declared=True,
        expected_exit_status=2,
        expected_stdout_contains="",
        expected_stderr_contains="",
        effects=(),
        effects_declared=True,
        test_ids=("tests/test_old.py::test_old",),
        retirement_reason="route removed",
    )
    stale_active = ReviewCase(
        case_id="case:removed",
        state="active",
        decision="accept",
        reviewed_signature="sha256:gone",
        rationale="old action",
        invocation=(),
        invocation_declared=True,
        expected_exit_status=0,
        expected_stdout_contains="",
        expected_stderr_contains="",
        effects=(),
        effects_declared=True,
        test_ids=(),
        retirement_reason="",
    )
    stale_retired = ReviewCase(
        case_id="case:retired-removed",
        state="retired",
        decision="refuse",
        reviewed_signature="sha256:old",
        rationale="removed route",
        invocation=(),
        invocation_declared=True,
        expected_exit_status=2,
        expected_stdout_contains="",
        expected_stderr_contains="",
        effects=(),
        effects_declared=True,
        test_ids=("tests/test_old.py::test_retired",),
        retirement_reason="route removed",
    )
    pending = ReviewCase(
        case_id=pending_candidate["id"],
        state="pending",
        decision=None,
        reviewed_signature=None,
        rationale="",
        invocation=(),
        invocation_declared=True,
        expected_exit_status=None,
        expected_stdout_contains="",
        expected_stderr_contains="",
        effects=(),
        effects_declared=True,
        test_ids=(),
        retirement_reason="",
    )
    surface = {
        "schema_version": 1,
        "library_contract": {"name": "cli-extended", "version": 1},
        "entrypoint": {
            "command": "audit-tool",
            "prog": "audit-tool",
            "builtins": [],
            "single_command": True,
            "no_args_action": True,
        },
        "routes": [
            {
                "id": "route:inspect",
                "path": ["inspect", "run"],
                "kind": "route-prefix",
                "single_command": True,
                "no_args_action": True,
                "aliases": ["r"],
                "subcommand_groups": [
                    {"destination": "operation", "required": True, "subcommands": ["route:inspect/run"]},
                    {"destination": "optional-operation", "required": False, "subcommands": ["route:inspect/check"]},
                ],
                "description": "Run an operation.",
                "summary": "run operation [mutating]",
                "group": "CHANGE",
                "behavior": ["mutating"],
                "confirmation": True,
                "synopsis_override": "inspect <RESOURCE>",
                "usage_override": "audit-tool inspect [options] RESOURCE",
                "parser_configured_by_callback": True,
                "opaque_fields": ["option:parent.type"],
                "delegated_metadata": [
                    {
                        "id": "inner-run",
                        "name": "run",
                        "description": "run an operation",
                        "summary": "run an operation [mutating]",
                        "group": "CHANGE",
                        "behavior": ["mutating"],
                        "confirmation": False,
                        "synopsis": "run <RESOURCE>",
                    }
                ],
                "syntax_complete": False,
                "parser_settings": [
                    {
                        "parser_path": [],
                        "allow_abbrev": False,
                        "prefix_chars": "-",
                        "fromfile_prefix_chars": None,
                        "negative_number_matcher": {"pattern": r"-?\d+", "flags": 32},
                        "has_negative_number_optionals": False,
                        "negative_number_matcher_custom": True,
                    }
                ],
                "actions": [
                    {
                        "id": "option:parent",
                        "kind": "option",
                        "flags": ["--parent"],
                        "nargs": None,
                        "metavar": "VALUE",
                        "required": True,
                        "choices": ["a", "b"],
                        "effective_default": None,
                        "exclusive_group": None,
                        "exclusive_required": True,
                        "scope": "common",
                        "placement": {"before_verb": True, "after_verb": False},
                        "parser_path": ["inspect"],
                        "before_nested_subcommand": True,
                        "help_group": "OPTIONS",
                        "description": "choose a source",
                        "action": "argparse._StoreAction",
                        "type": {"callable": "pathlib.Path"},
                        "const": None,
                        "default": None,
                        "hidden": False,
                    },
                    {
                        "id": "option:run-flag",
                        "kind": "option",
                        "flags": ["--flag"],
                        "nargs": 0,
                        "metavar": None,
                        "required": False,
                        "choices": None,
                        "effective_default": False,
                        "exclusive_group": None,
                        "exclusive_required": False,
                        "scope": "verb-local",
                        "placement": {"before_verb": False, "after_verb": True},
                        "parser_path": ["inspect"],
                        "before_nested_subcommand": True,
                        "help_group": "OPTIONS",
                        "description": "set the mode",
                        "action": "argparse._StoreTrueAction",
                        "type": None,
                        "const": True,
                        "default": False,
                        "hidden": True,
                    },
                    {
                        "id": "argument:child",
                        "kind": "argument",
                        "name": "resource",
                        "nargs": 2,
                        "metavar": "RESOURCE",
                        "required": True,
                        "choices": None,
                        "effective_default": None,
                        "exclusive_group": None,
                        "scope": "positional",
                        "description": "resource to inspect",
                        "type": {"callable": "builtins.int"},
                        "hidden": False,
                    },
                ],
            },
            {
                "id": "route:entrypoint:audit-tool",
                "path": [],
                "kind": "invocation",
                "single_command": True,
                "no_args_action": True,
                "confirmation": False,
                "parser_configured_by_callback": False,
                "aliases": [],
                "subcommand_groups": [],
                "description": None,
                "group": None,
                "behavior": [],
                "syntax_complete": True,
                "parser_settings": [
                    {
                        "parser_path": [],
                        "allow_abbrev": False,
                        "prefix_chars": "-",
                        "fromfile_prefix_chars": None,
                    }
                ],
                "actions": [],
            },
            {
                "id": "route:entrypoint:audit-tool/adapter",
                "path": ["adapter"],
                "kind": "invocation",
                "single_command": True,
                "no_args_action": False,
                "confirmation": False,
                "parser_configured_by_callback": False,
                "aliases": [],
                "subcommand_groups": [],
                "description": "A delegated single-command adapter.",
                "group": None,
                "behavior": [],
                "syntax_complete": True,
                "parser_settings": [],
                "actions": [],
            },
        ],
        "candidates": [candidate, pending_candidate],
        "syntax_complete": False,
        "incomplete": ["uninspectable callback field | reason"],
    }
    catalog = ReviewCatalog(
        "audit-tool", 8, (), (retired, stale_active, stale_retired, pending)
    )
    markdown = render_cli_surface_markdown(surface, catalog)

    assert "route-prefix" in markdown
    assert "operation: route:inspect/run (required)" in markdown
    assert "optional-operation: route:inspect/check" in markdown
    assert '"before_nested_subcommand": true' in markdown
    assert "REVIEW REQUIRED: retired ID is active again" in markdown
    assert "STALE: disposition required" in markdown
    assert "Surface inventory is incomplete" in markdown
    assert "uninspectable callback field \\| reason" in markdown
    assert "inspect run (aliases: r)" in markdown
    assert "Run an operation." in markdown
    assert "run operation [mutating]" in markdown
    assert "CHANGE" in markdown
    assert "Confirmation" in markdown and "inspect <RESOURCE>" in markdown
    assert "audit-tool inspect [options] RESOURCE" in markdown
    assert "run an operation [mutating]" in markdown
    assert "Parser callback" in markdown and "Opaque fields" in markdown
    assert (
        "Empty argv: is parsed by the single-command parser; "
        "required syntax may still reject it."
        in markdown
    )
    assert "entrypoint; see empty-argv behavior above" in markdown
    assert "route prefix; selects a nested command" in markdown
    assert "single-command; empty remainder shows help before parsing" in markdown
    assert "multi-command; empty argv shows help" not in markdown
    assert "built-ins: none" in markdown
    assert "parser settings" in markdown.lower()
    assert "<entrypoint>: allow_abbrev=no," in markdown
    assert "negative_number_matcher_custom=yes" in markdown
    assert "VALUE" in markdown and '"nargs": null' not in markdown
    assert "--flag" in markdown and '"nargs": 0' in markdown
    assert '"nargs": 2' in markdown
    assert '"parser_path": ["inspect"]' in markdown
    assert "argparse._StoreAction" in markdown
    assert "argparse._StoreTrueAction" in markdown
    assert '"callable": "pathlib.Path"' in markdown
    assert '"callable": "builtins.int"' in markdown
    assert '"exclusive_required": true' in markdown
    assert '"hidden": true' in markdown
    assert "choose a source" in markdown and "resource to inspect" in markdown
    assert '"nargs": 2' in markdown
    assert "refuse" in markdown
    assert "route removed" in markdown
    assert "old action" in markdown
    assert "route removed" in markdown
    assert _case_status(candidate, catalog.cases_by_id)[0].startswith("REVIEW REQUIRED")
    retired_row = next(line for line in markdown.splitlines() if line.startswith("| case:inspect/run |"))
    assert "route removed" in retired_row
    assert "old route" not in retired_row
    pending_row = next(line for line in markdown.splitlines() if line.startswith("| case:pending |"))
    assert "| PENDING |" in pending_row
    assert "null" not in pending_row
    root_row = next(line for line in markdown.splitlines() if line.startswith("| route:entrypoint:audit-tool |"))
    assert "audit-tool" in root_row and "<entrypoint>" in root_row
    assert "null" not in root_row

    missing_completeness = dict(surface)
    missing_completeness.pop("syntax_complete")
    assert "Surface inventory is incomplete" in render_cli_surface_markdown(
        missing_completeness, catalog
    )


def test_surface_markdown_lists_delegated_group_children():
    group_id = "route:entrypoint:audit-tool/plugins"
    child_id = f"{group_id}/inspect"
    surface = {
        "schema_version": 7,
        "library_contract": {"name": "cli-extended", "version": 1},
        "entrypoint": {
            "command": "audit-tool",
            "prog": "audit-tool",
            "single_command": False,
            "no_args_action": False,
            "builtins": ["help", "help <verb>", "version", "--help", "--version"],
        },
        "routes": [
            {
                "id": group_id,
                "path": ["plugins"],
                "kind": "delegate-group",
                "single_command": False,
                "no_args_action": False,
                "confirmation": False,
                "parser_configured_by_callback": False,
                "subcommands": [child_id],
                "actions": [],
                "syntax_complete": True,
            },
            {
                "id": child_id,
                "path": ["plugins", "inspect"],
                "kind": "invocation",
                "single_command": False,
                "no_args_action": False,
                "confirmation": False,
                "parser_configured_by_callback": False,
                "subcommands": [],
                "actions": [],
                "syntax_complete": True,
            },
        ],
        "candidates": [],
        "syntax_complete": True,
    }

    markdown = render_cli_surface_markdown(
        surface, ReviewCatalog("audit-tool", 8, (), ())
    )
    group_row = next(
        line for line in markdown.splitlines() if line.startswith(f"| {group_id} |")
    )
    assert f"delegated: {child_id}" in group_row


def test_case_status_and_template_cover_signature_and_retirement_lifecycle(tmp_path):
    app, surface, review, manifest, spec = _make_files(tmp_path, active=True)
    candidates = surface["candidates"]
    candidate = candidates[0]
    active = load_cli_review_catalog(review)
    assert _case_status(candidate, active.cases_by_id)[0] in {"ACCEPT", "REFUSE"}
    assert _case_status(candidate, {}) == ("UNREVIEWED", None)

    retired = _case_for_candidate(
        candidate, (), state="retired", retirement_reason="old",
    )
    changed = _case_for_candidate(candidate, (), signature="sha256:old")
    pending = _case_for_candidate(candidate, (), state="pending")
    assert _case_status(candidate, {candidate["id"]: pending})[0] == "PENDING"
    retired_template = render_cli_review_template(
        {"candidates": [candidate]}, ReviewCatalog("audit-tool", 128, (), (retired,))
    )
    changed_template = render_cli_review_template(
        {"candidates": [candidate]}, ReviewCatalog("audit-tool", 128, (), (changed,))
    )
    assert "retired case is active again" in retired_template
    assert "Current generated signature" in changed_template
    unicode_candidate = {**candidate, "id": "case:inspect/café"}
    unicode_template = render_cli_review_template(
        {"candidates": [unicode_candidate]}, ReviewCatalog("audit-tool", 8, (), ())
    )
    assert 'id = "case:inspect/café"' in unicode_template
    assert "\\u00e9" not in unicode_template
    assert app is not None and review.exists()


def test_review_findings_reports_missing_decisions_stale_records_and_incomplete_syntax():
    route = {
        "id": "route:entrypoint:audit-tool/inspect",
        "path": ["inspect"],
        "aliases": [],
        "kind": "invocation",
        "actions": [],
    }
    candidates = [
        {"id": "case:missing", "route_id": route["id"], "signature": "s", "kind": "minimum", "members": [], "shape": {}},
        {"id": "case:pending", "route_id": route["id"], "signature": "s", "kind": "minimum", "members": [], "shape": {}},
        {"id": "case:retired", "route_id": route["id"], "signature": "s", "kind": "minimum", "members": [], "shape": {}},
        {"id": "case:bad", "route_id": "missing-route", "signature": "new", "kind": "minimum", "members": [], "shape": {"required_arguments": ["arg"]}},
    ]
    cases = (
        _case_for_candidate(candidates[1], ["inspect"], state="pending"),
        _case_for_candidate(candidates[2], ["inspect"], state="retired", retirement_reason="removed"),
        _case_for_candidate(
            candidates[3], (), signature="old", invocation_declared=False,
            effects_declared=False, test_ids=(),
        ),
        ReviewCase(
            "case:stale", "active", "accept", "old", "stale", (), True, 0,
            "", "", (), True, ("tests/test_old.py::test_old",), "",
        ),
    )
    surface = {
        "candidates": candidates,
        "routes": [route],
        "syntax_complete": False,
        "incomplete": ["custom syntax"],
    }
    findings = _review_findings(surface, ReviewCatalog("audit-tool", 8, (), cases))

    assert any("missing semantic review case: case:missing" in item for item in findings)
    assert any("semantic review is pending: case:pending" in item for item in findings)
    assert any("retired semantic case is active again: case:retired" in item for item in findings)
    assert any("signature changed: case:bad" in item for item in findings)
    assert any("no test IDs: case:bad" in item for item in findings)
    assert any("no invocation field: case:bad" in item for item in findings)
    assert any("no effects field: case:bad" in item for item in findings)
    assert any("no concrete invocation: case:bad" in item for item in findings)
    assert any("unknown route: case:bad" in item for item in findings)
    assert any("stale semantic case needs explicit retirement: case:stale" in item for item in findings)
    assert "incomplete parser syntax: custom syntax" in findings


def test_empty_single_command_invocation_is_valid_when_no_argument_is_required():
    route = {
        "id": "route:entrypoint:audit-tool",
        "path": [],
        "aliases": [],
        "kind": "invocation",
        "actions": [],
        "parser_settings": [],
    }
    candidate = {
        "id": "case:minimum",
        "route_id": route["id"],
        "signature": "sha256:current",
        "kind": "minimum",
        "members": [],
        "shape": {"required_arguments": []},
    }
    case = _case_for_candidate(candidate, [])
    findings = _review_findings(
        {
            "entrypoint": {"command": "audit-tool", "allow_abbrev": False},
            "routes": [route],
            "candidates": [candidate],
            "syntax_complete": True,
        },
        ReviewCatalog("audit-tool", 8, (), (case,)),
    )
    assert findings == []


def test_surface_markdown_rejects_unknown_action_kinds():
    surface = {
        "schema_version": 1,
        "library_contract": {"name": "cli-extended", "version": 1},
        "entrypoint": {
            "command": "audit-tool",
            "prog": "audit-tool",
            "builtins": [],
            "single_command": False,
            "no_args_action": False,
        },
        "routes": [
            {
                "id": "route:entrypoint:audit-tool",
                "path": [],
                "kind": "invocation",
                "single_command": False,
                "no_args_action": False,
                "confirmation": False,
                "parser_configured_by_callback": False,
                "actions": [
                    {"id": "custom:unknown", "kind": "custom", "name": "custom"}
                ],
            }
        ],
        "candidates": [],
    }

    with pytest.raises(SurfaceSpecError, match="unsupported surface action kind 'custom'"):
        render_cli_surface_markdown(surface, ReviewCatalog("audit-tool", 8, (), ()))


def test_review_findings_checks_minimum_alias_positional_and_option_semantics():
    route_id = "route:entrypoint:audit-tool/inspect"
    actions = [
        {"id": "arg:resource", "kind": "argument", "name": "resource", "nargs": None, "minimum_values": 1, "required": True, "choices": ["alpha"], "parser_path": ["inspect"]},
        {"id": "opt:needed", "kind": "option", "flags": ["--needed"], "nargs": None, "minimum_values": 1, "required": True, "parser_path": ["inspect"]},
        {"id": "opt:file", "kind": "option", "flags": ["--file"], "nargs": None, "minimum_values": 1, "required": False, "exclusive_group": "source", "exclusive_required": True, "parser_path": ["inspect"]},
        {"id": "opt:inline", "kind": "option", "flags": ["--inline"], "nargs": None, "minimum_values": 1, "required": False, "exclusive_group": "source", "exclusive_required": True, "parser_path": ["inspect"]},
        {"id": "opt:mode", "kind": "option", "flags": ["--mode"], "nargs": None, "minimum_values": 1, "required": False, "choices": ["safe", "fast"], "parser_path": ["inspect"]},
    ]
    route = {"id": route_id, "path": ["inspect"], "aliases": [], "kind": "invocation", "actions": actions}
    good_minimum = {"id": "case:minimum", "route_id": route_id, "signature": "s", "kind": "minimum", "members": ["arg:resource", "opt:needed", "opt:file"], "shape": {"required_arguments": ["arg:resource"], "required_argument_values": {"arg:resource": 1}, "required_options": ["opt:needed"], "required_exclusive_groups": {"source": ["opt:file", "opt:inline"]}}}
    alias = {"id": "case:alias", "route_id": route_id, "signature": "s", "kind": "route-alias", "members": [], "shape": {"alias": "i"}}
    argument_choice = {"id": "case:argument-choice", "route_id": route_id, "signature": "s", "kind": "argument-choice", "members": ["arg:resource"], "shape": {"choice": "alpha"}}
    argument_shape = {"id": "case:argument-shape", "route_id": route_id, "signature": "s", "kind": "argument-shape", "members": ["arg:resource"], "shape": {}}
    missing_spelling = {"id": "case:missing-spelling", "route_id": route_id, "signature": "s", "kind": "option-spelling", "members": ["opt:needed"], "shape": {"spelling": "--needed"}}
    missing_value = {"id": "case:missing-value", "route_id": route_id, "signature": "s", "kind": "option-spelling", "members": ["opt:needed"], "shape": {"spelling": "--needed"}}
    wrong_choice = {"id": "case:wrong-choice", "route_id": route_id, "signature": "s", "kind": "option-choice", "members": ["opt:mode"], "shape": {"choice": "fast"}}
    candidates = [good_minimum, alias, argument_choice, argument_shape, missing_spelling, missing_value, wrong_choice]
    invocations = {
        "case:minimum": ["inspect", "alpha", "--needed", "token", "--file", "profile.toml"],
        "case:alias": ["inspect"],
        "case:argument-choice": ["inspect", "wrong"],
        "case:argument-shape": ["inspect"],
        "case:missing-spelling": ["inspect"],
        "case:missing-value": ["inspect", "--needed"],
        "case:wrong-choice": ["inspect", "--mode", "safe"],
    }
    cases = tuple(_case_for_candidate(candidate, invocations[candidate["id"]]) for candidate in candidates)
    findings = _review_findings(
        {"routes": [route], "candidates": candidates, "syntax_complete": True},
        ReviewCatalog("audit-tool", 20, (), cases),
    )

    assert any("omits its command alias" in item for item in findings)
    assert any("omits its positional choice" in item for item in findings)
    assert any("omits its positional value" in item for item in findings)
    assert any("omits its reviewed option spelling" in item for item in findings)
    assert any("omits a value for --needed" in item for item in findings)
    assert any("does not supply its reviewed option choice" in item for item in findings)


def test_render_route_invocation_modes_describe_the_parser_receiving_argv():
    routes = [
        {
            "id": "route:entrypoint:audit-tool",
            "path": [],
            "kind": "invocation",
            "single_command": True,
            "no_args_action": True,
            "confirmation": False,
            "parser_configured_by_callback": False,
            "actions": [],
        },
        {
            "id": "route:entrypoint:audit-tool/adapter",
            "path": ["adapter"],
            "kind": "invocation",
            "single_command": True,
            "no_args_action": False,
            "confirmation": False,
            "parser_configured_by_callback": False,
            "actions": [],
        },
        {
            "id": "route:entrypoint:audit-tool/single",
            "path": ["single"],
            "kind": "invocation",
            "single_command": True,
            "no_args_action": True,
            "confirmation": False,
            "parser_configured_by_callback": False,
            "actions": [],
        },
        {
            "id": "route:entrypoint:audit-tool/plugins",
            "path": ["plugins"],
            "kind": "delegate-group",
            "single_command": False,
            "no_args_action": False,
            "confirmation": False,
            "parser_configured_by_callback": False,
            "actions": [],
        },
        {
            "id": "route:entrypoint:audit-tool/inspect",
            "path": ["inspect"],
            "kind": "route-prefix",
            "single_command": False,
            "no_args_action": False,
            "confirmation": False,
            "parser_configured_by_callback": False,
            "actions": [],
        },
        {
            "id": "route:entrypoint:audit-tool/status",
            "path": ["status"],
            "kind": "invocation",
            "single_command": False,
            "no_args_action": False,
            "confirmation": False,
            "parser_configured_by_callback": False,
            "actions": [],
        },
    ]
    surface = {
        "schema_version": 7,
        "library_contract": {"name": "cli-extended", "version": 1},
        "entrypoint": {
            "command": "audit-tool",
            "prog": "audit-tool",
            "builtins": [],
            "single_command": True,
            "no_args_action": True,
        },
        "routes": routes,
        "candidates": [],
    }
    markdown = render_cli_surface_markdown(
        surface, ReviewCatalog("audit-tool", 8, (), ())
    )

    assert (
        "Empty argv: is parsed by the single-command parser; "
        "required syntax may still reject it."
        in markdown
    )
    assert "entrypoint; see empty-argv behavior above" in markdown
    assert "single-command; empty remainder is parsed" in markdown
    assert "single-command; empty remainder shows help before parsing" in markdown
    assert "delegated group; child CLI parses remaining tokens" in markdown
    assert "route prefix; selects a nested command" in markdown

    empty_single_command = {
        **surface,
        "entrypoint": {
            **surface["entrypoint"],
            "no_args_action": False,
        },
        "routes": [
            {
                **routes[0],
                "no_args_action": False,
            }
        ],
    }
    empty_single_markdown = render_cli_surface_markdown(
        empty_single_command, ReviewCatalog("audit-tool", 8, (), ())
    )
    assert (
        "entrypoint; see empty-argv behavior above; "
        "single-command; empty remainder shows help before parsing"
        in empty_single_markdown
    )
    assert "command route; parser handles remaining tokens" in markdown
    assert "multi-command; empty argv shows help" not in markdown

    help_before_parse_surface = {
        **surface,
        "entrypoint": {
            **surface["entrypoint"],
            "no_args_action": False,
        },
    }
    assert "Empty argv: shows help." in render_cli_surface_markdown(
        help_before_parse_surface, ReviewCatalog("audit-tool", 8, (), ())
    )


def test_review_lexer_handles_negative_values_and_missing_option_values_at_end():
    route_id = "route:entrypoint:audit-tool/inspect"
    route = {
        "id": route_id,
        "path": ["inspect"],
        "aliases": [],
        "kind": "invocation",
        "parser_settings": [
            {
                "parser_path": ["inspect"],
                "allow_abbrev": False,
                "prefix_chars": "-",
                "fromfile_prefix_chars": "",
                **_argparse_negative_number_settings(
                    parser_path=("inspect",)
                )[1],
            }
        ],
        "actions": [
            {
                "id": "option:count",
                "kind": "option",
                "flags": ["--count"],
                "nargs": None,
                "minimum_values": 1,
                "required": False,
                "parser_path": ["inspect"],
            }
        ],
    }
    candidate = {
        "id": "case:count",
        "route_id": route_id,
        "signature": "sha256:count",
        "kind": "option-spelling",
        "members": ["option:count"],
        "shape": {"spelling": "--count"},
    }
    catalog = ReviewCatalog(
        "audit-tool",
        8,
        (),
        (_case_for_candidate(candidate, ["inspect", "--count", "-1"]),),
    )
    surface = {
        "entrypoint": {"allow_abbrev": False},
        "routes": [route],
        "candidates": [candidate],
        "syntax_complete": True,
    }
    assert _review_findings(surface, catalog) == []

    negative_option = {
        **route["parser_settings"][0],
        "has_negative_number_optionals": True,
    }
    negative_option_surface = {
        **surface,
        "routes": [{**route, "parser_settings": [negative_option]}],
    }
    assert any(
        "omits a value for --count" in finding
        for finding in _review_findings(negative_option_surface, catalog)
    )

    missing_value = ReviewCatalog(
        "audit-tool",
        8,
        (),
        (_case_for_candidate(candidate, ["inspect", "--count"]),),
    )
    findings = _review_findings(surface, missing_value)
    assert any("omits a value for --count" in item for item in findings)


@pytest.mark.parametrize("negative_option", (False, True))
@pytest.mark.parametrize("token", ("-1", "-3.5", "-", "-word", "value"))
def test_review_value_boundaries_match_runtime_argparse(negative_option, token):
    parser, parser_settings = _argparse_negative_number_settings(
        parser_path=("inspect",), negative_option=negative_option
    )
    route_id = "route:entrypoint:audit-tool/inspect"
    route = {
        "id": route_id,
        "path": ["inspect"],
        "aliases": [],
        "kind": "invocation",
        "parser_settings": [
            {
                **parser_settings,
                "allow_abbrev": False,
                "prefix_chars": "-",
                "fromfile_prefix_chars": None,
            }
        ],
        "actions": [
            {
                "id": "option:count",
                "kind": "option",
                "flags": ["--count"],
                "nargs": None,
                "minimum_values": 1,
                "parser_path": ["inspect"],
            }
        ],
    }
    candidate = {
        "id": "case:count",
        "route_id": route_id,
        "signature": "sha256:count",
        "kind": "option-spelling",
        "members": ["option:count"],
        "shape": {"spelling": "--count"},
    }
    case = _case_for_candidate(candidate, ["inspect", "--count", token])
    findings = _review_findings(
        {
            "entrypoint": {"allow_abbrev": False},
            "routes": [route],
            "candidates": [candidate],
            "syntax_complete": True,
        },
        ReviewCatalog("audit-tool", 8, (), (case,)),
    )
    argparse_treats_token_as_option = parser._parse_optional(token) is not None
    review_rejects_token_as_value = any(
        "omits a value for --count" in finding for finding in findings
    )
    assert review_rejects_token_as_value is argparse_treats_token_as_option


def test_review_lexer_counts_lone_dash_as_a_positional_value():
    route_id = "route:entrypoint:audit-tool/inspect"
    route = {
        "id": route_id,
        "path": ["inspect"],
        "aliases": [],
        "kind": "invocation",
        "actions": [
            {
                "id": "argument:resource",
                "kind": "argument",
                "name": "resource",
                "nargs": None,
                "minimum_values": 1,
                "required": True,
                "parser_path": ["inspect"],
            }
        ],
    }
    candidate = {
        "id": "case:inspect/minimum",
        "route_id": route_id,
        "signature": "sha256:minimum",
        "kind": "minimum",
        "members": [],
        "shape": {
            "required_arguments": ["argument:resource"],
            "required_argument_values": {"argument:resource": 1},
        },
    }
    case = _case_for_candidate(candidate, ["inspect", "-"])
    findings = _review_findings(
        {"routes": [route], "candidates": [candidate], "syntax_complete": True},
        ReviewCatalog("audit-tool", 8, (), (case,)),
    )
    assert not any("omits required positional argument" in item for item in findings)


def test_review_does_not_trust_a_custom_negative_number_matcher():
    route_id = "route:entrypoint:audit-tool/inspect"
    route = {
        "id": route_id,
        "path": ["inspect"],
        "kind": "invocation",
        "parser_settings": [
            {
                "parser_path": ["inspect"],
                "allow_abbrev": False,
                "prefix_chars": "-",
                "fromfile_prefix_chars": None,
                "negative_number_matcher": {"pattern": r"^-1$", "flags": 0},
                "has_negative_number_optionals": False,
                "negative_number_matcher_custom": True,
            }
        ],
        "actions": [
            {
                "id": "option:count",
                "kind": "option",
                "flags": ["--count"],
                "nargs": None,
                "minimum_values": 1,
                "parser_path": ["inspect"],
            }
        ],
    }
    candidate = {
        "id": "case:count",
        "route_id": route_id,
        "signature": "sha256:count",
        "kind": "option-spelling",
        "members": ["option:count"],
        "shape": {"spelling": "--count"},
    }
    case = _case_for_candidate(candidate, ["inspect", "--count", "-1"])
    findings = _review_findings(
        {"routes": [route], "candidates": [candidate], "syntax_complete": False},
        ReviewCatalog("audit-tool", 8, (), (case,)),
    )
    assert any("omits a value for --count" in finding for finding in findings)


@pytest.mark.parametrize(
    "matcher",
    (
        {"pattern": None, "flags": 0},
        {"pattern": r"-\d+", "flags": "bad"},
        {"pattern": "(", "flags": 0},
    ),
    ids=("uninspectable", "invalid-flags", "invalid-regex"),
)
def test_review_lexer_fails_closed_for_unusable_negative_number_matchers(matcher):
    route_id = "route:entrypoint:audit-tool/inspect"
    route = {
        "id": route_id,
        "path": ["inspect"],
        "kind": "invocation",
        "parser_settings": [
            {
                "parser_path": ["inspect"],
                "allow_abbrev": False,
                "prefix_chars": "-",
                "fromfile_prefix_chars": None,
                "negative_number_matcher": matcher,
                "has_negative_number_optionals": False,
            }
        ],
        "actions": [
            {
                "id": "option:count",
                "kind": "option",
                "flags": ["--count"],
                "nargs": None,
                "minimum_values": 1,
                "parser_path": ["inspect"],
            }
        ],
    }
    candidate = {
        "id": "case:count",
        "route_id": route_id,
        "signature": "sha256:count",
        "kind": "option-spelling",
        "members": ["option:count"],
        "shape": {"spelling": "--count"},
    }
    case = _case_for_candidate(candidate, ["inspect", "--count", "-1"])
    findings = _review_findings(
        {
            "routes": [route],
            "candidates": [candidate],
            "syntax_complete": True,
        },
        ReviewCatalog("audit-tool", 8, (), (case,)),
    )

    assert any("omits a value for --count" in finding for finding in findings)


@pytest.mark.parametrize("nargs", (None, "?"))
def test_review_route_lexer_does_not_consume_extra_parent_positionals(nargs):
    prefix = {
        "id": "route:entrypoint:audit-tool/inspect",
        "path": ["inspect"],
        "aliases": [],
        "kind": "route-prefix",
        "parser_settings": [
            {
                "parser_path": [],
                "allow_abbrev": False,
                "prefix_chars": "-",
                "fromfile_prefix_chars": None,
                **_argparse_negative_number_settings()[1],
            }
        ],
        "actions": [],
    }
    route_id = "route:entrypoint:audit-tool/inspect/apply"
    argument = {
        "id": "argument:parent",
        "kind": "argument",
        "name": "parent",
        "nargs": nargs,
        "minimum_values": 0 if nargs == "?" else 1,
        "required": nargs is None,
        "parser_path": ["inspect"],
        "before_nested_subcommand": True,
    }
    route = {
        "id": route_id,
        "path": ["inspect", "apply"],
        "aliases": [],
        "kind": "invocation",
        "actions": [argument],
        "parser_settings": [],
    }
    candidate = {
        "id": f"case:extra-parent-{nargs}",
        "route_id": route_id,
        "signature": "sha256:extra-parent",
        "kind": "minimum",
        "members": [],
        "shape": {},
    }
    case = _case_for_candidate(
        candidate, ["inspect", "first", "second", "apply"]
    )
    surface = {
        "entrypoint": {"allow_abbrev": False},
        "routes": [prefix, route],
        "candidates": [candidate],
        "syntax_complete": True,
    }

    findings = _review_findings(
        surface, ReviewCatalog("audit-tool", 8, (), (case,))
    )
    assert any("omits its command path" in item for item in findings)


def test_review_candidate_argument_ids_take_precedence_over_member_fallbacks():
    route_id = "route:entrypoint:audit-tool/inspect"
    route = {
        "id": route_id,
        "path": ["inspect"],
        "aliases": [],
        "kind": "invocation",
        "actions": [
            {
                "id": "argument:first",
                "kind": "argument",
                "name": "first",
                "nargs": None,
                "minimum_values": 1,
                "required": True,
                "choices": ["good"],
                "parser_path": ["inspect"],
            },
            {
                "id": "argument:second",
                "kind": "argument",
                "name": "second",
                "nargs": "?",
                "minimum_values": 0,
                "required": False,
                "choices": None,
                "parser_path": ["inspect"],
            },
        ],
    }
    choice = {
        "id": "case:choice",
        "route_id": route_id,
        "signature": "sha256:choice",
        "kind": "argument-choice",
        "members": ["argument:second"],
        "shape": {"argument_id": "argument:first", "choice": "good"},
    }
    shape = {
        "id": "case:shape",
        "route_id": route_id,
        "signature": "sha256:shape",
        "kind": "argument-shape",
        "members": ["argument:second"],
        "shape": {"argument_id": "argument:first"},
    }
    cases = (
        _case_for_candidate(choice, ["inspect", "good"]),
        _case_for_candidate(shape, ["inspect", "good"]),
    )
    findings = _review_findings(
        {
            "entrypoint": {"allow_abbrev": False},
            "routes": [route],
            "candidates": [choice, shape],
            "syntax_complete": True,
        },
        ReviewCatalog("audit-tool", 8, (), cases),
    )
    assert findings == []


def test_review_rejects_unassigned_positional_tokens():
    route_id = "route:entrypoint:audit-tool/inspect"
    route = {
        "id": route_id,
        "path": ["inspect"],
        "aliases": [],
        "kind": "invocation",
        "actions": [],
    }
    candidate = {
        "id": "case:unexpected-positional",
        "route_id": route_id,
        "signature": "sha256:unexpected-positional",
        "kind": "other",
        "members": [],
        "shape": {},
    }

    findings = _findings_for_invocation(
        candidate, route, ["inspect", "stray-value"]
    )

    assert any("unassigned positional token 'stray-value'" in item for item in findings)


def test_argument_choice_case_reports_values_outside_reviewed_choice():
    registry = CliRegistry(IDENTITY, prog="audit-tool", description="choice shape")
    registry.register(
        VerbSpec(
            "inspect",
            description="inspect a resource",
            arguments=(
                ArgumentSpec(
                    "resource",
                    "resource identifier",
                    parser_kwargs={"choices": ("alpha", "beta")},
                ),
            ),
            handler=lambda *_: 0,
        )
    )
    surface = export_cli_surface(registry.build())
    candidate = next(
        case for case in surface["candidates"] if case["kind"] == "argument-choice"
    )
    route = next(route for route in surface["routes"] if route["id"] == candidate["route_id"])

    findings = _findings_for_invocation(candidate, route, ["inspect", "other"])

    assert any("omits its positional choice" in item for item in findings)


def test_argument_shape_case_rejects_unknown_arguments_and_short_fixed_arity():
    registry = CliRegistry(IDENTITY, prog="audit-tool", description="fixed arity")
    registry.register(
        VerbSpec(
            "inspect",
            description="inspect a pair",
            arguments=(
                ArgumentSpec(
                    "pair",
                    "two values",
                    parser_kwargs={"nargs": 2, "choices": ("one", "two")},
                ),
            ),
            handler=lambda *_: 0,
        )
    )
    surface = export_cli_surface(registry.build())
    route = next(route for route in surface["routes"] if route["kind"] == "invocation")
    candidate = next(
        case for case in surface["candidates"] if case["kind"] == "argument-shape"
    )

    short = _findings_for_invocation(candidate, route, ["inspect", "one"])
    assert any("invalid positional value shape" in item for item in short)

    choice_candidate = next(
        case
        for case in surface["candidates"]
        if case["kind"] == "argument-choice"
        and case["shape"].get("choice") == "one"
    )
    choice_findings = _findings_for_invocation(
        choice_candidate, route, ["inspect", "one"]
    )
    assert any("invalid positional value shape" in item for item in choice_findings)

    unknown = {
        **candidate,
        "members": ["argument:missing"],
        "shape": {**candidate["shape"], "argument_id": "argument:missing"},
    }
    unknown_findings = _findings_for_invocation(
        unknown, route, ["inspect", "one", "two"]
    )
    assert any("references unknown positional argument" in item for item in unknown_findings)

    unknown_choice = {
        **candidate,
        "kind": "argument-choice",
        "members": ["argument:missing"],
        "shape": {
            "argument_id": "argument:missing",
            "choice": "resource",
        },
    }
    unknown_choice_findings = _findings_for_invocation(
        unknown_choice, route, ["inspect", "one", "two"]
    )
    assert any(
        "references unknown positional argument" in item
        for item in unknown_choice_findings
    )


def test_value_shape_validation_rejects_inconsistent_arity_metadata():
    assert not _value_shape_accepts(
        {"nargs": None, "minimum_values": 0}, ["one", "two"]
    )
    assert not _value_shape_accepts(
        {"nargs": 2, "minimum_values": 0}, ["one"]
    )
    assert not _value_shape_accepts(
        {"nargs": "?", "minimum_values": 0}, ["one", "two"]
    )


def test_value_count_problem_distinguishes_shortage_from_wrong_count():
    fixed = {"nargs": 2, "minimum_values": 2}
    assert _value_count_problem(fixed, ("one",)) == "too-few"
    assert _value_count_problem(fixed, ("one", "two")) is None
    assert _value_count_problem(fixed, ("one", "two", "three")) == "wrong-count"
    assert _value_count_problem(
        {"nargs": "?", "minimum_values": 0}, ("one", "two")
    ) == "wrong-count"


def test_option_occurrences_require_presence_and_reject_invalid_flag_values():
    flag = {"kind": "option", "nargs": 0}
    assert not _option_occurrences_accept(flag, ())
    assert _option_occurrences_accept(flag, [("--yes", (), False)])
    assert not _option_occurrences_accept(flag, [("--yes", (), True)])
    assert _option_occurrences_accept(
        {"kind": "option", "nargs": None},
        [("--source", ("input.toml",), False)],
    )
    assert not _option_occurrences_accept(
        {"kind": "option", "nargs": None}, [("--source", (), False)]
    )


def test_review_reports_inconsistent_optional_arity_and_unknown_action_kind():
    route = {
        "id": ROUTE_ID,
        "path": ["inspect"],
        "aliases": [],
        "kind": "invocation",
        "actions": [
            {
                "id": "argument:optional-pair",
                "kind": "argument",
                "name": "pair",
                "nargs": 2,
                "minimum_values": 2,
                "required": False,
                "parser_path": ["inspect"],
            },
            {"id": "opaque:action", "kind": "opaque"},
        ],
    }
    candidate = {
        "id": "case:malformed-action-surface",
        "route_id": ROUTE_ID,
        "signature": "sha256:malformed-action-surface",
        "kind": "other",
        "members": [],
        "shape": {},
    }

    findings = _findings_for_invocation(candidate, route, ["inspect", "one"])

    assert any(
        "omits a value for positional argument argument:optional-pair" in item
        for item in findings
    )
    assert any("unsupported surface action kind 'opaque'" in item for item in findings)


def test_required_group_exemptions_only_apply_to_declared_interactions():
    registry = CliRegistry(IDENTITY, prog="audit-tool", description="group interactions")
    registry.register(
        VerbSpec(
            "inspect",
            description="inspect a source",
            options=(
                OptionSpec(
                    ("--from-file",),
                    "read from a file",
                    mutually_exclusive_group="source",
                    mutually_exclusive_required=True,
                    parser_kwargs={"action": "store_true"},
                ),
                OptionSpec(
                    ("--from-inline",),
                    "read inline data",
                    mutually_exclusive_group="source",
                    mutually_exclusive_required=True,
                    parser_kwargs={"action": "store_true"},
                ),
            ),
            handler=lambda *_: 0,
        )
    )
    surface = export_cli_surface(registry.build())
    route = next(route for route in surface["routes"] if route["kind"] == "invocation")
    options = [
        action
        for action in route["actions"]
        if action.get("flags") in (["--from-file"], ["--from-inline"])
    ]
    option_ids = [action["id"] for action in options]
    conflict = next(
        candidate
        for candidate in surface["candidates"]
        if candidate["kind"] == "exclusive-conflict"
    )
    malformed_conflict = {
        **conflict,
        "shape": {**conflict["shape"], "group_id": None},
    }

    malformed_findings = _findings_for_invocation(
        malformed_conflict,
        route,
        ["inspect", "--from-file", "--from-inline"],
    )
    assert any(
        "must supply exactly one option from required group source" in item
        for item in malformed_findings
    )

    interaction = {
        "id": "case:required-group-interaction",
        "route_id": route["id"],
        "signature": "sha256:required-group-interaction",
        "kind": "interaction",
        "members": option_ids,
        "shape": {
            "option_ids": option_ids,
            "required_arguments": [],
            "required_options": [],
            "required_exclusive_groups": {},
            "external_options": [],
        },
    }
    interaction_findings = _findings_for_invocation(
        interaction,
        route,
        ["inspect", "--from-file", "--from-inline"],
    )
    assert not any(
        "must supply exactly one option from required group source" in item
        for item in interaction_findings
    )

    repeated_interaction_findings = _findings_for_invocation(
        interaction,
        route,
        ["inspect", "--from-file", "--from-file", "--from-inline"],
    )
    # The declared argv is structurally valid; its linked behavior test owns
    # whether the product accepts or refuses repeated occurrences.
    assert repeated_interaction_findings == []


def test_review_checks_required_baseline_for_non_minimum_candidates():
    registry = CliRegistry(IDENTITY, prog="audit-tool", description="required baseline")
    registry.register(
        VerbSpec(
            "inspect",
            description="inspect a resource",
            arguments=(ArgumentSpec("resource", "resource to inspect"),),
            options=(
                OptionSpec(
                    ("--required",),
                    "required setting",
                    parser_kwargs={"required": True},
                ),
                OptionSpec(
                    ("--pair",),
                    "required pair",
                    parser_kwargs={"required": True, "nargs": 2},
                ),
                OptionSpec(("--detail",), "show more detail"),
            ),
            handler=lambda *_: 0,
        )
    )
    surface = export_cli_surface(registry.build())
    candidate = next(
        candidate
        for candidate in surface["candidates"]
        if candidate["kind"] == "option-spelling"
        and candidate["shape"].get("spelling") == "--detail"
    )

    findings = _findings_for_invocation(
        candidate,
        next(route for route in surface["routes"] if route["id"] == candidate["route_id"]),
        ["inspect", "--detail", "full"],
    )

    assert any("required positional argument" in finding for finding in findings)
    assert any("omits required option" in finding for finding in findings)

    short_pair = _findings_for_invocation(
        candidate,
        next(route for route in surface["routes"] if route["id"] == candidate["route_id"]),
        [
            "inspect", "resource", "--detail", "full", "--required", "setting",
            "--pair", "only-one",
        ],
    )
    assert any("invalid value shape for required option" in item for item in short_pair)


def test_review_rejects_malformed_repeated_option_occurrences():
    registry = CliRegistry(IDENTITY, prog="audit-tool", description="repeated options")
    registry.register(
        VerbSpec(
            "inspect",
            description="inspect a resource",
            options=(
                OptionSpec(
                    ("--pair",),
                    "two values",
                    parser_kwargs={"nargs": 2, "choices": ("left", "right")},
                ),
            ),
            handler=lambda *_: 0,
        )
    )
    surface = export_cli_surface(registry.build())
    candidate = next(
        candidate
        for candidate in surface["candidates"]
        if candidate["kind"] == "option-spelling"
        and candidate["shape"].get("spelling") == "--pair"
    )

    findings = _findings_for_invocation(
        candidate,
        next(route for route in surface["routes"] if route["id"] == candidate["route_id"]),
        ["inspect", "--pair", "left", "right", "--pair", "left"],
    )

    assert any("invalid value shape for option" in finding for finding in findings), findings


def test_required_exclusive_group_checks_fixed_arity_for_selected_member():
    registry = CliRegistry(IDENTITY, prog="audit-tool", description="required group arity")
    registry.register(
        VerbSpec(
            "inspect",
            description="inspect a resource",
            options=(
                OptionSpec(
                    ("--detail",),
                    "show more detail",
                    parser_kwargs={"action": "store_true"},
                ),
                OptionSpec(
                    ("--from-pair",),
                    "two-part source",
                    mutually_exclusive_group="source",
                    mutually_exclusive_required=True,
                    parser_kwargs={"nargs": 2},
                ),
                OptionSpec(
                    ("--from-inline",),
                    "inline source",
                    mutually_exclusive_group="source",
                    mutually_exclusive_required=True,
                    parser_kwargs={"action": "store_true"},
                ),
            ),
            handler=lambda *_: 0,
        )
    )
    surface = export_cli_surface(registry.build())
    candidate = next(
        case
        for case in surface["candidates"]
        if case["kind"] == "option-spelling"
        and case["shape"].get("spelling") == "--detail"
    )
    route = next(route for route in surface["routes"] if route["id"] == candidate["route_id"])

    findings = _findings_for_invocation(
        candidate,
        route,
        ["inspect", "--detail", "--from-pair", "only-one"],
    )

    assert any("invalid value shape in required group source" in item for item in findings)


def test_review_rejects_bad_choice_in_a_repeated_option_occurrence():
    registry = CliRegistry(IDENTITY, prog="audit-tool", description="repeated choices")
    registry.register(
        VerbSpec(
            "inspect",
            description="inspect a resource",
            options=(
                OptionSpec(
                    ("--mode",),
                    "select a mode",
                    parser_kwargs={"choices": ("safe", "fast")},
                ),
            ),
            handler=lambda *_: 0,
        )
    )
    surface = export_cli_surface(registry.build())
    candidate = next(
        candidate
        for candidate in surface["candidates"]
        if candidate["kind"] == "option-choice"
        and candidate["shape"].get("choice") == "safe"
    )

    findings = _findings_for_invocation(
        candidate,
        next(route for route in surface["routes"] if route["id"] == candidate["route_id"]),
        ["inspect", "--mode", "safe", "--mode", "unsupported"],
    )

    assert any("invalid value for option" in finding for finding in findings)
    assert not any("invalid value shape for option" in finding for finding in findings)


@pytest.mark.parametrize(
    "parser_kwargs, value, accepted",
    (
        pytest.param(
            {"choices": (1, 2)}, "1", False, id="raw-string-vs-integer-choice"
        ),
        pytest.param(
            {"type": int, "choices": (1, 2)}, "1", True, id="integer-converter"
        ),
        pytest.param(
            {"type": int, "choices": (1, 2)},
            "not-an-integer",
            False,
            id="invalid-integer-conversion",
        ),
        pytest.param(
            {"type": int}, "not-an-integer", False,
            id="invalid-conversion-without-choices",
        ),
        pytest.param(
            {"type": str, "choices": (1,)}, "1", False, id="string-converter"
        ),
        pytest.param(
            {"type": int, "choices": ("1",)}, "1", False, id="mismatched-choice-type"
        ),
        pytest.param(
            {"type": float, "choices": (1.0,)}, "1", True, id="float-equivalent"
        ),
        pytest.param(
            {"type": bool, "choices": (False,)}, "", True, id="false-boolean-choice"
        ),
    ),
)
def test_review_choice_validation_matches_argparse_builtin_converters(
    parser_kwargs, value, accepted
):
    registry = CliRegistry(IDENTITY, prog="audit-tool", description="typed choices")
    registry.register(
        VerbSpec(
            "inspect",
            description="inspect one resource",
            options=(
                OptionSpec(
                    ("--mode",),
                    "select a mode",
                    parser_kwargs=parser_kwargs,
                ),
            ),
            handler=lambda *_: 0,
        )
    )
    app = registry.build()
    surface = export_cli_surface(app)
    candidate_kind = (
        "option-choice"
        if parser_kwargs.get("choices") is not None
        else "option-spelling"
    )
    candidate = next(
        case
        for case in surface["candidates"]
        if case["kind"] == candidate_kind
    )
    route = next(
        route for route in surface["routes"] if route["id"] == candidate["route_id"]
    )
    invocation = ["inspect", "--mode", value]

    findings = _findings_for_invocation(candidate, route, invocation)
    actual_status = app.run(
        argv=invocation, stdout=io.StringIO(), stderr=io.StringIO()
    )

    assert actual_status == (0 if accepted else 2)
    if accepted:
        assert findings == []
    else:
        assert any("invalid value for option" in item for item in findings)


def test_review_positional_choice_uses_the_built_in_converter_value():
    registry = CliRegistry(IDENTITY, prog="audit-tool", description="typed positional")
    registry.register(
        VerbSpec(
            "inspect",
            description="inspect one numeric resource",
            arguments=(
                ArgumentSpec(
                    "number",
                    "numeric resource identifier",
                    parser_kwargs={"type": float, "choices": (1.0,)},
                ),
            ),
            handler=lambda *_: 0,
        )
    )
    app = registry.build()
    surface = export_cli_surface(app)
    candidate = next(
        case
        for case in surface["candidates"]
        if case["kind"] == "argument-choice"
    )
    route = next(
        route for route in surface["routes"] if route["id"] == candidate["route_id"]
    )
    invocation = ["inspect", "1"]

    assert app.run(
        argv=invocation, stdout=io.StringIO(), stderr=io.StringIO()
    ) == 0
    assert _findings_for_invocation(candidate, route, invocation) == []


def test_review_parser_nargs_choice_case_uses_only_the_first_value():
    action = {
        "id": "argument:command",
        "kind": "argument",
        "action": "argparse._StoreAction",
        "nargs": argparse.PARSER,
        "type": None,
    }

    assert _choice_values_accept(action, ("inspect", "trailing"), "inspect") is True
    assert _choice_values_accept(action, ("inspect", "trailing"), "trailing") is False


def test_review_optional_option_const_can_satisfy_its_choice_case():
    registry = CliRegistry(IDENTITY, prog="audit-tool", description="const choice")
    registry.register(
        VerbSpec(
            "inspect",
            description="inspect one resource",
            options=(
                OptionSpec(
                    ("--mode",),
                    "select a mode",
                    parser_kwargs={
                        "nargs": "?",
                        "const": "1",
                        "type": int,
                        "choices": (1, 2),
                    },
                ),
            ),
            handler=lambda *_: 0,
        )
    )
    app = registry.build()
    surface = export_cli_surface(app)
    candidate = next(
        case
        for case in surface["candidates"]
        if case["kind"] == "option-choice" and case["shape"]["choice"] == 1
    )
    route = next(
        route for route in surface["routes"] if route["id"] == candidate["route_id"]
    )
    invocation = ["inspect", "--mode"]

    assert app.run(
        argv=invocation, stdout=io.StringIO(), stderr=io.StringIO()
    ) == 0
    assert _findings_for_invocation(candidate, route, invocation) == []


@pytest.mark.parametrize(
    "const, choices, expected",
    (
        pytest.param("allowed", ["allowed"], True, id="string-const-member"),
        pytest.param("rejected", ["allowed"], False, id="string-const-not-member"),
        pytest.param(1, [1], True, id="numeric-const-member"),
        pytest.param(2, [1], False, id="numeric-const-not-member"),
    ),
)
def test_review_optional_const_choice_rule_uses_the_recorded_probe(
    const, choices, expected
):
    action = {
        "id": "option:mode",
        "kind": "option",
        "action": "argparse._StoreAction",
        "nargs": "?",
        "type": None,
        "const": const,
        "const_choice_check_on_omission": True,
        "choices": choices,
    }

    assert _values_satisfy_action(action, ()) is expected


def test_review_treats_missing_optional_const_probe_as_opaque():
    action = {
        "id": "option:mode",
        "kind": "option",
        "action": "argparse._StoreAction",
        "nargs": "?",
        "type": None,
        "const": "outside-the-list",
        "choices": ["ready"],
    }

    assert _values_satisfy_action(action, ()) is True
    assert _choice_values_accept(action, (), "another-value") is True


def test_review_treats_non_scalar_optional_const_as_opaque():
    action = {
        "id": "option:mode",
        "kind": "option",
        "action": "argparse._StoreAction",
        "nargs": "?",
        "type": None,
        "const": {"opaque": "custom-constant"},
        "const_choice_check_on_omission": True,
        "choices": [],
    }

    assert _values_satisfy_action(action, ()) is True
    assert _choice_values_accept(action, (), "ready") is True


def test_review_non_string_optional_const_choice_candidate_uses_original_value():
    registry = CliRegistry(
        IDENTITY, prog="audit-tool", description="numeric const choice"
    )
    registry.register(
        VerbSpec(
            "inspect",
            description="inspect one resource",
            options=(
                OptionSpec(
                    ("--mode",),
                    "select a mode",
                    parser_kwargs={
                        "nargs": "?",
                        "const": 1,
                        "type": bool,
                        "choices": (1,),
                    },
                ),
            ),
            handler=lambda *_: 0,
        )
    )
    app = registry.build()
    surface = export_cli_surface(app)
    candidate = next(
        case
        for case in surface["candidates"]
        if case["kind"] == "option-choice" and case["shape"]["choice"] == 1
    )
    route = next(
        route for route in surface["routes"] if route["id"] == candidate["route_id"]
    )
    invocation = ["inspect", "--mode"]

    assert app.run(
        argv=invocation, stdout=io.StringIO(), stderr=io.StringIO()
    ) == 0
    assert _findings_for_invocation(candidate, route, invocation) == []


@pytest.mark.parametrize(
    "nargs, values, accepted",
    (
        pytest.param(
            argparse.REMAINDER,
            ("allowed", "anything"),
            True,
            id="remainder-ignores-choices",
        ),
        pytest.param(
            argparse.PARSER,
            ("allowed", "anything"),
            True,
            id="parser-checks-first-only",
        ),
        pytest.param(
            argparse.PARSER,
            ("anything", "allowed"),
            False,
            id="parser-rejects-first-value",
        ),
    ),
)
def test_review_positional_choice_validation_matches_argparse_nargs(
    nargs, values, accepted
):
    registry = CliRegistry(IDENTITY, prog="audit-tool", description="nargs choices")
    registry.register(
        VerbSpec(
            "inspect",
            description="inspect forwarded values",
            arguments=(
                ArgumentSpec(
                    "values",
                    "values to inspect",
                    parser_kwargs={"nargs": nargs, "choices": ("allowed",)},
                ),
            ),
            handler=lambda *_: 0,
        )
    )
    app = registry.build()
    surface = export_cli_surface(app)
    argument_id = next(
        action["id"]
        for route in surface["routes"]
        for action in route["actions"]
        if action.get("name") == "values"
    )
    candidate = next(
        case
        for case in surface["candidates"]
        if case["kind"] == "argument-shape"
        and case["shape"].get("argument_id") == argument_id
    )
    route = next(
        route for route in surface["routes"] if route["id"] == candidate["route_id"]
    )
    invocation = ["inspect", *values]

    assert app.run(
        argv=invocation, stdout=io.StringIO(), stderr=io.StringIO()
    ) == (0 if accepted else 2)
    findings = _findings_for_invocation(candidate, route, invocation)
    assert any(
        "invalid positional value" in finding
        or "omits its positional choice" in finding
        for finding in findings
    ) is (not accepted)


def test_review_parser_action_converts_every_supplied_value():
    registry = CliRegistry(
        IDENTITY, prog="audit-tool", description="parser positional conversion"
    )
    registry.register(
        VerbSpec(
            "inspect",
            description="inspect forwarded values",
            arguments=(
                ArgumentSpec(
                    "values",
                    "values to inspect",
                    parser_kwargs={
                        "nargs": argparse.PARSER,
                        "type": int,
                        "choices": (1,),
                    },
                ),
            ),
            handler=lambda *_: 0,
        )
    )
    app = registry.build()
    surface = export_cli_surface(app)
    argument_id = next(
        action["id"]
        for route in surface["routes"]
        for action in route["actions"]
        if action.get("name") == "values"
    )
    candidate = next(
        case
        for case in surface["candidates"]
        if case["kind"] == "argument-choice"
        and case["shape"].get("argument_id") == argument_id
    )
    route = next(
        route for route in surface["routes"] if route["id"] == candidate["route_id"]
    )
    invocation = ["inspect", "1", "not-an-int"]

    assert app.run(
        argv=invocation, stdout=io.StringIO(), stderr=io.StringIO()
    ) == 2
    assert any(
        "omits its positional choice" in finding
        for finding in _findings_for_invocation(candidate, route, invocation)
    )


def test_review_remainder_still_applies_builtin_conversion():
    registry = CliRegistry(
        IDENTITY, prog="audit-tool", description="remainder positional conversion"
    )
    registry.register(
        VerbSpec(
            "inspect",
            description="inspect forwarded values",
            arguments=(
                ArgumentSpec(
                    "values",
                    "values to inspect",
                    parser_kwargs={"nargs": argparse.REMAINDER, "type": int},
                ),
            ),
            handler=lambda *_: 0,
        )
    )
    app = registry.build()
    surface = export_cli_surface(app)
    argument_id = next(
        action["id"]
        for route in surface["routes"]
        for action in route["actions"]
        if action.get("name") == "values"
    )
    candidate = next(
        case
        for case in surface["candidates"]
        if case["kind"] == "argument-shape"
        and case["shape"].get("argument_id") == argument_id
    )
    route = next(
        route for route in surface["routes"] if route["id"] == candidate["route_id"]
    )
    invocation = ["inspect", "not-an-int"]

    assert app.run(
        argv=invocation, stdout=io.StringIO(), stderr=io.StringIO()
    ) == 2
    assert any(
        "invalid positional value" in finding
        for finding in _findings_for_invocation(candidate, route, invocation)
    )


@pytest.mark.parametrize(
    "const, converter, choices",
    (
        pytest.param("ready", None, ("ready",), id="choice-member-const"),
        pytest.param(
            "unknown", None, ("ready",), id="runtime-const-choice-rule"
        ),
        pytest.param("1", int, (1,), id="const-is-converted"),
        pytest.param("invalid", int, (1,), id="invalid-const-conversion"),
        pytest.param(
            "invalid", int, None, id="invalid-const-conversion-without-choices"
        ),
        pytest.param(None, None, (2,), id="none-const-choice-mismatch"),
        pytest.param(None, None, (None,), id="none-const-choice-match"),
        pytest.param(True, None, (False,), id="bool-const-choice-mismatch"),
        pytest.param(True, None, (True,), id="bool-const-choice-match"),
        pytest.param(1.5, None, (2.5,), id="float-const-choice-mismatch"),
        pytest.param(1.5, None, (1.5,), id="float-const-choice-match"),
        pytest.param(1, None, (2,), id="numeric-const-choice-mismatch"),
        pytest.param(1, None, (1,), id="numeric-const-choice-match"),
        pytest.param(1, bool, (False,), id="numeric-const-is-not-converted"),
    ),
)
def test_review_models_argparse_optional_option_const(const, converter, choices):
    registry = CliRegistry(IDENTITY, prog="audit-tool", description="const choices")
    registry.register(
        VerbSpec(
            "inspect",
            description="inspect one resource",
            options=(
                OptionSpec(
                    ("--mode",),
                    "select a mode",
                    parser_kwargs={
                        "nargs": "?",
                        "const": const,
                        "choices": choices,
                        "type": converter,
                    },
                ),
            ),
            handler=lambda *_: 0,
        )
    )
    app = registry.build()
    surface = export_cli_surface(app)
    mode_action = next(
        action
        for route in surface["routes"]
        for action in route["actions"]
        if action.get("flags") == ["--mode"]
    )
    candidate = next(
        case
        for case in surface["candidates"]
        if case["kind"] == "option-spelling"
        and case["shape"].get("option_id") == mode_action["id"]
    )
    route = next(
        route for route in surface["routes"] if route["id"] == candidate["route_id"]
    )
    action = next(
        action
        for action in route["actions"]
        if action.get("flags") == ["--mode"]
    )
    invocation = ["inspect", "--mode"]

    actual_status = app.run(
        argv=invocation, stdout=io.StringIO(), stderr=io.StringIO()
    )
    findings = _findings_for_invocation(candidate, route, invocation)

    assert "const_choice_check_on_omission" in action
    markdown = render_cli_surface_markdown(
        surface, ReviewCatalog("audit-tool", 8, (), ())
    )
    assert (
        '"const_choice_check_on_omission": '
        f'{str(action["const_choice_check_on_omission"]).lower()}'
    ) in markdown
    assert (findings == []) is (actual_status == 0)


def test_review_does_not_apply_optional_option_const_to_a_positional():
    positional = {
        "kind": "argument",
        "nargs": "?",
        "const": "not-a-choice",
        "choices": ["ready"],
        "type": None,
        "const_choice_check_on_omission": True,
    }

    assert _values_satisfy_action(positional, ())


def test_review_leaves_custom_optional_option_const_to_behavior_test():
    action = {
        "kind": "option",
        "nargs": "?",
        "const": ("custom",),
        "choices": ["ready"],
        "const_choice_check_on_omission": True,
    }

    assert _values_satisfy_action(action, ())


def test_review_choice_matching_handles_optional_consts_and_parser_arity():
    assert _choice_values_accept(
        {
            "kind": "option",
            "nargs": "?",
            "const": ("custom",),
            "type": None,
        },
        (),
        "reviewed-choice",
    )
    assert _choice_values_accept(
        {
            "kind": "option",
            "nargs": "?",
            "const": 2,
            "type": {"callable": "builtins.int"},
        },
        (),
        2,
    )
    assert _choice_values_accept(
        {"kind": "argument", "nargs": argparse.PARSER, "type": None},
        ("inspect", "ignored-tail"),
        "inspect",
    )


def test_review_does_not_execute_custom_choice_converters():
    calls = []

    def custom_converter(value):
        calls.append(value)
        return int(value)

    registry = CliRegistry(IDENTITY, prog="audit-tool", description="custom choices")
    registry.register(
        VerbSpec(
            "inspect",
            description="inspect a numeric resource",
            options=(
                OptionSpec(
                    ("--mode",),
                    "select a mode",
                    parser_kwargs={"type": custom_converter, "choices": (1,)},
                ),
            ),
            handler=lambda *_: 0,
        )
    )
    app = registry.build()
    surface = export_cli_surface(app)
    candidate = next(
        case
        for case in surface["candidates"]
        if case["kind"] == "option-choice"
    )
    route = next(
        route for route in surface["routes"] if route["id"] == candidate["route_id"]
    )

    findings = _findings_for_invocation(
        candidate, route, ["inspect", "--mode", "anything"]
    )

    assert findings == []
    assert calls == []
    assert _converted_action_values({"type": {"callable": []}}, ("1",)) == ("opaque", ())
    assert _converted_action_values(
        {"action": [], "type": {"callable": "builtins.int"}}, ("1",)
    ) == ("opaque", ())
    assert _converted_action_values(
        {
            "action": "consumer.CustomAction",
            "type": {"callable": "builtins.int"},
        },
        ("1",),
    ) == ("opaque", ())


def test_review_does_not_trust_a_custom_converter_with_a_builtin_label():
    calls = []

    def custom_converter(_value):
        calls.append("called")
        return 1

    custom_converter.__module__ = "builtins"
    custom_converter.__qualname__ = "int"
    registry = CliRegistry(IDENTITY, prog="audit-tool", description="spoofed type")
    registry.register(
        VerbSpec(
            "inspect",
            description="inspect one resource",
            options=(
                OptionSpec(
                    ("--mode",),
                    "select a mode",
                    parser_kwargs={"type": custom_converter, "choices": (1,)},
                ),
            ),
            handler=lambda *_: 0,
        )
    )
    app = registry.build()
    surface = export_cli_surface(app)
    candidate = next(
        case
        for case in surface["candidates"]
        if case["kind"] == "option-choice"
    )
    route = next(
        route for route in surface["routes"] if route["id"] == candidate["route_id"]
    )
    action = next(
        action
        for action in route["actions"]
        if action.get("flags") == ["--mode"]
    )

    assert action["type"] == {"opaque": "built-in-converter-label-collision"}
    assert action["parser_kwargs"]["type"] == {
        "opaque": "built-in-converter-label-collision"
    }
    assert _findings_for_invocation(
        candidate, route, ["inspect", "--mode", "not-an-integer"]
    ) == []
    assert calls == []


def test_review_allows_repeated_occurrences_of_one_required_group_option():
    registry = CliRegistry(IDENTITY, prog="audit-tool", description="group repeats")
    registry.register(
        VerbSpec(
            "inspect",
            description="inspect a resource",
            options=(
                OptionSpec(
                    ("--detail",),
                    "show more detail",
                    parser_kwargs={"action": "store_true"},
                ),
                OptionSpec(
                    ("--from-file",),
                    "read configuration from a file",
                    mutually_exclusive_group="source",
                    mutually_exclusive_required=True,
                ),
                OptionSpec(
                    ("--from-inline",),
                    "read configuration from an inline value",
                    mutually_exclusive_group="source",
                    mutually_exclusive_required=True,
                ),
            ),
            handler=lambda *_: 0,
        )
    )
    app = registry.build()
    surface = export_cli_surface(app)
    candidate = next(
        candidate
        for candidate in surface["candidates"]
        if candidate["kind"] == "option-spelling"
        and candidate["shape"].get("spelling") == "--detail"
    )
    route = next(
        route for route in surface["routes"] if route["id"] == candidate["route_id"]
    )
    invocation = [
        "inspect",
        "--detail",
        "--from-file",
        "first.toml",
        "--from-file",
        "second.toml",
    ]

    assert _findings_for_invocation(
        candidate,
        route,
        invocation,
    ) == []
    assert app.run(argv=invocation, stderr=io.StringIO()) == 0


def test_exclusive_candidates_enforce_selected_members_and_exact_conflict_shape():
    registry = CliRegistry(IDENTITY, prog="audit-tool", description="exclusive cases")
    registry.register(
        VerbSpec(
            "inspect",
            description="inspect a source",
            options=(
                OptionSpec(
                    ("--detail",),
                    "show detail",
                    parser_kwargs={"action": "store_true"},
                ),
                OptionSpec(
                    ("--from-file",),
                    "read from a file",
                    mutually_exclusive_group="source",
                    mutually_exclusive_required=True,
                    parser_kwargs={"action": "store_true"},
                ),
                OptionSpec(
                    ("--from-inline",),
                    "read inline data",
                    mutually_exclusive_group="source",
                    mutually_exclusive_required=True,
                    parser_kwargs={"action": "store_true"},
                ),
                OptionSpec(
                    ("--other",),
                    "another source",
                    mutually_exclusive_group="source",
                    mutually_exclusive_required=True,
                    parser_kwargs={"action": "store_true"},
                ),
            ),
            handler=lambda *_: 0,
        )
    )
    app = registry.build()
    surface = export_cli_surface(app)
    route = next(route for route in surface["routes"] if route["kind"] == "invocation")
    member = next(
        case
        for case in surface["candidates"]
        if case["kind"] == "exclusive-member"
        and case["shape"]["selected_option"].endswith("--from-file")
    )
    wrong_member = _findings_for_invocation(
        member, route, ["inspect", "--from-inline"]
    )
    assert any("only its selected exclusive option" in item for item in wrong_member)

    repeated_selected = ["inspect", "--from-file", "--from-file"]
    assert _findings_for_invocation(member, route, repeated_selected) == []
    assert app.run(argv=repeated_selected, stderr=io.StringIO()) == 0

    conflict = next(
        case
        for case in surface["candidates"]
        if case["kind"] == "exclusive-conflict"
        and set(case["shape"]["options"])
        == {
            action["id"]
            for action in route["actions"]
            if action.get("flags") in (["--from-file"], ["--from-inline"])
        }
    )
    case = _case_for_candidate(
        conflict,
        ["inspect", "--from-file", "--from-inline"],
        decision="refuse",
        expected_exit_status=2,
    )
    conflict_findings = _review_findings(
        {**surface, "candidates": [conflict]},
        ReviewCatalog("audit-tool", 8, (), (case,)),
    )
    assert conflict_findings == []
    assert app.run(argv=case.invocation, stderr=io.StringIO()) == 2

    incomplete_conflict = _case_for_candidate(
        conflict,
        ["inspect", "--from-file"],
        decision="refuse",
        expected_exit_status=2,
    )
    incomplete_findings = _review_findings(
        {**surface, "candidates": [conflict]},
        ReviewCatalog("audit-tool", 8, (), (incomplete_conflict,)),
    )
    assert any("must supply each conflicting option once" in item for item in incomplete_findings)


def test_review_invocation_lexer_handles_option_arities_and_delimiters():
    route_id = "route:entrypoint:audit-tool/inspect"
    arities = (
        ("zero", "--zero", 0, 0, ["--zero"]),
        ("one", "--one", None, 1, ["--one", "v"]),
        ("one-end", "--one-end", None, 1, ["--one-end"]),
        ("fixed", "--fixed", 2, 2, ["--fixed=first", "second"]),
        ("fixed-short", "--fixed-short", 2, 2, ["--fixed-short", "first"]),
        ("maybe", "--maybe", "?", 0, ["--maybe", "--zero"]),
        ("maybe-end", "--maybe-end", "?", 0, ["--maybe-end"]),
        ("many", "--many", "*", 0, ["--many", "a", "b", "--zero"]),
        ("many-end", "--many-end", "*", 0, ["--many-end", "a", "b"]),
        ("many-delimiter", "--many-delimiter", "*", 0, ["--many-delimiter", "a", "--", "--zero"]),
        ("some", "--some", "+", 1, ["--some", "a", "b", "--zero"]),
        ("some-empty", "--some-empty", "+", 1, ["--some-empty"]),
        ("some-end", "--some-end", "+", 1, ["--some-end", "a", "b"]),
        ("rest", "--rest", argparse.REMAINDER, 0, ["--rest", "tail", "--zero"]),
        ("parser", "--parser", argparse.PARSER, 1, ["--parser", "child"]),
        ("odd", "--odd", "odd", 0, ["--odd"]),
    )
    actions = [
        {
            "id": action_id,
            "kind": "option",
            "flags": [flag],
            "nargs": nargs,
            "minimum_values": minimum,
            "required": False,
            "choices": None,
            "const": None,
            "const_choice_check_on_omission": False,
            "type": None,
            "parser_path": ["inspect"],
        }
        for action_id, flag, nargs, minimum, _tokens in arities
    ]
    route = {"id": route_id, "path": ["inspect"], "aliases": [], "kind": "invocation", "actions": actions}
    candidates = []
    cases = []
    for action_id, flag, _nargs, _minimum, tokens in arities:
        candidate = {"id": f"case:{action_id}", "route_id": route_id, "signature": "s", "kind": "option-spelling", "members": [action_id], "shape": {"spelling": flag}}
        candidates.append(candidate)
        cases.append(_case_for_candidate(candidate, ["inspect", *tokens]))
    for action_id, flag, invocation in (
        ("fixed", "--fixed", ["--fixed", "first", "second"]),
        ("maybe", "--maybe", ["--maybe", "value"]),
        ("one", "--one", ["--one=value"]),
    ):
        candidate = {"id": f"case:{action_id}-alternate", "route_id": route_id, "signature": "s", "kind": "option-spelling", "members": [action_id], "shape": {"spelling": flag}}
        candidates.append(candidate)
        cases.append(_case_for_candidate(candidate, ["inspect", *invocation]))
    delimiter_candidate = {"id": "case:delimiter", "route_id": route_id, "signature": "s", "kind": "other", "members": [], "shape": {}}
    candidates.append(delimiter_candidate)
    cases.append(_case_for_candidate(delimiter_candidate, ["inspect", "-unknown", "bare", "--", "tail"]))
    eq_zero = {"id": "case:eq-zero", "route_id": route_id, "signature": "s", "kind": "option-spelling", "members": ["zero"], "shape": {"spelling": "--zero"}}
    candidates.append(eq_zero)
    cases.append(_case_for_candidate(eq_zero, ["inspect", "--zero=value"]))

    findings = _review_findings(
        {"routes": [route], "candidates": candidates, "syntax_complete": True},
        ReviewCatalog("audit-tool", 32, (), tuple(cases)),
    )
    assert any("case:eq-zero" in item and "omits its reviewed option spelling" in item for item in findings)
    assert not any("case:zero" in item for item in findings)
    assert any(
        "case:many-delimiter" in item
        and "unassigned positional token '--zero'" in item
        for item in findings
    )
    for case_id in ("one-end", "fixed-short", "some-empty"):
        assert any(
            f"invocation for case:{case_id} omits a value for --" in item
            for item in findings
        )
    assert not any("case:maybe-end" in item for item in findings)


def test_review_argument_shape_and_choice_are_checked_at_their_registered_position():
    route_id = "route:entrypoint:audit-tool/build"
    route = {
        "id": route_id,
        "path": ["build"],
        "aliases": [],
        "kind": "invocation",
        "actions": [
            {
                "id": "argument:source",
                "kind": "argument",
                "name": "source",
                "nargs": None,
                "minimum_values": 1,
                "required": True,
                "parser_path": ["build"],
            },
            {
                "id": "argument:format",
                "kind": "argument",
                "name": "format",
                "nargs": "?",
                "minimum_values": 0,
                "required": False,
                "choices": ["wheel", "sdist"],
                "parser_path": ["build"],
            },
            {
                "id": "argument:pair",
                "kind": "argument",
                "name": "pair",
                "nargs": 2,
                "minimum_values": 2,
                "required": True,
                "parser_path": ["build"],
            },
            {
                "id": "argument:custom",
                "kind": "argument",
                "name": "custom",
                "nargs": "custom",
                "minimum_values": 0,
                "required": False,
                "parser_path": ["build"],
            },
        ],
    }
    wrong_choice = {
        "id": "case:format-wheel",
        "route_id": route_id,
        "signature": "s",
        "kind": "argument-choice",
        "members": ["argument:format"],
        "shape": {"argument_id": "argument:format", "choice": "wheel"},
    }
    missing_format = {
        "id": "case:format-shape",
        "route_id": route_id,
        "signature": "s",
        "kind": "argument-shape",
        "members": ["argument:format"],
        "shape": {"argument_id": "argument:format"},
    }
    correct_choice = {
        "id": "case:format-sdist",
        "route_id": route_id,
        "signature": "s",
        "kind": "argument-choice",
        "members": ["argument:format"],
        "shape": {"argument_id": "argument:format", "choice": "sdist"},
    }
    correct_shape = {
        "id": "case:format-shape-present",
        "route_id": route_id,
        "signature": "s",
        "kind": "argument-shape",
        "members": ["argument:format"],
        "shape": {"argument_id": "argument:format"},
    }
    pair_shape = {
        "id": "case:pair-shape",
        "route_id": route_id,
        "signature": "s",
        "kind": "argument-shape",
        "members": ["argument:pair"],
        "shape": {"argument_id": "argument:pair"},
    }
    custom_shape = {
        "id": "case:custom-shape",
        "route_id": route_id,
        "signature": "s",
        "kind": "argument-shape",
        "members": ["argument:custom"],
        "shape": {"argument_id": "argument:custom"},
    }
    candidates = [
        wrong_choice,
        missing_format,
        correct_choice,
        correct_shape,
        pair_shape,
        custom_shape,
    ]
    cases = (
        _case_for_candidate(
            wrong_choice,
            ["build", "wheel", "left", "right"],
        ),
        _case_for_candidate(missing_format, ["build", "source.tar.gz"]),
        _case_for_candidate(
            correct_choice,
            ["build", "source.tar.gz", "sdist", "left", "right"],
        ),
        _case_for_candidate(
            correct_shape,
            ["build", "source.tar.gz", "sdist", "left", "right"],
        ),
        _case_for_candidate(
            pair_shape,
            ["build", "source.tar.gz", "left", "right"],
        ),
        _case_for_candidate(
            custom_shape,
            ["build", "source.tar.gz", "left", "right", "opaque"],
        ),
    )

    findings = _review_findings(
        {"routes": [route], "candidates": candidates, "syntax_complete": True},
        ReviewCatalog("audit-tool", 4, (), cases),
    )

    assert any("case:format-wheel" in item and "omits its positional choice" in item for item in findings)
    assert any("case:format-shape" in item and "omits its positional value" in item for item in findings)
    assert any("case:custom-shape" in item and "omits its positional value" in item for item in findings)
    assert not any("case:format-sdist" in item for item in findings)
    assert not any("case:format-shape-present" in item for item in findings)
    assert not any("case:pair-shape" in item for item in findings)


def test_review_minimum_rejects_inline_values_for_flag_only_requirements():
    route_id = "route:entrypoint:audit-tool/inspect"
    route = {
        "id": route_id,
        "path": ["inspect"],
        "aliases": [],
        "kind": "invocation",
        "actions": [
            {
                "id": "option:required-flag",
                "kind": "option",
                "flags": ["--required"],
                "nargs": 0,
                "minimum_values": 0,
                "required": True,
                "parser_path": ["inspect"],
            },
            {
                "id": "option:group-flag",
                "kind": "option",
                "flags": ["--source"],
                "nargs": 0,
                "minimum_values": 0,
                "required": False,
                "exclusive_group": "source",
                "exclusive_required": True,
                "parser_path": ["inspect"],
            },
        ],
    }
    required = {
        "id": "case:inline-required-flag",
        "route_id": route_id,
        "signature": "s",
        "kind": "minimum",
        "members": [],
        "shape": {"required_options": ["option:required-flag"]},
    }
    group = {
        "id": "case:inline-exclusive-flag",
        "route_id": route_id,
        "signature": "s",
        "kind": "minimum",
        "members": [],
        "shape": {
            "required_exclusive_groups": {
                "source": ["option:group-flag"]
            }
        },
    }
    candidates = [required, group]
    cases = (
        _case_for_candidate(required, ["inspect", "--required=value"]),
        _case_for_candidate(group, ["inspect", "--source=value"]),
    )

    findings = _review_findings(
        {"routes": [route], "candidates": candidates, "syntax_complete": True},
        ReviewCatalog("audit-tool", 4, (), cases),
    )

    assert any(
        "case:inline-required-flag" in item
        and "inline value to flag-only option option:required-flag" in item
        for item in findings
    )
    assert any(
        "case:inline-exclusive-flag" in item
        and "inline value to a flag-only option in required exclusive group source" in item
        for item in findings
    )


def test_review_findings_handles_route_prefixes_single_command_and_missing_minimums():
    prefix = {
        "id": "route:entrypoint:audit-tool/inspect",
        "path": ["inspect"],
        "aliases": [],
        "kind": "route-prefix",
        "actions": [],
    }
    leaf = {
        "id": "route:entrypoint:audit-tool/inspect/detail",
        "path": ["inspect", "detail"],
        "aliases": ["d"],
        "kind": "invocation",
        "actions": [
            {"id": "required-flag", "kind": "option", "flags": ["--required"], "nargs": None, "minimum_values": 1, "required": True, "parser_path": ["inspect", "detail"]},
            {"id": "exclusive-a", "kind": "option", "flags": ["--a"], "nargs": None, "required": False, "exclusive_group": "source", "exclusive_required": True, "parser_path": ["inspect", "detail"]},
            {"id": "exclusive-b", "kind": "option", "flags": ["--b"], "nargs": None, "required": False, "exclusive_group": "source", "exclusive_required": True, "parser_path": ["inspect", "detail"]},
            {"id": "argument:resource", "kind": "argument", "name": "resource", "nargs": None, "minimum_values": 1, "required": True, "choices": ["alpha"], "parser_path": ["inspect", "detail"]},
        ],
    }
    single = {"id": "route:entrypoint:audit-tool/single", "path": [], "aliases": [], "kind": "invocation", "actions": []}
    candidates = [
        {"id": "case:bad-minimum", "route_id": leaf["id"], "signature": "s", "kind": "minimum", "members": [], "shape": {"required_arguments": ["argument:resource"], "required_argument_values": {"argument:resource": 1}, "required_options": ["required-flag"], "required_exclusive_groups": {"source": ["exclusive-a", "exclusive-b"]}}},
        {"id": "case:minimum-group-missing-value", "route_id": leaf["id"], "signature": "s", "kind": "minimum", "members": [], "shape": {"required_arguments": [], "required_options": ["required-flag"], "required_exclusive_groups": {"source": ["exclusive-a", "exclusive-b"]}}},
        {"id": "case:bad-minimum-group-conflict", "route_id": leaf["id"], "signature": "s", "kind": "minimum", "members": [], "shape": {"required_arguments": [], "required_options": ["required-flag"], "required_exclusive_groups": {"source": ["exclusive-a", "exclusive-b"]}}},
        {"id": "case:missing-path", "route_id": leaf["id"], "signature": "s", "kind": "other", "members": [], "shape": {}},
        {"id": "case:alias-good", "route_id": leaf["id"], "signature": "s", "kind": "route-alias", "members": [], "shape": {"alias": "d"}},
        {"id": "case:choice-good", "route_id": leaf["id"], "signature": "s", "kind": "argument-choice", "members": ["argument:resource"], "shape": {"argument_id": "argument:resource", "choice": "alpha"}},
        {"id": "case:unknown-member", "route_id": leaf["id"], "signature": "s", "kind": "other", "members": ["does-not-exist"], "shape": {}},
        {"id": "case:single", "route_id": single["id"], "signature": "s", "kind": "minimum", "members": [], "shape": {}},
    ]
    invocations = {
        "case:bad-minimum": ["inspect", "d"],
        "case:minimum-group-missing-value": ["inspect", "detail", "--required", "value", "--a"],
        "case:bad-minimum-group-conflict": ["inspect", "detail", "--required", "value", "--a", "one", "--b", "two"],
        "case:missing-path": [],
        "case:alias-good": ["inspect", "d", "alpha", "--required", "value", "--a", "source"],
        "case:choice-good": ["inspect", "detail", "alpha", "--required", "value", "--a", "source"],
        "case:unknown-member": ["inspect", "detail", "alpha", "--required", "value", "--a", "source"],
        "case:single": [],
    }
    catalog = ReviewCatalog(
        "audit-tool", 16, (), tuple(
            _case_for_candidate(candidate, invocations[candidate["id"]])
            for candidate in candidates
        ),
    )
    findings = _review_findings(
        {"routes": [prefix, leaf, single], "candidates": candidates, "syntax_complete": True},
        catalog,
    )

    assert any("omits required positional argument" in item for item in findings)
    assert any("omits required option required-flag" in item for item in findings)
    assert any("must supply exactly one option from required group source" in item for item in findings)
    assert any("omits a value for required group source" in item for item in findings)
    assert any("omits its command path" in item for item in findings)
    assert not any("case:alias-good" in item for item in findings)
    assert not any("case:choice-good" in item for item in findings)


def test_review_minimum_required_options_use_lexed_occurrences():
    route = {
        "id": "route:entrypoint:audit-tool/publish",
        "path": ["publish"],
        "aliases": [],
        "kind": "invocation",
        "actions": [
            {
                "id": "option:profile",
                "kind": "option",
                "flags": ["--profile"],
                "nargs": None,
                "minimum_values": 1,
                "required": True,
                "parser_path": [],
                "placement": {"before_verb": True},
            },
            {
                "id": "option:force",
                "kind": "option",
                "flags": ["--force"],
                "nargs": 0,
                "minimum_values": 0,
                "required": True,
                "parser_path": [],
                "placement": {"before_verb": True},
            },
        ],
    }
    candidates = [
        {
            "id": "case:separator-literal",
            "route_id": route["id"],
            "signature": "s",
            "kind": "minimum",
            "members": [],
            "shape": {
                "required_arguments": [],
                "required_options": ["option:force"],
            },
        },
        {
            "id": "case:missing-option-value",
            "route_id": route["id"],
            "signature": "s",
            "kind": "minimum",
            "members": [],
            "shape": {
                "required_arguments": [],
                "required_options": ["option:profile", "option:force"],
            },
        },
    ]
    cases = (
        _case_for_candidate(candidates[0], ["publish", "--", "--force"]),
        _case_for_candidate(candidates[1], ["--profile", "--force", "publish"]),
    )
    findings = _review_findings(
        {"routes": [route], "candidates": candidates, "syntax_complete": True},
        ReviewCatalog("audit-tool", 4, (), cases),
    )
    assert any("omits required option option:force" in item for item in findings)
    assert any("omits a value for required option option:profile" in item for item in findings)


def test_review_minimum_single_command_counts_declared_options():
    route = {
        "id": "route:entrypoint:audit-tool",
        "path": [],
        "aliases": [],
        "kind": "invocation",
        "actions": [
            {
                "id": "option:profile",
                "kind": "option",
                "flags": ["--profile"],
                "nargs": None,
                "minimum_values": 1,
                "required": True,
                "parser_path": [],
                "placement": {"before_verb": False, "after_verb": True},
            }
        ],
    }
    candidate = {
        "id": "case:single-minimum",
        "route_id": route["id"],
        "signature": "s",
        "kind": "minimum",
        "members": [],
        "shape": {
            "required_arguments": [],
            "required_options": ["option:profile"],
        },
    }
    case = _case_for_candidate(candidate, ["--profile", "settings.toml"])
    findings = _review_findings(
        {"routes": [route], "candidates": [candidate], "syntax_complete": True},
        ReviewCatalog("audit-tool", 4, (), (case,)),
    )
    assert not any("omits required option" in item for item in findings)
    assert not any("omits a value for required option" in item for item in findings)


def test_review_findings_matches_nested_path_with_inherited_options():
    prefix = {
        "id": "route:entrypoint:audit-tool/inspect",
        "path": ["inspect"],
        "aliases": ["i"],
        "kind": "route-prefix",
        "actions": [],
    }
    leaf = {
        "id": "route:entrypoint:audit-tool/inspect/detail",
        "path": ["inspect", "detail"],
        "aliases": ["d"],
        "kind": "invocation",
        "actions": [
            {
                "id": "option:global",
                "kind": "option",
                "flags": ["--global"],
                "nargs": None,
                "parser_path": [],
                "placement": {"before_verb": True},
            },
            {
                "id": "option:parent",
                "kind": "option",
                "flags": ["--parent"],
                "nargs": None,
                "parser_path": ["inspect"],
            },
            {
                "id": "argument:parent-arg",
                "kind": "argument",
                "name": "resource",
                "nargs": "?",
                "minimum_values": 0,
                "parser_path": ["inspect"],
                "before_nested_subcommand": True,
            },
        ],
    }
    candidate = {
        "id": "case:nested-path",
        "route_id": leaf["id"],
        "signature": "s",
        "kind": "other",
        "members": [],
        "shape": {},
    }
    case = _case_for_candidate(
        candidate,
        ["--global", "profile.toml", "i", "--parent", "value", "detail"],
    )
    findings = _review_findings(
        {"routes": [prefix, leaf], "candidates": [candidate], "syntax_complete": True},
        ReviewCatalog("audit-tool", 4, (), (case,)),
    )
    assert not any("omits its command path" in item for item in findings)


@pytest.mark.parametrize(
    ("nargs", "invocation", "command_path_missing"),
    (
        (None, ["inspect", "resource", "detail"], False),
        (None, ["inspect", "detail"], True),
        ("?", ["inspect", "detail"], False),
        ("?", ["inspect", "resource", "detail"], False),
        ("*", ["inspect", "resource", "detail"], False),
        ("+", ["inspect", "resource", "detail"], False),
        ("...", ["inspect", "detail"], False),
    ),
)
def test_review_route_lexer_accounts_for_parent_positionals(
    nargs, invocation, command_path_missing
):
    parser = argparse.ArgumentParser(prog="audit-tool")
    root_commands = parser.add_subparsers(dest="command", required=True)
    inspect_parser = root_commands.add_parser("inspect")
    inspect_parser.add_argument(
        "resource",
        nargs=argparse.REMAINDER if nargs == "..." else nargs,
    )
    nested_commands = inspect_parser.add_subparsers(dest="action", required=True)
    nested_commands.add_parser("detail")
    try:
        parser.parse_args(invocation)
    except SystemExit:
        parser_accepts_invocation = False
    else:
        parser_accepts_invocation = True
    assert parser_accepts_invocation is not command_path_missing

    route_id = "route:entrypoint:audit-tool/inspect/detail"
    prefix = {
        "id": "route:entrypoint:audit-tool/inspect",
        "path": ["inspect"],
        "aliases": [],
        "kind": "route-prefix",
        "actions": [],
    }
    route = {
        "id": route_id,
        "path": ["inspect", "detail"],
        "aliases": [],
        "kind": "invocation",
        "actions": [
            {
                "id": "argument:resource",
                "kind": "argument",
                "name": "resource",
                "nargs": nargs,
                "minimum_values": 1 if nargs in (None, "+") else 0,
                "parser_path": ["inspect"],
                "before_nested_subcommand": True,
            }
        ],
    }
    candidate = {
        "id": "case:nested-positional",
        "route_id": route_id,
        "signature": "s",
        "kind": "other",
        "members": [],
        "shape": {},
    }
    case = _case_for_candidate(candidate, invocation)
    findings = _review_findings(
        {"routes": [prefix, route], "candidates": [candidate], "syntax_complete": True},
        ReviewCatalog("audit-tool", 4, (), (case,)),
    )
    assert any("omits its command path" in item for item in findings) is command_path_missing


def test_review_route_lexer_rejects_unexpected_root_positional_before_verb():
    route = {
        "id": "route:entrypoint:audit-tool/inspect",
        "path": ["inspect"],
        "aliases": [],
        "kind": "invocation",
        "actions": [],
    }
    candidate = {
        "id": "case:root-route",
        "route_id": route["id"],
        "signature": "s",
        "kind": "other",
        "members": [],
        "shape": {},
    }
    case = _case_for_candidate(candidate, ["unregistered", "inspect"])
    findings = _review_findings(
        {"routes": [route], "candidates": [candidate], "syntax_complete": True},
        ReviewCatalog("audit-tool", 4, (), (case,)),
    )
    assert any("omits its command path" in item for item in findings)


def test_review_findings_does_not_match_command_names_consumed_as_option_values():
    prefix = {
        "id": "route:entrypoint:audit-tool/inspect",
        "path": ["inspect"],
        "aliases": [],
        "kind": "route-prefix",
        "actions": [],
    }
    leaf = {
        "id": "route:entrypoint:audit-tool/inspect/detail",
        "path": ["inspect", "detail"],
        "aliases": ["d"],
        "kind": "invocation",
        "actions": [
            {
                "id": "option:profile",
                "kind": "option",
                "flags": ["--profile"],
                "nargs": None,
                "parser_path": [],
                "placement": {"before_verb": True},
            }
        ],
    }
    candidate = {
        "id": "case:detail-alias",
        "route_id": leaf["id"],
        "signature": "s",
        "kind": "route-alias",
        "members": [],
        "shape": {"alias": "d"},
    }
    case = _case_for_candidate(candidate, ["--profile", "inspect", "d"])
    findings = _review_findings(
        {"routes": [prefix, leaf], "candidates": [candidate], "syntax_complete": True},
        ReviewCatalog("audit-tool", 4, (), (case,)),
    )
    assert any("omits its command path" in item for item in findings)
    assert any("omits its command alias" in item for item in findings)


@pytest.mark.parametrize(
    ("nargs", "invocation", "has_route"),
    (
        (None, ["--value", "inspect", "detail", "d"], False),
        (None, ["--value=inspect", "inspect", "detail", "d"], True),
        (None, ["--value", "--unknown", "inspect", "d"], False),
        (None, ["--value", "-3", "inspect", "d"], True),
        (None, ["--value", "-", "inspect", "d"], True),
        (None, ["--value"], False),
        (0, ["--flag", "inspect", "d"], True),
        (2, ["--pair", "first", "second", "inspect", "d"], True),
        (2, ["--pair=first", "inspect", "detail", "d"], False),
        ("?", ["--maybe", "inspect", "detail", "d"], False),
        ("?", ["--maybe=value", "inspect", "detail", "d"], True),
        ("*", ["--many", "inspect", "detail", "d"], False),
        ("*", ["--many=value", "inspect", "detail", "d"], True),
        ("+", ["--some", "inspect", "detail", "d"], False),
        ("...", ["--rest", "inspect", "detail", "d"], False),
        ("A...", ["--parser", "inspect", "detail", "d"], False),
        (None, ["--", "--", "inspect", "detail", "d"], False),
        ("odd", ["--odd", "inspect", "d"], True),
        (None, ["--", "inspect", "d"], True),
    ),
)
def test_review_route_lexer_skips_option_values_and_stops_at_delimiter(
    nargs, invocation, has_route
):
    flag_by_nargs = {
        None: "--value",
        0: "--flag",
        2: "--pair",
        "?": "--maybe",
        "*": "--many",
        "+": "--some",
        "...": "--rest",
        "A...": "--parser",
        "odd": "--odd",
    }
    prefix = {
        "id": "route:entrypoint:audit-tool/inspect",
        "path": ["inspect"],
        "aliases": [],
        "kind": "route-prefix",
        "parser_settings": [
            {
                "parser_path": [],
                "allow_abbrev": False,
                "prefix_chars": "-",
                "fromfile_prefix_chars": None,
                **_argparse_negative_number_settings()[1],
            }
        ],
        "actions": [],
    }
    leaf = {
        "id": "route:entrypoint:audit-tool/inspect/detail",
        "path": ["inspect", "detail"],
        "aliases": ["d"],
        "kind": "invocation",
        "parser_settings": prefix["parser_settings"],
        "actions": [
            {
                "id": "option:value",
                "kind": "option",
                "flags": [flag_by_nargs[nargs]],
                "nargs": nargs,
                "parser_path": [],
                "placement": {"before_verb": True},
            }
        ],
    }
    candidate = {
        "id": "case:detail-alias",
        "route_id": leaf["id"],
        "signature": "s",
        "kind": "route-alias",
        "members": [],
        "shape": {"alias": "d"},
    }
    case = _case_for_candidate(candidate, invocation)
    findings = _review_findings(
        {"routes": [prefix, leaf], "candidates": [candidate], "syntax_complete": True},
        ReviewCatalog("audit-tool", 4, (), (case,)),
    )
    missing_path = any("omits its command path" in item for item in findings)
    assert missing_path is not has_route


def test_review_route_lexer_does_not_abbreviate_short_options_or_ignore_scope():
    route_id = "route:entrypoint:audit-tool/inspect"
    route = {
        "id": route_id,
        "path": ["inspect"],
        "aliases": [],
        "kind": "invocation",
        "parser_settings": [
            {
                "parser_path": ["inspect"],
                "allow_abbrev": True,
                "prefix_chars": "-",
                "fromfile_prefix_chars": None,
            }
        ],
        "actions": [
            {
                "id": "option:quiet",
                "kind": "option",
                "flags": ["-q"],
                "nargs": 0,
                "required": False,
                "parser_path": ["inspect"],
                "placement": {"before_verb": False},
            }
        ],
    }
    candidate = {
        "id": "case:quiet-spelling",
        "route_id": route_id,
        "signature": "sha256:quiet",
        "kind": "option-spelling",
        "members": ["option:quiet"],
        "shape": {"spelling": "-q"},
    }
    findings = _findings_for_invocation(candidate, route, ["inspect", "-"])
    assert any("omits its reviewed option spelling" in item for item in findings)

    route["actions"][0]["placement"]["before_verb"] = False
    route["actions"][0]["parser_path"] = ["inspect"]
    findings = _findings_for_invocation(candidate, route, ["-q", "inspect"])
    assert any("omits its command path" in item for item in findings)


def test_review_route_lexer_uses_entrypoint_abbreviation_policy_when_parser_settings_are_missing():
    route_id = "route:entrypoint:audit-tool/inspect"
    route = {
        "id": route_id,
        "path": ["inspect"],
        "aliases": [],
        "kind": "invocation",
        "actions": [
            {
                "id": "option:profile",
                "kind": "option",
                "flags": ["--profile"],
                "nargs": None,
                "required": False,
                "parser_path": ["inspect"],
            }
        ],
    }
    candidate = {
        "id": "case:profile-spelling",
        "route_id": route_id,
        "signature": "sha256:profile",
        "kind": "option-spelling",
        "members": ["option:profile"],
        "shape": {"spelling": "--profile"},
    }
    findings = _findings_for_invocation(
        candidate, route, ["inspect", "--pro", "work"], allow_abbrev=False
    )
    assert any("omits its reviewed option spelling" in item for item in findings)


def test_review_route_lexer_does_not_accept_options_after_a_delimiter():
    route_id = "route:entrypoint:audit-tool/inspect"
    route = {
        "id": route_id,
        "path": ["inspect"],
        "aliases": [],
        "kind": "invocation",
        "actions": [
            {
                "id": "option:force",
                "kind": "option",
                "flags": ["--force"],
                "nargs": 0,
                "required": False,
                "parser_path": ["inspect"],
            }
        ],
    }
    candidate = {
        "id": "case:force-spelling",
        "route_id": route_id,
        "signature": "sha256:force",
        "kind": "option-spelling",
        "members": ["option:force"],
        "shape": {"spelling": "--force"},
    }
    findings = _findings_for_invocation(
        candidate, route, ["inspect", "--", "--force"]
    )
    assert any("omits its reviewed option spelling" in item for item in findings)


def test_review_route_lexer_stops_at_fixed_and_option_boundaries():
    prefix = {
        "id": "route:entrypoint:audit-tool/inspect",
        "path": ["inspect"],
        "aliases": [],
        "kind": "route-prefix",
        "actions": [],
    }
    route_id = "route:entrypoint:audit-tool/inspect/detail"
    route = {
        "id": route_id,
        "path": ["inspect", "detail"],
        "aliases": [],
        "kind": "invocation",
        "actions": [
            {
                "id": "option:pair",
                "kind": "option",
                "flags": ["--pair"],
                "nargs": 2,
                "required": False,
                "parser_path": ["inspect"],
            }
        ],
    }
    candidate = {
        "id": "case:pair-route",
        "route_id": route_id,
        "signature": "sha256:pair",
        "kind": "other",
        "members": [],
        "shape": {},
    }
    assert _findings_for_invocation(
        candidate,
        route,
        ["inspect", "--pair", "one", "two", "detail"],
        route_prefixes=(prefix,),
    ) == []

    route["actions"] = [
        {
            "id": "option:many",
            "kind": "option",
            "flags": ["--many"],
            "nargs": "*",
            "required": False,
            "parser_path": ["inspect"],
        },
        {
            "id": "option:stop",
            "kind": "option",
            "flags": ["--stop"],
            "nargs": 0,
            "required": False,
            "parser_path": ["inspect"],
        },
    ]
    assert _findings_for_invocation(
        candidate,
        route,
        ["inspect", "--many", "one", "--stop", "detail"],
        route_prefixes=(prefix,),
    ) == []


def test_review_minimum_checks_the_exact_number_of_required_argument_values():
    route_id = "route:entrypoint:audit-tool/inspect"
    route = {
        "id": route_id,
        "path": ["inspect"],
        "aliases": [],
        "kind": "invocation",
        "actions": [
            {
                "id": "argument:pair",
                "kind": "argument",
                "name": "pair",
                "nargs": 2,
                "minimum_values": 2,
                "required": True,
                "parser_path": ["inspect"],
            }
        ],
    }
    candidate = {
        "id": "case:pair-minimum",
        "route_id": route_id,
        "signature": "sha256:pair-minimum",
        "kind": "minimum",
        "members": [],
        "shape": {
            "required_arguments": ["argument:pair"],
            "required_argument_values": {"argument:pair": 2},
        },
    }
    assert _findings_for_invocation(candidate, route, ["inspect", "one", "two"]) == []
    findings = _findings_for_invocation(candidate, route, ["inspect", "one"])
    assert any("omits required positional argument" in item for item in findings)
    inconsistent_route = {
        **route,
        "actions": [{**route["actions"][0], "minimum_values": 0}],
    }
    inconsistent_findings = _findings_for_invocation(
        candidate, inconsistent_route, ["inspect", "one"]
    )
    assert any(
        "invalid positional value shape" in item for item in inconsistent_findings
    )
    assert not any(
        "omits required positional argument" in item
        for item in inconsistent_findings
    )


def test_review_minimum_reports_unknown_required_options_without_false_certification():
    route_id = "route:entrypoint:audit-tool/inspect"
    route = {
        "id": route_id,
        "path": ["inspect"],
        "aliases": [],
        "kind": "invocation",
        "actions": [
            {
                "id": "option:present",
                "kind": "option",
                "flags": ["--present"],
                "nargs": 0,
                "required": True,
                "parser_path": ["inspect"],
            }
        ],
    }
    candidate = {
        "id": "case:unknown-required-option",
        "route_id": route_id,
        "signature": "sha256:unknown-option",
        "kind": "minimum",
        "members": [],
        "shape": {
            "required_arguments": ["argument:missing"],
            "required_options": ["option:missing"],
        },
    }
    findings = _findings_for_invocation(
        candidate, route, ["inspect", "--present"]
    )
    assert any("references unknown required option option:missing" in item for item in findings)
    assert any(
        "references unknown required argument argument:missing" in item
        for item in findings
    )
    known_required = {
        **candidate,
        "shape": {
            "required_arguments": [],
            "required_options": ["option:present"],
        },
    }
    known_findings = _findings_for_invocation(
        known_required, route, ["inspect", "--present"]
    )
    assert not any(
        "references unknown required option" in item for item in known_findings
    )


def test_review_route_alias_check_handles_an_empty_command_position_list():
    route = {
        "id": "route:entrypoint:audit-tool",
        "path": [],
        "aliases": [],
        "kind": "invocation",
        "actions": [],
    }
    candidate = {
        "id": "case:impossible-alias",
        "route_id": route["id"],
        "signature": "sha256:alias",
        "kind": "route-alias",
        "members": [],
        "shape": {"alias": "alternate"},
    }
    findings = _findings_for_invocation(candidate, route, [])
    assert any("omits its command alias" in item for item in findings)


def test_review_route_lexer_accepts_unambiguous_long_option_abbreviation():
    prefix = {
        "id": "route:entrypoint:audit-tool/inspect",
        "path": ["inspect"],
        "aliases": [],
        "kind": "route-prefix",
        "actions": [],
    }
    leaf = {
        "id": "route:entrypoint:audit-tool/inspect/detail",
        "path": ["inspect", "detail"],
        "aliases": ["d"],
        "kind": "invocation",
        "actions": [
            {
                "id": "option:profile",
                "kind": "option",
                "flags": ["--profile"],
                "nargs": None,
                "parser_path": [],
                "placement": {"before_verb": True},
            }
        ],
    }
    candidate = {
        "id": "case:detail-alias",
        "route_id": leaf["id"],
        "signature": "s",
        "kind": "route-alias",
        "members": [],
        "shape": {"alias": "d"},
    }
    case = _case_for_candidate(candidate, ["--prof", "inspect", "d"])
    findings = _review_findings(
        {
            "entrypoint": {"allow_abbrev": True},
            "routes": [prefix, leaf],
            "candidates": [candidate],
            "syntax_complete": True,
        },
        ReviewCatalog("audit-tool", 4, (), (case,)),
    )
    assert any("omits its command path" in item for item in findings)


@pytest.mark.parametrize(
    ("root_abbrev", "verb_abbrev", "expected_missing_path"),
    ((False, True, False), (True, False, True)),
)
def test_review_route_lexer_uses_abbreviation_policy_at_parser_depth(
    root_abbrev: bool,
    verb_abbrev: bool,
    expected_missing_path: bool,
):
    prefix = {
        "id": "route:entrypoint:audit-tool/inspect",
        "path": ["inspect"],
        "aliases": [],
        "kind": "route-prefix",
        "actions": [],
        "parser_settings": [
            {
                "parser_path": [],
                "allow_abbrev": root_abbrev,
                "prefix_chars": "-",
                "fromfile_prefix_chars": None,
            }
        ],
    }
    leaf = {
        "id": "route:entrypoint:audit-tool/inspect/detail",
        "path": ["inspect", "detail"],
        "aliases": ["d"],
        "kind": "invocation",
        "actions": [
            {
                "id": "option:profile",
                "kind": "option",
                "flags": ["--profile"],
                "nargs": None,
                "parser_path": ["inspect"],
            }
        ],
        "parser_settings": [
            {
                "parser_path": [],
                "allow_abbrev": root_abbrev,
                "prefix_chars": "-",
                "fromfile_prefix_chars": None,
            },
            {
                "parser_path": ["inspect"],
                "allow_abbrev": verb_abbrev,
                "prefix_chars": "-",
                "fromfile_prefix_chars": None,
            },
            {
                "parser_path": ["inspect", "detail"],
                "allow_abbrev": False,
                "prefix_chars": "-",
                "fromfile_prefix_chars": None,
            },
        ],
    }
    candidate = {
        "id": "case:detail-alias",
        "route_id": leaf["id"],
        "signature": "s",
        "kind": "route-alias",
        "members": [],
        "shape": {"alias": "d"},
    }
    case = _case_for_candidate(
        candidate,
        ["inspect", "--prof", "work", "d"],
    )

    findings = _review_findings(
        {
            "entrypoint": {"allow_abbrev": root_abbrev},
            "routes": [prefix, leaf],
            "candidates": [candidate],
            "syntax_complete": True,
        },
        ReviewCatalog("audit-tool", 4, (), (case,)),
    )

    assert any("omits its command path" in item for item in findings) is expected_missing_path


def test_review_accepts_abbreviation_from_exported_registered_parser_settings():
    registry = CliRegistry(
        IDENTITY,
        prog="audit-tool",
        description="audit CLI parser settings",
        allow_abbrev=False,
    )

    def configure(parser: argparse.ArgumentParser) -> None:
        parser.allow_abbrev = True

    registry.register(
        VerbSpec(
            "inspect",
            description="inspect one item",
            options=(OptionSpec(("--profile",), "select a profile"),),
            configure=configure,
            handler=lambda *_: 0,
        )
    )
    app = registry.build()
    assert app.command_parsers["inspect"].parse_args(["--prof", "work"]).profile == "work"

    surface = export_cli_surface(app)
    cases = tuple(
        _case_for_candidate(
            candidate,
            ["inspect", "--prof", "work"]
            if candidate["kind"] == "minimum"
            else _candidate_argv(candidate, surface),
        )
        for candidate in surface["candidates"]
    )

    assert not _review_findings(
        surface,
        ReviewCatalog("audit-tool", 4, (), cases),
    )


@pytest.mark.parametrize(
    "invocation",
    (["--", "inspect", "d"], ["inspect", "--", "d"]),
)
def test_review_route_lexer_honors_option_terminator_at_parser_depth(invocation):
    parser = argparse.ArgumentParser(prog="audit-tool")
    root_commands = parser.add_subparsers(dest="command", required=True)
    inspect_parser = root_commands.add_parser("inspect")
    nested_commands = inspect_parser.add_subparsers(dest="action", required=True)
    nested_commands.add_parser("detail", aliases=["d"])
    assert parser.parse_args(invocation)

    prefix = {
        "id": "route:entrypoint:audit-tool/inspect",
        "path": ["inspect"],
        "aliases": [],
        "kind": "route-prefix",
        "actions": [],
    }
    route = {
        "id": "route:entrypoint:audit-tool/inspect/detail",
        "path": ["inspect", "detail"],
        "aliases": ["d"],
        "kind": "invocation",
        "actions": [],
    }
    candidate = {
        "id": "case:detail-alias",
        "route_id": route["id"],
        "signature": "s",
        "kind": "route-alias",
        "members": [],
        "shape": {"alias": "d"},
    }
    case = _case_for_candidate(candidate, invocation)
    findings = _review_findings(
        {"routes": [prefix, route], "candidates": [candidate], "syntax_complete": True},
        ReviewCatalog("audit-tool", 4, (), (case,)),
    )
    assert not any("omits its command path" in item for item in findings)
    assert not any("omits its command alias" in item for item in findings)


def test_review_minimum_option_before_terminator_reports_missing_value():
    prefix = {
        "id": "route:entrypoint:audit-tool/inspect",
        "path": ["inspect"],
        "aliases": [],
        "kind": "route-prefix",
        "actions": [],
    }
    route = {
        "id": "route:entrypoint:audit-tool/inspect/detail",
        "path": ["inspect", "detail"],
        "aliases": ["d"],
        "kind": "invocation",
        "actions": [
            {
                "id": "option:value",
                "kind": "option",
                "flags": ["--value"],
                "nargs": None,
                "minimum_values": 1,
                "required": True,
                "parser_path": [],
                "placement": {"before_verb": True},
            }
        ],
    }
    candidate = {
        "id": "case:minimum-value",
        "route_id": route["id"],
        "signature": "s",
        "kind": "minimum",
        "members": [],
        "shape": {
            "required_arguments": [],
            "required_options": ["option:value"],
        },
    }
    invocation = ["--value", "--", "inspect", "d"]
    case = _case_for_candidate(candidate, invocation)
    findings = _review_findings(
        {"routes": [prefix, route], "candidates": [candidate], "syntax_complete": True},
        ReviewCatalog("audit-tool", 4, (), (case,)),
    )
    assert not any("omits its command path" in item for item in findings)
    assert any("omits a value for required option option:value" in item for item in findings)


def test_review_route_lexer_does_not_certify_ambiguous_long_option_abbreviation():
    prefix = {
        "id": "route:entrypoint:audit-tool/inspect",
        "path": ["inspect"],
        "aliases": [],
        "kind": "route-prefix",
        "actions": [],
    }
    leaf = {
        "id": "route:entrypoint:audit-tool/inspect/detail",
        "path": ["inspect", "detail"],
        "aliases": ["d"],
        "kind": "invocation",
        "actions": [
            {
                "id": "option:profile",
                "kind": "option",
                "flags": ["--profile"],
                "nargs": None,
                "parser_path": [],
                "placement": {"before_verb": True},
            },
            {
                "id": "option:project",
                "kind": "option",
                "flags": ["--project"],
                "nargs": None,
                "parser_path": [],
                "placement": {"before_verb": True},
            },
        ],
    }
    candidate = {
        "id": "case:detail-alias",
        "route_id": leaf["id"],
        "signature": "s",
        "kind": "route-alias",
        "members": [],
        "shape": {"alias": "d"},
    }
    case = _case_for_candidate(candidate, ["--pro", "inspect", "d"])
    findings = _review_findings(
        {
            "entrypoint": {"allow_abbrev": True},
            "routes": [prefix, leaf],
            "candidates": [candidate],
            "syntax_complete": True,
        },
        ReviewCatalog("audit-tool", 4, (), (case,)),
    )
    assert any("omits its command path" in item for item in findings)


@pytest.mark.parametrize(
    ("actions", "invocation", "allow_abbrev", "path_missing"),
    (
        (
            (
                {"flags": ["--maybe"], "nargs": "?", "parser_path": []},
                {"flags": ["--switch"], "nargs": 0, "parser_path": []},
            ),
            ["--maybe", "--switch", "inspect", "detail"],
            False,
            False,
        ),
        (
            (
                {"flags": ["--pair"], "nargs": 2, "parser_path": []},
                {"flags": ["--next"], "nargs": 0, "parser_path": []},
            ),
            ["--pair", "first", "--next", "inspect", "detail"],
            False,
            False,
        ),
        (({"flags": ["--nested"], "nargs": None, "parser_path": ["inspect"]},), ["--nested", "inspect", "detail"], False, True),
        (({"flags": ["--profile", "--progress"], "nargs": None, "parser_path": [], "placement": {"before_verb": True}},), ["--pro", "inspect", "detail"], True, True),
    ),
)
def test_review_route_lexer_rejects_ambiguous_or_out_of_scope_options(
    actions, invocation, allow_abbrev, path_missing
):
    route_id = "route:entrypoint:audit-tool/inspect/detail"
    prefix = {
        "id": "route:entrypoint:audit-tool/inspect",
        "path": ["inspect"],
        "aliases": [],
        "kind": "route-prefix",
        "actions": [],
    }
    option_actions = [
        {
            "id": f"option:{index}",
            "kind": "option",
            "choices": None,
            "const": None,
            "const_choice_check_on_omission": False,
            "type": None,
            "placement": {"before_verb": not action.get("parser_path")},
            **action,
        }
        for index, action in enumerate(actions)
    ]
    route = {
        "id": route_id,
        "path": ["inspect", "detail"],
        "aliases": [],
        "kind": "invocation",
        "actions": option_actions,
    }
    candidate = {
        "id": "case:lexer",
        "route_id": route_id,
        "signature": "s",
        "kind": "other",
        "members": [],
        "shape": {},
    }
    case = _case_for_candidate(candidate, invocation)
    findings = _review_findings(
        {
            "entrypoint": {"allow_abbrev": allow_abbrev},
            "routes": [prefix, route],
            "candidates": [candidate],
            "syntax_complete": True,
        },
        ReviewCatalog("audit-tool", 4, (), (case,)),
    )
    assert any("omits its command path" in item for item in findings) is path_missing


def test_review_findings_handles_nested_path_without_prefix_route_or_complete_match():
    leaf = {
        "id": "route:entrypoint:audit-tool/inspect/detail",
        "path": ["inspect", "detail", "run"],
        "aliases": [],
        "kind": "invocation",
        "actions": [],
    }
    candidate = {
        "id": "case:incomplete-nested-path",
        "route_id": leaf["id"],
        "signature": "s",
        "kind": "other",
        "members": [],
        "shape": {},
    }
    case = _case_for_candidate(candidate, ["inspect", "detail"])
    findings = _review_findings(
        {"routes": [leaf], "candidates": [candidate], "syntax_complete": True},
        ReviewCatalog("audit-tool", 4, (), (case,)),
    )
    assert any("omits its command path" in item for item in findings)


def test_prepare_rejects_catalog_for_different_registered_cli(tmp_path):
    app, _surface, review, manifest, spec = _make_files(tmp_path, active=False)
    review.write_text(
        review.read_text(encoding="utf-8").replace('cli_id = "audit-tool"', 'cli_id = "other-tool"'),
        encoding="utf-8",
    )
    with pytest.raises(ReviewCatalogError, match="does not match registered executable"):
        check_cli_surface(app, review_path=review, manifest_path=manifest, spec_path=spec)


def test_check_cli_surface_reports_unreadable_consumer_spec(tmp_path, monkeypatch):
    import cli_extended.review as review_module

    app, _surface, review, manifest, spec = _make_files(tmp_path, active=False)
    catalog = load_cli_review_catalog(review)
    rendered_spec = spec.read_text(encoding="utf-8")
    prepared = (catalog, {}, "{}\n", rendered_spec, [])
    monkeypatch.setattr(review_module, "_prepare", lambda *_args, **_kwargs: prepared)

    report = review_module.check_cli_surface(
        app,
        review_path=review,
        manifest_path=manifest,
        spec_path=tmp_path / "missing-spec.md",
    )
    assert any("cannot check generated CLI spec" in finding for finding in report.findings)


def test_case_test_helper_reports_empty_ids_and_handles_non_pytest_items():
    case_without_ids = _one_case_catalog().cases[0]
    empty_id = ReviewCase(
        "", "active", "accept", "sig", "reviewed", (), True, 0,
        "", "", (), True, (), "",
    )
    no_tests = ReviewCase(
        case_without_ids.case_id, "active", "accept", "sig", "reviewed", (),
        True, 0, "", "", (), True, (), "",
    )
    with pytest.raises(AssertionError, match="empty case ID"):
        assert_cli_case_tests([], ReviewCatalog("audit-tool", 4, (), (empty_id,)))
    with pytest.raises(AssertionError, match="has no test_ids"):
        assert_cli_case_tests([], ReviewCatalog("audit-tool", 4, (), (no_tests,)))

    class CollectedButNotPytest:
        nodeid = "test_file.py::test_case"

    assert _statically_skipped(CollectedButNotPytest()) is False


def test_case_test_helper_recognizes_keyword_skipif_condition():
    catalog = _one_case_catalog()
    node = catalog.cases[0].test_ids[0]
    item = _Item(
        node,
        (_Marker("cli_case", catalog.cases[0].case_id), _Marker("skipif", condition=True)),
    )
    with pytest.raises(AssertionError, match="statically skipped"):
        assert_cli_case_tests([item], catalog)


def test_sync_is_idempotent_preserves_outside_bytes_and_never_rewrites_catalog(tmp_path):
    app, _surface, review, manifest, spec = _make_files(tmp_path, active=False)
    original_review = review.read_bytes()
    original = spec.read_bytes()
    prefix = original[: original.index(SURFACE_START_MARKER.encode())]
    suffix = original[original.index(SURFACE_END_MARKER.encode()) + len(SURFACE_END_MARKER) :]

    first = sync_cli_surface(app, review_path=review, manifest_path=manifest, spec_path=spec)
    synced = spec.read_bytes()
    second = sync_cli_surface(app, review_path=review, manifest_path=manifest, spec_path=spec)

    assert first.passed is False
    assert any("semantic review is pending" in item for item in first.findings)
    assert second.findings == first.findings
    assert spec.read_bytes() == synced
    assert synced.startswith(prefix)
    assert synced.endswith(suffix)
    assert b"\r\nAfter\r\n" in synced
    assert review.read_bytes() == original_review
    assert json.loads(manifest.read_text(encoding="utf-8"))["schema_version"] == 7
    assert SURFACE_START_MARKER.encode() in synced
    assert SURFACE_END_MARKER.encode() in synced


def test_sync_preserves_and_displays_stale_interaction_reviews(tmp_path):
    app = _build_cli()
    route_id = "route:entrypoint:audit-tool/inspect"
    case_id = f"case:{route_id}/interaction:removed-options/old-flag"
    review = tmp_path / "cli-review.toml"
    review.write_text(
        "\n".join(
            (
                "schema_version = 1",
                'cli_id = "audit-tool"',
                "max_candidates = 128",
                "",
                "[[interaction_groups]]",
                'id = "removed-options"',
                f'route_id = "{route_id}"',
                "[[interaction_groups.combinations]]",
                'id = "old-flag"',
                'option_ids = ["option:route:entrypoint:audit-tool/inspect/--removed"]',
                "",
                "[[cases]]",
                f'id = "{case_id}"',
                'state = "active"',
                'decision = "refuse"',
                'reviewed_signature = "sha256:old"',
                'rationale = "The removed option was rejected."',
                'invocation = ["inspect", "--removed"]',
                "expected_exit_status = 2",
                "effects = []",
                'test_ids = ["tests/test_review.py::test_stale_interaction"]',
                "",
            )
        ),
        encoding="utf-8",
    )
    manifest = tmp_path / "cli-manifest.json"
    spec = tmp_path / "SPEC.md"
    spec.write_text(
        f"# Spec\n\n{SURFACE_START_MARKER}\nold\n{SURFACE_END_MARKER}\n",
        encoding="utf-8",
    )
    original_catalog = review.read_bytes()

    report = sync_cli_surface(
        app, review_path=review, manifest_path=manifest, spec_path=spec
    )

    assert not report.passed
    assert any("stale interaction catalog reference" in item for item in report.findings)
    assert any(f"stale semantic case needs explicit retirement: {case_id}" in item for item in report.findings)
    assert review.read_bytes() == original_catalog
    assert manifest.exists()
    generated_spec = spec.read_text(encoding="utf-8")
    assert "### Removed or stale semantic cases" in generated_spec
    assert case_id in generated_spec
    assert "### Interaction references to repair" in generated_spec
    assert "names unknown options" in generated_spec
    assert json.loads(manifest.read_text(encoding="utf-8"))["interaction_issues"]


@pytest.mark.parametrize(
    ("collision", "expected_roles"),
    (
        ("review-manifest", "manifest and review catalog"),
        ("review-spec", "specification and review catalog"),
        ("manifest-spec", "specification and manifest"),
    ),
)
def test_sync_and_check_reject_colliding_paths_without_writing(
    tmp_path, collision, expected_roles
):
    app, _surface, review, manifest, spec = _make_files(tmp_path, active=False)
    manifest.write_text("{}\n", encoding="utf-8")
    original_files = {
        path: path.read_bytes() for path in (review, manifest, spec)
    }
    if collision == "review-manifest":
        manifest = review
    elif collision == "review-spec":
        spec = review
    else:
        spec = manifest

    for operation in (sync_cli_surface, check_cli_surface):
        with pytest.raises(SurfaceSpecError, match="must resolve to distinct files") as exc:
            operation(
                app,
                review_path=review,
                manifest_path=manifest,
                spec_path=spec,
            )
        assert expected_roles in str(exc.value)

    assert {path: path.read_bytes() for path in original_files} == original_files


@pytest.mark.parametrize("alias_kind", ("symlink", "hardlink"))
def test_sync_rejects_file_alias_of_review_catalog(tmp_path, alias_kind):
    app, _surface, review, _manifest, spec = _make_files(tmp_path, active=False)
    manifest_alias = tmp_path / "review-alias.toml"
    if alias_kind == "symlink":
        manifest_alias.symlink_to(review)
    else:
        manifest_alias.hardlink_to(review)
    original_review = review.read_bytes()
    original_spec = spec.read_bytes()

    with pytest.raises(SurfaceSpecError, match="manifest and review catalog"):
        sync_cli_surface(
            app,
            review_path=review,
            manifest_path=manifest_alias,
            spec_path=spec,
        )

    assert review.read_bytes() == original_review
    assert spec.read_bytes() == original_spec
    assert manifest_alias.is_symlink() is (alias_kind == "symlink")


def test_sync_rejects_unresolvable_artifact_path(tmp_path):
    app, _surface, review, _manifest, spec = _make_files(tmp_path, active=False)
    manifest_loop = tmp_path / "manifest-loop"
    manifest_loop.symlink_to(manifest_loop.name)

    with pytest.raises(SurfaceSpecError, match="cannot resolve manifest path"):
        sync_cli_surface(
            app,
            review_path=review,
            manifest_path=manifest_loop,
            spec_path=spec,
        )


def test_sync_template_and_markdown_make_candidates_reviewable(tmp_path):
    app, surface, review, manifest, spec = _make_files(tmp_path, active=False)
    catalog = ReviewCatalog("audit-tool", 128, (), ())
    template = render_cli_review_template(surface, catalog)
    markdown = render_cli_surface_markdown(surface, catalog)

    assert "state = \"pending\"" in template
    assert "expected_exit_status = 0" not in template
    assert "Surface IDs / shape" in markdown
    assert "Test IDs prove collection/linkage only" in markdown
    assert "UNREVIEWED" in markdown
    changed_surface = dict(surface)
    changed_surface["candidates"] = [dict(surface["candidates"][0])]
    changed_surface["candidates"][0]["signature"] = "sha256:changed"
    changed_case = ReviewCase(
        case_id=surface["candidates"][0]["id"],
        state="active",
        decision="accept",
        reviewed_signature="sha256:old",
        rationale="old decision",
        invocation=(),
        invocation_declared=True,
        expected_exit_status=0,
        expected_stdout_contains="",
        expected_stderr_contains="",
        effects=(),
        effects_declared=True,
        test_ids=("tests/test_review.py::test_surface_check_accepts_synced_outputs",),
        retirement_reason="",
    )
    changed_catalog = ReviewCatalog("audit-tool", 128, (), (changed_case,))
    assert "Current generated signature: sha256:changed" in render_cli_review_template(
        changed_surface, changed_catalog
    )
    sync_cli_surface(app, review_path=review, manifest_path=manifest, spec_path=spec)
    assert template


def test_surface_sync_rejects_missing_duplicate_reversed_and_nested_markers(tmp_path):
    app, _surface, review, manifest, spec = _make_files(tmp_path, active=False)
    contents = (
        "# no markers\n",
        f"{SURFACE_START_MARKER}\n{SURFACE_START_MARKER}\n{SURFACE_END_MARKER}\n",
        f"{SURFACE_END_MARKER}\n{SURFACE_START_MARKER}\n",
        f"{SURFACE_START_MARKER}\n{SURFACE_END_MARKER}\n{SURFACE_END_MARKER}\n",
    )
    for content in contents:
        spec.write_text(content, encoding="utf-8")
        with pytest.raises(SurfaceSpecError):
            sync_cli_surface(app, review_path=review, manifest_path=manifest, spec_path=spec)


def test_check_accepts_synced_outputs_and_reports_semantic_and_file_drift(tmp_path):
    app, _surface, review, manifest, spec = _make_files(tmp_path, active=True)
    report = sync_cli_surface(app, review_path=review, manifest_path=manifest, spec_path=spec)
    assert report.passed
    assert check_cli_surface(app, review_path=review, manifest_path=manifest, spec_path=spec).passed

    old_manifest = manifest.read_bytes()
    old_spec = spec.read_bytes()
    manifest.write_text("{}\n", encoding="utf-8")
    spec.write_bytes(old_spec.replace(b"Generated CLI surface", b"Stale CLI surface"))
    checked = check_cli_surface(app, review_path=review, manifest_path=manifest, spec_path=spec)
    assert "generated CLI manifest is stale" in checked.findings
    assert "generated CLI spec block is stale" in checked.findings
    assert manifest.read_text(encoding="utf-8") == "{}\n"
    assert b"Stale CLI surface" in spec.read_bytes()
    manifest.write_bytes(old_manifest)

    manifest.unlink()
    missing = check_cli_surface(app, review_path=review, manifest_path=manifest, spec_path=spec)
    assert "generated CLI manifest is stale" in missing.findings


def test_check_requires_changed_signatures_and_explicit_retirement(tmp_path):
    app, surface, review, manifest, spec = _make_files(tmp_path, active=True)
    sync_cli_surface(app, review_path=review, manifest_path=manifest, spec_path=spec)
    old = review.read_text(encoding="utf-8")
    first_candidate = surface["candidates"][0]
    changed_signature = first_candidate["signature"] + "changed"
    review.write_text(old.replace(first_candidate["signature"], changed_signature, 1), encoding="utf-8")
    report = check_cli_surface(app, review_path=review, manifest_path=manifest, spec_path=spec)
    assert any("signature changed" in finding for finding in report.findings)

    review.write_text(old + '\n[[cases]]\nid = "case:removed"\nstate = "active"\ndecision = "accept"\nreviewed_signature = "sha256:old"\nrationale = "old"\ninvocation = []\nexpected_exit_status = 0\neffects = []\ntest_ids = ["test::old"]\n', encoding="utf-8")
    report = check_cli_surface(app, review_path=review, manifest_path=manifest, spec_path=spec)
    assert any("stale semantic case needs explicit retirement" in finding for finding in report.findings)
    sync_cli_surface(app, review_path=review, manifest_path=manifest, spec_path=spec)
    assert "Removed or stale semantic cases" in spec.read_text(encoding="utf-8")
    retired = review.read_text(encoding="utf-8").replace(
        'id = "case:removed"\nstate = "active"',
        'id = "case:removed"\nstate = "retired"\nretirement_reason = "workflow removed"',
    )
    review.write_text(retired, encoding="utf-8")
    retired_report = sync_cli_surface(app, review_path=review, manifest_path=manifest, spec_path=spec)
    assert retired_report.passed
    assert "RETIRED" in spec.read_text(encoding="utf-8")


class _Marker:
    def __init__(self, name, *args, **kwargs):
        self.name = name
        self.args = args
        self.kwargs = kwargs


class _Item:
    def __init__(self, nodeid, markers=()):
        self.nodeid = nodeid
        self.markers = list(markers)

    def iter_markers(self, name=None):
        return [marker for marker in self.markers if name is None or marker.name == name]

    def get_closest_marker(self, name):
        return next((marker for marker in self.markers if marker.name == name), None)


def _one_case_catalog(*, state="active", test_ids=("test_file.py::test_case",)):
    case = ReviewCase(
        case_id="case:inspect/minimum",
        state=state,
        decision="accept" if state == "active" else None,
        reviewed_signature="sha256:current" if state == "active" else None,
        rationale="reviewed",
        invocation=(),
        invocation_declared=True,
        expected_exit_status=0 if state == "active" else None,
        expected_stdout_contains="",
        expected_stderr_contains="",
        effects=(),
        effects_declared=True,
        test_ids=tuple(test_ids),
        retirement_reason="",
    )
    return ReviewCatalog("audit-tool", 8, (), (case,))


def test_case_test_helper_requires_collected_marked_active_nodes():
    catalog = _one_case_catalog()
    item = _Item(
        "test_file.py::test_case",
        (_Marker("cli_case", catalog.cases[0].case_id), _Marker("skipif", False)),
    )

    assert_cli_case_tests([item], catalog)


def test_case_test_helper_detects_missing_unknown_nonactive_and_skipped_tests():
    active = _one_case_catalog()
    node = active.cases[0].case_id
    with pytest.raises(AssertionError, match="uncollected test"):
        assert_cli_case_tests([], active)
    with pytest.raises(AssertionError, match="lacks cli_case"):
        assert_cli_case_tests([_Item("test_file.py::test_case")], active)
    with pytest.raises(AssertionError, match="unknown CLI case"):
        assert_cli_case_tests([_Item("test_file.py::test_case", (_Marker("cli_case", "missing"),))], active)
    with pytest.raises(AssertionError, match="non-active CLI case"):
        assert_cli_case_tests(
            [_Item("test_file.py::test_case", (_Marker("cli_case", node),))],
            _one_case_catalog(state="pending"),
        )
    with pytest.raises(AssertionError, match="statically skipped"):
        assert_cli_case_tests(
            [_Item("test_file.py::test_case", (_Marker("cli_case", node), _Marker("skip")))],
            active,
        )
    with pytest.raises(AssertionError, match="statically skipped"):
        assert_cli_case_tests(
            [_Item("test_file.py::test_case", (_Marker("cli_case", node), _Marker("skipif", True)))],
            active,
        )
    with pytest.raises(AssertionError, match="not listed in test_ids"):
        assert_cli_case_tests(
            [
                _Item("test_file.py::test_case", (_Marker("cli_case", node),)),
                _Item("test_file.py::test_extra", (_Marker("cli_case", node),)),
            ],
            active,
        )


def test_case_test_helper_rejects_malformed_and_duplicate_catalog_references():
    case = _one_case_catalog().cases[0]
    malformed = _Item("test_file.py::test_case", (_Marker("cli_case"),))
    with pytest.raises(AssertionError, match="malformed"):
        assert_cli_case_tests([malformed], _one_case_catalog())

    duplicate_catalog = ReviewCatalog("audit-tool", 8, (), (case, case))
    with pytest.raises(AssertionError, match="duplicate case ID"):
        assert_cli_case_tests([], duplicate_catalog)

    duplicate_tests = _one_case_catalog(test_ids=("test_file.py::test_case", "test_file.py::test_case"))
    with pytest.raises(AssertionError, match="duplicate test_ids"):
        assert_cli_case_tests(
            [_Item("test_file.py::test_case", (_Marker("cli_case", case.case_id),))],
            duplicate_tests,
        )
    assert duplicate_tests.cases[0].test_ids


@pytest.mark.parametrize("marker_args", ((), ("",), (42,), ("case:a", "case:b")))
def test_case_test_helper_requires_one_nonempty_string_marker_argument(marker_args):
    malformed = _Item("test_file.py::test_case", (_Marker("cli_case", *marker_args),))
    with pytest.raises(AssertionError, match="empty or malformed"):
        assert_cli_case_tests([malformed], _one_case_catalog())


def test_surface_cli_uses_the_same_workflow_and_reports_check_status(tmp_path, monkeypatch, capsys):
    import cli_extended.surface_cli as surface_cli

    app, _surface, review, manifest, spec = _make_files(tmp_path, active=True)
    monkeypatch.setattr(surface_cli, "_load_factory", lambda _name: app)
    args = [
        "--factory", "consumer.cli:build_cli",
        "--review", str(review),
        "--manifest", str(manifest),
        "--spec", str(spec),
    ]

    assert surface_cli.main([*args, "sync"]) == 0
    assert "synchronized" in capsys.readouterr().out
    assert surface_cli.main([*args, "check"]) == 0
    assert "check passed" in capsys.readouterr().out
    assert surface_cli.main(
        ["--factory", "consumer.cli:build_cli", "--review", str(review), "template"]
    ) == 0
    assert "No semantic review rows" in capsys.readouterr().out
    manifest.write_text("{}", encoding="utf-8")
    assert surface_cli.main([*args, "check"]) == 1
    assert "manifest is stale" in capsys.readouterr().err


def test_surface_cli_converts_configuration_failures_to_status_two(tmp_path, monkeypatch, capsys):
    import cli_extended.surface_cli as surface_cli

    app, _surface, review, manifest, spec = _make_files(tmp_path, active=True)
    monkeypatch.setattr(surface_cli, "_load_factory", lambda _name: object())
    args = [
        "--factory", "consumer.cli:build_cli",
        "--review", str(review),
        "--manifest", str(manifest),
        "--spec", str(spec),
        "check",
    ]
    assert surface_cli.main(args) == 2
    assert "RegisteredCli" in capsys.readouterr().err

    monkeypatch.setattr(surface_cli, "_load_factory", lambda _name: app)
    review.write_text(
        review.read_text(encoding="utf-8").replace('cli_id = "audit-tool"', 'cli_id = "other-tool"'),
        encoding="utf-8",
    )
    assert surface_cli.main(
        ["--factory", "consumer.cli:build_cli", "--review", str(review), "template"]
    ) == 2
    assert "does not match registered executable" in capsys.readouterr().err

    review.write_text(
        review.read_text(encoding="utf-8").replace('cli_id = "other-tool"', 'cli_id = "audit-tool"'),
        encoding="utf-8",
    )

    monkeypatch.setattr(surface_cli, "_load_factory", lambda _name: app)
    args[args.index("check")] = "unknown"
    with pytest.raises(SystemExit):
        surface_cli.main(args)

    with pytest.raises(SystemExit):
        surface_cli.main(["--factory", "consumer.cli:build_cli", "--review", str(review), "sync"])


def test_surface_cli_factory_loader_validates_and_calls_imported_target(monkeypatch):
    import types

    import cli_extended.surface_cli as surface_cli

    app = _build_cli()
    module = types.SimpleNamespace(build_cli=lambda: app, value=1)
    monkeypatch.setattr(surface_cli.importlib, "import_module", lambda _name: module)

    assert surface_cli._load_factory("consumer.cli:build_cli") is app
    with pytest.raises(ValueError, match="module:callable"):
        surface_cli._load_factory("not-a-factory")
    with pytest.raises(TypeError, match="not callable"):
        surface_cli._load_factory("consumer.cli:value")
    with pytest.raises(TypeError, match="did not return"):
        module.build_cli = lambda: object()
        surface_cli._load_factory("consumer.cli:build_cli")
    module.build_cli = lambda: app
    monkeypatch.setattr(
        surface_cli.importlib,
        "import_module",
        lambda _name: (_ for _ in ()).throw(ImportError("missing module")),
    )
    with pytest.raises(ImportError, match="missing module"):
        surface_cli._load_factory("consumer.cli:build_cli")


def test_surface_cli_factory_loader_supports_hyphenated_script_and_sibling_imports(
    tmp_path, monkeypatch,
):
    module_name = "_cli_extended_surface_monitor_task"
    monkeypatch.setattr(sys, "path", list(sys.path))
    monkeypatch.delitem(sys.modules, module_name, raising=False)
    script_dir = tmp_path / "consumer"
    script_dir.mkdir()
    (script_dir / "consumer_support.py").write_text(
        "VALUE = 'loaded from sibling'\n", encoding="utf-8"
    )
    script = script_dir / "monitor-task.py"
    script.write_text(
        "from __future__ import annotations\n"
        "from dataclasses import dataclass\n"
        "from consumer_support import VALUE\n"
        "from cli_extended import ArgumentSpec, CliIdentity, CliRegistry, VerbSpec\n"
        "@dataclass\n"
        "class FactoryState:\n"
        "    value: str = VALUE\n"
        "def parse_target(value):\n"
        "    return value\n"
        "def build_cli():\n"
        "    assert FactoryState().value == 'loaded from sibling'\n"
        "    registry = CliRegistry(\n"
        "        CliIdentity('AUDIT', '1.0', 'Audit Tool', command='audit-tool'),\n"
        "        prog='audit-tool', description='Audit resources.')\n"
        "    registry.register(VerbSpec(\n"
        "        'inspect', description='inspect resources', group='READ',\n"
        "        handler=lambda _args: None,\n"
        "        arguments=(ArgumentSpec('target', 'target', parser_kwargs={'type': parse_target}),)))\n"
        "    return registry.build()\n",
        encoding="utf-8",
    )

    app = _load_factory(f"{script}:build_cli")
    surface = export_cli_surface(app)
    target = next(
        action
        for action in surface["routes"][0]["actions"]
        if action["kind"] == "argument"
    )

    assert app.identity.command_name == "audit-tool"
    assert "inspect" in app.command_parsers
    assert target["type"] == {
        "callable": "_cli_extended_surface_monitor_task.parse_target"
    }

    # A second load finds the script directory already on sys.path; it still
    # rebuilds the same registry under the same manifest-visible module name.
    again = _load_factory(f"{script}:build_cli")
    assert again.identity.command_name == app.identity.command_name

    loaded_module = sys.modules[module_name]
    script.write_text("raise RuntimeError('factory import failed')\n", encoding="utf-8")
    with pytest.raises(RuntimeError, match="factory import failed"):
        _load_factory(f"{script}:build_cli")
    assert sys.modules[module_name] is loaded_module

    broken_script = script_dir / "broken-cli.py"
    broken_script.write_text("raise RuntimeError('broken factory')\n", encoding="utf-8")
    with pytest.raises(RuntimeError, match="broken factory"):
        _load_factory(f"{broken_script}:build_cli")
    assert "_cli_extended_surface_broken_cli" not in sys.modules


def test_surface_cli_factory_loader_rejects_a_directory_path(tmp_path):
    with pytest.raises(ValueError, match="is not a file"):
        _load_factory(f"{tmp_path}:build_cli")


def test_surface_cli_factory_loader_preserves_missing_path_error(tmp_path):
    missing_script = tmp_path / "missing-factory.py"

    with pytest.raises(FileNotFoundError):
        _load_factory(f"{missing_script}:build_cli")


@pytest.mark.parametrize(
    "invalid_spec",
    (None, pytest.param(types.SimpleNamespace(loader=None), id="no-loader")),
)
def test_surface_cli_factory_loader_rejects_unloadable_script_specs(
    tmp_path, monkeypatch, invalid_spec
):
    import cli_extended.surface_cli as surface_cli

    script = tmp_path / "factory.py"
    script.write_text("def build_cli(): pass\n", encoding="utf-8")
    monkeypatch.setattr(
        surface_cli.importlib.util,
        "spec_from_file_location",
        lambda *_args: invalid_spec,
    )

    with pytest.raises(ImportError, match="cannot load factory module"):
        surface_cli._load_factory(f"{script}:build_cli")


@pytest.mark.parametrize("specification", ("module", ":build_cli", "module:"))
def test_surface_cli_factory_loader_rejects_each_incomplete_factory_component(
    specification,
):
    import cli_extended.surface_cli as surface_cli

    with pytest.raises(ValueError, match="module:callable"):
        surface_cli._load_factory(specification)


def test_surface_cli_requires_factory_review_manifest_and_spec(tmp_path, monkeypatch):
    import cli_extended.surface_cli as surface_cli

    app, _surface, review, manifest, spec = _make_files(tmp_path, active=True)
    with pytest.raises(SystemExit):
        surface_cli._argument_parser().parse_args(["--review", str(review), "template"])
    with pytest.raises(SystemExit):
        surface_cli._argument_parser().parse_args(["--factory", "consumer.cli:build", "template"])

    monkeypatch.setattr(surface_cli, "_load_factory", lambda _name: app)
    common = ["--factory", "consumer.cli:build", "--review", str(review)]
    with pytest.raises(SystemExit):
        surface_cli.main([*common, "--manifest", str(manifest), "check"])
    with pytest.raises(SystemExit):
        surface_cli.main([*common, "--spec", str(spec), "check"])


def test_surface_cli_module_entrypoint_runs_main(tmp_path, monkeypatch, capsys):
    import runpy
    import sys
    import types

    app, _surface, review, _manifest, _spec = _make_files(tmp_path, active=True)
    review.write_text('schema_version = 1\ncli_id = "audit-tool"\n', encoding="utf-8")
    monkeypatch.setitem(sys.modules, "surface_factory_for_test", types.SimpleNamespace(build= lambda: app))
    monkeypatch.delitem(sys.modules, "cli_extended.surface_cli")
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "surface_cli",
            "--factory",
            "surface_factory_for_test:build",
            "--review",
            str(review),
            "template",
        ],
    )
    with pytest.raises(SystemExit) as raised:
        runpy.run_module("cli_extended.surface_cli", run_name="__main__")
    assert raised.value.code == 0
    assert "Candidate kind" in capsys.readouterr().out


def test_surface_report_renders_findings_and_success_as_plain_text():
    from cli_extended import SurfaceReport

    assert SurfaceReport().passed is True
    assert SurfaceReport().render() == ""
    report = SurfaceReport(("missing case", "stale manifest"))
    assert report.passed is False
    assert report.render() == "missing case\nstale manifest"


def test_review_keeps_unknown_dash_options_out_of_missing_option_values():
    route = {
        "id": "route:entrypoint:audit-tool",
        "path": [],
        "aliases": [],
        "kind": "invocation",
        "single_command": True,
        "no_args_action": True,
        "actions": [
            {
                "id": "option:count",
                "kind": "option",
                "flags": ["--count"],
                "nargs": None,
                "minimum_values": 1,
                "required": False,
                "parser_path": [],
            }
        ],
        "parser_settings": [
            {
                **_argparse_negative_number_settings()[1],
                "negative_number_matcher": None,
                "negative_number_matcher_custom": False,
            }
        ],
    }
    candidate = {
        "id": "case:count",
        "route_id": route["id"],
        "signature": "sha256:count",
        "kind": "option-spelling",
        "members": ["option:count"],
        "shape": {"spelling": "--count"},
    }

    findings = _findings_for_invocation(candidate, route, ["--count", "--unknown"])

    assert any("omits a value for --count" in item for item in findings)


@pytest.mark.parametrize("nargs, should_report_missing", (("*", False), ("+", True)))
def test_review_star_and_plus_options_finish_at_empty_argv_tail(
    nargs, should_report_missing
):
    route = {
        "id": "route:entrypoint:audit-tool",
        "path": [],
        "aliases": [],
        "kind": "invocation",
        "single_command": True,
        "no_args_action": True,
        "actions": [
            {
                "id": "option:many",
                "kind": "option",
                "flags": ["--many"],
                "nargs": nargs,
                "minimum_values": 0 if nargs == "*" else 1,
                "required": False,
                "parser_path": [],
            }
        ],
    }
    candidate = {
        "id": f"case:many-{nargs}",
        "route_id": route["id"],
        "signature": "sha256:many",
        "kind": "option-spelling",
        "members": ["option:many"],
        "shape": {"spelling": "--many"},
    }

    findings = _findings_for_invocation(candidate, route, ["--many"])

    assert any("omits a value for --many" in item for item in findings) is should_report_missing


def test_review_abbreviation_lookup_ignores_non_option_actions():
    route = {
        "id": "route:entrypoint:audit-tool",
        "path": [],
        "aliases": [],
        "kind": "invocation",
        "single_command": True,
        "no_args_action": True,
        "parser_settings": [{"parser_path": [], "allow_abbrev": True}],
        "actions": [
            {
                "id": "argument:synthetic",
                "kind": "argument",
                "name": "synthetic",
                "flags": ["--count"],
                "nargs": None,
                "parser_path": [],
            }
        ],
    }
    candidate = {
        "id": "case:unknown-abbreviation",
        "route_id": route["id"],
        "signature": "sha256:unknown-abbreviation",
        "kind": "other",
        "members": [],
        "shape": {},
    }

    findings = _findings_for_invocation(
        candidate, route, ["--cou", "value"], allow_abbrev=True
    )

    assert any("undeclared unknown option '--cou'" in item for item in findings)


def test_review_parent_positional_can_consume_lone_dash_before_nested_verb():
    prefix = {
        "id": "route:entrypoint:audit-tool/inspect",
        "path": ["inspect"],
        "aliases": [],
        "kind": "route-prefix",
        "actions": [],
    }
    parent = {
        "id": "argument:parent-label",
        "kind": "argument",
        "name": "label",
        "nargs": "?",
        "minimum_values": 0,
        "required": False,
        "parser_path": ["inspect"],
        "before_nested_subcommand": True,
    }
    route = {
        "id": "route:entrypoint:audit-tool/inspect/apply",
        "path": ["inspect", "apply"],
        "aliases": [],
        "kind": "invocation",
        "actions": [parent],
    }
    candidate = {
        "id": "case:nested-route",
        "route_id": route["id"],
        "signature": "sha256:nested-route",
        "kind": "other",
        "members": [],
        "shape": {},
    }

    findings = _findings_for_invocation(
        candidate,
        route,
        ["inspect", "-", "apply"],
        route_prefixes=(prefix,),
    )

    assert not any("omits its command path" in item for item in findings)


def test_review_exact_minimum_values_do_not_report_missing_values():
    route = {
        "id": "route:entrypoint:audit-tool",
        "path": [],
        "aliases": [],
        "kind": "invocation",
        "single_command": True,
        "no_args_action": True,
        "actions": [
            {
                "id": "argument:required-pair",
                "kind": "argument",
                "name": "required-pair",
                "nargs": 2,
                "minimum_values": 2,
                "required": True,
                "choices": ["left", "right"],
                "parser_path": [],
            },
            {
                "id": "argument:optional-many",
                "kind": "argument",
                "name": "optional-many",
                "nargs": "+",
                "minimum_values": 1,
                "required": False,
                "choices": ["up", "down"],
                "parser_path": [],
            },
            {
                "id": "option:required-pair",
                "kind": "option",
                "flags": ["--required-pair"],
                "nargs": 2,
                "minimum_values": 2,
                "required": True,
                "choices": ["one", "two"],
                "parser_path": [],
            },
            {
                "id": "option:optional-many",
                "kind": "option",
                "flags": ["--optional-many"],
                "nargs": "+",
                "minimum_values": 1,
                "required": False,
                "choices": ["alpha", "beta"],
                "parser_path": [],
            },
        ],
    }
    candidate = {
        "id": "case:exact-minimum",
        "route_id": route["id"],
        "signature": "sha256:exact-minimum",
        "kind": "other",
        "members": [],
        "shape": {},
    }
    invocation = [
        "bad-left",
        "bad-right",
        "bad-optional",
        "--required-pair",
        "bad-one",
        "bad-two",
        "--optional-many",
        "bad-optional",
    ]

    findings = _findings_for_invocation(candidate, route, invocation)

    assert any("invalid value for required argument" in item for item in findings)
    assert any("invalid positional value" in item for item in findings)
    assert any("invalid value for required option" in item for item in findings)
    assert any(
        "invalid value for option option:optional-many" in item
        for item in findings
    )
    assert not any("omits required positional argument" in item for item in findings)
    assert not any("omits a value for positional argument" in item for item in findings)
    assert not any("omits a value for --" in item for item in findings)


def test_review_interaction_exemption_keeps_required_group_diagnostic():
    route = {
        "id": "route:entrypoint:audit-tool",
        "path": [],
        "aliases": [],
        "kind": "invocation",
        "single_command": True,
        "no_args_action": True,
        "actions": [
            {
                "id": "option:file",
                "kind": "option",
                "flags": ["--file"],
                "nargs": 0,
                "required": False,
                "exclusive_group": "source",
                "exclusive_required": True,
                "parser_path": [],
            },
            {
                "id": "option:inline",
                "kind": "option",
                "flags": ["--inline"],
                "nargs": 0,
                "required": False,
                "exclusive_group": "source",
                "exclusive_required": True,
                "parser_path": [],
            },
            {
                "id": "option:extra",
                "kind": "option",
                "flags": ["--extra"],
                "nargs": 0,
                "required": False,
                "exclusive_group": None,
                "exclusive_required": False,
                "parser_path": [],
            },
        ],
    }
    candidate = {
        "id": "case:one-required-member-interaction",
        "route_id": route["id"],
        "signature": "sha256:group-interaction",
        "kind": "interaction",
        "members": ["option:file", "option:extra"],
        "shape": {
            "option_ids": ["option:file", "option:extra"],
            "required_arguments": [],
            "required_options": [],
            "required_exclusive_groups": {},
            "external_options": [],
        },
    }

    findings = _findings_for_invocation(candidate, route, ["--extra"])

    assert any(
        "does not supply participating option option:file" in item
        for item in findings
    )
    assert any(
        "must supply exactly one option from required group source" in item
        for item in findings
    )


def test_review_required_minimum_references_must_have_the_matching_kind_and_id():
    route = {
        "id": "route:entrypoint:audit-tool",
        "path": [],
        "aliases": [],
        "kind": "invocation",
        "single_command": True,
        "no_args_action": True,
        "actions": [
            {
                "id": "shared-id",
                "kind": "option",
                "flags": ["--shared"],
                "nargs": 0,
                "required": False,
            },
            {
                "id": "option:other",
                "kind": "option",
                "flags": ["--other"],
                "nargs": 0,
                "required": False,
            },
        ],
    }
    candidate = {
        "id": "case:bad-required-references",
        "route_id": route["id"],
        "signature": "sha256:bad-required-references",
        "kind": "minimum",
        "members": [],
        "shape": {
            "required_arguments": ["shared-id"],
            "required_options": ["option:missing"],
        },
    }

    findings = _findings_for_invocation(
        candidate, route, ["--shared", "--other"]
    )

    assert any("unknown required argument shared-id" in item for item in findings)
    assert any("unknown required option option:missing" in item for item in findings)


def test_review_conflict_validation_is_scoped_to_its_own_group():
    route = {
        "id": "route:entrypoint:audit-tool",
        "path": [],
        "aliases": [],
        "kind": "invocation",
        "single_command": True,
        "no_args_action": True,
        "actions": [
            {
                "id": "option:file",
                "kind": "option",
                "flags": ["--file"],
                "nargs": 0,
                "required": False,
                "exclusive_group": "source",
                "exclusive_required": True,
            },
            {
                "id": "option:inline",
                "kind": "option",
                "flags": ["--inline"],
                "nargs": 0,
                "required": False,
                "exclusive_group": "source",
                "exclusive_required": True,
            },
            {
                "id": "option:json",
                "kind": "option",
                "flags": ["--json"],
                "nargs": 0,
                "required": False,
                "exclusive_group": "format",
                "exclusive_required": False,
            },
            {
                "id": "option:text",
                "kind": "option",
                "flags": ["--text"],
                "nargs": 0,
                "required": False,
                "exclusive_group": "format",
                "exclusive_required": False,
            },
        ],
    }
    candidate = {
        "id": "case:source-conflict",
        "route_id": route["id"],
        "signature": "sha256:source-conflict",
        "kind": "exclusive-conflict",
        "members": ["option:file", "option:inline"],
        "shape": {
            "group_id": "source",
            "options": ["option:file", "option:inline"],
        },
    }

    findings = _findings_for_invocation(
        candidate, route, ["--file", "--inline", "--json"]
    )

    assert not any("conflicting option once" in item for item in findings)


def test_review_conflict_validation_requires_each_expected_member_once():
    route = {
        "id": "route:entrypoint:audit-tool",
        "path": [],
        "aliases": [],
        "kind": "invocation",
        "single_command": True,
        "no_args_action": True,
        "actions": [
            {
                "id": "option:file",
                "kind": "option",
                "flags": ["--file"],
                "nargs": 0,
                "required": False,
                "exclusive_group": "source",
                "exclusive_required": True,
            },
            {
                "id": "option:inline",
                "kind": "option",
                "flags": ["--inline"],
                "nargs": 0,
                "required": False,
                "exclusive_group": "source",
                "exclusive_required": True,
            },
        ],
    }
    candidate = {
        "id": "case:source-conflict",
        "route_id": route["id"],
        "signature": "sha256:source-conflict",
        "kind": "exclusive-conflict",
        "members": ["option:file", "option:inline"],
        "shape": {
            "group_id": "source",
            "options": ["option:file", "option:inline"],
        },
    }

    findings = _findings_for_invocation(candidate, route, ["--file", "--file"])

    assert any("supply each conflicting option once" in item for item in findings)


def test_distinct_surface_path_validation_refuses_a_missing_parent(tmp_path):
    missing_parent = tmp_path / "missing"
    with pytest.raises(SurfaceSpecError, match="cannot resolve manifest path"):
        _validate_distinct_surface_paths(
            tmp_path / "review.toml",
            missing_parent / "surface.json",
            tmp_path / "SPEC.md",
        )


def test_markdown_marks_positional_actions_as_outside_exclusive_groups():
    surface = {
        "schema_version": 5,
        "library_contract": {"name": "cli-extended", "version": 1},
        "entrypoint": {
            "command": "audit-tool",
            "prog": "audit-tool",
            "single_command": False,
            "no_args_action": False,
            "builtins": [],
        },
        "routes": [
            {
                "id": "route:entrypoint:audit-tool",
                "path": [],
                "kind": "invocation",
                "single_command": False,
                "no_args_action": False,
                "confirmation": False,
                "parser_configured_by_callback": False,
                "actions": [
                    {
                        "id": "argument:target",
                        "kind": "argument",
                        "name": "target",
                        "description": "target to inspect",
                        "required": True,
                        "hidden": False,
                    }
                ],
            }
        ],
        "candidates": [],
        "syntax_complete": True,
    }

    markdown = render_cli_surface_markdown(
        surface, ReviewCatalog("audit-tool", 8, (), ())
    )
    argument_row = next(
        line for line in markdown.splitlines() if line.startswith("| argument:target |")
    )

    assert '"exclusive_required": false' in argument_row
