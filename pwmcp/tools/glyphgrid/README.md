# glyphgrid: deterministic glyph-grid matching for fixed-font consoles

Prototype of the `glyphgrid` engine for the pwmcp OCR sidecar (`pwmcp/containers/ocr/ocr_service.py`
registers it as unavailable; this tool is written to be dropped in later). Pure function, stdlib + Pillow
only, no OCR model. Honest numbers and limits: `GLYPHGRID-FINDINGS.md`.

Idea: a VGA/framebuffer console draws a FIXED bitmap font on an exact cell grid. So split the frame into
cells, normalise each cell to a 1-bit foreground bitmap, and look the bitmap up in an atlas of known glyphs.
Every cell is then either a known glyph (exact match) or an explicit UNKNOWN (`�`); never a guess.

## Safety contract

* **UNKNOWN is never a guess.** The nearest atlas glyph and its Hamming distance are reported as *evidence*
  in `unknown_cells`, not as output. Tolerant matching (`max_hamming > 0`) is OFF by default.
* A consumer that sends keystrokes must match **on exact cells only**, in a region with **zero unknown
  cells** (`region()` returns the count; `find_phrase()` never matches across an unknown cell), and treat
  everything else as **indeterminate**.
* If the capture is resampled/blurred the engine **refuses** (`status: "resampled"`); if there is no fixed
  grid it says so (`status: "no_grid"`). Exact matching is only defined on the intrinsic 1:1 bitmap.
* An atlas bitmap labelled with two different characters is a **conflict** (ground-truth error or two
  different glyphs that look equal in one font): it is never resolved by majority, the cell reads UNKNOWN.

## API (`glyphgrid.py`)

```python
import glyphgrid as gg
atlas = gg.Atlas.load("pwmcp/tests/fixtures/glyphgrid/truenas-27-vanilla.atlas.json")
res = gg.read_image(png_bytes, atlas)          # bytes | path | PIL.Image  ->  dict
res["status"]        # "ok" | "no_grid" | "resampled"
res["grid"]          # {cell_w, cell_h, origin_x, origin_y, cols, rows}
res["font"]          # atlas font chosen by exact-hit count (one atlas can hold several fonts)
res["rows"]          # one string per grid row, UNKNOWN cells as U+FFFD
res["conf"]          # one string per row, one letter per cell:
                     #   e exact   b blank   u unknown   x atlas conflict   m multicolour   n nearest (only if max_hamming>0)
res["unknown_cells"] # [{row, col, x, y, status, hex, reason, nearest, distance, ...}]
gg.region(res, row0, row1, col0, col1)   # -> (text, n_unknown)
gg.find_phrase(res, "Enter an option from 1-10:")   # exact/blank cells only -> [{row, col, unknown_in_row}]
```

Lower level: `load_image`, `probe_scale`, `detect_grid`, `extract`, `decode`, `choose_extraction`.
`detect_grid(im, hints=[(8, 16)])` restricts the search for a consumer that knows its console geometry.
Pass `return_cells=True` for the full per-cell record (bitmap hex, fg/bg colours, evidence).

Pipeline: `probe_scale` (refuse blur; undo crisp integer nearest-neighbour upscales) -> `detect_grid` (row pitch
from glyph-scale transition profile, column pitch and origin by minimum-description-length of repeating
cells; refuses photographs/logos and anti-aliased content) -> `extract` (two-colour cells; foreground = the
minority colour, so light-on-dark, dark-on-grey and reverse video give the same bitmap; 3+ colour cells are
`multicolor`, uniform cells are blank) -> `decode` (exact dictionary lookup).

## Learning and evaluation

```
python3 learn_atlas.py CORPUS_DIR --out atlas.json --report report.json
python3 eval_glyphgrid.py --loo --curve --colour --scale --run-series --out results.json
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
ATLAS = gg.Atlas.load(os.environ.get("GLYPHGRID_ATLAS", "/app/atlas.json"))
def engine_glyphgrid(png: bytes, params) -> dict:
    res = gg.read_image(png, ATLAS, grid_hints=params.get("grid_hints"))
    if res["status"] != "ok":
        return {"engine": "glyphgrid", "status": res["status"], "text": "", "words": []}
    return {"engine": "glyphgrid", "status": "ok", "text": "\n".join(res["rows"]),
            "unknown_cells": res["unknown_cells"], "conf": res["conf"], "grid": res["grid"]}
```

The sidecar contract must carry `status`, `conf` and `unknown_cells` through to the client; a client that only
reads `text` would see U+FFFD for unknowns, which is safe (it never matches a phrase) but loses the evidence.

## Known limits (details in the findings)

* Only fonts present in the atlas are readable; an unseen font is 100% UNKNOWN (by design).
* Anti-aliased / scaled / lossy-compressed captures are refused or unreadable, not approximated.
* Uniform cells read as blank whatever their colour (a solid block cell is a space; fill colours are in
  `return_cells`). Box-drawing glyphs are not in the transcribed ground truth, so they read UNKNOWN unless
  an atlas entry was added by hand.
* Atlas labels are as good as the human transcript; conflicts are reported, not hidden.
