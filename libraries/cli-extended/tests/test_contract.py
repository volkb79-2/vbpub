"""The library contract: library-owned controls are named, never described."""

from __future__ import annotations

import inspect
import json
import re
import types
from dataclasses import replace

import pytest

from cli_extended import (
    CONTRACT_VERSION,
    CliIdentity,
    CliRegistry,
    ExtendedArgumentParser,
    OptionSpec,
    ReviewCase,
    ReviewCatalog,
    SurfaceSpecError,
    VerbSpec,
    add_common_options,
    check_cli_surface,
    export_cli_surface,
    render_cli_surface_json,
    render_cli_surface_markdown,
    sync_cli_surface,
)
from cli_extended import contract, parser as parser_module
from cli_extended.review import (
    SURFACE_END_MARKER,
    SURFACE_START_MARKER,
    _committed_contract_version,
    _review_findings,
)
from cli_extended.surface import (
    _common_parser_path,
    _route_common_actions,
    _routes_by_path,
)

IDENTITY = CliIdentity("CONTRACT", "1.0", "Contract Tool", command="contract-tool")
DEPLOY = "route:entrypoint:contract-tool/deploy"
RAW = "route:entrypoint:contract-tool/raw"
PLAIN = "route:entrypoint:contract-tool/plain"
TREE = "route:entrypoint:contract-tool/tree"
LEAF = f"{TREE}/leaf"
ALL_V1_FLAGS = {
    "--help", "--version", "--log-level", "--quiet", "--debug", "--debug-raw",
    "--color", "--no-color", "--json", "--progress", "--yes",
}


def _configure_tree(parser):
    nested = parser.add_subparsers(dest="leaf_name")
    nested.add_parser("leaf", help="leaf")


def _build(*, target_help="target host", target_metavar=None, extra_verbs=True):
    registry = CliRegistry(IDENTITY, prog="contract-tool", description="Contract demo.")
    registry.register(
        VerbSpec(
            "deploy",
            description="deploy",
            mutating=True,
            handler=lambda *_: 0,
            options=(OptionSpec(("--target",), target_help, metavar=target_metavar),),
        )
    )
    if extra_verbs:
        registry.register(
            VerbSpec(
                "raw",
                description="raw",
                include_json=False,
                handler=lambda *_: 0,
                options=(
                    OptionSpec(("--json",), "consumer json", parser_kwargs={"action": "store_true"}),
                ),
            )
        )
        registry.register(
            VerbSpec("plain", description="plain", include_json=False, handler=lambda *_: 0)
        )
        registry.register(
            VerbSpec("tree", description="tree", configure=_configure_tree, handler=lambda *_: 0)
        )
    return registry.build()


def _routes(surface):
    return {route["id"]: route for route in surface["routes"]}


def _catalog(*cases):
    return ReviewCatalog("contract-tool", 512, (), tuple(cases))


def _case(candidate, invocation):
    return ReviewCase(
        case_id=candidate["id"],
        state="active",
        decision="accept",
        reviewed_signature=candidate["signature"],
        rationale="reviewed",
        invocation=tuple(invocation),
        invocation_declared=True,
        expected_exit_status=0,
        expected_stdout_contains="",
        expected_stderr_contains="",
        effects=(),
        effects_declared=True,
        test_ids=("tests/test_cli.py::test_it",),
        retirement_reason="",
    )


def _invocation_findings(surface, candidate, invocation):
    findings = _review_findings(surface, _catalog(_case(candidate, invocation)))
    return [item for item in findings if candidate["id"] in item]


def _candidate(surface, route_id, kind):
    return next(
        item for item in surface["candidates"]
        if item["route_id"] == route_id and item["kind"] == kind
    )


# ---------------------------------------------------------------- contract.py


def test_contract_constants_and_export():
    assert contract.LIBRARY_CONTRACT_NAME == "cli-extended"
    assert contract.CONTRACT_VERSION == 1
    assert CONTRACT_VERSION == 1
    assert contract.CONSUMER_REVIEWED_COMMON_FLAGS == frozenset(
        {"--json", "--yes", "--debug-raw", "--dry-run"}
    )


def test_common_control_table_keys_are_derived_from_every_include_flag():
    """O7: the table is derived from add_common_options, never restated."""

    include_flags = [
        name
        for name in inspect.signature(add_common_options).parameters
        if name.startswith("include_")
    ]
    assert include_flags
    parser = ExtendedArgumentParser(prog="scratch", identity=IDENTITY)
    add_common_options(parser, IDENTITY, **{name: True for name in include_flags})
    built = {
        contract.canonical_flag(action.option_strings)
        for action in parser._actions
        if action.option_strings
    }
    table = contract.common_control_table()
    assert set(table) == built
    assert ALL_V1_FLAGS <= set(table)
    # Switching an include flag off removes exactly its control.
    partial = ExtendedArgumentParser(prog="scratch", identity=IDENTITY)
    add_common_options(
        partial,
        IDENTITY,
        **{name: name != "include_json" for name in include_flags},
    )
    assert {
        contract.canonical_flag(action.option_strings)
        for action in partial._actions
        if action.option_strings
    } == (
        set(table) - {"--json"}
    )


def test_common_control_table_entries_describe_arity_and_placement():
    table = contract.common_control_table()
    assert table["--log-level"] == {
        "flags": ["--log-level"],
        "nargs": None,
        "takes_value": True,
        "choices": ["error", "warn", "info", "debug"],
        "before_verb": True,
        "after_verb": True,
    }
    assert table["--quiet"]["nargs"] == 0
    assert table["--quiet"]["takes_value"] is False
    assert table["--quiet"]["choices"] is None
    assert table["--debug"]["flags"] == ["--debug", "--verbose"]
    assert table["--no-color"]["flags"] == ["--no-color"]
    assert table["--progress"]["choices"] == ["auto", "tty", "plain", "quiet", "rawjson"]
    for entry in table.values():
        assert entry["before_verb"] is True
        assert entry["after_verb"] is True
        assert entry["takes_value"] is (entry["nargs"] != 0)


def test_common_control_table_returns_independent_copies():
    first = contract.common_control_table()
    first["--json"]["flags"].append("--mutated")
    first.pop("--yes")
    second = contract.common_control_table()
    assert second["--json"]["flags"] == ["--json"]
    assert "--yes" in second


def test_library_actions_are_marked_and_consumer_actions_are_not():
    parser = ExtendedArgumentParser(prog="scratch", identity=IDENTITY)
    consumer = parser.add_argument("--consumer")
    before = len(parser._actions)
    add_common_options(parser, IDENTITY)
    late = parser.add_argument("--late")
    spelled_alike = ExtendedArgumentParser(prog="scratch", identity=IDENTITY).add_argument(
        "--json", action="store_true"
    )
    assert contract.is_library_action(consumer) is False
    assert contract.is_library_action(late) is False
    assert contract.is_library_action(spelled_alike) is False
    library = parser._actions[before:-1]
    assert library
    assert all(contract.is_library_action(action) for action in library)
    assert contract.is_library_action(
        types.SimpleNamespace(_cli_extended_common=1)
    ) is False
    assert contract.canonical_flag(["-x", "--long"]) == "--long"
    assert contract.canonical_flag(["-x", "-y"]) == "-x"


# ------------------------------------------------------------------ manifest


def test_manifest_names_controls_per_route_and_records_the_contract():
    surface = export_cli_surface(_build())
    assert surface["schema_version"] == 7
    assert surface["library_contract"] == {"name": "cli-extended", "version": 1}
    routes = _routes(surface)
    assert routes[DEPLOY]["common_controls"] == sorted(ALL_V1_FLAGS)
    assert routes[RAW]["common_controls"] == sorted(ALL_V1_FLAGS - {"--json", "--yes"})
    assert routes[PLAIN]["common_controls"] == sorted(ALL_V1_FLAGS - {"--json", "--yes"})
    # Nested routes inherit their verb parser's controls.
    assert routes[LEAF]["common_controls"] == routes[TREE]["common_controls"]
    for route in routes.values():
        assert route["common_controls"] == sorted(route["common_controls"])
        assert not any(action.get("scope") == "common" for action in route["actions"])
    deploy_flags = {
        flag for action in routes[DEPLOY]["actions"] for flag in action.get("flags", ())
    }
    assert deploy_flags == {"--target"}


def test_manifest_holds_no_library_control_description():
    text = render_cli_surface_json(export_cli_surface(_build()))
    for fragment in (
        "show errors and warnings only",
        "emit machine-readable output",
        "accept confirmation prompts",
        "choose progress presentation",
        "LEVEL",
        "MODE",
        "_HelpAction",
        "_VersionAction",
    ):
        assert fragment not in text


def test_single_command_cli_names_its_controls_on_the_root_route():
    registry = CliRegistry(
        IDENTITY, prog="contract-tool", description="One.", single_command=True
    )
    registry.register(VerbSpec("only", description="only", handler=lambda *_: 0))
    surface = export_cli_surface(registry.build())
    (route,) = surface["routes"]
    assert route["single_command"] is True
    assert set(route["common_controls"]) == ALL_V1_FLAGS - {"--yes"}
    rebuilt = {
        item["canonical"]: item
        for item in _route_common_actions(route, _routes_by_path(surface["routes"]))
    }
    assert rebuilt["--quiet"]["placement"] == {
        "before_verb": False,
        "after_verb": True,
        "single_command_invocation": True,
    }
    multi = export_cli_surface(_build())
    deploy = _routes(multi)[DEPLOY]
    deploy_rebuilt = {
        item["canonical"]: item
        for item in _route_common_actions(deploy, _routes_by_path(multi["routes"]))
    }
    assert deploy_rebuilt["--quiet"]["placement"] == {
        "before_verb": True,
        "after_verb": True,
        "single_command_invocation": False,
    }
    minimum = _candidate(surface, route["id"], "minimum")
    assert _invocation_findings(surface, minimum, ["--quiet", "--log-level", "info"]) == []
    assert any(
        "undeclared unknown option" in item
        for item in _invocation_findings(surface, minimum, ["--yes"])
    )


def test_consumer_option_spelled_like_a_library_control_stays_consumer_grammar():
    """O3: marker, not spelling, decides ownership."""

    surface = export_cli_surface(_build())
    raw = _routes(surface)[RAW]
    assert "--json" not in raw["common_controls"]
    consumer = [action for action in raw["actions"] if action.get("flags") == ["--json"]]
    assert len(consumer) == 1
    assert consumer[0]["scope"] == "verb-local"
    assert consumer[0]["description"] == "consumer json"
    assert consumer[0]["action"] == "argparse._StoreTrueAction"


# ---------------------------------------------------------------- candidates


def test_reviewed_control_candidates_keep_their_pre_change_ids():
    """O5: IDs captured from the code before the contract change."""

    surface = export_cli_surface(_build())
    spelling_ids = sorted(
        item["id"]
        for item in surface["candidates"]
        if item["kind"] == "option-spelling"
        and item["shape"]["spelling"] in {"--json", "--yes", "--debug-raw"}
    )
    assert spelling_ids == [
        "case:route:entrypoint:contract-tool/deploy/option-spelling/option:route:entrypoint:contract-tool/deploy/--debug-raw/--debug-raw",
        "case:route:entrypoint:contract-tool/deploy/option-spelling/option:route:entrypoint:contract-tool/deploy/--json/--json",
        "case:route:entrypoint:contract-tool/deploy/option-spelling/option:route:entrypoint:contract-tool/deploy/--yes/--yes",
        "case:route:entrypoint:contract-tool/plain/option-spelling/option:route:entrypoint:contract-tool/plain/--debug-raw/--debug-raw",
        "case:route:entrypoint:contract-tool/raw/option-spelling/option:route:entrypoint:contract-tool/raw/--debug-raw/--debug-raw",
        "case:route:entrypoint:contract-tool/raw/option-spelling/option:route:entrypoint:contract-tool/raw/--json/--json",
        "case:route:entrypoint:contract-tool/tree/leaf/option-spelling/option:route:entrypoint:contract-tool/tree/leaf/parser:tree/--debug-raw/--debug-raw",
        "case:route:entrypoint:contract-tool/tree/leaf/option-spelling/option:route:entrypoint:contract-tool/tree/leaf/parser:tree/--json/--json",
        "case:route:entrypoint:contract-tool/tree/option-spelling/option:route:entrypoint:contract-tool/tree/--debug-raw/--debug-raw",
        "case:route:entrypoint:contract-tool/tree/option-spelling/option:route:entrypoint:contract-tool/tree/--json/--json",
    ]
    json_candidate = next(
        item for item in surface["candidates"] if item["id"] == spelling_ids[1]
    )
    assert json_candidate["kind"] == "option-spelling"
    assert json_candidate["members"] == [
        "option:route:entrypoint:contract-tool/deploy/--json"
    ]
    assert json_candidate["shape"] == {
        "option_id": "option:route:entrypoint:contract-tool/deploy/--json",
        "spelling": "--json",
    }


def test_other_library_controls_get_no_candidates():
    surface = export_cli_surface(_build())
    spellings = {
        item["shape"]["spelling"]
        for item in surface["candidates"]
        if item["kind"] == "option-spelling"
    }
    assert spellings == {"--json", "--yes", "--debug-raw", "--target"}
    assert not any(
        item["kind"] in {"exclusive-member", "exclusive-conflict", "option-choice"}
        for item in surface["candidates"]
    )


def test_library_help_and_metavar_never_reach_the_manifest_or_markdown(monkeypatch):
    """O1: patch the spec source and the built actions; output is byte-identical."""

    interactions = (
        {
            "id": "quiet-and-json",
            "route_id": DEPLOY,
            "option_ids": (f"option:{DEPLOY}/--quiet", f"option:{DEPLOY}/--json"),
        },
        {
            "id": "yes-from-elsewhere",
            "route_id": RAW,
            "option_ids": (f"option:{RAW}/--json", f"option:{DEPLOY}/--yes"),
        },
    )
    catalog = _catalog()

    def snapshot():
        surface = export_cli_surface(_build(), interaction_groups=interactions)
        return (
            render_cli_surface_json(surface),
            render_cli_surface_markdown(surface, catalog),
        )

    before_json, before_markdown = snapshot()
    assert '"interaction"' in before_json

    original_specs = parser_module._common_option_specs
    original_add = parser_module.add_common_options

    def patched_specs(**kwargs):
        return tuple(
            replace(spec, description="PATCHED " + spec.description, metavar="PATCHED")
            for spec in original_specs(**kwargs)
        )

    def patched_add(parser, identity, **kwargs):
        original_add(parser, identity, **kwargs)
        for action in parser._actions:
            if contract.is_library_action(action):
                action.help = "PATCHED " + str(action.help)
                if action.nargs != 0:
                    action.metavar = "PATCHED"

    monkeypatch.setattr(parser_module, "_common_option_specs", patched_specs)
    monkeypatch.setattr(parser_module, "add_common_options", patched_add)
    app = _build()
    level = app.parser._option_string_actions["--log-level"]
    quiet = app.parser._option_string_actions["--quiet"]
    assert level.metavar == "PATCHED"
    assert level.help.startswith("PATCHED")
    assert quiet.help.startswith("PATCHED")

    after_json, after_markdown = snapshot()
    assert after_json == before_json
    assert after_markdown == before_markdown
    assert "PATCHED" not in after_json
    assert "PATCHED" not in after_markdown


def test_consumer_declared_grammar_change_changes_that_routes_signatures():
    """O2: negative control for O1 (consumer grammar is still covered).

    Consumer help prose lands in the manifest and Markdown but, as before this
    change, is not part of any signature; the declared metavar is.
    """

    def signatures(surface):
        return {item["id"]: item["signature"] for item in surface["candidates"]}

    base = export_cli_surface(_build())
    metavar = export_cli_surface(_build(target_metavar="HOST"))
    helped = export_cli_surface(_build(target_help="a different target description"))
    target_id = f"case:{DEPLOY}/option-spelling/option:{DEPLOY}/--target/--target"
    json_id = f"case:{DEPLOY}/option-spelling/option:{DEPLOY}/--json/--json"
    raw_id = f"case:{RAW}/option-spelling/option:{RAW}/--json/--json"
    assert signatures(base).keys() == signatures(metavar).keys()
    assert signatures(base)[target_id] != signatures(metavar)[target_id]
    # Only the changed option's candidates move; library and other routes do not.
    assert signatures(base)[json_id] == signatures(metavar)[json_id]
    assert signatures(base)[raw_id] == signatures(metavar)[raw_id]
    assert signatures(base) == signatures(helped)
    assert render_cli_surface_json(base) != render_cli_surface_json(helped)


def test_interaction_over_library_controls_resolves_by_id():
    interactions = (
        {
            "id": "yes-from-elsewhere",
            "route_id": RAW,
            "option_ids": (f"option:{RAW}/--json", f"option:{DEPLOY}/--yes"),
        },
        {
            "id": "quiet-and-level",
            "route_id": DEPLOY,
            "option_ids": (f"option:{DEPLOY}/--quiet", f"option:{DEPLOY}/--log-level"),
        },
    )
    surface = export_cli_surface(_build(), interaction_groups=interactions)
    assert surface["interaction_issues"] == []
    external = _candidate_by_id(surface, f"case:{RAW}/interaction:yes-from-elsewhere")
    assert external["shape"]["external_options"] == [
        {
            "id": f"option:{DEPLOY}/--yes",
            "route_id": DEPLOY,
            "path": ["deploy"],
            "canonical": "--yes",
        }
    ]
    level = _candidate_by_id(surface, f"case:{DEPLOY}/interaction:quiet-and-level")
    assert level["members"] == [f"option:{DEPLOY}/--log-level", f"option:{DEPLOY}/--quiet"]


def _candidate_by_id(surface, candidate_id):
    return next(item for item in surface["candidates"] if item["id"] == candidate_id)


def test_common_parser_path_falls_back_to_the_route_path():
    route = {"id": "r", "path": ["verb", "sub"], "common_controls": []}
    assert _common_parser_path(route, {}, "--json") == ("verb", "sub")
    verb = {"id": "v", "path": ["verb"], "common_controls": ["--json"]}
    paths = _routes_by_path([verb, route])
    assert _common_parser_path(route, paths, "--json") == ("verb",)
    assert _common_parser_path(route, paths, "--quiet") == ("verb", "sub")
    assert _routes_by_path([verb, {**verb, "id": "dup"}])[("verb",)]["id"] == "v"


# ------------------------------------------------------------------ markdown


def test_markdown_renders_one_common_controls_line_per_route():
    surface = export_cli_surface(_build())
    markdown = render_cli_surface_markdown(surface, _catalog())
    assert (
        "Surface schema: `7`; review catalog schema: `1`; library contract: "
        "`cli-extended` v1."
    ) in markdown
    lines = [line for line in markdown.splitlines() if "Common controls" in line]
    assert len(lines) == len(surface["routes"])
    expected = (
        f"- `{DEPLOY}`: Common controls: "
        "--color, --debug, --debug-raw, --help, --json, --log-level, "
        "--no-color, --progress, --quiet, --version, --yes"
    )
    assert expected in lines
    for flag in ("--log-level", "--quiet", "--progress"):
        assert f"| {flag} " not in markdown
        assert f"/{flag} |" not in markdown
    assert "v1" in markdown.split("### Library common controls")[0]
    assert markdown.count("library contract:") == 1
    assert len(re.findall(r"\bv1\b", markdown.split("### Semantic case review")[0])) == 1


def test_markdown_lists_none_when_a_route_has_no_common_controls():
    surface = export_cli_surface(_build())
    surface["routes"][0]["common_controls"] = []
    del surface["routes"][1]["common_controls"]
    markdown = render_cli_surface_markdown(surface, _catalog())
    lines = [line for line in markdown.splitlines() if "Common controls" in line]
    assert lines[0] == f"- `{surface['routes'][0]['id']}`: Common controls: none"
    assert lines[1] == f"- `{surface['routes'][1]['id']}`: Common controls: none"


@pytest.mark.parametrize(
    "record",
    (
        None,
        [],
        {"name": "cli-extended"},
        {"version": 1},
        {"name": 3, "version": 1},
        {"name": "cli-extended", "version": True},
        {"name": "cli-extended", "version": "1"},
    ),
)
def test_markdown_rejects_a_surface_without_a_valid_contract_record(record):
    surface = export_cli_surface(_build())
    if record is None:
        del surface["library_contract"]
    else:
        surface["library_contract"] = record
    with pytest.raises(SurfaceSpecError, match="library_contract"):
        render_cli_surface_markdown(surface, _catalog())


# ------------------------------------------------------------------- checker


def test_invocations_with_library_controls_check_on_enabled_routes():
    """O6: controls before and after the verb pass where they are enabled."""

    surface = export_cli_surface(_build())
    candidate = _candidate_by_id(
        surface,
        f"case:{DEPLOY}/option-spelling/option:{DEPLOY}/--json/--json",
    )
    for invocation in (
        ["deploy", "--log-level", "debug", "--progress", "plain", "--json"],
        ["--json", "--log-level", "debug", "--progress", "plain", "deploy"],
        ["--quiet", "deploy", "--json", "--yes", "--no-color", "--debug-raw"],
        ["--verbose", "deploy", "--json", "--target", "host"],
    ):
        assert _invocation_findings(surface, candidate, invocation) == [], invocation


def test_control_missing_from_a_route_is_an_unrecognized_option():
    surface = export_cli_surface(_build())
    candidate = _candidate(surface, PLAIN, "minimum")
    assert _invocation_findings(surface, candidate, ["plain", "--quiet"]) == []
    for invocation in (["plain", "--json"], ["--json", "plain"], ["plain", "--yes"]):
        findings = _invocation_findings(surface, candidate, invocation)
        assert any("undeclared unknown option" in item for item in findings), invocation
    unknown = _invocation_findings(surface, candidate, ["plain", "--json"])
    assert any("'--json'" in item for item in unknown)


def test_library_control_values_are_checked_from_the_contract_table():
    surface = export_cli_surface(_build())
    candidate = _candidate(surface, DEPLOY, "minimum")
    invalid = _invocation_findings(surface, candidate, ["deploy", "--log-level", "loud"])
    assert any("supplies an invalid value for option" in item for item in invalid)
    missing = _invocation_findings(surface, candidate, ["deploy", "--log-level"])
    assert any("omits a value for --log-level" in item for item in missing)
    inline = _invocation_findings(surface, candidate, ["deploy", "--quiet=1"])
    assert any("inline value to flag-only option" in item for item in inline)
    nested = _candidate(surface, LEAF, "minimum")
    assert _invocation_findings(surface, nested, ["tree", "--quiet", "leaf"]) == []
    assert _invocation_findings(surface, nested, ["tree", "leaf", "--quiet"]) != []


# ------------------------------------------------------------- check and sync


def _files(tmp_path, app):
    review = tmp_path / "cli-review.toml"
    review.write_text(
        'schema_version = 1\ncli_id = "contract-tool"\n', encoding="utf-8"
    )
    manifest = tmp_path / "cli-surface.json"
    spec = tmp_path / "SPEC.md"
    spec.write_text(
        f"# Spec\n\n{SURFACE_START_MARKER}\nold\n{SURFACE_END_MARKER}\n", encoding="utf-8"
    )
    return {"review_path": review, "manifest_path": manifest, "spec_path": spec}


def test_sync_writes_the_contract_fields(tmp_path):
    app = _build()
    paths = _files(tmp_path, app)
    sync_cli_surface(app, **paths)
    written = json.loads(paths["manifest_path"].read_text(encoding="utf-8"))
    assert written["library_contract"] == {"name": "cli-extended", "version": 1}
    assert written["schema_version"] == 7
    routes = {route["id"]: route for route in written["routes"]}
    assert "--log-level" in routes[DEPLOY]["common_controls"]
    spec = paths["spec_path"].read_text(encoding="utf-8")
    assert "library contract: `cli-extended` v1." in spec
    assert f"- `{DEPLOY}`: Common controls: --color," in spec
    findings = check_cli_surface(app, **paths).findings
    assert findings
    assert all(item.startswith("missing semantic review case") for item in findings)


def test_contract_bump_yields_exactly_one_finding(monkeypatch, tmp_path):
    """O4: one finding, no per-case or stale-file noise."""

    app = _build()
    paths = _files(tmp_path, app)
    sync_cli_surface(app, **paths)
    before = check_cli_surface(app, **paths)
    assert before.findings
    assert not any("contract changed" in item for item in before.findings)

    monkeypatch.setattr(contract, "CONTRACT_VERSION", 2)
    report = check_cli_surface(app, **paths)
    assert report.findings == (
        "cli-extended contract changed v1 → v2; read cli-extended CHANGES.md "
        "contract notes, then run sync",
    )
    assert report.passed is False

    sync_cli_surface(app, **paths)
    written = json.loads(paths["manifest_path"].read_text(encoding="utf-8"))
    assert written["library_contract"]["version"] == 2
    again = check_cli_surface(app, **paths)
    assert not any("contract changed" in item for item in again.findings)


@pytest.mark.parametrize(
    "mutate",
    (
        lambda data: data.pop("library_contract"),
        lambda data: data.update(library_contract=[]),
        lambda data: data.update(library_contract={"name": "cli-extended"}),
        lambda data: data.update(library_contract={"version": True}),
        lambda data: data.update(library_contract={"version": "1"}),
    ),
)
def test_manifest_without_a_usable_contract_record_keeps_stale_handling(tmp_path, mutate):
    app = _build()
    paths = _files(tmp_path, app)
    sync_cli_surface(app, **paths)
    data = json.loads(paths["manifest_path"].read_text(encoding="utf-8"))
    mutate(data)
    data["schema_version"] = 6
    paths["manifest_path"].write_text(json.dumps(data), encoding="utf-8")
    report = check_cli_surface(app, **paths)
    assert "generated CLI manifest is stale" in report.findings
    assert not any("contract changed" in item for item in report.findings)


def _patch_library_syntax(monkeypatch, *, extra_flag=True):
    original = contract.common_control_table

    def patched():
        table = original()
        for entry in table.values():
            entry.update(
                nargs=3,
                takes_value=True,
                choices=["x", "y"],
                help="PATCHED",
                metavar="PATCHED",
            )
            if extra_flag:
                entry["flags"] = [*entry["flags"], "--extra"]
        return table

    monkeypatch.setattr(contract, "common_control_table", patched)


GOLDEN_JSON_SIGNATURE = (
    "sha256:4c674c51a53b799d2cf385dae801ea250185ae5cc016a1045a5dca54514a867f"
)


def test_library_control_candidate_shape_and_signature_are_pinned(monkeypatch):
    json_id = f"case:{DEPLOY}/option-spelling/option:{DEPLOY}/--json/--json"
    surface = export_cli_surface(_build())
    candidate = _candidate_by_id(surface, json_id)
    assert candidate["shape"] == {
        "option_id": f"option:{DEPLOY}/--json",
        "spelling": "--json",
    }
    # The literal covers the signed member shape (id, kind, canonical, scope)
    # and the route context, so any widening or narrowing changes it.
    assert candidate["signature"] == GOLDEN_JSON_SIGNATURE

    _patch_library_syntax(monkeypatch)
    patched = export_cli_surface(_build())
    assert _candidate_by_id(patched, json_id)["signature"] == GOLDEN_JSON_SIGNATURE
    assert {c["id"]: c["signature"] for c in patched["candidates"]} == {
        c["id"]: c["signature"] for c in surface["candidates"]
    }
    assert render_cli_surface_json(patched) == render_cli_surface_json(surface)


def test_interaction_signature_ignores_library_control_syntax(monkeypatch):
    interactions = (
        {
            "id": "yes-from-elsewhere",
            "route_id": RAW,
            "option_ids": (f"option:{RAW}/--json", f"option:{DEPLOY}/--yes"),
        },
    )
    case_id = f"case:{RAW}/interaction:yes-from-elsewhere"
    before = _candidate_by_id(
        export_cli_surface(_build(), interaction_groups=interactions), case_id
    )
    _patch_library_syntax(monkeypatch, extra_flag=False)
    after_surface = export_cli_surface(_build(), interaction_groups=interactions)
    assert after_surface["interaction_issues"] == []
    after = _candidate_by_id(after_surface, case_id)
    assert after["signature"] == before["signature"]
    assert after["shape"] == before["shape"]
    (external,) = after["shape"]["external_options"]
    assert set(external) == {"id", "route_id", "path", "canonical"}


def test_foreign_library_control_still_checks_in_an_interaction_invocation():
    interactions = (
        {
            "id": "yes-from-elsewhere",
            "route_id": RAW,
            "option_ids": (f"option:{RAW}/--json", f"option:{DEPLOY}/--yes"),
        },
    )
    surface = export_cli_surface(_build(), interaction_groups=interactions)
    candidate = _candidate_by_id(surface, f"case:{RAW}/interaction:yes-from-elsewhere")
    assert _invocation_findings(surface, candidate, ["raw", "--json", "--yes"]) == []
    missing = _invocation_findings(surface, candidate, ["raw", "--json"])
    assert any("does not supply the out-of-route option" in item for item in missing)
    inline = _invocation_findings(surface, candidate, ["raw", "--json", "--yes=1"])
    assert any("inline value to flag-only out-of-route option" in item for item in inline)


def _report_cli(*, single_command=False, delegate=None):
    registry = CliRegistry(
        IDENTITY,
        prog="contract-tool",
        description="Report mode.",
        unexpected_exceptions="report",
        single_command=single_command,
    )
    registry.register(
        VerbSpec(
            "go", description="go", mutating=True, dry_run=True, handler=lambda *_: 0
        )
    )
    if not single_command:
        registry.register(VerbSpec("stay", description="stay", handler=lambda *_: 0))
    if delegate is not None:
        registry.register(VerbSpec("leaf", description="leaf", delegate=delegate))
    return registry.build()


def _assert_report_controls_are_named_only(surface, *, present):
    assert surface["routes"]
    for route in surface["routes"]:
        flags = {
            flag for action in route["actions"] for flag in action.get("flags", ())
        }
        assert "--traceback" not in flags
        assert "--dry-run" not in flags
    for route_id, expected in present.items():
        controls = _routes(surface)[route_id]["common_controls"]
        for flag in expected:
            assert flag in controls, (route_id, flag)


def test_report_mode_controls_are_marked_for_multi_verb_and_single_command():
    multi = export_cli_surface(_report_cli())
    _assert_report_controls_are_named_only(
        multi,
        present={
            "route:entrypoint:contract-tool/go": ("--traceback", "--dry-run"),
            "route:entrypoint:contract-tool/stay": ("--traceback",),
        },
    )
    assert "--dry-run" not in _routes(multi)["route:entrypoint:contract-tool/stay"][
        "common_controls"
    ]
    single = export_cli_surface(_report_cli(single_command=True))
    (route,) = single["routes"]
    _assert_report_controls_are_named_only(
        single, present={route["id"]: ("--traceback", "--dry-run")}
    )


def test_report_mode_controls_are_marked_in_a_delegated_child_cli():
    child = _report_cli()
    parent = _report_cli(delegate=child)
    surface = export_cli_surface(parent)
    leaf = "route:entrypoint:contract-tool/leaf"
    _assert_report_controls_are_named_only(
        surface,
        present={
            f"{leaf}/go": ("--traceback", "--dry-run"),
            f"{leaf}/stay": ("--traceback",),
        },
    )
    assert _routes(surface)[leaf]["common_controls"] == []


def test_committed_contract_version_reads_only_integer_versions():
    good = json.dumps({"library_contract": {"name": "cli-extended", "version": 4}})
    assert _committed_contract_version(good) == 4
    assert _committed_contract_version(None) is None
    assert _committed_contract_version("not json") is None
    assert _committed_contract_version("[]") is None
    assert _committed_contract_version("{}") is None
    assert _committed_contract_version('{"library_contract": {"version": 2.5}}') is None
    assert _committed_contract_version('{"library_contract": {"version": false}}') is None
