"""Tests of the hostobs dev-container shim, the dev image sources and the dev image build script.

The generic remote dev tool itself (scripts/controller/dev, dev_profile.py, dev-profiles.toml) is
tested in scripts/controller/tests/test_dev.py (lane `suite` of scripts/controller). What stays here:
the shim hostobs/tools/dev (argv and HOSTOBS_DEV_* mapping, run against a stub tool), the static
contract of the image sources (Dockerfile.dev, build-dev-image.sh), and build-dev-image.sh run against
a stub `docker` on PATH. No docker daemon, no ssh and no network are used. Needs HOSTOBS_ROOT.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

HOSTOBS_ROOT = Path(os.environ["HOSTOBS_ROOT"]).resolve()
REPO_ROOT = HOSTOBS_ROOT.parent
PROJECT_ROOT = REPO_ROOT / "tester-unified-rust"
SHIM = HOSTOBS_ROOT / "tools" / "dev"
TOOL = REPO_ROOT / "scripts" / "controller" / "dev"
TOOL_PROFILE = REPO_ROOT / "scripts" / "controller" / "dev_profile.py"
DOCKERFILE = PROJECT_ROOT / "Dockerfile.dev"
BUILD_DEV = PROJECT_ROOT / "build-dev-image.sh"

# A stand-in for scripts/controller/dev that records what the shim handed it: one argv word per line, then
# the AGENT_DEV_* and HOSTOBS_DEV_* variables it sees.
TOOL_STUB = """#!/bin/bash
{ printf 'argv:%s\\n' "$@"; env | grep -E '^(AGENT|HOSTOBS)_DEV_' | sort | sed 's/^/env:/'; } > "$STUB_OUT"
exit "${STUB_EXIT:-0}"
"""


class ShimTests(unittest.TestCase):
    """hostobs/tools/dev only translates; run it from a temp repo whose scripts/controller/dev is a recording stub."""

    def setUp(self) -> None:
        self.tmp = Path(tempfile.mkdtemp(prefix="devshim-")).resolve()
        self.addCleanup(shutil.rmtree, self.tmp, True)
        (self.tmp / "hostobs" / "tools").mkdir(parents=True)
        (self.tmp / "scripts" / "controller").mkdir(parents=True)
        shutil.copy(SHIM, self.tmp / "hostobs" / "tools" / "dev")
        stub = self.tmp / "scripts" / "controller" / "dev"
        stub.write_text(TOOL_STUB)
        stub.chmod(0o755)
        self.out = self.tmp / "out.txt"

    def run_shim(self, *args: str, **extra: str):
        env = {"PATH": "/usr/local/bin:/usr/bin:/bin", "HOME": str(self.tmp), "STUB_OUT": str(self.out)}
        env.update(extra)
        proc = subprocess.run([str(self.tmp / "hostobs" / "tools" / "dev"), *args], env=env, capture_output=True, text=True,
                              stdin=subprocess.DEVNULL, timeout=60)
        lines = self.out.read_text().splitlines() if self.out.exists() else []
        self.out.unlink(missing_ok=True)
        argv = [line[5:] for line in lines if line.startswith("argv:")]
        seen = dict(line[4:].split("=", 1) for line in lines if line.startswith("env:"))
        return proc, argv, seen

    def test_a_verb_gets_the_hostobs_profile_and_every_word_byte_for_byte(self) -> None:
        proc, argv, _ = self.run_shim("test", "--", "-k", "a b", "$(x)", "it's")
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertEqual(argv, ["-p", "hostobs", "test", "--", "-k", "a b", "$(x)", "it's"])

    def test_the_tool_exit_code_is_the_shim_exit_code(self) -> None:
        for code in ("0", "3", "75", "124"):
            proc, _, _ = self.run_shim("check", STUB_EXIT=code)
            self.assertEqual(proc.returncode, int(code))

    def test_hostobs_dev_variables_map_to_agent_dev_and_are_not_left_behind(self) -> None:
        proc, _, seen = self.run_shim("check", HOSTOBS_DEV_HOST="v1001", HOSTOBS_DEV_MEMORY="2g", HOSTOBS_DEV_CPUS="2",
                                      HOSTOBS_DEV_PIDS="512", HOSTOBS_DEV_MAX_S="600")
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertEqual(seen, {"AGENT_DEV_HOST": "v1001", "AGENT_DEV_MEMORY": "2g", "AGENT_DEV_CPUS": "2",
                                "AGENT_DEV_PIDS": "512", "AGENT_DEV_MAX_S": "600"})

    def test_an_empty_hostobs_dev_value_is_mapped_not_dropped(self) -> None:
        _, _, seen = self.run_shim("check", HOSTOBS_DEV_CPUS="")
        self.assertEqual(seen, {"AGENT_DEV_CPUS": ""})

    def test_any_other_hostobs_dev_variable_reaches_the_tool_unmapped_to_be_refused(self) -> None:
        _, _, seen = self.run_shim("check", HOSTOBS_DEV_WAIT_S="9")
        self.assertEqual(seen, {"HOSTOBS_DEV_WAIT_S": "9"})

    def test_clean_becomes_gc_this_without_a_project(self) -> None:
        for args in (("clean",), ("clean", "--checkout")):
            proc, argv, _ = self.run_shim(*args)
            self.assertEqual(proc.returncode, 0, proc.stderr)
            self.assertEqual(argv, ["gc", "--this"], args)

    def test_clean_with_anything_else_is_a_usage_error_and_runs_nothing(self) -> None:
        for args in (("clean", "--all"), ("clean", "--checkout", "x")):
            proc, argv, _ = self.run_shim(*args)
            self.assertEqual(proc.returncode, 2, args)
            self.assertIn("usage: dev clean [--checkout]", proc.stderr)
            self.assertEqual(argv, [])

    def test_gc_is_passed_through_without_a_project(self) -> None:
        proc, argv, _ = self.run_shim("gc", "--dry-run")
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertEqual(argv, ["gc", "--dry-run"])

    def test_the_real_tool_refuses_an_unknown_hostobs_dev_variable_with_2_before_doing_anything(self) -> None:
        env = {"PATH": "/usr/local/bin:/usr/bin:/bin", "HOME": str(self.tmp), "HOSTOBS_DEV_NOPE": "1"}
        proc = subprocess.run([str(SHIM), "--dry-run", "check"], env=env, capture_output=True, text=True,
                              stdin=subprocess.DEVNULL, timeout=60)
        self.assertEqual(proc.returncode, 2, proc.stderr)
        self.assertIn("HOSTOBS_DEV_NOPE is no longer read", proc.stderr)


class ImageSourceTests(unittest.TestCase):
    def test_scripts_are_valid_bash_and_executable(self) -> None:
        for script in (SHIM, TOOL, BUILD_DEV):
            self.assertTrue(os.access(script, os.X_OK), script)
            self.assertEqual(subprocess.run(["bash", "-n", str(script)], capture_output=True).returncode, 0, script)
        for script in (SHIM, TOOL, BUILD_DEV):
            self.assertIn("set -euo pipefail", script.read_text(), script)

    def test_dev_tool_never_asks_for_host_namespaces_or_a_second_transport(self) -> None:
        text = TOOL.read_text() + TOOL_PROFILE.read_text()
        for fragment in ("--privileged", "--pid=host", "--net=host", "--network host", "--cgroupns=host", "docker.sock", "ancestor="):
            self.assertNotIn(fragment, text, fragment)
        self.assertIn("hx.sh", TOOL.read_text())
        self.assertNotRegex(text, r"(?m)^\s*ssh\b")
        # no user word is ever inspected to decide how to quote it
        self.assertNotIn("'$HOME/'*)", text)

    def test_dockerfile_dev_installs_exactly_the_listed_packages_and_ends_as_the_gate_user(self) -> None:
        text = DOCKERFILE.read_text()
        self.assertRegex(text, r"(?m)^ARG BASE_IMAGE=tester-unified-rust:local$")
        self.assertIn("FROM ${BASE_IMAGE}", text)
        self.assertIn('org.vbpub.tester-unified-rust-dev.base-image="${BASE_IMAGE_TAG}"', text)
        self.assertRegex(text, r"(?m)^ARG BASE_IMAGE_TAG=")
        self.assertIn('org.vbpub.tester-unified-rust-dev.base-image-id="${BASE_IMAGE_ID}"', text)
        install = re.search(r"apt-get install -y --no-install-recommends \\\n\s+(.+?) \\\n", text)
        self.assertIsNotNone(install)
        self.assertEqual(set(install.group(1).split()), {"gdb", "strace", "procps", "less", "ca-certificates"})
        self.assertEqual(text.count("apt-get install"), 1)
        self.assertEqual(re.findall(r"(?m)^USER (.+)$", text), ["root", "1003:1003"])
        directives = [line.split()[0] for line in text.splitlines() if re.match(r"^[A-Z]+ ", line)]
        self.assertLess(text.index("USER 1003:1003"), text.index("WORKDIR /work"))
        self.assertEqual(directives[-1], "CMD")

    def test_dockerfile_dev_copies_nothing_and_leaves_the_cache_and_identity_alone(self) -> None:
        text = "\n".join(line for line in DOCKERFILE.read_text().splitlines() if not line.lstrip().startswith("#"))
        for directive in ("COPY", "ADD"):
            self.assertIsNone(re.search(rf"(?m)^{directive}\b", text), directive)
        for fragment in ("chown", "cargo fetch", "cargo install", "/usr/local/cargo", "registry", "rm -rf /usr/local",
                         "Cargo.lock.registry-sha256", "rust-crate-cache", "rustup update", "rustup toolchain", "rustup install",
                         "rustup default", "rustup self", "curl", "wget"):
            self.assertNotIn(fragment, text, fragment)
        self.assertEqual(text.count("hostobs-Cargo.lock"), 1)
        self.assertIn("/usr/local/bin/verify-crate-cache /usr/local/share/hostobs-Cargo.lock", text)

    def test_dockerfile_dev_pins_the_installed_toolchain_and_fails_on_any_change(self) -> None:
        text = DOCKERFILE.read_text()
        self.assertIn('before="$(rustc -vV)"', text)
        self.assertIn('after="$(rustc -vV)"', text)
        self.assertIn('test "$before" = "$after"', text)
        self.assertIn('toolchain="$(rustup show active-toolchain | cut -d\' \' -f1)"', text)
        self.assertEqual(text.count("rustup component add"), 1)
        self.assertIn('rustup component add --toolchain "$toolchain" rust-src rust-analyzer', text)
        self.assertLess(text.index('before="$(rustc -vV)"'), text.index("rustup component add"))
        self.assertLess(text.index("rustup component add"), text.index('after="$(rustc -vV)"'))
        self.assertIn("dpkg-query -W gdb strace procps less ca-certificates", text)
        self.assertIn("rust-analyzer --version", text)

    def test_selftest_lane_syntax_checks_the_new_scripts(self) -> None:
        text = (PROJECT_ROOT / "run-gate.toml").read_text()
        self.assertIn("build-dev-image.sh", text)
        self.assertIn('"$HOSTOBS_ROOT/tools/dev"', text)


BUILD_DOCKER_STUB = f"""#!{sys.executable}
import json, os, sys
args = sys.argv[1:]
with open(os.path.join(os.environ["STUB_LOG_DIR"], "docker.log"), "a") as fh:
    fh.write(json.dumps(args) + "\\n")
BASE_ID = "sha256:" + "a" * 64
if args[:2] == ["image", "inspect"]:
    fmt, image = args[3], args[4]
    if image == os.environ.get("STUB_MISSING_IMAGE"):
        sys.exit(1)
    if fmt == "{{{{json .RootFS.Layers}}}}":
        if image.startswith("sha256:"):
            print('["sha256:l1","sha256:l2"]')
        else:
            print(os.environ.get("STUB_DEV_LAYERS", '["sha256:l1","sha256:l2","sha256:l3"]'))
    elif fmt == "{{{{.Id}}}}":
        print(os.environ.get("STUB_BASE_ID", BASE_ID))
    elif fmt.endswith('base-image-id"}}}}'):
        print(os.environ.get("STUB_LABEL_ID", BASE_ID))
    elif fmt.endswith('base-image"}}}}'):
        print(os.environ.get("STUB_LABEL_BASE", "tester-unified-rust:local"))
    sys.exit(0)
if args[:2] == ["buildx", "inspect"]:
    sys.exit(1 if args[2] == os.environ.get("STUB_UNKNOWN_BUILDER") else 0)
if args[:2] == ["buildx", "build"]:
    sys.exit(0)
if args[0] == "run":
    i = max(i for i, a in enumerate(args) if a in ("dpkg-query", "rustc", "rust-analyzer", "rustup"))
    image = args[i - 1]
    tool = args[i]
    base = [("bash", "5.2-1"), ("libc6", "2.36-9"), ("ca-certificates", "20230311")]
    dev = base[:2] + [("ca-certificates", "20240203"), ("gdb", "13.1-3"), ("libpython3.11", "3.11.2-6"), ("libdebuginfod1", "0.188-2")]
    if tool == "dpkg-query":
        for name, version in (base if image.startswith("sha256:") else dev):
            print(f"{{name}}\\t{{version}}")
    elif tool == "rustc":
        print("rustc 1.99.0 (abcdef 2026-01-01)")
    elif tool == "rust-analyzer":
        print("rust-analyzer 1.99.0")
    elif tool == "rustup":
        print("rust-src-x86_64-unknown-linux-gnu")
        print("rust-analyzer-x86_64-unknown-linux-gnu")
    sys.exit(0)
sys.exit(0)
"""


class BuildDevImageTests(unittest.TestCase):
    SLICE = "dev-gates.slice"
    SLICE_PATH = "dev.slice/dev-gates.slice"

    def setUp(self) -> None:
        self.tmp = Path(tempfile.mkdtemp(prefix="builddev-")).resolve()
        self.addCleanup(shutil.rmtree, self.tmp, True)
        (self.tmp / "bin").mkdir()
        (self.tmp / "logs").mkdir()
        (self.tmp / "bin" / "docker").write_text(BUILD_DOCKER_STUB)
        (self.tmp / "bin" / "docker").chmod(0o755)

    def run_build(self, *args: str, **extra: str):
        (self.tmp / "logs" / "docker.log").unlink(missing_ok=True)
        env = {"PATH": f"{self.tmp / 'bin'}:/usr/local/bin:/usr/bin:/bin", "HOME": str(self.tmp), "STUB_LOG_DIR": str(self.tmp / "logs"),
               "TMPDIR": str(self.tmp)}
        env.update(extra)
        proc = subprocess.run([str(BUILD_DEV), *args], env=env, capture_output=True, text=True, stdin=subprocess.DEVNULL, timeout=60)
        log = self.tmp / "logs" / "docker.log"
        calls = [json.loads(line) for line in log.read_text().splitlines()] if log.exists() else []
        return proc, calls

    def test_refuses_without_a_cgroup_parent_and_documents_options(self) -> None:
        proc, calls = self.run_build()
        self.assertEqual(proc.returncode, 2)
        self.assertIn("is required (the estate rule for every image build); refusing to build without it", proc.stderr)
        self.assertNotIn("ungoverned image build", proc.stderr)
        self.assertEqual(calls, [])
        proc, _ = self.run_build("--help")
        self.assertEqual(proc.returncode, 2)
        for option in ("--tag", "--base", "--cgroup-parent", "--record", "--builder", "TESTER_UNIFIED_RUST_BUILDER"):
            self.assertIn(option, proc.stderr)

    def test_builder_precedence_is_option_then_environment_then_default(self) -> None:
        def builders(calls):
            return ([c[c.index("inspect") + 1] for c in calls if c[:2] == ["buildx", "inspect"]],
                    [c[c.index("--builder") + 1] for c in calls if c[:2] == ["buildx", "build"]])
        proc, calls = self.run_build("--cgroup-parent", self.SLICE)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertEqual(builders(calls), (["default"], ["default"]))
        self.assertIn("builder=default\n", proc.stdout)
        proc, calls = self.run_build("--cgroup-parent", self.SLICE, TESTER_UNIFIED_RUST_BUILDER="envb")
        self.assertEqual(builders(calls), (["envb"], ["envb"]))
        proc, calls = self.run_build("--cgroup-parent", self.SLICE, "--builder", "optb", TESTER_UNIFIED_RUST_BUILDER="envb")
        self.assertEqual(builders(calls), (["optb"], ["optb"]))
        for bad in ("-x", "a b", "", "a;b", "é"):
            proc, calls = self.run_build("--cgroup-parent", self.SLICE, "--builder", bad)
            self.assertEqual(proc.returncode, 2, bad)
            self.assertEqual(calls, [], bad)
        proc, calls = self.run_build("--cgroup-parent", self.SLICE, TESTER_UNIFIED_RUST_BUILDER="-bad")
        self.assertEqual(proc.returncode, 2)

    def test_missing_base_is_refused_before_any_build(self) -> None:
        proc, calls = self.run_build("--cgroup-parent", self.SLICE, "--base", "nope:1", STUB_MISSING_IMAGE="nope:1")
        self.assertEqual(proc.returncode, 2)
        self.assertIn("not in the local daemon", proc.stderr)
        self.assertEqual([c for c in calls if c[0] == "buildx"], [])

    def test_the_build_records_the_base_tag_and_id_and_verifies_the_labels(self) -> None:
        proc, calls = self.run_build("--cgroup-parent", self.SLICE, "--tag", "x:y", "--base", "tester-unified-rust:local")
        self.assertEqual(proc.returncode, 0, proc.stderr)
        build = [c for c in calls if c[:2] == ["buildx", "build"]][0]
        args = [build[i + 1] for i, a in enumerate(build) if a == "--build-arg"]
        # FROM is the immutable id (no tag race); the tag travels separately, for the label
        self.assertEqual(args, ["BASE_IMAGE=tester-unified-rust-base:" + "a" * 64, "BASE_IMAGE_TAG=tester-unified-rust:local",
                                "BASE_IMAGE_ID=sha256:" + "a" * 64])
        # the content-named tag is created from the exact id, before the build, and never removed
        names = [c[:2] for c in calls]
        self.assertEqual([c for c in calls if c[0] == "tag"], [["tag", "sha256:" + "a" * 64, "tester-unified-rust-base:" + "a" * 64]])
        self.assertLess(names.index(["tag", "sha256:" + "a" * 64]), names.index(["buildx", "build"]))
        self.assertEqual([c for c in calls if c[0] in ("rmi", "image") and "rm" in c[:2]], [])
        self.assertEqual(build[build.index("--cgroup-parent") + 1], self.SLICE_PATH)
        self.assertIn("-t", build)
        self.assertIn("base_id=sha256:" + "a" * 64, proc.stdout)
        proc, _ = self.run_build("--cgroup-parent", self.SLICE, STUB_LABEL_ID="sha256:" + "b" * 64)
        self.assertEqual(proc.returncode, 2)
        self.assertIn("does not record the requested base", proc.stderr)
        proc, _ = self.run_build("--cgroup-parent", self.SLICE, STUB_LABEL_BASE="other:tag")
        self.assertEqual(proc.returncode, 2)

    def test_the_dev_image_must_sit_on_the_layers_of_the_base_image(self) -> None:
        proc, _ = self.run_build("--cgroup-parent", self.SLICE)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        for layers in ('["sha256:l1","sha256:lX","sha256:l3"]', '["sha256:l1","sha256:l2"]', '["sha256:l2","sha256:l1","sha256:l3"]', "null", ""):
            with self.subTest(layers=layers):
                proc, _ = self.run_build("--cgroup-parent", self.SLICE, STUB_DEV_LAYERS=layers)
                self.assertEqual(proc.returncode, 2, proc.stdout)
                self.assertIn("not built on the layers of base image", proc.stderr)

    def test_the_build_states_the_measured_truth_about_the_governed_slice(self) -> None:
        proc, calls = self.run_build("--cgroup-parent", self.SLICE)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        note = [line for line in proc.stderr.splitlines() if line.startswith("NOTE:")]
        self.assertEqual(len(note), 1, proc.stderr)
        self.assertIn("cgroup PATH dev.slice/dev-gates.slice (derived from the slice name dev-gates.slice)", note[0])
        self.assertIn("measured on v1001 (2026-11-18) with the docker-driver builder `default`, the BuildKit RUN steps run inside the governed slice", note[0])
        self.assertIn("plain slice name would put them in an ungoverned root-level cgroup", note[0])
        self.assertIn("Probes use the slice name", note[0])
        self.assertNotIn("was not measured", note[0])
        for stale in ("does NOT place", "watch its memory", "/sys/fs/cgroup/dev-gates.slice/buildkit"):
            self.assertNotIn(stale, proc.stderr)
        # buildx gets the PATH form, exactly once
        build = [c for c in calls if c[:2] == ["buildx", "build"]][0]
        self.assertEqual(build.count("--cgroup-parent"), 1)
        self.assertEqual(build[build.index("--cgroup-parent") + 1], self.SLICE_PATH)

    def test_the_note_claims_governed_placement_only_for_the_measured_builder(self) -> None:
        for builder in ("mdt-managed", "other"):
            with self.subTest(builder=builder):
                proc, calls = self.run_build("--cgroup-parent", self.SLICE, "--builder", builder)
                self.assertEqual(proc.returncode, 0, proc.stderr)
                note = [line for line in proc.stderr.splitlines() if line.startswith("NOTE:")]
                self.assertEqual(len(note), 1, proc.stderr)
                self.assertIn(f"placement of builder {builder} was not measured", note[0])
                self.assertIn("only the docker-driver builder `default` on v1001, 2026-11-18", note[0])
                self.assertIn("check /proc/<pid>/cgroup of a RUN-step process", note[0])
                self.assertIn("cgroup PATH dev.slice/dev-gates.slice", note[0])
                self.assertNotIn("run inside the governed slice", note[0])
                build = [c for c in calls if c[:2] == ["buildx", "build"]][0]
                self.assertEqual(build[build.index("--cgroup-parent") + 1], self.SLICE_PATH)

    def test_buildx_gets_the_path_and_probes_the_name_for_every_slice_shape(self) -> None:
        for name, path in (
            ("dev-gates.slice", "dev.slice/dev-gates.slice"),
            ("a-b-c.slice", "a.slice/a-b.slice/a-b-c.slice"),
            ("x.slice", "x.slice"),
        ):
            with self.subTest(name=name):
                proc, calls = self.run_build("--cgroup-parent", name)
                self.assertEqual(proc.returncode, 0, proc.stderr)
                builds = [c for c in calls if c[:2] == ["buildx", "build"]]
                self.assertEqual(len(builds), 1)
                self.assertEqual(builds[0][builds[0].index("--cgroup-parent") + 1], path)
                probes = [c for c in calls if c[0] == "run"]
                self.assertGreaterEqual(len(probes), 4)
                for probe in probes:
                    self.assertEqual(probe.count("--cgroup-parent"), 1)
                    self.assertEqual(probe[probe.index("--cgroup-parent") + 1], name)
                # no other docker call carries a cgroup parent at all
                for call in calls:
                    if call[:2] != ["buildx", "build"] and call[0] != "run":
                        self.assertNotIn("--cgroup-parent", call)

    def test_the_slice_input_also_comes_from_the_environment(self) -> None:
        proc, calls = self.run_build(CGROUP_PARENT_DEV_GATES=self.SLICE)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        build = [c for c in calls if c[:2] == ["buildx", "build"]][0]
        self.assertEqual(build[build.index("--cgroup-parent") + 1], self.SLICE_PATH)
        probe = [c for c in calls if c[0] == "run"][0]
        self.assertEqual(probe[probe.index("--cgroup-parent") + 1], self.SLICE)

    def test_an_invalid_slice_name_is_refused_with_exit_2_before_any_docker_call(self) -> None:
        for bad in ("-.slice", "a--b.slice", "a-.slice", "dev.slice/dev-gates.slice", "/dev.slice/dev-gates.slice",
                    "../a.slice", "a b.slice", "a.slice\n", "dev-gates", "é.slice", ""):
            with self.subTest(bad=bad):
                proc, calls = self.run_build("--cgroup-parent", bad)
                self.assertEqual(proc.returncode, 2, repr(bad))
                self.assertEqual(calls, [], repr(bad))
                self.assertTrue("invalid --cgroup-parent" in proc.stderr or "is required" in proc.stderr, proc.stderr)
        proc, calls = self.run_build(CGROUP_PARENT_DEV_GATES="a/b.slice")
        self.assertEqual(proc.returncode, 2)
        self.assertEqual(calls, [])
        self.assertIn("invalid --cgroup-parent / CGROUP_PARENT_DEV_GATES", proc.stderr)
        self.assertIn("systemd slice name", proc.stderr)

    def test_the_old_claims_are_gone_from_the_scripts_and_the_docs_state_both_spellings(self) -> None:
        # Negative greps cover the two scripts only (their NOTE and comments); prose docs may quote history.
        stale = (
            "does NOT place", "does not place", "NOT in a governed", "not in the governed slice",
            "untested path form", "watch its memory", "not because it governs this build",
            "passed because the estate requires it", "(unlike the build above)",
        )
        for relative in ("build-dev-image.sh", "build-image.sh"):
            text = (PROJECT_ROOT / relative).read_text(encoding="utf-8")
            for phrase in stale:
                self.assertNotIn(phrase, text, f"{relative}: {phrase}")
        for relative in ("docs/DEV.md", "docs/CONSUMERS.md"):
            text = (PROJECT_ROOT / relative).read_text(encoding="utf-8")
            self.assertIn("dev.slice/dev-gates.slice", text, relative)
            self.assertIn("/dev-gates.slice/buildkit", text, relative)
        # the governed claim is scoped to the measured builder everywhere it is made
        for relative in ("docs/DEV.md", "docs/CONSUMERS.md", "README.md"):
            text = (PROJECT_ROOT / relative).read_text(encoding="utf-8")
            self.assertIn("docker-driver builder `default`", text, relative)
            self.assertIn("/proc/<pid>/cgroup", text, relative)
        self.assertIn("1,533,550,592", (PROJECT_ROOT / "docs/DEV.md").read_text(encoding="utf-8"))
        self.assertIn("docker-driver builder `default`", (PROJECT_ROOT / "CHANGES.md").read_text(encoding="utf-8"))

    def test_probe_containers_are_governed_and_networkless(self) -> None:
        _, calls = self.run_build("--cgroup-parent", self.SLICE)
        probes = [c for c in calls if c[0] == "run"]
        self.assertGreaterEqual(len(probes), 4)
        for probe in probes:
            self.assertEqual(probe[probe.index("--network") + 1], "none")
            self.assertEqual(probe[probe.index("--cap-drop") + 1], "ALL")
            # `docker run` takes the slice NAME: the systemd cgroup driver refuses the path form
            self.assertEqual(probe[probe.index("--cgroup-parent") + 1], self.SLICE)
            self.assertIn("--pull", probe)

    def test_record_lists_the_whole_package_diff_and_the_toolchain(self) -> None:
        record = self.tmp / "record.tsv"
        proc, _ = self.run_build("--cgroup-parent", self.SLICE, "--record", str(record))
        self.assertEqual(proc.returncode, 0, proc.stderr)
        lines = record.read_text().splitlines()
        header = [line for line in lines if line.startswith("#")]
        self.assertTrue(any("BUILD date" in line and "release date" in line for line in header), header)
        self.assertTrue(any("base=tester-unified-rust:local" in line and "base_id=sha256:" + "a" * 64 in line for line in header), header)
        rows = {line.split("\t")[0]: line.split("\t") for line in lines if not line.startswith("#")}
        # added AND upgraded packages (the dependency closure), never the unchanged ones
        for name, version in (("gdb", "13.1-3"), ("libpython3.11", "3.11.2-6"), ("libdebuginfod1", "0.188-2"), ("ca-certificates", "20240203")):
            self.assertEqual(rows[name][1], version, name)
            self.assertRegex(rows[name][2], r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z$")
        self.assertNotIn("bash", rows)
        self.assertNotIn("libc6", rows)
        self.assertEqual(rows["rustc"][1], "rustc 1.99.0 (abcdef 2026-01-01)")
        self.assertEqual(rows["rust-analyzer"][1], "rust-analyzer 1.99.0")
        components = [line for line in lines if line.startswith("component\t")]
        self.assertEqual(len(components), 2)
        self.assertIn("packages added or upgraded", proc.stdout)


if __name__ == "__main__":
    unittest.main()
