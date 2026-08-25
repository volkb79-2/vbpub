# CIU SPEC Reconciliation — 2026-08-24

## Status

This is a working document produced by auditing `docs/SPEC.md` (3065 lines) against live source (`src/ciu/`, ~17k lines), tests, and the dstdns adoption (`/workspaces/dstdns`). It identifies mismatches, missing spec coverage, dstdns schema gaps, and improvement opportunities for a potential major (v8) schema revision.

---

## 1. Identified SPEC ↔ Implementation Mismatches

These are places where the normative contract does not match what the shipped code actually does.

### 1a. Version header stale

SPEC says "CIU v5 Specification", version 5.0.0, dated 2026-08-12. Package is at v7.0.0; tags go through `ciu-v7.0.0`. The versioning rule ("wheel MAJOR tracks this SPEC's MAJOR") has been violated by 2 major releases without a corresponding SPEC MAJOR bump. Either renumber SPEC to 7.x and note the drift, or decouple the two versions.

**Recommendation:** decouple. CIU ships features faster than SPEC revisions land.

### 1b. S10 CLI surface incomplete

S10.1 lists verbs but omits `layouts`, `capabilities`, and `host-secrets` — all shipped in `cli.py`. S10.4 has the same gap.

**Fix:** add all three to both sections.

### 1c. S11 validation catalog omits several shipped checks

Missing entries for:
- S7.5c layout shape/environment/host/bundle validation
- S16.7 exec-target declaration shape
- S17.5 vendor_images type checks
- S13.2 provisioning ref grammar at render time for single-stack mode

**Impact:** a stack can declare `requires = ["bogus:nonsense"]` and pass `ciu up --dir infra/foo` because `validate_stack_provisioning()` is only called from deploy.py's preflight path, which runs for profile-mode up but NOT for single-stack `--dir` mode.

**Fix:** call `validate_stack_provisioning()` inside `engine.main_execution` after `validate_stack_shape`.

### 1d. S12 extension points outdated

Should also list: `governance.cpu_limit`, `deploy.layouts.*.hosts.*.environment_tag`, `ciu.instance.shared_infra.services[*].aliases`.

### 1e. ARCHITECTURE.md function names drifted

Lists `ksm.build_shim`/`verify_shim`; actual: `ksm.build`/`_verify`. Lists `transport_ssh.connect`; actual: `ssh_exec`. Lists `diagnose.diagnose`; actual: `collect`+`run`. Also `push_bundle` lives in activate.py not transport_ssh.py.

**Fix:** update the module-map table.

### 1f. FEATURES.md CLI table omits `layouts`, `capabilities`, `host-secrets`

Same gap as 1b at the docs level.

### 1g. CIU.md "Common public options" incomplete

Doesn't mention `--layout NAME`, `--thin`, `--bootstrap`, `--rollback`, `--host NAME`, `--json`, `--live`.

### 1h. CONFIG.md missing `[build]` documentation

dstdns declares `[build] python_version`, etc. Consumer-defined global metadata tables have no documented namespace convention.

**Recommendation for v8:** document an explicit `[meta]` or `[project]` namespace.

### 1i. CONFIG.md `[ciu]` table has dead keys

dstdns sets `repo_root`, `physical_repo_root`, `workspace_env_file`, `fail_fast` in `[ciu]`.
- `repo_root` / `physical_repo_root`: informational, read by templates.
- `workspace_env_file`: declared but CIU always uses hardcoded name. Dead key.
- `fail_fast`: dead key. No code reads it.

**Fix:** implement or mark as consumer-only informational.

### 1j. CONFIG.md `[ciu.ports]` undocumented

dstdns uses `ciu.ports.custom_http_port = 8080`. Not documented anywhere.

### 1k. Legacy doc disposition

| Document | Status | Recommendation |
|---|---|---|
| `docs/plans/V2-PACKETS.md` | Historical wave tracker, all DONE | Archive |
| `docs/MIGRATION-V2.md` | References ciu 2.x; we're on 7.x | Keep if v1 migration is still relevant |
| `docs/CIU-BUILD-PROPOSAL.md` | Already marked superseded | Delete |

---

## 2. dstdns Adoption Audit — Schema Gaps & Shortcomings

### 2a. Global config overloaded with app-domain data

Top-level tables like `[workflow]`, `[pubsub]`, `[load_control]`, `[authentik]`, `[auth]`, `[build]` carry application-domain facts consumed by stack templates alongside CIU's reserved tables. No formal boundary between infrastructure wiring and application data. A new CIU release adding a reserved key named `auth` would break dstdns.

**Proposed fix (v8):** introduce `[app]` root; all consumer-owned global tables must live under it.

### 2b. Service inventory duplicated between global `[service.*]` and per-stack config

Global carries ~40 service identity tables; per-stack files reference them via verbose Jinja paths. A typo silently renders empty.

**Proposed fix (v8):** move name/image/port into per-stack defaults directly. Use global registry only for cross-stack topology references.

### 2c. Topology mixes endpoint and route metadata

`[topology.services.*]` (internal host/port), `[topology.routes.*]` (URL path prefixes), `[topology.external]` (FQDN) serve different purposes but share one namespace. Multi-host override requires restating full entries.

**Proposed fix (v8):** split into `[endpoints.<name>]` + `[routes.<name>]`; allow field-level profile overrides.

### 2d. Provisioning graph cannot express one-shot completion cleanly

`stack:<name>:healthy` probes container_name `{project}-{env}-{stack_basename}`, which requires basename == container suffix (fragile). The exit_code==0 special case works only when no healthcheck exists. A healthcheck that passes before the container exits gives a false positive.

**Proposed fix (v8):** add `one_shot = true` on phase services. Health gate treats exit-0 as satisfied without Docker-health polling. Allow `requires = "stack:infra/db-init:completed"` using full stack path.

### 2e. Configfile fan-out duplicates per-instance sections

worker-io declares TWO separate configfile blocks for worker-io-1 and worker-io-2. CIU's `instances = N` fan-out would eliminate this, but compose services are ALSO generated via a manual Jinja loop. Using both mechanisms produces duplicate mounts.

**Proposed fix (v8):** make root-level `instances = N` drive BOTH configfile fan-out AND compose enumeration. Provide an instance-loop context so compose templates iterate automatically.

### 2f. Environment passthrough is ad-hoc

Templates inject `${CONTAINER_UID:-0}`, `${PHYSICAL_REPO_ROOT:?...}`, `${HOST_MDT_TMP}` directly from process env. No typed declaration of expected vars. Some templates use `:-` fallbacks (violating no-silent-fallbacks doctrine).

**Proposed fix (v8):** add `[<root>.<service>].env_required = [...]`. Validate before render. Auto-inject known machine identity keys into Jinja context as `{{ env.CONTAINER_UID }}` instead of raw Compose interpolation.

### 2g. Registry tables lack schema enforcement

`[registry.postgresql]`, `[registry.redis.users.*]`, etc. are free-form TOML consumed by hooks with no CIU-level validation. Typos surface only at hook runtime.

**Proposed fix (v8):** allow optional JSON Schema declaration for registry shapes, or provide validated Pydantic models to hooks.

### 2h. `ciu bake` doesn't respect selection model

`ciu bake [targets...]` passes targets straight to buildx. No `--profile` support. Meanwhile internal `action_build` DOES filter by selected stacks. Two build paths with different behavior.

**Proposed fix (v8):** unify: `ciu bake --profile core` builds only targets belonging to that selection.

### 2i. Health gate timeout is global, not per-service

Authentik needs ~240s; workers need ~5s. One timeout means slow services are excluded from gating or fast ones waste time.

**Proposed fix (v8):** allow per-service `health_timeout` override in phase entry.

### 2j. No `ciu status` verb

No single command shows "which stacks are running, healthy, what images" across a selection.

**Proposed addition:** read-only dashboard verb with optional JSON output.

### 2k. Worktree budget unknown to most users

Defaults to unlimited, not mentioned in README or CONSUMERS.md.

---

## 3. Improvement Ideas for Major Schema Revision (v8 candidate)

### Priority 1: Structural clarity

| Change | Effort |
|---|---|
| Introduce `[app]` root for consumer domain tables | Medium |
| Move service identity into per-stack config | High |
| Split topology into endpoints + routes | Low |

### Priority 2: Validation & fail-fast

| Change | Effort |
|---|---|
| Validate provisioning refs in single-stack mode | Low |
| Declare required env vars per service | Low |
| Optional JSON Schema for registry shapes | Medium |
| Implement or remove dead `[ciu]` keys | Low |

### Priority 3: Developer experience

| Change | Effort |
|---|---|
| Add `ciu status` verb | Medium |
| Unified `ciu bake` respecting profiles | Medium |
| Per-service health-timeout override | Low |
| Instance-aware configfile + compose fan-out | High |
| Document worktree budget | Low |

### Priority 4: Integration readiness

| Change | Effort |
|---|---|
| Stable JSON for status/check/health | Medium |
| Expose effective governance/config verb | Low |
| Plugin interface for external provisioners | High |

### Non-goals

- General-purpose config management
- More secret providers (use Vault transit)
- Non-Docker runtimes
- Web UI / REST API

---

## 4. Parallelizable Work Paths

| Path | Files touched | Depends on |
|---|---|---|
| Fix doc drift (S10/S11/function names/CLI table) | SPEC.md, FEATURES.md, ARCHITECTURE.md, CIU.md | none |
| Single-stack provisioning-ref validation | engine.py, tests | none |
| `[app]` namespace introduction | config_model.py, SPEC.md, migration guide | none (breaking) |
| `ciu status` verb | cli.py, new module | none |
| Per-service health timeout | deploy.py, health.py, SPEC.md | none |
| Env-required declarations | config_model.py, engine.py, SPEC.md | none |
| Unified bake | cli.py, deploy.py | none |
