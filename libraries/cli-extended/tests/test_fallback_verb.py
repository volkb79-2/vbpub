"""LCR-1: the fallback ("default") verb.

Oracles are the numbered rules and the required-test list of
DESIGN-RG82 section 2.3. Every test drives a real built registry; nothing
here mocks the normalisation.
"""

from __future__ import annotations

import io
import json
import sys
import types
from pathlib import Path
from types import SimpleNamespace

import pytest

import cli_extended.cli as cli_module
import cli_extended.identity as identity_module
from baseline_app import build_baseline_app
from cli_extended import (
    ArgumentSpec,
    CliFailure,
    CliIdentity,
    CliRegistry,
    OptionSpec,
    UsageError,
    VerbSpec,
    assert_cli_contract,
    check_cli_surface,
    export_cli_surface,
    render_cli_surface_json,
    sync_cli_surface,
)
from cli_extended.audit import run_audit
from cli_extended.cli import template_text
from cli_extended.config import load_project_config
from cli_extended.review import SURFACE_END_MARKER, SURFACE_START_MARKER

DATA = Path(__file__).parent / "data"
IDENTITY = CliIdentity("AUDIT", "1.0", "Audit Tool", command="audit-tool")
RUN_ROUTE = "route:entrypoint:audit-tool/run"


def build(calls, *, fallback=True, extra=None):
    """A run-gate shaped registry: fallback ``run`` plus ordinary verbs."""

    def record(name):
        def handler(args, runtime):
            calls.append((name, dict(vars(args))))
            return 0

        return handler

    def run_handler(args, runtime):
        calls.append(("run", dict(vars(args))))
        if args.lane == "docter":
            raise CliFailure(f"unknown lane {args.lane!r}", exit_code=2)
        return 0

    child = CliRegistry(
        IDENTITY, prog="audit-tool admission", description="Admission."
    )
    child.register(
        VerbSpec("show", description="show admission", handler=record("admission show"))
    )
    registry = CliRegistry(IDENTITY, prog="audit-tool", description="Audit.")
    registry.register(
        VerbSpec(
            "run",
            description="run a lane",
            fallback=fallback,
            handler=run_handler,
            arguments=(
                ArgumentSpec("lane", "the lane"),
                ArgumentSpec(
                    "lane_args", "extra tokens", parser_kwargs={"nargs": "*"}
                ),
            ),
            options=(
                OptionSpec(("--worktree",), "worktree", metavar="PATH"),
                OptionSpec(("--base",), "base ref", metavar="REF"),
                OptionSpec(
                    ("--fresh",), "fresh run", parser_kwargs={"action": "store_true"}
                ),
            ),
        )
    )
    registry.register(VerbSpec("list", description="list lanes", handler=record("list")))
    registry.register(VerbSpec("admission", description="admission", delegate=child.build()))
    if extra is not None:
        extra(registry)
    return registry.build()


def run(app, *argv):
    out, err = io.StringIO(), io.StringIO()
    code = app.run(argv=list(argv), stdout=out, stderr=err)
    return code, out.getvalue(), err.getvalue()


# ------------------------------------------------------- rule 2: normalisation


def test_bare_lane_selects_the_fallback_verb():
    calls = []
    code, out, err = run(build(calls), "lane1")
    assert (code, err) == (0, "")
    assert [name for name, _ in calls] == ["run"]
    assert calls[0][1]["lane"] == "lane1"
    assert calls[0][1]["verb"] == "run"


def test_leading_options_before_the_lane_are_recognised():
    """Leading-option test: the verb must go FIRST in the rewritten argv."""

    calls = []
    code, _, err = run(build(calls), "--worktree", "X", "--base", "B", "--fresh", "lane1")
    assert (code, err) == (0, "")
    _, args = calls[0]
    assert (args["lane"], args["worktree"], args["base"], args["fresh"]) == (
        "lane1", "X", "B", True,
    )


def test_equals_form_leading_option():
    calls = []
    code, _, _ = run(build(calls), "--worktree=X", "--base=B", "lane1")
    assert code == 0
    assert (calls[0][1]["worktree"], calls[0][1]["base"]) == ("X", "B")


def test_library_root_options_may_lead_the_lane():
    calls = []
    code, _, _ = run(build(calls), "--json", "--worktree", "X", "lane1")
    assert code == 0
    assert calls[0][1]["json"] is True and calls[0][1]["lane"] == "lane1"


def test_double_dash_after_the_lane_reaches_lane_args():
    calls = []
    code, _, err = run(build(calls), "lane1", "--", "a", "-b")
    assert (code, err) == (0, "")
    assert calls[0][1]["lane"] == "lane1"
    assert calls[0][1]["lane_args"] == ["a", "-b"]


def test_options_after_the_lane_still_parse():
    calls = []
    assert run(build(calls), "lane1", "--base", "B")[0] == 0
    assert calls[0][1]["base"] == "B"


def test_unknown_leading_option_is_not_rewritten_and_exits_two():
    calls = []
    code, _, err = run(build(calls), "--bogus", "lane1")
    assert code == 2
    assert calls == []
    assert "invalid choice: 'lane1'" in err


def test_double_dash_first_is_not_rewritten():
    calls = []
    code, _, err = run(build(calls), "--", "lane1")
    assert code == 2
    assert calls == []
    assert "[ERROR]" in err


def test_option_missing_its_value_is_not_rewritten():
    calls = []
    code, _, _ = run(build(calls), "--worktree")
    assert code == 2
    assert calls == []


def test_only_leading_options_still_selects_the_fallback_which_refuses():
    calls = []
    code, _, err = run(build(calls), "--worktree", "X")
    assert code == 2
    assert calls == []
    assert "lane" in err


@pytest.mark.parametrize(
    "argv, expected",
    [
        (["list"], "list"),
        (["--json", "list"], "list"),
        (["admission", "show"], "admission show"),
        (["run", "lane1"], "run"),
    ],
)
def test_a_registered_verb_or_delegate_wins_over_the_fallback(argv, expected):
    calls = []
    code, _, err = run(build(calls), *argv)
    assert (code, err) == (0, "")
    assert [name for name, _ in calls] == [expected]


def test_explicit_run_is_the_same_call_as_the_bare_form():
    bare, explicit = [], []
    run(build(bare), "--worktree", "X", "lane1", "--", "a")
    run(build(explicit), "run", "--worktree", "X", "lane1", "--", "a")
    assert bare == explicit and bare


def test_a_verb_named_like_a_lane_needs_the_explicit_spelling():
    calls = []
    run(build(calls), "run", "list")
    assert calls[0][0] == "run" and calls[0][1]["lane"] == "list"


def test_help_and_version_win_over_the_fallback():
    calls = []
    app = build(calls)
    code, out, _ = run(app, "help")
    assert code == 0 and out.startswith(IDENTITY.headline)
    code, out, _ = run(app, "version")
    assert (code, out) == (0, IDENTITY.version_line + "\n")
    code, out, _ = run(app, "--version")
    assert (code, out) == (0, IDENTITY.version_line + "\n")
    code, out, _ = run(app, "--help")
    assert code == 0 and "default verb" in out
    code, _, err = run(app, "help", "nonsense")
    assert code == 2 and "unknown help topic 'nonsense'" in err
    assert calls == []


def test_empty_argv_prints_help_and_exits_zero():
    calls = []
    code, out, err = run(build(calls))
    assert (code, err) == (0, "")
    assert out.startswith(IDENTITY.headline) and "audit-tool [options] lane ..." in out
    assert calls == []


def test_a_lane_help_flag_shows_the_fallback_verbs_help():
    code, out, _ = run(build([]), "lane1", "--help")
    assert code == 0 and "Behavior: default verb." in out


def test_errors_inside_the_fallback_are_not_swallowed():
    """Rule 5: ``docter`` becomes ``run docter`` and fails in the handler."""

    calls = []
    code, _, err = run(build(calls), "docter")
    assert code == 2
    assert "unknown lane 'docter'" in err
    assert calls[0][0] == "run"


# ----------------------------------------------------------------- parse_args


@pytest.mark.parametrize(
    "argv",
    [
        ["lane1"],
        ["--worktree", "X", "--base", "B", "lane1"],
        ["--worktree=X", "lane1", "--", "a", "-b"],
        ["run", "lane1"],
        ["--json", "list"],
        ["lane1", "--fresh"],
    ],
)
def test_parse_args_equals_what_run_executes(argv):
    calls = []
    app = build(calls)
    assert run(app, *argv)[0] == 0
    parsed = app.parse_args(argv)
    assert vars(parsed) == calls[0][1]


def test_parse_args_runs_no_handler_and_reports_usage_errors():
    calls = []
    app = build(calls)
    app.parse_args(["lane1"])
    assert calls == []
    with pytest.raises(UsageError):
        app.parse_args(["--bogus", "lane1"])
    with pytest.raises(UsageError):
        app.parse_args([])


def test_parse_args_defaults_to_sys_argv(monkeypatch):
    monkeypatch.setattr(sys, "argv", ["audit-tool", "--base", "B", "laneX"])
    parsed = build([]).parse_args()
    assert (parsed.verb, parsed.lane, parsed.base) == ("run", "laneX", "B")


def test_parse_args_without_a_fallback_does_not_rewrite():
    app = build([], fallback=False)
    assert app.fallback_verb is None
    with pytest.raises(UsageError):
        app.parse_args(["lane1"])
    assert app.parse_args(["run", "lane1"]).lane == "lane1"


def test_registered_cli_exposes_the_fallback_name():
    assert build([]).fallback_verb == "run"


# ------------------------------------------------------------------- help


def test_catalog_marks_the_fallback_verb_and_help_run_works():
    app = build([])
    text = app.catalog.render()
    assert "  run        run a lane [default verb]\n" in text
    assert "Usage: audit-tool <verb> [options]\n       audit-tool [options] lane ...\n" in text
    code, out, _ = run(app, "help", "run")
    assert code == 0 and "Behavior: default verb." in out
    assert "default verb" not in "".join(
        line for line in text.splitlines() if line.lstrip().startswith("list")
    )
    markdown = app.catalog.render_markdown()
    assert "audit-tool [options] lane ..." in markdown
    assert "**Behavior:** default verb." in markdown


def test_markdown_help_with_a_configure_callback_lists_the_label():
    def extra(registry):
        registry.register(
            VerbSpec(
                "custom",
                description="custom",
                handler=lambda *_: 0,
                configure=lambda parser: parser.add_argument("--x"),
            )
        )

    app = build([], extra=extra)
    assert "**Behavior:** default verb." in app.catalog.render_markdown()


def test_no_fallback_no_usage_line_and_no_label():
    app = build([], fallback=False)
    text = app.catalog.render()
    assert "[options] lane" not in text and "default verb" not in text
    assert "[options] lane" not in app.catalog.render_markdown()


# ------------------------------------------------------- rule 1: registration


def _verb(name="other", **kwargs):
    kwargs.setdefault("arguments", (ArgumentSpec("name", "the token"),))
    kwargs.setdefault("handler", lambda *_: 0)
    return VerbSpec(name, description=name, fallback=True, **kwargs)


def test_two_fallback_verbs_are_refused():
    registry = CliRegistry(IDENTITY, prog="audit-tool", description="Audit.")
    registry.register(_verb("one"))
    with pytest.raises(ValueError, match="'two' cannot also be a fallback verb; 'one'"):
        registry.register(_verb("two"))
    assert [verb.name for verb in registry.verbs] == ["one"]


def test_a_fallback_with_a_delegate_is_refused():
    registry = CliRegistry(IDENTITY, prog="audit-tool", description="Audit.")
    child = CliRegistry(IDENTITY, prog="audit-tool group", description="Group.")
    child.register(VerbSpec("x", description="x", handler=lambda *_: 0))
    with pytest.raises(ValueError, match="cannot delegate"):
        registry.register(_verb("group", handler=None, delegate=child.build()))


def test_a_fallback_without_an_argument_spec_is_refused():
    registry = CliRegistry(IDENTITY, prog="audit-tool", description="Audit.")
    with pytest.raises(ValueError, match="at least one ArgumentSpec"):
        registry.register(_verb(arguments=()))


def test_fallback_must_be_a_bool():
    with pytest.raises(TypeError, match="fallback must be a bool"):
        VerbSpec("x", description="x", handler=lambda *_: 0, fallback="yes")


def test_a_single_command_registry_refuses_a_fallback():
    registry = CliRegistry(
        IDENTITY, prog="audit-tool", description="Audit.", single_command=True
    )
    registry.register(_verb("only"))
    with pytest.raises(ValueError, match="only valid for a multi-verb CLI"):
        registry.build()


# --------------------------------------------------- rule 4: surface and audit


def _surface(app):
    return export_cli_surface(app)


def test_surface_key_is_present_only_for_the_fallback_route():
    surface = _surface(build([]))
    by_id = {route["id"]: route for route in surface["routes"]}
    assert by_id[RUN_ROUTE]["fallback"] is True
    others = [route for route_id, route in by_id.items() if route_id != RUN_ROUTE]
    assert others and all("fallback" not in route for route in others)
    assert surface["library_contract"] == {"name": "cli-extended", "version": 1}
    assert surface["schema_version"] == 7


def test_surface_without_a_fallback_has_no_key_at_all():
    surface = _surface(build([], fallback=False))
    assert all("fallback" not in route for route in surface["routes"])
    assert '"fallback"' not in render_cli_surface_json(surface)


def test_a_manifest_without_a_fallback_is_byte_identical_to_the_one_generated_at_main():
    """The baseline was generated by the 0.3.0 library at ``main`` (see baseline_app)."""

    expected = (DATA / "surface-baseline-main.json").read_bytes()
    actual = render_cli_surface_json(export_cli_surface(build_baseline_app()))
    assert actual.encode("utf-8") == expected


def test_the_fallback_route_changes_the_review_signature():
    plain = _surface(build([], fallback=False))
    flagged = _surface(build([]))
    plain_sigs = {c["id"]: c["signature"] for c in plain["candidates"]}
    flagged_sigs = {c["id"]: c["signature"] for c in flagged["candidates"]}
    run_cases = [case for case in plain_sigs if "/run/" in case]
    list_cases = [case for case in plain_sigs if "/list/" in case]
    assert run_cases and list_cases
    assert all(plain_sigs[case] != flagged_sigs[case] for case in run_cases)
    assert all(plain_sigs[case] == flagged_sigs[case] for case in list_cases)


def _surface_files(tmp_path, app):
    review = tmp_path / "cli-review.toml"
    review.write_text('schema_version = 1\ncli_id = "audit-tool"\n', encoding="utf-8")
    review.write_text(
        review.read_text(encoding="utf-8") + template_text(app, review, None),
        encoding="utf-8",
    )
    spec = tmp_path / "SPEC.md"
    spec.write_text(
        f"# Spec\n\n{SURFACE_START_MARKER}\nold\n{SURFACE_END_MARKER}\n", encoding="utf-8"
    )
    return {
        "review_path": review,
        "manifest_path": tmp_path / "cli-surface.json",
        "spec_path": spec,
    }


def test_surface_sync_and_check_work_with_a_fallback_verb(tmp_path):
    app = build([])
    paths = _surface_files(tmp_path, app)
    sync_cli_surface(app, **paths)
    written = json.loads(paths["manifest_path"].read_text(encoding="utf-8"))
    flagged = [route["id"] for route in written["routes"] if route.get("fallback")]
    assert flagged == [RUN_ROUTE]
    spec = paths["spec_path"].read_text(encoding="utf-8")
    assert "default verb: a first token that is not a verb selects this verb" in spec
    report = check_cli_surface(app, **paths)
    # Only the (pending) semantic-review cases remain: manifest and spec are current.
    assert report.findings
    assert all(f.startswith("semantic review is pending") for f in report.findings)
    # Removing the flag makes the committed manifest stale: check must notice.
    stale = check_cli_surface(build([], fallback=False), **paths)
    assert any("manifest" in finding.lower() for finding in stale.findings)


@pytest.fixture
def isolated(tmp_path, monkeypatch):
    monkeypatch.setattr(sys, "path", list(sys.path))
    monkeypatch.setitem(
        sys.modules,
        "_cli_extended_surface_factory",
        types.ModuleType("_cli_extended_surface_factory"),
    )
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(tmp_path / "home" / "claude"))
    monkeypatch.setattr(identity_module, "installed_version", lambda _name: "0.1.0")


FACTORY = '''\
from cli_extended import (
    ArgumentSpec, CliIdentity, CliRegistry, OptionSpec, VerbSpec,
)


def build_cli():
    registry = CliRegistry(
        CliIdentity("AUDIT", "1.0", "Audit Tool", command="audit-tool"),
        prog="audit-tool",
        description="Audit.",
        unexpected_exceptions="report",
    )
    registry.register(VerbSpec(
        "run", description="run a lane", fallback=True, handler=lambda *_: 0,
        arguments=(ArgumentSpec("lane", "the lane"),),
        options=(OptionSpec(("--worktree",), "worktree", metavar="PATH"),),
    ))
    registry.register(VerbSpec(
        "list", description="list lanes", handler=lambda *_: 0, include_progress=False,
    ))
    return registry.build()
'''


def test_audit_runs_with_a_fallback_verb(tmp_path, isolated):
    root = tmp_path / "proj"
    root.mkdir()
    (root / "factory.py").write_text(FACTORY, encoding="utf-8")
    (root / "cli-extended.toml").write_text(
        'schema_version = 1\n\n[[clis]]\nid = "audit-tool"\nfactory = "factory.py:build_cli"\n',
        encoding="utf-8",
    )
    project = load_project_config(root / "cli-extended.toml")
    items = {item.check: item for item in run_audit(project.select(None), project)}
    assert {name: items[name].status for name in (
        "synopsis-overrides", "shadowed-controls", "configure-callbacks",
        "hidden-options", "exception-policy", "mutation-safety",
    )} == dict.fromkeys((
        "synopsis-overrides", "shadowed-controls", "configure-callbacks",
        "hidden-options", "exception-policy", "mutation-safety",
    ), "pass")


def test_the_cli_extended_audit_and_surface_commands_accept_a_fallback_project(
    tmp_path, isolated, monkeypatch, capsys
):
    root = tmp_path / "proj"
    root.mkdir()
    (root / "factory.py").write_text(FACTORY, encoding="utf-8")
    (root / "cli-extended.toml").write_text(
        'schema_version = 1\n\n[[clis]]\nid = "audit-tool"\nfactory = "factory.py:build_cli"\n'
        'review = "cli-review.toml"\nmanifest = "cli-surface.json"\nspec = "CLI-SPEC.md"\n',
        encoding="utf-8",
    )
    (root / "cli-review.toml").write_text(
        'schema_version = 1\ncli_id = "audit-tool"\n', encoding="utf-8"
    )
    (root / "CLI-SPEC.md").write_text(
        f"# Spec\n\n{SURFACE_START_MARKER}\nold\n{SURFACE_END_MARKER}\n", encoding="utf-8"
    )
    monkeypatch.chdir(root)
    assert cli_module.main(["surface", "sync"]) == 0
    capsys.readouterr()
    manifest = json.loads((root / "cli-surface.json").read_text(encoding="utf-8"))
    assert [r["id"] for r in manifest["routes"] if r.get("fallback")] == [RUN_ROUTE]
    # The catalog has no judged cases yet, so `surface check` fails, but ONLY
    # for missing review cases: manifest and spec are current.
    assert cli_module.main(["surface", "check"]) == 1
    findings = [
        line for line in capsys.readouterr().err.splitlines() if line.startswith("[REVIEW]")
    ]
    assert findings
    assert all(line.startswith("[REVIEW] missing semantic review case") for line in findings)
    code = cli_module.main(["audit"])
    out = capsys.readouterr().out
    assert code == 1
    assert "[PASS] AC-16 surface-configured" in out
    assert "[PASS] AC-18 surface-complete: the exported surface is syntax-complete" in out
    assert "[FAIL] AC-17 surface-check: the surface check reports 8 problem(s)" in out


def test_assert_cli_contract_passes_for_a_fallback_cli():
    app = build([])

    def invoke(argv):
        out, err = io.StringIO(), io.StringIO()
        code = app.run(argv=list(argv), stdout=out, stderr=err)
        return SimpleNamespace(returncode=code, stdout=out.getvalue(), stderr=err.getvalue())

    assert_cli_contract(
        invoke,
        IDENTITY,
        ("run", "list", "admission"),
        invalid_invocations={"unknown leading option": ["--bogus", "lane1"]},
    )
