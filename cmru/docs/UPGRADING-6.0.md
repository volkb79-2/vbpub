# Upgrading cmru from 5.5 to 6.0

This is the operator upgrade guide from cmru 5.5 to 6.0.0. It lives here, not in `CHANGES.md`,
because `CHANGES.md` is generated per release by `cmru release` (a hand-authored
`## [6.0.0] - UNRELEASED` heading blocks the release, KI-23). The per-commit change list for 6.0.0 is
the generated `## [6.0.0]` section of `CHANGES.md`; the pre-release hand-written wave entries are
recoverable from the W3-PREP commit history (`git show 1be285b09:cmru/CHANGES.md`), and the
pre-wave hand-written text from `git show 68a03b4fe^:cmru/CHANGES.md`.

**Release status: PROVISIONAL.** cmru 6.0.0 was released through `./run-gate.py gate-provisional`,
which skips ONLY the R2 mutation campaign by operator decision (2026-10-06); the postponement is
recorded in the retained release evidence (`.assay/mutation-postponed-cmru.json`) and tracked as
**KI-62**. The R2 run against the `cmru-v6.0.0` tag, and the fixes it backports, follow the release;
see `KNOWN_ISSUES_TODO_BACKLOG.md` KI-62 for the current state. Until then 6.0.0 has no mutation
evidence. The two landing-window compatibility shims for a not-yet-upgraded 5.5
(`default_projects` in `cmru.orchestration.toml`, and `ciu/cmru.toml` without its installer
`extensions`) were removed after the release; the only remaining `TODO(cmru-6.0 post-release)`
markers belong to KI-62.

cmru 6.0.0 is a **breaking** release: the command line was redesigned (hard renames, no
compatibility spellings except `release --ref`), the exit-code scheme gained a fifth value, the
agent and controller were retired, and `cli-extended` became a real wheel dependency. Operators
upgrading from 5.5 should read the first two sections below first.

## BREAKING: command grammar (old to new)

| 5.5 | 6.0.0 |
|---|---|
| `cmru run --run-tests` / `--build` / `--push` / `--validate` | `cmru run --step run-tests` / `--step build` / `--step push` / `--step validate` (repeat `--step`; no flag runs the configured default steps) |
| `cmru run-step PROJECT --step NAME` | `cmru run PROJECT --step NAME` (`run-step` is removed) |
| `cmru status --show-run-details` / `--log-append` | removed (no-ops); `status` is read-only and gains `--json` |
| `cmru publish [PROJECT]` (published the caller's checkout) | `cmru publish PROJECT --build-output ID` (a verified retained build) or `cmru publish PROJECT --from-checkout` (explicitly not isolated); one is required |
| `cmru cleanup` with no mode (applied the configured policy) | `cmru cleanup --policy`; a mode is now required (`--policy`, `--remove-assets AGE`, `--delete-unmanaged-release-tag TAG`, `--delete-build-output ID`) |
| `cmru cleanup PROJECT --remove-assets AGE` | refused (exit 2): `--remove-assets` is estate-wide and takes no project target |
| `cmru cleanup --discard-build-worktree PATH` | `cmru abandon PATH` (or the build branch name) |
| `cmru release --discard-logs-on-release` / `--discard-artifacts-on-release` / `--discard-evidence-on-release` | `cmru release --discard logs` / `--discard artifacts` / `--discard evidence` (repeatable) |
| `cmru release --ref REF` | `cmru release --ahead-check-ref REF` (`--ref` is kept as a DEPRECATED alias for this one release only) |
| `cmru release --set-version V` with several projects | refused: `--set-version` needs exactly one selected project |
| `cmru handler oci-image-build\|oci-image-push --target T` | `--bake-target T` (names a docker bake target, not a cmru project target) |
| `cmru handler oci-image-build\|oci-image-push --repack` | removed (it only ever failed while KI-02 is open) |
| `cmru tester-gate --forward-cgroup-parent-var` / `--forward-cgroup-parent-gates-var` | `--forward-background-slice` / `--forward-gates-slice` |
| `cmru get-py A,B` or `all` to stdout | refused (use `--output-dir`); `--dry-run` needs `--output` or `--output-dir` |
| `cmru resolve ... --format` shape for one vs several names | one explicit name prints one object; `all` or a list prints a keyed map; new config-free `cmru resolve --repo OWNER/REPO --prefix PREFIX` |
| `orchestration.default_projects` | no longer required; accepted with a one-line warning and ignored (will be removed) |
| `CMRU_BIN`, `CMRU_RUN_LOG`, `CMRU_SHOW_RUN_DETAILS`, `CMRU_LOG_APPEND`, `CMRU_LOG_PREFIX_TIME_SHORT` | `CMRU_INTERNAL_*` (see "Environment variables" below) |
| `cmru-agent`, `cmru-controller`, `cmru.agent`, `cmru.controller` | removed |

## Exit codes and errors

- One scheme for every verb: `0` done (including a declined confirmation and nothing to do), `1` the
  operation failed after starting, `2` usage or configuration error, `3` a prerequisite is missing
  (including cmru not installed as a distribution), `4` **new**: refused by policy or verification with
  nothing changed (release plan refused, uncommitted paths, stale tool dependencies in `release`,
  `abandon` blockers, `standards` issues, `tool-deps` stale pins).
- Operator scripts that tested for `2` on `standards`/`tool-deps`/`abandon` refusals, `1` on a refused
  release plan, `2` on `init` declining, or `1` on a `tester-gate` missing configuration must be updated
  (those are now `4`, `4`, `0`, `3`).
- Domain failures raise `CmruError` (a `CliFailure`) and render as one line with no traceback; an
  unexpected exception is reported as one `unexpected <Type>: ...` line with exit 1 (the library
  `report` policy), not as a traceback. Pass the new global `--traceback` to see the Python stack.

## Global flags and dry-run

Verified against `cmru --help`, `cmru handler --help` and `cmru versions --help` of the 6.0 tree:

- `--traceback`: global debugging flag, shows the Python stack for an unexpected error.
- `--progress MODE`: global; `auto`, `tty`, `plain`, `quiet` or `rawjson` (`rawjson` uses stdout and is
  muted with `--json`).
- `--json`: global machine-readable output for the verbs that offer it (for example `status`,
  `worktrees`, `dependencies`, `standards`, `tool-deps`, `versions check`); it is the library-owned
  compact JSON, not the 5.5 sorted indented text.
- `--dry-run`: now available on `cmru handler` (the mutating handler verbs: `wheel-build`,
  `wheel-publish`, `tarball-publish`, `bundle-manifest`, `oci-image-build`, `oci-image-push`) and on
  `cmru versions` (`init`, `resolve`); it shows what would change without changing anything.

## Dependencies, packaging and new verbs

- `cli-extended>=0.3.0` is now a real wheel dependency of cmru (it is no longer vendored or put on
  `PYTHONPATH` from the monorepo). It is a release asset, not on PyPI: install it first, or use
  `--no-index --find-links`; `pip install -e .` without `--no-deps` queries PyPI for it. The tester image
  installs the released wheel by sha256.
- New extra: `pip install 'cmru[interactive]'` (the library's prompt driver, used by `cmru init`).
- New `cmru doctor`: checks `git`, `docker`, `config`, `credentials` (names which variable, never the
  value), `images`, the `cli-extended` floor, and the packaged `skills`.
- New `cmru skills install|list|check|uninstall`: the agent skill ships inside the wheel
  (`cmru/skills/cmru-cli`) instead of a checkout `.claude/skills` directory.
- The agent and controller are retired (`cmru-agent`, `cmru-controller`, `cmru.agent`,
  `cmru.controller`, `packaging/cmru-agent.service`; operator decision O5, superseded by push over SSH).
  `docs/spec-cmru-agent-controller.md` is kept with a RETIRED banner.
- Every verb is built from one shared registry factory; `python -m cmru.handlers` remains as a
  bootstrap-only alias of `cmru handler` (same builder; project steps should call `cmru handler <verb>`).
- `cmru.runner.run_step` and `cmru.bundle.run_bundle` remain supported Python APIs (modern-debian-tools
  `build-push.py` imports `run_step`; an estate test guards it). Only the `run-step` CLI was removed.
- `get.py` split: the rendered installer is now a generic, fail-closed, transactional installer
  (install, update, status, rollback; sha256 mandatory for every file; trusted-comment binding of the
  signed manifest). Host enrollment moved out of the template into ciu: a project opts into extra
  commands through `[project.installer] extensions = ["path/to/fragment.py"]`, inlined verbatim and
  checked at render time. Re-render every `get.py` (ciu and tls-edge are done in the estate).
- `cmru standards`: project template revision 5; run `cmru standards --update`.
- `cmru abandon BRANCH` retires an origin-only `cmru-release-*` candidate branch whose commits are all
  on `origin/main` (KI-35); see `RELEASE-TRANSACTIONS.md`.

## Environment variables

- `CMRU_INTERNAL_*`: the former `CMRU_BIN`/`RUN_LOG`/`SHOW_RUN_DETAILS`/`LOG_APPEND`/
  `LOG_PREFIX_TIME_SHORT` are internal, written by cmru for its own children; only `CMRU_INTERNAL_BIN`
  (the launcher) is honoured exclusively inside a verified transaction child. The whole `CMRU_INTERNAL_`
  prefix is reserved and refused in project `[env]`. Operator inputs: `CMRU_RELEASE_LOG`,
  `CMRU_TESTER_*`. Do not set the internal names yourself.
- Removed with the agent and controller (nothing in 6.0 reads them; they were read only by
  `cmru.agent`, checked at `68a03b4fe^`): `CMRU_CONSUL_TOKEN`, `CMRU_LANDSCAPE`,
  `CMRU_MINISIGN_PUBKEY` and `CMRU_NODE_ID`. `CMRU_AGENT_SERVICE_TEMPLATE` was not an environment
  variable but the name of the agent's systemd unit template constant in `cmru.agent.selfupdate`; it is
  gone with that module. Remove any of these from unit files, CI and scripts.

## How to upgrade from 5.5

1. Install `cli-extended>=0.3.0` first (or let the release wheelhouse resolve it), then cmru 6.0.0.
2. Run `cmru standards --update` in each project, then `cmru standards all`; set `CMRU_TESTER_PIDS_LIMIT`
   (estate value `4096`) in `[env]` of any project that runs `cmru tester-gate`, and digest-pin the probe images.
3. Search scripts, CI and docs for the removed spellings and replace them per the table above:
   `run-step`, `run --run-tests|--build|--push|--validate`, bare `publish`, bare `cleanup`,
   `cleanup --discard-build-worktree`, `--discard-*-on-release`, `release --ref`, `--target` on
   `handler oci-image-*`, `--repack`, the two `--forward-cgroup-parent*` flags, `get-py` with several
   projects on stdout, `cmru-agent`, `cmru-controller`.
4. Replace `python -m cmru.handlers ...` in project steps with `["cmru", "handler", ...]` (`cmru standards`
   reports the old form).
5. Update exit-code checks (`4` for refusals; `init` decline is `0`; `tester-gate` missing config is `3`).
6. Remove any `default_projects` from `cmru.orchestration.toml` (a warning until then), any use of the
   old `CMRU_*` presentation variables, and the removed agent-only variables above.
7. Re-render installers with `cmru get-py` and move any enrollment-style custom commands into an
   `[project.installer] extensions` fragment.
8. Run `cmru doctor` and `cmru skills install` where the agent skill is used.
