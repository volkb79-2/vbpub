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


# ── Claude Code version: newest release at least 14 days old (freshness rule) ──

import pytest  # noqa: E402
from datetime import datetime, timezone  # noqa: E402

_NOW = datetime(2026, 10, 18, 12, 0, tzinfo=timezone.utc)
_TIMES = {
    "created": "2025-02-01T00:00:00.000Z",
    "modified": "2026-10-17T00:00:00.000Z",
    "2.1.286": "2026-09-30T10:00:00.000Z",
    "2.1.289": "2026-10-03T20:12:02.717Z",   # 14d 16h old: eligible
    "2.1.292": "2026-10-04T12:00:00.000Z",   # exactly 14 days old: eligible
    "2.1.296": "2026-10-04T12:00:01.000Z",   # one second short of 14 days: not eligible
    "2.1.302": "2026-10-16T16:48:28.057Z",
    "2.2.0-beta.1": "2026-08-01T00:00:00.000Z",  # old but a prerelease
}


def test_the_newest_release_at_least_fourteen_days_old_is_chosen() -> None:
    assert staging._newest_aged_stable_version(_TIMES, now=_NOW) == "2.1.292"


def test_ordering_is_numeric_not_lexicographic() -> None:
    times = {"2.1.9": "2026-01-01T00:00:00Z", "2.1.10": "2026-01-02T00:00:00Z", "2.0.99": "2026-01-03T00:00:00Z"}
    assert staging._newest_aged_stable_version(times, now=_NOW) == "2.1.10"


def test_prereleases_and_bookkeeping_keys_are_never_chosen() -> None:
    times = {"created": "2020-01-01T00:00:00Z", "3.0.0-rc.1": "2020-01-01T00:00:00Z"}
    with pytest.raises(staging.StageError, match="at least 14 days old"):
        staging._newest_aged_stable_version(times, now=_NOW)


def test_claude_latest_resolves_through_the_registry_with_the_freshness_rule(monkeypatch: pytest.MonkeyPatch) -> None:
    urls: list[str] = []

    def fake_fetch(url: str, **_kw) -> dict:
        urls.append(url)
        return {"time": _TIMES}

    monkeypatch.setattr(staging, "_fetch_json", fake_fetch)

    assert staging._resolve_claude_code_version("latest", now=_NOW) == "2.1.292"
    assert staging._resolve_claude_code_version(None, now=_NOW) == "2.1.292"
    assert urls == ["https://registry.npmjs.org/@anthropic-ai/claude-code"] * 2


def test_an_explicit_claude_pin_at_least_fourteen_days_old_is_accepted(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(staging, "_fetch_json", lambda *a, **k: {"time": _TIMES})

    assert staging._resolve_claude_code_version("2.1.289", now=_NOW) == "2.1.289"
    with pytest.raises(staging.StageError, match="Unsupported"):
        staging._resolve_claude_code_version("two", now=_NOW)


def test_an_explicit_claude_pin_younger_than_fourteen_days_is_refused_naming_its_release_date(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(staging, "_fetch_json", lambda *a, **k: {"time": _TIMES})

    with pytest.raises(staging.StageError, match=r"2\.1\.302 was released on 2026-10-16.*at least 14 days"):
        staging._resolve_claude_code_version("2.1.302", now=_NOW)
    with pytest.raises(staging.StageError, match="2026-10-04"):
        staging._resolve_claude_code_version("2.1.296", now=_NOW)  # one second short


def test_an_explicit_claude_pin_that_was_never_published_is_refused(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(staging, "_fetch_json", lambda *a, **k: {"time": _TIMES})

    with pytest.raises(staging.StageError, match="not a published"):
        staging._resolve_claude_code_version("9.9.9", now=_NOW)


def test_claude_is_no_longer_staged_as_a_native_binary() -> None:
    source = (Path(__file__).resolve().parent / "stage_tool_artifacts.py").read_text(encoding="utf-8")
    assert "_stage_claude_code" not in source and "downloads.claude.ai" not in source
