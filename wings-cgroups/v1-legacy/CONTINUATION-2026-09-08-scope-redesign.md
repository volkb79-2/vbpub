# Continuation brief — scope redesign, 2026-09-08

State at hand-off after the rebase-to-v1.13.3 + slice→scope redesign session.
Read this before touching `wings-cgroups` again.

## What shipped

Two commits on `main` in `/workspaces/vbpub`:

| commit | what |
|---|---|
| `a30a0db6` | mechanical rebase of the unchanged 11-patch series onto upstream `v1.13.3` |
| `d34969e6` | the redesign: series retargeted from per-server slice to container scope, renumbered `0001`–`0009` |

Plus a docs commit (see `git log wings-cgroups/`).

- Authoritative series: `v1-legacy/patchstack/patches/pterodactyl-v1.13.3/0001..0009-*.patch`
- Working branch in the disposable clone: `cgroup/v1.13.3` in
  `v1-legacy/build/wings-pterodactyl` (backup ref `backup/pre-redesign-v1133`
  holds the pure-rebase, pre-redesign tip)
- `stack.conf` `PTERODACTYL_REF="v1.13.3"`
- Image built and present on the host daemon: **`wings-local:1.13.3-cgroup.1`**
  (nothing deployed — no compose file was touched, the live host still runs
  `wings-local:1.13.1-cgroup.11`)

## Verified

- `scripts/test.sh pterodactyl` — build + strict vet + unit tests: green
- `INTEGRATION=1 scripts/test.sh pterodactyl` — the `dockerintegration`
  placement tests against the real daemon: 5/5 pass
- `scripts/build-image.sh pterodactyl cgroup.1` → `wings-local:1.13.3-cgroup.1`
- `test/smoke-placement.sh` — PASS (run against the throwaway
  `wings-smoke.slice`, deliberately NOT against the live `wings.slice`)
- `test/e2e-systemd/run-e2e.sh` — **E2E: ALL PASS**, against a real systemd
  over D-Bus in the privileged harness. This is the one that matters: the
  `systemdintegration` tests were rewritten for the new design and now stand a
  real process up in a transient `docker-<64hex>.scope` (via
  `StartTransientUnit` + `PIDs`, exactly how Docker's systemd cgroup driver
  creates a container scope) and read the properties back off the **`Scope`**
  D-Bus interface. Three new cases pass:
  - `TestApplyScopePropertiesIntegration` — `MemoryMin`/`MemoryLow`/`CPUWeight`
    land on a real scope; a second `Apply` updates in place; a property omitted
    from the second request keeps its previous value.
  - `TestScopeMemoryCurrentIsReadableIntegration` — proves patch 0008's
    retargeted read actually works on a scope. Had the interface still said
    `"Slice"`, this fails.
  - `TestApplyRefusesASliceIntegration` — the guard against ever pointing a
    container property write at a slice.
- `patchstack/scripts/coverage.sh pterodactyl` (or `COVERAGE=1 test.sh`) —
  assay 6.0.0 R1 changed-line coverage: **PASS**, 336/624 changed lines =
  **53.85%**, floor 50.0. R0+R1 only; assay's Go adapter is registered at
  `{"R1"}` and mutation testing is unconditionally UNSUPPORTED for Go, so this
  is coverage, **not** mutation testing — do not claim more.

  **What that number excludes.** It is a plain `go test` profile, so the
  `systemdintegration` tests are not in it: `sysd.go` (~99 uncovered changed
  lines) and `ensure.go` (~41) — the D-Bus code this series exists for — count
  as uncovered even though the e2e harness above does exercise them and
  passes. `cmd/root.go` is uncovered outright (~95 lines, no test in the
  package); excluding `cmd/` the same run is 63.5%. `server/` and
  `internal/database/` (~964 changed lines) are deliberately outside
  `source_roots`. Merging the e2e profile in (`go test -c -cover` +
  `GOCOVERDIR` + `go tool covdata textfmt`) touches `run-e2e.sh`, the e2e
  Dockerfile and the artifact hand-off, and is open follow-up.

## NOT verified — the honest gaps

1. **Nothing was run against a live Wings.** The post-`ContainerStart()`
   application path (`environment/docker/scope.go`) has never executed against
   a real Pterodactyl server. The D-Bus layer beneath it is now e2e-proven, but
   the wiring from `Environment.Start` through `ScopeEnsureRequest` to `Apply`
   is only covered by unit tests plus reading.
2. **The pelican series is untouched.** See below.

## Deferred: the pelican series

`v1-legacy/patchstack/patches/pelican-main/` is still the **old 11-patch,
pre-redesign, pre-rebase** series and is now inconsistent with pterodactyl.
It was deliberately not ported — the task ranked it explicitly lower than the
pterodactyl series, and the pterodactyl redesign consumed the session.

To port it: `scripts/clone.sh pelican`, `scripts/apply.sh pelican`, then repeat
the same forward-build the pterodactyl series got (below). The pelican tree
differs mainly in `config/` layout, so expect the `config_docker.go` edits to
need re-siting; the `internal/cgroups` package is portable almost verbatim.

## How the series was built, and how to rebuild it

**Do not scripted-rebase this stack and do not `--amend` a non-HEAD commit** —
both are recorded failure modes (memories `git-patchstack-rebase-care`,
`wings-patchstack-amend-care`). The whole series was built **forward**, so the
commit being edited was always HEAD:

```bash
cd v1-legacy/build/wings-pterodactyl
git checkout -b <branch> v1.13.3
P=../../patchstack/patches/pterodactyl-v1.13.3
git am -3 $P/0001-*.patch ... $P/0004-*.patch
#   ... edit, then: git add -A && git commit --amend
git am -3 $P/0005-*.patch ... $P/0009-*.patch
../../patchstack/scripts/export-patches.sh pterodactyl
```

That is exactly how the stale `internal/cgroups` package doc comment was fixed
*inside* 0004 rather than smeared onto the tip. Reuse it for any further edit
to a middle patch.

## Load-bearing seams

| file:symbol | why it matters |
|---|---|
| `internal/cgroups/scope.go:ScopeName` | derives `docker-<64 hex>.scope`; returns `""` for anything else. A short id or a name would produce a unit systemd does not know, and every property call would be a silent no-op. |
| `internal/cgroups/scope.go` floor ledger | keyed by **server UUID**, not scope name, so a restart replaces its own entry instead of leaking one per start. Feeds only the log line. `ForgetServer` is called from `Environment.Destroy`. |
| `internal/cgroups/sysd.go:Apply` | the whole D-Bus write. Refuses any non-scope unit name up front. |
| `internal/cgroups/sysd.go:memoryCurrent` | reads `MemoryCurrent` from the **`"Scope"`** interface. This is patch 0008's entire subject. A wrong interface name is not a compile error, produces no partial behaviour, and shows up only as a ramp that silently never runs. |
| `internal/cgroups/sysd.go:sliceMemoryMin` | reads the tier's `MemoryMin` from the **`"Slice"`** interface — different unit type, different interface, on purpose. |
| `environment/docker/scope.go:applyScopeProps` | the post-`ContainerStart()` application point and the accepted timing window. |
| `environment/docker/power.go` | two call sites: `PhaseStartup` after `ContainerStart`, `PhaseSteady` when re-attaching to an already-running container. |
| `config/config_docker.go:ResolveServerCgroupParent` | placement + `managed`. Override == node value is the opt-out. |
| `config/config_docker.go:ScopeEnsureRequest` | `ApplyDefaults`/`Parent` are set only when the server is on the node tier. |
| `server/slice_phase.go:sliceRequest` / `resolveScope` | deliberately split: `sliceRequest` decides *whether* to apply without needing Docker (so it stays unit-testable); `resolveScope` does the inspect at apply time. `req.Owner`, not `req.Scope`, is the "no request" sentinel. |

## Open questions a reviewer should press on

1. **IO weight now has two writers on one file.** The Panel's per-server
   "Block IO Weight" reaches `io.bfq.weight` on `docker-<id>.scope` via runc;
   Wings' `IOWeight` reaches the same unit and systemd re-derives
   `io.bfq.weight` from it. Under the retired shape these composed
   multiplicatively across two cgroup levels. They no longer do. Documented in
   `CGROUP-SEMANTICS.md` Rule 7 as "use one or the other" — **not verified by
   experiment.** Worth an actual measurement on the e2e harness.
2. **Same collision for the memory ceiling.** The Panel's memory limit reaches
   the scope via Docker; Wings' `MemoryHigh`/`MemoryMax` now reach the same
   unit. Under the old shape a *looser* Wings value was inert (min-along-path);
   on one unit a looser value **replaces** the Panel's. That is a real change in
   blast radius for a misconfigured `WINGS_CG_MEMORY_MAX` and deserves an
   explicit decision — clamp to the Panel's value, or document loudly.
3. **The audit surface moved and shrank.** `systemctl show wings-<uuid>.slice -p
   MemoryMin` used to answer "what floor is this server committed to?" whether or
   not it was running. The answer now lives on `docker-<id>.scope`, which exists
   only while the container runs. `TODO.md` records this as an accepted cost, not
   a refuted one.
4. **The tripwire's threshold is read live** from the tier slice's own
   `MemoryMin` rather than from a config knob. That is deliberate (a knob can
   drift from reality; this cannot) but it is a judgment call beyond the literal
   brief, and it means the warning silently does not fire on a node whose
   `wings.slice` declares no `MemoryMin`.

## Still to do

- [ ] Merge the e2e coverage profile into the assay lane (see above) — the
      lane's headline number understates the series precisely where it matters
- [ ] Port the pelican series
- [ ] Independent adversarial review of both commits (estate rule: **every**
      merged change gets one, no size exception)
- [ ] `v1-legacy/README.md`, `v1-legacy/pr/*.md`,
      `v1-legacy/t2-per-server-placement/README.md`,
      `v1-legacy/t3a-slice-manager/README.md`, `v1-legacy/test/README.md` still
      describe transient slices and `io_bfq_weight`. The `pr/` set in particular
      tells the upstream-PR story in retired terms and needs its own pass before
      any PR is filed.
- [ ] Pre-existing broken relative links: `SETUP.md` links `CGROUP-SEMANTICS.md`
      and `STRATEGY.md` links `SETUP.md` / `patchstack/…` without the
      `v1-legacy/` prefix — stale since the `v1-legacy/` move.
