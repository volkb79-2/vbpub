---
schema_version: 1
id: assay-P22-committed-object-snapshot-substrate
project: assay
title: "Committed-object snapshots preserve repository topology and bytes"
tier: implement-2
input_revision: "2f2167f5928e5deacd93f1e9565238aef8acfe32"
source: {kind: product-goal, ref: "nyxloom-trove/reports/assay-v2-post-series-review-sol-P15-P19.md"}
stack: none
depends_on: [assay-P21-verdict-v4-evidence-contract]
session: fresh
scope:
  touch: ["src/assay/git.py", "src/assay/isolation.py", "tests/test_isolation.py", "tests/fixtures/isolation/**", "README.md", "docs/DESIGN-GUIDE.md"]
  forbid: ["src/assay/schemas", "src/assay/verdict.py", "src/assay/verify.py", "src/assay/config.py", "src/assay/runner.py", "src/assay/mutation.py", "src/assay/canary.py", "src/assay/attestation.py", "src/assay/adapters"]
oracles:
  - id: O1
    observable: "A bounded private Git repository contains the exact reachable objects and checked-out bytes of the supplied full commit, with HEAD at that same OID and the project at its original repo-relative prefix"
    negative: "A project nested at apps/p reads ../shared in the source repository but the snapshot drops or relocates that tracked sibling"
    gate: tester-unified
  - id: O2
    observable: "Snapshot construction reads only committed Git objects through P20's sanitized boundary; no checkout, archive, hook, filter, replace ref, alternate object store, untracked byte, or consumer-controlled process participates"
    negative: "A clean/smudge filter, replace ref, hostile hook, ignored profile, or source-object-store write changes snapshot content or source state"
    gate: tester-unified
  - id: O3
    observable: "Regular/executable files and contained relative symlinks are reproduced exactly; absolute/escaping symlinks, gitlinks, unsupported modes, path collisions, and limits fail before exposing a runnable snapshot"
    negative: "An external symlink is dereferenced, a gitlink becomes an empty directory, or a limit+1 tree is partially returned"
    gate: tester-unified
  - id: O4
    observable: "A caller can derive a second snapshot from one exact byte replacement through private Git plumbing with neutral fixed identity; the source repository and first snapshot remain byte-identical"
    negative: "A transform invokes git commit in the consumer checkout, reprints the file, shares an inode, or consults ambient identity/config"
    gate: tester-unified
gates: [tester-unified]
escalate_if:
  - "exact required Git history cannot be copied privately within the fixed object/count/byte limits"
  - "a supported tracked mode cannot be materialized without a consumer hook or filter"
mutexes: []
---

# P22 — committed-object snapshot substrate

The claim to attack: **Assay can reconstruct a bounded, inert, byte-faithful
repository snapshot from one commit without consulting untracked state or
executing consumer-controlled Git behavior.**

## Dispatch contract

- Contract class: **2b — complex solution-bearing execution** (`implement-4`
  when deployed; frontmatter names today's live `implement-2` route).
- Required roles: **Sol xhigh carver/prober → Opus xhigh implementer → a fresh
  Opus xhigh independent reviewer session**.
- Readiness: **PROVISIONAL until P21 merges, then JIT-FREEZE REQUIRED.** Sol must
  land the compiling `isolation.py` skeleton and locked hostile object/symlink/
  limit fixtures, and witness their controlled failures before dispatch.
- Implementer freedom: internal streaming, packing, and private helper
  decomposition only. Object source, topology, modes, limits, refusals, and
  absence of hooks/filters/alternates are fixed.

## Worktree and branch

Work only in `/workspaces/vbpub/.worktrees/assay-P22-committed-object-snapshot-substrate`
on branch `feat/assay-P22-committed-object-snapshot-substrate`.

## Context to read first

1. Post-series review findings F02–F04/F13–F14 and decisions A-156/A-161.
   Reproduce the monorepo sibling, ignored stale profile, escaping symlink, and
   hostile ambient Git cases before editing.
2. P20's final sanitized Git process API. Use it exclusively; do not create a
   second Git subprocess boundary.
3. `src/assay/canary.py` and `src/assay/mutation.py` only to identify the current
   `copytree` callers and required return data. They are forbidden in this
   package; P23 integrates the substrate.
4. `docs/DESIGN-GUIDE.md` §§5, 9, 11, and 12 plus decisions A-119–A-120,
   A-145, A-149–A-151, A-154–A-161.
5. `/workspaces/vbpub/shared-ramdisk-depot-manager` only as read-only topology
   evidence. Do not run Go or edit that project here.

## Implementation packet (normative)

### Public owner and exact interface

`src/assay/isolation.py` owns the only snapshot abstraction. The JIT carve lands
this compiling shape (docstrings/errors omitted here, not signatures):

```python
@dataclass(frozen=True, kw_only=True)
class SnapshotLimits:
    max_objects: int = 100_000
    max_path_bytes: int = 4_096
    max_blob_bytes: int = 64 * 1024 * 1024
    max_total_object_bytes: int = 1024 * 1024 * 1024
    max_pack_bytes: int = 512 * 1024 * 1024

@dataclass(frozen=True, kw_only=True)
class SnapshotSpec:
    repo_top: Path
    commit: str                  # exactly 40 lowercase hex characters
    project_prefix: PurePosixPath
    limits: SnapshotLimits

@dataclass(frozen=True, kw_only=True)
class Snapshot:
    root: Path
    project_root: Path
    commit: str

@contextmanager
def materialize_snapshot(spec: SnapshotSpec) -> Iterator[Snapshot]: ...

def materialize_replacement(
    base: SnapshotSpec, *, path: PurePosixPath, expected: bytes, replacement: bytes
) -> ContextManager[Snapshot]: ...
```

`Snapshot.root/.git` is a private, self-contained repository. It has no
alternates path back to the source object store. `Snapshot.project_root` is
exactly `root / project_prefix`. A context yields nothing until construction and
validation are complete; any error removes its temporary directory.

### Required construction

1. Validate the supplied full OID and `project_prefix`; confirm the project tree
   exists at that prefix in the commit through P20's sanitized Git boundary.
2. Enumerate the reachable object closure for the commit without replace refs.
   Use batch object metadata to enforce object count, per-blob size, and total
   uncompressed size **before** accepting a pack. Refuse limit+1; never truncate.
3. Stream exactly that closure into a bounded pack, install it in a fresh private
   repository, and verify the requested commit resolves there without any
   alternate. Copy no refs/config/hooks from the consumer. Set detached HEAD to
   the original commit OID with a minimal carver-specified config.
4. Enumerate the commit tree with NUL-delimited names. Reject empty/absolute/
   dot/traversing paths, NUL, duplicates, file-directory prefix collisions,
   overlong names, gitlinks, and modes other than `100644`, `100755`, `120000`.
5. Write regular blob bytes without filters or hardlinks; preserve only the
   executable bit. Create a symlink only from a relative target whose lexical
   resolution stays under snapshot root; never dereference it.
6. Verify tree bytes/modes against the object inventory, verify `.git` has no
   alternates/hooks/foreign config, then yield. Untracked/ignored source files,
   including coverage, FIFOs, sockets and devices, are absent by construction.

`materialize_replacement` first proves the current blob bytes equal `expected`.
It creates a new blob/tree/commit using plumbing and a private temporary index,
with fixed neutral author/committer name/email/time and the base commit as parent.
It then calls the same materializer for the new OID. It never invokes `checkout`,
`commit`, filters, hooks, or a consumer command.

### Decision and attack matrix

| input/state | result | side effect allowed |
|---|---|---|
| valid nested repository and contained symlink | complete private snapshot at exact OID | private temp files only |
| ignored coverage/FIFO/socket/device in consumer | absent from snapshot | none in consumer |
| replace ref/filter/hook/local config | ignored by sanitized object reads | none |
| absolute/escaping symlink, gitlink, unsupported mode | typed snapshot refusal | no yielded snapshot |
| duplicate/prefix collision or any limit+1 | `SNAPSHOT_LIMIT_EXCEEDED` or typed invalid-tree refusal | clean partial temp only |
| replacement expected bytes mismatch | typed stale-target refusal | no object/working-tree mutation |
| valid replacement | new private child commit and independent snapshot | source and base snapshot unchanged |

### Prepared proof required before ACTIVE

The JIT carve commits fixtures for: nested `apps/p` plus tracked `shared/`;
ignored stale coverage and special files; contained/absolute/escaping symlinks;
executable/plain files; gitlink and crafted prefix-collision tree; limit-1/limit/
limit+1 objects; replace ref, filter and hook traps; non-ASCII/NUL-delimited
names; same-inode detection; and an exact replacement with before/after hashes.
Each fixture has an independently authored manifest of OID, path bytes, modes,
blob hashes, and expected refusal. The carver demonstrates at least one naive
`copytree`/`git archive` construction failing a locked negative.

### Traceability

| work | owner | oracle | controlled break |
|---|---|---|---|
| object closure/private repo | `isolation.py` | O1/O2 | add source alternates or honor replace ref |
| tree/path/mode materialization | `isolation.py` | O1/O3 | dereference symlink or accept gitlink |
| bounds/cleanup | `isolation.py` | O3 | truncate at max or yield partial tree |
| replacement commit | `isolation.py` | O4 | mutate source/shared inode or ambient identity |

## Work

1. Land the exact interface and typed errors prepared by the carver.
2. Implement bounded private object transfer and detached-HEAD repository
   construction through P20's sanitized Git API.
3. Implement exact tree materialization and fail-closed mode/path/symlink rules.
4. Implement exact-byte replacement as private Git plumbing over a new snapshot.
5. Add the prepared fixtures and behavior assertions. Add reviewer-independent
   combined attacks: nested project + hostile config + stale ignored artifact,
   and escaping symlink + limit boundary + non-ASCII path.
6. Run the real `tester-unified` gate and record controlled-break counts in the
   package LOG/report.

## Test constraints copied from AUTHORING.md §3b

- No wall-clock deadline or sleep decides a verdict; inject clocks and use real
  synchronization. Timeouts are hang failsafes only.
- No process-global state leaks between tests; restore environment/config and use
  fresh temporary repositories per test.
- No hollow assertion: assert manifest bytes, modes, OIDs, absence, cleanup, and
  source immutability—not merely “did not raise” or a private call count.
- No coverage-evasion pragmas or weakened assertions.
- Network, clock, Git repository, config, filesystem, and subprocess are explicit
  inputs; the gate is offline.

## Scope / forbid

This package builds a reusable snapshot substrate only. It does not alter lane
configuration, command execution, verdicts, R2/R3, adapters, or consumers; P23
owns those integrations.

## BLOCKED rule

If a named contract cannot be met as specified, or scope requires a forbidden
file, STOP — write `BLOCKED: <reason>` to the LOG, commit, and exit. Do not
improvise a workaround.
