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
    SurfaceReport,
    _case_status,
    _markdown_cell,
    _parse_interaction_groups,
    _replace_generated_region,
    _review_findings,
    _statically_skipped,
)

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


def _findings_for_invocation(
    candidate,
    route,
    invocation,
    *,
    route_prefixes=(),
    allow_abbrev=False,
):
    case = _case_for_candidate(candidate, invocation)
    return _review_findings(
        {
            "entrypoint": {
                "command": "audit-tool",
                "allow_abbrev": allow_abbrev,
            },
            "routes": [*route_prefixes, route],
            "candidates": [candidate],
            "syntax_complete": True,
        },
        ReviewCatalog("audit-tool", 8, (), (case,)),
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
        "entrypoint": {
            "command": "audit-tool",
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
                    },
                ],
            },
            {
                "id": "route:entrypoint:audit-tool",
                "path": [],
                "kind": "invocation",
                "single_command": True,
                "no_args_action": True,
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
        "schema_version": 3,
        "entrypoint": {
            "command": "audit-tool",
            "prog": "audit-tool",
            "builtins": ["help", "help <verb>", "version", "--help", "--version"],
        },
        "routes": [
            {
                "id": group_id,
                "path": ["plugins"],
                "kind": "delegate-group",
                "subcommands": [child_id],
                "actions": [],
                "syntax_complete": True,
            },
            {
                "id": child_id,
                "path": ["plugins", "inspect"],
                "kind": "invocation",
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
        "entrypoint": {"command": "audit-tool"},
        "routes": [
            {
                "id": "route:entrypoint:audit-tool",
                "path": [],
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
            "actions": [],
        },
        {
            "id": "route:entrypoint:audit-tool/adapter",
            "path": ["adapter"],
            "kind": "invocation",
            "single_command": True,
            "no_args_action": True,
            "actions": [],
        },
        {
            "id": "route:entrypoint:audit-tool/plugins",
            "path": ["plugins"],
            "kind": "delegate-group",
            "single_command": False,
            "actions": [],
        },
        {
            "id": "route:entrypoint:audit-tool/inspect",
            "path": ["inspect"],
            "kind": "route-prefix",
            "actions": [],
        },
        {
            "id": "route:entrypoint:audit-tool/status",
            "path": ["status"],
            "kind": "invocation",
            "single_command": False,
            "actions": [],
        },
    ]
    surface = {
        "schema_version": 3,
        "entrypoint": {
            "command": "audit-tool",
            "prog": "audit-tool",
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
    assert "delegated group; child CLI parses remaining tokens" in markdown
    assert "route prefix; selects a nested command" in markdown
    assert "command route; parser handles remaining tokens" in markdown
    assert "multi-command; empty argv shows help" not in markdown


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
        {"id": action_id, "kind": "option", "flags": [flag], "nargs": nargs, "minimum_values": minimum, "required": False, "parser_path": ["inspect"]}
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
            {"id": "exclusive-a", "kind": "option", "flags": ["--a"], "nargs": None, "required": False, "parser_path": ["inspect", "detail"]},
            {"id": "exclusive-b", "kind": "option", "flags": ["--b"], "nargs": None, "required": False, "parser_path": ["inspect", "detail"]},
            {"id": "argument:resource", "kind": "argument", "name": "resource", "nargs": "?", "minimum_values": 0, "required": False, "choices": ["alpha"], "parser_path": ["inspect", "detail"]},
        ],
    }
    single = {"id": "route:entrypoint:audit-tool/single", "path": [], "aliases": [], "kind": "invocation", "actions": []}
    candidates = [
        {"id": "case:bad-minimum", "route_id": leaf["id"], "signature": "s", "kind": "minimum", "members": [], "shape": {"required_arguments": ["resource"], "required_argument_values": {"resource": 1}, "required_options": ["required-flag"], "required_exclusive_groups": {"source": ["exclusive-a", "exclusive-b"]}}},
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
    assert any("omits a value for its required exclusive group source" in item for item in findings)
    assert any("supplies multiple options for required exclusive group source" in item for item in findings)
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
                "required": False,
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
        "shape": {"required_options": ["option:missing"]},
    }
    findings = _findings_for_invocation(
        candidate, route, ["inspect", "--present"]
    )
    assert any("references unknown required option option:missing" in item for item in findings)


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
    assert json.loads(manifest.read_text(encoding="utf-8"))["schema_version"] == 3
    assert SURFACE_START_MARKER.encode() in synced
    assert SURFACE_END_MARKER.encode() in synced


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
