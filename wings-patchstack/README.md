# wings-patchstack — the v2 Wings series

Four independent patch series over two upstream bases, exported as
`git format-patch` files. The design lives in
[`../wings-cgroups/shared-ramdisk-update-lifecycle-5-fable.md`](../wings-cgroups/shared-ramdisk-update-lifecycle-5-fable.md)
(lifecycle + manager, the master plan) and
[`../wings-cgroups/shared-ramdisk-update-lifecycle-cgroups-2-fable.md`](../wings-cgroups/shared-ramdisk-update-lifecycle-cgroups-2-fable.md)
(resources). [`series.yaml`](series.yaml) is the normative metadata.

The v1 `cgroup` series is **not** here — it is frozen under
`../wings-cgroups/v1-legacy/patchstack/` on the legacy layout, and production
is moving off it.

## State

| Series | Patches | Status |
|---|---|---|
| `filesystem` | F1 | **exported for pterodactyl-v1.13.1**; pelican export pending; `gofmt`/`build`/`test` green — see below; oracles 16–18 and pelican still open |
| `lifecycle` | L1, L1b, L2, L3 | not started; **L2 (readiness) retired** — see below |
| `resources` | R1–R8 | **superseded** — replaced by a single placement patch, see below |
| `integration` | I1 | not started (combined branch only) |

## Why `lifecycle`, `resources` and `integration` are still "not started"

Not an oversight, and not blocking anything today. The master plan orders
its own delivery in phases (§Kickoff plan): v1 ships on `host-bind` exposure
with **F1 as the only Wings patch it needs** — `srdm`'s v1 packages (P01–P08b,
`shared-ramdisk-depot-manager/nyxloom-trove/roadmap.md`) are what v1 is, and
they are complete. `lifecycle` (L1/L1b/L2/L3) needs the provider **socket**
D-025 confirmed does not exist in v1 at all — L1 is a start-attempt
transaction keyed on a protocol request id, validated leases and a
`docker.lifecycle_providers` socket config that has nothing to resolve
against until v2. `resources`'s R-series is the companion cgroups redesign
for that same provider-driven shape. `integration`'s I1 exists only to bind
the two together on the combined branch.

Building any of the three now would mean writing Wings patches against a
protocol nothing on the srdm side speaks yet, and gating them would need
fixtures for messages srdm cannot send — exactly the "no provider-less
interim, no 0011 bridge" decision the wings-cgroups project memory records.
**v2 (master-plan Phases 4–7) starts after v1 proves itself in production**
(`srdm`'s P09: Soulmask profile, managed egg, migration rehearsal) — "the
cutover is a config flip, not a migration," per the roadmap, precisely
because nothing built for v1 gets thrown away.

## Two of these series shrank or died — 2026-08-04

The authoritative plan is now `shared-ramdisk-depot-manager/nyxloom-trove/PLAN.md`
(the `wings-cgroups/` documents linked above are superseded history). Two of
its decisions land directly on this stack:

- **`resources` R1–R8 is superseded.** Placement and properties are
  independent axes and **only placement needs Wings code**: setting
  `memory.min`/`cpu.weight`/`io.weight` on a slice is host-side, and srdm
  already does it for generation slices. So the eight-patch in-Wings resource
  engine is replaced by **one** patch exposing `HostConfig.CgroupParent`.
  PLAN.md §Direction 3; carved as srdm's P13.
- **`lifecycle` L2 (readiness events) is retired.** srdm reads readiness from
  the console log — the same mechanism Wings itself uses, since the egg's
  `config.startup.done` is exactly such a match. A structured signal from
  Wings would be *derived from* a log match, so emitting one buys nothing.
  PLAN.md §Direction 2.

Net: the v2 Wings contract is **one small patch**, not nine. That is a much
easier upstream ask, and it means the design is far less hostage to review
than when this stack was drawn. L1/L1b and `integration` I1 remain as
written, still gated behind the provider socket, still unscheduled.

## F1 — the only patch on the MVP path

`srdm`'s v1 exposure driver (`host-bind`, `access: ro`) binds read-only
generations under a server's volume path. Vanilla Wings' pre-boot chown walk
calls `Lchownat` on every entry unconditionally, and a chown against a
read-only mount is `EROFS` even when the ownership already matches — so
without F1 a server with a read-only bind in its volume cannot start.

The zero-patch alternative is `system.check_permissions_on_boot: false` in
the node config, which is coarser: it also gives up Wings' ownership
self-repair after manual or SFTP edits.

**The skip predicate is deliberately narrower than "uid and gid match."** A
root chown with *identical* ids still strips setuid, setgid and file
capabilities from non-directories — measured, not assumed:

```
$ chmod 4755 f && chown 1000:1000 f   # f already owned 1000:1000
4755 -> 755
$ setcap cap_net_raw+ep c && chown 1000:1000 c
cap_net_raw=ep -> (none)
$ chmod 2755 d && chown 1000:1000 d   # directory
2755 -> 2755   (unchanged — the kernel only clears these for non-directories)
```

So the walk still chowns unconditionally whenever the entry is a
non-directory carrying setuid or setgid, preserving that hardening. File
capabilities are *not* probed: setting one needs `CAP_SETFCAP`, which no path
Wings offers a server can obtain. Measured on the case-study node: zero
setuid/setgid files and zero fscaps across the shared tmpfs and every server
volume, so the strict branch never fires there.

## Reproducing the F1 export

```bash
CLONE=../wings-cgroups/v1-legacy/build/wings-pterodactyl
git -C "$CLONE" worktree add -b filesystem/v1.13.1 /tmp/f1-ptero v1.13.1
# edit server/filesystem/filesystem.go, commit
git -C /tmp/f1-ptero format-patch -1 --no-numbered --zero-commit --no-signature \
    -o patches/filesystem/pterodactyl-v1.13.1
```

The branch `filesystem/v1.13.1` lives in that clone (untracked by vbpub); the
exported `.patch` here is the durable artifact.

## Gate — `gofmt`/`build`/`test` green 2026-08-04; oracles 16–18 still open

Per repo doctrine the devcontainer is a cockpit, never the gate.
`$CGROUP_PARENT_DEV_BACKGROUND` was blocked 2026-08-03 (unset in the
devcontainer, absent from PID 1's environment) and is now exported by hand —
see [[vbpub-cgroup-parent-env-gap]] / `srdm/nyxloom-trove/GUIDE.md`. With it
set:

```bash
docker run --rm --cgroup-parent="$CGROUP_PARENT_DEV_BACKGROUND" \
  -v /tmp/f1-ptero:/src -w /src \
  -e GOFLAGS=-mod=mod -e GOCACHE=/tmp/gocache -e GOMODCACHE=/tmp/gomod \
  golang:1.24 bash -c 'gofmt -l server/filesystem/ && go build ./... && \
                       go test ./server/filesystem/...'
```

`gofmt` reports nothing, `go build ./...` succeeds, and every test under
`server/filesystem/` passes (`TestFilesystem_Delete`, `_Path`,
`_Blocks_Symlinks`, and the `goblin`-driven `NewFs`-based suite).

**`go vet ./server/filesystem/` is NOT clean, and that is vanilla Wings, not
F1.** It reports three `unreachable code` findings in
`server/filesystem/filesystem_test.go`'s `NewFs()` helper — `panic(err);
return nil, nil` three times over, at the lines Go itself will not let a
`panic` fall through from. The F1 patch touches only `filesystem.go` (one
file, verified against `git show --stat` on the exported patch); nothing in
it or near it accounts for this. Measured on an unpatched checkout of the
same commit before drawing that conclusion, not assumed. Left as upstream's
own finding rather than patched around — F1 is a chown-walk skip, not a
vendor cleanup, and silently "fixing" code the patch does not own would make
the diff larger than what it claims to do.

**Still open, and still what "not gated" means**: pelican's export, and
oracles 16–18 of the master plan — the privileged EROFS/chown-walk behavior
against a real read-only mount, which needs the same privileged harness
`srdm`'s P02 built, pointed at this patched Wings rather than at `srdm`
itself. No privileged harness exists in `wings-patchstack` yet; wiring one
is real work, not a measurement away.
