import copy

import pytest

from conftest import GOLDEN, PROJECT_COMMITS, project_git
from rigor_record import derive
from rigor_record.check import load_records
from rigor_record.gitfacts import GitError
from rigor_record.render import storage_order

C1, C2, C3, C4, C5, C6, C7 = PROJECT_COMMITS


@pytest.fixture(scope="module")
def records():
    loaded, problems = load_records(GOLDEN / "project", (), ())
    assert problems == []
    return storage_order(loaded)


@pytest.fixture
def at(records):
    return derive.targets_at_factory(records, project_git())


def result_of(records, commit, lane):
    record = next(r for r in records if r["commit"] == commit)
    return next(r for r in record["results"] if r["lane"] == lane)


def test_a_result_is_valid_only_at_identical_targets(records):
    result = result_of(records, C2, "assay-r1")
    here = dict(result["targets"])
    assert derive.valid_at(result, here)
    assert not derive.valid_at(result, {**here, "cgprofile": "9" * 40})
    assert not derive.valid_at(result, {"cgprofile": here["cgprofile"]})  # a declared input is missing
    assert not derive.valid_at(result, {**here, "node_modules": None})


def test_a_result_without_targets_is_never_valid():
    assert not derive.valid_at({"targets": {}}, {"anything": "x"})


def test_targets_at_reads_git_once_per_commit_and_maps_errors_to_none(records):
    calls = []

    class Counting:
        def tree(self, commit, path):
            calls.append((commit, path))
            if commit == C1:
                raise GitError("gone")
            return "5" * 40

    at = derive.targets_at_factory(records, Counting())
    assert at(C1) == {"cgprofile": None, "node_modules": None}
    assert at(C2) == {"cgprofile": "5" * 40, "node_modules": "5" * 40}
    before = len(calls)
    at(C2)
    assert len(calls) == before


def test_worst_and_latest_label(records):
    assert derive.worst(["pass", "survivors", "pass-with-waivers"]) == "survivors"
    assert derive.worst([]) is None
    assert derive.latest_label(records) == "cgprofile-v2.1.0"
    assert derive.latest_label([]) is None


def test_the_latest_label_ignores_withdrawn_ones(records):
    changed = copy.deepcopy(records)
    for record in changed:
        for label in record["labels"]:
            if label["tag"] == "cgprofile-v2.1.0":
                label["state"] = "withdrawn"
    assert derive.latest_label(changed) == "cgprofile-v2.0.0"


def test_level_summary_of_a_fully_judged_release(records, at):
    assert derive.level_summary(records, C4, at) == {
        "review": "accept", "R0": "pass", "R1": "pass", "R3": "pass", "R2": "1/1 survivors",
    }


def test_level_summary_of_a_release_without_r2_evidence(records, at):
    assert derive.level_summary(records, C1, at)["R2"] == derive.NOT_JUDGED


def test_an_unlabelled_commit_holds_only_what_it_was_judged_for(records, at):
    summary = derive.level_summary(records, C5, at)
    assert summary == {"review": "missing", "R0": derive.NOT_JUDGED, "R1": derive.NOT_JUDGED, "R3": derive.NOT_JUDGED, "R2": "1/1 pass"}


def test_a_command_lane_holds_r0_only(records, at):
    held = derive.held_results(records, C2, at, "R0")
    assert held["r0-r1"]["kind"] == "command"
    for level in ("R1", "R2", "R3"):
        assert "r0-r1" not in derive.held_results(records, C2, at, level)
    changed = copy.deepcopy(records)
    command = result_of(changed, C2, "r0-r1")
    command["levels"] = {"R0": "pass", "R1": "pass", "R3": "pass"}  # even a command that runs `assay run`
    only_commands = derive.level_summary([r for r in changed if r["commit"] == C2], C2, at)
    assert only_commands["R1"] == "pass" and only_commands["R3"] == "pass"  # from the assay lanes, never the command
    assert "r0-r1" not in derive.held_results(changed, C2, at, "R1")


def test_r1_is_not_judged_without_an_assay_claim(records, at):
    changed = copy.deepcopy(records)
    for record in changed:
        for result in record["results"]:
            if result["kind"] == "assay":
                result["levels"] = {"R0": "pass"}
    summary = derive.level_summary(changed, C2, at)
    assert summary["R1"] == summary["R3"] == derive.NOT_JUDGED


def test_a_result_does_not_apply_at_a_commit_with_other_targets(records):
    moved = {C2: {"cgprofile": "9" * 40, "node_modules": "9" * 40}}
    git = project_git()
    real = derive.targets_at_factory(records, git)
    summary = derive.level_summary(records, C2, lambda commit: moved.get(commit) or real(commit))
    assert summary["R0"] == summary["R1"] == summary["R3"] == derive.NOT_JUDGED
    assert summary["R2"] == "superseded"  # no valid result left on an older label


def test_r0_needs_every_member_of_the_sequence(records, at):
    changed = copy.deepcopy(records)
    record = next(r for r in changed if r["commit"] == C2)
    record["results"] = [r for r in record["results"] if r["lane"] != "assay-r3"]
    assert derive.level_summary(changed, C2, at)["R0"] == derive.NOT_JUDGED


def test_r0_without_a_sequence_result_is_not_judged(records, at):
    changed = copy.deepcopy(records)
    for record in changed:
        record["results"] = [r for r in record["results"] if r["kind"] != "sequence"]
    assert derive.level_summary(changed, C2, at)["R0"] == derive.NOT_JUDGED


def test_r0_of_an_empty_sequence_is_not_judged(records, at):
    changed = copy.deepcopy(records)
    for record in changed:  # the newest sequence in storage order decides
        for r in record["results"]:
            if r["kind"] == "sequence":
                r["members"] = []
    assert derive.level_summary(changed, C2, at)["R0"] == derive.NOT_JUDGED


def test_a_failed_sequence_is_never_reported_as_pass(records, at):
    changed = copy.deepcopy(records)
    for record in changed:
        for r in record["results"]:
            if r["kind"] == "sequence":
                r["outcome"] = "FAIL"
    assert derive.level_summary(changed, C2, at)["R0"] == "fail"


def test_a_failed_member_is_reported_as_fail(records, at):
    changed = copy.deepcopy(records)
    for record in changed:
        for r in record["results"]:
            if r["kind"] == "sequence":
                r["outcome"] = "FAIL"
    result_of(changed, C2, "assay-r1")["levels"]["R0"] = "fail"
    assert derive.level_summary(changed, C2, at)["R0"] == "fail"


def test_r2_is_pending_on_the_latest_label_without_a_valid_result(records, at):
    changed = copy.deepcopy(records)
    record = next(r for r in changed if r["commit"] == C4)
    record["results"] = [r for r in record["results"] if r["lane"] != "assay-r2"]
    assert derive.level_summary(changed, C4, at)["R2"] == "pending"


def test_r2_is_running_only_when_a_notes_provider_says_so(records, at):
    changed = copy.deepcopy(records)
    record = next(r for r in changed if r["commit"] == C4)
    record["results"] = [r for r in record["results"] if r["lane"] != "assay-r2"]
    asked = []

    def notes(commit, lane):
        asked.append((commit, lane))
        return True

    assert derive.level_summary(changed, C4, at, notes)["R2"] == "running"
    assert asked == [(C4, "assay-r2")]
    assert derive.level_summary(changed, C4, at, lambda commit, lane: False)["R2"] == "pending"


def test_r2_is_superseded_on_an_older_label(records, at):
    changed = copy.deepcopy(records)
    record = next(r for r in changed if r["commit"] == C3)
    record["results"] = [r for r in record["results"] if r["lane"] != "assay-r2"]
    assert derive.level_summary(changed, C3, at)["R2"] == "superseded"


def test_r2_of_an_unlabelled_commit_without_a_result_is_not_judged(records, at):
    changed = copy.deepcopy(records)
    record = next(r for r in changed if r["commit"] == C5)
    record["results"] = []
    assert derive.level_summary(changed, C5, at)["R2"] == derive.NOT_JUDGED


def test_a_commit_without_a_record_of_its_own_reads_the_newest_inventory(records, at):
    summary = derive.level_summary(records, "8" * 40, lambda commit: {})
    assert summary["review"] == "missing"
    assert summary["R2"] == derive.NOT_JUDGED  # nothing valid at that commit


def test_an_empty_store_judges_nothing():
    summary = derive.level_summary([], C1, lambda commit: {})
    assert summary == {
        "review": "missing", "R0": derive.NOT_JUDGED, "R1": derive.NOT_JUDGED, "R3": derive.NOT_JUDGED, "R2": derive.NOT_JUDGED,
    }


def test_the_newest_record_wins_per_lane(records, at):
    changed = copy.deepcopy(records)
    # the same targets at C5 and C7: an older result at 6666.. is superseded by a later record's result
    older = result_of(changed, C6, "assay-r2")
    newer = copy.deepcopy(older)
    newer["levels"]["R2"] = "survivors"
    extra = copy.deepcopy(next(r for r in changed if r["commit"] == C6))
    extra["commit"] = C7
    extra["results"] = [newer]
    both = changed + [extra]
    assert derive.held_results(both, C6, at, "R2")["assay-r2"]["levels"]["R2"] == "survivors"


def test_r0_uses_the_commits_own_sequence_not_the_newest_records(records, at):
    """H2: a later release adding a member lane must not flip an earlier release to not-judged."""
    before = derive.level_summary(records, C2, at)["R0"]
    assert before == "pass"
    changed = copy.deepcopy(records)
    for record in changed:
        if record["commit"] != C2:
            for r in record["results"]:
                if r["kind"] == "sequence":
                    r["members"] = r["members"] + ["a-new-lane"]
    assert derive.level_summary(changed, C2, at)["R0"] == before


def test_an_unlabelled_commit_without_its_own_sequence_has_no_r0(records, at):
    assert derive.level_summary(records, C5, at)["R0"] == derive.NOT_JUDGED


def test_a_result_with_unresolved_inputs_is_valid_only_at_its_own_commit(records):
    result = copy.deepcopy(result_of(records, C2, "assay-r1"))
    result["unresolved_inputs"] = ["node_modules"]
    here = dict(result["targets"])
    assert not derive.valid_at(result, here)
    assert derive.valid_at(result, here, own_commit=True)
    changed = copy.deepcopy(records)
    result_of(changed, C2, "assay-r1")["unresolved_inputs"] = ["node_modules"]
    same = lambda commit: here  # noqa: E731 - every commit shows identical targets
    own = derive.valid_results(changed, C2, same)
    other = derive.valid_results(changed, C3, same)
    assert any(r["lane"] == "assay-r1" and rec["commit"] == C2 for rec, r in own)
    assert not any(r["lane"] == "assay-r1" and r.get("unresolved_inputs") for _rec, r in other)