# Wings `CgroupParent` / systemd-slice support — feasibility study + patch sketch

Status: proposal, not deployed. Nothing on this host was modified while writing this —
all code changes below were written and **compiled + vetted + smoke-tested** against a
scratch clone of `pterodactyl/wings` at tag `v1.13.1` (matches the version running here:
confirmed via `docker exec ... wings version` → `wings v1.13.1`). The live wings
container, `/etc/pterodactyl/config.yml`, and the running Soulmask container were only
read, never written.

---

## 0. Motivation — what this actually solves

cgroup-v2 memory protection (`memory.min` / `memory.low`) is **hierarchical**: a
cgroup's *effective* floor is capped by the value of **every ancestor** on the path to
the root. A 6 GB `memory.min` on the game's `docker-<id>.scope` is worth exactly zero
against global reclaim if its parent `system.slice` has `memory.min=0` — which is the
default, and the live state on this host. This is not theoretical here:

- **`plan-host-resource-governance.md` §1.5 Finding A** (verified live): the game
  scope's `memory.min=6G` and the pak slice's `MemoryMin=150M` currently protect
  **nothing** against global reclaim, because `system.slice/memory.min=0` and
  `soulmask.slice/memory.min=0` sit above them. The observed stability comes entirely
  from `memory.high` demand-shaping, not from the floors.
- **`plan-host-resource-governance.md` §1.5 Finding D** (proven live 2026-07-07): raw
  writes to a Docker scope's cgroup files (`echo … > memory.min`) are **silently wiped
  by any `systemctl daemon-reload`** — systemd re-applies its recorded properties to
  every transient scope, and any apt package that ships a unit triggers a reload. The
  reload-safe mechanism is `systemctl set-property` (systemd then owns and re-applies
  the values), i.e. the durable home for protection values is **systemd-managed units
  (slices), not post-hoc file writes**.

Because the floor math is hierarchical, the only way to give the game a real floor
today is to inflate `system.slice`-wide `MemoryMin` — which drags sshd, dockerd,
wings, and every other host service into the protected budget (a blunt instrument,
and it erodes the prod/interactive/best-effort tiering the governance plan is built
on). What Wings `CgroupParent` support buys:

1. **Per-server floors become real.** Game container under `soulmask.slice` (or a
   per-server slice, §5) → the slice's `MemoryMin=5-6G` is on the ancestor path and
   actually protects the game against global reclaim.
2. **Tiering works.** Game workloads get their own top-level slice, ranked against
   `interactive.slice` (devcontainer) and `besteffort.slice` (test stack, builds) by
   CPU/IO weight and bounded by slice-level ceilings — instead of everything blending
   into `system.slice`.
3. **Game-host governance decouples from host services.** `system.slice` goes back to
   containing only actual system services; its protection can be sized for
   sshd/dockerd alone instead of "sshd/dockerd + an 8 GB game".

Wings v1.13.1 cannot express any of this: it never sets
`HostConfig.CgroupParent`, so every game container lands in `system.slice` (verified
live: the running Soulmask container's cgroup is
`system.slice/docker-<id>.scope`). Hence this proposal.

---

## 1. TL;DR

- Wings v1.13.1 genuinely has no `CgroupParent` support anywhere in its Docker
  container-creation code. Confirmed by source read, not just grep: `environment/docker/container.go`
  `Create()` and `server/install.go` `Execute()` both build a `container.HostConfig{}`
  with no `CgroupParent`/`Resources.CgroupParent` field set.
- Root cause of the live problem: on **this host right now**, the running Soulmask
  container's cgroup is `system.slice/docker-<id>.scope` (verified via
  `/proc/<pid>/cgroup`), and `soulmask.slice` itself is a **transient** slice with
  `MemoryMin=0`, `FragmentPath=` (empty — no unit file loaded), confirmed via
  `systemctl show soulmask.slice`. See §0.
- A minimal patch (~65 lines across 4 files) adds a node-level `docker.cgroup_parent`
  config key and plumbs it into both container-creation call sites. It **compiles,
  vets cleanly, and runs** (`go build ./...`, `go vet`, and a standalone binary that
  prints its version) against the exact dependency versions pinned in `go.mod`
  (`github.com/docker/docker v28.3.3+incompatible`, Go 1.24). See §2 for the diff.
- A second, small increment (~25 lines, one file) adds a per-server override via a
  reserved environment variable (`WINGS_CGROUP_PARENT`) that **requires no Panel code
  changes at all** — Panel already ships a generic admin-only egg-variable mechanism
  (`user_viewable`/`user_editable` flags) that Wings already forwards verbatim into
  the container's `Env`. See §3b.
- Staged adoption path (§4): **v1** node-level config key (this patch, now), **v2**
  per-server reserved egg variable (small increment, no Panel changes), **v3**
  Panel/egg schema + Wings-managed per-server slices via the systemd D-Bus API — the
  long-term, multi-host-operator-friendly, upstreamable vision. Slices stay
  sysadmin-managed in v1/v2; v3 would let Wings own a constrained `wings-*.slice`
  namespace (§4c, §5).
- Why slices at all, instead of pushing all cgroup config through the egg → Docker
  API? Because Docker's API **cannot express `memory.min`** (verified against the
  full moby v28.3.3 `Resources` struct), raw post-create cgroup writes are wiped by
  `daemon-reload` (Finding D), and the hierarchical floor math requires host-side
  ancestor budgets regardless. Egg-level config and host slices are complements, not
  alternatives. See §6.
- Recommended path: **local fork now** (prod needs this today), **submit upstream to
  `pelican-dev/wings`** in parallel (more receptive to exactly this class of
  node-config-additive PR — see §7), and keep the local patch rebased on `pelican-dev`
  once merged.

---

## 2. Option 1 — global node-level `docker.cgroup_parent`

### Design

Add one new key to `/etc/pterodactyl/config.yml`:

```yaml
docker:
  cgroup_parent: soulmask.slice
```

- Empty (default) → unchanged behavior, Docker's normal placement (`system.slice`
  under the systemd cgroup driver, since Wings never sets a network/cgroup namespace
  otherwise).
- Non-empty → every server container **and every installer container** created from
  that point forward get `HostConfig.CgroupParent` set to this value.
- Validated at Wings startup (`cmd/root.go` `initConfig()`, right after
  `config.FromFile`): must be non-empty-or-whitespace-free and end in `.slice`. Wings
  does **not** call into Docker's `Info()` API to check the daemon's actual cgroup
  driver (see "not implemented" note below) — an invalid value simply causes Wings to
  refuse to start with a clear error, same pattern as other `log2.Fatalf` config
  errors already in that function.
- **Does not retroactively move existing containers.** `environment/docker/container.go`
  `Create()` returns early if `ContainerInspect` finds the container already exists —
  confirmed by reading the function. A currently-running server must have its
  container removed (not just restarted) for a newly-set `cgroup_parent` to take
  effect; see §8 for the exact operational sequence.

### Verified source pointers (v1.13.1)

| File | What's there today |
|---|---|
| `environment/docker/container.go:227-260` | `Create()` builds `HostConfig{PortBindings, Mounts, Tmpfs, Resources, DNS, LogConfig, SecurityOpt, ReadonlyRootfs, CapDrop, NetworkMode, UsernsMode}` — no `CgroupParent`. |
| `server/install.go:428-451` | `Execute()` (installer container) builds a second, separate `HostConfig{}` — also no `CgroupParent`. |
| `config/config_docker.go:49-98` | `DockerConfiguration` struct — no cgroup-related field of any kind (`grep -i cgroup` across the whole module returns zero hits before this patch). |
| `environment/settings.go:105-137` | `Limits.AsContainerResources()` builds the *embedded* `container.Resources` (Memory, Swap, CPU, block-IO weight, pids) that `HostConfig` embeds anonymously. `CgroupParent` is a sibling field on that same embedded `Resources` struct (confirmed against `moby/moby v28.3.3` `api/types/container/hostconfig.go`), so it can be set either via the `Resources` literal or, more simply, via field promotion on the constructed `*container.HostConfig` (`hostConf.CgroupParent = "..."`) — both are valid Go. |
| `cmd/root.go:395-414` | `initConfig()` — loads config, `log2.Fatalf`s on error. Natural home for a startup-time validation call. |

### Patch (compiled + vetted against v1.13.1; see Appendix)

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
+	//     the intended resource limits applied, instead of failing loudly.
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
  (confirmed present as fields on `moby/moby`'s `system.Info` struct) and warn if the
  daemon isn't using `systemd`. Cheap to add later; skipped here to keep the patch
  minimal and avoid an extra API round-trip on every config load.
- No re-validation against the live cgroupfs (`/sys/fs/cgroup/<parent>`) — by design;
  Docker/systemd already do this at container-create time, and duplicating it in
  Wings adds coupling for no real safety gain (see §8's transient-slice caveat).

---

## 3. Option 2 — per-server override

### How per-server data reaches Wings today (verified)

- Panel → Wings sync: `remote.ServerConfigurationResponse{Settings, ProcessConfiguration}`
  (`remote/types.go`), where `Settings` is a `json.RawMessage` unmarshaled into
  `server.Configuration` (`server/configuration.go:24-62`). That struct already carries
  `Labels map[string]string` (a generic passthrough that becomes Docker container
  labels — precedent for "generic panel→Wings passthrough field") and
  `Build environment.Limits` (memory/cpu/swap/io-weight — no cgroup field today).
- Egg/server environment variables: `Configuration.EnvVars environment.Variables`
  (map) is merged with Wings-computed vars (`TZ`, `STARTUP`, `SERVER_MEMORY`,
  `SERVER_IP`, `SERVER_PORT`) in `server.Server.GetEnvironmentVariables()`
  (`server/server.go:151-174`) and becomes the container's `Env` in
  `Configuration.EnvironmentVariables()`.
- Confirmed **Wings already has full access to the resolved env-var list before
  building `HostConfig`**: `Create()` in `container.go` reads
  `evs := e.Configuration.EnvironmentVariables()` and even rewrites one value in
  place (`SERVER_IP=127.0.0.1` → real interface IP) *before* `hostConf` is
  constructed. So reading a reserved variable at that same point is a natural,
  already-precedented extension point.
- Confirmed (via Panel source, `app/Models/EggVariable.php` on `pterodactyl/panel`
  `develop`) that egg variables already carry `user_viewable`/`user_editable`
  booleans and a fixed `RESERVED_ENV_NAMES` list
  (`SERVER_MEMORY,SERVER_IP,SERVER_PORT,ENV,HOME,USER,STARTUP,SERVER_UUID,UUID`) that
  egg authors cannot reuse. `WINGS_CGROUP_PARENT` is not on that list, so **an admin
  can define this variable on an egg (or per-server override) today, with
  `user_viewable=false`/`user_editable=false`, with zero Panel code changes.** Those
  flags are enforced only by the Panel's own client-facing UI/API — Wings receives
  the fully-resolved `environment` map regardless and applies no extra access
  control of its own (this is already true for every other env var Wings consumes).

### (a) Wings-side config.yml map (UUID → slice)

Add e.g. `docker.server_cgroup_parents: {<uuid>: <slice>}` to config.yml, looked up
by server UUID in `Create()`.
- Code size: trivial (a `map[string]string` field + one lookup).
- Upgrade survivability: good — purely local config, survives Wings upgrades exactly
  like `cgroup_parent` would (as long as the upstream patch, or local fork, is
  present).
- Downside: **duplicates panel state on the node** — the sysadmin has to remember to
  update this file whenever a server is added, renamed, or migrated to another node.
  For a single-tenant / small-fleet host like this one that's a minor nuisance, not a
  blocker, but it doesn't scale and isn't something upstream would want as the
  canonical mechanism.
- Verdict: **fine as a stopgap on this host if the reserved-variable approach (b)
  turns out to be undesirable for some reason**, but (b) is strictly better here
  (single source of truth stays the Panel, no file to keep in sync).

### (b) Reserved egg/server variable `WINGS_CGROUP_PARENT` — recommended

Layered on top of the Option 1 patch, ~25 lines, one file:

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
+		// variable. This MUST only ever be set by an admin -- create the egg
+		// variable with user_viewable=false and user_editable=false on the
+		// Panel, since Wings applies no further access control here and will
+		// happily honor whatever the Panel sends down as this server's
+		// environment.
+		if val, ok := strings.CutPrefix(v, "WINGS_CGROUP_PARENT="); ok && val != "" {
+			cgroupParent = val
+		}
+	}
+	if cgroupParent != "" && cgroupParent != cfg.Docker.CgroupParent {
+		if err := config.ValidateCgroupParentValue(cgroupParent); err != nil {
+			e.log().WithField("error", err).Warn("environment/docker: ignoring invalid WINGS_CGROUP_PARENT override, falling back to node default")
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

- Code size: smallest of all per-server options.
- Upgrade survivability: as good as Option 1 (same fork/rebase story) — plus it needs
  **zero Panel changes**, so it survives Panel upgrades trivially too.
- Upstream acceptability: plausible as a companion to Option 1 in the same PR — it's
  a natural extension of an existing pattern (Wings already special-cases one env var,
  `SERVER_IP`, in exactly this loop).
- **Security note (important):** the variable must be admin-only
  (`user_viewable=false`, `user_editable=false`) on the egg/server. If it were
  user-editable, any server owner could move their own container into an arbitrary
  slice — worst case is escaping a resource-constrained tier into an
  unconstrained/transient one (a noisy-neighbor / DoS risk against other tenants on
  the same host), not a container-breakout, but still not something to expose to
  non-admins. This patch does not and cannot enforce that on the Wings side; it's a
  Panel-configuration responsibility, same as any other admin-only egg variable
  today.

### Comparison

| | Code size | Upgrade survivability | Upstream acceptability | Verdict |
|---|---|---|---|---|
| 1. Global `docker.cgroup_parent` | ~65 lines / 4 files | High (config-only addition, same shape as existing keys) | High — matches recent merged PRs on both forks (see §7) | **Do this** |
| 2a. Wings-side UUID→slice map | ~15 lines | High | Low (duplicates Panel state, not canonical) | Stopgap only |
| 2b. Reserved env var `WINGS_CGROUP_PARENT` | ~25 lines / 1 file | High, no Panel dependency | High (mirrors existing `SERVER_IP` special-case) | **Do this too** |
| 2c. Proper Panel field | Medium (Panel migration + UI + Wings) | Highest (canonical) | Uncertain, needs Panel maintainers | Defer — subsumed by §4 v3 |

*(2c in brief, assessed from a single-file read of the Panel's `EggVariable` model,
no full Panel clone: a new column on `servers` mirroring how `Container.Image`
already flows into Wings' `Configuration`, plus migration + admin form + API
transformer. Effort medium; only worth it as part of the fuller v3 design in §4.)*

---

## 4. Option 3 — Panel-managed slices, reconsidered

The first draft of this document rejected "Panel UI manages slices on the host"
outright. The operator counter-argument is fair and worth taking seriously: **an
admin running many Wings nodes does not want to hand-install slice unit files on
every node.** They want to install Wings, point it at the Panel, and manage
everything — including resource tiering — through the Panel UI and eggs. For a fleet
operator, "slices are sysadmin-managed IaC" translates to "every node is a snowflake
until you build config-management tooling the Panel was supposed to make
unnecessary."

So: what would Wings-managed slices actually take, and what does it cost?

### (a) Mechanism 1 — transient slices via the systemd D-Bus API (preferred)

Wings creates each slice itself at boot / server-creation time by calling systemd's
manager API (`StartTransientUnit` — the same call `systemd-run` and dockerd's systemd
cgroup driver use), e.g. via `github.com/coreos/go-systemd/v22/dbus`:

- `StartTransientUnit("wings-b87c0a5b.slice", "replace", props)` with properties
  (`MemoryMin`, `MemoryLow`, `MemoryHigh`, `MemoryMax`, `CPUWeight`, `IOWeight`, …)
  supplied from Panel-provided per-server/per-egg config.
- Later changes: `SetUnitProperties(runtime=true, …)` — the programmatic equivalent of
  `systemctl set-property`, which is exactly the **Finding D-safe** mechanism: systemd
  records the values and *re-applies* them on every daemon-reload instead of wiping
  them.
- Lifecycle fits Wings naturally: Wings already syncs all servers from the Panel at
  boot (before starting any container), so it can (re)create the transient slices
  then; delete the slice (`StopUnit`) when a server is deleted. Transient units don't
  survive reboot — but Wings restarting after boot recreates them before it starts
  any game container, so the window where a slice is missing is exactly the window
  where nothing runs in it.

### (b) Mechanism 2 — writing unit files + `daemon-reload`

Wings writes `/etc/systemd/system/wings-<uuid>.slice` files and triggers
`daemon-reload`. Persistent across reboots without Wings' involvement, but strictly
worse in practice: Wings needs write access to the host's `/etc`, has to own
file lifecycle/cleanup (orphaned unit files after server deletion or node
migration), and every change triggers a host-global daemon-reload. Note the irony:
daemon-reload is precisely the event Finding D identified as destructive to
non-systemd-owned cgroup state — Wings *causing* frequent reloads would increase the
blast radius for anything else on the host that still raw-writes cgroup attributes.
Mechanism 1 is cleaner on every axis except reboot persistence, which its lifecycle
already covers.

### (c) The honest security assessment

Wings runs **in a container** on this host (docker-compose, talking to the host's
`docker.sock`). To call the systemd manager API it would need the host's D-Bus
socket (`/run/dbus/system_bus_socket`) or systemd private socket
(`/run/systemd/private`) bind-mounted into the container — and the systemd manager
API is **root-equivalent** (`StartTransientUnit` can start arbitrary units, i.e.
arbitrary code as host root).

The honest framing, though, is that this does **not add a new privilege class**:
Wings already mounts `/var/run/docker.sock`, which is itself root-equivalent on the
host (anyone holding it can run a privileged container with `/` bind-mounted). What
it *does* do:

- widens the amount of Wings code wielding root-equivalent power (bigger audit
  surface, more places for a bug to live);
- turns a Panel compromise into direct host-resource-topology manipulation (though a
  Panel compromise already implies arbitrary-container execution via Wings, so the
  delta is modest);
- creates a new class of *non-malicious* failure: a Panel/egg misconfiguration could
  modify slices that govern non-Wings workloads.

The key mitigation — and it should be a hard rule in any upstream design — is a
**namespace constraint**: Wings may only ever create/modify slices matching
`wings.slice` / `wings-*.slice` (enforced by the same kind of check as
`ValidateCgroupParentValue`), never arbitrary unit names. That confines both bugs
and abuse to the Wings-owned subtree (§5). Rootless-mode deployments are excluded by
construction (no host systemd access) and must degrade gracefully to v1/v2 behavior.

### (d) Panel-side work for v3

- Egg schema: per-egg default slice-property block (`memory_min`, `memory_low`,
  `memory_high`, `cpu_weight`, `io_weight`, possibly `zswap_writeback` — the latter
  is systemd-version-gated and needs feature detection); per-server admin override.
- Delivery: extend the existing `ServerConfigurationResponse.Settings` blob → a new
  struct next to `Build environment.Limits` in `server/configuration.go` (same
  plumbing shape as existing fields; the Wings side is the easy part).
- Admin UI + validation + migration on the Panel; a Panel release to ship it. This is
  the long pole, and the reason v3 is a vision, not this quarter's patch.

### (e) Staged path (the actual recommendation)

| Stage | What | Panel changes | Host prep per node | Status |
|---|---|---|---|---|
| **v1** | `docker.cgroup_parent` in config.yml — all containers under one admin-named slice | none | install slice unit(s) once (IaC) | **patch compiled, §2 — deploy now** |
| **v2** | `WINGS_CGROUP_PARENT` reserved egg/server variable — per-server placement | none (admin-only egg variable, existing mechanism) | install per-server slice units (or accept transient) | **patch compiled, §3b — same PR** |
| **v3** | Panel/egg slice-property schema + Wings creates/owns transient `wings-*.slice` units via systemd D-Bus, applies properties via `SetUnitProperties` (Finding D-safe) | egg schema + admin UI + API | none — that is the point: zero per-node hand-config | **design sketch only (§4, §5); upstreamable long-term vision — propose as an RFC issue alongside the v1/v2 PR** |

v1/v2 keep slices 100% sysadmin-managed and are what this host deploys. v3 is the
multi-host answer, and framing the v1/v2 PR as "step one of this staged path" makes
the small patch *more* attractive upstream, not less — it's additive, opt-in, and
doesn't paint the project into a corner.

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
│   │                                        whole game-hosting tier:
│   │                                        MemoryMin ≥ Σ child floors   (protection budget)
│   │                                        MemoryHigh/Max = tier ceiling (optional)
│   │                                        CPUWeight/IOWeight vs other tiers
│   ├── wings-b87c0a5b.slice              ← per-server slice (short server UUID)
│   │   │                                    MemoryMin=6G MemoryLow=12G MemoryHigh=8G
│   │   ├── docker-<id>.scope             ← the Soulmask container
│   │   └── wings-b87c0a5b-paks.slice     ← optional: per-server pak slice, auto-nests
│   │                                        MemoryMin=<hot pak set>, MemoryZSwapMax=0
│   └── wings-<uuid2>.slice               ← next game server: own floor/ceiling
│       └── docker-<id>.scope
│
├── interactive.slice                     ← devcontainer + AI agents (plan §3.3)
└── besteffort.slice                      ← dstdns test stack, builds (plan §3.3)
```

Where the protection-chain values go (the §0 math, made concrete):

- **`wings.slice` MemoryMin ≥ sum of all child floors** (per-server `MemoryMin` +
  per-server pak floors). A top-level slice's `memory.min` is directly effective
  against global reclaim — there is no ancestor above it to cap it. If the parent
  budget is smaller than the sum of child claims, children compete proportionally for
  the shortfall — keep the invariant explicit and monitored.
- **Per-server floors/ceilings live on `wings-<uuid>.slice`**, not on the Docker
  scope. This is the quiet superpower of the design: slice values are
  **systemd-owned** (unit file or `set-property`), so they are *re-applied* on every
  daemon-reload — Finding D becomes a non-issue for exactly the values that matter
  most. Today's watcher writes to the transient `docker-<id>.scope` and is one
  `apt install` away from being wiped.
- `memory.zswap.writeback=0` semantics at the slice level (does disabling it on the
  per-server slice cover the scope beneath, i.e. is the check hierarchical?) —
  **unverified**; test on this host before relying on it, otherwise keep that one
  knob applied per-scope via `set-property`, as `setup-cgroups.sh` does now.
- **PSI/accounting fall out for free**: `/sys/fs/cgroup/wings.slice/memory.pressure`
  = whole-tier pressure; `wings.slice/wings-<uuid>.slice/memory.pressure` =
  per-server pressure — exactly the per-tier/per-workload split
  `CGROUP-MONITORING.md` builds its monitoring around, with no scope-PID chasing.

How the stages map onto this tree: **v1** puts every server directly under
`wings.slice` (tier bound + one shared floor; per-server floors not yet
expressible — on this single-game host that is already the full win). **v2** adds
`WINGS_CGROUP_PARENT=wings-b87c0a5b.slice` per server, with the sysadmin
pre-installing the child slice units (dash-naming auto-nests them). **v3** has Wings
create the child slices itself. On *this* host, the existing `soulmask.slice`
(+ nested `soulmask-paks.slice`) already plays the role of the per-server slice —
with exactly one game server, `cgroup_parent: soulmask.slice` and the `wings.slice`
design are equivalent; adopt the `wings-*` naming when a second server or the
upstream PR makes the rename worth it.

---

## 6. Alternatives analysis — why host slices at all?

The obvious counter-question: *"why not put the full cgroup config in the egg (or
per-server via the Panel UI) and have Wings apply it at container start — no slices,
no host-side config at all?"* Three facts close this off:

1. **Docker's API cannot express a protection floor.** Verified against the complete
   `container.Resources` struct in `moby/moby v28.3.3`
   (`api/types/container/hostconfig.go`): it offers `Memory` (→ `memory.max`),
   `MemoryReservation` ("memory soft limit" — mapped to `memory.low` on cgroup v2 by
   runc), `MemorySwap`, `MemorySwappiness`, CPU shares/quota/sets, `Blkio*`
   weights/throttles, `PidsLimit`, ulimits, devices — and **no `memory.min` field of
   any kind, and no zswap knobs**. So an egg → Docker HostConfig pipeline, no matter
   how rich the egg schema, physically cannot build a `memory.min` floor or set
   `memory.zswap.*`. (Wings already sends `MemoryReservation` today — `memory.low`
   at best, and see point 3 for why even that is currently moot.)
2. **Wings raw-writing cgroup files after container start is a trap.** The
   alternative "Wings echoes values into `/sys/fs/cgroup/.../docker-<id>.scope/*`
   post-create" was **disproven live on this host on 2026-07-07**
   (`plan-host-resource-governance.md` §1.5 Finding D): any `systemctl daemon-reload`
   — triggered by any apt package that ships a unit — silently resets every
   raw-written attribute on the transient scope back to Docker defaults. The
   reload-safe channel is `systemctl set-property` / D-Bus `SetUnitProperties`, i.e.
   going through systemd — which requires the same host-systemd access as §4's v3
   while delivering less (per-scope values still vanish with the container; slice
   values persist across container recreates).
3. **The hierarchical math is decisive regardless of mechanism.** Even a *perfect*
   per-container configuration — imagine Docker grew a `MemoryMin` field tomorrow —
   is capped by every ancestor's `memory.min` on the path to the root (§0). A 6 GB
   floor under `system.slice(min=0)` protects nothing; that is Finding A, observed
   live, not theory. Some host-level ancestor configuration is therefore
   **mathematically unavoidable** for protection floors: somebody has to put a
   protection budget on the ancestor slice. Per-egg config can carry the
   *per-server* values; the *tier/ancestor* budget must live host-side — either as
   sysadmin IaC (v1/v2) or as a Wings-managed `wings.slice` subtree (v3).

**Conclusion: egg-level cgroup config and host slices are complements, not
alternatives.** The egg is the right home for per-server *values* (floor sizes,
weights); the slice hierarchy is the only place the *structure* (ancestor budgets,
tier separation, reload-safe ownership) can live.

---

## 7. Upstream PR strategy

Checked both projects' recent PR/issue activity via the GitHub API (public,
unauthenticated) on 2026-07-07:

**`pterodactyl/wings`** (upstream, default branch `develop`):
- Not archived, 1019 stars, pushed 2026-06-29, 51 open issues — actively maintained.
- Zero existing PRs or issues mention "cgroup parent" / slice support
  (`search/issues?q=repo:pterodactyl/wings+cgroup` → 1 hit, an unrelated dependency
  bump).
- But recent merged work shows real appetite for cgroup v2 nuance: PR #324, **"Only
  set the container block IO weight when the host supports io.weight"** (merged
  2026-05-30), fixes exactly the kind of cgroup-v2-vs-v1 edge case this proposal also
  touches — and it's already reflected in the `blkioWeightSupported()` helper in
  `environment/settings.go` that this study read. Good signal, but this is the only
  cgroup-adjacent PR in the repo's history.
- No `CONTRIBUTING.md` found in the repo or a `.github` org-level one.

**`pelican-dev/wings`** (independent hard fork of Pterodactyl panel+wings, not a
GitHub "fork" flag — fully detached):
- Not archived, 296 stars, pushed 2026-07-04 (3 days before this study) — **more
  recent activity than upstream**, and a visibly faster merge cadence for small,
  additive, node-config features: merged in the last ~3 weeks alone: "Add disk quota
  support", "Add quiet config option", **"feat: add support for container network
  mode"** (closed, not merged, but only because the *author* redirected it to a
  standalone side-image rather than any maintainer pushback —
  `engels74/wings-vpn`), "add conditional support for ioweight" (their version of the
  same `blkioWeightSupported()`-style fix). This is a near-exact precedent: a new,
  additive `DockerConfiguration`/`HostConfig` knob, reviewed (they run
  `coderabbitai` automated review) and merged quickly.
- This is the more receptive target for a `cgroup_parent` PR. It is also the more
  plausible home for the §4 v3 vision — Pelican controls both panel and wings under
  one org, so a cross-cutting egg-schema + wings feature has one review pipeline
  instead of two.

**Recommendation:**
1. Build and run the local fork now (§8) — prod needs it immediately, independent of
   any upstream outcome.
2. Submit the Option 1 + Option 2b patch (§4's v1+v2) to `pelican-dev/wings` first;
   cross-post/PR the same to `pterodactyl/wings` in parallel (low cost, no reason not
   to try both). Frame it explicitly as stage one of the §4 staged path, and open a
   companion RFC issue sketching v3 (`wings.slice` hierarchy + D-Bus-managed
   per-server slices, §5) so reviewers see the small patch as a step on a coherent
   road, not a one-off knob.
3. If/when either merges, rebase the local image onto that tag instead of maintaining
   a bespoke diff indefinitely.

---

## 8. Local deployment path

### Build (on this host, using the existing Docker daemon — no registry needed)

```bash
# 1. Get the source, apply the patch (as a local branch/commit, never push to prod paths)
git clone --branch v1.13.1 https://github.com/pterodactyl/wings.git /path/to/build/wings
cd /path/to/build/wings
git apply /path/to/option1+2b.patch   # or cherry-pick the commits

# 2. Build with the repo's own Dockerfile (verified: golang:1.24.11-alpine builder ->
#    gcr.io/distroless/static:latest runtime, CGO_ENABLED=0, ldflags inject Version)
docker build \
  --build-arg VERSION=1.13.1-gstammtisch-cgroupparent.1 \
  -t wings-local:1.13.1-cgroupparent.1 \
  .
```
Building directly on the host (same Docker daemon that will run the container) avoids
any push/pull step — the image lands straight in the local image store that
`docker compose` will resolve against.

### Deploy

1. Edit `/root/ptero-wings/docker-compose.yml`: change
   `image: ghcr.io/pterodactyl/wings:latest` → `image: wings-local:1.13.1-cgroupparent.1`.
   Use a plain local tag (no registry-looking prefix) deliberately: a future
   `docker compose pull` will then **fail loudly** ("no such image" / "repository does
   not exist") instead of silently reverting to the stock upstream image — this is a
   feature, not a bug, given there's no watchtower/cron auto-update on this host
   (verified: no crontab, no systemd timer, no watchtower container).
2. Add to `/etc/pterodactyl/config.yml` under `docker:`:
   ```yaml
   docker:
     cgroup_parent: soulmask.slice
   ```
3. **Install and load the `soulmask.slice` unit file first.** Verified on this host
   right now: `soulmask.slice` is currently a **transient** slice — `systemctl show
   soulmask.slice` reports `FragmentPath=` (empty, no unit file) and `MemoryMin=0`.
   `plan-host-resource-governance.md` §3.3 already specifies the intended unit
   (`MemoryMin=5G`, `MemoryLow=12G`, etc.) but it has not been installed under
   `/etc/systemd/system/` yet (only `dev-workloads.slice` and `soulmask-paks.slice`
   exist on disk today). Install it and `systemctl daemon-reload` **before** flipping
   `cgroup_parent` on, or the game container will land in the same limit-less
   transient slice it's already in today — no regression, but no benefit either.
4. Recreate the Wings management container:
   `cd /root/ptero-wings && docker compose up -d --force-recreate`. This only touches
   the Wings process itself, not the running game container.
5. **The running Soulmask container will NOT move on its own.** Confirmed by source:
   `Create()` returns early (`ContainerInspect` succeeds → no-op) whenever the
   container already exists, and no code path recreates the main server container on
   image/config change (`Reinstall()` only re-runs the *installer* container, never
   touches the main one). To apply the new `cgroup_parent` to the existing server:
   stop it from the Panel, then remove the container object on the host
   (`docker rm <server-uuid>` — the bind-mounted server data directory is untouched),
   then start it from the Panel again. This is a **planned, brief outage window**, not
   a side effect to be surprised by.

### Rollback

- Revert step 1 (`image:` back to `ghcr.io/pterodactyl/wings:latest`) and step 2
  (remove/blank `cgroup_parent`), `docker compose up -d --force-recreate`. Since the
  stock image never sets `CgroupParent`, any already-recreated game container simply
  keeps whatever cgroup it was last placed in — no forced second outage is required
  for rollback (only for *adopting* the new placement, per step 5).
- Keep the previous `wings-local:*` image tag (or the stock `ghcr.io/pterodactyl/wings:latest`
  one already cached locally) untouched as an instant fallback; don't `docker rmi` it
  until the new build has run clean for a while.

### Risk assessment

- **Auto-update risk: low.** Verified no watchtower container, no root crontab, no
  systemd timer touching wings on this host — updates are 100% manual
  (`docker compose pull && up -d`, or Pterodactyl's official one-line install script
  re-run). The main residual risk is a future *person* re-running the official
  install script, which would overwrite `docker-compose.yml`'s `image:` line back to
  upstream `latest`. Mitigate by leaving a comment in the compose file and noting it
  in this repo's runbook docs.
- **Config compatibility on next official Wings upgrade: low-medium.** The patch adds
  one optional key with a zero-value default (`""`), so an unpatched future official
  image would just silently ignore `docker.cgroup_parent` in the YAML (unknown keys
  are not rejected by the `yaml.v2` unmarshal used in `config.FromFile`) — you'd lose
  the feature, not break startup. The real risk is purely "forgot this is a custom
  build" bit-rot; keeping the diff small and rebasing periodically against new
  upstream tags is the main defense.
- **Build/runtime risk: verified low.** The full patch was built with `go build ./...`
  and `go vet` against the pinned `go.mod` (`github.com/docker/docker
  v28.3.3+incompatible`, Go 1.24.5 toolchain fetched for this study) with **zero
  errors or vet warnings in the changed files** (two pre-existing, unrelated vet
  warnings exist elsewhere in `server/resources.go` / `server/server.go` /
  `server/filesystem/filesystem_test.go` — not touched by this patch). The resulting
  binary runs (`wings version` → `wings vdevelop`). `ValidateCgroupParentValue` was
  additionally exercised standalone against `"", "soulmask.slice", "soulmask",
  "custom.slice/soulmask.slice", " soulmask.slice", "soulmask.slice ", "system.slice"`
  and produced the expected accept/reject result for every case.
- **Not verified (flagged):** no live container was actually created with
  `CgroupParent` set against a real dockerd in this study — the runtime effect (does
  `docker-<id>.scope` really land under `soulmask.slice` when the systemd driver is
  in use) rests on the documented Docker/moby behavior of `HostConfig.CgroupParent`
  plus this host's own existing manual precedent (`dev-workloads.slice`'s doc comment:
  `docker run --cgroup-parent=dev-workloads.slice ...`), not on an end-to-end test
  performed here. Recommend a smoke test (spin up a throwaway container with
  `--cgroup-parent=soulmask.slice --label test=cgroup-parent-poc` via plain `docker
  run`, confirm its `/proc/<pid>/cgroup`) before rolling the patched Wings into
  production — this exercises the exact Docker/systemd mechanics without touching
  Wings at all.

---

## 9. Benefit summary for the governance design

- **Fixes the protection-chain gap directly** (§0, Finding A). Today (verified): the
  live game container sits at `system.slice/docker-<id>.scope`; `soulmask.slice` is
  transient with `MemoryMin=0`. The only current workaround
  (`plan-host-resource-governance.md` finding 14) is `systemctl set-property
  system.slice MemoryMin=7G`, which protects the *entire* system slice (sshd, dockerd,
  Wings, and every other system service) — a much blunter instrument than protecting
  just the game's own slice. With `cgroup_parent: soulmask.slice`, the existing
  `soulmask.slice` unit design in §3.3 of that plan (`MemoryMin=5G`, `MemoryLow=12G`,
  nested `soulmask-paks.slice`) becomes directly enforceable against the actual game
  cgroup, and the `system.slice`-wide `MemoryMin` hack can be narrowed or dropped.
- **Makes the floors reload-proof** (Finding D). Values on a slice are systemd-owned
  and re-applied on every daemon-reload; the current watcher's raw scope writes are
  not. Moving protection from the scope to the slice removes the single most
  surprising failure mode found during this governance work.
- **Per-egg / per-server slice reference becomes possible** without any Panel change
  (§3b): different eggs/servers on the same node can reference different
  admin-defined slices purely through an admin-only reserved variable — and the
  `wings.slice` / `wings-<uuid>.slice` hierarchy (§5) gives each server its own
  floor, ceiling, and PSI accounting under one tier-wide knob.
- **"Panel UI manages slices on host"** — no longer rejected outright; reframed as
  the **v3 stage** of the staged path (§4): legitimate for multi-node operators,
  technically feasible via systemd's D-Bus transient-unit API with a hard
  `wings-*.slice` namespace constraint, but out of scope short-term (Panel schema +
  release cycle + security review). For v1/v2 — everything this host deploys —
  slices remain 100% sysadmin-managed, version-controlled IaC
  (`files/etc/systemd/system/*.slice` + `setup-cgroups.sh`), exactly as
  `plan-host-resource-governance.md` finding 13 concluded: install once, reference
  forever, degrade gracefully to a transient slice if the reference is missing.

---

## Appendix — verification method

- Cloned `https://github.com/pterodactyl/wings` at tag `v1.13.1` into scratch space
  (not this repo, not the host's `/root/ptero-wings`).
- Read (not grepped-and-assumed) every file this proposal touches or cites:
  `environment/docker/container.go`, `server/install.go`, `config/config_docker.go`,
  `environment/settings.go`, `environment/config.go`, `server/configuration.go`,
  `server/server.go`, `server/manager.go`, `remote/types.go`, `cmd/root.go`,
  `config/config.go`, `Dockerfile`, `Makefile`.
- Downloaded a Go 1.24.5 toolchain into scratch space (no `go` was preinstalled) and
  ran `go build ./...`, `go vet ./config/... ./cmd/... ./environment/... ./server/...`,
  and a direct `go build -o /tmp/... wings.go` + `wings version` against the fully
  patched tree — all clean (pre-existing, unrelated vet warnings noted above; none in
  changed files).
- Compiled and ran `ValidateCgroupParentValue` standalone against 7 representative
  inputs to confirm accept/reject behavior matches intent.
- Fetched `moby/moby` tag `v28.3.3` (matches `go.mod`'s pinned
  `github.com/docker/docker v28.3.3+incompatible`) `api/types/container/hostconfig.go`
  to confirm `CgroupParent` and `Resources` field shapes — including reading the
  **complete** `Resources` struct to confirm the §6 claim that it contains no
  `memory.min` equivalent and no zswap fields — and `api/types/system/info.go` to
  confirm `CgroupDriver`/`CgroupVersion` exist on `client.Info()` for the
  "not implemented" note in §2.
- Fetched `pterodactyl/panel`'s `app/Models/EggVariable.php` (single file, not a full
  clone) to confirm `user_viewable`/`user_editable`/`RESERVED_ENV_NAMES` exist and that
  `WINGS_CGROUP_PARENT` isn't reserved.
- Queried the public GitHub API (unauthenticated) for PR/issue history and repo
  metadata on both `pterodactyl/wings` and `pelican-dev/wings`.
- Findings A and D are cited from `plan-host-resource-governance.md` §1.5 (operator's
  live verification on this host, 2026-07-06/07), re-read in full for this revision —
  not re-derived independently here.
- §4's D-Bus mechanism description (`StartTransientUnit` / `SetUnitProperties`,
  `go-systemd/v22/dbus`) is from prior knowledge of systemd's API and was **not**
  prototyped in this study — flagged as design sketch, not compiled code. Likewise
  the §5 note on `memory.zswap.writeback` hierarchy semantics is explicitly
  unverified.
- Read-only checks on **this** host: `docker exec ... wings version` (confirmed
  v1.13.1), `cat /etc/pterodactyl/config.yml` (docker section only, secrets excluded,
  confirmed it matches the struct read from source), `sudo cat
  /root/ptero-wings/docker-compose.yml`, `systemctl list-units --type=slice`,
  `systemctl show soulmask.slice`, `/proc/<pid>/cgroup` for the running game
  container's PID, presence/absence of crontab/systemd-timer/watchtower for
  auto-updates. No file on the host was written; no container was created, removed,
  or restarted.
