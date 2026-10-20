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
   **Measured 2026-10-20** (run from the devcontainer, where `CGROUP_PARENT_DEV_BACKGROUND` is set; these
   were this step's command and step 2's, verbatim apart from the output path): the probe printed
   `Debian GNU/Linux 12 (bookworm)` and `ldd (Debian GLIBC 2.36-9+deb12u14) 2.36`.
2. **Get a `libeatmydata.so` for that release.** Take the library from that release's own package, so it
   matches the container's glibc (Debian bookworm package `libeatmydata1`, file
   `/usr/lib/x86_64-linux-gnu/libeatmydata.so.1.3.0`). Extract it inside the same image into a scratch
   directory (not the live volume):
   ```bash
   OUT=<a scratch dir>          # where YOU read the result (see "Host path" below)
   mkdir -p "$OUT"
   docker run --rm --name "eatmydata-extract-$$" --cgroup-parent="${CGROUP_PARENT_DEV_BACKGROUND:?set CGROUP_PARENT_DEV_BACKGROUND (devcontainer env, or the dev-background slice name)}" \
     -u 0 --entrypoint bash -v "$HOST_OUT":/out ghcr.io/ptero-eggs/steamcmd:debian -c \
     'apt-get update -qq && cd /tmp && apt-get download libeatmydata1 && dpkg-deb -x libeatmydata1_*.deb x \
      && cp -L x/usr/lib/x86_64-linux-gnu/libeatmydata.so.1 /out/libeatmydata.so'
   ```
   The container is throwaway (`--rm`, unique `--name`) and placed by the `--cgroup-parent` shown (the
   `${VAR:?}` makes the command refuse to run if it is empty). Do not use a
   `libeatmydata` from the host's own (Debian 13) package for a bookworm image: a newer-glibc build can
   require newer symbols. *Alternative: build from source* the way `ksm-optin.c` is built
   (`gcc -shared -fPIC`): upstream is autotools (`autoreconf -fi && ./configure && make` in the image
   with `autoconf automake libtool` installed); not verified here, the package route is simpler.

   **Host path (devcontainer).** The Docker daemon is the host's, so `-v` must name the HOST path of the
   output directory, not the devcontainer's. Derive it, never type it: find the host source of a mount that
   contains your scratch dir, then append the relative part. Measured 2026-10-20: the devcontainer's `/tmp` is
   the host's `/home/vb/mdt--mounted-folders/tmp`. The general form (not run by the author as written):
   ```bash
   HOST_TMP=$(docker inspect "$(hostname)" --format '{{range .Mounts}}{{if eq .Destination "/tmp"}}{{.Source}}{{end}}{{end}}')
   : "${HOST_TMP:?could not derive the host path of /tmp}"
   OUT=/tmp/eatmydata-out; HOST_OUT="$HOST_TMP/eatmydata-out"
   ```
   On a bare host `OUT` and `HOST_OUT` are the same path.

   **Measured result 2026-10-20 (gstammtisch):** package `libeatmydata1_130-2+b1_amd64.deb`, file
   `libeatmydata.so.1.3.0` (14240 bytes), sha256
   `6951fac679ffe4fadc79219ce6cc5e0eba930ef67d3e421875de1a985e5238af`. Compare your copy:
   `sha256sum "$OUT/libeatmydata.so"` (a different sha means a different package version, not necessarily wrong).
3. **Put it in each instance volume** (the volume root, owner = the container user 988:988):
   ```bash
   install -o 988 -g 988 -m 0755 "$OUT"/libeatmydata.so /var/lib/pterodactyl/volumes/<uuid>/libeatmydata.so
   ```
4. **Update the egg** in the panel (Nests, the egg, Import, or paste the JSON). The new `EATMYDATA`
   variable (default `0`) appears. Then set `EATMYDATA` to `1` for the servers that should have it.

   > **PITFALL, measured 2026-10-20: importing the egg and setting `EATMYDATA=1` is NOT enough.** The
   > recreated container had `EATMYDATA=1` in its environment, but its `STARTUP` was still the OLD command
   > (`LD_PRELOAD=/home/container/ksm-optin.so` only), so eatmydata was not loaded. A server keeps its OWN
   > copy of the Startup Command; the egg import does not rewrite it. **Replace each server's Startup
   > Command with the new egg text explicitly: Admin → Servers → the server → Startup.**
5. **Make the container pick it up.** Do a Wings start so the container is recreated; a plain
   `docker restart` reuses the frozen environment. See
   [SOULMASK.md, "Forcing a container to pick up a Startup Command change"](../../scripts/gstammtisch-guide/SOULMASK.md#forcing-a-container-to-pick-up-a-startup-command-change-without-the-panel).
   Do it in a quiet window (stop, wait for the save, start).

#### Verify

First check that the container got the NEW startup, without dumping its environment (it holds passwords:
grep only the two keys, and trim):

```bash
docker inspect <uuid> --format '{{range .Config.Env}}{{println .}}{{end}}' | grep -E '^EATMYDATA=|LD_PRELOAD' | cut -c1-200
# expect EATMYDATA=1 and a STARTUP line containing LD_PRELOAD=/home/container/ksm-optin.so$( [ ... EATMYDATA ...
```

Then the process:

```bash
PID=$(docker top <uuid> | awk '/WSServer-Linux-Shipping/{print $2; exit}')
tr '\0' '\n' < /proc/$PID/environ | grep ^LD_PRELOAD=   # the game's own LD_PRELOAD (not the whole environ: it holds passwords)
grep -c libeatmydata /proc/$PID/maps       # > 0 : the library is mapped into the game
grep -c ksm-optin    /proc/$PID/maps       # > 0 : KSM shim still there
# count the game's sync syscalls for 60 s: with eatmydata working this prints 0
bpftrace -e 'tracepoint:syscalls:sys_enter_fsync,tracepoint:syscalls:sys_enter_fdatasync,tracepoint:syscalls:sys_enter_msync,tracepoint:syscalls:sys_enter_sync_file_range /pid == '"$PID"'/ { @[probe] = count(); } interval:s:60 { exit(); }'
```

An empty map at exit means 0 syscalls. Compare with `EATMYDATA=0`, where `fdatasync` is seen (the
`WS.log` writer and SQLite). Read this as a real result only after the server has players online; an
idle server syncs rarely.

**Baseline measured 2026-10-20 (eatmydata effectively off):** 41 and 28 `fdatasync` per 60 s with 0 players
online and `WS.log` on tmpfs. After numbers: *pending, to be added when eatmydata is live.*

#### Disable

Set `EATMYDATA` to `0` in the panel and do a Wings start (container recreated). Deleting the `.so`
also works but leaves an `ld.so` warning in the console on every start.

## KSM (`ksm-optin.so`)

**What it does.** `ksm-optin.c` is an `LD_PRELOAD` shim whose constructor calls
`prctl(PR_SET_MEMORY_MERGE, 1)` inside the game process, opting all current and future private anonymous
memory into KSM (the equivalent of `MADV_MERGEABLE` on every compatible VMA), inherited by forked children.
Why a preload: the flag lives on the calling process's `mm` and does not survive `execve`, so a wrapper that
calls `prctl` and then execs the game would lose it. It needs kernel >= 6.4 and succeeds **unprivileged**
inside a default docker container (verified 2026-07-07 on kernel 7.0; older kernels needed
`CAP_SYS_RESOURCE`, in which case the log line shows `EPERM`). It prints
`[ksm-optin] KSM enabled for this process (PR_SET_MEMORY_MERGE=1)` to the console on success. Build and deploy
are in the header of `ksm-optin.c` (`gcc -shared -fPIC -O2`, install to the volume root, chown 988:988); a
missing `.so` is a harmless `ld.so` warning and the game runs without KSM.

**Host side.** `scripts/gstammtisch-guide/files/etc/tmpfiles.d/ksm.conf` (optional; remove it to not use
KSM) writes at boot: `/sys/kernel/mm/ksm/run` = 1 (ksmd on), `advisor_mode` = `scan-time` with
`advisor_target_scan_time` = 200 (ksmd self-tunes its scan rate), and `use_zero_pages` = 1 (all-zero anon pages
map to the kernel's shared zero page instead of the general merge path, freeing that RAM without waiting for
memory pressure; added 2026-07-21). KSM merges only identical anonymous pages across opted-in processes;
read-only image pages are already shared through the page cache. If a line errors at boot, that knob does not
exist on the kernel (`ls /sys/kernel/mm/ksm/`).

**One `LD_PRELOAD`, two libraries.** The startup builds a single list, colon-separated:
`LD_PRELOAD=/home/container/ksm-optin.so` alone, or
`LD_PRELOAD=/home/container/ksm-optin.so:/home/container/libeatmydata.so` with `EATMYDATA=1` (see "The startup
change"). The order is irrelevant: the KSM shim is a constructor, eatmydata interposes libc wrappers, and
neither depends on the other.

**Verify.**

```bash
PID=$(docker top <uuid> | awk '/WSServer-Linux-Shipping/{print $2; exit}')
grep -c ksm-optin /proc/$PID/maps          # > 0 : the shim is mapped
cat /proc/$PID/ksm_stat                    # ksm_merging_pages, ksm_process_profit, ...
```

Measured 2026-10-20 on the live server: `ksm_merging_pages` 302488, `ksm_process_profit` 1173604800
(about 1.17 GB saved). What KSM can and cannot merge, and how to estimate the benefit:
[`MEASUREMENTS.md` M7](../../scripts/gstammtisch-guide/MEASUREMENTS.md#m7-ksm-across-two-soulmask-instances--setup--benefit-measurement).
