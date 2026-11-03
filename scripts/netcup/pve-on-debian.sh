#!/bin/bash
# pve-on-debian.sh -- turn a freshly installed Debian 13 (trixie) host into a Proxmox VE node.
#
# Runs ON THE TARGET HOST as root (copy it over, or pipe it: ssh root@HOST 'bash -s' < pve-on-debian.sh).
# It is the fallback route when the provider's ISO attach is unavailable (see PROXMOX-VE.md); it follows the
# official "Install Proxmox VE on Debian 13 Trixie" procedure plus the fixes that a vbpub-installed host needs.
#
# The script is split into phases because the Proxmox kernel must be booted before the rest is installed:
#   PHASE=1  repo + keyring + hostname check + proxmox-default-kernel; ends by telling you to reboot
#   (reboot; confirm `uname -r` ends in -pve; on a host that also has Debian kernels remove them first, see below)
#   PHASE=2  proxmox-ve + postfix + open-iscsi + chrony
#
# Environment (all optional):
#   PVE_KEYRING_SHA256  expected sha256 of the repo keyring (the value published on the wiki page above); when
#                       unset the script prints the hash and refuses to continue unless PVE_ACCEPT_UNVERIFIED=1
#   PVE_FQDN / PVE_IP   host name and primary IPv4 for /etc/hosts (default: `hostname -f` / first global IPv4)
#   PVE_ROOT_PW_FILE    file holding the root password to set (read here, never put on a command line);
#                       needed for the web UI login (realm "Linux PAM"); if unset the password is not touched
set -euo pipefail
PHASE=${PHASE:-1}
SUITE=trixie
KEYRING=/usr/share/keyrings/proxmox-archive-keyring.gpg
log() { printf '[pve-on-debian] %s\n' "$*"; }

[ "$(id -u)" = 0 ] || { echo "run as root" >&2; exit 2; }
. /etc/os-release
[ "${VERSION_CODENAME:-}" = "$SUITE" ] || { echo "expected Debian $SUITE, found ${VERSION_CODENAME:-?}" >&2; exit 2; }
export DEBIAN_FRONTEND=noninteractive
APT=(apt-get -y -o Dpkg::Options::=--force-confold -o Debug::pkgPolicy=0)

phase1() {
  local fqdn ip tmp
  fqdn=${PVE_FQDN:-$(hostname -f)}
  ip=${PVE_IP:-$(ip -4 -o addr show scope global | awk '{print $4}' | head -1 | cut -d/ -f1)}
  [ -n "$ip" ] || { echo "no global IPv4 found; set PVE_IP" >&2; exit 2; }
  # Proxmox requires the host name to resolve to the real address, not 127.0.1.1.
  sed -i '/^127\.0\.1\.1[[:space:]]/d' /etc/hosts
  grep -q "^$ip[[:space:]]" /etc/hosts || printf '%s %s %s\n' "$ip" "$fqdn" "${fqdn%%.*}" >> /etc/hosts
  getent hosts "$fqdn" | grep -q "$ip" || { echo "$fqdn does not resolve to $ip" >&2; exit 2; }

  tmp=$(mktemp)
  curl -fsSL -o "$tmp" "https://enterprise.proxmox.com/debian/proxmox-archive-keyring-$SUITE.gpg"
  got=$(sha256sum "$tmp" | cut -d' ' -f1)
  log "keyring sha256 $got"
  if [ -n "${PVE_KEYRING_SHA256:-}" ]; then
    [ "$got" = "$PVE_KEYRING_SHA256" ] || { echo "keyring sha256 mismatch" >&2; exit 3; }
  elif [ "${PVE_ACCEPT_UNVERIFIED:-0}" != 1 ]; then
    echo "compare the hash above with the wiki page, then re-run with PVE_KEYRING_SHA256=<hash>" >&2; exit 3
  fi
  install -m 0644 "$tmp" "$KEYRING"; rm -f "$tmp"

  cat > /etc/apt/sources.list.d/pve-install-repo.sources <<EOF
Types: deb
URIs: http://download.proxmox.com/debian/pve
Suites: $SUITE
Components: pve-no-subscription
Signed-By: $KEYRING
EOF
  # FIRST-MATCH-WINS: apt uses the first matching stanza across preferences.d in file-name order. The vbpub
  # debian-install pins `a=stable` (which also matches the Proxmox repo, suite "stable") at 500/550 in
  # `debian-priorities`; the Proxmox ceph/qemu libraries must beat the Debian ones, so this file sorts first.
  printf 'Package: *\nPin: release o=Proxmox\nPin-Priority: 1000\n' > /etc/apt/preferences.d/00-proxmox
  # Backports/testing/unstable stanzas (present on vbpub hosts) drag libelf1t64/libdw1t64/curl/openssh ahead of
  # trixie and make proxmox-ve uninstallable; PVE_PRUNE_APT=1 drops them (backup in /root/apt-backup).
  if [ "${PVE_PRUNE_APT:-0}" = 1 ] && [ -f /etc/apt/sources.list.d/debian.sources ]; then
    mkdir -p /root/apt-backup && cp -a /etc/apt/sources.list.d /etc/apt/preferences.d /root/apt-backup/
    python3 - <<'PY'
p = "/etc/apt/sources.list.d/debian.sources"
blocks = open(p).read().split("\n\n")
keep = [b for b in blocks if not any(("Suites: " + s) in b for s in ("trixie-backports", "testing", "unstable"))]
open(p, "w").write("\n\n".join(keep).rstrip("\n") + "\n")
PY
  fi
  "${APT[@]}" update
  "${APT[@]}" full-upgrade
  "${APT[@]}" install proxmox-default-kernel
  log "PHASE 1 done. Remove the Debian kernels so GRUB boots the -pve one, then reboot:"
  log "  apt-get remove linux-image-amd64 'linux-image-*+deb13-*' ; update-grub ; systemctl reboot"
}

phase2() {
  case "$(uname -r)" in *-pve) ;; *) echo "not running a -pve kernel ($(uname -r)); finish phase 1 and reboot" >&2; exit 2;; esac
  echo "postfix postfix/main_mailer_type select Local only" | debconf-set-selections
  echo "postfix postfix/mailname string $(hostname -f)" | debconf-set-selections
  # libelf1t64=0.192-4: only needed on hosts that took libelf from backports in phase 1; harmless otherwise.
  extra=()
  dpkg-query -W -f '${Version}' libelf1t64 2>/dev/null | grep -q bpo && extra+=(libelf1t64=0.192-4)
  "${APT[@]}" --allow-downgrades install "${extra[@]}" proxmox-ve postfix open-iscsi chrony
  # a running container engine with its own iptables rules fights the PVE firewall; this is a test node
  systemctl disable --now docker.socket docker.service 2>/dev/null || true
  if [ -n "${PVE_ROOT_PW_FILE:-}" ]; then
    { printf 'root:'; cat "$PVE_ROOT_PW_FILE"; echo; } | chpasswd
    log "root password set from $PVE_ROOT_PW_FILE"
  fi
  systemctl is-active pveproxy pvedaemon pve-cluster
  pveversion
  log "web UI: https://$(hostname -f):8006/ (self-signed certificate)"
  [ -e /dev/kvm ] && log "/dev/kvm present: VMs can use KVM" || log "no /dev/kvm: create VMs with --kvm 0 (TCG, slow)"
}

case "$PHASE" in 1) phase1;; 2) phase2;; *) echo "PHASE must be 1 or 2" >&2; exit 2;; esac
