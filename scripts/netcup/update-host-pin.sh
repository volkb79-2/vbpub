#!/usr/bin/env bash
# Replace one Netcup SSH host-key pin only after its fingerprint is verified
# through the provider console or another authenticated out-of-band channel.
set -Eeuo pipefail
umask 077

usage() {
  echo "usage: update-host-pin.sh <v1001|r1002> --pin-file ABSOLUTE_PATH --fingerprint SHA256:..." >&2
  exit 2
}

[[ $# == 5 ]] || usage
host=$1
[[ $2 == --pin-file ]] || usage
pin_file=$3
[[ $4 == --fingerprint ]] || usage
expected_fingerprint=$5
[[ $pin_file == /* && -f $pin_file && ! -L $pin_file ]] || {
  echo "refusing: pin file must be an existing regular file and not a symlink" >&2
  exit 2
}
[[ $expected_fingerprint =~ ^SHA256:[A-Za-z0-9+/]+={0,2}$ ]] || {
  echo "refusing: expected fingerprint must be a SHA256 fingerprint from the authenticated provider path" >&2
  exit 2
}

case $host in
  v1001)
    fqdn=v1001.vxxu.de
    address=152.53.166.181
    expected_pin_name=ssh_known_hosts-v1001-20261105
    ;;
  r1002)
    fqdn=r1002.vxxu.de
    address=152.53.187.201
    expected_pin_name=ssh_known_hosts-after-reinstall-20261104
    ;;
  *) usage ;;
esac
[[ ${pin_file##*/} == "$expected_pin_name" ]] || {
  echo "refusing: unexpected $host pin filename; expected $expected_pin_name" >&2
  exit 2
}

work_dir=$(mktemp -d /tmp/vbpub-host-pin.XXXXXX)
updated_pin="$(dirname -- "$pin_file")/.${expected_pin_name}.new.XXXXXX"
updated_pin=$(mktemp "$updated_pin")
cleanup() {
  rm -rf -- "$work_dir"
  rm -f -- "$updated_pin" "$updated_pin.old"
}
trap cleanup EXIT

candidate="$work_dir/candidate.known_hosts"
if ! ssh-keyscan -T 10 -t ed25519 "$address" > "$candidate" 2>/dev/null; then
  echo "refusing: ssh-keyscan failed for $host ($address)" >&2
  exit 1
fi
[[ $(grep -c '[^[:space:]]' "$candidate") == 1 ]] || {
  echo "refusing: expected exactly one scanned ed25519 host key for $host" >&2
  exit 1
}
read -r scanned_host key_type key_blob _ < "$candidate"
[[ $scanned_host == "$address" && $key_type == ssh-ed25519 && -n $key_blob ]] || {
  echo "refusing: ssh-keyscan returned an unexpected host or key type" >&2
  exit 1
}
actual_fingerprint=$(ssh-keygen -lf "$candidate" -E sha256 | awk 'NR == 1 {print $2}')
[[ $actual_fingerprint == "$expected_fingerprint" ]] || {
  echo "refusing: $host SSH fingerprint mismatch (expected $expected_fingerprint, got ${actual_fingerprint:-unavailable})" >&2
  exit 1
}

cp -- "$pin_file" "$updated_pin"
ssh-keygen -R "$address" -f "$updated_pin" >/dev/null
ssh-keygen -R "$fqdn" -f "$updated_pin" >/dev/null
cat -- "$candidate" >> "$updated_pin"
chmod 0600 "$updated_pin"
mv -f -- "$updated_pin" "$pin_file"
trap - EXIT
rm -rf -- "$work_dir"
echo "Updated $host SSH host-key pin: $pin_file ($actual_fingerprint)"
