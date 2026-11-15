"""The packaged agent skill: one source inside the wheel, no checked-in install tree.

Cli-extended owns the `skills` verbs; what fleetd owns is the skill SOURCE. AC-19 asks
for exactly this split — the source ships as package data, validates, and is never
duplicated into a `.claude/skills` tree in the checkout (the old failure mode, where the
copy in the repo and the copy in the wheel drift apart).
"""

from __future__ import annotations

import io
import os
import tomllib
from pathlib import Path

import pytest
from cli_extended.skills import SkillError, validate_skill_source

PROJECT = Path(__file__).resolve().parents[1]
SKILLS = PROJECT / "src" / "fleetd" / "skills"
SKILL = SKILLS / "fleetd-campaigns" / "SKILL.md"
PACKAGE_INIT = PROJECT / "src" / "fleetd" / "__init__.py"


def _frontmatter() -> dict[str, str]:
    text = SKILL.read_text(encoding="utf-8")
    assert text.startswith("---\n"), "a SKILL.md must open with YAML frontmatter"
    block = text.split("---\n", 2)[1]
    return {
        key: value.strip().strip("'\"")
        for key, _, value in (line.partition(":") for line in block.splitlines() if line.strip())
    }


def test_the_skill_source_validates_with_the_library_validator():
    """`fleet skills install` validates before writing; a source that fails here fails there."""

    files = {
        str(path.relative_to(SKILLS / "fleetd-campaigns")): path.read_bytes()
        for path in (SKILLS / "fleetd-campaigns").rglob("*")
        if path.is_file()
    }
    validate_skill_source("fleetd-campaigns", files)
    assert "SKILL.md" in files


def test_the_validator_refuses_a_source_without_a_description():
    """The guard is real: the same call fails on a malformed source."""

    with pytest.raises(SkillError):
        validate_skill_source("fleetd-campaigns", {"SKILL.md": b"---\nname: fleetd-campaigns\n---\n\nbody\n"})


def test_the_skill_source_exists_with_a_name_and_a_description():
    fields = _frontmatter()
    assert fields["name"] == "fleetd-campaigns"
    assert len(fields["description"]) > 80
    assert set(fields) == {"name", "description"}


def test_the_skill_text_has_no_controller_local_path_host_address_or_credential():
    """The skill is installed on arbitrary machines; it must not encode one estate."""

    text = SKILL.read_text(encoding="utf-8")
    forbidden = (
        "/home/vscode", "vbpub-release-program", "/workspaces/vbpub",
        "r1002", "v1001", "ghcr.io", "ssh ", "Bearer", "token=", "password",
    )
    for fragment in forbidden:
        assert fragment not in text, fragment
    # It may name the documented defaults, which are environment-derived, not machine-specific.
    assert "$XDG_STATE_HOME/fleetd/campaigns" in text


def test_the_skill_is_packaged_as_package_data():
    """A skill that lives only in the checkout is absent from an installed host."""

    pyproject = tomllib.loads((PROJECT / "pyproject.toml").read_text(encoding="utf-8"))
    package_data = pyproject["tool"]["setuptools"]["package-data"]
    assert package_data["fleetd"] == ["skills/**/*"]
    assert SKILL.is_file()


def test_no_checkout_install_tree_is_the_source_of_truth():
    """A `.claude/skills` folder in the repo would be a second, drifting copy."""

    for candidate in (PROJECT / ".claude", PROJECT / ".agents", PROJECT / "src" / "fleetd" / ".claude"):
        assert not candidate.exists(), candidate
    assert list(SKILLS.rglob("SKILL.md")) == [SKILL]


def test_the_cli_registers_the_packaged_skills_group():
    from fleetd.cli import build_cli

    app = build_cli()
    assert app.skills_package == ("fleetd", "skills")
    assert set(app.delegates["skills"].handlers) == {"install", "check", "list", "uninstall"}


def test_skills_install_check_list_uninstall_run_in_a_throwaway_home(tmp_path, monkeypatch):
    """The library verbs act on a real directory: install, verify current, then remove."""

    from fleetd.cli import build_cli

    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.delenv("XDG_STATE_HOME", raising=False)
    monkeypatch.delenv("CLAUDE_CONFIG_DIR", raising=False)
    destination = tmp_path / "skills"
    app = build_cli()

    stdout, stderr = io.StringIO(), io.StringIO()
    assert app.run(argv=["skills", "install", "--dest", str(destination)], stdout=stdout, stderr=stderr) == 0
    assert "installed fleetd-campaigns" in stdout.getvalue()

    stdout = io.StringIO()
    assert app.run(argv=["skills", "check", "--dest", str(destination)], stdout=stdout, stderr=io.StringIO()) == 0
    assert "current" in stdout.getvalue()

    installed = destination / "fleetd-campaigns"
    (installed / "SKILL.md").write_text(
        (installed / "SKILL.md").read_text(encoding="utf-8") + "\nedited\n", encoding="utf-8"
    )
    stdout = io.StringIO()
    assert app.run(argv=["skills", "check", "--dest", str(destination)], stdout=stdout, stderr=io.StringIO()) == 1
    assert "modified" in stdout.getvalue()

    stdout = io.StringIO()
    assert app.run(
        argv=["skills", "install", "--dest", str(destination), "--overwrite-modified"],
        stdout=stdout, stderr=io.StringIO(),
    ) == 0
    stdout = io.StringIO()
    assert app.run(argv=["skills", "list", "--dest", str(destination), "--json"], stdout=stdout, stderr=io.StringIO()) == 0
    assert "fleetd-campaigns" in stdout.getvalue()

    stdout = io.StringIO()
    assert app.run(argv=["skills", "uninstall", "--dest", str(destination)], stdout=stdout, stderr=io.StringIO()) == 0
    assert not installed.exists()


def test_skills_list_reports_absent_before_and_after_install(tmp_path, monkeypatch):
    from fleetd.cli import build_cli

    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.delenv("XDG_STATE_HOME", raising=False)
    monkeypatch.delenv("CLAUDE_CONFIG_DIR", raising=False)
    app = build_cli()

    stdout = io.StringIO()
    assert app.run(argv=["skills", "list"], stdout=stdout, stderr=io.StringIO()) == 0
    assert "absent" in stdout.getvalue()
    assert not os.path.exists(home / ".agents" / "skills" / "fleetd-campaigns")


# Flags the skill names that belong to Run-Gate (the commands a plan renders), not to `fleet`.
_RUN_GATE_FLAGS = {"--jobs", "--only-candidates"}


def test_every_flag_and_command_the_skill_names_exists_on_the_real_cli():
    """The skill is generated knowledge for agents: an invented flag or verb must fail here.

    The flag set is the committed surface (kept current by the catalog check against the real
    registry), so a flag the skill mentions that `fleet` does not accept is refused.
    """

    import re

    surface = (PROJECT / "docs" / "cli-surface.json").read_text(encoding="utf-8")
    real_flags = set(re.findall(r'"(--[a-z][a-z-]*)"', surface))
    text = SKILL.read_text(encoding="utf-8")
    named = set(re.findall(r"(?<![\w-])(--[a-z][a-z-]*)", text))
    assert named - _RUN_GATE_FLAGS <= real_flags, sorted(named - _RUN_GATE_FLAGS - real_flags)

    verbs = {"facts": {"collect", "validate"}, "guard": {"validate"}, "plan": None, "run": None,
             "status": None, "tick": None, "collect": None, "skills": {"install", "check", "list", "uninstall"}}
    commands = [line.split() for line in text.splitlines() if line.startswith("fleet ")]
    assert commands, "the skill shows no runnable `fleet` command"
    for words in commands:
        if words[1].startswith("-"):
            continue  # `fleet --version`
        assert words[1] in verbs, words
        if verbs[words[1]] is not None:
            assert words[2] in verbs[words[1]], words
