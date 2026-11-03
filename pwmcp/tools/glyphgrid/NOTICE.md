# Third-party notices: font bitmaps in the glyphgrid atlases

The committed atlases contain **bitmaps derived from third-party bitmap fonts**: for each glyph in the declared repertoire, the atlas stores its pixel
rows as a hex key plus the character(s) it draws, and the font's other code points that share that bitmap (`"x"`). The bitmaps are used only for
exact comparison against image cells; they are not rendered back into artwork. Each font entry records its source file and sha256 in the atlas JSON.
The inventory below lists every font in every committed atlas, including source artifact, source version, source-file sha256, and licence attribution.
The source-file checksums are repeated in the atlas metadata (and, for the console-extra fonts, the build manifest).

<!-- committed-font-inventory:start -->
| atlas font | committed atlas | source file | source / version | source-file sha256 | licence attribution |
|---|---|---|---|---|---|
| `font:edk2-8x19` | `fonts.atlas.json` | `/usr/share/OVMF/OVMF_CODE_4M.fd` | Debian package `ovmf 2025.02-8+deb13u1`; EDK2 `EFI_NARROW_GLYPH` table | `624e06de18b4fa535e90db7160d00d3d07d206422b89999bf1e27d920264e4e0` | BSD-2-Clause-Patent (EDK2) |
| `font:unifont-16` | `fonts.atlas.json` | `/boot/grub/fonts/unicode.pf2` | GNU Unifont Regular 16 in `TrueNAS-27.0.0-RC.1.iso` | `662eb86fc1ea89635e4bba8413590b79eb714ade5a2b5a5aa3dc5cf5f53a4463` | GNU GPL-2.0-or-later with the GNU Font Embedding Exception, or SIL OFL-1.1 (dual-licensed by the Unifont project) |
| `font:vga-8x16` | `fonts.atlas.json` | `/live/filesystem.squashfs:/boot/vmlinuz-6.18.52-production+truenas` | Linux kernel `6.18.52-production+truenas` in `TrueNAS-27.0.0-RC.1.iso`; `fontdata_8x16` and default Unicode map | `56e41016311834d94da724d4ac22ddf83533c9c4c48100e7ec207f94bfd3e283` | GPL-2.0 (Linux kernel; the font data is part of the kernel source tree) |
| `font:csvga-8x16` | `fonts.console-ambiguous.atlas.json` | `/usr/share/consolefonts/Lat15-VGA16.psf.gz` | Debian `console-setup-linux 1.242~deb13u1` | `3818f6f8a805515ed24283b8fa050278af877813782b980509d22cad969634c8` | MIT/X11-style attribution for `u_vga16.bdf` (copyright 2000-2001 Dmitry Bolkhovityanov); the PSF-to-BDF derivation is not stated per size |
| `font:fixed-8x16` | `fonts.console-ambiguous.atlas.json` | `/usr/share/consolefonts/Uni3-Fixed16.psf.gz` | Debian `console-setup-linux 1.242~deb13u1` | `4e5d96fa087381e04c66c98df1411d629736ad040a957534377a93d860a4696a` | Attribution unresolved: misc-fixed BDF sources are called public domain in snippet F; the 8x16 BDF has a separate permissive notice in snippet S; the PSF-to-BDF mapping is inferred, not stated |
| `font:terminus-bold-16x32` | `fonts.console-ambiguous.atlas.json` | `/usr/share/consolefonts/Uni3-TerminusBold32x16.psf.gz` | Debian `console-setup-linux 1.242~deb13u1` | `6c9390e7a8101e3600b5e007333eef482154ea21846a0ac3b17672206a5406a8` | SIL OFL-1.1, (c) 2010 Dimitar Toshkov Zhekov, Reserved Font Name "Terminus Font"; supported by snippet T |
| `font:csvga-16x28` | `fonts.console-extra.atlas.json` | `/usr/share/consolefonts/Lat15-VGA28x16.psf.gz` | Debian `console-setup-linux 1.242~deb13u1` | `c4a73dbbe3c37a934a598500a1288f601ffa4d1343ab38a4e019969cd5ed0991` | MIT/X11-style attribution for `u_vga16.bdf` (copyright 2000-2001 Dmitry Bolkhovityanov); the PSF-to-BDF derivation is not stated per size |
| `font:csvga-16x32` | `fonts.console-extra.atlas.json` | `/usr/share/consolefonts/Lat15-VGA32x16.psf.gz` | Debian `console-setup-linux 1.242~deb13u1` | `1765a808e05c9b1ca88b39aef1777b5a330e83576ff6b9dadc54b0df44d0f61a` | MIT/X11-style attribution for `u_vga16.bdf` (copyright 2000-2001 Dmitry Bolkhovityanov); the PSF-to-BDF derivation is not stated per size |
| `font:csvga-8x14` | `fonts.console-extra.atlas.json` | `/usr/share/consolefonts/Lat15-VGA14.psf.gz` | Debian `console-setup-linux 1.242~deb13u1` | `fe3e804ea8e0fbd37b2ed6b462958bf2887fa7f91fa130f9d61340923613e08a` | MIT/X11-style attribution for `u_vga16.bdf` (copyright 2000-2001 Dmitry Bolkhovityanov); the PSF-to-BDF derivation is not stated per size |
| `font:csvga-8x8` | `fonts.console-extra.atlas.json` | `/usr/share/consolefonts/Lat15-VGA8.psf.gz` | Debian `console-setup-linux 1.242~deb13u1` | `6c5cf355632e12e8b91682957dc5a5ae55d7ec9247f70123c75d31c36696641d` | MIT/X11-style attribution for `u_vga16.bdf` (copyright 2000-2001 Dmitry Bolkhovityanov); the PSF-to-BDF derivation is not stated per size |
| `font:fixed-8x13` | `fonts.console-extra.atlas.json` | `/usr/share/consolefonts/Uni3-Fixed13.psf.gz` | Debian `console-setup-linux 1.242~deb13u1` | `74b917d61759843a3df65be96a0fe4aa6978840dca437d90a044dbc7b17a68a7` | Attribution unresolved: misc-fixed BDF sources are called public domain in snippet F; the PSF-to-BDF mapping is inferred, not stated |
| `font:fixed-8x14` | `fonts.console-extra.atlas.json` | `/usr/share/consolefonts/Uni3-Fixed14.psf.gz` | Debian `console-setup-linux 1.242~deb13u1` | `df034303323f2e6c396120f28571386c63fe789b7286523d3faba5eb030123ba` | Attribution unresolved: misc-fixed BDF sources are called public domain in snippet F; the PSF-to-BDF mapping is inferred, not stated |
| `font:fixed-8x15` | `fonts.console-extra.atlas.json` | `/usr/share/consolefonts/Uni3-Fixed15.psf.gz` | Debian `console-setup-linux 1.242~deb13u1` | `707d0fb26c2240241b7af22ace56013812e59ce3ba4d68637af90694748232e2` | Attribution unresolved: misc-fixed BDF sources are called public domain in snippet F; the PSF-to-BDF mapping is inferred, not stated |
| `font:fixed-8x18` | `fonts.console-extra.atlas.json` | `/usr/share/consolefonts/Uni3-Fixed18.psf.gz` | Debian `console-setup-linux 1.242~deb13u1` | `bd1d6b7d2864c273ef3cb3cc7f26f288829eb42176672359122cb0b123ca36a1` | Attribution unresolved: misc-fixed BDF sources are called public domain in snippet F; the PSF-to-BDF mapping is inferred, not stated |
| `font:linux-ter16x32` | `fonts.console-extra.atlas.json` | `/install.amd/vmlinuz` | Debian 13.7.0 amd64 netinst ISO; Linux kernel `6.12.107+deb13-amd64` | `2b2358b37674d2505350528875bb17afae2a36522a9e8a9417eaca65a7da0e08` | GPL-2.0 for the kernel source file `lib/fonts/font_ter16x32.c` (header `SPDX-License-Identifier: GPL-2.0`, https://raw.githubusercontent.com/torvalds/linux/master/lib/fonts/font_ter16x32.c, fetched 2026-11-03; the file names the font `TER16x32` and carries no copyright or Terminus/OFL statement). Terminus Bold glyph origin and its OFL-1.1 attribution remain unverified for this table (evidence still needed: an upstream statement that the kernel table is derived from Terminus) |
| `font:terminus-10x18` | `fonts.console-extra.atlas.json` | `/usr/share/consolefonts/Uni3-Terminus18x10.psf.gz` | Debian `console-setup-linux 1.242~deb13u1` | `5e4f30fddf41903658003c2fb45e4bb7d870fb753000953f87ee51f46e8690b4` | SIL OFL-1.1 (Terminus Font), confirmed upstream for the whole package incl. size 10x18 (https://terminus-font.sourceforge.net/, fetched 2026-11-03: "licensed under the SIL Open Font License, Version 1.1", sizes 6x12 ... 16x32 listed); still UNVERIFIED for this exact file: the Debian `copyright.fonts` block does not name the u18 source, and the atlas JSON provenance (a hash-pinned fixture, not regenerated) still says UNVERIFIED |
| `font:terminus-10x20` | `fonts.console-extra.atlas.json` | `/usr/share/consolefonts/Uni3-Terminus20x10.psf.gz` | Debian `console-setup-linux 1.242~deb13u1` | `51a4aa8a1bbff36fa7ae6b5bd905a9308a64fbf57cfb01c52fe5396a8853d27c` | SIL OFL-1.1, (c) 2010 Dimitar Toshkov Zhekov, Reserved Font Name "Terminus Font"; supported by snippet T |
| `font:terminus-11x22` | `fonts.console-extra.atlas.json` | `/usr/share/consolefonts/Uni3-Terminus22x11.psf.gz` | Debian `console-setup-linux 1.242~deb13u1` | `817a7aecd5414410e43dd41320539473b617fe217a332ba25f0f219a40011165` | SIL OFL-1.1 (Terminus Font), confirmed upstream for the whole package incl. size 11x22 (https://terminus-font.sourceforge.net/, fetched 2026-11-03: "licensed under the SIL Open Font License, Version 1.1", sizes 6x12 ... 16x32 listed); still UNVERIFIED for this exact file: the Debian `copyright.fonts` block does not name the u22 source, and the atlas JSON provenance (a hash-pinned fixture, not regenerated) still says UNVERIFIED |
| `font:terminus-12x24` | `fonts.console-extra.atlas.json` | `/usr/share/consolefonts/Uni3-Terminus24x12.psf.gz` | Debian `console-setup-linux 1.242~deb13u1` | `efc268dcd69ef86e3cd5f5a0bfbf4d0e7ae618334e27198c605b7cd00315616b` | SIL OFL-1.1, (c) 2010 Dimitar Toshkov Zhekov, Reserved Font Name "Terminus Font"; supported by snippet T |
| `font:terminus-14x28` | `fonts.console-extra.atlas.json` | `/usr/share/consolefonts/Uni3-Terminus28x14.psf.gz` | Debian `console-setup-linux 1.242~deb13u1` | `cd0975a4639fdfce20a24607d458ff32d181805ed71101438f2e59c94156f799` | SIL OFL-1.1, (c) 2010 Dimitar Toshkov Zhekov, Reserved Font Name "Terminus Font"; supported by snippet T |
| `font:terminus-16x32` | `fonts.console-extra.atlas.json` | `/usr/share/consolefonts/Uni3-Terminus32x16.psf.gz` | Debian `console-setup-linux 1.242~deb13u1` | `a13bd9ced51d694bd0f9c40592dd18e747a441616ed6578d797ee8e33a2e2f69` | SIL OFL-1.1, (c) 2010 Dimitar Toshkov Zhekov, Reserved Font Name "Terminus Font"; supported by snippet T |
| `font:terminus-6x12` | `fonts.console-extra.atlas.json` | `/usr/share/consolefonts/Uni3-Terminus12x6.psf.gz` | Debian `console-setup-linux 1.242~deb13u1` | `d8fec3bade8da7baaf3ff7b1635fe62e2cdbc0914518bdeedb8b66b8f2ee448a` | SIL OFL-1.1, (c) 2010 Dimitar Toshkov Zhekov, Reserved Font Name "Terminus Font"; supported by snippet T |
| `font:terminus-8x14` | `fonts.console-extra.atlas.json` | `/usr/share/consolefonts/Uni3-Terminus14.psf.gz` | Debian `console-setup-linux 1.242~deb13u1` | `27381672f14435611ca9bf5d017f85da5bcf4c2f3d19b14623268c06cfd1d1da` | SIL OFL-1.1, (c) 2010 Dimitar Toshkov Zhekov, Reserved Font Name "Terminus Font"; supported by snippet T |
| `font:terminus-8x16` | `fonts.console-extra.atlas.json` | `/usr/share/consolefonts/Uni3-Terminus16.psf.gz` | Debian `console-setup-linux 1.242~deb13u1`; also byte-identical in grml-full 2026.09 | `2bc51a16e2bbd3aee7463d8318935f819684579127defff2ef23f092f98c9420` | SIL OFL-1.1, (c) 2010 Dimitar Toshkov Zhekov, Reserved Font Name "Terminus Font"; supported by snippet T |
| `font:terminus-bold-12x24` | `fonts.console-extra.atlas.json` | `/usr/share/consolefonts/Uni3-TerminusBold24x12.psf.gz` | Debian `console-setup-linux 1.242~deb13u1` | `e939fdac22cd698dd1e57d29ef38e0e366f4dee632d65b794996bb4250f80e1b` | SIL OFL-1.1, (c) 2010 Dimitar Toshkov Zhekov, Reserved Font Name "Terminus Font"; supported by snippet T |
| `font:terminus-bold-8x16` | `fonts.console-extra.atlas.json` | `/usr/share/consolefonts/Uni3-TerminusBold16.psf.gz` | Debian `console-setup-linux 1.242~deb13u1` | `7c580d03f9ea4e0da24b1db042a4f482d0d021c1f40a5c948f51696d2fbe7fe5` | SIL OFL-1.1, (c) 2010 Dimitar Toshkov Zhekov, Reserved Font Name "Terminus Font"; supported by snippet T |
<!-- committed-font-inventory:end -->

## Console-extra atlas (2026-10-27): installer / rescue / console-setup fonts

`pwmcp/tests/fixtures/glyphgrid/fonts.console-extra.atlas.json` (and `fonts.console-ambiguous.atlas.json`) are built by
`build_font_atlas.py --manifest fonts-console-extra.manifest.json`; the manifest lists every source file with its sha256 (the build REFUSES a mismatch),
each atlas font records file name, sha256, package/version/path and licence under `origin.provenance`. Sources (all fetched from official locations, checksums
of the downloads verified, **GPG signatures NOT verified**):

* TrueNAS 27.0.0-RC.1 ISO, sha256 `515a0252e72f1a6c86d603fa4d1661333029fe9a4804d0a7a28d24fe4f63498e` (base VGA and Unifont sources).
* Debian 13.7.0 amd64 netinst ISO, sha256 `a7ef94ac2fb9a7fec454552abd629b7cc9d5155c886165a45649f5ce6167e355` (matches `SHA256SUMS` on cdimage.debian.org/debian-cd/current).
* grml-full 2026.09 amd64 ISO, sha256 `76e8019ff1b68ddb3e49cecb1ea35ecead9e0f1c1ba22c0e819c9b13de939e72` (matches `grml-full-2026.09-amd64.iso.sha256` on download.grml.org).
* Debian trixie package `console-setup-linux 1.242~deb13u1` (`apt-get download`, deb sha256 `c96eb9386cd8d65dceb0a58726dcef7cf7a0aedca7f514139c225ab391baca27`).

The inventory distinguishes verified text from unresolved attribution. A named licence does not mean that bitmap extraction has had legal review. The
full evidence and limitations follow; unresolved attribution stays explicitly marked and must not be read as a verified licence claim.

The repeated package version in each row is intentional: every committed font has its own attributable source/version/licence line. Source checksums are the
input-file checksums, not the atlas JSON checksums. The extra `font:terminus-8x16` is also byte-identical to the grml 2026.09 cached console font; its
primary committed source and checksum are from the Debian package shown in the inventory.

Checkable evidence (package `keyboard-configuration 1.242~deb13u1`, deb sha256 `f8b614f910166a74db8b4fe1d28a1b464dc4f4f726ed60ed9cc321e9eff8fed1`; `console-setup-linux`
carries its docs as a symlink to it): `/usr/share/doc/keyboard-configuration/copyright.fonts.gz` sha256 `e51cd6608b3d3c3e3e71669532a4c59653a8b84060a47651046eb8556b69ee88`
(decompressed text `adeb95d45c8f1b944dc21032e74ea2e5636e0f4a34a63783fd37044af9852dcb`). Snippets by decompressed line range (`zcat copyright.fonts.gz | sed -n 'A,Bp' | sha256sum`):

* **V**, lines 5-8, sha256 `0cb77c55d33e0f6131945090234225a389b93b333c0264aff697c7950d37721a`: `== u_vga16.bdf`, a blank line, `Copyright © 2000, 2001 by Dmitry Bolkhovityanov.  All Rights Reserved.` (the MIT/X11 permission text follows at line 9).
* **F**, lines 32-38, sha256 `016f4e3973071399c2b7ad978dc64ecd693a4764f4ee4ed628643117071beaf7`: `== 7x14B.bdf ... 8x13.bdf ... 9x15.bdf ... 9x18.bdf ...` / `Public domain.  Donated (in unspecified order) by H. Kagotani ...`.
* **S**, lines 48-52, sha256 `25ca3792e8c0e6dfb5b37bfcacce81cc96f3da0ead6aff02fd530acb683b5ed0`: `== 8x16-IL2.bdf 8x16c.bdf` / `Copyright 1987, 1988, 1989 by Sony Corp.` / `Copyright 1996 BIZNET Poland, Inc.` / `Copyright 2000 by Dmitry Yu. Bolkhovityanov` ...
* **T**, lines 114-122, sha256 `1367be4bddcf2a7afb5a251b10805697694c8e803d54cd306cd36ce7ac4e598d`: `== ter-u12b.bdf, ter-u12n.bdf, ... ter-u32b.bdf, ter-u32n.bdf` / `Terminus Font is licensed under the SIL Open Font License, Version 1.1.` / `Copyright (c) 2010 Dimitar Toshkov Zhekov,` / `with Reserved Font Name "Terminus Font".`
* **Upstream check, 2026-11-03** (https://gitlab.freedesktop.org/xorg/font/misc-misc/-/raw/master/COPYING, the X.Org `misc-misc` package that holds the misc-fixed BDF sources): the whole file reads `"Public domain font.  Share and enjoy."`. This supports "public domain" for the BDF sources behind the `font:fixed-*` rows; it does not state how the Debian PSF files `Uni3-Fixed*.psf` were derived from them, so those rows stay "attribution unresolved" (missing: the Debian/bdf2psf derivation statement). The u_vga16 (MIT/X11-style), Sony/BIZNET 8x16 and kernel-font rows were not re-checked upstream.
* **Upstream check, 2026-11-03** (Terminus, https://terminus-font.sourceforge.net/): the package is under SIL OFL-1.1 (full text shipped as `OFL.TXT`) and includes sizes 6x12, 8x14, 8x16, 10x18, 10x20, 11x22, 12x24, 14x28, 16x32; install scripts are GPL-2.0-or-later. The OFL-1.1 attribution of the 10x18 and 11x22 rows is therefore confirmed at package level; the `OFL.TXT` file itself and a per-file match to the Debian PSFs were not fetched.
* main `copyright` (sha256 `9aed29abee9d52d714b393c54a3394dba936694091a984b6d367fbb68cc5547c`) line 14: `All console fonts are public domain by nature.`

"Also seen" records (not added again; `fonts.console-extra.report.json` has the numbers; `same_as` now compares the repertoire bitmaps AND the character mapping per bitmap):
Debian kernel `vmlinuz` (`2b2358b3...`) and grml kernel `/boot/grmlfullamd64/vmlinuz` (`5e2698f8628f2dba...`): identical to `font:vga-8x16` (keys and mapping).
grml GRUB `/boot/grub/fonts/unicode.pf2` (`dc6d6a18148cef2a...`, 57086 glyphs): same repertoire bitmaps and repertoire characters as `font:unifont-16` but **not identical**: for 1 bitmap
(`m`) its list of other code points that draw it is shorter (U+A773 missing: an older/newer Unifont release). Debian GRUB `/boot/grub/font.pf2` (`556e49a42fc9c8b1...`, "GNU Unifont Regular 16",
159 glyphs): a SUBSET: 123 of the 258 repertoire bitmaps (the box-drawing characters are missing), the repertoire characters equal, and for 56 of them the homoglyph lists are shorter (fewer code points in the file).

Tiers are decided mechanically by `build_font_atlas.hazard_analysis` and the build FAILS when the manifest tier disagrees: a font that shares >= 50% of the smaller font's bitmaps with an earlier same-size font
(or a base font) is `ambiguous` (merged with the base atlas it turns exact reads into alias sets: font-exact cells 10346 -> 7083 and alias 0 -> 3263 on the TrueNAS corpus, 19 frames with a grid out of 20).
The three ambiguous fonts: `font:fixed-8x16` (all 110 repertoire bitmaps are Unifont's, 16 with another character), `font:csvga-8x16` (105 of 110 equal the kernel VGA font), `font:terminus-bold-16x32` (108 of 110 equal the
kernel's `font_ter_16x32`). Use them only for a target known to show them. The `extra` set merged into the base atlas leaves every cell count and every cell reading of the corpus unchanged (font-exact 10346, alias 0,
homoglyph 1886; `font_proven` stays True on all 19 frames, the runner-up share rises to 0.2-0.30 because Terminus Bold shares digits/letters with VGA, which does not block proof since it draws them alike).

Scale hazards (integer upscales): `probe_scale` undoes the LARGEST crisp k in (8, 6, 5, 4, 3, 2), and a k-fold enlargement of the cell may be the native screen of a font of cell d*cell (d a divisor
of k, d >= 2) shown at zoom k/d: the exact 2x enlargement of `font:csvga-8x16` IS the real 16x32 screen of `font:csvga-16x32`, and the same screen at 2x browser zoom is a 4x enlargement. Measured by the
analysis: `csvga-16x32` doubles 105 `font:vga-8x16` bitmaps with 16 different characters and 12 Unifont bitmaps with 11 different ones (heavy box lines read as light ones). Contract of `glyphgrid.decode`
for an undone k > 1 (carried on `ex.grid["unscaled"]` by `prepare`, so `verify_fonts`/`eval_glyphgrid` cannot bypass it):
* a bitmap that a loaded font of ANY cell d*cell (d | k) draws as another character is an alias set, never F/H (union over every divisor: k=4: 2,4; k=6: 2,3,6; k=8: 2,4,8);
* `res["scale_unverified"] = k` and `res["scale_guard_fonts"]` (the larger fonts that were checked) are always present for k > 1;
* unless a font exists for EVERY divisor, the remaining F/H cells become class `z` (`scale_capped`): the character is shown but it is NOT actionable (`find_phrase`/`region`/`strict_match` refuse it).
  With base+extra loaded that is satisfied for k=2 (16x32 fonts) only; k=3,4,6,8 captures are capped, which includes legitimate browser-zoomed 8x16 captures: non-actionable BY DESIGN, because the loaded
  atlas cannot establish that no larger font exists. `strict_match(res, phrase, trust_scale=True)` (or `glyphgrid.trust_scale(res)` for `find_phrase`/`region`) is the explicit opt-in; the result records
  `trust_scale` and `scale_unverified`.
Scale-hazard fonts stay in the extra atlas (the guard needs the larger font LOADED; `origin.scale_hazard` records the pairs, also when a NEW smaller font is doubled by a larger BASE font). The real
Terminus 16x32 fonts have only 1-5 doublable glyphs per smaller font, all drawn with the same characters (0 differing).

### Regenerating the atlas at image build time instead of committing bytes (proposal, not implemented)

The sidecar image (`containers/ocr/Dockerfile`, `debian:trixie-slim` pinned by digest) would, in a **throw-away build stage**, `apt-get install` (or
`apt-get download` + `dpkg-deb -x`) the pinned packages `console-setup-linux=<version>`, `grub-common`/`fonts-unifont` (`unicode.pf2`), `linux-image-<abi>-amd64`
(kernel fonts) and `ovmf` (EDK2 font), run `python3 tools/glyphgrid/build_font_atlas.py --manifest fonts-console-extra.manifest.json --src-root /stage
--out /opt/pwmcp-ocr/fonts.atlas.json`, and copy only the resulting JSON plus this NOTICE into the final stage. The manifest's sha256 per file turns a
changed package build into a loud build failure (re-vet and re-pin, per the 14-day freshness rule) instead of a silently different atlas. ISO-only sources
(TrueNAS kernel/GRUB, Debian ISO kernel) have no package: either pin the ISO URL + sha256 in the build (download in the throw-away stage) or keep those few
atlases committed. The repository then carries manifest + builder, not bitmaps; the licence review question below shrinks to "what does the built image
have to ship", which this NOTICE answers per font.

Obligations and status:

* Shipping an atlas (for example inside the pwmcp OCR sidecar image) means redistributing data derived from the fonts in the inventory: this notice
  (names, licences, sources, sha256) must travel with it, and the source files can be re-obtained from the ISO / Debian package named above.
* The glyph data is used as a lookup key, not as a font: whether pixel-for-pixel derived bitmap tables are a derivative work of the font, and what
  each listed licence requires of a distribution that contains them, **has not been legally reviewed**. Do not treat this file as legal advice; get a licence review before shipping the
  atlas in a distributed image. Regenerating the atlas at image build time from the distro packages (instead of committing it) avoids carrying the
  bytes in this repository, at the price of needing those packages at build time.
* `GLYPHGRID-FINDINGS.md` and the tests use cropped screenshots of the TrueNAS installer and of GRUB/OVMF screens purely as test fixtures of program output.
