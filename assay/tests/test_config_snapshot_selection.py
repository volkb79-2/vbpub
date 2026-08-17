"""O1 (B006a/A-269, §3.2) — the lane schema v2 ``[isolation]`` grammar.

``config.py`` gains a frozen ``IsolationConfig(snapshot_selection,
unsafe_symlink_omissions)`` whose ``__post_init__`` enforces one closed
grammar for BOTH the TOML loader and direct construction, and ``Lane`` gains
a required, non-defaulted ``isolation`` field. This module is the acceptance
oracle for that contract (O1): a single, exhaustive closed matrix proves
every enum/requiredness/path/list bound in both directions, plus a battery of
direct-constructor differential tests for the shapes that are awkward or
impossible to spell as loadable TOML (a lone surrogate, a NUL byte inside a
basic string, a non-table ``isolation`` value).

House style, matching ``test_config_reject.py``/``test_config_rigor.py``:
every negative has an unmodified positive in the same matrix, so a loader
that rejects everything fails the ACCEPT half, and a loader that accepts
everything fails the REJECT half.

O1's own command runs exactly this module's ``test_snapshot_selection_
closed_matrix``:

    pytest -q -p no:randomly tests/test_config_snapshot_selection.py::test_snapshot_selection_closed_matrix

and the exact success marker is ``ASSAY_B006A_CONFIG=1``, printed by the
registered gate only once this test PASSES.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import PurePosixPath

import pytest
import tomllib
from conftest import Project

from assay.config import IsolationConfig, LaneConfigError, load_lane_file
from assay.errors import Outcome, ReasonCode

# --- lane text builders -------------------------------------------------------
#
# Every case shares one skeleton: a valid R0-led lane, optionally R1 (which
# pulls in a complete, valid, minimal R1 judge table so an isolation defect
# is never masked by an unrelated judge-field refusal -- `_load_lane` checks
# `judge` before `isolation`, so a broken judge table would fail for the
# WRONG reason and this matrix would prove nothing about isolation).

_R1_JUDGE = (
    "\n[lanes.package.judge]\n"
    'language = "python"\n'
    'source_roots = ["src"]\n'
    "fail_under = 100.0\n"
    "allow_excluded = false\n"
    'coverage = { format = "coverage-py-json", artifact = "cov.json" }\n'
    'base = "main"\n'
)


def _lane_text(*, rigor: str, isolation_block: str) -> str:
    judge = _R1_JUDGE if rigor != '["R0"]' else ""
    return (
        "schema_version = 2\n\n"
        "[lanes.package]\n"
        'scope = "S1"\n'
        f"rigor = {rigor}\n"
        'enforcement = "gate"\n'
        'argv = ["pytest", "-q"]\n'
        "env = {}\n"
        'env_passthrough = ["PATH"]\n'
        'budget = "5m"\n'
        "allow_argv_append = false\n"
        f"{isolation_block}"
        f"{judge}"
    )


def _repository_block() -> str:
    return '[lanes.package.isolation]\nsnapshot_selection = "repository"\n\n'


def _omission_block(paths: list[str]) -> str:
    rendered = ", ".join(f'"{p}"' for p in paths)
    return (
        "[lanes.package.isolation]\n"
        'snapshot_selection = "repository-minus-unsafe-symlinks"\n'
        f"unsafe_symlink_omissions = [{rendered}]\n\n"
    )


#: The carve's own canonical CMRU example (§3.2) -- already strictly ascending
#: by UTF-8 bytes, proven here as an accept-side control rather than assumed.
_TOPOS_THREE = [
    "topos/tests/fixtures/inspect_files/_danger/passwd_link",
    "topos/tests/fixtures/inspect_files/cgroup_escape/system.slice/ssh.service/dangerous_link/passwd_escape",
    "topos/tests/fixtures/inspect_files/cgroup_nonreg/system.slice/ssh.service/memory.current",
]
assert _TOPOS_THREE == sorted(_TOPOS_THREE, key=lambda s: s.encode("utf-8")), (
    "the carve's own example must already be strictly ascending, or this "
    "control proves nothing"
)

_SIXTY_FOUR_PATHS = [f"omission-{i:03d}" for i in range(64)]
assert _SIXTY_FOUR_PATHS == sorted(_SIXTY_FOUR_PATHS, key=lambda s: s.encode("utf-8"))

_MAX_BYTE_PATH = "x" * 4096

_DIVERSE_PATHS = ["a", "a-b_c.d", "dir/nested/leaf", "z.ext"]
assert _DIVERSE_PATHS == sorted(_DIVERSE_PATHS, key=lambda s: s.encode("utf-8"))


@dataclass(frozen=True)
class _Case:
    id: str
    rigor: str
    isolation_block: str
    expected: IsolationConfig | None  # None means REJECT
    match: str | None = None  # required when expected is a reject sentinel


_REJECT = object()  # sentinel distinguishing "accept None" from "reject"


CASES: list[_Case] = [
    # --- accept ---------------------------------------------------------------
    _Case("r0_lane_has_no_isolation", '["R0"]', "", None),
    _Case(
        "r1_lane_repository_mode",
        '["R0", "R1"]',
        _repository_block(),
        IsolationConfig(snapshot_selection="repository", unsafe_symlink_omissions=()),
    ),
    _Case(
        "r1_lane_omission_mode_single_entry",
        '["R0", "R1"]',
        _omission_block(["a/b"]),
        IsolationConfig(
            snapshot_selection="repository-minus-unsafe-symlinks",
            unsafe_symlink_omissions=("a/b",),
        ),
    ),
    _Case(
        "r1_lane_omission_mode_topos_three_paths",
        '["R0", "R1"]',
        _omission_block(_TOPOS_THREE),
        IsolationConfig(
            snapshot_selection="repository-minus-unsafe-symlinks",
            unsafe_symlink_omissions=tuple(_TOPOS_THREE),
        ),
    ),
    _Case(
        "r1_lane_omission_mode_boundary_64_entries",
        '["R0", "R1"]',
        _omission_block(_SIXTY_FOUR_PATHS),
        IsolationConfig(
            snapshot_selection="repository-minus-unsafe-symlinks",
            unsafe_symlink_omissions=tuple(_SIXTY_FOUR_PATHS),
        ),
    ),
    _Case(
        "r1_lane_omission_mode_boundary_max_byte_length",
        '["R0", "R1"]',
        _omission_block([_MAX_BYTE_PATH]),
        IsolationConfig(
            snapshot_selection="repository-minus-unsafe-symlinks",
            unsafe_symlink_omissions=(_MAX_BYTE_PATH,),
        ),
    ),
    _Case(
        "r1_lane_omission_mode_diverse_accepted_spellings",
        '["R0", "R1"]',
        _omission_block(_DIVERSE_PATHS),
        IsolationConfig(
            snapshot_selection="repository-minus-unsafe-symlinks",
            unsafe_symlink_omissions=tuple(_DIVERSE_PATHS),
        ),
    ),
    # --- reject: the R0/R1+ conditional ----------------------------------------
    _Case(
        "missing_isolation_on_r1_plus",
        '["R0", "R1"]',
        "",
        _REJECT,
        match=r"has no \[isolation\] table",
    ),
    _Case(
        "isolation_present_on_r0_only",
        '["R0"]',
        _repository_block(),
        _REJECT,
        match=r"rigor \['R0'\] is R0-only",
    ),
    # --- reject: table shape ----------------------------------------------------
    _Case(
        "unknown_isolation_key",
        '["R0", "R1"]',
        (
            "[lanes.package.isolation]\n"
            'snapshot_selection = "repository"\n'
            "extra = 1\n\n"
        ),
        _REJECT,
        match="unknown isolation key",
    ),
    _Case(
        "missing_snapshot_selection",
        '["R0", "R1"]',
        "[lanes.package.isolation]\n\n",
        _REJECT,
        match="missing required field 'isolation.snapshot_selection'",
    ),
    _Case(
        "invalid_snapshot_selection_value",
        '["R0", "R1"]',
        '[lanes.package.isolation]\nsnapshot_selection = "project"\n\n',
        _REJECT,
        match="must be one of",
    ),
    # --- reject: repository forbids the omissions key ---------------------------
    _Case(
        "repository_forbids_omissions_key",
        '["R0", "R1"]',
        (
            "[lanes.package.isolation]\n"
            'snapshot_selection = "repository"\n'
            'unsafe_symlink_omissions = ["a"]\n\n'
        ),
        _REJECT,
        match="forbidden",
    ),
    # --- reject: omission mode requires the list, bounded 1..64 -----------------
    _Case(
        "omission_mode_missing_list",
        '["R0", "R1"]',
        (
            "[lanes.package.isolation]\n"
            'snapshot_selection = "repository-minus-unsafe-symlinks"\n\n'
        ),
        _REJECT,
        match="missing required field 'isolation.unsafe_symlink_omissions'",
    ),
    _Case(
        "omission_mode_empty_list_is_refused",
        '["R0", "R1"]',
        _omission_block([]),
        _REJECT,
        match=r"1\.\.64 entries",
    ),
    _Case(
        "omission_mode_too_many_entries",
        '["R0", "R1"]',
        _omission_block([f"p{i:03d}" for i in range(65)]),
        _REJECT,
        match="got 65",
    ),
    # --- reject: per-path grammar -------------------------------------------------
    _Case(
        "omission_non_string_entry",
        '["R0", "R1"]',
        (
            "[lanes.package.isolation]\n"
            'snapshot_selection = "repository-minus-unsafe-symlinks"\n'
            "unsafe_symlink_omissions = [1]\n\n"
        ),
        _REJECT,
        match=r"unsafe_symlink_omissions\[0\] must be a string",
    ),
    _Case(
        "omission_empty_string_entry",
        '["R0", "R1"]',
        _omission_block([""]),
        _REJECT,
        match="must not be empty",
    ),
    _Case(
        "omission_leading_slash_is_absolute",
        '["R0", "R1"]',
        _omission_block(["/x"]),
        _REJECT,
        match="must not be absolute",
    ),
    _Case(
        "omission_dot_component_raw_not_normalised",
        '["R0", "R1"]',
        _omission_block(["./x"]),
        _REJECT,
        match="invalid path component",
    ),
    _Case(
        "omission_dotdot_component",
        '["R0", "R1"]',
        _omission_block(["a/../b"]),
        _REJECT,
        match="invalid path component",
    ),
    _Case(
        "omission_dotgit_component",
        '["R0", "R1"]',
        _omission_block(["a/.git/b"]),
        _REJECT,
        match="invalid path component",
    ),
    _Case(
        "omission_doubled_slash_raw_not_normalised",
        '["R0", "R1"]',
        _omission_block(["a//b"]),
        _REJECT,
        match="invalid path component",
    ),
    _Case(
        "omission_trailing_slash_raw_not_normalised",
        '["R0", "R1"]',
        _omission_block(["a/"]),
        _REJECT,
        match="invalid path component",
    ),
    _Case(
        "omission_backslash_component",
        '["R0", "R1"]',
        (
            "[lanes.package.isolation]\n"
            'snapshot_selection = "repository-minus-unsafe-symlinks"\n'
            "unsafe_symlink_omissions = ['a\\b']\n\n"
        ),
        _REJECT,
        match="contain a backslash",
    ),
    _Case(
        "omission_over_max_byte_length",
        '["R0", "R1"]',
        _omission_block(["x" * 4097]),
        _REJECT,
        match="exceeding the 4096-byte ceiling",
    ),
    # --- reject: strictly ascending, never silently sorted -----------------------
    _Case(
        "omission_duplicate_entries_not_strictly_ascending",
        '["R0", "R1"]',
        _omission_block(["a", "a"]),
        _REJECT,
        match="strictly ascending",
    ),
    _Case(
        "omission_unsorted_entries_not_silently_sorted",
        '["R0", "R1"]',
        _omission_block(["b", "a"]),
        _REJECT,
        match="strictly ascending",
    ),
]


@pytest.mark.parametrize("case", CASES, ids=[c.id for c in CASES])
def test_snapshot_selection_closed_matrix(case: _Case, project: Project):
    text = _lane_text(rigor=case.rigor, isolation_block=case.isolation_block)

    if case.expected is _REJECT:
        with pytest.raises(LaneConfigError) as excinfo:
            load_lane_file(project.write(text))
        assert case.match is not None, "every reject case must name its match"
        assert re.search(case.match, str(excinfo.value)), (
            f"expected {case.match!r} in {excinfo.value!s}"
        )
        assert excinfo.value.outcome is Outcome.ERROR
        assert excinfo.value.reason_code is ReasonCode.BAD_LANE_CONFIG
        assert excinfo.value.exit_code == 2
        return

    lane = load_lane_file(project.write(text)).lane("package")
    assert lane.isolation == case.expected

    # Independent round trip: `as_declared()` must equal `tomllib`'s own
    # parse, so an invented default or a dropped field would show up as an
    # inequality here rather than as something a reviewer has to notice.
    declared = lane.as_declared()
    raw = tomllib.loads(text)["lanes"]["package"]
    assert declared == raw

    if case.expected is None:
        assert "isolation" not in raw
        return

    # The derived-theorem control (§3.2): for every ACCEPTED omission path,
    # equality to `PurePosixPath(raw).as_posix()` holds -- proving the
    # component-level refusal mechanism above already refuses everything
    # that round-trip would have changed, without a second, unreachable
    # `as_posix()` equality branch inside `IsolationConfig` itself.
    for raw_path in case.expected.unsafe_symlink_omissions:
        assert PurePosixPath(raw_path).as_posix() == raw_path, (
            f"accepted path {raw_path!r} does not equal its own POSIX "
            f"round-trip; the grammar above should have refused it instead "
            f"of silently accepting an un-normalised spelling"
        )
    if case.expected.snapshot_selection == "repository":
        assert "unsafe_symlink_omissions" not in raw["isolation"], (
            "as_declared() must omit the forbidden key rather than "
            "serialising an empty list"
        )


# --- a defect this matrix cannot express as a table-header substitution -------


def test_isolation_that_is_not_a_table_is_rejected(project: Project):
    text = _lane_text(rigor='["R0", "R1"]', isolation_block="").replace(
        "allow_argv_append = false\n", 'allow_argv_append = false\nisolation = "nope"\n'
    )
    with pytest.raises(LaneConfigError, match="'isolation' must be a table"):
        load_lane_file(project.write(text))


def test_snapshot_selections_public_constant_is_exactly_the_closed_pair():
    from assay.config import SNAPSHOT_SELECTIONS

    assert SNAPSHOT_SELECTIONS == {"repository", "repository-minus-unsafe-symlinks"}


# --- direct construction: the shapes TOML cannot spell, or that prove the ------
# --- SAME __post_init__ mechanism runs for BOTH the loader and a direct caller


def test_direct_construction_of_a_valid_repository_policy_succeeds():
    policy = IsolationConfig(snapshot_selection="repository", unsafe_symlink_omissions=())
    assert policy.as_declared() == {"snapshot_selection": "repository"}


def test_direct_construction_of_a_valid_omission_policy_succeeds():
    policy = IsolationConfig(
        snapshot_selection="repository-minus-unsafe-symlinks",
        unsafe_symlink_omissions=("a/b", "c"),
    )
    assert policy.as_declared() == {
        "snapshot_selection": "repository-minus-unsafe-symlinks",
        "unsafe_symlink_omissions": ["a/b", "c"],
    }


def test_direct_construction_rejects_an_unknown_selection():
    with pytest.raises(LaneConfigError, match="must be one of"):
        IsolationConfig(snapshot_selection="nope", unsafe_symlink_omissions=())


def test_direct_construction_rejects_a_non_string_selection():
    with pytest.raises(LaneConfigError, match="must be one of"):
        IsolationConfig(snapshot_selection=1, unsafe_symlink_omissions=())  # type: ignore[arg-type]


def test_direct_construction_repository_forbids_a_non_empty_list():
    with pytest.raises(LaneConfigError, match="forbidden"):
        IsolationConfig(snapshot_selection="repository", unsafe_symlink_omissions=("a",))


def test_direct_construction_omission_mode_refuses_zero_entries():
    with pytest.raises(LaneConfigError, match=r"1\.\.64 entries"):
        IsolationConfig(
            snapshot_selection="repository-minus-unsafe-symlinks",
            unsafe_symlink_omissions=(),
        )


def test_direct_construction_omission_mode_refuses_65_entries():
    with pytest.raises(LaneConfigError, match="got 65"):
        IsolationConfig(
            snapshot_selection="repository-minus-unsafe-symlinks",
            unsafe_symlink_omissions=tuple(f"p{i:03d}" for i in range(65)),
        )


def test_direct_construction_rejects_a_lone_surrogate_as_unencodable():
    # TOML text cannot spell a lone surrogate (basic-string \u escapes must
    # name a valid Unicode SCALAR value, which excludes D800..DFFF, and
    # tomllib refuses the literal byte too) -- direct construction is the
    # only reachable way to prove the "require strict UTF-8 encoding" branch
    # at all, exactly as O1 says: "commit-dependent symlink kind is
    # intentionally tested only by O2" names the analogous split.
    with pytest.raises(LaneConfigError, match="cannot be encoded as strict UTF-8"):
        IsolationConfig(
            snapshot_selection="repository-minus-unsafe-symlinks",
            unsafe_symlink_omissions=("\ud800",),
        )


def test_direct_construction_rejects_a_nul_byte_component():
    with pytest.raises(LaneConfigError, match="invalid path component"):
        IsolationConfig(
            snapshot_selection="repository-minus-unsafe-symlinks",
            unsafe_symlink_omissions=("a\x00b",),
        )


def test_direct_construction_rejects_a_non_string_omission_entry():
    with pytest.raises(LaneConfigError, match="must be a string"):
        IsolationConfig(
            snapshot_selection="repository-minus-unsafe-symlinks",
            unsafe_symlink_omissions=(1,),  # type: ignore[arg-type]
        )


def test_direct_construction_names_the_index_of_the_offending_entry():
    with pytest.raises(LaneConfigError, match=r"unsafe_symlink_omissions\[1\]"):
        IsolationConfig(
            snapshot_selection="repository-minus-unsafe-symlinks",
            unsafe_symlink_omissions=("a", ""),
        )


def test_direct_construction_single_entry_needs_no_sortedness_check():
    # count == 1 never enters the pairwise-ascending loop at all (there is no
    # second element to compare against) -- covering the "for" statement's
    # zero-iteration branch, mirrored by the >=2-entry cases above covering
    # its one-or-more-iteration branch.
    policy = IsolationConfig(
        snapshot_selection="repository-minus-unsafe-symlinks",
        unsafe_symlink_omissions=("only-one",),
    )
    assert policy.unsafe_symlink_omissions == ("only-one",)
