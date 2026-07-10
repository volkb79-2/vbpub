# P57 - Docker-Name Entity Selectors

## Goal

Add `--container NAME_OR_PREFIX`, resolved via groop's existing Docker
metadata join, anywhere groop currently takes a raw cgroup-path/entity
identifier — so a user can start from the docker container name they already
know instead of hand-resolving `docker-<64hex>.scope` paths.

## Independence

Pure ergonomics; small; independent of P53, P54, P55, and P56 — none need to
exist or merge first. It depends only on already-merged plumbing: the
docker-join module `src/groop/collect/dockerjoin.py`, which the collector
already calls every sweep (`Collector.collect_once()` →
`enrich_entities(entities, self.docker_inspect)`,
`src/groop/collect/collector.py` line 60) to populate `Entity.docker`
(`DockerMeta`: `cid`, `full_id`, `name`, `image`, `compose_project`,
`ptero_uuid`) for every `EntityKey` whose cgroup-path leaf matches
`docker-<64hex>.scope` (`DOCKER_SCOPE_RE` /
`docker_id_from_key()` in `dockerjoin.py`). P57 adds a resolver that inverts
this join (name → `EntityKey`) using data the collector already produces —
it adds no new Docker API surface. If P55's `--entities`/`--slice` or P56's
`groop squeeze --target` already exist when this is implemented, wire
`--container` into them per the composition rules below; if they do not yet
exist, land `--container` only where identifier flags already exist today
(`inspect-files --target`, `action --target`) and note the future
composition points in code/docs for whichever of P55/P56 lands later to pick
up — no blocking dependency either direction.

## Motivation

Every live workflow exercised so far starts from a docker container name,
not a cgroup path: `container-mempress.sh` (the script P56 specifies
absorbing) takes `<container-name-or-prefix>` and does its own
`docker ps`/`docker inspect`/`find /sys/fs/cgroup ... -name
"docker-${FULL_ID}.scope"` resolution inline; `damon_cli.py::cmd_timeseries_
container`'s "largest container" default target logic (cited in
`TUI-SPEC.md` around DAMON vaddr target defaults) likewise starts from
container identity; and routine groop inspection (`inspect-files --target`,
`action --target`) already accepts "container id/name" as one of several
target shapes per their existing `--help` text (`src/groop/cli.py` lines
~203, ~209) — but only where a plan/preview module happens to implement its
own name lookup, not via one shared resolver. Hand-resolving
`docker-<64hex>.scope` is error-prone (wrong container matched on a loose
`docker ps --filter name=` prefix, stale IDs after a restart) and is
currently reimplemented ad hoc per caller.

## Workflow

- Branch: `feat/groop-p57-docker-name-entity-selectors`
- Worktree: `.worktrees/-groop-p57-docker-name-entity-selectors`
- Touch only `groop/**`; write P57-LOG.md/P57-REPORT.md; commit, do not merge.

## Requirements

- Add a resolver function, e.g. `resolve_container_key(name_or_prefix: str,
  entities: dict[EntityKey, Entity]) -> EntityKey` (module home: alongside
  `dockerjoin.py`, e.g. `src/groop/collect/dockerjoin.py` or a new
  `src/groop/collect/container_resolve.py` if keeping join-population and
  name-resolution logically separate reads cleaner — pick one and justify
  it in the LOG), that scans already-enriched `Entity.docker` metadata
  (`DockerMeta.name`, `DockerMeta.cid`) for entities whose docker name
  equals `name_or_prefix` or starts with it, and whose `EntityKey` matches
  `DOCKER_SCOPE_RE`. This requires entities to already be docker-enriched
  (i.e. resolution happens after `enrich_entities()` has run in the current
  sweep) — document that ordering constraint explicitly since it means
  `--container` cannot resolve against a *stale*/cross-sweep entity set.
- Ambiguity/no-match handling: exact name match wins over prefix match when
  both exist; multiple distinct prefix matches is an error (exit 2, list the
  ambiguous candidates by name so the user can disambiguate) rather than an
  arbitrary first-match, matching the existing target-validation strictness
  precedent (`src/groop/actions/catalog.py validate_target` rejects
  ambiguous/multi-unit targets rather than guessing). Zero matches is also
  exit 2 with a clear "no running container matches" message, mirroring the
  script's `die "no running container matches name filter"`.
- Wire `--container NAME_OR_PREFIX` into every CLI surface that already
  takes a raw identifier today: `inspect-files plan/read --target`
  (`src/groop/cli.py` ~203/~209) and `action preview/execute --target`
  (~489/~495) as an alternative input mode — `--container` resolves to the
  cgroup-path/container-id form those flags already accept, so the resolver
  runs before the existing validation, not as a parallel code path through
  it. Reject giving both `--target` and `--container` together (exit 2,
  "choose either --target or --container"), matching the existing
  mutually-exclusive-flag-pair pattern already used elsewhere in `cli.py`
  (e.g. `--record`/`--replay`).
- Composition with P55 (if merged): extend `--entities`/`--slice` acceptance
  to also take `--container NAME_OR_PREFIX` as a third selector form that
  resolves to a matched `EntityKey` and feeds the same
  ancestor-inclusion/collection-time-pruning path P55 specifies — do not
  reimplement pruning/ancestor logic here, just supply P55's selector set
  with one more resolved key. If P55 is not yet merged, skip this bullet
  entirely (do not stub dead code paths for an unmerged package); leave a
  one-line TODO/doc pointer instead.
- Composition with P56 (if merged): accept `--container NAME_OR_PREFIX` as
  an alternative to `groop squeeze --target CGROUP_PATH`, resolving before
  P56's own root/`memory.min`/`--force` checks run — same "resolve first,
  then hand off to existing validation" shape as the `inspect-files`/
  `action` wiring above. If P56 is not yet merged, skip this bullet; leave a
  one-line TODO/doc pointer instead.
- Add tests: exact-name match, unambiguous-prefix match, ambiguous-prefix
  rejection (multiple candidates, exit 2, names listed), zero-match
  rejection (exit 2), resolution against a fixture entity set with
  `Entity.docker` populated (reuse existing `dockerjoin.py` test fixtures/
  patterns rather than inventing new fixture shapes), `--target`/
  `--container` mutual-exclusion rejection on each wired CLI surface, and —
  only if P55/P56 are merged at implementation time — their respective
  composition paths.
- Update `README.md` quickstart/CLI docs (document `--container` next to
  existing `--target` usage on each wired subcommand) and
  `docs/ROADMAP.md`/`docs/STATUS.md` package entries.

## Out Of Scope

- TUI-side container-name jump/search (the TUI already has its own filter
  key (`/`) over rendered rows; wiring `--container` into TUI navigation is
  future polish, not this package).
- Any new Docker API calls beyond what `dockerjoin.py` already performs —
  P57 resolves against data the collector already gathered this sweep; it
  does not add a second `docker inspect`/`docker ps` call path.
- DAMON vaddr session target selection — `TUI-SPEC.md`'s DAMON default-target
  heuristic (`cmd_timeseries_container`-style "largest container") is cited
  as motivation only; wiring an explicit `--container` flag into DAMON
  session start (`groop damon paddr start` et al.) is future work once that
  CLI surface accepts a per-target identifier at all (it currently does
  not).
- Changing `dockerjoin.py`'s forward join (cgroup key → `DockerMeta`) or its
  matching regex/UUID heuristics — P57 only adds a reverse (name → key)
  lookup over existing data.
