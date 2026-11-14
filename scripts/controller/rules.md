# Controller implementation rules

- The devcontainer is a cockpit. Tests and gates run only through the
  registered `run-gate` lane in `tester-unified`; never run a test suite in the
  cockpit. Acquire the shared gate lock and run one lane at a time.
- Keep run logs, run streams, host config, queue state, release archives, and
  reports outside the repository (the controller's own `plan.yaml` and
  `.controller-state/` are the one gitignored in-checkout exception, see the root
  `AGENTS.md`) under `CONTROLLER_HOME` or their explicitly
  requested absolute destination. The default is `$HOME/controller`.
- Do not commit host addresses, server IDs, token paths, SSH key paths, or
  private credentials. Put local values in ignored config files and commit
  placeholders only.
- Run status and job checks are read-only. A missing value that has an
  authoritative source must be read or rejected; do not invent a fallback.
- Tests must be deterministic under arbitrary host load. Use fake roots and
  fixtures instead of fixed sleeps or live host dependencies.
- Capture the exit status of the process being judged. For a pipeline, enable
  `pipefail` or capture `${PIPESTATUS[@]}` immediately; a later wrapper's
  status does not prove the child's result.
- A run without a completion marker is not complete. Treat old output with no
  marker as stalled, not successful. Reports must be written at absolute
  paths and contain their evidence before the task ends.
- Every commit carries exactly one route-specific trailer. Audit all commits
  in the task range, including merge commits.
- Exact OpenAI route trailers are `Co-Authored-By: Codex gpt-6-luna
  <noreply@openai.com>` for Luna, `Co-Authored-By: Codex gpt-6-sol
  <noreply@openai.com>` for Sol (the Codex review route: Sol reviewers hold the
  same allowances as other reviewers, including fixing small things in the
  reviewed branch under their own trailer; every commit still carries exactly
  one trailer) and `Co-Authored-By: Codex deepseek-v4.1-flash
  <noreply@openai.com>` for DeepSeek or OpenCode.
- The Codex DeepSeek routes are `deepseek` (automatic account selection),
  `deepseek-volker`, and `deepseek-github2` (pinned accounts). The two pinned
  forms use the same DeepSeek trailer above.
- Pi's favored Open Design route is `deepseek-pi` (automatic account
  selection), with `deepseek-pi-volker` and `deepseek-pi-github2` as pinned
  forms. They use the same DeepSeek trailer above.
- Run each registered lane under the shared lock, one lane at a time:

  ```bash
  flock "$CONTROLLER_HOME/gate.lock" nice -n 10 run-gate <lane>
  ```
- Make the final required `origin/main` merge the last content change, then
  run gates on a clean tree and confirm every gate log names the final head.
- Keep the task branch isolated. Never push, tag, release, or merge it into
  shared main unless the operator separately assigned that lifecycle action.
- Before a queue update, set pause flags and drain every current lane before
  stopping the queue loop. A stale `.current` marker requires evidence-based
  diagnosis. Stop and wait for the PID file to disappear before staging a
  same-filesystem temporary file and atomically renaming it into place.
- Wait loops inspect files and process state directly. Do not use a piped
  predicate under `pipefail`, and do not treat a fixed sleep count as proof
  that a marker or process has disappeared.
- For `rel-*` worktrees, preserve each project `logs/`, `artifacts/`, and
  `evidence/` under the local release archive before any workflow can remove
  the worktree. Do not run `git clean -x` in a checkout that holds artifacts.
- Review a change on a separate route and against the exact final commit. The
  review must critique the design as well as list defects.
