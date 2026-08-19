---
schema_version: 1
id: ciu-P09-configfile-schema-validation
project: ciu
component: composefile
title: "configfile entries accept schema=<path>: rendered app config validated against the app's JSON schema at render time, failing with the key path"
tier: implement-2
input_revision: "0b920f806b4aedcc12014ebb028b917858450de0"
source: {kind: backlog, ref: "KNOWN_ISSUES_TODO_BACKLOG.md#CIU-37"}
stack: none
depends_on: []
session: fresh
scope:
  touch:
    - "src/ciu/composefile.py"
    - "pyproject.toml"
    - "tests/tests/test_ciu_configfile_schema.py"
    - "docs/CONFIG.md"
    - "docs/SPEC.md"
    - "CHANGES.md"
    - "KNOWN_ISSUES_TODO_BACKLOG.md"
    - "nyxloom-trove/reports/ciu-P09-configfile-schema-validation-LOG.md"
  forbid:
    - "src/ciu/engine.py"
    - "src/ciu/deploy.py"
    - "src/ciu/config_model.py"
    - "nyxloom-trove/backlog.md"
    - "nyxloom-trove/decisions.md"
    - "nyxloom-trove/roadmap.md"
oracles:
  - id: O1-schema-key
    observable: "A configfile entry [<root>.<svc>.configfile.<name>] accepts an OPTIONAL schema = \"<path relative to stack dir>\" key, validated in the same key-validation block that guards template/target/instances (src/ciu/composefile.py:516-548): missing file → tagged error at declaration-validation time (before any render); non-TOML target with schema declared → tagged error (v1 validates TOML targets only, stated in docs)."
    negative: "schema key silently ignored when the file is missing; validation deferred to first render"
    gate: "tester-unified"
  - id: O2-validate-after-render
    observable: "In render_configfiles, immediately after the atomic write (os.replace, composefile.py:614-620) and BEFORE the ConfigFileMount append: when schema is declared, the rendered bytes are parsed with tomllib and validated against the JSON schema (Draft 2020-12 via the jsonschema library); a violation fails the run with a tagged error naming service, configfile name, per-instance suffix when instances>1, and the offending KEY PATH (jsonschema's absolute_path joined with '.'); the stale-file sweep does NOT leave the invalid rendered file behind as consumable."
    negative: "validation passing on additionalProperties because the schema was loosened in test fixtures; error text without the key path; a failing render leaving a mount emitted"
    gate: "tester-unified"
  - id: O3-optional-dep
    observable: "jsonschema is an OPTIONAL extra (pyproject [project.optional-dependencies] schema = [\"jsonschema\"], precedent: ssh = [paramiko]); when a schema key is declared and jsonschema is NOT importable, the run fails with a tagged error telling the operator to install ciu[schema] — never a silent skip. When no schema key exists anywhere, the import is never attempted."
    negative: "a hard dependency added; a declared schema silently unvalidated when the lib is absent (§the-worst-outcome)"
    gate: "tester-unified"
  - id: O4-docs
    observable: "docs/CONFIG.md configfile section documents the key + the ciu[schema] extra + the render-verb caveat (ciu render renders TOML only — configfile schema validation runs on the up/dev path, engine step 12); docs/SPEC.md normative clause with local S-numbering; CHANGES.md entry; KNOWN_ISSUES CIU-37 row → FIXED with evidence."
    negative: "docs omitting the ciu-render caveat (the consumer filed the ask expecting render-time)"
    gate: "tester-unified"
gates: ["tester-unified"]
escalate_if:
  - "ConfigFileMount consumers outside composefile.py require a schema field on the dataclass and editing them exceeds scope — BLOCKED naming the consumer"
mutexes: [merge-lane]
review_focus:
  - "failure ordering: declaration errors before render, content errors after write but before mount emission"
  - "the optional-dependency failure mode is fail-loud, never skip"
---

# ciu-P09 — schema-validated configfile render (CIU-37)

## Context to read first
1. `KNOWN_ISSUES_TODO_BACKLOG.md#CIU-37` — the ask: the app's GENERATED JSON schema is the
   source; ciu only checks. (dstdns generates per-service schemas with
   `scripts/gen-config-schema.py`; `additionalProperties:false` end-to-end.)
2. `src/ciu/composefile.py:433-627` (`render_configfiles`) — the exact function: key validation
   :516-548, per-instance render loop :560-579, atomic write :614-620, mount emission :622-631.
3. `src/ciu/composefile.py:382-410` (`ConfigFileMount`) — extend only if O2 needs it; the
   validation itself has `cfg` in scope and may not need to.
4. `pyproject.toml` optional-dependency precedent (`ssh = [paramiko]`).

## Dispatch contract
The feature is a **check, not a transformation**: rendered bytes are never modified. v1 scope is
TOML targets only. The consumer's schema is opaque input — ciu performs no schema authoring, no
defaulting, no coercion. Out-of-scope / forbid: `engine.py` / `deploy.py` / `config_model.py`
stay untouched (the insertion point is entirely inside `render_configfiles`); a `ciu render
--configfiles` verb is explicitly NOT this package (note it in the tracker as a follow-up
candidate if reviewers want it); sibling repos are read-only context.

## Work
1. `schema` key in the validation block (O1).
2. Post-write validation (O2) + the optional-dependency guard (O3).
3. Tests: valid passes; wrong key fails naming path; missing schema file; multi-instance suffix
   in the error; absent-lib fail-loud; absent-schema-key never imports.
4. Docs per O4; CIU-37 → FIXED with evidence in tracker and LOG.

## Environment setup
Implement in the dispatched worktree at `../.worktrees/<branch>/ciu` (trove `worktree_root`).
Standard ciu gate (`run-ciu-tests.py`, 100% line+branch, tester-unified; no live Docker — all
fixtures under tmp_path).

## BLOCKED rule
Impossible within scope.touch → `BLOCKED: <mechanical reason>` in the LOG, commit, exit.
Forbidden workarounds: making jsonschema a hard dep; warn-and-continue; validating only in a
new verb nobody calls.
