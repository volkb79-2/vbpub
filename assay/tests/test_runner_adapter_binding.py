"""A-404's core seam: ``evaluate_r1`` binds the adapter to the project ONCE,
before anything reads the profile, and everything downstream uses what came
back.

The failure this file exists to catch is not "``for_project`` was not called"
— that would be caught by any test of the Go adapter. It is the subtler one
the ruling warns about: the core calling it and then continuing to use the
UNBOUND adapter for some of the work. B057's whole cost was two spellings of
one path drifting apart, so a binding that reaches the statement oracle but
not the key join (or the other mode) would recreate it inside the fix.

The adapter here is synthetic on purpose. Whether a Go project's ``go.mod``
says what assay thinks it says is proven against the real toolchain
(``tests/qualification/test_go_r1_real.py``, A-334); what is proven HERE is
that the core hands over its two anchors and then judges by the object it got
back, which is language-free and true of every adapter.
"""

from __future__ import annotations

from dataclasses import dataclass, field, replace
from pathlib import Path
from types import MappingProxyType

import pytest
from conftest import GitRepo, make_lane, make_r1_judge

from assay import runner
from assay.coverage_parsers.model import CoverageProfile, FileCoverage
from assay.errors import AssayError, Outcome, ReasonCode

#: The prefix the UNBOUND adapter knows nothing about and the BOUND one
#: strips. Every assertion below turns on which of the two did the work.
_PREFIX = "example.invalid/harness"


@dataclass(frozen=True, kw_only=True)
class _BindingAdapter:
    """An adapter that learns its key prefix from the project, the way
    ``GoAdapter`` learns its module path from ``go.mod``."""

    name: str = "bindy"
    source_globs: tuple[str, ...] = ("*.bnd",)
    excluded_dir_names: frozenset[str] = frozenset()
    requires_span_attribution: bool = False
    requires_statement_attribution: bool = False
    external_tools: tuple[str, ...] = ()
    #: Empty until bound, exactly as ``GoAdapter.module_path`` is.
    key_prefix: str = ""
    #: Every ``(repo_top, project_root)`` this adapter was bound with, so a
    #: test can assert it happened ONCE and with the right anchors.
    bindings: list[tuple[Path, Path]] = field(default_factory=list)
    refuse_binding: bool = False

    def for_project(self, *, repo_top: Path, project_root: Path) -> "_BindingAdapter":
        self.bindings.append((repo_top, project_root))
        if self.refuse_binding:
            raise AssayError(
                "this project is not one this adapter can bind to",
                outcome=Outcome.ERROR,
                reason_code=ReasonCode.BAD_LANE_CONFIG,
            )
        return replace(self, key_prefix=_PREFIX)

    def is_test_path(self, rel_path: str) -> bool:
        return False

    def has_executable_code(self, rel_path: str, text: str) -> bool:
        return True

    def normalize_coverage_key(self, key: str) -> str:
        prefix = self.key_prefix
        if prefix and key.startswith(prefix + "/"):
            return key[len(prefix) + 1 :]
        return key

    def statement_spans(self, text: str):
        return None

    def statement_blocks(self, repo_top, rel_paths, *, remaining=None):
        return None


def _profile() -> CoverageProfile:
    """Keyed the way a Go cover profile is: by import path, not by the
    spelling ``git diff`` uses."""
    return CoverageProfile(
        files=MappingProxyType(
            {
                f"{_PREFIX}/pkg/mod.bnd": FileCoverage(
                    executed=frozenset({2, 3}),
                    missing=frozenset(),
                    excluded=None,
                    branches=None,
                )
            }
        )
    )


def _seed(git_repo: GitRepo) -> None:
    git_repo.write("pkg/mod.bnd", "".join(f"line {n}\n" for n in range(1, 6)))
    git_repo.commit_all("seed the source the evaluator reads")


def _whole_target_lane(git_repo: GitRepo):
    judge = make_r1_judge(
        source_root_paths=(git_repo.path / "pkg",),
        mode="whole_target",
        targets=("pkg/mod.bnd",),
        base=None,
    )
    return make_lane(rigor=("R0", "R1"), judge=judge)


def test_the_bound_adapter_is_what_resolves_the_profiles_keys(git_repo: GitRepo):
    """The headline. The profile names ``example.invalid/harness/pkg/mod.bnd``
    and the tree has ``pkg/mod.bnd``; only the BOUND adapter knows the prefix,
    so a PASS here is proof the core judged with what ``for_project``
    returned. With the unbound one the key resolves to a file that does not
    exist, under no source root — which is B057, exactly."""
    _seed(git_repo)
    adapter = _BindingAdapter()

    claim = runner.evaluate_r1(
        _whole_target_lane(git_repo),
        repo=git_repo.path,
        project_root=git_repo.path,
        base=None,
        adapter=adapter,
        profile=_profile(),
    )

    assert claim.status is Outcome.PASS, claim.reason_code
    assert claim.coverage is not None
    assert claim.coverage.executable == 2


def test_the_unbound_adapter_alone_cannot_resolve_the_same_profile(
    git_repo: GitRepo,
):
    """The vacuity guard for the test above: if the prefix were irrelevant —
    if the key happened to resolve either way — the PASS would prove nothing
    about which adapter did the work."""
    _seed(git_repo)
    unbound = _BindingAdapter()

    assert (
        unbound.normalize_coverage_key(f"{_PREFIX}/pkg/mod.bnd")
        == f"{_PREFIX}/pkg/mod.bnd"
    )
    assert not (git_repo.path / _PREFIX / "pkg" / "mod.bnd").exists()


def test_the_adapter_is_bound_exactly_once_and_with_the_lanes_own_anchors(
    git_repo: GitRepo,
):
    """Once, because a second binding is a second chance to derive something
    different; and with ``repo_top``/``project_root`` — the two anchors the
    core already holds — because an adapter resolving a root of its own is the
    ambient-discovery trust A-173 removed."""
    _seed(git_repo)
    adapter = _BindingAdapter()

    runner.evaluate_r1(
        _whole_target_lane(git_repo),
        repo=git_repo.path,
        project_root=git_repo.path,
        base=None,
        adapter=adapter,
        profile=_profile(),
    )

    assert adapter.bindings == [(git_repo.path, git_repo.path)]


def test_the_changed_lines_mode_binds_the_same_way(git_repo: GitRepo):
    """Both modes judge the same profile through the same join (A-385), so a
    binding applied on one path only is the drift this seam exists to
    prevent."""
    _seed(git_repo)
    base = git_repo.head()
    git_repo.write("pkg/mod.bnd", "".join(f"line {n}\n" for n in range(1, 7)))
    git_repo.commit_all("add one line")
    adapter = _BindingAdapter()
    judge = make_r1_judge(source_root_paths=(git_repo.path / "pkg",), base=base)

    claim = runner.evaluate_r1(
        make_lane(rigor=("R0", "R1"), judge=judge),
        repo=git_repo.path,
        project_root=git_repo.path,
        base=base,
        adapter=adapter,
        profile=CoverageProfile(
            files=MappingProxyType(
                {
                    f"{_PREFIX}/pkg/mod.bnd": FileCoverage(
                        executed=frozenset({6}),
                        missing=frozenset(),
                        excluded=None,
                        branches=None,
                    )
                }
            )
        ),
    )

    assert claim.status is Outcome.PASS, claim.reason_code
    assert claim.coverage is not None
    assert claim.coverage.executable == 1
    assert adapter.bindings == [(git_repo.path, git_repo.path)]


def test_a_refused_binding_becomes_a_complete_payload_free_r1_claim(
    git_repo: GitRepo,
):
    """``for_project`` raising is an EXPECTED failure, so ``evaluate_r1``'s
    own ``except AssayError`` must render it as a claim carrying that cause —
    not let it escape as a crash the lane reports as a tooling error with no
    reason_code. This is the path a Go lane whose ``cwd`` is in no Go module
    takes."""
    _seed(git_repo)
    adapter = _BindingAdapter(refuse_binding=True)

    claim = runner.evaluate_r1(
        _whole_target_lane(git_repo),
        repo=git_repo.path,
        project_root=git_repo.path,
        base=None,
        adapter=adapter,
        profile=_profile(),
    )

    assert claim.rigor == "R1"
    assert claim.status is Outcome.ERROR
    assert claim.reason_code is ReasonCode.BAD_LANE_CONFIG
    assert claim.coverage is None
