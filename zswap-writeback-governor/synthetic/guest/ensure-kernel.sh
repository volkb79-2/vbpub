#!/usr/bin/env bash
# Install a 7.x kernel in the VM-harness guest (Debian 13 ships 6.12, whose zswap
# shrinker still has the older "protected" accounting, not the v7.2 one analysed
# in docs/KERNEL-ANALYSIS.md). Runs INSIDE the guest as root.
# Exit 0: already >= 7.x.  Exit 10: a newer kernel was installed, REBOOT the guest.
set -euo pipefail

major="$(uname -r | cut -d. -f1)"
if [ "$major" -ge 7 ]; then
    echo "ensure-kernel: running $(uname -r), nothing to do"
    exit 0
fi

cat > /etc/apt/sources.list.d/zwg-trixie-backports.sources <<'EOF'
Types: deb
URIs: http://deb.debian.org/debian
Suites: trixie-backports
Components: main
Signed-By: /usr/share/keyrings/debian-archive-keyring.pgp
EOF
apt-get update
apt-get install -y -t trixie-backports linux-image-cloud-amd64
candidate="$(dpkg-query -W -f='${Version}\n' linux-image-cloud-amd64)"
echo "ensure-kernel: installed linux-image-cloud-amd64 $candidate; reboot required"
exit 10
