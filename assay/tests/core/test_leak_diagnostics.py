"""B233: a leaked-process-tree verdict names the processes that leaked."""

from __future__ import annotations

import io
import os
import signal
import sys
from pathlib import Path

import pytest

from assay import process_tree, runner, termination
from assay.errors import Outcome
from assay.process_tree import (
    ProcessTreeDescendant,
    ProcessTreeEvidence,
    ProcessTreeProcessRunner,
    sanitize_cmdline,
)

from tests.core.test_runner_execute import make_lane  # type: ignore[import-not-found]


def test_sanitize_cmdline_redacts_truncates_and_bounds() -> None:
    raw = b"\0".join(
        [b"tool", b"--password", b"hunter2", b"API_TOKEN=abc", b"x" * 500]
        + [b"a"] * 30
    ) + b"\0"
    out = sanitize_cmdline(raw)
    assert out is not None
    assert out[:3] == ("tool", "--password", "<redacted>")
    assert out[3] == "API_TOKEN=<redacted>"
    assert out[4].endswith("...") and len(out[4]) == 203
    assert out[-1] == "..." and len(out) == 17
    assert "hunter2" not in " ".join(out)
    assert sanitize_cmdline(b"") is None


def test_descendant_wire_roundtrip_and_validation() -> None:
    plain = ProcessTreeDescendant(pid=5, ppid=1, comm="sleep")
    assert plain.to_dict() == {"pid": 5, "ppid": 1, "comm": "sleep"}
    rich = ProcessTreeDescendant(pid=5, ppid=1, comm="sleep", cmdline=("sleep", "5"))
    assert ProcessTreeDescendant.from_dict(rich.to_dict()) == rich
    assert rich.describe() == "pid=5 ppid=1 comm=sleep argv=sleep 5"
    assert ProcessTreeDescendant(pid=9, ppid=None, comm=None).describe() == (
        "pid=9 (identity unavailable)"
    )
    with pytest.raises(ValueError):
        ProcessTreeDescendant(pid=9, ppid=None, comm=None, cmdline=("x",))
    with pytest.raises(ValueError):
        ProcessTreeDescendant.from_dict(
            {"pid": 5, "ppid": 1, "comm": "c", "cmdline": [1]}
        )
    with pytest.raises(ValueError):
        ProcessTreeDescendant.from_dict(
            {"pid": 5, "ppid": 1, "comm": "c", "cmdline": "sleep"}
        )


def test_leaked_detail_names_the_descendants() -> None:
    assert termination.detail(termination.Termination.LEAKED) == "leaked-process-tree"
    text = termination.detail(
        termination.Termination.LEAKED,
        leaked_descendants=("pid=5 ppid=1 comm=sleep argv=sleep 5", "pid=6 (identity unavailable)"),
    )
    assert text == (
        "leaked-process-tree: pid=5 ppid=1 comm=sleep argv=sleep 5; "
        "pid=6 (identity unavailable)"
    )


def _leaking_command(tmp_path: Path) -> list[str]:
    pid_file = tmp_path / "leak.pid"
    child = (
        "import os,time\nos.setsid()\n"
        f"open({str(pid_file)!r},'w').write(str(os.getpid()))\ntime.sleep(60)\n"
    )
    parent = (
        "import os,subprocess,sys,time\n"
        f"subprocess.Popen([sys.executable,'-c',{child!r},'--secret','s3cr3t'],"
        "stdin=subprocess.DEVNULL,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)\n"
        f"d=time.monotonic()+30\nwhile not os.path.getsize({str(pid_file)!r}) if os.path.exists({str(pid_file)!r}) else True:\n"
        " time.sleep(0.01)\n"
        " if time.monotonic()>d: raise SystemExit(3)\n"
    )
    return [sys.executable, "-c", parent]


def test_real_leak_reaches_the_claim_progress_and_diagnostics(tmp_path: Path) -> None:
    lane = make_lane(argv=tuple(_leaking_command(tmp_path)))
    result = runner.execute_command(
        lane, cwd=tmp_path, process_runner=ProcessTreeProcessRunner()
    )
    pid_file = tmp_path / "leak.pid"
    try:
        assert result.outcome is Outcome.ERROR
        assert result.termination is termination.Termination.LEAKED
        claim = runner.build_r0_claim(result)
        assert claim.detail is not None
        assert claim.detail.startswith("leaked-process-tree: pid=")
        assert "comm=" in claim.detail and "argv=" in claim.detail
        assert "--secret <redacted>" in claim.detail
        assert "s3cr3t" not in claim.detail

        event = runner._command_finished_event(result, phase="baseline")
        (leaked,) = [
            item for item in event["leaked_descendants"] if "cmdline" in item
        ]
        assert leaked["cmdline"][-2:] == ["--secret", "<redacted>"]

        stream = io.StringIO()
        runner._announce_r0_pressure(result, diagnostics=stream)
        assert "leaked process outlived the command: pid=" in stream.getvalue()
        assert "s3cr3t" not in stream.getvalue()
    finally:
        if pid_file.is_file() and pid_file.read_text():
            try:
                os.kill(int(pid_file.read_text()), signal.SIGKILL)
            except ProcessLookupError:
                pass


def test_clean_run_has_no_leak_diagnostics(tmp_path: Path) -> None:
    result = runner.execute_command(
        make_lane(argv=("/bin/sh", "-c", "exit 0")),
        cwd=tmp_path,
        process_runner=ProcessTreeProcessRunner(),
    )
    assert "leaked_descendants" not in runner._command_finished_event(result, phase="baseline")
    stream = io.StringIO()
    runner._announce_r0_pressure(result, diagnostics=stream)
    assert stream.getvalue() == ""
    assert runner._leaked_descendants(result) == ()


def test_malformed_evidence_yields_no_descendants() -> None:
    result = runner.CommandResult(
        plan=None,  # type: ignore[arg-type]
        outcome=Outcome.ERROR,
        reason_code=None,
        returncode=None,
        started="2026-01-01T00:00:00+00:00",
        ended="2026-01-01T00:00:00+00:00",
        process_tree_evidence={"outliving_descendants": 1},
    )
    assert runner._leaked_descendants(result) == ()


def test_read_cmdline_of_vanished_process_is_none() -> None:
    assert process_tree._read_cmdline(2**22 + 12345) is None


def test_zombie_descendant_describes_without_argv() -> None:
    """The case this diagnostic exists for: ppid and comm known, argv empty (a zombie)."""
    assert ProcessTreeDescendant(pid=5, ppid=1, comm="sleep").describe() == (
        "pid=5 ppid=1 comm=sleep"
    )
    assert ProcessTreeDescendant(pid=5, ppid=1, comm="sleep", cmdline=()).describe() == (
        "pid=5 ppid=1 comm=sleep"
    )


def _argv(*items: str) -> bytes:
    return b"\0".join(item.encode() for item in items) + b"\0"


@pytest.mark.parametrize(
    ("argv", "expected", "absent"),
    [
        (("curl", "-H", "Authorization: Bearer abc123", "x"), ("curl", "-H", "<redacted>", "x"), "abc123"),
        (("curl", "Authorization: Bearer abc123"), ("curl", "Authorization: <redacted>"), "abc123"),
        (("curl", "-u", "user:pw", "x"), ("curl", "-u", "<redacted>", "x"), "user:pw"),
        (("curl", "--header=X-Api-Key: abcdef"), ("curl", "--header=X-Api-Key: <redacted>"), "abcdef"),
        (("mysql", "-pSECRET"), ("mysql", "-p<redacted>"), "SECRET"),
        (("mysql", "-p", "SECRET"), ("mysql", "-p", "<redacted>"), "SECRET"),
        (("tool", "--password=hunter2"), ("tool", "--password=<redacted>"), "hunter2"),
        (("tool", "--token", "T0K"), ("tool", "--token", "<redacted>"), "T0K"),
        (("git", "clone", "https://user:tok@host/x.git"), ("git", "clone", "https://<redacted>@host/x.git"), "tok"),
        (("git", "clone", "https://ghp_xxx@github.com/a/b"), ("git", "clone", "https://<redacted>@github.com/a/b"), "ghp_xxx"),
        (("x", "Bearer zzz"), ("x", "Bearer <redacted>"), "zzz"),
        (("sh", "-c", "export GH_TOKEN=abc; curl http://h"), ("sh", "-c", "export GH_TOKEN=<redacted>...<redacted shell string>"), "abc"),
        (("sh", "-c", "curl -H 'X-Api-Key: abcdef' http://h"), ("sh", "-c", "curl -H 'X-Api-Key: <redacted>...<redacted shell string>"), "abcdef"),
        (("sh", "-c", "sleep 5"), ("sh", "-c", "sleep 5"), None),
        (("tool", "--env", "A=1"), ("tool", "--env", "<redacted>"), "A=1"),
        (("sleep", "5"), ("sleep", "5"), None),
    ],
)
def test_sanitize_cmdline_redacts_common_secret_carriers(argv, expected, absent) -> None:
    out = sanitize_cmdline(_argv(*argv))
    assert out == expected
    if absent is not None:
        assert absent not in " ".join(out)


def test_progress_event_caps_the_leaked_descendant_list(tmp_path: Path) -> None:
    many = tuple(
        ProcessTreeDescendant(pid=10 + index, ppid=1, comm="sleep") for index in range(25)
    )
    evidence = ProcessTreeEvidence(
        leader_status="exited",
        group_signalled_before_reap=False,
        outliving_descendants=25,
        descendants_signalled=25,
        descendants=many,
        clean=True,
    ).to_dict()
    result = runner.CommandResult(
        plan=None,  # type: ignore[arg-type]
        outcome=Outcome.ERROR,
        reason_code=None,
        returncode=None,
        started="2026-01-01T00:00:00+00:00",
        ended="2026-01-01T00:00:00+00:00",
        process_tree_evidence=evidence,
    )
    event = runner._command_finished_event(result, phase="baseline")
    assert len(event["leaked_descendants"]) == 20
    assert event["leaked_descendants_total"] == 25
