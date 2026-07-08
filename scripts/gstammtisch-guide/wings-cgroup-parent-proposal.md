# Proposal: cgroup-parent / systemd-slice support in Wings

Audience: Pterodactyl / Pelican maintainers and node operators. This document
proposes first-class support for placing Wings-created containers under named
systemd slices (`HostConfig.CgroupParent`), as a staged path from a one-key node
config option up to panel-managed per-server resource guarantees.

Provenance: developed against `pterodactyl/wings` tag `v1.13.1`. The v1 patch in §2
and the runtime portion of the v2 sketch in §3 were applied to a clean clone and
**compiled, vetted and smoke-tested** (`go build ./...`, `go vet`, binary runs)
against the exact dependency versions pinned in `go.mod` (`github.com/docker/docker
v28.3.3+incompatible`, Go 1.24). The upstreamable v2 shape now also requires the
installer/helper/guard amendments called out in §3b. Claims are marked verified vs.
inferred throughout; Appendix B lists the method. A real-world deployment
(single-node 16 GB game host) is used as a worked example, confined to Appendix A.

Revision note: 2026-07-08: folded external review — installer coverage (v2),
namespace/allowlist guard (v2), missing-slice runbook checks, v2.5 reconciliation
semantics, env-visibility clarification, effort/options assessment.

---

## 0. Motivation — resource guarantees are a product feature, not host tuning

Pterodactyl and Pelican host **arbitrary workloads** (any egg, any game, any
runtime), very often **multi-tenant**: hosting providers selling individual game
servers to customers, sharing nodes among many tenants, and deliberately
oversubscribing memory because most servers idle most of the time. In that setting,
two things are product-level features, not per-host tuning:

- **Limits** — a noisy tenant must not degrade the others. The panel already sells
  these: per-server memory / CPU / IO settings that Wings maps to Docker resource
  limits (`memory.max`, CPU quota, IO weight).
- **Guarantees** — "the 8 GB server you pay for actually gets its 8 GB, even when
  the node is busy." The panel **cannot** sell these today, because nothing in the
  Wings → Docker pipeline can express a protection floor.

### The technical gap, precisely

On cgroup v2, guarantees are `memory.min` / `memory.low` — reclaim-protection
floors. Two hard facts make them unreachable for Wings servers today:

1. **Docker's API cannot express a floor.** Verified against the complete
   `container.Resources` struct in `moby/moby v28.3.3`
   (`api/types/container/hostconfig.go`): it offers `Memory` (→ `memory.max`),
   `MemoryReservation` ("soft limit", mapped by runc to `memory.low` on cgroup v2),
   swap, CPU, blkio, pids — and **no `memory.min` field of any kind, and no zswap
   knobs**. Wings already sends `MemoryReservation`, which is as far as the Docker
   API goes.
2. **Protection is hierarchical, and the hierarchy is broken by default.** A
   cgroup's *effective* floor is capped by **every ancestor** on the path to the
   root. Wings sets no `CgroupParent`, so with the systemd cgroup driver every
   server container lands directly in `system.slice` — which has `memory.min=0` /
   `memory.low=0` by default. Even the `memory.low` that Wings *does* send via
   `MemoryReservation` is therefore **arithmetically zeroed** by its parent: a 6 GB
   floor under `system.slice(min=0)` protects nothing against global reclaim. This
   is not theory — it was verified live on a production node (Appendix A,
   Finding A).

The consequence for the product: under memory pressure (the normal operating state
of an oversubscribed multi-tenant node), the kernel reclaims from whichever server
is cheapest to reclaim from — not from the tenant causing the pressure. Paying
customers get latency spikes caused by their neighbors, and operators have no
supported knob to prevent it.

### What `CgroupParent` support unlocks

Placing Wings containers under named, resource-configured systemd slices makes the
ancestor chain an asset instead of a bug:

- **Per-server QoS**: each server (or tier of servers) under a slice whose
  `MemoryMin` / `MemoryLow` / `MemoryHigh` / `CPUWeight` / `IOWeight` the operator
  (or eventually the panel) controls — real floors, real ceilings, real weights.
- **Tenant isolation**: a runaway server exhausts its own slice, not the node.
  Premium tiers can be given floors; best-effort tiers can be capped.
- **Node stability under oversubscription**: the Wings workload as a whole can be
  bounded by one parent slice, so game load can never starve sshd / dockerd /
  Wings itself — and vice versa, host services no longer share a protection domain
  with tenant workloads.
- **Observability for free**: cgroup v2 PSI (`memory.pressure`, `cpu.pressure`,
  `io.pressure`) and accounting per slice — per-server and per-tier — with no
  process tracking.

There is an additional durability argument for putting these values on *slices*
specifically, rather than having anything write them to the container's cgroup
directly: raw writes to a Docker scope's cgroup files are **silently wiped by any
`systemctl daemon-reload`** (systemd re-applies its recorded properties to every
transient unit; any apt/yum package that ships a unit triggers a reload). This
failure mode was proven live on a production node (Appendix A, Finding D).
Slice-held values are systemd-owned and get *re-applied*, not wiped, on reload.

> **Worked example.** A single-node 16 GB host running one large game server plus
> dev/test workloads hit exactly this wall: floors configured on the container's
> cgroup were live-verified ineffective (ancestor `memory.min=0`) and separately
> wiped by a daemon-reload. Appendix A walks through the full case, including
> before/after cgroup paths and the deployment runbook for a patched Wings.

---

## 1. TL;DR

- Wings v1.13.1 has no `CgroupParent` support anywhere. Confirmed by source read:
  `environment/docker/container.go` `Create()` and `server/install.go` `Execute()`
  both build `container.HostConfig{}` without it; `config/config_docker.go` has no
  cgroup-related key (`grep -i cgroup` across the module: zero hits).
- **v1 (this proposal's core patch, ~65 lines, 4 files, compiled + vetted):** a new
  node-level `docker.cgroup_parent` config key applied to all server and installer
  containers, validated at startup (`.slice` suffix). §2.
- **v2 (same PR or follow-up only with the guardrails in §3):** per-server
  placement via a reserved, admin-only egg/server variable `WINGS_CGROUP_PARENT` —
  no panel CODE changes needed, but panel data must still be updated (egg
  reimport/update, variable creation, and per-server overrides for existing
  servers). The variable is transport only: `user_viewable=false` /
  `user_editable=false` hides it from the tenant panel UI/API, not from the running
  process. §3.
- **v2.5 (design, wings-only):** an "advanced egg" carries a full per-server cgroup
  spec in structured admin-only variables; Wings creates a **transient per-server
  slice via the systemd D-Bus API** (`StartTransientUnit` / `SetUnitProperties` —
  the daemon-reload-safe channel) and places the container in it. Full
  floors/ceilings/weights per server with **no panel CODE changes**; requires the
  host systemd/D-Bus socket in the Wings container. §4.3.
- **v3 (vision):** proper panel/egg schema for slice properties + Wings-managed
  slices — the multi-node operator story with first-class UI. §4.4.
- Transport constraint that shapes v2/v2.5 (verified against panel source): Wings
  never sees raw eggs — the panel sends a **hard-coded whitelist** of per-server
  fields (`ServerConfigurationStructureService`, comment: "DO NOT MODIFY THIS
  FUNCTION"), and Wings wholesale-replaces its local server config from the panel
  on every sync/start. Arbitrary new egg JSON fields do not reach the node, and
  node-local config edits do not survive. **Egg variables are the one
  admin-extensible channel that does reach Wings**, resolved into the container env
  map that `Create()` already reads. §4.2.
- Why slices at all, instead of pushing everything through the Docker API: no
  `memory.min` in the API, raw cgroup writes wiped on daemon-reload, and the
  hierarchical floor math requires ancestor budgets regardless. Egg-level values
  and host-side structure are complements, not alternatives. §6.
- Landing strategy: `pelican-dev/wings` is currently the more receptive target for
  v1 first, with v2 in the same PR only if the §3 guardrails are included;
  submitting to `pterodactyl/wings` in parallel costs nothing. §7.

---

## 2. v1 — node-level `docker.cgroup_parent` (compiled patch)

### Design

One new key in the node's `config.yml`:

```yaml
docker:
  cgroup_parent: wings.slice
```

- Empty (default) → behavior unchanged: Docker's normal placement, i.e.
  `system.slice` under the systemd cgroup driver.
- Non-empty → every server container **and every installer container** created from
  then on gets `HostConfig.CgroupParent` set to this value.
- Validated at Wings startup (`cmd/root.go` `initConfig()`, right after
  `config.FromFile`): no whitespace/control characters, must end in `.slice`
  (systemd driver naming convention). For this node-owner config key, suffix-only
  validation is intentionally sufficient. Invalid value → Wings refuses to start with a
  clear error, matching the existing `log2.Fatalf` config-error pattern in that
  function. Wings does **not** query Docker's `Info()` for the daemon's cgroup
  driver (see "not implemented" below).
- **Existing containers are not moved.** `Create()` returns early when
  `ContainerInspect` finds the container already exists (confirmed by source read);
  a running server picks up a changed `cgroup_parent` only after its container is
  removed and re-created. Operationally a brief, planned stop/start per server
  (worked example: Appendix A, deployment step 5).

### Verified source pointers (v1.13.1)

| File | What's there today |
|---|---|
| `environment/docker/container.go:227-260` | `Create()` builds `HostConfig{PortBindings, Mounts, Tmpfs, Resources, DNS, LogConfig, SecurityOpt, ReadonlyRootfs, CapDrop, NetworkMode, UsernsMode}` — no `CgroupParent`. |
| `server/install.go:428-451` | `Execute()` (installer container) builds a second, separate `HostConfig{}` — also no `CgroupParent`. |
| `config/config_docker.go:49-98` | `DockerConfiguration` struct — no cgroup-related field of any kind. |
| `environment/settings.go:105-137` | `Limits.AsContainerResources()` builds the *embedded* `container.Resources` (Memory, Swap, CPU, block-IO weight, pids) that `HostConfig` embeds anonymously. `CgroupParent` is a sibling field on that same embedded `Resources` struct (confirmed against `moby/moby v28.3.3`), so it can be set via field promotion on the constructed `*container.HostConfig` (`hostConf.CgroupParent = "..."`). |
| `cmd/root.go:395-414` | `initConfig()` — loads config, `log2.Fatalf`s on error. Natural home for startup-time validation. |

### Patch (exact diff that compiled + vetted; comments/examples would be lightly genericized for the actual PR)

```diff
diff --git a/cmd/root.go b/cmd/root.go
index f411c53..00dce6a 100644
--- a/cmd/root.go
+++ b/cmd/root.go
@@ -408,6 +408,9 @@ func initConfig() {
 		}
 		log2.Fatalf("cmd/root: error while reading configuration file: %s", err)
 	}
+	if err := config.Get().Docker.ValidateCgroupParent(); err != nil {
+		log2.Fatalf("cmd/root: invalid docker configuration: %s", err)
+	}
 	if debug && !config.Get().Debug {
 		config.SetDebugViaFlag(debug)
 	}
diff --git a/config/config_docker.go b/config/config_docker.go
index 95501e7..5ae0896 100644
--- a/config/config_docker.go
+++ b/config/config_docker.go
@@ -7,6 +7,7 @@ import (
 	"sort"
 	"strings"
 
+	"emperror.dev/errors"
 	"github.com/distribution/reference"
 	"github.com/docker/docker/api/types/container"
 	"github.com/docker/docker/api/types/registry"
@@ -91,6 +92,28 @@ type DockerConfiguration struct {
 	// remapping disabled
 	UsernsMode string `default:"" json:"userns_mode" yaml:"userns_mode"`
 
+	// CgroupParent sets the parent cgroup that every server and installer
+	// container is created under (Docker HostConfig.CgroupParent). Leave
+	// blank (default) to keep Docker's normal placement, which is typically
+	// system.slice when the daemon uses the systemd cgroup driver.
+	//
+	// This exists so that game server containers can be isolated under a
+	// dedicated, admin-managed systemd slice (e.g. "soulmask.slice") instead
+	// of system.slice, so that cgroup v2 memory.min/memory.low protection
+	// chains can be scoped to game workloads without applying them to
+	// system.slice as a whole.
+	//
+	// Requirements NOT validated beyond the ".slice" suffix check below:
+	//   - Docker must be running with the systemd cgroup driver
+	//     (daemon.json: "exec-opts": ["native.cgroupdriver=systemd"]).
+	//   - The named slice unit must already be installed under
+	//     /etc/systemd/system and loaded (systemctl daemon-reload). If it is
+	//     not, systemd will silently create a transient slice with none of
+	//     the intended resource limits applied, instead of failing loudly:
+	//     the cgroup path can look correct while the guarantees are absent.
+	//   - Existing containers are NOT moved when this value changes; it only
+	//     affects containers created after Wings picks up the new config.
+	CgroupParent string `default:"" json:"cgroup_parent" yaml:"cgroup_parent"`
+
 	LogConfig struct {
 		Type   string            `default:"local" json:"type" yaml:"type"`
 		Config map[string]string `default:"{\"max-size\":\"5m\",\"max-file\":\"1\",\"compress\":\"false\",\"mode\":\"non-blocking\"}" json:"config" yaml:"config"`
@@ -108,6 +131,34 @@ func (c DockerConfiguration) ContainerLogConfig() container.LogConfig {
 	}
 }
 
+// ValidateCgroupParent performs a best-effort sanity check on the configured
+// CgroupParent (or an ad-hoc per-server override value, see
+// ValidateCgroupParentValue). Wings has no dependency on systemd and cannot
+// confirm the referenced slice unit is actually loaded on the host --
+// Docker/runc will surface that error at container creation time if the
+// cgroup driver is systemd and the unit does not exist. This only guards
+// against obviously wrong values (e.g. a cgroupfs-style absolute path, or an
+// empty segment) being silently accepted.
+func (c DockerConfiguration) ValidateCgroupParent() error {
+	return ValidateCgroupParentValue(c.CgroupParent)
+}
+
+// ValidateCgroupParentValue is the standalone check used both for the
+// node-wide docker.cgroup_parent setting and for any per-server override
+// value, e.g. one sourced from a reserved egg/server environment variable.
+func ValidateCgroupParentValue(v string) error {
+	if v == "" {
+		return nil
+	}
+	if strings.ContainsAny(v, "\x00\n\r") || strings.TrimSpace(v) != v {
+		return errors.New("cgroup parent must not contain whitespace or control characters: " + v)
+	}
+	if !strings.HasSuffix(v, ".slice") {
+		return errors.New("cgroup parent must end in \".slice\" (systemd cgroup driver naming convention): " + v)
+	}
+	return nil
+}
+
 // RegistryCredentialsForImage returns registry credentials for an image only
 // when the configured registry and image reference share the same registry
 // identity.
diff --git a/environment/docker/container.go b/environment/docker/container.go
index f503af1..512d1a3 100644
--- a/environment/docker/container.go
+++ b/environment/docker/container.go
@@ -259,6 +259,12 @@ func (e *Environment) Create() error {
 		UsernsMode:  container.UsernsMode(cfg.Docker.UsernsMode),
 	}
 
+	// Place the container under the node-wide cgroup parent (systemd slice) if
+	// one is configured. See config.DockerConfiguration.CgroupParent.
+	if cfg.Docker.CgroupParent != "" {
+		hostConf.CgroupParent = cfg.Docker.CgroupParent
+	}
+
 	if _, err := e.client.ContainerCreate(ctx, conf, hostConf, nil, nil, e.Id); err != nil {
 		return errors.Wrap(err, "environment/docker: failed to create container")
 	}
diff --git a/server/install.go b/server/install.go
index 0d31d50..5275add 100644
--- a/server/install.go
+++ b/server/install.go
@@ -450,6 +450,12 @@ func (ip *InstallationProcess) Execute() (string, error) {
 		UsernsMode:  container.UsernsMode(cfg.Docker.UsernsMode),
 	}
 
+	// Keep the installer container under the same cgroup parent as the
+	// server it is installing, for consistency (see config.DockerConfiguration.CgroupParent).
+	if cfg.Docker.CgroupParent != "" {
+		hostConf.CgroupParent = cfg.Docker.CgroupParent
+	}
+
 	// Ensure the root directory for the server exists properly before attempting
 	// to trigger the reinstall of the server. It is possible the directory would
 	// not exist when this runs if Wings boots with a missing directory and a user
```

### Not implemented (flagged, deliberately out of scope for this patch)

- No call to Docker's `client.Info(ctx)` to check `CgroupDriver`/`CgroupVersion`
  (both confirmed present on `moby/moby`'s `system.Info` struct) and warn when the
  daemon isn't using the systemd driver. Cheap to add in review if maintainers want
  it; skipped to keep the patch minimal.
- No re-validation against the live cgroupfs — by design; Docker/systemd already
  fail container creation on a genuinely invalid parent, and the transient-slice
  degradation case (named slice not installed → limit-less transient slice) is a
  documentation/ops concern, not something Wings can reliably detect without a
  systemd dependency. The operational footgun is a false-positive rollout: the
  cgroup path looks right while the intended `MemoryMin`/`MemoryLow`/`MemoryHigh`
  guarantees are absent.
- Possible non-blocking upstream enhancement: when Docker reports the systemd
  cgroup driver and a target slice appears to have no configured properties after
  a smoke-created container, Wings could warn without refusing startup. The v1 PR
  should not require that systemd probe.

---

## 3. v2 — per-server placement via a reserved variable

### How per-server data reaches Wings today (verified)

- Panel → Wings sync: `remote.ServerConfigurationResponse{Settings,
  ProcessConfiguration}` (`remote/types.go`); `Settings` is a `json.RawMessage`
  unmarshaled into `server.Configuration` (`server/configuration.go:24-62`), which
  already carries `Labels map[string]string` (generic passthrough → Docker labels —
  precedent for panel→Wings passthrough) and `Build environment.Limits` (no cgroup
  field today).
- Egg/server variables: `Configuration.EnvVars` is merged with Wings-computed vars
  (`TZ`, `STARTUP`, `SERVER_MEMORY`, `SERVER_IP`, `SERVER_PORT`) in
  `Server.GetEnvironmentVariables()` (`server/server.go:151-174`) and becomes the
  container `Env`.
- **Wings already reads the resolved env list before building `HostConfig`**:
  `Create()` loads `evs := e.Configuration.EnvironmentVariables()` and rewrites one
  value in place (`SERVER_IP=127.0.0.1` → interface IP) *before* `hostConf` is
  constructed. A reserved variable is a natural, precedented extension point.
- Panel side (verified from panel source, single-file reads): egg variables carry
  `user_viewable` / `user_editable` flags and a `RESERVED_ENV_NAMES` list
  (`SERVER_MEMORY,SERVER_IP,SERVER_PORT,ENV,HOME,USER,STARTUP,SERVER_UUID,UUID` —
  `app/Models/EggVariable.php`). `WINGS_CGROUP_PARENT` is not reserved, so an admin
  can define it today — admin-only — with no panel CODE changes. Note the flags are
  enforced by the panel's client-facing UI/API only; Wings receives the resolved map
  regardless (true of every env var Wings consumes today). This is not a secrecy
  boundary: the running game process can read its own environment, so
  `WINGS_CGROUP_PARENT` may carry only non-secret placement/profile metadata.

### (a) Wings-side config.yml map (UUID → slice) — considered, not recommended

`docker.server_cgroup_parents: {<uuid>: <slice>}`: trivial code, but duplicates
panel state on the node (must be maintained on server create/move/delete). Fine as
a stopgap on a single node; not canonical, unlikely to be what upstream wants.

### (b) Reserved variable `WINGS_CGROUP_PARENT` — recommended

Layered on top of the v1 patch. The runtime-only hunk below was compiled during
the first pass, but the upstreamable v2 patch **must** factor the resolution into a
small shared helper used by both `environment/docker/container.go` and
`server/install.go`; otherwise installer containers still use only the node
default and the "per-server placement" claim is overstated.

Required shape:

- Start with `docker.cgroup_parent` as the node default.
- Resolve `WINGS_CGROUP_PARENT` from the already-resolved server environment in a
  helper shared by the runtime create path and the installer create path.
- For v2 only, do not rely on `.slice` suffix validation alone. Require one of:
  `docker.allowed_cgroup_parents` as an explicit allowlist;
  `docker.cgroup_parent` as a required root with child-of-root validation; or a
  hard `wings.slice` / `wings-*.slice` namespace. The namespace guard from §4.5 is
  therefore a v2 requirement, not only a v2.5/v3 rule.
- If an override is invalid, fail closed for that override: fall back to the node
  default and log the server UUID, attempted value, and selected parent.
- Add table-driven validation tests covering empty, default, valid override, and
  invalid override.

Runtime hunk from the compiled first pass:

```diff
diff --git a/environment/docker/container.go b/environment/docker/container.go
index 512d1a3..991c3f7 100644
--- a/environment/docker/container.go
+++ b/environment/docker/container.go
@@ -155,12 +155,31 @@ func (e *Environment) Create() error {
 	cfg := config.Get()
 	a := e.Configuration.Allocations()
 	evs := e.Configuration.EnvironmentVariables()
+	// cgroupParent starts out as the node-wide default and may be overridden
+	// below by a reserved, admin-only server/egg environment variable.
+	cgroupParent := cfg.Docker.CgroupParent
 	for i, v := range evs {
 		// Convert 127.0.0.1 to the pterodactyl0 network interface if the environment is Docker
 		// so that the server operates as expected.
 		if v == "SERVER_IP=127.0.0.1" {
 			evs[i] = "SERVER_IP=" + cfg.Docker.Network.Interface
 		}
+
+		// Allow a per-server cgroup parent override via a reserved egg/server
+		// variable. The upstreamable version must route this through a shared
+		// resolver used by both runtime and installer container creation, and
+		// must enforce an allowlist/root/namespace guard for panel-supplied
+		// values rather than accepting any ".slice" name.
+		if val, ok := strings.CutPrefix(v, "WINGS_CGROUP_PARENT="); ok && val != "" {
+			cgroupParent = val
+		}
+	}
+	if cgroupParent != "" && cgroupParent != cfg.Docker.CgroupParent {
+		if err := config.ValidateCgroupParentValue(cgroupParent); err != nil {
+			e.log().WithField("error", err).WithField("server_uuid", e.Id).WithField("attempted_value", cgroupParent).WithField("selected_parent", cfg.Docker.CgroupParent).Warn("environment/docker: ignoring invalid WINGS_CGROUP_PARENT override, falling back to node default")
+			cgroupParent = cfg.Docker.CgroupParent
+		}
 	}
 
 	// Merge user-provided labels with system labels
@@ -259,10 +278,11 @@ func (e *Environment) Create() error {
 		UsernsMode:  container.UsernsMode(cfg.Docker.UsernsMode),
 	}
 
-	// Place the container under the node-wide cgroup parent (systemd slice) if
-	// one is configured. See config.DockerConfiguration.CgroupParent.
-	if cfg.Docker.CgroupParent != "" {
-		hostConf.CgroupParent = cfg.Docker.CgroupParent
+	// Place the container under the node-wide cgroup parent (systemd slice),
+	// or the per-server override resolved above, if any. See
+	// config.DockerConfiguration.CgroupParent.
+	if cgroupParent != "" {
+		hostConf.CgroupParent = cgroupParent
 	}
 
 	if _, err := e.client.ContainerCreate(ctx, conf, hostConf, nil, nil, e.Id); err != nil {
```

Required installer wiring for the claim to hold:

```diff
diff --git a/server/install.go b/server/install.go
@@
-	if cfg.Docker.CgroupParent != "" {
-		hostConf.CgroupParent = cfg.Docker.CgroupParent
-	}
+	cgroupParent := config.ResolveServerCgroupParent(
+		ip.Server.UUID(),
+		ip.Server.GetEnvironmentVariables(),
+		cfg.Docker,
+	)
+	if cgroupParent != "" {
+		hostConf.CgroupParent = cgroupParent
+	}
```

The helper name and exact server UUID accessor are illustrative; the requirement is
the shared resolver and identical validation/logging behavior at both create sites.

- Smallest per-server option; no panel CODE changes; survives panel upgrades
  trivially. It still requires operational panel-data work: updating/reimporting
  eggs, adding the admin-only variable, and setting per-server overrides for
  existing servers that need non-default placement.
- Natural companion to v1 in the same PR (mirrors the existing `SERVER_IP`
  special-case in the same loop).
- **Existing Docker scopes are not moved.** As with v1, a placement change through
  `WINGS_CGROUP_PARENT` only affects containers created after the new value is
  resolved; already-existing server or installer containers must be recreated.
- **Multi-tenant security note:** the variable must be admin-only in the panel
  (`user_viewable=false`, `user_editable=false`), but those flags only hide it from
  the tenant UI/API. They do not hide it from the running process, and they are not
  an authorization boundary for Wings. The v2 resolver must enforce the operator's
  allowlist/root/namespace itself. A tenant or compromised panel payload must not
  be able to place a server under arbitrary host slices such as `system.slice` or
  an unconstrained custom slice.

### Comparison

| | Code size | Upgrade survivability | Upstream acceptability | Verdict |
|---|---|---|---|---|
| v1 global `docker.cgroup_parent` | ~65 lines / 4 files | High (config-only addition) | High — matches recent merged-PR patterns (see §7) | **Propose** |
| v2a UUID→slice map in config.yml | ~15 lines | High | Low (duplicates panel state) | Stopgap only |
| v2b reserved `WINGS_CGROUP_PARENT` | ~25 lines for runtime-only sketch; upstreamable patch also needs shared resolver, installer wiring, and guard tests | High, no panel CODE dependency; requires panel-data update | High if constrained (mirrors `SERVER_IP` precedent without trusting arbitrary slice names) | **Propose after/with v1 only with guardrails** |
| Panel-native field | Medium (migration + UI + Wings) | Highest (canonical) | Needs panel maintainers | Fold into §4.4 (v3) |

---

## 4. Wings-managed slices — from "no panel CODE changes" (v2.5) to panel-native (v3)

### 4.1 The operator problem

v1/v2 assume a sysadmin installs slice unit files on each node. That is fine for a
single node and correct as IaC — but **an operator running many Wings nodes does
not want to hand-configure slice units per node**. They install Wings, point it at
the panel, and expect to manage everything — including resource tiers and
guarantees — through the panel UI and eggs. This section covers what it takes for
Wings to *create and own* the slices itself, in two flavors: a light one that needs
no panel CODE changes at all (v2.5), and the panel-native end state (v3).

### 4.2 What config channel actually reaches the node (verified — this shapes everything)

Three facts, all verified against wings v1.13.1 and current panel source:

1. **Wings never fetches raw eggs.** It pulls *processed per-server configuration*
   from the panel's remote API (`remote/servers.go`: `GetServers` at boot,
   `GetServerConfiguration` on sync). What the panel sends is built by
   `app/Services/Servers/ServerConfigurationStructureService.php` — a
   **hard-coded whitelist** (`uuid`, `meta`, `suspended`, `environment`,
   `invocation`, `skip_egg_scripts`, `build{memory_limit,swap,io_weight,cpu_limit,
   threads,disk_space,oom_disabled}`, `container{image}`, `allocations`, `mounts`,
   `egg{id,file_denylist}`), carrying the comment *"DO NOT MODIFY THIS FUNCTION"*.
   An arbitrary new field added to an egg's JSON simply never appears in this
   payload; and even if it did, Wings' `json.Unmarshal` into `server.Configuration`
   silently drops unknown fields (standard Go behavior — inferred from language
   semantics, not separately tested).
2. **Node-local modification is not a thing.** There is no egg file on the node to
   edit, and per-server config is not durable node state: `Server.Sync()`
   (`server/server.go:186`) re-fetches from the panel and **wholesale-replaces** the
   in-memory configuration (`SyncWithConfiguration`: `s.cfg = c`), then re-derives
   the env list (`server/update.go:46`:
   `SetEnvironmentVariables(s.GetEnvironmentVariables())`). `Sync()` runs at Wings
   boot (`cmd/root.go:264`), **before every server start**
   (`server/power.go:173`, `onBeforeStart`), on reinstall (`server/install.go:89`),
   and on panel-triggered sync endpoints (`router/router_server.go`). Any node-local
   mutation of server config is overwritten at the latest on next start. **Config
   must travel panel → Wings through supported fields.**
3. **Egg variables are the one admin-extensible channel that survives the whole
   path.** They are part of the egg import/export JSON format
   (`app/Services/Eggs/Sharing/EggExporterService.php` exports a `variables` array
   with `env_variable`, `default_value`, `user_viewable`, `user_editable`, `rules`),
   per-server overridable in the existing admin UI, resolved by
   `app/Services/Servers/EnvironmentService.php` (`server_value ?? default_value`)
   into the `environment` map of the whitelisted payload, and land in Wings'
   `Configuration.EnvVars` → the env slice available in `Create()` **before**
   `HostConfig` is built. Install-time and runtime containers can draw from the same
   source: the installer sets `Env: ip.Server.GetEnvironmentVariables()`
   (`server/install.go:419`) — the very function that also feeds the runtime
   environment — so a `WINGS_CG_*` variable is visible at both create sites. The
   v2 implementation still has to call the same resolver from both create paths;
   sharing the env source alone is not sufficient.

Conclusion: **structured, admin-only egg variables are the viable
no-panel-code transport** for per-server cgroup config. Nothing else is. Arbitrary
top-level egg JSON may round-trip through import/export files, but it does not
reach Wings through the panel payload; `variables` is the only no-panel-code
transport.

### 4.3 v2.5 — "advanced egg": full cgroup spec via egg variables, Wings-created transient slices

The light flavor: circumvent panel schema work entirely. An egg (importable JSON,
shareable like any egg) defines admin-only variables carrying the full per-server
cgroup spec — either one blob:

```
WINGS_CGROUP_JSON = {"slice":"wings-<short-uuid>.slice","memory_min":"6G","memory_low":"12G","memory_high":"8G","cpu_weight":800,"io_weight":100}
```

or discrete keys (`WINGS_CG_SLICE`, `WINGS_CG_MEMORY_MIN`, `WINGS_CG_MEMORY_LOW`,
`WINGS_CG_MEMORY_HIGH`, `WINGS_CG_CPU_WEIGHT`, `WINGS_CG_IO_WEIGHT`, …). At
container-create time, and through reconciliation independent of container create,
Wings:

1. parses and validates the spec (name must match the `wings-*.slice` namespace,
   §4.5; values clamped);
2. creates or updates a **transient slice** via systemd D-Bus
   (`StartTransientUnit("wings-<uuid>.slice", …)` / `SetUnitProperties(runtime=true,
   …)` — e.g. `github.com/coreos/go-systemd/v22/dbus`);
3. sets `HostConfig.CgroupParent` to that slice (the v1/v2 plumbing, unchanged);
4. reconciles slice existence and properties at Wings boot after panel sync,
   before each server start, after server sync/update, and optionally on a periodic
   loop if D-Bus access is enabled;
5. removes the slice (`StopUnit`) when the server is deleted.

Honest assessment:

- **Capability: complete for slice properties, not for moving existing scopes.**
  Everything v3 can do at the resource level — real per-server `memory.min` floors,
  `memory.high`/`max` ceilings, CPU/IO weights — is reload-safe when represented as
  systemd-owned transient-unit properties (the Finding D-safe channel; Appendix A).
  Slice existence and properties must be reconciled at the lifecycle points above.
  Existing Docker scopes are never moved by v2.5; if placement changes, the
  container must be recreated. Docker live-restore and host-restart edge cases need
  explicit behavior before this becomes an implementation plan.
- **Prerequisites: the same host-systemd access as v3.** The Wings container needs
  the host D-Bus socket (`/run/dbus/system_bus_socket`) or systemd private socket
  mounted — see the security discussion in §4.5c, which applies unchanged.
  Rootless deployments are excluded and must degrade to v1/v2 behavior.
- **Multi-tenant abuse surface: floors are zero-sum.** A tenant who can raise their
  own `memory.min` takes protection away from everyone else. Therefore: (i) the
  variables **must** be `user_viewable=false` / `user_editable=false` — a tenant
  must never be able to edit their own floor; (ii) Wings should additionally
  enforce a node-side budget (e.g. a `docker.cgroup_budget` key: refuse or clamp
  when Σ requested floors exceeds it), because defense-in-depth against panel
  misconfiguration is cheap here and the failure mode (oversold guarantees) is
  silent otherwise.
- **Inelegance, stated plainly:** this is a stringly-typed side-channel. The spec
  rides in env vars, so it also appears inside the container's environment. A
  tenant can always read their own resource spec from the running process; therefore
  these variables may contain only non-secret placement/profile metadata. There is
  no panel-side validation or UI affordance beyond "an admin typed JSON into a
  variable." That is the price of no panel CODE changes, and the reason v2.5 is a
  bridge, not the end state.
- **Position in the staged path:** between v2 and v3 — all of v3's runtime
  machinery (D-Bus, transient slices, namespace guard, budget check) with none of
  its panel work. If v2.5's Wings-side code is written cleanly, v3 is "swap the
  transport from env vars to a schema field" — the investment is not throwaway.

### 4.4 v3 — panel-native slice properties

- Egg schema: per-egg default slice-property block (`memory_min`, `memory_low`,
  `memory_high`, `cpu_weight`, `io_weight`; possibly zswap knobs, which are
  systemd-version-gated and need feature detection); per-server admin override with
  real validation and UI.
- Delivery: extend the whitelisted payload (`ServerConfigurationStructureService`)
  with one new block → a new struct next to `Build environment.Limits` in
  `server/configuration.go`. The Wings side is the easy part; the panel migration,
  admin UI, API transformers, and a release cycle are the long pole.
- Same Wings runtime machinery as v2.5.

### 4.5 Mechanisms and security (applies to v2.5 and v3)

**(a) Transient slices via systemd D-Bus — preferred.** `StartTransientUnit` is the
same call `systemd-run` and dockerd's systemd cgroup driver use. Properties are
systemd-owned: daemon-reload *re-applies* them (this is precisely the mechanism
that survives the Finding D failure mode — Appendix A). Transient units don't
survive reboot, so Wings must reconcile slice existence and properties after boot
and panel sync, before each server start, after server sync/update, and optionally
periodically. This reconciles the slice, not already-running Docker scopes: any
placement change still requires container recreation. Docker live-restore and host
restart behavior must be specified explicitly because containers may outlive the
Wings process that would otherwise recreate transient units before start.

**(b) Writing unit files + `daemon-reload` — worse on every axis that matters.**
Persistent across reboots, but Wings would need write access to the host's `/etc`,
must own unit-file lifecycle/cleanup (orphans after server deletion/migration), and
every change triggers a host-global daemon-reload — the very event Finding D
identified as destructive to any remaining non-systemd-owned cgroup state on the
host. Mechanism (a)'s lifecycle already covers reboot; prefer it.

**(c) Security, honestly.** The systemd manager API is **root-equivalent**
(`StartTransientUnit` can start arbitrary units = arbitrary code as host root), and
Wings (commonly run as a container) would need the host D-Bus/systemd socket
mounted. The honest framing: this does **not add a new privilege class** — Wings
deployments already mount `/var/run/docker.sock`, which is itself root-equivalent
(mount `/` into a privileged container). What it does do:

- widens the amount of Wings code wielding root-equivalent power (audit surface);
- turns a panel compromise into direct host-resource-topology manipulation (though
  a panel compromise already implies arbitrary-container execution via Wings — the
  delta is modest);
- adds a *non-malicious* failure class: panel/egg misconfiguration touching slices
  that govern non-Wings workloads.

The key mitigation, which should be a **hard rule** in any upstream design: Wings
may only ever create/modify/delete slices matching `wings.slice` / `wings-*.slice`
(enforced by the same kind of validation as `ValidateCgroupParentValue`), never
arbitrary unit names. That confines bugs and abuse to the Wings-owned subtree (§5).
For v2 per-server overrides, the same idea appears earlier as an allowlist,
root-child validation under `docker.cgroup_parent`, or this hard namespace guard.

### 4.6 Staged path

| Stage | What | Panel changes | Host prep per node | Status |
|---|---|---|---|---|
| **v1** | `docker.cgroup_parent` in config.yml — all new server and installer containers under one named slice | none | install slice unit(s) once | **first minimal PR — §2** |
| **v2** | `WINGS_CGROUP_PARENT` reserved egg/server variable — per-server placement | no panel CODE changes; requires egg variable/update and per-server overrides for existing servers | install per-server slice units or maintain allowed namespace | **same PR or follow-up only with shared runtime/installer resolver + namespace/allowlist — §3b** |
| **v2.5** | Full cgroup spec in admin-only egg variables; Wings creates/reconciles transient `wings-*.slice` units via D-Bus and applies properties (reload-safe) | no panel CODE changes; requires egg variable/update and admin data management | mount host D-Bus socket into Wings; no unit files | **RFC material, not an initial PR — §4.3** |
| **v3** | Panel/egg slice-property schema + UI; same Wings machinery as v2.5 with a proper transport | egg schema + admin UI + API + migration | none | **long-term end state / RFC material — §4.4** |

Each stage is additive and opt-in; none paints the project into a corner, and
v2.5's runtime code is v3's runtime code. At every stage, existing Docker scopes
are never moved in place; placement changes require container recreation.

---

## 5. Target hierarchy — a dedicated `wings.slice` parent

Independent of which stage delivers it, the *shape* worth aiming for is a dedicated
parent slice for everything Wings creates, with per-server child slices. systemd's
dash-naming does the nesting automatically: `wings-b87c0a5b.slice` is, by name
alone, a child of `wings.slice` (and `wings-b87c0a5b-paks.slice` would nest under
`wings-b87c0a5b.slice`).

```
-.slice (root)
├── system.slice                          ← host services ONLY (sshd, dockerd,
│   ├── ssh.service, docker.service, …       the wings management container).
│   └── docker-<wings-mgmt-id>.scope         MemoryMin sized for services alone (small).
│
├── wings.slice                           ← EVERYTHING Wings creates. ONE knob for the
│   │                                        whole hosting tier:
│   │                                        MemoryMin ≥ Σ child floors   (protection budget)
│   │                                        MemoryHigh/Max = tier ceiling (optional)
│   │                                        CPUWeight/IOWeight vs other tiers
│   ├── wings-b87c0a5b.slice              ← per-server slice (short server UUID);
│   │   │                                    example values from the Appendix A case study:
│   │   │                                    MemoryMin=6G MemoryLow=12G MemoryHigh=8G
│   │   ├── docker-<id>.scope             ← server A's game container
│   │   └── wings-b87c0a5b-paks.slice     ← optional per-server data-cache slice, auto-nests
│   │                                        MemoryMin=<hot set>, MemoryZSwapMax=0
│   └── wings-<uuid2>.slice               ← next server: own floor/ceiling
│       └── docker-<id>.scope
│
├── interactive.slice                     ← other host tiers (example: dev workloads)
└── besteffort.slice                      ← e.g. CI/test stacks, builds
```

Where the protection-chain values go (the §0 math, made concrete):

- **`wings.slice` MemoryMin ≥ sum of all child floors** (per-server `MemoryMin` +
  any nested cache-slice floors). A top-level slice's `memory.min` is directly
  effective against global reclaim — there is no ancestor above it to cap it. If
  the parent budget is smaller than the sum of child claims, children compete
  proportionally for the shortfall — keep the invariant explicit and monitored
  (and, in v2.5/v3, Wings-enforced via the budget check, §4.3).
- **Per-server floors/ceilings live on `wings-<uuid>.slice`**, not on the Docker
  scope. This is the quiet superpower of the design: slice values are
  **systemd-owned** (unit file, `set-property`, or transient-unit properties), so
  they are *re-applied* on every daemon-reload — the Finding D failure mode
  (Appendix A) becomes a non-issue for exactly the values that matter most. Any
  scheme that writes to the transient `docker-<id>.scope` instead is one
  `apt install` away from being silently wiped.
- `memory.zswap.writeback=0` semantics at the slice level (does disabling it on the
  per-server slice cover the scope beneath, i.e. is the check hierarchical?) —
  **unverified**; test before relying on it, otherwise apply that one knob
  per-scope via `set-property`.
- **PSI/accounting fall out for free**: `/sys/fs/cgroup/wings.slice/memory.pressure`
  = whole-tier pressure; `wings.slice/wings-<uuid>.slice/memory.pressure` =
  per-server pressure — a clean per-tenant observability story with no scope-PID
  chasing.

How the stages map onto this tree: **v1** puts every server directly under
`wings.slice` (tier bound + one shared floor; per-server floors not yet
expressible — already the full win on a single-server node). **v2** adds
`WINGS_CGROUP_PARENT=wings-<uuid>.slice` per server, with the sysadmin
pre-installing the child slice units (dash-naming auto-nests them). **v2.5/v3**
have Wings create the child slices itself. A worked single-server instance of this
tree, with live values, is in Appendix A.

---

## 6. Alternatives analysis — why host slices at all?

The obvious counter-question: *"why not put the full cgroup config in the egg (or
per-server via the panel UI) and have Wings apply it at container start — no
slices, no host-side structure at all?"* Three facts close this off:

1. **Docker's API cannot express a protection floor.** Verified against the
   complete `container.Resources` struct in `moby/moby v28.3.3`
   (`api/types/container/hostconfig.go`): `Memory` (→ `memory.max`),
   `MemoryReservation` ("memory soft limit" — mapped to `memory.low` on cgroup v2
   by runc), `MemorySwap`, `MemorySwappiness`, CPU shares/quota/sets, `Blkio*`
   weights/throttles, `PidsLimit`, ulimits, devices — and **no `memory.min` field
   of any kind, and no zswap knobs**. An egg → Docker HostConfig pipeline, no
   matter how rich the egg schema, physically cannot build a `memory.min` floor.
   (Wings already sends `MemoryReservation` today — `memory.low` at best, and see
   point 3 for why even that is currently moot.)
2. **Writing cgroup files directly after container start is a trap.** The
   alternative "Wings echoes values into
   `/sys/fs/cgroup/.../docker-<id>.scope/*` post-create" was **disproven on a live
   production node** (Appendix A, Finding D): any `systemctl daemon-reload` —
   triggered by any package that ships a unit — silently resets every raw-written
   attribute on the transient scope back to Docker defaults. The reload-safe
   channel is `systemctl set-property` / D-Bus `SetUnitProperties`, i.e. going
   through systemd — which requires the same host-systemd access as §4's v2.5/v3
   while delivering less (per-scope values still vanish with the container; slice
   values persist across container recreates).
3. **The hierarchical math is decisive regardless of mechanism.** Even a *perfect*
   per-container configuration — imagine Docker grew a `MemoryMin` field tomorrow —
   is capped by every ancestor's `memory.min` on the path to the root (§0). A 6 GB
   floor under `system.slice(min=0)` protects nothing; that is Finding A, observed
   live, not theory. Some host-level ancestor configuration is therefore
   **mathematically unavoidable** for protection floors: somebody has to put a
   protection budget on the ancestor slice. Per-egg config can carry the
   *per-server* values; the *tier/ancestor* budget must live host-side — installed
   by a sysadmin (v1/v2) or owned by Wings in a constrained namespace (v2.5/v3).

**Conclusion: egg-level cgroup config and host slices are complements, not
alternatives.** The egg is the right home for per-server *values* (floor sizes,
weights); the slice hierarchy is the only place the *structure* (ancestor budgets,
tier separation, reload-safe ownership) can live.

---

## 7. Landing strategy — where to submit

Checked both projects' recent PR/issue activity via the GitHub API (public,
unauthenticated) on 2026-07-07:

**`pterodactyl/wings`** (default branch `develop`):
- Not archived, 1019 stars, pushed 2026-06-29, 51 open issues — actively maintained.
- Zero existing PRs or issues mention cgroup-parent/slice support
  (`search/issues?q=repo:pterodactyl/wings+cgroup` → 1 hit, an unrelated dependency
  bump).
- Recent merged work shows real appetite for cgroup-v2 nuance: PR #324, "Only set
  the container block IO weight when the host supports io.weight" (merged
  2026-05-30) — the same class of fix, already visible as the
  `blkioWeightSupported()` helper in `environment/settings.go`. But it is the only
  cgroup-adjacent PR in the repo's history.
- No `CONTRIBUTING.md` in the repo or a `.github` org repo.

**`pelican-dev/wings`** (independent hard fork of the Pterodactyl stack):
- Not archived, 296 stars, pushed 2026-07-04 — **more recent activity than
  upstream**, and a visibly faster merge cadence for small, additive node-config
  features. Merged in the last ~3 weeks alone: "Add disk quota support", "Add quiet
  config option", "add conditional support for ioweight"; plus "feat: add support
  for container network mode" (closed only because the author moved it to a
  side-image, not from maintainer pushback). A new, additive
  `DockerConfiguration`/`HostConfig` knob with automated review (coderabbit) and
  quick merges is a near-exact precedent for v1.
- Pelican also controls panel *and* wings under one org — the natural home for the
  v3 vision, since a cross-cutting egg-schema + wings feature has one review
  pipeline instead of two.

**Recommended sequence:**
1. Operators who need this today: run a patched build (a complete single-node
   deployment runbook, including rollback and risk assessment, is in Appendix A).
2. Submit v1 (§2) to `pelican-dev/wings` first; cross-submit to
   `pterodactyl/wings` in parallel (low cost). Add v2 (§3b) in the same PR only if
   it includes the shared runtime/installer resolver and namespace/allowlist guard;
   otherwise keep v2 as the immediate follow-up. Frame the PR explicitly as stage
   one of §4.6's staged path, and open a companion RFC issue sketching v2.5/v3
   (`wings.slice` hierarchy §5, D-Bus-managed per-server slices §4.3) so reviewers
   see a small additive knob on a coherent road, not a one-off.
3. Once merged anywhere, rebase local builds onto that tag.

---

## 8. Options and effort assessment (external review, 2026-07-08)

Effort ranges assume an engineer already familiar with Wings, the panel data flow,
systemd cgroup v2, and the case-study host. Upstream estimates include review
iteration, tests, documentation, and compatibility discussion.

| Option | Description | Main components | Local effort | Upstream effort | Assessment |
|---|---|---|---:|---:|---|
| A | Keep current watcher/scope mutation model | host scripts, `systemctl set-property`, instance env files | 0.5-1 day for maintenance | Not suitable | Useful fallback only; it keeps compensating for bad placement instead of fixing it. |
| B | v1 global `docker.cgroup_parent` | Wings config struct, validation, runtime `HostConfig`, installer `HostConfig`, real systemd slice unit, deployment runbook | 1-2 days including smoke test | 3-7 days | **Do first.** Small, understandable, and directly fixes ancestor placement for the current host. |
| C | v1 + v2 `WINGS_CGROUP_PARENT` to pre-created slices | Option B plus shared resolver, installer support, allowlist/root/namespace validation, egg/server variable, per-server slice units | 2-4 days plus panel-data updates | 1-2 weeks | **Do next.** Good for multiple servers or per-tier placement without panel CODE changes, but only with the v2 guardrails in §3b. |
| D | Egg variables as admin-only placement/profile transport | `WINGS_CGROUP_PARENT`, optional profile metadata, egg reimport/update, per-server overrides | 1-2 days after v2 | Part of Option C | Good after v2 if limited to non-secret placement metadata. Do not put UUID-specific defaults in a generic egg. |
| E | Egg variables with full cgroup spec; Wings creates transient slices via systemd D-Bus | parser, validation, budget accounting, D-Bus client, systemd socket mount, namespace guard, cleanup/reconcile loop | 1.5-3 weeks | 3-6 weeks | Defer. Powerful, but security and lifecycle complexity make it poor initial PR material. |
| F | Panel-native cgroup schema | panel migrations/models, egg export/import, admin UI, API payload, Wings structs, D-Bus slice manager, tests/docs | 6-10 weeks | major feature cycle | Correct long-term product design and the clean end state. Too large for the immediate placement fix. |
| G | Docker daemon-wide `--cgroup-parent` / daemon config | Docker daemon config only | 0.5 day | N/A | Reject. Too broad: affects unrelated Docker workloads and does not solve per-server placement. |
| H | Direct raw writes to Docker scope cgroup files | Wings or scripts writing `/sys/fs/cgroup/.../docker-*.scope/*` | 1-3 days | Not recommended | Reject. It conflicts with systemd ownership and has already failed under daemon-reload on this host. |

Recommendation: implement Option B first. Add Option C next, either in the same PR
only if the shared runtime/installer resolver and namespace/allowlist guard are
present, or as a follow-up PR. Use egg variables as admin-only transport (Option D)
only after v2 exists. Defer D-Bus transient slices (Option E) to RFC work, and
treat panel-native schema (Option F) as the product end state. Do not pursue
Options G or H.

---

## 9. If upstream acceptance is unlikely

If upstream PR acceptance stalls, favor a patch shape that survives rebases over a
feature-complete local fork:

**minimal Wings patch + standard egg variables + host-side reconciler.**

Concrete boundary:

- Egg variables are a **transport** for non-secret placement/profile metadata,
  e.g. `WINGS_CGROUP_PARENT=soulmask.slice` or
  `WINGS_CGROUP_PROFILE=soulmask-prod`.
- Wings only reads the resolved environment metadata and places the Docker scope
  under the selected slice via `HostConfig.CgroupParent`.
- The host-side reconciler owns resource properties, budgets, zswap or
  systemd-version edge cases, and reconciliation after daemon reloads/restarts,
  container restarts, and server syncs.
- Panel-native schema remains the clean future design if a real fork or upstream
  feature is accepted later.

This keeps the Wings delta small: no panel patch, no non-standard egg top-level
schema, stable PTDL_v2 import/export, and Docker container creation as the only
Wings touch point. The reconciler can evolve faster than a Wings fork while Wings
continues to do the one placement action Docker already supports.

Do not rely on arbitrary top-level egg JSON such as:

```json
{
  "cgroups": {
    "memory_min": "6G"
  }
}
```

That field may round-trip through an exported JSON file, but it will not reach
Wings through the current panel-to-Wings server configuration payload without
panel CODE changes. Use `variables` for no-panel-code transport.

---

## Appendix A — Case study: single-node 16 GB game host

> **Clearly-marked worked example.** Everything in this appendix is specific to one
> production node (a 16 GB host running one large game server — Soulmask — plus dev
> containers and a test stack, governed by the companion documents
> `plan-host-resource-governance.md`, `MEMORY-ARCHITECTURE.md` and
> `CGROUP-MONITORING.md` in this repository). It exists to ground the abstract
> claims in §0/§6 with live-verified data and to provide a concrete deployment
> runbook for a patched Wings.

### A.1 Live findings that motivated this proposal

- **Current placement (verified):** the running game container's cgroup is
  `system.slice/docker-<id>.scope` (read from `/proc/<pid>/cgroup`); the intended
  parent `soulmask.slice` exists only as a **transient** slice with `MemoryMin=0`
  and no unit file (`systemctl show soulmask.slice` → `FragmentPath=` empty).
  This is the false-positive rollout mode to avoid: a path can mention the intended
  slice while the actual resource guarantees are absent.
- **Finding A** (`plan-host-resource-governance.md` §1.5, verified live): the game
  scope's `memory.min=6G` and a nested pak-cache slice's `MemoryMin=150M` protect
  **nothing** against global reclaim, because `system.slice/memory.min=0` and
  `soulmask.slice/memory.min=0` sit above them. Observed stability comes entirely
  from `memory.high` demand-shaping. The only workaround available without Wings
  changes — `systemctl set-property system.slice MemoryMin=7G` — drags sshd,
  dockerd and Wings into the game's protection budget: the blunt instrument §0
  describes.
- **Finding D** (`plan-host-resource-governance.md` §1.5, proven live 2026-07-07):
  a watcher applied and verified cgroup knobs on the game scope by raw file writes
  at 23:16; `apt install systemd-oomd` triggered a daemon-reload at ~00:12; by
  01:00 the scope was back to `min=0/low=0/high=max/writeback=1/cpu.weight=100`.
  Values must be systemd-owned (`set-property` / unit files / transient-unit
  properties) to survive. This is the empirical basis for §4.5/§5/§6's insistence
  on systemd-owned slice values.
- With `cgroup_parent: soulmask.slice` (v1) plus the already-designed
  `soulmask.slice` unit (`MemoryMin=5G`, `MemoryLow=12G`, nested pak slice —
  governance plan §3.3), the floors become arithmetically effective, and the
  `system.slice`-wide `MemoryMin` hack can be narrowed or dropped. On this
  single-server node, `soulmask.slice` plays the role of §5's per-server slice;
  the `wings-*` naming becomes worth adopting when a second server or an upstream
  merge arrives.

### A.2 Deployment runbook (patched Wings, docker-compose node)

Node specifics: wings v1.13.1 runs as a docker-compose service
(`/root/ptero-wings/docker-compose.yml`, image `ghcr.io/pterodactyl/wings:latest`)
against the host `docker.sock`; dockerd uses the systemd cgroup driver (cgroup v2).

**Build** (on the node, no registry needed):

```bash
git clone --branch v1.13.1 https://github.com/pterodactyl/wings.git /path/to/build/wings
cd /path/to/build/wings
git apply /path/to/v1.patch      # §2; add guarded §3b only when the shared resolver is present

# Repo's own Dockerfile (verified: golang:1.24.11-alpine builder ->
# gcr.io/distroless/static:latest runtime, CGO_ENABLED=0, ldflags inject Version)
docker build \
  --build-arg VERSION=1.13.1-cgroupparent.1 \
  -t wings-local:1.13.1-cgroupparent.1 \
  .
```

**Deploy:**

1. In `/root/ptero-wings/docker-compose.yml`, change `image:` to
   `wings-local:1.13.1-cgroupparent.1`. A plain local tag (no registry-shaped
   prefix) is deliberate: a stray `docker compose pull` **fails loudly** instead of
   silently reverting to the stock upstream image. (Verified: no watchtower, no
   cron, no systemd timer auto-updates wings on this node — updates are 100%
   manual.)
2. Add to `/etc/pterodactyl/config.yml`:
   ```yaml
   docker:
     cgroup_parent: soulmask.slice
   ```
3. **Install and load the slice unit first.** `soulmask.slice` is currently
   transient with no limits (A.1); install the unit file from the governance plan
   §3.3 under `/etc/systemd/system/` and `systemctl daemon-reload` **before**
   flipping `cgroup_parent`, or the container lands in the same limit-less
   transient slice as today (no regression, but no benefit).
4. **Mandatory pre-check before pointing Wings at the slice:**
   ```bash
   systemctl show soulmask.slice -p FragmentPath -p MemoryMin -p MemoryLow -p MemoryHigh
   ```
   `FragmentPath` must point at the intended unit file and the memory properties
   must match the rollout plan. Then smoke-test Docker's placement path directly:
   create a throwaway container with `--cgroup-parent=soulmask.slice`, verify both
   `/proc/<pid>/cgroup` and the effective files under `/sys/fs/cgroup/soulmask.slice`
   (`memory.min`, `memory.low`, `memory.high`), and remove the throwaway container.
5. `cd /root/ptero-wings && docker compose up -d --force-recreate` — touches only
   the Wings process, not the running game container.
6. **The running game container will NOT move on its own** (§2: `Create()` no-ops
   on an existing container; `Reinstall()` only re-runs the installer container).
   Planned brief outage: stop the server from the panel → `docker rm <server-uuid>`
   (bind-mounted server data is untouched) → start from the panel.

**Rollback:** revert steps 1-2, `docker compose up -d --force-recreate`. The stock
image never sets `CgroupParent`, and an already-recreated game container keeps its
last placement — no second outage needed for rollback.

**Risk assessment:**

- *Auto-update risk: low* — no auto-update mechanism exists on this node (verified,
  above). Residual risk is a human re-running the official install script (which
  rewrites the compose file); mitigated with a comment in the compose file + this
  runbook.
- *Config compat on future official upgrade: low-medium* — the added key has a
  zero-value default and unknown YAML keys are ignored by wings' `yaml.v2`
  unmarshal, so a stock image silently drops the feature rather than failing to
  start. Main real risk is "forgot this is a custom build" bit-rot; keep the diff
  small and rebase on upstream tags.
- *Build/runtime risk: verified low* — full patch built with `go build ./...` and
  `go vet` against the pinned deps: zero errors, zero new vet warnings (two
  pre-existing unrelated vet warnings elsewhere in the repo, untouched). The binary
  runs. `ValidateCgroupParentValue` exercised standalone against 7 representative
  inputs with expected accept/reject on each.
- *Not verified (flagged):* no container was actually created with `CgroupParent`
  set during this study — the runtime effect rests on documented Docker/moby
  behavior plus this node's manual precedent
  (`docker run --cgroup-parent=dev-workloads.slice …` in the repo's slice-unit
  docs). The mandatory pre-check/smoke test above must verify both the cgroup path
  and the effective resource files before production rollout; path-only verification
  is not enough.

---

## Appendix B — verification method

- Cloned `https://github.com/pterodactyl/wings` at tag `v1.13.1` into scratch space
  (not this repository, not the node's `/root/ptero-wings`).
- Read (not grepped-and-assumed) every wings file this proposal cites:
  `environment/docker/container.go`, `server/install.go`, `config/config_docker.go`,
  `environment/settings.go`, `environment/config.go`, `server/configuration.go`,
  `server/server.go` (incl. `Sync`/`SyncWithConfiguration`), `server/update.go`,
  `server/manager.go`, `server/power.go` (Sync call sites), `remote/types.go`,
  `remote/servers.go`, `cmd/root.go`, `config/config.go`, `Dockerfile`, `Makefile`.
- Downloaded a Go 1.24.5 toolchain into scratch space and ran `go build ./...`,
  `go vet ./config/... ./cmd/... ./environment/... ./server/...`, and a direct
  `go build -o … wings.go` + `wings version` against the fully patched tree — all
  clean (pre-existing, unrelated vet warnings noted in A.2; none in changed files).
- Compiled and ran `ValidateCgroupParentValue` standalone against 7 representative
  inputs to confirm accept/reject behavior.
- Fetched `moby/moby` tag `v28.3.3` (matching `go.mod`'s pin)
  `api/types/container/hostconfig.go` — including the **complete** `Resources`
  struct, to confirm §0/§6's claim of no `memory.min` and no zswap fields — and
  `api/types/system/info.go` (for `CgroupDriver`/`CgroupVersion` on `Info()`).
- Fetched from `pterodactyl/panel` `develop` (single files, no full clone):
  `app/Models/EggVariable.php` (`user_viewable`/`user_editable`,
  `RESERVED_ENV_NAMES`, `WINGS_CGROUP_PARENT` not reserved),
  `app/Services/Servers/ServerConfigurationStructureService.php` (hard-coded
  whitelist payload, "DO NOT MODIFY THIS FUNCTION"),
  `app/Services/Servers/EnvironmentService.php` (egg variables →
  `environment` map, `server_value ?? default_value`, panel-key precedence),
  `app/Services/Eggs/Sharing/EggExporterService.php` (`variables` array in the egg
  import/export JSON format).
- Verified in wings source that the resolved env map is available at **both**
  container-create sites: runtime `Create()`
  (`environment/docker/container.go:157`, before `HostConfig` construction) and
  installer `Execute()` (`server/install.go:419`,
  `Env: ip.Server.GetEnvironmentVariables()`). This proves the variable is
  available to both paths, not that the first-pass v2 runtime hunk already wired the
  installer; the required shared resolver is specified in §3b. Also verified:
  `Sync()` wholesale-replaces server config from the panel (`s.cfg = c`) and is
  called at boot, before every start, on reinstall, and from panel-triggered
  endpoints (call sites listed in §4.2).
- Inferred, not separately tested: Go's `json.Unmarshal` dropping unknown JSON
  fields (language-standard behavior); runc's `MemoryReservation` → `memory.low`
  mapping on cgroup v2 (documented Docker/runc behavior).
- Queried the public GitHub API (unauthenticated) for PR/issue history and repo
  metadata on `pterodactyl/wings` and `pelican-dev/wings` (§7 figures, 2026-07-07).
- Findings A and D are cited from `plan-host-resource-governance.md` §1.5 (the case
  study node's live verification, 2026-07-06/07), re-read in full for this
  revision — not re-derived here.
- The D-Bus mechanism description in §4.3/§4.5 (`StartTransientUnit` /
  `SetUnitProperties`, `go-systemd/v22/dbus`) is from prior knowledge of systemd's
  API and was **not** prototyped — design sketch, not compiled code. Likewise the
  §5 note on `memory.zswap.writeback` hierarchy semantics is explicitly unverified.
- Read-only checks on the case-study node: `docker exec … wings version`
  (v1.13.1), the `docker:` section of `/etc/pterodactyl/config.yml` (matches the
  source-read struct), `/root/ptero-wings/docker-compose.yml`,
  `systemctl list-units --type=slice`, `systemctl show soulmask.slice`,
  `/proc/<pid>/cgroup` of the running game container, absence of any wings
  auto-update mechanism. No file on the node was written; no container was
  created, removed, or restarted.
