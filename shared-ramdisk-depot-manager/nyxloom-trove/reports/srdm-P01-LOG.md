# srdm-P01 — LOG

Package: project bootstrap + the release store
Handoff: `../../HANDOFF-P01-bootstrap-and-store.md`
Date: 2026-08-03

---

## The blocker, cleared first

The handoff's "known blocker to clear first" was
`$CGROUP_PARENT_DEV_BACKGROUND` being unset, with `AGENTS.md` forbidding a
fallback. Verified before touching anything:

- both `CGROUP_PARENT_DEV_BACKGROUND` and `CGROUP_PARENT_DEV_INTERACTIVE`
  unset in the devcontainer, and absent from PID 1's environment;
- this devcontainer actually runs under `interactive.slice`, and dstdns's
  test containers under `besteffort.slice` — the **pre-rollout** flat names,
  not mdt's `dev-interactive.slice` / `dev-background.slice`;
- mdt ships the intended units
  (`modern-debian-tools-python-debug/host-setup/units/dev*.slice.in`) and its
  `templates/devcontainer.json` sets both variables, so the rollout existed
  as code but not in this container's environment;
- the cockpit **cannot** verify a slice itself: `CgroupnsMode=private`,
  `Privileged=false`, no host cgroupfs bind, no systemd.

Also found while scoping it, and material: **there is no Go toolchain in the
devcontainer at all**, so P01 was fully container-blocked — build as well as
gate.

Raised with the operator rather than guessed. Answer: the mdt host-setup had
since been run and the tiers are live, the devcontainer cannot be rebuilt
mid-session, so set the variable by hand and reference the real slices.

Verified empirically before using it — `dev-background.slice` carries
`memory.max=16GiB`, `memory.high=8GiB`, `memory.swap.max=48G`,
`cpu.weight=20`, `io.weight=10`, beneath a `dev.slice` holding the measured
`io.max`. Those match `host-setup.env.example` exactly, so it is the
installed unit and not a fail-open transient slice.

That verification is now `tools/cgroup-parent.sh`, and it refuses rather than
guesses. Negative control run: `tools/cgroup-parent.sh nonexistent-tier.slice`
exits 1 and explains that the name would otherwise fail **open** into an
unlimited transient slice next to production.

## The gate — a deviation, authorized

The handoff names `tester-unified` as the gate. It cannot host srdm's
oracles: it is a Python 3.14 venv closure built from four projects'
pyprojects, with no Go toolchain, unprivileged as uid 1003, no systemd.

Put to the operator as a choice between adding Go to `tester-unified` and
building a container tailored to srdm. **Tailored chosen** — recorded as
**D-006** with the reasoning (scope: `tester-unified` is shared by four
unrelated Python projects; shape: srdm's P02+ oracles need privileged
systemd-in-Docker, which `tester-unified` structurally cannot be).

`gate/Dockerfile` ships `unit` and `e2e` targets. The run-uid gets a full
identity — passwd entry, group, writable `HOME`, XDG dirs — and Go's caches
live outside the bind-mounted worktree so a gate run never pollutes the tree
it is judging.

## What was built

| Package | What it is |
|---|---|
| `internal/config` | on-disk layout, ownership policy, name validation |
| `internal/profile` | classes, longest-prefix classification, probes, credential handling |
| `internal/store` | the transactional release store, manifests, recovery |
| `internal/journal` | durable per-operation records, events JSONL, journald native protocol |
| `internal/doctor` | the offline check subset |
| `internal/fsx` | atomic write + fsync primitives, tree copy |
| `cmd/srdm` | `store promote/activate/verify/list/recover`, `journal list/show`, `doctor`, `version` |
| stubs | `publish`, `expose`, `source/steam`, `providerapi`, `adminapi` — each carrying the findings its phase will need |

Zero third-party dependencies, so the gate is hermetic: no module downloads,
nothing vendored.

## Oracles

All five are implemented and **all five were proved able to fail**
(`tools/canary.sh`, eight canaries, each breaking exactly one contract):

| | Oracle | How it is asserted |
|---|---|---|
| O1 | kill at every phase | A **real subprocess, SIGKILLed at a real boundary** — not an injected error. The child announces it has reached the boundary on a pipe and blocks; the parent kills it only after reading that. Nine boundaries, each with its own expected outcome. No sleep, no deadline, nothing timing-dependent in the verdict. |
| O2 | `COMPLETE` is last and means it | A promoted release verifies; mutated, missing and *extra* files all make verification fail, naming the path. `COMPLETE` pins the manifest by hash, so a swapped manifest is caught. |
| O3 | unclassified path blocks promotion | The refusal is a typed `*profile.UnclassifiedError` naming both path and profile; the store is asserted unchanged afterwards (no new release, channel not moved) and the journal records a **refusal**, distinct from a fault. |
| O4 | manifest is per-file, content-addressed | Identical trees → identical manifests. An mtime-only change → identical manifest (not keyed on time). A **same-size** byte flip → exactly one entry changes, its hash, and the content digest (not keyed on size). |
| O5 | journal has no secrets | A real promotion, refusal and recovery run against a profile carrying credentials, then **every byte** of the journal tree is grepped. Plus negative controls, so an empty journal cannot pass vacuously. |

Two things O5 tests that a parsed-record check would miss: a credential
embedded in a provenance URL, and an unclassified **path whose name is the
credential** — the error message route.

## Findings carried into the code

Each of the handoff's verified findings is encoded where it will be enforced,
not just documented:

- **The setuid trap.** `store.needsChown` uses F1's narrow predicate: skip
  only when the ids already match *and* the entry is a directory or carries
  neither setuid nor setgid. A naive "skip when ids match" would silently
  preserve bits an unconditional walk strips. The kernel behaviour it rests
  on has its own test, which runs when the suite is root and says so
  otherwise. The fscaps limit (not visible via `FileInfo`) is stated in the
  doc comment rather than glossed.
- **The systemd hyphen.** `config.Validate` refuses a hyphenated slice name
  and explains the consequence — auto-created ancestors at `MemoryMin=0` make
  every class floor beneath arithmetically dead.
- **`memory_recursiveprot`.** A `doctor` check that fails closed; without it
  the floors are decoration.
- **A missing slice fails open.** Both `doctor`'s `parent-slice` check and
  `tools/cgroup-parent.sh` treat an unknown slice as fatal.
- **F-a, F-b, `rw` single-consumer, teardown-does-not-free.** Recorded in
  `internal/expose/doc.go` and `internal/publish/doc.go`, at the seam that
  will implement them, so P02/P03 meet them before writing code.

## Decisions filed

`D-001` `WS/Config` (open, from the handoff) · `D-002` retention (open, from
the handoff) · `D-003` srdm verifies rather than writes `srdm.slice`
(proposed, confirm before P02) · `D-004` `privileged-e2e` declared but empty
until P02 · `D-005` profiles are JSON in P01, YAML additive later ·
`D-006` srdm's own gate container · `D-007` no coverage floor, canaries
instead · `D-008` fsync ordering is asserted, durability is not observable
in a unit gate.

## Gaps, stated plainly

- **`privileged-e2e` has no cases.** Declared because the handoff requires
  it; the image target is real and buildable so the declaration is not
  vapour, and `nyxloom.toml` says in the gate's own comment that a green run
  is not evidence until P02. (D-004)
- **No changed-line coverage floor.** nyxloom's evaluator is Python-only.
  Declaring the assertion without enforcing it would be a declaration
  mismatch. The canary gate is the compensating control. (D-007)
- **Durability is not gated.** A process kill proves ordering; only power
  loss or a fault-injecting device distinguishes written from durable.
  Belongs with the privileged harness. (D-008)
- **`$CGROUP_PARENT_DEV_BACKGROUND` is still unset in this devcontainer.**
  It needs a rebuild the session could not do. Every srdm tool refuses
  without it and `nyxloom-trove/GUIDE.md` says how to export it. Fixing the
  devcontainer itself is out of this package's scope.

## Scope

Touched `shared-ramdisk-depot-manager/**` only. `wings-cgroups/**`,
`wings-patchstack/**`, `scripts/gstammtisch-guide/**` and every Wings clone
were read and never written.

The handoff stays at the project root rather than moving into
`nyxloom-trove/handoffs/`: it deliberately carries no frontmatter (there were
no `[gates.*]` ids to reference when it was written), so `nyxloom lint` would
reject it there. Packages from P02 on land in `handoffs/` with valid
frontmatter, as the handoff intended.

## Verification

```
tools/gate.sh          → build, vet, and all oracles green
tools/canary-run.sh    → 8 canaries rejected, 0 survived
```

Plus an end-to-end CLI run: promote → list → verify → refusal → store
unchanged → recover → journal → doctor.
