## 1 Verdict

The model is not implementable as written. Its useful center is recognizable: declarations are separated from resolutions, service addresses are derived from consumer bindings, realness is explicit, and the gate is intended to produce durable evidence. But draft.3 contains eleven direct contradictions or missing data paths at the level where two conforming implementations would necessarily do different things. The supplied demo also cannot pass the specified checker.

The most important defects are these:

1. The identity rules require a Compose project name to be both shared by all services of a Realization and unique for every service (T-01).
2. The dependency model loses information twice: a network names a Realization but asks for an unspecified variant service, and `wait = "started"` is nevertheless gated as healthy (T-04, T-05).
3. Durable state is stored entirely in ignored checkout files, while the spec promises deterministic recovery after `git clean -x`; the inputs needed for that recovery have been deleted (T-11). The registry and lock design compounds this for moved worktrees and joined instances (T-18, T-19).
4. The test-gate lift does not preserve the behavior it claims to lift: the assay command is syntactically incomplete, the allowed judge can lack the queried API, container jobs regress to `--rm`, and the demo requests lane limits above its execution container's limit (T-08, T-12, T-20).
5. Multi-host deployment is neither transactional nor evidence-bearing. Cross-host facts are reduced to port reachability, secret transport has no coherent receiving mode, and `rsync` writes directly into `current` while the advertised rollback is merely `ciu down` (T-09, T-16, T-25).

I would first adopt three changes. First, put stable instance identity, registry records, leases, port reservations, and lock objects in a durable host-local registry keyed by a random instance UUID; keep short hashes only as display suffixes. Second, make push/activation a content-addressed transaction: stage a manifest-verified release directory, switch an atomic `current` link, and record a digest-bound activation receipt containing the services, endpoints, facts, and probes that passed. Third, make bindings a filesystem projection resembling the Service Binding specification, with environment variables and template data as derived compatibility views. That gives secret rotation an atomic directory boundary and removes much of the endpoint/credential special-casing.

The design should not proceed to implementation from draft.3. Fix the blockers, make the full demo a machine-executable conformance fixture, and require that fixture plus the standalone zero-instance fixture to pass before freezing draft.4.

## 2 Findings

### T-01 — Compose-project uniqueness contradicts the identity derivation

**Severity:** BLOCKER

**Where:** S4.2.1, S4.3.1; demo `infra/db-core/ciu.stack.toml:21-136`

**Claim:** A multi-service Realization cannot satisfy both identity rules.

**Evidence:** S4.2.1 derives one `compose_project = "{P}-{I}-{R'}"`; therefore every service in Realization `R` has the same Compose project. S4.3.1 then requires “every `compose_project` … unique within the deploy set on one host.” The demo makes the conflict ordinary: `db_core` has seven enabled services but one Compose project; `vault`, `consul_server`, `authentik`, `mock_targets`, `skywalking`, `registry_lightweight`, and `github_runner` also have multiple enabled services.

**Failure scenario:** A literal validator rejects every one of those stacks for duplicate `compose_project`. A validator that deduplicates equal values silently implements a different rule.

**Proposed fix:** Replace the relevant S4.3.1 sentence with: “For each host, `compose_project` MUST be unique among selected Realizations. Within a Realization all services and replicas MUST have that Realization's one `compose_project`. `compose_key`, `container_name`, `hostname`, and every injected network alias MUST be collision-free in their respective namespaces.” Add separate checks for each namespace.

**Challenges a settled decision:** No. It preserves the settled derivation and corrects the validator.

### T-02 — The declared FQDN type rejects every real FQDN in the demo

**Severity:** BLOCKER

**Where:** S1.4, S7.2, S7.3; demo `ciu.toml:230`, `ciu.hosts.toml:12,18,37,52,70`

**Claim:** `fqdn` is typed with a grammar that forbids dots.

**Evidence:** S1.4 defines `hostname` as a single DNS label (`^[a-z0-9]...$`, with no `.`). S7.2 and S7.3 type host and proxy-network `fqdn` as `hostname`. The demo supplies `gstammtisch.dchive.de`, `rs1002.dchive.de`, and similar multi-label names.

**Failure scenario:** Stage 3 rejects the provided demo. Relaxing the regex ad hoc would also accept malformed names unless every implementation invents the same replacement grammar.

**Proposed fix:** Add a distinct scalar type `dns_name`: one or more RFC 1123 labels separated by dots, maximum 253 bytes, no trailing dot in canonical stored form. Keep `hostname` as one label. Change both `fqdn` keys to `dns_name` and add positive/negative schema fixtures.

**Challenges a settled decision:** No.

### T-03 — Structured hook entries use a key the closed schema omits

**Severity:** BLOCKER

**Where:** S3.8.1, S6.10, S12.1, Appendix D migration item 3; demo `infra/db-core/ciu.stack.toml:135`

**Claim:** The only syntax capable of assigning hook facts to a non-primary service is forbidden by the hook schema.

**Evidence:** S6.10 says an entry is a string or `{ run, provides }`, then says `service` is optional. Under S3.8.1, unlisted semantic keys are errors. Appendix D explicitly migrates old hooks to entries “with `service`,” and the demo uses `{ run = "...", service = "minio", provides = [...] }`.

**Failure scenario:** A conforming parser rejects the demo's MinIO fact-producing hook. If it ignores `service`, facts are attributed to the primary Postgres service and probes execute in the wrong container.

**Proposed fix:** Define the exact object as `{ run: path, service?: service_key, provides?: [TypedFact] }`; default `service` to the unique primary; require it if the stack has no primary; validate that it names an enabled service for the selected variant.

**Challenges a settled decision:** No. It makes the settled subprocess-hook shape coherent.

### T-04 — The resolver is not total for network providers or selected mock targets

**Severity:** BLOCKER

**Where:** S3.5.4, S6.4.5, S7.3, S7.3.2, S7.6.5, S7.8.4, S8.2-S8.3, S9.3.4; demo `ciu.toml:92-103,215-230`

**Claim:** Two legal selections erase target information that downstream readiness or data delivery requires.

**Evidence:** Variant services belong to LogicalService selections under S5.2/S9.3.3, not to a Realization in isolation. One Realization may back several LogicalServices through different services; the demo's `db_core` backs `main_db` with `postgres`, `object_store` with `minio`, and `db_admin` with `pgadmin`. S7.3 nevertheless stores only a Realization name and S7.3.2 asks for its singular variant service. S7.6.5 forbids data bindings to a `per_host` capability but permits endpoint-less bindings; S8.2 then points at a Realization that has one copy per host without selecting the consumer-local or provider-host copy. Separately, S9.3.4 says any binding to a capability selected as `mock = {}` yields no edge **and no resolution**. S3.5.4/S6.4.5 still require template/env deliveries to have data.

**Failure scenario:** If `network.mesh.realized_by` names a multi-service Realization, one implementation waits for its primary, another for every service, and another refuses it. An endpoint-less `requires = ["mesh_node"]` can wait for the local Tailscale copy, all copies, or an arbitrary copy. Separately, select `mock` for a database capability consumed through `delivery = "env"`: the consumer's required `*_HOST`/`*_PORT` variables have no values, yet S9.3.4 tells the resolver to return nothing rather than refuse. These are admitted inputs with no unique downstream value.

**Proposed fix:** Make network `realized_by` name a LogicalService and resolve its selected variant service, or require `{ realization, service }`. Define an endpoint-less binding to a `per_host` service as targeting the copy on `host(consumer)` and refuse it if that copy is not placed; write the host-qualified predicate into the graph/gate. For `mock = {}`, stage 5 MUST reject every enabled binding that requests endpoint/fact data or `env`/`template` delivery, naming the layout and consumer; allow only endpoint-less ordering bindings that explicitly declare `optional = true`, which resolve to a typed `absent` object and add no edge. A richer mock must name an actual Realization rather than `{}`.

**Challenges a settled decision:** Yes — it narrows the settled generic network entity and the settled empty-mock behavior so both remain mechanically derivable.

### T-05 — `wait = "started"` is defined as Running and executed as healthy

**Severity:** BLOCKER

**Where:** S6.4, S8.2, S8.5.1, S8.6.3

**Claim:** The wave gate erases the public distinction between `started` and `healthy`.

**Evidence:** S6.4 and S8.2 say `wait = "started"` creates an edge satisfied when the provider is Running, while `healthy` requires the health predicate. S8.5.1 instead waits for every provider with an incoming edge from a later wave to be healthy. No exception is made for a `started` edge.

**Failure scenario:** A provider is Running but its health check deliberately remains `starting` during cache warming. A `started` consumer should launch, but the wave stalls until healthy or times out. If an implementer follows S8.2 instead, the same declaration behaves differently.

**Proposed fix:** Write the gate as a set of predicates derived per outgoing edge: `started` → container Running, `healthy` → S8.6.3, one-shot dependency → successful completion, fact requirement → successful fact probe, `none` → no graph edge or gate. Deduplicate predicates only after retaining the strongest requested predicate for each provider/consumer boundary.

**Challenges a settled decision:** No. It preserves the closed `wait` vocabulary.

### T-06 — `ciu init` is specified to create an invalid project

**Severity:** BLOCKER

**Where:** S16.11.1, S19.1-S19.2; demo `examples/minimal/ciu.toml:1-32`

**Claim:** Ordinary initialization writes instance-only tables without a Realization, which is exactly the invalid zero-instance shape.

**Evidence:** S19.1 says `ciu init` writes explicit `[realness]`, `[layouts]`, and `[bundles]`. S16.11.1 says a declaration with no `[realization]` is zero-instance and those three tables are errors. S19.1 also says `--stack DIR` creates the minimal stack, but without `--from-compose` it has no authoritative image, build context, service name, ports, or health command from which to create the demo's Nginx files.

**Failure scenario:** `ciu init` followed by the prescribed `ciu check` rejects its own output. `ciu init --stack web` must either invent `nginx:1.27` and port 8080 or write an incomplete stack; neither behavior is specified.

**Proposed fix:** Make bare `ciu init` emit only `[project]` and a minimal `[testing]` skeleton (or only `[project]` if no lane was requested). Require either `--from-compose PATH` or explicit `--image IMAGE --service NAME` for `--stack`; only then emit Realization, service, bundle, layout, host inventory, and templates. State that every emitted scaffold must pass `ciu check` before returning 0.

**Challenges a settled decision:** Yes — it changes the settled claim that init always writes explicit host/layout tables, but only for zero-instance projects where those tables are forbidden.

### T-07 — LaneResult both must and must not contain `status`

**Severity:** BLOCKER

**Where:** S16.9, S18.4; previous review R-49

**Claim:** The exact LaneResult object contradicts the universal JSON-envelope rule.

**Evidence:** S16.9 enumerates the LaneResult keys and contains `outcome` but no `status`. S18.4 says “Every `--json` output and every LaneResult” has `{ schema_version, operation, status, ... }`. R-49 marked the envelopes unified, but only the assertion was added; the concrete record was not unified.

**Failure scenario:** A schema generated from S16.9 rejects `status`; a generic v8 reader following S18.4 rejects a LaneResult without it. Either choice violates a normative rule.

**Proposed fix:** Add `status` to S16.9 and define the total mapping: `PASS → ok`; every completed non-PASS outcome → findings; runner/configuration/serialization failure → error. Alternatively, explicitly exempt LaneResult from S18.4 and give it a separate artifact discriminator. The first is closer to R-49's accepted resolution.

**Challenges a settled decision:** No. It completes the settled one-envelope decision and shows R-49 was accepted on weak evidence.

### T-08 — The mandated assay command is malformed and the permitted judge may not implement its discovery API

**Severity:** BLOCKER

**Where:** S15.3 stage 12, S16.3, S16.7.1-S16.7.2, S16.12; run-gate R-34, R-38; demo `ciu.toml:497-499`

**Claim:** No implementation can execute S16.7.2 literally, and a configuration allowed by S16.3 can fail before a lane runs.

**Evidence:** S16.7.2 ends the command with `--resume --progress` but gives `--progress` no path. Run-gate R-38 requires `--progress .assay/progress-<assay_lane>.jsonl`. CIU also mandates `assay lanes --json`; run-gate R-34 records that API as assay 3.2.0+, while S16.3's example and the demo accept `>=2.4`. Progress itself requires at least 2.4.1, not 2.4.0.

**Failure scenario:** With assay 2.4.0, the version floor passes but `--progress`/discovery fails. With a current judge, the literal command consumes the next option as the progress path or exits for a missing argument, so every assay lane fails for CIU's argv rather than the project.

**Proposed fix:** Require `testing.judge.version` to be at least `>=3.2.0` whenever assay lanes exist and reject a weaker declared floor. Specify the exact tail `--resume --progress .assay/progress-<assay_lane>.jsonl`; create `.assay/`, keep it ignored, and include the progress path in evidence when configured. Better, query a machine-readable judge capability/version record and require `lanes-json`, `resume`, `progress-path`, and `judge-provenance` explicitly.

**Challenges a settled decision:** No. It restores the behavior the settled run-gate lift claims to preserve.

### T-09 — Per-host secret transport has no coherent source on either side

**Severity:** BLOCKER

**Where:** S10.1, S10.2.3, S10.6.4, S17.3.1; demo `ciu.hosts.toml:13-15`

**Claim:** The sender is required to transport values it never stores, and the receiver is required to refetch values it may be unable to reach.

**Evidence:** For `from = "file"`, S10.1 records the path and says the value is not stored; S17.3.1 says its “stored value travels.” For Vault, S17.3.1 says the sender fetches and transports a value when the target has no Vault resolution, but the row remains `from = "vault"`; S10.6.4 requires that source to refresh on every `up`. `native` explicitly materializes nothing, yet the transport rules do not exclude or define it. The specified store schema has no transported-source mode.

**Failure scenario:** A target without Vault connectivity receives a copied value, then `ciu up` follows `from = "vault"` and fails trying to refresh it. A file-sourced TLS key has no stored value to put in the bundle. If the sender also cannot reach Vault, the spec gives no required refusal or diagnostic. Two targets needing one `ephemeral` value can independently mint incompatible values.

**Proposed fix:** Define a sealed per-host secret capsule. The sender MUST resolve every value before transfer or refuse with source, host, and failed reachability; file values are read into the capsule without entering the ordinary store. The target imports each entry as `source = "transport"`, tied to bundle digest and original source metadata, and MUST NOT refresh it. Define whether one logical ephemeral is per instance, per host, or per service; derive and transport accordingly.

**Alternative design:** Use SOPS/age-encrypted deployment capsules for static/bootstrap values and Vault Agent for renewable Vault values (Alternative C). This separates deploy-time transport from run-time renewal.

**Challenges a settled decision:** Yes — it replaces the settled claim that the same store row can represent local resolution and transported fallback.

### T-10 — Host-network services are given unreachable addresses and illegal port mappings

**Severity:** BLOCKER

**Where:** S6.2, S7.4, S7.8, S11.4; demo `infra/tailscale-node/ciu.stack.toml:20`

**Claim:** The generic endpoint and injection rules do not have a valid branch for `host_network = true`.

**Evidence:** S11.4 omits the instance network but still injects `ports` under S7.4. S7.8's same-host branch returns the service's Compose DNS name on the instance network. A host-network container is not attached to that network, and Docker rejects/ignores port publishing with host networking: published ports are unsupported in host mode ([Docker host-network documentation](https://docs.docker.com/engine/network/drivers/host/)).

**Failure scenario:** A consumer on the same host receives `dstdns-...-tailscale:PORT`, which cannot resolve on its Compose network. If an endpoint is host-published, the generated Compose model combines `network_mode: host` and `ports`, which Docker does not implement as the declared mapping.

**Proposed fix:** Add a host-network branch: never inject `ports` or service networks; same-host consumers resolve an explicitly selected host address (or a declared loopback only when the consumer also uses host networking); cross-host consumers use an admitted host address; `port == host_port` and `host_bind` is forbidden or purely documentary. Validate collisions against the host listener namespace.

**Challenges a settled decision:** No.

### T-11 — The promised `git clean -x` recovery deletes its own authoritative inputs

**Severity:** BLOCKER

**Where:** S2.3.1-S2.3.4, S3.1.4, S3.7.3, S14.4.5

**Claim:** CIU promises identical regeneration after deleting files that contain non-derivable identity and state.

**Evidence:** S2.3.1 requires the instance file, generated file, store, host inventory, record, rendered file, and `ciu-data/` to be ignored. `git clean -x` therefore removes all of them. S2.3.4 concedes that render cannot recreate the store, instance file, generated file, record, or data; S3.1.4 requires the instance/generated files before rendering. S14.4.5 nevertheless says the next render “regenerates it identically.” The path-derived instance id, selected layout, realness record, host-local facts, and secrets are gone.

**Failure scenario:** Instance A is running, the operator runs `git clean -x`, then `ciu render`. CIU cannot know A's id or layout. Re-running `instance init` can produce a different id or prompt, while `clean` can no longer identify A's resources. The command either fails contrary to S14.4.5 or risks orphaning/deleting the wrong resources.

**Proposed fix:** Put authoritative mutable state outside the ignored worktree: under a stable per-repository directory rooted at `git rev-parse --git-common-dir` for repository state, and a host-local XDG state directory for host facts/secrets. Store a random instance UUID and checkout association there. Worktree files may be regenerated mirrors. Change S14.4.5 to require an explicit `ciu instance adopt --id ...` if that durable registry is unavailable; never claim derivation from deleted facts.

**Challenges a settled decision:** Yes — it replaces “visible ignored state inside the checkout” for authoritative state. A visible mirror can remain, but it cannot be the only copy if `git clean -x` is a supported event.

### T-12 — The demo gate is statically inadmissible

**Severity:** MAJOR

**Where:** S13.3, S15.3 stage 11-stage 12, S16.4.5, S16.6.4; demo `ciu.toml:497-599`, `tools/test-runner/ciu.stack.toml:53`, `assay.toml:64`

**Claim:** The checked-in example cannot pass the governance and testing stages it is meant to demonstrate.

**Evidence:** The `tester` service has `memory_max = "800M"`, while its `exec` lanes request 2G, 3G, and 4G. S16.6.4 requires an exec lane's caps not to exceed the target service's effective governance. The `cw2b_schema` assay lane reports `env_required = ["POSTGRES_HOST", "POSTGRES_PORT", "SCHEMA_GATE_PG_DUMP"]`; the `clean` environment's `forward_env` is absent, and stage 12 states `env_required ⊆ forward_env`. Even if binding-injected `POSTGRES_*` are intended to count, `SCHEMA_GATE_PG_DUMP` has no source. The judge floor is also too low (T-08).

**Failure scenario:** `ciu check --layout prod3` stops before deployment with several deterministic errors. An implementer that lets the demo pass must weaken or reinterpret at least two normative checks.

**Proposed fix:** Set the tester's enforced ceiling at or above the largest exec-lane limit, or run high-memory lanes ephemerally under their own enforceable cgroups. Define a lane's available environment as `forward_env ∪ binding-derived variables ∪ explicitly declared fixed lane variables`; compare assay requirements against that set. Add `SCHEMA_GATE_PG_DUMP` to a declared/forwarded source or remove it from the assay adapter. Set the judge floor to `>=3.2.0`.

**Challenges a settled decision:** No.

### T-13 — `prod3` contains an unreachable publication, a port collision, and unenforced admission data

**Severity:** MAJOR

**Where:** S7.4.1-S7.4.5, S7.8 step 5; demo `ciu.site.toml:18-23`, `infra/db-core/ciu.stack.toml:102`, `infra/authentik/ciu.stack.toml:16`

**Claim:** The demo's publication policy certifies an unreachable Consul address, assigns mesh port 9000 twice on `gstammtisch`, and presents unused `allow_from` data as access control.

**Evidence:** Consul is explicitly published only as `127.0.0.1:8500`, while consumers on `rs1002` resolve it cross-host as `100.64.0.11:8500`; loopback does not listen on that address. Separately, cross-host data bindings require both `db_core.minio.s3` and `authentik.server.http` to be published on `gstammtisch`'s mesh address. Both declare/default `host_port = 9000`, violating S7.4.5. S7.4.4 explicitly says CIU does not program firewalls; no demo template or hook consumes `allow_from_resolved`, so the `allow_from` lists on controller, webapp, UI, and Authentik endpoints enforce nothing.

**Failure scenario:** If stage 7 enforces the collision rule, `prod3` is rejected. If it does not, one of MinIO or Authentik fails to bind. If deployment gets past that, remote Consul clients receive a resolution that times out even though CIU printed it as reachable, while every mesh peer can reach publications whose declarations visually claim a smaller `allow_from` set.

**Proposed fix:** Change one of the two mesh host ports and update its derived resolution. Reject a cross-host resolution through a `publish = "host"` endpoint unless `host_bind` is wildcard or equals the selected network address; alternatively derive an additional network-specific publication rather than reusing a loopback-only one. Either make CIU generate and verify nftables/host-firewall rules for `allow_from`, or rename it `declared_allow_from` and require a named stack adapter/template sink whose rendered output is checked. An unused list must be an ERROR, never apparent enforcement.

**Challenges a settled decision:** No.

### T-14 — The example wave list is neither complete nor derivable from the stated algorithm

**Severity:** MAJOR

**Where:** S8.2-S8.4, S8.8; demo `ciu.toml:237-270`, `examples/ciu.resolved.toml.example:53-61`

**Claim:** The rendered example omits selected Realizations and assigns graph-isolated Realizations to nonzero waves without a rule.

**Evidence:** `prod3` selects 22 Realizations. The example lists 17 and omits `cadvisor`, `registry_lightweight`, `github_runner`, `github_runner_webhook`, and `webhook_listener`. Realizations with no incoming dependency—such as `webapp_ui_react` and the OTEL collectors under the declared `wait = "none"` bindings—are shown late. `reverse_proxy` is placed last although all four upstream bindings are `wait = "none"`; only its network-provider role creates a dependency. S8 does not define edge orientation, the exact level function, or whether sibling edges become discarded self-edges after Realization collapse.

**Failure scenario:** An implementation following ordinary dependency-first topological levels emits the five-wave list in section 4, not the seven-wave example. Another can reproduce the example only by adding undeclared policy such as “proxy last” and “observability after databases.” Host activation order and wait points therefore differ.

**Proposed fix:** Define a directed edge as `consumer → provider`, discard same-Realization edges after collapse, and define `wave(R) = 0` when R has no inter-Realization providers, otherwise `1 + max(wave(provider))`. State exactly which secret/network/fact edges enter that graph. Generate the example from an executable reference resolver and fail CI when it drifts.

**Challenges a settled decision:** No. It makes computed waves actually mechanical.

### T-15 — The gate can report success without checking selected capabilities

**Severity:** MAJOR

**Where:** S5.3.1-S5.3.5, S8.5.1, S8.5.5, S11.5; demo `README.md:50-75`, `infra/mock-targets/ciu.stack.toml:10-43`, `ciu.toml:567-576`

**Claim:** A selected leaf provider or one-shot can fail without affecting `ciu up`, and an empty derived contract makes a realness variant behaviorally unchecked.

**Evidence:** S8.5.1 gates only providers that have an incoming edge from a later wave. S8.5.5 checks merely Running for every non-one-shot at the end. Thus an unconsumed provider with an unhealthy primary is not required healthy, and an unconsumed one-shot's success is not in the final condition. A capability with no consumers has an empty derived contract under S5.3; selection of `seeded` or `simulated` proves no behavior. In the demo, no worker binding targets `probe_targets` despite README line 74 claiming they do; the release lane checks only the selected capability's primary (`dns`), so the HTTP mock can be dead while the release gate passes.

**Failure scenario:** Select an unconsumed seeded database or a two-service mock target. Its primary starts but never becomes healthy, its secondary is dead, or its one-shot exits 1. `ciu up` and the release lane still report success.

**Proposed fix:** Add an explicit deployment acceptance contract. At minimum, every selected capability's variant service must satisfy its primary predicate, every selected one-shot must complete successfully, and every fact or endpoint named by the acceptance contract must be probed. Empty consumer-derived contracts should WARN and require either `accept = [...]` on the LogicalService/variant or an explicit `unchecked = true` acknowledgement. Correct the mock-target bindings or require both mock services in the release lane.

**Challenges a settled decision:** Yes — it challenges the settled assertion that the contract can be only the union of current consumers. Consumer derivation is useful but cannot express “this selected provider itself must work.”

### T-16 — Cross-host and external facts are assertions mislabeled as proof

**Severity:** MAJOR

**Where:** S5.3, S5.6, S8.1, S8.5.3, S17.4; previous review R-24

**Claim:** TCP reachability to one endpoint is not evidence that a provider created each TypedFact, and serial host order does not make it so.

**Evidence:** S8.5.3 says cross-host gates probe only the derived resolution and treat the provider host's own gate as authoritative for facts. A fact-only dependency may have no endpoint to probe. S8.1 treats facts from `external` and `joined` Realizations as already satisfied or asserted. R-24 accepted serial activation as closing cross-host synchronization, but no digest, generation, freshness, or fact list ties the remote gate result to the consumer's current render.

**Failure scenario:** Host A previously passed a Postgres port probe, then its hook fails to create role `controller` on a new release. Host B connects to port 5432 and treats `pg:role/controller` as satisfied. Or a joined instance changes realness after the joiner's record; a stale rendered file still supplies an assertion. CIU reports a stronger conclusion than it compared.

**Proposed fix:** After activation, each host must atomically write a signed or locally authenticated receipt keyed by release/render digest, host, instance UUID, selected realness, and exact passed service/fact/endpoint predicates. A remote consumer accepts a fact only from a receipt for the same deployment digest and expected provider identity, or executes a protocol-level fact probe itself. External facts require a declared probe/receipt; otherwise call them `assertions` and never report them as verified.

**Alternative design:** Kubernetes readiness conditions and Nomad service checks separate readiness evidence from mere process existence; CIU can borrow that evidence model without adopting either orchestrator (Alternative D).

**Challenges a settled decision:** Yes — it reopens R-24, whose resolution proved ordering but not the claimed facts.

### T-17 — Identity checks do not cover all collision namespaces or ownership

**Severity:** MAJOR

**Where:** S4.2-S4.5, S15.3 stage 8; previous review R-14/R-15 discussion

**Claim:** The check named “identity” validates too little to prevent ambiguous resource ownership.

**Evidence:** Stage 8 names `container_name` and `compose_project`, while S4.2 also derives Compose keys, replica keys, hostnames, service-level and replica aliases, and a 24-bit six-hex instance hash. Labels identify project and instance but not a full repository identity or full instance UUID. S4.3's structural ambiguity example does not require checking all derived aliases after `_ → -`, service-name elision, and replica suffixing, nor collision against already-running resources from another checkout.

**Failure scenario:** Two different checkout paths collide in 24 bits, or two project directories share the same project name and instance suffix. One checkout's `down --remove-orphans` or `clean` selects containers/volumes belonging to the other. A base identifier that fits the limit overflows only at replica `-10`, or a service-level alias collides after normalization although container names do not.

**Proposed fix:** Generate a random stable instance UUID and a repository UUID; label every container, network, volume, and image with both full values plus realization/service/replica. Check every emitted key/name/alias at maximum replica count after normalization, and scan existing Docker resources for a mismatched full owner before create/delete. Six hex characters may remain a human-readable suffix, never the authority for deletion.

**Challenges a settled decision:** Yes — it replaces the settled path-hash identity as authority while preserving the settled readable naming format.

### T-18 — Moving a worktree destroys identity while its registry record moves with it

**Severity:** MAJOR

**Where:** S4.1, S14.2, S14.6-S14.7; previous review R-46

**Claim:** Path-derived identity plus a checkout-local registry cannot support rename, move, reap, budgets, or leases safely.

**Evidence:** S4.1 hashes the physical checkout path. A move changes it. The instance record is itself inside that checkout and ignored, so it moves or disappears with the directory; R-46 considered only duplication of `instance_id`, not the location of the authoritative record. The family registry is needed to enumerate instances and enforce `max_concurrent`, leases, and reap, yet no stable repository-level catalog can find a checkout whose path no longer matches its recorded hash. S4.1 also assumes a container mount table can yield the Docker daemon's host path; on Docker Desktop the client container, Linux VM daemon, and macOS/Windows host can expose different namespaces, and no normative source/proof or refusal is defined.

**Failure scenario:** Move `.worktrees/feature-a` to `.worktrees/feature-b` while its instance is up. CIU derives a new id and no longer associates the old containers, ports, record, or joined dependents. Starting the new instance bypasses the family cap; `reap` cannot reliably find the old one. On Docker Desktop, hashing `/workspaces/app` while the daemon needs `/host_mnt/Users/me/app` produces unstable identity and an invalid bind source; a local `is_file()` would query the wrong kernel and cannot prove it.

**Proposed fix:** On first initialization allocate a UUID in a registry under the Git common directory; store checkout path as mutable metadata. `instance move` (or automatic move detection by repository/worktree identity) updates the path atomically while retaining the UUID. Refuse implicit re-identification while owned resources exist. Joined references should store UUID plus repository identity, not only a path/label. Treat the daemon-visible repository path as a fact: read it from a declared environment adapter or prove it with a live sentinel bind/inspect round trip; if neither is available, fail. Never validate a namespace-translated daemon path with the client's local filesystem.

**Challenges a settled decision:** Yes — path hashing becomes discovery metadata rather than stable identity.

### T-19 — The lock order can deadlock and checkout-local gate locks can split

**Severity:** MAJOR

**Where:** S9.5.4, S14.4.1-S14.4.6, S16.5.3; previous review R-42

**Claim:** The declared “deadlock-free” order has a two-instance cycle, and only the primary instance lock was protected from unlink.

**Evidence:** Instance A can hold `LOCK_EX(A)` then request `LOCK_SH(B)` for a join while B holds `LOCK_EX(B)` then requests `LOCK_SH(A)`. Linux `flock()` does not detect deadlocks ([flock(2)](https://man7.org/linux/man-pages/man2/flock.2.html)). Shared-resource locks are `ciu.gate.shared-*.lock` files inside the ignored checkout; `git clean -x` can unlink one while held and create a second inode, reproducing the split mutex R-42 fixed for the rendered file. On NFS/SMB, `flock` behavior depends on server, mount, and kernel semantics; successful acquisition is not proof that another client contends on the same lock.

**Failure scenario:** Two mutually joined instances run `up` concurrently and wait forever. Separately, gate A holds a shared-resource lock, `git clean -x` unlinks it, and gate B creates and acquires a new lock while A is still using the database.

**Proposed fix:** Resolve all referenced instance UUIDs before mutation, then acquire every required instance and shared-resource lock in one global order by `(repository_uuid, instance_uuid, resource)`. Place lock objects in the durable registry, never the worktree. Support only tested local filesystems, or use a host-local lock broker/SQLite registry; a two-client contention probe is required before claiming a network filesystem is supported. Document that kernel `flock` supplies no deadlock detection.

**Challenges a settled decision:** Yes — it keeps `flock` only for supported host-local storage and revises R-42's incomplete proof.

### T-20 — The gate lift regresses detached execution, Git identity, and dual mounts

**Severity:** MAJOR

**Where:** S16.4, S16.12; run-gate R-15, R-17, R-19a, R-23; previous review R-56

**Claim:** Listing lifted rule numbers did not preserve their behavior.

**Evidence:** S16.4 says ephemeral jobs use `docker run --rm`; run-gate R-15/R-17 forbid `--rm` and require detached create/run, logs, `docker wait`, and finally removal so the job—not a log/wrapper transport—determines status and failure evidence survives. S16.12 sets `GIT_CONFIG_GLOBAL=/dev/null`, while R-19a requires a writable isolated file and writes `safe.directory '*'`. Its “dual-mount guard” merely forbids an overlapping extra mount; R-23 requires mounting both physical and namespace-visible repository paths because Git worktree gitfiles can contain either namespace. R-56 marked these lifted on citation alone.

**Failure scenario:** A failed container disappears before logs/artifacts can be collected; a wrapper reports 0 although the job failed; Git refuses a UID-mismatched checkout because `/dev/null` cannot store safe-directory configuration; or a worktree gitfile points to the unmounted physical path and Git says it is not a repository.

**Proposed fix:** Incorporate the run-gate state machine verbatim: detached named container, capture id, follow logs separately, take exit status only from `docker wait`, collect evidence, remove in `finally`; use a private writable Git config and configure `safe.directory`; dual-mount the repository at both resolved physical root and environment workdir when they differ. Turn each lifted rule into a shared conformance fixture run against both tools.

**Challenges a settled decision:** No. It makes the settled lift real and shows R-56 was accepted on weak evidence.

### T-21 — Gate admission is a racy observation, and exec lane limits are not enforceable

**Severity:** MAJOR

**Where:** S16.5.3, S16.6.1-S16.6.4, S16.9

**Claim:** Concurrent lanes can over-admit and overwrite results, while an exec lane's declared caps are only compared, not applied.

**Evidence:** S16.6 reads current slice use, sums requested resources, and admits or refuses, but specifies no reservation or admission lock. Two callers can both observe the same headroom and both start. An exec process already lives in the service container's cgroup; validating a lower lane `memory_max` cannot enforce that limit on just the exec process. Each lane also writes one `ciu.gate.<lane>.json` and prunes one history directory without a per-lane result lock.

**Failure scenario:** Two 4G lanes simultaneously see 5G free and both enter an 8G slice. Concurrent invocations of the same lane race atomic writes/history pruning. An exec lane declared at 1G consumes up to the container's 4G cap while the LaneResult says 1G was applied.

**Proposed fix:** Serialize admission and reserve capacity transactionally in the durable registry; release the reservation in `finally`, with PID/start-time recovery for dead holders. Serialize each lane's result/history update. Rename exec-lane values to `requested_admission` unless CIU creates a child cgroup for the exec process, moves the process into it before user code, and verifies controller writes. `resources_applied` must report measured applied values, not requested values.

**Alternative design:** A small host-local SQLite admission/lock service is sufficient; GitHub/GitLab concurrency controls solve serialization at a coarser CI level but not local multi-process enforcement.

**Challenges a settled decision:** Yes — it rejects the settled assertion that the same resource keys mean enforced caps in every environment mode.

### T-22 — Compose fields are not a faithful cgroup-v2 vocabulary

**Severity:** MAJOR

**Where:** S13.1-S13.4, S16.6; proposal §4.3; demo `ciu.toml:470-483`

**Claim:** Several governance values are reported as direct cgroup-v2 controls although the specified Compose translation has different semantics or no enforcement.

**Evidence:** In cgroup v2, `memory.swap.max` is a swap-only limit, while Compose `memswap_limit` is total memory plus swap; direct numeric mapping changes the cap ([kernel cgroup-v2 memory controller](https://www.kernel.org/doc/html/latest/admin-guide/cgroup-v2.html), [Compose service attributes](https://docs.docker.com/reference/compose-file/services/)). `cpu.weight` ranges 1–10000, whereas legacy `cpu_shares` uses a different scale and daemon mapping. `memory_min` is described as preflight-only rather than applied. Applying `memory_high` after container start creates an uncapped interval. Yet the result vocabulary implies these were enforced.

**Failure scenario:** `memory_max=1G, memory_swap_max=0` is translated to a Compose total-swap setting that means something other than “1G RAM, no swap.” A lane or service exceeds its claimed soft/minimum constraint while CIU reports the cgroup-v2 key as applied.

**Proposed fix:** Define each key in terms of the actual cgroup file and unit. Prefer creating the cgroup and writing v2 controller files directly, then placing the container/lane beneath it. Where Docker is used as the adapter, specify and test the exact conversion and read back the resulting cgroup files. Split result fields into `requested`, `applied`, and `unsupported`; never label a preflight sum as an enforced controller.

**Challenges a settled decision:** Yes — the shared names can remain, but “same numeric scale and code path” cannot.

### T-23 — Hooks receive ambient authority, and validation cannot be promised side-effect-free

**Severity:** MAJOR

**Where:** S10.2.3-S10.2.5, S12.2, S12.4; demo `infra/db-core/ciu.stack.toml:130-136`

**Claim:** A hook can read unrelated process credentials and every stack secret, validation executes untrusted code under an unenforceable side-effect contract, and same-phase state visibility is undefined.

**Evidence:** The hook environment removes `CIU_SECRET_*` but does not define a clean allowlist, so ambient `VAULT_TOKEN`, cloud credentials, SSH agent sockets, proxy credentials, and arbitrary operator variables remain. S12.2 supplies the stack's secrets rather than a per-hook need set; that conflicts with `native`, which promises CIU materializes nothing. S12.4 says `--validate` “MUST be side-effect-free,” but CIU simply runs the program with a timeout and no filesystem/network/process sandbox. A convention is not enforcement. S12.1 orders entries and S12.3 merges state, but “visible to every later step” does not say whether hook 2 in the same phase receives hook 1's state in a freshly built context.

**Failure scenario:** A compromised stack hook exfiltrates the operator's `VAULT_TOKEN` during `ciu check`, or a buggy `--validate` path edits a database. A hook needing one MinIO key receives unrelated database and TLS material. Two ordered `pre_compose` hooks disagree across implementations when the second expects state emitted by the first. CIU then falsely describes check as non-mutating and cannot promise deterministic hook behavior.

**Proposed fix:** Launch hooks with a clean, versioned allowlist environment, closed stdin/stdout protocol, no inherited credential-agent sockets, and per-entry `needs_secrets = [...]`; reject native secrets in that list. Rebuild the context before each list entry and state explicitly that output from entry `i` is visible to entry `i+1`, or explicitly isolate entries and forbid that dependency. Do not execute hooks during ordinary static check. Put dynamic validation under `ciu check --live` in a read-only/sandbox profile, label it potentially effectful, and make network/filesystem privileges explicit.

**Alternative design:** Keep subprocess JSON—it has better crash and dependency isolation than in-process plugins—but copy Ansible's explicit argument/result contract and OCI hook lifecycle discipline. An OCI container sandbox per hook is safer but adds image/build overhead.

**Challenges a settled decision:** Yes — it retains subprocess hooks but narrows their settled context and moves validation.

### T-24 — In-place secret refresh is not atomic for readers

**Severity:** MAJOR

**Where:** S10.6.3-S10.7, S11.4

**Claim:** The rotation procedure can expose an empty or partial secret to a running container.

**Evidence:** S10.7 requires stable temp-copy paths because Docker bind mounts an inode, then refreshes the file in place. In-place truncate/write is not atomic to concurrent readers; rename would be atomic but would replace the inode hidden behind a file bind mount.

**Failure scenario:** A service opens `/run/secrets/token` between truncate and final write and caches an empty or truncated credential. CIU reports a successful rotation, but the service begins failing authentication until restarted.

**Proposed fix:** Bind-mount a per-service secret directory, not each file inode. Write a new file in that directory, set ownership/mode, `fsync`, then atomically rename over the old name and `fsync` the directory. Specify whether applications must reopen the path and whether CIU signals/restarts those that do not. Never expose the staging filename inside the container.

**Alternative design:** Vault Agent templates already implement renewable secret rendering and process signaling; systemd credentials are strong for host services, and Docker secrets are useful only if Swarm is acceptable. Use CIU's directory projection for standalone Compose and Vault Agent only for renewable Vault sources.

**Challenges a settled decision:** No.

### T-25 — Push mutates `current` in place and “rollback” has no previous release

**Severity:** MAJOR

**Where:** S17.1-S17.5; demo `ciu.hosts.toml:24-34,45-49,60-64`

**Claim:** An interrupted transfer can create a mixed release, and the demo's rollback command destroys availability instead of restoring the previous version.

**Evidence:** S17.3.3 transfers the reduced checkout directly to `bundle_dir`; the demo sets that directory to `/opt/ciu/current`. There is no closure manifest, file digest, staging directory, completeness marker, or atomic switch. `bundle_excludes` can omit a file still referenced by a stack/template. The conventional rollback command on every host is `ciu down`, not activation of a prior artifact.

**Failure scenario:** `rsync` updates `ciu.toml` and half the templates, then the connection drops. `activate apply` consumes a hybrid old/new tree. A failed health step runs `ciu down`, leaving the service absent; no previous bytes or state selection are named.

**Proposed fix:** Build a manifest containing every required declaration, template, hook, config input, image digest, mode, and SHA-256. Transfer to `releases/<digest>.staging`, verify closure and hashes on the target, rename to `releases/<digest>`, then atomically switch `current` and record `previous`. Activation consumes the immutable release path. Rollback switches to `previous` and runs its declared activation. Reject an exclusion that intersects the computed closure.

**Alternative design:** An OCI artifact gives standard content-addressed distribution and signatures; it is net positive if a registry already exists. A Nix closure is stronger but too large a dependency for this estate. Rsync remains a transport, not the activation model.

**Challenges a settled decision:** Yes — it replaces direct bundle-directory mutation while retaining rsync as one transport.

### T-26 — Port collision keys do not model the host socket namespace

**Severity:** MAJOR

**Where:** S6.3.2, S7.4.1-S7.4.5, S14.7.1, S15.3 stage 7/stage 15

**Claim:** `(network, host_port)` both rejects legal sockets and misses illegal ones.

**Evidence:** TCP and UDP may legally use the same address/port, but S6.3.2 rejects a duplicate numeric port without preserving protocol. Conversely, two declared networks can resolve to the same host address; wildcard `0.0.0.0:9000` conflicts with `100.64.0.11:9000` even if their abstract network names differ. The record/lease design does not reserve stopped instances' future host sockets transactionally.

**Failure scenario:** A DNS service using TCP and UDP 53 is falsely refused. On another host, a wildcard host publication and a mesh-specific derived publication both pass the `(network, port)` check and Docker rejects the second bind. Two concurrent instance starts can both see a free port.

**Proposed fix:** Normalize publications to actual socket claims `(host, protocol, bind_address, port)`. Define wildcard and IPv4/IPv6 overlap rules, include current daemon/listener state, and reserve claims atomically before startup. Instance overrides participate in the same registry transaction; release occurs only after resources are confirmed stopped.

**Challenges a settled decision:** No.

### T-27 — Renaming Compose services leaves embedded service references dangling

**Severity:** MAJOR

**Where:** S4.2, S11.2-S11.5

**Claim:** CIU rewrites service keys but neither rewrites nor forbids Compose fields that refer to the original keys.

**Evidence:** S11.4 injects derived `depends_on`, networks, and identities, but Compose also permits service-name references in `network_mode: service:<name>`, `ipc: service:<name>`, `pid: service:<name>`, `volumes_from`, legacy `links`, and `extends`. These are not in S11.3's forbidden list and no rewrite algorithm is given. The Compose specification defines the reference semantics ([Compose services reference](https://docs.docker.com/reference/compose-file/services/)).

**Failure scenario:** A template contains `network_mode: "service:browser"`. CIU renames `browser` to a derived replica key, leaves the string unchanged, and Compose fails or attaches to a different unrenamed service. Replication makes one-to-one rewriting impossible without an explicit rule.

**Proposed fix:** Either forbid every service-key-bearing field outside CIU's own `depends_on`, with exact YAML-path diagnostics, or specify a complete AST rewrite. For replicas, refuse singular reference fields unless a target replica is declared. Add conformance cases for every Compose reference field and every extension field CIU allows.

**Challenges a settled decision:** No.

### T-28 — The lexical secret scan cannot certify a file as secret-free

**Severity:** MAJOR

**Where:** S2.4.1-S2.4.2, S3.1.2; estate defaults/check doctrine

**Claim:** The scan has trivial false negatives and false positives but its result is presented as a security conclusion.

**Evidence:** Key suffixes and a few token forms miss `dsn = "postgres://u:p@h/db"`, `cookie = "..."`, `client_key = "..."`, authorization headers in free-form strings, base64/hex values, and secrets under arbitrary user tables. Conversely, `api_token = "production"`, a long test fixture, or a public key-like identifier can match despite not being secret. Exempting declared paths does not fix classification.

**Failure scenario:** A committed config contains `headers = "Authorization: Bearer ghp_..."` and passes; CIU certifies it as secret-free. Another project uses `api_token = "not-a-secret-placeholder"` and cannot pass stage 2 despite there being no secret.

**Proposed fix:** Rename this to a heuristic secret-lint. Block only high-confidence known formats and exact matches to values currently held in CIU's secret store; report entropy/suspicious-key findings as warnings with path-level suppressions that themselves contain no value. Make semantic secret sinks explicit in schemas. Never print “secret-free”; print exactly which comparisons ran.

**Challenges a settled decision:** No.

### T-29 — The demo has no complete image/build ownership model

**Severity:** MAJOR

**Where:** S3.4.3, S6.2, S17.6; demo `ciu.toml:30`, all `ciu.stack.toml`, `infra/mock-targets/ciu.compose.yml.j2:14,33`

**Claim:** Most demo images are neither declared vendor images nor given build contexts, so `ciu build` cannot implement the promised classification.

**Evidence:** `[project].vendor_images` lists only five repositories. The stack files contain many other third-party images (`nginx`, `postgres`, `apache/skywalking-*`, `registry`, `joxit/docker-registry-ui`, and others) and many `dstdns/*` images. Only `mock-targets` has Compose `build:` blocks. S17.6 says CIU builds project-built images and records vendor images by digest, but no per-image kind or build context supplies the missing distinction.

**Failure scenario:** `ciu build` either tries to build `nginx:alpine` without a context, treats `dstdns/controller:latest` as vendor and never stamps the checkout revision, or invents ownership from the repository prefix. Each violates explicit-over-magic or provenance.

**Proposed fix:** Put ownership next to every image: `image = { ref = "...", kind = "vendor" }` or `image = { ref = "...", kind = "build", context = "...", dockerfile = "..." }`. A shorthand literal may mean vendor only if that is an explicit policy default. Remove the separate incomplete `vendor_images` list, or mechanically require it to cover every literal vendor repository. Make the demo classify every image and supply every project build context.

**Challenges a settled decision:** Yes — it replaces the settled global vendor list with locally complete declarations.

### T-30 — Config-directory and hostdir semantics reject harmless layouts while leaving dangerous cases undefined

**Severity:** MINOR

**Where:** S6.8-S6.9, S11.4; demo `infra-global/registry-lightweight/ciu.stack.toml:64-71`

**Claim:** Directory-mount collision rules compare container paths across isolated services, but do not define image symlinks or `hostdir.seed` behavior.

**Evidence:** Two services have separate mount namespaces, so both targeting `/etc/app` is not a collision; the cross-service parent-overlap refusal cannot prevent one service hiding another's files. Within a service it can. The spec does not say whether CIU inspects a target that is a symlink in the image, whether it follows/replaces it, or how `hostdir.seed` behaves: source, empty-only rule, ownership, symlink policy, atomicity, retries, and upgrades are absent.

**Failure scenario:** Two unrelated services legitimately mount `/etc/nginx/ciu` and are refused. A target resolves through an image symlink outside the intended directory, or two retries partially seed a persistent hostdir differently, with no normative outcome.

**Proposed fix:** Apply target-overlap checks per rendered service only. Define target directories lexically and either forbid symlink targets after an image probe or resolve them inside a stopped container and record the result. Specify seed as an idempotent transaction: source path, copy only into a verified empty destination, no symlink traversal, ownership/mode mapping, temp directory plus atomic rename, and a recorded seed digest; changed seed with nonempty destination must refuse.

**Challenges a settled decision:** No.

### T-31 — Zero-instance gates forbid a legitimate standalone integration-test input

**Severity:** MINOR

**Where:** S16.4.2, S16.11; prompt §3.2 zero-instance probe

**Claim:** Refusing all bindings in a zero-instance project blocks libraries that test against an explicitly external database or API.

**Evidence:** Zero-instance mode is specifically for libraries and tools, yet S16.4.2 makes `binds` an error. A library integration suite often needs a typed externally supplied endpoint without owning a CIU instance. Falling back to untyped ambient `DATABASE_URL` discards the validation and delivery model the gate otherwise provides.

**Failure scenario:** A standalone database client library has only host/ephemeral lanes and an externally managed test database. It cannot declare the dependency as a binding and must either add a fake Realization—losing zero-instance mode—or use ambient variables CIU cannot check.

**Proposed fix:** Add `[testing.externals.<name>]` with either a literal non-secret endpoint or required environment-variable names and optional probe. Permit zero-instance environment bindings only to these externals; continue to forbid LogicalService, instance-network, Vault, and joined bindings. Missing values are NOT_RUN/`external-missing`, failed probes are NOT_RUN/`external-down`.

**Challenges a settled decision:** Yes — it narrows the settled refusal without introducing a CIU instance.

### T-32 — Testing inheritance is unnecessarily shallow and leaves path ownership ambiguous

**Severity:** MINOR

**Where:** S16.2.1

**Claim:** One-level inheritance blocks a common monorepo shape and does not say which file resolves inherited relative mounts.

**Evidence:** An inherited file is forbidden from inheriting another, so a leaf cannot reuse a team environment that itself reuses an estate environment. `inherit` is resolved relative to the current file, but `extra_mounts` inside the inherited environment can contain relative host paths and no defining-file base is retained.

**Failure scenario:** Project C inherits B; B inherits the central A. C is rejected despite an acyclic chain. If B says `extra_mounts = ["./cache:/cache"]`, C may resolve it against C rather than B and mount the wrong directory.

**Proposed fix:** Resolve recursively with a canonical-path visited set and explicit cycle trace. Every inherited path-valued field is normalized relative to the file that declared it before merge. Provide `ciu schema export-testing` (or `ciu testing flatten`) to emit a standalone resolved environment for consumers that do not want inheritance at runtime.

**Challenges a settled decision:** Yes — it replaces the settled one-level limit with cycle-checked recursion.

### T-33 — One schema number is overloaded across unrelated machine artifacts

**Severity:** MINOR

**Where:** S3.7.1, S12.3, S16.9, S18.4; previous review R-49

**Claim:** `schema_version = 2` does not identify which schema is versioned or permit independent compatibility changes.

**Evidence:** The same number labels rendered TOML, hook JSON, LaneResult, and every CLI JSON envelope. Those artifacts have different producers, consumers, and evolution rates. S18.4 says unknown versions are refused but does not say whether adding a LaneResult field forces the resolved TOML and hook protocol to version 3, or how a v7 reader distinguishes artifact kinds before applying a schema.

**Failure scenario:** CIU adds a backward-incompatible hook-context field and increments only that producer. A consumer sees version 3 but cannot determine whether it is hook protocol 3 or generic output 3; alternatively CIU bumps everything and unnecessarily breaks readers of unchanged resolved TOML.

**Proposed fix:** Give every artifact an explicit discriminator and independent version, for example `{ api = "ciu.dev/lane-result", api_version = 1 }`, `{ api = "ciu.dev/command-result", api_version = 1 }`, and `[resolved] api = "ciu.dev/resolved"; api_version = 1`. Publish a compatibility policy for additive fields and require readers to ignore unknown fields within a known compatible version.

**Challenges a settled decision:** Yes — it refines the settled unified envelope into a common header with artifact-specific schemas.

### T-34 — Three mechanical rules are internally unresolved

**Severity:** MINOR

**Where:** S7.4.6, S7.5-S7.5.1, S8.6.4 references in S6/S8/S9/S16; demo `ciu.toml:233-250`

**Claim:** Bundle recursion, publication recording, and health cross-references each lack one implementable definition.

**Evidence:** S7.5 calls `includes` “one level of composition” and in the same sentence says an included bundle's own includes are followed; S7.5.1 recursively defines effective sets. S7.4.6 writes `published_on = [<networks>]`, but `publish = "host"` is not associated with a Network, so its required value is undefined. Several rules cite S8.6.4 for health although S8.6 ends at S8.6.3.

**Failure scenario:** A nested bundle is accepted by one implementation and rejected by another; a host-published endpoint cannot be serialized into the mandated `published_on` type; a generated implementation/reference linker has a dangling rule id.

**Proposed fix:** State “includes are recursively expanded to arbitrary acyclic depth” or enforce exactly one level consistently. Replace `published_on` with structured claims such as `{ scope = "host", bind = "0.0.0.0", port = 443, protocol = "tcp" }` and `{ scope = "network", network = "mesh", ... }`. Change every S8.6.4 reference to S8.6.3 after verifying the intended predicate, and add a cross-reference linter to the document gate.

**Challenges a settled decision:** No.

### T-35 — The schema is writable data, but the machine query surface is too coarse

**Severity:** NOTE

**Where:** S3-S7, S18.4; proposal P3, P8-P11; previous review R-68/R-72

**Claim:** Plain TOML is a defensible canonical form, but a newcomer must understand too many overloaded names and a machine must parse the entire resolved document to retrieve one identity.

**Evidence:** A one-stack deploy still requires project, LogicalService, Realization, RealizedService/variant service, bundle, layout, host, endpoint, binding, identity, and optionally environment/lane concepts across at least three declarations plus two generated files. “Service” means capability, stack member, selected variant carrier, and Compose service key. Static JSON Schema can validate local shapes, but cannot prove cross-file references, selected-variant endpoint availability, graph acyclicity, or context-dependent user tables. `ciu.resolved.toml` is a large merged input plus derived output; a consumer seeking one container name must know both logical selection and realization/service paths.

**Why it matters:** This raises third-party adoption cost and encourages scripts to depend on incidental TOML layout. R-68 accepted the vocabulary and R-72 accepted the rendered file as the machine surface without providing narrow stable queries.

**Proposed fix:** Keep TOML as canonical input, but rename documentation terms consistently (`capability`, `realization`, `component`, `compose_key`). Generate artifact-specific editor schemas plus a semantic `ciu check --json`. Add stable narrow queries such as `ciu query identity --logical main_db --json`, `ciu query binding --consumer controller.controller --name database --json`, and `ciu query publications --layout prod3 --json`; index the rendered artifact by LogicalService as well as Realization. Include schema/API compatibility tests in migration.

**Alternative design:** An optional CUE or Pkl authoring frontend can compile typed references into canonical TOML, but making it mandatory would violate the standalone/read-write constraint and add a runtime dependency. Nickel, Jsonnet, Dhall, and KCL have the same trade: stronger composition, worse universal tooling and round-trip editing. Do not replace canonical TOML in v8.

**Challenges a settled decision:** No. This retains the settled declaration format and adds a proper API.

## 3 Alternative designs

### A. Durable identity, registry, reservations, and locks — highest expected value

**What it replaces:** S4.1's path-hash authority, checkout-local instance records, path/label joined references, checkout-local shared-resource locks, and observational port/resource admission.

**How it works:** `ciu instance init` creates a random UUID and registers it under a repository UUID in a durable registry rooted at the Git common directory for repository-shared metadata and an XDG state directory for host-specific metadata. One transactional store (SQLite in WAL mode on a supported local filesystem is sufficient) holds checkout paths, labels, layout/realness records, leases, resource/port reservations, joined UUIDs, and active operation records. Docker objects carry the full repository and instance UUID labels. Operations resolve every participating UUID first, acquire locks in sorted order, reserve resources and sockets transactionally, then act. Short path hashes remain display suffixes only.

**Why better against P1-P11 and standalone:** Identity has one durable source; moves update a fact rather than change identity; recovery after `git clean -x` is honest; delete ownership is checked against the full object; and one local process can enforce atomic admission. It remains a CIU-only standard-library/SQLite facility. SQLite's locking model is documented and inspectable ([SQLite locking](https://www.sqlite.org/lockingv3.html)); unlike arbitrary checkout files, the registry can make multi-record changes atomic.

**Costs and migration:** Existing instances need one adoption pass that verifies labels and records old id → UUID. Network-shared Git common directories still need host-local registries and explicit cross-host coordination; SQLite is not a distributed lock service. The visible ignored mirror required by estate doctrine should remain for inspection, clearly marked non-authoritative.

**Spec changes:** Rewrite S2.3, S4.1-S4.5, S9.5, S14.2, S14.4, S14.6-S14.7, S16.5.3, and S16.6; add registry schema, transaction boundaries, crash recovery, label ownership, move/adopt, and sorted lock-order rules.

### B. Manifested, content-addressed, transactional activation

**What it replaces:** S17's mutable rsync into `bundle_dir`, ad hoc excludes, unversioned activation commands, and `ciu down` as rollback.

**How it works:** CIU computes a closed release manifest from declarations, included bundles, stack templates, hooks, config templates, migrations, image digests, and modes. The artifact is addressed by the manifest digest. A target receives it in a staging directory, verifies every hash and prerequisite, renders host-local artifacts, then atomically renames the release and switches `current`. `apply` records the previous digest; `rollback` switches back and executes the previous release's activation. Rsync, SSH tar streams, or an OCI registry may transport the same artifact. OCI descriptors already define digest-addressed content ([OCI image descriptor](https://github.com/opencontainers/image-spec/blob/main/descriptor.md)); the distribution protocol supplies a standard remote store ([OCI Distribution Specification](https://github.com/opencontainers/distribution-spec/blob/main/spec.md)).

**Why better against P1-P11 and standalone:** The artifact declares exactly what travels, a missing input fails before mutation, interrupted transfer cannot alter current, and rollback names a real prior object. CIU can implement a directory/tar transport standalone; OCI support is optional. This is materially simpler than adopting Nix, whose closures and content-addressed store solve more than CIU needs.

**Costs and migration:** Disk temporarily holds two releases; mutable data and secrets must be explicitly outside the release root; hook paths must be release-relative. Existing `bundle_dir` installations need a one-time layout migration.

**Spec changes:** Replace S17.3-S17.5 with manifest schema, closure computation, staging/verification/switch transactions, previous/current records, garbage collection, and recovery after interruption. Make exclusions subtract only non-closure files.

### C. Binding directories plus split secret backends

**What it replaces:** Per-delivery endpoint special cases, per-file bind mounts, whole-stack hook secret contexts, and the attempt to use one store row for both local and transported/Vault-refresh values.

**How it works:** Each consumer gets `/run/ciu/bindings/<local>/`, a read-only directory containing typed files such as `type`, `host`, `port`, `uri`, `path`, `ca.crt`, and only the credentials that binding authorizes. This follows the portable filesystem projection of the [Service Binding Specification](https://servicebinding.io/spec/core/1.1.0/). CIU can derive environment variables or template mappings from the same object for legacy consumers. The directory is the atomic update boundary. Static/bootstrap secrets travel in age-recipient-encrypted SOPS documents; renewable Vault values are rendered by Vault Agent when the target can reach Vault ([SOPS](https://github.com/getsops/sops), [Vault Agent templates](https://developer.hashicorp.com/vault/docs/agent-and-proxy/agent/template)). Host services may use [systemd credentials](https://systemd.io/CREDENTIALS/); Docker Swarm secrets are not worth requiring solely for Compose deployments.

**Why better against P1-P11 and standalone:** A binding is one typed object regardless of delivery, secret authorization is per consumer, rotation is atomic, and transported values have an explicit backend. The core directory projection needs no external tool; SOPS and Vault Agent are optional adapters selected explicitly.

**Costs and migration:** Applications that currently consume environment variables need either a compatibility projection or code changes. SOPS needs recipient/key management; Vault Agent adds one process/sidecar and policy. A binding schema must define filenames and reload behavior.

**Spec changes:** Replace S6.4 delivery details and S10.2/S10.7 with a binding-object schema and projections; add `needs_secrets`/credential references, backend selection, atomic directory replacement, and transport capsule rules. Keep `env` and `template` as derived views, not independent sources of truth.

### D. Digest-bound readiness and activation receipts

**What it replaces:** Reachability-only cross-host fact gates, timeless external assertions, and the claim that host ordering makes remote facts authoritative.

**How it works:** A host completes activation by writing a receipt containing repository/instance/release/render digests, host identity, selected realness, service readiness, completed one-shots, fact probes, endpoint probes, timestamps, and expiries. It is signed or authenticated by a host key already pinned in inventory. A consumer accepts only predicates matching its expected release and provider identity. Protocol probes remain available for independently testable facts. Kubernetes distinguishes readiness from process state and removes unready Pods from Service traffic ([Pod lifecycle/readiness](https://kubernetes.io/docs/concepts/workloads/pods/pod-lifecycle/)); Nomad likewise models checks independently ([Nomad service checks](https://developer.hashicorp.com/nomad/docs/job-specification/check)). CIU needs their evidence distinction, not their schedulers.

**Why better against P1-P11 and standalone:** The comparison matches the conclusion: a named fact is backed by a named probe for a named artifact. Receipts are plain JSON and can travel over the same SSH channel or be read from a file, so no orchestrator is required.

**Costs and migration:** Receipt signing/key rotation and clock/expiry policy add machinery. Hand-parallel `up` must exchange receipts or refuse rather than guess. External providers require probe plugins or explicit unverified assertions.

**Spec changes:** Rewrite S5.6, S8.1, S8.5, S9.5.3, and S17.4; define receipt schema/version, signer trust, freshness, digest matching, storage/transport, and exact fallback refusal.

### E. One executable gate conformance engine

**What it replaces:** Prose-level copying of run-gate rules into CIU and two drifting implementations of detached execution, Git/worktree mounting, assay discovery/progress, evidence, and exit status.

**How it works:** Keep both CLIs standalone, but publish a small versioned conformance fixture/package that contains pure argv/state-machine builders and black-box scenarios. Both implementations must pass: detached container exit capture, physical+namespace dual mounts, safe-directory isolation, cgroup placement/readback, progress/resume flags, artifact modes, cleanup on signals, sequence outcomes, and concurrent admission. The runtime remains local to each tool. GitHub Actions and GitLab model jobs and dependencies well, and GitLab resource groups model coarse mutual exclusion ([GitHub jobs](https://docs.github.com/en/actions/how-tos/write-workflows/choose-what-workflows-do/use-jobs), [GitLab `needs`](https://docs.gitlab.com/ci/yaml/), [GitLab resource groups](https://docs.gitlab.com/ci/resource_groups/)); Bazel provides hermetic test contracts and Dagger service dependencies, but either would be an excessive hard dependency here ([Bazel test encyclopedia](https://bazel.build/reference/test-encyclopedia), [Dagger services](https://docs.dagger.io/0.21.4/getting-started/types/service/)).

**Why better against P1-P11 and standalone:** Behavioral parity is executable, a regression goes red in both repositories, and neither CLI shells out to the other. Exact job status and evidence are tested rather than asserted.

**Costs and migration:** A small shared protocol/fixture must be versioned. Duplicated runtime code remains unless factored into a dependency; keeping only fixtures preserves the no-hard-dependency rule at the cost of some duplication.

**Spec changes:** Make S16.12 normative by behavior and fixture version, copy the exact run-state machine into S16, define atomic admission/result writes, and add compatibility tests to both release gates.

### F. Stable query APIs with optional typed authoring

**What it replaces:** `ciu.resolved.toml` as the only broad machine interface and a single undifferentiated `schema_version`.

**How it works:** Retain plain TOML as canonical declaration data. Publish artifact-specific schemas and narrow `ciu query ... --json` commands whose common header names the artifact and API version. Add an optional compiler from CUE or Pkl into canonical TOML for teams wanting typed references/composition; the emitted TOML is checked and can be committed. CUE's constraints and unification and Pkl's typed configuration catch reference/shape errors earlier ([CUE specification](https://cuelang.org/docs/reference/spec/), [Pkl language reference](https://pkl-lang.org/main/current/language-reference/index.html)). Nickel, Dhall, KCL, and Jsonnet offer related composition tradeoffs; none should become a CIU runtime prerequisite.

**Why better against P1-P11 and standalone:** Any TOML tool can still read/write declarations, while machines get small stable responses and authors may opt into stronger checking. A compiler is explicit and one-way; CIU never secretly evaluates declarations.

**Costs and migration:** More APIs and schemas need compatibility tests. Optional authoring languages cannot preserve round-trip TOML comments, so generated files must be marked as such; documentation must choose one canonical path and avoid a two-class ecosystem.

**Spec changes:** Rewrite S18.4, add an API compatibility section and query grammar, index resolutions by LogicalService, and define optional external compiler inputs as noncanonical sources.

### G. Generated migration plan with proof obligations

**What it replaces:** A best-effort rewrite that can silently choose semantics for Jinja-controlled v7 declarations and image ownership.

**How it works:** `ciu migrate --plan` emits canonical v8 declarations plus a machine-readable list of unresolved decisions. Each item names the v7 source span, target rule, candidate choices, and a probe/assertion that closes it. Migration refuses finalization until all decisions are explicitly answered. It then renders v7 and v8 for representative layouts and compares normalized Compose models, identity ownership, publications, mounts, secret sinks, and gate lanes; irreducible intended differences require recorded approvals.

**Why better against P1-P11 and standalone:** Nothing is invented, unmechanical choices fail loudly, and the result is evidence rather than a successful parser run. It needs only CIU and Docker/Compose where behavioral comparison is requested.

**Costs and migration:** More operator work for genuinely dynamic Jinja and no perfect proof of arbitrary templates. The tool must retain a v7 reader during the cutover and provide normalization rules.

**Spec changes:** Expand Appendix D and S18's migration verb into plan/decision/apply/verify phases; define unresolved-decision records, comparison scopes, approval provenance, and exit codes.

### Known-solution disposition

| Area | Better-known mechanism | Net for this estate |
|---|---|---|
| Binding/delivery | Service Binding's filesystem projection gives a uniform, least-authority interface; 12-factor environment variables are ubiquitous but restart-bound and collision-prone. Compose DNS is excellent on one network, while a service mesh supplies cross-host identity/routing at significant operational cost. Compose `depends_on` conditions order siblings but do not prove arbitrary cross-stack facts ([Compose startup order](https://docs.docker.com/compose/how-tos/startup-order/), [Compose networking](https://docs.docker.com/compose/how-tos/networking/)). | Adopt binding directories and Compose DNS locally; keep env as compatibility. Do not adopt a mesh solely for CIU. |
| Declarations | CUE/Pkl/Dhall/KCL/Nickel give typed references/composition; Jsonnet gives powerful generation. They are worse for round-trip editing and “any TOML tool” interoperability. | Keep canonical TOML; optional compile-to-TOML frontend only. |
| Secrets | SOPS/age is strong for encrypted static material in Git/artifacts; Vault Agent is strong for renewable values; systemd credentials are strong for host units; Docker secrets require Swarm semantics. | Adopt explicit SOPS and Vault Agent adapters plus a standalone atomic directory backend; no mandatory Swarm. |
| Locks/registry | Stable lock files/SQLite transactions beat directory locks tied to movable checkouts; `flock` is suitable only on verified host-local filesystems. | Adopt the durable local registry; do not pretend it is a distributed lock. |
| Hooks | In-process plugins share crashes and dependencies. Subprocess JSON resembles Ansible modules and isolates language/runtime failures; OCI hooks give precise lifecycle but are low-level and privileged ([Ansible module architecture](https://docs.ansible.com/projects/ansible/latest/dev_guide/developing_program_flow_modules.html), [OCI runtime hooks](https://github.com/opencontainers/runtime-spec/blob/main/config.md)). | Keep subprocess JSON with a clean environment, capabilities, per-hook secrets, and sandboxed live validation. |
| Waves/readiness | Kubernetes/Nomad/systemd model ordering/readiness more rigorously; full adoption introduces schedulers/control planes and conflicts with the lightweight Compose goal. | Borrow explicit conditions and receipts; do not replace CIU with an orchestrator. |
| Test graph | GitHub/GitLab provide job DAGs and concurrency, Bazel hermetic targets, Dagger programmatic service DAGs. None covers local CIU instance identity/realness without an adapter. | Keep CIU lanes; use executable run-gate parity and optionally export a CI DAG. |
| Push/activation | OCI artifacts provide digests/distribution; Nix provides complete closures; rsync only copies bytes and has no activation transaction ([rsync manual](https://download.samba.org/pub/rsync/rsync.1)). | Adopt manifested atomic releases; OCI optional, Nix excessive, rsync transport-only. |
| Identity/labels | Kubernetes UIDs and Docker labels distinguish display names from immutable ownership. | Adopt UUID authority and full ownership labels; retain readable derived names. |
| Migration | Terraform-style plans make unknown changes visible; semantic Compose comparison provides a domain-specific oracle. | Adopt plan/apply/verify with explicit unresolved decisions, not heuristic success. |

## 4 Demo walk results

All top-level and stack TOML files in `v8-dstdns-demo/` parse as TOML. That is only syntax: the semantic contradictions and demo violations below remain.

### Three binding resolutions

The identity examples use project `dstdns` and instance `98535c`. For `prod3`, the consumer/provider placements are from `ciu.toml:265-270`; for `local`, all selected stacks share `localhost` and the implicit instance network.

1. **`controller.controller.database → main_db.sql`.** `main_db=live` selects `db_core.postgres` (`ciu.toml:71-77`; `infra/db-core/ciu.stack.toml:34`). On `prod3`, controller is on `rs1002`, the provider is on `gstammtisch`, and the first admitted shared address network is `mesh`: `host=100.64.0.11`, `port=5432`, no URL for TCP, `delivery=env`, variables `POSTGRES_HOST`/`POSTGRES_PORT`, `requires=["tailscale_node"]`. On `local`, resolution is `network=instance`, `host=dstdns-98535c-db-core-postgres`, `port=5432`, `requires=[]`. This agrees with `examples/ciu.resolved.toml.example:169-178` for `prod3`; the example does not print the local row.

2. **`reverse_proxy.nginx.controller → controller.http`.** On `prod3`, nginx is on `tsstammtisch`, controller is on `rs1002`, so the direct mesh form wins: `host=100.64.0.12`, `port=8083` (the endpoint's cross-host `host_port`), `url=http://100.64.0.12:8083`, `path=/api/controller`, `delivery=template`, `requires=["tailscale_node"]`. On `local`, it is `host=dstdns-98535c-controller`, `port=8080`, `url=http://dstdns-98535c-controller:8080`, the same path, and no network requirement. The `prod3` row agrees with example lines 203-213.

3. **`ciu.vault → vault.api`.** For CIU running/rendering on `rs1002` in `prod3`, Vault is on `gstammtisch`: `network=mesh`, `host=100.64.0.11`, `port=8200`, `url=http://100.64.0.11:8200`, `delivery=none`, `requires=["tailscale_node"]`. On `local`, it is `network=instance`, `host=dstdns-98535c-vault`, `port=8200`, `url=http://dstdns-98535c-vault:8200`, `requires=[]`; a devcontainer reaches it by S7.8.7's attach/inspect branch. The `prod3` row agrees with example lines 214-223.

These three successful rows do not rescue the model: Consul's analogous remote resolution yields `100.64.0.11:8500` while the only declared listener is `127.0.0.1:8500` (T-13).

### Wave reconstruction

**ASSUMPTION:** “maximum topological level” means provider-first levels over consumer → provider edges; same-Realization sibling edges are discarded after collapse; every `wait = "none"` data resolution contributes no bind edge but does contribute S8.3's network edge when cross-host; enabled `store = "vault"` contributes `secret→vault`. Those are the closest literal readings of S8.2-S8.4.

The selected `prod3` closure has 22 Realizations. The resulting waves are:

```text
0: cadvisor, github_runner, github_runner_webhook, otel_aggregator,
   otel_collector_node, registry_lightweight, tailscale_node, vault,
   webapp_ui_react, webhook_listener
1: consul_server, db_core, redis_core, reverse_proxy, skywalking
2: authentik, db_init, docker_stats_exporter
3: controller, webapp_server, worker_db
4: worker_io
```

The example's complete-looking array at lines 53-61 has seven waves, names only 17 Realizations, and omits `cadvisor`, `registry_lightweight`, `github_runner`, `github_runner_webhook`, and `webhook_listener`. It puts edge-free `webapp_ui_react`, `otel_aggregator`, and `otel_collector_node` after wave 0. It puts `skywalking` in wave 3 despite its enabled `store = "vault"` secret (`infra-global/skywalking/ciu.stack.toml:118`) creating a direct edge to wave-0 Vault. It puts `reverse_proxy` last although its upstream binds are all `wait = "none"`; its only literal dependency here is mesh readiness. The example's `[resolved.gates.2]` commentary consequently attributes `db_core` to wave 2 rather than literal wave 1.

Because S8 does not fully define level orientation and collapsed self-edges, I cannot call this the unique conforming list; that non-uniqueness is itself T-14. No plausible interpretation explains omission of five selected Realizations.

### `prod3` publication table

`host_port` defaults to endpoint `port` for derived S7.4.1 publications. `declared host` rows come from `publish = "host"`; `derived mesh` rows exist because at least one cross-host binding with data targets the endpoint.

| Host | Endpoint | Publication | Host port → container port/protocol |
|---|---|---|---|
| gstammtisch | `vault.vault.api` | derived mesh `100.64.0.11` | 8200 → 8200/tcp |
| gstammtisch | `consul_server.consul.http` | declared host `127.0.0.1` | 8500 → 8500/tcp |
| gstammtisch | `redis_core.redis.redis` | derived mesh `100.64.0.11` | 6379 → 6379/tcp |
| gstammtisch | `db_core.postgres.sql` | derived mesh `100.64.0.11` | 5432 → 5432/tcp |
| gstammtisch | `db_core.minio.s3` | derived mesh `100.64.0.11` | **9000 → 9000/tcp** |
| gstammtisch | `authentik.server.http` | derived mesh `100.64.0.11` | **9000 → 9000/tcp** |
| rs1002 | `controller.controller.http` | derived mesh `100.64.0.12` | 8083 → 8080/tcp |
| rs1002 | `webapp_server.server.http` | derived mesh `100.64.0.12` | 8081 → 8080/tcp |
| rs1002 | `webapp_ui_react.ui.http` | derived mesh `100.64.0.12` | 8082 → 80/tcp |
| rs1002 | `skywalking.oap.grpc` | derived mesh `100.64.0.12` | 11800 → 11800/tcp |
| rs1002 | `cadvisor.cadvisor.http` | declared host `0.0.0.0` | 8080 → 8080/tcp |
| rs1002 | `docker_stats_exporter.exporter.metrics` | declared host `0.0.0.0` | 9558 → 9558/tcp |
| rs1002 | `otel_aggregator.collector.otlp_grpc` | declared host `0.0.0.0` | 4317 → 4317/tcp |
| rs1002 | `otel_aggregator.collector.otlp_http` | declared host `0.0.0.0` | 4318 → 4318/tcp |
| rs1002 | `otel_aggregator.collector.metrics` | declared host `0.0.0.0` | 8888 → 8888/tcp |
| rs1002 | `otel_aggregator.collector.health` | declared host `0.0.0.0` | 13133 → 13133/tcp |
| rs1002 | `otel_collector_node.collector.otlp_grpc` | declared host `0.0.0.0` | 4319 → 4317/tcp |
| rs1002 | `otel_collector_node.collector.otlp_http` | declared host `0.0.0.0` | 4320 → 4318/tcp |
| rs1002 | `otel_collector_node.collector.metrics` | declared host `0.0.0.0` | 8889 → 8888/tcp |
| rs1002 | `otel_collector_node.collector.health` | declared host `0.0.0.0` | 13134 → 13133/tcp |
| tsstammtisch | `reverse_proxy.nginx.https` | declared host `0.0.0.0` | 443 → 443/tcp |
| tsstammtisch | `registry_lightweight.tls_proxy.tls` | declared host `0.0.0.0` | 5443 → 443/tcp |
| tsstammtisch | `github_runner_webhook.webhook.http` | declared host `0.0.0.0` | 9001 → 9000/tcp |
| tsstammtisch | `webhook_listener.webhook.http` | declared host `0.0.0.0` | 9000 → 9000/tcp |

The bold rows collide on the same address, protocol, and port. Consul's row does not collide, but it is unreachable by the remote address CIU derives. The resolved example does not include a publication table to compare row-for-row despite S15.4 requiring `ciu check --layout` to print one; its three displayed resolutions are consistent only because none is Consul or the colliding Authentik/MinIO pair.

### Other demo/spec discrepancies

- Multi-service stacks violate literal Compose-project uniqueness (T-01).
- Every real host/proxy FQDN violates the declared scalar grammar (T-02).
- The DB-core hook uses the omitted `service` key (T-03).
- The judge declaration, assay argv, lane caps, and assay environment inventory make the demo gate fail before execution (T-08, T-12).
- README line 74 says worker bindings make both mock targets gate providers, but no worker binds `probe_targets`; the release lane's LogicalService health checks only primary `dns` (T-15).
- The five-entry vendor-image list and one stack with build contexts cannot classify/build the demo's image set (T-29).
- `examples/minimal/` omits the `.gitignore` that S19 says `ciu init` writes, and its claimed `--stack web` output contains facts that command cannot derive (section 5 and T-06).

## 5 Minimal project from the spec alone

I constructed the smallest one-stack, one-host-lane project rather than copying Appendix B. Two values cannot be derived. **ASSUMPTION:** the user chose vendor image `nginx:1.27` and test command `python -m pytest -q`; they are inputs, not facts `ciu init --stack web` can know.

`.gitignore` (S2.3 and the default S16.2 evidence directory):

```gitignore
ciu.resolved.toml
ciu.instance.toml
ciu.instance.generated.toml
ciu.instance.json
ciu.compose.yml
ciu.state.toml
ciu.rendered/
ciu.secret-copy.*
ciu.secrets.toml
ciu.hosts.toml
ciu.gate.*
ciu.env
ciu-data/
ciu-gate-evidence/
```

`ciu.toml`:

```toml
[project]
name = "hello"
revision = 8
vendor_images = ["nginx"]

[realness]
default = "live"

[service.web]
live = "web"

[realization.web]
kind = "ciu_stack"
location = "web"

[bundles.all]
services = ["web"]

[layouts.local]
hosts.localhost = { bundles = ["all"], reach = ["instance"] }

[testing.lanes.unit]
kind = "command"
environment = "host"
argv = ["python", "-m", "pytest", "-q"]
```

`ciu.hosts.toml`:

```toml
[hosts.localhost]
local = true
```

`web/ciu.stack.toml`:

```toml
[ciu_stack.web]
image = "nginx:1.27"
```

`web/ciu.compose.yml.j2`:

```yaml
services:
  web:
    image: {{ ciu_stack.web.image }}
```

After these declarations exist, `ciu instance init` is supposed to create `ciu.instance.toml`, `ciu.instance.generated.toml`, and the registry record; render creates the resolved and Compose artifacts. Those are outputs, not files an author should invent.

Gaps encountered:

1. S19.2 makes `--from-compose FILE` optional but says it copies each service's image. Without the file there is no image, service list, endpoint, port, health command, or build context to copy. The example invents all of Nginx, `1.27`, port 80, host port 8080, a curl healthcheck, and a Pytest lane.
2. S19.1 says plain `ciu init` writes realness/layout/bundle tables but no Realization; S16.11 then rejects that output (T-06). `--gate-only` is coherent, so it should be the behavior of bare init unless deployment inputs are supplied.
3. S19.2 never says `--stack` adds a test lane. The example comment says the whole file is written by `ciu init --stack web`, but its `unit` lane is not derivable and is not specified as scaffold policy.
4. S19 says init writes the S2.3 ignore patterns. `examples/minimal/` contains no `.gitignore`, so the shown directory is not the actual output and would fail check stage 1 in a Git worktree.
5. The spec does not define normalization or refusal when `DIR`'s basename is not a valid `name`, nor how two directory basenames that normalize alike are handled.
6. An image literal does not say whether it is vendor-owned or project-built. I had to add `vendor_images = ["nginx"]` based on outside knowledge; this is T-29.
7. No endpoint or healthcheck is necessary for this literal smallest deploy because nothing binds the service and the host lane does not require it. The example's endpoint/healthcheck is useful policy, but it is not a consequence of the spec. If every deployable primary is intended to be healthy rather than merely Running, S8/S11 must say so (T-15).

Diff against `examples/minimal/`: the four core declarations agree after supplying the same arbitrary image; my stack omits the example's non-derived endpoint and healthcheck, adds explicit image ownership, uses a test command identified as a user decision, and includes the required `.gitignore`. The example therefore demonstrates a reasonable hand-authored project, not reproducible `ciu init --stack web` output.

## 6 Not verified

- There is no v8 implementation. I could not run `ciu check`, `render`, `up`, `gate`, `push`, `activate`, `migrate`, or schema generation. The review is against draft.3's normative text and mechanically parsed demo declarations.
- I parsed all demo TOML files and inventoried all 27 stack declarations. I did not render every Jinja template because no v8 resolver/context exists; I statically inspected the Compose templates for prohibited/service-reference shapes. Exact generated YAML beyond the rules in S11 remains unverified.
- I did not contact the demo's remote hosts, Docker daemons, Vault, registries, or secret sources. Publication reachability and collision results are declaration-level consequences, not live probes.
- I did not benchmark cgroup behavior on the eventual CIU-supported Docker/Compose/kernel versions. T-22 compares the specified mapping with the documented controller and Compose semantics; exact daemon conversions must be verified by readback tests.
- I could not test directory `flock` on the author's actual filesystem, NFS server, Docker Desktop VM, macOS, or Windows. T-19 does not assume it always fails; it rejects the stronger claim that one successful local call proves cross-client exclusion.
- I read the named v8 documents, the required run-gate rules, the demo, and the relevant v7 replacement sections. I did not execute a v7→v8 migration because only a design exists, so Alternative G's behavioral comparison remains a proposed oracle.
- **ASSUMPTION (wave derivation):** consumer→provider orientation, dependency-first level numbering, collapsed sibling self-edges discarded, cross-host `wait = "none"` data resolutions still create network edges, and enabled Vault-stored secrets create `secret→vault` edges. S8 must remove the need for these assumptions.
- **ASSUMPTION (publication table):** absent `host_port` on a derived publication means the endpoint's `port`, and HTTP/HTTPS use TCP, per S6.3/S7.4. An implementation cannot otherwise form the mandated mapping.
- **ASSUMPTION (realness):** `prod3` uses the global `live` default because the supplied generated file records only `local`; `local` uses its recorded `probe_targets=simulated`. None of the three selected binding examples depends on that differing capability.
- **ASSUMPTION (minimal project):** `nginx:1.27` and the Pytest argv were explicit user choices. They were selected only to make the files concrete and are not claimed as legitimate defaults.

Adversarial probes that did not produce separate findings: S6.4.6 explicitly rejects environment-variable collisions between a binding, another binding, and an env-delivered secret; per-service delivery means two services may bind the same target differently without sharing an injected namespace. S10.1/S12.3 explicitly reject an unsatisfied `from = "hook"` key and a hook overwriting a differently sourced declared key. S9.4.2 defines committed-pin/CLI-override conflicts with an existing realness record, and S9.5.2-S9.5.3 refuse changed/down joined instances (their stale evidence weakness is T-16). S16.5.4 defines propagation and first-non-PASS behavior for sequences; a nested acyclic sequence appears legal. A syntactically invalid instance file cannot be round-trip parsed and should fail before `instance add`, although that ordering should be stated. A caller's shell pipeline still needs `pipefail` or `${PIPESTATUS[...]}`; CIU cannot control the shell, while T-20 covers CIU's own obligation to take container status from `docker wait`.

## 7 Machine summary

```json
[
  {
    "id": "T-01",
    "severity": "BLOCKER",
    "where": ["S4.2.1", "S4.3.1"],
    "claim": "A multi-service Realization cannot satisfy both Compose-project identity rules.",
    "challenges_settled": false,
    "has_alternative": false
  },
  {
    "id": "T-02",
    "severity": "BLOCKER",
    "where": ["S1.4", "S7.2", "S7.3"],
    "claim": "The fqdn fields use a hostname grammar that forbids dots.",
    "challenges_settled": false,
    "has_alternative": false
  },
  {
    "id": "T-03",
    "severity": "BLOCKER",
    "where": ["S3.8.1", "S6.10", "S12.1"],
    "claim": "Structured hooks require a service key that their closed schema omits.",
    "challenges_settled": false,
    "has_alternative": false
  },
  {
    "id": "T-04",
    "severity": "BLOCKER",
    "where": ["S3.5.4", "S6.4.5", "S7.3", "S7.3.2", "S7.6.5", "S8.2-S8.3", "S9.3.4"],
    "claim": "Legal network-provider, per-host, and empty-mock selections erase target information required by readiness or data delivery.",
    "challenges_settled": true,
    "has_alternative": false
  },
  {
    "id": "T-05",
    "severity": "BLOCKER",
    "where": ["S6.4", "S8.2", "S8.5.1"],
    "claim": "The wave gate erases the declared distinction between started and healthy waits.",
    "challenges_settled": false,
    "has_alternative": false
  },
  {
    "id": "T-06",
    "severity": "BLOCKER",
    "where": ["S16.11.1", "S19.1", "S19.2"],
    "claim": "Ordinary initialization is specified to emit an invalid zero-instance project and to copy absent stack facts.",
    "challenges_settled": true,
    "has_alternative": false
  },
  {
    "id": "T-07",
    "severity": "BLOCKER",
    "where": ["S16.9", "S18.4", "R-49"],
    "claim": "LaneResult both must and must not contain the universal status field.",
    "challenges_settled": false,
    "has_alternative": false
  },
  {
    "id": "T-08",
    "severity": "BLOCKER",
    "where": ["S16.3", "S16.7.1", "S16.7.2", "run-gate R-34", "run-gate R-38"],
    "claim": "The mandated assay command is malformed and the permitted judge floor can lack its required API.",
    "challenges_settled": false,
    "has_alternative": false
  },
  {
    "id": "T-09",
    "severity": "BLOCKER",
    "where": ["S10.1", "S10.6.4", "S17.3.1"],
    "claim": "Per-host secret transport has no coherent stored source on the sender or consuming source on the target.",
    "challenges_settled": true,
    "has_alternative": true
  },
  {
    "id": "T-10",
    "severity": "BLOCKER",
    "where": ["S6.2", "S7.4", "S7.8", "S11.4"],
    "claim": "Host-network services are assigned unreachable instance-network names and unsupported port mappings.",
    "challenges_settled": false,
    "has_alternative": false
  },
  {
    "id": "T-11",
    "severity": "BLOCKER",
    "where": ["S2.3.1", "S2.3.4", "S3.1.4", "S14.4.5"],
    "claim": "The promised recovery after git clean deletes non-derivable identity and state inputs.",
    "challenges_settled": true,
    "has_alternative": false
  },
  {
    "id": "T-12",
    "severity": "MAJOR",
    "where": ["S15.3", "S16.4.5", "S16.6.4", "demo ciu.toml:497-599"],
    "claim": "The demo gate is statically inadmissible under its own resource and environment checks.",
    "challenges_settled": false,
    "has_alternative": false
  },
  {
    "id": "T-13",
    "severity": "MAJOR",
    "where": ["S7.4.1-S7.4.5", "demo ciu.site.toml:18-23"],
    "claim": "The prod3 demo contains an unreachable Consul publication, a real mesh port collision, and unused admission data.",
    "challenges_settled": false,
    "has_alternative": false
  },
  {
    "id": "T-14",
    "severity": "MAJOR",
    "where": ["S8.2-S8.4", "S8.8", "demo resolved example:53-61"],
    "claim": "The example wave list is incomplete and cannot be derived from the stated graph algorithm.",
    "challenges_settled": false,
    "has_alternative": false
  },
  {
    "id": "T-15",
    "severity": "MAJOR",
    "where": ["S5.3", "S8.5.1", "S8.5.5", "S11.5"],
    "claim": "CIU can report success without checking selected leaf capabilities or failed unconsumed one-shots.",
    "challenges_settled": true,
    "has_alternative": false
  },
  {
    "id": "T-16",
    "severity": "MAJOR",
    "where": ["S5.6", "S8.1", "S8.5.3", "R-24"],
    "claim": "Cross-host reachability and external assertions are mislabeled as proof of TypedFacts.",
    "challenges_settled": true,
    "has_alternative": true
  },
  {
    "id": "T-17",
    "severity": "MAJOR",
    "where": ["S4.2-S4.5", "S15.3 stage 8"],
    "claim": "Identity checks do not cover every collision namespace or prove resource ownership.",
    "challenges_settled": true,
    "has_alternative": false
  },
  {
    "id": "T-18",
    "severity": "MAJOR",
    "where": ["S4.1", "S14.2", "S14.6-S14.7", "R-46"],
    "claim": "Moving a worktree changes its identity while its supposed family registry moves with it.",
    "challenges_settled": true,
    "has_alternative": false
  },
  {
    "id": "T-19",
    "severity": "MAJOR",
    "where": ["S9.5.4", "S14.4", "S16.5.3", "R-42"],
    "claim": "The lock order can deadlock and checkout-local gate locks can split after unlink.",
    "challenges_settled": true,
    "has_alternative": false
  },
  {
    "id": "T-20",
    "severity": "MAJOR",
    "where": ["S16.4", "S16.12", "run-gate R-15", "run-gate R-19a", "run-gate R-23", "R-56"],
    "claim": "The gate lift regresses detached status capture, writable Git isolation, and dual mounts.",
    "challenges_settled": false,
    "has_alternative": false
  },
  {
    "id": "T-21",
    "severity": "MAJOR",
    "where": ["S16.5.3", "S16.6", "S16.9"],
    "claim": "Gate admission is racy, result writes collide, and exec lane caps are not enforceable as stated.",
    "challenges_settled": true,
    "has_alternative": true
  },
  {
    "id": "T-22",
    "severity": "MAJOR",
    "where": ["S13.1-S13.4", "S16.6"],
    "claim": "The specified Compose fields are not a faithful application of the named cgroup-v2 controls.",
    "challenges_settled": true,
    "has_alternative": false
  },
  {
    "id": "T-23",
    "severity": "MAJOR",
    "where": ["S10.2.3-S10.2.5", "S12.2", "S12.4"],
    "claim": "Hooks receive ambient authority, validation has an unenforceable side-effect promise, and same-phase state visibility is undefined.",
    "challenges_settled": true,
    "has_alternative": true
  },
  {
    "id": "T-24",
    "severity": "MAJOR",
    "where": ["S10.6.3-S10.7", "S11.4"],
    "claim": "In-place secret refresh can expose an empty or partial value to running readers.",
    "challenges_settled": false,
    "has_alternative": true
  },
  {
    "id": "T-25",
    "severity": "MAJOR",
    "where": ["S17.1-S17.5", "demo ciu.hosts.toml:24-64"],
    "claim": "Push mutates current in place and the documented rollback cannot restore a prior release.",
    "challenges_settled": true,
    "has_alternative": true
  },
  {
    "id": "T-26",
    "severity": "MAJOR",
    "where": ["S6.3.2", "S7.4.5", "S14.7.1"],
    "claim": "The port collision key rejects legal protocol pairs and misses conflicting host sockets.",
    "challenges_settled": false,
    "has_alternative": false
  },
  {
    "id": "T-27",
    "severity": "MAJOR",
    "where": ["S4.2", "S11.2-S11.5"],
    "claim": "Renaming Compose services leaves allowed embedded service references dangling.",
    "challenges_settled": false,
    "has_alternative": false
  },
  {
    "id": "T-28",
    "severity": "MAJOR",
    "where": ["S2.4.1-S2.4.2", "S3.1.2"],
    "claim": "The lexical secret scan has trivial false negatives and positives but is presented as certification.",
    "challenges_settled": false,
    "has_alternative": false
  },
  {
    "id": "T-29",
    "severity": "MAJOR",
    "where": ["S3.4.3", "S6.2", "S17.6", "demo ciu.toml:30"],
    "claim": "The demo does not completely declare which images are vendor-owned and which CIU can build.",
    "challenges_settled": true,
    "has_alternative": false
  },
  {
    "id": "T-30",
    "severity": "MINOR",
    "where": ["S6.8-S6.9", "S11.4"],
    "claim": "Config-directory checks reject harmless cross-service paths while image symlinks and hostdir seeding remain undefined.",
    "challenges_settled": false,
    "has_alternative": false
  },
  {
    "id": "T-31",
    "severity": "MINOR",
    "where": ["S16.4.2", "S16.11"],
    "claim": "Zero-instance gates cannot declare a legitimate externally managed integration-test endpoint.",
    "challenges_settled": true,
    "has_alternative": false
  },
  {
    "id": "T-32",
    "severity": "MINOR",
    "where": ["S16.2.1"],
    "claim": "Testing inheritance is unnecessarily shallow and leaves inherited relative-path ownership ambiguous.",
    "challenges_settled": true,
    "has_alternative": false
  },
  {
    "id": "T-33",
    "severity": "MINOR",
    "where": ["S3.7.1", "S12.3", "S16.9", "S18.4", "R-49"],
    "claim": "One schema number is overloaded across unrelated machine artifacts with independent compatibility needs.",
    "challenges_settled": true,
    "has_alternative": false
  },
  {
    "id": "T-34",
    "severity": "MINOR",
    "where": ["S7.4.6", "S7.5-S7.5.1", "S8.6.4 references"],
    "claim": "Bundle recursion, publication recording, and health cross-references each lack one implementable definition.",
    "challenges_settled": false,
    "has_alternative": false
  },
  {
    "id": "T-35",
    "severity": "NOTE",
    "where": ["S3-S7", "S18.4", "R-68", "R-72"],
    "claim": "Plain TOML is defensible, but overloaded concepts and a coarse resolved artifact make adoption and machine use harder than necessary.",
    "challenges_settled": false,
    "has_alternative": true
  }
]
```
