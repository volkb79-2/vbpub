"""MDT-033.e: mdt-psi-guard on a fake cgroupfs with injected pressure."""
from __future__ import annotations

import contextlib
import io
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from test_io_governance import ROOT, _load  # noqa: E402

GUARD_PATH = ROOT / "scripts" / "mdt-psi-guard.py"
GUARD = _load(GUARD_PATH, "mdt_psi_guard_under_test")
MIB = 1024 * 1024


class FakeHost:
    """cgroupfs with wings.slice (the protected unit) and the dev tiers; pressure is injected."""

    def __init__(self, case: unittest.TestCase) -> None:
        tmp = tempfile.TemporaryDirectory()
        case.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name)
        self.cg = self.root / "cg"
        self.proc = self.root / "proc-pressure"
        self.state = self.root / "run" / "psi-guard"
        self.proc.mkdir()
        (self.cg / "wings.slice").mkdir(parents=True)
        for tier in ("dev-interactive.slice", "dev-gates.slice", "dev-background.slice", "dev-buildkitd.slice"):
            d = self.cg / "dev.slice" / tier
            d.mkdir(parents=True)
            (d / "cpu.weight").write_text("100\n")
            (d / "io.weight").write_text("default 100\n")
            (d / "memory.high").write_text("max\n")
            (d / "memory.current").write_text(f"{4000 * MIB}\n")
        self.totals = {"unit_mem": 0, "host_full": 0, "unit_cpu": 0}
        self.write_pressure()

    def write_pressure(self) -> None:
        (self.cg / "wings.slice" / "memory.pressure").write_text(
            f"some avg10=0.00 avg60=0.00 avg300=0.00 total={self.totals['unit_mem']}\n"
            "full avg10=0.00 avg60=0.00 avg300=0.00 total=0\n")
        (self.cg / "wings.slice" / "cpu.pressure").write_text(
            f"some avg10=0.00 avg60=0.00 avg300=0.00 total={self.totals['unit_cpu']}\n")
        (self.proc / "memory").write_text(
            "some avg10=0.00 avg60=0.00 avg300=0.00 total=0\n"
            f"full avg10=0.00 avg60=0.00 avg300=0.00 total={self.totals['host_full']}\n")

    def stall(self, share_mem: float = 0.0, share_cpu: float = 0.0, share_full: float = 0.0, seconds: float = 1.0) -> None:
        """Advance the cumulative counters as if the stated share of wall time was stalled."""
        self.totals["unit_mem"] += int(share_mem * seconds * 1e6)
        self.totals["unit_cpu"] += int(share_cpu * seconds * 1e6)
        self.totals["host_full"] += int(share_full * seconds * 1e6)
        self.write_pressure()

    def guard(self, mode: str, **kw):
        return GUARD.Guard(cg_root=self.cg, units=["wings.slice"], state_dir=self.state, mode=mode,
                           proc_pressure=self.proc, **kw)

    def value(self, tier: str, name: str) -> str:
        return (self.cg / "dev.slice" / tier / name).read_text().strip()

    def events(self) -> list[dict]:
        path = self.state / "events.jsonl"
        return [json.loads(line) for line in path.read_text().splitlines()] if path.exists() else []


def drive(host: FakeHost, guard, seconds: int, start: int = 0, **stall) -> int:
    """One tick per second with a constant injected pressure; returns the next second."""
    t = start
    for _ in range(seconds):
        t += 1
        host.stall(**stall)
        guard.tick(float(t))
    return t


class StateMachineTests(unittest.TestCase):
    def test_below_the_hold_time_nothing_fires(self) -> None:
        host = FakeHost(self)
        guard = host.guard("enforce")
        guard.tick(0.0)
        drive(host, guard, 9, share_mem=0.5)
        self.assertEqual(host.events(), [])
        self.assertEqual(host.value("dev-gates.slice", "cpu.weight"), "100")

    def test_enforce_stage_one_sets_the_weights_of_the_three_tiers_to_one(self) -> None:
        host = FakeHost(self)
        guard = host.guard("enforce")
        guard.tick(0.0)
        drive(host, guard, 12, share_mem=0.5)
        for tier in ("dev-gates.slice", "dev-background.slice", "dev-buildkitd.slice"):
            self.assertEqual(host.value(tier, "cpu.weight"), "1")
            self.assertEqual(host.value(tier, "io.weight"), "default 1")
        self.assertEqual(host.value("dev-interactive.slice", "cpu.weight"), "100")
        self.assertEqual(host.value("dev-gates.slice", "memory.high"), "max")
        self.assertTrue(any(e["event"] == "pressure" and e["signal"] == "unit_memory_some" for e in host.events()))

    def test_stage_two_lowers_memory_high_after_thirty_seconds_still_above(self) -> None:
        host = FakeHost(self)
        guard = host.guard("enforce")
        guard.tick(0.0)
        t = drive(host, guard, 12, share_mem=0.5)  # fires at ~11 s, stage 1
        self.assertEqual(host.value("dev-gates.slice", "memory.high"), "max")
        drive(host, guard, 31, start=t, share_mem=0.5)
        want = str(int(4000 * MIB * 0.9))
        self.assertEqual(host.value("dev-gates.slice", "memory.high"), want)
        self.assertEqual(host.value("dev-background.slice", "memory.high"), want)
        self.assertEqual(host.value("dev-buildkitd.slice", "memory.high"), "max")  # only gates + background
        self.assertEqual(host.value("dev-interactive.slice", "memory.high"), "max")

    def test_memory_high_never_goes_below_the_floor(self) -> None:
        host = FakeHost(self)
        for tier in ("dev-gates.slice", "dev-background.slice"):
            (host.cg / "dev.slice" / tier / "memory.current").write_text(f"{100 * MIB}\n")
        guard = host.guard("enforce")
        guard.tick(0.0)
        drive(host, guard, 45, share_mem=0.5)
        self.assertEqual(host.value("dev-gates.slice", "memory.high"), str(256 * MIB))

    def test_release_needs_sixty_seconds_below_half_the_threshold_and_reverts_everything(self) -> None:
        host = FakeHost(self)
        guard = host.guard("enforce")
        guard.tick(0.0)
        t = drive(host, guard, 45, share_mem=0.5)
        self.assertEqual(guard.owned_keys()[0].split("/")[0] in ("dev-background.slice", "dev-buildkitd.slice", "dev-gates.slice"), True)
        # between half and full threshold (7%): still held
        t = drive(host, guard, 70, start=t, share_mem=0.07)
        self.assertEqual(host.value("dev-gates.slice", "cpu.weight"), "1")
        # below half (3%) for 59 s: still held; the 60th second releases
        t = drive(host, guard, 59, start=t, share_mem=0.03)
        self.assertEqual(host.value("dev-gates.slice", "cpu.weight"), "1")
        t = drive(host, guard, 2, start=t, share_mem=0.03)
        for tier in ("dev-gates.slice", "dev-background.slice", "dev-buildkitd.slice"):
            self.assertEqual(host.value(tier, "cpu.weight"), "100")
            self.assertEqual(host.value(tier, "io.weight"), "default 100")
        self.assertEqual(host.value("dev-gates.slice", "memory.high"), "max")
        self.assertEqual(guard.owned_keys(), [])
        names = [e["event"] for e in host.events()]
        self.assertIn("released", names)
        self.assertIn("reverted", names)

    def test_each_signal_fires_on_its_own_threshold(self) -> None:
        for kwargs, signal_name in (
            ({"share_full": 0.06}, "host_memory_full"),
            ({"share_cpu": 0.30}, "unit_cpu_some"),
            ({"share_mem": 0.12}, "unit_memory_some"),
        ):
            with self.subTest(signal=signal_name):
                host = FakeHost(self)
                guard = host.guard("report")
                guard.tick(0.0)
                drive(host, guard, 12, **kwargs)
                self.assertIn(signal_name, [e.get("signal") for e in host.events()])
        for kwargs in ({"share_full": 0.04}, {"share_cpu": 0.20}, {"share_mem": 0.08}):
            with self.subTest(below=kwargs):
                host = FakeHost(self)
                guard = host.guard("report")
                guard.tick(0.0)
                drive(host, guard, 30, **kwargs)
                self.assertEqual(host.events(), [])

    def test_report_mode_writes_events_and_changes_nothing(self) -> None:
        host = FakeHost(self)
        before = {p: p.read_text() for p in (host.cg / "dev.slice").rglob("*") if p.is_file()}
        guard = host.guard("report")
        guard.tick(0.0)
        drive(host, guard, 50, share_mem=0.5)
        after = {p: p.read_text() for p in (host.cg / "dev.slice").rglob("*") if p.is_file()}
        self.assertEqual(before, after)
        names = {e["event"] for e in host.events()}
        self.assertIn("pressure", names)
        self.assertIn("would_apply", names)
        self.assertNotIn("applied", names)
        self.assertEqual(guard.owned_keys(), [])

    def test_off_does_nothing(self) -> None:
        host = FakeHost(self)
        guard = host.guard("off")
        guard.tick(0.0)
        drive(host, guard, 30, share_mem=0.9)
        self.assertEqual(host.events(), [])

    def test_the_state_file_lists_the_owned_keys(self) -> None:
        host = FakeHost(self)
        guard = host.guard("enforce")
        guard.tick(0.0)
        drive(host, guard, 12, share_mem=0.5)
        state = json.loads((host.state / "state.json").read_text())
        self.assertEqual(sorted(state["owned"]), [
            "dev-background.slice/cpu.weight", "dev-background.slice/io.weight",
            "dev-buildkitd.slice/cpu.weight", "dev-buildkitd.slice/io.weight",
            "dev-gates.slice/cpu.weight", "dev-gates.slice/io.weight",
        ])

    def test_the_guard_never_freezes_or_kills(self) -> None:
        source = GUARD_PATH.read_text()
        for forbidden in ("cgroup.freeze", "cgroup.kill", "os.kill(", "SIGKILL", "subprocess"):
            self.assertNotIn(forbidden, source)


class RestoreTests(unittest.TestCase):
    def test_stop_restores_every_value(self) -> None:
        host = FakeHost(self)
        guard = host.guard("enforce")
        ticks = iter(range(1, 60))
        clock_state = {"t": 0.0}

        def clock() -> float:
            return clock_state["t"]

        def sleep(_: float) -> None:
            clock_state["t"] += 1
            host.stall(share_mem=0.5)

        GUARD.run(guard, max_ticks=45, sleep=sleep, clock=clock)
        # max_ticks reached -> run() reverts in its finally block (the stop path)
        self.assertEqual(host.value("dev-gates.slice", "cpu.weight"), "100")
        self.assertEqual(host.value("dev-gates.slice", "memory.high"), "max")
        self.assertTrue(any(e.get("reason") == "stop" for e in host.events() if e["event"] == "reverted"))
        del ticks

    def test_the_next_start_recovers_what_a_crashed_run_left_owned(self) -> None:
        host = FakeHost(self)
        first = host.guard("enforce")
        first.tick(0.0)
        drive(host, first, 12, share_mem=0.5)
        self.assertEqual(host.value("dev-gates.slice", "cpu.weight"), "1")
        # the process dies without reverting; a new instance starts on the same state directory
        second = host.guard("enforce")
        second.recover(100.0)
        for tier in ("dev-gates.slice", "dev-background.slice", "dev-buildkitd.slice"):
            self.assertEqual(host.value(tier, "cpu.weight"), "100")
            self.assertEqual(host.value(tier, "io.weight"), "default 100")
        self.assertTrue(any(e.get("reason") == "boot" for e in host.events() if e["event"] == "reverted"))

    def test_the_io_weight_restores_only_the_default_line(self) -> None:
        host = FakeHost(self)
        (host.cg / "dev.slice" / "dev-gates.slice" / "io.weight").write_text("default 100\n8:0 50\n")
        guard = host.guard("enforce")
        guard.tick(0.0)
        drive(host, guard, 12, share_mem=0.5)
        guard.revert(99.0, reason="stop")
        self.assertEqual((host.cg / "dev.slice" / "dev-gates.slice" / "io.weight").read_text().strip(), "default 100")


class ServiceWiringTests(unittest.TestCase):
    def test_the_mode_vocabulary_is_closed(self) -> None:
        for good in ("off", "report", "enforce"):
            self.assertIsNone(GUARD.validate_mode(good))
        self.assertIsNotNone(GUARD.validate_mode("enable"))
        wizard = _load(ROOT / "mdt-host-setup-wizard.py", "mdt_wizard_psi_guard")
        self.assertIsNone(wizard.validate_psi_guard_mode("report"))
        self.assertIsNotNone(wizard.validate_psi_guard_mode(""))
        self.assertEqual(wizard.parse_env_file((ROOT / "host-setup.env.example").read_text())["MDT_PSI_GUARD"], "report")

    def test_the_cli_in_off_mode_exits_cleanly_and_report_mode_runs_one_tick(self) -> None:
        host = FakeHost(self)
        cfg = host.root / "env"
        cfg.write_text("MDT_PROTECTED_UNITS=wings.slice\nMDT_PSI_GUARD=off\n")
        base = ["--config", str(cfg), "--cgroup-root", str(host.cg), "--proc-pressure", str(host.proc),
                "--state-dir", str(host.state)]
        with contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(GUARD.main(base), 0)
        cfg.write_text("MDT_PROTECTED_UNITS=wings.slice\nMDT_PSI_GUARD=report\n")
        with contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(GUARD.main([*base, "--max-ticks", "1"]), 0)
        cfg.write_text("MDT_PSI_GUARD=bogus\n")
        with contextlib.redirect_stderr(io.StringIO()):
            self.assertEqual(GUARD.main(base), 2)

    def test_the_unit_runs_outside_dev_slice_and_install_wires_it(self) -> None:
        unit = (ROOT / "units" / "mdt-psi-guard.service").read_text()
        self.assertNotIn("dev.slice", unit.replace("outside dev.slice", ""))
        self.assertNotIn("Slice=", unit)
        install = (ROOT / "install.sh").read_text()
        self.assertIn('units/mdt-psi-guard.service', install)
        self.assertIn('scripts/mdt-psi-guard.py', install)
        self.assertIn('MDT_PSI_GUARD:-report', install)

    def test_the_reconcile_sweep_skips_keys_the_guard_owns(self) -> None:
        host = FakeHost(self)
        guard = host.guard("enforce")
        guard.tick(0.0)
        drive(host, guard, 12, share_mem=0.5)
        script = (ROOT / "scripts" / "mdt-dev-governance-reconcile.sh").read_text()
        start = script.index("_psi_guard_state=")
        end = script.index("if [ -r \"$_psi_guard_state\" ]")
        snippet = script[start:end]
        probe = (
            f'MDT_PSI_GUARD_STATE="{host.state / "state.json"}"; {snippet}\n'
            '_mdt_guard_owns dev-gates.slice/cpu.weight && echo owned-gates || echo free-gates\n'
            '_mdt_guard_owns dev-gates.slice/memory.high && echo owned-high || echo free-high\n'
            '_mdt_guard_owns dev-interactive.slice/cpu.weight && echo owned-int || echo free-int\n'
        )
        out = subprocess.run(["bash", "-c", probe], capture_output=True, text=True, check=False)
        self.assertEqual(out.stdout.split(), ["owned-gates", "free-high", "free-int"], out.stderr)


if __name__ == "__main__":
    unittest.main()
