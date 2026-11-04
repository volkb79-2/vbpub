import pytest

from conftest import DELETE, GOLDEN, edit, has
from rigor_record import validate_record
from rigor_record.validate import canonical_json, check_size

RECORDS = ["command-lane", "r2-pass", "r2-pass-after-rerun", "r2-pass-with-waivers", "r2-survivors", "r2-incomplete",
           "r2-error", "r2-unlabelled", "withdrawn"]
R0, A1, A3, A2, SEQ = ("results", 0), ("results", 1), ("results", 2), ("results", 3), ("results", 4)
WAIVER = {"reason": "slow io lane", "plan_item": None, "expires_before": "9.0.0"}


@pytest.mark.parametrize("name", RECORDS)
def test_golden_records_validate_and_are_canonical(rec, name):
    doc = rec(name)
    assert validate_record(doc) == []
    text = (GOLDEN / "records" / f"{name}.json").read_text()
    assert text == canonical_json(doc)
    assert check_size("record", text) == []


def test_one_golden_record_per_stored_r2_status(rec):
    seen = {rec(n)["results"][3]["levels"]["R2"] for n in RECORDS if n not in ("command-lane", "withdrawn", "r2-unlabelled")}
    assert seen == {"pass", "pass-with-waivers", "survivors", "incomplete", "error"}


def test_goldens_exercise_the_record_variants(rec):
    assert rec("withdrawn")["labels"][0]["state"] == "withdrawn"
    assert rec("r2-unlabelled")["labels"] == []
    assert rec("r2-pass-after-rerun")["results"][3]["history"][0]["status"] == "incomplete"
    assert rec("command-lane")["results"][0]["kind"] == "command"


def test_schema_level_rejections(rec):
    base = rec("r2-pass")
    for path, value, where in [
        (("schema",), "urn:vbpub:rigor-record:2", "$.schema"),
        (("revision",), 0, "$.revision"),
        (("project",), "Has Space", "$.project"),
        (("commit",), "abc", "$.commit"),
        (("labels", 0, "tag"), "v2.0.0", "$.labels[0].tag"),
        (("labels", 0, "published_at"), "yesterday", "$.labels[0].published_at"),
        (("labels", 0, "artifacts", 0, "name"), "/abs/x.whl", "$.labels[0].artifacts[0].name"),
        (("labels", 0, "state"), "gone", "$.labels[0].state"),
        (("toolsets", "t2", "assay", "judge_identity"), "x", "$.toolsets.t2.assay.judge_identity"),
        (("toolsets", "host-a"), {}, "$.toolsets.host-a"),
        (("results", 0, "levels", "R0"), "ok", "$.results[0].levels.R0"),
        (("results", 0, "toolset"), "t01x", "$.results[0].toolset"),
        (("results", 0, "kind"), "magic", "$.results[0].kind"),
        (("review", "status"), "reject", "$.review.status"),
        (("review", "reviewer_role"), "Alice Smith", "$.review.reviewer_role"),
        (("perf", 0, "metrics", "wall_seconds", "unit"), "furlongs", "$.perf[0].metrics.wall_seconds.unit"),
    ]:
        out = validate_record(edit(base, (path, value)))
        assert any(v.path == where for v in out), (path, out)


def test_label_rules(rec):
    base = rec("r2-pass")
    assert has(validate_record(edit(base, (("labels", 0, "version"), "9.9.9"))), "$.labels[0].version", "version part")
    assert has(validate_record(edit(base, (("labels", 0, "state"), "withdrawn"))), "$.labels[0].withdrawn", "exactly when")
    gone = {"reason": "r", "replacement": "cgprofile-v2.0.1"}
    assert has(validate_record(edit(base, (("labels", 0, "withdrawn"), gone))), "$.labels[0].withdrawn", "exactly when")
    assert has(validate_record(edit(base, (("labels",), base["labels"] * 2))), "$.labels", "duplicate label")


def test_toolset_references_must_exist(rec):
    base = rec("r2-pass-after-rerun")
    for path, where in [((*R0, "toolset"), "$.results[0].toolset"), ((*A2, "history", 0, "toolset"), "$.results[3].history[0].toolset")]:
        assert has(validate_record(edit(base, (path, "t9"))), where, "not declared"), path
    q = edit(rec("r2-pass"), (("qualifications",), {"staging": {"judged_commit": "a" * 40, "results": [rec("r2-pass")["results"][0]]}}),
             (("qualifications", "staging", "results", 0, "toolset"), "t9"))
    assert has(validate_record(q), "$.qualifications.staging.results[0].toolset", "not declared")


# ---- inventory and results ------------------------------------------------------------------------------------
def test_results_and_inventory_must_agree(rec):
    base = rec("r2-pass")
    dup = edit(base, (("results", 1, "lane"), "r0-r1"))
    assert has(validate_record(dup), "$.results[1].lane", "duplicate lane")
    two = edit(base, (("results",), base["results"] + [base["results"][4]]))
    assert has(validate_record(two), "$.results", "at most one sequence")
    assert has(validate_record(edit(base, (("inventory", "sequence"), "other"))), "$.inventory", "names the sequence")
    assert has(validate_record(edit(base, (("inventory", "members"), ["r0-r1"]))), "$.inventory", "names the sequence")
    no_seq = edit(base, (("results",), base["results"][:4]))
    assert has(validate_record(no_seq), "$.inventory", "needs the sequence result")
    assert has(validate_record(edit(base, (("inventory", "r2_lanes"), ["assay-r2", "r0-r1"]))), "$.inventory.r2_lanes", "not a member")
    assert has(validate_record(edit(base, (("inventory", "r2_lanes"), []))), "$.results[3]", "listed in inventory.r2_lanes")


def test_lane_result_rules(rec):
    base = rec("r2-pass")
    assert has(validate_record(edit(base, ((*R0, "levels"), {}))), "$.results[0].levels", "at least one level")
    assert has(validate_record(edit(base, ((*R0, "targets"), {}))), "$.results[0].targets", "names the targets")
    for steps in [[((*R0, "note"), DELETE)], [((*R0, "judged_by"), "assay")], [((*R0, "verdict_sha256"), "1" * 64)],
                  [((*R0, "toolset"), None)], [((*R0, "levels"), {"R0": "pass", "R1": "pass"})]]:
        assert has(validate_record(edit(base, *steps)), "$.results[0]", "command lane"), steps
    assert has(validate_record(edit(base, ((*A1, "verdict_sha256"), None))), "$.results[1]", "assay lane")
    assert has(validate_record(edit(base, ((*A1, "judged_by"), "run-gate"))), "$.results[1]", "assay lane")
    assert has(validate_record(edit(base, ((*A1, "levels"), {"R1": "pass"}))), "$.results[1]", "assay lane")
    assert has(validate_record(edit(base, ((*A1, "note"), "declared, not judged"))), "$.results[1]", "assay lane")
    assert has(validate_record(edit(base, ((*A1, "levels", "R1"), "survivors"))), "$.results[1].levels.R1", "pass or fail")
    assert has(validate_record(edit(base, ((*A2, "levels", "R2"), "pass-with-x"))), "$.results[3].levels.R2", "must be one of")
    assert has(validate_record(edit(base, ((*A2, "levels", "R2"), "fail"))), "$.results[3].levels.R2", "stored as")


def test_a_failed_level_is_stored_only_with_its_waiver(rec):
    base = rec("r2-pass")
    failed = edit(base, ((*A1, "levels", "R1"), "fail"))
    assert has(validate_record(failed), "$.results[1].waiver", "only with its waiver")
    waived = edit(failed, ((*A1, "waiver"), WAIVER))
    assert not has(validate_record(waived), "$.results[1].waiver", "only with its waiver")
    assert has(validate_record(edit(base, ((*A1, "waiver"), WAIVER))), "$.results[1].waiver", "needs a failed level")


def test_r1_pass_needs_coverage_at_floor(rec):
    base = rec("r2-pass")
    cov = (*A1, "coverage")
    assert has(validate_record(edit(base, (cov, DELETE))), "$.results[1].coverage", "exactly when")
    assert has(validate_record(edit(base, (cov + ("line_pct",), 99.9))), "$.results[1].coverage", "at or above")
    assert has(validate_record(edit(base, (cov + ("branch_pct",), 99.9))), "$.results[1].coverage", "at or above")
    exact = edit(base, (cov + ("line_pct",), 99.5), (cov + ("branch_pct",), 98.0), (cov + ("floor",), {"line": 99.5, "branch": 98.0}))
    assert validate_record(exact) == []  # exactly at the floor passes
    stray = edit(base, ((*A3, "coverage"), base["results"][1]["coverage"]))
    assert has(validate_record(stray), "$.results[2].coverage", "exactly when")


def test_r3_pass_needs_a_passing_canary(rec):
    base = rec("r2-pass")
    can = (*A3, "canary")
    assert has(validate_record(edit(base, (can, DELETE))), "$.results[2].canary", "exactly when")
    assert has(validate_record(edit(base, (can + ("outcome",), "FAIL"))), "$.results[2].canary", "passing canary")
    stray = edit(base, ((*A1, "canary"), base["results"][2]["canary"]))
    assert has(validate_record(stray), "$.results[1].canary", "exactly when")


def test_review_rules(rec):
    base = rec("r2-pass")
    for key in ("range", "reviewer_role", "document_sha256"):
        assert has(validate_record(edit(base, (("review", key), None))), "$.review", "accept needs"), key
    assert has(validate_record(edit(base, (("review", "status"), "missing"))), "$.review", "missing carries none")
    assert validate_record(rec("r2-unlabelled")) == []


def test_qualification_results_are_checked_like_lane_results(rec):
    base = rec("r2-pass")
    bad = dict(base["results"][0], levels={"R0": "pass", "R1": "pass"})
    q = edit(base, (("qualifications",), {"q": {"judged_commit": "a" * 40, "results": [bad]}}))
    assert has(validate_record(q), "$.qualifications.q.results[0]", "command lane")


# ---- R2 --------------------------------------------------------------------------------------------------------
def r2(rec, name, *steps):
    return validate_record(edit(rec(name), *[((*A2, *p), v) for p, v in steps]))


def test_r2_keys_belong_to_an_r2_result(rec):
    assert has(r2(rec, "r2-pass", (("levels",), {"R0": "pass"})), "$.results[3]", "belong to an R2 result")
    for key in ("campaign", "candidates", "buckets"):
        assert has(r2(rec, "r2-pass", ((key,), DELETE)), "$.results[3]", "an R2 result carries"), key


def test_complete_statuses_need_the_survivor_block(rec):
    assert has(r2(rec, "r2-survivors", (("survivors",), DELETE)), "$.results[3]", "needs the survivor block")


def test_complete_campaign_arithmetic(rec):
    assert has(r2(rec, "r2-survivors", (("candidates", "executed"), 500)), "$.results[3].candidates", "every candidate ran")
    assert has(r2(rec, "r2-survivors", (("buckets", "killed"), 1)), "$.results[3].buckets", "add up")
    assert has(r2(rec, "r2-survivors", (("campaign", "closeout_exit"), 3)), "$.results[3].campaign.closeout_exit", "exit 0 or 1")
    assert has(r2(rec, "r2-survivors", (("buckets", "survived"), 30), (("buckets", "killed"), 520)), "$.results[3].survivors", "add up")


def test_status_follows_survivor_counts(rec):
    resolved = [dict(s, disposition="resolved", resolved_by="a" * 40) for s in rec("r2-survivors")["results"][3]["survivors"]["listed"]]
    assert has(r2(rec, "r2-survivors", (("survivors", "open"), 0), (("survivors", "resolved"), 31), (("survivors", "listed"), resolved)),
               "$.results[3].levels.R2", "at least one open")
    assert has(r2(rec, "r2-pass-with-waivers", (("levels", "R2"), "pass")), "$.results[3].levels.R2", "pass needs no")
    assert has(r2(rec, "r2-pass", (("levels", "R2"), "pass-with-waivers")), "$.results[3].levels.R2", "pass-with-waivers needs")
    assert has(r2(rec, "r2-survivors", (("levels", "R2"), "pass-with-waivers")), "$.results[3].levels.R2", "pass-with-waivers needs")
    assert has(r2(rec, "r2-survivors", (("levels", "R2"), "pass")), "$.results[3].levels.R2", "pass needs")


def test_resolved_only_survivors_make_a_pass(rec):
    listed = [dict(s, disposition="resolved", resolved_by="a" * 40, reason=None) for s in rec("r2-survivors")["results"][3]["survivors"]["listed"]]
    out = r2(rec, "r2-survivors", (("levels", "R2"), "pass"), (("survivors", "open"), 0), (("survivors", "resolved"), 31),
             (("survivors", "listed"), listed))
    assert out == []


def test_listed_survivors_must_match_their_counts(rec):
    assert has(r2(rec, "r2-survivors", (("survivors", "listed"), [])), "$.results[3].survivors", "all listed")
    assert has(r2(rec, "r2-survivors", (("survivors", "by_file"), {"src/a.py": 1})), "$.results[3].survivors", "all listed")
    assert has(r2(rec, "r2-survivors", (("survivors", "sidecar_sha256"), "1" * 64)), "$.results[3].survivors", "all listed")
    out = r2(rec, "r2-survivors", (("survivors", "listed", 0, "disposition"), "accepted"), (("survivors", "listed", 0, "reason"), "accepted risk"))
    assert has(out, "$.results[3].survivors.open", "count differs")
    assert has(out, "$.results[3].survivors.accepted", "count differs")


def test_listed_survivor_field_rules(rec):
    assert has(r2(rec, "r2-survivors", (("survivors", "listed", 0, "resolved_by"), "a" * 40)),
               "$.results[3].survivors.listed[0].resolved_by", "exactly when")
    assert has(r2(rec, "r2-pass-with-waivers", (("survivors", "listed", 0, "reason"), None)),
               "$.results[3].survivors.listed[0].reason", "states why")


def test_more_than_200_survivors_collapse_to_per_file_counts(rec):
    base = rec("r2-survivors")
    survivors = {"open": 250, "equivalent_reviewed": 0, "accepted": 0, "resolved": 0, "listed": [],
                 "by_file": {"src/a.py": 150, "src/b.py": 100}, "sidecar_sha256": "3" * 64}
    buckets = {"killed": 300, "survived": 250, "equivalent": 0, "hung": 2, "budget_exceeded": 3, "crashed": 2}
    cand = {"total": 557, "executed": 557, "resumed": 0}
    big = edit(base, ((*A2, "survivors"), survivors), ((*A2, "buckets"), buckets))
    assert validate_record(big) == []
    for patch in [{"listed": base["results"][3]["survivors"]["listed"][:1]}, {"by_file": {"src/a.py": 1}}, {"sidecar_sha256": None}]:
        out = validate_record(edit(big, ((*A2, "survivors"), dict(survivors, **patch))))
        assert has(out, "$.results[3].survivors", "collapse"), patch
    assert cand["total"] == 557


def test_incomplete_and_error(rec):
    assert has(r2(rec, "r2-incomplete", (("campaign", "closeout_exit"), 2)), "$.results[3].campaign.closeout_exit", "exit 3")
    assert has(r2(rec, "r2-error", (("campaign", "closeout_exit"), 3)), "$.results[3].campaign.closeout_exit", "exit 2")
    assert has(r2(rec, "r2-incomplete", (("candidates", "executed"), 999)), "$.results[3].candidates", "exceeds total")
    assert has(r2(rec, "r2-incomplete", (("survivors",), rec("r2-survivors")["results"][3]["survivors"])), "$.results[3].survivors",
               "carries no survivor block")


def test_scope_rules(rec):
    changed = rec("r2-pass-with-waivers")
    assert changed["results"][3]["scope"]["kind"] in ("whole-source", "changed-since")
    assert has(r2(rec, "r2-pass", (("scope", "base_commit"), "a" * 40)), "$.results[3].scope", "no base")
    assert has(r2(rec, "r2-pass", (("scope", "anchored"), False)), "$.results[3].scope", "no base")
    assert has(r2(rec, "r2-pass", (("scope", "kind"), "changed-since")), "$.results[3].scope", "names its base commit")
    assert r2(rec, "r2-pass", (("scope", "kind"), "changed-since"), (("scope", "base_commit"), "a" * 40), (("scope", "anchored"), False)) == []


def test_history_scope_is_checked(rec):
    out = validate_record(edit(rec("r2-pass-after-rerun"), ((*A2, "history", 0, "scope", "base_commit"), "a" * 40)))
    assert has(out, "$.results[3].history[0].scope", "no base")


# ---- perf and machine classes inside a record -----------------------------------------------------------------
def test_record_perf_and_machine_class_semantics(rec):
    base = rec("r2-pass")
    assert has(validate_record(edit(base, (("perf", 0, "machine_classes"), {"m7": 1}))), "$.perf[0].machine_classes.m7", "not declared")
    dup = edit(base, (("machine_classes",), base["machine_classes"] * 2))
    assert has(validate_record(dup), "$.machine_classes[1].id", "duplicate")
    bad = edit(base, (("machine_classes", 0, "speed", "geekbench_major"), 6))
    assert has(validate_record(bad), "$.machine_classes[0].speed.geekbench_major", "major part")


# ---- size and privacy -----------------------------------------------------------------------------------------
def test_record_size_limit_is_enforced(rec):
    base = rec("r2-pass")
    perf = base["perf"][0]
    bloated = edit(base, (("perf",), [perf] * 64))
    assert has(validate_record(bloated), "$", "over the 65536 byte limit")


def test_privacy_is_enforced_on_records(rec):
    base = rec("r2-pass")
    out = validate_record(edit(base, (("machine_classes", 0, "cpu_model"), "Xeon @ 2.4GHz host-9")), hosts=["host-9"])
    assert has(out, "$.machine_classes[0].cpu_model", "privacy rule host")
    leak = validate_record(edit(base, (("results", 0, "lane"), "ghp_" + "a" * 30)))
    assert has(leak, "$.results[0].lane", "privacy rule token-prefix")
    review_prose = validate_record(edit(base, (("review", "reviewer_role"), "see /home/x/review.md")))
    assert any(v.path == "$.review.reviewer_role" for v in review_prose)
