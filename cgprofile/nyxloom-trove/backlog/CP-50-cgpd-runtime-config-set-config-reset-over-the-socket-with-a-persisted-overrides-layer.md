---
kind: backlog-entry
schema_version: 1
id: CP-50
title: "cgpd runtime settings over the socket: config-set / config-reset with a persisted overrides layer, audit log and doctor shadow warning"
status: fixed
type: "feature"
component: "daemon"
context_estimate: "medium"
provenance: "Operator decision 2026-11-04 (cgpctl plan defaults accepted with one change: runtime settings may be changed over the socket); contract in docs/CGPCTL-SPEC.md section 3.5, docs/PROTOCOL.md section 10.4, docs/NATIVE-DEPLOYMENT.md section 3.2.1"
filed_by: "vbpub maintenance"
spec_owner: "cgroup-profiler daemon"
priority: 2
filed_date: "2026-11-04"
---

## Problem

The plan rejected a socket write path for settings. The operator reversed that: an operator needs to change the access list, the sampling
interval, retention, the export cap and the concurrency caps live, without editing a file and cycling a reload, while host-setup's
`/etc/cgprofile/cgpd.toml` stays the authority for what it declares.

## Design

Contract: `docs/CGPCTL-SPEC.md` sections 2.3 (`config show|set|reset`) and 3.5, `docs/PROTOCOL.md` sections 10.4 to 10.7, `docs/NATIVE-DEPLOYMENT.md`
section 3.2.1. Implemented with the cgpd package (CP-48) for the daemon half and the cgpctl package (CP-47) for the client half; this entry owns the
acceptance list.

- Runtime-tunable allow-list: `allow_uids`, `interval`, `damon_default`, `keep_sessions`, `keep_days`, `export_max_bytes`, `max_sessions`,
  `max_exports`, `max_watchers`. Never socket paths, the group, `observe_slices` or `gates_slice`.
- Precedence override > file > default; overrides in memory, or with `--persist` in `/var/lib/cgprofile/overrides.toml` (root, 0600, atomic write).
- Wire: `config` get gains `source: override`, `shadows`, `tunable`, `persisted`; new `config-set`, `config-reset`; error `persist-failed`.
- Root only by `SO_PEERCRED` uid 0, fail closed; server-side validation with the file's schema function; applied atomically with the reload swap.
- Audit line `config-audit id=... op=... uid=... pid=... key=... old=... new=... persist=... ts=...` for every attempt, refusals included.
- `systemctl reload` keeps overrides; `cgpd reload --reset-overrides` drops them; restart keeps persisted ones only.
- `doctor` `overrides` check: warn when an override shadows a file-declared value or when overrides-file entries were ignored.
- New config keys `max_exports` and `max_watchers` (previously constants).

## Acceptance criteria

- Non-root `config-set` / `config-reset` is `peer-refused`, including with an empty `allow_uids`; unreadable credentials fail closed; uid 0 stays authorised
  whatever `allow_uids` says (test: a `config-set allow_uids` that excludes every group member leaves uid 0 able to repair).
- A non-tunable or unknown key and a type or range violation are `bad-argument` listing the tunable keys; a failed persist leaves nothing applied.
- Overrides survive SIGHUP and a restart (persisted only); an invalid, unknown-key or wrong-mode overrides file is ignored with the warning and never
  blocks startup.
- Audit line asserted for accepted, refused and reset operations.
- Goldens listed in `PROTOCOL.md` section 10.7 (config-set, config-reset, refused, persist-failed, config with an override).
- The file schema and the `config-set` validator are one function (test).

## Dependencies and order

After CP-46. Lands inside CP-47 / CP-48; blocks the release and the host-setup package (MDT-055 needs `doctor` shadow warnings in `check.sh`).

## Updates

**2026-11-04** FIXED, daemon half (cgp2 package 3: 994ecb972 daemon_config, 1c7df3d53 / d9347fe21 `config`, `config-set`, `config-reset` verbs with the persisted overrides layer, bef71678a `cgpd config check` / `reload [--reset-overrides]`). The cgpctl half was package 2.
