# assay Wave D (v10) — BRIEF-3 (generation 3 → generation 4)

Written at generation 3's E-008 checkpoint. **Cumulative delta since BRIEF-2
only.** BRIEF-1 is the seam map for everything it settled; BRIEF-2 is the
delta up to `c80b3452`. Read them in that order, then this.

**The headline: PHASE 1 IS COMPLETE, 10 of 10, and the gate is GREEN on the
phase-1 tip `93188912`.** Generation 4's job is phase 2 — the design step
first, then the single `feat(assay)!:` cut.

---

## 1. Where the branch stands

- Worktree `/workspaces/vbpub/.worktrees/assay-wave-d-v10`, branch
  `feature/assay-wave-d-v10`, forked from `main` at `a4a865da`.
- **Phase 1: 10 of 10 DONE.** Nothing under `verdict.py`, `verify.py`,
  `src/assay/schemas/` or the drift-guard carve-assets has been modified on
  this branch, and no commit carries `!` — the branch is releasable on v9
  exactly as the wave prompt requires, and both v9 schema gate phases passed.

| # | item | ruling | status |
|---|---|---|---|
| 1 | B049 | DA-D1 | DONE (gen 1) — `3b2b8e62`, A-408 |
| 2 | B054 | DA-D3 + DA-R2 | DONE (gen 2) — `c37ca3fb`, A-410 |
| 3 | B053 (a)+(b) | DA-D2 + DA-R1 | DONE (gen 2) — `440d5da9`, A-409 |
| 3b | **B053 follow-ups** | **DA-R3 + DA-R4** | **DONE — `21bdf19d`, A-414** |
| 4 | **B028** | DA-D10 | **DONE — `dd8f4d2c`, A-415** |
| 5 | **B029** | DA-D11 → DA-R6 | **DONE (resolved by measurement) — `81228b25`, A-416** |
| 6 | B060 | DA-D14 | DONE (gen 2) — `c80b3452`, A-411 |
| 7 | B056 | DA-D13 | DONE (gen 2) — `c80b3452`, A-412 |
| 8 | **B024** | DA-D15 | **BLOCKED, escape hatch taken — `93188912`, NOTHING LANDED, decision ask in the REPORT** |
| 9 | B055 | DA-D12 | DONE (gen 2) — `c80b3452`, A-413 |
| 10 | B009 | DA-D16 | DONE (gen 2) — `c80b3452` |

## 2. What generation 3 landed, one line each (details: LOG entries 8-11, REPORT)

- **`21bdf19d` — B053 follow-ups, A-414 (DA-R3 + DA-R4).** Six refusal sites
  that refused from a bare `(status, reason_code)` literal now compose their
  message where the fact is known and go through the same
  `announce_refusal`: `runner.py:3792` (`DIRTY_TREE`, snapshot path), `:3806`
  (`HEAD_CHANGED`), `:4056` (`MISSING_EXTERNAL_TOOL`), `:4219`
  (`env_required`), `:4284` (bad `--shard`), `:4369` (`DIRTY_TREE`, direct
  R0). And the one refusal the verdict can DISCARD — the `equivalence_artifact`
  early-R2 refusal — is now recorded at `:2855` and announced only at
  `:3194-3200`, where the surviving claim is chosen. 8 new tests; red-first
  7F/10P.
- **`dd8f4d2c` — B028, A-415 (DA-D10).** One outer catch for direct R0
  (`runner.py:4465-4551`), with the post-command guard extracted verbatim
  into `_finish_direct_r0_lane` (`:4552`); the higher-rigor entry point was
  MEASURED already correct and left alone.
  `_replace_highest_higher_rigor_claim_with_git_failed` (`:3609`) gained
  `status`/`reason_code` parameters so a cleanup that failed because time ran
  out is not relabelled `GIT_FAILED` (`:3916`). 7 tests; red-first 3F/4P.
- **`81228b25` — B029, A-416 (DA-D11 → DA-R6).** MEASURED FIRST, and the
  predicted defect does not reproduce on the shipped path. Threading landed
  anyway on the legacy path it was confined to; docstring corrected;
  regression guard landed and labelled as one. 2 tests.
- **`93188912` — B024, DA-D15's escape hatch.** Measured, nothing landed,
  decision ask written.

## 3. B024 is the one open decision ask, and it is generation 4's to relay, not to answer

Three checks, all run (transcripts in the backlog entry and the REPORT):

1. `tester-unified:local` carries neither `pyflakes` nor `ruff`.
2. `gate/distribution/build-wheelhouse/` holds exactly five hash-pinned build
   wheels and no linter.
3. `tools/tester-unified-gate.sh` installs `--no-index --find-links` from
   that wheelhouse (`:53-56`, `:82`, `:126`) and has no other ingress.

The image's `Dockerfile` is at `tester-unified/Dockerfile`, **outside
`assay/**`**, which this wave forbids touching. So DA-D15's own escape hatch
applies and **nothing was landed**. The REPORT's decision ask offers three
options — (a) add `pyflakes` to the shared image, (b) add it to assay's own
build wheelhouse, (c) run it outside the gate — and recommends (b) without
taking it. **Do not land any of them on the controller's silence.**

## 4. Load-bearing seams generation 3 added or changed (read before touching them)

| seam | where | why it matters |
|---|---|---|
| `r2_deferred_early_error` | `runner.py:2822`-ish (declaration), set at `:2855`, read at `:3194-3200` | the ONE deferred announcement. Any NEW early-R2 refusal added OUTSIDE the `result.outcome is Outcome.PASS` guard must join it; one added inside must announce eagerly at its own site |
| the six composed refusal messages | `runner.py:3792`, `:3806`, `:4056`, `:4219`, `:4284`, `:4369` | each builds an `AssayError` purely to carry the message; the pair passed to `refuse_lane`/`refuse_all` is unchanged, so the DOCUMENT is byte-identical to before |
| `_finish_direct_r0_lane` | `runner.py:4552` | the direct-R0 post-command guard, moved verbatim out of `run_lane` so B028's `try` could span it. Nothing in it changed but its address — a diff reader will see it as new code and it is not |
| `_replace_highest_higher_rigor_claim_with_git_failed(status=…, reason_code=…)` | `runner.py:3609` | defaults are A-193/A-194's `ERROR`/`GIT_FAILED`; only the `LANE_TIMEOUT` branch at `:3916` passes anything else |
| `execute_command(infrastructure_source=…, infrastructure_environment=…)` | `runner.py:828`, forwarded at `:884` | new keyword pair, both defaulting to `None`. `canary._run_pipeline` (`canary.py:188`) and `canary.run_python_canary` (`canary.py:225`) forward them |
| `tests/test_lane_timeout_writes_a_verdict.py` | new | uses a REAL `budget = "1s"` and `sleep 30`; each CLI test costs ~1-2s of wall clock. Its one double is a `scratch_root_factory` whose teardown raises |
| `tests/test_r3_canary_sees_infrastructure.py` | new | drives a real pytest R3 canary through the CLI; ~40s. Writes `ciu.global.toml` AFTER `commit_all` deliberately (gitignored, and the tree must stay clean) |

## 5. Gate state

**GATE-VERIFIED COMMIT: `93188912`.** One run, first try, green. Launched
exactly as BRIEF-2 §7 shows (log path a literal inside the `bash -c` string;
worktree committed clean and untouched for the whole run). Exactly one gate
process and one `tester-unified` container confirmed 20s after launch.

Verdict read in a SEPARATE step from the log's own markers:

```
COMPLETE_MARKERS=1          (ASSAY_REGISTERED_GATE_COMPLETE=1, exactly one)
GATE_EXIT=0
BAD=0                       (grep -c -E 'FAILED|DIRTY_TREE|Traceback')
Created wheel for assay: assay-4.1.1.dev14+g93188912-py3-none-any.whl
  size=526694 sha256=087415a9227f86ce9eb9ce7b0b1084911b5d083e19a7f855930cf2a1c6a299f2
tester-unified: PASS (exit 0)
  commit: 931889122cf663469a81e4db6e5e990c43d0263d
ASSAY_GATE_PHASE=verdict-v6-v7-v8-hard-cut-verified
ASSAY_GATE_PHASE=verdict-v9-successors-verified
ASSAY_GATE_PHASE=self-hosted-lane-passed
ASSAY_GATE_PHASE=topos-qualified
ASSAY_B006A_CMRU_QUALIFIED=1
ASSAY_GATE_PHASE=independent-self-hosting-passed
```

The wheel name carries the judged commit. **The branch tip is one docs-only
commit past it (this brief and the LOG's gate entry); nothing executable
changed after `93188912`.**

Whole suite, worktree-local, immediately before the gate: **3985 passed, 20
skipped in 538.51s**, zero failures. (Generation 2's figure was 3968; the 17
added are 8 for B053's follow-ups, 7 for B028 and 2 for B029.)

**One environment note, not a trap but worth the minute it saves:** in this
harness a foreground `sleep` is blocked and long `Bash` calls are moved to
the background, so polling the gate log by hand does not work. Arm a
`Monitor` with `until grep -q 'GATE_EXIT=' <log>; do sleep 30; done` at
launch and let it fire.

## 6. Next free ids (re-checked against `main`, which MOVED TWICE)

`main` advanced from `9b0bca62` → `ba741c3b` (the Wave D controller log
recording generation 2's verification and rulings DA-R3..DA-R6) → `8be4c6b9`
(a run-gate docs commit, RG-32). Neither touches assay's ledgers:

```
$ git show main:assay/nyxloom-trove/decisions.md | grep -o '^| A-[0-9]*' | tail -1
| A-407
$ git show main:assay/nyxloom-trove/4-backlog.md  | grep -o '^## B[0-9]*'  | tail -1
## B061
```

Generation 3 allocated **A-414, A-415, A-416** and filed no new backlog
entry. **Next free: A-417, B062.** Re-run both commands against `main` before
allocating — it moves, twice in one generation this time.

## 7. Generation 4's job — phase 2, in the wave prompt's own order

**Step 7 first, and it is a DESIGN step: A-rows BEFORE any schema edit.**
The wave prompt is explicit that every wire change is designed before the cut
commit. Design, as A-rows:

- **B050's `judgment.r2.fail_under`** (DA-D6): required under
  `producer = "ingested"`, forbidden under `"native"`; `judge_mutation` takes
  the floor; `verify.py` reads it FROM the document; the load-time refusal
  deleted; CONSUMERS' "must be 100.0" paragraph dropped.
- **B053's per-claim `detail`** (DA-D2 (c)): optional string on NON-PASS
  claims only, byte-copied from the refusing `AssayError`'s message, bounded
  at 2048 bytes with B014's `dropped_bytes` truncation convention, absent on
  PASS and absent when no refusal produced text; `verify.py` checks the
  presence rule and the bound only. **Generation 3's A-414 makes this
  strictly easier: every refusal reachable through `assay run` now HAS a
  message, so `detail`'s "absent when no refusal produced text" case is now
  narrow rather than the common one.** Note the DA-R4 rule too: a refusal
  whose claim the verdict discards must not put `detail` on some other claim.
- **B004's `PROVENANCE_UNVERIFIED` + the §5.4 narrowing** (DA-D7) — and
  RE-CAPTURE the ciu assets first; W2's frozen ciu 6.0.3 / schema 1 documents
  are stale (ciu 7.10.1 emits `schema_version: 2` with an `unlabelled`
  status). `cd /workspaces/dstdns && ciu provenance --json` is read-only.
- **B007's `targets`/`aggregation`/per-attempt payload** (DA-D8) — MEASURE
  one materialisation before choosing the bound, and record the number.
- **F015's claim shape** (DA-D9) — designed in phase 2, implemented in phase 3.

**Then the cut, ONE `feat(assay)!:` commit:** `VERDICT_SCHEMA_VERSION = 10`
(hard cut, A-138/A-170; `assay verify` refuses v9 exactly as v9 refused v8),
every field registered in the schema, the dataclass AND `verify.py` (the
third place — the 2.4.0 lesson), new frozen drift-guard
`carve-assets/W6/verdict.schema.v10.json` + `expected/` +
`test_acceptance_v10.py` with W5 kept as history.

**Then the items, in order:** B050 → B051 (DA-D4) → B052 (DA-D5) → B053's
`detail` → B004 (DA-D7) → B007 (DA-D8), then CONSUMERS' "Migration notes
(v9 → v10)".

**`LANE_SCHEMA_VERSION` stays 2 for the whole wave** (Consumer coupling fact
1) and `inventory_schema` stays 1 (fact 2).

## 8. Retention prompt for generation 4 (self-authored)

> **KEEP:** the branch/worktree identity and the tip; that **PHASE 1 IS
> COMPLETE, 10/10, gate-green on `93188912`** with A-408..A-416 allocated;
> that B024 is the ONE open decision ask and generation 4 must NOT land any
> of its three options without a ruling; §4's table of the seams generation 3
> added — especially `r2_deferred_early_error`'s rule (a new early-R2 refusal
> outside the PASS guard must join it) and that `_finish_direct_r0_lane` is
> MOVED code, not new logic; the gate launch recipe, BOTH of BRIEF-2 §7's
> traps ("never run git after `cd /workspaces/vbpub`", "the worktree must
> stay untouched for the WHOLE run"), and §5's Monitor note; next free ids
> A-417 / B062 and that `main` moves; BRIEF-1 §8's rules (Edit tool only, no
> bare `git stash`, `git commit -F … --only -- <paths>` with both trailers,
> commit before you gate, read the verdict in a separate step, touch only
> `assay/**`); §7's phase-2 task list verbatim, including that A-414 narrows
> B053's `detail` absent-case and that DA-R4's rule constrains where `detail`
> may appear.
>
> **DROP:** the reading trail behind every phase-1 seam (the REPORT has the
> conclusions); the full text of every resolved backlog entry (B049, B053,
> B054, B055, B056, B009, B060, B028, B029); the phase-1 red-first
> transcripts (LOG and REPORT carry them); the docs wording.
>
> **DO NOT** re-open a phase-1 item, land B024 on silence, or write the
> `feat(assay)!:` cut before every wire change of step 7 exists as an A-row.
> R-1 is reviewing `93188912` in parallel; a phase-1 blocker it returns comes
> back through the controller, not by generation 4 pre-empting it.
