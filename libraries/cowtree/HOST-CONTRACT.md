# Shared tree cache: the host contract

What a consumer (cowtree `cache`, assay template, a gate runner) may assume about a host that offers a shared, read-only tree area. **Optional**: a
consumer that finds the contract unmet must fall back to a private copy. Nothing here names mdt-cockpit; any host can provide it. The mdt-cockpit
`host-setup` (`mdt-cockpit/host-setup/README.md`, "Shared tree cache of the dedicated-dev role") is one implementation, not a dependency.

## The contract

1. **Cache root**: an absolute directory path, a **tmpfs mount point** (consumers and GC refuse a plain directory, so nothing is ever deleted from a real
   disk). Handed to consumers as a path (an argument, an environment variable or a discovery document; the consumer is told the path, no name is assumed).
2. **Ownership and size**: `root:<group>`, mode `2770` (setgid), the consuming users in `<group>`. Size and `nr_inodes` bounded by the host (suggested: 25 % of RAM,
   one inode per 16 KiB; always set `nr_inodes`, an unbounded tmpfs inode table is a memory hazard). Mounted `nosuid,nodev`, **not** `noexec` (trees hold test
   binaries), not `noswap`, `huge=never`.
   **Trust**: mode `2770` has no sticky bit, so every group member can rename, replace or delete another member's keys and can alter a published tree (cache
   poisoning is possible). Group members are therefore fully trusted; `<group>` must be a narrow group of the gate runner(s), never a broad one (`users`, `docker`
   or similar), and lanes must not get a writable bind of the root (read-only only).
3. **Layout** (published by the consumer library, honoured by the host's GC):
   `<root>/<key>/{COMPLETE,tree/,.lease,.last-used}`; builds in `<root>/<key>.tmp-<id>` under an exclusive `flock` on `<root>/.locks/<key>`, then `rename`
   to `<key>` after `COMPLETE` is written. A lane touches `.last-used` **first**, then holds `LOCK_SH` (flock) on `<key>/.lease` for its whole life, and takes the
   lease **before** it mounts or binds anything of the key (the GC reads the mount table once per pass; the lease is what protects a later mount).
   **Re-validate after taking the lock**: the GC may have removed the key between the lessee's `open` and its `flock`, leaving a lock on an unlinked inode.
   After `LOCK_SH` the lessee must check `fstat(fd).st_nlink > 0` and that `<key>/COMPLETE` still resolves inside the same directory (same device/inode as before);
   otherwise it drops the lease and retries or rebuilds. A builder takes `LOCK_EX` on `.locks/<key>` **before** it creates `<key>.tmp-<id>`.
4. **GC** (host side, optional but expected): remove a key only when it is idle longer than the host's idle time, `LOCK_EX|LOCK_NB` on `.lease` succeeds,
   and no process's `/proc/*/mountinfo` holds it (a bind of `<key>/...`, or an overlay `lowerdir=`/`upperdir=`/`workdir=` naming it). Remove stale
   `<key>.tmp-*` (older than an hour) when `.locks/<key>` is free. Any doubt keeps the entry: an unreadable process entry that is not gone or a zombie, a `.lease` or
   lock that is not a regular file, or any error on one entry keeps that entry and the pass goes on. Without a GC the area only fills until the operator clears it;
   there is no pressure eviction, a full cache stays full until keys age out, and consumers then copy privately.
5. **Upper area** (optional, for per-worker overlays): a second tmpfs with the same flags; per-lane directories `<token>/w<k>/{u,w}`, created and removed by
   the placing tool. Absent means: no overlay slots, copy instead. The upper area has **no per-lane limit**: it is one shared tmpfs, so one lane that fills it
   gives ENOSPC to the writes of every other lane. A placing tool must classify ENOSPC in an upper as an infrastructure event (inconclusive), never as a product failure.
6. **Overlay capability** (optional): the container engine can create a `local` volume with `type=overlay,device=overlay,o=lowerdir=<root>/<key>/tree,upperdir=...,workdir=...,redirect_dir=on,xino=auto`
   (dockerd mounts it as root; no extra privilege for the lane).
7. **Charging cgroup**: the populate helper (the process that writes the tree) should run in a **persistent** cgroup/slice, not in a lane. When a cgroup
   that wrote or swapped pages dies, the charge moves to its parent, so a persistent owner keeps the shared pages off the lanes. Any existing slice works;
   the host tells the placing tool its name. Without one the helper runs in the lane and the first lane is charged.
8. **Discovery**: a host may describe the above in a JSON document with a `tree_cache` object (`root`, `slice`, `size_bytes`, `upper_root`, `upper_size_bytes`,
   `overlay_opts`, `group`, `mode`); where that document lives is host-specific. Consumers must also accept the values as plain arguments.
9. **Swap**: tmpfs pages are swappable. On a host without swap, the areas are unevictable RAM; size them accordingly (or do not offer them).

## A non-vbpub host in about ten lines

```ini
# /etc/systemd/system/srv-tree\x2dcache.mount   (name = systemd-escape -p --suffix=mount /srv/tree-cache)
[Mount]
What=tmpfs
Where=/srv/tree-cache
Type=tmpfs
Options=size=2G,nr_inodes=131072,mode=2770,gid=<runner gid; a narrow group, see the trust note>,nosuid,nodev,huge=never
[Install]
WantedBy=local-fs.target
```

```ini
# /etc/systemd/system/tree-cache.slice  (persistent owner of helper pages; optional MemoryLow=)
[Slice]
MemoryLow=512M
```

Then `systemctl enable --now 'srv-tree\x2dcache.mount'`, start the slice, and run the populate helper with `--cgroup-parent tree-cache.slice` (docker) or
`systemd-run --slice=tree-cache.slice`. GC: any periodic job implementing item 4 (one implementation is `mdt_tree_cache.py gc` in
`mdt-cockpit/host-setup`, readable as a specification; the host-setup README documents its configuration keys). A host with none of this simply offers no cache; consumers copy.
