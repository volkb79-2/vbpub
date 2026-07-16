# AI CLI Controller Guide

Status: legacy CLI invocation evidence retained during migration. The
project-neutral architecture, typed state model and deterministic scheduler are
defined under [`nyxloom`](../nyxloom/README.md). Do not copy this file as
the controller architecture for another project.

This guide records the controller workflow used for `topos` handoff packages
across Reasonix, Codex CLI, Claude Code, and OpenCode. It is intentionally
operational: exact worktree boundaries, non-interactive invocation, metrics,
monitoring, review, and merge protocol.

## Controller Role

> **Superseded framing — see `docs/controller-workflow-v2.md` (2026-07-12).**
> In v2 the roles are split by model tier: the **controller** (cheap model,
> Claude Code harness only — the sole CLI with async supervision) *only*
> dispatches, monitors, and routes; it never carves, reviews, or merges. The
> **frontier review/carve session** (Opus-tier) owns architecture, carving,
> review pass #2, merge, and evidence. A flash/pro-tier session must never be
> the merge authority over flash-tier output (P51 replay + P52 false-green
> evidence). This section's original wording described the era when one
> frontier session played all roles; read everything below as the *invocation
> reference* for whichever role runs the command, not as a single-session
> job description.

Implementation agents do bounded packages in dedicated worktrees.

Default package flow:

1. Carve or update `topos/handoff/P<NN>-<slug>.md`.
2. Add a Planned row to `topos/README.md` and `topos/docs/ROADMAP.md`.
3. Commit the carve on `main`.
4. Start a Reasonix agent from `main` in `.worktrees/-topos-p<NN>-<slug>`.
5. Let the agent implement, test, write `P<NN>-LOG.md` and `P<NN>-REPORT.md`,
   and commit its feature branch.
6. Review the diff, patch issues in the feature worktree, rerun tests.
7. Merge with `--no-ff`, validate from `main`, record evidence, and commit the
   evidence update.

## Worktree Protocol

Use repo-local worktrees, not `/tmp`, because `.worktrees/` is gitignored and
available to agents without extra host permission surprises.

```bash
git worktree add -b feat/topos-p28-io-cap-saturation \
  .worktrees/-topos-p28-io-cap-saturation main
```

Each agent prompt should state:

- branch name;
- exact worktree path;
- work only inside that worktree;
- touch only `topos/**`;
- read the handoff and required context;
- run focused tests, full suite, and `py_compile`;
- write log/report files;
- commit the feature branch.

## Starting Reasonix Agents

Fresh implementation agent:

```bash
reasonix run --model deepseek-flash-high/deepseek-v4-flash --max-steps 0 \
  'Implement topos P29 ... follow topos/README.md Workflow protocol exactly ...'
```

Continue the latest Reasonix session to reuse cache:

```bash
reasonix run -c --model deepseek-flash-high/deepseek-v4-flash --max-steps 0 \
  'Continue with topos P28 ...'
```

Parallel two-stream run, when packages do not overlap:

```bash
reasonix run -c --model deepseek-flash-high/deepseek-v4-flash --max-steps 0 --dir /path/to/vbpub \
  'Implement topos P32 ... worktree .worktrees/-topos-p32-daemon-status ...'

reasonix run --model deepseek-flash-high/deepseek-v4-flash --max-steps 0 --dir /path/to/vbpub \
  'Implement topos P33 ... worktree .worktrees/-topos-p33-release-smoke ...'
```

Useful help:

```bash
reasonix --help
reasonix run --help
```

Important flags:

- `--model PROVIDER/MODEL`: select an explicit model-and-effort provider alias.
- `--max-steps 0`: no artificial step cap.
- `-c` / `--continue`: resume latest saved session and preserve useful cache.
- `--resume PATH`: resume a specific saved session.
- `--copy`: duplicate a resumed session if the original is locked.
- `--metrics PATH`: write token/cache/cost summary JSON if a durable metric
  artifact is useful.
- `--dir PATH`: run from a specific project root.

### Model and effort tiers

Reasonix effort is encoded in provider aliases in `~/.reasonix/config.toml`, so
controller runs are reproducible without an interactive `/effort` prelude. The
configured selectors are:

| Selector | Intended use |
| --- | --- |
| `deepseek-flash-auto/deepseek-v4-flash` | Let Reasonix select Flash effort |
| `deepseek-flash-high/deepseek-v4-flash` | Default for bounded, low-risk implementation and documentation |
| `deepseek-flash-max/deepseek-v4-flash` | Medium work where more effort is useful but Pro is not justified |
| `deepseek-pro-auto/deepseek-v4-pro` | Let Reasonix select Pro effort |
| `deepseek-pro-high/deepseek-v4-pro` | Concurrency, lifecycle, protocol, security, or multi-module reconciliation |
| `deepseek-pro-max/deepseek-v4-pro` | Exceptional ambiguous or adversarial architecture/recovery work |

Validate configuration before assigning work:

```bash
reasonix doctor --json
reasonix run --model deepseek-pro-high/deepseek-v4-pro \
  --max-steps 1 'Reply with exactly ALIAS_OK.'
```

On 2026-07-10 all four selectors passed a live `ALIAS_OK` probe. Identical
minimal probes cost about `¥0.012` for Flash and `¥0.036` for Pro under the
configured price table, so the observed local premium was about 3x. Treat the
price table as an estimate and verify it against actual provider billing.

Do not infer that a higher tier removes the need for controller review. In a
same-base P51 replay, Pro High found and repaired several of its own mistakes,
but its committed result still passed its full suite while missing important
blocked-producer shutdown, history-gap, terminal-state, and resource-bound
contracts. Choose tiers by expected total cost: agent run plus review and the
probability-weighted repair/rerun cost.

## Starting Codex CLI Agents

Use native Codex when the requested OpenAI model is unavailable through the
configured OpenRouter account or when native Codex harness behavior is part of
the comparison:

```bash
codex exec --json --dangerously-bypass-approvals-and-sandbox \
  -m gpt-5.6-luna -c 'model_reasoning_effort="medium"' \
  -C /path/to/worktree 'Implement PNN ...'
```

- `-C` establishes the actual workspace root; still repeat the exact worktree
  and branch in the prompt.
- `--json` emits machine-readable events. The final `turn.completed` event
  includes input, cached-input, output, and reasoning token totals.
- Use `--dangerously-bypass-approvals-and-sandbox` only inside an already
  isolated, trusted worktree/container. It is not a substitute for isolation.
- Native subscription runs may report tokens but no per-run cash cost. When
  comparing value, label any catalog-price calculation as an estimate.
- **Incident, 2026-07-13 (pwmcp P03 terra-med / luna-high legs):** dispatched
  with plain `codex exec -c ... --model ...` (no bypass flag), which defaults
  to `--sandbox workspace-write`. That sandbox scopes writes to `[workdir,
  /tmp, $TMPDIR]` — but a `git worktree`'s actual index/metadata lives at
  `<main-repo>/.git/worktrees/<name>/`, **outside** the worktree's own
  directory. Both legs completed real implementation + self-review work but
  could not `git commit` (`index.lock` creation denied) or reach
  `docker.sock`, and the controller had to finalize their commits externally.
  This is not a model-quality signal — it is a harness/sandbox-boundary
  mismatch specific to git worktrees. Fix: always launch codex worker legs
  against a git worktree with `--sandbox danger-full-access` (narrower than
  full bypass — keeps approval semantics, widens only the disk boundary) or
  `--dangerously-bypass-approvals-and-sandbox` per the existing guidance
  above; do not use bare `workspace-write` for any codex run whose `-C`/`--dir`
  is a `git worktree add`'d path.

## Starting Claude Code Agents

For a non-interactive Claude implementation run:

```bash
claude -p --output-format json --dangerously-skip-permissions \
  --model claude-sonnet-5 --effort medium \
  --name PNN-sonnet5-medium 'Implement PNN ...'
```

Run the command with its process working directory set to the exact worktree.
The final JSON result includes `num_turns`, token/cache usage, `modelUsage`, and
`total_cost_usd`. Use `--dangerously-skip-permissions` only under the same
trusted-isolated-worktree rule as Codex bypass mode.

## Starting OpenCode Agents

OpenCode supports explicit directory, model, effort variant, session export,
and non-interactive permissions:

```bash
opencode run --auto \
  --model openrouter/z-ai/glm-5.2 --variant high \
  --dir /path/to/worktree --title PNN-glm52-high \
  'Implement PNN ...'
```

Operational points:

- `--dir` is the project boundary. Repeat it in the prompt and verify `pwd`,
  branch, and `git status` in the resulting transcript.
- `--auto` approves requests that would otherwise ask, but explicit permission
  denies still apply. For durable policy, use `permission` in `opencode.json`;
  deny destructive commands and `git push` even for automated agents.
- OpenCode permissions distinguish `read`, `edit`, `bash`, `task`, network
  tools, and `external_directory`. Paths outside `--dir` require an explicit
  external-directory rule.
- `opencode session list`, `opencode export SESSION`, and `opencode stats
  --models` provide durable evidence. `~/.local/state/opencode/model.json`
  records recent model/variant selections, but is not the authorization source.
- Model catalog presence does not prove a usable route. On 2026-07-12,
  OpenRouter authentication and a GLM-5.2 control request succeeded while both
  `openai/gpt-5.6-luna` and `anthropic/claude-sonnet-5` failed before generation
  with `No allowed providers are available for the selected model`. This is an
  OpenRouter provider/routing-policy failure, not a worktree-permission failure.
  Use native Codex/Claude for those models unless the OpenRouter routing policy
  is changed and a minimal live probe succeeds.

Official references: [OpenCode CLI](https://opencode.ai/docs/cli/) and
[OpenCode permissions](https://opencode.ai/docs/permissions/).

## Cache Strategy

Reasonix shows token/cache/cost lines while running, for example:

```text
179872 tok · in 179715 (179456 cached / 259 new) · out 157 · ¥0.0042
```

The cached/new split is the key number. Continuing related Topos/TUI packages
with `reasonix run -c` produced high cache reuse and low marginal cost. For a
separate area of work, start a fresh Reasonix session so it builds an
independent cache around that domain.

Practical pattern:

- same stream, similar files: use `run -c`;
- independent stream: use fresh `reasonix run`;
- parallel streams: use separate feature branches and worktrees, then serialize
  merges on `main`.

Recent P32/P33 example:

- P32 continued the daemon-context session with `run -c`; its progress lines
  showed roughly `315k` input tokens with more than `314k` cached by the end.
- P33 used a fresh release-confidence session; it built a separate cache around
  acceptance docs/tests and ended around `104k` tokens with most later input
  cached.
- The controller still reviewed both branches completely. Cache efficiency is a
  cost signal, not a correctness signal.

When durable stats matter, add a metrics artifact:

```bash
reasonix run --metrics .worktrees/reasonix-pNN-metrics.json ...
```

If no metrics file is used, read the terminal progress line:

```text
315444 tok · in 315148 (315008 cached / 140 new) · out 296 · ¥0.0070
```

Interpretation:

- total tokens used by that session line;
- input tokens, split into cached and new;
- output tokens;
- estimated cost shown by Reasonix.

The cached/new split is the most useful controller signal. A high cached count
means continuing that session is cheap for nearby work; a high new-token count
means the task probably belongs in a fresh domain-specific session.

## Monitoring

Prefer sparse monitoring. For long runs, wait for completion or poll every few
minutes:

```bash
# Codex tool-level equivalent:
# poll the running session without writing input
```

Avoid detailed commentary for every agent step. It costs controller context and
usually does not improve the result. Useful updates are:

- agent started and worktree created;
- agent found a real blocker;
- agent finished with commit/test evidence;
- controller found a review issue.

If Reasonix completes real work but gets stuck satisfying its own internal
`complete_step` bookkeeping, stop it after confirming the feature branch is
committed:

```text
Ctrl-C
```

Then continue with controller review.

Logs:

- Every handoff should require `topos/handoff/reports/P<NN>-LOG.md`.
- The log should contain observable actions, commands, files changed, decisions,
  blockers, and next steps.
- Do not trust dates or test counts until the controller verifies them.
- After controller patches, append the controller validation to both `LOG.md`
  and `REPORT.md`.

## Review Checklist

Always review before merging. Reasonix is useful, but not a trusted committer.

Check:

```bash
git -C .worktrees/-topos-pNN-slug status --short --branch
git -C .worktrees/-topos-pNN-slug log --oneline --max-count=5
git -C .worktrees/-topos-pNN-slug diff --stat main...HEAD -- topos
git -C .worktrees/-topos-pNN-slug diff --find-renames main...HEAD -- topos
```

Common issues found:

- wrong dates in logs/reports;
- overclaimed evidence, such as "after merge" before merge;
- missing handoff requirements;
- tests that pass but do not assert the important behavior;
- stale docs after a review patch;
- subtle correctness gaps around edge cases.
- environment-specific claims, such as dependency failures in an agent venv
  that do not reproduce in the controller validation venv.

Patch in the feature worktree, then commit a controller review commit:

```bash
git add topos/...
git commit -m "test(topos): polish PNN <topic> review"
```

## Validation

Use the package venv if available, but force imports from the checkout being
validated:

```bash
PYTHONPATH=topos/src /tmp/p25-venv/bin/python -m pytest topos/tests -q
```

Focused examples:

```bash
/tmp/p25-venv/bin/python -m pytest topos/tests/test_io_cap_saturation.py -q
/tmp/p25-venv/bin/python -m py_compile \
  topos/src/topos/collect/cgroup.py \
  topos/src/topos/collect/collector.py
```

After merge, rerun from `main`, record the exact result in
`topos/docs/STATUS.md` and `topos/handoff/reports/P<NN>-LOG.md` /
`P<NN>-REPORT.md`, then commit:

```bash
git merge --no-ff feat/topos-pNN-slug -m "Merge topos PNN <topic>"
PYTHONPATH=topos/src /tmp/p25-venv/bin/python -m pytest topos/tests -q
git add topos/docs/STATUS.md topos/handoff/reports/P<NN>-*.md
git commit -m "docs(topos): record PNN merge evidence"
```

## Lessons Learned

- Reasonix is cost-effective when the prompt and handoff are specific.
- `run -c` materially improves cache reuse for a sequence of related packages.
- Separate fresh sessions are useful for independent areas because each builds a
  focused cache.
- It follows the `.worktrees/` protocol reliably when the exact command is in
  the prompt.
- It often writes useful tests, but review must check whether they assert the
  behavioral contract rather than implementation trivia.
- It can struggle with edit conflicts and internal `complete_step` receipts; do
  not spend controller time watching every retry.
- Logs and reports are valuable resumability artifacts, but dates/evidence need
  controller verification.
- Keep handoffs small. Packages with clear files, tests, and explicit out-of-
  scope constraints finish more reliably.
- Parallel agents are feasible only for non-overlapping areas. Merge them one at
  a time and expect minor README/ROADMAP/STATUS reconciliation.
- Two-agent parallelism works best when each stream owns a different context:
  for example daemon CLI work in one continued session and release measurement
  work in a fresh session.
- Reasonix may introduce polished but non-ASCII output in new files; normalize
  to the repo's ASCII default unless the file already has a reason to use
  Unicode.
- Subprocess and UI-import tests need special scrutiny. P33 initially had a
  test that passed alone but failed in the full suite because other UI tests had
  already imported `topos.ui`; controller validation caught it.
- If an agent installs or mutates local tooling to make tests pass, treat that
  as agent-environment evidence only. Re-run in the controller's known venv
  before merge.
