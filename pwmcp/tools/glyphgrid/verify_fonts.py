#!/usr/bin/env python3
"""Verify font identity from screenshots WITHOUT transcripts, then audit the transcripts against the font files.

For every frame: grid + phase are found by exact matches against the font-file atlas only; the share of ink cells
whose normalised bitmap equals some glyph of the candidate font is the identification evidence.  Any matching cell is read with the
character FROM THE FONT FILE.  The human transcripts are used only afterwards, to list disagreements.

  verify_fonts.py --atlas fonts.atlas.json --sources kernel.bzimage unicode.pf2 OVMF_CODE_4M.fd \
      --corpus CORPUS_DIR [--series DIR] [--corrections ground-truth-corrections.json] --out font-verify.json
"""
import argparse
import collections
import glob
import json
import os
import sys
import time
import unicodedata

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import fontfiles as ff  # noqa: E402
import glyphgrid as gg  # noqa: E402
import learn_atlas as la  # noqa: E402


def full_font_keys(bzimage, pf2, ovmf):
    """key -> [codepoints] over the COMPLETE font files (to explain cells outside the repertoire)."""
    out = {}
    jobs = []
    if bzimage:
        blob = ff.decompress_bzimage(bzimage)
        jobs.append(("font:vga-8x16", ff.load_raw_table(blob, ff.find_vga_table(blob), unimap=ff.kernel_unimap(blob))))
    if pf2:
        jobs.append(("font:unifont-16", ff.load_pf2(pf2)))
    if ovmf:
        import hashlib
        sha = hashlib.sha256(open(ovmf, "rb").read()).hexdigest()
        for off, vol in ff.ovmf_decompressed_volumes(ovmf):
            try:
                jobs.append(("font:edk2-8x19", ff.load_edk2_narrow(vol, ovmf, sha, "x")))
                break
            except ValueError:
                continue
    for name, font in jobs:
        d = out.setdefault(name, {})
        for g in font.glyphs:
            key = ff.render_cell(font, g)
            for cp in g[0]:
                d.setdefault(key, []).append(cp)
    return out


def read_frame(path, atlas):
    im, probe, g = gg.prepare(path, atlas=atlas)
    if im is None:
        return None, "resampled"
    if g["status"] != "ok":
        return None, "no_grid"
    ex, _ = gg.choose_extraction(im, g, atlas)
    res = gg.decode(ex, atlas)
    return (ex, res), "ok"


def cell_stats(ex, res):
    w, h = ex.grid["cell_w"], ex.grid["cell_h"]
    glyph = font_exact = alias = box_unmatched = other_unmatched = homo = cursor = homo_refused = 0
    unmatched = collections.Counter()
    for r, row in enumerate(ex.rows):
        for c, cell in enumerate(row):
            if cell.kind != "glyph":
                continue
            glyph += 1
            st = res["conf"][r][c]
            if st in (gg.CONF_FONT, gg.CONF_HOMO):
                font_exact += 1
                if st == gg.CONF_HOMO:
                    homo += 1
                    if not gg._benign_latin(res["rows"][r][c], res["aliases"]["%d,%d" % (r, c)][1:]):
                        homo_refused += 1   # refused by the default 'latin-only' policy (Cyrillic/Greek/symbol lookalike exists)
                cursor += res["flags"][r][c] == "c"
            elif st == gg.CONF_ALIAS:
                alias += 1
            elif gg.boxlike(cell.key, w, h):
                box_unmatched += 1
                unmatched[cell.key.hex()] += 1
            else:
                other_unmatched += 1
                unmatched[cell.key.hex()] += 1
    return dict(ink_cells=glyph, font_exact=font_exact, homoglyph_cells=homo, homoglyph_refused_latin_only=homo_refused, cursor_flagged=cursor, alias=alias, unmatched_boxlike=box_unmatched,
                unmatched_text=other_unmatched, font_share=res.get("font_share"), screen_status=res.get("status")), unmatched


def audit(ex, res, gt_lines, atlas):
    """Compare transcript lines with the FONT reading.  Placement uses only the blank/ink pattern of the line plus
    agreement with the font reading (best-agreeing window, >= 50%); a line made only of punctuation (a rule such as the title
    underline) is placed by pattern alone so that a wrong transcript is reported as a disagreement, not silently dropped."""
    pats = ["".join("0" if c.kind == "blank" else "1" for c in row) for row in ex.rows]  # ink/blank only: no box heuristics
    agree = dis = unk = alias_ok = alias_bad = 0
    diffs, unplaced = [], []
    r0 = 0
    for line in gt_lines:
        tokens = line.split()
        if not tokens:
            continue
        text = "".join(tokens)
        best, first = None, None
        punct_only = not any(ch.isalnum() for ch in text)
        for r in range(r0, len(pats)):
            for idx in la._windows(pats[r], tokens):
                ag = sum(1 for ch, ci in zip(text, idx) if res["conf"][r][ci] in (gg.CONF_FONT, gg.CONF_HOMO) and res["rows"][r][ci] == ch
                         or res["conf"][r][ci] == gg.CONF_ALIAS and ch in res["aliases"].get("%d,%d" % (r, ci), []))
                score = ag / len(text)
                if first is None:
                    first = (score, r, idx)
                if best is None or score > best[0] + 1e-9:
                    best = (score, r, idx)
            if best and best[0] >= 0.5:
                break
            if punct_only and first:
                best = first   # a rule line is placed by pattern alone, at its first occurrence
                break
        if best is None or (best[0] < 0.5 and not punct_only):
            unplaced.append(line.strip())
            continue
        _, r, idx = best
        r0 = r + 1
        for i, (ch, ci) in enumerate(zip(text, idx)):
            if ch == " " or (ch == "?" and i == 0 and len(text) > 1 and tokens[0].startswith("?")):
                continue
            st = res["conf"][r][ci]
            if st in (gg.CONF_FONT, gg.CONF_HOMO):
                if res["rows"][r][ci] == ch:
                    agree += 1
                else:
                    dis += 1
                    diffs.append({"row": r, "col": ci, "transcript": ch, "font": res["rows"][r][ci]})
            elif st == gg.CONF_ALIAS:
                al = res["aliases"].get("%d,%d" % (r, ci), [])
                if ch in al:
                    alias_ok += 1
                else:
                    alias_bad += 1
                    diffs.append({"row": r, "col": ci, "transcript": ch, "font_aliases": al})
            else:
                unk += 1
    return dict(agree=agree, disagree=dis, alias_consistent=alias_ok, alias_inconsistent=alias_bad, not_font_exact=unk,
                diffs=diffs, unplaced_lines=unplaced)


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--atlas", required=True)
    ap.add_argument("--bzimage")
    ap.add_argument("--pf2")
    ap.add_argument("--ovmf")
    ap.add_argument("--corpus")
    ap.add_argument("--series")
    ap.add_argument("--corrections")
    ap.add_argument("--out", required=True)
    a = ap.parse_args(argv)
    atlas = gg.Atlas.load(a.atlas)
    full = full_font_keys(a.bzimage, a.pf2, a.ovmf) if (a.bzimage or a.pf2 or a.ovmf) else {}
    corr = la.load_corrections(a.corrections)
    out = {"corpus": {}, "series": {}}
    explain = collections.Counter()
    explain_names = {}

    def explain_unmatched(font, unmatched, w, h):
        for hx, n in unmatched.items():
            cps = full.get(font, {}).get(bytes.fromhex(hx))
            explain[(font, hx)] += n
            if cps:
                explain_names[(font, hx)] = ["U+%04X %s" % (cp, unicodedata.name(chr(cp), "?")) for cp in cps[:3]]

    if a.corpus:
        for base, fp, gp in la.load_corpus(a.corpus):
            got, why = read_frame(fp, atlas)
            rec = {"status": why}
            if got:
                ex, res = got
                st, unm = cell_stats(ex, res)
                rec.update(font=res["font"], status=res["status"], origin=[ex.grid["origin_x"], ex.grid["origin_y"]], **st)
                explain_unmatched(res["font"], unm, ex.grid["cell_w"], ex.grid["cell_h"])
                if gp:
                    rec["audit_original"] = audit(ex, res, la.read_gt(gp), atlas)
                    if base in corr:
                        rec["audit_corrected"] = audit(ex, res, la.read_gt(gp, corr, base), atlas)
            out["corpus"][base] = rec
            print(base, rec.get("font"), rec.get("ink_cells"), rec.get("font_exact"), (rec.get("audit_original") or {}).get("disagree"), flush=True)
    if a.series:
        for fp in sorted(glob.glob(os.path.join(a.series, "*.png"))):
            t0 = time.time()
            got, why = read_frame(fp, atlas)
            rec = {"status": why}
            if got:
                ex, res = got
                st, unm = cell_stats(ex, res)
                rec.update(font=res["font"], **st)
                explain_unmatched(res["font"], unm, ex.grid["cell_w"], ex.grid["cell_h"])
            out["series"][os.path.basename(fp)] = rec
            if len(out["series"]) % 20 == 0:
                print(len(out["series"]), time.time() - t0, flush=True)
    out["corrections"] = [list(n) for n in la.CORRECTION_NOTES]
    out["unmatched_bitmaps"] = [{"font": f, "hex": hx, "cells": n, "font_file_glyph": explain_names.get((f, hx))}
                                for (f, hx), n in explain.most_common(40)]
    with open(a.out, "w", encoding="utf-8") as f:
        json.dump(out, f, indent=1, ensure_ascii=False, default=str)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
