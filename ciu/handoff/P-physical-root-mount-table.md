# ciu — derive PHYSICAL_REPO_ROOT per-repo from the mount table

> Tier: sonnet · Date: 2026-07-15 · Requested by user (multi-repo
> devcontainer correctness + future devcontainer relocation safety).

## Problem (reproduced live, 2026-07-15)

In the dstdns devcontainer, four sibling repos are bind-mounted:
`/home/vb/volkb79-2/{dstdns,vbpub,vbpro,netcup-api-filter}` →
`/workspaces/<name>`. Running `ciu env generate` (and
`env generate --define-root /workspaces/vbpub`) inside
`/workspaces/vbpub` produces:

```
REPO_ROOT="/workspaces/vbpub"
PHYSICAL_REPO_ROOT="/home/vb/volkb79-2/dstdns"   # WRONG — devcontainer origin
REPO_NAME="dstdns"                                # follows the same coupling
INSTANCE_ID="98535c"                              # dstdns's id
```

The physical root is derived from the devcontainer's own origin instead of
from where REPO_ROOT is actually mounted, so any bind mount ciu renders for
a non-origin repo points at the wrong host path.

## Contract

1. Physical root resolution MUST consult the process's mount table
   (`/proc/self/mountinfo`): for a given `repo_root`, find the LONGEST
   mount destination that is a prefix of `repo_root.resolve()` and map
   through its source. Reference implementation (working, in production):
   `physical_path()` in `/workspaces/vbpub/tls-edge/scripts/render_standalone.py`
   — port the longest-match logic, do not import across repos.
2. Fallback order when mountinfo yields nothing for repo_root: existing
   devcontainer-origin behavior, then identity (native host). Native-host
   identity (S1.9, REPO_ROOT == PHYSICAL_REPO_ROOT) must keep working.
3. Derived identity fields (`REPO_NAME`, `INSTANCE_ID`,
   `DOCKER_NETWORK_INTERNAL`) MUST follow the spec's definition relative to
   the CORRECT repo (read the S2.8 section in ciu's docs/spec before
   changing anything; if the spec defines them from the devcontainer origin
   rather than REPO_ROOT, flag it in the report instead of guessing).
4. **Hard regression bound: for `repo_root=/workspaces/dstdns` the generated
   env MUST be byte-identical to today's** (`REPO_NAME=dstdns`,
   `INSTANCE_ID=98535c`, `PHYSICAL_REPO_ROOT=/home/vb/volkb79-2/dstdns`) —
   the live landscape's container identity depends on it. Add a test that
   locks these three values for a dstdns-shaped fixture.
5. `--define-root PATH` MUST resolve the physical root for PATH (that was
   its documented intent; today it doesn't reach the physical derivation).

## Scope

- Touch: `src/ciu/workspace_env.py` (entry: `generate_ciu_env`, line ~583,
  and whatever helper currently supplies the physical root), plus new/
  extended tests under `tests/`.
- Forbid: `src/ciu/paths.py` semantics (S1.4 consumers — `to_physical_path`
  reads the env; it must not gain its own detection), any rendering code,
  any spec file edits.

## Oracles

1. Unit: mountinfo fixture (write a temp file, monkeypatch its path or the
   reader) mapping `/workspaces/vbpub -> /home/vb/volkb79-2/vbpub` and
   `/workspaces/dstdns -> /home/vb/volkb79-2/dstdns`; repo_root
   `/workspaces/vbpub` → physical `/home/vb/volkb79-2/vbpub`. Nested-mount
   longest-match case included (e.g. an extra `/workspaces` catch-all entry
   must lose to the more specific one).
2. Fallback: repo_root absent from fixture → current behavior (assert
   against whatever the existing code path produced — characterize it in a
   test before changing).
3. Regression bound of Contract 4.
4. Live smoke (run it, paste output): `cd /workspaces/vbpub &&
   /workspaces/dstdns/.venv/bin/ciu env generate` then grep
   `PHYSICAL_REPO_ROOT` — must print `/home/vb/volkb79-2/vbpub`. Then
   restore the file: `git -C /workspaces/vbpub checkout -- ciu.env` if
   tracked, and regenerate dstdns's own env unchanged:
   `cd /workspaces/dstdns && ciu env generate` + diff against git HEAD →
   no diff (or explain every changed line).
5. Full suite green: `cd /workspaces/vbpub/ciu &&
   /workspaces/vbpub/.venv/bin/python -m pytest tests/ -q` (note the
   existing runner `run-ciu-tests.py` — use it instead if the README says
   it is the canonical gate; report which you used).

## Rules

Work only in `/workspaces/vbpub/ciu` (plus the two live-smoke commands
above). Do not commit. Do not modify tls-edge. If the S2.8 spec contradicts
Contract 1, or the dstdns regression bound cannot hold: STOP, write
`BLOCKED: <reason>` in your report. Deliverables: implementation, tests,
`handoff/reports/P-physical-root-REPORT.md` (result, per-oracle table, gate
output tail, files touched). Final message = short receipt only.
