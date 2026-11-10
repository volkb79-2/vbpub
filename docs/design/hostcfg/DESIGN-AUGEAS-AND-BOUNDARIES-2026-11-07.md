<!-- origin: 2026-11-07; status: design; copied from the controller program dir 2026-11-10 -->
<!-- source: reports/hostcfg-AUGEAS-AND-BOUNDARIES-DESIGN.md -->
<!-- relation: hostcfg boundaries design; contains later coupling corrections -->

# hostcfg, Augeas, standalone installers, cgprofile rename, topos vs cgprofile (DESIGN, rev 1, 2026-11-07)

Author: Opus design agent. Repositories read-only. Anchors are **origin/main 73f0f840d** (export
`/tmp/vbpub-om`; the local `/workspaces/vbpub` checkout is 96 commits behind and must not be used
for anchors). Spike: `<controller-program-dir>/spike-augeas/` (scratch roots only,
no `/etc`, no sudo, no installs). `[V]` = verified here, `[U]` = not verified (documented or
inferred).

## 0. Decisions

| # | Decision |
|---|---|
| H-1 | **hostcfg** is a declarative host-configuration library + CLI shipped **inside the cli-extended wheel** as `cli_extended.hostcfg` (stdlib only, zipimportable, P-7 unchanged). The in-flight `cli_extended.hostendpoints` (WP-E0) becomes its `endpoint` resource: module renamed to `cli_extended.hostcfg.endpoints` **before** cli-extended 0.6.0 is released. |
| H-2 | **Augeas is not adopted now.** It solves only the in-file edit primitive, and every estate target except two (`daemon.json`, `/etc/environment`) is a whole file in a drop-in directory we own. For those two, stdlib providers beat the Augeas lenses (spike B.2). hostcfg keeps a provider seam; an `augeas` provider (via `augtool` from Debian trixie's `augeas-tools` 1.14.1) is built only when the first request targets a foreign file with no drop-in directory. No lens extension, no 1.15.0 backport. |
| H-3 | hostcfg manages **persistent configuration only**. Runtime kernel/cgroup state (io.cost, `memory.high`, `set-property --runtime`, iobench restores) stays with its single owning daemon (mdtd ownership matrix, iogovmon recommend-only). |
| H-4 | **mdt host-setup becomes a profile applier**: it applies its own hostcfg requests (owner `mdt-host-setup`) and calls each listed service's standalone install verb through one generic contract. It contains no `topos`/`cgprofile` string after the change. |
| H-5 | **cgprofile 3.0.0**: top-level `cgprofile/`, commands `cgprofiled` / `cgprofilectl` / `cgprofile`, unit `cgprofiled.service`, config `/etc/cgprofile/cgprofiled.toml`. cmru project id `cgprofile`, tag prefix unchanged (`cgprofile-v`), so version continuity is free. WP-E1 is folded into the rename branch and never runs under the old names. |
| H-6 | topos = operator console; cgprofile = workload measurement service for gates. De-duplicate the ~2.1k-line copy-adapted installer/doctor code into hostcfg; fix the **DAMON double-writer hazard** with a host-wide lease; leave collectors, storage and protocols separate. |
| H-7 | **Pause H1a/H1b as briefed** (they hard-code `CGPROFILE_*`/`TOPOS_*` keys and module paths into host-setup). Replace them with **H1g** (generic host-services loop + the deletions H1a already listed). Keep E0/E3/E4/R1 running with one naming fix-up. |

---

## A. Inventory: what the estate changes on a host today

Write method: **W** whole file in a directory we own, **M** merge into a shared/foreign file,
**RT** runtime only. "Atomic" = temp + rename.

| Kind | Target(s) | Writer, anchor | Method / validator / uninstall |
|---|---|---|---|
| sysctl | `/etc/sysctl.d/99-mdt-inotify.conf` | host-setup `install.sh:1326-1346` | W, `sysctl -q -p`; no uninstall |
| sysctl | `/etc/sysctl.d/90-mdt-io-writeback.conf` | `mdt-container-io-caps.lib.sh:951-964,1119-1142` (re-asserted each minute :1461-1478) | W atomic, read-back |
| sysctl | `/etc/sysctl.d/99-vbpub-swap.conf` | debian-install-v2 `installer.py:1541-1544`, `templates.py:185-214` | W atomic; restart `systemd-sysctl` |
| systemd units | `dev*.slice`, `mdt-buildkitd`, watchers, psi-guard, io-cost, reconcile timer, tree-cache `.mount` | host-setup `install.sh:1195-1233,1527`; `mdt_tree_cache.py:295-363` | W (sed render, not atomic; `sed -i` fix-ups :1394,:1406-1412); `daemon-reload` + LoadState check :1550-1563; no `systemd-analyze verify` |
| systemd drop-ins | `docker-.scope.d/50-default-limits.conf`, `docker.socket.d/50-mdt-dedicated-api-socket.conf`, `topos.service.d/50-mdt-socket-group.conf`, `system.slice.d/50-mdt-dedicated-dev.conf`, `<unit>.d/50-mdt-protection.conf` | `install.sh:1235-1290,1312-1314,1381-1383`; `mdt_capacity.py:987-999` | W; dedicated-dev drop-ins are the only ones with a removal path |
| systemd units | `cgpd.service`, `cgprofile.slice`, `cgprofile-{control,observer}.socket` | cgpd `cli/installer.py:1240-1287` (atomic :400-415) | W, `cgpd config check`; **uninstall :1450-1548** |
| systemd units | `topos.service`, `topos.slice`, `topos-observe.socket` | `topos/service/install.py:1140-1191` | W atomic; **uninstall :1369-1446** |
| systemd units | `iogovmon.slice`, `iogovmond{,-tick,-bench}` + timers | `iogovmon/install.sh:203-224` | W (`cmp -s`, `install -m`); **`systemd-analyze verify` :216** (only one in estate); no uninstall |
| systemd units | `vbpub-*` (cleanup, apt-check, reboot-check, iocost, zswap-config, min-free-floor), `cgroup2-flags`, `oomd.conf.d`, `fstrim.timer.d`, `journald.conf.d/99-vbpub-v2.conf` | debian-install-v2 `installer.py:943-958,1111-1157,1440-1549,1636` | W atomic (`actions.py:291-311`); no uninstall |
| tmpfiles | `mdt-cgprofile.conf`, `mdt-cgprofile-observer.conf`, `mdt-topos.conf`, `mdt-damon-stat.conf` | `install.sh:194,264,1368-1379`; `lib.sh:870-886` | W + `systemd-tmpfiles --create` |
| tmpfiles | `vbpub-thp.conf`, `vbpub-ksm.conf` | debian-install-v2 `:1478-1484,1545,1553` | W |
| sysusers / groups | `mdt-cgprofile.conf` (`g cgprofile-observer`) | `install.sh:193,263-275` | W + `systemd-sysusers`; docker-GID check :273 |
| sysusers / groups | `cgprofile.conf`, `topos.conf`; `gpasswd -a` | `installer.py:1263-1276`; `install.py:1153-1175` | W; `groupdel` only with `--remove-groups` |
| system user | `iogovmon` | `iogovmon/install.sh:113-116` (`useradd --system`) | never removed |
| daemon.json | `cgroup-parent` | host-setup `install.sh:1416-1443` (inline python, `write_text`, **not atomic**) | **M**; no validator, no restart |
| daemon.json | `features.buildkit`, `metrics-addr`, `storage-driver`, `userland-proxy`; `live-restore`, `log-driver`, `log-opts`, `default-address-pools` | debian-install-v2 `installer.py:991-1003,1068-1100` (`_read_json_for_merge` :1007-1019) | **M** atomic; no lock between the two writers, no `dockerd --validate` |
| /etc/environment | `# BEGIN mdt-buildkit` block | `install.sh:527-566` | **M** marker block, structure check, temp + mv |
| /etc/environment, profile.d | `# BEGIN host-endpoints` block, `/etc/host-endpoints{.env,.d/}`, `profile.d/host-endpoints.sh` | E0 (untracked, `x02-e0` worktree) `hostendpoints.py:31-39,505,608-687` | **M** block + W; lock, collision check, withdraw |
| profile.d | `mdt-buildkit.sh` | `install.sh:1481` | W |
| fstab | data areas, swap lines | debian-install-v2 `installer.py:3897-3906`, `:4304-4312` | **M** (keep foreign lines) atomic; host-setup uses `.mount` units instead |
| udev / modules-load | `60-mdt-io-scheduler.rules`, `61-mdt-io-queue.rules`, `mdt-bfq.conf`; `99-iogovmon-raw-read.rules`; `vbpub-zstd.conf` | `lib.sh:1125-1131,1313-1343`; `iogovmon/install.sh:224-231`; div2 `:1540` | W; host-setup deletes a foreign rule only if its sha256 is known (`lib.sh:1317-1327`) |
| apt | `debian.sources`, `apt.conf.d/*`, `preferences.d`, `docker.sources`, unattended-upgrades, needrestart | debian-install-v2 `:872-879,970-989,1166-1175` | W (backup first) |
| ssh | `/root/.ssh/authorized_keys` | div2 `:4569-4610` | M append/remove; no `sshd_config` writer |
| initramfs / cloud-init | root-shrink hooks, `cloud.cfg.d/99-…` | div2 `:2523-2621,4869-4877` | W |
| cgroup/sysfs (RT) | `io.cost.{model,qos}` (**two boot writers**: `lib.sh:1565-1590` and div2 `templates.py:395-396`), `io.max`/memory via `set-property --runtime`, psi-guard weights, zswap params, cgroup2 remount flags | host-setup lib, psi-guard `:295-340`, div2 templates | RT: out of hostcfg scope (H-3) |
| sudoers, limits, grub | none | | |

Findings the inventory surfaces (each becomes a hostcfg doctor finding, not a hand fix):
1. `cgprofile-observer` is declared twice (host-setup `mdt-cgprofile.sysusers` and cgpd's `cgprofile.conf`).
2. The phase-1 installers refuse host-setup's own files (`installer.py:237-269`, `install.py:421-453`), and host-setup still writes them on every run (`install.sh:1368-1383`).
3. The `vm.dirty_*` lexical-order fight (`99-vbpub-swap` ratio after `90-mdt-io-writeback` bytes). `[U]` It bites only when `IO_WRITEBACK_*` is set.
4. The two daemon.json writers take no lock, and host-setup's write is not atomic.
5. Two boot-time io.cost writers.
6. host-setup, debian-install-v2 and iogovmon have no uninstall.

Ownership-marker conventions are ad hoc (`# RENDERED by host-setup…`, `Generated by vbpub debian-install v2`, `Rendered by iogovmon/install.sh`, `mdt-`/`vbpub`/`50-mdt-` prefixes). cgpd and topos use no in-file marker, only `install.toml`.

**Shared files that genuinely need a merge: `/etc/docker/daemon.json`, `/etc/environment`, `/etc/fstab`
(div2 only), `/etc/group`/`gshadow` (via tools), `/root/.ssh/authorized_keys`.** Everything else is
a whole file in a drop-in directory.

---

## B. Augeas evaluation

### B.1 What was spiked

- **1.14.1-1.1~deb13u1** (the trixie package): `augeas-tools`, `libaugeas0` and `augeas-lenses` debs
  from deb.debian.org, extracted with `dpkg-deb -x`, run with `LD_LIBRARY_PATH`. `augtool --version`
  = 1.14.1 `[V]`. Its changelog shows CVE-2025-2588 already patched in trixie `[V]`.
- **1.15.0**: built from the upstream release tarball. Its sha256 `95b2b5c4…30ff` equals Debian's
  `augeas_1.15.0.orig.tar.gz` `[V]`. It configures and builds on trixie against the system
  `libxml2.so.2` (readline headers from the trixie `libreadline-dev` deb) `[V]`.
  - Published 2026-09-29 per the GitHub API, so it clears the 14-day freshness rule.
  - Debian's own `1.15.0-1` (sid) binary does **not** run on trixie: it needs `libxml2.so.16`
    (`Depends: libxml2-16`, `ldd`: not found) `[V]`. A 1.15 on trixie therefore means a backport
    rebuild that we own.
- **python3-augeas 1.2.0** (cffi, `_augeas.abi3.so`): works on Python 3.14 + cffi 2.0 with libaugeas
  1.14.1 `[V]`. It is a C extension, so it is neither stdlib nor zipimportable, which conflicts with
  P-7.
- The devcontainer has no apt lists (`apt-cache policy` prints nothing); versions come from the pool
  listing.
- Script: `spike-augeas/scripts/spike.sh`. Outputs: `spike-v1141.out`, `spike-b1150.out`; they are
  identical except where noted in the table below.

### B.2 Results (1.14.1 and 1.15.0 behave the same unless a column says otherwise)

| Test | Result |
|---|---|
| Sysctl: set existing key, add key with a comment before it | Comments, blank lines and odd whitespace (`kernel.pid_max=4194304   `) kept byte-for-byte `[V]` |
| Sysctl idempotence | Re-running the same `set`s does not rewrite the file (same inode and mtime) `[V]` |
| Systemd drop-in and unit edit | Works, comments kept. `ExecStart` is split into `command`/`arguments`. New keys go at the end of the section **after the blank line** (cosmetic) `[V]` |
| Systemd `+` flag | 1.14.1 stores `+/usr/…` inside `command`; 1.15.0 has a `fullprivileges` node `[V]` |
| Json, daemon.json | Key order and existing formatting kept. **But an appended key renders as `\n,\n"cgroup-parent":"dev.slice"}` with no final newline** (still valid JSON) `[V]` |
| Json typing | Typed set needs the node kind (`string`/`const`/`number`/`dict`) `[V]` |
| Json duplicate keys | `{"a":1,"a":2}` loads silently as two entries `[V]` |
| Json, no autoload | The lens has no file filter, so it needs an explicit `-t "Json incl …"` `[V]` |
| Shellvars `/etc/environment` | Can insert BEGIN/END comments, but Augeas writes them as ` # BEGIN …` (**leading space**) `[V]`. Has no concept of a block. A value with a space is refused unless the caller quotes it `[V]` (pam_env quoting differs from shell quoting `[U]`) |
| Toml | Inline comments kept on edit `[V]` |
| Group / Gshadow | Membership edits work `[V]`, but they bypass `lckpwdf`/nscd/sssd `[U]`. Use `gpasswd` |
| Tmpfiles | 1.14.1 has no `/usr/lib/tmpfiles.d` filter and rejects the `$` modifier; 1.15.0 parses it `[V]` |
| Parse error | `/augeas/files/<f>/error` with line, char and lens position `[V]` |
| Put error (bad label or value) | That file's save is aborted `[V]` |
| **Multi-file transaction** | **None**: a good sysctl edit **was written** while a bad `/etc/group` edit in the same `save` failed `[V]` |
| Dry run / diff | `-n` (SAVE_NEWFILE) writes `.augnew` to diff against; `-b` writes `.augsave` backups; python `SAVE_NOOP` + `/augeas/events/saved` + `preview()` returns the text without writing (1.14.1) `[V]` |
| Symlinked target | Written through the symlink `[V]` (hostcfg must refuse that itself) |
| String-in/string-out | Python `text_store`/`text_retrieve` edits a buffer with no file IO, 26 ms. On error it raises `"failed: No error"` (binding bug) `[V]` |
| Speed | `augtool` with autoload of all 233 lenses: 0.31 s; one lens via `-A -t`: 0.01 s `[V]` |

### B.3 What Augeas does not do (all of it is the actual requirement)

- Ownership per file, key or block, and conflict detection between owners.
- Validators: `systemd-analyze verify`, `dockerd --validate`, `visudo -c`, `sshd -t`, sysctl key
  existence.
- Reload/restart hooks, `systemd-sysusers`/`tmpfiles` runs, and pending-restart records.
- Atomic multi-file apply with rollback (verified above: none).
- Audit, records and drift detection.
- Privilege and safety checks (symlinks, modes, executable checks for referenced scripts).
- Users and groups the right way (`gpasswd`, `systemd-sysusers`).

### B.4 Verdict and cost

- **Verdict (ii) now, (i) later only on demand.**
  - Of the estate's targets, Augeas would help only with `daemon.json` and `/etc/environment`. For
    both, the spike shows stdlib code giving cleaner output (json: canonical 2-space indent, final
    newline, duplicate-key refusal; block: exact marker lines, E0 already implements it).
  - Every other kind is better served by owning a whole drop-in file.
  - Puppet's architecture (Augeas as one provider beneath a resource/ownership layer) is the right
    mental model, and hostcfg follows it.
- **When a request first targets a foreign file with no drop-in** (candidates: `/etc/login.defs`,
  `/etc/adduser.conf`, `/etc/default/*` without a `.d`; *not* sshd, apt, journald, sudoers, grub,
  modprobe or udev, which all have drop-in dirs on trixie `[U]` per Debian defaults):
  - add provider `augeas`: `augtool --root=<stage> -S -I <lensdir> -A -t "<Lens> incl <file>" -n`
    on a staged copy, then diff the `.augnew`, then hostcfg's own atomic write;
  - Debian dependency `augeas-tools` (trixie 1.14.1);
  - cost **S** (~300 LOC with tests, about one Codex day).
- **Not recommended:**
  - python-augeas inside the install path (breaks P-7);
  - a 1.15.0 trixie backport: **M** plus ongoing security tracking, for changes (Systemd `+`,
    Tmpfiles `$`, Aptsources822) that only matter when parsing files we would write whole anyway;
  - lens work: upstream Json pretty-printing, **L**, no estate value. If a private lens is ever
    needed, ship it as package data `cli_extended/hostcfg/lenses/` and pass it with `-I`.

---

## C. hostcfg: the generic layer

### C.1 Shape

`cli_extended.hostcfg` has a Python API and a CLI:

- API: `load(path|dict)`, `plan(req, root="/")`, `apply(req, root="/", dry_run=False)`,
  `remove(owner)`, `doctor(owner=None)`, `audit()`.
- CLI: `python3 -m cli_extended.hostcfg {plan,apply,remove,doctor,audit,show} [REQ.toml] [--root DIR] [--json] [--yes] [--dry-run]`.
  It is built on cli-extended `VerbSpec(mutating=True, dry_run=True)` and `register_doctor`.
- `--root` is honoured everywhere for VM tests and scratch roots. Only the post-actions
  (`systemctl`, `sysctl`, `gpasswd`) are skipped or redirected under `--root`.

State:
- `/var/lib/hostcfg/owners/<owner>.toml`: what an owner holds (resources, sha256 per file, claimed keys and blocks, txn id, writer).
- `/var/lib/hostcfg/txn/<id>/`: pre-images, last 5 kept.
- `/run/lock/hostcfg.lock`: one flock. E0's endpoint lock is taken inside it.

### C.2 Resource kinds

| Kind | Writes | Built-in checks | Post-action | `remove` |
|---|---|---|---|---|
| `file` | whole file at `path`; marker header when the comment syntax is known | path in an allowed dir, no symlink in any component, mode/owner; `validate = "json"\|"toml"\|"udev"\|"sudoers"\|"sshd"\|["cmd",…]` on the staged file | `reload`/`restart` of named units, `udevadm control --reload` | delete |
| `unit` | `/etc/systemd/system/<name>` or `<name>.d/<dropin>.conf` (dir is a parameter, see D) | `systemd-analyze verify` on a staged unit dir `[U exact invocation for drop-ins]`; **exec-check** of every `Exec*=`/`ExecCondition=` path (prefixes `-@:+!` stripped): exists, executable, root-owned, no group/other-writable component, not under `/tmp`, `/home`, `/var/tmp`; `preflight = [argv]` run with a 30 s timeout | `daemon-reload`; `enable`; `on_change = "restart"\|"reload"\|"none"\|"pending"`; **socket rule**: restart a `.socket` only when its own text changed (plan 3.5 step 7) | stop, disable, delete, `reset-failed` |
| `sysctl` | one engine-owned file `/etc/sysctl.d/60-hostcfg-<owner>.conf`, one `# comment` + `key = value` per entry, sorted | key exists under `/proc/sys`; cross-owner duplicate refused; doctor warns when a later file (by `systemd-sysctl --cat-config` order) overrides it (finding A.3) | `sysctl -p <file>` when `apply_now`, then read back | delete; runtime value left as is (reported) |
| `tmpfiles`, `sysusers` | `/etc/{tmpfiles,sysusers}.d/<owner>.conf` | line syntax; dry-run option of the systemd tool if available `[U]` | `systemd-tmpfiles --create <f>`, `systemd-sysusers <f>` | delete file |
| `group` | via `sysusers` (`g name -`) | constraints `not_gid_of = ["docker"]`, `not_gid = [0]`, `distinct_from = [...]`, `level = true` (then the **cross-owner rule**: no two `level` groups of any owners share a GID; this replaces host-setup's cross-service check, P-5) | `gpasswd -a` for `add_members` (only ever added) | `groupdel` only when the owner created it **and** `remove --remove-groups` |
| `block` | marker block `# BEGIN <owner> (managed by hostcfg; do not edit)` … `# END <owner>` in a shared file (`/etc/environment`) | structure rules of `install.sh:527-560` and E0; keys duplicated outside the block refused; value alphabet per file type | none | strip the block |
| `json_key` | one key path in a shared JSON file (`daemon.json`) | stdlib `json`; duplicate keys refused (`object_pairs_hook`); key order kept; output 2-space indent + final newline; `validate = "dockerd"` → `dockerd --validate --config-file <staged>` `[U: flag on the installed Docker]` | `on_change = { unit = "docker.service", action = "pending"\|"restart" }`. **Default `pending`**, recorded and shown by doctor, because a dockerd restart kills `live-restore=false` containers | delete the key **only** if its value is still ours |
| `endpoint` | E0 fragment `/etc/host-endpoints.d/<owner>.env` + aggregate + `/etc/environment` block + profile.d | E0 `plan/verify/check`; values may reference `@gid:<group>` (resolved after the group step) | none | E0 `withdraw` **first** |
| `check` | nothing | `exec = [argv]`, `expect_exit = 0`, `timeout`; also `path_mode` checks (dir exists with owner/mode or is absent; P-11 style "refuse if exists with another mode") | | |
| (`kv` via `augeas`) | deferred (B.4) | | | |

### C.3 Request document (TOML, schema `hostcfg = 1`)

```toml
hostcfg = 1
owner   = "cgprofile"                 # required; ^[a-z][a-z0-9-]{1,31}$; the ownership key
writer  = "cgprofiled install 3.0.0"  # recorded, shown by audit
[[<kind>]]                            # typed arrays, see C.2; order inside a kind is kept
```

There are no defaults that change host state. A missing required field is an error naming it
("defaults are hazards"). Unknown keys are errors.

### C.4 Apply pipeline

1. **Load and normalise.** Schema check; templates are resolved *before* hostcfg (hostcfg receives
   literal content).
2. **Plan** (read-only, under the lock). For each resource, read the current state and compute the
   diff (unified diff for files, key deltas, actions to run). Then the **ownership check**:
   - a file or key or block held by another owner → **refuse**, naming both;
   - an existing file or key not in any record:
     - equal to the requested content: adopt silently, recorded as `adopted`;
     - different: refuse unless the resource says `adopt = true`;
   - a file we hold whose sha256 ≠ the recorded value (hand-edited) → refuse unless `--take-over`.
     The diff is shown.
   - `hostcfg plan` stops here. Exit 0 = no change; exit 10 = changes pending (scriptable dry run).
3. **Validate** on staged copies in `/var/lib/hostcfg/txn/<id>/stage/` (validators, exec-check,
   preflight, group constraints). Any failure → exit 2, **nothing written**.
4. **Apply**, in kind order: `sysusers/group` → `tmpfiles` → `file` → `unit` → `sysctl` →
   `json_key`/`block` → `endpoint`. Each file goes through a pre-image into `txn/<id>/`, then temp +
   fsync + rename + dir fsync.
5. **Post-actions:** `daemon-reload` once, then sysusers/tmpfiles/sysctl loads, then
   enable/start/restart in dependency order, `gpasswd -a`.
6. **Verify:** re-read every file (sha256 = staged), units `active` where enabled, sysctl read-back,
   endpoint `verify`, and an optional `[[check]]` set with `phase = "verify"`.
7. **Record:** write `owners/<owner>.toml` (atomic) and log one journald line per txn
   (`SYSLOG_IDENTIFIER=hostcfg`, txn id, owner, changed paths).
8. **Rollback** (steps 4-6 failed):
   - restore the pre-images in reverse order, delete newly created files, `daemon-reload`, restart
     the units that were restarted;
   - groups and memberships are **not** rolled back (they are recorded as `irreversible`);
   - exit 4 = rolled back cleanly, exit 5 = rollback incomplete (names what to fix).

   Rollback is honest best-effort: a restarted service restarts again on the old config.

Idempotence: an unchanged request does no write and no action. Upgrade = re-apply with new content;
resources dropped from the owner's request are removed (the record is the diff base). `doctor
[--owner]` compares records with disk and runtime: hashes, blocks, keys, units enabled/active,
pending restarts, cross-owner GID rule, sysctl effective values, foreign files in our drop-in dirs
claiming our trees (generalises P-11 `foreign-rules`). `audit` lists who owns what plus unowned
`mdt-*`/`vbpub-*` files.

### C.5 Who calls it

- **Service install verbs** (`cgprofiled install`, `toposd install`, `mdtd install`, `iogovmond
  install` later) build one request in memory from their config + flags and call `apply()`. They
  keep what is theirs: venv/version tree, `MANIFEST`, drain logic, config rendering, P-11 refusal.
  The ~2.1k duplicated lines per side (F.4b) move into `cli_extended.hostcfg.service`: atomic
  write, `version_key`, `register_install`, `find_foreign_rules`, socket/group/unit doctor checks,
  `/opt/<svc>/<ver>` + `current` symlink + prune.
- **`<svc>d install --print-request`** emits the TOML, for review and for H-4.
- **mdt host-setup (H-4)** becomes `install.sh` + a **host profile** `/etc/mdt/host-profile.toml`:
  ```toml
  profile = 1
  requests = ["/usr/share/mdt-host-setup/requests/slices.toml", "…/buildkit.toml", "…/docker.toml"]  # owner mdt-host-setup, rendered from today's units/*.in
  [[service]]
  wheel  = "cgprofile-3.0.0-py3-none-any.whl"
  sha256 = "…"
  source = "https://github.com/volkb79-2/vbpub/releases/download/cgprofile-v3.0.0/"
  args   = ["--add-user", "control=runner", "--add-user", "control=volker", "--enable"]
  ```
  The **generic service-installer contract**:
  - the wheel's `entry_points.txt` group `[vbpub.service_installer]` names the module (read with
    `zipfile`, stdlib);
  - host-setup runs `PYTHONPATH=<svc.whl>:<cli_extended.whl> python3 -m <module> install --yes --wheel <whl>@sha256:<h> --wheel-source <dir> --cli-extended-wheel <whl>@sha256:<h> <args…>`;
  - exit 3 (drain) is handled as today;
  - then `<module> doctor --json`;
  - the wizard asks each service `install --describe-inputs --json` for its questions. host-setup
    never interprets service arguments.
- **debian-install-v2**: unchanged on the fast path. Later (DI-1) its sysctl, journald, daemon.json
  keys and apt files become requests with owner `debian-install-v2`, which turns D-F1 from a comment
  into an enforced key claim. Its `host_services` stage (mdtd INST-01) calls host-setup with a
  profile, so it never learns service names.
- **mdtd INST-01**: as designed, but it passes a host profile instead of service-specific values.

### C.6 Worked examples

**(1) cgprofile sockets, groups, endpoint**: what `cgprofiled install --print-request` emits (abridged):
```toml
hostcfg = 1
owner = "cgprofile"
writer = "cgprofiled install 3.0.0"

[[group]]
name = "cgprofile-control"
level = true
not_gid_of = ["docker"]
not_gid = [0]
distinct_from = ["cgprofile-observer"]
add_members = ["runner", "volker"]

[[group]]
name = "cgprofile-observer"
level = true
not_gid_of = ["docker"]
not_gid = [0]
distinct_from = ["cgprofile-control"]
add_members = []

[[check]]          # P-11 / plan 3.1: refuse a pre-existing tree with another owner or mode
path_mode = { path = "/run/cgprofile/control", owner = "root", group = "root", mode = "0755", if_exists = "must-match" }

[[unit]]
name = "cgprofile-control.socket"
on_change = "restart"
enable = true
text = """
[Unit]
Description=cgprofile daemon, control access level
[Socket]
ListenStream=/run/cgprofile/control/cgprofile.sock
SocketUser=root
SocketGroup=cgprofile-control
SocketMode=0660
DirectoryMode=0755
FileDescriptorName=control
RemoveOnStop=no
Service=cgprofiled.service
[Install]
WantedBy=sockets.target
"""

[[unit]]
name = "cgprofiled.service"
enable = true
on_change = "restart"
source = "/opt/cgprofile/3.0.0/share/units/cgprofiled.service"   # rendered by `cgprofiled unit print`
preflight = ["/opt/cgprofile/3.0.0/venv/bin/cgprofiled", "config", "check", "--config", "/etc/cgprofile/cgprofiled.toml"]
# exec-check of ExecStart=/opt/cgprofile/current/venv/bin/cgprofiled is automatic

[[endpoint]]
CGPROFILE_CONTROL_SOCKET     = "/run/cgprofile/control/cgprofile.sock"
CGPROFILE_CONTROL_SOCKET_DIR = "/run/cgprofile/control"
CGPROFILE_CONTROL_GID        = "@gid:cgprofile-control"
# OBSERVER_* likewise
```

**(2) A sysctl with a comment** (owner `mdt-host-setup`). It renders
`/etc/sysctl.d/60-hostcfg-mdt-host-setup.conf`:
```toml
hostcfg = 1
owner = "mdt-host-setup"
writer = "mdt host-setup install.sh 3.4.0"
[[sysctl]]
key = "fs.inotify.max_user_watches"
value = "1048576"
comment = "devcontainer file watchers + run-gate worktrees; see mdt-cockpit/host-setup/README.md#inotify"
apply_now = true
```

**(3) A daemon.json setting** (owner `mdt-host-setup`; debian-install-v2's keys are claimed under its
own owner, so the D-F1 split is enforced):
```toml
hostcfg = 1
owner = "mdt-host-setup"
writer = "mdt host-setup install.sh 3.4.0"
[[json_key]]
file = "/etc/docker/daemon.json"
key = ["cgroup-parent"]          # path list; nested e.g. ["log-opts", "max-size"]
value = "dev-background.slice"   # TOML type = JSON type
validate = "dockerd"
on_change = { unit = "docker.service", action = "pending" }   # doctor: "pending restart since <txn>"
```

---

## D. Packaging stance

The standalone `<name>d install|uninstall|doctor` verbs are the product interface. Debian packages
are postponed. What must be true **now** so that a `.deb` can wrap the verbs later without a
redesign:

1. **Non-interactive completeness.** Every value comes from flags or `/etc/<svc>/install.toml`;
   the TTY wizard only fills flags. debconf later maps to the same flags.
2. **Offline install.** `--wheel-source <dir>` with sha256 pins works with no network; a package
   ships wheels in `/usr/share/<svc>/wheels/`.
3. **Re-run = upgrade, idempotent.** `postinst configure` calls `<svc>d install --yes --from-record`.
   `prerm remove` calls `uninstall` (data kept). `postrm purge` calls `uninstall --purge-data --remove-groups`.
4. **Config files are operator-owned once written.**
   - The verb never overwrites a hand-edited `/etc/<svc>/*.toml`: hash in the record, a
     `.hostcfg-new` beside it, doctor warns. This is ucf semantics, so a package can use `ucf` and
     keep the behaviour.
5. **Install locations are parameters.**
   - `--unit-dir` (packages: `/usr/lib/systemd/system`), sysusers/tmpfiles dirs (`/usr/lib/…`) and
     the prefix (`/opt/<svc>` now; FHS allows `/opt/<package>` for add-on software).
   - hostcfg resources take the same parameters, plus `packaged = true`: files dpkg owns become
     **verify-only** in hostcfg, so dpkg and hostcfg never both own a path.
6. **The uninstall removes exactly what the record lists.** Nothing is discovered by glob except
   doctor's foreign-file *report*.
7. **No new legacy.** The P-11 refusal of foreign layouts stays a refusal.

Augeas, if ever used, is a `Depends: augeas-tools` of the cli-extended package (trixie version).

---

## E. cgprofile rename (3.0.0)

### E.1 Today

- Directory `scripts/cgroup-profiler`; cmru id `cgroup-profiler`.
- Tag prefix `cgprofile-v`, latest `cgprofile-v2.2.0` (never pinned; VM acceptance **not accepted**,
  C1-C5).
- Dist/package `cgprofile`.
- Commands: only `cgpd` and `cgpctl` (`pyproject.toml:55-59`). There is **no** end-user `cgprofile`
  command; `cgpctl` mixes daemon verbs with local ones (`run` :913, `mark` :924, `report` :889).
- Image names are already `cgprofile*`.

### E.2 Target names (no aliases, no fallbacks)

| Thing | 2.x | 3.0.0 |
|---|---|---|
| tree | `scripts/cgroup-profiler/` | `cgprofile/` (`git mv`; history via `git log --follow`) |
| cmru id / orchestration key | `cgroup-profiler` | `cgprofile` (`cmru.toml:29`, `cmru.orchestration.toml:55,214,272,277-278`, graph comments :23-42) |
| tag prefix / dist / package | `cgprofile-v` / `cgprofile` / `cgprofile` | unchanged → `git describe --match cgprofile-v*` continuity (cmru reads `project.prefix`, `config.py:1485`) |
| root daemon | `cgpd` (`run`, `config check`, `reload`, `unit print/check`, `install`, `uninstall`, `doctor`) | `cgprofiled` (same verbs) |
| socket client (control level) | `cgpctl` status/host/sessions/show/watch/start/stop/prune/config/doctor | `cgprofilectl` (same verbs) |
| end-user client | (inside `cgpctl`) | `cgprofile`: `run -- CMD` (direct mode), `mark`, `report` (local render, report extra), `analyze`, skills verbs |
| unit / drop-in dir | `cgpd.service`, `cgpd.service.d` | `cgprofiled.service`, `cgprofiled.service.d`; slice and sockets unchanged (`cgprofile.slice`, `cgprofile-{control,observer}.socket`) |
| config | `/etc/cgprofile/cgpd.toml` | `/etc/cgprofile/cgprofiled.toml`; `install.toml`, `/var/lib/cgprofile`, `/run/cgprofile/*`, groups unchanged |
| client symlinks | `/usr/local/bin/cgpctl` | `/usr/local/bin/cgprofilectl`, `/usr/local/bin/cgprofile` |
| env | `CGPCTL_SOCKET` | removed (already planned); `CGPROFILE_*` unchanged |
| entry-point group | n/a | `[vbpub.service_installer] cgprofile = "cgprofile.service.install"` |

P-11 refusal gains the 2.x layout: `cgpd.service`, `cgpd.service.d/`, `/etc/cgprofile/cgpd.toml`,
`/usr/local/bin/cgpctl`. This is a refusal with removal steps, not a migration. Fresh hosts never
hit it.

### E.3 References to change (origin/main, `git grep -E 'cgpd|cgpctl|cgroup-profiler|cgroup_profiler'`)

| Where | Count | Load-bearing items |
|---|---|---|
| the project itself | 2710 hits / 182 files | 1200+ hits in `docs/cli-surface.json`, `CLI-SPEC.md` and `cli-review.toml` (regenerate them). Path depth: `pyproject.toml:77 root="../.."` → `".."`, `test_packaging_layout.py:114`, `tools/gate.sh:41`, `tests/test_summary.py:133 parents[3]`, `test_deployment_contract.py:165,185`, `test_docs_sync.py:242-272`, `test_version.py:207,333`. `run-gate.toml:48,63,76,132,155`. `Dockerfile:71`. `cmru.toml:13` header |
| run-gate-project | 285 / 61 | `tests/test_run_gate.py:32935-32940` builds `scripts/cgroup-profiler` and **silently skips if it is absent**: fix it and make it fail. Also `tests/_testdeps.py:25`, `assay.toml:36,116`, and `run_gate.py` comments :97-108,:693,:2617,:2638 |
| mdt-cockpit | 170 / 27 | Removed outright by H1g (`mdt-cgprofile-install.sh`, `check.sh`, `units/mdt-cgprofile*`, `env.example`, wizard validators). Also `pip/wheels.list`, `templates/*devcontainer.json`, `tests/runtime/test_image_contract.py`, `mdt_capacity.py:360-362` (`cgpd.service` in `DEDICATED_PROTECTED_UNITS_DEFAULT` → `cgprofiled.service`) |
| libraries | 126 / 46 | rigor-record `schemas/rigor-machine-1.json:4-5`, `cli.py:412`, golden fixtures; `cli-extended/docs/CONSUMERS.md:1286` |
| assay | 83 / 36 | golden `assay-r1-pass.json:39`, `assay-r2-survivors.json:29` (`"project": "cgroup-profiler"`) |
| cmru | 49 / 19 | `tests/test_tester_unified_image.py` (symlink and key, :152-4948) |
| ciu | 31 / 8 | `tests/tests/test_ciu_202_global_only_roots.py:122-123,168` |
| tester-unified / tester-base | 12 / 6 | `Dockerfile:176,343,353`, `gen-requirements.py:47,72`, docs |
| root | | `.dockerignore:59,85,119-121`, `.gitignore:76` (`!scripts/cgroup-profiler/lib/`); check the bare-`core` trap for the new tree; `docs/BOOTSTRAP-FROM-ZERO.md:37,328`; sockets plan (58 hits: add a header note only) |
| topos, nyxloom, debian-install-v2 | 3, 12, 6 | docs or comments |

Historical reports and logs are **not** rewritten.

### E.4 cmru procedure and version

cmru has no rename support (no `previous_prefix` or alias), and it does not need it: the prefix is
unchanged.

1. One commit: the `git mv`, the cmru id, the orchestration key, path-depth fixes.
2. Name commits: commands, units, config, P-11 additions.
3. E1 content commits.
4. `feat!:` marker, so the next version is **3.0.0** (`cmru release cgprofile --dry-run` must show
   `2.2.0 → 3.0.0`).

Known gap: cmru's changelog range uses project paths without `--follow` (`changelog.py:275`), so
the generated 3.0.0 section shows only post-move commits. Write the 3.0.0 CHANGES section by hand
in the branch (rename + E1 + C1-C5), and keep the title "Changelog — cgprofile".

### E.5 Sequencing with X02

- **E1 has not started.** It becomes **REN**: one branch `cgprofile-3-20261107` = rename commits
  first, then E1 (endpoint publish via `cli_extended.hostcfg.endpoints`, env-only
  `cgprofilectl`/`ctlwire`, owner de-dup, doctor) and the C1-C5 + BrokenPipe fixes.
- E3, E4 and R1 add only `CGPROFILE_*` names (they survive) plus some `cgpd` strings in messages,
  docs and tests. Send each running agent one fix-up line now: "say `cgprofiled`/`cgprofilectl`,
  never `cgpd`/`cgpctl`, in new text", and do a grep check before M1.
- cgprofile 2.3.0 is **cancelled**: the release set becomes cli-extended 0.6.0 → topos 0.8.0,
  cgprofile 3.0.0 → run-gate 25.4.0.

---

## F. topos vs cgprofile

### F.1 Measured overlap (origin/main; total LOC topos 35,316, cgprofile 23,497)

| Area | topos | cgprofile | Verdict |
|---|---|---|---|
| cgroup/proc collectors | `collect/cgroup.py` 526 (`:352-418` one pass), `collector.py` 517, `host.py` 609 (broad: zram, ZFS, GPU, net, block) | `metrics.py` 324 (`:33-99`), `util.py` 407, `sampler.py` 327 (adaptive 0.25-2 s), `proc_stat.py` 20 | Same kernel files, ~150 lines of equivalent parsing with different return shapes. Low value to share |
| observer, limits, lanes | none (only `effective_memory_min`) | `resource_observer` 626, `observer_windows` 837, `subtree` 295, `events` 337, `limits` 378, `placement` 2262 | No overlap |
| DAMON | `damon/` 986 + `daemon/paddr_lifecycle.py` 320; takes the first off slot (`control.py:250-263`) | `damon.py` 1053 + vendored `_damon` 898; reserves 16 slots, refuses if `nr_kdamonds≠0` (`damon.py:552-556`), flock in its own state dir | **Same sysfs, no arbitration: live hazard** (topos first → cgprofile DAMON degraded; cgprofile first → topos turns on a reserved slot, which cgprofile then quarantines) |
| iocost | `collect/iocost.py` 63 (read) | none (iogovmon reads, `collectors.py:281`) | trivial |
| daemon + sockets | `daemon/` 5768, `{id,op,v}` JSON lines, one level `observe` | `serve.py` 4409, `ctlwire` 304, `access` 273, `{verb,args,contract}` JSON lines, levels control + observer | Same pattern, different schema and privilege. Keep separate |
| **install + doctor** | `service/install.py` 1640, `doctor` 455 | `cli/installer.py` 1752, `service_doctor.py` 561 | **Copy-adapted duplicate**: 22 install functions and 20 doctor checks share names; `_caller_in_group` is byte-identical |
| stored data | JSONL frame recordings, ring 300 s + 60 s points, `/var/lib/topos/history` 1 day / 256 MB | per-session dirs, gzip-member JSONL, 200 sessions / 14 days | Different models. Keep separate |
| output, consumers | TUI, MCP, text reports (operators); **no runtime consumer in run-gate or assay** | analysis/plotly/md reports, placement; **consumed by run-gate (`run_gate.py:505-511,728,2938,3073`) and assay (`resource_limits.py:1070,1100`)** | No overlap |

### F.2 Identity

- **topos**: the operator's live console for one host. It shows what is under pressure now and
  recently and why, across every cgroup and process, and offers explicit, operator-confirmed
  actions. Read-mostly; TUI and MCP; no gate depends on it.
- **cgprofile**: the workload measurement service. It profiles named workloads (sessions, lanes)
  over their life, resolves effective limits, places and observes gate lanes, and hands run-gate
  and assay contract-versioned evidence. A privileged daemon with a stable API.

### F.3 What is shared, what stays separate, and the sequence (no big-bang)

1. **DAMON lease (S per side, do first, in REN and E2).** A host-wide lease
   `/run/lock/damon-owner.lock` (flock) with `/run/damon-owner.json` `{owner, pid, since}`.
   - Whoever holds it may write `nr_kdamonds` and kdamond `state`; the other side degrades with a
     message naming the holder.
   - cgprofiled takes it at its first acquire. topos takes it per vaddr/paddr session.
   - The lease is generic, so neither tool names the other in code.
   - Later (F-3): topos's DAMON views read cgprofiled's sessions over the observer API when the
     lease holder is cgprofiled.
2. **Install/doctor de-duplication (M, after REN and E2 ship).**
   - Extract `cli_extended.hostcfg.service` + resources (C.5).
   - Port cgprofiled first (3.1.0), then toposd (0.9.0). Each port deletes its local copy in the
     same change.
3. **Collectors: not shared now.** Revisit only if a third consumer appears (mdtd's snapshot is the
   candidate). The shape would be a stdlib `cli_extended`-free `cgstat` module (~400 lines:
   cgroup-file and pressure parsers, `/proc/<pid>/stat`).
4. **Stays separate:** wire protocols, storage, analysis/reporting, UI/MCP, placement, observer.
   Neither imports the other.

---

## G. Work packages

Sizes: S < 300 changed lines, M < 900, L more.

| WP | Content | Size | Depends | Status impact |
|---|---|---|---|---|
| E0 | hostendpoints (running) | M | - | **continue**; add **E0b** |
| E0b | rename module to `cli_extended.hostcfg.endpoints`, `python3 -m cli_extended.hostcfg.endpoints …` CLI, docs | S | E0 reviewed | before cli-extended 0.6.0 |
| E3, E4, R1 | run-gate env-only, assay env-only + release-gate, runner script (running) | M, M, S | - | **continue** + naming fix-up line (E.5) |
| REN | cgprofile 3.0.0: move + names + E1 content + C1-C5 + DAMON lease + P-11 for the 2.x layout + `[vbpub.service_installer]` entry point + `install --describe-inputs` | L | E0b API | **replaces E1** |
| E2 | topos 0.8.0 as briefed + module path from E0b + DAMON lease + entry point + `--describe-inputs` | M | E0b | **unpause** after E0b |
| H1g | host-setup: H1a's deletions (topos/cgprofile units, observer provisioning, `--observer-only`, tmpfiles, `mdt-cgprofile-install.sh`, `check.sh` sections) + generic `[[service]]` loop over a host profile (`/etc/mdt/host-profile.toml`) + per-service `doctor --json` in `check.sh` + `mdt-host-setup` endpoint fragment + refusal on old files | M | E0b, REN/E2 verb flags | **replaces H1a and H1b's check part** |
| H1b' | BuildKit `mdt-buildkit` group (P-5a) | S | H1g | **paused**: operator go on the name is still open |
| V-2, M1, REL | VM acceptance on branch wheels (incl. the cgprofile VM script under new names), merge, releases cli-extended 0.6.0 → topos 0.8.0, cgprofile 3.0.0 → run-gate 25.4.0 | ops | all above | as planned |
| V-3, M2 | host-setup VM case with a profile, merge | ops | H1g, REL | |
| HC-1 | hostcfg engine: request schema, plan/validate/apply/verify/record/rollback, `file`/`unit`/`sysctl`/`tmpfiles`/`sysusers`/`group`/`block`/`json_key`/`check`, doctor/audit, `--root` tests | L | E0b | **not on the fast path**; start beside REN |
| HC-2 | `hostcfg.service` extraction; cgprofiled 3.1.0, toposd 0.9.0 ported (each deletes its copy) | M + M | HC-1, REL | |
| HS-2 | host-setup's own resources as requests (slices, buildkit, docker.socket drop-in, daemon.json `cgroup-parent`, inotify sysctl, udev, tmpfiles) | L | HC-1 | |
| DI-1 | debian-install-v2 writes via hostcfg (owner `debian-install-v2`); resolves A.3/A.5 double writers | M | HC-1 | optional |
| AUG-1 | `augeas` provider | S | first real need | **not scheduled** |
| D1' | amend sockets plan (D5 superseded by `json_key` ownership; 5.3 → H1g), endpoints design (E0b, H1g), mdtd 3.3/INST-01 (profile) | S | - | docs |

**Pause now:** H1a and H1b as briefed; the cgprofile 2.3.0 release plan; any edit to host-setup.env
that adds `CGPROFILE_*`/`TOPOS_*` keys.

**Still valid:** E0, E3, E4, R1; E2 (with the E0b path); the endpoint record design (E-1..E-6) with
cgprofile names; the runner design; the assay release-host design; mdtd S1/S2.

**Fastest honest path to v1001 running the phase-1 services under the new direction:**
1. **Now, in parallel.** Ops track: div2 fix4 merge, then the v1001 reinstall per runbook
   (operator go), post-install checks, `provision-runner.sh`. Code track: E0 → E0b; E3/E4/R1 finish.
2. **REN and E2 in parallel.** This is the critical path, L for REN. **H1g** starts as soon as
   REN's verb flags are frozen (`install --describe-inputs` output agreed).
3. **V-2** (VM: cgprofiled/toposd install from a host profile through H1g on a fresh guest), then
   **M1**, then **REL** (cli-extended 0.6.0, topos 0.8.0, cgprofile 3.0.0, run-gate 25.4.0), then
   **V-3**, then **M2**.
4. **On v1001:**
   - write `/etc/mdt/host-profile.toml` (pins with sha256, `--add-user` args);
   - run `install.sh` with the profile, then `check.sh` (service doctors + `hostendpoints check`);
   - check the endpoint variables in a fresh login;
   - start the runner (R1) and do H-1, then the assay release per the endpoints design §5.

This path meets directions (1), (3), (4) and (5) at v1001 time. Direction (2) (hostcfg as the
general engine) follows in HC-1/HC-2/HS-2 with no rework of the fast path, because the services'
install verbs keep their CLI and only swap their internals.

## Unverified items to close in the WPs

`systemd-analyze verify` on staged drop-ins; `dockerd --validate` on the installed Docker;
`systemd-tmpfiles`/`systemd-sysusers` dry-run flags on trixie's systemd; pam_env handling of quoted
values and of a leading-space comment; drop-in directories for sshd, apt, journald, sudoers and grub
on a stock trixie; whether cmru's `feat!:` yields exactly 3.0.0 after the move (dry run).

## Operator rulings 2026-11-08 (override rev 1 where they differ; rev 2 design pending)
- HC-1 = **reduced core for now, more later** (plan/apply/verify/record for file, unit, sysctl, group, sysusers/tmpfiles, endpoint, check; doctor; `--root` tests; block/json_key/augeas only when a real consumer needs them).
- Failure model: **dry-run plan first + pre-images + manual `hostcfg revert <txn>`; no automatic rollback** (a failed apply stops and says what to do; by design).
- **COUPLING CORRECTION (supersedes H-4 and C.5 contract):** `host-setup` must be a STANDALONE tool that can be called or used as a library to realize a desired configuration, including DELETION of a setting/file (state = absent). It must not call into services through a `[vbpub.service_installer]` entry point or any per-service contract. Services install themselves (their own install/uninstall/doctor verbs, config in /etc, wizard) and may use hostcfg as a library or hand it a request: "I need a socket with this config at this place." The `[vbpub.service_installer]` entry point and `install --describe-inputs` as a host-setup contract are DROPPED; who orchestrates a multi-service host profile is an open question for rev 2 (no coupling in hostcfg itself).
- Augeas: **not decided to adopt.** Requirements for in-place edits of foreign files are not yet gathered (only daemon.json and /etc/environment are known shared files); rev 2 must produce the requirements catalogue and a build/borrow decision table (Augeas, libelektra [archived], Ansible-style modules, own small providers) before any commitment.

## Operator rulings 2026-11-08 (round 4, on hostcfg rev 2)
- sysctl: per-owner files (not one combined file) BUT SHADOWING IS AN ERROR: plan computes the effective order (`systemd-sysctl --cat-config` order or the file-name order) and refuses when a key we set is overridden by a later file or when we override another owner's key, unless the resource states the acceptance explicitly (for example `accept_shadow = [...]`). The principle generalises to every layered setting (systemd directives in drop-ins, env variables): intention is expressed in the request, silent shadowing counters it.
- takeover: operator objects to ceremony: we usually just want to change an existing file (fstab, an apt file). Direction (to be built in HC-1a fix): NO takeover flag; an existing unowned file is replaced/edited, the origin is saved (pre-image + origin layer, so withdraw restores it) and the plan shows the diff; acceptance = the TTY prompt / --yes. Two OWNERS on one whole file/unit still refuse (accepted earlier).

## Operator rulings 2026-11-08 (round 5: philosophy)
- hostcfg is a GUARDED APPLIER: it receives a change, applies it, and recognizes problems (syntax, duplicates, shadowing, permissions, missing exec paths); the value over `sed`/appended lines is format-aware ("lens") editing plus guards. It does NOT track owners or history. State kept = per-apply journals/pre-images for manual `hostcfg revert <txn>` only. There is NO tool-level uninstall: the caller decides what to apply again at an uninstall event; clean uninstall only works for OWNED resources (deleting a socket/unit/own file). Operations on SHARED files/settings are by design problematic/undecidable: document that. (Supersedes the ownership stack, origins and 'remove --owner' restore semantics of rev 1/rev 2.)
- /etc/environment: guarded provider, any caller; no single exclusive writer (the endpoints module is a specialised front for that format).
- Augeas direction (operator): take Augeas as the lens ecosystem and extend it (our changes as upstream PRs; own fork if needed) to stay in ONE ecosystem. Feasibility evidence being gathered (reports/augeas-extension-FEASIBILITY.md); hostcfg's apply/guard/verify/journal layer is built around it regardless.
- Lost profiling data: a separate outcome PROFILING_INVALID (the regular evidence may still exist as in a non-profiling run; only the profiling data is lost; the workload verdict is kept).

## Operator rulings 2026-11-08 (round 6, after reports/augeas-extension-FEASIBILITY.md)
- Augeas = the lens ecosystem via `augtool` on stock trixie 1.14.1 (`augtool -I <overlay dir>`); JSON stays the ONE documented exception (stdlib `json_key` provider, duplicate-key refusal, canonical output). Overlay lenses ship WITH THE HOST-SETUP TOOL, not cli-extended (open: where the engine itself lives, see question to the operator). NO upstream PRs now (assumed to be ignored; larger Augeas modifications wanted anyway); a real fork stays a future option. First real `edit` request driving the build: /etc/fstab data and swap lines (div2 edits these with its own code today). `augeas-tools` is installed with the debian-install-v2 base packages on every host. Overlay lenses need an `augparse` check in CI (augtool does not type-check).
