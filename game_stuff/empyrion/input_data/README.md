Copy source CSV/ECF files from Empyrion Age / Reforged Eden 2 into this folder.

Preferred structure uses snapshot subfolders named `YYYYMMDD-bNN` (for example `20260225-b41`).

Local example path for reforged eden 2 data: `G:\SteamLibrary\steamapps\workshop\content\383120\3143225812\`

- `Content/Configuration/Dialogues.csv`
- `Extras/Localization.csv`
- `Extras/PDA/PDA.csv`
- `Content/Configuration/ItemsConfig.ecf` (needed for `keep_item_names_english_in_german = true`)

`empyrion_localize.py` resolves `--base-dir` like this:

1. Use `--base-dir` directly if it already contains all required CSV files.
2. Otherwise auto-select the newest `YYYYMMDD-bNN` subfolder containing all required CSV files.

Run tooling from `game_stuff/empyrion` (for example `python3 empyrion_localize.py ...`).
Generated reports are written to `reports/`, and final completed CSVs to `output-all-real/`.
