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
* **Undone integer upscales are NOT actionable unless guarded.** The probe undoes the largest crisp k; a k-fold enlargement of a cell may be the native screen of a
  font of cell d*cell (d | k, d >= 2) at zoom k/d (a real 16x32 Lat15-VGA screen is exactly the 2x enlargement of an 8x16 one, and its 2x browser zoom is 4x). `prepare` carries
  k in `grid["unscaled"]` and `decode` reads it from the Extraction (no caller can forget it). For k > 1: `res["scale_unverified"] = k`, `res["scale_guard_fonts"]` lists the larger
  fonts checked; a bitmap that any loaded font of cell d*cell draws as another character is an alias set `a`; and unless a loaded font exists for EVERY divisor d, all remaining F/H
  cells are capped to class **`z`** (character shown, not actionable; `find_phrase`/`region`/`strict_match` refuse it; `res["scale_capped"]`). The atlas cannot establish that no larger
  font exists, so browser-zoomed 8x16 captures are non-actionable BY DESIGN; the explicit opt-in is `strict_match(res, phrase, trust_scale=True)` (or `glyphgrid.trust_scale(res)`), which records
  `trust_scale` and `scale_unverified` in the result.
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

Lower level: `load_image`, `probe_scale`, `detect_grid`, `extract`, `decode`, `choose_extraction`; `GlyphReader(atlas).read(frame)` for a frame sequence (see Performance).
`detect_grid(im, hints=[(8, 16)])` restricts the search for a consumer that knows its console geometry.
Pass `return_cells=True` for the full per-cell record (bitmap hex, fg/bg colours, evidence).

Pipeline: `probe_scale` (refuse blur; undo crisp integer nearest-neighbour upscales) -> `detect_grid` (row pitch
from glyph-scale transition profile, column pitch and origin by minimum-description-length of repeating
cells; refuses photographs/logos and anti-aliased content) -> `extract` (two-colour cells; foreground = the
minority colour, so light-on-dark, dark-on-grey and reverse video give the same bitmap; 3+ colour cells are
`multicolor`, uniform cells are blank) -> `decode` (exact dictionary lookup).

## Performance (measured 2026-10-27, 242-frame TrueNAS series, 1280x800, one core, nice 10)

Per frame, `read_image`, same atlas, output BIT-IDENTICAL to the pre-optimisation code on all 242 frames (and `reference_mode()` is
asserted identical on the 20 committed frames and on synthetic screens by `tests/test_glyphgrid_perf.py`):

| | before | now (fast paths) | `GlyphReader`, few cells changed |
|---|---|---|---|
| median / p95 / max | 987 / 1220 / 3643 ms | 197 / 270 / 920 ms | about 20 ms (PIL image), about 30 ms from PNG; 37 ms median over the 98 incremental frames of the real series |
| load + PNG decode | 11 | 11 | 11 (PNG input only) |
| probe_scale | 113 | 25 | updated from the changed band only |
| detect_grid (hinted by atlas sizes) | 530 | 89 | cached; one tiling check |
| choose_extraction (candidate origins + cells) | 506 | 59 | changed cells only |
| decode | 12 | 12 | 12 |
| peak RSS | 114 MB | 87 MB | similar |

What made it fast, all exact: shared transition masks between probe and grid detector; the blend test of the probe only inside the bounding box
of candidate pixels; run removal in log steps with memcpy rolls instead of `ImageChops.offset`; banded pre-check of the integer-upscale test;
cells extracted from ONE transposed strip per text row (cell bytes are contiguous slices, deduplicated before any per-cell Pillow call) and shared
between the candidate origins of one frame; bit packing by translate; candidate origins scored on shared value rows (Cell objects only for the
winner). `GLYPHGRID_REFERENCE=1` (or `with glyphgrid.reference_mode():`) selects the original code.

`GlyphReader(atlas, ...)` is the canvas-change-watcher path: `read(frame)` returns the `read_image` dict plus `res["session"]`
(`full` | `identical` | `incremental`). It reuses the grid geometry and origin of the previous frame, updates the probe numbers and colour census from the
changed bands only (they are local), re-extracts only changed cells (and unchanged exact-tie cells, whose bg/fg depends on the frame-wide colour ranking)
and decodes the whole screen. It falls back to the full pipeline when: first frame / other size, more than `max_dirty` (0.25) of the text rows changed,
pixels outside the grid rows changed, the frame is an integer upscale or `resampled`, the cached tiling no longer compresses the screen (>= 12 non-blank
cells and description length <= 0.5, the same gates as a fresh search), the new decode is not `ok`, or a changed ink cell is unknown to the atlas (stale
phase / foreign content). Contract: the whole result dict equals a fresh `read_image` of the same frame, except `session` and `grid_report` (the latter is the
length/contrast/candidates report of the search that established the cached geometry; an incremental frame runs no search, so it differs by design). Verified
on all 242 frames (98 incremental, 144 full fallbacks) and on synthetic edits. Geometry is an assumption re-verified by the gates above, not a proof that a
fresh search would pick the same tiling. The safety argument (an argument, not a proof) is that every cell is still an exact lookup, the evidence floor and
font share are recomputed over the whole new screen, and an ink cell in a changed area that the atlas does not know sends the frame to the full pipeline, so
a stale tiling shows up as UNKNOWN cells or the full path instead of silently producing exact cells. The "pixels outside the grid" check covers only the
full-width bands above the first grid row and below the last one; left/right sub-cell strips inside the grid rows are not checked separately: such a change
marks its text row as changed (the row test is full width), so the probe numbers are updated from it, and no cell contains those pixels, so no cell
value can depend on them. `GlyphReader` instances and `reference_mode()` are not thread-safe (mutable per-sequence state / a module global).

Rejected (measured on the same frames): **numpy** - the vectorised MDL step is 3x faster per call (0.25 -> 0.08 ms) but the whole frame gains about
15-20% (detect_grid 89 -> ~60 ms), numpy is in neither the OCR image (`python3-pil` only) nor the `pwmcp-client` test extra, and an optional second path
doubles the identity tests; on the incremental path the search does not run at all. **Hints** (`detect_grid(hints=[(8,16)])`): already applied
automatically from the atlas font sizes (free search 643 ms -> 94 ms); a single size saves another 13 ms only, the cost is the origin refinement.
**Pillow-native getdata/point batching** for packing: no gain over translate. **Compiled rewrite** (not built): the remaining 197 ms are (profile estimate) about 60% Pillow C
calls (masks, crops, getcolors) that a rewrite would not speed up and about 40% pure-Python loops (MDL, extraction, decode, ~80 ms) that a C/Rust extension
could cut to a few ms, i.e. at best 197 -> ~110 ms per cold frame, and nothing on the incremental path (already ~20 ms, bounded by PNG decode and decode()); it
would add a wheel per platform to the sidecar and tester images and a release step. Not worth it before frames/s becomes a real requirement.

`python3 bench_glyphgrid.py --series DIR [--reference | --check | --reader | --typing]` reproduces every number above.

## Synthetic image generator

`imagegen` builds deterministic console images with exact per-cell ground truth, applies ordered image transforms, and compares the real
glyph-grid reader, an oracle with known geometry, or Tesseract. It is a test and analysis tool; it does not change the OCR service, where
the glyph-grid engine remains unavailable (HTTP 501).

Run from this directory and keep generated PNGs, truth files, atlases, and reports under the ignored `scratch/` directory:

```sh
python3 -m imagegen generate --font vga-8x16 --screen installer --seed 1 -o scratch/sample
python3 -m imagegen suite --font vga-8x16 --seed 1 -o scratch/suite
python3 -m imagegen atlas --font vga-8x16 -o scratch/learned.atlas.json
python3 -m imagegen eval --suite scratch/suite --engine glyph-grid --json scratch/eval.json
```

The generator supports the committed bitmap fonts and a vector-font fallback; its transforms include scaling, offsets, blur, JPEG, noise,
colour changes, and subpixel shifts. Generated image bytes are reproducible within the recorded Pillow/zlib/libjpeg environment. See
[`imagegen/IMAGEGEN-FINDINGS.md`](imagegen/IMAGEGEN-FINDINGS.md) for the measured matrix, known reader defects, and interpretation limits.

The CLI vocabulary is: commands `generate`, `suite`, `atlas`, `eval`, and `read`;
screens `alphabet`, `installer`, and `random`; evaluation engines `glyph-grid`,
`glyph-grid-oracle`, and `tesseract` (the `read` command accepts the two
GlyphGrid engines); and evaluation groups `label`, `font`, and `screen`. Bitmap
font IDs are `vga-8x16`, `unifont-16`, and `edk2-8x19`; vector IDs include
`vector-<px>`, `dejavu-mono-<px>`, `pillow-default-<px>`, and `ttf:<path>:<px>`.
Transform names are `nn-upscale`, `scale`, `blur`, `boxblur`, `jpeg`, `quant`,
`gamma`, `contrast`, `offset`, `letterbox`, `noise`, and `subpixel`. `scale`
accepts the filters `nearest`, `bilinear`, `bicubic`, `lanczos`, and `box`.

## Font-file atlases

```
python3 build_font_atlas.py --kernel-bzimage vmlinuz-... --pf2 unicode.pf2 --ovmf OVMF_CODE_4M.fd --out fonts.atlas.json   # also --psf/--hex/--bdf
# installer/rescue console fonts (Debian console-setup Terminus/Fixed/VGA, kernel TER16x32, grml, Debian netinst): sha256-pinned manifest, tiers extra|ambiguous
python3 build_font_atlas.py --manifest fonts-console-extra.manifest.json --src-root DIR --base-atlas fonts.atlas.json --report report.json --out fonts.console-extra.atlas.json
# merge at load time: gg.Atlas.merge(base, extra); fonts.console-ambiguous.atlas.json only for targets known to show those fonts (see NOTICE.md)
python3 verify_fonts.py --atlas fonts.atlas.json --bzimage ... --pf2 ... --ovmf ... --corpus CORPUS --series SERIES --out font-verify.json
atlas = gg.Atlas.merge(gg.Atlas.load("fonts.atlas.json"), learned_atlas)      # font entries first, learned ones for other consoles
```
Committed: `pwmcp/tests/fixtures/glyphgrid/fonts.atlas.json` (VGA 8x16 from the Linux kernel, GNU Unifont 16 from GRUB's `unicode.pf2`, EDK2 8x19 from OVMF; 91,837 bytes); the installer/rescue fixtures add `fonts.console-extra.atlas.json` (537,773 bytes) and `fonts.console-ambiguous.atlas.json` (79,120 bytes). Every committed font has a source, version, checksum, and licence attribution in [`NOTICE.md`](NOTICE.md); the build manifest refuses source checksum drift.
Runtime needs only the JSON. Regeneration needs the source files listed in the manifest and NOTICE; no font source files are committed.
Cell origin is found by exact font hits over all phases (`choose_extraction`, accepted only with a large margin).

## Learning and evaluation

```
python3 learn_atlas.py CORPUS_DIR --out atlas.json --report report.json --groups screen-groups.json [--corrections ground-truth-corrections.json]
python3 eval_glyphgrid.py --loo --probe --curve --colour --scale --run-series --groups screen-groups.json \
    --corrections ground-truth-corrections.json --out results.json   # LOO + leave-screen-group-out, original AND corrected transcript
```

`eval_glyphgrid.py` defaults are derived from the script location, not from a home directory: `--corpus` is
`pwmcp/tests/corpus/console/truenas-27-vanilla` and `--series` is `pwmcp/artifacts/console-corpus/frames` (the
gitignored place for a local capture series; pass the flags to read elsewhere).

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
