from __future__ import annotations

import hashlib
import io
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


class OciToolStagingTests(unittest.TestCase):
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
        config = tomllib.loads((ROOT / "cmru.build.toml").read_text())

        for requested, resolved, binary in (
            ("CRANE_VERSION", "CRANE_VER", "crane"),
            ("REGCTL_VERSION", "REGCTL_VER", "regctl"),
        ):
            self.assertIn(f'variable "{requested}"', bake)
            self.assertIn(f"ARG {requested}", dockerfile)
            self.assertIn(resolved, dockerfile)
            self.assertIn(f'"{binary}"', manifest)
            self.assertIn(resolved, resolver)
            for step in ("build-images", "push-images"):
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
