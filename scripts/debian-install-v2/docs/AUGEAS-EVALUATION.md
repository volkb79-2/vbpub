# Augeas evaluation for scripts/debian-install-v2

**Status: evaluated 2026-10-06, NOT adopted.**
**Revisit: test as an alternative some time (operator note).**

Tags: VERIFIED = observed here (repo reads, devcontainer probes, Debian source/package web pages fetched today). FROM-DOCS = upstream docs / my knowledge of Augeas, NOT executed here.

Environment (VERIFIED): devcontainer is Debian 13; `dpkg -l | grep augeas` empty, `import augeas` -> ModuleNotFoundError, no `/usr/share/augeas/lenses`, no `augtool`. No packages installed (per instructions). **None of the code sketches below were executed.** Lens facts come from the trixie source tree (augeas 1.14.1-1.1~deb13u1, fetched from sources.debian.org).

## 0. How v2 writes config today (VERIFIED)
- `actions.write_file(path, content, mode)`: atomic (temp file in same dir + fsync + chmod + os.replace), absolute-path/`..` guard, records `PlannedAction`, and in dry-run stores content in `dry_run_writes[path]`. Whole-file only.
- Whole-file / drop-in writes (installer.py): `/etc/apt/sources.list.d/debian.sources` (deb822, full replace, old file copied to a backup), `apt.conf.d/custom.conf`, `51-vbpub-unattended-upgrades`, `20auto-upgrades`, `preferences.d/debian-priorities`, `journald.conf.d/99-vbpub-v2.conf`, `sysctl.d/99-vbpub-swap.conf`, `modules-load.d/vbpub-zstd.conf`, `oomd.conf.d/vbpub.conf`, `fstrim.timer.d/vbpub-daily.conf`, `needrestart/conf.d/vbpub.conf`, all `/etc/systemd/system/*.service|timer`, `/etc/vbpub/*`.
- Read-modify-write in Python (the only true in-place edits):
  1. `/etc/fstab` (`_persist_fstab`, ~l.1474): drop every line whose 3rd field is `swap`, append new `PARTUUID=... none swap sw,pri=N[,discard=once] 0 0` lines, rewrite whole file. Line-oriented split(), no regex.
  2. `/etc/docker/daemon.json`: `json.load`, merge dict, `json.dumps` (already a proper structured editor).
  3. `/root/.ssh/authorized_keys` (l.1644/1677): text concatenation.
- NOT present in v2 (VERIFIED by grep): any sshd_config edit, `/etc/default/grub` edit, `sed -i`, or regex substitution of distro files. The issue-list in the brief (sshd, grub) is hypothetical for v2 today.
- Legacy `scripts/debian-install/` (VERIFIED): `configure-apt.sh:66` `sed -i 's/^deb/#deb/g' /etc/apt/sources.list` (the only real distro-file sed); fstab handled by `grep -q || echo >>` (appends) and an awk filter + `mv` to drop swap lines (create-swap-partitions.sh ~l.946-955, setup-swap.sh ~l.891). Other sed use is for parsing command output, not config.

## 1. Which writes would benefit
Augeas pays only for in-place edits of files the distro owns where just some keys change. In v2 that set is tiny.

| File | Today | Augeas fit |
|---|---|---|
| /etc/fstab | python line filter + rewrite | Possible (lens `fstab.aug`, VERIFIED present in trixie source). Benefit modest: the python code is 8 lines and already correct. Augeas adds structured `*[spec='PARTUUID=..']`, comment/blank preservation (python also preserves them). Marginal. |
| sshd_config | not touched | Don't edit in place. Trixie openssh-server ships `Include /etc/ssh/sshd_config.d/*.conf` (FROM-DOCS), so write `/etc/ssh/sshd_config.d/10-vbpub.conf` whole (first-match-wins, so a low number sorts first). Lens `sshd.aug` does cover both sshd_config and sshd_config.d/*.conf (VERIFIED) but no need. |
| journald.conf | whole drop-in 99-vbpub-v2.conf | Keep drop-in. Also: `journald.conf` is NOT in the systemd lens filter (VERIFIED: filter has logind.conf, units, networkd only), so Augeas would need a custom `transform`. |
| /etc/default/grub | not touched | If ever needed: `shellvars.aug`/`grub.aug` exist (VERIFIED), but prefer `/etc/default/grub.d/*.cfg` drop-in (Debian's update-grub sources it; FROM-DOCS). |
| apt sources | whole deb822 file | Keep whole. `aptsources.aug` only handles one-line `sources.list` + `sources.list.d/*` in old format (VERIFIED filter lines 62-63); there is NO deb822 lens in the trixie lens list (VERIFIED, list: aptconf, aptpreferences, aptsources, ... no deb822). Trixie default is `debian.sources` deb822, so Augeas cannot edit it. |
| apt preferences, apt.conf.d | whole drop-ins | Keep. Lenses `aptpreferences.aug`, `aptconf.aug` exist (VERIFIED) but whole-file is simpler. |
| sysctl.d, modules-load.d, tmpfiles.d, oomd.conf.d, systemd units/drop-ins, needrestart | whole files | Keep whole. Drop-ins are the distro-native mechanism and need no parsing. |
| docker daemon.json | json merge | Keep Python json. (`json.aug` exists but is a worse tool than the json module.) |
| authorized_keys | text | Not Augeas (no lens; line-set logic is trivial). |

Verdict: only fstab is a candidate, and it is the weakest case.

## 2. Availability / bootstrap ordering
- Debian 13 packages (VERIFIED on packages.debian.org): `augeas-tools` 1.14.1-1.1~deb13u1, `augeas-lenses` 1.14.1-1.1~deb13u1, `python3-augeas` 1.2.0-1 (depends on libaugeas0). Not in the default minimal image: FROM-DOCS for the netcup image (not checkable here; this devcontainer lacks them, which suggests not by default but proves nothing about netcup). The installer would have to `apt-get install --no-install-recommends augeas-tools python3-augeas` (pulls libaugeas0, augeas-lenses, libxml2) before first use.
- v2 `_packages()` runs apt-get update+install per stage. Ordering: apt is configured in stage1 (`_configure_apt`), so the package could be installed right after it, before any Augeas edit. But apt sources themselves are written whole BEFORE, so nothing in the apt step could depend on Augeas anyway. Needs the `apt-get install` allowlist entry (already allowed) and a pre-check `import augeas` fallback. Cost: one more network-dependent package set in the critical path, on hosts that are otherwise bare.
- `augtool` is not needed if python3-augeas is used; use python bindings only (`augeas-tools` optional; still handy for debugging).

## 3. Usage sketch (FROM-DOCS, not executed)
API: `import augeas; a = augeas.Augeas(root="/", flags=augeas.Augeas.NO_MODL_AUTOLOAD)`; `a.set(path, val)`, `a.get`, `a.match`, `a.remove`, `a.insert`, `a.save()`, `a.srun`/`a.text_store`. `flags=NO_LOAD|NO_MODL_AUTOLOAD` + `transform`/`incl`/`load` lets you load only one lens/file. `SAVE_NEWFILE` writes `file.augnew` so you can diff before committing. Errors surface via `a.match("/augeas//error")` (`/augeas/files/<path>/error/{message,line,char}`) not exceptions on load.

### 3a. sshd (recommended form: whole drop-in, no Augeas)
Before/after, same effect, no dependency:
```python
self.actions.write_file("/etc/ssh/sshd_config.d/10-vbpub.conf",
    "PasswordAuthentication no\nPermitRootLogin prohibit-password\n")
```
Augeas form (editing the main file in place; for contrast):
```python
aug_set("/etc/ssh/sshd_config", {"PasswordAuthentication": "no",
                                 "PermitRootLogin": "prohibit-password"}, lens="Sshd")
# -> a.set("/files/etc/ssh/sshd_config/PasswordAuthentication", "no")
```
Caveat (FROM-DOCS): in a file with `Match` blocks, new keys must be inserted before the first Match or they land inside it. Augeas `set` of a missing key appends at end, which can be wrong; the lens exposes `Match` as a node you must `insert ... before`.

### 3b. journald (keep the drop-in; shown for contrast)
Today: whole `99-vbpub-v2.conf`. Augeas would need a custom load because journald.conf is outside the systemd lens filter (VERIFIED):
```python
a.set("/augeas/load/Systemd/lens", "Systemd.lns")
a.set("/augeas/load/Systemd/incl", "/etc/systemd/journald.conf")
a.load(); a.set("/files/etc/systemd/journald.conf/Journal/SystemMaxUse", "1G"); a.save()
```
Inferior: edits the distro file (package upgrade -> dpkg conffile prompt/merge), versus an untouched drop-in.

### 3c. fstab swap entries
```python
def set_swap_entries(a, entries):                      # entries: [(partuuid, opts)]
    for n in a.match("/files/etc/fstab/*[vfstype='swap']"):
        a.remove(n)
    for i, (pu, opts) in enumerate(entries, 1):
        base = "/files/etc/fstab/01"                   # append: use '0' + counter via a.insert
        a.set("/files/etc/fstab/9%d/spec" % i, f"PARTUUID={pu}")
        a.set("/files/etc/fstab/9%d/file" % i, "none")
        a.set("/files/etc/fstab/9%d/vfstype" % i, "swap")
        a.set("/files/etc/fstab/9%d/opt[1]" % i, "sw")
        a.set("/files/etc/fstab/9%d/opt[2]" % i, "pri")
        a.set("/files/etc/fstab/9%d/opt[2]/value" % i, str(pri))
        a.set("/files/etc/fstab/9%d/dump" % i, "0"); a.set("/files/etc/fstab/9%d/passno" % i, "0")
```
(FROM-DOCS: new entries need numeric node labels; options are `opt` nodes with optional `value` children. This is noticeably more verbose and less readable than the existing 8-line Python filter + one f-string per entry.)

### Wrapper in actions.py style
```python
def augeas_set(self, path, kv, *, lens, root="/"):
    import augeas                                      # lazy; ActionError if missing
    a = augeas.Augeas(root=root, flags=augeas.Augeas.NO_MODL_AUTOLOAD | augeas.Augeas.SAVE_NEWFILE)
    a.transform(lens, path); a.load()
    err = a.match(f"/augeas/files{path}/error")
    if err: raise ActionError(f"{path}: {a.get(err[0]+'/message')} line {a.get(f'/augeas/files{path}/error/line')}")
    for k, v in kv.items():
        if a.get(f"/files{path}/{k}") != v:            # idempotent: skip equal
            a.set(f"/files{path}/{k}", v)
    a.save()                                           # writes <path>.augnew
    new = Path(path + ".augnew")
    if not new.exists(): return                        # no change
    diff = unified_diff(Path(path).read_text().splitlines(), new.read_text().splitlines(), ...)
    self.planned.append(PlannedAction(("augeas", path), diff, True))
    if self.dry_run: new.unlink(); self.dry_run_writes[path] = diff; return
    self.write_file(path, new.read_text(), mode_of(path)); new.unlink()   # reuse atomic write
```
Idempotency, diffing via `.augnew` and atomic reuse of `write_file` are all workable. Note Augeas's own `save()` replaces the file non-atomically-by-fsync (rename of temp, no fsync; FROM-DOCS) which is why routing through `write_file` is preferable.

## 4. Pros / cons
Pros (FROM-DOCS unless noted)
- Preserves comments/whitespace/order; structured, key-based edits, not regexes.
- Idempotent by construction (`set` of existing value is a no-op).
- `Augeas(root="/tmp/x")` retargets all paths under a tmp root -> unit tests can run against copies of real files without root (documented; unverified here as the lib is absent).
- Wide lens catalog (VERIFIED present: sshd, ssh, fstab, grub, shellvars, sysctl, systemd, aptsources, aptpreferences, aptconf, dpkg, limits, login_defs, modules, resolv, sudoers, json, inifile, simplevars).

Cons
- Not installed by default here (VERIFIED), so it adds 3-4 packages (augeas-tools optional, python3-augeas, libaugeas0, augeas-lenses) to a bare host and a stage1 ordering step.
- Lens gaps that hit our files: no deb822 lens (VERIFIED) so `debian.sources` can't be edited; `journald.conf` and `*.conf.d` drop-ins outside the systemd lens filter (VERIFIED); `/etc/default/grub` is shell-syntax (shellvars) where multi-quoted values like GRUB_CMDLINE_LINUX are one opaque string (FROM-DOCS).
- Failure mode: a hand-edited file that deviates from the lens grammar fails to load silently; you only see `/augeas/files/<f>/error` and a terse "Failed to match ... with tree" message with line/char (FROM-DOCS). On a fresh host running as root, mis-parse -> refuse to edit, so a hard stop needs explicit handling.
- Path-expression syntax and tree shapes (opt/value nodes, numeric labels, Match blocks) have a real learning curve; the fstab sketch is longer than the current code.
- Security surface as root: native C parser (libaugeas) on files processed as root; low risk for our own controlled inputs, but it is extra privileged native code and path expressions built from variables need escaping (injection-like bugs in the path language, FROM-DOCS). `augtool`'s `/files` tree writes with plain `save`.
- Dry-run/diff must be built (shown above), v2's `write_file` dry-run model is whole-content based.
- Dpkg-owned files edited in place cause conffile prompts on upgrade; drop-ins avoid it.

## 5. Recommendation
**Not worth adopting for v2 now.** Only one in-place edit exists (fstab swap lines), already solved correctly in 8 lines of Python, and the legacy sed/awk/grep edits it replaced are gone in v2. For future host-hardening needs:
- sshd: write `/etc/ssh/sshd_config.d/10-vbpub.conf` (whole drop-in); validate with `sshd -t` (add to allowlist).
- grub: `/etc/default/grub.d/vbpub.cfg`, then `update-grub`.
- journald/sysctl/modules/tmpfiles/systemd: keep whole drop-ins (current practice).
- apt: keep whole deb822 file (no lens).
- If an in-place edit of a file without a drop-in mechanism is ever needed (e.g. fstab, /etc/hosts), add a ~25-line `actions.set_kv(path, {key: value}, sep="=")` line-based editor that: reads, replaces the last uncommented `^key\s*=` line or appends, preserves everything else, returns a unified diff for dry-run, then goes through `write_file`. Keep `_persist_fstab` as is.
- Revisit Augeas only if v2 gains many structured edits of distro-owned non-drop-in files (e.g. sudoers, ssh_config with Match, pam). In that case install `python3-augeas` right after `_configure_apt`, and test via `Augeas(root=tmp)`; first prove the sketches in a throwaway container with the packages (not done here).
