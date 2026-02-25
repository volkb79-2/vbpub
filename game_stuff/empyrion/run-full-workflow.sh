#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/../.." && pwd)"

CLEAN=0
ACCEPT_QA_FAILED=0
PROMOTE_EXISTING_FAILURES=0
PROMOTE_FAILURES_FROM="./reports/translations.mt.full.failures.jsonl"
PROMOTE_STEP5_FAILURES_TO_OK=""
MT_CONFIG_FILE="${EMPYRION_MT_CONFIG:-./mt.toml}"

usage() {
    cat <<'USAGE'
Usage: ./run-full-workflow.sh [--clean] [--accept-qa-failed] [--promote-existing-failures] [--promote-failures-from <jsonl>] [--promote-step5-failures-to-ok]

Runs the full Empyrion MT workflow from CSV input_data to release build+push.

Default behavior (resume mode):
- Re-runs audit/export from CSV.
- Runs translate-mt with --resume so existing translation output is reused.
- Re-applies, re-validates, then builds + pushes release.

Options:
  --clean   Remove previous generated artifacts and restart from CSV-only state.
  --accept-qa-failed  Pass --treat-remaining-failures-as-ok to translate-mt
                      to promote failed rows that still have translation_masked.
  --promote-existing-failures  Do not run MT again; promote rows from failures JSONL into output and continue.
                               Requires existing translated output + failures files from a prior run.
  --promote-failures-from <jsonl>
                      Failures JSONL used by --promote-existing-failures
                      (default: ./reports/translations.mt.full.failures.jsonl)
  --promote-step5-failures-to-ok
                      Keep Step 5 token QA failures in reports but continue workflow
                      (release build/push still runs).

  TOML defaults:
    - mt.toml [workflow].promote_step5_failures_to_ok can enable Step 5 promotion by default.
    - CLI flag --promote-step5-failures-to-ok overrides TOML.
    - You can point to a different config via EMPYRION_MT_CONFIG=/path/to/mt.toml.
  -h, --help
USAGE
}

read_toml_bool() {
    local toml_file="$1"
    local dotted_key="$2"
    python3 - "$toml_file" "$dotted_key" <<'PY'
import sys
import tomllib
from pathlib import Path

toml_path = Path(sys.argv[1])
dotted_key = sys.argv[2]

if not toml_path.exists():
    print("0")
    raise SystemExit(0)

with toml_path.open("rb") as handle:
    data = tomllib.load(handle)

cursor = data
for part in dotted_key.split("."):
    if not isinstance(cursor, dict) or part not in cursor:
        print("0")
        raise SystemExit(0)
    cursor = cursor[part]

if isinstance(cursor, bool):
    print("1" if cursor else "0")
elif isinstance(cursor, (int, float)):
    print("1" if cursor else "0")
elif isinstance(cursor, str):
    print("1" if cursor.strip().lower() in {"1", "true", "yes", "on"} else "0")
else:
    print("0")
PY
}

while [[ $# -gt 0 ]]; do
    case "$1" in
        --clean)
            CLEAN=1
            shift
            ;;
        --accept-qa-failed)
          ACCEPT_QA_FAILED=1
          shift
          ;;
        --promote-existing-failures)
          PROMOTE_EXISTING_FAILURES=1
          shift
          ;;
        --promote-failures-from)
          if [[ $# -lt 2 ]]; then
            echo "[ERROR] --promote-failures-from requires a path argument" >&2
            usage
            exit 1
          fi
          PROMOTE_FAILURES_FROM="$2"
          shift 2
          ;;
        --promote-step5-failures-to-ok)
          PROMOTE_STEP5_FAILURES_TO_OK=1
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

if [[ -z "${PROMOTE_STEP5_FAILURES_TO_OK}" ]]; then
  PROMOTE_STEP5_FAILURES_TO_OK="$(read_toml_bool "${MT_CONFIG_FILE}" "workflow.promote_step5_failures_to_ok")"
  if [[ "${PROMOTE_STEP5_FAILURES_TO_OK}" == "1" ]]; then
    echo "[INFO] Step 5 promotion enabled via TOML: ${MT_CONFIG_FILE} [workflow].promote_step5_failures_to_ok=true"
  fi
fi

cd "${SCRIPT_DIR}"

if [[ ${CLEAN} -eq 1 ]]; then
    echo "[INFO] --clean requested: removing generated artifacts"
    rm -rf ./chunks ./chunks_* ./chunks_full
    rm -f ./reports/*.jsonl ./reports/*.csv ./reports/*.txt ./reports/*.md
    rm -f ./output-all-real/*.csv ./output-all-real/applied_changes.csv
    rm -f ./dist/empyrion-de-translation-*.zip
fi

if [[ ${PROMOTE_EXISTING_FAILURES} -eq 1 && ${ACCEPT_QA_FAILED} -eq 1 ]]; then
  echo "[INFO] --promote-existing-failures enabled; --accept-qa-failed is ignored because MT stage is skipped"
fi

mkdir -p ./reports ./output-all-real

echo "[INFO] Step 1/7: audit"
python3 empyrion_localize.py audit --base-dir ./input_data --report-dir ./reports

echo "[INFO] Step 2/7: export"
python3 empyrion_localize.py export \
  --base-dir ./input_data \
  --output ./reports/translation_units.risk.v2.jsonl

echo "[INFO] Step 3/7: translate-mt (resume enabled)"
TRANSLATE_ARGS=(
  --input ./reports/translation_units.risk.v2.jsonl
  --output ./reports/translations.mt.full.jsonl
  --review-output ./reports/translations.mt.full.review.md
  --failures-output ./reports/translations.mt.full.failures.jsonl
  --target-lang DE
  --resume
)

if [[ ${ACCEPT_QA_FAILED} -eq 1 ]]; then
    echo "[INFO] translate-mt configured to accept QA-failed rows with fallback translation"
    TRANSLATE_ARGS+=(--treat-remaining-failures-as-ok)
fi

if [[ ${PROMOTE_EXISTING_FAILURES} -eq 1 ]]; then
    echo "[INFO] translate-mt configured for promotion-only mode from existing failures JSONL"
    TRANSLATE_ARGS+=(
      --promote-failures-from "${PROMOTE_FAILURES_FROM}"
      --promote-failures-only
    )
fi

python3 empyrion_localize.py translate-mt \
  "${TRANSLATE_ARGS[@]}"

echo "[INFO] Step 4/7: apply"
python3 empyrion_localize.py apply \
  --base-dir ./input_data \
  --export-file ./reports/translation_units.risk.v2.jsonl \
  --translated-file ./reports/translations.mt.full.jsonl \
  --out-dir ./output-all-real

echo "[INFO] Step 5/7: token QA"
STEP5_FAILURES_JSONL="./reports/translations.step5.failures.jsonl"
STEP5_FAILURES_MD="./reports/translations.step5.failures.md"

STEP5_ARGS=(
  --changes-csv ./output-all-real/applied_changes.csv
  --export-file ./reports/translation_units.risk.v2.jsonl
  --failures-output-jsonl "${STEP5_FAILURES_JSONL}"
  --failures-output-md "${STEP5_FAILURES_MD}"
)

if [[ "${PROMOTE_STEP5_FAILURES_TO_OK}" == "1" ]]; then
  echo "[INFO] Step 5 failures will be promoted to OK (report-only mode)"
  STEP5_ARGS+=(--promote-failures-as-ok)
fi

STEP5_EXIT=0
python3 qa_validate_tokens.py \
  "${STEP5_ARGS[@]}" \
  ./output-all-real/Dialogues.de.completed.csv \
  ./output-all-real/Localization.de.completed.csv \
  ./output-all-real/PDA.de.completed.csv || STEP5_EXIT=$?

echo "" >> ./reports/translation-failures.md
echo "## Step 5 Token Parity QA (Post-Apply)" >> ./reports/translation-failures.md
echo "" >> ./reports/translation-failures.md
echo "This section is generated after apply in workflow Step 5 and is separate from translate-mt placeholder QA." >> ./reports/translation-failures.md
echo "" >> ./reports/translation-failures.md
echo "- MT section above: transport/provider + placeholder-sequence QA inside translate-mt." >> ./reports/translation-failures.md
echo "- Step 5 section below: final CSV English-vs-Deutsch token parity QA after apply." >> ./reports/translation-failures.md
echo "" >> ./reports/translation-failures.md
cat "${STEP5_FAILURES_MD}" >> ./reports/translation-failures.md

if [[ ${STEP5_EXIT} -ne 0 ]]; then
  echo "[WARN] Step 5 token QA reported failures (exit=${STEP5_EXIT})."
  if [[ "${PROMOTE_STEP5_FAILURES_TO_OK}" != "1" ]]; then
    echo "[ERROR] Step 5 token QA failed. Use --promote-step5-failures-to-ok to continue with release while preserving report evidence."
    exit ${STEP5_EXIT}
  fi
fi

cd "${REPO_ROOT}"

if [[ "${PROMOTE_STEP5_FAILURES_TO_OK}" == "1" ]]; then
  export EMPYRION_PROMOTE_STEP5_FAILURES_TO_OK=1
else
  unset EMPYRION_PROMOTE_STEP5_FAILURES_TO_OK || true
fi

echo "[INFO] Step 6/7: release build"
python3 release-all.py --project empyrion-translation --build

echo "[INFO] Step 7/7: release push"
python3 release-all.py --project empyrion-translation --push

echo "[INFO] Full workflow complete"
