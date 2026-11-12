<!-- origin: 2026-11-08; status: evidence; copied from the controller program dir 2026-11-10 -->
<!-- source: reports/codex-image-REVIEW2.md -->
<!-- relation: Codex image review; verbatim source text follows -->
<!-- Evidence scrub 2026-11-10: environment-specific home, checkout, worktree, and per-run temporary paths were normalized to neutral placeholders; command output and verdict text are otherwise unchanged. -->

# Codex image independent re-review (round 2)

Head `ed23b19b8` (worktree `<worktree>`, branch codex-image-20261108). Review ran from a scratch `git archive` copy (+ root files, own `git init`) in `<controller-scratch>/rv-cimg2/`. The worktree stayed clean (`git status` empty). No image build, npm install, push, merge or host access; the only real-binary call was `/opt/mdt/npm/bin/codex --version` (no model call).

**Verdict: MERGE.** Both round-1 blockers are fixed; no new blocking findings.

## B1: verify_codex_install runs the installed launcher
- `verify_codex_install()` ends with `verify_codex_launcher(prefix/bin/codex, version)`: executable-regular-file check, then `subprocess.run([str(launcher), "--version"], capture_output, text, timeout=60, check=False)`. List argv, no `shell=True`. OSError and TimeoutExpired become InstallerError; non-zero exit refused; empty output refused; stdout+stderr both read.
- Own fake (not the author's fixture): self-consistent gnu tree (package.json, platform package, gnu vendor dir, manifest, executables) with a launcher that selects the musl target like `codex.js`: refused ("exited 1: Error: Missing optional dependency"). The same tree with a musl vendor dir passes, also with `HOME` unset.
- Real tree `/opt/mdt/npm` pinned 0.167.0: passes with the real launcher run; pins 0.167.1, 0.16.0 and 1.167.0 refused (at the package.json version check).
- Token regex `(?<![\w.+-])VER(?![\w.+-])`: matches `codex-cli 0.167.0`, `0.167.0` and a trailing newline or ` (abc)`; rejects `10.167.0`, `0.167.01`, `0.167.0-alpha.1`, `x0.167.0`. It is also strict on `v0.167.0` and a trailing `0.167.0.`; neither occurs in the real output (`codex-cli 0.167.0`), so I count this as acceptable strictness, not a defect.
- Timeout: a hanging launcher (`sleep 300 & wait`, timeout set to 2 s) was cut off at 2.0 s with InstallerError. Environment is inherited (no `env=`), so PATH (needed by the `#!/usr/bin/env node` launcher) and HOME pass through; `NPM_CONFIG_PREFIX` is required and never guessed (Dockerfile lines 243 and 723 set it).
- Nit (not blocking): `subprocess.run` kills only the launcher process; a grandchild is orphaned (my `sleep 300` survived and I killed it). Real `--version` of the native binary exits at once, so this is cosmetic.

## B2: merge with origin/main
`git fetch origin` leaves origin/main at `11b4a7c34`; it is an ancestor of `ed23b19b8`. `git merge-tree --write-tree origin/main ed23b19b8` exits 0 (no conflict). The `CHANGES.md` Unreleased section keeps upstream's Changed, Removed and host-endpoints Added entries together with the Codex Fixed and Added entries. `git diff origin/main...HEAD --stat` shows 11 files only.

## Tests (each file once, serial, `nice -n 10`, scratch copy)
`test_install_ai_cli_tools.py` 76 passed; `test_oci_tool_staging.py` 90 passed; `test_hs02.py` 30 passed; `test_excluded_capacity.py` 71 passed. The scratch needed `cmru.orchestration.toml`, `pwmcp/` and a git repo to run the staging tests; these are environment, not product, issues.

## Hand mutants on the launcher guard (all killed)
| # | Mutant | Killed by |
|---|---|---|
| 1 | launcher run removed | `test_a_self_consistent_but_wrong_linux_target_fails_at_the_launcher` |
| 2 | token match loosened to `version in output` | `test_launcher_version_must_be_the_whole_pinned_token[... alpha.1]` |
| 3 | timeout removed | `test_launcher_that_hangs_is_cut_off` (runs the 30 s sleep) |
| 4 | exit-code check removed | the wrong-target test and `test_launcher_failing_with_the_right_version_text_still_fails` (2 failed) |
| 5 | empty-output check removed | `test_launcher_printing_nothing_fails` |
| 6 | launcher exists/executable check removed (extra) | `test_missing_launcher_fails_and_names_the_path` |
| 7 | stderr dropped (extra) | wrong-target test |
Scratch source restored after each mutant (76 passed again).

## Gate log
`logs/codex-image-gates3.log`: smoke run on commit label `ed23b19b8` started 19:32:55Z: 1484 passed, 1 skipped, 7 deselected, 240 subtests, "smoke: OK", verdict PASS. `host-setup-admission` started 19:44:26Z on the same commit: 238 passed, 114 subtests, verdict PASS. Last source mtimes are 19:20:58 (installer), 19:21:30 (tests), 19:22:56 (test_hs02), 19:23:07 (CHANGES.md, merge commit `%cI` 19:23:07Z); both gates started after them, on the exact head.

## Scope
Changed paths are only mdt-cockpit installer, Dockerfile, README/CHANGES/docs/CONSUMERS/DESIGN-GUIDE, handoff/MDT-P01-REPORT.md (superseded note), `scripts/test_install_ai_cli_tools.py`, `scripts/test_oci_tool_staging.py` and the two host-setup tests `test_hs02.py` and `test_excluded_capacity.py`. `install_binaries_from_archive` is gone (only the CHANGES.md mention remains). The Dockerfile is unchanged by the fix round.

Merge note: the unpushed foreign commit `897de671c` conflicts textually with this branch only in `scripts/test_oci_tool_staging.py` (from round 1); keep both sides when it is integrated.
