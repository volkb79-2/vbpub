# Codex CLI guide (operating `codex` as an external agent)

**Status:** operational · last-verified 2026-07-25 (codex-cli 0.145.0)

How to drive OpenAI's **codex** CLI as a headless implementation/assessment/review
agent, and the non-obvious lessons learned running it here. Companion to the
reasonix guide; the same *cross-tool operating discipline* (bottom section) applies
to codex, reasonix, and opencode alike.

Codex is an **external** agent — it runs via the shell (`codex exec ...`), NOT through
the Claude Agent/Workflow tools. It knows nothing about ciu, `.worktrees/`, the
`tester-unified` gate, or our conventions unless the prompt says so explicitly.

## Invocation — fresh non-interactive run

```bash
cd <a-real-git-repo>                       # see the sandbox gotcha below
codex exec -m gpt-5.6-sol \                # model (see aliases)
  -c model_reasoning_effort=high \         # effort: low|medium|high
  -s workspace-write \                     # sandbox: read-only|workspace-write|danger-full-access
  -o /path/final-message.txt \             # capture the agent's final message
  - < /path/prompt.md \                    # prompt via stdin ("-"), or pass as a arg
  > /path/run.log 2>&1
```

- `codex exec` is the non-interactive entrypoint. It runs `approval: never` — it
  auto-executes shell commands **within the sandbox**, no prompts. Ideal for
  background/nohup.
- Prompt: as a trailing arg, or `-` to read from stdin (append via stdin even with an arg).
- `-o <file>` writes the agent's **final message** to a file — read the conclusion
  without tailing the whole transcript. Have the agent write real deliverables via
  file tool-calls (they survive a mid-stream disconnect better than a streamed final answer).

## Model + effort ("`<alias>@<effort>`")

Frontier model aliases (bands 1/2/3 low→high capability/cost):

| alias | band | `-m` value |
|---|---|---|
| luna | 1 | `gpt-5.6-luna` |
| terra | 2 | `gpt-5.6-terra` |
| sol | 3 | `gpt-5.6-sol` |

So "**sol@high**" = `-m gpt-5.6-sol -c model_reasoning_effort=high`. Effort is a
first-class `-c` config, values `low|medium|high`. Confirm a model id is real via
`~/.codex/models_cache.json`; the default model+effort live in `~/.codex/config.toml`.

## Sandbox — the bwrap gotcha (cost us a whole run)

Codex sandboxes shell commands with **bubblewrap**. Two rules:

1. **Root at a real git repo.** The working root (`cd` target, or `-C <dir>`) must be a
   git repository. Rooting at a non-repo dir (e.g. `/workspaces`, the mount parent) fails
   at sandbox init: `bwrap: Can't mkdir /workspaces/.git: Permission denied`, aborting
   *before any command runs*. `--skip-git-repo-check` alone does **not** fix this — root
   at an actual repo. For a multi-repo task, root at the repo you'll WRITE to.
2. **`workspace-write` confines writes, not reads.** `-s workspace-write` makes only the
   workspace root (+ `/tmp`, `$TMPDIR`) writable, but the rest of the filesystem stays
   **readable** — so sibling repos outside the workspace are readable, just not writable.
   This is the safe default for a cross-repo *assessment*: root at the repo that receives
   the output, instruct a strict one-file write-discipline, and **verify with
   `git status` across every touched repo afterward** (codex is not a trusted committer).
3. `-C <dir>` sets the working root without `cd`; `--add-dir <dir>` adds an *extra
   writable* dir (use sparingly). `--dangerously-bypass-approvals-and-sandbox` disables
   bwrap entirely — only for an already-externally-sandboxed environment (a devcontainer);
   prefer a proper sandbox root over the bypass.

## Sessions + resume — context is durable, reuse it

Every `codex exec` run **persists its session** to
`~/.codex/sessions/YYYY/MM/DD/rollout-<ISO-ts>-<uuid>.jsonl` (unless `--ephemeral`).
The session **UUID is in the filename** — recover it any time with `ls`.

Resuming replays the whole transcript, so **all context the run gathered (file reads,
analysis) is preserved and reused** — invaluable for expensive read-heavy tasks, and the
reason a mid-task backend failure is recoverable without starting over.

```bash
codex exec resume <session-uuid> [prompt]      # or --last for the most recent
```

**Flag quirk (the reason for this guide):** `codex exec resume` does **NOT** accept
`-s`/`-m` as flags — it errors `unexpected argument '-s'`. Pass model, effort, and
sandbox as `-c` config overrides instead:

```bash
cd <repo> && codex exec resume <uuid> \
  -c model=gpt-5.6-sol \
  -c model_reasoning_effort=high \
  -c sandbox_mode=workspace-write \
  -o final.txt "Continue: <nudge>"
```

Pick the **richest** saved session to resume (highest token count = most gathered
context — check log tails / file sizes), not necessarily the most recent.

## Backend failures are transient — probe, then resume

Observed failure signatures, all **backend-side, not your config**:
- `stream disconnected before completion: Internal server error` (after N reconnects);
- `unexpected status 503 Service Unavailable ... auth error code: ...circuit...open`.

A failed run still **burns tokens** (saw 148k–195k on failed runs), so do not blindly
retry. Instead:
1. **Cheap probe** to detect recovery: `codex exec -m <model> -c model_reasoning_effort=low "Reply with exactly: PROBE_OK"` (short `timeout`). Exit 0 + `PROBE_OK` ⇒ backend healthy.
2. Then **`resume` the richest session** to finish reusing its context (see above).

Keep a durable resume note (session UUIDs + the resume command + the prompt copy) in a
spot that survives job-dir cleanup, e.g. `~/.codex/RESUME-<task>.md`.

## Cross-tool operating discipline (codex · reasonix · opencode)

Shared with the reasonix guide — applies to any external CLI agent:

- **Not a trusted committer.** It runs in a *third* environment (neither our devcontainer
  cockpit nor the `tester-unified` gate). Prefer read-only *assessment*, or re-run its
  self-reported results through our real gate (`./scripts/testing-exec.sh` for dstdns; the
  project's `[gates.*]` for nyxloom) before trusting them. Always `git status` its blast
  radius afterward.
- **Give it everything explicitly.** Exact files/paths to read, the exact scope it may
  write, the real gate command, and a strict "touch only X" rule — it has no ambient
  knowledge of our stack.
- **Cost routing** (per project policy): route *mechanical, tightly-bounded* packages to a
  cheaper external agent; keep high-judgment / frozen-core-adjacent work on the primary
  controller loop with adversarial review.
- **Classify by OUTPUT, not exit code.** A green exit from a third environment is not a
  ship signal; re-verify.

## Lessons-learned log

- **2026-07-25** — `codex exec resume` rejects `-s`/`-m`; use `-c model=…`,
  `-c model_reasoning_effort=…`, `-c sandbox_mode=…`. (This guide's origin.)
- **2026-07-25** — bwrap sandbox init fails if the working root isn't a git repo
  (`Can't mkdir <root>/.git`); root at a real repo, not the mount parent.
- **2026-07-25** — sessions persist under `~/.codex/sessions/…/rollout-*<uuid>.jsonl`;
  resuming a failed run reuses ~all gathered context. Recovered a full 3-repo gate-adoption
  assessment by resuming session `019f988e…` after two backend outages, losing no context.
- **2026-07-25** — `workspace-write` rooted at repo X reads siblings (read-only) but writes
  only X — the right shape for a cross-repo assessment that emits one report into X.
