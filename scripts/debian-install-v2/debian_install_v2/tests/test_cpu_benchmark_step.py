"""HB-2: installer step ``run_cpu_benchmark`` (memo 4.6). Hermetic: the shared tool is replaced by a fake behind the
real HostActions allowlist; nothing downloads, executes Geekbench or touches /var/lib/mdt."""
from __future__ import annotations

from dataclasses import replace
import hashlib
import json
from pathlib import Path

import pytest

from debian_install_v2 import installer as installer_module
from debian_install_v2.actions import ActionError, ActionTimeout, HostActions, PlannedAction
from debian_install_v2.config import Config, ConfigError, load_config, validate_config
from debian_install_v2.installer import CPU_BENCHMARK_PINS, Installer
from debian_install_v2.state import StateStore
from debian_install_v2.tests.test_io_benchmark import make as make_stage2

TOOLS = Path(__file__).resolve().parents[2] / "tools"
GOOD = (
    "cpu-benchmark: single_core=2900 multi_core=11000 cpu=AMD EPYC vcpus=4 geekbench=7.1.0 "
    "result=https://browser.geekbench.com/v7/cpu/123456\n"
)
CLAIM = "https://browser.geekbench.com/v7/cpu/123456/claim?key=SECRETKEY"


class FakeTool(HostActions):
    def __init__(self, output: str = GOOD, error: Exception | None = None) -> None:
        super().__init__(dry_run=False)
        self.output, self.error = output, error
        self.calls: list[tuple[list[str], float | None]] = []

    def run(self, argv, description="", dangerous=False, input=None, timeout=None):
        self._validate(list(argv))
        self.planned.append(PlannedAction(tuple(argv), description or " ".join(argv), dangerous))
        if Path(argv[0]).name != "cpu_benchmark.py":
            return ""
        self.calls.append((list(argv), timeout))
        if self.error:
            raise self.error
        return self.output


def build(tmp_path: Path, actions: HostActions | None = None, **overrides) -> tuple[Installer, FakeTool]:
    actions = actions or FakeTool()
    config = Config(state_dir=str(tmp_path / "state"), log_dir=str(tmp_path / "logs"),
                    telegram_bot_token="", telegram_chat_id="", **overrides)
    StateStore(config.state_dir).save_new(StateStore.new(config))
    installer = Installer(config, actions, inspect_host=False)
    installer._cpu_benchmark_tool_path = lambda: TOOLS / "cpu_benchmark.py"  # the real, really pinned file
    return installer, actions


def step(installer: Installer) -> dict:
    return StateStore(installer.config.state_dir).load()["steps"].get("cpu_benchmark", {})


@pytest.fixture(autouse=True)
def x86(monkeypatch):
    monkeypatch.setattr(installer_module.platform, "machine", lambda: "x86_64")


def test_default_on_and_run_geekbench_pointer():
    assert Config().run_cpu_benchmark is True
    with pytest.raises(ConfigError, match="run_geekbench was replaced by run_cpu_benchmark"):
        load_config(raw_json=json.dumps({"schema_version": 1, "run_geekbench": True}))


def test_pin_keys_must_come_together_and_be_well_formed():
    with pytest.raises(ConfigError, match="set together"):
        validate_config(replace(Config(), cpu_benchmark_geekbench_version="7.1.0"))
    with pytest.raises(ConfigError, match="set together"):
        validate_config(replace(Config(), cpu_benchmark_sha256="a" * 64))
    with pytest.raises(ConfigError, match="look like"):
        validate_config(replace(Config(), cpu_benchmark_geekbench_version="seven", cpu_benchmark_sha256="a" * 64))
    with pytest.raises(ConfigError, match="64 lowercase hex"):
        validate_config(replace(Config(), cpu_benchmark_geekbench_version="7.1.0", cpu_benchmark_sha256="zz"))
    validate_config(replace(Config(), cpu_benchmark_geekbench_version="7.1.0", cpu_benchmark_sha256="a" * 64))


def test_success_passes_both_pins_and_records_scores(tmp_path):
    installer, actions = build(tmp_path)
    installer._run_cpu_benchmark()
    (argv, timeout), = actions.calls
    version, sha = CPU_BENCHMARK_PINS["x86_64"]
    assert argv[1:5] == ["--geekbench-version", version, "--sha256", sha]
    assert "--force" not in argv and "--state-dir" not in argv  # never re-measure, never redirect
    assert timeout and timeout > 1800  # outer bound exceeds the tool's own hard timeout
    record = step(installer)
    assert record["status"] == "success"
    assert "single_core=2900" in record["detail"] and "browser.geekbench.com/v7/cpu/123456" in record["detail"]
    assert installer._cpu_benchmark_note == record["detail"]


def test_config_pin_overrides_builtin(tmp_path):
    installer, actions = build(tmp_path, cpu_benchmark_geekbench_version="6.7.1", cpu_benchmark_sha256="b" * 64)
    installer._run_cpu_benchmark()
    argv = actions.calls[0][0]
    assert "6.7.1" in argv and "b" * 64 in argv


def test_disabled_does_nothing(tmp_path):
    installer, actions = build(tmp_path, run_cpu_benchmark=False)
    installer._run_cpu_benchmark()
    assert actions.calls == [] and step(installer) == {}


def test_claim_link_never_reaches_note_or_state(tmp_path):
    installer, _ = build(tmp_path, FakeTool(output=GOOD.rstrip("\n") + f" {CLAIM}\n"))
    installer._run_cpu_benchmark()
    assert "claim" not in (step(installer)["detail"] + installer._cpu_benchmark_note).lower()
    assert "SECRETKEY" not in json.dumps(StateStore(installer.config.state_dir).load())


@pytest.mark.parametrize("code, needle", [(4, "--resolve"), (2, "refused"), (3, "failed")])
def test_nonzero_exit_warns_and_continues(tmp_path, code, needle):
    err = ActionError(f"action failed ({code}): measure the host CPU\ncpu-benchmark: something went wrong")
    installer, _ = build(tmp_path, FakeTool(error=err))
    installer._run_cpu_benchmark()  # must not raise
    record = step(installer)
    assert record["status"] == "warned" and needle in record["detail"]
    assert installer._cpu_benchmark_note == record["detail"]


def test_timeout_warns(tmp_path):
    installer, _ = build(tmp_path, FakeTool(error=ActionTimeout("action timed out after 2700s and was killed")))
    installer._run_cpu_benchmark()
    assert step(installer)["status"] == "warned" and "timed out" in step(installer)["detail"]


def test_unpinned_architecture_warns_without_running(tmp_path, monkeypatch):
    monkeypatch.setattr(installer_module.platform, "machine", lambda: "riscv64")
    installer, actions = build(tmp_path)
    installer._run_cpu_benchmark()
    assert actions.calls == [] and step(installer)["status"] == "warned"
    assert "no pinned Geekbench archive" in step(installer)["detail"]


def test_tool_integrity_mismatch_warns_without_running(tmp_path):
    fake_tool = tmp_path / "cpu_benchmark.py"
    fake_tool.write_text("#!/usr/bin/env python3\nprint('tampered')\n")
    fake_tool.with_name("cpu_benchmark.py.sha256").write_text(hashlib.sha256(b"other").hexdigest() + "  cpu_benchmark.py\n")
    installer, actions = build(tmp_path)
    installer._cpu_benchmark_tool_path = lambda: fake_tool
    installer._run_cpu_benchmark()
    assert actions.calls == [] and step(installer)["status"] == "warned"
    assert "integrity" in step(installer)["detail"]


def test_missing_tool_warns(tmp_path):
    installer, actions = build(tmp_path)
    installer._cpu_benchmark_tool_path = lambda: tmp_path / "absent.py"
    installer._run_cpu_benchmark()
    assert actions.calls == [] and step(installer)["status"] == "warned"


def test_resume_does_not_measure_twice_and_restores_note(tmp_path):
    installer, actions = build(tmp_path)
    installer._run_cpu_benchmark()
    again = Installer(installer.config, actions, inspect_host=False)
    again._cpu_benchmark_tool_path = installer._cpu_benchmark_tool_path
    again._run_cpu_benchmark()
    assert len(actions.calls) == 1
    assert "single_core=2900" in again._cpu_benchmark_note


def test_dry_run_plans_and_executes_nothing(tmp_path):
    actions = HostActions(dry_run=True)
    installer, _ = build(tmp_path, actions)
    installer._run_cpu_benchmark()
    assert [Path(p.argv[0]).name for p in actions.planned] == ["cpu_benchmark.py"]


def test_allowlist_refuses_force_binary_and_state_dir():
    for flag in ("--force", "--binary", "--state-dir", "--resolve"):
        with pytest.raises(ActionError, match="not allowlisted"):
            HostActions(dry_run=True).run(["/opt/x/tools/cpu_benchmark.py", flag, "x"])


def test_stage2_runs_it_after_swap_health_before_apt_timers_and_completion_note(tmp_path):
    installer, disk = make_stage2(tmp_path)
    installer.config = replace(installer.config, run_io_benchmark=False, run_cpu_benchmark=True)
    order: list[str] = []
    for name in ("_grow_root_to_plan", "_health_gate_swap_devices", "_release_apt_timers", "_run_cpu_benchmark"):
        setattr(installer, name, (lambda n: lambda *a, **k: order.append(n))(name))
    installer._stage2()
    assert order == [
        "_grow_root_to_plan", "_health_gate_swap_devices", "_run_cpu_benchmark", "_release_apt_timers"
    ]


def test_a_summary_line_that_somehow_carries_a_claim_link_is_withheld_whole(tmp_path):
    installer, _ = build(tmp_path, FakeTool(output=GOOD.rstrip("\n") + f" {CLAIM}\n"))
    installer._run_cpu_benchmark()
    assert installer._cpu_benchmark_note == "cpu-benchmark: finished (summary withheld)"
