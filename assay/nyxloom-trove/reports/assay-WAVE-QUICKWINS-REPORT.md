# assay — B068 + quick-wins wave, implementer REPORT (2026-09-08)

Companion to `assay-WAVE-QUICKWINS-LOG.md` (what was done, per item, with
hashes). This document is the **acceptance-box status per item** plus the
findings the controller has to decide something about.

Branch `fix/assay-b068-quickwins-2026-09-08`, tip `b12ec9f2`, 5 commits over
`a78d0280`.

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
| a `cp -r` copy outside vbpub runs the suite with zero failures and zero errors attributable to the missing parent (quote the numbers) | **MET** — `2 failed, 4143 passed, 77 skipped in 446.07s`; **zero errors**, and neither failure is attributable (both are a pre-existing `main` red, see below) |
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

---

## Findings the controller has to decide about

### 1. B074 filed — a THIRD instance of the `RecursionError` gap, on `assay verify`'s own untrusted-input path

B072's required sweep was widened past the four named modules to all 7
`json.loads`/`json.load` sites in `src/assay`. It was **not clean**:

| site | guard | verdict |
|---|---|---|
| `attestation.py:239` | now includes `RecursionError` | fixed by B072 |
| `adjudication.py:154` | includes `RecursionError` | already fixed by `f0126b35` |
| **`verify.py:2562` (`verify_text`)** | **`json.JSONDecodeError` only** | **B074 — untrusted, confirmed crashing live** |
| `mutation.py:838` | `(UnicodeDecodeError, JSONDecodeError)` | assay's own state record, written by assay in the same run |
| `provenance.py:137` | `json.JSONDecodeError` only | pip-written `direct_url.json`; whole function is best-effort → `None` |
| `verdict.py:501` | none | assay's own shipped package resource |

`verify_text` is the parser behind `assay verify`, a command whose entire
purpose is reading a verdict artifact **produced somewhere else** — untrusted
by definition, from a path or from stdin. Its contract for an unparseable
document is already a returned failure list; a deeply nested one instead
crashes with a traceback, which a CI caller reads as a tooling fault rather
than a bad artifact. Reproduced live.

**Filed, not fixed**, because this wave's binding constraints state
"`assay verify` is unaffected by every item above". B072's acceptance asks for
the sweep result to be *named*, which filing satisfies. **Controller decision
ask: fix it in the next wave, or as a hotfix?** It is the same one-token
change, with a red-first test and one through `cmd_verify`.

`provenance.py:137` was deliberately NOT counted as a third instance, and the
reasoning is recorded in B074 so the next sweep does not re-derive it.

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
would have kept it red. **They are outside the five items' scope**; the
controller may prefer them extracted into their own commit before merge, or
filed retroactively.

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

### Verdict: **GREEN**, at `b12ec9f2`

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

**One aborted first attempt, disclosed.** The first run returned
`NO_MEASUREMENT/DIRTY_TREE (exit 3)` — **my fault, not a product fault**: I
wrote this REPORT and the LOG into `nyxloom-trove/reports/` *while the lane
was running*, and assay correctly refused, naming both files and pointing out
it had observed the tree clean at `b12ec9f2` immediately before starting. The
refusal message was exactly right and the behaviour is the feature working.
The two files were moved out of the tree, the tree re-verified clean at the
same commit, and the gate re-run from scratch — that second run is the green
above. Both documents were written back only after the gate had exited.

The gate's own container was capped to `--cpus=3` immediately after start on
both attempts, and both were launched with no other gate container or
`tester-unified-gate.sh` process running.

---

## What I did NOT do

- No merge, no release, no reviewer dispatch — the controller's next step.
- No `CHANGES.md` edit: the `[Unreleased]` block is deliberately empty, and
  `cmru`'s generator produces the dated entry from the commit range at release
  time. The commit subjects carry the item ids.
- No `write_progress` payload change (B071's explicitly out-of-scope half).
- No `killed`/`survived`/`budget_exceeded` tails.
- No `gate/` addition to the lint scope.
- No fix to `verify.py`'s `RecursionError` gap (B074) or to
  `provenance.py:137`.
