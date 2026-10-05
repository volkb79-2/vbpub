"""W3b: findings integration, the cli-extended console script and its reports."""

from __future__ import annotations

import io
import json
import subprocess
import sys
import tomllib
import types
from importlib.metadata import PackageNotFoundError
from pathlib import Path

import pytest
from test_review import (
    INTERACTIONS,
    ROUTE_ID,
    _build_cli,
    _make_files,
    _write_catalog,
)

import cli_extended
import cli_extended.cli as cli_module
import cli_extended.identity as identity_module
from cli_extended import (
    CliIdentity,
    CliRegistry,
    ReviewCatalogError,
    SurfaceReport,
    VerbSpec,
    assert_cli_contract,
    check_cli_surface,
    export_cli_surface,
    load_cli_review_catalog,
    render_cli_surface_markdown,
    sync_cli_surface,
    validate_skill_source,
)
from cli_extended.cli import _case_toml, _fence, render_pack, render_report
from cli_extended.findings import FindingsError, load_review_findings
from cli_extended.parser import fixed_help_width
from cli_extended.review import SURFACE_END_MARKER, SURFACE_START_MARKER

SRC = Path(cli_extended.__file__).resolve().parent.parent
FACTORY_NAME = "_cli_extended_surface_factory"
FACTORY = '''\
from pathlib import Path

from cli_extended import CliIdentity, CliRegistry, OptionSpec, VerbSpec

DESCRIPTION = Path(__file__).with_name("desc.txt").read_text(encoding="utf-8").strip()


def build_cli():
    registry = CliRegistry(
        CliIdentity("AUDIT", "1.0", "Audit Tool", command="audit-tool"),
        prog="audit-tool",
        description="Audit resources.",
    )
    registry.register(
        VerbSpec(
            "inspect",
            description=DESCRIPTION,
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
'''
CONFIG = """\
schema_version = 1

[[clis]]
id = "audit-tool"
factory = "factory.py:build_cli"
review = "cli-review.toml"
manifest = "cli-surface.json"
spec = "CLI-SPEC.md"
"""


@pytest.fixture(autouse=True)
def _isolated(tmp_path, monkeypatch):
    monkeypatch.setattr(sys, "path", list(sys.path))
    monkeypatch.setitem(sys.modules, FACTORY_NAME, types.ModuleType(FACTORY_NAME))
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(tmp_path / "home" / "claude"))
    monkeypatch.setattr(identity_module, "installed_version", lambda _name: "0.1.0")


def _project(tmp_path, monkeypatch, *, config=CONFIG, catalog=True):
    root = tmp_path / "proj"
    root.mkdir()
    (root / "desc.txt").write_text("inspect one resource\n", encoding="utf-8")
    (root / "factory.py").write_text(FACTORY, encoding="utf-8")
    app = _build_cli()
    surface = export_cli_surface(app, interaction_groups=INTERACTIONS)
    if catalog:
        _write_catalog(root / "cli-review.toml", surface)
    else:
        (root / "cli-review.toml").write_text(
            'schema_version = 1\ncli_id = "audit-tool"\n', encoding="utf-8"
        )
    (root / "CLI-SPEC.md").write_text(
        f"# Spec\n\n{SURFACE_START_MARKER}\nold\n{SURFACE_END_MARKER}\n", encoding="utf-8"
    )
    (root / "cli-extended.toml").write_text(config, encoding="utf-8")
    monkeypatch.chdir(root)
    return root


def _run(capsys, *argv):
    code = cli_module.main(list(argv))
    captured = capsys.readouterr()
    return code, captured.out, captured.err


def _finding(identifier, severity, *, status="open", route="", extra=""):
    text = (
        f'[[findings]]\nid = "{identifier}"\nstatus = "{status}"\nseverity = "{severity}"\n'
        f'category = "help"\nsummary = "S {identifier}"\n'
    )
    if status == "open":
        text += f'remedy = "R {identifier}"\n'
    if route:
        text += f'route = "{route}"\n'
    return text + extra


def _findings_file(path, *entries, cli_id="audit-tool"):
    path.write_text(
        f'schema_version = 1\ncli_id = "{cli_id}"\n' + "".join(entries), encoding="utf-8"
    )
    return path


# ---- O3: findings in the review functions -------------------------------


def test_o3_open_blocker_and_major_fail_check_and_minor_note_do_not(tmp_path):
    app, _surface, review, manifest, spec = _make_files(tmp_path)
    findings = _findings_file(
        tmp_path / "findings.toml",
        _finding("m1", "major", route=ROUTE_ID),
        _finding("b1", "blocker"),
        _finding("n1", "note"),
        _finding("i1", "minor"),
        _finding("done", "blocker", status="fixed"),
    )
    kwargs = dict(review_path=review, manifest_path=manifest, spec_path=spec)
    synced = sync_cli_surface(app, findings_path=findings, **kwargs)
    assert synced.findings == (
        "open blocker finding b1: S b1",
        "open major finding m1: S m1",
    )
    assert synced.notes == (
        "open minor finding i1: S i1",
        "open note finding n1: S n1",
    )
    checked = check_cli_surface(app, findings_path=findings, **kwargs)
    assert checked == synced
    assert checked.passed is False

    only_minor = _findings_file(
        tmp_path / "minor.toml", _finding("i1", "minor"), _finding("n1", "note")
    )
    sync_cli_surface(app, findings_path=only_minor, **kwargs)
    report = check_cli_surface(app, findings_path=only_minor, **kwargs)
    assert report.passed is True
    assert report.findings == ()
    assert report.notes == ("open minor finding i1: S i1", "open note finding n1: S n1")
    sync_cli_surface(app, **kwargs)
    assert check_cli_surface(app, **kwargs) == SurfaceReport()


def test_o3_stale_route_findings_fail_for_any_status(tmp_path):
    app, _surface, review, manifest, spec = _make_files(tmp_path)
    findings = _findings_file(
        tmp_path / "findings.toml",
        _finding("zz", "note", status="fixed", route="route:entrypoint:audit-tool/gone"),
        _finding("aa", "minor", route="route:entrypoint:audit-tool/also-gone"),
        _finding("ok", "note", status="fixed", route=ROUTE_ID),
    )
    report = sync_cli_surface(
        app, review_path=review, manifest_path=manifest, spec_path=spec,
        findings_path=findings,
    )
    assert report.findings == (
        "stale finding aa: route route:entrypoint:audit-tool/also-gone no longer exists",
        "stale finding zz: route route:entrypoint:audit-tool/gone no longer exists",
    )
    assert report.notes == ("open minor finding aa: S aa",)


def test_o3_findings_cli_id_must_match_and_app_must_be_a_cli(tmp_path):
    app, _surface, review, manifest, spec = _make_files(tmp_path)
    kwargs = dict(review_path=review, manifest_path=manifest, spec_path=spec)
    other = _findings_file(tmp_path / "other.toml", cli_id="other-tool")
    with pytest.raises(
        FindingsError, match="'other-tool' does not match registered executable 'audit-tool'"
    ):
        check_cli_surface(app, findings_path=other, **kwargs)
    with pytest.raises(TypeError, match="must be a RegisteredCli"):
        sync_cli_surface(object(), findings_path=other, **kwargs)
    with pytest.raises(OSError):
        sync_cli_surface(app, findings_path=tmp_path / "missing.toml", **kwargs)


def _region(spec_path):
    text = spec_path.read_bytes().decode("utf-8").replace("\r\n", "\n")
    return text.split(SURFACE_START_MARKER)[1].split(SURFACE_END_MARKER)[0]


def test_o3_sync_renders_the_findings_section_deterministically(tmp_path):
    app, _surface, review, manifest, spec = _make_files(tmp_path)
    kwargs = dict(review_path=review, manifest_path=manifest, spec_path=spec)
    findings = _findings_file(
        tmp_path / "findings.toml",
        _finding("z", "major", route=ROUTE_ID),
        _finding("a", "note"),
        _finding("b", "blocker"),
        _finding("c", "note", status="fixed"),
        _finding("multi", "minor"),
    )
    sync_cli_surface(app, findings_path=findings, **kwargs)
    first = spec.read_bytes()
    section = _region(spec).split("### Open review findings\n")[1]
    assert section == (
        "\n"
        "- **blocker** `b` (route: none): S b Remedy: R b\n"
        f"- **major** `z` (route: {ROUTE_ID}): S z Remedy: R z\n"
        "- **minor** `multi` (route: none): S multi Remedy: R multi\n"
        "- **note** `a` (route: none): S a Remedy: R a\n"
    )
    sync_cli_surface(app, findings_path=findings, **kwargs)
    assert spec.read_bytes() == first


def test_o3_section_says_none_when_nothing_is_open_and_is_omitted_without_a_file(tmp_path):
    app, _surface, review, manifest, spec = _make_files(tmp_path)
    kwargs = dict(review_path=review, manifest_path=manifest, spec_path=spec)
    closed = _findings_file(tmp_path / "findings.toml", _finding("x", "major", status="fixed"))
    sync_cli_surface(app, findings_path=closed, **kwargs)
    assert _region(spec).endswith("### Open review findings\n\nNone.\n")
    sync_cli_surface(app, **kwargs)
    assert "Open review findings" not in _region(spec)
    surface = export_cli_surface(app, interaction_groups=INTERACTIONS)
    catalog = load_cli_review_catalog(review)
    assert render_cli_surface_markdown(surface, catalog) == render_cli_surface_markdown(
        surface, catalog, None
    )


def test_o3_spec_goes_stale_when_findings_change(tmp_path):
    app, _surface, review, manifest, spec = _make_files(tmp_path)
    kwargs = dict(review_path=review, manifest_path=manifest, spec_path=spec)
    findings = _findings_file(tmp_path / "findings.toml", _finding("x", "minor"))
    sync_cli_surface(app, findings_path=findings, **kwargs)
    assert check_cli_surface(app, findings_path=findings, **kwargs).passed
    _findings_file(findings, _finding("y", "minor"))
    stale = check_cli_surface(app, findings_path=findings, **kwargs)
    assert stale.findings == ("generated CLI spec block is stale",)


# ---- O4: the console script end to end ------------------------------------


def test_o4_surface_check_end_to_end_then_stale_after_a_help_edit(tmp_path, monkeypatch, capsys):
    root = _project(tmp_path, monkeypatch)
    code, out, err = _run(capsys, "surface", "sync")
    assert (code, out.strip(), err) == (0, "CLI surface files synchronized.", "")
    assert (root / "cli-surface.json").is_file()
    assert "inspect one resource" in (root / "CLI-SPEC.md").read_text(encoding="utf-8")
    assert _run(capsys, "surface", "check") == (0, "CLI surface check passed.\n", "")

    (root / "desc.txt").write_text("inspect another resource\n", encoding="utf-8")
    code, out, err = _run(capsys, "surface", "check")
    assert code == 1
    assert out == ""
    lines = err.splitlines()
    assert "[REVIEW] generated CLI manifest is stale" in lines
    assert "[REVIEW] generated CLI spec block is stale" in lines
    assert lines[-1] == "CLI surface check failed."
    assert all(line.startswith(("[REVIEW]", "CLI surface")) for line in lines)


def test_o4_bad_or_missing_config_is_a_usage_level_refusal(tmp_path, monkeypatch, capsys):
    root = _project(tmp_path, monkeypatch)
    (root / "cli-extended.toml").write_text("schema_version = 9\n", encoding="utf-8")
    code, out, err = _run(capsys, "surface", "check")
    assert (code, out) == (2, "")
    assert err.startswith("[ERROR] ")
    assert "schema_version" in err

    empty = tmp_path / "empty"
    empty.mkdir()
    code, _out, err = _run(capsys, "surface", "sync", "--config", str(empty / "none.toml"))
    assert code == 2
    assert "cannot read" in err

    (root / "cli-extended.toml").unlink()
    code, _out, err = _run(capsys, "surface", "report")
    assert code == 2
    assert "searched:" in err


def test_o4_load_errors_exit_two(tmp_path, monkeypatch, capsys):
    root = _project(tmp_path, monkeypatch)
    broken = CONFIG.replace("factory.py:build_cli", "no_such_module_for_w3b:build")
    (root / "cli-extended.toml").write_text(broken, encoding="utf-8")
    code, _out, err = _run(capsys, "surface", "check")
    assert code == 2
    assert "no_such_module_for_w3b" in err

    (root / "cli-extended.toml").write_text(
        CONFIG.replace("cli-review.toml", "absent-review.toml"), encoding="utf-8"
    )
    code, _out, err = _run(capsys, "surface", "check")
    assert code == 2
    assert "absent-review.toml" in err

    (root / "cli-extended.toml").write_text(
        CONFIG.replace('id = "audit-tool"', 'id = "other"'), encoding="utf-8"
    )
    code, _out, err = _run(capsys, "surface", "check")
    assert code == 2
    assert "'other' does not match the registered executable 'audit-tool'" in err


def test_o4_unconfigured_paths_are_named(tmp_path, monkeypatch, capsys):
    root = _project(tmp_path, monkeypatch)
    only_review = 'schema_version = 1\n[[clis]]\nid = "audit-tool"\nfactory = "factory.py:build_cli"\nreview = "cli-review.toml"\n'
    (root / "cli-extended.toml").write_text(only_review, encoding="utf-8")
    for verb in ("sync", "check"):
        code, _out, err = _run(capsys, "surface", verb)
        assert code == 2
        assert f"must configure manifest, spec for 'surface {verb}'" in err
    for verb in ("template", "pack", "report"):
        code, _out, err = _run(capsys, "surface", verb)
        assert code == 0, verb
    (root / "cli-extended.toml").write_text(
        'schema_version = 1\n[[clis]]\nid = "audit-tool"\nfactory = "factory.py:build_cli"\n',
        encoding="utf-8",
    )
    for verb in ("template", "pack", "report"):
        code, _out, err = _run(capsys, "surface", verb)
        assert code == 2
        assert f"must configure review for 'surface {verb}'" in err


def test_o4_cli_selection_with_several_clis(tmp_path, monkeypatch, capsys):
    root = _project(tmp_path, monkeypatch)
    two = CONFIG + '\n[[clis]]\nid = "second"\nfactory = "factory.py:build_cli"\n'
    (root / "cli-extended.toml").write_text(two, encoding="utf-8")
    code, _out, err = _run(capsys, "surface", "report")
    assert code == 2
    assert "several CLIs (audit-tool, second); pass --cli ID" in err
    code, _out, err = _run(capsys, "surface", "report", "--cli", "nope")
    assert code == 2
    assert "unknown CLI 'nope'" in err
    code, out, _err = _run(capsys, "surface", "report", "--cli", "audit-tool")
    assert code == 0
    assert out.startswith("# CLI review report: audit-tool\n")


def test_o4_max_candidates_is_a_positive_integer_and_a_limit(tmp_path, monkeypatch, capsys):
    _project(tmp_path, monkeypatch)
    for bad in ("0", "-3", "many"):
        code, _out, err = _run(capsys, "surface", "sync", "--max-candidates", bad)
        assert code == 2, bad
    code, _out, err = _run(capsys, "surface", "sync", "--max-candidates", "1")
    assert code == 2
    assert "[ERROR]" in err
    code, out, _err = _run(capsys, "surface", "sync", "--max-candidates", "500")
    assert (code, out.strip()) == (0, "CLI surface files synchronized.")


def test_o4_findings_flow_through_the_script(tmp_path, monkeypatch, capsys):
    root = _project(tmp_path, monkeypatch)
    configured = CONFIG + 'findings = "findings.toml"\n'
    (root / "cli-extended.toml").write_text(configured, encoding="utf-8")
    _findings_file(root / "findings.toml", _finding("n1", "minor"))
    assert _run(capsys, "surface", "sync")[0] == 0
    code, out, err = _run(capsys, "surface", "check")
    assert (code, out) == (0, "CLI surface check passed.\n")
    assert err == "[NOTE] open minor finding n1: S n1\n"

    _findings_file(root / "findings.toml", _finding("b1", "blocker"))
    code, _out, err = _run(capsys, "surface", "sync")
    assert code == 0
    assert "[REVIEW] open blocker finding b1: S b1" in err.splitlines()
    code, _out, err = _run(capsys, "surface", "check")
    assert code == 1
    assert "[REVIEW] open blocker finding b1: S b1" in err.splitlines()

    _findings_file(root / "findings.toml", cli_id="wrong")
    code, _out, err = _run(capsys, "surface", "check")
    assert code == 2
    assert "findings file cli_id 'wrong'" in err


def test_o4_template_prints_rows_or_says_none_needed(tmp_path, monkeypatch, capsys):
    root = _project(tmp_path, monkeypatch)
    assert _run(capsys, "surface", "template") == (
        0, "No semantic review rows need adding or updating.\n", "",
    )
    (root / "cli-review.toml").write_text(
        'schema_version = 1\ncli_id = "audit-tool"\n', encoding="utf-8"
    )
    code, out, _err = _run(capsys, "surface", "template", "--max-candidates", "200")
    assert code == 0
    assert out.startswith("# Candidate kind: ")
    assert "[[cases]]" in out
    (root / "cli-review.toml").write_text(
        'schema_version = 1\ncli_id = "wrong"\n', encoding="utf-8"
    )
    code, _out, err = _run(capsys, "surface", "template")
    assert code == 2
    assert "does not match registered executable" in err


# ---- O6: report ----------------------------------------------------------


def _surface_and_catalog(tmp_path, mutate=None, extra_case=None):
    app = _build_cli()
    surface = export_cli_surface(app, interaction_groups=INTERACTIONS)
    path = tmp_path / "catalog.toml"
    _write_catalog(path, surface, extra_case=extra_case)
    if mutate is not None:
        path.write_text(mutate(path.read_text(encoding="utf-8")), encoding="utf-8")
    return app, surface, load_cli_review_catalog(path)


def test_o6_report_says_none_for_every_empty_section(tmp_path):
    _app, surface, catalog = _surface_and_catalog(tmp_path)
    assert render_report("audit-tool", surface, catalog, None) == (
        "# CLI review report: audit-tool\n\n"
        "## Open findings\n\nNone.\n\n"
        "## Cases awaiting review\n\nNone.\n\n"
        "## Stale cases\n\nNone.\n\n"
        "## Incomplete syntax\n\nNone.\n"
    )


def _split_cases(text):
    head, *blocks = text.split("[[cases]]\n")
    return head, blocks


def _rejoin(head, blocks):
    return head + "".join("[[cases]]\n" + block for block in blocks)


def test_o6_report_lists_each_kind_of_awaiting_case_stale_cases_and_gaps(tmp_path):
    def mutate(text):
        head, blocks = _split_cases(text)
        blocks[0] = blocks[0].replace('state = "active"', 'state = "pending"', 1)
        blocks[1] = blocks[1].replace("reviewed_signature = ", 'reviewed_signature = "sha256:old" #', 1)
        blocks[2] = blocks[2].replace(
            'state = "active"', 'state = "retired"\nretirement_reason = "was removed"', 1
        )
        del blocks[3]
        return _rejoin(head, blocks)

    extra = [
        "[[cases]]",
        'id = "case:gone"',
        'state = "pending"',
        "",
        "[[cases]]",
        'id = "case:retired-gone"',
        'state = "retired"',
        'retirement_reason = "removed"',
    ]
    _app, surface, catalog = _surface_and_catalog(tmp_path, mutate, extra)
    ids = [candidate["id"] for candidate in surface["candidates"]]
    findings = load_review_findings(
        _findings_file(
            tmp_path / "f.toml",
            _finding("z", "minor"),
            _finding("a", "blocker"),
            _finding("c", "note", status="fixed"),
        )
    )
    text = render_report(
        "audit-tool", {**surface, "incomplete": ["verb x uses configure"]}, catalog, findings
    )
    kinds = {candidate["id"]: candidate["kind"] for candidate in surface["candidates"]}
    assert text == (
        "# CLI review report: audit-tool\n\n"
        "## Open findings\n\n"
        "- **blocker** `a` [help] (route: none): S a Remedy: R a\n"
        "- **minor** `z` [help] (route: none): S z Remedy: R z\n\n"
        "## Cases awaiting review\n\n"
        f"- `{ids[0]}` ({kinds[ids[0]]}): pending\n"
        f"- `{ids[1]}` ({kinds[ids[1]]}): changed\n"
        f"- `{ids[2]}` ({kinds[ids[2]]}): reappeared\n"
        f"- `{ids[3]}` ({kinds[ids[3]]}): new\n\n"
        "## Stale cases\n\n"
        "- `case:gone`: no longer generated; retire or repair\n\n"
        "## Incomplete syntax\n\n"
        "- verb x uses configure\n"
    )


def test_o6_report_command_prints_the_report(tmp_path, monkeypatch, capsys):
    _project(tmp_path, monkeypatch, catalog=False)
    code, out, err = _run(capsys, "surface", "report")
    assert (code, err) == (0, "")
    assert out.startswith("# CLI review report: audit-tool\n\n## Open findings\n\nNone.\n\n## Cases awaiting review\n\n- `")
    assert out.index("## Open findings") < out.index("## Cases awaiting review")
    assert out.index("## Cases awaiting review") < out.index("## Stale cases")
    assert out.index("## Stale cases") < out.index("## Incomplete syntax")
    assert ": new\n" in out


def test_o6_report_tolerates_broken_interaction_references(tmp_path, monkeypatch, capsys):
    root = _project(tmp_path, monkeypatch)
    text = (root / "cli-review.toml").read_text(encoding="utf-8")
    (root / "cli-review.toml").write_text(
        text.replace(f'route_id = "{ROUTE_ID}"', 'route_id = "route:entrypoint:audit-tool/gone"'),
        encoding="utf-8",
    )
    code, out, _err = _run(capsys, "surface", "report")
    assert code == 0
    assert "## Stale cases" in out


def test_w8b_template_refuses_broken_interaction_references(tmp_path, monkeypatch, capsys):
    root = _project(tmp_path, monkeypatch)
    text = (root / "cli-review.toml").read_text(encoding="utf-8")
    broken = text.replace(
        f'route_id = "{ROUTE_ID}"', 'route_id = "route:entrypoint:audit-tool/gone"'
    )
    assert broken != text
    (root / "cli-review.toml").write_text(broken, encoding="utf-8")
    code, out, err = _run(capsys, "surface", "template")
    assert (code, out) == (2, "")
    assert "[ERROR]" in err
    assert "interaction group 'mode-and-dry-run/both' names an unknown route" in err
    # the tolerant views of the same catalog still succeed
    assert _run(capsys, "surface", "report")[0] == 0


# ---- O5: pack --------------------------------------------------------------


def _rubric():
    return (SRC / "cli_extended" / "review_rubric.md").read_text(encoding="utf-8")


def test_o5_pack_contains_rubric_help_for_every_route_and_each_pending_case(tmp_path, monkeypatch, capsys):
    _project(tmp_path, monkeypatch, catalog=False)
    code, first, err = _run(capsys, "surface", "pack")
    assert (code, err) == (0, "")
    assert first.startswith(_rubric().rstrip("\n") + "\n\n## CLI\n")
    app = _build_cli()
    surface = export_cli_surface(app)
    assert "- Identity: AUDIT 1.0 — Audit Tool\n" in first
    assert "- Executable: `audit-tool`\n" in first
    assert "- cli-extended contract version: 1\n" in first or "- cli-extended contract version: " in first
    assert f"- Surface schema version: {surface['schema_version']}\n" in first
    for argv in (["help"], ["help", "inspect"]):
        out = io.StringIO()
        with fixed_help_width(100):
            assert app.run(argv=argv, stdout=out, stderr=io.StringIO()) == 0
        assert "```text\n" + out.getvalue().rstrip("\n") + "\n```" in first
    for candidate in surface["candidates"]:
        assert f"### `{candidate['id']}`\n" in first
        assert candidate["signature"] in first
    assert first.count("Current catalog row:\n\n(none)") == len(surface["candidates"])
    assert "## Open findings\n\nNone.\n\n## Your task\n" in first
    assert first.endswith("The library never rewrites the catalog or the findings file; only you do.\n")
    assert "### root\n" in first and f"### `{ROUTE_ID}`\n" in first
    code, second, _err = _run(capsys, "surface", "pack")
    assert second == first


def test_o5_output_flag_writes_the_same_bytes_and_nothing_to_stdout(tmp_path, monkeypatch, capsys):
    root = _project(tmp_path, monkeypatch, catalog=False)
    _code, printed, _err = _run(capsys, "surface", "pack")
    target = tmp_path / "bundle.md"
    code, out, err = _run(capsys, "surface", "pack", "--output", str(target))
    assert (code, out, err) == (0, "", "")
    assert target.read_bytes() == printed.encode("utf-8")
    code, _out, err = _run(capsys, "surface", "pack", "--output", str(root))
    assert code == 2
    assert "[ERROR]" in err


def test_o5_pack_shows_catalog_rows_stale_cases_and_open_findings(tmp_path):
    def mutate(text):
        head, blocks = _split_cases(text)
        blocks[0] = blocks[0].replace('state = "active"', 'state = "pending"', 1)
        return _rejoin(head, blocks)

    extra = ["[[cases]]", 'id = "case:gone"', 'state = "pending"', 'rationale = "was here"']
    app, surface, catalog = _surface_and_catalog(tmp_path, mutate, extra)
    findings = load_review_findings(_findings_file(tmp_path / "f.toml", _finding("a", "major")))
    text = render_pack(app, surface, catalog, findings)
    first_id = surface["candidates"][0]["id"]
    section = text.split(f"### `{first_id}`\n")[1].split("\n### ")[0]
    assert "- status: pending\n" in section
    assert "```json\n" in section
    shape = json.loads(section.split("```json\n")[1].split("\n```")[0])
    assert shape["signature"] == surface["candidates"][0]["signature"]
    assert shape["route_id"] == surface["candidates"][0]["route_id"]
    row = tomllib.loads(section.split("```toml\n")[1].split("\n```")[0])
    assert row["id"] == first_id and row["state"] == "pending"
    stale = text.split("### `case:gone`\n")[1].split("\n## ")[0]
    assert "- kind: stale\n" in stale
    assert 'rationale = "was here"' in stale
    assert "## Open findings\n\n- **major** `a` [help] (route: none): S a Remedy: R a\n" in text


def test_o5_pack_with_no_cases_to_review_says_none(tmp_path):
    app, surface, catalog = _surface_and_catalog(tmp_path)
    text = render_pack(app, surface, catalog, None)
    assert "## Cases to review\n\nNone.\n\n## Open findings\n" in text


def test_o5_pack_of_a_single_command_cli_has_only_the_root_help_block():
    registry = CliRegistry(
        CliIdentity("ONE", "1.0", "One", command="one-tool"), prog="one-tool",
        description="One.", single_command=True,
    )
    registry.register(VerbSpec("only", description="only", handler=lambda *_: 0))
    app = registry.build()
    surface = export_cli_surface(app)
    catalog = types.SimpleNamespace(
        cases=(), cases_by_id={}, max_candidates=128, interaction_groups=()
    )
    text = render_pack(app, surface, catalog, None)
    assert text.count("### root") == 1
    assert "### `route:" not in text


def test_fences_grow_past_any_backtick_run_in_the_content():
    assert _fence("plain", "text") == ["```text", "plain", "```"]
    assert _fence("has ``` inside\n", "text") == ["````text", "has ``` inside", "````"]
    assert _fence("a ````` b", "") == ["``````", "a ````` b", "``````"]


def test_case_rows_render_as_toml_that_round_trips(tmp_path):
    path = tmp_path / "catalog.toml"
    path.write_text(
        'schema_version = 1\ncli_id = "audit-tool"\n'
        '[[cases]]\nid = "full"\nstate = "active"\ndecision = "refuse"\n'
        'reviewed_signature = "sha256:abc"\nrationale = "why \\"quoted\\" é"\n'
        'invocation = ["inspect", "--mode", "x"]\nexpected_exit_status = 2\n'
        'expected_stdout_contains = "out"\nexpected_stderr_contains = "err"\n'
        'effects = ["writes a file"]\ntest_ids = ["tests/t.py::a"]\n'
        '[[cases]]\nid = "empty-lists"\nstate = "active"\ndecision = "accept"\n'
        'reviewed_signature = "s"\nrationale = "r"\ninvocation = []\n'
        'expected_exit_status = 0\neffects = []\ntest_ids = ["x"]\n'
        '[[cases]]\nid = "gone"\nstate = "retired"\nretirement_reason = "removed"\n'
        '[[cases]]\nid = "bare"\n',
        encoding="utf-8",
    )
    full, empty_lists, retired, bare = load_cli_review_catalog(path).cases
    assert tomllib.loads(_case_toml(full)) == {
        "id": "full", "state": "active", "decision": "refuse",
        "reviewed_signature": "sha256:abc", "rationale": 'why "quoted" é',
        "invocation": ["inspect", "--mode", "x"], "expected_exit_status": 2,
        "expected_stdout_contains": "out", "expected_stderr_contains": "err",
        "effects": ["writes a file"], "test_ids": ["tests/t.py::a"],
    }
    assert tomllib.loads(_case_toml(empty_lists)) == {
        "id": "empty-lists", "state": "active", "decision": "accept",
        "reviewed_signature": "s", "rationale": "r", "invocation": [],
        "expected_exit_status": 0, "effects": [], "test_ids": ["x"],
    }
    assert tomllib.loads(_case_toml(retired)) == {
        "id": "gone", "state": "retired", "retirement_reason": "removed",
    }
    assert _case_toml(bare) == 'id = "bare"\nstate = "pending"'


def test_catalog_for_another_cli_is_refused_by_pack_and_report(tmp_path, monkeypatch, capsys):
    root = _project(tmp_path, monkeypatch, catalog=False)
    (root / "cli-review.toml").write_text('schema_version = 1\ncli_id = "wrong"\n', encoding="utf-8")
    for verb in ("pack", "report"):
        code, _out, err = _run(capsys, "surface", verb)
        assert code == 2
        assert "does not match registered executable" in err
    with pytest.raises(ReviewCatalogError):
        cli_module.template_text(_build_cli(), root / "cli-review.toml", None)


# ---- O7: the deprecated module ---------------------------------------------


def test_o7_deprecated_entrypoint_warns_first_and_still_works(tmp_path, monkeypatch, capsys):
    import cli_extended.surface_cli as surface_cli

    app, _surface, review, manifest, spec = _make_files(tmp_path)
    monkeypatch.setattr(surface_cli, "load_factory", lambda _name: app)
    args = [
        "--factory", "consumer.cli:build_cli", "--review", str(review),
        "--manifest", str(manifest), "--spec", str(spec),
    ]
    assert surface_cli.main([*args, "sync"]) == 0
    captured = capsys.readouterr()
    assert captured.err.splitlines()[0] == (
        "[WARN] python -m cli_extended.surface_cli is deprecated; "
        "use 'cli-extended surface ...'"
    )
    assert captured.out == "CLI surface files synchronized.\n"
    assert surface_cli.main([*args, "check"]) == 0
    assert capsys.readouterr().out == "CLI surface check passed.\n"
    assert surface_cli.main(["--factory", "x:y", "--review", str(review), "template"]) == 0
    captured = capsys.readouterr()
    assert captured.out == "No semantic review rows need adding or updating.\n"
    assert captured.err.startswith("[WARN] python -m cli_extended.surface_cli is deprecated")


# ---- O8: the packaged review skill ------------------------------------------


def test_o8_review_skill_validates_and_installs_into_a_tmp_destination(tmp_path, monkeypatch, capsys):
    directory = SRC / "cli_extended" / "skills" / "cli-extended-review"
    files = {path.name: path.read_bytes() for path in directory.iterdir() if path.is_file()}
    validate_skill_source("cli-extended-review", files)
    text = files["SKILL.md"].decode("utf-8")
    for needle in (
        "cli-extended surface sync", "surface pack --output", "cli-extended surface check",
        "cli-extended surface report", "Never invent a test ID", "wontfix",
        "never rewrites",
    ):
        assert needle in text

    dest = tmp_path / "skills-dest"
    code, out, _err = _run(capsys, "skills", "list", "--dest", str(dest))
    assert code == 0
    assert [line.split()[:2] for line in out.splitlines()] == [
        ["absent", "cli-extended-adoption"],
        ["absent", "cli-extended-review"],
    ]
    code, _out, _err = _run(capsys, "skills", "install", "--dest", str(dest))
    assert code == 0
    installed = (dest / "cli-extended-review" / "SKILL.md").read_text(encoding="utf-8")
    assert "name: cli-extended-review" in installed
    assert "cli-extended-tool: cli-extended" in installed
    assert not (tmp_path / "home" / ".claude").exists()
    assert [
        line.split()[0] for line in _run(capsys, "skills", "list", "--dest", str(dest))[1].splitlines()
    ] == ["current", "current"]


# ---- O9: help, version, contract, identity --------------------------------


def _subprocess_invoke(tmp_path, *, patch_version):
    prelude = (
        "import importlib.metadata as m, sys\n"
        "import cli_extended.identity as i\n"
    )
    if patch_version:
        prelude += "i.installed_version = lambda _d: '0.1.0'\n"
    else:
        prelude += (
            "def _missing(_d):\n    raise m.PackageNotFoundError(_d)\n"
            "i.installed_version = _missing\n"
        )
    program = prelude + "from cli_extended.cli import main\nraise SystemExit(main(sys.argv[1:]))\n"
    env = {
        "HOME": str(tmp_path / "home"),
        "PYTHONPATH": str(SRC),
        "PYTHONDONTWRITEBYTECODE": "1",
        "PATH": "/usr/bin:/bin",
    }

    def invoke(argv):
        return subprocess.run(
            [sys.executable, "-c", program, *argv],
            capture_output=True, text=True, env=env, cwd=tmp_path, timeout=300,
        )

    return invoke


def test_o9_contract_holds_for_the_real_executable(tmp_path):
    invoke = _subprocess_invoke(tmp_path, patch_version=True)
    identity = CliIdentity(
        name="CLI-EXTENDED", version="0.1.0", long_name="shared CLI contract tooling",
        command="cli-extended",
    )
    assert_cli_contract(
        invoke, identity, ("surface", "skills"),
        invalid_invocations={"unknown verb": ("bogus",)},
    )
    version = invoke(["version"])
    assert (version.returncode, version.stdout, version.stderr) == (0, "cli-extended 0.1.0\n", "")


def test_o9_an_uninstalled_distribution_fails_cleanly(tmp_path):
    result = _subprocess_invoke(tmp_path, patch_version=False)(["version"])
    assert result.returncode == 2
    assert result.stdout == ""
    assert result.stderr.startswith("[ERROR] cli-extended: ")
    assert "'cli-extended' is not installed" in result.stderr
    assert "Traceback" not in result.stderr


def test_o9_module_entrypoint_runs_main(monkeypatch, capsys):
    import runpy

    monkeypatch.delitem(sys.modules, "cli_extended.cli")
    monkeypatch.setattr(sys, "argv", ["cli-extended", "version"])
    with pytest.raises(SystemExit) as raised:
        runpy.run_module("cli_extended.cli", run_name="__main__")
    assert raised.value.code == 0
    assert capsys.readouterr().out == "cli-extended 0.1.0\n"


def test_o9_main_reports_a_missing_distribution_in_process(monkeypatch, capsys):
    def missing(name):
        raise PackageNotFoundError(name)

    monkeypatch.setattr(identity_module, "installed_version", missing)
    assert cli_module.main(["version"]) == 2
    captured = capsys.readouterr()
    assert captured.out == ""
    assert captured.err.startswith("[ERROR] cli-extended: ")


def test_o9_in_process_help_version_and_registry_policy(capsys):
    assert cli_module.main(["version"]) == 0
    assert capsys.readouterr().out == "cli-extended 0.1.0\n"
    app = cli_module.build_cli()
    assert app.unexpected_exceptions == "report"
    assert app.identity.command_name == "cli-extended"
    assert sorted(app.command_parsers) == ["audit", "skills", "surface"]
    assert sorted(app.delegates["surface"].command_parsers) == [
        "check", "pack", "report", "sync", "template",
    ]
    assert "--traceback" in app.parser._option_string_actions
    injected = CliIdentity("X", "9.9.9", "x", command="cli-extended")
    assert cli_module.build_cli(injected).identity is injected
    assert cli_module.main(["--help"]) == 0
    assert capsys.readouterr().out.startswith("CLI-EXTENDED 0.1.0")


# ---- review round 1 ----------------------------------------------------------


def test_pack_is_identical_for_any_terminal_width(tmp_path, monkeypatch, capsys):
    _project(tmp_path, monkeypatch, catalog=False)
    bundles = {}
    for columns in ("40", "200"):
        monkeypatch.setenv("COLUMNS", columns)
        code, out, err = _run(capsys, "surface", "pack")
        assert (code, err) == (0, "")
        bundles[columns] = out
    assert bundles["40"] == bundles["200"]
    # The width is pinned, not absent: the same help wraps differently outside pack.
    app = _build_cli()
    plain = {}
    for columns in ("60", "200"):
        monkeypatch.setenv("COLUMNS", columns)
        out = io.StringIO()
        app.run(argv=["help", "inspect"], stdout=out, stderr=io.StringIO())
        plain[columns] = out.getvalue()
    assert plain["60"] != plain["200"]
    # ... and pack uses the 100-column rendering.
    monkeypatch.setenv("COLUMNS", "100")
    out = io.StringIO()
    app.run(argv=["help", "inspect"], stdout=out, stderr=io.StringIO())
    assert out.getvalue().rstrip("\n") in bundles["40"]


def test_pack_help_has_no_colour_even_when_argparse_would_force_it(tmp_path, monkeypatch, capsys):
    _project(tmp_path, monkeypatch, catalog=False)
    monkeypatch.setenv("FORCE_COLOR", "1")
    monkeypatch.delenv("NO_COLOR", raising=False)
    code, out, _err = _run(capsys, "surface", "pack")
    assert code == 0
    assert "\x1b" not in out
    assert "usage: audit-tool inspect" in out


def test_help_with_no_color_is_plain_under_force_color_and_color_still_works(monkeypatch):
    monkeypatch.setenv("FORCE_COLOR", "1")
    monkeypatch.delenv("NO_COLOR", raising=False)
    app = _build_cli()
    for argv in (["--help", "--no-color"], ["inspect", "--help", "--no-color"], ["--no-color", "help", "inspect"]):
        out = io.StringIO()
        assert app.run(argv=argv, stdout=out, stderr=io.StringIO()) == 0
        assert "\x1b" not in out.getvalue(), argv
    out = io.StringIO()
    assert app.run(argv=["inspect", "--help", "--color"], stdout=out, stderr=io.StringIO()) == 0
    assert "\x1b[1;36m--help\x1b[0m" in out.getvalue()


def test_the_parser_never_lets_argparse_colour_on_its_own(monkeypatch):
    from cli_extended import ExtendedArgumentParser

    monkeypatch.setenv("FORCE_COLOR", "1")
    parser = ExtendedArgumentParser(
        prog="x", identity=CliIdentity("X", "1.0", "X", command="x")
    )
    parser.add_argument("--flag", help="a flag")
    assert "\x1b" not in parser.format_help()
    assert "\x1b" not in parser.format_usage()


def test_pack_shape_json_is_key_sorted(tmp_path):
    app, surface, catalog = _surface_and_catalog(tmp_path)
    candidate = {
        **surface["candidates"][0],
        "shape": {"zeta": 1, "alpha": {"yy": 1, "bb": [2, {"q": 1, "c": 2}]}},
        "members": ["m"],
    }
    catalog = types.SimpleNamespace(cases=(), cases_by_id={})
    text = render_pack(app, {**surface, "candidates": [candidate]}, catalog, None)
    expected = json.dumps(
        {
            "route_id": candidate["route_id"],
            "members": ["m"],
            "shape": candidate["shape"],
            "signature": candidate["signature"],
        },
        indent=2, sort_keys=True,
    )
    assert expected in text
    assert text.index('"alpha"') < text.index('"zeta"')
    assert text.index('"bb"') < text.index('"yy"')
    assert text.index('"members"') < text.index('"route_id"') < text.index('"shape"') < text.index('"signature"')


def test_pack_with_only_a_stale_case_lists_it_and_does_not_say_none(tmp_path):
    extra = ["[[cases]]", 'id = "case:gone"', 'state = "pending"']
    app, surface, catalog = _surface_and_catalog(tmp_path, None, extra)
    text = render_pack(app, surface, catalog, None)
    section = text.split("## Cases to review\n\n")[1].split("\n## Open findings")[0]
    assert section.startswith("### `case:gone`\n")
    assert "None." not in section
    assert "- kind: stale\n" in section


def test_discovery_from_a_symlinked_directory_finds_the_physical_parents_config(tmp_path, monkeypatch):
    from cli_extended import load_project_config

    project = tmp_path / "project"
    (project / "sub").mkdir(parents=True)
    (project / "cli-extended.toml").write_text(
        'schema_version = 1\n[[clis]]\nid = "a"\nfactory = "m:f"\n', encoding="utf-8"
    )
    link = tmp_path / "elsewhere" / "link"
    link.parent.mkdir()
    link.symlink_to(project / "sub", target_is_directory=True)
    assert load_project_config(start=link).path == (project / "cli-extended.toml").resolve()
    monkeypatch.chdir(link)
    assert load_project_config().path == (project / "cli-extended.toml").resolve()


def test_backslash_alone_does_not_make_a_factory_a_file_path(monkeypatch):
    import cli_extended.config as config

    seen = []

    def fake_import(name):
        seen.append(name)
        return types.SimpleNamespace(build=lambda: _build_cli())

    monkeypatch.setattr(config.importlib, "import_module", fake_import)
    assert config.load_factory("pkg\\mod:build").identity.command_name == "audit-tool"
    assert seen == ["pkg\\mod"]
    assert config._is_file_target("a/b") and config._is_file_target("x.py")
    assert not config._is_file_target("a\\b")


def test_fixed_help_width_pins_exactly_nests_and_restores_on_error(monkeypatch):
    from cli_extended.parser import MIN_HELP_COLUMNS, help_columns

    monkeypatch.setenv("COLUMNS", "133")
    unpinned = help_columns()
    assert unpinned == 133
    with fixed_help_width(100):
        assert help_columns() == 100
        with fixed_help_width(70):
            assert help_columns() == 70
        assert help_columns() == 100
    assert help_columns() == unpinned
    with fixed_help_width(87):
        assert help_columns() == 87
    with fixed_help_width(MIN_HELP_COLUMNS):
        assert help_columns() == 60
    with pytest.raises(RuntimeError):
        with fixed_help_width(90):
            assert help_columns() == 90
            raise RuntimeError("boom")
    assert help_columns() == unpinned


@pytest.mark.parametrize("bad", (0, -1, 59, 60.0, "100", None, True))
def test_fixed_help_width_rejects_values_below_the_floor_or_not_ints(bad):
    from cli_extended.parser import help_columns

    before = help_columns()
    with pytest.raises(ValueError, match="integer of at least 60"):
        with fixed_help_width(bad):
            raise AssertionError("must not enter")
    assert help_columns() == before


def test_pack_help_width_is_100_and_no_help_line_exceeds_it(tmp_path, monkeypatch, capsys):
    assert cli_module.PACK_HELP_COLUMNS == 100
    _project(tmp_path, monkeypatch, catalog=False)
    monkeypatch.setenv("COLUMNS", "300")
    _code, out, _err = _run(capsys, "surface", "pack")
    blocks = [part.split("\n```")[0] for part in out.split("```text\n")[1:]]
    lines = [line for block in blocks for line in block.splitlines()]
    assert lines
    assert max(len(line) for line in lines) <= 100
    # With a wide terminal the same help would use longer lines.
    wide = io.StringIO()
    _build_cli().run(argv=["help", "inspect"], stdout=wide, stderr=io.StringIO())
    assert max(len(line) for line in wide.getvalue().splitlines()) > 100
