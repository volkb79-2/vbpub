# Fonts roadmap (later stage, NOT built)

Status: the atlas covers **bitmap console fonts** (evidence class F: the exact 1-bit cell comes from a font file, see `NOTICE.md`, `fonts-console-extra.manifest.json`).
This list is what a desktop-terminal stage would need. Licence statements below are from general knowledge of each font's upstream licence and must be
re-checked against the exact package/file before anything is shipped (none of these was downloaded or inspected for this note).

| font | licence / redistributable? | notes |
|---|---|---|
| Consolas | Microsoft proprietary (ships with Windows/Office). **Not redistributable**; no atlas may be committed or built into a public image. | Matching would need the user's own Windows install at build time, or glyphs learned from their screenshots (class 2 only, never class F). |
| Lucida Console | Proprietary (Bigelow & Holmes, licensed to Microsoft). **Not redistributable.** | Same as Consolas. Largely replaced by Consolas/Cascadia in new Windows tools. |
| Cascadia Code / Cascadia Mono | SIL OFL-1.1 (Microsoft, github.com/microsoft/cascadia-code). Redistributable with the licence text; Reserved Font Name rules apply to derived fonts. | Windows Terminal default. Variable + static builds; ligatures (Code) change glyph runs, Mono has none: use Mono for cell matching. |
| DejaVu Sans Mono | Bitstream Vera licence + public-domain DejaVu changes; free to redistribute (Debian `fonts-dejavu-mono`). | Default of many Linux terminals/xterm.js fallbacks. |
| Liberation Mono | SIL OFL-1.1 (2.x; 1.x was GPL-2 with font exception). Debian `fonts-liberation`. | Metric-compatible with Courier New. |
| Noto Mono / Noto Sans Mono | SIL OFL-1.1 (Debian `fonts-noto-mono`, `fonts-noto-core`). | Large coverage; Noto Sans Mono is the better terminal face. |
| Ubuntu Mono | Ubuntu Font Licence 1.0 (free but with its own naming/derivative conditions; not OSI-approved). Debian `fonts-ubuntu`. Needs a licence review before it goes into an image. | Ubuntu desktop terminal default for years. |
| Cantarell | SIL OFL-1.1 (GNOME). **It is a proportional UI sans, not a monospace font**, so it never appears as a terminal cell font; GNOME's monospace UI font is now Adwaita Mono (OFL-1.1) / earlier Source Code Pro/Monospace. | Listed because the request named it; nothing to build for terminals. |

## Why these are a different problem (not "more PSF files")

The existing engine relies on three properties that vector desktop fonts do not have:

1. **The glyph is a fixed 1-bit bitmap.** A console font file *is* the pixels. A TrueType/OpenType font is an outline; the pixels come from a rasteriser
   (FreeType, DirectWrite, CoreText) with settings that are not in the font file. The atlas key (exact bit pattern) would have to be produced by rendering,
   and the rendering must be the same one the guest used.
2. **Binary foreground/background.** Anti-aliased glyphs are gray-level (or, with ClearType/LCD filtering, per-colour-channel) blends of foreground and
   background. `glyphgrid` normalises a cell to a 1-bit mask and refuses three-colour cells; AA text has a continuous alpha plane that must be recovered
   (needs both colours known, gamma, and text-contrast settings; colour-on-colour makes it ambiguous).
3. **Integer cell grid.** Terminals place glyphs on an advance-width grid, but sizes in pt x DPI x display scale give fractional advances; cell width/height
   are rounded by the terminal (VTE, xterm.js, Windows Terminal each differently), and glyph origins may be subpixel positioned.

## What has to be known to match one

* exact font file + version (hinting tables and metrics change between releases), weight/style, and whether bold is the real face or synthesised;
* point size, DPI, display scale factor, and the terminal's own cell-size rounding rule;
* rasteriser and flags: FreeType version, hinting mode (none / light / full TrueType bytecode / autohint), `FT_LOAD_TARGET_*`, LCD filter; or
  DirectWrite rendering mode (grayscale vs ClearType), gamma, enhanced contrast, ClearType level; CoreText font smoothing;
* subpixel order (RGB/BGR/none) and filter weights: ClearType splits the horizontal resolution by 3 and colour-fringes the edges;
* text and background colours and any compositor gamma/colour management; transparency or blur behind the terminal;
* font fallback chain for glyphs the face lacks (box drawing is often drawn by the terminal itself, not the font: Windows Terminal and kitty/alacritty
  draw their own box and block characters, so the font file does not even contain the pixels).

## Suggested plan (when this is wanted)

1. Treat it as a new evidence class between F and e: **R, rendered-and-calibrated**. Never "font-exact"; the claim is "this cell is within a measured distance of
   the rendering of glyph g at settings S, and no other glyph is closer by a margin", with S itself verified from a calibration screen (a known string
   rendered by the target terminal, compared at several candidate settings).
2. Start with grayscale AA, no hinting (FreeType `FT_LOAD_NO_HINTING`, DirectWrite grayscale) for the OFL fonts where a deterministic render is
   available in the sidecar (Liberation/DejaVu/Noto/Cascadia); defer ClearType (needs the subpixel model) and the proprietary fonts (no redistributable
   source; learn from the user's own screenshots only).
3. Match on the alpha plane (recover alpha per pixel from the two known colours), with correlation plus an explicit second-best margin, and keep UNKNOWN
   as the default as in the bitmap engine.
4. Keep rendering configuration in the atlas provenance (rasteriser version and flags) so an atlas is reproducible from the distro package, as for the
   bitmap fonts (`NOTICE.md`, "Regenerating the atlas at image build time").
