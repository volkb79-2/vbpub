# P49 - Governed systemd memory.high Adjustment

## Goal

Build on reviewed P46 with a single Finding-D-safe resource governance action:
structured preview/execution of `systemctl set-property ... memory.high=...`,
never a raw cgroup write.

## Dependency And Workflow

- Starts only after reviewed P46 is merged.
- Branch: `feat/groop-p49-systemd-memory-governance`
- Worktree: `.worktrees/-groop-p49-systemd-memory-governance`
- Touch only `groop/**`; write P49-LOG.md/P49-REPORT.md; commit, do not merge.

## Requirements

- Replace the unsafe composite preview target for systemd-set-property with
  structured unit/property/value inputs. Preserve compatibility only if it can
  be parsed unambiguously and safely; otherwise fail clearly.
- Initially allow only `memory.high`; accept `max` or a canonical positive byte
  value with overflow/range checks. Reject percentages, signs, whitespace,
  extra assignments, arbitrary properties, and option/path-like units.
- Read and show the current value plus existing origin/drift classification.
  Revalidate the current value immediately before execution and return stale
  plan if it changed.
- Default `--runtime` for transient/container scopes and persistent mode for
  slice/service units, while allowing an explicit safe operator choice. Preview
  must display exact argv, old/new value, and persistence semantics.
- Reuse P46 root/admin/typed-confirmation, absolute argv, timeout, result bounds,
  and fail-closed audit contract. Never write cgroupfs directly.
- Add fixture origin/current-value readers and injected runner tests for gates,
  stale detection, unit/value validation, mode defaults, exact argv, audit, and
  no real systemd mutation. Update governance/operations/readiness/status docs.

## Out Of Scope

- Other properties, automatic recommendations, ancestor propagation, batch
  changes, TUI controls, daemon RPCs, or live destructive acceptance.

