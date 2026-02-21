# Empyrion German Localization Completion (Reforged Eden 2)

## Why this was done

In-game text was still partially English while playing in German (especially with Reforged Eden 2 content).  
The goal was to make German (`Deutsch`) consistently available across the main localization CSV files **without using DeepL**, while keeping all gameplay/UI control syntax intact.

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

4. **Merge**
   - Merge all translated chunk outputs into one JSONL.

5. **Apply**
   - Restore protected tokens into translated text.
   - Write side-by-side completed CSV files (`*.de.completed.csv`).

6. **Validate**
   - Run token parity QA on changed rows.
   - Verify placeholders/tags/control codes remained valid.

## Protected syntax (must not break)

Examples of protected patterns:

- Placeholders: `{PlayerName}`, `{TotalGamesWon}`
- XML-like tags: `<color=#fddc1e>...</color>`
- PDA/format tags: `[b]`, `[/b]`, `[c]`, `[-]`, `[00fbff]`
- Control codes: `@q0`, `@w2`, `@p9`, `@d3`
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
