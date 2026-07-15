# Lifecycle-owner adapters

Status: **accepted product architecture**, D-016, 2026-07-15. This document
defines a future extension boundary; it does not claim that these adapters
exist today. `docs/STATUS.md` remains implementation authority.

## Principle: model an owner chain

A runtime object is not necessarily its own lifecycle authority. Examples:

- Docker container → Compose service → CIU stack;
- containerd/CRI-O container → Kubernetes Pod → ReplicaSet → Deployment →
  possible GitOps controller;
- Docker task container → Swarm service;
- Nomad driver process/container → allocation → task group → job;
- Podman container → generated systemd service → Quadlet source file.

Groop may observe every link, but an action targets the highest authoritative
owner it can prove **and** the operator has explicitly configured. Discovery
metadata never grants authorization. If ownership is ambiguous, externally
reconciled above the known chain, stale, or unsupported, Groop refuses the
mutation and explains the safe native tool/API to use.

Raw execution layers such as `runc`, `crun`, containerd and CRI-O are not
desired-state owners. Groop must not restart their runtime objects behind an
orchestrator's back.

## Adapter contract

All built-in lifecycle adapters expose the same bounded contract:

1. **Discover** an `OwnerKey`, owner chain, concrete incarnation IDs,
   provenance/confidence, endpoint/context and reconciliation hints.
2. **Capabilities** declare only supported operations, such as inspect,
   restart, resource-plan, redeploy, scale or rollback. Absence is a refusal,
   not a generic Docker fallback.
3. **Plan** is side-effect free and returns the logical target, current and
   desired revision, affected replicas/dependencies, rollout semantics, exact
   bounded argv or API operation, required authority, expected replacement
   identities, timeout and verification oracle. Secrets are never returned.
4. **Authorize and confirm** through Groop's existing root/admin/capability,
   typed-confirmation and protected-workload gates. Owner labels are inputs to
   discovery, not gates.
5. **Execute** only through an audited built-in adapter or separately
   permissioned helper. The root daemon does not import arbitrary third-party
   Python plugins.
6. **Verify** the owner-reported result and the observed replacement
   incarnation. Record partial outcomes and lifecycle facts in the shared
   history.

Runtime-only changes such as the existing narrow `docker update` path must say
whether they are ephemeral and liable to be overwritten by the owner. A
persistent change belongs in the owner's source configuration and adapter.

## Candidate owner families

Priority is based on Groop's single-Linux-host/cgroup-v2 fit and safety, not a
promise to implement every integration.

| Family | Correct lifecycle authority | Product posture |
|---|---|---|
| Native systemd services/scopes | systemd unit manager and persistent unit/drop-in source | Core owner family. Existing bounded systemd actions are the starting adapter. |
| Docker Compose | Compose project/service plus the exact merged Compose file/context | Core owner family. Never recreate a service container as standalone Docker. Compose files can merge, so labels alone do not recover the source invocation. |
| CIU | CIU stack/deploy plan above its Compose service | Project-specific core adapter candidate. CIU remains authoritative when it owns the deployment. |
| Pterodactyl/Wings | Panel/Wings API and server identity | Project-specific adapter. Never directly restart or recreate a Wings container. |
| Podman Quadlet | generated systemd unit plus `.container`/`.pod`/`.kube` Quadlet source | Best next general-purpose adapter: it is cgroup-v2/systemd-native and covers rootful and rootless ownership. Route lifecycle through the unit/source, not an inferred `podman run`. |
| Kubernetes distributions, including k3s | Kubernetes API workload controller, normally Deployment/StatefulSet/DaemonSet/Job rather than Pod/container | High-value read-only owner join; action support is later and opt-in because RBAC, multi-node scheduling, rollout semantics and GitOps reconciliation exceed a host-local Docker action. Never act through CRI/containerd. |
| Docker Swarm | Swarm service on a manager, not the task container on a worker | Straightforward owner semantics but scenario-driven priority. Service updates can replace tasks across nodes. Refuse actions from a worker-only context. |
| HashiCorp Nomad | Nomad job/task group through the server API, not the local allocation process | Read-only joins first; actions later through job plan/run/restart with Nomad ACLs and rollout semantics. |
| Incus/LXC | Incus instance/project through its Unix/HTTPS API | Later host/VM/container adapter if a named scenario demands it. Do not manage its instance processes directly. |
| systemd-nspawn/machined | machine unit and machine1 API/settings | Low-cost extension of the systemd family when encountered. |
| libvirt or Proxmox-managed VM/LXC guests | libvirt/Proxmox management API and guest identity | Later optional family. A QEMU/LXC process is observable as a cgroup/process but is not a safe lifecycle target by itself. |
| Higher-level panels and GitOps controllers | the panel/controller API or repository workflow above Compose/Kubernetes | Explicit adapter only. Detection should cause a refusal when Groop knows a lower-level action would be reconciled away. |

Relevant upstream ownership models:

- [Docker Compose application model](https://docs.docker.com/compose/intro/compose-application-model/)
- [Podman Quadlet systemd units](https://docs.podman.io/en/latest/markdown/podman-systemd.unit.5.html)
- [Kubernetes workload controllers](https://kubernetes.io/docs/concepts/workloads/controllers/)
- [Docker Swarm services](https://docs.docker.com/engine/swarm/services/)
- [Nomad desired-state jobs](https://developer.hashicorp.com/nomad/docs/job-declare)
- [Incus API](https://linuxcontainers.org/incus/docs/main/rest-api-spec/)
- [systemd-nspawn settings](https://www.freedesktop.org/software/systemd/man/systemd.nspawn.html)

## Recommended implementation order

1. Freeze and fixture-test the owner-chain/adapter protocol before adding a new
   mutation.
2. Finish systemd, Compose, CIU and Wings routing for workloads already present
   in the project's real scenarios.
3. Add Podman/Quadlet discovery and read-only ownership; it is the closest
   general-purpose match to the existing host model.
4. Add Kubernetes/k3s read-only joins if a real host requires them. Treat
   mutation as a separately reviewed security/cluster-semantics project.
5. Admit Swarm, Nomad, Incus, VM managers or panels only for named operator
   scenarios and after measurement/security gates.

This order keeps Groop useful as an observer for many runtimes without turning
its privileged daemon into a universal deployment engine.
