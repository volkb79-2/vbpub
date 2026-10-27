# imagegen: deterministic OCR test images with exact ground truth, first measurements

Date 2026-10-27. Generator: `pwmcp/tools/glyphgrid/imagegen/` (this commit). Engines measured: glyph-grid (existing `read_image`),
glyph-grid-oracle (its cell matcher with grid detection and scale probe removed, grid taken from the truth), Tesseract
(**not measured: no `tesseract` binary on this host and the OCR image was not run**; see "Tesseract" below).

## What it is

Font + text screen + transform chain -> PNG + sidecar JSON truth (per-cell char/fg/bg/flags, text rows, transform parameters
and the cumulative geometry, font id and sha256, png sha256). Same arguments give the same bytes (tested for every transform).

```
cd pwmcp/tools/glyphgrid
python3 -m imagegen generate --font vga-8x16 --screen alphabet --transform nn-upscale:2 --transform blur:0.8 --seed 1 -o DIR
python3 -m imagegen suite -o DIR [--font F]... [--screen S]... --seed 1      # 4 fonts x 3 screens x 20 chains = 240 images, ~10 s
python3 -m imagegen atlas -o gen.atlas.json                                   # atlas LEARNED from generator pages (bitmap fonts)
python3 -m imagegen eval --suite DIR --engine glyph-grid|glyph-grid-oracle|tesseract [--atlas A] [--by label|font|screen] [--json out.json]
```

* Fonts: `vga-8x16`, `unifont-16`, `edk2-8x19` (bitmaps from the committed font-exact fixture
  `pwmcp/tests/fixtures/glyphgrid/fonts.atlas.json`, no font files exist on this host; `fonts.from_fontfile()` takes any
  `fontfiles.FontFile`), `dejavu-mono-<px>` / `ttf:<path>:<px>` (Pillow, anti-aliased; DejaVu Sans Mono 16 px here, cell 10x19).
* Screens: `alphabet` (code chart, hex row/column labels, every repertoire character once), `installer` (grey title bar, double-box
  menu, reverse-video selection, multicolour status words, progress bar, black-on-grey and white-on-blue, prompt + cursor),
  `random` (seeded random words in 10 colour pairs, 10 % reverse spans, cursor).
* Transforms (`name:args`): `nn-upscale:k`, `scale:f,bilinear|bicubic|lanczos`, `blur:sigma`, `boxblur:r`, `jpeg:q`, `quant:n`,
  `gamma:g`, `contrast:c`, `offset:dx,dy`, `letterbox:l,t,r,b`, `noise:sigma`, `subpixel:shift[,fringe]`. Noise is seeded from
  (seed, chain position, name).
* Truth flags: `R` reverse video, `B` box/block char, `C` cursor (not scored), `A` ambiguous (font draws another repertoire
  character with the same bitmap: excluded from the accuracy denominator, a wrong confident char there still counts).
* Scoring (ink cell = non-space truth cell): `exact`; `unknown` (declined or nothing emitted); `wrong-confident` = a different
  char, a confident blank on an ink cell, a non-blank on a blank cell or outside the grid (`spurious`, shown in brackets).
  Engine output is mapped to truth cells through the recorded geometry, so crisp scale/offset/letterbox are scored correctly.
* Suites are scratch and not committed (`imagegen/.gitignore`).

## Round trip (tested: `pwmcp/tests/test_imagegen.py`)

`imagegen atlas` renders each bitmap font as three alphabet pages (grey/black, white/blue, black/grey), the installer and two
random pages, extracts the cells with `glyphgrid.extract` on the known grid, pairs them with the truth characters, and
`Atlas.add`s them with one source per page. Reading back freshly generated clean screens (other seed) with it, all three fonts
x alphabet/installer/random: **every non-block ink cell exact, zero wrong confident**. Random screens read 100 % exact
(1512 / 1507 / 1523 cells). The residue is only block elements U+2580-259F: cells with half or more ink or solid cells have no
unique foreground-minority bitmap, so glyph-grid declines them (UNKNOWN or fill) except one case below. Caveat: the "2+ groups"
rule is satisfied by separately named pages of ONE generator, which is not independent evidence in the glyph-grid doctrine; this
atlas is a measurement fixture only, never keystroke-grade. The font-exact fixture atlas and the generated atlas read identically.

## Results (suite seed 1, 240 images: vga-8x16, unifont-16, edk2-8x19, dejavu-mono-16 x alphabet/installer/random)

Per transform, all 12 images per chain (ink cells 8887 per row). 3 of the 12 are the vector font, which glyph-grid must refuse, so
no engine can exceed 75 % here. "glyph-grid" = `read_image` with the fixture atlas; "oracle" = true grid, point-sampled back to 1:1,
public `extract`/`decode` (generated atlas); non-integer geometry is not oracle-able and counts as refused/unknown.

| transform chain | glyph-grid exact | unknown | wrong | oracle exact | unknown | wrong |
|---|---:|---:|---:|---:|---:|---:|
| `blur:0.8` | 0.0% | 100.0% | 0 | 0.0% | 100.0% | 0 |
| `boxblur:1` | 0.0% | 100.0% | 0 | 0.0% | 100.0% | 0 |
| `clean` | 8.0% | 92.0% | 0 | 72.9% | 27.1% | 3 |
| `contrast:0.6` | 8.0% | 92.0% | 0 | 72.9% | 27.1% | 3 |
| `gamma:1.4` | 8.0% | 92.0% | 0 | 72.9% | 27.1% | 3 |
| `jpeg:50` | 0.0% | 100.0% | 0 | 0.0% | 100.0% | 1 |
| `jpeg:90` | 0.0% | 100.0% | 0 | 0.0% | 100.0% | 1 |
| `letterbox:1` | 0.0% | 100.0% | 0 | 72.9% | 27.1% | 3 |
| `nn-upscale:2+blur:0.8` | 0.0% | 100.0% | 0 | 0.0% | 100.0% | 0 |
| `nn-upscale:2+jpeg:75` | 0.0% | 100.0% | 0 | 0.0% | 100.0% | 0 |
| `nn-upscale:2` | 8.0% | 92.0% | 0 | 72.9% | 27.1% | 3 |
| `nn-upscale:3` | 8.0% | 92.0% | 0 | 72.9% | 27.1% | 3 |
| `noise:8` | 0.0% | 100.0% | 0 | 0.0% | 100.0% | 0 |
| `offset:1,1` | 0.0% | 100.0% | 0 | 70.3% | 29.7% | 3 |
| `quant:16` | 8.0% | 92.0% | 0 | 72.9% | 27.1% | 3 |
| `scale:0.75,bilinear` | 0.0% | 100.0% | 0 | 0.0% | 100.0% | 0 |
| `scale:1.25,bicubic` | 0.0% | 100.0% | 0 | 0.0% | 100.0% | 0 |
| `scale:1.5,lanczos` | 0.0% | 100.0% | 0 | 0.0% | 100.0% | 0 |
| `subpixel:0.33` | 0.0% | 100.0% | 0 | 0.0% | 100.0% | 0 |
| `subpixel:0.33,fringe` | 0.0% | 100.0% | 0 | 0.0% | 100.0% | 0 |

Clean images, per font and screen (exact / unknown / wrong):

| font | screen | ink | glyph-grid status | glyph-grid | oracle |
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

1. **glyph-grid never produced a wrong-confident char on the whole matrix (0 of 8887 x 20); the only wrong-confident cells in the
   oracle runs are one solid block** (finding 4). Under blur, box blur, JPEG, noise, resampling and sub-pixel shifts everything
   goes UNKNOWN or the image is refused (`resampled`): the safe direction. Quantising to 16 colours, gamma and contrast do not
   hurt the 1-bit matcher at all.
2. **Grid detection is the weak link, not the matcher.** Real `read_image` refuses clean, 1:1, font-exact alphabet and random
   screens with `no_grid` ("best tiling 8x16: description length 0.70 / 0.81 > 0.50"): `detect_grid` needs repeated cells, and a
   code chart (every glyph once) or random text does not compress. Only the installer screens (repeated spaces and words) read.
   The oracle reads the same random screens 100 % exact, so a consumer that knows its console font and size cannot currently say
   so (`grid_hints` only restricts candidates, the 0.5 compression gate still applies). Candidate improvement: when
   `hints` name exactly one known atlas cell size, gate on font_share instead of description length.
3. **Cell-size and origin choice:** the EDK2 8x19 installer screen is detected as 8x16 (origin_y 1, 29 rows, font_share 0.01,
   `no_font`) although 8x19 is in the atlas hints; a 1-pixel offset (`offset:1,1`) or letterbox on the VGA installer gives origin
   (0, 15) and `no_font`. Both are refusals, not misreads, but they show 1:1 captures with an odd origin are not covered.
4. **Isolated solid block U+2588 reads as a confident blank.** On the alphabet page the single full block (a uniform grey cell
   on a black page, no text beside it) is returned as `b` (blank) because the `f` fill rule only applies next to text. That is
   the one wrong-confident cell on clean screens (3 per transform for the 3 bitmap fonts, plus JPEG variants). Pinned in
   `test_the_solid_block_reads_blank_is_pinned_as_a_known_finding`. Half-ink blocks (U+2580 etc.) are declined or, when the
   glyph is symmetric under fg/bg swap (U+2584 vs U+2580), unknown; no misread seen.
5. **Vector fonts are refused wholesale** (`resampled`: anti-aliased edges), never misread: 0 exact, 0 wrong, 100 % unknown.
6. The matrix has 31 declined cells per installer screen: the 31 `█` progress-bar cells (uniform cells next to text, status `f`).
   Correct behaviour, counted as unknown.

## Tesseract

`--engine tesseract` is implemented (CLI TSV, words spread over columns by their boxes, Lanczos 3x pre-scale by default,
every emitted char counted as confident) and unit-tested on canned TSV; it FAILS without the binary unless
`PWMCP_ALLOW_MISSING_TEST_DEPS=1` (then prints SKIP). It has not been run on a real Tesseract: this host has none and the OCR
sidecar image was not started (host is shared with a production server). To fill the gap, run the `suite` + `eval --engine
tesseract` inside the OCR image and append the table here; expect wrong-confident counts to be the interesting column
(box-drawing and `|`/`l`/`I` confusions, hallucinated text on blank rows).

## Limitations

* Bitmap fonts come from the committed fixture atlas (ascii + box repertoire, no accented or wide characters); no PSF/PF2/OVMF
  font file exists on this host. Unifont 16 here is the 8x16 half-width cell.
* Vector font: DejaVu Sans Mono 16 only; sub-pixel LCD rendering is emulated by the `subpixel` transform, not by a font rasteriser.
* `subpixel` is 1/3-pixel resolution; JPEG bytes depend on the local libjpeg (deterministic per machine, not guaranteed identical
  across libjpeg versions); PNG output is plain and timestamp-free.
* The oracle reads only integer-scale, integer-offset geometry; its "unknown" under non-integer scale is "not attempted".
* Scores aggregate over a deliberately hostile matrix (3/12 images unreadable by design); compare engines on the same suite, not
  against an absolute.
