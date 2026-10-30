# glyphgrid findings (2026-10-27)

> **Scope statement for every number in this file: same ISO, same screens, 19 of 20 frames seen; it does not predict a new
> screen, a new ISO or a new font.** The leave-one-frame-out numbers are inflated by near-duplicate frames (12/13, 19/20,
> 05/06, 09/10/11); the leave-screen-group-out numbers below are the honest ones.

## Revision 4 (review of the font-file commit): closing the ways a wrong character could still read as font-exact

1. **Cross-font collisions.** VGA and Unifont share the 8x16 cell and 10 box-drawing bitmaps mean different characters in the two fonts. `decode` now
   returns `font_share` (share of ordinary ink cells the chosen font reads exactly), `font_runner_up_share`, `font_proven`. A font-FILE font needs
   `font_share >= 0.9` before ANY cell may be font-exact (else status `mixed_font`, every cell UNKNOWN), and a bitmap another same-size font draws as a
   different character is an alias set `a` unless the font is proven (share >= 0.9 AND no rival above 0.3). Regression: Unifont text pasted over the VGA
   dialog (160/320/400 px) now reads `mixed_font`/`no_font`, no `F` cell (before: 66/45/33 wrong `F` cells).
2. **Homoglyphs outside the repertoire.** Alias sets are now computed against the font's FULL code-point coverage (kernel default unicode map parsed from
   the vmlinuz, every PF2 code point, PSF unicode tables) and stored per bitmap (`"x"`): 20 VGA and 60 Unifont repertoire glyphs have such homoglyphs
   (A <- A-grave A-acute A-circumflex A-tilde, C <- copyright, K <- Kelvin, `-` <- soft hyphen ...; Unifont: Cyrillic/Greek capitals, U+2010/2011, ...).
   Status `H` = unique repertoire character but homoglyphs exist; `res["aliases"]` carries the whole set. **A bitmap identifies a glyph, not a code
   point: Cyrillic A and Latin A cannot be told apart by bitmap.** Policy (explicit, tested): `homoglyphs="latin-only"` (default) accepts an `H` cell only
   if every other code point is a Latin lookalike of the expected character (accented variant, Kelvin sign, hyphen class); `"none"` refuses any;
   `"declared-repertoire"` = the consumer declares the screen Latin text and accepts the risk. Measured on the series (296,891 ink cells): 147,721 `H` cells
   (49.8%); under the default policy 127,829 of them are refused (Unifont 123,547 of 131,277, VGA 4,282 of 16,444). So the cells a strict consumer may rely on
   are F-without-homoglyph + benign H = **168,894 (56.9%)**; VGA screens 94%, Unifont screens only 35%: the installed console's text is mostly
   NOT uniquely readable by bitmap (opt in with `declared-repertoire` if you know the console shows Latin).
3. **Cursor.** Cursor-shaped glyphs (U+2581..2584 lower blocks, U+2588) read by any font are always flagged `c` unless the caller passes `cursor=(row, col)` (then
   only that cell is flagged); a bar-like last cell of a row is flagged as before. Flagged cells never match. Series: 59 cursor-flagged Unifont cells; the F share
   excluding them is 99.963% (unchanged to three decimals: 296,723/296,832 = 99.963%).
4. **Word boundary.** A boundary is the row edge or a READABLE non-alphanumeric cell (F/e/b/policy-ok H, unflagged). Unknown / alias / single / fill / flagged /
   cursor neighbours are not boundaries (`sda` + unreadable cell no longer matches; nor `x<alias>OK`).
5. **Licences.** `NOTICE.md`: kernel font (GPL-2.0), GNU Unifont (GPL-2.0-or-later with font-embedding exception / OFL-1.1), EDK2 (BSD-2-Clause-Patent), sources, sha256,
   how the bitmaps are used; legal review of bitmap-table derivation is NOT done.

Minor: `strict_match(res, phrase, region, cursor)` is the single decision function (reasons per candidate); ambiguous hits only via `find_phrase_ambiguous`;
fill runs are fill throughout; `Atlas.merge` cross-checks learned entries against font-file entries of the same font (would have caught `-` vs `_`) and demotes
them; atlas validation requires `origin.kind` + sha256 for `font_exact`; no fixed temp files (verify/build use memory/tempfile); `load_raw_table` takes bytes; PF2 code points
>= 0x110000 (GRUB flag bits) are skipped; corrections that do not apply are reported (`already applied`/`no matching line`); the font-file phase search has a time budget
(`max_seconds`, default 4 s per image); atlas origin records the ISO sha256 and the path inside the ISO (EDK2: the devcontainer `ovmf` package, production firmware
unverified). The identification evidence is `font_share`; the `no_font` floor in `decode` is only the minimum for reading at all (Revision 3 conflated them).

Numbers after merging main (corpus carries the corrected transcript; `ground-truth-corrections.json` is now a reported no-op: `already applied`):
audit of the transcripts against the font reading: **6,839 agree, 1 disagree** (frame 20 cursor cell: transcript `_`, font `U+2581`), 0 alias conflicts, 8 button lines not placed.
Series (242 frames): 231 read, 10 no grid, 1 refused; **296,782 / 296,891 ink cells font-exact (99.963%)**, 0 alias (`a`) cells, 147,721 `H` cells, 59 cursor-flagged,
`font_share` over read frames: median 1.0, one frame below 0.98 (the near-empty EDK2 frame, 0.0). Corpus: 10,346 / 10,357 (99.894%), min `font_share` 0.9934.
Tests: 74, including ports of the reviewer's perturbation/crop repros (shifts, crops, border, 2x+1px, 1.5x, 1.03x, random crops) with every F/H/e cell checked.

Not covered, additionally: **mixed-font screens**, **homoglyph/unimap substitution beyond the declared policy**, and **firmware variance** (the EDK2 font of the production
host may differ from the devcontainer's `ovmf` package).

---

## Revision 3: font-file atlases (authoritative, no transcripts needed)

Console text is drawn from KNOWN bitmap fonts, so the atlas can come from the font files and screenshots only have to VERIFY the font.
`fontfiles.py` (stdlib loaders: PSF1/PSF2 with unicode table, GRUB PF2, Unifont `.hex`, BDF, raw 256-glyph tables, the kernel's built-in
font inside a decompressed bzImage, the EDK2 `EFI_NARROW_GLYPH` table inside an OVMF image), `build_font_atlas.py` (generator) and
`verify_fonts.py` (identification, audit, coverage). Results: `font-verify-results.json`.

**Where the fonts were found** (this devcontainer has none: no `/usr/share/consolefonts`, no kbd, no unifont package): inside the
artifacts of the corpus (the TrueNAS ISO: kernel and GRUB font) and, for the EDK2 firmware font, the devcontainer's own `ovmf` Debian package
(`2025.02-8+deb13u1`); the firmware actually used by the production (Netcup) host is NOT verified. All read-only, nothing mounted or run.

| screen font (our label) | source | what it is | sha256 (source file) |
|---|---|---|---|
| live/installer console, 14 frames (`f8x16-1`) | `/live/filesystem.squashfs` -> `boot/vmlinuz-6.18.52-production+truenas`, decompressed (zstd) | the Linux built-in IBM VGA 8x16 table (`font_vga_8x16`) at 0x14ce900, 256 glyphs, glyph index -> CP437 | bzImage `56e4101631183...` |
| installed console + ISO GRUB, 3 frames (`f8x16-2`) | ISO `/boot/grub/fonts/unicode.pf2` | GNU Unifont Regular 16 (PF2, ascent 14 + descent 2), 57,248 glyphs | `662eb86fc1ea8963...` |
| installed GRUB (EFI text console), 2 frames (`f8x19-3`) | `/usr/share/OVMF/OVMF_CODE_4M.fd` (Debian `ovmf 2025.02-8+deb13u1`), LZMA volume at 0xa8 | the TianoCore/EDK2 UEFI console font (`EFI_NARROW_GLYPH`, 8x19, 235 entries): GRUB's EFI console, not a GRUB `.pf2` | `624e06de18b4fa53...` |

So the "installed GRUB 8x19" font is the firmware's, which also explains the 19-pixel row pitch. The atlas stores file name, sha256, repertoire and
note per font. Generated atlas: `pwmcp/tests/fixtures/glyphgrid/fonts.atlas.json` (87 KB; 146 + 258 + 136 glyph bitmaps for the repertoire printable ASCII + box/block/arrow
characters). The sidecar needs only that JSON at runtime; to regenerate: Debian `linux-image-*` (kernel font), `grub-common`/`fonts-unifont`
(`unicode.pf2`), `ovmf` (EDK2 font), or `kbd`/`console-setup-linux` (PSF) with the loaders.

**Identification without transcripts** (grid and cell origin found by exact matches against the font files only; ink cell = any non-blank 2-colour cell):

| font | frames | ink cells | font-exact | share | note |
|---|---|---|---|---|---|
| VGA 8x16 (kernel) | 14 corpus | 7871 | 7865 | 99.92% | per frame 99.78-100% |
| Unifont 16 | 3 | 1838 | 1836 | 99.89% | |
| EDK2 8x19 | 2 | 648 | 645 | 99.54% | frame 17: 106/107 |

Frame 15 (UEFI logo) is refused (anti-aliased, graphical). Series, 242 frames (no transcripts at all): 230 frames read, 10 no grid (uniform/near-empty), 1 refused
(logo), 1 `no_font`; **296,782 of 296,891 ink cells (99.963%) are font-exact**: Unifont 197,682/197,698 (99.992%, 109 frames), VGA 96,841/96,899 (99.940%, 116 frames,
min frame share 99.23%), EDK2 2,259/2,294 (98.47%, 6 frames, including the one `no_font` frame). A font counts as identified when its share is this high on every frame
of that font; no frame of any font is below 99.07% except the near-empty `no_font` EDK2 frame (13 cells, 0%).
Box drawing, the block elements and the arrows of the VGA/EDK2/Unifont tables are font-exact too (the repertoire includes them), which is why the dialogs
now read completely, border cells included.

**What matches nothing (109 cells, 0.037%, 13 of them the `no_font` frame):** the text cursor (VGA: 52 cells, an all-ones bottom-two-rows bar; also the cursor drawn over a glyph, e.g. `00007cc6...ffff`), which is
not a glyph of any font; 3 VGA cells that ARE glyphs of the full font file outside the repertoire (U+0393 Gamma, U+00E5, U+00C6 in garbled boot output); the
double dagger (U+2021, Unifont, 4 cells) = the glyph the transcriber could not name (`?`): the font file confirms the transcriber's `‡` guess; a few EDK2 cells that are
triangles/blocks drawn by GRUB itself (the arrows in the GRUB help text) and are not in the EDK2 table.

**Audit of the transcripts against the font reading** (placement by blank/ink pattern + agreement, never by transcript content for the comparison; 6,840 transcript
cells that are font-exact): **6,813 agree, 27 disagree, 0 aliases**. The 27: 26 are the title underline of frames 19 and 20 (transcript `-` x13 each, font `_`,
i.e. the transcript is wrong, independent confirmation of the conflict found earlier; with `ground-truth-corrections.json` applied: 0), 1 is the cursor cell in frame 20
(transcript `_`, font reading U+2581 because a cursor bar equals that glyph's bitmap: the font file cannot know about the cursor). Every other transcript cell agrees with the font
reading, including all 1,384 cells of frame 18 (the "possible mis-read digit" caveat in the README is not borne out) and all dialogs; 8 button lines (`< OK >`, `<Cancel>`)
could not be placed (multi-colour hotkey cells). No alias sets occur in the repertoires `ascii`, `ascii+box`, `latin1` for these fonts; aliases appear only when the repertoire is widened
(e.g. Cyrillic/Greek lookalikes in Unifont), and then the read returns the SET (status `a`, `res["aliases"]`), never a pick; tests exercise this with a synthetic font.

**Three evidence classes** (what a keystroke decision may rely on):
1. `F` font-exact: character from the verified font file. May be relied on (provided the font is the identified one for that screen, i.e. the evidence floor passed).
2. `e` learned, confirmed by >= 2 independent screen groups. May be relied on.
3. `s` single-source / `u` unknown / `a` alias set / `x` conflict / `f` fill / `m` multicolour: never relied on (an alias set is non-unique; `find_phrase(allow_alias=True)` reports such matches as `ambiguous`).
Learned entries keep the single-source rule; font entries are not subject to it (the file, not the screens, is the evidence). Use `Atlas.merge(font_atlas, learned_atlas)` for a console whose font is not in a file.

**Still not covered:** fonts not shipped in the corpus (another distro's console font, Terminus/larger HiDPI fonts such as 16x32, the Debian-installer's font), any glyph outside the
declared repertoire, the cursor (a runtime overlay, not a glyph: flag it, e.g. `cursor=(row, col)`), coloured status cells, graphical (UEFI/BIOS setup) screens, and scaled/anti-aliased captures.
To cover another system: add its font file (PSF from `console-setup`/`kbd`, or its kernel/firmware image) to `build_font_atlas.py`; no screenshots or transcripts needed beyond the identification check.
The identification check shows the three fonts here match their files at >= 99.07% per frame (excluding that near-empty frame); it does not prove the file is the font in other builds of the same distro.

## Revision 2 (review round 1): what changed and the corrected numbers

The first version (commit 04f40ef23) over-claimed. Review found, and this revision fixes:

1. **Misattributed headline.** The 2 "wrong" cells were frame 19 `_` cells (the cursor `_` and the `_` in `rcu_node`) read `-`. The title underline
   (13 cells in each of frames 19 and 20) was scored "exact-correct" because the transcript says `-`, but its bitmap is the
   `_` bitmap (atlas conflict `{'-': 26, '_': 2}`; a real hyphen in this font is mid-height). Scored against the corrected transcript
   (`ground-truth-corrections.json`, applied in memory, corpus untouched) the old rule gave **15 confidently wrong cells, not 2**
   (13 title cells in frame 19 + the 2 `_`). Cause: 26 labels came from two near-duplicate frames.
   Fix: a glyph label counts as **exact only when >= 2 independent screen groups confirm it** (`screen-groups.json`; near-duplicate
   frames share a group); otherwise the cell has status `s` (single-source), reported like unknown with `would_read` as evidence. The same
   leave-one-out run under the new rule yields **0 exact-wrong cells** and the 15 cells above are `s`.
2. **Evidence floor in `decode`.** The chosen font must read >= 8 ordinary (not bar/box-like) glyph cells exactly and >= 25% of them, else
   `status: "no_font"` and every cell UNKNOWN. Bar-like bitmaps (full-width rows, `_`, `-`, `=` rules, vertical bars) do not count as evidence.
   A run of >= 4 identical bar-like cells with no ordinary glyph beside it gets flag `r` (rule-line, e.g. a title underline); the last
   inked bar-like cell of a row gets `c` (cursor candidate), or the caller passes `cursor=(row, col)`. `find_phrase`/`region` exclude flagged
   cells by default (`exclude_flagged=False` to include). The previously reproduced case (2x nearest + 1 px offset reading 513 cells as exact `_`)
   now ends `no_font`/no exact cell (tests below).
3. **Scale probe.** It never proved 1:1 and now says so (`verdict: "crisp (1:1 NOT proven)"`). The colour-count heuristic (top-8 colours)
   was replaced by `ramp_ratio` (share of edge pixels that are a blended three-level step), which does not refuse colour-rich crisp consoles.
   Threshold calibration, held out: chosen on the even-numbered frames (+ 4 resampled variants of each), tested on the odd ones: max crisp ramp
   on the calibration half 0.000, min resampled ramp 0.390, chosen threshold 0.039 (default now 0.04); on the held-out half every resampled
   variant is refused (0 missed) and the only "false refusal" is frame 15, the anti-aliased UEFI logo (ramp 0.396), which is correctly refused;
   all 18 text frames of the test half and the calibration half have ramp 0.000. The gap is two orders of magnitude, but it is measured on ONE
   rendering stack (indexed-colour QEMU console): treat the threshold as unproven for JPEG/video-compressed capture paths.
   **The probe CANNOT prove 1:1.** Crisp non-integer nearest scaling, integer upscales with an odd offset or a letterbox, and a canvas that is
   simply a different size than the guest framebuffer all pass it. They are contained only by the font-consistency floor (no atlas font
   matches -> `no_font`), which is a consequence of the atlas, not a guarantee. The capture path must deliver the intrinsic bitmap and the
   consumer must verify canvas width/height against the guest framebuffer size independently.
4. **Held-out by screen group** added (`--loo` runs both).
5. Minor: whole-grid metric; `f` (fill) status for uniform cells next to text with a different background (excluded from phrases, not
   blank); cursor/rule flags; tolerant matching now needs a margin (second-best different character at least `margin`=2 farther) and reports
   the atlas minimum pairwise distance (+ a warning when `max_hamming` >= half of it); `find_phrase` word boundaries (on for alphanumeric
   phrases); `load_image` rejects > 8 MP; `Atlas.from_json` validates format/cell sizes/keys/counts with specific errors; in-sample test names say so.

### Numbers, 19 frames with a grid, 6795 non-space ground-truth cells (frame 15 refused)

Atlas learned from the ORIGINAL transcript unless noted. "exact" = exact AND correct; wrong = exact but different from the transcript.

| run | exact | single-source `s` | unknown | wrong | lines found | key phrases |
|---|---|---|---|---|---|---|
| old rule (min_groups=1), leave-one-frame-out, original GT (commit 04f40ef23) | 6687 (98.4%) | - | 106 | 2 | 222/265 | 22/26 |
| same, scored vs CORRECTED GT (derived: title cells now count wrong) | 6672 | - | 106 | **15** | - | - |
| **new rule, leave-one-frame-out, original GT** | 5891 (86.7%) | 798 (796 would read right, 2 wrong) | 106 | **0** | 172/265 | 12/26 |
| new rule, leave-one-frame-out, original atlas scored vs corrected GT | 5891 | 798 (783 right, **15 wrong**) | 106 | **0** | 172/265 | 12/26 |
| **new rule, leave-one-frame-out, corrected GT** | 5891 (86.7%) | 811 (all would read right) | 93 | **0** | 172/265 | 12/26 |
| **new rule, leave-SCREEN-GROUP-out, original = corrected GT** | 4940 (72.7%) | 1530 (22.5%) | 325 | **0** | 154/265 | 10/26 |

* The single-source rule demotes 798-811 cells (11.7-11.9%) from exact in leave-one-out and 1530 (22.5%) in group-out; all but 2 (original GT) of them
  would have been read correctly, i.e. the rule trades coverage for safety exactly where the evidence is one screen. By category (leave-one-out,
  corrected GT): console 4495/4813 exact, 268 single, 50 unknown; dialogs 1396/1398 exact (the dialogs share their glyphs with many screens);
  GRUB 0/584 exact (543 single, 41 unknown: every GRUB font is learned from at most 1-2 screens, so none of it is exact evidence).
* Group-out is what a new screen costs: 72.7% exact, 22.5% single-source, 4.8% unknown, still no confident wrong read in this corpus.
* The key-phrase count fell from 22/26 to 12/26 because most rare punctuation (`: / [ ] = @ ! ?`) and every GRUB glyph is single-source; with
  the full atlas the phrases read exact only where each glyph has two independent screens.
* Whole grid (leave-one-out, every non-blank cell of the 19 decoded grids, not only transcript cells): 10,646 non-blank, 5,904 exact (55.5%; box
  drawing, cursor, coloured cells and single-source cells are the rest), **13 exact cells outside the scored transcript cells** (listed per
  frame as `grid.exact_unscored` in the results JSON; not individually inspected), 48 exact cells carrying a rule/cursor flag, 31 fill cells. Transcript cells
  being a subset of the grid, the cell-accuracy headline above is "of cells the transcript names"; the whole-grid exact share is the lower number.
* The not-yet-rerun parts (learning curve, series coverage, colour-scheme table, nearest-glyph tolerance table below, in
  `glyphgrid-results.json`) are from the previous commit's rule (min_groups=1, no evidence floor, old probe); `glyphgrid-results-v2.json` has
  the new LOO/group-out/probe/scale results. They were not rerun because of the "nothing heavy" instruction; their qualitative conclusions
  stand but the exact-cell shares in them are upper bounds relative to the new rule.
* Scale variants of frame 18 under the new probe: original 1347 exact; 0.9x bilinear, 0.9x Lanczos and 1.25x bilinear refused (ramp 0.60, 0.46, 0.55);
  2x nearest undone (1347 exact); letterbox +37 px found (1347 exact); 1.5x nearest now ends `no_font` (before: status ok with 3317 unknown).
* Ground truth: the controller corrects frames 19/20 on main (title `-` -> `_`); this tool never edits the corpus, the correction is the
  in-memory `ground-truth-corrections.json` (`eval_glyphgrid.py --corrections`).

---

The sections below are the first version's text; where they conflict with Revision 2 (headline numbers, "2 wrong cells", the underline
attribution in "Ground-truth problems" item 1, "the probe catches resampling"), Revision 2 wins.

Prototype `pwmcp/tools/glyphgrid/`: deterministic glyph-grid matching for fixed-font console screenshots.
Measured on ONE ISO (TrueNAS 27.0.0-RC.1 vanilla, QEMU+OVMF) and its three fonts; nothing here generalises
beyond that without more captures. Raw numbers: `glyphgrid-results.json` (produced by
`eval_glyphgrid.py --loo --curve --colour --scale --run-series`). Corpus: 20 labelled frames
(`pwmcp/tests/corpus/console/truenas-27-vanilla/`, human-transcribed ground truth) and the 242-frame series
(no ground truth, coverage only).

## Headline

* Leave-one-frame-out (learn from 19 frames, read the 20th), 19 frames with a grid, 6795 non-space ground-truth
  cells: **6687 exact and correct (98.4%), 106 UNKNOWN (1.6%), 2 wrong-with-confidence (0.03%)**.
  Both wrong cells are one ground-truth/glyph conflict (below), not an engine guess; with the full atlas that
  bitmap is flagged `conflict` and reads UNKNOWN.
* Every one of the 106 unknown cells is an explicit `U+FFFD` with the nearest candidate and Hamming distance
  attached; none is a guess. 18 of them are characters that occur in no other labelled frame (`!`, `?`, `#`, `Y`, `;`,
  `=`, `@`, `z` ...): unavoidable with leave-one-out, fixed by labelling one more frame.
* Black-on-grey dialogs (the frames Tesseract lost completely): **1396/1398 exact (99.86%)**, 0 wrong.
* Tesseract (best setting psm 6 scale 3, no learning) for the same 20 frames: 0.585 mean character accuracy. glyphgrid
  LOO: **0.935** mean (the refused UEFI logo counted as 0). Mean exact-line recall 0.738 vs 0.408. The comparison is not
  symmetric: glyphgrid had labelled frames of the same fonts, Tesseract had none (and needs none).
* Key phrases (the 26 of OCR-EVAL.md), LOO, exact cells only: **22/26 found** vs 11/26 for Tesseract (p6 s3).
  Misses: `Proceed with the installation?` (`?` seen in one frame only), `The TrueNAS installation on vda succeeded!` (`!`
  seen in one frame only), `Loading initial ramdisk ...` (GRUB 8x19 font learned from 2 frames only), and
  `Enter an option from 1-10:` in frame 19 (see "stray cell" below). With the full atlas (learn on all, read the same frames) 25/26 are
  exact; the frame-19 prompt is the only miss, by design.
* Series (242 frames, learn-on-all, coverage only): 231 frames gridded, 10 not (uniform black or <12 text cells), 1 refused
  (UEFI logo). Of 297,915 non-blank cells **276,762 exact (92.9%)**; 15,329 of the unknown cells are box-drawing,
  the text cursor or multi-colour status cells that the transcribed ground truth never labels; excluding those the
  text-cell coverage is **97.9%** (VGA font 99.94%, installed-console font 97.1%, GRUB 8x19 97.6%). 111 of 231 frames
  read with zero unknown text cells. Remaining unknown text cells are a handful of glyphs never labelled (`M H N K P Z V 7` in the
  installed-console font).

## Leave-one-frame-out, per frame

Cells = non-space ground-truth cells aligned onto the grid (the `?` marker cells of frame 19 excluded). `exact` = exact
match AND equals ground truth. rows = aligned rows whose every cell is exact-correct. lines = ground-truth lines
found as an exact substring of a decoded row (alignment-free, comparable to the Tesseract "line" metric). char = 1 -
normalised Levenshtein of whitespace-collapsed text, box-drawing cells not counted as text (raw variant, box cells as
errors: 0.21-0.53 for dialogs, 0.98-1.00 for plain consoles).

| frame | kind | cells | exact | unknown | wrong | rows | lines | char (glyphgrid / Tesseract) |
|---|---|---|---|---|---|---|---|---|
| 01 grub iso menu | grub | 218 | 210 | 8 | 0 | 2/5 | 2/6 | 0.959 / 0.21 |
| 02 live copy squashfs | console | 122 | 122 | 0 | 0 | 4/4 | 4/4 | 1.000 / 0.98 |
| 03 live login banner | console | 390 | 385 | 5 | 0 | 4/7 | 4/7 | 0.989 / 1.00 |
| 04 installer console setup | dialog | 88 | 88 | 0 | 0 | 6/6 | 6/7 | 0.991 / 0.51 |
| 05 choose destination | dialog | 242 | 242 | 0 | 0 | 8/8 | 8/9 | 0.997 / 0.20 |
| 06 destination selected | dialog | 243 | 243 | 0 | 0 | 8/8 | 8/9 | 0.997 / 0.19 |
| 07 erase warning | dialog | 223 | 222 | 1 | 0 | 8/9 | 8/10 | 0.993 / 0.10 |
| 08 auth method | dialog | 91 | 91 | 0 | 0 | 4/4 | 5/5 | 1.000 / 0.66 |
| 09 password empty | dialog | 106 | 106 | 0 | 0 | 5/5 | 6/6 | 1.000 / 0.00 |
| 10 password filled | dialog | 130 | 130 | 0 | 0 | 5/5 | 6/6 | 1.000 / 0.00 |
| 11 progress over dialog | dialog | 171 | 171 | 0 | 0 | 7/7 | 8/8 | 1.000 / 0.00 |
| 12 progress extracting | console | 848 | 848 | 0 | 0 | 49/49 | 49/49 | 1.000 / 1.00 |
| 13 progress complete | console | 883 | 883 | 0 | 0 | 49/49 | 49/49 | 1.000 / 1.00 |
| 14 installation succeeded | dialog | 104 | 103 | 1 | 0 | 3/4 | 3/4 | 0.992 / 0.24 |
| 15 uefi logo | uefi | refused | - | - | - | - | 0/2 | 0 / 0.82 |
| 16 grub installed menu | grub | 260 | 232 | 28 | 0 | 1/7 | 1/8 | 0.890 / 0.98 |
| 17 grub booting installed | grub | 106 | 101 | 5 | 0 | 1/3 | 1/3 | 0.958 / 0.98 |
| 18 kernel boot log | console | 1384 | 1347 | 37 | 0 | 13/29 | 13/29 | 0.977 / 0.98 |
| 19 console setup + kernel noise | console | 587 | 578 | 7 | 2 | 17/20 | 17/20 | 0.981 / 0.93 |
| 20 console setup redraw | console | 599 | 585 | 14 | 0 | 24/26 | 24/26 | 0.978 / 0.92 |

Aggregates: console (7 frames) 4748/4813 = 98.65% exact, 63 unknown, 2 wrong; dialogs (9 frames) 1396/1398 = 99.86%, 2
unknown, 0 wrong; GRUB (3 frames, tiny training set for those fonts) 543/584 = 92.98%, 41 unknown, 0 wrong. Rows:
218/255 aligned rows exact (85.5%); lines 222/265 (83.8%). Per-frame mean: char 0.935, lines 0.738 (frame 15 counted as 0).
Unknown cells concentrate where a glyph is new to the training set: frame 18 (`=` 17x, `z`, `'`, `X`), frame 16 (GRUB 8x19 font:
only frame 17 as training data), frame 01.

Not scored in the cell metrics (the line could not be aligned to a row, listed in the report): `< OK >`/`< Yes >` button lines
(multi-colour hotkey cells) in frames 04-07, and the `Use the ^ and v keys ...` line (arrow glyphs) in GRUB frames 01 and 16: 6 lines of 265.
The alignment-free line metric does count them.

## Learning curve (VGA font, 14 frames, 6 random orders, held-out exact-correct share)

| frames learned | distinct glyph bitmaps | held-out exact share |
|---|---|---|
| 1 | 45 | 0.822 |
| 2 | 58 | 0.897 |
| 3 | 71 | 0.985 |
| 4-6 | 72-73 | 0.986-0.987 |
| 8 | 75 | 0.988 |
| 13 (of the 14 frames in this font, 1 held out) | 80 | 0.991 |

Saturation of *coverage of what the screens show* is fast (3 frames: 98.5%), saturation of the *alphabet* is slow: 82 distinct
bitmaps in the VGA font after 14 frames and still growing (printable ASCII is 95). The tail (rare punctuation,
capitals that happen not to occur) is what breaks "100%": each unseen glyph is one UNKNOWN until a frame containing it
is labelled. Total atlas: 82 (VGA 8x16) + 66 (installed-console/GRUB-ISO 8x16) + 54 (GRUB 8x19) bitmaps.

## Same glyph across colour schemes

Normalisation (foreground = minority colour of a 2-colour cell) works: of the 137 characters seen in two or more of the 15
fg/bg schemes in the corpus (grey on black, black on grey, white on blue, cyan on blue, yellow/red hotkeys on grey/blue, blue on grey
titles, reverse video highlights ...), **136 map to a single bitmap**. The one exception is the transcription conflict below. Reverse video
(white on blue highlight over a black-on-grey dialog, frames 04-08) reads exactly. Uniform cells are blank whatever their colour
(a solid-colour block cell is indistinguishable from a space by bitmap; fill colours are in `return_cells`).

## Fonts, GRUB, UEFI

* Three fonts were separated automatically (cell size + exact-hit overlap, learned phases stored in the atlas): the VGA-like
  8x16 live/installer font; an 8x16 Unifont-like font shared by the installed console (19, 20) AND the ISO GRUB screen (01, which
  in LOO read at 96% with the font learned from 19/20); the installed GRUB 8x19 font (16, 17).
* Cell origin is not always (0,0): GRUB 01 at (2,12), the installed console glyphs sit 2 rows down (0,2). The detector reports all
  equivalent origins; reading picks by exact atlas hits, and the atlas remembers the phases it was learned at (a
  crop-aligned 8x16 line from frame 20 only reads because of that; found by a test).
* No anti-aliasing anywhere: text frames have exactly 2 colours (dialogs 6-9, GRUB 5); the UEFI logo is the only frame with a colour
  continuum (65 colours). Anti-aliased text would be refused by the 3+ colour rule, not approximated.
* UEFI TianoCore splash: `no fixed grid found (37% of non-blank cells have 3+ colours)`, correct refusal (its two text lines
  are not recoverable by this engine; it is a logo with a tag line).
* Box drawing (`┌─┐│└┘`), the text cursor (bottom-two-rows block) and `[ OK ]`-style coloured cells are not in the transcribed
  ground truth, so they are UNKNOWN (15,329 cells in the series): by design they are never guessed, they can be added by a
  hand-labelled atlas entry (the most frequent unknown bitmaps in the series are Unifont capitals, then the cursor, `│` and `─`).
  The tool keeps them out of text: `boxlike()` marks ink touching the cell edge.

## Ground-truth problems found by the tool (listed, not averaged)

1. **Title underline of the installed console (frames 19, 20).** Ground truth: 13 `-` per frame (26 cells). The bitmap is a full
   7-pixel bar on pixel row 14, the same bitmap as the `_` cursor-underscore labelled `_` twice in frame 19; a real hyphen in the same font
   (`1-10`, `RC.1`) is mid-height. The tool reports one atlas conflict `{'-': 26, '_': 2}` and reads that glyph UNKNOWN (`conflict`).
   Most likely the line is made of underscores and the transcriber wrote hyphens. In LOO this shows as the only 2 wrong-with-confidence
   cells (training without frame 19 never sees the `_` evidence).
2. **`?` marker cells (frame 19).** Two `?` at the start of `?Tasks blocked` / `?(detected by` are the transcriber's "unreadable glyph" marker (README); two
   different bitmaps (they differ in the cursor row) are quarantined and never learned. A real `?` (frame 07) is learned normally.
3. **Frame 18 mis-read digit** noted in the README: no cell of frame 18 conflicts with any other frame's bitmap labels, so no
   evidence of a mis-transcribed digit was found (the reviewer's "verified as correct" stands for the bitmaps compared).
4. **Stray cell in frame 19:** the space between `from` and `1-10` in `Enter an option from 1-10:` is not blank on screen (a residual
   pixel group, unknown bitmap); the ground truth says space. With "exact cells only" the phrase `Enter an option from 1-10:` is therefore NOT matched
   in frame 19 (it IS in frame 20). That is the contract working: a consumer needing that prompt in a noisy frame should match words (`from`, `1-10:`)
   in a zero-unknown sub-region.
5. Quotes: GRUB `e'`/`c'` shapes are learned as whatever the transcript says (` and '); same bitmap for both would show as a conflict, none occurred.

## Resampled / scaled captures (scale probe)

The Netcup SCP screen is a 1280x800 `<canvas>` fed by VNC. Whether the guest console is shown 1:1, scaled or letterboxed is UNKNOWN to me.
Exact matching is only valid on the intrinsic 1:1 bitmap, so the engine refuses rather than approximates. Tested on frame 18 (1384 text cells):

| variant | probe | result |
|---|---|---|
| original | 2 colours, soft ratio 0.000 | 1384 exact, 0 unknown |
| 0.9x bilinear then back to 1280x800 | 171 colours, soft ratio 0.79 | **refused: `resampled: exact matching unavailable`** |
| 0.9x Lanczos then back | 222 colours, soft ratio 0.70 | **refused** |
| 1.25x bilinear (1600x1000) | 42 colours, soft ratio 0.35 | **refused** |
| 2x nearest (2560x1600) | integer upscale 2 detected | undone exactly: 1384 exact |
| 1.5x nearest (1920x1200) | crisp (2 colours), passes the blur probe | grid 12x8, no atlas font of that size: 0 exact, 3317 unknown |
| 1:1 with 37 px black top bar (letterbox) | crisp | origin found (0,5), 1384 exact |

`soft_ratio` = pixels outside the 8 commonest colours / non-background pixels; a crisp indexed console is 0, any resampling gives
tens of percent; the threshold is 0.05. Crisp non-integer nearest-neighbour scaling is not caught by the probe; it is caught
because the cells no longer match any atlas font (all UNKNOWN), which a consumer must treat as indeterminate. Integer
scale detection and undo, and letterbox offsets, work.

What a tolerant nearest-glyph fallback would need (the consumer is assumed to already know scale and grid, i.e. the best case; 1384 cells,
Hamming distance of the thresholded blurred cell to the true bitmap; nearest glyph accepted if unique within the threshold):

| round trip | median / p90 / max distance (px of 128) | tolerance 2 | tolerance 4 | tolerance 6 |
|---|---|---|---|---|
| 0.9x bilinear | 2 / 5 / 16 | 894 right, **7 wrong**, 483 unknown | 1228 / **7** / 149 | 1329 / **7** / 48 |
| 0.9x Lanczos | 0 / 1 / 6 | 1346 right, 0 wrong, 38 unknown | 1365 / **13** / 6 | 1365 / **19** / 0 |

A fallback needs tolerance 2-6 bits to be useful and then makes confident wrong readings (0.5-1.4% of cells) at every setting that
reads most cells. That contradicts the "UNKNOWN is never a guess" contract; recommendation: no tolerance, refuse, and fix the capture path
(capture the VNC/RFB framebuffer or `canvas.toDataURL()` at the canvas's intrinsic size with devicePixelRatio 1, instead of a scaled page screenshot).

## Failure modes (measured or by construction)

1. **Unseen glyph** -> UNKNOWN (always safe, costs coverage). 18 of 106 LOO unknowns; most of the rest are shapes that exist in the
   atlas for another char/font only.
2. **Unseen font** (new ISO, other distro, other console font) -> everything UNKNOWN until a few labelled frames are added. Nothing
   transfers between fonts except by exact bitmap identity (the ISO GRUB and the installed console happen to share one).
3. **Cell origin ambiguity**: blank margins make several origins equivalent; resolved by exact atlas hits and learned phases. A frame/crop
   whose blank-run differs and whose phase was never learned is read UNKNOWN, not wrongly (phase-search over all origins was tried and removed:
   it produced `.`->`_` confident errors).
4. **Grid detection on sparse screens**: free search (minimum description length) preferred a multiple of the true cell (16x16 for 8x16)
   on 56 of 242 series frames (mostly a static installer screen); with the atlas's known cell sizes tried first, all 231 non-blank frames
   resolve to a font. The detector is still a convenience for learning, not a safety property. Screens with <12 text cells have no grid.
5. **Non-text cells** (box drawing, cursor, coloured status blocks, 3+ colour cells) are UNKNOWN/multicolour; text next to
   them is unaffected.
6. **Ground-truth quality**: any transcription error becomes an atlas conflict or a wrong label; conflicts are reported, a wrong label
   with no counter-evidence is undetectable (frame 18 digits: no counter-evidence found).
7. **Alignment of ground truth** is by blank/ink pattern; lines with multi-colour hotkey cells or ambiguous patterns are skipped and listed (1 line
   per dialog frame), so the cell metrics slightly under-sample button lines.
8. **Timing**: ~1.2 s per frame in pure Python on a loaded shared host (grid detection 0.3-0.8 s dominated by Pillow crops); not tuned.

## What this cannot do

* Read anything that is not a 1:1 bitmap of a fixed bitmap font: scaled, blurred, JPEG-compressed, anti-aliased, proportional or
  graphical text (UEFI/BIOS setup menus in graphics mode, logos).
* Know a glyph it has not been shown. 100% on known cells is real; 100% on a new screen is not promised, and a new ISO needs
  labelled captures first.
* Separate a space from a solid-colour block, or distinguish glyphs that share a bitmap in one font (the `-` / `_` case above is
  surfaced as a conflict, not solved).
* Say anything about colour semantics (red/green status), only about the shape.

## Captures that would raise coverage

Label (even a handful of lines each) so the atlas stops having holes: (1) the Debian installer (newt dialogs, ncurses box set, full
ASCII incl. `! ? # % & @ ~ | \`), (2) systemd boot messages with the Linux console font (`[  OK  ]` coloured, uppercase `M H K N P Z V`), (3) GRUB
with real typographic quotes and arrows at 8x16 and 8x19, (4) BIOS/UEFI setup in text mode, (5) a console with box drawing and a visible cursor in both
phases (block/underline), (6) the same screens at another VGA mode (e.g. 1024x768 where the canvas may be scaled), (7) a full printable-ASCII test page in
each font (one frame labels all 95 glyphs: the fastest way to close the alphabet).
