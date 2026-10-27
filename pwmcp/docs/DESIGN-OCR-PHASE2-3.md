# Design note: console corpus as a release asset, and OCR phases 2-3 (PWMCP-11)

Status: design for the NEXT stream (nothing in sections 2-3 is implemented). Written 2026-10-27 from the
state of main plus the unmerged glyphgrid (fonts + speed), imagegen and canvas-capture branches.

## 1. The console corpus as a release asset (implemented in this wave)

The 242-frame TrueNAS install capture (5.4 MB of 1280x800 PNG) is too big for git. The repository carries
its INDEX (`tests/corpus/console/truenas-27-vanilla/series-manifest.jsonl`: file, sha256, size per frame,
already committed) and its PIN (`tests/corpus/console/CORPUS-ASSETS.json`: release tag, asset name, URL,
tar sha256, size, frame count). The frames themselves are a GitHub release asset.

Mechanism (`tools/corpus/console_corpus.py`, stdlib only, tests in `tests/test_console_corpus.py`):

| Item | Decision |
|---|---|
| Release | a dedicated data release `pwmcp-console-corpus-truenas-27-vanilla-v1` (immutable, never edited). It must NOT start with `pwmcp-v` (cmru's tag prefix scan) and a pwmcp release cannot carry it: the gate runs BEFORE the pwmcp tag exists, so an asset "next to the pwmcp release" could never be fetched by that release's own gate. The pwmcp release notes link it. |
| Asset | `pwmcp-console-corpus-truenas-27-vanilla-v1.tar`, deterministic ustar of `frames/<NNNN_Ns.png>` (sorted, uid/gid 0, mtime 0, no compression: PNG does not compress). Reproduced byte-identically from the same frames (checked twice). sha256 `38b287087c42b45e47d25083363490e7853fd043d2a5f8be26dffec4bd480eb2`, 5365760 bytes. A copy sits in the wave evidence directory. |
| Integrity | three layers: tar sha256 + size (pin) BEFORE extraction; extraction accepts only exactly the indexed names as regular files (no traversal, no extras, no duplicates); every extracted frame is verified against the index sha256 and PNG dimensions. `fetch` is idempotent and does no network access when `<dest>/frames` already verifies. Redirects only to https on the GitHub asset hosts. |
| Where gates find it | gate lanes run network-disabled, so a lane never fetches. The corpus is a declared dependency of the TESTER IMAGE (same doctrine as `fetch-cli-extended.py`): the image build runs `fetch --dest /opt/pwmcp-console-corpus`. Developers run `python3 pwmcp/tools/corpus/console_corpus.py fetch` once (cache `~/.cache/pwmcp-console-corpus`); `PWMCP_CONSOLE_CORPUS_DIR` overrides and, if set but not verifying, is an error (never a silent fall-through). |
| Failure mode | `locate()` raises `CorpusMissing` naming the paths tried and the fetch command; the two real-corpus tests turn that into a test FAILURE through `_testdeps.missing` (same policy as every other missing test dependency; `PWMCP_ALLOW_MISSING_TEST_DEPS=1` is the developer opt-out). |
| Tests that use it | `test_real_corpus_is_complete_and_verified` (all 242 frames, sha256, 1280x800) and `test_curated_frames_are_byte_identical_members_of_the_full_series` (the 20 committed curated frames are exact members of the pinned series). Phases 2-3 add corpus-driven recognition regressions on top. |

Publish procedure (operator or controller, once, BEFORE the tester image is rebuilt and BEFORE the pwmcp r4 gate):

```
gh release create pwmcp-console-corpus-truenas-27-vanilla-v1 \
  --repo volkb79-2/vbpub --title "pwmcp console corpus: TrueNAS 27.0.0-RC.1 vanilla install (242 frames)" \
  --notes "Screen output of public software (TrueNAS, GRUB, Linux, OVMF) used as test fixtures. Index: pwmcp/tests/corpus/console/truenas-27-vanilla/series-manifest.jsonl. sha256 38b287087c42b45e47d25083363490e7853fd043d2a5f8be26dffec4bd480eb2" \
  /home/vscode/vbpub-release-program-20261027/evidence/pwmcp-console-corpus-truenas-27-vanilla-v1.tar
python3 pwmcp/tools/corpus/console_corpus.py fetch --dest /tmp/corpus-check   # must print .../frames and exit 0
```

Tester-unified change (controller owns that tree; patch text in the wave report): COPY the tool, the pin and the
index into the image under `/usr/local/lib/tester-unified/pwmcp/...` (the tool locates the pin relative to its
own path) and `RUN python3.14 .../console_corpus.py fetch --dest /opt/pwmcp-console-corpus`. Order matters:
publish the data release, rebuild tester-unified, THEN gate pwmcp; merging the tests first turns the pwmcp
r1 lane red by design.

Check before publishing: `cmru cleanup --remove-assets` must not touch a release whose tag is outside every
project's prefix (it ran on 2026-10-21 with a 20-day age). Read its dry run first.

## 2. OCR phase 2: exact glyph-grid recognition in the sidecar

Inputs that exist: `tools/glyphgrid` (pure function, stdlib + Pillow, `read_image(png, atlas) -> dict` with
`status`, `grid`, `rows`, `conf`, `flags`, `aliases`, `unknown_cells`, `font_share`; decision helper
`strict_match`), the sidecar `Engine` protocol and `build_registry()` slot (`glyphgrid` is registered as
`UnavailableEngine`, HTTP 501), `python3-pil` already in the image, and (after the merges) the fonts atlas,
the 5x faster `read_image` with `reference_mode()` as its bit-identical oracle, and the imagegen suite.

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
  `screenshot` -> Tesseract only. Auto routing may widen what is READ, never what is TRUSTED: the future
  `wait_for_text` / expect-and-send primitive accepts only an `exact` glyphgrid match (`strict_match`, boundary
  cells exact, zero bad cells in the region) and treats a Tesseract hit as advisory, never as a keystroke trigger.

### 2.4 Declared-repertoire homoglyph hint (optional, off by default)
- Query `homoglyphs=latin-only|none|declared-repertoire` (default `latin-only`, the glyphgrid default) and, only
  with `declared-repertoire`, `repertoire=latin` (a closed enum; extendable by a reviewed change). The consumer
  thereby states that the screen is Latin text, so Cyrillic/Greek lookalikes the bitmap cannot distinguish are
  accepted. The policy is echoed in `policy{}`; the sidecar never infers it. The client exposes it as
  `--homoglyphs`/`--repertoire` and never sets it implicitly; a keystroke primitive that uses it must log it.
- Tests: imagegen "A"-flagged ambiguous cells (complement twins, `IMAGEGEN-FINDINGS.md`) must read as aliases
  under `latin-only`, as the unique Latin character under `declared-repertoire` only when every other member is
  inside the declared repertoire, and as refused under `none`.

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
6. Memory/time: re-measure the 512m cap with a 24 MP image and glyphgrid at `max_seconds`, and the R2 runtime
   for the added judged code (see PWMCP-12).

Order: WP1 adapter + Dockerfile + `/v1/engines` -> WP2 schema/params/refusals -> WP3 `source`/`expect_size` ->
WP4 client routing + `--homoglyphs` -> WP5 canvas composition (needs canvas-capture merged) -> WP6 corpus
regressions -> docs/backlog. Release as r5 (or later r4 follow-up) once PWMCP-12 is not the only evidence gap.
