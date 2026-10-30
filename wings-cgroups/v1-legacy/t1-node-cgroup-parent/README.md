# T1 — node-wide `docker.cgroup_parent` (patched Wings)

> Historical T1 note. The current series and build workflow live in
> [`wings-patchstack/`](../../../wings-patchstack/README.md).

The Wings code lives in the patch stack:
`../../../wings-patchstack/patches/pterodactyl-v1.13.3/0001-*.patch`. This
folder holds the host-side artifacts and explains *why the
mechanism works*. **The deployment procedure is in `../SETUP.md`** — one
runbook for the whole 0001–0006 series, including the pieces T1 alone does not
have (`per_server_slices`, the D-Bus socket mount, the current image tag).

T1 places **all** server + installer containers on a node under one named
slice. Works on shared nodes (unlike T0b). On a single-game-server node this
already delivers per-server floors — the node slice *is* the server slice.

Build the image with a ledger-derived suffix:

```bash
../../../wings-patchstack/scripts/build-image.sh pterodactyl
```

## The mechanism

`docker.cgroup_parent` in `/etc/pterodactyl/config.yml` (see
`config.yml.snippet`) is applied to `HostConfig.CgroupParent` on every
container Wings creates — runtime and installer alike. Docker's systemd cgroup
driver then asks systemd for a scope *under that slice*, so the game processes
inherit the slice's `MemoryMin`/`MemoryLow`/`MemoryHigh`/`CPUWeight`/`IOWeight`
(see `wings.slice`). This is the whole of T1: one config key, one unit file.

Two properties follow from that and drive everything else:

- **Placement is create-time only.** Changing the key moves nothing that is
  already running; each container has to be recreated to land in the new slice.
- **The floor is only real if the ancestor chain has one.** `memory.min` is
  arithmetically dead under an ancestor with `memory.min=0`, which is why the
  slice unit — not a property write on the scope — is the anchor.

## The footgun this tier is built around

A missing or typo'd unit **degrades silently**: systemd happily creates the
slice as a transient, limit-less unit on demand. `/proc/<pid>/cgroup` then
shows exactly the path you wanted, `docker inspect` agrees, and none of the
guarantees exist. There is no error anywhere in the chain.

So the pre-flight is mandatory, and it checks unit *identity*, not path:

```bash
systemctl show wings.slice -p FragmentPath -p MemoryMin -p MemoryLow -p MemoryHigh
# FragmentPath MUST point at your unit file — empty means transient, i.e. no
# guarantees. Values MUST match the plan.
../test/smoke-placement.sh wings.slice        # path + effective-file check
```

Install the slice unit **before** pointing Wings at it, for the same reason —
ordering it the other way is what produces a transient slice nobody notices:

```bash
cp wings.slice /etc/systemd/system/
systemctl daemon-reload && systemctl start wings.slice
```

Rollback is cheap and needs no second outage: revert the image and the config
key and recreate Wings. Stock Wings ignores the unknown YAML key, and
already-placed containers keep their placement until they are next recreated.

Background and the full ordering rationale:
`../../scripts/gstammtisch-guide/wings-cgroup-parent-proposal.md` (Appendix A).
