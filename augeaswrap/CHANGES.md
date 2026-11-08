# Changelog — Augeaswrap

## Unreleased

- Add the guarded applier core with strict TOML requests, `file` and `check`
  resources, plan/apply/revert/journal commands, pre-image journals, stale-plan
  checks, and explicit revert.
- Move the shared resource protocol and value types into `augeaswrap`; resource
  work for HC-1b-u/-s builds against that frozen contract.
- Tighten protocol input checks for NUL-bearing writers and non-string change
  actions without changing resource method signatures or dataclass fields.
- Keep the standalone endpoint publisher and its paths; label the generated
  environment block with `augeaswrap.endpoints`.
- Emit one JSON document per CLI invocation, with plan and result together for
  apply and revert.
- Mark an unobservable failed-commit post-image in the journal and refuse an
  unsafe explicit revert.
- Preserve transaction allocation order for same-second and clock-rollback
  cases; bind each journal to its selected root.
- Record address-bound post-actions and replay recorded actions during revert.
