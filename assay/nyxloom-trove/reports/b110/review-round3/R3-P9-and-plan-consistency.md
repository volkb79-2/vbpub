<!-- Verbatim round-3 (final) pre-dispatch review output (fresh-session, read-only reviewer), 2026-09-28, against 210d6130. Dispositions: ../REVIEW-2026-09-28-round3.md -->

**Verdicts: P9 is READY-WITH-FIXES and the plan set is READY-WITH-FIXES.** Nothing is blocking. P9 has two new MAJOR gaps in how held audit records are cleared and recovered, plus one lower-likelihood path that skips the audit. Each needs a text fix, not a redesign. Everything else I found is a minor stale-text residual. I only read files (commit `210d6130`, under nice/ionice) and edited, ran and committed nothing. Line numbers are in `P9-distributed-evidence.md` (P9) or the plan unless named.

One precondition before dispatch: the carve log `reports/assay-B110-P9-CARVE-LOG.md` (P9:140, plan §11.6) does not exist yet.

## P9: round-2 status

| ID | Status | Evidence |
|---|---|---|
| P9R2-1 | RESOLVED | P9:151-160 matches P3b:287 exactly, and the order matches A-470's 1–8. P9:173, O21 and the BLOCKED trigger at :705 enforce it. |
| P9R2-2 | RESOLVED | Nonce drawn after every `SHA256SUMS` verifies (:47-55, OC11, step 9 :315-318). Injectable seam; recorded in the receipt (:352, :375); tested by O16. |
| P9R2-3 | **PARTIAL** | Held records never become root records (:131, :314, O18), and audit-check keys on the held directory (:329, :427). See P9R3-1 and P9R3-2. |
| P9R2-4 | RESOLVED | :254, `LC_ALL=C` recipe :257-265, O20. |
| P9R2-5 | RESOLVED | OC14 :134: lock once per process, the gate never locks, pre-run `BAD_LANE_CONFIG` with no verdict. I checked `reserve_verdict_output`: it creates no file, so "no verdict" holds. O15; forbid :682. |
| P9R2-6 | RESOLVED | OC16, check 7, O19. |
| P9R2-7 | RESOLVED (minor wrong reason) | O3 says "≥ 16 sites, so shard 1/2 has ≥ 8". Sharding is a hash mod N (`mutation.py:1500`), not an even split, so this is not guaranteed. The fix: state `len(S1) ≥ 8` as a fixture precondition. |
| P9R2-8 | RESOLVED | :9, OC8, :501, :650, forbid :683; plan :445 and :478. |
| P9R2-9 | RESOLVED | :94, :213-217, runner.py threading at :659. |
| P9R2-10 | RESOLVED | P7:439 already asserts through `glob("*.json")`; P9:513 is conditional. |
| P9R2-11 | PARTIAL (acceptable) | Most items are fixed. The B119 backlog boxes and the missing B118 dependency are left to Work step 9. |
| N-2 | RESOLVED | Plan §9.3.4 (:443-446) and §6 (:315-316) now match P9:413-424. |
| N-3 | RESOLVED | One stale word remains: plan D7 row :86 still says "a **deterministic** audit sample". |
| N-7 | RESOLVED | A-470's canonical list, plan :283, P9 :9 and :149. |
| N-8 | RESOLVED in the plan and P9 | A-471 still omits "a yes also requires a P6 change". |

## Plan, decisions, backlog, README: N-1..N-15

| N | Status | Note |
|---|---|---|
| 1 | RESOLVED | Graph :175-179, :527, P10 header. Stale leftover: P10:561 still says "after v14 **including P10b**". |
| 2, 3, 4, 5, 6, 9, 10, 12, 13, 14, 15 | RESOLVED | :45/:450 (N-4), :432 (N-5), :125/:542 (N-6), :535 (N-9), :174/:528 (N-10), report :53-54 and the new column (N-12), :388/:410-414 (N-13), A-467 and plan :106 (N-14), A-465 and D1 (N-15). |
| 7, 8 | RESOLVED | See the P9 table. |
| 11 | **PARTIAL** | P7's template (P7:743-748) still numbers the GO criteria 1–5 with "1 screen clean". So its "3 memory / 4 fixed overhead" disagree with plan §8.1's 1–4, with §8.2.2's "8.1.1–8.1.3", and with P8:392's "criterion 3" (fixed overhead). The template also still says "Stratum (file × operator)". |

**Global consistency checks**
- The §4 table, the graph and §11.6 agree, and the order is acyclic: P0 → P6 → P4/P7 → integration merged into v14 → P3b → P3d → v14 back to integration → P7b → pilot.
- P10b is off the pilot path (:175-176, :181, §7 :332).
- P9 never waits on P10c (:179, :527).
- The integration line merges into v14 before P3b, P3d and P10b (:169, :528).
- P5 against P3b is covered by :519.
- §11 is numbered 1–9, and every C1–C32 reference resolves.
- D7's default NO reads the same everywhere.
- "Qualified" includes gate exit 0 plus audit-check (:45, :450, A-471).
- The lane list (:530-535) and the identity list (A-470) are consistent.
- Minor mismatches:
  - The §4 table and the README omit P4 from P3b's dependencies (C30).
  - The README gives P10b's dependencies without P3d and P6.
  - The §4 row for P9 omits P7 and P7b, which P9:8 requires before the branch is cut.

## New findings

**P9R3-1, MAJOR (possible false PASS): "discard the source" is not an operation that exists.** At P9:339 (also P9:46, :496 and plan :324), "The disagreeing source must be discarded (re-import without it) and consolidation re-run."
- By then, that source's *unsampled* accepted records are already root records (step 11). Re-importing never removes them.
- Every later consolidation resumes those records. The held directory stays, because it is only moved on exit 0 (:340), so the audit fails forever.
- The only way out is to delete or move `.import-audit/<stem>/<LABEL>/` by hand. That skips the audit, and the dishonest records resume, giving a PASS.
- More generally, deleting a held directory skips the audit. Only receipt deletion is guarded (:242).

**Fix:** add `assay state discard-source --receipt-stem S --label L`, run under the lock:
- delete each root record in that receipt's `accepted` list for L whose on-disk sha256 equals `record_sha256`;
- remove `.import-audit/S/L/`;
- write a discard receipt.

Then make audit-check also require that, for every receipt `.assay/import-*.json` with a non-empty `audit_held`, each (stem, label) has held records, a pass record in `import-audit-done`, or a discard receipt. Otherwise it fails with `audit-evidence-missing`. Add oracles for both paths, and put the discard procedure in the runbook.

**P9R3-2, MAJOR: a consolidation that takes more than one invocation can never pass the audit.** Audit-check step 3 (P9:334, plan §6 :313, O18's last clause at :540) requires a `candidate` event in the **latest run segment**.
- The gate appends to one progress file (`.assay/progress-self-qualification.jsonl`, opened in append mode at `mutation.py:797`) and always runs with `--resume`.
- Multi-invocation runs are a designed path: a deadline cut, C3 unclassified attempts, the C28 OOM rule.
- Held IDs executed in invocation 1 get root records, so invocation 2 resumes them and emits no event. Audit-check then returns `audit-not-executed` on every later run.

**Fix:** accept a `candidate` event for the held ID from **any run segment that started after the receipt's `created_at`**, as long as its bucket equals both the root record's and the held record's. This is sound because, at import time, a held ID never has a root record at the store's judge (step 8 dedup). Keep the "planted root record with no event after import → `audit-not-executed`" negative. Add a two-invocation oracle, and update plan §6 :313 to match.

**P9R3-3, MAJOR (lower likelihood; audit bypass after a crash).** P9:274 says that after `journal-pending` "the operator inspects it; resume validation remains authoritative". That claim is false for honesty: resume checks identity, not outcomes.
- Step 11 (:320-323) does not fix whether root or held records are written first.
- After a crash, the obvious recovery is to delete the journal and re-run `b119-import`; the inbox is still in place, because it is moved only on exit 0 or 1.
- The root records written before the crash are then store duplicates. Step 9 excludes them from sampling, so they are never audited.

**Fix:** write all held records before any root record. Define the `journal-pending` recovery as a rollback: under the lock, delete every journaled destination whose bytes match the journaled sha256, then remove the journal. Never "delete the journal and re-import". Add an oracle with a crash injected mid-write.

**G3-1, MINOR (misleading stale text).**
- A-471 (decisions:995) says a locked store gives a "**whole-lane** `BAD_LANE_CONFIG`". C27 and OC14 say a pre-run refusal with no verdict; align A-471.
- A-471 and the plan's D7 row (:86) omit the P6 change a "yes" requires, and D7 still says "deterministic".
- P10:561: remove "including P10b".
- P7's template: renumber to §8.1 1–4, relabel "screen clean" as "§8.2.1, Qualifying GO only", and fix the stratum label.
- P3b's README item (P3b:733) lists identity inputs without the hook and coverage fingerprints.

## Verdicts
- **P9: READY-WITH-FIXES.** Apply P9R3-1, P9R3-2 and P9R3-3 (brief text and oracles), fix O3's precondition, and write the carve log before dispatch. The identity keys, nonce, lock, transfer grammar, D7-yes gating and ledger handling are correct.
- **Plan set: READY-WITH-FIXES.** The merge order is acyclic, and the qualification definition, lane list, identity list and D7 default are consistent. The remaining work is the minor residuals above, plus the §6 :313 wording that goes with P9R3-2.