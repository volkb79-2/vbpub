# console-capture

Records a screenshot series of a QEMU guest console through QMP `screendump`, keeping only frames
that changed, and lets you drive the guest by queuing keys. Used to build
`pwmcp/tests/corpus/console/truenas-27-vanilla/`.

## Daemon: `console_capture.py run --qmp SOCK --out DIR`

Owns the QMP socket (QMP serves one client) and polls every `--interval` s (3).

* Output: `DIR/frames/NNNN_<elapsed>s.png`; one JSON line per kept frame in `DIR/manifest.jsonl`
  (sequence, elapsed_s, file, sha256, width, height, fraction_of_pixels_changed, keys_before); every
  command sent through `ctl` in `DIR/keys.jsonl`; `DIR/state.json` (real start time).
* Bounds: every QMP connect/read has a timeout (`--qmp-timeout`, 30 s); `--max-runtime` (default 14400 s = 4 h
  since the first start) ends the run; the 2000-frame `--cap` is logged ("FRAME CAP HIT"). `DIR/DONE` is
  **always** written on exit (try/finally) and contains the reason: `quit-command`, `max-runtime`, `cap` or
  `error: ...`.
* `--own-qemu [--qemu-pidfile FILE]`: the daemon owns the guest and quits QEMU on every exit except an explicit
  `ctl --quit` (QMP `quit`, then SIGTERM of the pid in the pidfile as a last resort if QMP is dead). Without it
  the guest is left running.
* Resume: running the daemon again in the same `DIR` continues the numbering, keeps the REAL clock (from
  `state.json`, so the downtime counts in `elapsed_s`) and reloads the last kept frame from `frames/`, so the first
  frame after a restart is compared, not blindly kept. (Directories from the first version have no `state.json`;
  then the old arithmetic is used and the restart gap is lost. This happened once, see the corpus README
  "Clock note".)

### Keep rule and cursor blink

A frame is kept when the fraction of pixels whose luma differs by more than 48 from the last kept frame is
>= `--diff-threshold` (default 0.00002 = about 20 px at 1280x800; 0.0002 missed a ticked checkbox).

Cursor blink is suppressed with **known phases**, not by timing: a change confined to a `--cursor-w` x
`--cursor-h` box (24x40) is held for one poll. If the next poll is back on a known phase of the current screen
(the last kept frame, or a phase learned earlier) the held frame is a blink phase and is dropped and remembered;
otherwise it is a real change and is kept, and the old frame becomes the second known phase. Therefore:
* once both blink phases of a screen are known the suppression is deterministic;
* while they are being learned, a blink whose cursor-on phase is seen on two consecutive polls is kept once
  (at most one extra frame per screen);
* a genuine change confined to one cell that equals an already known blink phase is suppressed (not
  distinguishable from a blink by pixels alone).

### Driving: `console_capture.py ctl --out DIR ...` (exactly one of)

* `--keys ret | down,down,ret | shift+a` (comma = successive sends, `+` = chord, QMP qcodes)
* `--type TEXT`: validated BEFORE queueing; characters outside US-layout ASCII are rejected with an error and
  nothing is queued. Add `--secret` to redact the text in `keys.jsonl`: this redacts the log only; the text is
  visible in `ps`/argv and sits briefly in `DIR/inbox/<n>.json`. Use throwaway test values only.
* `--type-file FILE`: the text is read from FILE when it is typed (never in argv or inbox; always redacted in
  `keys.jsonl`); the file must not be left lying around.
* `--qmp-json '{"execute":...}'`, `--quit`.
* Giving more than one of these is rejected (earlier versions silently preferred `--keys`).

## Launcher: `launch-truenas-vm.sh WORKDIR ISO|none`

QEMU TCG + OVMF, 2 vCPU, 3 GiB, user-mode networking, same shape as
`scripts/debian-install-v2/testing/vm/vmctl`. Per run, create in WORKDIR once:
`qemu-img create -q -f qcow2 WORKDIR/disk.qcow2 20G` and
`cp /usr/share/OVMF/OVMF_VARS_4M.fd WORKDIR/OVMF_VARS.fd`; the script checks they and the OVMF code file exist and
says what is missing. `VGA_DEV` defaults to `virtio-vga` (with std `VGA` the TrueNAS kernel console screendump was
garbage under QEMU 10.0.13).

Resource limits: only `nice -n 19`, `ionice -c3`, `-smp 2`, `-m 3072` and `timeout 14400`. There is **no cgroup/slice cap**:
the devcontainer has no systemd, so the `dev-background.slice` launch of the repository runner is not available here. On a
host that has it, wrap the script in `systemd-run --slice=dev-background.slice`. Run one guest at a time and watch
load and MemAvailable (this host also runs a production server).

## Notes

* Requires Pillow; `screendump` format png needs QEMU >= 7.1 (used 10.0.13).
* Detach an ISO from the running guest: `blockdev-open-tray` then `blockdev-remove-medium`, both with `id` = the
  device's qdev path from `query-block` (e.g. `/machine/peripheral-anon/device[1]`), not the drive id.
* The committed daemon was only syntax-checked and its `ctl` validation exercised after the corpus run; the capture
  of the corpus itself was done with the first version (see the corpus README for the differences).
