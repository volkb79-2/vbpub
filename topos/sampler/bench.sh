#!/bin/sh
# Runs ONE measurement on a host. Reads /sys and /proc; writes only under $D and $COLD.
# usage: [PYARGS="--ring 1"] bench.sh DIR COLD_PARENT WHICH SECONDS      WHICH = rust | rust-nostore | python
set -eu
D=$1; CP=$2; W=$3; T=$4
NAME=$(basename "$D")-$W-$$
R=$D/$NAME; C=$CP/$NAME
mkdir -p "$R" "$C"
SOCK=$R/s.sock
psi() { awk '/^some/{split($5,a,"=");print a[2]}' /proc/pressure/cpu; }
L0=$(cut -d' ' -f1-3 /proc/loadavg); P0=$(psi)
case $W in
rust|rust-nostore)
  sed -e "s#^socket_path = .*#socket_path = \"$SOCK\"#" -e "s#^duration_s = .*#duration_s = $T#" \
      -e "s#^stats_path = .*#stats_path = \"$R/stats.json\"#" -e "s#^events_path = .*#events_path = \"$C/events.jsonl\"#" \
      -e "s#^tmpfs_dir = .*#tmpfs_dir = \"$R/tmpfs\"#" -e "s#^cold_dir = .*#cold_dir = \"$C\"#" \
      "$D/sampler.example.toml" > "$R/c.toml"
  [ "$W" = rust-nostore ] && sed -i 's#^enabled = true#enabled = false#' "$R/c.toml"
  "$D/topos-sampler" "$R/c.toml" ;;
python)
  python3 "$D/lean_python_sampler.py" --duration "$T" --stats "$R/stats.json" ${PYARGS:-} ;;
esac
L1=$(cut -d' ' -f1-3 /proc/loadavg); P1=$(psi)
echo "{\"run\":\"$W\",\"seconds\":$T,\"load_before\":\"$L0\",\"load_after\":\"$L1\",\"cpu_psi_some_pct\":$(awk "BEGIN{printf \"%.1f\", ($P1-$P0)/($T*1000000)*100}"),\"stats\":$(cat "$R/stats.json")}" | tee -a "$D/results.jsonl"
rm -rf "$R" "$C"
