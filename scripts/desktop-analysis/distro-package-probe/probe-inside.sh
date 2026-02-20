#!/usr/bin/env bash
set -euo pipefail

TARGET_DISTRO="${1:-${TARGET_DISTRO:-unknown}}"

TOOL_KEYS=(
  glmark2
  lshw
  xlsclients
  libinput
  kscreen_doctor
  qdbus
  xrandr
)

pkg_manager="unknown"
if command -v dnf >/dev/null 2>&1; then
  pkg_manager="dnf"
elif command -v apt-cache >/dev/null 2>&1; then
  pkg_manager="apt"
elif command -v pacman >/dev/null 2>&1; then
  pkg_manager="pacman"
elif command -v zypper >/dev/null 2>&1; then
  pkg_manager="zypper"
fi

candidate_list() {
  local key="$1"
  case "$pkg_manager" in
    dnf)
      case "$key" in
        glmark2) echo "glmark2" ;;
        lshw) echo "lshw" ;;
        xlsclients) echo "xorg-x11-utils xlsclients xorg-x11-apps" ;;
        libinput) echo "libinput libinput-utils" ;;
        kscreen_doctor) echo "kscreen kscreen-utils" ;;
        qdbus) echo "qt6-qttools qt5-qttools" ;;
        xrandr) echo "xrandr" ;;
      esac
      ;;
    apt)
      case "$key" in
        glmark2) echo "glmark2" ;;
        lshw) echo "lshw" ;;
        xlsclients) echo "x11-utils" ;;
        libinput) echo "libinput-tools libinput10" ;;
        kscreen_doctor) echo "libkf5screen-bin libkf6screen-bin kscreen" ;;
        qdbus) echo "qttools5-dev-tools qt6-tools-dev-tools" ;;
        xrandr) echo "x11-xserver-utils" ;;
      esac
      ;;
    pacman)
      case "$key" in
        glmark2) echo "glmark2" ;;
        lshw) echo "lshw" ;;
        xlsclients) echo "xorg-xlsclients xorg-xset" ;;
        libinput) echo "libinput" ;;
        kscreen_doctor) echo "kscreen" ;;
        qdbus) echo "qt6-tools qt5-tools" ;;
        xrandr) echo "xorg-xrandr" ;;
      esac
      ;;
    zypper)
      case "$key" in
        glmark2) echo "glmark2" ;;
        lshw) echo "lshw" ;;
        xlsclients) echo "xorg-x11 xlsclients" ;;
        libinput) echo "libinput-tools libinput10" ;;
        kscreen_doctor) echo "kscreen5 kscreen6" ;;
        qdbus) echo "qt6-tools qdbus-qt6 qt5-tools" ;;
        xrandr) echo "xrandr" ;;
      esac
      ;;
    *)
      echo ""
      ;;
  esac
}

is_available() {
  local pkg="$1"
  case "$pkg_manager" in
    dnf)
      dnf -q repoquery --whatprovides "$pkg" >/dev/null 2>&1 || dnf -q list --available "$pkg" >/dev/null 2>&1
      ;;
    apt)
      apt-cache policy "$pkg" 2>/dev/null | awk -F': ' '/Candidate:/{if ($2 != "(none)") found=1} END{exit(found?0:1)}'
      ;;
    pacman)
      pacman -Si "$pkg" >/dev/null 2>&1
      ;;
    zypper)
      local out
      out="$(zypper -n search --match-exact "$pkg" 2>/dev/null || true)"
      [[ "$out" != *"No matching items found"* ]] && [[ "$out" == *"|"* ]]
      ;;
    *)
      return 1
      ;;
  esac
}

if [[ "$pkg_manager" == "unknown" ]]; then
  echo "[ERROR] no supported package manager found"
  exit 2
fi

refresh_repos() {
  case "$pkg_manager" in
    dnf)
      dnf -q makecache >/dev/null 2>&1 || true
      ;;
    apt)
      export DEBIAN_FRONTEND=noninteractive
      apt-get -qq update >/dev/null 2>&1 || true
      ;;
    pacman)
      pacman -Sy --noconfirm >/dev/null 2>&1 || true
      ;;
    zypper)
      zypper -n --gpg-auto-import-keys refresh >/dev/null 2>&1 || true
      ;;
  esac
}

echo "[INFO] target_distro=${TARGET_DISTRO}"
echo "[INFO] package_manager=${pkg_manager}"
echo "[INFO] refreshing package metadata"
refresh_repos

echo ""
echo "tool_key,first_match,candidates"
for key in "${TOOL_KEYS[@]}"; do
  candidates="$(candidate_list "$key")"
  match=""
  for pkg in $candidates; do
    if is_available "$pkg"; then
      match="$pkg"
      break
    fi
  done
  echo "${key},${match:-none},${candidates}"
done
