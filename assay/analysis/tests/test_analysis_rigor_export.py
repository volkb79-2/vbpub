"""`assay analyze export`: a verified verdict becomes a rigor-fragment/1 judgment-fact fragment (RIGOR A-1)."""

from __future__ import annotations

import copy
import hashlib
import io
import json
import subprocess
from pathlib import Path

import pytest

from assay.candidate_identity import candidate_id_from_fields
from analysis.tests.analysis_support import JUDGE_VERDICT_FIXTURES
from analysis.tests.rigor_support import GOLDEN, golden, violations
from assay_analysis import cli as analysis_cli
from assay_analysis import rigor_export

LANE_FILE = 'schema_version = 2\n\n[lanes.package]\nscope = "S1"\nrigor = ["R0"]\nargv = ["pytest"]\n'
PROVENANCE = {"name": "assay", "version": "12.0.0", "artifact": "wheel", "digest_algorithm": "sha256",
              "digest": "c" * 64}
GIT = ["git", "-c", "user.name=t", "-c", "user.email=t@example.invalid", "-c", "commit.gpgsign=false"]


def _git(repo: Path, *args: str) -> str:
    return subprocess.run([*GIT, "-C", str(repo), *args], check=True, capture_output=True, text=True).stdout.strip()


class World:
    """A throwaway repository whose project directory holds the lane file, plus verdict builders."""

    def __init__(self, root: Path, project_dir: str = "proj"):
        self.repo = root / "repo"
        self.dir = self.repo / project_dir if project_dir else self.repo
        self.dir.mkdir(parents=True)
        subprocess.run(["git", "init", "-q", str(self.repo)], check=True)
        self.lane_file = self.dir / "assay.toml"
        self.lane_file.write_text(LANE_FILE, encoding="utf-8")
        _git(self.repo, "add", "-A")
        _git(self.repo, "commit", "-q", "-m", "init")
        self.commit = _git(self.repo, "rev-parse", "HEAD")
        self.out_dir = root / "out"
        self.out_dir.mkdir()

    def verdict(self, fixture: str, mutate=None, provenance=PROVENANCE, name: str = "verdict.json") -> Path:
        document = json.loads((JUDGE_VERDICT_FIXTURES / f"{fixture}.json").read_text(encoding="utf-8"))
        document["commit"] = self.commit
        if provenance is not None:
            document["judge_provenance"] = copy.deepcopy(provenance)
        if mutate is not None:
            mutate(document)
        path = self.out_dir / name
        path.write_text(json.dumps(document, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        return path

    def export(self, verdict: Path, **kwargs) -> dict:
        out = kwargs.pop("out", self.out_dir / "fragment.json")
        kwargs.setdefault("lane", "package")
        return rigor_export.export(verdict, self.commit, lane_file=self.lane_file, out=out, **kwargs)

    def fragment(self, verdict: Path, **kwargs) -> dict:
        result = self.export(verdict, **kwargs)
        return json.loads(Path(result["fragment"]).read_text(encoding="utf-8"))


@pytest.fixture
def world(tmp_path):
    return World(tmp_path)


# ---- the vendored contract itself ------------------------------------------------------------------------------
@pytest.mark.parametrize("name", ["assay-r1-pass", "assay-r2-survivors"])
def test_vendored_golden_fragments_validate_and_are_canonical(name):
    assert violations(golden(name)) == []
    assert (GOLDEN / f"{name}.json").read_text(encoding="utf-8") == rigor_export._canonical(golden(name))


@pytest.mark.parametrize("mutate,needle", [
    (lambda d: d.update(outcome="pass"), "outcome"),
    (lambda d: d.update(reason=None), "reason is null"),
    (lambda d: d.update(outcome="PASS"), "reason is null"),
    (lambda d: d["r2"]["survivors"].update(total=1), "survivors.total"),
    (lambda d: d["r2"]["candidates"].update(executed=1000), "executed exceeds total"),
    (lambda d: d["r2"]["candidates"].update(resumed=1000), "resumed exceeds executed"),
    (lambda d: d["r2"]["buckets"].update(killed=0), "buckets must add up"),
    (lambda d: d["r2"].update(worker_counts={"1": 10000}), "worker counts"),
    (lambda d: d["r2"].update(profile_status_counts={"MEASURED": 10000, "PARTIAL": 0, "NO_MEASUREMENT": 0}),
     "profile counts"),
    (lambda d: d["r2"]["survivors"].update(listed=[]), "lists min"),
    (lambda d: d["r2"]["survivors"].update(sidecar_sha256="a" * 64), "sidecar"),
    (lambda d: d["r2"]["survivors"]["listed"].append(d["r2"]["survivors"]["listed"][0]), "lists min"),
    (lambda d: d["r2"]["survivors"]["listed"][1].update(candidate_id="cand-0000"), "duplicate"),
    (lambda d: d["r2"]["campaign"].update(started_at="2027-01-01T00:00:00Z"), "completed_at precedes"),
    (lambda d: d["judge"].update(version="/home/x"), "version"),
    (lambda d: d["r2"]["survivors"]["listed"][0].update(path="a/10.0.0.1.py"), "privacy"),
    (lambda d: d["r2"]["survivors"]["listed"][0].update(first_failing_test="::1"), "privacy"),
    (lambda d: d["r2"]["survivors"]["listed"][0].update(first_failing_test="a.py::ab:cd:ef:01:02:03:04:05"),
     "privacy"),
    (lambda d: d["r2"]["survivors"]["listed"][0].update(path="a/tmp/b.py"), "privacy"),
])
def test_the_contract_checker_names_each_violation(mutate, needle):
    document = copy.deepcopy(golden("assay-r2-survivors"))
    mutate(document)
    found = violations(document)
    assert found and any(needle in message for message in found), found


def test_the_contract_checker_walks_lists_and_scalars():
    document = copy.deepcopy(golden("assay-r2-survivors"))
    document["r2"]["campaign"] = None
    document["canary"] = {"mechanism": "uncovered-line", "targets": ["a.py", "b.py"], "outcome": "PASS"}
    document["claims"].append({"rigor": "R3", "outcome": "PASS"})
    assert violations(document) == []
    document["canary"]["targets"][1] = "x/tmp/y.py"
    assert any("privacy" in message for message in violations(document))


# ---- export shape ----------------------------------------------------------------------------------------------
def test_r1_pass_exports_a_valid_fragment_with_the_documented_fields(world):
    verdict = world.verdict("r1_pass")
    fragment = world.fragment(verdict)
    assert violations(fragment) == []
    assert set(fragment) == set(golden("assay-r1-pass"))
    assert fragment["kind"] == "assay-lane" and fragment["project"] == "proj" and fragment["lane"] == "package"
    assert fragment["judged_commit"] == world.commit
    assert fragment["project_tree"] == _git(world.repo, "rev-parse", f"{world.commit}:proj")
    assert fragment["outcome"] == "PASS" and fragment["reason"] is None and fragment["verified"] is True
    assert fragment["verdict_sha256"] == hashlib.sha256(verdict.read_bytes()).hexdigest()
    assert fragment["judge"] == {"version": "12.0.0", "artifact_sha256": "c" * 64,
                                 "judge_identity": rigor_export.JUDGE_IDENTITY_LABEL,
                                 "verdict_schema": "urn:assay:schema:verdict:18"}
    # branches unavailable in this fixture: pct 0, floor 0 -- the fragment cannot express "unknown"
    assert fragment["coverage"] == {"line_pct": 100.0, "branch_pct": 0.0,
                                    "floor": {"line": 100.0, "branch": 0}, "excluded_lines": 0}
    assert fragment["canary"] is None and fragment["r2"] is None


def test_export_is_byte_deterministic_and_reports_a_summary(world):
    verdict = world.verdict("r1_pass")
    first = world.export(verdict, out=world.out_dir / "a.json")
    second = world.export(verdict, out=world.out_dir / "b.json")
    assert (world.out_dir / "a.json").read_bytes() == (world.out_dir / "b.json").read_bytes()
    assert first["fragment_sha256"] == second["fragment_sha256"] == hashlib.sha256(
        (world.out_dir / "a.json").read_bytes()).hexdigest()
    assert (first["outcome"], first["reason"]) == ("PASS", None) and "survivor_sidecar" not in first


def test_config_digest_covers_the_lane_declaration_only(world):
    first = world.fragment(world.verdict("r1_pass"), out=world.out_dir / "1.json")["config_sha256"]
    expected = hashlib.sha256(rigor_export._canonical({
        "schema_version": 2, "lane": {"scope": "S1", "rigor": ["R0"], "argv": ["pytest"]}}).encode()).hexdigest()
    assert first == expected
    world.lane_file.write_text(LANE_FILE + '\n[lanes.other]\nscope = "S1"\nrigor = ["R0"]\nargv = ["x"]\n',
                               encoding="utf-8")
    assert world.fragment(world.verdict("r1_pass"), out=world.out_dir / "2.json")["config_sha256"] == first
    world.lane_file.write_text(LANE_FILE.replace('"pytest"', '"pytest", "-q"'), encoding="utf-8")
    assert world.fragment(world.verdict("r1_pass"), out=world.out_dir / "3.json")["config_sha256"] != first


def test_branch_coverage_is_reported_against_the_same_floor(world):
    fragment = world.fragment(world.verdict("r1_fail_uncovered_branches"))
    assert violations(fragment) == []
    assert fragment["outcome"] == "FAIL" and fragment["reason"] == "UNCOVERED_BRANCHES"
    assert fragment["coverage"]["branch_pct"] == 50.0
    assert fragment["coverage"]["floor"] == {"line": 100.0, "branch": 100.0}


def test_reported_branches_with_no_arcs_count_as_fully_covered(world):
    def reported(document):
        coverage = next(c for c in document["claims"] if c["rigor"] == "R1")["coverage"]
        coverage["branch_capability"] = "reported"

    fragment = world.fragment(world.verdict("r1_pass", reported))
    assert fragment["coverage"]["branch_pct"] == 100.0 and fragment["coverage"]["floor"]["branch"] == 100.0


def test_excluded_lines_are_summed(world):
    fragment = world.fragment(world.verdict("r1_fail_excluded_lines"))
    assert fragment["outcome"] == "FAIL" and fragment["coverage"]["excluded_lines"] >= 1


def test_require_branch_raises_the_branch_floor_even_when_unavailable(world):
    def require(document):
        document["judgment"]["r1"]["require_branch"] = True

    fragment = world.fragment(world.verdict("r1_pass", require))
    assert fragment["coverage"]["floor"]["branch"] == 100.0


def test_a_non_judged_r1_exports_null_coverage_and_an_error_outcome(world):
    fragment = world.fragment(world.verdict("r1_no_measurement_empty_coverage"))
    assert violations(fragment) == []
    assert fragment["coverage"] is None and fragment["outcome"] == "ERROR"
    assert fragment["reason"] == "EMPTY_COVERAGE"


@pytest.mark.parametrize("fixture,outcome", [
    ("r3_pass", "PASS"), ("r3_fail_canary_survived_wrong_reason", "FAIL"),
    ("r3_inconclusive_canary_inconclusive", "ERROR")])
def test_canary_facts_and_outcome_mapping(world, fixture, outcome):
    fragment = world.fragment(world.verdict(fixture))
    assert violations(fragment) == []
    assert fragment["canary"]["outcome"] == outcome and fragment["outcome"] == outcome
    assert fragment["canary"]["mechanism"] and fragment["canary"]["targets"]
    assert (fragment["reason"] is None) == (outcome == "PASS")


# ---- R2 --------------------------------------------------------------------------------------------------------
def test_r2_counts_survivors_and_score(world):
    fragment = world.fragment(world.verdict("r2_fail_mutants_survived"))
    assert violations(fragment) == []
    r2 = fragment["r2"]
    assert r2["candidates"] == {"total": 2, "executed": 2, "resumed": 0}
    assert r2["buckets"] == {"killed": 1, "survived": 1, "equivalent": 0, "hung": 0, "budget_exceeded": 0,
                             "crashed": 0}
    assert r2["score"] == 0.5 and r2["campaign"] is None
    assert r2["worker_counts"] == {"1": 2}
    assert r2["profile_status_counts"] == {"MEASURED": 1, "PARTIAL": 1, "NO_MEASUREMENT": 0}
    assert r2["survivors"]["total"] == 1 and r2["survivors"]["sidecar_sha256"] is None
    survivor = r2["survivors"]["listed"][0]
    assert (survivor["path"], survivor["line"], survivor["operator"]) == ("pkg/checks.py", 30, "python:compare-swap")
    assert survivor["first_failing_test"] is None


def test_the_key_set_matches_the_golden_and_carries_no_resource_numbers(world):
    r2 = world.fragment(world.verdict("r2_fail_mutants_survived"))["r2"]
    assert set(r2) == set(golden("assay-r2-survivors")["r2"])
    assert set(r2["survivors"]["listed"][0]) == set(golden("assay-r2-survivors")["r2"]["survivors"]["listed"][0])

    def keys(node):
        if isinstance(node, dict):
            for key, value in node.items():
                yield key
                yield from keys(value)
        elif isinstance(node, list):
            for value in node:
                yield from keys(value)

    banned = ("rss", "cpu", "memory", "wall", "bytes", "seconds", "io_", "psi", "steal")
    assert not [k for k in keys(r2) if any(word in k.lower() for word in banned)]


def _mutant(template, path, line, number, test="unset", operator="python:compare-swap"):
    """A schema-valid native mutant outcome whose candidate_id is the real digest of its identity inputs."""
    entry = copy.deepcopy(template)
    entry.update(path=path, lineno=line, start_byte=10 * number, end_byte=10 * number + 1, operator=operator,
                 mutated_file_sha256=hashlib.sha256(f"mutated-{number}".encode()).hexdigest())
    entry["candidate_id"] = candidate_id_from_fields(
        path=path, source_sha256=entry["source_sha256"], start_byte=entry["start_byte"], end_byte=entry["end_byte"],
        mutated_file_sha256=entry["mutated_file_sha256"], operator=operator)
    if test != "unset":
        entry["first_failing_test"] = test
    return entry


def _r2_claim(document):
    return next(c for c in document["claims"] if c["rigor"] == "R2")["mutation"]


def test_a_survivor_takes_the_failing_test_of_the_nearest_kill_in_its_file(world):
    def shape(document):
        mutation = _r2_claim(document)
        kill, alive = mutation["killed"][0], mutation["survived"][0]
        mutation["killed"] = [
            _mutant(kill, "pkg/checks.py", 10, 1, "tests/t.py::far"),
            _mutant(kill, "pkg/checks.py", 28, 2, "tests/t.py::near"),
            _mutant(kill, "pkg/checks.py", 32, 3, "tests/t.py::tie"),
            _mutant(kill, "pkg/checks.py", 29, 4, None),
            _mutant(kill, "pkg/other.py", 30, 5, "tests/t.py::other-file"),
        ]
        mutation["survived"] = [_mutant(alive, "pkg/checks.py", 30, 6)]
        mutation["total"] = mutation["candidate_count"] = 6
        mutation["candidate_ids"] = [m["candidate_id"] for m in mutation["killed"] + mutation["survived"]]
        document["judgment"]["r2"]["max_mutants"] = 50

    fragment = world.fragment(world.verdict("r2_fail_mutants_survived", shape))
    assert violations(fragment) == []
    assert fragment["r2"]["survivors"]["listed"][0]["first_failing_test"] == "tests/t.py::near"
    assert fragment["r2"]["score"] == round(5 / 6, 4)


def test_a_failing_test_id_the_schema_cannot_hold_is_reported_as_unknown():
    kill = {"path": "a.py", "lineno": 3, "candidate_id": "k" * 64, "first_failing_test": "t.py::with space"}
    alive = {"path": "a.py", "lineno": 4}
    assert rigor_export._nearest_failing_test(alive, [kill]) is None
    assert rigor_export._nearest_failing_test(alive, []) is None
    assert rigor_export._nearest_failing_test(alive, [{**kill, "first_failing_test": None}]) is None
    assert rigor_export._nearest_failing_test(alive, [{**kill, "first_failing_test": "t.py::ok"}]) == "t.py::ok"


def test_r2_without_decided_mutants_has_a_null_score(world):
    fragment = world.fragment(world.verdict("r2_inconclusive_no_mutants"))
    assert violations(fragment) == []
    assert fragment["r2"]["score"] is None and fragment["outcome"] == "ERROR"
    assert fragment["r2"]["survivors"] == {"total": 0, "listed": [], "sidecar_sha256": None}


def test_r2_pass_has_no_survivors(world):
    fragment = world.fragment(world.verdict("r2_pass"))
    assert violations(fragment) == [] and fragment["r2"]["score"] == 1.0 and fragment["outcome"] == "PASS"


def _survivors_over_the_listing_limit(world, count):
    def shape(document):
        mutation = next(c for c in document["claims"] if c["rigor"] == "R2")["mutation"]
        template = mutation["survived"][0]
        mutation["killed"] = []
        mutation["survived"] = [_mutant(template, "pkg/checks.py", 1 + n, n) for n in range(count)]
        mutation["total"] = mutation["candidate_count"] = count
        mutation["candidate_ids"] = [m["candidate_id"] for m in mutation["survived"]]
        document["judgment"]["r2"]["max_mutants"] = count + 10
        document["judgment"]["r2"]["worker_counts"] = {"1": count}
        document["judgment"]["r2"]["profile"]["status_counts"] = {"MEASURED": count, "PARTIAL": 0,
                                                                  "NO_MEASUREMENT": 0}

    return world.verdict("r2_fail_mutants_survived", shape)


def test_more_than_500_survivors_go_to_a_sidecar_next_to_the_fragment(world):
    result = world.export(_survivors_over_the_listing_limit(world, 501))
    fragment = json.loads(Path(result["fragment"]).read_text(encoding="utf-8"))
    assert violations(fragment) == []
    survivors = fragment["r2"]["survivors"]
    assert survivors["total"] == 501 and len(survivors["listed"]) == 500
    sidecar = Path(result["survivor_sidecar"])
    assert sidecar.name == "fragment.json.survivors.json"
    assert hashlib.sha256(sidecar.read_bytes()).hexdigest() == survivors["sidecar_sha256"]
    assert result["survivor_sidecar_sha256"] == survivors["sidecar_sha256"]
    assert len(json.loads(sidecar.read_text(encoding="utf-8"))) == 501


def test_exactly_500_survivors_need_no_sidecar(world):
    result = world.export(_survivors_over_the_listing_limit(world, 500))
    assert "survivor_sidecar" not in result
    assert not list(world.out_dir.glob("*.survivors.json"))


# ---- progress --------------------------------------------------------------------------------------------------
def _progress(world, *events) -> Path:
    path = world.out_dir / "progress.jsonl"
    path.write_text("".join(json.dumps(e) + "\n" for e in events), encoding="utf-8")
    return path


def _run(commit, started="2026-11-04T08:00:00.123456+00:00", **extra):
    return {"event": "run", "commit": commit, "lane": "package", "started": started, **extra}


def test_progress_supplies_campaign_times_and_the_resumed_count(world):
    progress = _progress(
        world,
        _run("f" * 40, started="2020-01-01T00:00:00+00:00", emitted_at="2020-01-01T00:00:01+00:00"),
        {"event": "candidates", "emitted_at": "2020-01-01T00:00:02+00:00"},
        _run(world.commit, emitted_at="2026-11-04T08:00:00+00:00"),
        {"event": "resume", "resumed_total": 1, "emitted_at": "2026-11-04T08:00:10+00:00"},
        {"event": "candidate", "commit": world.commit},
        {"event": "end", "emitted_at": "2026-11-04T11:30:00.9+02:00"})
    fragment = world.fragment(world.verdict("r2_fail_mutants_survived"), progress=progress)
    assert violations(fragment) == []
    assert fragment["r2"]["campaign"] == {"started_at": "2026-11-04T08:00:00Z", "completed_at": "2026-11-04T09:30:00Z"}
    assert fragment["r2"]["candidates"]["resumed"] == 1


def test_resumed_is_capped_at_executed_and_the_newest_run_wins(world):
    progress = _progress(
        world,
        _run(world.commit, started="2026-11-04T01:00:00+00:00", emitted_at="2026-11-04T01:00:00+00:00"),
        {"event": "resume", "resumed_total": 99, "emitted_at": "2026-11-04T01:00:05+00:00"},
        _run(world.commit, started="2026-11-04T08:00:00+00:00", emitted_at="2026-11-04T08:00:00+00:00"),
        {"event": "resume", "resumed_total": 9, "emitted_at": "2026-11-04T08:00:05+00:00"},
        {"event": "end", "emitted_at": "2026-11-04T08:10:00+00:00"})
    fragment = world.fragment(world.verdict("r2_fail_mutants_survived"), progress=progress)
    assert fragment["r2"]["candidates"]["resumed"] == 2
    assert fragment["r2"]["campaign"] == {"started_at": "2026-11-04T08:00:00Z",
                                          "completed_at": "2026-11-04T08:10:00Z"}


def test_progress_without_times_leaves_the_campaign_null(world):
    progress = _progress(world, {"event": "run", "commit": world.commit}, {"event": "end"})
    fragment = world.fragment(world.verdict("r2_fail_mutants_survived"), progress=progress)
    assert fragment["r2"]["campaign"] is None and fragment["r2"]["candidates"]["resumed"] == 0


def test_progress_is_ignored_for_a_verdict_without_r2(world):
    progress = _progress(world, _run(world.commit, emitted_at="2026-11-04T08:00:00+00:00"))
    assert world.fragment(world.verdict("r1_pass"), progress=progress)["r2"] is None


@pytest.mark.parametrize("events,needle", [
    ([{"event": "run", "commit": "f" * 40}], "no run at commit"),
    ([{"event": "candidates"}], "no run at commit"),
    ([["not", "an", "event"]], "malformed progress event"),
    ([{"event": 5}], "malformed progress event"),
    ([{"event": "run", "started": "yesterday", "emitted_at": "2026-11-04T08:00:00+00:00"}], "not ISO 8601"),
    ([{"event": "run", "started": "2026-11-04T08:00:00", "emitted_at": "2026-11-04T08:00:00+00:00"}],
     "no UTC offset"),
])
def test_bad_progress_is_refused(world, events, needle):
    events = [dict(e, commit=world.commit) if isinstance(e, dict) and e.get("event") == "run"
              and e.get("commit") is None and "started" in e else e for e in events]
    progress = _progress(world, *events)
    with pytest.raises(ValueError, match=needle):
        world.export(world.verdict("r2_fail_mutants_survived"), progress=progress)


# ---- refusals --------------------------------------------------------------------------------------------------
def test_an_unverifiable_verdict_is_refused_and_no_file_is_written(world):
    def corrupt(document):
        document["claims"][0]["verified_by_assay"] = False

    with pytest.raises(ValueError, match="invalid Assay verdict"):
        world.export(world.verdict("r1_pass", corrupt))
    assert not (world.out_dir / "fragment.json").exists()


def test_an_archived_schema_is_reported_by_name(world):
    def archive(document):
        document["schema_version"] = 17

    with pytest.raises(ValueError, match="VERDICT_SCHEMA_ARCHIVED"):
        world.export(world.verdict("r1_pass", archive))


def test_a_newer_unknown_schema_is_an_ordinary_verification_failure(world):
    def future(document):
        document["schema_version"] = 19

    with pytest.raises(ValueError, match="invalid Assay verdict"):
        world.export(world.verdict("r1_pass", future))


def test_a_non_object_verdict_is_an_ordinary_verification_failure(world):
    path = world.out_dir / "list.json"
    path.write_text("[]\n", encoding="utf-8")
    with pytest.raises(ValueError, match="invalid Assay verdict"):
        world.export(path)


def test_the_expected_commit_must_match(world):
    with pytest.raises(ValueError, match="differs from"):
        rigor_export.export(world.verdict("r1_pass"), "e" * 40, "package", world.lane_file,
                            world.out_dir / "f.json")


def test_a_dirty_override_is_refused(world):
    def dirty(document):
        document["worktree_integrity"] = {"overridden_dirty_paths": ["x.txt"]}

    with pytest.raises(ValueError):
        world.export(world.verdict("r1_pass", dirty))


def test_the_lane_must_match_the_verdict_and_the_lane_file(world):
    verdict = world.verdict("r1_pass")
    with pytest.raises(ValueError, match="not 'other'"):
        world.export(verdict, lane="other")
    with pytest.raises(ValueError, match="cannot be represented"):
        world.export(verdict, lane="bad lane")
    world.lane_file.write_text("schema_version = 2\n", encoding="utf-8")
    with pytest.raises(ValueError, match="not declared"):
        world.export(verdict)


def test_a_verdict_without_judge_provenance_is_refused(world):
    with pytest.raises(ValueError, match="judge_provenance"):
        world.export(world.verdict("r1_pass", provenance=None))


def test_a_judge_version_the_schema_cannot_hold_is_refused(world):
    bad = dict(PROVENANCE, version="12.0.0.dev3+g1234")
    with pytest.raises(ValueError, match="judge version"):
        world.export(world.verdict("r1_pass", provenance=bad))


def test_an_existing_output_is_never_overwritten(world):
    verdict = world.verdict("r1_pass")
    world.export(verdict)
    with pytest.raises(ValueError, match="refusing to overwrite"):
        world.export(verdict)


def test_an_existing_sidecar_is_never_overwritten(world):
    verdict = _survivors_over_the_listing_limit(world, 501)
    (world.out_dir / "fragment.json.survivors.json").write_text("[]", encoding="utf-8")
    with pytest.raises(ValueError, match="refusing to overwrite"):
        world.export(verdict)
    assert not (world.out_dir / "fragment.json").exists()


def test_a_fragment_over_the_size_limit_is_refused(world, monkeypatch):
    monkeypatch.setattr(rigor_export, "MAX_FRAGMENT_BYTES", 100)
    with pytest.raises(ValueError, match="256 KiB"):
        world.export(world.verdict("r1_pass"))


#: The verifier already rejects most malformed values, so these exercise the exporter's own guard directly.
_SURVIVED = {"candidate_id": "a" * 64, "path": "pkg/mod.py", "lineno": 3, "operator": "python:compare-swap"}


@pytest.mark.parametrize("field,value,needle", [
    ("path", "/abs/mod.py", "survivor path"),
    ("path", "../up.py", "survivor path"),
    ("operator", "Compare Swap", "survivor operator"),
    ("candidate_id", "x" * 200, "survivor candidate_id"),
])
def test_unrepresentable_survivor_values_are_refused(field, value, needle):
    mutation = {"killed": [], "survived": [{**_SURVIVED, field: value}]}
    with pytest.raises(ValueError, match=needle):
        rigor_export._survivors(mutation)


def test_a_survivor_without_an_id_is_refused():
    survivor = {key: value for key, value in _SURVIVED.items() if key != "candidate_id"}
    with pytest.raises(ValueError, match="lacks candidate_id"):
        rigor_export._survivors({"killed": [{"path": "a.py", "lineno": 1}], "survived": [survivor]})


def test_survivors_are_sorted_by_file_line_and_id():
    survivors = [{**_SURVIVED, "path": "pkg/b.py", "lineno": 1}, {**_SURVIVED, "lineno": 9},
                 {**_SURVIVED, "lineno": 2, "candidate_id": "b" * 64}, {**_SURVIVED, "lineno": 2}]
    ordered = rigor_export._survivors({"killed": [], "survived": survivors})
    assert [(s["path"], s["line"], s["candidate_id"][0]) for s in ordered] == [
        ("pkg/b.py", 1, "a"), ("pkg/mod.py", 2, "a"), ("pkg/mod.py", 2, "b"), ("pkg/mod.py", 9, "a")]


def test_buckets_beyond_the_candidate_count_are_refused(world, monkeypatch):
    verdict = world.verdict("r2_fail_mutants_survived")
    original = rigor_export._claims

    def inflate(document):
        claims = original(document)
        claims["R2"]["mutation"]["candidate_count"] = 1
        return claims

    monkeypatch.setattr(rigor_export, "_claims", inflate)
    with pytest.raises(ValueError, match="exceed candidate_count"):
        world.export(verdict)


def test_a_canary_with_an_unrepresentable_target_is_refused():
    claim = {"status": "PASS", "canary": {"mechanism": "uncovered-line", "attempts": [{"target": "/abs/x.py"}]}}
    with pytest.raises(ValueError, match="canary target"):
        rigor_export._canary(claim)


def test_a_canary_with_too_many_targets_is_refused(world, monkeypatch):
    monkeypatch.setattr(rigor_export, "MAX_CANARY_TARGETS", 0)
    with pytest.raises(ValueError, match="canary target count"):
        world.export(world.verdict("r3_pass"))


# ---- project identity ------------------------------------------------------------------------------------------
def test_the_project_name_can_be_chosen_and_must_be_valid(world):
    assert world.fragment(world.verdict("r1_pass"), project="my-tool")["project"] == "my-tool"
    with pytest.raises(ValueError, match="project"):
        world.export(world.verdict("r1_pass"), project="Bad Name", out=world.out_dir / "x.json")


def test_a_lane_file_at_the_repository_root_binds_the_root_tree(tmp_path):
    root = World(tmp_path, project_dir="")
    fragment = root.fragment(root.verdict("r1_pass"), project="whole")
    assert fragment["project_tree"] == _git(root.repo, "rev-parse", f"{root.commit}^{{tree}}")
    assert violations(fragment) == []


def test_the_project_tree_is_the_tree_at_the_judged_commit_not_the_checkout(world):
    before = world.fragment(world.verdict("r1_pass"), out=world.out_dir / "before.json")["project_tree"]
    (world.dir / "later.txt").write_text("later\n", encoding="utf-8")
    _git(world.repo, "add", "-A")
    _git(world.repo, "commit", "-q", "-m", "later")
    after = world.fragment(world.verdict("r1_pass"), out=world.out_dir / "after.json")["project_tree"]
    assert before == after


# ---- A-1a: verified claims and declared paths ------------------------------------------------------------------
def _claim(rigor, status="PASS", verified=True):
    return {"rigor": rigor, "status": status, "verified_by_assay": verified}


def test_an_all_level_lane_exports_four_claims_in_level_order():
    document = {"claims": [_claim("R3"), _claim("R1", "FAIL"), _claim("R0"), _claim("R2", "INCONCLUSIVE")]}
    assert rigor_export.claim_set(document) == [
        {"rigor": "R0", "outcome": "PASS"}, {"rigor": "R1", "outcome": "FAIL"},
        {"rigor": "R2", "outcome": "ERROR"}, {"rigor": "R3", "outcome": "PASS"}]


def test_an_unverified_claim_and_a_level_outside_the_vocabulary_are_absent():
    document = {"claims": [_claim("R0"), _claim("R1", verified=False), _claim("R4")]}
    assert rigor_export.claim_set(document) == [{"rigor": "R0", "outcome": "PASS"}]
    assert rigor_export.claim_set({"claims": []}) == []


@pytest.mark.parametrize("fixture,levels", [
    ("r0_pass", ["R0"]), ("r1_pass", ["R0", "R1"]), ("r2_pass", ["R0", "R2"]), ("r3_pass", ["R0", "R3"]),
    ("r1_no_measurement_empty_coverage", ["R0", "R1"])])
def test_the_fragment_lists_exactly_the_claims_the_verdict_carries(world, fixture, levels):
    fragment = world.fragment(world.verdict(fixture))
    assert violations(fragment) == []
    assert [claim["rigor"] for claim in fragment["claims"]] == levels
    # a section is present only under its claim, and a claim can exist without its section (R1 not measured)
    assert (fragment["coverage"] is not None) <= ("R1" in levels)


def test_a_non_passing_claim_is_listed_with_its_outcome_not_dropped(world):
    fragment = world.fragment(world.verdict("r1_no_measurement_empty_coverage"))
    assert fragment["claims"] == [{"rigor": "R0", "outcome": "PASS"}, {"rigor": "R1", "outcome": "ERROR"}]


def test_link_paths_appear_as_sorted_inputs_and_source_roots_as_judged_paths(world):
    world.lane_file.write_text(
        LANE_FILE + '\n[lanes.package.isolation]\nlink_paths = ["a/node_modules", "vendor"]\n'
        '\n[lanes.package.judge]\nsource_roots = ["src", "lib/core"]\n', encoding="utf-8")
    fragment = world.fragment(world.verdict("r0_pass"))
    assert violations(fragment) == []
    assert fragment["inputs"] == ["a/node_modules", "vendor"]
    assert fragment["judged_paths"] == ["lib/core", "src"]


def test_a_lane_without_isolation_or_judge_tables_has_empty_path_lists(world):
    fragment = world.fragment(world.verdict("r0_pass"))
    assert fragment["inputs"] == [] and fragment["judged_paths"] == []


def test_declared_paths_the_schema_cannot_hold_are_refused(world):
    world.lane_file.write_text(LANE_FILE + '\n[lanes.package.isolation]\nlink_paths = ["a b"]\n', encoding="utf-8")
    with pytest.raises(ValueError, match="link_paths"):
        world.export(world.verdict("r0_pass"))


def test_too_many_declared_paths_are_refused(world, monkeypatch):
    monkeypatch.setattr(rigor_export, "MAX_LISTED_PATHS", 0)
    world.lane_file.write_text(LANE_FILE + '\n[lanes.package.isolation]\nlink_paths = ["vendor"]\n', encoding="utf-8")
    with pytest.raises(ValueError, match="more than 0 paths"):
        world.export(world.verdict("r0_pass"))


@pytest.mark.parametrize("mutate,needle", [
    (lambda d: d.update(claims=[{"rigor": "R2", "outcome": "FAIL"}, {"rigor": "R0", "outcome": "PASS"}]), "ordered"),
    (lambda d: d.update(claims=[{"rigor": "R0", "outcome": "PASS"}]), "r2 section without"),
    (lambda d: d.update(inputs=["z", "a"]), "inputs must be sorted"),
    (lambda d: d.update(judged_paths=["z", "a"]), "judged_paths must be sorted"),
    (lambda d: d["claims"][0].update(rigor="R4"), "rigor"),
    (lambda d: d["claims"][0].update(extra=1), "extra"),
    (lambda d: d.update(inputs=["/home/x"]), "inputs"),
])
def test_the_contract_checker_names_each_a1a_violation(mutate, needle):
    document = copy.deepcopy(golden("assay-r2-survivors"))
    mutate(document)
    found = violations(document)
    assert found and any(needle in message for message in found), found


def test_a_pre_a1a_fragment_without_the_new_keys_still_validates():
    document = copy.deepcopy(golden("assay-r2-survivors"))
    for key in ("claims", "inputs", "judged_paths"):
        del document[key]
    assert violations(document) == []


# ---- the command line ------------------------------------------------------------------------------------------
def _cli(world, verdict, *extra):
    out, err = io.StringIO(), io.StringIO()
    code = analysis_cli.main(
        ["export", "--verdict", str(verdict), "--lane", "package", "--file", str(world.lane_file),
         "--expected-commit", world.commit, "--out", str(world.out_dir / "cli.json"), *extra],
        stdout=out, stderr=err)
    return code, out.getvalue(), err.getvalue()


def test_the_command_writes_the_fragment_and_prints_a_summary(world):
    verdict = world.verdict("r2_fail_mutants_survived")
    progress = _progress(world, _run(world.commit, emitted_at="2026-11-04T08:00:00+00:00"))
    code, out, err = _cli(world, verdict, "--progress", str(progress), "--project", "tool")
    assert (code, err) == (0, "")
    summary = json.loads(out)
    assert summary["outcome"] == "FAIL" and summary["reason"] == "MUTANTS_SURVIVED"
    written = json.loads((world.out_dir / "cli.json").read_text(encoding="utf-8"))
    assert violations(written) == [] and written["project"] == "tool"


def test_the_command_exits_one_with_the_reason_on_a_refusal(world):
    def archive(document):
        document["schema_version"] = 17

    code, out, err = _cli(world, world.verdict("r1_pass", archive))
    assert code == 1 and out == ""
    assert "VERDICT_SCHEMA_ARCHIVED" in err and not (world.out_dir / "cli.json").exists()


def test_the_parser_requires_the_identity_arguments(capsys):
    with pytest.raises(SystemExit) as caught:
        analysis_cli.build_parser().parse_args(["export", "--verdict", "v"])
    assert caught.value.code == 2
    assert "--lane" in capsys.readouterr().err
