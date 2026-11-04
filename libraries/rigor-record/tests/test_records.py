import pytest

from conftest import DELETE, GOLDEN, edit, has
from rigor_record import validate_record
from rigor_record.validate import canonical_json, check_size

R2_STATUSES = ["pending", "pass", "pass-with-waivers", "survivors", "incomplete", "error", "partial", "not-applicable", "waived"]
RECORDS = [f"r2-{s}" for s in R2_STATUSES] + ["adopted-unknown", "withdrawn"]
R2 = ("levels", "R2")


@pytest.mark.parametrize("name", RECORDS)
def test_golden_records_validate_and_are_canonical(rec, name):
    doc = rec(name)
    assert validate_record(doc) == []
    text = (GOLDEN / "records" / f"{name}.json").read_text()
    assert text == canonical_json(doc)
    assert check_size("record", text) == []


def test_one_golden_record_per_stored_r2_status(rec):
    seen = {rec(f"r2-{s}")["levels"]["R2"]["status"] for s in R2_STATUSES}
    assert seen == set(R2_STATUSES)
    assert rec("adopted-unknown")["levels"]["R2"]["status"] == "unknown"


def test_goldens_exercise_level_variants(rec):
    assert rec("r2-not-applicable")["levels"]["R0"]["status"] == "pass-with-waivers"
    assert rec("r2-not-applicable")["levels"]["R3"]["status"] == "not-applicable"
    assert rec("r2-waived")["levels"]["R1"]["status"] == "waived"
    assert rec("r2-error")["levels"]["R2"]["history"][0]["status"] == "incomplete"
    assert rec("r2-pass")["qualifications"]["staging"]["status"] == "pass"
    assert rec("withdrawn")["release"]["state"] == "withdrawn"
    assert rec("adopted-unknown")["adopted_from"] == "cgprofile-v2.0.0"


def test_schema_level_rejections(rec):
    base = rec("r2-pending")
    for path, value, where in [
        (("schema",), "urn:vbpub:rigor-record:2", "$.schema"),
        (("revision",), 0, "$.revision"),
        (("project",), "Has Space", "$.project"),
        (("release", "tag"), "v2.0.0", "$.release.tag"),
        (("release", "commit"), "abc", "$.release.commit"),
        (("release", "published_at"), "yesterday", "$.release.published_at"),
        (("release", "artifacts", 0, "name"), "/abs/x.whl", "$.release.artifacts[0].name"),
        (("release", "state"), "gone", "$.release.state"),
        (("toolsets", "t1", "assay", "judge_identity"), "x", "$.toolsets.t1.assay.judge_identity"),
        (("toolsets", "host-a"), {}, "$.toolsets.host-a"),
        (("levels", "R0", "status"), "ok", "$.levels.R0.status"),
        (("levels", "R0", "toolset"), "t01x", "$.levels.R0.toolset"),
        (("levels", "R0", "lanes", 0, "outcome"), "SKIPPED", "$.levels.R0.lanes[0].outcome"),
        (("levels", "review", "status"), "reject", "$.levels.review.status"),
        (("levels", "review", "reviewer_role"), "Alice Smith", "$.levels.review.reviewer_role"),
        (("levels", "R3", "canaries", 0, "targets"), [], "$.levels.R3.canaries[0].targets"),
        (("levels", "R3", "canaries", 0, "targets", 0), "/etc/passwd", "$.levels.R3.canaries[0].targets[0]"),
        (("levels", "R2", "status"), "superseded", "$.levels.R2.status"),
        (("qualifications", "Bad Name"), dict(base["levels"]["R0"]), "$.qualifications.Bad Name"),
        (("perf", 0, "metrics", "wall_seconds", "unit"), "furlongs", "$.perf[0].metrics.wall_seconds.unit"),
    ]:
        out = validate_record(edit(base, (path, value)))
        assert any(v.path == where for v in out), (path, out)


def test_stored_status_superseded_is_derived_never_stored(rec):
    out = validate_record(edit(rec("r2-pending"), ((*R2, "status"), "superseded")))
    assert has(out, "$.levels.R2.status", "must be one of")


def test_missing_level_key_is_refused_not_defaulted(rec):
    for key in ("R0", "R1", "R3", "review", "R2"):
        out = validate_record(edit(rec("r2-pending"), (("levels", key), DELETE)))
        assert has(out, "$.levels", f"missing required key '{key}'"), key


def test_release_rules(rec):
    base = rec("r2-pending")
    assert has(validate_record(edit(base, (("release", "version"), "9.9.9"))), "$.release.version", "version part")
    assert has(validate_record(edit(base, (("release", "state"), "withdrawn"))), "$.release.withdrawn", "exactly when")
    assert has(validate_record(edit(base, (("release", "withdrawn"), {"reason": "r", "replacement": "cgprofile-v2.0.1"}))),
               "$.release.withdrawn", "exactly when")


def test_toolset_references_must_exist(rec):
    base = rec("r2-error")
    for path, where in [(("levels", "R0", "toolset"), "$.levels.R0.toolset"), ((*R2, "toolset"), "$.levels.R2.toolset"),
                        ((*R2, "history", 0, "toolset"), "$.levels.R2.history[0].toolset")]:
        assert has(validate_record(edit(base, (path, "t9"))), where, "not declared"), path
    q = edit(rec("r2-pass"), (("qualifications", "staging", "toolset"), "t9"))
    assert has(validate_record(q), "$.qualifications.staging.toolset", "not declared")


# ---- gate levels ----------------------------------------------------------------------------------------------
def test_pass_means_every_lane_passed_without_waiver(rec):
    base = rec("r2-pending")
    assert has(validate_record(edit(base, (("levels", "R0", "lanes", 0, "outcome"), "FAIL"))), "$.levels.R0.lanes", "pass means")
    waiver = {"reason": "r", "plan_item": None, "expires_before": "9.0.0"}
    assert has(validate_record(edit(base, (("levels", "R0", "lanes", 0, "waiver"), waiver))), "$.levels.R0.lanes", "pass means")
    assert has(validate_record(edit(base, (("levels", "R0", "lanes"), []))), "$.levels.R0", "needs a toolset")
    assert has(validate_record(edit(base, (("levels", "R0", "toolset"), None))), "$.levels.R0", "needs a toolset")
    assert has(validate_record(edit(base, (("levels", "R0", "judged_commit"), None))), "$.levels.R0", "needs a toolset")
    w = {"id": "OPW-9", "reason": "r"}
    assert has(validate_record(edit(base, (("levels", "R0", "waiver"), w))), "$.levels.R0.waiver", "only a waived")


def test_pass_with_waivers_needs_exactly_the_failed_lanes_waived(rec):
    base = rec("r2-not-applicable")
    assert validate_record(base) == []
    failed = ("levels", "R0", "lanes", 1)
    for steps in [
        [((*failed, "waiver"), DELETE)],  # failed lane without waiver
        [((*failed, "outcome"), "PASS")],  # waived lane that passed
        [(("levels", "R0", "lanes", 1), {"lane": "slow-io", "outcome": "FAIL", "verdict_sha256": None, "config_sha256": "f" * 64}),
         (("levels", "R0", "lanes", 0, "waiver"), {"reason": "r", "plan_item": None, "expires_before": "9.0.0"})],
    ]:
        assert has(validate_record(edit(base, *steps)), "$.levels.R0.lanes", "pass-with-waivers"), steps
    # waivers claimed but no lane failed
    no_fail = edit(base, ((*failed, "outcome"), "PASS"), ((*failed, "waiver"), DELETE))
    assert has(validate_record(no_fail), "$.levels.R0.lanes", "pass-with-waivers")
    # an unrelated passing lane may stay without a waiver (the golden), and an ERROR outcome counts as failed
    assert validate_record(edit(base, ((*failed, "outcome"), "ERROR"))) == []


def test_empty_statuses_carry_nothing(rec):
    base = rec("r2-not-applicable")
    assert validate_record(base) == []
    for path, value in [(("lanes",), [dict(rec("r2-pending")["levels"]["R0"]["lanes"][0])]), (("toolset",), "t1"),
                        (("judged_commit",), "a" * 40), (("waiver",), {"id": "x", "reason": "r"})]:
        out = validate_record(edit(base, (("levels", "R3", *path), value)))
        assert has(out, "$.levels.R3", "carries no lanes"), path
    unknown = rec("adopted-unknown")
    assert has(validate_record(edit(unknown, (("levels", "R0", "toolset"), "t1"))), "$.levels.R0", "carries no lanes")


def test_waived_level_names_its_waiver(rec):
    base = rec("r2-waived")
    assert validate_record(base) == []
    assert has(validate_record(edit(base, (("levels", "R1", "waiver"), None))), "$.levels.R1.waiver", "names its waiver")


def test_r1_pass_needs_coverage_at_floor(rec):
    base = rec("r2-pending")
    cov = ("levels", "R1", "coverage")
    assert has(validate_record(edit(base, (cov, DELETE))), "$.levels.R1.coverage", "at or above")
    assert has(validate_record(edit(base, (cov + ("line_pct",), 99.9))), "$.levels.R1.coverage", "at or above")
    assert has(validate_record(edit(base, (cov + ("branch_pct",), 99.9))), "$.levels.R1.coverage", "at or above")
    exact = edit(base, (cov + ("line_pct",), 99.5), (cov + ("branch_pct",), 98.0), (cov + ("floor",), {"line": 99.5, "branch": 98.0}))
    assert validate_record(exact) == []  # exactly at the floor passes


def test_r3_pass_needs_passing_canaries(rec):
    base = rec("r2-pending")
    can = ("levels", "R3", "canaries")
    assert has(validate_record(edit(base, (can, DELETE))), "$.levels.R3.canaries", "at least one canary")
    assert has(validate_record(edit(base, (can, []))), "$.levels.R3.canaries", "at least one canary")
    assert has(validate_record(edit(base, (can + (0, "outcome"), "FAIL"))), "$.levels.R3.canaries", "all of them")


def test_coverage_and_canaries_belong_to_their_levels(rec):
    base = rec("r2-pending")
    cov = base["levels"]["R1"]["coverage"]
    can = base["levels"]["R3"]["canaries"]
    assert has(validate_record(edit(base, (("levels", "R0", "coverage"), cov))), "$.levels.R0.coverage", "only R1")
    assert has(validate_record(edit(base, (("levels", "R3", "coverage"), cov))), "$.levels.R3.coverage", "only R1")
    assert has(validate_record(edit(base, (("levels", "R1", "canaries"), can))), "$.levels.R1.canaries", "only R3")
    assert has(validate_record(edit(base, (("qualifications",), {"q": dict(base["levels"]["R0"], coverage=cov)}))),
               "$.qualifications.q.coverage", "only R1")


def test_review_rules(rec):
    base = rec("r2-pending")
    rv = ("levels", "review")
    for steps in [[(rv + ("range",), None)], [(rv + ("reviewer_role",), None)], [(rv + ("document_sha256",), None)]]:
        assert has(validate_record(edit(base, *steps)), "$.levels.review", "accept needs"), steps
    missing = edit(base, (rv + ("status",), "missing"))
    assert has(validate_record(missing), "$.levels.review", "missing carries none")
    assert validate_record(rec("adopted-unknown")) == []


# ---- R2 --------------------------------------------------------------------------------------------------------
def r2(rec, name, *steps):
    return validate_record(edit(rec(f"r2-{name}"), *[((*R2, *p), v) for p, v in steps]))


def test_complete_statuses_need_their_data(rec):
    for key in ("toolset", "judged_commit", "scope", "campaign", "candidates", "buckets", "survivors"):
        out = r2(rec, "survivors", ((key,), None))
        assert has(out, "$.levels.R2", "needs"), key


def test_complete_campaign_arithmetic(rec):
    assert has(r2(rec, "survivors", (("candidates", "executed"), 500)), "$.levels.R2.candidates", "every candidate ran")
    assert has(r2(rec, "survivors", (("buckets", "killed"), 1)), "$.levels.R2.buckets", "add up")
    assert has(r2(rec, "survivors", (("campaign", "closeout_exit"), 3)), "$.levels.R2.campaign.closeout_exit", "exit 0 or 1")
    assert has(r2(rec, "survivors", (("buckets", "survived"), 30), (("buckets", "killed"), 520)), "$.levels.R2.survivors", "add up")


def test_status_follows_survivor_counts(rec):
    assert has(r2(rec, "survivors", (("survivors", "open"), 0), (("survivors", "resolved"), 27),
                  (("survivors", "listed"), [dict(s, disposition="resolved", resolved_by="cgprofile-v2.0.1") if s["disposition"] == "open" else s
                                               for s in rec("r2-survivors")["levels"]["R2"]["survivors"]["listed"]])),
               "$.levels.R2.status", "at least one open")
    assert has(r2(rec, "pass-with-waivers", (("status",), "pass")), "$.levels.R2.status", "pass needs no")
    assert has(r2(rec, "pass", (("status",), "pass-with-waivers")), "$.levels.R2.status", "pass-with-waivers needs")
    assert has(r2(rec, "survivors", (("status",), "pass-with-waivers")), "$.levels.R2.status", "pass-with-waivers needs")
    assert has(r2(rec, "survivors", (("status",), "pass")), "$.levels.R2.status", "pass needs")


def test_resolved_only_survivors_make_a_pass(rec):
    base = rec("r2-survivors")
    listed = [dict(s, disposition="resolved", resolved_by="cgprofile-v2.0.1", reason=None) for s in base["levels"]["R2"]["survivors"]["listed"]]
    out = r2(rec, "survivors", (("status",), "pass"), (("survivors", "open"), 0), (("survivors", "equivalent"), 0),
             (("survivors", "accepted"), 0), (("survivors", "resolved"), 31), (("survivors", "listed"), listed))
    assert out == []


def test_listed_survivors_must_match_their_counts(rec):
    assert has(r2(rec, "survivors", (("survivors", "listed"), [])), "$.levels.R2.survivors", "all listed")
    assert has(r2(rec, "survivors", (("survivors", "by_file"), {"src/a.py": 1})), "$.levels.R2.survivors", "all listed")
    assert has(r2(rec, "survivors", (("survivors", "sidecar_sha256"), "1" * 64)), "$.levels.R2.survivors", "all listed")
    swapped = (("survivors", "listed", 0, "disposition"), "accepted")
    out = r2(rec, "survivors", swapped, (("survivors", "listed", 0, "reason"), "accepted risk"))
    assert has(out, "$.levels.R2.survivors.open", "count differs")
    assert has(out, "$.levels.R2.survivors.accepted", "count differs")


def test_listed_survivor_field_rules(rec):
    assert has(r2(rec, "survivors", (("survivors", "listed", 0, "resolved_by"), "cgprofile-v2.0.1")),
               "$.levels.R2.survivors.listed[0].resolved_by", "exactly when")
    assert has(r2(rec, "survivors", (("survivors", "listed", 27, "reason"), None)),
               "$.levels.R2.survivors.listed[27].reason", "states why")
    ok = r2(rec, "survivors", (("survivors", "listed", 0, "reason"), "kept open on purpose"))
    assert ok == []


def test_more_than_200_survivors_collapse_to_per_file_counts(rec):
    base = rec("r2-survivors")
    survivors = {"open": 250, "equivalent": 0, "accepted": 0, "resolved": 0, "listed": [],
                 "by_file": {"src/a.py": 150, "src/b.py": 100}, "sidecar_sha256": "3" * 64}
    buckets = {"killed": 300, "survived": 250, "equivalent": 0, "hung": 2, "budget_exceeded": 3, "crashed": 2}
    big = edit(base, ((*R2, "survivors"), survivors), ((*R2, "buckets"), buckets))
    assert validate_record(big) == []
    for patch in [{"listed": base["levels"]["R2"]["survivors"]["listed"][:1]}, {"by_file": {"src/a.py": 1}}, {"sidecar_sha256": None}]:
        out = validate_record(edit(big, ((*R2, "survivors"), dict(survivors, **patch))))
        assert has(out, "$.levels.R2.survivors", "collapse"), patch
    over_listed = edit(big, ((*R2, "survivors", "listed"), [base["levels"]["R2"]["survivors"]["listed"][0]] * 201))
    assert any(v.path == "$.levels.R2.survivors.listed" for v in validate_record(over_listed))


def test_incomplete_and_error(rec):
    assert has(r2(rec, "incomplete", (("campaign", "closeout_exit"), 2)), "$.levels.R2.campaign.closeout_exit", "exit 3")
    assert has(r2(rec, "error", (("campaign", "closeout_exit"), 3)), "$.levels.R2.campaign.closeout_exit", "exit 2")
    assert has(r2(rec, "incomplete", (("campaign",), None)), "$.levels.R2", "needs the campaign")
    assert has(r2(rec, "error", (("candidates",), None)), "$.levels.R2", "needs the campaign")
    assert has(r2(rec, "incomplete", (("candidates", "executed"), 999)), "$.levels.R2.candidates", "exceeds total")


def test_partial_needs_both_kinds_of_lane(rec):
    assert has(r2(rec, "partial", (("lanes",), [])), "$.levels.R2.lanes", "partial needs")
    assert has(r2(rec, "partial", (("lanes", 1, "state"), "complete")), "$.levels.R2.lanes", "partial needs")
    assert has(r2(rec, "partial", (("lanes", 0, "state"), "not-runnable")), "$.levels.R2.lanes", "partial needs")


def test_waived_r2_names_its_waiver_and_nothing_else_may(rec):
    assert has(r2(rec, "waived", (("waiver",), None)), "$.levels.R2.waiver", "names its waiver")
    assert has(r2(rec, "pending", (("waiver",), {"id": "x", "reason": "r"})), "$.levels.R2", "carries no campaign")
    assert has(r2(rec, "survivors", (("waiver",), {"id": "x", "reason": "r"})), "$.levels.R2.waiver", "only a waived")


def test_pending_like_statuses_carry_no_campaign_data(rec):
    camp = rec("r2-survivors")["levels"]["R2"]["campaign"]
    for name in ("pending", "not-applicable"):
        assert has(r2(rec, name, (("campaign",), camp)), "$.levels.R2", "carries no campaign"), name
    for key, value in [("score", 0.5), ("worker_counts", {"1": 2}), ("lanes", [{"lane": "x", "state": "complete"}])]:
        assert has(r2(rec, "pending", ((key,), value)), "$.levels.R2", "carries no campaign"), key
    unknown = edit(rec("adopted-unknown"), ((*R2, "score"), 0.5))
    assert has(validate_record(unknown), "$.levels.R2", "carries no campaign")


def test_scope_rules(rec):
    assert has(r2(rec, "survivors", (("scope", "base_tag"), None)), "$.levels.R2.scope", "names its base tag")
    assert has(r2(rec, "survivors", (("scope", "kind"), "whole-source")), "$.levels.R2.scope", "whole-source")
    assert has(r2(rec, "survivors", (("scope", "kind"), "whole-source"), (("scope", "base_tag"), None)), "$.levels.R2.scope", "anchored")
    assert r2(rec, "survivors", (("scope", "kind"), "whole-source"), (("scope", "base_tag"), None), (("scope", "anchored"), True)) == []


def test_history_entries_carry_no_history_of_their_own(rec):
    base = rec("r2-error")
    nested = edit(base, ((*R2, "history", 0, "history"), [base["levels"]["R2"]["history"][0]]))
    assert has(validate_record(nested), "$.levels.R2.history[0].history", "no history of its own")
    too_many = edit(base, ((*R2, "history"), [base["levels"]["R2"]["history"][0]] * 6))
    assert has(validate_record(too_many), "$.levels.R2.history", "more than 5")


# ---- perf and machine classes inside a record -----------------------------------------------------------------
def test_record_perf_and_machine_class_semantics(rec):
    base = rec("r2-pending")
    assert has(validate_record(edit(base, (("perf", 0, "machine_classes"), {"m7": 1}))), "$.perf[0].machine_classes.m7", "not declared")
    dup = edit(base, (("machine_classes",), base["machine_classes"] * 2))
    assert has(validate_record(dup), "$.machine_classes[1].id", "duplicate")
    bad = edit(base, (("machine_classes", 0, "speed", "geekbench_major"), 6))
    assert has(validate_record(bad), "$.machine_classes[0].speed.geekbench_major", "major part")


# ---- size and privacy -----------------------------------------------------------------------------------------
def test_record_size_limit_is_enforced(rec):
    base = rec("r2-pending")
    perf = base["perf"][0]
    bloated = edit(base, (("perf",), [perf] * 64))
    assert has(validate_record(bloated), "$", "over the 65536 byte limit")
    # just under the limit stays valid: 30 perf entries are about 60 KB
    assert validate_record(edit(base, (("perf",), [perf] * 30))) == []


def test_privacy_is_enforced_on_records(rec):
    base = rec("r2-pending")
    out = validate_record(edit(base, (("machine_classes", 0, "cpu_model"), "Xeon @ 2.4GHz host-9"))
                          , hosts=["host-9"])
    assert has(out, "$.machine_classes[0].cpu_model", "privacy rule host")
    leak = validate_record(edit(base, (("levels", "R0", "lanes", 0, "lane"), "ghp_" + "a" * 30)))
    assert has(leak, "$.levels.R0.lanes[0].lane", "privacy rule token-prefix")
    review_prose = validate_record(edit(base, (("levels", "review", "reviewer_role"), "see /home/x/review.md")))
    assert any(v.path == "$.levels.review.reviewer_role" for v in review_prose)
