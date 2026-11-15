"""Exercise Assay's R1 adapter against real cargo tools in tester-unified-rust.

This is an executable qualification script, not a pytest test: the registered
Run-Gate lane supplies the Rust toolchain and invokes ``main`` directly.
"""

from __future__ import annotations

import argparse
import os
import re
import shutil
import subprocess
import tempfile
from pathlib import Path

from assay.adapters.rust import RustAdapter
from assay.coverage import check_empty_coverage, parse_coverage_artifact
from assay.failure_summary import first_failing_test


PROJECT_ROOT = Path(__file__).resolve().parents[1]
WORKSPACE = PROJECT_ROOT / "tests/fixtures/rust/r1-workspace"
EXPECTED_NEXTTEST = "assay-rust-fixture integration::forced_failure"
EXPECTED_LIBTEST = "integration::forced_failure"
NEXTEST_LINE = re.compile(
    r"^\s*FAIL \[\s*\d+(?:\.\d+)?s\]\s+(?:\(\s*\d+/\d+\)\s+)?"
    r"\S+(?:\s+\S+)?\s*$"
)
LIBTEST_LINE = re.compile(r"^\s*test \S+ \.\.\. FAILED\s*$")


def _run(command: list[str], *, cwd: Path, env: dict[str, str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        command,
        cwd=cwd,
        env=env,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        check=False,
    )


def _require_exit(result: subprocess.CompletedProcess[str], code: int, label: str) -> None:
    if result.returncode != code:
        raise AssertionError(
            f"{label} exited {result.returncode}, expected {code}:\n{result.stdout}"
        )


def _captured_line(output: str, pattern: re.Pattern[str], label: str) -> str:
    for line in output.splitlines():
        if pattern.fullmatch(line):
            return line
    raise AssertionError(f"real {label} failure output had no matching line:\n{output}")


def _fixture_line(path: Path, expected: str, label: str) -> None:
    actual = path.read_text(encoding="utf-8").strip()
    if first_failing_test(actual) != expected:
        raise AssertionError(f"captured {label} fixture no longer names {expected!r}: {actual!r}")


def qualify(*, capture_only: bool) -> None:
    adapter = RustAdapter()
    with tempfile.TemporaryDirectory(prefix="assay-rust-r1-") as temp_name:
        temp = Path(temp_name)
        workspace = temp / "workspace"
        shutil.copytree(WORKSPACE, workspace)
        env = os.environ.copy()
        env.update(
            {
                "CARGO_NET_OFFLINE": "true",
                "CARGO_TARGET_DIR": str(temp / "target"),
                "CARGO_TERM_COLOR": "never",
                "NO_COLOR": "1",
            }
        )

        nextest = _run(
            [
                "cargo", "nextest", "run", "--offline", "--locked",
                "--no-tests=fail", "--retries", "0",
            ],
            cwd=workspace,
            env={**env, "ASSAY_RUST_FIXTURE_FORCE_FAIL": "1"},
        )
        _require_exit(nextest, 100, "forced cargo nextest run")
        nextest_line = _captured_line(nextest.stdout, NEXTEST_LINE, "nextest")
        if first_failing_test(nextest.stdout) != EXPECTED_NEXTTEST:
            raise AssertionError(f"nextest witness mismatch:\n{nextest.stdout}")

        libtest = _run(
            ["cargo", "test", "--offline", "--locked", "--no-fail-fast"],
            cwd=workspace,
            env={**env, "ASSAY_RUST_FIXTURE_FORCE_FAIL": "1"},
        )
        _require_exit(libtest, 101, "forced cargo test run")
        libtest_line = _captured_line(libtest.stdout, LIBTEST_LINE, "libtest")
        if first_failing_test(libtest.stdout) != EXPECTED_LIBTEST:
            raise AssertionError(f"libtest witness mismatch:\n{libtest.stdout}")

        no_tests = _run(
            [
                "cargo", "nextest", "run", "--offline", "--locked",
                "--no-tests=fail", "--retries", "0", "--filter-expr",
                "test(no_assay_fixture_test_has_this_name)",
            ],
            cwd=workspace,
            env=env,
        )
        _require_exit(no_tests, 4, "cargo nextest empty selection")

        coverage_path = temp / "coverage.info"
        coverage = _run(
            [
                "cargo", "llvm-cov", "nextest", "--offline", "--locked",
                "--no-tests=fail", "--lcov", "--output-path", str(coverage_path),
            ],
            cwd=workspace,
            env=env,
        )
        _require_exit(coverage, 0, "cargo llvm-cov nextest")
        raw_coverage = coverage_path.read_bytes()
        profile = parse_coverage_artifact(
            raw_coverage,
            declared_format="lcov",
            normalizer=adapter.normalize_coverage_text,
        )
        check_empty_coverage(profile)
        if not any(key.endswith("src/lib.rs") for key in profile.files):
            raise AssertionError(f"real LCOV omitted the fixture source: {sorted(profile.files)}")
        if any(file_coverage.branches is not None for file_coverage in profile.files.values()):
            raise AssertionError("stable Rust LCOV was not normalized to branch-unavailable")

        evidence_dir = PROJECT_ROOT / ".assay/rust-r1-real"
        evidence_dir.mkdir(parents=True, exist_ok=True)
        (evidence_dir / "nextest-failure.txt").write_text(nextest_line + "\n", encoding="utf-8")
        (evidence_dir / "libtest-failure.txt").write_text(libtest_line + "\n", encoding="utf-8")
        (evidence_dir / "coverage.info").write_bytes(raw_coverage)
        versions = []
        for command in (["rustc", "--version"], ["cargo", "nextest", "--version"], ["cargo", "llvm-cov", "--version"]):
            result = _run(command, cwd=workspace, env=env)
            _require_exit(result, 0, "tool version probe")
            versions.append(result.stdout.strip())
        (evidence_dir / "toolchain.txt").write_text("\n".join(versions) + "\n", encoding="utf-8")

        if not capture_only:
            _fixture_line(
                PROJECT_ROOT / "tests/fixtures/rust/nextest-failure.txt",
                EXPECTED_NEXTTEST,
                "nextest",
            )
            _fixture_line(
                PROJECT_ROOT / "tests/fixtures/rust/libtest-failure.txt",
                EXPECTED_LIBTEST,
                "libtest",
            )


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--capture-only",
        action="store_true",
        help="run real tools and capture evidence before committed text fixtures are prepared",
    )
    args = parser.parse_args()
    qualify(capture_only=args.capture_only)


if __name__ == "__main__":
    main()
