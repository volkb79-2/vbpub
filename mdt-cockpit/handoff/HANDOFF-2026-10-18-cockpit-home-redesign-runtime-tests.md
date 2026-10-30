---
id: MDTC-01
title: mdt-cockpit — fresh-home redesign, mdt group, system-wide defaults, local-dev build, in-container runtime tests
contract_class: 2c
model: sonnet
base: ad46d4fa1   # baseline commit of the fork in this worktree
worktree: /workspaces/vbpub/.worktrees/mdt-home-persistence-20261018
branch: mdt-home-persistence-20261018
---

# MDTC-01 — mdt-cockpit fresh-home redesign + in-container runtime tests

Controller: Opus session (operator interview 2026-10-18). You are the single
end-to-end implementer. Every design decision below is FIXED by the operator
or the controller (`D1`..`D10`); you implement and prove them. Anything that
would change externally visible behavior beyond them is a BLOCKED trigger or a
reported question, never your own choice.

Work in phases. **Stop after Phase 2** and report (see "Report"). Phase 3
(dedicated tester-base image) is NOT part of this dispatch; the controller
will send it later.

## Context to read first (only these, in this order)

1. `mdt-cockpit/docs/HOME-PERSISTENCE.md` — current (baseline) design; most of
   its migration content is being REMOVED by D1.
2. `mdt-cockpit/Dockerfile` — whole file; focus on: user creation (~l.130-145),
   npm/AI CLI install (~l.185-200, ~l.580-620), customization install
   (~l.280-315), venvs (~l.655-740), `/srv` + PWMCP (~l.740-755), manifest
   (~l.795-1022), home-clearing RUN (~l.1024-1060).
3. `mdt-cockpit/templates/devcontainer.json`, `templates/initialize_container_environment.py`,
   `templates/migrate_persistent_home.py` (to delete), `templates/README.md`.
4. `mdt-cockpit/scripts/finalize_container_environment.py` (postCreateCommand),
   `customization/profile.sh`, `customization/README.md`, `scripts/install_ai_cli_tools.py`,
   `ai-cli-tools.list`, `scripts/install_pwmcp_instance.py`.
5. `mdt-cockpit/build-push.py`, `docker-bake.hcl` (targets `trixie-py314-php85`,
   `trixie-py314-php85-vsc`, tag helpers, PWMCP_* variables),
   `scripts/resolve-devcontainers-release.py` (how `pip/wheels.list` wheels are resolved).
6. `mdt-cockpit/run-gate.toml` (lanes `smoke`, `release`, `vm-system` — the
   `vm-system` lane is the precedent for a `bare-host` lane with `required_env`).
7. `mdt-cockpit/KNOWN_ISSUES_TODO_BACKLOG.md` items MDT-006 and the home-persistence item (~l.39-80).
8. `/workspaces/vbpub/AGENTS.md` sections "Defaults and fallbacks are hazards",
   "User-facing docs are part of the change", "Read the exit status from the job".

Facts already measured by the controller (do not re-derive):
- In this devcontainer `vscode` runs as uid **1003** (Dev Containers
  `updateRemoteUserUID` remapped it from 1000), with `NOPASSWD:ALL` sudo.
  The remap re-owns ONLY `$HOME`: `/usr/local/py-utils` is still uid 1000.
  So anything owned by `vscode` (uid 1000) at build time outside `$HOME` is
  NOT writable by the runtime user. That is why D3 uses a group.
- On overlayfs a `chown`/`chmod`/`chgrp -R` over files from an earlier layer
  copies every file up into the new layer (doubles image size).
- Claude Code's native updater writes `~/.local/bin/claude ->
  ~/.local/share/claude/versions/<v>` (visible in this devcontainer today).
- The Docker daemon is the HOST's (socket passthrough). Bind sources resolve in
  the host namespace — a devcontainer path like `/tmp/x` is NOT what the
  daemon mounts. Prefer named volumes / tmpfs in tests; never bind a
  devcontainer-local path.
- `pwmcp-v1.64.0-r2` exists only locally (`/srv/pwmcp-instance/releases/pwmcp-v1.64.0-r2-5551a86eaaa9`,
  and pwmcp worktree `dist/` dirs); its release URL 404s. That is why D9 exists.

## Decisions (normative)

- **D1 — No migration.** mdt-cockpit is an opt-in fork; a user starts with an
  empty home and copies old content manually. Delete
  `templates/migrate_persistent_home.py`, `scripts/test_migrate_persistent_home.py`,
  the legacy-source preflight refusal and the migration-marker logic in
  `initialize_container_environment.py` (+ their tests). Replace with an
  informational stderr notice (exit status unaffected) when
  `~/mdt--mounted-folders/` exists and contains any file/symlink AND
  `~/mdt-cockpit-home/` is absent or empty at bootstrap time: one line saying
  the old split state exists and pointing to the manual-copy doc section.
  Docs get a "Moving from modern-debian-tools-python-debug" section listing
  what is worth copying (`.claude/`, `.claude.json`, `.codex*/`, `.ssh/`,
  `.gnupg/`, `.minisign/`, `.config/` selectively, `.claudelink/`, `.pi/`,
  `.reasonix/`, `.openclaw/`, `.local/share/opencode/`) with a pasteable
  `cp -a` example, and an explicit warning NOT to copy the old image's
  `~/.venv*`, `~/.oh-my-zsh`, `~/.local/bin` + `~/.local/lib/node_modules`
  (old image CLIs) or `~/.codex/packages/standalone`.
- **D2 — Host source names.** Home: `${localEnv:HOME}/mdt-cockpit-home/` →
  `/home/vscode` (mode 0700). Tmp: `${localEnv:HOME}/mdt-cockpit-tmp/` →
  `/tmp` (mode 1777). Any further mount source is named
  `${localEnv:HOME}/mdt-cockpit-<purpose>`. No `mdt--mounted-folders` parent
  in live code (only in the D1 notice + docs). Host SSH stays read-only at
  `/run/mdt/host-ssh`. Update `initialize_container_environment.py` so its
  managed-source recognition, modes and FALLBACK set follow this naming.
- **D3 — `mdt` group.** Create group `mdt` with fixed GID from build ARG
  `MDT_GID` (default `2750`, documented as a policy choice; the build must
  fail if that GID is already taken in the base). `vscode` is a member.
  `/opt/mdt` and `/srv` and everything beneath them: group `mdt`, directories
  mode `2775` (setgid), regular files group-writable. Construction rule: create
  `/opt/mdt` and `/srv` as `root:mdt 2775` BEFORE any content, and run every
  RUN that writes beneath them with `umask 0002` so new files inherit group +
  g+w. A fix-up `chgrp/chmod` is allowed only on content created in the SAME
  RUN; NEVER a recursive chgrp/chmod over earlier-layer content. Add a
  build-time assertion (final stage) that finds no entry under `/opt/mdt` or
  `/srv` (symlinks excluded) that is not group `mdt`, no directory lacking
  setgid+g+rwx, no regular file lacking g+w. Remove the `vscode:vscode`
  ownership of `/srv` and `/srv/pwmcp-instance` in favour of this.
  (Security note for DESIGN-GUIDE: group-writable image tools grant no new
  privilege because `vscode` already has passwordless sudo.)
- **D4 — Tool locations.** npm CLIs: prefix `/opt/mdt/npm` (baseline already).
  `claude`, `antigravity`, `agy`: move from `/usr/local/bin` to `/opt/mdt/bin`.
  Codex stays the npm package for now (final per-tool choice comes from the
  Phase 2 measurement). Python: `/opt/mdt/venv`, `/opt/mdt/venvs/pyNN`,
  uv-managed interpreters `/opt/mdt/python`.
- **D5 — One PATH order, defined once.**
  `/opt/mdt/bin:/opt/mdt/venv/bin:/opt/mdt/npm/bin:/srv/pwmcp-instance/bin:<base image PATH>:/home/vscode/.local/bin`.
  Image tools always win; `~/.local/bin` is LAST (user additions only).
  Define the list once (Dockerfile `ENV PATH`) and have `profile.sh`
  re-establish exactly that order for login shells (Debian `/etc/profile`
  resets PATH) without duplicating entries; the template must not set a
  different PATH (drop `remoteEnv.PATH` or make it `${containerEnv:PATH}`-only).
- **D6 — Environment variables.** Image `ENV NPM_CONFIG_PREFIX=/opt/mdt/npm`
  (remove the `/home/vscode/.local` value from Dockerfile, template
  `containerEnv` and `profile.sh`). `UV_PYTHON_INSTALL_DIR=/opt/mdt/python`.
  Oh My Zsh (vsc family): `ZSH=/opt/mdt/oh-my-zsh`, `ZSH_CUSTOM` left at
  OMZ's default under `$ZSH/custom` (now group-writable); OMZ cache must land
  in the home (`ZSH_CACHE_DIR` → `~/.cache/oh-my-zsh`) and auto-update
  disabled (image owns OMZ). `VIRTUAL_ENV` stays unset. AUDIT every tool the
  image installs for further location variables (manager prefixes, caches,
  config dirs) and write the result as a table in `docs/DESIGN-GUIDE.md`:
  `variable | value | why | set in`. Any variable you add beyond the ones
  named here goes in the report's "added variables" list with its reason.
- **D7 — Defaults via system-wide config, home stays empty.** For every
  default the image ships (bash, zsh, nano, htop, mc, nvim/NvChad, less/lesspipe,
  MDT customization files, manifest), install it where the tool reads
  SYSTEM-WIDE configuration (e.g. `/etc/bash.bashrc` hook, `/etc/zsh/zshrc`
  or `zshrc.d`, `/etc/nanorc`, `/etc/htoprc`, mc's system ini,
  `/etc/xdg/nvim` with `XDG_CONFIG_DIRS`, `LESSOPEN`), so a user overrides by
  creating their own file. VERIFY each tool's actual lookup in the built image
  (don't trust memory). Only when a tool cannot be configured system-wide may
  the finalizer (postCreateCommand — the in-container late-rollout step)
  write into the home, and then only a MINIMAL STUB that sources/includes the
  image file (so image updates still reach the user), only when the target is
  absent, never replacing a user file. Expected case: `~/.zshrc` stub to avoid
  `zsh-newuser-install` — verify. NvChad: its lazy.nvim writes plugins to
  stdpath data (home, fine) and `lazy-lock.json` next to the config — point the
  lockfile into the home (stdpath data/state) so a read-only system config
  works. Drop the compatibility symlinks `~/.venv`, `~/.venv-pyNN`,
  `~/.oh-my-zsh`, `~/mdt-manifest.md` (they existed for migration) and update
  every reference (template settings, docs, scripts) to the `/opt/mdt` /
  share paths. Record per tool in DESIGN-GUIDE: `tool | system location |
  home write? (stub/none) | why`. Remove the `home-defaults` copy tree if
  nothing needs it.
- **D8 — Namespace rename.** `modern-debian-tools-python-debug` →
  `mdt-cockpit` in every LIVE reference of the fork: `/usr/local/share/mdt-cockpit`,
  `~/.config/mdt-cockpit`, `/etc/profile.d/50-mdt-cockpit.sh`, os-release
  keys/labels/manifest text, scripts, tests, docs, host-setup, templates.
  Leave HISTORICAL records verbatim (files that record a past run or decision:
  `CHANGES.md` past entries, `*-REPORT.md`, `*-HANDOFF.md`, `handoff/`,
  `benchmarks/`, `build-push.txt`, `build-images.txt`, `push-images.txt`) and
  statements that name the original project as the fork origin. The report
  lists every remaining occurrence (`grep -rIn modern-debian-tools-python-debug mdt-cockpit`)
  grouped by file with a one-line justification. Do NOT touch the original
  `modern-debian-tools-python-debug/` project.
- **D9 — Local-dev build mode.** Explicit opt-in only (never a fallback when a
  release is missing): `build-push.py --build --local-dev <manifest.toml>`
  (and the bake variable it sets). The manifest (gitignored, e.g.
  `local-dev-artifacts.toml`, example committed as `local-dev-artifacts.toml.example`)
  lists each local artifact explicitly: `name`, `path`, `sha256`, `version`.
  Covered: every first-party wheel in `pip/wheels.list` (ciu, cmru, topos,
  nyxloom, cli-extended) and the PWMCP installer/bundle consumed by
  `install_pwmcp_instance.py`. Any entry missing, unreadable, or sha256-mismatched
  → refuse the build naming it; names not in the manifest still come from
  their releases as today. The image records build mode and every local
  artifact (`name`, `version`, `sha256`, original path) in OCI labels AND a
  manifest section. Tags: `<debian>-py<python>[-<flavor>]-dev-<YYYYMMDD>[-<n>]`
  plus local-only floating `<debian>-py<python>[-<flavor>]-dev-latest`; a
  local-dev build never receives a release tag or bare `latest`. The push /
  publish path refuses any image whose build-mode label is local-dev (test it
  with a fixture; do not push anything for real).
- **D10 — Runtime test lane.** New registered lane `image-runtime` in
  `mdt-cockpit/run-gate.toml`: `kind = "command"`, `environment = "bare-host"`
  (the `vm-system` precedent: the driver only orchestrates; the subject runs in
  its own containers), `required_env = ["CGROUP_PARENT_DEV_GATES",
  "MDT_RUNTIME_IMAGE_NATIVE", "MDT_RUNTIME_IMAGE_VSC"]`. The two image vars are
  the exact refs of the images built in Phase 2 — the lane refuses when unset;
  it never defaults to a `:latest` tag. Driver: stdlib `unittest` in
  `mdt-cockpit/tests/runtime/` (no third-party deps), invoked with
  `python3 -m unittest discover`. Every container it starts:
  `--cgroup-parent "$CGROUP_PARENT_DEV_GATES"`, `--init`, a unique name
  `mdt-cockpit-rt-<pid>-<n>`, removed by EXACT name in a finally. It prints
  each image's ID + build-mode label at start.

## Work

### Phase 1 — source changes (commit when done)

1. D1: delete migration code/tests; implement the notice; rewrite
   `docs/HOME-PERSISTENCE.md` to the fresh-home design (keep the inventory
   table, updated to D2–D7; remove migration sections; add the manual-copy section).
2. D2: template mounts + bootstrap naming/modes + its tests.
3. D3: group, construction order, umask, final assertion. Do not add any
   recursive ownership pass over earlier layers.
4. D4/D5/D6: move binaries, single PATH, env vars, audit table.
5. D7: system-wide defaults, finalizer reduced to stubs/idempotent steps, drop
   compat symlinks and update references.
6. D8: rename.
7. D9: local-dev mode in `build-push.py`/bake/Dockerfile/push path + unit tests
   in `scripts/` (refusal on missing/mismatch, labels rendered, tag shape,
   push refusal).
8. Docs triad in sync (README = what, `docs/DESIGN-GUIDE.md` = why incl. the
   D-tables, `docs/CONSUMERS.md` = how: pasteable template use, manual copy,
   local-dev build example). Update `templates/README.md`,
   `customization/README.md`, `DEVCONTAINER-LIFECYCLE.md`, and the backlog
   (close/rewrite the home-persistence and migration items; MDT-006 stays
   until Phase 2 lands).
9. Gate: `cd /workspaces/vbpub/.worktrees/mdt-home-persistence-20261018/mdt-cockpit && ./run-gate.py smoke`
   (and `release` lane if you touched release-flow code). Must be green from a
   clean committed tree. Commit.

### Phase 2 — build, runtime tests, measurement (commit when done)

10. Build exactly two targets — `trixie-py314-php85` (native) and
    `trixie-py314-php85-vsc` — in local-dev mode, using the project's
    supported entry point (`build-push.py --build`). If it cannot restrict to
    those two targets, ADD a target-selection option to `build-push.py`
    (in scope) rather than hand-rolling `docker buildx bake`. Local-dev
    manifest: the local PWMCP 1.64.0-r2 bundle + the locally built wheels you
    can find (record the paths you used; compute sha256 yourself).
    Builds run detached with an exit marker (`... > log 2>&1; echo EXIT=$? >> log`),
    `nice -n 19 ionice -c 3`, ONE build at a time, and nothing else heavy
    concurrently. You WAIT for it inside your own turn (Monitor / until-loop
    on `kill -0 <pid>` — never a bare `pgrep -f` self-matching pattern); do
    not end your turn while a build or test runs.
11. Implement the `image-runtime` lane (D10). Scenarios — each is a
    behavioral oracle, both images unless marked:
    a. **Image contract (no mounts):** `/home/vscode` exists, is empty, owned
       by vscode; `id vscode` lists group `mdt`; D3 ownership/mode invariant
       holds (re-check at runtime, not only at build); every AI CLI in
       `ai-cli-tools.list` and every first-party CLI resolves (`command -v`)
       to its D4 location and runs `--version` (or the tool's equivalent)
       successfully as `vscode`; `python -c 'import sys; print(sys.prefix)'`
       is `/opt/mdt/venv`; `VIRTUAL_ENV` unset; `NPM_CONFIG_PREFIX`,
       `UV_PYTHON_INSTALL_DIR`, `ZSH` (vsc) as D6.
    b. **PATH parity:** identical PATH (exact D5 order, no duplicates) in
       `sh -c`, `bash -c`, `bash -lc`, `bash -ic`, `zsh -ic`, `zsh -lc` as vscode.
    c. **Remapped uid (devcontainer reality):** derive a throwaway image that
       performs the SAME steps as devcontainers/cli's `updateUID.Dockerfile`
       (cite the upstream file + version you mirrored in a comment) with a
       non-1000 uid/gid (e.g. 4242), then as that user: write to
       `/opt/mdt/npm` by installing a LOCAL npm tarball you create in the test
       (`npm pack` of a tiny package — no registry); `pip install` a locally
       built tiny wheel into `/opt/mdt/venv` (`--no-index`); create a file in
       `/srv/pwmcp-instance`; all succeed without sudo. Negative control: the
       same writes as a uid NOT in `mdt` fail with EACCES (proves the group,
       not world-write, grants it).
    d. **Fresh home + finalizer:** empty named volume at `/home/vscode`
       (chowned to the run uid by a root pre-step), run the template's
       `postCreateCommand` as vscode; assert the home afterwards contains ONLY
       the D7 stub set (+ dirs the tools themselves create, each listed and
       justified in the test); run it a second time and assert the home tree
       (paths, modes, content hashes) is byte-identical; pre-create a user
       `~/.zshrc` / `~/.config/htop/htoprc` with sentinel content in a fresh
       volume, run the finalizer, assert sentinels unchanged.
    e. **Interactive shells clean:** `bash -ic true`, `zsh -ic true` (vsc and
       native) produce no stderr output (no newuser wizard, no OMZ permission
       or cache errors) in the fresh-home container from (d).
    f. **System-wide defaults effective:** for each D7 tool, prove the tool
       actually loads the system default with an empty home (e.g.
       `nvim --headless` reports the NvChad config loaded / `:checkhealth`-free
       probe of a setting; `htop`/`mc`/`nano` via a setting observable
       non-interactively — choose a real observable per tool and name it in
       the test docstring). A user file in the home overrides it.
    g. **Local-dev provenance:** labels list the local artifacts; the
       installed wheel versions / PWMCP version inside the image equal the
       manifest's versions.
    Run the lane: `MDT_RUNTIME_IMAGE_NATIVE=<ref> MDT_RUNTIME_IMAGE_VSC=<ref> ./run-gate.py image-runtime`
    (exact form per run-gate's CLI). Green required. Each scenario must have
    been seen RED once against a deliberately broken image or fixture (e.g.
    a derived image with a file left in `/home/vscode`, a wrong PATH order,
    a file not group-writable); record the controlled breaks in the report.
12. **Per-tool update-mechanism measurement (probe, not a gate test; network
    allowed).** In a throwaway container from the vsc image (remapped uid as in
    c, empty home volume), for each AI CLI (claude, codex, copilot, opencode,
    pi, reasonix, openclaw, claudelink, antigravity/agy, aider): find its
    documented self-update command / auto-update behaviour, run it, and record
    `tool | installed at | update command | what the update wrote (paths) |
    works as vscode via mdt group? | resulting `command -v` | survives
    container recreate? | notes`. Do not change any design based on it —
    the operator decides afterwards.
13. Update MDT-006 (close if (11) covers its acceptance; else narrow it) and
    docs. Commit.

## Oracles (summary — each must be able to go red)

| # | Claim | Observable | Negative it rejects |
|---|---|---|---|
| O1 | Home empty in image | 11a + build assertion | file baked into `/home/vscode` |
| O2 | mdt group grants writes, no world-write, no size blow-up | 11c incl. negative control; report final image size vs the locally present `ghcr.io/volkb79-2/modern-debian-tools-python-debug-vsc-devcontainer:trixie-py3.14-php8.5-latest` (do NOT build a baseline), plus `docker history` of the new image showing no permission-only RUN layer of /opt/mdt size | recursive chgrp layer; world-writable shortcut |
| O3 | One PATH order everywhere | 11b | profile.sh reordering, template PATH override |
| O4 | Defaults are system-wide, home writes only stubs, idempotent, never clobber | 11d/11e/11f | full copies into home; second run changes tree; user file replaced |
| O5 | Local-dev is explicit, verified, labelled, unpublishable | 9 unit tests + 11g | silent fallback to release; sha mismatch accepted; dev image pushable |
| O6 | Docs in sync | every config/command example in README/DESIGN-GUIDE/CONSUMERS matches shipped names (D2/D8); grep shows no live old names | stale `mdt--mounted-folders`/old namespace in a live doc |

### Test rules (from nyxloom AUTHORING §3b — binding)
- A. No verdict may depend on machine speed: no `deadline`/`sleep`-then-assert,
  no elapsed-time assertions; wait on real sync points (`docker wait`, process
  exit). Timeouts only as generous failsafes.
- B. No dependence on test order or global state; each test its own container
  names/volumes; restore anything you change; clean up by exact name.
- C. No hollow tests: assert the contract (observable outcome), not call
  counts or log strings; never weaken an assertion to pass.
- D. No coverage pragmas.
- E. Unit tests: no real network/registries/clock (inject). The runtime lane
  is offline except where a scenario says otherwise (none of 11a–g needs network).
- F. No predicted measurements — report sizes/numbers you measured.

## Scope

Touch: `mdt-cockpit/**` only, plus `.gitignore` at worktree root if needed for
the local-dev manifest. Forbidden: `modern-debian-tools-python-debug/**`,
`tester-unified/**`, any other project, pushing/publishing any image or tag,
`git push`, merging, host-setup *installation* on this host (editing host-setup
sources for D8 is fine), deleting any Docker resource you did not create (remove
by exact name only), all-container inventories (`docker ps -a` unfiltered,
`docker events` unfiltered).

## Rules
- Every Bash tool call MUST set the tool's `description` parameter to a short
  plain-language statement of what the command does and why (a successor reads these).
- Host load: this 8-core host runs a production game server. pytest serial
  only under `nice -n 19 ionice -c 3`; one build or gate container at a time;
  no build concurrent with a suite. Prefer registered lanes over ad-hoc pytest;
  do not run ad-hoc whole-suite pytest in the devcontainer.
- Read exit status from the job (marker lines, `PIPESTATUS`), never through `| tail`.
- Edit files with the Edit tool (no sed/python rewrite scripts for source edits).
- Commit in this worktree (private index) at the end of each phase; message
  ends with `Co-Authored-By: Claude Sonnet <noreply@anthropic.com>`.
- No `git stash`.

## BLOCKED rule
If a named contract cannot be met as specified (e.g. a tool provably cannot be
configured system-wide AND a stub cannot work; the supported build entry
cannot build the two targets even after adding target selection; the D3
assertion cannot hold without a recursive ownership pass), or scope requires a
forbidden file: STOP — write `BLOCKED: <reason + evidence>` at the top of your
report, commit what is done, and exit. Do NOT improvise a workaround.

## Report (end of Phase 2, your final message)
1. Commits (hash + subject) and `git status --short`.
2. Traceability table: `work item | files | test(s) | controlled break seen red (how)`.
3. Gate evidence: lane, command, exit marker line, test counts — `smoke`,
   (`release` if run), `image-runtime`.
4. Built image refs + IDs + sizes (and baseline-for-comparison if you built one) and the local-dev manifest used.
5. D6 added variables; D7 per-tool table; D8 residual-occurrence list.
6. The update-mechanism table (12).
7. Open questions / anything you could not verify.
