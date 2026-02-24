#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/../.." && pwd)"

CLEAN=0

usage() {
    cat <<'USAGE'
Usage: ./run-full-workflow.sh [--clean]

Runs the full Empyrion MT workflow from CSV input_data to release build+push.

Default behavior (resume mode):
- Re-runs audit/export from CSV.
- Runs translate-mt with --resume so existing translation output is reused.
- Re-applies, re-validates, then builds + pushes release.

Options:
  --clean   Remove previous generated artifacts and restart from CSV-only state.
  -h, --help
USAGE
}

while [[ $# -gt 0 ]]; do
    case "$1" in
        --clean)
            CLEAN=1
            shift
            ;;
        -h|--help)
            usage
            exit 0
            ;;
        *)
            echo "[ERROR] Unknown option: $1" >&2
            usage
            exit 1
            ;;
    esac
done

cd "${SCRIPT_DIR}"

if [[ ${CLEAN} -eq 1 ]]; then
    echo "[INFO] --clean requested: removing generated artifacts"
    rm -rf ./chunks ./chunks_* ./chunks_full
    rm -f ./reports/*.jsonl ./reports/*.csv ./reports/*.txt ./reports/*.md
    rm -f ./output-all-real/*.csv ./output-all-real/applied_changes.csv
    rm -f ./dist/empyrion-de-translation-*.zip
fi

mkdir -p ./reports ./output-all-real

echo "[INFO] Step 1/7: audit"
python3 empyrion_localize.py audit --base-dir ./input_data --report-dir ./reports

echo "[INFO] Step 2/7: export"
python3 empyrion_localize.py export \
  --base-dir ./input_data \
  --output ./reports/translation_units.risk.v2.jsonl

echo "[INFO] Step 3/7: translate-mt (resume enabled)"
python3 empyrion_localize.py translate-mt \
  --input ./reports/translation_units.risk.v2.jsonl \
  --output ./reports/translations.mt.full.jsonl \
  --review-output ./reports/translations.mt.full.review.md \
  --failures-output ./reports/translations.mt.full.failures.jsonl \
  --target-lang DE \
  --resume

echo "[INFO] Step 4/7: apply"
python3 empyrion_localize.py apply \
  --base-dir ./input_data \
  --export-file ./reports/translation_units.risk.v2.jsonl \
  --translated-file ./reports/translations.mt.full.jsonl \
  --out-dir ./output-all-real

echo "[INFO] Step 5/7: token QA"
python3 qa_validate_tokens.py \
  --changes-csv ./output-all-real/applied_changes.csv \
  ./output-all-real/Dialogues.de.completed.csv \
  ./output-all-real/Localization.de.completed.csv \
  ./output-all-real/PDA.de.completed.csv

cd "${REPO_ROOT}"

echo "[INFO] Step 6/7: release build"
python3 release-all.py --project empyrion-translation --build

echo "[INFO] Step 7/7: release push"
python3 release-all.py --project empyrion-translation --push

echo "[INFO] Full workflow complete"
