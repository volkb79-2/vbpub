# ciu-P50 — LOG

Package: `nyxloom-trove/handoffs/ciu-P50-ciu94-ciu95-memory-min-guaranteed-slice.md`
Branch/worktree: `ciu-p50-memory-min-guaranteed-slice` (`.worktrees/ciu-p50-memory-min-guaranteed-slice`)
Base commit: `25d82dfa` (docs(ciu): apply adversarial carve-review fixes to ciu-P50)
Implementer: fresh session, no prior context beyond the handoff + the live repo.

---

## Reading pass (context, in the handoff's order)

1. `KNOWN_ISSUES_TODO_BACKLOG.md` — **contract deviation worth naming, not a
   BLOCKED trigger.** The handoff says to search for `## CIU-94` / `## CIU-95`
   sections ("both have full sections, not just table rows"). There are no such
   headings: both entries exist ONLY as table rows (lines 544 and 546), each
   carrying its full design reasoning inline. Nothing was missing — the whole
   incident/design text the handoff refers to is in those rows and was read in
   full — so this is a wrong pointer in the handoff, not a missing input.
2. `host-setup/CGROUP-NOTES.md` §"Per-container `memory.min` guarantees" — read
   in full, including "the one invariant that actually matters" (pin
   `dev.slice`'s MemoryMin EXACTLY equal to the guaranteed slice's ceiling,
   never generous) and "Chose (2)" (static ceiling + admission control, not a
   dynamic reconciler). Not touched — forbidden path.
3. `docs/DESIGN-NOTES.md` D2 (full) and D6 (full).
4. `src/ciu/governance.py` — read across the regions named.
5. `src/ciu/deploy.py` — `governance_slice_preflight`, its `_run()` call site,
   `action_deploy`'s per-entry loop, `resolve_selection_health_containers`.
6. `host-setup/scripts/mdt-slice-audit.py` — `read_protection`/`ancestors`/
   `audit_tree`.
7. `host-setup/scripts/mdt-apply-dev-caps.sh` — `_apply_container_caps`'s
   `docker inspect` → `/proc/<pid>/cgroup` → `.scope`-trim → `set-property
   --runtime` chain.
8. `docs/SPEC.md` S15.16 and S15.21.
9. `tests/tests/test_ciu_governance.py` — `TestCheckSliceUnit`,
   `TestCheckSliceMemoryMin`, `TestSliceAncestorChain` idioms.
10. `tests/tests/test_ciu_deploy_actions.py` — `_plain_config`,
    `_governance_selection_rendered{,_mem_min}`, the `fail_mem_min` idiom.

Also read `nyxloom-trove/decisions.md` **D-012** (build in v7 now).

---

## Commits

### 1 — `feat(ciu): CIU-94 + CIU-95 -- per-container memory.min admission control, injection, and the memory_recursiveprot/downward-enumeration primitives (ciu-P50)`

Self-hash: `a4f5aa94`

Everything in Parts A-F landed as one commit: the parts are not independently
green (Part C's admission control is built on Part A's walk, Part D's call site
sits in the same loop as Part C's, and the SPEC/DESIGN-NOTES/CHANGES text
describes all of them), so splitting would have produced intermediate commits
that fail their own gate.

**Part A** (`src/ciu/governance.py`) — `CGROUP_ROOT` (:307),
`slice_cgroup_path` (:1004), `_read_memory_min_bytes` (:1026),
`enumerate_slice_children` (:1051).

**Part B** — `check_memory_recursiveprot` (`governance.py:1105`); call site in
`governance_slice_preflight` (`deploy.py:1568`), `severity="ERROR"`, guarded by
`if mem_min_required:`.

**Part C** — `check_mem_min_admission` (`governance.py:1170`);
`deploy.mem_min_admission_check` (`deploy.py:1236`) called from
`action_deploy`'s per-entry loop immediately before `_run_stack`
(`deploy.py:2079`), guarded `rendered is not None and not no_preflight and not
dry_run`. The optional shared helper the handoff recommended was implemented:
`deploy._resolve_entry_governance` (`deploy.py:1186`), and
`governance_slice_preflight`'s own loop body was rewritten to call it — behavior
unchanged (verified by the pre-existing preflight tests, all still green).

**Part D** — `container_transient_scope` (`governance.py:1253`),
`set_scope_memory_min` (`governance.py:1296`);
`deploy.apply_mem_min_injections` (`deploy.py:1315`) called right after a
successful `_run_stack` where `deployed.append(...)` sits (`deploy.py:2108`),
guarded only by `rendered is not None and not dry_run` — deliberately NOT by
`no_preflight`.

**Part E** — the severity table was verified against the implementation
line-by-line; see REPORT.

**Part F** — `docs/SPEC.md` S15.22 (:3580) + S15.23 (:3655) before Appendix A;
S15.16 companion amendment; `docs/DESIGN-NOTES.md` D9 (:532);
`GOVERNANCE_DEFAULTS["mem_min"]` comment block; `composefile.py` docstring line;
`docs/CONFIG.md` `mem_min` paragraph; `CHANGES.md` `[Unreleased]`;
`KNOWN_ISSUES_TODO_BACKLOG.md` CIU-94/CIU-95 → FIXED with real file:line
citations.

Tests: `tests/tests/test_ciu_governance.py` (+6 classes),
`tests/tests/test_ciu_deploy_actions.py` (+14 tests, and 5 pre-existing tests
gained an explicit `check_memory_recursiveprot` monkeypatch — see REPORT, this
is an anti-pattern-E fix, not evasion).

Fixture: `test-repo/infra/db-core/ciu.defaults.toml.j2` gained a
`[db_core.governance]` table declaring `mem_min = "128m"` on an explicit
`cgroup_parent = "dev-memory_min_guaranteed.slice"`.

Gate on this commit alone: **FAIL** — see commit 2. All 3639 tests passed; the
lane's R1 changed-line floor (100%, branches required) reported 90.26%.

---

## Deviations / judgment calls (all reported, none improvised past)

- The handoff's backlog-section pointer is wrong (table rows, not `##`
  sections). Not blocking — see above.
- Fixture stack choice: `infra/db-core`, not `infra/redis-core`. Reasoning in
  the REPORT.
- Five pre-existing deploy tests were edited to pin
  `check_memory_recursiveprot`. Reasoning in the REPORT.
- Live verification (real slice, real `systemctl show`) is NOT done and is NOT
  claimed — explicitly out of this dispatch's oracle contract per the handoff.

### 2 — `test(ciu): ciu-P50 -- close the changed-line coverage gaps the first gate run found`

Self-hash: `b57cc41c`

Commit 1's own error/degradation paths had no oracle: `_read_memory_min_bytes`'s
unparseable-value branch, `check_memory_recursiveprot`'s unreadable-`/proc/mounts`
branch, `mem_min_admission_check`'s malformed-size raise,
`apply_mem_min_injections`' three degradation paths, and the `[S15.23]` refusal
handler wired into `action_deploy`'s per-entry loop (both branches of its
`if not ignore_errors`). Eight behavioral tests added, no pragma anywhere.

Gate: `./run-gate.py ciu --worktree /workspaces/vbpub/.worktrees/ciu-p50-memory-min-guaranteed-slice` — **PASS (exit 0)** at commit `b57cc41c`, verdict read in a separate step from `.assay/verdict-ciu.json`: R0 PASS, R1 PASS, changed-line coverage **100.0%**, branches **84/84**, `mode=changed_lines fail_under=100.0 require_branch=True`, base `faaa49c2` (merge-base). 3647 tests, all passing.

Verdict read in a separate step from the JSON artifact, never off a piped tail
(vbpub AGENTS.md, LESSONS L4).

---

## Final state

- Branch `ciu-p50-memory-min-guaranteed-slice`, two commits, clean tree.
- **Not merged, not pushed** — per the handoff, a fresh adversarial reviewer
  verifies first.
- No BLOCKED condition was hit. No forbidden file was touched.
- Checkpoint clause not triggered (the package completed well inside the
  ~120k-context / ~60-call budget).
