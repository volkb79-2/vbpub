"""PWMCP-09: the one user-agent module (presets, validation, client hints, entrypoint helper)."""

from __future__ import annotations

import json
import re
import subprocess
import sys
from pathlib import Path

import pytest

from pwmcp_client import user_agent as ua

MODULE = Path(ua.__file__)
PWMCP = Path(__file__).resolve().parents[2]

EXPECTED_PRESETS = {
    "edge-windows": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/155.0.0.0 Safari/537.36 Edg/155.0.0.0",
    "chrome-windows": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/156.0.0.0 Safari/537.36",
    "firefox-windows": "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:158.0) Gecko/20100101 Firefox/158.0",
}


def test_preset_table_is_exactly_the_operator_decision() -> None:
    assert ua.PRESETS == EXPECTED_PRESETS


@pytest.mark.parametrize("name", sorted(EXPECTED_PRESETS))
def test_each_preset_resolves_with_its_source(name: str) -> None:
    resolved = ua.resolve(name)
    assert resolved.user_agent == EXPECTED_PRESETS[name]
    assert resolved.source == f"preset:{name}"
    assert resolved.as_fields() == {
        "user_agent": EXPECTED_PRESETS[name],
        "user_agent_source": f"preset:{name}",
    }


@pytest.mark.parametrize("value", [None, "", "default"])
def test_unset_and_default_mean_no_override(value: str | None) -> None:
    resolved = ua.resolve(value)
    assert resolved.user_agent is None and not resolved.active
    assert resolved.source == "default"


def test_valid_custom_string_is_used_verbatim() -> None:
    resolved = ua.resolve("MyBot/2.0 (+https://example.test/bot)")
    assert resolved.user_agent == "MyBot/2.0 (+https://example.test/bot)"
    assert resolved.source == "custom"
    assert ua.resolve("a/" + "x" * 510).source == "custom"


@pytest.mark.parametrize(
    "bad",
    ["a\nb", "a\rb", "a\r\nb", "tab\there", "café", "日本", "x" * 513, " lead", "trail ", "   ", "nul\x00"],
)
def test_invalid_custom_strings_are_rejected(bad: str) -> None:
    with pytest.raises(ua.UserAgentError):
        ua.resolve(bad)


@pytest.mark.parametrize("typo", ["edge-window", "Edge-Windows", "chrome", "EDGE-WINDOWS", "Default", "firefox"])
def test_a_mistyped_preset_is_rejected_not_taken_as_a_custom_ua(typo: str) -> None:
    with pytest.raises(ua.UserAgentError) as error:
        ua.resolve(typo)
    assert "edge-windows" in str(error.value) and "chrome-windows" in str(error.value)


def test_edge_client_hints_name_edge_not_headless_chrome() -> None:
    hints = ua.client_hints(EXPECTED_PRESETS["edge-windows"])
    assert hints is not None
    assert {b["brand"]: b["version"] for b in hints["brands"]} == {
        "Not=A?Brand": "24", "Chromium": "155", "Microsoft Edge": "155",
    }
    assert hints["platform"] == "Windows" and hints["mobile"] is False
    assert hints["bitness"] == "64"
    assert "HeadlessChrome" not in json.dumps(hints)


def test_chrome_client_hints_name_google_chrome() -> None:
    hints = ua.client_hints(EXPECTED_PRESETS["chrome-windows"])
    assert hints is not None
    assert {b["brand"] for b in hints["brands"]} == {"Not=A?Brand", "Chromium", "Google Chrome"}
    assert {b["brand"]: b["version"] for b in hints["fullVersionList"]}["Google Chrome"] == "156.0.0.0"


def test_firefox_has_empty_brands_and_unknown_custom_has_no_metadata() -> None:
    hints = ua.client_hints(EXPECTED_PRESETS["firefox-windows"])
    assert hints is not None and hints["brands"] == [] and hints["platform"] == "Windows"
    assert ua.client_hints("MyBot/2.0") is None
    assert "userAgentMetadata" not in ua.cdp_override_params("MyBot/2.0")
    params = ua.cdp_override_params(EXPECTED_PRESETS["chrome-windows"])
    assert params["userAgent"] == EXPECTED_PRESETS["chrome-windows"] and "userAgentMetadata" in params


def _helper(*argv: str) -> subprocess.CompletedProcess[str]:
    # Exactly how entrypoint.sh invokes the copy baked into the image.
    return subprocess.run([sys.executable, "-I", str(MODULE), *argv], capture_output=True, text=True, check=False)


def test_entrypoint_helper_prints_each_field_and_nothing_for_default() -> None:
    assert _helper("ua", "edge-windows").stdout == EXPECTED_PRESETS["edge-windows"] + "\n"
    assert _helper("source", "edge-windows").stdout == "preset:edge-windows\n"
    assert _helper("source", "x y").stdout == "custom\n"
    metadata = json.loads(_helper("metadata", "chrome-windows").stdout)
    assert metadata["brands"][2] == {"brand": "Google Chrome", "version": "156"}
    assert _helper("ua", "default").stdout == ""
    assert _helper("metadata", "default").stdout == ""
    assert _helper("source", "default").stdout == "default\n"
    assert _helper("metadata", "MyBot/2.0").stdout == ""  # custom, no recognisable browser token


def test_entrypoint_helper_fails_fatally_on_an_invalid_value() -> None:
    result = _helper("ua", "bad\r\nvalue")
    assert result.returncode == 1 and result.stdout == ""
    assert "FATAL: invalid PWMCP_USER_AGENT" in result.stderr
    assert _helper("bogus").returncode == 2


def test_image_carries_this_exact_module_so_there_is_no_second_table() -> None:
    dockerfile = (PWMCP / "containers" / "pwmcp" / "Dockerfile").read_text(encoding="utf-8")
    assert "COPY client/src/pwmcp_client/user_agent.py /opt/pwmcp/pwmcp_user_agent.py" in dockerfile
    entrypoint = (PWMCP / "containers" / "pwmcp" / "entrypoint.sh").read_text(encoding="utf-8")
    assert "/opt/pwmcp/pwmcp_user_agent.py" in entrypoint
    # No other source file under pwmcp/ hand-copies a preset string.
    needle = "Chrome/155.0.0.0 Safari/537.36 Edg/155.0.0.0"
    holders = [
        path.relative_to(PWMCP).as_posix()
        for path in PWMCP.rglob("*")
        if path.is_file()
        and path.suffix in {".py", ".js", ".sh", ".conf", ".j2", ".toml"}
        and "tests" not in path.relative_to(PWMCP).parts
        and ".git" not in path.parts
        and needle in path.read_text(encoding="utf-8", errors="ignore")
    ]
    assert holders == ["client/src/pwmcp_client/user_agent.py"]


def test_config_surface_is_wired_end_to_end() -> None:
    compose = (PWMCP / "ciu.compose.yml.j2").read_text(encoding="utf-8")
    defaults = (PWMCP / "ciu.defaults.toml.j2").read_text(encoding="utf-8")
    assert 'PWMCP_USER_AGENT: {{ pwmcp.unified.user_agent | default("default") | tojson | replace("$", "$$") }}' in compose
    assert re.search(r'^user_agent = "default"$', defaults, re.MULTILINE)  # OFF by default
    shared = (PWMCP / "containers" / "pwmcp" / "supervisord.shared.conf").read_text(encoding="utf-8")
    per_session = (PWMCP / "containers" / "pwmcp" / "supervisord.conf").read_text(encoding="utf-8")
    wrapper = "/usr/local/bin/pwmcp-ua-args.sh"
    assert f"command={wrapper} --user-agent= -- %(ENV_PWMCP_CHROMIUM_PATH)s --headless=new" in shared
    assert "[program:ua-override]" not in shared  # added by the entrypoint only under an override
    assert f"command={wrapper} --user-agent= -- playwright-mcp" in per_session
    assert f"command={wrapper} --chrome-arg=--user-agent= -- mcp-proxy" in per_session
    readme = (PWMCP / "README.md").read_text(encoding="utf-8")
    assert "`user_agent`" in readme and "PWMCP_USER_AGENT" in readme


def test_compose_line_renders_valid_yaml_and_escapes_dollar_for_compose(optional_module) -> None:
    jinja2 = optional_module("jinja2", "jinja2 is needed to render the compose line")
    yaml = optional_module("yaml", "PyYAML is needed to parse the rendered compose line")
    compose = (PWMCP / "ciu.compose.yml.j2").read_text(encoding="utf-8")
    (line,) = [ln.strip() for ln in compose.splitlines() if ln.strip().startswith("PWMCP_USER_AGENT:")]
    template = jinja2.Environment().from_string(line)
    custom = 'Bot/1.0 ("quoted" $HOME ${X} back\\slash)'
    rendered = template.render(pwmcp={"unified": {"user_agent": custom}})
    value = yaml.safe_load(rendered)["PWMCP_USER_AGENT"]
    # What compose hands the container after its own $$ -> $ interpolation:
    assert value.replace("$$", "$") == custom
    assert "$$HOME" in value and "$${X}" in value
    unset = yaml.safe_load(template.render(pwmcp={"unified": {}}))["PWMCP_USER_AGENT"]
    assert unset == "default"
