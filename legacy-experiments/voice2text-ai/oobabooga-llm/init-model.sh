#!/usr/bin/env bash
set -euo pipefail

Y="\033[1;33m"; G="\033[1;32m"; R="\033[1;31m"; C="\033[0m"
log() { echo -e "${Y}[INFO]${C} $*"; }
ok()  { echo -e "${G}[OK]${C}   $*"; }
err() { echo -e "${R}[ERR]${C}  $*"; }

if [[ ! -f .env ]]; then
  err ".env not found. Copy .env.example to .env and edit first."; exit 1
fi

source .env

# Resolve directory (single canonical variable)
MODELS_DIR=${LLM_HOSTDIR_MODELS:-./vol-llm-models}
mkdir -p "${MODELS_DIR}" || true

# Also create cache directory
CACHE_DIR=${LLM_HOSTDIR_CACHE:-./vol-llm-cache}
mkdir -p "${CACHE_DIR}" || true

PRESET=${MODEL_PRESET:-custom}
CONVERSION=${MODEL_CONVERSION:-skip}

# If preset specified, map to repo/file if user hasn't overridden explicitly
if [[ "${PRESET}" != "custom" ]]; then
  case "${PRESET}" in
    tinyllama)
      MODEL_HF_REPO=${MODEL_HF_REPO:-TheBloke/TinyLlama-1.1B-Chat-v1.0-GGUF}
      MODEL_HF_FILE=${MODEL_HF_FILE:-tinyllama-1.1b-chat-v1.0.Q4_K_M.gguf}
      ;;
    phi2)
      # Note: phi-2 original repo is not GGUF; instruct user about conversion.
      MODEL_HF_REPO=${MODEL_HF_REPO:-microsoft/phi-2}
      MODEL_HF_FILE=${MODEL_HF_FILE:-pytorch_model.bin}
      ;;
    mistral7b)
      MODEL_HF_REPO=${MODEL_HF_REPO:-TheBloke/Mistral-7B-Instruct-v0.2-GGUF}
      MODEL_HF_FILE=${MODEL_HF_FILE:-mistral-7b-instruct-v0.2.Q4_K_M.gguf}
      ;;
    *)
      echo "Unknown preset '${PRESET}', continuing with custom values." ;;
  esac
fi

# Allow legacy variable names / final variable mapping
HF_REPO="${MODEL_HF_REPO:-}" 
HF_FILE="${MODEL_HF_FILE:-}" 
STRAT="${MODEL_DOWNLOAD_STRATEGY:-direct}"

if [[ -z "${HF_REPO}" || -z "${HF_FILE}" ]]; then
  err "MODEL_HF_REPO or MODEL_HF_FILE not set in .env"; exit 1
fi

TARGET_PATH="${MODELS_DIR}/${HF_FILE}"
if [[ -f "${TARGET_PATH}" ]]; then
  ok "Model already present: ${TARGET_PATH}"; exit 0
fi

echo ""; log "Selected (preset=${PRESET}) model: ${HF_REPO} / ${HF_FILE}"; echo ""

# Warn if conversion likely required
if [[ "${HF_FILE}" != *.gguf && "${CONVERSION}" != "skip" ]]; then
  log "Non-GGUF file detected (${HF_FILE}). Conversion step (stub) engaged."
fi

RAW_URL="https://huggingface.co/${HF_REPO}/resolve/main/${HF_FILE}?download=true"

log "Downloading (strategy=${STRAT}) -> ${TARGET_PATH}" 

case "${STRAT}" in
  direct)
    curl -L --fail --progress-bar -o "${TARGET_PATH}.part" "${RAW_URL}" || { err "Download failed"; rm -f "${TARGET_PATH}.part"; exit 1; } ;;
  hf-cli)
    if ! command -v huggingface-cli >/dev/null 2>&1; then
      log "Installing huggingface_hub (pip) for CLI..."; pip install --user huggingface_hub >/dev/null
    fi
    huggingface-cli download "${HF_REPO}" "${HF_FILE}" --local-dir "${MODELS_DIR}" --local-dir-use-symlinks False || { err "huggingface-cli download failed"; exit 1; } 
    mv "${MODELS_DIR}/${HF_FILE}" "${TARGET_PATH}.part" 2>/dev/null || true
    ;;
  manual)
    err "Manual strategy selected. Please place the file at: ${TARGET_PATH}"; exit 1 ;;
  *)
    err "Unknown strategy: ${STRAT}"; exit 1 ;;
esac

mv "${TARGET_PATH}.part" "${TARGET_PATH}" 2>/dev/null || true
if [[ -f "${TARGET_PATH}" ]]; then
  ok "Model downloaded: ${TARGET_PATH}"
else
  err "Model file missing after download step"; exit 1
fi

# Stub conversion logic (guide only)
if [[ "${CONVERSION}" != "skip" && "${HF_FILE}" != *.gguf ]]; then
  echo -e "\n${Y}[INFO] Conversion requested but not implemented automatically.${C}" >&2
  echo "Manual steps (example):" >&2
  echo "  1. Clone llama.cpp repo" >&2
  echo "  2. Run: python convert-hf-to-gguf.py ${HF_REPO} --outfile ${HF_FILE%.bin}.Q4_K_M.gguf --quant Q4_K_M" >&2
  echo "  3. Re-run init-model.sh pointing MODEL_HF_FILE at resulting .gguf" >&2
fi

log "Calculating SHA256..."
SHA256=$(sha256sum "${TARGET_PATH}" | awk '{print $1}')
echo "${SHA256}  ${HF_FILE}" > "${TARGET_PATH}.sha256"
ok "Checksum saved: ${TARGET_PATH}.sha256"

cat <<EOF

Next:
  1. Ensure docker-compose model mount points reference ${MODELS_DIR}
  2. Start services: docker compose up -d --build
  3. Test: ./test-llm.sh

To change model:
  - Edit .env (MODEL_HF_REPO / MODEL_HF_FILE)
  - Remove old file; re-run ./init-model.sh

EOF