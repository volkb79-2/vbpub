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
