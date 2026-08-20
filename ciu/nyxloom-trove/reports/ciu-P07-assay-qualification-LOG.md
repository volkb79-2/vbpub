# LOG — ciu-P07-assay-qualification

- Package: `ciu-P07-assay-qualification`
- Branch: `docs/ciu-P07-assay-qualification` (forked from origin/main @ `98549075`)
- Handoff input_revision: `71f5ec79`
- Status: COMPLETE (BLOCKED raised then UNBLOCKED by controller; vendoring path authorized)
- Commits: `3271681f` (BLOCKED LOG) → `b68e8a4d` (Assay-backed gate + vendored Assay) →
  `f087b00d` (lane env passthrough) → this LOG

## The BLOCKED trigger and its resolution

`3271681f` recorded escalate_if #1: released Assay v2.1.0 was NOT installed in
tester-unified (`cmru tester-gate` probes: `ModuleNotFoundError` in
`/opt/tester-venv`; no wheel/pyz vendored anywhere). Installation was out of
P07's original scope (tester-unified forbidden; no vendoring slot).
**Controller resolution (2026-08-20): vendoring is the accepted estate path.**
Evidence found: the released, verified zipapp is already vendored by CMRU at
`cmru/tools/assay/assay-2.1.0.pyz` + `.sha256`, pinned in `cmru.toml`
(`[[project.tool_dependencies]]` sha256) and verified/consumed in its own
gate (`cmru.toml [steps.run-tests]`: `sha256sum -c` + the zipapp's `run`).

## Probe (exact commands, before edits — recorded in 3271681f)

`cmru tester-gate --cwd ciu -- bash -c 'which assay; assay --version; python -c "import assay"'`
on `tester-unified:local` AND `tester-unified:ciu-gate113` →
`bash: line 1: assay: command not found` / `ModuleNotFoundError: No module named 'assay'`.

## Work done

Scope.touch + three documented gate-setup extensions (below). `src/ciu`,
`nyxloom-trove/decisions.md` untouched.

1. **Vendored the released Assay artifact** (`tools/assay/assay-2.1.0.pyz` +
   `.sha256`), byte-identical to cmru's vendored copy (`sha256sum` match
   `f2f13021…`); verified `sha256sum -c` → `OK`. Root `.gitignore`'s `*.pyz`
   exception mirrored (`!tools/assay/*.pyz`, cmru precedent).
2. **`assay.toml`** — lane `ciu`: `rigor = ["R0", "R1"]`, argv =
   `run-ciu-tests.py` (whole-source 100% line+branch), snapshot
   `repository-minus-unsafe-symlinks` declaring the monorepo's three
   absolute-target topos fixtures verbatim (complete set verified by scanning
   all tracked symlinks), judge `fail_under=100.0`, `require_branch=true`,
   `allow_excluded=false`, `base="origin/main"`, coverage artifact
   `coverage.json`. `env` = `PYTHONPATH=src, PYTHONDONTWRITEBYTECODE=1`;
   `env_passthrough = ["CGROUP_PARENT_DEV_BACKGROUND","HOME","PATH","TERM","LANG"]`
   (measured: the four governance tests read `CGROUP_PARENT_DEV_BACKGROUND`
   by design S15.2; xdist/git need PATH; HOME for `~/.config` writes — a bare
   `env_passthrough=[]` red'd the suite).
3. **`nyxloom-trove/nyxloom.toml`** — `[gates.tester-unified]` argv replaced
   the retired `nyxloom.coverage_gate` with: `${CGROUP_PARENT_DEV_BACKGROUND:?}`
   (no literal, no fallback) → `systemctl show --property=LoadState --value`
   must equal `loaded` (fail-closed) → `docker run --cgroup-parent="$CGP"`
   → inner: `mkdir -p .assay && sha256sum -c` the pin → the Assay zipapp's
   `run ciu --verdict-json .assay/verdict-ciu.json`. The gate's exit status IS
   the Assay job's (set -euo pipefail chain; no wrapper/pipe masking).
   `asserts` gained `assay-verdict`.
4. **`_last-summary.txt` gitignored** (ciu/.gitignore): the Assay lane refuses
   a dirty tree (S18.4) and the controller artifact is never committed — a
   non-ignored untracked file would red every gate run forever.
5. **Docs** (all in scope.touch): SPEC **S18** (S18.1 pinned artifact,
   S18.2 lane contract, S18.3 cgroup fail-closed, S18.4 status/evidence +
   clean-tree, S18.5 manual reproduction); CONFIG.md gate-artifacts section;
   CONSUMERS **§10** (pasteable gate reproduction + contract notes);
   DESIGN-GUIDE (why vendored-zipapp, why R1+repository-minus-unsafe-symlinks,
   why clean-tree, why env-only cgroup, why job status); FEATURES matrix row;
   ARCHITECTURE gate flow; README gate section; CHANGES; KNOWN_ISSUES
   **CIU-28/CIU-29 → FIXED**; roadmap Package D + final qualification →
   complete.
6. **Doc contract**: `test_ciu_documentation_contract.py` — one anchor fix
   (README link slug `…-assay-backed-s18`), then 3 passed.

## Lane validation (the part the implementer can run)

The lane argv's interpreter (`/opt/tester-venv/bin/python`) exists only in
tester-unified, so the lane LOGIC was validated in a throwaway worktree
(`git worktree add /tmp/probe-ciu b68e8a4d`) with the argv's interpreter
pointed at this devcontainer's venv (absolute path; everything else identical),
then the worktree was removed.

- **Green run**: `assay run ciu` → **PASS (exit 0)**; snapshot materialised
  (repository-minus-unsafe-symlinks), full suite at 100% line+branch inside
  the snapshot, R0 + R1 both PASS, verdict emitted. (Coverage payload 0/0:
  the P07 commit has no src delta vs origin/main — a legitimate P05 vacuous
  pass; the whole-source floor ran regardless.)
- **Canary 1 (bad test)**: committed `tests/tests/test_canary_tmp.py` with
  `assert False` → **FAIL/COMMAND_FAILED (exit 1)** — the gate rejects a
  failing test.
- **Canary 2 (pragma'd changed line)**: committed a `# pragma: no cover`
  executable line in `src/ciu/deploy_pkg/profiles.py` → **R0 PASS, R1 FAIL
  `EXCLUDED_LINES`** — the changed-line floor catches a changed line that
  whole-coverage cannot see (`allow_excluded=false`), the precise role of the
  retired `nyxloom.coverage_gate` ("no pragma on changed code"). Both canaries
  reverted by removing the throwaway worktree; branch restored to the real
  argv.

## Adversarial review (fresh combined-axis, not named in P04-P06)

- **Pragma'd-changed-line × whole-coverage blindness × diff judgment** — the
  canary above; accepted, and it is the reason the gate keeps R1
  (`allow_excluded=false`) rather than relying on `--cov-fail-under=100` alone.
- **Gate-cleanliness × retained evidence** — a gate that wrote its verdict
  inside the judged tree would red every subsequent run; the verdict goes to
  gitignored `.assay/`, coverage artifacts are repo-gitignored (`coverage.json`,
  `.coverage*`), verified.
- **Cgroup env × systemd transient-slice auto-creation** — `${CGP:?}` fails
  closed on absence; `LoadState=loaded` check fails closed on unloaded/missing.
  A typo'd name that systemd auto-creates as a transient unit passes the
  LoadState check (best-effort, same mechanism as `cmru tester-gate`); the
  authoritative source is the controller's trusted environment
  (devcontainer.json), not operator input to CIU — accepted, documented in
  S18.3/DESIGN-GUIDE.
- **Empty-subject × green verdict** — a docs-only commit yields a vacuous 0/0
  R1 pass but the whole-source floor (the lane command) still runs the full
  suite at 100%; observed on this branch's own commit. Accepted (P05 contract).
- **Snapshot selection × monorepo drift** — the omissions list is the complete,
  current unsafe-symlink set (verified); a new unsafe symlink anywhere reds
  the lane fail-closed (documented maintenance obligation, CONSUMERS).
- **argv integrity** — `allow_argv_append=false`; the gate invocation passes
  no appended argv.

## Iteration signal (venv run)

```
env -u REPO_ROOT -u PHYSICAL_REPO_ROOT -u CIU_GOV_READ_IOPS \
  .venv/bin/python run-ciu-tests.py
2173 passed / 0 failed, 100% line+branch (7159 stmts / 2808 br), exit 0
```
Recorded as "venv run", never "the gate". The Assay-backed tester-unified gate
runs at checkpoint review (operator), where the real interpreter + container
env exist.

## Deviations / scope extensions (all controller-authorized or gate-setup)

1. Vendored `tools/assay/` (the controller-approved unblock path).
2. `.gitignore` changes (`!tools/assay/*.pyz`, `_last-summary.txt`) — required
   for the gate to run at all (S18.1 pin trackable; S18.4 clean tree).
3. `env_passthrough` of the five suite-required vars — measured necessity.
4. `asserts` list gained `assay-verdict`.
5. The BLOCKED LOG (3271681f) is retained as the trigger record; it names the
   three unblock options, of which the controller chose vendoring.

## Controller review fix (checkpoint review, 2026-08-20) — two argv defects, both measured

The committed gate argv had never executed end-to-end (the lane was rightly
validated with a substitute interpreter; the argv itself was not). Two defects,
both invisible to that validation and both found by cheap live probes at review:

1. **Missing `-e CGROUP_PARENT_DEV_BACKGROUND`**: the tester-unified image does
   not bake the var (`docker inspect`: absent) and the argv passed no `-e`, so
   inside the container `env_passthrough` had nothing to pass through — the four
   governance tests (S15.2) red by construction. The implementer's passthrough
   fix worked in validation only because the DEVCONTAINER shell has the var
   ambient — the exact environment-specific-claim class. Fixed: `-e
   CGROUP_PARENT_DEV_BACKGROUND="$CGP"`.
2. **Unconditional LoadState pre-check**: the devcontainer (and any
   containerized gate context) has no reachable systemd — worse, its
   `/usr/local/bin/systemctl` is a SHIM that exits 0 and prints an advisory to
   STDOUT, so `[ "$(systemctl show …)" = loaded ]` can never pass there. The
   check is now guarded by `[ -d /run/systemd/system ]` (canonical systemd
   reachability probe): enforced on real hosts, skipped where it cannot work
   (trusted source there = devcontainer.json, per this LOG's own S18.3 note).

Standing lesson re-confirmed (see checkpoint-B): an argv validated with any
substituted component proves construction, not acceptance — every NEW gate argv
gets its live probes at review.

**Defect 3 (found by the first live gate run, 2026-08-20):** `sha256sum -c
tools/assay/assay-2.1.0.pyz.sha256` resolves the pin's bare filename against
the CWD (`ciu/`), not the pin's directory → `No such file or directory`,
exit 1. The cmru precedent (`cmru.toml:50`) does `cd tools/assay && sha256sum
-c assay-2.1.0.pyz.sha256`; the vendoring was copied, the invocation shape was
not. Fixed to the subshell-cd form. Three argv defects total — all three from
the same root cause (argv never executed end-to-end pre-review).
