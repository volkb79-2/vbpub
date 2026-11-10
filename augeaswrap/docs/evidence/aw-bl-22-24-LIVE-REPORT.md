<!-- origin: 2026-11-06; status: evidence; copied from the controller program dir 2026-11-10 -->
<!-- source: reports/aw-bl-22-24-LIVE-REPORT.md -->
<!-- relation: augeaswrap live evidence; verbatim source text follows -->

# augeaswrap AW-BL-22 / 23 / 24 live smoke on v1001 (2026-11-09)

Verdicts: **AW-BL-22 PASS**, **AW-BL-23 PASS (code assumption partly dead, still correct)**, **AW-BL-24 FAIL (blocking: the `edit` records path cannot work against the real augtool; the fake runner hid two independent defects)**.

Host: v1001 (server-id 804027, identity-checked on every call), Debian trixie, 4 cores / 8 GB, host Docker 29.9.3.
Repo under test: vbpub `main` at `25f571124` (`augeaswrap/src`, copied read-only into a container; the repo was NOT edited).
Raw outputs (untrimmed): `<controller-program-dir>/reports/aw-bl-22-24-evidence/` (file names given per item).

## Isolation and cleanup
- Host-sat queue paused with `host-sat-queue.disabled.v1001` at 16:42Z, waited for the in-flight `sat-fake-integration` lane to end (`v1001.current` gone), flag REMOVED at the end. (A `host-sat-queue.disabled.r1002` flag appeared meanwhile; it is not mine and I left it.)
- Only my own objects were created: containers `aw-smoke-aug`, `aw-smoke-dock`, `aw-smoke-dockce`, `aw-smoke-sdbuild`, `aw-smoke-sd`; image `aw-smoke/trixie-systemd:1`; the `debian:trixie` image (not present before; I pulled it); dir `/var/tmp/aw-smoke-20261109`. All were removed by exact name/tag; verified: 0 `aw-smoke*` containers, work dir gone, no `aw-*` units. No `--privileged`, `--cgroupns=host`, `--pid=host`, `--net=host`, no loop devices, no host package installs, no docker/systemd config touched. Peak host memory about 1 GB used.
- Containers ran with `--memory=2g`; no daemon was started in any docker-validate container (policy-rc.d denied service starts; no dockerd/containerd process and no socket afterwards).
- The systemd container ran with ONLY `--security-opt writable-cgroups=true --cgroupns=private --tmpfs /run /run/lock /tmp --stop-signal SIGRTMIN+3` (`Privileged=false`, `CapAdd=[]`). `docker inspect`: `priv=false cgns=private caps=[]`. systemd reached `running` as PID 1 with `/proc/self/cgroup` = `0::/init.scope` (private cgroup namespace). This is the minimum-privilege recipe that works on Docker 29.9.3 and is worth recording for other lanes.

## AW-BL-22 dockerd --validate: PASS
Evidence: `aw-bl-22-dockerd-validate-docker-ce-29.9.3.out`, `aw-bl-22-dockerd-validate-docker.io-26.1.5.out`.
Tools (both in disposable `debian:trixie` containers, daemon never started):
- docker-ce `5:29.9.3-1~debian.13~trixie` (same build as the host; `Docker version 29.9.3, build 70efc0e`; from download.docker.com via the host's docker.sources, read-only bind)
- docker.io `26.1.5+dfsg1-9+deb13u1` (`Docker version 26.1.5+dfsg1, build 411e817`)

argv (identical to `json_key.py:293`): `dockerd --validate --config-file <staged>`. Results identical on both builds except message wording:

| fixture (staged bytes) | exit | stdout | stderr |
|---|---|---|---|
| `{"log-driver":"json-file","log-opts":{"max-size":"10m","max-file":"3"},"live-restore":true}\n` | 0 | empty | `configuration OK` |
| `{}\n` | 0 | empty | `configuration OK` |
| `{"no-such-key":true}\n` | 1 | empty | `unable to configure the Docker daemon with file <f>: the following directives don't match any configuration option: no-such-key` |
| `{"live-restore":` (truncated) | 1 | empty | 26.1.5: `...: unexpected end of JSON input`; 29.9.3: `...: invalid JSON: unexpected end of JSON input` |
| `{"live-restore":"yes please"}` | 1 | empty | `...json: cannot unmarshal string into Go struct field Config.CommonConfig.live-restore of type bool` |
| `{"log-level":"loud"}` | 1 | empty | `...merged configuration validation from file and command line flags failed: invalid logging level: loud` |
| `[]` | 1 | empty | `...cannot unmarshal array into Go value of type map[string]interface {}` |
| missing file | 1 | empty | `unable to configure the Docker daemon with file <f>: open <f>: no such file or directory` |

Both outcomes proven (valid accepted exit 0, invalid refused exit 1). The seam `json_key.py:292-300` (exit code via `shadow.command_error`, first stderr line as detail) works unchanged: errors are on stderr, and the first stderr line is the full diagnostic.
Limits of the validator to document in SPEC (not defects): validation is shallow. These pass with exit 0: empty file (0 bytes), duplicate keys (`{"debug":true,"debug":false}`), `{"bip":"not-a-cidr"}`, `{"log-driver":"json-file","log-opts":{"bogus-opt":"1"}}`, and `{"hosts":["fd://"]}` (this last one conflicts with a `-H` flag in the packaged unit only at daemon start, which `--validate` cannot see). Note that success text `configuration OK` is on stderr, stdout is empty.
Unverified: validation as a non-root caller; behaviour of the live daemon on a config that passes `--validate` (out of scope by instruction).

## AW-BL-23 systemctl show semantics: PASS
Evidence: `aw-bl-23-systemctl-show.out`.
Environment: systemd `257 (257.13-1~deb13u1)` (trixie), real systemd as PID 1 inside the unprivileged container above (`is-system-running` = `running`), not a QEMU guest (see Unverified).
argv (identical to `transactions.py:745-751`): `systemctl show -p ActiveEnterTimestampMonotonic,ActiveState,LoadState <unit>`. Output ORDER is the manager's property order (LoadState, ActiveState, ActiveEnterTimestampMonotonic), not the requested order. Lines are `Name=value`, no trailing spaces.

| unit state | exit | stdout | stderr |
|---|---|---|---|
| active (`Type=simple`) | 0 | `LoadState=loaded` `ActiveState=active` `ActiveEnterTimestampMonotonic=85558340585` | empty |
| active (oneshot, RemainAfterExit) | 0 | loaded / active / non-zero timestamp | empty |
| inactive, never started | 0 | loaded / inactive / `...=0` | empty |
| inactive after a clean stop of a unit that was active | 0 | loaded / inactive / **`...=0`** (the old timestamp is cleared) | empty |
| failed (`ExecStart=/bin/false`) | 0 | loaded / failed / non-zero timestamp | empty |
| activating (oneshot running) | 0 | loaded / activating / `...=0` | empty |
| masked | 0 | `LoadState=masked` / inactive / 0 | empty |
| unit file with syntax error | 0 | `LoadState=error` / inactive / 0 | empty |
| **missing** `aw-missing.service` | **0** | `LoadState=not-found` `ActiveState=inactive` `ActiveEnterTimestampMonotonic=0` | **empty** |
| missing without suffix (`aw-missing`) | 0 | same as missing | empty |
| unknown suffix (`aw.nosuchtype`) | 0 | same as missing | empty |
| two units (active, missing) | 0 | two blocks separated by one blank line | empty |
| `-p Bogus` | 0 | empty | empty |

Missing-unit answer for the backlog question: **the command SUCCEEDS** with `LoadState=not-found`, `ActiveState=inactive`, `ActiveEnterTimestampMonotonic=0`, empty stderr. There is no "Unit X could not be found." diagnostic for `show` on systemd 257. (That string does not appear for `show`; `systemctl restart -- aw-missing.service` instead exits 5 with `Failed to restart aw-missing.service: Unit aw-missing.service not found.`.)
`restart` advances the timestamp (start 85559624351 -> restart 85560676553), so the `active_enter > created` comparison works on a real manager. Non-root callers get identical output.

Other operational failures stay distinguishable (all exit 1, empty stdout, diagnostic on stderr):
- invalid name `bad name!.service`: `Invalid unit name "bad name!.service" escaped as ...` plus `Failed to get properties: Unit name bad\\x20name!.service.service is neither a valid invocation ID nor unit name.`
- empty unit: `Failed to mangle name: Invalid argument` / `Failed to expand names: Invalid argument`
- systemd installed but not PID 1 (second container): `System has not been booted with systemd as init system (PID 1). Can't operate.` + `Failed to connect to system scope bus via local transport: Host is down`
- `--user` without a session bus: `Failed to connect to user scope bus via local transport: $DBUS_SESSION_BUS_ADDRESS and $XDG_RUNTIME_DIR not defined ...`
- `-M nosuchcontainer`: `Failed to connect to system scope bus via machine transport: Host is down`
- unit name starting with `-` and no `--`: `systemctl: invalid option -- 'w'` (exit 1); with `--` it works and returns the not-found triple.
- binary absent / hang are Python-level (`OSError` / `TimeoutExpired`), not exit codes; not simulated here.

Consequences for `transactions.py:706-830` (`pending_actions`):
1. The branch at `transactions.py:761` (`returncode != 0 and diagnostics == ["Unit X could not be found."]`) is DEAD on systemd 257: a missing unit takes the success path. It is harmless (fail-closed otherwise) but the fake-runner test that exercises it models a behaviour that real systemd 257 never exhibits. Keep it only as a compatibility branch for older systemd and say so in a comment, or drop it.
2. The success path at `transactions.py:797-830` handles the real not-found triple correctly (`load_state == "not-found"`, `inactive`, `0` -> `unit_state="missing"`). PASS as is. Recommend adding a test whose fake output is exactly the stdout above (property order LoadState, ActiveState, ActiveEnterTimestampMonotonic) so the contract is pinned to real bytes.
3. `LoadState=masked` and `LoadState=error` currently collapse to `unit_state="inactive"` (only `not-found` becomes "missing"). Decide if a masked/broken unit should be reported as such; the data is available.
4. Add `--` before the unit in the query argv (`transactions.py:745-751`): today a dash-leading unit name makes `systemctl` fail with "invalid option" (fail-closed refusal, but with a misleading message).

## AW-BL-24 Augeas / findmnt: FAIL (blocking defects in `edit`), spelling and validator facts recorded
Evidence: `aw-bl-24-augtool-spelling.out`, `aw-bl-24-augtool-records.out`, `aw-bl-24-augtool-save-failure-detail.out`, `aw-bl-24-findmnt-verify.out`, `aw-bl-24-edit-plan-REAL-augtool-UNPATCHED.out`, `aw-bl-24-edit-plan-REAL-augtool-EVIDENCE-PATCHED.out`, `aw-bl-24-evidence-only-patch.diff`.
Tools (disposable `debian:trixie` container, `Debian 13.7`): `augeas-tools 1.14.1-1.1~deb13u1` (`augtool 1.14.1`), `augeas-lenses 1.14.1-1.1~deb13u1`, `libaugeas0 1.14.1-1.1~deb13u1`, `util-linux 2.41.5-0+deb13u1` (`findmnt from util-linux 2.41.5`).
Exact argv used for every augtool run (copy of `augtool.py:run`): `augtool --root=<root> --noautoload --nostdinc -I <empty lenses dir> -I /usr/share/augeas/lenses/dist -t 'Fstab incl "/etc/fstab"' [--new] -f <commands.aug>`. Fixture (staged bytes):
```
# /etc/fstab: static file system information.
#
# <file system> <mount point> <type> <options> <dump> <pass>
UUID=aaaaaaaa-0000-0000-0000-000000000001 /     ext4  errors=remount-ro,noatime 0 1

# swap lives here
/dev/null  none  swap  sw  0  0
LABEL=data /srv  xfs   defaults,nofail,x-systemd.device-timeout=5  0  2
```

### 24a `-t` spelling: PASS (nothing to change)
`-t 'Fstab incl "/etc/fstab"'` -> exit 0, tree loaded. `-t 'Fstab.lns incl "/etc/fstab"'` -> exit 0, identical tree. Lower-case `fstab` also accepted. A non-existent lens (`Nonexistent`) exits **0** and only writes `/augeas/load/Nonexistent/error = "Can not find lens Nonexistent.lns"`, which the trailing `print /augeas//error` surfaces and `_error_text` (`augtool.py:83`) turns into a refusal. Pin: `AUGTOOL_TRANSFORM_LENS_SPELLING = "module"` (`augtool.py:19`) is supported and can stay; `"lns"` is equally valid on 1.14.1. Update the comment at `augtool.py:17-18` (smoke done, both accepted).

### 24b Record labels: FAIL against the code's assumption
- Real `print /files/etc/fstab` labels existing records `1`, `2`, `3` (lens `seq`), and `match /files/etc/fstab/*` returns them AND the `#comment[n]` nodes. There is NO `01[n]` label on a loaded tree. The code and all fake trees assume `01[1]`, `01[2]`, ... (`edit.py:271-286`, `edit.py:412-415`, tests/test_edit.py fixtures).
- The label `01` IS valid for a NEW node: `ins 01 after "/files/etc/fstab/3"` then `set "/files/etc/fstab/01[1]/..."` saves correctly (the NEW record gets label `01` in the working tree, and is a plain record `4` on the next load). So the "new record gets label `01`" claim is confirmed, but only as the insertion idiom; it is never what a re-observation returns.
- Effect on the real plan path (UNPATCHED code on real augtool, scenarios S1-S10): EVERY records plan, including the empty-file case and the unchanged case, is refused with `planning edit failed: records sequence has an unsupported node label` (`edit.py:412-415` via `_sequence_index`, which only accepts `01`).

### 24c Value quoting: FAIL (second defect, found by fixing the first)
`augtool print` quotes string values (`/files/etc/fstab/1/file = "/"`), whereas `match` does not. `tree_values` (`augtool.py:166-190`) keeps the quotes, so `_record_values` never equals the desired item and `found` is always empty. With ONLY the label defect fixed, the planner never converges: `edit.py:482` `while True:` appends a new record on every iteration, unbounded (observed: S1, S4-S8 did not terminate in 40 s; I had to kill it inside my container). Needs both an unquote step AND a hard iteration bound in the `while True` loops (`edit.py:482`, `edit.py:498`) so a mismatch refuses instead of looping.

### 24d Options containing `=`: FAIL (third defect)
The Fstab lens stores `name=value` as `opt[n] = "name"` plus a child `opt[n]/value = "value"` (e.g. `x-systemd.device-timeout=5`: `opt[3]` + `opt[3]/value = "5"`). Setting `opt[n] = "errors=remount-ro"` as one flat string makes `save` fail (exit 1):
`error: Failed to execute command` / `saving failed (run 'errors' for details)`; the lens message says `opt` must match `/[^\t\n #,=]+/`.
Consequences: (1) `_set_item_commands` (`edit.py:286-296`) must split an option at the first `=` into `opt[n]` and `opt[n]/value`; (2) `tree_values` drops depth-3 nodes (`len(relative) != 2`), so existing `name=value` options compare as bare `name`, and the record is always seen as "changed"; it must fold `opt[n]/value` back into `name=value`; (3) `formats/fstab.py` `swap_item` always emits `pri=N` and mount options like `errors=remount-ro`, `subvol=@`, `x-systemd.*` are the norm, so the SPEC sentence "each `opt` list element is one option and may not contain a comma" (SPEC.md ~line 523) is true but incomplete: `=` is legal in the helper input and is the common case.

### 24e Save-failure diagnostics lost
`edit_script` (`augtool.py:162-165`) prints `/augeas//error` only BEFORE `save`. When `save` fails, the useful detail (`/augeas/files/etc/fstab/error = "put_failed"` with `.../message` naming the failed pattern) is not printed, and the refusal only says "saving failed (run 'errors' for details)". Verified: appending a second `print /augeas//error` AFTER `save` prints the detail even though `save` exits 1 (augtool continues the script). Add it.

### 24f Repeated/ordered `opt`, unchanged tree, comments (raw augtool, exact script shapes of edit.py): PASS
- Ordered AND repeated `opt` survive `--new`: set `opt[1]=noatime`, `opt[2]=nofail`, `opt[3]=noatime` -> `.augnew` line `UUID=bbbbbbbb-0000-0000-0000-000000000002<TAB>/data<TAB>ext4<TAB>noatime,nofail,noatime<TAB>0 2` (appended as the last line; `rm .../opt` on a record that has none prints `rm : ... 0` and is harmless).
- Unchanged tree (`load`, `save` with `--new`): exit 0, **no `.augnew` created**, original untouched. A no-op `set` to the existing value (`set .../3/vfstype "xfs"`): also no `.augnew`. Matches the SPEC ("missing `.augnew` is a no-op").
- Editing a selected record (replace record `3` by `ins 01 after` + `set` + `rm`): `.augnew` diff is exactly that one line; the 3 comment lines, the blank line, the `# swap lives here` comment and the other 2 records are byte-identical. Cosmetic: the re-emitted record loses its column alignment (`LABEL=data<TAB>/srv<TAB>xfs<TAB>noatime<TAB>0 2`), because the lens re-prints a rebuilt node with tabs. Expect noisy diffs for replaced records.
- New record in an EMPTY file produces a leading blank line (`+` empty line, then the record), comment-only file works.

### 24g End-to-end proof that the three defects are the whole story (EVIDENCE-ONLY patch on a throwaway copy; repo untouched)
A copy of `augeaswrap/src` plus `cli_extended` in the container, patched as in `aw-bl-24-evidence-only-patch.diff` (accept numeric labels and always use `01[1]` for the new node; unquote `print` values and fold `opt/value`; split `k=v` on set). Then real `plan()` against real augtool:
- S1 new mount (plain opts), S2 new mount with `errors=remount-ro`, S3 swap_op with `pri=5`, S5 change an existing record, S6 empty file, S7 comments-only, S8 two new mounts: all terminate with a correct unified diff (S3 exclusive swap replaced `/dev/null` swap as designed).
- S9/S10 item identical to an existing record (including a `=` option): `has_changes: False`, action `noop`.
This patch is a proof of feasibility, not a reviewed fix. It does not add the iteration bound or the `--` / diagnostics changes.

### 24h `findmnt --verify --tab-file <staged>` (util-linux 2.41.5): PASS for documentation, with two seam problems
Device presence was SIMULATED in the container (see Unverified): a block-special node `/dev/aw-blk0` (b 7:250, stat-level only) plus udev-style symlinks `/dev/disk/by-uuid/<uuid>` and `/dev/disk/by-label/data` pointing at it. Exit codes and findings:

| staged line | exit | findings |
|---|---|---|
| `/dev/aw-blk0 /srv ext4 defaults 0 2` (present path) | 0 | `[W] cannot detect on-disk filesystem type (Operation not permitted)` (container artifact) |
| `UUID=<present> ...`, `LABEL=data ...` (present) | 0 | same single warning |
| `/dev/aw-absent0 /srv ext4 defaults 0 2` | **0** | `[W] unreachable source: /dev/aw-absent0: No such file or directory` + fs-type warning |
| `UUID=<absent> /srv ext4 defaults 0 2` | **1** | `[E] unreachable on boot required source: UUID=99999999-...` |
| `LABEL=nolabel ...` | **1** | `[E] unreachable on boot required source: LABEL=nolabel` |
| same UUID with `nofail` or `x-systemd.device-timeout=5` | **1** | still `[E]`: `nofail` does NOT downgrade it |
| swap line with absent `/dev/...` | 0 | two `[W]` |
| swap line with absent `UUID=...` | 1 | `[E] unreachable on boot required source` |
| mountpoint directory missing (`/mnt/does-not-exist`) | **1** | `[E] unreachable on boot required target: No such file or directory` |
| unknown fs type `zzzfs` | 0 | `[W] zzzfs seems unsupported by the current kernel` |
| relative target `srv` | 0 | `[W] non-canonical target path (real: /srv)` |
| same target twice | 0 | `[W] target specified more than once` |
| `proc /proc proc defaults 0 0`, empty file, comment-only file | 0 | `Success, no errors or warnings detected` |
| parse errors (3 fields only; non-numeric dump; non-numeric passno; any line `x`) | **139 (SIGSEGV)** | stderr `findmnt: <f>: parse error at line 1 -- ignored`, stdout empty, core dumped |
| missing tab file | 1 | `findmnt: can't read <f>: No such file or directory` |
| tab file is a directory | 0 | `Success, no errors or warnings detected` |

Findings that refuse the plan under the current rule (`edit.py:765-780`: any non-zero exit refuses): absent `UUID=`/`LABEL=` source (even with `nofail`), missing mountpoint directory, and any parse error. Absent plain `/dev/...` paths and every `[W]` finding pass (exit 0; this util-linux does not use the documented exit 64 for warnings-only here). Warnings never change the exit code in these runs.
Seam problems (`edit.py:765-780`):
1. The refusal text is built as `(result.stderr or result.stdout).strip()`. findmnt prints the findings on STDOUT and a summary line (`0 parse errors, 1 error, 0 warnings`) on STDERR, so the refusal says only the count and drops the `[E] ...` line. Include stdout for `findmnt` (or both streams).
2. util-linux 2.41.5 **segfaults (exit 139; Python sees returncode -11)** on a tab-file parse error instead of reporting it. The existing "non-zero refuses" rule still refuses, but the message would be `validator exited -11: findmnt: <f>: parse error at line 1 -- ignored`; fine, yet worth a documented note and a regression test so nobody "fixes" the check to accept a signal death. Report it upstream if not already known (I did not file anything).
3. Behavioural consequence for adopters: a new mount whose mountpoint directory does not yet exist, or whose `UUID=`/`LABEL=` device is not present at plan time, is REFUSED. Typical flows (partition a disk, then fstab) must create the directory/device before `plan`, or the spec must define a way to skip/relax (e.g. an explicit `validate = "none"` with the existing `skipped(root)` style). State this in SPEC and CONSUMERS.

## Required augeaswrap changes (summary, with seams)
1. `edit.py:271-286, 412-415`: accept numeric sequence labels; the new node is `01[1]` of the freshly loaded tree.
2. `augtool.py:166-190` (`tree_values`): unquote `print` values; fold `opt[n]/value` into `name=value`.
3. `edit.py:286-296` (`_set_item_commands`): split `name=value` into `opt[n]` + `opt[n]/value`; update SPEC.md ~523 and `formats/fstab.py` docs.
4. `edit.py:482, 498`: bound the `while True` re-observation loops (e.g. max records + 2) and refuse with `edit.augtool.failed` when the item is not found after an insertion. Without it any parse mismatch appends records forever.
5. `augtool.py:162-165` (`edit_script`): print `/augeas//error` after `save` too.
6. `edit.py:765-780`: include findmnt stdout findings in the refusal; document the UUID/LABEL/mountpoint-must-exist consequences; keep non-zero (including signal deaths) as refusal.
7. `transactions.py:745-751`: add `--` before the unit; comment/test that the not-found diagnostic branch at `:761` is dead on systemd 257; add a real-bytes test for the not-found triple.
8. Test strategy: the fake trees in `tests/test_edit.py` (`fstab_tree`, `01[n]` rows, unquoted values, flat `opt=`) encode the wrong contract. Add at least one tool-backed test lane (container with `augeas-tools`, skipped when absent) that runs S1-S10 above, so the contract is anchored to real output. Re-run this smoke after the fix as the closing evidence for AW-BL-24; the item should stay OPEN until then.
9. `AUGTOOL_TRANSFORM_LENS_SPELLING`: keep `"module"` (verified on augeas-tools 1.14.1); update the stale "still has to verify" comment.

## Unverified and why
- AW-BL-23 ran with real systemd 257 as PID 1 in an unprivileged container, not in a QEMU guest. Userland behaviour of `systemctl show` is the same, but a booted guest was not exercised (the VM harness runner needs the repo, a cloud image download and a TCG boot; the container met the stated acceptance form). Monotonic timestamps are the host's CLOCK_MONOTONIC (no time namespace), so the absolute value differs from a guest's; only ordering was checked.
- AW-BL-24 device presence for `UUID=`/`LABEL=` was simulated with a stat-level block node and udev-style symlinks; real blkid probing of a real filesystem (cache, udev database) was not exercised, and "cannot detect on-disk filesystem type" is a container artifact. Behaviour of `findmnt --verify` with real partitions under a booted guest is untested.
- The `findmnt` SIGSEGV on parse errors was observed once per input (4 inputs, 4 crashes) only in this container; not reproduced on the bare host (by instruction) or on another util-linux version.
- Not tested: `augeaswrap apply`/commit of an `edit` resource (only `plan`, since commit is the same staged bytes), `edit` with `create=true`, other lenses (`sshd`, `sudoers`, `systemd` validators), augeas behaviour with CRLF, non-UTF8 or very large fstab files.
- No fix was applied to the repo, nothing was filed in a backlog, no tool bug was reported upstream.
