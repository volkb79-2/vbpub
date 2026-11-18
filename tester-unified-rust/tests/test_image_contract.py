from __future__ import annotations

import json
import os
import re
import shutil
import stat
import subprocess
import tempfile
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
        self.assertIn('test "$registry_sha256" = "$HOSTOBS_LOCK_REGISTRY_SHA256"', dockerfile)
        self.assertIn("cargo fetch --locked", dockerfile)
        self.assertIn("rust-crate-cache.sha256", dockerfile)
        self.assertIn("verify-crate-cache.py", dockerfile)
        self.assertIn("verify-crate-cache /usr/local/share/hostobs-Cargo.lock", dockerfile)
        # The digest comes from THE verifier function in both places, never from sha256sum of the file.
        self.assertIn(
            'registry_sha256="$(python3 /usr/local/lib/hostobs/verify-crate-cache.py '
            '--print-registry-digest --lock Cargo.lock)"',
            dockerfile,
        )
        self.assertIn(
            'python3 "$repo_root/hostobs/tools/verify-crate-cache.py" --print-registry-digest '
            '--lock "$repo_root/hostobs/Cargo.lock"',
            build,
        )
        self.assertNotIn("sha256sum Cargo.lock", dockerfile)
        self.assertNotIn("sha256sum", build)
        self.assertIn('--build-arg "HOSTOBS_LOCK_REGISTRY_SHA256=$lock_registry_sha256"', build)
        self.assertIn('[[ -n "$cgroup_parent" ]] || {', build)
        self.assertIn('refusing an ungoverned image build', build)
        self.assertIn('[[ -f "$repo_root/hostobs/Cargo.lock" ]] || {', build)
        self.assertIn("cmru tester-gate \\\n  --cwd run-gate-project", build)
        # The bounds travel in CMRU_TESTER_* variables, never as shell-expanded option values:
        # cmru's estate argv check parses this invocation literally and a typed option
        # (--cpus) cannot take an unexpanded ${...} word.
        self.assertIn('values.append(f"{key}={value}")', build)
        self.assertIn('export "$tester_gate_assignment"', build)
        invocation = build.split("cmru tester-gate \\\n", 1)[1].split("/bin/true", 1)[0]
        self.assertNotIn("$", invocation.replace('"$cgroup_parent"', ""))
        for option in ("--image", "--memory", "--memory-swap", "--cpus", "--pids-limit", "--cgroup-probe-image"):
            self.assertNotRegex(invocation, rf"(?m)^\s*{option}\b")
        self.assertIn('"$repo_root/cmru.orchestration.toml"', build)
        self.assertIn('document["orchestration"]["defaults"]["env"]', build)
        self.assertIn('--cgroup-parent "$cgroup_parent"', build)
        self.assertEqual(build.count('--cgroup-parent "$cgroup_parent"'), 2)
        self.assertIn('if [[ "$actual_lock_registry_sha256" != "$lock_registry_sha256" ]]; then', build)
        self.assertIn('if [[ "$actual_base" != "$expected_base" ]]; then', build)
        self.assertNotIn('--resource memory=', build)
        self.assertNotIn('--resource cpu-quota=', build)
        self.assertIn('--build-arg "RUST_IMAGE=$expected_base"', build)
        self.assertNotIn('    --build-arg)\n', build)

    def test_registry_identity_names_are_consistent_and_no_whole_lock_name_survives(self) -> None:
        dockerfile = (PROJECT_ROOT / "Dockerfile").read_text(encoding="utf-8")
        build = (PROJECT_ROOT / "build-image.sh").read_text(encoding="utf-8")
        wrapper = (HOSTOBS_ROOT / "tools/verify-crate-cache").read_text(encoding="utf-8")
        label = "org.vbpub.tester-unified-rust.hostobs-lock-registry-sha256"
        baked = "/usr/local/share/hostobs-Cargo.lock.registry-sha256"
        # ARG is declared in both stages (build stage checks it, final stage labels it).
        self.assertEqual(dockerfile.count("ARG HOSTOBS_LOCK_REGISTRY_SHA256"), 2)
        self.assertIn(f'{label}="${{HOSTOBS_LOCK_REGISTRY_SHA256}}"', dockerfile)
        self.assertIn(f'"{label}"', build)
        # The baked file is written, copied into the final image and read by the wrapper by one name.
        self.assertIn(f"> {baked};", dockerfile)
        self.assertIn(f"--baked-registry-sha256 {baked}", dockerfile)
        self.assertIn(f"COPY --from=cargo-tools {baked} {baked}", dockerfile)
        self.assertIn(f"--baked-registry-sha256 {baked}", wrapper)
        for text in (dockerfile, build, wrapper):
            for stale in (
                "HOSTOBS_LOCK_SHA256",
                "hostobs-lock-sha256",
                "hostobs-Cargo.lock.sha256",
                "--baked-lock-sha256",
            ):
                self.assertNotIn(stale, text)

    def test_build_image_hands_exactly_the_six_tester_bounds_to_tester_gate_by_environment(self) -> None:
        # Behavioural pin with stub `cmru`/`docker` on PATH: the bounds must arrive as the
        # exact CMRU_TESTER_* variables tester-gate reads (a renamed or dropped key passes
        # the text assertions above and fails only on a real host), and the argv stays literal.
        bounds = {
            "CMRU_TESTER_UNIFIED_IMAGE", "CMRU_TESTER_MEMORY", "CMRU_TESTER_MEMORY_SWAP",
            "CMRU_TESTER_CPUS", "CMRU_TESTER_PIDS_LIMIT", "CMRU_TESTER_CGROUP_PROBE_IMAGE",
        }
        defaults = tomllib.loads(
            (REPO_ROOT / "cmru.orchestration.toml").read_text(encoding="utf-8")
        )["orchestration"]["defaults"]["env"]
        dockerfile = (PROJECT_ROOT / "Dockerfile").read_text(encoding="utf-8")
        base = re.search(r"(?m)^ARG RUST_IMAGE=(.+)$", dockerfile).group(1)
        lock = (HOSTOBS_ROOT / "Cargo.lock").read_bytes()
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "tester-unified-rust").mkdir()
            (root / "hostobs" / "tools").mkdir(parents=True)
            (root / "bin").mkdir()
            shutil.copy(
                HOSTOBS_ROOT / "tools" / "verify-crate-cache.py",
                root / "hostobs" / "tools" / "verify-crate-cache.py",
            )
            registry_digest = subprocess.run(
                ["python3", "-I", str(HOSTOBS_ROOT / "tools" / "verify-crate-cache.py"),
                 "--print-registry-digest", "--lock", str(HOSTOBS_ROOT / "Cargo.lock")],
                capture_output=True, text=True, check=True,
            ).stdout.strip()
            shutil.copy(PROJECT_ROOT / "build-image.sh", root / "tester-unified-rust" / "build-image.sh")
            shutil.copy(PROJECT_ROOT / "Dockerfile", root / "tester-unified-rust" / "Dockerfile")
            shutil.copy(REPO_ROOT / "cmru.orchestration.toml", root / "cmru.orchestration.toml")
            (root / "hostobs" / "Cargo.lock").write_bytes(lock)
            (root / "bin" / "cmru").write_text(
                "#!/usr/bin/env python3\n"
                "import json, os, sys\n"
                "with open(os.environ['STUB_OUT'], 'w', encoding='utf-8') as out:\n"
                "    json.dump({'argv': sys.argv[1:], 'env': {k: v for k, v in os.environ.items()"
                " if k.startswith('CMRU_TESTER_')}}, out)\n",
                encoding="utf-8",
            )
            (root / "bin" / "docker").write_text(
                "#!/bin/sh\n"
                'case "$*" in\n'
                f"  *hostobs-lock-registry-sha256*) echo {registry_digest} ;;\n"
                f"  *base-image*) echo {base} ;;\n"
                "esac\n",
                encoding="utf-8",
            )
            for stub in ("cmru", "docker"):
                (root / "bin" / stub).chmod(0o755)
            environment = {
                name: value for name, value in os.environ.items()
                if not name.startswith("CMRU_TESTER_") and name != "CGROUP_PARENT_DEV_GATES"
            }
            environment["PATH"] = f"{root / 'bin'}:{environment.get('PATH', '/usr/bin:/bin')}"
            environment["STUB_OUT"] = str(root / "seen.json")
            result = subprocess.run(
                ["bash", str(root / "tester-unified-rust" / "build-image.sh"),
                 "--cgroup-parent", "dev-gates.slice"],
                env=environment, capture_output=True, text=True, check=False,
            )
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertIn(f"hostobs_lock_registry_sha256={registry_digest}", result.stdout)
            seen = json.loads((root / "seen.json").read_text(encoding="utf-8"))
        self.assertEqual(
            seen["argv"],
            ["tester-gate", "--cwd", "run-gate-project", "--cgroup-parent", "dev-gates.slice", "/bin/true"],
        )
        self.assertEqual(set(seen["env"]), bounds)
        for name in bounds:
            self.assertTrue(seen["env"][name], name)
            if not defaults[name].startswith("${"):
                self.assertEqual(seen["env"][name], defaults[name], name)

    def test_lanes_are_network_none_and_cargo_is_locked_offline(self) -> None:
        hostobs_lanes = tomllib.loads((HOSTOBS_ROOT / "run-gate.toml").read_text(encoding="utf-8"))["lanes"]
        self.assertEqual(set(hostobs_lanes), {"lint", "selftest", "assay-r1"})
        for name, lane in hostobs_lanes.items():
            self.assertEqual(lane["environment"], "tester-unified-rust", name)
            self.assertEqual(lane["network"], "none", name)
            if name == "assay-r1":
                # The judge lane's command lives in assay.toml; its cargo calls must be locked and offline.
                assay_lanes = tomllib.loads((HOSTOBS_ROOT / "assay.toml").read_text(encoding="utf-8"))["lanes"]
                command = assay_lanes["assay-r1"]["argv"][-1]
                self.assertIn("--locked", command, name)
                self.assertIn("--offline", command, name)
                continue
            self.assertIn("--locked", lane["argv"][-1], name)
            self.assertIn("--offline", lane["argv"][-1], name)
        self.assertIn(
            "deny --locked --offline check licenses bans sources",
            hostobs_lanes["lint"]["argv"][-1],
        )

        image_lane = tomllib.loads((PROJECT_ROOT / "run-gate.toml").read_text(encoding="utf-8"))["lanes"]["selftest"]
        self.assertEqual(image_lane["network"], "none")

        central = tomllib.loads((REPO_ROOT / "run-gate.root.toml").read_text(encoding="utf-8"))
        environment = central["environments"]["tester-unified-rust"]
        self.assertEqual(environment["image"], "tester-unified-rust:rs-20261118")
        self.assertEqual(environment["cgroup_slice_env"], "CGROUP_PARENT_DEV_GATES")
        self.assertEqual(environment["network"], "none")

    def test_cargo_wrappers_and_workspace_floors_keep_the_offline_contract(self) -> None:
        cargo_wrapper = (HOSTOBS_ROOT / "tools/cargo-offline.sh").read_text(encoding="utf-8")
        self.assertIn('/usr/local/bin/verify-crate-cache "$PWD/Cargo.lock"', cargo_wrapper)
        self.assertIn("export CARGO_NET_OFFLINE=true", cargo_wrapper)
        self.assertIn('exec cargo "$@"', cargo_wrapper)
        cache_wrapper = (HOSTOBS_ROOT / "tools/verify-crate-cache").read_text(encoding="utf-8")
        self.assertIn("--manifest /usr/local/share/rust-crate-cache.sha256", cache_wrapper)
        self.assertIn(
            "--baked-registry-sha256 /usr/local/share/hostobs-Cargo.lock.registry-sha256", cache_wrapper
        )
        self.assertNotIn("--baked-lock-sha256", cache_wrapper)
        self.assertIn("--cargo-home /usr/local/cargo", cache_wrapper)
        self.assertIn("offline = true", (HOSTOBS_ROOT / ".cargo/config.toml").read_text(encoding="utf-8"))

        nextest = tomllib.loads((HOSTOBS_ROOT / ".config/nextest.toml").read_text(encoding="utf-8"))
        self.assertEqual(nextest["profile"]["ci"]["retries"], 0)

        floors = tomllib.loads((HOSTOBS_ROOT / "coverage-floors.toml").read_text(encoding="utf-8"))["crates"]
        package_dirs = {
            path.parent.relative_to(HOSTOBS_ROOT).as_posix()
            for pattern in ("crates/**/Cargo.toml", "bins/**/Cargo.toml")
            for path in HOSTOBS_ROOT.glob(pattern)
        }
        self.assertEqual(set(floors), package_dirs)
        # Floors are never lowered: this is the ratchet, the real value per crate at the time of
        # writing (bootstrap 90/85 for the scaffold crates). Raise an entry together with its floor.
        ratchet = {
            "bins/act": (90.0, 85.0),
            "bins/probe": (90.0, 85.0),
            "crates/act": (90.0, 85.0),
            "crates/client": (90.0, 85.0),
            "crates/eval": (90.0, 85.0),
            "crates/kernel": (92.0, 93.0),
            "crates/model": (97.0, 96.0),
            "crates/proto": (98.0, 96.0),
            "crates/store": (94.0, 93.0),
        }
        self.assertEqual(set(ratchet), set(floors))
        for package, floor in floors.items():
            with self.subTest(package=package):
                self.assertGreaterEqual(floor["line"], ratchet[package][0])
                self.assertGreaterEqual(floor["region"], ratchet[package][1])

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


class BuildImageBuilderOptionTests(unittest.TestCase):
    """Behavioural pins (stub `docker`/`cmru` on PATH) for the selectable BuildKit builder."""

    SLICE = "dev-gates.slice"
    PREFLIGHT_MARKER = "STUB-CMRU-PREFLIGHT-RAN"

    def _run(
        self, args: list[str], extra_env: dict[str, str] | None = None
    ) -> tuple[subprocess.CompletedProcess[str], list[list[str]], list[list[str]], Path]:
        """Run a copy of build-image.sh; return (result, docker argvs, cmru argvs, sandbox root)."""
        dockerfile = (PROJECT_ROOT / "Dockerfile").read_text(encoding="utf-8")
        base = re.search(r"(?m)^ARG RUST_IMAGE=(.+)$", dockerfile).group(1)
        lock = (HOSTOBS_ROOT / "Cargo.lock").read_bytes()
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp).resolve()
            (root / "tester-unified-rust").mkdir()
            (root / "hostobs" / "tools").mkdir(parents=True)
            (root / "bin").mkdir()
            shutil.copy(
                HOSTOBS_ROOT / "tools" / "verify-crate-cache.py",
                root / "hostobs" / "tools" / "verify-crate-cache.py",
            )
            registry_digest = subprocess.run(
                ["python3", "-I", str(HOSTOBS_ROOT / "tools" / "verify-crate-cache.py"),
                 "--print-registry-digest", "--lock", str(HOSTOBS_ROOT / "Cargo.lock")],
                capture_output=True, text=True, check=True,
            ).stdout.strip()
            shutil.copy(PROJECT_ROOT / "build-image.sh", root / "tester-unified-rust" / "build-image.sh")
            shutil.copy(PROJECT_ROOT / "Dockerfile", root / "tester-unified-rust" / "Dockerfile")
            shutil.copy(REPO_ROOT / "cmru.orchestration.toml", root / "cmru.orchestration.toml")
            (root / "hostobs" / "Cargo.lock").write_bytes(lock)
            # Each stub appends one record per call: its arguments joined by the unit separator.
            (root / "bin" / "cmru").write_text(
                "#!/bin/sh\n"
                "{ printf '%s\\037' \"$@\"; echo; } >> \"$STUB_CMRU_LOG\"\n"
                f"echo {self.PREFLIGHT_MARKER}\n",
                encoding="utf-8",
            )
            (root / "bin" / "docker").write_text(
                "#!/bin/sh\n"
                "{ printf '%s\\037' \"$@\"; echo; } >> \"$STUB_DOCKER_LOG\"\n"
                'if [ "$1 $2" = "buildx inspect" ] && [ -n "$STUB_UNKNOWN_BUILDER" ]'
                ' && [ "$3" = "$STUB_UNKNOWN_BUILDER" ]; then\n'
                '  echo "ERROR: no builder $3 found" >&2; exit 1\n'
                "fi\n"
                'case "$*" in\n'
                f"  *hostobs-lock-registry-sha256*) echo {registry_digest} ;;\n"
                f"  *base-image*) echo {base} ;;\n"
                "esac\n",
                encoding="utf-8",
            )
            for stub in ("cmru", "docker"):
                (root / "bin" / stub).chmod(0o755)
            environment = {
                name: value for name, value in os.environ.items()
                if not name.startswith("CMRU_TESTER_")
                and name not in {"CGROUP_PARENT_DEV_GATES", "TESTER_UNIFIED_RUST_BUILDER", "STUB_UNKNOWN_BUILDER"}
            }
            environment["PATH"] = f"{root / 'bin'}:{environment.get('PATH', '/usr/bin:/bin')}"
            environment["STUB_DOCKER_LOG"] = str(root / "docker.log")
            environment["STUB_CMRU_LOG"] = str(root / "cmru.log")
            environment.update(extra_env or {})
            result = subprocess.run(
                ["bash", str(root / "tester-unified-rust" / "build-image.sh"), *args],
                env=environment, capture_output=True, text=True, check=False,
            )
            self.registry_digest = registry_digest
            self.base = base

            def records(name: str) -> list[list[str]]:
                log = root / name
                if not log.exists():
                    return []
                return [line.rstrip("\x1f").split("\x1f") for line in log.read_text(encoding="utf-8").splitlines()]

            return result, records("docker.log"), records("cmru.log"), root

    @staticmethod
    def _builders(calls: list[list[str]]) -> tuple[list[str], list[str]]:
        inspected = [c[2] for c in calls if c[:2] == ["buildx", "inspect"]]
        built = [c[c.index("--builder") + 1] for c in calls if c[:2] == ["buildx", "build"]]
        return inspected, built

    def test_default_builder_is_mdt_managed_in_inspect_and_build(self) -> None:
        result, calls, _, _ = self._run(["--cgroup-parent", self.SLICE])
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(self._builders(calls), (["mdt-managed"], ["mdt-managed"]))
        self.assertIn("builder=mdt-managed\n", result.stdout)
        self.assertLess(result.stdout.index("builder="), result.stdout.index("image="))

    def test_builder_option_is_honoured_in_inspect_and_build(self) -> None:
        result, calls, _, _ = self._run(["--builder", "default", "--cgroup-parent", self.SLICE])
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(self._builders(calls), (["default"], ["default"]))
        self.assertIn("builder=default\n", result.stdout)

    def test_full_build_argv_and_preflight_order_are_unchanged_apart_from_the_builder(self) -> None:
        result, calls, cmru_calls, root = self._run(
            ["--builder", "default", "--tag", "x:y", "--cgroup-parent", self.SLICE]
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        builds = [c for c in calls if c[:2] == ["buildx", "build"]]
        self.assertEqual(
            builds,
            [[
                "buildx", "build",
                "--builder", "default",
                "--load",
                "--cgroup-parent", self.SLICE,
                "--network", "default",
                "--build-arg", f"RUST_IMAGE={self.base}",
                "--build-arg", f"HOSTOBS_LOCK_REGISTRY_SHA256={self.registry_digest}",
                "-f", str(root / "tester-unified-rust" / "Dockerfile"),
                "-t", "x:y",
                str(root / "hostobs"),
            ]],
        )
        # The governance preflight is untouched: it ran once, with the slice, before `builder=`.
        self.assertEqual(
            cmru_calls,
            [["tester-gate", "--cwd", "run-gate-project", "--cgroup-parent", self.SLICE, "/bin/true"]],
        )
        self.assertLess(result.stdout.index(self.PREFLIGHT_MARKER), result.stdout.index("builder=default"))
        self.assertEqual(calls[0][:2], ["buildx", "inspect"])
        self.assertEqual(calls[1][:2], ["buildx", "build"])

    def test_an_unknown_builder_fails_the_inspect_preflight_before_the_build(self) -> None:
        result, calls, cmru_calls, _ = self._run(
            ["--builder", "ghost", "--cgroup-parent", self.SLICE], {"STUB_UNKNOWN_BUILDER": "ghost"}
        )
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(self._builders(calls), (["ghost"], []))
        self.assertEqual(len(cmru_calls), 1)
        self.assertNotIn("image=", result.stdout)

    def test_builder_environment_is_honoured_and_the_option_beats_it(self) -> None:
        result, calls, _, _ = self._run(
            ["--cgroup-parent", self.SLICE], {"TESTER_UNIFIED_RUST_BUILDER": "from-env"}
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(self._builders(calls), (["from-env"], ["from-env"]))
        result, calls, _, _ = self._run(
            ["--builder", "from-option", "--cgroup-parent", self.SLICE],
            {"TESTER_UNIFIED_RUST_BUILDER": "from-env"},
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(self._builders(calls), (["from-option"], ["from-option"]))
        self.assertIn("builder=from-option\n", result.stdout)

    def _assert_refused_before_any_call(self, args: list[str], extra_env: dict[str, str] | None, label: str) -> None:
        result, calls, cmru_calls, _ = self._run(args, extra_env)
        self.assertEqual(result.returncode, 2, label)
        self.assertIn("usage:", result.stderr, label)
        self.assertEqual((calls, cmru_calls), ([], []), label)
        self.assertNotIn("builder=", result.stdout, label)

    def test_invalid_builder_name_is_refused_with_exit_2_before_any_docker_call(self) -> None:
        for bad in ("", "-x", ".x", "a b", "a/b", "a;b", "$(id)", "a\nb", "a\n"):
            self._assert_refused_before_any_call(["--builder", bad, "--cgroup-parent", self.SLICE], None, repr(bad))
        for bad in ("bad name", "a\nb", "-x"):
            self._assert_refused_before_any_call(
                ["--cgroup-parent", self.SLICE], {"TESTER_UNIFIED_RUST_BUILDER": bad}, f"env {bad!r}"
            )

    def test_builder_option_without_a_value_or_with_equals_is_a_usage_error(self) -> None:
        self._assert_refused_before_any_call(["--cgroup-parent", self.SLICE, "--builder"], None, "no value")
        self._assert_refused_before_any_call(["--builder=default", "--cgroup-parent", self.SLICE], None, "equals")

    def test_builder_name_check_is_locale_proof(self) -> None:
        # In a UTF-8 locale the bracket range [A-Za-z0-9] is collation-dependent and bash accepts
        # an accented and a fullwidth letter; the script must still refuse both.
        listed = subprocess.run(["locale", "-a"], capture_output=True, text=True, check=False).stdout.split()
        utf8 = [name for name in listed if re.search(r"utf-?8", name, re.IGNORECASE)]
        preferred = [name for name in utf8 if name.lower().startswith("en_us")] or utf8
        if not preferred:
            self.skipTest("no UTF-8 locale is installed (locale -a lists none)")
        for bad in ("é", "ａ", "aé"):
            self._assert_refused_before_any_call(
                ["--builder", bad, "--cgroup-parent", self.SLICE],
                {"LC_ALL": preferred[0], "LANG": preferred[0]},
                f"{preferred[0]} {bad!r}",
            )

    def test_cgroup_parent_is_still_required_whatever_the_builder(self) -> None:
        for args in ([], ["--builder", "default"]):
            result, calls, cmru_calls, _ = self._run(args)
            self.assertEqual(result.returncode, 2, args)
            self.assertIn("refusing an ungoverned image build", result.stderr)
            self.assertEqual((calls, cmru_calls), ([], []), args)


if __name__ == "__main__":
    unittest.main()
