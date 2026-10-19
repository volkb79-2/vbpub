from __future__ import annotations

import hashlib
import importlib.util
import io
import subprocess
import sys
import tarfile
import tempfile
import tomllib
import unittest
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
