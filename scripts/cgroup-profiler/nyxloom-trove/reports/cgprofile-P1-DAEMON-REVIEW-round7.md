# RG-55 candidate review — 2026-10-07

Candidate: `2ecb3b9a2a941c0827fbed2f981424013c8bfb12`
Base: `4670f53a67038a8a19b27ffe33e8308ec6f93fde`
Disposition: **CONDITIONAL**

Review route selected by the controller: `gpt-6-sol`, xhigh; Codex home
`$HOME/.codex`, shared database `$HOME/.codex/sqlite-shared`.

This was a read-only review of the candidate diff and relevant source, tests,
and documents. I ran no tests, gates, mutation campaign, container, or host
acceptance command. The checkout was clean at the recorded HEAD before and
after inspection.

## Findings

1. **Major — release-blocking documentation contradiction.**
   `scripts/cgroup-profiler/README.md:218` said “PID and cgroup namespaces
   remain private” in the daemon feature section.
   `run-gate-project/nyxloom-trove/DESIGN-2026-09-12-liveness-placement-admission.md:643-646`
   called the current no-broker daemon private in PID/cgroup/network, and
   line 688 described “namespace-safe PID moves” as though private PID were
   the reason for the bridge. These present-tense claims contradicted the
   daemon's host-PID Compose setting, the RG-55 contract mirrors, and the
   updated README security paragraph. **Remedy:** correct the README and
   design record's current-deployment description and bridge rationale;
   preserve historical decisions as history; make the documentation oracle
   catch both directions of this contradiction. This is a document blocker,
   not evidence of a runtime code defect.

2. **Minor — stale deployment/source comments.**
   `scripts/cgroup-profiler/ciu.defaults.toml.j2:5-7` called `serve` “PID 1
   inside” while also specifying host PID mode. In that mode the container
   process has a host PID, not a private-namespace PID 1.
   `scripts/cgroup-profiler/Dockerfile:43-46` said the daemon “never joins a
   host namespace”; `lib/placement.py:9` called it a “private-namespace
   daemon”; `lib/access.py:6-10` said a direct process's own identity always
   comes from a private `/proc`. **Remedy:** distinguish current daemon
   deployment from the private helper in these comments.

3. **Low — startup check proves view agreement, not independent host-PID
   identity.** `lib/access.py:119-130` accepts host mode when the configured
   proc view's PID 1 namespace inode equals local `/proc/1`, and
   `cgprofile.py:1031-1044` requires a caller-supplied mode string. Two
   matching private proc views with that string also satisfy the predicate.
   The shipped Compose template supplies `pid: "host"` and the host `/proc`
   bind, so this does not invalidate the managed deployment; it limits what
   the runtime check itself certifies. **Remedy:** describe the check as
   agreement with the authored deployment, or add an independent host
   identity anchor if `serve` is meant to certify the actual host namespace
   outside the managed template. Do not treat the environment variable alone
   as a security proof.

## Code/design assessment

- `ciu.compose.yml.j2:57-69,110-136` is an explicit daemon-only host-PID
  deployment with private cgroup namespace, no network, no Docker socket, a
  read-only host proc bind, and the existing guarded writable host
  cgroup/system-bus/DAMON mounts. The one-shot helper's argv in
  `lib/access.py:666-704` still selects private cgroup and network modes and
  does not request host PID.
- `cmd_serve` requires the explicit daemon mode and the matching proc-view
  namespace. `targets._cgroup_namespace_root` derives the private cgroup
  namespace offset from the daemon's visible PID and its actual host-root
  cgroup membership. Host-PID mode makes that PID readable from the host-root
  `cgroup.procs` view; unresolved mappings refuse.
- Placement's existing start-time and cgroup membership checks, write-ahead
  journal, systemd unit verification and `AttachProcessesToUnit` readback
  remain in place. Host PID access does not add an `os.kill` or numeric PID
  enforcement path; `serve.py:1733-1795` and `placement.py:1607-1633` still
  require an exact cgroup kill boundary.
- Run-Gate RG-88's `run-gate.py:3193-3195` guards both the `damon` object and
  its nullable `hot_bytes` field before reading `p90`. The regression test
  covers `hot_bytes: null` and asserts the rest of the footprint line
  survives. No code blocker was found in this formatter adjustment.

## Evidence still required

- Supplied R0/R1: 2,381 tests and 100% changed-line/branch coverage; supplied
  R3: seven canaries rejected; supplied Run-Gate selftest: 1,700 passed, two
  skipped. The reviewer did not rerun these.
- **R2 is unresolved.** Assay 8.0.0 refused before executing candidates
  because its cgroup-v2 event-counter validation could not see parent
  cgroups from the private cgroup namespace. This is neither a mutation pass
  nor a product-code failure established by mutants. Resolve the gate's
  visibility/preflight issue or document an authorized disposition; do not
  weaken the daemon's private cgroup namespace to make the gate green.
- **Live candidate acceptance is absent.** The prior host-PID/host-cgroup
  probe's DAMON `EINVAL` does not establish host-PID/private-cgroup behavior.
  After managed deployment of the candidate, require a real host-PID DAMON
  context start/stop and the existing socket and placement/restore flows. If
  `EINVAL` persists, report the exact kernel/configuration refusal and retain
  verdict-neutral `unavailable` behavior rather than claiming operational
  DAMON success.

There are no demonstrated code blockers in the reviewed diff. Final
acceptance is conditional on correcting the current-behavior documents and
resolving the two acceptance evidence gaps.
