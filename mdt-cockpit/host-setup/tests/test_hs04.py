"""MDT-HS-04: systemd gets the whole-disk /dev/<DEVNAME> (from sysfs uevent), and every IO set-property
resets the list in the SAME call.

Measured on gstammtisch (systemd 257, root on a device-mapper LV over vda): `set-property --runtime
dev.slice "IOReadBandwidthMax=/dev/mapper/<lv> N"` left the kernel io.max EMPTY; `/dev/vda` and
`/dev/block/254:0` wrote it. Two spellings of one disk in a runtime list: the kernel took the last.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from test_io_governance import Host, LIB, MIB, ROOT, SCRIPTS  # noqa: E402

KEYS = ("IOReadBandwidthMax", "IOWriteBandwidthMax", "IOReadIOPSMax", "IOWriteIOPSMax")
SD = "/dev/vda"   # the fixture's /dev/null is a partition of whole disk 254:0, DEVNAME=vda


def _split(call: str) -> list[str]:
    """`set-property --runtime UNIT k=v k=dev N ...` -> ['k=', 'k=dev N', ...] (a value holds one space)."""
    out: list[str] = []
    for word in call.split()[3:]:
        if word.startswith(KEYS) and "=" in word:
            out.append(word)
        elif out:
            out[-1] += " " + word
    return out


class Base(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.host = Host(Path(self._tmp.name))

    def calls_for(self, unit: str) -> list[str]:
        return [c for c in self.host.calls() if c.startswith(f"set-property --runtime {unit} ")]


class SystemdSpellingTests(Base):
    def test_dev_slice_gets_the_whole_disk_block_node_and_reset_then_set_in_one_call(self) -> None:
        result = self.host.run()
        self.assertEqual(result.returncode, 0, result.stdout)
        calls = self.calls_for("dev.slice")
        self.assertEqual(len(calls), 1, calls)
        got = _split(calls[0])
        for key in KEYS:
            self.assertIn(f"{key}=", got)
            idx = got.index(f"{key}=")
            self.assertTrue(got[idx + 1].startswith(f"{key}={SD} "), got)
        self.assertNotIn("/dev/null", calls[0], "the configured node is never handed to systemd")

    def test_tier_slices_get_the_same_spelling_and_reset_then_set(self) -> None:
        self.host.config_lines += ["DEV_GATES_IO_RBPS_MAX=40M"]
        self.assertEqual(self.host.run().returncode, 0)
        calls = self.calls_for("dev-gates.slice")
        self.assertTrue(calls)
        got = _split(calls[0])
        self.assertIn("IOReadBandwidthMax=", got)
        self.assertEqual(got[got.index("IOReadBandwidthMax=") + 1], f"IOReadBandwidthMax={SD} {40 * MIB}")
        self.assertNotIn("/dev/null", calls[0])

    def _seed(self, paths: list) -> None:
        self.host.state.write_text(json.dumps({"unit": self.host.unit_static, "paths": {"dev.slice": paths},
                                               "touched": {"dev.slice": True}}))

    def test_a_stale_other_spelling_in_the_runtime_list_is_healed(self) -> None:
        """A pin made earlier under /dev/vda must not shadow the intended cap: reset-then-set clears it."""
        self._seed([["/dev/vda", {k: "1" for k in KEYS}]])
        result = self.host.run()
        self.assertEqual(result.returncode, 0, result.stdout)
        self.assertEqual(self.host.io_max(),
                         f"254:0 rbps={150 * MIB} wbps={150 * MIB} riops=500 wiops=500\n", result.stdout)
        shown = self.show("dev.slice")
        self.assertNotIn("/dev/vda 1\n", shown)

    def show(self, unit: str) -> str:
        env = os.environ.copy()
        env.update({"CG": str(self.host.cg), "FAKE_STATE": str(self.host.state), "FAKE_LOG": str(self.host.log),
                    "FAKE_UNIT_STATIC": json.dumps(self.host.unit_static)})
        return subprocess.run([str(self.host.bin / "systemctl"), "show", unit], env=env, text=True,
                              capture_output=True).stdout

    def test_upgrade_with_a_stale_all_max_entry_after_ours_is_healed_by_one_daemon_reload(self) -> None:
        """The live defect (inferred, 2026-10-20): the spelling we set already exists earlier in systemd's
        list and a stale all-max entry for another spelling comes AFTER it.  Reset keeps that entry at max,
        show hides it, and the apply writes it last -> io.max empty although `show` reports our values."""
        self._seed([["/dev/vda", {k: "max" for k in KEYS}], ["/dev/mapper/gstammtisch--vg-root", {k: "max" for k in KEYS}]])
        result = self.host.run()
        self.assertEqual(result.returncode, 0, result.stdout)
        self.assertEqual(self.host.io_max(),
                         f"254:0 rbps={150 * MIB} wbps={150 * MIB} riops=500 wiops=500\n", result.stdout)
        self.assertIn("WARN: dev.slice: systemctl show reports the intended IO limits", result.stdout)
        self.assertEqual(self.host.calls().count("daemon-reload"), 1, self.host.calls())
        self.assertNotIn("ERROR", result.stdout)

    STALE = [["/dev/vda", {k: "max" for k in KEYS}], ["/dev/mapper/gstammtisch--vg-root", {k: "max" for k in KEYS}]]
    NOW = 1_000_000

    def _run_at(self, offset: int, **extra: str):
        result = self.host.run(MDT_FAKE_NOW=str(self.NOW + offset), **extra)
        return result, self.host.calls().count("daemon-reload")

    def stamp(self) -> Path:
        return self.host.base / "state" / "io-selfheal-reload.stamp"

    def test_a_heal_that_does_not_help_reloads_exactly_once_inside_the_window(self) -> None:
        """H-39: the timer starts a fresh process every minute; the limiter must persist across runs."""
        self._seed(self.STALE)
        r1, n1 = self._run_at(0, FAKE_RELOAD_NOOP="1")
        self.assertNotEqual(r1.returncode, 0, r1.stdout)
        self.assertEqual(n1, 1)
        self.assertIn("ERROR: dev.slice: kernel io.max has NO entry", r1.stdout)
        self.assertIn("stale-entry heal (one daemon-reload, then every managed unit re-applied) was tried and did not fix it", r1.stdout)
        self.assertNotIn("Remedy: systemctl daemon-reload", r1.stdout)
        self.assertIn("io controller not enabled", r1.stdout)
        self.assertTrue(self.stamp().exists())
        for minute in range(1, 30):             # the timer fires every minute for 29 more minutes
            r, n = self._run_at(minute * 60, FAKE_RELOAD_NOOP="1")
            self.assertEqual(n, 0, f"minute {minute}: PID 1 must not be reloaded again")
            self.assertNotEqual(r.returncode, 0)
            self.assertIn("reload suppressed: last self-heal reload at", r.stdout)
            self.assertIn("did not fix it", r.stdout)
        r, n = self._run_at(1801, FAKE_RELOAD_NOOP="1")      # past the 30 min window: one more allowed
        self.assertEqual(n, 1, r.stdout)

    def test_a_clean_verify_clears_the_stamp(self) -> None:
        self._seed(self.STALE)
        r1, n1 = self._run_at(0)                  # the heal works
        self.assertEqual((r1.returncode, n1), (0, 1), r1.stdout)
        self.assertTrue(self.stamp().exists(), "the run that reloaded keeps the stamp")
        r2, n2 = self._run_at(60)                 # a clean run with no reload ends the window
        self.assertEqual((r2.returncode, n2), (0, 0), r2.stdout)
        self.assertFalse(self.stamp().exists())

    # ---- MDT-HS-06: one reload for the WHOLE batch, never unit by unit -----------------------------------
    TIERS = ("dev.slice", "dev-gates.slice", "dev-buildkitd.slice")

    def _seed_all(self) -> None:
        stale = [["/dev/vda", {k: "max" for k in KEYS}], ["/dev/mapper/gstammtisch--vg-root", {k: "max" for k in KEYS}]]
        self.host.state.write_text(json.dumps({
            "unit": self.host.unit_static,
            "paths": {unit: json.loads(json.dumps(stale)) for unit in self.TIERS},
            "touched": {unit: True for unit in self.TIERS},
        }))
        self.host.config_lines += ["DEV_GATES_IO_RBPS_MAX=40M"]

    def test_two_units_with_stale_spellings_are_healed_by_one_global_reload(self) -> None:
        """The live incident: the reload is ONE global event that clears the kernel state of every unit applied
        before it.  Healing only the trigger unit left the next unit failing as 'reload suppressed'."""
        self._seed_all()
        result, reloads = self._run_at(0, FAKE_RELOAD_CLEARS_KERNEL="1")
        self.assertEqual(result.returncode, 0, result.stdout)
        self.assertEqual(reloads, 1, self.host.calls())
        self.assertNotIn("reload suppressed", result.stdout)
        self.assertNotIn("ERROR", result.stdout)
        self.assertIn(f"rbps={150 * MIB}", self.host.io_max("dev.slice"))
        gates = self.host.io_max("dev.slice/dev-gates.slice")
        self.assertIn(f"rbps={40 * MIB}", gates)
        self.assertTrue(self.host.io_max("dev.slice/dev-buildkitd.slice"), "the third unit is re-applied too")
        self.assertIn("EVERY managed unit", result.stdout)

    def test_the_reload_re_applies_every_unit_after_it(self) -> None:
        self._seed_all()
        result, _ = self._run_at(0, FAKE_RELOAD_CLEARS_KERNEL="1")
        calls = self.host.calls()
        at = calls.index("daemon-reload")
        after = [c.split()[2] for c in calls[at + 1:] if c.startswith("set-property --runtime")]
        self.assertEqual(sorted(set(after)), sorted(self.TIERS), "every managed unit is set again after the reload")

    def test_a_second_run_inside_the_window_is_suppressed_but_a_run_never_fails_from_its_own_reload(self) -> None:
        self._seed_all()
        r1, n1 = self._run_at(0, FAKE_RELOAD_CLEARS_KERNEL="1")
        self.assertEqual((r1.returncode, n1), (0, 1), r1.stdout)
        # the same stale shape again 60 s later (the timer's next run): suppressed, no second reload
        self._seed_all()
        r2, n2 = self._run_at(60, FAKE_RELOAD_CLEARS_KERNEL="1")
        self.assertEqual(n2, 0, "the persisted limiter blocks a reload inside the window")
        self.assertNotEqual(r2.returncode, 0, r2.stdout)
        self.assertIn("reload suppressed: last self-heal reload at", r2.stdout)

    def test_a_heal_that_does_not_help_reloads_once_and_every_failing_unit_gets_the_tried_remedy(self) -> None:
        self._seed_all()
        result, reloads = self._run_at(0, FAKE_RELOAD_NOOP="1")
        self.assertNotEqual(result.returncode, 0, result.stdout)
        self.assertEqual(reloads, 1)
        for unit in ("dev.slice", "dev-gates.slice"):
            self.assertIn(f"ERROR: {unit}: kernel io.max has NO entry", result.stdout)
        self.assertGreaterEqual(result.stdout.count("was tried and did not fix it"), 2)

    def test_no_reload_when_systemd_itself_does_not_report_the_values(self) -> None:
        """H-40: the kernel is empty and `show` hides the values: not the stale-entry case, so NO reload."""
        result, reloads = self._run_at(0, FAKE_KERNEL_DROPS="1", FAKE_SHOW_HIDES="1")
        self.assertNotEqual(result.returncode, 0, result.stdout)
        self.assertEqual(reloads, 0, "a reload occurred although systemd did not report the values")
        self.assertIn("systemd does not report all the intended values either", result.stdout)
        self.assertIn("no reload was attempted", result.stdout)
        self.assertNotIn("reload suppressed", result.stdout)
        self.assertFalse(self.stamp().exists())

    def test_a_partial_show_report_is_not_enough_to_reload(self) -> None:
        result, reloads = self._run_at(0, FAKE_KERNEL_DROPS="1", FAKE_SHOW_PARTIAL="1")
        self.assertNotEqual(result.returncode, 0, result.stdout)
        self.assertEqual(reloads, 0, result.stdout)
        self.assertIn("systemd does not report all the intended values either", result.stdout)

    def test_the_old_spelling_would_have_been_dropped_by_systemd(self) -> None:
        """Oracle sensitivity for the measured failure: a spelling systemd drops writes nothing; the
        block node is not dropped."""
        result = self.host.run(FAKE_DROP_SPELLING="/dev/null")
        self.assertEqual(result.returncode, 0, result.stdout)
        self.assertIn("254:0", self.host.io_max())

    def test_log_lines_name_the_path_given_to_systemd(self) -> None:
        result = self.host.run()
        self.assertIn(f"io.max on {SD} (from /dev/null) is authoritative", result.stdout)


class ContainerCapsTests(Base):
    def _apply(self) -> subprocess.CompletedProcess:
        scope = self.host.cg / "dev.slice" / "dev-gates.slice" / "docker-abc.scope"
        scope.mkdir(parents=True, exist_ok=True)
        script = (
            f'log(){{ echo "$*"; }}; . "{LIB}"; _mdt_scope_path(){{ echo "{scope}"; }}\n'
            'IO_DEV_PATH=/dev/null WATCHER_RBPS=1048576 WATCHER_WBPS=2097152 WATCHER_RIOPS=100 '
            'WATCHER_WIOPS=200 WATCHER_SRC=test\n'
            '_mdt_apply_container_caps abc lbl 0\n')
        env = os.environ.copy()
        env.update({"CG": str(self.host.cg), "MDT_SYSFS_ROOT": str(self.host.sys),
                    "PATH": f"{self.host.bin}:/usr/bin:/bin", "FAKE_STATE": str(self.host.state),
                    "FAKE_LOG": str(self.host.log), "FAKE_UNIT_STATIC": "{}"})
        self.host.log.write_text("")
        return subprocess.run(["bash", "-c", script], env=env, text=True, capture_output=True)

    def test_container_scope_caps_use_the_block_node_reset_then_set_and_log_it(self) -> None:
        result = self._apply()
        calls = self.calls_for("docker-abc.scope")
        self.assertEqual(len(calls), 1, (calls, result.stdout, result.stderr))
        got = _split(calls[0])
        for key in KEYS:
            idx = got.index(f"{key}=")
            self.assertTrue(got[idx + 1].startswith(f"{key}={SD} "), got)
        self.assertIn(f"io.max on {SD}", result.stdout)

    def test_an_unresolvable_device_skips_loudly(self) -> None:
        result = subprocess.run(
            ["bash", "-c", f'log(){{ echo "$*"; }}; . "{LIB}"; _mdt_io_systemd_dev /nonexistent; echo rc=$?'],
            env=dict(os.environ, MDT_SYSFS_ROOT=str(self.host.sys)), text=True, capture_output=True)
        self.assertIn("rc=1", result.stdout)


class DevnameTests(Base):
    def _helper(self, expr: str, sysroot: Path | None = None) -> subprocess.CompletedProcess:
        return subprocess.run(
            ["bash", "-c", f'log(){{ :; }}; . "{LIB}"; {expr}; echo rc=$?'],
            env=dict(os.environ, MDT_SYSFS_ROOT=str(sysroot or self.host.sys)), text=True, capture_output=True)

    def test_the_node_is_read_from_the_sysfs_uevent_of_the_derived_majmin(self) -> None:
        r = self._helper("_mdt_io_systemd_dev /dev/null")
        self.assertIn("/dev/vda", r.stdout)
        self.assertIn("rc=0", r.stdout)

    def test_a_major_renumbering_does_not_change_the_spelling(self) -> None:
        """virtio 254 vs dm 253 can swap across kernels: the DEVNAME follows the disk, not the number."""
        (self.host.sys / "devices" / "vda" / "dev").write_text("253:0\n")
        (self.host.sys / "dev" / "block" / "254:0").unlink()
        (self.host.sys / "dev" / "block" / "253:0").symlink_to("../../devices/vda")
        r = self._helper("_mdt_io_systemd_dev /dev/null")
        self.assertIn("/dev/vda", r.stdout)

    def test_missing_devname_is_refused_not_invented(self) -> None:
        (self.host.sys / "devices" / "vda" / "uevent").write_text("MAJOR=254\nMINOR=0\n")
        r = self._helper("_mdt_io_systemd_dev /dev/null")
        self.assertIn("rc=1", r.stdout)
        self.assertIn("no DEVNAME", r.stderr)
        self.assertNotIn("/dev/", r.stdout.replace("rc=1", ""))

    def test_reconcile_fails_loudly_when_devname_is_missing(self) -> None:
        (self.host.sys / "devices" / "vda" / "uevent").write_text("MAJOR=254\nMINOR=0\n")
        result = self.host.run()
        self.assertNotEqual(result.returncode, 0, result.stdout)
        self.assertIn("cannot name the whole disk 254:0", result.stdout)
        self.assertEqual(self.calls_for("dev.slice"), [])


class InstallerTests(unittest.TestCase):
    def test_static_unit_lines_use_the_derived_block_node(self) -> None:
        unit = (ROOT / "units" / "dev.slice.in").read_text()
        self.assertIn("IOReadBandwidthMax=@IO_SD_DEV@ ", unit)
        self.assertNotIn("@IO_DEV_PATH@", unit)
        self.assertNotIn("/dev/block/", unit)
        install = (ROOT / "install.sh").read_text()
        self.assertIn("_mdt_io_systemd_dev", install)
        self.assertIn("IO_DEV_PATH IO_SD_DEV", install)

    def test_install_messages_distinguish_unresolvable_from_no_device(self) -> None:
        install = (ROOT / "install.sh").read_text()
        self.assertIn("was found but its whole disk could not be resolved", install)
        self.assertIn("no host block device was discovered", install)
        self.assertIn("IO_SD_STATE", install)

    def test_design_guide_records_measured_versus_inferred(self) -> None:
        design = " ".join((ROOT.parent / "docs" / "DESIGN-GUIDE.md").read_text().split())
        for needle in ("MDT-HS-04", "DEVNAME", "measured", "inferred", "virtio", "PER PATH STRING", "daemon-reload"):
            self.assertIn(needle, design)
        self.assertIn("MDT-HS-04", (ROOT.parent / "CHANGES.md").read_text())


if __name__ == "__main__":
    unittest.main()
