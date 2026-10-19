"""Fix round 1 for MDT-HS-01 (review H-01..H-10): behaviours the first round left open."""
from __future__ import annotations

import os
import re
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from test_io_governance import (  # noqa: E402
    Host,
    LIB,
    RECONCILE,
    ROOT,
    SCRIPTS,
    _load,
    _write_exec,
)

PROJECT = ROOT.parent
MIB = 1024 * 1024


class DocsAndUnitsTests(unittest.TestCase):
    def _section(self, text: str, heading: str) -> str:
        start = text.index(heading)
        end = text.find("\n## ", start + 5)
        return text[start:end if end != -1 else None]

    def test_readme_persistence_model_states_the_new_truth_not_the_old_claim(self) -> None:
        """H-01: a keyword-presence test cannot catch a contradiction, so the false sentences are named."""
        section = self._section((ROOT / "README.md").read_text(), "## Persistence model")
        flat = " ".join(section.split())
        self.assertNotIn("authoritative again", flat)
        self.assertNotIn("clears only MDT's runtime IO properties on `dev.slice`", flat)
        self.assertNotIn("static unit-file values authoritative", flat)
        self.assertIn("re-asserts", flat)
        self.assertIn("set-property --runtime", flat)
        self.assertIn("reads the kernel `io.max` back", flat)

    def test_no_document_still_claims_an_empty_assignment_falls_back_to_the_unit_file(self) -> None:
        for path in (ROOT / "README.md", ROOT / "CGROUP-NOTES.md", PROJECT / "docs" / "CONSUMERS.md",
                     PROJECT / "docs" / "DESIGN-GUIDE.md", ROOT / "host-setup.env.example"):
            flat = " ".join(path.read_text().split())
            self.assertNotIn("authoritative again", flat, path.name)

    def test_the_swap_attribution_is_stated_with_its_kernel_source_and_as_not_measured(self) -> None:
        """H-08"""
        for path in (ROOT / "README.md", ROOT / "CGROUP-NOTES.md", PROJECT / "docs" / "CONSUMERS.md",
                     ROOT / "host-setup.env.example", PROJECT / "docs" / "DESIGN-GUIDE.md"):
            flat = " ".join(path.read_text().split())
            self.assertIn("bio_associate_blkg_from_page", flat, path.name)
            self.assertIn("not measured here", flat, path.name)

    def test_changes_and_consumers_describe_the_real_upgrade_path(self) -> None:
        """H-07"""
        changes = (PROJECT / "CHANGES.md").read_text()
        self.assertNotIn("the four new (empty) keys", changes)
        self.assertIn("20 new", changes)
        consumers = (PROJECT / "docs" / "CONSUMERS.md").read_text()
        self.assertNotIn("edit the four (empty) lines", consumers)
        self.assertIn("runtime-optional", consumers)
        self.assertIn("NOTICE", consumers)

    def test_the_audit_does_not_depend_on_the_reconcile_succeeding(self) -> None:
        """H-06: a oneshot unit skips its second ExecStart= when the first fails."""
        unit = (ROOT / "units" / "mdt-dev-governance-reconcile.service").read_text()
        lines = [ln for ln in unit.splitlines() if ln and not ln.startswith("#")]
        self.assertEqual([ln for ln in lines if ln.startswith("ExecStart=")],
                         ["ExecStart=/usr/local/sbin/mdt-dev-governance-reconcile.sh"])
        self.assertIn("ExecStopPost=/usr/local/sbin/mdt-slice-memory-min-low-audit.py", lines)

    def test_buildkitd_start_timeout_covers_a_cold_pull_and_the_render_test_pins_it(self) -> None:
        unit = (ROOT / "units" / "mdt-buildkitd.service.in").read_text()
        self.assertRegex(unit, r"(?m)^TimeoutStartSec=10min$")
        self.assertIn("TimeoutStartSec", (ROOT / "tests" / "test-render.sh").read_text())

    def test_vm_lane_leaves_evidence_and_does_not_throttle_the_harness(self) -> None:
        lane = (ROOT / "tests" / "run-vm-acceptance.sh").read_text()
        self.assertNotIn("docker update", lane)
        self.assertIn("vm-dump-diagnostics.sh", lane)
        self.assertIn("trap", lane)
        dump = (ROOT / "tests" / "vm-dump-diagnostics.sh").read_text()
        self.assertIn("journalctl -u mdt-buildkitd.service", dump)
        self.assertIn("--failed", dump)
        pull = (ROOT / "tests" / "vm-install-with-diagnostics.sh").read_text()
        self.assertRegex(pull, r"timeout \d+ docker pull")

    def test_vm_recovery_step_sets_a_different_value_before_the_target(self) -> None:
        vm = (ROOT / "tests" / "vm-governance-acceptance.sh").read_text()
        self.assertIn("DEV_IO_RIOPS_MAX 700", vm)


class LibRound1Tests(unittest.TestCase):
    def _bash(self, script: str, **env: str) -> subprocess.CompletedProcess:
        return subprocess.run(["bash", "-c", f'log(){{ echo "$*"; }}; . "{LIB}"; {script}'],
                              env={**os.environ, **env}, text=True, stdout=subprocess.PIPE, stderr=subprocess.STDOUT)

    def test_pin_values_are_bounded_before_arithmetic(self) -> None:
        """H-10: huge values wrapped silently (18446744073709551621 -> 5 B/s, an effective halt)."""
        for bad in ("18446744073709551621", "9999999999T", "99999999999999999999", "5000000000000000000"):
            r = self._bash(f'_mdt_size_to_bytes {bad}; echo rc=$?')
            self.assertIn("rc=1", r.stdout, bad)
        for good, want in (("4T", 4 * 1024 ** 4), ("80M", 80 * MIB), ("800", 800)):
            r = self._bash(f'_mdt_size_to_bytes {good}')
            self.assertEqual(r.stdout.strip(), str(want))

    def test_the_wizard_rejects_what_the_runtime_rejects(self) -> None:
        wizard = _load(ROOT / "mdt-host-setup-wizard.py", "mdt_wizard_round1")
        for bad in ("18446744073709551621", "9999999999T"):
            self.assertIsNotNone(wizard.validate_io_pin(bad), bad)
        self.assertIsNone(wizard.validate_io_pin("4T"))

    def test_runtime_optional_keys_may_be_absent_render_time_keys_may_not(self) -> None:
        """H-07"""
        with tempfile.TemporaryDirectory() as tmp:
            example = Path(tmp) / "example"
            conf = Path(tmp) / "conf"
            example.write_text("DEV_CPU_QUOTA=auto\nDEV_IO_RBPS_MAX=\nDEV_GATES_IO_WBPS_MAX=\n"
                               "DEV_INTERACTIVE_IO_RIOPS_MAX=\nIO_BASELINE_ROTATIONAL_FLAG=\n")
            conf.write_text("# nothing declared\n")
            required = self._bash(f'_mdt_missing_declared_keys "{conf}" "{example}" required').stdout.split()
            optional = self._bash(f'_mdt_missing_declared_keys "{conf}" "{example}" optional').stdout.split()
            self.assertEqual(required, ["DEV_CPU_QUOTA"])
            self.assertEqual(sorted(optional), ["DEV_GATES_IO_WBPS_MAX", "DEV_INTERACTIVE_IO_RIOPS_MAX",
                                                "DEV_IO_RBPS_MAX", "IO_BASELINE_ROTATIONAL_FLAG"])

    def test_every_shipped_runtime_key_is_classified_optional(self) -> None:
        example = ROOT / "host-setup.env.example"
        r = self._bash(f'_mdt_missing_declared_keys /dev/null "{example}" optional')
        keys = set(r.stdout.split())
        self.assertEqual(len([k for k in keys if "_IO_" in k and k.endswith("_MAX")]), 20)
        req = set(self._bash(f'_mdt_missing_declared_keys /dev/null "{example}" required').stdout.split())
        self.assertFalse([k for k in req if k.endswith("_IO_RBPS_MAX") or k.endswith("_IO_WIOPS_MAX")])

    def test_install_prints_one_loud_notice_naming_the_effective_cap_source(self) -> None:
        r = self._bash('DEV_STATIC_RBW=150M; _mdt_resolve_static_io; _mdt_io_pin_notice')
        self.assertIn("NOTICE", r.stdout)
        self.assertIn("static fallback", r.stdout)
        self.assertIn(str(150 * MIB), r.stdout)
        r = self._bash('DEV_IO_RBPS_MAX=80M; _mdt_resolve_static_io; _mdt_io_pin_notice')
        self.assertNotIn("NOTICE", r.stdout)
        install = (ROOT / "install.sh").read_text()
        self.assertIn("_mdt_missing_declared_keys", install)
        self.assertIn("_mdt_io_pin_notice", install)


class ReconcileRound1Tests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.host = Host(Path(self._tmp.name))

    def _empty_assignments(self, unit: str) -> list[str]:
        return [c for c in self.host.calls() if c.startswith(f"set-property --runtime {unit} ")
                and re.search(r"\bIO\w+Max=(\s|$)", c)]

    # H-03 -------------------------------------------------------------------
    def test_an_invalid_tier_pin_keeps_the_last_good_cap_when_it_is_one_of_several_pins(self) -> None:
        self.host.config_lines += ["DEV_GATES_IO_WBPS_MAX=20M", "DEV_GATES_IO_RBPS_MAX=40M"]
        self.assertEqual(self.host.run().returncode, 0)
        self.host.config_lines[-2] = "DEV_GATES_IO_WBPS_MAX=20MB"
        result = self.host.run()
        self.assertNotEqual(result.returncode, 0, result.stdout)
        self.assertIn("ERROR: DEV_GATES_IO_WBPS_MAX='20MB'", result.stdout)
        self.assertEqual([c for c in self._empty_assignments("dev-gates.slice") if "IOWriteBandwidthMax=" in c
                          and "IOWriteBandwidthMax=/" not in c], [], self.host.calls())
        self.assertIn(f"wbps={20 * MIB}", self.host.io_max("dev.slice/dev-gates.slice"))
        self.assertIn(f"rbps={40 * MIB}", self.host.io_max("dev.slice/dev-gates.slice"))

    def test_an_invalid_tier_pin_keeps_the_last_good_cap_when_it_is_the_only_pin(self) -> None:
        self.host.config_lines += ["DEV_GATES_IO_WBPS_MAX=20M"]
        self.assertEqual(self.host.run().returncode, 0)
        self.host.config_lines[-1] = "DEV_GATES_IO_WBPS_MAX=20MB"
        result = self.host.run()
        self.assertNotEqual(result.returncode, 0, result.stdout)
        self.assertIn(f"wbps={20 * MIB}", self.host.io_max("dev.slice/dev-gates.slice"))

    def test_a_removed_tier_pin_is_cleared_and_the_clear_is_verified(self) -> None:
        self.host.config_lines += ["DEV_GATES_IO_RBPS_MAX=40M"]
        self.assertEqual(self.host.run().returncode, 0)
        self.host.config_lines.remove("DEV_GATES_IO_RBPS_MAX=40M")
        result = self.host.run()
        self.assertEqual(result.returncode, 0, result.stdout)
        self.assertIn("dev-gates.slice: verified kernel io.max 254:0 rbps=max", result.stdout)

    # H-04 -------------------------------------------------------------------
    def test_no_device_while_the_slice_carries_a_cap_refuses_to_wipe_it(self) -> None:
        self.host.config_lines[0] = "IO_DEV_PATH="
        self.host.extra_env["FAKE_FINDMNT_SOURCE"] = "overlay"
        # the unit file carries a static device cap (host.unit_static), so a transient
        # findmnt failure must not uncap the host
        result = self.host.run()
        self.assertNotEqual(result.returncode, 0, result.stdout)
        self.assertIn("ERROR: no block device was discovered but dev.slice carries IO limits", result.stdout)
        self.assertEqual([c for c in self.host.calls() if c.startswith("set-property")], [])

    # H-05 -------------------------------------------------------------------
    def test_a_multi_backing_device_host_is_refused_once_with_one_clear_message(self) -> None:
        dm = self.host.sys / "devices" / "dm-0"
        (dm / "slaves").mkdir(parents=True)
        (dm / "slaves" / "vda").symlink_to("../../vda")
        (dm / "slaves" / "vdb").symlink_to("../../vdb")
        link = self.host.sys / "dev" / "block" / "1:3"
        link.unlink()
        link.symlink_to("../../devices/dm-0")
        result = self.host.run()
        self.assertNotEqual(result.returncode, 0, result.stdout)
        errors = [ln for ln in result.stdout.splitlines() if "ERROR" in ln and "backing devices" in ln]
        self.assertEqual(len(errors), 1, result.stdout)
        self.assertIn("vda", errors[0])
        self.assertIn("vdb", errors[0])
        self.assertEqual([c for c in self.host.calls() if c.startswith("set-property")], [])

    # H-10 -------------------------------------------------------------------
    def test_a_btrfs_swap_file_source_is_classified_not_reported_unresolved(self) -> None:
        self.host.set_swaps([("/swapfile", "file")])
        self.host.extra_env["FAKE_FINDMNT_SOURCE"] = "/dev/null[/@swap]"
        result = self.host.run()
        self.assertNotIn("unresolved", result.stdout)
        self.assertNotIn("UNCAPPED SWAP", result.stdout)

    def test_the_uncapped_swap_warning_fires_once_per_state_change(self) -> None:
        self.host.set_swaps([("/dev/zero", "partition")])
        first = self.host.run()
        self.assertIn("UNCAPPED SWAP", first.stdout)
        self.assertNotIn("UNCAPPED SWAP", self.host.run().stdout)
        self.host.set_swaps([("/dev/zero", "partition"), ("/dev/null", "partition")])
        self.host.set_swaps([("/dev/zero", "partition")])  # same state again: still quiet
        self.assertNotIn("UNCAPPED SWAP", self.host.run().stdout)

    def test_no_uncapped_swap_warning_while_dev_slice_is_inactive(self) -> None:
        import shutil
        shutil.rmtree(self.host.cg / "dev.slice")
        self.host.set_swaps([("/dev/zero", "partition")])
        self.assertNotIn("UNCAPPED SWAP", self.host.run().stdout)

    # VM-related: the audit runs even when the reconcile fails ----------------
    def test_the_service_unit_text_is_rendered_with_the_audit_as_stop_post(self) -> None:
        unit = (ROOT / "units" / "mdt-dev-governance-reconcile.service").read_text()
        self.assertIn("ExecStopPost=", unit)


if __name__ == "__main__":
    unittest.main()
