from __future__ import annotations

import importlib.util
import json
import runpy
import subprocess
import sys
import urllib.error
from pathlib import Path

import pytest
from hypothesis import given, strategies as st


MODULE_PATH = Path(__file__).resolve().parents[1] / "scripts" / "resolve-playwright-version.py"
DIGEST = "sha256:" + "a" * 64
SPEC = importlib.util.spec_from_file_location("pwmcp_resolve_playwright_version", MODULE_PATH)
assert SPEC and SPEC.loader
resolver = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = resolver
SPEC.loader.exec_module(resolver)


def _payload() -> dict:
    return {
        "release": "1.0.0-r1",
        "playwright": {"python": "1.0.0", "protocol": "1.0"},
    }


def _projection_fixture(tmp_path: Path) -> dict[str, Path]:
    defaults = tmp_path / "defaults"
    override = tmp_path / "override"
    bake = tmp_path / "bake"
    dockerfile = tmp_path / "Dockerfile"
    package = tmp_path / "package.json"
    lock = tmp_path / "package-lock.json"
    contract = tmp_path / "contract.json"
    release_vars = tmp_path / "cmru.vars"
    defaults.write_text(
        'image_distro = "noble"\nplaywright_version = "1.63.0"\n'
        '[pwmcp.unified.image]\ntag = "1.63.0-r3"\n', encoding="utf-8"
    )
    override.write_text(defaults.read_text(), encoding="utf-8")
    bake.write_text(
        'variable "PLAYWRIGHT_VERSION" { default = "1.63.0" }\n'
        f'variable "PLAYWRIGHT_IMAGE_DIGEST" {{ default = "{DIGEST}" }}\n'
        'variable "PWMCP_VERSION" { default = "1.63.0-r3" }\n'
        'variable "PLAYWRIGHT_MCP_VERSION" { default = "0.0.80" }\n'
        'variable "CHROME_DEVTOOLS_MCP_VERSION" { default = "1.8.0" }\n'
        'variable "MCP_PROXY_VERSION" { default = "6.7.14" }\n'
        'variable "LIGHTHOUSE_VERSION" { default = "13.4.1" }\n', encoding="utf-8"
    )
    dockerfile.write_text(
        "ARG PLAYWRIGHT_VERSION=1.63.0\n"
        f"ARG PLAYWRIGHT_IMAGE_DIGEST={DIGEST}\n"
        "FROM mcr.microsoft.com/playwright:v${PLAYWRIGHT_VERSION}-${PLAYWRIGHT_DISTRO}@${PLAYWRIGHT_IMAGE_DIGEST}\n"
        "ARG PLAYWRIGHT_MCP_VERSION=0.0.80\n"
        "ARG CHROME_DEVTOOLS_MCP_VERSION=1.8.0\n"
        "ARG MCP_PROXY_VERSION=6.7.14\n"
        "ARG LIGHTHOUSE_VERSION=13.4.1\n", encoding="utf-8"
    )
    dependencies = {
        "@modelcontextprotocol/sdk": "1.30.0",
        "chrome-launcher": "1.2.1",
        "lighthouse": "13.4.1",
    }
    package.write_text(json.dumps({"dependencies": dependencies}), encoding="utf-8")
    lock.write_text(json.dumps({
        "lockfileVersion": 3,
        "packages": {
            "": {"dependencies": dependencies},
            **{f"node_modules/{name}": {"version": version}
               for name, version in dependencies.items()},
        },
    }), encoding="utf-8")
    contract.write_text(json.dumps({
        "release": "1.63.0-r3",
        "playwright": {"python": "1.63.0", "protocol": "1.63"},
    }), encoding="utf-8")
    return {
        "defaults": defaults, "override": override, "bake": bake,
        "dockerfile": dockerfile, "package": package, "lock": lock,
        "contract": contract, "release_vars": release_vars,
    }


def _bind_projection_fixture(
    monkeypatch: pytest.MonkeyPatch, files: dict[str, Path]
) -> None:
    for name, path in files.items():
        constant = {
            "defaults": "DEFAULTS_FILE",
            "override": "TOML_OVERRIDE_FILE",
            "bake": "BAKE_FILE",
            "dockerfile": "DOCKERFILE",
            "package": "LIGHTHOUSE_PACKAGE_FILE",
            "lock": "LIGHTHOUSE_LOCK_FILE",
            "contract": "CONTRACT_FILE",
            "release_vars": "RELEASE_VARS_FILE",
        }[name]
        monkeypatch.setattr(resolver, constant, path)


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
    def __init__(self, payload: object, headers: dict[str, str] | None = None) -> None:
        self.payload = json.dumps(payload).encode()
        self.headers = headers or {}

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


def test_fetch_json_zero_retry_budget_exercises_defensive_return(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    messages: list[str] = []
    monkeypatch.setattr(resolver, "RETRIES", 0)
    monkeypatch.setattr(resolver, "fail", messages.append)
    assert resolver._fetch_json("https://example.test", "example") == {}
    assert messages == ["example fetch failed after 0 attempts: None"]


def test_fetch_mcr_manifest_digest_reads_and_validates_header(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[tuple[str, str, str]] = []

    def fake_urlopen(request: object, timeout: float) -> _Response:
        calls.append((request.full_url, request.method, request.get_header("Accept")))
        return _Response({}, {"Docker-Content-Digest": DIGEST})

    monkeypatch.setattr(resolver.urllib.request, "urlopen", fake_urlopen)
    assert resolver.fetch_mcr_manifest_digest("1.63.0", "noble") == DIGEST
    assert calls == [(
        "https://mcr.microsoft.com/v2/playwright/manifests/v1.63.0-noble",
        "HEAD",
        "application/vnd.docker.distribution.manifest.list.v2+json, application/vnd.oci.image.index.v1+json",
    )]


@pytest.mark.parametrize("headers", [{}, {"Docker-Content-Digest": "sha256:bad"}])
def test_fetch_mcr_manifest_digest_rejects_missing_or_invalid_header(
    monkeypatch: pytest.MonkeyPatch,
    headers: dict[str, str],
) -> None:
    monkeypatch.setattr(
        resolver.urllib.request,
        "urlopen",
        lambda *_args, **_kwargs: _Response({}, headers),
    )
    with pytest.raises(SystemExit):
        resolver.fetch_mcr_manifest_digest("1.63.0", "noble")


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


@pytest.mark.parametrize("value", [None, ""])
def test_parse_release_time_ignores_missing_metadata(value: object) -> None:
    assert resolver._parse_release_time(value, "test") is None


def test_parse_release_time_normalizes_naive_and_aware_values() -> None:
    naive = resolver._parse_release_time("2026-01-01T00:00:00", "test")
    aware = resolver._parse_release_time("2026-01-01T01:00:00+01:00", "test")
    expected = resolver.datetime(2026, 1, 1, tzinfo=resolver.timezone.utc)
    assert naive == expected
    assert aware == expected


def test_parse_release_time_rejects_invalid_metadata() -> None:
    with pytest.raises(SystemExit):
        resolver._parse_release_time("not-a-time", "test")


def test_fetch_npm_release_times_filters_nonstable_and_missing_entries(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(resolver, "_fetch_json", lambda *_args: {
        "time": {
            "created": "2026-01-01T00:00:00Z",
            "1.2.3": "2026-01-02T00:00:00Z",
            "1.2.4-beta": "2026-01-03T00:00:00Z",
            "1.2.5": None,
        },
    })
    assert set(resolver.fetch_npm_release_times()) == {"1.2.3"}
    monkeypatch.setattr(resolver, "_fetch_json", lambda *_args: {"time": []})
    with pytest.raises(SystemExit):
        resolver.fetch_npm_release_times()


def test_fetch_pypi_release_times_handles_file_metadata_shapes(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(resolver, "_fetch_json", lambda *_args: {
        "releases": {
            "1.2.3": [
                {"upload_time_iso_8601": "2026-01-01T00:00:00Z"},
                {"upload_time": "2026-01-02T00:00:00Z"},
                "not-a-file",
            ],
            "1.2.4-beta": [{}],
            "1.2.5": [],
            "1.2.6": "not-a-file-list",
            "1.2.7": [{}],
        },
    })
    assert set(resolver.fetch_pypi_release_times()) == {"1.2.3"}
    monkeypatch.setattr(resolver, "_fetch_json", lambda *_args: {"releases": []})
    with pytest.raises(SystemExit):
        resolver.fetch_pypi_release_times()


def test_age_filter_refuses_when_no_version_is_old_enough() -> None:
    cutoff = resolver.datetime(2026, 1, 1, tzinfo=resolver.timezone.utc)
    with pytest.raises(SystemExit):
        resolver._filter_by_age(
            {"1.2.3"},
            {"1.2.3": resolver.datetime(2026, 1, 2, tzinfo=resolver.timezone.utc)},
            cutoff,
            "npm",
            14,
        )


@pytest.mark.parametrize(
    ("function", "payload", "message"),
    [
        (resolver.fetch_npm_versions, {"versions": []}, "versions"),
        (resolver.fetch_pypi_versions, {"releases": []}, "releases"),
        (resolver.fetch_mcr_versions, {"tags": {}}, "tags"),
    ],
)
def test_fetch_upstream_rejects_wrong_payload_shape(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    function,
    payload: object,
    message: str,
) -> None:
    monkeypatch.setattr(resolver, "_fetch_json", lambda *_args: payload)
    with pytest.raises(SystemExit):
        function("noble") if function is resolver.fetch_mcr_versions else function()
    assert message in capsys.readouterr().err


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
        f'variable "PLAYWRIGHT_IMAGE_DIGEST" {{ default = "{DIGEST}" }}\n'
        'variable "PWMCP_VERSION" { default = "old-r1" }\n',
        encoding="utf-8",
    )
    resolver.update_bake_hcl(bake, "1.2.3", "1.2.3-r4", DIGEST)
    assert 'PLAYWRIGHT_VERSION" { default = "1.2.3"' in bake.read_text()
    assert f'PLAYWRIGHT_IMAGE_DIGEST" {{ default = "{DIGEST}"' in bake.read_text()
    assert 'PWMCP_VERSION" { default = "1.2.3-r4"' in bake.read_text()

    dockerfile = tmp_path / "Dockerfile"
    dockerfile.write_text(
        "ARG PLAYWRIGHT_IMAGE_DIGEST=sha256:" + "b" * 64 + "\n"
        "FROM mcr.microsoft.com/playwright:v${PLAYWRIGHT_VERSION}-${PLAYWRIGHT_DISTRO}@${PLAYWRIGHT_IMAGE_DIGEST}\n",
        encoding="utf-8",
    )
    resolver.update_dockerfile(dockerfile, DIGEST)
    assert f"ARG PLAYWRIGHT_IMAGE_DIGEST={DIGEST}" in dockerfile.read_text()
    assert f"@{DIGEST}" in dockerfile.read_text()

    contract = tmp_path / "contract.json"
    contract.write_text(json.dumps(_payload()), encoding="utf-8")
    resolver.update_contract(contract, release="1.2.3-r4", playwright_version="1.2.3")
    updated = json.loads(contract.read_text())
    assert updated["release"] == "1.2.3-r4"
    assert updated["playwright"] == {"python": "1.2.3", "protocol": "1.2"}


def test_update_helpers_refuse_template_drift(tmp_path: Path) -> None:
    toml = tmp_path / "ciu.toml.j2"
    toml.write_text('[pwmcp]\nplaywright_version = "1.0.0"\n', encoding="utf-8")
    with pytest.raises(SystemExit):
        resolver.update_toml_j2(toml, "1.2.3", "1.2.3-r4")

    bake = tmp_path / "docker-bake.hcl"
    bake.write_text('variable "PWMCP_VERSION" { default = "old-r1" }\n', encoding="utf-8")
    with pytest.raises(SystemExit):
        resolver.update_bake_hcl(bake, "1.2.3", "1.2.3-r4", DIGEST)

    toml.write_text('[pwmcp.unified.image]\ntag = "old-r1"\n', encoding="utf-8")
    with pytest.raises(SystemExit):
        resolver.update_toml_j2(toml, "1.2.3", "1.2.3-r4")

    bake.write_text('variable "PLAYWRIGHT_VERSION" { default = "old" }\n', encoding="utf-8")
    with pytest.raises(SystemExit):
        resolver.update_bake_hcl(bake, "1.2.3", "1.2.3-r4", DIGEST)

    dockerfile.write_text("ARG PLAYWRIGHT_IMAGE_DIGEST=" + DIGEST + "\n", encoding="utf-8")
    with pytest.raises(SystemExit):
        resolver.update_dockerfile(dockerfile, DIGEST)


def test_read_bake_var_and_release_vars(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    bake = tmp_path / "docker-bake.hcl"
    bake.write_text('variable "X" { default = "value" }\n', encoding="utf-8")
    monkeypatch.setattr(resolver, "BAKE_FILE", bake)
    assert resolver._read_bake_var("X") == "value"
    with pytest.raises(SystemExit):
        resolver._read_bake_var("MISSING")

    output = tmp_path / "cmru.vars"
    monkeypatch.setattr(resolver, "RELEASE_VARS_FILE", output)
    resolver.write_release_vars("1.2.3", "noble", "1.2.3-r4", DIGEST, "0.0.82", "1.9.0", "6.7.18", "13.4.0")
    assert "PLAYWRIGHT_VERSION=1.2.3" in output.read_text()
    assert f"PLAYWRIGHT_IMAGE_DIGEST={DIGEST}" in output.read_text()
    assert "GHCR_PACKAGE_NAMES=pwmcp" in output.read_text()


def test_projection_helpers_refuse_ambiguous_values(tmp_path: Path) -> None:
    path = tmp_path / "projection"
    path.write_text('value = "one"\nvalue = "two"\n', encoding="utf-8")
    with pytest.raises(SystemExit):
        resolver._read_single_value(path, r'value\s*=\s*"([^"]*)"', "value")
    with pytest.raises(SystemExit):
        resolver._assert_same("version", {"a": "1.0.0", "b": "2.0.0"})


def test_read_current_distro_requires_authoritative_field(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    defaults = tmp_path / "defaults"
    defaults.write_text('image_distro = "jammy"\n', encoding="utf-8")
    monkeypatch.setattr(resolver, "DEFAULTS_FILE", defaults)
    assert resolver.read_current_distro() == "jammy"
    defaults.write_text("[pwmcp]\n", encoding="utf-8")
    with pytest.raises(SystemExit):
        resolver.read_current_distro()
    defaults.write_text('image_distro = ""\n', encoding="utf-8")
    with pytest.raises(SystemExit):
        resolver.read_current_distro()


def test_main_updates_all_prepared_outputs(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    defaults = tmp_path / "defaults"
    override = tmp_path / "override"
    bake = tmp_path / "bake"
    dockerfile = tmp_path / "Dockerfile"
    release_vars = tmp_path / "vars"
    contract = tmp_path / "contract"
    defaults.write_text(
        'image_distro = "noble"\nplaywright_version = "old"\n'
        '[pwmcp.unified.image]\ntag = "old"\n', encoding="utf-8"
    )
    override.write_text(defaults.read_text(), encoding="utf-8")
    bake.write_text(
        'variable "PLAYWRIGHT_VERSION" { default = "old" }\n'
        f'variable "PLAYWRIGHT_IMAGE_DIGEST" {{ default = "{DIGEST}" }}\n'
        'variable "PWMCP_VERSION" { default = "old-r1" }\n'
        'variable "PLAYWRIGHT_MCP_VERSION" { default = "0.0.82" }\n'
        'variable "CHROME_DEVTOOLS_MCP_VERSION" { default = "1.9.0" }\n'
        'variable "MCP_PROXY_VERSION" { default = "6.7.18" }\n'
        'variable "LIGHTHOUSE_VERSION" { default = "13.4.0" }\n', encoding="utf-8"
    )
    dockerfile.write_text(
        "ARG PLAYWRIGHT_IMAGE_DIGEST=" + DIGEST + "\n"
        "FROM mcr.microsoft.com/playwright:v${PLAYWRIGHT_VERSION}-${PLAYWRIGHT_DISTRO}@${PLAYWRIGHT_IMAGE_DIGEST}\n",
        encoding="utf-8",
    )
    contract.write_text(json.dumps(_payload()), encoding="utf-8")
    monkeypatch.setattr(resolver, "DEFAULTS_FILE", defaults)
    monkeypatch.setattr(resolver, "TOML_OVERRIDE_FILE", override)
    monkeypatch.setattr(resolver, "BAKE_FILE", bake)
    monkeypatch.setattr(resolver, "DOCKERFILE", dockerfile)
    monkeypatch.setattr(resolver, "RELEASE_VARS_FILE", release_vars)
    monkeypatch.setattr(resolver, "CONTRACT_FILE", contract)
    monkeypatch.setattr(resolver, "fetch_npm_versions", lambda: {"1.2.3"})
    monkeypatch.setattr(resolver, "fetch_pypi_versions", lambda: {"1.2.3"})
    monkeypatch.setattr(resolver, "fetch_mcr_versions", lambda distro: {"1.2.3"})
    old = resolver.datetime(2000, 1, 1, tzinfo=resolver.timezone.utc)
    monkeypatch.setattr(resolver, "fetch_npm_release_times", lambda: {"1.2.3": old})
    monkeypatch.setattr(resolver, "fetch_pypi_release_times", lambda: {"1.2.3": old})
    monkeypatch.setattr(resolver, "fetch_mcr_manifest_digest", lambda version, distro: DIGEST)
    monkeypatch.setattr(resolver, "compute_release_number", lambda version: 4)

    resolver.main(["--refresh"])
    assert 'playwright_version = "1.2.3"' in defaults.read_text()
    assert 'default = "1.2.3-r4"' in bake.read_text()
    assert DIGEST in bake.read_text()
    assert "PWMCP_VERSION=1.2.3-r4" in release_vars.read_text()
    assert json.loads(contract.read_text())["release"] == "1.2.3-r4"

    override.unlink()
    resolver.main(["--refresh"])


def test_check_committed_inputs_validates_projection_and_writes_vars(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    defaults = tmp_path / "defaults"
    override = tmp_path / "override"
    bake = tmp_path / "bake"
    dockerfile = tmp_path / "Dockerfile"
    package = tmp_path / "package.json"
    lock = tmp_path / "package-lock.json"
    contract = tmp_path / "contract.json"
    release_vars = tmp_path / "cmru.vars"
    defaults.write_text(
        'image_distro = "noble"\nplaywright_version = "1.63.0"\n'
        '[pwmcp.unified.image]\ntag = "1.63.0-r3"\n', encoding="utf-8"
    )
    override.write_text(defaults.read_text(), encoding="utf-8")
    bake.write_text(
        'variable "PLAYWRIGHT_VERSION" { default = "1.63.0" }\n'
        f'variable "PLAYWRIGHT_IMAGE_DIGEST" {{ default = "{DIGEST}" }}\n'
        'variable "PWMCP_VERSION" { default = "1.63.0-r3" }\n'
        'variable "PLAYWRIGHT_MCP_VERSION" { default = "0.0.80" }\n'
        'variable "CHROME_DEVTOOLS_MCP_VERSION" { default = "1.8.0" }\n'
        'variable "MCP_PROXY_VERSION" { default = "6.7.14" }\n'
        'variable "LIGHTHOUSE_VERSION" { default = "13.4.1" }\n', encoding="utf-8"
    )
    dockerfile.write_text(
        "ARG PLAYWRIGHT_VERSION=1.63.0\n"
        f"ARG PLAYWRIGHT_IMAGE_DIGEST={DIGEST}\n"
        "FROM mcr.microsoft.com/playwright:v${PLAYWRIGHT_VERSION}-${PLAYWRIGHT_DISTRO}@${PLAYWRIGHT_IMAGE_DIGEST}\n"
        "ARG PLAYWRIGHT_MCP_VERSION=0.0.80\n"
        "ARG CHROME_DEVTOOLS_MCP_VERSION=1.8.0\n"
        "ARG MCP_PROXY_VERSION=6.7.14\n"
        "ARG LIGHTHOUSE_VERSION=13.4.1\n",
        encoding="utf-8",
    )
    package_payload = {
        "dependencies": {
            "@modelcontextprotocol/sdk": "1.30.0",
            "chrome-launcher": "1.2.1",
            "lighthouse": "13.4.1",
        }
    }
    package.write_text(json.dumps(package_payload), encoding="utf-8")
    lock.write_text(
        json.dumps({
            "lockfileVersion": 3,
            "packages": {
                "": {"dependencies": package_payload["dependencies"]},
                **{
                    f"node_modules/{name}": {"version": version}
                    for name, version in package_payload["dependencies"].items()
                },
            },
        }),
        encoding="utf-8",
    )
    contract.write_text(
        json.dumps({"release": "1.63.0-r3", "playwright": {"python": "1.63.0", "protocol": "1.63"}}),
        encoding="utf-8",
    )
    monkeypatch.setattr(resolver, "DEFAULTS_FILE", defaults)
    monkeypatch.setattr(resolver, "TOML_OVERRIDE_FILE", override)
    monkeypatch.setattr(resolver, "BAKE_FILE", bake)
    monkeypatch.setattr(resolver, "DOCKERFILE", dockerfile)
    monkeypatch.setattr(resolver, "LIGHTHOUSE_PACKAGE_FILE", package)
    monkeypatch.setattr(resolver, "LIGHTHOUSE_LOCK_FILE", lock)
    monkeypatch.setattr(resolver, "CONTRACT_FILE", contract)
    monkeypatch.setattr(resolver, "RELEASE_VARS_FILE", release_vars)

    resolver.main(["--check"])

    assert "PWMCP_VERSION=1.63.0-r3" in release_vars.read_text()


def test_check_committed_inputs_refuses_contract_and_lock_drift(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    files = _projection_fixture(tmp_path)
    _bind_projection_fixture(monkeypatch, files)
    good_contract = json.loads(files["contract"].read_text())

    for bad_contract in (
        {},
        {"release": "1.63.0-r3", "playwright": []},
        {"release": "1.63.0-r3", "playwright": {"python": "1.61.0", "protocol": "1.63"}},
        {"release": "1.63.0-r3", "playwright": {"python": "1.63.0", "protocol": "1.61"}},
    ):
        files["contract"].write_text(json.dumps(bad_contract), encoding="utf-8")
        with pytest.raises(SystemExit):
            resolver.main(["--check"])
    files["contract"].write_text(json.dumps(good_contract), encoding="utf-8")

    package = json.loads(files["package"].read_text())
    lock = json.loads(files["lock"].read_text())
    package["dependencies"]["lighthouse"] = "^13.4.1"
    files["package"].write_text(json.dumps(package), encoding="utf-8")
    with pytest.raises(SystemExit):
        resolver.main(["--check"])
    package["dependencies"]["lighthouse"] = "13.4.1"
    files["package"].write_text(json.dumps(package), encoding="utf-8")

    lock["packages"][""]["dependencies"]["lighthouse"] = "13.4.0"
    files["lock"].write_text(json.dumps(lock), encoding="utf-8")
    with pytest.raises(SystemExit):
        resolver.main(["--check"])
    lock["packages"][""]["dependencies"]["lighthouse"] = "13.4.1"
    lock["packages"]["node_modules/lighthouse"]["version"] = "13.4.0"
    files["lock"].write_text(json.dumps(lock), encoding="utf-8")
    with pytest.raises(SystemExit):
        resolver.main(["--check"])

    lock["packages"]["node_modules/lighthouse"]["version"] = "13.4.1"
    lock["packages"][""]["dependencies"] = None
    files["lock"].write_text(json.dumps(lock), encoding="utf-8")
    with pytest.raises(SystemExit):
        resolver.main(["--check"])

    lock["packages"][""]["dependencies"] = package["dependencies"]
    files["lock"].write_text(json.dumps(lock), encoding="utf-8")
    files["override"].unlink()
    resolver.main(["--check"])


def test_refresh_rejects_negative_age_window() -> None:
    with pytest.raises(SystemExit):
        resolver.refresh_upstream_projection(-1)


def test_resolver_module_entrypoint_runs_committed_check(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(sys, "argv", ["resolve-playwright-version.py", "--check"])
    runpy.run_path(str(MODULE_PATH), run_name="__main__")
