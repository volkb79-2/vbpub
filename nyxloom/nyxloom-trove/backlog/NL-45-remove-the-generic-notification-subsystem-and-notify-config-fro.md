---
kind: backlog-entry
schema_version: 1
id: NL-45
title: "Remove the generic notification subsystem and notify config from Nyxloom"
status: open
type: "feature"
severity: "medium"
component: "notifications"
context_estimate: "medium"
provenance: "operator session, netcup-api-filter CLI use, 2026-10-15"
filed_by: "operator request via Codex"
spec_owner: "src/nyxloom/notify.py; src/nyxloom/config.py; schemas/nyxloom-config.schema.json"
filed_date: "2026-10-15"
---

## Observed mechanism and reproduction

Nyxloom currently carries a generic outbound notification subsystem: the self-hosted `nyxloom-trove/nyxloom.toml` has `[notify]`, and the greenfield `_INIT_NYXLOOM_TOML` template emits an empty `[notify]` table. `NotifyConfig` and `src/nyxloom/notify.py` are used by daemon/effect notifications, findings, command-channel replies, extraction-follow alerts, and the Mattermost intake bridge. Existing tests and user-facing docs describe these paths.

NL-17 records the earlier request to keep/add a backend adapter. The operator changed direction on 2026-10-15 and requested removing `[notify]` and notify itself from the Nyxloom project.

## Why nyxloom owns it

The config model, notification dispatcher, runtime callers, scaffold, and public documentation all live in Nyxloom. Consumers should not need to carry a Nyxloom-owned notification backend contract that the project no longer wants.

## Proposed contract

Retire the generic outbound notification feature and its `[notify]` configuration, including the config model/schema, scaffold/template values, dispatcher module, tests, and public docs/CLI surfaces. Inventory and account for every current caller so no dead or silently renamed generic notification path remains. In particular, explicitly settle the command-channel and intake-bridge reply paths: if either remains, it must have a narrowly owned transport/configuration that does not import or depend on `notify.py`; otherwise remove that behavior under the approved scope. Do not preserve or expand the Mattermost/ntfy adapter direction from NL-17.

Update NL-17 to `obsolete` with a reason linking this replacement direction. Update README, DESIGN-GUIDE, CONSUMERS, CLI reference, configuration guides, and changelog in the same change so the public capability surface matches the code.

## Oracles

- The shipped schema and scaffold no longer expose or emit `[notify]`.
- No Nyxloom runtime caller imports the generic `notify` module after the removal; retained intake/command behavior is covered by its own explicitly scoped transport contract.
- Removed notification events no longer attempt outbound delivery, and no notification-only CLI option remains advertised.
- README, DESIGN-GUIDE, CONSUMERS, CLI/config references, and changelog make no current-capability claim for the removed subsystem.
- NL-17 records the superseding reason and is absent from the open backlog view.

## SPEC ownership

`src/nyxloom/config.py`; `src/nyxloom/notify.py`; `src/nyxloom/intake_bridge.py`; `src/nyxloom/commands.py`; `src/nyxloom/effects.py`; `src/nyxloom/findings.py`; `src/nyxloom/daemon.py`; `src/nyxloom/session_extract/follow.py`; config schema, scaffold template, tests, and user-facing docs.

## Updates
