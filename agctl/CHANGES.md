# Changelog

All notable changes to `agctl` are recorded here.

## [Unreleased]

### Added
- New project `agctl` at the repository root (`src/agctl/`): the agent-control package of
  `docs/design/agctl/DESIGN.md` section 2 (controller state, the run registry, the estate
  view). Registered in `cmru.orchestration.toml` at L1 (its wheel requires `cli-extended`),
  with a `cmru.toml` release contract and one gate lane, `tests`.
- `agctl estate` reads every input of DESIGN section 4a and prints one row per package, each
  cell with its value, source, state and mtime, in text and in the `agctl-estate/1` schema:
  git (`git worktree list --porcelain`, `git branch --merged origin/main`, both
  `--no-optional-locks`), the legacy flat run records (`.done` outcome with the `REVIEW-*`-wins
  rule, `.live` context, `.wt`/`.route`/`.thread`, the `.last.txt` READY/YIELD position and the
  `<run>-y<N>` chain), `runs/<id>/run.json` when present and valid, the remote-gate results
  under `$CONTROLLER_HOME/remote-gates/`, the gate lock files with held/free decided from
  `/proc/locks` by device and inode, the peer `STATE.md` package lines (marked `unverified`,
  with the designator as its own cell), the handover's `MERGE QUEUE` line and the own and
  per-peer handover ages. A source without a reader yet reports a stated `unknown` with its
  reason instead of a plausible-looking value.
- `agctl estate --header`: the compact injected block of DESIGN section 8 item 1d (the
  handover's merge queue, falling back to unmerged worktree branches and their heads; held
  leases; live runs with a bucketed context figure; per-peer and own handover age buckets),
  stable order, bucketed ages and context so an unchanged estate prints byte-identical text,
  each line bounded where it is composed, and a 4096-byte cap with a `[truncated]` marker.
- `agctl.toml` read model (`[runs].legacy_dirs`) plus the documented `$CONTROLLER_HOME` /
  `$CONTROLLER_RUNS_DIR` / `$CONTROLLER_REPO` resolution of DESIGN sections 3 and 4.
- `bounded.py`: every read is bounded by construction — record files by head, `STATE.md` by
  size, and the files whose answer is their *last* matching line by a streaming scan
  (`last.txt` position, `lane.log` verdict, `HANDOFF.md` merge queue), never by a tail window.

### Changed
- **The row key is the whole slug after the stamp** (`core-ciu-r2fix`, `edge-relgate`,
  `plat-22c-resumebusy`), not the topic: one row is one package, never an aggregate of
  several. `STATE.md` prose joins on the topic (the only stage a branch slug and a `P-<topic>-
  <designator>` line share) and reports the designator separately.
- **Runs with no worktree get a row**, and a run joins its branch's row through its `.wt`
  path when that path is readable — so the runs are reported whether or not the branch still
  exists.
- **A gate result joins a row only on that row's head** (exact or a prefix of ≥7 characters).
  A project-directory collision is named in the `detail`, never printed as the row's verdict.
- **A repository that cannot be resolved costs the git cells only**; the run, gate and state
  rows still render. The root comes from `$CONTROLLER_REPO` then the caller's cwd — the
  controller home is never asked.
- **A run's liveness is derived**: a free `.lock` with no `.done` is `DIED`; no lock file at
  all is `undecided`. Neither is listed as live.
- **An unreadable directory degrades its own source**: runs, gate locks, gate results and peer
  state directories are listed through a guarded walk that carries the reason into the cell,
  the header line and the JSON (`sources.*_detail`).
- **A peer is a directory holding `STATE.md` or `HANDOFF.md`**; `archive`, `hooks`, `runs`,
  `e021`, `scratch-*` are no longer printed as controllers with `unknown` state.
- **Exit codes**: a configuration refusal and the `--header --json` refusal both exit 2, as
  documented; `ConfigError` carries `exit_code = 2` because cli-extended otherwise reads 1.
- The merge listing asks for `%(refname)` and strips `refs/heads/`: `%(refname:short)` prints
  `heads/<name>` when a tag shares the branch name, which would turn a merged branch into a
  merge candidate.
- The project starts at cmru template revision 5, the revision `cmru standards` demands today
  (`cmru/src/cmru/standards.py:29`): a brand-new project has no legacy to migrate, and no lane
  runs `cmru standards`, so starting at 4 would ship already stale.
