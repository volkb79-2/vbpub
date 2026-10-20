"""MDT-HS-05: host-setup owns the writeback and root-queue settings.

Keys IO_WRITEBACK_DIRTY_BYTES, IO_WRITEBACK_DIRTY_BACKGROUND_BYTES, IO_ROOT_QUEUE_NR_REQUESTS,
IO_ROOT_QUEUE_MAX_SECTORS_KB; grammar = a positive integer or `kernel` (empty = kernel).  Everything here
runs against fakes (PATH shims, a fake proc and sysfs); nothing touches a host.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent))
from test_io_governance import Host, LIB, ROOT, _load, _write_exec  # noqa: E402

WIZARD = _load(ROOT / "mdt-host-setup-wizard.py", "mdt_wizard_hs05")
KEYS = ("IO_WRITEBACK_DIRTY_BYTES", "IO_WRITEBACK_DIRTY_BACKGROUND_BYTES",
        "IO_ROOT_QUEUE_NR_REQUESTS", "IO_ROOT_QUEUE_MAX_SECTORS_KB")
FULL = {"IO_WRITEBACK_DIRTY_BYTES": "16777216", "IO_WRITEBACK_DIRTY_BACKGROUND_BYTES": "4194304",
        "IO_ROOT_QUEUE_NR_REQUESTS": "16", "IO_ROOT_QUEUE_MAX_SECTORS_KB": "64"}
ALL_KERNEL = {k: "kernel" for k in KEYS}


class ValidatorTests(unittest.TestCase):
    """ONE validator source: the wizard prompts and install.sh --validate-config both use these."""

    def test_grammar_number_kernel_or_empty(self) -> None:
        v = WIZARD._config_value_validators()
        for key in KEYS:
            for good in ("kernel", "", "8192" if "DIRTY" in key else "64"):
                self.assertIsNone(v[key](good), (key, good))
            for bad in ("-1", "1.5", "16M", "Kernel", "off", "0x10"):
                self.assertIsNotNone(v[key](bad), (key, bad))

    def test_bounds(self) -> None:
        self.assertIsNotNone(WIZARD.validate_io_nr_requests("3"))
        self.assertIsNone(WIZARD.validate_io_nr_requests("4"))
        self.assertIsNotNone(WIZARD.validate_io_max_sectors_kb("3"))
        self.assertIsNone(WIZARD.validate_io_max_sectors_kb("4"))
        self.assertIsNotNone(WIZARD.validate_io_dirty_bytes("0"))

    def test_background_must_be_below_dirty_only_when_both_are_numbers(self) -> None:
        rel = WIZARD.io_writeback_relation_errors
        self.assertTrue(rel({"IO_WRITEBACK_DIRTY_BYTES": "4194304", "IO_WRITEBACK_DIRTY_BACKGROUND_BYTES": "4194304"}))
        self.assertTrue(rel({"IO_WRITEBACK_DIRTY_BYTES": "4194304", "IO_WRITEBACK_DIRTY_BACKGROUND_BYTES": "8388608"}))
        self.assertEqual(rel({"IO_WRITEBACK_DIRTY_BYTES": "16777216", "IO_WRITEBACK_DIRTY_BACKGROUND_BYTES": "4194304"}), [])
        self.assertEqual(rel({"IO_WRITEBACK_DIRTY_BYTES": "kernel", "IO_WRITEBACK_DIRTY_BACKGROUND_BYTES": "4194304"}), [])
        self.assertEqual(rel({"IO_WRITEBACK_DIRTY_BYTES": "16777216"}), [])

    def test_the_candidate_validator_applies_the_relation_and_the_grammar(self) -> None:
        example = WIZARD.parse_env_file((ROOT / "host-setup.env.example").read_text())
        meminfo = {"MemTotal": 16_000_000, "MemAvailable": 8_000_000}
        base = dict(example)
        base.update(FULL)
        WIZARD.validate_host_setup_values(base, example, meminfo, config_label="c", example_label="e", meminfo_label="m")
        bad = dict(base, IO_WRITEBACK_DIRTY_BACKGROUND_BYTES="16777216")
        with self.assertRaisesRegex(WIZARD.TemplateError, "must be below"):
            WIZARD.validate_host_setup_values(bad, example, meminfo, config_label="c", example_label="e", meminfo_label="m")
        bad = dict(base, IO_ROOT_QUEUE_NR_REQUESTS="2")
        with self.assertRaisesRegex(WIZARD.TemplateError, "IO_ROOT_QUEUE_NR_REQUESTS"):
            WIZARD.validate_host_setup_values(bad, example, meminfo, config_label="c", example_label="e", meminfo_label="m")

    def test_the_disk_bound_is_checked_by_the_same_module(self) -> None:
        self.assertIsNone(WIZARD.io_max_sectors_hw_error("64", 1280))
        self.assertIsNone(WIZARD.io_max_sectors_hw_error("1280", 1280))
        self.assertIn("exceeds", WIZARD.io_max_sectors_hw_error("4096", 1280))
        self.assertIsNone(WIZARD.io_max_sectors_hw_error("kernel", 1280))
        install = (ROOT / "install.sh").read_text()
        self.assertIn("--check-max-sectors-kb", install)
        main = (ROOT / "mdt-host-setup-wizard.py").read_text()
        self.assertIn("io_max_sectors_hw_error(", main[main.index("def main("):])

    def test_the_keys_are_declared_default_to_kernel_and_are_runtime_optional(self) -> None:
        example = WIZARD.parse_env_file((ROOT / "host-setup.env.example").read_text())
        optional = WIZARD.runtime_optional_pattern()
        for key in KEYS:
            self.assertEqual(example[key], "kernel", key)
            self.assertTrue(optional.fullmatch(key), key)
        text = (ROOT / "host-setup.env.example").read_text()
        for anchor in ("HOST-IO-INTERFERENCE.md", "4.2", "4.4", "4.5", "section 5", "NOT a default"):
            self.assertIn(anchor, text)


class Fixture(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.host = Host(Path(self._tmp.name))
        base = Path(self._tmp.name)
        self.vm = self.host.proc / "sys" / "vm"
        self.vm.mkdir(parents=True)
        for name, value in (("dirty_bytes", "0"), ("dirty_background_bytes", "0")):
            (self.vm / name).write_text(value + "\n")
        self.queue = self.host.sys / "block" / "vda" / "queue"
        self.queue.mkdir(parents=True)
        for name, value in (("nr_requests", "256"), ("max_sectors_kb", "4096"), ("max_hw_sectors_kb", "4096")):
            (self.queue / name).write_text(value + "\n")
        self.sd = base / "sysctl.d"
        self.ud = base / "udev"
        self.sd.mkdir()
        self.ud.mkdir()
        # a sysctl that really "applies" the file into the fake /proc, a udevadm that applies the rule's
        # ATTR assignments into the fake sysfs; FAKE_SYSCTL_NOOP / FAKE_UDEV_NOOP make them lie
        _write_exec(self.host.bin / "sysctl", """#!__PY__
import os, sys, re
if os.environ.get("FAKE_SYSCTL_NOOP"): sys.exit(0)
args = sys.argv[1:]
if args[:1] == ["-p"]:
    for line in open(args[1]):
        m = re.match(r"\\s*vm\\.(\\w+)\\s*=\\s*(\\S+)", line)
        if m: open(os.path.join(os.environ["MDT_PROC_ROOT"], "sys/vm", m.group(1)), "w").write(m.group(2) + "\\n")
""".replace("__PY__", sys.executable))
        _write_exec(self.host.bin / "udevadm", """#!__PY__
import os, sys, re, glob
args = sys.argv[1:]
if args[:1] == ["trigger"] and not os.environ.get("FAKE_UDEV_NOOP"):
    for rule in sorted(glob.glob(os.path.join(os.environ["MDT_UDEV_RULES_D"], "*.rules"))):
        text = open(rule).read()
        m = re.search(r'KERNEL=="(\\w+)"', text)
        if not m: continue
        for attr, val in re.findall(r'ATTR\\{queue/(\\w+)\\}="(\\d+)"', text):
            open(os.path.join(os.environ["MDT_SYSFS_ROOT"], "block", m.group(1), "queue", attr), "w").write(val + "\\n")
sys.exit(0)
""".replace("__PY__", sys.executable))

    def env(self, **cfg: str) -> dict[str, str]:
        env = os.environ.copy()
        env.update({
            "PATH": f"{self.host.bin}:{os.environ['PATH']}", "MDT_SYSFS_ROOT": str(self.host.sys),
            "MDT_PROC_ROOT": str(self.host.proc), "MDT_SYSCTL_D": str(self.sd), "MDT_UDEV_RULES_D": str(self.ud),
            "MDT_ROOT_TARGET": "/", "FAKE_FINDMNT_SOURCE": "/dev/null", "HERE": str(ROOT),
        })
        for key in KEYS:
            env.pop(key, None)
        env.update(cfg)
        return env

    def bash(self, expr: str, **cfg: str) -> subprocess.CompletedProcess:
        return subprocess.run(["bash", "-c", f'log(){{ :; }}; . "{LIB}"; {expr}'], env=self.env(**cfg),
                              text=True, capture_output=True)

    def live(self) -> dict[str, str]:
        return {
            "dirty": (self.vm / "dirty_bytes").read_text().strip(),
            "bg": (self.vm / "dirty_background_bytes").read_text().strip(),
            "nr": (self.queue / "nr_requests").read_text().strip(),
            "sectors": (self.queue / "max_sectors_kb").read_text().strip(),
        }

    def interim(self) -> tuple[Path, Path]:
        s = self.sd / "90-mdt-io-writeback-interim.conf"
        u = self.ud / "61-mdt-io-queue-interim.rules"
        s.write_text("vm.dirty_bytes = 16777216\n")
        u.write_text('KERNEL=="vda", ATTR{queue/nr_requests}="16"\n')
        return s, u


class RenderTests(Fixture):
    def test_sysctl_holds_only_the_set_keys_with_a_header(self) -> None:
        r = self.bash("_mdt_render_writeback_sysctl", **{**ALL_KERNEL, "IO_WRITEBACK_DIRTY_BYTES": "16777216"})
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("vm.dirty_bytes = 16777216", r.stdout)
        self.assertNotIn("dirty_background", r.stdout.replace("IO_WRITEBACK_DIRTY_BACKGROUND", ""))
        self.assertIn("host-setup.env", r.stdout)
        self.assertIn("HOST-IO-INTERFERENCE.md", r.stdout)

    def test_every_key_kernel_or_empty_renders_nothing(self) -> None:
        self.assertEqual(self.bash("_mdt_render_writeback_sysctl", **ALL_KERNEL).returncode, 1)
        self.assertEqual(self.bash("_mdt_render_writeback_sysctl").returncode, 1)  # unset == kernel
        self.assertEqual(self.bash('_mdt_render_queue_udev vda', **ALL_KERNEL).returncode, 1)
        self.assertEqual(self.bash('_mdt_render_queue_udev vda', IO_ROOT_QUEUE_NR_REQUESTS="").returncode, 1)

    def test_the_rule_matches_the_derived_whole_disk_and_only_the_set_attributes(self) -> None:
        derived = self.bash("_mdt_root_disk_node", **FULL)
        self.assertEqual(derived.stdout.strip(), "/dev/vda", derived.stderr)  # /dev/null is a partition of vda
        r = self.bash('_mdt_render_queue_udev "$(basename "$(_mdt_root_disk_node)")"', **{**ALL_KERNEL, "IO_ROOT_QUEUE_NR_REQUESTS": "16"})
        self.assertIn('KERNEL=="vda"', r.stdout)
        self.assertIn('ATTR{queue/nr_requests}="16"', r.stdout)
        self.assertNotIn("max_sectors_kb}", r.stdout)
        self.assertIn("AFTER 60-mdt-bfq-scheduler.rules", r.stdout)

    def test_the_rule_file_sorts_after_the_scheduler_rule_and_its_interim_sorts_before_it(self) -> None:
        names = sorted(["60-mdt-bfq-scheduler.rules", "61-mdt-io-queue.rules", "61-mdt-io-queue-interim.rules"])
        self.assertEqual(names, ["60-mdt-bfq-scheduler.rules", "61-mdt-io-queue-interim.rules", "61-mdt-io-queue.rules"])
        sysctls = sorted(["90-mdt-io-writeback.conf", "90-mdt-io-writeback-interim.conf"])
        self.assertEqual(sysctls[-1], "90-mdt-io-writeback.conf")   # sysctl.d: the last one wins
        source = LIB.read_text()
        self.assertIn("MDT_IO_WB_UDEV_FILE_NAME=61-mdt-io-queue.rules", source)

    def test_an_underivable_disk_is_refused_not_invented(self) -> None:
        r = self.bash("_mdt_root_disk_node", FAKE_FINDMNT_SOURCE="overlay")
        self.assertEqual(r.returncode, 1)
        self.assertEqual(r.stdout.strip(), "")


class ApplyTests(Fixture):
    def apply(self, **cfg: str) -> subprocess.CompletedProcess:
        return self.bash("_mdt_apply_writeback_settings", **cfg)

    def test_apply_renders_applies_verifies_and_retires_the_interim_files_last(self) -> None:
        interim_s, interim_u = self.interim()
        r = self.apply(**FULL)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertTrue((self.sd / "90-mdt-io-writeback.conf").is_file())
        self.assertTrue((self.ud / "61-mdt-io-queue.rules").is_file())
        self.assertEqual(self.live(), {"dirty": "16777216", "bg": "4194304", "nr": "16", "sectors": "64"})
        self.assertFalse(interim_s.exists())
        self.assertFalse(interim_u.exists())
        self.assertIn("NOTICE: removed the interim file", r.stdout)

    def test_a_readback_mismatch_fails_names_the_file_and_keeps_the_interim_files(self) -> None:
        interim_s, interim_u = self.interim()
        r = self.bash("_mdt_apply_writeback_settings", **FULL, FAKE_UDEV_NOOP="1")
        self.assertEqual(r.returncode, 2, r.stdout + r.stderr)
        self.assertIn("live /sys/block/vda/queue/nr_requests is 256 but the intended value is 16", r.stderr)
        self.assertIn("61-mdt-io-queue.rules", r.stderr)
        self.assertIn("Remedy:", r.stderr)
        self.assertTrue(interim_s.exists() and interim_u.exists(), "interim removal only after success")

    def test_a_sysctl_mismatch_fails_the_same_way(self) -> None:
        interim_s, interim_u = self.interim()
        r = self.bash("_mdt_apply_writeback_settings", **FULL, FAKE_SYSCTL_NOOP="1")
        self.assertEqual(r.returncode, 2, r.stdout + r.stderr)
        self.assertIn("live vm.dirty_bytes is 0 but the intended value is 16777216", r.stderr)
        self.assertIn("90-mdt-io-writeback.conf", r.stderr)
        self.assertTrue(interim_s.exists() and interim_u.exists())

    def test_an_unreadable_live_value_is_never_certified(self) -> None:
        (self.queue / "nr_requests").unlink()
        r = self.bash("_mdt_apply_writeback_settings", **FULL, FAKE_UDEV_NOOP="1")
        self.assertEqual(r.returncode, 2, r.stdout + r.stderr)
        self.assertIn("could not read the live /sys/block/vda/queue/nr_requests", r.stderr)

    def test_a_key_set_back_to_kernel_loses_its_line_and_says_it_cannot_restore_the_value(self) -> None:
        self.assertEqual(self.apply(**FULL).returncode, 0)
        r = self.apply(**{**FULL, "IO_ROOT_QUEUE_MAX_SECTORS_KB": "kernel", "IO_WRITEBACK_DIRTY_BYTES": "kernel",
                          "IO_WRITEBACK_DIRTY_BACKGROUND_BYTES": "kernel"})
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        rule = (self.ud / "61-mdt-io-queue.rules").read_text()
        self.assertNotIn("max_sectors_kb}", rule)
        self.assertIn('ATTR{queue/nr_requests}="16"', rule)
        self.assertFalse((self.sd / "90-mdt-io-writeback.conf").exists(), "no key set: MDT's file is removed")
        self.assertIn("cannot restore the kernel's boot value", r.stderr)
        self.assertIn("max_sectors_kb", r.stderr)
        self.assertIn("vm.dirty_bytes", r.stderr)
        self.assertEqual(self.live()["dirty"], "16777216", "the running value is NOT guessed back")

    def test_all_kernel_with_no_prior_files_changes_nothing(self) -> None:
        r = self.apply(**ALL_KERNEL)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertEqual(list(self.sd.iterdir()), [])
        self.assertEqual(list(self.ud.iterdir()), [])
        self.assertEqual(self.live(), {"dirty": "0", "bg": "0", "nr": "256", "sectors": "4096"})

    def test_queue_keys_with_an_underivable_disk_refuse_before_writing(self) -> None:
        r = self.bash("_mdt_apply_writeback_settings", **FULL, FAKE_FINDMNT_SOURCE="overlay")
        self.assertEqual(r.returncode, 2)
        self.assertIn("cannot be derived", r.stderr)
        self.assertEqual(list(self.ud.iterdir()), [])

    def test_install_sh_runs_the_apply_after_the_bfq_block_and_fails_loudly(self) -> None:
        install = (ROOT / "install.sh").read_text()
        self.assertLess(install.index("60-mdt-bfq-scheduler.rules"), install.index("_mdt_apply_writeback_settings"))
        self.assertIn('WRITEBACK_FAILED=1', install)
        self.assertTrue(install.rstrip().endswith("fi"))
        self.assertIn("exit 2", install[install.index('if [ "$WRITEBACK_FAILED" = 1 ]'):])
        pre = install[:install.index("# === COMMIT POINT")]
        self.assertIn("_mdt_io_queue_keys_set", pre)       # derivation + bound are PREFLIGHT, before the commit point
        self.assertNotIn("_mdt_apply_writeback_settings", pre)


class CheckShTests(Fixture):
    def run_check(self, **cfg: str) -> str:
        text = (ROOT / "scripts" / "check.sh").read_text()
        block = text[text.index("# BEGIN io-writeback-check"):text.index("# END io-writeback-check")]
        harness = self.host.base / "h.sh"
        harness.write_text(
            f'ok(){{ echo "OK $*"; }}; warn(){{ echo "WARN $*"; }}; fail(){{ echo "FAIL $*"; }}; info(){{ echo "INFO $*"; }}\n'
            f'log(){{ :; }}; . "{LIB}"\n' + block + "\n")
        return subprocess.run(["bash", str(harness)], env=self.env(**cfg), text=True, capture_output=True).stdout

    def test_ok_when_the_live_values_match(self) -> None:
        self.assertEqual(self.bash("_mdt_apply_writeback_settings", **FULL).returncode, 0)
        out = self.run_check(**FULL)
        for line in ("OK vm.dirty_bytes = 16777216", "OK vm.dirty_background_bytes = 4194304",
                     "OK vda nr_requests = 16", "OK vda max_sectors_kb = 64"):
            self.assertIn(line, out)
        self.assertNotIn("FAIL", out)

    def test_mismatch_not_managed_and_could_not_read_are_distinct(self) -> None:
        (self.vm / "dirty_bytes").write_text("999\n")                 # MISMATCH
        (self.queue / "nr_requests").unlink()                           # could not read
        out = self.run_check(**{**FULL, "IO_WRITEBACK_DIRTY_BACKGROUND_BYTES": "kernel"})   # not managed
        self.assertIn("FAIL vm.dirty_bytes MISMATCH: live 999, host-setup.env says 16777216", out)
        self.assertIn("INFO vm.dirty_background_bytes: not managed", out)
        self.assertIn("WARN vda nr_requests: could not read", out)
        self.assertNotIn("OK vda nr_requests", out)
        self.assertNotIn("OK vm.dirty_bytes", out)

    def test_an_unreadable_sysctl_is_never_ok(self) -> None:
        (self.vm / "dirty_bytes").unlink()
        out = self.run_check(**FULL)
        self.assertIn("WARN vm.dirty_bytes: could not read", out)
        self.assertNotIn("OK vm.dirty_bytes", out)

    def test_everything_kernel_or_absent_is_not_managed(self) -> None:
        for cfg in (ALL_KERNEL, {}):
            out = self.run_check(**cfg)
            self.assertEqual(out.count("not managed"), 4, out)
            self.assertNotIn("OK", out)

    def test_an_underivable_disk_is_reported_not_skipped(self) -> None:
        out = self.run_check(**FULL, FAKE_FINDMNT_SOURCE="overlay")
        self.assertIn("could not derive the whole disk", out)


class WizardProposalTests(unittest.TestCase):
    """Precedence per key: a CURRENT benchmark > the existing configured value > `kernel`, with the
    gstammtisch example shown (labelled) but never defaulted."""

    DEVNO = "254:0"

    def result(self, **over) -> dict:
        doc = {
            "schema": 1, "run": {"status": "complete"}, "quality": {"plausible": True, "limits": []},
            "identity": {"disk": {"devno": self.DEVNO}},
            "proposals": {"writeback": {
                "dirty_bytes": {"value": 8388608, "rule": "R1", "confidence": "measured+confirmed"},
                "dirty_background_bytes": {"value": 2097152, "rule": "R1b", "confidence": "ratio-inferred"},
                "nr_requests": {"value": 8, "rule": "R2", "confidence": "measured+confirmed"},
                "max_sectors_kb": {"value": 128, "rule": "R3", "confidence": "measured", "flags": ["read_floor_violated"]},
            }, "target_missed": None},
        }
        for key, value in over.items():
            doc[key] = value
        return doc

    def write(self, doc) -> Path:
        d = Path(tempfile.mkdtemp())
        self.addCleanup(lambda: __import__("shutil").rmtree(d, ignore_errors=True))
        p = d / "result.json"
        p.write_text(doc if isinstance(doc, str) else json.dumps(doc))
        return p

    def runner(self, majmin: str | None = DEVNO, hw: int | None = 1280):
        def run(cmd, **kwargs):
            expr = cmd[2]
            if "_mdt_dev_majmin" in expr:
                out, rc = (majmin or ""), (0 if majmin else 1)
            elif "_mdt_io_systemd_dev" in expr:
                out, rc = "/dev/vda", 0
            elif "max_hw_sectors_kb" in expr:
                out, rc = (str(hw) if hw is not None else ""), (0 if hw is not None else 1)
            else:
                out, rc = "", 1
            return subprocess.CompletedProcess(cmd, rc, out + "\n", "")
        return run

    def step(self, cfg, result_path, **kw):
        asked = {}

        def fake_ask(label, default, validate=None, *, default_source=None, **_):
            key = label.rsplit("(", 1)[1].rstrip(")")
            asked[key] = (default, default_source)
            return default

        printed: list[str] = []
        with mock.patch.object(WIZARD, "ask", side_effect=fake_ask), \
             mock.patch.object(WIZARD, "out", side_effect=lambda text="", **k: printed.append(text)):
            chosen = WIZARD.step_io_writeback(cfg, {}, io_dev_path="/dev/null", iobench_result=result_path,
                                              runner=kw.get("runner", self.runner()))
        return chosen, asked, "\n".join(printed)

    def test_a_current_benchmark_wins_and_carries_its_rule_and_flags(self) -> None:
        chosen, asked, printed = self.step({"IO_WRITEBACK_DIRTY_BYTES": "999"}, self.write(self.result()))
        self.assertEqual(chosen["IO_WRITEBACK_DIRTY_BYTES"], "8388608")
        self.assertEqual(chosen["IO_ROOT_QUEUE_MAX_SECTORS_KB"], "128")
        self.assertIn("rule R1", asked["IO_WRITEBACK_DIRTY_BYTES"][1])
        self.assertIn("read_floor_violated", asked["IO_ROOT_QUEUE_MAX_SECTORS_KB"][1])

    def test_without_a_benchmark_the_existing_value_wins_over_kernel(self) -> None:
        cfg = {"IO_WRITEBACK_DIRTY_BYTES": "33554432", "IO_ROOT_QUEUE_NR_REQUESTS": "kernel"}
        chosen, asked, printed = self.step(cfg, Path("/nonexistent/result.json"))
        self.assertEqual(chosen["IO_WRITEBACK_DIRTY_BYTES"], "33554432")
        self.assertEqual(asked["IO_WRITEBACK_DIRTY_BYTES"][1], "existing")
        self.assertEqual(chosen["IO_ROOT_QUEUE_NR_REQUESTS"], "kernel")
        self.assertEqual(chosen["IO_WRITEBACK_DIRTY_BACKGROUND_BYTES"], "kernel")
        self.assertIn("absent", printed)

    def test_the_gstammtisch_values_are_an_example_never_the_default(self) -> None:
        chosen, asked, printed = self.step({}, Path("/nonexistent/result.json"))
        self.assertEqual(chosen, {k: "kernel" for k in KEYS})
        self.assertIn("NOT a default", printed)
        self.assertIn("measured on gstammtisch 2026-10-20", printed)
        self.assertIn("16777216", printed)
        for default, source in asked.values():
            self.assertEqual(default, "kernel")
            self.assertIn("policy default", source)

    def test_an_unusable_benchmark_falls_through_with_its_reason(self) -> None:
        cases = {
            "corrupt": "{not json",
            "unsupported": self.result(schema=2),
            "incomplete": self.result(run={"status": "aborted"}),
            "implausible": self.result(quality={"plausible": False}),
            "stale": self.result(identity={"disk": {"devno": "8:0"}}),
        }
        for state, doc in cases.items():
            with self.subTest(state=state):
                chosen, _, printed = self.step({"IO_ROOT_QUEUE_NR_REQUESTS": "12"}, self.write(doc))
                self.assertEqual(chosen["IO_ROOT_QUEUE_NR_REQUESTS"], "12", "falls to the configured value")
                self.assertIn(state, printed)
        loaded = WIZARD.load_iobench_writeback(self.write(self.result()), None)
        self.assertEqual(loaded.state, "unverified")         # identity cannot be compared: not used
        self.assertIsNone(loaded.values)

    def test_formula_only_target_missed_and_limits_are_labelled_not_hidden(self) -> None:
        doc = self.result(quality={"plausible": True, "limits": ["fresh_write not measured"]})
        doc["proposals"]["target_missed"] = {"what": "W_s"}
        doc["proposals"]["writeback"]["dirty_bytes"]["confidence"] = "formula-only"
        chosen, asked, _ = self.step({}, self.write(doc))
        label = asked["IO_WRITEBACK_DIRTY_BYTES"][1]
        for word in ("formula-only", "target missed", "fresh_write not measured"):
            self.assertIn(word, label)

    def test_the_reader_is_coupled_to_the_schema_file_only(self) -> None:
        source = (ROOT / "mdt-host-setup-wizard.py").read_text()
        reader = source[source.index("def load_iobench_writeback"):source.index("_IO_WRITEBACK_EXPLAIN")]
        self.assertNotIn("subprocess.", reader)
        self.assertNotIn("runner", reader)
        self.assertNotIn("import iobench", source)
        self.assertEqual(WIZARD.load_iobench_writeback(Path("/nonexistent"), "254:0").state, "absent")

    def test_the_prompt_validators_are_the_shared_ones_incl_the_disk_bound(self) -> None:
        seen = {}

        def fake_ask(label, default, validate=None, **_):
            seen[label.rsplit("(", 1)[1].rstrip(")")] = validate
            return default

        with mock.patch.object(WIZARD, "ask", side_effect=fake_ask), mock.patch.object(WIZARD, "out"):
            WIZARD.step_io_writeback({}, {}, io_dev_path="/dev/null", iobench_result=Path("/nonexistent"),
                                     runner=self.runner(hw=1280))
        self.assertIsNotNone(seen["IO_ROOT_QUEUE_MAX_SECTORS_KB"]("4096"))     # above max_hw 1280
        self.assertIsNone(seen["IO_ROOT_QUEUE_MAX_SECTORS_KB"]("64"))
        self.assertIsNotNone(seen["IO_ROOT_QUEUE_NR_REQUESTS"]("2"))
        self.assertIsNotNone(seen["IO_WRITEBACK_DIRTY_BACKGROUND_BYTES"]("-3"))

    def test_the_wizard_main_asks_the_step_after_the_io_settings(self) -> None:
        source = (ROOT / "mdt-host-setup-wizard.py").read_text()
        main = source[source.index("def main("):]
        self.assertLess(main.index("step_io_settings("), main.index("step_io_writeback("))


class VmLaneTests(unittest.TestCase):
    def test_the_vm_lane_runs_the_writeback_acceptance_and_it_checks_live_values(self) -> None:
        lane = (ROOT / "tests" / "run-vm-acceptance.sh").read_text()
        self.assertLess(lane.index("vm-governance-acceptance.sh"), lane.index("vm-writeback-acceptance.sh"))
        self.assertLess(lane.index("vm-writeback-acceptance.sh"), lane.index("vm-upgrade-acceptance.sh"))
        script = (ROOT / "tests" / "vm-writeback-acceptance.sh").read_text()
        for needle in ("/proc/sys/vm/dirty_bytes", "queue/nr_requests", "queue/max_sectors_kb", "_mdt_root_disk_node",
                       "interim", "mdt-host-check.sh", "kernel"):
            self.assertIn(needle, script)
        self.assertTrue(os.access(ROOT / "tests" / "vm-writeback-acceptance.sh", os.X_OK))


class DocsTests(unittest.TestCase):
    def test_docs_describe_the_keys_the_measurement_and_the_ordering(self) -> None:
        readme = " ".join((ROOT / "README.md").read_text().split())
        design = " ".join((ROOT.parent / "docs" / "DESIGN-GUIDE.md").read_text().split())
        changes = (ROOT.parent / "CHANGES.md").read_text()
        for key in KEYS:
            self.assertIn(key, readme)
        for needle in ("MDT-HS-05", "HOST-IO-INTERFERENCE.md", "61-mdt-io-queue.rules", "60-mdt-bfq-scheduler.rules",
                       "1.2-1.4 GB/s", "Read-cost", "host-global", "kernel"):
            self.assertIn(needle, design, needle)
        self.assertIn("MDT-HS-05", changes)


if __name__ == "__main__":
    unittest.main()
