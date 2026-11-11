from __future__ import annotations

import re
import stat
import tomllib
import unittest
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
REPO_ROOT = PROJECT_ROOT.parent
HOSTOBS_ROOT = REPO_ROOT / "hostobs"


class ImageContractTests(unittest.TestCase):
    def test_workspace_gate_entrypoints_are_executable(self) -> None:
        exec_bits = stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH
        for relative in (
            "tools/cargo-offline.sh",
            "tools/check-lints.sh",
            "tools/verify-crate-cache",
        ):
            path = HOSTOBS_ROOT / relative
            self.assertTrue(path.is_file(), relative)
            self.assertEqual(path.stat().st_mode & exec_bits, exec_bits, relative)

    def test_image_uses_the_measured_digest_and_pinned_cargo_tools(self) -> None:
        dockerfile = (PROJECT_ROOT / "Dockerfile").read_text(encoding="utf-8")
        self.assertRegex(
            dockerfile,
            r"(?m)^ARG RUST_IMAGE=rust@sha256:e97df055c54a05160e7c67ba3b2963de6ddb95903911baf8922dbcd1b394f698$",
        )
        self.assertIn("FROM ${RUST_IMAGE} AS cargo-tools", dockerfile)
        for name, version in (
            ("CARGO_NEXTEST_VERSION", "0.9.151"),
            ("CARGO_LLVM_COV_VERSION", "0.9.1"),
            ("CARGO_MUTANTS_VERSION", "27.1.0"),
            ("CARGO_DENY_VERSION", "0.20.2"),
        ):
            self.assertRegex(dockerfile, rf"(?m)^ARG {name}={re.escape(version)}$")
        self.assertIn("cargo install --locked", dockerfile)
        self.assertNotIn("--jobs 2", dockerfile)
        self.assertNotIn("CARGO_BUILD_JOBS=2", dockerfile)
        self.assertEqual(dockerfile.count("rustup component add clippy rustfmt llvm-tools-preview"), 2)
        self.assertEqual(dockerfile.count("rustup target add x86_64-unknown-linux-musl"), 2)
        self.assertIn("useradd -u \"${RUN_UID}\"", dockerfile)

    def test_image_fetches_exact_lock_and_prechecks_baked_archives(self) -> None:
        dockerfile = (PROJECT_ROOT / "Dockerfile").read_text(encoding="utf-8")
        build = (PROJECT_ROOT / "build-image.sh").read_text(encoding="utf-8")
        self.assertIn('test "$lock_sha256" = "$HOSTOBS_LOCK_SHA256"', dockerfile)
        self.assertIn("cargo fetch --locked", dockerfile)
        self.assertIn("rust-crate-cache.sha256", dockerfile)
        self.assertIn("verify-crate-cache.py", dockerfile)
        self.assertIn("verify-crate-cache /usr/local/share/hostobs-Cargo.lock", dockerfile)
        self.assertIn('sha256sum "$repo_root/hostobs/Cargo.lock"', build)
        self.assertIn("cmru tester-gate \\\n  --cwd run-gate-project", build)
        self.assertIn('"$repo_root/cmru.orchestration.toml"', build)
        self.assertIn('document["orchestration"]["defaults"]["env"]', build)
        self.assertIn('--cgroup-parent "$cgroup_parent"', build)
        self.assertNotIn('--resource memory=', build)
        self.assertNotIn('--resource cpu-quota=', build)
        self.assertIn('--build-arg "RUST_IMAGE=$expected_base"', build)
        self.assertNotIn('    --build-arg)\n', build)

    def test_lanes_are_network_none_and_cargo_is_locked_offline(self) -> None:
        hostobs_lanes = tomllib.loads((HOSTOBS_ROOT / "run-gate.toml").read_text(encoding="utf-8"))["lanes"]
        self.assertEqual(set(hostobs_lanes), {"lint", "selftest"})
        for name, lane in hostobs_lanes.items():
            self.assertEqual(lane["environment"], "tester-unified-rust", name)
            self.assertEqual(lane["network"], "none", name)
            self.assertIn("--locked", lane["argv"][-1], name)
            self.assertIn("--offline", lane["argv"][-1], name)

        image_lane = tomllib.loads((PROJECT_ROOT / "run-gate.toml").read_text(encoding="utf-8"))["lanes"]["selftest"]
        self.assertEqual(image_lane["network"], "none")

    def test_deny_policy_keeps_workspace_exceptions_narrow(self) -> None:
        deny = tomllib.loads((HOSTOBS_ROOT / "deny.toml").read_text(encoding="utf-8"))
        self.assertTrue(deny["licenses"]["private"]["ignore"])
        bans = deny["bans"]
        self.assertEqual(bans["multiple-versions"], "deny")
        self.assertEqual(bans["wildcards"], "deny")
        self.assertTrue(bans["allow-wildcard-paths"])
        self.assertEqual(
            [(item["crate"], item["reason"]) for item in bans["skip"]],
            [
                (
                    "winnow@0.7.15",
                    "toml 0.9.8 and toml_parser 1.1.5 resolve separate winnow lines in the imported sampler lock",
                )
            ],
        )

    def test_bootstrap_does_not_vendor_crates(self) -> None:
        self.assertFalse((HOSTOBS_ROOT / "vendor").exists())
        self.assertNotIn("vendor", (HOSTOBS_ROOT / ".dockerignore").read_text(encoding="utf-8"))


if __name__ == "__main__":
    unittest.main()
