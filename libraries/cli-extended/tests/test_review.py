from __future__ import annotations

import argparse
import json
from pathlib import Path

import pytest

from cli_extended import (
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
    _case_status,
    _parse_interaction_groups,
    _replace_generated_region,
    _review_findings,
    _statically_skipped,
)

IDENTITY = CliIdentity("AUDIT", "1.0", "Audit Tool", command="audit-tool")
ROUTE_ID = "route:entrypoint:audit-tool/inspect"
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


def _candidate_argv(candidate, surface):
    route = next(route for route in surface["routes"] if route["id"] == candidate["route_id"])
    actions = {action["id"]: action for action in route["actions"]}
    argv = list(route["path"])
    kind = candidate["kind"]
    if kind == "route-alias":
        argv[-1] = candidate["shape"]["alias"]
    elif kind == "minimum":
        for _argument in candidate["shape"]["required_arguments"]:
            argv.append("RESOURCE")
        required_options = list(candidate["shape"]["required_options"])
        for _group_id, option_ids in candidate["shape"]["required_exclusive_groups"].items():
            required_options.append(option_ids[0])
        for option_id in required_options:
            action = actions[option_id]
            flag = action["flags"][0]
            argv.append(flag)
            if action.get("nargs") != 0:
                argv.append(_choice_value(action))
    elif kind in {"argument-shape", "argument-choice"}:
        if kind == "argument-choice":
            argv.append(str(candidate["shape"]["choice"]))
        else:
            argv.append("RESOURCE")
    elif kind == "option-spelling":
        spelling = candidate["shape"]["spelling"]
        action = actions[candidate["shape"]["option_id"]]
        argv.append(spelling)
        if action.get("nargs") != 0:
            argv.append(_choice_value(action))
    elif kind == "option-choice":
        action = actions[candidate["shape"]["option_id"]]
        argv.extend((action["flags"][0], str(candidate["shape"]["choice"])))
    elif kind in {"exclusive-member", "exclusive-conflict", "interaction"}:
        for member_id in candidate["members"]:
            action = actions[member_id]
            argv.append(action["flags"][0])
            if action.get("nargs") != 0:
                argv.append(_choice_value(action))
    return argv


def _case_for_candidate(
    candidate,
    invocation,
    *,
    state="active",
    signature=None,
    invocation_declared=True,
    effects_declared=True,
    test_ids=("tests/test_review.py::test_review_findings_fixture",),
    retirement_reason="",
):
    return ReviewCase(
        case_id=candidate["id"],
        state=state,
        decision="accept" if state == "active" else None,
        reviewed_signature=signature or candidate.get("signature"),
        rationale="reviewed" if state == "active" else "",
        invocation=tuple(invocation),
        invocation_declared=invocation_declared,
        expected_exit_status=0 if state == "active" else None,
        expected_stdout_contains="",
        expected_stderr_contains="",
        effects=(),
        effects_declared=effects_declared,
        test_ids=tuple(test_ids),
        retirement_reason=retirement_reason,
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


def test_replace_generated_region_preserves_prefix_newlines_and_rejects_nested_markers():
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


def test_surface_markdown_exposes_nested_routes_stale_rows_and_incomplete_inventory():
    candidate = {
        "id": "case:inspect/run",
        "route_id": "route:inspect",
        "signature": "sha256:new",
        "kind": "minimum",
        "members": ["option:parent", "argument:child"],
        "shape": {"values": [1, 2]},
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
    surface = {
        "schema_version": 1,
        "entrypoint": {"command": "audit-tool"},
        "routes": [
            {
                "id": "route:inspect",
                "path": ["inspect", "run"],
                "kind": "route-prefix",
                "aliases": ["r"],
                "subcommand_groups": [
                    {"destination": "operation", "required": True, "subcommands": ["route:inspect/run"]},
                    {"destination": "optional-operation", "required": False, "subcommands": ["route:inspect/check"]},
                ],
                "description": "Run an operation.",
                "group": "CHANGE",
                "behavior": ["mutating"],
                "syntax_complete": False,
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
                        "scope": "common",
                        "placement": {"before_verb": True, "after_verb": False},
                        "parser_path": ["inspect"],
                        "before_nested_subcommand": True,
                        "help_group": "OPTIONS",
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
                    },
                ],
            }
        ],
        "candidates": [candidate],
        "syntax_complete": False,
        "incomplete": ["uninspectable callback field | reason"],
    }
    catalog = ReviewCatalog("audit-tool", 8, (), (retired, stale_active))
    markdown = render_cli_surface_markdown(surface, catalog)

    assert "route-prefix" in markdown
    assert "operation: route:inspect/run (required)" in markdown
    assert "optional-operation: route:inspect/check" in markdown
    assert "before nested subcommand" in markdown
    assert "REVIEW REQUIRED: retired ID is active again" in markdown
    assert "STALE: disposition required" in markdown
    assert "Surface inventory is incomplete" in markdown
    assert "uninspectable callback field \\| reason" in markdown
    assert _case_status(candidate, catalog.cases_by_id)[0].startswith("REVIEW REQUIRED")


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


def test_review_findings_checks_minimum_alias_positional_and_option_semantics():
    route_id = "route:entrypoint:audit-tool/inspect"
    actions = [
        {"id": "arg:resource", "kind": "argument", "name": "resource", "nargs": None, "minimum_values": 1, "required": True, "choices": ["alpha"]},
        {"id": "opt:needed", "kind": "option", "flags": ["--needed"], "nargs": None, "minimum_values": 1, "required": True},
        {"id": "opt:file", "kind": "option", "flags": ["--file"], "nargs": None, "minimum_values": 1, "required": False, "exclusive_group": "source", "exclusive_required": True},
        {"id": "opt:inline", "kind": "option", "flags": ["--inline"], "nargs": None, "minimum_values": 1, "required": False, "exclusive_group": "source", "exclusive_required": True},
        {"id": "opt:mode", "kind": "option", "flags": ["--mode"], "nargs": None, "minimum_values": 1, "required": False, "choices": ["safe", "fast"]},
    ]
    route = {"id": route_id, "path": ["inspect"], "aliases": [], "kind": "invocation", "actions": actions}
    good_minimum = {"id": "case:minimum", "route_id": route_id, "signature": "s", "kind": "minimum", "members": ["arg:resource", "opt:needed", "opt:file"], "shape": {"required_arguments": ["arg:resource"], "required_argument_values": {"arg:resource": 1}, "required_options": ["opt:needed"], "required_exclusive_groups": {"source": ["opt:file", "opt:inline"]}}}
    alias = {"id": "case:alias", "route_id": route_id, "signature": "s", "kind": "route-alias", "members": [], "shape": {"alias": "i"}}
    argument_choice = {"id": "case:argument-choice", "route_id": route_id, "signature": "s", "kind": "argument-choice", "members": ["arg:resource"], "shape": {"choice": "alpha"}}
    argument_shape = {"id": "case:argument-shape", "route_id": route_id, "signature": "s", "kind": "argument-shape", "members": ["arg:resource"], "shape": {}}
    missing_spelling = {"id": "case:missing-spelling", "route_id": route_id, "signature": "s", "kind": "option-spelling", "members": ["opt:needed"], "shape": {"spelling": "--needed"}}
    wrong_choice = {"id": "case:wrong-choice", "route_id": route_id, "signature": "s", "kind": "option-choice", "members": ["opt:mode"], "shape": {"choice": "fast"}}
    candidates = [good_minimum, alias, argument_choice, argument_shape, missing_spelling, wrong_choice]
    invocations = {
        "case:minimum": ["inspect", "alpha", "--needed", "token", "--file", "profile.toml"],
        "case:alias": ["inspect"],
        "case:argument-choice": ["inspect", "wrong"],
        "case:argument-shape": ["inspect"],
        "case:missing-spelling": ["inspect"],
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


def test_review_invocation_lexer_handles_option_arities_and_delimiters():
    route_id = "route:entrypoint:audit-tool/inspect"
    arities = (
        ("zero", "--zero", 0, 0, ["--zero"]),
        ("one", "--one", None, 1, ["--one", "v"]),
        ("fixed", "--fixed", 2, 2, ["--fixed=first", "second"]),
        ("maybe", "--maybe", "?", 0, ["--maybe", "--zero"]),
        ("many", "--many", "*", 0, ["--many", "a", "b", "--zero"]),
        ("some", "--some", "+", 1, ["--some", "a", "b", "--zero"]),
        ("rest", "--rest", argparse.REMAINDER, 0, ["--rest", "tail", "--zero"]),
        ("parser", "--parser", argparse.PARSER, 1, ["--parser", "child"]),
        ("odd", "--odd", "odd", 0, ["--odd"]),
    )
    actions = [
        {"id": action_id, "kind": "option", "flags": [flag], "nargs": nargs, "minimum_values": minimum, "required": False}
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
    assert any("omits its reviewed option spelling" in item for item in findings)


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
            {"id": "required-flag", "kind": "option", "flags": ["--required"], "nargs": None, "minimum_values": 1, "required": True},
            {"id": "exclusive-a", "kind": "option", "flags": ["--a"], "nargs": None, "required": False},
            {"id": "exclusive-b", "kind": "option", "flags": ["--b"], "nargs": None, "required": False},
        ],
    }
    single = {"id": "route:entrypoint:audit-tool/single", "path": [], "aliases": [], "kind": "invocation", "actions": []}
    candidates = [
        {"id": "case:bad-minimum", "route_id": leaf["id"], "signature": "s", "kind": "minimum", "members": [], "shape": {"required_arguments": ["resource"], "required_argument_values": {"resource": 1}, "required_options": ["required-flag"], "required_exclusive_groups": {"source": ["exclusive-a", "exclusive-b"]}}},
        {"id": "case:missing-path", "route_id": leaf["id"], "signature": "s", "kind": "other", "members": [], "shape": {}},
        {"id": "case:alias-good", "route_id": leaf["id"], "signature": "s", "kind": "route-alias", "members": [], "shape": {"alias": "d"}},
        {"id": "case:choice-good", "route_id": leaf["id"], "signature": "s", "kind": "argument-choice", "members": [], "shape": {"choice": "alpha"}},
        {"id": "case:unknown-member", "route_id": leaf["id"], "signature": "s", "kind": "other", "members": ["does-not-exist"], "shape": {}},
        {"id": "case:single", "route_id": single["id"], "signature": "s", "kind": "minimum", "members": [], "shape": {}},
    ]
    invocations = {
        "case:bad-minimum": ["inspect", "d"],
        "case:missing-path": [],
        "case:alias-good": ["inspect", "d"],
        "case:choice-good": ["inspect", "detail", "alpha"],
        "case:unknown-member": ["inspect", "detail"],
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

    assert any("minimum invocation" in item and "positional" in item for item in findings)
    assert any("omits required option required-flag" in item for item in findings)
    assert any("omits required exclusive group source" in item for item in findings)
    assert any("omits its command path" in item for item in findings)
    assert not any("case:alias-good" in item for item in findings)
    assert not any("case:choice-good" in item for item in findings)


def test_review_findings_matches_nested_path_after_skipping_unrelated_tokens():
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
        "actions": [],
    }
    candidate = {
        "id": "case:nested-path",
        "route_id": leaf["id"],
        "signature": "s",
        "kind": "other",
        "members": [],
        "shape": {},
    }
    case = _case_for_candidate(candidate, ["--global", "i", "unknown", "detail"])
    findings = _review_findings(
        {"routes": [prefix, leaf], "candidates": [candidate], "syntax_complete": True},
        ReviewCatalog("audit-tool", 4, (), (case,)),
    )
    assert not any("omits its command path" in item for item in findings)


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
    assert json.loads(manifest.read_text(encoding="utf-8"))["schema_version"] == 1
    assert SURFACE_START_MARKER.encode() in synced
    assert SURFACE_END_MARKER.encode() in synced


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
