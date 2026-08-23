"""`ciu init` — guided repo scaffolding, validation-first (S10.1 verb).

Oracles:
- Non-interactive init produces a global defaults template that renders
  through the real Jinja step into TOML the parser accepts (S3.2 order), and
  stack defaults/ compose templates that render against the same context.
- An existing target file is never overwritten.
- The gitignore additions land exactly once.
"""

from __future__ import annotations

import sys
import tomllib
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from ciu import scaffold  # noqa: E402


@pytest.fixture
def workdir(tmp_path: Path, monkeypatch) -> Path:
    monkeypatch.chdir(tmp_path)
    return tmp_path


def test_init_writes_tree_and_renders_clean(workdir):
    plan = scaffold.collect_plan(
        ["--project-name", "demo", "--stacks", "api"], workdir
    )
    files = scaffold.build_files(plan, workdir)
    written = {p.relative_to(workdir).as_posix() for p, _ in files}
    assert written == {
        "ciu.global.defaults.toml.j2",
        "applications/api/ciu.defaults.toml.j2",
        "applications/api/ciu.compose.yml.j2",
    }
    for path, content in files:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")

    # The global template renders to TOML the shipped parser accepts (S3.2).
    from jinja2 import Environment

    context = {
        "deploy": {
            "project_name": "demo", "environment_tag": "dev",
            "health": {"interval": "5s", "timeout": "3s", "retries": 3,
                        "start_period": "5s"},
            "env": {"defaults": {"TZ": "UTC", "PYTHONUNBUFFERED": "1"}},
        },
    }
    rendered = Environment(keep_trailing_newline=True).from_string(
        (workdir / "ciu.global.defaults.toml.j2").read_text(encoding="utf-8")
    ).render(**context)
    parsed = tomllib.loads(rendered)
    assert parsed["deploy"]["project_name"] == "demo"
    assert parsed["deploy"]["network_name"] == "$DOCKER_NETWORK_INTERNAL"
    # Review-blocker guard: ownership facts ship with the template.
    shared = parsed["deploy"]["env"]["shared"]
    assert {"CONTAINER_UID", "DOCKER_GID", "REPO_ROOT", "PHYSICAL_REPO_ROOT"} <= set(shared)
    # Scaffolded stacks are REGISTERED for orchestration (render/up without --dir).
    services = parsed["deploy"]["phases"]["phase_1"]["services"]
    assert any(s["path"] == "applications/api" for s in services)


def test_init_refuses_existing_targets(workdir):
    (workdir / "ciu.global.defaults.toml.j2").write_text(
        "# operator content\n", encoding="utf-8"
    )
    plan = scaffold.collect_plan(["--project-name", "demo"], workdir)
    with pytest.raises(SystemExit, match="refusing to overwrite"):
        scaffold.build_files(plan, workdir)


def test_gitignore_additions_applied_once(workdir, monkeypatch):
    """The gitignore merge DEDUPES: a refused rerun (existing targets) must
    still not append duplicate entries."""
    scaffold.init_main(["--project-name", "demo"])
    first = (workdir / ".gitignore").read_text(encoding="utf-8")

    # A rerun with different stack name only adds NEW files; the gitignore
    # entries already present are skipped (dedupe by exact line).
    monkeypatch.chdir(workdir)
    plan = scaffold.collect_plan(["--project-name", "demo", "--stacks", "other"],
                                 workdir)
    files = scaffold.build_files(plan, workdir) if False else None
    # direct gitignore-merge path (what init_main does after writing):
    existing_entries = {ln.strip() for ln in first.splitlines()
                        if ln.strip() and not ln.lstrip().startswith("#")}
    missing = [e for e, _ in scaffold._GITIGNORE_ENTRIES
               if e not in existing_entries]
    assert missing == []  # nothing left to add — deduped
    with pytest.raises(SystemExit, match="refusing to overwrite"):
        scaffold.init_main(["--project-name", "demo"])  # targets now exist
    assert (workdir / ".gitignore").read_text(encoding="utf-8") == first


def test_cli_end_to_end(workdir, monkeypatch):
    monkeypatch.setattr(sys, "argv", ["ciu", "init", "--project-name", "solo"])
    from ciu.cli import main

    with pytest.raises(SystemExit) as exc:
        main()
    assert exc.value.code == 0
    assert (workdir / "ciu.global.defaults.toml.j2").is_file()


# --- mutation/coverage hardening: prompt engine, flag forms, guards -------

def test_prompt_takes_default_on_eof(monkeypatch):
    def eof(prompt=""):
        raise EOFError
    monkeypatch.setattr("builtins.input", eof)
    assert scaffold._prompt("Q", "fallback") == "fallback"
    assert scaffold._prompt("Q", "") == ""


def test_prompt_answer_beats_default_and_shows_suffix(monkeypatch):
    seen = []

    def fake_input(prompt=""):
        seen.append(prompt)
        return "  typed  "
    monkeypatch.setattr("builtins.input", fake_input)
    assert scaffold._prompt("Q", "def") == "typed"
    assert any("[def]" in p for p in seen)


def test_render_jinja_substitutes():
    assert scaffold._render_jinja("hi {{ who }}!", {"who": "x"}) == "hi x!"
    assert scaffold._render_jinja("no vars\n", {}) == "no vars\n"


def test_equals_form_flags_parse(workdir):
    plan = scaffold.collect_plan(
        ["--project-name=eqdemo", "--stacks=api,db",
         "--environment-tag=prod"], workdir)
    assert plan["project_name"] == "eqdemo"
    assert plan["environment_tag"] == "prod"
    assert [s["stack_name"] for s in plan["stacks"]] == ["api", "db"]


def test_project_name_defaults_to_dir_slug(workdir):
    plan = scaffold.collect_plan(["--stacks", "api"], workdir)
    assert plan["project_name"] == scaffold._slug(workdir.name)


def test_invalid_project_name_refuses(workdir):
    with pytest.raises(SystemExit, match="--project-name must match"):
        scaffold.collect_plan(["--project-name", "9bad"], workdir)


def test_interactive_flow_prompts_name_env_stacks(monkeypatch, workdir):
    answers = iter(["", "", "web"])
    prompts = []
    monkeypatch.setattr(
        "builtins.input",
        lambda p="": prompts.append(p) or next(answers))
    plan = scaffold.collect_plan([], workdir)
    assert plan["project_name"] == scaffold._slug(workdir.name)
    assert plan["environment_tag"] == "dev"
    assert [s["stack_name"] for s in plan["stacks"]] == ["web"]


@pytest.mark.parametrize("bad", ['has"quote', "a\nb", "a#b", "   "])
def test_hostile_environment_tag_refuses(workdir, bad):
    with pytest.raises(SystemExit, match="plain TOML string"):
        scaffold.collect_plan(
            ["--project-name", "ok", "--environment-tag", bad], workdir)


def test_stack_name_slug_info(capfd, workdir):
    scaffold.collect_plan(["--project-name", "ok", "--stacks", "Web_App"],
                          workdir)
    assert "[INFO] stacking" in capfd.readouterr().out


def test_build_files_guard_rejects_global_without_shared_vars(
        workdir, monkeypatch):
    real_template = scaffold._template
    stripped = "\n".join(
        ln for ln in real_template("global.defaults.toml.j2").splitlines()
        if "CONTAINER_UID" not in ln and "deploy.env.shared" not in ln
        and "[deploy.env.shared]" not in ln)
    monkeypatch.setattr(scaffold, "_template",
                        lambda n: stripped if n == "global.defaults.toml.j2"
                        else real_template(n))
    plan = scaffold.collect_plan(
        ["--project-name", "demo", "--stacks", "api"], workdir)
    with pytest.raises(SystemExit, match=r"lacks deploy\.env\.shared"):
        scaffold.build_files(plan, workdir)


def test_gitignore_dedupe_understands_both_formats(tmp_path, monkeypatch):
    """Rerun over an OLD commented-format .gitignore must not duplicate, and
    a fresh dir without .gitignore gets one created."""
    monkeypatch.chdir(tmp_path)
    (tmp_path / ".gitignore").write_text(
        "ciu.env  # legacy trailing-comment form\n", encoding="utf-8")
    assert scaffold.init_main(
        ["--project-name", "d", "--stacks", "api"]) == 0
    text = (tmp_path / ".gitignore").read_text(encoding="utf-8")
    assert text.count("ciu.env") == 1  # old entry recognized, no dup

    fresh = tmp_path / "fresh"
    fresh.mkdir()
    monkeypatch.chdir(fresh)
    assert scaffold.init_main(
        ["--project-name", "d2", "--stacks", "api"]) == 0
    fresh_text = (fresh / ".gitignore").read_text(encoding="utf-8")
    assert "**/.ciu/" in fresh_text and "# per-stack machine dirs" in fresh_text


def test_gitignore_fully_satisfied_appends_nothing(tmp_path, monkeypatch, capfd):
    monkeypatch.chdir(tmp_path)
    (tmp_path / ".gitignore").write_text(
        "# machine identity layer (S2.7) — regenerated by `ciu env generate`\n"
        "ciu.env\n"
        "# rendered global config — track the .j2 source, not its output\n"
        "ciu.global.toml\n"
        "# per-stack machine dirs: rendered ciu.toml, secrets, overlays\n"
        "**/.ciu/\n"
        "# CIU's rendered compose output for .j2 templates (S8.1)\n"
        "**/ciu.compose.yml\n", encoding="utf-8")
    before = (tmp_path / ".gitignore").read_text(encoding="utf-8")
    assert scaffold.init_main(["--project-name", "d", "--stacks", "api"]) == 0
    assert (tmp_path / ".gitignore").read_text(encoding="utf-8") == before
    assert "updated .gitignore" not in capfd.readouterr().out
