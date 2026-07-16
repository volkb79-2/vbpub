# Spike plan — multi-stack capacity (dstdns stack mutex > 1)

Status: **plan / blocked on a RAM decision** (2026-07-15). Carved from the
user's directive #4; NOT yet dispatched as a live spike because the host
cannot currently hold a second landscape (see §0).

## 0. The binding finding (measured 2026-07-15)

Host: **15 GB RAM, ~2 GB available** (12 used by the running landscape).
KSM `general_profit` is **negative** (~−1 MB): with almost nothing opted in
(`pages_sharing`=14), ksmd metadata costs more than it saves. A second full
dstdns landscape (~25 containers; SkyWalking OAP alone is a multi-GB JVM)
**will not fit** — booting it now risks OOM-killing the *running* factory.

Therefore the spike is **sequenced in three gated steps**, each with a
go/no-go, rather than one "boot a second stack" action:

## 1. Step A — make KSM savings real (unblocks everything)

The mdt shim (`modern-debian-tools`) opts in only mdt-derived processes.
The RAM hogs are upstream images. All three SkyWalking containers are
**dynamically linked** (measured): oap=glibc, banyandb=glibc, ui=musl — so
`LD_PRELOAD` works, but needs BOTH a glibc and a musl build of the shim.

- Build `ksm-optin.so` for glibc AND musl (the musl one from an alpine
  builder stage); publish both in a tiny `ksm-optin` OCI layer or a host
  path bind-mounted read-only.
- For each big upstream container (skywalking-oap, banyandb, postgres,
  redis, minio, authentik, vault), add via ciu container config:
  `environment: LD_PRELOAD=/opt/ksm/ksm-optin-<libc>.so` +
  a read-only bind of the matching `.so`. Java (OAP): the constructor runs
  in the launcher process; `PR_SET_MEMORY_MERGE` sets `MMF_VM_MERGE_ANY` on
  the mm, so the whole JVM heap (one process, no heap-fork) becomes
  mergeable — verified-plausible, MEASURE it. Distroless/static caveat:
  none of the three SkyWalking images are static, but re-check any image
  added later (`ls /lib*/ld-*` → absent = static = preload is a no-op;
  those need a different opt-in and are out of scope).
- Gate A: `general_profit` goes clearly positive and `free` shows materially
  more available (target: enough headroom that a *reduced* second stack
  fits). Measure with the existing game_stuff MEASUREMENTS methodology.

## 2. Step B — a REDUCED-profile worktree stack (not a full clone)

Most packages under test need a handful of services, not the whole
landscape (SkyWalking/analytics are rarely the thing under test). ciu
already gives per-path identity (INSTANCE_ID from the worktree path →
unique container names + a unique internal network), and today's mountinfo
fix makes the worktree's physical path correct.

Worktree stack profile (`ciu.toml.j2` override rendered per worktree):
- **No host ports**: every service uses `expose:` not `ports:` (the spike
  needs no external access — the test-runner reaches it over the shared
  docker network). This alone dissolves the 8443/9558/… publisher-collision
  problem without any tls-edge work.
- **Service subset via ciu profile** (`CIU_SERVICES_PROFILE`): boot only
  {postgres, redis, consul, vault, the app services under test}; SkyWalking
  / analytics / MinIO omitted unless the package needs them.
- **Own internal network**: ciu's per-instance `<project>-<instance>-network`
  already isolates east-west; no cross-talk with the main landscape.
- **One suffix scheme, everywhere (flat).** The same `<project>-<instance>`
  ciu identity that names containers and the internal network is ALSO the
  FQDN suffix if/when a worktree stack ever gets a human-facing edge route:
  `<project>-<instance>.<public_fqdn>` (e.g. `dstdns-p11.gstammtisch.dchive.de`).
  Keep it FLAT (one hyphenated label, per dstdns D-006) — not nested
  `<instance>.<project>.<public_fqdn>` — so container name, network name, and
  hostname all derive from one suffix AND the existing
  `*.gstammtisch.dchive.de` wildcard covers every instance with no new cert.
  The main landscape is just the zero-suffix case: `<project>.<public_fqdn>`
  (infra-P24's `{{ project_name }}.{{ public_fqdn }}` template).
- Singleton safety: each worktree stack has its OWN vault/consul/postgres
  with isolated volumes (fine — separate data), so "singletons never run
  twice" becomes "singletons never run twice ON THE SAME instance id",
  which ciu enforces by naming. Confirm no host-level singleton (a bound
  host port, a fixed host path) leaks — the no-host-ports rule covers ports;
  audit bind mounts for fixed host paths in the reduced profile.
- Gate B: a reduced worktree stack boots green, the test-runner runs a
  live-lane test against it, teardown (`ciu --reset` in the worktree) leaves
  the main landscape untouched. Measure its RAM; that number × desired
  parallelism must fit post-Step-A headroom.

## 3. Step C — raise the mutex, add provisioning/teardown hooks

Only after A+B: model the live stack as a **counted** host resource. But
note a subtlety — a counted flock lease gives N *slots*, yet each slot must
map to a DISTINCT instance id / network / volume set. So capacity>1 needs a
per-attempt "stack instance" allocator (the worktree path already yields
one; the lease just bounds how many run at once). Handoffctl side:
- `mutexes.stack.capacity = 2` (config; UI-settable per P15).
- A project-adapter hook: on acquiring a stack slot for an exclusive
  package, render+boot the reduced worktree stack; on release, teardown.
  This is dstdns-specific glue, not generic daemon code.
- Gate C: two exclusive packages run concurrently against two isolated
  stacks, both gates green, both torn down, host stays healthy.

## Open questions for the user (do not proceed past Step A without answers)

1. tls-edge for worktree stacks: the no-host-ports rule means the spike
   needs NO tls-edge integration. Routing a worktree stack's UI to a human
   (e.g. `dstdns-p11.gstammtisch.dchive.de` — FLAT/one-label, per D-006, so
   the existing `*.gstammtisch.dchive.de` wildcard covers it; the nested
   `p11.dstdns.gstammtisch.dchive.de` form would NOT be covered and would
   need a new per-project wildcard cert) is a SEPARATE nice-to-have — worth
   doing for the MAIN landscape (infra-P24 already moves its reverse-proxy
   TLS to tls-edge with host `dstdns.gstammtisch.dchive.de` derived from ciu
   `project_name`; worktree hosts extend that same template with the ciu
   instance id). Decouple from the spike?
2. Reduced-profile is the pragmatic answer to 2 GB; do you also want the
   full-clone path proven (needs more RAM than this host has — would need a
   bigger host or aggressive KSM), or is reduced-profile sufficient?
3. Step A touches the LIVE landscape (adding LD_PRELOAD env to running
   singletons = a restart each). That's a `Stack: exclusive` action on the
   main stack. Run it as its own carved package under the factory, or hand-
   apply once and measure?

## 2026-07-16 — Step B EXECUTED (manual, dstdns): FAIL on isolation, one root cause

Full report: `dstdns:nyxloom-trove/reports/SPIKE-multistack-stepB-REPORT.md`.
New **binding finding replacing §0**: RAM is no longer the blocker (5–8 GB
available; a full second landscape measured ≈2.4 GB and booted healthy in
~7 min, zero rebuilds). The blocker is **ciu's compose project name = stack
name, shared across instances** — `docker compose up` from the second
instance adopted and REMOVED the main landscape's containers
(same project+service labels, changed container_name → recreate). Fix in
ciu deploy: instance-scope the project (`-p <stack>-<INSTANCE_ID>` or
compose `name:`). Until that lands, NEVER `ciu up` a second instance of a
stack on a shared host. Secondary findings: `CIU_SERVICES_PROFILE=core,db
ciu up` did NOT narrow the deployment (all 22 stacks deployed); rendered
`ciu.toml` hand-edits are clobbered by `ciu up` re-render (overrides must
go in the render-input layer). Step A (KSM) is downgraded to
optional-for-headroom; Step C gains the F1 fix as a dependency.

Addendum (same day, operator discussion): architecture for per-worktree
stacks ratified and persisted in dstdns `nyxloom-trove/GUIDE.md` §3 (two
modes attach/isolate, identity dimensions incl. image-tag isolation,
teardown rules, carver env-recipe contract). Precise upstream ciu fix
contracts in GUIDE §3.5: F1 = pass `-p {project}-{env_tag}-{stack}` at all
compose lifecycle sites (engine.py:838 up, :700 down, ~:1332 shipped) with
a same-name/foreign-project migration guard; F2 = drop the empty-phase-
union→ALL coercion in resolve_profiles (contradicts SPEC S7.5a's own
narrowing example). RAM headroom is TEMPORARY — retry promptly once fixes land.
