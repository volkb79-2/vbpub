# Controller implementation rules

## House rules for every implementer brief (CTL-50)

Paste this block verbatim into section 6 of every brief. Where an older rule
below disagrees, this block wins.

1. **Compile and test remotely before every commit, serially.** Use
   `scripts/controller/dev -p <project> <verb> …` (the remote capped dev
   container; profiles in `scripts/controller/dev-profiles.toml`, guide in
   `scripts/controller/docs/DEV.md`; `hostobs/tools/dev` is a shim onto it).
   A project without a profile is not run before the commit, only through its
   registered lane. On exit 75 (not admitted: host memory pressure) or exit 3
   for a transport failure or for another dev call on the same worktree in
   progress, retry once, then report it. Any other exit 3 (the host user or
   identity refusal, a failed sync step) is never retried: report it. A dev run is never
   gate evidence: evidence is only a registered lane on the exact final head.
2. **Never use `hx.sh`, `ssh` or `docker` directly, and never run `pytest`,
   `cargo` or a project test script in the devcontainer** (it is cockpit-only;
   killing its PID 1's child stops the whole cockpit).
   `scripts/controller/gate-run.sh <lane>` is allowed: the lane runs in its own
   container. Code that sends signals to processes (kill, pkill, process
   groups, pidfds) runs only inside the dev container or a registered lane's
   container (both have a private PID namespace), never in the devcontainer.
3. **Contract and size.** The brief states a contract (allowed files and
   behaviour) and a target size. Review runs at most 2 rounds against that
   contract; a finding outside it is closed by design and filed in the tool's
   backlog, not fixed in the package.
4. **Explicit effort.** The caller launches with an explicit
   `CODEX_EFFORT=max` (pi: an explicit `PI_THINKING=<value>` stated in the
   brief) until routes.toml lands (CTL-39).
5. **Unique run names** (`<label>-$(date -u +%Y%m%dT%H%M%S)`), never reused.
   Every Codex exec path derives `.last.txt` from that invocation's stream on
   normal and signal exits; verify the result from git before publication.
6. **Finish with DONE, PARTIAL or BLOCKED** plus the finalization: what is done
   and verified (heads, gate logs), what is half-known, the exact next step,
   and facts that exist only in the conversation. A budget or context stop is
   PARTIAL with an estimate of the remaining work, never BLOCKED.

## Standing rules

- The devcontainer is a cockpit. Gate evidence comes only from the registered
  `run-gate` lane in `tester-unified`; pre-commit compile and test runs go
  through the dev tool (house rule 1). Never run a test suite in the cockpit. Take a gate slot through the gate wrapper (below) and run one lane
  at a time per job.
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
- Run each registered lane through the gate wrapper, from the project
  directory, never more than two lane containers at once:

  ```bash
  scripts/controller/gate-run.sh <lane>
  ```

  It takes `CONTROLLER_GATE_LOCK`, else `CONTROLLER_GATE2_LOCK` (and the further
  slots when `CONTROLLER_GATE_SLOTS` raises the count), else polls all of them
  and takes whichever frees first; the arguments and the working directory are
  exactly `run-gate`'s. Wait for a lane in the FOREGROUND of a tool call
  (a bounded poll loop) and never end a run while a lane is queued. Read the
  verdict in a separate step. A stale in-flight record left by a dead client is
  reported, never deleted by hand without saying so.
- Do not launch other agent sessions (Claude, Codex, OpenCode or any reviewer)
  from inside a job: reviews are arranged only by the controller. A job that
  needs a second opinion says so in its report; a self-arranged review is not
  independent and is ignored for merge purposes.
- Every Bash call carries a `description` that states its intent in plain
  words; successor extracts and audits depend on it.
- Do not ship unrun code: if a gate cannot run yet, still run the focused test
  files of your change serially through the dev tool where house rule 1
  provides one (never in the cockpit); otherwise say that nothing ran before
  the commit, and report exactly what ran. Coverage gates
  hide never-run branches, so keep the static undefined-name test green.
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
- Harness environment limits measured here (re-measure per harness before
  generalising; a stale limit is dropped, not carried): a Codex worker under a
  read-only sandbox fails in this devcontainer because its nested bubblewrap
  cannot mount `/proc` (2026-11-14, CTL-10), and the Claude Code Bash tool
  rejects a long foreground sleep such as `sleep 40` (2026-11-14, CTL-10). Use a
  shorter command or a background command there, and pick an explicit sandbox
  where the read-only one does not start.
- Review a change on a separate route and against the exact final commit. The
  review must critique the design as well as list defects.
