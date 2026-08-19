# Wave 2 — COMPLETE (B004 carved, reviewed, DEFERRED), 2026-08-17

**Wave 2's deliverable is a ruling, not a feature, and there is nothing to
release.** No file under `src/` changed, so assay stays at **v2.0.0** and no
dstdns notification was sent — the `.assay-inbox` protocol carries releases, and
inventing a message type for "we decided not to build something" would be worse
than the pointer that already exists in ciu's tracker.

## What landed

* `6724c94a` — the `--no-ff` merge into `main`.
* Gate green **twice**: on the branch at `9bd0cf72`, and on the merge commit
  `6724c94a` itself — `tester-unified: PASS (exit 0)` through
  `ASSAY_REGISTERED_GATE_COMPLETE=1`, including the cmru B006(a) qualification
  receipt with R0–R3 all PASS.
* `bdc3dc78` — **CIU-28** (renumbered **CIU-39**, 2026-08-19 — the ciu worktree-automation branch had independently allocated CIU-28) filed in `ciu/KNOWN_ISSUES_TODO_BACKLOG.md`, High,
  OPEN.

**A note on the second gate run, because the first attempt looks like a
failure and is not.** Running the gate against the live `main` checkout returned
`NO_MEASUREMENT/HEAD_CHANGED (exit 3)`: `/workspaces/vbpub` has a **concurrent
committer**, and it landed a nyxloom commit mid-run. That is the mechanism
working, not a defect. The confirming run must be taken against a stable
detached worktree pinned at the merge commit. Do that by default from now on;
gating the live shared checkout of this monorepo is a race by construction.

## The four rulings

| id | what |
|---|---|
| **A-275** | B004's implementation is DEFERRED on two independent blockers, and `4-backlog.md` §B004's "no verdict-schema change was needed and none proposed" is corrected to **false** for the VERIFIED half. |
| **A-276** | `PROVENANCE_UNVERIFIED` is RESERVED BY NAME, to ride whichever bump B001/P34 or B007 already pays for. B004 unblocks only when **both** that code has shipped **and** ciu can emit `verified-match`. |
| **A-277** | A-270's doc rule found its first real defect: `ALL_MUTANTS_EQUIVALENT` was documented nowhere since v5. Repaired, plus a fifth derived vocabulary so the gate catches the next one. |
| **A-278** | Wave 1's release embargo could not survive its own success — it forbade the very release it protected. Rephrased to assert the property rather than the proxy. |

## Why B004 is deferred, in two sentences

It needs verdict-schema surface after all — exactly one new `ReasonCode`,
because `_check_reason_code` demands a code for every non-`PASS` outcome,
`adjudicated` evidence has no payload slot for ciu's status string, and none of
the 30 shipped codes truthfully names "the provenance tool returned a non-green
verdict". And ciu cannot emit `verified-match` on any live host in this estate,
because it compares every container's OCI label against its own repository hash,
vendor images included — so the feature's only day-one observable would be its
failure mode.

## What a future session must NOT redo

* **Do not re-carve B004.** `W2-CARVE-B004-provenance-verified.md` (958 lines)
  and `reports/assay-B004-carve-review-fable.md` are both merged. The review
  tested four zero-schema escapes plus a Tier-3 recast the carve never
  considered; all died, each with its reason. Read those before proposing a
  fifth.
* **Do not re-measure ciu's shape casually.** The evidence is frozen at
  `carve-assets/W2/` with hashes, and the MANIFEST records what each document
  proves. If ciu's behaviour changes, capture a NEW asset — never edit these.
* **Do not treat B004's PASS branch as unwitnessed.** The carve overstated this
  and A-275 corrects it: ciu's own `provenance-verified-match.json` is
  producer-pinned by `ciu/tests/tests/test_ciu_provenance_json.py:78`, so a
  green-path oracle can consume real output. What is missing is a green document
  from a **live** host.

## Sequencing, as it now stands

**B001/P34 (wave 3) → B007.** Wave 2 inverted one premise of that order without
changing the order itself: B007 is no longer a solo migration. It has a
passenger now (`PROVENANCE_UNVERIFIED`), so v7 carries two features on one
consumer migration — which is what the original pairing argument wanted.
B001/P34 still runs first because it is believed to need no schema surface, and
because B004 is blocked on ciu regardless of when any code ships.

**Wave 3's carve is dispatched** on branch `assay-P34-sql-adapter`. Its brief
carries four corrections to `reports/assay-P34-CARVE-SCOPE.md`, which was
written against **v5** and whose §4 claim that the schema "is not expected to
move again" was falsified six days later.
