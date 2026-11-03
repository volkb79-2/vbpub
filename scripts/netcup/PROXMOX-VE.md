# Proxmox VE on a Netcup test server

Placeholders: `<SERVER_ID>` numeric SCP id, `<HOST>` its FQDN. Only ever target servers on the explicit allowlist of the
task; every mutating call names `<SERVER_ID>` explicitly.

## Route A: official ISO through the SCP (preferred, not working on every server)

1. Download `proxmox-ve_<VER>.iso` from `https://enterprise.proxmox.com/iso/` and verify it against `SHA256SUMS` from the
   same place (the sha256 is also printed on proxmox.com/downloads). `download.proxmox.com` had a TLS name mismatch on
   2026-10-27; do not bypass it with `-k`.
2. Upload and attach with the restore-ISO CLI (`install-restore-iso.py start <SERVER_ID> --iso <FILE>
   --user-iso-name <NAME>.iso --yes`, or `scp-api.py user-iso` + `attach-iso`). User ISOs are deleted by Netcup after
   48 h; delete your own upload when done (`DELETE /api/v1/users/<USER_ID>/isos/<NAME>`).
3. Drive the installer through the SCP screen with `pwmcp/tools/console-capture/web_console_session.py` (login, `canvas`
   reads, `press`/`typekeys`; see WEB-CONSOLES.md). Detach the ISO and restore the boot order afterwards.

**Observed blocker (2026-10-27, one server):** the upload succeeds (26 parts, 1.7 GB, about 40 s), but every
`ServerAttachIsoTask` ends in `ERROR error.internalserver` at step `ServerAttachIsoTaskStepAttachWait` after about
110 s: for the uploaded ISO and for a provider ISO (Finnix, id 22), with the guest running or powered off, via the API
and via the SCP web UI ("Attach" in Media > DVD Drive, which first asks to re-enter the password in a popup). The guest
keeps its empty 1 GiB `sr0`. Another server of the same template attaches ISOs fine, so this looks like a provider-side
fault on that server; report it to Netcup support with the task UUIDs instead of retrying.

Unattended variant for later: `proxmox-auto-install-assistant prepare-iso <ISO> --fetch-from http --url https://<ANSWER-HOST>/answer`
(or `--fetch-from iso --answer-file answer.toml`) produces an ISO that needs no screen driving; it still needs a working
ISO attach.

## Route B: Debian then `proxmox-ve` (used when Route A is blocked)

On a Debian 13 host with root SSH:

```bash
scp scripts/netcup/pve-on-debian.sh root@<HOST>:/root/
ssh root@<HOST> 'PVE_KEYRING_SHA256=<hash from the wiki> PVE_PRUNE_APT=1 PHASE=1 bash /root/pve-on-debian.sh'
ssh root@<HOST> 'apt-get -y remove linux-image-amd64 "linux-image-*+deb13-*"; update-grub; systemctl reboot'
# uname -r must now end in -pve
ssh root@<HOST> 'PVE_ROOT_PW_FILE=/root/pw PHASE=2 bash /root/pve-on-debian.sh'     # pw file: 0600, delete afterwards
```

Pitfalls found (all handled by the script): `a=stable` pins in a vbpub host's `debian-priorities` also match the Proxmox
repository and make Debian's ceph libraries win (first matching stanza in file-name order wins, so the Proxmox pin is
`00-proxmox`); backports/testing/unstable on the host make `proxmox-ve` uninstallable; `full-upgrade` installs a newer
Debian kernel that GRUB prefers until it is removed; a virtualised guest has no `/dev/kvm` (check `nestedGuest` in
server-details), so VMs need `--kvm 0` and run under TCG; `ifupdown` is replaced by `ifupdown2`, the static
`/etc/network/interfaces` keeps working.

Verify: `https://<HOST>:8006/` (self-signed; realm "Linux PAM", user `root`), `pveversion`, `pvesm status`.
A small test VM: `pvesh create /nodes/<NODE>/storage/local/download-url --url <ISO-URL> --content iso --filename <F>
--checksum <SHA256> --checksum-algorithm sha256`, then `qm create <ID> --memory 512 --cores 1 --kvm 0 --scsi0
local:2,format=qcow2 --ide2 local:iso/<F>,media=cdrom --boot 'order=ide2;scsi0' --serial0 socket --vga std`.
Proxmox has no per-VM console password: the web consoles use a short-lived ticket; the guest login is the guest's own.
Console measurements: `pwmcp/tools/console-capture/WEB-CONSOLES.md`.
