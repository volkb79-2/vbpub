---
name: vmscreen
description: Read the console of a local QEMU guest as exact text (VGA text buffer or serial log), wait for banners, send keys, and capture screenshot plus exact-text pairs for OCR corpora, via the vmscreen CLI.
---

# Reading a QEMU console without OCR

Use `vmscreen` when you work with a local QEMU guest that has a QMP socket (`-qmp unix:SOCK,server=on,wait=off`).

1. `vmscreen probe --qmp SOCK` says which exact-text source applies right now and why the others do not.
2. `vmscreen text --qmp SOCK [--serial-log FILE] --json` prints the console. The `exact` field says how far to trust it: `true` (VGA text cross-checked against the screendump), `"unverified"` (do not rely on it; see `exact_reason`), `"stream"` (serial: what the guest printed, not a screen; use `--since-mark` with `wait-text` so an earlier boot cannot match). Exit 4 means the screen is a graphical framebuffer: no exact text exists, use `vmscreen screen` and OCR.
3. `vmscreen wait-text 'login:' --timeout 120 --qmp SOCK` blocks until the pattern appears; exit 3 is a timeout.
4. `vmscreen send --qmp SOCK --keys ret` or `--type 'text\n'` drives the guest.
5. `vmscreen ocr-pair --qmp SOCK --out corpus/name` writes name.png and name.json captured at the same instant.

Rules: QMP serves one client, so stop other pollers (for example pwmcp console_capture) first. QEMU writes dump files itself, so `--dump-dir` must be a path both processes see. Never start QEMU in the interactive devcontainer; guests run through the VM harness.
