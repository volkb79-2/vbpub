"""WP-10 P2: the install-time iobench rung is config, not a hard-coded veryquick.

Everything here is a boundary test: the runner's calibration is never executed,
and the installer's host is the SimDisk fake shared with test_io_benchmark.py.
"""

from __future__ import annotations

import importlib.machinery
import importlib.util
import json
from pathlib import Path

import pytest

from debian_install_v2.actions import ActionError, HostActions
from debian_install_v2.config import (
    IOBENCH_OUTER_MARGIN_S,
    IO_BENCHMARK_OUTER_TIMEOUT_S,
    IO_BENCHMARK_PROFILE_DEFAULT,
    IO_BENCHMARK_PROFILES,
    Config,
    ConfigError,
    load_config,
    validate_config,
)
from debian_install_v2.tests.test_io_benchmark import RESULTS, make, step

PROJECT = Path(__file__).resolve().parents[2]
RUNNER = PROJECT / "tools" / "iobench-install-runner"
IOBENCH_PLAN = PROJECT.parents[1] / "libraries" / "iobench" / "src" / "iobench" / "plan.py"


def load_runner():
    """The installer's runner script, imported without running its main()."""
    spec = importlib.util.spec_from_loader(
        "iobench_install_runner",
        importlib.machinery.SourceFileLoader("iobench_install_runner", str(RUNNER)),
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


# --- config: one closed vocabulary, one timeout table ------------------------

def test_profile_defaults_to_quick():
    assert Config().io_benchmark_profile == "quick"
    assert load_config(raw_json='{"schema_version": 1}').io_benchmark_profile == "quick"
    assert IO_BENCHMARK_PROFILE_DEFAULT == "quick"


@pytest.mark.parametrize("profile", IO_BENCHMARK_PROFILES)
def test_each_released_rung_loads_and_keeps_its_name(profile):
    assert load_config(raw_json=json.dumps({"io_benchmark_profile": profile})).io_benchmark_profile == profile


@pytest.mark.parametrize("bad", ["thorough", "matrix", "Quick", "", "quick ", 1, True, None])
def test_an_unreleased_rung_is_refused_by_name(bad):
    with pytest.raises(ConfigError, match="io_benchmark_profile must be one of"):
        load_config(raw_json=json.dumps({"io_benchmark_profile": bad}))
    with pytest.raises(ConfigError, match="io_benchmark_profile must be one of"):
        validate_config(Config(io_benchmark_profile=bad))


def test_the_profile_key_is_accepted_and_unused_with_the_benchmark_off():
    for profile in IO_BENCHMARK_PROFILES:
        config = load_config(raw_json=json.dumps({"run_io_benchmark": False, "io_benchmark_profile": profile}))
        assert config.run_io_benchmark is False and config.io_benchmark_profile == profile


def iobench_budgets() -> dict[str, int]:
    """iobench's own per-rung budget table, read from the library source (READ-ONLY)."""
    for line in IOBENCH_PLAN.read_text(encoding="utf-8").splitlines():
        if line.startswith("PROFILE_BUDGET_S = "):
            return json.loads(line.split("= ", 1)[1].strip().rstrip(",").replace("'", '"'))
    raise AssertionError("iobench's PROFILE_BUDGET_S moved; update this reader")


def test_one_timeout_table_covers_every_rung_and_is_the_iobench_budget_plus_the_margin():
    assert set(IO_BENCHMARK_OUTER_TIMEOUT_S) == set(IO_BENCHMARK_PROFILES)

    budgets = iobench_budgets()
    assert budgets
    for profile in IO_BENCHMARK_PROFILES:
        assert IO_BENCHMARK_OUTER_TIMEOUT_S[profile] == budgets[profile] + IOBENCH_OUTER_MARGIN_S

    # The operator decision of 2026-11-24 (WP-10 Q3) is these exact numbers.
    assert IO_BENCHMARK_OUTER_TIMEOUT_S == {"veryquick": 270, "quick": 780, "standard": 3660}


# --- installer: the rung reaches the runner, its state dir and the record ----

def _runner_argv(disk) -> tuple[str, ...]:
    return next(action.argv for action in disk.planned if Path(action.argv[0]).name == "iobench-install-runner")


@pytest.mark.parametrize("profile", IO_BENCHMARK_PROFILES)
def test_each_rung_is_named_and_bounded_on_the_runner_called(tmp_path, profile):
    installer, disk = make(tmp_path, io_benchmark_profile=profile)
    installer._run_io_benchmark()

    argv = _runner_argv(disk)
    assert argv[1:3] == ("--profile", profile)
    assert argv[9] == "--state-dir" and argv[11] == "--output"
    assert argv[10] == str(tmp_path / "state" / "iobench" / profile)
    assert argv[12] == str(tmp_path / "state" / "io-benchmark.json")
    assert (("iobench-install-runner", IO_BENCHMARK_OUTER_TIMEOUT_S[profile])
            in disk.timeouts)
    assert f"iobench {profile}" in step(installer)["detail"]
    assert step(installer)["status"] == "success"


def test_each_rung_gets_its_own_state_dir(tmp_path):
    dirs = set()
    for profile in IO_BENCHMARK_PROFILES:
        installer, disk = make(tmp_path / profile, io_benchmark_profile=profile)
        installer._run_io_benchmark()
        dirs.add(_runner_argv(disk)[10])
    assert dirs == {str(tmp_path / profile / "state" / "iobench" / profile) for profile in IO_BENCHMARK_PROFILES}
    assert len(dirs) == 3


def test_the_runner_argv_passes_the_production_allowlist_and_the_old_shape_does_not():
    argv = [
        str(RUNNER),
        "--profile", "quick",
        "--target", "/dev/vda5",
        "--expect-size", "8589934592",
        "--expect-partuuid", "12345678-1234-1234-1234-123456789012",
        "--state-dir", "/var/lib/vbpub/bootstrap/iobench/quick",
        "--output", "/var/lib/vbpub/bootstrap/io-benchmark.json",
    ]
    HostActions._validate(argv)
    for bad in (
        argv[:1] + ["--target", "/dev/vda5"] + argv[3:],              # the retired 9-argument shape
        argv[:2] + ["thorough"] + argv[3:],                            # an unreleased rung
        argv[:9] + ["--state-dir", "/tmp/../x"] + argv[11:],           # a path with a parent hop
    ):
        with pytest.raises(ActionError):
            HostActions._validate(bad)


@pytest.mark.parametrize("missing", [["rbps"], ["rbps", "wbps", "wseqiops"]])
def test_a_rung_without_every_coefficient_warns_with_the_missing_names(tmp_path, missing):
    installer, disk = make(tmp_path)
    seeded = {key: value for key, value in RESULTS.items() if key not in missing}
    disk.iobench_results = seeded
    sent: list[str] = []
    installer._notify = lambda message, **kwargs: sent.append(kwargs.get("event", message))  # type: ignore[method-assign]

    installer._run_io_benchmark()

    records = step(installer)
    # Advisory: never a crash, never a partial model.
    assert records["status"] == "warned"
    for key in missing:
        assert key in records["detail"]
    assert "iobench quick" in records["detail"]
    assert len(sent) == 1 and "iobench failed (advisory)" in sent[0]

    installer._configure_iocost()
    assert step(installer, "iocost")["status"] == "skipped"
    assert "no valid io benchmark result" in installer._iocost_note
    assert not [path for path in disk.files if path.endswith("io.cost.model")]


def test_the_record_names_the_rung_that_produced_it():
    runner = load_runner()
    document = runner._document(
        profile="standard",
        target="/dev/vda5",
        partition_size_bytes=8 * 1024 ** 3,
        tool_version="vbpub-iobench-0",
        calibration_id="run-1",
        results=dict(RESULTS),
    )
    assert document["profile"] == "standard"

    # A rung that left a coefficient absent records only what it measured.
    model = {key: {"value": value, "status": "proven"} for key, value in RESULTS.items()}
    model["rseqiops"] = {"value": None, "status": "absent"}
    partial = runner._document(
        profile="quick", target="/dev/vda5", partition_size_bytes=8 * 1024 ** 3,
        tool_version="vbpub-iobench-0", calibration_id="run-2",
        results=runner._collect_results(model),
    )
    assert "rseqiops" not in partial["results"]
    assert runner._absent(partial["results"]) == ["rseqiops"]
    assert set(partial["results"]) == set(RESULTS) - {"rseqiops"}


# --- runner: a state dir belongs to one rung ---------------------------------

def write_record(state_dir: Path, profile: str) -> None:
    state_dir.mkdir(parents=True, exist_ok=True)
    (state_dir / "calibration.json").write_text(
        json.dumps({"run": {"profile": profile}}), encoding="utf-8"
    )


@pytest.mark.parametrize("recorded", IO_BENCHMARK_PROFILES)
def test_a_state_dir_with_another_rungs_record_is_refused_not_reused(tmp_path, recorded):
    runner = load_runner()
    state_dir = tmp_path / "state" / "iobench" / "quick"
    write_record(state_dir, recorded)
    target = next(profile for profile in IO_BENCHMARK_PROFILES if profile != recorded)

    with pytest.raises(SystemExit, match="state_dir_profile_mismatch"):
        runner._require_state_dir_profile(state_dir, target)
    assert json.loads((state_dir / "calibration.json").read_text(encoding="utf-8"))["run"]["profile"] == recorded

    runner._require_state_dir_profile(state_dir, recorded)     # its own rung may resume
    runner._require_state_dir_profile(tmp_path / "empty", target)  # nothing recorded yet


def test_every_config_rung_is_a_real_iobench_rung():
    """The config vocabulary never invents a rung iobench cannot plan."""
    budgets = iobench_budgets()
    assert set(IO_BENCHMARK_PROFILES) <= set(budgets)
    # iobench also plans rungs the install deliberately does not offer.
    assert set(budgets) - set(IO_BENCHMARK_PROFILES) == {"thorough", "matrix", "knob"}


def test_the_runner_falls_back_to_iobench_for_the_rung_vocabulary():
    runner = load_runner()
    assert runner.RESULT_KEYS == tuple(k for k in (
        "rbps", "rseqiops", "rrandiops", "wbps", "wseqiops", "wrandiops"
    ))
    parser = runner._build_parser()
    args = parser.parse_args([
        "--profile", "thorough", "--target", "/dev/vda5", "--expect-size", "1",
        "--expect-partuuid", "12345678-1234-1234-1234-123456789012",
        "--state-dir", "/tmp/s", "--output", "/tmp/o",
    ])
    assert args.profile not in IO_BENCHMARK_PROFILES
    # The runner itself refuses it after loading iobench's own budget table; the
    # installer never gets that far because config load refuses it first.
    assert "thorough" in iobench_budgets()


def test_the_runner_requires_the_profile_and_output_flags(tmp_path):
    runner = load_runner()
    parser = runner._build_parser()
    for missing in ("--profile", "--output"):
        argv = [
            "--profile", "quick", "--target", "/dev/vda5", "--expect-size", "1",
            "--expect-partuuid", "12345678-1234-1234-1234-123456789012",
            "--state-dir", str(tmp_path / "s"), "--output", str(tmp_path / "o"),
        ]
        index = argv.index(missing)
        with pytest.raises(SystemExit):
            parser.parse_args(argv[:index] + argv[index + 2:])
    # The full shape is what the installer sends and the allowlist accepts.
    assert parser.parse_args(argv).profile == "quick"
