from __future__ import annotations

import importlib.util
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock


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

    def test_guard_periodic_rescan_catches_container_after_initial_scan(self) -> None:
        config = GUARD.GuardConfig("report-only", "moby/buildkit:buildx-stable-1-rootless")
        scans = iter(([], ["created-between-scans"]))
        reconciled = []

        class Clock:
            now = 0.0

            def __call__(self):
                return self.now

        clock = Clock()

        class Stream:
            def readline(self):
                return ""

        class Process:
            def __init__(self):
                self.stdout = Stream()
                self.stderr = None
                self.returncode = None
                self.waited = False

            def poll(self):
                return self.returncode

            def wait(self):
                self.waited = True
                return self.returncode

            def terminate(self):
                self.returncode = 1

        process = Process()

        class Selector:
            def register(self, stream, event):
                self.stream = stream

            def select(self, timeout):
                if clock.now == 0.0:
                    clock.now = 1.0
                    process.returncode = 0
                    return []
                process.returncode = 1
                return [(self.stream, 1)]

            def unregister(self, stream):
                self.asserted_stream = stream

            def close(self):
                pass

        with mock.patch.object(GUARD, "_container_ids", side_effect=lambda docker: next(scans)), \
             mock.patch.object(GUARD, "reconcile", side_effect=lambda docker, container_id, cfg: reconciled.append(container_id)):
            with self.assertRaises(GUARD.GuardError):
                GUARD.watch(
                    "docker",
                    config,
                    rescan_interval=1.0,
                    popen=lambda *args, **kwargs: process,
                    clock=clock,
                    selector_factory=Selector,
                )

        self.assertEqual(reconciled, ["created-between-scans"])
        self.assertTrue(process.waited)

    def test_builder_creates_only_explicit_remote_and_is_idempotent(self) -> None:
        class FakeDocker:
            def __init__(self):
                self.calls = []
                self.exists = False

            def __call__(self, argv, **kwargs):
                self.calls.append((argv, kwargs))
                if argv[1:4] == ["buildx", "inspect", BUILDER.MANAGED_BUILDER] and "--bootstrap" not in argv:
                    if not self.exists:
                        return subprocess.CompletedProcess(argv, 1, "", "missing")
                    return subprocess.CompletedProcess(argv, 0, "Driver: remote\nEndpoint: unix:///run/mdt-buildkitd/buildkitd.sock\n", "")
                if argv[1:3] == ["buildx", "create"]:
                    self.exists = True
                return subprocess.CompletedProcess(argv, 0, "", "")

        fake = FakeDocker()
        BUILDER.ensure_managed_builder("docker", buildx_config="/etc/mdt/buildx", runner=fake)
        BUILDER.ensure_managed_builder("docker", buildx_config="/etc/mdt/buildx", runner=fake)
        creates = [call for call, _ in fake.calls if call[1:3] == ["buildx", "create"]]
        self.assertEqual(len(creates), 1)
        self.assertIn("--driver", creates[0])
        self.assertIn("remote", creates[0])
        self.assertIn("unix:///run/mdt-buildkitd/buildkitd.sock", creates[0])
        self.assertNotIn("docker-container", " ".join(creates[0]))
        for _, kwargs in fake.calls:
            self.assertEqual(kwargs["env"]["BUILDX_CONFIG"], "/etc/mdt/buildx")
        profile = (ROOT / "etc/profile.d/mdt-buildkit.sh").read_text()
        self.assertIn("export BUILDX_CONFIG=/etc/mdt/buildx", profile)
        self.assertIn("export BUILDX_BUILDER=mdt-managed", profile)
        install = (ROOT / "install.sh").read_text()
        self.assertIn('--buildx-config "$BUILDX_CONFIG_DIR"', install)

    def test_wizard_enforces_memory_order_and_live_aggregate(self) -> None:
        proposals = WIZARD.propose_memory_tiers(16 * 1024 * 1024, 2500 * 1024)
        self.assertEqual(proposals["DEV_MEMORY_HIGH"], "2.5G")
        for prefix in ("DEV_INTERACTIVE", "DEV_BACKGROUND", "DEV_GATES", "DEV_BUILDKITD"):
            self.assertLessEqual(
                WIZARD.parse_size_to_kib(proposals[f"{prefix}_MEMORY_LOW"]),
                WIZARD.parse_size_to_kib(proposals[f"{prefix}_MEMORY_HIGH"]),
            )
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

    def test_guaranteed_sibling_memory_min_confirms_and_updates_shared_key(self) -> None:
        values = {"DEV_MEMORY_MIN_GUARANTEED_CEILING": "1G"}
        with mock.patch.object(WIZARD, "_prompt", return_value="2G") as prompt:
            WIZARD._prompt_shared_memory_min(values)

        self.assertEqual(values, {"DEV_MEMORY_MIN_GUARANTEED_CEILING": "2G"})
        prompt_text = prompt.call_args.args[0]
        self.assertIn("DEV_MEMORY_MIN_GUARANTEED_CEILING", prompt_text)
        self.assertIn("updates both", prompt_text)

    def test_wizard_validates_manual_config_and_guaranteed_sibling_aggregate(self) -> None:
        example = (ROOT / "host-setup.env.example").read_text()
        valid = example.replace("DEV_MEMORY_HIGH=\n", "DEV_MEMORY_HIGH=32G\n")
        valid = valid.replace("DEV_MEMORY_MAX=\n", "DEV_MEMORY_MAX=64G\n")
        meminfo = "MemTotal: 67108864 kB\nMemAvailable: 67108864 kB\n"
        with tempfile.TemporaryDirectory() as directory:
            directory_path = Path(directory)
            config_path = directory_path / "host-setup.env"
            meminfo_path = directory_path / "meminfo"
            config_path.write_text(valid)
            meminfo_path.write_text(meminfo)
            WIZARD.validate_host_setup_config(
                config_path, ROOT / "host-setup.env.example", meminfo_path
            )
            self.assertEqual(
                WIZARD.main(
                    [
                        "--validate-config",
                        str(config_path),
                        "--example",
                        str(ROOT / "host-setup.env.example"),
                        "--meminfo-path",
                        str(meminfo_path),
                    ]
                ),
                0,
            )

            sibling_over_budget = valid.replace(
                "DEV_MEMORY_MIN_GUARANTEED_HIGH=\n",
                "DEV_MEMORY_MIN_GUARANTEED_HIGH=16G\n",
            ).replace(
                "DEV_MEMORY_MIN_GUARANTEED_MAX=\n",
                "DEV_MEMORY_MIN_GUARANTEED_MAX=16G\n",
            )
            config_path.write_text(sibling_over_budget)
            with self.assertRaises(WIZARD.TemplateError) as raised:
                WIZARD.validate_host_setup_config(
                    config_path, ROOT / "host-setup.env.example", meminfo_path
                )
            self.assertIn("memory aggregate", str(raised.exception))

            invalid_hierarchy = valid.replace(
                "DEV_BUILDKITD_MEMORY_LOW=\n", "DEV_BUILDKITD_MEMORY_LOW=7G\n"
            )
            config_path.write_text(invalid_hierarchy)
            with self.assertRaises(WIZARD.TemplateError) as raised:
                WIZARD.validate_host_setup_config(
                    config_path, ROOT / "host-setup.env.example", meminfo_path
                )
            self.assertIn("memory hierarchy", str(raised.exception))

        install = (ROOT / "install.sh").read_text()
        self.assertIn('--validate-config "$CANDIDATE_PATH"', install)
        self.assertIn("--meminfo-path /proc/meminfo", install)

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

    def test_live_build_docs_use_managed_remote_names(self) -> None:
        docs = (
            ROOT.parent / "USAGE.md",
            ROOT.parent / "docs/BUILD-ARCHITECTURE.md",
            ROOT.parent / "docs/OCI-IMAGE-TOOLING.md",
            ROOT.parent / "docs/DOCKER-IMAGE-STORE.md",
            ROOT / "README.md",
        )
        stale_names = ("mdt-governed-v1", "host-buildkitd", "builder_container", "docker-container")
        for path in docs:
            text = path.read_text()
            for stale in stale_names:
                self.assertNotIn(stale, text, f"stale live builder guidance in {path}: {stale}")
            self.assertIn("mdt-managed", text, f"managed remote is not named in {path}")

    def test_release_shell_scripts_parse(self) -> None:
        scripts = [ROOT.parent / "scripts/ensure-release-builder.sh", ROOT.parent / "scripts/release-bake.sh"]
        result = subprocess.run(["bash", "-n", *map(str, scripts)], check=False)
        self.assertEqual(result.returncode, 0)

    def test_installer_validates_candidate_before_installing_or_backing_up(self) -> None:
        install = (ROOT / "install.sh").read_text()
        validate = install.index('--validate-config "$CANDIDATE_PATH"')
        install_config = install.index('mkdir -p "$CONFIG_DIR" /var/lib/mdt')
        self.assertLess(validate, install_config)
        self.assertIn('CANDIDATE_DIR="$(mktemp -d', install)
        self.assertIn('cp -- "$HERE/host-setup.env.example" "$CANDIDATE_PATH"', install)
        self.assertIn("no valid /etc/mdt/host-setup.env exists; the shipped example is incomplete", install)
        self.assertIn("--force requires --wizard", install)
        self.assertNotIn('cp -- "$HERE/host-setup.env.example" /etc/mdt/host-setup.env', install)


if __name__ == "__main__":
    unittest.main()
