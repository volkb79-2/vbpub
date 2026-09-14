from __future__ import annotations

import importlib.util
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def load_module(path: Path, name: str):
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


WIZARD = load_module(ROOT / "mdt-host-setup-wizard.py", "mdt_wizard_test")
GUARD = load_module(ROOT / "scripts/mdt-buildkit-guard.py", "mdt_guard_test")
BUILDER = load_module(ROOT.parent / "scripts/mdt_buildkit_builder.py", "mdt_builder_test")


class BuildKitGovernanceTests(unittest.TestCase):
    def test_guard_policy_and_missing_config_fail_closed(self) -> None:
        config = GUARD.load_config(ROOT / "host-setup.env.example")
        self.assertEqual(config.policy, "terminate")
        self.assertTrue(config.buildkit_image)
        with self.assertRaises(GUARD.GuardError):
            GUARD.load_config(Path("/definitely/missing/mdt-host-setup.env"))
        with tempfile.TemporaryDirectory() as directory:
            config_path = Path(directory) / "invalid.env"
            config_path.write_text("DEV_BUILDKITD_IMAGE=buildkit\nBUILDX_ACCIDENTAL_CONTAINER_POLICY=ignore\n")
            with self.assertRaises(GUARD.GuardError):
                GUARD.load_config(config_path)

    def test_guard_requires_identity_not_name(self) -> None:
        config = GUARD.GuardConfig("terminate", "moby/buildkit:buildx-stable-1-rootless")
        base = {
            "Name": "/mdt-buildkitd",
            "Config": {
                "Image": config.buildkit_image,
                "Labels": dict(GUARD.MANAGED_LABELS),
            },
            "HostConfig": {"CgroupParent": GUARD.MANAGED_CGROUP_PARENT},
        }
        approved = GUARD.from_inspect("managed-id", [base])
        self.assertEqual(GUARD.decide(approved, config).status, "approved-managed")
        for change in (
            {"HostConfig": {"CgroupParent": "dev-background.slice"}},
            {"Config": {"Image": "moby/buildkit:latest", "Labels": dict(GUARD.MANAGED_LABELS)}},
            {"Config": {"Image": config.buildkit_image, "Labels": {}}},
        ):
            altered = json.loads(json.dumps(base))
            altered.update(change)
            decision = GUARD.decide(GUARD.from_inspect("bad-id", [altered]), config)
            self.assertEqual(decision.status, "unapproved-managed-name")
            self.assertTrue(decision.enforce)
        non_worker = json.loads(json.dumps(base))
        non_worker["Name"] = "/buildx_buildkit_not-a-worker"
        non_worker["Config"]["Image"] = "alpine:3.20"
        self.assertEqual(GUARD.decide(GUARD.from_inspect("x", [non_worker]), config).status, "ignored")

    def test_builder_creates_only_explicit_remote_and_is_idempotent(self) -> None:
        class FakeDocker:
            def __init__(self):
                self.calls = []
                self.exists = False

            def __call__(self, argv, **_kwargs):
                self.calls.append(argv)
                if argv[1:4] == ["buildx", "inspect", BUILDER.MANAGED_BUILDER] and "--bootstrap" not in argv:
                    if not self.exists:
                        return subprocess.CompletedProcess(argv, 1, "", "missing")
                    return subprocess.CompletedProcess(argv, 0, "Driver: remote\nEndpoint: unix:///run/mdt-buildkitd/buildkitd.sock\n", "")
                if argv[1:3] == ["buildx", "create"]:
                    self.exists = True
                return subprocess.CompletedProcess(argv, 0, "", "")

        fake = FakeDocker()
        BUILDER.ensure_managed_builder("docker", runner=fake)
        BUILDER.ensure_managed_builder("docker", runner=fake)
        creates = [call for call in fake.calls if call[1:3] == ["buildx", "create"]]
        self.assertEqual(len(creates), 1)
        self.assertIn("--driver", creates[0])
        self.assertIn("remote", creates[0])
        self.assertNotIn("docker-container", " ".join(creates[0]))

    def test_wizard_enforces_memory_order_and_live_aggregate(self) -> None:
        self.assertEqual(WIZARD.propose_memory_tiers(16 * 1024 * 1024, 8 * 1024 * 1024)["DEV_MEMORY_HIGH"], "8G")
        values = {
            "DEV_MEMORY_HIGH": "8G", "DEV_MEMORY_MAX": "16G",
            "DEV_INTERACTIVE_MEMORY_MIN": "", "DEV_INTERACTIVE_MEMORY_LOW": "2G",
            "DEV_INTERACTIVE_MEMORY_HIGH": "1G", "DEV_INTERACTIVE_MEMORY_MAX": "3G",
            "DEV_BACKGROUND_MEMORY_MIN": "", "DEV_BACKGROUND_MEMORY_LOW": "",
            "DEV_BACKGROUND_MEMORY_HIGH": "1G", "DEV_BACKGROUND_MEMORY_MAX": "2G",
            "DEV_GATES_MEMORY_MIN": "", "DEV_GATES_MEMORY_LOW": "",
            "DEV_GATES_MEMORY_HIGH": "1G", "DEV_GATES_MEMORY_MAX": "2G",
            "DEV_BUILDKITD_MEMORY_MIN": "", "DEV_BUILDKITD_MEMORY_LOW": "",
            "DEV_BUILDKITD_MEMORY_HIGH": "1G", "DEV_BUILDKITD_MEMORY_MAX": "2G",
            "DEV_MEMORY_MIN_GUARANTEED_CEILING": "",
        }
        self.assertTrue(WIZARD.memory_relationship_errors(values))
        self.assertIsNotNone(WIZARD.memory_aggregate_error(3 * 1024 * 1024, values))
        self.assertEqual(WIZARD.parse_size_to_kib("1.5G"), 1572864)

    def test_template_has_all_memory_controls_and_managed_buildkit(self) -> None:
        example = (ROOT / "host-setup.env.example").read_text()
        templates = "\n".join(path.read_text() for path in (ROOT / "units").glob("*.in"))
        for key in (
            "DEV_MEMORY_MIN_GUARANTEED_CEILING", "DEV_MEMORY_LOW", "DEV_MEMORY_HIGH", "DEV_MEMORY_MAX",
            "DEV_INTERACTIVE_MEMORY_MIN", "DEV_INTERACTIVE_MEMORY_LOW", "DEV_INTERACTIVE_MEMORY_HIGH", "DEV_INTERACTIVE_MEMORY_MAX",
            "DEV_BACKGROUND_MEMORY_MIN", "DEV_BACKGROUND_MEMORY_LOW", "DEV_BACKGROUND_MEMORY_HIGH", "DEV_BACKGROUND_MEMORY_MAX",
            "DEV_GATES_MEMORY_MIN", "DEV_GATES_MEMORY_LOW", "DEV_GATES_MEMORY_HIGH", "DEV_GATES_MEMORY_MAX",
            "DEV_BUILDKITD_MEMORY_MIN", "DEV_BUILDKITD_MEMORY_LOW", "DEV_BUILDKITD_MEMORY_HIGH", "DEV_BUILDKITD_MEMORY_MAX",
            "DEV_MEMORY_MIN_GUARANTEED_LOW", "DEV_MEMORY_MIN_GUARANTEED_HIGH", "DEV_MEMORY_MIN_GUARANTEED_MAX",
        ):
            self.assertIn(f"{key}=", example)
            self.assertIn(f"@{key}@", templates)
        template = (ROOT / "../templates/devcontainer.json").resolve().read_text()
        self.assertIn('"BUILDX_BUILDER": "mdt-managed"', template)
        self.assertIn('source=/run/mdt-buildkitd,target=/run/mdt-buildkitd,type=bind', template)
        self.assertNotIn("host-buildkitd", template)
        self.assertNotIn("OPTIONAL and commented out", template)
        self.assertIn('BUILDX_BUILDER = "mdt-managed"', (ROOT.parent / "cmru.toml").read_text())
        release = (ROOT.parent / "scripts/ensure-release-builder.sh").read_text()
        self.assertNotIn("docker-container", release)
        self.assertNotIn("buildx rm", release)

    def test_release_shell_scripts_parse(self) -> None:
        scripts = [ROOT.parent / "scripts/ensure-release-builder.sh", ROOT.parent / "scripts/release-bake.sh"]
        result = subprocess.run(["bash", "-n", *map(str, scripts)], check=False)
        self.assertEqual(result.returncode, 0)


if __name__ == "__main__":
    unittest.main()
