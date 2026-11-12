"""MDT-033: the capacity unavailable to MDT (RAM, disk swap, CPU cores).

Covers the key grammar, the refusal of the retired *_RESERVE_* keys by every entry point,
the derived sizing on fake ``/proc/meminfo`` and ``nproc``, the protection verification and
the read-only probe on fake cgroup trees, the opt-in drop-in writer, and the wording rule.
Nothing here touches a real host: cgroup trees are temporary directories.
"""
from __future__ import annotations

import contextlib
import io
import re
import subprocess
import sys
import tempfile
import unittest
from decimal import Decimal
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent))
from test_io_governance import ROOT, _load  # noqa: E402

sys.path.insert(0, str(ROOT))
import mdt_capacity as MC  # noqa: E402

EXAMPLE = ROOT / "host-setup.env.example"
WIZARD_PATH = ROOT / "mdt-host-setup-wizard.py"
README = ROOT / "README.md"
CHECK = ROOT / "scripts" / "check.sh"
GIB = MC.GIB
MIB = MC.MIB

# The shared reference host: a 16 GiB-class machine (MemTotal 15.56 GiB), 69 GiB swap.
SHARED_MEM = 16318464 * 1024
SHARED_SWAP = 69 * GIB


def _tmp(case: unittest.TestCase) -> Path:
    d = tempfile.TemporaryDirectory()
    case.addCleanup(d.cleanup)
    return Path(d.name)


class KeyGrammarTests(unittest.TestCase):
    def test_fractional_cores_validate(self) -> None:
        for good in ("0", "0.5", "1.5", "3", "12.0"):
            self.assertIsNone(MC.validate_cores(good), good)
        for bad in ("1.55", "", "-1", "1.", ".5", "two", "1,5"):
            self.assertIsNotNone(MC.validate_cores(bad), bad)

    def test_sizes_accept_zero_and_suffixes(self) -> None:
        for good in ("0", "6500M", "14G", "512M", "1.5G", "1048576"):
            self.assertIsNone(MC.validate_size_or_zero(good), good)
        for bad in ("", "lots", "-1G", "6.5.0G"):
            self.assertIsNotNone(MC.validate_size_or_zero(bad), bad)

    def test_units_are_space_separated_unit_names(self) -> None:
        self.assertIsNone(MC.validate_units(""))
        self.assertIsNone(MC.validate_units("wings.slice docker.service"))
        self.assertIsNotNone(MC.validate_units("wings"))
        self.assertIsNotNone(MC.validate_units("a.slice;rm"))

    def test_the_example_declares_every_new_key_with_its_default(self) -> None:
        text = EXAMPLE.read_text()
        for line in (
            "MDT_EXCLUDED_RAM=0", "MDT_EXCLUDED_SWAP=0", "MDT_EXCLUDED_CPU_CORES=0",
            "DEV_TIER_EXCLUDED_CPU_CORES=0", 'MDT_PROTECTED_UNITS=""', "MDT_HOST_SWAP_MARGIN=512M",
        ):
            self.assertRegex(text, rf"(?m)^{re.escape(line)}$")


class RenamedKeyTests(unittest.TestCase):
    def test_every_old_key_is_refused_with_the_exact_message(self) -> None:
        errors = MC.renamed_key_errors({"DEV_CPU_RESERVE_CORES": "1"})
        self.assertEqual(errors, [
            "DEV_CPU_RESERVE_CORES is no longer read: rename it to MDT_EXCLUDED_CPU_CORES "
            "(cores that other services on this host use and that are unavailable to MDT; "
            "your value 1 → MDT_EXCLUDED_CPU_CORES=1). "
            'See host-setup/README.md "Renamed keys (2026-11)".'
        ])

    def test_the_tier_key_migrates_max_of_zero_and_k_minus_c(self) -> None:
        for c, k, want in (("1", "3", "2"), ("2", "1", "0"), ("1", "1", "0")):
            [msg] = [m for m in MC.renamed_key_errors(
                {"DEV_CPU_RESERVE_CORES": c, "DEV_SUBSLICE_CPU_RESERVE_CORES": k}
            ) if m.startswith("DEV_SUBSLICE")]
            self.assertIn(f"your value {k} → DEV_TIER_EXCLUDED_CPU_CORES={want})", msg)
            self.assertIn("rename it to DEV_TIER_EXCLUDED_CPU_CORES", msg)

    def test_the_tier_key_without_the_root_key_assumes_the_retired_default_of_one(self) -> None:
        [msg] = MC.renamed_key_errors({"DEV_SUBSLICE_CPU_RESERVE_CORES": "3"})
        self.assertIn("DEV_TIER_EXCLUDED_CPU_CORES=2)", msg)

    def test_a_memory_reserve_and_any_other_reserve_key_are_refused(self) -> None:
        [msg] = MC.renamed_key_errors({"DEV_MEMORY_RESERVE": "6G"})
        self.assertIn("rename it to MDT_EXCLUDED_RAM", msg)
        [other] = MC.renamed_key_errors({"DEV_FOO_RESERVE_BAR": "1"})
        self.assertIn("DEV_FOO_RESERVE_BAR is no longer read", other)

    def test_clean_files_pass(self) -> None:
        self.assertEqual(MC.renamed_key_errors({"MDT_EXCLUDED_CPU_CORES": "1", "WATCHER_INTERVAL": "30"}), [])

    def test_the_cli_refuses_with_exit_2(self) -> None:
        env = _tmp(self) / "env"
        env.write_text("DEV_CPU_RESERVE_CORES=1\n")
        err = io.StringIO()
        with contextlib.redirect_stderr(err):
            rc = MC.main(["refuse-renamed", str(env)])
        self.assertEqual(rc, 2)
        self.assertIn("rename it to MDT_EXCLUDED_CPU_CORES", err.getvalue())
        env.write_text("MDT_EXCLUDED_CPU_CORES=1\n")
        self.assertEqual(MC.main(["refuse-renamed", str(env)]), 0)


class WizardEntryPointTests(unittest.TestCase):
    """The installer reaches the validator through ``--validate-config``; the wizard reads the
    existing file. Both refuse an old key."""

    def _wizard(self):
        return _load(WIZARD_PATH, "mdt_wizard_excluded_capacity")

    def _validate(self, text: str, meminfo: str = "MemTotal: 16384000 kB\nSwapTotal: 8388604 kB\n"):
        wizard = self._wizard()
        d = _tmp(self)
        (d / "env").write_text(text)
        (d / "meminfo").write_text(meminfo)
        buf = io.StringIO()
        with mock.patch.object(wizard, "host_context_error", return_value=None), \
             contextlib.redirect_stdout(buf), contextlib.redirect_stderr(buf):
            rc = wizard.main(["--validate-config", str(d / "env"), "--example", str(EXAMPLE),
                              "--meminfo-path", str(d / "meminfo")])
        return rc, buf.getvalue()

    def test_the_installer_validator_refuses_an_old_key_naming_the_new_one(self) -> None:
        text = EXAMPLE.read_text().replace("MDT_EXCLUDED_CPU_CORES=0\n", "MDT_EXCLUDED_CPU_CORES=0\nDEV_CPU_RESERVE_CORES=1\n")
        rc, out = self._validate(text)
        self.assertEqual(rc, 2, out)
        self.assertIn("DEV_CPU_RESERVE_CORES is no longer read: rename it to MDT_EXCLUDED_CPU_CORES", out)

    def test_the_validator_refuses_a_fractional_value_with_two_decimals(self) -> None:
        text = EXAMPLE.read_text().replace("MDT_EXCLUDED_CPU_CORES=0\n", "MDT_EXCLUDED_CPU_CORES=1.55\n")
        rc, out = self._validate(text)
        self.assertEqual(rc, 2, out)
        self.assertIn("MDT_EXCLUDED_CPU_CORES", out)

    def test_the_validator_accepts_fractional_values(self) -> None:
        text = EXAMPLE.read_text().replace("MDT_EXCLUDED_CPU_CORES=0\n", "MDT_EXCLUDED_CPU_CORES=0.5\n")
        rc, out = self._validate(text)
        self.assertEqual(rc, 0, out)

    def test_the_validator_refuses_when_less_than_one_gib_is_left_for_mdt(self) -> None:
        text = EXAMPLE.read_text().replace("MDT_EXCLUDED_RAM=0\n", "MDT_EXCLUDED_RAM=15500M\n")
        rc, out = self._validate(text)
        self.assertEqual(rc, 2, out)
        self.assertIn("at least 1G is required", out)

    def test_a_file_missing_a_new_key_is_refused_as_missing(self) -> None:
        text = EXAMPLE.read_text().replace("MDT_HOST_SWAP_MARGIN=512M\n", "")
        rc, out = self._validate(text)
        self.assertEqual(rc, 2, out)
        self.assertIn("MDT_HOST_SWAP_MARGIN", out)

    def test_the_wizard_refuses_an_existing_file_with_an_old_key(self) -> None:
        wizard = self._wizard()
        d = _tmp(self)
        output = d / "host-setup.env"
        original = "DEV_SUBSLICE_CPU_RESERVE_CORES=3\n"
        output.write_text(original)
        err = io.StringIO()
        with mock.patch.object(wizard, "host_context_error", return_value=None), \
             contextlib.redirect_stderr(err), contextlib.redirect_stdout(io.StringIO()):
            rc = wizard.main(["--example", str(EXAMPLE), "--output", str(output),
                              "--meminfo-path", str(d / "missing"), "--skip-run-offer"])
        self.assertEqual(rc, 1)
        self.assertIn("rename it to DEV_TIER_EXCLUDED_CPU_CORES", err.getvalue())
        self.assertEqual(output.read_text(), original)

    def test_check_sh_refuses_an_old_key(self) -> None:
        d = _tmp(self)
        conf = d / "host-setup.env"
        conf.write_text("DEV_CPU_RESERVE_CORES=1\n")
        result = subprocess.run(
            ["bash", str(CHECK)], capture_output=True, text=True, check=False,
            env={"PATH": "/usr/bin:/bin", "CONF": str(conf), "CG": str(d / "cg"),
                 "CAPACITY_PY": str(ROOT / "mdt_capacity.py")},
        )
        self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
        self.assertIn("DEV_CPU_RESERVE_CORES is no longer read: rename it to MDT_EXCLUDED_CPU_CORES", result.stderr)

    def test_install_sh_installs_the_capacity_module_and_reaches_the_validator_first(self) -> None:
        install = (ROOT / "install.sh").read_text()
        self.assertIn('install -m 0644 "$HERE/mdt_capacity.py"', install)
        # The main flow's call, not the first occurrence of the bare string (which is
        # inside admission_only() at line ~51 and precedes everything).
        self.assertLess(install.index('--validate-config "$CANDIDATE_PATH"'), install.index("mdt_capacity.py\" derive"))
        for old in MC.RENAMED_KEYS:
            self.assertNotIn(old, install)
            self.assertNotIn(old, CHECK.read_text())


class DerivedSizingTests(unittest.TestCase):
    def test_the_shared_host_worked_example(self) -> None:
        cap = MC.derive_capacity(
            mem_total=SHARED_MEM, swap_total=SHARED_SWAP, nproc=8,
            excluded_ram=6500 * MIB, excluded_swap=14 * GIB, excluded_cores=Decimal("1.5"),
        )
        self.assertEqual(cap.ram_mdt, SHARED_MEM - 6500 * MIB)
        self.assertAlmostEqual(cap.ram_mdt / GIB, 9.2, delta=0.05)
        self.assertEqual(cap.swap_mdt, 69 * GIB - 14 * GIB - 512 * MIB)
        self.assertEqual(MC.format_size(cap.swap_mdt), "55808M")  # 54.5 GiB
        self.assertAlmostEqual(cap.swap_mdt / GIB, 54.5)
        self.assertEqual(cap.dev_cpu_quota_pct, 650)
        self.assertEqual(cap.tier_cpu_quota_pct, 650)
        self.assertFalse(cap.no_disk_swap)

    def test_a_host_without_other_load(self) -> None:
        mem = int(7.75 * GIB)
        cap = MC.derive_capacity(mem_total=mem, swap_total=8 * GIB, nproc=4)
        self.assertEqual(cap.ram_mdt, mem)
        self.assertEqual(cap.swap_mdt, 8 * GIB - 512 * MIB)
        self.assertEqual(cap.dev_cpu_quota_pct, 400)
        self.assertEqual(cap.tier_cpu_quota_pct, 400)

    def test_tier_cpu_excludes_the_tier_cores_on_top(self) -> None:
        cap = MC.derive_capacity(mem_total=8 * GIB, swap_total=0, nproc=8,
                                 excluded_cores=Decimal("1"), tier_excluded_cores=Decimal("2.5"))
        self.assertEqual((cap.dev_cpu_quota_pct, cap.tier_cpu_quota_pct), (700, 450))

    def test_refuses_below_one_gib_of_ram_and_half_a_core(self) -> None:
        with self.assertRaisesRegex(MC.CapacityError, "at least 1G"):
            MC.derive_capacity(mem_total=8 * GIB, swap_total=0, nproc=4, excluded_ram=7500 * MIB)
        MC.derive_capacity(mem_total=8 * GIB, swap_total=0, nproc=4, excluded_ram=7 * GIB)  # exactly 1G is allowed
        with self.assertRaisesRegex(MC.CapacityError, "at least 0.5"):
            MC.derive_capacity(mem_total=8 * GIB, swap_total=0, nproc=4, excluded_cores=Decimal("3.6"))
        MC.derive_capacity(mem_total=8 * GIB, swap_total=0, nproc=4, excluded_cores=Decimal("3.5"))

    def test_no_positive_swap_warns_and_gives_mdt_none(self) -> None:
        cap = MC.derive_capacity(mem_total=8 * GIB, swap_total=2 * GIB, nproc=4,
                                 excluded_swap=int(1.75 * GIB))
        self.assertTrue(cap.no_disk_swap)
        self.assertEqual(cap.swap_mdt_bytes, 0)
        self.assertTrue(any("MDT gets no disk swap" in w for w in cap.warnings))

    def test_tiers_overcommit_ram_by_design(self) -> None:
        ram = 10 * GIB
        sizes = MC.tier_memory_bytes(ram)
        self.assertEqual(sizes["DEV_MEMORY_MAX"], ram)
        self.assertEqual(sizes["DEV_MEMORY_HIGH"], ram * 92 // 100)
        for prefix, (high, maximum) in {
            "DEV_INTERACTIVE": (45, 60), "DEV_BACKGROUND": (50, 70),
            "DEV_GATES": (50, 70), "DEV_BUILDKITD": (25, 35),
        }.items():
            self.assertEqual(sizes[f"{prefix}_MEMORY_HIGH"], ram * high // 100)
            self.assertEqual(sizes[f"{prefix}_MEMORY_MAX"], ram * maximum // 100)
        self.assertFalse([k for k in sizes if k.endswith("_MEMORY_LOW")])
        tier_max_sum = sum(sizes[f"{p}_MEMORY_MAX"] for p in ("DEV_INTERACTIVE", "DEV_BACKGROUND", "DEV_GATES", "DEV_BUILDKITD"))
        self.assertGreater(tier_max_sum, ram)

    def test_tier_swap_is_the_cascade_percentage_of_swap_mdt(self) -> None:
        self.assertEqual(MC.tier_swap_bytes(10 * GIB, 80), 8 * GIB)
        self.assertEqual(MC.tier_swap_bytes(-5, 80), 0)

    def test_the_wizard_proposals_follow_ram_minus_the_excluded_ram(self) -> None:
        wizard = _load(WIZARD_PATH, "mdt_wizard_excluded_capacity_tiers")
        total_kib = 16 * 1024 * 1024
        plain = wizard.propose_memory_tiers(total_kib, 0)
        reduced = wizard.propose_memory_tiers(total_kib, 0, 8 * 1024 * 1024)
        self.assertEqual(plain["DEV_MEMORY_MAX"], "16G")
        self.assertEqual(reduced["DEV_MEMORY_MAX"], "8G")
        self.assertEqual(reduced["DEV_MEMORY_HIGH"], "7.5G")
        self.assertEqual(reduced["DEV_BACKGROUND_MEMORY_MAX"], "5.5G")

    def test_the_cli_derives_from_fake_meminfo_and_prints_install_variables(self) -> None:
        d = _tmp(self)
        (d / "meminfo").write_text("MemTotal: 16318464 kB\nSwapTotal: %d kB\n" % (69 * GIB // 1024))
        (d / "env").write_text(
            "MDT_EXCLUDED_RAM=6500M\nMDT_EXCLUDED_SWAP=14G\nMDT_EXCLUDED_CPU_CORES=1.5\n"
            "DEV_TIER_EXCLUDED_CPU_CORES=0\nMDT_HOST_SWAP_MARGIN=512M\n"
        )
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            rc = MC.main(["derive", str(d / "env"), "--meminfo", str(d / "meminfo"), "--nproc", "8"])
        self.assertEqual(rc, 0)
        lines = dict(line.split("=", 1) for line in buf.getvalue().splitlines())
        self.assertEqual(lines["MDT_DEV_CPU_QUOTA"], "650%")
        self.assertEqual(int(lines["MDT_SWAP_BYTES"]), 69 * GIB - 14 * GIB - 512 * MIB)
        self.assertEqual(lines["MDT_NO_DISK_SWAP"], "0")

    def test_the_cli_reports_no_disk_swap_and_refusals(self) -> None:
        d = _tmp(self)
        (d / "meminfo").write_text("MemTotal: 8388608 kB\nSwapTotal: 524288 kB\n")  # 512M of swap
        (d / "env").write_text("MDT_HOST_SWAP_MARGIN=512M\n")
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf), contextlib.redirect_stderr(io.StringIO()):
            self.assertEqual(MC.main(["derive", str(d / "env"), "--meminfo", str(d / "meminfo"), "--nproc", "4"]), 0)
        self.assertIn("MDT_NO_DISK_SWAP=1", buf.getvalue())
        (d / "env").write_text("MDT_EXCLUDED_CPU_CORES=4\n")
        with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
            self.assertEqual(MC.main(["derive", str(d / "env"), "--meminfo", str(d / "meminfo"), "--nproc", "4"]), 2)

    def test_install_sh_requires_confirmation_before_giving_mdt_no_disk_swap(self) -> None:
        install = (ROOT / "install.sh").read_text()
        self.assertIn("--confirm-no-disk-swap", install)
        self.assertIn("MDT_NO_DISK_SWAP", install)
        self.assertIn("re-run with --confirm-no-disk-swap", install)


def _fake_cgroups(root: Path) -> Path:
    """The shared host's shape: wings.slice memory.min 6500M with scopes 4000M + 3000M."""
    def node(path: Path, **files: str) -> Path:
        path.mkdir(parents=True, exist_ok=True)
        for name, content in files.items():
            (path / name.replace("_", ".", 1) if name.startswith(("memory_", "cpu_", "io_")) else path / name).write_text(content + "\n")
        return path

    cg = root / "cg"
    node(cg / "dev.slice", cpu_weight="100", io_weight="default 100")
    wings = node(cg / "wings.slice", memory_min=str(6500 * MIB), cpu_weight="800", io_weight="default 800",
                 memory_current=str(7000 * MIB))
    node(wings / "docker-a.scope", memory_min=str(4000 * MIB), memory_current=str(3500 * MIB))
    node(wings / "docker-b.scope", memory_min=str(3000 * MIB), memory_current=str(3000 * MIB))
    return cg


class ProtectionTests(unittest.TestCase):
    def test_children_exceeding_the_parent_are_reported_and_scaled(self) -> None:
        cg = _fake_cgroups(_tmp(self))
        warnings = MC.verify_protection(cg, ["wings.slice"], 6500 * MIB)
        self.assertTrue(any("children's memory.min sum 7000M exceeds the parent's 6500M" in w for w in warnings), warnings)
        # 6500M of the parent's min is honoured; the scopes are scaled down inside it
        self.assertEqual(MC.effective_memory_min(cg, cg / "wings.slice"), 6500 * MIB)
        self.assertAlmostEqual(MC.effective_memory_min(cg, cg / "wings.slice" / "docker-a.scope") / MIB, 4000 * 6500 / 7000, delta=1)

    def test_a_sum_below_the_excluded_ram_is_reported(self) -> None:
        cg = _fake_cgroups(_tmp(self))
        warnings = MC.verify_protection(cg, ["wings.slice"], 9000 * MIB)
        self.assertTrue(any("is 6500M, below MDT_EXCLUDED_RAM 9000M" in w for w in warnings), warnings)

    def test_a_fully_protected_host_is_clean(self) -> None:
        cg = _fake_cgroups(_tmp(self))
        (cg / "wings.slice" / "docker-a.scope" / "memory.min").write_text(str(3500 * MIB) + "\n")
        (cg / "wings.slice" / "docker-b.scope" / "memory.min").write_text(str(3000 * MIB) + "\n")
        self.assertEqual(MC.verify_protection(cg, ["wings.slice"], 6500 * MIB), [])

    def test_a_weight_below_four_times_dev_slice_is_reported(self) -> None:
        cg = _fake_cgroups(_tmp(self))
        (cg / "wings.slice" / "cpu.weight").write_text("399\n")
        (cg / "wings.slice" / "io.weight").write_text("default 100\n")
        warnings = MC.verify_protection(cg, ["wings.slice"], 0)
        self.assertTrue(any("CPUWeight 399 is below 4x that of dev.slice (100)" in w for w in warnings), warnings)
        self.assertTrue(any("IOWeight 100 is below 4x" in w for w in warnings), warnings)

    def test_a_parent_without_memory_min_protects_nothing_below_it(self) -> None:
        cg = _fake_cgroups(_tmp(self))
        (cg / "wings.slice" / "memory.min").write_text("0\n")
        self.assertEqual(MC.effective_memory_min(cg, cg / "wings.slice" / "docker-a.scope"), 0)

    def test_a_missing_unit_is_reported_and_no_units_means_no_warnings(self) -> None:
        cg = _fake_cgroups(_tmp(self))
        self.assertTrue(any("ghost.slice has no cgroup" in w for w in MC.verify_protection(cg, ["ghost.slice"], 0)))
        self.assertEqual(MC.verify_protection(cg, [], 5 * GIB), [])

    def test_a_nested_named_unit_is_counted_once(self) -> None:
        cg = _fake_cgroups(_tmp(self))
        warnings = MC.verify_protection(cg, ["wings.slice", "docker-a.scope"], 6500 * MIB)
        self.assertFalse(any("below MDT_EXCLUDED_RAM" in w for w in warnings), warnings)

    def test_the_cli_prints_warn_lines_and_writes_nothing(self) -> None:
        d = _tmp(self)
        cg = _fake_cgroups(d)
        env = d / "env"
        env.write_text("MDT_PROTECTED_UNITS=wings.slice\nMDT_EXCLUDED_RAM=9000M\n")
        before = sorted(str(p) for p in d.rglob("*"))
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            self.assertEqual(MC.main(["verify-protection", str(env), "--cgroup-root", str(cg)]), 0)
        self.assertIn("WARN effective memory.min", buf.getvalue())
        self.assertEqual(before, sorted(str(p) for p in d.rglob("*")))

    def test_check_sh_surfaces_the_protection_warnings(self) -> None:
        d = _tmp(self)
        cg = _fake_cgroups(d)
        conf = d / "host-setup.env"
        conf.write_text("MDT_PROTECTED_UNITS=wings.slice\nMDT_EXCLUDED_RAM=9000M\n")
        result = subprocess.run(
            ["bash", str(CHECK)], capture_output=True, text=True, check=False,
            env={"PATH": "/usr/bin:/bin", "CONF": str(conf), "CG": str(cg),
                 "CAPACITY_PY": str(ROOT / "mdt_capacity.py")},
        )
        self.assertIn("== protected units (MDT_PROTECTED_UNITS; verify only) ==", result.stdout)
        self.assertRegex(result.stdout, r"WARN .*is 6500M, below MDT_EXCLUDED_RAM 9000M")


class CheckConfigWarningTests(unittest.TestCase):
    """F4/F8: dev.slice MemoryMax vs RAM_MDT, and the no-disk-swap warning, both WARN only."""

    def _run(self, env_text: str, swap_kib: int = 8 * 1024 * 1024) -> tuple[int, str, str]:
        d = _tmp(self)
        env = d / "env"
        env.write_text(env_text)
        meminfo = d / "meminfo"
        meminfo.write_text(f"MemTotal: {16 * 1024 * 1024} kB\nSwapTotal: {swap_kib} kB\n")
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            rc = MC.main(["check-config", str(env), "--meminfo", str(meminfo), "--nproc", "4"])
        return rc, out.getvalue(), err.getvalue()

    def test_a_dev_memory_max_above_ram_mdt_warns(self) -> None:
        rc, out, _ = self._run("MDT_EXCLUDED_RAM=6G\nDEV_MEMORY_MAX=14G\n")
        self.assertEqual(rc, 0)
        self.assertRegex(out, r"WARN dev\.slice MemoryMax exceeds the RAM MDT may use: DEV_MEMORY_MAX=14G is above RAM_MDT 10G")

    def test_an_empty_dev_memory_max_with_excluded_ram_warns(self) -> None:
        rc, out, _ = self._run("MDT_EXCLUDED_RAM=6G\nDEV_MEMORY_MAX=\n")
        self.assertEqual(rc, 0)
        self.assertIn("WARN dev.slice has no MemoryMax", out)
        self.assertIn("at most 10G", out)

    def test_no_warning_when_it_fits_or_nothing_is_excluded(self) -> None:
        self.assertEqual(self._run("MDT_EXCLUDED_RAM=6G\nDEV_MEMORY_MAX=10G\n")[1], "")
        self.assertEqual(self._run("MDT_EXCLUDED_RAM=0\nDEV_MEMORY_MAX=\n")[1], "")

    def test_the_no_disk_swap_warning_is_reported(self) -> None:
        _, out, _ = self._run("MDT_EXCLUDED_SWAP=8G\nMDT_EXCLUDED_RAM=0\nDEV_MEMORY_MAX=16G\n")
        self.assertIn("MDT gets no disk swap", out)

    def test_install_derive_prints_the_memory_warning_without_refusing(self) -> None:
        d = _tmp(self)
        env = d / "env"
        env.write_text("MDT_EXCLUDED_RAM=6G\nDEV_MEMORY_MAX=14G\n")
        meminfo = d / "meminfo"
        meminfo.write_text(f"MemTotal: {16 * 1024 * 1024} kB\nSwapTotal: {8 * 1024 * 1024} kB\n")
        err = io.StringIO()
        with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(err):
            rc = MC.main(["derive", str(env), "--meminfo", str(meminfo), "--nproc", "4"])
        self.assertEqual(rc, 0)
        self.assertIn("WARN: dev.slice MemoryMax exceeds the RAM MDT may use", err.getvalue())

    def test_check_sh_has_the_section_and_warns_when_the_helper_is_missing(self) -> None:
        text = CHECK.read_text()
        self.assertIn("check-config", text)
        d = _tmp(self)
        conf = d / "host-setup.env"
        conf.write_text("MDT_EXCLUDED_RAM=0\n")
        result = subprocess.run(
            ["bash", str(CHECK)], capture_output=True, text=True, check=False,
            env={"PATH": "/usr/bin:/bin", "CONF": str(conf), "CG": str(d / "cg"),
                 "CAPACITY_PY": str(d / "missing.py")},
        )
        self.assertRegex(result.stdout, r"WARN .*mdt_capacity\.py|WARN .*missing\.py missing")


class VmUpgradeAcceptanceTests(unittest.TestCase):
    def test_the_vm_upgrade_test_has_an_old_key_refusal_step(self) -> None:
        text = (ROOT / "tests" / "vm-upgrade-acceptance.sh").read_text()
        self.assertIn("DEV_CPU_RESERVE_CORES=1", text)
        self.assertIn("expected 2", text)
        self.assertIn("rename it to MDT_EXCLUDED_CPU_CORES", text)
        msg = MC.renamed_key_errors({"DEV_CPU_RESERVE_CORES": "1"})[0]
        self.assertIn("DEV_CPU_RESERVE_CORES is no longer read: rename it to MDT_EXCLUDED_CPU_CORES", msg)
        subprocess.run(["bash", "-n", str(ROOT / "tests" / "vm-upgrade-acceptance.sh")], check=True)


class ProbeTests(unittest.TestCase):
    """The probe reads fake cgroups only and suggests; it never writes."""

    def _usage_tree(self, root: Path, protected: bool) -> Path:
        cg = root / "cg"
        unit = cg / "system.slice" / "game.service"
        unit.mkdir(parents=True)
        (unit / "memory.current").write_text(f"{2000 * MIB}\n")
        (unit / "memory.swap.current").write_text(f"{700 * MIB}\n")
        (unit / "memory.stat").write_text(f"anon {1500 * MIB}\nfile {400 * MIB}\n")
        (unit / "cpu.stat").write_text("usage_usec 0\n")
        (unit / "cpu.weight").write_text("100\n")
        (unit / "io.weight").write_text("default 100\n")
        if protected:
            (cg / "system.slice" / "memory.min").write_text(f"{3000 * MIB}\n")
            (unit / "memory.min").write_text(f"{3000 * MIB}\n")
        return cg

    def _run(self, cg: Path, *, seconds: float = 40, interval: float = 5):
        game = cg / "system.slice" / "game.service"

        def burn(i: int) -> None:  # 1.2 cores of CPU for every 5 s sample, a swap peak at sample 3
            (game / "cpu.stat").write_text(f"usage_usec {int(i * interval * 1.2 * 1e6)}\n")
            (game / "memory.swap.current").write_text(f"{(1700 if i == 3 else 700) * MIB}\n")

        return MC.probe_units(cg, ["game.service"], seconds=seconds, interval=interval, on_sample=burn)

    def test_with_memory_min_the_ram_is_the_effective_min_rounded_up(self) -> None:
        cg = self._usage_tree(_tmp(self), protected=True)
        result = self._run(cg)
        self.assertEqual(result.ram, 3072 * MIB)  # 3000M rounded up to 256M
        self.assertTrue(result.ram_protected)
        self.assertEqual(result.swap, 2 * GIB)  # peak 1700M rounded up to 512M
        self.assertEqual(result.cpu_cores, 1.5)  # p95 1.2 cores rounded up to 0.5

    def test_without_memory_min_the_ram_is_the_p95_flagged_unprotected(self) -> None:
        cg = self._usage_tree(_tmp(self), protected=False)
        result = self._run(cg)
        self.assertEqual(result.ram, 2048 * MIB)  # 2000M rounded up to 256M
        self.assertFalse(result.ram_protected)
        table = MC.format_probe(result)
        self.assertIn("unprotected", table)
        self.assertIn("NO (no memory.min)", table)

    def test_the_probe_writes_nothing_and_reports_the_protection_warnings(self) -> None:
        d = _tmp(self)
        cg = self._usage_tree(d, protected=False)
        before = {p: p.read_text() for p in cg.rglob("*") if p.is_file()}
        result = self._run(cg)
        # the injected sampler wrote to the fake tree; the probe itself changes nothing else
        after = {p: p.read_text() for p in cg.rglob("*") if p.is_file()}
        self.assertEqual(set(before), set(after))
        text = MC.format_probe(result, 3000 * MIB, cg, ["game.service"])
        self.assertIn("below MDT_EXCLUDED_RAM", text)
        self.assertIn("Suggested numbers (nothing is applied)", text)

    def test_a_missing_unit_is_skipped_with_a_warning(self) -> None:
        cg = _tmp(self) / "cg"
        cg.mkdir()
        result = MC.probe_units(cg, ["ghost.service"], seconds=0)
        self.assertEqual(result.rows, [])
        self.assertTrue(any("ghost.service" in w for w in result.warnings))

    def _wizard_args(self, d: Path, cg: Path, *extra: str):
        wizard = _load(WIZARD_PATH, "mdt_wizard_excluded_capacity_probe")
        args = wizard.parse_cli_args(
            ["--probe-excluded", "--seconds", "0", "--cgroup-root", str(cg), "--output", str(d / "host-setup.env"), *extra]
        )
        return wizard, args

    def test_the_wizard_probe_prints_suggestions_and_leaves_the_config_untouched(self) -> None:
        d = _tmp(self)
        cg = self._usage_tree(d, protected=True)
        config = EXAMPLE.read_text()
        (d / "host-setup.env").write_text(config)
        wizard, args = self._wizard_args(d, cg, "--units", "game.service")
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            self.assertEqual(wizard.run_probe_excluded(args), 0)
        self.assertIn("MDT_EXCLUDED_RAM=3G", buf.getvalue())
        self.assertIn("Nothing was written", buf.getvalue())
        self.assertEqual((d / "host-setup.env").read_text(), config)

    def test_accepting_writes_only_the_three_numbers_and_the_units(self) -> None:
        d = _tmp(self)
        cg = self._usage_tree(d, protected=True)
        config = EXAMPLE.read_text()
        (d / "host-setup.env").write_text(config)
        wizard, args = self._wizard_args(d, cg, "--units", "game.service", "--accept-probe")
        with contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(wizard.run_probe_excluded(args), 0)
        new = (d / "host-setup.env").read_text()
        values = wizard.parse_env_file(new)
        self.assertEqual(values["MDT_EXCLUDED_RAM"], "3G")
        self.assertEqual(values["MDT_PROTECTED_UNITS"], "game.service")
        changed = [k for k, v in wizard.parse_env_file(config).items() if values[k] != v]
        # a zero-length window sees no CPU delta, so the cores stay 0
        self.assertEqual(sorted(changed), ["MDT_EXCLUDED_RAM", "MDT_EXCLUDED_SWAP", "MDT_PROTECTED_UNITS"])
        self.assertEqual(values["MDT_EXCLUDED_CPU_CORES"], "0")

    def test_accept_needs_an_existing_file_and_units_are_required(self) -> None:
        d = _tmp(self)
        cg = self._usage_tree(d, protected=True)
        wizard, args = self._wizard_args(d, cg, "--units", "game.service", "--accept-probe")
        with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
            self.assertEqual(wizard.run_probe_excluded(args), 1)
        self.assertFalse((d / "host-setup.env").exists())
        wizard, args = self._wizard_args(d, cg)
        with contextlib.redirect_stderr(io.StringIO()):
            self.assertEqual(wizard.run_probe_excluded(args), 2)


def _weak_wings(cg: Path, *, memory_min: int = 0, cpu_weight: int = 100, io_weight: int = 100) -> None:
    """Lower wings.slice's own protection so a plan has something to raise."""
    w = cg / "wings.slice"
    (w / "memory.min").write_text(f"{memory_min}\n")
    (w / "cpu.weight").write_text(f"{cpu_weight}\n")
    (w / "io.weight").write_text(f"default {io_weight}\n")


class ApplyProtectionTests(unittest.TestCase):
    def test_the_plan_mirrors_the_excluded_ram_and_uses_four_times_the_dev_weights(self) -> None:
        cg = _fake_cgroups(_tmp(self))
        _weak_wings(cg)
        plan = MC.plan_protection(cg, ["wings.slice"], 6500 * MIB)
        self.assertEqual(plan, {"wings.slice": {
            "MemoryMin": ("0", "6500M"), "CPUWeight": ("100", "400"), "IOWeight": ("100", "400")}})

    def test_the_ram_is_split_by_current_use_across_several_units(self) -> None:
        d = _tmp(self)
        cg = _fake_cgroups(d)
        _weak_wings(cg)
        svc = cg / "system.slice" / "game.service"
        svc.mkdir(parents=True)
        (svc / "memory.current").write_text(str(1000 * MIB) + "\n")
        (svc / "memory.min").write_text("0\n")
        (svc / "cpu.weight").write_text("100\n")
        plan = MC.plan_protection(cg, ["wings.slice", "game.service"], 8000 * MIB)
        self.assertEqual(plan["wings.slice"]["MemoryMin"], ("0", "7000M"))
        self.assertEqual(plan["game.service"]["MemoryMin"], ("0", "1000M"))

    def test_it_never_lowers_an_existing_memory_min_or_weight(self) -> None:
        cg = _fake_cgroups(_tmp(self))
        # wings.slice already has MemoryMin 6500M, CPU/IO weight 800: a smaller figure and the
        # 4x-dev weights (400) must not replace them.
        notes: list[str] = []
        plan = MC.plan_protection(cg, ["wings.slice"], 2000 * MIB, notes=notes)
        self.assertEqual(plan, {})
        self.assertTrue(any("nothing would increase" in n for n in notes), notes)

    def test_a_partial_increase_lists_only_the_properties_that_rise(self) -> None:
        cg = _fake_cgroups(_tmp(self))
        _weak_wings(cg, memory_min=8000 * MIB, cpu_weight=1000, io_weight=100)
        plan = MC.plan_protection(cg, ["wings.slice"], 6500 * MIB)
        self.assertEqual(plan, {"wings.slice": {"IOWeight": ("100", "400")}})

    def test_a_proportional_share_never_lowers_a_larger_current_min(self) -> None:
        d = _tmp(self)
        cg = _fake_cgroups(d)  # wings.slice: min 6500M, current 7000M
        svc = cg / "system.slice" / "docker.service"
        svc.mkdir(parents=True)
        (svc / "memory.current").write_text(str(7000 * MIB) + "\n")
        (svc / "memory.min").write_text("0\n")
        plan = MC.plan_protection(cg, ["wings.slice", "docker.service"], 8000 * MIB)
        self.assertNotIn("wings.slice", plan)  # its share (4000M) is below its 6500M
        self.assertEqual(plan["docker.service"]["MemoryMin"], ("0", "4000M"))

    def test_zero_or_unparsable_excluded_ram_is_refused_and_writes_nothing(self) -> None:
        d = _tmp(self)
        cg = _fake_cgroups(d)
        _weak_wings(cg)
        with self.assertRaises(MC.ProtectionRefused):
            MC.plan_protection(cg, ["wings.slice"], 0)
        sysd = d / "systemd"
        for ram_line in ("MDT_EXCLUDED_RAM=0\n", "", "MDT_EXCLUDED_RAM=lots\n"):
            env = d / "env"
            env.write_text("MDT_PROTECTED_UNITS=wings.slice\n" + ram_line)
            with contextlib.redirect_stderr(io.StringIO()):
                rc = MC.main(["apply-protection", str(env), "--yes", "--systemd-dir", str(sysd),
                              "--cgroup-root", str(cg)])
            self.assertEqual(rc, 2, ram_line)
            self.assertFalse(sysd.exists(), ram_line)

    def test_nothing_to_raise_writes_nothing_even_with_yes(self) -> None:
        d = _tmp(self)
        cg = _fake_cgroups(d)
        env = d / "env"
        env.write_text("MDT_PROTECTED_UNITS=wings.slice\nMDT_EXCLUDED_RAM=2000M\n")
        sysd = d / "systemd"
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            rc = MC.main(["apply-protection", str(env), "--yes", "--systemd-dir", str(sysd), "--cgroup-root", str(cg)])
        self.assertEqual(rc, 0)
        self.assertFalse(sysd.exists())
        self.assertIn("Nothing would increase", out.getvalue())

    def test_the_confirmation_shows_current_and_new_values(self) -> None:
        d = _tmp(self)
        cg = _fake_cgroups(d)
        _weak_wings(cg, memory_min=1000 * MIB)
        env = d / "env"
        env.write_text("MDT_PROTECTED_UNITS=wings.slice\nMDT_EXCLUDED_RAM=6500M\n")
        out = io.StringIO()
        with mock.patch("builtins.input", return_value="n"), contextlib.redirect_stdout(out):
            MC.main(["apply-protection", str(env), "--systemd-dir", str(d / "s"), "--cgroup-root", str(cg)])
        self.assertIn("MemoryMin: 1000M -> 6500M", out.getvalue())
        self.assertIn("CPUWeight: 100 -> 400", out.getvalue())

    def test_a_missing_cgroup_is_skipped_with_a_note_not_written(self) -> None:
        cg = _fake_cgroups(_tmp(self))
        notes: list[str] = []
        self.assertEqual(MC.plan_protection(cg, ["ghost.service"], 1000 * MIB, notes=notes), {})
        self.assertTrue(any("ghost.service" in n and "skipped" in n for n in notes), notes)

    def test_only_slice_service_and_scope_units_are_accepted(self) -> None:
        for good in ("wings.slice", "docker.service", "docker-a.scope"):
            self.assertIsNone(MC.validate_units(good))
        for bad in ("docker.socket", "data.mount", "multi-user.target"):
            self.assertIsNotNone(MC.validate_units(bad), bad)

    def test_it_writes_only_after_confirmation_and_only_for_named_units(self) -> None:
        d = _tmp(self)
        cg = _fake_cgroups(d)
        _weak_wings(cg)
        env = d / "env"
        env.write_text("MDT_PROTECTED_UNITS=wings.slice\nMDT_EXCLUDED_RAM=6500M\n")
        sysd = d / "systemd"
        argv = ["apply-protection", str(env), "--systemd-dir", str(sysd), "--cgroup-root", str(cg)]
        with mock.patch("builtins.input", return_value="n"), contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(MC.main(argv), 1)
        self.assertFalse(sysd.exists())
        with mock.patch("builtins.input", side_effect=EOFError), contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(MC.main(argv), 1)
        self.assertFalse(sysd.exists())
        with mock.patch("builtins.input", return_value="y"), contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(MC.main(argv), 0)
        files = sorted(p.relative_to(sysd).as_posix() for p in sysd.rglob("*") if p.is_file())
        self.assertEqual(files, ["wings.slice.d/50-mdt-protection.conf"])
        text = (sysd / "wings.slice.d" / "50-mdt-protection.conf").read_text()
        self.assertIn("[Slice]\nMemoryMin=6500M\nCPUWeight=400\nIOWeight=400\n", text)

    def test_an_empty_unit_list_is_refused(self) -> None:
        d = _tmp(self)
        env = d / "env"
        env.write_text("MDT_PROTECTED_UNITS=\n")
        with contextlib.redirect_stderr(io.StringIO()):
            self.assertEqual(MC.main(["apply-protection", str(env), "--yes", "--systemd-dir", str(d / "s")]), 2)

    def test_install_sh_runs_it_stand_alone_and_opt_in(self) -> None:
        install = (ROOT / "install.sh").read_text()
        self.assertIn("--apply-protection", install)
        block = install[install.index('if [ "$APPLY_PROTECTION" = 1 ]; then'):]
        block = block[: block.index("fi\n\n")]
        self.assertIn("refuse-renamed", block)
        self.assertIn("exit 0", block)


class WordingTests(unittest.TestCase):
    """No key name, prompt or help text calls the excluded capacity a reserve."""

    ALLOWED = (
        "RENAMED_KEYS", "_OLD_ROOT_DEFAULT", "_ANY_RESERVE_KEY",
        '"DEV_CPU_RESERVE_CORES"', '"DEV_SUBSLICE_CPU_RESERVE_CORES"', '"DEV_MEMORY_RESERVE"',
        "*_RESERVE_*", "retired default", "Removed outright",
    )

    def _hits(self, path: Path) -> list[str]:
        return [
            f"{path.name}:{n}: {line.strip()}"
            for n, line in enumerate(path.read_text().splitlines(), 1)
            if re.search(r"\breserve", line, re.IGNORECASE) and not any(a in line for a in self.ALLOWED)
        ]

    def test_no_reserve_wording_in_keys_prompts_or_help(self) -> None:
        files = [EXAMPLE, ROOT / "mdt_capacity.py", ROOT / "install.sh", CHECK,
                 *sorted((ROOT / "units").glob("*.in"))]
        for path in files:
            hits = [h for h in self._hits(path) if "reserved" not in h]  # BuildKit's own reserved builder prefixes
            self.assertEqual(hits, [], hits)
        wizard = [h for h in self._hits(WIZARD_PATH) if "reserved" not in h]
        self.assertEqual(wizard, [], wizard)

    def test_no_example_or_validator_key_contains_reserve(self) -> None:
        keys = re.findall(r"(?m)^([A-Z][A-Z0-9_]*)=", EXAMPLE.read_text())
        self.assertFalse([k for k in keys if "RESERVE" in k])
        wizard = _load(WIZARD_PATH, "mdt_wizard_excluded_capacity_keys")
        self.assertFalse([k for k in wizard._config_value_validators() if "RESERVE" in k])

    def test_the_prompt_texts_match_the_decision_record(self) -> None:
        text = WIZARD_PATH.read_text()
        for phrase in (
            "RAM unavailable to MDT \\u2014 used by other services on this host (0 if none)",
            "Disk swap unavailable to MDT \\u2014 swap space other services on this host occupy (0 if none)",
            "Swap space on the swap device that these services use.",
            "CPU cores unavailable to MDT \\u2014 used by other services on this host, fractions allowed (0 on a host without other load)",
            "Host/production units to verify and probe (space-separated, empty if none)",
        ):
            self.assertIn(phrase, text)
        cap = MC.derive_capacity(mem_total=8 * GIB, swap_total=4 * GIB, nproc=4)
        self.assertEqual(
            MC.summary_line(cap),
            "MDT may use: RAM 8G (overcommitted across tiers), swap 3584M, CPU 4 cores. "
            "Everything else is unavailable to MDT.",
        )


class DocsInSyncTests(unittest.TestCase):
    def test_the_readme_documents_the_rename_table_once(self) -> None:
        text = README.read_text()
        self.assertEqual(text.count("## Renamed keys (2026-11)"), 1)
        section = text[text.index("## Renamed keys (2026-11)"):]
        for old, (new, _) in MC.RENAMED_KEYS.items():
            self.assertIn(f"`{old}", section)
            self.assertIn(f"`{new}`", section)

    def test_the_readme_mentions_every_new_key_and_flag(self) -> None:
        text = README.read_text()
        for key in MC.NEW_KEYS + ("--apply-protection", "--confirm-no-disk-swap", "--probe-excluded"):
            self.assertIn(key, text)

    def test_the_old_keys_survive_nowhere_outside_the_rename_table_and_the_refusal_code(self) -> None:
        for path in (EXAMPLE, ROOT / "units" / "dev-buildkitd.slice.in", ROOT / "install.sh",
                     WIZARD_PATH, CHECK, ROOT / "cgroup-cpu-swap-redesign-HANDOFF.md"):
            for old in MC.RENAMED_KEYS:
                self.assertNotIn(old, path.read_text(), f"{old} in {path.name}")


if __name__ == "__main__":
    unittest.main()
