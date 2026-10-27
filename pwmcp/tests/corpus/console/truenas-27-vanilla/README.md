# truenas-27-vanilla: console screenshots of an unpatched TrueNAS 27.0.0-RC.1 install

Screenshots of a **vanilla** TrueNAS 27.0.0-RC.1 installer (SHA-256
`515a0252e72f1a6c86d603fa4d1661333029fe9a4804d0a7a28d24fe4f63498e`) installing onto a blank 20 GiB
virtio disk in QEMU TCG + OVMF, then booting the installed system. Captured 2026-10-27 with
`pwmcp/tools/console-capture/`. All frames are 1280x800 PNG. Exact VM command line, key sequence,
timings and capture threshold: `MANIFEST.json`. Every key sent: `keys.jsonl`. Record of the full
242-frame series (kept outside git): `series-manifest.jsonl`.

First observations of the screens that were never seen before: **14 (finish)**, the reboot path
(**15-17**) and the installed system's **Console setup** (19, 20).

## Provenance and licence

The images are screen output of publicly available software: TrueNAS (an open-source distribution;
its installer and console menu are GPL/open-source components on a Debian base), GNU GRUB (Debian
build), the Linux kernel boot log, and TianoCore/OVMF firmware output. They are included only as
test fixtures of that program output, not as product artwork. To reproduce: download
`TrueNAS-27.0.0-RC.1.iso` (SHA-256 above) with `scripts/truenas/vanilla-iso-patcher/truenas-vanilla-iso.py`
and follow `MANIFEST.json`. `keys.jsonl`, `series-manifest.jsonl` and `MANIFEST.json` mention
`/home/vscode/console-corpus/...` paths of the capturing machine; they are informative only and
do not exist in a checkout. The host/clock values in the frames (IP `10.0.2.15`, kernel time stamps,
`truenas-installer.local`) are QEMU user-network and run-specific values.

## Frames

"label" is the elapsed value in the frame's file name in the full series; "real" is seconds since
capture start measured from the file modification times (+-1 s). They agree up to series frame 13
and **differ by 38.5 s from frame 14 on**, see "Clock note".

| file | series seq (label / real s) | shows | useful for |
|---|---|---|---|
| 01-grub-iso-menu | 0002 (3 / 3) | ISO GRUB menu, 13 s countdown | GRUB gfx text; countdown digit changes |
| 02-live-copy-squashfs | 0004 (27 / 27.5) | live boot: rsync progress copying squashfs to RAM | plain console text with a progress line |
| 03-live-login-banner | 0010 (58 / 58) | Debian tty1 login banner of the live installer | long plain-text paragraph; hostname `truenas-installer.local` marks the INSTALLER environment |
| 04-installer-console-setup | 0011 (70 / 70) | installer menu "TrueNAS 27.0.0-RC.1 Console Setup" (1 Install/Upgrade ... 4 Shutdown System) | newt dialog, numbered options; NOT the installed system's menu (similar title words) |
| 05-choose-destination-media | 0013 (125 / 125) | disk picker, `[ ] vda Unknown Model -- 20 GiB` | checkbox state |
| 06-choose-destination-selected | 0014 (128 / 166.5) | same, `[*]` ticked | checkbox state change is only ~40 px |
| 07-erase-warning | 0015 (146 / 185) | "This erases ALL partitions ..." Yes/No | destructive-action confirmation (Yes focused) |
| 08-auth-method | 0016 (167 / 206) | Web UI Authentication Method | numbered menu |
| 09-password-empty | 0017 (186 / 225) | password prompt, empty fields | input dialog |
| 10-password-filled | 0019 (211 / 250) | 12 asterisks in both fields | masked input (value is not visible) |
| 11-progress-start-over-dialog | 0021 (236 / 274.5) | progress lines `[0%] Formatting disk vda` appear at the screen bottom OVER the still-drawn password dialog | mixed screen, partial overdraw |
| 12-progress-extracting | 0102 (763 / 802) | full-screen scrolling `[NN%] Extracting` progress, up to `[80%] Updating initramfs` | progress percentage patterns |
| 13-progress-complete | 0106 (961 / 1000) | progress ends `[100%] Installation completed successfully` | success text before the dialog |
| 14-installation-succeeded | 0107 (964 / 1003) | **Finish dialog**: "The TrueNAS installation on vda succeeded! Please reboot and remove the installation media." with only an OK button | finish signature |
| 15-uefi-tianocore-logo | 0112 (1080 / 1119) | OVMF splash during the reboot | reboot-in-progress; almost no text |
| 16-grub-installed-menu | 0118 (1252 / 1291) | installed system's GRUB: `TrueNAS Scale GNU/Linux 27.0.0-RC.1` / Advanced options / UEFI Firmware Settings | boot-into-installed signature; different GRUB font metrics than 01 |
| 17-grub-booting-installed | 0122 (1267 / 1306) | `Booting 'TrueNAS Scale GNU/Linux 27.0.0-RC.1'` / Loading Linux ... / Loading initial ramdisk ... | |
| 18-kernel-boot-log | 0123 (1276 / 1315) | kernel messages, early lines wrapped at 80 columns | dense kernel log text |
| 19-console-setup-with-kernel-noise | 0240 (1850 / 1889) | **installed system Console setup** with the four management URLs and options 1-10; kernel `rcu:` messages printed over the prompt | ready signature under kernel noise |
| 20-console-setup-duplicated-redraw | 0242 (1859 / 1898) | same menu after a redraw: the top of the menu appears twice (option 1-3 block, then the full menu) and the prompt is clean | ready signature, duplicated block, clean prompt |

### Clock note and the provenance of frame 06 (resolved)

Frame 06 (series 0014) shows the ticked `[*]`. The Space key is logged at 140.4 s, and series 0014 is
labelled 128 s, which looked inconsistent. Resolution, from file modification times: the capture daemon
was stopped and restarted once, after the Space and before the next key, to lower the change threshold
from 0.0002 to 0.00002 (the first daemon's threshold had not kept the `[*]` frame, a change of ~40 px).
The restart gap (~38.5 s) was not counted by the daemon's resume arithmetic (it set its clock to "last
elapsed + one poll interval"), so every `elapsed_s` label and every `keys.jsonl` `t` from the restart on
is 38.5 s smaller than real time. Series 0014 is the first frame taken by the new daemon (real 166.5 s, about 26 s
after the Space at 140.4 s); it was kept with fraction 1.0 and no `keys_before` because a restarted
daemon had no previous frame to compare with (the resume now reloads the last kept frame, see the tool
README). It is the first *kept* frame showing the ticked box; the first daemon may have seen the ticked
state earlier but did not keep it. `series-manifest.jsonl` has the added field `elapsed_real_s` for every
frame, and `keys.jsonl` the added field `t_real_s` (entries after the restart shifted by +38.5 s);
use those for durations. File names are unchanged.

## Behaviour of the vanilla installer that the OCR/watch logic must know

1. After "Installation Succeeded" and OK, the installer does **not** reboot by itself: it returns to
   its own menu (frame 04 look-alike, title "TrueNAS 27.0.0-RC.1 Console Setup"). The operator has
   to choose `3 Reboot System`. So the finish signature is the dialog text (14) and the success line
   (13), and the "Console Setup" title alone does not prove the installed system is up.
2. The installed system's ready signature is `Console setup` plus `The web user interface is at:`
   plus `http://`/`https://` URLs plus `Enter an option from 1-10:` (19, 20). Lowercase "setup"
   and no box-drawing, unlike the installer's "Console Setup" dialog.
3. In this harness the CD had boot priority, so after the reboot the ISO's GRUB started again
   (frames 0113-0116 in the full series). On real hardware this depends on boot order; the installer's
   instruction "remove the installation media" is real.
4. The installed GRUB (16) has a different font and layout than the ISO's GRUB (01).
5. First-boot under TCG is slow (about 10 minutes from kernel to the menu, rcu stall messages); the
   menu was reached anyway. IPs in frames 19/20 are QEMU user-mode network values (10.0.2.15).

## Ground-truth rules and caveats

* `ground-truth/NN-*.txt` is the visible text transcribed by eye, line by line, without box-drawing
  characters and without column layout (offsets are not preserved; vertical blank rows are kept where
  seen, e.g. two blank rows after the first line of frame 03). Colours were not transcribed.
* Quote normalisation: the GRUB screens (01, 16, 17) draw quote characters that may be typographic
  (`‘ ’`, grave accent) glyphs; the ground truth writes them as the ASCII grave accent `` ` `` and
  apostrophe `'` (e.g. `Booting `TrueNAS ...'`, `e' ... c'`). Compare with that normalisation in mind. Arrow glyphs are transcribed as drawn
  (`↑ ↓` in 01, `▲ ▼` in 16).
* Uncertain glyphs are `?`. In frame 19 the character before `Tasks blocked` and before `(detected by`
  is a double-dagger-like glyph (`‡`) that I could not identify; the ground truth keeps it as `?`.
* Kernel time stamps and hashes in frame 18 were read from the image and may contain a mis-read digit;
  treat frame 18 as lower-trust ground truth.
* The only text in frames 09-11 for the password is asterisks; the test value was `truenas-test` (a
  throwaway). `keys.jsonl` records it in clear because the capture was run without `--secret`.
* Dialogs are black text on grey (0xAA) over blue, highlighted entries white on blue, GRUB cyan/white
  on blue.
* The full series also contains many near-identical systemd boot-message frames (seq 123-236) and a
  first, discarded std-VGA attempt (garbage framebuffer, see `MANIFEST.json`).
* OCR numbers in `OCR-EVAL.md` came from the OCR sidecar as it stood in the sidecar branch worktree at
  about 07:55 UTC on 2026-10-27; see that file for the exact commit statement.
