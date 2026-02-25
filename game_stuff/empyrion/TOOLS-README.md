# Empyrion German Localization Tooling

This toolchain completes German localization for Empyrion CSV files while preserving control tags/placeholders.

## Covered files
- `Dialogues.csv`
- `Localization.csv`
- `PDA.csv`

Input discovery supports either:

- direct CSV files in `--base-dir`, or
- latest snapshot subfolder `YYYYMMDD-bNN` under `--base-dir`.

When item-name lock is enabled, `ItemsConfig.ecf` is also read from the selected input directory.

## Safety model
- Reads CSV via `csv.DictReader`/`DictWriter` (multiline + quoting safe).
- Protects immutable fragments before translation:
  - `{...}` placeholders
  - real `<tag...>` / `</tag...>` markup tags (tag-name based)
  - formatting tags (`[b]`, `[/b]`, `[c]`, `[-]`, etc.)
  - url/control tags (`[/url]`, `[url=...]`)
  - structured bracket markers (`[S-1]`, `[E-5]`, `[F-?]`, speaker labels like `[ IDA ]`)
  - color tags (`[00ff00]`)
  - control tokens (`@w3`, `@p9`, ...)
  - critical command literals (for example `give item Token 6995`)
  - escaped newlines (`\n`)
- Enforces glossary replacements (`glossary_de.csv`).
- Writes side-by-side outputs: `*.de.completed.csv`.
- MT row-review markdown now includes a non-protected bracket-label watchlist to highlight remaining potentially risky bracket text for manual review.
- Literal angle-bracket prose that is not real tag markup is translated as normal text (not protected).

## MT transport details (current)

- Production `translate-mt` transport uses direct `TKPHnTK` tokens for adjacent placeholder runs.
- For `--source-field source_masked`, MT prep uses exported `source_masked` + exported `protected` as the canonical mapping (no remask/reindex in this mode).
- Default transport wraps edge tokens in parentheses (for example `(TKPH0LTK)`), controlled by `mt.parenthesized_transport_token_edges`.
- Newline placeholders are converted to real newlines before MT request so paragraph structure is visible to MT.
- Adjacent-run detection merges placeholders separated by spaces/tabs, but never across newlines.
- Adjacent-run coalescing also handles bracket/paren separator patterns around transport tokens to improve restoration stability.
- Coalescing does not merge runs across sentence punctuation separators (`.`, `!`, `?`).
- After MT response, transport tokens are restored back to original placeholder runs before token-sequence QA.
- Before restore/coalesce, expected transport-token order from source payload is re-applied to reduce false reorder failures caused by MT swapping adjacent `TKPH` tokens.
- Newline placeholders are restored in order before final placeholder spacing normalization.
- Report fields for pipeline variables are emitted as top-level fenced `text` code blocks (review and failures reports).
- `translate-mt` output rows persist `source_masked` and `protected`, so `apply` can restore with the same mapping lineage used during transport.

## Final-output leak prevention gate

`qa_validate_tokens.py` now enforces two independent checks on final CSV output:

1. token parity (`missing_tokens`, `extra_tokens`), and
2. leak scan for internal tokens in `Deutsch`:
  - `__PH_n__`
  - `__BPH_n__`
  - `TKPH/TKBPH` transport tokens

Why this exists:

- parity-only checks can pass while internal tokens still leak into final CSV,
- leak scan catches restoration-lineage issues that are invisible to basic parity.

Operational effect:

- Step 5 now fails on leak tokens and emits them in `leak_tokens` for direct triage.

## Legacy prompt artifacts

- `export` still writes `<output>.prompt.txt` as a compatibility helper for older manual translation workflows.
- In current MT-first operation, this file is informational only and not consumed by `translate-mt`.
- Safe default: ignore it (or clean it with `cleanup-artifacts.sh`) when running MT-only.

## MT request bundling and split behavior (efficiency + limits)

`translate-mt` uses two levels of packing/splitting:

1. **Row batching** (throughput):
  - controlled by CLI `--batch-size` or TOML `mt.batch_size`.
  - row cap per scheduler batch (`0` means unlimited rows).

2. **Char-aware scheduler cap** (throughput + stability):
  - controlled by CLI `--batch-max-chars` or TOML `mt.batch_max_chars`.
  - caps total source chars per scheduler batch (`0` disables char cap).

3. **Provider request sizing** (hard limits):
  - `max_request_texts` = max row segments per API call,
  - `max_request_chars` = max total chars per API call,
  - `max_text_chars` = max chars for a single row segment.

If a row exceeds `max_text_chars` and `auto_split_long_texts=true`, it is split with placeholder-aware segmentation, translated in pieces, then reassembled before placeholder QA.
If a request would exceed `max_request_texts` or `max_request_chars`, `translate-mt` starts a new API call automatically.
In packed mode, projected request size includes separator overhead, and the effective packed cap is bounded by both request and per-text limits.
If a packed request still overflows at provider side, `translate-mt` downshifts that request (fewer rows) and retries instead of immediate hard-fail.

Result: higher MT efficiency without breaching provider payload limits.

## Quick start
Run from `game_stuff/empyrion`.

## Optional item-name English lock

Config (`mt.toml` or `mt.local.toml`):

```toml
keep_item_names_english_in_german = true
```

Effect:

- `export`: item name keys (derived from `ItemsConfig.ecf` `Name:` fields) are skipped.
- `apply`: those keys are forced to English in `Deutsch`.

## Risk classification (v2)

`export` computes per-row metadata from `compute_risk(...)`:

- `risk_version = v2`
- `risk_score` (weighted flag sum)
- `risk_level` (`low`/`medium`/`high`)
- `risk_flags` (triggered rule list)

Default thresholds used in this repo:

- `low`: score `< 3`
- `medium`: score `3..5`
- `high`: score `>= 6`

Operational routing:

- Bulk MT: typically low-risk rows
- Review-first chunks: medium/high, especially score `>= 6`

### Built-in risk utilities

Generate row distribution by risk score (plus level totals in console):

```bash
python3 empyrion_localize.py risk-report \
  --export-file ./reports/translation_units.risk.v2.jsonl \
  --output-csv ./reports/risk_distribution.v2.csv
```

Create random sample rows for selected risk classes:

```bash
python3 empyrion_localize.py risk-sample \
  --export-file ./reports/translation_units.risk.v2.jsonl \
  --output ./reports/translation_units.risk.sample10.medium_high.jsonl \
  --report-csv ./reports/translation_units.risk.sample10.medium_high.csv \
  --risk-levels medium high \
  --size 10
```

Run MT test translation on the sample before full translation:

```bash
python3 empyrion_localize.py translate-mt \
  --input ./reports/translation_units.risk.sample10.medium_high.jsonl \
  --output ./reports/translations.mt.sample10.medium_high.jsonl \
  --failures-output ./reports/mt_failures.sample10.medium_high.jsonl \
  --review-output ./reports/risk_score_class_samples.sample10.medium_high.md \
  --mt-config ./mt.toml \
  --mt-local-config ./mt.local.toml \
  --target-lang DE \
  --source-field english \
  --batch-size 10 \
  --max-parallel 2
```

Translate specific single rows by CSV `KEY` values:

```bash
python3 empyrion_localize.py translate-mt \
  --input ./reports/translation_units.risk.v2.jsonl \
  --output ./reports/translations.mt.keys.jsonl \
  --source-field english \
  --keys dialogue_iKK4CKC eden_pda_eGSGG
```

Translate specific rows by full export IDs (for triage/replay):

```bash
python3 empyrion_localize.py translate-mt \
  --input ./reports/translation_units.risk.v2.jsonl \
  --output ./reports/translations.mt.ids.jsonl \
  --ids-file ./reports/targeted_ids.txt \
  --target-lang DE \
  --resume
```

## MT dedupe + telemetry

- Request dedupe is enabled by default via `mt.dedupe_identical_mt_payloads=true`.
- Rows with identical `transport_payload` are collapsed to one provider request, then expanded back to all member rows.
- Success trace reports include:
  - `dedupe identical mt payloads`
  - `deduped duplicate rows saved`
- Runtime provider telemetry includes rows, words, chars, and durations per call and per provider summary.
- Per-call telemetry is emitted immediately at runtime; duplicate end-of-run replay of the same call list is removed.

## Fresh checkout runbook (from zero)

Prerequisites:
- Python 3 available as `python3`
- Source CSV files placed in `game_stuff/empyrion/input_data/`:
  - `Dialogues.csv`
  - `Localization.csv`
  - `PDA.csv`

Recommended clean start:
```bash
cd game_stuff/empyrion
rm -rf chunks* output* reports/*.jsonl reports/*.csv reports/*.txt
```

End-to-end MT pipeline from raw CSV to release:

Preferred runner (resume by default):

```bash
cd game_stuff/empyrion

# Resume existing work
./run-full-workflow.sh

# Restart from CSV only
./run-full-workflow.sh --clean

# Resume and promote failed rows with fallback translations
./run-full-workflow.sh --accept-qa-failed

# Reuse existing output + failures JSONL only (no MT re-translation)
./run-full-workflow.sh --promote-existing-failures

# Keep Step 5 token QA failures as report-only and continue release
./run-full-workflow.sh --promote-step5-failures-to-ok
```

Resume/promotion behavior:
- `translate-mt --resume` continues from existing output JSONL and skips already translated keys.
- `translate-mt --promote-failures-from <failures.jsonl> --promote-failures-only` promotes fallback rows without MT API calls.
- `translate-mt --resume-from-output <path>` resumes against a different output file explicitly.
- `run-full-workflow.sh` reads default Step 5 promotion from `mt.toml` `[workflow].promote_step5_failures_to_ok` (CLI flag still overrides).
- `translate-mt` emits heartbeat progress at `mt.status_interval_seconds` including done/remaining rows and words.

Equivalent explicit command sequence:

```bash
cd game_stuff/empyrion

# 1) Audit
python3 empyrion_localize.py audit --base-dir ./input_data --report-dir ./reports

# 2) Export with risk scoring
python3 empyrion_localize.py export \
  --base-dir ./input_data \
  --output ./reports/translation_units.risk.v2.jsonl \
  --risk-medium-threshold 3 \
  --risk-high-threshold 6 \
  --high-risk-min-score 6 \
  --high-risk-report ./reports/high_risk_samples.top100.csv \
  --high-risk-sample-size 100

# 3) MT translate (medium+high in this example)
python3 empyrion_localize.py translate-mt \
  --input ./reports/translation_units.risk.v2.jsonl \
  --output ./reports/translations.medhigh.v2.jsonl \
  --review-output ./reports/translations.medhigh.v2.review.md \
  --failures-output ./reports/translations.medhigh.v2.failures.jsonl \
  --target-lang DE \
  --batch-size 20 \
  --resume

# 3b) Resume later from same output file
python3 empyrion_localize.py translate-mt \
  --input ./reports/translation_units.risk.v2.jsonl \
  --output ./reports/translations.medhigh.v2.jsonl \
  --target-lang DE \
  --resume

# 3c) Promote existing failures from JSONL only (no MT re-translation)
python3 empyrion_localize.py translate-mt \
  --input ./reports/translation_units.risk.v2.jsonl \
  --output ./reports/translations.medhigh.v2.jsonl \
  --resume \
  --promote-failures-from ./reports/translations.medhigh.v2.failures.jsonl \
  --promote-failures-only

# 4) Apply to release target folder
python3 empyrion_localize.py apply \
  --base-dir ./input_data \
  --export-file ./reports/translation_units.risk.v2.jsonl \
  --translated-file ./reports/translations.medhigh.v2.jsonl \
  --out-dir ./output-all-real

# 5) Token QA on changed rows
python3 qa_validate_tokens.py \
  --changes-csv output-all-real/applied_changes.csv \
  output-all-real/Dialogues.de.completed.csv \
  output-all-real/Localization.de.completed.csv \
  output-all-real/PDA.de.completed.csv

# 6) Build release artifact (run from repository root)
cd /workspaces/vbpub
python3 release-all.py --project empyrion-translation --build

# 7) Push release
python3 release-all.py --project empyrion-translation --push
```

### Is previous “basic translation” required?

No. A prior baseline translation run is **not required**.
You can start from raw source CSVs and run the risk pipeline directly.
The previous baseline is optional only as historical context.

### Optional manual chunk workflow (legacy/fallback)

Chunk + prompt-based translation remains available for exceptional/manual scenarios, but is no longer the default production path.
If used, run `chunk`, produce `*.translated.jsonl`, then `merge` as before.

### 1) Audit missing/mixed German rows
```bash
python3 empyrion_localize.py audit --base-dir ./input_data --report-dir ./reports
```

### 2) Export translation units (MT default input)
```bash
python3 empyrion_localize.py export --base-dir ./input_data --output ./reports/translation_units.jsonl
```

Risk tuning options (for risk-focused MT routing/review):
```bash
python3 empyrion_localize.py export \
  --base-dir ./input_data \
  --output ./reports/translation_units.jsonl \
  --risk-medium-threshold 3 \
  --risk-high-threshold 6 \
  --high-risk-min-score 6 \
  --high-risk-report ./reports/high_risk_samples.csv \
  --high-risk-sample-size 100
```

Optional: create a stub JSONL template:
```bash
python3 empyrion_localize.py build-stub \
  --export-file ./reports/translation_units.jsonl \
  --output ./reports/translations.masked.jsonl
```

### 2b) Create chunk packs (legacy/manual fallback)
```bash
python3 empyrion_localize.py chunk \
  --export-file ./reports/translation_units.jsonl \
  --out-dir ./chunks \
  --size 200
```

Create a dedicated top-100 high-risk batch for manual quality inspection first:
```bash
python3 empyrion_localize.py chunk \
  --export-file ./reports/translation_units.jsonl \
  --out-dir ./chunks_highrisk_top100 \
  --size 100 \
  --split-by-risk \
  --high-risk-min-score 6 \
  --high-risk-top-n 100 \
  --high-risk-only
```

Then prepare lower-risk chunks for bulk translation:
```bash
python3 empyrion_localize.py chunk \
  --export-file ./reports/translation_units.jsonl \
  --out-dir ./chunks_lowrisk \
  --size 200 \
  --split-by-risk \
  --high-risk-min-score 6 \
  --standard-only
```

Artifacts:
- `chunks/chunk_XXXX.jsonl` (input rows)
- `chunks/chunk_XXXX.prompt.txt` (prompt instructions)
- `chunks/chunks_index.csv` (worklist)

Risk chunk options:
- `--high-risk-min-score`: minimum score for high-risk bucket.
- `--high-risk-top-n`: keep only top N high-risk rows.
- `--high-risk-only`: emit only high-risk chunk files.
- `--standard-only`: emit only standard/lower-risk chunk files.

If you only want rows where German is empty:
```bash
python3 empyrion_localize.py chunk \
  --export-file ./reports/translation_units.jsonl \
  --out-dir ./chunks \
  --size 200 \
  --skip-existing
```

After translating chunk files into `chunk_XXXX.translated.jsonl`, merge them:
```bash
python3 empyrion_localize.py merge \
  --in-dir ./chunks \
  --pattern "*.translated.jsonl" \
  --output ./reports/translations.masked.merged.jsonl
```

### 3) Provide translated JSONL
Expected format per line:
```json
{"id":"Dialogues.csv:42:txt_xxx:abc123", "translation_masked":"...__PH_0__..."}
```
Rules:
- Keep `id` unchanged.
- Keep every `__PH_n__` token unchanged.

### 4) Apply translations and generate completed files
```bash
python3 empyrion_localize.py apply \
  --base-dir . \
  --export-file ./reports/translation_units.jsonl \
  --translated-file ./reports/translations.masked.merged.jsonl \
  --out-dir ./output
```

Generated files:
- `output/Dialogues.de.completed.csv`
- `output/Localization.de.completed.csv`
- `output/PDA.de.completed.csv`
- `output/applied_changes.csv`

### 5) Validate token parity
```bash
python3 qa_validate_tokens.py \
  --changes-csv output/applied_changes.csv \
  output/Dialogues.de.completed.csv \
  output/Localization.de.completed.csv \
  output/PDA.de.completed.csv
```

Use `--full-file` only if you intentionally want to see legacy mismatches already present in original files.

## Notes
- The `apply` command also uses in-file translation memory (same English text already translated in German elsewhere) when no explicit JSONL translation exists.
- Edit `protect_patterns.txt` and `glossary_de.csv` to tune behavior.
- Default protection now also keeps common control markers and command literals stable during MT (for example `[/url]`, `[S-1]`, `[ IDA ]`, and console commands like `give item Token 6995`).


## Modular MT providers (DeepL + Google)

### Setup
1) Copy config template and add local API keys:

```bash
cp mt.sample.toml mt.toml
# optional local machine override (gitignored)
cp mt.sample.toml mt.local.toml
```

2) Edit `mt.toml` and set:
- `providers.deepl.api_key`
- `providers.google.api_key`
- `mt.parenthesized_transport_token_edges = true|false` (default: `true`, recommended)
- provider policy controls per service:
  - `providers.<name>.enabled = true|false`
  - `providers.<name>.weight = <int>` (higher = used more often)
  - `providers.<name>.max_text_chars = <int>` (0 = unlimited; per text)
  - `providers.<name>.max_request_texts = <int>` (0 = unlimited; per API request)
  - `providers.<name>.max_request_chars = <int>` (0 = unlimited; total chars per API request)
  - `providers.<name>.auto_split_long_texts = true|false` (split long masked text before request)

Recommended sparse-cost policy:
- Keep `providers.deepl.weight` low (for expensive/quota-limited usage).
- Prefer higher weights for lower-cost providers when available.

3) Optional override rules:
- `mt.toml`: shared local base config for this workspace
- `mt.local.toml`: optional local override merged on top of `mt.toml`

Both files are gitignored.

4) Install provider libraries (optional for non-stdlib providers):

```bash
pip install google-api-python-client
```

Notes:
- `google` provider uses official Google Cloud Translate API (requires API key).
- `easygoogletranslate` provider is implemented in-repo via a dedicated Google mobile wrapper (`google_mobile_translate.py`) and does not depend on the external `easygoogletranslate` pip package.

### MT translate command

Translate chunk JSONL directly with providers in parallel:

```bash
python3 empyrion_localize.py translate-mt \
  --input chunks/highrisk_chunk_0001.jsonl \
  --output chunks/highrisk_chunk_0001.translated.jsonl \
  --mt-config mt.toml \
  --mt-local-config mt.local.toml \
  --providers deepl google easygoogletranslate \
  --max-parallel 2 \
  --failures-output reports/mt_failures_highrisk_0001.jsonl
```

Optional full-coverage mode (accept unresolved QA rows that still have a masked MT output):

```bash
python3 empyrion_localize.py translate-mt \
  --input reports/translation_units.risk.v2.jsonl \
  --output reports/translations.mt.full_accepted.jsonl \
  --mt-config mt.toml \
  --mt-local-config mt.local.toml \
  --providers deepl google easygoogletranslate \
  --resume \
  --failures-output reports/mt_failures.full_accepted.jsonl \
  --review-output reports/risk_score_class_full_accepted.md \
  --treat-remaining-failures-as-ok
```

Runtime behavior (automatic):
- Provider preflight check runs first (probe translation: Hello world.) and reports per-provider availability.
- Translation requests always use pass1-only direct transport:
  - prefers `source_masked` when present in the input JSONL
  - otherwise derives masked text by placeholder protection before sending to MT
  - converts placeholder runs to direct MT-safe transport tokens (`TKBPHnTK`)
  - converts newline placeholders to real line breaks before MT so paragraph structure remains visible
  - restores `TKBPHnTK` back to the original placeholder runs after MT
  - restores newline placeholders in deterministic order before final spacing normalization
  - enforces token boundary spacing to reduce grammar bleed across placeholder boundaries
  - applies provider-specific request sizing (`max_request_texts`, `max_request_chars`) automatically
  - if a row exceeds provider `max_text_chars`, it can be split into smaller masked segments and reassembled after translation
- Provider routing is weighted and config-driven:
  - providers with `enabled = false` are skipped
  - providers with `weight = 0` are skipped
  - higher `weight` values receive proportionally more batch assignments
- If all providers become unavailable, remaining batches are marked as provider_unavailable in failures output.

Row-level status semantics in review markdown:
- `mt_error`: provider-stage transport/quota/rate/runtime failure.
- `qa_error`: placeholder integrity failed after provider response (`token_drop`, `token_reorder`, `token_insert_dup`).
- The review now shows the full lifecycle per row:
  - `en_original_raw` (original English, JSON-escaped so control chars are visible)
  - `source_masked_placeholders` (canonical placeholder sequence used for expected-token QA)
  - `en_sent_to_mt_normalized` (exact normalized payload sent to MT)
  - `de_returned_by_mt_raw` (raw provider return)
  - `de_final_game_ready` (placeholder-restored German written by `apply`)
- Internal masked fields are hidden by default:
  - `source_masked_internal` (protected/masked English intermediate)
  - `translation_masked_final` (post-processed masked German used for QA)
- Show internal masked fields explicitly with `--include-internal-masked-fields`.
- `translate-mt` always writes a failures markdown report:
  - if failures remain, it includes complete failure diagnostics for pass1-only direct transport
  - if no failures remain, it explicitly states that no errors remain
- `--treat-remaining-failures-as-ok` promotes rows that still failed QA but have `translation_masked` output into the translated JSONL.
  - promoted rows remain listed in failures JSONL/markdown for manual QA inspection
  - this enables "0 untranslated rows" CSV output when remaining failures are placeholder-QA-only
  - rows with no translation payload (provider hard failure) still remain in failures
- CLI override flags:
  - `--parenthesized-transport-token-edges` forces parenthesized edge transport tokens
  - `--no-parenthesized-transport-token-edges` disables parenthesized edge transport tokens

Why the current strategy is stable:
- There is only one translation pass, so there is no pass-transition drift between two transport formats.
- Direct transport tokens (`TKBPHnTK`) are simple, explicit anchors around markup/control runs.
- Real newlines are visible to MT, preserving sentence/paragraph context that reduces punctuation and grammar distortion.
- Placeholder QA validates ordered token sequence and catches drops/reorders immediately.
- Newline placeholders are excluded from token-drop QA comparison to avoid false positives from formatting-only line-break handling.

### Example walkthrough: `eden_pda_eGSGG` (why each stage exists)

This section explains the full row lifecycle using:
- row key: `PDA.csv:3077:eden_pda_eGSGG`
- reference review: `reports/rowtest_dialogue_WOQKWiC_plus_eGSGG_paren_config_on_afterfix17_20260224.review.md`

The goal is to make each transformation explainable, reversible, and QA-verifiable.

1) **`en_original_raw` (source truth from dataset)**
- What it contains:
  - original English text with game markup (`[b]`, `[c]`, `[-][/c]`)
  - escaped line breaks (`\\n`) and inline formatting structure
- Why this stage exists:
  - provides the canonical semantic content and markup shape
  - lets reviewers verify whether later German output preserved structure and intent

2) **`source_masked_placeholders` (canonical placeholder stream)**
- What happens:
  - every protected markup/control span is converted into ordered placeholders (`__PH_n__`)
  - plain-language segments remain visible between placeholders
- Why this stage exists:
  - defines the exact expected token order for placeholder QA
  - decouples linguistic translation from markup integrity
  - prevents translators/providers from mutating control syntax directly

3) **`en_sent_to_mt_normalized` (transport-safe MT payload)**
- What happens:
  - placeholder runs are compacted into MT transport anchors (`TKPHnTK` with optional edge flags)
  - newline placeholders are materialized as real line breaks before MT
- Why this stage exists:
  - compact anchors survive MT better than raw placeholder noise
  - real paragraph boundaries improve grammar/punctuation quality in MT output
  - edge flags preserve adjacency intent near lexical text

4) **`de_returned_by_mt_raw` (provider output before restoration)**
- What it shows:
  - direct provider text result (German)
  - transport anchors still present, lexical content translated
- Why this stage exists:
  - isolates provider behavior from local post-processing
  - enables root-cause analysis: provider drift vs restoration/spacing logic

5) **`de_final_game_ready` (restored game markup output)**
- What happens:
  - transport anchors are restored to original placeholder runs
  - placeholders are restored to original game control markup
  - deterministic spacing normalization is applied
- Why this stage exists:
  - produces immediately deployable game text with original control syntax
  - ensures consistent formatting and readability while preserving structure

6) **Placeholder QA (`qa_status`, `qa_error`)**
- What is validated:
  - expected placeholder sequence from `source_masked_placeholders`
  - actual restored sequence in MT result
  - error classes: `token_drop`, `token_reorder`, `token_insert_dup`
- Why this stage exists:
  - fail-fast safety net against silent structural corruption
  - guarantees markup/control integrity independently from linguistic quality

### What was fixed in afterfix17 (spacing, not placeholder integrity)

Observed in earlier iteration (`afterfix16`):
- in-flow spaces were occasionally removed near styled segments in final German text
- example symptom in `eden_pda_eGSGG`: missing readability spaces around style boundaries in long lines

Fix applied:
- refined tag-space compaction so removal after opening tags only applies at line start (indent cleanup), not globally within flowing text

Why this is the correct fix:
- preserves legitimate lexical spacing between adjacent style segments
- still removes unwanted leading whitespace artifacts after newline boundaries
- keeps placeholder QA behavior unchanged (this is a formatting/readability correction layer)

Validation outcome (`afterfix17`):
- rows considered: 2
- translated: 2
- failed: 0
- `qa_status`: passed for both rows, including `eden_pda_eGSGG`

### Canonical order: CSV -> translated artifact (with manual LLM gate)

For release-quality output, keep this exact sequence:

1) `audit`
2) `export`
3) `chunk`
4) **Manual LLM/Copilot step** using generated `*.prompt.txt` + chunk JSONL input
5) `merge`
6) `apply`
7) `qa_validate_tokens.py`
8) `release-empyrion-translation.py` (build artifact)
9) optional publish (`--publish-github` or release manager push flow)

The manual step between `chunk` and `merge` is intentional for intelligent linguistic review/translation of high-risk rows.

### Sample-only test run (MT + QA inspection)

Run MT on a small subset first before full dataset:

```bash
python3 empyrion_localize.py translate-mt \
  --input reports/translation_units.risk.v2.jsonl \
  --output reports/translations.mt.sample.jsonl \
  --source-field source_masked \
  --mt-config mt.toml \
  --mt-local-config mt.local.toml \
  --providers deepl easygoogletranslate \
  --sample-mode \
  --sample-size 200 \
  --max-parallel 2
```

Rate/usage behavior:
- Per-provider retries with backoff on retryable failures.
- Automatic provider disable on quota/rate-limit exhaustion.
- Remaining active providers continue processing pending batches.

Then apply + QA on sample output paths before running full translation.

Deterministic sample option:
- Add `--sample-seed <int>` to make sample row selection reproducible (same input + same seed => same selected rows/order).
- Omit `--sample-seed` when you want fresh random coverage each run.

### MT telemetry and request-size statistics

`translate-mt` now prints timestamped progress and request telemetry automatically:

- `[STEP] <UTC timestamp> ...` for major phases
- Per-provider totals:
  - `calls`, `ok`, `error`
  - `rows_total` and `rows/req`
  - `words_total` and `words/req`
  - `sec_total`, `sec/req`, `sec_min`, `sec_max`
- Per-call log entries with timestamp, provider, rows, words, chars, duration, status, and error text
- Per-call lines are printed immediately when each provider call completes

If you want to save telemetry output for later analysis:

```bash
python3 empyrion_localize.py translate-mt \
  --input reports/translation_units.risk.sample10_per_score.0_9.jsonl \
  --output reports/translations.mt.sample10_per_score.0_9.jsonl \
  --failures-output reports/mt_failures_sample10_per_score.0_9.jsonl \
  --mt-config mt.toml \
  --target-lang DE \
  --source-field english \
  --batch-size 20 \
  --max-parallel 2 \
  2>&1 | tee reports/mt_sample10.telemetry.log
```

## Fresh stratified sample-10 rerun (risk scores 0-9)

Use this when you want a clean, reproducible sample run from the initial CSV-derived pipeline.

### 1) Cleanup temporary artifacts

```bash
cd game_stuff/empyrion
find . -type f -name '.tmp_*.py' -delete
rm -f \
  reports/mt_failures_sample*.jsonl \
  reports/translations.mt.sample*.jsonl \
  reports/risk_score_class_samples.sample*.md \
  reports/chat_repairs.sample*.jsonl \
  reports/translation_units.risk.sample10_per_score.0_9.jsonl
```

### 2) Rebuild audit/export from source CSVs

```bash
cd game_stuff/empyrion
python3 empyrion_localize.py audit --base-dir . --report-dir ./reports
python3 empyrion_localize.py export \
  --base-dir . \
  --output ./reports/translation_units.risk.v2.jsonl \
  --risk-medium-threshold 3 \
  --risk-high-threshold 6 \
  --high-risk-min-score 6 \
  --high-risk-report ./reports/high_risk_samples.top100.csv \
  --high-risk-sample-size 100
```

### 3) Build stratified sample (10 rows per score class)

```bash
cd game_stuff/empyrion
python3 - <<'PY'
import json
from pathlib import Path

source = Path('reports/translation_units.risk.v2.jsonl')
out = Path('reports/translation_units.risk.sample10_per_score.0_9.jsonl')

by_score = {score: [] for score in range(10)}
with source.open('r', encoding='utf-8') as f:
    for line in f:
        if not line.strip():
            continue
        row = json.loads(line)
        score = int(row.get('risk_score', 0))
        if 0 <= score <= 9:
            by_score[score].append(row)

selected = []
for score in range(10):
    selected.extend(by_score[score][:10])

with out.open('w', encoding='utf-8') as f:
    for row in selected:
        f.write(json.dumps(row, ensure_ascii=False) + '\n')

print(f"[INFO] Wrote stratified sample rows={len(selected)}: {out}")
PY
```

### 4) Run MT on sample (row-treatment review markdown is generated by `translate-mt`)

```bash
cd game_stuff/empyrion
python3 empyrion_localize.py translate-mt \
  --input ./reports/translation_units.risk.sample10_per_score.0_9.jsonl \
  --output ./reports/translations.mt.sample10_per_score.0_9.jsonl \
  --failures-output ./reports/mt_failures_sample10_per_score.0_9.jsonl \
  --review-output ./reports/risk_score_class_samples.sample10_per_score.md \
  --mt-config ./mt.toml \
  --target-lang DE \
  --source-field english \
  --batch-size 20 \
  --max-parallel 2
```

Outputs:
- `reports/translations.mt.sample10_per_score.0_9.jsonl`
- `reports/mt_failures_sample10_per_score.0_9.jsonl`
- `reports/mt_failures_sample10_per_score.0_9.md`
- `reports/risk_score_class_samples.sample10_per_score.md` (generated by `translate-mt`; includes full EN→MT→final-DE lifecycle fields, provider-vs-QA error separation, and a non-protected bracket-label watchlist)


## Generic score-9 quality refinement (no manual key targeting)

Use this when you want a systematic second pass for high-risk rows (e.g. `risk_score >= 9`) that may still be literal/awkward after normal translation.

### 1) Build quality candidates from current merged translation

```bash
python3 empyrion_localize.py quality-audit \
  --export-file ./reports/translation_units.risk.v2.jsonl \
  --baseline-translated-file ./reports/translations.all.jsonl \
  --current-translated-file ./reports/translations.integrated.v3.jsonl \
  --min-risk-score 9 \
  --max-quality-score 7 \
  --output ./reports/highrisk_quality_candidates.jsonl \
  --report-csv ./reports/highrisk_quality_candidates.csv
```

What this does:
- Normalizes English and German text (markup removed for scoring only).
- Compares baseline vs current translation (`unchanged_vs_baseline`).
- Applies quality heuristics (word order/article/verb plausibility flags).
- Emits only rows needing refinement (low quality score).

### 2) Create bulk refinement chunks for chat

```bash
python3 empyrion_localize.py quality-chunk \
  --candidates-file ./reports/highrisk_quality_candidates.jsonl \
  --out-dir ./chunks_quality_score9 \
  --size 120
```

This generates:
- `quality_chunk_XXXX.jsonl` with `source_masked` + `current_translation_masked`
- `quality_chunk_XXXX.prompt.txt` for chat-based intelligent revision

### 3) Merge refined chunks and re-apply

```bash
python3 empyrion_localize.py merge \
  --in-dir ./chunks_quality_score9 \
  --pattern "quality_chunk_*.translated.jsonl" \
  --output ./reports/translations.quality-refined.jsonl

python3 empyrion_localize.py apply \
  --base-dir . \
  --export-file ./reports/translation_units.risk.v2.jsonl \
  --translated-file ./reports/translations.quality-refined.jsonl \
  --out-dir ./output-all-real
```

### 4) Validate token safety

```bash
python3 qa_validate_tokens.py \
  --changes-csv output-all-real/applied_changes.csv \
  output-all-real/Dialogues.de.completed.csv \
  output-all-real/Localization.de.completed.csv \
  output-all-real/PDA.de.completed.csv
```


## Risk score distribution (current dataset, 24767 entries)

low: 13830
medium: 3764
high: 7173
medium+high (score >= 3): 10937
Full histogram CSV: risk_distribution.v2.csv

