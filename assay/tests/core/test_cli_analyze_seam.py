"""The judge's one seam to the analysis package (A-478, CD18).

``assay analyze`` is forwarded, unparsed, to ``assay_analysis.cli.main`` by
``assay.cli._run_analyze``. Both it and ``assay capabilities`` require the
package; every other command works without it.
"""

from __future__ import annotations

import io
import sys
import types

from conftest import PROJECT_ROOT

from assay.cli import main


def test_analyze_forwards_argv_and_streams_to_the_analysis_package(monkeypatch):
    calls = []

    def fake_main(argv, *, stdout, stderr):
        calls.append((list(argv), stdout, stderr))
        return 42

    package = types.ModuleType("assay_analysis")
    module = types.ModuleType("assay_analysis.cli")
    module.main = fake_main
    package.cli = module
    monkeypatch.setitem(sys.modules, "assay_analysis", package)
    monkeypatch.setitem(sys.modules, "assay_analysis.cli", module)
    out, err = io.StringIO(), io.StringIO()

    code = main(["analyze", "report", "--", "x"], stdout=out, stderr=err)

    assert code == 42
    assert calls == [(["report", "--", "x"], out, err)]


def test_non_analysis_commands_work_without_the_package(monkeypatch):
    monkeypatch.setitem(sys.modules, "assay_analysis", None)
    monkeypatch.setitem(sys.modules, "assay_analysis.cli", None)
    monkeypatch.chdir(PROJECT_ROOT)

    out, err = io.StringIO(), io.StringIO()
    assert main(["lanes"], stdout=out, stderr=err) == 0
    assert "tester-unified" in out.getvalue()


def test_analyze_reports_a_missing_analysis_package(monkeypatch):
    monkeypatch.setitem(sys.modules, "assay_analysis", None)
    monkeypatch.setitem(sys.modules, "assay_analysis.cli", None)

    out, err = io.StringIO(), io.StringIO()
    code = main(["analyze", "report"], stdout=out, stderr=err)

    assert code == 2
    assert out.getvalue() == ""
    assert len(err.getvalue().splitlines()) == 1
    assert "ERROR/EXEC_FAILED" in err.getvalue()
    assert "assay_analysis" in err.getvalue()


def test_capabilities_requires_the_analysis_package(monkeypatch):
    monkeypatch.setitem(sys.modules, "assay_analysis", None)
    monkeypatch.setitem(sys.modules, "assay_analysis.cli", None)

    out, err = io.StringIO(), io.StringIO()
    code = main(["capabilities", "--json"], stdout=out, stderr=err)

    assert code == 2
    assert out.getvalue() == ""
    assert len(err.getvalue().splitlines()) == 1
    assert "ERROR/EXEC_FAILED" in err.getvalue()
    assert "assay_analysis" in err.getvalue()
