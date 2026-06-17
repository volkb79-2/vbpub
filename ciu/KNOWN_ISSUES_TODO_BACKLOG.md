# CIU — Known Issues, TODO & Backlog

> **This is the canonical CIU issue tracker.** File CIU bugs and enhancements **here**, in
> the CIU product repo — not in consumer repos. Consumers (e.g. dstdns) that discover a CIU
> gap while building/operating a stack should report it here and keep only a pointer on their
> side. Each issue is fixed in this repo with **code + tests + spec + docs** in lockstep.
>
> Normative behaviour is defined in [`docs/SPEC.md`](docs/SPEC.md) (`S-xx` IDs). When an issue
> changes behaviour, the SPEC change is part of the fix, and the SPEC ID is cited below.

Last updated: 2026-06-17.

## How issues get here

Most of these were surfaced by **dstdns**, the first large CIU consumer, while running a
disposable-greenfield workflow (`ciu clean` → rebuild → `ciu up`, repeatedly). That workflow
exercises teardown/re-render far harder than a normal deploy and flushed out a family of
state-staleness bugs. The originating notes were captured verbatim and then distilled into the
structured issues below.

---

## Status board

| # | Title | Severity | Status |
|---|---|---|---|
| CIU-2 | configfile (S5) sections don't fan out across instance-indexed / replicated services | Low (ergonomic) | **FIXED** (see Resolution) |
| CIU-3 | `clean` leaves exited init/sidecar containers + their named volumes; teardown failures only WARNed | **High** — breaks disposable greenfield | **FIXED** (see Resolution) |
| CIU-4 | `post_compose` hooks run before container readiness (no health gate) | Med | **FIXED** (see Resolution) |
| CIU-5 | `bake` is production-image-only; no dev-loop build for contract-coupled (node/Vite) stacks | Med (UI DX) | **FIXED** (`ciu dev` verb; see Resolution) |
| CIU-6 | S4.20 leak/consumption scan flags secrets consumed via `ctx.secret_file()` / `secret()` as unused | Low | **FIXED** (see Resolution) |
| CIU-7 | per-verb `--help` prints the legacy `ciu-deploy` argparse, not the v3 verb's own options | Low (DX) | **FIXED** (see Resolution) |
| CIU-8 | `render` reuses a stale generated `ciu.toml.j2`; committed-defaults edits get silently shadowed | **High** — silent config staleness | **FIXED** (see Resolution) |

## Resolved / not-a-gap

| # | Title | Verdict |
|---|---|---|
| CIU-1 | "No config-file render+mount directive" | **NOT A GAP** — CIU S5 implements it; the consumer must *adopt* it, not request it. (An agent reading only the consumer repo cannot conclude a provider lacks a capability — check the provider SPEC/source first.) |

---

## CIU-8 — `render` reuses a stale `ciu.toml.j2`; committed-defaults edits get shadowed

**Severity:** High (silent config staleness). **Status:** FIXED.

**Mechanism.** `render_stack()` called `ensure_override_template()`, which **copied the full
`ciu.defaults.toml.j2` → `ciu.toml.j2`** on first run (when `ciu.toml.j2` was absent). That full
copy was then deep-merged *on top of* the freshly-rendered defaults as the override layer. Because
override wins in a deep merge, the stale full copy permanently **shadowed** any later edit to the
committed defaults. `clean`/`reset_service` removed the rendered `ciu.toml` but never the generated
`ciu.toml.j2`, so the staleness survived a "clean" too.

This was **asymmetric** with the global config, which already follows the right model (S3.1a): the
global override `ciu.global.toml.j2` is a *committed sparse* file that CIU **never auto-creates** —
if absent, defaults apply alone.

**Live repro.** Changing `bootstrap_token` to `GEN_TO_VAULT` in `ciu.defaults.toml.j2` had no
effect until the stale `infra/authentik/ciu.toml.j2` was deleted. A *new* key (`name=`) appeared to
work only because the stale copy lacked it — making the symptom partial and confusing.

**Fix (adopt the global sparse-override model for per-stack configs).** Per-stack now mirrors the
global chain exactly: render from `ciu.defaults.toml.j2` + an **optional committed sparse**
`ciu.toml.j2` override; **never auto-create or persist a generated full intermediate**. Nothing is
generated, so nothing can go stale and nothing for `clean` to miss. The per-stack override, like the
global one, is now (a) optional, (b) scanned for raw credentials before render (S3.1a), and (c)
git-committed (sparse) rather than gitignored-and-generated.

- **Code:** `config_model.py` — `render_stack()` no longer calls `ensure_override_template()`;
  `ensure_override_template()` is removed. The per-stack override is secret-scanned via
  `scan_override_for_secrets()` (same as global).
- **Spec:** S3.1 (per-stack override is committed sparse, not auto-created), S3.1a (constraints now
  apply to both global and per-stack overrides), S3.4 (no generated intermediate).
- **Docs:** `docs/CONFIG.md` file-roles table; README file hierarchy block.
- **Migration:** consumers delete any existing generated `ciu.toml.j2` and (optionally) re-create it
  as a hand-written sparse override containing only keys that differ from defaults.
- **Tests:** `test_ciu_config_model.py` — an edit to defaults is reflected without deleting any
  file; a present sparse override is applied; no `ciu.toml.j2` is created when absent.

---

## CIU-3 — `clean` leaves exited init/sidecar containers + their volumes

**Severity:** High (breaks disposable greenfield). **Status:** FIXED.

**Mechanism.** Two compounding defects:
1. `deploy._matching_containers()` listed **running** containers only (`docker ps`, no `-a`), so an
   exited one-shot init/sidecar (e.g. `*-vault-init`, `Exited (0)`) was invisible to clean's
   container sweep. Docker refuses to remove a volume referenced by *any* container — even a stopped
   one — so the init sidecar pinned its named volumes through teardown.
2. Both `engine.reset_service`'s `docker compose down -v` and `deploy._remove_project_volumes`'s
   `docker volume rm` sweep only **WARNed** on failure. So a teardown that left a container behind
   left the volume behind *silently*.

**Live repro.** `infra/vault` declares `vault` + a one-shot `vault-init` sidecar, both mounting plain
named volumes `vault-data`/`vault-logs`. After `ciu clean` the exited `vault-init` survived; the
volume sweep hit "volume is in use" and only WARNed; the volumes persisted. On the next deploy Vault
held a **stale `consul/mgmt/token`** while Consul came up fresh → the consul hook got
`403 ACL system must be bootstrapped`. The same incompleteness later bit Postgres hostdir data.

> **Downstream lesson for consumers (informative):** a `post_compose` hook that recovers a token
> from a provider (Vault) and skips bootstrap purely because "a token exists" is fragile against
> *any* state asymmetry. Robust hooks validate the recovered token against the live service and
> re-bootstrap on rejection. CIU's job is to make `clean` complete so the asymmetry never arises;
> the hook hardening is the consumer's belt-and-braces. (dstdns hardened its consul hook in
> `cd5f229`.)

**Fix (CIU).**
- `reset_service`: tear down with `docker compose down -v --remove-orphans`; **before** the volume
  step, remove **all** project-labeled containers regardless of state (`docker ps -a` + `rm -f`),
  including exited init/sidecars; then **verify** no project volumes remain and **error** (not WARN)
  if they do.
- `_matching_containers`: add `-a` so the container sweep sees exited containers; callers that only
  want running containers (`--stop`) filter by state explicitly.
- `_remove_project_volumes`: after `docker volume rm`, re-list and **fail** if any project volume
  survives, naming the survivors and the likely cause (a container still referencing them).
- **Spec:** S6.4 (reset removes containers of any state + `--remove-orphans`; the post-clean
  invariant is normative: zero project containers AND zero project volumes remain).
- **Tests:** `test_ciu_reset_service.py` / `test_ciu_deploy_actions.py` — the post-clean invariant
  (exited init sidecar + its volumes are gone); `down` uses `--remove-orphans`; a surviving volume
  is an error, not a warning.

---

## CIU-4 — `post_compose` hooks run before container readiness

**Severity:** Med. **Status:** FIXED.

**Mechanism.** The pipeline runs `docker compose up -d` (S8.3 step 16) then **immediately** runs
`post_compose` hooks (step 17) with no wait for container health. Service-touching hooks therefore
race startup: a redis ACL hook hit `Could not connect to Redis ... Connection refused` because
`redis-server` had not yet bound its port. Every hook had to hand-roll readiness polling
(`post_compose_consul.py` polls `/v1/status/leader`; redis needed a PING-until-ready loop).

**Fix (CIU).** Give hooks a first-class readiness API instead of each reinventing it: extend
`HookContext` (S9.3) with `ctx.wait_healthy(service, timeout=...)` (polls the container's Docker
health via the existing `health.classify`/`evaluate_gate`) and `ctx.wait_tcp(host, port, timeout=...)`
(a dependency-free port probe for images without a healthcheck). The engine wires both. Hooks call
`ctx.wait_healthy("redis-core")` at the top instead of re-implementing a poll loop.

- **Code:** `hooks_runner.py` (`HookContext` gains `wait_healthy`/`wait_tcp` callables);
  `engine.py` wires them with the stack's project/env name resolver; `deploy_pkg/health.py` reused.
- **Spec:** S9.3 (readiness helpers on `ctx`), S8.3 step 17 note (hooks own their readiness via the
  provided helpers; CIU does not implicitly block the whole step on a global gate).
- **Docs:** `docs/CIU.md` hooks section + `src/ciu/hooks/examples/README.md` context table.
- **Tests:** `test_hook_interfaces.py` — `ctx.wait_tcp` returns on an open port and times out on a
  closed one (deterministic, injected clock/socket); `ctx.wait_healthy` honours classify results.

---

## CIU-5 — `bake` is production-image-only; no dev-loop build

**Severity:** Med (UI DX; also the test-container enabler). **Status:** FIXED.

**Context.** `ciu bake` builds the **production** image. For a contract-coupled UI stack
(`webapp-ui-react`) the dev loop is a *different* chain: `npm run fetch:openapi` (pull the live
backend's OpenAPI) → `npm run gen:api` (openapi-typescript codegen) → `vite dev` (HMR) — a multi-step
pre-build with a dependency on a *running* service, none of which `bake` models. A UI developer wants
sub-second HMR against the live API, not a multi-minute image rebuild per keystroke.

**Fix (CIU) — a generic, build-tool-agnostic `ciu dev <stack>` verb.** A `[<root>.dev]` section in
the stack's `ciu.defaults.toml.j2` declares the dev loop declaratively:

```toml
[webapp_ui.dev]
prebuild = ["npm run fetch:openapi", "npm run gen:api"]   # optional, ordered; may depend on a live service
command  = "npm run dev"                                    # the long-running dev server
port     = 5173                                              # HMR port to expose
mount    = ["./:/app", "/app/node_modules"]                 # source bind + anon node_modules
depends_on = ["webapp-server"]                              # wait_healthy before prebuild (reuses CIU-4)
```

`ciu dev <stack>` renders the stack config, runs `prebuild` steps in order (gating on `depends_on`
health via the CIU-4 helpers), then runs `command` with the source bind-mounted and `port` exposed.
No npm/Vite-specific logic lives in CIU — it works for any dev-server stack (Vite/Next/`uvicorn
--reload`) and composes with CIU's existing render model.

> **Why this matters beyond UI:** the same verb is how a project runs its **test container** — a
> `[<root>.dev]`/`[<root>.test]` profile that builds the app image + test extras and runs the suite
> *inside a container* whose dependency closure equals the runtime closure. This closes the
> "green test in the devcontainer, crash in prod" class (see the consumer's container doctrine).

- **Code:** `config_model` (`[<root>.dev]` shape validation), `cli.py` (`dev` verb), a small
  `dev` runner (render → prebuild → compose-run an ephemeral dev service).
- **Spec:** new S5a (dev-loop profile) + S10.1 (`ciu dev`).
- **Docs:** `docs/CIU.md` dev-loop section; supersedes the speculative `docs/CIU-BUILD-PROPOSAL.md`.
- **Tests:** `test_ciu_dev_profile.py` — `[<root>.dev]` parse/validate; prebuild ordering; missing
  `command` aborts.

---

## CIU-6 — leak/consumption scan flags `secret_file()`/`secret()` consumption as unused

**Severity:** Low. **Status:** FIXED.

**Mechanism.** `validate_consumption()` (S4.20) only counts a secret as "consumed" when a compose
service lists it under `services.<svc>.secrets:`. A secret consumed via `ctx.secret_file(name)` in a
hook, or via `secret('<name>')` in a configfile template (S5.4), is invisible to that scan — so CIU
warned `declared secret 'redis_password' is consumed by no service` for a secret the redis hook
genuinely uses.

**Fix (CIU).** Broaden the consumption check to also count:
1. secrets referenced by `secret('<name>')` in any rendered configfile (S5), and
2. secrets a hook may read via `ctx.secret_file()` — surfaced by an opt-in
   `[<root>.secrets.<name>]` marker `consumed_by = "hook"` (explicit, auditable) so the scan
   recognises hook consumption without guessing.

Anything still consumed by nothing keeps the (correct) S4.20 warning. The undeclared-reference case
(a service references a secret that was never declared) remains a hard abort.

- **Code:** `render_configfiles()` records `secret()` calls on each configfile mount;
  `composefile.validate_consumption()` accepts configfile mounts + hook-consumed names and unions all
  three consumption channels before computing the "unused" set; `SecretSpec` accepts
  `consumed_by = "hook"`.
- **Spec:** S4.20 (consumption channels enumerated: compose `secrets:`, configfile `secret()`,
  hook `consumed_by = "hook"`).
- **Tests:** `test_ciu_composefile.py` — a configfile-only secret is not flagged; a
  `consumed_by="hook"` secret is not flagged; a genuinely-unused secret still warns.

---

## CIU-7 — per-verb `--help` prints the legacy `ciu-deploy` argparse

**Severity:** Low (DX). **Status:** FIXED.

**Mechanism.** `ciu clean --help` / `ciu up --help` forwarded `--help` straight to `deploy.main`'s
argparse, which printed the whole legacy `ciu-deploy` flag set (`--deploy/--clean/--profile/-y/...`)
instead of the v3 verb's own options — so discovering a verb's flags (e.g. `-y` on `clean`) was
non-obvious, and the legacy surface leaked into the v3 CLI.

**Fix (CIU).** `cli.py` intercepts `-h`/`--help` per verb and prints a focused, verb-scoped usage
block (synopsis + only that verb's options + examples) before any forwarding. The legacy argparse is
no longer reachable from the v3 verbs.

- **Code:** `cli.py` — a per-verb help table; `_verb_help(verb)` printed on `-h`/`--help`.
- **Spec:** S10.1 (per-verb help is verb-scoped, not the legacy orchestrator help).
- **Tests:** `test_ciu_cli_parser.py` — `ciu clean --help` shows clean's options and not
  `--deploy`; every verb has a help entry; exit 0.

---

## CIU-2 — configfile (S5) sections don't fan out across instance-indexed services

**Severity:** Low (ergonomic). **Status:** FIXED.

**Mechanism (verified in `composefile.render_configfiles`).** A `[<root>.<service>.configfile.<name>]`
section mounts only to a compose service whose key matches `<service>` **exactly**. For a service
deployed as N instances (compose keys `svc-1`, `svc-2`, … from an `{{ name }}-{{ instance_index }}`
loop), there is no auto-expansion: a single base-name section mounts to a *phantom* service and the
real containers get no file. You must declare one quoted per-instance section per replica
(`["root"."svc-1".configfile.app]`, `["root"."svc-2"....]`, …) — fragile (adding a replica needs a
new section) and verbose.

**dstdns workaround (previously in place):** worker-io and worker-db each declared 2 quoted
per-instance sections; webapp-server (single instance) declared one quoted hyphenated section.

**Fix (CIU).** A configfile section may now target a **base** service name. During overlay
generation, CIU reads the rendered compose service keys: an exact key match still wins, preserving
the single-service behavior; otherwise `[<root>.svc.configfile.main]` fans out to every
instance-indexed key named `svc-1`, `svc-2`, ... . This keeps the source config declaration stable
when replicas are added or removed.

- **Code:** `composefile.generate_overlay()` expands configfile mounts using the rendered compose
  service keys; `engine.py` passes the rendered compose text into overlay generation.
- **Spec:** S5.3 defines exact-match precedence and `<service>-<positive-int>` fan-out.
- **Docs:** `docs/CIU.md` configfile section notes replicated service fan-out.
- **Tests:** `test_ciu_composefile.py` covers fan-out and exact-match precedence.
