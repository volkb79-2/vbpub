# agctl — agent control

`agctl` gives a controller the estate picture in one read-only command: which package is in
which state, which branch and head a worktree holds, whether that head is merged into
`origin/main`, which runs are live and which gate slots are held — each field naming the
source it came from and that source's age.

**Status: slice 1 is being built.** `agctl estate` currently reads git (`git worktree list
--porcelain`, `git branch --merged origin/main`) and reports every other source as a stated
`unknown`; the run, gate, plan and handover readers land in the following commits (backlog
AGC-03). Design: [`docs/design/agctl/DESIGN.md`](../../docs/design/agctl/DESIGN.md) sections
2, 4a and 8 item 1d.

## Install

```bash
python3 -m pip install --no-index --find-links . agctl-*.whl cli_extended-*.whl
```

`cli-extended` is a real wheel dependency (never vendored). Until it ships inside
`tester-unified`, the gate and any source-tree use put it on `PYTHONPATH`:
`PYTHONPATH=src:../libraries/cli-extended/src`.

## Use

```bash
agctl estate             # human rows, one per package, with field provenance
agctl estate --json      # the same rows as agctl-estate/1 JSON
```

## Sources, and what is unverified

| Input | Where it comes from | State today |
|---|---|---|
| worktrees, branches, heads | `git worktree list --porcelain` (`--no-optional-locks`) | read |
| merged into `origin/main` | `git branch --merged origin/main` | read |
| run records | `$CONTROLLER_RUNS_DIR`, `$CONTROLLER_HOME/runs`, `~/c5-runs`, `[runs].legacy_dirs` in `agctl.toml` | `unknown` (AGC-03) |
| gate slots and results | `$CONTROLLER_HOME/remote-gate-locks/`, `$CONTROLLER_HOME/remote-gates/` | `unknown` (AGC-03) |
| plan state | `.controller-state/<name>/STATE.md`, later `~/controller-state/<name>/plan.yaml` | `unknown` (AGC-03) |
| handover age | `.controller-state/HANDOFF.md`, `~/controller-state/<name>/HANDOFF.md` | `unknown` (AGC-03) |

Two things the output never does: it never invents a value for a field it could not read
(it prints `unknown` with the reason), and it never guesses an age it never measured. A
malformed `agctl.toml` is refused with exit 2 rather than silently ignored, because a
`[runs].legacy_dirs` the caller set is a fact worth failing for.

## Safety contract

Read-only by construction: no file is written, no lock is taken (a probe `flock` could make a
real gate admission or launcher see "busy"; held/free is decided from `/proc/locks`), no host
and no network is contacted, and the only subprocess is `git` with `--no-optional-locks`.
Run streams, prompts, credentials and session transcripts are never read or echoed.

## Development

```bash
cd agctl && run-gate tests     # tester-unified, pytest, 100 % line+branch coverage floor
```
