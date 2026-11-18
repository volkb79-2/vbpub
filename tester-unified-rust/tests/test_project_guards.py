from __future__ import annotations

import os
import shutil
import subprocess
import tempfile
import tomllib
import unittest
from pathlib import Path

HOSTOBS_ROOT = Path(os.environ["HOSTOBS_ROOT"]).resolve()


class WorkspaceLintTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.copy = Path(self.temp.name) / "hostobs"
        shutil.copytree(
            HOSTOBS_ROOT,
            self.copy,
            ignore=shutil.ignore_patterns("target", ".git", ".assay", ".run-gate"),
        )

    def tearDown(self) -> None:
        self.temp.cleanup()

    def run_guard(self) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            ["python3", str(self.copy / "tools/check-lints.py"), str(self.copy)],
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            check=False,
        )

    def test_current_workspace_passes_static_lints(self) -> None:
        result = self.run_guard()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("9 workspace packages", result.stdout)

    def test_workspace_must_forbid_unsafe_code(self) -> None:
        path = self.copy / "Cargo.toml"
        source = path.read_text(encoding="utf-8")
        path.write_text(source.replace('unsafe_code = "forbid"', 'unsafe_code = "warn"'), encoding="utf-8")
        result = self.run_guard()
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('[workspace.lints.rust] must set unsafe_code = "forbid"', result.stderr)

    def test_workspace_member_set_must_match_the_m0_layout(self) -> None:
        path = self.copy / "Cargo.toml"
        source = path.read_text(encoding="utf-8")
        path.write_text(
            source.replace('    "crates/proto",', '    "crates/proto",\n    "crates/rogue",'),
            encoding="utf-8",
        )
        rogue = self.copy / "crates/rogue/Cargo.toml"
        rogue.parent.mkdir(parents=True)
        rogue.write_text(
            '[package]\nname = "rogue"\nversion = "0.1.0"\nedition = "2021"\n',
            encoding="utf-8",
        )
        result = self.run_guard()
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("workspace package paths differ from the M0 layout", result.stderr)

    def test_package_must_inherit_workspace_lints(self) -> None:
        path = self.copy / "crates/proto/Cargo.toml"
        path.write_text(path.read_text(encoding="utf-8").replace("workspace = true", "workspace = false"), encoding="utf-8")
        result = self.run_guard()
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("must inherit [workspace.lints]", result.stderr)

    def test_non_allowlisted_crate_cannot_override_unsafe_policy(self) -> None:
        path = self.copy / "crates/proto/Cargo.toml"
        path.write_text(path.read_text(encoding="utf-8") + '\n[lints.rust]\nunsafe_code = "allow"\n', encoding="utf-8")
        result = self.run_guard()
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("must not override workspace unsafe_code", result.stderr)

    def test_allowlist_keeps_unsafe_policy_narrow(self) -> None:
        path = self.copy / "crates/kernel/Cargo.toml"
        path.write_text(path.read_text(encoding="utf-8").replace('unsafe_code = "allow"', 'unsafe_code = "warn"'), encoding="utf-8")
        result = self.run_guard()
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("must retain the plan's unsafe_code", result.stderr)

    def test_allowlisted_crate_cannot_inherit_and_override_workspace_lints(self) -> None:
        path = self.copy / "crates/kernel/Cargo.toml"
        source = path.read_text(encoding="utf-8")
        path.write_text(source.replace("[lints.rust]", "[lints]\nworkspace = true\n\n[lints.rust]"), encoding="utf-8")
        result = self.run_guard()
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("Cargo rejects this combination", result.stderr)

    def test_mutants_skip_attribute_is_forbidden(self) -> None:
        path = self.copy / "crates/proto/src/lib.rs"
        path.write_text(path.read_text(encoding="utf-8") + "\n#[mutants::skip]\n", encoding="utf-8")
        result = self.run_guard()
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("forbidden #[mutants::skip]", result.stderr)

    def test_write_capabilities_stay_in_act_or_store(self) -> None:
        path = self.copy / "crates/kernel/src/lib.rs"
        original = path.read_text(encoding="utf-8")
        for token in (
            ".write(true)",
            "O_WRONLY",
            "O_RDWR",
            "Command::new",
            "create_dir(",
            "create_dir_all(",
            "remove_dir(",
            "remove_dir_all(",
        ):
            with self.subTest(token=token):
                try:
                    path.write_text(original + f"\n// forbidden capability probe: {token}\n", encoding="utf-8")
                    result = self.run_guard()
                    self.assertNotEqual(result.returncode, 0)
                    self.assertIn("writes belong in", result.stderr)
                    self.assertIn(token, result.stderr)
                finally:
                    path.write_text(original, encoding="utf-8")

    def test_mutation_exclusion_requires_reason_comment(self) -> None:
        path = self.copy / ".cargo/mutants.toml"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text('[mutants]\nexclude_re = ["^known_equivalent$"]\n', encoding="utf-8")
        result = self.run_guard()
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("needs a preceding '# reason:' comment", result.stderr)


class CrateFloorTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.summary = self.root / "cov-summary.json"
        self.floors = HOSTOBS_ROOT / "coverage-floors.toml"
        self.packages = sorted(
            path.parent.relative_to(HOSTOBS_ROOT).as_posix()
            for pattern in ("crates/**/Cargo.toml", "bins/**/Cargo.toml")
            for path in HOSTOBS_ROOT.glob(pattern)
        )
        self.write_summary()

    def tearDown(self) -> None:
        self.temp.cleanup()

    def write_summary(self, low_metric: str | None = None) -> None:
        # Each package reports exactly its own registered floor (out of 100), so the guard's
        # "exactly at the floor passes" case follows coverage-floors.toml as floors are raised.
        floors = tomllib.loads(self.floors.read_text(encoding="utf-8"))["crates"]
        files = []
        for package in self.packages:
            source = next((HOSTOBS_ROOT / package).glob("src/*.rs"))
            # count 10000 keeps a fractional floor (e.g. 92.5) exact instead of rounding it away.
            lines_covered = 8000 if low_metric == f"{package}:lines" else round(floors[package]["line"] * 100)
            regions_covered = 8000 if low_metric == f"{package}:regions" else round(floors[package]["region"] * 100)
            files.append(
                {
                    "filename": str(source),
                    "summary": {
                        "lines": {"covered": lines_covered, "count": 10000, "percent": lines_covered / 100},
                        "regions": {"covered": regions_covered, "count": 10000, "percent": regions_covered / 100},
                    },
                }
            )
        self.summary.write_text(__import__("json").dumps({"data": [{"files": files}]}), encoding="utf-8")

    def run_floor_check(self) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            [
                "python3",
                str(HOSTOBS_ROOT / "tools/crate-floors.py"),
                str(self.summary),
                str(self.floors),
                "--workspace",
                str(HOSTOBS_ROOT),
            ],
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            check=False,
        )

    def test_exactly_at_both_floors_passes_for_every_package(self) -> None:
        result = self.run_floor_check()
        self.assertEqual(result.returncode, 0, result.stderr)
        floors = tomllib.loads(self.floors.read_text(encoding="utf-8"))["crates"]
        self.assertEqual(set(floors), set(self.packages))
        for package in self.packages:
            floor = floors[package]["line"]
            self.assertIn(f"line {floor:.2f}% ({round(floor * 100)}/10000) >= {floor:.2f}%", result.stdout)

    def test_line_floor_rejects_coverage_below_floor(self) -> None:
        package = self.packages[0]
        self.write_summary(f"{package}:lines")
        result = self.run_floor_check()
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("line coverage is 80.00%", result.stderr)

    def test_region_floor_rejects_coverage_below_floor(self) -> None:
        package = self.packages[0]
        self.write_summary(f"{package}:regions")
        result = self.run_floor_check()
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("region coverage is 80.00%", result.stderr)

    def test_unreported_workspace_package_is_refused(self) -> None:
        document = __import__("json").loads(self.summary.read_text(encoding="utf-8"))
        document["data"][0]["files"].pop()
        self.summary.write_text(__import__("json").dumps(document), encoding="utf-8")
        result = self.run_floor_check()
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("reported no source files for crate", result.stderr)

    def test_every_workspace_package_must_have_a_floor(self) -> None:
        source = self.floors.read_text(encoding="utf-8")
        table = '[crates."crates/act"]\nline = 90.0\nregion = 85.0\n\n'
        self.assertIn(table, source)
        self.floors = self.root / "coverage-floors.toml"
        self.floors.write_text(source.replace(table, "", 1), encoding="utf-8")
        result = self.run_floor_check()
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("packages without floors: crates/act", result.stderr)


if __name__ == "__main__":
    unittest.main()
