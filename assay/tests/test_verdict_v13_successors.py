"""Keep W8's v12-only R3/R4 controls covered after the v13 hard cut.

The historical W8 assets stay byte-frozen and are rejected by ``assay verify``
under v13. These controls lift only two documents whose R3/R4 shapes contain no
native mutation outcomes, so changing their schema identity to 13 does not
invent the B106 candidate provenance that a v12 mutation artifact lacks.
"""

from __future__ import annotations

import copy
import json
from pathlib import Path

from assay.verify import verify_document


W8_EXPECTED = (
    Path(__file__).resolve().parents[1]
    / "nyxloom-trove"
    / "carve-assets"
    / "W8"
    / "expected"
)
RED_FIRST = "r4-red-first-v12-template.json"
MULTI_TARGET = "multi-target-r3-v12-template.json"
SINGLE_TARGET = "ca1-r3-no-base-v12-template.json"


def _load_v13_control(name: str) -> dict:
    source = (W8_EXPECTED / name).read_text(encoding="utf-8")
    source = source.replace("@STARTED@", "2026-08-11T00:00:00+00:00")
    source = source.replace("@ENDED@", "2026-08-11T00:00:01+00:00")
    document = json.loads(source)
    assert document["schema_version"] == 12
    document["schema_version"] = 13
    return document


def test_w8_multi_target_r3_control_still_verifies_under_v13():
    document = _load_v13_control(MULTI_TARGET)

    assert verify_document(document) == []
    assert document["judgment"]["r3"]["targets"] == [
        "pkg/greet.py",
        "pkg/farewell.py",
    ]
    assert document["judgment"]["r3"]["aggregation"] == "any"


def test_v13_multi_target_r3_refuses_reordered_targets():
    clean = _load_v13_control(MULTI_TARGET)
    assert verify_document(clean) == []

    broken = copy.deepcopy(clean)
    broken["judgment"]["r3"]["targets"] = ["pkg/farewell.py", "pkg/greet.py"]
    assert verify_document(broken)


def test_v13_multi_target_r3_refuses_a_short_attempt_list():
    clean = _load_v13_control(MULTI_TARGET)
    assert verify_document(clean) == []

    broken = copy.deepcopy(clean)
    del broken["claims"][1]["canary"]["attempts"][1]
    assert verify_document(broken)


def test_v13_multi_target_r3_refuses_short_circuit_under_all():
    clean = _load_v13_control(MULTI_TARGET)
    assert verify_document(clean) == []

    broken = copy.deepcopy(clean)
    broken["judgment"]["r3"]["aggregation"] = "all"
    assert verify_document(broken)


def test_v13_not_attempted_r3_entry_may_not_carry_a_run():
    clean = _load_v13_control(MULTI_TARGET)
    assert verify_document(clean) == []

    broken = copy.deepcopy(clean)
    broken["claims"][1]["canary"]["attempts"][1]["control_outcome"] = "PASS"
    assert verify_document(broken)


def test_v13_single_target_r3_control_has_no_aggregation():
    document = _load_v13_control(SINGLE_TARGET)

    assert verify_document(document) == []
    assert document["judgment"]["r3"]["targets"] == ["pkg/greet.py"]
    assert "aggregation" not in document["judgment"]["r3"]

    broken = copy.deepcopy(document)
    broken["judgment"]["r3"]["aggregation"] = "all"
    assert verify_document(broken)


def test_w8_r4_red_first_control_still_verifies_under_v13():
    document = _load_v13_control(RED_FIRST)

    assert verify_document(document) == []
    assert document["declared_rigor"] == ["R0", "R4"]
    assert document["claims"][1]["red_first"]["before_outcome"] == "FAIL"
    assert document["claims"][1]["red_first"]["after_outcome"] == "PASS"


def test_v13_r4_refuses_a_test_that_passed_before_the_fix():
    clean = _load_v13_control(RED_FIRST)
    assert verify_document(clean) == []

    broken = copy.deepcopy(clean)
    broken["claims"][1]["red_first"]["before_outcome"] = "PASS"
    assert verify_document(broken)


def test_v13_r4_refuses_when_the_after_run_did_not_pass():
    clean = _load_v13_control(RED_FIRST)
    assert verify_document(clean) == []

    broken = copy.deepcopy(clean)
    broken["claims"][1]["red_first"]["after_outcome"] = "FAIL"
    assert verify_document(broken)


def test_v13_r4_accepts_the_valid_fail_when_the_test_passed_before_the_fix():
    clean = _load_v13_control(RED_FIRST)
    assert verify_document(clean) == []

    judged_fail = copy.deepcopy(clean)
    judged_fail["claims"][1]["status"] = "FAIL"
    judged_fail["claims"][1]["reason_code"] = "RED_FIRST_UNPROVEN"
    judged_fail["claims"][1]["red_first"]["before_outcome"] = "PASS"
    del judged_fail["claims"][1]["red_first"]["after_outcome"]
    judged_fail["outcome"] = "FAIL"
    judged_fail["reason_code"] = "RED_FIRST_UNPROVEN"
    judged_fail["exit_code"] = 1
    assert verify_document(judged_fail) == []

    contradictory = copy.deepcopy(judged_fail)
    contradictory["claims"][1]["red_first"]["after_outcome"] = "PASS"
    assert verify_document(contradictory)


def test_v13_red_first_unproven_belongs_to_the_r4_claim():
    clean = _load_v13_control(RED_FIRST)
    assert verify_document(clean) == []

    broken = copy.deepcopy(clean)
    broken["claims"][0]["status"] = "FAIL"
    broken["claims"][0]["reason_code"] = "RED_FIRST_UNPROVEN"
    broken["outcome"] = "FAIL"
    broken["reason_code"] = "RED_FIRST_UNPROVEN"
    broken["exit_code"] = 1
    assert verify_document(broken)
