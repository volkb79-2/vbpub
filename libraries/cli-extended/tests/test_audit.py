"""W7: the adoption checklist, the mechanical audit and the ``audit`` verb."""

from __future__ import annotations

import json
import os
import re
import sys
import textwrap
import types
from importlib import resources
from pathlib import Path

import pytest
from test_workflow import CONFIG as WORKFLOW_CONFIG
from test_workflow import _project as workflow_project
from test_workflow import _run

import cli_extended
import cli_extended.audit as audit_module
import cli_extended.cli as cli_module
import cli_extended.identity as identity_module
from cli_extended import (
    AuditItem,
    ConfigError,
    load_project_config,
    run_audit,
    validate_skill_source,
)
from cli_extended.audit import CHECKLIST_IDS, SHADOWED_REPLACEMENTS

PACKAGE_ROOT = Path(__file__).resolve().parents[1]
CHECKLIST = PACKAGE_ROOT / "docs" / "ADOPTION-CHECKLIST.md"
FACTORY_NAME = "_cli_extended_surface_factory"
SKILL_PACKAGES = "skpkg_audit_"

HEAD = '''\
from cli_extended import *
from cli_extended import CheckResult, DoctorCheck, register_doctor, register_skills_verbs


def build_cli():
    registry = CliRegistry(
        CliIdentity("AUDIT", "1.0", "Audit Tool", command="audit-tool"),
        prog="audit-tool",
        description="Audit.",
        {kwargs}
    )
'''
TAIL = '''\
    registry.register(VerbSpec(
        "base", description="do the base thing", handler=lambda *_: 0, include_progress=False,
    ))
    return registry.build()
'''
STANDALONE = 'schema_version = 1\n\n[[clis]]\nid = "audit-tool"\nfactory = "factory.py:build_cli"\n{extra}'


@pytest.fixture(autouse=True)
def _isolated(tmp_path, monkeypatch):
    monkeypatch.setattr(sys, "path", list(sys.path))
    monkeypatch.setitem(sys.modules, FACTORY_NAME, types.ModuleType(FACTORY_NAME))
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(tmp_path / "home" / "claude"))
    monkeypatch.setattr(identity_module, "installed_version", lambda _name: "0.1.0")
    before = set(sys.modules)
    yield
    for name in set(sys.modules) - before:
        if name.startswith(SKILL_PACKAGES):
            del sys.modules[name]


def _factory(body: str = "", kwargs: str = 'unexpected_exceptions="report",') -> str:
    return (
        HEAD.format(kwargs=kwargs)
        + textwrap.indent(textwrap.dedent(body), "    ")
        + TAIL
    )


def _setup(tmp_path, body="", kwargs='unexpected_exceptions="report",', files=None, config=""):
    root = tmp_path / "proj"
    root.mkdir()
    (root / "factory.py").write_text(_factory(body, kwargs), encoding="utf-8")
    (root / "cli-extended.toml").write_text(STANDALONE.format(extra=config), encoding="utf-8")
    for name, text in (files or {}).items():
        path = root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        if isinstance(text, bytes):
            path.write_bytes(text)
        else:
            path.write_text(text, encoding="utf-8")
    return root


def _audit(tmp_path, **kwargs):
    root = _setup(tmp_path, **kwargs)
    return _run_audit(root / "cli-extended.toml")


def _run_audit(config_path):
    project = load_project_config(config_path)
    cli = project.select(None)
    return {item.check: item for item in run_audit(cli, project)}


# ----------------------------------------------------------- O2 checklist <-> code


def _rows():
    rows = []
    for line in CHECKLIST.read_text(encoding="utf-8").splitlines():
        if line.startswith("| AC-"):
            cells = [cell.strip() for cell in line.strip().strip("|").split("|")]
            rows.append(cells)
    return rows


def test_o2_checklist_audit_checks_match_code_exactly():
    rows = _rows()
    assert all(len(row) == 5 for row in rows)
    ids = [row[0] for row in rows]
    assert len(ids) == len(set(ids))
    assert all(re.fullmatch(r"AC-\d\d", identifier) for identifier in ids)
    assert ids == sorted(ids)
    audited = [row[3] for row in rows if row[3] != "manual"]
    assert all(cell.startswith("audit:") for cell in audited)
    names = [cell.removeprefix("audit:") for cell in audited]
    assert len(names) == len(set(names))
    assert set(names) == set(CHECKLIST_IDS) == set(audit_module._CHECKS)
    by_check = {row[3].removeprefix("audit:"): row[0] for row in rows if row[3] != "manual"}
    assert by_check == CHECKLIST_IDS
    # Checklist order is report order.
    assert [row[0] for row in rows if row[3] != "manual"] == list(CHECKLIST_IDS.values())
    assert all(row[1] and row[2] and "](" in row[4] for row in rows)


def test_o2_every_area_table_exists_and_rows_link_to_docs():
    text = CHECKLIST.read_text(encoding="utf-8")
    for area in (
        "Identity and version", "Registration and grammar", "Runtime policy",
        "Confirmation and dry-run", "Output", "Surface and semantic review", "Skills",
        "Doctor", "Tests", "Packaging and dependency",
    ):
        assert f"\n## {area}\n" in text


def test_shadowed_controls_cover_every_documented_library_flag():
    assert set(SHADOWED_REPLACEMENTS) == {
        "--dry-run", "--yes", "--json", "--traceback", "--no-color", "--color",
        "--quiet", "--debug", "--verbose", "--log-level", "--progress",
        "--debug-raw", "--help", "--version",
    }


def test_audit_item_as_dict_and_exports():
    item = AuditItem("doctor", "AC-20", "manual", "s", ("e",), "r")
    assert item.as_dict() == {
        "check": "doctor", "checklist_id": "AC-20", "status": "manual",
        "summary": "s", "evidence": ["e"], "remedy": "r",
    }
    assert AuditItem("a", "AC-01", "pass", "s").evidence == ()
    assert AuditItem("a", "AC-01", "pass", "s").remedy is None
    assert cli_extended.run_audit is run_audit
    assert "AuditItem" in cli_extended.__all__ and "run_audit" in cli_extended.__all__


def test_report_order_and_ids_follow_the_checklist(tmp_path):
    root = _setup(tmp_path)
    project = load_project_config(root / "cli-extended.toml")
    items = run_audit(project.select(None), project)
    assert [item.check for item in items] == list(CHECKLIST_IDS)
    assert [item.checklist_id for item in items] == list(CHECKLIST_IDS.values())


def test_baseline_statuses_for_a_minimal_project(tmp_path):
    items = _audit(tmp_path)
    assert {name: item.status for name, item in items.items()} == {
        "version-source": "manual",
        "synopsis-overrides": "pass",
        "shadowed-controls": "pass",
        "configure-callbacks": "pass",
        "hidden-options": "pass",
        "exception-policy": "pass",
        "mutation-safety": "pass",
        "surface-configured": "fail",
        "surface-check": "manual",
        "surface-complete": "pass",
        "skills-packaged": "manual",
        "doctor": "manual",
        "pytest-plugin": "manual",
        "dependency-declared": "manual",
        "no-path-hacks": "pass",
    }


# ------------------------------------------------------------------ version-source


@pytest.mark.parametrize(
    ("files", "status"),
    [
        ({"a.py": "CliIdentity(\nimport importlib.metadata\n"}, "fail"),
        ({"a.py": "CliIdentity(\nre.match(x)\nVERSION = 1\n"}, "fail"),
        ({"a.toml": "CliIdentity(\nimportlib.metadata\n"}, "fail"),
        ({"a.py": "CliIdentity(\nre.match(x)\n"}, "manual"),
        ({"a.py": "CliIdentity(\nVERSION = 1\n"}, "manual"),
        ({"a.py": "CliIdentity(\nare.x\nVERSION = 1\n"}, "manual"),
        ({"a.py": "import importlib.metadata\nre.match(x)\nVERSION = 1\n"}, "manual"),
        ({"a.txt": "CliIdentity(\nimportlib.metadata\n"}, "manual"),
        ({".venv/a.py": "CliIdentity(\nimportlib.metadata\n"}, "manual"),
        ({".worktrees/w/a.py": "CliIdentity(\nimportlib.metadata\n"}, "manual"),
        ({"a.py": "CliIdentity.resolve(name='x')\n"}, "pass"),
        # Only a call counts: a longer name that merely starts with the
        # method name is not a use of CliIdentity.resolve.
        ({"a.py": "x = CliIdentity.resolved_name\n"}, "manual"),
        (
            {"a.py": "CliIdentity.resolve(name='x')\n", "b.py": "CliIdentity(\nimportlib.metadata\n"},
            "fail",
        ),
    ],
)
def test_version_source_cases(tmp_path, files, status):
    item = _audit(tmp_path, files=files)["version-source"]
    assert item.status == status
    assert item.checklist_id == "AC-01"
    if status == "fail":
        assert item.evidence and set(item.evidence) <= {"a.py", "a.toml", "b.py"}
        assert item.remedy.startswith("Replace it with CliIdentity.resolve")
    if status == "pass":
        assert item.summary == "heuristic: CliIdentity.resolve( is used"
        assert item.evidence == ()
    if status == "manual":
        assert item.summary == (
            "heuristic: no CliIdentity.resolve( found; confirm where the version comes from"
        )
        assert item.remedy == (
            "Use CliIdentity.resolve so the version has one source and no fallback."
        )
        assert item.evidence == ()


def test_version_source_failure_evidence_is_relative_and_ordered(tmp_path):
    item = _audit(
        tmp_path,
        files={
            "z.py": "CliIdentity(\nimportlib.metadata\n",
            "pkg/m.py": "CliIdentity(\nre.sub(a)\nVERSION\n",
            "a.py": "CliIdentity(\nimportlib.metadata\n",
        },
    )["version-source"]
    assert item.evidence == ("a.py", "z.py", "pkg/m.py")
    assert item.summary == "heuristic: a hand-rolled version reader sits next to CliIdentity(...)"


# ---------------------------------------------------------------- synopsis-overrides


def test_synopsis_redundant_is_warn_and_overrides_are_manual(tmp_path):
    body = '''
    registry.register(VerbSpec(
        "alpha", description="alpha", synopsis="[options]", handler=lambda *_: 0,
        options=(OptionSpec(("--mode",), "mode"),),
    ))
    registry.register(VerbSpec(
        "beta", description="beta", synopsis="<thing> [options]", handler=lambda *_: 0,
        options=(OptionSpec(("--mode",), "mode"),),
    ))
    '''
    item = _audit(tmp_path, body=body)["synopsis-overrides"]
    assert item.status == "warn"
    assert item.summary == "1 redundant synopsis override(s)"
    assert item.evidence == (
        "alpha: synopsis '[options]' equals the derived synopsis",
        "beta: synopsis '<thing> [options]' overrides derived '[options]'",
    )
    assert item.remedy == "Delete the synopsis= argument; the library derives it."


def test_synopsis_override_alone_is_manual(tmp_path):
    body = '''
    registry.register(VerbSpec(
        "beta", description="beta", synopsis="<thing>", handler=lambda *_: 0,
        options=(OptionSpec(("--mode",), "mode"),),
    ))
    '''
    item = _audit(tmp_path, body=body)["synopsis-overrides"]
    assert item.status == "manual"
    assert item.summary == "1 synopsis override(s) need a justification"
    assert item.evidence == ("beta: synopsis '<thing>' overrides derived '[options]'",)
    assert item.remedy == "Keep an override only when the derived synopsis misleads."


# ------------------------------------------------------------------ shadowed-controls


def test_shadowed_controls_fail_names_verb_flag_and_replacement(tmp_path):
    body = '''
    registry.register(VerbSpec(
        "alpha", description="alpha", handler=lambda *_: 0,
        include_json=False, include_progress=False,
        options=(
            OptionSpec(("--json", "-j"), "json"),
            OptionSpec(("--progress",), "progress"),
            OptionSpec(("--dry-run",), "dry"),
            OptionSpec(("--fine",), "fine"),
        ),
    ))
    '''
    item = _audit(tmp_path, body=body)["shadowed-controls"]
    assert item.status == "fail"
    assert item.summary == "3 consumer option(s) shadow library controls"
    assert item.evidence == (
        "alpha: --json shadows a library control; use the library --json (VerbSpec include_json)",
        "alpha: --progress shadows a library control; "
        "use the library progress control (VerbSpec include_progress)",
        "alpha: --dry-run shadows a library control; use VerbSpec(dry_run=True)",
    )
    assert item.remedy == (
        "Remove the hand-rolled option and use the library feature named in the evidence."
    )


def test_shadowed_controls_see_global_and_delegated_options(tmp_path):
    kwargs = (
        'unexpected_exceptions="report", '
        'global_options=(OptionSpec(("--progress",), "p"), OptionSpec(("--profile",), "q")),'
    )
    body = '''
    child = CliRegistry(
        CliIdentity("AUDIT", "1.0", "Audit Tool", command="audit-tool"),
        prog="audit-tool grp", description="Group.", unexpected_exceptions="report",
    )
    child.register(VerbSpec(
        "inner", description="inner", handler=lambda *_: 0, include_json=False,
        options=(OptionSpec(("--json",), "json"),),
    ))
    registry.register(VerbSpec(
        "grp", description="group", delegate=child.build(), include_progress=False,
    ))
    registry.register(VerbSpec(
        "alpha", description="alpha", handler=lambda *_: 0, include_progress=False,
    ))
    '''
    item = _audit(tmp_path, body=body, kwargs=kwargs)["shadowed-controls"]
    assert item.status == "fail"
    assert item.evidence == (
        "(global): --progress shadows a library control; "
        "use the library progress control (VerbSpec include_progress)",
        "grp inner: --json shadows a library control; use the library --json (VerbSpec include_json)",
    )


# ------------------------------------------------------------------ mutation-safety


def test_mutation_safety_lists_only_verbs_with_neither_protection(tmp_path):
    body = '''
    registry.register(VerbSpec("wipe", description="wipe", mutating=True,
                               include_confirmation=False, handler=lambda *_: 0))
    registry.register(VerbSpec("confirmed", description="c", mutating=True,
                               handler=lambda *_: 0))
    registry.register(VerbSpec("dry", description="d", mutating=True, dry_run=True,
                               include_confirmation=False, handler=lambda *_: 0))
    registry.register(VerbSpec("reader", description="r", include_confirmation=False,
                               handler=lambda *_: 0))
    '''
    item = _audit(tmp_path, body=body)["mutation-safety"]
    assert item.status == "manual"
    assert item.summary == "1 mutating verb(s) have neither confirmation nor dry-run"
    assert item.evidence == ("wipe",)
    assert item.remedy == (
        "Judge each: add VerbSpec(dry_run=True) or confirmation, or record why not."
    )


def test_mutation_safety_passes_when_protected(tmp_path):
    body = '''
    registry.register(VerbSpec("confirmed", description="c", mutating=True,
                               handler=lambda *_: 0))
    '''
    item = _audit(tmp_path, body=body)["mutation-safety"]
    assert (item.status, item.summary, item.evidence) == (
        "pass", "every mutating verb has confirmation or dry-run", (),
    )


# --------------------------------------------------- configure-callbacks, hidden-options


def test_configure_callbacks_and_hidden_options(tmp_path):
    body = '''
    registry.register(VerbSpec("cfg", description="c", handler=lambda *_: 0,
                               configure=lambda parser: None))
    registry.register(VerbSpec(
        "hid", description="h", handler=lambda *_: 0,
        options=(OptionSpec(("--secret", "-s"), "s", hidden=True),
                 OptionSpec(("--shown",), "shown")),
    ))
    '''
    kwargs = (
        'unexpected_exceptions="report", '
        'global_options=(OptionSpec(("--internal",), "i", hidden=True),),'
    )
    items = _audit(tmp_path, body=body, kwargs=kwargs)
    configure = items["configure-callbacks"]
    assert configure.status == "manual"
    assert configure.summary == "1 verb(s) use a configure callback"
    assert configure.evidence == ("cfg",)
    assert configure.remedy == (
        "Judge whether each could be declared with ArgumentSpec/OptionSpec/constraints."
    )
    hidden = items["hidden-options"]
    assert hidden.status == "manual"
    assert hidden.summary == "2 hidden option(s) need a justification"
    assert hidden.evidence == ("(global): --internal", "hid: --secret/-s")
    assert hidden.remedy == (
        "Keep a hidden option only when it is internal or deprecated; record why."
    )


def test_clean_configure_and_hidden_pass(tmp_path):
    items = _audit(tmp_path)
    assert items["configure-callbacks"].summary == "no verb uses a configure callback"
    assert items["hidden-options"].summary == "no hidden options"
    assert items["configure-callbacks"].evidence == ()


# ------------------------------------------------------------------ exception-policy


def test_exception_policy_report_passes_and_raise_warns(tmp_path):
    ok = _audit(tmp_path)["exception-policy"]
    assert (ok.status, ok.summary) == (
        "pass", "unexpected_exceptions='report' (library boundary)",
    )
    other = tmp_path / "other"
    other.mkdir()
    warn = _audit(other, kwargs="")["exception-policy"]
    assert warn.status == "warn"
    assert warn.summary == "unexpected_exceptions='raise': tracebacks reach users"
    assert warn.remedy == (
        "Pass unexpected_exceptions='report' to CliRegistry; --traceback restores the stack."
    )


# --------------------------------------------------------------------------- surface


def test_surface_configured_pass_and_fail_variants(tmp_path):
    full = (
        'review = "r.toml"\nmanifest = "m.json"\nspec = "s.md"\n'
    )
    (tmp_path / "a").mkdir()
    ok = _audit(tmp_path / "a", config=full)
    assert (ok["surface-configured"].status, ok["surface-configured"].summary) == (
        "pass", "review, manifest and spec are configured",
    )
    (tmp_path / "b").mkdir()
    only_review = _audit(tmp_path / "b", config='review = "r.toml"\n')["surface-configured"]
    assert only_review.status == "fail"
    assert only_review.summary == "the surface contract is not fully configured"
    assert only_review.evidence == ("missing: manifest", "missing: spec")
    assert only_review.remedy == (
        "Set review, manifest and spec on the [[clis]] entry (see the CONSUMERS guide)."
    )
    (tmp_path / "c").mkdir()
    no_review = _audit(tmp_path / "c", config='manifest = "m.json"\nspec = "s.md"\n')
    assert no_review["surface-configured"].evidence == ("missing: review",)
    # An unconfigured surface leaves the check manual.
    assert no_review["surface-check"].status == "manual"
    assert no_review["surface-check"].summary == "surface not configured; nothing to check"


def test_surface_check_and_complete_on_a_synced_project(tmp_path, monkeypatch, capsys):
    root = workflow_project(tmp_path, monkeypatch)
    assert _run(capsys, "surface", "sync")[0] == 0
    assert _run(capsys, "surface", "check")[0] == 0
    items = _run_audit(root / "cli-extended.toml")
    assert items["surface-configured"].status == "pass"
    check = items["surface-check"]
    assert (check.status, check.summary, check.evidence) == (
        "pass", "the surface check passes", (),
    )
    complete = items["surface-complete"]
    assert (complete.status, complete.summary) == (
        "pass", "the exported surface is syntax-complete",
    )
    assert items["pytest-plugin"].status == "fail"
    assert items["pytest-plugin"].summary == (
        "heuristic: a review catalog exists but cli_extended.pytest_plugin is not enabled"
    )
    assert items["pytest-plugin"].remedy == (
        "Add `-p cli_extended.pytest_plugin` to pytest addopts or `pytest_plugins` "
        "in conftest.py."
    )


def test_surface_check_reports_stale_files_as_evidence(tmp_path, monkeypatch):
    root = workflow_project(tmp_path, monkeypatch)
    item = _run_audit(root / "cli-extended.toml")["surface-check"]
    assert item.status == "fail"
    assert item.summary == f"the surface check reports {len(item.evidence)} problem(s)"
    assert "generated CLI manifest is stale" in item.evidence
    assert item.remedy == "Run `cli-extended surface sync`, then judge and re-check."


def test_surface_check_passes_the_findings_path(tmp_path, monkeypatch, capsys):
    config = WORKFLOW_CONFIG + 'findings = "findings.toml"\n'
    root = workflow_project(tmp_path, monkeypatch, config=config)
    (root / "findings.toml").write_text(
        'schema_version = 1\ncli_id = "audit-tool"\n\n[[findings]]\nid = "b1"\n'
        'status = "open"\nseverity = "blocker"\ncategory = "adoption"\n'
        'summary = "Blocking."\nremedy = "Fix it."\n',
        encoding="utf-8",
    )
    assert _run(capsys, "surface", "sync")[0] == 0
    item = _run_audit(root / "cli-extended.toml")["surface-check"]
    assert item.status == "fail"
    assert item.evidence == ("open blocker finding b1: Blocking.",)


def test_surface_check_and_complete_fail_on_a_broken_catalog(tmp_path, monkeypatch):
    root = workflow_project(tmp_path, monkeypatch)
    (root / "cli-review.toml").write_text("not = [valid", encoding="utf-8")
    items = _run_audit(root / "cli-extended.toml")
    for name, summary, remedy in (
        (
            "surface-check", "the surface check could not run",
            "Fix the reported file, then run `cli-extended surface check`.",
        ),
        (
            "surface-complete", "the surface could not be exported",
            "Fix the reported problem, then re-run the audit.",
        ),
    ):
        item = items[name]
        assert (item.status, item.summary, item.remedy) == ("fail", summary, remedy)
        assert len(item.evidence) == 1 and "cli-review.toml" in item.evidence[0]


def test_surface_complete_lists_incomplete_entries_and_routes(tmp_path):
    body = '''
    registry.register(VerbSpec(
        "odd", description="odd", handler=lambda *_: 0,
        configure=lambda parser: parser.add_argument("--x", nargs="?", const=object()),
    ))
    '''
    item = _audit(tmp_path, body=body)["surface-complete"]
    assert item.status == "fail"
    assert item.summary == "the exported surface is incomplete (3 entries)"
    assert item.evidence == (
        "route route:entrypoint:audit-tool/odd is not syntax-complete",
        "route:entrypoint:audit-tool/odd: cannot enumerate parser field "
        "option:route:entrypoint:audit-tool/odd/--x.const",
        "route:entrypoint:audit-tool/odd: optional-value const for 'x' "
        "cannot be represented exactly",
    )
    assert item.remedy == (
        "Declare the missing syntax (arguments, options, constraints) on the verbs."
    )


def test_surface_complete_without_catalog_uses_default_limit(tmp_path):
    item = _audit(tmp_path)["surface-complete"]
    assert item.status == "pass"


def test_surface_complete_reports_a_limit_error_as_failure(tmp_path, monkeypatch):
    root = workflow_project(tmp_path, monkeypatch)
    catalog = (root / "cli-review.toml").read_text(encoding="utf-8")
    (root / "cli-review.toml").write_text(
        catalog.replace("max_candidates = 128", "max_candidates = 1"), encoding="utf-8"
    )
    item = _run_audit(root / "cli-extended.toml")["surface-complete"]
    assert item.status == "fail"
    assert item.summary == "the surface could not be exported"


# ---------------------------------------------------------------------------- skills


def _skill_package(root: Path, name: str, skills: dict[str, str]) -> None:
    package = root / name
    (package / "skills").mkdir(parents=True)
    (package / "__init__.py").write_text("", encoding="utf-8")
    for skill, text in skills.items():
        (package / "skills" / skill).mkdir()
        (package / "skills" / skill / "SKILL.md").write_text(text, encoding="utf-8")


def _skill(name: str, description: str = "Use when testing.") -> str:
    return f"---\nname: {name}\ndescription: {description}\n---\n\nBody.\n"


def _skills_project(tmp_path, monkeypatch, name, skills):
    libs = tmp_path / "libs"
    libs.mkdir()
    _skill_package(libs, name, skills)
    monkeypatch.syspath_prepend(str(libs))
    return _audit(
        tmp_path,
        body=f'register_skills_verbs(registry, package="{name}")\n',
    )["skills-packaged"]


def test_skills_packaged_pass_lists_sorted_names(tmp_path, monkeypatch):
    item = _skills_project(
        tmp_path, monkeypatch, f"{SKILL_PACKAGES}ok",
        {"zeta": _skill("zeta"), "alpha": _skill("alpha")},
    )
    assert item.status == "pass"
    assert item.summary == "2 packaged skill(s) validate: alpha, zeta"
    assert item.checklist_id == "AC-19"


def test_skills_packaged_fails_on_an_invalid_skill(tmp_path, monkeypatch):
    item = _skills_project(
        tmp_path, monkeypatch, f"{SKILL_PACKAGES}bad",
        {"good": _skill("good"), "broken": "---\nname: broken\n---\n"},
    )
    assert item.status == "fail"
    assert item.summary == "a packaged skill does not validate"
    assert len(item.evidence) >= 1
    assert any("broken" in line for line in item.evidence)
    assert item.remedy == "Fix the named SKILL.md (frontmatter name, description, LF endings)."


def test_skills_packaged_fails_for_a_claude_skills_source_tree(tmp_path):
    files = {
        ".claude/skills/zed/SKILL.md": _skill("zed"),
        ".claude/skills/abc/SKILL.md": _skill("abc"),
        ".claude/skills/empty/notes.md": "x",
    }
    item = _audit(tmp_path, files=files)["skills-packaged"]
    assert item.status == "fail"
    assert item.summary == "skills live in a .claude/skills source tree"
    assert item.evidence == (
        ".claude/skills/abc/SKILL.md", ".claude/skills/zed/SKILL.md",
    )
    assert item.remedy == "Move them into package data and ship them with register_skills_verbs."


def test_skills_packaged_manual_when_not_registered(tmp_path):
    item = _audit(tmp_path)["skills-packaged"]
    assert item.status == "manual"
    assert item.summary == "no packaged skills registered; does this tool need skills?"
    assert item.remedy == (
        "If agents drive this tool, register_skills_verbs(registry, package=...)."
    )


def test_consumer_skills_group_is_never_executed_by_the_audit(tmp_path):
    body = '''
    def boom(*_):
        raise RuntimeError("the audit must not run consumer handlers")

    child = CliRegistry(
        CliIdentity("AUDIT", "1.0", "Audit Tool", command="audit-tool"),
        prog="audit-tool skills", description="Skills.", unexpected_exceptions="report",
    )
    for name in ("install", "uninstall", "check", "list"):
        child.register(VerbSpec(name, description=name, handler=boom))
    registry.register(VerbSpec("skills", description="skills", delegate=child.build()))
    registry.register(VerbSpec("own", description="own", handler=boom))
    '''
    item = _audit(tmp_path, body=body)["skills-packaged"]
    assert item.status == "manual"
    assert item.summary == "no packaged skills registered; does this tool need skills?"


def test_registered_cli_carries_the_skills_registration(tmp_path, monkeypatch):
    libs = tmp_path / "libs"
    libs.mkdir()
    name = f"{SKILL_PACKAGES}carry"
    _skill_package(libs, name, {"a": _skill("a")})
    monkeypatch.syspath_prepend(str(libs))
    registry = cli_extended.CliRegistry(
        cli_extended.CliIdentity("T", "1.0", "Tool", command="tool"), prog="tool",
        description="d",
    )
    registry.register(cli_extended.VerbSpec("x", description="x", handler=lambda *_: 0))
    assert registry.build().skills_package is None
    cli_extended.register_skills_verbs(registry, package=name)
    assert registry.build().skills_package == (name, "skills")


# ------------------------------------------------------------------------- doctor


def test_doctor_pass_and_manual(tmp_path):
    (tmp_path / "x").mkdir()
    manual = _audit(tmp_path / "x")
    assert manual["doctor"].status == "manual"
    assert manual["doctor"].summary == (
        "no doctor verb; does this tool have an environment to diagnose?"
    )
    assert manual["doctor"].remedy == (
        "If it has dependencies or services to verify, register_doctor(registry, checks)."
    )
    (tmp_path / "y").mkdir()
    body = '''
    register_doctor(registry, [
        DoctorCheck("c", "d", lambda runtime, args: CheckResult("ok", "fine")),
    ])
    '''
    ok = _audit(tmp_path / "y", body=body)["doctor"]
    assert (ok.status, ok.summary) == ("pass", "a doctor verb is registered")


# ------------------------------------------------------------------- pytest-plugin


def _plugin_project(tmp_path, monkeypatch, files):
    root = workflow_project(tmp_path, monkeypatch)
    for name, text in files.items():
        path = root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
    return _run_audit(root / "cli-extended.toml")["pytest-plugin"]


@pytest.mark.parametrize(
    ("files", "status", "evidence"),
    [
        ({"conftest.py": 'pytest_plugins = ["cli_extended.pytest_plugin"]\n'}, "pass", ("conftest.py",)),
        ({"pytest.ini": "[pytest]\naddopts = -p cli_extended.pytest_plugin\n"}, "pass", ("pytest.ini",)),
        ({"setup.cfg": "[tool:pytest]\naddopts = -p cli_extended.pytest_plugin\n"}, "pass", ("setup.cfg",)),
        (
            {"pyproject.toml": 'addopts = "-p cli_extended.pytest_plugin"\n'},
            "pass", ("pyproject.toml",),
        ),
        ({"notes.txt": "cli_extended.pytest_plugin\n"}, "fail", ()),
        ({".venv/c.py": "cli_extended.pytest_plugin\n"}, "fail", ()),
        ({"build/c.py": "cli_extended.pytest_plugin\n"}, "fail", ()),
        ({"c.py": "cli_extended.other\n"}, "fail", ()),
    ],
)
def test_pytest_plugin_cases(tmp_path, monkeypatch, files, status, evidence):
    item = _plugin_project(tmp_path, monkeypatch, files)
    assert item.status == status
    assert item.evidence == evidence
    if status == "pass":
        assert item.summary == "heuristic: cli_extended.pytest_plugin is referenced"
        assert item.checklist_id == "AC-22"


def test_pytest_plugin_manual_without_a_catalog(tmp_path):
    item = _audit(tmp_path)["pytest-plugin"]
    assert (item.status, item.summary) == (
        "manual", "heuristic: no review catalog configured",
    )


# ------------------------------------------------------------- dependency-declared


def _pyproject(dependencies=None, extra=""):
    body = "[project]\nname = 'tool'\nversion = '1'\n"
    if dependencies is not None:
        body += "dependencies = [" + ", ".join(repr(item) for item in dependencies) + "]\n"
    return body + extra


@pytest.mark.parametrize(
    ("pyproject", "status"),
    [
        (_pyproject(["cli-extended>=0.2.0"]), "pass"),
        (_pyproject(["other", "cli-extended[interactive] >= 0.2"]), "pass"),
        (_pyproject(["CLI-Extended>=1"]), "pass"),
        (_pyproject(["cli-extended==0.2.0"]), "fail"),
        (_pyproject(["cli-extended"]), "fail"),
        (_pyproject(["other>=1"]), "fail"),
        (_pyproject(["cli-extended-extras>=1"]), "fail"),
        (_pyproject(), "fail"),
    ],
)
def test_dependency_declared_cases(tmp_path, pyproject, status):
    item = _audit(tmp_path, files={"pyproject.toml": pyproject})["dependency-declared"]
    assert item.status == status
    if status == "pass":
        assert item.summary == "heuristic: pyproject declares cli-extended>="
    else:
        assert item.summary == "heuristic: [project].dependencies has no cli-extended>= entry"
        assert item.remedy == "Add 'cli-extended>=X.Y.Z' with a documented reason for the floor."


def test_dependency_declared_manual_for_standalone_scripts(tmp_path):
    item = _audit(tmp_path)["dependency-declared"]
    assert item.status == "manual"
    assert item.summary == "heuristic: no pyproject.toml; a standalone script"
    assert item.remedy == "scripts use the installed library (CX-D3)"
    assert item.checklist_id == "AC-24"


@pytest.mark.parametrize(
    "mapping",
    [
        '[tool.setuptools.package-dir]\ncli_extended = "../libraries/cli-extended/src/cli_extended"\n',
        '[tool.setuptools.package-dir]\n"" = "libraries/cli-extended/src"\n',
    ],
)
def test_dependency_declared_fails_on_package_dir_vendoring(tmp_path, mapping):
    text = _pyproject(["cli-extended>=0.2.0"], mapping)
    item = _audit(tmp_path, files={"pyproject.toml": text})["dependency-declared"]
    assert item.status == "fail"
    assert item.summary == "heuristic: package-dir vendors cli_extended"
    assert len(item.evidence) == 1 and item.evidence[0].startswith("package-dir ")
    assert item.remedy == "Depend on the released library: dependencies = ['cli-extended>=X.Y.Z']."


def test_dependency_declared_ignores_unrelated_package_dir(tmp_path):
    text = _pyproject(["cli-extended>=0.2.0"], '[tool.setuptools.package-dir]\n"" = "src"\n')
    item = _audit(tmp_path, files={"pyproject.toml": text})["dependency-declared"]
    assert item.status == "pass"


@pytest.mark.parametrize("content", [b"not = [valid", b"\xff\xfe\x00"])
def test_dependency_declared_fails_on_unreadable_pyproject(tmp_path, content):
    item = _audit(tmp_path, files={"pyproject.toml": content})["dependency-declared"]
    assert item.status == "fail"
    assert item.summary == "heuristic: pyproject.toml cannot be read"
    assert len(item.evidence) == 1
    assert item.remedy == "Fix pyproject.toml."


# ----------------------------------------------------------------------- path hacks


def test_no_path_hacks_cases(tmp_path):
    files = {
        "b.py": "sys.path.insert(0, '/x/libraries/cli-extended/src')\n",
        "a.toml": "PYTHONPATH = 'libraries/cli-extended/src'\n",
        "run-gate.toml": "PYTHONPATH = 'libraries/cli-extended/src'\n",
        "only_path.py": "libraries/cli-extended\n",
        "only_sys.py": "sys.path.insert(0, 'x')\n",
        "note.txt": "libraries/cli-extended sys.path\n",
        "sub/run-gate.toml": "libraries/cli-extended PYTHONPATH\n",
        "sub/deep.py": "libraries/cli-extended PYTHONPATH\n",
        "venv/x.py": "libraries/cli-extended sys.path\n",
        "node_modules/x.py": "libraries/cli-extended sys.path\n",
        "dist/x.py": "libraries/cli-extended sys.path\n",
    }
    item = _audit(tmp_path, files=files)["no-path-hacks"]
    assert item.status == "fail"
    assert item.summary == "heuristic: source paths point at the library checkout"
    assert item.evidence == ("a.toml", "b.py", "sub/deep.py")
    assert item.remedy == (
        "Install cli-extended as a dependency instead of putting its source on a path."
    )


def test_no_path_hacks_passes_when_only_the_gate_file_points_at_the_source(tmp_path):
    item = _audit(
        tmp_path, files={"run-gate.toml": "PYTHONPATH = 'libraries/cli-extended/src'\n"}
    )["no-path-hacks"]
    assert (item.status, item.summary, item.evidence) == (
        "pass", "heuristic: no sys.path/PYTHONPATH vendoring found", (),
    )


def test_run_audit_propagates_a_broken_factory(tmp_path):
    root = _setup(tmp_path)
    (root / "factory.py").write_text("def build_cli():\n    return 1\n", encoding="utf-8")
    project = load_project_config(root / "cli-extended.toml")
    with pytest.raises(ConfigError):
        run_audit(project.select(None), project)


# ------------------------------------------------------------------- O3: the verb

SYNTHETIC = [
    AuditItem("version-source", "AC-01", "pass", "ok"),
    AuditItem("synopsis-overrides", "AC-04", "warn", "careful", ("e1", "e2"), "do this"),
    AuditItem("shadowed-controls", "AC-05", "fail", "broken", ("bad",), None),
    AuditItem("doctor", "AC-20", "manual", "judge", (), "think"),
]


def _patched(monkeypatch, items):
    monkeypatch.setattr(cli_module, "run_audit", lambda cli, project: list(items))


def test_o3_text_rendering_is_exact(tmp_path, monkeypatch, capsys):
    workflow_project(tmp_path, monkeypatch)
    _patched(monkeypatch, SYNTHETIC)
    code, out, err = _run(capsys, "audit")
    assert out == (
        "[PASS] AC-01 version-source: ok\n"
        "[WARN] AC-04 synopsis-overrides: careful\n"
        "    evidence: e1\n"
        "    evidence: e2\n"
        "    remedy: do this\n"
        "[FAIL] AC-05 shadowed-controls: broken\n"
        "    evidence: bad\n"
        "[MANUAL] AC-20 doctor: judge\n"
        "    remedy: think\n"
        "audit: 1 pass, 1 warn, 1 fail, 1 manual\n"
    )
    assert err == ""
    assert code == 1


def test_o3_json_rendering_is_exact(tmp_path, monkeypatch, capsys):
    workflow_project(tmp_path, monkeypatch)
    _patched(monkeypatch, SYNTHETIC)
    code, out, err = _run(capsys, "audit", "--json")
    assert json.loads(out) == {
        "cli": "audit-tool",
        "items": [item.as_dict() for item in SYNTHETIC],
        "summary": {"pass": 1, "warn": 1, "fail": 1, "manual": 1},
    }
    assert out.count("\n") == 1
    assert code == 1


@pytest.mark.parametrize("statuses", [("pass", "warn", "manual"), ()])
def test_o3_exit_zero_without_failures(tmp_path, monkeypatch, capsys, statuses):
    workflow_project(tmp_path, monkeypatch)
    _patched(
        monkeypatch,
        [AuditItem(f"c{index}", "AC-01", status, "s") for index, status in enumerate(statuses)],
    )
    code, out, _err = _run(capsys, "audit")
    assert code == 0
    assert out.endswith(
        f"audit: {statuses.count('pass')} pass, {statuses.count('warn')} warn, "
        f"0 fail, {statuses.count('manual')} manual\n"
    )


def test_o3_real_audit_end_to_end(tmp_path, monkeypatch, capsys):
    workflow_project(tmp_path, monkeypatch)
    code, out, _err = _run(capsys, "audit")
    lines = out.splitlines()
    assert code == 1
    assert lines[0].startswith("[MANUAL] AC-01 version-source:")
    assert any(line.startswith("[FAIL] AC-05 shadowed-controls:") for line in lines)
    assert lines[-1].startswith("audit: ")
    code, out, _err = _run(capsys, "audit", "--json")
    payload = json.loads(out)
    assert code == 1
    assert [item["check"] for item in payload["items"]] == list(CHECKLIST_IDS)
    assert sum(payload["summary"].values()) == len(CHECKLIST_IDS)


def test_o3_selects_cli_and_config_and_rejects_bad_input(tmp_path, monkeypatch, capsys):
    root = workflow_project(tmp_path, monkeypatch)
    _patched(monkeypatch, SYNTHETIC[:1])
    explicit = str(root / "cli-extended.toml")
    assert _run(capsys, "audit", "--config", explicit, "--cli", "audit-tool")[0] == 0
    code, out, err = _run(capsys, "audit", "--cli", "nope")
    assert (code, out) == (2, "")
    assert "unknown CLI 'nope'" in err
    code, out, err = _run(capsys, "audit", "--config", str(tmp_path / "missing.toml"))
    assert (code, out) == (2, "")
    assert "missing.toml" in err


def test_o3_factory_import_errors_exit_two(tmp_path, monkeypatch, capsys):
    root = workflow_project(tmp_path, monkeypatch)
    (root / "factory.py").write_text("import not_a_real_module_xyz\n", encoding="utf-8")
    code, out, err = _run(capsys, "audit")
    assert (code, out) == (2, "")
    assert "not_a_real_module_xyz" in err


def test_o3_audit_is_read_only_and_listed_in_help(tmp_path, monkeypatch, capsys):
    root = workflow_project(tmp_path, monkeypatch)
    before = sorted((path.name, path.read_bytes()) for path in root.iterdir() if path.is_file())
    _run(capsys, "audit")
    after = sorted((path.name, path.read_bytes()) for path in root.iterdir() if path.is_file())
    assert before == after
    code, out, _err = _run(capsys, "help", "audit")
    assert code == 0
    assert "--config" in out and "--cli" in out and "--json" in out
    assert "adoption checklist" in out


# ------------------------------------------------------------------- O4: the skill


def test_o4_adoption_skill_validates_and_is_listed(tmp_path, capsys):
    root = resources.files("cli_extended").joinpath("skills", "cli-extended-adoption")
    files = {child.name: child.read_bytes() for child in root.iterdir()}
    assert set(files) == {"SKILL.md"}
    validate_skill_source("cli-extended-adoption", files)
    text = files["SKILL.md"].decode("utf-8")
    for fragment in (
        "cli-extended audit --json", 'category = "adoption"', "cli-extended-review",
        "`wontfix`", "rationale",
    ):
        assert fragment in text
    for check in CHECKLIST_IDS:
        if check in {
            "version-source", "synopsis-overrides", "configure-callbacks", "hidden-options",
            "mutation-safety", "skills-packaged", "doctor", "pytest-plugin",
            "dependency-declared", "surface-check",
        }:
            assert f"`{check}`" in text, f"manual criteria for {check} missing"
    destination = tmp_path / "dest"
    destination.mkdir()
    code = cli_module.main(["skills", "list", "--dest", str(destination)])
    out = capsys.readouterr().out
    assert code == 0
    assert "cli-extended-adoption" in out
    assert "cli-extended-review" in out


# ------------------------------------------------- review round 1


def _nested_body():
    return '''
    def make(name, child=None):
        registry_ = CliRegistry(
            CliIdentity("AUDIT", "1.0", "Audit Tool", command="audit-tool"),
            prog="audit-tool " + name, description=name, unexpected_exceptions="report",
        )
        if child is None:
            registry_.register(VerbSpec(
                "leaf", description="leaf", handler=lambda *_: 0, include_json=False,
                options=(OptionSpec(("--json",), "json"),),
            ))
        else:
            registry_.register(VerbSpec(
                child[0], description=child[0], delegate=child[1], include_progress=False,
            ))
        return registry_.build()

    level3 = make("level2")
    level2 = make("level1", ("level2", level3))
    registry.register(VerbSpec(
        "level1", description="level1", delegate=level2, include_progress=False,
    ))
    '''


def test_depth_three_delegates_report_the_full_prefix(tmp_path):
    item = _audit(tmp_path, body=_nested_body())["shadowed-controls"]
    assert item.evidence == (
        "level1 level2 leaf: --json shadows a library control; "
        "use the library --json (VerbSpec include_json)",
    )


def test_audit_verb_refuses_progress(tmp_path, monkeypatch, capsys):
    workflow_project(tmp_path, monkeypatch)
    code, out, err = _run(capsys, "audit", "--progress", "plain")
    assert code == 2
    assert out == ""
    assert "--progress" in err


@pytest.mark.skipif(not hasattr(os, "symlink"), reason="needs symlinks")
def test_dangling_symlink_is_listed_not_fatal(tmp_path):
    files = {"a.py": "CliIdentity.resolve(name='x')\n"}
    root = _setup(tmp_path, files=files)
    (root / "gone.py").symlink_to(root / "missing-target.py")
    items = _run_audit(root / "cli-extended.toml")
    version = items["version-source"]
    assert version.status == "manual"
    assert version.summary == (
        "heuristic: CliIdentity.resolve( is used; not fully verified, 1 unreadable file(s)"
    )
    assert version.evidence == ("unreadable: gone.py",)
    assert version.remedy == "Make the unreadable files readable, then re-run the audit."
    assert items["no-path-hacks"].status == "manual"
    assert items["no-path-hacks"].evidence == ("unreadable: gone.py",)


@pytest.mark.skipif(os.geteuid() == 0, reason="root ignores file permissions")
def test_unreadable_file_keeps_fail_and_lists_evidence(tmp_path):
    files = {
        "bad.py": "CliIdentity(\nimportlib.metadata\n",
        "secret.py": "x = 1\n",
    }
    root = _setup(tmp_path, files=files)
    (root / "secret.py").chmod(0)
    try:
        items = _run_audit(root / "cli-extended.toml")
    finally:
        (root / "secret.py").chmod(0o644)
    assert items["version-source"].status == "fail"
    assert items["version-source"].evidence == ("bad.py", "unreadable: secret.py")
    assert items["no-path-hacks"].status == "manual"


def test_unreadable_files_do_not_hide_a_plugin_failure(tmp_path, monkeypatch):
    root = workflow_project(tmp_path, monkeypatch)
    (root / "gone.py").symlink_to(root / "missing-target.py")
    item = _run_audit(root / "cli-extended.toml")["pytest-plugin"]
    assert item.status == "fail"
    assert item.evidence == ("unreadable: gone.py",)
    (root / "conftest.py").write_text("cli_extended.pytest_plugin\n", encoding="utf-8")
    item = _run_audit(root / "cli-extended.toml")["pytest-plugin"]
    assert item.status == "manual"
    assert item.evidence == ("conftest.py", "unreadable: gone.py")


def test_tool_caches_and_egg_info_are_not_scanned(tmp_path):
    files = {
        ".tox/py/lib/a.py": "CliIdentity(\nimportlib.metadata\nlibraries/cli-extended sys.path\n",
        ".nox/s/a.py": "libraries/cli-extended sys.path\n",
        ".eggs/a.py": "libraries/cli-extended sys.path\n",
        ".mypy_cache/a.py": "libraries/cli-extended sys.path\n",
        ".pytest_cache/a.py": "libraries/cli-extended sys.path\n",
        ".ruff_cache/a.py": "libraries/cli-extended sys.path\n",
        "lib/site-packages/a.py": "libraries/cli-extended sys.path\n",
        "tool.egg-info/a.py": "libraries/cli-extended sys.path\n",
        "keep.egg_info_not/a.py": "x = 1\n",
    }
    items = _audit(tmp_path, files=files)
    assert items["no-path-hacks"].status == "pass"
    assert items["version-source"].status == "manual"
    (tmp_path / "second").mkdir()
    visible = _audit(
        tmp_path / "second", files={"egg-info/a.py": "libraries/cli-extended sys.path\n"}
    )
    assert visible["no-path-hacks"].status == "fail"


def test_ac05_row_lists_exactly_the_shadowed_flags():
    row = next(row for row in _rows() if row[0] == "AC-05")
    assert set(re.findall(r"`(--[a-z-]+)`", row[1])) == set(SHADOWED_REPLACEMENTS)
