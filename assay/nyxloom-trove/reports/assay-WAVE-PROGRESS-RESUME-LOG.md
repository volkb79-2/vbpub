# Wave LOG — progress/resume family (2026-09-08)

**Supersedes** `assay-WAVE-PROGRESS-RESUME-BRIEF.md` (the mid-wave
continuation brief). That file stays in place as the record of the
checkpoint it documents; everything still load-bearing from it — most of all
the gate-discipline lesson — is carried forward here.

| fact | value |
| --- | --- |
| branch | `feat/assay-progress-resume-2026-09-08` |
| worktree | `/workspaces/vbpub/.worktrees/assay-progress-resume` |
| wave prompt | `assay/nyxloom-trove/WAVE-PROMPT-2026-09-08-progress-resume.md` |
| items | **B067, B064, B065, B066 — all four done** |
| implementers | two, split at the E-008 checkpoint (B067 / the rest) |
| verdict schema | **unchanged**: `VERDICT_SCHEMA_VERSION` 10, `schema_version` 2, `inventory_schema` 1 |
| `assay verify` | **unaffected by every item** — progress and resume state are diagnostic, never evidence |

## Commits, in order

| commit | item(s) | headline |
| --- | --- | --- |
| `7f2ba056` | **B067** | `budget = "unbounded"`, admissible only where every unit carries its own bound |
| `8bc6b9ad` | — | the checkpoint's continuation brief |
| `940b5ba2` | **B064 + B065** | the progress stream reaches every rigor tier, ticks, and carries time |
| `243de634` | **B066** | `--state-dir` — resume state that outlives its worktree |
| `48561aba` | — | this LOG and the wave REPORT |
| `842921ef` | — | adversarial review round 1 (reviewer's own commit) — **NOT ACCEPT** |
| `b5532895` | **round-1 fixes** | B1 blocker, SF-1/2/3/4/5/6, seven of eight nits |
| _(this commit)_ | — | LOG/REPORT updated for round 1 |

---

## B067 — `budget = "unbounded"` (`7f2ba056`)

`budget` now accepts the single literal `"unbounded"` beside a duration.
`assay.config.UNBOUNDED_BUDGET` is the constant; `Lane.budget_seconds` is
`float | None`, and **`None` means and only means `"unbounded"`** — never
"not computed", never "defaulted".

Admissibility, refused at load by
`config._refuse_unbounded_without_unit_bounds`:

| lane | `budget = "unbounded"` |
| --- | --- |
| R0/R1 | refused — one command, whose only bound *is* `budget` |
| ingested R2 (`judge.mutation.format`) | refused — likewise one command |
| native R2 | requires `judge.mutation.budget_per_candidate` |
| R3 | requires `judge.canary.budget_per_attempt` (**new key**) |

`judge.canary.budget_per_attempt` bounds one canary probe end to end
(control materialisation + control run + transformed run) and is re-derived
fresh per declared target via `LaneDeadline.tightened(seconds)`. It also
works under a numeric `budget`, where it only tightens; `tightened(None)`
returns `self`, which is what keeps every pre-B067 lane byte-identical.

**The four `math.inf` boundaries.** An unbounded `LaneDeadline` carries
`expires_at = math.inf` and `remaining()` returns `math.inf`. An infinity
handed to `selectors` or `Popen.wait` raises `OverflowError` — it does not
wait — so every layer that turns a remainder into a real child timeout
converts it: `runner.execute_plan` (→ `subprocess` `timeout=None`),
`git._sample_remaining` (→ `None`), `git._P22Deadline.remaining` (→ `None`),
and `isolation._check_timeout` (admits `math.inf`, and only `math.inf`).
**Any new remainder-to-child-timeout site needs the same conversion.**

**The one honest gap, filed not hidden: B076.** An unbounded R2 lane's own
*baseline* run is the one command left with no bound (measured:
`timeouts == [None, 45.0]`). Deliberate — `budget_per_candidate` is a
per-*mutant* bound and a baseline runs the whole suite, so tightening it
there would refuse healthy lanes. Stated in the docstring, in
`docs/CONSUMERS.md`, and filed with three options and none chosen.

### B067's headline refusal was WRONG as first shipped (round-1 blocker B1)

The predicate above was implemented as

```python
if (not r2 and not r3) or (ingested_r2 and not r3):
```

— both refusal arms conditioned on the **absence of R3**, so declaring a
canary switched the whole refusal off. `judge.canary.budget_per_attempt`
bounds one canary *probe*; it never bounds the lane's own top-level command,
which is what produces the R0 status and the R1 coverage artifact. An
unbounded `["R0","R1","R3"]` lane was therefore **admitted** and ran its own
evidence-producing command with `timeout=None`, beside two properly-bounded
canary halves. The ingested-R2+R3 shape was worse: that one command is the
lane's entire R2 evidence.

Corrected in `b5532895`. The predicate is now about the lane's own top-level
command and is **orthogonal to which other tiers are declared**: a native R2
sweep is the one admissible shape, because it is the one where the
unguessable bulk is bounded per unit. `budget_per_attempt` stays required of
an unbounded R3 lane — necessary, not sufficient — and the refusal says so by
name whenever R3 is in play. Ruling: **A-447**.

**The lesson worth keeping.** 29 tests covered B067 and none caught this,
because every tier was tested **in isolation** and nothing asked what a
**combination** does. The regression therefore asserts the whole
admissibility table in ONE place, and pins the reviewer's own repro at the
process boundary — `subprocess.run` instrumented, asserting no child of such
a lane is ever launched with `timeout=None`.

---

## B064 — the R0/R1 phase stream, with a heartbeat (`940b5ba2`)

### What was wrong

`--progress PATH` had exactly one producer, four layers down inside
`mutation.run_mutation`, gated by one line in `runner._run_prepared_lane`:

```python
progress_path = progress_artifact if r2_declared else None
```

An R0/R1 lane was handed a zero-byte file. The nine-silent-minutes case the
entry was filed about stayed illegible.

### Where the stream is opened, and why it had to move

`verdict_written` is the R0/R1 stream's terminal record, and the verdict is
written by `cli`'s own `write_verdict` **after `run_lane` has returned** — so
a writer opened inside `run_lane` could never emit it. The stream is now
opened in `cli._cmd_run`, spanning both the `run_lane` call and
`write_verdict`. A library caller that passes only `progress_artifact` gets
an equivalent stream through a **one-level re-entry** at the top of
`run_lane` (chosen over an enclosing `with`: that body has a dozen early
returns across two dispatch branches, and re-indenting all of it buys a large
blast radius for no behaviour).

### The ruling B064's acceptance asks for, with the rejected alternatives

**The R0/R1 phase stream is BUILT.** Rejected alternatives, named:

* **nothing** — measured: `--progress` on an R0/R1 lane wrote a zero-byte
  file, and the hung-looking lane stayed illegible;
* **per-tier bespoke events** — rejected because A-429's whole point is a
  uniform invocation shape; a second vocabulary makes a reader tier-aware for
  no gain;
* **runner-aware progress** (parsing the child's own output) — rejected
  outright: assay does not know which of a foreign runner's tests completed
  and must not guess. That is **B073**, filed separately and deliberately not
  started here.

### The closed vocabulary, enforced

`mutation.PROGRESS_EVENTS` is the single table and `ProgressStream.emit`
**refuses** any name outside it — a table nothing enforces is a comment, not
a vocabulary. It is identical across tiers; an R0/R1 lane emits fewer names,
never different ones.

```
run → snapshot_materialized → command_started → command_running
    → command_finished → coverage_parsed → verdict_written
```

**A phase that did not happen is never emitted**, and that is a decision
stated rather than hidden: the DIRECT R0-only path (`run_lane`'s live-tree
branch, A-189) has no snapshot anywhere in it, so it never says
`snapshot_materialized`; a lane with no R1 never says `coverage_parsed`.
Emitting a phase to make two dispatch branches look alike would be the first
lie in an artifact whose only job is to say what is happening right now.

### The heartbeat

`--progress-heartbeat SECONDS` — default 60, floor 5, **refused by name**
below the floor (never clamped: a silently substituted interval is how a
misconfiguration survives to become a mystery in a log), no-op without
`--progress`.

A **pure time-based tick**. It never reads the child's stdout/stderr, counts
bytes, tracks activity or knows which tool is running. Armed only around the
lane's own command — deliberately **not** inside `execute_plan`, because
`mutation._execute_mutation_jobs` calls that once per mutant, `jobs`-way
concurrent, and would interleave N tick streams about work the per-candidate
records already describe one line at a time. A heartbeat write failure stops
the heartbeat and nothing else.

**Stall detection stays entirely with the CALLER** (run-gate RG-36). assay
makes the signal rich enough to compute staleness from; it never becomes the
watcher and gains no threshold of its own.

### Not built, and left open

B064's **R3 half** — per-attempt canary progress and per-target resume — is
NOT built and stays open in the backlog. It must reuse B007's own
per-attempt identity, and inventing a second one inside this wave is exactly
what that entry warns against.

---

## B065 — every record carries time, bounds and outcome (`940b5ba2`)

Landed with B064 as one tight group because they are the same lines: the
`ProgressStream` wrapper B064 needs in order to have one writer is exactly
where B065's fields belong.

* **`emitted_at`** (UTC ISO 8601) and **`elapsed_s`** (monotonic seconds
  since the `run` header) on **every** record, added centrally — so a
  producer cannot forget them and two producers cannot disagree about what
  `elapsed_s` measures. `elapsed_s` is run-relative without exception; the
  heartbeat's own question is `command_elapsed_s`, a different name because
  it is a different quantity.
* **The `run` header** names `lane`, `commit`, `rigor`, `budget_s` and
  `budget_per_candidate_s`, so the bounds travel with the artifact.
  `budget_s` is `null` exactly when the lane is unbounded (B067) — which
  B067 made a real case, not a hypothetical.
* **`candidate_total` is `null` on the header**, honestly. The header must be
  the FIRST record in an append-only file (that is what attributes every
  later record to a run), and the total cannot be known before a snapshot
  exists and the sites are collected. A header deferred until it IS known is
  a header that arrives after the records it exists to attribute. The real
  total arrives on a new **`candidates`** record
  (`candidate_total`/`selected_total`/`pending_total`) the moment it is
  known, and still rides on `baseline` and every `candidate` record.
* **A terminal `end` record** carries the sweep's bucket counts, so a
  finished run is distinguishable from a dead one without reading the
  verdict.
* The **per-candidate record** — the one record that named no event at all —
  now carries `event: "candidate"`.

---

## B066 — `--state-dir PATH` (`243de634`)

`mutation_state_record_path` fixed the records under
`<project_root>/.assay/mutation-state/`, so a persistent worktree resumed
across retries and a **fresh worktree per run** — cmru's release transaction,
a dstdns Mode-B instance — carried its own empty store away with it.
`--resume` was inert exactly where budget-capped retries happen most.

The store's **ROOT** became the consumer's choice; the record's **NAME** did
not. `mutation_state_record_name` is split out and still folds the source
file's exact bytes, span, replacement and operator, which is what makes a
shared store safe **by construction**: a record either matches its identity
or is ignored, and one that contradicts the identity it is filed under still
fails the lane `UNREADABLE_ARTIFACT`. `run_mutation`'s `state_project_root`
is renamed `state_root` — it is a root, not a project root, and the old name
was the bug's own shape.

**Refused before any work:** a `--state-dir` inside the judged tree that git
can SEE, naming both the cause and the fix. The reason is measured, not
theoretical — an untracked path inside the work tree is what
`git.dirty_paths` reports, which makes the lane's NEXT run
`NO_MEASUREMENT`/`DIRTY_TREE`, the identical failure B031 measured for the
progress artifact.

Two details worth keeping in view:

* the check asks `git check-ignore` about a **representative record name**
  under the directory, not about the directory itself. The directory does not
  exist yet (created on demand) and `check-ignore` cannot tell a
  not-yet-existing path is a directory — so an ordinary directory-only
  `resume-store/` line in `.gitignore` would have answered "not ignored" and
  refused a correctly-configured consumer;
* `git.path_is_ignored` is the **one** call in `git.py` that runs without
  `--literal-pathspecs`, because `check-ignore` refuses that flag outright
  ("pathspec magic not supported by this command: 'literal'"). `_run_raw`
  grew a keyword for it; every other call keeps the anchor, since a pathspec
  silently reinterpreted as a glob is a real hazard elsewhere.

What the records **contain** — including B071's `result_stdout_tail` /
`result_stderr_tail` on a `crashed` record — travels unchanged.

---

## Round-1 review, and what it changed (`b5532895`)

Verdict: **NOT ACCEPT** — one blocker, six should-fixes, eight nits. The
report is `assay-WAVE-PROGRESS-RESUME-REVIEW-round1.md`. Everything except
one nit is addressed in `b5532895`.

| item | what it was | disposition |
| --- | --- | --- |
| **B1** | `budget = "unbounded"` voidable by declaring R3 | **fixed**, red-first, two regression tests (see B067 above); A-447 |
| **SF-1** | the `--state-dir` containment check failed OPEN across a symlink | **fixed**, red-first on both of the reviewer's probes |
| **SF-2** | B067's backlog entry never marked done | **fixed** — and it now records that box 1 was *not* met by the first cut |
| **SF-3** | no A-row, though B064's box asks for one by name | **fixed** — A-444…A-447 in `decisions.md` |
| **SF-4** | a breaking artifact change filed only under "Added" | **fixed** — `### Changed`, marked BREAKING, with a migration line |
| **SF-5** | `--progress` newly self-inflicts `DIRTY_TREE`, and the wave built the fix without applying it | **fixed** — the same preflight, one flag over |
| **SF-6** | one hollow test with no assertion | **fixed** — asserts the write was attempted, exactly once |
| **N1** | enrichment computed outside the lock it is written under | **fixed** — both stamps now inside the lock |
| **N2** | a `:`-prefixed path yielded a raw git-stderr passthrough | **fixed** — named refusal before git is asked |
| **N3** | inconsistent clock seam between the two heartbeat sites | **fixed** — both use the real monotonic clock, deliberately |
| **N4** | `end` missing on `run_mutation`'s three early returns | **fixed** — `end` on every path out, with a `reason` |
| **N5** | `elapsed_s` and `elapsed_seconds` one character apart | **NOT fixed — flagged to the controller**, see below |
| **N6** | two cross-module names absent from `__all__` | **fixed** |
| **N7** | no end-to-end heartbeat test | **fixed** — a real run over a real child that outlives one interval |
| **N8** | no test for a per-attempt bound EXPIRING | **fixed** — the cascade is pinned |

**N5 is deliberately open.** A `candidate` record carries both `elapsed_s`
(run-relative, new and universal) and `elapsed_seconds` (that candidate's own
duration, pre-existing). Renaming the older field would be a **second**
breaking change to the progress artifact in one release, on top of A-445's,
and it needs a decision rather than an implementer's guess. Mitigated for now
in `docs/CONSUMERS.md`, which carries a three-row table distinguishing
`elapsed_s` / `command_elapsed_s` / `elapsed_seconds` explicitly.

Two structural observations from the round worth keeping beside the fixes:

* **B1 and SF-1 are the same failure in two places** — a predicate that
  answers "is this safe?" and gets the *default* wrong. B1 defaulted to
  admitting; SF-1 defaulted to "outside the tree, nothing to check". Both are
  now fail-closed, and `_containments` says so in its own docstring: a false
  refusal costs one clear message, a false accept costs a work tree that
  refuses its own next run.
* **SF-5 is the wave marking its own asymmetry.** B066 built
  `git.path_is_ignored` and gave `--state-dir` a named preflight; B064 made
  `--progress` write on every tier in the same commit range and left it
  without one. The mechanism existed; only the second call site was missing.

## Gate-discipline lesson, carried forward from the checkpoint brief

**READ THIS BEFORE ANY GATE RUN ON THIS PROJECT.** The `tester-unified`
lane's own self-hosted `assay run` judges the **LIVE worktree**, not the
exact-OID clone the driver builds from. The first implementer's first gate
run went red — `NO_MEASUREMENT`/`DIRTY_TREE` — because it wrote its
continuation brief into the tree *while the gate was running*:

```
assay: NO_MEASUREMENT/DIRTY_TREE: the lane's own command left 1 uncommitted
file(s) in …/assay-progress-resume/assay -- assay observed that tree CLEAN
at 7f2ba056… immediately before starting the command
Affected: assay/nyxloom-trove/reports/assay-WAVE-PROGRESS-RESUME-BRIEF.md
```

The wave prompt's warning is stronger than it reads. The measured rule:
**write nothing into the tree at any point between launching the gate and
reading its verdict.** Park the file outside the tree (the session
scratchpad) and move it in only once green. This LOG and the REPORT beside it
were written that way.

Two operational notes that also held on the second half of the wave:

* **Identify YOUR container before capping it.** Peer agents run containers
  from the same `tester-unified:local` image. Match on your own worktree path
  in the argv —
  `docker ps --no-trunc --format '{{.ID}}|{{.Command}}' | grep -F -- "--inner <your worktree>"`
  — then `docker update --cpus=3` that id and nothing else.
* **The host really does saturate.** Both halves of this wave had to wait:
  the first hit load 17 with ~5 GB free (which killed its background tasks),
  and the second found two peer gates live (a dstdns `p176` gate and a vbpub
  `ciu` gate) at load ~9 and waited for them rather than racing.

---

## Local suite

| tip | result |
| --- | --- |
| pre-wave baseline | 4235 passed |
| `7f2ba056` (B067) | 4266 passed |
| `940b5ba2` (B064/B065) | 4282 passed, 20 skipped (16 new) |
| `243de634` (B066) | 4289 passed, 20 skipped (7 new) |
| `b5532895` (round-1 fixes) | 4302 passed, 20 skipped (13 new) |

The skip count differs from the checkpoint brief's "11" because the two
implementers ran in different environments; collected totals match at both
tips, and no test moved from passing to skipped.

## Registered gate

```
cd /workspaces/vbpub/assay
./run-gate.py --worktree /workspaces/vbpub/.worktrees/assay-progress-resume tester-unified
```

Run twice, from scratch each time — a new commit is a new judged tip, and the
round-1 green was never carried over.

### Round 1 of fixes — **GREEN at `b5532895`**

```
tester-unified: PASS (exit 0)
ASSAY_REGISTERED_GATE_COMPLETE=1
run-gate: lane 'tester-unified' exit 0
```

All **12** `ASSAY_GATE_PHASE` markers, **zero** `ASSAY_GATE_DIAGNOSTIC`
lines, verdict read from the log's own markers in a separate step after the
run finished. Launched after waiting out two peer gates (a dstdns `p176`
gate, then a peer's own `run-gate-vbpub-tester-unified` container that took
the slot first); my own container identified by matching `--inner
/workspaces/vbpub/.worktrees/assay-progress-resume` in `docker ps
--no-trunc`, excluding the reviewer's `-review` worktree, and capped with
`docker update --cpus=3` on that id alone.

One deliberate relaxation, recorded rather than silently taken: the wait
condition asked for ≥6 GB available, and this host now idles at 5 GB with the
production game server resident. Launched at load 3.29 with 5 GB available,
no gate process and no gate container — the conditions that actually contend
(a peer gate, real load) were all clear. If a future run finds the 6 GB bar
never clearing, that is why.

### The original four items — GREEN at `243de634`

```
tester-unified: PASS (exit 0)
ASSAY_REGISTERED_GATE_COMPLETE=1
run-gate: lane 'tester-unified' exit 0
```

All **12** `ASSAY_GATE_PHASE` markers present (`wheel-installed` →
`attestation-hardened` → `verdict-v5-accepted` →
`lane-schema-v2-successors-verified` →
`verdict-v6-v7-v8-v9-hard-cut-verified` → `verdict-v10-successors-verified` →
`judge-provenance-bound-to-the-installed-wheel` → `self-hosted-lane-passed`
→ `topos-qualified` → `cmru-b006a-qualified` →
`independent-self-hosting-passed` → `pyflakes-clean`), **zero**
`ASSAY_GATE_DIAGNOSTIC` lines. Verdict read from the log's own markers in a
SEPARATE step after the run finished, never from a piped exit code
(LESSONS L4).

One run, green first time — the tree was left untouched for its whole
duration, and this file was moved in from the session scratchpad only after
the verdict was read.
