# glyphgrid: deterministic glyph-grid matching for fixed-font consoles

Prototype of the `glyphgrid` engine for the pwmcp OCR sidecar (`pwmcp/containers/ocr/ocr_service.py`
registers it as unavailable; this tool is written to be dropped in later). Pure function, stdlib + Pillow
only, no OCR model. Honest numbers and limits: `GLYPHGRID-FINDINGS.md`.

Idea: a VGA/framebuffer console draws a FIXED bitmap font on an exact cell grid. So split the frame into
cells, normalise each cell to a 1-bit foreground bitmap, and look the bitmap up in an atlas of known glyphs.
Every cell is then either a known glyph (exact match) or an explicit UNKNOWN (`�`); never a guess.

## Safety contract

**Three evidence classes.** (1) `F` font-exact: the character comes from an authoritative font FILE (sha256 and repertoire recorded in the atlas;
`build_font_atlas.py`, `fontfiles.py`), no transcript involved; (2) `e` learned from screenshots and confirmed by >= 2 independent screen groups;
(3) everything else (`s` single-source, `u` unknown, `a` alias set, `x` conflict, `f` fill, `m` multicolour): not evidence. A keystroke decision may rely
on classes (1) and (2) only, and ambiguity sets (`a`: several characters share one bitmap in the font; the read returns the set in `res["aliases"]`, never a pick)
are non-unique. Screenshots only VERIFY which font a screen uses (`verify_fonts.py`: share of ink cells that equal a font-file glyph; 99.9% on the TrueNAS
corpus). The details below describe the learned-atlas rules (class 2) and the common limits.

* **UNKNOWN is never a guess.** The nearest atlas glyph and its Hamming distance are evidence in `unknown_cells`, not output. Tolerant
  matching (`max_hamming > 0`) is OFF by default, needs a margin over the second-best different character and reports the atlas minimum
  pairwise distance (warning when `max_hamming` is at least half of it).
* **Exact needs independent evidence.** A bitmap reads `e` only if >= 2 independent screen groups confirmed its label (`Atlas.groups`;
  near-duplicate frames are one group). One screen (or two copies of it) gives `s` single-source: not exact, `would_read` in the evidence.
* **Evidence floor.** The chosen atlas font must read >= 8 ordinary (not bar/box-like) cells exactly and >= 25% of them, else
  `status: "no_font"` and every cell is UNKNOWN. A screen that is merely a run of underscores/rules never counts as a font match.
* **Consumers that send keystrokes** match on exact cells only, in a region with zero bad cells (`region()` counts unknown/single/fill/
  flagged cells; `find_phrase()` never matches across them), and treat anything else as indeterminate.
  Cell flags (`res["flags"]`): `r` rule-line (>= 4 identical bar-like cells with no text beside them, e.g. a title underline), `c` cursor
  candidate; both are excluded from `find_phrase`/`region` by default. Status `f` (fill) is a uniform cell next to text on a different
  background: it is not a space.
* **The scale probe CANNOT prove the capture is 1:1.** It refuses blended/resampled edges (`status: "resampled"`) and undoes crisp integer
  nearest-neighbour upscales. Crisp non-integer scaling, integer scaling with an odd offset/letterbox, or a canvas of another size than the guest
  framebuffer pass it; only the evidence floor (no atlas font matches) contains those, which is a property of the atlas, not a guarantee.
  The capture path must deliver the intrinsic bitmap and the consumer must verify canvas width/height against the guest framebuffer size independently.
* An atlas bitmap labelled with two different characters is a **conflict** (ground-truth error or two glyphs that look equal): never resolved
  by majority, the cell reads UNKNOWN.
* **Numbers are same-ISO, same-screens**: see GLYPHGRID-FINDINGS.md (leave-one-frame-out is optimistic; leave-screen-group-out is the honest figure).

## API (`glyphgrid.py`)

```python
import glyphgrid as gg
atlas = gg.Atlas.load("pwmcp/tests/fixtures/glyphgrid/truenas-27-vanilla.atlas.corrected-gt.json")
res = gg.read_image(png_bytes, atlas)          # bytes | path | PIL.Image  ->  dict
res["status"]        # "ok" | "no_grid" | "resampled" | "no_font" | "mixed_font"   (only "ok" screens may yield text)
res["font_share"]    # share of ordinary ink cells the chosen font reads exactly; a font-FILE font needs >= 0.9 (and no rival > 0.3) before ANY cell is font-exact
res["grid"]          # {cell_w, cell_h, origin_x, origin_y, cols, rows}
res["font"]          # atlas font chosen by exact-hit count (one atlas can hold several fonts)
res["rows"]          # one string per grid row, UNKNOWN cells as U+FFFD
res["conf"]          # one string per row, one letter per cell:
                     #   F font-exact (character from a font file)   e learned, >= 2 independent screens   b blank
                     #   H font-exact, repertoire character unique, but other code points draw the same bitmap (res["aliases"] has the set)
                     #   a alias set (several characters, or a cross-font collision with an unproven font): char is U+FFFD, the set is in res["aliases"]
                     #   s learned single-source   f fill (solid cells, whole run)   u unknown   x atlas conflict   m multicolour
                     #   n nearest (only if max_hamming>0)
res["flags"]         # one string per row: "." / "r" rule-line / "c" cursor candidate (cursor-shaped glyphs are always "c" unless cursor=(row, col) is given)
res["aliases"]       # {"row,col": [characters...]}
res["unknown_cells"] # [{row, col, x, y, status, hex, reason, nearest, distance, ...}]
gg.strict_match(res, "Enter an option from 1-10:", region=None, cursor=None)
                     # THE decision function: {"matched", "hits", "candidates": [{row, col, reasons}], "reasons"}.  Phrase cells AND both boundary
                     # cells must be F/e/b (H only per policy), unflagged, no alias/single/fill/unknown, screen status ok, font_share >= 0.9.
gg.find_phrase(res, "sda")        # plain unique hits (same rules; unreadable neighbours are not word boundaries)
gg.find_phrase_ambiguous(res, "AK")   # readable-but-not-unique hits (alias sets / refused homoglyphs): NEVER rely on these
gg.region(res, row0, row1, col0, col1)   # -> (text, n_bad)
# homoglyph policy (find_phrase/strict_match `homoglyphs=`): "latin-only" (default: accept H only if every other code point is a Latin lookalike of
# the expected character: accented variants, Kelvin sign, hyphen class), "none" (any homoglyph refuses), "declared-repertoire" (the consumer states
# that the screen is Latin text: accepts Cyrillic/Greek lookalikes it cannot tell apart by bitmap).
```

Lower level: `load_image`, `probe_scale`, `detect_grid`, `extract`, `decode`, `choose_extraction`.
`detect_grid(im, hints=[(8, 16)])` restricts the search for a consumer that knows its console geometry.
Pass `return_cells=True` for the full per-cell record (bitmap hex, fg/bg colours, evidence).

Pipeline: `probe_scale` (refuse blur; undo crisp integer nearest-neighbour upscales) -> `detect_grid` (row pitch
from glyph-scale transition profile, column pitch and origin by minimum-description-length of repeating
cells; refuses photographs/logos and anti-aliased content) -> `extract` (two-colour cells; foreground = the
minority colour, so light-on-dark, dark-on-grey and reverse video give the same bitmap; 3+ colour cells are
`multicolor`, uniform cells are blank) -> `decode` (exact dictionary lookup).

## Font-file atlases

```
python3 build_font_atlas.py --kernel-bzimage vmlinuz-... --pf2 unicode.pf2 --ovmf OVMF_CODE_4M.fd --out fonts.atlas.json   # also --psf/--hex/--bdf
python3 verify_fonts.py --atlas fonts.atlas.json --bzimage ... --pf2 ... --ovmf ... --corpus CORPUS --series SERIES --out font-verify.json
atlas = gg.Atlas.merge(gg.Atlas.load("fonts.atlas.json"), learned_atlas)      # font entries first, learned ones for other consoles
```
Committed: `pwmcp/tests/fixtures/glyphgrid/fonts.atlas.json` (VGA 8x16 from the Linux kernel, GNU Unifont 16 from GRUB's `unicode.pf2`, EDK2 8x19 from OVMF; 87 KB).
Runtime needs only the JSON; regeneration needs the source files (Debian: `linux-image-*`, `grub-common`/`fonts-unifont`, `ovmf`, `kbd`/`console-setup-linux`).
Cell origin is found by exact font hits over all phases (`choose_extraction`, accepted only with a large margin).

## Learning and evaluation

```
python3 learn_atlas.py CORPUS_DIR --out atlas.json --report report.json --groups screen-groups.json [--corrections ground-truth-corrections.json]
python3 eval_glyphgrid.py --loo --probe --curve --colour --scale --run-series --groups screen-groups.json \
    --corrections ground-truth-corrections.json --out results.json   # LOO + leave-screen-group-out, original AND corrected transcript
```

`CORPUS_DIR` holds `frames/NN-name.png` and `ground-truth/NN-name.txt` (plain text, no box drawing, no
column offsets). `learn_atlas.py` aligns each ground-truth line onto the grid by its blank/ink pattern
(runs of spaces are collapsed in the transcript, so a gap may be any width), skips lines it cannot place or
that are ambiguous (listed in the report), pairs each aligned cell bitmap with its character, groups frames
into fonts by exact-hit overlap, and reports every bitmap that carries two different characters.
`eval_glyphgrid.py --loo` learns from N-1 frames and reads the N-th (no tuning on the test frame).

The atlas is JSON: `{"format": "glyphgrid-atlas/1", "fonts": {name: {cell_w, cell_h, glyphs: {bitmap_hex: {"c": {char: count}, "f": [source frames]}}}}}`.
A font is keyed by cell size and learned bitmap set; a new console font or GRUB build is a new font entry,
learned from a few labelled captures, never mixed into another.

## How a sidecar engine would call it

```python
# ocr_service.py, engine "glyphgrid" (sketch, not wired)
ATLAS = gg.Atlas.load(os.environ.get("GLYPHGRID_ATLAS", "/app/fonts.atlas.json"))
def engine_glyphgrid(png: bytes, params) -> dict:
    res = gg.read_image(png, ATLAS, grid_hints=params.get("grid_hints"), cursor=params.get("cursor"))
    out = {"engine": "glyphgrid", "status": res["status"], "message": res.get("message", ""), "text": "", "words": []}
    if res["status"] != "ok":
        return out
    out.update(text="\n".join(res["rows"]), conf=res["conf"], flags=res["flags"], aliases=res["aliases"], grid=res["grid"],
               font=res["font"], font_share=res["font_share"], font_is_file=res["font_is_file"], unknown_cells=res["unknown_cells"])
    if params.get("expect"):   # keystroke consumers ask the server to decide, with reasons
        out["strict"] = gg.strict_match(res, params["expect"], region=params.get("region"), cursor=params.get("cursor"),
                                        homoglyphs=params.get("homoglyphs", "latin-only"))
    return out
```

The sidecar contract must carry `status`, `conf`, `flags`, `aliases`, `font`/`font_share` and `unknown_cells` through to the client: a client
that only reads `text` sees U+FFFD for unknown/alias cells (never matches a phrase), but it would ALSO see a cursor bar or a homoglyph letter as
ordinary text. Prefer `strict_match` (server side) for any decision that sends keystrokes.

## Known limits (details in the findings)

* Only fonts present in the atlas are readable; a font that is NOT in the atlas (another distro's console font, HiDPI fonts) reads
  `no_font`/all UNKNOWN (by design); a screen mixing fonts or foreign content is `mixed_font`.
* Anti-aliased / scaled / lossy-compressed captures are refused or unreadable, not approximated; the probe cannot prove 1:1.
* Uniform cells next to text on a different background (and the whole run) are `f` fill, not blank; other uniform cells are blank.
  Box-drawing, block and arrow glyphs ARE read by the font-file atlases (repertoire ascii+box) and are UNKNOWN in learned-only atlases.
* A bitmap identifies a glyph, not a code point: homoglyphs (Cyrillic/Greek lookalikes in Unifont; accented Latin via the kernel unimap; soft hyphen)
  are `H`/`a` with the full set in `res["aliases"]` and are resolved only by the explicit policy above.
* The cursor is not a glyph; cursor-shaped cells are flagged, never text, unless the caller supplies `cursor=`.
* The EDK2 8x19 font comes from the devcontainer's `ovmf` package; the production (Netcup) firmware's font is unverified. Mixed-font screens,
  unimap substitution and firmware variance are NOT covered.
* Learned-atlas labels are as good as the human transcript; conflicts are reported, not hidden (and `Atlas.merge` cross-checks them against font files).
