"""The oci-push lane driver and the test's required-vs-skip outcome (MDTC-02 T6)."""
from __future__ import annotations

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import run_oci_push_lane as lane  # noqa: E402
import test_oci_layout_push as push  # noqa: E402


class PreconditionOutcome(unittest.TestCase):
    def test_nothing_missing_passes_through(self) -> None:
        push.precondition_outcome(True, [])
        push.precondition_outcome(False, [])

    def test_a_missing_tool_is_a_failure_when_the_lane_requires_it(self) -> None:
        with self.assertRaises(Exception) as ctx:  # a SkipTest must not satisfy this
            push.precondition_outcome(True, ["crane"])
        self.assertNotIsInstance(ctx.exception, unittest.SkipTest)
        self.assertIsInstance(ctx.exception, AssertionError)
        self.assertIn("crane", str(ctx.exception))

    def test_a_missing_tool_is_a_skip_outside_the_lane(self) -> None:
        with self.assertRaises(unittest.SkipTest):
            push.precondition_outcome(False, ["crane"])

    def test_the_test_class_has_no_unconditional_skip_decorator(self) -> None:
        source = (Path(push.__file__)).read_text(encoding="utf-8")
        self.assertNotIn("skipUnless", source.split("class OciLayoutPushTests")[0].splitlines()[-2])
        self.assertIn("precondition_outcome(_required(), _missing())", source)


class Driver(unittest.TestCase):
    def test_the_gate_tier_has_no_default(self) -> None:
        for env in ({}, {lane.ENV_GATES: " "}):
            with self.assertRaises(SystemExit):
                lane.require_gates(env)
        self.assertEqual(lane.require_gates({lane.ENV_GATES: "g.slice"}), "g.slice")

    def test_the_lane_needs_no_typed_image_reference(self) -> None:
        import tomllib

        declared = tomllib.loads((Path(lane.__file__).resolve().parents[1] / "run-gate.toml").read_text(encoding="utf-8"))
        required = declared["lanes"]["oci-push"]["required_env"]
        self.assertEqual(required, ["CGROUP_PARENT_DEV_GATES"])
        self.assertFalse(hasattr(lane, "ENV_IMAGE"))
        source = Path(lane.__file__).read_text(encoding="utf-8")
        self.assertNotIn("MDT_OCI_PUSH_IMAGE", source.split('"""', 2)[2])

    def test_no_layout_means_no_image_was_built_and_the_lane_fails(self) -> None:
        import tempfile

        with tempfile.TemporaryDirectory() as tmp:
            for root in (Path(tmp) / "missing", Path(tmp)):
                with self.assertRaises(SystemExit) as ctx:
                    lane.find_layout(root)
                self.assertIn("no image was built", str(ctx.exception))
            (Path(tmp) / "half").mkdir()
            (Path(tmp) / "half" / "oci-layout").write_text("{}")  # no index.json: not a layout
            with self.assertRaises(SystemExit):
                lane.find_layout(Path(tmp))

    def test_the_first_complete_layout_is_chosen_and_its_digest_read(self) -> None:
        import json
        import tempfile

        with tempfile.TemporaryDirectory() as tmp:
            for name, digest in (("trixie-py3.14-php8.5-vsc", "sha256:" + "b" * 64), ("trixie-py3.14-php8.5", "sha256:" + "a" * 64)):
                d = Path(tmp) / name
                d.mkdir()
                (d / "oci-layout").write_text("{}")
                (d / "index.json").write_text(json.dumps({"manifests": [{"digest": digest}]}))
            chosen = lane.find_layout(Path(tmp))
            self.assertEqual(chosen.name, "trixie-py3.14-php8.5")
            self.assertEqual(lane.top_digest(chosen), "sha256:" + "a" * 64)
            (chosen / "index.json").write_text(json.dumps({"manifests": []}))
            with self.assertRaises(SystemExit):
                lane.top_digest(chosen)

    def test_the_export_is_platform_pinned_and_named_for_docker_load(self) -> None:
        argv = lane.export_argv(Path("/l"), "sha256:" + "a" * 64, "mdt-cockpit-oci-push:9")
        self.assertEqual(argv[:3], ["regctl", "image", "export"])
        self.assertIn("--name", argv)
        self.assertEqual(argv[argv.index("--name") + 1], "mdt-cockpit-oci-push:9")
        self.assertEqual(argv[-1], "ocidir:///l@sha256:" + "a" * 64)

    def test_the_layout_root_is_build_pushs_own_resolution(self) -> None:
        self.assertEqual(lane.layout_root(), Path(lane.__file__).resolve().parents[1] / "build" / "oci-layouts")

    def test_the_container_is_gate_placed_reaped_named_and_forces_the_requirement(self) -> None:
        argv = lane.docker_run_argv("img:1", "g.slice", "mdt-cockpit-oci-push-7")
        joined = " ".join(argv)
        self.assertIn("--cgroup-parent g.slice", joined)
        self.assertIn("--init", argv)
        self.assertIn("--rm", argv)
        self.assertIn("--name mdt-cockpit-oci-push-7", joined)
        self.assertIn("MDT_OCI_PUSH_REQUIRED=1", argv)
        self.assertIn("/var/run/docker.sock:/var/run/docker.sock", joined)
        self.assertEqual(argv[argv.index("img:1") - 1], "bash", "image follows --entrypoint bash")
        # nothing from the devcontainer filesystem is bind-mounted except the daemon socket
        self.assertEqual([a for i, a in enumerate(argv) if argv[i - 1] == "-v"], ["/var/run/docker.sock:/var/run/docker.sock"])
        self.assertNotIn("--network", argv)

    def test_the_inner_command_runs_the_push_test_module(self) -> None:
        self.assertIn("unittest -v scripts.test_oci_layout_push", lane.INNER)
        # the buildx-dependent bake-file test moved here from tester-unified (T8 skip-diff)
        self.assertIn("scripts/test_local_dev.py::test_the_bake_file_gives_a_local_dev_build_only_dev_tags", lane.INNER)
        self.assertIn("&&", lane.INNER, "both stages must succeed")

    def test_the_smoke_lane_deselects_the_bake_test_explicitly_and_oci_push_runs_it(self) -> None:
        import tomllib

        root = Path(__file__).resolve().parents[1]
        smoke = tomllib.loads((root / "run-gate.toml").read_text(encoding="utf-8"))["lanes"]["smoke"]["argv"][-1]
        node = "scripts/test_local_dev.py::test_the_bake_file_gives_a_local_dev_build_only_dev_tags_and_leaves_release_tags_alone"
        self.assertIn(f"--deselect {node}", smoke)
        self.assertIn(node, lane.INNER)

    def test_the_bake_test_fails_instead_of_skipping_when_required(self) -> None:
        import test_local_dev as local_dev_tests

        source = Path(local_dev_tests.__file__).read_text(encoding="utf-8")
        self.assertIn("not _buildx_available() and not _buildx_required()", source)
        self.assertIn("MDT_OCI_PUSH_REQUIRED=1 but docker buildx is not available", source)


if __name__ == "__main__":
    unittest.main()
