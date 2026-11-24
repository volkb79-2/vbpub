"""WP-10 P2: the install-time iobench rung is config, not a hard-coded veryquick.

Everything here is a boundary test: the runner's calibration is never executed,
and the installer's host is the SimDisk fake shared with test_io_benchmark.py.
"""

from __future__ import annotations

import contextlib
import importlib.machinery
import importlib.util
import io
import json
from pathlib import Path
import shutil
import sys

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
from debian_install_v2.installer import IOBENCH_RESULT_KEYS
from debian_install_v2.tests.test_io_benchmark import GIB, RESULTS, make, step

PROJECT = Path(__file__).resolve().parents[2]
RUNNER = PROJECT / "tools" / "iobench-install-runner"
IOBENCH_PLAN = PROJECT.parents[1] / "libraries" / "iobench" / "src" / "iobench" / "plan.py"


def load_runner(path: Path = RUNNER):
    """The installer's runner script (or a copy of it), imported without running its main()."""
    spec = importlib.util.spec_from_loader(
        "iobench_install_runner",
        importlib.machinery.SourceFileLoader("iobench_install_runner", str(path)),
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


# --- the runner's main(): the rung must reach iobench and the record ----------

def stub_calibrate_source(seen: Path, calibration: dict) -> str:
    """The stub iobench calibration module: it records the Config it was handed."""
    lines = [
        "import json",
        "from pathlib import Path",
        "",
        f"_SEEN = Path({str(seen)!r})",
        f"_CALIBRATION = json.loads({json.dumps(calibration, sort_keys=True)!r})",
        "",
        "",
        "class _Outcome:",
        "    exit_code = 0",
        '    status = "complete"',
        '    message = ""',
        "    calibration = _CALIBRATION",
        "",
        "",
        "class Calibrator:",
        "    def __init__(self, host, config):",
        "        self.host, self.config = host, config",
        "        _SEEN.write_text(json.dumps(vars(config), sort_keys=True), encoding='utf-8')",
        "",
        "    def execute(self):",
        "        return _Outcome()",
        "",
    ]
    return "\n".join(lines)


def stub_install_root(tmp_path: Path, *, budgets: dict[str, int] | None = None) -> Path:
    """A stand-in installer tree: the REAL runner beside a stub iobench package.

    The runner resolves its package from ``<its own parent>/iobench/src``, so a
    copy of the script beside a stub package is exactly the layout the bootstrap
    creates. The stub records the ``Config`` it was handed and returns a canned
    complete calibration, so ``main()`` runs in process without fio, a block
    device or a signal.
    """
    root = tmp_path.resolve()
    (root / "tools").mkdir(parents=True, exist_ok=True)
    shutil.copyfile(RUNNER, root / "tools" / "iobench-install-runner")
    package = root / "iobench" / "src" / "iobench"
    package.mkdir(parents=True, exist_ok=True)
    (package / "__init__.py").write_text("", encoding="utf-8")
    (package / "plan.py").write_text(
        "PROFILE_BUDGET_S = %r\n" % ({"veryquick": 210, "quick": 720, "standard": 3600}
                                    if budgets is None else budgets),
        encoding="utf-8",
    )
    (package / "guard.py").write_text(
        "def init_device(host, path, *, own, expect_size, expect_partuuid, expect_serial,\n"
        "                created, dry_run=False):\n"
        "    return None\n",
        encoding="utf-8",
    )
    (package / "host.py").write_text(
        'class RealHost:\n    """Stand-in for the operating-system host; it reads no device."""\n',
        encoding="utf-8",
    )
    (package / "stages.py").write_text(
        "from dataclasses import dataclass\n\n"
        "@dataclass\n"
        "class Config:\n"
        "    target: str\n"
        "    tool_version: str\n"
        "    profile: str = 'standard'\n"
        "    state_dir: str | None = None\n"
        "    installer: bool = False\n",
        encoding="utf-8",
    )
    model = {key: {"value": 100 + index, "status": "proven"}
             for index, key in enumerate(IOBENCH_RESULT_KEYS)}
    calibration = {"calibration": {"id": "stub-run", "proposals": {"iocost_model": model}}}
    (package / "calibrate.py").write_text(
        stub_calibrate_source(root / "seen-config.json", calibration), encoding="utf-8"
    )
    return root


def run_main(argv: list[str]) -> tuple[object, str, str]:
    """Run the runner's own ``main()`` in this process with ``argv``.

    Returns ``(exit_status, stdout, stderr)``. The runner refuses with a
    ``SystemExit`` whose code is its message, so that code is returned as it
    stands: a ``--profile`` refusal and a state-dir refusal are different texts
    that must not be flattened into one exit number.
    """
    # The runner imports its package from its own ``../iobench/src``. One
    # parametrized test writes a fresh stub per case, so the package cached from
    # an earlier case must go: without this the second case would import the
    # first case's stub (and record into its seen-file).
    cached = {name for name in sys.modules if name == "iobench" or name.startswith("iobench.")}
    for name in cached:
        del sys.modules[name]
    module = load_runner(Path(argv[0]))
    saved, saved_path = sys.argv, list(sys.path)
    out, err = io.StringIO(), io.StringIO()
    sys.argv = list(argv)
    try:
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            try:
                status = module.main()
            except SystemExit as exc:
                status = exc.code
    finally:
        sys.argv = saved
        # The runner prepends its stub package to sys.path; restore both it and
        # sys.modules so the next case (and any later test) sees its own stub.
        sys.path[:] = saved_path
        for name in {n for n in sys.modules if n == "iobench" or n.startswith("iobench.")}:
            del sys.modules[name]
    return (0 if status is None else status), out.getvalue(), err.getvalue()


def write_record(state_dir: Path, profile: str) -> None:
    """A state directory whose stored record names `profile` (iobench's own shape)."""
    state_dir.mkdir(parents=True, exist_ok=True)
    (state_dir / "calibration.json").write_text(
        json.dumps({"run": {"profile": profile}}), encoding="utf-8"
    )


RAW_PARTITION = "/dev/vda5"
RUNNER_PARTUUID = "12345678-1234-1234-1234-123456789012"
# iobench's own short table, as the stub package publishes it.
STUB_BUDGETS = {"veryquick": 210, "quick": 720, "standard": 3600}


def runner_argv(root: Path, *, profile: str, state_dir: Path, output: Path) -> list[str]:
    """The exact argv shape the installer builds (installer.py:3729-3741)."""
    return [
        str(root / "tools" / "iobench-install-runner"),
        "--profile", profile,
        "--target", RAW_PARTITION,
        "--expect-size", str(8 * GIB),
        "--expect-partuuid", RUNNER_PARTUUID,
        "--state-dir", str(state_dir),
        "--output", str(output),
    ]


@pytest.mark.parametrize("profile", IO_BENCHMARK_PROFILES)
def test_the_runner_main_hands_the_rung_to_iobench_and_records_it(tmp_path, profile):
    """Reverting the runner to a hard-coded veryquick, or dropping --output, goes red here."""
    root = stub_install_root(tmp_path, budgets=STUB_BUDGETS)
    state_dir = tmp_path / "state" / "iobench" / profile
    output = tmp_path / "state" / "io-benchmark.json"

    status, out, err = run_main(runner_argv(root, profile=profile, state_dir=state_dir, output=output))

    assert status == 0, err
    # runner :104 — the rung iobench is handed is the requested one.
    seen = json.loads((root / "seen-config.json").read_text(encoding="utf-8"))
    assert seen["profile"] == profile
    assert seen["target"] == RAW_PARTITION
    assert seen["state_dir"] == str(state_dir)
    assert seen["installer"] is True
    # runner :119 — the record names the rung that produced it.
    record = json.loads(output.read_text(encoding="utf-8"))
    assert record["profile"] == profile
    assert record["target"] == RAW_PARTITION
    assert record["calibration_id"] == "stub-run"
    # runner :126 — the file lands at --output, not at state_dir.parent/io-benchmark.json.
    assert output.parent == tmp_path / "state"
    assert not (state_dir / "io-benchmark.json").exists()
    assert output.stat().st_mode & 0o777 == 0o600
    assert set(record["results"]) == set(IOBENCH_RESULT_KEYS)
    assert f"iobench {profile}:" in out


def test_the_runner_main_refuses_a_state_dir_recorded_for_another_rung(tmp_path):
    """runner :80 — one state directory per rung, refused before iobench is built."""
    root = stub_install_root(tmp_path, budgets=STUB_BUDGETS)
    state_dir = tmp_path / "state" / "iobench" / "quick"
    write_record(state_dir, "standard")
    output = tmp_path / "state" / "io-benchmark.json"

    status, _out, _err = run_main(runner_argv(root, profile="quick", state_dir=state_dir, output=output))

    assert "state_dir_profile_mismatch" in str(status)
    assert str(state_dir) in str(status) and "standard" in str(status) and "quick" in str(status)
    # It refused before the calibration was built, and it wrote nothing.
    assert not (root / "seen-config.json").exists()
    assert json.loads((state_dir / "calibration.json").read_text(encoding="utf-8"))["run"]["profile"] == "standard"
    assert not output.exists()


def test_the_runner_falls_back_to_iobench_for_the_rung_vocabulary(tmp_path):
    """The rung vocabulary is iobench's: a rung it cannot plan is refused by name."""
    root = stub_install_root(tmp_path, budgets={"quick": 720, "standard": 3600})
    state_dir = tmp_path / "state" / "iobench" / "thorough"
    output = tmp_path / "state" / "io-benchmark.json"

    status, _out, _err = run_main(runner_argv(root, profile="thorough", state_dir=state_dir, output=output))

    assert "unknown_profile" in str(status)
    assert "thorough is not an iobench rung" in str(status)
    assert "quick, standard" in str(status)
    # Nothing ran on a guessed budget, and the installer never sends this rung:
    # config load refuses it first (test_an_unreleased_rung_is_refused_by_name).
    assert not (root / "seen-config.json").exists()
    assert not output.exists()
    assert "thorough" not in IO_BENCHMARK_PROFILES


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
        # The output is the install's own file: exactly <state_dir>/../io-benchmark.json.
        argv[:12] + ["/etc/fstab"],                                    # any absolute path at all
        argv[:12] + ["/var/lib/vbpub/bootstrap/benchmark.json"],        # the right dir, another name
        argv[:12] + ["/var/lib/vbpub/bootstrap/iobench/io-benchmark.json"],  # one level too deep
        argv[:10] + ["/var/lib/vbpub/bootstrap/iobench/io-benchmark.json"] + argv[11:],  # not a rung dir
        argv[:10] + ["/var/lib/vbpub/bootstrap/iobench/standard"] + argv[11:],  # quick into standard's dir
    ):
        with pytest.raises(ActionError):
            HostActions._validate(bad)


@pytest.mark.parametrize("profile", IO_BENCHMARK_PROFILES)
def test_a_failure_before_the_runner_starts_still_names_the_rung(tmp_path, profile):
    """The generic warned path (installer.py:3768) names the rung, not only its `started` detail."""
    installer, disk = make(tmp_path, io_benchmark_profile=profile)
    disk.fail["apt-get"] = "simulated apt-get failure: install fio"   # before the runner starts
    sent: list[str] = []
    installer._notify = lambda message, **kwargs: sent.append(kwargs.get("event", message))  # type: ignore[method-assign]

    installer._run_io_benchmark()

    records = step(installer)
    assert records["status"] == "warned"
    assert records["detail"].startswith(f"iobench {profile} failed (advisory)")
    assert "simulated apt-get failure" in records["detail"]
    assert "profile=" not in records["detail"]          # the started detail is overwritten
    assert len(sent) == 1 and f"iobench {profile} failed (advisory)" in sent[0]
    # It failed before the runner: nothing was wiped and nothing was executed.
    assert not any(Path(action.argv[0]).name in {"wipefs", "iobench-install-runner"}
                   for action in disk.planned)


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
    assert len(sent) == 1 and "iobench quick failed (advisory)" in sent[0]

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
