# Execution plan — finish cmru release-modes (post P8 Phase B)

Self-contained specs for a Sonnet-subagent Workflow + the supervised final cut. Sub-agents
cannot see the chat — everything they need is here. Companion: `plan-cmru-release-modes.md`
(design), `ciu-vs-cmru.md` (roles). **Work directly on `main` in `/workspaces/vbpub`** (no
worktree — the parallel phase touches disjoint files, see §Workflow).

## Status (done, pushed)
- P8 Phase A (`cb66184`): built-in wheel handlers (`cmru/handlers.py`, `cmru.release`
  glue, `cli._builtin_step_command`), cmru dogfood, scripts→delegations, 102 tests.
- ciu-vs-cmru docs (`0ccfd32`).
- P8 Phase B (`9e121e3`): ciu fully zero-script; deleted 5 wheel scripts + 3 redundant
  configs (`ciu/cmru.build.toml`, `cmru/cmru.build.toml`, `cmru/cmru.toml`).

## Conventions every agent must follow
- After editing cmru core, run `cd /workspaces/vbpub/cmru && python -m pytest -q` — must
  stay green (currently 102). Add tests for new behaviour.
- Never print/commit a real token. cmru.toml carries no secrets.
- Commit your phase with a `feat(cmru)`/`refactor`/`fix` message ending
  `Co-Authored-By: Claude Sonnet <noreply@anthropic.com>`. Stage only your phase's files.
- `cmru` is editable-installed; `python -m cmru.handlers --help` works. The repo-root
  `cmru.py` shim shadows the package — run python checks from `/tmp` with an absolute
  config path.

---

## PHASE 1 (serial, cmru core) — tarball built-in publish/validate

**Why:** tls-edge + empyrion still carry their own publish scripts. Generalize the wheel
built-in to tarballs (build stays project-owned — there is no universal tarball build cmd).

**Files:** `cmru/src/cmru/release.py`, `cmru/src/cmru/handlers.py`, `cmru/src/cmru/cli.py`,
`cmru/tests/`.

**release.py:** generalize wheel discovery —
```python
def find_artifact(dir, glob):   # rename core of find_built_wheel; keep find_built_wheel as alias
    ...
```
`validate_latest_release` already takes `artifact_suffix` — reuse as-is.

**handlers.py:** add two subcommands (build NOT included):
- `tarball-publish --prefix P --cwd D --glob G (--version-file F | --version-env E) [--notes-env N] [--env-file]`
  → read version (from `F` relative to cwd, else env `E`); `art = find_artifact(cwd/'dist', G)`;
  `publish_versioned(gh, prefix=P, version=ver, asset_path=art, notes=..., latest_pointer=True)`.
  Same GITHUB_* env contract as wheel-publish.
- `tarball-validate --prefix P [--artifact-suffix .tar.xz]` → `validate_latest_release(gh, P, artifact_suffix=...)`.

**cli.py:** extend built-in dispatch —
```python
_PROFILE_BUILTIN_STEPS = {
    "wheel":   ("build", "push", "validate"),
    "tarball": ("push", "validate"),          # build stays project-owned
}
```
In `_builtin_step_command`, when `artifact == "tarball"`:
- `push` → `tarball-publish --prefix <bare> --cwd <abs> --glob "<bare>-v*.tar.xz" --version-file VERSION` (+ `--notes-env <BARE>_RELEASE_NOTES` if any);
- `validate` → `tarball-validate --prefix <bare>`;
- `build` → None (the load_config guard already requires a build step when no built-in build exists).
The version-file default `VERSION` suits file:VERSION projects; expose `--version-env` for others.

**Tests:** `find_artifact` (glob/multi/none), `_builtin_step_command` for a tarball project
(push→tarball-publish, validate→tarball-validate, build→None), load_config: tarball project
WITHOUT a build step is rejected, WITH a build step loads.

**Acceptance:** `pytest -q` green; a synthetic tarball project resolves the right built-ins.

---

## PHASE 2 (parallel — disjoint files; ≤4 agents)

### 2A — tls-edge + empyrion → built-ins  (file: `cmru.toml`, delete tls-edge publish script)
- **tls-edge** `[project.tls-edge]`: keep `[steps.build]` (`bash scripts/build-artifact.sh`),
  **remove** `[steps.push]` (the `publish-release.py` step) — the tarball built-in publishes.
  Add `[project.tls-edge.env] TLS_EDGE_RELEASE_NOTES = "..."`. Keep `version.strategy="file:VERSION"`.
  **Delete `tls-edge/scripts/publish-release.py`** (built-in replaces it); keep `build-artifact.sh`
  + `release.sh` (standalone). Verify `cmru status` still lists tls-edge.
- **empyrion** `[project.empyrion-translation]`: it stays on-demand (NOT in project_order).
  Goal = route publish through the shared keystone, not a bespoke uploader. Inspect
  `game_stuff/empyrion/release-empyrion-translation.py`: if it already calls
  `cmru.release.publish_versioned`/`GitHubReleases`, leave build as-is and just confirm
  `artifacts=["tarball"]`. If it hand-rolls GitHub upload, refactor its publish path to call
  `cmru.release.publish_versioned` (date version → pass explicitly). Do NOT change its
  date-tag scheme. Keep build bespoke. Flag any ambiguity in the commit body rather than
  guessing the version scheme.
- Update `cmru.sample.toml` tls-edge block to match.

### 2B — mdt: externalize wheels + include cmru  (files under `modern-debian-tools-python-debug/`)
- New `modern-debian-tools-python-debug/pip/wheels.list` — first-party wheels to install,
  mirroring `apt/packages.list` + the python packages list (one per line; comments with `#`).
  Seed with `cmru` (note ciu can be added later). Add an upstream/source comment per memory
  `reference-links-in-container-defs`.
- **Fetch mechanism = cmru's resolver, pre-build (like manifests are build inputs):** extend
  `scripts/resolve-devcontainers-release.py` to, for each entry in `wheels.list`, resolve
  `<name>-latest` via `cmru.release.GitHubReleases.resolve_latest(name)` (owner/repo from
  cmru.toml, token optional for public), download the `.whl` + `.whl.sha256` into a
  build-context dir `pip/wheels/`, verify the sha256. Record name+version+sha256 for the
  manifest. **Depends on `<name>-latest` existing → the actual rebuild is the FINAL cut,
  after cmru is re-released.** Write the wiring now; do not rebuild.
- **Dockerfile:** COPY `pip/wheels/` and `pip install --no-index --find-links` them into the
  image (so cmru ships in the image). Keep it behind the same target structure as existing
  package installs.
- **P5 manifest content** (do here): `manifest_sections.py` / the generated
  `devcontainer-manifest-*.md` — add a "first-party wheels" section (cmru version+sha256),
  move the full sha256 list to an appendix at the doc END (unobtrusive), add a python-libs
  section if missing, and harmonize the host `package-manifests-versioned/*.md` with the
  in-image manifest. Keep generation dynamic per built target (already done).
- Do NOT run a docker build (no engine in this cockpit; builds happen in the final cut).

### 2C — pwmcp cmru.vars self-heal  (files under `pwmcp/`)
- In `pwmcp/build-push.py`, `pwmcp/scripts/build-bundle.py`, `pwmcp/scripts/publish-bundle.py`:
  where they load `cmru.vars` and fail if absent, add a shared helper that, if `cmru.vars` is
  missing/incomplete, runs `scripts/resolve-playwright-version.py` (idempotent) then reloads —
  so out-of-order partial invocations (`cmru publish` with no prior build) self-heal instead
  of erroring. Keep env-wins precedence.

### 2D — P6: implement `cmru cleanup`  (file: `cmru/src/cmru/cli.py` core + tests)
- Extend the existing `remove_assets`/cleanup path (cli.py ~line 766) into a generic
  `cmru cleanup`: per `[cleanup]` config, delete old GitHub Releases (keep `-latest` +
  `keep_release_tags`), prune old ghcr package versions, delete stale tags. Optional
  per-project `[steps.clean]` invoked with `$CMRU_VERSION` for referenced-file deletion;
  cmru commits the result (generic, no hardcoded paths). Add tests (mock the GH client).
- **Note for orchestrator:** 2D edits `cli.py`; Phase 1 also edits `cli.py`. 2D MUST run
  after Phase 1 completes (pipeline barrier guarantees this). 2A/2B/2C touch disjoint files
  and are safe alongside 2D.

---

## PHASE 3 (serial, Opus) — review
One Opus agent reads all Phase 1+2 diffs together: correctness, no duplicated logic, tests
present + green (`pytest -q`), `cmru status` clean, sample config loads, no token leakage,
docs updated. Produce a punch-list; fix-forward.

---

## FINAL CUT (supervised by the main session — NOT in the Workflow)
Destructive + needs docker/ghcr/network + a deletion-list confirm. Order (greenfield batch
→ one fresh cut):
1. **P0 wipe** (confirm exact list first): delete all GitHub Releases + `*-v*`/`*-latest`
   tags (keep `empyrion-de-translation-*`) + ghcr versions; runbook plan-cmru-release-modes §7b.
2. **P7 re-release** core: `cmru release --project ciu --set-version 3.1.0`; `cmru release
   --project cmru --set-version 1.0.0`; `cmru release --project tls-edge --set-version 1.0.0`;
   `cmru release --project pwmcp`. Verify tags + Releases + `.sha256` + resolve.
3. **P5 mdt rebuild** (now `cmru-latest` exists): build images, resolver downloads cmru wheel
   into context, push to ghcr, commit manifests. mdt deliverable = images only (no tag/Release).
4. Verify `cmru resolve --project X` for each; `-latest`/latest.json correct.

## Workflow shape (for the launcher)
```
phase('Phase 1: tarball built-in')   // 1 Sonnet agent (cmru core), tests green
phase('Phase 2: parallel modules')   // parallel(): 2A cmru.toml, 2B mdt, 2C pwmcp, 2D cli.py
phase('Phase 3: Opus review')        // 1 Opus agent over all diffs
// FINAL CUT stays in the main session (destructive, supervised)
```
2A and 2D both could appear to touch config vs cli.py — they are disjoint (cmru.toml vs
cli.py). 2D depends on Phase 1 (same file cli.py) → the phase barrier handles it.
