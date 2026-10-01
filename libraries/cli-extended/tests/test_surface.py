from __future__ import annotations

import argparse
import hashlib
import json
import re
from dataclasses import FrozenInstanceError, replace
from enum import Enum

import pytest

from cli_extended import (
    ArgumentSpec,
    CliIdentity,
    CliRegistry,
    OptionSpec,
    SurfaceError,
    SurfaceLimitError,
    VerbSpec,
    export_cli_surface,
    render_cli_surface_json,
)
from cli_extended.surface import (
    _canonical_flag,
    _callable_label,
    _describe_parser,
    _effective_default,
    _generate_candidates,
    _minimum_values,
    _mutex_groups,
    _normalize,
    _ParserActionContext,
    _parser_syntax_issues,
    _route_id,
    _safe_choice_values,
    _scope,
    _signature,
    _surface_action,
    _action_surface_id,
    _walk_registered_cli,
)

IDENTITY = CliIdentity("SURFACE", "1.0", "Surface Demo", command="surface-demo")


def _complex_cli(*, duplicate_ids: bool = False):
    registry = CliRegistry(
        IDENTITY,
        prog="surface-demo",
        description="Inspect and change resources.",
        global_options=(
            OptionSpec(
                ("--profile",),
                "select a profile",
                surface_id="shared-profile",
                parser_kwargs={"default": "safe"},
            ),
            OptionSpec(
                ("--profile-file",),
                "read the profile from a file",
                mutually_exclusive_group="profile-source",
            ),
            OptionSpec(
                ("--profile-inline",),
                "read the profile inline",
                mutually_exclusive_group="profile-source",
            ),
        ),
    )

    def configure(parser: argparse.ArgumentParser) -> None:
        parser.add_argument("--plugin-mode", choices=("one", "two"), help="plugin mode")
        nested = parser.add_subparsers(dest="detail")
        detail = nested.add_parser("detail", aliases=("d",), help="show detail")
        detail.add_argument("--raw", action="store_true", help="show raw detail")

    first_id = "duplicate" if duplicate_ids else "source-file"
    second_id = "duplicate" if duplicate_ids else "source-inline"
    registry.register(
        VerbSpec(
            "inspect",
            description="inspect one resource",
            group="EXPLORATION",
            arguments=(
                ArgumentSpec(
                    "resource",
                    "resource identifier",
                    parser_kwargs={"choices": ("alpha", "beta")},
                    surface_id="resource-id",
                ),
            ),
            options=(
                OptionSpec(
                    ("--format", "-f"),
                    "output format",
                    parser_kwargs={"choices": ("text", "json"), "required": True},
                    surface_id="format-id",
                ),
                OptionSpec(
                    ("--from-file",),
                    "read config from a file",
                    metavar="PATH",
                    mutually_exclusive_group="configuration",
                    mutually_exclusive_required=True,
                    surface_id=first_id,
                ),
                OptionSpec(
                    ("--from-inline",),
                    "read inline config",
                    metavar="JSON",
                    mutually_exclusive_group="configuration",
                    mutually_exclusive_required=True,
                    surface_id=second_id,
                ),
                OptionSpec(
                    ("--force",), "force operation", parser_kwargs={"action": "store_true"}
                ),
            ),
            configure=configure,
            handler=lambda _args, _runtime: 0,
        )
    )
    registry.register(
        VerbSpec("version-info", description="show version details", handler=lambda *_: 0)
    )
    return registry.build()


def test_surface_export_is_deterministic_and_describes_installed_parser_tree():
    app = _complex_cli()
    interaction = {
        "id": "format-and-force",
        "route_id": "route:entrypoint:surface-demo/inspect",
        "option_ids": (
            "format-id",
            "option:route:entrypoint:surface-demo/inspect/--force",
        ),
    }

    first = export_cli_surface(app, interaction_groups=(interaction,))
    second = export_cli_surface(app, interaction_groups=(interaction,))
    routes = {route["id"]: route for route in first["routes"]}
    inspect = routes["route:entrypoint:surface-demo/inspect"]
    actions = {action["id"]: action for action in inspect["actions"]}
    detail = routes["route:entrypoint:surface-demo/inspect/detail"]

    assert render_cli_surface_json(first) == render_cli_surface_json(second)
    assert first["entrypoint"]["allow_abbrev"] is False
    assert first["entrypoint"]["prefix_chars"] == "-"
    assert first["entrypoint"]["fromfile_prefix_chars"] is None
    assert first["entrypoint"]["single_command"] is False
    assert first["entrypoint"]["builtins"] == [
        "help", "help <verb>", "version", "--help", "--version"
    ]
    assert [setting["parser_path"] for setting in inspect["parser_settings"]] == [
        [], ["inspect"]
    ]
    runtime_matcher = argparse.ArgumentParser(add_help=False)._negative_number_matcher
    for setting in inspect["parser_settings"]:
        assert setting["allow_abbrev"] is False
        assert setting["prefix_chars"] == "-"
        assert setting["fromfile_prefix_chars"] is None
        assert setting["negative_number_matcher"] == {
            "pattern": runtime_matcher.pattern,
            "flags": runtime_matcher.flags,
        }
        assert setting["has_negative_number_optionals"] is False
        assert setting["negative_number_matcher_custom"] is False
    assert [
        setting["parser_path"] for setting in detail["parser_settings"]
    ] == [[], ["inspect"], ["inspect", "detail"]]
    assert inspect["subcommands"] == [detail["id"]]
    assert detail["aliases"] == ["d"]
    assert inspect["parser_configured_by_callback"] is True
    assert actions["shared-profile"]["scope"] == "global"
    assert any(
        action["id"] == (
            "option:route:entrypoint:surface-demo/inspect/--profile-file"
        )
        for action in inspect["actions"]
    )
    assert actions["shared-profile"]["placement"] == {
        "before_verb": True,
        "after_verb": True,
        "single_command_invocation": False,
    }
    assert actions["shared-profile"]["effective_default"] == "safe"
    nested_actions = {action["id"]: action for action in detail["actions"]}
    assert "resource-id" in nested_actions
    assert nested_actions["resource-id"]["parser_path"] == ["inspect"]
    assert nested_actions["resource-id"]["before_nested_subcommand"] is True
    assert any(
        action.get("flags") == ["--plugin-mode"]
        and action["parser_path"] == ["inspect"]
        for action in detail["actions"]
    )
    assert any(
        action.get("flags") == ["--raw"]
        and action["before_nested_subcommand"] is False
        for action in detail["actions"]
    )
    parsed = app.parser.parse_args(
        [
            "--profile", "work",
            "inspect", "alpha", "--format", "text", "--plugin-mode", "one",
            "--from-file", "profile.toml", "detail", "--raw",
        ]
    )
    assert parsed.resource == "alpha"
    assert parsed.detail == "detail"
    assert parsed.raw is True
    assert parsed.profile == "work"
    assert actions["option:route:entrypoint:surface-demo/inspect/--profile-file"]["exclusive_group"] == "profile-source"
    assert actions["format-id"]["flags"] == ["--format", "-f"]
    assert actions["format-id"]["choices"] == ["text", "json"]
    assert actions["format-id"]["required"] is True
    assert actions["option:route:entrypoint:surface-demo/inspect/--plugin-mode"]["choices"] == [
        "one",
        "two",
    ]
    assert "--plugin-mode" in render_cli_surface_json(first)
    assert any(
        candidate["kind"] == "route-alias"
        and candidate["shape"]["alias"] == "d"
        for candidate in first["candidates"]
    )
    assert any(
        candidate["kind"] == "interaction"
        and candidate["shape"]["interaction_id"] == "format-and-force"
        for candidate in first["candidates"]
    )
    minimum = next(
        candidate
        for candidate in first["candidates"]
        if candidate["kind"] == "minimum"
        and candidate["route_id"] == inspect["id"]
    )
    assert "shared-profile" in minimum["shape"]["defaulted_options"]
    force_id = "option:route:entrypoint:surface-demo/inspect/--force"
    assert not any(
        candidate["kind"] == "exclusive-member"
        and candidate["members"] == [force_id]
        for candidate in first["candidates"]
    )
    assert first["syntax_complete"] is True


def test_surface_signature_tracks_parser_scoped_abbreviation_policy():
    def build_surface(allow_abbrev: bool):
        registry = CliRegistry(
            IDENTITY,
            prog="surface-demo",
            description="inspect sample commands",
            allow_abbrev=False,
        )

        def configure(parser: argparse.ArgumentParser) -> None:
            parser.allow_abbrev = allow_abbrev

        registry.register(
            VerbSpec(
                "show",
                description="show one item",
                options=(OptionSpec(("--profile",), "select profile"),),
                configure=configure,
                handler=lambda *_: 0,
            )
        )
        return export_cli_surface(registry.build())

    abbreviated = build_surface(True)
    exact = build_surface(False)
    abbreviated_route = abbreviated["routes"][0]
    exact_route = exact["routes"][0]
    assert abbreviated_route["parser_settings"][-1]["allow_abbrev"] is True
    abbreviated_case = next(
        candidate
        for candidate in abbreviated["candidates"]
        if candidate["kind"] == "option-spelling"
        and candidate["shape"].get("spelling") == "--profile"
    )
    exact_case = next(
        candidate
        for candidate in exact["candidates"]
        if candidate["kind"] == "option-spelling"
        and candidate["shape"].get("spelling") == "--profile"
    )
    assert abbreviated_case["signature"] != exact_case["signature"]


def test_surface_signature_tracks_mutation_confirmation_policy():
    def minimum_signature(mutating: bool) -> str:
        registry = CliRegistry(
            IDENTITY,
            prog="surface-demo",
            description="inspect sample commands",
        )
        registry.register(
            VerbSpec(
                "show",
                description="show one item",
                mutating=mutating,
                handler=lambda *_: 0,
            )
        )
        surface = export_cli_surface(registry.build())
        minimum = next(
            candidate
            for candidate in surface["candidates"]
            if candidate["kind"] == "minimum"
        )
        return minimum["signature"]

    assert minimum_signature(False) != minimum_signature(True)


@pytest.mark.parametrize(
    ("setting", "value", "finding"),
    (
        ("prefix_chars", "+-", "has unsupported prefix_chars"),
        ("fromfile_prefix_chars", "@", "argument-file expansion"),
    ),
)
def test_surface_refuses_to_certify_unmodeled_parser_token_syntax(
    setting: str, value: str, finding: str
):
    registry = CliRegistry(
        IDENTITY,
        prog="surface-demo",
        description="inspect sample commands",
    )

    def configure(parser: argparse.ArgumentParser) -> None:
        setattr(parser, setting, value)

    registry.register(
        VerbSpec(
            "show",
            description="show one item",
            configure=configure,
            handler=lambda *_: 0,
        )
    )

    surface = export_cli_surface(registry.build())

    assert surface["syntax_complete"] is False
    assert any(finding in item for item in surface["incomplete"])
    assert any("parser show" in item for item in surface["incomplete"])


def test_surface_candidates_include_each_argument_option_alias_choice_and_exclusion():
    surface = export_cli_surface(_complex_cli())
    kinds = {candidate["kind"] for candidate in surface["candidates"]}

    assert "minimum" in kinds
    assert "argument-shape" in kinds
    assert "argument-choice" in kinds
    assert "option-spelling" in kinds
    assert "option-choice" in kinds
    assert "exclusive-member" in kinds
    assert "exclusive-conflict" in kinds
    option_spellings = {
        candidate["shape"]["spelling"]
        for candidate in surface["candidates"]
        if candidate["kind"] == "option-spelling"
    }
    assert "--format" in option_spellings
    assert "-f" in option_spellings
    assert not any(
        candidate["kind"] == "option-spelling"
        and candidate["shape"]["spelling"] in {"--quiet", "--debug", "--color"}
        for candidate in surface["candidates"]
    )


def test_surface_keeps_explicit_shared_ids_unique_within_each_route():
    app = _complex_cli()
    surface = export_cli_surface(app)
    matching = [
        action["id"]
        for route in surface["routes"]
        for action in route["actions"]
        if action["id"] == "shared-profile"
    ]

    assert len(matching) == 3


def test_surface_signature_ignores_help_copy_but_tracks_option_shape():
    original = export_cli_surface(_complex_cli())
    changed_help = _complex_cli()
    changed_help.command_parsers["inspect"]._option_string_actions["--force"].help = "different wording"
    rewritten = export_cli_surface(changed_help)
    original_cases = {case["id"]: case["signature"] for case in original["candidates"]}
    rewritten_cases = {case["id"]: case["signature"] for case in rewritten["candidates"]}
    force_case = next(
        case_id
        for case_id in original_cases
        if "/option-spelling/option:route:entrypoint:surface-demo/inspect/--force/--force" in case_id
    )
    assert original_cases[force_case] == rewritten_cases[force_case]

    altered_registry = CliRegistry(IDENTITY, prog="surface-demo", description="Inspect.")
    altered_registry.register(
        VerbSpec(
            "inspect",
            description="changed description",
            options=(OptionSpec(("--force",), "force", parser_kwargs={"action": "store_true", "default": True}),),
            handler=lambda *_: 0,
        )
    )
    changed = export_cli_surface(altered_registry.build())
    changed_cases = {case["id"]: case["signature"] for case in changed["candidates"]}
    changed_force_id = next(case_id for case_id in changed_cases if "--force" in case_id)
    assert original_cases[force_case] != changed_cases[changed_force_id]


def test_surface_marks_custom_validator_opaque_but_unenumerable_choices_incomplete():
    def configure(parser):
        parser.add_subparsers(dest="subcommand").add_parser("child")

    registry = CliRegistry(IDENTITY, prog="surface-demo", description="Inspect.")
    registry.register(
        VerbSpec(
            "inspect",
            description="inspect",
            arguments=(ArgumentSpec("value", "validated", parser_kwargs={"type": lambda value: value}),),
            options=(OptionSpec(("--range",), "range", parser_kwargs={"choices": range(5)}),),
            configure=configure,
            handler=lambda *_: 0,
        )
    )
    app = registry.build()
    surface = export_cli_surface(app)
    route = surface["routes"][0]

    assert surface["syntax_complete"] is False
    assert route["syntax_complete"] is False
    assert any(field.endswith(".type") for field in route["opaque_fields"])
    assert any("cannot enumerate parser field" in item for item in surface["incomplete"])

    action = next(
        action
        for action in app.command_parsers["inspect"]._actions
        if isinstance(action, argparse._SubParsersAction)
    )
    action.choices["broken"] = object()
    assert export_cli_surface(app)["syntax_complete"] is False


def test_surface_rejects_duplicate_local_ids_bad_interactions_and_candidate_overflow():
    with pytest.raises(SurfaceError, match="duplicate argument or option IDs"):
        export_cli_surface(_complex_cli(duplicate_ids=True))

    app = _complex_cli()
    with pytest.raises(SurfaceError, match="unknown route"):
        export_cli_surface(
            app,
            interaction_groups=({"id": "bad", "route_id": "missing", "option_ids": ["x"]},),
        )
    with pytest.raises(SurfaceError, match="unknown options"):
        export_cli_surface(
            app,
            interaction_groups=(
                {
                    "id": "bad",
                    "route_id": "route:entrypoint:surface-demo/inspect",
                    "option_ids": ["missing"],
                },
            ),
        )
    with pytest.raises(SurfaceError, match="non-empty string list"):
        export_cli_surface(
            app,
            interaction_groups=(
                {
                    "id": "bad",
                    "route_id": "route:entrypoint:surface-demo/inspect",
                    "option_ids": "--format",
                },
            ),
        )
    with pytest.raises(SurfaceLimitError, match="more than 1 candidates"):
        export_cli_surface(app, max_candidates=1)
    routes = export_cli_surface(app, max_candidates=4096)["routes"]
    candidates = _generate_candidates(routes, (), max_candidates=4096)
    assert _generate_candidates(routes, (), max_candidates=len(candidates)) == candidates
    with pytest.raises(SurfaceLimitError):
        _generate_candidates(routes, (), max_candidates=len(candidates) - 1)
    with pytest.raises(ValueError, match="must be positive"):
        export_cli_surface(app, max_candidates=0)
    with pytest.raises(TypeError, match="must be an integer"):
        export_cli_surface(app, max_candidates=True)
    with pytest.raises(TypeError, match="must be a sequence"):
        export_cli_surface(app, interaction_groups="bad")
    with pytest.raises(TypeError, match=r"interaction_groups\[0\] must be a mapping"):
        export_cli_surface(app, interaction_groups=("bad",))


def test_required_nested_subcommand_is_a_prefix_not_a_false_invocation_candidate():
    def configure(parser):
        parser.add_argument("--before-child", action="store_true")
        children = parser.add_subparsers(dest="operation", required=True)
        child = children.add_parser("apply")
        child.add_argument("--limit", type=int)

    registry = CliRegistry(IDENTITY, prog="surface-demo", description="Run jobs.")
    registry.register(
        VerbSpec(
            "run",
            description="run a job",
            configure=configure,
            handler=lambda *_: 0,
        )
    )
    app = registry.build()
    surface = export_cli_surface(app)
    routes = {route["id"]: route for route in surface["routes"]}
    prefix = routes["route:entrypoint:surface-demo/run"]
    child_route = routes["route:entrypoint:surface-demo/run/apply"]

    assert prefix["kind"] == "route-prefix"
    assert prefix["subcommands_required"] is True
    assert prefix["subcommand_groups"] == [
        {
            "destination": "operation",
            "required": True,
            "subcommands": [child_route["id"]],
        }
    ]
    assert not any(
        candidate["route_id"] == prefix["id"]
        for candidate in surface["candidates"]
    )
    actions = {action["flags"][0]: action for action in child_route["actions"]}
    assert actions["--before-child"]["before_nested_subcommand"] is True
    assert actions["--limit"]["before_nested_subcommand"] is False
    assert actions["--before-child"]["id"] == (
        "option:route:entrypoint:surface-demo/run/apply/parser:run/--before-child"
    )
    assert actions["--limit"]["id"] == (
        "option:route:entrypoint:surface-demo/run/apply/--limit"
    )
    parsed = app.parser.parse_args(
        ["run", "--before-child", "apply", "--limit", "3"]
    )
    assert parsed.before_child is True
    assert parsed.limit == 3


def test_leaf_positional_is_not_misreported_as_a_parent_argument():
    def configure(parser):
        child = parser.add_subparsers(dest="operation").add_parser("detail")
        child.add_argument("resource")

    registry = CliRegistry(IDENTITY, prog="surface-demo", description="Inspect.")
    registry.register(
        VerbSpec("inspect", description="inspect", configure=configure, handler=lambda *_: 0)
    )
    surface = export_cli_surface(registry.build())
    detail = next(route for route in surface["routes"] if route["path"] == ["inspect", "detail"])
    resource = next(action for action in detail["actions"] if action["kind"] == "argument")
    assert resource["parser_path"] == ["inspect", "detail"]
    assert resource["before_nested_subcommand"] is False


def test_surface_single_command_retains_metadata_and_standalone_defaults():
    registry = CliRegistry(
        IDENTITY,
        prog="surface-demo",
        description="run one operation",
        single_command=True,
        no_args_action=True,
        global_options=(OptionSpec(("--target",), "target", parser_kwargs={"default": "local"}),),
    )
    registry.register(
        VerbSpec(
            "run",
            description="run a job",
            options=(OptionSpec(("--verbose-result",), "verbose", parser_kwargs={"action": "store_true"}),),
            handler=lambda *_: 0,
        )
    )
    app = registry.build()
    surface = export_cli_surface(app)

    assert surface["entrypoint"]["single_command"] is True
    assert surface["entrypoint"]["builtins"] == [
        "help", "version", "--help", "--version"
    ]
    route = surface["routes"][0]
    assert route["id"] == "route:entrypoint:surface-demo"
    assert route["single_command"] is True
    assert route["no_args_action"] is True
    assert route["behavior"] == []
    target = next(action for action in route["actions"] if action["id"].endswith("--target"))
    assert target["effective_default"] == "local"
    assert target["placement"] == {
        "before_verb": False,
        "after_verb": True,
        "single_command_invocation": True,
    }


def test_surface_signature_tracks_empty_single_command_invocation_behavior():
    def minimum_signature(no_args_action: bool) -> str:
        registry = CliRegistry(
            IDENTITY,
            prog="surface-demo",
            description="run one operation",
            single_command=True,
            no_args_action=no_args_action,
        )
        registry.register(VerbSpec("run", description="run", handler=lambda *_: 0))
        surface = export_cli_surface(registry.build())
        return next(
            candidate["signature"]
            for candidate in surface["candidates"]
            if candidate["kind"] == "minimum"
        )

    assert minimum_signature(False) != minimum_signature(True)


@pytest.mark.parametrize(
    "matcher",
    (re.compile(r"-\d+x"), object()),
    ids=("custom", "uninspectable"),
)
def test_surface_marks_custom_or_uninspectable_negative_number_matchers_incomplete(
    matcher,
):
    registry = CliRegistry(IDENTITY, prog="surface-demo", description="Inspect.")

    def configure(parser):
        parser._negative_number_matcher = matcher

    registry.register(
        VerbSpec("inspect", description="inspect", configure=configure, handler=lambda *_: 0)
    )
    surface = export_cli_surface(registry.build())

    assert surface["syntax_complete"] is False
    assert any("negative-number matcher" in finding for finding in surface["incomplete"])


def test_surface_marks_callbacks_that_replace_parser_behavior_incomplete():
    registry = CliRegistry(IDENTITY, prog="surface-demo", description="Inspect.")

    def configure(parser):
        parser.add_argument("--visible", action="store_true")
        parser.parse_known_args = lambda args=None, namespace=None: (  # type: ignore[method-assign]
            argparse.Namespace(),
            [],
        )

    registry.register(
        VerbSpec("inspect", description="inspect", configure=configure, handler=lambda *_: 0)
    )
    surface = export_cli_surface(registry.build())
    route = next(route for route in surface["routes"] if route["path"] == ["inspect"])

    assert route["syntax_complete"] is False
    assert any(
        "overrides argparse syntax method parse_known_args" in finding
        for finding in surface["incomplete"]
    )
    assert any(
        action.get("flags") == ["--visible"] for action in route["actions"]
    )


def test_surface_marks_inconsistent_option_lookup_maps_incomplete():
    registry = CliRegistry(IDENTITY, prog="surface-demo", description="Inspect.")

    def configure(parser):
        action = parser.add_argument("--known", action="store_true")
        parser._option_string_actions["--ghost"] = action
        action.option_strings.append("--lost")

    registry.register(
        VerbSpec("inspect", description="inspect", configure=configure, handler=lambda *_: 0)
    )
    surface = export_cli_surface(registry.build())

    assert surface["syntax_complete"] is False
    assert any("lookup for '--ghost' does not match" in item for item in surface["incomplete"])
    assert any("action lookup for '--lost' does not match" in item for item in surface["incomplete"])


def test_surface_global_capture_handles_unregistered_global_action():
    registry = CliRegistry(
        IDENTITY,
        prog="surface-demo",
        description="Inspect.",
        global_options=(OptionSpec(("--scope",), "scope"),),
    )
    registry.register(VerbSpec("inspect", description="inspect", handler=lambda *_: 0))
    app = registry.build()
    app.parser._option_string_actions.pop("--scope")

    surface = export_cli_surface(app)

    assert surface["syntax_complete"] is False
    assert any("parser action lookup for '--scope' does not match" in item for item in surface["incomplete"])


def test_surface_marks_uncaptured_parser_level_defaults_incomplete():
    registry = CliRegistry(IDENTITY, prog="surface-demo", description="Inspect.")
    registry.register(
        VerbSpec(
            "inspect",
            description="inspect",
            configure=lambda parser: parser.set_defaults(mode="hidden-default"),
            handler=lambda *_: 0,
        )
    )
    surface = export_cli_surface(registry.build())

    assert surface["syntax_complete"] is False
    assert any(
        "uncaptured parser-level defaults: mode" in finding
        for finding in surface["incomplete"]
    )


def test_surface_captures_parser_defaults_applied_to_declared_actions():
    registry = CliRegistry(IDENTITY, prog="surface-demo", description="Inspect.")
    registry.register(
        VerbSpec(
            "inspect",
            description="inspect",
            options=(OptionSpec(("--mode",), "mode"),),
            configure=lambda parser: parser.set_defaults(mode="fast"),
            handler=lambda *_: 0,
        )
    )

    surface = export_cli_surface(registry.build())

    assert surface["syntax_complete"] is True
    mode = next(
        action
        for route in surface["routes"]
        for action in route["actions"]
        if action.get("flags") == ["--mode"]
    )
    assert mode["effective_default"] == "fast"
    assert not any("uncaptured parser-level defaults" in item for item in surface["incomplete"])


def test_surface_checks_the_multi_command_entrypoint_parser(monkeypatch):
    registry = CliRegistry(IDENTITY, prog="surface-demo", description="Inspect.")
    registry.register(VerbSpec("inspect", description="inspect", handler=lambda *_: 0))
    app = registry.build()
    app.parser.parse_args = lambda args=None, namespace=None: (  # type: ignore[method-assign]
        argparse.Namespace(),
    )

    surface = export_cli_surface(app)
    route = next(route for route in surface["routes"] if route["path"] == ["inspect"])

    assert surface["syntax_complete"] is False
    assert route["syntax_complete"] is False
    assert any(
        "route:entrypoint:surface-demo: parser overrides argparse syntax method parse_args"
        in reason
        for reason in surface["incomplete"]
    )

    parser_type = type(app.parser)

    def altered_optional(self, token):
        return None

    monkeypatch.setattr(parser_type, "_parse_optional", altered_optional)
    overridden = export_cli_surface(registry.build())
    assert overridden["syntax_complete"] is False
    assert any(
        "overrides argparse syntax method _parse_optional" in reason
        for reason in overridden["incomplete"]
    )


def test_parser_syntax_issues_detect_inconsistent_option_lookups():
    parser = argparse.ArgumentParser(add_help=False)
    action = parser.add_argument("--known")
    parser._option_string_actions["--alias"] = action
    parser._option_string_actions["--orphan"] = argparse.Action(
        option_strings=["--orphan"], dest="orphan"
    )
    del parser._option_string_actions["--known"]

    issues = _parser_syntax_issues(parser, route_id="route:test")

    assert any("option lookup for '--orphan'" in issue for issue in issues)
    assert any("option lookup for '--alias'" in issue for issue in issues)
    assert any("action lookup for '--known'" in issue for issue in issues)


def test_surface_marks_inherited_uninspectable_negative_number_matcher_incomplete():
    parser = argparse.ArgumentParser(prog="surface-demo")
    incomplete = []
    records = _describe_parser(
        parser,
        entrypoint_id="entrypoint:surface-demo",
        path=("inspect",),
        verb_specs=(),
        global_options=(),
        single_command=False,
        incomplete=incomplete,
        inherited_parser_settings=(
            {
                "parser_path": [],
                "allow_abbrev": False,
                "prefix_chars": "-",
                "fromfile_prefix_chars": None,
                "negative_number_matcher": None,
                "negative_number_matcher_custom": False,
            },
        ),
    )

    assert records[0]["syntax_complete"] is False
    assert any("uninspectable negative-number matcher" in item for item in incomplete)


def test_surface_requires_a_registered_cli_and_surfaces_json_is_valid():
    with pytest.raises(TypeError, match="RegisteredCli"):
        export_cli_surface(object())
    surface = export_cli_surface(_complex_cli())
    assert json.loads(render_cli_surface_json(surface)) == surface


def test_surface_signatures_are_canonical_for_unicode_payloads():
    payload = {"z": "last", "a": "café"}
    reversed_payload = {"a": "café", "z": "last"}
    expected = "sha256:" + hashlib.sha256(
        json.dumps(
            {"schema_version": 4, "payload": payload},
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=False,
        ).encode("utf-8")
    ).hexdigest()
    assert _signature(4, payload) == expected
    assert _signature(4, payload) == _signature(4, reversed_payload)


def test_candidate_choice_ids_hash_unicode_values_without_ascii_escaping():
    registry = CliRegistry(IDENTITY, prog="surface-demo", description="Inspect.")
    registry.register(
        VerbSpec(
            "inspect",
            description="inspect",
            arguments=(
                ArgumentSpec("format", "format", parser_kwargs={"choices": ("café",)}),
            ),
            options=(
                OptionSpec(("--mode",), "mode", parser_kwargs={"choices": ("café",)}),
            ),
            handler=lambda *_: 0,
        )
    )
    candidates = export_cli_surface(registry.build())["candidates"]
    suffix = hashlib.sha256(json.dumps("café", ensure_ascii=False).encode("utf-8")).hexdigest()[:10]
    choice_ids = {
        candidate["id"]
        for candidate in candidates
        if candidate["kind"] in {"argument-choice", "option-choice"}
    }
    assert f"case:route:entrypoint:surface-demo/inspect/argument-choice/argument:route:entrypoint:surface-demo/inspect/format/{suffix}" in choice_ids
    assert any(candidate_id.endswith(f"/{suffix}") for candidate_id in choice_ids)


def test_surface_json_renderer_keeps_sorted_unicode_keys_and_rejects_nonfinite_values():
    rendered = render_cli_surface_json({"z": "last", "a": "café"})
    assert rendered.index('"a"') < rendered.index('"z"')
    assert '"a": "café"' in rendered
    assert "\\u00e9" not in rendered
    with pytest.raises(ValueError, match="Out of range float values"):
        render_cli_surface_json({"not-finite": float("nan")})


def test_callable_labels_fall_back_when_only_one_import_attribute_is_a_string():
    class Parser:
        def __call__(self, value):
            return value

    parse = Parser()
    parse.__module__ = "consumer.handlers"
    parse.__qualname__ = None
    assert _callable_label(parse) == f"{type(parse).__module__}.{type(parse).__qualname__}"


def test_parser_action_context_is_frozen():
    parser = argparse.ArgumentParser(add_help=False)
    context = _ParserActionContext(
        parser_path=("run",),
        allow_abbrev=False,
        prefix_chars="-",
        fromfile_prefix_chars=None,
        actions=(),
        option_specs=(),
        argument_specs=(),
        group_titles={},
        mutex_groups={},
    )
    assert parser is not None
    with pytest.raises(FrozenInstanceError):
        context.allow_abbrev = True


def test_surface_normalization_handles_supported_and_opaque_values_deterministically():
    class State(Enum):
        READY = "ready"

    opaque = []
    assert _normalize(argparse.SUPPRESS, path="x", opaque=opaque) == {
        "kind": "suppressed"
    }
    assert _normalize(2.5, path="x", opaque=opaque) == 2.5
    assert _normalize(float("inf"), path="x", opaque=opaque) == {
        "opaque": "non-finite-float"
    }
    assert _normalize(State.READY, path="x", opaque=opaque) == "ready"
    assert _normalize({"b": 2, "a": [1, True]}, path="x", opaque=opaque) == {
        "a": [1, True], "b": 2
    }
    assert _normalize({1: "bad"}, path="x", opaque=opaque) == {
        "opaque": "builtins.dict"
    }
    assert _normalize(("a", "b"), path="x", opaque=opaque) == ["a", "b"]
    assert _normalize({"z", "a"}, path="x", opaque=opaque) == ["a", "z"]
    assert _normalize({"é", "zz"}, path="x", opaque=opaque) == ["zz", "é"]
    assert _normalize(str, path="x", opaque=opaque) == {"callable": "builtins.str"}
    assert _normalize(lambda value: value, path="x", opaque=opaque)["callable"].startswith(
        "test_surface."
    )
    assert _normalize(object(), path="x", opaque=opaque) == {
        "opaque": "builtins.object"
    }
    assert opaque == ["x", "x", "x", "x"]


def test_surface_minimum_value_and_choice_helpers_cover_nargs_shapes():
    assert _minimum_values(None) == 1
    assert _minimum_values(2) == 2
    assert _minimum_values(0) == 0
    assert _minimum_values("+") == 1
    assert _minimum_values(argparse.PARSER) == 1
    assert _minimum_values("?") == 0
    assert _canonical_flag(("-q",)) == "-q"
    assert _action_surface_id(
        "option", "route:test/run", ("run",), (), "--global"
    ) == "option:route:test/run/parser:<root>/--global"

    opaque = []
    assert _safe_choice_values(None, path="x.choices", opaque=opaque) is None
    assert _safe_choice_values(("a", "b"), path="x.choices", opaque=opaque) == ["a", "b"]
    assert _safe_choice_values({"b", "a"}, path="x.choices", opaque=opaque) == ["a", "b"]
    assert _safe_choice_values({"é", "zz"}, path="x.choices", opaque=opaque) == ["zz", "é"]
    assert _safe_choice_values(range(2), path="x.choices", opaque=opaque) == {
        "opaque": "builtins.range"
    }
    assert _safe_choice_values((object(),), path="x.choices", opaque=opaque) == {
        "opaque": "choice-value"
    }


def test_surface_action_scope_defaults_and_custom_action_are_described():
    parser = argparse.ArgumentParser(add_help=False)
    defaulted = parser.add_argument("--defaulted", default="yes", help="defaulted")
    suppressed = parser.add_argument("--suppressed", default=argparse.SUPPRESS)
    builtin_help = parser.add_argument("--help", dest="help", default="wrong")
    hidden = parser.add_argument("--hidden", help=argparse.SUPPRESS)

    class CustomAction(argparse.Action):
        def __call__(self, _parser, _namespace, _values, _option_string=None):
            return None

    custom = parser.add_argument("--custom", action=CustomAction, help="custom")
    global_spec = OptionSpec(("--global",), "global", parser_kwargs={"default": argparse.SUPPRESS})
    global_action = parser.add_argument("--global", default=argparse.SUPPRESS)
    common_action = parser.add_argument("--quiet", action="store_true", default=argparse.SUPPRESS)
    json_action = parser.add_argument("--json", action="store_true")
    progress_action = parser.add_argument("--progress", action="store_true")
    confirmation_action = parser.add_argument("--yes", action="store_true")
    integer_action = parser.add_argument("--integer", type=int)
    many_argument = parser.add_argument("many", nargs="*")
    local_spec = OptionSpec(("--local",), "local")
    local_action = parser.add_argument("--local")
    custom_unregistered = parser.add_argument("--unregistered")

    assert _effective_default(defaulted, scope="custom", global_options=(), single_command=False) == (True, "yes")
    assert _effective_default(builtin_help, scope="common", global_options=(), single_command=False) == (False, argparse.SUPPRESS)
    assert _effective_default(suppressed, scope="custom", global_options=(), single_command=True) == (False, argparse.SUPPRESS)
    assert _effective_default(global_action, scope="global", global_options=(global_spec,), single_command=False) == (False, argparse.SUPPRESS)
    assert _effective_default(global_action, scope="global", global_options=(), single_command=False) == (False, argparse.SUPPRESS)
    assert _effective_default(common_action, scope="common", global_options=(), single_command=False) == (True, None)
    assert _effective_default(suppressed, scope="custom", global_options=(), single_command=False) == (False, argparse.SUPPRESS)

    assert _scope(global_action, option_specs=(), global_options=(global_spec,), single_command=False)[0] == "global"
    assert _scope(common_action, option_specs=(), global_options=(), single_command=False)[0] == "common"
    for action in (json_action, progress_action, confirmation_action):
        assert _scope(action, option_specs=(), global_options=(), single_command=False)[0] == "common"
    assert _scope(local_action, option_specs=(local_spec,), global_options=(), single_command=False)[0] == "verb-local"
    assert _scope(custom_unregistered, option_specs=(), global_options=(), single_command=True)[0] == "custom"

    opaque = []
    def describe(action):
        return _surface_action(
            action,
            route_id="route:test",
            route_path=("run",),
            parser_path=("run",),
            option_specs=(),
            argument_specs=(),
            global_options=(),
            single_command=False,
            group_titles={},
            mutex_groups={},
            opaque=opaque,
        )

    assert describe(hidden)["description"] is None
    assert describe(hidden)["hidden"] is True
    assert describe(custom)["description"] == "custom"
    assert any(field.endswith(".action") for field in opaque)
    opaque_after_custom = list(opaque)
    assert describe(local_action)["action"] == "argparse._StoreAction"
    assert opaque == opaque_after_custom
    assert describe(integer_action)["type"] == {"callable": "builtins.int"}
    assert describe(integer_action)["metavar"] is None
    assert describe(local_action)["exclusive_group"] is None
    assert describe(local_action)["exclusive_required"] is False
    many = describe(many_argument)
    assert many["required"] is False
    assert many["minimum_values"] == 0
    assert many["metavar"] == "MANY"


def test_library_help_actions_are_not_reported_as_opaque_custom_actions():
    registry = CliRegistry(IDENTITY, prog="surface-demo", description="Inspect.")
    registry.register(VerbSpec("inspect", description="inspect", handler=lambda *_: 0))
    app = registry.build()
    opaque = []
    for parser in (app.parser, app.command_parsers["inspect"]):
        for action in parser._actions:
            if "--help" not in action.option_strings and "--version" not in action.option_strings:
                continue
            _surface_action(
                action,
                route_id="route:entrypoint:surface-demo/inspect",
                route_path=("inspect",),
                parser_path=(),
                option_specs=(),
                argument_specs=(),
                global_options=(),
                single_command=False,
                group_titles={},
                mutex_groups={},
                opaque=opaque,
            )
    assert opaque == []

    spoofed_help = type(
        "_HelpAction", (argparse.Action,), {"__module__": "cli_extended.parser"}
    )
    custom_parser = argparse.ArgumentParser(add_help=False)
    spoofed = custom_parser.add_argument("--spoofed", action=spoofed_help)
    _surface_action(
        spoofed,
        route_id="route:entrypoint:surface-demo/inspect",
        route_path=("inspect",),
        parser_path=("inspect",),
        option_specs=(),
        argument_specs=(),
        global_options=(),
        single_command=False,
        group_titles={},
        mutex_groups={},
        opaque=opaque,
    )
    assert any(field.endswith(".action") for field in opaque)


def test_nested_routes_keep_their_own_ids_and_parser_descriptions():
    def configure(parser):
        parser.add_subparsers(dest="operation").add_parser(
            "detail", description="Detailed operation."
        )

    registry = CliRegistry(IDENTITY, prog="surface-demo", description="Inspect.")
    registry.register(
        VerbSpec(
            "inspect",
            description="Inspect one item.",
            surface_id="stable-inspect",
            configure=configure,
            handler=lambda *_: 0,
        )
    )
    routes = export_cli_surface(registry.build())["routes"]
    route_by_path = {tuple(route["path"]): route for route in routes}
    assert route_by_path[("inspect",)]["id"] == "stable-inspect"
    nested = route_by_path[("inspect", "detail")]
    assert nested["id"] == "route:entrypoint:surface-demo/inspect/detail"
    assert nested["description"] == "Detailed operation."


def test_surface_exclusive_group_ids_and_conflicting_declarations():
    parser = argparse.ArgumentParser(add_help=False)
    group = parser.add_mutually_exclusive_group(required=True)
    left = group.add_argument("--left")
    right = group.add_argument("--right")
    inferred = _mutex_groups(parser, (), route_id="route:test")
    assert inferred[id(left)] == inferred[id(right)]
    assert inferred[id(left)][1] is True

    named = _mutex_groups(
        parser,
        (
            OptionSpec(("--left",), "left", mutually_exclusive_group="mode"),
            OptionSpec(("--right",), "right", mutually_exclusive_group="mode"),
        ),
        route_id="route:test",
    )
    assert named[id(left)][0] == "mode"

    with pytest.raises(SurfaceError, match="conflicting labels"):
        _mutex_groups(
            parser,
            (
                OptionSpec(("--left",), "left", mutually_exclusive_group="left-mode"),
                OptionSpec(("--right",), "right", mutually_exclusive_group="right-mode"),
            ),
            route_id="route:test",
        )


def test_candidate_dimensions_keep_non_library_common_options_and_reject_empty_interactions():
    route = {
        "id": "route:test/run",
        "path": ["run"],
        "kind": "invocation",
        "confirmation": False,
        "actions": [
            {
                "id": "option:custom-common",
                "kind": "option",
                "flags": ["--project-format"],
                "scope": "common",
                "exclusive_group": None,
                "exclusive_required": False,
                "required": False,
                "default": None,
                "choices": None,
                "parser_path": ["run"],
                "placement": {"before_verb": False},
                "hidden": False,
            }
        ],
    }
    candidates = _generate_candidates([route], (), max_candidates=10)
    assert any(
        candidate["kind"] == "option-spelling"
        and candidate["shape"]["spelling"] == "--project-format"
        for candidate in candidates
    )
    with pytest.raises(SurfaceError, match="non-empty string list"):
        _generate_candidates(
            [route],
            ({"id": "empty", "route_id": route["id"], "option_ids": []},),
            max_candidates=10,
        )


def test_surface_ids_delegates_missing_registry_metadata_and_parser_routes():
    assert _route_id("entry", VerbSpec("run", surface_id="stable-run", description="run"), ("run",)) == "stable-run"

    child_identity = CliIdentity("CHILD", "1.0", "Child", command="child-tool")
    child = CliRegistry(
        child_identity, prog="child-tool", description="Child commands."
    )
    child.register(VerbSpec("alpha", description="alpha", handler=lambda *_: 0))
    child.register(VerbSpec("beta", description="beta", handler=lambda *_: 0))

    parent = CliRegistry(IDENTITY, prog="surface-demo", description="Parent.")
    parent.register(
        VerbSpec("plugins", description="plugin commands", delegate=child.build())
    )
    delegated_surface = export_cli_surface(parent.build())
    routes = {route["id"]: route for route in delegated_surface["routes"]}
    group = routes["route:entrypoint:surface-demo/plugins"]
    assert group["kind"] == "delegate-group"
    assert group["parser_configured_by_callback"] is False
    assert group["syntax_complete"] is True
    assert group["subcommands"] == [
        "route:entrypoint:surface-demo/plugins/alpha",
        "route:entrypoint:surface-demo/plugins/beta",
    ]
    assert not any(
        candidate["route_id"] == group["id"]
        for candidate in delegated_surface["candidates"]
    )

    configured_parent = CliRegistry(
        IDENTITY, prog="surface-demo", description="Configured delegate."
    )
    configured_parent.register(
        VerbSpec(
            "plugins",
            description="plugin commands",
            delegate=child.build(),
            configure=lambda parser: parser.add_argument("--plugin-scope"),
        )
    )
    configured_parent.register(
        VerbSpec("status", description="show status", handler=lambda *_: 0)
    )
    configured_surface = export_cli_surface(configured_parent.build())
    configured_routes = {tuple(route["path"]): route for route in configured_surface["routes"]}
    configured_group = configured_routes[("plugins",)]
    assert configured_group["parser_configured_by_callback"] is True
    assert configured_group["syntax_complete"] is False
    assert configured_routes[("status",)]["syntax_complete"] is True
    assert all(
        route["syntax_complete"]
        for route in configured_surface["routes"]
        if route["path"][:1] == ["plugins"] and route is not configured_group
    )
    assert any(
        "delegated wrapper parser syntax is not applied" in reason
        for reason in configured_surface["incomplete"]
    )

    def delegated_group_surface(*, confirmation_required: bool):
        group_parent = CliRegistry(
            IDENTITY, prog="surface-demo", description="Group contract."
        )
        group_parent.register(
            VerbSpec(
                "plugins",
                description="plugin commands",
                mutating=True,
                confirmation_required=confirmation_required,
                delegate=child.build(),
            )
        )
        return export_cli_surface(group_parent.build())

    unconfirmed_group = delegated_group_surface(confirmation_required=False)
    confirmed_group = delegated_group_surface(confirmation_required=True)
    unconfirmed_contract = {
        candidate["id"]: candidate["signature"]
        for candidate in unconfirmed_group["candidates"]
    }
    confirmed_contract = {
        candidate["id"]: candidate["signature"]
        for candidate in confirmed_group["candidates"]
    }
    assert unconfirmed_group["routes"][0]["behavior"] == ["mutating"]
    assert unconfirmed_group["routes"][0]["confirmation"] is False
    assert confirmed_group["routes"][0]["confirmation"] is True
    assert all(
        unconfirmed_contract[case_id] != confirmed_contract[case_id]
        for case_id in unconfirmed_contract.keys() & confirmed_contract.keys()
    )

    single_child = CliRegistry(
        child_identity,
        prog="child-tool",
        description="One child command.",
        single_command=True,
    )
    single_child.register(
        VerbSpec(
            "run",
            description="run one child operation",
            mutating=True,
            confirmation_required=False,
            options=(OptionSpec(("--child-option",), "child option"),),
            handler=lambda *_: 0,
        )
    )
    single_parent = CliRegistry(
        IDENTITY, prog="surface-demo", description="Single delegate."
    )
    single_parent.register(
        VerbSpec("adapter", description="run adapter", delegate=single_child.build())
    )
    single_surface = export_cli_surface(single_parent.build())
    delegated_route = next(
        route for route in single_surface["routes"]
        if route["id"] == "route:entrypoint:surface-demo/adapter"
    )
    assert delegated_route["id"] == "route:entrypoint:surface-demo/adapter"
    assert delegated_route["delegated_metadata"][0]["name"] == "run"
    assert delegated_route["behavior"] == []
    assert delegated_route["confirmation"] is False
    assert delegated_route["delegated_metadata"][0]["behavior"] == ["mutating"]
    assert delegated_route["delegated_metadata"][0]["confirmation"] is False
    child_option = next(
        action
        for action in delegated_route["actions"]
        if action.get("flags") == ["--child-option"]
    )
    assert child_option["description"] == "child option"
    assert delegated_route["syntax_complete"] is True

    changed_child = CliRegistry(
        child_identity,
        prog="child-tool",
        description="One child command.",
        single_command=True,
    )
    changed_child.register(
        VerbSpec("run", description="run one child operation", handler=lambda *_: 0)
    )
    changed_parent = CliRegistry(
        IDENTITY, prog="surface-demo", description="Single delegate."
    )
    changed_parent.register(
        VerbSpec(
            "adapter",
            description="run adapter",
            delegate=changed_child.build(),
        )
    )
    changed_surface = export_cli_surface(changed_parent.build())
    changed_route = next(
        route
        for route in changed_surface["routes"]
        if route["id"] == delegated_route["id"]
    )
    original_candidates = {
        candidate["id"]: candidate["signature"]
        for candidate in single_surface["candidates"]
    }
    changed_candidates = {
        candidate["id"]: candidate["signature"]
        for candidate in changed_surface["candidates"]
    }
    assert any(
        original_candidates[case_id] != changed_candidates[case_id]
        for case_id in original_candidates.keys() & changed_candidates.keys()
    )

    wrapper_with_syntax = CliRegistry(
        IDENTITY, prog="surface-demo", description="Invalid wrapper grammar."
    )
    wrapper_with_syntax.register(
        VerbSpec(
            "adapter",
            description="run adapter",
            options=(OptionSpec(("--ignored",), "ignored wrapper option"),),
            delegate=single_child.build(),
        )
    )
    wrapper_surface = export_cli_surface(wrapper_with_syntax.build())
    assert wrapper_surface["syntax_complete"] is False
    assert any(
        "wrapper declares parser syntax" in reason
        for reason in wrapper_surface["incomplete"]
    )

    parent_global = CliRegistry(
        IDENTITY,
        prog="surface-demo",
        description="Inherited global option.",
        global_options=(OptionSpec(("--scope",), "scope"),),
    )
    parent_global.register(
        VerbSpec("adapter", description="run adapter", delegate=single_child.build())
    )
    global_surface = export_cli_surface(parent_global.build())
    assert global_surface["syntax_complete"] is False
    assert any(
        "does not register inherited global option(s): --scope" in reason
        for reason in global_surface["incomplete"]
    )

    multi_parent_global = CliRegistry(
        IDENTITY,
        prog="surface-demo",
        description="Multi-command inherited global option.",
        global_options=(OptionSpec(("--scope",), "scope"),),
    )
    multi_parent_global.register(
        VerbSpec("plugins", description="plugin commands", delegate=child.build())
    )
    multi_surface = export_cli_surface(multi_parent_global.build())
    assert multi_surface["syntax_complete"] is False
    assert all(
        not route["syntax_complete"]
        for route in multi_surface["routes"]
        if route["path"][:1] == ["plugins"] and route["kind"] != "delegate-group"
    )

    child_with_global = CliRegistry(
        child_identity,
        prog="child-tool",
        description="Inherited global option is installed.",
        single_command=True,
        global_options=(OptionSpec(("--scope",), "scope"),),
    )
    child_with_global.register(
        VerbSpec("run", description="run", handler=lambda *_: 0)
    )
    parent_with_global = CliRegistry(
        IDENTITY,
        prog="surface-demo",
        description="Inherited global option.",
        global_options=(OptionSpec(("--scope",), "scope"),),
    )
    parent_with_global.register(
        VerbSpec("adapter", description="run adapter", delegate=child_with_global.build())
    )
    assert export_cli_surface(parent_with_global.build())["syntax_complete"] is True

    child_with_mismatched_global = CliRegistry(
        child_identity,
        prog="child-tool",
        description="Incompatible inherited global option.",
        single_command=True,
        global_options=(
            OptionSpec(("--scope",), "scope", parser_kwargs={"action": "store"}),
        ),
    )
    child_with_mismatched_global.register(
        VerbSpec("run", description="run", handler=lambda *_: 0)
    )
    parent_with_boolean_global = CliRegistry(
        IDENTITY,
        prog="surface-demo",
        description="Boolean inherited global option.",
        global_options=(
            OptionSpec(("--scope",), "scope", parser_kwargs={"action": "store_true"}),
        ),
    )
    parent_with_boolean_global.register(
        VerbSpec(
            "adapter",
            description="run adapter",
            delegate=child_with_mismatched_global.build(),
        )
    )
    parent_with_boolean_global.register(
        VerbSpec("status", description="show status", handler=lambda *_: 0)
    )
    mismatched_surface = export_cli_surface(parent_with_boolean_global.build())
    assert mismatched_surface["syntax_complete"] is False
    assert any(
        "changes inherited global option semantics: --scope" in reason
        for reason in mismatched_surface["incomplete"]
    )
    assert all(
        not route["syntax_complete"]
        for route in mismatched_surface["routes"]
        if route["path"][:1] == ["adapter"]
    )
    assert next(
        route["syntax_complete"]
        for route in mismatched_surface["routes"]
        if route["path"] == ["status"]
    )

    child_with_different_default = CliRegistry(
        child_identity,
        prog="child-tool",
        description="Incompatible inherited global default.",
        single_command=True,
        global_options=(
            OptionSpec(("--scope",), "scope", parser_kwargs={"default": "child"}),
        ),
    )
    child_with_different_default.register(
        VerbSpec("run", description="run", handler=lambda *_: 0)
    )
    parent_with_default = CliRegistry(
        IDENTITY,
        prog="surface-demo",
        description="Inherited global default.",
        global_options=(
            OptionSpec(("--scope",), "scope", parser_kwargs={"default": "parent"}),
        ),
    )
    parent_with_default.register(
        VerbSpec(
            "adapter",
            description="run adapter",
            delegate=child_with_different_default.build(),
        )
    )
    default_mismatch = export_cli_surface(parent_with_default.build())
    assert default_mismatch["syntax_complete"] is False
    assert any(
        "changes inherited global option semantics: --scope" in reason
        for reason in default_mismatch["incomplete"]
    )

    middle_identity = CliIdentity("MIDDLE", "1.0", "Middle", command="middle-tool")

    def nested_surface(*, outer_mutating: bool):
        leaf = CliRegistry(
            child_identity,
            prog="child-tool",
            description="Leaf single command.",
            single_command=True,
            no_args_action=True,
        )
        leaf.register(
            VerbSpec(
                "run",
                description="run leaf operation",
                options=(OptionSpec(("--child-option",), "child option"),),
                handler=lambda *_: 0,
            )
        )
        middle = CliRegistry(
            middle_identity,
            prog="middle-tool",
            description="Middle commands.",
        )
        middle.register(
            VerbSpec("adapter", description="run adapter", delegate=leaf.build())
        )
        outer = CliRegistry(IDENTITY, prog="surface-demo", description="Outer CLI.")
        outer.register(
            VerbSpec(
                "plugins",
                description="plugin commands",
                mutating=outer_mutating,
                delegate=middle.build(),
            )
        )
        return export_cli_surface(outer.build())

    nested_unmutating = nested_surface(outer_mutating=False)
    nested_mutating = nested_surface(outer_mutating=True)
    nested_route = next(
        route
        for route in nested_unmutating["routes"]
        if route["path"] == ["plugins", "adapter"]
    )
    assert nested_route["syntax_complete"] is True
    assert [metadata["name"] for metadata in nested_route["delegated_metadata"]] == [
        "plugins",
        "run",
    ]
    nested_signatures = {
        candidate["id"]: candidate["signature"]
        for candidate in nested_unmutating["candidates"]
    }
    changed_nested_signatures = {
        candidate["id"]: candidate["signature"]
        for candidate in nested_mutating["candidates"]
    }
    assert any(
        nested_signatures[case_id] != changed_nested_signatures[case_id]
        for case_id in nested_signatures.keys() & changed_nested_signatures.keys()
    )

    simple = CliRegistry(
        IDENTITY, prog="surface-demo", description="Simple commands."
    )
    simple.register(
        VerbSpec("show", description="show one item", handler=lambda *_: 0)
    )
    simple_route = export_cli_surface(simple.build())["routes"][0]
    assert simple_route["parser_configured_by_callback"] is False

    no_child_registry = replace(single_child.build(), registered_verbs=())
    incomplete_single: list[str] = []
    fallback_single = _walk_registered_cli(
        no_child_registry,
        entrypoint_id="entrypoint:child-tool",
        path_prefix=(),
        incomplete=incomplete_single,
    )
    assert incomplete_single
    assert fallback_single

    direct = _complex_cli()
    missing_registry = replace(direct, registered_verbs=())
    incomplete: list[str] = []
    fallback_routes = _walk_registered_cli(
        missing_registry,
        entrypoint_id="entrypoint:surface-demo",
        path_prefix=(),
        incomplete=incomplete,
    )
    assert incomplete
    assert any(route["path"] == ["inspect"] for route in fallback_routes)
    assert fallback_routes[0]["confirmation"] is False
    assert fallback_routes[0]["parser_configured_by_callback"] is False
    assert any(
        action.get("scope") == "common"
        and action["placement"]["single_command_invocation"] is False
        for action in fallback_routes[0]["actions"]
    )

    missing_parser = _complex_cli()
    missing_parser.command_parsers.pop("inspect")
    assert export_cli_surface(missing_parser)["syntax_complete"] is False

    duplicate_routes = CliRegistry(
        IDENTITY, prog="surface-demo", description="Duplicate routes."
    )
    duplicate_routes.register(
        VerbSpec("first", description="first", surface_id="shared", handler=lambda *_: 0)
    )
    duplicate_routes.register(
        VerbSpec("second", description="second", surface_id="shared", handler=lambda *_: 0)
    )
    with pytest.raises(SurfaceError, match="duplicate route IDs"):
        export_cli_surface(duplicate_routes.build())


def test_surface_marks_ambiguous_nested_parser_groups_and_unportable_choices_incomplete():
    parser = argparse.ArgumentParser(add_help=False)
    first_group = parser.add_subparsers(dest="first")
    first_group.add_parser("one")
    second_parent = argparse.ArgumentParser(add_help=False)
    second_group = second_parent.add_subparsers(dest="second")
    second_group.add_parser("two")
    parser._actions.append(second_group)

    incomplete: list[str] = []
    records = _describe_parser(
        parser,
        entrypoint_id="entrypoint:surface-demo",
        path=("run",),
        verb_specs=(),
        global_options=(),
        single_command=False,
        incomplete=incomplete,
    )
    assert records[0]["syntax_complete"] is False
    assert any("multiple nested subcommand groups" in item for item in incomplete)

    registry = CliRegistry(IDENTITY, prog="surface-demo", description="Inspect.")
    registry.register(
        VerbSpec(
            "inspect",
            description="inspect",
            options=(OptionSpec(("--odd-choice",), "odd", parser_kwargs={"choices": ({},)}),),
            handler=lambda *_: 0,
        )
    )
    surface = export_cli_surface(registry.build())
    assert surface["syntax_complete"] is False


def test_surface_rejects_malformed_and_duplicate_interaction_dimensions():
    app = _complex_cli()
    route = "route:entrypoint:surface-demo/inspect"
    valid = {"id": "same", "route_id": route, "option_ids": ["format-id"]}
    with pytest.raises(SurfaceError, match="non-empty id"):
        export_cli_surface(app, interaction_groups=({"id": "", "route_id": route, "option_ids": ["format-id"]},))
    with pytest.raises(SurfaceError, match="non-invocable route"):
        _generate_candidates(
            [{"id": route, "kind": "route-prefix", "path": ["inspect"], "actions": []}],
            ({"id": "bad", "route_id": route, "option_ids": ["format-id"]},),
            max_candidates=10,
        )
    with pytest.raises(SurfaceError, match="repeats an option ID"):
        export_cli_surface(app, interaction_groups=({**valid, "option_ids": ["format-id", "format-id"]},))
    with pytest.raises(SurfaceError, match="duplicate IDs"):
        export_cli_surface(app, interaction_groups=(valid, valid))

    ambiguous = CliRegistry(IDENTITY, prog="surface-demo", description="Ambiguous options.")
    ambiguous.register(VerbSpec("show", description="show", handler=lambda *_: 0))
    ambiguous.register(
        VerbSpec(
            "watch-one",
            description="watch one",
            options=(OptionSpec(("--poll-one",), "poll", surface_id="shared-poll"),),
            handler=lambda *_: 0,
        )
    )
    ambiguous.register(
        VerbSpec(
            "watch-two",
            description="watch two",
            options=(OptionSpec(("--poll-two",), "poll", surface_id="shared-poll"),),
            handler=lambda *_: 0,
        )
    )
    with pytest.raises(SurfaceError, match="ambiguous option IDs: shared-poll"):
        export_cli_surface(
            ambiguous.build(),
            interaction_groups=(
                {
                    "id": "foreign-option",
                    "route_id": "route:entrypoint:surface-demo/show",
                    "option_ids": ["shared-poll"],
                },
            ),
        )


@pytest.mark.parametrize("surface_id", (1, "", "contains whitespace"))
def test_registry_surface_ids_must_be_nonempty_whitespace_free_tokens(surface_id):
    with pytest.raises(ValueError, match="surface_id"):
        OptionSpec(("--option",), "option", surface_id=surface_id)
    with pytest.raises(ValueError, match="surface_id"):
        ArgumentSpec("argument", "argument", surface_id=surface_id)
    with pytest.raises(ValueError, match="surface_id"):
        VerbSpec("verb", description="verb", surface_id=surface_id)
