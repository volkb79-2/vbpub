"""Harness shared by the reviewed CLI cases: the daemon and host they run against and the way an invocation is executed.

An invocation is the argv recorded in the CLI's review catalog (docs/cli-review-cgprofilectl.toml for the socket client,
docs/cli-review-cgprofile.toml for the end-user client). Two tokens are substituted before it runs: ``{SOCK}`` (the fake
daemon's socket, contract 2) and ``{TMP}`` (a fresh scratch directory that is also the working directory and ``$HOME``).
``$CGPROFILE_RUN_DIR`` points at ``{TMP}/run`` (an existing, empty run directory) so ``mark`` has one.
The report stack and the direct-mode runner are stand-ins (pandas is absent in some environments; ``run`` needs
cgroupfs), exactly as in test_cgprofile.py.
"""
from __future__ import annotations

import contextlib
import io
import os
import pathlib
import sys
import tempfile
import tomllib
import types
from unittest import mock

import _fakectl
from cgprofile import access, direct
from cgprofile.cli import cgprofile as client
from cgprofile.cli import cgprofilectl
from cli_extended import CliIdentity

import test_cgprofilectl

DOCS = pathlib.Path(__file__).resolve().parents[1] / "docs"
CTL_CATALOG = DOCS / "cli-review-cgprofilectl.toml"
CLIENT_CATALOG = DOCS / "cli-review-cgprofile.toml"
CTL_IDENT = CliIdentity("cgprofilectl", "2.0.0", "cgprofile control client", "cgprofilectl")
CLIENT_IDENT = CliIdentity("cgprofile", "2.0.0", "cgprofile end-user client", "cgprofile")


def _stage_modules():
    """Stand-ins for the report stack, installed in sys.modules and on the package."""
    import cgprofile
    analyze = types.ModuleType("cgprofile.analyze")
    analyze.build = lambda run: types.SimpleNamespace(proposals=[])
    html = types.ModuleType("cgprofile.report_html")
    md = types.ModuleType("cgprofile.report_md")

    def write(path):
        with open(path, "w") as handle:
            handle.write("x")
        return path

    html.render = lambda analysis, path: write(path)
    md.render = lambda analysis, out_dir: write(os.path.join(out_dir, "report.md"))
    return {"analyze": analyze, "report_html": html, "report_md": md}, cgprofile


def _substitute(argv, sock, tmp):
    return [token.replace("{SOCK}", sock).replace("{TMP}", tmp) for token in argv]


def _execute(build, ident, argv):
    out, err = io.StringIO(), io.StringIO()
    with tempfile.TemporaryDirectory(prefix="cgc") as tmp, contextlib.ExitStack() as stack:
        tmp = os.path.realpath(tmp)
        os.mkdir(os.path.join(tmp, "run"))
        daemon = _fakectl.FakeDaemon(os.path.join(tmp, "c.sock"), test_cgprofilectl.handlers())
        stack.callback(daemon.close)
        stack.enter_context(mock.patch.dict(os.environ, {"HOME": tmp, "CGPROFILE_RUN_DIR": os.path.join(tmp, "run")}))
        os.environ["CGPROFILE_CONTROL_SOCKET"] = daemon.path
        os.environ["CGPROFILE_OBSERVER_SOCKET"] = "/tmp/cgprofile-observer.sock"
        modules, package = _stage_modules()
        stack.enter_context(mock.patch.dict(sys.modules, {f"cgprofile.{n}": m for n, m in modules.items()}))
        for name, module in modules.items():
            stack.enter_context(mock.patch.object(package, name, module, create=True))
        stack.enter_context(mock.patch.object(access, "have_host_cgroup_view", lambda: True))
        stack.enter_context(mock.patch.object(direct, "run_command", lambda command, **kw: direct.Outcome(
            os.path.join(kw["out_dir"], "run-1"), 0, 1.5)))
        stack.enter_context(mock.patch.object(cgprofilectl, "resolve_container", lambda name: "c" * 64))
        # doctor reads host state (CGPROFILE_OBSERVER_SOCKET, cgprofile.slice): keep it host-independent
        stack.enter_context(mock.patch.object(cgprofilectl, "SLICE_DIR", os.path.join(tmp, "no-slice")))
        previous = os.getcwd()
        os.chdir(tmp)
        stack.callback(os.chdir, previous)
        code = build(ident).run(argv=_substitute(argv, daemon.path, tmp), stdout=out, stderr=err)
    return code, out.getvalue(), err.getvalue()


def execute(argv):
    """Run one recorded ``cgprofilectl`` invocation; returns (exit code, stdout, stderr)."""
    return _execute(cgprofilectl.build_cli, CTL_IDENT, argv)


def execute_client(argv):
    """Run one recorded ``cgprofile`` invocation; returns (exit code, stdout, stderr)."""
    return _execute(client.build_cli, CLIENT_IDENT, argv)


def load_cases(catalog=CTL_CATALOG):
    doc = tomllib.loads(pathlib.Path(catalog).read_text())
    return [c for c in doc["cases"] if c.get("state") == "active"]


def slug(case_id: str, cli: str = "cgprofilectl") -> str:
    stem = case_id.removeprefix(f"case:route:entrypoint:{cli}/")
    prefix = f"route:entrypoint:{cli}/"
    return stem.replace(f"option:{prefix}", "").replace(f"argument:{prefix}", "").replace("/", ".")
