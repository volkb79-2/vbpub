---
schema_version: 1
id: nyxloom-P35-lint-project-resolution
project: nyxloom
title: "Lint path resolution: owning project, own repo, and trove handoff paths"
tier: sonnet5-high
input_revision: "a7499cc"
depends_on: [nyxloom-P29-intake-agent-backend]
session: fresh
source: {kind: backlog, ref: nyxloom-trove/4-backlog.md}
scope:
  touch:
    - "src/nyxloom/lint.py"
    - "src/nyxloom/cli.py"
    - "tests/test_lint.py"
  forbid:
    - "src/nyxloom/daemon.py"
    - "src/nyxloom/reconcile.py"
    - "src/nyxloom/config.py"
oracles:
  - id: O1
    observable: "A new resolver in lint.py maps a handoff path to its OWNING project's config by walking the path's ancestors for the nearest `nyxloom-trove/nyxloom.toml` (falling back to the registered project root that contains the path). A test builds two fake project roots, each with its own nyxloom.toml and a handoff, and asserts the resolver returns each file's own project id — including when the other project is first in the registry dict."
    negative: "cli.py:210 keeps doing `root = next(iter(registry.values()))`, so the config used depends on registry iteration order, not on the file being linted."
    gate: tester-unified
  - id: O2
    observable: "`cmd_lint` with path arguments lints each path against its OWN resolved project config. A test lints a nyxloom handoff while a DIFFERENT project sits first in the registry and asserts zero L1 project-mismatch findings, zero L2 undeclared-gate findings, and zero L7 path-does-not-exist findings — the three families the wrong root manufactures."
    negative: "the live bug: linting a known-good nyxloom handoff reports `L1 project 'nyxloom' does not match config 'dstdns'`, `L2 gate id 'tester-unified' not declared`, and a wall of `L7 path does not exist` (paths resolved from the wrong root), making CLI lint unusable as a pre-flight signal."
    gate: tester-unified
  - id: O3
    observable: "A path that resolves to no registered project produces a typed, actionable diagnostic naming the path (and a non-zero exit), rather than being silently linted against an arbitrary config or crashing with a traceback. A test asserts the diagnostic mentions the path and that no findings are attributed to an unrelated project."
    negative: "an unresolvable path is silently linted against whichever project happens to be first (wrong findings presented as authoritative), or raises an unhandled exception."
    gate: tester-unified
  - id: O4
    observable: "L7's body cross-repo check no longer hardcodes one project name: the `/workspaces/(?!dstdns)[a-z0-9_-]+` pattern at lint.py:523 is driven by the project being linted, so a nyxloom handoff citing its own `/workspaces/vbpub` gate command is NOT flagged, while a genuine reference to another repo still warns. A test asserts no cross-repo warning for a handoff citing its own repo path and that a foreign path still warns."
    negative: "every nyxloom handoff carries a permanent spurious `cross-repo reference '/workspaces/vbpub' may not resolve` warning because the rule exempts exactly one hardcoded project (dstdns), which is noise that trains operators to ignore L7."
    gate: tester-unified
  - id: O5
    observable: "L1's depends_on resolution finds handoffs at the project's CONFIGURED location instead of the hardcoded legacy `handoff/<dep_id>.md` (lint.py:296), and also resolves a dep whose handoff has been archived on merge. A test asserts a handoff whose dep exists only under `nyxloom-trove/handoffs/` produces no L1 depends_on finding when no statefile exists, and that a dep resolvable nowhere still errors."
    negative: "the hardcoded `cfg.root / 'handoff/<dep>.md'` never resolves for a trove-standard project (nyxloom has no `handoff/` directory), so the check silently degrades to statefile-existence-only: in any checkout without the daemon's state volume EVERY chained handoff reports a bogus `depends_on task ref does not resolve` error. Verified on a7499cc — P29, P30, P31 and P32 all report it."
    gate: tester-unified
gates: [tester-unified]
escalate_if:
  - "resolving the owning project from a path cannot be done without editing config.py or daemon.py (both forbidden)"
  - "making L7's cross-repo check project-driven requires a new nyxloom.toml field (then file a D-decision; do not invent config keys)"
  - "resolving an archived dep handoff requires a new nyxloom.toml field beyond the existing archive_dir"
---

# P35 — Lint path resolution

`nyxloom lint <path>` is **unusable today**: it lints every path against
whichever project happens to be first in the registry, regardless of which
project the file belongs to or where the cwd is. Backlog **B15**, discovered
while adopting netcup-api-filter.

The daemon's own per-project `lint_project` path is fine — which is exactly why
this went unnoticed: the automated route never hits the broken code.

This package fixes **three** resolution bugs in `lint.py`, all the same mistake
in different clothes — a path is resolved against something other than the
project that actually owns it. B15 (the wrong root, O1-O3) is the reason lint is
unusable; O4 and O5 are two hardcoded assumptions found while carving it, both in
the same file, so they land together rather than in a conflicting follow-up.

## The bug, exactly

`src/nyxloom/cli.py:207-215`:

```python
if args.path:
    for path_str in args.path:
        path = Path(path_str)
        # Use the first available project config (they're all the same for lint purposes)
        if registry:
            root = next(iter(registry.values()))
            cfg = config.ProjectConfig.load(root)
            findings = lint.lint_file(path, cfg)
```

The comment asserts *"they're all the same for lint purposes"*. That is false
and is the root cause: `cfg` supplies `project_id` (L1 compares it to the
handoff's `project`), `gates` (L2 checks every oracle's gate id against it), and
`root` (L7 resolves every scope path under it). A config from the wrong project
therefore manufactures three families of bogus findings at once.

Live reproduction from B15 — a known-good, merged handoff lints dirty:

```
docker exec -w /workspaces/vbpub/nyxloom nyxloom-prod-nyxloomd python -m \
  nyxloom.cli lint nyxloom-trove/handoffs/nyxloom-P24-config-schema-lint.md
```

reports `L1 project 'nyxloom' does not match config 'dstdns'`, `L2 gate id
'tester-unified' not declared`, and a wall of `L7 path does not exist`. The only
way to read CLI lint output today is to diff it against another handoff's
findings and ignore the shared noise.

## The third bug: L1 depends_on looks in the legacy `handoff/` directory

`src/nyxloom/lint.py:296` resolves a dependency as:

```python
dep_file = cfg.root / f"handoff/{dep_id}.md"
```

The trove migration moved handoffs to `nyxloom-trove/handoffs/`
(`cfg.handoff_globs`), and nyxloom has **no `handoff/` directory at all** — so
this never resolves and the check silently degrades to "does a statefile exist
in the daemon's state volume". In any checkout without that volume (a carve
worktree, a fresh clone, CI), every chained handoff reports a bogus error.
Verified on `a7499cc`: P29, P30, P31 and P32 each report
`depends_on task ref ... does not resolve`.

A dep whose handoff was **archived on merge** (moved to `cfg.archive_dir`) is the
second half of the same gap — P31's dep on the merged P28 cannot resolve from
`handoff/` or `nyxloom-trove/handoffs/`. Resolve against the configured handoff
location and the archive, keeping the statefile fallback.

## Context to read first (read ONLY these, in order)

- `src/nyxloom/cli.py` **199-240** — `cmd_lint`. The `args.path` branch is the
  defect; the no-args branch (per-project `lint_project`) is correct and is your
  model for how a config is meant to be paired with its files. Keep the printing
  and exit-code behaviour.
- `src/nyxloom/lint.py`
  - **289-306** — L1's depends_on resolution: the hardcoded `handoff/` path (O5)
    and the `state_dir` fallback that masks it.
  - **508-563** — `_check_l7` and `_check_path_resolution`. Note `cfg.root` is
    the resolution base (why the wrong root yields the L7 wall), and note the
    hardcoded `other_repo_pattern = r"/workspaces/(?!dstdns)[a-z0-9_-]+"` at
    **523** — the O4 half of this package.
  - the `lint_file(path, cfg)` and `lint_project(cfg)` entry points — put the
    new resolver next to them so both CLI and any later caller share it.
- `src/nyxloom/config.py` (**READ only — forbidden to edit**) — `load_registry()`
  (project id -> root) and `ProjectConfig.load(root)`. The trove convention is
  `<root>/nyxloom-trove/nyxloom.toml`; a resolver can walk a path's ancestors
  for that file, and/or match against registered roots. Prefer the registry when
  both agree; the ancestor walk is what makes an unregistered checkout work.
- `tests/test_lint.py` — mirror an existing lint test's fixture style. You need
  TWO project roots in one test to prove the bug is gone; a single-project
  fixture cannot distinguish correct resolution from the current accident.

## Work

1. Add a resolver to `lint.py` mapping a file path to its owning project's
   `ProjectConfig` — walk ancestors for the nearest `nyxloom-trove/nyxloom.toml`,
   and/or match the path against registered project roots (deepest match wins, so
   a nested checkout resolves to the innermost project).
2. Rewrite `cmd_lint`'s `args.path` branch to resolve each path with it, then
   `lint_file(path, cfg_for_that_path)`. Delete the false comment.
3. Unresolvable path: emit a typed diagnostic naming the path and exit non-zero.
   Never fall back to an arbitrary config.
4. Make L7's cross-repo body check project-driven instead of hardcoding
   `dstdns`: derive the "own repo" segment from the project being linted, so a
   handoff citing its own repo path is clean and a foreign repo still warns.
5. Fix L1's depends_on resolution (lint.py:296) to use the project's configured
   handoff location plus `cfg.archive_dir`, keeping the statefile fallback.
6. Tests in `tests/test_lint.py` for O1-O5. The O2 test must place a different
   project FIRST in the registry mapping — that ordering is what the current
   code accidentally depends on, so without it the test passes against the bug.
   The O5 test must run with NO statefile present, since an existing statefile is
   exactly what masks the bug today.

## Scope / forbid

Touch ONLY `lint.py`, `cli.py`, `tests/test_lint.py`. `config.py` is forbidden
(read it, do not change it) — this is a resolution bug in the caller, not a
config-loading gap. `daemon.py` and `reconcile.py` are out of scope: the
per-project daemon lint path is already correct and must not change behaviour.

## BLOCKED rule

If a named contract cannot be met as specified, or scope requires a forbidden
file (see `escalate_if`), STOP — write `BLOCKED: <reason>` to the LOG, commit,
and exit. Do NOT improvise a workaround.

## Worktree / branch

The daemon runs this on a dedicated `implement` branch in its own git worktree
under `.worktrees/` (branch `feat/nyxloom-P35-lint-project-resolution` from
`main`); commit all work on that branch. Do not touch the main checkout.

## Gate

`tester-unified` (the project's real gate — never the cockpit):

```
docker run --rm -v /home/vb/volkb79-2/vbpub:/workspaces/vbpub tester-unified:local \
  bash -c 'cd {worktree}/nyxloom && PYTHONPATH=src /opt/tester-venv/bin/python -m pytest tests -q'
```
