"""W2's v7 acceptance suite: B014's hard cut plus its new output-tail contract.

The six templates are the frozen W1 v6 documents migrated by bumping
`schema_version` to 7, then verified clean before being written. They remain in
committed form so this suite tests real bytes, not a runtime transformation.

This module also carries forward W1's differential discipline: every negative
has an unmodified clean control that must verify clean in the same test.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[2]
W1_EXPECTED = ROOT / "nyxloom-trove" / "carve-assets" / "W1" / "expected"
P26_EXPECTED = ROOT / "nyxloom-trove" / "carve-assets" / "P26" / "expected"

V7_TEMPLATES = [
    "missing-tool-v7-template.json",
    "sql-r2-v7-template.json",
    "ca1-r3-no-base-v7-template.json",
    "ca4-all-equivalent-v7-template.json",
]

P25_V7_TEMPLATES = ["p25-pass-v7-template.json", "p25-missing-v7-template.json"]

SUBS = {
    "@STARTED@": "2026-08-11T00:00:00+00:00",
    "@ENDED@": "2026-08-11T00:00:01+00:00",
}


def load(path: Path) -> dict:
    text = path.read_text()
    for key, value in SUBS.items():
        text = text.replace(key, value)
    return json.loads(text)


def refuses_only_the_defect(verify_document, clean: dict, broken: dict, why: str):
    assert verify_document(clean) == [], (
        f"{why}: control document must verify clean under v7"
    )
    assert verify_document(broken), why


@pytest.fixture(scope="module")
def verify_document():
    from assay.verify import verify_document as fn

    return fn


# --- identity and hard-cut guards -------------------------------------------


def test_schema_identity_is_internally_consistent_under_v7():
    from assay import verdict as V

    schema = load(ROOT / "src" / "assay" / "schemas" / "verdict.schema.json")
    assert schema["$id"] == "urn:assay:schema:verdict:7"
    assert schema["properties"]["schema_version"]["const"] == 7
    assert V.VERDICT_SCHEMA_VERSION == 7


def test_shipped_schema_is_byte_identical_to_the_locked_v7_asset():
    shipped = (ROOT / "src" / "assay" / "schemas" / "verdict.schema.json").read_bytes()
    locked = (HERE / "verdict.schema.v7.json").read_bytes()
    assert shipped == locked


@pytest.mark.parametrize("name", sorted(path.name for path in W1_EXPECTED.glob("*.json")))
def test_every_frozen_v6_template_is_rejected_under_v7(verify_document, name):
    failures = verify_document(load(W1_EXPECTED / name))
    assert len(failures) == 1
    assert (
        "schema_version 6 is not this verifier's version 7" in failures[0]
    ), failures


# --- migrated v6 controls ----------------------------------------------------


@pytest.mark.parametrize("name", V7_TEMPLATES)
def test_locked_v7_template_is_accepted(verify_document, name):
    assert verify_document(load(HERE / "expected" / name)) == []


@pytest.mark.parametrize("name", P25_V7_TEMPLATES)
def test_p25_v7_siblings_validate(verify_document, name):
    assert verify_document(load(HERE / "expected" / name)) == []


@pytest.mark.parametrize("name", P25_V7_TEMPLATES)
def test_p25_v7_templates_omit_runtime_output_tails(name):
    doc = load(HERE / "expected" / name)
    assert not any(key.startswith("result_stdout") for key in doc)
    assert not any(key.startswith("result_stderr") for key in doc)


@pytest.mark.parametrize(
    "name",
    [
        "current-v4-template.json",
        "stale-directory-v4-template.json",
        "independent-errors-v4-template.json",
        "attestation-timeout-v4-template.json",
    ],
)
def test_p26_attestation_shapes_survive_v7(verify_document, name):
    doc = load(P26_EXPECTED / name)
    doc["schema_version"] = 7
    assert verify_document(doc) == []


# --- B014's paired output-tail fields ---------------------------------------


def test_a_tail_requires_its_dropped_byte_count(verify_document):
    for tail, dropped in [
        ("result_stdout_tail", "result_stdout_dropped_bytes"),
        ("result_stderr_tail", "result_stderr_dropped_bytes"),
    ]:
        clean = load(HERE / "expected" / "missing-tool-v7-template.json")
        broken = load(HERE / "expected" / "missing-tool-v7-template.json")
        broken[tail] = ""
        refuses_only_the_defect(
            verify_document,
            clean,
            broken,
            f"{tail} without {dropped} must be refused",
        )


def test_dropped_bytes_cannot_be_negative_or_miscounted(verify_document):
    clean = load(HERE / "expected" / "missing-tool-v7-template.json")
    broken = load(HERE / "expected" / "missing-tool-v7-template.json")
    broken.update(
        result_stdout_tail="x",
        result_stderr_tail="",
        result_stdout_dropped_bytes=-1,
        result_stderr_dropped_bytes=0,
    )
    refuses_only_the_defect(
        verify_document,
        clean,
        broken,
        "a negative dropped-byte count must be refused",
    )


def test_b015_operators_are_schema_and_verifier_compatible(verify_document):
    doc = load(HERE / "expected" / "sql-r2-v7-template.json")
    doc["judgment"]["r2"]["operators"] = [
        "python:uuid-equality-swap",
        "python:enum-comparison-swap",
    ]
    doc["judgment"]["resolved"]["language"] = "python"
    for bucket in ("killed", "survived"):
        for item in doc["claims"][1]["mutation"].get(bucket, []):
            item["operator"] = (
                "python:uuid-equality-swap"
                if item is doc["claims"][1]["mutation"][bucket][0]
                else "python:enum-comparison-swap"
            )
    failures = verify_document(doc)
    assert not any("unknown field" in failure for failure in failures)
