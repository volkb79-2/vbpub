"""Contract tests for the stdlib estate requirement checker."""
from __future__ import annotations

import importlib.util
import io
import json
import os
import tempfile
import sys
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[2]
DOCTOR_PATH = ROOT / "host-setup" / "estate_doctor.py"
MANIFEST_PATH = ROOT / "docs" / "ESTATE-REQUIREMENTS.toml"
SPEC = importlib.util.spec_from_file_location("estate_doctor_test_subject", DOCTOR_PATH)
assert SPEC is not None and SPEC.loader is not None
DOCTOR = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = DOCTOR
SPEC.loader.exec_module(DOCTOR)

IMAGE_CHECK_PATH = ROOT / "host-setup" / "check_tester_run_gate_image.py"
IMAGE_CHECK_SPEC = importlib.util.spec_from_file_location("estate_run_gate_image_test_subject", IMAGE_CHECK_PATH)
assert IMAGE_CHECK_SPEC is not None and IMAGE_CHECK_SPEC.loader is not None
IMAGE_CHECK = importlib.util.module_from_spec(IMAGE_CHECK_SPEC)
sys.modules[IMAGE_CHECK_SPEC.name] = IMAGE_CHECK
IMAGE_CHECK_SPEC.loader.exec_module(IMAGE_CHECK)


class FakeRunner:
    def __init__(self, *results):
        self.results = list(results)
        self.calls = []

    def run(self, argv, timeout):
        self.calls.append((list(argv), timeout))
        if not self.results:
            raise AssertionError("unexpected process invocation")
        return self.results.pop(0)


def minimal_data(*, minimum_version="capability-based", required=True):
    return {
        "components": {
            "probe": {
                "kind": "test",
                "applies_to": "both",
                "requires": [],
                "provides": ["probe"],
                "minimum_version": minimum_version,
                "required": required,
                "verify": {
                    "command": "printf PASS",
                    "expected_regex": "^PASS$",
                    "timeout_seconds": 2,
                },
                "fix": "repair it",
                "owner": "mdt-cockpit",
                "doc": "CONSUMERS.md",
            }
        }
    }


class EstateDoctorTests(unittest.TestCase):
    def run_probe(self, fake_result, data=None, remote=None):
        runner = FakeRunner(fake_result)
        results = DOCTOR.run_checks(
            data or minimal_data(), "controller", runner, MANIFEST_PATH,
            ROOT.parent, remote,
        )
        return runner, results[0]

    def test_probe_passes_expected_output(self):
        runner, row = self.run_probe(DOCTOR.Result(0, "PASS\n"))
        self.assertEqual(row["status"], "pass")
        self.assertEqual(row["evidence"], "PASS")
        self.assertEqual(len(runner.calls), 1)

    def test_probe_fails_on_nonzero_exit(self):
        _, row = self.run_probe(DOCTOR.Result(2, "", "ignored diagnostic"))
        self.assertEqual(row["status"], "fail")
        self.assertEqual(row["evidence"], "verifier exited 2")

    def test_indeterminate_probe_is_unknown(self):
        _, row = self.run_probe(DOCTOR.Result(DOCTOR.UNKNOWN_EXIT))
        self.assertEqual(row["status"], "unknown")
        self.assertIn("could not observe", row["evidence"])

    def test_timeout_is_unknown_with_distinct_evidence(self):
        _, row = self.run_probe(DOCTOR.Result(DOCTOR.TIMEOUT_EXIT))
        self.assertEqual(row["status"], "unknown")
        self.assertIn("timed out", row["evidence"])

    def test_runner_timeout_exception_is_unknown(self):
        class TimeoutRunner(FakeRunner):
            def run(self, argv, timeout):
                self.calls.append((list(argv), timeout))
                raise TimeoutError

        runner = TimeoutRunner()
        results = DOCTOR.run_checks(
            minimal_data(), "controller", runner, MANIFEST_PATH, ROOT.parent,
        )
        self.assertEqual(results[0]["status"], "unknown")
        self.assertIn("timed out", results[0]["evidence"])

    def test_process_runner_timeout_kills_its_process_group(self):
        class FakeProcess:
            pid = 4321
            returncode = 0

            def __init__(self):
                self.calls = 0

            def communicate(self, timeout):
                self.calls += 1
                if self.calls == 1:
                    raise DOCTOR.subprocess.TimeoutExpired("probe", timeout)
                return b"", b""

        process = FakeProcess()
        with patch.object(DOCTOR.subprocess, "Popen", return_value=process) as popen:
            with patch.object(DOCTOR.os, "killpg") as killpg:
                result = DOCTOR.ProcessRunner().run(["probe"], timeout=0.1)
        self.assertEqual(result.rc, DOCTOR.TIMEOUT_EXIT)
        self.assertTrue(popen.call_args.kwargs["start_new_session"])
        killpg.assert_called_once_with(process.pid, DOCTOR.signal.SIGTERM)

    def test_failed_requirement_blocks_dependent_probe(self):
        data = minimal_data()
        data["components"]["dependent"] = {
            **data["components"]["probe"],
            "requires": ["probe"],
            "verify": {"command": "printf SHOULD_NOT_RUN", "expected_regex": "SHOULD_NOT_RUN"},
        }
        runner = FakeRunner(DOCTOR.Result(1))
        results = DOCTOR.run_checks(data, "controller", runner, MANIFEST_PATH, ROOT.parent)
        self.assertEqual([row["status"] for row in results], ["fail", "unknown"])
        self.assertEqual(len(runner.calls), 1)

    def test_unknown_requirement_blocks_dependent_probe(self):
        data = minimal_data()
        data["components"]["dependent"] = {
            **data["components"]["probe"],
            "requires": ["probe"],
            "verify": {"command": "printf SHOULD_NOT_RUN", "expected_regex": "SHOULD_NOT_RUN"},
        }
        runner = FakeRunner(DOCTOR.Result(DOCTOR.UNKNOWN_EXIT, "probe context unavailable\n"))
        results = DOCTOR.run_checks(data, "controller", runner, MANIFEST_PATH, ROOT.parent)
        self.assertEqual([row["status"] for row in results], ["unknown", "unknown"])
        self.assertIn(
            "probe (UNKNOWN: probe could not observe this host context)",
            results[1]["evidence"],
        )
        self.assertEqual(len(runner.calls), 1)

    def test_checkout_manifest_path_probes_resolve_in_real_tree(self):
        data = DOCTOR.load_requirements(MANIFEST_PATH)
        checkout_only_kinds = {
            "source-tool", "source-consumer", "source-and-tooling", "source-library",
            "host-provisioning-source", "workflow-environment",
        }
        repo_path_components = {
            component_id
            for component_id, row in data["components"].items()
            if any(
                "$ESTATE_REPO_ROOT" in probe["command"]
                for probe in row["verify"].values()
                if isinstance(probe, dict) and "command" in probe
            )
        }
        checkout_only_components = {
            component_id
            for component_id, row in data["components"].items()
            if row["kind"] in checkout_only_kinds
        }
        self.assertEqual(
            repo_path_components - checkout_only_components,
            {"iobench_host_calibration", "tester_unified_image"},
            "classify every repository-path probe as checkout-only or host-dependent",
        )
        self.assertTrue(checkout_only_components)

        runner = DOCTOR.ProcessRunner()
        for component_id in sorted(checkout_only_components):
            with self.subTest(component=component_id):
                probe = DOCTOR._probe_for(data["components"][component_id], "controller")
                result = runner.run(
                    DOCTOR._argv_for(probe["command"], "controller", ROOT.parent, None),
                    timeout=float(probe.get("timeout_seconds", 10)),
                )
                self.assertEqual(result.rc, 0, result.err)
                self.assertRegex(result.out, r"(?m)^PASS$")

    def test_cgprofile_session_reports_missing_control_socket_and_fix(self):
        data = DOCTOR.load_requirements(MANIFEST_PATH)
        row = data["components"]["cgprofile_session_delivery"]
        self.assertEqual(row["requires"], [])
        with patch.dict(os.environ, {"CGPROFILE_CONTROL_SOCKET": ""}):
            results = DOCTOR.run_checks(
                {"components": {"cgprofile_session_delivery": row}},
                "controller", DOCTOR.ProcessRunner(), MANIFEST_PATH, ROOT.parent,
            )
        self.assertEqual(results[0]["status"], "fail")
        self.assertEqual(results[0]["evidence"], "CGPROFILE_CONTROL_SOCKET unset")
        self.assertEqual(results[0]["fix"], row["fix"])

    def test_cgprofile_daemon_missing_control_socket_is_unknown_with_reason(self):
        data = DOCTOR.load_requirements(MANIFEST_PATH)
        row = {**data["components"]["cgprofile_daemon"], "requires": []}
        with patch.dict(os.environ, {"CGPROFILE_CONTROL_SOCKET": ""}):
            results = DOCTOR.run_checks(
                {"components": {"cgprofile_daemon": row}},
                "controller", DOCTOR.ProcessRunner(), MANIFEST_PATH, ROOT.parent,
            )
        self.assertEqual(results[0]["status"], "unknown")
        self.assertEqual(results[0]["evidence"], "CGPROFILE_CONTROL_SOCKET unset")

    def test_tester_unified_image_matches_installed_run_gate_identity(self):
        version = "25.5.0"
        wheel_sha256 = "a" * 64

        def check(labels_result):
            runner = FakeRunner(
                DOCTOR.Result(0, f"run-gate {version}\n"),
                DOCTOR.Result(0, json.dumps({"version": version, "wheel_sha256": wheel_sha256})),
                labels_result,
            )
            result = IMAGE_CHECK.check_identity(runner, ROOT.parent, env={})
            self.assertEqual(len(runner.calls), 3)
            self.assertEqual(
                runner.calls[2][0],
                ["docker", "image", "inspect", "--format", "{{json .Config.Labels}}", "tester-unified:local"],
            )
            return result

        matched = check(DOCTOR.Result(0, json.dumps({
            "org.vbpub.run-gate.version": version,
            "org.vbpub.run-gate.wheel-sha256": wheel_sha256,
        })))
        self.assertEqual(matched.rc, 0)
        self.assertEqual(matched.out, f"PASS run-gate {version} {wheel_sha256}\n")

        version_mismatch = check(DOCTOR.Result(0, json.dumps({
            "org.vbpub.run-gate.version": "25.4.0",
            "org.vbpub.run-gate.wheel-sha256": wheel_sha256,
        })))
        self.assertNotEqual(version_mismatch.rc, 0)
        self.assertIn("tester-unified image does not contain the published Run-Gate wheel", version_mismatch.out)

        sha_mismatch = check(DOCTOR.Result(0, json.dumps({
            "org.vbpub.run-gate.version": version,
            "org.vbpub.run-gate.wheel-sha256": "b" * 64,
        })))
        self.assertNotEqual(sha_mismatch.rc, 0)
        self.assertIn("tester-unified image does not contain the published Run-Gate wheel", sha_mismatch.out)

        missing = check(DOCTOR.Result(1, "", "no such image"))
        self.assertNotEqual(missing.rc, 0)
        self.assertEqual(missing.out, "tester-unified image is missing or cannot be inspected\n")

    def test_tester_unified_doctor_reports_published_wheel_mismatch(self):
        data = DOCTOR.load_requirements(MANIFEST_PATH)
        row = {**data["components"]["tester_unified_image"], "requires": []}
        message = "tester-unified image does not contain the published Run-Gate wheel: version mismatch"
        runner = FakeRunner(DOCTOR.Result(1, message + "\n"))
        results = DOCTOR.run_checks(
            {"components": {"tester_unified_image": row}},
            "controller", runner, MANIFEST_PATH, ROOT.parent,
        )
        self.assertEqual(results[0]["status"], "fail")
        self.assertEqual(results[0]["evidence"], message)
        self.assertEqual(results[0]["fix"], row["fix"])
        self.assertIn("check_tester_run_gate_image.py", runner.calls[0][0][-1])

    def test_tester_unified_remote_check_uses_runner_run_gate_bin(self):
        version = "25.5.0"
        wheel_sha256 = "c" * 64
        with tempfile.TemporaryDirectory() as temp_dir:
            runner_env = Path(temp_dir) / "runner.env"
            runner_env.write_text("RUN_GATE_BIN=/opt/runner/gate-tools/bin/run-gate\n", encoding="utf-8")
            runner = FakeRunner(
                DOCTOR.Result(0, f"run-gate {version}\n"),
                DOCTOR.Result(0, json.dumps({"version": version, "wheel_sha256": wheel_sha256})),
                DOCTOR.Result(0, json.dumps({
                    "org.vbpub.run-gate.version": version,
                    "org.vbpub.run-gate.wheel-sha256": wheel_sha256,
                })),
            )
            result = IMAGE_CHECK.check_identity(
                runner, ROOT.parent, cockpit="fixture-cockpit", runner_env_path=runner_env, env={},
            )
        self.assertEqual(result.rc, 0)
        self.assertEqual(
            runner.calls[0][0],
            ["docker", "exec", "-u", "vscode", "fixture-cockpit", "/opt/runner/gate-tools/bin/run-gate", "--version"],
        )

    def test_minimum_version_is_enforced(self):
        data = minimal_data(minimum_version="25.4.0")
        data["components"]["probe"]["verify"] = {
            "command": "run-gate --version",
            "expected_regex": "^run-gate",
            "version_pattern": r"run-gate\s+(\d+\.\d+\.\d+)",
        }
        _, old = self.run_probe(DOCTOR.Result(0, "run-gate 25.3.9\n"), data)
        self.assertEqual(old["status"], "fail")
        self.assertIn("below 25.4.0", old["evidence"])
        _, current = self.run_probe(DOCTOR.Result(0, "run-gate 25.4.0\n"), data)
        self.assertEqual(current["status"], "pass")

    def test_remote_probe_uses_injected_ssh_runner_and_guard(self):
        data = minimal_data()
        target = DOCTOR.RemoteTarget(
            ssh=["ssh", "configured-runner"],
            cockpit="configured-cockpit",
            runner_home="/home/configured-runner",
            guard=lambda script: "GUARDED\n" + script,
        )
        runner, row = self.run_probe(DOCTOR.Result(0, "PASS\n"), data, target)
        argv, _timeout = runner.calls[0]
        self.assertEqual(argv[:2], ["ssh", "configured-runner"])
        self.assertTrue(argv[-1].startswith("GUARDED\n"))
        self.assertIn("/home/configured-runner/src/vbpub-current", argv[-1])
        self.assertIn("configured-cockpit", argv[-1])
        self.assertEqual(row["status"], "pass")

    def test_cli_host_uses_remote_gate_identity_guard_and_injected_runner(self):
        remote_gate = DOCTOR._load_remote_gate()
        hosts = {
            "hosts": {
                "fixture-host": {
                    "ssh": ["ssh", "fixture-target"],
                    "api_check": ["provider-identity-check"],
                    "guard": "test -r /etc/os-release",
                    "runner_home": "/home/fixture-runner",
                    "cockpit": "fixture-cockpit",
                }
            }
        }
        requirements = {
            "schema_version": 1,
            "components": {
                "fixture": {
                    "kind": "probe",
                    "applies_to": "test-host",
                    "requires": [],
                    "provides": ["fixture"],
                    "minimum_version": "capability-based",
                    "required": True,
                    "verify": {
                        "command": "printf PASS",
                        "expected_regex": "^PASS$",
                        "timeout_seconds": 2,
                    },
                    "fix": "repair fixture",
                    "owner": "mdt-cockpit",
                    "doc": "CONSUMERS.md",
                }
            },
        }
        with tempfile.TemporaryDirectory() as temp_dir:
            temp = Path(temp_dir)
            hosts_path = temp / "hosts.json"
            hosts_path.write_text(json.dumps(hosts), encoding="utf-8")
            requirements_path = temp / "requirements.toml"
            import tomllib
            # The fixture is serialized as TOML to exercise the same CLI path.
            requirements_path.write_text(
                'schema_version = 1\n[components.fixture]\n'
                'kind = "probe"\napplies_to = "test-host"\nrequires = []\n'
                'provides = ["fixture"]\nminimum_version = "capability-based"\n'
                'required = true\nfix = "repair fixture"\nowner = "mdt-cockpit"\n'
                'doc = "CONSUMERS.md"\n[components.fixture.verify]\n'
                'command = "printf PASS"\nexpected_regex = "^PASS$"\ntimeout_seconds = 2\n',
                encoding="utf-8",
            )
            (temp / "CONSUMERS.md").write_text("# Fixture\n", encoding="utf-8")
            self.assertEqual(tomllib.loads(requirements_path.read_text()), requirements)
            runner = FakeRunner(
                DOCTOR.Result(0, "provider identity matches\n"),
                DOCTOR.Result(0, "PASS\n"),
            )
            output = io.StringIO()
            with patch.object(remote_gate, "ALLOWED_HOSTS", ("fixture-host",)):
                with patch.object(DOCTOR, "_load_remote_gate", return_value=remote_gate):
                    with redirect_stdout(output):
                        exit_code = DOCTOR.main(
                            ["--host", "fixture-host", "--hosts-file", str(hosts_path),
                             "--requirements", str(requirements_path), "--json"],
                            runner=runner,
                        )
        self.assertEqual(exit_code, 0)
        self.assertEqual(json.loads(output.getvalue())["checks"][0]["status"], "pass")
        self.assertEqual(runner.calls[0][0], ["provider-identity-check"])
        ssh_argv = runner.calls[1][0]
        self.assertEqual(ssh_argv[:2], ["ssh", "fixture-target"])
        self.assertIn("test -r /etc/os-release", ssh_argv[-1])
        self.assertIn("/home/fixture-runner/src/vbpub-current", ssh_argv[-1])

    def test_only_required_components_control_exit(self):
        rows = [
            {"status": "pass", "required": True},
            {"status": "fail", "required": False},
        ]
        self.assertEqual(DOCTOR.checks_exit_code(rows), 0)
        rows[0]["status"] = "unknown"
        self.assertEqual(DOCTOR.checks_exit_code(rows), 1)

    def test_manifest_fields_and_doc_links_resolve(self):
        data = DOCTOR.load_requirements(MANIFEST_PATH)
        self.assertEqual(DOCTOR.validate_requirements(data, MANIFEST_PATH), [])
        for component_id, row in data["components"].items():
            with self.subTest(component=component_id):
                self.assertTrue(row["verify"])
                self.assertTrue(row["fix"].strip())
                self.assertTrue((MANIFEST_PATH.parent / row["doc"].split("#", 1)[0]).is_file())

    def test_validator_rejects_bad_applicability_without_raising(self):
        data = minimal_data()
        data["components"]["probe"]["applies_to"] = ["controller"]
        errors = DOCTOR.validate_requirements(data, MANIFEST_PATH)
        self.assertIn("probe: invalid applies_to", errors)

    def test_validator_rejects_missing_component_fields_and_doc_anchors(self):
        data = minimal_data()
        del data["components"]["probe"]["provides"]
        data["components"]["probe"]["doc"] = "CONSUMERS.md#missing-anchor"
        errors = DOCTOR.validate_requirements(data, MANIFEST_PATH)
        self.assertIn("probe: missing provides", errors)
        self.assertIn("probe: doc anchor does not resolve", errors)

    def test_json_output_is_machine_readable(self):
        output = io.StringIO()
        rows = [{"component": "probe", "status": "pass", "evidence": "PASS", "fix": "repair it", "required": True}]
        with patch.object(DOCTOR, "run_checks", return_value=rows):
            with redirect_stdout(output):
                exit_code = DOCTOR.main(["--json"], runner=FakeRunner())
        self.assertEqual(exit_code, 0)
        payload = json.loads(output.getvalue())
        self.assertEqual(payload["checks"], rows)
        self.assertEqual(payload["exit_code"], 0)


if __name__ == "__main__":
    unittest.main()
