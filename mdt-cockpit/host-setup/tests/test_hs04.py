"""MDT-HS-04: systemd gets the whole-disk node as /dev/block/MAJ:MIN, and every IO set-property
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
SD = "/dev/block/254:0"   # the fixture's /dev/null is a partition of whole disk 254:0


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

    def test_a_stale_other_spelling_in_the_runtime_list_is_healed(self) -> None:
        """A pin made earlier under /dev/vda (or an older reconcile's mapper path) must not shadow
        the intended cap: the kernel takes the LAST entry of the list."""
        self.host.state.write_text(json.dumps({"unit": self.host.unit_static, "runtime": {"dev.slice": {
            k: [["/dev/vda", "1"]] for k in KEYS}}}))
        result = self.host.run()
        self.assertEqual(result.returncode, 0, result.stdout)
        self.assertEqual(self.host.io_max(),
                         f"254:0 rbps={150 * MIB} wbps={150 * MIB} riops=500 wiops=500\n", result.stdout)
        runtime = json.loads(self.host.state.read_text())["runtime"]["dev.slice"]
        for key in KEYS:
            self.assertEqual([e[0] for e in runtime[key]], [SD], "the stale spelling must be gone, not outvoted")

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


class InstallerTests(unittest.TestCase):
    def test_static_unit_lines_use_the_derived_block_node(self) -> None:
        unit = (ROOT / "units" / "dev.slice.in").read_text()
        self.assertIn("IOReadBandwidthMax=@IO_SD_DEV@ ", unit)
        self.assertNotIn("@IO_DEV_PATH@", unit)
        install = (ROOT / "install.sh").read_text()
        self.assertIn("_mdt_io_systemd_dev", install)
        self.assertIn("IO_DEV_PATH IO_SD_DEV", install)

    def test_design_guide_records_measured_versus_inferred(self) -> None:
        design = " ".join((ROOT.parent / "docs" / "DESIGN-GUIDE.md").read_text().split())
        for needle in ("MDT-HS-04", "/dev/block/", "measured", "inferred"):
            self.assertIn(needle, design)
        self.assertIn("MDT-HS-04", (ROOT.parent / "CHANGES.md").read_text())


if __name__ == "__main__":
    unittest.main()
