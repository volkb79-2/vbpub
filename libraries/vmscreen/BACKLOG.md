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
* **VMS-BL-07 qcode list.** `keys.QCODES` is a static copy of QEMU's QKeyCode names; `--allow-unknown-keys` is the escape hatch.
  Newer QEMUs may add names. The QMP schema obfuscates type names, so it cannot be queried simply.
* **VMS-BL-08 ink check performance.** `pixels.decode_png` is pure Python (about a second for 720x400 with filtered rows) and runs
  on every VGA poll; add an optional Pillow fast path if `wait-text` polling becomes a load issue.
* **VMS-BL-09 CRTC start address.** The displayed page is verified by the ink map, not read. A direct read of the CRTC start
  registers (not reachable through QMP/HMP) would turn `unverified` into a definite answer for page-flipped screens.
* **VMS-BL-10 glyph identity.** The ink map proves blank/visible only. A cp437 8x16/8x14/8x8 bitmap render compared with the PNG
  would also prove the font assumption; it needs the font bitmaps shipped (licence check) and is the right place to detect 512-glyph consoles.
* **VMS-BL-11 CRTC cursor.** The stale-cursor case is tolerated by shape (one cursor-shaped block in a blank cell). Reading the CRTC
  cursor location needs index/data port writes (HMP `o`) into a running guest, which can race the guest's own index-register use;
  revisit only for a paused guest.
* **VMS-BL-12 serial amplification.** Columns are clamped to 4096, but each input line can still render up to 4096 columns of
  padding; a sparse line representation would remove the factor.
* **VMS-BL-13 cursor colour.** An uncertain cursor-shaped cell could be settled by comparing the block's colour with the attribute's fg
  through the DAC palette (not readable via QMP) or by a pixel-accurate glyph render (VMS-BL-10); until then it stays `partial`.
* **VMS-BL-14 signal after commit.** A signal that arrives after the files are committed but before the handlers are restored is
  dropped (the work is done); a re-raise would need the commit to report it.
