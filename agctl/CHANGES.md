# Changelog

All notable changes to `agctl` are recorded here.

## [Unreleased]

### Added
- New project `agctl` at the repository root (`src/agctl/`): the agent-control package of
  `docs/design/agctl/DESIGN.md` section 2 (controller state, the run registry, the estate
  view). Registered in `cmru.orchestration.toml` at L1 (its wheel requires `cli-extended`),
  with a `cmru.toml` release contract and one gate lane, `tests`.
- `agctl estate`: one row per package, every field carrying its value, its source and that
  source's mtime in text and in the `agctl-estate/1` JSON schema. Git is read with
  `git worktree list --porcelain` and `git branch --merged origin/main` (both
  `--no-optional-locks`). Every source without a reader yet reports a stated `unknown` with
  its reason instead of a plausible-looking value.
- `agctl.toml` read model (`[runs].legacy_dirs`) plus the documented `$CONTROLLER_HOME` /
  `$CONTROLLER_RUNS_DIR` / `$CONTROLLER_REPO` resolution of DESIGN sections 3 and 4. A
  malformed or explicitly named but absent file is a configuration error (exit 2).
- `agctl estate` reads every input of DESIGN section 4a and prints one row per package, each
  cell with its value, source, state and mtime: the legacy flat run records (`.done` outcome
  with the `REVIEW-*`-wins rule, `.live` context, `.wt`/`.route`/`.thread`, the `.last.txt`
  READY/YIELD position and the `<run>-y<N>` chain), `runs/<id>/run.json` when it is present and
  valid, the remote-gate results under `$CONTROLLER_HOME/remote-gates/`, the gate lock files
  with held/free decided from `/proc/locks` by device and inode, the peer `STATE.md`
  package-id lines (marked `unverified`), and the own and per-peer handover ages.
- `agctl estate --header`: the compact injected block of DESIGN section 8 item 1d (merge
  candidates, held leases, live runs, per-peer and own handover age buckets), stable order and
  bucketed ages so an unchanged estate prints byte-identical text, capped at 4096 bytes with a
  `[truncated]` marker.
- `agctl` readers are bounded by construction (new `bounded.py`): record files by head, logs by
  tail or by a streaming scan for the last line that starts with a prefix, `STATE.md` by size.
  A source too large to read whole is `unknown`, never a prefix reported as its content.
