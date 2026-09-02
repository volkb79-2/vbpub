# nyxloom-P48-assay-gate -- LOG

Chronological record. Implementer: fresh Sonnet session, tier implement-1.
Worktree: `/workspaces/vbpub/.worktrees/nyxloom-P48-assay-gate` (branch
`feat/nyxloom-P48-assay-gate`).

## 2026-09-02T02:3x -- Handoff read + tip check

- Read `nyxloom-trove/handoffs/nyxloom-P48-assay-gate.md` in full (frontmatter
  + body, W1-W5, oracles, deliberately-deferred, scope, evidence discipline,
  BLOCKED rule).
- `git log --oneline -5`: tip is `f1bbead1` (carve commit), parent `a74bc6f6`
  matches the handoff's `input_revision`. No drift -- nothing to note.
- `git status --short` at start: `?? tools/assay/` only (the carver's
  prepared, sha256-verified vendor drop, deliberately left uncommitted per
  the carve commit message: "the handoff's W1 step un-ignores them so the
  implementer's own commit picks them up alongside the wiring").

## Context read (exact order per handoff)

1. `nyxloom-trove/backlog/NL-1-...md` -- backlog entry NL-1, whole file.
2. `../ciu/assay.toml` -- template for W2. Verbatim `[lanes.ciu]` shape,
   `schema_version = 2`, `rigor = ["R0","R1"]`, judge block, isolation block
   with 3 unsafe-symlink omissions.
3. `../ciu/run-gate.toml` -- template for W3. `kind = "assay"`,
   `assay_lane`, `assay_command` pointing at the pinned pyz, `[lanes.ciu.pins.assay]`
   with `version` + `sha256` (sidecar path). No `clean_tree` key present
   (schema default `true` applies).
4. `../ciu/nyxloom-trove/nyxloom.toml` `[gates.tester-unified]` (actually
   `[gates.tester-unified]` in ciu's copy too) -- read the `asserts` line
   shape and the SSOT-pointer comment block sitting directly above `argv`.
5. `../ciu/.gitignore` tail -- the `!tools/assay/*.pyz` exception comment
   shape, plus confirmed empirically that this pattern DOES successfully
   un-ignore `ciu/tools/assay/*.pyz` despite the monorepo root's
   `*.py[codz]` rule (`git ls-files ciu/tools/assay/` shows both files
   tracked).
6. `nyxloom/run-gate.toml` and `nyxloom/nyxloom-trove/nyxloom.toml` (current
   state) -- confirmed the exact strings W3/W4 target exist verbatim.
7. `nyxloom/src/nyxloom/coverage_gate.py` docstring + `--fail-under` default
   (`type=float, default=100.0`, docstring: "default 100 = every changed
   line must run") -- confirms `fail_under = 100.0` in W2 is not invented.
8. `docs/backlog-entries-spec.md` `carved_handoff` field -- example
   frontmatter shows it positioned after `filed_date`/`spec_owner`/
   `decisions`, before `merge_commit`. NL-1 has none of the intervening
   optional fields, so `carved_handoff` was placed directly after
   `filed_date`.
- Also read `nyxloom/.gitignore` (only line: `controller/.env`) and
  confirmed the actual `*.py[codz]` ignore rule that catches `*.pyz` lives
  in the WORKTREE ROOT `.gitignore` (one level above `nyxloom/`, monorepo
  root), not in `nyxloom/.gitignore` itself -- `git check-ignore -v
  nyxloom/tools/assay/assay-4.0.0.pyz` confirmed the match came from
  `.gitignore:3:*.py[codz]` at the worktree root. This matches ciu's own
  situation exactly (`ciu/.gitignore`'s own `*.py[cod]` line is a
  project-local, unrelated 3-char-class rule; ciu's un-ignore exception
  also lives in the PROJECT .gitignore and works via git's per-directory
  precedence). W1 targets `nyxloom/.gitignore` per scope.touch and this is
  the correct, effective place for the exception (verified: git's negated
  pattern in a deeper .gitignore overrides a shallower directory's ignore
  for the same path, and ciu's identical setup empirically un-ignores and
  tracks its own pyz).

## W1 -- `nyxloom/.gitignore`

Appended the exact block from the handoff body (comment + `!tools/assay/*.pyz`)
after the existing `controller/.env` line.

## W2 -- `nyxloom/assay.toml` (new file)

Created with the handoff's EXACT locked content, byte-for-byte (only the
header prose was already nyxloom-voiced in the handoff itself, so no further
copy-edit was made). Verified with `python3 -c "import tomllib; ..."` that
the file parses as valid TOML.

## W3 -- `nyxloom/run-gate.toml`

Replaced the entire file content with the handoff's EXACT locked content.
Confirmed via `git diff --cached run-gate.toml` that: `kind = "command"` ->
`kind = "assay"`; the old inline pytest+coverage_gate argv is gone; the
`clean_tree = false` key + comment is gone (no `clean_tree` key at all,
matching ciu's own file, which also declares none). Verified TOML parses.

## W4 -- `nyxloom/nyxloom-trove/nyxloom.toml`

In `[gates.tester-unified]`:
- Added `asserts = ["tests-pass", "changed-line-coverage", "canary-verified",
  "assay-verdict"]` between `phase = "implementation"` and
  `timeout_seconds = 1800`, matching ciu's line order.
- Replaced ONLY the trailing 4-line comment block ("Judgment is unchanged
  ... retiring coverage_gate self-judgment") with the landed-state SSOT
  pointer comment the handoff specifies verbatim. Left the two comment
  blocks above it (the 2026-08-22 run-gate-P01 note and the Cgroup/A3 note)
  untouched, matching the handoff's "replace the four-line comment block"
  (not the whole comment stack) and ciu's own file shape (SSOT pointer
  comment sits directly above `argv`, other historical comments stay above
  that).
- `argv`, `phase`, `timeout_seconds`, `environment` values themselves left
  byte-identical.

## W5 -- `nyxloom-trove/backlog/NL-1-...md` + backlog CLI

- Added `carved_handoff: nyxloom-P48-assay-gate` to NL-1's frontmatter,
  directly after `filed_date`.
- `nyxloom backlog set-status NL-1 carved` (run from `nyxloom/`): succeeded,
  printed the entry path, `status: open` -> `status: carved` confirmed by
  re-reading the file.
- `nyxloom backlog index`: succeeded, regenerated `INDEX.md` (NL-1 row now
  shows `carved` status; row order changed as a side effect of the
  generator, which is expected/mechanical, not a hand-edit).
- Sanity-checked with `nyxloom backlog show NL-1` (renders cleanly) and
  `nyxloom lint` (full project scan, no path) -- zero errors reference NL-1,
  INDEX.md, or any file this package touches; all reported errors are
  pre-existing, unrelated handoff-schema issues in other projects' handoffs
  (topos-P1xx, ciu-P1x-P3x, dstdns-P161, nyxloom-P90), none of which this
  package's scope.touch includes. `nyxloom lint <path>` on the backlog entry
  itself errors because that subcommand validates HANDOFF frontmatter only
  (confirmed via `nyxloom lint --help`: "Handoff file paths"), not
  backlog-entry frontmatter -- expected, not a defect in this package.

## Vendor integrity check (O1 positive half)

```
$ cd tools/assay && sha256sum -c assay-4.0.0.pyz.sha256
assay-4.0.0.pyz: OK
$ python3 tools/assay/assay-4.0.0.pyz --version
assay 4.0.0
```

## Staging + commit (W1-W5)

`git add -A -- .gitignore assay.toml run-gate.toml nyxloom-trove/nyxloom.toml
nyxloom-trove/backlog/ tools/assay/` -- one deviation from the literal
Environment-Setup recipe string: `tools/assay/` was added explicitly to the
pathspec list (the recipe's example list omitted it), because the carve
commit message states outright that "the implementer's own commit picks
them up alongside the wiring" and a clean tree (required before the live
gate run, O2) is impossible while `tools/assay/` remains untracked. This
does not touch/edit the forbidden `tools/assay/` CONTENT (still
sha256-identical to the carver's drop) -- it only stages the carver's
already-correct, already-verified files so they become part of the tracked
tree, which the forbid note's own rationale ("it is already correct;
touching it is out of scope") anticipates as necessary.

(Continued below after the live gate run.)

## Commit (W1-W5)

`git commit` on `feat/nyxloom-P48-assay-gate` -> `345944cb623608b85f7f53429af02b33f31769c0`.
`git status --short` confirmed empty (clean tree) immediately before the live
gate run.

## Live gate run (O2)

Command (from `nyxloom/`):
```
./run-gate.py --worktree /workspaces/vbpub/.worktrees/nyxloom-P48-assay-gate tester-unified
```
Run in the background (`run_in_background`) since the budget is 30m and the
suite's measured wall time is several minutes; polled/waited on rather than
guessed at. Confirmed genuinely progressing mid-run via `docker ps` (container
`run-gate-vbpub-tester-unified-1084383-1788316954`, "Up") and `docker exec ...
ps aux` (real `assay-4.0.0.pyz run tester-unified` process plus 7 live pytest
-n auto xdist workers accumulating CPU time, not hung).

**Finished: exit code 1 (from the run's own log, captured separately from
`echo $?` at the point the backgrounded command exited: `EXIT_CODE:1`).**

Full run-gate stdout/stderr (verbatim):
```
run-gate: admission: lane 'tester-unified' declares no resources.memory — not memory-accounted (shared-infra rules still apply)
run-gate: rev 32 | lane tester-unified | env [environments.tester-unified] in central /workspaces/vbpub/.worktrees/nyxloom-P48-assay-gate/run-gate.toml | slice dev-background.slice ($CGROUP_PARENT_DEV_BACKGROUND)
run-gate: ephemeral env (nothing declared)
run-gate: budget 30m (advisory)
run-gate: docker argv: [... omitted, see LOG-internal capture; the sha256 pin verify + version-probe wrapper ...]
assay-4.0.0.pyz: OK
tester-unified: FAIL/COMMAND_FAILED (exit 1)
  commit: 345944cb623608b85f7f53429af02b33f31769c0
  argv: /opt/tester-venv/bin/python -m pytest tests -n auto -q --cov=src/nyxloom --cov-report=json:coverage.json
run-gate: lane 'tester-unified' failed with exit 1; full container logs preserved at /tmp/run-gate/run-gate-vbpub-tester-unified-1084383-1788316954.log
run-gate: verdict artifact: /workspaces/vbpub/.worktrees/nyxloom-P48-assay-gate/nyxloom/.assay/verdict-tester-unified.json
run-gate: lane 'tester-unified' exit 1
```

Verdict JSON read as a SEPARATE step (`cat .assay/verdict-tester-unified.json`,
after the run exited and after reading the run's own log): `outcome: "FAIL"`,
`reason_code: "COMMAND_FAILED"`, `exit_code: 1`, `commit:
345944cb623608b85f7f53429af02b33f31769c0`. `claims`: R0 = FAIL/COMMAND_FAILED;
R1 = PASS, coverage pct 100.0 (expected and correct: this package's diff adds
zero executable Python lines, so R1's changed-line floor is trivially met --
consistent with assay.toml's own documented "an empty delta is a clean pass"
behavior; R1 is NOT the failure here).

`result_stdout_tail` (captured in the verdict JSON) names three pytest
failures:
1. `tests/test_lint.py::TestL10Size::test_large_handoff_warning` -- `assert False`
2. `tests/test_lint.py::TestL10Size::test_huge_handoff_error` -- `assert False`
3. `tests/test_lint.py::TestConfigLintSchema::test_repos_own_config_no_findings` --
   ```
   assert [LintFinding(...)] == []
   Left contains one more item: LintFinding(rule='CFG1', severity='error',
     message="gates.tester-unified.asserts.3: 'assay-verdict' is not one of
     ['te...", ...)
   ```

Failures 1-2 touch nothing this package's diff changed (no edit to
`src/nyxloom/lint.py`, `src/nyxloom/config.py`, or `tests/test_lint.py`'s L10
logic) and appear pre-existing/independent of this change (not investigated
further live, since scope forbids touching `tests/`/`src/` regardless of
cause, and the third failure alone is already a definitive, self-contained
block).

**Failure 3 is directly and mechanically caused by this package's own W4
change and is the BLOCKING finding.** Root-caused:

```
$ git diff a74bc6f6 -- src/nyxloom/schemas/nyxloom-config.schema.json | wc -l
0
$ git ls-files src/nyxloom/schemas/nyxloom-config.schema.json
src/nyxloom/schemas/nyxloom-config.schema.json
```

`src/nyxloom/schemas/nyxloom-config.schema.json`'s `asserts` items enum
(lines ~125-131, untouched, byte-identical to `a74bc6f6`) is a CLOSED
vocabulary of exactly four values:
```json
"asserts": {
  "type": "array",
  "items": {
    "enum": ["tests-pass", "changed-line-coverage", "mutation", "canary-verified"]
  }
}
```
It does not include `"assay-verdict"`. The handoff's W4 step LOCKS the value
`asserts = ["tests-pass", "changed-line-coverage", "canary-verified",
"assay-verdict"]` (mirroring `ciu/nyxloom-trove/nyxloom.toml`'s own use of
`"assay-verdict"` verbatim). Writing that exact locked line into nyxloom's
OWN `nyxloom-trove/nyxloom.toml` [gates.tester-unified] makes nyxloom's own
dogfood self-lint test (`tests/test_lint.py::TestConfigLintSchema::
test_repos_own_config_no_findings`, which lints nyxloom's own
`nyxloom-trove/nyxloom.toml` and hard-asserts zero findings) fail CFG1,
because the schema enum that same repo's `nyxloom lint` reads has not been
taught the new value.

This gap does not surface for `ciu`'s own adoption of the identical
`asserts` line because ciu carries no equivalent "my own config must
self-lint clean" unit test in ITS suite -- this is a dogfood-specific trap
unique to nyxloom linting itself.

## Why this is BLOCKED, not worked around

Three theoretically possible fixes, all foreclosed by scope:
1. Add `"assay-verdict"` to the schema enum in
   `src/nyxloom/schemas/nyxloom-config.schema.json` -- requires touching
   `src/`, which is in `scope.forbid`.
2. Adjust/skip the failing assertion in `tests/test_lint.py` -- requires
   touching `tests/`, which is in `scope.forbid`.
3. Drop `"assay-verdict"` from the `asserts` line in
   `nyxloom-trove/nyxloom.toml` (a file that IS in `scope.touch`) -- this
   file IS editable, but the VALUE is explicitly locked by the handoff
   ("every value below is locked... there is no design decision left for
   you to make. If you find one anyway, that is a carve defect: STOP and
   write BLOCKED, do not invent the missing value"). Silently dropping a
   locked assert value to make a test pass is exactly the kind of
   unauthorized product call the BLOCKED rule reserves for the controller
   ("Product calls are the controller's (D-numbered), never yours").

This matches the handoff's own `escalate_if` triggers verbatim: "the new
assay-judged gate cannot reach a real green on this worktree's clean HEAD
for a reason your diff cannot fix" AND "a needed change falls outside
scope.touch, or requires touching a forbidden file."

## O1/O3 negative-half scratch checks (done regardless of the O2 block, since
   independent of it)

O1 negative (byte-flip a SCRATCH COPY outside the tracked tree, never
committed):
```
$ cp tools/assay/assay-4.0.0.pyz tools/assay/assay-4.0.0.pyz.sha256 <scratch>/
$ python3 -c "... flip byte 100 ..."
$ sha256sum -c assay-4.0.0.pyz.sha256   # against the corrupted scratch copy
assay-4.0.0.pyz: FAILED
sha256sum: WARNING: 1 computed checksum did NOT match
```
(non-zero exit; script under `set -e` halted there, consistent with "fails
before any test executes, no test output").

O3 negative (in-place uncommitted edit of the real `assay.toml`, reverted
immediately with `git checkout -- assay.toml`, confirmed by `git status
--short assay.toml` / `git diff --stat assay.toml` both empty afterward):
```
$ python3 -c "... fail_under = 100.0 -> fail_under = \"100\" ..."
$ python3 tools/assay/assay-4.0.0.pyz lanes
assay: ERROR/BAD_LANE_CONFIG: .../assay.toml: lane 'tester-unified':
  'judge.fail_under' must be a number, got str
EXIT:2
$ git checkout -- assay.toml   # revert
$ git status --short   # clean, confirmed
```
(First attempt used a separate `/tmp` scratch copy via `--file`, but that
copy has no sibling `src/` directory, so it errored on `source_roots`
resolution before ever reaching `fail_under` -- a confound, not a defect;
the in-place-then-revert form isolates the intended `fail_under` type
check cleanly and is explicitly permitted by the handoff's Oracles section
("a SCRATCH COPY ... or an immediately-reverted uncommitted edit").)

## Conclusion: BLOCKED

`BLOCKED: nyxloom-P48-assay-gate's locked W4 asserts value "assay-verdict"
is rejected by nyxloom's own (untouched, forbidden-scope) asserts schema
enum in src/nyxloom/schemas/nyxloom-config.schema.json, failing nyxloom's
own dogfood self-lint test (tests/test_lint.py::TestConfigLintSchema::
test_repos_own_config_no_findings) and blocking the live gate at
COMMAND_FAILED/exit 1 -- fixing it requires touching forbidden src/ or
tests/, or un-locking a pinned handoff value, neither of which is mine to
do.`

Branch left as-is (committed W1-W5 at `345944cb`), plus this LOG and the
REPORT committed in a follow-up commit. Not merged (controller's step, per
the handoff and per doctrine).
