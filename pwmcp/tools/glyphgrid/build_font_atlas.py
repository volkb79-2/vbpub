#!/usr/bin/env python3
"""Generate a font-exact glyphgrid atlas from authoritative font sources (no screenshots, no transcripts).

  build_font_atlas.py --kernel-bzimage vmlinuz-... --pf2 unicode.pf2 --ovmf OVMF_CODE_4M.fd --out fonts.atlas.json

Sources (any subset):
  --kernel-bzimage F   Linux console font ``font_vga_8x16`` (IBM VGA 8x16, glyph index -> CP437), found inside the decompressed kernel
  --pf2 F              GRUB PF2 font (e.g. /boot/grub/fonts/unicode.pf2 = GNU Unifont), rendered at ascent+descent
  --ovmf F             OVMF/EDK2 firmware image: the UEFI console font (EFI_NARROW_GLYPH 8x19), used by GRUB's EFI text console
  --psf F --hex F --bdf F   PSF1/PSF2 (kbd, console-setup), GNU Unifont .hex, BDF
  --manifest M --src-root D   a JSON manifest of distro-package / ISO font files (``fonts-console-extra.manifest.json``): each source is
                       located under D, its sha256 is CHECKED against the manifest, then it is rendered as a named atlas font; an entry
                       with ``same_as`` is not added but verified against the SAME-named font of ``--base-atlas`` (identical / subset
                       reported in ``--report``, never silently merged or hidden).  Format: see ``build_manifest``.
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


def _prov(text):
    """--*-origin 'key=value;key=value' -> dict (iso_sha256, path_in_iso, package, version ...)"""
    out = {}
    for part in (text or "").split(";"):
        if "=" in part:
            k, v = part.split("=", 1)
            out[k.strip()] = v.strip()
    return out


def _file_sha(path):
    with open(path, "rb") as f:
        return hashlib.sha256(f.read()).hexdigest()


def _manifest_font(kind, path):
    """-> FontFile for a manifest ``kind``.  kernel-* kinds read a bzImage (decompressed in memory only)."""
    if kind == "psf":
        return ff.load_psf(path)
    if kind == "pf2":
        return ff.load_pf2(path)
    if kind in ("kernel-vga-8x16", "kernel-ter-16x32"):
        blob = ff.decompress_bzimage(path)
        unimap = ff.kernel_unimap(blob)
        if kind == "kernel-vga-8x16":
            base = ff.find_vga_table(blob)
            font = ff.load_raw_table(blob, base, unimap=unimap, name="linux-font_vga_8x16",
                                     note="IBM VGA 8x16 table (256 glyphs) at 0x%x of the decompressed kernel %s; code points = kernel default unicode map" % (base, os.path.basename(path)))
        else:
            base = ff.find_ter16x32_table(blob)
            font = ff.load_raw_table(blob, base, w=16, h=32, unimap=unimap, name="linux-font_ter_16x32",
                                     note="Terminus Bold 16x32 table (256 glyphs) at 0x%x of the decompressed kernel %s; code points = kernel default unicode map" % (base, os.path.basename(path)))
        font.sha256, font.path = _file_sha(path), path   # provenance = the shipped bzImage, as for font:vga-8x16
        return font
    raise ValueError("unknown manifest font kind %r" % kind)


def build_manifest(manifest_path, src_root, atlas, base_atlas=None, repertoire_default="ascii+box", tier="extra"):
    """Build the manifest entries of one ``tier`` ("extra" = fonts that merge into the base atlas without making any base read ambiguous;
    "ambiguous" = near-duplicates of a base/extra font whose merge turns exact reads into alias sets, measured on the TrueNAS corpus; "all")
    into ``atlas``; -> report dict.  Fails (ValueError) on a sha256 mismatch.

    Manifest: {"entries": [{"atlas_font": "font:terminus-8x16", "kind": "psf|pf2|kernel-vga-8x16|kernel-ter-16x32", "file": "path under src_root",
    "sha256": "<of the file as shipped>", "repertoire": "ascii+box", "origin": {"package": ..., "version": ..., "path": ..., "licence": ...},
    "same_as": "font:vga-8x16"?}]}.
    * ``same_as``: the file is NOT added; its repertoire bitmaps are compared with that font of ``base_atlas`` and the relation (identical /
      subset / counts of bitmaps only on either side) is reported.
    * two entries with one ``atlas_font`` are the same font seen in two places: the second must be bitmap-identical (else ValueError) and is
      recorded under ``origin.also``."""
    with open(manifest_path, encoding="utf-8") as f:
        man = json.load(f)
    report = []
    for e in man["entries"]:
        if tier != "all" and not e.get("same_as") and e.get("tier", "extra") != tier:
            continue
        path = os.path.join(src_root, e["file"])
        got = _file_sha(path)
        if got != e["sha256"]:
            raise ValueError("%s: sha256 %s does not match the manifest %s" % (e["file"], got, e["sha256"]))
        rep = e.get("repertoire", repertoire_default)
        font = _manifest_font(e["kind"], path)
        name, rec = e["atlas_font"], {"atlas_font": e["atlas_font"], "file": e["file"], "sha256": got, "kind": e["kind"]}
        tmp = gg.Atlas()
        ff.build_font_atlas(tmp, "t", font, rep)
        keys = set(tmp.fonts["t"]["glyphs"])
        if e.get("same_as"):
            ref = (base_atlas or atlas).fonts.get(e["same_as"])
            if ref is None:
                raise ValueError("%s: same_as %s is not in the base atlas" % (e["file"], e["same_as"]))
            rk = set(ref["glyphs"])
            rec.update(same_as=e["same_as"], identical=keys == rk, bitmaps=len(keys), reference_bitmaps=len(rk), only_here=len(keys - rk),
                       only_reference=len(rk - keys), cell=[font.cell_w, font.cell_h], same_cell=(font.cell_w, font.cell_h) == (ref["cell_w"], ref["cell_h"]),
                       subset_of_reference=keys <= rk, origin=e.get("origin"))
            report.append(rec)
            continue
        if name in atlas.fonts:
            if set(atlas.fonts[name]["glyphs"]) != keys:
                raise ValueError("%s: atlas font %s was built from another source and these bitmaps differ" % (e["file"], name))
            atlas.fonts[name]["origin"].setdefault("also", []).append({"file": os.path.basename(path), "sha256": got, **(e.get("origin") or {})})
            rec.update(merged_into=name, identical=True, bitmaps=len(keys))
            report.append(rec)
            continue
        info = ff.build_font_atlas(atlas, name, font, rep, provenance={"provenance": e["origin"]} if e.get("origin") else None)
        rel = {}   # same-size font-file fonts that share bitmaps with this one: collisions the atlas machinery keeps reporting (cross_font_chars)
        for other, of in atlas.fonts.items():
            if other != name and (of["cell_w"], of["cell_h"]) == (font.cell_w, font.cell_h):
                common = len(set(of["glyphs"]) & keys)
                if common:
                    rel[other] = {"shared_bitmaps": common, "identical": common == len(of["glyphs"]) == len(keys)}
        rec.update(bitmaps=info["glyphs"], cell=[font.cell_w, font.cell_h], alias_sets=len(info["alias_sets"]), homoglyph_sets=len(info["homoglyph_sets"]),
                   same_size_relations=rel, note=font.note)
        report.append(rec)
    return {"entries": report}


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[1])
    ap.add_argument("--kernel-bzimage")
    ap.add_argument("--pf2")
    ap.add_argument("--ovmf")
    ap.add_argument("--psf", action="append", default=[])
    ap.add_argument("--hex")
    ap.add_argument("--bdf")
    ap.add_argument("--kernel-origin", help="provenance, e.g. 'iso_sha256=...;path_in_iso=/live/filesystem.squashfs:boot/vmlinuz-...'")
    ap.add_argument("--pf2-origin")
    ap.add_argument("--ovmf-origin", help="e.g. 'package=ovmf 2025.02-8+deb13u1;path=/usr/share/OVMF/OVMF_CODE_4M.fd;note=devcontainer package, not the production firmware'")
    ap.add_argument("--manifest")
    ap.add_argument("--src-root", help="directory the manifest 'file' paths are relative to (extracted ISO / dpkg-deb -x trees)")
    ap.add_argument("--base-atlas", help="existing atlas that 'same_as' manifest entries are compared with")
    ap.add_argument("--tier", default="extra", choices=("extra", "ambiguous", "all"), help="which manifest tier to build (default extra)")
    ap.add_argument("--report", help="write the manifest build report (identical / subset findings) here")
    ap.add_argument("--repertoire", default="ascii+box", choices=sorted(ff.REPERTOIRES))
    ap.add_argument("--out", required=True)
    a = ap.parse_args(argv)
    atlas, info = gg.Atlas(), {}
    jobs = []
    if a.kernel_bzimage:
        blob = ff.decompress_bzimage(a.kernel_bzimage)  # in memory only: nothing is written next to --out
        base = ff.find_vga_table(blob)
        unimap = ff.kernel_unimap(blob)
        font = ff.load_raw_table(blob, base, name="linux-font_vga_8x16", unimap=unimap,
                                 note="IBM VGA 8x16 table (256 glyphs) at 0x%x of the decompressed kernel %s; code points = the kernel default unicode map "
                                      "(dfont_unitable)" % (base, os.path.basename(a.kernel_bzimage)))
        font.sha256 = hashlib.sha256(open(a.kernel_bzimage, "rb").read()).hexdigest()  # provenance = the shipped bzImage
        font.path = a.kernel_bzimage
        jobs.append(("font:vga-8x16", font, _prov(a.kernel_origin)))
    if a.pf2:
        jobs.append(("font:unifont-16", ff.load_pf2(a.pf2), _prov(a.pf2_origin)))
    if a.ovmf:
        sha = hashlib.sha256(open(a.ovmf, "rb").read()).hexdigest()
        for off, vol in ff.ovmf_decompressed_volumes(a.ovmf):
            try:
                font = ff.load_edk2_narrow(vol, a.ovmf, sha, "LZMA volume at 0x%x of %s" % (off, os.path.basename(a.ovmf)))
            except ValueError:
                continue
            jobs.append(("font:edk2-8x19", font, _prov(a.ovmf_origin)))
            break
    for i, p in enumerate(a.psf):
        jobs.append(("font:psf-%d" % i, ff.load_psf(p), {}))
    if a.hex:
        jobs.append(("font:hex", ff.load_hex(a.hex), {}))
    if a.bdf:
        jobs.append(("font:bdf", ff.load_bdf(a.bdf), {}))
    for name, font, prov in jobs:
        info[name] = ff.build_font_atlas(atlas, name, font, a.repertoire, provenance={"provenance": prov} if prov else None)
        print("%-18s %dx%d  %d glyph bitmaps  %d alias sets  %d homoglyph sets  sha256 %s  (%s)" % (
            name, font.cell_w, font.cell_h, info[name]["glyphs"], len(info[name]["alias_sets"]), len(info[name]["homoglyph_sets"]), font.sha256[:16], font.note))
        for k, chars in info[name]["alias_sets"].items():
            print("    alias set:", " ".join("U+%04X" % ord(c) for c in chars), "".join(chars))
    if a.manifest:
        rep = build_manifest(a.manifest, a.src_root or ".", atlas, gg.Atlas.load(a.base_atlas) if a.base_atlas else None, a.repertoire, a.tier)
        for r in rep["entries"]:
            what = ("same_as %s identical=%s subset=%s" % (r["same_as"], r["identical"], r["subset_of_reference"]) if r.get("same_as") else
                    "merged into %s" % r["merged_into"] if r.get("merged_into") else "%d bitmaps %s" % (r["bitmaps"], r["cell"]))
            print("%-28s %s  %s" % (r["atlas_font"], what, r["file"]))
        if a.report:
            with open(a.report, "w", encoding="utf-8") as f:
                json.dump(rep, f, indent=1, sort_keys=True)
    atlas.save(a.out)
    print("wrote", a.out, os.path.getsize(a.out), "bytes")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
