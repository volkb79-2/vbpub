---
kind: backlog-entry
schema_version: 1
id: NL-43
title: "Add project-scoped nyxloom doctor for trove and config readiness"
status: open
type: "feature"
severity: "medium"
component: "cli"
context_estimate: "medium"
provenance: "operator session, netcup-api-filter CLI use, 2026-10-15"
filed_by: "operator request via Codex"
spec_owner: "docs/CLI-REFERENCE.md; schemas/nyxloom-config.schema.json"
filed_date: "2026-10-15"
---

## Observed mechanism and reproduction

The local `nyxloom` CLI exposes `init`, `onboard`, `lint`, and managed `backlog` commands. `doctor` is currently on `nyxloomctl`, whose existing contract is host/operator diagnostics over registered projects, replay state, and liveness. It does not provide a simple local check that answers whether the current checkout has a usable trove and whether its `nyxloom.toml` matches Nyxloom's current tables and keys.

A user who encounters a missing optional table gets a bare refusal from the command they tried; there is no local project diagnostic that explains the config path, required keys, or which trove files are present.

## Why nyxloom owns it

Local project configuration and trove authoring are owned by the `nyxloom` executable and must work without the Nyxloom daemon or project registry. The existing `nyxloomctl doctor` remains a separate host/operator surface.

## Proposed contract

Add a read-only `nyxloom doctor` for the nearest project discovered from the current directory, using the same resolver as local `lint` and `backlog`. It reports the resolved project root and `nyxloom.toml` path; checks TOML syntax and the current shipped config schema, including missing required keys, unknown/retired keys, invalid values, and tables that are optional versus required; and checks presence of the configured trove, direction-spine files, backlog inbox/managed-entry directory, handoff, and report locations as applicable. Each finding names its file and table/key path and gives an accurate remediation without inventing a value. Optional sections such as `[backlog_entries]` are reported as unused, not as a project error. Output must not reveal credential values.

The command must not require registry registration, daemon availability, host service access, or write permissions. Keep `nyxloomctl doctor` host diagnostics and its grammar unchanged.

## Oracles

- A valid local project reports the resolved config and passes checks without registry or daemon state.
- Missing config, malformed TOML, missing required keys, unknown keys, invalid values, and absent declared files each produce a finding naming the precise path/table/key.
- An omitted optional table is identified as optional and does not fail the project.
- Secret-valued config fields are never printed in diagnostic output.
- `nyxloomctl doctor` continues to invoke the existing host/operator checks.
- README, DESIGN-GUIDE, CONSUMERS, CLI reference, and generated help describe the project doctor and distinguish it from `nyxloomctl doctor`.

## SPEC ownership

`src/nyxloom/cli_registry.py`; `src/nyxloom/config.py`; `src/nyxloom/doctor.py` or a new project-local diagnostic module; `schemas/nyxloom-config.schema.json`; `docs/CLI-REFERENCE.md`.

## Updates
