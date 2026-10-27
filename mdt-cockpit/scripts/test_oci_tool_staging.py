from __future__ import annotations

import hashlib
import importlib.util
import io
import os
import subprocess
import sys
import tarfile
import tempfile
import tomllib
import unittest
import zipfile
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest import mock


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import stage_tool_artifacts as staging  # noqa: E402
import manifest_sections  # noqa: E402

_resolver_spec = importlib.util.spec_from_file_location(
    "resolve_devcontainers_release", ROOT / "scripts/resolve-devcontainers-release.py"
)
assert _resolver_spec is not None and _resolver_spec.loader is not None
resolver = importlib.util.module_from_spec(_resolver_spec)
_resolver_spec.loader.exec_module(resolver)


import pytest as _pytest  # noqa: E402


def fake_version_records(versions: dict[str, str] | None = None, resolved_at: str | None = None) -> dict:
    """The `[versions.targets]` tables as cmru.toml records them after `cmru versions resolve`."""
    from datetime import datetime, timezone

    versions = versions or {"npm.copilot": "1.0.92-4", "npm.openclaw": "2026.9.8", "npm.claudelink": "1.6.1"}
    stamp = resolved_at or datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    fmt = "%Y-%m-%dT%H:%M:%SZ"
    try:
        base = datetime.strptime(stamp, fmt).replace(tzinfo=timezone.utc)
    except ValueError:
        base = datetime.now(timezone.utc)  # a deliberately unreadable resolved_at stays unreadable below
    from datetime import timedelta

    cutoff = (base - timedelta(days=14)).strftime(fmt)
    released = (base - timedelta(days=15)).strftime(fmt)
    return {
        t: {"mode": "single", "constraint": "*", "resolved": {
            "version": v, "resolved_at": stamp, "age_cutoff": cutoff,
            "sources": {"npm": {"version": v, "released_at": released, "age_source": "npm-registry-time"}}}}
        for t, v in versions.items()
    }


@_pytest.fixture(autouse=True)
def _records_from_memory(monkeypatch: "_pytest.MonkeyPatch") -> None:
    """Staging tests use an in-memory, fresh record; the committed cmru.toml has its own tests."""
    monkeypatch.setattr(staging, "_load_version_records", lambda path=None: fake_version_records())


class OciToolStagingTests(unittest.TestCase):
    def test_base_image_pull_failure_preserves_bounded_docker_diagnostic(self) -> None:
        failed = subprocess.CompletedProcess(
            ["docker", "pull", "example.invalid/image:tag"],
            1,
            stdout="",
            stderr="permission denied while trying to connect to the docker API",
        )
        with mock.patch.object(resolver.subprocess, "run", return_value=failed):
            with self.assertRaisesRegex(RuntimeError, "permission denied.*docker API"):
                resolver.pull_fresh("example.invalid/image:tag")

    def test_base_image_pull_failure_truncates_unbounded_docker_output(self) -> None:
        failed = subprocess.CompletedProcess(
            ["docker", "pull", "example.invalid/image:tag"],
            1,
            stdout="",
            stderr="x" * 3000,
        )
        with mock.patch.object(resolver.subprocess, "run", return_value=failed):
            with self.assertRaises(RuntimeError) as raised:
                resolver.pull_fresh("example.invalid/image:tag")
        message = str(raised.exception)
        self.assertLessEqual(
            len(message),
            len("Failed to pull base image: example.invalid/image:tag\n") + 2001,
        )
        self.assertTrue(message.endswith("…"))

    def test_live_discovery_payloads_are_never_artifact_cache_entries(self) -> None:
        self.assertFalse(
            staging._cacheable_download(
                "https://api.github.com/repos/example/tool/releases/latest",
                staging.STAGE_ROOT / "tmp-json-response",
            )
        )
        self.assertFalse(
            staging._cacheable_download(
                "https://example.invalid/latest",
                staging.STAGE_ROOT / "tmp-text-response",
            )
        )

    def test_verified_artifact_cache_restores_bytes_without_network(self) -> None:
        url = "https://example.invalid/releases/tool-1.2.3.tar.gz"
        payload = b"verified artifact"
        with tempfile.TemporaryDirectory() as temp:
            cache = Path(temp) / "cache"
            destination = Path(temp) / "stage" / "tool.tar.gz"
            source = Path(temp) / "source"
            source.write_bytes(payload)
            with mock.patch.object(staging, "_artifact_cache_dir", return_value=cache):
                staging._store_cached_download(url, url, source)
                self.assertEqual(staging._restore_cached_download(url, destination), url)

            self.assertEqual(destination.read_bytes(), payload)

    def test_default_artifact_cache_is_the_main_checkouts_gitignored_artifacts_dir_shared_by_worktrees(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            main = Path(temp).resolve() / "main"
            (main / "proj").mkdir(parents=True)

            def git(*args: str) -> None:
                subprocess.run(["git", "-C", str(main), *args], check=True, capture_output=True)

            git("init", "-q")
            (main / "proj" / "f").write_text("x")
            git("add", ".")
            git("-c", "user.name=t", "-c", "user.email=t@t", "commit", "-q", "-m", "x")
            worktree = Path(temp).resolve() / "wt"
            git("worktree", "add", "-q", str(worktree), "-b", "b")
            expected = main / "proj" / "artifacts" / "cache" / "tool-artifacts"
            environ = {k: v for k, v in os.environ.items() if k != "MDT_ARTIFACT_CACHE_DIR"}
            with mock.patch.dict(os.environ, environ, clear=True):
                for root in (main / "proj", worktree / "proj"):
                    with mock.patch.object(staging, "PROJECT_ROOT", root):
                        self.assertEqual(staging._artifact_cache_dir(), expected)
                        self.assertNotIn(".git", staging._artifact_cache_dir().parts)

    def test_github_asset_digest_parser_is_strict(self) -> None:
        digest = "a" * 64
        payload = {"assets": [{"name": "regctl-linux-amd64", "digest": f"sha256:{digest}"}]}
        self.assertEqual(
            staging._parse_github_asset_sha256(payload, "regctl-linux-amd64"),
            digest,
        )
        with self.assertRaises(staging.StageError):
            staging._parse_github_asset_sha256(
                {"assets": [{"name": "regctl-linux-amd64", "digest": "sha1:deadbeef"}]},
                "regctl-linux-amd64",
            )

    def test_regctl_uses_github_asset_digest_and_writes_offline_sidecar(self) -> None:
        binary = b"regctl-test-binary"
        expected = hashlib.sha256(binary).hexdigest()
        release = {
            "assets": [
                {
                    "name": "regctl-linux-amd64",
                    "digest": f"sha256:{expected}",
                }
            ]
        }

        with tempfile.TemporaryDirectory() as temp:
            downloads = Path(temp)

            def fake_download(url: str, destination: Path, **_: object) -> str:
                destination.write_bytes(binary)
                return url

            records: list[staging.StagedArtifact] = []
            with (
                mock.patch.object(staging, "DOWNLOADS_DIR", downloads),
                mock.patch.object(staging, "_fetch_json", return_value=release),
                mock.patch.object(staging, "_download", side_effect=fake_download),
            ):
                staging._stage_regctl("1.2.3", records)

            self.assertEqual(len(records), 1)
            self.assertEqual(records[0].tool, "regctl")
            self.assertEqual(records[0].sha256, expected)
            self.assertEqual(
                (downloads / "regctl-1.2.3.sha256").read_text(),
                f"{expected}  regctl-1.2.3-linux-amd64\n",
            )

    def test_crane_uses_upstream_checksum_file(self) -> None:
        archive_stream = io.BytesIO()
        with tarfile.open(fileobj=archive_stream, mode="w:gz") as archive:
            member = tarfile.TarInfo("crane")
            member.mode = 0o755
            payload = b"crane-test-binary"
            member.size = len(payload)
            archive.addfile(member, io.BytesIO(payload))
        archive_bytes = archive_stream.getvalue()
        expected = hashlib.sha256(archive_bytes).hexdigest()

        with tempfile.TemporaryDirectory() as temp:
            downloads = Path(temp)

            def fake_download(url: str, destination: Path, **_: object) -> str:
                if destination.name.endswith("checksums.txt"):
                    destination.write_text(
                        f"{expected}  go-containerregistry_Linux_x86_64.tar.gz\n"
                    )
                else:
                    destination.write_bytes(archive_bytes)
                return url

            records: list[staging.StagedArtifact] = []
            with (
                mock.patch.object(staging, "DOWNLOADS_DIR", downloads),
                mock.patch.object(staging, "_download", side_effect=fake_download),
            ):
                staging._stage_crane("1.2.3", records)

            self.assertEqual([record.tool for record in records], ["crane", "crane-checksums"])
            self.assertEqual(records[0].sha256, expected)

    def test_image_and_release_configuration_are_wired(self) -> None:
        dockerfile = (ROOT / "Dockerfile").read_text()
        bake = (ROOT / "docker-bake.hcl").read_text()
        manifest = (ROOT / "scripts" / "manifest_sections.py").read_text()
        resolver = (ROOT / "scripts" / "resolve-devcontainers-release.py").read_text()
        config = tomllib.loads((ROOT / "cmru.toml").read_text())

        for requested, resolved, binary in (
            ("CRANE_VERSION", "CRANE_VER", "crane"),
            ("REGCTL_VERSION", "REGCTL_VER", "regctl"),
        ):
            self.assertIn(f'variable "{requested}"', bake)
            self.assertIn(f"ARG {requested}", dockerfile)
            self.assertIn(resolved, dockerfile)
            self.assertIn(f'"{binary}"', manifest)
            self.assertIn(resolved, resolver)
            for step in ("build", "push"):
                self.assertIn(requested, config["steps"][step]["bake_set_vars"])

        self.assertIn(
            "regctl version 2>&1 | awk '/^VCSTag:/ "
            "{ sub(/^v/, \"\", $2); print $2; exit }'",
            dockerfile,
        )

    def test_generated_manifest_links_digest_to_immutable_source(self) -> None:
        digest = "1" * 64
        source = (
            "https://github.com/google/go-containerregistry/releases/download/"
            "v1.2.3/go-containerregistry_Linux_x86_64.tar.gz"
        )
        rendered = "\n".join(
            manifest_sections.render_tool_table(
                {"crane": "1.2.3"},
                artifact_map={
                    "crane": {
                        "sha256": digest,
                        "source_url": source,
                    }
                },
            )
        )
        self.assertIn("`1.2.3`", rendered)
        self.assertIn(f"[`sha256:{digest[:24]}…`]({source})", rendered)


if __name__ == "__main__":
    unittest.main()


# ── Claude Code version: the exact `latest` or the explicit pin, recorded (R16) ──
#
# Claude is user-updatable, so the image follows upstream and pins/records what it shipped; there is
# deliberately NO freshness window for it.

import pytest  # noqa: E402


def test_claude_latest_resolves_to_the_exact_dist_tag_version_not_the_word_latest(monkeypatch: pytest.MonkeyPatch) -> None:
    urls: list[str] = []

    def fake_fetch(url: str, **_kw) -> dict:
        urls.append(url)
        return {"version": "2.1.302"}

    monkeypatch.setattr(staging, "_fetch_json", fake_fetch)

    assert staging._resolve_claude_code_version("latest") == "2.1.302"
    assert staging._resolve_claude_code_version(None) == "2.1.302"
    assert urls == ["https://registry.npmjs.org/@anthropic-ai/claude-code/latest"] * 2


def test_a_freshly_published_claude_release_is_accepted_there_is_no_age_window(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(staging, "_fetch_json", lambda url, **k: {"version": url.rsplit("/", 1)[-1]})

    assert staging._resolve_claude_code_version("2.1.302") == "2.1.302"


def test_an_explicit_claude_pin_must_be_a_published_version(monkeypatch: pytest.MonkeyPatch) -> None:
    def missing(url: str, **_kw) -> dict:
        raise staging.StageError("HTTP 404")

    monkeypatch.setattr(staging, "_fetch_json", missing)
    with pytest.raises(staging.StageError, match="not a published"):
        staging._resolve_claude_code_version("9.9.9")
    monkeypatch.setattr(staging, "_fetch_json", lambda *a, **k: {"version": "something-else"})
    with pytest.raises(staging.StageError, match="not a published"):
        staging._resolve_claude_code_version("9.9.9")
    with pytest.raises(staging.StageError, match="Unsupported"):
        staging._resolve_claude_code_version("two")


def _stub_other_resolvers(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(staging, "_resolve_npm_version", lambda requested, package: f"npm-{package}")
    monkeypatch.setattr(staging, "_resolve_codex_version", lambda _v: "codex-x")
    monkeypatch.setattr(staging, "_resolve_pypi_version", lambda _v, package: f"pypi-{package}")
    monkeypatch.setattr(staging, "_resolve_version", lambda _v, repo: f"gh-{repo}")
    monkeypatch.setattr(staging, "_resolve_latest_aged_release", lambda repo, **_k: f"aged-{repo}")
    monkeypatch.setattr(staging, "_resolve_sss_commit", lambda _v: "e" * 40)


def test_the_resolved_claude_version_is_what_staging_records(monkeypatch: pytest.MonkeyPatch) -> None:
    _stub_other_resolvers(monkeypatch)
    monkeypatch.setattr(staging, "_resolve_claude_code_version", lambda requested: "2.1.302")
    monkeypatch.delenv("INSTALL_CLAUDE_CODE", raising=False)

    resolved = staging._resolve_versions()

    assert resolved["CLAUDE_CODE_VER"] == "2.1.302" and "latest" not in resolved["CLAUDE_CODE_VER"]


@pytest.mark.parametrize("value", ["false", "FALSE", "0", "no", "off"])
def test_claude_resolution_is_skipped_and_logged_when_claude_is_not_installed(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], value: str
) -> None:
    _stub_other_resolvers(monkeypatch)
    monkeypatch.setattr(staging, "_resolve_claude_code_version", lambda requested: pytest.fail("no registry lookup"))
    monkeypatch.setenv("INSTALL_CLAUDE_CODE", value)

    resolved = staging._resolve_versions()

    assert "CLAUDE_CODE_VER" not in resolved
    assert "skipping Claude Code version resolution" in capsys.readouterr().err


def test_the_five_user_updatable_tools_are_recorded_by_their_exact_resolved_version(monkeypatch: pytest.MonkeyPatch) -> None:
    _stub_other_resolvers(monkeypatch)
    monkeypatch.setattr(staging, "_resolve_claude_code_version", lambda requested: "2.1.302")
    monkeypatch.delenv("INSTALL_CLAUDE_CODE", raising=False)

    resolved = staging._resolve_versions()

    assert {k: resolved[k] for k in ("CODEX_VER", "CLAUDE_CODE_VER", "REASONIX_VER", "OPENCODE_VER", "PI_VER")} == {
        "CODEX_VER": "codex-x", "CLAUDE_CODE_VER": "2.1.302", "REASONIX_VER": "npm-reasonix",
        "OPENCODE_VER": "npm-opencode-ai", "PI_VER": "npm-@earendil-works/pi-coding-agent",
    }
    display = dict(resolver.TOOL_VERSION_DISPLAY_ORDER)
    for tool, key in (("codex", "CODEX_VER"), ("claude", "CLAUDE_CODE_VER"), ("reasonix", "REASONIX_VER"),
                      ("opencode", "OPENCODE_VER"), ("pi", "PI_VER")):
        assert display[tool] == key, f"{tool} is not listed in the image manifest's version table"


def test_claude_is_no_longer_staged_as_a_native_binary() -> None:
    source = (Path(__file__).resolve().parent / "stage_tool_artifacts.py").read_text(encoding="utf-8")
    assert "_stage_claude_code" not in source and "downloads.claude.ai" not in source


# ── M2: an explicit pin of any R16 npm tool must be a published version ──

@pytest.mark.parametrize("variable,package", [
    ("REASONIX_VERSION", "reasonix"),
    ("OPENCODE_VERSION", "opencode-ai"),
    ("PI_VERSION", "@earendil-works/pi-coding-agent"),
])
def test_an_explicit_npm_pin_must_be_published(monkeypatch: pytest.MonkeyPatch, variable: str, package: str) -> None:
    _stub_other_resolvers(monkeypatch)
    monkeypatch.setattr(staging, "_resolve_claude_code_version", lambda requested: "2.1.302")
    monkeypatch.delenv("INSTALL_CLAUDE_CODE", raising=False)
    monkeypatch.setenv(variable, "9.9.9")

    def fetch(url: str, **_kw) -> dict:
        assert url == f"https://registry.npmjs.org/{package}/9.9.9"
        raise staging.StageError("HTTP 404")

    monkeypatch.setattr(staging, "_fetch_json", fetch)
    monkeypatch.setattr(staging, "_resolve_npm_version", staging._resolve_npm_version)  # real latest path unused

    with pytest.raises(staging.StageError, match="not a published"):
        staging._resolve_versions()


# ── M3: one shared "install enabled" definition ──

@pytest.mark.parametrize("value", [None, "", " ", "true", "1", "yes", "on", "false", "FALSE", " 0 ", "no", "Off"])
def test_install_enabled_has_one_definition_shared_by_staging_resolver_and_installer(
    monkeypatch: pytest.MonkeyPatch, value: str | None
) -> None:
    import importlib.util
    import install_ai_cli_tools as installer

    spec = importlib.util.spec_from_file_location("resolve_release", Path(__file__).parent / "resolve-devcontainers-release.py")
    resolver_module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(resolver_module)
    assert resolver_module.install_enabled is staging.install_enabled, "the resolver must import, not redefine"
    if value is None:
        monkeypatch.delenv("INSTALL_X", raising=False)
    else:
        monkeypatch.setenv("INSTALL_X", value)

    assert staging.install_enabled("INSTALL_X") == installer.is_enabled("INSTALL_X", True)


def test_dust_fails_closed_without_an_api_digest_and_before_any_download(tmp_path: Path, monkeypatch: _pytest.MonkeyPatch) -> None:
    _api(monkeypatch, {"/repos/bootandy/dust/releases/tags/": {"assets": [{"name": "dust-v9.9.9-x86_64-unknown-linux-musl.tar.gz"}]}})
    with mock.patch.object(staging, "DOWNLOADS_DIR", tmp_path), mock.patch.object(staging, "_download", side_effect=AssertionError("no download")):
        with _pytest.raises(staging.StageError, match="no sha256 digest"):
            staging._stage_dust("9.9.9", [])


def _dust_release(version: str, digest: str) -> dict:
    return {"assets": [{"name": f"dust-v{version}-x86_64-unknown-linux-musl.tar.gz", "digest": f"sha256:{digest}"}]}


def test_dust_rejects_an_archive_that_differs_from_the_api_digest(tmp_path: Path, monkeypatch: _pytest.MonkeyPatch) -> None:
    _api(monkeypatch, {"/repos/bootandy/dust/releases/tags/v9.9.9": _dust_release("9.9.9", "0" * 64)})
    fake = _fake_download_writing(b"tampered archive")
    with mock.patch.object(staging, "DOWNLOADS_DIR", tmp_path), mock.patch.object(staging, "_download", side_effect=fake):
        with _pytest.raises(staging.StageError, match="dust checksum mismatch"):
            staging._stage_dust("9.9.9", [])


def test_dust_verifies_against_the_api_digest_and_writes_the_sidecar(tmp_path: Path, monkeypatch: _pytest.MonkeyPatch) -> None:
    buffer = io.BytesIO()
    with tarfile.open(fileobj=buffer, mode="w:gz") as archive:
        info = tarfile.TarInfo("dust-v9.9.9-x86_64-unknown-linux-musl/dust")
        info.size = 1
        archive.addfile(info, io.BytesIO(b"x"))
    content = buffer.getvalue()
    digest = hashlib.sha256(content).hexdigest()
    _api(monkeypatch, {"/repos/bootandy/dust/releases/tags/v9.9.9": _dust_release("9.9.9", digest)})
    fake = _fake_download_writing(content)
    records: list = []
    with mock.patch.object(staging, "DOWNLOADS_DIR", tmp_path), mock.patch.object(staging, "_download", side_effect=fake):
        staging._stage_dust("9.9.9", records)
    assert (tmp_path / "dust-9.9.9.sha256").read_text() == f"{digest}  dust-9.9.9.tar.gz\n"
    assert "asset digest" in records[0].verification


def test_gdu_rejects_a_download_that_differs_from_the_sums_file_and_fails_closed_without_its_line(tmp_path: Path) -> None:
    with mock.patch.object(staging, "DOWNLOADS_DIR", tmp_path):
        with mock.patch.object(staging, "_download", side_effect=_mismatching_download(b"tampered", "gdu_linux_amd64_static.tgz")):
            with _pytest.raises(staging.StageError, match="gdu checksum mismatch"):
                staging._stage_gdu("9.9.9", [])
        with mock.patch.object(staging, "_download", side_effect=_fake_download_writing(b"x", "other-file")):
            with _pytest.raises(staging.StageError, match="Unable to find checksum entry"):
                staging._stage_gdu("9.9.9", [])


def test_gdu_and_dust_versions_resolve_through_the_aged_resolver_and_latest_means_aged(monkeypatch: _pytest.MonkeyPatch) -> None:
    _api(monkeypatch, {"/repos/dundee/gdu/releases?": [_rel("v5.40.0", 2), _rel("v5.39.0", 20)]})
    for variable in ("GDU_VERSION", "DUST_VERSION"):
        monkeypatch.delenv(variable, raising=False)
    assert staging._resolve_aged_version(None, "dundee/gdu", "GDU_VERSION", now=NOW) == "5.39.0"
    assert staging._resolve_aged_version("latest", "dundee/gdu", "GDU_VERSION", now=NOW) == "5.39.0"


def _fake_download_writing(content: bytes, sums_for: str | None = None):
    """A `_download` stand-in: checksum files claim the digest of `content` for `sums_for`; everything else is `content`."""

    def fake_download(url: str, destination: Path, **_: object) -> str:
        if destination.name.endswith("sums.txt"):
            destination.write_text(f"{hashlib.sha256(content).hexdigest()}  {sums_for}\n", encoding="utf-8")
        else:
            destination.write_bytes(content)
        return url

    return fake_download


NOW = datetime(2026, 10, 27, 12, 0, 0, tzinfo=timezone.utc)


def _rel(tag: str, days_old: float, *, draft: bool = False, prerelease: bool = False, assets: list | None = None) -> dict:
    published = (NOW - timedelta(days=days_old)).strftime("%Y-%m-%dT%H:%M:%SZ")
    return {"tag_name": tag, "published_at": published, "draft": draft, "prerelease": prerelease, "assets": assets or []}


def _api(monkeypatch: _pytest.MonkeyPatch, routes: dict[str, object]) -> list[str]:
    """Replace `_fetch_json` with a route table keyed by URL substring; returns the list of requested URLs."""
    seen: list[str] = []

    def fake(url: str, **_: object) -> object:
        seen.append(url)
        for key, payload in routes.items():
            if key in url:
                return payload
        raise staging.StageError(f"unrouted {url}")

    monkeypatch.setattr(staging, "_fetch_json", fake)
    return seen


def test_the_resolver_picks_the_newest_release_older_than_the_cutoff(monkeypatch: _pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("MDT_TOOL_MIN_AGE_DAYS", raising=False)
    releases = [
        _rel("v3.0.0", 2),  # too young
        _rel("v2.9.0", 20, draft=True),
        _rel("v2.8.0", 16, prerelease=True),
        _rel("v2.0.0", 15),  # the newest qualifying release
        _rel("v1.0.0", 100),
    ]
    seen = _api(monkeypatch, {"/repos/o/r/releases?per_page=30": releases})
    assert staging._resolve_latest_aged_release("o/r", now=NOW) == "2.0.0"
    assert seen == ["https://api.github.com/repos/o/r/releases?per_page=30"]


def test_the_resolver_orders_by_published_at_not_by_list_position(monkeypatch: _pytest.MonkeyPatch) -> None:
    _api(monkeypatch, {"releases?per_page": [_rel("v1.0.0", 50), _rel("v1.1.0", 30)]})
    assert staging._resolve_latest_aged_release("o/r", now=NOW) == "1.1.0"


def test_the_resolver_raises_naming_the_repo_and_cutoff_when_nothing_qualifies(monkeypatch: _pytest.MonkeyPatch) -> None:
    _api(monkeypatch, {"releases?per_page": [_rel("v1.0.0", 3), _rel("v0.9.0", 40, draft=True)]})
    with _pytest.raises(staging.StageError, match=r"o/r.*2026-10-13T12:00:00Z"):
        staging._resolve_latest_aged_release("o/r", now=NOW)


def test_the_age_comes_from_the_environment_and_is_validated(monkeypatch: _pytest.MonkeyPatch) -> None:
    _api(monkeypatch, {"releases?per_page": [_rel("v2.0.0", 5), _rel("v1.0.0", 20)]})
    monkeypatch.setenv("MDT_TOOL_MIN_AGE_DAYS", "3")
    assert staging._resolve_latest_aged_release("o/r", now=NOW) == "2.0.0"
    monkeypatch.setenv("MDT_TOOL_MIN_AGE_DAYS", "0")
    assert staging._min_age_days() == 0
    monkeypatch.setenv("MDT_TOOL_MIN_AGE_DAYS", "10")
    assert staging._resolve_latest_aged_release("o/r", now=NOW) == "1.0.0"
    for bad in ("abc", "-1", "1.5"):
        monkeypatch.setenv("MDT_TOOL_MIN_AGE_DAYS", bad)
        with _pytest.raises(staging.StageError, match="MDT_TOOL_MIN_AGE_DAYS"):
            staging._resolve_latest_aged_release("o/r", now=NOW)
    monkeypatch.delenv("MDT_TOOL_MIN_AGE_DAYS")
    assert staging._min_age_days() == 14


def test_an_explicit_version_younger_than_the_cutoff_warns_but_is_accepted(
    monkeypatch: _pytest.MonkeyPatch, capsys: _pytest.CaptureFixture[str]
) -> None:
    monkeypatch.delenv("MDT_TOOL_MIN_AGE_DAYS", raising=False)
    _api(monkeypatch, {"/releases/tags/v9.9.9": _rel("v9.9.9", 2)})
    assert staging._resolve_aged_version("9.9.9", "o/r", "OV_VERSION", now=NOW) == "9.9.9"
    err = capsys.readouterr().err
    assert "[WARNING]" in err and "OV_VERSION=9.9.9" in err and "2.0 days" in err


def test_an_explicit_version_older_than_the_cutoff_is_accepted_silently(
    monkeypatch: _pytest.MonkeyPatch, capsys: _pytest.CaptureFixture[str]
) -> None:
    _api(monkeypatch, {"/releases/tags/v1.0.0": _rel("v1.0.0", 60)})
    assert staging._resolve_aged_version("1.0.0", "o/r", "OV_VERSION", now=NOW) == "1.0.0"
    assert "[WARNING]" not in capsys.readouterr().err


@_pytest.mark.parametrize("explicit", ["latest", "", None, "  latest "])
def test_latest_as_an_explicit_value_means_the_aged_latest(monkeypatch: _pytest.MonkeyPatch, explicit: str | None) -> None:
    _api(monkeypatch, {"releases?per_page": [_rel("v2.0.0", 1), _rel("v1.5.0", 30)]})
    assert staging._resolve_aged_version(explicit, "o/r", "OV_VERSION", now=NOW) == "1.5.0"


def test_gdu_dust_ov_dry_and_yazi_versions_resolve_through_the_aged_resolver(monkeypatch: _pytest.MonkeyPatch) -> None:
    for variable in ("OV_VERSION", "DRY_VERSION", "YAZI_VERSION", "SSS_VERSION"):
        monkeypatch.delenv(variable, raising=False)
    calls: list[str] = []

    def fake(repo: str, **_: object) -> str:
        calls.append(repo)
        return "1.2.3"

    monkeypatch.setattr(staging, "_resolve_latest_aged_release", fake)
    monkeypatch.setattr(staging, "_resolve_sss_commit", lambda requested: "a" * 40)
    monkeypatch.setattr(staging, "_resolve_version", lambda requested, repo: "0.0.1")
    monkeypatch.setattr(staging, "_resolve_pypi_version", lambda requested, package: "0.0.1")
    monkeypatch.setattr(staging, "_resolve_codex_version", lambda requested: "0.0.1")
    monkeypatch.setattr(staging, "_resolve_pinned_npm_version", lambda requested, package, variable: "0.0.1")
    monkeypatch.setattr(staging, "_vetted_ai_cli_versions", lambda: {})
    monkeypatch.setattr(staging, "install_enabled", lambda name: False)
    resolved = staging._resolve_versions()
    assert [resolved["OV_VER"], resolved["DRY_VER"], resolved["YAZI_VER"]] == ["1.2.3"] * 3
    assert calls == ["dundee/gdu", "bootandy/dust", "noborus/ov", "moncho/dry", "sxyazi/yazi"]
    assert resolved["GDU_VER"] == resolved["DUST_VER"] == "1.2.3"
    assert resolved["SSS_VER"] == "a" * 40


SSS_REPO = {"default_branch": "trunk"}


def _commit(sha: str, days_old: float) -> dict:
    when = (NOW - timedelta(days=days_old)).strftime("%Y-%m-%dT%H:%M:%SZ")
    return {"sha": sha, "commit": {"committer": {"date": when}}}


def test_sss_resolves_the_newest_default_branch_commit_older_than_the_cutoff(monkeypatch: _pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("MDT_TOOL_MIN_AGE_DAYS", raising=False)
    seen = _api(monkeypatch, {"/commits?": [_commit("b" * 40, 20), _commit("c" * 40, 40)], "/repos/azlux/sss": SSS_REPO})
    assert staging._resolve_sss_commit(None, now=NOW) == "b" * 40
    assert seen[0] == "https://api.github.com/repos/azlux/sss"
    assert "sha=trunk" in seen[1] and "until=2026-10-13T12%3A00%3A00Z" in seen[1]


def test_sss_filters_by_commit_date_itself_when_until_is_ignored(monkeypatch: _pytest.MonkeyPatch) -> None:
    _api(
        monkeypatch,
        {"/commits?": [_commit("a" * 40, 1), _commit("b" * 40, 13), _commit("c" * 40, 15), _commit("d" * 40, 90)], "/repos/azlux/sss": SSS_REPO},
    )
    assert staging._resolve_sss_commit("latest", now=NOW) == "c" * 40


def test_sss_fails_when_no_commit_is_old_enough_or_the_branch_is_unknown(monkeypatch: _pytest.MonkeyPatch) -> None:
    _api(monkeypatch, {"/commits?": [_commit("a" * 40, 1)], "/repos/azlux/sss": SSS_REPO})
    with _pytest.raises(staging.StageError, match="azlux/sss.*2026-10-13T12:00:00Z"):
        staging._resolve_sss_commit(None, now=NOW)
    _api(monkeypatch, {"/repos/azlux/sss": {}})
    with _pytest.raises(staging.StageError, match="default branch"):
        staging._resolve_sss_commit(None, now=NOW)


@_pytest.mark.parametrize("bad", ["f182bf6", "main", "A" * 40, "g" * 40, "a" * 41])
def test_sss_rejects_an_override_that_is_not_a_full_lowercase_hex_commit(monkeypatch: _pytest.MonkeyPatch, bad: str) -> None:
    _api(monkeypatch, {})
    with _pytest.raises(staging.StageError, match="40-hex"):
        staging._resolve_sss_commit(bad, now=NOW)
    assert staging._resolve_sss_commit("a" * 40, now=NOW) == "a" * 40


def test_the_new_tools_have_no_pinned_tables() -> None:
    for name in dir(staging):
        assert not name.endswith(("_PINNED_VERSION", "_PINNED_SHA256", "_PINNED_COMMIT")), name
    assert not hasattr(staging, "_resolve_pinned_version") and not hasattr(staging, "_pinned_sha256")


def _mismatching_download(content: bytes, sums_for: str):
    """Checksums file claims the digest of DIFFERENT bytes than the file that is downloaded."""

    def fake_download(url: str, destination: Path, **_: object) -> str:
        if destination.name.endswith("sums.txt"):
            destination.write_text(f"{hashlib.sha256(b'the genuine asset').hexdigest()}  {sums_for}\n", encoding="utf-8")
        else:
            destination.write_bytes(content)
        return url

    return fake_download


@_pytest.mark.parametrize(
    ("stage", "sums_for"),
    [(staging._stage_ov, "ov_1.0.0_amd64.deb"), (staging._stage_dry, "dry-linux-amd64")],
)
def test_ov_and_dry_reject_a_download_that_differs_from_the_checksums_file(tmp_path: Path, stage: object, sums_for: str) -> None:
    fake = _mismatching_download(b"tampered asset", sums_for)
    with mock.patch.object(staging, "DOWNLOADS_DIR", tmp_path), mock.patch.object(staging, "_download", side_effect=fake):
        with _pytest.raises(staging.StageError, match="checksum mismatch"):
            stage("1.0.0", [])  # type: ignore[operator]


@_pytest.mark.parametrize("stage", [staging._stage_ov, staging._stage_dry])
def test_ov_and_dry_fail_closed_when_the_checksums_file_lacks_the_asset_line(tmp_path: Path, stage: object) -> None:
    fake = _fake_download_writing(b"asset", "some-other-file")
    with mock.patch.object(staging, "DOWNLOADS_DIR", tmp_path), mock.patch.object(staging, "_download", side_effect=fake):
        with _pytest.raises(staging.StageError, match="Unable to find checksum entry"):
            stage("1.0.0", [])  # type: ignore[operator]


def _yazi_zip(path: Path) -> str:
    with zipfile.ZipFile(path, "w") as archive:
        archive.writestr("yazi-x86_64-unknown-linux-gnu/yazi", "x")
        archive.writestr("yazi-x86_64-unknown-linux-gnu/ya", "x")
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _yazi_release(digest: str | None) -> dict:
    asset: dict = {"name": "yazi-x86_64-unknown-linux-gnu.zip"}
    if digest is not None:
        asset["digest"] = f"sha256:{digest}"
    return {"assets": [asset]}


def test_yazi_verifies_against_the_api_digest_and_writes_the_sidecar(tmp_path: Path, monkeypatch: _pytest.MonkeyPatch) -> None:
    good = _yazi_zip(tmp_path / "source.zip")
    seen = _api(monkeypatch, {"/repos/sxyazi/yazi/releases/tags/v26.9.9": _yazi_release(good)})

    def fake_download(url: str, destination: Path, **_: object) -> str:
        destination.write_bytes((tmp_path / "source.zip").read_bytes())
        return url

    records: list = []
    with mock.patch.object(staging, "DOWNLOADS_DIR", tmp_path), mock.patch.object(staging, "_download", side_effect=fake_download):
        staging._stage_yazi("26.9.9", records)
    assert seen == ["https://api.github.com/repos/sxyazi/yazi/releases/tags/v26.9.9"]
    assert (tmp_path / "yazi-26.9.9.sha256").read_text() == f"{good}  yazi-26.9.9.zip\n"
    assert records[0].sha256 == good and "asset digest" in records[0].verification


def test_yazi_fails_closed_without_an_api_digest_and_before_any_download(tmp_path: Path, monkeypatch: _pytest.MonkeyPatch) -> None:
    _api(monkeypatch, {"/releases/tags/": _yazi_release(None)})
    with mock.patch.object(staging, "DOWNLOADS_DIR", tmp_path), mock.patch.object(staging, "_download", side_effect=AssertionError("no download")):
        with _pytest.raises(staging.StageError, match="no sha256 digest"):
            staging._stage_yazi("26.9.9", [])


def test_yazi_rejects_a_download_that_differs_from_the_api_digest(tmp_path: Path, monkeypatch: _pytest.MonkeyPatch) -> None:
    _api(monkeypatch, {"/releases/tags/": _yazi_release("0" * 64)})
    fake = _fake_download_writing(b"tampered")
    with mock.patch.object(staging, "DOWNLOADS_DIR", tmp_path), mock.patch.object(staging, "_download", side_effect=fake):
        with _pytest.raises(staging.StageError, match="yazi checksum mismatch"):
            staging._stage_yazi("26.9.9", [])


def test_sss_records_the_computed_sha256_and_requires_the_bash_shebang(tmp_path: Path) -> None:
    commit = "d" * 40
    script = b"#!/usr/bin/env bash\necho sss\n"
    fake = _fake_download_writing(script)
    records: list = []
    with mock.patch.object(staging, "DOWNLOADS_DIR", tmp_path), mock.patch.object(staging, "_download", side_effect=fake):
        staging._stage_sss(commit, records)
    digest = hashlib.sha256(script).hexdigest()
    assert (tmp_path / f"sss-{commit}.sha256").read_text() == f"{digest}  sss-{commit}\n"
    assert records[0].version == commit and records[0].sha256 == digest
    assert "first-use trust" in records[0].verification and "default branch" in records[0].verification
    bad = _fake_download_writing(b"#!/bin/sh\n")
    with mock.patch.object(staging, "DOWNLOADS_DIR", tmp_path), mock.patch.object(staging, "_download", side_effect=bad):
        with _pytest.raises(staging.StageError, match="bash"):
            staging._stage_sss(commit, [])
    with _pytest.raises(staging.StageError, match="40-hex"):
        staging._stage_sss("abc", [])
