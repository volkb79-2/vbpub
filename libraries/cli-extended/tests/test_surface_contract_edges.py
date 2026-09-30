from __future__ import annotations

import argparse
import hashlib
import json
from dataclasses import FrozenInstanceError

import pytest

from cli_extended import (
    ArgumentSpec,
    CliIdentity,
    CliRegistry,
    OptionSpec,
    RegisteredCli,
    ReviewCatalog,
    ReviewCase,
    ReviewCatalogError,
    SurfaceLimitError,
    SurfaceReport,
    VerbSpec,
    assert_cli_case_tests,
    export_cli_surface,
    load_cli_review_catalog,
    render_cli_review_template,
    render_cli_surface_json,
    render_cli_surface_markdown,
)
from cli_extended.review import (
    _markdown_cell,
    _review_findings,
)
from cli_extended.surface import (
    _ParserActionContext,
    _generate_candidates,
    _normalize,
    _safe_choice_values,
    _signature,
)
from cli_extended.surface_cli import _load_factory, main as surface_cli_main


def _review_case(
    case_id: str,
    *,
    state: str = "active",
    decision: str = "accept",
    invocation: tuple[str, ...] = (),
    signature: str = "signature",
    retirement_reason: str = "",
) -> ReviewCase:
    return ReviewCase(
        case_id=case_id,
        state=state,
        decision=decision,
        reviewed_signature=signature,
        rationale="reviewed behavior",
        invocation=invocation,
        invocation_declared=True,
        expected_exit_status=0,
        expected_stdout_contains="",
        expected_stderr_contains="",
        effects=(),
        effects_declared=True,
        test_ids=("tests/test_cli.py::test_invocation",),
        retirement_reason=retirement_reason,
    )


def _candidate(case_id: str, route_id: str, kind: str, *, members=(), shape=None):
    return {
        "id": case_id,
        "route_id": route_id,
        "signature": "signature",
        "kind": kind,
        "members": list(members),
        "shape": {} if shape is None else shape,
    }


def _review_findings_for(
    route,
    candidate,
    invocation,
    *,
    complete=True,
    incomplete=(),
    all_routes=None,
    entrypoint=None,
):
    case = _review_case(candidate["id"], invocation=tuple(invocation))
    surface = {
        "entrypoint": {"allow_abbrev": False} if entrypoint is None else entrypoint,
        "routes": [route] if all_routes is None else all_routes,
        "candidates": [candidate],
        "syntax_complete": complete,
        "incomplete": list(incomplete),
    }
    if complete is None:
        surface.pop("syntax_complete")
    return _review_findings(
        surface,
        ReviewCatalog("audit-tool", 8, (), (case,)),
    )


def _route(route_id="route:entrypoint:audit-tool/deploy", path=("deploy",), actions=()):
    return {
        "id": route_id,
        "path": list(path),
        "aliases": [],
        "kind": "invocation",
        "actions": list(actions),
    }


def test_review_model_objects_are_immutable_and_registered_cli_defaults_are_false():
    case = _review_case("case:one")
    catalog = ReviewCatalog("audit-tool", 1, (), (case,))
    report = SurfaceReport()
    context = _ParserActionContext(
        parser_path=(),
        allow_abbrev=False,
        prefix_chars="-",
        fromfile_prefix_chars=None,
        actions=(),
        option_specs=(),
        argument_specs=(),
        group_titles={},
        mutex_groups={},
    )

    for value, attribute, replacement in (
        (case, "state", "retired"),
        (catalog, "cli_id", "other"),
        (report, "findings", ("changed",)),
        (context, "allow_abbrev", True),
    ):
        with pytest.raises(FrozenInstanceError):
            setattr(value, attribute, replacement)

    identity = CliIdentity("AUDIT", "1.0", "Audit Tool", command="audit-tool")
    cli = RegisteredCli(identity, argparse.ArgumentParser(), {}, {})
    assert cli.single_command is False
    assert cli.allow_abbrev is False


def test_catalog_accepts_the_exact_positive_candidate_limit(tmp_path):
    path = tmp_path / "review.toml"
    path.write_text(
        'schema_version = 1\ncli_id = "audit-tool"\nmax_candidates = 1\n',
        encoding="utf-8",
    )

    assert load_cli_review_catalog(path).max_candidates == 1


def test_markdown_rows_preserve_empty_fallbacks_shapes_and_review_dispositions():
    retired = _review_case(
        "case:removed-retired",
        state="retired",
        decision="refuse",
        retirement_reason="workflow removed",
    )
    stale = _review_case("case:removed-active", state="active", decision="accept")
    surface = {
        "schema_version": 1,
        "entrypoint": {"command": "audit-tool"},
        "routes": [
            {
                "id": "route:entrypoint:audit-tool",
                "path": [],
                "kind": "invocation",
                "aliases": ["at"],
                "subcommand_groups": [
                    {"destination": "commands", "required": False, "subcommands": []}
                ],
                "description": "root help text",
                "group": "TOOLS",
                "behavior": ["read-only"],
                "parser_settings": [
                    {
                        "parser_path": [],
                        "allow_abbrev": False,
                        "prefix_chars": "-",
                        "fromfile_prefix_chars": None,
                    }
                ],
                "syntax_complete": True,
                "actions": [
                    {
                        "id": "option:root/--plain",
                        "kind": "option",
                        "flags": ["--plain"],
                        "name": "",
                        "metavar": "VALUE",
                        "nargs": None,
                        "required": False,
                        "choices": None,
                        "effective_default": None,
                        "exclusive_group": None,
                        "scope": "custom",
                        "placement": {"before_verb": True, "after_verb": False},
                        "parser_path": [],
                        "before_nested_subcommand": False,
                        "help_group": "OPTIONS",
                    },
                    {
                        "id": "option:root/--pair",
                        "kind": "option",
                        "flags": ["--pair"],
                        "name": "",
                        "metavar": "ITEM",
                        "nargs": 2,
                        "required": False,
                        "choices": None,
                        "effective_default": None,
                        "exclusive_group": None,
                        "scope": "custom",
                        "placement": {"before_verb": False, "after_verb": True},
                        "parser_path": ["run"],
                        "before_nested_subcommand": False,
                        "help_group": "OPTIONS",
                    },
                    {
                        "id": "argument:root/resource",
                        "kind": "argument",
                        "name": "resource",
                        "metavar": "RESOURCE",
                        "nargs": 2,
                        "required": True,
                        "choices": ["雪"],
                        "effective_default": None,
                        "exclusive_group": None,
                        "scope": "positional",
                        "parser_path": ["run"],
                        "before_nested_subcommand": False,
                    },
                ],
            }
        ],
        "candidates": [
            {
                "id": "case:current",
                "route_id": "route:entrypoint:audit-tool",
                "signature": "new-signature",
                "kind": "minimum",
                "members": [],
                "shape": {},
            }
        ],
        "syntax_complete": True,
        "incomplete": ["opaque field"],
    }

    text = render_cli_surface_markdown(
        surface,
        ReviewCatalog("audit-tool", 8, (), (retired, stale)),
    )

    assert "audit-tool (aliases: at)" in text
    assert "commands: none" in text
    assert "root help text" in text
    assert "TOOLS" in text
    assert "<entrypoint>: allow_abbrev=no" in text
    assert "option:root/--plain | option | --plain | VALUE" in text
    assert "; parser <entrypoint>" in text
    assert '{"metavar": "ITEM", "nargs": 2}' in text
    assert '{"metavar": "RESOURCE", "nargs": 2}' in text
    assert "; before_verb" in text
    assert "; parser run" in text
    assert "雪" in text
    assert "refuse" in text
    assert "workflow removed" in text
    assert "STALE: disposition required" in text
    assert "Surface inventory is incomplete" in render_cli_surface_markdown(
        {key: value for key, value in surface.items() if key != "syntax_complete"},
        ReviewCatalog("audit-tool", 8, (), (retired, stale)),
    )

    cell = _markdown_cell({"z": "雪", "a": "ö"})
    assert cell == '{"a": "ö", "z": "雪"}'


def test_surface_json_is_sorted_unicode_and_rejects_nonfinite_values():
    text = render_cli_surface_json({"z": "雪", "a": 1})
    assert text.index('"a"') < text.index('"z"')
    assert "雪" in text
    assert "\\u96ea" not in text
    with pytest.raises(ValueError, match="Out of range float values"):
        render_cli_surface_json({"number": float("nan")})


def test_surface_signatures_are_sorted_and_utf8_encoded():
    payload = {"z": "雪", "a": 1}
    expected_json = json.dumps(
        {"schema_version": 1, "payload": payload},
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
    )
    expected = "sha256:" + hashlib.sha256(expected_json.encode("utf-8")).hexdigest()

    assert _signature(1, payload) == expected
    assert _signature(1, {"a": 1, "z": "雪"}) == expected


def test_review_template_writes_unicode_case_ids_as_utf8():
    template = render_cli_review_template(
        {
            "candidates": [
                {
                    "id": "case:雪",
                    "signature": "sha256:current",
                    "kind": "minimum",
                    "route_id": "route:entrypoint:audit-tool",
                    "members": [],
                }
            ]
        },
        ReviewCatalog("audit-tool", 8, (), ()),
    )

    assert 'id = "case:雪"' in template
    assert "\\u96ea" not in template


def test_surface_normalization_uses_type_fallback_and_unicode_choice_order():
    def callable_without_module():
        return None

    callable_without_module.__module__ = None
    opaque: list[str] = []

    assert _normalize(callable_without_module, path="callable", opaque=opaque) == {
        "callable": "builtins.function"
    }
    assert _normalize({"z", "雪"}, path="set", opaque=opaque) == ["z", "雪"]
    assert _safe_choice_values({"z", "雪"}, path="choices", opaque=opaque) == [
        "z",
        "雪",
    ]
    assert opaque == ["callable"]


def test_surface_export_records_option_and_argument_edges_and_candidate_boundaries():
    identity = CliIdentity("SURFACE", "1.0", "Surface Tool", command="surface-tool")

    class LocallyNamedAction(argparse.Action):
        def __call__(self, parser, namespace, values, option_string=None):
            setattr(namespace, self.dest, values)

    LocallyNamedAction.__module__ = "cli_extended.parser"

    def configure(parser):
        parser.add_argument("--hidden", help=argparse.SUPPRESS)
        parser.add_argument("--custom-action", action=LocallyNamedAction)
        nested = parser.add_subparsers(dest="operation", required=True)
        child = nested.add_parser("child", description="child parser description")
        child.add_argument("leaf")

    registry = CliRegistry(
        identity,
        description="inspect surfaces",
        prog="surface-tool",
        global_options=(OptionSpec(("--root",), "root option"),),
    )
    registry.register(
        VerbSpec(
            "inspect",
            description="inspect resources",
            mutating=True,
            options=(
                OptionSpec(("--explicit",), "path", parser_kwargs={"metavar": "FILE"}),
                OptionSpec(("--count",), "count", parser_kwargs={"type": int}),
                OptionSpec(("--mode",), "mode", parser_kwargs={"choices": ("雪", "z")}),
                OptionSpec(("--defaulted",), "default", parser_kwargs={"default": False}),
            ),
            arguments=(
                ArgumentSpec(
                    "maybe",
                    "optional positional",
                    parser_kwargs={"nargs": "?", "choices": ("z", "雪")},
                ),
            ),
            configure=configure,
            handler=lambda *_: 0,
        )
    )
    app = registry.build()
    surface = export_cli_surface(app, max_candidates=100)
    routes = {tuple(route["path"]): route for route in surface["routes"]}
    parent = routes[("inspect",)]
    child = routes[("inspect", "child")]
    parent_actions = {action.get("name", action.get("flags", [None])[0]): action for action in parent["actions"]}

    assert parent["confirmation"] is True
    assert parent["parser_configured_by_callback"] is True
    assert parent_actions["--hidden"]["hidden"] is True
    assert parent_actions["--count"]["type"] == {"callable": "builtins.int"}
    assert parent_actions["--explicit"]["metavar"] == "FILE"
    assert parent_actions["maybe"]["required"] is False
    assert parent_actions["--defaulted"]["exclusive_required"] is False
    assert child["description"] == "child parser description"
    assert child["actions"][-1]["before_nested_subcommand"] is False
    root_option = next(
        action for action in child["actions"] if action.get("flags") == ["--root"]
    )
    assert root_option["scope"] == "global"
    assert root_option["parser_path"] == ["inspect"]
    assert parent["opaque_fields"] == [
        "option:route:entrypoint:surface-tool/inspect/--custom-action.action"
    ]
    for spelling in ("--json", "--progress", "--yes"):
        assert parent_actions[spelling]["scope"] == "common"

    generated = {
        candidate["id"]: candidate for candidate in surface["candidates"]
    }
    defaulted_minimum = generated[
        "case:route:entrypoint:surface-tool/inspect/child/minimum"
    ]
    assert "option:route:entrypoint:surface-tool/inspect/child/parser:inspect/--defaulted" in (
        defaulted_minimum["shape"]["defaulted_options"]
    )
    choice_digest = hashlib.sha256(
        json.dumps("雪", ensure_ascii=False).encode("utf-8")
    ).hexdigest()[:10]
    assert any(
        f"argument-choice/argument:route:entrypoint:surface-tool/inspect/child/parser:inspect/maybe/{choice_digest}"
        in candidate_id
        for candidate_id in generated
    )
    option_choice_digest = hashlib.sha256(
        json.dumps("雪", ensure_ascii=False).encode("utf-8")
    ).hexdigest()[:10]
    assert any(
        f"option-choice/option:route:entrypoint:surface-tool/inspect/child/parser:inspect/--mode/{option_choice_digest}"
        in candidate_id
        for candidate_id in generated
    )

    simple = CliRegistry(
        identity, description="surface tool", prog="surface-tool"
    )
    simple.register(
        VerbSpec("show", description="show one item", handler=lambda *_: 0)
    )
    simple_surface = export_cli_surface(simple.build(), max_candidates=100)
    assert any(
        candidate["kind"] == "minimum"
        and candidate["route_id"] == "route:entrypoint:surface-tool/show"
        for candidate in simple_surface["candidates"]
    )
    with pytest.raises(SurfaceLimitError, match="more than 1"):
        export_cli_surface(app, max_candidates=1)


def test_candidate_builder_keeps_common_option_exclusions_scoped_and_groups_real():
    route_id = "route:entrypoint:audit-tool/deploy"
    route = {
        "id": route_id,
        "path": ["deploy"],
        "aliases": [],
        "kind": "invocation",
        "confirmation": False,
        "actions": [
            {
                "id": "common-custom",
                "kind": "option",
                "flags": ["--custom-common"],
                "scope": "common",
                "required": False,
                "choices": None,
                "default": None,
                "exclusive_group": None,
                "exclusive_required": False,
            },
            {
                "id": "local-progress",
                "kind": "option",
                "flags": ["--progress"],
                "scope": "verb-local",
                "required": False,
                "choices": None,
                "default": None,
                "exclusive_group": None,
                "exclusive_required": False,
            },
            {
                "id": "default-false",
                "kind": "option",
                "flags": ["--toggle"],
                "scope": "custom",
                "required": False,
                "choices": None,
                "default": False,
                "exclusive_group": None,
                "exclusive_required": False,
            },
        ],
    }

    candidates = _generate_candidates([route], (), max_candidates=10)
    ids = {candidate["id"] for candidate in candidates}
    minimum = next(candidate for candidate in candidates if candidate["kind"] == "minimum")

    assert f"case:{route_id}/option-spelling/common-custom/--custom-common" in ids
    assert f"case:{route_id}/option-spelling/local-progress/--progress" in ids
    assert minimum["shape"]["defaulted_options"] == ["default-false"]
    assert minimum["members"] == ["default-false"]
    assert not any(candidate["kind"].startswith("exclusive-") for candidate in candidates)


@pytest.mark.parametrize("invalid_factory", ("module", ":factory", "module:", ""))
def test_surface_cli_factory_requires_both_module_and_target(invalid_factory):
    with pytest.raises(ValueError, match="python.module:callable"):
        _load_factory(invalid_factory)


@pytest.mark.parametrize(
    "argv",
    (
        ["--review", "review.toml", "sync"],
        ["--factory", "module:factory", "sync"],
        ["--factory", "module:factory", "--review", "review.toml", "--manifest", "manifest.json", "sync"],
        ["--factory", "module:factory", "--review", "review.toml", "--spec", "SPEC.md", "sync"],
    ),
)
def test_surface_cli_requires_factory_review_manifest_and_spec_before_work(argv):
    with pytest.raises(SystemExit) as error:
        surface_cli_main(argv)
    assert error.value.code == 2


def test_review_findings_accept_exact_minimum_and_empty_root_invocation():
    root_id = "route:entrypoint:audit-tool"
    minimum = _candidate(
        "case:root-minimum",
        root_id,
        "minimum",
        shape={"required_arguments": [], "required_argument_values": {}},
    )
    findings = _review_findings_for(_route(root_id, ()), minimum, [])
    assert findings == []

    route_id = "route:entrypoint:audit-tool/deploy"
    action = {
        "id": "argument:file",
        "kind": "argument",
        "name": "file",
        "nargs": None,
        "minimum_values": 1,
        "required": True,
        "parser_path": ["deploy"],
    }
    candidate = _candidate(
        "case:required-file",
        route_id,
        "minimum",
        members=("argument:file",),
        shape={
            "required_arguments": ["argument:file"],
            "required_argument_values": {"argument:file": 1},
        },
    )
    assert _review_findings_for(_route(route_id, ("deploy",), (action,)), candidate, ["deploy", "x.tar"]) == []


def test_review_findings_reports_alias_omission_even_when_path_is_missing():
    route_id = "route:entrypoint:audit-tool/deploy"
    route = _route(route_id, ("deploy",))
    candidate = _candidate(
        "case:deploy-alias",
        route_id,
        "route-alias",
        shape={"alias": "d"},
    )

    findings = _review_findings_for(route, candidate, [])

    assert any("omits its command path" in finding for finding in findings)
    assert any("omits its command alias" in finding for finding in findings)


def test_review_lexer_does_not_infer_pre_verb_placement_for_global_options():
    route_id = "route:entrypoint:audit-tool/deploy"
    option = {
        "id": "option:global",
        "kind": "option",
        "flags": ["--global"],
        "nargs": 0,
        "required": False,
        "parser_path": [],
    }
    candidate = _candidate(
        "case:global-option",
        route_id,
        "option-spelling",
        members=("option:global",),
        shape={"option_id": "option:global", "spelling": "--global"},
    )

    findings = _review_findings_for(
        _route(route_id, ("deploy",), (option,)),
        candidate,
        ["--global", "deploy"],
    )

    assert any("omits its reviewed option spelling" in finding for finding in findings)


def test_review_lexer_defaults_to_exact_option_spellings_without_parser_metadata():
    route_id = "route:entrypoint:audit-tool/deploy"
    option = {
        "id": "option:force",
        "kind": "option",
        "flags": ["--force"],
        "nargs": 0,
        "required": True,
        "parser_path": ["deploy"],
    }
    candidate = _candidate(
        "case:minimum-force",
        route_id,
        "minimum",
        members=("option:force",),
        shape={"required_arguments": [], "required_options": ["option:force"]},
    )

    findings = _review_findings_for(
        _route(route_id, ("deploy",), (option,)),
        candidate,
        ["deploy", "--fo"],
        entrypoint={},
    )

    assert any("omits required option option:force" in finding for finding in findings)


def test_review_lexer_only_accepts_long_option_abbreviations():
    route_id = "route:entrypoint:audit-tool/deploy"
    option = {
        "id": "option:force",
        "kind": "option",
        "flags": ["--force"],
        "nargs": 0,
        "required": True,
        "parser_path": ["deploy"],
    }
    route = _route(route_id, ("deploy",), (option,))
    route["parser_settings"] = [
        {"parser_path": ["deploy"], "allow_abbrev": True}
    ]
    candidate = _candidate(
        "case:minimum-force",
        route_id,
        "minimum",
        members=("option:force",),
        shape={"required_arguments": [], "required_options": ["option:force"]},
    )

    findings = _review_findings_for(route, candidate, ["deploy", "-f"])

    assert any("omits required option option:force" in finding for finding in findings)


@pytest.mark.parametrize(
    ("nargs", "tokens"),
    ((None, ["source", "extra"]), ("?", ["source", "extra"])),
)
def test_review_route_lexer_rejects_extra_parent_positional_before_nested_command(nargs, tokens):
    prefix_id = "route:entrypoint:audit-tool/deploy"
    leaf_id = f"{prefix_id}/run"
    prefix = {
        "id": prefix_id,
        "path": ["deploy"],
        "aliases": [],
        "kind": "route-prefix",
        "actions": [],
    }
    argument = {
        "id": "argument:source",
        "kind": "argument",
        "name": "source",
        "nargs": nargs,
        "minimum_values": 1 if nargs is None else 0,
        "required": nargs is None,
        "parser_path": ["deploy"],
        "before_nested_subcommand": True,
    }
    leaf = {
        "id": leaf_id,
        "path": ["deploy", "run"],
        "aliases": [],
        "kind": "invocation",
        "actions": [argument],
    }
    candidate = _candidate("case:extra-parent", leaf_id, "other")
    invocation = ["deploy", *tokens, "run"]
    findings = _review_findings_for(
        leaf,
        candidate,
        invocation,
        all_routes=[prefix, leaf],
    )

    assert any("omits its command path" in finding for finding in findings)


def test_review_findings_do_not_treat_options_after_double_dash_as_active():
    route_id = "route:entrypoint:audit-tool/deploy"
    option = {
        "id": "option:flag",
        "kind": "option",
        "flags": ["--flag"],
        "nargs": None,
        "minimum_values": 1,
        "required": False,
        "parser_path": ["deploy"],
    }
    candidate = _candidate(
        "case:flag-spelling",
        route_id,
        "option-spelling",
        members=("option:flag",),
        shape={"spelling": "--flag"},
    )

    findings = _review_findings_for(
        _route(route_id, ("deploy",), (option,)),
        candidate,
        ["deploy", "--", "--flag", "value"],
    )

    assert any("omits its reviewed option spelling" in finding for finding in findings)


def test_review_findings_resolve_argument_id_from_members_when_shape_omits_it():
    route_id = "route:entrypoint:audit-tool/build"
    route = _route(
        route_id,
        ("build",),
        (
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
                "parser_path": ["build"],
            },
        ),
    )
    candidate = _candidate(
        "case:format-choice-fallback",
        route_id,
        "argument-choice",
        members=("argument:source",),
        shape={"argument_id": "argument:format", "choice": "wheel"},
    )

    assert _review_findings_for(route, candidate, ["build", "src.tar", "wheel"]) == []


def test_review_findings_reports_incomplete_when_surface_completeness_is_omitted():
    route_id = "route:entrypoint:audit-tool"
    candidate = _candidate("case:root", route_id, "minimum")
    findings = _review_findings_for(
        _route(route_id, ()),
        candidate,
        [],
        complete=None,
        incomplete=("opaque parser setting",),
    )
    assert any("incomplete parser syntax" in finding for finding in findings)


def test_case_marker_error_identifies_empty_marker_as_malformed():
    catalog = ReviewCatalog(
        "audit-tool",
        2,
        (),
        (_review_case("case:one"),),
    )

    class Marker:
        args = ("",)

    class Item:
        nodeid = "tests/test_cli.py::test_invocation"

        @staticmethod
        def iter_markers(*, name):
            return [Marker()] if name == "cli_case" else []

    with pytest.raises(AssertionError, match="malformed cli_case marker"):
        assert_cli_case_tests([Item()], catalog)
