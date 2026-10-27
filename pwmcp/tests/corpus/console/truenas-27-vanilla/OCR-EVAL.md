# OCR evaluation: pwmcp OCR sidecar vs this corpus

Date 2026-10-27. Sidecar: branch `worktree-agent-a201aaf71c2233a93`, `pwmcp/containers/ocr/`
(Dockerfile + `ocr_service.py`, Tesseract 5 from Debian trixie, English traineddata), built unmodified as
`pwmcp-ocr-eval:local`, run as `pwmcp-ocr-eval-run` (`--cpus=1 --memory=512m --read-only --tmpfs /tmp`,
removed afterwards). Requests: `POST /v1/ocr?psm=P&scale=S`, PNG body. Scoring script: `ocr_eval.py`
(raw per-run output including recognised text: `ocr-results.json`). 20 frames x psm {6, 11} x scale {1, 2, 3}
= 120 runs, no request errors.

The container publishes only on the docker daemon host's loopback, which the devcontainer cannot
reach, so `ocr_eval.py` posts through `docker exec -i <container> python3 -c ...` (the default); `--url`
posts directly.

Metrics: **char** = 1 - normalised Levenshtein distance to the ground truth (whitespace runs collapsed,
floored at 0). **line** = share of non-empty ground-truth lines found as an exact (whitespace-collapsed)
output line. Cells below read `char%/line%`.

## Per frame

| frame | p6 s1 | p6 s2 | p6 s3 | p11 s1 | p11 s2 | p11 s3 |
|---|---|---|---|---|---|---|
| 01-grub-iso-menu | 21/0 | 21/0 | 21/17 | 21/0 | 21/0 | 21/17 |
| 02-live-copy-squashfs | 96/25 | 100/100 | 98/50 | 96/25 | 100/75 | 99/50 |
| 03-live-login-banner | 99/43 | 99/43 | 100/71 | 98/14 | 99/43 | 100/71 |
| 04-installer-console-setup | 42/14 | 45/14 | 51/29 | 30/14 | 26/0 | 30/14 |
| 05-choose-destination-media | 17/11 | 18/11 | 20/22 | 10/11 | 16/0 | 16/0 |
| 06-choose-destination-selected | 16/11 | 18/11 | 19/22 | 9/11 | 16/0 | 16/0 |
| 07-erase-warning | 7/10 | 7/10 | 10/20 | 7/10 | 7/0 | 7/0 |
| 08-auth-method | 60/20 | 65/20 | 66/40 | 26/0 | 26/20 | 26/0 |
| 09-password-empty | 0/0 | 0/0 | 0/0 | 0/0 | 0/0 | 0/0 |
| 10-password-filled | 0/0 | 0/0 | 0/0 | 0/0 | 0/0 | 0/0 |
| 11-progress-start-over-dialog | 0/0 | 0/0 | 0/0 | 0/0 | 0/0 | 0/0 |
| 12-progress-extracting | 93/45 | 100/92 | 100/98 | 85/0 | 99/0 | 94/0 |
| 13-progress-complete | 94/41 | 99/88 | 100/94 | 85/4 | 98/0 | 95/2 |
| 14-installation-succeeded | 18/25 | 23/25 | 24/50 | 18/25 | 17/0 | 18/0 |
| 15-uefi-tianocore-logo | 11/0 | 80/0 | 82/0 | 5/0 | 16/0 | 48/0 |
| 16-grub-installed-menu | 95/38 | 98/75 | 98/75 | 97/50 | 98/75 | 98/62 |
| 17-grub-booting-installed | 98/67 | 98/67 | 98/67 | 92/33 | 93/33 | 92/33 |
| 18-kernel-boot-log | 97/45 | 98/59 | 98/66 | 91/14 | 92/14 | 93/14 |
| 19-console-setup-with-kernel-noise | 95/65 | 95/55 | 93/45 | 90/20 | 94/10 | 93/10 |
| 20-console-setup-duplicated-redraw | 95/69 | 95/62 | 92/50 | 91/27 | 93/35 | 93/35 |

Means over all 20 frames (char / exact-line / fuzzy-line >=0.9 similarity / seconds per request):

| setting | char | line | line fuzzy | s/request |
|---|---|---|---|---|
| psm 6 scale 1 | 0.527 | 0.264 | 0.500 | 0.5 |
| psm 6 scale 2 | 0.579 | 0.365 | 0.543 | 0.8 |
| **psm 6 scale 3** | **0.585** | **0.408** | **0.571** | 1.2 |
| psm 11 scale 1 | 0.475 | 0.129 | 0.356 | 0.5 |
| psm 11 scale 2 | 0.506 | 0.152 | 0.334 | 0.9 |
| psm 11 scale 3 | 0.520 | 0.154 | 0.316 | 1.2 |

## Key phrases (exact, case-sensitive, whitespace-collapsed substring)

Columns: p6s1 p6s2 p6s3 p11s1 p11s2 p11s3; `Y` found, `-` not found.

| frame | phrase | result |
|---|---|---|
| 04 | `TrueNAS 27.0.0-RC.1 Console Setup` | Y Y Y Y - Y |
| 04 | `1 Install/Upgrade` | - - - - - - |
| 04 | `3 Reboot System` | - - - - - - |
| 05 | `Choose Destination Media` | Y Y Y Y - Y |
| 05 | `vda` | - - - - - - |
| 06 | `[*]` | - - - - - - |
| 07 | `Proceed with the installation?` | - - - - - - |
| 08 | `Administrative user (truenas_admin)` | Y Y Y - - - |
| 09 | `Confirm Password:` | - - - - - - |
| 13 | `[100%] Installation completed successfully` | Y - Y Y - Y |
| 14 (finish) | `Installation Succeeded` | Y Y Y Y - Y |
| 14 (finish) | `The TrueNAS installation on vda succeeded!` | - - - - - - |
| 14 (finish) | `Please reboot and remove the installation media.` | - - - - - - |
| 16 | `TrueNAS Scale GNU/Linux 27.0.0-RC.1` | Y Y Y - Y Y |
| 17 | `Loading initial ramdisk ...` | Y Y Y Y Y Y |
| 19 | `Console setup` | Y Y Y Y Y Y |
| 19 | `http://10.0.2.15` | Y - - Y - - |
| 19 | `https://10.0.2.15` | - Y - - - - |
| 19 | `http://[fec0::5054:ff:fe12:3456]` | - - - - - - |
| 19 | `4) Change local administrator password` | Y Y Y - Y Y |
| 19 | `10) Shutdown` | Y - - Y - - |
| 19 | `Enter an option from 1-10:` | Y - - Y - - |
| 20 | `Console setup` | Y Y Y Y Y Y |
| 20 | `https://10.0.2.15` | - Y - - - - |
| 20 | `9) Reboot` | - Y Y - - Y |
| 20 | `Enter an option from 1-10:` | Y - - Y - - |

## Findings

**Best settings.** `psm=6`, `scale=3` (highest means; and best on the pure console frames 12, 13, 18). `psm=11`
(sparse text) is worse than psm 6 for every metric because it breaks lines apart; do not use it for full-screen
consoles. Scale 1 is not enough for 8x16 cells: digits are confused (see below). Scale 2 vs 3 is within
noise on plain console frames, and scale 3 doubles the cost (about 1.2 s with 1 CPU).

**What reads well** (char >= 93% at psm 6 scale 2-3): plain light-on-black console text, i.e. the live
boot text (02, 03), progress screens (12, 13: 98-100%), GRUB text (16, 17), the kernel log (18), and the
installed Console setup menu (19, 20). The finish *screen* is not among them, see below.

**What fails, and why**
* Newt dialogs in the installer (04-11, 14): black text on a mid-grey (0xAA) dialog over saturated blue. The
  service converts to 8-bit grayscale and Tesseract's automatic binarisation loses the body text: only
  the title bar text and some highlighted entries are recognised. Result for the finish dialog (14): title
  `Installation Succeeded` and `< OK >` read, but both body lines are missing (char 24%). Frames 09-11 return
  no words at all (0 words). This is the most important result for the use case: **the finish message "The
  TrueNAS installation on vda succeeded! Please reboot..." is NOT readable by the sidecar as shipped**; only its
  title is. Untested suggestion: pre-threshold per colour (treat colour 0xAA grey and black as ink on
  light) or add an inverted pass; the exact glyph-grid engine (text on a known 8x16 grid with a 16-colour
  palette) avoids the problem entirely.
* GRUB ISO menu (01): the menu text is cyan/light-grey on saturated blue; only two of the six lines were
  read (`*Start TrueNAS Installation`, `Start TrueNAS Installation (115286 baud)`: the digits `115200` come out as
  `115286`) and the box border confuses layout. Same cause as above.
* TianoCore splash (15): logo with a small tag line; at scale 3 only `Open Platform Firmware Development` is read
  (the word `TianoCore` is a graphic, `T .` was read).
* Digit and punctuation errors on the otherwise good console frames at scale 1-2: `0` read as `6`, `@` or `9`,
  `1` as `l`, `5` as `S` (`10.0.2.15` came out as `16.0.2.15` at scale 2 in frame 20; option
  `9) Reboot` as `3) Reboot`; `Enter an option from 1-10` as `1-16`; `10) Shutdown` as `18) Shutdown`), `::` as `: :`, and a spurious space
  after `https:`. IPv6 URLs (`http://[fec0::5054:ff:fe12:3456]`) were never recognised exactly in any of the six
  settings. Consequence: OCR alone cannot give a *verified* IP or option number; use fuzzy phrase
  matching for the signature and exact glyph matching for values.
* Kernel noise printed over the menu prompt (19) makes `Enter an option from 1-10:` fall into a longer line
  that is only exact at scale 1.
* Line-exact recall is strict; where char accuracy is 93-99% a single wrong glyph removes the whole line
  from the exact-line metric, which is why line recall is much lower than char accuracy.

**Is a "ready" signature usable?** For the installed system: yes with fuzzy matching. `Console setup` is read in
all 12 runs on frames 19 and 20; `4) Change local administrator password` in 10 of 12 and the other option lines
mostly; the URLs are recognised only roughly (`http://`, `https://`, the IPv4 with possible digit errors). A robust rule:
`Console setup` AND `web user interface` AND an `http` line AND `Enter an option` (fuzzy, edit distance <= 2).
Note the installer's own menu (frame 04) says `Console Setup` with a capital S in a box and does not have the
URLs; distinguishing it needs the URL/option lines or the `truenas-installer` banner.

**Defects / risks noticed in the sidecar** (code not modified)
1. No colour-aware preprocessing: grayscale + Tesseract's default Otsu fails on black-on-grey dialogs (above). Either
   expose an `invert`/`threshold`/`channel` option or do per-frame colour-pair separation for console frames.
2. Low-confidence words: the service returns `conf` per word and the evaluation did not use it; mean confidence of
   the garbled dialog outputs was still high (91% for the 5-word frame-14 result), so confidence alone does not flag
   the missing body text. A watcher must treat absent expected lines as INDETERMINATE, not as a mismatch, which matches the
   design decision.
3. The published port is on the docker host's loopback; callers inside a devcontainer need the compose network alias
   (not tested here, only `docker exec`).

## Text grid (input for an exact glyph-matching engine)

All Linux-console/newt/tty text frames are rendered on a fixed grid: **8 x 16 px cells, 160 columns x 50 rows**
on the 1280x800 canvas (the VGA 8x16 console font). Measured by autocorrelation of ink row/column sums: row
periods 16 (or 32/48 where lines alternate), column period 8 on frames 01-03, 12, 13, 19, 20; newt dialogs
use the same cells (the dialog text in frame 04 starts at x = 408 = 51 x 8 and its lines are 16 px apart,
y = 327, 343, 359, ...), the autocorrelation on the dialog frames was unreliable because the grey dialog
background is bright (period results 9/4 there are artefacts, ignore). The kernel boot log (frame 18) wraps at 80
columns (640 px) for the early lines, while later lines run past 100 columns (apparently the console was
resized during boot; not investigated). Exceptions: the **installed system's GRUB** (16) and the "Booting ..." text (17) are
drawn differently: 8 px character advance but a 19 px row pitch (16), and about 16 px advance / 38 px pitch in
frame 17, whose glyphs look enlarged 2x (measured only by autocorrelation, not verified further). The ISO's GRUB (01) has an 8 x 16 grid
inside its frame. The TianoCore logo (15) is a bitmap. The cursor is a block in the console and an underscore
at the prompt, with a blink the capture tool had to suppress. Colours: black text background, light grey
(about 0xAA) foreground; newt dialogs use black on 0xAA grey; highlighted rows are white on blue; progress lines
`[NN%] ...` have no colour. Because the glyph set is the standard 8x16 VGA font (with the `[`/`]`/`%` etc. from the same font), exact
template matching per cell on this grid should recognise everything that OCR missed, with colour pair
detection per cell.
