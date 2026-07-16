# P35-REVIEW — independent frontier review (merge gate)

Reviewer: Opus 4.8, fresh session. Single-task packet.
Date: 2026-07-16. Commit reviewed: `94c2660`.

## Verdict

**APPROVED after review-fix.**

The implementation is correct and lands all five oracles. I reproduced the
live B15 bug on `main` and confirmed it is gone on the branch — the exact
command the handoff cites as the repro now prints `clean`.

One real defect sat underneath the oracles: **the O3 test was hollow — it
passed against the unfixed code.** I proved it, fixed it on the branch, and
re-verified. It was a test defect, not a design defect: the O3 *implementation*
(`resolve_project_for_path` returning `None` + `unresolved_project_finding`)
was right all along, which is why this is a fix-and-approve rather than a
rejection.

## Git state (verified, not taken from the receipt)

- `git log main..feat/nyxloom-P35-lint-project-resolution` → one commit,
  `94c2660`.
- `git status` in the branch worktree → clean; no uncommitted work to rescue.
- Files touched (`git diff --name-only main...HEAD`), exactly the three in
  scope:
  - `nyxloom/src/nyxloom/cli.py`
  - `nyxloom/src/nyxloom/lint.py`
  - `nyxloom/tests/test_lint.py`
- Forbidden files (`daemon.py`, `reconcile.py`, `config.py`): **untouched**,
  confirmed by name-only diff. `config.py` was read-only-consulted as the
  handoff intended; `archive_dir` was reached via a local raw-TOML read
  (`_archive_dir`, mirroring `render.py`'s `_trove_project_extras`) rather than
  by adding a field to `ProjectConfig` — the correct call under the freeze, and
  it avoids the `escalate_if` trigger.

## Gate — re-run by me, not trusted from the report

```
docker run --rm -v /home/vb/volkb79-2/vbpub:/workspaces/vbpub tester-unified:local \
  bash -c 'cd <worktree>/nyxloom && PYTHONPATH=src /opt/tester-venv/bin/python -m pytest tests -q'
```

`515 passed in 71.75s`, including my added assertions. Green at `94c2660` and
green after my fix.

## The end-to-end check that matters

The handoff cites a specific live reproduction. I ran that exact lint on both
sides.

**On `main` (old code)** — a known-good, merged handoff lints dirty with all
four bogus families at once:

```
nyxloom-P24-config-schema-lint.md:- L1 error project 'nyxloom' does not match config 'dstdns'
nyxloom-P24-config-schema-lint.md:- L2 error gate id 'tester-unified' not declared in project.toml
nyxloom-P24-config-schema-lint.md:- L7 error path 'src/nyxloom/config.py' does not exist
nyxloom-P24-config-schema-lint.md:- L7 error path 'src/nyxloom/cli.py' does not exist
   ... (L7 wall) ...
nyxloom-P24-config-schema-lint.md:- L7 warning cross-repo reference '/workspaces/vbpub' may not resolve
```

**On the branch** — same file, same command: `clean`, exit 0.

That single output covers B15 (O1-O2), and the last line is the O4 half — the
permanent spurious `/workspaces/vbpub` warning is gone.

## Finding 1 — the O3 test was hollow (FIXED on branch)

**Severity: real, caught by adversarial check, fixed.**

The discriminating question for any regression test is "does it fail against
the bug?" I ran the five new test classes against `main`'s source. Four failed
as they should. One passed:

```
PASSED  TestCmdLintUnresolvedPath::test_orphan_path_gets_diagnostic_not_wrong_project_findings
```

**Why it was hollow.** The test's orphan fixture was
`orphan.write_text("not even a valid handoff\n")` — a file with no frontmatter.
O3's negative is *"an unresolvable path is silently linted against whichever
project happens to be first (wrong findings presented as authoritative)"*. But a
file with no `project:` field **has nothing to mismatch**: on the old code it
was linted against `other`'s config, produced a generic frontmatter/schema
error, and every assertion still held — path in output ✓, non-zero exit ✓,
`"does not match config" not in out` ✓ (vacuously). The test asserted the
absence of symptoms its own fixture could not produce.

**The fix** (`tests/test_lint.py`):
- Split `_write_real_handoff` into a text builder `_handoff_text(...)` + a thin
  writer, so a schema-valid handoff can be written *outside* any project root.
- The orphan is now a schema-valid handoff naming an unregistered project
  (`orphanproj`), placed outside every project root — so the "silently linted
  against an arbitrary config" negative is actually reachable.
- Added assertions on the **typed** diagnostic: `"L0" in out` and
  `"no owning project" in out`. This is the half of O3 that says *typed and
  actionable*, which the original test never checked at all.

**Verified both directions.** Against `main`'s source the fixed test now fails
with precisely the oracle's negative:

```
E  assert 'L0' in "...orphanproj-P01-real.md:- L1 error project 'orphanproj' does not match config 'other'"
```

That is the bug, caught. Against the branch it passes.

*Note on the fixture:* my first attempt used project id `orphan-proj`, which
tripped the id schema (`^[a-z][a-z0-9]*-P[0-9]{2,4}...` — no hyphen in the first
segment) and made the test fail for the wrong reason (parse error, not
wrong-config). Renamed to `orphanproj`. Worth flagging because
`TestResolveProjectForPath::test_unregistered_checkout_resolves_via_ancestor_walk`
still uses the id `orphan-proj`; that is harmless there (it only reads
`cfg.project_id` and never lints), but it is a fixture that could not survive
its own linter.

## Oracle-by-oracle

| Oracle | Verdict | Evidence |
|---|---|---|
| O1 resolver maps path → owning project | **HOLDS** | Two trove roots, `projb` first in the registry dict; each handoff resolves to its own id. Plus an unregistered-checkout test the registry-only fallback could not pass. Both fail on `main`. |
| O2 `cmd_lint` uses each path's own config | **HOLDS** | `other` first in registry, `mine`'s handoff lints clean, exit 0. Fails on `main` with the wrong-config findings. Confirmed end-to-end by the live repro above. |
| O3 unresolvable path → typed diagnostic | **HOLDS after my fix** | Implementation was always correct; the test did not discriminate. See Finding 1. |
| O4 L7 cross-repo is project-driven | **HOLDS** | Own-repo ref clean, foreign repo still warns; fails on `main`. Live repro confirms the `/workspaces/vbpub` warning is gone. |
| O5 depends_on resolves at configured + archive location | **HOLDS** | Trove-located dep and archived dep both resolve with **no statefile** (asserted absent); dep resolvable nowhere still errors. First two fail on `main`. |

On O5's negative control: `test_dep_resolvable_nowhere_still_errors` also passes
against `main`. That is correct and not a hollowness flag — it is the guard
against over-permissive resolution, and it is *supposed* to hold on both sides.

## Implementation notes (reviewed, no change required)

- **`resolve_project_for_path` ordering.** Ancestor-walk first, registry
  deepest-match second. The prose asked to "prefer the registry when both
  agree" — when they agree the result is identical, and O1's parenthetical
  specifies exactly the implemented order. `(search_start, *search_start.parents)`
  walks inward-out, so the nearest config wins and a nested checkout resolves to
  the innermost project, as required. Non-existent paths are handled
  (`is_dir()` false → walk from `.parent`).
- **O4 does not regress dstdns.** The old hardcode exempted `dstdns`; the new
  code derives the segment from `cfg.root`. Registry has
  `dstdns → /workspaces/dstdns`, so `_own_repo_segment` yields `dstdns` and the
  exemption is preserved. The new check is also *stricter* than the old
  `(?!dstdns)` lookahead, which sloppily exempted any repo merely *prefixed*
  `dstdns` (e.g. `/workspaces/dstdns-scratch`).
- **`ProjectConfig.load` and the resolver agree on layouts.** Both check
  `nyxloom-trove/nyxloom.toml` then legacy `.nyxloom/project.toml`, so the
  resolver never hands `load` a root it cannot read.

## Observations for the backlog (not blocking, not fixed here)

1. **`_resolve_dep_handoff` assumes a non-recursive glob.** It derives the dep
   directory with `Path(glob_pattern).parent`. For a recursive
   `handoffs/**/*.md` that yields a literal `**` component and the candidate
   never exists. Every current project uses the simple `dir/*.md` form, and the
   archive + statefile fallbacks remain, so there is no live impact — but the
   assumption is silent. Worth a note if nested handoff dirs ever land.
2. **A malformed `nyxloom.toml` in an ancestor tracebacks the path branch.**
   `resolve_project_for_path` calls `ProjectConfig.load` unguarded, while the
   no-args branch swallows exceptions via `try/except Exception: pass`. This is
   *not* a regression — the old path branch called `load` unguarded too — and
   it is outside O3's scope (which concerns unresolvable paths, not unparseable
   configs). Flagging only because O3's language mentions tracebacks.

## What I changed

`nyxloom/tests/test_lint.py` only:
- `_write_real_handoff` split into `_handoff_text` + writer.
- `TestCmdLintUnresolvedPath` fixture made a schema-valid, unregistered-project
  handoff outside all roots; added typed-diagnostic assertions (`L0`,
  `no owning project`).

No source change was needed. Gate green after the fix (`515 passed`).
