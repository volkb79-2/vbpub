import copy
import json

import pytest

from conftest import GOLDEN, golden, project_git
from rigor_record import canonical_json, derive, validate_index, validate_record
from rigor_record import render as render_module
from rigor_record.check import load_records
from rigor_record.render import (
    RenderError, build_index, r2_text, render_body, render_index, render_project_md, render_record, render_views,
    storage_order, tools_text,
)

PROJECT = GOLDEN / "project"


@pytest.fixture(scope="module")
def chain():
    loaded, problems = load_records(PROJECT, (), ())
    assert problems == []
    return loaded


@pytest.fixture
def at(chain):
    return derive.targets_at_factory(chain, project_git())


def rec(name):
    return golden(f"records/{name}.json")


def own_targets(*records):
    merged = {r["commit"]: {p: o for x in r["results"] if x["kind"] != "sequence" for p, o in x["targets"].items()} for r in records}
    return lambda commit: merged.get(commit, {})


def clone(record, n, version=None):
    """A copy of ``record`` at a synthetic commit (every target moved with it), optionally labelled ``version``."""
    out = copy.deepcopy(record)
    out["commit"] = f"{n:040x}"
    for result in out["results"]:
        if result["kind"] != "sequence":
            result["targets"] = {path: f"{n:040x}" for path in result["targets"]}
    if version is None:
        out["labels"] = []
    else:
        out["labels"][0].update(tag=f"cgprofile-v{version}", version=version)
    return out


# -- goldens round-trip byte-identically ------------------------------------------------------------------------
@pytest.mark.parametrize("path", sorted((GOLDEN / "records").glob("*.json")), ids=lambda p: p.stem)
def test_every_golden_record_renders_to_its_own_bytes(path):
    text = path.read_text()
    assert render_record(json.loads(text)) == text


def test_golden_project_views_are_byte_identical_to_a_regeneration(chain, at):
    views = render_views(chain, "cgroup-profiler", at)
    assert views["rigor/index.json"] == (PROJECT / "rigor" / "index.json").read_text()
    assert views["RIGOR.md"] == (PROJECT / "RIGOR.md").read_text()


def test_views_do_not_depend_on_record_order(chain, at):
    assert render_views(list(reversed(chain)), "cgroup-profiler", at) == render_views(chain, "cgroup-profiler", at)


def test_golden_index_is_schema_valid():
    assert validate_index(json.loads((PROJECT / "rigor" / "index.json").read_text())) == []


def test_golden_project_index_content():
    index = json.loads((PROJECT / "rigor" / "index.json").read_text())
    assert index["latest"] == "cgprofile-v2.1.0" and index["stable"] == "cgprofile-v2.0.0"
    assert index["labels"]["cgprofile-v1.1.0"] == "1" * 40
    labelled = [c for c in index["commits"] if c["labels"]]
    assert [c["levels"]["R2"] for c in labelled] == ["1/1 survivors", "1/1 pass-with-waivers", "1/1 pass", "not-judged"]
    assert labelled[0]["r2_scope"] == {"kind": "changed-since", "base_commit": "3" * 40}
    assert labelled[3]["r2_scope"] is None and labelled[3]["anchored"] is None
    unlabelled = [c for c in index["commits"] if not c["labels"]]
    assert [c["commit"][0] for c in unlabelled] == ["6", "5"]


def test_render_record_refuses_an_invalid_record_and_a_privacy_hit():
    with pytest.raises(RenderError):
        render_record({"schema": "urn:vbpub:rigor-record:1"})
    with pytest.raises(RenderError):
        render_record(rec("r2-pass"), hosts=("cgroup",))


# -- status text ------------------------------------------------------------------------------------------------
def r2_of(name):
    return next(r for r in rec(name)["results"] if "R2" in r.get("levels", {}))


def test_r2_text_every_status():
    base = r2_of("r2-survivors")
    assert r2_text(base) == "survivors 31 open / 557 (score 94.4 %), whole source"
    assert r2_text({**base, "score": None}).endswith("(score n/a), whole source")
    assert r2_text(r2_of("r2-pass-with-waivers")).startswith("pass-with-waivers ")
    assert r2_text(r2_of("r2-pass")).startswith("pass 557 candidates (score ")
    inc = r2_of("r2-incomplete")
    assert r2_text(inc) == f"incomplete {inc['candidates']['executed']}/{inc['candidates']['total']}"
    assert r2_text(r2_of("r2-error")) == "error"


def test_r2_text_scope_variants():
    result = copy.deepcopy(r2_of("r2-survivors"))
    result["scope"] = {"kind": "changed-since", "base_commit": "3" * 40, "anchored": False}
    assert "since " + "3" * 12 + ", unanchored" in r2_text(result)
    result["scope"] = {"kind": "changed-since", "base_commit": "3" * 40, "base_label": "cgprofile-v2.0.0", "anchored": True}
    assert r2_text(result).endswith("since v2.0.0")


def test_tools_text_merges_lanes_and_dashes_when_empty():
    record = rec("r2-pass")
    assert tools_text(record) == "run-gate 25.0.1, assay 12.0.0"
    empty = copy.deepcopy(record)
    for result in empty["results"]:
        result["toolset"] = None
    assert tools_text(empty) == "—"
    nodaemon = copy.deepcopy(record)
    for toolset in nodaemon["toolsets"].values():
        if "cgprofile" in toolset:
            toolset["cgprofile"]["daemon_version"] = None
    assert "cgprofile" not in tools_text(nodaemon)
    shown = copy.deepcopy(record)
    for result in shown["results"]:
        if result["toolset"] is not None and "R2" not in result.get("levels", {}):
            shown["toolsets"][result["toolset"]]["cgprofile"] = {"daemon_version": "2.0.0"}
    assert "cgprofile 2.0.0" in tools_text(shown)
    bare = copy.deepcopy(record)
    for toolset in bare["toolsets"].values():
        toolset.pop("cgprofile", None)
    assert tools_text(bare) == "run-gate 25.0.1, assay 12.0.0"


# -- the body ---------------------------------------------------------------------------------------------------
def test_body_rows_and_refusals():
    body = render_body(rec("r2-pass"), "cgroup-profiler")
    assert body.count("\n| ") == 5 and "| R2 | pending: runs after release on the latest release only |" in body
    assert "R1-R3 count only when assay judged them; command lanes are declared, not judged." in body
    assert "| R1 | pass (line 100.0 %, branch 100.0 %) |" in body
    with pytest.raises(RenderError) as info:
        render_body(rec("r2-pass"), "x" * 9000)
    assert "over the 8192 byte limit" in str(info.value)
    with pytest.raises(RenderError) as info:
        render_body(rec("r2-pass"), "dir", hosts=("dir",))
    assert "privacy rule host" in str(info.value)


def test_body_counts_waived_lanes():
    record = copy.deepcopy(rec("r2-pass"))
    waiver = {"reason": "x", "plan_item": None, "expires_before": "9.0.0"}
    for result in record["results"]:
        if result["lane"] == "assay-r1":
            result["waiver"] = waiver
    assert "| R1 | pass (line 100.0 %, branch 100.0 %) (1 waived lane) |" in render_body(record, "p")
    for result in record["results"]:
        if result["lane"] == "assay-r3":
            result["waiver"] = waiver
    assert "(1 waived lane)" in render_body(record, "p")
    for result in record["results"]:
        if result["lane"] == "r0-r1":
            result["waiver"] = waiver
    assert "| R0 | pass (3 waived lanes) |" in render_body(record, "p")  # R0 counts the lanes holding R0


# -- index and ordering ------------------------------------------------------------------------------------------
def test_storage_order_is_by_judged_time_then_commit(chain):
    ordered = storage_order(chain)
    assert ordered == storage_order(reversed(chain))
    stamps = [r["labels"][0]["published_at"] for r in ordered if r["labels"]]
    assert stamps == sorted(stamps)
    assert storage_order([]) == []


def test_ordering_of_releases_is_by_semver_not_by_text():
    base = rec("r2-pass")
    records = [clone(base, n, v) for n, v in enumerate(("1.9.0", "1.10.0", "1.2.0"), start=1)]
    index = build_index(records, "p", own_targets(*records))
    assert [c["labels"] for c in index["commits"]] == [["cgprofile-v1.10.0"], ["cgprofile-v1.9.0"], ["cgprofile-v1.2.0"]]


def test_index_of_nothing_and_with_no_current_release():
    assert build_index([], "p", lambda commit: {}) == {
        "schema": "urn:vbpub:rigor-index:1", "project": "p", "latest": None, "stable": None, "adopted_from": None,
        "labels": {}, "commits": [],
    }
    only = [rec("withdrawn")]
    index = build_index(only, "p", own_targets(*only))
    assert index["latest"] is None and list(index["labels"]) == ["cgprofile-v1.1.0"]
    assert validate_index(json.loads(render_index(only, "p", own_targets(*only)))) == []


def test_stable_requires_a_current_label_with_a_passing_promotion():
    passing = rec("r2-pass")
    promotion = {"judged_commit": passing["commit"], "results": [r for r in passing["results"] if r["lane"] == "r0-r1"]}
    new = clone(passing, 2, "2.0.0")
    old = clone(passing, 1, "1.0.0")
    new["qualifications"]["promotion"] = {**promotion, "results": [{**promotion["results"][0], "levels": {"R0": "fail"}}]}
    old["qualifications"]["promotion"] = promotion
    assert build_index([old, new], "p", own_targets(old, new))["stable"] == "cgprofile-v1.0.0"
    old["labels"][0]["state"] = "withdrawn"
    old["labels"][0]["withdrawn"] = {"reason": "x", "replacement": "cgprofile-v2.0.0"}
    assert build_index([old, new], "p", own_targets(old, new))["stable"] is None
    assert build_index([clone(passing, 3, "3.0.0")], "p", own_targets(clone(passing, 3, "3.0.0")))["stable"] is None


def test_the_index_keeps_a_bounded_number_of_unlabelled_commits(monkeypatch):
    base = rec("r2-unlabelled")
    records = [clone(base, n) for n in range(1, 6)]
    monkeypatch.setattr(render_module, "INDEX_UNLABELLED", 2)
    index = build_index(records, "p", own_targets(*records))
    assert len(index["commits"]) == 2


# -- RIGOR.md ----------------------------------------------------------------------------------------------------
def test_empty_project_view():
    text = render_project_md([], lambda commit: {})
    assert "No release has a record yet." in text and "Latest: —" in text and "Latest release detail" not in text
    assert "No unreleased commit has a stored result." in text


def test_only_withdrawn_releases_have_no_latest_detail():
    only = [rec("withdrawn")]
    text = render_project_md(only, own_targets(*only))
    assert "(withdrawn)" in text and "Latest release detail" not in text and "Latest: —" in text


def test_table_is_capped_and_says_so():
    base = rec("r2-pass")
    records = [clone(base, n, f"1.{n}.0") for n in range(1, 31)]
    text = render_project_md(records, own_targets(*records))
    assert text.count("| cgprofile-v1.") == 25 and "5 older releases are in `rigor/commits/`." in text


def test_rows_drop_when_over_the_limit_and_a_hopeless_limit_refuses():
    base = rec("r2-pass")
    records = [clone(base, n, f"1.{n}.0") for n in range(1, 31)]
    at = own_targets(*records)
    full = render_project_md(records, at)
    smaller = render_project_md(records, at, limit=len(full.encode()) - 10)
    assert smaller.count("| cgprofile-v1.") < 25 and "older releases are in" in smaller
    with pytest.raises(RenderError):
        render_project_md(records, at, limit=10)


def test_the_since_table_lists_unlabelled_commits_newest_first(chain, at):
    text = render_project_md(chain, at)
    since = text.split("## Judged since the last release")[1].split("## Latest release detail")[0]
    assert since.index("666666666666") < since.index("555555555555")


def test_row_variants_coverage_cells():
    record = copy.deepcopy(rec("r2-pass"))
    for result in record["results"]:
        if result["lane"] == "assay-r1":
            result["levels"] = {"R0": "pass"}
            result.pop("coverage", None)
    text = render_project_md([record], own_targets(record))
    assert "| not-judged |" in text
    waived = copy.deepcopy(rec("r2-pass"))
    for result in waived["results"]:
        if result["lane"] == "assay-r1":
            result["levels"]["R1"] = "fail"
            result["waiver"] = {"reason": "x", "plan_item": None, "expires_before": "9.0.0"}
    text = render_project_md([waived], own_targets(waived))
    assert "| fail |" in text and "lane assay-r1: valid until before 9.0.0" in text
    assert "until before 9.0.0: x" in text


def test_detail_survivors_waivers_deviations_and_qualifications():
    record = copy.deepcopy(rec("r2-survivors"))
    at = own_targets(record)
    text = render_project_md([record], at)
    assert "### Open survivors (31)" in text and "- and 11 more" in text
    r2 = next(r for r in record["results"] if "R2" in r["levels"])
    r2["survivors"]["listed"] = r2["survivors"]["listed"][:3]
    assert "more" not in render_project_md([record], at).split("### Open survivors")[1].split("###")[0]
    r2["survivors"]["equivalent_reviewed"], r2["survivors"]["accepted"] = 3, 1
    assert "R2 survivors of assay-r2: 3 equivalent-reviewed, 1 accepted" in render_project_md([record], at)
    r2["deviations"] = [{"file": "run-gate.toml", "tag_sha256": "1" * 64, "used_sha256": "2" * 64, "note": "PYTHONPATH line"}]
    assert "- `run-gate.toml`: PYTHONPATH line" in render_project_md([record], at)
    plain = copy.deepcopy(rec("r2-incomplete"))
    text = render_project_md([plain], own_targets(plain))
    assert "### Open survivors" not in text and "### Waivers in force\n\n- none" in text and "### Deviations\n\n- none" in text
    qualified = copy.deepcopy(rec("r2-pass"))
    qualified["qualifications"]["staging"] = {
        "judged_commit": qualified["commit"], "results": [r for r in qualified["results"] if r["lane"] == "assay-r1"],
    }
    assert "| assay-r1 (qualification staging) | assay | R0 pass, R1 pass | — |" in render_project_md([qualified], own_targets(qualified))


def test_a_command_lane_is_declared_not_judged_in_the_lanes_table(chain, at):
    text = render_project_md(chain, at)
    assert "| r0-r1 | run-gate | declared, not judged | — |" in text
    assert "| assay-r1 | assay | R0 pass, R1 pass | — |" in text


def test_no_valid_r2_result_is_listed_as_none():
    record = copy.deepcopy(rec("r2-pass"))
    text = render_project_md([record], lambda commit: {"cgroup-profiler": "9" * 40})
    assert "- none valid at this commit" in text


def test_performance_section_variants():
    record = rec("r2-pass")
    assert "### Performance" in render_project_md([record], own_targets(record))
    none = copy.deepcopy(record)
    none["perf"], none["machine_classes"] = [], []
    text = render_project_md([none], own_targets(none))
    assert "- none recorded" in text and "Machine classes:\n\n- none" in text
    unavailable = copy.deepcopy(record)
    unavailable["perf"][0]["metrics"]["wall_seconds"] = {"unavailable": True, "reason": "NO_MEASUREMENT"}
    unavailable["machine_classes"][0]["hypervisor"] = False
    unavailable["machine_classes"].append(
        {**unavailable["machine_classes"][0], "id": "m9", "speed": {"unavailable": True, "reason": "NO_HOST_MEASUREMENT"}}
    )
    text = render_project_md([unavailable], own_targets(unavailable))
    assert "unavailable: NO_MEASUREMENT" in text and "bare metal" in text and "speed unavailable (NO_HOST_MEASUREMENT)" in text
    only_raw = copy.deepcopy(record)
    only_raw["perf"][0]["metrics"] = {k: v for k, v in only_raw["perf"][0]["metrics"].items() if not k.endswith("_ref")}
    assert "Normalised to the Geekbench baseline core:" not in render_project_md([only_raw], own_targets(only_raw))


def test_rendered_views_pass_the_privacy_lint_and_the_record_validator(chain, at):
    from rigor_record import lint_string

    for line in render_project_md(chain, at).splitlines():
        assert lint_string(line) == [], line
    assert all(validate_record(r) == [] for r in chain)
    index = render_index(chain, "cgroup-profiler", at)
    assert canonical_json(json.loads(index)) == index
