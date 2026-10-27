# console-capture

Records a screenshot series of a QEMU guest console through QMP `screendump`, keeping only frames
that changed, and lets you drive the guest by queuing keys. Used to build
`pwmcp/tests/corpus/console/truenas-27-vanilla/`.

* `console_capture.py run --qmp SOCK --out DIR` is the daemon. It owns the QMP socket (QMP serves one
  client), polls every `--interval` s (3), writes `DIR/frames/NNNN_<elapsed>s.png` and one line per
  kept frame to `DIR/manifest.jsonl` (sequence, elapsed_s, file, sha256, width, height,
  fraction_of_pixels_changed, keys_before). Every key or QMP command sent through `ctl` is logged to
  `DIR/keys.jsonl`. It writes `DIR/DONE` on quit or when the 2000-frame cap hits (logged). On restart in the same
  `DIR` it continues the numbering.
* `console_capture.py ctl --out DIR --keys ret | down,down,ret | shift+a --type TEXT [--secret] --qmp-json '{...}' | --quit`
  queues a command in `DIR/inbox/`; the daemon executes it before its next poll.
* Keep rule: fraction of pixels with luma difference > 48 from the last kept frame must be >= `--diff-threshold`.
  At 1280x800 the default is **0.00002** (about 20 px); 0.0002 missed a ticked checkbox. A change confined to a `--cursor-w` x `--cursor-h` box (24x40) is held for one poll and dropped if the
  next poll shows the previous frame again (blink). The thresholds in use are logged at start.
* `launch-truenas-vm.sh WORKDIR ISO|none` starts the QEMU TCG + OVMF guest (2 vCPU, 3 GiB, user networking), same
  shape as `scripts/debian-install-v2/testing/vm/vmctl`. Use `VGA_DEV=virtio-vga`: with the default std `VGA` the
  screendump of the TrueNAS kernel console came back as garbage in this QEMU (10.0.13).
* Host safety (this host also runs a production server): one guest at a time, `nice`/`ionice`, wall-clock
  `timeout 14400`, and watch load and MemAvailable while it runs.
* Requires Pillow. `screendump` format png needs QEMU >= 7.1 (checked 10.0.13).
* Detaching an ISO from the running guest: `blockdev-open-tray` then `blockdev-remove-medium`, both with
  `id` = the device's qdev path (from `query-block`, e.g. `/machine/peripheral-anon/device[1]`), not the drive id.
