"""The pure record transitions of ``update``: R2 from fragments, qualifications, reconcile, withdraw (commit keying)."""

import copy
import hashlib
import json

import pytest

import world
from conftest import ASSEMBLE, COMMIT, TREE, FakeGit, assemble_kwargs, fragment_doc, run_assemble
from rigor_record import canonical_json
from rigor_record.assemble import Refusal
from rigor_record.sync import plan_sync
from rigor_record.update import (
    HISTORY_LIMIT, TRANSITIONS, check_expected_revision, check_transition, closeout_of, fragment_key, label_commits, name_of,
    plan_qualification, plan_r2, plan_reconcile, plan_withdraw, qualification_key, read_waivers, record_of, sha12,
    withdraw_key,
)

TAG = "cgprofile-v2.0.0"
DIR = "cgroup-profiler"
LANE = "r2"
OTHER = "c" * 40
TREE2 = "d" * 40


def base_record():
    return run_assemble().record


def pair(commit=COMMIT, tree=TREE, edit=None):
    return world.gate_pair(commit, tree, edit=edit)


def r2(records=None, fragments=None, **kw):
    args = dict(
        records=records if records is not None else {COMMIT: base_record()},
        fragments=fragments if fragments is not None else pair(),
        attempts=2, closeout_exit=None, base=None, project_dir=DIR, git=FakeGit(),
    )
    args.update(kw)
    return plan_r2(**args)


def refused(call, fragment):
    with pytest.raises(Refusal) as info:
        call()
    assert any(fragment in p for p in info.value.problems), info.value.problems
    return info.value.problems


def lane_result(record, lane=LANE):
    return next(r for r in record["results"] if r["lane"] == lane)


def shrink(drop):
    def apply(assay):
        s = assay["r2"]["survivors"]
        s["listed"], s["total"] = s["listed"][drop:], s["total"] - drop
        assay["r2"]["buckets"]["survived"] -= drop
        assay["r2"]["buckets"]["killed"] += drop
    return apply


def grow(count, sidecar=None):
    def apply(assay):
        r = assay["r2"]
        listed = [
            {"candidate_id": f"cand-{i:04d}", "path": f"src/cgprofile/mod{i % 3}.py", "line": i + 1,
             "operator": "python:compare-swap", "first_failing_test": None}
            for i in range(min(count, 500))
        ]
        r["survivors"].update(listed=listed, total=count, sidecar_sha256=sidecar)
        r["buckets"] = {"killed": 0, "survived": count, "equivalent": 0, "hung": 0, "budget_exceeded": 0, "crashed": 0}
        r["candidates"] = {"total": count, "executed": count, "resumed": 0}
        r["worker_counts"] = {"1": count}
        r["profile_status_counts"] = {"MEASURED": 0, "PARTIAL": 0, "NO_MEASUREMENT": 0}
    return apply


def fit(exit_code, edit=None):
    """An assay-fragment edit whose campaign closes out with ``exit_code`` (the status is read from the fragment)."""
    def apply(assay):
        if edit is not None:
            edit(assay)
        if exit_code == 3:
            r = assay["r2"]
            r["candidates"]["executed"] -= 1
            r["buckets"]["killed"] -= 1
            r["worker_counts"]["1"] -= 1
            r["profile_status_counts"]["PARTIAL"] -= 1
        if exit_code == 2:
            assay["outcome"], assay["reason"] = "ERROR", "VERDICT_UNVERIFIED"
    return apply


def renamed(fragments, lane):
    """The same fragment pair under another lane name (digest reference recomputed)."""
    assay, gate = (json.loads(data) for _name, data in fragments)
    assay["lane"] = gate["lane"] = lane
    assay_bytes = canonical_json(assay).encode()
    gate["assay_fragment_sha256"] = hashlib.sha256(assay_bytes).hexdigest()
    return [(f"{lane}-assay.json", assay_bytes), (f"{lane}-gate.json", canonical_json(gate).encode())]


# -- R2 from fragments ---------------------------------------------------------------------------------------------
def test_an_r2_campaign_lands_in_the_record_of_the_commit_it_judged():
    plan = r2()
    record = plan.changed[COMMIT]
    result = lane_result(record)
    assert plan.commit == COMMIT and record["revision"] == 2
    assert result["levels"] == {"R0": "pass", "R2": "survivors"}
    assert result["scope"] == {"kind": "whole-source", "base_commit": None, "anchored": True}
    assert result["attributed_from"] is None and result["survivors"]["open"] == 31
    assert record["inventory"]["r2_lanes"] == [LANE] and [r["lane"] for r in record["results"]][-1] == "release"
    assert plan.subject == "chore(rigor): cgprofile-v2.0.0 R2 survivors"
    assert plan.trailers == (f"Rigor-Record: cgroup-profiler@{COMMIT} rev=2 fragments={plan.key}",)


def test_the_key_depends_on_the_fragments_and_the_attribution():
    assert r2().key == r2().key
    assert r2(fragments=pair(edit=shrink(1))).key != r2().key
    assert fragment_key(["a", "b"]) == fragment_key(["b", "a"]) and fragment_key(["a"], "x") != fragment_key(["a"])
    assert qualification_key("n", COMMIT, ["a"]) != qualification_key("m", COMMIT, ["a"])
    assert withdraw_key("t", "why", "r") != withdraw_key("t", "why", "s") and len(sha12("x")) == 12


@pytest.mark.parametrize("exit_code,survivors,status", [(0, 0, "pass"), (1, 3, "survivors"), (2, 0, "error"), (3, 5, "incomplete")])
def test_the_stored_status_follows_the_closeout(exit_code, survivors, status):
    edit = shrink(31 - survivors) if survivors < 31 else None
    plan = r2(fragments=pair(edit=fit(exit_code, edit)))
    result = lane_result(plan.changed[COMMIT])
    assert result["levels"]["R2"] == status and result["campaign"]["closeout_exit"] == exit_code
    assert ("survivors" in result) == (status in ("pass", "survivors"))


def test_the_closeout_is_read_from_the_fragment_and_a_disagreeing_value_refuses():
    """M2: --closeout-exit is only a cross-check; a completed campaign cannot be stored as incomplete or error."""
    assert r2(closeout_exit=1).changed[COMMIT]["results"] == r2().changed[COMMIT]["results"]
    problems = refused(lambda: r2(closeout_exit=3), "--closeout-exit 3 disagrees with the fragment, which closes out with 1")
    assert len(problems) == 1
    refused(lambda: r2(closeout_exit=2), "disagrees with the fragment")
    refused(lambda: r2(fragments=pair(edit=fit(3)), closeout_exit=1), "which closes out with 3")
    assert closeout_of({"outcome": "ERROR", "r2": {"campaign": None}}) == 2


def test_an_assay_only_r2_fragment_is_refused_until_the_run_gate_half_exists():
    """M1 (RRK-11): no run-gate half means no tool identity, machine class or perf."""
    problems = refused(lambda: r2(fragments=pair()[:1]), "has no run-gate half")
    assert len(problems) == 1


def test_an_incomplete_campaign_resumes_and_keeps_its_history():
    first = r2(fragments=pair(edit=fit(3)))
    second = r2(records=first.changed, fragments=pair(edit=shrink(31)), closeout_exit=0)
    result = lane_result(second.changed[COMMIT])
    assert result["levels"]["R2"] == "pass" and second.changed[COMMIT]["revision"] == 3
    assert [h["status"] for h in result["history"]] == ["incomplete"]
    records = second.changed
    for _ in range(HISTORY_LIMIT + 2):  # error -> error -> ... history stays bounded
        records = r2(records={COMMIT: first.changed[COMMIT]}, fragments=pair(edit=fit(2))).changed
        records = r2(records=records, fragments=pair(edit=fit(2))).changed
    assert len(lane_result(records[COMMIT])["history"]) <= HISTORY_LIMIT


def test_a_final_result_is_not_overwritten():
    refused(lambda: r2(records=r2().changed), "which is final")
    check_transition("incomplete", "pass")
    refused(lambda: check_transition("pass", "error"), "not an allowed transition")
    assert "survivors" in TRANSITIONS


def test_an_unpublished_judged_commit_refuses_unless_attributed():
    git = FakeGit(published=[])
    refused(lambda: r2(git=git), "is not published")
    problems = refused(lambda: r2(git=FakeGit(published=[COMMIT]), attribute_to=OTHER), f"--attribute-to {OTHER} is not published")
    assert problems


def test_attribution_needs_identical_targets_and_records_the_origin():
    judged = "e" * 40
    git = FakeGit(published=[OTHER], trees={judged: TREE, OTHER: TREE})
    plan = r2(records={COMMIT: base_record(), OTHER: {**base_record(), "commit": OTHER}}, fragments=pair(commit=judged), git=git, attribute_to=OTHER)
    result = lane_result(plan.changed[OTHER])
    assert result["attributed_from"] == judged and plan.commit == OTHER and "attribute-to" not in plan.subject
    assert plan.key != r2(fragments=pair(commit=judged), git=FakeGit(published=True)).key
    moved = FakeGit(published=[OTHER], trees={judged: TREE, OTHER: TREE2})
    refused(lambda: r2(fragments=pair(commit=judged), git=moved, attribute_to=OTHER), "differs at")
    gone = FakeGit(published=[OTHER], trees={judged: TREE}, missing=[OTHER])
    refused(lambda: r2(fragments=pair(commit=judged), git=gone, attribute_to=OTHER), "differs at")


class UntrackedInput(FakeGit):
    """A repository in which the declared input ``node_modules`` is not tracked."""

    def tree(self, commit, path):
        from rigor_record.gitfacts import GitError

        if path == "node_modules":
            raise GitError("untracked")
        return super().tree(commit, path)


def test_an_untracked_input_is_recorded_as_unresolved_and_blocks_attribution():
    declared = lambda a: a.update(inputs=["node_modules"])  # noqa: E731
    result = lane_result(r2(fragments=pair(edit=declared), git=UntrackedInput()).changed[COMMIT])
    assert result["unresolved_inputs"] == ["node_modules"] and result["targets"] == {DIR: TREE}
    assert "unresolved_inputs" not in lane_result(r2().changed[COMMIT])
    judged = "e" * 40
    git = UntrackedInput(published=[OTHER], trees={judged: TREE, OTHER: TREE})
    refused(lambda: r2(fragments=pair(commit=judged, edit=declared), git=git, attribute_to=OTHER), "have no git identity")


def test_an_unlabelled_commit_gets_its_own_record():
    plan = r2(records={}, fragments=pair(commit=OTHER))
    record = plan.changed[OTHER]
    assert record["labels"] == [] and record["revision"] == 1 and record["review"]["status"] == "missing"
    assert record["inventory"] == {"sequence": None, "members": [], "r2_lanes": [LANE]}
    assert plan.subject == f"chore(rigor): {OTHER[:12]} R2 survivors"
    refused(lambda: r2(records={}, fragments=pair(commit=OTHER), expect_revision=1), "has no record, expected revision 1")


def test_two_lanes_in_one_update_are_two_results_of_one_record():
    fragments = pair() + renamed(pair(), "r2b")
    plan = r2(fragments=fragments)
    record = plan.changed[COMMIT]
    assert [r["lane"] for r in record["results"] if "R2" in r.get("levels", {})] == [LANE, "r2b"]
    assert record["inventory"]["r2_lanes"] == [LANE, "r2b"] and record["revision"] == 2
    refused(lambda: r2(fragments=fragments, lane=LANE), "--lane pins")


def test_a_release_sequence_member_cannot_be_an_r2_lane():
    refused(lambda: r2(fragments=renamed(pair(), "assay-r1")), "member of the release sequence")


def test_a_fragment_set_must_make_sense():
    refused(lambda: r2(fragments=[]), "no fragments")
    refused(lambda: r2(fragments=[("x.json", b"nonsense")]), "x.json")
    release = [(n, d) for n, d in assemble_kwargs()["fragments"]]
    refused(lambda: r2(fragments=release), "")  # a release set (composite + members) belongs to assemble


def test_a_fragment_without_r2_data_or_a_passing_r0_claim_refuses():
    refused(lambda: r2(fragments=pair(edit=lambda a: a.update(r2=None))), "carries no R2 claim and campaign data")
    refused(lambda: r2(fragments=pair(edit=lambda a: a.update(claims=[c for c in a["claims"] if c["rigor"] != "R0"]))), "needs a passing R0 claim")


def test_fragments_for_another_project_or_tree_refuse():
    refused(lambda: r2(fragments=pair(edit=lambda a: a.update(project="other"))), "not other")
    refused(lambda: r2(git=FakeGit(trees={COMMIT: TREE2})), "has")
    refused(lambda: r2(git=FakeGit(missing=[COMMIT])), "is not in the repository")


def test_a_scope_base_must_be_a_clean_recorded_commit():
    first = r2(fragments=pair(edit=shrink(31)), closeout_exit=0).changed
    nxt = "f" * 40
    git = FakeGit(trees={nxt: TREE2})
    fragments = pair(commit=nxt, tree=TREE2, edit=shrink(31))
    plan = r2(records=first, fragments=fragments, git=git, base=COMMIT, base_label=TAG, closeout_exit=0)
    scope = lane_result(plan.changed[nxt])["scope"]
    assert scope == {"kind": "changed-since", "base_commit": COMMIT, "anchored": True, "base_label": TAG}
    refused(lambda: r2(records=first, fragments=fragments, git=git, base=OTHER), "has no record")
    refused(lambda: r2(records=first, fragments=fragments, git=git, base=COMMIT, base_label="x-v9.9.9"), "is not a label")
    refused(lambda: r2(records={COMMIT: base_record()}, fragments=fragments, git=git, base=COMMIT), "not a clean base")
    refused(lambda: r2(records=first, fragments=pair(edit=shrink(31)), base=COMMIT), "is the judged commit itself")


def test_a_later_clean_campaign_resolves_older_open_survivors():
    old = r2().changed
    older = old[COMMIT]
    newer_commit = "9" * 40
    newer = copy.deepcopy(older)
    newer.update(commit=newer_commit, revision=1, results=[], labels=[{**older["labels"][0], "tag": "cgprofile-v2.1.0", "version": "2.1.0"}])
    newer["inventory"] = {"sequence": None, "members": [], "r2_lanes": []}
    plan = r2(records={**old, newer_commit: newer}, fragments=pair(commit=newer_commit, edit=shrink(31)), closeout_exit=0)
    resolved = lane_result(plan.changed[COMMIT])
    assert resolved["levels"]["R2"] == "pass" and resolved["survivors"]["resolved"] == 31
    assert {s["resolved_by"] for s in resolved["survivors"]["listed"]} == {newer_commit}
    assert len(plan.trailers) == 2 and plan.trailers[0].startswith(f"Rigor-Record: cgroup-profiler@{newer_commit}")


def test_many_survivors_collapse_to_per_file_counts():
    plan = r2(fragments=pair(edit=grow(300)))
    survivors = lane_result(plan.changed[COMMIT])["survivors"]
    assert survivors["listed"] == [] and sum(survivors["by_file"].values()) == 300 and survivors["sidecar_sha256"]
    refused(lambda: r2(fragments=pair(edit=grow(501, "a" * 64))), "per-file counts need the sidecar")


# -- qualifications -----------------------------------------------------------------------------------------------
def qualification_fragments():
    return [("assay-r1.json", json.dumps(fragment_doc("assay-r1.json")).encode())]


def test_a_qualification_is_written_once_at_the_record_commit():
    plan = plan_qualification(
        ref=TAG, name="promotion", records={COMMIT: base_record()}, fragments=qualification_fragments(),
        project_dir=DIR, git=FakeGit(),
    )
    qualification = plan.changed[COMMIT]["qualifications"]["promotion"]
    assert qualification["judged_commit"] == COMMIT and qualification["results"][0]["lane"] == "assay-r1"
    assert plan.subject == "chore(rigor): cgprofile-v2.0.0 promotion pass" and plan.changed[COMMIT]["revision"] == 2
    again = plan.changed
    refused(lambda: plan_qualification(ref=TAG, name="promotion", records=again, fragments=qualification_fragments(), project_dir=DIR, git=FakeGit()), "written once")
    refused(lambda: plan_qualification(ref=TAG, name="p", records={COMMIT: base_record()}, fragments=[], project_dir=DIR, git=FakeGit()), "no fragments")
    refused(lambda: plan_qualification(ref=TAG, name="p", records={COMMIT: base_record()}, fragments=pair(), project_dir=DIR, git=FakeGit()), "enters through update")


def test_a_qualification_of_other_targets_refuses():
    # the fragment's own binding passes (its judged commit has TREE), the record commit has another tree
    other = dict(base_record(), commit=OTHER)
    refused(
        lambda: plan_qualification(
            ref=OTHER, name="p", records={OTHER: other}, fragments=qualification_fragments(), project_dir=DIR,
            git=FakeGit(trees={OTHER: TREE2}, tag=OTHER),
        ),
        "the commit has",
    )


# -- reconcile --------------------------------------------------------------------------------------------------------
def waivers_for(ids, disposition="equivalent-reviewed"):
    return {"survivor": [{"candidate_id": i, "disposition": disposition, "reason": "same behaviour"} for i in ids]}


def test_reconcile_applies_rulings_and_completes_the_lane_when_none_stay_open():
    records = r2().changed
    listed = lane_result(records[COMMIT])["survivors"]["listed"]
    some = plan_reconcile(ref=COMMIT, records=records, waivers=waivers_for([listed[0]["candidate_id"]]), git=FakeGit())
    result = lane_result(some.changed[COMMIT])
    assert result["levels"]["R2"] == "survivors" and result["survivors"]["open"] == 30 and result["survivors"]["equivalent_reviewed"] == 1
    everything = plan_reconcile(ref=TAG, records=records, waivers=waivers_for([s["candidate_id"] for s in listed], "accepted"), git=FakeGit())
    done = lane_result(everything.changed[COMMIT])
    assert done["levels"]["R2"] == "pass-with-waivers" and done["survivors"]["accepted"] == 31
    assert plan_reconcile(ref=COMMIT, records=records, waivers=None, git=FakeGit()) is None
    refused(lambda: plan_reconcile(ref=COMMIT, records={COMMIT: base_record()}, waivers=None, git=FakeGit()), "only survivors can be reconciled")
    collapsed = r2(fragments=pair(edit=grow(300))).changed
    refused(lambda: plan_reconcile(ref=COMMIT, records=collapsed, waivers=None, git=FakeGit()), "needs the full list")


# -- withdraw ---------------------------------------------------------------------------------------------------------
def two_releases():
    old = base_record()
    new = copy.deepcopy(old)
    new.update(commit=OTHER, revision=1)
    new["labels"][0].update(tag="cgprofile-v2.1.0", version="2.1.0")
    return {COMMIT: old, OTHER: new}


def test_withdraw_marks_the_label_and_needs_a_newer_current_replacement():
    records = two_releases()
    plan = plan_withdraw(tag=TAG, records=records, reason="broken", replacement="cgprofile-v2.1.0", git=FakeGit(tag=COMMIT))
    label = plan.changed[COMMIT]["labels"][0]
    assert label["state"] == "withdrawn" and label["withdrawn"] == {"reason": "broken", "replacement": "cgprofile-v2.1.0"}
    assert plan.subject == "chore(rigor): cgprofile-v2.0.0 release withdrawn"
    refused(lambda: plan_withdraw(tag=TAG, records=plan.changed | {OTHER: records[OTHER]}, reason="x", replacement="cgprofile-v2.1.0", git=FakeGit()), "already withdrawn")
    refused(lambda: plan_withdraw(tag=TAG, records=records, reason="x", replacement="cgprofile-v9.9.9", git=FakeGit()), "has no record")
    refused(lambda: plan_withdraw(tag="cgprofile-v2.1.0", records=records, reason="x", replacement=TAG, git=FakeGit(tag=OTHER)), "is not newer")
    gone = copy.deepcopy(records)
    gone[OTHER]["labels"][0].update(state="withdrawn", withdrawn={"reason": "r", "replacement": "cgprofile-v3.0.0"})
    refused(lambda: plan_withdraw(tag=TAG, records=gone, reason="x", replacement="cgprofile-v2.1.0", git=FakeGit()), "is itself withdrawn")


# -- helpers ----------------------------------------------------------------------------------------------------------
def test_record_of_resolves_commits_and_tags_and_refuses_a_moved_tag():
    records = {COMMIT: base_record()}
    assert record_of(records, COMMIT, FakeGit()) is records[COMMIT]
    assert record_of(records, TAG, FakeGit()) is records[COMMIT]
    refused(lambda: record_of(records, OTHER, FakeGit()), "has no record")
    refused(lambda: record_of(records, "cgprofile-v9.9.9", FakeGit()), "run rigor-record sync first")
    refused(lambda: record_of(records, TAG, FakeGit(tag=None)), "no such tag")
    refused(lambda: record_of(records, TAG, FakeGit(tag=OTHER)), "a moved tag refuses")
    assert label_commits(records) == {TAG: COMMIT}
    assert name_of(records[COMMIT]) == TAG and name_of(r2(records={}, fragments=pair(commit=OTHER)).changed[OTHER]) == OTHER[:12]


def test_expected_revision_is_compare_and_swap():
    record = base_record()
    check_expected_revision(record, None)
    check_expected_revision(record, 1)
    refused(lambda: check_expected_revision(record, 5), "expected 5")
    refused(lambda: r2(expect_revision=5), "expected 5")


def test_read_waivers(tmp_path):
    assert read_waivers(tmp_path, (), ()) is None
    (tmp_path / "rigor").mkdir()
    (tmp_path / "rigor" / "waivers.toml").write_text("not = [valid")
    with pytest.raises(Exception):
        read_waivers(tmp_path, (), ())
    (tmp_path / "rigor" / "waivers.toml").write_text('[[survivor]]\ncandidate_id = "a"\nreason = ""\ndisposition = "nonsense"\n')
    refused(lambda: read_waivers(tmp_path, (), ()), "rigor/waivers.toml")
    (tmp_path / "rigor" / "waivers.toml").write_text('schema = "urn:vbpub:rigor-waivers:1"\n')
    assert read_waivers(tmp_path, (), ()) is not None


# -- coverage of the remaining refusals and fold/resolve branches --------------------------------------------------
def labelled(commit, version):
    record = copy.deepcopy(base_record())
    record.update(commit=commit, revision=1, results=[])
    record["labels"] = [{**record["labels"][0], "tag": f"cgprofile-v{version}", "version": version}]
    record["inventory"] = {"sequence": None, "members": [], "r2_lanes": []}
    return record


def test_an_invalid_stored_record_refuses_the_update():
    bad = base_record()
    bad["inventory"]["extra"] = 1
    refused(lambda: r2(records={COMMIT: bad}), f"record {COMMIT[:12]}")


def test_a_base_without_a_label_and_a_result_without_r2():
    first = r2(fragments=pair(edit=shrink(31)), closeout_exit=0).changed
    nxt = "f" * 40
    git = FakeGit(trees={nxt: TREE2})
    plan = r2(records=first, fragments=pair(commit=nxt, tree=TREE2, edit=shrink(31)), git=git, base=COMMIT, closeout_exit=0)
    assert "base_label" not in lane_result(plan.changed[nxt])["scope"]
    record = base_record()
    stray = copy.deepcopy(lane_result(record, "assay-r1"))
    stray["lane"] = LANE
    record["results"].append(stray)
    refused(lambda: r2(records={COMMIT: record}), "holds no R2")


def test_resolving_older_survivors_skips_clean_based_and_still_reported_ones():
    old = r2().changed
    mid_c, new_c = "8" * 40, "9" * 40
    # the same survivors reported again: nothing to mark
    again = r2(records={**old, new_c: labelled(new_c, "2.1.0")}, fragments=pair(commit=new_c), closeout_exit=1)
    assert list(again.changed) == [new_c]
    # a partial fix: ten resolved, the rest stay open
    partial = r2(records={**old, new_c: labelled(new_c, "2.1.0")}, fragments=pair(commit=new_c, edit=shrink(10)), closeout_exit=1)
    older = lane_result(partial.changed[COMMIT])
    assert older["levels"]["R2"] == "survivors" and older["survivors"]["resolved"] == 10 and older["survivors"]["open"] == 21
    # a campaign based on a newer clean release leaves the older one alone
    clean = r2(records={COMMIT: lane_free(old[COMMIT]), mid_c: labelled(mid_c, "2.1.0")}, fragments=pair(commit=mid_c, edit=shrink(31)), closeout_exit=0)
    records = {COMMIT: old[COMMIT], mid_c: clean.changed[mid_c], new_c: labelled(new_c, "2.2.0")}
    based = r2(records=records, fragments=pair(commit=new_c, edit=shrink(31)), base=mid_c, closeout_exit=0)
    assert list(based.changed) == [new_c]


def lane_free(record):
    """The record without its R2 lane result (a release that was never campaigned)."""
    free = copy.deepcopy(record)
    free["results"] = [r for r in free["results"] if r["lane"] != LANE]
    free["inventory"]["r2_lanes"] = []
    return free


def test_fragments_must_agree_on_commit_and_project():
    assay, gate = (json.loads(data) for _name, data in pair())
    gate["judged_commit"] = OTHER
    split = [("assay.json", canonical_json(assay).encode()), ("gate.json", canonical_json(gate).encode())]
    refused(lambda: r2(fragments=split), "different judged commits")
    other = [json.loads(data) for _name, data in renamed(pair(), "r2b")]
    for doc in other:
        doc["project"] = "other"
    other[1]["assay_fragment_sha256"] = hashlib.sha256(canonical_json(other[0]).encode()).hexdigest()
    mixed = pair() + [("b-assay.json", canonical_json(other[0]).encode()), ("b-gate.json", canonical_json(other[1]).encode())]
    refused(lambda: r2(fragments=mixed), "R2 fragments are for projects")


def test_a_campaign_without_times_refuses():
    refused(lambda: r2(fragments=pair(edit=lambda a: a["r2"].update(campaign=None))), "campaign")


def test_qualification_refusals_beyond_the_basics():
    records = {COMMIT: base_record()}
    kwargs = dict(ref=COMMIT, name="p", records=records, project_dir=DIR, git=FakeGit())
    composite = [("composite.json", (ASSEMBLE / "fragments" / "composite.json").read_bytes())]
    refused(lambda: plan_qualification(fragments=composite, **kwargs), "belongs to assemble")
    first, second = fragment_doc("assay-r1.json"), fragment_doc("assay-r3.json")
    second["judged_commit"] = OTHER
    split = [("a.json", canonical_json(first).encode()), ("b.json", canonical_json(second).encode())]
    refused(lambda: plan_qualification(fragments=split, **kwargs), "different commits")
    refused(lambda: plan_qualification(fragments=qualification_fragments(), **{**kwargs, "git": FakeGit(trees={COMMIT: TREE2})}), "fragment claims tree")
    other = dict(base_record(), commit=OTHER)
    refused(
        lambda: plan_qualification(
            ref=OTHER, name="p", records={OTHER: other}, fragments=qualification_fragments(), project_dir=DIR,
            git=FakeGit(missing=[OTHER], tag=OTHER),
        ),
        "the commit has None",
    )


def test_folding_a_release_into_an_earlier_record_keeps_history_and_skips_the_sequence():
    first = r2(records={}, fragments=pair(edit=fit(3)))
    second = r2(records=first.changed, fragments=pair(edit=shrink(31)), closeout_exit=0)
    folded = plan_sync(record=base_record(), asset_sha256="a" * 64, records=second.changed)
    assert folded is not None and lane_result(folded.changed[COMMIT])["history"][0]["status"] == "incomplete"
    existing = base_record()
    existing["labels"] = [{**existing["labels"][0], "tag": "cgprofile-v1.9.9", "version": "1.9.9"}]
    existing["results"] = [r for r in existing["results"] if r["kind"] == "sequence"]
    released = base_record()
    released["labels"] = [{**released["labels"][0], "tag": "cgprofile-v2.0.1", "version": "2.0.1"}]
    assert plan_sync(record=released, asset_sha256="b" * 64, records={COMMIT: existing}) is not None


def test_a_conflicting_machine_class_speed_refuses():
    gate = json.loads(pair()[1][1])
    clash = dict(gate["machine_classes"][0], speed={"unavailable": True, "reason": "other"})
    record = base_record()
    record["machine_classes"] = [clash]
    refused(lambda: r2(records={COMMIT: record}), "different speed")
    qualifying = json.loads(world.retarget((ASSEMBLE / "fragments" / "r0-r1.run-gate.json").read_bytes(), COMMIT, TREE))
    clash = dict(qualifying["machine_classes"][0], speed={"unavailable": True, "reason": "other"})
    record["machine_classes"] = [clash]
    refused(
        lambda: plan_qualification(
            ref=COMMIT, name="p", records={COMMIT: record}, fragments=[("g.json", canonical_json(qualifying).encode())],
            project_dir=DIR, git=FakeGit(),
        ),
        "different speed",
    )


def test_an_older_record_touched_by_the_update_is_not_resolved_twice():
    old = r2().changed
    new_c = "9" * 40
    fragments = pair(commit=new_c, edit=shrink(31)) + renamed(pair(commit=COMMIT, edit=shrink(31)), "r2b")
    plan = r2(records={**old, new_c: labelled(new_c, "2.1.0")}, fragments=fragments, closeout_exit=0)
    assert set(plan.changed) == {COMMIT, new_c}
