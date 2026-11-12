"""MDT-TESTHOST-MEM: the memory half of HOST_ROLE=dedicated-dev.  Hermetic: fake meminfo, fake systemctl, tmp dirs.

shared-prod (and an empty role) must stay exactly as it was; dedicated-dev derives dev.slice's limits from
RAM + swap minus DEV_HOST_MEMORY_KEEP, renders no per-tier ceiling and protects the host's own services."""
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


class DerivationTests(unittest.TestCase):
    def test_default_keep_is_1g_and_the_limits_follow_the_operator_formula(self) -> None:
        m = cap.dedicated_memory({}, **HOST)
        self.assertEqual(m.keep, GIB)  # covers the measured 1.0-1.1 GB system.slice working set
        self.assertEqual(m.maximum, 8 * GIB + 16 * GIB - GIB)  # RAM + swap - keep
        self.assertEqual(m.high, 8 * GIB - GIB)  # the RAM the dev tier may hold
        self.assertEqual((m.high_source, m.max_source), ("derived", "derived"))
        self.assertIsNone(m.low)

    def test_keep_is_configurable_and_excluded_capacity_is_subtracted(self) -> None:
        m = cap.dedicated_memory({"DEV_HOST_MEMORY_KEEP": "1G", "MDT_EXCLUDED_RAM": "1G", "MDT_EXCLUDED_SWAP": "2G"}, **HOST)
        self.assertEqual(m.maximum, (8 - 1) * GIB + (16 - 2) * GIB - GIB)
        self.assertEqual(m.high, (8 - 1) * GIB - GIB)

    def test_pins_win_per_value_and_a_dash_omits(self) -> None:
        m = cap.dedicated_memory({"DEV_MEMORY_HIGH": "6G", "DEV_MEMORY_MAX": "-"}, **HOST)
        self.assertEqual(m.high, 6 * GIB)
        self.assertIsNone(m.maximum)
        self.assertIn("pin", m.high_source)

    def test_dev_low_is_the_sum_of_the_tier_lows_unless_pinned(self) -> None:
        values = {"DEV_INTERACTIVE_MEMORY_LOW": "512M", "DEV_BUILDKITD_MEMORY_LOW": "256M", "DEV_GATES_MEMORY_LOW": "-"}
        self.assertEqual(cap.dedicated_memory(values, **HOST).low, 768 * MIB)
        self.assertEqual(cap.dedicated_memory({**values, "DEV_MEMORY_LOW": "1G"}, **HOST).low, GIB)

    def test_refusals(self) -> None:
        cases = {
            "keep 0": ({"DEV_HOST_MEMORY_KEEP": "0"}, "DEV_HOST_MEMORY_KEEP"),
            "keep 1 byte": ({"DEV_HOST_MEMORY_KEEP": "1"}, "at least 256M"),
            "keep 255M": ({"DEV_HOST_MEMORY_KEEP": "255M"}, "at least 256M"),
            "high pin 100 bytes": ({"DEV_MEMORY_HIGH": "100"}, "stall"),
            "max pin 128M": ({"DEV_MEMORY_MAX": "128M"}, "stall"),
            "pin junk": ({"DEV_MEMORY_MAX": "lots"}, "not a size"),
            "keep junk": ({"DEV_HOST_MEMORY_KEEP": "lots"}, "DEV_HOST_MEMORY_KEEP"),
            "keep leaves < 1G RAM": ({"DEV_HOST_MEMORY_KEEP": "7500M"}, "at least 1G"),
            "high above max": ({"DEV_MEMORY_HIGH": "20G", "DEV_MEMORY_MAX": "10G"}, "MemoryHigh"),
        }
        for name, (values, text) in cases.items():
            with self.subTest(name), self.assertRaisesRegex(cap.CapacityError, text):
                cap.dedicated_memory(values, **HOST)

    def test_a_zero_pin_omits_like_a_dash_as_in_the_shared_validators(self) -> None:
        # shared validators (validate_size_or_zero) define 0 as "none"; dedicated-dev must not refuse what shared-prod accepts
        for key in ("DEV_MEMORY_HIGH", "DEV_MEMORY_MAX", "DEV_MEMORY_LOW"):
            m = cap.dedicated_memory({key: "0"}, **HOST)
            attr = {"DEV_MEMORY_HIGH": m.high, "DEV_MEMORY_MAX": m.maximum, "DEV_MEMORY_LOW": m.low}[key]
            self.assertIsNone(attr, key)
        self.assertEqual(cap.dedicated_memory({"DEV_MEMORY_MAX": "0"}, **HOST).max_source, "omitted (0)")
        self.assertIsNone(cap.validate_size_or_zero("0"))

    LOWS = {"DEV_INTERACTIVE_MEMORY_LOW": "512M", "DEV_BUILDKITD_MEMORY_LOW": "256M"}

    def test_load_tiers_sit_below_dev_slice_by_the_protected_lows(self) -> None:
        m = cap.dedicated_memory(self.LOWS, **HOST)
        self.assertEqual(m.protected, 768 * MIB)
        for tier in ("gates", "background"):
            self.assertEqual(getattr(m, f"{tier}_high"), m.high - 768 * MIB)
            self.assertEqual(getattr(m, f"{tier}_max"), m.high)  # AD2-0: MemoryMax = dev.slice MemoryHigh
            self.assertEqual(getattr(m, f"{tier}_high_source"), "derived")
        # unprotected tiers (gates' / background's own Low do not count) and no Lows at all: High equal to dev.slice
        m0 = cap.dedicated_memory({"DEV_GATES_MEMORY_LOW": "1G"}, **HOST)
        self.assertEqual((m0.gates_high, m0.gates_max), (m0.high, m0.high))

    def test_cache_low_counts_in_the_subtraction_and_defaults_to_zero(self) -> None:
        m = cap.dedicated_memory({**self.LOWS, "DEV_CACHE_MEMORY_LOW": "256M"}, **HOST)
        self.assertEqual(m.protected, GIB)
        self.assertEqual(m.gates_high, m.high - GIB)
        for off in ("", "-"):
            self.assertEqual(cap.dedicated_memory({**self.LOWS, "DEV_CACHE_MEMORY_LOW": off}, **HOST).protected, 768 * MIB)
        with self.assertRaises(cap.CapacityError):
            cap.dedicated_memory({"DEV_CACHE_MEMORY_LOW": "lots"}, **HOST)

    def test_load_tier_swap_is_dev_swap_minus_protected_with_floor_and_cap(self) -> None:
        # T 7.75 G (MemTotal 8 G, keep 1 G -> dev High 7 G) x dev swap in {0, 8, 16} G, protected 768 MiB
        for swap_gib, want in ((0, 0), (8, 8 * GIB - 768 * MIB), (16, 16 * GIB - 768 * MIB)):
            self.assertEqual(cap.load_tier_swap(swap_gib * GIB, 768 * MIB), want)
        self.assertEqual(cap.load_tier_swap(512 * MIB, 768 * MIB), 256 * MIB)  # floor
        self.assertEqual(cap.load_tier_swap(128 * MIB, 768 * MIB), 128 * MIB)  # never above dev.slice's
        self.assertEqual(cap.load_tier_swap(8 * GIB, 0), 8 * GIB)

    def test_load_tier_max_is_dev_high_on_each_swap_size(self) -> None:
        for swap_gib in (0, 8, 16):
            m = cap.dedicated_memory(self.LOWS, mem_total=8 * GIB, swap_total=swap_gib * GIB)
            self.assertEqual((m.gates_max, m.background_max), (m.high, m.high))
            self.assertEqual(m.gates_high, m.high - 768 * MIB)

    def test_load_tier_floor_cap_and_omission(self) -> None:
        m = cap.dedicated_memory({**self.LOWS, "DEV_MEMORY_HIGH": "1500M", "DEV_MEMORY_MAX": "2G"}, **HOST)
        self.assertEqual(m.gates_high, GIB)  # 1500M - 768M = 732M, floored at 1G
        tiny = cap.dedicated_memory({"DEV_INTERACTIVE_MEMORY_LOW": "4G", "DEV_MEMORY_HIGH": "512M", "DEV_MEMORY_MAX": "512M"}, **HOST)
        self.assertEqual(tiny.gates_high, 512 * MIB)  # never above dev.slice's own
        none = cap.dedicated_memory({**self.LOWS, "DEV_MEMORY_HIGH": "-", "DEV_MEMORY_MAX": "-"}, **HOST)
        self.assertIsNone(none.gates_high)
        self.assertIsNone(none.background_max)

    def test_load_tier_pins_win_and_a_pair_is_reconciled_or_refused(self) -> None:
        m = cap.dedicated_memory({**self.LOWS, "DEV_GATES_MEMORY_HIGH": "3G", "DEV_BACKGROUND_MEMORY_MAX": "-"}, **HOST)
        self.assertEqual(m.gates_high, 3 * GIB)
        self.assertIn("pin", m.gates_high_source)
        self.assertEqual(m.gates_max, m.high)  # the other side stays derived (dev.slice MemoryHigh)
        self.assertIsNone(m.background_max)
        self.assertEqual(m.background_high, m.high - 768 * MIB)
        low_max = cap.dedicated_memory({**self.LOWS, "DEV_GATES_MEMORY_MAX": "4G"}, **HOST)
        self.assertEqual((low_max.gates_high, low_max.gates_max), (4 * GIB, 4 * GIB))  # derived high follows the MemoryMax pin
        with self.assertRaises(cap.CapacityError):
            cap.dedicated_memory({"DEV_GATES_MEMORY_HIGH": "5G", "DEV_GATES_MEMORY_MAX": "4G"}, **HOST)
        with self.assertRaises(cap.CapacityError):
            cap.dedicated_memory({"DEV_BACKGROUND_MEMORY_HIGH": "128M"}, **HOST)

    def test_interactive_and_buildkitd_ceilings_are_not_derived(self) -> None:
        m = cap.dedicated_memory(self.LOWS, **HOST)
        self.assertFalse(hasattr(m, "interactive_high"))
        self.assertFalse(hasattr(m, "buildkitd_high"))

    def test_a_keep_at_the_floor_is_accepted(self) -> None:
        self.assertEqual(cap.dedicated_memory({"DEV_HOST_MEMORY_KEEP": "256M"}, **HOST).keep, 256 * MIB)

    def test_no_swap_host_gets_ram_only_limits(self) -> None:
        m = cap.dedicated_memory({}, mem_total=8 * GIB, swap_total=0)
        self.assertEqual(m.maximum, 8 * GIB - GIB)
        self.assertEqual(m.high, m.maximum)

    def test_validators(self) -> None:
        self.assertIsNone(cap.validate_host_memory_keep("512M"))
        for bad in ("", "0", "-", "1x", "1", "255M"):
            self.assertIsNotNone(cap.validate_host_memory_keep(bad), bad)
        for ok in ("", "-", "docker.service containerd.service"):
            self.assertIsNone(cap.validate_host_protected_units(ok), ok)
        self.assertIsNotNone(cap.validate_host_protected_units("rm -rf"))

    def test_ssh_can_never_be_protected_by_an_oom_score_drop_in(self) -> None:
        # review F1: sshd hands its own score to every login session it forks
        self.assertNotIn("ssh", " ".join(cap.DEDICATED_PROTECTED_UNITS_DEFAULT.split()).replace("systemd", ""))
        for unit in ("ssh.service", "sshd.service", "ssh.socket", "docker.service ssh.service"):
            with self.subTest(unit):
                self.assertRegex(cap.validate_host_protected_units(unit) or "", "login")
                with self.assertRaisesRegex(cap.CapacityError, "DEV_HOST_PROTECTED_UNITS"):
                    cap.dedicated_protected_units({"DEV_HOST_PROTECTED_UNITS": unit})

    def test_the_key_is_not_a_retired_reserve_key(self) -> None:
        self.assertEqual(cap.renamed_key_errors({"DEV_HOST_MEMORY_KEEP": "512M", "DEV_HOST_PROTECTED_UNITS": ""}), [])

    def test_protected_units_default_dash_and_custom(self) -> None:
        defaults = cap.dedicated_protected_units({})
        self.assertEqual(defaults, cap.DEDICATED_PROTECTED_UNITS_DEFAULT.split())
        self.assertIn("cgpd.service", defaults)
        self.assertIn("cgprofiled.service", defaults)
        example = (ROOT / "host-setup.env.example").read_text(encoding="utf-8")
        protected_default = next(line for line in example.splitlines()
                                 if line.startswith("# DEV_HOST_PROTECTED_UNITS:"))
        self.assertIn("cgpd.service", protected_default)
        self.assertIn("cgprofiled.service", protected_default)
        self.assertEqual(cap.dedicated_protected_units({"DEV_HOST_PROTECTED_UNITS": "-"}), [])
        self.assertEqual(cap.dedicated_protected_units({"DEV_HOST_PROTECTED_UNITS": "a.service b.service"}), ["a.service", "b.service"])


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
        self.assertEqual(out["DEDICATED_MEM_MAX"], str(24 * GIB - GIB))
        self.assertEqual(out["DEDICATED_MEM_HIGH"], str(8 * GIB - GIB))
        self.assertEqual(out["DEDICATED_MEM_KEEP"], str(GIB))
        self.assertEqual(out["DEDICATED_PROTECTED_UNITS"], cap.DEDICATED_PROTECTED_UNITS_DEFAULT)

    def test_dedicated_memory_prints_the_load_tier_swap_for_a_dev_swap(self) -> None:
        mi = str(meminfo(self.tmp / "mi"))
        env = "HOST_ROLE=dedicated-dev\nDEV_INTERACTIVE_MEMORY_LOW=512M\nDEV_BUILDKITD_MEMORY_LOW=256M\nDEV_CACHE_MEMORY_LOW=256M\n"
        for swap_gib in (0, 8, 16):
            r = self.run_cli(env, "dedicated-memory", "--meminfo", mi, "--dev-swap", str(swap_gib * GIB))
            self.assertEqual(r.returncode, 0, r.stderr)
            out = dict(line.split("=", 1) for line in r.stdout.splitlines())
            self.assertEqual(out["DEDICATED_PROTECTED_LOW"], str(GIB))
            self.assertEqual(out["DEDICATED_LOAD_SWAP"], str(cap.load_tier_swap(swap_gib * GIB, GIB)))
            self.assertEqual(out["DEDICATED_GATES_MAX"], out["DEDICATED_MEM_HIGH"])
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
    """The two install.sh blocks, extracted and run with fakes."""

    PRE = shell_block(INSTALL_SH, "# HOST_ROLE=dedicated-dev memory (MDT-TESTHOST-MEM), PRE-FLIGHT",
                      'if [ -n "${DEV_SWAP_MAX:-}" ]; then\n  if ! _swap_max_bytes')
    DROPINS = shell_block(INSTALL_SH, "# HOST_ROLE=dedicated-dev host protection (MDT-TESTHOST-MEM)",
                          'if [ -n "${IO_SD_DEV:-}" ]; then\n  if [ "$(_mdt_host_role)" = dedicated-dev ]')

    # The installer's own role detection (install.sh sets MDT_DEDICATED_MEM from _mdt_host_role), not a flag the test sets.
    ROLE_DETECT = shell_block(INSTALL_SH, "MDT_DEDICATED_MEM=0\nif [ \"$(_mdt_host_role)\" = dedicated-dev ]; then", "_host_swap_bytes=\"\"\n")

    def setUp(self) -> None:
        self.tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.bin = self.tmp / "bin"
        self.bin.mkdir()
        fake = self.bin / "systemctl"
        # `systemctl cat U` succeeds for every unit except ghost.service; OOMScoreAdjust of containerd is -999, 0 for the rest
        fake.write_text('#!/bin/sh\ncase "$1" in\n cat) [ "$2" != ghost.service ] ;;\n'
                        ' show) case "$5" in containerd.service) echo -999;; *) echo 0;; esac ;;\nesac\n')
        fake.chmod(fake.stat().st_mode | stat.S_IEXEC)

    def run_pre(self, env_text: str, *, mem_gib: int = 8, swap_gib: int = 16) -> subprocess.CompletedProcess:
        env = self.tmp / "candidate.env"
        env.write_text(env_text)
        mi = meminfo(self.tmp / "meminfo", mem_gib, swap_gib)
        block = self.PRE.replace("/proc/meminfo", str(mi))
        script = f'''set -euo pipefail
HERE="{ROOT}"; CANDIDATE_PATH="{env}"; CONFIG_PATH=/etc/mdt/host-setup.env
. "{ROOT}/scripts/mdt-container-io-caps.lib.sh" 2>/dev/null
set -a; . "{env}"; set +a
{self.ROLE_DETECT}
{block}
echo "HIGH=${{DEV_MEMORY_HIGH:-}} MAX=${{DEV_MEMORY_MAX:-}} LOW=${{DEV_MEMORY_LOW:-}} GATES_HIGH=${{DEV_GATES_MEMORY_HIGH:-}} GATES_MAX=${{DEV_GATES_MEMORY_MAX:-}} BK_MAX=${{DEV_BUILDKITD_MEMORY_MAX:-}} KEEP=$MDT_DEDICATED_KEEP"
'''
        return subprocess.run(["bash", "-c", script], capture_output=True, text=True)

    def test_dedicated_dev_derives_dev_slice_and_drops_every_tier_ceiling(self) -> None:
        r = self.run_pre('HOST_ROLE=dedicated-dev\nDEV_GATES_MEMORY_HIGH=4G\nDEV_GATES_MEMORY_MAX=5.5G\nDEV_BUILDKITD_MEMORY_MAX=2G\n'
                         'DEV_INTERACTIVE_MEMORY_LOW=512M\n')
        self.assertEqual(r.returncode, 0, r.stderr)
        # gates are pinned (wins), buildkitd is ignored and named
        self.assertIn(f"HIGH={8 * GIB - GIB} MAX={24 * GIB - GIB} LOW={512 * MIB} GATES_HIGH={4 * GIB} GATES_MAX={int(5.5 * GIB)} BK_MAX= KEEP={GIB}", r.stdout)
        self.assertIn("ignores the per-tier memory ceilings", r.stderr)
        self.assertIn("DEV_BUILDKITD_MEMORY_MAX=2G", r.stderr)
        self.assertNotIn("DEV_GATES_MEMORY_HIGH=4G", r.stderr)

    def test_gates_and_background_are_derived_below_dev_slice_by_the_protected_lows(self) -> None:
        r = self.run_pre("HOST_ROLE=dedicated-dev\nDEV_INTERACTIVE_MEMORY_LOW=512M\nDEV_BUILDKITD_MEMORY_LOW=256M\n")
        self.assertEqual(r.returncode, 0, r.stderr)
        want = 7 * GIB - 768 * MIB
        self.assertIn(f"GATES_HIGH={want} GATES_MAX={7 * GIB}", r.stdout)  # AD2-0: Max = dev.slice MemoryHigh

    def test_the_install_swap_loop_uses_the_one_python_derivation_for_the_load_tiers_on_dedicated_dev_only(self) -> None:
        self.assertIn("DEV_GATES_MEMORY_SWAP_MAX|DEV_BACKGROUND_MEMORY_SWAP_MAX)", INSTALL_SH)
        self.assertIn("--dev-swap \"$DEV_SWAP_MAX\"", INSTALL_SH)
        self.assertIn('[ "$MDT_DEDICATED_MEM" = 1 ]', INSTALL_SH)
        # an explicit value (pin) never reaches the derivation: the branch is inside the `-z ${!_swap_var}` condition
        self.assertIn('if [ -z "${!_swap_var:-}" ] && [ "${!_unset_var:-0}" != 1 ] && [ -n "${DEV_SWAP_MAX:-}" ]; then\n      case', INSTALL_SH)

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
HERE="{ROOT}"; CANDIDATE_PATH="{env}"; CONFIG_PATH=/etc/mdt/host-setup.env
. "{ROOT}/scripts/mdt-container-io-caps.lib.sh" 2>/dev/null
set -a; . "{env}"; set +a
DEV_SWAP_CASCADE_PCT=80
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
        env = ("DEV_MEMORY_MAX=7.5G\nDEV_MEMORY_HIGH=7G\nDEV_MEMORY_LOW=1G\nDEV_GATES_MEMORY_HIGH=4G\nDEV_GATES_MEMORY_MAX=5.5G\n"
               "DEV_INTERACTIVE_MEMORY_MAX=4.5G\nDEV_BUILDKITD_MEMORY_MAX=2.5G\nDEV_BUILDKITD_MEMORY_LOW=256M\n")
        for role in ("", "HOST_ROLE=shared-prod\n"):
            with self.subTest(role=role):
                before = self.render_all(role + env, with_block=False)
                after = self.render_all(role + env, with_block=True)
                self.assertEqual(before, after)
                self.assertIn("MemoryMax=7.5G", before["dev.slice.in"])

    def test_dedicated_dev_renders_differently_so_the_identity_check_is_not_vacuous(self) -> None:
        env = "HOST_ROLE=dedicated-dev\nDEV_MEMORY_MAX=7.5G\nDEV_INTERACTIVE_MEMORY_MAX=4.5G\n"
        self.assertNotEqual(self.render_all(env, with_block=False), self.render_all(env, with_block=True))

    def run_dropins(self, dedicated: bool, units: str = "docker.service containerd.service ghost.service") -> tuple[subprocess.CompletedProcess, Path]:
        sysdir = self.tmp / "systemd"
        sysdir.mkdir(exist_ok=True)
        script = f'''set -euo pipefail
export PATH="{self.bin}:$PATH"
MDT_SYSTEMD_DIR="{sysdir}"
MDT_DEDICATED_MEM={1 if dedicated else 0}
MDT_DEDICATED_KEEP=536870912
MDT_DEDICATED_PROTECTED_UNITS="{units}"
{self.DROPINS}
echo "LIVE=$MDT_DED_LIVE_UNITS"
'''
        return subprocess.run(["bash", "-c", script], capture_output=True, text=True), sysdir

    def test_dedicated_dev_writes_the_dropins_and_never_weakens_a_stronger_one(self) -> None:
        r, sysdir = self.run_dropins(True)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual((sysdir / "system.slice.d/50-mdt-dedicated-dev.conf").read_text().splitlines()[-2:], ["[Slice]", "MemoryLow=536870912"])
        self.assertEqual((sysdir / "docker.service.d/50-mdt-dedicated-dev.conf").read_text().splitlines()[-2:], ["[Service]", "OOMScoreAdjust=-900"])
        self.assertFalse((sysdir / "containerd.service.d").exists(), "containerd already has -999")
        self.assertFalse((sysdir / "ghost.service.d").exists(), "a unit that is not installed is skipped")
        self.assertIn("LIVE= docker.service", r.stdout)
        for f in (sysdir / "docker.service.d/50-mdt-dedicated-dev.conf", sysdir / "system.slice.d/50-mdt-dedicated-dev.conf"):
            self.assertEqual(stat.S_IMODE(f.stat().st_mode), 0o644, f)

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

    def test_narrowing_the_protected_list_removes_the_dropins_of_units_that_left(self) -> None:
        _, sysdir = self.run_dropins(True, "docker.service cgprofiled.service")
        self.assertTrue((sysdir / "cgprofiled.service.d/50-mdt-dedicated-dev.conf").exists())
        r, _ = self.run_dropins(True, "docker.service")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertFalse((sysdir / "cgprofiled.service.d").exists())
        self.assertTrue((sysdir / "docker.service.d/50-mdt-dedicated-dev.conf").exists())
        r, _ = self.run_dropins(True, "")
        self.assertFalse((sysdir / "docker.service.d").exists())
        self.assertTrue((sysdir / "system.slice.d/50-mdt-dedicated-dev.conf").exists())

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
        self.assertFalse((sysdir / "system.slice.d").exists())
        self.assertFalse((sysdir / "docker.service.d/50-mdt-dedicated-dev.conf").exists())
        self.assertTrue(other.exists(), "an operator's own drop-in is never touched")


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
    def test_check_sh_memory_block_is_role_gated_and_warns_about_an_undersized_keep_and_a_stale_ssh_dropin(self) -> None:
        block = shell_block(CHECK_SH, "HOST_ROLE=dedicated-dev (MDT-TESTHOST-MEM): the derived values", "# dev.slice's own MemoryZSwapWriteback")
        self.assertIn('[ "$(_mdt_host_role)" = dedicated-dev ]', CHECK_SH)
        self.assertRegex(block, r"system\.slice memory\.current=\$_sys_cur exceeds DEV_HOST_MEMORY_KEEP")
        self.assertIn("ssh.service.d/50-mdt-dedicated-dev.conf", block)
        # the whole script is still syntactically valid shell
        self.assertEqual(subprocess.run(["bash", "-n", str(ROOT / "scripts" / "check.sh")], capture_output=True, text=True).returncode, 0)

    def test_the_wizard_does_not_warn_about_a_missing_dev_memory_low_when_dedicated_dev_derives_it(self) -> None:
        wiz = _load("mdt_wizard_mem_t", ROOT / "mdt-host-setup-wizard.py")
        values = {"DEV_INTERACTIVE_MEMORY_LOW": "512M", "DEV_MEMORY_LOW": ""}
        self.assertTrue(wiz.memory_review_warnings(dict(values, HOST_ROLE="shared-prod")))
        self.assertEqual(wiz.memory_review_warnings(dict(values, HOST_ROLE="dedicated-dev")), [])
        # an explicit omission is still worth naming
        self.assertTrue(wiz.memory_review_warnings(dict(values, HOST_ROLE="dedicated-dev", DEV_MEMORY_LOW="-")))


class TemplateAndDocsTests(unittest.TestCase):
    def test_the_dedicated_role_blanks_tier_ceilings_that_shared_prod_still_renders(self) -> None:
        # behavioural: the pre-flight block empties the tier variables on dedicated-dev only (InstallBlockTests), while the
        # templates still carry the directive for shared-prod
        for tier in ("interactive", "background", "gates", "buildkitd"):
            text = (ROOT / "units" / f"dev-{tier}.slice.in").read_text()
            self.assertIn(f"MemoryMax=@DEV_{tier.upper()}_MEMORY_MAX@", text)

    def test_the_keys_are_declared_once_with_an_allowed_line_and_are_runtime_optional(self) -> None:
        text = EXAMPLE.read_text()
        for key in ("DEV_HOST_MEMORY_KEEP", "DEV_HOST_PROTECTED_UNITS"):
            self.assertEqual(len(re.findall(rf"(?m)^{key}=", text)), 1, key)
            self.assertRegex(text, rf"(?m)^# Allowed [^\n]*\b{key}\b")
        self.assertRegex(text, r"(?m)^DEV_HOST_MEMORY_KEEP=1G$")
        self.assertEqual(cap.DEDICATED_MEMORY_KEEP_DEFAULT, "1G")

    def test_check_sh_expects_no_tier_ceiling_on_dedicated_dev(self) -> None:
        # buildkitd still has none; gates/background are verified against the derivation (dev.slice minus the protected Lows)
        self.assertEqual(CHECK_SH.count("HOST_ROLE=dedicated-dev means no per-tier memory ceiling"), 1)
        self.assertIn("dedicated-memory", CHECK_SH)
        self.assertIn("DEDICATED_${_tier_key}_", CHECK_SH)
        self.assertIn("is below dev.slice memory.high", CHECK_SH)
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
        for needle in ("thrash", "memory.swap.max", "global kernel OOM killer", "io.max", "never ssh", "at least 256M"):
            self.assertIn(needle, readme + example, needle)
        self.assertNotIn("ssh.service docker.service", readme + example)


if __name__ == "__main__":
    unittest.main()
