"""O1 (SQL half), A-242's own sentence -- ``SqlAdapter.for_project``,
``.has_executable_code``, ``.normalize_coverage_key``, ``.statement_spans``,
``.inject_import_break`` and ``.inject_uncovered_line`` all raise
``NotImplementedError`` naming SQL's ``R0,R2``-only reachability, rather than
implementing a capability the CLI can never ask a SQL lane for
(``RegistryEntry.rigor == {"R2"}``, wired in W6 -- outside this package's own
scope, but the reachability FACT these raises are named after).

``for_project`` joined the list with A-404, and it is the one member whose
protocol-level default is ``return self`` rather than absence. Taking that
default here would put a line in this file that no SQL lane could ever
execute: A-404 places the single call at the top of ``runner.evaluate_r1``,
which is precisely where ``normalize_coverage_key`` is reached from, and SQL
does not go there. Raising is what makes a future caller who routes SQL
through R1 find out.

Each is asserted by a DIRECT call (never a bare ``...`` Protocol-stub body):
``coverage.py`` auto-excludes an ellipsis-only function body from a branch
report, and this project's own coverage gate reads that exclusion as a
pragma dodge (the ``ciu`` Protocol-stub trap) -- so every one of these five
methods has a real, executed ``raise`` statement, and this file is what
proves it fires.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from assay.adapters.sql import SqlAdapter

ADAPTER = SqlAdapter()


def test_for_project_raises_not_implemented_naming_r2_only_reachability():
    with pytest.raises(NotImplementedError, match="for_project"):
        ADAPTER.for_project(repo_top=Path("/repo"), project_root=Path("/repo"))


def test_has_executable_code_raises_not_implemented_naming_r2_only_reachability():
    with pytest.raises(NotImplementedError, match="has_executable_code"):
        ADAPTER.has_executable_code("infra/db-init/init-scripts/20-create-corpora.sql", "x")


def test_normalize_coverage_key_raises_not_implemented_naming_r2_only_reachability():
    with pytest.raises(NotImplementedError, match="normalize_coverage_key"):
        ADAPTER.normalize_coverage_key("some/key")


def test_statement_spans_raises_not_implemented_naming_r2_only_reachability():
    with pytest.raises(NotImplementedError, match="statement_spans"):
        ADAPTER.statement_spans("CREATE TABLE t (a INT);")


def test_inject_import_break_raises_not_implemented_naming_r2_only_reachability():
    with pytest.raises(NotImplementedError, match="inject_import_break"):
        ADAPTER.inject_import_break("CREATE TABLE t (a INT);")


def test_inject_uncovered_line_raises_not_implemented_naming_r2_only_reachability():
    with pytest.raises(NotImplementedError, match="inject_uncovered_line"):
        ADAPTER.inject_uncovered_line("CREATE TABLE t (a INT);")


def test_every_raise_names_the_r2_only_rigor_fact_not_just_the_method():
    """Not merely "raises" -- each message states WHY: SQL's registered
    rigor is R2-only, so the CLI never reaches any of these six."""
    for call in (
        lambda: ADAPTER.for_project(
            repo_top=Path("/repo"), project_root=Path("/repo")
        ),
        lambda: ADAPTER.has_executable_code("x.sql", "x"),
        lambda: ADAPTER.normalize_coverage_key("x"),
        lambda: ADAPTER.statement_spans("x"),
        lambda: ADAPTER.inject_import_break("x"),
        lambda: ADAPTER.inject_uncovered_line("x"),
    ):
        with pytest.raises(NotImplementedError) as excinfo:
            call()
        assert "R2" in str(excinfo.value)
