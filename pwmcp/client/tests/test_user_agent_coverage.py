"""PWMCP-09 user_agent.py: the platform/client-hint derivation and the entrypoint helper, in process.

The first test file covers the preset table and validation; this one drives every remaining branch
(platform detection, Chromium/Edge/Firefox/custom client hints, CDP params, and ``_main`` with every
argv shape) so the module can be a judged 100% line-and-branch target.
"""

from __future__ import annotations

import json
import runpy
import sys

import pytest

from pwmcp_client import user_agent as ua

WINDOWS_CHROME = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/156.0.1.2 Safari/537.36"
WINDOWS_EDGE = WINDOWS_CHROME + " Edg/155.0.3.4"
MAC_CHROME = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 Chrome/156.0.1.2 Safari/537.36"
ANDROID_CHROME = "Mozilla/5.0 (Linux; Android 14; Pixel 8) AppleWebKit/537.36 Chrome/156.0.1.2 Mobile Safari/537.36"
LINUX_CHROME = "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 Chrome/156.0.1.2 Safari/537.36"
X11_ONLY = "Mozilla/5.0 (X11; CrOS armv7l) AppleWebKit/537.36 Chrome/156.0.1.2 Safari/537.36"
FIREFOX = "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:158.0) Gecko/20100101 Firefox/158.0"
CUSTOM = "MyBot/2.0 (+https://example.test/bot)"


@pytest.mark.parametrize(
    ("agent", "expected"),
    [
        (WINDOWS_CHROME, ("Windows", "10.0.0")),
        (MAC_CHROME, ("macOS", "14.0.0")),
        (ANDROID_CHROME, ("Android", "14.0.0")),
        (LINUX_CHROME, ("Linux", "")),
        (X11_ONLY, ("Linux", "")),
        (CUSTOM, ("", "")),
    ],
)
def test_platform_is_read_from_the_user_agent(agent: str, expected: tuple[str, str]) -> None:
    assert ua._platform(agent) == expected


def test_chrome_hints_name_chrome_with_the_uas_own_versions() -> None:
    hints = ua.client_hints(WINDOWS_CHROME)
    assert hints["brands"] == [
        {"brand": "Not=A?Brand", "version": "24"},
        {"brand": "Chromium", "version": "156"},
        {"brand": "Google Chrome", "version": "156"},
    ]
    assert hints["fullVersionList"][1:] == [
        {"brand": "Chromium", "version": "156.0.1.2"},
        {"brand": "Google Chrome", "version": "156.0.1.2"},
    ]
    assert (hints["platform"], hints["platformVersion"], hints["architecture"]) == ("Windows", "10.0.0", "x86")
    assert (hints["bitness"], hints["mobile"], hints["wow64"], hints["model"]) == ("64", False, False, "")


def test_edge_hints_name_edge_with_its_own_versions() -> None:
    hints = ua.client_hints(WINDOWS_EDGE)
    assert hints["brands"][2] == {"brand": "Microsoft Edge", "version": "155"}
    assert hints["fullVersionList"][2] == {"brand": "Microsoft Edge", "version": "155.0.3.4"}
    assert hints["fullVersionList"][1] == {"brand": "Chromium", "version": "156.0.1.2"}


def test_mobile_and_architecture_and_bitness_follow_the_string() -> None:
    android = ua.client_hints(ANDROID_CHROME)
    assert android["mobile"] is True and android["architecture"] == "" and android["bitness"] == ""
    mac = ua.client_hints(MAC_CHROME)
    assert mac["architecture"] == "x86" and mac["platform"] == "macOS" and mac["bitness"] == ""
    assert ua.client_hints(LINUX_CHROME)["bitness"] == "64"


def test_firefox_sends_no_brands() -> None:
    hints = ua.client_hints(FIREFOX)
    assert hints["brands"] == [] and hints["fullVersionList"] == [] and hints["platform"] == "Windows"


def test_a_ua_mixing_chrome_and_firefox_tokens_is_not_firefox() -> None:
    mixed = "Mozilla/5.0 (X11; Linux) Chrome/9 Firefox/9"  # a short Chrome token: no full version, not Firefox
    assert ua.client_hints(mixed) is None


@pytest.mark.parametrize("agent", [CUSTOM, "Chrome/156 (no full version)", "curl/8.0 (x)"])
def test_unrecognised_browsers_get_no_hints(agent: str) -> None:
    assert ua.client_hints(agent) is None


def test_cdp_params_carry_metadata_only_when_there_are_hints() -> None:
    with_hints = ua.cdp_override_params(WINDOWS_CHROME)
    assert with_hints["userAgent"] == WINDOWS_CHROME and with_hints["userAgentMetadata"]["platform"] == "Windows"
    assert ua.cdp_override_params(CUSTOM) == {"userAgent": CUSTOM}


# ------------------------------------------------------------------ the entrypoint helper (_main)


def run(argv: list[str], capsys: pytest.CaptureFixture[str]) -> tuple[int, str, str]:
    status = ua._main(argv)
    captured = capsys.readouterr()
    return status, captured.out, captured.err


@pytest.mark.parametrize("argv", [[], ["ua", "a", "b"], ["nope"], ["nope", "x"]])
def test_main_usage_errors(argv: list[str], capsys: pytest.CaptureFixture[str]) -> None:
    status, out, err = run(argv, capsys)
    assert (status, out) == (2, "") and "usage: pwmcp_user_agent.py" in err


def test_main_rejects_an_invalid_value(capsys: pytest.CaptureFixture[str]) -> None:
    status, out, err = run(["ua", "edge-window"], capsys)  # a typo'd preset
    assert (status, out) == (1, "") and err.startswith("FATAL: invalid PWMCP_USER_AGENT:")


def test_main_source_for_default_preset_and_custom(capsys: pytest.CaptureFixture[str]) -> None:
    assert run(["source"], capsys)[:2] == (0, "default\n")
    assert run(["source", "default"], capsys)[:2] == (0, "default\n")
    assert run(["source", "edge-windows"], capsys)[:2] == (0, "preset:edge-windows\n")
    assert run(["source", CUSTOM], capsys)[:2] == (0, "custom\n")


def test_main_ua_prints_the_string_only_when_an_override_is_active(capsys: pytest.CaptureFixture[str]) -> None:
    assert run(["ua"], capsys)[:2] == (0, "")
    assert run(["ua", "default"], capsys)[:2] == (0, "")
    status, out, _ = run(["ua", "chrome-windows"], capsys)
    assert (status, out) == (0, ua.PRESETS["chrome-windows"] + "\n")


def test_main_metadata_prints_compact_json_when_there_are_hints(capsys: pytest.CaptureFixture[str]) -> None:
    status, out, _ = run(["metadata", "edge-windows"], capsys)
    assert status == 0 and ", " not in out and ": " not in out and json.loads(out)["brands"][2]["brand"] == "Microsoft Edge"
    assert run(["metadata"], capsys)[:2] == (0, "")  # no override
    assert run(["metadata", CUSTOM], capsys)[:2] == (0, "")  # active, but no recognisable browser: no hints


def test_the_module_runs_as_a_script(monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
    monkeypatch.setattr(sys, "argv", ["pwmcp_user_agent.py", "source", "firefox-windows"])
    with pytest.raises(SystemExit) as caught:
        runpy.run_path(ua.__file__, run_name="__main__")
    assert caught.value.code == 0 and capsys.readouterr().out == "preset:firefox-windows\n"
