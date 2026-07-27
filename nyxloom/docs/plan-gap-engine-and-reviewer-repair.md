# Plan: F007 (gap-engine) + D-R8 (bounded reviewer repair)

Status: active · written 2026-07-26 · **premise-audited and revised 2026-07-27**
· operator directive: "keep implementing until built"

## Premise audit (2026-07-27) — read this before trusting the 07-26 text

The first draft of this plan asserted five "this already exists, just mirror
it" premises. Four held; **three related claims were wrong** and had to be
corrected before any package was carved. Recorded here so a future session
does not re-derive the audit or reintroduce the mistakes.

**Verified ✅**
- The test-health carve trigger IS the right shape to mirror: `daemon.py`'s
  `kind == "test-health"` branch in `_carve_packet_body_lines`, the structured
  `carve_kind` marker written into the `TASK_CREATED` payload, and
  `_days_since_test_health_carve`'s event backscan.
- GA4's gate-verify cadence (`config.py` `gate_verify_interval_days`,
  `daemon._days_since_gate_verify`, `reconcile.py` module-contract item 16) is
  a near-exact cadence template.
- `2-product-definition.md` has the structure the gap-audit reads:
  `features[]` each with `id` / `title` / `acceptance[]` / `status`.
- **Better than claimed:** `component` is a *top-level handoff frontmatter
  field*, not merely a backlog key — so the verdict-audit's component grouping
  genuinely does not need `P42`.

**Wrong ❌ — corrected below**
1. *"Reuse the changed-executable-lines figure `coverage_gate` already computes
   per merge — reused, not a new metric."* There are **two unrelated
   `GateResult` classes**. The persisted one (`types.py`) carries
   `gate_id/phase/commit/exit_code/started/ended/environment/artifacts/output_tail`
   — **no line counts**. `coverage_gate.GateResult.changed_executable` is
   transient, computed in-process and discarded. Worse, `phase` is
   `implementation|review|pre-merge|post-merge|mutation`, so summing per-gate
   figures would count the same lines **up to 4×**.
   → **Corrected:** the cadence counter comes from git, not from a gate
   artifact. See GAP1 below.
2. *"Wave 2 parallel OK — disjoint primary files."* The two packages share
   `config.py` **and** `daemon.py`. That is the same collision profile that
   produced a real hand-resolved `adapters.py` conflict in the DRY /
   incapable-class round.
   → **Corrected:** still run in parallel, but each package is assigned a
   **different insertion region** in the shared files so git auto-merges.
3. *GE0 as a standalone "activity-cadence infra" package.* It would add a
   `ReconcileInput` field and a policy knob that **nothing reads** until GE1
   merges — a defined-but-unwired extension point, exactly what P43's guard
   exists to prevent, and its only available oracle would assert plumbing
   rather than behaviour (hollow, against F005). **GA4 — the closest
   precedent — shipped knob + daemon helper + reconcile field + trigger +
   executor as ONE package.**
   → **Corrected:** GE0 is folded into GE1 as a single vertical slice.

Minor: the 07-26 text said `status: done`; the real schema enum is
`planned|building|shipped`. The audit reads **`shipped` only** — `planned` and
`building` features are legitimately incomplete, and auditing them would
manufacture busywork.

## Design decisions (locked)

1. **No per-path sandbox enforcement exists** for any dispatch role.
   `scope.touch`/`scope.forbid` is prompt instruction plus review judgment;
   the only sandbox is the coarse CLI-level `--sandbox workspace-write`. D-R8's
   "mechanical enforcement" is therefore necessarily **post-hoc** — check what
   a repair touched *after* it lands, never a preventive block.
2. **The gap-engine adds no new dispatch mechanism.** It is a sibling
   `kind == "gap-audit"` branch alongside `kind == "test-health"`. `types.py`'s
   frozen `Role` enum is untouched.
3. **Activity-counted cadence, not calendar** (operator correction, 2026-07-26)
   — the system can idle for days, and a calendar cadence would spend carve
   budget re-auditing an unchanged codebase. The counter is
   `git diff --numstat <recorded head_sha>..HEAD -- <source paths>`, summing
   added+deleted, scoped to **production source only** (operator decision,
   2026-07-27: a docs-heavy or test-only week must not trigger a code-gap
   audit that has no code delta to find gaps in).
4. **Verdict-audit runs BLIND FIRST** — given only the handoff's oracles and
   the final merged diff, it forms an independent judgment BEFORE being shown
   the recorded verdict. Same non-anchoring principle as D-R3's escalated
   review.
5. **Component-level audit granularity**, not per-handoff — catches cross-task
   drift no single-task review can see. Attribution uses the existing durable
   event log plus git history; `component` is already a handoff frontmatter
   field, so `P42` is a precision improvement, not a blocker.
6. **D-R8's serial/batch coupling is dropped, the knob defaults `True`, and
   invalidation reverts rather than re-labels.** Full rationale in
   `docs/routing-model-redesign.md` §D-R8 "(refined 2026-07-27)".

## Sequencing

```
Wave 1 (parallel — different insertion regions in the shared files):
  GAP1 — activity-counted gap-audit carve trigger   [was GE0 + GE1]
  DR8  — bounded reviewer repair
Wave 2 (after GAP1 merges):
  GAP2 — verdict-audit extension                    [was GE2]
```

Every branch is cut from a `main` that already carries the DRY and
incapable-REJECT_CLASS merges (`4c480530` or later).

**Region assignment (the anti-collision device):**

| File | GAP1 | DR8 |
|---|---|---|
| `config.py` | after `gate_verify_interval_days` (~170) | after `gate_diagnosis_after_failures` (~198) |
| `daemon.py` | ~1080, 1793-1870, 3600-3660, 4005 | ~5300-5600, 6433 |
| `reconcile.py` | owns it | forbidden |
| `adapters.py` | forbidden | owns it |

## GAP1 — activity-counted gap-audit carve trigger

**Files:** `config.py` (`gap_audit_after_changed_lines: int = 0`,
`gap_audit_source_paths: list[str]`), `schemas/nyxloom-config.schema.json`,
`daemon.py` (`_changed_lines_since_gap_audit` helper + `head_sha` stamped into
the gap-audit `TASK_CREATED` payload + the `kind == "gap-audit"` carve-packet
branch), `reconcile.py` (`ReconcileInput.changed_lines_since_gap_audit`,
module-contract item 17 + trigger), `nyxloom-trove/nyxloom.toml`.

**Two correctness properties that are easy to get wrong and each need their
own oracle:**

- **`None` vs `0` is load-bearing.** `None` means "never run" and therefore
  **fires** the audit. So a transient git failure must return **`0`**, not
  `None` — otherwise an unrelated git hiccup spends carve budget. `0` is the
  fail-safe answer; log a warning and never raise.
- **Trigger ordering is correctness, not style.** Read the ORDERING rationale
  at `reconcile.py:280-288`. The gap-audit trigger must be evaluated **after
  item 15's test-health block and before item 9's headroom refill**. Item 9's
  condition is true on essentially every pass of an active project, so a rare
  cadence trigger placed after it would lose the single carve slot forever and
  silently never fire.

It is a real carver dispatch, so unlike item 16 it **must** participate in the
single-carve-authority mutex and all shared guards (`project_paused`,
`carve_in_flight`, `carve_dispatch_planned`, `budget_allows`,
`frontier_route_available`) — item 15 is the structural template, item 16 only
the cadence template.

**Dogfood entry held at 0.** `nyxloom.toml` gets the knob with the intended
value (2000) *documented in the comment* but **set to 0**, following exactly
the pattern `test_health_interval_days = 0` already uses in that file: item 17
is gated by its own cadence, not by `carve_ahead_target`, so a live value
would fire a brand-new carve the moment the project unpauses — precisely what
the convergence freeze exists to prevent. Restore together with
`carve_ahead_target`.

**Oracles:** fires at/above threshold; does **not** fire below it *even with
large elapsed calendar time* (this is the actual behavioural distinction from
its `*_interval_days` neighbours and must be asserted explicitly); `None`
fires; disabled is byte-identical to the pre-change action list; mutex
respected; ordering beats item 9; git failure returns `0` and does not fire;
`head_sha` round-trips through the payload; the carve packet names only
`shipped` features and carries the explicit carve-nothing authorization
(with a `planned` feature as the negative).

## DR8 — bounded reviewer repair

**Files:** `config.py` (`reviewer_repair: bool = True`,
`reviewer_repair_paths: list[str]`), `schemas/nyxloom-config.schema.json`,
`adapters.py` (`build_dispatch(repair_allowed=...)` + a budgeted
`REVIEW_INDEPENDENT` prompt append), `daemon.py` (record `pre_review_sha` at
review launch; post-hoc scope check, revert, and forced outcome).

**The budgeted append must use the `argv_max - 200` margin**, not a bare
`<= argv_max` check — that margin protects a real past incident (a long-path
reviewer prompt overflow that permanently stranded review dispatches), guarded
by `test_review_independent_prompt_stays_under_argv_max_with_real_paths`. If
the note does not fit, skip it silently; never truncate mid-sentence into a
permission grant.

**Enforcement:** baseline `pre_review_sha` → `git diff --name-only
<baseline>..HEAD` → every touched path must match a `reviewer_repair_paths`
glob. In bounds → ordinary `APPROVED` (verified by the ordinary full gate
rerun; **no new verification layer**, per `reconcile.py` module contract item
13). Out of bounds, **or baseline absent** → `git revert` the reviewer's
commits, record the invalidation durably, and force `REJECTED` +
`REJECT_CLASS: fixable` via a daemon-recorded override that takes precedence
over the text scan (the committed `<task>-REVIEW.md` still literally says
`VERDICT: APPROVED (repaired)`). Empty diff → mis-stamped verdict, not a
violation, handle as ordinary `APPROVED`.

## GAP2 — verdict-audit extension (Wave 2)

Extends GAP1's dispatch (or a sibling `kind == "verdict-audit"` — the
implementer's call, based on how much the two prompts actually share):
sample recently-`COMPLETED` tasks up to the activity threshold, grouped by the
handoff's `component` field; give each sample **only** the handoff's oracles
and final diff — never the recorded verdict — to render an independent
judgment; then compare. `DISPUTED` samples become carve candidates through the
same envelope GAP1 uses.

**Oracle:** blind judgment disagreeing with a pre-recorded `APPROVED` produces
a `DISPUTED` carve candidate; agreement produces nothing; and a test proving
the auditor's dispatch is genuinely built **without** the recorded verdict in
its prompt — assert on the prompt string, not on an instruction to ignore it,
mirroring how the reviewer-escalation package proves non-anchoring.

## Worktree + gate directive (per package)
```
git worktree add -b feat/<slug> .worktrees/<slug> main
docker run --rm -v /home/vb/volkb79-2/vbpub:/workspaces/vbpub tester-unified:local bash -c \
  'cd /workspaces/vbpub/.worktrees/<slug>/nyxloom && PYTHONPATH=src /opt/tester-venv/bin/python -m pytest tests -n 4 -q --cov=src/nyxloom --cov-report=json:/tmp/cov-<slug>.json; echo PYTEST_EXIT:$?; PYTHONPATH=src /opt/tester-venv/bin/python -m nyxloom.coverage_gate --base main --coverage-json /tmp/cov-<slug>.json --source src/nyxloom; echo GATE_EXIT:$?'
```

## Explicitly deferred (do not fold in)
- `P42` (first-class component/file mapping) — precision improvement for
  GAP1/GAP2, confirmed **not** a blocker.
- A batch/parallel-scheduling variant of DR8 — obsolete: the scheduling-mode
  coupling was dropped entirely, see §D-R8 "(refined 2026-07-27)".
