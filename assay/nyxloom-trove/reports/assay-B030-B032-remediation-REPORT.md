# assay B030/B031/B032 — observability remediation — REPORT

**Branch:** `fix/assay-b030-b032-observability-remediation`
**Base:** `main` at `142143a4` ("docs(assay): file the 2.1.0->2.3.0 review-gap
audit and its backlog (B030-B034, RG-23)")
**Source of the filings:** `reports/assay-review-gap-audit-2026-08-25.md` §6 —
the first independent review of `8a2a4731` (shipped in assay-v2.2.0 as B010's
`environment_command` mechanism plus part of B012).

## How the evidence below was produced

Everything is driven through the CLI, not through unit tests. A wrapper script
runs the *worktree's* `assay.cli.main` (the installed `/home/vscode/.venv/bin/assay`
is v2.4.0 — i.e. `main` — and is used deliberately as the BEFORE side of every
comparison):

```sh
#!/bin/sh
exec env PYTHONPATH=<worktree>/assay/src python -c \
  'import sys; from assay.cli import main; sys.exit(main())' "$@"
```

The fixture is a throwaway Git repository with one Python package, one pytest
test, one `python:bool-const-flip` candidate on `pkg/flags.py`, an `R0+R2` lane
(`unit`), a `mode = "whole_target"` lane (`whole`), and four
`environment_command` probe lanes (`nonzero`, `missing`, `slow`, `timeout`).
`assay` below means the worktree wrapper; `assay(main)` means the installed
v2.4.0 binary.

---

## B030 — `assay plan` reported zero candidates for every lane

### Before (installed v2.4.0 = `main`)

```
$ assay(main) plan unit
{ "budget_per_candidate": "60s", "by_file": {}, "by_operator": {},
  "candidate_count": 0, "candidates": [],
  "estimated_serial_seconds": 0.0, "estimated_wall_seconds": 0.0,
  "jobs": 1, "max_mutants": 10, "shard": null, "status": "ok" }

$ assay(main) plan whole --file whole.toml
assay: ERROR/BAD_LANE_CONFIG: mutation target 'pkg/flags.py' is outside
  judge.source_roots ['/tmp/assay-plan-seed-g6at64lv/unused/pkg']
```

The same lane, same commit, actually run:

```
$ assay run unit --verdict-json v1.json
unit: PASS (exit 0)
  -> claims[].mutation: {"candidate_count": 1, "total": 1,
       "killed": [{"path": "pkg/flags.py", "operator": "python:bool-const-flip",
                   "description": "True->False", ...}]}
```

### After (this branch)

```
$ assay plan unit
{ "by_file": {"pkg/flags.py": 1},
  "by_operator": {"python:bool-const-flip": 1},
  "candidate_count": 1,
  "candidates": [{"description": "True->False", "end_byte": 8,
                  "id": "254df6651e46e3cf004908434685132e56153438a39ceb7acc1563717959e269",
                  "lineno": 1, "operator": "python:bool-const-flip",
                  "path": "pkg/flags.py", "start_byte": 4}],
  "estimated_serial_seconds": 60.0, "estimated_wall_seconds": 60.0,
  "jobs": 1, "max_mutants": 10, "shard": null, "status": "ok" }

$ assay plan whole --file whole.toml
{ ... "candidate_count": 1, "by_file": {"pkg/flags.py": 1},
      "candidates": [{"id": "254df665…", ...}] }
```

The reported id `254df6651e46e3cf004908434685132e56153438a39ceb7acc1563717959e269`
is byte-for-byte the `candidate_id` a real `assay run` of the same lane emits
into its progress stream (see B031's after-transcript below) — plan's answer is
the run's answer, which is the whole point of the verb.

### The test that froze the bug

`tests/test_mutation_progress_budget_plan.py::test_plan_reports_candidates_without_executing`
asserted `payload["candidate_count"] == 0`, `by_operator == {}`, `by_file == {}`
and `candidates == []` against a fixture that genuinely yields one candidate. It
now asserts the true count and, at the end, that the reported id equals
`assay.mutation.candidate_id(job)` computed independently for the job a run
executes. A second test,
`test_plan_whole_target_lane_plans_its_declared_target`, covers the
`mode = "whole_target"` half (2 candidates on that fixture, since whole-target
mode judges the whole file rather than the diff's one changed line).

### Doc correction

`docs/CONSUMERS.md`'s "reports deterministic candidate IDs … and runtime
estimates" is now true, and the estimate itself is described honestly: it is
`candidate_count x budget_per_candidate` (or a 60 s fallback) over declared
`jobs` — a declaration-derived upper bound, never a measured baseline. B012's
acceptance box is re-verified against a real run rather than re-checked, and its
narrowing is recorded in the box itself.

---

## B031 — the progress artifact poisoned the clean-tree precondition; the field was dead and unregistered

### Before (installed v2.4.0 = `main`)

```
$ assay(main) run unit --verdict-json v1.json
unit: PASS (exit 0)
$ git status --porcelain
?? .assay/
$ find .assay -type f
.assay/unit.progress.jsonl

$ assay(main) run unit          # the consumer changed NOTHING
unit: NO_MEASUREMENT/DIRTY_TREE (exit 3)
```

The emitted verdict's mutation block, on that same passing run:

```json
{"budget_exceeded":[],"candidate_count":1,"crashed":[],"equivalent":[],
 "killed":[{...}],"survived":[],"total":1}
```

— no `progress_artifact` key, while `.assay/unit.progress.jsonl` sat on disk
unreferenced.

The verify layer, on documents that pass JSON Schema validation cleanly:

```
$ assay(main) verify v1-with-progress_artifact.json
assay verify: schema: unknown mutation field(s): ['progress_artifact']   exit=1
$ assay(main) verify v1-with-candidate_ids.json
assay verify: schema: unknown mutation field(s): ['candidate_ids']       exit=1
```

The two progress `replacement_sha256` values disagreed for the same candidate:
the verdict's `60a33e6c…` (= `sha256(b"False")`, the replacement TEXT) vs. the
progress stream's `cf2e122c…` (the whole mutated FILE).

### After (this branch)

Default — nothing is written, and the lane survives a second run:

```
$ assay run unit --verdict-json v2.json
unit: PASS (exit 0)
$ git status --porcelain          # empty
$ ls -d .assay                    # No such file or directory

$ assay run unit                  # nothing changed
unit: PASS (exit 0)               # exit 0, was exit 3
```

Opt-in, to a path the consumer chose, outside the repository:

```
$ assay run unit --progress /tmp/prog.jsonl
unit: PASS (exit 0)
$ git status --porcelain          # still empty
$ cat /tmp/prog.jsonl
{"candidate_total":1,"commit":"2ac51dd8…","event":"run","started":"2026-08-25T21:44:08.070504+00:00"}
{"candidate_index":-1,"candidate_total":1,"end_byte":0,"event":"baseline","mutated_file_sha256":"","operator":"baseline","path":".","start_byte":0}
{"candidate_id":"254df6651e46e3cf004908434685132e56153438a39ceb7acc1563717959e269","candidate_index":0,"candidate_total":1,"description":"True->False","elapsed_seconds":1.375,"end_byte":8,"lineno":1,"mutated_file_sha256":"cf2e122caafd1f7067fef45a64634226fdf61690c4f833061f55d82957725949","operator":"python:bool-const-flip","outcome_bucket":"killed","path":"pkg/flags.py","start_byte":4}
```

Note the `run` header (commit + start time — the file is opened for append and
never truncated, so without it a tailing monitor cannot attribute a
`candidate_index`) and `mutated_file_sha256`, which no longer collides with the
verdict's replacement-TEXT `replacement_sha256`.

`candidate_ids` now has a real producer and round-trips through `assay verify`.
A live sharded run:

```
$ assay run unit --shard 0/1 --verdict-json v3.json
unit: PASS (exit 0)
$ jq '.claims[]|select(.mutation).mutation|{candidate_count,total,candidate_ids}' v3.json
{ "candidate_count": 1, "total": 1,
  "candidate_ids": ["254df6651e46e3cf004908434685132e56153438a39ceb7acc1563717959e269"] }
$ assay verify v3.json
exit=0
```

The removed field is refused, as it must be — the field is gone from the
dataclass and the schema together:

```
$ assay verify v1-with-progress_artifact.json
assay verify: schema: unknown mutation field(s): ['progress_artifact']   exit=1
```

### A fourth registration gap, found while verifying this one (A-323)

Driving that sharded run through `assay verify` for the first time surfaced a
defect nobody had filed:

```
$ assay(main) run unit --shard 0/1 --verdict-json v3.json   # main
unit: PASS (exit 0)
$ assay(main) verify v3.json
assay verify: schema: unknown judgment.r2 field(s): ['shard_count', 'shard_index']
exit=1
```

`verify.py`'s `_reconstruct_judgment_r2` never read `shard_index`/`shard_count`
(added by `7a4f6333` alongside `candidate_ids`), so **`assay verify` rejected
assay's own output** for any sharded run. Registered here; the round-trip above
is now exit 0. Recorded as A-323 rather than folded into A-320 because it is a
different field group on a different object from a different commit — and
because the running count matters: four B012 fields have now reached `main`
schema-valid and verify-invalid.

### The decision

Recorded as **A-320**. Applying A-292's ruling ("never the consumer's real
worktree") to this feature leaves `mutation.progress_artifact` with no truthful
value it can carry — its only legal grammar (`_check_wire_path`,
repo-tree-relative) can name nothing *but* the forbidden location, and an
absolute machine-local path in a portable artifact would be worse. Keeping it
would have required either forcing the destination inside the work tree (which
re-creates the `DIRTY_TREE` defect) or making its presence conditional on where
the caller happened to point (which makes ABSENCE ambiguous). So the field is
removed — dataclass, `to_dict`, JSON Schema and the W2 frozen lock together —
and the writer is kept, opt-in, at a consumer-named path. The precedent is one
flag over: assay does not record `--verdict-json`'s destination inside the
verdict either. B012 requirement 1's "summarize the artifact path in the
verdict" is therefore **deliberately unmet**, and B012's acceptance box says so
rather than being quietly re-checked.

---

## B032 — the preflight probe discarded its own outcome, misreported budget overruns, and shipped 0 bytes

### Before (installed v2.4.0 = `main`)

| lane | `environment_command` | budget | verdict | exit | stderr |
|---|---|---|---|---|---|
| `nonzero` | `sh -c 'exit 7'` | 5m | ERROR/BAD_LANE_CONFIG | 2 | **0 bytes** |
| `missing` | `/nonexistent/probe-binary` | 5m | ERROR/BAD_LANE_CONFIG | 2 | **0 bytes** |
| `timeout` | `sh -c 'sleep 45'` | 30s | ERROR/BAD_LANE_CONFIG | **2** | **0 bytes** |
| `slow` | `sh -c 'sleep 45'` | 5m | **PASS** after **46 s** | 0 | **0 bytes** |

The `slow` row is the dead-cap proof: the intended 30 s probe cap was set on
`plan.budget_seconds`, which `execute_plan` never reads, while `timeout=` got
the full `deadline.remaining()`. The probe burned 45 s of the lane's budget and
then passed.

### After (this branch)

```
$ assay run nonzero --file probe.toml
nonzero: ERROR/BAD_LANE_CONFIG (exit 2)
--- stderr (171 bytes) ---
assay: ERROR/BAD_LANE_CONFIG: lane 'nonzero': its declared environment does not
match the invoking one (the probe exited 7), so the lane's own command never
started. Run via the declared wrapper: /bin/sh -c 'exit 7'

$ assay run missing --file probe.toml
missing: ERROR/BAD_LANE_CONFIG (exit 2)
--- stderr (198 bytes) ---
assay: ERROR/BAD_LANE_CONFIG: lane 'missing': its declared environment does not
match the invoking one (the probe command could not be executed), so the lane's
own command never started. Run via the declared wrapper: /nonexistent/probe-binary

$ assay run timeout --file probe.toml          # budget 30s, sleep 45
timeout: BUDGET_EXCEEDED/LANE_TIMEOUT (exit 4)     [elapsed 31s]
--- stderr (215 bytes) ---
assay: BUDGET_EXCEEDED/LANE_TIMEOUT: lane 'timeout': its declared
environment_command did not finish within the 30s preflight cap, so the lane's
own command never started. Run via the declared wrapper: /bin/sh -c 'sleep 45'

$ assay run slow --file probe.toml             # budget 5m, sleep 45
slow: BUDGET_EXCEEDED/LANE_TIMEOUT (exit 4)        [elapsed 30s]
```

All three sub-defects, measured:

- **(a)** `timeout` moved from `ERROR`/`BAD_LANE_CONFIG` exit 2 to
  `BUDGET_EXCEEDED`/`LANE_TIMEOUT` exit 4, while `nonzero`/`missing` stayed at
  `ERROR`/`BAD_LANE_CONFIG` exit 2 — the one distinction A-321 rules must not
  collapse (gates retry the former and hard-fail the latter).
- **(b)** `slow` went from `PASS` after 46 s to `BUDGET_EXCEEDED` after 30 s.
  The cap is now `PROBE_BUDGET_SECONDS = 30.0`, applied through
  `probe_timeout = min(PROBE_BUDGET_SECONDS, deadline.remaining())` as
  `execute_plan`'s `timeout=` argument — the value it actually reads. The lane
  budget still wins when it is the smaller of the two (`timeout` fired at 31 s,
  not 30 s, because its own 30 s lane budget and the cap coincide there).
- **(c)** stderr went from 0 bytes to B010's actual asked-for text, naming the
  lane, the specific cause, and the declared wrapper to run via. The timeout
  wording deliberately does **not** claim an environment mismatch: a probe that
  never finished proved nothing about the environment either way.

The verdict artifact is not widened — no free-text field was added (A-138/A-170,
restated at A-309). The diagnosis goes to a caller-supplied stream
(`run_lane(diagnostics=...)`, which `assay.cli` fills with its own `err`),
which is where the CLI already prints every other typed refusal it catches.

---

## Gate

The REAL registered gate (`bash tools/tester-unified-gate.sh ..` from the assay
project root), against the final HEAD `0e6cab39`, exit code captured separately
from the run and read in a separate step:

```
$ ( bash tools/tester-unified-gate.sh .. > /tmp/gate3.log 2>&1; echo $? > /tmp/gate3.exit )
$ cat /tmp/gate3.exit
0
$ grep -n 'ASSAY_GATE_PHASE\|ASSAY_REGISTERED_GATE_COMPLETE\|QUALIFIED' /tmp/gate3.log
22:ASSAY_GATE_PHASE=wheel-installed
25:ASSAY_GATE_PHASE=attestation-hardened
28:ASSAY_GATE_PHASE=verdict-v5-accepted
31:ASSAY_GATE_PHASE=lane-schema-v2-successors-verified
33:ASSAY_GATE_PHASE=verdict-v6-successors-verified
36:ASSAY_GATE_PHASE=verdict-v7-successors-verified
40:ASSAY_GATE_PHASE=self-hosted-lane-passed
41:ASSAY_GATE_PHASE=topos-qualified
55:ASSAY_B006A_CMRU_QUALIFIED=1
56:ASSAY_GATE_PHASE=cmru-b006a-qualified
59:ASSAY_GATE_PHASE=independent-self-hosting-passed
60:ASSAY_REGISTERED_GATE_COMPLETE=1
```

`verdict-v7-successors-verified` runs `carve-assets/W2/test_acceptance_v7.py`,
including `test_shipped_schema_is_byte_identical_to_the_locked_v7_asset` -- the
drift guard outside `tests/` that A-316 records as having caught real drift only
at release time. `carve-assets/W2/verdict.schema.v7.json` was re-witnessed by
copying the shipped schema over it and diffing before committing; the diff is
exactly the five removed `progress_artifact` lines and nothing else, which
independently confirms the lock was otherwise in sync.

`topos-qualified` / `cmru-b006a-qualified` drive real `assay run` invocations
against pinned disposable Topos and CMRU trees and compare complete artifacts
against frozen templates -- the byte-for-byte real-run fidelity check A-317/
A-318 record as the only thing that answers a question schema validity does not.

Separately, over the same tree:

```
$ PYTHONPATH=$PWD/src python -m pytest tests/ -q -p no:randomly
3271 passed, 11 skipped
```

necessary but not sufficient on its own -- it never reaches
`nyxloom-trove/carve-assets/`, which is exactly where B010/B012's original
defects hid.

## A-numbers recorded

| id | What | Why it needed a decision |
|---|---|---|
| **A-319** | B030: `_cmd_plan`'s `_relocate_source_roots` call is **deleted**, not repaired with a different argument. | `plan` never materializes a snapshot, so there is no snapshot project root to relocate against; inventing one (materializing purely to have a root) would make `plan` do the work its own contract says it does not do. |
| **A-320** | B031: progress is opt-in and consumer-named (`--progress PATH`); `mutation.progress_artifact` is **removed** from dataclass + schema + W2 lock together; `candidate_ids` registered in `verify.py` and given its first producer. | B031 explicitly asked for a keep-vs-drop ruling. The field's only legal grammar can name nothing but the location A-292 forbids, so it cannot be made truthful; the `--verdict-json` precedent says a caller-chosen destination is not recorded back. |
| **A-321** | B032 scope: **timeout vs. everything else** is the only probe distinction that survives into the verdict. | Gates branch on exactly that (retry `BUDGET_EXCEEDED`, hard-fail `BAD_LANE_CONFIG`). Separating the other three would cost a closed-enum widening every consumer's schema copy would then reject. |
| **A-322** | B032 fix: the 30 s cap is applied where `execute_plan` reads it, and B010's clear message ships on a caller-supplied `diagnostics` stream. | The message needed a channel decision — a verdict free-text field was rejected (A-138/A-170/A-309), so it is a stream the CLI already owns. |
| **A-323** | `verify.py`'s `_reconstruct_judgment_r2` never read `shard_index`/`shard_count`; `assay verify` rejected assay's own sharded output. | Found during verification of A-320, unfiled anywhere. Its own number because it is a different field group, object and origin commit — and because the count is itself evidence about the review process. |
| **A-324** | Round 2: A-320's no-bump justification restated with the load-bearing fact — no released `assay verify` ever accepted a `progress_artifact`-bearing document EITHER, not merely that no producer ever emitted one. Appended (this file's own convention is append-only); A-320's row is left as shipped. | Round-2 review found "no producer emitted it" alone does not license a no-bump removal (a hand-authored v7 document could still have carried it, schema-legally); the verifier-rejection fact does, because nothing that ever successfully verified stops verifying. Also records the cross-repo pointer: `progress_artifact` was requested by name in `dstdns/docs/proposals/tools/assay-mutation-requirements.md:58`. |

## Known, deliberately out of scope

- **`--shard`/`--resume` still write `.assay/mutation-state/` into the caller's
  project root** (observed on the sharded run above: `?? .assay/`). That is
  A-292's own explicit ruling — resume evidence must outlive the ephemeral
  snapshot and be caller-owned — and it is opt-in, unlike the unconditional
  progress write B031 fixed. Not changed here.
- **`_cmd_plan` still does not run `lane.environment_command`** and plans
  against HEAD without `run`'s clean-tree precondition (audit 8a-H). Named in
  B030's "beyond the headline bug" narrative, not in its acceptance list, and
  each is a behaviour change to `plan`'s contract rather than a bug fix.
- **Per-candidate timeout vs. lane-budget exhaustion are still indistinguishable**
  in the `budget_exceeded` bucket (audit 8a-H). B012 requirement 6's residue;
  untouched.
- **`config.py:1782-1788`'s misindented `kill_signal_artifact` block** (audit
  8a-H) — behaviourally a no-op, left alone.
- **N4 (round 2 review):** `argv_effective` on a probe refusal still names
  the LANE's own command, which never ran, rather than the probe command
  that actually did. Confirmed correct-shape-but-imprecise by round 2's
  reviewer: the message text (`_report_probe_refusal`) already names the
  right thing, and this is a display artifact of the verdict's frozen
  `CommandPlan` shape rather than a new refusal-cause defect. Not fixed
  here; non-blocking.

---

## Round 2 — fix-forward review response (2026-08-25)

A fresh adversarial reviewer independently reproduced every headline claim
above (including on fixtures never tried in round 1 — nested source roots, a
monorepo subdirectory project) and returned **ACCEPT-conditional** on two
blockers, one decision-record condition (D1, on A-320's no-bump reasoning —
see A-324 above), and two cheap non-blockers. This section records what
changed. Commits, LOG entries, and the gate transcript below are all new;
round 1's content above is left as shipped, per this file's own append
convention.

### Blocker 1 — the probe-timeout message named a cap that did not fire

`_report_probe_refusal` hardcoded `PROBE_BUDGET_SECONDS` (30) into the
rendered timeout message even when the LANE's own remaining budget — not the
fixed cap — was the bound that actually fired (`probe_timeout =
min(PROBE_BUDGET_SECONDS, deadline.remaining())`, A-322). A false claim about
which bound applied. `_report_probe_refusal` now takes the effective
`probe_timeout` as a parameter and renders it, naming both candidate bounds:

```
$ cat >> shortbudget.toml <<'EOF'
[lanes.shortbudget]
...
budget = "10s"
environment_command = ["sh", "-c", "sleep 45"]
EOF
$ assay run shortbudget --file shortbudget.toml
assay: BUDGET_EXCEEDED/LANE_TIMEOUT: lane 'shortbudget': its declared
environment_command did not finish within its 9.99477s preflight window (the
lesser of the 30s probe cap and the lane's remaining budget), so the lane's
own command never started. Run via the declared wrapper: sh -c 'sleep 45'
shortbudget: BUDGET_EXCEEDED/LANE_TIMEOUT (exit 4)
```

`9.99477s`, not `30s` — the number now matches what actually enforced the
refusal. Two new behavioral tests in `tests/test_environment_preflight.py`
drive a real subprocess (not a source-text grep, see N1) under each bound: a
lane budget well below the cap (extends the existing
`test_a_probe_that_exhausts_its_budget_reports_a_timeout_not_a_config_error`,
`budget="2s"`) and the cap itself as the binding constraint with the lane
budget patched far above it
(`test_the_probe_cap_is_enforced_where_execute_plan_actually_reads_it`,
rewritten — see N1).

### Blocker 2 — a bad `--progress` destination was laundered into `ERROR`/`GIT_FAILED`

`progress_writer`'s `path.open("a")` (and its own `path.parent.mkdir(...)`)
raised a bare `IsADirectoryError`/`OSError` for a directory destination or an
empty `--progress ""` (which resolves to `.`, the invoking CWD — itself a
directory). Uncaught, it escaped `run_mutation` and was caught by
`runner.run_lane`'s broad `except OSError:`, which relabels ANY escaped
OSError `ERROR`/`GIT_FAILED` — the exact mislabelled-cause class B032 was
filed to close, reopened on the new `--progress` flag. A-320 claimed
`--progress` behaves "exactly like `--verdict-json`'s" destination handling;
it did not.

Two changes make that claim true. First, `progress_writer` (`mutation.py`)
now wraps its own `mkdir`/`open`/`write`/close and raises the same typed
`AssayError(ERROR, OUTPUT_WRITE_FAILED)` `--verdict-json` raises for the
identical mistake, naming the path — defence in depth for any caller that
reaches it without going through the CLI. Second, and primarily, a new
`output.validate_progress_destination` runs in `cli.py` at the SAME
OUTPUT-RESERVATION step as `--verdict-json`'s own reservation, before HEAD is
even resolved — catching the two mistakes visible without opening anything
(an existing non-regular destination, an unparseable empty spelling)
immediately, with a real message, rather than after the whole lane has run.
It is deliberately NOT a full `reserve_verdict_output`-style reservation: a
progress destination is opened once, later, only if the lane reaches R2, and
its own writer creates missing parent directories on demand — a behavior
`--verdict-json`'s reservation does not have and must not gain by accident.

```
$ assay run nested --file assay.toml --progress ""
assay: ERROR/OUTPUT_WRITE_FAILED: the progress destination '' exists and is
not an ordinary regular file; assay only appends to a file it can account for

$ assay run nested --file assay.toml --progress /tmp
assay: ERROR/OUTPUT_WRITE_FAILED: the progress destination '/tmp' exists and
is not an ordinary regular file; assay only appends to a file it can account
for
```

Both now `ERROR`/`OUTPUT_WRITE_FAILED` with a named cause, before any
repository work — reproduced on a real `R0+R2` lane (`nested`), matching the
review's exact repro commands. A regression check confirms the auto-created-
parent-directory behavior survives: a `--progress` destination whose parent
does not yet exist is still created and written to
(`test_a_progress_destination_whose_parent_does_not_yet_exist_is_still_created`).
Four new tests total, two in `tests/test_environment_preflight.py` (CLI
level, the two exact repro cases plus the auto-mkdir regression check) and
two in `tests/test_mutation_progress_budget_plan.py` (unit level, directly
against `progress_writer`, covering both its `mkdir` and `open` OSError
sites).

### N1 — the 30s-cap test was a text oracle

`test_the_probe_cap_is_enforced_where_execute_plan_actually_reads_it` used to
grep `runner.py`'s own source text for three literals — green on a fix that
is correct in text but wrong in effect, the exact "test written to match
observed output rather than the requirement" shape the original audit report
named as a genuinely new lesson. Rewritten to drive a real subprocess:
`PROBE_BUDGET_SECONDS` is monkeypatched down (to keep the test fast — real
elapsed time is still asserted, just against a smaller real cap) so the CAP,
not the lane's much larger declared budget, is unambiguously the binding
constraint; both the exit code/reason code and the rendered message are
asserted against the real, measured outcome.

### N3 — the on-disk state-record shape changed, undocumented

The `mutated_file_sha256` rename (A-320, closing the `replacement_sha256`
collision with the verdict's own field) splats a new key into every on-disk
`.assay/mutation-state/*.json` record going forward. Harmless —
`_load_validated_state_record` tolerates extra keys — but unrecorded until
now: noted here so a future reader is not surprised finding it.

### Gate, at the new HEAD

The registered gate is run against the round-2 final HEAD by a follow-up
commit, same convention round 1 used (`fcdfde92`): exit code captured
separately from the run and read in a separate step, never a pipe tail. See
`assay-B030-B032-remediation-LOG.md`'s "Gate (round 2)" section for the real
transcript.

### Cleared without action

N2 (progress-path traversal) reconfirmed correct, matching `--verdict-json`.
N6/N7 confirmed non-blocking, already correctly deferred/explained. The four
pre-existing deliberately-out-of-scope items above all still hold.
