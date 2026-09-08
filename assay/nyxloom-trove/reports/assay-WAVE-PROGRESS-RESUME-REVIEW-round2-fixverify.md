# Fix-verification (round 2) — progress/resume wave (B067/B064/B065/B066)

**Reviewer:** same independent session that produced
`assay-WAVE-PROGRESS-RESUME-REVIEW-round1.md` (NOT ACCEPT). Companion to that
file, which is left intact as the record of what was found.
**Verifying:** `b5532895` (fixes) and `e1db9655` (LOG/REPORT), on
`feat/assay-progress-resume-2026-09-08`.
**Review worktree:** `/workspaces/vbpub/.worktrees/assay-progress-resume-review`,
updated to `e1db9655`.

## Verdict: **ACCEPT**

The blocker is genuinely fixed — verified at the loader *and* at the process
boundary with my own original repro — and the fix is a real change of predicate
rather than a patch of my two examples. All six should-fixes and seven of eight
nits are fixed, each one re-derived here rather than read off the disposition
table. N5 was the controller's to rule and has been ruled; the mitigation
shipped is correct.

Every claim in the implementers' round-2 LOG/REPORT that I checked was true, and
the REPORT is notably honest: it records B067's box 1 as
"✅ after `b5532895`; ❌ as first shipped" rather than quietly overwriting the
tick, and it volunteers that the box's own wording had been paraphrased in a way
that dropped "as an A-row".

One **new, minor** observation is recorded at the end. It does not block the
merge and I recommend filing it rather than fixing it here.

---

## 1. B1 (blocker) — FIXED

The predicate is now `native_r2 = r2 and not ingested_r2` / `if not native_r2:
refuse` (`config.py:1820`), which is orthogonal to R3 exactly as the coordinator
describes.

**My original repro, re-run against the fixed code** — same lane, same
instrumented `subprocess.run`, same everything:

```
MY ORIGINAL REPRO: R0/R1+R3, budget=unbounded
    exit=2   BAD_LANE_CONFIG ... an R0/R1 lane is ONE command ...
    lane children launched: 0    with timeout=None: 0

MY 2nd REPRO: ingested R2+R3, budget=unbounded
    exit=2   BAD_LANE_CONFIG ... an ingested R2 lane is ONE command ...
    lane children launched: 0    with timeout=None: 0
```

The lane is refused before a single child is launched. `timeout=None` on the
lane's own command no longer happens.

**Both controls still behave**, so the fix does not over-refuse:

```
native R2, budget=unbounded          -> exit=1, 2 children: [None, 45.0]
                                        (the one None is the baseline = B076,
                                         unchanged and still openly filed)
R0/R1+R3 with a NUMERIC budget       -> exit=1, 3 children:
                                        [299.87, 29.98, 29.88]
                                        (lane command under the lane budget,
                                         both canary halves under per-attempt)
```

**Does the six-shape test cover what it claims?** Yes. I re-derived the whole
table independently through `load_lane_file` rather than trusting the test, and
got the same six answers the test asserts:

| shape | mine | test asserts |
| --- | --- | --- |
| R0/R1 | refused | False |
| R0/R1+R3 (+`budget_per_attempt`) | refused | False |
| ingested R2 | refused | False |
| ingested R2+R3 (+`budget_per_attempt`) | refused | False |
| native R2 (+`budget_per_candidate`) | admitted | True |
| native R2+R3 (+both) | admitted | True |

The test builds each shape as a real `assay.toml`, calls the real loader, and
asserts the whole `admitted` dict in one comparison — so a future change that
flips any single cell fails it. That is the shape whose absence let B1 through
(every tier previously tested in isolation), and it is now present.

**Hunting for a NEW combination.** I probed beyond the six:

* native R2 **without** `budget_per_candidate` → refused, names the missing key;
* native R2+R3 **without** `budget_per_attempt` → refused, names the missing key
  (so `budget_per_attempt` is still *necessary*, just no longer *sufficient*);
* `["R0","R2"]` native, no R1 → admitted, correctly (it is a native sweep);
* `["R0","R3"]`, `["R0"]` → refused.
* **R4** — worth stating because B067's own text says "R3 (and R4 when it
  lands)". R4 is in `RIGOR_LEVELS` and `_required_judge_fields` demands
  `judge.red_first`, but `red_first` is **not an accepted judge key at all**, so
  *no* R4 lane loads under any budget. R4 is therefore not a live escape route.
  That contradiction is pre-existing and unrelated to this wave; I mention it
  only so nobody assumes R4 was checked and found safe for a *reason*.

I found no combination that still slips through.

## 2. SF-1 (`--state-dir` containment) — FIXED, and genuinely general

`_containments` now asks both path namespaces in both directions and refuses if
*any* pair lands inside. I re-ran my two original probes and added four
variations of my own:

| probe | result | tree after |
| --- | --- | --- |
| **C** project root reached via a symlink (my original) | **REFUSED**, names the resolved in-tree path | clean |
| **D** intermediate symlink hop → repo (my original) | **REFUSED** | clean |
| **E** symlink **chain** `a → b → repo` | **REFUSED** | clean |
| **F** **relative** `--state-dir hop/rel` from a cwd *outside* the repo, via a symlink back in | **REFUSED** | clean |
| **G** relative `../proj/dotdot` traversing back in | **REFUSED** | clean |
| **I** genuinely outside (must be accepted) | accepted | clean |
| **J** gitignored inside the tree (must be accepted) | accepted | clean |

So it is not a patch of my two cases: the two namespaces × two directions
generalise to chains, relative spellings and `..` traversal, and the two
must-accept controls confirm it did not become an over-refusal. The
fail-closed disposition is the right call and is argued in the helper's own
docstring.

## 3. SF-5 (`--progress` preflight) — FIXED, and it probes the destination itself

`--progress` now goes through the identical helper. Six cases, all as intended:

| case | result |
| --- | --- |
| non-ignored `.assay/` (my round-1 case) | **refused before any work**, message names `--progress`, the cause and the fix; tree clean |
| ignored `.assay/` (the estate convention) | accepted, stream written, tree clean |
| `.gitignore` = `*.jsonl`, destination `.jsonl` | **accepted** |
| `.gitignore` = `*.json`, destination `.jsonl` | **refused** |
| outside the tree | accepted (no git call at all) |
| `:`-prefixed component | refused **by name**, before git is asked (round-1 N2) |

Rows 3 and 4 are the "extension-matching `.gitignore`" concern the implementer
flagged, and they settle it: the probe really is the destination file itself. A
representative `…0000.json` sibling would have got row 3 wrong (refusing a
correctly-configured consumer) and row 4 wrong in the other direction. The
asymmetry with `--state-dir` — which *must* probe a representative record name,
because the directory does not exist yet and `check-ignore` cannot tell a
non-existent path is a directory — is real and correctly reasoned in
`_refuse_a_visible_store_inside_the_tree`'s docstring.

Round-1's regression is closed: the R0/R1 case that used to pass, then started
self-inflicting a bare `DIRTY_TREE`, now gets a named refusal instead.

**Does the new refusal break any real consumer?** No — I checked rather than
assumed. Every `kind = "assay"` lane in the estate is in a project whose
`.assay/` is already git-ignored, so the destination run-gate RG-33 passes
(`.assay/progress-<lane>.jsonl`) is accepted everywhere it is used today:

```
vbpub/nyxloom/run-gate.toml : .assay/ -> IGNORED
vbpub/cmru/run-gate.toml    : .assay/ -> IGNORED
vbpub/ciu/run-gate.toml     : .assay/ -> IGNORED
dstdns/run-gate.toml        : .assay/ -> IGNORED
```

So this refusal costs the estate nothing and only catches a
misconfiguration that would otherwise have surfaced as an unexplained red.

## 4. SF-6 (hollow test) — FIXED, and I mutation-tested the assertion

The test now records every attempted write and asserts `len(attempts) == 1`. I
checked it is actually binding by running the same body against the shipped
implementation and two mutants:

```
SHIPPED (retires on failure):        attempts=1   -> assert len==1 PASSES
MUTANT  (swallows, never retires):   attempts=14  -> assert len==1 FAILS
MUTANT  (bare yield, no thread):     attempts=0   -> assert len==1 FAILS
```

So it fails both ways the old assertion-free version could not: it catches a
heartbeat that never ran *and* one that spins on a broken destination for the
rest of the lane. Exactly the two halves the docstring claims.

## 5. SF-2 / SF-3 / SF-4 — real and accurate, not merely present

**SF-2 (backlog).** B067 now reads 3/3 `[x]` with an `IMPLEMENTED` note. Box
states across the family, checked by parsing the sections rather than eyeballing:

```
B064: [x]=3  [ ]=1   (R3 half still honestly open)
B065: [x]=3  [ ]=0
B066: [x]=2  [ ]=0
B067: [x]=3  [ ]=0
B076: [x]=0  [ ]=3   (still open — it was not fixed, and is not claimed to be)
```

**SF-3 (A-rows).** `decisions.md` gains **A-444…A-447**, one per item. I read
A-444 and A-447 in full rather than checking they exist:

* **A-444** does what B064's box actually asks — records the ruling *as an
  A-row* and names all three rejected alternatives (build nothing / per-tier
  bespoke events / runner-aware progress → B073), and states the R3 half is not
  built. The box's tick is now earned.
* **A-447** describes B1 accurately: the shipped predicate verbatim, why
  `budget_per_attempt` never bounded the lane's own `argv`, both affected
  shapes, the new predicate, that `budget_per_attempt` remains necessary but not
  sufficient, that the surviving unbounded baseline is B076 — and the testing
  lesson (every tier tested in isolation, nothing asked what a combination
  does). Every one of those matches what I re-derived.

**SF-4 (CHANGES).** A real `### Changed` section with the `candidate_total`
change marked **BREAKING** and carrying a migration line ("read it from the new
`candidates` record"), plus a second `Changed` entry for the `--progress`
preflight and a `### Fixed` section covering B1 and SF-1. It also now documents
the per-attempt-expiry cascade, which was my N8 concern about an undocumented
behaviour change. Accurate.

## 6. The nits — spot-checked

| nit | verified how | result |
| --- | --- | --- |
| **N1** lock | 9-record real run, jobs=4 | `elapsed_s` non-decreasing; stamps now computed inside the lock |
| **N2** `:` magic | real CLI, both flags | named refusal before git is asked |
| **N3** clock seam | code read | both heartbeat sites now use the real `monotonic`, deliberately, with the reason stated |
| **N4** `end` on early returns | two real runs | `over_candidate_cap` (`candidate_total=2`) and `no_candidates` (`candidate_total=0`) both emit `end` with the documented `reason`; a normal sweep emits `reason: null` and buckets that still match the verdict exactly |
| **N6** `__all__` | import check | `default_state_root` and `mutation_state_record_name` both exported |
| **N7** e2e heartbeat | new test + my own round-1 run | a real 7 s child under `--progress-heartbeat 5` produces a real `command_running` record, and the test also pins that no tick follows `command_finished` |
| **N8** per-attempt expiry | test read | pins the *cascade* specifically — asserts the later target's `description` says "never started" (not a second independent expiry) and that the lane clock stayed under 100 s of its 300 s budget, so only the tightened bound can have cut it short |

**N5 — the controller's ruling, and the mitigation.** I agree with the ruling
and confirm the shipped mitigation is clear and accurate. `docs/CONSUMERS.md`
now carries:

| field | on | means |
| --- | --- | --- |
| `elapsed_s` | every record | seconds since the `run` header — the *run*'s age |
| `command_elapsed_s` | `command_running` | seconds since *this command* started |
| `elapsed_seconds` | `candidate` | how long *that one mutant* took |

All three rows are correct against the code (`elapsed_s` is measured from stream
open, and the header is the first record emitted after open, so "since the `run`
header" is right). Three names, three rows, one place — that is the disambiguation
the confusion needed, and it avoids stacking a second breaking artifact change
onto `candidate_total`'s in one release.

## 7. Regression sweep — nothing round 1 verified has broken

Re-derived on the fixed tip, not inherited:

* R0-only stream still exactly `run → command_started → command_finished →
  verdict_written`; R0+R1 still adds `snapshot_materialized` and
  `coverage_parsed`;
* `end` buckets still agree field-for-field with the same run's verdict;
* closed vocabulary still enforced (unlisted name refused, 0 records written);
* two-worktree resume still works: `resumed_total=1`, `pending_total=0`;
* `VERDICT_SCHEMA_VERSION` 10, `inventory_schema` 1, `assay.toml`
  `schema_version` 2 — all unchanged, and `git diff main...HEAD` over
  `verify.py` and `verdict.py` is still **empty**;
* the affected + new test files run clean directly: **134 passed**
  (`nice -n 19 ionice -c 3`, serial).

---

## New observation (not a blocker — recommend filing, not fixing here)

**A `--state-dir` or `--progress` path that traverses a symlink *inside* the
judged tree produces a raw git-stderr passthrough.** Isolated repro, symlink
committed, target directory gitignored — i.e. a *correctly configured*
consumer:

```
.gitignore = "realstore/"
<repo>/aka -> realstore            (committed)

--state-dir <repo>/aka/x
  assay: ERROR/GIT_FAILED: git check-ignore aka/x/0000….json failed (128):
  fatal: pathspec 'aka/x/0000….json' is beyond a symbolic link

--progress <repo>/aka/p.jsonl
  assay: ERROR/GIT_FAILED: git check-ignore aka/p.jsonl failed (128):
  fatal: pathspec 'aka/p.jsonl' is beyond a symbolic link

(direct, no symlink: --state-dir <repo>/realstore/x  ->  accepted, exit 0)
```

Assessment, and why it is not a blocker:

* **It is fail-CLOSED and the tree stays clean** — exit 2, nothing written. The
  safe direction, unlike the round-1 SF-1 defect it sits beside.
* **It is mostly pre-existing, not a regression from the SF-1 fix.** The shipped
  B066 code formed the identical `aka/x` pair (both spellings are lexically
  under the root, so the old single-pair check reached the same `check-ignore`
  and the same exit 128). `--progress` is the genuinely new half, via SF-5.
* **But it is a false refusal with an opaque message** — the configuration above
  is correct, and the operator gets git's own `fatal:` rather than a named
  reason. That is the same family as round-1 N2 (`:`-prefixed components), which
  *was* fixed by naming the case before asking git; symlink traversal is the
  sibling case and is unhandled.

My round-1 review did not find this — I probed pathspec magic but not symlink
traversal — so this is a new finding, not an unaddressed one. A backlog entry
(the fix is presumably to fall back to the fully-resolved spelling when
`check-ignore` reports "beyond a symbolic link", or to name the case as N2's is
named) is the right disposition. It should not hold the merge.

---

## Gate (my own run, from scratch)

Run independently in my own worktree at `e1db9655`, after checking `docker ps`
and `pgrep -af tester-unified-gate.sh` (no peer gate live, load average 3.8).
My container was identified by matching
`--inner /workspaces/vbpub/.worktrees/assay-progress-resume-review` in
`docker ps --no-trunc` and capped with `docker update --cpus=3` on that id
alone. Nothing was written into the worktree while it ran; this file was
authored in the session scratchpad and moved in only after the verdict was read
from the log's own markers, in a separate step (LESSONS L4).

```
cd /workspaces/vbpub/assay
./run-gate.py --worktree /workspaces/vbpub/.worktrees/assay-progress-resume-review tester-unified
```

**GREEN at `e1db9655`** — the merge candidate itself, not the fix commit
underneath it:

```
tester-unified: PASS (exit 0)
ASSAY_REGISTERED_GATE_COMPLETE=1
run-gate: lane 'tester-unified' exit 0
  commit: e1db9655e55e9afeddc17de9db53ef33318253f0   (self-hosted lane)
```

All **12** distinct `ASSAY_GATE_PHASE` markers present (`wheel-installed`,
`attestation-hardened`, `verdict-v5-accepted`,
`lane-schema-v2-successors-verified`, `verdict-v6-v7-v8-v9-hard-cut-verified`,
`verdict-v10-successors-verified`,
`judge-provenance-bound-to-the-installed-wheel`, `self-hosted-lane-passed`,
`topos-qualified`, `cmru-b006a-qualified`, `independent-self-hosting-passed`,
`pyflakes-clean`), and **zero** `ASSAY_GATE_DIAGNOSTIC` lines. This is my
second independent green on this branch (round 1 was green at `48561aba`), and
it confirms the fixes did not break the self-hosted lane, the topos/cmru
qualification lanes, or the independent self-hosting check — the three places
where a `--progress` preflight regression would have surfaced.

The implementers' own green was at `b5532895`; mine judges `e1db9655`, so the
docs commit on top is covered too.

I also ran the affected and new test files directly, serial, under
`nice -n 19 ionice -c 3`: **134 passed**. The fix commit adds exactly 13 new
test functions and removes none, matching the LOG's "4302 passed (13 new)"
against the prior 4289.

---

## Summary

| round-1 finding | status |
| --- | --- |
| **B1** blocker | **fixed** — re-derived at the loader and the process boundary; no new hole found |
| SF-1 | **fixed** — general across 5 refusal shapes, 2 acceptance controls |
| SF-2 | **fixed** — and honest about box 1 having been wrongly ticked |
| SF-3 | **fixed** — A-444…A-447, read and checked for accuracy |
| SF-4 | **fixed** — BREAKING callout with a migration line |
| SF-5 | **fixed** — including the extension-matching case, both directions |
| SF-6 | **fixed** — assertion mutation-tested and binding |
| N1, N2, N3, N4, N6, N7, N8 | **fixed** — each spot-checked |
| N5 | **ruled by the controller**; mitigation shipped and correct |

**ACCEPT.** This is the merge signal.
