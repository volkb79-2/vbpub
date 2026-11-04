import pytest
import copy
import hashlib
import json

from conftest import (
    ASSEMBLE, COMMIT, DELETE, TREE, FakeGit, GOLDEN, any_problem, assemble_kwargs, dump, edit, fragment_doc, refusal, run_assemble,
)
from rigor_record import canonical_json, validate_record
from rigor_record.assemble import parse_review


def frags(*docs):
    return [(f"f{i}.json", dump(d)) for i, d in enumerate(docs)]


def gate():
    return fragment_doc("r0-r1.run-gate.json")


def a1():
    return fragment_doc("assay-r1.json")


def a3():
    return fragment_doc("assay-r3.json")


def comp(*members, **changes):
    doc = fragment_doc("composite.json")
    if members:
        doc["members"] = list(members)
    doc.update(changes)
    return doc


def standard(g=None, one=None, three=None, c=None):
    return frags(c or comp(), g or gate(), one or a1(), three or a3())


def sha(doc):
    return hashlib.sha256(dump(doc)).hexdigest()


def result_of(record, lane):
    return next(r for r in record["results"] if r["lane"] == lane)


# -- the golden round trip ------------------------------------------------------------------------------------
def test_golden_assemble_is_byte_identical():
    result = run_assemble()
    assert result.record_text == (ASSEMBLE / "record.json").read_text()
    assert result.body_text == (ASSEMBLE / "release-body.md").read_text()
    assert validate_record(result.record) == []
    assert canonical_json(json.loads(result.record_text)) == result.record_text


def test_assemble_is_deterministic_and_input_order_independent():
    first = run_assemble()
    second = run_assemble(fragments=list(reversed(assemble_kwargs()["fragments"])))
    assert first.record_text == second.record_text


def test_record_shape():
    record = run_assemble().record
    assert record["revision"] == 1 and record["commit"] == COMMIT and "adopted_from" not in record
    assert record["review"] == {
        "status": "accept", "range": {"from": "cgprofile-v1.2.0", "to": COMMIT},
        "reviewer_role": "independent-reviewer",
        "document_sha256": hashlib.sha256((ASSEMBLE / "review.md").read_bytes()).hexdigest(),
    }
    label = record["labels"][0]
    assert label["kind"] == "release" and label["version"] == "2.0.0" and label["state"] == "current" and label["withdrawn"] is None
    assert label["artifacts"] == [{"name": "cgprofile-2.0.0-py3-none-any.whl", "sha256": "6" * 64}]
    assert record["inventory"] == {"sequence": "release", "members": ["r0-r1", "assay-r1", "assay-r3"], "r2_lanes": []}
    assert record["toolsets"].keys() == {"t1", "t2", "t3"}
    assert [r["lane"] for r in record["results"]] == ["r0-r1", "assay-r1", "assay-r3", "release"]
    assert result_of(record, "release")["kind"] == "sequence"


def test_results_are_derived_from_the_fragments_claims_not_from_sections():
    record = run_assemble().record
    assert result_of(record, "r0-r1")["levels"] == {"R0": "pass"}
    assert result_of(record, "assay-r1")["levels"] == {"R0": "pass", "R1": "pass"}
    assert result_of(record, "assay-r3")["levels"] == {"R0": "pass", "R3": "pass"}
    # R1 is claimed by assay-r1 only; its canary section (if any) is not evidence
    assert "canary" not in result_of(record, "assay-r1")
    no_claims = edit(a1(), (("claims",), DELETE))
    again = run_assemble(fragments=standard(one=no_claims)).record
    assert result_of(again, "assay-r1")["levels"] == {"R0": "pass"}
    assert "coverage" not in result_of(again, "assay-r1")


def test_a_command_lane_is_declared_never_judged():
    r0 = result_of(run_assemble().record, "r0-r1")
    assert r0["kind"] == "command" and r0["judged_by"] == "run-gate" and r0["note"] == "declared, not judged"
    assert r0["verdict_sha256"] is None and list(r0["levels"]) == ["R0"]


def test_targets_are_the_project_tree_plus_resolvable_inputs():
    record = run_assemble().record
    assert result_of(record, "assay-r1")["targets"] == {"cgroup-profiler": TREE, "node_modules": TREE}
    assert "unresolved_inputs" not in result_of(record, "assay-r1")
    assert result_of(record, "assay-r3")["targets"] == {"cgroup-profiler": TREE}


def test_an_input_git_cannot_resolve_is_recorded_never_dropped():
    record = run_assemble(git=_UntrackedGit()).record
    result = result_of(record, "assay-r1")
    assert result["targets"] == {"cgroup-profiler": TREE}
    assert result["unresolved_inputs"] == ["node_modules"]
    assert validate_record(record) == []
    assert "unresolved_inputs" not in result_of(record, "assay-r3")


class _UntrackedGit(FakeGit):
    def tree(self, commit, path):
        from rigor_record.gitfacts import GitError

        if path == "node_modules":
            raise GitError("untracked")
        return super().tree(commit, path)


def test_body_has_no_heading_and_stays_small():
    body = run_assemble().body_text
    assert not body.startswith("#") and len(body.encode()) < 8 * 1024
    assert "cgroup-profiler/RIGOR.md on main" in body


# -- oracle: refuses a tree mismatch --------------------------------------------------------------------------
def test_refuses_a_fragment_judged_on_another_tree():
    other = edit(a1(), (("project_tree",), "c" * 40))
    problems = refusal(fragments=standard(one=other), git=FakeGit(trees={COMMIT: TREE}, default="c" * 40))
    assert any_problem(problems, "judged on project tree " + "c" * 40)
    assert any_problem(problems, TREE)  # names both trees


def test_refuses_when_the_tag_commit_has_another_tree_than_the_fragments():
    problems = refusal(git=FakeGit(trees={COMMIT: "d" * 40}, default="d" * 40))
    assert any_problem(problems, "judged on project tree " + TREE)
    assert any_problem(problems, "claims tree")


def test_refuses_a_fragment_whose_claimed_tree_differs_from_its_judged_commit():
    moved = edit(a1(), (("judged_commit",), "e" * 40))
    problems = refusal(fragments=standard(one=moved), git=FakeGit(trees={"e" * 40: "9" * 40}))
    assert any_problem(problems, "claims tree " + TREE + " but its judged commit has " + "9" * 40)


def test_refuses_an_unknown_judged_commit():
    gone = edit(a1(), (("judged_commit",), "e" * 40))
    problems = refusal(fragments=standard(one=gone), git=FakeGit(missing={"e" * 40}))
    assert any_problem(problems, "judged commit " + "e" * 40 + " is not in the repository")


def test_accepts_a_release_input_commit_with_an_equal_tree():
    other = "e" * 40
    one, three = (edit(d, (("judged_commit",), other)) for d in (a1(), a3()))
    result = run_assemble(fragments=standard(one=one, three=three), git=FakeGit(trees={other: TREE}))
    assert result.record["commit"] == COMMIT


def test_refuses_a_fragment_of_another_project():
    problems = refusal(fragments=standard(one=edit(a1(), (("project",), "other"))))
    assert any_problem(problems, "is for project other, not cgroup-profiler")


def test_refuses_two_judged_commits_in_one_lane():
    wrapped = edit(a1(), (("lane",), "wrapped"), (("judged_commit",), "e" * 40))
    g = edit(gate(), (("assay_fragment_sha256",), sha(wrapped)))
    problems = refusal(fragments=frags(comp(), g, wrapped, a1(), a3()), git=FakeGit(trees={"e" * 40: TREE}))
    assert any_problem(problems, "its fragments name different judged commits")


def test_refuses_a_tag_that_resolves_elsewhere_or_not_at_all():
    assert any_problem(refusal(git=FakeGit(tag="f" * 40)), "resolves to " + "f" * 40)
    assert any_problem(refusal(git=FakeGit(tag=None)), "no such tag")
    assert any_problem(refusal(git=FakeGit(missing={COMMIT})), "no such commit")


# -- oracle: the sequence and its members ---------------------------------------------------------------------
def test_refuses_a_missing_or_duplicated_composite():
    assert any_problem(refusal(fragments=frags(gate(), a1(), a3())), "exactly one run-gate-composite fragment, found 0")
    assert any_problem(refusal(fragments=frags(comp(), comp(), gate(), a1(), a3())), "exactly one run-gate-composite fragment, found 2")


def test_refuses_a_member_without_a_fragment():
    problems = refusal(fragments=frags(comp(), gate(), a1()))
    assert any_problem(problems, "member assay-r3 has no fragment")


def test_refuses_a_fragment_the_sequence_does_not_list():
    extra = edit(a1(), (("lane",), "unlisted"))
    problems = refusal(fragments=frags(comp(), gate(), a1(), a3(), extra))
    assert any_problem(problems, "lane unlisted: a fragment exists but the sequence release does not list the lane")


def test_refuses_a_composite_for_another_commit_or_project():
    assert any_problem(refusal(fragments=standard(c=comp(judged_commit="f" * 40))), "judged another project or commit")
    assert any_problem(refusal(fragments=standard(c=comp(project="other"))), "judged another project or commit")


def test_r2_evidence_enters_through_update_not_assemble():
    r2 = fragment_doc("../../fragments/assay-r2-survivors.json")
    r2 = edit(r2, (("lane",), "assay-r2"), (("judged_commit",), COMMIT), (("project_tree",), TREE))
    problems = refusal(fragments=frags(comp("r0-r1", "assay-r1", "assay-r3", "assay-r2"), gate(), a1(), a3(), r2))
    assert any_problem(problems, "lane assay-r2: R2 evidence enters through update")
    assert any_problem(refusal(r2_lanes=["assay-r1"]), "R2 lane assay-r1 is a member")


def test_declared_r2_lanes_land_in_the_inventory():
    assert run_assemble(r2_lanes=["z-r2", "a-r2"]).record["inventory"]["r2_lanes"] == ["a-r2", "z-r2"]


def test_a_failed_sequence_needs_a_failed_member():
    assert any_problem(refusal(fragments=standard(c=comp(outcome="FAIL"))), "FAIL without a failed member lane")


# -- oracle: refuses an unbound review ------------------------------------------------------------------------
def review_with(line):
    return ("# Review\n\nprose\n\n" + line + "\n").encode()


def marker(verdict="ACCEPT", frm="cgprofile-v1.2.0", to=COMMIT, who="independent-reviewer"):
    return f"<!-- rigor-review: verdict={verdict} from={frm} to={to} reviewer={who} -->"


def test_refuses_a_review_bound_to_another_commit():
    assert any_problem(refusal(review=review_with(marker(to="f" * 40))), "unbound")


def test_refuses_a_review_that_is_not_accept():
    assert any_problem(refusal(review=review_with(marker(verdict="REJECT"))), "not ACCEPT")


def test_refuses_a_review_without_or_with_two_marker_lines_or_a_malformed_one():
    assert any_problem(refusal(review=b"# Review\n"), "found 0")
    assert any_problem(refusal(review=review_with(marker() + "\n" + marker())), "found 2")
    assert any_problem(refusal(review=review_with("<!-- rigor-review: verdict=ACCEPT -->")), "malformed")


def test_review_without_a_previous_tag_has_a_null_start():
    entry, problems = parse_review(review_with(marker(frm="none")), COMMIT)
    assert problems == [] and entry["range"] == {"from": None, "to": COMMIT}


def test_review_bad_verdict_and_unbound_are_both_reported():
    entry, problems = parse_review(review_with(marker(verdict="REJECT", to="f" * 40)), COMMIT)
    assert entry is None and len(problems) == 2


# -- oracle: refuses an expired waiver ------------------------------------------------------------------------
def waivers_with(version, lane="slow-io", level="R0"):
    return {
        "schema": "urn:vbpub:rigor-waivers:1",
        "lane": [{"lane": lane, "level": level, "reason": "flaky", "expires_before": version}],
    }


def test_refuses_an_expired_waiver_even_when_unused():
    assert any_problem(refusal(waivers=waivers_with("2.0.0")), "expired")
    assert any_problem(refusal(waivers=waivers_with("1.9.9")), "expired")


def test_a_waiver_expiring_after_the_version_is_accepted():
    assert result_of(run_assemble(waivers=waivers_with("2.0.1")).record, "r0-r1")["levels"] == {"R0": "pass"}


def test_assemble_works_without_a_waivers_file():
    assert result_of(run_assemble(waivers=None).record, "r0-r1")["levels"] == {"R0": "pass"}


def test_an_invalid_waivers_document_is_refused():
    assert any_problem(refusal(waivers={"schema": "wrong"}), "waivers:")


# -- failed lanes and waivers -----------------------------------------------------------------------------------
def test_a_failed_lane_without_a_waiver_is_refused():
    failing = edit(a3(), (("outcome",), "FAIL"), (("reason",), "TESTS_FAILED"))
    assert any_problem(refusal(fragments=standard(three=failing), waivers=None), "lane assay-r3: FAIL and no waiver covers it")


def test_a_failed_gate_exit_is_a_fail():
    failing = edit(gate(), (("exit",), 1))
    assert any_problem(refusal(fragments=standard(g=failing), waivers=None), "lane r0-r1: FAIL")


def test_a_waived_failed_lane_is_stored_failed_with_its_waiver():
    failing = edit(gate(), (("exit",), 1))
    waivers = waivers_with("3.0.0", lane="r0-r1")
    waivers["lane"][0]["plan_item"] = "P-1"
    r0 = result_of(run_assemble(fragments=standard(g=failing), waivers=waivers).record, "r0-r1")
    assert r0["levels"] == {"R0": "fail"}
    assert r0["waiver"] == {"reason": "flaky", "plan_item": "P-1", "expires_before": "3.0.0"}


def test_a_waiver_without_plan_item_has_null_plan_item():
    failing = edit(gate(), (("exit",), 2))
    record = run_assemble(fragments=standard(g=failing), waivers=waivers_with("3.0.0", lane="r0-r1")).record
    assert result_of(record, "r0-r1")["waiver"]["plan_item"] is None


def test_a_waiver_for_another_lane_does_not_cover_a_failed_lane():
    failing = edit(gate(), (("exit",), 1))
    problems = refusal(fragments=standard(g=failing), waivers=waivers_with("3.0.0", lane="other"))
    assert any_problem(problems, "no waiver covers it")


def test_an_assay_error_outcome_counts_as_failed_and_a_failed_level_fails_all_its_levels():
    erred = edit(
        a1(), (("outcome",), "ERROR"), (("reason",), "VERDICT_UNVERIFIED"),
        (("claims",), [{"rigor": "R0", "outcome": "ERROR"}, {"rigor": "R1", "outcome": "ERROR"}]),
    )
    assert any_problem(refusal(fragments=standard(one=erred), waivers=None), "lane assay-r1: ERROR")
    record = run_assemble(fragments=standard(one=erred), waivers=waivers_with("3.0.0", lane="assay-r1")).record
    assert result_of(record, "assay-r1")["levels"] == {"R0": "fail", "R1": "fail"}


def test_an_errored_claim_without_its_section_does_not_block_a_waived_failing_lane():
    erred = edit(
        a1(), (("outcome",), "ERROR"), (("reason",), "VERDICT_UNVERIFIED"), (("coverage",), None),
        (("claims",), [{"rigor": "R0", "outcome": "ERROR"}, {"rigor": "R1", "outcome": "ERROR"}]),
    )
    record = run_assemble(fragments=standard(one=erred), waivers=waivers_with("3.0.0", lane="assay-r1")).record
    result = result_of(record, "assay-r1")
    assert result["levels"] == {"R0": "fail"} and "coverage" not in result and "waiver" in result
    assert any_problem(refusal(fragments=standard(one=erred), waivers=None), "lane assay-r1: ERROR and no waiver")


def test_each_claim_has_its_own_status_not_the_lanes():
    failed_claim = edit(a1(), (("claims",), [{"rigor": "R0", "outcome": "PASS"}, {"rigor": "R1", "outcome": "FAIL"}]))
    assert any_problem(refusal(fragments=standard(one=failed_claim), waivers=None), "claim R1 failed and no waiver")
    record = run_assemble(fragments=standard(one=failed_claim), waivers=waivers_with("3.0.0", lane="assay-r1")).record
    assert result_of(record, "assay-r1")["levels"] == {"R0": "pass", "R1": "fail"}
    failed_lane = edit(a1(), (("outcome",), "FAIL"), (("reason",), "SURVIVORS_OPEN"), (("claims",), [{"rigor": "R0", "outcome": "FAIL"}, {"rigor": "R1", "outcome": "PASS"}]))
    record = run_assemble(fragments=standard(one=failed_lane), waivers=waivers_with("3.0.0", lane="assay-r1")).record
    assert result_of(record, "assay-r1")["levels"] == {"R0": "fail", "R1": "pass"}


def test_a_wrapped_assay_fragment_joins_its_run_gate_lane():
    wrapped = edit(a1(), (("lane",), "wrapped"))
    g = edit(gate(), (("assay_fragment_sha256",), sha(wrapped)))
    record = run_assemble(fragments=frags(comp("r0-r1"), g, wrapped)).record
    lane = result_of(record, "r0-r1")
    assert lane["kind"] == "assay" and lane["verdict_sha256"] == wrapped["verdict_sha256"]
    assert set(record["toolsets"]["t1"]) == {"assay", "run_gate", "tester_image"}
    assert lane["levels"] == {"R0": "pass", "R1": "pass"}


# -- fragments ------------------------------------------------------------------------------------------------
def test_unparseable_invalid_and_oversize_fragments_are_refused():
    huge = dump(edit(a1(), (("lane",), "x" * 300000)))
    problems = refusal(fragments=[("bad.json", b"{"), ("huge.json", huge), ("wrong.json", dump({"kind": "x"}))])
    assert any_problem(problems, "bad.json: not valid JSON")
    assert any_problem(problems, "huge.json: fragment is")
    assert any_problem(problems, "wrong.json: $.kind")


def test_duplicate_lane_fragments_are_refused():
    problems = refusal(fragments=frags(comp(), gate(), gate(), a1(), a1(), a3()))
    assert any_problem(problems, "lane r0-r1: two run-gate fragments")
    assert any_problem(problems, "lane assay-r1: two assay fragments")


def test_a_missing_assay_reference_is_refused():
    g = edit(gate(), (("assay_fragment_sha256",), "7" * 64))
    assert any_problem(refusal(fragments=standard(g=g)), "its assay fragment " + "7" * 12 + " is not in the fragment set")


def test_bad_tag_and_project_dir_are_refused():
    assert any_problem(refusal(tag="nonsense"), "is not <prefix>-v<semver>")
    assert any_problem(refusal(project_dir="/abs"), "project-relative POSIX path")
    assert any_problem(refusal(project_dir="../up"), "project-relative POSIX path")


def test_the_root_project_dir_is_allowed():
    assert result_of(run_assemble(project_dir=".").record, "r0-r1")["targets"] == {".": TREE}


# -- artifacts ------------------------------------------------------------------------------------------------
def manifest(**changes):
    base = json.loads((ASSEMBLE / "build.json").read_text())
    base.update(changes)
    return base


def test_artifact_inventory_problems():
    assert any_problem(refusal(artifacts=[]), "not a cmru build inventory")
    assert any_problem(refusal(artifacts={"kind": "other"}), "not a cmru build inventory")
    assert any_problem(refusal(artifacts=manifest(publication="forbidden")), "not eligible")
    assert any_problem(refusal(artifacts=manifest(source_commit="f" * 40)), "different commit")
    assert any_problem(refusal(artifacts=manifest(project="other")), "different project")


def release_inventory(**changes):
    """The inventory cmru's record step passes as CMRU_BUILD_INVENTORY (cmru record_step.write_inventory)."""
    local = json.loads((ASSEMBLE / "build.json").read_text())
    base = {
        "schema_version": 1, "kind": "cmru-release-inventory", "project": local["project"], "tag": "cgprofile-v2.0.0",
        "source_commit": local["source_commit"], "artifacts": local["artifacts"],
    }
    base.update(changes)
    return base


def test_the_inventory_cmrus_record_step_passes_is_accepted():
    """RRK-15: producer (cmru) and consumer (assemble) agree on kind and keys; no `publication` key exists there."""
    from_step = run_assemble(artifacts=release_inventory()).record
    assert from_step == run_assemble().record
    assert any_problem(refusal(artifacts=release_inventory(tag="cgprofile-v9.9.9")), "inventory is for tag cgprofile-v9.9.9")
    assert any_problem(refusal(artifacts=release_inventory(source_commit="f" * 40)), "different commit")
    assert any_problem(refusal(artifacts=release_inventory(project="other")), "different project")


def test_duplicate_artifact_names_are_refused_and_nested_paths_use_the_file_name():
    entry = {"bytes": "1", "path": "sub/x.whl", "sha256": "1" * 64}
    two = manifest(artifacts=[{"directory": "a", "files": [entry]}, {"directory": "b", "files": [entry]}])
    assert any_problem(refusal(artifacts=two), "duplicate artifact name x.whl")
    one = manifest(artifacts=[{"directory": "a", "files": [entry, {"bytes": "1", "path": "a.whl", "sha256": "2" * 64}]}])
    assert [a["name"] for a in run_assemble(artifacts=one).record["labels"][0]["artifacts"]] == ["a.whl", "x.whl"]


# -- claims: coverage and canaries ----------------------------------------------------------------------------
def test_a_claim_without_its_section_is_refused():
    no_cov = edit(a1(), (("coverage",), None))
    assert any_problem(refusal(fragments=standard(one=no_cov)), "assay claims R1 but the fragment carries no coverage")
    no_canary = edit(a3(), (("canary",), None))
    assert any_problem(refusal(fragments=standard(three=no_canary)), "assay claims R3 but the fragment carries no canary")


def test_a_gate_fragment_without_an_image_adds_no_tester_image():
    no_image = edit(gate(), (("image",), None))
    record = run_assemble(fragments=standard(g=no_image)).record
    assert set(record["toolsets"]["t1"]) == {"run_gate"}


def test_a_waived_r1_may_keep_its_sub_floor_coverage():
    low = edit(a1(), (("outcome",), "FAIL"), (("reason",), "COVERAGE_BELOW_FLOOR"),
               (("claims",), [{"rigor": "R0", "outcome": "FAIL"}, {"rigor": "R1", "outcome": "FAIL"}]),
               (("coverage",), {**a1()["coverage"], "line_pct": 85.5}))
    waivers = waivers_with("3.0.0", lane="assay-r1", level="R1")
    r1 = result_of(run_assemble(fragments=standard(one=low), waivers=waivers).record, "assay-r1")
    assert r1["levels"]["R1"] == "fail" and r1["coverage"]["line_pct"] == 85.5


# -- perf and machine classes ---------------------------------------------------------------------------------
def test_machine_classes_are_merged_by_identity_and_renumbered():
    other = copy.deepcopy(gate())
    other["lane"] = "r0-r1b"
    other["machine_classes"][0]["id"] = "m7"
    other["perf"][0]["machine_classes"] = {"m7": 1}
    other["perf"][0]["lane"] = "r0-r1b"
    record = run_assemble(fragments=frags(comp("r0-r1", "r0-r1b", "assay-r1", "assay-r3"), gate(), other, a1(), a3())).record
    assert [m["id"] for m in record["machine_classes"]] == ["m1"]
    assert [p["machine_classes"] for p in record["perf"]] == [{"m1": 1}, {"m1": 1}]


def test_two_ids_of_one_class_add_their_counts():
    twin = copy.deepcopy(gate())
    twin["machine_classes"].append({**twin["machine_classes"][0], "id": "m2"})
    twin["perf"][0]["machine_classes"] = {"m1": 2, "m2": 3}
    record = run_assemble(fragments=standard(g=twin)).record
    assert record["perf"][0]["machine_classes"] == {"m1": 5}
    assert len(record["machine_classes"]) == 1


def test_one_class_with_two_speeds_is_refused():
    other = copy.deepcopy(gate())
    other["lane"] = "r0-r1b"
    other["machine_classes"][0]["speed"] = {"unavailable": True, "reason": "NO_HOST_MEASUREMENT"}
    other["perf"] = []
    problems = refusal(fragments=frags(comp("r0-r1", "r0-r1b", "assay-r1", "assay-r3"), gate(), other, a1(), a3()))
    assert any_problem(problems, "different speed")


# -- privacy ------------------------------------------------------------------------------------------------
def test_a_privacy_hit_in_the_record_is_refused():
    assert any_problem(refusal(hosts=("cgroup",)), "privacy rule host")


def test_a_privacy_hit_in_the_release_body_is_refused():
    problems = refusal(project_dir="libraries/zzhostzz", hosts=("zzhost",))
    assert any_problem(problems, "privacy rule host")


def test_golden_directory_is_reachable_from_the_tests():
    assert (GOLDEN / "fragments" / "assay-r2-survivors.json").exists()


def test_a_release_body_over_its_limit_is_refused(monkeypatch):
    from rigor_record.assemble import Refusal
    from rigor_record.validate import LIMITS

    monkeypatch.setitem(LIMITS, "release-body", 10)
    with pytest.raises(Refusal, match="release body is"):
        run_assemble()
