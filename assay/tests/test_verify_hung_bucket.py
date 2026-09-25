"""B091/RW-33, P7 A3 -- the `hung` bucket is ADDITIVE under schema v11 (no
v12 cut, matching A1's own `budget_per_candidate_derived_s` precedent): a
document produced BEFORE this bucket existed omits the `hung` key entirely
and must still verify clean; a NATIVE document produced by this build always
includes it (possibly empty, and populated when a candidate was actually
classified `hung`), and both shapes must reconstruct/re-derive identically
through :func:`~assay.verify.verify_document`.

Built on the real, hand-written `r2_pass.json` fixture (A-041/A-067's own
independent-oracle discipline -- never assay's own serializer) rather than a
fresh hand-rolled document, so this proves the EXISTING fixture (schema
v11, no `hung` key -- exactly what the field's own additivity claims) still
verifies clean under this session's changes.
"""

from __future__ import annotations

import copy
import json
from pathlib import Path

from assay.verify import verify_document

_FIXTURE = (
    Path(__file__).parent / "fixtures" / "verdicts" / "r2_pass.json"
)


def _load() -> dict:
    return json.loads(_FIXTURE.read_text(encoding="utf-8"))


def test_pre_hung_document_with_no_hung_key_still_verifies_clean() -> None:
    """A document shaped exactly like one produced BEFORE this session --
    schema v11, mutation payload with the ORIGINAL five buckets and no
    `hung` key at all -- must still verify clean. The fixture on disk now
    carries `hung: []` itself (A-041/A-067's independent-oracle discipline:
    `Mutation.to_dict()` emits it unconditionally as of this session, so the
    hand-written oracle must match what real code now produces), so the
    PRE-existing shape is reconstructed here explicitly by deleting the key
    -- this is the additive-schema regression test BRIEF-3 asks for, not a
    claim about what this fixture file currently contains.
    """
    document = _load()
    mutation = document["claims"][1]["mutation"]
    assert "hung" in mutation  # confirms the fixture DOES carry it now.
    del mutation["hung"]
    assert verify_document(document) == []


def test_a_hung_entry_verifies_clean_and_counts_toward_total() -> None:
    """A `hung` entry also flips the OVERALL R2 claim (and top-level
    document) outcome to `BUDGET_EXCEEDED`/`CANDIDATE_HUNG` -- the real gap
    this session found in `mutation.judge_mutation`'s own precedence chain
    (a `hung`-only candidate used to fall all the way through to `PASS`).
    Both the outcome/status/reason_code AND the bucket counts are updated
    here to the shape a real hung candidate produces, so this test proves
    the FULL round trip, not just the bucket arithmetic.
    """
    document = _load()
    r2_claim = document["claims"][1]
    mutation = r2_claim["mutation"]
    hung_entry = {
        "path": "pkg/checks.py",
        "lineno": 30,
        "start_byte": 300,
        "end_byte": 302,
        "replacement_sha256": "c10987bd7cf853f6ea92ddac1b6c95fa830e3aee160cc5d4ba2fea3743be1aa2",
        "operator": "python:compare-swap",
        "description": "Eq->NotEq",
        "candidate_id": "5a15c9c792fc3a529a4fe0436e05e5b9a848c78aef9cfac34eaa2de09fe83cc4",
        "source_sha256": "4d6ae68c084958848c0f45ca958aeca0ebd5089d71b0e4d8aa27e70f20001298",
        "mutated_file_sha256": "93174d3ddf91258398d19c1009184df60c239e33654425191ca696960ac83dc0",
        "execution": {"mode": "full"},
    }
    mutation["hung"] = [hung_entry]
    mutation["candidate_count"] = 3
    mutation["total"] = 3
    mutation["candidate_ids"].append(hung_entry["candidate_id"])
    document["outcome"] = "BUDGET_EXCEEDED"
    document["exit_code"] = 4
    document["reason_code"] = "CANDIDATE_HUNG"
    r2_claim["status"] = "BUDGET_EXCEEDED"
    r2_claim["reason_code"] = "CANDIDATE_HUNG"
    assert verify_document(document) == []


def test_a_hung_entry_uncounted_in_total_is_refused() -> None:
    """The raw arithmetic re-derivation (`total` must equal the bucket sum)
    applies to `hung` exactly like every other bucket -- proving `hung` was
    actually threaded into that check, not merely tolerated as an unknown
    field.
    """
    document = _load()
    mutation = document["claims"][1]["mutation"]
    mutation["hung"] = [
        {
            "path": "pkg/checks.py",
            "lineno": 30,
            "start_byte": 300,
            "end_byte": 302,
            "replacement_sha256": "c10987bd7cf853f6ea92ddac1b6c95fa830e3aee160cc5d4ba2fea3743be1aa2",
            "operator": "python:compare-swap",
            "description": "Eq->NotEq",
            "candidate_id": "5a15c9c792fc3a529a4fe0436e05e5b9a848c78aef9cfac34eaa2de09fe83cc4",
            "source_sha256": "4d6ae68c084958848c0f45ca958aeca0ebd5089d71b0e4d8aa27e70f20001298",
            "mutated_file_sha256": "93174d3ddf91258398d19c1009184df60c239e33654425191ca696960ac83dc0",
            "execution": {"mode": "full"},
        }
    ]
    # `total`/`candidate_count` deliberately left at their ORIGINAL 2 --
    # the bucket sum is now 3.
    failures = verify_document(document)
    assert any("attempted mutant" in failure for failure in failures)


def test_a_hung_entry_also_present_in_another_bucket_is_refused() -> None:
    """A-182's identity-uniqueness rule applies to `hung` too: the SAME
    identity cannot be both `killed` and `hung`."""
    document = _load()
    mutation = document["claims"][1]["mutation"]
    duplicate = copy.deepcopy(mutation["killed"][0])
    mutation["hung"] = [duplicate]
    mutation["candidate_count"] = 3
    mutation["total"] = 3
    failures = verify_document(document)
    assert any("recorded in both" in failure for failure in failures)
