# truenas-27-vanilla: console screenshots of an unpatched TrueNAS 27.0.0-RC.1 install

Screenshots of a **vanilla** TrueNAS 27.0.0-RC.1 installer (SHA-256
`515a0252e72f1a6c86d603fa4d1661333029fe9a4804d0a7a28d24fe4f63498e`) installing onto a blank 20 GiB
virtio disk in QEMU TCG + OVMF, then booting the installed system. Captured 2026-10-27 with
`pwmcp/tools/console-capture/`. All frames are 1280x800 PNG. Exact VM command line, key sequence,
timings and capture threshold: `MANIFEST.json`. Every key sent: `keys.jsonl`. Record of the full
242-frame series (kept outside git): `series-manifest.jsonl`.

First observations of the screens that were never seen before: **14 (finish)**, the reboot path
(**15-17**) and the installed system's **Console setup** (19, 20).

| file | origin (series seq, elapsed) | shows | useful for |
|---|---|---|---|
| 01-grub-iso-menu | 0002, 3 s | ISO GRUB menu, 13 s countdown | GRUB gfx text; countdown digit changes |
| 02-live-copy-squashfs | 0004, 27 s | live boot: rsync progress copying squashfs to RAM | plain console text with a progress line |
| 03-live-login-banner | 0010, 58 s | Debian tty1 login banner of the live installer | long plain-text paragraph; hostname `truenas-installer.local` marks the INSTALLER environment |
| 04-installer-console-setup | 0011, 70 s | installer menu "TrueNAS 27.0.0-RC.1 Console Setup" (1 Install/Upgrade ... 4 Shutdown System) | newt dialog, numbered options; NOT the installed system's menu (same title words!) |
| 05-choose-destination-media | 0013, 125 s | disk picker, `[ ] vda Unknown Model -- 20 GiB` | checkbox state |
| 06-choose-destination-selected | 0014, 128 s | same, `[*]` ticked | checkbox state change is only ~40 px |
| 07-erase-warning | 0015, 146 s | "This erases ALL partitions ..." Yes/No | destructive-action confirmation (Yes focused) |
| 08-auth-method | 0016, 167 s | Web UI Authentication Method | numbered menu |
| 09-password-empty | 0017, 186 s | password prompt, empty fields | input dialog |
| 10-password-filled | 0019, 211 s | 12 asterisks in both fields | masked input (value is not visible) |
| 11-progress-start-over-dialog | 0021, 236 s | progress lines `[0%] Formatting disk vda` appear at the screen bottom OVER the still-drawn password dialog | mixed screen, partial overdraw |
| 12-progress-extracting | 0102, 763 s | full-screen scrolling `[NN%] Extracting` progress, up to `[80%] Updating initramfs` | progress percentage patterns |
| 13-progress-complete | 0106, 961 s | progress ends `[100%] Installation completed successfully` | success text before the dialog |
| 14-installation-succeeded | 0107, 964 s | **Finish dialog**: "The TrueNAS installation on vda succeeded! Please reboot and remove the installation media." with only an OK button | finish signature |
| 15-uefi-tianocore-logo | 0112, 1080 s | OVMF splash during the reboot | reboot-in-progress; almost no text |
| 16-grub-installed-menu | 0118, 1252 s | installed system's GRUB: `TrueNAS Scale GNU/Linux 27.0.0-RC.1` / Advanced options / UEFI Firmware Settings | boot-into-installed signature; different GRUB font metrics than 01 |
| 17-grub-booting-installed | 0122, 1267 s | `Booting 'TrueNAS Scale GNU/Linux 27.0.0-RC.1'` / Loading Linux ... / Loading initial ramdisk ... | |
| 18-kernel-boot-log | 0123, 1276 s | kernel messages, lines wrapped at 80 columns | dense kernel log text |
| 19-console-setup-with-kernel-noise | 0240, 1850 s | **installed system Console setup** with the four management URLs and options 1-10; kernel `rcu:` messages printed over the prompt | ready signature under kernel noise |
| 20-console-setup-duplicated-redraw | 0242, 1859 s | same menu after a redraw: the top of the menu appears twice (option 1-3 block, then the full menu) and the prompt is clean | ready signature, duplicated block, clean prompt |

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

## Caveats

* The only text in frames 09-11 for the password is asterisks; the test value was `truenas-test`
  (a throwaway). `keys.jsonl` records it in clear because the capture was run without `--secret`.
* Ground truth (`ground-truth/NN-*.txt`) is the visible text transcribed by eye, line by line,
  without box-drawing characters and without layout (column offsets are not preserved). Uncertain
  glyphs are `?` (frame 19, the two characters that precede `Tasks blocked` and `(detected by`).
  Kernel timestamps and hashes in frame 18 were read from the image and may contain a mis-read digit;
  treat frame 18 as lower-trust ground truth. Dialog frames have no trailing-space precision.
* Colours were not transcribed: dialogs are black text on grey (0xAA) over blue, highlighted entries
  white on blue, GRUB cyan/white on blue.
* The full series also contains many near-identical systemd boot-message frames (seq 123-236)
  and a first, discarded std-VGA attempt (garbage framebuffer, see MANIFEST.json).
