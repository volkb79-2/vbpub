---
name: nyxloom-merge-p
description: The nyxloom package merge pipeline — run after a fix-verification ACCEPT. Ledger record, --no-ff merge, post-merge gates with separate verdict reads, progress row, memory, teardown. Use in any nyxloom-registered project.
---

> **Tool versions as of last verified update (2026-09-03):** nyxloom
> `0.3.1.dev1263+gf3b89f46`, run-gate `23.5.0` (pip-installed), ciu `7.11.0`.
> `assay` pip package is `5.0.0` (v9→v10 verdict schema, a hard cut — see
> `assay/docs/CONSUMERS.md` "Migration notes (v9 → v10)"). A consuming
> project's own gate lanes may still pin an older frozen `.pyz` artifact in
> `run-gate.toml`'s `[lanes.*.pins.assay]` blocks — check both before assuming
> the newer schema applies. If any CLI verb/flag below errors, check
> `nyxloom --version` / `pip show run-gate` / `python3 -c "import ciu; print(
> ciu.__version__)"` against these before assuming the skill text is still
> right — re-verify and bump this line rather than patching around a mismatch
> silently.

> **Canonical, repo-agnostic skill.** The pipeline SHAPE is universal to any
> nyxloom-registered project; substitute the target repo's own trove paths and
> gate argv from its own CLAUDE.md/AGENTS.md. Examples below use dstdns's
> conventions — read them as illustrations, never run a dstdns gate line
> against another repo.

# Package merge pipeline (run from `<repo-root>`, main checkout)

Precondition: the ORIGINAL code reviewer (resumed, holds review context) stated
ACCEPT unambiguously. Never merge on the implementer's self-report.

1. **Ledger**: append the fix-verification decision record to `<trove>/
   decisions.md` (verdict, probes re-run, gate numbers reproduced, backlog
   riders); commit.
2. **Merge**: `git merge --no-ff --no-commit p<NNN>-<slug>` — apply any
   pre-adjudicated in-merge corrections (e.g. LOG self-hash chicken-and-egg), then
   commit with a body naming the package's what/why, pipeline decision records,
   branch gate numbers, and the trailer.
3. **Post-merge gates from main** — never trust branch numbers. `run-gate` (pip-installed)
   dispatches every lane into the CIU-managed `test-runner`. **RG-39 shipped
   2026-09-03 (run-gate 23.5.0, rev 35):** an internal exec-mode mutex keyed
   by the resolved container name now serializes concurrent consumers of the
   SAME container automatically, so the caller-side `flock` below is no
   longer load-bearing for correctness — keep using it anyway (cheap outer
   lock, lets you skip a busy container rather than block inside run-gate):
   - `eval "$(ciu env print)" && flock "/tmp/${REPO_NAME}-${INSTANCE_ID}-testrunner.lock" run-gate gate > <scratch>/pNNN-postmerge-gate.log 2>&1; echo exit=$?`
     — the full composite (dstdns example: `schema && test-runner && assay && assay-dlq && ui_unit`,
     check `[lanes.gate]`'s argv in `run-gate.toml` for the current chain, it can change). Omit
     `--worktree` when running from the main checkout itself; pass
     `--worktree <repo-root>/.worktrees/<branch>` only when judging a worktree.
     A lane deriving its own changed-line comparison base internally (e.g. via a
     `gate-base.sh`-style script) needs no `--base` flag — passing one may be
     refused by name. Otherwise a bare `run-gate gate` can silently fall back
     to `git merge-base HEAD @{upstream}`, which can be far stale — do not
     trust a RED from a changed-line lane member at face value without
     checking that fallback base first.
   - Read the verdict in a SEPARATE step (`grep 'passed\|failed' … | tail`), never
     a pipe tail. Account EVERY delta vs the prior main baseline test-by-test
     (a worktree-vs-main artifact reversal is a known class: main can add
     rendered-output tests or an xfail→xpass vs a worktree run).
   - **The composite does not cover every lane.** A package-specific `assay-*` lane
     may not be wired into `[lanes.gate]`'s argv — treat this as a standing gap
     to track per-project, and check the composite's actual argv, running
     `run-gate <lane>` for anything the package's own review/oracles depend on
     that isn't in it explicitly; the composite going green is not evidence an
     unwired lane did.
   - `run-gate --list` to discover current lane names if unsure; never hardcode a
     lane name from memory without checking it still exists in `run-gate.toml`.
4. **Progress row** in the program's current plan doc; commit.
5. **Memory**: update the program state file + memory index (merged block: hash,
   gate numbers, pipeline summary, riders).
6. **Teardown**: `ciu worktree rm <branch> -y` (preferred — runs `ciu clean` then
   removes the checkout) or `git worktree remove .worktrees/<branch>` + `git
   branch -d` as a fallback; for Mode-B stacks verify the teardown resolved BOTH
   volume prefixes (`<branch>-<instance>-*` AND bare `<branch>-*`).
7. Docs lifecycle: on merge, `git mv` completed plan/spec docs to
   `docs/archive/{plans,specs}/` per the project's own "docs lifecycle on merge"
   rule.
