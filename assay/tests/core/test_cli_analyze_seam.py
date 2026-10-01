"""The judge's one seam to the analysis package (A-478, CD18).

``assay analyze`` is forwarded, unparsed, to ``assay_analysis.cli.main`` by
``assay.cli._run_analyze``; every other command works without the package.
"""

from __future__ import annotations

import io
import sys
import types

import pytest
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


def test_the_judge_needs_no_analysis_package_except_for_analyze(monkeypatch):
    monkeypatch.setitem(sys.modules, "assay_analysis", None)
    monkeypatch.setitem(sys.modules, "assay_analysis.cli", None)
    monkeypatch.chdir(PROJECT_ROOT)

    out, err = io.StringIO(), io.StringIO()
    assert main(["lanes"], stdout=out, stderr=err) == 0
    assert "tester-unified" in out.getvalue()

    with pytest.raises(ModuleNotFoundError):
        main(["analyze", "report"], stdout=io.StringIO(), stderr=io.StringIO())
