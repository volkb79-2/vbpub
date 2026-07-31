"""End-to-end tests for the OCI-layout build-once + digest-verified push path
(RELEASE_IMAGE_FLOW=load) against a REAL local registry — not mocked, so a
tooling incompatibility between buildx's OCI export, crane, and regctl shows
up here instead of only during a real release.

Skipped automatically when docker/buildx/crane/regctl aren't available (e.g. a
restricted CI runner) rather than failing the whole suite.
"""
from __future__ import annotations

import importlib.util
import json
import shutil
import subprocess
import tempfile
import unittest
import unittest.mock
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def _load_build_push():
    spec = importlib.util.spec_from_file_location("mdt_build_push", ROOT / "build-push.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _docker(*args, check=True):
    return subprocess.run(["docker", *args], capture_output=True, text=True, check=check)


def _tools_available() -> bool:
    return all(shutil.which(tool) for tool in ("docker", "crane", "regctl", "curl", "tar"))


def _docker_reachable() -> bool:
    try:
        _docker("version", "--format", "{{.Server.Version}}")
        return True
    except (subprocess.CalledProcessError, FileNotFoundError, OSError):
        return False


@unittest.skipUnless(_tools_available() and _docker_reachable(), "docker/crane/regctl not available")
class OciLayoutPushTests(unittest.TestCase):
    """Build a tiny throwaway image, push it to a real registry:2 container, and
    exercise build-push.py's digest-verified push mechanics against it."""

    @classmethod
    def setUpClass(cls) -> None:
        cls.build_push = _load_build_push()
        cls.work = Path(tempfile.mkdtemp(prefix="mdt-oci-push-test-"))

        # Publish to an ephemeral host port, then find a host address this
        # process can actually reach — plain localhost works for a normal
        # docker setup, but docker-outside-of-docker needs the bridge gateway.
        cls.registry_name = "mdt-oci-push-test-registry"
        _docker("rm", "-f", cls.registry_name, check=False)
        _docker("run", "-d", "--rm", "--name", cls.registry_name, "-p", "0:5000", "registry:2")
        inspected = json.loads(_docker("inspect", cls.registry_name).stdout)
        port = inspected[0]["NetworkSettings"]["Ports"]["5000/tcp"][0]["HostPort"]
        cls.registry = cls._reachable_registry_host(port)
        # crane auto-falls-back to plain HTTP for IP-literal/localhost registry
        # addresses; regctl (used for the OCI-layout-scoped push, see
        # _push_oci_layouts) requires this configured explicitly per registry.
        subprocess.run(["regctl", "registry", "set", cls.registry, "--tls", "disabled"], check=True)

        cls.builder_name = "mdt-oci-push-test-builder"
        _docker("buildx", "rm", cls.builder_name, check=False)
        _docker("buildx", "create", "--name", cls.builder_name, "--driver", "docker-container")

    @classmethod
    def tearDownClass(cls) -> None:
        _docker("rm", "-f", cls.registry_name, check=False)
        _docker("buildx", "rm", cls.builder_name, check=False)
        shutil.rmtree(cls.work, ignore_errors=True)

    @staticmethod
    def _reachable_registry_host(port: str) -> str:
        for host in ("127.0.0.1", "172.17.0.1"):
            probe = subprocess.run(
                ["curl", "-sf", "-o", "/dev/null", f"http://{host}:{port}/v2/"],
                capture_output=True,
            )
            if probe.returncode == 0:
                return f"{host}:{port}"
        raise unittest.SkipTest(f"local registry:2 (port {port}) not reachable from this process")

    def _build_oci_layout(self, name: str, marker_path: str, marker_content: str) -> Path:
        """Build a tiny image via buildx --output type=oci and extract it to a
        directory — exactly what oci_layout_bake() (release-bake.sh) produces
        per target."""
        src = self.work / f"{name}-src"
        src.mkdir()
        (src / "Dockerfile").write_text(
            f"FROM alpine:3.20\n"
            f"RUN mkdir -p $(dirname {marker_path}) && echo '{marker_content}' > {marker_path}\n"
        )
        tar_path = self.work / f"{name}.tar"
        _docker(
            "buildx", "build", "--builder", self.builder_name,
            "--output", f"type=oci,dest={tar_path}",
            "-t", f"{name}:test", str(src),
        )
        layout_dir = self.work / f"{name}-layout"
        layout_dir.mkdir()
        subprocess.run(["tar", "-xf", str(tar_path), "-C", str(layout_dir)], check=True)
        tar_path.unlink()
        return layout_dir

    def _stage_as_target(self, layout_dir: Path, target: str, layout_root: Path) -> None:
        target_dir = layout_root / self.build_push._safe_target_name(target)
        shutil.copytree(layout_dir, target_dir)

    def _build_multi_tag_oci_layout(self, name: str, tags: list[str]) -> Path:
        """Like _build_oci_layout, but with >1 `-t` so buildx's OCI export writes
        one index.json entry per tag — reproducing what a bake target with
        multiple `tags` (e.g. a dated tag plus a floating "-latest" alias)
        actually produces."""
        src = self.work / f"{name}-src"
        src.mkdir()
        (src / "Dockerfile").write_text("FROM alpine:3.20\nRUN echo hi > /marker.txt\n")
        tar_path = self.work / f"{name}.tar"
        tag_args = []
        for tag in tags:
            tag_args += ["-t", tag]
        _docker(
            "buildx", "build", "--builder", self.builder_name,
            "--output", f"type=oci,dest={tar_path}",
            *tag_args, str(src),
        )
        layout_dir = self.work / f"{name}-layout"
        layout_dir.mkdir()
        subprocess.run(["tar", "-xf", str(tar_path), "-C", str(layout_dir)], check=True)
        tar_path.unlink()
        return layout_dir

    def test_tag_component_handles_registry_port(self):
        bp = self.build_push
        self.assertEqual(bp._tag_component("ghcr.io/owner/name:v1.2.3"), "v1.2.3")
        self.assertEqual(bp._tag_component("localhost:5000/owner/name:v1"), "v1")
        with self.assertRaises(ValueError):
            bp._tag_component("ghcr.io/owner/name")  # no tag

    def test_push_oci_layouts_verifies_identical_digest(self):
        bp = self.build_push
        layout_dir = self._build_oci_layout("proto-a", "/marker.txt", "hello-a")
        ref = f"{self.registry}/proto-a:test"

        layout_root = self.work / "layouts-ok"
        layout_root.mkdir()
        self._stage_as_target(layout_dir, "base", layout_root)

        with unittest.mock.patch.object(bp, "_oci_layout_dir", lambda: layout_root), \
             unittest.mock.patch.object(bp, "_enumerate_bake_targets",
                                         lambda: ({"base": {"tags": [ref]}}, ["base"])):
            bp._push_oci_layouts({})

        expected = subprocess.run(
            ["regctl", "manifest", "digest", f"ocidir://{layout_dir}:test"],
            capture_output=True, text=True, check=True,
        ).stdout.strip()
        actual = subprocess.run(
            ["crane", "digest", ref], capture_output=True, text=True, check=True,
        ).stdout.strip()
        self.assertEqual(actual, expected)

    def test_push_oci_layouts_handles_multi_tag_targets(self):
        """A bake target with a dated tag plus a floating "-latest" alias makes
        buildx write 2 same-digest index.json entries into one target's OCI
        layout. A bare `crane push <dir> <tag>` can't disambiguate those and
        fails closed with "layout contains 2 entries, consider --index" —
        this must push and verify both tags individually instead."""
        bp = self.build_push
        dated_ref = f"{self.registry}/proto-multi:20260731"
        latest_ref = f"{self.registry}/proto-multi:latest"
        layout_dir = self._build_multi_tag_oci_layout("proto-multi", [dated_ref, latest_ref])

        index = json.loads((layout_dir / "index.json").read_text())
        self.assertEqual(len(index["manifests"]), 2)

        layout_root = self.work / "layouts-multi"
        layout_root.mkdir()
        self._stage_as_target(layout_dir, "multi", layout_root)

        with unittest.mock.patch.object(bp, "_oci_layout_dir", lambda: layout_root), \
             unittest.mock.patch.object(
                 bp, "_enumerate_bake_targets",
                 lambda: ({"multi": {"tags": [dated_ref, latest_ref]}}, ["multi"]),
             ):
            bp._push_oci_layouts({})

        expected = subprocess.run(
            ["regctl", "manifest", "digest", f"ocidir://{layout_dir}:20260731"],
            capture_output=True, text=True, check=True,
        ).stdout.strip()
        for ref in (dated_ref, latest_ref):
            actual = subprocess.run(
                ["crane", "digest", ref], capture_output=True, text=True, check=True,
            ).stdout.strip()
            self.assertEqual(actual, expected)

    def test_push_oci_layouts_reads_manifest_file_from_the_layout(self):
        """The same layout _push_oci_layouts() publishes must be independently
        readable by extract_manifests()'s regctl get-file mechanism."""
        bp = self.build_push
        layout_dir = self._build_oci_layout(
            "proto-manifest", bp._MANIFEST_PATH, "manifest content from prototype",
        )
        destination = self.work / "extracted-manifest.md"
        subprocess.run(
            [
                "regctl", "image", "get-file",
                f"ocidir://{layout_dir}:test", bp._MANIFEST_PATH, str(destination),
            ],
            capture_output=True, text=True, check=True,
        )
        self.assertEqual(destination.read_text().strip(), "manifest content from prototype")

    def test_push_oci_layouts_fails_closed_on_missing_layout(self):
        bp = self.build_push
        layout_root = self.work / "layouts-missing"
        layout_root.mkdir()

        with unittest.mock.patch.object(bp, "_oci_layout_dir", lambda: layout_root), \
             unittest.mock.patch.object(
                 bp, "_enumerate_bake_targets",
                 lambda: ({"base": {"tags": [f"{self.registry}/proto-missing:test"]}}, ["base"]),
             ):
            with self.assertRaises(SystemExit) as ctx:
                bp._push_oci_layouts({})
        self.assertIn("Missing built OCI layout", str(ctx.exception))

    def test_push_oci_layouts_detects_registry_digest_drift(self):
        """If the registry-reported digest ever disagreed with the local layout's
        own digest, the push must fail loudly rather than accept it — a genuine
        disagreement between two correct, independent tools pushing the same
        bytes should not happen, so this exercises the failure branch by
        intercepting only the final readback."""
        bp = self.build_push
        layout_dir = self._build_oci_layout("proto-drift", "/marker.txt", "hello-drift")
        ref = f"{self.registry}/proto-drift:test"

        layout_root = self.work / "layouts-drift"
        layout_root.mkdir()
        self._stage_as_target(layout_dir, "base", layout_root)

        real_run = subprocess.run

        def fake_run(argv, **kwargs):
            if list(argv[:2]) == ["crane", "digest"]:
                result = real_run(argv, **kwargs)
                result.stdout = "sha256:" + "0" * 64 + "\n"
                return result
            return real_run(argv, **kwargs)

        with unittest.mock.patch.object(bp, "_oci_layout_dir", lambda: layout_root), \
             unittest.mock.patch.object(
                 bp, "_enumerate_bake_targets", lambda: ({"base": {"tags": [ref]}}, ["base"]),
             ), \
             unittest.mock.patch.object(bp.subprocess, "run", side_effect=fake_run):
            with self.assertRaises(SystemExit) as ctx:
                bp._push_oci_layouts({})
        self.assertIn("Checksum drift", str(ctx.exception))


if __name__ == "__main__":
    unittest.main()
