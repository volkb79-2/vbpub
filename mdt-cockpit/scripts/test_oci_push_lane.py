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
        self.assertEqual(required, ["CGROUP_PARENT_DEV_GATES", "BUILDKIT_HOST"])
        self.assertFalse(hasattr(lane, "ENV_IMAGE"))
        source = Path(lane.__file__).read_text(encoding="utf-8")
        self.assertNotIn("MDT_OCI_PUSH_IMAGE", source.split('"""', 2)[2])

    # ── V4: the layout is bound to THIS build and the target is explicit ──────────────────────────────

    HEAD = "h" * 40
    SOURCE = "s" * 40

    def make_build(self, tmp: str, *, targets=("trixie-py314-php85", "trixie-py314-php85-vsc")) -> tuple[Path, Path]:
        import json

        import shutil

        root = Path(tmp) / "oci-layouts"
        shutil.rmtree(root, ignore_errors=True)
        root.mkdir()
        recorded = {}
        for name in targets:
            d = root / name
            d.mkdir()
            (d / "oci-layout").write_text("{}")
            digest = "sha256:" + (name[-1] * 64 if name[-1].isalnum() else "0" * 64)
            (d / "index.json").write_text(json.dumps({"manifests": [{"digest": digest}]}))
            recorded[name] = digest
        (root / "transaction.json").write_text(json.dumps(
            {"build_date": "20261019", "build_timestamp": "2026-10-19T00:00:00Z", "source_head": self.SOURCE, "targets": recorded}))
        env = Path(tmp) / "build-env.json"
        env.write_text(json.dumps({"vars": {"BUILD_DATE": "20261019", "BUILD_TIMESTAMP": "2026-10-19T00:00:00Z"}}))
        return root, env

    def fake_git(self, *, ancestor: bool = True, changed: str = "package-manifests-versioned/x.md\n"):
        def git(args: list[str]):
            import subprocess

            out = {"rev-parse": self.HEAD + "\n", "merge-base": "", "diff": changed}[args[0]]
            return subprocess.CompletedProcess(args, 0 if (args[0] != "merge-base" or ancestor) else 1, stdout=out, stderr="")
        return git

    def accept(self, tmp: str, target: str = "trixie-py314-php85", **kwargs) -> Path:
        root, env = self.make_build(tmp)
        return lane.find_layout(root, target, git=self.fake_git(**kwargs), build_env=env, prefixes=["package-manifests-versioned", "CHANGES.md"])

    def test_a_layout_of_this_build_is_accepted_and_the_target_is_the_named_one(self) -> None:
        import tempfile

        with tempfile.TemporaryDirectory() as tmp:
            self.assertEqual(self.accept(tmp).name, "trixie-py314-php85")
            self.assertEqual(self.accept(tmp, "trixie-py314-php85-vsc").name, "trixie-py314-php85-vsc")

    def test_no_transaction_record_means_no_image_was_built_in_this_transaction(self) -> None:
        import tempfile

        with tempfile.TemporaryDirectory() as tmp:
            for root in (Path(tmp) / "missing", Path(tmp)):
                with self.assertRaises(SystemExit) as ctx:
                    lane.find_layout(root, "trixie-py314-php85")
                self.assertIn("no image was built in this transaction", str(ctx.exception))

    def test_the_target_is_required_and_must_have_been_built(self) -> None:
        import tempfile

        with self.assertRaises(SystemExit):
            lane.parse_args([])
        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaises(SystemExit) as ctx:
                self.accept(tmp, "trixie-py311")
            self.assertIn("not in", str(ctx.exception))
        import tomllib

        argv = tomllib.loads((Path(lane.__file__).resolve().parents[1] / "run-gate.toml").read_text(encoding="utf-8"))["lanes"]["oci-push"]["argv"]
        self.assertEqual(argv[-2:], ["--target", "trixie-py314-php85"])

    def test_a_stray_layout_beside_the_build_is_ambiguous(self) -> None:
        import tempfile

        with tempfile.TemporaryDirectory() as tmp:
            root, env = self.make_build(tmp)
            stray = root / "old-target"
            stray.mkdir()
            (stray / "oci-layout").write_text("{}")
            with self.assertRaises(SystemExit) as ctx:
                lane.find_layout(root, "trixie-py314-php85", git=self.fake_git(), build_env=env, prefixes=["package-manifests-versioned"])
            self.assertIn("ambiguous", str(ctx.exception))

    def test_a_layout_edited_after_the_build_is_refused(self) -> None:
        import json
        import tempfile

        with tempfile.TemporaryDirectory() as tmp:
            root, env = self.make_build(tmp)
            (root / "trixie-py314-php85" / "index.json").write_text(json.dumps({"manifests": [{"digest": "sha256:" + "f" * 64}]}))
            with self.assertRaises(SystemExit) as ctx:
                lane.find_layout(root, "trixie-py314-php85", git=self.fake_git(), build_env=env, prefixes=["package-manifests-versioned"])
            self.assertIn("changed since the build", str(ctx.exception))

    def test_a_saved_build_state_from_another_build_means_stale_layouts(self) -> None:
        import json
        import tempfile

        with tempfile.TemporaryDirectory() as tmp:
            root, env = self.make_build(tmp)
            for key, value in (("BUILD_DATE", "20260101"), ("BUILD_TIMESTAMP", "2026-01-01T00:00:00Z")):
                saved = json.loads(env.read_text())
                saved["vars"][key] = value
                env.write_text(json.dumps(saved))
                with self.assertRaises(SystemExit) as ctx:
                    lane.find_layout(root, "trixie-py314-php85", git=self.fake_git(), build_env=env, prefixes=["package-manifests-versioned"])
                self.assertIn("stale layouts", str(ctx.exception))
                saved["vars"][key] = {"BUILD_DATE": "20261019", "BUILD_TIMESTAMP": "2026-10-19T00:00:00Z"}[key]
                env.write_text(json.dumps(saved))
            env.unlink()
            with self.assertRaises(SystemExit):
                lane.find_layout(root, "trixie-py314-php85", git=self.fake_git(), build_env=env, prefixes=["package-manifests-versioned"])

    def test_layouts_built_at_an_unrelated_commit_or_before_a_source_change_are_refused(self) -> None:
        import tempfile

        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaises(SystemExit) as ctx:
                self.accept(tmp, ancestor=False)
            self.assertIn("not an ancestor", str(ctx.exception))
            with self.assertRaises(SystemExit) as ctx:
                self.accept(tmp, changed="scripts/test_local_dev.py\npackage-manifests-versioned/x.md\n")
            self.assertIn("sources changed since the layouts were built", str(ctx.exception))

    def test_build_push_writes_the_transaction_record_the_lane_reads(self) -> None:
        import importlib.util
        import json
        import os
        import tempfile

        spec = importlib.util.spec_from_file_location("bp_under_test", Path(lane.__file__).resolve().parents[1] / "build-push.py")
        bp = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(bp)
        with tempfile.TemporaryDirectory() as tmp:
            root, _env = self.make_build(tmp)
            (root / "transaction.json").unlink()
            os.environ["MDT_OCI_LAYOUT_DIR"] = str(root)
            try:
                path = bp.write_layout_transaction("20261019", "2026-10-19T00:00:00Z")
            finally:
                del os.environ["MDT_OCI_LAYOUT_DIR"]
            record = json.loads(path.read_text(encoding="utf-8"))
        self.assertEqual(sorted(record["targets"]), ["trixie-py314-php85", "trixie-py314-php85-vsc"])
        self.assertEqual((record["build_date"], record["build_timestamp"]), ("20261019", "2026-10-19T00:00:00Z"))
        self.assertRegex(record["source_head"], r"^[0-9a-f]{40}$")

    def test_the_allowed_generated_paths_mirror_cmrus_rule_including_the_changelog(self) -> None:
        import tempfile

        def prefixes(release: str) -> list[str]:
            with tempfile.TemporaryDirectory() as tmp:
                cfg = Path(tmp) / "cmru.toml"
                cfg.write_text(f"[project.release]\ngit_tag = false\nbuild_step = \"b\"\n{release}\n", encoding="utf-8")
                return lane.generated_prefixes(cfg)

        self.assertEqual(prefixes('commit_generated = ["package-manifests-versioned"]'), ["package-manifests-versioned", "CHANGES.md"])
        self.assertEqual(prefixes('commit_generated = ["a"]\nchangelog = "docs/HISTORY.md"'), ["a", "docs/HISTORY.md"])
        self.assertEqual(prefixes('commit_generated = ["a"]\nchangelog = false'), ["a"])
        self.assertEqual(prefixes(""), ["CHANGES.md"])
        with self.assertRaises(SystemExit):
            prefixes('changelog = "/abs/CHANGES.md"')
        with self.assertRaises(SystemExit):
            prefixes('changelog = true')

    def test_the_real_project_config_allows_the_manifests_and_the_changelog(self) -> None:
        allowed = lane.generated_prefixes()
        self.assertIn("package-manifests-versioned", allowed)
        self.assertIn("CHANGES.md", allowed)

    def test_a_changelog_only_change_since_the_build_is_accepted_but_a_source_change_is_not(self) -> None:
        import tempfile

        with tempfile.TemporaryDirectory() as tmp:
            root, env = self.make_build(tmp)
            # no explicit prefixes: the lane reads the project config itself
            for changed in ("CHANGES.md\npackage-manifests-versioned/x.md\n", "package-manifests-versioned/x.md\n"):
                layout = lane.find_layout(root, "trixie-py314-php85", git=self.fake_git(changed=changed), build_env=env)
                self.assertEqual(layout.name, "trixie-py314-php85")
            with self.assertRaises(SystemExit):
                lane.find_layout(root, "trixie-py314-php85", git=self.fake_git(changed="CHANGES.md\nscripts/x.py\n"), build_env=env)

    def test_do_build_binds_the_layouts_after_a_successful_load_flow_build(self) -> None:
        source = (Path(lane.__file__).resolve().parents[1] / "build-push.py").read_text(encoding="utf-8")
        body = source[source.index("def do_build("):source.index("LAYOUT_TRANSACTION_FILE")]
        self.assertIn('if release_image_flow == "load":\n        write_layout_transaction(build_date, build_timestamp)', body)
        self.assertLess(body.index('run_step(config_path, "build")'), body.index("write_layout_transaction("))
        self.assertLess(body.index("write_layout_transaction("), body.index("extract_manifests(build_date, env_vars)"))

    def test_the_private_tag_and_container_are_removed_even_when_the_load_fails(self) -> None:
        source = Path(lane.__file__).read_text(encoding="utf-8")
        main = source[source.index("def main("):]
        finally_block = main[main.index("    finally:"):]
        self.assertIn('"docker", "rm", "-f", name', finally_block)
        self.assertIn('"docker", "rmi", tag', finally_block)
        self.assertNotIn("if imported", finally_block, "the tag is removed unconditionally")
        self.assertLess(main.index("try:"), main.index("import_image(layout, tag)"), "the load runs inside the try")

    def test_the_export_is_platform_pinned_and_named_for_docker_load(self) -> None:
        argv = lane.export_argv(Path("/l"), "sha256:" + "a" * 64, "mdt-cockpit-oci-push:9")
        self.assertEqual(argv[:3], ["regctl", "image", "export"])
        self.assertIn("--name", argv)
        self.assertEqual(argv[argv.index("--name") + 1], "mdt-cockpit-oci-push:9")
        self.assertEqual(argv[-1], "ocidir:///l@sha256:" + "a" * 64)

    def test_the_layout_root_is_build_pushs_own_resolution(self) -> None:
        self.assertEqual(lane.layout_root(), Path(lane.__file__).resolve().parents[1] / "build" / "oci-layouts")

    def test_the_container_is_gate_placed_reaped_named_and_forces_the_requirement(self) -> None:
        argv = lane.docker_run_argv("img:1", "g.slice", "mdt-cockpit-oci-push-7", "unix:///run/buildkit-fixture/bk.sock")
        joined = " ".join(argv)
        self.assertIn("--cgroup-parent g.slice", joined)
        self.assertIn("--init", argv)
        self.assertIn("--rm", argv)
        self.assertIn("--name mdt-cockpit-oci-push-7", joined)
        self.assertIn("MDT_OCI_PUSH_REQUIRED=1", argv)
        self.assertIn("/var/run/docker.sock:/var/run/docker.sock", joined)
        self.assertEqual(argv[argv.index("img:1") - 1], "bash", "image follows --entrypoint bash")
        # nothing from the devcontainer filesystem is bind-mounted except the daemon socket and the
        # managed BuildKit socket directory (the only builder the host guard lets run)
        self.assertEqual(
            [a for i, a in enumerate(argv) if argv[i - 1] == "-v"],
            ["/var/run/docker.sock:/var/run/docker.sock", "/run/buildkit-fixture:/run/buildkit-fixture"],
        )
        self.assertIn("BUILDKIT_HOST=unix:///run/buildkit-fixture/bk.sock", argv)
        self.assertNotIn("--network", argv)

    def test_the_managed_buildkit_endpoint_is_required_never_guessed(self) -> None:
        for env in ({}, {lane.ENV_BUILDKIT_HOST: ""}, {lane.ENV_BUILDKIT_HOST: "tcp://x:1"}):
            with self.assertRaises(SystemExit):
                lane.require_buildkit_host(env)
        self.assertEqual(
            lane.require_buildkit_host({lane.ENV_BUILDKIT_HOST: " unix:///run/buildkit-fixture/bk.sock "}),
            "unix:///run/buildkit-fixture/bk.sock",
        )

    def test_the_push_test_never_creates_a_container_driver_builder(self) -> None:
        import test_oci_layout_push as push_tests

        source = Path(push_tests.__file__).read_text(encoding="utf-8")
        self.assertNotIn('"--driver", "docker-container"', source)
        self.assertIn('"--driver", "remote"', source)

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
        self.assertIn("--deselect scripts/test_oci_layout_push.py", smoke)
        self.assertIn(node, lane.INNER)

    def test_the_bake_test_fails_instead_of_skipping_when_required(self) -> None:
        import test_local_dev as local_dev_tests

        source = Path(local_dev_tests.__file__).read_text(encoding="utf-8")
        self.assertIn("not _buildx_available() and not _buildx_required()", source)
        self.assertIn("MDT_OCI_PUSH_REQUIRED=1 but docker buildx is not available", source)


if __name__ == "__main__":
    unittest.main()
