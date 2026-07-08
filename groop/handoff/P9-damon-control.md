# P9 — DAMON controlled vaddr session (opt-in, root, confirmed)

**Cut:** v1.5. **Depends:** P8 merged. Branch: `feat/groop-p9-damon-control`.
Follow `groop/README.md` workflow protocol.

## Goal

The ONE mutating feature of v1.5: start/stop a groop-owned vaddr DAMON session
for a selected entity, behind root + explicit typed confirmation, leaving all
foreign sessions untouched. This is the controlled counterpart to P8.

## Spec references

§3.6 (control stage), §0.1 (v1.5: "optional controlled vaddr session behind
root and explicit confirmation"), §6.5 (security model: confirmation gates,
audit logging), MEASUREMENTS.md DAMON overhead gate (E11/spec §9 item 14).

## Scope — in

1. `damon/control.py`: allocate a FREE kdamond slot (never touch a busy one —
   refuse if none free), build a vaddr context targeting the entity's pids,
   configurable attrs from `[damon]` config (conservative defaults from the
   spec), start; stop + teardown restores the slot to empty state. Track
   ownership via a marker (groop-owned context naming/state file under
   $XDG_STATE_HOME/groop/) so groop NEVER stops a session it didn't start.
2. Lifecycle safety: TUI exit leaves the session running with a status
   indicator on next start (deliberate: sessions outlive the viewer) plus a
   `groop damon stop --all-mine` CLI to clean up; document this in the
   session-start confirmation text.
3. UI: hotkey on drill-down (root only), typed confirmation modal
   ("start DAMON vaddr on <entity>, ~X% overhead, type START"), status line
   while active, stop flow with confirmation; non-root: action hidden.
4. Audit log: every start/stop appended to $XDG_STATE_HOME/groop/actions.log
   (ts, user, entity, kdamond, attrs) per §6.5.
5. Refuse-paths tested: no free kdamond; entity vanished mid-flight; pids
   changed (container restart) → session marked stale, offer stop; running
   as non-root.

## Scope — out

paddr sessions/auto-start (v2), DAMOS schemes (never in this cut — monitoring
only, no reclamation actions), any other mutation.

## Acceptance

- On the reference host as root: start on a test container, P8 columns
  populate from the groop-owned session, stop tears down cleanly, foreign
  damo session untouched throughout (test alongside one).
- Audit log entries written; confirmation cannot be bypassed by any hotkey
  path (pilot test).
- pytest green; report per README protocol.
