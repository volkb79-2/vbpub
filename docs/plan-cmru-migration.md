# Plan: migrate all of vbpub fully to cmru

**Status:** APPROVED 2026-06-18 — full migration P1–P5, then release ALL products.
Naming locked: every user-facing file is `cmru.*` (see SPEC S-CLI). Spec written first.
**Date:** 2026-06-18.
**Driver:** the release CLI was confusing (advertised `publish` verb 404'd; `release`
only tagged; two config schemas; per-product scripts conflicting with cmru versioning).
User asks: make it intuitive **and** "ensure our whole vbpub and its projects is migrated
fully to using cmru."

This extends (does not replace) `docs/plan-release-architecture.md` (the immutable-release
+ resolver + thin-pointer design, SHIPPED 2026-06-16). That covers *the release scheme*;
this plan covers *moving every product onto the cmru CLI + one config schema*.

---

## A. What is already fixed (this session, committed)

CLI coherence in `cmru/src/cmru/cli.py` — done, tests green (75 passed):

- **`publish` verb implemented** — runs each project's `push` step (`build`/`publish` are
  now project-oriented: `cmru build [--project P]`, `cmru publish [--project P]`).
- **`release` is the real one-shot** — `detect → tag → push tag → build → publish`
  (with `--dry-run`, `--no-build`). Handles a half-finished release (re-uses the tag
  already on HEAD) so it is idempotent.
- **`status`/`release` restricted to the orchestrated set** (`orchestration.project_order`)
  so projects with their own pipelines are never silently auto-tagged. Cleaned up the two
  erroneous local-only tags (`tls-edge-v0.2.1`, `empyrion-translation-v0.1.0`).
- **Raw single-step runner moved to `run-step`** (was the hidden meaning of `build`).
- **Usage rewritten** with an explicit "TYPICAL WORKFLOW" + ordering; "writes to GitHub"
  marked. `release-all.py` / `release-runner.py` docstrings clarified; `release-runner.py`
  rewritten (it passed the dead pre-verb flag interface → now drives `cmru release` +
  optional `cmru cleanup`, teed to `logs/`).

## B. Current state (the mess this plan removes)

| Concern | Today | Problem |
|---|---|---|
| Config schema | `release.toml` (legacy `[projects.X.steps]`) read by everything **except** `cmru get`, which reads `cmru.toml` (S2 `[project.X]`) via `config.py:load_forge_config` | Two schemas, two loaders; SPEC.md documents S2 but the CLI ignores it |
| Versioning | cmru tag-based (scm/patch/counter) | per-product scripts re-derive their own versions |
| ciu | wheel via cmru built-in handlers (P8; no ciu release scripts) | conforms ✓ |
| cmru | wheel via cmru built-in handlers (P8 dogfood; zero step scripts) | conforms ✓ (dogfood) |
| modern-debian-tools-python-debug | images via `build-push.py` | conforms ✓ |
| pwmcp | `resolve-playwright-version.py` rewrites tracked files + uses **counter `-r<N>`** in `.release-vars` | dirties tree (breaks clean-tree guard); cmru `pwmcp-v1.61.1` tag ≠ published version |
| tls-edge | `scripts/release.sh` does its **own** bump+tag+build+publish | double-tags vs cmru `release`; not in `project_order` |
| empyrion-translation | `release-empyrion-translation.py` uses **date tags** `empyrion-de-translation-YYYYMMDD` | unrelated scheme; cmru `…-v0.1.0` meaningless |
| `release-manager/` | old package, pre-cmru | superseded; retire (lineage: release-manager → cmru → ciu-forge, see plan-ciu-forge.md) |
| `cmru/build/lib/…` | stale `python -m build` output committed-ish in tree | should be gitignored |
| `docs/RELEASE-TOOLING.md` | references `release_manager.step_runner`, old `--config` flag interface | stale; rewrite for the verb CLI |
| CLI tests | none for `cli.py` dispatch | add verb-level tests |

## C. Target end-state

1. **One schema:** unify on S2 `cmru.toml` at repo root. `cli.py` reads it through a single
   loader (extend `config.py:load_forge_config` to feed the orchestrator, retire
   `cli.py:load_config`'s legacy path). `release.sample.toml` → `cmru.sample.toml`.
2. **Every product released through `cmru release`** — no bespoke tag/publish scripts.
   Per-product `build`/`push` steps may still shell out to project build scripts, but
   **tagging + GitHub-release creation + checksums happen only in cmru**.
3. **Versioning declared per project** in `[project.X.version]` (`scm` | `counter` |
   `file:`): pwmcp → `counter`; ciu/cmru/tls-edge → `scm`; empyrion → decide (counter or
   retire from cmru).
4. **No tree-dirtying during release:** pwmcp's playwright-version resolution becomes a
   pre-release commit step (commit the bump, *then* tag), not an in-`build` mutation.
5. `release-manager/` deleted; `RELEASE-TOOLING.md`/`VERSIONING.md` updated; CLI dispatch tests added.

## D. Phased execution — DONE

- **P0 ✅** CLI verb coherence (publish/build/release one-shot, clear usage) + wrapper scripts.
- **P1 ✅** One schema: `cli.py:load_config` reads S2 `cmru.toml` (legacy keys tolerated one
  release); `cmru.toml` + `cmru.secret.toml` + `cmru.sample.toml`; `./cmru.py` entry; token
  resolution S2.4; +6 CLI dispatch tests.
- **P2 ✅** pwmcp `version.strategy = "delegated"` — cmru detects the change and runs
  build/publish; `_run_delegated_project` builds → commits & pushes the resolver's build-input
  bump → publishes (no tree-dirty; tag == published version). `.release-vars` → `cmru.vars`;
  credentials → `cmru.toml`/`cmru.secret.toml`.
- **P3 ✅** tls-edge → `cmru.vars` + cmru credentials; `delegated`. Kept OUT of `project_order`
  (release.sh needs an explicit version → not unattended-safe); on-demand command documented.
  *(Did not split release.sh — delegated is the lower-risk, equivalent outcome.)*
- **P4 ✅** empyrion-translation → `delegated`, OUT of `project_order` (date-tagged game asset);
  on-demand release documented.
- **P5 ✅** Retired `release-manager/` (no code left; source already in `cmru/`); `cmru/build/`
  gitignored; rewrote `RELEASE-TOOLING.md`, updated `VERSIONING.md`; renamed every
  `build-push.toml` → `cmru.build.toml`; discoverable `cmru.*.sh` shims + README section.

**Auto-release set (`project_order`): ciu, cmru, modern-debian-tools-python-debug, pwmcp.**
Delegated/on-demand: pwmcp (in project_order, self-versioned), tls-edge, empyrion-translation.

## E. Open decisions (need user input)

1. **Run the actual release now, and for which products?** The orchestrated, cmru-clean set
   is **cmru** (wheel) + **modern-debian-tools-python-debug** (images) + **pwmcp**
   (images+bundle; ~GB build, dirties tree). ciu is unchanged → skipped. Releases are
   immutable + public. Note: cmru itself now has uncommitted fixes → its tag should be
   re-cut to `cmru-v0.2.2` after committing.
2. **Migration depth:** full P1–P5 (retire legacy schema + bespoke scripts) vs. stop at P0
   (CLI is now coherent; products keep current steps).
3. **empyrion-translation:** in-scope for cmru, or out-of-scope game asset?
