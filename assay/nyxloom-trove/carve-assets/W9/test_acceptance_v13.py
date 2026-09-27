"""W9's v13 successors for the P25 whole-artifact qualification."""

from __future__ import annotations

import json
from pathlib import Path

from jsonschema import Draft202012Validator

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[2]
W8_EXPECTED = ROOT / "nyxloom-trove" / "carve-assets" / "W8" / "expected"
V13_TEMPLATES = (
    "p25-pass-v13-template.json",
    "p25-missing-v13-template.json",
)


def _load(path: Path) -> dict:
    text = path.read_text(encoding="utf-8")
    text = text.replace("@STARTED@", "2026-09-25T00:00:00+00:00")
    text = text.replace("@ENDED@", "2026-09-25T00:00:01+00:00")
    return json.loads(text)


def test_shipped_schema_is_byte_identical_to_the_locked_v13_asset():
    shipped = (ROOT / "src/assay/schemas/verdict.schema.json").read_bytes()
    locked = (HERE / "verdict.schema.v13.json").read_bytes()
    assert shipped == locked


def test_p25_successors_are_v13_schema_and_verifier_accepted():
    from assay.verdict import VERDICT_SCHEMA_VERSION, load_schema
    from assay.verify import verify_document

    assert VERDICT_SCHEMA_VERSION == 13
    validator = Draft202012Validator(load_schema())
    for name in V13_TEMPLATES:
        document = _load(HERE / "expected" / name)
        assert document["schema_version"] == 13
        validator.validate(document)
        assert verify_document(document) == []


def test_w8_p25_templates_remain_frozen_and_hit_the_v13_hard_cut():
    from assay.verify import verify_document

    for name in (
        "p25-pass-v12-template.json",
        "p25-missing-v12-template.json",
    ):
        failures = verify_document(_load(W8_EXPECTED / name))
        assert failures == [
            "schema_version 12 is not this verifier's version 13: a verdict "
            "artifact is rejected, never upgraded in place -- re-produce it "
            "with an assay whose VERDICT_SCHEMA_VERSION is 13"
        ]
