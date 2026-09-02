"""A-404 (DA-8) — ``GoAdapter.for_project`` derives the lane's module path
from the project's own ``go.mod``, and a key outside that module refuses.

The defect this closes is **B057**, measured end to end inside
``tester-unified-go:local``: ``cli._built_in_registry`` builds ``GoAdapter()``
with ``module_path = ""``, so every profile key kept its import-path prefix,
resolved to a file that does not exist, and refused with a message about the
profile and the tree being different revisions — naming staleness for what was
actually an unstripped prefix.

Nothing here needs a Go toolchain: the subject is where assay gets the module
path and what it does with a key that is not under it, both of which are
assay's own logic over committed text. The real ``go test`` half is
``tests/qualification/test_go_r1_real.py`` (A-334).
"""

from __future__ import annotations

from pathlib import Path

import pytest

from assay.adapters.go import GoAdapter
from assay.errors import AssayError, Outcome, ReasonCode


def _module(root: Path, relative: str, module_path: str) -> None:
    path = root / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(f"module {module_path}\n\ngo 1.25\n", encoding="utf-8")


# --- deriving -----------------------------------------------------------------


def test_the_module_path_is_read_from_the_projects_own_go_mod(tmp_path: Path):
    _module(tmp_path, "go.mod", "example.com/harness")

    bound = GoAdapter().for_project(repo_top=tmp_path, project_root=tmp_path)

    assert bound.module_path == "example.com/harness"
    assert bound.module_file == "go.mod"


def test_a_monorepo_lane_picks_its_own_module_not_an_ancestors(tmp_path: Path):
    """B043's ``cwd``: the lane's working directory is the module root, and a
    repository holding several modules must not hand it the outermost one."""
    _module(tmp_path, "go.mod", "example.com/outer")
    _module(tmp_path, "srdm/go.mod", "srdm")

    bound = GoAdapter().for_project(
        repo_top=tmp_path, project_root=tmp_path / "srdm"
    )

    assert bound.module_path == "srdm"
    assert bound.module_file == "srdm/go.mod"


def test_binding_returns_a_new_adapter_and_never_mutates_the_registrys_own(
    tmp_path: Path,
):
    """``cli._built_in_registry`` holds ONE ``GoAdapter`` for the process, so
    a binding that mutated in place would leak one lane's module path into the
    next lane judged in the same run."""
    _module(tmp_path, "go.mod", "example.com/harness")
    registry_instance = GoAdapter()

    bound = registry_instance.for_project(
        repo_top=tmp_path, project_root=tmp_path
    )

    assert bound is not registry_instance
    assert registry_instance.module_path == ""
    assert registry_instance.module_file == ""


def test_every_other_declaration_survives_the_binding(tmp_path: Path):
    """The bound adapter is still the shipped adapter: a copy that quietly
    dropped ``requires_statement_attribution`` would disarm A-392's guard."""
    _module(tmp_path, "go.mod", "example.com/harness")

    bound = GoAdapter().for_project(repo_top=tmp_path, project_root=tmp_path)

    assert type(bound) is GoAdapter
    assert bound.requires_statement_attribution is True
    assert bound.external_tools == ("go",)
    assert bound.source_globs == ("*.go",)


# --- refusing to bind ---------------------------------------------------------


def test_a_lane_whose_cwd_is_in_no_go_module_refuses(tmp_path: Path):
    """A-404 (b). ``BAD_LANE_CONFIG`` and not ``UNREADABLE_ARTIFACT``: the
    coverage artifact is not the thing that is wrong."""
    (tmp_path / "svc").mkdir()

    with pytest.raises(AssayError) as excinfo:
        GoAdapter().for_project(repo_top=tmp_path, project_root=tmp_path / "svc")

    error = excinfo.value
    assert error.outcome is Outcome.ERROR
    assert error.reason_code is ReasonCode.BAD_LANE_CONFIG
    assert "svc/go.mod" in str(error)


def test_a_declared_module_path_that_disagrees_with_go_mod_refuses(tmp_path: Path):
    """A-328's rule, applied: refuse precedence between two sources of one
    fact rather than picking a winner, because whichever loses is config
    nothing reads."""
    _module(tmp_path, "go.mod", "example.com/harness")

    with pytest.raises(AssayError) as excinfo:
        GoAdapter(module_path="example.com/other").for_project(
            repo_top=tmp_path, project_root=tmp_path
        )

    error = excinfo.value
    assert error.reason_code is ReasonCode.BAD_LANE_CONFIG
    assert "example.com/other" in str(error)
    assert "example.com/harness" in str(error)
    assert "go.mod" in str(error)


def test_a_declared_module_path_that_agrees_is_simply_re_derived(tmp_path: Path):
    _module(tmp_path, "go.mod", "example.com/harness")

    bound = GoAdapter(module_path="example.com/harness").for_project(
        repo_top=tmp_path, project_root=tmp_path
    )

    assert bound.module_path == "example.com/harness"
    assert bound.module_file == "go.mod"


# --- what a bound adapter then does with a key --------------------------------


def test_a_bound_adapter_strips_its_own_modules_prefix(tmp_path: Path):
    _module(tmp_path, "go.mod", "example.invalid/harness")
    bound = GoAdapter().for_project(repo_top=tmp_path, project_root=tmp_path)

    assert (
        bound.normalize_coverage_key("example.invalid/harness/internal/calc/calc.go")
        == "internal/calc/calc.go"
    )


def test_a_key_outside_the_derived_module_refuses_and_names_all_three_facts(
    tmp_path: Path,
):
    """A-404 (c), and it REPLACES B057's misattributed message. The three
    things a consumer needs are the key, the module path assay derived, and
    the file it derived it from — the last one because their next action is to
    open it."""
    _module(tmp_path, "srdm/go.mod", "srdm")
    bound = GoAdapter().for_project(
        repo_top=tmp_path, project_root=tmp_path / "srdm"
    )

    with pytest.raises(AssayError) as excinfo:
        bound.normalize_coverage_key("example.com/elsewhere/internal/x.go")

    error = excinfo.value
    assert error.outcome is Outcome.ERROR
    assert error.reason_code is ReasonCode.UNREADABLE_ARTIFACT
    message = str(error)
    assert "example.com/elsewhere/internal/x.go" in message
    assert "'srdm'" in message
    assert "srdm/go.mod" in message
    assert "revision" not in message, (
        "the message this replaces blamed staleness; naming the wrong cause "
        "is the half of B057 that is a FIX rather than a keep"
    )


def test_a_sibling_module_sharing_the_prefixs_characters_refuses_when_derived(
    tmp_path: Path,
):
    """Boundary safety is preserved and then sharpened: ``srdm_legacy/...`` is
    still never mis-stripped into ``_legacy/...``, and on a DERIVED adapter it
    is now refused instead of passed through to become a path matching no
    file."""
    _module(tmp_path, "go.mod", "srdm")
    bound = GoAdapter().for_project(repo_top=tmp_path, project_root=tmp_path)

    with pytest.raises(AssayError, match="not under this lane's own Go module"):
        bound.normalize_coverage_key("srdm_legacy/internal/x.go")


def test_a_declared_module_path_keeps_the_pass_through(tmp_path: Path):
    """The library affordance (``tests/test_standalone.py`` builds its own
    registry this way) is unchanged: with no ``go.mod`` behind it, assay has
    nothing to contradict the caller with, so a foreign key is simply not one
    it was told to strip."""
    adapter = GoAdapter(module_path="srdm")

    assert adapter.module_file == ""
    assert (
        adapter.normalize_coverage_key("srdm_legacy/internal/x.go")
        == "srdm_legacy/internal/x.go"
    )
