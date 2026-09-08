# Adversarial review round 1 — progress/resume wave (B067/B064/B065/B066)

**Reviewer:** fresh independent session, no relationship to either implementer.
**Branch reviewed:** `feat/assay-progress-resume-2026-09-08`, tip `48561aba`, six
commits not on `main`.
**Review worktree:** `/workspaces/vbpub/.worktrees/assay-progress-resume-review`
(the implementers' own worktree was not touched).

## Verdict: **NOT ACCEPT**

One blocker: **B067's headline refusal is voidable by declaring R3**, and I
reproduced a lane whose own evidence-producing command runs with
`timeout=None` under `budget = "unbounded"` — precisely the state the item
exists to refuse. B067's first acceptance box is therefore not met, the REPORT
marks it ✅, and `docs/CONSUMERS.md`'s shipped table states the opposite of what
the code does.

Everything else in the wave holds up well. I re-derived every load-bearing
claim rather than reading it off the LOG, and B064/B065/B066 are all genuinely
built and genuinely measured; the four `math.inf` boundaries are real and
complete; the two-worktree resume acceptance reproduces independently; the
closed vocabulary is enforced, not documented. The remaining items below are
should-fixes and nits.

**Registered gate (my own run, not the implementers'):** see
[§ Gate](#gate-my-own-run) at the end.

---

## BLOCKER

### B1 — `budget = "unbounded"` is admitted on a lane whose only command is unbounded, whenever R3 is declared

`assay/src/assay/config.py:1801`

```python
if (not r2 and not r3) or (ingested_r2 and not r3):
```

Both refusal arms are conditioned on the *absence* of R3, so declaring an R3
canary switches them off. But `judge.canary.budget_per_attempt` bounds only the
canary probes — it does not bound the lane's own top-level command, which is
what produces the R0 status and the R1 coverage artifact. The result is exactly
the state the refusal's own message describes as impossible.

**Reproduced, end to end, through the real CLI.** A lane with
`rigor = ["R0", "R1", "R3"]`, `budget = "unbounded"`,
`judge.canary.budget_per_attempt = "30s"` loads without complaint, and with
`subprocess.run` instrumented at the process boundary:

```
LANE ACCEPTED with budget=unbounded on an R0/R1+R3 lane
   child timeout=None                 argv=['/bin/sh','-c','cp cov-fixture.json coverage.json']   <- the LANE's own command
   child timeout=29.970889588999853   argv=[...]   <- canary control half
   child timeout=29.778693405001830   argv=[...]   <- canary transformed half
```

The identical lane with `R3` removed is refused by name:

> `budget = 'unbounded' is refused on this lane: an R0/R1 lane is ONE command,
> so it has no per-unit bound to require and 'budget' is its only liveness
> bound; declaring 'unbounded' would leave it with none at all.`

The same hole swallows the ingested-R2 arm. Load-time results from a direct
`load_lane_file` probe over five shapes:

| lane | `budget = "unbounded"` | expected |
| --- | --- | --- |
| `["R0","R1"]` | REFUSED ("an R0/R1 lane is ONE command") | refused ✓ |
| `["R0","R1","R3"]` + `budget_per_attempt` | **ADMITTED**, `budget_seconds=None` | should be refused |
| `["R0","R1","R2"]` ingested | REFUSED ("an ingested R2 lane is ONE command") | refused ✓ |
| `["R0","R1","R2","R3"]` ingested + `budget_per_attempt` | **ADMITTED**, `budget_seconds=None` | should be refused |
| `["R0","R1","R2"]` native + `budget_per_candidate` | ADMITTED | admitted ✓ |

The ingested case is the worse of the two: an ingested R2 lane's *entire* R2
evidence comes from that one unbounded command.

This is **not** B076. B076 is about a native-R2 lane's baseline, where every
mutant still carries `budget_per_candidate` and the sweep — the part whose
length genuinely cannot be guessed — is bounded per unit. Here the lane's only
evidence-producing work is unbounded and nothing else in the lane compensates.

**Consequences beyond the code:**

* `assay/nyxloom-trove/4-backlog.md` B067 acceptance box 1 ("`unbounded` with a
  missing unit bound refuses at load naming the unit") is **not met**.
* `assay/nyxloom-trove/reports/assay-WAVE-PROGRESS-RESUME-REPORT.md:28` marks
  that box ✅.
* `assay/docs/CONSUMERS.md` (the `budget = "unbounded"` table) tells a lane
  author that R0/R1 and ingested R2 are "**refused**", unconditionally. They
  are not.
* `assay/CHANGES.md` repeats the same unconditional claim.

`tests/test_config_unbounded_budget.py` has no case for either combination, so
nothing catches it.

I am not proposing the fix (that is the implementer's call — the "one command"
predicate is orthogonal to R2/R3 presence and should probably be evaluated on
its own), but the docs, the CHANGES entry, the REPORT tick and the backlog box
all have to move together with whatever lands.

---

## SHOULD-FIX

### SF-1 — `--state-dir`'s inside-the-tree check fails OPEN across a symlink

`assay/src/assay/cli.py:_resolve_state_dir` (the `resolved.relative_to(root)`
block)

Two path namespaces are compared:

* `resolved` comes from `output.resolve_state_directory` →
  `output._normalized_absolute`, which is **lexical by design** (`os.path.normpath`,
  documented as "resolving them against the filesystem would follow symlinks,
  which the descriptor walk below exists to refuse");
* `root = project_root.resolve()`, which **does** resolve symlinks.

When the two disagree, `relative_to` raises and the path is silently classified
"Outside the judged tree: git never sees it, nothing to check."

Reproduced twice, with a real repo and the real CLI:

```
PROBE C: project root reached via a symlink (link -> proj), --state-dir <link>/inside-via-link
  refusal seen: False
  records written INSIDE the real work tree: 1
  git status --porcelain: '?? inside-via-link/'

PROBE D: intermediate symlink component (outside/hop -> proj), --state-dir outside/hop/sneaky
  refused: False
  records inside the real tree: 1
  git status --porcelain: '?? sneaky/'
```

In both cases the work tree is left dirty by untracked resume records — which
is exactly the `NO_MEASUREMENT`/`DIRTY_TREE` on the lane's next run that B066's
second acceptance box exists to prevent. `validate_progress_destination` shares
the lexical-path philosophy but never had to answer an inside/outside question,
so this hazard is new with B066. This estate bind-mounts and worktrees heavily,
so it is not an exotic shape.

### SF-2 — B067's backlog entry was never marked done

`assay/nyxloom-trove/4-backlog.md:6594-6601`

All three of B067's acceptance boxes are still `[ ]` and the entry carries no
`**IMPLEMENTED …**` / `### Resolution` note, while B064, B065 and B066 each got
both. B067 is the one item the *first* implementer did before the checkpoint,
and the successor did not go back for it. Meanwhile the REPORT states
"**B067 — `budget = "unbounded"` — ACCEPTED, 3/3**". Whatever the outcome of B1,
the backlog is currently silent about the largest grammar change in the wave.

### SF-3 — no A-row was recorded, and B064's box asks for one by name

`assay/nyxloom-trove/decisions.md` is untouched by this branch (460 rows,
unchanged; last row A-443).

* B064's acceptance box 1 reads "a ruling recorded **as an A-row** on whether
  the R0/R1 phase stream is built". It is ticked `[x]`, with the evidence given
  as "LOG § B064 and the backlog entry" — neither of which is an A-row.
* The REPORT's restatement of that same box (line 45) silently **drops the
  phrase "as an A-row"**. That is the acceptance text being weakened in the
  document that reports on it.
* The project convention supports the box: every recent `feat(assay)` commit on
  `main` carries one (A-437 … A-442); only `fix(assay)` commits do not. This
  wave has **three** `feat(assay)` commits introducing a new `budget` literal, a
  new lane key (`judge.canary.budget_per_attempt`), two new CLI flags and a new
  closed public artifact vocabulary — and no A-row.

### SF-4 — a breaking progress-artifact change is filed only under "Added"

`assay/CHANGES.md` (the `[Unreleased]` block)

The `run` header's `candidate_total` used to be the sweep's real total (emitted
by `run_mutation`); it is now **always `null`**, with the value moved to a new
`candidates` record. That is a breaking change for any existing reader of the
progress file, and the reasoning for it (the header must come first in an
append-only file) is sound — but it appears only inside a "### Added" bullet,
with no "Changed"/"Breaking" heading and no migration line. Verified on a real
run: `header candidate_total=None`.

(For the record, I checked the one real consumer: `run-gate.py`'s `_newest()`
keys on an integer `candidate_index`, which is unchanged and still present, and
run-gate never parses assay's own `budget`. So nothing downstream breaks today
— but the artifact is a documented public contract.)

### SF-5 — B064 newly turns a previously-passing R0/R1 invocation red, and the wave built the fix without applying it

Before this branch, `--progress <path inside the work tree>` on an R0/R1 lane
wrote **nothing** — the `r2_declared` gate saw to that — so the invocation
passed. After it, the file is created, and the lane refuses itself on its very
**first** run. Reproduced on a fresh repo whose `.assay/` is not git-ignored:

```
assay run unit --file … --progress <repo>/.assay/progress-unit.jsonl
  unit: NO_MEASUREMENT/DIRTY_TREE (exit 3)
  progress file created inside a NON-ignored .assay/: True
  git status --porcelain: '?? .assay/'
  (second run: exit 3 as well)
```

That is a real regression for any consumer that passes `--progress` at a
non-ignored path and only runs R0/R1 lanes — which was harmless before and is
now an immediate red. The estate's own convention (run-gate RG-33/RG-13's
gitignore obligation) keeps `.assay/` ignored, so this should not bite here,
but it is a behaviour change for existing callers that CHANGES.md does not
mention.

The sharper point is the asymmetry the wave created in one commit range: B066
gave `--state-dir` a git-visibility preflight that refuses **before any work**,
naming both the cause and the fix, using a new `git.path_is_ignored` helper —
and `--progress`, which the *same wave* just made write on every tier, still
has none, so the operator gets a bare `NO_MEASUREMENT/DIRTY_TREE` where the
sibling flag would have given a named, actionable refusal. The mechanism was
built here; extending it one flag over (or at minimum a CONSUMERS/CHANGES
migration note) belongs with this wave.

### SF-6 — one genuinely hollow test

`assay/tests/test_progress_phase_stream.py:255-267`
`test_a_heartbeat_write_failure_stops_the_heartbeat_and_nothing_else`

The body has **no assertion**; the comment says "Reaching here at all is the
assertion". It would pass identically if `_command_heartbeat` never started a
thread, never called `explode`, or were a bare `yield`. It should assert that
`explode` was actually invoked at least once (proving a write was attempted and
swallowed) and that no further calls follow.

That is the only hollow test I found. I read all 45 new test functions; the
rest are substantive — real git repositories, real worktrees, real CLI
invocations, real child processes, and assertions that fail against the
unfixed code (e.g. `test_the_direct_r0_path_never_claims_a_snapshot_it_did_not_take`
would fail on an empty pre-B064 file because of its `{"direct"}` phase-set
assertion). No mocked collaborator stands in for something that should be a
real call; `_Gate`/`record` process runners are legitimate boundary recorders
at the documented `ProcessRunner` seam, and every one of them is paired with a
real-subprocess test elsewhere in the same file.

---

## NITS

* **N1 — enrichment is computed outside the lock it is written under.**
  `mutation.ProgressStream.emit` builds `emitted_at`/`elapsed_s` before
  acquiring `self._lock`, so with the heartbeat thread plus a `jobs`-way
  concurrent sweep two records can be *written* in a different order than their
  timestamps. `tests/test_environment_preflight.py` now asserts non-decreasing
  `elapsed_s` across consecutive records — a latent flake — and run-gate's
  `_rate_per_min` reads that field off the newest candidate record. Move the
  two computations inside the lock.
* **N2 — a `:`-prefixed `--state-dir` yields a raw git-stderr passthrough.**
  Because `path_is_ignored` runs without `--literal-pathspecs`, git parses a
  leading `:` as pathspec magic:
  `assay: ERROR/GIT_FAILED: git check-ignore :(icase)…/0000….json failed (128): fatal: … pathspec magic not supported`
  — the exact shape B068 was fixed to stop emitting. Low reach, but a named
  refusal would be cheap.
  **Both of the implementer's disclosed claims here are TRUE and I reproduced
  them** (git 2.55.0): `git --literal-pathspecs check-ignore -q -- <path>` exits
  128 with `pathspec magic not supported by this command: 'literal'`, and
  `:(literal)<path>` is refused the same way — so dropping the flag for this one
  call really was the only route. I also probed wildcard names (`we*rd`,
  `ignored-stor?`) and found **no fail-open**: the observed failure mode is a
  fail-*closed* refusal, and the "representative record name" probe genuinely
  is what makes a directory-only `resume-store/` `.gitignore` line work
  (verified: accepted, records written, `git status` clean).
* **N3 — inconsistent clock seam.** `_execute_snapshot_unit` calls
  `_command_heartbeat(...)` without `monotonic=`, so the baseline heartbeat uses
  the real `time.monotonic` while the direct-R0 path passes the injected
  `monotonic`. Harmless in production, but the two call sites should agree.
* **N4 — the `end` record's own gap.** `run_mutation`'s three early returns
  (`UNSUPPORTED`, over the candidate cap, zero candidates) emit no `end`. Under
  the lane path `verdict_written` covers it; for a **direct library caller**
  with only `progress_artifact` there is no `verdict_written`, so those runs
  remain indistinguishable from a dead process — the exact question `end` was
  added to answer. Documented in the code comment, but worth closing or naming
  in CONSUMERS.
* **N5 — two near-identical field names on one record.** A `candidate` record
  now carries both `elapsed_s` (run-relative, new and universal) and
  `elapsed_seconds` (that candidate's own duration, pre-existing). Both are
  documented in CONSUMERS' table, but they are one character apart with
  different meanings on the same line of JSON. Confirmed on a real run's record
  keys.
* **N6 — export hygiene.** `mutation_state_record_name` and
  `default_state_root` are cross-module callers' API (`runner.py` uses the
  latter) but are absent from `mutation.__all__`, which still lists only
  `mutation_state_record_path`.
* **N7 — the heartbeat's wiring has no end-to-end test.** Every heartbeat test
  drives `runner._command_heartbeat` directly or tests a CLI refusal; none
  asserts a `command_running` record in a real `--progress` run. I verified the
  wiring myself (below) and it works, so this is a coverage gap, not a defect.
* **N8 — no test for a per-attempt bound actually EXPIRING.**
  `test_every_probe_runs_under_the_declared_per_attempt_bound` proves the bound
  is *handed to* the boundary, but the `budget_exhausted` cascade that
  `canary.py`'s new comment reasons about at length (an expired per-attempt
  deadline marking this probe *and every later one* `budget_exhausted`, chosen
  to keep `verify.py`'s R-2/SF-1 trailing-run rule intact) is not exercised.
  That is a real behaviour change worth pinning: one slow target now abandons
  every subsequent target even when the lane has hours left.

---

## What I independently verified as CORRECT

Everything here was re-derived in my own worktree, not taken from the LOG.

**Wave-wide invariants.** `VERDICT_SCHEMA_VERSION` is 10 (`verdict.py:287`),
`assay.toml`'s `schema_version` is 2, `LANE_INVENTORY_SCHEMA_VERSION` is 1
(`cli.py:1514`), and `git diff --stat main...HEAD -- src/assay/verify.py
src/assay/verdict.py` is **empty** — both files are untouched. `assay verify`
returns 0 on an unbounded lane's verdict and on a `--state-dir` run's verdict,
and neither the verdict nor `assay lanes --json` names a progress file, a state
directory, or a budget in seconds.

**B067 — the four `math.inf` boundaries.** All four are real and I traced an
unbounded lane through each:

| boundary | conversion | verified |
| --- | --- | --- |
| `runner.execute_plan` | `child_timeout = None if timeout == math.inf else timeout` | ✓ |
| `git._sample_remaining` | `sampled == math.inf → None` | ✓ |
| `git._P22Deadline.remaining` | `self._expiry == math.inf → None` | ✓ |
| `isolation._check_timeout` | admits `math.inf` and only `math.inf`, then hands it to `_P22Deadline` | ✓ (all four call sites at `isolation.py:451/478/490/1669` construct a `_P22Deadline`) |

I swept for a **fifth** unconverted site and found none: `git.py:1186`
(`selector.select`) and `git.py:1224` (`proc.wait`) both consume
`_P22Deadline.remaining`; `git.py:315-320` already branches on
`_sample_remaining(...) is None → proc.wait()`; `canary.py:528/560/604` all
enter `isolation`; `attestation.py`/`adjudication.py` call `remaining()` only
for its expiry side effect. `runner.py:4934`'s environment probe computes
`min(PROBE_BUDGET_SECONDS, deadline.remaining())`, which stays **finite** under
an unbounded lane — the probe is not accidentally unbounded.

Then I ran a **real** unbounded R2 lane end to end — real git repo, real P22
snapshots, real `/bin/sh` children, no stubbed `ProcessRunner` (the
implementer's own box-2 test stubs it, so the real `subprocess.run(timeout=None)`
path was not covered by it):

```
exit=0   R2 status=PASS  total=1  killed=1
events: run, snapshot_materialized, command_started, command_finished,
        candidates, baseline, candidate, end, verdict_written
header budget_s=None  budget_per_candidate_s=45.0
assay verify -> 0     assay plan -> 0     assay lanes -> "budget=unbounded"
inventory_schema: 1   inventory budget: "unbounded"
```

**B076's reasoning is sound as far as it goes.** `budget_per_candidate` really
is a per-mutant bound and a baseline really does run the whole suite, so
tightening one to the other would refuse healthy lanes; the three options are
fairly stated and leaving it open with the caller watching is defensible given
the settled "assay does not watch itself" ruling. My objection is not to B076
— it is that the *same* reasoning, applied one step further, exposes B1, which
the wave did not notice.

**B064 — the stream genuinely reaches R0/R1.** Real CLI runs in a real repo:

```
R0-only lane, --progress-heartbeat 5, command = `sleep 12; exit 0`
  events: run, command_started, command_running, command_running,
          command_finished, verdict_written
  snapshot_materialized present: False      coverage_parsed present: False
  phases: ['direct']                        git status after run: clean
  tick: {"event":"command_running","phase":"direct",
         "command_elapsed_s":5.001,"elapsed_s":5.02,"emitted_at":"…"}

R0+R1 lane
  events: run, snapshot_materialized, command_started, command_finished,
          coverage_parsed, verdict_written
  phases: ['baseline']
  coverage_parsed: {"parsed": true, "reason_code": null, …}
```

So: the direct R0 path emits **no** `snapshot_materialized` (there is no
snapshot in that branch) and a lane with no R1 emits **no** `coverage_parsed`,
while the higher-rigor path emits both — reproduced against real lanes of each
shape, not inherited.

**The heartbeat is what it claims to be.** Two real ticks at a 5 s interval
over a 12 s command, cancelled with it; `command_elapsed_s` is command-relative
and `elapsed_s` run-relative, so the named collision is genuinely resolved.
Nothing in `_command_heartbeat` reads the child's streams — it takes a
`ProgressStream`, an interval and a phase name, and nothing else. It is armed
at exactly two sites, both the lane's own top-level command
(`runner._execute_snapshot_unit` and `run_lane`'s direct branch); the mutation
sweep executes mutants through `_execute_mutation_jobs`'s injected
`execute_plan`, which has no heartbeat, so the "would flood on N concurrent
candidates" hazard is structurally closed, not merely avoided. Default 60.0 and
floor 5.0 (`runner.PROGRESS_HEARTBEAT_{DEFAULT,FLOOR}_SECONDS`); a sub-floor
value is **refused by name** before the destination is opened, never clamped;
and the flag is a no-op without `--progress` (verified: exit 0, no file, clean
tree).

**The closed vocabulary is enforced, not documented.** I tried five unlisted
names through `ProgressStream.emit` myself — `'tests_are_going_well'`, `''`,
`None`, `'RUN'` (wrong case), `'candidate '` (trailing space). All five raised
`ValueError: progress event … is not in the closed progress vocabulary`, and
the recorder received **0** records.

**B065.** `emitted_at` and `elapsed_s` are on every record on every real run I
made (added centrally in `ProgressStream.emit`, so no producer can omit them).
The two deliberate `null`s are real and reasoned: `budget_s` is `null` exactly
on an unbounded lane (observed `budget_s=None, budget_per_candidate_s=45.0`),
and `candidate_total` is `null` on the header with the value arriving on a
later `candidates` record (observed `candidate_total=None` on the header,
`candidate_total/selected_total/pending_total` on `candidates`). The header
ordering argument holds — the file is opened for append and the header is what
attributes later records to a run, so it cannot wait for a snapshot. The
terminal `end` record's buckets agree **field for field** with the same run's
own verdict:

```
end buckets:     {'budget_exceeded':0,'crashed':0,'equivalent':0,'killed':0,'survived':1}
verdict buckets: {'budget_exceeded':0,'crashed':0,'equivalent':0,'killed':0,'survived':1}
AGREE: True
```

**B066 — the two-worktree acceptance, run independently.** Same commit, two
real worktrees (second `--detach`), one shared `--state-dir`:

```
run1 (worktree A): 1 record written, no `resume` event, pending_total=1
run2 (worktree B): event: resume, resumed_total=1, pending_total=0
                   worktree B's own .assay/mutation-state/ was never created
run3 (after a real committed source edit in B):
                   pending_total=2, store grew 1 -> 3, old ids preserved
```

Validation order is correct: `_resolve_state_dir` runs inside `_cmd_run` before
`validate_progress_destination` and before `_run_reserved` starts any work. A
non-ignored directory inside the tree refuses (`exit 2`, message names
`DIRTY_TREE` and the path) **and the directory is not created**. A
directory-only `.gitignore` entry (`resume-store/`) is accepted, records are
written, and `git status --porcelain` is empty afterwards — which is the
concrete payoff of the "representative record name" probe. An existing
non-directory refuses. The default location is unchanged
(`mutation.default_state_root(project_root)` composes exactly
`<project_root>/.assay/mutation-state`).

**B064's R3 half is honestly left open.** Box 3 in `4-backlog.md` is still
`[ ]`, the entry says in its own words "**R3's half is NOT built** and stays
open above", the entry carries no `### Resolution` heading, and the REPORT
marks the box ⬜ **NOT BUILT** with a reason. The code matches: `canary.py`
passes no `progress` into `_execute_snapshot_unit`, so canary probes emit
nothing. This is the wave's most honest piece of bookkeeping — and the contrast
with SF-2 (B067's entry, not updated at all) is what makes SF-2 stand out.

**The disclosed process deviation — the result is correct.** The two mechanical
multi-site edits are the `state_project_root=` → `state_root=` rename (10 call
sites across four test files) and the
`state_root / ".assay" / "mutation-state"` → `state_root` store-path change (4
sites). I checked for exactly the naive-find-replace damage this could have
caused and found none:

* no `state_project_root` survives in any `.py` file — the only remaining
  occurrences are three deliberate historical references in prose
  (`mutation.py:1536` docstring, the backlog entry, the LOG);
* `mutation_state_record_path` was **not** collaterally renamed and still
  returns the project-relative default spelling
  (`.assay/mutation-state/<id>.json`), which `merge_mutation_shards` still uses
  as a normalising dedup key (`mutation.py:1249`, no filesystem access — safe);
* every surviving `.assay/mutation-state` literal is intentional
  (`mutation.py:976`/`981`, plus docstrings, help text and CONSUMERS);
* each converted test site was individually correct against the new
  `state_root` semantics — I ran all six affected files plus the three new ones:
  **106 passed**.

One cosmetic residue: `tests/test_mutation_state_crash_tails.py:81`'s helper is
now `directory = state_root` behind a two-line comment, and
`tests/test_mutation_progress_budget_plan.py:333`'s docstring still describes
the old `.assay/mutation-state/` location. Neither is wrong; both are the kind
of thing a hand edit would have tidied.

**Cross-tool compatibility (not asked for, checked anyway).** `run-gate.py`
rev 36's `_newest()` selects the last record with an integer `candidate_index`
— still present and unchanged on the per-candidate record, so adding
`event: "candidate"` is purely additive there. `_rate_per_min` already prefers
`elapsed_s` (RG-36/R-40 was written in anticipation of B065), which this wave
now supplies. run-gate never parses an assay lane's `budget`, so
`budget = "unbounded"` cannot reach its duration grammar. Nothing downstream
breaks.

---

## Gate (my own run)

Run independently in my own worktree, from the main checkout's `run-gate.py`,
after checking `docker ps` and `pgrep -af tester-unified-gate.sh` (no peer gate
live; load average 3.5). My own container was identified by matching
`--inner /workspaces/vbpub/.worktrees/assay-progress-resume-review` in
`docker ps --no-trunc` and capped with `docker update --cpus=3` on that id and
no other. Nothing was written into the review worktree while the gate ran; this
file was authored in the session scratchpad and moved in only after the verdict
was read from the log's own markers, in a separate step (LESSONS L4).

```
cd /workspaces/vbpub/assay
./run-gate.py --worktree /workspaces/vbpub/.worktrees/assay-progress-resume-review tester-unified
```

**GREEN at `48561aba`**, read from the log's own markers after the run
finished:

```
tester-unified: PASS (exit 0)
ASSAY_REGISTERED_GATE_COMPLETE=1
run-gate: lane 'tester-unified' exit 0
  commit: 48561abab3ff47037197e5cf867e584487d55dd7   (self-hosted lane)
```

All **12** distinct `ASSAY_GATE_PHASE` markers present (`wheel-installed`,
`attestation-hardened`, `verdict-v5-accepted`,
`lane-schema-v2-successors-verified`, `verdict-v6-v7-v8-v9-hard-cut-verified`,
`verdict-v10-successors-verified`,
`judge-provenance-bound-to-the-installed-wheel`, `self-hosted-lane-passed`,
`topos-qualified`, `cmru-b006a-qualified`, `independent-self-hosting-passed`,
`pyflakes-clean`), and **zero** `ASSAY_GATE_DIAGNOSTIC` lines. So the
implementers' green-gate claim reproduces independently.

I also ran the six affected test files plus the three new ones directly
(`nice -n 19 ionice -c 3`): **106 passed**.

**Branch drift during the review.** The prompt named tip `48561aba`; while I
was reviewing, the branch advanced to `9ffd56c7`
(`docs(assay): Wave 2 controller log — PR-R3 …`), a **docs-only** commit adding
27 lines to the wave's controller log. No source, test, backlog or CHANGES file
changed, so every finding above applies unchanged to the current tip. My gate
run judged `48561aba`.

The gate result does not change the verdict. B1 is a correctness hole the suite
has no case for; a green gate is exactly what you would expect.
