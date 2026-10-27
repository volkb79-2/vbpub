# vmscreen backlog (VMS-BL)

* **VMS-BL-01 release contract.** Not registered in release machinery yet: no `cmru.toml` (tag `vmscreen-v*` is already
  named in `pyproject.toml`), no `assay.toml` mutation lane. Copy the iobench trio when the library is adopted.
* **VMS-BL-02 semantic review catalog.** `docs/cli-surface.json` and the generated region of `docs/CLI-SPEC.md` are in sync, but
  `docs/cli-review.toml` is an empty header: the per-route review cases (`cli-extended surface template`, then the
  `cli-extended-review` skill) are not authored, so `surface check` reports every case as missing.
* **VMS-BL-03 key table duplication.** `keys.py` copies the US-layout table from `pwmcp/tools/console-capture/console_capture.py`
  (a script). Decide whether console-capture should import vmscreen's `keys` (then it needs a wheel dep) or stay standalone.
* **VMS-BL-04 live verification.** The `0xb8000` read through `pmemsave` on a real QEMU (std VGA, TCG) is covered only by the
  opt-in `tests/test_smoke_qemu.py`; it has not been run in the harness yet. Open risks: `screendump` while the VM is stopped
  (ocr-pair) and `pmemsave` through the legacy VGA MMIO window in odd/even text addressing.
* **VMS-BL-05 vcs source.** `/dev/vcsa*` over a guest-agent or serial-login channel (README source 3).
* **VMS-BL-06 virtio-gpu / QXL text.** Text mode exists only on legacy VGA devices; `-vga virtio` / `virtio-vga` guests in text
  mode are untested (virtio-vga keeps a VGA core, so it may work).
