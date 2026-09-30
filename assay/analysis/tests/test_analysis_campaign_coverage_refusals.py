"""B108 step 3a (W9): the ``--coverage`` block of ``campaign.py`` over a real R1 run.

Every case edits the artifact or the (unsigned) verdict of one real ``assay run`` and
asks the public command. Rows name the refusal they reach; the positive rows pin what the
block must NOT refuse. The raises that no input can reach (the artifact parser or the
verdict verifier or the R1 re-evaluation refuses first) were deleted under CD58; the
dominating check for each is recorded in the W9 LOG.
"""

from __future__ import annotations

import copy
import json

import pytest

from analysis.tests.test_analysis_campaign_real import (
    _COVERAGE,
    _commit,
    _git,
    _r1_campaign,
    _repository,
    _run,
)

_MOD = "def f(x):\n    return x\n    x += 1\n\n\ndef g(y):\n    if y:\n        y = 1\n    return y\n"
_LCOV = (
    "SF:src/mod.py\nDA:1,1\nDA:2,1\nDA:3,0\nDA:6,1\nDA:7,1\nDA:8,1\nDA:9,1\n"
    "BRDA:7,0,0,1\nBRDA:7,0,1,0\nend_of_record\n"
)


def _cov(change):
    def mutate(root, verdict_path, cov_path):
        document = copy.deepcopy(_COVERAGE)
        change(document)
        cov_path.write_text(json.dumps(document))

    return mutate


def _verdict(change):
    def mutate(root, verdict_path, cov_path):
        document = json.loads(verdict_path.read_text())
        change(document)
        verdict_path.write_text(json.dumps(document))

    return mutate


def _go(root, head, evidence, *, verdict=True, coverage="cov.json", command_exit="1"):
    arguments = [
        "analyze", "campaign", "package",
        "--worktree", str(root), "--file", str(root / "assay.toml"),
        "--expected-commit", head, "--progress", str(evidence / "progress.jsonl"), "--format", "json",
    ]
    if verdict:
        arguments += ["--verdict", str(evidence / "verdict.json"), "--command-exit", command_exit]
    if coverage is not None:
        arguments += ["--coverage", str(root / coverage)]
    code, out, _err = _run(arguments)
    return code, json.loads(out)


REFUSALS = (
    (
        "artifact-key-repeated",
        _cov(lambda d: d["files"].update({"./src/mod.py": copy.deepcopy(d["files"]["src/mod.py"])})),
        "re-evaluation disagrees with the verified R1 verdict field 'status'",
    ),
    (
        "artifact-arc-on-a-judged-line-added",
        _cov(lambda d: d["files"]["src/mod.py"]["missing_branches"].append([1, -1])),
        "re-evaluation disagrees with the verified R1 verdict field 'coverage'",
    ),
    (
        "verdict-policy-field-differs",
        _verdict(lambda d: d["judgment"]["r1"].update(allow_excluded=True)),
        "R1 verdict policy 'allow_excluded' differs from the named lane declaration",
    ),
    (
        "verdict-policy-artifact-differs",
        _verdict(lambda d: d["judgment"]["r1"].update(coverage_artifact="other.json")),
        "R1 verdict policy differs from the named lane declaration",
    ),
)


@pytest.mark.parametrize(("case_id", "mutate", "fragment"), REFUSALS, ids=[case[0] for case in REFUSALS])
def test_every_reachable_coverage_refusal_is_an_evidence_error(tmp_path, case_id, mutate, fragment):
    root, head, evidence = _r1_campaign(tmp_path)
    mutate(root, evidence / "verdict.json", root / "cov.json")
    code, document = _go(root, head, evidence)
    assert code == 2, (case_id, document.get("status"))
    assert fragment in document["errors"][0]["message"], (case_id, document["errors"])
    assert document["errors"][0]["source"] in ("coverage", "verdict")


def test_coverage_without_a_verdict_is_an_evidence_error(tmp_path):
    root, head, evidence = _r1_campaign(tmp_path)
    code, document = _go(root, head, evidence, verdict=False)
    assert code == 2
    assert document["errors"][0]["message"] == "coverage cannot be reverified without --verdict"
    assert document["errors"][0]["source"] == "coverage"


def test_a_coverage_path_other_than_the_declared_artifact_is_an_evidence_error(tmp_path):
    root, head, evidence = _r1_campaign(tmp_path)
    (root / ".assay").mkdir(exist_ok=True)
    (root / ".assay" / "other.json").write_text(json.dumps(_COVERAGE))
    code, document = _go(root, head, evidence, coverage=".assay/other.json")
    assert code == 2
    message = document["errors"][0]["message"]
    assert message == (
        f"coverage artifact {root / '.assay' / 'other.json'} differs from lane-declared artifact {root / 'cov.json'}"
    )
    assert document["errors"][0]["source"] == "coverage"


def test_a_missing_declared_artifact_is_reported_not_refused(tmp_path):
    root, head, evidence = _r1_campaign(tmp_path)
    (root / "cov.json").unlink()
    code, document = _go(root, head, evidence)
    coverage = document["coverage"]
    assert coverage["artifact_status"] == "missing"
    assert coverage["artifact_error"] == f"declared coverage artifact is missing: {root / 'cov.json'}"
    assert coverage["branch_arc_detail_status"] == "unavailable_without_artifact"
    assert coverage["missing_branch_arcs"] is None
    assert document["status"] == "incomplete"
    assert code == 3


@pytest.mark.parametrize(
    "change",
    [
        lambda d: d["files"].update({"/nowhere/x.py": copy.deepcopy(d["files"]["src/mod.py"])}),
        lambda d: d["files"].update({"a\0b": copy.deepcopy(d["files"]["src/mod.py"])}),
        lambda d: d["files"].update({"src/other.py": {
            "executed_lines": [], "missing_lines": [], "excluded_lines": [],
            "executed_branches": [], "missing_branches": [],
        }}),
        lambda d: (
            d["files"]["src/mod.py"]["executed_lines"].append(100),
            d["files"]["src/mod.py"]["missing_branches"].append([100, 101]),
        ),
    ],
    ids=[
        "key-outside-the-repository", "key-that-cannot-be-resolved", "key-for-another-file-in-the-repository",
        "arc-on-a-line-the-verdict-does-not-judge",
    ],
)
def test_records_and_arcs_the_verdict_does_not_judge_never_change_the_reported_arcs(tmp_path, change):
    root, head, evidence = _r1_campaign(tmp_path)
    _cov(change)(root, evidence / "verdict.json", root / "cov.json")
    code, document = _go(root, head, evidence)
    assert code == 1, document.get("errors")
    assert document["coverage"]["missing_branch_arcs"] == [
        {"path": "src/mod.py", "source_line": 7, "destination": 9}
    ]


_NO_BRANCH_DATA = {
    "meta": {"branch_coverage": False},
    "files": {
        "src/mod.py": {
            "executed_lines": [1, 2, 6, 7, 8, 9], "missing_lines": [3], "excluded_lines": [],
        }
    },
}


def test_a_verdict_that_reports_no_branch_capability_yields_no_arc_detail(tmp_path):
    root, head, evidence = _r1_campaign(tmp_path, _NO_BRANCH_DATA)
    code, document = _go(root, head, evidence)
    coverage = document["coverage"]
    assert code == 1, document.get("errors")
    assert coverage["artifact_status"] == "parsed_and_reverified_r1"
    assert coverage["r1"]["missing_lines"] == {"src/mod.py": [3]}
    assert coverage["branch_arc_detail_status"] == "unavailable"
    assert coverage["missing_branch_arcs"] is None


def test_a_format_that_does_not_expose_arc_destinations_says_so(tmp_path):
    root = _repository(tmp_path)
    (root / "README").write_text("base\n")
    base = _commit(root, "base")
    (root / "src" / "mod.py").write_text(_MOD)
    _commit(root, "add mod.py")
    write_lcov = "cat > cov.lcov <<'EOF'\n" + _LCOV + "EOF"
    (root / ".gitignore").write_text("cov.lcov\n.assay/\n")
    (root / "assay.toml").write_text(
        "schema_version = 2\n\n[lanes.package]\n"
        'scope = "S1"\nrigor = ["R0", "R1"]\nenforcement = "gate"\n'
        f"argv = [\"/bin/sh\", \"-c\", {json.dumps(write_lcov)}]\n"
        'env = {}\nenv_passthrough = ["PATH"]\nbudget = "1m"\nallow_argv_append = false\n\n'
        '[lanes.package.isolation]\nsnapshot_selection = "repository"\n\n'
        '[lanes.package.judge]\nlanguage = "python"\nsource_roots = ["src"]\n'
        "fail_under = 100.0\nallow_excluded = false\n"
        'coverage = { format = "lcov", artifact = "cov.lcov" }\n'
        f'base = "{base}"\n'
    )
    head = _commit(root, "add assay.toml")
    evidence = tmp_path / "ev"
    evidence.mkdir()
    code, _out, err = _run([
        "run", "package", "--file", str(root / "assay.toml"),
        "--verdict-json", str(evidence / "verdict.json"), "--progress", str(evidence / "progress.jsonl"),
    ])
    assert code == 1, err
    (root / "cov.lcov").write_text(_LCOV)
    code, document = _go(root, head, evidence, coverage="cov.lcov")
    assert code == 1, document.get("errors")
    assert document["coverage"]["branch_arc_detail_status"] == "format_does_not_expose_exact_destinations"
    assert document["coverage"]["missing_branch_arcs"] is None
    assert _git(root, "rev-parse", "HEAD") == head
