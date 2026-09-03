# Assay Wave D — Reviewer R-2, round 1

**Range:** `93188912..a4528144` (phase-1 tip through generation 12's checkpoint) on `feature/assay-wave-d-v10`.
**Verdict: ACCEPT-conditional.** One SHOULD-FIX (a verification-completeness gap in the new B007 payload's independent re-derivation) should land before merge; everything else checked out clean, including a second post-cut schema touch beyond the one the dispatch prompt named, which turned out to be correctly authorised and executed.

## Blockers

None.

## Should-fix

**SF-1 — `_check_r3_rederivation` never validates the `budget_exhausted` bookkeeping member, so a document claiming a judged R3 status while a target is recorded `not_attempted`/`budget_exhausted` verifies clean.**

`assay/src/assay/verify.py:2346-2369` validates two of the three closed `not_attempted_reason` members (`short_circuited`, `earlier_target_terminal`) but has no branch for the third, `budget_exhausted`. By construction (`canary.run_isolated_canaries`, `assay/src/assay/canary.py:715-742`) a real producer never pairs `budget_exhausted` with a judged claim status — budget exhaustion always ends the run as `BUDGET_EXCEEDED`/`LANE_TIMEOUT` — but nothing enforces that on the verify side, and the JSON Schema can't express it either (it's a cross-object rule between `claims[].status` and `claims[].canary.attempts[].not_attempted_reason`, exactly the class of rule this project's independent verifier exists to catch — see the module's own O2 docstring in `test_verdict_conformance.py`). The function's own comment at `verify.py:2327-2328`, about the `BUDGET_EXCEEDED` claim-status exemption, states the intent this misses: *"The BOOKKEEPING below still applies to it in full: what may not be checked is the status, not the record."* The record is not, in fact, checked — for this one member.

Confirmed this isn't merely untested: `tests/test_canary_multi_target.py::test_a_budget_exhausted_r3_claim_reaches_the_wire_with_its_payload` is the only test naming `budget_exhausted`, and it only exercises the CONSISTENT pairing (claim really is `BUDGET_EXCEEDED`/`LANE_TIMEOUT`, `_accepted(verdict)`); there is no negative test for the mismatched pairing.

Reproduction (real artifact, not a synthetic fixture): took the W6 drift-guard's own real-run template `nyxloom-trove/carve-assets/W6/expected/multi-target-r3-v10-template.json` (a genuine `assay run` output, PASS via `any`-short-circuit) and flipped its one `not_attempted` entry's `not_attempted_reason` from `"short_circuited"` to `"budget_exhausted"`, leaving the claim's own `status: PASS` untouched:

```python
import sys, json, copy
sys.path.insert(0, "src")
from assay.verify import verify_document
d = json.load(open("nyxloom-trove/carve-assets/W6/expected/multi-target-r3-v10-template.json"))
mutant = copy.deepcopy(d)
for claim in mutant["claims"]:
    if claim.get("rigor") == "R3":
        for attempt in claim["canary"]["attempts"]:
            if attempt.get("disposition") == "not_attempted":
                attempt["not_attempted_reason"] = "budget_exhausted"
print(verify_document(mutant))  # -> []
```

`verify_document(mutant)` returns `[]` — no failures — and the independent `jsonschema` Draft202012Validator over the same mutant also reports zero errors. A self-contradictory document (a judged PASS that also claims one probe never ran because time ran out) is accepted by both layers O2 requires to independently agree.

**Fix:** add the third branch, symmetric with the other two, e.g.:

```python
elif why == "budget_exhausted":
    if (claim.status, claim.reason_code) != (Outcome.BUDGET_EXCEEDED, ReasonCode.LANE_TIMEOUT):
        failures.append(
            f"R3 canary attempt {index} ({attempt.target}) records "
            f"not_attempted_reason 'budget_exhausted', which only the "
            f"mechanism's own BUDGET_EXCEEDED/LANE_TIMEOUT terminal can "
            f"produce -- a judged status is unreachable once the deadline "
            f"cuts a probe short; got {_fmt(claim.status, claim.reason_code)}"
        )
```

plus a regression test built the same way as this reproduction (mutate the real W6 template, assert the specific failure string), alongside the existing `test_a_payload_under_a_refusal_other_than_the_budget_is_refused`-style tests in `tests/test_canary_multi_target.py`.

This does not affect any currently-shipped or currently-producible document — no real call path emits the bad combination — so it is not a blocker on its own; it closes a real hole in this wave's own stated purpose ("the integrity cut") for a payload this same range introduces. Small, well-scoped, one function, one test.

## Nit

**N-1 — A-430's decision-row prose names the adjudicated-evidence lane field `name`; the actual (pre-existing, shipped) `judge.evidence[]` grammar uses `key`, and everything else downstream already got it right.**

`assay/nyxloom-trove/decisions.md` A-430 §(4): *"`judge.evidence = [{source = "adjudicated", name = "image-provenance"}]`"*. But the field has been `key`, not `name`, since P26/A-209 — `assay/src/assay/config.py:118` (`_EVIDENCE_FIELDS = ("source", "key")`), the loader at `config.py:2056-2071`, and `Evidence`'s own dataclass/tests (`tests/test_verdict_claims.py`, `key="sast"`). `docs/CONSUMERS.md`'s migration-notes example (added later, in A-441/`fd489620`) already spells it `key = "image-provenance"` (`docs/CONSUMERS.md:1885`) — correctly, matching the real grammar — so the inconsistency is confined to A-430's own text.

Nothing is built against this yet (B004 hasn't started; DA-R28), so this doesn't block the range under review. Worth relaying promptly to generation 13, which is building B004 in the shared worktree right now: decisions.md is append-only (A-408), so the fix is a short corrective row (the A-431 precedent — "ledger corrections... recorded rather than silently carried") noting A-430(4)'s `name` should read `key`, rather than editing A-430 in place.

## What I verified

**Blind diff pass** (`git diff 93188912..a4528144`, 136 files, +21960/-883) done before reading any brief or the controller log's per-generation detail, to form an independent view; the detailed pass below cross-checked every push-list item against the wave prompt's R-2 section and the rulings named as off-limits (DA-R19/20/21/26/28/30 — not re-litigated).

**Schema/drift-guard touch discipline.** Exactly one `!` commit in range, `b2fd09f3` (97 files: `verdict.py`, `verify.py`, `schemas/verdict.schema.json`, the whole `carve-assets/W6/` drift guard together). Every other commit's diff avoids `schemas/verdict.schema.json` and `carve-assets/W6/verdict.schema.v10.json` **except two**, both legitimate:
- `d30b313b` (B007) — touches only `carve-assets/W6/MANIFEST.md` and `expected/multi-target-r3-v10-template.json` (the one authorised post-cut W6 template replacement, verbatim real `assay run` output, not hand-typed — MANIFEST records it).
- `5b2730b6` (B051, per DA-R26) — a **second**, controller-ruled exception the dispatch prompt's summary didn't name: description-bytes-only edit to `judgment.r2.discarded`'s schema description, applied byte-identically to both `src/assay/schemas/verdict.schema.json` and the W6 frozen copy (diffed both files directly — identical single-line description change, no `type`/`enum`/`required`/bound touched), and the MANIFEST records it as "the one post-cut edit" with a `cmp`-verification note. Confirmed sound, not a defect — DA-R26 explicitly authorised exactly this.

**`LANE_SCHEMA_VERSION`** stays `2` (`config.py:141`, zero diff lines touching it in range) and **`inventory_schema`** stays `1` (`cli.py:1222`, zero diff lines touching it in range).

**Conformance tripwire.** `tests/test_verdict_conformance.py`'s `EXCLUDED_ENTIRELY` correctly carries both `("NO_MEASUREMENT", "PROVENANCE_UNVERIFIED")` (B004, no producer yet — DA-R28, out of range) and `("FAIL", "RED_FIRST_UNPROVEN")` (F015/R4, wire-only — DA-R21), each with a recorded obligation to remove the exclusion once a producer lands.

**verify.py independence**, read end to end:
- R2 (`_check_r2_rederivation`, `verify.py:2070-2213`): floor read FROM the document (`judgment.r2.fail_under`, absent → `100.0`), fed to the real `judge_mutation`/`mutation_pct` — a deliberate, already-ruled exception to "hand-transcribe everything" (DA-R22: "no second formula anywhere," reusing the SAME function the producer calls rather than risking a silently drifting copy); confirmed this is what's actually implemented, not just claimed.
- R3 (`_check_r3_rederivation`, `verify.py:2244-2369`): status and bookkeeping HAND-TRANSCRIBED (A-182), matches DA-R18/DA-R19/DA-D8 exactly — `all` doesn't short-circuit on FAIL, INCONCLUSIVE is terminal in both modes, first-decisive-wins (independently traced `[FAIL, INCONCLUSIVE]` under `all` → `FAIL`, matching generation 12's own disclosed push-point). Gap found: SF-1 above.
- R4 (`_check_r4_rederivation`, `verify.py:2390-2410`): `PASS` iff `before_outcome != PASS and after_outcome == PASS`, matches A-433 as amended by A-434/DA-R18 exactly, including that BOTH judged halves take `RED_FIRST_UNPROVEN` (never `COMMAND_FAILED`) — independently traced against A-434's reasoning, not just read.

**B069 (tripwire).** `tests/test_gate_harness_version_pins.py` — genuinely red-first (`test_the_scanner_reports_a_pre_cut_harness_as_stale` runs the same scanner over a fixture literally taken from `git show b2fd09f3^:gate/python/qualify_topos.py`), correctly scoped (ignores the lane-file `schema_version = 2`, prose, and `P25` non-`W<n>` dirs), and the live harnesses at tip are confirmed on `schema_version 10` / `W6` (`gate/python/qualify_topos.py:102,858`).

**B050.** `judgment.r2.fail_under` required-under-ingested/forbidden-under-native enforced identically in three places (config.py loader, `verdict.py`'s `_check_producer_fork`/`_INGESTED_ONLY_FIELDS`, and the JSON Schema's `if/then/else`); real-artifact acceptance witness (`test_a_declared_floor_the_real_report_MEETS_produces_a_verified_pass`, the real StrykerJS fixture, 21/109 killed = 19.27%, floor 19.0 → PASS, `verify_document(...) == []`) paired with a real control at the default 100.0 floor (still FAIL) proving B050 changed no outcome it wasn't asked to.

**B051.** `judgment.r2.discarded` declared-not-verified in all four places DA-R26 named (schema description ×2 in sync, DESIGN-GUIDE §11, CONSUMERS, `verify.py` docstring); the `9999`-on-a-real-109-mutant-document reproduction re-confirmed NOT refused, as ruled.

**B052.** Content-tier check (`mutation.py:_check_report_source_matches_commit`, reading through `SnapshotRepository.read_regular_file` inside the same block that materialises the baseline snapshot) matches DA-D5 exactly — CRLF/one-trailing-newline normalised, everything else byte-exact, a foreign/rewritten/untracked source refused naming the file and all three causes. Test coverage is real and non-vacuous: byte-identical control, stale (one appended line), REWRITTEN (whitespace-only reindent — the "most benign rewrite imaginable" still refused, exactly the point DA-D5 makes), CRLF pass, trailing-newline pass in both directions, a SECOND trailing newline refused (the bound, not just the fold), and an untracked path refused as the same code (not `GIT_FAILED`) — all driven through the real `main()` CLI over real git repos with the real committed StrykerJS artifact.

**B053.** `RefusalDetail`/`refusal_detail()` truncation is UTF-8-codepoint-safe (walks back off continuation bytes; verified the boundary math by hand), head-kept (opposite end from `_bounded_tail`, for the stated reason), `detail`/`detail_dropped_bytes` presence-iff-refused enforced in `Claim._check_detail`. Real end-to-end over-bound test drives 140 stray files through the installed CLI to produce a genuine >2048-byte `DIRTY_TREE` sentence, confirms the wire copy is bounded, the stream copy is whole, and `dropped_bytes` is exact. `announce_refusal`/`_report_probe_refusal` confirmed to print nothing when `diagnostics is None` (both guarded), addressing generation 11's own disclosed push-point.

**B007.** `CanaryConfig`/`JudgmentR3` exactly-one-of and aggregation-iff-plural invariants match at both the dataclass (`config.py:593-609`, `verdict.py:2601-2614`) and JSON-Schema (`if/then/else` on `targets.minItems: 2`) layers. `MAX_CANARY_TARGETS = 8` / `MIN = 1` (`verdict.py:445-446`) matches the measured bound recorded in DA-R17/A-432. `run_isolated_canaries` (`canary.py:643-780`) matches DA-R19/DA-R20/DA-D8: `any` short-circuits on first PASS, `all` runs on after a FAIL (2N bound, deliberate), INCONCLUSIVE terminal in both modes, budget exhaustion its own terminal keeping the payload. The B005 whole-target rule correctly generalised to every declared canary target (`config.py:1277-1297`), naming the offending one. The two branch-local verifier defects generation 12's own report disclosed (a FAIL-under-`all` wrongly treated as terminal; status-equality wrongly applied to a `BUDGET_EXCEEDED` payload) are fixed as reported — independently confirmed against the code, not just the report's word. `test_canary_multi_target.py`'s 532 lines include the negative cases that matter (`test_a_short_circuit_the_aggregation_cannot_explain_is_refused`, `test_a_payload_under_a_refusal_other_than_the_budget_is_refused`). The one gap this range's own new bookkeeping check misses is SF-1 above.

**Migration notes.** Read `docs/CONSUMERS.md` "Migration notes (v9 → v10)" as a consumer: it says what changed and what (if anything) to do, organised by what a lane declares rather than by backlog id, correctly states the no-op case (Python/Go lane, no R3/no ingested R2/no refusal reading → only the schema number changes). All four in-doc anchor referrers verified to resolve, including the two pre-existing dangling anchors DA-R31 records as fixed in the same commit — recomputed the GitHub slug by hand for both the `migration-notes-v9--v10` anchor and the retitled refusal-line heading's anchor and confirmed they match the actual heading text now in the file, not just that a link exists.

**Consumer-coupling facts.** `assay.diff.parse_added_lines`, `assay.git.*`, `assay.mutation.resolve_mutation_targets`, `assay.adapters.python.PythonAdapter` (cmru's imports) — untouched in range (grepped; no diff lines). No renames anywhere in the diff that would break the wave prompt's five binding coupling facts.

**decisions.md append-only.** `git diff 93188912..a4528144 -- assay/nyxloom-trove/decisions.md` has zero `-` lines outside diff headers — purely additive, as A-408 requires. No A-id or B-id collisions found (`A-441` is the max in range, matching "next A-442"; `B070` is the max backlog header, matching "next B071").

**Registered gate**, run myself, own worktree (`/workspaces/vbpub/.worktrees/assay-wave-d-v10-r2`, detached at `a4528144`), host rule followed (`docker ps`/`pgrep` checked clear before starting; `nice -n19 ionice -c3`; `docker update --cpus=3` applied to the launched container `loving_cannon` once it appeared): log `<scratchpad>/gate-r2-round1.log`, read directly, not piped — one `ASSAY_REGISTERED_GATE_COMPLETE=1`, zero `FAILED`/`DIRTY_TREE`/`Traceback`, every phase present through `pyflakes-clean` (`wheel-installed` → `attestation-hardened` → `verdict-v5-accepted` → `lane-schema-v2-successors-verified` → `verdict-v6-v7-v8-v9-hard-cut-verified` (25 frozen templates) → `verdict-v10-successors-verified` (79 passed) → `tester-unified: PASS (exit 0)` → `judge-provenance-bound-to-the-installed-wheel` → `self-hosted-lane-passed` → `topos-qualified` → `cmru-b006a-qualified` (7 passed) → `independent-self-hosting-passed` → `pyflakes-clean`), wheel `assay-4.1.1.dev42+ga4528144`, commit line `a45281444cdf1463149ceb642d5a44d736d2c667` — matches the judged tip exactly.

**Full local suite**, run once, serially, `nice -n19 ionice -c3`, over the worktree's own source (`PYTHONPATH=src`, ambient venv's pytest/jsonschema, no `-n`/xdist): log `<scratchpad>/local-suite-r2.log`, read directly, not piped — **`4131 passed, 20 skipped, 1 warning in 499.50s (0:08:19)`**. Matches generation 12's own independently-reported count on `fd489620` (`4131 passed / 20 skipped`) exactly, which is the expected corroboration since nothing between `fd489620` and `a4528144` touches a test.

## What I did NOT find (confirmed sound, not re-litigating)

DA-R19 (2N `all` bound), DA-R20 (per-attempt independence), DA-R21 (F015/R4 wire-only), DA-R26 (`discarded` declared-not-verified), DA-R28 (B004 out of range, tripwire armed), DA-R30 (`earlier_target_terminal` producer set) — all confirmed implemented as ruled, none re-opened.
