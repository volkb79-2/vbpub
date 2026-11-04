import pytest

from conftest import DELETE, edit, has
from rigor_record import validate_fragment
from rigor_record.validate import canonical_json, check_size

TABLE_SPEED = {"basis": "geekbench7-table", "geekbench_major": 7, "geekbench_version": None, "baseline_score": 2500,
               "single_core": 1650, "multi_core": None, "cpus_at_measurement": None, "measured_on": None}
GOLDEN_FRAGMENTS = ["assay-r1-pass", "assay-r2-survivors", "run-gate-r0-r1", "run-gate-r2-assay", "run-gate-composite"]


@pytest.mark.parametrize("name", GOLDEN_FRAGMENTS)
def test_golden_fragments_validate_and_are_canonical(pf, name):
    doc = pf(name)
    assert validate_fragment(doc) == []
    text = (__import__("conftest").GOLDEN / "fragments" / f"{name}.json").read_text()
    assert text == canonical_json(doc)
    assert check_size("fragment", text) == []


def test_kind_must_be_known():
    assert has(validate_fragment({"kind": "other"}), "$.kind", "assay-lane, run-gate-lane or run-gate-composite")
    assert has(validate_fragment([]), "$.kind", "assay-lane")


# ---- schema level: each wrong value is named at its path ------------------------------------------------------
ASSAY_BAD = [
    (("schema",), "urn:vbpub:rigor-fragment:2", "$.schema"),
    (("judged_commit",), "abc", "$.judged_commit"),
    (("judged_commit",), "A" * 40, "$.judged_commit"),
    (("project_tree",), None, "$.project_tree"),
    (("outcome",), "pass", "$.outcome"),
    (("verified",), False, "$.verified"),
    (("lane",), "bad lane", "$.lane"),
    (("lane",), "../x", "$.lane"),
    (("judge", "judge_identity"), "judge/14", "$.judge.judge_identity"),
    (("judge", "version"), "12", "$.judge.version"),
    (("coverage", "line_pct"), 100.5, "$.coverage.line_pct"),
    (("verdict_sha256",), "G" * 64, "$.verdict_sha256"),
]


@pytest.mark.parametrize("path,value,where", ASSAY_BAD)
def test_assay_fragment_schema_violations(pf, path, value, where):
    doc = pf("assay-r1-pass")
    out = validate_fragment(edit(doc, (path, value)))
    assert any(v.path == where for v in out), out


def test_assay_unexpected_and_missing_keys(pf):
    doc = pf("assay-r1-pass")
    assert has(validate_fragment(edit(doc, (("host",), "x"))), "$.host", "unexpected key")
    assert has(validate_fragment(edit(doc, (("judge",), DELETE))), "$", "missing required key 'judge'")


def test_assay_survivor_fields_are_restricted(pf):
    base = pf("assay-r2-survivors")
    for field, bad in [("path", "/abs/path.py"), ("path", "../up.py"), ("line", 0), ("operator", "Compare Swap"),
                       ("first_failing_test", "tests/a b.py::t"), ("candidate_id", "x" * 200)]:
        doc = edit(base, (("r2", "survivors", "listed", 0, field), bad))
        assert any(v.path == f"$.r2.survivors.listed[0].{field}" for v in validate_fragment(doc)), (field, bad)


def test_claims_inputs_and_judged_paths_are_ordered(pf):
    base = pf("assay-r2-survivors")
    claims = base["claims"]
    assert has(validate_fragment(edit(base, (("claims",), claims[::-1]))), "$.claims", "ordered R0..R3")
    assert has(validate_fragment(edit(base, (("claims",), claims + claims[:1]))), "$.claims", "ordered R0..R3")
    for key in ("inputs", "judged_paths"):
        out = validate_fragment(edit(base, ((key,), ["src/b", "src/a"])))
        assert has(out, f"$.{key}", "sorted and distinct"), key


def test_composite_members_are_distinct_and_exclude_the_sequence(pf):
    base = pf("run-gate-composite")
    assert has(validate_fragment(edit(base, (("members",), ["r0-r1", "r0-r1"]))), "$.members", "distinct lanes")
    assert has(validate_fragment(edit(base, (("members",), ["r0-r1", base["lane"]]))), "$.members", "distinct lanes")


def test_assay_fragment_may_not_carry_resource_numbers(pf):
    doc = edit(pf("assay-r2-survivors"), (("r2", "rss_peak_bytes"), 5))
    assert has(validate_fragment(doc), "$.r2.rss_peak_bytes", "unexpected key")


# ---- assay semantics ------------------------------------------------------------------------------------------
def test_outcome_and_reason_agree(pf):
    assert has(validate_fragment(edit(pf("assay-r1-pass"), (("reason",), "X"))), "$.reason", "exactly when")
    assert has(validate_fragment(edit(pf("assay-r2-survivors"), (("reason",), None))), "$.reason", "exactly when")


R2 = ("r2",)


def r2edit(pf, *steps):
    return edit(pf("assay-r2-survivors"), *[(R2 + p, v) for p, v in steps])


def test_r2_counts_must_be_consistent(pf):
    cases = [
        ([(("candidates", "executed"), 600)], "$.r2.candidates.executed", "exceeds total"),
        ([(("candidates", "resumed"), 600)], "$.r2.candidates.resumed", "exceeds executed"),
        ([(("buckets", "killed"), 518)], "$.r2.buckets", "add up"),
        ([(("worker_counts",), {"1": 300, "2": 300})], "$.r2.worker_counts", "exceed"),
        ([(("profile_status_counts", "MEASURED"), 600)], "$.r2.profile_status_counts", "exceed"),
        ([(("survivors", "total"), 30)], "$.r2.survivors.total", "buckets.survived"),
        ([(("survivors", "listed"), [])], "$.r2.survivors.listed", "min(total, 500)"),
        ([(("survivors", "sidecar_sha256"), "0" * 64)], "$.r2.survivors.sidecar_sha256", "exceeds 500"),
        ([(("campaign", "started_at"), "2026-11-05T00:00:00Z")], "$.r2.campaign", "precedes"),
    ]
    for steps, where, fragment in cases:
        assert has(validate_fragment(r2edit(pf, *steps)), where, fragment), (where, fragment)


def test_duplicate_survivor_ids_refused(pf):
    doc = pf("assay-r2-survivors")
    dup = doc["r2"]["survivors"]["listed"][0]["candidate_id"]
    doc = edit(doc, (("r2", "survivors", "listed", 1, "candidate_id"), dup))
    assert has(validate_fragment(doc), "$.r2.survivors.listed", "duplicate candidate_id")


def test_more_than_500_survivors_need_a_sidecar_and_a_capped_list(pf):
    doc = pf("assay-r2-survivors")
    item = doc["r2"]["survivors"]["listed"][0]
    listed = [dict(item, candidate_id=f"cand-{i:05d}") for i in range(500)]
    big = edit(doc, (("r2", "buckets", "survived"), 600), (("r2", "buckets", "killed"), 450),
               (("r2", "candidates", "total"), 1100), (("r2", "candidates", "executed"), 1100),
               (("r2", "worker_counts"), {"1": 1100}), (("r2", "profile_status_counts"), {"MEASURED": 0, "PARTIAL": 1100, "NO_MEASUREMENT": 0}),
               (("r2", "buckets", "hung"), 25), (("r2", "buckets", "budget_exceeded"), 25),
               (("r2", "buckets", "crashed"), 0),
               (("r2", "survivors", "total"), 600), (("r2", "survivors", "listed"), listed),
               (("r2", "survivors", "sidecar_sha256"), "9" * 64))
    assert validate_fragment(big) == []
    assert has(validate_fragment(edit(big, (("r2", "survivors", "sidecar_sha256"), None))),
               "$.r2.survivors.sidecar_sha256", "exceeds 500")
    too_many = edit(big, (("r2", "survivors", "listed"), listed + [dict(item, candidate_id="cand-extra")]))
    assert has(validate_fragment(too_many), "$.r2.survivors.listed", "")


def test_campaign_may_be_unknown(pf):
    assert validate_fragment(r2edit(pf, (("campaign",), None))) == []


# ---- run-gate fragments ---------------------------------------------------------------------------------------
def test_run_gate_schema_violations(pf):
    base = pf("run-gate-r0-r1")
    for path, value, where in [
        (("exit",), 256, "$.exit"), (("exit",), -1, "$.exit"), (("duration_seconds",), -1, "$.duration_seconds"),
        (("io_sync",), "dur able", "$.io_sync"),
        (("image", "image_id"), "sha256:abc", "$.image.image_id"),
        (("image", "ref"), "UPPER:x", "$.image.ref"),
        (("image", "labels"), {"k": "has space"}, "$.image.labels.k"),
        (("run_gate", "wheel_sha256"), "abc", "$.run_gate.wheel_sha256"),
        (("started_at",), "2026-11-04 07:00:00", "$.started_at"),
        (("machine_classes", 0, "cpu_model"), "CPU /home/x", "$.machine_classes[0].cpu_model"),
        (("machine_classes", 0, "cpus_available"), 0, "$.machine_classes[0].cpus_available"),
        (("machine_classes", 0, "id"), "host1", "$.machine_classes[0].id"),
        (("machine_classes", 0, "speed", "geekbench_major"), 5, "$.machine_classes[0].speed.geekbench_major"),
        (("machine_classes", 0, "speed", "geekbench_version"), "5.4.0", "$.machine_classes[0].speed"),
        (("perf", 0, "subject"), "host", "$.perf[0].subject"),
        (("perf", 0, "level"), "R9", "$.perf[0].level"),
        (("perf", 0, "window"), "yesterday", "$.perf[0].window"),
        (("perf", 0, "normalisation", "geekbench_major"), 5, "$.perf[0]"),
        (("perf", 0, "host_psi_some_avg", "cpu"), 101, "$.perf[0].host_psi_some_avg.cpu"),
        (("perf", 0, "metrics", "Bad Name"), {"unavailable": True, "reason": "X"}, "$.perf[0].metrics.Bad Name"),
    ]:
        out = validate_fragment(edit(base, (path, value)))
        assert any(v.path.startswith(where) for v in out), (path, value, out)


def test_rolling_window_object_is_accepted_and_bounded(pf):
    win = {"runs": 200, "first_commit": "1" * 40, "last_commit": "2" * 40}
    assert validate_fragment(edit(pf("run-gate-r0-r1"), (("perf", 0, "window"), win))) == []
    assert validate_fragment(edit(pf("run-gate-r0-r1"), (("perf", 0, "window"), dict(win, runs=201)))) != []


def test_run_gate_time_order(pf):
    doc = edit(pf("run-gate-r0-r1"), (("completed_at",), "2026-11-04T06:00:00Z"))
    assert has(validate_fragment(doc), "$.completed_at", "precedes")


def test_machine_class_rules(pf):
    base = pf("run-gate-r2-assay")
    dup = edit(base, (("machine_classes", 1, "id"), "m1"))
    assert has(validate_fragment(dup), "$.machine_classes[1].id", "duplicate")
    major = edit(base, (("machine_classes", 0, "speed", "geekbench_major"), 6))
    assert has(validate_fragment(major), "$.machine_classes[0].speed.geekbench_major", "major part")
    assert has(validate_fragment(major), "$.machine_classes[0].speed.basis", "own Geekbench major")
    ok_table = edit(base, (("machine_classes", 0, "speed"), TABLE_SPEED))
    assert validate_fragment(ok_table) == []
    # an unavailable speed is a valid class (m3 in the golden)
    assert base["machine_classes"][1]["speed"]["unavailable"] is True


# ---- perf semantics -------------------------------------------------------------------------------------------
def metric(doc, name):
    return ("perf", 0, "metrics", name)


def test_distribution_null_rules_follow_min_n(pf):
    base = pf("run-gate-r2-assay")
    # n=40 memory distribution: p90 present, p98/p99 null. Filling p98 with a number is a lie.
    doc = edit(base, (metric(base, "memory_peak_bytes") + ("p98",), 5.0))
    assert has(validate_fragment(doc), "$.perf[0].metrics.memory_peak_bytes.p98", "must be null")
    doc = edit(base, (metric(base, "wall_seconds") + ("p99",), None))
    assert has(validate_fragment(doc), "$.perf[0].metrics.wall_seconds.p99", "must be a number")
    doc = edit(base, (metric(base, "wall_seconds") + ("max",), None))
    assert has(validate_fragment(doc), "$.perf[0].metrics.wall_seconds.max", "max is null exactly")


def test_distribution_percentiles_must_not_decrease(pf):
    base = pf("run-gate-r2-assay")
    doc = edit(base, (metric(base, "wall_seconds") + ("p50",), 1e9))
    assert has(validate_fragment(doc), "$.perf[0].metrics.wall_seconds", "non-decreasing")


def test_metric_units_follow_the_metric_name(pf):
    base = pf("run-gate-r2-assay")
    cases = {"wall_seconds": "bytes", "wall_seconds_ref": "s", "rss_peak_bytes": "s", "cpu_per_wall": "s",
             "steal_fraction": "s", "phase_seconds.materialize": "ratio"}
    for name, wrong in cases.items():
        doc = edit(base, (metric(base, name) + ("unit",), wrong))
        assert any(v.path.endswith(f"{name}.unit") for v in validate_fragment(doc)), name
    ops = edit(base, (metric(base, "io_read_ops"), {"unit": "s", "n": 0, "n_excluded": {"contaminated": 0, "no_measurement": 0},
                                                  "p50": None, "p90": None, "p98": None, "p99": None, "max": None, "source": "x"}))
    assert any(v.path.endswith("io_read_ops.unit") for v in validate_fragment(ops))
    other = edit(base, (("perf", 0, "metrics", "mystery"), dict(base["perf"][0]["metrics"]["wall_seconds"])))
    assert validate_fragment(other) == []  # unknown names default to seconds


def test_normalisation_must_be_internally_consistent(pf):
    base = pf("run-gate-r2-assay")
    n = ("perf", 0, "normalisation")
    for steps in [
        [(n + ("reference_score",), 1000)],
        [(n + ("divisor",), "none")],
        [(n + ("reason",), "SOMETHING")],
        [(n + ("geekbench_major",), None)],
        [(("perf", 1, "normalisation", "reason"), None)],
        [(("perf", 1, "normalisation", "geekbench_major"), 7)],
        [(("perf", 1, "normalisation", "reference_score"), 2500)],
        [(("perf", 1, "normalisation", "divisor"), "single_core")],
    ]:
        out = validate_fragment(edit(base, *steps))
        assert any("normalisation" in v.path for v in out), steps


def test_reference_score_follows_the_machine_baseline(pf):
    base = pf("run-gate-r2-assay")
    out = validate_fragment(edit(base, (("machine_classes", 0, "speed", "baseline_score"), 3000)))
    assert has(out, "$.perf[0].normalisation.reference_score", "baseline_score of machine class m1")


def test_normalised_metric_without_a_basis_is_refused(pf):
    base = pf("run-gate-r2-assay")
    doc = edit(base, (("perf", 1, "metrics", "wall_seconds_ref"), dict(base["perf"][0]["metrics"]["wall_seconds_ref"])))
    assert has(validate_fragment(doc), "$.perf[1].metrics.wall_seconds_ref", "normalisation basis")


def test_majors_must_not_be_mixed_within_an_entry(pf):
    base = pf("run-gate-r2-assay")
    doc = edit(base, (("perf", 0, "machine_classes"), {"m1": 5, "m2": 1}),
               (("machine_classes",), base["machine_classes"] + [{
                   "id": "m2", "cpu_model": "Intel(R) Xeon(R) CPU E5-2680 v4 @ 2.40GHz", "cpus_available": 3.5,
                   "hypervisor": True, "speed": dict(TABLE_SPEED, basis="geekbench6-table", geekbench_major=6)}]))
    assert has(validate_fragment(doc), "$.perf[0].machine_classes.m2", "must not be mixed")
    same = edit(doc, (("machine_classes", 2, "speed"), TABLE_SPEED))
    assert validate_fragment(same) == []
    off_baseline = edit(same, (("machine_classes", 2, "speed", "baseline_score"), 2000))
    assert has(validate_fragment(off_baseline), "$.perf[0].normalisation.reference_score", "baseline_score of machine class m2")


def test_perf_may_only_name_declared_machine_classes(pf):
    doc = edit(pf("run-gate-r2-assay"), (("perf", 0, "machine_classes"), {"m9": 3}))
    assert has(validate_fragment(doc), "$.perf[0].machine_classes.m9", "not declared")


# ---- privacy is part of validity --------------------------------------------------------------------------------
def test_privacy_lint_runs_inside_validation(pf):
    doc = edit(pf("run-gate-r0-r1"), (("environment",), "tester-unified"))
    assert validate_fragment(doc, hosts=["tester-unified"]) != []
    out = validate_fragment(doc, hosts=["tester-unified"])
    assert has(out, "$.environment", "privacy rule host")
    assert has(validate_fragment(doc, users=["unified"]), "$.environment", "privacy rule user")


def test_privacy_hit_in_a_schema_valid_string_is_still_refused(pf):
    # "ghp_..." fits the identifier pattern (letters, digits, underscore is not allowed -> use mechanism token-like)
    doc = edit(pf("assay-r1-pass"), (("lane",), "ABCdefGHI123456789ABCdefGHI123456789ABCdef"))
    out = validate_fragment(doc)
    assert has(out, "$.lane", "privacy rule token-like")


def test_fragment_size_limit():
    big = "x" * (256 * 1024 + 1)
    assert check_size("fragment", big) != []
    assert check_size("fragment", big.encode()) != []
    assert check_size("fragment", "x" * (256 * 1024)) == []
    assert check_size("release-body", "x" * (8 * 1024 + 1))[0].message.startswith("release-body is")


def test_every_percentile_vector_satisfies_the_structural_distribution_rule():
    import json

    from conftest import GOLDEN
    from rigor_record.validate import _check_distribution

    vectors = json.loads((GOLDEN / "percentile-vectors.json").read_text())
    assert vectors["cases"]
    for case in vectors["cases"]:
        expect = case["expect"]
        size = len(case["values"]) if "values" in case else case["range"][1] - case["range"][0] + 1
        assert expect["n"] == size, case["id"]
        assert _check_distribution(expect, "$.d") == [], case["id"]
        assert all(expect[k] is None for k in ("p90", "p98", "p99") if expect["n"] < {"p90": 10, "p98": 50, "p99": 100}[k])
