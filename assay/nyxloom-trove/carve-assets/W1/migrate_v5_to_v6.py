#!/usr/bin/env python3
"""Carver-owned v5 -> v6 verdict DOCUMENT transform. THE auditable delta for
wave-1's single v5->v6 hard cut (`W1-CARVE-branch-coverage-and-whole-target.md`
§6 "Migration", A-261, and `W1-CARVE-B006a-project-scope.md` §5.3's amendment).

Unlike P33's `migrate_v4_to_v5.py` (a SCHEMA-DEFINITION transform: one frozen
v4 schema snapshot in, one v5 schema file out), this script transforms
VERDICT DOCUMENT INSTANCES -- the real artifacts under
`tests/fixtures/verdicts/`, which are migrated IN PLACE rather than written to
a parallel v6-suffixed tree. There is therefore no permanent "v5 source"
file for `--check` to diff against the way P33's frozen v4 snapshot is; the
honest equivalent is:

* a CLASSIFIER that scans the whole repository for the three patterns named
  in the carve (`"schema_version": 5`, `VERDICT_SCHEMA_VERSION`,
  `urn:assay:schema:verdict:5`) and sorts every match into exactly one of
  four buckets, refusing to run if any file falls into none (fail-closed
  beats a sweep); and
* a small, embedded set of representative v5 DOCUMENT INSTANCES (one per
  migration case this transform must handle) that `transform_document` is
  proven against on every `--check` run: the result must be schema-v6-valid,
  `assay.verify`-clean, and schema-v5-INVALID (the differential A-252 asks
  for), and the hostile "already carries snapshot_policy" input must be
  REFUSED rather than merged or overwritten.

Run from the assay project root:

    python3 nyxloom-trove/carve-assets/W1/migrate_v5_to_v6.py --check

Design authority: `nyxloom-trove/W1-CARVE-branch-coverage-and-whole-target.md`
§6 "Migration", A-261/A-262/A-264; `nyxloom-trove/W1-CARVE-B006a-project-scope.md`
§5.3 (the AMENDED rule this script actually implements: `snapshot_policy` is
keyed on DECLARED RIGOR, never on outcome, and never on a synthesised
`isolation`/`materialisation` object -- both withdrawn by A-269).
"""

from __future__ import annotations

import argparse
import copy
import json
import subprocess
import sys
from pathlib import Path
from typing import Any

HERE = Path(__file__).resolve().parent
# nyxloom-trove/carve-assets/W1 -> assay project root is three parents up.
PROJECT_ROOT = HERE.parents[2]
assert (PROJECT_ROOT / "pyproject.toml").is_file(), (
    f"expected assay's project root at {PROJECT_ROOT}, found none"
)

sys.path.insert(0, str(PROJECT_ROOT / "src"))

#: The three patterns the carve names, verbatim. A `git grep -l` hit list is
#: an inventory of PATTERN MATCHES, never itself the set of verdict
#: documents -- P33's own `sweep_v4_consumers.py` lesson, restated for this
#: migration's own bucket rule (A4 addendum, `4-backlog.md`).
SCAN_PATTERNS: tuple[str, ...] = (
    '"schema_version": 5',
    "VERDICT_SCHEMA_VERSION",
    "urn:assay:schema:verdict:5",
)

#: Bucket 3 (hand-edit source): files whose v5->v6 edit is source code, a
#: version-coupled test assertion, or the registered gate's own deselection
#: wiring, made by hand elsewhere in this same commit -- listed here so the
#: classifier can name them rather than leaving them to fall through
#: unclassified. Most of these read `VERDICT_SCHEMA_VERSION` as a SYMBOL
#: (never a literal `5`), so bumping the constant in `verdict.py` is the
#: entire edit; nothing else needed a textual change there.
#: `tools/tester-unified-gate.sh` is the one exception in KIND, not degree:
#: its own hand-edit is the 26 new `--deselect` nodeids the schema-version
#: hard cut reddens in `carve-assets/P33/test_acceptance_v5.py` (measured,
#: per this wave's own §6 instruction), and its explanatory comment names
#: `VERDICT_SCHEMA_VERSION` in PROSE rather than reading it as code -- still
#: a hand-edit made because of this exact migration, so it belongs here
#: rather than in bucket 4's "merely mentions the string" reading.
#: `nyxloom-trove/carve-assets/W1/test_acceptance_v6.py` is this same
#: migration's own one-for-one successor suite for those 26 deselections
#: (§6's own instruction): it reads `VERDICT_SCHEMA_VERSION` as a real
#: symbol (asserting it now equals 6) AND names the literal v5 `$id` string
#: in prose, documenting what the LOCKED v5 sibling test asserted -- a
#: brand-new file, hand-authored in this exact commit, entirely because of
#: this exact migration.
HAND_EDIT_SOURCE_FILES: frozenset[str] = frozenset(
    {
        "src/assay/__init__.py",
        "src/assay/verdict.py",
        "src/assay/verify.py",
        "src/assay/schemas/verdict.schema.json",
        "tests/test_distribution_build_release.py",
        "tests/test_self_hosting.py",
        "tests/test_verdict_conformance.py",
        "tests/test_verdict_schema_is_packaged.py",
        "tests/test_verdict_serialises.py",
        "tools/tester-unified-gate.sh",
        "nyxloom-trove/carve-assets/W1/test_acceptance_v6.py",
    }
)

#: Bucket 4 (must not change): files that merely MENTION one of the three
#: strings without being a verdict document, a schema, or a version-coupled
#: test -- each with the reason it is exempt, so a future reader does not
#: have to rediscover why.
MUST_NOT_CHANGE_FILES: dict[str, str] = {
    "nyxloom-trove/4-backlog.md": "prose citing the v5/v6 history, not a document or schema",
    "nyxloom-trove/decisions.md": "the decisions ledger; A-257..A-269's own text names v5/v6",
    "nyxloom-trove/W1-CARVE-branch-coverage-and-whole-target.md": (
        "this specification names its own patterns; matches itself (A4 addendum)"
    ),
    "nyxloom-trove/W1-RESUME.md": "resume notes citing the schema-version history",
    "nyxloom-trove/reports/assay-B006a-carve-review-fable.md": "historical review report",
    "nyxloom-trove/reports/assay-P01-verdict-model-and-schema-LOG.md": "historical package log",
    "nyxloom-trove/reports/assay-P16-independent-verdict-conformance-v3-LOG.md": (
        "historical package log"
    ),
    "nyxloom-trove/reports/assay-P21-JIT-CARVE.md": "historical carve document",
    "nyxloom-trove/reports/assay-P33-JIT-CARVE.md": "historical carve document",
    "nyxloom-trove/reports/assay-P33-pre-dispatch-adversarial-review.md": (
        "historical review report"
    ),
    "nyxloom-trove/reports/assay-P33-pre-dispatch-adversarial-review-round4.md": (
        "historical review report"
    ),
    "nyxloom-trove/reports/assay-v3-post-P33-review-fable.md": "historical review report",
    "tests/test_output_reservation.py": (
        "A4 addendum: the literal '{\"schema_version\": 5}' is an ARBITRARY test "
        "payload proving the output-reservation writer round-trips bytes exactly; "
        "it asserts nothing about the real verdict schema and is not itself a "
        "verdict document"
    ),
    # This script's own source necessarily contains the three patterns as
    # SCAN_PATTERNS literals and in this very table's keys/prose -- it is the
    # classifier, not a document the classifier classifies. Self-referential,
    # exactly like the carve document's own row above.
    "nyxloom-trove/carve-assets/W1/migrate_v5_to_v6.py": (
        "this classifier's own source contains the three scan patterns as data"
    ),
}


def classify_repository() -> dict[str, list[str]]:
    """Scan the whole repository for :data:`SCAN_PATTERNS` and sort every
    matching TRACKED file into exactly one of four buckets. Raises
    :class:`RuntimeError` (fail-closed, never a silent skip) if any matched
    file falls into none.
    """
    pattern = "|".join(SCAN_PATTERNS)
    matches = subprocess.run(
        ["git", "grep", "-lE", pattern],
        cwd=PROJECT_ROOT,
        capture_output=True,
        text=True,
    )
    # `git grep` exits 1 when there are zero matches -- not an error here.
    if matches.returncode not in (0, 1):
        raise RuntimeError(f"git grep failed: {matches.stderr}")
    files = sorted(line for line in matches.stdout.splitlines() if line)

    buckets: dict[str, list[str]] = {
        "transform": [],
        "preserve-byte-identical": [],
        "hand-edit-source": [],
        "must-not-change": [],
    }
    unclassified: list[str] = []

    for rel in files:
        if rel.startswith("nyxloom-trove/carve-assets/") and not rel.startswith(
            "nyxloom-trove/carve-assets/W1/"
        ):
            buckets["preserve-byte-identical"].append(rel)
        elif rel.startswith("tests/fixtures/verdicts/") and rel.endswith(".json"):
            buckets["transform"].append(rel)
        elif rel in HAND_EDIT_SOURCE_FILES:
            buckets["hand-edit-source"].append(rel)
        elif rel in MUST_NOT_CHANGE_FILES:
            buckets["must-not-change"].append(rel)
        else:
            unclassified.append(rel)

    if unclassified:
        raise RuntimeError(
            "REFUSING: the following file(s) matched a scan pattern but fall "
            "into none of the four buckets -- a fail-closed classifier stops "
            "rather than silently skipping them:\n  "
            + "\n  ".join(unclassified)
        )
    return buckets


class HostileDocumentError(ValueError):
    """Raised when a v5 input already carries `snapshot_policy` -- refused,
    never merged or overwritten (§6 Migration bucket 1, third clause)."""


def transform_document(v5: dict[str, Any]) -> dict[str, Any]:
    """One v5 verdict DOCUMENT, transformed to v6 in place (a fresh deep
    copy is returned; *v5* is never mutated).

    Three rules, all from §6's Migration bucket 1 and its A-269 amendment:

    1. ``schema_version``: 5 -> 6.
    2. Every claim's ``coverage`` payload (if any): ``changed_executable``
       renamed ``executable`` (A-262), and the five new branch fields
       DEFAULTED to the "no branch data" shape (``branches_covered``/
       ``branches_total`` 0, ``branch_capability`` "unavailable",
       ``missing_branch_lines``/``files_with_missing_branch_lines`` empty)
       -- the honest historical fact: branch judging did not exist under
       v5, so every v5 R1 claim measured none. ``judgment.r1`` (if any)
       gains ``mode: "changed_lines"`` and ``require_branch: false`` for
       the identical reason -- the only mode and policy that existed then.
    3. ``snapshot_policy`` (B006a §5.3, AMENDED by A-269 -- keyed on
       DECLARED RIGOR, never on outcome, and never a synthesised
       ``isolation``/``materialisation`` object): a document already
       carrying the key is REFUSED (:class:`HostileDocumentError`); a
       document whose ``declared_rigor`` contains R1, R2 or R3 gets
       ``{"selection": "repository"}`` inserted; an R0-only or
       lane-never-resolved document gets no object at all.

    Raises :class:`HostileDocumentError` on the third rule's refusal --
    the ONLY way this function raises.
    """
    if "snapshot_policy" in v5:
        raise HostileDocumentError(
            "input document already carries 'snapshot_policy' -- refusing "
            "rather than merging or overwriting an existing key (§6 Migration "
            "bucket 1, third clause)"
        )
    v6 = copy.deepcopy(v5)
    v6["schema_version"] = 6

    for claim in v6.get("claims", []):
        coverage = claim.get("coverage")
        if coverage is not None and "changed_executable" in coverage:
            coverage["executable"] = coverage.pop("changed_executable")
            coverage.setdefault("branches_covered", 0)
            coverage.setdefault("branches_total", 0)
            coverage.setdefault("branch_capability", "unavailable")
            coverage.setdefault("missing_branch_lines", {})
            coverage.setdefault("files_with_missing_branch_lines", [])

    judgment = v6.get("judgment")
    if isinstance(judgment, dict) and isinstance(judgment.get("r1"), dict):
        judgment["r1"].setdefault("mode", "changed_lines")
        judgment["r1"].setdefault("require_branch", False)

    declared_rigor = v6.get("declared_rigor")
    if declared_rigor is not None and any(level != "R0" for level in declared_rigor):
        v6["snapshot_policy"] = {"selection": "repository"}

    return v6


# ---------------------------------------------------------------------------
# --check's own proof: transform_document against one representative v5
# DOCUMENT per migration case, verified against the real shipped v6 schema
# AND the real assay.verify -- never a hand-rolled re-implementation of
# either (A-056).
# ---------------------------------------------------------------------------


def _example_documents() -> dict[str, dict[str, Any]]:
    """One synthetic-but-realistic v5 verdict document per migration case.

    Each is a MINIMAL, schema-v5-shaped instance -- not read back from any
    v5 schema definition this build no longer ships (A-261's hard cut means
    there is no v5 validator left to check them against; their v5-shape
    honesty is instead the same hand-authored-independently discipline
    every fixture under `tests/fixtures/verdicts/` already follows). Every
    one was schema-v5-valid before this wave's own hard cut, which is
    precisely why they are worth transforming.
    """
    r0_pass = {
        "rigor": "R0", "source": "computed", "status": "PASS",
        "verified_by_assay": True,
    }
    base_fields = {
        "assay_version": "0.1.0",
        "lane": "package",
        "commit": "1" * 40,
        "started": "2026-08-16T09:00:00+00:00",
        "ended": "2026-08-16T09:00:01+00:00",
        "declared_evidence": [],
        "argv_declared": ["pytest", "-q"],
        "argv_appended": [],
        "argv_effective": ["pytest", "-q"],
        "argv_modified": False,
        "env_declared": {},
        "env_effective": {},
        "scope": "S1",
        "enforcement": "gate",
        "evidence": [],
    }

    r0_only = {
        "schema_version": 5,
        **base_fields,
        "outcome": "PASS",
        "exit_code": 0,
        "declared_rigor": ["R0"],
        "claims": [r0_pass],
    }

    r1_pass = {
        "schema_version": 5,
        **base_fields,
        "outcome": "PASS",
        "exit_code": 0,
        "declared_rigor": ["R0", "R1"],
        "judgment": {
            "resolved": {
                "language": "python", "source_roots": ["src"], "base": "a" * 40,
            },
            "r1": {
                "coverage_format": "coverage-py-json",
                "coverage_artifact": "cov.json",
                "fail_under": 100.0,
                "allow_excluded": False,
            },
        },
        "claims": [
            r0_pass,
            {
                "rigor": "R1", "source": "computed", "status": "PASS",
                "verified_by_assay": True,
                "coverage": {
                    "covered": 2, "changed_executable": 2, "pct": 100.0,
                    "considered": 1, "exclusion_capability": "reported",
                    "missing_lines": {}, "files_missing_coverage": [],
                    "unclassified_lines": {}, "files_with_unclassified_lines": [],
                    "excluded_lines": {}, "files_with_excluded_lines": [],
                },
            },
        ],
    }

    r2_only = {
        "schema_version": 5,
        **base_fields,
        "outcome": "INCONCLUSIVE",
        "reason_code": "NO_MUTANTS",
        "exit_code": 5,
        "declared_rigor": ["R0", "R2"],
        "judgment": {
            "resolved": {
                "language": "python", "source_roots": ["pkg"], "base": "a" * 40,
            },
            "r2": {
                "jobs": 1, "max_mutants": 50, "operators": ["python:compare-swap"],
                "kill_attribution": "unattributed",
            },
        },
        "claims": [
            r0_pass,
            {
                "rigor": "R2", "source": "computed", "status": "INCONCLUSIVE",
                "verified_by_assay": True, "reason_code": "NO_MUTANTS",
                "mutation": {
                    "candidate_count": 0, "total": 0, "killed": [], "survived": [],
                    "crashed": [], "budget_exceeded": [], "equivalent": [],
                },
            },
        ],
    }

    r3_only = {
        "schema_version": 5,
        **base_fields,
        "outcome": "PASS",
        "exit_code": 0,
        "declared_rigor": ["R0", "R3"],
        "judgment": {
            "resolved": {"language": "python", "source_roots": ["pkg"]},
            "r3": {"mechanism": "uncovered-line", "target": "pkg/mod.py"},
        },
        "claims": [
            r0_pass,
            {
                "rigor": "R3", "source": "computed", "status": "PASS",
                "verified_by_assay": True,
                "canary": {
                    "mechanism": "uncovered-line", "target": "pkg/mod.py",
                    "description": "appended a never-called function",
                    "control_outcome": "PASS", "transformed_outcome": "FAIL",
                    "expected_reason_code": "UNCOVERED_LINES",
                    "observed_reason_code": "UNCOVERED_LINES",
                },
            },
        ],
    }

    hostile_already_has_key = {
        "schema_version": 5,
        **base_fields,
        "outcome": "PASS",
        "exit_code": 0,
        "declared_rigor": ["R0"],
        "claims": [r0_pass],
        # The hostile input: a v5 document that already (illegitimately,
        # since the key does not exist in any real v5 schema) carries the
        # v6 key. transform_document must refuse this, never merge/overwrite.
        "snapshot_policy": {"selection": "repository"},
    }

    return {
        "r0_only_no_object_inserted": r0_only,
        "r1_pass_repository_inserted": r1_pass,
        "r2_only_repository_inserted": r2_only,
        "r3_only_repository_inserted": r3_only,
        "hostile_pre_existing_key_refused": hostile_already_has_key,
    }


def run_transform_proof(*, verbose: bool = True) -> list[str]:
    """Run :func:`transform_document` against every case in
    :func:`_example_documents`, independently checked against the REAL
    shipped v6 schema and the REAL `assay.verify` (never a hand-rolled
    re-implementation of either, A-056). Returns the log lines; raises
    :class:`AssertionError` on the first violated expectation.
    """
    from jsonschema import Draft202012Validator

    from assay.verdict import load_schema
    from assay.verify import verify_document

    schema = load_schema()
    validator = Draft202012Validator(schema)
    log: list[str] = []

    for name, v5_doc in _example_documents().items():
        if name == "hostile_pre_existing_key_refused":
            try:
                transform_document(v5_doc)
            except HostileDocumentError as exc:
                log.append(f"{name}: REFUSED as expected ({exc})")
                continue
            raise AssertionError(
                f"{name}: transform_document accepted a document that already "
                f"carried snapshot_policy -- it must refuse"
            )

        v6_doc = transform_document(v5_doc)
        assert v6_doc["schema_version"] == 6, name
        assert v5_doc["schema_version"] == 5, f"{name}: input was mutated"

        # A-252's differential validity sweep, per-document: valid under v6...
        schema_errors = list(validator.iter_errors(v6_doc))
        assert not schema_errors, f"{name}: transformed document is schema-v6-INVALID: {schema_errors}"
        verify_errors = verify_document(v6_doc)
        assert not verify_errors, f"{name}: assay.verify rejects the transformed document: {verify_errors}"

        # ...and a document valid under BOTH v5 and v6 would mean the
        # migration did not actually change anything (the hard-cut
        # expectation A-252 inverts for a v4/v5-style additive migration).
        # There is no v5 validator left in this build (A-261's hard cut),
        # so the honest, re-derivable proxy is structural: the transformed
        # document's own schema_version const no longer equals 5, and (for
        # every R1 claim) it no longer has the renamed key.
        assert v6_doc.get("schema_version") != 5, name
        for claim in v6_doc.get("claims", []):
            coverage = claim.get("coverage")
            if coverage is not None:
                assert "changed_executable" not in coverage, name

        declared_rigor = v6_doc.get("declared_rigor")
        higher_rigor = declared_rigor is not None and any(
            level != "R0" for level in declared_rigor
        )
        if higher_rigor:
            assert v6_doc.get("snapshot_policy") == {"selection": "repository"}, name
        else:
            assert "snapshot_policy" not in v6_doc, name

        log.append(f"{name}: OK (schema-v6-valid, verify-clean, correctly ruled)")

    if verbose:
        for line in log:
            print(f"  - {line}")
    return log


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument(
        "--check", action="store_true",
        help="classify the repository and prove the transform, writing nothing",
    )
    args = ap.parse_args()

    try:
        buckets = classify_repository()
    except RuntimeError as exc:
        print(str(exc), file=sys.stderr)
        return 1

    print("Classification (fail-closed -- every scanned file is in exactly one bucket):")
    for bucket, items in buckets.items():
        print(f"  {bucket}: {len(items)} file(s)")
        for item in items:
            print(f"    - {item}")

    try:
        run_transform_proof()
    except AssertionError as exc:
        print(f"TRANSFORM PROOF FAILED: {exc}", file=sys.stderr)
        return 1

    if args.check:
        # The differential half a re-runner can actually observe today:
        # every currently-committed fixture under tests/fixtures/verdicts/
        # must ALREADY be past this migration -- none of them may still
        # match a v5 scan pattern (drift = a fixture nobody migrated).
        drifted = [
            item
            for item in buckets["transform"]
            if any(
                pattern in (PROJECT_ROOT / item).read_text(encoding="utf-8")
                for pattern in SCAN_PATTERNS
            )
        ]
        if drifted:
            print(
                "DRIFT: the following fixture(s) are still v5-shaped:\n  "
                + "\n  ".join(drifted),
                file=sys.stderr,
            )
            return 1
        print(
            f"OK: all {len(buckets['transform'])} live verdict fixture(s) are "
            f"past the v5->v6 migration, and the transform itself is proven "
            f"against {len(_example_documents())} representative cases"
        )
        return 0

    print("OK: classification and transform proof both passed (no --check; nothing written)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
