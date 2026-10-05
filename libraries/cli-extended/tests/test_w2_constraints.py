"""W2: declarative option constraints (CLI-EXT-02)."""

from __future__ import annotations

import argparse
import copy
import io
import json

import pytest

from cli_extended import (
    ArgumentSpec,
    CliIdentity,
    CliRegistry,
    Conflicts,
    OptionSpec,
    Requires,
    RequiresChoice,
    ReviewCase,
    ReviewCatalog,
    SurfaceError,
    VerbSpec,
    export_cli_surface,
    render_cli_surface_json,
    render_cli_surface_markdown,
)
from cli_extended import constraints as constraints_module
from cli_extended import contract
from cli_extended.review import _review_findings
from cli_extended.surface import _generate_candidates

IDENT = CliIdentity("TOOL", "1.0.0", "Tool CLI", "tool")
SYNC = "route:entrypoint:tool/sync"
STORE_TRUE = {"action": "store_true"}

R_REQ = "dry-run needs a work flag"
R_CONF = "refresh has no JSON form"
R_CHOICE = "turbo needs fast mode"

DRY_REQ = Requires("--dry-run", ("--update", "--write", "--refresh"), R_REQ)
REFRESH_JSON = Conflicts(("--refresh", "--json"), R_CONF)
TURBO_FAST = RequiresChoice("--turbo", "--mode", ("fast",), R_CHOICE)

MSG_REQ = f"--dry-run requires --update or --write or --refresh: {R_REQ}"
MSG_CONF = f"--refresh and --json cannot be used together: {R_CONF}"
MSG_CHOICE = f"--turbo requires --mode fast: {R_CHOICE}"


def _flag(name, **kwargs):
    return OptionSpec((name,), f"{name} option", parser_kwargs=kwargs)


def _sync_verb(seen=None, *, reason=R_REQ, **overrides):
    def handler(args, runtime):
        if seen is not None:
            seen.append(args)
        return 0

    fields = dict(
        name="sync",
        description="Sync things.",
        mutating=True,
        dry_run=True,
        handler=handler,
        options=(
            _flag("--update", action="store_true"),
            _flag("--write", action="store_true"),
            _flag("--refresh", action="store_true"),
            _flag("--turbo", action="store_true"),
            _flag("--mode", choices=["fast", "slow"]),
            _flag("--tags", action="append", default=[]),
            _flag("--also", action="store_true"),
        ),
        constraints=(
            Requires("--dry-run", ("--update", "--write", "--refresh"), reason),
            REFRESH_JSON,
            TURBO_FAST,
            Requires("--tags", ("--also",), "tags need also"),
        ),
    )
    fields.update(overrides)
    return VerbSpec(**fields)


def _cli(verbs, **registry_kwargs):
    registry = CliRegistry(
        IDENT, prog="tool", description="Tool.", **registry_kwargs
    )
    for verb in verbs:
        registry.register(verb)
    return registry.build()


def _run(cli, argv):
    out, err = io.StringIO(), io.StringIO()
    code = cli.run(argv=argv, stdout=out, stderr=err, stdin=io.StringIO())
    return code, out.getvalue(), err.getvalue()


def _show():
    return VerbSpec("show", description="Show.", handler=lambda a, r: 0)


# ------------------------------------------------------------------ O1 / O2


@pytest.mark.parametrize(
    ("argv", "message"),
    (
        (["sync", "--dry-run"], MSG_REQ),
        (["--dry-run", "sync"], MSG_REQ),
        (["sync", "--refresh", "--json"], MSG_CONF),
        (["--json", "sync", "--refresh"], MSG_CONF),
        (["sync", "--json", "--refresh"], MSG_CONF),
        (["sync", "--turbo"], MSG_CHOICE),
        (["sync", "--turbo", "--mode", "slow"], MSG_CHOICE),
        (["sync", "--tags", "x"], "--tags requires --also: tags need also"),
        # Declaration order: the Requires comes before the RequiresChoice.
        (["sync", "--dry-run", "--turbo"], MSG_REQ),
    ),
)
def test_violation_exits_2_with_exact_message_and_verb_help(argv, message):
    seen: list = []
    cli = _cli([_sync_verb(seen), _show()])
    code, out, err = _run(cli, argv)
    assert code == 2
    assert seen == []
    assert out == ""
    first, _, rest = err.partition("\n")
    assert first == f"[ERROR] {message}"
    assert "CONSTRAINTS\n  " + MSG_REQ in rest
    assert "usage: tool sync" in rest


@pytest.mark.parametrize(
    "argv",
    (
        ["sync"],
        ["sync", "--dry-run", "--update"],
        ["--dry-run", "sync", "--write"],
        ["sync", "--refresh", "--dry-run"],
        ["--dry-run", "sync", "--refresh"],
        ["sync", "--refresh"],
        ["sync", "--json"],
        ["--json", "sync", "--update"],
        ["sync", "--turbo", "--mode", "fast"],
        ["sync", "--mode", "slow"],
        ["sync", "--tags", "x", "--also"],
    ),
)
def test_satisfied_invocation_runs_the_handler(argv):
    seen: list = []
    cli = _cli([_sync_verb(seen), _show()])
    assert _run(cli, argv)[0] == 0
    assert len(seen) == 1


def test_empty_default_list_is_not_presence():
    seen: list = []
    cli = _cli([_sync_verb(seen), _show()])
    assert _run(cli, ["sync"])[0] == 0
    assert seen[0].tags == []


def test_conflicts_message_lists_present_options_in_declared_order():
    verb = VerbSpec(
        "multi",
        description="Multi.",
        handler=lambda a, r: 0,
        options=(
            _flag("--aa", action="store_true"),
            _flag("--bb", action="store_true"),
            _flag("--cc", action="store_true"),
        ),
        constraints=(Conflicts(("--aa", "--bb", "--cc"), "pick one"),),
    )
    cli = _cli([verb])
    code, _, err = _run(cli, ["multi", "--cc", "--aa"])
    assert code == 2
    assert err.startswith("[ERROR] --aa and --cc cannot be used together: pick one\n")
    assert _run(cli, ["multi", "--bb"])[0] == 0
    # The help line names every option of the rule.
    assert (
        "  --aa and --bb and --cc cannot be used together: pick one"
        in cli.command_parsers["multi"].format_help()
    )


def test_library_controls_in_constraints_json_and_dry_run():
    seen: list = []
    cli = _cli([_sync_verb(seen), _show()])
    assert _run(cli, ["sync", "--refresh", "--json"])[0] == 2
    assert _run(cli, ["sync", "--dry-run"])[0] == 2
    assert _run(cli, ["sync", "--dry-run", "--json", "--update"])[0] == 0
    assert seen[-1].dry_run is True and seen[-1].json is True


def test_global_option_constraints_before_and_after_the_verb():
    seen: list = []

    def handler(args, runtime):
        seen.append(args)
        return 0

    verb = VerbSpec(
        "ship",
        description="Ship.",
        handler=handler,
        options=(_flag("--release", action="store_true"),),
        constraints=(RequiresChoice("--release", "--env", ("prod",), "prod only"),),
    )
    env = OptionSpec(
        ("--env",), "environment", group="GLOBAL OPTIONS",
        parser_kwargs={"choices": ["dev", "prod"]},
    )
    cli = _cli([verb, _show()], global_options=(env,))
    refusal = "--release requires --env prod: prod only"
    for argv in (
        ["ship", "--release"],
        ["--env", "dev", "ship", "--release"],
        ["ship", "--release", "--env", "dev"],
    ):
        code, _, err = _run(cli, argv)
        assert code == 2 and err.startswith(f"[ERROR] {refusal}\n"), argv
    for argv in (
        ["--env", "prod", "ship", "--release"],
        ["ship", "--release", "--env", "prod"],
        ["--env", "dev", "ship"],
    ):
        assert _run(cli, argv)[0] == 0, argv
    assert len(seen) == 3


def test_global_default_lookup_uses_the_root_parser_and_suppressed_defaults():
    verb = VerbSpec(
        "ship",
        description="Ship.",
        handler=lambda a, r: 0,
        options=(_flag("--release", action="store_true"),),
        constraints=(Requires("--tz", ("--release",), "tz needs release"),),
    )
    tz = OptionSpec(
        ("--tz",), "zone", group="GLOBAL OPTIONS",
        parser_kwargs={"default": argparse.SUPPRESS},
    )
    cli = _cli([verb, _show()], global_options=(tz,))
    assert _run(cli, ["ship"])[0] == 0
    assert _run(cli, ["ship", "--tz", "x"])[0] == 2
    assert _run(cli, ["--tz", "x", "ship", "--release"])[0] == 0
    region = OptionSpec(
        ("--region",), "region", group="GLOBAL OPTIONS", parser_kwargs={"default": "eu"}
    )
    bad = VerbSpec(
        "ship",
        description="Ship.",
        handler=lambda a, r: 0,
        constraints=(Requires("--region", ("--json",), "r"),),
    )
    with pytest.raises(ValueError) as info:
        _cli([bad], global_options=(region,))
    assert str(info.value) == (
        "verb 'ship' constraint references --region, whose default 'eu' is not "
        "None, False or an empty list/tuple"
    )


def test_single_command_cli_enforces_and_shows_constraints_in_help():
    seen: list = []
    cli = _cli([_sync_verb(seen)], single_command=True)
    code, _, err = _run(cli, ["--dry-run"])
    assert code == 2
    assert err.startswith(f"[ERROR] {MSG_REQ}\n")
    assert "CONSTRAINTS\n  " + MSG_REQ in err
    assert _run(cli, ["--dry-run", "--update"])[0] == 0
    assert len(seen) == 1


def test_nested_verb_constraints_apply_before_the_nested_route():
    def configure(parser):
        parser.add_argument("--alpha", action="store_true")
        parser.add_argument("--beta", action="store_true")
        nested = parser.add_subparsers(dest="leaf_name")
        nested.add_parser("leaf", help="leaf")

    verb = VerbSpec(
        "tree",
        description="Tree.",
        configure=configure,
        handler=lambda a, r: 0,
        constraints=(Conflicts(("--alpha", "--beta"), "one at a time"),),
    )
    cli = _cli([verb])
    code, _, err = _run(cli, ["tree", "--alpha", "--beta", "leaf"])
    assert code == 2
    assert err.startswith("[ERROR] --alpha and --beta cannot be used together")
    assert _run(cli, ["tree", "--alpha", "leaf"])[0] == 0


def test_help_command_and_markdown_render_the_constraint_lines():
    cli = _cli([_sync_verb(), _show()])
    code, out, _ = _run(cli, ["help", "sync"])
    assert code == 0
    assert (
        "CONSTRAINTS\n"
        f"  {MSG_REQ}\n"
        f"  {MSG_CONF}\n"
        f"  {MSG_CHOICE}\n"
        "  --tags requires --also: tags need also\n"
    ) in out
    assert "CONSTRAINTS" not in cli.command_parsers["show"].format_help()
    assert out.index("CONSTRAINTS") > out.index("--update")
    markdown = cli.catalog.render_markdown()
    assert (
        "#### Constraints\n\n"
        f"- {MSG_REQ}\n- {MSG_CONF}\n- {MSG_CHOICE}\n"
        "- --tags requires --also: tags need also\n"
    ) in markdown
    assert markdown.count("#### Constraints") == 1


# ----------------------------------------------------------------------- O3


@pytest.mark.parametrize(
    ("factory", "fragment"),
    (
        (lambda: Requires("update", ("--a",), "r"), "option must be a long option flag"),
        (lambda: Requires("-u", ("--a",), "r"), "option must be a long option flag"),
        (lambda: Requires("--", ("--a",), "r"), "option must be a long option flag"),
        (lambda: Requires(None, ("--a",), "r"), "option must be a long option flag"),
        (lambda: Requires("--u", ["--a"], "r"), "any_of must be a tuple"),
        (lambda: Requires("--u", (), "r"), "any_of needs at least 1"),
        (lambda: Requires("--u", ("a",), "r"), "any_of must be a long option flag"),
        (lambda: Requires("--u", ("--a", "--a"), "r"), "any_of must not contain duplicate"),
        (lambda: Requires("--u", ("--u",), "r"), "option must not appear in its own any_of"),
        (lambda: Requires("--u", ("--a",), ""), "reason must be a non-empty"),
        (lambda: Requires("--u", ("--a",), "  "), "reason must be a non-empty"),
        (lambda: Requires("--u", ("--a",), "a\nb"), "reason must be a non-empty"),
        (lambda: Requires("--u", ("--a",), "a\rb"), "reason must be a non-empty"),
        (lambda: Requires("--u", ("--a",), None), "reason must be a non-empty"),
        (lambda: Conflicts(("--a",), "r"), "options needs at least 2"),
        (lambda: Conflicts(("--a", "--a"), "r"), "options must not contain duplicate"),
        (lambda: Conflicts(("--a", "-b"), "r"), "options must be a long option flag"),
        (lambda: Conflicts(["--a", "--b"], "r"), "options must be a tuple"),
        (lambda: Conflicts(("--a", "--b"), ""), "reason must be a non-empty"),
        (lambda: RequiresChoice("x", "--t", ("v",), "r"), "option must be a long"),
        (lambda: RequiresChoice("--o", "t", ("v",), "r"), "target must be a long"),
        (lambda: RequiresChoice("--o", "--o", ("v",), "r"), "option and target must differ"),
        (lambda: RequiresChoice("--o", "--t", (), "r"), "values must be a non-empty tuple"),
        (lambda: RequiresChoice("--o", "--t", ["v"], "r"), "values must be a non-empty tuple"),
        (lambda: RequiresChoice("--o", "--t", (1,), "r"), "values must be a non-empty tuple"),
        (lambda: RequiresChoice("--o", "--t", ("v", "v"), "r"), "values must not contain dup"),
        (lambda: RequiresChoice("--o", "--t", ("v",), "r\n"), "reason must be a non-empty"),
    ),
)
def test_constraint_dataclass_validation(factory, fragment):
    with pytest.raises(ValueError) as info:
        factory()
    assert fragment in str(info.value)


def test_valid_constraints_expose_flags_and_records():
    assert DRY_REQ.flags == ("--dry-run", "--update", "--write", "--refresh")
    assert REFRESH_JSON.flags == ("--refresh", "--json")
    assert TURBO_FAST.flags == ("--turbo", "--mode")
    assert DRY_REQ.to_record() == {
        "kind": "requires",
        "option": "--dry-run",
        "any_of": ["--update", "--write", "--refresh"],
        "reason": R_REQ,
    }
    assert REFRESH_JSON.to_record() == {
        "kind": "conflicts",
        "options": ["--refresh", "--json"],
        "reason": R_CONF,
    }
    assert TURBO_FAST.to_record() == {
        "kind": "requires-choice",
        "option": "--turbo",
        "target": "--mode",
        "values": ["fast"],
        "reason": R_CHOICE,
    }
    assert constraints_module.rule_lines((DRY_REQ, REFRESH_JSON, TURBO_FAST)) == (
        MSG_REQ, MSG_CONF, MSG_CHOICE,
    )
    assert constraints_module.help_epilog(()) is None
    two = RequiresChoice("--o", "--t", ("a", "b"), "why")
    assert constraints_module.rule_lines((two,)) == ("--o requires --t a|b: why",)


def test_verbspec_constraints_must_be_a_tuple_of_constraints():
    base = dict(name="v", description="d", handler=lambda a, r: 0)
    assert VerbSpec(**base).constraints == ()
    with pytest.raises(TypeError):
        VerbSpec(**base, constraints=[DRY_REQ])
    with pytest.raises(TypeError):
        VerbSpec(**base, constraints=(DRY_REQ, "--x"))
    with pytest.raises(TypeError):
        VerbSpec(**base, constraints=DRY_REQ)
    child = _cli([VerbSpec("c", description="c", handler=lambda a, r: 0)])
    with pytest.raises(ValueError, match="delegates to another CLI"):
        VerbSpec("d", description="d", delegate=child, constraints=(DRY_REQ,))


def _build_error(verb, **kwargs):
    with pytest.raises(ValueError) as info:
        _cli([verb], **kwargs)
    return str(info.value)


def _plain(**kwargs):
    fields = dict(name="v", description="d", handler=lambda a, r: 0)
    fields.update(kwargs)
    return VerbSpec(**fields)


def test_build_refuses_unknown_flags_naming_verb_and_flag():
    message = "verb 'v' constraint references {}, which the verb does not accept"
    assert _build_error(
        _plain(constraints=(Requires("--nope", ("--json",), "r"),))
    ) == message.format("--nope")
    assert _build_error(
        _plain(constraints=(Requires("--json", ("--nope",), "r"),))
    ) == message.format("--nope")
    assert _build_error(
        _plain(constraints=(Conflicts(("--json", "--nope"), "r"),))
    ) == message.format("--nope")
    # --dry-run is a library control only on verbs that declare dry_run.
    assert _build_error(
        _plain(constraints=(Requires("--dry-run", ("--json",), "r"),))
    ) == message.format("--dry-run")
    # A control the verb disabled is not accepted either.
    assert _build_error(
        _plain(include_json=False, constraints=(Requires("--json", ("--yes",), "r"),))
    ) == message.format("--json")


@pytest.mark.parametrize(
    ("kwargs", "shown"),
    (
        ({"default": "x"}, "'x'"),
        ({"default": 0, "type": int}, "0"),
        ({"default": True, "action": "store_false"}, "True"),
        ({"default": [1], "action": "append"}, "[1]"),
        ({"default": "", "type": str}, "''"),
    ),
)
def test_build_refuses_non_empty_defaults(kwargs, shown):
    verb = _plain(
        options=(_flag("--level", **kwargs),),
        constraints=(Requires("--level", ("--json",), "r"),),
    )
    assert _build_error(verb) == (
        f"verb 'v' constraint references --level, whose default {shown} is not "
        "None, False or an empty list/tuple"
    )
    as_target = _plain(
        options=(_flag("--level", **kwargs),),
        constraints=(Requires("--json", ("--level",), "r"),),
    )
    assert "--level, whose default" in _build_error(as_target)


def test_build_accepts_none_false_and_empty_sequence_defaults():
    verb = _plain(
        options=(
            _flag("--a", action="store_true"),
            _flag("--b"),
            _flag("--c", action="append", default=[]),
            _flag("--d", nargs="*", default=()),
            _flag("--e", action="store_true", default=False),
        ),
        constraints=(Conflicts(("--a", "--b", "--c", "--d", "--e"), "r"),),
    )
    cli = _cli([verb])
    assert _run(cli, ["v", "--c", "x", "--d", "y"])[0] == 2
    assert _run(cli, ["v", "--d", "y"])[0] == 0
    assert _run(cli, ["v", "--e", "--b", "q"])[0] == 2


def test_build_requires_choices_on_the_target_and_values_within_them():
    options = (
        _flag("--turbo", action="store_true"),
        _flag("--mode", choices=["fast", "slow"]),
        _flag("--free"),
    )
    assert _build_error(
        _plain(
            options=options,
            constraints=(RequiresChoice("--turbo", "--free", ("fast",), "r"),),
        )
    ) == "verb 'v' constraint target --free declares no choices"
    assert _build_error(
        _plain(
            options=options,
            constraints=(RequiresChoice("--turbo", "--mode", ("fast", "zzz"), "r"),),
        )
    ) == "verb 'v' constraint value 'zzz' is not a choice of --mode"
    ok = _plain(
        options=options,
        constraints=(RequiresChoice("--turbo", "--mode", ("slow", "fast"), "r"),),
    )
    cli = _cli([ok])
    assert _run(cli, ["v", "--turbo", "--mode", "fast"])[0] == 0
    assert _run(cli, ["v", "--turbo", "--mode", "slow"])[0] == 0
    code, _, err = _run(cli, ["v", "--turbo"])
    assert code == 2 and err.startswith("[ERROR] --turbo requires --mode slow|fast: r\n")


def test_unconstrained_verbs_carry_no_resolved_constraints():
    cli = _cli([_show()])
    for parser in cli.command_parsers.values():
        assert parser._cli_constraints == ()
    assert cli.parser.format_help()


# ----------------------------------------------------------------------- O4


def _surface(reason=R_REQ, **kwargs):
    return export_cli_surface(_cli([_sync_verb(reason=reason), _show()], **kwargs))


def _by_id(surface, candidate_id):
    return next(item for item in surface["candidates"] if item["id"] == candidate_id)


def test_route_records_constraints_with_canonical_library_flags():
    routes = {route["id"]: route for route in _surface()["routes"]}
    assert routes[SYNC]["constraints"] == [
        DRY_REQ.to_record(),
        REFRESH_JSON.to_record(),
        TURBO_FAST.to_record(),
        {
            "kind": "requires",
            "option": "--tags",
            "any_of": ["--also"],
            "reason": "tags need also",
        },
    ]
    assert routes["route:entrypoint:tool/show"]["constraints"] == []


def test_three_candidate_kinds_with_literal_ids_members_and_shape():
    surface = _surface()
    ids = [
        item["id"] for item in surface["candidates"] if "/constraint-" in item["id"]
    ]
    assert sorted(ids) == sorted(
        (
            f"case:{SYNC}/constraint-requires/1",
            f"case:{SYNC}/constraint-conflict/2",
            f"case:{SYNC}/constraint-choice/3",
            f"case:{SYNC}/constraint-requires/4",
        )
    )
    requires = _by_id(surface, f"case:{SYNC}/constraint-requires/1")
    assert requires["kind"] == "constraint-requires"
    assert requires["route_id"] == SYNC
    assert requires["members"] == [
        f"option:{SYNC}/--dry-run",
        f"option:{SYNC}/--update",
        f"option:{SYNC}/--write",
        f"option:{SYNC}/--refresh",
    ]
    assert requires["shape"] == DRY_REQ.to_record()
    conflict = _by_id(surface, f"case:{SYNC}/constraint-conflict/2")
    assert conflict["kind"] == "constraint-conflict"
    assert conflict["members"] == [f"option:{SYNC}/--refresh", f"option:{SYNC}/--json"]
    assert conflict["shape"] == REFRESH_JSON.to_record()
    choice = _by_id(surface, f"case:{SYNC}/constraint-choice/3")
    assert choice["kind"] == "constraint-choice"
    assert choice["members"] == [f"option:{SYNC}/--turbo", f"option:{SYNC}/--mode"]
    assert choice["shape"] == TURBO_FAST.to_record()
    assert surface["schema_version"] == 7


def test_nested_leaf_route_inherits_constraints_but_prefix_has_no_candidate():
    def configure(parser):
        parser.add_argument("--alpha", action="store_true")
        parser.add_argument("--beta", action="store_true")
        nested = parser.add_subparsers(dest="leaf_name", required=True)
        nested.add_parser("leaf", help="leaf")

    verb = VerbSpec(
        "tree",
        description="Tree.",
        configure=configure,
        handler=lambda a, r: 0,
        constraints=(Conflicts(("--alpha", "--beta"), "one at a time"),),
    )
    surface = export_cli_surface(_cli([verb]))
    routes = {route["id"]: route for route in surface["routes"]}
    tree = "route:entrypoint:tool/tree"
    assert routes[tree]["kind"] == "route-prefix"
    assert routes[tree]["constraints"] == routes[f"{tree}/leaf"]["constraints"] != []
    constraint_ids = [
        item["id"] for item in surface["candidates"] if "/constraint-" in item["id"]
    ]
    assert constraint_ids == [f"case:{tree}/leaf/constraint-conflict/1"]
    (candidate,) = [
        item for item in surface["candidates"] if item["id"] == constraint_ids[0]
    ]
    assert candidate["members"] == [
        f"option:{tree}/leaf/parser:tree/--alpha",
        f"option:{tree}/leaf/parser:tree/--beta",
    ]


def test_changing_a_reason_changes_only_that_routes_signatures():
    before = {item["id"]: item["signature"] for item in _surface()["candidates"]}
    after = {
        item["id"]: item["signature"]
        for item in _surface(reason="a different reason")["candidates"]
    }
    assert before.keys() == after.keys()
    changed = {key for key in before if before[key] != after[key]}
    sync_ids = {key for key in before if f"{SYNC}/" in key}
    assert changed == sync_ids
    assert changed
    assert all("tool/show" not in key for key in changed)


def test_unconstrained_route_signatures_do_not_carry_a_constraints_key():
    plain = export_cli_surface(_cli([_show()]))
    (route,) = plain["routes"]
    assert route["constraints"] == []
    # An empty list is omitted from the signed context, so a consumer without
    # constraints keeps the signatures it had before this field existed.
    constrained_without = export_cli_surface(
        _cli([VerbSpec("show", description="Show.", handler=lambda a, r: 0)])
    )
    assert {c["id"]: c["signature"] for c in plain["candidates"]} == {
        c["id"]: c["signature"] for c in constrained_without["candidates"]
    }


def test_library_syntax_change_does_not_move_constraint_signatures(monkeypatch):
    surface = _surface()
    original = contract.common_control_table

    def patched():
        table = original()
        for entry in table.values():
            entry.update(nargs=3, takes_value=True, choices=["x", "y"])
            entry["flags"] = [*entry["flags"], "--extra"]
        return table

    monkeypatch.setattr(contract, "common_control_table", patched)
    after = _surface()
    assert {c["id"]: c["signature"] for c in after["candidates"]} == {
        c["id"]: c["signature"] for c in surface["candidates"]
    }
    assert render_cli_surface_json(after) == render_cli_surface_json(surface)
    record = next(r for r in after["routes"] if r["id"] == SYNC)["constraints"][1]
    assert record["options"] == ["--refresh", "--json"]
    members = _by_id(after, f"case:{SYNC}/constraint-conflict/2")["members"]
    assert members == [f"option:{SYNC}/--refresh", f"option:{SYNC}/--json"]


def test_unresolvable_constraint_flag_in_a_surface_is_a_surface_error():
    routes = copy.deepcopy(_surface()["routes"])
    sync = next(route for route in routes if route["id"] == SYNC)
    sync["constraints"] = [
        {"kind": "requires", "option": "--update", "any_of": ["--ghost"], "reason": "r"}
    ]
    with pytest.raises(SurfaceError) as info:
        _generate_candidates(routes, [], max_candidates=512)
    assert str(info.value) == (
        f"route '{SYNC}' constraint references unknown option --ghost"
    )


def _catalog_case(candidate, invocation):
    return ReviewCase(
        case_id=candidate["id"],
        state="active",
        decision="refuse",
        reviewed_signature=candidate["signature"],
        rationale="reviewed",
        invocation=tuple(invocation),
        invocation_declared=True,
        expected_exit_status=2,
        expected_stdout_contains="",
        expected_stderr_contains="",
        effects=(),
        effects_declared=True,
        test_ids=("tests/test_cli.py::test_it",),
        retirement_reason="",
    )


def _findings(surface, candidate, invocation):
    catalog = ReviewCatalog("tool", 512, (), (_catalog_case(candidate, invocation),))
    return [
        item
        for item in _review_findings(surface, catalog)
        if candidate["id"] in item
    ]


def test_checker_accepts_constraint_violating_invocations_and_never_evaluates():
    surface = _surface()
    requires = _by_id(surface, f"case:{SYNC}/constraint-requires/1")
    conflict = _by_id(surface, f"case:{SYNC}/constraint-conflict/2")
    choice = _by_id(surface, f"case:{SYNC}/constraint-choice/3")
    assert _findings(surface, requires, ["sync", "--dry-run"]) == []
    assert _findings(surface, conflict, ["sync", "--refresh", "--json"]) == []
    assert _findings(surface, choice, ["sync", "--turbo"]) == []
    assert _findings(surface, choice, ["sync", "--turbo", "--mode", "slow"]) == []
    # A case that satisfies the rule is just as valid: nothing is evaluated.
    assert _findings(surface, requires, ["sync", "--dry-run", "--update"]) == []
    assert _findings(surface, conflict, ["sync"]) == []
    # Syntax of the options that are present is still checked.
    bad = _findings(surface, choice, ["sync", "--turbo", "--mode", "bad"])
    assert any("supplies an invalid value for option" in item for item in bad)


def test_markdown_lists_constraints_per_route_only_when_declared():
    catalog = ReviewCatalog("tool", 512, (), ())
    markdown = render_cli_surface_markdown(_surface(), catalog)
    assert (
        f"\n### Constraints\n\n- `{SYNC}`:\n"
        f"  - {MSG_REQ}\n"
        f"  - {MSG_CONF}\n"
        f"  - {MSG_CHOICE}\n"
        "  - --tags requires --also: tags need also\n"
    ) in markdown
    assert "tool/show`:\n  -" not in markdown
    plain = render_cli_surface_markdown(export_cli_surface(_cli([_show()])), catalog)
    assert "### Constraints" not in plain


def test_surface_json_is_deterministic_with_constraints():
    first = render_cli_surface_json(_surface())
    assert first == render_cli_surface_json(_surface())
    assert json.loads(first)["routes"][0]["constraints"] is not None


def test_positional_argument_cannot_be_referenced_as_a_flag():
    verb = _plain(
        arguments=(ArgumentSpec("name", "a name"),),
        constraints=(Requires("--name", ("--json",), "r"),),
    )
    assert "constraint references --name" in _build_error(verb)
