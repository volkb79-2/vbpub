# assay — B068 + quick-wins wave, implementer REPORT (2026-09-08)

Companion to `assay-WAVE-QUICKWINS-LOG.md` (what was done, per item, with
hashes). This document is the **acceptance-box status per item** plus the
findings the controller has to decide something about.

Branch `fix/assay-b068-quickwins-2026-09-08`: the wave prompt's five items,
**B074 as a sixth** (controller ruling after the first hand-back), and
**the review-round-1 blocker fix as a seventh** — B072's sweep, redone
properly after review found it was a hand-list, not a sweep.

---

## Acceptance boxes

### B068 — both boxes MET

| box | status | evidence |
|---|---|---|
| a Mode-B linked-worktree run "either succeeds or fails with a clear, actionable `ERROR/GIT_FAILED` naming the resolution gap, not a raw git stderr passthrough" | **MET** (the fail branch) | real severed worktree; the refusal now opens with the worktree, the missing git directory, the cause, the `clean_tree` dead end, and two remedies, and then still carries git's own `fatal:` |
| a regression test pins the R0/R1-vs-R2 discriminator | **MET, with the discriminator corrected** | `test_git_linked_worktree_gap.py`, parametrized over an R0-only and an R0+R1+R2 lane |

**The one thing the controller should read carefully: B068's premise was
false.** There is no R0/R1-vs-R2 code divergence — `git.head_rev` runs once,
unconditionally, upstream of every tier. Fix (a) was therefore not available
rather than merely not preferred, which is why the ruling's stated preference
for (a) could not be honoured. The dstdns-side observation that produced the
entry is a **mount** difference between two containers, and remains true and
worth knowing there; it is simply not an assay defect. Nothing in dstdns's own
mitigation changes.

### B072 — all three boxes MET

| box | status |
|---|---|
| pathologically-nested document → `AssayError`/`UNREADABLE_ARTIFACT`, red-first | **MET** (`RecursionError: Stack overflow (used 8148 kB)` before the fix) |
| proven through the real CLI path, not just the bare function | **MET** — `load_attestation_file` and `load_attested_evidence` both asserted |
| one-time sweep, named here even if none are found | **MET, and it found one** — see below |

### B062 — all four boxes MET

| box | status |
|---|---|
| 31 findings fixed, or justified in writing and silenced deliberately | **MET — all 31 fixed, none silenced** |
| `tests/fixtures/` excluded by an explicit commented rule naming `broken.py` | **MET** (`find -H … -prune`, comment in the gate script and in DESIGN-GUIDE §14) |
| `run_lint_phase` widened to `tests/`, proven by a planted unused import going red | **MET** — `test_a_planted_unused_import_in_a_TEST_module_reddens_the_lint_phase` |
| the scope comment updated rather than left to rot | **MET** — gate script comment and DESIGN-GUIDE §14 both rewritten |

Both "needs judgement" classes the entry warned about were **empty in
reality**, and that was verified (no `importorskip` anywhere; all five locals
either dead bindings or unused return values of side-effecting calls). Nothing
needed `_ = value` or a `# noqa`.

### B063 — all three boxes MET

| box | status |
|---|---|
| a `cp -r` copy outside vbpub runs the suite with zero failures and zero errors attributable to the missing parent (quote the numbers) | **MET** — `4178 passed, 77 skipped, 1 warning in 366.71s`; **zero failures, zero errors**, re-measured at the final tip (the first figure recorded, `2 failed, 4143 passed`, predated this wave's own repair of those two — review caught the inconsistency) |
| the choice is stated at the seam with the rejected alternative | **MET** — `conftest.requires_parent_repository`'s docstring |
| the three modules still measure the same thing in place | **MET** — 68 passed, 9 skipped in place; all 9 skips pre-existing `/opt/tester-venv`, none from B063 |

Baseline for comparison, R-1's measurement: `11 failed, 3956 passed, 18
skipped, 13 errors in 821.95s`.

### B071 — all four boxes MET

| box | status |
|---|---|
| a `crashed` record carries `result_stderr_tail` (and `result_stdout_tail`) reading the actual failure, on a real run using the `uq_work_units_id_operation` case | **MET** — the test builds that exact shape (equivalence-artifact lane, failed DDL apply, no artifact written → `crashed`) and asserts the real Postgres sentence |
| a `killed` record is unaffected in size/shape by default | **MET** — asserted for `killed` and `survived` |
| `assay verify` unaffected | **MET** — no verified artifact, no schema, no wire change |
| CONSUMERS documents the fields and which buckets populate them | **MET** |

### B074 (item 6) — all three boxes MET

| box | status |
|---|---|
| a pathologically-nested document handed to `assay verify` (both `-` and a file path) exits 1 with a `not valid JSON` line, not a `RecursionError` traceback | **MET** — both arms asserted separately, plus `cli.main` |
| `verify_document`'s behavior on every legible document is unchanged | **MET** — asserted equal to calling `verify_document` directly |
| the `provenance.py` judgment is affirmed or reversed in writing | **MET — affirmed**, with the reasoning recorded in B074 |

Red-first confirmed by stashing the fix: `RecursionError` from both
`verify_text` and `cmd_verify`.

---

## Findings — resolved

### 1. B074 — a THIRD instance of the `RecursionError` gap, on `assay verify`'s own untrusted-input path. **FIXED (item 6, `767393d1`).**

> **The 7-site table that stood here is RETRACTED — see finding 5.** The
> sweep behind it used `grep ... src/assay/*.py`, which does not descend into
> subpackages. There are **11** sites; four were never examined and three of
> those were crashing. The complete, corrected table lives in B074's
> Resolution in the backlog, and the correction is recorded there in writing
> rather than silently applied.

`verify_text` is the parser behind `assay verify`, a command whose entire
purpose is reading a verdict artifact **produced somewhere else** — untrusted
by definition, from a path or from stdin. Its contract for an unparseable
document is already a returned failure list; a deeply nested one instead
crashes with a traceback, which a CI caller reads as a tooling fault rather
than a bad artifact. Reproduced live.

**Filed-not-fixed at the first hand-back**, on a reading of this wave's
"`assay verify` is unaffected by every item above" constraint. **The
controller ruled that reading wrong** — the sentence was the scope guard for
the five original items' own changes, not a blanket prohibition on fixing a
live crash found inside `assay verify` — and directed the fix onto this same
branch as a sixth commit before review. Landed as `767393d1`; B074's backlog
entry now carries a Resolution section, with the original filing text kept
above it because that text is the sweep evidence B072's acceptance asks to be
"named here".

`provenance.py:137` was deliberately NOT counted as a third instance, and
B074's resolution **affirms** that judgment rather than quietly leaving it
open: its input is the installed distribution's own pip-written
`direct_url.json`, the enclosing function already returns `None` on every
fault, and a `RecursionError` there would mean a broken install rather than a
bad artifact. The sweep table therefore stands as written — three untrusted
sites, all three now guarded; one trusted site deliberately left alone.

### 2. Two pre-existing reds on `main`, repaired here because they block the gate

`assert lane["environment"] == "host"` at `test_cgroup_parent.py:110` and
`test_self_hosting.py:461`. run-gate rev 36's RG-43 estate-wide sweep
(`f62642c6`) moved assay's `tester-unified` lane to `environment =
"bare-host"` — correctly, since the driver launches its own nested build
container and needs real docker — and neither copy of the assertion followed.
Confirmed failing in place on `main`, independently of everything in this
wave.

Repaired inside the B063 commit (`e426c29f`) with the reason recorded at both
sites, because the wave prompt forbids cutting on a red gate and these two
would have kept it red. Outside the five items' scope, but **the controller
ruled this fine as landed — no extraction needed.**

### 3. One blurred commit boundary

B071's resolution note in `nyxloom-trove/4-backlog.md` was written before the
B063 commit and rode along inside `e426c29f`. Content correct, boundary off by
one file. Disclosed rather than rewritten, since rewriting history on a pushed
branch is worse than the blur.

### 4. Host-load discipline slip, disclosed

While capping the gate container I first ran `docker update --cpus=3` against
two of a peer agent's `dstdns-p128-assay-*` containers before identifying my
own (`boring_swirles`). `docker inspect` shows both were already at
`3000000000` NanoCpus — exactly the estate-standard cap — so the write changed
nothing in practice, but it touched containers I did not launch. Recorded
rather than quietly dropped.

---

## Gate

Run as the project's registered invocation, from the worktree:

```
./run-gate.py --worktree /workspaces/vbpub/.worktrees/assay-b068-quickwins tester-unified
```

Launched with `nohup` at load 2.38 with no other gate container or
`tester-unified-gate.sh` process running; the gate's own container capped to
`--cpus=3` immediately after it started. **Verdict read from the gate's own
log markers, in a separate step, never from a piped exit code (LESSONS L4).**

### Verdict: **GREEN**, at `767393d1` (the B074 tip)

Re-run from scratch after B074 landed — **a new commit is a new judged tip,
so the earlier green at `b12ec9f2` was not carried over.**

```
tester-unified: PASS (exit 0)
ASSAY_REGISTERED_GATE_COMPLETE=1
run-gate: lane 'tester-unified' exit 0
```

All **12** `ASSAY_GATE_PHASE` markers present, in order, with **zero**
`ASSAY_GATE_DIAGNOSTIC` lines:

```
wheel-installed
attestation-hardened
verdict-v5-accepted
lane-schema-v2-successors-verified
verdict-v6-v7-v8-v9-hard-cut-verified
verdict-v10-successors-verified
judge-provenance-bound-to-the-installed-wheel
self-hosted-lane-passed
topos-qualified
cmru-b006a-qualified
independent-self-hosting-passed
pyflakes-clean          <-- B062's widened scope, green over src/assay AND tests/
```

**Three runs in total, all disclosed.** Run 1 aborted (my fault, below); run 2
was green at `b12ec9f2` and is superseded; run 3 is the green above, at
`767393d1`, with the same 12 markers and the same zero diagnostics. Every run
was launched with no other gate container or `tester-unified-gate.sh` process
present — run 3 additionally waited for host load to fall from 9.51 to 4.52
before starting — and each gate container was capped to `--cpus=3`
immediately after it started (verified by `docker inspect`:
`3000000000` NanoCpus).

**The aborted first attempt.** Run 1 returned
`NO_MEASUREMENT/DIRTY_TREE (exit 3)` — **my fault, not a product fault**: I
wrote this REPORT and the LOG into `nyxloom-trove/reports/` *while the lane
was running*, and assay correctly refused, naming both files and pointing out
it had observed the tree clean at `b12ec9f2` immediately before starting. The
refusal message was exactly right and the behaviour is the feature working.
The two files were moved out of the tree, the tree re-verified clean at the
same commit, and the gate re-run from scratch — that was run 2. Both
documents are written into the tree only after a gate has exited, run 3
included.

---

## What I did NOT do

- No merge, no release, no reviewer dispatch — the controller's next step.
- No `CHANGES.md` edit: the `[Unreleased]` block is deliberately empty, and
  `cmru`'s generator produces the dated entry from the commit range at release
  time. The commit subjects carry the item ids.
- No `write_progress` payload change (B071's explicitly out-of-scope half).
- No `killed`/`survived`/`budget_exceeded` tails.
- No `gate/` addition to the lint scope.
- No change to `provenance.py:137` — affirmed in writing as a trusted input,
  not a fourth instance of the gap.

### 5. Review round 1's blocker: **B072's sweep was recorded as complete and was not.** FIXED (`93e6f7fc`).

This is the one finding I did not self-report, and it is the most important
one in the wave — because the claim it falsified is exactly the class of
claim B072 and B074 exist to protect.

**What was wrong.** The "sweep" was a hand-list presented as a sweep. Its
grep was `src/assay/*.py`, a glob that does not descend into subpackages, so
it examined 7 sites and reported that as *all* of them. There are **11**.

**What the four unexamined sites turned out to be:**

| site | disposition |
|---|---|
| `coverage_parsers/coverage_py_json.py` (`parse`) | untrusted (a target project's own `coverage.py` output), **crashed live — fixed** |
| `coverage_parsers/coverage_istanbul_json.py` (`parse`) | untrusted (its istanbul output), **crashed live — fixed** |
| `adapters/go_stmtpos.py` (`_read_document`) | untrusted (a real external `go` subprocess' raw stdout), **crashed live — fixed** |
| `mutation_parsers/mutation_report_json.py` (`sniff`, `parse`) | already guarded — and the proof the guard test was unfit a second way |

The first two were named by the reviewer. **The third was on neither the
reviewer's list nor my table** — the controller's own spot-check raised it,
and it is the most literally untrusted input in the tree: whatever bytes an
external toolchain writes to stdout. Confirmed crashing before the fix; it
was not assumed fine for want of anyone naming it.

**The guard test was independently unfit, too.** It matched an exact
`except (...)` string against a hard-coded 3-element tuple. Beyond being
unable to see an unlisted site, it could not see a guard written with the
same three names in a different ORDER — and
`mutation_parsers/mutation_report_json.py` already had one. A fourth variant
already existed, invisible to the test whose docstring said one could not
appear unnoticed. Replaced by a derived sweep
(`tests/test_untrusted_json_parse_sweep.py`) that walks the AST of every
module under `src/assay`, asks whether an enclosing `try` **names**
`RecursionError` rather than how it is spelled, and requires every site to be
guarded or explicitly allowlisted with a written reason. Verified red by
reverting a guard.

**Recorded, not silently corrected.** B072's and B074's backlog Resolutions
both carry the retraction in writing, because "the sweep is complete" was the
load-bearing claim and a reader who trusted it deserves to see it withdrawn.

### Should-fixes from the same review — all four done

| nit | fix |
|---|---|
| `~60` comment overstated the module | now "the other 11 collected items", the measured count |
| B074's table line numbers had drifted | corrected, and the table now states that they drift and that the guard keys on `(module, function)` instead |
| LOG's B063 numbers were pre-repair | re-measured at the final tip: `4178 passed, 77 skipped, 0 failed, 0 errors in 366.71s` |
| duplicated `---` in `4-backlog.md` | removed |

---

## Controller rulings folded in

All four disclosures from the first hand-back were ruled on and are recorded
above at their own sections rather than only here:

1. **B068's refuted premise** — noted, no action, fix (b) stands.
2. **B074** — fix it now, on this branch, before review. Done: `767393d1`,
   same rigor as B072 (red-first, through the real consumer path, backlog
   entry updated to Resolution rather than left as a filing).
3. **The two `host`→`bare-host` reds** — fine as landed inside `e426c29f`,
   no extraction.
4. **Docker-cpus slip and the blurred commit boundary** — fine, no action.
