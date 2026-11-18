# Controller: work versus state

**Work** is code and docs a future controller needs to operate the estate: it is
versioned in this repository. **State** is what one controller session produced
(runs, logs, briefs, reports, host tables, plans): it is never committed and
lives in a directory that a `git clean` or a lost volume can remove. This page
says which is which, what the backup saves, what can be rebuilt, and how to use
the doctor and the backup.

## The table

Sizes were measured once with `du -sh` on the controller that produced the
import (2026-11-18); they show the order of magnitude, not a quota.

| Location | Kind | Size (measured) | Contents (names only) | In the backup |
| --- | --- | --- | --- | --- |
| `scripts/controller/` in the repo | work | small | scripts, tests, templates, `*.example`, docs | no: it is git |
| program directory, `codex/` scripts, templates, rules | work, now imported | small | `gate-run.sh`, `remote-gate.sh`, `chain-run.sh`, rules, brief templates | no: the repo copy is canonical |
| program directory, `codex/runs/` | state | 2.2G | per-run `.jsonl` session streams, `.err`, `.events`, `.live`, `.last.txt`, `.done`, `.thread`, `.state.md`, `.route` | only `*.thread`, `*.state.md`, `*.done` |
| program directory, per-package briefs and prompts (under `codex/`) | state | included above | one brief per job | no (rebuildable from the packages' own docs; copy a brief you still need) |
| `logs/` | state | 46M | gate and chain logs, verdict JSON copies | no |
| `reports/` | state | 24M | per-job reports | yes |
| `remote-gates/` | state | 141M | per-run result directory of `remote-gate.sh` (summaries, logs, artifacts) | only `result.json` and `meta.json` |
| host runbooks (`r1002/`, `v1001/` style directories) | state | 103M and 906M | host notes, evidence, live-test material | no (large; keep the runbook text itself under review with `reports/`) |
| readbacks (`readback-*.md`) | state | small | context readbacks of earlier controller sessions | no (rebuildable from the session transcript with `build-readback.py`) |
| `$CONTROLLER_HOME/*.local.*` | state, secret-adjacent | 3.8M for the whole home | `controller.local.env`, `hosts.local.json` (paths and host facts; no key contents) | yes |
| repo root `.controller-state/` | state | 3.4M | handoff, operator decisions, hook state | yes |
| repo root `plan.yaml` | state | 40K | the controller's running plan | yes |

Session transcripts of agents (`*.jsonl`) are never backed up by this tool: they
are large, they are reproduced by the agent hosts, and they can hold secrets that
a later reader pasted in.

## What can be rebuilt, and how

- Logs and `remote-gates/` artifacts: re-run the lane (`gate-run.sh`, `remote-gate.sh`).
- Readbacks: `build-readback.py` over the session transcript.
- Worktrees (`remote-gate-work/`, `.worktrees/`): `git worktree add`; the state
  inside a worktree that is not committed is NOT rebuildable, so commit or copy it first.
- Host tables and the env file: from `*.example` plus the provider console; the
  SSH keys and pinned `known_hosts` files are NOT in the backup and must be kept
  where the provider-side process expects them (mode 600).

## The `git clean` hazard

The controller's program directory is a symlink into a gitignored `artifacts/`
directory of the checkout. `git clean -fdx` in that checkout deletes it; so does
a lost volume. `ctl-doctor.sh` reports where `$CONTROLLER_HOME` and
`$CONTROLLER_RUNS_DIR` really live (real target, inside a work tree or not,
covered by an ignore rule or not) and warns. Never run `git clean -x` in that
checkout; keep a recent state backup on another volume.

## Operator checklist

1. `ctl-doctor.sh` at the start of a session and after any host or image change;
   fix every `FAIL`, read every `WARN`.
2. Set `CONTROLLER_BACKUP_DIR` (absolute, outside every git work tree, ideally on
   another volume) in `controller.local.env`.
3. `ctl-state-backup.sh --list` to see what would be saved; `ctl-state-backup.sh`
   at each handoff and before risky repository cleanup; note the printed sha256.
4. `ctl-state-backup.sh --verify <archive>` after copying an archive elsewhere.
5. Restore: `tar -xf <archive> -C <scratch dir>`, then copy the files you need back
   (member names start with `repo/`, `home/`, `runs/`).
