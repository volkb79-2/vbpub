"""IOB-CLI: the cli-extended shell, exit codes, messages and the generated surface."""
import io
import json

import pytest
from cli_extended import CliIdentity

from iobench import cli

from helpers import INVERSION, calibrate, make_host

IDENT = CliIdentity("iobench", "0.1.0", "cache-proof disk benchmark", "iobench")


def run(host, *argv):
    out, err = io.StringIO(), io.StringIO()
    code = cli.build_cli(IDENT, host_factory=lambda: host).run(argv=list(argv), stdout=out, stderr=err)
    return code, out.getvalue(), err.getvalue()


def test_cli_01_commands_and_exit_codes():
    host = make_host(cache=INVERSION)
    code, out, _ = run(host, "check", "--json")
    assert code == 4 and json.loads(out)["state"] == "absent"
    code, out, _ = run(host, "calibrate", "--target", "/dev/vda7", "--work-dir", "/bench/w", "--dry-run", "--assume-r-est", "1.36e9", "--json",
                       "--confirm-quiet-window", "--protect-cgroup", "wings.slice", "--abort-psi", "40")
    assert code == 0 and not host.write_log
    code, out, _ = run(host, "target", "show", "/dev/vda7", "--json")
    assert code == 0 and json.loads(out)["target"] == "/dev/vda7"
    assert calibrate(host).status == "complete"
    code, out, _ = run(host, "check", "--json")
    assert code in (0, 7) and json.loads(out)["state"].startswith("current")
    code, out, _ = run(host, "show", "--proposals", "--json")
    assert code == 0 and "caps" in json.loads(out)["object"]
    code, out, _ = run(host, "ceilings", "summary", "--json")
    assert code == 0 and "read" in json.loads(out)
    code, out, _ = run(host, "ceilings", "add", "--dir", "write", "--workload", "seq1m", "--bps", "1e9", "--duration-s", "600", "--source", "governor", "--json")
    assert code == 0 and json.loads(out)["appended"] is True
    code, out, _ = run(host, "restore", "--json")
    assert code == 0
    code, out, _ = run(host, "restore", "--dry-run", "--json")
    assert code == 0 and json.loads(out)["would_restore"] is False
    code, out, _ = run(host, "check", "--section", "caps", "--calibration", "--json")
    assert code in (0, 7)
    code, _, err = run(host, "matrix", "--target", "/dev/vda7", "--candidates", "1,2")
    assert code == 2


def test_cli_02_messages_name_condition_and_remedy():
    host = make_host(cache=INVERSION)
    host.containers = ["game-1"]
    code, out, err = run(host, "calibrate", "--target", "/dev/vda7", "--work-dir", "/bench/w")
    assert code == 2 and "production" in (err + out).lower() and not host.write_log
    code, out, err = run(host, "target", "init", "/dev/vda7")
    assert code == 2 and "--expect-size" in err and "pass --expect-size 435853320192" in err
    code, out, err = run(host, "show", "--proposals")
    assert code != 0 and "absent" in err


def test_cli_10_surface_matches_registry():
    registry = cli.build_cli(IDENT)
    verbs = set(registry.handlers) | set(registry.delegates)
    assert {"target", "calibrate", "matrix", "run", "check", "show", "ceilings", "restore"} <= verbs
    for name in ("calibrate", "matrix", "run", "restore"):
        assert registry.command_parsers[name].parse_args  # registered with its own parser
    out, err = io.StringIO(), io.StringIO()
    assert registry.run(argv=["--version"], stdout=out, stderr=err) == 0 and "0.1.0" in out.getvalue()
