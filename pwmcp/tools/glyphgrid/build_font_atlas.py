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


def font_mapping(entry):
    """{bitmap hex: (sorted repertoire characters, sorted other code points)}: the CHARACTER-to-bitmap mapping of an atlas font, not just its keys."""
    return {k: (sorted(g["c"]), sorted(g.get("x", []))) for k, g in entry["glyphs"].items()}


def mapping_differences(a, b):
    """-> (only_in_a, only_in_b, {bitmap: (a mapping, b mapping)} for bitmaps present in both with a different mapping)."""
    ma, mb = font_mapping(a), font_mapping(b)
    return (sorted(set(ma) - set(mb)), sorted(set(mb) - set(ma)), {k: (ma[k], mb[k]) for k in set(ma) & set(mb) if ma[k] != mb[k]})


NATIVE_SHARE_LIMIT = 0.5   # same-size fonts that share >= this fraction of the smaller font's bitmaps cannot be told apart on ordinary text


def hazard_analysis(fonts, order, base_names):
    """Mechanical hazards between atlas fonts (``fonts`` name -> atlas font entry; ``order`` = manifest order of the NEW fonts; ``base_names`` = fonts
    of the base atlas, which come first).  -> {name: {...}} for every new font:

    * ``native``: same cell size, another font earlier in the order (or in the base) shares >= NATIVE_SHARE_LIMIT of the smaller font's bitmaps; lists
      each such font with shared / differing-character counts.  Such a font is *ambiguous tier* (merging it turns exact reads into alias sets).
    * ``scale``: for every other font of k times the cell (k = 2..8) or 1/k times it: how many glyphs of the larger font are exact k-fold enlargements
      of a bitmap of the smaller one (``doublable``) and for how many of those the two fonts draw different characters (``differing``).  A larger
      font whose doublable glyphs differ in character is a ``scale_hazard``: an integer-upscale capture of the smaller font is indistinguishable from
      it, and ``glyphgrid.decode(unscaled=k)`` turns those cells into alias sets (needs the larger font LOADED, so it stays in the extra atlas)."""
    out = {}
    names = list(base_names) + [n for n in order if n not in base_names]
    for name in order:
        me = fonts[name]
        rec = {"native": [], "scale": []}
        for other in names:
            if other == name or other not in fonts:
                continue
            of = fonts[other]
            same = (of["cell_w"], of["cell_h"]) == (me["cell_w"], me["cell_h"])
            if same:
                if names.index(other) < names.index(name):
                    shared = set(of["glyphs"]) & set(me["glyphs"])
                    if shared and len(shared) >= NATIVE_SHARE_LIMIT * min(len(of["glyphs"]), len(me["glyphs"])):
                        diff = sum(1 for k in shared if set(of["glyphs"][k]["c"]) != set(me["glyphs"][k]["c"]))
                        rec["native"].append({"font": other, "shared": len(shared), "differing_chars": diff,
                                              "fraction_of_smaller": round(len(shared) / min(len(of["glyphs"]), len(me["glyphs"])), 3)})
                continue
            for k in range(2, 9):
                for big, small, bn, sn in ((me, of, name, other), (of, me, other, name)):
                    if (big["cell_w"], big["cell_h"]) == (small["cell_w"] * k, small["cell_h"] * k):
                        dbl = diff = 0
                        for hx, g in big["glyphs"].items():
                            red = gg.reduce_cell(bytes.fromhex(hx), big["cell_w"], big["cell_h"], k)
                            sg = small["glyphs"].get(red.hex()) if red is not None else None
                            if sg is not None:
                                dbl += 1
                                diff += set(sg["c"]) != set(g["c"])
                        if dbl:
                            rec["scale"].append({"larger": bn, "smaller": sn, "k": k, "glyphs_of_larger": len(big["glyphs"]), "doublable": dbl, "differing_chars": diff})
        rec["tier"] = "ambiguous" if rec["native"] else "extra"
        # hazard pairs that matter for this font: it is the LARGER one (its own screens are read as the smaller font), or it is the SMALLER one and the
        # larger font is a BASE font (a screen of that base font is read as this font; the base font is loaded, so the unscale guard sees it)
        rec["hazard_pairs"] = [x for x in rec["scale"] if x["differing_chars"] and (x["larger"] == name or x["larger"] in base_names)]
        rec["scale_hazard"] = bool(rec["hazard_pairs"])
        out[name] = rec
    return out


def build_manifest(manifest_path, src_root, atlas, base_atlas=None, repertoire_default="ascii+box", tier="extra"):
    """Build the manifest entries of one ``tier`` into ``atlas``; -> report dict.  Fails (ValueError) on a sha256 mismatch.

    Every non-``same_as`` font is first built into a universe together with the base atlas and the other tiers, and ``hazard_analysis`` decides its
    tier mechanically: ``extra`` fonts merge into the base atlas without making any base read ambiguous; ``ambiguous`` fonts (>= 50% of the smaller
    font's bitmaps shared with an earlier font of the same cell size) turn exact reads into alias sets, measured on the TrueNAS corpus.  If the manifest
    says another tier than the analysis, the build FAILS.  Scale hazards (integer-doubles of another font with other characters) are reported and kept
    in the extra tier on purpose: the unscale guard of ``glyphgrid.decode`` needs the larger font loaded.  ``tier="all"`` builds everything.

    Manifest: {"entries": [{"atlas_font": "font:terminus-8x16", "kind": "psf|pf2|kernel-vga-8x16|kernel-ter-16x32", "file": "path under src_root",
    "sha256": "<of the file as shipped>", "repertoire": "ascii+box", "tier": "extra|ambiguous", "origin": {...}, "same_as": "font:vga-8x16"?}]}.
    * ``same_as``: the file is NOT added; its repertoire bitmaps AND their character mapping are compared with that font of ``base_atlas`` and the
      relation (identical / subset / counts, mapping differences per bitmap) is reported.  ``identical`` needs the same keys AND the same mapping.
    * two entries with one ``atlas_font`` are the same font seen in two places: the second must have the identical mapping (else ValueError) and is
      recorded under ``origin.also``."""
    with open(manifest_path, encoding="utf-8") as f:
        man = json.load(f)
    uni, report, order, wanted_tier = gg.Atlas(), [], [], {}
    for e in man["entries"]:
        path = os.path.join(src_root, e["file"])
        got = _file_sha(path)
        if got != e["sha256"]:
            raise ValueError("%s: sha256 %s does not match the manifest %s" % (e["file"], got, e["sha256"]))
        rep = e.get("repertoire", repertoire_default)
        font = _manifest_font(e["kind"], path)
        name, rec = e["atlas_font"], {"atlas_font": e["atlas_font"], "file": e["file"], "sha256": got, "kind": e["kind"]}
        tmp = gg.Atlas()
        ff.build_font_atlas(tmp, "t", font, rep)
        if e.get("same_as"):
            ref = (base_atlas or uni).fonts.get(e["same_as"])
            if ref is None:
                raise ValueError("%s: same_as %s is not in the base atlas" % (e["file"], e["same_as"]))
            only_here, only_ref, mapdiff = mapping_differences(tmp.fonts["t"], ref)
            rec.update(same_as=e["same_as"], identical=not (only_here or only_ref or mapdiff), bitmaps=len(tmp.fonts["t"]["glyphs"]), reference_bitmaps=len(ref["glyphs"]),
                       only_here=len(only_here), only_reference=len(only_ref), mapping_differences=len(mapdiff),
                       repertoire_char_differences=sum(1 for v in mapdiff.values() if v[0][0] != v[1][0]),
                       homoglyph_list_differences=sum(1 for v in mapdiff.values() if v[0][0] == v[1][0]),
                       homoglyph_lists_here_subset_of_reference=all(set(v[0][1]) <= set(v[1][1]) for v in mapdiff.values()),
                       same_cell=(font.cell_w, font.cell_h) == (ref["cell_w"], ref["cell_h"]),
                       subset_of_reference=not only_here and all(v[0][0] == v[1][0] for v in mapdiff.values()), origin=e.get("origin"))
            report.append(rec)
            continue
        if name in uni.fonts:
            only_here, only_ref, mapdiff = mapping_differences(tmp.fonts["t"], uni.fonts[name])
            if only_here or only_ref or mapdiff:
                raise ValueError("%s: atlas font %s was built from another source and its bitmaps/characters differ (%d only here, %d only there, %d with another mapping)"
                                 % (e["file"], name, len(only_here), len(only_ref), len(mapdiff)))
            uni.fonts[name]["origin"].setdefault("also", []).append({"file": os.path.basename(path), "sha256": got, **(e.get("origin") or {})})
            rec.update(merged_into=name, identical=True, mapping_identical=True, bitmaps=len(tmp.fonts["t"]["glyphs"]))
            report.append(rec)
            continue
        info = ff.build_font_atlas(uni, name, font, rep, provenance={"provenance": e["origin"]} if e.get("origin") else None)
        order.append(name)
        wanted_tier[name] = e.get("tier", "extra")
        rec.update(bitmaps=info["glyphs"], cell=[font.cell_w, font.cell_h], alias_sets=len(info["alias_sets"]), homoglyph_sets=len(info["homoglyph_sets"]), note=font.note)
        report.append(rec)
    base_fonts = dict(base_atlas.fonts) if base_atlas else {}
    ana = hazard_analysis({**base_fonts, **uni.fonts}, order, list(base_fonts))
    for name in order:
        if ana[name]["tier"] != wanted_tier[name]:
            raise ValueError("%s: manifest tier %r but the hazard analysis says %r (%s)" % (name, wanted_tier[name], ana[name]["tier"], json.dumps(ana[name]["native"])))
        if ana[name]["scale_hazard"]:
            uni.fonts[name]["origin"]["scale_hazard"] = ana[name]["hazard_pairs"]
    for rec in report:
        if rec["atlas_font"] in ana and not rec.get("same_as") and not rec.get("merged_into"):
            rec["analysis"] = ana[rec["atlas_font"]]
    for name in order:
        if tier == "all" or ana[name]["tier"] == tier:
            atlas.fonts[name] = uni.fonts[name]
    if tier != "all":
        report = [r for r in report if r.get("same_as") or r["atlas_font"] in atlas.fonts]
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
            what = ("same_as %s identical=%s subset=%s mapping_differences=%d" % (r["same_as"], r["identical"], r["subset_of_reference"], r["mapping_differences"]) if r.get("same_as") else
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
