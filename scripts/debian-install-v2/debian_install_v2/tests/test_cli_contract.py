from __future__ import annotations

import os
import re
import subprocess
import sys
from pathlib import Path
from types import ModuleType, SimpleNamespace

from cli_extended import CliIdentity, assert_cli_contract

from debian_install_v2.bootstrap import build_cli, main

PROJECT = Path(__file__).resolve().parents[2]
ENTRYPOINT = PROJECT / "debian-install-v2.py"
IDENTITY = CliIdentity(
    name="DEBIAN-INSTALL-V2",
    command="debian-install-v2",
    version="2",
    long_name="Debian host installer",
)


def test_executable_obeys_shared_cli_contract_without_touching_host_or_home(tmp_path):
    home = tmp_path / "home"
    home.mkdir()

    def invoke(argv):
        environment = os.environ.copy()
        environment["HOME"] = str(home)
        environment["NO_COLOR"] = "1"
        environment.pop("VBPUB_STATE_DIR", None)
        return subprocess.run(
            [sys.executable, str(ENTRYPOINT), *argv],
            cwd=tmp_path,
            env=environment,
            capture_output=True,
            text=True,
            check=False,
        )

    assert_cli_contract(
        invoke,
        IDENTITY,
        tuple(build_cli().command_parsers),
        invalid_invocations={
            "wizard requires output": ("wizard",),
            "install requires config": ("install",),
            "verify has no synthetic dry run": (
                "verify", "--config", "unused.json", "--dry-run"
            ),
            "plan is already read-only": (
                "plan", "--config", "unused.json", "--dry-run"
            ),
        },
        known_verb_errors={
            "wizard requires output": "wizard",
            "install requires config": "install",
            "verify has no synthetic dry run": "verify",
            "plan is already read-only": "plan",
        },
    )
    forced_color = invoke(["--help", "--color"])
    assert forced_color.returncode == 0
    assert "\033[" in forced_color.stdout
    forced_plain = invoke(["--help", "--no-color"])
    assert forced_plain.returncode == 0
    assert "\033[" not in forced_plain.stdout
    assert not (home / ".ssh").exists()
    assert not (home / ".config").exists()


def test_bare_invocation_prints_help_without_running_an_action(capsys):
    assert main([]) == 0
    result = capsys.readouterr()
    assert result.err == ""
    assert result.out.startswith("DEBIAN-INSTALL-V2 2 — Debian host installer\n")
    assert "Prepare settings, review the host plan" in result.out
    assert "status (--config FILE | --config-json JSON)" not in result.out
    assert "show installation status" in result.out
    assert "MODIFICATION" in result.out
    assert "EXPLORATION" in result.out


def test_wizard_help_explains_its_optional_prompt_dependency(capsys):
    assert main(["wizard", "--help"]) == 0
    assert "Questionary" in capsys.readouterr().out


def test_config_source_grammar_and_verb_description_are_library_rendered(capsys):
    app = build_cli()
    status = next(verb for verb in app.catalog.verbs if verb.name == "status")
    flattened_usage = " ".join(app.command_parsers["status"].format_usage().split())

    assert status.synopsis is None
    assert status.description == (
        "show persisted installation status, recorded steps, and paths to recent logs"
    )
    assert status.summary_description == "show installation status"
    assert status.display_synopsis == "(--config FILE | --config-json JSON)"
    catalog = app.parser.format_help()
    assert "status (--config FILE | --config-json JSON)" not in catalog
    assert "show installation status" in catalog
    assert status.description not in catalog
    assert "(--config FILE | --config-json JSON)" in flattened_usage
    assert "[--config FILE] [--config-json JSON]" not in flattened_usage

    assert main(["status"]) == 2
    refusal = capsys.readouterr().err
    assert "one of the arguments --config --config-json is required" in refusal
    assert refusal.startswith("[ERROR]")
    assert "\n\nDEBIAN-INSTALL-V2 2 — Debian host installer\n\nusage:" in refusal
    assert "\n\nusage:" in refusal

    assert main(["status", "--help"]) == 0
    help_text = capsys.readouterr().out
    assert (
        "\n\nshow persisted installation status, recorded steps, and paths to recent logs"
        "\n\nExamples:" in help_text
    )


def test_wizard_dispatch_treats_omitted_from_config_as_none(monkeypatch, tmp_path):
    import debian_install_v2.wizard as wizard_module

    observed = []
    monkeypatch.setattr(
        wizard_module,
        "run_configuration_wizard",
        lambda **kwargs: observed.append(kwargs) or 0,
    )

    assert main(["wizard", "--output", str(tmp_path / "install.json")]) == 0
    assert len(observed) == 1
    assert observed[0]["from_config"] is None


def test_missing_optional_wizard_dependency_is_a_helpful_cli_error(
    monkeypatch, capsys, tmp_path
):
    from cli_extended import CliOutput

    monkeypatch.setattr(CliOutput, "is_interactive", property(lambda self: True))
    monkeypatch.setitem(sys.modules, "questionary", None)

    result = main(["wizard", "--output", str(tmp_path / "install.json")])
    output = capsys.readouterr()

    assert result == 2
    assert "cannot load its optional Questionary" in output.err
    assert "pip install -r" in output.err
    assert "Traceback" not in output.err


def test_wizard_prompt_cancel_is_reported_as_normal_cli_cancellation(
    monkeypatch, capsys, tmp_path
):
    from cli_extended import CliOutput

    monkeypatch.setattr(CliOutput, "is_interactive", property(lambda self: True))
    questionary = ModuleType("questionary")
    questionary.checkbox = lambda *args, **kwargs: SimpleNamespace(
        ask=lambda **ask_kwargs: None
    )
    monkeypatch.setitem(sys.modules, "questionary", questionary)

    result = main(["wizard", "--output", str(tmp_path / "install.json")])
    output = capsys.readouterr()

    assert result == 130
    assert "[INFO] Cancelled." in output.err
    assert "[ERROR]" not in output.err
    assert "Traceback" not in output.err
    assert not (tmp_path / "install.json").exists()


def test_registered_verbs_are_findable_in_user_docs():
    docs = "\n".join(
        path.read_text(encoding="utf-8")
        for path in (
            PROJECT / "README.md",
            PROJECT / "docs" / "CONSUMERS.md",
            PROJECT / "docs" / "DESIGN-GUIDE.md",
        )
    )
    for verb in build_cli().command_parsers:
        assert f" {verb}" in docs or f"`{verb}`" in docs, verb


def test_documented_config_examples_parse_with_the_shipped_loader():
    from debian_install_v2.config import load_config

    for path in (
        PROJECT / "README.md",
        PROJECT / "docs" / "CONSUMERS.md",
        PROJECT / "docs" / "DESIGN-GUIDE.md",
    ):
        source = path.read_text(encoding="utf-8")
        for example in re.findall(r"```json\s*\n(.*?)\n```", source, re.DOTALL):
            config = load_config(raw_json=example)
            assert config.schema_version == 1
        for inline_example in re.findall(
            r"--config-json\s+\\\n\s+'([^']+)'", source
        ):
            config = load_config(raw_json=inline_example)
            assert config.schema_version == 1


def test_user_guide_relative_links_and_anchors_resolve():
    docs = (
        PROJECT / "README.md",
        PROJECT / "docs" / "CONSUMERS.md",
        PROJECT / "docs" / "DESIGN-GUIDE.md",
        PROJECT / "debian_install_v2" / "README.md",
    )

    def slug(heading: str) -> str:
        value = heading.lower().strip()
        value = re.sub(r"[^\w\- ]", "", value)
        return re.sub(r"\s+", "-", value)

    for source_path in docs:
        source = source_path.read_text(encoding="utf-8")
        for target in re.findall(r"\[[^\]]+\]\(([^)]+)\)", source):
            if target.startswith(("https://", "http://", "mailto:")):
                continue
            target_path, separator, fragment = target.partition("#")
            resolved = (source_path.parent / target_path).resolve() if target_path else source_path
            assert resolved.is_file(), f"{source_path}: broken link target {target!r}"
            if separator and fragment:
                target_text = resolved.read_text(encoding="utf-8")
                anchors = {
                    slug(match.group(1))
                    for match in re.finditer(r"(?m)^#{1,6}\s+(.+?)\s*#*\s*$", target_text)
                }
                assert fragment in anchors, f"{source_path}: broken anchor {target!r}"
