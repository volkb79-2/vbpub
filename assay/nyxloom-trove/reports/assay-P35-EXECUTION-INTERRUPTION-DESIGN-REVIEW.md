# Assay P35 execution-interruption-boundary — adversarial design review

**Result: NOT READY — do not dispatch or implement.**

## Reviewed revision and scope

- Checkpoint tip: `c132d598474ab1dcc9c84e3dbe943dc672417874`
- Checkpoint parent: `bcb70320edc26cdfe1ed52d90b4495aa025a905e`
- Review worktree: `/workspaces/vbpub/.worktrees/assay-p35-execution-interruption-review`
- Branch: `review/assay-p35-execution-interruption-boundary`
- Reviewed target files: B099 in `assay/nyxloom-trove/4-backlog.md`, A-448 in
  `assay/nyxloom-trove/decisions.md`, and the P35 handoff.

No target file was edited. No `run-gate-project` file, `dstdns` file, or
operator-owned dirty path was edited. No mutation campaign, tester gate,
Docker mutation container, merge, or release was run.

## Blocking findings

### F-1 — RG-55 `watch` kill is outside the receipt vocabulary [P0]

P35 closes the producer pairs at `run-gate.progress`, `run-gate.log-stream`,
and `run-gate.budget` (handoff lines 141–147 and 240–248). The frozen RG-55
contract independently requires every daemon-profiled lane to open one
`cgprofile ctl watch`, and its `--on-stall kill` path can kill the lane and
report `verdict: killed` (RG55 contract lines 450–467, 508–523, 562–570).

There is no `run-gate.cgprofile` producer, no mapping from a daemon `killed`
reading to the receipt, and no explicit report-only rule that prevents that
watch from terminating until such a producer exists. A daemon kill can
therefore leave the receipt in `running` or absent; assay then applies the
B078 exit fallback to an incomplete report and can emit functional `FAIL` or
`PASS`. That is the exact false-certification boundary this package is meant
to close. The current run-gate implementation has no receipt/reconcile or
`ctl watch` path to make the omission harmless.

The companion contract must either add an exact cgprofile observer/fact and
publish it before every daemon kill, or explicitly force `--on-stall report`
until that producer is accepted. Both branches need a cross-boundary test.

### F-2 — forced termination is not durable across owner death [P0]

P35 requires reconciliation before clearing inflight and says a later
re-attach may finish the same reconciliation (handoff lines 232–248). The
current run-gate tail removes the container and clears inflight in one
`finally` without a reconciliation-pending state (`run-gate.py` lines
6139–6193). The current inflight grammar records container/artifact/profile
identity but has no receipt path, journal path, run id, termination state, or
reconcile completion marker (`SPEC.md` lines 1211–1251).

If the owner writes the interruption receipt, terminates the container, and
dies before `assay reconcile`, the next invocation can treat the record as an
ordinary exited/gone run, clear it, and start fresh. The required no-test
verdict is then lost; a later re-attach cannot finish it because no durable
state says reconciliation is pending. P35 needs a versioned inflight state
machine with atomic ownership/claim transitions, idempotent reconciliation,
and a refusal-preserving recovery path before the companion can be accepted.

### F-3 — no one-winner status/start TOCTOU protocol is specified [P0]

The receipt contract says a watcher observed a stall “while the container was
still running” and names an “owning watcher” (handoff lines 126–147), but does
not define the atomic check, container identity binding, or arbitration among
progress, log-stream, budget, and cgprofile producers. The current watcher
loop only infers its precondition from `docker logs -f` not returning and then
polls the signal (`run-gate.py` lines 6071–6100); it does not perform a
run-scoped compare-and-publish operation.

A natural container exit can race the receipt replacement, or two watchers
can both observe `running` and overwrite the receipt with different causes.
A re-attach/start can then read or terminate the wrong run unless the receipt,
container id, inflight record, and lifecycle transition are claimed together.
The design must specify a single-winner CAS/lock protocol, the exact
status/start ordering, late-writer behavior, and fixtures for natural-exit
versus forced-kill races. “Before termination” alone is not an atomicity
contract.

### F-4 — `budget_expired` has no authoritative producer or bound [P0]

P35 names `run-gate.budget` and a “fixed safety ceiling” (handoff lines
141–147), but the current run-gate contract explicitly says the lane `budget`
is advisory and not enforced (`SPEC.md` lines 397 and 1442–1484; the same is
stated in `run-gate.py` near `ProgressWatch`). No new outer ceiling key,
duration grammar, owner, clock, precedence against assay `LANE_TIMEOUT`, or
relation to RG-55 `--ceiling`/`over_ceiling` is defined.

An implementer can silently make the existing advisory budget a kill budget,
reuse `LANE_TIMEOUT`, or translate an RG-55 ceiling into the new reason. Each
would collapse different facts into `INFRASTRUCTURE_INTERRUPTED` and make the
receipt a false causal assertion. The source, limit, defaults, termination
owner, and closed mapping must be decided and tested, or this producer pair
must be removed from v1.

### F-5 — the execution-journal wire contract is not implementable as written [P0]

The journal requirement says “exact” schema/version/identity and “one record
for each rigor boundary,” but gives no journal schema or exact record grammar
(handoff lines 200–207). The implementation list names a verdict schema and a
receipt schema, but no `execution-journal.schema.json` or journal module
(lines 281–298). It also leaves the resolved metadata, byte bound, atomic
write/fsync/rename protocol, duplicate and ordering rules, writer ownership,
report-path binding, and stale/torn-file behavior for the implementer to
invent.

That is unsafe for R2: a boundary record can be complete while a candidate
result is only partially persisted, while progress/resume state disagrees, or
while another invocation appends concurrently. Reconcile cannot award only
independently valid completed boundaries without a closed transaction and
identity grammar. The packet must define the journal schema, bounded safe
reader/writer, commit point, per-boundary payload/provenance, and recovery
matrix for every R0–R4 boundary before the no-partial-credit oracle is
satisfiable.

### F-6 — v12 cannot represent mixed claim outcomes under the stated rules [P0]

P35 says an interrupted R1–R4 claim is payload-free while an independently
complete functional `FAIL` remains a functional `FAIL` (handoff lines
193–198). The current rollup precedence is `ERROR > NO_MEASUREMENT >
BUDGET_EXCEEDED > FAIL > INCONCLUSIVE` (`assay/src/assay/verdict.py` lines
428–437), and the verdict currently has one top-level `reason_code` and no
interruption summary (`verdict.py` lines 4068–4165). The proposed summary is
instead said to be present iff the verdict contains the new
`INCONCLUSIVE/INFRASTRUCTURE_INTERRUPTED` reason (handoff lines 168–174).

For a complete R0 functional `FAIL` plus an interrupted R1, either the rollup
keeps `FAIL` and cannot carry the new reason/summary, or it becomes
`INCONCLUSIVE` and loses the explicitly retained functional failure. The
current model also requires a canary payload for an R3 `INCONCLUSIVE`
judgement (`verdict.py` lines 3696–3701), directly conflicting with the
payload-free interruption claim. The packet needs an exact v12 claim/top-level
representation and rollup table for mixed R0–R4 states, including existing
ERROR/NO_MEASUREMENT/BUDGET terminals, before schema-v12 can be justified and
wired without either invalid artifacts or hidden functional results.

### F-7 — receipt identity, namespace, bounds, and refusal vocabulary are open [P0]

The receipt examples and prose specify only five or seven fields and say
“bounded,” “strict,” “stale,” and “named infrastructure refusal” (handoff
lines 107–164 and 287–289). They do not specify the byte ceiling, JSON
duplicate-key/additional-property policy, field patterns/maxima, freshness
proof, producer identity, exact host-to-container path mapping, ownership and
permissions, or the stable outcome/reason/exit behavior for each refusal.

This matters because the existing safe-I/O API requires every caller to supply
the bound and maps unreadable inputs to `ERROR/UNREADABLE_ARTIFACT`
(`assay/src/assay/safeio.py` lines 353–437). The current result-report reader
deliberately maps every report refusal to the B078 fallback
(`assay/src/assay/result_reports/__init__.py` lines 30–33); reusing that path
would turn a malformed supplied receipt into a functional result. “Missing
receipt is legacy” versus “supplied path is missing” is also unresolved.

The packet must close the path/namespace grammar, fixed limits, strict JSON
rules, freshness/identity source, and a distinct refusal result before the
malformed, stale, symlinked, oversized, mismatched, and write-failure tests
can prove fail-closed behavior.

### F-8 — dispatch metadata and mechanical dependency/scope are inconsistent [P1]

The handoff declares `depends_on: []` (line 10) while its own companion-owner
section says the package is not dispatchable until a separate run-gate/cgprofile
packet is accepted (lines 305–312); B099 says the same in its status block.
The canonical authoring contract says `depends_on` is what makes the daemon
hold a handoff until merged handoffs/open decisions resolve
(`nyxloom/reference/AUTHORING.md` lines 329–332 and 415–419). No companion
package id is named, so the hold is prose-only.

The handoff also names a focused receipt module but does not include its exact
path in `scope.touch`, names no journal schema/module, and instructs the agent
to use the nonexistent worktree/branch
`/workspaces/vbpub/.worktrees/assay-P35-execution-interruption-boundary` /
`feat/assay-P35-execution-interruption-boundary` (lines 47–52). Finally,
`tier: luna-high` is a model-era name, while the tracked live routing matrix
has only `implement-1`, `implement-2`, `implement-1-free`, and `review-3`
(`nyxloom/routes.host.toml` lines 75–84); the authoring contract requires a
live tier key, not a model name. `nyxloom lint` does not catch this route/path
drift.

The BLOCKED rule says to write to an implementation LOG (lines 325–332), but
no exact LOG path is in `scope.touch` and no companion dependency gate invokes
it. The packet needs a real dependency id, exact new-file scope, a valid live
tier/worktree contract, and a mechanical location/handling for BLOCKED.

## Required red-first additions

Before dispatch, add pairwise fixtures that exercise at least these combined
axes:

| Combined axes | Required invariant |
|---|---|
| RG-55 daemon `killed`/`reported` × complete/incomplete report | `killed` publishes a verified interruption before kill; `reported` never changes a verdict; a complete report always wins |
| progress/log/budget/cgprofile producers × natural exit/forced kill | one run-scoped producer wins; no late writer or status/start race changes the cause |
| owner death before receipt, after receipt, after kill, before/after reconcile | inflight remains recoverable and reconciliation is exactly once, without rerunning tests |
| R0 functional FAIL × interrupted R1/R2/R3/R4 | complete functional claims remain visible while unfinished claims are payload-free and the top-level v12 representation remains schema-valid |
| R2 journal commit × candidate/progress persistence × resume | only committed identities enter buckets; the next resume begins at the next uncommitted identity |
| assay lane timeout × run-gate outer budget × RG-55 over-ceiling | each terminal retains its distinct owner and reason; no narrower reason is reused |
| absent/supplied-missing/stale/symlink/mismatched/oversized receipt × exit 0/nonzero | legacy fallback applies only to genuinely absent optional input; supplied invalid input refuses closed |

## Commands and results

All commands below ran in the isolated review worktree.

- `nyxloom lint assay/nyxloom-trove/handoffs/assay-P35-execution-interruption-boundary.md`
  → exit 0, `clean`.
- `git diff --check bcb70320edc26cdfe1ed52d90b4495aa025a905e c132d598474ab1dcc9c84e3dbe943dc672417874 -- assay/nyxloom-trove/4-backlog.md assay/nyxloom-trove/decisions.md assay/nyxloom-trove/handoffs/assay-P35-execution-interruption-boundary.md`
  → exit 0, no output.
- Memory PSI before targeted tests: `full avg10=0.51` (threshold ≤ 5).
- `pytest -q assay/tests/test_result_reports.py assay/tests/test_runner_result_report.py assay/tests/test_config_result_report.py assay/tests/test_safeio.py assay/tests/test_verdict_reason_codes.py assay/tests/test_verdict_schema_rejects.py assay/tests/test_verdict_schema_is_packaged.py`
  → exit 0, `734 passed in 14.12s`.
- An initial command used the singular typo `assay/tests/test_result_report.py`; pytest returned exit 4 with “file or directory not found” and ran no tests. It was immediately corrected; the successful result above is the only targeted-test result.
- Read-only checks of `run-gate.py`, `SPEC.md`, and `RG55-INTERFACE-CONTRACT.md` found no current receipt or reconcile implementation and confirmed the advisory-budget and daemon-watch contracts cited above.

## Decision

**NOT READY.** The focused existing assay tests are green, but they exercise
the pre-P35 surfaces and cannot certify the missing cross-project lifecycle.
Do not dispatch P35 until F-1 through F-8 are resolved in the handoff and the
companion contract, with the red-first fixtures above and a real gate-boundary
acceptance probe.
