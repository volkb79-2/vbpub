"""MDT-TESTHOST-MEM: the memory half of HOST_ROLE=dedicated-dev.  Hermetic: fake meminfo, fake systemctl, tmp dirs.

shared-prod (and an empty role) must stay exactly as it was; dedicated-dev derives dev.slice's limits from
RAM minus the host reserve K (floors + margin, P-plat-19e), renders no MemoryHigh and protects the host's own services."""
from __future__ import annotations

import importlib.util
import os
import re
import shutil
import stat
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
INSTALL_SH = (ROOT / "install.sh").read_text(encoding="utf-8")
CHECK_SH = (ROOT / "scripts" / "check.sh").read_text(encoding="utf-8")
EXAMPLE = ROOT / "host-setup.env.example"
CAPACITY = ROOT / "mdt_capacity.py"
sys.path.insert(0, str(ROOT))


def _load(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


cap = _load("mdt_capacity_mem_t", CAPACITY)
guard = _load("mdt_psi_guard_mem_t", ROOT / "scripts" / "mdt-psi-guard.py")

GIB = 1024 ** 3
MIB = 1024 ** 2
# The two test hosts: 8 GB RAM, 16 GB swap.
HOST = dict(mem_total=8 * GIB, swap_total=16 * GIB)


def meminfo(path: Path, mem_gib: int = 8, swap_gib: int = 16) -> Path:
    path.write_text(f"MemTotal:       {mem_gib * 1024 * 1024} kB\nSwapTotal:      {swap_gib * 1024 * 1024} kB\n")
    return path


K = 1077 * MIB  # F 565M (373 + 128 + 64) + M 512M, design note section 4


class DerivationTests(unittest.TestCase):
    def test_default_k_is_the_floors_plus_the_margin_and_dev_max_follows(self) -> None:
        m = cap.dedicated_memory({}, **HOST)
        self.assertEqual((m.floors.floors, m.floors.margin, m.keep), (565 * MIB, 512 * MIB, K))
        self.assertEqual((m.floors.system_slice, m.floors.user_slice, m.floors.cgprofile_slice), (373 * MIB, 64 * MIB, 128 * MIB))
        self.assertEqual(m.maximum, 8 * GIB - K)  # RAM only: swap does not enter the ceiling any more
        self.assertIsNone(m.high)
        self.assertIsNone(m.low)
        self.assertEqual((m.max_source, m.interactive_min, m.dev_min), ("derived", 512 * MIB, 512 * MIB))

    def test_r1002_numbers_of_the_design_note(self) -> None:
        m = cap.dedicated_memory({"DEV_INTERACTIVE_MEMORY_LOW": "2.5G"}, mem_total=8320679936, swap_total=63 * GIB)
        self.assertEqual(m.keep, 1129316352)
        self.assertEqual(m.maximum, 7191363584)  # 6858.22 MiB
        self.assertEqual((m.gates_max, m.background_max), (7191363584, 7191363584))  # P-plat-19h: = dev.slice's own Max, 6858.22 MiB
        self.assertEqual(m.dev_min, 536870912)
        self.assertEqual((m.gates_high, m.background_high, m.high), (None, None, None))  # no MemoryHigh anywhere
        self.assertEqual(cap.load_tier_swap(62 * GIB, m.protected), 62 * GIB - 2560 * MIB)  # AD2-0 unchanged

    def test_excluded_ram_is_subtracted_and_swap_does_not_matter(self) -> None:
        m = cap.dedicated_memory({"MDT_EXCLUDED_RAM": "1G", "MDT_EXCLUDED_SWAP": "2G"}, **HOST)
        self.assertEqual(m.maximum, 7 * GIB - K)
        self.assertEqual(cap.dedicated_memory({}, mem_total=8 * GIB, swap_total=0).maximum, 8 * GIB - K)

    def test_keep_is_a_pin_of_k_and_is_refused_below_the_floors(self) -> None:
        m = cap.dedicated_memory({"DEV_HOST_MEMORY_KEEP": "1280M"}, **HOST)
        self.assertEqual((m.keep, m.maximum, m.floors.pinned), (1280 * MIB, 8 * GIB - 1280 * MIB, True))
        self.assertEqual(cap.dedicated_memory({"DEV_HOST_MEMORY_KEEP": "693M"}, **HOST).keep, 693 * MIB)  # F + 128M
        with self.assertRaisesRegex(cap.CapacityError, "F \\+ 128M"):
            cap.dedicated_memory({"DEV_HOST_MEMORY_KEEP": "692M"}, **HOST)

    def test_pins_win_per_value_a_dash_omits_and_high_pins_are_ignored_and_named(self) -> None:
        m = cap.dedicated_memory({"DEV_MEMORY_MAX": "6G", "DEV_MEMORY_HIGH": "5G", "DEV_GATES_MEMORY_HIGH": "4G",
                                  "DEV_BACKGROUND_MEMORY_HIGH": "-"}, **HOST)
        self.assertEqual(m.maximum, 6 * GIB)
        self.assertIn("pin", m.max_source)
        self.assertIsNone(m.high)
        self.assertEqual(m.ignored, ("DEV_MEMORY_HIGH=5G", "DEV_GATES_MEMORY_HIGH=4G"))

    def test_an_explicit_unlimited_dev_memory_max_is_refused(self) -> None:
        # P-plat-19e review B1: `-`, 0, max and infinity would run the whole dev tree at memory.max=max
        for raw in ("-", "0", "0G", "0.0", "max", "infinity", "MAX"):
            with self.subTest(raw), self.assertRaisesRegex(cap.CapacityError, "without a MemoryMax"):
                cap.dedicated_memory({"DEV_MEMORY_MAX": raw}, **HOST)

    def test_dev_low_is_the_sum_of_the_tier_lows_unless_pinned(self) -> None:
        values = {"DEV_INTERACTIVE_MEMORY_LOW": "512M", "DEV_BUILDKITD_MEMORY_LOW": "256M", "DEV_GATES_MEMORY_LOW": "-"}
        self.assertEqual(cap.dedicated_memory(values, **HOST).low, 768 * MIB)
        self.assertEqual(cap.dedicated_memory({**values, "DEV_MEMORY_LOW": "1G"}, **HOST).low, GIB)

    def test_dev_min_is_the_sum_of_every_child_min_key(self) -> None:
        m = cap.dedicated_memory({"DEV_MEMORY_MIN_GUARANTEED_CEILING": "1G", "DEV_GATES_MEMORY_MIN": "100M",
                                  "DEV_BACKGROUND_MEMORY_MIN": "50M", "DEV_BUILDKITD_MEMORY_MIN": "25M",
                                  "DEV_INTERACTIVE_MEMORY_MIN": "300M"}, **HOST)
        self.assertEqual(m.dev_min, (1024 + 100 + 50 + 25 + 300) * MIB)
        self.assertEqual(cap.dedicated_memory({"DEV_INTERACTIVE_MEMORY_MIN": "-"}, **HOST).dev_min, 0)

    def test_refusals(self) -> None:
        big = dict(mem_total=32 * GIB, swap_total=16 * GIB)
        cases = {
            "keep 0": ({"DEV_HOST_MEMORY_KEEP": "0"}, HOST, "DEV_HOST_MEMORY_KEEP"),
            "keep 1 byte": ({"DEV_HOST_MEMORY_KEEP": "1"}, HOST, "at least 256M"),
            "keep junk": ({"DEV_HOST_MEMORY_KEEP": "lots"}, HOST, "DEV_HOST_MEMORY_KEEP"),
            "keep leaves < 1G RAM": ({"DEV_HOST_MEMORY_KEEP": "7500M"}, HOST, "at least 1G"),
            "max pin 128M": ({"DEV_MEMORY_MAX": "128M"}, HOST, "stall"),
            "pin junk": ({"DEV_MEMORY_MAX": "lots"}, HOST, "not a size"),
            "max pin above MemTotal - K": ({"DEV_MEMORY_MAX": "8G"}, HOST, "exceeds MemTotal"),
            "tier max pin above dev": ({"DEV_GATES_MEMORY_MAX": "7G"}, HOST, "exceeds dev.slice MemoryMax"),
            "F above 10% of MemTotal": ({"DEV_HOST_MEMORY_MIN": "docker.service=900M"}, HOST, "10% of MemTotal"),
            "system.slice above 460M": ({"DEV_HOST_MEMORY_MIN": "docker.service=500M"}, big, "460M"),
            "row without size": ({"DEV_HOST_MEMORY_MIN": "docker.service"}, HOST, "unit=size"),
            "slice row": ({"DEV_HOST_MEMORY_MIN": "system.slice=10M"}, HOST, "unit=size"),
            "row below 1M": ({"DEV_HOST_MEMORY_MIN": "docker.service=512K"}, HOST, "unit=size"),
            "margin below 64M": ({"DEV_HOST_MEMORY_MARGIN": "32M"}, HOST, "at least 64M"),
            "interactive min above low": ({"DEV_INTERACTIVE_MEMORY_MIN": "1G", "DEV_INTERACTIVE_MEMORY_LOW": "512M"}, HOST, "exceeds DEV_INTERACTIVE_MEMORY_LOW"),
        }
        for name, (values, host, text) in cases.items():
            with self.subTest(name), self.assertRaisesRegex(cap.CapacityError, text):
                cap.dedicated_memory(values, **host)

    def test_a_zero_pin_omits_like_a_dash_as_in_the_shared_validators(self) -> None:
        self.assertIsNone(cap.dedicated_memory({"DEV_MEMORY_LOW": "0"}, **HOST).low)
        self.assertEqual(cap.dedicated_memory({"DEV_GATES_MEMORY_MAX": "0"}, **HOST).gates_max_source, "omitted (0)")
        self.assertIsNone(cap.validate_size_or_zero("0"))

    LOWS = {"DEV_INTERACTIVE_MEMORY_LOW": "512M", "DEV_BUILDKITD_MEMORY_LOW": "256M"}

    def test_load_tiers_equal_dev_max_whatever_the_protected_lows_and_carry_no_high(self) -> None:
        # P-plat-19h regression guard: the tier MemoryMax is dev.slice's own; a minus-P subtraction must fail here
        m = cap.dedicated_memory(self.LOWS, **HOST)
        self.assertEqual(m.protected, 768 * MIB)  # P still exists: it sizes the swap ceiling and the Low protection
        for tier in ("gates", "background"):
            self.assertEqual(getattr(m, f"{tier}_max"), m.maximum)
            self.assertIsNone(getattr(m, f"{tier}_high"))
        big = cap.dedicated_memory({"DEV_INTERACTIVE_MEMORY_LOW": "2.5G"}, mem_total=8320679936, swap_total=63 * GIB)
        self.assertEqual((big.gates_max, big.background_max), (big.maximum, big.maximum))
        self.assertNotEqual(big.gates_max, big.maximum - big.protected)
        m0 = cap.dedicated_memory({"DEV_GATES_MEMORY_LOW": "1G"}, **HOST)  # a load tier's own Low is not protected
        self.assertEqual((m0.gates_max, m0.background_max), (m0.maximum, m0.maximum))

    def test_cache_low_counts_in_the_protected_sum_and_defaults_to_zero(self) -> None:
        m = cap.dedicated_memory({**self.LOWS, "DEV_CACHE_MEMORY_LOW": "256M"}, **HOST)
        self.assertEqual((m.protected, m.gates_max), (GIB, m.maximum))
        for off in ("", "-"):
            self.assertEqual(cap.dedicated_memory({**self.LOWS, "DEV_CACHE_MEMORY_LOW": off}, **HOST).protected, 768 * MIB)
        with self.assertRaises(cap.CapacityError):
            cap.dedicated_memory({"DEV_CACHE_MEMORY_LOW": "lots"}, **HOST)

    def test_load_tier_swap_is_dev_swap_minus_protected_with_floor_and_cap(self) -> None:
        for swap_gib, want in ((0, 0), (8, 8 * GIB - 768 * MIB), (16, 16 * GIB - 768 * MIB)):
            self.assertEqual(cap.load_tier_swap(swap_gib * GIB, 768 * MIB), want)
        self.assertEqual(cap.load_tier_swap(512 * MIB, 768 * MIB), 256 * MIB)  # floor
        self.assertEqual(cap.load_tier_swap(128 * MIB, 768 * MIB), 128 * MIB)  # never above dev.slice's
        self.assertEqual(cap.load_tier_swap(8 * GIB, 0), 8 * GIB)

    def test_load_tier_cap_omission_and_pins(self) -> None:
        m = cap.dedicated_memory({**self.LOWS, "DEV_INTERACTIVE_MEMORY_MIN": "-", "DEV_MEMORY_MAX": "1500M"}, **HOST)
        self.assertEqual(m.gates_max, 1500 * MIB)  # dev's own Max: no minus-P, so no 1G floor is needed either
        tiny = cap.dedicated_memory({"DEV_INTERACTIVE_MEMORY_LOW": "4G", "DEV_INTERACTIVE_MEMORY_MIN": "-", "DEV_MEMORY_MAX": "512M"}, **HOST)
        self.assertEqual(tiny.gates_max, 512 * MIB)  # never above dev.slice's own
        pinned = cap.dedicated_memory({**self.LOWS, "DEV_GATES_MEMORY_MAX": "3G", "DEV_BACKGROUND_MEMORY_MAX": "-"}, **HOST)
        self.assertEqual(pinned.gates_max, 3 * GIB)
        self.assertIn("pin", pinned.gates_max_source)
        self.assertIsNone(pinned.background_max)

    def test_interactive_and_buildkitd_ceilings_are_not_derived(self) -> None:
        m = cap.dedicated_memory(self.LOWS, **HOST)
        self.assertFalse(hasattr(m, "interactive_high"))
        self.assertFalse(hasattr(m, "buildkitd_high"))

    def test_validators(self) -> None:
        self.assertIsNone(cap.validate_host_memory_keep("512M"))
        self.assertIsNone(cap.validate_host_memory_keep(""))  # empty derives K
        for bad in ("0", "-", "1x", "1", "255M"):
            self.assertIsNotNone(cap.validate_host_memory_keep(bad), bad)
        for ok in ("", "-", "docker.service containerd.service"):
            self.assertIsNone(cap.validate_host_protected_units(ok), ok)
        self.assertIsNotNone(cap.validate_host_protected_units("rm -rf"))
        for ok in ("", "-", "docker.service=300M ssh.service=1M"):
            self.assertIsNone(cap.validate_host_memory_min(ok), ok)
        self.assertIsNone(cap.validate_host_memory_margin(""))
        self.assertIsNone(cap.validate_host_memory_margin("64M"))
        self.assertIsNotNone(cap.validate_host_memory_margin("63M"))

    def test_ssh_can_never_be_protected_by_an_oom_score_drop_in(self) -> None:
        # review F1: sshd hands its own score to every login session it forks (it gets a MemoryMin floor instead)
        self.assertNotIn("ssh", " ".join(cap.DEDICATED_PROTECTED_UNITS_DEFAULT.split()).replace("systemd", ""))
        self.assertIn("ssh.service", cap.DEDICATED_HOST_MEMORY_MIN_DEFAULTS)
        for unit in ("ssh.service", "sshd.service", "ssh.socket", "docker.service ssh.service"):
            with self.subTest(unit):
                self.assertRegex(cap.validate_host_protected_units(unit) or "", "login")
                with self.assertRaisesRegex(cap.CapacityError, "DEV_HOST_PROTECTED_UNITS"):
                    cap.dedicated_protected_units({"DEV_HOST_PROTECTED_UNITS": unit})

    def test_the_key_is_not_a_retired_reserve_key(self) -> None:
        self.assertEqual(cap.renamed_key_errors({"DEV_HOST_MEMORY_KEEP": "512M", "DEV_HOST_MEMORY_MARGIN": "", "DEV_HOST_PROTECTED_UNITS": ""}), [])

    def test_protected_units_default_dash_and_custom(self) -> None:
        defaults = cap.dedicated_protected_units({})
        self.assertEqual(defaults, cap.DEDICATED_PROTECTED_UNITS_DEFAULT.split())
        self.assertIn("cgpd.service", defaults)
        self.assertIn("cgprofiled.service", defaults)
        example = (ROOT / "host-setup.env.example").read_text(encoding="utf-8")
        protected_default = next(line for line in example.splitlines() if line.startswith("# DEV_HOST_PROTECTED_UNITS:"))
        self.assertIn("cgpd.service", protected_default)
        self.assertIn("cgprofiled.service", protected_default)
        self.assertEqual(cap.dedicated_protected_units({"DEV_HOST_PROTECTED_UNITS": "-"}), [])
        self.assertEqual(cap.dedicated_protected_units({"DEV_HOST_PROTECTED_UNITS": "a.service b.service"}), ["a.service", "b.service"])


class FloorTests(unittest.TestCase):
    def test_the_table_and_the_derived_slice_floors(self) -> None:
        f = cap.host_floors({}, 8 * GIB)
        self.assertEqual({u: v // MIB for u, v in f.units.items()},
                         {"ssh.service": 10, "docker.service": 210, "containerd.service": 92, "systemd-journald.service": 24,
                          "cgprofiled.service": 4, "mdt-psi-guard.service": 5})
        self.assertEqual((f.system_slice // MIB, f.user_slice // MIB, f.cgprofile_slice // MIB), (373, 64, 128))  # 341 + 32; cgprofiled stays out
        self.assertEqual((f.floors // MIB, f.keep // MIB), (565, 1077))

    def test_a_raised_unit_raises_the_system_slice_sum_and_a_lower_raise_changes_nothing(self) -> None:
        f = cap.host_floors({}, 8 * GIB, {"docker.service": 250 * MIB, "ssh.service": MIB})
        self.assertEqual((f.units["docker.service"] // MIB, f.units["ssh.service"] // MIB, f.system_slice // MIB), (250, 10, 413))

    def test_row_overrides_extras_and_none(self) -> None:
        f = cap.host_floors({"DEV_HOST_MEMORY_MIN": "docker.service=250M foo.service=5M"}, 8 * GIB)
        self.assertEqual((f.units["docker.service"] // MIB, f.system_slice // MIB), (250, 418))  # 413 + foo
        none = cap.host_floors({"DEV_HOST_MEMORY_MIN": "-"}, 8 * GIB)
        self.assertEqual((none.units, none.system_slice, none.floors // MIB), ({}, 0, 192))

    def test_system_slice_low_equals_the_min_at_defaults_and_follows_a_keep_pin(self) -> None:
        self.assertEqual(cap.system_slice_low(cap.host_floors({}, 8 * GIB)), 373 * MIB)
        pinned = cap.host_floors({"DEV_HOST_MEMORY_KEEP": "1280M"}, 8 * GIB)
        self.assertEqual(cap.system_slice_low(pinned), (1280 - 512 - 128 - 64) * MIB)  # above the 373M Min

    @unittest.skipUnless((ROOT.parents[1] / "scripts/debian-install-v2/debian_install_v2/templates.py").is_file(), "debian-install-v2 not in this checkout")
    def test_the_mirror_equals_debian_install_v2_and_the_design_note_table(self) -> None:
        import ast
        tree = ast.parse((ROOT.parents[1] / "scripts/debian-install-v2/debian_install_v2/templates.py").read_text(encoding="utf-8"))
        node = next(n.value for n in tree.body if isinstance(n, ast.Assign)
                    and any(getattr(t, "id", "") == "CONTROL_PLANE_MEMORY_MIN" for t in n.targets))
        table = eval(compile(ast.Expression(node), "templates.py", "eval"), {"__builtins__": {}})  # literal tuples of str and `n * 1024 * 1024`
        self.assertEqual({u: row[1] for u, row in table.items()}, cap.DEDICATED_HOST_MEMORY_MIN_DEFAULTS)
        note = (ROOT / "docs" / "DESIGN-DEDICATED-DEV-MEMORY.md").read_text(encoding="utf-8")
        rows = {u: int(v) * MIB for u, v in re.findall(r"(?m)^\| ((?:[a-z-]+)\.service) \| (\d+)M \|", note)}
        self.assertEqual(rows, cap.DEDICATED_HOST_MEMORY_MIN_DEFAULTS)


class CliTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.tmp, True)

    def run_cli(self, env_text: str, *args: str) -> subprocess.CompletedProcess:
        env = self.tmp / "host-setup.env"
        env.write_text(env_text)
        return subprocess.run([sys.executable, str(CAPACITY), *args[:1], str(env), *args[1:]], capture_output=True, text=True)

    def test_dedicated_memory_refuses_ssh_in_the_protected_list(self) -> None:
        r = self.run_cli("HOST_ROLE=dedicated-dev\nDEV_HOST_PROTECTED_UNITS=docker.service ssh.service\n", "dedicated-memory", "--meminfo", str(meminfo(self.tmp / "mi")))
        self.assertEqual(r.returncode, 2)
        self.assertIn("ssh.service", r.stderr)

    def test_dedicated_memory_prints_key_value_lines(self) -> None:
        r = self.run_cli("HOST_ROLE=dedicated-dev\n", "dedicated-memory", "--meminfo", str(meminfo(self.tmp / "mi")))
        self.assertEqual(r.returncode, 0, r.stderr)
        out = dict(line.split("=", 1) for line in r.stdout.splitlines())
        self.assertEqual(out["DEDICATED_MEM_MAX"], str(8 * GIB - K))
        self.assertEqual(out["DEDICATED_MEM_HIGH"], "")
        self.assertEqual((out["DEDICATED_MEM_KEEP"], out["DEDICATED_MEM_MIN"]), (str(K), str(512 * MIB)))
        self.assertEqual(out["DEDICATED_RESERVE"], f"{565 * MIB} {512 * MIB} {K}")
        self.assertEqual((out["DEDICATED_SYSTEM_SLICE_MIN"], out["DEDICATED_SYSTEM_SLICE_LOW"], out["DEDICATED_USER_SLICE_MIN"]),
                         (str(373 * MIB), str(373 * MIB), str(64 * MIB)))
        self.assertEqual(out["DEDICATED_FLOOR docker.service"], str(210 * MIB))
        self.assertEqual(out["DEDICATED_PROTECTED_UNITS"], cap.DEDICATED_PROTECTED_UNITS_DEFAULT)

    def test_a_raised_floor_reaches_the_slice_sum_and_the_ignored_highs_are_named(self) -> None:
        r = self.run_cli("HOST_ROLE=dedicated-dev\nDEV_GATES_MEMORY_HIGH=4G\n", "dedicated-memory", "--meminfo", str(meminfo(self.tmp / "mi")),
                         "--raised", f"docker.service={250 * MIB}")
        self.assertEqual(r.returncode, 0, r.stderr)
        out = dict(line.split("=", 1) for line in r.stdout.splitlines())
        self.assertEqual((out["DEDICATED_FLOOR docker.service"], out["DEDICATED_SYSTEM_SLICE_MIN"]), (str(250 * MIB), str(413 * MIB)))
        self.assertEqual(out["DEDICATED_IGNORED"], "DEV_GATES_MEMORY_HIGH=4G")

    def test_dedicated_memory_prints_the_load_tier_swap_for_a_dev_swap(self) -> None:
        mi = str(meminfo(self.tmp / "mi"))
        env = "HOST_ROLE=dedicated-dev\nDEV_INTERACTIVE_MEMORY_LOW=512M\nDEV_BUILDKITD_MEMORY_LOW=256M\nDEV_CACHE_MEMORY_LOW=256M\n"
        for swap_gib in (0, 8, 16):
            r = self.run_cli(env, "dedicated-memory", "--meminfo", mi, "--dev-swap", str(swap_gib * GIB))
            self.assertEqual(r.returncode, 0, r.stderr)
            out = dict(line.split("=", 1) for line in r.stdout.splitlines())
            self.assertEqual(out["DEDICATED_PROTECTED_LOW"], str(GIB))
            self.assertEqual(out["DEDICATED_LOAD_SWAP"], str(cap.load_tier_swap(swap_gib * GIB, GIB)))
            self.assertEqual(out["DEDICATED_GATES_MAX"], out["DEDICATED_MEM_MAX"])
            self.assertEqual(out["DEDICATED_GATES_HIGH"], "")
        self.assertNotIn("DEDICATED_LOAD_SWAP", self.run_cli(env, "dedicated-memory", "--meminfo", mi).stdout)

    def test_dedicated_memory_refuses_a_bad_keep_with_exit_2(self) -> None:
        r = self.run_cli("HOST_ROLE=dedicated-dev\nDEV_HOST_MEMORY_KEEP=0\n", "dedicated-memory", "--meminfo", str(meminfo(self.tmp / "mi")))
        self.assertEqual(r.returncode, 2)
        self.assertIn("DEV_HOST_MEMORY_KEEP", r.stderr)

    def test_the_ram_only_sizing_warning_is_silent_on_dedicated_dev_only(self) -> None:
        mi = str(meminfo(self.tmp / "mi"))
        big = "DEV_MEMORY_MAX=23G\n"
        shared = self.run_cli("HOST_ROLE=shared-prod\n" + big, "check-config", "--meminfo", mi, "--nproc", "4")
        dedicated = self.run_cli("HOST_ROLE=dedicated-dev\n" + big, "check-config", "--meminfo", mi, "--nproc", "4")
        self.assertIn("exceeds the RAM MDT may use", shared.stdout)
        self.assertNotIn("exceeds the RAM MDT may use", dedicated.stdout)


class PsiGuardTests(unittest.TestCase):
    def test_default_unit_list_is_the_same_in_the_guard_and_the_capacity_module(self) -> None:
        self.assertEqual(guard.DEDICATED_HOST_UNITS, cap.DEDICATED_PROTECTED_UNITS_DEFAULT)

    def test_shared_prod_and_empty_role_watch_only_the_configured_units(self) -> None:
        for role in ("", "shared-prod"):
            self.assertEqual(guard.protected_units({"HOST_ROLE": role, "MDT_PROTECTED_UNITS": "wings.slice"}), ["wings.slice"])
            self.assertEqual(guard.protected_units({"HOST_ROLE": role}), [])

    def test_dedicated_dev_adds_the_host_services_without_duplicates(self) -> None:
        units = guard.protected_units({"HOST_ROLE": "dedicated-dev", "MDT_PROTECTED_UNITS": "docker.service x.service"})
        self.assertEqual(units[:2], ["docker.service", "x.service"])
        self.assertEqual(len(units), len(set(units)))
        self.assertNotIn("ssh.service", units)
        self.assertIn("docker.service", units)
        self.assertIn("cgpd.service", units)
        self.assertIn("cgprofiled.service", units)
        self.assertEqual(guard.protected_units({"HOST_ROLE": "dedicated-dev", "DEV_HOST_PROTECTED_UNITS": "-"}), [])
        self.assertEqual(guard.protected_units({"HOST_ROLE": "dedicated-dev", "DEV_HOST_PROTECTED_UNITS": "a.service"}), ["a.service"])

    def test_the_thresholds_are_pressure_fractions_not_byte_limits(self) -> None:
        self.assertEqual({k: v[1] for k, v in guard.SIGNALS.items()}, {"unit_memory_some": 0.10, "host_memory_full": 0.05, "unit_cpu_some": 0.25})
        # stage 2 follows the tier's live memory.current; the only constant is a floor, and `max` (no tier memory.high on
        # dedicated-dev) is restored on release like any other original
        self.assertEqual(guard.MEMORY_HIGH_FRACTION, 0.9)


def shell_block(source: str, start: str, end: str) -> str:
    i = source.index(start)
    return source[i:source.index(end, i)]


class InstallBlockTests(unittest.TestCase):
    """The install.sh blocks, extracted and run with fakes."""

    PRE = shell_block(INSTALL_SH, "# HOST_ROLE=dedicated-dev memory (MDT-TESTHOST-MEM), PRE-FLIGHT",
                      'if [ -n "${DEV_SWAP_MAX:-}" ]; then\n  if ! _swap_max_bytes')
    DROPINS = shell_block(INSTALL_SH, "# HOST_ROLE=dedicated-dev host protection (MDT-TESTHOST-MEM, P-plat-19e)",
                          'if [ -n "${IO_SD_DEV:-}" ]; then\n  if [ "$(_mdt_host_role)" = dedicated-dev ]')
    SIZE_FN = shell_block(INSTALL_SH, "_size_to_bytes() {", "for _swap_var in DEV_SWAP_MAX DEV_INTERACTIVE_MEMORY_SWAP_MAX")
    OTHER_FN = shell_block(INSTALL_SH, "_other_memory_min() {", 'DEV_MEMORY_MIN=""')

    # The installer's own role detection (install.sh sets MDT_DEDICATED_MEM from _mdt_host_role), not a flag the test sets.
    ROLE_DETECT = shell_block(INSTALL_SH, "MDT_DEDICATED_MEM=0\nif [ \"$(_mdt_host_role)\" = dedicated-dev ]; then", "_host_swap_bytes=\"\"\n")

    def setUp(self) -> None:
        self.tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.bin = self.tmp / "bin"
        self.bin.mkdir()
        fake = self.bin / "systemctl"
        # `systemctl cat U` succeeds for every unit except ghost.service; OOMScoreAdjust of containerd is -999, 0 for the rest;
        fake.write_text('#!/bin/sh\ncase "$1" in\n cat) [ "$2" != ghost.service ] ;;\n'
                        ' --version) echo "systemd ${FAKE_SD_VERSION:-257} (257.9-1)" ;;\n'
                        ' show) case "$5" in containerd.service) echo -999;; *) echo 0;; esac ;;\nesac\n')
        fake.chmod(fake.stat().st_mode | stat.S_IEXEC)

    def run_pre(self, env_text: str, *, mem_gib: int = 8, swap_gib: int = 16, raise_text: str = "") -> subprocess.CompletedProcess:
        env = self.tmp / "candidate.env"
        env.write_text(env_text)
        sysd = self.tmp / "sysd"
        if raise_text:  # a foreign MemoryMin drop-in of docker.service (DI2's 90-vbpub-memory-min.conf)
            (sysd / "docker.service.d").mkdir(parents=True, exist_ok=True)
            (sysd / "docker.service.d" / "90-vbpub-memory-min.conf").write_text(raise_text)
        mi = meminfo(self.tmp / "meminfo", mem_gib, swap_gib)
        block = self.PRE.replace("/proc/meminfo", str(mi))
        script = f'''set -euo pipefail
export PATH="{self.bin}:$PATH" MDT_SYSTEMD_DIR="{sysd}"
HERE="{ROOT}"; CANDIDATE_PATH="{env}"; CONFIG_PATH=/etc/mdt/host-setup.env
. "{ROOT}/scripts/mdt-container-io-caps.lib.sh" 2>/dev/null
set -a; . "{env}"; set +a
{self.SIZE_FN}
{self.ROLE_DETECT}
{block}
echo "HIGH=${{DEV_MEMORY_HIGH:-}} MAX=${{DEV_MEMORY_MAX:-}} MIN=${{DEV_MEMORY_MIN:-}} IMIN=${{DEV_INTERACTIVE_MEMORY_MIN:-}} LOW=${{DEV_MEMORY_LOW:-}} GATES_HIGH=${{DEV_GATES_MEMORY_HIGH:-}} GATES_MAX=${{DEV_GATES_MEMORY_MAX:-}} BK_MAX=${{DEV_BUILDKITD_MEMORY_MAX:-}} SCOPE=${{DOCKER_SCOPE_BACKSTOP_MEMORY_MAX:-}} FLOORS=$MDT_FLOOR_UNITS SYSMIN=$MDT_SYSTEM_SLICE_MIN SYSLOW=$MDT_SYSTEM_SLICE_LOW KEEP=$MDT_DEDICATED_KEEP"
'''
        return subprocess.run(["bash", "-c", script], capture_output=True, text=True)

    def test_dedicated_dev_derives_dev_slice_without_a_high_and_drops_every_tier_ceiling(self) -> None:
        r = self.run_pre('HOST_ROLE=dedicated-dev\nDEV_GATES_MEMORY_HIGH=4G\nDEV_GATES_MEMORY_MAX=5.5G\nDEV_BUILDKITD_MEMORY_MAX=2G\n'
                         'DEV_INTERACTIVE_MEMORY_LOW=512M\nDEV_MEMORY_HIGH=7G\n')
        self.assertEqual(r.returncode, 0, r.stderr)
        # the gates MAX pin wins; HIGH pins render nothing; buildkitd is ignored and named
        self.assertIn(f"HIGH= MAX={8 * GIB - K} MIN={512 * MIB} IMIN={512 * MIB} LOW={512 * MIB} GATES_HIGH= GATES_MAX={int(5.5 * GIB)} BK_MAX= ", r.stdout)
        self.assertIn(f"KEEP={K}", r.stdout)
        self.assertIn("ignores the per-tier memory ceilings", r.stderr)
        self.assertIn("DEV_BUILDKITD_MEMORY_MAX=2G", r.stderr)
        self.assertIn("renders no MemoryHigh anywhere", r.stderr)
        self.assertIn("DEV_MEMORY_HIGH=7G", r.stderr)
        self.assertIn("DEV_GATES_MEMORY_HIGH=4G", r.stderr)

    def test_gates_and_background_are_derived_equal_to_dev_max_despite_the_protected_lows(self) -> None:
        r = self.run_pre("HOST_ROLE=dedicated-dev\nDEV_INTERACTIVE_MEMORY_LOW=512M\nDEV_BUILDKITD_MEMORY_LOW=256M\n")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn(f"GATES_HIGH= GATES_MAX={8 * GIB - K} ", r.stdout)

    def test_floors_sum_slices_and_a_foreign_raise_reaches_the_system_slice(self) -> None:
        plain = self.run_pre("HOST_ROLE=dedicated-dev\n")
        self.assertEqual(plain.returncode, 0, plain.stderr)
        self.assertIn(f"docker.service={210 * MIB}", plain.stdout)
        self.assertIn(f"SYSMIN={373 * MIB} SYSLOW={373 * MIB} ", plain.stdout)
        raised = self.run_pre("HOST_ROLE=dedicated-dev\n", raise_text="[Service]\nMemoryMin=250M\n")
        self.assertEqual(raised.returncode, 0, raised.stderr)
        self.assertIn(f"docker.service={250 * MIB}", raised.stdout)
        self.assertIn(f"SYSMIN={413 * MIB} ", raised.stdout)
        lower = self.run_pre("HOST_ROLE=dedicated-dev\n", raise_text="[Service]\nMemoryMin=100M\n")
        self.assertIn(f"docker.service={210 * MIB}", lower.stdout)  # raise-only: a lower foreign value changes nothing

    def test_a_keep_pin_prints_its_new_meaning_and_lifts_the_system_slice_low(self) -> None:
        r = self.run_pre("HOST_ROLE=dedicated-dev\nDEV_HOST_MEMORY_KEEP=1280M\n")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("now PINS the host keep K", r.stderr)
        self.assertIn(f"KEEP={1280 * MIB}", r.stdout)
        self.assertIn(f"SYSLOW={576 * MIB} ", r.stdout)

    def test_the_container_scope_default_is_8g_on_dedicated_dev_12g_elsewhere_and_an_explicit_value_wins(self) -> None:
        self.assertIn("SCOPE=8G", self.run_pre("HOST_ROLE=dedicated-dev\n").stdout)
        self.assertIn("SCOPE=6G", self.run_pre("HOST_ROLE=dedicated-dev\nDOCKER_SCOPE_BACKSTOP_MEMORY_MAX=6G\n").stdout)
        for role in ("", "HOST_ROLE=shared-prod\n"):
            self.assertIn("SCOPE=12G", self.run_pre(role).stdout, role)

    def test_the_install_swap_loop_uses_the_one_python_derivation_for_the_load_tiers_on_dedicated_dev_only(self) -> None:
        self.assertIn("DEV_GATES_MEMORY_SWAP_MAX|DEV_BACKGROUND_MEMORY_SWAP_MAX)", INSTALL_SH)
        self.assertIn("--dev-swap \"$DEV_SWAP_MAX\"", INSTALL_SH)
        self.assertIn('[ "$MDT_DEDICATED_MEM" = 1 ]', INSTALL_SH)
        # an explicit value (pin) never reaches the derivation: the branch is inside the `-z ${!_swap_var}` condition
        self.assertIn('if [ -z "${!_swap_var:-}" ] && [ "${!_unset_var:-0}" != 1 ] && [ -n "${DEV_SWAP_MAX:-}" ]; then\n      case', INSTALL_SH)

    def test_dev_slice_renders_the_child_min_sum_on_dedicated_dev_through_the_guaranteed_ceiling_variable(self) -> None:
        self.assertIn('DEV_MEMORY_MIN_GUARANTEED_CEILING="$DEV_MEMORY_MIN"\n  render "$HERE/units/dev.slice.in"', INSTALL_SH)
        self.assertIn("MemoryMin=@DEV_MEMORY_MIN_GUARANTEED_CEILING@", (ROOT / "units" / "dev.slice.in").read_text())

    def test_the_cockpit_score_is_published_on_dedicated_dev_only_and_consumed_by_the_three_devcontainer_files(self) -> None:
        self.assertIn('if [ "$MDT_DEDICATED_MEM" = 1 ]; then export MDT_COCKPIT_OOM_SCORE_ADJ_PUBLISH=-900; fi', INSTALL_SH)
        endpoints = (ROOT / "scripts" / "mdt-host-endpoints.sh").read_text()
        self.assertIn('[ -z "${MDT_COCKPIT_OOM_SCORE_ADJ_PUBLISH:-}" ] || cockpit_oom_entry="MDT_COCKPIT_OOM_SCORE_ADJ=', endpoints)
        for rel in (".devcontainer/devcontainer.json", "mdt-cockpit/templates/devcontainer.json", "mdt-cockpit/templates/host-folder/devcontainer.json"):
            text = (ROOT.parents[1] / rel).read_text()
            self.assertIn('"--oom-score-adj", "${localEnv:MDT_COCKPIT_OOM_SCORE_ADJ:0}",', text, rel)

    def test_a_refused_derivation_exits_2_before_anything_is_changed(self) -> None:
        r = self.run_pre("HOST_ROLE=dedicated-dev\nDEV_HOST_MEMORY_KEEP=0\n")
        self.assertNotEqual(r.returncode, 0)
        self.assertIn("cannot be derived", r.stderr)

    def test_shared_prod_block_is_inert(self) -> None:
        # role detection is the installer's (HOST_ROLE through _mdt_host_role); nothing here sets MDT_DEDICATED_MEM
        for role in ("", "HOST_ROLE=shared-prod\n"):
            with self.subTest(role=role):
                r = self.run_pre(role + "DEV_MEMORY_MAX=7.5G\nDEV_GATES_MEMORY_MAX=5.5G\n")
                self.assertEqual(r.returncode, 0, r.stderr)
                self.assertIn("MAX=7.5G", r.stdout)
                self.assertIn("GATES_MAX=5.5G", r.stdout)
                self.assertTrue(r.stdout.rstrip().endswith("KEEP="), r.stdout)
                self.assertNotIn("HOST_ROLE=dedicated-dev memory", r.stdout)

    RENDER_VARS = re.search(r'RENDER_VARS="([^"]*)"', INSTALL_SH).group(1).replace("\\\n", " ").split()

    def render_all(self, env_text: str, with_block: bool) -> dict[str, str]:
        """Render every slice/scope template with install.sh's own render() semantics, optionally after the pre-flight block."""
        env = self.tmp / "candidate.env"
        env.write_text(env_text)
        mi = meminfo(self.tmp / "meminfo")
        block = self.PRE.replace("/proc/meminfo", str(mi)) if with_block else ""
        out = self.tmp / ("with" if with_block else "without")
        out.mkdir(exist_ok=True)
        templates = sorted((ROOT / "units").glob("*.in"))
        script = f'''set -euo pipefail
export PATH="{self.bin}:$PATH"
HERE="{ROOT}"; CANDIDATE_PATH="{env}"; CONFIG_PATH=/etc/mdt/host-setup.env
. "{ROOT}/scripts/mdt-container-io-caps.lib.sh" 2>/dev/null
set -a; . "{env}"; set +a
DEV_SWAP_CASCADE_PCT=80
{self.SIZE_FN}
{self.ROLE_DETECT}
{block}
RENDER_VARS="{' '.join(self.RENDER_VARS)}"
for src in {' '.join(str(t) for t in templates)}; do
  args=(); for v in $RENDER_VARS; do args+=(-e "s|@$v@|${{!v:-}}|g"); done
  dst="{out}/$(basename "$src")"
  sed "${{args[@]}}" "$src" > "$dst"; sed -i '/^[A-Za-z][A-Za-z0-9]*=$/d' "$dst"
done
echo "CASCADE=$DEV_SWAP_CASCADE_PCT"
'''
        r = subprocess.run(["bash", "-c", script], capture_output=True, text=True)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("CASCADE=100" if "dedicated-dev" in env_text else "CASCADE=80", r.stdout)
        return {f.name: f.read_text() for f in out.iterdir()}

    def test_shared_prod_and_empty_role_render_byte_identical_units_with_and_without_the_new_block(self) -> None:
        # a real installed config carries DOCKER_SCOPE_BACKSTOP_MEMORY_MAX=12G (the empty key now renders the role default)
        env = ("DEV_MEMORY_MAX=7.5G\nDEV_MEMORY_HIGH=7G\nDEV_MEMORY_LOW=1G\nDEV_GATES_MEMORY_HIGH=4G\nDEV_GATES_MEMORY_MAX=5.5G\n"
               "DEV_INTERACTIVE_MEMORY_MAX=4.5G\nDEV_BUILDKITD_MEMORY_MAX=2.5G\nDEV_BUILDKITD_MEMORY_LOW=256M\n"
               "DOCKER_SCOPE_BACKSTOP_MEMORY_MAX=12G\n")
        for role in ("", "HOST_ROLE=shared-prod\n"):
            with self.subTest(role=role):
                before = self.render_all(role + env, with_block=False)
                after = self.render_all(role + env, with_block=True)
                self.assertEqual(before, after)
                self.assertIn("MemoryMax=7.5G", before["dev.slice.in"])
                self.assertIn("MemoryMax=12G", before["docker-scope-default-limits.conf.in"])

    def test_dedicated_dev_renders_differently_so_the_identity_check_is_not_vacuous(self) -> None:
        env = "HOST_ROLE=dedicated-dev\nDEV_MEMORY_MAX=6G\nDEV_INTERACTIVE_MEMORY_MAX=4.5G\n"
        self.assertNotEqual(self.render_all(env, with_block=False), self.render_all(env, with_block=True))

    def test_dedicated_dev_keeps_a_finite_memory_max_on_gates_and_background_and_no_high_anywhere(self) -> None:
        out = self.render_all("HOST_ROLE=dedicated-dev\nDEV_INTERACTIVE_MEMORY_LOW=512M\n", with_block=True)
        for name in ("dev-gates.slice.in", "dev-background.slice.in"):
            self.assertRegex(out[name], r"(?m)^MemoryMax=[0-9]", name)  # an omitted or infinite cap would be the oomd-era hole
        for name in ("dev.slice.in", "dev-gates.slice.in", "dev-background.slice.in", "dev-interactive.slice.in", "dev-buildkitd.slice.in"):
            self.assertNotRegex(out[name], r"(?m)^MemoryHigh=", name)  # P-plat-19e: a High throttles while RAM is free
        self.assertRegex(out["dev-interactive.slice.in"], r"(?m)^MemoryMin=536870912$")  # the cockpit floor (512M default)
        self.assertRegex(out["docker-scope-default-limits.conf.in"], r"(?m)^MemoryMax=8G$")
        # operator decision 2026-11-24: interactive and buildkitd are bounded only by dev.slice (no own MemoryMax)
        for name in ("dev-interactive.slice.in", "dev-buildkitd.slice.in"):
            self.assertNotRegex(out[name], r"(?m)^MemoryMax=", name)
        for name, text in out.items():
            self.assertNotRegex(text, r"(?m)^ManagedOOM", name)
            self.assertNotRegex(text, r"(?m)^Memory(Swap)?Max=infinity", name)

    def run_dropins(self, dedicated: bool, units: str = "docker.service containerd.service ghost.service", *, low: int = 391118848,
                    floors: str = "docker.service=220200960 ssh.service=10485760 ghost.service=1048576 containerd.service=96468992 cgprofiled.service=4194304",
                    sd_version: str = "257") -> tuple[subprocess.CompletedProcess, Path]:
        sysdir = self.tmp / "systemd"
        sysdir.mkdir(exist_ok=True)
        script = f'''set -euo pipefail
export PATH="{self.bin}:$PATH" FAKE_SD_VERSION={sd_version}
{self.SIZE_FN}
{self.OTHER_FN}
MDT_SYSTEMD_DIR="{sysdir}"
MDT_DEDICATED_MEM={1 if dedicated else 0}
MDT_DEDICATED_KEEP=1129316352
MDT_SYSTEM_SLICE_MIN=391118848; MDT_SYSTEM_SLICE_LOW={low}; MDT_USER_SLICE_MIN=67108864
MDT_FLOOR_UNITS="{floors if dedicated else ''}"
MDT_DEDICATED_PROTECTED_UNITS="{units}"
{self.DROPINS}
echo "LIVE=$MDT_DED_LIVE_UNITS"
'''
        return subprocess.run(["bash", "-c", script], capture_output=True, text=True), sysdir

    def test_dedicated_dev_writes_the_dropins_and_never_weakens_a_stronger_one(self) -> None:
        r, sysdir = self.run_dropins(True)
        self.assertEqual(r.returncode, 0, r.stderr)
        # at the defaults the system.slice Low equals its Min: the line is omitted
        self.assertEqual((sysdir / "system.slice.d/50-mdt-dedicated-dev.conf").read_text().splitlines()[1:], ["[Slice]"])
        self.assertEqual((sysdir / "docker.service.d/50-mdt-dedicated-dev.conf").read_text().splitlines()[-2:], ["[Service]", "OOMScoreAdjust=-900"])
        self.assertFalse((sysdir / "containerd.service.d/50-mdt-dedicated-dev.conf").exists(), "containerd already has -999")
        self.assertFalse((sysdir / "ghost.service.d").exists(), "a unit that is not installed is skipped")
        self.assertIn("LIVE= docker.service", r.stdout)
        for f in (sysdir / "docker.service.d/50-mdt-dedicated-dev.conf", sysdir / "system.slice.d/50-mdt-dedicated-dev.conf",
                  sysdir / "docker.service.d/95-mdt-memory-min.conf", sysdir / "docker-.scope.d/60-mdt-dedicated-dev.conf"):
            self.assertEqual(stat.S_IMODE(f.stat().st_mode), 0o644, f)

    def test_the_floors_are_written_per_unit_ssh_is_floored_but_never_scored_an_absent_unit_is_skipped(self) -> None:
        r, sysdir = self.run_dropins(True)
        self.assertEqual(r.returncode, 0, r.stderr)
        for rel, section, value in (("system.slice.d", "Slice", 391118848), ("user.slice.d", "Slice", 67108864),
                                    ("docker.service.d", "Service", 220200960), ("ssh.service.d", "Service", 10485760),
                                    ("containerd.service.d", "Service", 96468992), ("cgprofiled.service.d", "Service", 4194304)):
            self.assertEqual((sysdir / rel / "95-mdt-memory-min.conf").read_text().splitlines()[-2:], [f"[{section}]", f"MemoryMin={value}"], rel)
        self.assertFalse((sysdir / "ssh.service.d/50-mdt-dedicated-dev.conf").exists())
        self.assertIn("ghost.service is not installed", r.stdout)

    def test_a_keep_pin_renders_a_system_slice_low_above_the_min_and_the_scope_drop_in_needs_systemd_253(self) -> None:
        r, sysdir = self.run_dropins(True, low=603979776)
        self.assertEqual((sysdir / "system.slice.d/50-mdt-dedicated-dev.conf").read_text().splitlines()[-2:], ["[Slice]", "MemoryLow=603979776"])
        self.assertEqual((sysdir / "docker-.scope.d/60-mdt-dedicated-dev.conf").read_text().splitlines()[-2:], ["[Scope]", "OOMPolicy=kill"])
        r, sysdir = self.run_dropins(True, sd_version="252")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertFalse((sysdir / "docker-.scope.d").exists())
        self.assertIn("older than 253", r.stderr)

    def test_a_stale_ssh_dropin_from_an_earlier_version_is_removed_and_nothing_else(self) -> None:
        sysdir = self.tmp / "systemd"
        (sysdir / "ssh.service.d").mkdir(parents=True)
        stale = sysdir / "ssh.service.d/50-mdt-dedicated-dev.conf"
        stale.write_text("[Service]\nOOMScoreAdjust=-900\n")
        operator = sysdir / "ssh.service.d/10-operator.conf"
        operator.write_text("[Service]\nNice=5\n")
        r, _ = self.run_dropins(True)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertFalse(stale.exists())
        self.assertTrue(operator.exists())
        self.assertIn("removed", r.stdout)
        self.assertTrue((sysdir / "system.slice.d/50-mdt-dedicated-dev.conf").exists(), "the role's own system.slice file stays")
        self.assertTrue((sysdir / "docker.service.d/50-mdt-dedicated-dev.conf").exists())

    def test_narrowing_the_protected_list_or_the_floors_removes_the_files_of_units_that_left(self) -> None:
        _, sysdir = self.run_dropins(True, "docker.service cgprofiled.service")
        self.assertTrue((sysdir / "cgprofiled.service.d/50-mdt-dedicated-dev.conf").exists())
        r, _ = self.run_dropins(True, "docker.service")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertFalse((sysdir / "cgprofiled.service.d/50-mdt-dedicated-dev.conf").exists())
        self.assertTrue((sysdir / "docker.service.d/50-mdt-dedicated-dev.conf").exists())
        r, _ = self.run_dropins(True, "")
        self.assertFalse((sysdir / "docker.service.d/50-mdt-dedicated-dev.conf").exists())
        self.assertTrue((sysdir / "system.slice.d/50-mdt-dedicated-dev.conf").exists())
        self.assertTrue((sysdir / "ssh.service.d/95-mdt-memory-min.conf").exists())
        r, _ = self.run_dropins(True, "", floors="docker.service=220200960")
        self.assertFalse((sysdir / "ssh.service.d/95-mdt-memory-min.conf").exists())  # ssh left the floor table
        self.assertTrue((sysdir / "docker.service.d/95-mdt-memory-min.conf").exists())

    def test_a_second_run_rewrites_instead_of_deciding_from_its_own_dropin(self) -> None:
        self.run_dropins(True)
        r, sysdir = self.run_dropins(True)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertTrue((sysdir / "docker.service.d/50-mdt-dedicated-dev.conf").exists())

    def test_leaving_the_role_removes_exactly_the_files_it_owns(self) -> None:
        _, sysdir = self.run_dropins(True)
        other = sysdir / "docker.service.d" / "10-operator.conf"
        other.write_text("[Service]\nNice=5\n")
        r, _ = self.run_dropins(False)
        self.assertEqual(r.returncode, 0, r.stderr)
        for gone in ("system.slice.d", "user.slice.d", "docker-.scope.d", "ssh.service.d", "cgprofiled.service.d"):
            self.assertFalse((sysdir / gone).exists(), gone)
        self.assertFalse((sysdir / "docker.service.d/50-mdt-dedicated-dev.conf").exists())
        self.assertFalse((sysdir / "docker.service.d/95-mdt-memory-min.conf").exists())
        self.assertTrue(other.exists(), "an operator's own drop-in is never touched")


class ConvergeTests(unittest.TestCase):
    """mdt-dev-governance-reconcile.sh's memory converge (design 6.2): the real function over a fake cgroupfs."""

    RECONCILE = (ROOT / "scripts" / "mdt-dev-governance-reconcile.sh").read_text(encoding="utf-8")
    FN = shell_block(RECONCILE, "_mdt_converge_prop() {", 'if [ "${HOST_ROLE:-}" = dedicated-dev ]; then\n  rm -f')

    def setUp(self) -> None:
        self.tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.cg = self.tmp / "cg"
        (self.cg / "dev.slice").mkdir(parents=True)
        self.props = self.tmp / "props"
        self.props.mkdir()
        self.calls = self.tmp / "calls"
        bin_dir = self.tmp / "bin"
        bin_dir.mkdir()
        fake = bin_dir / "systemctl"
        fake.write_text(f'#!/bin/sh\necho "$@" >> "{self.calls}"\n[ "$1" = show ] && cat "{self.props}/$5.$3" 2>/dev/null\nexit 0\n')
        fake.chmod(fake.stat().st_mode | stat.S_IEXEC)
        self.bin = bin_dir

    def cgfile(self, name: str, value: str) -> Path:
        path = self.cg / "dev.slice" / name
        path.write_text(value + "\n")
        return path

    def converge(self, prop: str, want: str, file: str, guard_owned: str = "") -> subprocess.CompletedProcess:
        (self.props / f"dev.slice.{prop}").write_text(want + "\n")
        script = f'''export PATH="{self.bin}:$PATH"; CG="{self.cg}"; MDT_RC=0; _guard_owned="{guard_owned}"
_cv_dir="{self.tmp}/reconcile"
log(){{ echo "$*"; }}
{self.FN}
_mdt_converge_prop dev.slice "$CG/dev.slice" {prop} {file}
echo "RC=$MDT_RC"
'''
        return subprocess.run(["bash", "-c", script], capture_output=True, text=True)

    def test_a_difference_is_written_directly_and_read_back_never_through_set_property(self) -> None:
        f = self.cgfile("memory.min", "0")
        r = self.converge("MemoryMin", "536870912", "memory.min")
        self.assertEqual(f.read_text().strip(), "536870912")
        self.assertIn("out of step", r.stdout)
        self.assertIn("RC=0", r.stdout)
        self.assertNotIn("set-property", self.calls.read_text())
        self.assertNotIn("set-property", self.FN)

    def test_equal_values_and_systemd_infinity_cause_no_write(self) -> None:
        f = self.cgfile("memory.max", "max")
        self.assertNotIn("out of step", self.converge("MemoryMax", "infinity", "memory.max").stdout)
        self.assertEqual(f.read_text().strip(), "max")

    def test_raising_max_applies_directly(self) -> None:
        f = self.cgfile("memory.max", "1000000000")
        self.cgfile("memory.current", "999999999")
        self.converge("MemoryMax", "2000000000", "memory.max")
        self.assertEqual(f.read_text().strip(), "2000000000")

    def test_lowering_max_writes_only_with_64m_of_headroom_else_defers_and_reclaims(self) -> None:
        f = self.cgfile("memory.max", "7191363584")
        self.cgfile("memory.current", str(GIB))
        self.cgfile("memory.reclaim", "")
        self.converge("MemoryMax", "6684901376", "memory.max")
        self.assertEqual(f.read_text().strip(), "6684901376")
        g = self.cgfile("memory.max", "max")  # from unlimited to a finite value is a lowering too
        self.cgfile("memory.current", "4500000000")  # above target - 64M
        r = self.converge("MemoryMax", "6684901376", "memory.max")
        self.assertEqual(g.read_text().strip(), "max")
        self.assertIn("deferred", r.stdout)
        self.assertIn("dev.slice 6684901376", (self.tmp / "reconcile" / "memory-deferred").read_text())

    def test_a_deferred_dev_slice_lowering_reclaims_the_load_tiers_never_dev_slice_itself(self) -> None:
        # P-plat-19e review F3: the cockpit lives in dev.slice; only dev-gates and dev-background are reclaimed
        self.cgfile("memory.max", "7191363584")
        self.cgfile("memory.current", "7000000000")  # above target - 64M, the fake never shrinks
        dev_reclaim = self.cgfile("memory.reclaim", "")
        tiers = []
        for tier in ("dev-gates.slice", "dev-background.slice"):
            (self.cg / "dev.slice" / tier).mkdir()
            tiers.append((self.cg / "dev.slice" / tier / "memory.reclaim"))
            tiers[-1].write_text("")
        r = self.converge("MemoryMax", "6684901376", "memory.max")
        self.assertIn("deferred", r.stdout)
        self.assertEqual(dev_reclaim.read_text().strip(), "")  # the old code wrote 256M here
        for f in tiers:
            self.assertEqual(f.read_text().strip(), "256M", f)

    def test_a_key_psi_guard_owns_is_left_alone(self) -> None:
        f = self.cgfile("memory.high", "123456789")
        r = self.converge("MemoryHigh", "max", "memory.high", guard_owned="dev.slice/memory.high")
        self.assertEqual(f.read_text().strip(), "123456789")
        self.assertIn("owned by psi-guard", r.stdout)


class ActivateLoopTests(unittest.TestCase):
    """The in-place oom_score_adj lowering of the activate section (running services are never restarted)."""

    LOOP = shell_block(INSTALL_SH, "for _ded_unit in $MDT_DED_LIVE_UNITS; do\n  _ded_pid=", "# Slices activate on demand")

    def run_loop(self, pid: str, units: str = "docker.service") -> subprocess.CompletedProcess:
        tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, tmp, True)
        fake = tmp / "systemctl"
        fake.write_text(f'#!/bin/sh\n[ "$1" = show ] && echo "{pid}"\n')
        fake.chmod(fake.stat().st_mode | stat.S_IEXEC)
        script = f'export PATH="{tmp}:$PATH"\nMDT_DED_LIVE_UNITS="{units}"\n{self.LOOP}\n'
        return subprocess.run(["bash", "-c", script], capture_output=True, text=True)

    def test_a_running_main_process_is_lowered_in_place_or_reported_never_ignored(self) -> None:
        child = subprocess.Popen(["sleep", "30"])
        self.addCleanup(child.kill)
        before = Path(f"/proc/{child.pid}/oom_score_adj").read_text().strip()
        r = self.run_loop(str(child.pid))
        self.assertEqual(r.returncode, 0, r.stderr)
        after = Path(f"/proc/{child.pid}/oom_score_adj").read_text().strip()
        if os.geteuid() == 0:
            self.assertEqual(after, "-900")
            self.assertIn(f"oom_score_adj {before} -> -900", r.stdout)
        else:
            self.assertEqual(after, before, "without CAP_SYS_RESOURCE the score must stay as it was")
            self.assertIn("WARN: could not lower oom_score_adj", r.stdout)

    def test_no_main_pid_means_nothing_is_touched(self) -> None:
        for pid in ("0", "", "garbage"):
            with self.subTest(pid=pid):
                r = self.run_loop(pid)
                self.assertEqual(r.returncode, 0, r.stderr)
                self.assertEqual(r.stdout.strip(), "")


class CheckShTests(unittest.TestCase):
    def test_check_sh_memory_block_is_role_gated_and_verifies_floors_overrides_and_the_cockpit_score(self) -> None:
        block = shell_block(CHECK_SH, "HOST_ROLE=dedicated-dev (MDT-TESTHOST-MEM): the derived values", "# dev.slice's own MemoryZSwapWriteback")
        self.assertIn('[ "$(_mdt_host_role)" = dedicated-dev ]', CHECK_SH)
        for needle in ("DEDICATED_FLOOR", "DEDICATED_SYSTEM_SLICE_MIN", "DropInPaths", "memory-deferred", "run mdt-dev-governance-reconcile.sh",
                       "inactive_file", "oom_score_adj", "rebuild the devcontainer", "foreign memory drop-in", "20-memory-policy.conf",
                       "provision-gates-limits.sh", "DEV_HOST_MEMORY_MEASURED", "owned by psi-guard"):
            self.assertIn(needle, block, needle)
        # runtime/hand overrides of memory or oomd keys: FAIL on dedicated-dev, WARN elsewhere (outside the role block)
        self.assertIn('_ovr=fail; [ "$(_mdt_host_role)" = dedicated-dev ] || _ovr=warn', CHECK_SH)
        self.assertIn("/run/systemd/system.control /etc/systemd/system.control", CHECK_SH)
        self.assertNotIn("exceeds DEV_HOST_MEMORY_KEEP", CHECK_SH)
        # the whole script is still syntactically valid shell
        self.assertEqual(subprocess.run(["bash", "-n", str(ROOT / "scripts" / "check.sh")], capture_output=True, text=True).returncode, 0)

    def test_the_wizard_does_not_warn_about_a_missing_dev_memory_low_when_dedicated_dev_derives_it(self) -> None:
        wiz = _load("mdt_wizard_mem_t", ROOT / "mdt-host-setup-wizard.py")
        values = {"DEV_INTERACTIVE_MEMORY_LOW": "512M", "DEV_MEMORY_LOW": ""}
        self.assertTrue(wiz.memory_review_warnings(dict(values, HOST_ROLE="shared-prod")))
        self.assertEqual(wiz.memory_review_warnings(dict(values, HOST_ROLE="dedicated-dev")), [])
        # an explicit omission is still worth naming
        self.assertTrue(wiz.memory_review_warnings(dict(values, HOST_ROLE="dedicated-dev", DEV_MEMORY_LOW="-")))
        # the empty role default of the scope backstop and of the KEEP pin pass the wizard's validators
        validators = wiz._config_value_validators()
        for key in ("DOCKER_SCOPE_BACKSTOP_MEMORY_MAX", "DEV_HOST_MEMORY_KEEP", "DEV_HOST_MEMORY_MIN", "DEV_HOST_MEMORY_MARGIN"):
            self.assertIsNone(validators[key](""), key)


class TemplateAndDocsTests(unittest.TestCase):
    def test_dev_tiers_document_no_systemd_oomd_kill(self) -> None:
        readme = " ".join((ROOT / "README.md").read_text().lower().split())
        notes = " ".join((ROOT / "CGROUP-NOTES.md").read_text().lower().split())
        for phrase in ("full avg10 >= 3", "fleetd", "no dev tier opts into systemd-oomd"):
            self.assertIn(phrase, readme, f"README does not document {phrase!r}")
        for phrase in ("finite `memorymax`", "large `memoryswapmax`", "overcommit design", "reclaims", "operator directive 2026-11-24",
                       "full avg10 >= 3", "fleetd"):
            self.assertIn(phrase, notes, f"CGROUP-NOTES does not document {phrase!r}")
        for text, name in ((readme, "README"), (notes, "CGROUP-NOTES"), (EXAMPLE.read_text(), "env example")):
            self.assertNotIn("MDT_MEMORY_POLICY", text, name)

    def test_the_no_own_cap_decision_for_interactive_and_buildkitd_is_documented(self) -> None:
        notes = " ".join((ROOT / "CGROUP-NOTES.md").read_text().split())
        self.assertIn("Operator decision 2026-11-24", notes)
        self.assertIn("`dev-interactive.slice` and `dev-buildkitd.slice` are intentionally bounded", notes)
        self.assertIn("`dev-interactive` / `dev-buildkitd` | per tier keys | no ceiling of their own", " ".join((ROOT / "README.md").read_text().split()))

    def test_the_dedicated_role_blanks_tier_ceilings_that_shared_prod_still_renders(self) -> None:
        for tier in ("interactive", "background", "gates", "buildkitd"):
            text = (ROOT / "units" / f"dev-{tier}.slice.in").read_text()
            self.assertIn(f"MemoryMax=@DEV_{tier.upper()}_MEMORY_MAX@", text)

    def test_the_keys_are_declared_once_with_an_allowed_line_and_are_runtime_optional(self) -> None:
        text = EXAMPLE.read_text()
        for key in ("DEV_HOST_MEMORY_KEEP", "DEV_HOST_MEMORY_MIN", "DEV_HOST_MEMORY_MARGIN", "DEV_HOST_MEMORY_MEASURED", "DEV_HOST_PROTECTED_UNITS"):
            self.assertEqual(len(re.findall(rf"(?m)^{key}=", text)), 1, key)
            self.assertRegex(text, rf"(?m)^# Allowed [^\n]*\b{key}\b")
            self.assertTrue(re.compile((ROOT / "runtime-optional-keys.re").read_text().strip().splitlines()[-1]).fullmatch(key), key)
        self.assertRegex(text, r"(?m)^DEV_HOST_MEMORY_KEEP=$")  # empty: K is derived, KEEP only pins it
        self.assertRegex(text, r"(?m)^DOCKER_SCOPE_BACKSTOP_MEMORY_MAX=$")  # empty: 8G on dedicated-dev, 12G elsewhere (install.sh)
        self.assertFalse(hasattr(cap, "DEDICATED_MEMORY_KEEP_DEFAULT"))

    def test_check_sh_expects_no_tier_ceiling_on_dedicated_dev(self) -> None:
        # buildkitd still has none; gates/background are verified against the derivation (= dev.slice's Max since P-plat-19h)
        self.assertEqual(CHECK_SH.count("HOST_ROLE=dedicated-dev means no per-tier memory ceiling"), 1)
        self.assertIn("dedicated-memory", CHECK_SH)
        self.assertIn("DEDICATED_${_tier_key}_", CHECK_SH)
        self.assertNotIn("is below dev.slice memory.high", CHECK_SH)  # no MemoryHigh to compare with
        # AD2-0: swap derivation checked too
        self.assertIn("minus the protected tiers' MemoryLow sum (AD2-0)", CHECK_SH)
        self.assertIn("--dev-swap", CHECK_SH)
        self.assertIn("DEV_CACHE_MEMORY_LOW", EXAMPLE.read_text())

    def test_no_text_calls_memory_max_a_leak_backstop_and_the_bounds_are_documented_honestly(self) -> None:
        readme = (ROOT / "README.md").read_text()
        example = EXAMPLE.read_text()
        for name, text in (("README", readme), ("env example", example), ("capacity", CAPACITY.read_text())):
            for m in re.finditer(r"leak backstop", text):
                self.assertRegex(text[max(0, m.start() - 40):m.start()], r"(NOT|no|not)\W+(a\W+)?$", f"{name}: affirmative leak-backstop claim")
        for needle in ("io.max", "never ssh", "at least 256M", "set-property", "DEV_HOST_MEMORY_MARGIN", "OOMPolicy=kill", "oom-score-adj"):
            self.assertIn(needle, readme + example, needle)
        self.assertNotIn("ssh.service docker.service", readme + example)

    def test_the_design_note_ships_with_the_implementation(self) -> None:
        self.assertTrue((ROOT / "docs" / "DESIGN-DEDICATED-DEV-MEMORY.md").is_file())


if __name__ == "__main__":
    unittest.main()
