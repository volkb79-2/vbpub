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
    def test_no_default_image_or_tier(self) -> None:
        for env in ({}, {lane.ENV_GATES: "x.slice"}, {lane.ENV_IMAGE: "img"}, {lane.ENV_IMAGE: " ", lane.ENV_GATES: "x"}):
            with self.assertRaises(SystemExit):
                lane.require_environment(env)
        self.assertEqual(lane.require_environment({lane.ENV_IMAGE: "img:1", lane.ENV_GATES: "g.slice"}), ("img:1", "g.slice"))

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

    def test_the_bake_test_fails_instead_of_skipping_when_required(self) -> None:
        import test_local_dev as local_dev_tests

        source = Path(local_dev_tests.__file__).read_text(encoding="utf-8")
        self.assertIn("not _buildx_available() and not _buildx_required()", source)
        self.assertIn("MDT_OCI_PUSH_REQUIRED=1 but docker buildx is not available", source)


if __name__ == "__main__":
    unittest.main()
