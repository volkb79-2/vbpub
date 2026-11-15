<!-- origin: 2026-11-08; status: design; copied from the controller program dir 2026-11-10 -->
<!-- source: reports/changes-md-friction-ANALYSIS.md -->
<!-- relation: CHANGES.md release-friction analysis -->

# CHANGES.md friction: what it is, why pwmcp is blocked, what to change

Date: 2026-11-08. Analyst: Opus, read-only. The only process run was one `cmru release pwmcp --dry-run`, from a
clean detached worktree of origin/main `ac7b97e9a`, using cmru 7.2.2. The worktree was removed afterwards.

## Short answer

- **pwmcp is not blocked by its CHANGES.md.** Its committed release version is still `1.64.1-r5`, and that
  version is already tagged and released (`pwmcp-v1.64.1-r5`). Nobody made the usual "release coordinate"
  commit that moves it to `-r6`. cmru does not check whether the version is already tagged before it writes the
  changelog. Its changelog code therefore treats the old r5 section as a release that needs refreshing, and
  then miscounts the section's own generated lines as "32 hand edits". The real fix is a 5-line version-bump
  commit. **Do not edit CHANGES.md, and do not follow the "Remedy" line in the error message.**
- **The recurring trouble is real but mostly fixed already.** The largest past cause was notes written in the
  wrong place: KI-23, KI-30 and KI-58, with 6 recurrences on ciu alone. cmru 6.2 (KI-78) removed that cause, and
  it has not recurred since 2026-10-14. What remains is many small papercuts, and each one is found only at
  release time.
- **Recommendation:** do not redesign CHANGES.md. Make five small fixes (S each) and one merge-time check (M),
  in the order given in section 5.

## 1. The three kinds of text in a CHANGES.md

A cmru-managed CHANGES.md is split by one line, `<!-- cmru: release history -->` (the "marker"):

```
# Changelog                                  <- free text
## [Unreleased]                              <- (1) pending notes, ABOVE the marker
- what the next release changes, migration notes ...
<!-- cmru: release history -->               <- the marker
## [1.64.1-r5] - 2026-11-07                  <- (2) generated release history, BELOW the marker
<!-- cmru: generated -->
<!-- cmru: source-end=<40-hex commit> -->
### Fixed
- fix(pwmcp): ... (abc1234)                  <- one line per commit subject
<!-- cmru: pending release notes begin -->   <- (1) after release: the notes, moved here by cmru
...
<!-- cmru: pending release notes end -->
## [1.64.1-r4] - ...
```

| Kind | Where | Who writes it | What cmru does with it |
|---|---|---|---|
| **(1) Pending notes** | under `## [Unreleased]`, above the marker | humans and agents, inside their feature commits | At release time, cmru moves the notes into the new version section, between the `pending release notes` markers. That section becomes the body of the GitHub Release. On a failed release or a dry run, the source branch is left untouched (KI-78, cmru 6.2.0). |
| **(2) Generated release history** | dated `## [X] - YYYY-MM-DD` sections marked `cmru: generated` | **cmru only**. It writes one line per commit subject in the project's path range since the previous tag (`changelog.py:_subject_groups`), plus a `source-end` cursor recording which commit the section was generated up to. | It never overwrites a section without the marker. It regenerates a generated section only when it is releasing that same version again and newer project commits exist (REL-02). Before regenerating, it refuses if the section holds lines its own generator would not produce (KI-68, "hand edits"). |
| **(3) Standing hand-written text** | anything outside (1) and (2): intro text, cli-extended's `## Contract and upgrade notes`, mdt-cockpit's "Detailed notes ... (hand-written)", ciu's 6 pre-cmru dated sections, cmru's HTML comment block under [Unreleased] | humans | cmru ignores it and never publishes it. HTML comments under [Unreleased] stay in place on every release. |

Other readers of CHANGES.md: cmru's `Bootstrap Run-Gate version` release precondition reads
`run-gate-project/CHANGES.md`. assay has its own "CHANGES schema-note check" (this caused the 12.1.0 gate
failure fixed in `01c11a32e`). Several projects' `tests/test_docs.py` also read it.

What the estate has today (21 CHANGES.md files on origin/main):
- 18 follow the layout above.
- **iogovmon** and **libraries/test-policy** are cmru-managed but have **no marker**. Their first release will
  fail exactly as augeaswrap's did.
- **cli-extended** has its `## [Unreleased]` buried in the middle of the history (line 230, between 0.5.1 and
  0.5.0). It still works, but it reads wrongly.
- **cmru/CHANGES.md** has a stray **second** marker at line 285. It is harmless today because cmru uses the
  first one.
- debian-install-v2 is not cmru-managed.

## 2. Why the pwmcp dry-run fails

Reproduced: exit 4, with the same message as in the handoff (16:25Z run, cmru 7.2.2).

**Chain of events:**
1. pwmcp's version is not computed by cmru. Its `prepare` step runs
   `scripts/resolve-playwright-version.py --check`, which reads the **committed** coordinate:
   `ciu.defaults.toml.j2` `tag`, `docker-bake.hcl` `PWMCP_VERSION` default, `pwmcp.contract.json` `release`,
   and `client/.../VERSION`. On origin/main these still say `1.64.1-r5`.
2. Each earlier release had a separate "release coordinate" commit that moved this value forward:
   `c39a762fb` (r3), `3f9c71bf9` + `f1789927b` (r4). For r5 the bump was carried inside the feature commit
   `74bd2c68e`. **No commit has moved it to r6.** The tag `pwmcp-v1.64.1-r5` already exists, on `8f795771f`.
3. cmru plans version `1.64.1-r5`. It does **not** check whether that tag already exists before generating
   the changelog (`cli.py:_prepare_release_projects` → `changelog.generate_release_changelog`).
4. CHANGES.md already has a generated `[1.64.1-r5]` section, and 15 pwmcp commits have landed after its
   `source-end` (`b5808b12`). The REL-02 logic was written for a resumed, half-finished release. Here it
   decides "this section is stale, regenerate it".
5. Before regenerating, the KI-68 guard (`_dropped_hand_edits`) renders what the generator would have produced
   for the old range `previous_tag..source-end`. Because r5 is already tagged, `previous_tag` is **r5 itself**,
   and `b5808b12` is an ancestor of that tag. The range is empty, so the generator "would have produced"
   nothing.
6. As a result, **every** body line of the r5 section counts as a "hand edit". There are exactly 32 of them:
   4 `###` group headings and 28 commit-subject bullets (Added 1, Fixed 7, Documentation 1, Testing 19). The
   folded notes block is excluded from the count.

**So the 32 lines are cmru's own generated lines, not hand edits.** No commit of ours put hand edits into r5.
The notes the pwmcp-4344 work wrote under `[Unreleased]` (`7e6b3ba83`, `006503551`, `2bd776752`) are
correctly placed and are not involved.

One cosmetic oddity in the file: those notes sit between an empty `pending release notes begin/end` marker
pair under `[Unreleased]`. That pair is left behind by cmru itself, because the fold step keeps every HTML
comment found under [Unreleased]. cmru/CHANGES.md shows the same leftover. It is harmless: the notes fold
correctly.

**Two cmru defects** (neither is in the backlog; the next free id is KI-130):
- **KI-130:** a planned version whose tag already exists should be refused up front, with a plain message:
  "pwmcp-v1.64.1-r5 already exists; the version input was not advanced". Today the user gets a misleading
  changelog error instead.
- **KI-131:** `_dropped_hand_edits` uses the wrong base range when the section's version is already tagged.
  Its printed **Remedy** ("advance `source-end` ... ") is also **harmful** in this situation. Following it would
  make the released r5 section claim 15 commits it never shipped, and the release would then fail on the
  existing tag anyway.

## 3. The fix for pwmcp today

Order of steps (CHANGES.md is not touched):

1. In a fresh worktree off origin/main, make **one commit**, `chore(pwmcp): release coordinate 1.64.1-r6`,
   with 5 line edits in 4 files. This is the same shape as `c39a762fb`.
   - `pwmcp/ciu.defaults.toml.j2:146` `tag = "1.64.1-r6"`
   - `pwmcp/ciu.defaults.toml.j2:177` `tag = "1.64.1-r6"` (the OCR image; must equal the line above)
   - `pwmcp/docker-bake.hcl:47` `default = "1.64.1-r6"`
   - `pwmcp/pwmcp.contract.json:3` `"release": "1.64.1-r6",`
   - `pwmcp/client/src/pwmcp_client/VERSION` → `1.64.1.post6`

   *Alternative:* `python3 scripts/resolve-playwright-version.py --refresh` (needs network). It may choose a
   newer Playwright, which would be a dependency upgrade with an image change. Use it only if that is wanted,
   and apply the estate's 14-day freshness rule.
2. In that worktree, run `python3 scripts/resolve-playwright-version.py --check`. It is offline and takes under
   a second, and it refuses if the 5 values disagree.
3. Review: under the standing rule, every merged change gets an independent review, scaled to its size. A
   single Sonnet read of the 5-line diff plus the `--check` output is enough. **No CHANGES.md review round is
   needed.**
4. Merge to main. Then re-run the dry run from a clean worktree of origin/main. The expected result is a new
   `[1.64.1-r6]` section listing the 15 commits since r5, with the [Unreleased] notes folded into it. I checked
   this in memory with cmru's own `fold_unreleased_notes` and `extract_release_section`: the fold succeeds and
   the r6 section is 49 lines.
5. Then run the real release through the usual release-when-quiet path.

The README example lines that mention `1.64.1-r5` (README.md:237-307) are examples. Earlier coordinate commits
did not update them, so they are optional.

## 4. The pattern of holdups (from git history, the handoffs and the reports)

| Class | Count / examples | Status |
|---|---|---|
| **A. Missing marker on first release** | 4 fix commits: assay `5460d9371` (08-17), run-gate `e47f769e6` (08-24), tester-base `656402603` (10-28), augeaswrap `fd363e29f` (11-08) | **2 more queued**: iogovmon, test-policy. SPEC.md:1515 says cmru creates the marker "when absent", but the code only does this when the whole file is absent. Docs and code disagree. |
| **B. Wrong `[Unreleased]` heading form** | tester-base `f093aed2e`, augeaswrap (in `fd363e29f`), ciu duplicate heading `7ef33e037` | refused correctly, but only found at release time |
| **C. Next-version drafts / stale [Unreleased]** (KI-23, KI-30, KI-58) | ciu: 6 recurrences (KI-23) + `acba4b3b9`, `5c879d3fa`; nyxloom `9444bd1d8`; run-gate and cgprofile notes parked outside the file (KI-58); cmru's 6.0 comment block | **Fixed by KI-78 (cmru 6.2.0, 2026-10-14).** No recurrence since. One brief (cgp2-pkg1) still told an agent to use the old `- UNRELEASED` convention. |
| **D. Hand-curated text inside generated sections** (KI-68) | run-gate `7e7413a90`, `43dd94c49`; assay `e4e3db005` (10-06/07). pwmcp today *looks* like this class but is the false positive described in section 2. | guard works; its message is hard to act on |
| **E. BOOTSTRAP-FROM-ZERO version guard** | **11 commits in 8 days** only to bump the Run-Gate number (b01d6c1f5, fc812e601, 5f2938ad4, 090d30d87, 68890590a, dce95063c, e82c13aa0, 2e4162526, 9920a9a11, 137ee29ed, plus inside 640c72fa3). One failed release gate (dce95063c). Reviewers flagged it as an unrelated red test (aw-0-REVIEW, bootstrap-2540-REVIEW). | **open; the largest current source of churn** |
| **F. cmru machinery rework** | 8 backlog items (KI-23/30/58/59/68/75/78, REL-02/10), with ≥4 review rounds on KI-78 alone | shipped |
| **G. Merge conflicts in [Unreleased]** | 7 reports (ciu-survivors, ciu-run, ciu-clix, cmru-hub-economy, delta-fix-cmruki115/tls-edge, nyx-3, hc-1a2/augeaswrap) | always resolved by keeping both sides, which is trivial |
| **H. Merge-commit noise in generated history** | **200** `- Merge ...` entries across pwmcp 11, ciu 40, cmru 23, assay 42, run-gate 53, cgprofile 19, nyxloom 11, augeaswrap 1 | cosmetic only |

**What these have in common:** each rule is reasonable on its own, but nothing checks the file when it is
merged. Every slip therefore surfaces at release time, often inside an operator-attended release window. Two
items are not about notes at all: E is a doc version pin, and today's pwmcp error is a version-input problem
reported as a changelog error.

## 5. Recommendation

Rule applied throughout: **refuse** where text could be lost or credited to the wrong release (correctness).
**Auto-fix or lint** pure-format slips. Use a known-issue/by-design note only for cosmetic matters.

| # | Candidate | Effort | What it fixes | Risk | Verdict |
|---|---|---|---|---|---|
| 0 | pwmcp coordinate commit (section 3) | S | today's block | none | **do now** |
| 1 | cmru KI-130: refuse an already-existing pending tag before the changelog step. KI-131: correct the base range in `_dropped_hand_edits` and make the Remedy line conditional. | S + S | the misleading error class seen today | low | **do** |
| 2 | (v) BOOTSTRAP: compute the version once in the doc (`RUN_GATE_VERSION=$(git describe --tags --abbrev=0 --match 'run-gate-v*')`, prefix stripped) and use `$RUN_GATE_VERSION` in the 6 places. The guard then checks only that the doc uses the variable and contains no literal version. | S | class E: 11 commits in 8 days plus gate failures | low (the doc is less literal, but always current) | **do**. Preferred over an "example version" marker, which leaves a stale number in the doc that readers copy. |
| 3 | (vi) Merge-time lint: `cmru changelog check <project>` (or an extension of the existing `cmru standards`). Checks: marker exactly once; exactly one `## [Unreleased]`, above the marker; no `- UNRELEASED` headings; no unclosed `<!--` under [Unreleased]; generated sections unchanged since their `source-end` (the KI-68 comparison, run earlier). Call it with one line from each project's existing R0 docs test or r0 lane. | M | classes A, B, D (and C if it returns) are caught at merge, not release. Today it would flag iogovmon, test-policy, cli-extended and cmru. | low | **do** |
| 4 | (ii) cmru writes the marker itself when the file has never had a release section, placing it after the [Unreleased] body, and fixes SPEC.md:1515 to match. | S | class A at its source | low (a file that was never released has nothing to misplace) | **do**. Until then, add the 2 lines to iogovmon and test-policy by hand. |
| 5 | (iv) `--no-merges` in `_subject_groups`. Also skip `Merge ...` lines in the KI-68 comparison, so that old sections are not newly reported as "hand edits". | S | class H going forward | low, provided the KI-68 adjustment is made with it | **do**. Do not rewrite old sections. |
| 6 | One paragraph in root AGENTS.md: "Write notes only under `## [Unreleased]` above the marker. Never edit dated sections or add `- UNRELEASED` headings. In a merge conflict there, keep both sides." | S | stops briefs drifting back to old conventions | none | **do** |
| 7 | (iii) Auto-fold or auto-repair hand edits in generated sections or `- UNRELEASED` drafts | M | — | **high**: silent loss or misattribution of notes | **don't**. Keep the refusals and improve their wording (item 1). |
| 8 | (i) A separate generated file plus a hand-written "Notes" file, or per-change note fragments | L | class G only; the current [Unreleased] already *is* the notes file | high: 21-file migration, reopens KI-78 after 4+ review rounds | **don't** |

**Ordered plan:** 0 → 1 and 2 (independent, can run in parallel) → 4 and 5 (both in `changelog.py`, one cmru
package) → 3 → 6. Items 1, 4 and 5 fit one cmru patch release. Item 2 touches the cmru precondition and the
bootstrap doc.

**What I would NOT change:**
- The three-kind design itself (section 1) and KI-78's fold. KI-78 is the fix that removed the biggest
  historical cause.
- The refusals that protect correctness (KI-23, KI-68, the near-miss `[Unreleased]` refusal). No override flag
  for KI-68.
- The existing standing hand-written text: cli-extended's upgrade notes, mdt-cockpit's detailed notes, ciu's
  pre-cmru sections and cmru's comment block. It is legal and cmru ignores it. Moving cli-extended's
  `[Unreleased]` above the marker is a one-minute tidy-up that the lint will point out.
- Old generated sections. Do not strip their merge lines retroactively.

**Known issue / by design (document only, no machinery):**
- The empty `pending release notes` marker pair left under `[Unreleased]` after a fold. It is harmless, and
  notes written between the two markers fold correctly.
- `[Unreleased]` merge conflicts. Resolve by keeping both sides.
