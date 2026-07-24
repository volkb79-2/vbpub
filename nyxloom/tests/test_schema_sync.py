"""Guard: keep duplicated JSON schemas byte-identical.

The statefile JSON schema is maintained in two copies — a top-level *published*
`schemas/statefile.schema.json` and the *packaged* `src/nyxloom/schemas/statefile.schema.json`.
They MUST stay byte-identical. A divergence has silently slipped through twice
already (the D-CORRECT-2 role-enum add, and the F017 GateResult phase-enum add),
each time caught late by a human/AI reviewer rather than mechanically. This test
turns the *next* divergence into a gate failure — the structural fix that makes
the duplication safe instead of relying on someone noticing.

If you intentionally change one copy, change BOTH.
"""
from pathlib import Path

_NYXLOOM_ROOT = Path(__file__).resolve().parent.parent  # .../nyxloom


def test_statefile_schema_copies_are_byte_identical():
    top = _NYXLOOM_ROOT / "schemas" / "statefile.schema.json"
    packaged = _NYXLOOM_ROOT / "src" / "nyxloom" / "schemas" / "statefile.schema.json"
    assert top.exists(), f"missing published schema copy: {top}"
    assert packaged.exists(), f"missing packaged schema copy: {packaged}"
    assert top.read_bytes() == packaged.read_bytes(), (
        "schemas/statefile.schema.json and src/nyxloom/schemas/statefile.schema.json "
        "have diverged — the two copies must be byte-identical; update BOTH."
    )
