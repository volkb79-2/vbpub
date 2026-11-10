<!-- origin: 2026-11-02; status: design; copied from the controller program dir 2026-11-10 -->
<!-- source: reports/CIU-RELEASE-PLAN-2026-11-02.md -->
<!-- relation: CIU release plan -->

# CIU release plan — 2026-11-02

Read-only analysis. Base: `main` = `origin/main` = `10c8d8ff8`.
The last CIU tag is `ciu-v7.18.0` (`fb824dfda`, 2026-10-28). Since then, 20 commits touch `ciu/` or `libraries/worktree/`, including stage 1 (`53d31bee4`) and stage 2 (`58488fb96`).

## 1. Version and command

**Version: `7.19.0` (minor).**
- `ciu/cmru.toml` uses `strategy = "scm"` with `bump = "conventional"`. The commits since the tag are `fix(...)`, `test(...)`, `docs(...)`, plus one untyped stage-1 subject, so the planner would most likely propose only a patch (7.18.1).
- The content is new public surface:
  - new flags: `--keep-branch`, `--delete-branch`, `--discard-protected-state`, `--discard-changes`, `--root-escalation`;
  - new default behaviour: unmerged branches are kept, dirty or protected checkouts are refused;
  - JSON schema bumps: worktree lifecycle and removal phase report schema 2, branches schema 4.
- That warrants a minor version.
- `8.0.0` is reserved for CIU v8 (memory: CIU v8 design set), so not a major.

**Command:**
1. Run `cmru release ciu --minor --dry-run`. It must show `ciu-v7.19.0`, and the body must be the `[Unreleased]` section.
2. Run `cmru release ciu --minor`.

`--set-version 7.19.0` is the equivalent explicit form. Never use `--skip-gates`. Never use `--resume` without the recorded plan.

**Release notes.** `ciu/CHANGES.md [Unreleased]` (lines 24-47) is a single Fixed / Changed / Adoption-Migration block and reads correctly as release notes:
- The stage-1 behaviour is present: unmerged branches kept by default, `--keep-branch` / `--delete-branch`, and the dirty-checkout refusal with `--discard-changes`.
- Stage 2 is present: S6.5 retry, Docker 25 floor for the helper and for `--reset`, `protected_reason`, the walk cap.
- The schema bumps are listed under Changed, and the migration notes tell strict consumers to accept branches schema 4 and removal schema 2.
- Stage-1 lines about schema 3 and "CIU-134 still open" were correctly replaced by the stage-2 text. No "not released" or "next release" wording for this content remains in `ciu/README.md`, `ciu/docs`, or the `libraries/worktree` documents.

**One small docs gap, non-blocking.** `ciu/KNOWN_ISSUES_TODO_BACKLOG.md`:
- The index table (rows around `:599-604`) has no rows for CIU-156, CIU-157, CIU-158.
- Their sections (`:5706`, `:5713`, `:5719`) say FIXED, and the "Last updated" header lists them.
- Add the three index rows in the release-prep commit, or right after the release.

## 2. libraries/worktree is embedded in both ciu and cmru

- **Embedding.** Both wheels embed the library from source:
  - `ciu/pyproject.toml:46-50` and `cmru/pyproject.toml:58-64` both list `packages.find where = ["src", "../libraries/worktree/src"]` and `package-dir worktree = ...`.
  - So each wheel installs the same top-level `worktree` package into the venv. Whichever wheel is installed last owns those files.
- **Current devcontainer state.** `<controller-home>/.venv` has `ciu 7.16.0` (7.17 and 7.18 were never deployed), `cmru 7.1.2`, and one shared `worktree/` directory.
- **Hazard.**
  - ciu 7.19.0's `worktree rm` needs the new library: `finish_workspace_removal`, and the `checkout_recovery` and `residual_checkout_guard` parameters.
  - If `cmru 7.1.2` is reinstalled after ciu 7.19.0, the library is downgraded and `ciu worktree rm` fails with an `AttributeError` or `TypeError`. Uninstalling either wheel also deletes the shared `worktree/` files.
- **cmru does need a release, for its own changes and to embed the same library.** Its changes since `cmru-v7.1.2` are: KI-106 mutation timeout, the patchseries review fixes, and tester-unified tmux/Go/eatmydata.
- **Compatibility.** cmru's calls into the library stay compatible: `remove_workspace(context, force=True)` (`cmru/src/cmru/transaction.py:686`), plus `gate_snapshot` and `config`. The new parameters are keyword-only with defaults.
- **Gate coverage.** cmru's Assay lanes import the sibling library from source (`cmru/assay.toml:44`, `PYTHONPATH=src:../libraries/worktree/src`). cmru's release gate (`run-gate gate-provisional`) therefore exercises cmru against the new library, so no extra lane is needed.
- **Order.**
  1. Release ciu, then install its wheel.
  2. Release cmru (the installed cmru 7.1.2 runs that release, using whichever library is installed; both are compatible), then install the new cmru wheel.
  3. After both installs, check that the shared package is the new one (see 4).
  - Never reinstall cmru 7.1.2 after ciu 7.19.0.
- **Follow-up, not for this release.** File a cmru/ciu backlog item to ship `libraries/worktree` as its own wheel dependency instead of embedding it twice. This is the same pattern as the cli-extended adoption, and the comment at `cmru/pyproject.toml:61` already points at it.

## 3. Blockers and gate facts

- **Release gate: R2 is not part of it.**
  - CMRU's ciu gate (`[steps.run-tests]`) runs `cmru tester-gate` with `run-ciu-tests.py`, which is the 100% line+branch coverage gate. It also runs the isolated enrollment lane (`run-gate enroll` with CMRU's digest-pinned DinD sidecar).
  - Neither includes the run-gate R2 mutation stage, so R2's NO_MEASUREMENT (`CGROUP_OBSERVATION_UNAVAILABLE`) here does not affect the release, and no host observer is needed.
  - 7.18.0 was cut with these same steps.
  - The run-gate `ciu` lane result (R0/R1/R3 PASS, R2 NO_MEASUREMENT accepted) is merge evidence only.
- **Builder and registry: none.** ciu's artifact is a wheel only (`artifacts = ["wheel"]`; push = `cmru handler wheel-publish ... --extra-asset get.py` to a GitHub release).
  - There is no buildx or GHCR step, so `BUILDX_BUILDER` and `BUILDKIT_HOST` are irrelevant.
  - The enrollment lane needs the Docker daemon to pull or run `docker@sha256:5efed980…`.
  - The release needs the usual GitHub release identity, `GITHUB_PUSH_PAT`, through CMRU.
- **`--allow-uncommitted` is not needed.**
  - `git status --short -- ciu libraries/worktree cmru` is empty.
  - The operator's untracked files are `pwmcp/tools/console-capture/*`, `scripts/netcup/{PROXMOX-VE.md,pve-on-debian.sh}`, and `target-host.jsonc`. None touches ciu or libraries/worktree.
  - CMRU releases the pushed `origin/main` snapshot in an isolated candidate.
  - Those files can only make CMRU's post-release rebase of the local `main` refuse with a warning. That is harmless: leave them untouched, and never delete or commit them.
  - If the release preflight itself refuses because of them, `--allow-uncommitted` is safe here: it builds the committed state, and nothing under `ciu/` is dirty.
- **Docker floor.** The new S6.5 helper (both `worktree rm --root-escalation` and the `--reset` volume cleanup) requires Docker CLI and Engine 25.0.0 or newer. This devcontainer runs Engine 29.9.2; hosts with Debian bookworm's `docker.io` 20.10 are refused. The floor is stated in CHANGES. It is not a release blocker.
- **CIU-151 stays OPEN** (live verification of the physical owner-label path pending). It is documented and not a blocker.

## 4. After the release

1. **Verify (read-only).**
   - `git ls-remote --tags origin ciu-v7.19.0` points at the release commit.
   - The GitHub release `ciu-v7.19.0` carries `ciu-7.19.0-py3-none-any.whl` and `get.py`.
2. **Install in the devcontainer.**
   - Download the released wheel, then run `<controller-home>/.venv/bin/python -m pip install --force-reinstall --no-deps <ciu-7.19.0 wheel>`.
   - Check:
     - `which -a ciu` and `pip show ciu` show 7.19.0, not editable, no stray shim (memory: devcontainer tool installs get clobbered);
     - `ciu --version`;
     - `python -c "import worktree; assert hasattr(worktree, 'finish_workspace_removal')"`.
3. **cmru release** (section 2 order), then install its wheel the same way, then repeat the `worktree` assertion and `cmru --version`.
4. **Dependent pins.**
   - mdt-cockpit resolves the CIU wheel at build time (`CIU_WHEEL_TAG` / `CIU_WHEEL_ASSET_NAME`, `scripts/resolve-devcontainers-release.py`). It re-pins at its own release, the last in the wave. No edit is needed now.
   - dstdns declares `ciu>=7.15.2` (optional `vbpub-tools` extra), a floor that 7.19.0 satisfies. Nothing is required. Optionally notify dstdns of the new `worktree rm` defaults: unmerged branches are kept, and dirty checkouts are refused without `--discard-changes BRANCH`.
   - No other vbpub project pins a ciu version (`git grep` outside ciu found only mdt-cockpit test fixtures and comments).
5. **Notes.** Update the release-wave memory and handoff with the current state and the next steps only (cmru release, CIU-151 live verification, the worktree-wheel split follow-up).

## 5. Steps for the controller

1. Preflight:
   - `which -a cmru run-gate`, `cmru --version`;
   - no gate running (`gate.lock`);
   - `git status --short -- ciu libraries/worktree` is empty;
   - `main` equals `origin/main`.
2. Optional docs commit: add the CIU-156, CIU-157, CIU-158 index rows to `ciu/KNOWN_ISSUES_TODO_BACKLOG.md`. Commit with `git commit --only`, then push.
3. Run `cmru release ciu --minor --dry-run`. Confirm `ciu-v7.19.0`, a release body equal to the `[Unreleased]` block, and no refusals.
4. Run `cmru release ciu --minor`, under `gate.lock`, one gate at a time. Monitor it and read the verdict and result in a separate step.
5. Post-release steps 1-2 (verify, install the ciu wheel, check the `worktree` assertion).
6. `cmru release cmru --dry-run`, then `cmru release cmru`. The version is whatever the conventional planner proposes for cmru's own changes, and the gate is `gate-provisional`. Install the cmru wheel and repeat the `worktree` assertion.
7. Post-release steps 4-5.

## 6. Operator decisions

**None required.**
- The minor bump to 7.19.0 follows from the new flags and schema bumps, and 8.0.0 is reserved for v8.
- R2 is not part of CMRU's ciu release gate.
- The operator's untracked files are outside ciu.
