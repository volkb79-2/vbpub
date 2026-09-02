"""A-404 (a) — the ``go.mod`` ``module`` directive parser, and only it.

Every accepted and refused shape below is the behaviour of the toolchain that
produces the profiles this join has to match: ``go1.25.14``'s own vendored
``golang.org/x/mod/modfile``, read out of ``tester-unified-go:local`` at
``/usr/local/go/src/cmd/vendor/golang.org/x/mod/modfile/{read.go,rule.go}``
and transcribed into ``go_modfile.py``'s module docstring. These tests need no
toolchain themselves: their subject is assay's reader, not Go — the same split
``go_stmtpos._read_document``'s own tests already draw (A-334 forbids proving a
claim ABOUT Go with a double, not testing assay's own parsing).
"""

from __future__ import annotations

from pathlib import Path

import pytest

from assay.adapters.go_modfile import (
    ModuleDeclaration,
    find_module_declaration,
    parse_module_directive,
)
from assay.errors import AssayError, Outcome, ReasonCode


def parse(text: str) -> str:
    return parse_module_directive(text, source="go.mod")


# --- the accepted forms ------------------------------------------------------


def test_the_bare_form_is_the_ordinary_one():
    assert parse("module example.com/foo\n\ngo 1.25\n") == "example.com/foo"


def test_the_double_quoted_form_is_unquoted():
    """``rule.go``'s ``parseString`` runs ``strconv.Unquote`` over a
    ``"``-prefixed argument, so the quotes are syntax and not part of the
    path."""
    assert parse('module "example.com/foo"\ngo 1.25\n') == "example.com/foo"


def test_an_end_of_line_comment_is_not_part_of_the_module_path():
    """``read.go``'s identifier scan breaks on ``in.peekPrefix("//")``. A
    parser that split on whitespace alone would keep ``//`` and produce a
    prefix matching nothing."""
    assert parse("module example.com/foo // vanity import\n") == "example.com/foo"


def test_a_whole_line_comment_before_the_directive_is_skipped():
    text = "// SPDX-License-Identifier: MIT\n// module example.com/decoy\n\nmodule example.com/real\n"
    assert parse(text) == "example.com/real"


def test_the_factored_block_form_is_accepted():
    """``rule.go`` lists ``module`` among the verbs that may be written as a
    ``LineBlock``, and ``f.add`` then requires exactly one argument."""
    assert parse("module (\n\texample.com/foo\n)\n\ngo 1.25\n") == "example.com/foo"


def test_the_module_path_keeps_every_internal_slash_and_dot():
    assert parse("module github.com/org/repo/v2\n") == "github.com/org/repo/v2"


def test_a_trailing_slash_is_dropped_so_the_boundary_strip_is_exact():
    """``normalize_coverage_key`` fires on ``module_path + "/"``; a path that
    already ended in one would make that ``foo//``, which matches nothing."""
    assert parse('module "example.com/foo/"\n') == "example.com/foo"


def test_a_later_directive_beginning_a_line_inside_a_block_is_not_the_module():
    """A ``require ( ... )`` block's lines also begin with a path token. Only
    a verb position at paren depth zero counts."""
    text = "require (\n\tmodule v1.2.3\n)\n\nmodule example.com/real\n"
    assert parse(text) == "example.com/real"


def test_the_go_and_toolchain_directives_are_skipped_not_understood():
    text = "go 1.25\ntoolchain go1.25.14\n\nmodule example.com/foo\n"
    assert parse(text) == "example.com/foo"


# --- the refusals ------------------------------------------------------------


def _refusal(text: str) -> AssayError:
    with pytest.raises(AssayError) as excinfo:
        parse(text)
    error = excinfo.value
    assert error.outcome is Outcome.ERROR
    assert error.reason_code is ReasonCode.BAD_LANE_CONFIG
    return error


def test_a_file_with_no_module_directive_refuses():
    error = _refusal("go 1.25\n\nrequire example.com/dep v1.0.0\n")
    assert "no `module` directive" in str(error)


def test_a_module_directive_with_no_argument_refuses():
    error = _refusal("module\n")
    assert "carries no module path" in str(error)


def test_an_empty_module_path_refuses_rather_than_stripping_nothing():
    """The whole defect B057 measured is an empty prefix silently stripping
    nothing, so the one value that must never be derived is ``""``."""
    error = _refusal('module ""\n')
    assert "empty module path" in str(error)


def test_a_block_comment_refuses_because_a_go_mod_may_not_contain_one():
    error = _refusal("/* a header */\nmodule example.com/foo\n")
    assert "// comments" in str(error)


def test_a_block_comment_inside_an_identifier_refuses_too():
    """``read.go`` raises the same error from INSIDE its identifier scan, not
    only between tokens."""
    error = _refusal("module example.com/foo/*x*/\n")
    assert "// comments" in str(error)


def test_a_backquoted_argument_refuses_because_cmd_go_refuses_it():
    """``parseString`` only unquotes a ``"``-prefixed token and errors on any
    other token containing a quote character, so a backquoted module path is
    not valid input to ``cmd/go`` either."""
    error = _refusal("module `example.com/foo`\n")
    assert "backquoted" in str(error)


def test_an_unknown_escape_refuses_rather_than_being_decoded():
    error = _refusal('module "example.com/\\nfoo"\n')
    assert "refused rather than decoded" in str(error)


def test_a_known_escape_is_decoded():
    assert parse('module "example.com/a\\\\b"\n') == "example.com/a\\b"


def test_an_unterminated_string_refuses():
    error = _refusal('module "example.com/foo\n')
    assert "newline inside a string literal" in str(error)


def test_an_unclosed_factored_block_refuses():
    error = _refusal("module (\n\texample.com/foo\n\texample.com/bar\n)\n")
    assert "exactly one" in str(error)


# --- the search ---------------------------------------------------------------


def _write(root: Path, relative: str, text: str) -> None:
    path = root / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def test_the_nearest_go_mod_at_or_above_the_project_root_wins(tmp_path: Path):
    _write(tmp_path, "go.mod", "module example.com/outer\n")
    _write(tmp_path, "svc/go.mod", "module example.com/inner\n")
    (tmp_path / "svc" / "internal").mkdir()

    assert find_module_declaration(tmp_path, tmp_path / "svc") == ModuleDeclaration(
        module_path="example.com/inner", module_file="svc/go.mod"
    )


def test_an_ancestor_go_mod_is_found_when_the_project_root_has_none(tmp_path: Path):
    """A monorepo lane whose ``cwd`` is the module root's own parent-relative
    subdirectory still belongs to that module."""
    _write(tmp_path, "svc/go.mod", "module example.com/inner\n")
    (tmp_path / "svc" / "internal" / "calc").mkdir(parents=True)

    found = find_module_declaration(tmp_path, tmp_path / "svc" / "internal" / "calc")

    assert found == ModuleDeclaration(
        module_path="example.com/inner", module_file="svc/go.mod"
    )


def test_no_go_mod_anywhere_in_range_refuses_and_names_what_it_looked_for(
    tmp_path: Path,
):
    (tmp_path / "svc").mkdir()

    with pytest.raises(AssayError) as excinfo:
        find_module_declaration(tmp_path, tmp_path / "svc")

    error = excinfo.value
    assert error.outcome is Outcome.ERROR
    assert error.reason_code is ReasonCode.BAD_LANE_CONFIG
    assert "svc/go.mod" in str(error)
    assert "go.mod" in str(error)
    assert 'judge.language = "go"' in str(error)


def test_a_go_mod_above_the_repository_top_is_out_of_range(tmp_path: Path):
    """The search stops at *repo_top*: a ``go.mod`` outside the repository
    under judgment is not part of the tree the verdict is about."""
    _write(tmp_path, "go.mod", "module example.com/outside\n")
    repo = tmp_path / "repo"
    (repo / "svc").mkdir(parents=True)

    with pytest.raises(AssayError, match="no go.mod exists at or above"):
        find_module_declaration(repo, repo / "svc")


def test_a_project_root_outside_the_repository_refuses(tmp_path: Path):
    repo = tmp_path / "repo"
    repo.mkdir()
    other = tmp_path / "other"
    other.mkdir()

    with pytest.raises(AssayError, match="is not contained by its own repository"):
        find_module_declaration(repo, other)
