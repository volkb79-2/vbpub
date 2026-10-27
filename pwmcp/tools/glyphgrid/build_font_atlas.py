#!/usr/bin/env python3
"""Generate a font-exact glyphgrid atlas from authoritative font sources (no screenshots, no transcripts).

  build_font_atlas.py --kernel-bzimage vmlinuz-... --pf2 unicode.pf2 --ovmf OVMF_CODE_4M.fd --out fonts.atlas.json

Sources (any subset):
  --kernel-bzimage F   Linux console font ``font_vga_8x16`` (IBM VGA 8x16, glyph index -> CP437), found inside the decompressed kernel
  --pf2 F              GRUB PF2 font (e.g. /boot/grub/fonts/unicode.pf2 = GNU Unifont), rendered at ascent+descent
  --ovmf F             OVMF/EDK2 firmware image: the UEFI console font (EFI_NARROW_GLYPH 8x19), used by GRUB's EFI text console
  --psf F --hex F --bdf F   PSF1/PSF2 (kbd, console-setup), GNU Unifont .hex, BDF
Every font entry records the source file name, sha256 and the repertoire; characters that share one bitmap inside the
repertoire are alias sets.  Debian packages that carry these files: linux-image-* (kernel), grub-common / fonts-unifont
(unicode.pf2, unifont.hex), ovmf, console-setup-linux / kbd (PSF).
"""
import argparse
import hashlib
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import fontfiles as ff  # noqa: E402
import glyphgrid as gg  # noqa: E402


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[1])
    ap.add_argument("--kernel-bzimage")
    ap.add_argument("--pf2")
    ap.add_argument("--ovmf")
    ap.add_argument("--psf", action="append", default=[])
    ap.add_argument("--hex")
    ap.add_argument("--bdf")
    ap.add_argument("--repertoire", default="ascii+box", choices=sorted(ff.REPERTOIRES))
    ap.add_argument("--out", required=True)
    a = ap.parse_args(argv)
    atlas, info = gg.Atlas(), {}
    jobs = []
    if a.kernel_bzimage:
        blob = ff.decompress_bzimage(a.kernel_bzimage)
        base = ff.find_vga_table(blob)
        tmp = a.out + ".vmlinux.tmp"
        with open(tmp, "wb") as f:
            f.write(blob)
        font = ff.load_raw_table(tmp, base, name="linux-font_vga_8x16",
                                 note="IBM VGA 8x16 table (256 glyphs, CP437) at 0x%x of the decompressed kernel %s" % (base, os.path.basename(a.kernel_bzimage)))
        os.unlink(tmp)
        font.sha256 = hashlib.sha256(open(a.kernel_bzimage, "rb").read()).hexdigest()  # provenance = the shipped bzImage
        font.path = a.kernel_bzimage
        jobs.append(("font:vga-8x16", font))
    if a.pf2:
        jobs.append(("font:unifont-16", ff.load_pf2(a.pf2)))
    if a.ovmf:
        sha = hashlib.sha256(open(a.ovmf, "rb").read()).hexdigest()
        for off, vol in ff.ovmf_decompressed_volumes(a.ovmf):
            try:
                font = ff.load_edk2_narrow(vol, a.ovmf, sha, "LZMA volume at 0x%x of %s" % (off, os.path.basename(a.ovmf)))
            except ValueError:
                continue
            jobs.append(("font:edk2-8x19", font))
            break
    for i, p in enumerate(a.psf):
        jobs.append(("font:psf-%d" % i, ff.load_psf(p)))
    if a.hex:
        jobs.append(("font:hex", ff.load_hex(a.hex)))
    if a.bdf:
        jobs.append(("font:bdf", ff.load_bdf(a.bdf)))
    for name, font in jobs:
        info[name] = ff.build_font_atlas(atlas, name, font, a.repertoire)
        print("%-18s %dx%d  %d glyph bitmaps  %d alias sets  sha256 %s  (%s)" % (
            name, font.cell_w, font.cell_h, info[name]["glyphs"], len(info[name]["alias_sets"]), font.sha256[:16], font.note))
        for k, chars in info[name]["alias_sets"].items():
            print("    alias set:", " ".join("U+%04X" % ord(c) for c in chars), "".join(chars))
    atlas.save(a.out)
    print("wrote", a.out, os.path.getsize(a.out), "bytes")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
