"""Harness shared by the reviewed CLI cases: the daemon and host they run against and the way an invocation is executed.

An invocation is the argv recorded in docs/cli-review.toml. Two tokens are substituted before it runs: ``{SOCK}`` (the
fake daemon's socket, contract 2) and ``{TMP}`` (a fresh scratch directory that is also the working directory and
``$HOME``). ``$CGPROFILE_RUN_DIR`` points at ``{TMP}/run`` (an existing, empty run directory) so ``mark`` has one.
The report stack and the direct-mode runner are stand-ins (pandas is absent in some environments; ``run`` needs
cgroupfs), exactly as in test_cgpctl.py.
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
from cgprofile.cli import cgpctl
from cli_extended import CliIdentity

import test_cgpctl

CATALOG = pathlib.Path(__file__).resolve().parents[1] / "docs" / "cli-review.toml"
IDENT = CliIdentity("cgpctl", "2.0.0", "cgprofile control client", "cgpctl")


def _stage_modules():
    """Stand-ins for the report stack, installed in sys.modules and on the package."""
    import cgprofile
    analyze = types.ModuleType("cgprofile.analyze")
    analyze.build = lambda run: "analysis"
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


def execute(argv):
    """Run one recorded invocation; returns (exit code, stdout, stderr)."""
    out, err = io.StringIO(), io.StringIO()
    with tempfile.TemporaryDirectory(prefix="cgc") as tmp, contextlib.ExitStack() as stack:
        tmp = os.path.realpath(tmp)
        os.mkdir(os.path.join(tmp, "run"))
        daemon = _fakectl.FakeDaemon(os.path.join(tmp, "c.sock"), test_cgpctl.handlers())
        stack.callback(daemon.close)
        stack.enter_context(mock.patch.dict(os.environ, {"HOME": tmp, "CGPROFILE_RUN_DIR": os.path.join(tmp, "run")}))
        os.environ.pop("CGPCTL_SOCKET", None)
        modules, package = _stage_modules()
        stack.enter_context(mock.patch.dict(sys.modules, {f"cgprofile.{n}": m for n, m in modules.items()}))
        for name, module in modules.items():
            stack.enter_context(mock.patch.object(package, name, module, create=True))
        stack.enter_context(mock.patch.object(access, "have_host_cgroup_view", lambda: True))
        stack.enter_context(mock.patch.object(direct, "run_command", lambda command, **kw: direct.Outcome(
            os.path.join(kw["out_dir"], "run-1"), 0, 1.5)))
        stack.enter_context(mock.patch.object(cgpctl, "resolve_container", lambda name: "c" * 64))
        # doctor reads host state (observer directory, cgprofile.slice): point both at nothing so it is host-independent
        stack.enter_context(mock.patch.object(cgpctl, "OBSERVER_DIR", os.path.join(tmp, "no-observer")))
        stack.enter_context(mock.patch.object(cgpctl, "SLICE_DIR", os.path.join(tmp, "no-slice")))
        previous = os.getcwd()
        os.chdir(tmp)
        stack.callback(os.chdir, previous)
        code = cgpctl.build_cli(IDENT).run(argv=_substitute(argv, daemon.path, tmp), stdout=out, stderr=err)
    return code, out.getvalue(), err.getvalue()


def load_cases():
    doc = tomllib.loads(CATALOG.read_text())
    return [c for c in doc["cases"] if c.get("state") == "active"]


def slug(case_id: str) -> str:
    stem = case_id.removeprefix("case:route:entrypoint:cgpctl/")
    return stem.replace("option:route:entrypoint:cgpctl/", "").replace("argument:route:entrypoint:cgpctl/", "").replace("/", ".")
