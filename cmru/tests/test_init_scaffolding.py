"""Behavioral tests for the CMRU adoption wizard (``cmru init``).

The wizard asks through the cli-extended prompt API (``runtime.prompts``) only
for facts the options leave open; a complete set of options runs without a
terminal. Interactive answers are scripted with ``ScriptedPrompts``.
"""

from __future__ import annotations

import io
import subprocess
import sys
from importlib.metadata import version as installed_version
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from cli_extended import CliFailure  # noqa: E402
from cmru import scaffold  # noqa: E402
from cmru.config import load_forge_config  # noqa: E402
from tests.prompt_fakes import ScriptedPrompts  # noqa: E402


@pytest.fixture
def git_repo(tmp_path: Path) -> Path:
    root = tmp_path / "repo"
    root.mkdir()
    subprocess.run(["git", "init", "-qb", "main", "."], cwd=root, check=True, capture_output=True)
    return root


class _Tty(io.StringIO):
    """A stdin that claims to be a terminal, to exercise confirmation answers."""

    def isatty(self) -> bool:
        return True


COMPLETE = (
    "--owner", "acme", "--repo", "repo", "--owner-type", "user", "--layout", "single",
    "--kind", "python", "--artifacts", "wheel", "--release-tags", "yes",
)


def _plan(git_repo, answers, options=None):
    return scaffold.collect_plan(options or {}, git_repo, ScriptedPrompts(answers))


def test_monorepo_wizard_renders_central_facts_and_custom_commands(monkeypatch, git_repo):
    # The generated orchestration contract deliberately requires the host's
    # gates tier; tests provide that declared fact instead of weakening the
    # shipped fail-closed configuration with a fallback.
    monkeypatch.setenv("CGROUP_PARENT_DEV_GATES", "dev-gates.slice")
    (git_repo / "alpha").mkdir()
    (git_repo / "beta").mkdir()
    plan = _plan(git_repo, [
        "acme", "vbpub", "org", "monorepo", "alpha,beta",
        "alpha", "Alpha", "python", "wheel", "yes",
        "beta", "Beta", "generic", "bundle", "no", "make bundle", "make publish",
    ])
    files = scaffold.build_files(plan, git_repo)
    scaffold.validate(files, git_repo)

    for path, content in files:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")
    cfg = load_forge_config(git_repo / "cmru.orchestration.toml", require_orchestration=True)
    assert list(cfg.projects) == ["alpha", "beta"]
    assert "[github]" not in (git_repo / "alpha/cmru.toml").read_text()
    beta = (git_repo / "beta/cmru.toml").read_text()
    assert 'argv = ["make", "bundle"]' in beta
    assert 'argv = ["make", "publish"]' in beta


def test_single_project_wizard_allows_same_folder_and_keeps_standalone_facts(git_repo):
    plan = _plan(git_repo, [
        "acme", "repo", "user", "single", "", "repo", "Repo", "python", "tarball", "yes",
        "make", "make publish",
    ])
    assert plan["root"] == git_repo
    files = scaffold.build_files(plan, git_repo)
    scaffold.validate(files, git_repo)
    assert [path.name for path, _ in files] == ["cmru.toml"]
    content = files[0][1]
    assert "[github]" in content and 'artifacts = ["tarball"]' in content


def test_single_project_wizard_resolves_relative_folder_inside_root(git_repo):
    project_root = git_repo / "project"
    project_root.mkdir()

    plan = _plan(git_repo, [
        "acme", "repo", "user", "single", "project", "", "", "python", "wheel", "yes",
    ])

    assert plan["root"] == project_root
    assert plan["projects"][0]["folder"] == project_root


def test_single_project_wizard_refuses_missing_relative_folder(git_repo, capsys):
    with pytest.raises(SystemExit):
        _plan(git_repo, ["acme", "repo", "user", "single", "missing-project"])

    assert "project folder is not an existing directory" in capsys.readouterr().err


def test_wizard_refuses_existing_targets_before_writing(monkeypatch, git_repo, capsys):
    project = git_repo / "alpha"
    project.mkdir()
    existing = project / "cmru.toml"
    existing.write_text("# operator content\n", encoding="utf-8")
    plan = _plan(git_repo, [
        "acme", "repo", "user", "monorepo", "alpha", "alpha", "Alpha", "python", "wheel", "yes",
    ])
    with pytest.raises(SystemExit):
        scaffold.build_files(plan, git_repo)
    assert "refusing to overwrite" in capsys.readouterr().err
    assert existing.read_text(encoding="utf-8") == "# operator content\n"


def test_wizard_refuses_project_path_escape(git_repo, tmp_path, capsys):
    outside = tmp_path / "outside"
    outside.mkdir()
    with pytest.raises(SystemExit):
        _plan(git_repo, ["acme", "repo", "user", "single", str(outside)])
    assert "escapes CMRU root" in capsys.readouterr().err


def test_wizard_refuses_invalid_artifact_and_missing_generic_commands(git_repo, capsys):
    with pytest.raises(SystemExit):
        _plan(git_repo, ["acme", "repo", "user", "single", "", "repo", "Repo", "python", "unknown"])
    assert "artifact types" in capsys.readouterr().err

    with pytest.raises(SystemExit):
        _plan(git_repo, [
            "acme", "repo", "user", "single", "", "repo", "Repo", "python", "oci-image",
            "yes", "", "",
        ])
    assert "build command is required" in capsys.readouterr().err


def test_old_init_project_option_is_rejected(git_repo, capsys):
    assert scaffold.init_main(["--project", "old"]) == 2
    diagnostic = capsys.readouterr().err
    assert "unrecognized arguments: --project old" in diagnostic
    assert "CMRU " in diagnostic


def test_wizard_accepts_all_artifact_types_and_requires_generic_commands(git_repo):
    (git_repo / "alpha").mkdir()
    plan = _plan(git_repo, [
        "acme", "repo", "org", "monorepo", "alpha", "alpha", "Alpha", "generic", "all", "yes",
        "build all", "publish all",
    ])
    assert plan["projects"][0]["artifacts"] == ["wheel", "tarball", "bundle", "oci-image"]
    content = scaffold.build_files(plan, git_repo)[0][1]
    assert 'artifacts = ["wheel", "tarball", "bundle", "oci-image"]' in content
    assert 'argv = ["build", "all"]' in content
    assert 'argv = ["publish", "all"]' in content


def test_cli_init_help_has_version_headline(capsys):
    from cmru.cli import main

    assert main(["init", "--help"]) == 0
    out = capsys.readouterr().out
    assert out.startswith(f"CMRU {installed_version('cmru')} — Configurable Multi Release Utility\n")
    assert "Guided scaffolding" in out


# --- redesign B12 / CLI-10: non-interactive when the facts are complete -------


def _init(git_repo, *argv, stdin=""):
    stdin_stream = stdin if isinstance(stdin, io.StringIO) else io.StringIO(stdin)
    return scaffold.init_cli().run(argv=["--root", str(git_repo), *argv], stdin=stdin_stream)


def test_complete_options_dry_run_needs_no_terminal_and_does_not_hit_eof(git_repo, capsys):
    """CLI-10: all options + --dry-run + empty stdin used to die with EOFError."""
    assert _init(git_repo, *COMPLETE, "--dry-run") == 0
    out = capsys.readouterr().out
    assert "CMRU init preview:" in out
    assert "Validated plan only" in out
    assert not (git_repo / "cmru.toml").exists()


def test_complete_options_with_yes_write_the_validated_contract(git_repo, capsys):
    assert _init(git_repo, *COMPLETE, "--yes") == 0
    assert (git_repo / "cmru.toml").is_file()
    out = capsys.readouterr().out
    assert "wrote cmru.toml" in out
    # CLI-10: a bare `cmru run` really runs the steps; the hint says so.
    assert "cmru run --dry-run" in out
    assert "Dry-run the estate graph" not in out


def test_complete_options_without_yes_refuse_on_a_non_terminal(git_repo, capsys):
    assert _init(git_repo, *COMPLETE) == 2
    assert "confirmation is required" in capsys.readouterr().err
    assert not (git_repo / "cmru.toml").exists()


def test_declined_confirmation_exits_zero_and_writes_nothing(git_repo, capsys):
    """Redesign E: a declined confirmation is 0 (it used to exit 2)."""
    assert _init(git_repo, *COMPLETE, stdin=_Tty("no\n")) == 0
    captured = capsys.readouterr()
    assert "CMRU init preview:" in captured.out
    assert "Declined" in captured.err
    assert not (git_repo / "cmru.toml").exists()


def test_accepted_confirmation_writes(git_repo):
    assert _init(git_repo, *COMPLETE, stdin=_Tty("yes\n")) == 0
    assert (git_repo / "cmru.toml").is_file()


def test_missing_facts_on_a_non_terminal_ask_nothing_and_write_nothing(git_repo, capsys):
    argv = ["--owner", "acme", "--repo", "repo", "--owner-type", "user", "--layout", "single"]
    assert _init(git_repo, *argv) == 2
    assert "interactive prompts require both stdin and stdout to be terminals" in capsys.readouterr().err
    assert not (git_repo / "cmru.toml").exists()


def test_a_per_project_option_implies_the_single_layout(git_repo):
    options = {
        "owner": "acme", "repo": "repo", "owner_type": "user", "project_type": "python",
        "artifacts": "wheel", "release_tags": "no",
    }
    plan = scaffold.collect_plan(options, git_repo)  # no prompt API at all
    assert plan["layout"] == "single"
    assert plan["projects"][0]["git_tag"] is False


def test_monorepo_refuses_per_project_options(git_repo, capsys):
    with pytest.raises(SystemExit):
        scaffold.collect_plan(
            {"owner": "a", "repo": "r", "owner_type": "user", "layout": "monorepo", "project_type": "python"},
            git_repo, ScriptedPrompts([]),
        )
    assert "apply to --layout single" in capsys.readouterr().err


def test_only_the_open_facts_are_asked(git_repo):
    prompts = ScriptedPrompts(["", "", "", "wheel", "yes"])  # folder, id, description, artifacts, tags
    options = {
        "owner": "acme", "repo": "repo", "owner_type": "user", "layout": "single",
        "project_type": "python",
    }
    plan = scaffold.collect_plan(options, git_repo, prompts)
    assert plan["projects"][0]["artifacts"] == ["wheel"]
    asked = " | ".join(prompts.asked)
    assert "Artifact types" in asked and "release tags" in asked
    assert "owner" not in asked.lower() and "Project type" not in asked and "Layout" not in asked


@pytest.mark.parametrize(
    ("options", "git", "expected"),
    [
        ({}, ("", ""), ["--owner", "--repo", "--owner-type", "--layout single"]),
        ({"layout": "monorepo", "owner_type": "org"}, ("a", "r"), ["--layout single"]),
        (
            {"layout": "single", "owner_type": "user"}, ("a", "r"),
            ["--kind", "--artifacts", "--release-tags"],
        ),
        (
            {"layout": "single", "owner_type": "user", "project_type": "generic",
             "artifacts": "wheel", "release_tags": "yes"}, ("a", "r"),
            ["--build-command", "--publish-command"],
        ),
        (
            {"layout": "single", "owner_type": "user", "project_type": "python",
             "artifacts": "wheel,bundle", "release_tags": "yes", "build_command": "make"},
            ("a", "r"), ["--publish-command"],
        ),
        (
            {"layout": "single", "owner_type": "user", "project_type": "python",
             "artifacts": "generic", "release_tags": "yes", "build_command": "b",
             "publish_command": "p"}, ("a", "r"), ["--artifacts"],
        ),
        (
            {"layout": "single", "owner_type": "user", "project_type": "python",
             "artifacts": "wheel", "release_tags": "yes"}, ("a", "r"), [],
        ),
    ],
)
def test_missing_facts_names_exactly_the_open_options(options, git, expected):
    assert scaffold._missing_facts(options, *git) == expected


def test_no_prompts_refuses_a_question_without_a_default():
    prompts = scaffold._NoPrompts()
    assert prompts.text("Project id", default="demo") == "demo"
    assert prompts.select("Layout", ("single", "monorepo"), default="single") == "single"
    with pytest.raises(CliFailure, match="GitHub owner is required"):
        prompts.text("GitHub owner:")
    with pytest.raises(CliFailure, match="release tags is required"):
        prompts.confirm("CMRU creates release tags?")


def test_unbalanced_build_command_is_a_clean_refusal(git_repo, capsys):
    options = {
        "owner": "a", "repo": "r", "owner_type": "user", "layout": "single",
        "project_type": "generic", "artifacts": "wheel", "release_tags": "yes",
        "build_command": "make 'oops", "publish_command": "make publish",
    }
    with pytest.raises(SystemExit):
        scaffold.collect_plan(options, git_repo)
    assert "build command is not valid shell words" in capsys.readouterr().err


def test_generated_contracts_call_the_handler_through_the_bound_cmru_launcher(git_repo):
    """CLI-20 (scaffold side): steps use `cmru handler ...`, never `python -m`."""
    plan = scaffold.collect_plan(
        {"owner": "a", "repo": "r", "owner_type": "user", "project_type": "python",
         "artifacts": "wheel", "release_tags": "yes"},
        git_repo,
    )
    content = scaffold.build_files(plan, git_repo)[0][1]
    assert '"cmru", "handler", "wheel-build"' in content
    assert '"cmru", "handler", "wheel-publish"' in content
    assert "cmru.handlers" not in content
