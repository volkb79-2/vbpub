# vmscreen

Read the console of a **local QEMU guest as exact text, without OCR**, drive it with keys, wait for banners, and capture
**screenshot + exact-text pairs** as ground truth for developing pwmcp's OCR. Standard library plus the `cli-extended`
wheel (real dependency, never vendored). Talks QMP over a unix socket; QEMU >= 7.1 (`screendump` PNG).

It is a working tool for people and agents driving VMs (installer tests, `vmctl` guests). It is *not* a pwmcp component:
pwmcp's sidecar OCRs images; vmscreen is how you get the truth to compare that OCR against.

## Commands

All take `--qmp SOCK` (or `$VMSCREEN_QMP`), `--qmp-timeout`, `--dump-dir` and `--json`.

| command | what it does |
| --- | --- |
| `probe` | which exact-text source applies right now, and why the others do not (always exit 0 if QMP answers) |
| `screen --out F.png` | PNG of the display (QMP `screendump`) |
| `text [--serial-log F] [--source auto\|vga\|serial] [--tail N] [--cells]` | the console as exact text; the output names the source |
| `wait-text PATTERN [--timeout S] [--interval S] [--literal]` | poll until the regex matches; **exit 3 on timeout** |
| `send --keys ret,down,ctrl+alt+f2 \| --type TEXT \| --type-file F [--delay S] [--hold-ms MS]` | QMP `send-key`; `--type` is validated (US ASCII) before anything is sent |
| `ocr-pair --out PREFIX [--allow-inexact] [--no-pause]` | writes `PREFIX.png` and `PREFIX.json` taken at the same instant |
| `skills install\|check\|list` | the packaged agent skill (`vmscreen`) |

Exit codes: 0 ok, 1 other failure, 2 usage, 3 timeout, 4 no exact-text source applies, 5 QMP problem. With `--json` a failure
also prints `{"ok": false, "error", "exit_code", "tried"|"last_text"}` to stdout.

`--dump-dir` matters: QEMU itself writes the `screendump`/`pmemsave` files, so the directory must be one **both QEMU and
vmscreen see** at the same path. The default (the system temp dir) is right only when both run in one container. If the
`pmemsave` file is unusable vmscreen falls back to HMP `xp` over the socket (slower, needs no shared path); the screendump
still needs the shared path.

## Exact-text sources

Tried in order; the result always says which one answered and `tried` lists why the others did not.

1. **`vga-text`** (scope: the screen). QEMU's VGA text buffer at physical `0xb8000` (`0xb0000` in mono mode 7, plus the BIOS
   page offset), 2 bytes per cell, read with `pmemsave` (HMP `xp` fallback). Characters are decoded with the VGA CP437 glyph
   table (control codes 1-31 are the graphic glyphs, 0 and 0xFF are blank); the attribute byte gives fg (4 bits), bg (3 bits)
   and blink (bit 7; `--no-blink-mode` reads bit 7 as a bright background). Validity is decided by the **BIOS data area**
   (mode `0x449`, columns `0x44a`, rows-1 `0x484`, character height `0x485`, page offset `0x44e`, cursor `0x450`) **and** a
   cross-check that the `screendump` is exactly `cols*(8|9) x rows*char_height` pixels (80x25 -> 720x400). The BDA goes stale
   when a guest driver programs the VGA registers itself, so the size check is what rejects fbcon/KMS/GOP screens.
2. **`serial`** (scope: stream). A `-serial file:PATH` log, rendered (escape sequences stripped, CR/BS/TAB applied). This is what
   the guest *printed* to ttyS0, not what is on the screen, and it is not a terminal emulator: a curses UI on a serial line is
   not reconstructed. `ocr-pair` never uses it (it would not be the pixels' ground truth).
3. **`vcs`** (`/dev/vcsa*` from a guest shell channel, or the guest agent). **Not implemented**: it needs a channel into the
   guest (guest-agent `guest-exec`, ssh, or a serial login) and is only valid for the guest's Linux text console, which under
   QEMU is the fbcon on a graphical framebuffer anyway. `cat /dev/vcsa1` in the guest gives chars+attributes; reported as
   `not implemented` in `tried`.
4. **none**. A graphical framebuffer has no text to read: `text` exits 4, `wait-text` keeps polling until its timeout, and the
   output says the screen needs `screen` + OCR.

### Which of our known screens are text-readable

Verified from the repository (not by running them here), see the status column:

| screen | text mode? | basis |
| --- | --- | --- |
| SeaBIOS banner / "Booting from..." / No bootable device (QEMU `-machine pc` default, `std` VGA) | yes (VGA 80x25, 720x400) | architecture: the BIOS sets mode 3; **live check is the opt-in smoke test** |
| Linux/Debian installer on **serial** (`vmctl` runs `-display none -serial file:serial.log`) | via the **serial** source | `scripts/debian-install-v2/testing/vm/vmctl` |
| Debian installer / grub graphical on `-vga std`, UEFI (OVMF) GOP screens | **no** | graphical framebuffer |
| TrueNAS installer console (kernel fb console, `launch-truenas-vm.sh`, OVMF + `virtio-vga`) | **no** | OVMF GOP then the kernel framebuffer console; the pwmcp corpus `truenas-27-vanilla` was captured as PNGs only, which is why it needs OCR |
| any Linux fbcon / KMS console | **no** | the text lives in kernel memory, not VGA text RAM (reachable only via `/dev/vcsa*`, source 3) |

## Worked example

Run QEMU only where it may run (the VM harness container, `scripts/debian-install-v2/testing/vm/run-vm-harness.sh`, which
places it under the right `--cgroup-parent`); **never in the interactive devcontainer**. Inside that container, a tiny
diskless guest whose SeaBIOS prints in VGA text mode:

```bash
D=/var/tmp/vmscreen-demo; mkdir -p $D
nice -n 10 qemu-system-x86_64 -machine pc -accel tcg -m 32 -smp 1 -display none -vga std \
  -serial file:$D/serial.log -monitor none -qmp unix:$D/qmp.sock,server=on,wait=off -daemonize -pidfile $D/qemu.pid

export VMSCREEN_QMP=$D/qmp.sock
vmscreen wait-text 'SeaBIOS' --timeout 60 --dump-dir $D          # exit 0, or 3 on timeout
vmscreen text --dump-dir $D --json                              # {"source": "vga-text", "exact": true, "text": "SeaBIOS (version ...", ...}
vmscreen send --keys f12 --dump-dir $D                          # the boot menu key
vmscreen ocr-pair --out $D/corpus/seabios-001 --dump-dir $D     # seabios-001.png + seabios-001.json
kill $(cat $D/qemu.pid)
```

## Feeding pwmcp's OCR corpus

`ocr-pair` pauses the guest (`stop`), takes the `screendump` and the text buffer, resumes (`cont`, only if it was running),
and writes:

* `PREFIX.png` - the screen exactly as QEMU renders it;
* `PREFIX.json` (`schema: vmscreen.ocr-pair/1`) - `sha256`, `width`/`height`, `captured_at`, `source`, `exact`, `text`, `lines`,
  `geometry` (cols, rows, **cell_w x cell_h pixels**, base address, cursor), `char_rows_hex` / `attr_rows_hex` (the lossless
  buffer), and `cells` (fg, bg, blink per cell). Cell geometry plus text makes it ground truth for glyph-grid matching, not only
  for line OCR: cell (r, c) is the pixel box `(c*cell_w, r*cell_h)`.

On a graphical screen the pair is refused (exit 4) unless `--allow-inexact`, which writes the PNG with `exact: false` and
`text: null` (useful to collect *test* images whose truth you type in by hand).

Relationship to `pwmcp/tools/console-capture`: that daemon polls `screendump` on a timer, keeps changed frames, and drives
keys, for **graphical** screens where no text exists. vmscreen is the sibling for **text** screens: instant exact truth.
QMP serves one client, so the two cannot run on the same socket at once. The key-name table is duplicated from
`console_capture.py` (a script, not importable); see BACKLOG VMS-BL-03.

## Tests

```bash
cd libraries/vmscreen
VMSCREEN_SKIP_QEMU_SMOKE=1 python -m pytest -q          # everything except the real-QEMU smoke
python -m pytest -q tests/test_smoke_qemu.py            # inside the VM harness only: boots a diskless SeaBIOS guest
```

Parser, QMP client (against a fake QMP socket server), source chain, wait loop and CLI tests need no QEMU. The smoke test is
the only one that starts QEMU; missing QEMU **fails** it, and the one explicit opt-out is `VMSCREEN_SKIP_QEMU_SMOKE=1`
(the gate lane sets it, because the tester image is not where QEMU runs).
