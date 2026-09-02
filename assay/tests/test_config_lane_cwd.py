"""B043 — the lane-level ``cwd``: the command's working directory as a
DECLARED, recorded fact, refused at load when it cannot be one.

Before this key the lane key set was closed with no working-directory key and
every command ran at the snapshot's project root, so a monorepo app's package
script needed ``argv = ["bash", "-c", "cd applications/x && ..."]``. That
wrapper is not a cosmetic problem: it hides the real ``argv[0]`` from the
``MISSING_EXTERNAL_TOOL`` preflight (which then checks ``bash``), it makes
``allow_argv_append`` meaningless for the inner command, and it puts shell
quoting between the lane file and what ran — which ``argv_effective`` then
records opaquely.

This module owns the LOAD half of the contract. The execution half (the lane
command, every R2 candidate and every R3 canary running in the same resolved
directory) is ``test_runner_lane_cwd.py``'s, and the two are deliberately
separate: a spelling this loader accepts and a directory the snapshot does
not hold are different facts, discovered at different times, and only the
second can name the commit.
"""

from __future__ import annotations

import pytest
from conftest import R1_LANE, Project, lane_table

from assay.config import load_lane_file
from assay.errors import LaneConfigError, Outcome, ReasonCode


def _load(project: Project, text: str):
    return load_lane_file(project.write(text))


def _with_cwd(text: str, value: str) -> str:
    """Insert a ``cwd = <value>`` line into the lane table.

    Written by hand rather than through ``set_key``: ``cwd`` is a NEW key, so
    there is no line for ``set_key`` to rewrite.
    """
    return text.replace(
        "allow_argv_append = true\n",
        f"allow_argv_append = true\ncwd = {value}\n",
        1,
    )


def _refusal(project: Project, text: str) -> LaneConfigError:
    with pytest.raises(LaneConfigError) as excinfo:
        _load(project, text)
    assert excinfo.value.outcome is Outcome.ERROR
    assert excinfo.value.reason_code is ReasonCode.BAD_LANE_CONFIG
    return excinfo.value


# --------------------------------------------------------------------------
# Accepted
# --------------------------------------------------------------------------


def test_a_declared_cwd_is_recorded_verbatim(project: Project):
    project.dir("applications", "webapp-ui-react")
    text = _with_cwd(R1_LANE, '"applications/webapp-ui-react"')
    lane = _load(project, text).lane("package")
    assert lane.cwd == "applications/webapp-ui-react"


def test_an_omitted_cwd_is_none_never_a_dot(project: Project):
    """``None`` is ABSENT, and absence is what the verdict records.

    ``"."`` would be a value the file never wrote, and a consumer reading it
    back could not tell a lane that chose the project root from one that said
    nothing — which is exactly the collapse ``cwd_declared``'s own absence
    rule exists to prevent.
    """
    lane = _load(project, R1_LANE).lane("package")
    assert lane.cwd is None


def test_the_declared_lane_round_trips_through_as_declared(project: Project):
    """The round-trip oracle: ``as_declared()`` must equal ``tomllib``'s own
    parse, so a key assay invented shows up as an inequality."""
    project.dir("applications", "webapp-ui-react")
    text = _with_cwd(R1_LANE, '"applications/webapp-ui-react"')
    lane = _load(project, text).lane("package")
    assert lane.as_declared() == lane_table(text)


def test_an_omitted_cwd_emits_no_key_at_all(project: Project):
    lane = _load(project, R1_LANE).lane("package")
    assert "cwd" not in lane.as_declared()


def test_a_nested_cwd_is_accepted(project: Project):
    project.dir("a", "b", "c")
    lane = _load(project, _with_cwd(R1_LANE, '"a/b/c"')).lane("package")
    assert lane.cwd == "a/b/c"


# --------------------------------------------------------------------------
# Refused — one test per refusal, as the contract's own acceptance box asks
# --------------------------------------------------------------------------


def test_a_non_string_cwd_is_refused(project: Project):
    error = _refusal(project, _with_cwd(R1_LANE, "42"))
    assert "cwd" in str(error) and "string" in str(error)


def test_an_empty_cwd_is_refused(project: Project):
    error = _refusal(project, _with_cwd(R1_LANE, '""'))
    assert "must not be empty" in str(error)


def test_an_absolute_cwd_is_refused(project: Project):
    error = _refusal(project, _with_cwd(R1_LANE, '"/etc"'))
    assert "must not be absolute" in str(error)


def test_a_parent_traversal_is_refused(project: Project):
    """The one refusal that is more than hygiene: a lane whose cwd escapes
    the project would run its command outside the snapshot the verdict names.
    """
    error = _refusal(project, _with_cwd(R1_LANE, '"../elsewhere"'))
    assert "invalid path component" in str(error) and ".." in str(error)


def test_an_interior_parent_traversal_is_refused(project: Project):
    project.dir("a")
    error = _refusal(project, _with_cwd(R1_LANE, '"a/../a"'))
    assert "invalid path component" in str(error)


def test_a_dot_component_is_refused(project: Project):
    project.dir("a")
    error = _refusal(project, _with_cwd(R1_LANE, '"./a"'))
    assert "invalid path component" in str(error)


def test_a_trailing_slash_is_refused(project: Project):
    project.dir("src")
    error = _refusal(project, _with_cwd(R1_LANE, '"src/"'))
    assert "invalid path component" in str(error)


def test_a_git_component_is_refused(project: Project):
    project.dir(".git", "hooks")
    error = _refusal(project, _with_cwd(R1_LANE, '".git/hooks"'))
    assert "invalid path component" in str(error) and ".git" in str(error)


def test_a_backslash_component_is_refused(project: Project):
    error = _refusal(project, _with_cwd(R1_LANE, r'"a\\b"'))
    assert "invalid path component" in str(error)


def test_a_missing_directory_is_refused(project: Project):
    error = _refusal(project, _with_cwd(R1_LANE, '"applications/nope"'))
    assert "is not a directory in the checkout" in str(error)


def test_a_file_is_refused_as_a_cwd(project: Project):
    project.file("Makefile", "all:\n")
    error = _refusal(project, _with_cwd(R1_LANE, '"Makefile"'))
    assert "is not a directory in the checkout" in str(error)


def test_a_symlink_escaping_the_project_is_refused(project: Project, tmp_path):
    """The grammar above cannot see a symlink: ``outside`` has no ``..`` in
    it. Containment after symlink collapse is the complementary check, and
    this is the case that proves the two are not redundant."""
    outside = tmp_path / "outside-the-project"
    outside.mkdir()
    (project.root / "escape").symlink_to(outside)
    error = _refusal(project, _with_cwd(R1_LANE, '"escape"'))
    assert "not contained beneath the project root" in str(error)


def test_a_symlink_inside_the_project_is_accepted(project: Project):
    """The containment check refuses ESCAPE, not symlinks as such — a symlink
    that lands back inside the project is contained, and refusing it would be
    a rule the contract does not state."""
    project.dir("applications", "webapp-ui-react")
    (project.root / "app").symlink_to(project.root / "applications")
    lane = _load(project, _with_cwd(R1_LANE, '"app/webapp-ui-react"')).lane("package")
    assert lane.cwd == "app/webapp-ui-react"


def test_the_refusal_names_the_offending_key(project: Project):
    error = _refusal(project, _with_cwd(R1_LANE, '"applications/nope"'))
    assert "'cwd'" in str(error)


# --------------------------------------------------------------------------
# cwd x isolation.link_paths (fix round 1) — the PAIR
#
# Each key was individually valid and individually checked, and together they
# composed into a snapshot escape: `link_paths` planted a symlink at
# `<snapshot>/deps` pointing back into the invoking checkout, and the runner's
# commit-bound `cwd` check was a filesystem test that followed it and accepted
# an untracked directory as committed. The lane's command then ran in the
# consumer's REAL working tree. `test_runner_lane_cwd.py` owns the reproduction
# and the authoritative, commit-bound refusal; this section owns the early
# refusal, in the layer that can name the file being edited.
#
# The rule is one-directional, and both directions are pinned below: a cwd AT
# or BENEATH a linked path can never be satisfied by any commit (a link must be
# untracked, a cwd must be tracked, and a tracked directory's ancestors are
# tracked), while a link BENEATH the cwd is the ordinary `cwd = "app"` +
# `link_paths = ["app/node_modules"]` shape B041(b) exists for.
# --------------------------------------------------------------------------


def _with_link_paths(text: str, value: str) -> str:
    return text.replace(
        'snapshot_selection = "repository"\n',
        f'snapshot_selection = "repository"\nlink_paths = {value}\n',
        1,
    )


def test_a_cwd_that_is_also_a_declared_link_path_is_refused(project: Project):
    project.dir("deps")
    error = _refusal(
        project, _with_link_paths(_with_cwd(R1_LANE, '"deps"'), '["deps"]')
    )
    assert "'cwd'" in str(error)
    assert "link_paths" in str(error)
    assert "deps" in str(error)


def test_a_cwd_BENEATH_a_declared_link_path_is_refused(project: Project):
    """The same impossibility one level down: if `deps` is not tracked then
    `deps/pkg` cannot be a tracked directory either, so naming it as a cwd is
    wrong for exactly the same reason and must not need its own escape."""
    project.dir("deps", "pkg")
    error = _refusal(
        project, _with_link_paths(_with_cwd(R1_LANE, '"deps/pkg"'), '["deps"]')
    )
    assert "'deps'" in str(error)


def test_a_link_path_BENEATH_the_cwd_is_accepted(project: Project):
    """The legitimate composition, and the reason the check is not symmetric.
    `cwd = "app"` with `link_paths = ["app/node_modules"]` is the exact shape
    B041(b) was built for: a committed application directory to run in, with
    an uncommitted dependency closure linked in beside its sources."""
    project.dir("app")
    lane = _load(
        project,
        _with_link_paths(_with_cwd(R1_LANE, '"app"'), '["app/node_modules"]'),
    ).lane("package")
    assert lane.cwd == "app"
    assert lane.isolation is not None
    assert lane.isolation.link_paths == ("app/node_modules",)


def test_an_unrelated_link_path_leaves_the_cwd_alone(project: Project):
    """The control for the two refusals above: `link_paths` and `cwd` naming
    disjoint paths is not an interaction at all, and a check that refused it
    would be refusing the feature rather than the escape."""
    project.dir("app")
    lane = _load(
        project, _with_link_paths(_with_cwd(R1_LANE, '"app"'), '["node_modules"]')
    ).lane("package")
    assert lane.cwd == "app"
    assert lane.isolation is not None
    assert lane.isolation.link_paths == ("node_modules",)


def test_a_link_path_that_merely_shares_a_name_prefix_is_not_an_ancestor(
    project: Project,
):
    """Component-wise, never string-prefix: `deps-extra` is not `deps`, and a
    `startswith` implementation would refuse a lane with nothing wrong."""
    project.dir("deps-extra")
    lane = _load(
        project, _with_link_paths(_with_cwd(R1_LANE, '"deps-extra"'), '["deps"]')
    ).lane("package")
    assert lane.cwd == "deps-extra"
