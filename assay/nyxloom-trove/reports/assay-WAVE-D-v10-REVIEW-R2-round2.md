# Assay Wave D — Reviewer R-2, round 2

**Range:** `2128e464..0c6863ff` (my own round-1 tip through generation 13's completion) on `feature/assay-wave-d-v10`: `78a786fc` (round 1's SF-1 fix, already landed) plus the entire B004 carve (`d9fc22eb` + `0c6863ff`).

**Verdict: ACCEPT.** One BLOCKER found by adversarial testing and fixed directly on the shared branch during this review (commit `f0126b35`, mirroring round 1's SF-1 precedent rather than consuming a review round): `evaluate_provenance`'s documented "TOTAL, never raises" contract was false for a plausible, bounded (well under the 1 MiB `MAX_ADJUDICATION_BYTES` limit) pathologically-nested `image-provenance.json` document, which raised an uncaught `RecursionError` all the way out of `main()`. Fixed, red-first proven, and the fixed tip is itself gate-verified GREEN (see Gate verification). Two documentation findings reported below (SF-2, MINOR-1), neither fixed, neither blocking merge. Everything else in the range checked out clean, including independent verification of the import-cycle claim (not just reading the report's prose) and rulings on all three of generation 13's self-flagged push points.

This is the last review of Wave D. All eight post-cut items are shipped; this ACCEPT clears the path to `--no-ff` merge + `cmru release --project assay` (expected 5.0.0).

## Blockers (found and fixed during this review)

### BLOCKER-1 (fixed, `f0126b35`) — `evaluate_provenance` raises an uncaught `RecursionError` for a pathologically nested document well inside the accepted size bound

`src/assay/adjudication.py`'s module docstring and `decisions.md`'s A-442 row both state `evaluate_provenance` is TOTAL over `document_bytes` and never raises — "every malformed, unreadable, absent, or non-green shape renders a judged terminal." The `json.loads` call (then at `adjudication.py:154`) only caught `(json.JSONDecodeError, ValueError)`. `json.loads` on a deeply nested document raises `RecursionError` — on the current CPython (3.14.6) a real C-stack-depth check, not merely `sys.getrecursionlimit()` — which is a `RuntimeError` subclass, not caught by that clause.

**Reproduction** (measured, not asserted): a document consisting of `depth` `[` characters followed by `depth` `]` characters. Empirically measured boundary on this host: depth 47,404 is fine, depth 47,405 raises. Used depth 100,000 (200,000 bytes) for margin — **well inside `MAX_ADJUDICATION_BYTES` (1,048,576 bytes)**, the only size bound the loader (`load_adjudicated_evidence`) applies before a document ever reaches this function.

```python
sys.path.insert(0, "src")
from assay.adjudication import evaluate_provenance
doc = b"[" * 100_000 + b"]" * 100_000
evaluate_provenance(doc, "deadbeef" * 5)
# RecursionError: Stack overflow (used 8148 kB) while decoding a JSON array
# from a unicode string
```

Reproduced through the full public entry point too — `load_adjudicated_evidence` (the function `cli.py` actually calls), via a real `EvidenceDeclaration`, a real temp `adjudication_dir`, and the real 200,000-byte file on disk: same uncaught `RecursionError`, confirmed before writing the fix.

**Blast radius, read directly from `cli.py`, not assumed**: the ONE `try/except AssayError` around both loader calls (`cli.py` ~819-860) only catches `AssayError` and only re-raises on non-`LANE_TIMEOUT` reason codes — a `RecursionError` is neither, so it is NOT caught there. `main()`'s own top-level handler (`cli.py:294-326`) also only catches `AssayError`. So this propagates all the way out of `main()` as an unhandled traceback: **no `verdict.json` artifact is written at all**, which is exactly the "un-auditable refusal" class of failure P17 (cited in `cli.py`'s own comments) exists to prevent — a worse outcome than the judged `ERROR`/`UNREADABLE_ARTIFACT` terminal this same function already renders for the sibling case (invalid JSON that fails to parse for an ordinary reason).

**Confirmed untested before this review**: grepped every adjudication-related test file for `recursion`/`Recursion`/`nested`/`MAX_ADJUDICATION_BYTES` — zero hits. `MAX_ADJUDICATION_BYTES` is referenced only at its own definition and its one use site in the loader; nothing exercised the byte bound or any pathological-shape input against `evaluate_provenance` anywhere in the suite before this review.

**Fix** (`f0126b35`, `src/assay/adjudication.py`): add `RecursionError` to the existing `except (json.JSONDecodeError, ValueError)` clause around `json.loads` — same terminal (`ERROR`/`UNREADABLE_ARTIFACT`), same classification ("present but unreadable", row 5) already used for the sibling case.

**Red-first**: added `test_a_pathologically_deep_document_renders_unreadable_artifact_not_a_raise` to `tests/test_adjudication_provenance_parse.py`; confirmed it fails with `RecursionError` against the unfixed source (`pytest -q`, single test, isolated run), then applied the fix and confirmed the whole file (22 tests) and the broader B004-related suite (`test_adjudication_provenance_parse.py`, `test_adjudication_registry.py`, `test_adjudication_pipeline_integration.py`, `test_config_accept.py`, `test_config_reject.py`, `test_cli_run.py`, `test_verdict_conformance.py` — 313 tests) all pass.

**Why fixed directly rather than NOT ACCEPT + round 3**: matches this wave's own established precedent (round 1's SF-1, `78a786fc`, landed the same way) for a small, well-scoped, one-function, mechanically-obvious gap discovered by adversarial testing the module explicitly asked a reviewer to perform. The fix touches no schema, no wire shape, no cross-module contract — it strictly narrows the set of exceptions caught by an existing except clause into an existing, already-well-tested judged terminal.

## Should-fix / Minor (reported, not fixed — left for the controller)

### SF-2 — `CHANGES.md`'s `[Unreleased]` entry still describes `PROVENANCE_UNVERIFIED` as "RESERVED... rendered by their own producers later," which is now half-stale

`CHANGES.md`'s `[Unreleased]` section (hand-written, becomes 5.0.0's release notes per its own header comment) was NOT touched anywhere in this range (`git diff 2128e464..0c6863ff -- CHANGES.md` is empty). Its existing sentence — "Both are RESERVED at this cut and rendered by their own producers later" — was accurate when written (at the `!` cut, before B004 landed) but is now **false for `PROVENANCE_UNVERIFIED`**: B004's producer (`src/assay/adjudication.py`) landed in this SAME unreleased window. Independently confirmed via `tests/test_verdict_conformance.py`'s `EXCLUDED_ENTIRELY` set: `("NO_MEASUREMENT", "PROVENANCE_UNVERIFIED")` is gone (matches B004's claim of discharging the tripwire); `("FAIL", "RED_FIRST_UNPROVEN")` is still present (F015/R4 genuinely is still reserved, phase 3, out of this wave). So the sentence is now half-right, half-wrong, and a consumer reading the 5.0.0 changelog would not learn that `judge.evidence = [{source="adjudicated", key="image-provenance"}]` is now a real, usable lane declaration in this release.

Not a code defect, not release-blocking by itself, but worth a one-paragraph correction before the 5.0.0 cut. This estate has a standing, previously-filed pattern of `[Unreleased]`/hand-authored-section fold-in gaps around releases (recorded elsewhere as a cmru finding); worth checking whether that same reconciliation step would catch this, or whether it needs its own hand fix.

### MINOR-1 — two new doc links resolve to the right section but not the right subsection

Both introduced by `d9fc22eb` (confirmed via `git log -S` + `git blame`, not pre-existing): `README.md:562` and `docs/CONSUMERS.md:1470` both read `["Adjudicated image provenance"](#migration-notes-v9--v10)`. That anchor is real and resolves — but it's the anchor of the PARENT section (`## Migration notes (v9 → v10)`, line 1772), not the actual subsection the link text names (`### Adjudicated image provenance, live in this release (B004)`, line 1864, ~92 lines further down, whose own GitHub-slug anchor would be `#adjudicated-image-provenance-live-in-this-release-b004`). Not a broken/dangling link (round 1's anchor-resolution check would not have caught this, since it genuinely resolves) — a reader clicking it lands at the top of a large section and has to scroll to find the actual B004 content. Cosmetic; two one-line fixes if anyone wants them.

## The three self-flagged push points — rulings

**1. The `overall != "verified-match"` collapse (all five non-green states render the identical `NO_MEASUREMENT`/`PROVENANCE_UNVERIFIED` pair).**

**RULING: ACCEPT as shipped** — and better-grounded than the report's own framing suggests. Verified two structural constraints independently, not just the stated philosophy ("assay renders ciu's decision, not diagnosis"): (a) `reason_code` is a CLOSED JSON-Schema enum (`src/assay/schemas/verdict.schema.json` — confirmed by reading the actual schema, not assuming); adding five distinct ciu-specific reason codes would itself be a wire/schema change, and this generation's own explicit discipline (confirmed: zero diff lines touch `schemas/verdict.schema.json` or `carve-assets/W6/` in `d9fc22eb`) was NOT to make one. (b) `Evidence` (`verdict.py:3388-3405`) has no free-text `detail` field (unlike `Claim`, which gained one under B053) to carry the extra diagnosis even without a new reason code. So the collapse isn't merely a stylistic choice — the two obvious alternatives (new reason codes, or a detail string) were both out of this generation's authorized scope. The raw discriminating detail is NOT lost from the system — the source ciu document stays on disk at `judge.adjudication_dir/<key>.json`, retrievable out of band — only from the verdict artifact itself, a reasonable and bounded tradeoff given the wave's own consumer-coupling survey found no consumer parses a verdict yet. Worth a backlog note for a future generation if a real consumer ever wants `mismatch` distinguished from the other four non-green states (a genuinely security-relevant distinction an alerting consumer might care about) — not a defect in this one.

**2. `_validate_evidence_dir`'s generalisation scoped to `config.py` only (not shared with `attestation.py`).**

**RULING: ACCEPT as shipped — generation 13's reading is correct.** Read the actual governing comment myself (`config.py:111-118`), not the report's paraphrase: it names the MODULE boundary explicitly — *"config.py and attestation.py stay independent readers of the same closed grammar, and neither trusts the other to have already validated it"* — not a per-field boundary within a single module. Independently confirmed `attestation.py`'s own imports (`from . import git, safeio`; `from .errors import ...`; `from .verdict import ...`) include no `config` import at all — the real defense-in-depth property the comment protects (two INDEPENDENTLY-CODED validation layers: config.py's load-time check vs. each of attestation.py's/adjudication.py's own run-time check) stays fully intact. Sharing one function for two fields WITHIN config.py's own single load-time layer doesn't touch that property. The narrower reading (that the comment forbids sharing even within one module) is not supported by the comment's own text.

**3. `_COMMIT_RE`'s 8-hex-character lower bound, self-described as "my own choice, not measured."**

**RULING: ACCEPT as shipped, and better-grounded than self-described.** The SAME file's own comment two lines above (`adjudication.py:129-130`) states ciu emits `git rev-parse --short=8 HEAD` — an 8-character minimum is exactly and only what that documented invocation can ever produce, so the floor is directly implied by ciu's own measured practice, not an arbitrary guess as the generation's own self-assessment undersells it as. The actual verification that matters is the prefix match against the run's real resolved `HEAD` (`head.startswith(commit_under_test)`), so the exact minimum length doesn't materially move the security bar either way — 8 vs. some other small number would both be swamped by the real check.

## Import-cycle claim — independently verified against actual imports, not the report's prose

Read every relevant module's import statements directly:
- `vocabulary.py`: imports only `re`, `types.MappingProxyType`, `typing.Mapping` — genuinely a leaf, zero internal (`assay.*`) imports, confirmed by grep.
- `config.py`: imports FROM `.vocabulary` (module level, includes `ADJUDICATED_EVIDENCE_KEYS`) and, via two pre-existing FUNCTION-SCOPED (deferred) imports at `config.py:2822`/`2869`, FROM `.verdict` (`MAX_CANARY_TARGETS`/`MIN_CANARY_TARGETS`/`CANARY_AGGREGATIONS`) — with its own comment explaining why the import is deferred rather than module-level: `verdict.py` already imports `config.py` at module level, so a module-level `config → verdict` import would itself be a cycle. This is a genuine, PRE-EXISTING precedent (predates B004) for resolving exactly this class of cycle via a deferred import — noted below (INFORMATIONAL-2), not a defect.
- `adjudication.py`: imports FROM `.verdict` (`Evidence`, `EvidenceDeclaration`) at MODULE level.
- `verdict.py`: imports FROM `.config` at module level.

So `config → adjudication` at module level would indeed close `config → adjudication → verdict → config` — the claim is **TRUE**, independently confirmed, not merely read as asserted.

**INFORMATIONAL-2 (design note, not a defect):** given `config.py` already has a working, precedented pattern for a DEFERRED (function-scoped) import to resolve exactly this kind of cycle (used twice already, for `verdict.py`), an alternative to introducing `vocabulary.ADJUDICATED_EVIDENCE_KEYS` as a new shared leaf would have been a deferred `from .adjudication import ADJUDICATORS` inside the one loader function that needs it (`config.py`'s key-validation check, `config.py:2155`, itself already function-scoped). Both are legitimate; the `vocabulary.py` route is arguably cleaner — module-level, no deferred-import indirection, and it supports the drift-equality test pattern (`test_adjudication_registry.py`) DA-R1/A-406 already established elsewhere. Not asking for a change — noting the alternative existed since generation 13's own docstring called direct-import "not buildable" when a second real option existed in the same file.

**INFORMATIONAL-1 (backlog-worthy, out of range):** `attestation.py`'s `parse_attestation` (unchanged in this range) has the identical `except (json.JSONDecodeError, ValueError)` shape as `evaluate_provenance` had before this review's fix, and is therefore very likely vulnerable to the same uncaught-`RecursionError` class of input. Not fixed here (out of B004's diff, and this review's mandate is the range under test) — worth a backlog filing for a future generation.

## Mixed-lane merge order / atomic timeout claim — independently verified

Read `cli.py`'s dispatch code directly (~lines 780-905), not the report's summary:
- `attested_declared`/`adjudicated_declared` are filtered from `declared_evidence` by `.source`.
- ONE `try/except AssayError` wraps BOTH `attestation.load_attested_evidence` and `adjudication.load_adjudicated_evidence` calls — confirmed by reading the actual `try:` block boundaries, not assuming from the report's prose.
- On `LANE_TIMEOUT`, `_timed_out_evidence(declared_evidence, exc)` discards BOTH loaders' results uniformly, for every declared identity from both sources.
- The merge is `by_identity = {item.identity: item for item in (*attested_evidence, *adjudicated_evidence)}` then `evidence = tuple(by_identity[item.identity] for item in declared_evidence)` — genuinely order-preserving by the DECLARED sequence, not concatenation order.

Read the two purpose-built tests and confirmed neither is hollow:
- `test_an_interleaved_attested_adjudicated_lane_merges_evidence_in_declared_order`: declares `[attested:alpha, adjudicated:image-provenance, attested:omega]` and asserts the wire `evidence[]` array is in EXACTLY that order — a naive concatenation of the two loaders' own outputs would instead produce `[alpha, omega, image-provenance]`, so this test would genuinely fail against that bug.
- `test_an_adjudication_timeout_after_a_completed_attestation_pass_is_atomic`: the FIRST loader (attestation) genuinely resolves a real attestation file over a real git repo before the SECOND loader is monkeypatched to raise `LANE_TIMEOUT`; asserts BOTH declared identities render the same payload-free `BUDGET_EXCEEDED`/`LANE_TIMEOUT` pair and explicitly asserts `"producer" not in item` (the real, already-computed attested result must be discarded, not partially reported) and that the lane's own command never ran. This would genuinely fail against an implementation that kept the first loader's already-resolved result.

## Isolation and shared-branch checks

- `78a786fc` (round 1's SF-1 fix): confirmed intact — `verify.py:2352-2369` still carries the `budget_exhausted` third branch, unchanged shape.
- File-overlap: `git show --stat 78a786fc` touches only `verify.py`/`tests/test_canary_multi_target.py`; `git show --stat d9fc22eb` touches 17 files, none of them those two (`comm -12` on the sorted file lists is empty) — genuinely non-overlapping, as the controller log and REPORT both claim.
- `decisions.md` append-only across the WHOLE round-2 range (`2128e464..0c6863ff`, not just `d9fc22eb`): `git diff` shows zero real `-` lines (only the `---` diff-header line matches `^-`).
- `A-430` untouched byte-for-byte (still reads the original `name` typo); `A-442` (the B004 implementation row) and `A-443` (the correction row) both present, in order, as claimed.

## Schema/drift-guard touch discipline (re-confirmed for the round-2 range)

- Zero commits in `2128e464..0c6863ff` carry a `!` marker (only the pre-existing `b2fd09f3` from before round 1's range does).
- `d9fc22eb`'s file list touches no `schemas/verdict.schema.json`, no `carve-assets/W6/*` — confirmed by grepping its own `--stat` output.
- `LANE_SCHEMA_VERSION` still `2` (`config.py:151`); `inventory_schema` still driven by the unchanged `LANE_INVENTORY_SCHEMA_VERSION` constant.
- `nyxloom-trove/carve-assets/W2/` (the real ciu 6.0.3/7.10.1 captures) untouched in this range (`git diff --stat` empty) — the only green witness (A-334) is still the frozen, sha256-recorded real capture, not a synthetic double.

## Hollow-test scan

- `test_every_non_green_overall_value_renders_the_same_terminal`: parametrized over all five real non-green `overall` values (`mismatch`, `not-verified-dirty`, `not-verified-unknown`, `not-verified-no-evidence`, `refused-no-identity`) — not hollow, would catch a partial collapse.
- Interleave/atomicity tests above: both would catch a plausible wrong implementation (see detail above).
- `test_config_accept.py`/`test_config_reject.py` additions: round-trip through the REAL `load_lane_file` loader over REAL TOML text, not synthetic dataclass construction — the reject-side tests specifically target the per-source pairing rule and the unknown-adjudicator-key refusal reachable from a real loadable `assay.toml`, matching carve §3.2's own reachability requirement.
- The one real gap this review found (BLOCKER-1) is an ABSENT test, not a hollow one: nothing anywhere exercised `evaluate_provenance`'s totality claim against oversized/pathological input before this review — now closed.

## Gate verification

**Controller's own transcript on `d9fc22eb`** (trusted, not independently re-run at that exact commit, since a further change was needed on top of it anyway): `GATE_EXIT=0` once, `ASSAY_REGISTERED_GATE_COMPLETE=1` once, zero `FAILED|DIRTY_TREE|Traceback`, wheel `assay-4.1.1.dev45+gd9fc22eb`, all twelve phase markers, full local suite 4178 passed/20 skipped.

**My own fresh run, required** since I committed a real source change (`f0126b35`) after that transcript was captured — worktree `.worktrees/assay-wave-d-v10-r2b`, checked out at `f0126b35` (synced from the shared worktree after committing there). Host-load rule followed throughout: `docker ps`/`pgrep tester-unified-gate.sh` checked clear before starting (load average 3.4-5.6 across the run), the launched container (`youthful_pascal`) capped `--cpus=3` within 5 seconds of appearing, `nice -n19 ionice -c3` on the launcher. Log `gate-r2-round2.log`, read directly in a separate step from the run (never a pipe tail, per LESSONS L4).

Result, checked as separate greps rather than one combined pipeline: `ASSAY_REGISTERED_GATE_COMPLETE=1` — exactly once. `FAILED|DIRTY_TREE|Traceback` — zero matches, anywhere. All twelve `ASSAY_GATE_PHASE` markers present, in order (`wheel-installed` → `attestation-hardened` → `verdict-v5-accepted` → `lane-schema-v2-successors-verified` → `verdict-v6-v7-v8-v9-hard-cut-verified` → `verdict-v10-successors-verified` → `judge-provenance-bound-to-the-installed-wheel` → `self-hosted-lane-passed` → `topos-qualified` → `cmru-b006a-qualified` → `independent-self-hosting-passed` → `pyflakes-clean`). Wheel `assay-4.1.1.dev47+gf0126b35`, `tester-unified` phase's own printed `commit: f0126b35042eac522a591abf3b39cb67cbc939e5` — matches the fixed tip exactly. `docker ps` after completion shows no leftover gate container (the `--rm` container `youthful_pascal` is gone).

One difference from the precedent transcripts worth stating plainly: my direct invocation of `tools/tester-unified-gate.sh` did not itself print a literal `GATE_EXIT=` line — grepping for it returns zero matches. That string does not appear anywhere inside `tester-unified-gate.sh`'s own source (confirmed by grep); it is evidently emitted by whatever wrapper prior generations/reviewers used around their own invocation of the script, which I did not reproduce (I invoked the script directly under `nohup nice ionice`). I am relying instead on `ASSAY_REGISTERED_GATE_COMPLETE=1` (the script's own internal completion marker), the zero-failure-string grep, all twelve phase markers in order, and the clean container teardown — which together are the same substantive evidence the `GATE_EXIT=0` convention exists to summarize, just not that exact literal string.

## Summary

The most important finding: a real BLOCKER — `evaluate_provenance`'s documented, drift-tested-adjacent "TOTAL, never raises" contract was false for a plausible ~200KB adversarial/corrupted `image-provenance.json` document (well inside the 1 MiB `MAX_ADJUDICATION_BYTES` bound already enforced), an uncaught `RecursionError` that would crash the whole `assay run` invocation (no `verdict.json` at all) instead of the judged `ERROR`/`UNREADABLE_ARTIFACT` terminal the module exists to guarantee. Found by direct adversarial testing of the totality claim (as this review was specifically asked to do), fixed directly on the shared branch (`f0126b35`, red-first, mirroring round 1's SF-1 precedent) rather than consuming a review round, and the fix is now itself gate-verified GREEN on its own tip.

Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_01RJ3wqoyy8ZzHmj7ZK1qEnJ
