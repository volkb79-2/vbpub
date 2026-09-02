# Wave C (Go) — continuation brief 7

**Cumulative delta since BRIEF-6 only.** BRIEF-1 is the seam map; BRIEF-2..6
are the earlier deltas. Nothing here re-copies any of them.

Written by **generation 6**, a fresh session seeded with BRIEF-1..6 plus the
controller's 2026-09-02 entry (`vbpub@53eba55b`, DA-9).

**This brief is a completion record, not a hand-off to a generation 7.** Every
item on generation 6's dispatched list is done. What follows is what a fresh
reader — most usefully the adversarial reviewer — needs in order to check it.

---

## 1. BRIEF-6 §5's task list, item by item

| task | state |
|---|---|
| 1. F008-A4 — fixture regeneration + re-derived expectations | **DONE**, `394c6cc2` |
| 2. F008-A5 — the srdm qualification per DA-6 as corrected by DA-9 | **DONE**, `3355d238` (the run itself; REPORT §41) |
| 3. the acceptance boxes | **DONE** — F008-A4 and F008-A5 both `proven`; **F008 `shipped`**, **M6 `done`** |
| 4. B057's remaining boxes | **DONE** — all three; the canary double is DELETED, not documented |

**One item was not on the list and is the most consequential thing here:**
**B061**, a real defect in assay's own statement join, found by F008-A5 on its
first run and fixed at `875382d2`.

## 2. The load-bearing facts a successor must not re-derive

**A cover profile can carry MANY records for one block, and the join must fold
them.** `go test -coverpkg=./...` instruments every package into every test
binary and `go test` concatenates each binary's section, so a block gets one
record per binary and only the binary that ran it carries a non-zero count.
srdm's real profile is 68 761 lines with **20 records per block**.
`attribute_statements` keyed a dict by extent — last record wins — and read
`count` off it. Fixed: fold executed-wins BEFORE any count is read, the rule
`go_cover.parse` already applies one layer down. **Do not "simplify" that fold
back into a dict comprehension.** Three record orders, an all-zero extent, and
the downgrade-invariant are asserted; B061 carries the whole story.

**Every frozen P27 witness and every regenerated fixture has exactly ONE record
per block.** That is why the defect was invisible for the whole wave, and it is
the general lesson: a fixture corpus built from single-package probes cannot
exercise a multi-package profile's shape. If you add a Go fixture and want it
to be a real control, give it repeated records.

**The regeneration is re-runnable and position-bound.**
`carve-assets/P27-recarve/regenerate-fixtures.sh` produces all three profiles
AND `fixture-oracle.json` from the fixture sources in one in-image run. The
source bytes must be settled BEFORE the run — a block extent is a line/column
pair, so editing a fixture's header moves every extent below it. The tests join
profile against oracle with the production `attribute_statements`, which
refuses on any extent disagreement, so that instruction is enforced rather than
advisory. Provenance, the raw run output, the per-fixture derivation table and
sha256 of everything consumed or produced: `P27-recarve/PROVENANCE.md`.

**`conftest.as_pre_oracle_attributed` is gone.** Its replacement,
`as_statement_attributed`, marks a profile attributed only where its line sets
already ARE statement truth, and CHECKS that: a block whose extent spans one
line contains only statements on that line, so its expansion equals its
statement set; a multi-line extent is refused. The oracle-document reader is
one function, `conftest.load_go_statement_oracle`, used by three modules.

**The srdm harness works and its artifacts are reproducible.** DA-9's shape
exactly: `git archive 10b174a5|83c2ff79 shared-ramdisk-depot-manager` from the
bind-mounted host repo (read-only, `/hostrepo`) into a container-local git
repository, lane file at the MODULE root, `source_roots = ["internal"]`, no
`cwd`, srdm's own `gate.sh:105` argv. **Nothing was committed under
`shared-ramdisk-depot-manager/` in vbpub.** The lane file IS committed inside
the synthetic repository, because `covergate` refuses on a dirty tree while
assay's dirty check is scoped to source roots — committing satisfies both and a
`.toml` outside `internal/` is judged by neither.

## 3. The method that found B061, because it is the transferable part

DA-6 required each disagreement to be classified as extent-expansion or
file-absence *before* a side is named. That rule is what produced the finding,
in a way worth restating:

* The **denominator** fit the prediction perfectly — A-217/B058 say `covergate`
  over-approximates, so assay's executable set must be smaller, and 418 < 684.
  A conclusion was available right there, and it was the expected one.
* The **covered ratio** did not fit. Extent-expansion moves numerator and
  denominator together; it cannot turn 93.4% into 39.0%.
* Chasing the half that did not fit found the defect. Reading the denominator
  alone would have shipped B061 with "assay is stricter, by design" written
  next to it as evidence.

Two controls were run before any hypothesis was tested: srdm's argv twice in
the same checkout (profiles differ, but only in `cmd/srdm`, outside both tools'
scope), and a control lane whose argv copies `covergate`'s own profile file
into assay's artifact path — assay judging byte-identical bytes. It returned
identical numbers, which is what proved the difference was a judging rule and
not a measurement.

## 4. Scope state — the wave's own list

Every item of the original "Wave C" list is landed, and F008's five acceptance
criteria are all `proven`. `2-product-definition.md` records F008 `shipped`;
`3-roadmap.md` records M6 `done`, with the two findings neither the roadmap nor
the carve predicted (B059's module-path blocker, B061's repeated records) named
there because both were found by running rather than reading.

Backlog: **B057 closed (all three boxes)**, **B059 closed (all four)**, **B061
filed and closed**. Next free backlog id: **B062**. `decisions.md` is
UNCHANGED this generation — next free is still **A-405**. No decision row was
written for B061 deliberately: it implements a rule already ruled (A-391,
executed-wins) and already stated in the code's own comment, and calling a bug
fix a decision would inflate it.

**Decision asks open: none.**

## 5. What a reviewer should attack first

1. **The B061 fix's edge case.** `seen.count == 0 and block.count > 0` keeps
   the first non-zero record. In `atomic` mode counts are sums, so a later
   record with a LARGER non-zero count does not replace an earlier non-zero
   one. That is deliberate — nothing downstream reads the magnitude, only
   `count > 0` — but it is the line to challenge if anything ever does.
2. **The F008-A4 fixtures' extents against their sources.** `hello.go` is 40
   lines; the profile's blocks are `32.32,34.2` and `38.35,40.2`. Check them
   against the file by eye; the join would refuse a mismatch, but the join is
   also the thing under review.
3. **Whether `as_statement_attributed`'s invariant is actually true.** The
   claim is: a single-line extent's naive expansion equals its statement set.
   Attack it with a single-line block carrying two statements.
4. **The srdm classification's independence.** Both tools' rules were
   re-implemented from their own published descriptions and reproduce their own
   printed numbers (684/639/93.4 and 418/394/94.3). If those re-implementations
   are wrong in the same direction as the tools, the classification inherits
   the error. The check is the numbers matching what each tool printed, and
   that is in REPORT §41.
5. **The one thing NOT proven:** that srdm's `covergate` should change. B058
   stays open as a finding about srdm's tool. This wave measured it and did not
   patch it, on purpose.

## 6. Ledger

Decisions this generation: **none**. Next free: **A-405**.
Backlog: **B061** filed and closed. Next free: **B062**.
Acceptance boxes ticked: **F008-A4**, **F008-A5** → **F008 `shipped`**, **M6
`done`**.
Backlog boxes ticked: **B057** (3 of 3), **B059**'s last, **B061** (4 of 4).

## 7. Gate

**Run 10: PASS on `3355d238`**, the tip — `ASSAY_REGISTERED_GATE_COMPLETE=1`,
`GATE_EXIT=0`, eleven phase markers through `self-hosted-lane-passed` /
`topos-qualified` / `cmru-b006a-qualified` / `independent-self-hosting-passed`,
and the installed wheel `assay-4.0.1.dev39+g3355d238` names the judged commit
itself. A SEPARATE, independent grep for `FAILED|DIRTY_TREE|Traceback` returned
nothing. Transcript in REPORT §43.

The only commit after run 10 is the docs-only one carrying this brief, the
LOG's gate paragraph and REPORT §43 — no source, test, packaging, vocabulary,
fixture or decision-file change. This brief was written into a scratchpad
OUTSIDE the worktree while the run was in flight, exactly as BRIEF-6 was, for
the reason generation 3 lost a run: the self-hosted lane refuses `DIRTY_TREE`
on an untracked file and is right to.

Devcontainer full suite on the F008-A4 tree: **3905 passed, 18 skipped** (from
3902/18 — the three new tests are two union-fidelity controls and one canary
granularity check; B061's three arrived after that count).

**The wrapper-vs-job trap did not fire this generation.** That is nine
instances across five generations and five agreeing ones. The markers were read
separately anyway, which is the only reason this sentence can say which was
true.

---

## SELF-COMPACTION PROMPT

**KEEP:** BRIEF-1 in full (the seam map); BRIEF-2..6 in full; this brief in
full; the controller's 2026-09-01 and 2026-09-02 entries (DA-4..DA-9); the
rules block (A-334, A-335, A-042/A-043, A-097/A-101, decisions.md append-only
from **A-405**, backlog from **B062**, `git commit -F <file> --only --
<paths>`, the trailer, no `!` marker, **file edits via Edit/apply_patch,
never a rewrite script**); §2 and §3 above; the gate command and the
separate-verdict-read discipline.

**DROP:** how the fixtures were regenerated step by step (closed —
`regenerate-fixtures.sh` is committed and re-runnable, and `PROVENANCE.md`
carries the raw run); the search for why assay and `covergate` disagreed
(closed — B061 and REPORT §41 carry the whole chain, including the two
controls); B057's two shortcuts (both deleted, not documented — there is
nothing left to reason about).
