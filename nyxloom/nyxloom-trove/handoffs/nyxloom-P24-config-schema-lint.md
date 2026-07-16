---
schema_version: 1
id: nyxloom-P24-config-schema-lint
project: nyxloom
title: "nyxloom.toml JSON schema + `nyxloom lint` config validation"
tier: sonnet5-high
input_revision: "82593d5"
depends_on: []
session: fresh
source: {kind: backlog, ref: nyxloom-trove/backlog.md}
scope:
  touch:
    - "src/nyxloom/schemas/nyxloom-config.schema.json"
    - "src/nyxloom/lint.py"
    - "tests/test_lint.py"
  forbid:
    - "src/nyxloom/config.py"
    - "src/nyxloom/cli.py"
    - "src/nyxloom/daemon.py"
    - "src/nyxloom/reconcile.py"
    - "src/nyxloom/storage.py"
oracles:
  - id: O1
    observable: "A `nyxloom.toml` that violates the schema (a `[gates.*]` with an empty/missing `argv`, or a `[project]` missing `id`/`handoff_globs`, or a policy value of the wrong type) yields a blocking config finding (severity error, rule id in the new `CFG*` namespace) from `lint.lint_config(cfg)` AND appears under the config path key in `lint.lint_project(cfg)`; a valid `nyxloom.toml` (the repo's own) yields zero config findings"
    negative: "an invalid nyxloom.toml lints clean — the bad argv / missing required key is not caught until dispatch/runtime"
    gate: tester-unified
  - id: O2
    observable: "a `[refs]` entry pointing at a path that does not exist under the project root produces a config finding naming the unresolved ref; a `[refs]` whose paths all resolve produces none (this reads the raw nyxloom.toml — `[refs]` is not exposed on ProjectConfig — so config.py is not touched)"
    negative: "a `[refs]` pointing at a missing docs file lints clean"
    gate: tester-unified
gates: [tester-unified]
escalate_if:
  - "a named contract cannot be met as specified"
  - "scope requires editing a forbidden file (config.py/cli.py/daemon.py/reconcile.py/storage.py)"
---

# P24 — nyxloom.toml schema + `nyxloom lint` config validation

> Tier: sonnet5-high · Base branch: main (input_revision 82593d5).
> Backlog **B2** second half. Config *discovery* already landed (ProjectConfig
> now reads `nyxloom-trove/nyxloom.toml`, see the `B2 2026-07-16` notes in
> config.py); this package adds the missing *validation* half so `nyxloom lint`
> catches config typos before dispatch, not at runtime. Work happens in a
> git worktree on the carve/implement branch.

## Context to read first (read ONLY these)
- `src/nyxloom/schemas/handoff-frontmatter.schema.json` — mirror this JSON
  Schema style ($schema/$id draft 2020-12, `additionalProperties: false`,
  `required`) for the new config schema.
- `nyxloom-trove/nyxloom.toml` — the canonical valid config the schema must
  accept: `[project]`, `[gates.*]`, `[policy]`, `[notify]`, `[refs]`.
- `src/nyxloom/lint.py` — `lint_file` / `lint_project` / `has_blocking` /
  `LintFinding`; add a sibling `lint_config`. Note existing rules emit
  `LintFinding(rule=..., severity="error"|"warning", message=..., path=...)`.
- `src/nyxloom/config.py` `ProjectConfig.load` (READ only, do NOT edit) — shows
  the two-path lookup (`nyxloom-trove/nyxloom.toml`, legacy
  `.nyxloom/project.toml`) and that `[refs]` is dropped, not parsed.
- `tests/conftest.py` `sample_project` fixture + `tests/test_lint.py` — the
  test/fixture pattern to mirror (build config-file fixtures under `tmp_path`).

## Work
1. Add `src/nyxloom/schemas/nyxloom-config.schema.json`: a JSON Schema for
   `nyxloom.toml` covering `[project]` (require `id`, `handoff_globs`),
   `[gates.*]` (require non-empty `argv` array, `phase`, `timeout_seconds`),
   `[policy]`, `[notify]`, `[refs]`, `[mutexes.*]`. Use
   `additionalProperties: false` at the section boundaries you can pin.
2. Add `lint_config(cfg: ProjectConfig) -> list[LintFinding]` in `lint.py`:
   locate the raw config file the same way `ProjectConfig.load` does
   (`nyxloom-trove/nyxloom.toml`, legacy fallback), `tomllib.load` it, validate
   against the schema, and additionally check (a) each `[gates.*].argv` is a
   non-empty list, (b) `[project].worktree_root` is present or explicitly
   defaulted, (c) every `[refs]` path resolves under `cfg.root`. Emit findings
   with rule ids `CFG1`/`CFG2`/`CFG3` (a NEW namespace — do NOT reuse L1–L12 and
   do NOT edit docs/SPEC.md; spec codification is a separate docs follow-up).
3. Fold config findings into `lint_project(cfg)`: add the config file's
   root-relative path as an extra key mapping to `lint_config(cfg)` results, so
   `nyxloom lint` (which already iterates `lint_project`) surfaces them with no
   cli.py change. Keep existing handoff entries and existing lint_project tests
   intact.
4. Tests (`tests/test_lint.py`): O1 (invalid config → blocking `CFG*` finding;
   valid repo config → none), O2 (unresolved `[refs]` flagged; resolving refs
   clean). Build config fixtures under `tmp_path`; do not rely on the repo tree.

## Gate (the ONLY accepted gate)
docker run --rm -v /home/vb/volkb79-2/vbpub:/workspaces/vbpub tester-unified:local bash -c 'cd {worktree}/nyxloom && PYTHONPATH=src /opt/tester-venv/bin/python -m pytest tests -q'

## Scope / forbid
Touch only the three files in `scope.touch`. `config.py` is FROZEN CORE — read
it, never edit it (that is why `[refs]` validation reads the raw TOML). Editing
any forbidden file is out of scope and a BLOCKED trigger, not a stretch.

## BLOCKED rule
If a named contract cannot be met as specified, or the work requires editing a
forbidden file (config.py/cli.py/daemon.py/reconcile.py/storage.py), STOP —
write `BLOCKED: <reason>` to the LOG, commit, and exit. Do not improvise a
workaround.
