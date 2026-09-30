from __future__ import annotations

import argparse
import json
from dataclasses import replace
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
    _describe_parser,
    _effective_default,
    _generate_candidates,
    _minimum_values,
    _mutex_groups,
    _normalize,
    _route_id,
    _safe_choice_values,
    _scope,
    _surface_action,
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
    assert inspect["parser_settings"] == [
        {
            "parser_path": [],
            "allow_abbrev": False,
            "prefix_chars": "-",
            "fromfile_prefix_chars": None,
        },
        {
            "parser_path": ["inspect"],
            "allow_abbrev": False,
            "prefix_chars": "-",
            "fromfile_prefix_chars": None,
        },
    ]
    assert [
        setting["parser_path"] for setting in detail["parser_settings"]
    ] == [[], ["inspect"], ["inspect", "detail"]]
    assert inspect["subcommands"] == [detail["id"]]
    assert detail["aliases"] == ["d"]
    assert actions["shared-profile"]["scope"] == "global"
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
    parsed = app.parser.parse_args(
        ["run", "--before-child", "apply", "--limit", "3"]
    )
    assert parsed.before_child is True
    assert parsed.limit == 3


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
    route = surface["routes"][0]
    assert route["id"] == "route:entrypoint:surface-demo"
    assert route["behavior"] == []
    target = next(action for action in route["actions"] if action["id"].endswith("--target"))
    assert target["effective_default"] == "local"
    assert target["placement"] == {
        "before_verb": False,
        "after_verb": True,
        "single_command_invocation": True,
    }


def test_surface_requires_a_registered_cli_and_surfaces_json_is_valid():
    with pytest.raises(TypeError, match="RegisteredCli"):
        export_cli_surface(object())
    surface = export_cli_surface(_complex_cli())
    assert json.loads(render_cli_surface_json(surface)) == surface


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

    opaque = []
    assert _safe_choice_values(None, path="x.choices", opaque=opaque) is None
    assert _safe_choice_values(("a", "b"), path="x.choices", opaque=opaque) == ["a", "b"]
    assert _safe_choice_values({"b", "a"}, path="x.choices", opaque=opaque) == ["a", "b"]
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
    assert describe(custom)["description"] == "custom"
    assert any(field.endswith(".action") for field in opaque)


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
    assert group["subcommands"] == [
        "route:entrypoint:surface-demo/plugins/alpha",
        "route:entrypoint:surface-demo/plugins/beta",
    ]

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


@pytest.mark.parametrize("surface_id", (1, "", "contains whitespace"))
def test_registry_surface_ids_must_be_nonempty_whitespace_free_tokens(surface_id):
    with pytest.raises(ValueError, match="surface_id"):
        OptionSpec(("--option",), "option", surface_id=surface_id)
    with pytest.raises(ValueError, match="surface_id"):
        ArgumentSpec("argument", "argument", surface_id=surface_id)
    with pytest.raises(ValueError, match="surface_id"):
        VerbSpec("verb", description="verb", surface_id=surface_id)
