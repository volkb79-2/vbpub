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
    def test_default_keep_is_512m_and_the_limits_follow_the_operator_formula(self) -> None:
        m = cap.dedicated_memory({}, **HOST)
        self.assertEqual(m.keep, 512 * MIB)
        self.assertEqual(m.maximum, 8 * GIB + 16 * GIB - 512 * MIB)  # RAM + swap - keep (the 23.5 GiB of the directive)
        self.assertEqual(m.high, 8 * GIB - 512 * MIB)  # the RAM the dev tier may hold
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
            "keep junk": ({"DEV_HOST_MEMORY_KEEP": "lots"}, "DEV_HOST_MEMORY_KEEP"),
            "keep leaves < 1G RAM": ({"DEV_HOST_MEMORY_KEEP": "7500M"}, "at least 1G"),
            "high above max": ({"DEV_MEMORY_HIGH": "20G", "DEV_MEMORY_MAX": "10G"}, "MemoryHigh"),
            "pin zero": ({"DEV_MEMORY_MAX": "0"}, "positive"),
        }
        for name, (values, text) in cases.items():
            with self.subTest(name), self.assertRaisesRegex(cap.CapacityError, text):
                cap.dedicated_memory(values, **HOST)

    def test_no_swap_host_gets_ram_only_limits(self) -> None:
        m = cap.dedicated_memory({}, mem_total=8 * GIB, swap_total=0)
        self.assertEqual(m.maximum, 8 * GIB - 512 * MIB)
        self.assertEqual(m.high, m.maximum)

    def test_validators(self) -> None:
        self.assertIsNone(cap.validate_host_memory_keep("512M"))
        for bad in ("", "0", "-", "1x"):
            self.assertIsNotNone(cap.validate_host_memory_keep(bad), bad)
        for ok in ("", "-", "ssh.service docker.service"):
            self.assertIsNone(cap.validate_host_protected_units(ok), ok)
        self.assertIsNotNone(cap.validate_host_protected_units("rm -rf"))

    def test_the_key_is_not_a_retired_reserve_key(self) -> None:
        self.assertEqual(cap.renamed_key_errors({"DEV_HOST_MEMORY_KEEP": "512M", "DEV_HOST_PROTECTED_UNITS": ""}), [])

    def test_protected_units_default_dash_and_custom(self) -> None:
        self.assertEqual(cap.dedicated_protected_units({}), cap.DEDICATED_PROTECTED_UNITS_DEFAULT.split())
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

    def test_dedicated_memory_prints_key_value_lines(self) -> None:
        r = self.run_cli("HOST_ROLE=dedicated-dev\n", "dedicated-memory", "--meminfo", str(meminfo(self.tmp / "mi")))
        self.assertEqual(r.returncode, 0, r.stderr)
        out = dict(line.split("=", 1) for line in r.stdout.splitlines())
        self.assertEqual(out["DEDICATED_MEM_MAX"], str(24 * GIB - 512 * MIB))
        self.assertEqual(out["DEDICATED_MEM_HIGH"], str(8 * GIB - 512 * MIB))
        self.assertEqual(out["DEDICATED_MEM_KEEP"], str(512 * MIB))
        self.assertEqual(out["DEDICATED_PROTECTED_UNITS"], cap.DEDICATED_PROTECTED_UNITS_DEFAULT)

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
        self.assertIn("ssh.service", units)
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
MDT_DEDICATED_MEM={'1' if 'HOST_ROLE=dedicated-dev' in env_text else '0'}
{block}
echo "HIGH=${{DEV_MEMORY_HIGH:-}} MAX=${{DEV_MEMORY_MAX:-}} LOW=${{DEV_MEMORY_LOW:-}} GATES_HIGH=${{DEV_GATES_MEMORY_HIGH:-}} GATES_MAX=${{DEV_GATES_MEMORY_MAX:-}} BK_MAX=${{DEV_BUILDKITD_MEMORY_MAX:-}} KEEP=$MDT_DEDICATED_KEEP"
'''
        return subprocess.run(["bash", "-c", script], capture_output=True, text=True)

    def test_dedicated_dev_derives_dev_slice_and_drops_every_tier_ceiling(self) -> None:
        r = self.run_pre('HOST_ROLE=dedicated-dev\nDEV_GATES_MEMORY_HIGH=4G\nDEV_GATES_MEMORY_MAX=5.5G\nDEV_BUILDKITD_MEMORY_MAX=2G\n'
                         'DEV_INTERACTIVE_MEMORY_LOW=512M\n')
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn(f"HIGH={8 * GIB - 512 * MIB} MAX={24 * GIB - 512 * MIB} LOW={512 * MIB} GATES_HIGH= GATES_MAX= BK_MAX= KEEP={512 * MIB}", r.stdout)
        self.assertIn("ignores the per-tier memory ceilings", r.stderr)
        self.assertIn("DEV_GATES_MEMORY_HIGH=4G", r.stderr)

    def test_a_refused_derivation_exits_2_before_anything_is_changed(self) -> None:
        r = self.run_pre("HOST_ROLE=dedicated-dev\nDEV_HOST_MEMORY_KEEP=0\n")
        self.assertNotEqual(r.returncode, 0)
        self.assertIn("cannot be derived", r.stderr)

    def test_shared_prod_block_is_inert(self) -> None:
        for role in ("", "HOST_ROLE=shared-prod\n"):
            with self.subTest(role=role):
                r = self.run_pre(role + "DEV_MEMORY_MAX=7.5G\nDEV_GATES_MEMORY_MAX=5.5G\n")
                self.assertEqual(r.returncode, 0, r.stderr)
                self.assertIn("MAX=7.5G", r.stdout)
                self.assertIn("GATES_MAX=5.5G", r.stdout)

    def run_dropins(self, dedicated: bool, units: str = "ssh.service containerd.service ghost.service") -> tuple[subprocess.CompletedProcess, Path]:
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
        self.assertEqual((sysdir / "ssh.service.d/50-mdt-dedicated-dev.conf").read_text().splitlines()[-2:], ["[Service]", "OOMScoreAdjust=-900"])
        self.assertFalse((sysdir / "containerd.service.d").exists(), "containerd already has -999")
        self.assertFalse((sysdir / "ghost.service.d").exists(), "a unit that is not installed is skipped")
        self.assertIn("LIVE= ssh.service", r.stdout)

    def test_a_second_run_rewrites_instead_of_deciding_from_its_own_dropin(self) -> None:
        self.run_dropins(True)
        r, sysdir = self.run_dropins(True)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertTrue((sysdir / "ssh.service.d/50-mdt-dedicated-dev.conf").exists())

    def test_leaving_the_role_removes_exactly_the_files_it_owns(self) -> None:
        _, sysdir = self.run_dropins(True)
        other = sysdir / "ssh.service.d" / "10-operator.conf"
        other.write_text("[Service]\nNice=5\n")
        r, _ = self.run_dropins(False)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertFalse((sysdir / "system.slice.d").exists())
        self.assertFalse((sysdir / "ssh.service.d/50-mdt-dedicated-dev.conf").exists())
        self.assertTrue(other.exists(), "an operator's own drop-in is never touched")


class TemplateAndDocsTests(unittest.TestCase):
    def test_tier_ceilings_are_removed_only_by_the_role_not_by_the_templates(self) -> None:
        # the slice templates still carry the directives (shared-prod renders them); dedicated-dev blanks the variables
        for tier in ("interactive", "background", "gates", "buildkitd"):
            text = (ROOT / "units" / f"dev-{tier}.slice.in").read_text()
            self.assertIn(f"MemoryHigh=@DEV_{tier.upper()}_MEMORY_HIGH@", text)
            self.assertIn(f"MemoryMax=@DEV_{tier.upper()}_MEMORY_MAX@", text)

    def test_the_keys_are_declared_once_with_an_allowed_line_and_are_runtime_optional(self) -> None:
        text = EXAMPLE.read_text()
        for key in ("DEV_HOST_MEMORY_KEEP", "DEV_HOST_PROTECTED_UNITS"):
            self.assertEqual(len(re.findall(rf"(?m)^{key}=", text)), 1, key)
            self.assertRegex(text, rf"(?m)^# Allowed [^\n]*\b{key}\b")
        self.assertRegex(text, r"(?m)^DEV_HOST_MEMORY_KEEP=512M$")

    def test_check_sh_expects_no_tier_ceiling_on_dedicated_dev(self) -> None:
        self.assertEqual(CHECK_SH.count("HOST_ROLE=dedicated-dev means no per-tier memory ceiling"), 2)
        self.assertIn("dedicated-memory", CHECK_SH)

    def test_the_readme_documents_oom_order_and_the_pins(self) -> None:
        readme = (ROOT / "README.md").read_text()
        for needle in ("DEV_HOST_MEMORY_KEEP", "OOMScoreAdjust", "dev tier is killed first", "MDT-TESTHOST-MEM"):
            self.assertIn(needle, readme)


if __name__ == "__main__":
    unittest.main()
