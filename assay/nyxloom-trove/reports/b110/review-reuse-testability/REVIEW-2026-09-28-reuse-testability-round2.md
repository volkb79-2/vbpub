# Round-2 verification: B110 reuse/testability docs, nyxloom testability doctrine, cmru KI-35

Verifier: fresh, independent session (read-only). Date 2026-09-28.
Every command ran under `nice -n 19 ionice -c3`. No edits, commits, gates, containers or test suites. The KI-35 tally was checked against the round-1 reviewer's saved `scratchpad/abandon-dryrun.txt`; nothing was re-run.

## Scope

| Doc | Worktree / commits | Files |
|---|---|---|
| D1 | `.worktrees/assay-b110-landing`, `84e4618f..29b791bd` | `assay/nyxloom-trove/reports/assay-B110-REUSE-AND-TESTABILITY-2026-09-28.md` |
| D2 | same | `assay-B110-PLAN-2026-09-28.md` §12 |
| D3 | same | `b110/README.md` |
| D3b | same | new `b110/research/scripts/r10/README.md` |
| D3c | same | saved review copy `b110/review-reuse-testability/REVIEW-2026-09-28-reuse-testability.md` |
| D5 | `.worktrees/docs-testability-cleanup-20260928`, `703505aa..851d1648` | `nyxloom/reference/TESTING-METHODOLOGY.md` |
| D6 | same | `cmru/KNOWN_ISSUES_TODO_BACKLOG.md` KI-35 |

Line numbers below are at `29b791bd` (D1–D3c) and `851d1648` (D5–D6). Both worktrees are clean at those commits.

## Mechanical checks (all passed)

- **Plan changed only in §12.** `git diff 84e4618f 29b791bd -- <plan>` is one hunk, `@@ -570,30 +570,30 @@`; §12 starts at line 556 (`## 12.`) and the file ends at 599.
- **Relative links.** Every `](…)` link in D1, D2, D3, D3b and `b110/research/README.md` resolves (scripted check). `../../R10-snapshot-structural.md` from `scripts/r10/` resolves.
- **Markdown tables.** Every table row in D1, the plan, D3, D3b, D5 and the cmru backlog has its header's column count (scripted check, backtick- and escape-aware).
- **Renames (finding 26).** In D1, D3 and D3b no `R1–R6`, `G1–G12` or bare `G3…G12` remain; `TG1–TG12` appear only in B2/B3 and plan §12, `RP1–RP6` only in plan §12 and D3. Every remaining `R`/`G` id checked by context:
  - rigor: R0–R3, `coverage R1` (TG8), `R1–R3 re-derivation` (TG7; matches verify.py stage 3 "INDEPENDENT re-derivation of R1/R2/R3"), `R1–R4` (C4; matches R11 §6);
  - research: `Research R2` (C3), R6 (TG5), R7, R9–R11;
  - P0's guard tests: `G1–G5` (B2 intro, S6), `P0 G2` (RC4, S3).
  Nothing was wrongly renamed. No stale ids anywhere else in `assay/` or `nyxloom/` outside the verbatim review copy.
- **Review copy.** `REVIEW-2026-09-28-reuse-testability.md` is byte-identical to the scratchpad original.
- **D5 structure.**
  - Lines 1–297 are byte-identical to the pre-change file.
  - Everything from `## Property-based testing and Hypothesis` (now line 469) onward is identical to the pre-change file from its old line 298. The only difference is the added DoD "Mutation-testable design" line.
  - `grep -n '^#'` shows the original heading sequence, with `## Designing for mutation testability` (298) and its three `###` subsections inserted before it.
  - Every backticked cross-reference resolves: `## Scope, rigor, and lanes`, `## Mutation testing`, `## Designing for mutation testability`, `## Property-based testing and Hypothesis` and `## Do tests test the right thing?`. So do "Coverage exclusions: prefer deletion" and LESSONS L20 (`LESSONS.md:693`).
- **KI-35 counts.**
  - The tally is 9 + 5 + 3 + 1 + 1 = **19**. It matches the dry run's reasons exactly (9 snapshot-unavailable, 5 scope-metadata, 3 published, 1 progress-metadata, 1 untagged-publisher).
  - 5 + 19 = 24, and 19 `cmru-release-*` worktrees exist now.
  - `20260917_043157-all-d5847937`: `git ls-files` = 0 against 4,644 paths in HEAD.
  - `cmru-v5.5.0` is the latest cmru tag; `c54e9058` is not its ancestor; the tagged `cli.py` has no `def _abandon`; `CHANGES.md` `[Unreleased]` does not mention abandon.

## 1. The 39 round-1 findings

| # | Status | Reason |
|---|---|---|
| 1 | RESOLVED | `## Property-based testing and Hypothesis` is restored at D5:469, directly before "The "Property/state-machine testing" catalogue row…". The DoD cross-reference (D5:684) resolves, and the Hypothesis block is back under its own `##`. |
| 2 | RESOLVED | D1:91: "None is built today: P11's T4 probe describes one, and the pilot does not build it. This is new work…". D1:97 and RP1 add "a coverage-context run in the P7b screen mode". Checked against P11:54/102/211; no other brief has `--cov-context`. |
| 3 | RESOLVED (minor residue, N5) | D1:98 and RP2 now follow the dependency graph, and "P5, P6 and v14 are required for correctness" is gone. Residue: D1's "The pilot still needs P7, P7b and P8" is incomplete, because §7/§8.1.4 also require P5 and all of P3a–P3d. |
| 4 | RESOLVED | S6 (D1:389) is conditioned on S4 and names §1, §8.1.4, §2 item 3, §4, §7, D8/A-472 and B116. D1:350 reads "**Proposal (S6): … if the shallow snapshot (S4) is adopted.**" The C5 cell says "P5 deferrable (S6)", and the D2 S6 bullet matches. |
| 5 | RESOLVED | The RC4 cell (D1:284) names A-472 ("no residue between siblings"), A-161, P0 G2 and A-470's `assay-r2-pytest-nocov/1` bump, plus the mtime/size and `co_filename` caveats. S3 Needs (D1:386) is the round-1 text verbatim. The quotes were checked in `decisions.md` (A-470 line 970, A-472 line 1009). |
| 6 | RESOLVED | S2 Needs: "S1 first (or in the same change); A-468 amendment; P1 brief + fresh pre-dispatch review". The D1:395 and D2:597 order sentence now starts "S1 with S2, …" and is marked "(proposal; …)". |
| 7 | RESOLVED (nit) | The TG7 evidence cell is the round-1 text. "R1–R3 re-derivation" was checked against verify.py's stage-3 docstring. Nit: TG7's own rule text still says "State each boundary condition once." D5 now says "once per trust side"; align them. |
| 8 | RESOLVED | RC1/RC2: "It also narrows a false-kill channel. R9 counts 82 … The moved files hold only a minority, so the rest still need their time dependence removed (nyxloom LESSONS L20)." |
| 9 | RESOLVED (nit) | D5:309-311: "The largest cause was a tool fact: the runner had no early exit…". Nit: "The other measured causes were design facts" still implicitly covers two tool facts (the 60 s planner constant, jobs=1 with joined waves), and the full-history snapshot was partly a lane opt-in. "The other causes listed here…" would be exact. |
| 10 | RESOLVED (nit) | D5:363-368 is the round-1 text. Nit: "early exit … **was** by far the largest lever" describes a projection; P3b is not built (≈520 s → ≈10 s is [C]). Prefer "is projected to be". |
| 11 | RESOLVED | D5 guideline 7 is the round-1 text ("once per trust side … count it, don't remove it"). It no longer contradicts D5:450-452 or A-182. |
| 12 | RESOLVED, but see N2 | D5:318-326 uses full paths for D1, the analysis and R9/R10/R11, with "(Parts B and C)". The :436 fix is applied (D5:453-455). The added parenthetical is itself a new defect (N2). |
| 13 | RESOLVED | DoD (D5:674-679): "…not the per-mutant suite, and "done" then requires a same-commit pass of that release lane". |
| 14 | RESOLVED | KI-35 now reads 24 retained at cleanup time: 5 abandonable (abandoned), 19 withheld. This matches the controller's adjudication and the dry-run file, and the commit message records the "18 of 23" miscount. |
| 15 | RESOLVED | D1:31: "The preflight used 0.89 of its 3 CPUs on average [M] (a 0.83 reading is unverified)." |
| 16 | RESOLVED | D1:131 reads "3.3–4.0 h". 9.4×3760/3/3600 = 3.27 and 11.4×… = 3.97. |
| 17 | RESOLVED | C5 (D1:376): "S ≈ 0.5 s … + C ≈ 2.9–4.9 s … + O ≈ 0.5 s → ≈4–6 s". D1:380 adds "if S3, S4 and S5 are all adopted". D2:585 matches. |
| 18 | RESOLVED | D1:355 adds "(at ≈12.5 s per kill, R11 §5.1; less if C5's scenario holds)". |
| 19 | RESOLVED | D1:350 gives R2 0.25–0.85 s (C1) and P5 brief 0.6–1.4 s (C1+C2), checked at `R2-snapshot-internals.md:226` and `P5-snapshot-costs.md:24`. |
| 20 | RESOLVED | The row is relabeled "Shallow + refresh, `assay/`-only worktree (≈0.03 s more without the refresh, R10 §4)". R10:300 gives ≈0.03 s [C]. |
| 21 | RESOLVED | The D1:331 eatmydata cell is scoped to the snapshot path, test-side ≤0.16 s, and pip/venv untraced. D2:585 reads "saves nothing there". |
| 22 | PARTIAL (N3) | The caveat was appended to D1:245 and RP6. But D1:245's first sentence still says "It turns ≈130 MB of copies per candidate into metadata operations", which the appended sentence now contradicts. Per R10 §3.3 the worktree (≈81 MB) is written from `cat-file`, so only the ≈41–62 MB pack is a copy. |
| 23 | RESOLVED in D1 / NOT carried to D2 (N4) | D1:273: "≈5.5 CPU-s per collection, measured in the devcontainer [M]; ≈4–5 s of the gate's 7.4 s [I, needs the S3 probe]". Plan:582 still says "≈5.5 CPU-s per candidate is assertion rewriting". |
| 24 | RESOLVED with a new error (N1) | Items :359 and :361 are correct (6–8 of 12, verify 5; the `test_self_lane.py:128-135` targets pin and `b105_report_check.py:14-104` bindings were checked). But :358 now places the rule in "`verify.py`'s `_raw_claim`", and **no `_raw_claim` exists in verify.py**. R11 §6 *proposes* it; the rule lives in `_check_judgment_matches_claims` (verify.py:658-826). |
| 25 | RESOLVED (nit) | Now "(analysis §8; A-470's prefix manifest)". Nit: A-470's text (decisions.md:970) never says "manifest". Its binding is the collection proof (`collection_sha256`), so "A-470's collection proof" is more exact. |
| 26 | RESOLVED (minor traceability, N7) | The renames are complete and correct (see the mechanical checks). But D1 never uses RP ids: RP1–RP4 = A4 items 1–4, RP5 = B4 product item 1, RP6 = B4 codebase items 1–2. D3 says D1 contains "proposals RP1–RP6", which a reader cannot find there. |
| 27 | RESOLVED | The D3:11 row reads "(non-binding proposals RP1–RP6 and S1–S10, guidelines TG1–TG12; Part C summarizes research R9–R11)". |
| 28 | RESOLVED (nit) | S8 names "a state-record or sidecar field outside the v14 wire", or an A-470 amendment. State records tolerate extra keys (`P0` brief :49), so the option is sound. Nit: "all six keys" is a plan §3 D6 addendum (plan:116) and a P3a pin, not A-470 text. Cite "plan §3 D6 addenda / P3a", not only A-470. |
| 29 | RESOLVED in D1 / NOT carried to D2 (N4) | D1:310 reads "**Full history: no** [I, pending S4's drift-proof preflight]", and RC6 now says "move … to the release lane; without S1 this narrows R0". Plan:583 still states "the snapshot copies full history that nothing needs after A-468(a)" as fact. |
| 30 | RESOLVED (nit, N8) | The new `scripts/r10/README.md` records the strace flags (matching R10:193). It explains why zero-byte files are consistent with 0 fsyncs, admits that exit statuses were not retained, and marks `sanity2.txt` unexplained. That is honest and as complete as the evidence allows. Nit: see N8. |
| 31 | RESOLVED | The D5:357 row is the round-1 text ("bind it by content digest…, check that no test depends on code-object paths… Measure first"). |
| 32 | RESOLVED | D5:373-375: "…and fix the time dependence itself (LESSONS L20); moving the test only contains it." |
| 33 | RESOLVED | All three are applied: D5:456-459 (fsync scope, clone ≈27 ms/fsync), D5:463-465 ("was measured no cheaper on this host"), D5:466-467 ("the largest measured … in assay's case"). |
| 34 | RESOLVED (cosmetic) | D5:442-446 has "would remove an estimated 10.5–14% … (static estimate; not done)" and "the library's pinned content (e.g. its git subtree OID) and its own verified qualification verdict". Cosmetic: D5:443 is an over-long unwrapped line. |
| 35 | RESOLVED | The formula uses "passing run". Guideline 9 adds "in a separate, non-gating job with a recorded seed; the gate lane itself stays deterministic". |
| 36 | RESOLVED | The fifth reason (`20260917_041022-all-891e9fce`) is added, with a numeric tally. |
| 37 | RESOLVED | The empty-index worktree is in Observed and in Wanted #2. |
| 38 | RESOLVED | "No released cmru (latest `cmru-v5.5.0`) contains `cmru abandon`: KI-29 is shipped in source only, and `CHANGES.md` `[Unreleased]` does not list it." Verified. |
| 39 | RESOLVED | D2:597 is the finding-6 text, marked "(proposal; each needs the decision listed above)". |

**Tally:** 36 RESOLVED (several with nits), 1 PARTIAL (22), 2 resolved in D1 but not carried into plan §12 (23, 29). None NOT RESOLVED.

## 2. New defects introduced by the fixes

**N1 [minor, factual]. D1:358 cites a function that does not exist.**
- **Text.** "written four times in `verdict.py:4769-4910` and `verify.py`'s `_raw_claim`".
- **Evidence.** `grep _raw_claim src/assay/verify.py` finds nothing (only `_raw_mutant_identity` exists). R11 §6 lists `_raw_claim(claims, rigor)` as a *proposed* helper; the verify-side twin is `_check_judgment_matches_claims` (verify.py:658-826, R11 row 36).
- **Fix:**
  > the R1–R4 "judgment present iff attempted" rule, written four times in `verdict.py:4769-4910` and again in `verify.py`'s `_check_judgment_matches_claims` (≈20 + ≈15 removable through one per-rigor helper per side, plus a proposed verify-side `_raw_claim`; R11 §6);

**N2 [minor, required before merge]. D5:325-326 embeds a merge instruction in canonical doctrine that becomes false once merged.**
- **Text.** "(These files exist only on the `assay-b110-landing` branch: merge this change only after it.)"
- **Evidence.** This is true today (`git cat-file -e` finds all five files on `29b791bd` and none on `main` `87c13eff`). After the required merge order it is false.
- **Fix.** Delete the parenthetical; the merge-order constraint is already in commit `851d1648`'s message. Enforce it in the merge plan: merge `assay-b110-landing` first.

**N3 [minor]. D1:245 (B4 codebase item 2) contradicts itself** (finding 22 PARTIAL).
- **Fix.** Replace the first sentence:
  > **A reflink-capable `TMPDIR` for tester-unified.** It would turn the ≈41–62 MB pack copy per candidate into a metadata operation; the worktree is written from `cat-file`, not copied, so it benefits only if the optional C4 template copy is built.

  Keep the rest of the sentence ("A-184 already allows it; … it needs a new LV on the production host (no loop file); pursue only if the pilot shows I/O matters (Part C, C3)").

**N4 [minor]. Plan §12 still carries two claims that D1 corrected** (findings 23 and 29 were not propagated). This creates a D1 ↔ §12 inconsistency.
- **Fix, plan:582:**
  > ≈5.5 CPU-s per collection (devcontainer; an estimated ≈4–5 s of the gate's 7.4 s, unprobed) is assertion rewriting of unchanged test modules;
- **Fix, plan:583:**
  > the snapshot copies full history that no collected test appears to need after A-468(a) (static reading, pending S4's drift-proof preflight).

**N5 [minor]. D1:98 (A4 item 2): the "pilot still needs" list is incomplete.**
- **Evidence.** Plan §7 and §8.1.4 require P0–P8 (so P5), the *whole* v14 branch P3a–P3d, and P7b. D1 names only "P7, P7b and P8", while RP2 correctly says "the pilot-readiness set (§8.1.4) is unchanged".
- **Fix:**
  > The pilot-readiness set is unchanged (plan §8.1.4: P0–P8, the whole v14 branch P3a–P3d, and P7b); P5 leaves it only if S6 is decided.

**N6 [minor]. D1:55 (A2 table, P5 row) contradicts D1:98.**
- **What.** The row says P5 is "Needed to keep per-candidate isolation". Item 2 and S6 now call P5 a cost package that may be deferred. Isolation comes from the existing fresh-private-repo design; P5 (C1/C2) only makes it cheaper.
- **Fix (Verdict cell):**
  > The fresh-repo design is needed for per-candidate isolation (the stale-pyc false-kill hazard was reproduced); P5 only makes it cheaper and may be deferred (S6)

**N7 [minor, traceability]. D3 is partly stale and has gaps.**
- **(a) No RP mapping.** D3:11 says D1 holds "proposals RP1–RP6", but D1 has no RP ids.
  - **Fix.** Add one line under the plan §12 table: "RP1–RP4 = the sibling's A4 items 1–4; RP5 = B4 product item 1; RP6 = B4 codebase items 1–2." Or reword D3:11 to "(non-binding; plan §12 indexes them as RP1–RP6 …)".
- **(b) Stale range.** D3:7 still says "Verbatim research records R0–R8"; the directory holds R0–R11. This predates the fix, but the new row now points readers there.
  - **Fix:** "R0–R11".
- **(c) Review not indexed.** The new review copy is missing from the index.
  - **Fix.** Append to the D3:11 row: "Its review: [`review-reuse-testability/`](review-reuse-testability/)."

**N8 [nit]. D3b:3 misattributes the traces.**
- **What.** It says every file in `strace-outputs/` comes from "runs made by the R10 probe (`probe.py`)". But `probe.py` never invokes strace. The `strace-cand-*` files are strace-wrapped `probe.py` variants (`full`/`nofsync`/`refresh`); `strace-clone-full.txt` traces a direct `git clone --no-local --bare`; `sanity*.txt` trace a Python script.
- **Fix.** Say so, and give the traced argv shape: `probe.py <seed> <commit> <scratch> <variant> <reps>`.

**Nits (optional):**
- KI-35:1350 lists 2 of the 5 scope-metadata worktrees without "for example" (the next bullet uses "for example").
- KI-35 Wanted #2's continuation lines (1382-1383) are unindented. CommonMark renders them as a lazy continuation, but they are inconsistent with items 1 and 3.
- RP6 "(Part C, C3)" inside the plan should read "(sibling Part C, C3)".

## 3. Still overclaimed, or stated as decided when it is only a proposal

- **O1 [minor]. D1:260 (Part C headline): "none of them needs a new runner feature".**
  - **Evidence.** RC4 carry-in is snapshot/runner code (or an A-470 transform bump); S5 needs a new `snapshot_selection` value ("needs code", R10 table row 252); S8 is a P0/P3b extension.
  - **Fix:** "most of them need no new runner feature; RC4, S5 and S8 need small product changes plus the decisions listed in C5".
- **O2 [nit]. D1:355 (C4): "with **no claim change and no new decision**".** S9 makes the same work a "new small package", and D1:395 says "each needs the decision listed above".
  - **Fix:** "no claim change and no new claim decision".
  - **Also.** D1:355 and plan:585 still say "removes" in the present tense for a static estimate. D5 now says "would remove an estimated … (static estimate; not done)"; align them.
- **O3 [nit]. Plan:585, "P5 is deferrable (S6)".** The sentence is unconditioned; S6 conditions it on S4. **Fix:** "P5 becomes deferrable if S4 is adopted (S6)".
- **O4 [nit]. D1:390 (S7), "RC5/RC6/RC8 now".** RC6's lane move narrows R0 without S1, and S7 reopens the P1 brief after the round-3 cap, just as S2 does.
  - **Fix (Needs cell):** "P1 scope; S1 before RC6's lane move; fresh pre-dispatch review of the P1 brief".
- **O5 [nit]. D5:364, "early exit … was by far the largest lever".** This is a projection; see finding 10's nit.

Nothing else in D1, D2 or D5 reads as ratified. D1:5 and D2:558 both say nothing is binding, and every S/RP row names its needed decision. KI-35 claims nothing beyond what was verified.

## Verdicts

| File | Verdict |
|---|---|
| D1 `assay-B110-REUSE-AND-TESTABILITY-2026-09-28.md` | **READY-WITH-FIXES.** Apply N1 (D1:358, non-existent `_raw_claim`), N3 (D1:245 first sentence), N5 (D1:98 pilot-readiness sentence) and N6 (D1:55 P5 verdict cell), using the exact texts above. O1 is recommended; O2, O4 and the nits on findings 7 and 25 are optional. |
| D2 plan §12 | **READY-WITH-FIXES.** Apply N4 (plan:582 and plan:583) and the N7(a) RP mapping line. O3 and the RP6 "sibling" nit are optional. The diff is confined to §12. |
| D3 `b110/README.md` | **READY-WITH-FIXES.** Apply N7(b) ("R0–R8" → "R0–R11"). N7(c) (index the review copy) is recommended, and N7(a) if it is done here rather than in §12. |
| D3b `b110/research/scripts/r10/README.md` | **READY.** N8 is an optional nit. |
| D3c saved review copy | **READY.** Byte-identical to the round-1 original. |
| D5 `nyxloom/reference/TESTING-METHODOLOGY.md` | **READY-WITH-FIXES.** Apply N2: delete the D5:325-326 parenthetical "(These files exist only on the `assay-b110-landing` branch: merge this change only after it.)". Merge only after `assay-b110-landing`, which is not yet on `main`. The nits on findings 9 and 10 and the D5:443 rewrap are optional. The heading structure and every cross-reference are correct. |
| D6 `cmru/KNOWN_ISSUES_TODO_BACKLOG.md` KI-35 | **READY.** Counts are internally consistent (9 + 5 + 3 + 1 + 1 = 19; 5 + 19 = 24) and match the dry run. Every claim was verified. The two formatting nits are optional. |

No blocking or major item remains.
