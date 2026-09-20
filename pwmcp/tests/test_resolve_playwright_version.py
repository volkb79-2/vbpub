from __future__ import annotations

import importlib.util
import json
import subprocess
import sys
import urllib.error
from pathlib import Path

import pytest
from hypothesis import given, strategies as st


MODULE_PATH = Path(__file__).resolve().parents[1] / "scripts" / "resolve-playwright-version.py"
SPEC = importlib.util.spec_from_file_location("pwmcp_resolve_playwright_version", MODULE_PATH)
assert SPEC and SPEC.loader
resolver = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = resolver
SPEC.loader.exec_module(resolver)


def test_resolver_uses_newest_version_all_upstreams_can_supply() -> None:
    # npm can publish first, MCR can publish second, and PyPI can lag both.
    # The release must select the last version common to all three instead of
    # emitting a Dockerfile base-image tag that does not exist yet.
    assert resolver.resolve_latest_common_version(
        {"1.61.0", "1.61.1", "1.62.0"},
        {"1.61.0"},
        {"1.61.0", "1.61.1"},
    ) == "1.61.0"


def test_mcr_versions_only_accept_exact_stable_distro_tags(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        resolver,
        "_fetch_json",
        lambda _url, _label: {
            "tags": [
                "v1.61.0-noble",
                "v1.62.0-next-canary-20260709180306-noble",
                "v1.61.1-jammy",
                "v1.61-noble",
            ]
        },
    )

    assert resolver.fetch_mcr_versions("noble") == {"1.61.0"}


def test_resolver_fails_when_no_version_is_jointly_available() -> None:
    with pytest.raises(SystemExit):
        resolver.resolve_latest_common_version({"1.62.0"}, {"1.61.0"}, {"1.61.1"})


@st.composite
def _version_numbers(draw) -> set[tuple[int, int, int]]:
    return draw(
        st.sets(
            st.tuples(
                st.integers(min_value=0, max_value=99),
                st.integers(min_value=0, max_value=99),
                st.integers(min_value=0, max_value=99),
            ),
            min_size=1,
            max_size=20,
        )
    )


@given(_version_numbers())
def test_common_version_selection_is_the_highest_stable_intersection(
    versions: set[tuple[int, int, int]],
) -> None:
    """A newly published version is usable only when every upstream agrees."""
    common = {"%d.%d.%d" % version for version in versions}
    npm = common | {"999.0.0-beta", "garbage"}
    pypi = common | {"998.0.0rc1"}
    mcr = common | {"1.2.3-next"}
    expected = max(common, key=lambda value: tuple(map(int, value.split("."))))
    assert resolver.resolve_latest_common_version(npm, pypi, mcr) == expected


class _Response:
    def __init__(self, payload: object) -> None:
        self.payload = json.dumps(payload).encode()

    def __enter__(self) -> "_Response":
        return self

    def __exit__(self, *_args: object) -> None:
        pass

    def read(self) -> bytes:
        return self.payload


def test_fetch_json_returns_decoded_payload_and_sets_request_headers(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[tuple[str, float, str]] = []

    def fake_urlopen(request: object, timeout: float) -> _Response:
        calls.append((request.full_url, timeout, request.get_header("User-agent")))
        return _Response({"ok": True})

    monkeypatch.setattr(resolver.urllib.request, "urlopen", fake_urlopen)
    assert resolver._fetch_json("https://example.test", "example") == {"ok": True}
    assert calls == [("https://example.test", 20, "pwmcp/resolve-playwright-version")]


def test_fetch_json_retries_transient_http_error(monkeypatch: pytest.MonkeyPatch) -> None:
    attempts = 0
    sleeps: list[float] = []

    def fake_urlopen(*_args: object, **_kwargs: object) -> _Response:
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            raise urllib.error.HTTPError("https://example.test", 503, "busy", {}, None)
        return _Response({"ok": "after retry"})

    monkeypatch.setattr(resolver.urllib.request, "urlopen", fake_urlopen)
    monkeypatch.setattr(resolver.time, "sleep", sleeps.append)
    assert resolver._fetch_json("https://example.test", "example") == {"ok": "after retry"}
    assert attempts == 2
    assert sleeps == [1]


def test_fetch_json_retries_network_error_then_succeeds(monkeypatch: pytest.MonkeyPatch) -> None:
    attempts = 0
    sleeps: list[float] = []

    def fake_urlopen(*_args: object, **_kwargs: object) -> _Response:
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            raise urllib.error.URLError("offline")
        return _Response({"ok": True})

    monkeypatch.setattr(resolver.urllib.request, "urlopen", fake_urlopen)
    monkeypatch.setattr(resolver.time, "sleep", sleeps.append)
    assert resolver._fetch_json("https://example.test", "example") == {"ok": True}
    assert attempts == 2
    assert sleeps == [1]


def test_fetch_json_does_not_retry_non_transient_http_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls = 0

    def fake_urlopen(*_args: object, **_kwargs: object) -> _Response:
        nonlocal calls
        calls += 1
        raise urllib.error.HTTPError("https://example.test", 404, "missing", {}, None)

    monkeypatch.setattr(resolver.urllib.request, "urlopen", fake_urlopen)
    with pytest.raises(SystemExit):
        resolver._fetch_json("https://example.test", "example")
    assert calls == 1


def test_fetch_json_fails_after_network_retry_budget(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls = 0

    def fake_urlopen(*_args: object, **_kwargs: object) -> _Response:
        nonlocal calls
        calls += 1
        raise urllib.error.URLError("offline")

    monkeypatch.setattr(resolver.urllib.request, "urlopen", fake_urlopen)
    monkeypatch.setattr(resolver.time, "sleep", lambda _seconds: None)
    with pytest.raises(SystemExit):
        resolver._fetch_json("https://example.test", "example")
    assert calls == resolver.RETRIES


@pytest.mark.parametrize(
    ("value", "expected"),
    [("1.2.3", (1, 2, 3)), (" 10.0.0 ", (10, 0, 0)), ("1.2", None), ("1.2.3-beta", None)],
)
def test_stable_version_parser(value: str, expected: tuple[int, int, int] | None) -> None:
    assert resolver._stable_version(value) == expected


def test_latest_version_rejects_only_prereleases() -> None:
    with pytest.raises(SystemExit):
        resolver._latest_version({"1.2.3rc1", "garbage"}, "npm")


def test_fetch_upstream_payload_shapes(monkeypatch: pytest.MonkeyPatch) -> None:
    payloads = iter([
        {"versions": {"1.2.3": {}, "1.2.4-beta": {}}},
        {"releases": {"1.2.3": [{"filename": "x.whl"}], "1.2.4": []}},
        {"tags": ["v1.2.3-noble", "v1.2.4-next-noble", "v1.2.3-jammy", 3]},
    ])
    monkeypatch.setattr(resolver, "_fetch_json", lambda *_args: next(payloads))
    assert resolver.fetch_npm_versions() == {"1.2.3"}
    assert resolver.fetch_pypi_versions() == {"1.2.3"}
    assert resolver.fetch_mcr_versions("noble") == {"1.2.3"}


@pytest.mark.parametrize(
    ("function", "payload", "message"),
    [
        (resolver.fetch_npm_versions, {"versions": []}, "versions"),
        (resolver.fetch_pypi_versions, {"releases": []}, "releases"),
        (resolver.fetch_mcr_versions, {"tags": {}}, "tags"),
    ],
)
def test_fetch_upstream_rejects_wrong_payload_shape(
    monkeypatch: pytest.MonkeyPatch, function, payload: object, message: str
) -> None:
    monkeypatch.setattr(resolver, "_fetch_json", lambda *_args: payload)
    with pytest.raises(SystemExit, match=message):
        function("noble") if function is resolver.fetch_mcr_versions else function()


def test_list_git_tags_handles_failure_and_success(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        resolver.subprocess,
        "run",
        lambda *args, **kwargs: subprocess.CompletedProcess([], 1, stdout=""),
    )
    assert resolver.list_git_tags("pwmcp-*") == []
    monkeypatch.setattr(
        resolver.subprocess,
        "run",
        lambda *args, **kwargs: subprocess.CompletedProcess([], 0, stdout="a\n\n b \n"),
    )
    assert resolver.list_git_tags("pwmcp-*") == ["a", "b"]


def test_compute_release_number_ignores_nonmatching_tags(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        resolver,
        "list_git_tags",
        lambda pattern: ["pwmcp-v1.2.3-r2", "pwmcp-v1.2.3-r8", "pwmcp-v1.2.3-rc9", "other"],
    )
    assert resolver.compute_release_number("1.2.3") == 9
    monkeypatch.setattr(resolver, "list_git_tags", lambda pattern: [])
    assert resolver.compute_release_number("1.2.3") == 1


def test_update_helpers_rewrite_release_inputs(tmp_path: Path) -> None:
    toml = tmp_path / "ciu.toml.j2"
    toml.write_text(
        '[pwmcp]\nplaywright_version = "1.0.0"\n\n'
        '[pwmcp.unified.image]\ntag = "old-r1"\n',
        encoding="utf-8",
    )
    resolver.update_toml_j2(toml, "1.2.3", "1.2.3-r4")
    assert 'playwright_version = "1.2.3"' in toml.read_text()
    assert 'tag = "1.2.3-r4"' in toml.read_text()

    bake = tmp_path / "docker-bake.hcl"
    bake.write_text(
        'variable "PLAYWRIGHT_VERSION" { default = "1.0.0" }\n'
        'variable "PWMCP_VERSION" { default = "old-r1" }\n',
        encoding="utf-8",
    )
    resolver.update_bake_hcl(bake, "1.2.3", "1.2.3-r4")
    assert 'PLAYWRIGHT_VERSION" { default = "1.2.3"' in bake.read_text()
    assert 'PWMCP_VERSION" { default = "1.2.3-r4"' in bake.read_text()

    contract = tmp_path / "contract.json"
    contract.write_text(json.dumps(_payload()), encoding="utf-8")
    resolver.update_contract(contract, release="1.2.3-r4", playwright_version="1.2.3")
    updated = json.loads(contract.read_text())
    assert updated["release"] == "1.2.3-r4"
    assert updated["playwright"] == {"python": "1.2.3", "protocol": "1.2"}


def test_update_helpers_refuse_template_drift(tmp_path: Path) -> None:
    toml = tmp_path / "ciu.toml.j2"
    toml.write_text('[pwmcp]\nplaywright_version = "1.0.0"\n', encoding="utf-8")
    with pytest.raises(SystemExit, match="image tag field"):
        resolver.update_toml_j2(toml, "1.2.3", "1.2.3-r4")

    bake = tmp_path / "docker-bake.hcl"
    bake.write_text('variable "PWMCP_VERSION" { default = "old-r1" }\n', encoding="utf-8")
    with pytest.raises(SystemExit, match="PLAYWRIGHT_VERSION"):
        resolver.update_bake_hcl(bake, "1.2.3", "1.2.3-r4")

    bake.write_text('variable "PLAYWRIGHT_VERSION" { default = "old" }\n', encoding="utf-8")
    with pytest.raises(SystemExit, match="PWMCP_VERSION"):
        resolver.update_bake_hcl(bake, "1.2.3", "1.2.3-r4")


def test_read_bake_var_and_release_vars(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    bake = tmp_path / "docker-bake.hcl"
    bake.write_text('variable "X" { default = "value" }\n', encoding="utf-8")
    monkeypatch.setattr(resolver, "BAKE_FILE", bake)
    assert resolver._read_bake_var("X") == "value"
    with pytest.raises(SystemExit):
        resolver._read_bake_var("MISSING")

    output = tmp_path / "cmru.vars"
    monkeypatch.setattr(resolver, "RELEASE_VARS_FILE", output)
    resolver.write_release_vars("1.2.3", "noble", "1.2.3-r4", "0.0.82", "1.9.0", "6.7.18", "13.4.0")
    assert "PLAYWRIGHT_VERSION=1.2.3" in output.read_text()
    assert "GHCR_PACKAGE_NAMES=pwmcp" in output.read_text()


def test_read_current_distro_requires_authoritative_field(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    defaults = tmp_path / "defaults"
    defaults.write_text('image_distro = "jammy"\n', encoding="utf-8")
    monkeypatch.setattr(resolver, "DEFAULTS_FILE", defaults)
    assert resolver.read_current_distro() == "jammy"
    defaults.write_text("[pwmcp]\n", encoding="utf-8")
    with pytest.raises(SystemExit, match="image_distro"):
        resolver.read_current_distro()
    defaults.write_text('image_distro = ""\n', encoding="utf-8")
    with pytest.raises(SystemExit, match="image_distro"):
        resolver.read_current_distro()


def test_main_updates_all_prepared_outputs(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    defaults = tmp_path / "defaults"
    override = tmp_path / "override"
    bake = tmp_path / "bake"
    release_vars = tmp_path / "vars"
    contract = tmp_path / "contract"
    defaults.write_text(
        'image_distro = "noble"\nplaywright_version = "old"\n'
        '[pwmcp.unified.image]\ntag = "old"\n', encoding="utf-8"
    )
    override.write_text(defaults.read_text(), encoding="utf-8")
    bake.write_text(
        'variable "PLAYWRIGHT_VERSION" { default = "old" }\n'
        'variable "PWMCP_VERSION" { default = "old-r1" }\n'
        'variable "PLAYWRIGHT_MCP_VERSION" { default = "0.0.82" }\n'
        'variable "CHROME_DEVTOOLS_MCP_VERSION" { default = "1.9.0" }\n'
        'variable "MCP_PROXY_VERSION" { default = "6.7.18" }\n'
        'variable "LIGHTHOUSE_VERSION" { default = "13.4.0" }\n', encoding="utf-8"
    )
    contract.write_text(json.dumps(_payload()), encoding="utf-8")
    monkeypatch.setattr(resolver, "DEFAULTS_FILE", defaults)
    monkeypatch.setattr(resolver, "TOML_OVERRIDE_FILE", override)
    monkeypatch.setattr(resolver, "BAKE_FILE", bake)
    monkeypatch.setattr(resolver, "RELEASE_VARS_FILE", release_vars)
    monkeypatch.setattr(resolver, "CONTRACT_FILE", contract)
    monkeypatch.setattr(resolver, "fetch_npm_versions", lambda: {"1.2.3"})
    monkeypatch.setattr(resolver, "fetch_pypi_versions", lambda: {"1.2.3"})
    monkeypatch.setattr(resolver, "fetch_mcr_versions", lambda distro: {"1.2.3"})
    monkeypatch.setattr(resolver, "compute_release_number", lambda version: 4)

    resolver.main()
    assert 'playwright_version = "1.2.3"' in defaults.read_text()
    assert 'default = "1.2.3-r4"' in bake.read_text()
    assert "PWMCP_VERSION=1.2.3-r4" in release_vars.read_text()
    assert json.loads(contract.read_text())["release"] == "1.2.3-r4"

    override.unlink()
    resolver.main()
