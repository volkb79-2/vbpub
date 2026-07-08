# Review: Wings cgroup-parent proposal

Reviewed document: `wings-cgroup-parent-proposal.md`

Date: 2026-07-08

Scope:

- Proposal review for correctness, risk, and upstreamability.
- Effort/component estimates for the implementation paths described in the proposal.
- Specific assessment of using `game_stuff/soulmask/egg-soulmask-rcon.json` to carry cgroup definitions that Wings applies.
- No changes were made to the reviewed proposal.

## Executive Recommendation

Pursue the staged path, but tighten it:

1. Implement **v1 global `docker.cgroup_parent`** first.
2. Add **v2 `WINGS_CGROUP_PARENT`** only if it is used by both runtime and installer containers and is constrained to an operator-owned namespace or allowlist.
3. Use the Soulmask egg only as a **transport for admin-only variables**, not as an independent cgroup schema. Raw top-level egg JSON fields will not reach Wings without panel changes.
4. Defer **v2.5 D-Bus-managed transient slices** until v1/v2 are deployed and measured.
5. Treat **v3 panel-native schema** as the proper upstream end state, not as the first implementation.

For the current single-node Soulmask host, the most pragmatic path is:

1. Patch Wings with v1.
2. Install a real `soulmask.slice` or `wings.slice` unit with the resource properties.
3. Set `docker.cgroup_parent` to that slice.
4. Recreate the affected game container.
5. Keep the existing watcher only for residual properties not yet represented on the slice or not exposed cleanly through systemd.

## Findings

### F1. v2 per-server override does not cover installer containers

Severity: High for consistency, Medium for immediate production risk.

The proposal says installer and runtime containers draw from the same environment source, and the staged table presents v2 as "per-server placement." However, the v2 compiled patch shown in section 3 modifies only `environment/docker/container.go`. The installer path in `server/install.go` only receives the v1 node-wide `cfg.Docker.CgroupParent` behavior.

Relevant proposal locations:

- `wings-cgroup-parent-proposal.md:314` starts the v2 section.
- `wings-cgroup-parent-proposal.md:346` says the v2 variable is the recommended per-server option.
- `wings-cgroup-parent-proposal.md:350` shows a patch only for `environment/docker/container.go`.
- `wings-cgroup-parent-proposal.md:473` notes that install-time and runtime containers draw from the same source.
- `wings-cgroup-parent-proposal.md:588` describes v2 as per-server placement.

Impact:

- Runtime containers can land in the per-server slice while installer containers still land in the node-wide slice.
- Reinstall/update workloads may have different memory and I/O behavior than the server they install.
- Reviewers can reasonably reject the "same placement for installers" claim.

Recommendation:

- Factor cgroup-parent resolution into a small helper shared by runtime create and installer create.
- Apply `WINGS_CGROUP_PARENT` to the installer container too, or explicitly document that v2 affects runtime containers only.
- Add tests or at least table-driven validation for empty, default, valid override, and invalid override paths.

### F2. v2 needs a namespace or allowlist, not only `.slice` suffix validation

Severity: High for upstream multi-tenant use.

The proposed validation checks only empty value, surrounding whitespace/control characters, and `.slice` suffix. For a node-local admin setting that is acceptable. For a per-server value coming from the panel environment payload, it is too broad.

Relevant proposal locations:

- `wings-cgroup-parent-proposal.md:248` implements `ValidateCgroupParentValue`.
- `wings-cgroup-parent-proposal.md:408` documents that a tenant editing the value could escape a capped tier.
- `wings-cgroup-parent-proposal.md:579` introduces a `wings.slice` / `wings-*.slice` namespace guard, but only for v2.5/v3.

Impact:

- A compromised or misconfigured panel value can place a server under arbitrary host slices such as `system.slice`, `dev-workloads.slice`, or an unconstrained custom slice.
- Even if tenants cannot edit the variable through the panel UI, Wings still trusts the panel payload completely.
- This weakens upstream acceptability because the feature crosses tenant isolation boundaries.

Recommendation:

- For v1: allow any valid `.slice`, because it is node-owner config.
- For v2: require one of:
  - `docker.allowed_cgroup_parents` explicit allowlist.
  - `docker.cgroup_parent` as required root plus child validation under that root.
  - hard namespace such as `wings.slice` and `wings-*.slice`.
- Keep invalid override behavior fail-closed for the override. Falling back to the node default is reasonable, but the log should include server UUID and attempted value.

### F3. Missing-slice behavior is a deployment footgun

Severity: Medium.

The proposal correctly notes that a named slice may become a transient slice without intended resource limits if no real unit exists. That is the main operational failure mode for v1/v2. The proposal currently treats this mostly as documentation.

Relevant proposal locations:

- `wings-cgroup-parent-proposal.md:218` documents the missing-unit transient-slice behavior.
- `wings-cgroup-parent-proposal.md:306` says live cgroupfs validation is deliberately out of scope.
- `wings-cgroup-parent-proposal.md:821` instructs installing the slice unit first.

Impact:

- The container appears to move into the right cgroup path, but the intended `MemoryMin`, `MemoryLow`, `MemoryHigh`, `CPUWeight`, and `IOWeight` may not exist.
- This can produce a false positive rollout: paths look correct while guarantees remain absent.

Recommendation:

- For local deployment: make the runbook require `systemctl show <slice> -p FragmentPath -p MemoryMin -p MemoryLow -p MemoryHigh` before changing Wings config.
- For upstream v1: avoid a hard systemd dependency, but consider a warning when Docker reports the systemd cgroup driver and the target slice path has no configured properties visible after a smoke-created container.
- For tests: include a manual smoke test that creates a short-lived container with `--cgroup-parent=<slice>` and verifies both cgroup path and effective resource files.

### F4. v2.5 lifecycle needs stronger reconciliation semantics

Severity: Medium.

The v2.5 design says transient slices are reboot-proof in practice because Wings recreates them at boot before starting containers. That is mostly true for containers created after Wings starts. It needs more detail for already-existing containers, Docker restart behavior, and any live-restore scenario.

Relevant proposal locations:

- `wings-cgroup-parent-proposal.md:507` says capability is complete.
- `wings-cgroup-parent-proposal.md:511` says slices are recreated at boot before starting any server.
- `wings-cgroup-parent-proposal.md:553` says transient units do not survive reboot but Wings covers the recreate window.
- `wings-cgroup-parent-proposal.md:155` separately notes existing containers are not moved.

Impact:

- Existing containers are not moved by v1/v2, and v2.5 should not imply otherwise.
- If a container exists and `Create()` returns early, Wings must still ensure the slice exists and properties are current, or explicitly require container recreation.
- Docker live-restore or host restart edge cases need clear behavior.

Recommendation:

- Add a reconciler step independent of container create:
  - on Wings boot after panel sync;
  - before server start;
  - after server sync/update;
  - optionally periodic, if D-Bus access is enabled.
- Keep "container recreation required for placement changes" explicit for every stage.
- Distinguish "slice properties can be reconciled" from "existing Docker scopes can be moved"; the latter should be treated as recreate-required.

### F5. Egg variables are viable transport, but not an access-control boundary

Severity: Medium.

The proposal correctly identifies egg variables as the no-panel-code transport. The risk is that the cgroup spec becomes part of the container environment. The proposal mentions this for v2.5, but the same point applies to `WINGS_CGROUP_PARENT`.

Relevant proposal locations:

- `wings-cgroup-parent-proposal.md:332` discusses `user_viewable` and `user_editable`.
- `wings-cgroup-parent-proposal.md:408` says the variable must be admin-only.
- `wings-cgroup-parent-proposal.md:525` notes the spec is visible inside the container environment.

Impact:

- `user_viewable=false` hides the variable in the panel UI/API for the tenant, but the running process can still read its environment.
- This is acceptable for resource placement metadata, but it must not carry secrets.
- If the variable reveals commercial tiering or node topology, treat that as an intentional disclosure.

Recommendation:

- Use environment variables only for non-secret resource metadata.
- Prefer small discrete variables or a compact JSON blob with no secrets.
- Keep cgroup values admin-only in the panel, but document that "admin-only" does not mean hidden from the process.

### F6. The proposal should avoid overselling "zero panel changes"

Severity: Low.

No panel code changes are needed for v2/v2.5 if egg variables are used. However, operationally the panel data must still change: the egg must be reimported/updated, variables must be added, and existing servers may need per-server variable overrides.

Relevant proposal locations:

- `wings-cgroup-parent-proposal.md:105` says zero panel changes.
- `wings-cgroup-parent-proposal.md:479` calls egg variables the viable no-panel-changes transport.
- `wings-cgroup-parent-proposal.md:484` describes an importable egg carrying the spec.

Recommendation:

- Phrase as "no panel code changes" rather than "zero panel changes."
- Include migration work for existing servers in the effort estimate.

## Implementation Options

Effort ranges assume an engineer familiar with Wings, Pterodactyl/Pelican panel data flow, systemd cgroup v2, and this host's existing Soulmask scripts. Upstream timelines are larger because they include review iteration, test expectations, documentation, and compatibility discussion.

| Option | Description | Main components | Local effort | Upstream effort | Assessment |
|---|---|---|---:|---:|---|
| A | Keep current watcher/scope mutation model | `setup-cgroups.sh`, `soulmask-cgroup-watcher.sh`, systemd `set-property`, instance env files | 0.5-1 day for maintenance | Not suitable | Useful fallback, but not the right strategic path. It keeps compensating for bad placement instead of fixing it. |
| B | v1 global `docker.cgroup_parent` | Wings config struct, validation, runtime `HostConfig`, installer `HostConfig`, systemd slice unit, deployment runbook | 1-2 days | 3-7 days | Best immediate path. Small, understandable, and directly fixes ancestor placement for the current host. |
| C | v1 + v2 `WINGS_CGROUP_PARENT` to pre-created slices | Option B plus shared resolver, installer support, allowlist/namespace, egg/server variable, per-server slice units | 2-4 days | 1-2 weeks | Best next step once v1 works. Good for multiple servers or per-tier placement without panel code changes. |
| D | Enhance Soulmask egg with cgroup variables, Wings applies only `CgroupParent` | Egg variable(s), Wings env parser, admin-only variable defaults/overrides, pre-created slices | 1-2 days after v2 | Part of Option C | Good if limited to placement metadata. Do not put UUID-specific defaults in a generic egg. |
| E | Enhance Soulmask egg with full cgroup spec, Wings creates transient slices via systemd D-Bus | Egg variables, parser, validation, budget accounting, D-Bus client, systemd socket mount, namespace guard, cleanup/reconcile loop | 1.5-3 weeks | 3-6 weeks | Powerful but not the first step. Security and lifecycle complexity are real. |
| F | Panel-native cgroup schema | Panel migrations/models, egg export/import, admin UI, API payload, Wings config structs, D-Bus slice manager, tests/docs | 6-10 weeks | major feature cycle | Correct long-term product design. Too large for immediate Soulmask production need. |
| G | Docker daemon `--cgroup-parent` / daemon config | Docker daemon config only | 0.5 day | N/A | Too broad. Affects all Docker containers, including unrelated dev/test workloads, and does not solve per-server placement. |
| H | Direct raw writes to Docker scope cgroup files | Wings or scripts writing `/sys/fs/cgroup/.../docker-*.scope/*` | 1-3 days | Not recommended | Do not pursue. It conflicts with systemd ownership and has already failed under daemon-reload. |

## Assessment of the Soulmask Egg JSON Idea

Referenced file: `../../game_stuff/soulmask/egg-soulmask-rcon.json`

Current state:

- It is a PTDL_v2 egg export.
- It has a `variables` array and no cgroup-related variables.
- It validates as JSON with `jq`.
- Existing variables are mostly user-visible gameplay/server settings.

What will work:

- Add admin-only variables under `variables`, for example:

```json
{
  "name": "Wings Cgroup Parent",
  "description": "Admin-only: systemd slice for Wings CgroupParent placement.",
  "env_variable": "WINGS_CGROUP_PARENT",
  "default_value": "",
  "user_viewable": false,
  "user_editable": false,
  "rules": "nullable|string|max:128",
  "field_type": "text"
}
```

- For a tier-wide Soulmask slice, a default like `soulmask.slice` can be reasonable.
- For per-server slices, keep the egg default empty and set the value as an admin per-server override. A static UUID-specific default in the generic egg would be wrong for the next server created from the same egg.

What will not work by itself:

- Adding top-level fields such as `"cgroups": {...}` to the egg export will not reach Wings through the current panel-to-Wings payload.
- Adding variables will not affect placement until Wings is patched to read them and set `HostConfig.CgroupParent`.
- Adding full resource definitions to the egg will not let Docker set `memory.min`; Wings must use systemd-owned slice properties for that.

Recommended egg strategy:

1. For v2, add only `WINGS_CGROUP_PARENT`.
2. Keep it admin-only and non-secret.
3. Use per-server overrides for unique slice names.
4. Do not add full memory/CPU/IO definitions until Wings has a D-Bus-backed slice manager and node-side budget validation.

## Recommended Path

### Immediate local implementation

Implement Option B first.

Components:

- Wings patch:
  - `DockerConfiguration.CgroupParent`.
  - startup validation.
  - runtime container `HostConfig.CgroupParent`.
  - installer container `HostConfig.CgroupParent`.
- Host systemd:
  - real `soulmask.slice` or `wings.slice` unit, not a transient empty slice.
  - explicit `MemoryMin`, `MemoryLow`, `MemoryHigh`, `CPUWeight`, `IOWeight` as needed.
- Deployment:
  - rebuild Wings image.
  - update `/etc/pterodactyl/config.yml`.
  - restart Wings.
  - recreate the Soulmask container.
  - verify `/proc/<pid>/cgroup` and cgroup files under `/sys/fs/cgroup`.

Expected effort: 1-2 local dev days including a throwaway-container smoke test and production runbook.

### Near-term improvement

Implement Option C after v1 is confirmed.

Required changes beyond the proposal:

- Shared cgroup-parent resolver used by both runtime and installer paths.
- Namespace/allowlist enforcement for per-server overrides.
- Logging with server UUID, configured default, override value, and final selected parent.
- Admin-only `WINGS_CGROUP_PARENT` variable in the Soulmask egg or per-server panel variable.

Expected effort: 2-4 local dev days; 1-2 weeks for an upstream-quality PR.

### Do not do first

Do not start with full cgroup definitions in `egg-soulmask-rcon.json` plus D-Bus slice creation.

Reason:

- It combines transport design, privilege expansion, lifecycle reconciliation, budget enforcement, and systemd version handling in one step.
- It delays the small fix that directly addresses the current placement problem.
- It gives reviewers more reasons to reject the initial patch.

Use it as a second-stage local experiment only after v1/v2 placement is working.

## If Upstream Acceptance Is Unlikely

If upstream PR acceptance is not expected, favor a patch shape that survives rebases
over a feature-complete local fork. The best local-maintenance architecture is:

**minimal Wings patch + standard egg variables + host-side reconciler.**

Concrete shape:

1. The egg carries admin-only, non-secret metadata:
   - `WINGS_CGROUP_PARENT=soulmask.slice`
   - optionally `WINGS_CGROUP_PROFILE=soulmask-prod`
   - optionally, later, `WINGS_CGROUP_SPEC_JSON=...`
2. Wings only reads the resolved environment metadata and sets Docker
   `HostConfig.CgroupParent`.
3. A host-side reconciler owns the rich systemd policy:
   - watches Docker/Wings containers;
   - reads env/profile metadata from the container or panel-derived config;
   - applies resource properties with `systemctl set-property` or systemd D-Bus;
   - enforces host-wide budgets;
   - handles zswap/systemd-version edge cases;
   - reconciles after daemon reloads, container restarts, and server syncs.

This is favored over "enhanced eggs directly applied by Wings" because it keeps
the Wings fork small and rebasing-friendly:

- no panel patch;
- no non-standard egg top-level schema;
- standard PTDL_v2 import/export continues to work;
- the Wings patch touches stable Docker container creation points only;
- full systemd/D-Bus complexity remains outside Wings;
- future upstream changes are less likely to conflict;
- the host reconciler can evolve faster than a Wings fork.

The boundary should be explicit:

- Egg variables are a **transport** for placement/profile metadata.
- Wings is responsible only for placing the Docker scope under the selected slice.
- The host reconciler is responsible for resource properties on slices/scopes.
- Panel-native schema remains the clean future product design if a real fork is
  accepted later.

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
panel code changes. Use `variables` for no-panel-code transport.

## Suggested Upstream Framing

Submit v1 as a minimal Docker `HostConfig.CgroupParent` exposure:

- It aligns with Docker's existing per-container `--cgroup-parent` behavior.
- It is opt-in and default-empty.
- It keeps systemd resource policy outside Wings.
- It helps cgroup v2 operators without forcing D-Bus/systemd dependencies into Wings.

Submit v2 either in the same PR or a follow-up PR, but only with:

- shared runtime/installer implementation;
- namespace or allowlist protection;
- documentation that egg variables are panel data, not tenant-secret storage.

Keep v2.5/v3 as an RFC, not as the initial PR.

## Sources Checked

- Local proposal and host scripts in this repository.
- `../../game_stuff/soulmask/egg-soulmask-rcon.json` and `egg-soulmask-rcon-ksm.json`.
- Current Pterodactyl Wings `develop` source for container creation, installer creation, and Docker config.
- Current Pelican Wings `main` source for the same paths.
- Docker documentation for `--cgroup-parent`: https://docs.docker.com/reference/cli/dockerd/
- Docker Go API `container.Resources`: https://pkg.go.dev/github.com/docker/docker/api/types/container
- systemd cgroup single-writer/interface guidance: https://systemd.io/CONTROL_GROUP_INTERFACE/
- systemd resource-control manpage: https://manpages.debian.org/testing/systemd/systemd.resource-control.5.en.html
- OpenAI prompt caching guide: https://developers.openai.com/api/docs/guides/prompt-caching
- OpenAI prompt caching announcement: https://openai.com/index/api-prompt-caching/
