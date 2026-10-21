"""Harness shared by the reviewed CLI cases: the hosts they run on and the way an invocation is executed."""
import copy
import io
import json
import pathlib
import tomllib

from cli_extended import CliIdentity

from iobench import cli, testing

from helpers import INVERSION, calibrate, make_host

CATALOG = pathlib.Path(__file__).resolve().parents[1] / "docs" / "cli-review.toml"
IDENT = CliIdentity("iobench", "0.1.0", "cache-proof disk benchmark", "iobench")
_CACHE: dict[str, object] = {}


def _calibrated():
    if "cal" not in _CACHE:
        host = make_host(cache=INVERSION)
        assert calibrate(host).status == "complete"
        host.sweep_model = lambda k: testing.SweepOutcome(probe_p99_us=2e5, load_iops=800.0, load_fsync_p99_us=9e4, drain_bps=10.5e6)
        host.put("/var/lib/iobench/gov.hist", "{}\n")
        _CACHE["cal"] = host
    return copy.deepcopy(_CACHE["cal"])


def _fresh():
    host = testing.standard_host(target_size=testing.R1002_SIZE)
    host.dynamic["/sys/fs/cgroup/wings.slice/io.pressure"] = lambda: "some avg10=0.00 avg60=0.00 avg300=0.00 total=0\n"
    return host


def host_for(argv):
    """A fresh estate for `target init` (nothing prepared yet), the calibrated one for everything else."""
    return _fresh() if argv[:2] == ["target", "init"] else _calibrated()


def execute(argv):
    host = host_for(argv)
    out, err = io.StringIO(), io.StringIO()
    code = cli.build_cli(IDENT, host_factory=lambda: host).run(argv=list(argv), stdout=out, stderr=err)
    return code, out.getvalue(), err.getvalue()


def load_cases():
    doc = tomllib.loads(CATALOG.read_text())
    return [c for c in doc["cases"] if c.get("state") == "active"]


def slug(case_id: str) -> str:
    return case_id.removeprefix("case:route:entrypoint:iobench/").replace("option:route:entrypoint:iobench/", "").replace("/", ".")
