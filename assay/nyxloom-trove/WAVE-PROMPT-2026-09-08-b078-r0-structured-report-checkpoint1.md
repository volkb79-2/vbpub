# Wave prompt — B078 Checkpoint 1: R0 structured-report tiebreak, vitest only (2026-09-08)

Branch: `feat/assay-b078-r0-structured-report-2026-09-08`
Worktree: `/workspaces/vbpub/.worktrees/assay-b078-r0-structured-report`
Controller log: `assay/nyxloom-trove/reports/assay-WAVE-B078-CONTROLLER-LOG.md`

**This branches off `main` AFTER B070 (verdict schema v11) has already
merged.** You are building on top of v11, not v10 — do not be surprised by
`judgment.r2.discarded` being an array, `VERDICT_SCHEMA_VERSION == 11`,
etc. Nothing in this wave touches the mutation/verdict schema at all — this
is entirely inside R0's own evaluation path (`runner.py`'s
`execute_command`), so there should be no interaction with B070's surface,
but if you find one, stop and flag it rather than guessing.

**No verdict-schema change in this wave.** `Outcome`/`EXIT_CODES` (frozen
by A-021) are untouched. `assay verify` is unaffected. This is Checkpoint 1
of 3 from a design that is ALREADY WRITTEN — your job is to build exactly
what it specifies, not to re-derive the design.

## Context to read first, in this order

1. `assay/nyxloom-trove/R0-STRUCTURED-REPORT-DESIGN.md` — the FULL design
   (SR-0 through SR-6 plus the migration surface). Read it in full. This
   wave prompt does not repeat it — it only tells you which checkpoint to
   build and reminds you of the binding constraints SR-6 already names.
2. `assay/nyxloom-trove/4-backlog.md`, search `## B078` (the R0
   trusts-only-exit-code entry, NOT the compile-vs-runtime one — that one
   was renumbered to B079 at the same merge that landed B070; ignore
   `## B079`, it is unrelated to this wave). Read its acceptance checklist.
3. `src/assay/runner.py`'s `execute_command`/`resolve_command_plan`
   (`runner.py:1140-1142` names the exact site A-073's rule lives at) — the
   code this wave extends.
4. `src/assay/errors.py` — `Outcome`/`EXIT_CODES`, so you can see for
   yourself why SR-0 says this layer is already correct and untouched.
5. `src/assay/adapters/` — an existing per-format adapter (e.g.
   `go_stmtpos.py`, cited in SR-4 for its "adapt, do not invent" doctrine)
   for the shape a per-format reader takes in this codebase, since SR-4's
   own migration-surface note leaves "mirror `adapters/` or a new
   `report_adapters/`" as a checkpoint-1 design question for you to answer.

## Scope: Checkpoint 1 ONLY (SR-5's own sequencing)

Build, in order:

1. **The opt-in framework**: a new `[lanes.<name>.result_report]` TOML
   table (SR-2's illustrative shape: `format`, `path`) — exact key names
   and location are yours to decide (SR-2 says so explicitly), but follow
   this estate's existing nesting convention (`[lanes.<name>.pins.assay]`
   is the precedent SR-2 names). Validate it the way other lane tables are
   validated; a lane that doesn't declare it is byte-for-byte unaffected —
   **prove this**, don't just assert it.
2. **The completeness-verification core**, format-agnostic: SR-2's three
   bullets (valid/well-formed for the declared format, carries the
   format's own "finished" marker, non-zero total test count). This core
   is what every future per-language reader (Checkpoints 2/3) will reuse —
   design it so a reader is a small, format-specific plug-in to this core,
   not a copy of it.
3. **The `vitest-json` reader** (SR-4a): native `--reporter=json
   --outputFile=<path>`, no plugin. Read the Jest-compatible shape
   (`numTotalTests`, `numPassedTests`, `numFailedTests`, `success`,
   `testResults`) — SR-2's "finished marker" for this format is the
   top-level `success` field being present.
4. **R0 wiring in `execute_command`**: after the wrapped command exits,
   check for the declared report. Exactly the three-way branch SR-2
   specifies — memorize this, it is the entire point of the design:
   - report absent, unparseable, or fails the completeness check →
     **today's A-073 rule, exit code alone, completely unchanged**;
   - report verified-complete, zero reported failures → `PASS`,
     regardless of the wrapped process's exit code (this is the RG-45 case
     this checkpoint exists to fix);
   - report verified-complete, one or more reported failures → `FAIL`,
     regardless of exit code (already true in spirit, but now driven by
     the report rather than a coincidence).
5. **Fault-injection regression tests**, one per named failure shape (SR-5
   checkpoint-1 acceptance, quoted directly): a report claiming success
   but truncated mid-write must still FAIL (via A-073 fallback, not a
   crash); a report the wrong shape must still FAIL (same fallback); a
   report naming real failures must FAIL regardless of a zero exit code;
   the RG-45 shape itself (report verified-complete + 0 failures +
   non-zero exit) must PASS. A lane not declaring `result_report` at all
   must be byte-for-byte unaffected — write a test that proves this, not
   just an absence of new failures.

## Binding constraints (SR-6, already ruled — do not re-litigate)

- **No change to `Outcome`/`EXIT_CODES`** — frozen by A-021.
- **No change to A-073's default** for a lane that doesn't opt in.
- **No cross-container CPU-quota coordination primitive** and **no retry
  heuristic** — both explicitly rejected candidate directions from RG-45's
  own filing, do not build either even as a "helpful" addition.
- **No `run-gate.py` change.**
- **No dstdns-side argv change** — getting dstdns's own lanes to actually
  declare `result_report` is dstdns's own follow-up, not this wave's job.
- **No new `ReasonCode`** — `COMMAND_FAILED` continues to cover a lane's
  wrapped-command failure; this design changes WHEN it fires, never what
  it means. Whether to add free-text detail via the EXISTING `detail`
  field (SR-6's own suggestion, avoiding A-050's closed-enum stop-and-ask)
  is yours to decide, but do not add a new enum member.
- **Checkpoints 2 (`pytest-json-report`) and 3 (`go test -json`) are
  explicitly NOT this wave.** Leave the completeness core general enough
  that they're a small addition later, but do not build either reader now.

## Running the gate

`./run-gate.py` from inside `assay/` in your worktree; the registered lane
is `tester-unified` (`run-gate.toml`). Read the verdict from the gate's own
log markers in a SEPARATE step, never a piped exit code.

Host: 8 cores shared with a production game server, and this wave is
running IN PARALLEL with a sibling wave (B074+B077, a different worktree,
same host) by explicit operator authorization — the usual single-agent/
single-gate directive is deliberately reversed for these two waves only.
This makes host-load discipline MORE important, not less:
- `docker ps --no-trunc` AND `pgrep -af tester-unified-gate.sh` before
  starting anything — if either shows an existing gate container/process
  from ANY session (yours, the sibling wave's, or an unrelated one), WAIT
  for it rather than racing it.
- The moment your own gate container starts, `docker update --cpus=3` on
  it — **identify it by the worktree path in its own launch argv
  (`--inner <your-worktree-path>` or equivalent), never by recency or
  process-list position** — a peer session's container starting at nearly
  the same time as yours is expected this wave, not a sign something is
  wrong. Never cap or touch a container you cannot positively identify as
  your own.
- Serial pytest under `nice -n 19 ionice -c 3`. Never run a build
  concurrently with your own running test suite.

## Do NOT write your LOG/REPORT into the tree before your final gate run

Run the gate on a clean commit; write LOG/REPORT and commit them only
after you have a green verdict to report — a prior wave hit
`NO_MEASUREMENT`/`DIRTY_TREE` by doing this in the wrong order.

## Checkpoint clause (E-008)

If you cross ~120k context tokens or ~60 tool calls, cut at the next
coherent boundary (green gate > commit > LOG/REPORT write; never a red
gate) and write a continuation brief to
`assay/nyxloom-trove/reports/assay-WAVE-B078-BRIEF.md` plus a
self-authored `/compact`-retention prompt, commit, and stop.

## Commit trailer

```
Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>
```

## When done

Write `assay/nyxloom-trove/reports/assay-WAVE-B078-LOG.md` (what you built,
per commit, with hashes, and the two design questions SR-2/SR-4/migration-
surface left to you — exact TOML key names, and where the reader module
lives — with your answer and why) and
`assay/nyxloom-trove/reports/assay-WAVE-B078-REPORT.md` (the backlog's own
Checkpoint-1 acceptance box, item by item, with evidence — cite exact test
names, not just claims), commit both, then stop. Do not dispatch a reviewer
yourself — the controller does that next.
