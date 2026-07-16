---
schema_version: 1
id: nyxloom-P27-project-mount-reachability
project: nyxloom
title: "A registered project the daemon cannot reach: netcup mount + exec-nyxloom container resolution"
tier: sonnet5-high
input_revision: "a9f8991"
depends_on: []
session: fresh
source: {kind: user}
scope:
  touch:
    - "nyxloomd/ciu.compose.yml.j2"
    - "nyxloomd/docker-compose.yml"
    - "exec-nyxloom.py"
    - "src/nyxloom/adapters.py"
    - "tests/test_adapters.py"
    - "tests/test_render.py"
  forbid:
    - "src/nyxloom/daemon.py"
    - "src/nyxloom/reconcile.py"
    - "src/nyxloom/storage.py"
    - "src/nyxloom/config.py"
    - "nyxloomd/ciu.toml"
oracles:
  - id: O1
    observable: "The controller-container resolver returns the running daemon container for a name like `nyxloom-prod-nyxloomd` (and for any `<prefix>-nyxloomd` produced by nyxloomd/ciu.toml container_prefix), while still honouring an explicit $NYXLOOM_CONTAINER override and returning None when no candidate is running; the resolver is importable and unit-tested"
    negative: "the resolver requires both 'nyxloom' AND 'controller' in the name, so the real container `nyxloom-prod-nyxloomd` never matches, exec-nyxloom silently falls back to host mode, and the CLI reads a DIFFERENT state than the daemon it is meant to inspect"
    gate: tester-unified
  - id: O2
    observable: "The nyxloomd compose template and its pre-rendered sibling declare the SAME set of volume bind sources, and that set includes a bind for the netcup-api-filter repo mounted at the same physical path used by the other registered projects — asserted by a test that parses both files, so drift between them fails"
    negative: "a mount is added to the .j2 template but not the pre-rendered docker-compose.yml (or vice versa), so which projects the daemon can see depends on which file was deployed — today's 'keep both in sync' comment is the only guard"
    gate: tester-unified
gates: [tester-unified]
escalate_if:
  - "a named contract cannot be met as specified"
  - "scope requires editing a forbidden file (daemon.py / reconcile.py / storage.py / config.py / ciu.toml)"
  - "making the resolver importable would require moving code out of the files listed in scope.touch"
---

# P27 — A registered project the daemon cannot reach

> Tier: sonnet5-high · Base branch: main (input_revision a9f8991).
> Source: a real adoption attempt. `netcup-api-filter` has a complete, lint-clean trove
> (`nyxloom-trove/` with 6 handoffs, gates bound to its own python-3.11 test-runner), but
> nyxloom **cannot dispatch any of it** — the daemon has no mount for that repo. Filed
> there as `D-001`; the fix belongs here, in nyxloom.

## The two defects

1. **The daemon's project mounts are a hardcoded list.** `nyxloomd/ciu.compose.yml.j2`
   binds exactly `vbpub` + `dstdns` (+ home + docker.sock). The registry already knows
   three project roots (`nyxloom project list` → dstdns, nyxloom, topos) and a fourth is
   coming, but the mount list does not follow it. A project can therefore be *registered
   and unreachable* — the failure mode this package closes for netcup. (Generalising
   mounts from the registry is the principled fix and is **out of scope**: see backlog
   **B11**. This package hardcodes one more line and adds the drift test.)
2. **`exec-nyxloom.py` cannot find the daemon.** `_find_controller_container()` matches
   `"nyxloom" in name and "controller" in name`. The container is
   `nyxloom-prod-nyxloomd` — no "controller" — so the wrapper always falls through to the
   host fallback and runs the CLI against `~/.local/state`, while the authoritative state
   is the daemon's. Every `exec-nyxloom status` is answering from the wrong ledger.
   (Verified: `docker exec nyxloom-prod-nyxloomd python -m nyxloom.cli status` works;
   the container has no `nyxloom` on PATH, so the wrapper's `docker exec … nyxloom …`
   form would also need the venv path or an installed entrypoint — check this.)

## Context to read first (read ONLY these)

- `exec-nyxloom.py` — `_find_controller_container()` (~line 30) and `main()` (~line 61).
  Note it `os.execvp`s `docker exec <container> nyxloom ...`; confirm `nyxloom` resolves
  inside the image (the daemon's own CMD uses `/opt/nyxloom-venv/bin/python -m
  nyxloom.cli`, which suggests it may not).
- `nyxloomd/ciu.compose.yml.j2` — the `volumes:` list; the header comment states the
  invariant this package tests: *"the sibling docker-compose.yml is the pre-rendered copy
  … keep both in sync: edit HERE, re-render or hand-sync."*
- `nyxloomd/docker-compose.yml` — the pre-rendered sibling that must agree.
- `nyxloomd/ciu.toml` — READ ONLY. `container_prefix = "nyxloom-prod"` is what makes the
  container name `nyxloom-prod-nyxloomd`; the resolver must not hardcode that literal.
- `src/nyxloom/adapters.py` — where a docker-CLI helper belongs if you move the resolver
  somewhere importable (the wrapper then imports it). Mirror the existing adapter style.
- `tests/test_adapters.py`, `tests/test_render.py` — the fixture/assertion patterns to
  mirror. Do not invent a new test style.
- `nyxloom-trove/nyxloom.toml` — the SELF-HOSTING CAVEAT at the bottom: a merge to
  daemon-core needs a container rebuild to take effect. Relevant to activation (below).

## Work

1. Fix the resolver contract: match the daemon container by the name the ciu config
   actually produces (`<container_prefix>-nyxloomd`), keep the `$NYXLOOM_CONTAINER`
   override winning, and keep returning `None` when nothing matches (host fallback must
   still work). Do **not** hardcode `nyxloom-prod`.
2. Make the resolver **importable and unit-tested**. `exec-nyxloom.py` is a hyphenated
   script and cannot be imported as a module — that is why this bug shipped untested.
   Move the resolver into `src/nyxloom/adapters.py` and have the wrapper import it.
   Keep the wrapper working when `src/` is not yet on `sys.path` (it currently sets
   PYTHONPATH itself — preserve that ordering).
3. While there: verify the `docker exec` argv actually invokes the CLI inside the image.
   If `nyxloom` is not on PATH there, use the venv form the daemon's own CMD uses. If
   this turns out to need a change outside `scope.touch`, that is a BLOCKED trigger.
4. Add the netcup bind to **both** `nyxloomd/ciu.compose.yml.j2` and
   `nyxloomd/docker-compose.yml`, mounted at the same physical path convention as the
   other repos (`/home/vb/volkb79-2/netcup-api-filter:/workspaces/netcup-api-filter`),
   with a short comment matching the existing ones.
5. Add the drift test (O2): parse both compose files, assert their volume **source sets**
   are equal, and assert the netcup source is present. This is the invariant the header
   comment asks a human to maintain by hand — make it mechanical.

## Not in scope — the operator activates

This package **authors** the change; it does not take effect until the operator runs
`ciu up --dir nyxloom/nyxloomd` (the self-hosting caveat in `nyxloom.toml`) and then
`nyxloom project add` for the netcup trove. **Do NOT restart, rebuild, or `ciu up` the
running daemon**, and do not mutate the live registry from an attempt — you would be
pulling the floor out from under the process dispatching you. Note both steps in the
REPORT as operator follow-ups.

## Gate (the ONLY accepted gate)

```
docker run --rm -v /home/vb/volkb79-2/vbpub:/workspaces/vbpub tester-unified:local bash -c 'cd {worktree}/nyxloom && PYTHONPATH=src /opt/tester-venv/bin/python -m pytest tests -q'
```

## Scope / forbid

Touch only the six files in `scope.touch`. `daemon.py`/`reconcile.py`/`storage.py`/
`config.py` are FROZEN CORE for this package — nothing here needs them. `ciu.toml` is
read-only: `container_prefix` is the input the resolver must honour, not a value to bend.

## BLOCKED rule

If a named contract cannot be met as specified, or the work requires editing a forbidden
file, STOP — write `BLOCKED: <reason>` to the LOG, commit, and exit. Do NOT improvise a
workaround.
