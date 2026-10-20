# Fixes to soulmask egg

## install and start

```
Scenario       │ File exists? │ Writable?  │ Run steamcmd?
───────────────┼──────────────┼────────────┼──────────────── 
Fresh install  │ No           │ —          │ Yes (! -f true) 
Normal disk    │ Yes          │ Yes        │ Yes (-w true) 
Ramdisk active │ Yes          │ No (EROFS) │ No — skipped  
```


## Preloads in the game process (`LD_PRELOAD`)

`egg-soulmask-rcon-ksm-cgroups.json` starts the game binary (only the game, not steamcmd) with a
preload list built in its Startup Command:

| Library | Purpose | Switch | Default |
|---|---|---|---|
| `/home/container/ksm-optin.so` | opts the game into KSM (`ksm-optin.c`; why a preload: the flag does not survive `execve`) | always listed | on (missing file = harmless warning) |
| `/home/container/libeatmydata.so` | turns `fsync`/`fdatasync`/`msync`/`sync` into no-ops | panel variable `EATMYDATA` | `0` (off) |

Both are loaded through the SAME `LD_PRELOAD`. A list of preloads is separated by colons (or
spaces); the two libraries are independent (one is a constructor, the other interposes libc wrappers),
so they do not conflict, and the order does not matter. Wherever a `.so` is missing, `ld.so` prints
`cannot be preloaded ... ignored` and the game starts without that library (fail-open: with a missing
`libeatmydata.so`, fsync simply stays real).

### eatmydata (optional fsync removal)

**Why.** The game is compute-only; its ext4 writes are `WS.log` (now on tmpfs, SMT-01) and, with players
online, the SQLite files under `WS/Saved` (`world.db`, `account.db`). Their `fsync`s wait for a journal commit shared with
every other workload on the root filesystem, which stalled the server for up to 26 s under dev IO load
(measured 2026-10-20). Full mechanism and evidence:
[`HOST-IO-INTERFERENCE.md`](../../scripts/gstammtisch-guide/HOST-IO-INTERFERENCE.md). eatmydata removes
the game's own sync waits.

**What it does NOT fix.** Plain `write()` can still wait on `ext4_dirty_inode` → `wait_transaction_locked`.
Measured with the probe: `write()` max 3.6 s on kernel defaults, ~0.1–0.4 s with the bounded kernel
settings. The bounded kernel settings (`HOST-IO-INTERFERENCE.md` section 5) remain the main fix;
eatmydata only removes the sync waits.

**Decision record.** `eatmydata` was considered and rejected on 2026-08-28 on durability grounds
(`wings-cgroups/v1-legacy/README.md`, "Considered and rejected": it makes the game's `fsync` lie, so a crash
or power loss can lose the latest save/journal state entirely). **The operator decided on 2026-10-20 to offer
it as an opt-in, default off, admin-only, knowing that risk.** What changed is the evidence: `fsync` waits on
the shared host journal stalled the game for up to 26 s (measured 2026-10-20), and `WS.log`, the other
syncing writer, already moved to tmpfs (SMT-01), so the remaining syncs are the SQLite files. The old
rejection's reasoning is the risk statement below; it is not withdrawn, only accepted per server.

**Risk, stated plainly.** With `fsync` a no-op the SQLite databases are **not crash-consistent across a
host crash or power loss**: corruption is possible, not just the loss of the last minutes. A crash of the
game process alone is fine, the data is still in the page cache. A clean host `reboot` is fine too
(the kernel flushes the page cache at shutdown; inferred, not tested). Mitigations: the game's own
`-backup=` interval (egg variable `BACKUP_TIME`, default 960 s) and Wings backups, see
`scripts/gstammtisch-guide/SOULMASK-BACKUP.md`. These reduce logical loss only: the backups are written
without `fsync` too, so a host crash that tears the live file and a recent backup in the same window is not
covered. The variable is admin-only on purpose. Verify on first use (the bpftrace check below) that nothing
else reads an environment variable of this name.

#### The startup change

```diff
-LD_PRELOAD=/home/container/ksm-optin.so /home/container/WS/Binaries/Linux/WSServer-Linux-Shipping {{MAP_TYPE}} ...
+LD_PRELOAD=/home/container/ksm-optin.so$( [ "{{EATMYDATA}}" = "1" ] && printf %s :/home/container/libeatmydata.so ) /home/container/WS/Binaries/Linux/WSServer-Linux-Shipping {{MAP_TYPE}} ...
```

With `EATMYDATA=0` (or unset or empty) this renders to
`LD_PRELOAD=/home/container/ksm-optin.so /home/container/WS/Binaries/...`, identical to before. With
`EATMYDATA=1`: `LD_PRELOAD=/home/container/ksm-optin.so:/home/container/libeatmydata.so /home/container/WS/Binaries/...`.
The `{{EATMYDATA}}` substitution and the `$( ... )` use the same mechanism as the existing
`-serverid=` fragments. `tests/test_soulmask_egg_startup.py` (gstammtisch-guide, run by its `r0-r1`
lane; `game_stuff/` has no gate of its own) renders the startup through the image's entrypoint steps
for `EATMYDATA` 0, 1 and unset.

#### Setup steps (operator)

1. **Find the image's Debian release.** The egg's image `ghcr.io/ptero-eggs/steamcmd:debian` is built
   from `steamcmd/debian/Dockerfile` in github.com/Ptero-Eggs/yolks, which starts `FROM debian:bookworm-slim`
   (read 2026-10-20; Debian 12, glibc 2.36, amd64). That Dockerfile can change, so check the image you
   actually run, with the command below.

   Placement first: any container you start on this host must be placed in a cgroup tier (never left at
   Docker's unconfined default). The value comes from the devcontainer's environment (`CGROUP_PARENT_DEV_BACKGROUND`, set to
   `dev-background.slice` by its `devcontainer.json`). A bare host shell has no such variable and
   `/etc/mdt/host-setup.env` does not define one (it holds the `DEV_BACKGROUND_*` settings of that slice): there,
   export the slice name the host defines first (check it exists:
   `systemctl show dev-background.slice -p LoadState`). If your estate has a host-side source for this name,
   use that instead of typing it:
   ```bash
   export CGROUP_PARENT_DEV_BACKGROUND=dev-background.slice   # only on a bare host; skip inside the devcontainer
   docker run --rm --name "eatmydata-probe-$$" --cgroup-parent="${CGROUP_PARENT_DEV_BACKGROUND:?set CGROUP_PARENT_DEV_BACKGROUND (devcontainer env, or the dev-background slice name)}" \
     --entrypoint sh ghcr.io/ptero-eggs/steamcmd:debian -c '. /etc/os-release; echo $PRETTY_NAME; ldd --version | head -1'
   ```
2. **Get a `libeatmydata.so` for that release.** Take the library from that release's own package, so it
   matches the container's glibc (Debian bookworm package `libeatmydata1`, file
   `/usr/lib/x86_64-linux-gnu/libeatmydata.so.1.3.0`). Extract it inside the same image into a scratch
   directory (not the live volume):
   ```bash
   mkdir -p /root/eatmydata-out
   docker run --rm --name "eatmydata-extract-$$" --cgroup-parent="${CGROUP_PARENT_DEV_BACKGROUND:?set CGROUP_PARENT_DEV_BACKGROUND (devcontainer env, or the dev-background slice name)}" \
     -u 0 --entrypoint bash -v /root/eatmydata-out:/out ghcr.io/ptero-eggs/steamcmd:debian -c \
     'apt-get update -qq && cd /tmp && apt-get download libeatmydata1 && dpkg-deb -x libeatmydata1_*.deb x \
      && cp -L x/usr/lib/x86_64-linux-gnu/libeatmydata.so.1 /out/libeatmydata.so'
   ```
   The container is throwaway (`--rm`, unique `--name`) and placed by the `--cgroup-parent` shown (the
   `${VAR:?}` makes the command refuse to run if it is empty). Do not use a
   `libeatmydata` from the host's own (Debian 13) package for a bookworm image: a newer-glibc build can
   require newer symbols. *Alternative: build from source* the way `ksm-optin.c` is built
   (`gcc -shared -fPIC`): upstream is autotools (`autoreconf -fi && ./configure && make` in the image
   with `autoconf automake libtool` installed); not verified here, the package route is simpler.
   (The extraction commands above were not run on a host by the author of this document.)
3. **Put it in each instance volume** (the volume root, owner = the container user 988:988):
   ```bash
   install -o 988 -g 988 -m 0755 /root/eatmydata-out/libeatmydata.so /var/lib/pterodactyl/volumes/<uuid>/libeatmydata.so
   ```
4. **Update the egg** in the panel (Nests, the egg, Import, or paste the JSON). The new `EATMYDATA`
   variable (default `0`) appears. A server keeps its OWN copy of the Startup Command, so for each
   server also open Admin, the server, Startup, and set the Startup Command to the new egg text.
   Then set `EATMYDATA` to `1` for the servers that should have it.
5. **Make the container pick it up.** Do a Wings start so the container is recreated; a plain
   `docker restart` reuses the frozen environment. See
   [SOULMASK.md, "Forcing a container to pick up a Startup Command change"](../../scripts/gstammtisch-guide/SOULMASK.md#forcing-a-container-to-pick-up-a-startup-command-change-without-the-panel).
   Do it in a quiet window (stop, wait for the save, start).

#### Verify

```bash
PID=$(docker top <uuid> | awk '/WSServer-Linux-Shipping/{print $2; exit}')
grep -c libeatmydata /proc/$PID/maps       # > 0 : the library is mapped into the game
grep -c ksm-optin    /proc/$PID/maps       # > 0 : KSM shim still there
# count the game's sync syscalls for 60 s: with eatmydata working this prints 0
bpftrace -e 'tracepoint:syscalls:sys_enter_fsync,tracepoint:syscalls:sys_enter_fdatasync,tracepoint:syscalls:sys_enter_msync,tracepoint:syscalls:sys_enter_sync_file_range /pid == '"$PID"'/ { @[probe] = count(); } interval:s:60 { exit(); }'
```

An empty map at exit means 0 syscalls. Compare with `EATMYDATA=0`, where `fdatasync` is seen (the
`WS.log` writer and SQLite). Read this as a real result only after the server has players online; an
idle server syncs rarely.

#### Disable

Set `EATMYDATA` to `0` in the panel and do a Wings start (container recreated). Deleting the `.so`
also works but leaves an `ld.so` warning in the console on every start.
