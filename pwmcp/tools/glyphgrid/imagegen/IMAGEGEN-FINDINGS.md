# imagegen: deterministic OCR test images with exact ground truth, first measurements

Date 2026-10-27 (revised after independent review). Generator: `pwmcp/tools/glyphgrid/imagegen/`. Engines measured: glyph-grid
(existing `read_image`), glyph-grid-oracle (its cell matcher with grid detection and scale probe removed, grid taken from the
truth), Tesseract (**not measured: no `tesseract` binary on this host and the OCR image was not run**; see "Tesseract").

## What it is

Font + text screen + transform chain -> PNG + sidecar JSON truth (per-cell char/fg/bg/flags, text rows, transform parameters, the
cumulative geometry incl. the still-visible `clip`, font id and sha256, png sha256, the Pillow/zlib/libjpeg versions that produced
the bytes). Same arguments give the same bytes **within one environment** (tested for every transform; a golden hash is pinned for
the recorded Pillow version in the test). PNG bytes depend on Pillow's encoder and its zlib build, JPEG on libjpeg: a different
Pillow/zlib may change the bytes but not the pixels' meaning; compare `env` in the truth before comparing hashes across machines.

```
cd pwmcp/tools/glyphgrid
python3 -m imagegen generate --font vga-8x16 --screen alphabet --transform nn-upscale:2 --transform blur:0.8 --seed 1 -o DIR
python3 -m imagegen suite -o DIR [--font F]... [--screen S]... --seed 1      # 4 fonts x 3 screens x 20 chains = 240 images, ~10 s
python3 -m imagegen atlas -o gen.atlas.json                                   # atlas LEARNED from generator pages (bitmap fonts)
python3 -m imagegen eval --suite DIR --engine glyph-grid|glyph-grid-oracle|tesseract [--atlas A] [--by label|font|screen] [--json out.json]
python3 -m imagegen read IMAGE.png [--engine glyph-grid|glyph-grid-oracle --truth T.json]   # one image: status + text (reproducers)
```

* Fonts: `vga-8x16`, `unifont-16`, `edk2-8x19` (bitmaps from the committed font-exact fixture
  `pwmcp/tests/fixtures/glyphgrid/fonts.atlas.json`, no font files exist on this host; `fonts.from_fontfile()` takes any
  `fontfiles.FontFile`), `vector-<px>` (Pillow, anti-aliased: DejaVu Sans Mono when the system has it, otherwise Pillow's own
  bundled TrueType font (Aileron, Pillow >= 10.1 with FreeType), so no system package is required; the RESOLVED id
  `dejavu-mono-16` or `pillow-default-16` and the font file/bundle are recorded in the truth `env`; each glyph is centred in a cell as
  wide as the widest advance, so a proportional font works; if neither font is usable the vector tests FAIL, they do not skip),
  also `dejavu-mono-<px>`, `pillow-default-<px>`, `ttf:<path>:<px>`.
* Screens: `alphabet` (code chart, hex row/column labels, every repertoire character once), `installer`, `random` (seeded random
  words in 10 colour pairs, 10 % reverse spans, cursor).
* Transforms (`name:args`): `nn-upscale:k`, `scale:f,bilinear|bicubic|lanczos`, `blur:sigma`, `boxblur:r`, `jpeg:q`, `quant:n`,
  `gamma:g`, `contrast:c`, `offset:dx,dy` (far edge CROPPED, recorded in `clip`), `letterbox:l,t,r,b` (grows the canvas), `noise:sigma`,
  `subpixel:shift[,fringe]`. Offset/letterbox borders are filled with the SCREEN's background colour (the page colour, not the
  top-left pixel). Noise is seeded from (seed, chain position, name).
* Truth flags: `R` reverse video, `B` box/block char, `C` cursor (not scored), `A` ambiguous: no cell reader can know which of
  several characters it is: another character with the same bitmap, the fg/bg COMPLEMENT bitmap (a reader takes the minority colour as
  ink, so polarity is lost: lower half block vs upper half block), or, for a solid cell, a blank.
* Scoring, per cell. Not scored: cursor cells and cells not fully inside `clip` (`cropped`). Ink cell = non-space truth cell;
  **answered** = ink cell with any confident emission (a confident blank counts); `exact`; `unknown` (declined or nothing);
  `wrong` = a different char, a confident blank on an ink cell, a non-blank on a blank cell or outside the grid (`spurious`).
  Ambiguous cells are scored exactly like all others and also counted (`on ambiguous cells`), so a confident answer there is visible.
  A declined emission never conflicts with a confident one; two different confident ones in a cell are a wrong answer.
  **Read `wrong per answered`, not wrong out of all cells: an engine that refuses everything has zero wrong and zero answers.**
* Suites are scratch and not committed (`imagegen/.gitignore`).

## Round trip (tested: `pwmcp/tests/test_imagegen.py`)

`imagegen atlas` renders each bitmap font as three alphabet pages (grey/black, white/blue, black/grey), the installer and two
random pages, extracts the cells with `glyphgrid.extract` on the known grid (cursor cells skipped: the underline changes the bitmap),
pairs them with the truth characters and `Atlas.add`s them, one source per page. Reading back freshly generated clean screens (other
seed), all three fonts x alphabet/installer/random: **every cell NOT flagged ambiguous is exact and nothing is spuriously read;
random screens are 100 % exact**. The ambiguous cells are the block elements and are not all safe, see D-1 below: the generated atlas
misreads only the solid block (as blank); the font-exact fixture atlas also misreads complement twins. Caveat: the "2+ groups" rule
is satisfied by separately named pages of ONE generator, not independent evidence in the glyph-grid doctrine; this atlas is a
measurement fixture only, never keystroke-grade.

## Results (suite seed 1, 240 images: vga-8x16, unifont-16, edk2-8x19, vector-16 (here DejaVu Sans Mono) x alphabet/installer/random)

Per transform, 12 images per chain. 3 of the 12 are the vector font, which glyph-grid must refuse, so no engine can exceed 75 % of
ink cells here. "images (answered / refused)": answered = at least one confident ink-cell answer; refused = status other than `ok`.
"oracle" = true grid, point-sampled back to 1:1, public `extract`/`decode` (generated atlas); non-integer geometry is not
oracle-able and gives no answers. `offset:1,1` has fewer ink cells (8560): the 327 ink cells in the cropped last column/row are
not scored (previously they were counted as misses, which made the oracle look worse, 70.3 % against 72.9 %; the two rows are now equal).

### glyph-grid (`read_image`, font-exact fixture atlas)

| label | images (answered / refused) | ink cells | answered cells | exact (of ink) | unknown (of ink) | wrong-confident | wrong per answered |
|---|---:|---:|---:|---:|---:|---:|---:|
| `blur-0.8` | 12 (0 / 12) | 8887 | 0 | 0.0% | 100.0% | 0 (spurious 0, on ambiguous cells 0) | n/a |
| `boxblur-1` | 12 (0 / 12) | 8887 | 0 | 0.0% | 100.0% | 0 (spurious 0, on ambiguous cells 0) | n/a |
| `clean` | 12 (2 / 10) | 8887 | 714 | 8.0% | 92.0% | 0 (spurious 0, on ambiguous cells 0) | 0.0% |
| `contrast-0.6` | 12 (2 / 10) | 8887 | 714 | 8.0% | 92.0% | 0 (spurious 0, on ambiguous cells 0) | 0.0% |
| `gamma-1.4` | 12 (2 / 10) | 8887 | 714 | 8.0% | 92.0% | 0 (spurious 0, on ambiguous cells 0) | 0.0% |
| `jpeg-50` | 12 (0 / 12) | 8887 | 0 | 0.0% | 100.0% | 0 (spurious 0, on ambiguous cells 0) | n/a |
| `jpeg-90` | 12 (0 / 12) | 8887 | 0 | 0.0% | 100.0% | 0 (spurious 0, on ambiguous cells 0) | n/a |
| `letterbox-1` | 12 (0 / 12) | 8887 | 0 | 0.0% | 100.0% | 0 (spurious 0, on ambiguous cells 0) | n/a |
| `nn-upscale-2+blur-0.8` | 12 (0 / 12) | 8887 | 0 | 0.0% | 100.0% | 0 (spurious 0, on ambiguous cells 0) | n/a |
| `nn-upscale-2+jpeg-75` | 12 (0 / 12) | 8887 | 0 | 0.0% | 100.0% | 0 (spurious 0, on ambiguous cells 0) | n/a |
| `nn-upscale-2` | 12 (2 / 10) | 8887 | 714 | 8.0% | 92.0% | 0 (spurious 0, on ambiguous cells 0) | 0.0% |
| `nn-upscale-3` | 12 (2 / 10) | 8887 | 714 | 8.0% | 92.0% | 0 (spurious 0, on ambiguous cells 0) | 0.0% |
| `noise-8` | 12 (0 / 12) | 8887 | 0 | 0.0% | 100.0% | 0 (spurious 0, on ambiguous cells 0) | n/a |
| `offset-1_1` | 12 (0 / 12) | 8560 | 0 | 0.0% | 100.0% | 0 (spurious 0, on ambiguous cells 0) | n/a |
| `quant-16` | 12 (2 / 10) | 8887 | 714 | 8.0% | 92.0% | 0 (spurious 0, on ambiguous cells 0) | 0.0% |
| `scale-0.75_bilinear` | 12 (0 / 12) | 8887 | 0 | 0.0% | 100.0% | 0 (spurious 0, on ambiguous cells 0) | n/a |
| `scale-1.25_bicubic` | 12 (0 / 12) | 8887 | 0 | 0.0% | 100.0% | 0 (spurious 0, on ambiguous cells 0) | n/a |
| `scale-1.5_lanczos` | 12 (0 / 12) | 8887 | 0 | 0.0% | 100.0% | 0 (spurious 0, on ambiguous cells 0) | n/a |
| `subpixel-0.33` | 12 (0 / 12) | 8887 | 0 | 0.0% | 100.0% | 0 (spurious 0, on ambiguous cells 0) | n/a |
| `subpixel-0.33_fringe` | 12 (0 / 12) | 8887 | 0 | 0.0% | 100.0% | 0 (spurious 0, on ambiguous cells 0) | n/a |

### glyph-grid-oracle (generated atlas)

| label | images (answered / refused) | ink cells | answered cells | exact (of ink) | unknown (of ink) | wrong-confident | wrong per answered |
|---|---:|---:|---:|---:|---:|---:|---:|
| `blur-0.8` | 12 (0 / 10) | 8887 | 0 | 0.0% | 100.0% | 0 (spurious 0, on ambiguous cells 0) | n/a |
| `boxblur-1` | 12 (0 / 12) | 8887 | 0 | 0.0% | 100.0% | 0 (spurious 0, on ambiguous cells 0) | n/a |
| `clean` | 12 (9 / 2) | 8887 | 6478 | 72.9% | 27.1% | 3 (spurious 0, on ambiguous cells 3) | 0.0% |
| `contrast-0.6` | 12 (9 / 2) | 8887 | 6478 | 72.9% | 27.1% | 3 (spurious 0, on ambiguous cells 3) | 0.0% |
| `gamma-1.4` | 12 (9 / 2) | 8887 | 6478 | 72.9% | 27.1% | 3 (spurious 0, on ambiguous cells 3) | 0.0% |
| `jpeg-50` | 12 (1 / 10) | 8887 | 1 | 0.0% | 100.0% | 1 (spurious 0, on ambiguous cells 1) | 100.0% |
| `jpeg-90` | 12 (1 / 9) | 8887 | 1 | 0.0% | 100.0% | 1 (spurious 0, on ambiguous cells 1) | 100.0% |
| `letterbox-1` | 12 (9 / 2) | 8887 | 6478 | 72.9% | 27.1% | 3 (spurious 0, on ambiguous cells 3) | 0.0% |
| `nn-upscale-2+blur-0.8` | 12 (0 / 12) | 8887 | 0 | 0.0% | 100.0% | 0 (spurious 0, on ambiguous cells 0) | n/a |
| `nn-upscale-2+jpeg-75` | 12 (0 / 12) | 8887 | 0 | 0.0% | 100.0% | 0 (spurious 0, on ambiguous cells 0) | n/a |
| `nn-upscale-2` | 12 (9 / 2) | 8887 | 6478 | 72.9% | 27.1% | 3 (spurious 0, on ambiguous cells 3) | 0.0% |
| `nn-upscale-3` | 12 (9 / 2) | 8887 | 6478 | 72.9% | 27.1% | 3 (spurious 0, on ambiguous cells 3) | 0.0% |
| `noise-8` | 12 (0 / 0) | 8887 | 0 | 0.0% | 100.0% | 0 (spurious 0, on ambiguous cells 0) | n/a |
| `offset-1_1` | 12 (9 / 2) | 8560 | 6247 | 72.9% | 27.0% | 3 (spurious 0, on ambiguous cells 3) | 0.0% |
| `quant-16` | 12 (9 / 3) | 8887 | 6478 | 72.9% | 27.1% | 3 (spurious 0, on ambiguous cells 3) | 0.0% |
| `scale-0.75_bilinear` | 12 (0 / 12) | 8887 | 0 | 0.0% | 100.0% | 0 (spurious 0, on ambiguous cells 0) | n/a |
| `scale-1.25_bicubic` | 12 (0 / 12) | 8887 | 0 | 0.0% | 100.0% | 0 (spurious 0, on ambiguous cells 0) | n/a |
| `scale-1.5_lanczos` | 12 (0 / 12) | 8887 | 0 | 0.0% | 100.0% | 0 (spurious 0, on ambiguous cells 0) | n/a |
| `subpixel-0.33` | 12 (0 / 12) | 8887 | 0 | 0.0% | 100.0% | 0 (spurious 0, on ambiguous cells 0) | n/a |
| `subpixel-0.33_fringe` | 12 (0 / 12) | 8887 | 0 | 0.0% | 100.0% | 0 (spurious 0, on ambiguous cells 0) | n/a |

### Clean images, per font and screen (exact / unknown / wrong)

| font | screen | ink | glyph-grid status | glyph-grid exact / unknown / wrong | oracle exact / unknown / wrong |
|---|---|---:|---|---|---|
| dejavu-mono-16 | alphabet | 372 | resampled | 0 / 372 / 0 | 0 / 372 / 0 |
| dejavu-mono-16 | installer | 388 | resampled | 0 / 388 / 0 | 0 / 388 / 0 |
| dejavu-mono-16 | random | 1542 | resampled | 0 / 1542 / 0 | 0 / 1542 / 0 |
| edk2-8x19 | alphabet | 239 | no_grid | 0 / 239 / 0 | 238 / 0 / 1 |
| edk2-8x19 | installer | 388 | no_font | 0 / 388 / 0 | 357 / 31 / 0 |
| edk2-8x19 | random | 1507 | no_grid | 0 / 1507 / 0 | 1507 / 0 / 0 |
| unifont-16 | alphabet | 382 | no_grid | 0 / 382 / 0 | 369 / 12 / 1 |
| unifont-16 | installer | 388 | ok | 357 / 31 / 0 | 357 / 31 / 0 |
| unifont-16 | random | 1523 | no_grid | 0 / 1523 / 0 | 1523 / 0 / 0 |
| vga-8x16 | alphabet | 258 | no_grid | 0 / 258 / 0 | 255 / 2 / 1 |
| vga-8x16 | installer | 388 | ok | 357 / 31 / 0 | 357 / 31 / 0 |
| vga-8x16 | random | 1512 | no_grid | 0 / 1512 / 0 | 1512 / 0 / 0 |

## Findings

1. **What glyph-grid answered, it got right, except where the generator proves the question was undecidable.** The real engine
   answered 714 ink cells on each of the five lossless chains (2 of 12 images: the VGA and Unifont installers) with 0 wrong; the
   other 10 images are refused or declined. Out of 8887 ink cells per chain that is 8.0 % answered: **an accuracy of "0 wrong" over
   everything would be a statement about refusals, not about reading.** Under blur, box blur, JPEG, noise, resampling and sub-pixel
   shifts it answers nothing (refused as `resampled`/`no_font`, or every cell declined): the safe direction, but a coverage of 0.
2. **The oracle answers 6478 cells per lossless chain with 3 wrong, all on ambiguous cells** (the solid block reads as a blank);
   with the font-exact fixture atlas more (D-1). The oracle's wrong-per-answered is therefore 0.05 % and every wrong cell is one the
   generator flagged `A`. Under JPEG the oracle answers exactly one cell, and it is wrong (the same solid block): 1 of 1.
3. **D-1 (corrected, was "no misread seen"): bitmaps and their fg/bg complement are indistinguishable and the font-exact fixture
   atlas reads them as confident wrong characters on CLEAN frames.** Oracle over the clean alphabet pages: vga-8x16 `▄` read as `▀`,
   `█` as blank; unifont-16 `▇`->`▔`, `▉`->`▕`, `▙`->`▝`, `▛`->`▗`, `▜`->`▖`, `▟`->`▘`, `█` as blank; edk2-8x19 `█` as blank. The
   generated atlas learns both polarities per page and avoids all but the solid block. Recorded as a glyph-grid defect with a
   proposed fix in `GLYPHGRID-FINDINGS.md` ("Open defects"). The generator flags all of these cells `A`; the tests no longer ignore
   block characters wholesale: wrong cells are tolerated only if flagged `A`, and the fixture-atlas misreads are pinned pair by pair.
4. **Vector fonts are refused wholesale** (`resampled`: anti-aliased edges): 0 answered, 0 wrong.
5. Progress-bar cells (`█` next to text, status `f`) are declined: 31 unknown cells per installer, correct behaviour.

## Known defects of glyph-grid found by the generator (fix in a separate branch; NOT generator artefacts)

Reproduce with `cd pwmcp/tools/glyphgrid; python3 -m imagegen generate ... -o D; python3 -m imagegen read D/<file>.png`.

| id | reproducer | observed | one-line hypothesis |
|---|---|---|---|
| known-defect D-2 `no_grid` | `generate --font vga-8x16 --screen random --seed 1 -o D`; `read D/vga-8x16__random-1__clean__s1.png` (same for `--screen alphabet`) | `no_grid`: best tiling 8x16, description length 0.81 (alphabet 0.70) > 0.50 | random/alphabet screens are ~75 % ink with no blank cells and almost no repeated cell, so the MDL compression gate (0.5) refuses; `grid_hints` restricts candidates but does not bypass the gate. Real consoles repeat cells, so this mostly hits test pages |
| known-defect D-3 EDK2 read as 8x16 | `generate --font edk2-8x19 --screen installer --seed 1 -o D`; `read D/edk2-8x19__installer__clean__s1.png` | `no_font`; grid 8x16, origin_y 1, 29 rows, font_share 0.01 | the free/hinted search ranks 8x16 above the true 8x19 (`k2[1] % ph == 0`-style sub-tiling rules favour the smaller pitch) on a screen that tiles under both; choose among hinted sizes by atlas font_share, not by description length |
| known-defect D-4 offset origin | `generate --font vga-8x16 --screen installer --transform offset:1,1 --seed 1 -o D`; `read D/vga-8x16__installer__offset-1_1__s1.png` | `no_font`; grid origin (0, 15) | blank rows tie, `detect_grid` takes the tie nearest (0,0) by modular distance (15 = -1) instead of the true +1 phase; the phase search over `phases` does not recover it because the atlas phase list only holds the learned origins |
| known-defect D-1 complement twins | see `GLYPHGRID-FINDINGS.md` | confident wrong block elements, solid block read as blank | minority-colour ink rule loses polarity |

Letterbox: its previous `no_font` rows came partly from the generator filling the border with the top-left pixel (grey title bar);
the border now uses the page background. glyph-grid still reads 0 cells on `letterbox:1` (real engine), the oracle reads 72.9 %.

## Tesseract

`--engine tesseract` is implemented with per-CHARACTER boxes and confidences (hOCR `hocr_char_boxes=1`: `ocrx_cinfo` spans with
`x_bboxes` and `x_conf`), so one misread glyph cannot shift the columns of the rest of a word (the first version spread word boxes
evenly over their characters, which would have). Every emitted character counts as confident whatever its confidence (by design);
the eval prints the confidence distribution of its exact and its wrong cells so a confidence threshold can be judged. It FAILS
without the binary unless `PWMCP_ALLOW_MISSING_TEST_DEPS=1` (the variable of `tests/_testdeps.py`; then it prints SKIP); the
real-engine test uses the conftest sidecar-image rule (`real_tesseract`). **It has not been run against a real Tesseract**: the
hOCR parser is tested on canned output only and the exact hOCR attribute spelling of the installed version is unverified. Run
`suite` + `eval --engine tesseract` inside the OCR image and append the table here.

### Live-check list for the first real Tesseract run (inside the OCR container)

1. `tesseract --version`, then confirm `-c hocr_char_boxes=1 ... hocr` is accepted and the output contains `ocrx_cinfo` spans with
   `x_bboxes` and `x_conf` (the parser expects exactly those attribute spellings; spans with another shape are silently skipped, which
   would show up as an implausibly high `unknown` rate, not as errors).
2. LSTM per-character boxes can be loose or overlap: cells are assigned by the box CENTRE point, so a box may land in a neighbouring
   cell. That is scored as it lands (wrong on the neighbour, unknown on the real cell); a canned one-column shift is unit-tested to
   be detected as wrong cells, never absorbed (`test_one_column_shift_in_canned_hocr_...`).
3. Characters Tesseract does not list are unknown (ink cells) and cost nothing on blank cells; hallucinated text on blank cells and
   outside the grid is `spurious` wrong.
4. Check the pre-scale (default 3x Lanczos) against 1x and 2x before comparing engines, and record the Tesseract version and
   `--psm` next to the table.

## Limitations

* Bitmap fonts come from the committed fixture atlas (ascii + box repertoire, no accented or wide characters); no PSF/PF2/OVMF
  font file exists on this host. Unifont 16 here is the 8x16 half-width cell.
* Vector font: DejaVu Sans Mono 16 here (Pillow's bundled font otherwise); sub-pixel LCD rendering is emulated by the `subpixel` transform (1/3 px), not rasterised.
* JPEG bytes depend on the local libjpeg, PNG bytes on Pillow/zlib (versions recorded in each truth file).
* The oracle reads only integer-scale, integer-offset geometry; elsewhere it gives no answers.
* The matrix is deliberately hostile (3/12 images unreadable by design): compare engines on the same suite, not against an absolute.
