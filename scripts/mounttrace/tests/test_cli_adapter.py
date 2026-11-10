"""Black-box and boundary checks for the cli-extended adapter."""

from __future__ import annotations

import ast
import io
import json
from pathlib import Path
import sys
import tomllib

import pytest
from cli_extended import assert_cli_contract, make_invoker

import mounttrace_core
from mounttrace_cli.cli import build_cli, identity

from cli_cases import IDENTITY, fake_tables


PROJECT = Path(__file__).resolve().parents[1]
SCRIPT = PROJECT / "mounttrace.py"
SRC = PROJECT / "src"
LIBRARY_SRC = PROJECT.parents[1] / "libraries" / "cli-extended" / "src"


def run(*argv, read_tables=None, one_pass=None):
    core = mounttrace_core
    stdout, stderr = io.StringIO(), io.StringIO()
    original_read = core.read_tables
    original_pass = core.one_pass
    if read_tables is not None:
        core.read_tables = read_tables
    if one_pass is not None:
        core.one_pass = one_pass
    try:
        code = build_cli(IDENTITY, core_module=core).run(
            argv=list(argv), stdout=stdout, stderr=stderr
        )
    finally:
        core.read_tables = original_read
        core.one_pass = original_pass
    return code, stdout.getvalue(), stderr.getvalue()


def test_real_executable_obeys_cli_contract(tmp_path):
    invoke = make_invoker(
        SCRIPT,
        home=tmp_path / "home",
        cwd=PROJECT,
        pythonpath=(SRC, LIBRARY_SRC),
        scrub_prefixes=("MOUNTTRACE_",),
    )
    assert_cli_contract(
        invoke,
        identity(),
        ("explain", "compare", "audit", "predict", "soulmask"),
        invalid_invocations={
            "unknown verb": ["unknown"],
            "missing explain path": ["explain"],
            "invalid watch": ["--watch", "0", "explain", "/"],
        },
        known_verb_errors={
            "missing explain path": "explain",
            "invalid watch": "explain",
        },
    )


def test_identity_resolves_the_wheel_version_file():
    resolved = identity()
    assert resolved.name == "MOUNTTRACE"
    assert resolved.version == "3.2.0"
    assert resolved.command_name == "mounttrace"


def test_json_preserves_the_compact_wire_format():
    reports = []
    original = mounttrace_core.one_pass

    def capture(args, tables):
        report = original(args, tables)
        reports.append(report)
        return report

    code, stdout, stderr = run(
        "explain", "/", "--no-probe", "--json",
        read_tables=lambda _args: fake_tables(),
        one_pass=capture,
    )
    assert (code, stderr) == (0, "")
    expected = json.dumps(
        reports[0].as_dict(), ensure_ascii=True, separators=(",", ":")
    ) + "\n"
    assert stdout == expected


def test_json_watch_flushes_the_first_report_before_sleeping():
    class BufferedStdout:
        def __init__(self):
            self.pending = ""
            self.visible = ""

        def write(self, text):
            self.pending += text

        def flush(self):
            self.visible += self.pending
            self.pending = ""

        def isatty(self):
            return False

    stdout = BufferedStdout()
    seen = []
    core = mounttrace_core
    original_read = core.read_tables
    core.read_tables = lambda _args: fake_tables()
    try:
        def interrupt(_seconds):
            seen.append(stdout.visible)
            raise KeyboardInterrupt

        with pytest.MonkeyPatch.context() as monkeypatch:
            monkeypatch.setattr(core.time, "sleep", interrupt)
            code = build_cli(IDENTITY, core_module=core).run(
                argv=["--json", "--watch", "0.1", "explain", "/", "--no-probe"],
                stdout=stdout,
                stderr=io.StringIO(),
            )
    finally:
        core.read_tables = original_read
    assert code == 0
    assert seen and seen[0].startswith('{"schema":1')
    assert seen[0].endswith("\n")


def test_exit_status_contract_zero_three_one_and_two():
    assert run("explain", "/", "--no-probe", read_tables=lambda _a: fake_tables())[0] == 0

    alert = mounttrace_core.Report("explain", "host /", {"status": "ALERT", "text": "alert"})
    alert.finding("ALERT", "TEST", "alert")
    assert run("explain", "/", "--no-probe", one_pass=lambda _a, _t: alert)[0] == 3

    code, stdout, stderr = run(
        "explain", "/", "--no-probe",
        read_tables=lambda _args: (_ for _ in ()).throw(PermissionError(13, "denied")),
    )
    assert (code, stdout) == (1, "")
    assert stderr.startswith("[ERROR]")
    assert "permission denied" in stderr
    assert "Traceback" not in stderr

    code, stdout, stderr = run("explain")
    assert (code, stdout) == (2, "")
    assert stderr.startswith("[ERROR]")


@pytest.mark.parametrize(
    ("argv", "message"),
    (
        (("explain", "relative"), "expected /absolute/path"),
        (("compare", "/", "relative"), "expected /absolute/path"),
        (("audit", "42:/", "42:/"), "audit PREFIX must be an absolute host path"),
        (("audit", "/", "/"), "audit PID:PREFIX must name a PID and absolute path"),
        (("predict", "relative", "42"), "PATH must be absolute"),
        (("predict", "/", "0"), "PID values must be positive"),
        (("predict", "/", "not-a-pid"), "invalid PID value"),
        (("soulmask", "0"), "PID values must be positive"),
        (("soulmask", "42", "--volumes-root", "relative"), "PATH must be absolute"),
        (("--watch", "0", "explain", "/"), "--watch must be a finite positive number"),
        (("explain", "/", "--watch", "nan"), "--watch must be a finite positive number"),
        (("explain", "/", "--watch", "not-a-number"), "invalid watch interval"),
    ),
)
def test_parser_guards_refuse_invalid_values_before_analysis(argv, message):
    def forbidden(_args):
        raise AssertionError("invalid invocation reached analysis")

    code, stdout, stderr = run(*argv, read_tables=forbidden)
    assert (code, stdout) == (2, "")
    assert stderr.startswith("[ERROR]")
    assert message in stderr
    assert "Traceback" not in stderr


def test_unexpected_exceptions_use_the_library_boundary_and_traceback_escape():
    def broken(_args):
        raise AttributeError("adapter bug")

    code, stdout, stderr = run(
        "explain", "/", read_tables=broken
    )
    assert (code, stdout) == (1, "")
    assert stderr.startswith("[ERROR] unexpected AttributeError: adapter bug")
    assert "Traceback" not in stderr

    with pytest.raises(AttributeError, match="adapter bug"):
        run("explain", "/", "--traceback", read_tables=broken)


def test_verbose_compatibility_uses_library_debug():
    baseline = run("explain", "/", "--no-probe", read_tables=lambda _a: fake_tables())
    short = run("explain", "/", "--no-probe", "-v", read_tables=lambda _a: fake_tables())
    long = run("explain", "/", "--no-probe", "--verbose", read_tables=lambda _a: fake_tables())
    debug = run("explain", "/", "--no-probe", "--debug", read_tables=lambda _a: fake_tables())
    assert short == long == debug
    assert short[0] == 0
    assert short[1] != baseline[1]


@pytest.mark.parametrize(
    "argv",
    (["--help"], ["help"], ["help", "explain"], ["version"], ["--version"]),
)
def test_discovery_never_reads_mounts(argv):
    def forbidden(_args):
        raise AssertionError("discovery called read_tables")

    code, _stdout, stderr = run(*argv, read_tables=forbidden)
    assert code == 0
    assert stderr == ""


def test_core_imports_only_the_standard_library_and_never_starts_processes():
    tree = ast.parse((SRC / "mounttrace_core.py").read_text(encoding="utf-8"))
    roots = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            roots.update(alias.name.split(".", 1)[0] for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
            roots.add(node.module.split(".", 1)[0])
    third_party = roots - set(sys.stdlib_module_names) - {"__future__"}
    assert not third_party, third_party
    assert "subprocess" not in roots
    assert "argparse" not in roots
    assert "cli_extended" not in roots
    assert "mounttrace_cli" not in roots


def test_wheel_declares_the_released_cli_extended_floor():
    project = tomllib.loads((PROJECT / "pyproject.toml").read_text(encoding="utf-8"))
    assert project["project"]["dependencies"] == ["cli-extended>=0.6.0"]
    assert project["project"]["scripts"]["mounttrace"] == "mounttrace_cli.cli:main"
    assert project["project"]["version"] == "3.2.0"
    assert (SRC / "mounttrace_cli" / "VERSION").read_text(encoding="utf-8").strip() == "3.2.0"
