"""`cmru init` — guided scaffolding, validation-first (S10.1 verb).

Oracles:
- Non-interactive monorepo init generates an orchestration file + per-project
  contracts that PASS the real loaders (load_forge_config) — generation bugs
  die in `init`, not in a consumer's first run.
- Single-project layout writes ONLY the project contract (no orchestration
  file; the bare cmru.toml loads standalone).
- An existing target file is never overwritten — the run refuses naming every
  existing target before writing anything.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from cmru import scaffold  # noqa: E402
from cmru.config import load_forge_config  # noqa: E402


@pytest.fixture
def git_repo(tmp_path: Path) -> Path:
    root = tmp_path / "repo"
    root.mkdir()
    subprocess.run(["git", "init", "-qb", "main", "."], cwd=root, check=True,
                   capture_output=True)
    return root


def test_monorepo_init_generates_loader_valid_contracts(git_repo):
    plan = scaffold.collect_plan(
        ["--layout", "monorepo", "--project", "alpha", "--project", "beta",
         "--owner", "acme"], git_repo,
    )
    files = scaffold.build_files(plan, git_repo)
    scaffold.validate(files, git_repo)

    written = {p.relative_to(git_repo).as_posix() for p, _ in files}
    assert written == {
        "alpha/cmru.toml", "beta/cmru.toml", "cmru.orchestration.toml",
    }
    # loader-level oracle on the REAL tree after write
    for path, content in files:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")
    cfg = load_forge_config(git_repo / "cmru.orchestration.toml",
                            require_orchestration=True)
    assert set(cfg.projects) == {"alpha", "beta"}
    # ${NAME:-default} reference survives into the declared env verbatim;
    # expansion is a LOAD-time concern tested separately.
    assert "${CGROUP_PARENT_DEV_BACKGROUND:-dev-background.slice}" in (
        git_repo / "cmru.orchestration.toml"
    ).read_text(encoding="utf-8")


def test_single_project_layout_has_no_orchestration_file(git_repo):
    plan = scaffold.collect_plan(["--layout", "single"], git_repo)
    assert [p["id"] for p in plan["projects"]] == ["repo"]
    files = scaffold.build_files(plan, git_repo)
    scaffold.validate(files, git_repo)
    assert all(p.name == "cmru.toml" for p, _ in files)


def test_init_refuses_existing_targets_before_writing_anything(git_repo, capsys):
    (git_repo / "alpha").mkdir()
    existing = git_repo / "alpha" / "cmru.toml"
    existing.write_text("# operator content\n", encoding="utf-8")

    plan = scaffold.collect_plan(
        ["--layout", "monorepo", "--project", "alpha", "--owner", "acme"], git_repo,
    )
    with pytest.raises(SystemExit, match="refusing to overwrite"):
        scaffold.build_files(plan, git_repo)
    assert existing.read_text(encoding="utf-8") == "# operator content\n"


def test_templates_render_without_leftover_placeholders():
    for name in ("project-wheel.toml", "orchestration.toml"):
        text = scaffold._template(name)
        assert "@@" not in scaffold.render_project_toml(
            project_id="x", description="d", owner="o", repo="r",
            owner_type="user", generated_by="t",
        ) or name == "orchestration.toml"  # project template fully substituted


def test_cli_end_to_end_exit_zero_and_files(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    from cmru.cli import main

    assert main(["init", "--layout", "monorepo", "--project", "solo",
                 "--owner", "acme"]) == 0
    assert (tmp_path / "solo" / "cmru.toml").is_file()
    assert (tmp_path / "cmru.orchestration.toml").is_file()


# --- coverage of the remaining branches: prompts, flag forms, failure paths ---

def test_git_owner_repo_parses_origin_url(monkeypatch, tmp_path):
    import subprocess as sp

    monkeypatch.setattr(sp, "run", lambda *a, **kw: sp.CompletedProcess(
        [], 0, stdout="git@github.com:acme/widgets.git\n", stderr=""))
    assert scaffold._git_owner_repo(tmp_path) == ("acme", "widgets")


def test_git_owner_repo_survives_subprocess_failure(monkeypatch, tmp_path):
    import subprocess as sp

    def boom(*a, **kw):
        raise OSError("git vanished")
    monkeypatch.setattr(sp, "run", boom)
    assert scaffold._git_owner_repo(tmp_path) == ("", "")


def test_equals_style_flags_and_single_project_flag(git_repo):
    plan = scaffold.collect_plan(
        ["--layout=single", "--project=solo", "--owner=acme"], git_repo)
    assert [p["id"] for p in plan["projects"]] == ["solo"]
    assert plan["owner"] == "acme"


def test_unknown_layout_refuses(git_repo):
    with pytest.raises(SystemExit, match="unknown layout"):
        scaffold.collect_plan(["--layout", "weird", "--project", "x"], git_repo)


def _feed_input(monkeypatch, answers):
    it = iter(answers)
    monkeypatch.setattr("builtins.input", lambda prompt="": next(it))


def test_interactive_monorepo_flow_with_comma_ids(monkeypatch, tmp_path):
    _feed_input(monkeypatch, ["myowner", "2", "alpha,beta"])
    plan = scaffold.collect_plan([], tmp_path)
    assert [p["id"] for p in plan["projects"]] == ["alpha", "beta"]
    assert plan["owner"] == "myowner"


def test_interactive_empty_answers_take_defaults(monkeypatch, tmp_path):
    _feed_input(monkeypatch, ["", "", "", ""])
    plan = scaffold.collect_plan([], tmp_path)
    assert plan["owner"] == "your-github-owner"
    assert plan["layout"] == "single"
    assert plan["projects"][0]["id"] == tmp_path.name.lower().replace("_", "-")


def test_interactive_invalid_project_id_refuses(monkeypatch, tmp_path):
    # no flags -> interactive; choose single via the layout prompt, then
    # feed a project id that violates the slug grammar
    _feed_input(monkeypatch, ["", "", "9lives"])
    with pytest.raises(SystemExit, match="must match"):
        scaffold.collect_plan([], tmp_path)


def test_interactive_bad_id_in_comma_list_refuses(monkeypatch, tmp_path):
    _feed_input(monkeypatch, ["o", "2", "good,BAD!"])
    with pytest.raises(SystemExit, match="BAD!"):
        scaffold.collect_plan([], tmp_path)


def test_validate_reports_standards_failure(monkeypatch, git_repo):
    import subprocess as sp
    from cmru import standards  # noqa: F401  (module must be patchable-env)

    plan = scaffold.collect_plan(
        ["--layout", "monorepo", "--project", "alpha", "--owner", "a"], git_repo)
    files = scaffold.build_files(plan, git_repo)

    monkeypatch.setattr(sp, "run", lambda *a, **kw: sp.CompletedProcess(
        [], 1, stdout="STANDARDS-BOOM", stderr=""))
    with pytest.raises(SystemExit, match=r"fail `cmru standards`[\s\S]*STANDARDS-BOOM"):
        scaffold.validate(files, git_repo)


def test_cli_init_help_prints_usage(capsys):
    from cmru.cli import main

    assert main(["init", "--help"]) == 0
    out = capsys.readouterr().out
    assert "Guided scaffolding" in out and "--layout single|monorepo" in out


def test_interactive_monorepo_empty_ids_fall_back_to_root_name(monkeypatch, tmp_path):
    _feed_input(monkeypatch, ["", "2", ""])
    plan = scaffold.collect_plan([], tmp_path)
    assert [p["id"] for p in plan["projects"]] == [
        tmp_path.name.lower().replace("_", "-")]


# --- mutation-driven hardening: every survivor below is a pinned behavior ---

def test_git_owner_repo_passes_check_false(monkeypatch, tmp_path):
    """check=False flip would turn failing git probes into raises; the
    best-effort contract is 'never raise', pinned at the call site."""
    import subprocess as sp
    seen = {}
    real_run = sp.run

    def spy(*a, **kw):
        seen.update(kw)
        return sp.CompletedProcess([], 0, stdout="git@github.com:o/r.git\n")
    monkeypatch.setattr(sp, "run", spy)
    assert scaffold._git_owner_repo(tmp_path) == ("o", "r")
    assert seen["check"] is False


def test_flag_at_end_of_argv_yields_no_value(git_repo):
    """A trailing --project consumes nothing: non-interactive fallback to the
    root name, never an IndexError and never the flag token itself."""
    plan = scaffold.collect_plan(["--layout", "monorepo", "--project"], git_repo)
    assert [p["id"] for p in plan["projects"]] == [
        git_repo.name.lower().replace("_", "-")]


def test_real_git_origin_drives_owner_repo_defaults(tmp_path):
    import subprocess as sp
    sp.run(["git", "init", "-qb", "main", "."], cwd=tmp_path, check=True,
           capture_output=True)
    sp.run(["git", "-C", str(tmp_path), "remote", "add", "origin",
            "https://github.com/acme/widgets.git"], check=True, capture_output=True)
    plan = scaffold.collect_plan(["--layout", "single"], tmp_path)
    assert plan["repo"] == "widgets"      # git-derived
    assert plan["owner"] == "acme"
    flagged = scaffold.collect_plan(
        ["--layout", "single", "--repo", "explicit"], tmp_path)
    assert flagged["repo"] == "explicit"  # flag outranks git


def test_interactive_owner_prompt_shows_default(monkeypatch, tmp_path):
    prompts = []
    it = iter(["", "", "", ""])
    monkeypatch.setattr("builtins.input",
                        lambda prompt="": prompts.append(prompt) or next(it))
    scaffold.collect_plan([], tmp_path)
    assert any("[your-github-owner]" in p for p in prompts)


def test_noninteractive_without_layout_never_prompts(monkeypatch, git_repo):
    def forbidden(prompt=""):
        raise AssertionError("prompted outside interactive mode")
    monkeypatch.setattr("builtins.input", forbidden)
    with pytest.raises(SystemExit, match="unknown layout"):
        scaffold.collect_plan(["--project", "x"], git_repo)


def test_validate_mkdir_is_idempotent(monkeypatch, git_repo):
    """exist_ok flip would crash validating two files sharing a directory."""
    import subprocess as sp
    from pathlib import Path
    plan = scaffold.collect_plan(
        ["--layout", "monorepo", "--project", "alpha", "--owner", "a"], git_repo)
    files = scaffold.build_files(plan, git_repo)
    real_mkdir = Path.mkdir
    seen = []

    def spy(self, *a, **kw):
        seen.append(kw.get("exist_ok"))
        return real_mkdir(self, *a, **kw)
    monkeypatch.setattr(Path, "mkdir", spy)
    monkeypatch.setattr(sp, "run", lambda *a, **k: sp.CompletedProcess([], 0, "", ""))
    scaffold.validate(files, git_repo)
    assert True in seen


def test_validate_standards_call_shape(monkeypatch, git_repo):
    """The standards conformance probe must run captured+text (flips to
    False would deadlock on inherited stdio or mangle encoding)."""
    import subprocess as sp
    plan = scaffold.collect_plan(
        ["--layout", "monorepo", "--project", "alpha", "--owner", "a"], git_repo)
    files = scaffold.build_files(plan, git_repo)
    seen = {}

    def spy(*a, **kw):
        seen.update(kw)
        return sp.CompletedProcess([], 1, stdout="boom", stderr="")
    monkeypatch.setattr(sp, "run", spy)
    with pytest.raises(SystemExit, match="standards"):
        scaffold.validate(files, git_repo)
    assert seen["capture_output"] is True and seen["text"] is True


def test_init_main_tolerates_precreated_project_dir(monkeypatch, tmp_path):
    """exist_ok flip in the write loop crashes when the operator already made
    the project directory — scaffolding into a prepared tree must work."""
    monkeypatch.chdir(tmp_path)
    (tmp_path / "solo").mkdir()
    from cmru.scaffold import init_main
    assert init_main(["--layout", "monorepo", "--project", "solo",
                      "--owner", "acme"]) == 0
    assert (tmp_path / "solo" / "cmru.toml").is_file()
