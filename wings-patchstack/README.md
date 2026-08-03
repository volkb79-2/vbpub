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
| `filesystem` | F1 | **exported for pterodactyl-v1.13.1**; pelican export pending; gate not yet run |
| `lifecycle` | L1, L1b, L2, L3 | not started (Phase 5–6) |
| `resources` | R1–R8 | not started (Phase 6) |
| `integration` | I1 | not started (combined branch only) |

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

## Gate — NOT YET RUN

Per repo doctrine the devcontainer is a cockpit, never the gate. F1 still
needs `build` / `vet` / `test` plus oracles 16–18 of the master plan:

```bash
docker run --rm --cgroup-parent="$CGROUP_PARENT_DEV_BACKGROUND" \
  -v /tmp/f1-ptero:/src -w /src \
  -e GOFLAGS=-mod=mod -e GOCACHE=/tmp/gocache -e GOMODCACHE=/tmp/gomod \
  golang:1.24 bash -c 'gofmt -l server/filesystem/ && go build ./... && \
                       go vet ./server/filesystem/ && go test ./server/filesystem/...'
```

**Blocked 2026-08-03**: `$CGROUP_PARENT_DEV_BACKGROUND` is unset in the
devcontainer and absent from PID 1's environment, and the host has only a
flat `dev-workloads.slice` — no interactive/background children. Per
`AGENTS.md` ("No hardcoded fallbacks … refuse to launch") the build was not
run rather than guessing a tier. Provision the tier, or pass an explicit
`--cgroup-parent`, then run the above.
