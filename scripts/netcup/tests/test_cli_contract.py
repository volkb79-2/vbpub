"""Black-box CLI contract and help/version side-effect tests."""

from __future__ import annotations

import json
import os
import re
import subprocess
import sys
from pathlib import Path

import pytest
from cli_extended import assert_cli_contract

NETCUP_DIR = Path(__file__).resolve().parents[1]
REPO_ROOT = NETCUP_DIR.parents[1]
CLI_LIBRARY = REPO_ROOT / "libraries" / "cli-extended" / "src"


def _invoke(script: Path, argv: list[str], cwd: Path, home: Path):
    environment = os.environ.copy()
    for name in tuple(environment):
        if name.startswith("NETCUP_SCP_API_"):
            environment.pop(name)
    environment["HOME"] = str(home)
    environment["NO_COLOR"] = "1"
    existing_path = environment.get("PYTHONPATH")
    environment["PYTHONPATH"] = str(CLI_LIBRARY) + (
        os.pathsep + existing_path if existing_path else ""
    )
    return subprocess.run(
        [sys.executable, str(script), *argv],
        cwd=cwd,
        env=environment,
        capture_output=True,
        text=True,
        check=False,
    )


@pytest.mark.parametrize(
    ("script_name", "verbs", "invalid"),
    [
        (
            "scp-api.py",
            None,
            {
                "missing power target": ("power", "on"),
                "JSON is unavailable for login": ("login", "--json"),
            },
        ),
        (
            "install-host.py",
            ("wizard", "configure", "install", "attach"),
            {
                "missing option value": ("wizard", "--server-id"),
            },
        ),
    ],
)
def test_real_executable_obeys_cli_contract_without_reading_or_changing_local_state(
    script_name, verbs, invalid, tmp_path, monkeypatch
):
    script = NETCUP_DIR / script_name
    workdir = tmp_path / "work"
    home = tmp_path / "home"
    workdir.mkdir()
    home.mkdir()
    env_file = workdir / ".env"
    env_content = (
        "# untouched by discovery\n"
        "NETCUP_SCP_API_REFRESH_TOKEN=sentinel-secret\n"
        "NETCUP_SCP_API_PROTECTED_SERVERS=not-a-vname\n"
    )
    env_file.write_text(env_content, encoding="utf-8")
    env_mode = env_file.stat().st_mode & 0o777

    def invoke(argv):
        return _invoke(script, list(argv), workdir, home)

    if verbs is None:
        # Derive the command vocabulary from the same registry used to build
        # argparse and help, rather than maintaining a second test list.
        import importlib.util

        monkeypatch.syspath_prepend(str(CLI_LIBRARY))
        monkeypatch.syspath_prepend(str(NETCUP_DIR))
        spec = importlib.util.spec_from_file_location("netcup_scp_cli_contract", script)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        verbs = tuple(module.build_cli().command_parsers)

    # Installation of cli-extended into the test process may differ from the
    # child environment; use the script's version source and identity only.
    if script_name == "scp-api.py":
        command = "scp-api"
        long_name = "Netcup Server Control Panel API client"
    else:
        command = "install-host"
        long_name = "Netcup Server Control Panel installer"
    version = (NETCUP_DIR / "VERSION").read_text(encoding="utf-8").strip()
    from cli_extended import CliIdentity

    identity = CliIdentity(
        name="NETCUP SCP",
        command=command,
        version=version,
        long_name=long_name,
    )
    known_verb_errors = (
        {
            "missing power target": "power",
            "JSON is unavailable for login": "login",
        }
        if script_name == "scp-api.py"
        else {"missing option value": "wizard"}
    )
    assert_cli_contract(
        invoke,
        identity,
        verbs,
        invalid_invocations=invalid,
        known_verb_errors=known_verb_errors,
    )

    if script_name == "scp-api.py":
        for argv in (("login", "--json"), ("--json", "login")):
            result = invoke(argv)
            assert result.returncode == 2
            assert "usage: scp-api.py login" in result.stderr
            assert "Usage: scp-api.py <verb>" not in result.stderr

    assert env_file.read_text(encoding="utf-8") == env_content
    assert env_file.stat().st_mode & 0o777 == env_mode
    assert not (home / ".ssh").exists()


def test_registered_netcup_verbs_remain_findable_in_user_guides(monkeypatch):
    import importlib.util

    monkeypatch.syspath_prepend(str(CLI_LIBRARY))
    monkeypatch.syspath_prepend(str(NETCUP_DIR))
    guide_paths = (
        NETCUP_DIR / "README.md",
        NETCUP_DIR / "DESIGN-GUIDE.md",
        REPO_ROOT / "docs" / "CONSUMERS.md",
    )
    guide_texts = {path: path.read_text(encoding="utf-8") for path in guide_paths}
    guides = "\n".join(guide_texts.values())
    command_names = ("scp-api.py", "install-host.py", "monitor-task.py")
    for path, content in guide_texts.items():
        missing_commands = [name for name in command_names if name not in content]
        assert not missing_commands, (
            f"{path} omits Netcup CLI entrypoints: {missing_commands}"
        )

    for script_name in command_names:
        script = NETCUP_DIR / script_name
        spec = importlib.util.spec_from_file_location(
            f"netcup_docs_{script.stem.replace('-', '_')}", script
        )
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        missing = [
            verb for verb in module.build_cli().command_parsers if verb not in guides
        ]
        assert not missing, f"{script_name} verbs missing from user guides: {missing}"


def test_cli_guides_have_resolving_local_markdown_links():
    guide_paths = (
        NETCUP_DIR / "README.md",
        NETCUP_DIR / "DESIGN-GUIDE.md",
        REPO_ROOT / "docs" / "CONSUMERS.md",
        REPO_ROOT / "libraries" / "cli-extended" / "SPEC.md",
        REPO_ROOT / "libraries" / "cli-extended" / "README.md",
        REPO_ROOT / "libraries" / "cli-extended" / "docs" / "DESIGN-GUIDE.md",
        REPO_ROOT / "libraries" / "cli-extended" / "docs" / "CONSUMERS.md",
    )
    link_pattern = re.compile(r"(?<!!)\[[^\]]+\]\(([^)]+)\)")
    for document in guide_paths:
        source = document.read_text(encoding="utf-8")
        for target in link_pattern.findall(source):
            destination, _, anchor = target.partition("#")
            if not destination or destination.startswith(
                ("http://", "https://", "mailto:")
            ):
                continue
            linked_path = (document.parent / destination).resolve()
            assert linked_path.exists(), f"broken link in {document}: {target}"
            if not anchor:
                continue
            target_text = (
                linked_path.read_text(encoding="utf-8")
                if linked_path.is_file()
                else (linked_path / "README.md").read_text(encoding="utf-8")
            )
            headings = re.findall(
                r"^#{1,6}\s+(.+?)\s*#*\s*$", target_text, re.MULTILINE
            )
            slugs = set()
            for heading in headings:
                slug = heading.strip().lower().replace("`", "")
                slug = re.sub(r"[^\w -]", "", slug)
                slugs.add(re.sub(r"\s+", "-", slug))
            assert anchor in slugs, f"broken anchor in {document}: {target}"


def test_json_configuration_examples_in_user_guides_load_with_current_schema(
    monkeypatch, tmp_path
):
    debian_package_root = REPO_ROOT / "scripts" / "debian-install-v2"
    monkeypatch.syspath_prepend(str(debian_package_root))
    from debian_install_v2.config import SCHEMA_VERSION, load_config

    documents = (
        NETCUP_DIR / "README.md",
        NETCUP_DIR / "DESIGN-GUIDE.md",
        REPO_ROOT / "docs" / "CONSUMERS.md",
    )
    import netcup_scp_client

    for document in documents:
        source = document.read_text(encoding="utf-8")
        examples = re.findall(r"```json\s*\n(.*?)\n```", source, re.DOTALL)
        for index, example in enumerate(examples, start=1):
            parsed = json.loads(example)
            assert parsed.get("schema_version") == SCHEMA_VERSION, (
                f"JSON config example {index} in {document} does not declare "
                f"schema_version={SCHEMA_VERSION}"
            )
            load_config(raw_json=example)

        env_examples = re.findall(r"```dotenv\s*\n(.*?)\n```", source, re.DOTALL)
        for index, example in enumerate(env_examples, start=1):
            env_path = tmp_path / f"{document.stem}-{index}.env"
            env_path.write_text(example, encoding="utf-8")
            parsed_env = netcup_scp_client._load_env_file(env_path)
            assert parsed_env, f"dotenv example {index} in {document} is empty"
