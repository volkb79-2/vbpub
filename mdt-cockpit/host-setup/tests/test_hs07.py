"""MDT-HS-07: host-setup owns the IO scheduler and io.cost (model + QoS); BFQ is retired as the weight mechanism.

Keys IO_ROOT_SCHEDULER (mq-deadline | none | bfq | kernel), IO_COST_MODEL (kernel | six coefficients) and
IO_COST_QOS (off | six numbers); empty/absent = not managed.  Everything runs against fakes (PATH shims, a fake
sysfs and a fake cgroup root); nothing touches a host.
"""
from __future__ import annotations

import hashlib
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent))
from test_hs05 import Fixture  # noqa: E402
from test_io_governance import LIB, ROOT, _load, _write_exec  # noqa: E402

WIZARD = _load(ROOT / "mdt-host-setup-wizard.py", "mdt_wizard_hs07")
MODEL = "290000000 4900 4900 14680064 2800 2800"
QOS = "95 5000 95 20000 5 400"
KERNEL_MODEL = ("254:0 ctrl=user model=linear rbps=290000000 rseqiops=4900 rrandiops=4900 "
                "wbps=14680064 wseqiops=2800 wrandiops=2800")
KERNEL_QOS = "254:0 enable=1 ctrl=user rpct=95.00 rlat=5000 wpct=95.00 wlat=20000 min=5.00 max=400.00"
REPO_BFQ_RULE = ROOT / "etc" / "udev" / "rules.d" / "60-bfq-scheduler.rules"
GSTAMM_BFQ_RULE = ROOT.parents[1] / "scripts" / "gstammtisch-guide" / "files" / "etc" / "udev" / "rules.d" / "60-bfq-scheduler.rules"


class ValidatorTests(unittest.TestCase):
    def test_scheduler_grammar_and_the_disk_list(self) -> None:
        for good in ("mq-deadline", "none", "bfq", "kernel", ""):
            self.assertIsNone(WIZARD.validate_io_root_scheduler(good), good)
        for bad in ("kyber", "BFQ", "mq_deadline", "off", "bfq "):
            self.assertIsNotNone(WIZARD.validate_io_root_scheduler(bad), bad)
        listed = ["none", "mq-deadline"]
        self.assertIsNone(WIZARD.io_scheduler_available_error("mq-deadline", listed))
        self.assertIn("not offered by this disk", WIZARD.io_scheduler_available_error("kyber", listed))
        self.assertIsNone(WIZARD.io_scheduler_available_error("bfq", listed))      # module loaded at apply time
        self.assertIsNone(WIZARD.io_scheduler_available_error("kernel", []))

    def test_model_grammar(self) -> None:
        v = WIZARD.validate_io_cost_model
        for good in ("kernel", "", MODEL):
            self.assertIsNone(v(good), good)
        for bad in ("290000000 4900 4900 14680064 2800", MODEL + " 1", "0 4900 4900 14680064 2800 2800",
                    "029 4900 4900 14680064 2800 2800", MODEL.replace(" ", ","), "auto", MODEL.replace("4900", "4.9", 1)):
            self.assertIsNotNone(v(bad), bad)

    def test_qos_grammar_and_the_kernel_bounds(self) -> None:
        v = WIZARD.validate_io_cost_qos
        for good in ("off", "", QOS, "99.5 1 100 1 1 10000", "0 5000 0 20000 1.5 1.5"):
            self.assertIsNone(v(good), good)
        for bad, why in (("101 5000 95 20000 5 400", "above 100"), ("95 5000 100.01 20000 5 400", "above 100"),
                         ("95 5000 95 20000 0.5 400", "clamp"), ("95 5000 95 20000 5 10001", "clamp"),
                         ("95 5000 95 20000 50 40", "above max"), ("95 0 95 20000 5 400", "expected"),
                         ("95 5000 95 20000 5", "expected"), ("95.123 5000 95 20000 5 400", "expected"),
                         ("on", "expected")):
            error = v(bad)
            self.assertIsNotNone(error, bad)

    def test_keys_are_declared_validated_and_runtime_optional(self) -> None:
        example = WIZARD.parse_env_file((ROOT / "host-setup.env.example").read_text())
        optional = WIZARD.runtime_optional_pattern()
        self.assertEqual(example["IO_ROOT_SCHEDULER"], "kernel")
        self.assertEqual(example["IO_COST_MODEL"], "kernel")
        self.assertEqual(example["IO_COST_QOS"], "off")          # the explicit policy default
        validators = WIZARD._config_value_validators()
        for key in ("IO_ROOT_SCHEDULER", "IO_COST_MODEL", "IO_COST_QOS"):
            self.assertIn(key, validators)
            self.assertTrue(optional.fullmatch(key), key)
        text = (ROOT / "host-setup.env.example").read_text()
        for anchor in ("IO-STALL-TESTING-GUIDE.md section 11", "io.latency is NOT available", "wbt",
                       "NOT a default", "ioc_qos_write"):
            self.assertIn(anchor, text)

    def test_the_candidate_validator_rejects_a_bad_value(self) -> None:
        example = WIZARD.parse_env_file((ROOT / "host-setup.env.example").read_text())
        meminfo = {"MemTotal": 16_000_000, "MemAvailable": 8_000_000}
        base = dict(example, IO_ROOT_SCHEDULER="mq-deadline", IO_COST_MODEL=MODEL, IO_COST_QOS=QOS)
        WIZARD.validate_host_setup_values(base, example, meminfo, config_label="c", example_label="e", meminfo_label="m")
        with self.assertRaisesRegex(WIZARD.TemplateError, "IO_COST_QOS"):
            WIZARD.validate_host_setup_values(dict(base, IO_COST_QOS="95 5000 95 20000 50 40"), example, meminfo,
                                              config_label="c", example_label="e", meminfo_label="m")

    def test_install_checks_the_scheduler_with_the_same_module(self) -> None:
        install = (ROOT / "install.sh").read_text()
        self.assertIn("--check-scheduler", install)
        self.assertIn("--available-schedulers", install)
        main = (ROOT / "mdt-host-setup-wizard.py").read_text()
        self.assertIn("io_scheduler_available_error(", main[main.index("def main("):])


class SchedulerFixture(Fixture):
    def setUp(self) -> None:
        super().setUp()
        self.sched = self.queue / "scheduler"
        self.sched.write_text("[none] mq-deadline kyber bfq\n")
        self.modules = self.host.base / "modules-load.d"
        self.modules.mkdir()
        self.cgroot = self.host.base / "cgroot"
        self.cgroot.mkdir()
        (self.cgroot / "io.cost.model").write_text("")
        (self.cgroot / "io.cost.qos").write_text("")
        # udevadm that applies EVERY rule in sorted order (later wins): the scheduler and the queue attributes
        _write_exec(self.host.bin / "udevadm", """#!__PY__
import os, sys, re, glob
args = sys.argv[1:]
if args[:1] == ["trigger"] and not os.environ.get("FAKE_UDEV_NOOP"):
    sysroot = os.environ["MDT_SYSFS_ROOT"]
    sched = None
    attrs = {}
    for rule in sorted(glob.glob(os.path.join(os.environ["MDT_UDEV_RULES_D"], "*.rules"))):
        for line in open(rule):
            if line.lstrip().startswith("#"): continue
            m = re.search(r'KERNEL=="([^"]+)"', line)
            if not m: continue
            import fnmatch
            if not fnmatch.fnmatch("vda", m.group(1)): continue
            s = re.search(r'ATTR\\{queue/scheduler\\}="([^"]+)"', line)
            if s: sched = s.group(1)
            for a, v in re.findall(r'ATTR\\{queue/(nr_requests|max_sectors_kb)\\}="(\\d+)"', line): attrs[a] = v
    q = os.path.join(sysroot, "block", "vda", "queue")
    if sched:
        names = ["none", "mq-deadline", "kyber", "bfq"]
        open(os.path.join(q, "scheduler"), "w").write(" ".join(f"[{n}]" if n == sched else n for n in names) + "\\n")
    for a, v in attrs.items(): open(os.path.join(q, a), "w").write(v + "\\n")
sys.exit(0)
""".replace("__PY__", sys.executable))
        _write_exec(self.host.bin / "modprobe", "#!/bin/sh\nexit 0\n")

    def env(self, **cfg: str) -> dict[str, str]:
        env = super().env(**cfg)
        for key in ("IO_ROOT_SCHEDULER", "IO_COST_MODEL", "IO_COST_QOS"):
            env.pop(key, None)
        env.update({"MDT_MODULES_LOAD_D": str(self.modules), "MDT_CGROUP_ROOT": str(self.cgroot),
                    "MDT_UDEV_SCAN_DIRS": str(self.ud), "HERE": str(ROOT)})
        env.update(cfg)
        return env

    def rule(self) -> str:
        return (self.ud / "60-mdt-io-scheduler.rules").read_text()


class SchedulerReadTests(SchedulerFixture):
    def test_the_list_and_the_current_scheduler_are_read_from_the_disk(self) -> None:
        r = self.bash("_mdt_io_sched_list vda")
        self.assertEqual(r.stdout.split(), ["none", "mq-deadline", "kyber", "bfq"])
        self.assertEqual(self.bash("_mdt_io_sched_current vda").stdout.strip(), "none")
        self.sched.write_text("none [mq-deadline] kyber\n")
        self.assertEqual(self.bash("_mdt_io_sched_current vda").stdout.strip(), "mq-deadline")
        self.assertEqual(self.bash("_mdt_io_sched_list vda").stdout.split(), ["none", "mq-deadline", "kyber"])
        self.sched.unlink()
        self.assertEqual(self.bash("_mdt_io_sched_list vda").returncode, 1)

    def test_the_wizard_reads_the_list_of_the_root_disk_never_assumes_one(self) -> None:
        def run(cmd, **kwargs):
            self.assertIn("_mdt_io_sched_offered", cmd[2])          # listed OR loadable (H-58)
            self.assertIn("_mdt_root_disk_node", cmd[2])
            return subprocess.CompletedProcess(cmd, 0, "none\nmq-deadline\n", "")
        self.assertEqual(WIZARD.read_scheduler_list(runner=run), ["none", "mq-deadline"])
        self.assertEqual(WIZARD.read_scheduler_list(runner=lambda c, **k: subprocess.CompletedProcess(c, 1, "", "")), [])


class ModuleAvailabilityTests(SchedulerFixture):
    """H-58: queue/scheduler lists only LOADED schedulers; one that is loadable on demand is acceptable."""

    def setUp(self) -> None:
        super().setUp()
        self.mods = self.host.base / "lib-modules"
        (self.mods / "kernel" / "block").mkdir(parents=True)
        self.sched.write_text("[none] kyber\n")                          # mq-deadline and bfq NOT loaded

    def test_module_files_and_builtins_count_as_available(self) -> None:
        env = {"MDT_MODULES_DIR": str(self.mods)}
        self.assertEqual(self.bash("_mdt_sched_module_available mq-deadline", **env).returncode, 1)
        (self.mods / "kernel" / "block" / "mq-deadline.ko.xz").write_text("")
        self.assertEqual(self.bash("_mdt_sched_module_available mq-deadline", **env).returncode, 0)
        (self.mods / "modules.builtin").write_text("kernel/block/bfq.ko\nkernel/block/kyber-iosched.ko\n")
        self.assertEqual(self.bash("_mdt_sched_module_available bfq", **env).returncode, 0)
        self.assertEqual(self.bash("_mdt_sched_module_available nonsense", **env).returncode, 1)

    def test_offered_is_the_listed_plus_the_loadable(self) -> None:
        (self.mods / "kernel" / "block" / "mq-deadline.ko.xz").write_text("")
        r = self.bash("_mdt_io_sched_offered vda", MDT_MODULES_DIR=str(self.mods))
        self.assertEqual(sorted(r.stdout.split()), ["kyber", "mq-deadline", "none"])
        r = self.bash("_mdt_io_sched_offered vda", MDT_MODULES_DIR=str(self.host.base / "no-modules"))
        self.assertEqual(sorted(r.stdout.split()), ["kyber", "none"])
        # the shared validator: loadable is accepted, neither listed nor loadable is the only refusal
        self.assertIsNone(WIZARD.io_scheduler_available_error("mq-deadline", ["kyber", "none", "mq-deadline"]))
        self.assertIn("not offered", WIZARD.io_scheduler_available_error("mq-deadline", ["kyber", "none"]))

    def test_a_not_yet_loaded_scheduler_is_applied_and_proved_by_the_readback(self) -> None:
        (self.mods / "kernel" / "block" / "mq-deadline.ko.xz").write_text("")
        r = self.bash("_mdt_apply_scheduler_settings", IO_ROOT_SCHEDULER="mq-deadline", MDT_MODULES_DIR=str(self.mods))
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertIn("[mq-deadline]", self.sched.read_text())
        # and if the kernel never makes it active, the read-back fails loudly
        self.sched.write_text("[none] kyber\n")
        r = self.bash("_mdt_apply_scheduler_settings", IO_ROOT_SCHEDULER="mq-deadline", FAKE_UDEV_NOOP="1")
        self.assertEqual(r.returncode, 2)

    def test_install_preflight_uses_the_offered_list(self) -> None:
        install = (ROOT / "install.sh").read_text()
        self.assertIn("_mdt_io_sched_offered", install)
        self.assertNotIn('_s_list=$(_mdt_io_sched_list', install)


class ReassertTests(SchedulerFixture):
    """H-59: the minute-by-minute reconcile verifies scheduler, queue and io.cost against the env and re-applies."""

    CFG = {"IO_ROOT_SCHEDULER": "mq-deadline", "IO_COST_MODEL": MODEL, "IO_COST_QOS": QOS,
           "IO_ROOT_QUEUE_NR_REQUESTS": "16", "FAKE_FINDMNT_SOURCE": "/dev/null"}

    def installed(self) -> None:
        """A host as install.sh left it: rules rendered, scheduler/queue/io.cost live."""
        r = self.bash("_mdt_apply_scheduler_settings; _mdt_apply_writeback_settings; _mdt_apply_iocost", **self.CFG)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)

    def reassert(self, **extra: str) -> subprocess.CompletedProcess:
        script = f'log(){{ echo "LOG $*"; }}; . "{LIB}"; _mdt_reassert_io_settings; echo "rc=$?"'
        return subprocess.run(["bash", "-c", script], env=self.env(**{**self.CFG, **extra}), text=True, capture_output=True)

    def test_no_drift_is_silent_and_writes_nothing(self) -> None:
        self.installed()
        before = {p.name: p.stat().st_mtime_ns for p in (self.cgroot).iterdir()}
        r = self.reassert()
        self.assertIn("rc=0", r.stdout)
        self.assertNotIn("drift", r.stdout)
        self.assertEqual(before, {p.name: p.stat().st_mtime_ns for p in self.cgroot.iterdir()})

    def test_io_cost_drift_is_logged_and_re_applied(self) -> None:
        self.installed()
        (self.cgroot / "io.cost.qos").write_text("254:0 enable=0 ctrl=user rpct=95.00 rlat=5000 wpct=95.00 wlat=20000 min=5.00 max=400.00\n")
        (self.cgroot / "io.cost.model").write_text("")                    # a re-added disk has no line at all
        r = self.reassert()
        self.assertIn("rc=0", r.stdout, r.stdout + r.stderr)
        self.assertIn("LOG WARN: drift: io.cost qos on 254:0", r.stdout)
        self.assertIn("LOG WARN: drift: io.cost model on 254:0 is '<no line>'", r.stdout)
        self.assertIn("enable=1", (self.cgroot / "io.cost.qos").read_text())
        self.assertIn("rbps=290000000", (self.cgroot / "io.cost.model").read_text())

    def test_the_field_normalised_comparison_does_not_call_kernel_formatting_drift(self) -> None:
        self.installed()
        (self.cgroot / "io.cost.qos").write_text(KERNEL_QOS + "\n")
        (self.cgroot / "io.cost.model").write_text(KERNEL_MODEL + "\n")
        r = self.reassert()
        self.assertNotIn("drift", r.stdout)

    def test_scheduler_and_queue_drift_is_re_applied_through_udev(self) -> None:
        self.installed()
        self.sched.write_text("[none] mq-deadline kyber bfq\n")          # a scheduler switch
        (self.queue / "nr_requests").write_text("256\n")                  # ... which also reset nr_requests
        r = self.reassert()
        self.assertIn("rc=0", r.stdout, r.stdout + r.stderr)
        self.assertIn("LOG WARN: drift: scheduler of vda is 'none'", r.stdout)
        self.assertIn("[mq-deadline]", self.sched.read_text())
        self.assertEqual((self.queue / "nr_requests").read_text().strip(), "16")

    def test_a_failure_to_re_apply_is_loud(self) -> None:
        self.installed()
        self.sched.write_text("[none] mq-deadline kyber bfq\n")
        r = self.reassert(FAKE_UDEV_NOOP="1")                              # udev cannot restore it
        self.assertIn("rc=1", r.stdout)
        self.assertIn("ERROR: live scheduler of vda is none", r.stdout)
        (self.cgroot / "io.cost.qos").unlink()
        (self.cgroot / "io.cost.qos").mkdir()
        r = self.reassert()
        self.assertIn("rc=1", r.stdout)
        self.assertIn("ERROR: io.cost re-apply failed", r.stdout)

    def test_unmanaged_keys_are_never_touched(self) -> None:
        (self.cgroot / "io.cost.qos").write_text("254:0 enable=0\n")
        r = self.reassert(IO_ROOT_SCHEDULER="", IO_COST_MODEL="", IO_COST_QOS="", IO_ROOT_QUEUE_NR_REQUESTS="")
        self.assertIn("rc=0", r.stdout)
        self.assertEqual((self.cgroot / "io.cost.qos").read_text(), "254:0 enable=0\n")

    def test_the_reconcile_calls_it_and_the_docs_say_what_reverts_what(self) -> None:
        reconcile = (ROOT / "scripts" / "mdt-dev-governance-reconcile.sh").read_text()
        self.assertIn("_mdt_reassert_io_settings || MDT_RC=1", reconcile)
        design = " ".join((ROOT.parent / "docs" / "DESIGN-GUIDE.md").read_text().split())
        for needle in ("disk remove/re-add", "re-applies on drift", "What reverts what"):
            self.assertIn(needle, design)


class RenderTests(SchedulerFixture):
    def test_the_scheduler_rule_names_the_derived_disk_and_orders_before_the_queue_rule(self) -> None:
        r = self.bash('_mdt_render_scheduler_udev "$(basename "$(_mdt_root_disk_node)")"', IO_ROOT_SCHEDULER="mq-deadline")
        self.assertIn('KERNEL=="vda"', r.stdout)
        self.assertIn('ATTR{queue/scheduler}="mq-deadline"', r.stdout)
        self.assertIn("Sorts BEFORE 61-mdt-io-queue.rules", r.stdout)
        self.assertLess("60-mdt-io-scheduler.rules", "61-mdt-io-queue.rules")
        self.assertEqual(self.bash("_mdt_render_scheduler_udev vda", IO_ROOT_SCHEDULER="kernel").returncode, 1)
        self.assertEqual(self.bash("_mdt_render_scheduler_udev vda").returncode, 1)

    def test_model_and_qos_lines(self) -> None:
        r = self.bash('_mdt_iocost_model_line 254:0; _mdt_iocost_qos_line 254:0', IO_COST_MODEL=MODEL, IO_COST_QOS=QOS)
        self.assertEqual(r.stdout.splitlines(), [KERNEL_MODEL, KERNEL_QOS.replace("95.00", "95").replace("5.00", "5").replace("400.00", "400")])
        r = self.bash('_mdt_iocost_model_line 254:0; _mdt_iocost_qos_line 254:0', IO_COST_MODEL="kernel", IO_COST_QOS="off")
        self.assertEqual(r.stdout.splitlines(), ["254:0 ctrl=auto", "254:0 enable=0"])
        self.assertEqual(self.bash("_mdt_iocost_model_line 254:0").returncode, 1)     # unset = not managed
        self.assertEqual(self.bash("_mdt_iocost_qos_line 254:0").returncode, 1)

    def test_readback_comparison_is_field_normalised(self) -> None:
        match = "_mdt_iocost_match"
        want_qos = "254:0 enable=1 ctrl=user rpct=95 rlat=5000 wpct=95 wlat=20000 min=5 max=400"
        self.assertEqual(self.bash(f'{match} "{want_qos}" "{KERNEL_QOS}"').returncode, 0)    # 95 == 95.00, 5 == 5.00
        self.assertEqual(self.bash(f'{match} "{want_qos}" "{KERNEL_QOS.replace("rlat=5000", "rlat=4000")}"').returncode, 1)
        self.assertEqual(self.bash(f'{match} "{want_qos}" "{KERNEL_QOS.replace("max=400.00", "max=399.00")}"').returncode, 1)
        self.assertEqual(self.bash(f'{match} "{want_qos}" "254:0 enable=1 ctrl=user rpct=95.00"').returncode, 1)   # a field missing
        # after `enable=0` / `ctrl=auto` the kernel keeps other fields: only OUR keys are compared
        self.assertEqual(self.bash(f'{match} "254:0 enable=0" "{KERNEL_QOS.replace("enable=1", "enable=0")}"').returncode, 0)
        self.assertEqual(self.bash(f'{match} "254:0 enable=0" "{KERNEL_QOS}"').returncode, 1)
        self.assertEqual(self.bash(f'{match} "254:0 ctrl=auto" "254:0 ctrl=auto model=linear rbps=1"').returncode, 0)
        self.assertEqual(self.bash(f'{match} "254:0 ctrl=auto" "{KERNEL_MODEL}"').returncode, 1)


class ApplySchedulerTests(SchedulerFixture):
    def apply(self, **cfg: str) -> subprocess.CompletedProcess:
        return self.bash("_mdt_apply_scheduler_settings", **cfg)

    def test_apply_renders_applies_and_reads_back(self) -> None:
        r = self.apply(IO_ROOT_SCHEDULER="mq-deadline")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertIn('ATTR{queue/scheduler}="mq-deadline"', self.rule())
        self.assertIn("[mq-deadline]", self.sched.read_text())

    def test_a_readback_mismatch_fails_and_names_the_remedy(self) -> None:
        r = self.bash("_mdt_apply_scheduler_settings", IO_ROOT_SCHEDULER="mq-deadline", FAKE_UDEV_NOOP="1")
        self.assertEqual(r.returncode, 2, r.stdout + r.stderr)
        self.assertIn("live scheduler of vda is none but IO_ROOT_SCHEDULER=mq-deadline", r.stderr)
        self.assertIn("Remedy:", r.stderr)

    def test_an_unreadable_live_scheduler_is_never_certified(self) -> None:
        self.sched.unlink()
        r = self.bash("_mdt_apply_scheduler_settings", IO_ROOT_SCHEDULER="mq-deadline", FAKE_UDEV_NOOP="1")
        self.assertEqual(r.returncode, 2)
        self.assertIn("could not read the live scheduler", r.stderr)

    def test_the_old_mdt_bfq_rule_is_retired_and_the_bfq_module_file_follows_the_choice(self) -> None:
        old = self.ud / "60-mdt-bfq-scheduler.rules"
        old.write_text('ACTION=="add|change", KERNEL=="vd[a-z]", ATTR{queue/scheduler}="bfq"\n')
        mod = self.modules / "mdt-bfq.conf"
        mod.write_text("bfq\n")
        r = self.apply(IO_ROOT_SCHEDULER="mq-deadline")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertFalse(old.exists())
        self.assertFalse(mod.exists(), "the bfq module is loaded at boot only when IO_ROOT_SCHEDULER=bfq")
        self.assertIn("retired MDT's old BFQ rule", r.stdout)
        r = self.apply(IO_ROOT_SCHEDULER="bfq")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertTrue(mod.exists())
        self.assertIn("[bfq]", self.sched.read_text())

    def test_a_non_mdt_rule_identical_to_a_known_repo_copy_is_retired_with_a_notice(self) -> None:
        for source in (GSTAMM_BFQ_RULE, REPO_BFQ_RULE):
            foreign = self.ud / "60-bfq-scheduler.rules"
            foreign.write_bytes(source.read_bytes())
            r = self.apply(IO_ROOT_SCHEDULER="mq-deadline")
            self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
            self.assertFalse(foreign.exists(), source)
            self.assertIn("byte-identical to a known repo copy", r.stdout)
            self.assertIn("scripts/gstammtisch-guide/files/etc/udev/rules.d/60-bfq-scheduler.rules", r.stdout)

    def test_a_non_mdt_rule_that_is_not_a_known_copy_is_kept_and_refuses_verification_when_it_overrides(self) -> None:
        foreign = self.ud / "99-local-bfq.rules"
        foreign.write_text('ACTION=="add|change", KERNEL=="vd[a-z]", ATTR{queue/scheduler}="bfq"\n')
        r = self.apply(IO_ROOT_SCHEDULER="mq-deadline")
        self.assertEqual(r.returncode, 2, r.stdout + r.stderr)
        self.assertTrue(foreign.exists(), "a file MDT did not install and cannot cite is never deleted")
        self.assertIn(str(foreign), r.stderr)
        self.assertIn("NOT reported as verified", r.stderr)
        # a lookalike of the known name with different content is kept too
        foreign.unlink()
        lookalike = self.ud / "60-bfq-scheduler.rules"
        lookalike.write_text(REPO_BFQ_RULE.read_text() + "# edited\n")
        r = self.apply(IO_ROOT_SCHEDULER="mq-deadline")
        self.assertTrue(lookalike.exists())
        self.assertIn("NOT a byte-identical copy", r.stderr)
        self.assertEqual(r.returncode, 0, "it sorts BEFORE MDT's rule, which wins: a WARN, not a refusal")

    def test_a_kept_non_identical_rule_is_reported_also_when_the_scheduler_is_kernel(self) -> None:
        foreign = self.ud / "60-bfq-scheduler.rules"
        foreign.write_text(REPO_BFQ_RULE.read_text() + "# edited\n")
        r = self.apply(IO_ROOT_SCHEDULER="kernel")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertTrue(foreign.exists())
        self.assertIn("still assigns a scheduler to vda on every udev change event although IO_ROOT_SCHEDULER=kernel", r.stderr)

    def test_the_legacy_path_notices_when_the_live_scheduler_differs_from_what_the_rule_reapplies(self) -> None:
        install = (ROOT / "install.sh").read_text()
        legacy = install[install.index("legacy: IO_ROOT_SCHEDULER is not set"):install.index("else\n  echo \"== IO scheduler (MDT-HS-07)")]
        self.assertIn("the live scheduler of", legacy)
        self.assertIn("re-applies bfq on every udev change event", legacy)
        self.assertLess(legacy.index("the live scheduler of"), legacy.index("modules-load.d/bfq.conf"))

    def test_vm_budget_margin_is_at_least_15_minutes(self) -> None:
        import re
        lane = (ROOT / "tests" / "run-vm-acceptance.sh").read_text()
        outer = int(re.search(r'run_with_timeout (\d+)m "\$HARNESS" ssh "\$RUN" -- \\\n\s+"set -eu', lane).group(1))
        budget = int(re.search(r'\[lanes\.vm-system\].*?budget = "(\d+)m"', (ROOT.parent / "run-gate.toml").read_text(), re.S).group(1))
        self.assertGreaterEqual(budget - (outer + 15 + 2 + 10 + 12 + 5 + 2 + 5), 15)

    def test_kernel_removes_the_rule_with_a_notice_and_leaves_the_running_scheduler(self) -> None:
        self.assertEqual(self.apply(IO_ROOT_SCHEDULER="mq-deadline").returncode, 0)
        r = self.apply(IO_ROOT_SCHEDULER="kernel")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertFalse((self.ud / "60-mdt-io-scheduler.rules").exists())
        self.assertIn("running scheduler is unchanged until reboot", r.stdout)
        self.assertIn("[mq-deadline]", self.sched.read_text())

    def test_an_underivable_disk_refuses_before_writing(self) -> None:
        r = self.bash("_mdt_apply_scheduler_settings", IO_ROOT_SCHEDULER="mq-deadline", FAKE_FINDMNT_SOURCE="overlay")
        self.assertEqual(r.returncode, 2)
        self.assertEqual(list(self.ud.iterdir()), [])

    def test_install_keeps_the_legacy_bfq_rule_only_when_the_key_is_not_set(self) -> None:
        install = (ROOT / "install.sh").read_text()
        block = install[install.index("SCHEDULER_FAILED=0"):install.index("echo \"== writeback and root-queue settings")]
        self.assertIn('[ -z "$(_mdt_io_sched_value)" ]', block)
        self.assertIn("LEGACY BFQ rule is kept", block)
        self.assertIn("_mdt_apply_scheduler_settings", block)
        self.assertLess(block.index("LEGACY"), block.index("_mdt_apply_scheduler_settings"))
        self.assertLess(install.index("_mdt_apply_scheduler_settings"), install.index("_mdt_apply_writeback_settings"))
        self.assertLess(install.index("_mdt_apply_writeback_settings"), install.index("_mdt_apply_iocost"))


class ApplyIoCostTests(SchedulerFixture):
    def apply(self, **cfg: str) -> subprocess.CompletedProcess:
        cfg.setdefault("FAKE_FINDMNT_SOURCE", "/dev/null")
        return self.bash("_mdt_apply_iocost", **cfg)

    def test_model_then_qos_are_written_for_the_derived_whole_disk_and_read_back(self) -> None:
        r = self.apply(IO_COST_MODEL=MODEL, IO_COST_QOS=QOS)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertEqual((self.cgroot / "io.cost.model").read_text().strip(), KERNEL_MODEL)
        self.assertIn("enable=1", (self.cgroot / "io.cost.qos").read_text())
        self.assertIn("rpct=95", (self.cgroot / "io.cost.qos").read_text())
        self.assertIn("io.cost model verified on 254:0", r.stdout)

    def test_kernel_model_writes_ctrl_auto_with_a_notice_and_off_writes_enable_0(self) -> None:
        r = self.apply(IO_COST_MODEL="kernel", IO_COST_QOS="off")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertEqual((self.cgroot / "io.cost.model").read_text().strip(), "254:0 ctrl=auto")
        self.assertEqual((self.cgroot / "io.cost.qos").read_text().strip(), "254:0 enable=0")
        self.assertIn("rotational/ssd HEURISTIC", r.stdout)

    def test_unset_keys_write_nothing(self) -> None:
        r = self.apply()
        self.assertEqual(r.returncode, 0)
        self.assertEqual((self.cgroot / "io.cost.model").read_text(), "")
        r = self.apply(IO_COST_QOS="off")           # only QoS managed: the model file stays untouched
        self.assertEqual((self.cgroot / "io.cost.model").read_text(), "")

    def test_a_write_the_kernel_rejects_or_a_missing_readback_fails_loudly(self) -> None:
        (self.cgroot / "io.cost.qos").unlink()
        (self.cgroot / "io.cost.qos").mkdir()                      # writing to a directory fails
        r = self.apply(IO_COST_QOS=QOS)
        self.assertEqual(r.returncode, 2)
        self.assertIn("could not write", r.stderr)
        (self.cgroot / "io.cost.model").unlink()
        (self.cgroot / "io.cost.model").symlink_to("/dev/null")    # accepted write, nothing to read back
        r = self.apply(IO_COST_MODEL=MODEL)
        self.assertEqual(r.returncode, 2)
        self.assertIn("has no line for 254:0", r.stderr)

    def test_values_come_from_the_environment_never_literals(self) -> None:
        script = (ROOT / "scripts" / "mdt-io-cost-apply.sh").read_text()
        self.assertNotRegex(script, r"(?<![A-Za-z])\d{4,}")
        self.assertIn("_mdt_apply_iocost", script)
        unit = (ROOT / "units" / "mdt-io-cost.service").read_text()
        self.assertIn("udevadm settle", unit)
        self.assertIn("After=local-fs.target systemd-udev-trigger.service", unit)
        self.assertIn("not a udev RUN rule", unit.replace("A oneshot unit and not a udev RUN rule", "not a udev RUN rule"))
        install = (ROOT / "install.sh").read_text()
        self.assertIn("systemctl enable mdt-io-cost.service", install)


class CheckShTests(SchedulerFixture):
    def run_check(self, **cfg: str) -> str:
        text = (ROOT / "scripts" / "check.sh").read_text()
        block = text[text.index("# BEGIN io-sched-check"):text.index("# END io-sched-check")]
        harness = self.host.base / "h.sh"
        harness.write_text(
            'ok(){ echo "OK $*"; }; warn(){ echo "WARN $*"; }; fail(){ echo "FAIL $*"; }; info(){ echo "INFO $*"; }\n'
            f'log(){{ :; }}; . "{LIB}"\n' + block + "\n")
        cfg.setdefault("FAKE_FINDMNT_SOURCE", "/dev/null")
        return subprocess.run(["bash", str(harness)], env=self.env(**cfg), text=True, capture_output=True).stdout

    def systemctl_enabled(self, enabled: bool) -> None:
        _write_exec(self.host.bin / "systemctl", "#!/bin/sh\n" + ("exit 0\n" if enabled else "exit 1\n"))

    def set_qos(self, line: str) -> None:
        (self.cgroot / "io.cost.qos").write_text(line + "\n")

    # ---- the weight mechanism, derived from live files ----
    def test_weight_mechanism_io_cost(self) -> None:
        self.sched.write_text("none [mq-deadline] kyber bfq\n")
        self.set_qos(KERNEL_QOS)
        out = self.run_check()
        self.assertIn("OK vda weight mechanism: io.cost", out)
        self.assertNotIn("NONE", out)

    def test_weight_mechanism_bfq(self) -> None:
        self.sched.write_text("none mq-deadline kyber [bfq]\n")
        self.set_qos(KERNEL_QOS.replace("enable=1", "enable=0"))
        out = self.run_check()
        self.assertIn("OK vda weight mechanism: bfq", out)

    def test_weight_mechanism_none_is_a_warning(self) -> None:
        self.sched.write_text("[none] mq-deadline kyber bfq\n")
        self.set_qos(KERNEL_QOS.replace("enable=1", "enable=0"))
        out = self.run_check()
        self.assertIn("WARN vda weight mechanism: NONE", out)
        self.assertIn("IOWeight is inert", out)
        self.assertNotIn("OK vda weight mechanism", out)

    def test_weight_mechanism_both_and_unreadable_states(self) -> None:
        self.sched.write_text("none mq-deadline kyber [bfq]\n")
        self.set_qos(KERNEL_QOS)
        self.assertIn("WARN vda weight mechanism: io.cost is enabled AND the scheduler is bfq", self.run_check())
        self.sched.unlink()
        out = self.run_check()
        self.assertIn("WARN vda: could not read the live scheduler", out)
        self.assertNotIn("OK vda weight mechanism", out)
        self.sched.write_text("[none] mq-deadline\n")
        (self.cgroot / "io.cost.qos").unlink()
        out = self.run_check()
        self.assertIn("could not read", out)
        self.assertNotIn("OK vda weight mechanism", out)

    def test_the_old_bfq_only_message_is_gone(self) -> None:
        text = (ROOT / "scripts" / "check.sh").read_text()
        self.assertNotIn("no disk uses BFQ", text)
        self.assertNotIn("weights need BFQ", text)

    # ---- the scheduler key ----
    def test_scheduler_states(self) -> None:
        self.sched.write_text("none [mq-deadline] kyber bfq\n")
        self.set_qos(KERNEL_QOS.replace("enable=1", "enable=0"))
        out = self.run_check()                                           # key not set
        self.assertIn("INFO vda scheduler: not managed (IO_ROOT_SCHEDULER not set", out)
        self.assertIn("INFO vda scheduler: not managed (kernel", self.run_check(IO_ROOT_SCHEDULER="kernel"))
        (self.ud / "60-mdt-io-scheduler.rules").write_text('ACTION=="add|change", KERNEL=="vda", ATTR{queue/scheduler}="mq-deadline"\n')
        self.assertIn("OK vda scheduler = mq-deadline (matches host-setup.env, persisted", self.run_check(IO_ROOT_SCHEDULER="mq-deadline"))
        self.assertIn("FAIL vda scheduler MISMATCH: live mq-deadline, host-setup.env says none", self.run_check(IO_ROOT_SCHEDULER="none"))
        (self.ud / "60-mdt-io-scheduler.rules").unlink()
        out = self.run_check(IO_ROOT_SCHEDULER="mq-deadline")
        self.assertIn("WARN vda scheduler = mq-deadline is LIVE ONLY, NOT PERSISTED", out)
        self.assertNotIn("OK vda scheduler", out)

    def test_kernel_reports_a_kept_non_mdt_rule(self) -> None:
        self.sched.write_text("none [mq-deadline] kyber bfq\n")
        (self.ud / "60-bfq-scheduler.rules").write_text(REPO_BFQ_RULE.read_text())
        out = self.run_check(IO_ROOT_SCHEDULER="kernel")
        self.assertIn("60-bfq-scheduler.rules (not installed by MDT) still assigns a scheduler to vda", out)

    def test_a_later_or_non_mdt_scheduler_rule_is_noticed(self) -> None:
        self.sched.write_text("none [mq-deadline] kyber bfq\n")
        (self.ud / "60-mdt-io-scheduler.rules").write_text('ACTION=="add|change", KERNEL=="vda", ATTR{queue/scheduler}="mq-deadline"\n')
        (self.ud / "99-late.rules").write_text('ACTION=="add|change", KERNEL=="vd[a-z]", ATTR{queue/scheduler}="bfq"\n')
        (self.ud / "60-bfq-scheduler.rules").write_text(REPO_BFQ_RULE.read_text())
        (self.ud / "99-other-disk.rules").write_text('ACTION=="add|change", KERNEL=="sd[a-z]", ATTR{queue/scheduler}="bfq"\n')
        out = self.run_check(IO_ROOT_SCHEDULER="mq-deadline")
        self.assertIn("99-late.rules (not installed by MDT) also sets the scheduler for vda and sorts after", out)
        self.assertIn("60-bfq-scheduler.rules (not installed by MDT) also sets the scheduler for vda; it sorts before", out)
        self.assertNotIn("99-other-disk.rules", out)

    # ---- model and QoS ----
    def test_model_and_qos_states(self) -> None:
        self.systemctl_enabled(True)
        self.sched.write_text("none [mq-deadline] kyber bfq\n")
        (self.cgroot / "io.cost.model").write_text(KERNEL_MODEL + "\n")
        self.set_qos(KERNEL_QOS)
        out = self.run_check(IO_COST_MODEL=MODEL, IO_COST_QOS=QOS)
        self.assertIn("OK io.cost model verified", out)
        self.assertIn("OK io.cost qos verified", out)               # 95 vs 95.00 is normalised
        out = self.run_check(IO_COST_MODEL=MODEL.replace("4900", "4901", 1), IO_COST_QOS="95 4000 95 20000 5 400")
        self.assertIn("FAIL io.cost model MISMATCH", out)
        self.assertIn("FAIL io.cost qos MISMATCH", out)
        out = self.run_check()
        self.assertIn("INFO io.cost model: not managed", out)
        self.assertIn("INFO io.cost qos: not managed", out)
        (self.cgroot / "io.cost.qos").unlink()
        out = self.run_check(IO_COST_MODEL=MODEL, IO_COST_QOS=QOS)
        self.assertIn("WARN io.cost qos: could not read", out)
        self.assertNotIn("OK io.cost qos", out)
        self.set_qos("8:0 enable=1 ctrl=user")                       # a line for ANOTHER disk only
        self.assertIn("FAIL io.cost qos MISMATCH: ", self.run_check(IO_COST_QOS=QOS))

    def test_live_but_not_persisted_is_its_own_state(self) -> None:
        self.systemctl_enabled(False)
        (self.cgroot / "io.cost.model").write_text(KERNEL_MODEL + "\n")
        self.set_qos(KERNEL_QOS)
        out = self.run_check(IO_COST_MODEL=MODEL, IO_COST_QOS=QOS)
        self.assertIn("WARN io.cost model = ", out)
        self.assertIn("LIVE ONLY, NOT PERSISTED", out)
        self.assertNotIn("OK io.cost model", out)

    def test_an_underivable_disk_is_reported(self) -> None:
        out = self.run_check(FAKE_FINDMNT_SOURCE="overlay")
        self.assertIn("could not derive the whole disk under /", out)


class WizardTests(unittest.TestCase):
    DEVNO = "254:0"

    def result(self, model=True) -> dict:
        iocost = {"rbps": 290000000, "rseqiops": 4900, "rrandiops": 4900, "wbps": 14680064,
                  "wseqiops": 2800, "wrandiops": 2800, "rule": "R6"}
        if not model:
            iocost = {k: 0 for k in iocost if k != "rule"} | {"rule": "R6"}
        return {
            "schema": 1, "run": {"status": "complete"}, "quality": {"plausible": True, "limits": []},
            "identity": {"disk": {"devno": self.DEVNO, "model": "QEMU HARDDISK"}},
            "proposals": {"writeback": {}, "iocost_model": iocost, "target_missed": None},
        }

    def write(self, doc) -> Path:
        d = Path(tempfile.mkdtemp())
        self.addCleanup(lambda: __import__("shutil").rmtree(d, ignore_errors=True))
        p = d / "result.json"
        p.write_text(json.dumps(doc))
        return p

    def runner(self, schedulers="none\nmq-deadline\n"):
        def run(cmd, **kwargs):
            expr = cmd[2]
            if "_mdt_io_sched_offered" in expr or "_mdt_io_sched_list" in expr:
                out, rc = schedulers, 0
            elif "_mdt_root_disk_node" in expr and "_mdt_io_sched_list" not in expr:
                out, rc = "/dev/vda", 0
            elif "_mdt_root_disk_majmin" in expr or "_mdt_dev_majmin" in expr:
                out, rc = self.DEVNO, 0
            elif "_mdt_io_disk_identity" in expr:
                out, rc = "model=QEMU HARDDISK", 0
            elif "_mdt_io_queue_attr" in expr:
                out, rc = "1280", 0
            else:
                out, rc = "", 1
            return subprocess.CompletedProcess(cmd, rc, out, "")
        return run

    def step(self, cfg, result_path, schedulers="none\nmq-deadline\n", answers=None):
        asked = {}

        def fake_ask(label, default, validate=None, *, default_source=None, **_):
            key = label.split()[0]
            asked[key] = (default, default_source, validate)
            return (answers or {}).get(key, default)

        printed: list[str] = []
        with mock.patch.object(WIZARD, "ask", side_effect=fake_ask), \
             mock.patch.object(WIZARD, "out", side_effect=lambda text="", **k: printed.append(text)):
            chosen = WIZARD.step_io_scheduler_and_cost(cfg, {}, iobench_result=result_path, runner=self.runner(schedulers))
        return chosen, asked, "\n".join(printed)

    def test_model_precedence_benchmark_then_configured_then_kernel(self) -> None:
        chosen, asked, printed = self.step({"IO_COST_MODEL": "1 2 3 4 5 6"}, self.write(self.result()))
        self.assertEqual(chosen["IO_COST_MODEL"], MODEL)
        self.assertIn("benchmark rule R6", asked["IO_COST_MODEL"][1])
        chosen, asked, _ = self.step({"IO_COST_MODEL": "1 2 3 4 5 6"}, Path("/nonexistent/r.json"))
        self.assertEqual((chosen["IO_COST_MODEL"], asked["IO_COST_MODEL"][1]), ("1 2 3 4 5 6", "existing"))
        chosen, asked, _ = self.step({}, Path("/nonexistent/r.json"))
        self.assertEqual(chosen["IO_COST_MODEL"], "kernel")
        # a placeholder all-zero model in the result is "not measured", never a model
        chosen, _, _ = self.step({}, self.write(self.result(model=False)))
        self.assertEqual(chosen["IO_COST_MODEL"], "kernel")

    def test_policy_defaults_are_explicit_and_the_example_is_never_defaulted(self) -> None:
        chosen, asked, printed = self.step({}, Path("/nonexistent/r.json"))
        self.assertEqual(chosen, {"IO_ROOT_SCHEDULER": "kernel", "IO_COST_MODEL": "kernel", "IO_COST_QOS": "off"})
        for key in chosen:
            self.assertEqual(asked[key][1], "policy default")
        self.assertIn("NOT a default", printed)
        self.assertIn(MODEL, printed)
        self.assertIn("IO-STALL-TESTING-GUIDE.md section 11", printed)
        self.assertIn("io.latency is not available", printed)

    def test_the_scheduler_prompt_offers_only_what_the_disk_lists(self) -> None:
        _, asked, printed = self.step({}, Path("/nonexistent/r.json"), schedulers="none\nmq-deadline\n")
        self.assertIn("none mq-deadline", printed)
        validate = asked["IO_ROOT_SCHEDULER"][2]
        self.assertIsNone(validate("mq-deadline"))
        self.assertIsNotNone(validate("wrong"))
        _, asked, printed = self.step({}, Path("/nonexistent/r.json"), schedulers="none\nbfq\n")   # no mq-deadline here
        self.assertIn("not offered by this disk", asked["IO_ROOT_SCHEDULER"][2]("mq-deadline"))
        self.assertIsNone(asked["IO_ROOT_SCHEDULER"][2]("none"))
        # existing value wins over the policy default
        chosen, asked, _ = self.step({"IO_ROOT_SCHEDULER": "none", "IO_COST_QOS": QOS}, Path("/nonexistent/r.json"))
        self.assertEqual((chosen["IO_ROOT_SCHEDULER"], chosen["IO_COST_QOS"]), ("none", QOS))

    def test_main_asks_the_step_after_the_writeback_step(self) -> None:
        source = (ROOT / "mdt-host-setup-wizard.py").read_text()
        main = source[source.index("def main("):]
        self.assertLess(main.index("step_io_writeback("), main.index("step_io_scheduler_and_cost("))


class DocsAndWeightsTests(unittest.TestCase):
    def test_weight_comments_are_mechanism_conditional_not_bfq_only(self) -> None:
        env = (ROOT / "host-setup.env.example").read_text()
        self.assertNotIn("KEEP IO WEIGHTS <= 100 ON A BFQ HOST. systemd", env)
        self.assertIn("io.cost enabled", env)
        self.assertIn("BFQ scheduler:", env)
        for name in ("dev-interactive", "dev-background", "dev-gates", "dev-buildkitd"):
            text = (ROOT / "units" / f"{name}.slice.in").read_text()
            self.assertIn("io.cost", text, name)
            self.assertIn("BFQ", text, name)          # still true under bfq

    def test_docs_triad(self) -> None:
        readme = " ".join((ROOT / "README.md").read_text().split())
        design = " ".join((ROOT.parent / "docs" / "DESIGN-GUIDE.md").read_text().split())
        changes = (ROOT.parent / "CHANGES.md").read_text()
        for key in ("IO_ROOT_SCHEDULER", "IO_COST_MODEL", "IO_COST_QOS"):
            self.assertIn(key, readme)
        for needle in ("MDT-HS-07", "IO-STALL-TESTING-GUIDE.md", "C0", "C1", "C2", "wbt", "io.latency", "IOB-02",
                       "oneshot", "udev RUN", "60-mdt-io-scheduler.rules", "Findings"):
            self.assertIn(needle, design, needle)
        self.assertIn("MDT-HS-07", changes)

    def test_vm_lane_runs_the_iocost_acceptance(self) -> None:
        lane = (ROOT / "tests" / "run-vm-acceptance.sh").read_text()
        self.assertLess(lane.index("vm-writeback-acceptance.sh"), lane.index("vm-iocost-acceptance.sh"))
        self.assertLess(lane.index("vm-iocost-acceptance.sh"), lane.index("vm-upgrade-acceptance.sh"))
        script = (ROOT / "tests" / "vm-iocost-acceptance.sh").read_text()
        for needle in ("io.cost.model", "io.cost.qos", "queue/scheduler", "mdt-io-cost.service", "mdt-host-check.sh",
                       "drift", "SCHED=none", "never a refusal-only pass"):
            self.assertIn(needle, script)
        self.assertNotIn("asserting the REFUSAL path instead", script)
        self.assertTrue(os.access(ROOT / "tests" / "vm-iocost-acceptance.sh", os.X_OK))


if __name__ == "__main__":
    unittest.main()
