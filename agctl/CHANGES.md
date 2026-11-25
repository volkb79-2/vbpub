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
