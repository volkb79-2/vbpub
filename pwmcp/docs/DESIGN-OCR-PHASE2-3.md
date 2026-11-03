# Design note: local console corpus and OCR phases 2-3 (PWMCP-11)

Status: the local-corpus verifier is implemented. Sections 2-3 describe future OCR phases; the sidecar and
GlyphGrid development tooling are already on main. Written 2026-11-03 against the 1.64.0-r4 release state.

## 1. Local console corpus

The complete 242-frame TrueNAS install capture remains outside git and is never published. The repository
keeps its frame index (`tests/corpus/console/truenas-27-vanilla/series-manifest.jsonl`: file, SHA256, width,
and height per frame) and a local-source pin (`tests/corpus/console/CORPUS-ASSETS.json`: source variable,
deterministic archive name, tar SHA256 and size, frame count). Curated screenshots from public, anonymous,
agnostic sources may remain in git; generic reader tests should use generated per-font alphabets whenever
real console output is not part of the behavior being tested.

Mechanism (`tools/corpus/console_corpus.py`, stdlib only, tests in `tests/test_console_corpus.py`):

| Item | Decision |
|---|---|
| Source | `PWMCP_CONSOLE_CORPUS_DIR` must name an absolute local directory containing `frames/`. It is the only source; there is no download URL, cache, image path, or fallback. |
| Pin | `CORPUS-ASSETS.json` records `source`, the deterministic tar name, SHA256 `38b287087c42b45e47d25083363490e7853fd043d2a5f8be26dffec4bd480eb2`, size `5365760`, frame count, and index path. No release tag or URL is stored. |
| Integrity | The verifier requires exactly the indexed regular files, checks each frame SHA256 and PNG dimensions, then rebuilds the sorted ustar (uid/gid 0, mtime 0, mode 0644) and compares its size and SHA256 with the pin. The package command writes the same deterministic tar for independent comparison. |
| Missing data | If the variable is unset, `locate()` raises `CorpusMissing`. The two real-corpus tests skip with that reason; `PWMCP_REQUIRE_CONSOLE_CORPUS=1` makes the absence a failure. An explicitly supplied but invalid path always fails. |
| Gates | The registered gate lanes do not include or require the 242-frame capture. Nothing is added to tester-unified. |
| Tests | `test_real_corpus_is_complete_and_verified` checks the complete local series; `test_curated_frames_are_byte_identical_members_of_the_full_series` verifies committed curated frames against it. Both need the explicit local path. |

The [consumer guide](CONSUMERS.md#local-console-corpus) has a pasteable local verification example. No archive is
published, linked from a release, or fetched by a test.

## 2. OCR phase 2: exact glyph-grid recognition in the sidecar

Inputs that exist on main: `tools/glyphgrid` (pure function, stdlib + Pillow, `read_image(png, atlas) -> dict` with
`status`, `grid`, `rows`, `conf`, `flags`, `aliases`, `unknown_cells`, `font_share`; decision helper
`strict_match`), the sidecar `Engine` protocol and `build_registry()` slot (`glyphgrid` is registered as
`UnavailableEngine`, HTTP 501), the committed fonts atlas, the optimized `read_image` with
`reference_mode()` as its bit-identical oracle, and the imagegen suite. GlyphGrid remains development tooling;
it is not included in the OCR image.

### 2.1 Packaging into the image
- `COPY tools/glyphgrid/glyphgrid.py tools/glyphgrid/fontfiles.py` to `/opt/pwmcp-ocr/glyphgrid/` and the
  shipped atlas (`tests/fixtures/glyphgrid/fonts.atlas.json`, font-FILE derived, evidence class F; about 92 KB) to
  `/opt/pwmcp-ocr/atlases/`. Do NOT ship the learned TrueNAS atlases by default (class e/s evidence, one ISO's
  screens); they stay an explicit, named, allowlisted second atlas (`atlas=truenas-27`) added only with a recorded
  provenance, never a path from the request.
- Include `tools/glyphgrid/NOTICE.md` (font licences) under `/usr/share/doc/pwmcp-ocr/` and keep the NOTICE hash
  check that the fonts branch added. Image stays read-only rootfs, no network, same uid; no new apt packages.
- `Engine.version()` returns the glyphgrid module version plus the atlas sha256; `/v1/engines` lists it with
  `available: true`; the `/contract` `ocr.engines` block follows.

### 2.2 Engine adapter and result schema (`GlyphgridEngine`)
- `recognize()` calls `read_image(png, atlas, font=params.font, grid_hints=[(8,16)] when given,
  max_seconds <= OCR_TIMEOUT_S - 2)`. Never `reference_mode()` (a thread-unsafe module global; the service
  serves requests from threads under a one-at-a-time slot, so also never share a `GlyphReader`: the sidecar is
  stateless, `read_image` per request).
- The existing `Word` list cannot carry a grid. Add a `grid` object to the JSON response ONLY for this engine
  (additive; Tesseract replies unchanged): `status`, `font`, `font_share`, `grid{cols,rows,cell_w,cell_h,origin_x,origin_y}`,
  `rows[]` (U+FFFD for non-exact cells), `conf[]`, `flags[]`, `aliases`, `unknown_cells[]`, `policy{homoglyphs,repertoire}`.
  This resolves hazard (a) of the backlog (column layout lost) and (c)/(d) (digits and calibration): nothing is
  scored, a cell is exact or it is not.
- Words are derived from runs of consecutive exact non-blank cells in a row (`line` = grid row, `box` = the cell
  rectangle in original pixels). `conf` stays null (unscored): a made-up 100 would read as calibrated. Each word
  gains `exact: true`; the Tesseract words gain nothing.
- Non-`ok` statuses (`no_grid`, `resampled`, `no_font`, `mixed_font`) return HTTP 200 with `indeterminate: true`,
  empty `text`, and the engine's `message`: a refusal is a result, not an error, and never silently becomes
  Tesseract output inside the sidecar. Malformed/oversized input keeps the existing 400/413 codes.
- Parameters accepted for `engine=glyphgrid`: `font` (an id the atlas lists), `atlas` (allowlisted name),
  `grid_hint` (`WxH`), `homoglyphs` (2.4), `expect_size` (3.2). REJECTED with a specific 400: `scale`, `psm`,
  `lang`, `whitelist`, `min_conf`, `drop_low_conf` (meaningless or dangerous: scale resamples the exact grid;
  tolerant `max_hamming` is not exposed at all).
- `prepare_image` must pass the pixels through untouched for this engine (no scaling; alpha flatten and palette
  to RGB only, with the original dimensions kept for `expect_size`).

### 2.3 `source` hint and routing
- Request parameter `source=canvas|screenshot|unknown` (default `unknown`). `canvas` is asserted only by the
  client when the bitmap came from `pwmcp canvas-capture` (intrinsic bitmap, stable, 1:1-checked). The sidecar
  records it and uses it for one decision: for `source=screenshot` the glyphgrid engine refuses up front
  (`indeterminate`, reason "CSS-size capture resamples the cell grid", hazard (b)); for `unknown` it runs its normal
  scale probe, which refuses blended edges but cannot prove 1:1 (README: the consumer verifies the size).
- Routing lives in the CLIENT (`pwmcp_client.ocr.recognize(engine="auto")`, `pwmcp ocr --engine auto`), because
  the sidecar is a pure function: `canvas` + verified geometry -> glyphgrid first; if `indeterminate` or
  `font_share < 0.9` -> Tesseract, and the result carries `engine_used`, `fallback_reason` and `exact: false`;
  `screenshot` -> Tesseract only. The sidecar should expose glyphgrid's own decision (`expect=<phrase>` -> `strict` block in the response, with `font_is_file`, `font_share` and an optional `cursor=row,col` request parameter) so the client does not rebuild `strict_match` from a partial schema; the glyphgrid README prefers it server-side. Cells read `H` carry their character in `rows` but are not `exact` unless the homoglyph policy accepts them. `read_image` must be called with `auto_unscale=False` for `source=canvas` with `expect_size`, otherwise a 2x nearest-neighbour capture sized to match passes while the grid is read from the downscaled image. For `no_grid`/`resampled` the `grid`/`font`/`font_share`/`flags` fields are null. Auto routing may widen what is READ, never what is TRUSTED: the future
  `wait_for_text` / expect-and-send primitive accepts only an `exact` glyphgrid match (`strict_match`, boundary
  cells exact, zero bad cells in the region) and treats a Tesseract hit as advisory, never as a keystroke trigger.

### 2.4 Declared-repertoire homoglyph hint (optional, off by default)
- Query `homoglyphs=latin-only|none|declared-repertoire` (default `latin-only`, the glyphgrid default) and, only
  with `declared-repertoire`, `repertoire=latin` (a closed enum; extendable by a reviewed change). The consumer
  thereby states that the screen is Latin text, so Cyrillic/Greek lookalikes the bitmap cannot distinguish are
  accepted. The policy is echoed in `policy{}`; the sidecar never infers it. The client exposes it as
  `--homoglyphs`/`--repertoire` and never sets it implicitly; a keystroke primitive that uses it must log it.
- Tests: imagegen "A"-flagged ambiguous cells (complement twins, `IMAGEGEN-FINDINGS.md`) must read as aliases
  under `latin-only`, as the unique Latin character under `declared-repertoire` when the consumer has declared the screen Latin
  (the Cyrillic/Greek lookalikes the bitmap cannot tell apart are then accepted, as in the glyphgrid README), and as refused under `none`.

## 3. OCR phase 3: canvas bitmap input and the watcher

### 3.1 `pwmcp ocr --canvas SELECTOR`
Composes the canvas-capture branch (`pwmcp_client.canvas`, stable + 1:1-checked intrinsic bitmap) with 2.3:
capture the canvas bitmap, POST it with `source=canvas&expect_size=<cw>x<ch>`, route as above. The capture
refusal states (unstable canvas, size mismatch) are surfaced as indeterminate, not retried silently.

### 3.2 `expect_size`
The sidecar compares the decoded PNG size with `expect_size` (the consumer's independent knowledge of the guest
framebuffer, hazard (b)) and answers `indeterminate` on mismatch. This is the only 1:1 evidence the glyphgrid
README says is missing from the scale probe.

### 3.3 Watcher and expect-send (PWMCP-11 phases 2b/3 as already decided)
Unchanged operator decisions: in-browser canvas change detection (hash on animation frames, debounce) then
recognise on change, `wait_for_text(pattern|regex, timeout, stable)`, unrecognised screens INDETERMINATE; the
guarded expect-and-send stays OFF unless a config flag enables it (no allowlist/rules file in pwmcp). PWMCP-10
(one shared browser, "Browser is already in use") must be answered first, since a watcher occupies it.

## 4. Test and gate plan for the next stream
1. Adapter unit tests at 100% line and branch (add `containers/ocr/ocr_service.py` is already judged; the new adapter
   lives in it or in a sibling file added to `assay.toml` targets). `glyphgrid.py` itself joins the judge only as
   a separate decision (1,600 lines); its identity tests (fast vs `reference_mode`, imagegen suite, 109 cases,
   about 2 minutes, patch `test_glyphgrid_generator_identity.py` in the wave report) are the correctness oracle.
2. Corpus-driven regressions using `console_corpus.locate()`: for the curated expectations in
   `tests/fixtures/glyphgrid/expected.json` and the 242-frame series, `strict_match` finds "Console setup" and the
   installer/finish signatures on the right frames and on NO other frame (a negative sweep is the point).
3. Contract tests for the new fields (`grid`, `indeterminate`, `policy`, `exact`, `engine_used`), rejection
   tests for every forbidden parameter, `source=screenshot` refusal, `expect_size` mismatch.
4. Image: build + run the sidecar with the glyphgrid engine and POST a corpus frame (the first test that runs
   inside the OCR image; the real-Tesseract test can move to the same lane with `PWMCP_REQUIRE_TESSERACT=1`).
5. Docs: README "Overridable ..." sibling section, CLI-SPEC/cli-review cases for the new options (strict mode:
   every active reviewed case needs a collected test), CHANGES entry, backlog PWMCP-11 phase status.
6. Memory/time: re-measure the 512m cap with a 24 MP image and glyphgrid at `max_seconds`; the R2 follow-up is
   tracked in PWMCP-20.

Order: WP1 adapter + Dockerfile + `/v1/engines` -> WP2 schema/params/refusals -> WP3 `source`/`expect_size` ->
WP4 client routing + `--homoglyphs` -> WP5 canvas composition (once canvas capture is available) -> WP6 corpus
regressions -> docs/backlog. Keep any full-corpus acceptance run opt-in and local; it does not become a release
or gate dependency. R2 evidence is tracked in PWMCP-20.
