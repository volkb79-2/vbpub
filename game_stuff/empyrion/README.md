# Empyrion German Localization Completion (Reforged Eden 2)

## Why this was done

In-game text was still partially English while playing in German (especially with Reforged Eden 2 content).  
The goal is to make German (`Deutsch`) consistently available across the main localization CSV files while keeping all gameplay/UI control syntax intact, independent of MT provider choice.


## Build and push
- Build + publish a single project:
```bash
python3 release-all.py --project empyrion-translation --build --push
```

- Publish only (no rebuild):
```bash
python3 release-all.py --project empyrion-translation --push
```

Empyrion project note:
- `game_stuff/empyrion/release-empyrion-translation.py` reads from fixed folder `game_stuff/empyrion/output-all-real`.
- To avoid path confusion, keep the latest approved translation outputs synced into that folder before running release commands.

### Full workflow from raw CSV to release

Recommended one-command runner (resume by default):

```bash
cd game_stuff/empyrion

# Resume existing work (default)
./run-full-workflow.sh

# Start fresh from CSV only (remove previous generated artifacts)
./run-full-workflow.sh --clean

# Resume and carry over QA-failed rows that still have fallback translations
./run-full-workflow.sh --accept-qa-failed

# Reuse existing output + failures JSONL only (no MT re-translation)
./run-full-workflow.sh --promote-existing-failures

# Keep Step 5 token QA failures as report-only and continue release
./run-full-workflow.sh --promote-step5-failures-to-ok
```

Resume/promotion behavior:
- `--resume` continues from existing output JSONL and only schedules remaining rows.
- `--promote-existing-failures` skips MT API calls and promotes existing fallback rows from `reports/translations.mt.full.failures.jsonl`.
- `--accept-qa-failed` applies in-run promotion while translating.
- `--promote-step5-failures-to-ok` keeps Step 5 failures in reports but does not stop release build/push.
- You can set default Step 5 promotion in `mt.toml` via `[workflow].promote_step5_failures_to_ok = true` (CLI still overrides).
- Translation emits interval heartbeat progress using `mt.status_interval_seconds` with done/remaining rows and words.

Start in `game_stuff/empyrion`:

```bash
cd game_stuff/empyrion

# 1) Audit source CSV rows (input_data/*.csv)
python3 empyrion_localize.py audit --base-dir ./input_data --report-dir ./reports

# 2) Export JSONL used by MT (creates reports/translation_units.risk.v2.jsonl)
python3 empyrion_localize.py export \
   --base-dir ./input_data \
   --output ./reports/translation_units.risk.v2.jsonl

# Optional: after export, re-translate only rows whose English had fused control tags
# (IDs generated at reports/control_tag_spacing_ids.txt by default)
python3 empyrion_localize.py translate-mt \
   --input ./reports/translation_units.risk.v2.jsonl \
   --output ./reports/translations.mt.controlfix.jsonl \
   --ids-file ./reports/control_tag_spacing_ids.txt \
   --target-lang DE \
   --resume

# 3) MT translation (full set)
python3 empyrion_localize.py translate-mt \
   --input ./reports/translation_units.risk.v2.jsonl \
   --output ./reports/translations.mt.full.jsonl \
   --target-lang DE \
   --resume

# 3a) Re-translate targeted rows by IDs (from triage/export reports)
python3 empyrion_localize.py translate-mt \
   --input ./reports/translation_units.risk.v2.jsonl \
   --output ./reports/translations.mt.targeted.jsonl \
   --ids-file ./reports/targeted_ids.txt \
   --target-lang DE \
   --resume

# 3a-alt) Re-translate targeted rows by CSV KEY values
python3 empyrion_localize.py translate-mt \
   --input ./reports/translation_units.risk.v2.jsonl \
   --output ./reports/translations.mt.keys.jsonl \
   --keys dialogue_C04saqW eden_dialogue_mSiKCu0 eden_pda_eGSGG \
   --target-lang DE \
   --resume

# 3b) Resume later from same output file
python3 empyrion_localize.py translate-mt \
   --input ./reports/translation_units.risk.v2.jsonl \
   --output ./reports/translations.mt.full.jsonl \
   --target-lang DE \
   --resume

# 3c) Greenlight existing failures JSONL without re-running MT
python3 empyrion_localize.py translate-mt \
   --input ./reports/translation_units.risk.v2.jsonl \
   --output ./reports/translations.mt.full.jsonl \
   --resume \
   --promote-failures-from ./reports/translations.mt.full.failures.jsonl \
   --promote-failures-only

# 4) Apply translated rows to final deliverable folder used by release
python3 empyrion_localize.py apply \
   --base-dir ./input_data \
   --export-file ./reports/translation_units.risk.v2.jsonl \
   --translated-file ./reports/translations.mt.full.jsonl \
   --out-dir ./output-all-real

# 5) Token QA on changed rows
python3 qa_validate_tokens.py \
   --changes-csv ./output-all-real/applied_changes.csv \
   ./output-all-real/Dialogues.de.completed.csv \
   ./output-all-real/Localization.de.completed.csv \
   ./output-all-real/PDA.de.completed.csv
```

Then run release from repository root (`/workspaces/vbpub`):

```bash
cd /workspaces/vbpub

# 6) Build release artifact
python3 release-all.py --project empyrion-translation --build

# 7) Push release
python3 release-all.py --project empyrion-translation --push
```


## Scope

We process these source files in the `input_data` folder:

- `Dialogues.csv` — NPC dialogues, interaction text, narrative snippets
- `Localization.csv` — UI labels, item/block names, system strings
- `PDA.csv` — mission/chapter text, logs, long-form story content

Input discovery behavior:

- If `--base-dir` directly contains all three CSV files, that directory is used.
- Otherwise, the tool auto-selects the latest snapshot subfolder matching `YYYYMMDD-bNN` (for example `20260225-b41`) that contains all three CSV files.

All use the same column model:

`KEY, English, Deutsch, ...other languages...`

Target behavior:

- Fill empty `Deutsch` cells
- Replace obvious English leftovers in `Deutsch`
- Preserve all control/formatting tokens and placeholders exactly

## What data is where

### Source data
- `input_data/<snapshot>/Dialogues.csv`
- `input_data/<snapshot>/Localization.csv`
- `input_data/<snapshot>/PDA.csv`
- `input_data/<snapshot>/ItemsConfig.ecf` (required only when item-name English lock is enabled)

### Tooling
- `empyrion_localize.py` — audit/export/chunk/merge/apply pipeline
- `qa_validate_tokens.py` — token parity validator
- `protect_patterns.txt` — immutable token regex patterns
- `glossary_de.csv` — glossary replacements (consistency)
- `TOOLS-README.md` — command usage reference
- `cleanup-artifacts.sh` — cleanup helper for temporary artifacts

### Intermediate artifacts
- `reports/audit_summary.json` — counts by file/category
- `reports/audit_candidates.csv` — candidate rows found
- `reports/translation_units.jsonl` — masked translation units
- `chunks_full/chunk_XXXX.jsonl` — chunked translation work items
- `chunks_full/chunk_XXXX.translated.jsonl` — translated chunks
- `reports/translations.all.jsonl` — merged translated payload
- `reports/translation-success.md` — success trace
- `reports/translation-failures.md` — failure traces
- `reports/*.prompt.txt` — legacy helper prompts for manual workflows (safe to ignore in MT-only flow)

### Final output
- `output-all-real/Dialogues.de.completed.csv`
- `output-all-real/Localization.de.completed.csv`
- `output-all-real/PDA.de.completed.csv`
- `output-all-real/applied_changes.csv` (change log)

Path meaning:

- `output-all-real/` is the canonical final deliverable set.

## Processing pipeline (how it worked)

1. **Audit**
   - Detect rows with empty German or obvious English in German.

2. **Extract & mask**
   - Export candidates to JSONL.
   - Protect immutable fragments with placeholders like `__PH_0__`.

3. **Translate with MT (default production path)**
   - Run `translate-mt` over export JSONL (optionally filtered by risk/class/sample/keys).
   - Translator sends `source_masked` and receives `translation_masked` with placeholder QA.
   - For `--source-field source_masked`, `translate-mt` now treats exported `source_masked` + exported `protected` as canonical and keeps that mapping stable end-to-end.
   - `translate-mt` output rows now also include `source_masked` and `protected`, so downstream `apply` can restore with the exact mapping used during transport.
    - Optional: `translate-mt --treat-remaining-failures-as-ok` can promote remaining QA-failed rows (when they still contain a usable masked translation) so final apply can produce full CSV coverage with no untranslated rows left.
    - Promotion can also be driven from an existing failures JSONL without retranslation via `--promote-failures-from ... --promote-failures-only`.
      - `--resume-from-output` can be used to resume from a different output file path explicitly.
    - Retry behavior is split:
       - MT/provider errors: `mt.max_attempts` + `mt.provider_error_batch_max_attempts` (default 3)
       - Row-level QA failures: `mt.qa_failure_batch_max_attempts` (default 1 = no retries)

4. **Merge**
   - Merge all translated chunk outputs into one JSONL.

5. **Apply**
   - Restore protected tokens into translated text.
   - Write side-by-side completed CSV files (`*.de.completed.csv`).

6. **Validate**
   - Run token parity QA on changed rows.
   - Verify placeholders/tags/control codes remained valid.
   - Explicitly fail if final CSV still contains internal tokens (`__PH_n__`, `__BPH_n__`, `TKPH...`).

### Why this additional leak scan is required

Token parity alone is not sufficient: a row can preserve token sets and still leak internal placeholders into final CSV if restore mapping drifts.

The final validation gate now checks:

- token parity (`missing_tokens`, `extra_tokens`), and
- final-output leak tokens (`leak_tokens`) in `Deutsch`.

This closes the gap where `translation_masked` looked structurally valid but `de_final_game_ready` still contained internal transport/mask tokens.

## MT transport (current production flow)

`translate-mt` now uses direct word-token transport for adjacent placeholder runs.

1. Mask source into placeholders (`__PH_n__`).
2. Keep newline placeholders as real paragraph newlines for MT payload shaping.
3. Convert adjacent placeholder runs directly to transport tokens (`TKPHnTK`, default wrapped with parentheses/edge flags, e.g. `(TKPH0LTK)`).
4. Send normalized payload to MT.
5. Restore transport tokens back to original placeholder runs after MT.
6. Restore newline placeholders and enforce placeholder-token sequence QA.
7. Restore final game markup for CSV output.

Transport behavior constraints:

- Placeholder cluster detection merges placeholders separated by spaces/tabs only.
- Coalescing of transport token clusters does not merge across sentence punctuation boundaries (`.`, `!`, `?`).

## Item-name English lock (optional)

You can keep item display names in English in German output via config:

```toml
keep_item_names_english_in_german = true
```

Behavior:

- Export: item-name rows are skipped from MT input by deriving item keys from `ItemsConfig.ecf` `Name:` attributes.
- Apply: those keys are forced to English in the `Deutsch` column.
- Scope: item names only; non-item rows (status effects, dialogue, descriptions) are unaffected.

### MT request efficiency and max-size protection

The MT path is optimized for throughput while protecting provider limits:

- **Scheduler batching**: rows are grouped by both row count and optional char budget:
   - `batch_size` (CLI `--batch-size`, TOML `mt.batch_size`) is the row cap (`0` means unlimited rows),
   - `batch_max_chars` (CLI `--batch-max-chars`, TOML `mt.batch_max_chars`) is the per-scheduler-batch char cap (`0` disables char cap).
- **Per-provider request caps**: each provider can define
   - `max_request_texts` (max rows per API call),
   - `max_request_chars` (max total chars per API call),
   - `max_text_chars` (max chars per single row).
- **Packed request sizing** (easygoogle packed mode):
   - final packed payload length is pre-computed with separator overhead (`\n<separator>\n` between rows),
   - effective packed request cap is bounded by both `max_request_chars` and `max_text_chars` (when set),
   - row packing is reduced in advance so packed requests stay under provider limits.
- **Automatic request splitting**:
   - If a row exceeds `max_text_chars` and `auto_split_long_texts=true`, the row is split into placeholder-safe subsegments.
   - If adding a row would exceed `max_request_texts` or `max_request_chars`, a new API call is started.
   - If a provider still returns a packed overflow error, the current request batch is automatically downshifted (split into fewer rows) and retried.
   - Segmented row responses are reassembled back to a single row before QA/apply.
- **Fail-fast toggle**: set `auto_split_long_texts=false` to fail on oversize rows instead of splitting.

This means we can send larger batches for efficiency without breaching provider payload limits.

### MT request dedupe and telemetry

- `translate-mt` can dedupe identical MT payload rows before calling providers (`mt.dedupe_identical_mt_payloads`, default `true`).
- Dedupe preserves per-row output by fanning one translated representative result back to all member rows.
- Success reports include dedupe context (`dedupe identical mt payloads`, `deduped duplicate rows saved`).
- Provider telemetry now tracks both words and chars:
   - per-provider totals (`words_total`, `chars_total`),
   - per-request averages (`words/req`, `chars/req`),
   - per-call runtime lines including `words`, `chars`, and `duration_sec`.
- Each provider call line is emitted immediately when the call finishes; duplicate end-of-run per-call listing has been removed.

### Literal angle-bracket text vs real tags

- Protection now treats only real tag-like markup as immutable (for example `<i>`, `</color>`, `<tag=...>`).
- Prose enclosed in angle brackets that is not a real tag name (for example `< Update: Alien personnel on site. Sending extraction team >`) is no longer protected as markup and is sent through MT as normal text.

### Pipeline example (raw text flow)

```text
Original:
[b][c][ffffff]Location:[-][/c][/b] [c][00ff00]Delta System[-][/c]\n[b][c][ffffff]Difficulty:[-][/c][/b] ...

Masked:
__PH_0____PH_1____PH_2__Location:__PH_3____PH_4____PH_5__ __PH_6____PH_7__Delta System__PH_8____PH_9__\n__PH_10____PH_11____PH_12__Difficulty:...

Sent to MT (direct transport):
(TKPH0LTK) Location: (TKPH1LTK) Delta System (TKPH2LTK)
(TKPH3LTK) Difficulty: ...

Returned by MT (raw):
(TKPH0LTK) Standort: (TKPH1LTK) Deltasystem (TKPH2LTK)
(TKPH3LTK) Schwierigkeit: ...

Restored masked:
__PH_0____PH_1____PH_2__Standort:__PH_3____PH_4____PH_5__ __PH_6____PH_7__Deltasystem__PH_8____PH_9__\n__PH_10____PH_11____PH_12__Schwierigkeit:...

Final CSV text:
[b][c][ffffff]Standort:[-][/c][/b] [c][00ff00]Deltasystem[-][/c]\n[b][c][ffffff]Schwierigkeit:[-][/c][/b] ...
```

### Recent fixes reflected in code

- Direct transport switched from `__BPH__` payload transport to `TKPH` run transport.
- Parenthesized edge transport tokens are enabled by default via `mt.parenthesized_transport_token_edges = true` (toggleable in TOML or CLI override flags).
- Newline placeholders are preserved as real MT paragraph separators during transport.
- Placeholder bundling is restricted to spaces/tabs only (no cross-newline merge).
- Spaces around newline placeholders are stripped in final masked normalization.
- Post-restore tag spacing compaction keeps game markup canonical.
- MT reports render pipeline values as raw fenced code blocks (no JSON-escaped inline text).
- Export can normalize fused control tags in English (e.g. `@q0You` → `@q0 You`) via `mt.control_tag_spacing_enabled`.
- Export writes affected row IDs to `mt.control_tag_affected_ids_output` for selective re-translation (`translate-mt --ids-file ...`).
- Apply can persist normalized English into final CSV via `mt.persist_normalized_english_in_output_csv`.
- Alternative fused-output mode is configurable via `mt.control_tag_fused_output`.
- Direct transport coalescing now treats bracket/paren separators (`[]()`) as merge boundaries for adjacent placeholder clusters.
- A pre-restore expected-order retag pass now normalizes MT-swapped adjacent `TKPH` tokens back to source order to avoid false `token_reorder` failures.
- `source_masked` mode now uses canonical export mapping (no remask/reindex drift), preventing placeholder-ID mismatches between `translate-mt` and `apply`.
- `apply` now prefers translated-row `protected` mapping and selects the restoration candidate with the fewest leaked internal tokens.
- Step 5 QA now includes explicit leak-token detection (`__PH`, `__BPH`, `TKPH`) in final CSV output.

### Example trace: `eden_pda_aaWmSSe`

Observed issue pattern before fix:

- Export row had canonical placeholders ending at `__PH_4__`.
- MT result for this row contained `__PH_5__` after translation.
- Final CSV leaked `__PH_5__` because restore mapping did not contain that token.

What changed:

- `translate-mt` now reuses export `source_masked` + `protected` directly.
- The row is restored using the same mapping lineage used during transport.
- Step 5 leak scan confirms no internal tokens remain in final CSV.

## Original Plan Adherence (Audit)

Status against the original implementation plan:

1. Baseline + inventory (`de_empty`, `de_contains_english`, `de_ok`) — **met**
   - Implemented via `empyrion_localize.py audit` and `reports/audit_candidates.csv`.
2. Safe extractor + immutable token masking — **met**
   - Protected via `protect_patterns.txt` and masking sentinels `__PH_n__`.
3. Glossary + normalization assets — **met (initial)**
   - Implemented with `glossary_de.csv` and glossary enforcement during apply.
4. Translation orchestration preserving row/id mapping — **met**
   - JSONL `id`-based export/chunk/merge/apply pipeline.
5. Side-by-side completed outputs — **met**
   - `output-all-real/*.de.completed.csv`.
6. Automated QA gates — **partial**
   - Strong token/tag parity implemented; language fluency gate was missing in the first run.
7. Reviewer package — **met**
   - `applied_changes.csv`, release report, and optional high-risk sample export.
8. Pipeline documentation — **met**
   - This README and `TOOLS-README.md`.

## Verified Updated Implementation Plan (Current)

Quality recovery now follows a high-risk-first automated strategy:

1. Compute deterministic risk metadata during export:
   - `risk_score`, `risk_level`, `risk_flags`, `risk_version` per entry.
2. Mark rows as high-risk when source text likely breaks grammar under literal tag-fragment translation:
   - mixed markup/plain segments,
   - placeholder-adjacent text,
   - short dialogue utterances,
   - dialogue punctuation cues,
   - dense structure (`\\n` + tags).
3. Generate optional developer sample report (non-blocking):
   - `reports/high_risk_samples.csv`.
4. Route high-risk rows into dedicated chunk set (`highrisk_chunk_*`) with stricter translation prompt rules:
   - preserve placeholders exactly,
   - prefer idiomatic German for short dialogue acts,
   - maintain natural sentence grammar around markup boundaries.
5. Enforce MT-first quality gate for high/medium risk content:
   - export → `translate-mt` (optionally risk-filtered) → review/failures reports → apply → token QA.
   - Legacy manual chunk/prompt workflows remain available for exceptional cases, but are no longer the default production path.
6. Add command/control hard-lock safety:
   - critical literals (for example `give item Token 6995`) are masked before MT so they cannot drift.
7. Add report-time bracket watchlist:
   - `translate-mt` review markdown now reports remaining non-protected bracket labels to guide targeted manual checks.
8. Keep release flow unchanged in safety behavior:
   - build creates artifact,
   - push publishes only (no rebuild).

## Risk classification (v2)

`empyrion_localize.py` computes risk metadata during `export` via `compute_risk(...)` and writes:

- `risk_version`: `v2`
- `risk_score`: integer score from weighted rule flags
- `risk_level`: `low` / `medium` / `high`
- `risk_flags`: list of triggered rule names

Default thresholds:

- `low`: `risk_score < 3`
- `medium`: `3 <= risk_score < 6`
- `high`: `risk_score >= 6`

Current v2 flags (as emitted by code):

- `mixed_markup_plain`
- `placeholder_adjacent_text`
- `short_dialogue_utterance`
- `dialogue_cues`
- `structure_dense`
- `long_sentence_with_markup`
- `placeholder_cluster_dense`
- `punctuation_placeholder_boundary`
- `fragmented_micro_segments`
- `heavy_multiline_structure`
- `control_code_dense`
- `high_placeholder_density`

Interpretation:

- `low` rows are generally safe for bulk MT flow.
- `medium` rows should be reviewed when quality-sensitive.
- `high` rows are prioritized for manual LLM/Copilot review before merge/apply.

Built-in utilities:

- `risk-report`: writes per-score row distribution CSV and prints low/medium/high totals.
- `risk-sample`: selects random rows by risk selectors (levels/scores/min-max) and writes JSONL sample + CSV report.
- Typical sample-first validation flow:
   1) `export` with risk metadata,
   2) `risk-report` for distribution,
   3) `risk-sample --risk-levels medium high --size 10`,
   4) `translate-mt` on the sample with review/failure reports.

## Protected syntax (must not break)

Examples of protected patterns:

- Placeholders: `{PlayerName}`, `{TotalGamesWon}`
- XML-like tags: `<color=#fddc1e>...</color>`
- PDA/format tags: `[b]`, `[/b]`, `[c]`, `[-]`, `[00fbff]`
- URL/control bracket forms: `[/url]`, `[url=...]`, `[S-1]`, `[F-?]`, `[ IDA ]`
- Control codes: `@q0`, `@w2`, `@p9`, `@d3`
- Command literals: `give item Token 6995` (and similar command-value forms)
- Escaped newlines: `\n`

## Worked examples

### Example A: Dialogue with placeholder

**Original (`Dialogues.csv`, `KEY=dlgTCHolyStatue`)**

- English: `Oh {PlayerName}. I see you have the holy statue of our people. Do you want to give it to me?`
- Deutsch: *(empty)*

**Masked unit (`reports/translation_units.jsonl`)**

- `id`: `Dialogues.csv:2:dlgTCHolyStatue:04608ea5834c`
- `source_masked`: `Oh __PH_0__. I see you have the holy statue of our people. Do you want to give it to me?`
- `protected`: `{"__PH_0__": "{PlayerName}"}`

**Final (`output-all-real/Dialogues.de.completed.csv`)**

- Deutsch: `Oh {PlayerName}. Ich sehe, du hast die heilige Statue unseres Volkes. Willst du sie mir geben?`

---

### Example B: PDA formatting + color tags

**Original (`PDA.csv`, `KEY=pda_iG40h`)**

- English: `[b]Prologue:[/b] [b][c][00fbff]Journey into the unknown[-][/c][/b]`
- Deutsch: *(empty)*

**Final (`output-all-real/PDA.de.completed.csv`)**

- Deutsch: `[b]Prolog:[/b] [b][c][00fbff]Reise ins Unbekannte[-][/c][/b]`

All formatting tags remained intact.

---

### Example C: Existing good German kept

**Original (`Localization.csv`, `KEY=AlienBlocks`)**

- English: `Alien Hull Blocks`
- Deutsch: `Alien Baublöcke`

**Final (`output-all-real/Localization.de.completed.csv`)**

- Deutsch: `Alien Baublöcke`

No unnecessary overwrite.

## Important edge case handled

Some control codes are glued to English words in source, e.g. `@w2You`, `@q0I`.  
These were preserved exactly to satisfy strict token parity and avoid runtime/control parsing regressions.

## Result summary

From `output-all-real/applied_changes.csv`:

- Total changed rows: `24513`
- By file:
  - `Dialogues.csv`: `13055`
  - `Localization.csv`: `2869`
  - `PDA.csv`: `8589`
- By status:
  - `de_empty`: `24499`
  - `de_contains_english`: `14`

Final changed-row token QA passed with 0 issues.

## Notes for future updates

When upstream mod/game CSVs change:

1. Re-run audit/export/chunk
2. Translate new chunks
3. Merge/apply
4. Run token QA
5. Diff against previous `*.de.completed.csv`

This keeps German localization current while preserving Empyrion control syntax safely.

## Optional release artifact (zip + report)

To package the final CSV set into a release artifact without re-running translation:

```bash
cd game_stuff/empyrion
python3 release-empyrion-translation.py
```

This validates token parity and writes zip/report artifacts to `game_stuff/empyrion/dist/`.
Release now creates exactly two zip files:

1. `empyrion-de-translation-<date>.zip`
   - includes: `Dialogues.de.completed.csv`, `Localization.de.completed.csv`, `PDA.de.completed.csv`, `translation-report.md`, `translation-failures.md`
2. `empyrion-de-translation-traces-<date>.zip`
   - includes: `translation-failures.md`, `translation-success.md`

The push step publishes only these two zip files.
When executed through the release manager, credentials/metadata come from manager-injected environment variables sourced from root `release.toml`.

To also create/update a GitHub Release and upload the zip + report:

```bash
cd game_stuff/empyrion
python3 release-empyrion-translation.py --publish-github
```

Optional release flags:

- `--tag empyrion-de-translation-YYYYMMDD-HHMMSS`
- `--release-name "Empyrion DE Translation <stamp>"`
- `--draft`
- `--prerelease`

Opt-in through the repository release manager (not part of default project list):

```bash
python3 release-all.py --project empyrion-translation --build
```

To publish via release manager (no `gh`, uses GitHub REST API + token env):

```bash
python3 release-all.py --project empyrion-translation --build --push
```
