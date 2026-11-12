# vm-lab consumer guide

## Start a Debian guest

**Every snippet in this guide runs from the repository root.** `<project>` is
the project that owns the VM (for example `debian-rollback`). Keep its VM files
in `scripts/<project>/artifacts/vm-lab/<vm>/`.

Prerequisites: a loaded host background slice (`CGROUP_PARENT_DEV_BACKGROUND`),
a Docker network (`DOCKER_NETWORK_INTERNAL`), a QEMU image that has
`qemu-system-x86_64` and `qemu-img` (`VM_LAB_QEMU_IMAGE`), the OVMF firmware
files, and a Debian installer ISO.

```
VM=scripts/<project>/artifacts/vm-lab/deb1
BASE=scripts/<project>/artifacts/vm-lab/deb1-base.qcow2
mkdir -p "$VM"

# A blank base disk. The guest writes go to an overlay in "$VM", not here.
qemu-img create -f qcow2 "$BASE" 20G

python3 scripts/vm-lab/vm_lab.py start \
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

python3 scripts/vm-lab/vm_lab.py console screendump $S --filename boot.ppm
python3 scripts/vm-lab/vm_lab.py console sendkey $S ret
python3 scripts/vm-lab/vm_lab.py console sendkey $S ctrl-alt-delete
python3 scripts/vm-lab/vm_lab.py console reset $S
python3 scripts/vm-lab/vm_lab.py console serial $S     # Ctrl-] to leave
python3 scripts/vm-lab/vm_lab.py console type $S --text "hello"
python3 scripts/vm-lab/vm_lab.py status $S
python3 scripts/vm-lab/vm_lab.py stop $S
```

`console screendump` refuses to overwrite an existing file. The picture is a
PPM file in `$VM`.

After the install, `stop` the guest, then start again with `--resume` and the
same options, including the same ISO. Resume refuses if the base disk,
firmware, image, network, slice, sizing, forwards or ISO differ from the first
start. A first start without an ISO may add one on resume. `destroy` removes the stopped container and keeps the disk state.

Reach the guest from another container on the same Docker network at
`<container address>:2222`. Nothing is published on the host.

## Record a console session

Run the foreground recorder in one terminal:

```sh
VM=scripts/<project>/artifacts/vm-lab/deb1
CAP=$VM/capture
S="--name deb1 --state-dir $VM"

python3 scripts/vm-lab/vm_lab.py console record $S --out "$CAP"
```

In another terminal, use `ctl` for commands that must be recorded with the
frames, or use the direct console verbs:

```sh
CAP=scripts/<project>/artifacts/vm-lab/deb1/capture

python3 scripts/vm-lab/vm_lab.py console ctl --out "$CAP" --keys ret
python3 scripts/vm-lab/vm_lab.py console ctl --out "$CAP" --type "root"
python3 scripts/vm-lab/vm_lab.py console ctl --out "$CAP" --type-file /path/to/secret.txt
python3 scripts/vm-lab/vm_lab.py console ctl --out "$CAP" --qmp-json '{"execute":"query-status"}'
python3 scripts/vm-lab/vm_lab.py console ctl --out "$CAP" --quit
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
handlers stay in place until the guest stop has finished and `DONE` is written.

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
Two readers exist and the verbs pick one per poll (`--engine auto`):

| Reader | Reads | Needs |
| --- | --- | --- |
| `vmscreen` (exact) | The VGA text buffer of an 80+ column text mode: SeaBIOS and any boot loader that stays in text mode (a text-mode GRUB or syslinux menu). Not Linux's framebuffer console. | `--display-device std` (QEMU's standard VGA) and a guest that is in a text mode; vmscreen installed (`pip install` of `libraries/vmscreen`, an editable install is enough; it does not run from a bare source tree), as a `vmscreen` command on PATH or as a module of the Python that runs the VM tool. |
| `ocr` (recognised) | Any screen: UEFI GOP, a graphical GRUB theme, a framebuffer console, ZFSBootMenu, graphical installers. | `pwmcp` on PATH and pwmcp's OCR sidecar enabled (`pwmcp.ocr.enabled = true`). |

Choose the display device for the reader you rely on: exact text needs a VGA
text mode (BIOS boot with `--display-device std`); OCR works with either
device but a larger, plainer font reads better. OCR text is never exact, so
match a distinctive line, and treat a missing match as "unknown", not "absent".
`auto` uses vmscreen while it applies and OCR otherwise; it says once on
stderr when vmscreen cannot be started. `--engine vmscreen` and `--engine ocr`
force one reader (a forced vmscreen on a graphical screen simply sees no text).

Worked example: wait for the boot menu, pick an entry, and notice a boot loop.

```sh
VM=scripts/<project>/artifacts/vm-lab/deb1
S="--name deb1 --state-dir $VM"
VMLAB="python3 scripts/vm-lab/vm_lab.py"

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
bounded by the time left to `--timeout`, so a hung `pwmcp` still ends in exit 3
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
2. Make `scripts/vm-lab` importable by path from `__file__`, not by an
   environment variable:

   ```python
   import sys
   from pathlib import Path
   sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "vm-lab"))
   import vm_lab
   from vm_lab import PortForward, VmProfile
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
   `scripts/vm-lab/vm_lab.py`. This registry is the one list of labels that
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
change `vm_lab.py` and its tests.

## TrueNAS

TrueNAS guests are started with `scripts/truenas/tcg_vm.py`. See the
[TrueNAS guide](../../truenas/docs/CONSUMERS.md#run-a-local-truenas-tcg-guest).
