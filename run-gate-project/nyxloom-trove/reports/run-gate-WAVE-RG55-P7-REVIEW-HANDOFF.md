# run-gate-WAVE-RG55-P7 — adversarial review handoff (RG-55 wave, package P7: assay B091)

**Reviewer:** FRESH session (Opus, xhigh), never a fork of any implementer or
the controller. **Your job is to BREAK this before merge.** 3-round cap;
fix-verification rounds resume YOUR session (the controller messages you the
repair commit). Records: `run-gate-project/nyxloom-trove/reports/
run-gate-WAVE-RG55-P7-REVIEW-round<n>.md`. Edit tool only for that file; no
commits to the branch — repairs are the implementer's.

Branch `assay-liveness`, worktree `/workspaces/vbpub/.worktrees/assay-liveness`,
project dir `assay/` (src `assay/src/assay/`, tests `assay/tests/`). Base:
`main` at `11ac5d67`. The tip hash is in the dispatch message. Review the FULL
diff `11ac5d67...<tip>` — every file, every type (src, tests, fixtures, schema
JSON, the W7 locked schema copy, docs, CHANGES, backlog, the seven session
briefs and the LOG/REPORT). Use absolute paths; ignore whatever primary
working directory the environment reminder names.

## Phase 1 — BLIND (before any LOG/REPORT/BRIEF)

Read, in this order: design of record
`run-gate-project/nyxloom-trove/DESIGN-2026-09-12-liveness-placement-admission.md`
§1 (incidents), §2 D-17, D-22, D-23; controller rulings RW-28, RW-29, RW-33,
RW-36, RW-39, RW-41, RW-42 (`reports/run-gate-WAVE-RG55-CONTROLLER-LOG.md`);
the implementer handoff `run-gate-WAVE-RG55-P7-HANDOFF.md` (A1–A6, oracles);
assay backlog rows B088, B090, B091, B092 (`assay/nyxloom-trove/4-backlog.md`);
`assay/docs/CONSUMERS.md` and `docs/DESIGN-GUIDE.md` as they were on `main`
and as changed; then the diff itself. Form your own view BEFORE the
implementers' narratives. Run your OWN sweeps.

## Phase 2 — RECONCILE against the implementers' claims

Read `run-gate-WAVE-RG55-P7-LOG.md`, `-REPORT.md`, `-BRIEF-1..7.md`; check
every claim; list the ones you could not verify. Seven sessions built this —
look for seams between sessions (a later session's assumption about an
earlier one's behaviour).

## Attack surface (minimum; add your own)

1. **`os._exit` in the materialized plugin.** `pytest_unconfigure(trylast)`
   with `ASSAY_LIVENESS_EXIT=1` only for R2 candidates: prove the terminal
   summary and pytest-cov's data write survive (spike it: a candidate with
   `--cov`), prove R0/R1 never get EXIT set (coverage atexit writers intact),
   prove stdout/stderr are flushed before `os._exit`. Plant a leaked
   non-daemon thread and watch the candidate exit; remove the plugin and watch
   it hang. Does the plugin ever raise into pytest (every hook body wrapped)?
   Import-safe under pytest 7 and 8? Never imports assay?
2. **Injection rule (RW-36).** `judge.mutation.liveness = auto|true|false`;
   `auto` only when the lane argv literally invokes pytest (`pytest`,
   `…/pytest`, `-m pytest`); `true` on a non-pytest argv refuses at load;
   `false` → budget only. Liveness argv goes through `argv_appended` (never
   `argv_declared`); `cli_argv_appended` keeps `allow_argv_append` consent
   scoped to CLI tokens — try to smuggle a liveness token past the consent
   check and vice versa. The `plan` event and `judgment.r2.liveness` disclose
   `{active, reason, plugin}` — verify against a real run with liveness off.
   `PYTHONPATH` prepend: does it clobber a lane's own `PYTHONPATH`? A pyz
   install (assay not importable in the target venv) — the spike claim.
3. **`LivenessRunner` (R2 candidates ONLY).** Prove R0/R1/other lanes still
   use `default_process_runner` (grep every call site). `Popen(start_new_
   session=True)` + `os.killpg`: can a kill reach the wrong process group?
   The 1 s loop: process-tree CPU via `/proc/<pid>/stat` (task children +
   ppid fallback; any /proc failure = "growing" — plant an unreadable
   `/proc` entry and confirm NOT hung); `hung` iff idle ≥
   `expect_next_event_within_s` AND CPU growth < 1.0 s over the trailing
   30 s, OR `session_finish` seen and alive 30 s later; a CPU-spinning mutant
   must be `budget_exceeded`, never `hung` (the two e2e tests — run them,
   time them); `expect_next_event_within_s = max(3 × slowest_test_s, 15)`
   from the BASELINE side file, fallback `max(60, baseline_s / 4)` — is the
   baseline side file really written (session 5 found the plugin wrote
   `repr` not JSON — what else in that path was never exercised end to end?).
   Torn last line tolerance. Boundary conditions at exactly the thresholds
   (session 5's two `>=` mutants survived until fixed — plant your own).
4. **`hung` bucket threading.** `MUTATION_BUCKETS`, verdict model, `to_dict`,
   `verdict.schema.json`, the W7 locked copy (session 7 re-synced it — was the
   drift a symptom of a missing test?), `verify.py` re-derivation,
   `judge_mutation` outcome precedence (session 4 found a hung-only run fell
   through to PASS — craft a verdict document and re-derive), reporting; scored
   like `budget_exceeded` (outside killed/(killed+survived)); additive under
   schema v11 — an ingested (old) document still verifies.
5. **A4 progress stream.** `test` events for the BASELINE only (never per
   candidate — count lines in a real run), `plan.slowest_test_s` /
   `expect_next_event_within_s` read back from one computation,
   `candidate.tests_completed` from that candidate's own side file,
   `"test"` in `PROGRESS_EVENTS`; run-gate's ProgressWatch (RG-36/RG-41) must
   still see `candidate` cadence unchanged.
6. **A5 `--rejudge`.** Drops matching records before they are consulted;
   unknown/stale-source id → `MutationStateError` before any record loads;
   `--rejudge-outcome hung,budget_exceeded,error` (`error` = alias for the
   real bucket `crashed`); `resume.rejudged_total`; ids are `candidate_id()`
   digests, not `MutantOutcome.identity` — is that documented AND enforced
   (a wrong-shape id refused clearly)? Rewrite the verdict without touching
   other candidates — diff a before/after verdict.
7. **A1 (`"auto"` budget).** `max(3 × baseline, baseline + 60 s)` after the
   real baseline PASS; `"none"` WARN-on-declare; `budget = "unbounded"`
   admission; `judgment.r2.budget_per_candidate_derived_s`; `assay plan`'s
   60 s fallback; the `diagnostics=None` + `"none"` regression.
8. **Hollow tests / coverage.** `liveness.py` 100% line+branch is necessary,
   not sufficient: mutate the subject, watch the test, for each cluster
   (plugin, injection, runner loop, bucket, stream, rejudge). Tests must drive
   the real code path where they claim coverage (a subprocess-only test is
   invisible to a diff judge).
9. **Docs/backlog.** CONSUMERS progress-stream table accurate to the code
   (every field, every event); `judge.mutation.liveness` documented;
   `--rejudge` documented; CHANGES `[Unreleased]` complete with BREAKING
   notes where a CLI/schema surface changed; B091 FIXED evidence real;
   B092 row faithful to RW-41; README's B073 note honest.
10. **Gate.** assay's registered gate is ONE lane (`tester-unified`, R0-only
    by design A-046/A-133): re-run it yourself once (verdict in a SEPARATE
    step); it runs a container (`tester-unified:local`, `--cpus=3`) — check
    `docker ps` first (≤ 2 gate containers estate-wide) and remove yours in a
    `finally`. `cmru status --project assay` clean.

## Live probes (yours)

- The RW-28 incident replayed: a scratch project whose one mutant makes a
  test start a non-daemon thread sleeping 120 s; `assay run` with liveness
  `auto` → `hung` within ~45 s, killed, verdict written; with `liveness =
  false` → the candidate runs to its budget.
- A busy-loop mutant → `budget_exceeded`, never `hung`.
- A non-pytest python lane (`python -m unittest`) with `liveness = "auto"`
  → one WARN, budgets only, no `-p` injected (inspect the argv the runner
  executed).
- `--rejudge <one id>` on the scratch project's state → only that
  candidate re-executes (`resume.rejudged_total: 1`).

## Verdict

`ACCEPT` / `ACCEPT-conditional` / `REJECT` with numbered blockers (B1..),
each with file:line evidence and a concrete prescription; non-blocking
findings (S1..) separately; product calls named as decision asks for the
controller, never improvised. Claims you could not verify listed as such.
Write the round file, then return the verdict line first in your message.

## HOST LOAD (binding)

8 cores shared with a production game server; PSI is the signal
(`cat /proc/pressure/memory`; back off while `full avg10` > 5). pytest
SERIAL only, `nice -n 19 ionice -c 3`; targeted files while iterating, the
whole assay suite at most once (~7 min serial). ≤ 2 gate containers
estate-wide (`docker ps` for `run-gate-` / `tester-unified` first; other
packages' mutation runs may be live); `docker update --cpus=3` after any
launch; remove in a `finally`. Never `--cgroupns=host`/`--pid=host`. Never
touch `scripts/cgroup-profiler/`, `run-gate-project/run-gate.py`, `ciu/`,
`/workspaces/dstdns`, other worktrees.
