# v8-dstdns-demo — dstdns recreated in CIU v8 notation

This directory is a **worked example**, not a deployment: dstdns's real configuration (25 stacks, the global config, the host inventory, the gate) rewritten in the v8 notation of `../SPEC-V8.md` (normative, draft.2) and `../CIU-V8-TESTING-GATE-PROPOSAL.md` (rationale). Every file renders with a permissive Jinja context and parses as TOML/YAML (`validate_demo.py` in the design session's scratchpad; 63 files). Hook scripts and application config templates (`config.toml.j2`, `nginx.conf.j2`, …) are referenced by name and not copied — they need the re-rooting listed under "Migration notes".

## Layout of this directory

| path | what it shows |
|---|---|
| `ciu.global.defaults.toml.j2` | the whole global model: `[deploy]`, `[service.*]` (WHAT), `[realization]` (HOW), `[network.*]`/`[deploy.profiles]`/`[deploy.layouts.*]` (WHERE), `[testing.*]` (the gate), realness defaults, vault pointer, registry, governance |
| `ciu.global.toml.j2` | the committed sparse override (landscape id, auth, governance for this host) |
| `ciu.global.instance.toml.j2` | the primary instance's hand-edited OVERLAY: layout/bundles/label plus a commented `[ciu.instance.host_ports]` override example (S14.2) |
| `ciu.instance.generated.toml` | the CIU-owned GENERATED file merged after the overlay: `[ciu.instance.generated]` (identity), `[ciu.host.generated]` (host facts), `[ciu.instance.build]`, `[ciu.instance.realness.<layout>]` (S14.2) |
| `ciu.hosts.toml` | four netcup hosts from `hosts-avail.md` + `localhost`, SSH facts, per-network addresses (`mesh`, `public`), activate commands (`ciu instance init --host <h> && ciu check --layout prod3`), the host-scoped TLS secrets |
| `ciu.secrets.toml.example` | the one materialized-secrets store (shape only) |
| `assay.toml` | four of dstdns's assay lanes: three with the two v8-facing changes (`derived:` paths → `ciu.instance.resolved.*`; `judge.base_source = "request"`), plus `ui_unit` — a language-bound `javascript` lane whose dependency closure comes from the tester environment's offline npm cache (CIU-73 / assay B041) |
| `examples/ciu.global.instance.joined.toml.j2` + `examples/ciu.instance.generated.joined.toml` | a package worktree joining the primary's vault/consul/redis/db/identity/observability (`joined` realizations): its overlay and its generated file |
| `examples/ciu.global.toml.rendered.example` | an excerpt of the DERIVED tables: identities, endpoints with derived publication, placement, routes (prod3 on rs1002 and local), waves, gates, edges, the closing `render` table |
| `infra/*`, `applications/*`, `infra-global/*`, `tools/test-runner` | every stack: `ciu.defaults.toml.j2` (services under `[ciu_stack.<svc>]`) + `ciu.compose.yml.j2` (what ciu does NOT inject) |
| `infra/db-core-seeded` | a NEW stack: the `owned-seeded` realization of `main_db`/`app_schema` |

## The four layouts

`local` (everything on the devcontainer), `prod3` (gstammtisch: mesh/core/db/identity/worker-db · rs1002: mesh/apps/worker-io/observability · tsstammtisch: mesh/edge/global-services, public ingress), `staging2` (two of the same hosts, different bundles), and an `mtls-public` sketch (an `address` network with `tls = "mtls"` and a pki provider; nothing changes in any stack file). Cross-host reachability is derived: `PGHOST={{ routes.main_db.sql.host }}` in the controller template reads a container name on `local` and a tailscale address on `prod3`.

## What v8 derives that dstdns typed by hand (counted in the v7 sources)

container/host names (70/68 template references), the `app_identity.*` registry (~45 tables + 41 inlined copies), `[topology.services.*]` routes (12), `[deploy.phases]` ordering (9 phases), the `stack:infra/vault:healthy` self-declarations (4), the 30 spellings of the assay version, replica services `worker-io-1/-2` and `worker-db-1/-2`, secrets stanzas, `depends_on` conditions, `ports:` lines, labels, networks, cross-host port publication.

## Decisions taken while writing this demo (for review)

Design-level (also recorded in the proposal §4.3 and the spec):
- **D1** Render-context bindings: `ciu_stack` (own services, with `identity`/`health` merged), `routes`, `realization` (merged view), `instance` (identity + host facts), `stack_dir`; stack TOML files may use `instance`/`routes` but not `ciu_stack` (their own tables do not exist yet).
- **D2** ciu **injects** `container_name`, `hostname`, `networks`, base labels, `secrets`, `depends_on`, `ports`, config-file mounts and cgroup fields into the rendered compose; templates that write them are refused (equal `container_name`/`hostname` tolerated). Labels: a template label whose key is one of ciu's own (`project`, `instance`, `realization`, `service`, `replica`, `managed-by`) is an error (S4.5.2) — the v7 `managed-by=orchestrator` and per-replica `instance=<n>` labels are gone. This is why the compose templates here are ~40 % shorter than the originals.
- **D3** `deploy.registry.namespace` is a real key again: project-built images are `{{ deploy.registry.namespace }}/<name>:<tag>`.
- **D4** `deploy.health` is merged into every service's `health` table before render, so healthcheck stanzas need no conditionals.
- **D5** Identity elision: when a service key equals its realization name the service part is omitted (`dstdns-98535c-controller`, not `…-controller-controller`).
- **D6** One default realness level (`[deploy.realness] default = "live"`) plus per-service pins; the internal/third-party split was dropped as a second key set that bought nothing.
- **D7** A variant may name which service of a multi-service stack carries the capability — the **variant service** (`object_store.live = { realized_by = "db_core", service = "minio" }`): its health, endpoints, image and exec target stand for the capability, and the selection is recorded as `service` in `[ciu.instance.resolved.services.<x>]`; the stack's `primary = true` is the fallback.
- **D8** `GEN_TO_VAULT:<path>` derives `vault:secret/<path>`; hook-minted facts live in `[hooks.provides] <svc> = [...]`, keyed by the service they are probed in (S8.6.3); `ASK_VAULT` paths derive an edge to their minter.
- **D9** `delivery` ∈ `file | env | configfile | native | none`, mandatory; stack-level shared secrets under `[ciu_stack.secrets.<key>]`; host-scoped secrets consumed through the `ASK_HOST:<entry>` directive (D14).
- **D10** Endpoint names are unique per stack; fronted endpoints are `publish = "proxy"` with a `host_port` (the proxy hop is host-published when the proxy runs elsewhere) and an `allow_from` scoping the hosts that may reach them.
- **D11** `per_host = true` realizations (`tailscale_node`): deployed on every host of a layout that reaches them, the same identity on each, never a route target; network readiness and pki edges are evaluated per host (S7.6.5).
- **D12** `uses = [...]` declares a runtime-only dependency: the route is derived, no ordering edge (S7.8.3). A template may reference `routes.X.*` only when X is in `init_requires` or `uses`: `tracing` for controller/worker-io/worker-db/webapp-server/otel-aggregator, `controller` for worker-db/webapp-server/test-runner, `identity_provider` + `otel_collector` for webapp-server, `otel_aggregator` for the node collector, `otel_collector` for docker-stats-exporter, `webapp_ui` for authentik, the four fronted backends for the reverse proxy, `webapp_server`/`browser_service` for test-runner. A name in both lists is an error; a `uses` target must be selected by the bundles (S6.4.1), which is why the joined worktree example also joins `identity_provider`/`tracing`/`otel_collector`.
- **D13** The overlay/generated split (S14.2): `ciu.global.instance.toml.j2` is hand-edited only (layout, bundles, label, host-port overrides, joins); `ciu.instance.generated.toml` is CIU-owned plain TOML merged after it (identity, host facts, build facts, the `[ciu.instance.realness.<layout>]` records). No CIU-rewritten table ever sits in a hand-edited file.
- **D14** `ASK_HOST:<entry>` (S10.1/S10.4): the reverse proxy consumes the edge host's `tls_cert_pem`/`tls_key_pem` as file-delivered `tls_cert`/`tls_key` (`/run/secrets/tls_*`); every host a layout places the stack on must declare both entries (S10.1.3), so `localhost` carries an `ASK_FILE` dev pair for the `local` layout. `[network.public]` therefore has no `tls`: network-level `tls`/`mtls` means ciu-managed service TLS (S10.5), not proxy termination.
- **D15** DNS-safe identities (S4.2.1): `_` in realization/service names maps to `-` in every derived string — `dstdns-98535c-db-core-postgres`, compose key `db-core-postgres`, compose project `dstdns-98535c-db-core`, replicas `dstdns-98535c-worker-io-worker-1`. The TOML names stay `db_core`, `worker_io`.
- **D16** Derived cross-host publication (S7.4.1): an instance-only endpoint is published on the provider host — bound to that host's address on network N — only when some cross-host route reaches it over N (`published_on = ["mesh"]` in the rendered file); `publish = "host"` is always published; a `publish = "proxy"` endpoint is reached through the proxy network by consumers and through the derived publication by the proxy itself. `allow_from` must admit every consumer host of such a route: controller admits gstammtisch (worker-db), authentik admits rs1002 (webapp-server's JWKS), webapp-ui admits gstammtisch (authentik's redirect origins).

Gone with draft.2 (each file says so where it used to be): `[ciu.instances] exec_targets` (`ciu instance exec --env tester` runs in the gate environment), `[state]` tables in stack files (state lives in `<location>/ciu.state.toml`), `required_env` on services (an env-delivered secret's `env_name` guarantees presence; `${VAR:?…}`/`${VAR:-…}` forms are refused, S11.3), `[testing.judge] require_provenance` (always required), `[network.<n>] kind = "mesh"/"public"` (now `kind = "address"` + `description`).

Consumer-level (what the converters had to choose; each is commented in the file):
- consul: `init_requires = ["vault"]` because its hook writes Vault with the bootstrap token and no directive derives that edge; endpoints reduced to `http`/`grpc`(8300, Consul's server RPC)/`dns`; host publishing left to `ciu.toml.j2` overrides.
- db-core: `postgres` is primary; `minio`/`pgadmin` carry `object_store`/`db_admin` through the variants' `service`; the seven entrypoint-consumed per-app passwords stay `env`, the six MinIO IAM keys and `workerdb_ddl` are `none` (+ `consumed_by = "hook"`); `CLEAN_DATA_DIR` became `[maintenance] clean_data_dirs`; pgAdmin's server JSON becomes a configfile because `"Host": "postgres"` no longer resolves with qualified compose keys; the `minio:user/*` facts sit under `[hooks.provides] minio`.
- db-core-seeded: pg facts by construction; the Vault facts through a hook that writes the baked credentials (`[hooks.provides] postgres`).
- authentik: shared secrets at stack level; hostdirs per service; stack `[governance]` replaces per-service caps; public origin from `network.edge.fqdn`; `uses = ["webapp_ui"]` for the in-instance redirect origin.
- tailscale: `per_host = true`, `host_network = true`; `TS_HOSTNAME` from `${HOSTNAME}` interpolation; no labels of its own.
- pwmcp: `aliases = ["pwmcp", "pwmcp-mcp", "pwmcp-playwright"]` replace the v7 consumer-alias mechanism; Traefik labels/ingress dropped (public exposure is `publish = "proxy"`; auth guards are the proxy stack's data).
- otel: config YAMLs are `configfile` entries instead of `${PHYSICAL_REPO_ROOT}` binds; collector→aggregator and aggregator→OAP are `uses` (routes, no edges).
- docker-stats-exporter: explicit `health` endpoint; plain `${VAR}` for the env-delivered consul token (the v7 fail-fast form is refused; the secret's `env_name` guarantees presence); `consul.kv_path` declared (no derived identity yields the hyphenated policy spelling).
- webapp-server / ddcli / webapp-ui: `*_FILE` env pointers for file-delivered secrets; OIDC issuer from `routes.identity_provider.http.url` + `authentik.oidc_slug` (closes the P104 "third copy of dstdns-ui").
- worker-io / worker-db: one service with `instances = 2`, compose block per replica via `identity.replicas`; the consumer table `[identity] geo_zone` renamed `vantage` (`identity` is ciu-owned); the per-replica label is dropped (ciu stamps `<prefix>.replica`).
- skywalking: `banyandb` kept as `enabled = false` (retired in the source); `oap` primary; `[oap.health]` renamed `health_checker`.
- reverse-proxy: owns the host-published `https` endpoint and terminates TLS with the host's `ASK_HOST` certificate (the v7 traefik tls-edge is gone from this demo — a shape change, flagged in the file); backends are ordinary `uses` routes.
- registry-lightweight: `tls_proxy` on host port 5443 (the source used 5000/5001 — a demo choice); github-runner-webhook on host port 9001 (both webhook stacks sit in `global-services`).
- mock-targets: `dns` primary; compose `build:` kept (allowed); facts in a consumer table `[mock_image]`.
- test-runner: `RUN_LIVE_TESTS`/`MOCK_MODE` are no longer container env — the gate hands them to the lane process (`forward_env` on the environment, or the lane's own argv).

## Known deviations and consumer-side findings

- `pg:role/workerdb_ddl` is in `main_db`'s contract but `01-init-users.sh` never creates it (v7 data oddity, preserved).
- Consul's hook mints `consul/cmru/controller/token`, which no contract claims (would be an unclaimed-fact WARN if declared).
- `tools/admin-debug` was not converted (in no bundle; `ddcli` covers the admin use).
- Hook scripts read identities and routes from the hook context in v8; the v7 scripts that read `config["<root>"]` or `topology.external.public_fqdn` need the same re-rooting as the templates.
- Spec reading: S7.8 step 5 gives proxy-network routes `url = "https://<fqdn><path>"` while step 7 says the path is never appended; the rendered example follows step 5 for proxy routes (the path is what the proxy routes by) and step 7 for direct routes.

## Migration notes for the files not copied

`config.toml.j2` (controller, webapp-server, worker-io, worker-db, ddcli, docker-stats-exporter): root `webapp_server.*` → `ciu_stack.server.*`; `deploy.environment_tag` → `instance.id`; `app_identity.*`/`topology.*` → `routes.*` (every `routes.X` the file reads must be in the service's `init_requires` or `uses`); every `secret()` call requires `delivery = "configfile"` on that secret. `nginx.conf.j2`/`nginx-tls.conf.j2`: upstreams from `routes.<logical>.http.host/port/path`; listen ports from the endpoints; `ssl_certificate`/`ssl_certificate_key` from `/run/secrets/tls_cert`/`tls_key`.
