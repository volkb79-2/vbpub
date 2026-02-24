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
- `game_stuff/empyrion/release-empyrion-translation.py` always reads from fixed folder `game_stuff/empyrion/tools/output-all-real`.
- To avoid path confusion, keep the latest approved translation outputs synced into that folder before running release commands.


## Scope

We processed these source files in this folder:

- `Dialogues.csv` — NPC dialogues, interaction text, narrative snippets
- `Localization.csv` — UI labels, item/block names, system strings
- `PDA.csv` — mission/chapter text, logs, long-form story content

All use the same column model:

`KEY, English, Deutsch, ...other languages...`

Target behavior:

- Fill empty `Deutsch` cells
- Replace obvious English leftovers in `Deutsch`
- Preserve all control/formatting tokens and placeholders exactly

## What data is where

### Source data
- `Dialogues.csv`
- `Localization.csv`
- `PDA.csv`

### Tooling
- `tools/empyrion_localize.py` — audit/export/chunk/merge/apply pipeline
- `tools/qa_validate_tokens.py` — token parity validator
- `tools/protect_patterns.txt` — immutable token regex patterns
- `tools/glossary_de.csv` — glossary replacements (consistency)
- `tools/README.md` — command usage reference

### Intermediate artifacts
- `tools/reports/audit_summary.json` — counts by file/category
- `tools/reports/audit_candidates.csv` — candidate rows found
- `tools/reports/translation_units.jsonl` — masked translation units
- `tools/chunks_full/chunk_XXXX.jsonl` — chunked translation work items
- `tools/chunks_full/chunk_XXXX.translated.jsonl` — translated chunks
- `tools/reports/translations.all.jsonl` — merged translated payload

### Final output
- `tools/output-all-real/Dialogues.de.completed.csv`
- `tools/output-all-real/Localization.de.completed.csv`
- `tools/output-all-real/PDA.de.completed.csv`
- `tools/output-all-real/applied_changes.csv` (change log)

Path meaning:

- `tools/output/` and `tools/output-wave*-real/` are intermediate/test outputs from earlier partial runs.
- `tools/output-all-real/` is the canonical final deliverable set.

## Processing pipeline (how it worked)

1. **Audit**
   - Detect rows with empty German or obvious English in German.

2. **Extract & mask**
   - Export candidates to JSONL.
   - Protect immutable fragments with placeholders like `__PH_0__`.

3. **Translate in chunks**
   - Split into manageable chunk files (`chunk_0001.jsonl`, etc.).
   - Translate `source_masked` -> `translation_masked`.
   - Optional: `translate-mt --treat-remaining-failures-as-ok` can promote remaining QA-failed rows (when they still contain a usable masked translation) so final apply can produce full CSV coverage with no untranslated rows left.

4. **Merge**
   - Merge all translated chunk outputs into one JSONL.

5. **Apply**
   - Restore protected tokens into translated text.
   - Write side-by-side completed CSV files (`*.de.completed.csv`).

6. **Validate**
   - Run token parity QA on changed rows.
   - Verify placeholders/tags/control codes remained valid.

## MT transport (current production flow)

`translate-mt` now uses direct word-token transport for adjacent placeholder runs.

1. Mask source into placeholders (`__PH_n__`).
2. Keep newline placeholders as real paragraph newlines for MT payload shaping.
3. Convert adjacent placeholder runs directly to transport tokens (`TKBPHnTK`).
4. Send normalized payload to MT.
5. Restore `TKBPHnTK` back to original placeholder runs after MT.
6. Restore newline placeholders and enforce placeholder-token sequence QA.
7. Restore final game markup for CSV output.

### Pipeline example (raw text flow)

```text
Original:
[b][c][ffffff]Location:[-][/c][/b] [c][00ff00]Delta System[-][/c]\n[b][c][ffffff]Difficulty:[-][/c][/b] ...

Masked:
__PH_0____PH_1____PH_2__Location:__PH_3____PH_4____PH_5__ __PH_6____PH_7__Delta System__PH_8____PH_9__\n__PH_10____PH_11____PH_12__Difficulty:...

Sent to MT (direct TKBPH transport):
TKBPH0TK Location: TKBPH1TK Delta System TKBPH2TK
TKBPH3TK Difficulty: ...

Returned by MT (raw):
TKBPH0TK Standort: TKBPH1TK Deltasystem TKBPH2TK
TKBPH3TK Schwierigkeit: ...

Restored masked:
__PH_0____PH_1____PH_2__Standort:__PH_3____PH_4____PH_5__ __PH_6____PH_7__Deltasystem__PH_8____PH_9__\n__PH_10____PH_11____PH_12__Schwierigkeit:...

Final CSV text:
[b][c][ffffff]Standort:[-][/c][/b] [c][00ff00]Deltasystem[-][/c]\n[b][c][ffffff]Schwierigkeit:[-][/c][/b] ...
```

### Recent fixes reflected in code

- Direct transport switched from `__BPH__` payload transport to `TKBPH` run transport.
- Newline placeholders are preserved as real MT paragraph separators during transport.
- Placeholder bundling is restricted to spaces/tabs only (no cross-newline merge).
- Spaces around newline placeholders are stripped in final masked normalization.
- Post-restore tag spacing compaction keeps game markup canonical.
- MT reports render pipeline values as raw fenced code blocks (no JSON-escaped inline text).

## Original Plan Adherence (Audit)

Status against the original implementation plan:

1. Baseline + inventory (`de_empty`, `de_contains_english`, `de_ok`) — **met**
   - Implemented via `tools/empyrion_localize.py audit` and `tools/reports/audit_candidates.csv`.
2. Safe extractor + immutable token masking — **met**
   - Protected via `tools/protect_patterns.txt` and masking sentinels `__PH_n__`.
3. Glossary + normalization assets — **met (initial)**
   - Implemented with `tools/glossary_de.csv` and glossary enforcement during apply.
4. Translation orchestration preserving row/id mapping — **met**
   - JSONL `id`-based export/chunk/merge/apply pipeline.
5. Side-by-side completed outputs — **met**
   - `tools/output-all-real/*.de.completed.csv`.
6. Automated QA gates — **partial**
   - Strong token/tag parity implemented; language fluency gate was missing in the first run.
7. Reviewer package — **met**
   - `applied_changes.csv`, release report, and optional high-risk sample export.
8. Pipeline documentation — **met**
   - This README and `tools/README.md`.

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
   - `tools/reports/high_risk_samples.csv`.
4. Route high-risk rows into dedicated chunk set (`highrisk_chunk_*`) with stricter translation prompt rules:
   - preserve placeholders exactly,
   - prefer idiomatic German for short dialogue acts,
   - maintain natural sentence grammar around markup boundaries.
5. Enforce a human-in-the-loop quality gate for high/medium risk content:
   - export → chunk → **manual LLM/Copilot translation + review** → merge → apply → token QA.
   - The manual translation/review step happens between `chunk` and `merge` using generated prompt files and chunk JSONL inputs.
6. Add command/control hard-lock safety:
   - critical literals (for example `give item Token 6995`) are masked before MT so they cannot drift.
7. Add report-time bracket watchlist:
   - `translate-mt` review markdown now reports remaining non-protected bracket labels to guide targeted manual checks.
8. Keep release flow unchanged in safety behavior:
   - build creates artifact,
   - push publishes only (no rebuild).

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

**Masked unit (`tools/reports/translation_units.jsonl`)**

- `id`: `Dialogues.csv:2:dlgTCHolyStatue:04608ea5834c`
- `source_masked`: `Oh __PH_0__. I see you have the holy statue of our people. Do you want to give it to me?`
- `protected`: `{"__PH_0__": "{PlayerName}"}`

**Final (`tools/output-all-real/Dialogues.de.completed.csv`)**

- Deutsch: `Oh {PlayerName}. Ich sehe, du hast die heilige Statue unseres Volkes. Willst du sie mir geben?`

---

### Example B: PDA formatting + color tags

**Original (`PDA.csv`, `KEY=pda_iG40h`)**

- English: `[b]Prologue:[/b] [b][c][00fbff]Journey into the unknown[-][/c][/b]`
- Deutsch: *(empty)*

**Final (`tools/output-all-real/PDA.de.completed.csv`)**

- Deutsch: `[b]Prolog:[/b] [b][c][00fbff]Reise ins Unbekannte[-][/c][/b]`

All formatting tags remained intact.

---

### Example C: Existing good German kept

**Original (`Localization.csv`, `KEY=AlienBlocks`)**

- English: `Alien Hull Blocks`
- Deutsch: `Alien Baublöcke`

**Final (`tools/output-all-real/Localization.de.completed.csv`)**

- Deutsch: `Alien Baublöcke`

No unnecessary overwrite.

## Important edge case handled

Some control codes are glued to English words in source, e.g. `@w2You`, `@q0I`.  
These were preserved exactly to satisfy strict token parity and avoid runtime/control parsing regressions.

## Result summary

From `tools/output-all-real/applied_changes.csv`:

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
The created artifact now also bundles the latest MT failures markdown from `tools/reports/` so release diagnostics always include the current failure/no-error summary.
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
