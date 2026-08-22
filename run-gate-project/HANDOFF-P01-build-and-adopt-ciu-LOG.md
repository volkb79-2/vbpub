# HANDOFF-P01 LOG — build `run-gate.py` + adopt (first consumer)

Contract: `HANDOFF-P01-build-and-adopt-ciu.md` (vbpub main `910d8b8e`).
**Scope amendment A1 (controller, 2026-08-22, this session):** first consumer
is **nyxloom**, not ciu — ciu is under parallel development; its tree is
untouched. All other contract terms hold; `SPEC.md` written first as the
normative adherence target (controller request). Worktree
`.worktrees/run-gate-p01`, branch `feat/run-gate-p01` (base `e41adac0`).
Every claim below names its command; failures are logged where they happened.

## Chronology

1. **Spec first** (controller directive): wrote `run-gate-project/SPEC.md`
   (R-01..R-20 + amendments A1/A2/A3) BEFORE finalizing code; reconciled the
   draft implementation against it. Two adherence fixes fell out immediately:
   `find_project_dir` used `.resolve()` (follows symlinks — violates §1/R-01);
   pin `version` unvalidated (R-08).
2. **Unit suite** (`tests/test_run_gate.py`, fake-docker PATH shim, argv
   pinned as LISTS). Iterated to **51 passed**. Suite-caught impl bugs:
   - **sorted(parents) broke nearest-ancestor discovery** — Path.parents is
     already nearest-first; lexicographic sort inverted it (caught by
     `test_nearest_ancestor_wins`).
   - **`--git-common-dir` is relative to CWD, not toplevel** — joining it
     onto the toplevel relocated the repo root to tmp (caught by
     container-name pin; verified empirically with a scratch repo).
   - Test-side: hyphenated filename needs importlib (not `import run_gate`);
     POSIX printf has no `\xHH` (octal `\037`); `echo "$@"` destroys argv
     quoting (lossless `\x1f`-joined recording); module monkeypatches do NOT
     cross the subprocess boundary (assert REAL derived paths instead).
3. **Commits:** `9f2f765d` tool+spec+tests; `09cd343d` adoption (central
   `run-gate.toml` at vbpub root, `nyxloom/run-gate.py` symlink,
   `nyxloom/run-gate.toml` lane, thin `[gates]` argv, AGENTS.md pointer).
   Build-time catches: TOML has no implicit string concat (lane file
   rewritten); exec bit required on the symlink target.
4. **O4 live acceptance, run 1 — FAILED, and the failure was a REAL find:**
   `./run-gate.py tester-unified` (worktree, ambient
   `$CGROUP_PARENT_DEV_BACKGROUND=dev-background.slice`) → pytest
   `test_lint.py::TestConfigLintSchema::test_repos_own_config_no_findings`
   RED with CFG1 `backlog_entries: unexpected 'agent_logs','worktree_root'`.
   Root cause NOT run-gate: the `feat/nyxloom-backlog-entries` MERGE
   (`6e966fa9`, zero textual conflicts) had relocated `[backlog_entries]`
   above those bare keys — TOML swallows subsequent bare keys into the last
   table — silently breaking main's dogfood lint. The branch's own gate ran
   green pre-merge; the reorder was a semantic shift no textual merge can
   see. Fixed in `37a3b91b` (keys moved above the table + ordering warning
   comment). **run-gate's first live act was surfacing main-level breakage
   the merge machinery had certified clean.**
5. **O4 live acceptance, run 2 — PASS.** Verbatim tail:
   ```
   diff-coverage OK: 0/0 changed executable lines covered (100.0% ≥ 100.0% floor)
   run-gate: lane 'tester-unified' exit 0
   EXIT=0
   ```
   (0/0 is correct: this branch touches no `src/nyxloom` lines.) The printed
   docker argv shows every absorbed behavior live: dual mounts
   (`-v /home/vb/volkb79-2/vbpub:...` AND `-v ...:/workspaces/vbpub`),
   `--cgroup-parent dev-background.slice` + `-e CGROUP_PARENT_DEV_BACKGROUND=...`,
   `safe.directory '*'`, `{worktree}` substituted, detached `-d --name
   run-gate-vbpub-tester-unified-...`. Full log: `/tmp/opencode/o4-live-2.log`.
6. **Docs sync:** README status → BUILT + built-deltas section; CONSUMERS →
   final schema + central-defaults + A1 recipes; SPEC §8 amendments.

## Oracle status

| oracle | status | evidence |
|---|---|---|
| O1-ux | PASS | `--help`/`--list`/unknown-lane tests; no-config = one line, no traceback (test_no_config_is_one_line_not_traceback) |
| O2-failfast | PASS | 13 config-error cases each naming key+file; missing `$CGROUP_PARENT_DEV_BACKGROUND` names var + alternative; no fallback slice/path literals in source (test_no_stdlib_violations + grep) |
| O3-unit | PASS | 51/51 in fresh venv-less run (`python3 -m pytest`); argv pins assert LISTS (dual mounts, `-e` both-ways, pin-verify cwd, `-d --name` detachment) |
| O4-live | PASS (run 2) | verbatim lines above; run 1's failure was a true positive against main (see chronology #4) |
| O5-adoption | PASS (A1 form) | `nyxloom-trove/nyxloom.toml` gate argv is the pointer form; old incantation deleted from config and PRESENT behavior-equivalent in tool (inner command byte-comparable); AGENTS.md pointer landed |
| O6-docs | PASS | README BUILT + deltas; CONSUMERS final schema matches shipped validator (its examples are the test fixtures' shapes); deviations recorded here + SPEC §8 |

## Deviations from the handoff as written (all controller-sanctioned)

- A1: nyxloom-first; ciu untouched (O4's "ciu assay gate live" clause
  satisfied in A1 form: nyxloom's real gate live; assay-kind path
  construction-tested, live proof deferred to ciu adoption).
- A2: central repo-root config (handoff didn't know it; controller added it).
- Config discovery: script-parent-first (handoff said CWD-first; the symlink
  case is the whole point of vbpub-internal distribution — recorded in
  README built-deltas #2).
- `run-gate-project/run-gate.py` exec bit + LOG filename kept per contract
  despite the ciu-scope rename inside.

## Estate cgroup inventory (controller question, answered)

- ciu committed gate: `$CGROUP_PARENT_DEV_BACKGROUND` (env, LoadState-guarded).
- cmru tester-gate: `CMRU_TESTER_CGROUP_PARENT` > `$CGROUP_PARENT_DEV_BACKGROUND`,
  probe-verified via throwaway container (systemctl shimmed here).
- nyxloom committed gate (pre-P01): hardcoded `nyxloom-gates.slice` —
  **migrated by this package** to ambient resolution (A3).
- topos gate: same literal slice per nyxloom.toml history — NOT migrated
  (topos untouched this package; same migration applies when it adopts).
- Manual four-trap runs: `$CGROUP_PARENT_DEV_BACKGROUND`.
- `nyxloom-gates.slice` (CPUWeight=25, IO caps): reserved for the future PROD
  nyxloomd instance outside this repo; a declared `cgroup_slice` is the
  mechanism to name it there.

## Adversarial review round 1 (fresh reviewer, blind-then-reconcile)

**VERDICT: ACCEPT-conditional** — 3 blockers, all mechanical, all fixed here:

1. **Hollow wiring (3 behaviors survived deletion with zero reds):** the
   LoadState guard CALL on the lane path (R-11), `docker logs -f` streaming
   (R-17), success-path container cleanup (R-15). Fixed: three wiring tests
   observe side effects of the real lane path (in-process main() with a
   selective isdir patch — a blanket `isdir→True` breaks `shutil.which`,
   whose `_access_check` skips anything isdir() calls a directory; that trap
   is now written down). **Mutation-verified:** each reviewer mutation now
   reds exactly its test (1 failed each), suite 54/54 restored.
2. **CONSUMERS.md shipped an unpasteable example** (assay lane without
   `assay_command`/`sha256` — rejected by the shipped validator). Fixed to
   the fixture shape.
3. **R-09 error named only the project file** even when a central file
   exists (the spec requires naming BOTH candidates). Fixed: the message now
   interpolates the central path + what it defines.

Also folded in (reviewer non-blocking): README's stale `ciu.env`/
`$PHYSICAL_REPO_ROOT` mechanics replaced with the shipped mountinfo
derivation (design-authority prose now matches the code).

Reviewer-verified independently: 51→54 unit green; O4 live PASS reproduced
(LIVE_EXIT=0, dual mounts, both slice mechanisms, detached form); adoption
flag-for-flag behavior-equivalent to the old argv; forbid-list clean (11
allowed paths only); worktree clean after sanctioned mutations.

Known non-blocking observations (recorded, not fixed): argparse usage errors
exit 2 with 2-line stderr (no traceback; R-04 forbids tracebacks, not
argparse's shape); lane names unvalidated against docker's name charset;
negative `docker wait` codes surface as 255 through sys.exit; `memory` on a
host lane validates then ignores; `--list extra` ignores the positional.
