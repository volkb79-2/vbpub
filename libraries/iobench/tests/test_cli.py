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


# ---------------------------------------------------------------------------------------------------------
# every verb through the registry, on FakeHost
# ---------------------------------------------------------------------------------------------------------
from helpers import R1002_PARTUUID, R1002_SIZE  # noqa: E402,F401
from iobench import testing  # noqa: E402

GUARDS = ["--confirm-quiet-window", "--protect-cgroup", "wings.slice", "--abort-psi", "40"]


def fresh_host():
    host = testing.standard_host(target_size=R1002_SIZE)
    host.dynamic["/sys/fs/cgroup/wings.slice/io.pressure"] = lambda: "some avg10=0.00 avg60=0.00 avg300=0.00 total=0\n"
    return host


def test_cli_target_init_device_and_file():
    host = fresh_host()
    code, out, err = run(host, "target", "init", "/dev/vda7", "--i-own-this-device", "--expect-size", str(R1002_SIZE), "--expect-partuuid", R1002_PARTUUID, "--json")
    assert code == 0 and json.loads(out)["marker_written"] is True
    code, out, _ = run(host, "target", "show", "/dev/vda7", "--json")
    assert code == 0 and json.loads(out)["usable_bytes"] > 0
    code, out, _ = run(host, "target", "init", "/dev/vda7", "--i-own-this-device", "--expect-size", str(R1002_SIZE), "--expect-partuuid", R1002_PARTUUID, "--dry-run", "--json")
    assert code == 0 and json.loads(out)["marker_written"] is False
    code, _, err = run(host, "target", "init")
    assert code == 2 and "pass DEVICE" in err
    fhost = testing.standard_host()
    args = ["target", "init", "--file", "/bench/t.img", "--size", str(2 << 30), "--i-own-this-file", "--expect-fs-uuid", "22222222-2222-2222-2222-222222222222", "--json"]
    code, out, err = run(fhost, *args, "--dry-run")
    assert code == 0 and json.loads(out)["kind"] == "file" and not fhost.exists("/bench/t.img")
    code, out, err = run(fhost, *args)
    assert code == 0 and fhost.exists("/bench/t.img")


def test_cli_calibrate_matrix_run_and_reads():
    host = make_host(cache=INVERSION)
    base = ["--target", "/dev/vda7", "--work-dir", "/bench/w", *GUARDS]
    code, out, _ = run(host, "calibrate", *base, "--json")
    assert code == 0 and json.loads(out)["status"] == "complete"
    host.sweep_model = lambda k: testing.SweepOutcome(probe_p99_us=2e5, load_iops=800.0, load_fsync_p99_us=9e4, drain_bps=10.5e6)
    code, out, _ = run(host, "matrix", *base, "--json")
    assert code == 0 and json.loads(out)["run"]["stages"]["S4"] == "ok"
    code, out, _ = run(host, "matrix", *base, "--knob", "max_sectors_kb", "--candidates", "64,128", "--json")
    assert code == 0
    code, out, err = run(host, "matrix", *base, "--knob", "max_sectors_kb", "--candidates", "64", "--json")
    assert code == 2 and "two or three" in err
    code, out, err = run(host, "matrix", *base, "--knob", "max_sectors_kb", "--candidates", "a,b", "--json")
    assert code == 2 and "comma list of integers" in err
    code, out, _ = run(host, "run", *base, "--json")                # a current calibration exists: only the matrix runs
    assert code == 0
    code, out, _ = run(host, "run", *base, "--dry-run", "--json")
    assert code == 0 and json.loads(out)["command"] == "matrix"
    code, out, _ = run(host, "show", "--calibration", "--json")
    assert code == 0 and "ladder" in json.loads(out)["object"]
    code, out, _ = run(host, "show", "--proposals", "--section", "caps", "--json")
    assert code == 0 and "apply" in json.dumps(json.loads(out)["object"])
    code, out, _ = run(host, "show", "--ceilings", "--json")
    assert code == 0 and "observations" in json.loads(out)
    code, out, _ = run(host, "show", "--paths", "--json")
    assert code == 0
    code, out, _ = run(host, "check", "--result", "/nonexistent.json", "--json")
    assert code == 4


def test_cli_run_calibrates_first_and_stops_on_a_failed_calibration():
    host = make_host(cache=INVERSION)
    base = ["--target", "/dev/vda7", "--work-dir", "/bench/w", *GUARDS]
    code, out, _ = run(host, "run", *base, "--dry-run", "--assume-r-est", "1.36e9", "--json")
    assert code == 0 and json.loads(out)["command"] == "calibrate"
    code, out, _ = run(host, "run", *base, "--budget-minutes", "8", "--json")
    assert code == 1 and json.loads(out)["status"] == "failed"
    assert not host.exists("/var/lib/iobench/result.json")


def test_cli_matrix_dry_run_needs_a_calibration():
    host = make_host(cache=INVERSION)
    code, out, err = run(host, "matrix", "--target", "/dev/vda7", "--work-dir", "/bench/w", *GUARDS, "--dry-run", "--json")
    assert code != 0 and ("calibration" in err.lower() or "calibration" in out.lower())


def test_cli_restore_and_ceilings_refusals():
    host = make_host(cache=INVERSION)
    dead = __import__("iobench.restore", fromlist=["Safety"]).Safety(host, "run-dead", "2026-10-21T03:00:00Z")
    dead.acquire()
    dead.set_knob("/sys/class/block/vda/queue/nr_requests", "4")
    code, out, _ = run(host, "restore", "--dry-run", "--json")
    assert code == 0 and json.loads(out)["would_restore"] is True and host.read_text("/sys/class/block/vda/queue/nr_requests") == "4\n"
    code, out, _ = run(host, "restore", "--json")
    assert code == 0 and host.read_text("/sys/class/block/vda/queue/nr_requests") == "64\n"
    code, _, err = run(host, "ceilings", "add", "--dir", "write", "--workload", "seq1m", "--bps", "1e9", "--duration-s", "600", "--source", "governor")
    assert code == 2 and "calibrate" in err
    code, out, _ = run(host, "ceilings", "summary", "--json")
    assert code == 0 and json.loads(out)["history"] == "single-run"
    assert calibrate(host).status == "complete"
    code, _, err = run(host, "ceilings", "add", "--dir", "write", "--workload", "seq1m", "--bps", "1e9", "--duration-s", "10", "--source", "governor")
    assert code == 2
    code, out, _ = run(host, "ceilings", "add", "--dir", "write", "--workload", "seq1m", "--bps", "1e9", "--duration-s", "600", "--source", "governor", "--dry-run", "--json")
    assert code == 0 and json.loads(out)["appended"] is False


def test_cli_show_when_nothing_exists_and_when_corrupt():
    host = make_host(cache=INVERSION)
    code, _, err = run(host, "show", "--calibration")
    assert code == 4 and "absent" in err
    host.makedirs("/var/lib/iobench")
    host.put("/var/lib/iobench/result.json", "{not json")
    code, _, err = run(host, "show", "--proposals")
    assert code == 5 and "corrupt" in err
    code, out, _ = run(host, "check", "--json")
    assert code == 5


def test_cli_identity_comes_from_the_installed_distribution_only(monkeypatch):
    from cli_extended import identity as ident_mod
    with pytest.raises(ident_mod.VersionLookupError):
        cli.identity()                                   # not installed in the test environment: no literal fallback
    monkeypatch.setattr(ident_mod, "installed_version", lambda name: "0.1.0")
    assert cli.identity().version == "0.1.0"
    monkeypatch.setattr(cli, "identity", lambda: IDENT)
    assert cli.main(["--version"]) == 0


def test_cli_plain_output_target_knobs_and_matrix_dry_run():
    host = make_host(cache=INVERSION)
    base = ["--target", "/dev/vda7", "--work-dir", "/bench/w", *GUARDS]
    code, out, err = run(host, "run", *base, "--w-seconds", "3", "--p-ms", "300", "--f-read-ratio", "0.2", "--u-dev-share", "0.6")
    assert code == 0 and "complete" in err + out
    host.sweep_model = lambda k: testing.SweepOutcome(probe_p99_us=2e5, load_iops=800.0, load_fsync_p99_us=9e4, drain_bps=10.5e6)
    plan = json.loads(run(host, "matrix", *base, "--dry-run", "--json")[1])
    assert plan["command"] == "matrix"
    thorough = json.loads(run(make_host(cache=INVERSION), "calibrate", *base, "--profile", "thorough", "--dry-run", "--assume-r-est", "1.36e9", "--json")[1])
    assert any(s["name"] == "ladder-qd" for s in thorough["stages"])


def test_cli_ceilings_summary_warns_about_foreign_history():
    host = make_host(cache=INVERSION)
    assert calibrate(host).status == "complete"
    rows = host.read_text("/var/lib/iobench/ceilings.jsonl").splitlines()
    header = json.loads(rows[0])
    header["identity_digest"] = "0" * len(header["identity_digest"])
    other = [json.dumps(header), *rows[1:]]
    host.put("/var/lib/iobench/ceilings.jsonl", "\n".join(other) + "\n")
    code, out, _ = run(host, "ceilings", "summary", "--json")
    assert code == 0 and json.loads(out).get("warnings")


def test_cli_10_audit_and_surface_pass(monkeypatch):
    """The cli-extended audit and the surface check on the committed artefacts (IOB-CLI-10)."""
    import pathlib
    from cli_extended import check_cli_surface, load_project_config
    from cli_extended.audit import run_audit
    monkeypatch.setattr(cli, "identity", lambda: IDENT)
    root = pathlib.Path(__file__).resolve().parents[1]
    project = load_project_config(root / "pyproject.toml")
    entry = project.select("iobench")
    report = check_cli_surface(cli.build_cli(IDENT), review_path=entry.review, manifest_path=entry.manifest, spec_path=entry.spec,
                               findings_path=entry.findings)
    assert report.passed, report.render()
    items = run_audit(entry, project)
    assert [i.checklist_id for i in items if i.status == "fail"] == []
    assert {i.status for i in items} <= {"pass", "manual", "warn"}
