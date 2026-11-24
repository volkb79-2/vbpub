# vm-lab consumer guide

## Install, build the image, find the paths

**Every snippet in this guide runs from the repository root** and calls the
wrapper `vm-lab/vm-lab`; once the package is installed, the same verbs are the
command `vm-lab` (same arguments).

```
# 1. Pick one install form.
vm-lab/vm-lab --help                                    # bare checkout: nothing to install
/opt/mdt/venv/bin/python -m pip install -e libraries/vmscreen -e vm-lab   # editable; vmscreen first
python -m pip wheel --no-deps vm-lab -w /tmp/wheels     # a wheel; pip install it next to the vmscreen wheel
export PATH="/opt/mdt/venv/bin:$PATH"                   # the venv's bin/ holds the `vm-lab` command

# 2. Build the QEMU image once per Docker host (tag vm-lab-qemu:local; `start` finds it by that tag).
vm-lab/vm-lab image build
vm-lab/vm-lab image build --tag my-qemu:1                                # another tag; the wheel carries the build context too
vm-lab/vm-lab image build --context /path/to/dir                         # a different build context (override)
```

`vmscreen` (screen text) is a dependency of the package and a separate wheel;
the wrapper does not add it, so it must be installed where `python3` runs.
`console record` also needs Pillow, which is the optional extra `record`:
`pip install 'vm-lab[record]'` (the mdt-cockpit image installs the extra
explicitly). The image has no OVMF: pass your host's firmware files to `start`.

**From an installed wheel** there is no repository next to the module, so
`start` and `disk promote` need the directory of the admission lock: set it once
for the host and every starter that should exclude the others uses the same one.

```
export VM_LAB_ADMISSION_LOCK_DIR="$(git -C /path/to/vbpub rev-parse --path-format=absolute --git-common-dir)"
vm-lab start ...                      # takes the lock file there (the shared TrueNAS/vm-lab file name)
vm-lab start ... --lock-dir "$DIR"    # or per call; the option wins over the variable
```

A checkout, the wrapper, an editable install and the TrueNAS front ignore both
(they use the repository's common Git directory). With neither given and no
repository, `start` refuses and names `--lock-dir` and the variable.

The tool owns no paths. Pass `--state-dir` and `--base-disk` explicitly, taken
from the repository's path resolver (entries declared in `cowtree.toml`) and never hardcoded:

```
VM=$(libraries/cowtree/repo-path vm-lab --project scripts/debian-rollback)/deb1
BASE=$(libraries/cowtree/repo-path vm-images)/debian-13.raw
```

`<project>` below is the project that owns the VM (for example
`scripts/debian-rollback`); the resolver gives its `vm-lab/` folder (a CoW
subvolume on CoW hosts, else `<project>/vm-lab`). Base images shared between
projects live in `vm-images` and ISOs in `iso` (AGENTS.md "Directory
conventions and VM files").

## Start a Debian guest

`start` takes the base disk in one of two formats:
a qcow2 base is mounted read-only under a qcow2 overlay (this section), a raw
base is reflink-cloned into the state directory (see
[Start from a raw base](#start-from-a-raw-base)). `--base-format auto` (the
default) tells them apart by the first four bytes of the file.

Prerequisites: a loaded host background slice (`CGROUP_PARENT_DEV_BACKGROUND`),
a Docker network (`DOCKER_NETWORK_INTERNAL`; the ciu stack environment or the
devcontainer provides it, `ciu env print` shows it), a QEMU image (`--image`,
else `VM_LAB_QEMU_IMAGE`, else the local `vm-lab-qemu:local` built by `vm-lab
image build`), the OVMF firmware files, and a Debian installer ISO.

When `vm-lab` runs outside a cockpit container, add `--plain-host` and give the
local Docker host's loaded slice with `--cgroup-parent` (or
`CGROUP_PARENT_DEV_BACKGROUND`). Plain-host mode does not inspect the calling
container or look for a separate inspector container. Use one exact guest name,
state directory and user-defined network, and run one managed guest per host.
See [Start one guest on a test host](../README.md#start-one-guest-on-a-test-host)
for the complete command, caps and exact-name teardown.

```
VM=scripts/<project>/vm-lab/deb1
BASE=downloads/vm-images/deb1-base.qcow2
mkdir -p "$VM"

# A blank base disk. The guest writes go to an overlay in "$VM", not here.
qemu-img create -f qcow2 "$BASE" 20G

vm-lab/vm-lab start \
  --name deb1 \
  --state-dir "$VM" \
  --base-disk "$BASE" \
  --ovmf-code /usr/share/OVMF/OVMF_CODE_4M.fd \
  --ovmf-vars /usr/share/OVMF/OVMF_VARS_4M.fd \
  --memory-mib 2048 \
  --cpus 2 \
  --display-device std \
  --forward 2222:22 \
  --readonly-media /path/to/debian-installer.iso \
  --serial-console
```

The state directory must be empty on a first start, so the base disk lives
beside it, not inside it.

`start` prints its admission evidence and a `[started]` line. If it refuses, it
says why (memory pressure, guest cap, missing image, and so on). Do not work
around a refusal. Lower the load or the request.

Then drive the guest:

```
S="--name deb1 --state-dir $VM"

vm-lab/vm-lab console screendump $S --filename boot.ppm
vm-lab/vm-lab console sendkey $S ret
vm-lab/vm-lab console sendkey $S ctrl-alt-delete
vm-lab/vm-lab console reset $S
vm-lab/vm-lab console serial $S     # Ctrl-] to leave
vm-lab/vm-lab console type $S --text "hello"
vm-lab/vm-lab status $S
vm-lab/vm-lab stop $S
```

`console screendump` refuses to overwrite an existing file. The picture is a
PPM file in `$VM`.

After the install, `stop` the guest, then start again with `--resume` and the
same options, including the same ISO. Resume refuses if the base disk,
firmware, image, network, slice, sizing, forwards, boot order, config disk,
CPU shares or ISO differ from the first start. A first start without an ISO may
add one on resume. `destroy` removes the stopped container and keeps the disk
state.

Reach the guest from another container on the same Docker network at
`<container address>:2222`. Nothing is published on the host.

## Start from a raw base

A raw base is a plain disk image, preferably on btrfs. Each VM gets its own
disk as a reflink copy of the base: instant, and the two share their blocks
until the guest writes. The base is never mounted into the guest container and
never written; it is only the source of one `cp --reflink=always`.

**The base image and the state directory must be on one btrfs filesystem.**
Otherwise the clone fails and `start` says so and exits 1 (it prints
`cannot reflink <base> into <state dir>: <cp's message>` and removes anything
partial). Add `--allow-full-copy` to copy the whole file instead; `start` then
prints `[disk] full copy of <base> (no reflink possible)`. The checkout itself
may be on ext4: give the base and the state directory as explicit paths on the
CoW filesystem.

```
# 1. Convert a fetched qcow2 cloud image into a raw base, once. -S 4k keeps it sparse.
#    The base folder is read-only after creation and has a .sha256 next to each file.
mkdir -p downloads/vm-images
qemu-img convert -O raw -S 4k debian-13-generic-amd64.qcow2 downloads/vm-images/debian-13.raw
( cd downloads/vm-images && sha256sum debian-13.raw > debian-13.raw.sha256 )
chmod 0444 downloads/vm-images/debian-13.raw downloads/vm-images/debian-13.raw.sha256

# 2. Start a VM from it. The state directory is empty on a first start.
VM=scripts/<project>/vm-lab/deb1
mkdir -p "$VM"
vm-lab/vm-lab start \
  --name deb1 --state-dir "$VM" \
  --base-disk downloads/vm-images/debian-13.raw \
  --ovmf-code /usr/share/OVMF/OVMF_CODE_4M.fd \
  --ovmf-vars /usr/share/OVMF/OVMF_VARS_4M.fd \
  --memory-mib 2048 --cpus 2 --display-device std --forward 2222:22
# prints: [disk] reflink clone of .../debian-13.raw
# The guest disk is "$VM/disk.raw"; `status` shows disk_format and disk_file.

# 3. Install or provision the guest, then stop it (a running guest cannot be promoted).
vm-lab/vm-lab stop --name deb1 --state-dir "$VM"

# 4. Promote the installed VM to a new shared base for other VMs.
vm-lab/vm-lab disk promote \
  --state-dir "$VM" --dest-dir downloads/vm-images --name debian-13-installed
# prints: [promoted] .../debian-13-installed.raw sha256=<hex>
```

`--base-format auto` treats every file that does not start with the qcow2 magic
as raw. A vmdk, vhdx or compressed image is therefore taken for raw and would
boot as garbage; convert it first (`qemu-img convert -O raw`), and pass
`--base-format qcow2` when the base must be qcow2, so a wrong file is refused.

`--resume` reuses `disk.raw` and never clones again; it refuses when the
recorded format differs from the one the base file has (a raw guest cannot be
resumed with a qcow2 base). The raw guest does not need its base any more: it
resumes even if the base was renamed, replaced or deleted. A qcow2 guest does
need the same base. A raw guest runs with `cache=unsafe`: a host crash
may corrupt its disk, killing QEMU does not. Treat a run disk as disposable.
`disk promote` needs a raw disk (a qcow2 overlay guest is refused), a stopped
guest, an existing destination folder and a name made of letters, digits, `.`,
`_` and `-` that does not start with a dot. It refuses to overwrite either
file, writes `<name>.raw.sha256` (`<hex>  <name>.raw`) and sets both to mode
0444. It takes the same `--allow-full-copy` flag. `disk promote` holds the start
admission lock for the whole copy and for hashing the copied file (a read of the
whole image), so a `start` on any guest is refused as "in progress" until it
finishes.

A `disk promote` interrupted by Ctrl-C, SIGTERM or SIGHUP removes everything it
made (`<name>.raw`, `<name>.raw.sha256`, the hidden partial copy and the partial
digest) and exits 1 with `interrupted by SIGTERM; ...`; a second signal during
that cleanup is ignored.

Troubleshooting: a `start` killed by SIGTERM, or any command killed by SIGKILL
(or a host crash), can leave the hidden copy `.disk.raw.partial` in the state
directory. For a killed `disk promote` delete in the destination folder, by
hand: `<name>.raw`, `<name>.raw.sha256`, `.<name>.raw.partial` and
`.<name>.raw.sha256.partial` (whichever exist). A retry refuses to reuse a
leftover (`refusing to reuse existing ...; remove the leftover of an earlier
copy`): remove that hidden `.<name>.partial` file by hand and run the command
again. Ctrl-C during a `start` removes its partial copy itself.

## Record a console session

Run the foreground recorder in one terminal:

```sh
VM=scripts/<project>/vm-lab/deb1
CAP=$VM/capture
S="--name deb1 --state-dir $VM"

vm-lab/vm-lab console record $S --out "$CAP"
```

In another terminal, use `ctl` for commands that must be recorded with the
frames, or use the direct console verbs:

```sh
CAP=scripts/<project>/vm-lab/deb1/capture

vm-lab/vm-lab console ctl --out "$CAP" --keys ret
vm-lab/vm-lab console ctl --out "$CAP" --type "root"
vm-lab/vm-lab console ctl --out "$CAP" --type-file /path/to/secret.txt
vm-lab/vm-lab console ctl --out "$CAP" --qmp-json '{"execute":"query-status"}'
vm-lab/vm-lab console ctl --out "$CAP" --quit
```

`ctl --type-file` reads the file when the recorder executes the command; its
text is never put in the inbox and its recorded label is redacted. `--type`
with `--secret` is also redacted in `keys.jsonl`, though the text still
appears briefly in the inbox, so use throwaway values.

The recorder writes `frames/`, `manifest.jsonl`, `keys.jsonl`, `state.json`,
and `DONE`. Re-running the same `console record` command resumes numbering,
keeps the original start clock from `state.json`, and compares the first new
poll with the last kept frame. It leaves the guest running on exit unless
`--stop-guest-on-exit` is given. An explicit `ctl --quit` never stops the
guest.

### Recorder output contract

Each kept frame is `DIR/frames/NNNN_<elapsed>s.png`. Each line of
`DIR/manifest.jsonl` has the old recorder's fields and types: `sequence`
(integer), `elapsed_s` (integer seconds from the original start), `file`
(string), `sha256` (string digest of the saved PNG), `width` and `height`
(integers), `fraction_of_pixels_changed` (rounded float), and `keys_before`
(list of the `keys.jsonl` entries recorded since the previous kept frame).

`DIR/state.json` holds the real `t0_epoch`. `DIR/inbox/*.json` holds queued
commands. Inbox writes are atomic: `ctl` writes a hidden temporary file and
renames it into place. If the recorder finds a `*.json` file that does not
parse, it renames it to the corresponding `*.bad` file, prints one
`vm-lab:` line on stderr, and continues recording.

`DIR/DONE` always contains the final reason. Normal reasons are
`quit-command`, `max-runtime`, and `cap`. An error is `error: <message>`; ten
consecutive screendump or image-read failures end with
`error: 10 consecutive screen captures failed: <last message>`. SIGTERM and
SIGHUP produce `signal: SIGTERM` and `signal: SIGHUP`. A new recorder removes
an old `DONE` after validation and before its loop starts, and writes the new
one only after a requested guest stop has finished. A recorder that ends with an
error exits 1; the message is also printed on stderr as `vm-lab: <message>`.
SIGTERM and SIGHUP end the loop cleanly (temporary screendump removed, guest
stopped when `--stop-guest-on-exit` is set and the loop had started) and the
recorder exits 0. A signal that was already ignored when the recorder started
stays ignored, so a recorder under `nohup` survives the end of its shell. The
handlers stay in place until the guest stop has finished and `DONE` is written,
and from the first signal on every further SIGTERM or SIGHUP is ignored: a
second one cannot abort the guest stop (so a hung guest stop cannot be
interrupted by a second SIGTERM either: use SIGKILL). Run the recorder as the
same user that started the guest: the screendump file is created mode 0600 and
the QEMU container runs as that uid, so a different uid makes every screendump
fail and ends the recording after 10 skipped polls. Only the recorder's own temporary
screendump file is removed (a file that another process put at that name is
never deleted). If the guest stop itself fails, stderr gets
`vm-lab: stop-guest-on-exit failed: <message>`; `DONE` keeps the original reason
and the exit status stays what it would have been (0, or 1 after an error).

`--stop-guest-on-exit` takes effect only once the recording loop has started:
a recorder refused at start (existing screendump file, Pillow missing, invalid
resume state) never stops the guest. Run one recorder per guest. If the
screendump file of another recorder shows up during a run, that poll is skipped
and counted like a failed capture. A queued command whose JSON object has the
wrong shape is logged to `keys.jsonl` with `"result": {"error": ...}` and the
recording continues. When `ctl` rejects `--secret` or `--type-file` text, the
message does not name the offending character.

### Keep rule, cursor blink, and bounds

A frame is compared with the last kept frame. A pixel counts as changed when
its luma differs by more than 48. The frame is kept when the fraction of
changed pixels is at least `--diff-threshold` (default `0.00002`, about 20
pixels at 1280x800; `0.0002` missed a ticked checkbox in the old corpus).
Differences of exactly 48 luma units do not count; 49 does.

Cursor blink is suppressed using known phases, not timing. A change confined
to a `--cursor-w` x `--cursor-h` box (default 24x40) is held for one poll. If
the next poll is back on a known phase of the current screen (the last kept
frame, or a phase learned earlier), the held frame is a blink phase: it is
dropped and remembered. Otherwise the change is real and kept, and the old
frame becomes the second known phase. Once both phases are known, suppression is deterministic.
While learning them, a blink that remains on across two polls is kept once.
A persistent real change confined to one cursor-sized box is therefore kept
once, then becomes a known phase. A real change identical to an already
learned blink phase cannot be distinguished from a blink and is suppressed.

`--interval` and `--max-runtime` are positive seconds; `--max-runtime` defaults
to 14400 (4 hours). `--qmp-timeout` defaults to 30 seconds for every connect
and read. `--cap` defaults to 2000 and, together with `--cursor-w` and
`--cursor-h`, must be at least 1. Hitting the cap prints `FRAME CAP HIT`.

### Resume

Running `console record` again in the same `DIR` continues frame numbering,
uses the original real clock from `state.json`, reloads the last kept frame,
and compares the first new poll with it. Downtime therefore counts in
`elapsed_s`. Directories written by the first recorder version may have no
`state.json`; those use the old arithmetic, `now - elapsed_s - interval`, so
the restart gap is lost.

### Queued-command syntax and logs

`ctl --keys down,down,ret` sends three commands in order. A comma separates
sends and `+` joins a chord, as in `shift+a`; each name must satisfy the same
QEMU send-key syntax as `console sendkey`. An invalid name exits 2 and queues
nothing.

`--type` is validated before queueing. Text outside US-layout ASCII is
refused and nothing is queued. `--secret` redacts the text only in
`keys.jsonl`: the text is still visible in process arguments and briefly in
`DIR/inbox/<n>.json`. Use throwaway values. `--type-file` keeps the text out
of both argv and the inbox, reads the file when the recorder executes the
command, strips exactly one trailing newline, and records
`TYPE:<N chars redacted>`. Any value beginning with `-` needs the equals form,
for example `--type=-x`.

For successful key and type commands, the `sent` field in `keys.jsonl` is the
key list or `TYPE:<text>`/`TYPE:<N chars redacted>` label. A failed key
command records `{"keys": [...], "result": {"error": "<text>"}}` and a failed
type command records `{"type": "<label>", "result": {"error": "<text>"}}`
(for redacted text the error never names a character). A `--qmp-json` command
records `{"qmp": <request>, "result": {"return": <value>}}` on success or
`{"qmp": <request>, "result": {"error": "<message>"}}` on failure. Command
failures are logged and the recorder continues; they do not end the recording.
A malformed inbox file (not valid JSON, or not a JSON object) is renamed to
`.bad` as described above.

A single screendump error or an unreadable PNG skips that poll; the recorder
sleeps `--interval` and tries again. Any successful poll resets the count of
consecutive failures.

### Choosing sizes

- `--memory-mib` is guest RAM. The container limit is this plus 512 MiB.
- `--cpus` is the guest CPU count and the container CPU limit. The slice CPU
  quota must allow it.
- Up to three guests run at once on the host, counting guests of every profile
  (TrueNAS included). Use `--max-concurrent-guests 1` or `2` to be stricter.
- Memory pressure above policy refuses a start. Try again later. Do not raise
  `--memory-psi-full-avg10-limit` unless a person has judged the host.

## Wait for text on the screen

`console text` prints what the screen shows; `console wait-for-text` polls it.
Three readers exist and the verbs pick one per poll (`--engine auto`, in the order of
this table):

| Reader | Reads | Needs |
| --- | --- | --- |
| `serial` (exact) | What the guest printed to its serial console (`<state-dir>/serial.log`), with escape sequences and CR already rendered. Works for every guest whose console is on ttyS0, UEFI included (`--display-device std` under UEFI is a graphical framebuffer with no VGA text). | For `auto`: the guest started with `--serial-console` (recorded as `"serial_console": true` in `runner.json`; QEMU writes `serial.log` for every guest, so the file alone proves nothing, and `--engine serial` is the explicit choice for a guest without the record). A guest console on `ttyS0` (Debian cloud images: yes), and vmscreen installed as for the next row. Run the launcher with a Python that has Pillow only if you also use `console record`. |
| `vmscreen` (exact) | The VGA text buffer of an 80+ column text mode: SeaBIOS and any boot loader that stays in text mode (a text-mode GRUB or syslinux menu). Not Linux's framebuffer console. | `--display-device std` (QEMU's standard VGA) and a guest that is in a text mode; vmscreen installed (`pip install` of `libraries/vmscreen`, an editable install is enough; it does not run from a bare source tree), as a `vmscreen` command on PATH or as a module of the Python that runs the VM tool. |
| `ocr` (recognised) | Any screen: UEFI GOP, a graphical GRUB theme, a framebuffer console, ZFSBootMenu, graphical installers. | `pwmcp` on PATH and pwmcp's OCR sidecar enabled (`pwmcp.ocr.enabled = true`). |

Which reader for which guest:

| Guest | Use | Why |
| --- | --- | --- |
| UEFI Linux with a serial console (Debian cloud image) | `serial` (what `auto` picks) | exact; the display is graphical, so VGA finds nothing |
| BIOS guest in a text-mode boot menu | `vmscreen` | the VGA text buffer is exact; `auto` uses it for any guest not started with `--serial-console`, and while the serial log is quiet |
| A graphical screen with no serial output (installer GUI, GOP menu) | `ocr` | only a screendump exists |

The serial reader sees only what the guest wrote to the serial port: nothing
about a menu drawn on the graphical display, and nothing from a guest whose
console is not on `ttyS0`. `console sendkey`, `console type` and the recorder
send keys to the virtual keyboard, which feeds the graphical console (tty0),
not the serial login: in the 2026-11-17 run a Return sent that way left the
serial log unchanged (and the recorder kept no new frame). A log accumulates across boots and resets, so use marks.

**Marks: only text written after a point.** `console mark` prints a token for
the current end of the serial log (one line on stdout; it is vmscreen's mark, so
a log that was truncated or replaced since is refused: exit 1 with "the serial
log was truncated or replaced since the mark was taken", in every engine mode,
instead of silently re-read). `--since-mark TOKEN` on `console text` and
`console wait-for-text` limits the serial reader to text after it. A
`wait-for-text` without `--since-mark` takes an implicit mark at its start (there
is no `--since-start`): with `--engine serial` it sees only text written after the
wait began, so `login:` from the previous boot cannot satisfy it. In `auto`
that holds only while the serial log has new text: a quiet log falls through to
VGA and OCR, which read the current screen and may still show old text; use
`--engine serial` for a strict wait. While serial applies, each `auto` poll
costs one extra vmscreen call (a QMP connection). Take the mark *before* the action whose output
you wait for, because text written between the action and a later mark is lost
to the wait.

Worked example: wait for login on a UEFI guest, then reset and wait for the next boot.

```sh
VM=$MDT_COW_ROOT/vbpub/scripts/<project>/vm-lab/deb1   # a CoW subvolume; empty at first start
S="--name deb1 --state-dir $VM"
VMLAB="vm-lab/vm-lab"
$VMLAB start --name deb1 --state-dir "$VM" --base-disk "$BASE.raw" \
  --ovmf-code /usr/share/OVMF/OVMF_CODE_4M.fd --ovmf-vars /usr/share/OVMF/OVMF_VARS_4M.fd \
  --memory-mib 1024 --cpus 2 --display-device std --serial-console

MARK=$($VMLAB console mark $S)                      # the log's end right now
$VMLAB console wait-for-text $S 'login:' --since-mark "$MARK" --timeout 240
# exit 0: "localhost login:" appeared after the mark (about 40 s under TCG)
$VMLAB console text $S --engine serial              # the whole log, rendered

MARK=$($VMLAB console mark $S)                      # before the action
$VMLAB console reset $S
$VMLAB console wait-for-text $S 'Linux version [0-9]' --since-mark "$MARK" --timeout 240
$VMLAB console wait-for-text $S 'login:' --timeout 240   # implicit mark at its start
case $? in 0) echo booted ;; 3) echo "no login" ;; *) echo error ;; esac
```

With `auto` and an empty or quiet log (nothing after the mark yet) the serial
reader reports "not yet" and `auto` tries the VGA reader and OCR; if those
cannot work either, the read returns the empty serial text with one stderr note
and the wait keeps polling until `--timeout` (exit 3). Force `--engine serial`
for a wait that must never be satisfied by anything but the serial log.

Choose the display device for the reader you rely on: exact text needs a VGA
text mode (BIOS boot with `--display-device std`) or a serial console; OCR works with either
device but a larger, plainer font reads better. OCR text is never exact, so
match a distinctive line, and treat a missing match as "unknown", not "absent".
`auto` uses vmscreen while it applies and OCR otherwise; it says once on
stderr when vmscreen cannot be started. `--engine vmscreen` and `--engine ocr`
force one reader (a forced vmscreen on a graphical screen simply sees no text).

Worked example: wait for the boot menu, pick an entry, and notice a boot loop.

```sh
VM=scripts/<project>/vm-lab/deb1
S="--name deb1 --state-dir $VM"
VMLAB="vm-lab/vm-lab"

# 1. Wait for the menu. Exit 3 = never appeared, exit 4 = it panicked first.
$VMLAB console wait-for-text $S 'GNU GRUB' --timeout 180 --interval 3 \
  --fail-pattern 'Kernel panic' --fail-pattern 'No bootable device' || exit $?

# 2. Pick the second entry and boot it.
$VMLAB console sendkey $S down
$VMLAB console sendkey $S ret

# 3. Wait for the login prompt, but stop early if the guest is looping back
#    to the boot menu (the menu appearing a 3rd time ends the wait with exit 5).
$VMLAB console wait-for-text $S '^debian login:' --timeout 600 \
  --loop-pattern 'GNU GRUB' --loop-count 3 --json > "$VM/login.json"
case $? in
  0) echo "booted" ;;
  3) echo "no login prompt within 10 minutes" ;;
  5) echo "boot loop detected" ;;
  *) echo "error or fail pattern" ;;
esac
```

The loop count is of sightings *observed by polling*: a screen shown for less
than `--interval` can be missed, and the first sighting a poll observes counts
as 1. In step 3 that first sighting may be the menu from step 1 (if the first
poll still sees it after `ret`) or the first reboot, so the wait ends after two
or three menu appearances; use a larger `--loop-count` if that matters. Only
screens that were read move the count: a poll that failed or that vmscreen
could not verify leaves it unchanged. In `auto`, an OCR misreading of the
pattern can look like the menu going away and coming back; force one engine
(`--engine vmscreen`) or choose a pattern OCR reads reliably. `--literal`
matches plain text, so a menu line with regex characters needs no escaping.
Exit 2 (invalid regular expression, `--loop-count` without `--loop-pattern`) is
reported before the VM tool talks to the guest. Every external call of a poll is
bounded by the time left to `--timeout` (taken just before each call), so a hung `pwmcp` still ends in exit 3
shortly after the deadline. A `pwmcp` failure on the very first OCR call (sidecar not
enabled, or just busy) is an immediate exit 1 by design. With `--json` an exit 1
also prints `{"result": "error", "error": "..."}` on stdout.

`console ctl` queues keys for a running recorder. The recorder and these verbs
both use short QMP connections (QMP serves one client at a time), but running
them on one guest together is untested.

## Define a profile front for a project

Use a front when a project wants fixed sizing, its own names and labels, or its
own default state directory. `scripts/truenas/tcg_vm.py` is the worked example.

1. Put a small file in the project (for example `scripts/<project>/vm.py`).
2. Make `vm-lab/src` importable by path from `__file__`, not by an
   environment variable (or install the package; then these two lines are
   not needed). The functions that tests patch live in `vm_lab.core`:

   ```python
   import sys
   from pathlib import Path
   sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "vm-lab" / "src"))  # scripts/<project>/vm.py
   from vm_lab import PortForward, VmProfile
   from vm_lab import core as vm_lab
   ```

3. Define the profile. Use a label key and a state label key of your own, and
   names that mention your product:

   ```python
   PROFILE = VmProfile(
       program="vm.py",
       product="Debian rollback",
       description="Run Debian rollback test guests.",
       start_help="start a Debian rollback test guest",
       vm_label="org.vbpub.debian-rollback.vm",
       state_label="org.vbpub.debian-rollback.state-dir-sha256",
       label_value="true",
       socket_alias_prefix="debrb",
       image_env="DEBIAN_ROLLBACK_QEMU_IMAGE",
       extra_disk_drive_prefix="debrbdata",
       extra_disk_serial_prefix="DEBRB",
       readonly_media_path="/media/debian-readonly.iso",
       max_concurrent_guests=3,
       default_max_concurrent_guests=3,
       guest_memory_mib=2048,
       cpus=2,
       forwards=(PortForward(name="ssh", host_port=2222, guest_port=22),),
   )
   ```

   For command-line sizing instead, leave `guest_memory_mib`, `cpus` and
   `forwards` out and pass `sizing_from_cli=True`.

4. Add your `(vm_label, label_value)` pair to `KNOWN_VM_LABELS` in
   `vm-lab/src/vm_lab/core.py`. This registry is the one list of labels that
   start admission counts, each with its own value. Without the entry, start
   refuses your profile, because its guests would not count against the shared
   cap. This is a change to vm-lab and needs its own review.
5. Build the command line and run it:

   ```python
   def main(argv=None):
       return vm_lab.main(PROFILE, argv)

   if __name__ == "__main__":
       raise SystemExit(main())
   ```

   `vm_lab.build_parser(PROFILE)` makes `--state-dir` required. To derive a
   default state directory, pass `state_dir_defaults=(start_help, other_help)`
   to `build_parser` and fill the argument in `prepare_args=` as `tcg_vm.py`
   does.

6. Register a unit lane in the project's `run-gate.toml` and test the front
   with the vm-lab tests as a model. Do not start real VMs in unit tests.

A profile front must not duplicate launcher logic. If something is missing,
change `vm_lab.core` and its tests.

## TrueNAS

TrueNAS guests are started with `scripts/truenas/tcg_vm.py`. See the
[TrueNAS guide](../../scripts/truenas/docs/CONSUMERS.md#run-a-local-truenas-tcg-guest).
