"""B108 step 3a (W9): verdict-shape refusals.

Each row edits a copy of the ``r2_pass`` verdict document; the plan and the progress
stream stay the originals, so the verdict is the only input under test. The command
must exit 2 with an evidence error whose first message holds the fragment.

CD58: the campaign reader used to repeat a dozen shape checks that
``evidence.verify_text`` (``_read_verified_verdict``) already enforces on every verdict
before ``_candidate_outcomes`` or ``_verdict_inventory`` runs. Those repeats are deleted;
``DOMINATED`` pins that every input which used to reach them is still refused, by the
verifier, with the ``invalid Assay verdict`` prefix.
"""

from __future__ import annotations

import copy
import json

import pytest

from analysis.tests.test_analysis_campaign import (
    _fixture_document,
    _install_plan,
    _invoke,
    _plan_rows,
    _repository,
    _validate,
    _write_progress,
)


def _claim(document):
    return next(item for item in document["claims"] if item["rigor"] == "R2")


def _mutation(document):
    return _claim(document)["mutation"]


def _first_bucket(document):
    mutation = _mutation(document)
    return next(name for name in ("killed", "survived") if mutation.get(name))


def _allow_dirty(document):
    integrity = document.setdefault("worktree_integrity", {})
    integrity.setdefault("ignored_dirty_paths", [])
    integrity["overridden_dirty_paths"] = ["scratch/x.py"]


def _outcome_field_differs(document):
    mutation = _mutation(document)
    mutation[_first_bucket(document)][0]["lineno"] += 1


def _consistent_short_inventory(document):
    """A self-consistent verdict for a smaller plan than the reconstructed one."""
    mutation = _mutation(document)
    dropped = mutation[_first_bucket(document)].pop(0)
    mutation["candidate_ids"].remove(dropped["candidate_id"])
    mutation["total"] -= 1
    mutation["candidate_count"] -= 1


REACHABLE = (
    ("verdict-allow-dirty-overrides", _allow_dirty, "verdict records --allow-dirty overrides"),
    ("outcome-field-differs-from-plan", _outcome_field_differs, "differs from the current plan"),
    ("inventory-differs-from-plan", _consistent_short_inventory, "differs from the current lane plan"),
)


def _bucket(value):
    def mutate(document):
        _mutation(document)["killed"] = value

    return mutate


def _first_outcome(edit):
    def mutate(document):
        edit(_mutation(document)[_first_bucket(document)][0])

    return mutate


def _in_two_buckets(document):
    mutation = _mutation(document)
    mutation["equivalent"] = [copy.deepcopy(mutation[_first_bucket(document)][0])]


def _same_bucket_twice(document):
    mutation = _mutation(document)
    bucket = _first_bucket(document)
    mutation[bucket] = [mutation[bucket][0], copy.deepcopy(mutation[bucket][0])]


def _ids(edit):
    def mutate(document):
        mutation = _mutation(document)
        mutation["candidate_ids"] = edit(mutation["candidate_ids"])

    return mutate


def _pre_submission_with_outcomes(document):
    _claim(document)["reason_code"] = "MUTANT_LIMIT_EXCEEDED"
    _mutation(document)["total"] = 0


def _outcome_dropped(document):
    mutation = _mutation(document)
    mutation[_first_bucket(document)] = mutation[_first_bucket(document)][1:]


def _inventory_dropped(document):
    del _mutation(document)["candidate_ids"]


DOMINATED = (
    ("bucket-is-a-string", _bucket("x")),
    ("bucket-is-an-object", _bucket({})),
    ("bucket-is-null", _bucket(None)),
    ("outcome-is-a-number", _bucket([1])),
    ("outcome-is-a-string", _bucket(["x"])),
    ("outcome-id-malformed", _first_outcome(lambda outcome: outcome.update(candidate_id="zz"))),
    ("outcome-in-two-buckets", _in_two_buckets),
    ("outcome-twice-in-one-bucket", _same_bucket_twice),
    ("inventory-absent", _inventory_dropped),
    ("inventory-malformed", _ids(lambda ids: ["zz"])),
    ("inventory-duplicated", _ids(lambda ids: [*ids, ids[0]])),
    ("inventory-short", _ids(lambda ids: ids[:1])),
    ("pre-submission-with-outcomes", _pre_submission_with_outcomes),
    ("outcomes-do-not-exhaust-inventory", _outcome_dropped),
)


def _run(tmp_path, monkeypatch, mutate):
    original = _fixture_document("r2_pass")
    rows = _plan_rows(original)
    edited = copy.deepcopy(original)
    mutate(edited)
    root, head, verdict = _repository(tmp_path, edited)
    _install_plan(monkeypatch, original, rows)
    progress = tmp_path / "progress.jsonl"
    _write_progress(progress, head=head, document=original, plan_rows=rows)
    code, out, _err = _invoke(root, head, verdict, progress, command_exit=0)
    document = json.loads(out)
    _validate(document)
    return code, document


@pytest.mark.parametrize(("case_id", "mutate", "fragment"), REACHABLE, ids=[case[0] for case in REACHABLE])
def test_every_reachable_verdict_shape_refusal_names_the_verdict_source(
    tmp_path, monkeypatch, case_id, mutate, fragment
):
    code, document = _run(tmp_path, monkeypatch, mutate)
    assert code == 2, (case_id, document.get("status"))
    assert fragment in document["errors"][0]["message"], (case_id, document["errors"])
    assert document["errors"][0]["source"] == "verdict"


@pytest.mark.parametrize(("case_id", "mutate"), DOMINATED, ids=[case[0] for case in DOMINATED])
def test_shape_breaks_the_campaign_reader_no_longer_repeats_are_refused_by_the_verifier(
    tmp_path, monkeypatch, case_id, mutate
):
    code, document = _run(tmp_path, monkeypatch, mutate)
    assert code == 2, (case_id, document.get("status"))
    assert document["errors"][0]["message"].startswith("invalid Assay verdict: "), (case_id, document["errors"])
    assert document["errors"][0]["source"] == "verdict"
