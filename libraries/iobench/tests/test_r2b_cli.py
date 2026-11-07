"""IOB-R2-01 (group B): the verb handlers' contract (an integer exit code), option defaults and output ordering."""
from types import SimpleNamespace

from cli_extended import CliIdentity

from iobench import cli, testing

from helpers import INVERSION, R1002_PARTUUID, R1002_SIZE, calibrate, make_host

IDENT = CliIdentity("iobench", "0.1.0", "cache-proof disk benchmark", "iobench")
GUARDS = ["--confirm-quiet-window", "--protect-cgroup", "game-server.slice", "--abort-psi", "40"]
RUN_BASE = ["--target", "/dev/vda7", "--work-dir", "/bench/w", *GUARDS]


class Collector:
    def __init__(self):
        self.primary_lines = []

    def primary(self, text):
        self.primary_lines.append(text)


def invoke(host, verb, *argv, sub=None, dry_run=False, json_mode=True):
    """Parse ``argv`` with the verb's own parser and call the verb's handler directly: returns (what it returned, its primary output)."""
    registry = cli.build_cli(IDENT, host_factory=lambda: host)
    owner = registry.delegates[verb] if sub else registry
    name = sub or verb
    args = owner.command_parsers[name].parse_args(list(argv))
    runtime = SimpleNamespace(dry_run=dry_run, json_mode=json_mode, output=Collector())
    rc = owner.handlers[name](args, runtime)
    return rc, runtime.output.primary_lines


def test_every_verb_handler_returns_the_integer_zero_on_success():
    host = make_host(cache=INVERSION)
    returned = {}
    returned["target show"], _ = invoke(host, "target", "/dev/vda7", sub="show")
    returned["calibrate dry"], _ = invoke(host, "calibrate", *RUN_BASE, "--assume-r-est", "1.36e9", dry_run=True)
    assert calibrate(host).status == "complete"
    returned["matrix dry"], _ = invoke(host, "matrix", *RUN_BASE, dry_run=True)
    returned["run dry"], _ = invoke(host, "run", *RUN_BASE, dry_run=True)
    returned["show"], _ = invoke(host, "show", "--proposals")
    returned["ceilings add"], _ = invoke(host, "ceilings", "--dir", "write", "--workload", "seq1m", "--bps", "1e9", "--duration-s", "600",
                                         "--source", "governor", "--kind", "file", sub="add")
    returned["ceilings summary"], _ = invoke(host, "ceilings", sub="summary")
    returned["restore dry"], _ = invoke(host, "restore", dry_run=True)
    returned["restore"], _ = invoke(host, "restore")
    fresh = testing.standard_host(target_size=R1002_SIZE)
    returned["target init"], _ = invoke(fresh, "target", "/dev/vda7", "--i-own-this-device", "--expect-size", str(R1002_SIZE),
                                        "--expect-partuuid", R1002_PARTUUID, sub="init")
    assert returned == {name: 0 for name in returned} and all(type(rc) is int for rc in returned.values())


def test_a_run_whose_calibration_is_current_plans_only_the_matrix_even_without_a_result_file():
    host = make_host(cache=INVERSION)
    assert calibrate(host).status == "complete"
    host.remove("/var/lib/iobench/result.json")  # only calibration.json is left
    _, lines = invoke(host, "run", *RUN_BASE, dry_run=True)
    assert lines[0]["command"] == "matrix"


def test_a_run_without_a_calibration_plans_the_calibration():
    host = make_host(cache=INVERSION)
    _, lines = invoke(host, "run", *RUN_BASE, "--assume-r-est", "1.36e9", dry_run=True)
    assert lines[0]["command"] == "calibrate"


def test_the_primary_output_is_indented_with_sorted_keys_unless_json_is_asked_for():
    runtime = SimpleNamespace(json_mode=False, output=Collector())
    cli._emit(runtime, {"b": 1, "a": {"d": 1, "c": 2}})
    assert runtime.output.primary_lines == ['{\n "a": {\n  "c": 2,\n  "d": 1\n },\n "b": 1\n}']
    compact = SimpleNamespace(json_mode=True, output=Collector())
    cli._emit(compact, {"b": 1, "a": 2})
    assert compact.output.primary_lines == [{"b": 1, "a": 2}]


def test_the_profile_comes_from_the_option_or_the_default_and_a_matrix_has_none_of_its_own():
    registry = cli.build_cli(IDENT)
    quick = registry.command_parsers["calibrate"].parse_args([*RUN_BASE, "--profile", "quick"])
    assert cli._config(quick, "0.1.0").profile == "quick"
    default = registry.command_parsers["calibrate"].parse_args(RUN_BASE)
    assert cli._config(default, "0.1.0").profile == "standard"
    matrix = registry.command_parsers["matrix"].parse_args(RUN_BASE)
    assert cli._config(matrix, "0.1.0").profile == "standard"


def test_the_sweep_flags_are_off_for_a_verb_that_does_not_offer_them():
    registry = cli.build_cli(IDENT)
    cal = registry.command_parsers["calibrate"].parse_args(RUN_BASE)
    assert not hasattr(cal, "full_sweep") and not hasattr(cal, "repeat")
    cfg = cli._config(cal, "0.1.0")
    assert cfg.full_sweep is False and cfg.repeat is False
    matrix = registry.command_parsers["matrix"].parse_args([*RUN_BASE, "--full-sweep", "--knob", "max_sectors_kb", "--candidates", "64,128", "--repeat"])
    cfg = cli._config(matrix, "0.1.0")
    assert cfg.full_sweep is True and cfg.repeat is True
