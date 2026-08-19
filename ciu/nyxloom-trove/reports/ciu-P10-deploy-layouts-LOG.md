# LOG — ciu-P10-deploy-layouts

- Package: `ciu-P10-deploy-layouts`
- Branch: `docs/ciu-P10-deploy-layouts` (forked from origin/main @ `98549075`)
- Worktree: `/workspaces/vbpub/.worktrees/ciu-worktree-automation-backlog`
- Handoff input_revision: `3639b18c`
- Status: COMPLETE (no BLOCKED)

## Environment / gate notes

**Evidence ladder (brief rev 3-5):** the pytest result below is the
**iteration signal** — same suite, same 100% line+branch fail-under, in a
LOCAL venv. Recorded as "venv run", never as "the gate". Checkpoint evidence =
trove `[gates.tester-unified]` argv run by the operator/controller at merge
review; no docker/tester-unified container started by the implementer.

**Environment caveat (unchanged):** the devcontainer's ambient
`REPO_ROOT` / `PHYSICAL_REPO_ROOT` / `CIU_GOV_READ_IOPS` leak into a handful
of engine tests; the venv run scrubbed them
(`env -u REPO_ROOT -u PHYSICAL_REPO_ROOT -u CIU_GOV_READ_IOPS`).

## Iteration-signal result (venv run)

```
env -u REPO_ROOT -u PHYSICAL_REPO_ROOT -u CIU_GOV_READ_IOPS \
  .venv/bin/python run-ciu-tests.py
TOTAL   7267  0  2846  0  100%   (line+branch, --cov-fail-under=100)
2201 passed in ~12.6s
```

The run went red twice before green, both coverage-only, each closed by a
targeted test (see below) — no source workaround, no exclusion.

## Work done

Scope.touch only (verified via `git diff --stat 3639b18c` on
transport_ssh.py/hosts.py/deploy.py/engine.py = **empty**, O3):

1. `src/ciu/deploy_pkg/layouts.py` (new) — S7.5c model:
   - `ENVIRONMENTS = ("dev", "test", "staging", "prod")` — closed vocabulary.
   - `Layout` dataclass (name, environment, ordered `hosts`, `bundles` per
     host, optional description) — precedent `deploy_pkg/profiles.py` `Profile`.
   - `resolve_layout(global_cfg, hosts_cfg, name)` — validates, all tagged
     `[S7.5c]`, all before any transport: unknown layout; entry not a table;
     missing/invalid `environment`; empty/non-table `hosts`; unknown host
     (naming layout+host); host entry not a table; bundles not a list; unknown
     bundle (naming layout+host+bundle, validated via `resolve_profiles(..., 
     env={})` so ambient `CIU_HOST_PROFILE`/`CIU_SERVICES_PROFILE` cannot
     contaminate resolution). Declaration order preserved (dict order) = the
     execution order.
   - `list_layouts(global_cfg)` — pure DECLARED listing, deliberately no
     validation and no inventory requirement.
2. `src/ciu/deploy_pkg/__init__.py` — re-export `Layout`, `resolve_layout`,
   `list_layouts`.
3. `src/ciu/cli.py`:
   - `up --layout <name>` — resolves the layout, then FOR EACH host in
     declaration order runs the EXISTING `up --host` path (ssh_sync → single
     remote argv `cd <bundle_dir> && ciu env generate && ciu render && ciu up
     [remaining]`) with `CIU_SERVICES_PROFILE=<host bundles comma-joined>` and
     `CIU_LAYOUT` / `CIU_LAYOUT_HOST` / `CIU_DEPLOY_ENVIRONMENT` prepended as
     VAR=val exports inside the ONE remote argv string (same one-argv
     discipline as the --host branch). A host failure **aborts** the sequence
     (no continue-on-error), naming the failed host and the not-yet-deployed
     remainder. No local-env leakage: exports live only in the remote string.
   - `--layout` is mutually exclusive with `--host` AND `--profile` (tagged
     `[S7.5c]` error). The `--profile` exclusion is a deliberate extension of
     the handoff's `--host` exclusion, documented: S7.5 CLI precedence means a
     passthrough `--profile` would silently override the exported
     `CIU_SERVICES_PROFILE`, breaking the layout's bundle contract.
   - `ciu layouts` verb (implementer's choice over extending `ciu profiles`,
     since `deploy.py` is forbidden) — lists declared layouts
     (`name: environment=<env> hosts=[a, b]`), documented in CONFIG.md.
   - `_USAGE` + `_VERB_HELP` updated for `--layout` and the `layouts` verb.
4. Tests:
   - `tests/tests/test_ciu_deploy_layouts.py` (16 tests) — model resolution
     happy paths, order preservation, every tagged error path, ambient-env
     immunity, lenient listing.
   - `tests/tests/test_ciu_cli_layouts.py` (13 tests) — per-host order via the
     existing push path (fake ssh seams, precedent
     `test_ciu_cli_remote_dispatch.py`), exact remote-argv composition
     (shlex.quote discipline), quoting of hostile bundle_dir, abort-on-sync /
     abort-on-exec naming host + remainder, unknown layout / missing-inventory
     host / get_host-failure exits, --layout/--host and --layout/--profile
     exclusion, `ciu layouts` output (empty + populated), no local-env leak.
5. Docs (O4):
   - `docs/SPEC.md` — S7.5c normative clause (closed `environment` vocab,
     ordered push, env-export contract, tagged validation, abort semantics,
     --layout/--host/--profile exclusion, `ciu layouts`).
   - `docs/CONFIG.md` — `[deploy.layouts.<name>]` section: key table, the
     remote-command environment contract table (CIU_SERVICES_PROFILE /
     CIU_LAYOUT / CIU_LAYOUT_HOST / CIU_DEPLOY_ENVIRONMENT), worked examples
     (dev-local single-host + 3-host prod split) with pasteable `ciu up
     --layout` invocations; `[deploy]` subsections row added.
   - `CHANGES.md` — Unreleased entry (CIU-34, D-105 Q2, S7.5c).
   - `KNOWN_ISSUES_TODO_BACKLOG.md` — CIU-34 row + detail block → **FIXED**
     with evidence.
6. P10 handoff escalate_if #2 not triggered: the worktree-automation branch
   HAS merged; `test_ciu_documentation_contract.py` passes unmodified against
   the new docs shape (checked in the same targeted run).

## Controlled red during development

The full venv run was observed red three times, each a real coverage gap
closed by a test (never by weakening an assertion or adding an exclusion):
cli.py `get_host`-ValueError path (new CLI test), layouts.py:84 entry-not-a-
table and :112 host-entry-not-a-table (two new model tests). Final green:
2201 passed, 7267/2846, 100%.

## Deviations

1. `--layout` mutually exclusive with `--profile` in addition to `--host`
   (handoff required `--host`; `--profile` added because S7.5 CLI precedence
   would otherwise let a passthrough override the layout's bundles).
2. `ciu layouts` chosen over extending `ciu profiles` output (deploy.py is
   forbidden; documented in CONFIG.md).
3. Layout-vs-host validation uses the `load_hosts` dict (not `get_host`) per
   O1 ("host inventory passed in (load_hosts result)"); `get_host` is still
   called per host inside the push loop and its refusal aborts before
   transport (tested).
