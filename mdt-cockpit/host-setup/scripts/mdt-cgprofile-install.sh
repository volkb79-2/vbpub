#!/usr/bin/env bash
# mdt host-setup — install, pin and upgrade the native cgprofile service (MDT-055).
#
#   mdt-cgprofile-install.sh --config /etc/mdt/host-setup.env [--force-restart]
#   mdt-cgprofile-install.sh --config /etc/mdt/host-setup.env --print-config   # the cgpd.toml it would install
#
# Reads the CGPROFILE_* keys of host-setup.env (data only, never sourced) and installs the RELEASED
# cgprofile wheel into /opt/cgprofile/<version>/venv (pip --no-index, sha256 verified before anything is
# unpacked), renders /etc/cgprofile/cgpd.toml and the two units with the wheel's own `cgpd config check`
# / `cgpd unit print`, and restarts only on drift, only after the live sessions drained. The design is
# ../../../cgprofile/docs/NATIVE-DEPLOYMENT.md (sections 3, 5, 6); this file is the 2.x host side.
#
# Exit codes: 0 done or nothing to do, 2 refused or failed (nothing half-applied that the next run
# cannot repair), 3 the drain deadline passed with live sessions (old daemon and old `current` kept,
# pending version recorded in /opt/cgprofile/PENDING; --force-restart proceeds anyway).
#
# Test seams (the fake-root tests in tests/test_cgprofile_install.py; all default to the real thing):
#   MDT_CGP_ROOT           prefix for every path this script writes (default empty = the real root)
#   MDT_CGP_SYSTEMCTL      systemctl command            MDT_CGP_PYTHON    python3 used to build the venv
#   MDT_CGP_FETCH          command `FETCH URL DEST`     MDT_CGP_POLL_SECONDS (10)  MDT_CGP_READY_SECONDS (30)
set -euo pipefail

ROOT="${MDT_CGP_ROOT:-}"
SYSTEMCTL="${MDT_CGP_SYSTEMCTL:-systemctl}"
PYTHON="${MDT_CGP_PYTHON:-python3}"
FETCH="${MDT_CGP_FETCH:-}"
POLL_SECONDS="${MDT_CGP_POLL_SECONDS:-10}"
READY_SECONDS="${MDT_CGP_READY_SECONDS:-30}"

OPT_REAL=/opt/cgprofile
OPT="$ROOT$OPT_REAL"
ETC="$ROOT/etc/cgprofile"
UNIT_DIR="$ROOT/etc/systemd/system"
CLIENT_LINK="$ROOT/usr/local/bin/cgpctl"
DEFAULT_RELEASE_BASE="https://github.com/volkb79-2/vbpub/releases/download"
VERSION_RE='[0-9]+\.[0-9]+\.[0-9]+[A-Za-z0-9.+_-]*'

CONFIG_FILE=""
FORCE_RESTART=0
PRINT_CONFIG=0
LIVE_LINES=""
while [ "$#" -gt 0 ]; do
  case "$1" in
    --config) [ "$#" -ge 2 ] || { echo "ERROR: --config needs a file" >&2; exit 2; }; CONFIG_FILE="$2"; shift 2 ;;
    --force-restart) FORCE_RESTART=1; shift ;;
    --print-config) PRINT_CONFIG=1; shift ;;
    -h|--help) sed -n '2,16p' "$0"; exit 0 ;;
    *) echo "ERROR: unknown argument: $1" >&2; exit 2 ;;
  esac
done
[ -n "$CONFIG_FILE" ] && [ -r "$CONFIG_FILE" ] || { echo "ERROR: cgprofile install: --config FILE is required and must be readable" >&2; exit 2; }

die() { echo "ERROR: cgprofile: $*" >&2; exit 2; }
say() { echo "cgprofile: $*"; }

# The LAST assignment of KEY in the env file, one layer of matching quotes stripped; never sourced.
cfg() {
  local key="$1" default="${2-}" line value
  line="$(grep -E "^${key}=" "$CONFIG_FILE" | tail -n 1 || true)"
  if [ -z "$line" ]; then printf '%s' "$default"; return 0; fi
  value="${line#*=}"
  case "$value" in
    \"*\") value="${value#\"}"; value="${value%\"}" ;;
    \'*\') value="${value#\'}"; value="${value%\'}" ;;
  esac
  printf '%s' "$value"
}

# <version>@sha256:<hex> -> sets PIN_VERSION, PIN_SHA
parse_pin() {
  local key="$1" value="$2"
  [ -n "$value" ] || die "$key is empty while CGPROFILE_ENABLE=1; pin <version>@sha256:<hex> of the released wheel"
  [[ "$value" =~ ^(${VERSION_RE})@sha256:([0-9a-f]{64})$ ]] || die "$key=$value is not <version>@sha256:<64 lowercase hex>"
  PIN_VERSION="${BASH_REMATCH[1]}"; PIN_SHA="${BASH_REMATCH[2]}"
}

duration_seconds() {
  local value="$1"
  [[ "$value" =~ ^([0-9]+)([smh])$ ]] && [ "${BASH_REMATCH[1]}" -gt 0 ] || die "CGPROFILE_DRAIN_TIMEOUT=$value is not a positive number with s, m or h"
  case "${BASH_REMATCH[2]}" in
    s) echo "${BASH_REMATCH[1]}" ;;
    m) echo $((BASH_REMATCH[1] * 60)) ;;
    h) echo $((BASH_REMATCH[1] * 3600)) ;;
  esac
}

sha256_of() { sha256sum -- "$1" | cut -d' ' -f1; }

# fetch_wheel TAG-PREFIX FILE VERSION SHA DEST: copy or download, then verify BEFORE anything is unpacked.
fetch_wheel() {
  local project="$1" file="$2" version="$3" sha="$4" dest="$5" source base url
  source="$(cfg CGPROFILE_WHEEL_SOURCE "")"
  if [ -z "$source" ] || [[ "$source" == https://* ]]; then
    base="${source:-$DEFAULT_RELEASE_BASE}"
    url="${base%/}/${project}-v${version}/${file}"
    say "fetching $url"
    if [ -n "$FETCH" ]; then
      "$FETCH" "$url" "$dest" || die "download failed: $url"
    else
      curl --fail --silent --show-error --location --proto '=https' --tlsv1.2 --output "$dest" "$url" \
        || die "download failed: $url"
    fi
  else
    [ -f "$source/$file" ] || die "$source/$file not found (CGPROFILE_WHEEL_SOURCE is a directory and must hold $file)"
    cp -- "$source/$file" "$dest"
  fi
  [ "$(sha256_of "$dest")" = "$sha" ] || die "sha256 of $file does not match the pin ($sha); refusing, nothing was unpacked or changed"
}

supported_keys() {
  # The version-to-keys table: only keys the pinned cgpd knows are rendered, so a rollback never feeds an
  # older daemon a key it would refuse. Every release that adds a key adds a line here (and a test).
  local major="${1%%.*}"
  if [ "$major" = 2 ]; then echo "allow_uids gates_slice observe_slices"; else
    die "mdt host-setup installer supports cgprofile 2.x only (<3.0.0); 1.x is the ciu container deployment and 3.x uses cgprofiled install"
  fi
}

toml_list_of_strings() { local out="" item; for item in $1; do out="${out:+$out, }\"$item\""; done; printf '[%s]' "$out"; }
toml_list_of_ints() { local out="" item; for item in ${1//,/ }; do out="${out:+$out, }$item"; done; printf '[%s]' "$out"; }

render_config() {
  local version="$1" keys value
  keys=" $(supported_keys "$version") "
  echo "# /etc/cgprofile/cgpd.toml: rendered by mdt host-setup from /etc/mdt/host-setup.env (CGPROFILE_*)."
  echo "# Edit host-setup.env and re-run install.sh; runtime overrides live in /var/lib/cgprofile/overrides.toml."
  value="$(cfg CGPROFILE_GATES_SLICE dev-gates.slice)"
  if [[ "$keys" == *" gates_slice "* ]] && [ -n "$value" ]; then echo "gates_slice = \"$value\""; fi
  value="$(cfg CGPROFILE_OBSERVE_SLICES "")"
  if [[ "$keys" == *" observe_slices "* ]] && [ -n "${value// /}" ]; then echo "observe_slices = $(toml_list_of_strings "$value")"; fi
  value="$(cfg CGPROFILE_ALLOW_UIDS "")"
  if [[ "$keys" == *" allow_uids "* ]] && [ -n "${value//[ ,]/}" ]; then echo "allow_uids = $(toml_list_of_ints "$value")"; fi
  return 0
}

live_sessions_json() { "$OPT/current/venv/bin/cgpctl" sessions --state live --json; }

# Prints one line per live session ("id owner_uid age_seconds"); fails when the answer is not usable.
live_session_lines() {
  local json
  json="$(live_sessions_json)" || return 1
  printf '%s' "$json" | "$PYTHON" -c '
import json, sys
doc = json.load(sys.stdin)
for row in doc.get("sessions", []):
    if row.get("state", "live") == "live":
        print(row.get("session", "?"), row.get("owner_uid", "?"), int(row.get("elapsed_seconds") or 0))
'
}

drain_or_refuse() {
  local version="$1" deadline lines
  deadline=$((SECONDS + $(duration_seconds "$(cfg CGPROFILE_DRAIN_TIMEOUT 10m)")))
  if [ ! -x "$OPT/current/venv/bin/cgpctl" ]; then
    [ "$FORCE_RESTART" = 1 ] && { say "WARN: no cgpctl in $OPT/current to list live sessions; --force-restart given, restarting anyway"; return 0; }
    refuse "the running daemon's cgpctl is missing, so live sessions cannot be listed" "$version"
  fi
  say "waiting up to $(cfg CGPROFILE_DRAIN_TIMEOUT 10m) for live sessions to finish (polling every ${POLL_SECONDS}s; nothing is stopped)"
  while true; do
    if ! lines="$(live_session_lines)"; then
      [ "$FORCE_RESTART" = 1 ] && { say "WARN: could not list live sessions; --force-restart given, restarting anyway"; return 0; }
      refuse "the live sessions could not be listed (cgpctl sessions failed)" "$version"
    fi
    [ -z "$lines" ] && { say "no live sessions, proceeding"; return 0; }
    if [ "$SECONDS" -ge "$deadline" ]; then
      if [ "$FORCE_RESTART" = 1 ]; then
        say "WARN: --force-restart: aborting these live sessions (they finalize as daemon-stopped):"
        printf '%s\n' "$lines" | while read -r sid uid age; do echo "  session=$sid owner_uid=$uid age=${age}s"; done
        return 0
      fi
      LIVE_LINES="$lines"
      refuse "live sessions remain at the drain deadline" "$version"
    fi
    sleep "$POLL_SECONDS"
  done
}

refuse() {
  local why="$1" version="$2"
  echo "ERROR: cgprofile: not restarting: $why. The old daemon and the old 'current' stay in place." >&2
  if [ -n "$LIVE_LINES" ]; then
    while read -r sid uid age; do echo "  live session=$sid owner_uid=$uid age=${age}s" >&2; done <<< "$LIVE_LINES"
  fi
  printf '%s\n' "$version" > "$OPT/PENDING"
  echo "ERROR: cgprofile: version $version is installed and pending; re-run host-setup when the sessions are done, or use --force-cgprofile-restart (install.sh) / --force-restart (this script)." >&2
  exit 3
}

wait_ready() {
  local version="$1" until_s=$((SECONDS + READY_SECONDS)) json
  while true; do
    if json="$("$OPT/current/venv/bin/cgpctl" status --json 2>/dev/null)" \
      && printf '%s' "$json" | "$PYTHON" -c '
import json, sys
doc = json.load(sys.stdin)
sys.exit(0 if doc["daemon"]["version"] == sys.argv[1] and doc["config"].get("last_reload_error") is None else 1)
' "$version"; then
      say "daemon answers with version $version, config.last_reload_error null"
      return 0
    fi
    [ "$SECONDS" -ge "$until_s" ] && die "the daemon did not report version $version with a clean config within ${READY_SECONDS}s (cgpctl status --json); see journalctl -u cgpd"
    sleep 1
  done
}

install_atomic() { # SRC DEST MODE
  local tmp="$2.mdt-new"
  install -D -m "$3" -- "$1" "$tmp" && mv -f -- "$tmp" "$2"
}

prune_versions() {
  local current path name keep="" versions=""
  current="$(readlink "$OPT/current" 2>/dev/null || true)"
  for path in "$OPT"/*/; do
    name="$(basename "$path")"
    [[ "$name" =~ ^${VERSION_RE}$ ]] && [ ! -L "${path%/}" ] || continue
    versions="$versions$name"$'\n'
  done
  keep=" $current $(printf '%s' "$versions" | sort -V -r | head -n 2 | tr '\n' ' ') "
  while read -r name; do
    [ -n "$name" ] || continue
    case "$keep" in *" $name "*) continue ;; esac
    say "pruning old version directory $OPT/$name (the two newest and current are kept)"
    rm -rf -- "${OPT:?}/$name"
  done <<< "$versions"
}

# ---------------------------------------------------------------------------------------------------
if [ "$PRINT_CONFIG" = 1 ]; then # what check.sh compares /etc/cgprofile/cgpd.toml with; touches nothing
  parse_pin CGPROFILE_WHEEL "$(cfg CGPROFILE_WHEEL "")"
  render_config "$PIN_VERSION"
  exit 0
fi
ENABLE="$(cfg CGPROFILE_ENABLE 0)"
case "$ENABLE" in
  0)
    if [ -L "$OPT/current" ]; then say "CGPROFILE_ENABLE=0: existing install ($(readlink "$OPT/current")) left alone; mdt-host-check.sh reports it"
    else say "CGPROFILE_ENABLE=0: the service is not managed by host-setup"; fi
    exit 0 ;;
  1) ;;
  *) die "CGPROFILE_ENABLE=$ENABLE is not 0 or 1" ;;
esac

parse_pin CGPROFILE_WHEEL "$(cfg CGPROFILE_WHEEL "")"; VERSION="$PIN_VERSION"; WHEEL_SHA="$PIN_SHA"
parse_pin CGPROFILE_CLI_EXTENDED_WHEEL "$(cfg CGPROFILE_CLI_EXTENDED_WHEEL "")"; CLIX_VERSION="$PIN_VERSION"; CLIX_SHA="$PIN_SHA"
supported_keys "$VERSION" >/dev/null
duration_seconds "$(cfg CGPROFILE_DRAIN_TIMEOUT 10m)" >/dev/null
for tool in install sha256sum "$PYTHON"; do command -v "$tool" >/dev/null 2>&1 || die "required command is unavailable: $tool"; done

mkdir -p "$OPT"
STAGE="$(mktemp -d "$OPT/.stage.XXXXXX")"
trap 'rm -rf -- "$STAGE"' EXIT
VERSION_DIR="$OPT/$VERSION"
VENV="$VERSION_DIR/venv"

# 1. The version directory: immutable once MANIFEST exists; a partial one (no MANIFEST) is rebuilt.
if [ -f "$VERSION_DIR/MANIFEST" ]; then
  grep -q "^cgprofile_sha256=$WHEEL_SHA$" "$VERSION_DIR/MANIFEST" \
    || die "$VERSION_DIR is already installed from a different wheel (sha256 differs from the pin); a version directory is immutable, release a new version instead of re-pinning this one"
  say "version $VERSION already installed ($VERSION_DIR), digest matches the pin"
else
  if [ -e "$VERSION_DIR" ]; then say "removing the incomplete $VERSION_DIR (no MANIFEST)"; rm -rf -- "${VERSION_DIR:?}"; fi
  WHEEL_FILE="cgprofile-${VERSION}-py3-none-any.whl"
  CLIX_FILE="cli_extended-${CLIX_VERSION}-py3-none-any.whl"
  mkdir -p "$STAGE/wheels"
  fetch_wheel cgprofile "$WHEEL_FILE" "$VERSION" "$WHEEL_SHA" "$STAGE/wheels/$WHEEL_FILE"
  fetch_wheel cli-extended "$CLIX_FILE" "$CLIX_VERSION" "$CLIX_SHA" "$STAGE/wheels/$CLIX_FILE"
  mkdir -p "$VERSION_DIR"
  "$PYTHON" -m venv "$VENV" || { rm -rf -- "${VERSION_DIR:?}"; die "could not create the venv $VENV"; }
  "$VENV/bin/pip" install --quiet --no-index --find-links "$STAGE/wheels" "cgprofile==$VERSION" \
    || { rm -rf -- "${VERSION_DIR:?}"; die "pip --no-index install of cgprofile==$VERSION failed (the wheel or its cli-extended pin is wrong)"; }
  got="$("$VENV/bin/cgpd" --version 2>&1 || true)"
  case "$got" in
    *"$VERSION"*) ;;
    *) rm -rf -- "${VERSION_DIR:?}"; die "the installed cgpd reports '$got', expected $VERSION" ;;
  esac
  {
    echo "cgprofile_wheel=$WHEEL_FILE"
    echo "cgprofile_sha256=$WHEEL_SHA"
    echo "cli_extended_wheel=$CLIX_FILE"
    echo "cli_extended_sha256=$CLIX_SHA"
    echo "installed_at=$(date -u +%Y-%m-%dT%H:%M:%SZ)"
    echo "installer=mdt host-setup (MDT-055)"
  } > "$VERSION_DIR/MANIFEST"
  say "installed $VERSION into $VERSION_DIR"
fi
[ -x "$VENV/bin/cgpd" ] || die "$VENV/bin/cgpd is missing"

# 2. Render and verify the config and the units with the NEW venv before anything live is touched.
render_config "$VERSION" > "$STAGE/cgpd.toml"
"$VENV/bin/cgpd" config check --file "$STAGE/cgpd.toml" >/dev/null || die "cgpd config check refused the rendered config (see above); nothing was changed"
"$VENV/bin/cgpd" unit print --unit cgprofile.slice --config "$STAGE/cgpd.toml" > "$STAGE/cgprofile.slice" || die "cgpd unit print failed for cgprofile.slice"
"$VENV/bin/cgpd" unit print --unit cgpd.service --config "$STAGE/cgpd.toml" > "$STAGE/cgpd.service" || die "cgpd unit print failed for cgpd.service"

# 3. Drift: wanted version, config, units.
CURRENT="$(readlink "$OPT/current" 2>/dev/null || true)"
version_drift=0; config_drift=0; service_unit_drift=0; slice_unit_drift=0
[ "$CURRENT" = "$VERSION" ] || version_drift=1
cmp -s "$STAGE/cgpd.toml" "$ETC/cgpd.toml" || config_drift=1
cmp -s "$STAGE/cgpd.service" "$UNIT_DIR/cgpd.service" || service_unit_drift=1
cmp -s "$STAGE/cgprofile.slice" "$UNIT_DIR/cgprofile.slice" || slice_unit_drift=1
ACTIVE=0; "$SYSTEMCTL" is-active --quiet cgpd.service 2>/dev/null && ACTIVE=1
mkdir -p "$(dirname "$CLIENT_LINK")"
ln -sfn "$OPT_REAL/current/venv/bin/cgpctl" "$CLIENT_LINK"

if [ "$version_drift$config_drift$service_unit_drift$slice_unit_drift" = 0000 ] && [ "$ACTIVE" = 1 ]; then
  say "no drift (version $VERSION, config, units); not restarted"
  rm -f -- "$OPT/PENDING"
  prune_versions
  exit 0
fi

restart_needed=0
if [ "$version_drift" = 1 ] || [ "$service_unit_drift" = 1 ] || [ "$ACTIVE" = 0 ]; then restart_needed=1; fi

# 4. Drain, only when a running daemon is about to be restarted. Nothing live was changed so far.
if [ "$restart_needed" = 1 ] && [ "$ACTIVE" = 1 ]; then
  say "drift: version=$version_drift service-unit=$service_unit_drift config=$config_drift; a restart is needed"
  drain_or_refuse "$VERSION"
fi

# 5. Apply: config, units, the pointer, then enable/start/restart/reload.
install_atomic "$STAGE/cgpd.toml" "$ETC/cgpd.toml" 0644
unit_changed=0
if [ "$service_unit_drift" = 1 ]; then install_atomic "$STAGE/cgpd.service" "$UNIT_DIR/cgpd.service" 0644; unit_changed=1; fi
if [ "$slice_unit_drift" = 1 ]; then install_atomic "$STAGE/cgprofile.slice" "$UNIT_DIR/cgprofile.slice" 0644; unit_changed=1; fi
if [ "$version_drift" = 1 ]; then
  ln -sfn "$VERSION" "$OPT/current.new"
  mv -T -f -- "$OPT/current.new" "$OPT/current"
  say "current -> $VERSION"
fi
rm -f -- "$OPT/PENDING"
[ "$unit_changed" = 0 ] || "$SYSTEMCTL" daemon-reload

if [ "$ACTIVE" = 0 ]; then
  "$SYSTEMCTL" enable cgprofile.slice cgpd.service
  "$SYSTEMCTL" start cgprofile.slice
  "$SYSTEMCTL" start cgpd.service
elif [ "$restart_needed" = 1 ]; then
  "$SYSTEMCTL" enable cgprofile.slice cgpd.service
  "$SYSTEMCTL" restart cgpd.service
elif [ "$config_drift" = 1 ]; then
  say "config-only change: systemctl reload cgpd (no restart, overrides kept)"
  "$SYSTEMCTL" reload cgpd.service
fi

wait_ready "$VERSION"
prune_versions
say "done: version $VERSION"
