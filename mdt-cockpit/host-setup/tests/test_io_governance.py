"""dev.slice IO governance that is really in effect (MDT-016, MDT-HS-01).

Fake-based: a fake `systemctl` that models the systemd semantics that bit the
live host (an empty `IO*Max=` assignment resets the WHOLE list, unit-file values
included) and writes a fake kernel `io.max`; a fake sysfs for the device
resolution; a fake /proc/swaps.  Nothing here touches the real host.
"""
from __future__ import annotations

import importlib.util
import json
import os
import re
import shutil
import stat
import subprocess
import sys
import tempfile
import threading
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"
RECONCILE = SCRIPTS / "mdt-dev-governance-reconcile.sh"
LIB = SCRIPTS / "mdt-container-io-caps.lib.sh"
LISTER = SCRIPTS / "mdt-cgroup-io-limits.sh"
MIB = 1024 * 1024
sys.path.insert(0, str(ROOT))  # the wizard's source-tree import of mdt_cli


def _load(path: Path, name: str):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module  # dataclasses need the module registered
    spec.loader.exec_module(module)
    return module

FAKE_SYSTEMCTL = r'''#!__PY__
"""Fake systemctl: systemd's property layering + a fake kernel io.max."""
import json, os, sys
state_path = os.environ["FAKE_STATE"]
cg = os.environ["CG"]
with open(os.environ["FAKE_LOG"], "a") as log:
    log.write(" ".join(sys.argv[1:]) + "\n")
state = json.load(open(state_path)) if os.path.exists(state_path) else {
    "unit": json.loads(os.environ.get("FAKE_UNIT_STATIC", "{}")), "runtime": {}}
KEYS = [("IOReadBandwidthMax", "rbps"), ("IOWriteBandwidthMax", "wbps"),
        ("IOReadIOPSMax", "riops"), ("IOWriteIOPSMax", "wiops")]

def cgroup_dir(unit):
    return os.path.join(cg, "dev.slice") if unit == "dev.slice" else os.path.join(cg, "dev.slice", unit)

def recompute(unit):
    d = cgroup_dir(unit)
    if not os.path.isdir(d):
        return
    values = {}
    for key, field in KEYS:
        rt = state["runtime"].get(unit, {})
        # runtime layer present (even EMPTY) overrides the unit file: this is the
        # systemd behaviour that wiped dev.slice on the live host.
        entries = rt[key] if key in rt else state["unit"].get(unit, {}).get(key, [])
        values[field] = entries[1] if entries else "max"
    if os.environ.get("FAKE_KERNEL_DROPS"):
        text = ""
    elif all(v == "max" for v in values.values()):
        text = ""
    else:
        mm = os.environ.get("FAKE_KERNEL_MAJMIN", "254:0")
        text = "%s rbps=%s wbps=%s riops=%s wiops=%s\n" % (mm, values["rbps"], values["wbps"], values["riops"], values["wiops"])
    with open(os.path.join(d, "io.max"), "w") as f:
        f.write(text)

args = sys.argv[1:]
if args[:2] == ["set-property", "--runtime"]:
    unit = args[2]
    for assignment in args[3:]:
        key, _, val = assignment.partition("=")
        if key not in dict(KEYS):
            continue
        state["runtime"].setdefault(unit, {})[key] = val.split() if val else []
    recompute(unit)
    json.dump(state, open(state_path, "w"))
elif args and args[0] == "show":
    unit = args[1]
    rt = state["runtime"].get(unit, {})
    for key, _ in KEYS:
        print("%s=%s" % (key, " ".join(rt.get(key, state["unit"].get(unit, {}).get(key, [])))))
    print("DropInPaths=" + ("/run/systemd/system.control/%s.d/50-IO.conf" % unit if rt else ""))
sys.exit(0)
'''

FAKE_STAT = '''#!/usr/bin/env bash
# The baseline identity check compares st_dev of paths that do not exist in the
# test; everything else (notably the device-number query) is the real stat.
if [ "${1:-}" = -c ] && [ "${2:-}" = %d ]; then echo 111; exit 0; fi
exec __REAL_STAT__ "$@"
'''


def _write_exec(path: Path, text: str) -> None:
    path.write_text(text)
    path.chmod(0o755)


class Host:
    """A throwaway fake host: cgroup tree, sysfs, /proc/swaps, config, PATH."""

    def __init__(self, base: Path, *, active_slices: bool = True) -> None:
        self.base = base
        self.cg = base / "cgroup"
        self.sys = base / "sys"
        self.proc = base / "proc"
        self.bin = base / "bin"
        self.state = base / "state.json"
        self.log = base / "systemctl.log"
        self.conf = base / "host-setup.env"
        for d in (self.cg, self.proc, self.bin):
            d.mkdir(parents=True, exist_ok=True)
        if active_slices:
            for rel in ("dev.slice", "dev.slice/dev-gates.slice", "dev.slice/dev-buildkitd.slice"):
                (self.cg / rel).mkdir(parents=True, exist_ok=True)
                (self.cg / rel / "io.max").write_text("")
        # /dev/null is 1:3.  Model it as a partition of the whole disk 254:0
        # (what /dev/mapper/<root> -> vda looks like to systemd).
        dev = self.sys / "devices"
        (dev / "vda" / "vda1").mkdir(parents=True)
        (dev / "vda" / "dev").write_text("254:0\n")
        (dev / "vda" / "vda1" / "dev").write_text("1:3\n")
        (dev / "vda" / "vda1" / "partition").write_text("1\n")
        (dev / "vdb").mkdir()
        (dev / "vdb" / "dev").write_text("8:16\n")
        blk = self.sys / "dev" / "block"
        blk.mkdir(parents=True)
        (blk / "1:3").symlink_to("../../devices/vda/vda1")
        (blk / "254:0").symlink_to("../../devices/vda")
        (blk / "1:5").symlink_to("../../devices/vdb")  # /dev/zero: a second disk
        (blk / "8:16").symlink_to("../../devices/vdb")
        self.set_swaps([])
        _write_exec(self.bin / "systemctl", FAKE_SYSTEMCTL.replace("__PY__", sys.executable))
        _write_exec(self.bin / "stat", FAKE_STAT.replace("__REAL_STAT__", shutil.which("stat") or "/usr/bin/stat"))
        _write_exec(self.bin / "findmnt", "#!/usr/bin/env bash\necho /dev/fake\n")
        _write_exec(self.bin / "docker", "#!/usr/bin/env bash\nexit 1\n")
        self.unit_static = {"dev.slice": {
            "IOReadBandwidthMax": ["/dev/null", str(150 * MIB)],
            "IOWriteBandwidthMax": ["/dev/null", str(150 * MIB)],
            "IOReadIOPSMax": ["/dev/null", "500"],
            "IOWriteIOPSMax": ["/dev/null", "500"],
        }}
        self.baseline_py = base / "baseline-check.sh"
        _write_exec(self.baseline_py, "#!/usr/bin/env bash\nexit 0\n")
        self.config_lines = ["IO_DEV_PATH=/dev/null", "CGROUP2_FLAGS=warn",
                             f"IO_BASELINE_ENV={base / 'missing-baseline'}"]
        self.extra_env: dict[str, str] = {}

    def set_swaps(self, entries: list[tuple[str, str]]) -> None:
        text = "Filename\t\t\t\tType\t\tSize\tUsed\tPriority\n"
        for name, kind in entries:
            text += f"{name}\t{kind}\t1024\t0\t-2\n"
        (self.proc / "swaps").write_text(text)

    def write_baseline(self, *, schema: str = "3", rbw: int = 4_287_506_096, wbw: int = 2_001_630_742,
                       riops: int = 94246, wiops: int = 67748) -> None:
        target = self.base / "testfile"
        target.write_bytes(b"x")
        baseline = self.base / "io-baseline.env"
        baseline.write_text(
            f"SCHEMA_VERSION={schema}\nKERNEL_RELEASE={os.uname().release}\n"
            "MEASURE_METHOD=iocost-coef-gen\nMEASURED_AT=2026-10-19T00:00:00Z\n"
            f"RIOPS_MAX={riops}\nWIOPS_MAX={wiops}\nRBW_MAX_BPS={rbw}\nWBW_MAX_BPS={wbw}\n"
            "RBPS=1000\nRSEQIOPS=1000\nRRANDIOPS=1000\nWBPS=1000\nWSEQIOPS=1000\nWRANDIOPS=1000\n"
            "DEVNO=111\nTESTFILE_STAT_DEV=111\nDOCKER_STAT_DEV=111\n"
            f"FINDMNT_SOURCE=/dev/fake\nTESTFILE={target}\nTESTFILE_SIZE_BYTES=1\n"
        )
        self.config_lines = [ln for ln in self.config_lines if not ln.startswith("IO_BASELINE_ENV=")]
        self.config_lines += [f"IO_BASELINE_ENV={baseline}", f"IO_BASELINE_TESTFILE={target}"]

    def run(self, **extra: str) -> subprocess.CompletedProcess:
        self.conf.write_text("\n".join(self.config_lines) + "\n")
        env = os.environ.copy()
        env.update({
            "CG": str(self.cg), "CONF": str(self.conf), "MDT_SYSFS_ROOT": str(self.sys),
            "MDT_PROC_ROOT": str(self.proc), "MDT_IO_BASELINE_PY": str(self.baseline_py),
            "PATH": f"{self.bin}:/usr/bin:/bin", "FAKE_STATE": str(self.state), "FAKE_LOG": str(self.log),
            "FAKE_UNIT_STATIC": json.dumps(self.unit_static),
        })
        env.update(self.extra_env)
        env.update(extra)
        self.log.write_text("")
        return subprocess.run(["bash", str(RECONCILE)], env=env, text=True, stdout=subprocess.PIPE,
                              stderr=subprocess.STDOUT, check=False, timeout=60)

    def io_max(self, rel: str = "dev.slice") -> str:
        return (self.cg / rel / "io.max").read_text()

    def calls(self) -> list[str]:
        return self.log.read_text().splitlines()


class ReconcileIoGovernanceTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.host = Host(Path(self._tmp.name))

    # D2 -------------------------------------------------------------------
    def test_withdrawing_the_baseline_never_writes_an_empty_assignment_to_a_capped_slice(self) -> None:
        result = self.host.run()
        empty = [c for c in self.host.calls() if re.search(r"\bIO\w+Max=(\s|$)", c)]
        self.assertEqual(empty, [], result.stdout)
        # the runtime layer holds the EXPLICIT fallback, not nothing
        self.assertEqual(
            self.host.io_max(),
            f"254:0 rbps={150 * MIB} wbps={150 * MIB} riops=500 wiops=500\n",
            result.stdout,
        )
        self.assertEqual(result.returncode, 0, result.stdout)

    def test_the_fake_systemd_would_really_wipe_the_cap_on_an_empty_assignment(self) -> None:
        """Oracle sensitivity: the model must show the live failure when an empty
        assignment IS written, or the D2 test above proves nothing."""
        env = os.environ.copy()
        env.update({"CG": str(self.host.cg), "FAKE_STATE": str(self.host.state), "FAKE_LOG": str(self.host.log),
                    "FAKE_UNIT_STATIC": json.dumps(self.host.unit_static)})
        subprocess.run([str(self.host.bin / "systemctl"), "set-property", "--runtime", "dev.slice",
                        "IOReadBandwidthMax=", "IOWriteBandwidthMax=", "IOReadIOPSMax=", "IOWriteIOPSMax="],
                       env=env, check=True)
        self.assertEqual(self.host.io_max(), "")

    def test_static_fallback_comment_no_longer_claims_empty_assignments_reveal_unit_values(self) -> None:
        text = RECONCILE.read_text()
        self.assertNotIn("reveal the values declared by the installed", text)
        self.assertNotIn("static unit fallback is authoritative", text)

    def test_without_a_device_the_stale_runtime_values_may_be_removed(self) -> None:
        self.host.config_lines[0] = "IO_DEV_PATH="
        self.host.extra_env["PATH"] = f"{self.host.bin}:/usr/bin:/bin"
        # findmnt must not discover a device either
        _write_exec(self.host.bin / "findmnt", "#!/usr/bin/env bash\necho overlay\n")
        result = self.host.run()
        self.assertEqual(result.returncode, 0, result.stdout)
        self.assertIn("no host block device was discovered", result.stdout)

    # D3 -------------------------------------------------------------------
    def test_a_match_is_verified_and_only_then_called_authoritative(self) -> None:
        result = self.host.run()
        self.assertEqual(result.returncode, 0, result.stdout)
        self.assertIn("verified kernel io.max 254:0", result.stdout)
        self.assertIn("is authoritative", result.stdout)
        self.assertIn("disk 254:0", result.stdout)  # the resolved whole disk, not the node

    def test_an_empty_kernel_io_max_fails_and_is_never_certified(self) -> None:
        result = self.host.run(FAKE_KERNEL_DROPS="1")
        self.assertNotEqual(result.returncode, 0, result.stdout)
        self.assertIn("ERROR: dev.slice: kernel io.max has NO entry for 254:0", result.stdout)
        self.assertIn("NOT in effect", result.stdout)
        self.assertNotIn("is authoritative", result.stdout)

    def test_a_cap_on_another_device_is_a_mismatch_not_a_pass(self) -> None:
        result = self.host.run(FAKE_KERNEL_MAJMIN="8:0")
        self.assertNotEqual(result.returncode, 0, result.stdout)
        self.assertIn("NO entry for 254:0", result.stdout)
        self.assertNotIn("is authoritative", result.stdout)

    def test_different_values_in_the_kernel_are_logged_as_actually_applied(self) -> None:
        self.host.config_lines += ["DEV_IO_RBPS_MAX=80M", "DEV_IO_WBPS_MAX=40M", "DEV_IO_RIOPS_MAX=800", "DEV_IO_WIOPS_MAX=600"]
        result = self.host.run()
        self.assertEqual(result.returncode, 0, result.stdout)
        # now corrupt the fake kernel after apply by pre-seeding a drifted file and a
        # systemctl that does not update it
        _write_exec(self.host.bin / "systemctl", "#!/usr/bin/env bash\nexit 0\n")
        (self.host.cg / "dev.slice" / "io.max").write_text("254:0 rbps=1 wbps=1 riops=1 wiops=1\n")
        result = self.host.run()
        self.assertNotEqual(result.returncode, 0, result.stdout)
        self.assertIn("kernel io.max 254:0 is rbps=1 wbps=1 riops=1 wiops=1", result.stdout)

    def test_an_inactive_slice_is_indeterminate_not_a_failure(self) -> None:
        shutil.rmtree(self.host.cg / "dev.slice")
        result = self.host.run()
        self.assertEqual(result.returncode, 0, result.stdout)
        self.assertIn("NOT verified", result.stdout)
        self.assertNotIn("is authoritative", result.stdout)

    # D4 -------------------------------------------------------------------
    def test_pinned_absolute_caps_apply_without_a_baseline_and_size_the_sub_slices(self) -> None:
        self.host.config_lines += ["DEV_IO_RBPS_MAX=80M", "DEV_IO_WBPS_MAX=40M", "DEV_IO_RIOPS_MAX=800", "DEV_IO_WIOPS_MAX=600"]
        result = self.host.run()
        self.assertEqual(result.returncode, 0, result.stdout)
        self.assertEqual(self.host.io_max(), f"254:0 rbps={80 * MIB} wbps={40 * MIB} riops=800 wiops=600\n")
        self.assertEqual(self.host.io_max("dev.slice/dev-gates.slice"), "254:0 rbps=max wbps=max riops=480 wiops=360\n")
        self.assertEqual(self.host.io_max("dev.slice/dev-buildkitd.slice"), "254:0 rbps=max wbps=max riops=480 wiops=360\n")

    def test_without_pins_a_plausible_baseline_gives_the_percentage(self) -> None:
        self.host.write_baseline(rbw=1_000_000, wbw=2_000_000, riops=1000, wiops=2000)
        result = self.host.run()
        self.assertEqual(result.returncode, 0, result.stdout)
        self.assertEqual(self.host.io_max(), "254:0 rbps=600000 wbps=1200000 riops=600 wiops=1200\n")

    def test_when_a_pin_and_a_baseline_exist_the_smaller_wins_per_value(self) -> None:
        self.host.write_baseline(rbw=1_000_000, wbw=9_000_000_000, riops=1000, wiops=9_000_000)
        self.host.config_lines += ["DEV_IO_RBPS_MAX=80M", "DEV_IO_WBPS_MAX=40M", "DEV_IO_RIOPS_MAX=800", "DEV_IO_WIOPS_MAX=600"]
        result = self.host.run()
        self.assertEqual(result.returncode, 0, result.stdout)
        # rbps: 60% baseline (600000) < pin; wbps: pin < 60% baseline; riops: 600 < 800; wiops: pin 600
        self.assertEqual(self.host.io_max(), f"254:0 rbps=600000 wbps={40 * MIB} riops=600 wiops=600\n")

    def test_invalid_pins_are_refused_loudly_and_the_slice_stays_capped(self) -> None:
        for bad in ("0", "fast", "-5M", "1.5G"):
            with self.subTest(bad=bad):
                self.host.state.unlink(missing_ok=True)
                self.host.config_lines = [ln for ln in self.host.config_lines if not ln.startswith("DEV_IO_")]
                self.host.config_lines.append(f"DEV_IO_WBPS_MAX={bad}")
                result = self.host.run()
                self.assertNotEqual(result.returncode, 0, result.stdout)
                self.assertIn("ERROR: DEV_IO_WBPS_MAX=", result.stdout)
                self.assertIn(f"wbps={150 * MIB}", self.host.io_max())  # static fallback still holds

    def test_suffixes_are_converted_to_bytes_once_for_unit_and_runtime(self) -> None:
        script = f'''
log() {{ echo "$*"; }}
. "{LIB}"
DEV_IO_RBPS_MAX=80M DEV_STATIC_RBW=150M DEV_STATIC_WBW=2G DEV_STATIC_RIOPS=7 DEV_STATIC_WIOPS=
_mdt_resolve_static_io; echo "rc=$? r=$MDT_STATIC_RBPS w=$MDT_STATIC_WBPS ri=$MDT_STATIC_RIOPS wi=$MDT_STATIC_WIOPS"
'''
        out = subprocess.run(["bash", "-c", script], text=True, stdout=subprocess.PIPE, check=True).stdout
        self.assertIn(f"rc=0 r={80 * MIB} w={2 * 1024 ** 3} ri=7 wi=500", out)

    # D5 -------------------------------------------------------------------
    def test_an_old_schema_baseline_is_rejected_as_stale_with_a_clear_message(self) -> None:
        self.host.write_baseline(schema="2")
        result = self.host.run()
        self.assertIn("schema is 2, expected 3", result.stdout)
        self.assertIn("stale", result.stdout)
        self.assertEqual(self.host.io_max(), f"254:0 rbps={150 * MIB} wbps={150 * MIB} riops=500 wiops=500\n")

    def test_an_implausible_baseline_is_never_applied_even_though_its_numbers_parse(self) -> None:
        self.host.write_baseline()  # the cache-inflated numbers measured on 2026-10-19
        _write_exec(self.host.baseline_py,
                    "#!/usr/bin/env bash\necho 'implausible baseline: RBW_MAX_BPS=4287506096 exceeds 1073741824 B/s' >&2\nexit 1\n")
        result = self.host.run()
        self.assertIn("implausible baseline", result.stdout)
        self.assertEqual(self.host.io_max(), f"254:0 rbps={150 * MIB} wbps={150 * MIB} riops=500 wiops=500\n")

    def test_a_missing_baseline_checker_is_not_a_pass(self) -> None:
        self.host.write_baseline(rbw=1_000_000, wbw=1_000_000, riops=1000, wiops=1000)
        self.host.baseline_py.unlink()
        result = self.host.run()
        self.assertIn("cannot be verified", result.stdout)
        self.assertEqual(self.host.io_max(), f"254:0 rbps={150 * MIB} wbps={150 * MIB} riops=500 wiops=500\n")

    # D6 -------------------------------------------------------------------
    def test_swap_on_an_uncapped_disk_is_warned_loudly(self) -> None:
        self.host.set_swaps([("/dev/null", "partition"), ("/dev/zero", "partition")])
        result = self.host.run()
        self.assertIn("UNCAPPED SWAP: /dev/zero lies on disk 8:16", result.stdout)
        self.assertNotIn("UNCAPPED SWAP: /dev/null", result.stdout)

    def test_swap_on_the_capped_disk_and_zram_are_quiet(self) -> None:
        self.host.set_swaps([("/dev/null", "partition"), ("/dev/zram0", "partition")])
        result = self.host.run()
        self.assertNotIn("UNCAPPED SWAP", result.stdout)
        self.assertNotIn("zram0", result.stdout)


class DeviceResolutionTests(unittest.TestCase):
    def _resolve(self, host: Host, path: str) -> str:
        script = f'log(){{ :; }}; . "{LIB}"; _mdt_dev_majmin "{path}"; echo "rc=$?"'
        env = os.environ.copy()
        env["MDT_SYSFS_ROOT"] = str(host.sys)
        return subprocess.run(["bash", "-c", script], env=env, text=True, stdout=subprocess.PIPE).stdout.split()[0]

    def test_partition_and_single_slave_mapper_resolve_to_the_whole_disk(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            host = Host(Path(tmp))
            self.assertEqual(self._resolve(host, "/dev/null"), "254:0")  # partition -> disk
            # /dev/zero as a device-mapper node with ONE slave (vdb), systemd resolves through it
            dm = host.sys / "devices" / "dm-0"
            (dm / "slaves").mkdir(parents=True)
            (dm / "slaves" / "vdb").symlink_to("../../vdb")
            (host.sys / "dev" / "block" / "1:5").unlink()
            (host.sys / "dev" / "block" / "1:5").symlink_to("../../devices/dm-0")
            self.assertEqual(self._resolve(host, "/dev/zero"), "8:16")

    def test_a_node_with_several_slaves_is_unresolvable_not_guessed(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            host = Host(Path(tmp))
            dm = host.sys / "devices" / "dm-0"
            (dm / "slaves").mkdir(parents=True)
            (dm / "slaves" / "vdb").symlink_to("../../vdb")
            (dm / "slaves" / "vda").symlink_to("../../vda")
            (host.sys / "dev" / "block" / "1:5").unlink()
            (host.sys / "dev" / "block" / "1:5").symlink_to("../../devices/dm-0")
            self.assertEqual(self._resolve(host, "/dev/zero"), "rc=1")


class BaselinePlausibilityTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.mod = _load(SCRIPTS / "mdt-io-baseline.py", "mdt_io_baseline_gov")

    def _values(self, **over: str) -> dict[str, str]:
        v = {"RBW_MAX_BPS": "20000000", "WBW_MAX_BPS": "20000000", "RIOPS_MAX": "1500", "WIOPS_MAX": "1500",
             "DEVICE_ROTATIONAL": "1"}
        v.update(over)
        return v

    def test_the_cache_inflated_numbers_measured_on_the_live_host_are_implausible(self) -> None:
        problem = self.mod.plausibility_problem(self._values(
            RBW_MAX_BPS="4287506096", WBW_MAX_BPS="2001630742", RIOPS_MAX="94246", WIOPS_MAX="67748"))
        self.assertIn("implausible", problem or "")

    def test_a_realistic_rotational_baseline_and_a_fast_non_rotational_one_pass(self) -> None:
        self.assertIsNone(self.mod.plausibility_problem(self._values()))
        self.assertIsNone(self.mod.plausibility_problem(self._values(
            DEVICE_ROTATIONAL="0", RBW_MAX_BPS="4287506096", RIOPS_MAX="94246")))

    def test_each_value_is_checked_on_its_own(self) -> None:
        for key in ("RBW_MAX_BPS", "WBW_MAX_BPS", "RIOPS_MAX", "WIOPS_MAX"):
            with self.subTest(key=key):
                self.assertIn(key, self.mod.plausibility_problem(self._values(**{key: "99999999999"})) or "")

    def test_schema_3_records_the_method_and_old_schema_is_stale(self) -> None:
        self.assertEqual(self.mod.SCHEMA_VERSION, "3")
        self.assertIn("hypervisor caches are NOT defeated", self.mod.MEASURE_DETAIL)
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "b.env"
            path.write_text("SCHEMA_VERSION=2\nMEASURE_METHOD=iocost-coef-gen\nMEASURED_AT=x\n")
            self.assertIn("stale", self.mod.results_problem(path) or "")


class ListingToolTests(unittest.TestCase):
    """The listing tool must read cgroup CONTENT: control files report size 0."""

    @staticmethod
    def _fifo_file(path: Path, content: str) -> None:
        os.mkfifo(path)

        def serve() -> None:
            while True:
                try:
                    with open(path, "w") as handle:
                        handle.write(content)
                except OSError:
                    return

        threading.Thread(target=serve, daemon=True).start()

    def _tree(self, tmp: str) -> Path:
        root = Path(tmp)
        (root / "dev.slice" / "dev-gates.slice").mkdir(parents=True)
        self._fifo_file(root / "dev.slice" / "io.max", f"254:0 rbps={80 * MIB} wbps={40 * MIB} riops=800 wiops=600\n")
        self._fifo_file(root / "dev.slice" / "dev-gates.slice" / "io.max", "254:0 rbps=max wbps=max riops=480 wiops=360\n")
        return root

    def test_the_fixture_really_defeats_a_size_test(self) -> None:
        """Negative control: the old script's `[ -s io.max ]` sees nothing here."""
        with tempfile.TemporaryDirectory() as tmp:
            root = self._tree(tmp)
            f = root / "dev.slice" / "io.max"
            self.assertEqual(f.stat().st_size, 0)
            r = subprocess.run(["bash", "-c", f'if [ -s "{f}" ]; then echo SEEN; else echo MISSED; fi'],
                               text=True, stdout=subprocess.PIPE, check=True)
            self.assertEqual(r.stdout.strip(), "MISSED")

    def test_it_lists_limits_from_files_that_report_size_zero_with_effective_values(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = self._tree(tmp)
            r = subprocess.run(["bash", str(LISTER), "dev.slice"], env={**os.environ, "CGROUP_ROOT": str(root)},
                               text=True, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, timeout=30)
            self.assertEqual(r.returncode, 0, r.stdout)
            lines = {ln.split()[0]: ln for ln in r.stdout.splitlines()[1:] if ln.strip()}
            self.assertIn("EFFECTIVE", r.stdout.splitlines()[0])
            self.assertRegex(lines["dev.slice"], r"800\s+600\s+r=\S+ w=\S+ ri=800 wi=600")
            child = lines["dev.slice/dev-gates.slice"]
            # own entry is 480/360 and the parent's 80M/40M bandwidth is INHERITED into EFFECTIVE
            self.assertRegex(child, r"ri=480 wi=360")
            self.assertRegex(child, r"r=(80MB/s|83886080) w=(40MB/s|41943040)")

    def test_systemd_mode_prints_properties_and_drop_in_paths(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = self._tree(tmp)
            bin_dir = Path(tmp) / "bin"
            bin_dir.mkdir()
            log = Path(tmp) / "sc.log"
            state = Path(tmp) / "state.json"
            state.write_text(json.dumps({"unit": {}, "runtime": {"dev.slice": {"IOReadIOPSMax": []}}}))
            _write_exec(bin_dir / "systemctl", FAKE_SYSTEMCTL.replace("__PY__", sys.executable))
            r = subprocess.run(
                ["bash", str(LISTER), "dev.slice", "--systemd"],
                env={**os.environ, "CGROUP_ROOT": str(root), "PATH": f"{bin_dir}:{os.environ['PATH']}",
                     "FAKE_STATE": str(state), "FAKE_LOG": str(log), "CG": str(root)},
                text=True, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, timeout=30)
            self.assertEqual(r.returncode, 0, r.stdout)
            self.assertIn("== systemd view", r.stdout)
            self.assertIn("-- dev.slice", r.stdout)
            self.assertIn("DropInPaths=/run/systemd/system.control/dev.slice.d/", r.stdout)
            self.assertIn("-- dev-gates.slice", r.stdout)

    def test_no_size_test_is_ever_used_on_a_cgroup_control_file(self) -> None:
        for script in (LISTER, RECONCILE, LIB, SCRIPTS / "check.sh"):
            with self.subTest(script=script.name):
                self.assertIsNone(re.search(r"\[ +-s ", script.read_text()), script.name)

    def test_unknown_cgroup_is_an_error(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            r = subprocess.run(["bash", str(LISTER), "nope.slice"], env={**os.environ, "CGROUP_ROOT": tmp},
                               text=True, stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
            self.assertEqual(r.returncode, 1)


class WizardAndDocsTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.wizard = _load(ROOT / "mdt-host-setup-wizard.py", "mdt_wizard_gov")

    PIN_KEYS = ("DEV_IO_RBPS_MAX", "DEV_IO_WBPS_MAX", "DEV_IO_RIOPS_MAX", "DEV_IO_WIOPS_MAX")

    def test_pin_validator_matches_the_runtime_rules(self) -> None:
        for good in ("", "800", "80M", "1G", "1T", "4096K"):
            self.assertIsNone(self.wizard.validate_io_pin(good), good)
        for bad in ("0", "00", "-1", "1.5G", "fast", "80MB", " 80M", "M"):
            self.assertIsNotNone(self.wizard.validate_io_pin(bad), bad)

    def test_every_pin_key_is_validated_and_shipped_in_the_example(self) -> None:
        validators = self.wizard._config_value_validators()
        example = self.wizard.parse_env_file((ROOT / "host-setup.env.example").read_text())
        for key in self.PIN_KEYS:
            self.assertIn(key, validators)
            self.assertIn(key, example)
            self.assertEqual(example[key], "")  # an illustration lives in a comment, never a shipped host value

    def test_install_sh_renders_one_source_and_installs_the_listing_tool(self) -> None:
        install = (ROOT / "install.sh").read_text()
        self.assertIn("_mdt_resolve_static_io", install)
        self.assertIn("mdt-cgroup-io-limits.sh", install)
        self.assertTrue(os.access(LISTER, os.X_OK))

    def test_the_four_documents_cover_the_new_keys_and_the_tool(self) -> None:
        docs = {
            "host-setup/README.md": ROOT / "README.md",
            "host-setup/CGROUP-NOTES.md": ROOT / "CGROUP-NOTES.md",
            "docs/CONSUMERS.md": ROOT.parent / "docs" / "CONSUMERS.md",
            "docs/DESIGN-GUIDE.md": ROOT.parent / "docs" / "DESIGN-GUIDE.md",
        }
        for name, path in docs.items():
            text = path.read_text()
            for needle in (*self.PIN_KEYS, "mdt-cgroup-io-limits.sh"):
                if name in ("host-setup/CGROUP-NOTES.md", "docs/DESIGN-GUIDE.md") and needle != "mdt-cgroup-io-limits.sh" \
                        and needle != "DEV_IO_RBPS_MAX":
                    continue  # the rationale documents name the family once
                self.assertIn(needle, text, f"{name} does not mention {needle}")
        for name in ("host-setup/README.md", "docs/CONSUMERS.md"):
            text = docs[name].read_text()
            for needle in ("DEV_SWAP_MAX", "DEV_GATES_MEMORY_SWAP_MAX", "kernel update", "UNCAPPED SWAP"):
                self.assertIn(needle, text, f"{name} does not mention {needle}")
        self.assertIn("MDT-016", (ROOT.parent / "KNOWN_ISSUES_TODO_BACKLOG.md").read_text())


if __name__ == "__main__":
    unittest.main()
