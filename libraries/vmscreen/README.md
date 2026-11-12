# vmscreen

Read the console of a **local QEMU guest as exact text, without OCR**, drive it with keys, wait for banners, and capture
**screenshot + exact-text pairs** as ground truth for developing pwmcp's OCR. Standard library plus the `cli-extended`
wheel (real dependency, never vendored). Talks QMP over a unix socket; QEMU >= 7.1 (`screendump` PNG).

It is a working tool for people and agents driving VMs (installer tests, `vmctl` guests). It is *not* a pwmcp component:
pwmcp's sidecar OCRs images; vmscreen is how you get the truth to compare that OCR against.

## Commands

All take `--qmp SOCK` (or `$VMSCREEN_QMP`), `--qmp-timeout`, `--dump-dir`, `--qemu-dump-dir` (or `$VMSCREEN_QEMU_DUMP_DIR`) and `--json`.

| command | what it does |
| --- | --- |
| `probe` | which exact-text source applies right now, and why the others do not (always exit 0 if QMP answers) |
| `screen --out F.png` | PNG of the display (QMP `screendump`) |
| `text [--serial-log F] [--source auto\|vga\|serial] [--from-offset N] [--mark TOKEN] [--tail N] [--cells] [--no-blink-mode]` | the console as text; the output names the source and says how far it can be trusted (`exact`) |
| `wait-text PATTERN [--timeout S] [--interval S] [--literal] [--since-mark] [--from-offset N] [--mark TOKEN] [--accept-unverified]` | poll until the regex matches; **exit 3 on timeout**, exit 2 on an invalid regex (before connecting) |
| `mark --serial-log F` | print a mark `OFFSET.INODE.HEADLEN.HEADHASH` of the log: `--mark TOKEN` skips everything written so far and refuses a log that was truncated, regrown or replaced since |
| `send --keys ret,down,ctrl+alt+f2 \| --type TEXT \| --type-file F [--delay S] [--hold-ms MS] [--allow-unknown-keys]` | QMP `send-key`; everything (US-ASCII text, key names against the QEMU qcode list, numbers, files) is validated before connecting, so nothing is half-sent |
| `ocr-pair --out PREFIX [--allow-inexact] [--no-pause] [--force]` | writes `PREFIX.png` and `PREFIX.json` taken at the same instant; refuses to overwrite without `--force` |
| `skills install\|check\|list` | the packaged agent skill (`vmscreen`) |

Exit codes: 0 ok, 1 other failure (including an unwritable output), 2 usage or invalid input, 3 timeout, 4 no exact-text source applies, 5 QMP problem. With `--json` a failure
also prints `{"ok": false, "error", "exit_code", "tried"|"last_text"}` to stdout.

`--dump-dir` matters: QEMU itself writes the `screendump`/`pmemsave` files (vmscreen picks a fresh file name and does not
pre-create the file, so QEMU may run as another user), so the directory must be one **both QEMU and vmscreen see** at the same
path and be writable by QEMU's user; errors name the permissions. The default (the system temp dir) is right only when both run in one container. If the
`pmemsave` file is unusable vmscreen falls back to HMP `xp` over the socket (slower, needs no shared path); the screendump
still needs the shared path.

When QEMU sees that directory under a **different path** (for example `scripts/vm-lab/vm_lab.py` runs QEMU in a container that
mounts the guest's state directory at `/state`, while you see it as `/srv/vm/state`), say so with `--qemu-dump-dir` (or
`$VMSCREEN_QEMU_DUMP_DIR`; the option wins over the variable):

```bash
vmscreen text --qmp /srv/vm/state/qmp.sock --dump-dir /srv/vm/state --qemu-dump-dir /state
```

vmscreen still picks the fresh file name and reads, validates and removes the file under `--dump-dir`; only the path it sends to
QEMU (`screendump`, `pmemsave`; every command, `ocr-pair` included) is `--qemu-dump-dir` plus the same file name. `--qemu-dump-dir`
is refused with exit 2, before anything connects, when `--dump-dir` is not given explicitly (the default temp dir cannot be the
mapped one) or when it is not an absolute path; its existence is not checked, because it exists only for QEMU. Without the option
nothing changes. Errors then name both paths ("QEMU writes to ..., vmscreen reads ..."), and a screendump that QEMU reported as
written but that is not present under `--dump-dir` says the two paths probably do not point at the same directory. A `pmemsave` file
that is missing or unusable still falls back silently to HMP `xp`.

## Exact-text sources

Tried in order; the result always says which one answered and `tried` lists why the others did not.

1. **`vga-text`** (scope: the screen). QEMU's VGA text buffer at physical `0xb8000` (`0xb0000` in mono mode 7, plus the BIOS
   page offset), 2 bytes per cell, read with `pmemsave` (HMP `xp` fallback). Characters are decoded with the default VGA CP437
   glyph table (control codes 1-31 are the graphic glyphs, 0 and 0xFF are blank); the attribute byte gives fg (4 bits), bg (3 bits)
   and blink (bit 7). Only **80+ column** text modes are read (40-column modes are rejected). Whether the buffer is *the displayed
   one* is decided in three steps, and `exact` says which held:
   * the **BIOS data area** (mode `0x449`, columns `0x44a`, rows-1 `0x484`, character height `0x485`, page offset `0x44e`, cursor
     `0x450`) says text mode, and the `screendump` is `cols*(8|9) x rows*char_height` pixels (80x25 -> 720x400). The BDA goes stale
     when a guest driver programs the VGA registers itself, and it does not know the CRTC start address, so this alone cannot prove
     the page is the one being shown;
   * the **ink-map cross-check** (`pixels.py`, stdlib PNG decoder): per cell, the buffer predicts blank (code 0/0x20/0xFF, or
     fg == bg) or visible; the screendump cell must be uniform or not accordingly. Cursor cell, blink-attribute cells and the full
     block 0xDB are skipped. This catches a shifted page and a graphical mode of the same pixel size (80x25 at 720x400, 80x50);
     it does **not** prove which glyph is drawn;
   * **cursor**: Linux moves the cursor through the CRTC without updating the BIOS data area, and QEMU draws it. Exactly one
     cell that the buffer says is blank but that carries one filled block on the **last two scanlines** (14-15 of 16, 12-13 of
     14, 6-7 of 8) spanning the cell width is probably that cursor, but a glyph of that shape (a one-eighth lower block) looks the
     same, so such a cell is **not counted as verified**: it is listed in `uncertain_cells` (`{row, col, reason}`, also in the
     geometry and the sidecar), shown as `U+FFFD` in the text, and the result is `exact: "partial"` (every other cell agrees
     with the screendump). `_` (one scanline), the lower-half block, thicker or higher bars, narrow bars, two such cells or any
     glyph-shaped ink are plain mismatches: `unverified`. The cursor colour (the attribute's fg) is not checked because the DAC
     palette is not readable through QMP. Reading the CRTC cursor registers would need port writes into a running guest and is
     deliberately not done;
   * **one instant**: PNG, BIOS data area and buffer are read as a set and the screendump is taken again afterwards; a set that
     changed is retried 3 times (`ocr-pair` pauses the guest instead).

   `exact` is `true` only when all of that holds; `"partial"` when only uncertain cells remain; otherwise `"unverified"` with `exact_reason` (and the per-cell `verification`
   record). With `--source auto` (the default) an unverified or partial VGA result, and an **empty** serial result (empty log, nothing after
   the offset; typical when the console is on tty0), each count as a failed attempt. Last resort, in order: the flagged VGA text,
   else the empty serial result (**exit 0, empty text, `exact: "stream"`, the reason says the log has no text**; `wait-text` keeps
   polling). `--source vga|serial` return their result as is. `wait-text` never matches unverified text unless
   `--accept-unverified`, nor partial text unless `--accept-partial` (`ocr-pair --accept-partial` likewise); `ocr-pair` refuses it unless
   `--allow-inexact`. The `assumptions` field of every VGA result states what rests on assumption, not detection: the **default
   256-glyph cp437 font** (a 512-glyph or custom-font console may map codes differently) and **blink mode for attribute bit 7**
   (`--no-blink-mode` declares bit 7 a bright background instead; the attribute-controller register is not readable through QMP).
2. **`serial`** (scope: stream, `exact: "stream"`). A `-serial file:PATH` log, rendered: escape sequences stripped, CR/BS/TAB
   applied, erase-in-line (`ESC[K`), erase-in-display (`ESC[J`, `ESC c`) and the column part of cursor movement honoured. This is
   what the guest *printed* to ttyS0, not what is on the screen, and not a terminal emulator: a curses UI on a serial line is not
   reconstructed. A log accumulates across boots, so `wait-text 'login:'` can be satisfied by the previous boot: use
   `--since-mark` (only output written after the command started counts) or `--mark TOKEN` / `--from-offset N` (from
   `vmscreen mark`). A token (`OFFSET.INODE.HEADLEN.HEADHASH.TAILLEN.TAILHASH`, strict ASCII digits/hex, built from two small seeks, never a full read)
   fingerprints the inode, the first 64 bytes AND the last 64 bytes before the offset, so a log truncated and regrown past the
   offset, replaced, or rewritten by a restarted VM that prints the same banner, is an error; `--mark` cannot be combined with
   `--from-offset` or `--since-mark`; a bare `--from-offset` can only detect a log shorter than the offset. An offset inside a
   UTF-8 character or an escape sequence moves forward to the end of it, and an incomplete escape at the end of the log renders
   as nothing. Cursor movement is clamped (4096 columns, longer lines are cut) and the rendering keeps at most 4M cells in total (the oldest lines
   are dropped first), so hostile escape parameters or a huge log cannot exhaust memory. `ocr-pair` never uses the serial source.
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
| TrueNAS installer console (kernel fb console, started with `scripts/vm-lab/vm_lab.py start --display-device virtio`: OVMF + `virtio-vga`) | **no** | OVMF GOP then the kernel framebuffer console; the pwmcp corpus `truenas-27-vanilla` was captured as PNGs only, which is why it needs OCR |
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

`ocr-pair` pauses the guest (`stop`, only if it was running), takes the `screendump` and the text buffer, resumes (`cont`), and
writes:

* `PREFIX.png` - the screen exactly as QEMU renders it;
* `PREFIX.json` (`schema: vmscreen.ocr-pair/2`) - `sha256`, `width`/`height`, `captured_at`, `source`, `exact`, `exact_reason`, `verification`,
  `assumptions`, `vm_status_before`, `guest_was_paused_before`, `guest_paused_for_capture`, `text`, `lines`,
  `geometry` (cols, rows, **cell_w x cell_h pixels**, base address, cursor), `char_rows_hex` / `attr_rows_hex` (the lossless
  buffer), and `cells` (fg, bg, blink per cell). Cell geometry plus text makes it ground truth for glyph-grid matching, not only
  for line OCR: cell (r, c) is the pixel box `(c*cell_w, r*cell_h)`.

On a graphical screen the pair is refused (exit 4) unless `--allow-inexact`, which writes the PNG with `exact: false` and
`text: null` (useful to collect *test* images whose truth you type in by hand); unverified text is refused the same way and, with
`--allow-inexact`, written with `exact: "unverified"`, its reason and its text.

**The guest is never knowingly left paused.** After any failure inside the paused window (QMP timeout, protocol error, a lost
`stop` reply, SIGINT/SIGTERM) the guest is resumed over a *fresh* connection (a timed-out socket is poisoned; QMP serves one
client, so the old one is closed first), 3 attempts, confirmed with `query-status`. Creating the fresh connection is part of each retried attempt (a refused reconnect costs one attempt), and the resume sequence
runs exactly once. The original error is what you see; a resume failure is attached to it as a note, and only if nothing else
failed does `ResumeFailed` (exit 5, "may still be PAUSED: run `cont`", chained to the last cause) occur. Signal handlers are
restored in a `finally`; a signal received while resuming is recorded and acted on only afterwards, and never replaces the
"may still be PAUSED" failure (it is attached as a note). Undecodable reply bytes count as a failed attempt, not an abort.
A guest that was already paused is left paused. The names are `PREFIX.png` / `PREFIX.json` appended to the prefix (so
`vm-1.5` and `vm-1.6` never collide); both are written to temp files and committed with the JSON last, so a PNG without its JSON is
not left behind. Without `--force` the files are created exclusively (hard link), so a file that appeared after the pre-check is
never replaced (`OutputExists`); with `--force` the old files are moved to backups (`.NAME.PID.bak`) and restored if the commit fails, including on SIGINT/SIGTERM in
that window (should the restore itself fail, the error names the backups). A filesystem without hard links gives an error that
names `--force` (replacement writing) and changes nothing.

Relationship to the VM tool `scripts/vm-lab/vm_lab.py`: `console record` keeps changed frames on a timer, and `console ctl` /
`console type` queue and send keys, for **graphical** screens where no text exists. vmscreen is the sibling for **text**
screens: instant exact truth. QMP serves one client, so two clients must not talk to the socket at the same instant; the
recorder opens one short-lived connection per poll and per queued command (`scripts/vm-lab/docs/DESIGN-GUIDE.md`, "Console QMP
connections are short-lived"). The US-layout key table exists in both tools
(`vm_lab_console.py` and `keys.py`); see BACKLOG VMS-BL-03.

## Tests

```bash
cd libraries/vmscreen
VMSCREEN_SKIP_QEMU_SMOKE=1 python -m pytest -q          # everything except the real-QEMU smoke
python -m pytest -q tests/test_smoke_qemu.py            # inside the VM harness only: boots a diskless SeaBIOS guest
```

Parser, QMP client (against a fake QMP socket server), source chain, wait loop and CLI tests need no QEMU. The smoke test is
the only one that starts QEMU; missing QEMU **fails** it, and the one explicit opt-out is `VMSCREEN_SKIP_QEMU_SMOKE=1`
(the gate lane sets it, because the tester image is not where QEMU runs).
