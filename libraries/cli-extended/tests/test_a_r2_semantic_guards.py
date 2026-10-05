from __future__ import annotations

import argparse
import re

import pytest

from cli_extended import (
    ReviewCatalog,
    ReviewCase,
    SurfaceError,
    SurfaceSpecError,
    render_cli_surface_markdown,
)
from cli_extended.review import _review_findings
from cli_extended.surface import (
    _describe_parser,
    _generate_candidates,
    _negative_number_settings,
    _parser_syntax_issues,
)


def _route(actions=(), *, path=(), parser_settings=()):
    path = tuple(path)
    route_id = "route:entrypoint:audit-tool" + (
        "/" + "/".join(path) if path else ""
    )
    return {
        "id": route_id,
        "path": list(path),
        "aliases": [],
        "kind": "invocation",
        "single_command": not path,
        "no_args_action": not path,
        "confirmation": False,
        "parser_configured_by_callback": False,
        "parser_settings": list(parser_settings),
        "actions": list(actions),
    }


def _candidate(route, *, case_id="case:minimum", shape=None):
    return {
        "id": case_id,
        "route_id": route["id"],
        "signature": "sha256:current",
        "kind": "minimum",
        "members": [],
        "shape": {} if shape is None else shape,
    }


def _review(route, candidate, argv, *, test_id):
    case = ReviewCase(
        case_id=candidate["id"],
        state="active",
        decision="accept",
        reviewed_signature=candidate["signature"],
        rationale="reviewed behavior",
        invocation=tuple(argv),
        invocation_declared=True,
        expected_exit_status=0,
        expected_stdout_contains="",
        expected_stderr_contains="",
        effects=(),
        effects_declared=True,
        test_ids=(test_id,),
        retirement_reason="",
    )
    return _review_findings(
        {
            "entrypoint": {"command": "audit-tool", "allow_abbrev": False},
            "routes": [route],
            "candidates": [candidate],
            "syntax_complete": True,
        },
        ReviewCatalog("audit-tool", 16, (), (case,)),
    )


@pytest.mark.parametrize(("nargs", "minimum_values"), (("*", 0), ("+", 1)))
def test_variadic_option_consumes_to_end_without_crossing_argv_boundary(
    nargs, minimum_values
):
    route = _route(
        [
            {
                "id": "option:labels",
                "kind": "option",
                "flags": ["--labels"],
                "nargs": nargs,
                "minimum_values": minimum_values,
                "required": False,
                "parser_path": [],
            }
        ]
    )
    candidate = _candidate(route)

    assert _review(
        route,
        candidate,
        ["--labels", "one"],
        test_id=(
            "tests/test_a_r2_semantic_guards.py::"
            "test_variadic_option_consumes_to_end_without_crossing_argv_boundary["
            f"{nargs}-{minimum_values}]"
        ),
    ) == []


def test_review_refuses_a_negative_number_without_an_inspectable_matcher():
    route = _route(
        [
            {
                "id": "argument:count",
                "kind": "argument",
                "name": "count",
                "nargs": None,
                "minimum_values": 1,
                "required": True,
                "parser_path": [],
            }
        ]
    )
    candidate = _candidate(
        route, shape={"required_arguments": ["argument:count"]}
    )

    findings = _review(
        route,
        candidate,
        ["-1"],
        test_id=(
            "tests/test_a_r2_semantic_guards.py::"
            "test_review_refuses_a_negative_number_without_an_inspectable_matcher"
        ),
    )

    assert any("omits required positional argument argument:count" in item for item in findings)


def test_negative_number_settings_reject_a_bytes_pattern_and_mark_it_custom():
    parser = argparse.ArgumentParser(add_help=False)
    parser._negative_number_matcher = re.compile(b"^-\\d+$")

    assert _negative_number_settings(parser) == (None, False, True)


def test_parser_default_comparison_canonicalizes_mapping_key_order():
    parser = argparse.ArgumentParser(add_help=False)
    parser.add_argument("--settings", default={"a": 1, "b": 2})
    parser._defaults = {"settings": {"b": 2, "a": 1}}

    assert _parser_syntax_issues(parser, route_id="route:test") == []


def test_nested_route_with_empty_child_help_uses_parser_description():
    parser = argparse.ArgumentParser(
        add_help=False,
        description="Parent fallback summary.",
    )
    routes = _describe_parser(
        parser,
        entrypoint_id="entrypoint:audit-tool",
        path=("plugins", "inspect"),
        verb_specs=(),
        global_options=(),
        single_command=False,
        no_args_action=False,
        incomplete=[],
        nested_route=True,
        child_help="",
    )

    assert routes[0]["summary"] == "Parent fallback summary."


def test_markdown_refuses_missing_route_invocation_metadata():
    entrypoint = {
        "command": "audit-tool",
        "prog": "audit-tool",
        "builtins": [],
        "single_command": False,
        "no_args_action": False,
    }

    def root_row(route):
        markdown = render_cli_surface_markdown(
            {
                "schema_version": 6,
                "entrypoint": entrypoint,
                "routes": [route],
                "candidates": [],
                "syntax_complete": True,
            },
            ReviewCatalog("audit-tool", 8, (), ()),
        )
        return next(
            line
            for line in markdown.splitlines()
            if line.startswith("| route:entrypoint:audit-tool |")
        )

    base = {
        "id": "route:entrypoint:audit-tool",
        "path": [],
        "aliases": [],
        "kind": "invocation",
        "confirmation": False,
        "parser_configured_by_callback": False,
        "actions": [],
    }

    with pytest.raises(SurfaceSpecError, match="single_command"):
        root_row({**base, "no_args_action": True})

    with pytest.raises(SurfaceSpecError, match="no_args_action"):
        root_row({**base, "single_command": True})

    with pytest.raises(SurfaceSpecError, match="single_command"):
        root_row(base)


def test_interaction_option_ids_reject_an_empty_string_before_lookup():
    route = _route(
        [
            {
                "id": "option:toggle",
                "kind": "option",
                "flags": ["--toggle"],
                "nargs": 0,
                "minimum_values": 0,
                "required": False,
                "choices": None,
                "parser_path": ["run"],
            }
        ],
        path=("run",),
    )

    with pytest.raises(SurfaceError, match="non-empty string list"):
        _generate_candidates(
            [route],
            ({"id": "empty-member", "route_id": route["id"], "option_ids": [""]},),
            max_candidates=8,
        )
