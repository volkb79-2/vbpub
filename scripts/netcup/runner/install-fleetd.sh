#!/usr/bin/env bash
# Description: install hash-verified fleetd and cli-extended wheels into a host venv without network access.
set -Eeuo pipefail
here=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)
# shellcheck source=lib.sh
source "$here/lib.sh"
identity_check

usage() {
  cat >&2 <<'EOF'
usage: install-fleetd.sh --wheel-dir DIR --fleetd-version VER --fleetd-sha256 SHA256 \
  --cli-extended-version VER --cli-extended-sha256 SHA256 [--check] [--root DIR]
EOF
}

wheel_dir=
fleetd_version=
fleetd_sha256=
cli_extended_version=
cli_extended_sha256=
root_dir=/
check_only=0
while (($#)); do
  case $1 in
    --wheel-dir) (($# >= 2)) || { usage; exit 2; }; wheel_dir=$2; shift 2 ;;
    --fleetd-version) (($# >= 2)) || { usage; exit 2; }; fleetd_version=$2; shift 2 ;;
    --fleetd-sha256) (($# >= 2)) || { usage; exit 2; }; fleetd_sha256=$2; shift 2 ;;
    --cli-extended-version) (($# >= 2)) || { usage; exit 2; }; cli_extended_version=$2; shift 2 ;;
    --cli-extended-sha256) (($# >= 2)) || { usage; exit 2; }; cli_extended_sha256=$2; shift 2 ;;
    --root) (($# >= 2)) || { usage; exit 2; }; root_dir=$2; shift 2 ;;
    --check) check_only=1; shift ;;
    -h|--help) usage; exit 0 ;;
    *) echo "unknown argument: $1" >&2; usage; exit 2 ;;
  esac
done

if [[ -z $wheel_dir || -z $fleetd_version || -z $fleetd_sha256 || -z $cli_extended_version || -z $cli_extended_sha256 ]]; then
  usage
  exit 2
fi
[[ $fleetd_version =~ ^[0-9]+(\.[0-9]+)+$ && $cli_extended_version =~ ^[0-9]+(\.[0-9]+)+$ ]] || {
  echo 'wheel versions must be dotted numeric versions' >&2
  exit 2
}
[[ $fleetd_sha256 =~ ^[0-9A-Fa-f]{64}$ && $cli_extended_sha256 =~ ^[0-9A-Fa-f]{64}$ ]] || {
  echo 'wheel SHA-256 values must contain 64 hexadecimal digits' >&2
  exit 2
}
fleetd_sha256=${fleetd_sha256,,}
cli_extended_sha256=${cli_extended_sha256,,}
[[ -d $wheel_dir ]] || { echo "wheel directory does not exist: $wheel_dir" >&2; exit 3; }
wheel_dir=$(cd -- "$wheel_dir" && pwd -P)
fleetd_wheel="$wheel_dir/fleetd-$fleetd_version-py3-none-any.whl"
cli_extended_wheel="$wheel_dir/cli_extended-$cli_extended_version-py3-none-any.whl"
for wheel in "$cli_extended_wheel" "$fleetd_wheel"; do
  [[ -f $wheel ]] || { echo "required wheel is missing: $wheel" >&2; exit 3; }
done

verify_sha256() {
  local wheel_path=$1 expected_sha256=$2 actual_sha256
  actual_sha256=$(sha256sum -- "$wheel_path")
  actual_sha256=${actual_sha256%% *}
  [[ $actual_sha256 == "$expected_sha256" ]] || {
    echo "SHA-256 mismatch for $wheel_path: expected $expected_sha256, got $actual_sha256" >&2
    exit 4
  }
}
verify_sha256 "$cli_extended_wheel" "$cli_extended_sha256"
verify_sha256 "$fleetd_wheel" "$fleetd_sha256"

root_dir=${root_dir%/}
[[ -n $root_dir ]] || root_dir=/
[[ $root_dir == /* ]] || { echo '--root must be an absolute path' >&2; exit 2; }
rooted() {
  if [[ $root_dir == / ]]; then printf '/%s' "$1"; else printf '%s/%s' "$root_dir" "$1"; fi
}
if (( !check_only )) && [[ $root_dir == / ]] && [[ $(id -u) != 0 ]]; then
  echo 'installation to /opt and /usr/local/bin must run as root' >&2
  exit 6
fi

fleetd_base=$(rooted opt/fleetd)
version_dir=$(rooted "opt/fleetd/$fleetd_version")
venv="$version_dir/venv"
executable="$venv/bin/fleet"
marker="$venv/.wheel-sha256"
link=$(rooted usr/local/bin/fleet)
marker_value="$fleetd_sha256 $cli_extended_sha256"

for path in "$fleetd_base" "$version_dir" "$venv"; do
  [[ ! -L $path ]] || { echo "refusing symlinked install path: $path" >&2; exit 5; }
done
[[ ! -e $link || -L $link ]] || { echo "refusing to replace non-symlink command: $link" >&2; exit 5; }
if [[ -e $venv && ! -x $venv/bin/python ]]; then
  echo "existing venv is incomplete: $venv" >&2
  exit 5
fi

if [[ -e $marker && ! -f $marker ]]; then
  # "Cannot read the install record" is not "nothing is installed"; only a
  # genuinely absent record may be reported as not current.
  echo "cannot determine the installed state: install record is not a regular file: $marker" >&2
  exit 8
fi
already_installed=0
if [[ -x $executable && -f $marker && $(<"$marker") == "$marker_value" ]]; then
  already_installed=1
fi

if ((check_only)); then
  if ((already_installed)); then
    printf 'fleetd %s is installed at %s (sha256 %s)\n' "$fleetd_version" "$executable" "$fleetd_sha256"
    exit 0
  fi
  printf 'fleetd %s is NOT installed at %s\n' "$fleetd_version" "$executable"
  exit 1
fi

if ((!already_installed)); then
  mkdir -p -- "$fleetd_base"
  if [[ ! -e $venv ]]; then
    python3 -m venv "$venv"
  fi
  [[ -x $venv/bin/python ]] || { echo "venv Python is missing: $venv/bin/python" >&2; exit 7; }
  "$venv/bin/python" -m pip install --no-index --no-deps --force-reinstall "$cli_extended_wheel" "$fleetd_wheel"
  [[ -x $executable ]] || { echo "fleetd command was not installed: $executable" >&2; exit 7; }
  marker_tmp="$marker.$$"
  printf '%s\n' "$marker_value" > "$marker_tmp"
  mv -f -- "$marker_tmp" "$marker"
fi

mkdir -p -- "$(dirname -- "$link")"
if [[ ! -L $link || $(readlink -- "$link") != "$executable" ]]; then
  link_tmp="$link.$$"
  ln -s -- "$executable" "$link_tmp"
  mv -Tf -- "$link_tmp" "$link"
fi
printf 'installed fleetd %s at %s\n' "$fleetd_version" "$executable"
