# P23 — `exec-nyxloom init <project_folder>` — Implementation Report

**Status:** done · **Date:** 2026-07-16

## Summary

Adds a `nyxloom init <project_folder>` CLI subcommand that scaffolds a
`nyxloom-trove/` into a target project from this package's own bundled
templates (self-hosted dogfood: the canonical `nyxloom/nyxloom-trove/`
in this repo IS the template source). `exec-nyxloom.py` needed no routing
changes — it already forwards any subcommand generically (docker
exec-into-controller, host fallback otherwise); only its usage docstring
was extended to document the new path.

## Per-oracle results

| Oracle | Result | Test |
| --- | --- | --- |
| O1 (scaffold + valid nyxloom.toml) | PASS | `test_init_scaffolds_trove` |
| O1-negative (existing trove refused, no overwrite) | PASS | `test_init_refuses_existing_trove` |
| O2 (exec-nyxloom.py routes `init`) | PASS (pre-existing generic routing; doc'd) | n/a — routing already covered by `exec-nyxloom.py`'s existing `main()` |
| O2-negative (no `<project_folder>` arg -> exit 2) | PASS | `test_init_missing_project_folder_exits_2` |

## Files touched

- `src/nyxloom/cli.py` — new `cmd_init`, `_INIT_NYXLOOM_TOML` template
  constant, `init` subparser (required positional `project_folder`), and
  `main()` dispatch wiring. Docstring's subcommand table updated.
- `exec-nyxloom.py` — usage docstring only: documents `init <project_folder>`
  and notes it rides the existing generic argv-forwarding path (no code
  change needed — routing was already argument-agnostic).
- `tests/test_cli.py` — three new tests covering O1, O1-negative, O2-negative.

## What `init` writes

`<project_folder>/nyxloom-trove/{STANDARD.md, AUTHORING.md}` copied
verbatim from this repo's own `nyxloom-trove/` (located via
`Path(__file__).resolve().parent.parent.parent / "nyxloom-trove"` —
`src/nyxloom/cli.py` -> `src/` -> the package repo root); a fresh
`nyxloom.toml` with `[project] id = basename(<project_folder>)` and the
standard folder paths (gates/`[refs]` left as commented guidance for the
operator to fill, per STANDARD.md: "leaves `[refs]` for the operator to
fill"); `handoffs/`, `reports/`, `archive/.gitkeep`, `agent-logs/.gitkeep`,
`decisions.md`, `roadmap.md`, `backlog.md`, and a `.gitignore` containing
`agent-logs/`. Refuses (exit 1, stderr `error: ...`) and writes nothing if
`<project_folder>/nyxloom-trove/` already exists.

## Gate output (tail, verbatim)

```
........................................................................ [ 16%]
........................................................................ [ 32%]
........................................................................ [ 49%]
........................................................................ [ 65%]
........................................................................ [ 81%]
........................................................................ [ 98%]
........                                                                 [100%]
```
Exit code 0. 440 tests collected, all passing (3 new + 437 pre-existing).

## Deviations / assumptions

- The handoff's O2 wording ("exec-nyxloom.py grows an `init <project_folder>`
  path that forwards to `nyxloom init` ... reusing the existing routing")
  is satisfied without new logic in `exec-nyxloom.py`: its `main()` already
  forwards `argv[1:]` generically to either `docker exec <container> nyxloom
  ...` or the host-side `python -m nyxloom.cli ...`, so `init <dir>` was
  already routed correctly the moment `nyxloom.cli` gained the subcommand.
  Only the usage docstring was extended — no test exists for
  `exec-nyxloom.py` itself (none existed before this package either; the
  touch scope for tests was `tests/test_cli.py` only, which exercises
  `nyxloom.cli.main` directly).
- `nyxloom.toml`'s generated template omits `[gates.*]` and leaves `[refs]`
  empty (commented guidance instead), matching STANDARD.md's stated intent
  that `init` "leaves `[refs]` for the operator to fill" — a real gate is
  project-specific and can't be guessed. The template still includes
  `handoff_globs` etc. so `ProjectConfig.load()` succeeds unmodified on a
  freshly-scaffolded trove (verified manually, not asserted in a test since
  it wasn't a named oracle).
- Frozen files (`daemon.py`, `reconcile.py`, `storage.py`, `config.py`)
  untouched; `pyproject.toml` untouched (not in the touch scope).

## Suggestions for reviewer (do not act on)

- Consider whether `init` should also register the new project in the
  daemon's registry (`nyxloom project add`) — out of scope here per the
  handoff (B3 backlog item only mentions scaffolding + access-check).
