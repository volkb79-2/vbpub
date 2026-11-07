---
kind: backlog-entry
schema_version: 1
id: CP-35
title: "container: targets never re-resolve after a restart, and inferred-name/label targets use docker ps inventories; a standing monitor (CP-28) needs exact-name re-resolution at the discovery cadence"
status: open
type: "feature"
component: "targets"
context_estimate: "medium"
provenance: "Pre-release deep review of the 1.1.0 wave branch, 2026-10-27 (fable-cgprofile-20261027), judging lib/targets.py against the CP-28 acceptance list"
filed_by: "Claude (cgprofile 1.1.0 pre-release review)"
spec_owner: "cgroup-profiler sampler"
priority: 2
filed_date: "2026-10-27"
---

## Gap (code reality versus CP-28's acceptance list)

CP-28 requires monitor targets "resolved by exact docker name or exact 64-hex id, and re-resolved after a container
restart; never by prefix or label for a monitor target; never starts, stops or inspects any other container". Today:

1. `targets.resolve_container` resolves ONCE, in the caller, to a `containerid:` spec; `Target.container_id` is
   immutable and `Membership.refresh` only checks that the resolved cgroup directory still exists. A container that
   is recreated (`docker compose up` after an image pull, a game-server restart that re-creates the container) gets
   a NEW 64-hex id and a new `docker-<id>.scope`; the old target disappears from the membership (`cgroup_disappeared`
   event) and never comes back. For a gate lane this is correct; for a 24 h monitor it is a silent hole in the series.
2. `docker inspect <ref>` accepts an ID PREFIX (documented: `container:<name|id|prefix>`); 1.1.0 pins the lookup to
   `--type container` and a 64-hex `Id`, but prefix matching remains. A monitor target must be exact.
3. The no-scheme inference (`parse_target` with no `scheme:`) and `label:` run `docker ps --filter` / `docker inspect
   NAME`, i.e. inventories. Acceptable for the interactive CLI; a monitor must refuse `label:` and bare names.
4. The daemon's `ctl start` accepts only `containerid:` (a run-gate lane identity); there is no daemon path for a
   named container at all (CP-28 open question 1).

## Required contract

- A `container:` target option `@reresolve` (default ON in monitor mode, OFF for `run`/`attach`): at the discovery
  cadence, if the resolved cgroup is gone, run exactly ONE `docker inspect --type container <original exact name>`
  (the name recorded at parse time, never the prefix), and on a new 64-hex id re-locate the scope in the tree. The
  target keeps its `key`/`label`; the record carries the new `container_id`; emit `cgroup_appeared` with the new
  path and a `container_recreated` event naming old and new ids. Counters restart from zero in the new cgroup: rates
  stay `None` across the boundary (already guaranteed by `util.rate`).
- Monitor mode refuses `label:`, bare-name inference and ID prefixes (`container:` must be an exact name or exact
  64-hex id; validate the name with `docker inspect`'s returned `Name` equality).
- No `docker ps`, no listing, in any monitor path (the memory rule "inspect only named containers" applies).

## Oracles

A fixture tree where the scope directory is replaced by one with a new id between two discovery ticks, with a mocked
`_docker_json` returning the new id: the series continues under the same target key, a `container_recreated` event
is written, and `_docker_json` was called exactly once with `("inspect", "--type", "container", "<name>")`.

## Folds / depends

Feeds CP-28 carve (b) (monitor runner); independent of the daemon-vs-subcommand decision.
