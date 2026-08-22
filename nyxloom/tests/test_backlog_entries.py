"""Tests for backlog_entries.py + the `nyxloom backlog` CLI verbs +
lint BLG2/BLG3 (docs/backlog-entries-spec.md).

Oracles:
- O1 entry contract: scaffold/parse/validate (schema, stem==id, prefix,
  uniqueness, closed_* iff terminal).
- O2 index: generated INDEX.md is byte-stable; staleness is a lint error.
- O3 promotion: spine + plain inbox -> entry; inbox edited surgically
  (every byte outside the item's line range survives, comments included).
- O4 status machine: typed transitions, reason stamping, merged refusal,
  reopen clears closed_*; merge auto-tick writes ONLY its two tokens.
"""

from __future__ import annotations

import json
import textwrap
from pathlib import Path

import pytest
import structlog.contextvars
import logging

from nyxloom import backlog_entries, cli, lint, log
from nyxloom.frontmatter import HandoffParseError
from nyxloom.types import TaskState, TaskStateFile, utc_now


@pytest.fixture(autouse=True)
def _silence_nyxloom_logging():
    """Same rationale as test_backlog_items.py's fixture: this file makes
    stdout/byte-exact assertions and backlog_entries emits log.* calls;
    structlog's pre-configure default would print them."""
    log.configure(level=log.CRITICAL, console=False)
    yield
    structlog.contextvars.clear_contextvars()
    nyxloom_logger = logging.getLogger("nyxloom")
    for handler in list(nyxloom_logger.handlers):
        nyxloom_logger.removeHandler(handler)
        handler.close()


def make_cfg(tmp_path, *, with_entries=True, toml_extra=""):
    """A minimal ProjectConfig on disk (no registry involvement)."""
    root = tmp_path / "proj"
    trove = root / "nyxloom-trove"
    trove.mkdir(parents=True)
    entries = ""
    if with_entries:
        entries = '\n[backlog_entries]\nid_prefix = "CIU"\n'
    (trove / "nyxloom.toml").write_text(
        textwrap.dedent("""\
            [project]
            id = "demo"
            default_branch = "main"
            handoff_globs = ["handoff/*.md"]

            [gates.g]
            argv = ["true"]
            phase = "implementation"
            timeout_seconds = 60
            environment = "local"
            """)
        + entries + toml_extra,
        encoding="utf-8",
    )
    from nyxloom.config import ProjectConfig
    return ProjectConfig.load(root)


def write_entry(dir_path, name, fm: dict, body="## Updates\n"):
    ordered = ["kind", "schema_version", "id", "title", "status"]
    lines = ["---"]
    for key in ordered:
        if key in fm:
            v = fm[key]
            lines.append(f"{key}: {json.dumps(v) if isinstance(v, str) else v}")
    for key, v in fm.items():
        if key not in ordered:
            lines.append(f"{key}: {json.dumps(v) if isinstance(v, str) else v}")
    lines.append("---")
    p = dir_path / name
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text("\n".join(lines) + "\n\n" + body + "\n", encoding="utf-8")
    return p


def valid_fm(**over):
    fm = {"kind": "backlog-entry", "schema_version": 1, "id": "CIU-1",
          "title": "first", "status": "open"}
    fm.update(over)
    return fm


# ----- fixtures -----

@pytest.fixture()
def cfg(tmp_path):
    return make_cfg(tmp_path)


# ----- O1: entry contract -----

class TestO1EntryContract:
    def test_resolve_dir_none_without_section(self, tmp_path):
        cfg = make_cfg(tmp_path, with_entries=False)
        assert backlog_entries.resolve_dir(cfg) is None
        assert backlog_entries.load_entries(cfg) == []
        assert backlog_entries.validate_dir(cfg) == []
        assert backlog_entries.index_findings(cfg) == []

    def test_create_allocates_dashed_id_and_validates_clean(self, tmp_path):
        cfg = make_cfg(tmp_path)
        p = backlog_entries.create_entry(cfg, "env generate leaks ambient network")
        assert p.name.startswith("CIU-1-")
        e = backlog_entries.parse_entry(p)
        assert e.id == "CIU-1" and e.status == "open"
        assert e.filed_date == backlog_entries.today()
        assert "## Observed mechanism and reproduction" in p.read_text()
        assert backlog_entries.validate_dir(cfg) == []

    def test_allocation_continues_past_existing_ids(self, tmp_path):
        cfg = make_cfg(tmp_path)
        d = backlog_entries.resolve_dir(cfg)
        write_entry(d, "CIU-3-something.md", valid_fm(id="CIU-3"))
        assert backlog_entries.allocate_id(backlog_entries.load_entries(cfg), "CIU") == "CIU-4"

    def test_legacy_dashless_id_is_valid_when_prefix_matches(self, tmp_path):
        root = tmp_path / "legacy"
        trove = root / "nyxloom-trove"
        trove.mkdir(parents=True)
        (trove / "nyxloom.toml").write_text(
            '[project]\nid = "demo"\nhandoff_globs = ["h/*.md"]\n'
            '[gates.g]\nargv = ["true"]\nphase = "implementation"\n'
            'timeout_seconds = 60\nenvironment = "local"\n'
            '\n[backlog_entries]\nid_prefix = "B"\n',
            encoding="utf-8",
        )
        from nyxloom.config import ProjectConfig
        cfg = ProjectConfig.load(root)
        d = backlog_entries.resolve_dir(cfg)
        write_entry(d, "B001-sql-adapter.md", valid_fm(id="B001", title="sql"))
        assert backlog_entries.validate_dir(cfg) == []
        assert backlog_entries.allocate_id([], "B") == "B-1"
        assert backlog_entries.allocate_id(backlog_entries.load_entries(cfg), "B") == "B-2"

    def test_blug_wrong_prefix_fires(self, tmp_path):
        cfg = make_cfg(tmp_path)
        d = backlog_entries.resolve_dir(cfg)
        write_entry(d, "KI-1-other-tool-id.md", valid_fm(id="KI-1"))
        findings = backlog_entries.validate_dir(cfg)
        assert any("id_prefix" in f.message for f in findings)

    def test_stem_must_carry_slug(self, tmp_path):
        cfg = make_cfg(tmp_path)
        d = backlog_entries.resolve_dir(cfg)
        write_entry(d, "CIU-1.md", valid_fm())
        findings = backlog_entries.validate_dir(cfg)
        assert any("<id>-<slug>" in f.message for f in findings)

    def test_duplicate_id_fires(self, tmp_path):
        cfg = make_cfg(tmp_path)
        d = backlog_entries.resolve_dir(cfg)
        write_entry(d, "CIU-1-a.md", valid_fm())
        write_entry(d, "CIU-1-b.md", valid_fm(title="dupe"))
        findings = backlog_entries.validate_dir(cfg)
        assert any("duplicate id" in f.message for f in findings)

    def test_terminal_requires_reason_and_nonterminal_forbids_it(self, tmp_path):
        cfg = make_cfg(tmp_path)
        d = backlog_entries.resolve_dir(cfg)
        write_entry(d, "CIU-1-fixed-no-reason.md", valid_fm(status="fixed"))
        write_entry(d, "CIU-2-open-with-reason.md",
                    valid_fm(id="CIU-2", closed_reason='"done"'))
        msgs = [f.message for f in backlog_entries.validate_dir(cfg)]
        assert any("requires closed_reason" in m for m in msgs)
        assert any("closed_reason present but status is open" in m for m in msgs)

    def test_merged_requires_merge_commit(self, tmp_path):
        cfg = make_cfg(tmp_path)
        d = backlog_entries.resolve_dir(cfg)
        write_entry(d, "CIU-1-merged.md", valid_fm(status="merged"))
        findings = backlog_entries.validate_dir(cfg)
        assert any("merge_commit" in f.message for f in findings)

    def test_unparsable_frontmatter_is_fail_closed(self, tmp_path):
        cfg = make_cfg(tmp_path)
        d = backlog_entries.resolve_dir(cfg)
        d.mkdir(parents=True, exist_ok=True)
        (d / "CIU-1-broken.md").write_text("no frontmatter at all\n", encoding="utf-8")
        findings = backlog_entries.validate_dir(cfg)
        assert any(f.rule == "BLG2" and "unparsable" in f.message for f in findings)


# ----- O2: index -----

class TestO2Index:
    def test_index_generation_deterministic_and_fresh(self, tmp_path):
        cfg = make_cfg(tmp_path)
        backlog_entries.create_entry(cfg, "beta issue", priority=5)
        backlog_entries.create_entry(cfg, "alpha issue", priority=1)
        first = backlog_entries.render_index(backlog_entries.load_entries(cfg))
        second = backlog_entries.render_index(backlog_entries.load_entries(cfg))
        assert first == second
        assert backlog_entries.index_findings(cfg) == []

    def test_stale_index_is_lint_error(self, tmp_path):
        cfg = make_cfg(tmp_path)
        backlog_entries.create_entry(cfg, "an issue")
        index = backlog_entries.resolve_dir(cfg) / "INDEX.md"
        index.write_text(index.read_text() + "\n| hand-edited row |\n")
        findings = backlog_entries.index_findings(cfg)
        assert len(findings) == 1
        assert findings[0].rule == "BLG3"

    def test_missing_index_is_lint_error(self, tmp_path):
        cfg = make_cfg(tmp_path)
        backlog_entries.create_entry(cfg, "an issue")
        (backlog_entries.resolve_dir(cfg) / "INDEX.md").unlink()
        assert backlog_entries.index_findings(cfg)


# ----- O3: promotion -----

SPINE_INBOX = textwrap.dedent("""\
    ---
    kind: backlog
    schema_version: 1
    items:
    # routing ideas live here
    - id: B1
      title: route doctor verb
      type: feature
      component: routing
      context_estimate: small
      folds_into: F009
    - id: B2
      title: availability layer toggle
      type: feature
      component: routing
    ---

    Body prose survives untouched.
    """)


class TestO3Promotion:
    def test_promote_from_spine_inbox_surgical(self, tmp_path):
        cfg = make_cfg(tmp_path)
        # add the spine inbox key INSIDE the existing [project] section
        toml = cfg.root / "nyxloom-trove" / "nyxloom.toml"
        txt = toml.read_text().replace(
            'handoff_globs = ["handoff/*.md"]\n',
            'handoff_globs = ["handoff/*.md"]\nbacklog = "nyxloom-trove/4-backlog-inbox.md"\n')
        toml.write_text(txt)
        from nyxloom.config import ProjectConfig
        cfg = ProjectConfig.load(cfg.root)
        inbox = cfg.root / "nyxloom-trove" / "4-backlog-inbox.md"
        inbox.write_text(SPINE_INBOX, encoding="utf-8")

        entry_path = backlog_entries.promote(cfg, "B1")

        e = backlog_entries.parse_entry(entry_path)
        assert e.title == "route doctor verb"
        assert e.type == "feature" and e.component == "routing"
        assert e.context_estimate == "small" and e.folds_into == "F009"
        assert e.promoted_from == "B1"

        after = inbox.read_text()
        assert "route doctor verb" not in after          # item gone
        assert "# routing ideas live here" in after       # comment survived
        assert "availability layer toggle" in after       # sibling intact
        assert "Body prose survives untouched." in after  # body intact

    def test_promote_unknown_spine_id_raises_keyerror(self, tmp_path):
        cfg = make_cfg(tmp_path)
        toml = cfg.root / "nyxloom-trove" / "nyxloom.toml"
        toml.write_text(toml.read_text().replace(
            'handoff_globs = ["handoff/*.md"]\n',
            'handoff_globs = ["handoff/*.md"]\nbacklog = "nyxloom-trove/4-backlog-inbox.md"\n'))
        from nyxloom.config import ProjectConfig
        cfg = ProjectConfig.load(cfg.root)
        (cfg.root / "nyxloom-trove" / "4-backlog-inbox.md").write_text(SPINE_INBOX)
        with pytest.raises(KeyError):
            backlog_entries.promote(cfg, "B99")

    def test_promote_from_plain_bullet_inbox(self, tmp_path):
        cfg = make_cfg(tmp_path)
        plain = cfg.root / "nyxloom-trove" / "backlog.md"
        plain.write_text(
            "# backlog\n\n"
            "- **B1 — keep me.** stay.\n"
            "- **B2 — promote me.** The long detail\n"
            "  continues here.\n"
            "  <!-- nyxloom:backlog id=B2 status=open priority=2 -->\n"
            "- **B3 — keep me too.** stay.\n",
            encoding="utf-8")
        entry_path = backlog_entries.promote(cfg, "B2")
        assert backlog_entries.parse_entry(entry_path).promoted_from == "B2"
        after = plain.read_text()
        assert "promote me" not in after
        assert "<!-- nyxloom:backlog id=B2" not in after
        assert "- **B1 — keep me.** stay.\n" in after
        assert "- **B3 — keep me too.** stay.\n" in after

    def test_promote_without_any_inbox_refuses(self, tmp_path):
        cfg = make_cfg(tmp_path)
        with pytest.raises(FileNotFoundError):
            backlog_entries.promote(cfg, "B1")


# ----- O4: status machine + auto-tick -----

class TestO4StatusMachine:
    def test_terminal_transition_stamps_reason_and_date(self, tmp_path):
        cfg = make_cfg(tmp_path)
        p = backlog_entries.create_entry(cfg, "leaky clean")
        backlog_entries.set_status(cfg, "CIU-1", "withdrawn",
                                   reason="premise disproved")
        e = backlog_entries.parse_entry(p)
        assert e.status == "withdrawn" and e.closed_reason == "premise disproved"
        assert e.closed_date == backlog_entries.today()
        assert backlog_entries.validate_dir(cfg) == []

    def test_terminal_without_reason_refused(self, tmp_path):
        cfg = make_cfg(tmp_path)
        backlog_entries.create_entry(cfg, "leaky clean")
        with pytest.raises(ValueError, match="--reason is required"):
            backlog_entries.set_status(cfg, "CIU-1", "fixed")

    def test_merged_hand_set_refused(self, tmp_path):
        cfg = make_cfg(tmp_path)
        backlog_entries.create_entry(cfg, "leaky clean")
        with pytest.raises(ValueError, match="auto-tick"):
            backlog_entries.set_status(cfg, "CIU-1", "merged", reason="x")

    def test_unknown_status_refused(self, tmp_path):
        cfg = make_cfg(tmp_path)
        backlog_entries.create_entry(cfg, "leaky clean")
        with pytest.raises(ValueError, match="unknown status"):
            backlog_entries.set_status(cfg, "CIU-1", "shipped", reason="x")

    def test_reopen_clears_closed_fields(self, tmp_path):
        cfg = make_cfg(tmp_path)
        p = backlog_entries.create_entry(cfg, "leaky clean")
        backlog_entries.set_status(cfg, "CIU-1", "obsolete", reason="gone")
        backlog_entries.set_status(cfg, "CIU-1", "open", reason="recurred")
        e = backlog_entries.parse_entry(p)
        assert e.status == "open"
        assert e.closed_reason is None and e.closed_date is None
        assert "closed_" not in p.read_text()

    def test_tick_writes_only_its_two_tokens(self, tmp_path):
        cfg = make_cfg(tmp_path)
        d = backlog_entries.resolve_dir(cfg)
        p = write_entry(d, "CIU-7-linked.md", valid_fm(
            id="CIU-7", carved_handoff="demo-P01-x"))
        before = p.read_text()
        assert backlog_entries.tick_merged_entries(cfg, "demo-P01-x", "c" * 40)
        after = p.read_text()
        # every original line survives except status's value changes and the
        # merge_commit token appears; nothing else moved
        for ln in before.splitlines():
            if ln.startswith("status:") or ln.startswith("merge_commit"):
                continue
            assert ln in after.splitlines()
        assert "status: merged" in after
        assert f'merge_commit: {"c" * 40}' in after

    def test_tick_no_match_no_write(self, tmp_path):
        cfg = make_cfg(tmp_path)
        p = backlog_entries.create_entry(cfg, "unlinked")
        before = p.read_text()
        assert backlog_entries.tick_merged_entries(cfg, "nope", "c" * 40) is False
        assert p.read_text() == before

    def test_note_appends_dated_paragraph(self, tmp_path):
        cfg = make_cfg(tmp_path)
        p = backlog_entries.create_entry(cfg, "network leak")
        backlog_entries.note(cfg, "CIU-1", "second repro on 6.3.0: volumes leak too")
        body = p.read_text()
        assert "**" + backlog_entries.today() + "** — second repro on 6.3.0" in body


# ----- lint integration -----

class TestLintIntegration:
    def test_lint_project_surfaces_blb2_blb3(self, tmp_path):
        cfg = make_cfg(tmp_path)
        d = backlog_entries.resolve_dir(cfg)
        write_entry(d, "CIU-1-bad-status.md",
                    valid_fm(status="bogus"))  # schema violation
        results = lint.lint_project(cfg)
        all_findings = [f for fs in results.values() for f in fs]
        assert any(f.rule == "BLG2" for f in all_findings)
        assert any(f.rule == "BLG3" for f in all_findings)  # no INDEX yet

    def test_lint_silent_without_section(self, tmp_path):
        cfg = make_cfg(tmp_path, with_entries=False)
        results = lint.lint_project(cfg)
        assert not [f for fs in results.values() for f in fs
                    if f.rule in ("BLG2", "BLG3")]


# ----- config plumbing -----

class TestConfigPlumbing:
    def test_table_loads_into_fields(self, tmp_path):
        cfg = make_cfg(tmp_path)
        assert cfg.backlog_id_prefix == "CIU"
        assert cfg.backlog_entries_dir is None  # default applies at resolve
        assert backlog_entries.resolve_dir(cfg) == \
            cfg.root / "nyxloom-trove" / "backlog"

    def test_explicit_dir_honoured(self, tmp_path):
        cfg = make_cfg(tmp_path, toml_extra="")
        toml = cfg.root / "nyxloom-trove" / "nyxloom.toml"
        toml.write_text(toml.read_text().replace(
            '[backlog_entries]\nid_prefix = "CIU"',
            '[backlog_entries]\ndir = "nyxloom-trove/issues"\nid_prefix = "CIU"'))
        from nyxloom.config import ProjectConfig
        cfg = ProjectConfig.load(cfg.root)
        assert backlog_entries.resolve_dir(cfg) == cfg.root / "nyxloom-trove" / "issues"


# ----- CLI -----

@pytest.fixture()
def demo_with_entries(sample_project):
    """conftest's fully-wired `demo` project, plus [backlog_entries]."""
    toml = sample_project.root / ".nyxloom" / "project.toml"
    toml.write_text(toml.read_text()
                    + '\n[backlog_entries]\nid_prefix = "CIU"\n',
                    encoding="utf-8")
    from nyxloom.config import ProjectConfig
    return ProjectConfig.load(sample_project.root)


class TestCli:
    def test_new_creates_entry_and_prints_path(self, demo_with_entries):
        rc = cli.main(["backlog", "new", "--project", "demo",
                       "env generate leaks ambient network",
                       "--type", "bugfix", "--severity", "medium"])
        assert rc == 0
        d = backlog_entries.resolve_dir(demo_with_entries)
        files = [p for p in d.glob("*.md") if p.name != "INDEX.md"]
        assert len(files) == 1
        assert backlog_entries.parse_entry(files[0]).severity == "medium"
        assert (d / "INDEX.md").exists()

    def test_new_refuses_without_section(self, sample_project):
        rc = cli.main(["backlog", "new", "--project", "demo", "nope"])
        assert rc == 1

    def test_set_status_reason_refusal_exit_2(self, demo_with_entries):
        cli.main(["backlog", "new", "--project", "demo", "an issue"])
        rc = cli.main(["backlog", "set-status", "--project", "demo",
                       "CIU-1", "fixed"])
        assert rc == 2
        rc = cli.main(["backlog", "set-status", "--project", "demo",
                       "CIU-1", "fixed", "--reason", "done elsewhere"])
        assert rc == 0
        e = backlog_entries.load_entries(demo_with_entries)[0]
        assert e.status == "fixed" and e.closed_reason == "done elsewhere"

    def test_show_list_note(self, demo_with_entries, capsys):
        cli.main(["backlog", "new", "--project", "demo", "an issue"])
        cli.main(["backlog", "note", "--project", "demo", "CIU-1", "repro #2"])
        out = capsys.readouterr().out
        cli.main(["backlog", "show", "--project", "demo", "CIU-1"])
        assert "repro #2" in capsys.readouterr().out
        rc = cli.main(["backlog", "list", "--project", "demo"])
        assert rc == 0
        assert "| [CIU-1](" in capsys.readouterr().out

    def test_show_missing_exit_1(self, demo_with_entries):
        cli.main(["backlog", "new", "--project", "demo", "an issue"])
        # CIU-1 exists; asking for CIU-9 walks the loop's non-matching arc
        assert cli.main(["backlog", "show", "--project", "demo", "CIU-9"]) == 1

    def test_promote_plain_inbox_via_cli(self, demo_with_entries):
        plain = demo_with_entries.root / "nyxloom-trove" / "backlog.md"
        plain.parent.mkdir(parents=True, exist_ok=True)
        plain.write_text("- **B4 — idea.** detail.\n", encoding="utf-8")
        rc = cli.main(["backlog", "promote", "--project", "demo", "B4"])
        assert rc == 0
        entries = backlog_entries.load_entries(demo_with_entries)
        assert entries[0].promoted_from == "B4"
        assert "B4" not in plain.read_text()

    def test_merge_auto_ticks_linked_entry(self, demo_with_entries, make_statefile=None):
        from nyxloom import storage
        from nyxloom.types import TaskState, TaskStateFile, utc_now
        import tests.test_backlog_items as tbi

        d = backlog_entries.resolve_dir(demo_with_entries)
        fm = valid_fm(id="CIU-5", title="linked work",
                      carved_handoff="demo-P01-test")
        write_entry(d, "CIU-5-linked.md", fm)

        tsf = TaskStateFile(
            schema_version=1, task_id="demo-P01-test", project="demo",
            state=TaskState.MERGE_READY, since=utc_now(), paused=False)
        storage.save_state(tsf)

        rc = cli.main(["merge", "demo", "demo-P01-test",
                       "--commit", "b" * 40])
        assert rc == 0
        e = backlog_entries.parse_entry(d / "CIU-5-linked.md")
        assert e.status == "merged" and e.merge_commit == "b" * 40


# ----- docs sync (estate mandate: shipped-loader parses every example) -----

class TestDocsSync:
    def test_consumers_toml_examples_parse_and_validate(self):
        """Every ```toml block in docs/CONSUMERS.md must be valid TOML, and
        any [backlog_entries] example must validate against the SHIPPED
        config schema (docs drift fails here, not at an adopter)."""
        import re
        import tomllib
        doc = (Path(__file__).resolve().parents[1] / "docs" /
               "CONSUMERS.md").read_text(encoding="utf-8")
        blocks = re.findall(r"```toml\n(.*?)```", doc, re.S)
        assert blocks, "CONSUMERS.md lost its paste-able examples"
        schema = json.loads((Path(__file__).resolve().parents[1] / "src" /
                             "nyxloom" / "schemas" /
                             "nyxloom-config.schema.json").read_text())
        import jsonschema as _js
        for block in blocks:
            data = tomllib.loads(block)
            if "backlog_entries" in data:
                _js.Draft202012Validator(
                    schema["properties"]["backlog_entries"]).validate(
                    data["backlog_entries"])

    def test_consumers_covers_every_status_vocabulary_value(self):
        """Estate mandate #2: every closed-vocabulary value a consumer can
        type appears in at least one user-facing document."""
        doc = (Path(__file__).resolve().parents[1] / "docs" /
               "CONSUMERS.md").read_text(encoding="utf-8")
        for value in ("fixed", "withdrawn", "obsolete"):
            assert value in doc

    def test_spec_and_standard_name_the_same_verbs(self):
        import re
        text = (Path(__file__).resolve().parents[1] / "docs" /
                "backlog-entries-spec.md").read_text(encoding="utf-8")
        for verb in ("new", "promote", "note", "set-status", "list", "show",
                     "index"):
            # the verb must appear inside some code span (table cell or prose)
            assert re.search(rf"`[^`]*\b{re.escape(verb)}\b[^`]*`", text), verb


# ----- coverage closure: every remaining branch gets a behavioral oracle -----

class TestCoverageClosure:
    def test_decisions_field_parses(self, tmp_path):
        cfg = make_cfg(tmp_path)
        d = backlog_entries.resolve_dir(cfg)
        write_entry(d, "CIU-1-decided.md", valid_fm(decisions=["D-1", "D-2"]))
        e = backlog_entries.load_entries(cfg)[0]
        assert e.decisions == ["D-1", "D-2"]

    def test_missing_id_reports_schema_error_without_crash(self, tmp_path):
        cfg = make_cfg(tmp_path)
        d = backlog_entries.resolve_dir(cfg)
        d.mkdir(parents=True)
        (d / "CIU-1-noid.md").write_text(
            "---\nkind: backlog-entry\nschema_version: 1\ntitle: x\nstatus: open\n---\n",
            encoding="utf-8")
        findings = backlog_entries.validate_dir(cfg)
        assert any("id" in f.message for f in findings)

    def test_bad_id_grammar_fires(self, tmp_path):
        cfg = make_cfg(tmp_path)
        d = backlog_entries.resolve_dir(cfg)
        write_entry(d, "ciu-1-lower.md", valid_fm(id="ciu-1"))
        findings = backlog_entries.validate_dir(cfg)
        assert any("does not match" in f.message for f in findings)

    def test_promote_spine_file_missing_falls_to_plain_then_refuses(self, tmp_path):
        cfg = make_cfg(tmp_path)
        toml = cfg.root / "nyxloom-trove" / "nyxloom.toml"
        toml.write_text(toml.read_text().replace(
            'handoff_globs = ["handoff/*.md"]\n',
            'handoff_globs = ["handoff/*.md"]\nbacklog = "nyxloom-trove/gone.md"\n'))
        from nyxloom.config import ProjectConfig
        cfg = ProjectConfig.load(cfg.root)
        with pytest.raises(FileNotFoundError):
            backlog_entries.promote(cfg, "B1")

    def test_block_scan_mismatch_refuses_edit(self, tmp_path):
        cfg = make_cfg(tmp_path)
        toml = cfg.root / "nyxloom-trove" / "nyxloom.toml"
        toml.write_text(toml.read_text().replace(
            'handoff_globs = ["handoff/*.md"]\n',
            'handoff_globs = ["handoff/*.md"]\nbacklog = "nyxloom-trove/4-backlog-inbox.md"\n'))
        from nyxloom.config import ProjectConfig
        cfg = ProjectConfig.load(cfg.root)
        # flow-style second item parses as 2 frontmatter items but yields
        # only ONE scannable `- id: ` block -> mismatch must refuse the edit
        (cfg.root / "nyxloom-trove" / "4-backlog-inbox.md").write_text(
            "---\nkind: backlog\nschema_version: 1\nitems:\n"
            "- id: B1\n  title: one\n- {id: B2, title: two}\n---\n\nbody\n",
            encoding="utf-8")
        with pytest.raises(HandoffParseError, match="refusing to edit"):
            backlog_entries.promote(cfg, "B1")

    def test_promote_unknown_plain_inbox_id_raises(self, tmp_path):
        cfg = make_cfg(tmp_path)
        plain = cfg.root / "nyxloom-trove" / "backlog.md"
        plain.write_text("- **B1 — keep.** stay.\n", encoding="utf-8")
        with pytest.raises(KeyError):
            backlog_entries.promote(cfg, "B9")

    def test_note_creates_updates_section_when_body_lacks_it(self, tmp_path):
        cfg = make_cfg(tmp_path)
        p = backlog_entries.create_entry(cfg, "custom body", body="just prose")
        backlog_entries.note(cfg, "CIU-1", "late evidence")
        text = p.read_text()
        assert "## Updates" in text
        assert "late evidence" in text

    def test_tick_before_dir_exists_is_false(self, tmp_path):
        cfg = make_cfg(tmp_path)  # [backlog_entries] declared, dir never created
        assert backlog_entries.tick_merged_entries(cfg, "t", "c") is False

    def test_empty_spine_inbox_promote_raises_keyerror(self, tmp_path):
        cfg = make_cfg(tmp_path)
        toml = cfg.root / "nyxloom-trove" / "nyxloom.toml"
        toml.write_text(toml.read_text().replace(
            'handoff_globs = ["handoff/*.md"]\n',
            'handoff_globs = ["handoff/*.md"]\nbacklog = "nyxloom-trove/4-backlog-inbox.md"\n'))
        from nyxloom.config import ProjectConfig
        cfg = ProjectConfig.load(cfg.root)
        (cfg.root / "nyxloom-trove" / "4-backlog-inbox.md").write_text(
            "---\nkind: backlog\nschema_version: 1\nitems: []\n---\n\nbody\n",
            encoding="utf-8")
        with pytest.raises(KeyError):
            backlog_entries.promote(cfg, "B1")

    def test_find_on_empty_dir_raises_keyerror(self, tmp_path):
        cfg = make_cfg(tmp_path)
        with pytest.raises(KeyError):
            backlog_entries.set_status(cfg, "CIU-1", "fixed", reason="x")


# ----- gate-driven closure: every CLI refusal/discovery branch gets an oracle -----

class TestCliRefusalsAndDiscovery:
    def test_unknown_project_exit_1(self):
        assert cli.main(["backlog", "new", "--project", "nope",
                         "t"]) == 1

    def test_cwd_discovery_without_project_flag(self, demo_with_entries, monkeypatch):
        monkeypatch.chdir(demo_with_entries.root)
        rc = cli.main(["backlog", "new", "found via cwd walk"])
        assert rc == 0
        entries = backlog_entries.load_entries(demo_with_entries)
        assert entries[0].title == "found via cwd walk"

    def test_no_config_between_cwd_and_root_exit_1(self, tmp_path, monkeypatch):
        deep = tmp_path / "a" / "b"
        deep.mkdir(parents=True)
        monkeypatch.chdir(deep)
        assert cli.main(["backlog", "new", "t"]) == 1

    def test_new_body_from_file_and_missing_file(self, demo_with_entries, tmp_path):
        body = tmp_path / "body.md"
        body.write_text("## Observed mechanism and reproduction\n\nrepro\n",
                        encoding="utf-8")
        assert cli.main(["backlog", "new", "--project", "demo", "with body",
                         "--body-from", str(body)]) == 0
        e = backlog_entries.load_entries(demo_with_entries)[0]
        assert "repro" in e.path.read_text()
        assert cli.main(["backlog", "new", "--project", "demo", "bad",
                         "--body-from", "/nonexistent/f.md"]) == 1

    def test_promote_refusals_exit_1(self, demo_with_entries):
        plain = demo_with_entries.root / "nyxloom-trove" / "backlog.md"
        plain.parent.mkdir(parents=True, exist_ok=True)
        plain.write_text("- **B1 — keep.** stay.\n", encoding="utf-8")
        # unknown id in a PRESENT inbox -> KeyError -> 1
        assert cli.main(["backlog", "promote", "--project", "demo",
                         "B99"]) == 1
        plain.unlink()
        # no inbox anywhere -> FileNotFoundError -> 1
        assert cli.main(["backlog", "promote", "--project", "demo",
                         "B1"]) == 1

    def test_note_and_set_status_missing_entry_exit_1(self, demo_with_entries):
        assert cli.main(["backlog", "note", "--project", "demo", "CIU-9",
                         "x"]) == 1
        assert cli.main(["backlog", "set-status", "--project", "demo",
                         "CIU-9", "fixed", "--reason", "r"]) == 1

    def test_set_status_merged_and_unknown_exit_2(self, demo_with_entries):
        cli.main(["backlog", "new", "--project", "demo", "an issue"])
        assert cli.main(["backlog", "set-status", "--project", "demo",
                         "CIU-1", "merged"]) == 2
        assert cli.main(["backlog", "set-status", "--project", "demo",
                         "CIU-1", "shipped", "--reason", "r"]) == 2

    def test_list_regenerates_missing_index(self, demo_with_entries):
        cli.main(["backlog", "new", "--project", "demo", "an issue"])
        (backlog_entries.resolve_dir(demo_with_entries) / "INDEX.md").unlink()
        assert cli.main(["backlog", "list", "--project", "demo"]) == 0
        assert (backlog_entries.resolve_dir(demo_with_entries) /
                "INDEX.md").exists()

    def test_show_and_index_unknown_project_exit_1(self):
        assert cli.main(["backlog", "show", "--project", "nope",
                         "CIU-1"]) == 1
        assert cli.main(["backlog", "index", "--project", "nope"]) == 1

    def test_index_without_section_exit_1(self, sample_project):
        assert cli.main(["backlog", "index", "--project", "demo"]) == 1

    def test_bare_backlog_group_prints_help_exit_2(self, capsys):
        assert cli.main(["backlog"]) == 2
        assert "new" in capsys.readouterr().err

    def test_remaining_verbs_unknown_project_exit_1(self):
        for argv in (["promote", "B1"], ["note", "CIU-1", "x"],
                     ["set-status", "CIU-1", "fixed", "--reason", "r"],
                     ["list"]):
            assert cli.main(["backlog", *argv, "--project", "nope"]) == 1

    def test_list_status_filter(self, demo_with_entries):
        cli.main(["backlog", "new", "--project", "demo", "an issue"])
        rc = cli.main(["backlog", "list", "--project", "demo",
                       "--status", "open"])
        assert rc == 0

    def test_index_happy_path_prints_path(self, demo_with_entries, capsys):
        cli.main(["backlog", "new", "--project", "demo", "an issue"])
        assert cli.main(["backlog", "index", "--project", "demo"]) == 0
        assert "INDEX.md" in capsys.readouterr().out


# ----- mutation-survivor kills: each test asserts behavior a surviving -----
# ----- mutant was silently breaking (campaign 2026-08-21, 74/87 killed) -----

class TestMutationSurvivorKills:
    def test_toplevel_schema_error_message_uses_dollar_path(self, tmp_path):
        cfg = make_cfg(tmp_path)
        d = backlog_entries.resolve_dir(cfg)
        d.mkdir(parents=True)
        (d / "CIU-1-noid.md").write_text(
            "---\nkind: backlog-entry\nschema_version: 1\ntitle: x\nstatus: open\n---\n",
            encoding="utf-8")
        findings = backlog_entries.validate_dir(cfg)
        assert any(f.message.startswith("$:") for f in findings)

    def test_index_renders_none_fields_as_empty_cells(self, tmp_path):
        cfg = make_cfg(tmp_path)
        backlog_entries.create_entry(cfg, "no optional fields")
        index = (backlog_entries.resolve_dir(cfg) / "INDEX.md").read_text()
        data_rows = [ln for ln in index.splitlines() if ln.startswith("| [")]
        assert len(data_rows) == 1
        assert "None" not in data_rows[0]

    def test_write_index_creates_nested_dir(self, tmp_path):
        cfg = make_cfg(tmp_path)
        # dir never created: write_index must mkdir -p it
        out = backlog_entries.write_index(cfg)
        assert out.exists()

    def test_allocate_id_ignores_foreign_prefixes(self, tmp_path):
        cfg = make_cfg(tmp_path)
        d = backlog_entries.resolve_dir(cfg)
        write_entry(d, "CIU-3-mine.md", valid_fm(id="CIU-3"))
        write_entry(d, "KI-9-other.md", valid_fm(id="KI-9", title="foreign"))
        assert backlog_entries.allocate_id(
            backlog_entries.load_entries(cfg), "CIU") == "CIU-4"

    def test_slug_is_derived_from_title_not_constant(self, tmp_path):
        cfg = make_cfg(tmp_path)
        p = backlog_entries.create_entry(cfg, "env generate leaks ambient network")
        assert p.name == "CIU-1-env-generate-leaks-ambient-network.md"

    def test_non_ascii_title_and_provenance_stored_verbatim(self, tmp_path):
        cfg = make_cfg(tmp_path)
        p = backlog_entries.create_entry(
            cfg, "café network — leak",
            provenance="dstdns P111 §9 F2")
        raw = p.read_text(encoding="utf-8")
        assert "café network — leak" in raw
        assert "§9 F2" in raw
        assert "\\u" not in raw

    def test_non_ascii_reason_stored_verbatim(self, tmp_path):
        cfg = make_cfg(tmp_path)
        p = backlog_entries.create_entry(cfg, "an issue")
        backlog_entries.set_status(cfg, "CIU-1", "withdrawn",
                                   reason="prémise disproved — again")
        raw = p.read_text(encoding="utf-8")
        assert "prémise disproved — again" in raw
        assert "\\u" not in raw

    def test_promote_carries_title_from_detail_first_line(self, tmp_path):
        cfg = make_cfg(tmp_path)
        plain = cfg.root / "nyxloom-trove" / "backlog.md"
        plain.write_text(
            "- **B2 — marker.** The long detail\n  continues here.\n",
            encoding="utf-8")
        e = backlog_entries.parse_entry(backlog_entries.promote(cfg, "B2"))
        assert e.title == "The long detail"

    def test_single_line_bullet_removed_entirely(self, tmp_path):
        cfg = make_cfg(tmp_path)
        plain = cfg.root / "nyxloom-trove" / "backlog.md"
        plain.write_text(
            "- **B1 — keep.** stay.\n"
            "- **B5 — tiny.** one liner.\n"
            "\n"
            "- **B2 — keep too.** stay.\n",
            encoding="utf-8")
        backlog_entries.promote(cfg, "B5")
        after = plain.read_text()
        assert "tiny" not in after
        assert "- **B1 — keep.** stay." in after
        assert "- **B2 — keep too.** stay." in after

    def test_cli_note_and_show_return_zero(self, demo_with_entries):
        cli.main(["backlog", "new", "--project", "demo", "an issue"])
        assert cli.main(["backlog", "note", "--project", "demo",
                         "CIU-1", "repro #2"]) == 0
        assert cli.main(["backlog", "show", "--project", "demo",
                         "CIU-1"]) == 0

    def test_list_status_filter_filters_content(self, demo_with_entries):
        cli.main(["backlog", "new", "--project", "demo", "open item"])
        cli.main(["backlog", "new", "--project", "demo", "fixed item"])
        cli.main(["backlog", "set-status", "--project", "demo", "CIU-2",
                  "fixed", "--reason", "done"])
        out = _capture(lambda: cli.main(["backlog", "list", "--project",
                                         "demo", "--status", "fixed"]))
        assert "| fixed |" in out
        assert "open item" not in out


    def test_write_index_creates_deeply_nested_dir(self, tmp_path):
        cfg = make_cfg(tmp_path)
        toml = cfg.root / "nyxloom-trove" / "nyxloom.toml"
        toml.write_text(toml.read_text().replace(
            '[backlog_entries]\nid_prefix = "CIU"',
            '[backlog_entries]\ndir = "nyxloom-trove/deep/nested/backlog"\n'
            'id_prefix = "CIU"'))
        from nyxloom.config import ProjectConfig
        cfg = ProjectConfig.load(cfg.root)
        out = backlog_entries.write_index(cfg)
        assert out.exists()


def _capture(fn):
    import io
    from contextlib import redirect_stdout
    buf = io.StringIO()
    with redirect_stdout(buf):
        fn()
    return buf.getvalue()
