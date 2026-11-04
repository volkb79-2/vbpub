#!/usr/bin/env bash
# Promote a built candidate to the shared `<image>:local` tag, keeping a rollback (TB-10).
#
#   promote-image.sh --image tester-unified|wheel-builder --candidate REF [--date YYYYMMDD[suffix]] [--record-dir DIR]
#
# * The candidate is resolved to its immutable image id first and promoted BY ID, so a tag moved while
#   this script runs cannot change what is promoted.
# * The image currently tagged `<image>:local` is kept as `<image>:prev-<date>`. An existing `prev-*`
#   tag is NEVER overwritten (a second promotion on the same day would otherwise destroy the only
#   rollback): the script refuses and asks for a different --date suffix.
# * The replaced and promoted image ids are written to <record-dir>/<image>-<date>.json (default
#   `.assay/promotions` under the repo root; exclusive create, never overwritten).
# Run once per image; nothing is removed by this script.
set -euo pipefail

repo_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
image=""
candidate=""
date_tag="$(date -u +%Y%m%d)"
record_dir="$repo_root/.assay/promotions"

usage() {
  echo "usage: $0 --image tester-unified|wheel-builder --candidate REF [--date YYYYMMDD[suffix]] [--record-dir DIR]" >&2
  exit 2
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --image) image=${2:?}; shift 2 ;;
    --candidate) candidate=${2:?}; shift 2 ;;
    --date) date_tag=${2:?}; shift 2 ;;
    --record-dir) record_dir=${2:?}; shift 2 ;;
    -h|--help) usage ;;
    *) usage ;;
  esac
done

case "$image" in
  tester-unified|wheel-builder) ;;
  *) echo "--image must be tester-unified or wheel-builder" >&2; usage ;;
esac
[[ -n "$candidate" ]] || usage
[[ "$date_tag" =~ ^[0-9]{8}[A-Za-z0-9.-]*$ ]] || {
  echo "--date must look like 20261104 or 20261104-b (digits first, then letters, digits, dot or hyphen)" >&2
  exit 2
}

candidate_id="$(docker image inspect --format '{{.Id}}' "$candidate" 2>/dev/null)" || {
  echo "candidate image $candidate does not exist" >&2
  exit 2
}
[[ "$candidate_id" =~ ^sha256:[0-9a-f]{64}$ ]] || {
  echo "unexpected identity '$candidate_id' for candidate $candidate" >&2
  exit 2
}

local_tag="$image:local"
prev_tag="$image:prev-$date_tag"
if docker image inspect "$prev_tag" >/dev/null 2>&1; then
  echo "$prev_tag already exists; refusing to overwrite a rollback tag (pass a different --date, e.g. ${date_tag}-b)" >&2
  exit 2
fi

replaced_id=""
if replaced_id="$(docker image inspect --format '{{.Id}}' "$local_tag" 2>/dev/null)"; then
  [[ "$replaced_id" =~ ^sha256:[0-9a-f]{64}$ ]] || {
    echo "unexpected identity '$replaced_id' for $local_tag" >&2
    exit 2
  }
else
  replaced_id=""
fi

mkdir -p "$record_dir"
record="$record_dir/$image-$date_tag.json"
( set -o noclobber
  printf '{"image": "%s", "date": "%s", "prev_tag": "%s", "replaced_id": "%s", "promoted_id": "%s"}\n' \
    "$image" "$date_tag" "$prev_tag" "$replaced_id" "$candidate_id" > "$record" ) || {
  echo "cannot write the promotion record $record (it must not exist already)" >&2
  exit 2
}

if [[ -n "$replaced_id" ]]; then
  docker tag "$replaced_id" "$prev_tag"
  echo "kept $replaced_id as $prev_tag" >&2
else
  echo "no $local_tag existed; there is nothing to keep as a rollback" >&2
fi
docker tag "$candidate_id" "$local_tag"
echo "promoted $candidate_id to $local_tag (record: $record)" >&2
