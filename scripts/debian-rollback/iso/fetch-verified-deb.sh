#!/usr/bin/env bash
# fetch-verified-deb.sh PACKAGE DESTDIR [SUITE]
#
# Download one amd64 package from deb.debian.org WITHOUT apt lists or root, and refuse it unless the chain verifies:
#   InRelease  --gpgv (Debian archive keyring)-->  Packages.xz sha256  -->  package .deb sha256.
# The .deb is only downloaded and checked here; the caller unpacks it with `dpkg-deb -x` into a scratch directory.
# Used by the ISO build for build tools that the devcontainer does not have (squashfs-tools).
set -euo pipefail

pkg=${1:?package}
dest=${2:?destination directory}
suite=${3:-trixie}
mirror=${DR_MIRROR:-https://deb.debian.org/debian}
# Keyrings that may have signed the suite (space separated; DR_KEYRINGS overrides). All of them must be readable.
keyrings=${DR_KEYRINGS:-"/usr/share/keyrings/debian-archive-keyring.gpg /usr/share/keyrings/debian-archive-$suite-automatic.gpg /usr/share/keyrings/debian-archive-$suite-stable.gpg"}
keyring_args=()
for k in $keyrings; do
  [ -r "$k" ] || { echo "fetch-verified-deb: keyring $k not readable" >&2; exit 1; }
  keyring_args+=(--keyring "$k")
done
command -v gpg >/dev/null || { echo "fetch-verified-deb: gpg missing" >&2; exit 1; }
mkdir -p "$dest"
work=$(mktemp -d "${TMPDIR:-/tmp}/fvd.XXXXXX")
trap 'rm -rf "$work"' EXIT

curl -fsS --max-time 60 -o "$work/InRelease" "$mirror/dists/$suite/InRelease"
# gpg with an empty home and only the Debian keyring; a valid signature needs a VALIDSIG status line, not just exit 0
mkdir -m 700 "$work/gnupg"
gpg --homedir "$work/gnupg" --no-default-keyring "${keyring_args[@]}" --status-fd 3 --output "$work/Release" \
  --decrypt "$work/InRelease" 3>"$work/status" 2>"$work/gpg.err" \
  || { cat "$work/gpg.err" >&2; echo "fetch-verified-deb: InRelease signature does not verify" >&2; exit 1; }
grep -q '^\[GNUPG:\] VALIDSIG ' "$work/status" \
  || { echo "fetch-verified-deb: no valid signature on InRelease" >&2; exit 1; }
[ -s "$work/Release" ] || { echo "fetch-verified-deb: empty Release" >&2; exit 1; }

idx=main/binary-amd64/Packages.xz
want=$(awk -v f="$idx" '/^SHA256:/ {s=1; next} /^[A-Za-z]/ {s=0} s && $3==f {print $1; exit}' "$work/Release")
[ -n "$want" ] || { echo "fetch-verified-deb: $idx not listed in the signed Release" >&2; exit 1; }
curl -fsS --max-time 120 -o "$work/Packages.xz" "$mirror/dists/$suite/$idx"
got=$(sha256sum "$work/Packages.xz" | cut -d' ' -f1)
[ "$got" = "$want" ] || { echo "fetch-verified-deb: Packages.xz hash mismatch" >&2; exit 1; }

xz -dc "$work/Packages.xz" >"$work/Packages"   # a file, not a pipe: awk's early exit would SIGPIPE xz under pipefail
awk -v p="$pkg" '
  BEGIN { RS=""; FS="\n" }
  { name=""; fn=""; sum=""
    for (i=1;i<=NF;i++) { if ($i ~ /^Package: /) name=substr($i,10); if ($i ~ /^Filename: /) fn=substr($i,11); if ($i ~ /^SHA256: /) sum=substr($i,9) }
    if (name==p) { print fn, sum; exit } }' "$work/Packages" >"$work/entry"
read -r filename sum <"$work/entry" || true
[ -n "${filename:-}" ] && [ -n "${sum:-}" ] || { echo "fetch-verified-deb: package $pkg not found in $suite main amd64" >&2; exit 1; }

out="$dest/$(basename "$filename")"
curl -fsS --max-time 300 -o "$out.part" "$mirror/$filename"
got=$(sha256sum "$out.part" | cut -d' ' -f1)
[ "$got" = "$sum" ] || { rm -f "$out.part"; echo "fetch-verified-deb: $pkg hash mismatch" >&2; exit 1; }
mv "$out.part" "$out"
echo "$out"
