from __future__ import annotations

import importlib.util
import io
import json
import os
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
BASELINE = load_module(ROOT / "scripts/mdt-io-baseline.py", "mdt_baseline_test")
GUARD = load_module(ROOT / "scripts/mdt-buildkit-guard.py", "mdt_guard_test")
BUILDER = load_module(ROOT.parent / "scripts/mdt_buildkit_builder.py", "mdt_builder_test")


class BuildKitGovernanceTests(unittest.TestCase):
    def test_wizard_defaults_preserve_existing_empty_and_name_provenance(self) -> None:
        self.assertEqual(
            WIZARD.resolve_default("OPTIONAL", {"OPTIONAL": ""}, {"OPTIONAL": "6G"}, "8G"),
            "",
        )
        with mock.patch.object(WIZARD, "_prompt", return_value="") as prompt:
            value = WIZARD.walk_key(
                "optional size", "OPTIONAL", {"OPTIONAL": "2G"}, {},
                validate=WIZARD.validate_optional_positive_size,
                allow_empty_token=True,
            )
        self.assertEqual(value, "2G")
        self.assertIn("existing: 2G", prompt.call_args.args[0])
        self.assertNotIn("<function", prompt.call_args.args[0])

    def test_wizard_optional_none_and_invalid_yes_no_are_visible(self) -> None:
        with mock.patch.object(WIZARD, "_prompt", return_value="none"):
            self.assertEqual(
                WIZARD.ask(
                    "optional", "2G", WIZARD.validate_optional_positive_size,
                    empty_token="none",
                ),
                "",
            )
        with mock.patch.object(WIZARD, "_prompt", return_value="-"):
            self.assertEqual(
                WIZARD.ask(
                    "optional", "2G", WIZARD.validate_optional_positive_size,
                    empty_token="none",
                    empty_tokens=("-",),
                ),
                "",
            )
        with mock.patch.object(WIZARD, "_prompt", side_effect=["maybe", "n"]):
            self.assertFalse(WIZARD.ask_yn("policy", default=True))

    def test_wizard_migrates_legacy_watcher_names(self) -> None:
        legacy = {
            "SWEEP_IO_CAP_PCT": "61",
            "TESTRUNNER_IMAGE_PATTERNS": "*runner*",
            "BUILDKIT_NAME_PATTERNS": "old_buildkit_*",
            "DEVCONTAINER_NAME_PATTERNS": "*ide*",
            "SWEEP_INTERVAL": "7min",
        }
        migrated = WIZARD.migrate_legacy_config_keys(legacy)
        self.assertEqual(migrated["WATCHER_IO_CAP_PCT"], "61")
        self.assertEqual(migrated["WATCHER_TESTRUNNER_IMAGE_PATTERNS"], "*runner*")
        self.assertEqual(migrated["WATCHER_BUILDKIT_NAME_PATTERNS"], "old_buildkit_*")
        self.assertEqual(migrated["WATCHER_DEVCONTAINER_NAME_PATTERNS"], "*ide*")
        self.assertEqual(migrated["WATCHER_INTERVAL"], "7min")

    def test_memory_sibling_sums_are_allowed_and_parent_mismatches_warn_only(self) -> None:
        values = {
            "DEV_MEMORY_HIGH": "4G",
            "DEV_MEMORY_MAX": "8G",
            "DEV_INTERACTIVE_MEMORY_HIGH": "2G",
            "DEV_INTERACTIVE_MEMORY_MAX": "10G",
            "DEV_BACKGROUND_MEMORY_HIGH": "2G",
            "DEV_BACKGROUND_MEMORY_MAX": "2G",
            "DEV_GATES_MEMORY_HIGH": "2G",
            "DEV_GATES_MEMORY_MAX": "2G",
            "DEV_BUILDKITD_MEMORY_HIGH": "2G",
            "DEV_BUILDKITD_MEMORY_MAX": "2G",
            "DEV_INTERACTIVE_MEMORY_MIN": "",
            "DEV_INTERACTIVE_MEMORY_LOW": "",
            "DEV_BACKGROUND_MEMORY_MIN": "",
            "DEV_BACKGROUND_MEMORY_LOW": "",
            "DEV_GATES_MEMORY_MIN": "",
            "DEV_GATES_MEMORY_LOW": "",
            "DEV_BUILDKITD_MEMORY_MIN": "",
            "DEV_BUILDKITD_MEMORY_LOW": "",
            "DEV_MEMORY_MIN_GUARANTEED_CEILING": "",
        }
        with mock.patch.object(WIZARD, "_prompt") as prompt, \
             mock.patch.object(WIZARD, "out") as output:
            WIZARD._reprompt_memory_constraints(0, values)
        self.assertEqual(values["DEV_MEMORY_HIGH"], "4G")
        self.assertEqual(prompt.call_count, 0)
        warnings = WIZARD.memory_review_warnings(values)
        self.assertEqual(len(warnings), 1)
        self.assertIn("dev-interactive.slice MemoryMax=10G", warnings[0])
        self.assertTrue(any("does not block" in call.args[0] for call in output.call_args_list))

    def test_io_discovery_does_not_promote_container_overlay_to_host_device(self) -> None:
        def runner(argv, **kwargs):
            source = "overlay\n" if argv[-1] == "/var/lib/docker" else "/dev/nvme0n1\n"
            return subprocess.CompletedProcess(argv, 0, stdout=source)

        self.assertEqual(
            WIZARD.discover_io_dev_path_from_findmnt(runner), "/dev/nvme0n1"
        )
        self.assertIsNotNone(WIZARD.validate_io_dev_path("overlay"))
        self.assertIsNone(WIZARD.validate_io_dev_path("/dev/mapper/vg-root"))
        self.assertIsNotNone(WIZARD.validate_io_dev_path("/dev/sda;touch /tmp/pwned"))
        self.assertIsNotNone(WIZARD.validate_absolute_path("/var/lib/mdt/$(touch-pwned)"))

    def test_host_context_guard_rejects_this_container(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / ".dockerenv").touch()
            self.assertIn("host shell", WIZARD.host_context_error(root) or "")
            self.assertIn("host shell", BASELINE.host_context_error(root) or "")

    def test_baseline_cache_requires_complete_iocost_measurement(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "baseline.env"
            path.write_text(
                "SCHEMA_VERSION=2\nKERNEL_RELEASE=test\nGENERATOR_SHA256=test\n"
                "RIOPS_MAX=1\nWIOPS_MAX=1\nRBW_MAX_BPS=1\nWBW_MAX_BPS=1\n"
                "DEVNO=8:0\nTESTFILE_STAT_DEV=1\nDOCKER_STAT_DEV=1\n"
                "FINDMNT_SOURCE=/dev/sda\nDEVICE_SIZE_SECTORS=1\n"
                "DEVICE_ROTATIONAL=0\nDEVICE_TOPOLOGY=/sys/devices/test\n"
                "TESTFILE=/var/lib/mdt/testfile\nTESTFILE_SIZE_BYTES=1\n"
                "RBPS=1\nRSEQIOPS=1\nRRANDIOPS=1\nWBPS=1\nWSEQIOPS=1\nWRANDIOPS=1\n"
                "MEASURED_AT=2000-01-01T00:00:00Z\nMEASURE_METHOD=iocost-coef-gen\n"
            )
            self.assertTrue(BASELINE.cache_is_valid(path))
            target = Path(directory) / "testfile"
            target.write_bytes(b"x")
            values = BASELINE.parse_env(path)
            values["KERNEL_RELEASE"] = os.uname().release
            values["TESTFILE"] = str(target)
            values["TESTFILE_SIZE_BYTES"] = "1"
            values["DEVICE_SERIAL"] = ""
            values["DEVICE_MODEL"] = ""
            path.write_text("\n".join(f"{key}={value}" for key, value in values.items()) + "\n")
            current_identity = {
                "DEVNO": "8:0", "TESTFILE_STAT_DEV": "1", "DOCKER_STAT_DEV": "1",
                "FINDMNT_SOURCE": "/dev/sda", "DEVICE_SERIAL": "",
                "DEVICE_MODEL": "", "DEVICE_SIZE_SECTORS": "1",
                "DEVICE_ROTATIONAL": "0", "DEVICE_TOPOLOGY": "/sys/devices/test",
            }
            with mock.patch.object(BASELINE, "discover_generator", return_value=None), \
                 mock.patch.object(BASELINE, "device_identity", return_value=current_identity):
                self.assertTrue(BASELINE.cache_is_current(path, target))
            path.write_text("RIOPS_MAX=1\n")
            self.assertFalse(BASELINE.cache_is_valid(path))

    def test_io_watcher_accepts_docker_actor_id_event_shape_and_keeps_status(self) -> None:
        watcher = ROOT / "scripts" / "mdt-io-cap-watcher.sh"
        service = (ROOT / "units" / "mdt-io-cap-watcher.service").read_text()
        self.assertIn("--format '{{.Actor.ID}}'", watcher.read_text())
        self.assertIn("Restart=always", service)
        self.assertIn("RestartSec=5", service)

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            fake_bin = root / "bin"
            fake_bin.mkdir()
            docker_log = root / "docker.log"
            config = root / "host-setup.env"
            config.write_text(
                f"IO_DEV_PATH=/dev/fake\nIO_BASELINE_ENV={root / 'missing-baseline'}\n"
            )
            docker = fake_bin / "docker"
            docker.write_text(
                """#!/usr/bin/env bash
set -u
case "${1:-}" in
  info|ps) exit 0 ;;
  inspect)
    template="${3:-}"
    cid="${4:-}"
    printf '%s %s\\n' "$template" "$cid" >> "$FAKE_DOCKER_LOG"
    case "$template" in
      *State.Pid*) echo 0 ;;
      *Config.Image*) echo test-runner:fake ;;
      *Name*) echo /test-runner ;;
      *) exit 2 ;;
    esac
    ;;
  events)
    format=""
    while [ "$#" -gt 0 ]; do
      case "$1" in
        --format) format="$2"; shift 2 ;;
        *) shift ;;
      esac
    done
    [ "$format" = "{{.Actor.ID}}" ] || { echo "unexpected format: $format" >&2; exit 42; }
    printf '%s\\n' '{"status":"start","Actor":{"ID":"event-container-id"}}' \\
      | python3 -c 'import json, sys; print(json.load(sys.stdin)["Actor"]["ID"])'
    exit 17
    ;;
  *) exit 2 ;;
esac
"""
            )
            docker.chmod(0o755)
            systemctl = fake_bin / "systemctl"
            systemctl.write_text("#!/usr/bin/env bash\nexit 0\n")
            systemctl.chmod(0o755)
            env = os.environ.copy()
            env.update({
                "CONF": str(config),
                "FAKE_DOCKER_LOG": str(docker_log),
                "PATH": f"{fake_bin}:/usr/bin:/bin",
            })
            result = subprocess.run(
                [str(watcher)], env=env, text=True,
                stdout=subprocess.PIPE, stderr=subprocess.STDOUT, timeout=10,
                check=False,
            )
            self.assertEqual(result.returncode, 1)
            self.assertIn("docker events stream ended (status=17)", result.stdout)
            self.assertIn("event-container-id", docker_log.read_text())

    def test_no_baseline_uses_explicit_static_per_container_fallback(self) -> None:
        library = ROOT / "scripts" / "mdt-container-caps.lib.sh"
        with tempfile.TemporaryDirectory() as directory:
            env = os.environ.copy()
            env.update({
                "CONF": str(Path(directory) / "missing-config"),
                "IO_BASELINE_ENV": str(Path(directory) / "missing-baseline"),
            })
            result = subprocess.run(
                ["bash", "-c", f"""
set -u
log() {{ :; }}
. "{library}"
_mdt_load_config
_mdt_load_baseline
_mdt_derive_watcher_caps
printf '%s %s %s %s %s %s\\n' \\
  "$WATCHER_RIOPS" "$WATCHER_WIOPS" "$WATCHER_RBPS" "$WATCHER_WBPS" \\
  "$WATCHER_SRC" "$MDT_IO_BASELINE_VALID"
"""],
                env=env, text=True, stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT, check=False,
            )
            self.assertEqual(result.returncode, 0, result.stdout)
            self.assertEqual(
                result.stdout.strip(),
                "200 400 31457280 31457280 static fallback — no baseline, run mdt-io-baseline.py 0",
            )

    def test_no_baseline_clears_only_mdt_runtime_io_properties(self) -> None:
        apply_script = ROOT / "scripts" / "mdt-apply-dev-caps.sh"
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            fake_bin = root / "bin"
            fake_bin.mkdir()
            state = root / "cgroup-state"
            systemctl_log = root / "systemctl.log"
            missing_baseline = root / "missing-baseline"
            config = root / "host-setup.env"
            config.write_text(
                f"IO_BASELINE_ENV={missing_baseline}\n"
                "CGROUP2_FLAGS=warn\n"
            )
            systemctl = fake_bin / "systemctl"
            systemctl.write_text(
                """#!/usr/bin/env bash
set -u
printf '%s\\n' "$*" >> "$SYSTEMCTL_LOG"
if [ "${1:-}" = set-property ]; then
  unit="$3"
  shift 3
  for assignment in "$@"; do
    key="${assignment%%=*}"
    value="${assignment#*=}"
    [ -z "$value" ] || continue
    tmp="${STATE_PATH}.tmp"
    awk -F'|' -v unit="$unit" -v key="$key" \\
      '!(($1 == unit) && ($2 == key))' "$STATE_PATH" > "$tmp"
    mv "$tmp" "$STATE_PATH"
  done
fi
exit 0
"""
            )
            systemctl.chmod(0o755)
            findmnt = fake_bin / "findmnt"
            findmnt.write_text(
                """#!/usr/bin/env bash
if [ "${2:-}" = SOURCE ]; then
  echo overlay
fi
exit 0
"""
            )
            findmnt.chmod(0o755)
            docker = fake_bin / "docker"
            docker.write_text("#!/usr/bin/env bash\nexit 1\n")
            docker.chmod(0o755)
            env = os.environ.copy()
            env.update({
                "CG": str(root / "cgroup"),
                "CONF": str(config),
                "PATH": f"{fake_bin}:/usr/bin:/bin",
                "STATE_PATH": str(state),
                "SYSTEMCTL_LOG": str(systemctl_log),
            })
            for io_device in ("/dev/fake", ""):
                state.write_text(
                    "dev.slice|IOReadBandwidthMax|stale-read-bandwidth\n"
                    "dev.slice|IOWriteBandwidthMax|stale-write-bandwidth\n"
                    "dev.slice|IOReadIOPSMax|stale-read-iops\n"
                    "dev.slice|IOWriteIOPSMax|stale-write-iops\n"
                    "dev.slice|CPUWeight|900\n"
                    "dev.slice|MemoryMax|123456\n"
                    "dev-gates.slice|IOReadIOPSMax|stale-gates\n"
                    "dev-gates.slice|IOWriteIOPSMax|stale-gates\n"
                    "dev-gates.slice|MemoryMax|234567\n"
                    "dev-buildkitd.slice|IOReadIOPSMax|stale-buildkit-read\n"
                    "dev-buildkitd.slice|IOWriteIOPSMax|stale-buildkit-write\n"
                )
                config.write_text(
                    f"IO_DEV_PATH={io_device}\nIO_BASELINE_ENV={missing_baseline}\n"
                    "CGROUP2_FLAGS=warn\n"
                )
                systemctl_log.write_text("")
                result = subprocess.run(
                    ["bash", str(apply_script)], env=env, text=True,
                    stdout=subprocess.PIPE, stderr=subprocess.STDOUT, check=False,
                )
                self.assertEqual(result.returncode, 0, result.stdout)
                self.assertIn("unit-file static fallback is authoritative", result.stdout)
                calls = systemctl_log.read_text()
                self.assertIn(
                    "set-property --runtime dev.slice IOReadBandwidthMax= IOWriteBandwidthMax= IOReadIOPSMax= IOWriteIOPSMax=",
                    calls,
                )
                self.assertIn(
                    "set-property --runtime dev-gates.slice IOReadIOPSMax= IOWriteIOPSMax=",
                    calls,
                )
                self.assertIn(
                    "set-property --runtime dev-buildkitd.slice IOReadIOPSMax= IOWriteIOPSMax=",
                    calls,
                )
                self.assertNotIn("CPUWeight=", calls)
                self.assertNotIn("MemoryMax=", calls)
                remaining = state.read_text()
                self.assertIn("dev.slice|CPUWeight|900", remaining)
                self.assertIn("dev.slice|MemoryMax|123456", remaining)
                self.assertIn("dev-gates.slice|MemoryMax|234567", remaining)
                self.assertNotIn("IOReadBandwidthMax", remaining)
                self.assertNotIn("IOWriteBandwidthMax", remaining)
                self.assertNotIn("IOReadIOPSMax", remaining)
                self.assertNotIn("IOWriteIOPSMax", remaining)

    def test_memory_proposals_keep_per_slice_order_at_rounding_boundaries(self) -> None:
        for avail_kib in (1 * 1024 * 1024, 25 * 1024, 7_800 * 1024):
            proposals = WIZARD.propose_memory_tiers(avail_kib, avail_kib)
            self.assertEqual(
                proposals["DEV_MEMORY_HIGH"],
                WIZARD.kib_to_size_str(max(1, avail_kib * 75 // 100)),
            )
            self.assertLessEqual(
                WIZARD.parse_size_to_kib(proposals["DEV_MEMORY_HIGH"]),
                WIZARD.parse_size_to_kib(proposals["DEV_MEMORY_MAX"]),
            )
            for prefix in ("DEV_INTERACTIVE", "DEV_BACKGROUND", "DEV_GATES", "DEV_BUILDKITD"):
                self.assertLessEqual(
                    WIZARD.parse_size_to_kib(proposals[f"{prefix}_MEMORY_LOW"]),
                    WIZARD.parse_size_to_kib(proposals[f"{prefix}_MEMORY_HIGH"]),
                )
                self.assertLessEqual(
                    WIZARD.parse_size_to_kib(proposals[f"{prefix}_MEMORY_HIGH"]),
                    WIZARD.parse_size_to_kib(proposals[f"{prefix}_MEMORY_MAX"]),
                )

    def test_slice_flow_passes_reserve_validators_as_validators(self) -> None:
        example = WIZARD.parse_env_file((ROOT / "host-setup.env.example").read_text())
        calls = {}

        def fake_walk(label, key, cfg, defaults, proposal=None, validate=None, **kwargs):
            calls[key] = (label, validate, kwargs)
            return cfg.get(key) or proposal or defaults.get(key, "")

        with mock.patch.object(WIZARD, "walk_key", side_effect=fake_walk), \
             mock.patch.object(WIZARD, "walk_yn_key", side_effect=lambda label, key, cfg, defaults: cfg.get(key) or defaults.get(key, "yes")), \
             mock.patch.object(WIZARD, "ask", side_effect=lambda label, default, validate=None, **kwargs: default):
            WIZARD.step_slice_first_resources(
                {"MemTotal": 16 * 1024 * 1024, "MemAvailable": 16 * 1024 * 1024},
                {}, example,
            )
        self.assertIs(calls["DEV_CPU_RESERVE_CORES"][1], WIZARD.validate_nonneg_int)
        self.assertIs(calls["DEV_SUBSLICE_CPU_RESERVE_CORES"][1], WIZARD.validate_nonneg_int)
        self.assertNotIn("<function", calls["DEV_CPU_RESERVE_CORES"][0])
        memory_keys = {
            "DEV_MEMORY_LOW", "DEV_MEMORY_HIGH", "DEV_MEMORY_MAX",
            "DEV_MEMORY_MIN_GUARANTEED_CEILING",
            "DEV_MEMORY_MIN_GUARANTEED_LOW",
            "DEV_MEMORY_MIN_GUARANTEED_HIGH",
            "DEV_MEMORY_MIN_GUARANTEED_MAX",
            *WIZARD.MEMORY_CHILD_KEYS,
        }
        for key in memory_keys:
            self.assertIs(calls[key][1], WIZARD.validate_optional_positive_size, key)
            self.assertTrue(calls[key][2].get("allow_empty_token"), key)

    def test_memory_relationships_cover_each_adjacent_configured_pair(self) -> None:
        base = {
            "DEV_INTERACTIVE_MEMORY_MIN": "1G",
            "DEV_INTERACTIVE_MEMORY_LOW": "2G",
            "DEV_INTERACTIVE_MEMORY_HIGH": "3G",
            "DEV_INTERACTIVE_MEMORY_MAX": "4G",
        }
        for key, invalid_value, expected_right_key in (
            ("DEV_INTERACTIVE_MEMORY_MIN", "3G", "DEV_INTERACTIVE_MEMORY_LOW"),
            ("DEV_INTERACTIVE_MEMORY_LOW", "4G", "DEV_INTERACTIVE_MEMORY_HIGH"),
            ("DEV_INTERACTIVE_MEMORY_HIGH", "5G", "DEV_INTERACTIVE_MEMORY_MAX"),
        ):
            values = dict(base)
            values[key] = invalid_value
            errors = WIZARD.memory_relationship_errors(values)
            self.assertTrue(errors, key)
            self.assertEqual(errors[0][0], expected_right_key)

        with_optional_gap = dict(base)
        with_optional_gap["DEV_INTERACTIVE_MEMORY_LOW"] = ""
        with_optional_gap["DEV_INTERACTIVE_MEMORY_MIN"] = "3G"
        self.assertFalse(WIZARD.memory_relationship_errors(with_optional_gap))

    def test_memory_validator_map_matches_optional_prompt_contract(self) -> None:
        validators = WIZARD._config_value_validators()
        memory_keys = {
            "DEV_MEMORY_LOW", "DEV_MEMORY_HIGH", "DEV_MEMORY_MAX",
            "DEV_MEMORY_MIN_GUARANTEED_CEILING",
            "DEV_MEMORY_MIN_GUARANTEED_LOW",
            "DEV_MEMORY_MIN_GUARANTEED_HIGH",
            "DEV_MEMORY_MIN_GUARANTEED_MAX",
            *WIZARD.MEMORY_CHILD_KEYS,
        }
        self.assertTrue(memory_keys <= validators.keys())
        for key in memory_keys:
            self.assertIs(validators[key], WIZARD.validate_optional_positive_size, key)
            self.assertIsNone(validators[key](""))
            self.assertIsNone(validators[key]("2G"))
            self.assertIsNotNone(validators[key]("0"))

    def test_custom_baseline_path_is_passed_to_benchmark(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            cache = Path(directory) / "baseline.env"
            cache.write_text("RIOPS_MAX=1\n")
            script = ROOT / "scripts" / "mdt-io-baseline.py"
            with mock.patch.object(WIZARD, "walk_key", return_value=str(cache)), \
                 mock.patch.object(WIZARD, "check_baseline_freshness", return_value="stale"), \
                 mock.patch.object(WIZARD.shutil, "which", return_value="/usr/bin/fio"), \
                 mock.patch.object(WIZARD, "_prompt", return_value="y"), \
                 mock.patch.object(WIZARD.subprocess, "run", return_value=subprocess.CompletedProcess([], 0)) as run:
                selected, target, measured = WIZARD.step_io_baseline({}, {}, script)
            self.assertEqual(selected, str(cache))
            self.assertEqual(target, str(cache))
            self.assertTrue(measured)
            self.assertEqual(run.call_args.kwargs["env"]["IO_BASELINE_ENV"], str(cache))

    def test_iocost_generator_identifies_the_file_target_device(self) -> None:
        generator = ROOT.parent.parent / "scripts/debian-install-v2/tools/iocost_coef_gen.py"
        source = generator.read_text()
        self.assertIn(
            "probe_path = os.path.dirname(os.path.abspath(args.testfile)) if args.testfile else '.'",
            source,
        )
        self.assertIn("devname, devno = dir_to_dev(probe_path)", source)

    def test_interactive_main_smoke_writes_without_function_repr(self) -> None:
        example = ROOT / "host-setup.env.example"
        with tempfile.TemporaryDirectory() as directory:
            directory_path = Path(directory)
            meminfo = directory_path / "meminfo"
            meminfo.write_text("MemTotal: 16777216 kB\nMemAvailable: 16777216 kB\n")
            output = directory_path / "host-setup.env"
            transcript = io.StringIO()
            with mock.patch.object(WIZARD, "host_context_error", return_value=None), \
                 mock.patch.object(WIZARD, "check_baseline_freshness", return_value="fresh"), \
                 mock.patch("sys.stdin", io.StringIO("\n" * 100)), \
                 mock.patch("sys.stdout", transcript):
                self.assertEqual(
                    WIZARD.main([
                        "--example", str(example), "--output", str(output),
                        "--meminfo-path", str(meminfo), "--skip-run-offer",
                    ]),
                    0,
                )
            rendered = output.read_text()
            self.assertNotIn("<function", rendered)
            self.assertIn("DEV_CPU_RESERVE_CORES=1", rendered)
            self.assertIn("DEV_SUBSLICE_CPU_RESERVE_CORES=3", rendered)
            transcript_text = transcript.getvalue()
            self.assertLess(
                transcript_text.index("-- install map: what this creates and enables --"),
                transcript_text.index("-- a. IO device (IO_DEV_PATH) --"),
            )
            for bullet in (
                "mdt-io-cap-watcher.service",
                "DEV_IO_CAP_PCT",
                "WATCHER_IO_CAP_PCT",
                "Starting proposals (review before accepting)",
                "+-- dev-gates.slice",
                "Static fallback values come from",
                "no host mutation occurs",
                "Docker is not restarted automatically",
                "Sibling Min/Low/High controls are independent",
                "does not block",
            ):
                self.assertIn(bullet, transcript_text)
            self.assertNotIn("aggregate violations", transcript_text)

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

    def test_wizard_enforces_memory_order_without_live_availability_budget(self) -> None:
        proposals = WIZARD.propose_memory_tiers(16 * 1024 * 1024, 2500 * 1024)
        self.assertEqual(proposals["DEV_MEMORY_HIGH"], "12G")
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
        self.assertEqual(WIZARD.memory_review_warnings(values), [])
        self.assertEqual(WIZARD.parse_size_to_kib("1.5G"), 1572864)

    def test_guaranteed_sibling_memory_min_confirms_and_updates_shared_key(self) -> None:
        values = {"DEV_MEMORY_MIN_GUARANTEED_CEILING": "1G"}
        with mock.patch.object(WIZARD, "_prompt", return_value="2G") as prompt:
            WIZARD._prompt_shared_memory_min(values)

        self.assertEqual(values, {"DEV_MEMORY_MIN_GUARANTEED_CEILING": "2G"})
        prompt_text = prompt.call_args.args[0]
        self.assertIn("DEV_MEMORY_MIN_GUARANTEED_CEILING", prompt_text)
        self.assertIn("updates both", prompt_text)

    def test_wizard_validates_manual_config_and_reports_parent_review_warnings(self) -> None:
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
            meminfo_path.write_text("MemTotal: 67108864 kB\n")
            WIZARD.validate_host_setup_config(
                config_path, ROOT / "host-setup.env.example", meminfo_path
            )
            with mock.patch.object(WIZARD, "host_context_error", return_value=None):
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

            sibling_over_budget = valid.replace("DEV_MEMORY_HIGH=32G", "DEV_MEMORY_HIGH=4G")
            config_path.write_text(sibling_over_budget)
            warnings = WIZARD.validate_host_setup_config(
                config_path, ROOT / "host-setup.env.example", meminfo_path
            )
            self.assertTrue(any("MemoryHigh" in warning for warning in warnings))
            self.assertTrue(any("does not block" in warning for warning in warnings))
            with mock.patch.object(WIZARD, "host_context_error", return_value=None), \
                 mock.patch("sys.stdout", io.StringIO()) as transcript:
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
                self.assertIn("REVIEW WARNING (does not block)", transcript.getvalue())

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
        self.assertIn("must run from a host shell", install)
        self.assertIn("/proc/1/comm", install)
        self.assertIn("never from a devcontainer", install)
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
