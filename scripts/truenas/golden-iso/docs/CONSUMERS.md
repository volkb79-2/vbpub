# TrueNAS golden disk and unattended restore: consumer guide

This records a historical successful install of TrueNAS `27.0.0-RC.1` in an
x86_64 QEMU TCG VM and the 2026-10-31 20 GiB source rebuild. The vanilla RC.1
install, first boot, root SSH setup, API-key provisioning, and raw whole-disk
capture succeeded. The restore ISO build then stopped twice because package
installation inside the Debian Live chroot could not resolve
`deb.debian.org`. Host DNS and an HTTP request to the Debian repository worked
during diagnosis. No restore ISO, local restore replay, v1001 install, new
provider snapshot, or 20 GiB acceptance run exists yet. The existing 16 GiB
provider snapshot remains the only usable base. See the dated status below and
the operator text evidence under
`/home/vscode/vbpub-release-program-20261027/evidence/truenas-live-20261031/`.

The result sections below describe historical 16 GiB restore payloads and
their provider results. They do not certify the incomplete 20 GiB restore.

## 20 GiB source install and capture (2026-10-31)

- Source installer: vanilla `TrueNAS-27.0.0-RC.1.iso`, SHA-256
  `515a0252e72f1a6c86d603fa4d1661333029fe9a4804d0a7a28d24fe4f63498e`.
- Source disk: 20 GiB QCOW2, installed through the managed local TCG runner.
  The first three admission attempts were refused at memory PSI `some avg10`
  values 22.57%, 30.72%, and 38.47%; a later attempt passed after pressure
  subsided. The admission guard was not bypassed.
- First-boot `boot-pool`: 19 GiB size, 3.94 GiB allocated, 15.1 GiB free.
  This exceeds the required 12 GiB update headroom.
- Step 1 applied the protected sidecar password, configured root-key SSH with
  SSH password authentication disabled, created and validated a 30-day API
  key, and captured exactly 21,474,836,480 bytes. The raw SHA-256 is
  `721a8b22698f7be66d853c3719351849dacfc59271d023734a846615ebc6b1d7`; the
  secret sidecar is mode 0600.
- Step 2 verified the detached Debian Live signature and SHA-512 checksum and
  compressed the raw image, but failed twice while installing `sgdisk`,
  `efibootmgr`, and `eject` inside the Debian Live chroot. Both runs reported
  `Temporary failure resolving 'deb.debian.org'`. Host DNS and an HTTP HEAD
  request succeeded during diagnosis; the documented builder still failed.
- No 20 GiB restore ISO or local replay was produced. No v1001 provider action,
  snapshot, or acceptance run was started. Resume at the documented step 2
  build, then verify the local restore before using the image on v1001.

## Result

- TrueNAS ISO: `TrueNAS-27.0.0-RC.1.iso`, SHA-256
  `515a0252e72f1a6c86d603fa4d1661333029fe9a4804d0a7a28d24fe4f63498e`.
- QEMU: 10.0.13, `q35`, UEFI/OVMF, TCG, 2 vCPUs, 8 GiB RAM.
- Virtual disk: `16G` in QEMU, which is **16 GiB / 17,179,869,184 bytes**.
- The installer accepted that disk and completed. The installed GPT layout was
  a 1 MiB BIOS boot partition, a 512 MiB EFI partition, and a 15.5 GiB ZFS
  partition. `boot-pool` imported `ONLINE` after ejecting the ISO and rebooting
  from disk.
- After first boot, `boot-pool` reported 15 GiB size, 3.94 GiB allocated, and
  11.1 GiB free. `/` reported 11 GiB available.
- The root SSH key worked through QEMU's host forward. `ssh` ran `id -u` and
  returned `0`; password authentication was disabled in TrueNAS SSH settings.
- A sparse raw image was captured at
  `artifacts/truenas-27-16GiB-golden.raw`. `qemu-img compare` confirmed that
  its guest-visible sectors match the installed QCOW2 disk. Its virtual size
  is 16 GiB and host allocation was 4.26 GiB.

This demonstrates that a **16 GiB** boot disk suffices for this clean install,
first boot, and SSH setup. A decimal 16 GB disk is only about 14.9 GiB; that
smaller capacity was not tested. The VM had no data pool or user data.

The captured raw file is a whole-disk image with GPT, EFI, and ZFS content.
The restore ISO builder and runtime are documented below. This is a
single-target test image: it contains the configured admin password hash, root
authorized key, TrueNAS API-key record, SSH host keys, and machine identity.
The mode-0600 TOML sidecar beside the image keeps the admin password, root
private/public key, Mattermost webhook, and cleartext API key. The builder
embeds the complete disk and webhook, but not the cleartext API key or root
private key. Treat the image, sidecar, and ISO as private artifacts; they are
ignored by Git.

## Unattended restore test result (v3, historical)

The v3 restore ISO was 4,391,567,360 bytes with SHA-256
`36f631de1b10f39f0f8f7a620a5e47cf9d407506f7d11e908851f4e0a922e2aa`. A fresh
20 GiB raw target booted it in QEMU TCG without keyboard input. The five-second
GRUB timeout selected Debian Live; the runtime validated and wrote the bundled
16 GiB image, checked the write-stream hash, then read all 16 GiB back and
verified the same SHA-256. Mattermost startup, preflight, progress, verification,
GPT relocation, partition-layout, and completion notifications were delivered.

The target's backup GPT header was moved to the physical end of the 20 GiB
disk. The three partition start/end sectors, types, and unique GUIDs matched
the source; partition 3 remained sectors `1054720` through `33554398`, or
15.5 GiB. No ZFS partition or pool expansion occurred. With the ISO removed and
HDD boot selected, the restored EFI loader reached the TrueNAS 27 GRUB menu.
Root SSH then returned UID 0, `boot-pool` was ONLINE on `vda3` with no known
errors, and the Web UI redirected to `/ui/`. The restore and full-disk
read-back took about 34 minutes under TCG; first-boot middleware and SSH took
several more minutes. This successful run exercised v3, which did not yet write
an image-specific EFI restore receipt.

## v5 image and repeat-restore guard

The v5 ISO is 4,409,065,472 bytes with SHA-256
`aa0a6a0054a7f400cf6838071ff669a511208b0877346f311269d0dd18f949d3`. It embeds
the API key created and validated by step 1 before the golden disk was captured.
The raw image SHA-256 is
`8640d53b18712376dee615d58347441f90f1e2ac1d4dc92458f2c7ed9527c5cd`.

The v4 runtime had a false-skip defect: matching GPT identifiers and partition
extents were treated as proof that the full image was installed. Because the
new golden image retained those identifiers, a v4 boot could skip the write
and leave the previous API-key state in place. The v5 runtime skips only when
the EFI partition contains a receipt matching the exact raw-image digest and
size. It writes that receipt only after the whole-disk read-back hash and GPT
checks succeed. The regression checks cover matching GPT without a receipt, a
matching receipt, and a receipt for a different image.

The v5 ISO was uploaded and attached to Netcup v1001; the SCP attach and power
tasks completed. After the initial provider power cycle, SSH became available
on the existing TrueNAS installation, but the new API key and v5 EFI receipt
were absent. I then selected the provider DVD boot entry with EFI `BootNext`
and rebooted. The host remained RUNNING with the v5 ISO attached and CD-ROM
first in provider boot order; subsequent SSH and HTTPS probes were refused.
SCP disk metrics showed a read/write burst after that reboot, then low activity.

A local v5 TCG replay against a disposable 20 GiB disk reproduced a runtime
failure after both image SHA-256 checks and GPT verification passed. Debian
Live's `lsblk --json --output PATH,... /dev/vda` returned the disk and each
partition as separate top-level rows because the command omitted `--tree`.
The EFI receipt helper expected a single disk root with partition children,
so it stopped before writing the receipt, setting EFI `BootNext`, or rebooting.
This is the likely explanation for v1001's idle, unreachable guest after the
read/write burst; the same ISO, Debian tools, and `/dev/vda` are involved, but
the Netcup console was unavailable to confirm the guest's exact screen. The
v1001 restore is not certified until the current console or a successful
SSH/API check confirms the receipt, new API key, and ONLINE boot pool.

The fix adds `lsblk --tree` to EFI partition discovery and a regression test
for the Debian flat-output behavior. At the time of the earlier report, the v6
ISO was being rebuilt from the then-current golden image. That output is not
present in this checkout as of 2026-10-21; record its checksum and live/TCG
result here only after rebuilding and confirming them.

## Requirements and work directory

The tested host had `qemu-system-x86_64`, `qemu-img`, `curl`, `sha256sum`,
`openssl`, `ssh-keygen`, and the OVMF files in `/usr/share/OVMF`. No `/dev/kvm`
was present, so QEMU used software emulation. Run the VM command in a terminal
with interactive stdin. Use a second terminal in this directory for QMP and
host-side SSH commands.

```sh
cd /workspaces/vbpub/scripts/truenas/golden-iso
mkdir -p artifacts vm
```

## Download and verify the installer

```sh
ISO_URL=https://iso.sys.truenas.net/TrueNAS-27-RC/27.0.0-RC.1/TrueNAS-27.0.0-RC.1.iso
ISO_FILE=artifacts/TrueNAS-27.0.0-RC.1.iso
curl --fail --location --retry 5 --retry-all-errors --continue-at - \
  --output "$ISO_FILE" "$ISO_URL"
curl --fail --location --silent --show-error \
  --output "$ISO_FILE.sha256" "$ISO_URL.sha256"
printf '%s  %s\n' "$(cat "$ISO_FILE.sha256")" "$ISO_FILE" | sha256sum --check -
```

Expected checksum output ends in `artifacts/TrueNAS-27.0.0-RC.1.iso: OK`.
The checksum endpoint returns the digest by itself; the command above adds the
filename for `sha256sum --check`.

## Create the virtual disk, UEFI variables, and test credentials

`20G` means 20 GiB in `qemu-img`. The install steps below keep the same TrueNAS
edition, account choice, and firmware settings as the historical 16 GiB run;
only the source disk capacity changes. Step 1 reads the selected QCOW2's actual
virtual size and captures that exact whole-GiB disk; 16 GiB remains supported
for the legacy image. Copy the writable OVMF variables file for this VM; keep
the firmware code file read-only. Reuse the existing test key when continuity
with the acceptance scripts is required; generate a new key only for a separate
test identity.

```sh
qemu-img create -f qcow2 artifacts/truenas-27-20GiB-source-base.qcow2 20G
cp /usr/share/OVMF/OVMF_VARS_4M.fd vm/truenas-27-20GiB-OVMF_VARS.fd
test -f vm/root_ed25519 || ssh-keygen -q -t ed25519 -N '' -C truenas-golden-iso-test -f vm/root_ed25519
umask 077
openssl rand -hex 12 > artifacts/truenas-installer-password
openssl rand -base64 32 > artifacts/truenas-admin-password
chmod 600 artifacts/truenas-installer-password artifacts/truenas-admin-password vm/root_ed25519 vm/truenas-27-20GiB-OVMF_VARS.fd
```

The `ssh-keygen -N ''` key has no passphrase and is for this local disposable
test only. For a non-disposable system, generate a separate key and protect it
with a passphrase. Keep the installer password separate from the canonical
golden-image administrator password: the installer password is only needed to
finish this manual install, while step 1 applies the password from the secret
sidecar before it captures the image.

The generated canonical admin password is 32 random bytes encoded as Base64.
Keep both password files private; they are ignored by Git. To type the
temporary installer password, display it in a private second terminal:

```sh
cat artifacts/truenas-installer-password
```

## Start the source VM through the managed TCG runner

Use the TrueNAS runner so the VM starts in its configured cgroup with the
shared memory-pressure admission check. The runner requires a selected image,
the existing Docker network, a loaded cgroup slice, and OVMF files; see the
[shared TCG consumer guide](../../docs/CONSUMERS.md#run-a-local-truenas-tcg-guest).
For the 20 GiB source recipe, run from `scripts/truenas`:

```sh
mkdir -p .vm/truenas-27rc1-20g-source
export TRUENAS_TCG_IMAGE='debian-install-vm:<installed-image-tag>'
python3 tcg_vm.py start \
  --name truenas-27rc1-20g-source \
  --base-disk golden-iso/artifacts/truenas-27-20GiB-source-base.qcow2 \
  --ovmf-code /usr/share/OVMF/OVMF_CODE_4M.fd \
  --ovmf-vars golden-iso/vm/truenas-27-20GiB-OVMF_VARS.fd \
  --state-dir .vm/truenas-27rc1-20g-source \
  --image "$TRUENAS_TCG_IMAGE" \
  --network "$DOCKER_NETWORK_INTERNAL" \
  --cgroup-parent "$CGROUP_PARENT_DEV_GATES" \
  --max-concurrent-guests 1 \
  --readonly-media golden-iso/artifacts/TrueNAS-27.0.0-RC.1.iso \
  --vnc-console --serial-console
python3 tcg_vm.py console sendkey \
  --name truenas-27rc1-20g-source \
  --state-dir .vm/truenas-27rc1-20g-source down
python3 tcg_vm.py console sendkey \
  --name truenas-27rc1-20g-source \
  --state-dir .vm/truenas-27rc1-20g-source ret
python3 tcg_vm.py console serial \
  --name truenas-27rc1-20g-source \
  --state-dir .vm/truenas-27rc1-20g-source
```

At the first GRUB menu, choose **Start TrueNAS Installation (115200 baud)**,
the second item. The first, VGA installer entry does not provide the needed
serial output. After the install and first-boot configuration, close the serial
console with `Ctrl-]`, then stop and destroy only the managed container. Its
state directory retains the overlay and installed UEFI variables. Flatten
that stopped overlay into the source QCOW2 and carry forward the installed
variable store before running step 1:

```sh
python3 tcg_vm.py stop --name truenas-27rc1-20g-source \
  --state-dir .vm/truenas-27rc1-20g-source
python3 tcg_vm.py destroy --name truenas-27rc1-20g-source
qemu-img convert -f qcow2 -O qcow2 \
  .vm/truenas-27rc1-20g-source/disk-overlay.qcow2 \
  golden-iso/artifacts/truenas-27-20GiB-source-installed.qcow2
cp .vm/truenas-27rc1-20g-source/OVMF_VARS_4M.fd \
  golden-iso/vm/truenas-27-20GiB-OVMF_VARS.fd
```

Pass `--vm-disk golden-iso/artifacts/truenas-27-20GiB-source-installed.qcow2`
to step 1 (or move the flattened image to its default source path). The step 1
script reboots that installed disk to apply the root key, password, and API-key
configuration before capture.

## Install TrueNAS

In the VM serial console, follow these exact choices:

1. At **TrueNAS 27.0.0-RC.1 Console Setup**, choose `1` (Install/Upgrade).
2. Select the only destination, `vda`, shown as 20 GiB, with Space. Press
   Enter to accept the default **OK** button.
3. Confirm the erase warning with **Yes**. The VM disk is disposable.
4. Choose **Administrative user (truenas_admin)**.
5. Enter the temporary installer password, press Tab to move to confirmation,
   enter it again, then press Tab twice to focus **OK** and press Enter. The
   installer states that root password login will be disabled. Pressing Enter
   while a password field has focus submits the form before confirmation.
6. Wait for **Installation completed successfully**.

The installer screens and choices match the official
[TrueNAS 27 installation guide](https://www.truenas.com/docs/scale/27/gettingstarted/install/installingscale/).

The historical 16 GiB install took several minutes under TCG. The installer
emitted `/init: line 29: can't create /root/etc/network/interfaces: nonexistent
directory` during live boot, then continued normally and completed. The
installation screen reported success on `vda`.

## Eject the ISO and boot from the virtual disk

While the installer success dialog is still displayed, run this in the second
terminal. QEMU identified the CD device as `ide2-cd0`.

```sh
python3 -c 'import qmp; p="vm/qmp-tty.sock"; qmp.qmp_command(p, "blockdev-open-tray", {"device":"ide2-cd0"}); qmp.qmp_command(p, "eject", {"device":"ide2-cd0", "force":True})'
```

Back in the VM serial console, press Enter to close the success dialog, then
choose `3` (Reboot System). UEFI loaded the installed `TrueNAS-0` entry from
the GPT EFI partition, and GRUB showed `TrueNAS Scale GNU/Linux 27.0.0-RC.1`.
Wait for the console setup menu and its DHCP-assigned Web UI address.

## Add a root SSH key through TrueNAS middleware

At the TrueNAS console setup menu, choose `8` (Open Linux Shell). This opens a
root shell on the VM console. Use the public key from the host at
`vm/root_ed25519.pub`; do not copy the private key into TrueNAS.

First query the actual root and SSH service records:

```sh
midclt call user.query '[["username","=","root"]]'
midclt call service.query '[["service","=","ssh"]]'
```

In this run, root had middleware id `1` and SSH service id `11`. Use the ids
returned on the VM you are configuring. Replace the example public key below
with the complete one-line contents of `vm/root_ed25519.pub`:

```sh
midclt call user.update 1 '{"sshpubkey":"ssh-ed25519 AAAA... truenas-golden-iso-test","ssh_password_enabled":false}'
midclt call ssh.update '{"passwordauth":false}'
midclt call service.update 11 '{"enable":true}'
midclt call service.control START ssh
```

`service.control` takes the verb first and the service name second. The call
returns a job id; inspect its final state with
`midclt call core.get_jobs '[["id","=",JOB_ID]]'`, replacing `JOB_ID` with the
returned number. In this run the job finished `SUCCESS`, and
`service.query` reported SSH `RUNNING` and enabled on boot.

The root query after the update reported `password_disabled: true`,
`ssh_password_enabled: false`, and the configured `sshpubkey`. The global SSH
password setting was also false. TrueNAS documents root SSH access as a
security risk; keep password authentication disabled, protect the private
key, and only enable root SSH where this access is required. See the
[TrueNAS SSH service guide](https://www.truenas.com/docs/scale/27/systemsettings/services/sshservice/),
[`user.update`](https://api.truenas.com/v27.0/api_methods_user.update.html),
[`ssh.update`](https://api.truenas.com/v27.0/api_methods_ssh.update.html), and
[`service.control`](https://api.truenas.com/v27.0/api_methods_service.control.html)
references.

Test key login from the host in the second terminal:

```sh
ssh -T -i vm/root_ed25519 \
  -o IdentitiesOnly=yes \
  -o StrictHostKeyChecking=accept-new \
  -o UserKnownHostsFile=vm/truenas-27-20GiB-known_hosts \
  -o ConnectTimeout=10 \
  -p 2222 root@127.0.0.1 \
  'id -u; uname -r; zpool status'
```

The tested output included UID `0`, kernel `6.18.52-production+truenas`, and
`boot-pool ONLINE` with no data errors. The Web UI responded at
`https://127.0.0.1:8443/` and redirected to `/ui/`. The password was set in the
installer; this run did not submit a browser login form.

## Historical 16 GiB install inspection

These commands were run in the TrueNAS Linux shell. The VM's virtio boot disk
appears as `/dev/vda`:

```sh
lsblk -o NAME,SIZE,TYPE,FSTYPE,MOUNTPOINTS
fdisk -l /dev/vda
zpool status
zpool list
zfs list
df -h /
```

The tested partition table was:

```text
Device       Start      End  Sectors  Size Type
/dev/vda1     4096     6143     2048    1M BIOS boot
/dev/vda2     6144  1054719  1048576  512M EFI System
/dev/vda3  1054720 33554398 32499679 15.5G Solaris /usr & Apple ZFS
```

`boot-pool` was a single-disk pool on `vda3`, `ONLINE`, with no known data
errors. The boot partition layout is created by the TrueNAS installer; this
test did not resize or repair partition tables.

## Prepare and capture the golden raw disk

Before the first automated capture, create the sibling secret TOML from the
canonical golden-image administrator password, root key pair, and Mattermost
webhook. The canonical password may differ from the temporary install password;
step 1 applies the sidecar value before capture. The image path supplies the
expected image basename; it need not exist yet. Keep the admin password file
mode 0600 and retain the SSH private key in the protected `vm/` directory for
step 1 and later host verification:

```sh
./step2-build-unattended-restore-iso.py init-secrets \
  --image artifacts/truenas-27-20GiB-golden.raw \
  --admin-password-file artifacts/truenas-admin-password \
  --root-private-key vm/root_ed25519 \
  --root-public-key vm/root_ed25519.pub \
  --webhook-file /workspaces/vbpub/mattermost-server/.ciu/secrets/installer_webhook_url \
  --output artifacts/truenas-27-20GiB-golden.secrets.toml
stat -c '%a %n' artifacts/truenas-27-20GiB-golden.secrets.toml
```

The secret file must be mode `600`. The builder validates the admin password,
Ed25519 key pair, HTTPS webhook, and image basename; do not print the TOML.
The source VM must be powered off before running step 1. It starts the installed
20 GiB QCOW2 with loopback-only SSH/API forwards, updates the admin password,
ensures root key access with SSH password authentication disabled, creates a
TrueNAS API key with the requested expiry, validates it through the shared API
client, and stores the one-time cleartext key in the TOML. It then shuts the VM
down cleanly, converts the whole QCOW2 disk to raw, compares every sector, and
checks the GPT, EFI loader, size, and SHA-256:

```sh
./step1-install-prepare-truenas.py --api-key-expiry-days 30
sha256sum artifacts/truenas-27-20GiB-golden.raw
stat -c '%a %n' artifacts/truenas-27-20GiB-golden.secrets.toml
```

Choose an expiry interval long enough for capture, upload, restore, and API
verification; it is a required option so the script does not invent a policy.
If a raw image already exists and should be replaced after successful checks,
add `--force`. A saved, still-valid API key is authenticated and reused rather
than duplicated. The script writes secrets atomically with mode 0600 and
reclaims a newly created key if validation or sidecar persistence fails. It
never prints the API key or admin password. Keep the sidecar beside the raw
image; `step2` reads the webhook but does not copy the cleartext API key or root
private key into the ISO. If a previous failed run left the reserved key
recorded in TrueNAS without a sidecar secret, inspect it first and use
`--revoke-orphan-api-key` only to replace that confirmed orphan. A normal retry
never deletes a key silently. Shutdown runs as a nonblocking systemd job so
the SSH session can return while TrueNAS stops its network services.

## Build the unattended restore ISO

Install `xorriso`, `squashfs-tools`, `zstd`, `gdisk`, `mtools`, `dosfstools`,
`gpgv`, and `debian-keyring`. Download the Debian Live 13.7.0 standard ISO,
`SHA512SUMS`, and `SHA512SUMS.sign` from
`https://cdimage.debian.org/debian-cd/current-live/amd64/iso-hybrid/`. Verify
the detached signature with
`gpgv --keyring /usr/share/keyrings/debian-role-keys.pgp SHA512SUMS.sign SHA512SUMS`
and verify the ISO's SHA-512 against that file before building.

Run the builder as root so the repacked Debian Live filesystem retains its
ownership. It checks the signed base ISO, raw GPT layout, EFI fallback loader,
and secret sidecar; then embeds the compressed raw image, restore service, and
tools. The ISO SHA-256 sidecar is written next to the output. With `sudo`, the
ISO is returned to the calling user with mode 0600:

```sh
sudo python3 step2-build-unattended-restore-iso.py build \
  --base-iso artifacts/debian-live-13.7.0-amd64-standard.iso \
  --base-checksums artifacts/SHA512SUMS \
  --base-signature artifacts/SHA512SUMS.sign \
  --debian-cd-keyring /usr/share/keyrings/debian-role-keys.pgp \
  --image artifacts/truenas-27-20GiB-golden.raw \
  --secrets artifacts/truenas-27-20GiB-golden.secrets.toml \
  --output artifacts/truenas-27-20GiB-unattended-restore.iso
sha256sum --check artifacts/truenas-27-20GiB-unattended-restore.iso.sha256
xorriso -indev artifacts/truenas-27-20GiB-unattended-restore.iso -report_el_torito plain
```

At runtime the service requires UEFI and exactly one unmounted, writable,
non-removable disk apart from the ISO. It reports discovered disks and
identifiers, verifies the size-derived compressed payload, writes exactly the
20 GiB raw image recorded in this recipe's manifest, checks the written stream
and a full read-back, repairs only the backup GPT header on a larger disk,
checks that partition extents did not change, sets UEFI BootNext, and reboots.
It does not grow partition 3 or the ZFS pool. The ISO contains the Mattermost
webhook, so keep it private.

## QEMU TCG restore test

Use a disposable 20 GiB raw target to verify the 20 GiB restore. Copy fresh
UEFI variables before starting the VM:

```sh
qemu-img create -f raw vm/restore-target.raw 20G
cp /usr/share/OVMF/OVMF_VARS_4M.fd vm/restore-vars.fd
qemu-system-x86_64 \
  -name truenas-unattended-restore-test \
  -machine q35,accel=tcg -cpu max -smp 2 -m 8G \
  -rtc base=utc,clock=host \
  -drive if=pflash,format=raw,readonly=on,file=/usr/share/OVMF/OVMF_CODE_4M.fd \
  -drive if=pflash,format=raw,file=vm/restore-vars.fd \
  -drive file=vm/restore-target.raw,if=virtio,format=raw \
  -cdrom artifacts/truenas-27-20GiB-unattended-restore.iso \
  -boot order=d,menu=on \
  -netdev user,id=net0,hostfwd=tcp:127.0.0.1:2223-:22,hostfwd=tcp:127.0.0.1:8444-:443 \
  -device virtio-net-pci,netdev=net0 \
  -display none -monitor none -serial file:vm/restore-serial.log \
  -qmp unix:vm/restore-qmp.sock,server=on,wait=off
```

The ISO's GRUB menu selects Debian Live automatically after five seconds; do
not send a key to start the unattended path. Use QMP to inspect the console
while the restore service runs:

```sh
python3 qmp.py --socket vm/restore-qmp.sock screenshot vm/restore-screen.png
python3 qmp.py --socket vm/restore-qmp.sock status
```

On the tested QEMU command, `-boot order=d` makes the attached CD-ROM the only
firmware boot choice. After the restore and its reboot, stop QEMU at the UEFI
screen, then relaunch the same target without `-cdrom` and with HDD boot order.
This models detaching the provider ISO and restoring HDD-first boot:

```bash
python3 qmp.py --socket vm/restore-qmp.sock quit
qemu-system-x86_64 \
  -name truenas-restore-disk-boot-test \
  -machine q35,accel=tcg -cpu max -smp 2 -m 8G \
  -rtc base=utc,clock=host \
  -drive if=pflash,format=raw,readonly=on,file=/usr/share/OVMF/OVMF_CODE_4M.fd \
  -drive if=pflash,format=raw,file=vm/restore-vars.fd \
  -drive file=vm/restore-target.raw,if=virtio,format=raw \
  -boot order=c,menu=on \
  -netdev user,id=net0,hostfwd=tcp:127.0.0.1:2223-:22,hostfwd=tcp:127.0.0.1:8444-:443 \
  -device virtio-net-pci,netdev=net0 \
  -display none -monitor none -serial file:vm/restore-serial-diskboot.log \
  -qmp unix:vm/restore-qmp-diskboot.sock,server=on,wait=off
```

The restored ESP should take the VM to the TrueNAS GRUB menu without input.
TrueNAS middleware and SSH can take several minutes to start under TCG. Test
root SSH using the private key from the sibling TOML (write it to an ignored,
mode-0600 file under `vm/`):

```bash
python3 - <<'PY'
import tomllib
from pathlib import Path

secrets = tomllib.loads(Path("artifacts/truenas-27-20GiB-golden.secrets.toml").read_text())
key = Path("vm/root_ed25519")
key.write_text(secrets["root_ssh"]["private_key"].rstrip() + "\n")
key.chmod(0o600)
PY
ssh -T -i vm/root_ed25519 \
  -o IdentitiesOnly=yes \
  -o StrictHostKeyChecking=accept-new \
  -o UserKnownHostsFile=vm/truenas-27-20GiB-known_hosts \
  -o ConnectTimeout=10 \
  -p 2223 root@127.0.0.1 \
  'id -u; uname -r; zpool status'
curl -kIsS --connect-timeout 10 https://127.0.0.1:8444/
```

The tested output was UID `0`, kernel `6.18.52-production+truenas`, and
`boot-pool ONLINE` on `vda3` with no data errors. The Web UI returned HTTP 302
to `/ui/`. Partition 3 still ended at sector `33554398` on the 20 GiB target.

## Netcup live restore

For any future live test of a restored image, the first action after the host
boots is to change `truenas_admin` from the recipe's publicly known bootstrap
password to a fresh random value. Store it only in a mode-0600, gitignored
host-specific TOML file and use that file for later UI/API gate authentication.
Keep it separate from the golden-image sidecar. This credential rotation is a
required test preflight; the existing installer scripts currently restore the
image as captured and do not automate this host-specific change.

Use the SCP CLI for upload, attach, power, and detach. Confirm the server id
before using the destructive flow; `v1001.vxxu.de` is id `804027`. The exact
upload and boot/detach commands are in
[`CONSUMERS.md`](CONSUMERS.md).
The ISO only configures UEFI BootNext; Netcup API credentials stay on the
controller. The v3 ISO uploaded in 66 parts and was restored to v1001's single
512 GiB virtio disk. Netcup reported 16,387 MiB allocated, matching the
16 GiB image. The ISO was detached and the provider boot order was
`HDD,NETWORK,CDROM`.

The first VNC view showed the EDK2 firmware menu. Choosing **Continue** opened
the TrueNAS GRUB menu, whose default disk entry booted TrueNAS. This was not a
failed image restore: the guest had its expected partitions and an ONLINE boot
pool. The provider live record still had `serverLiveInfo.configChanged=true`,
matching Netcup's **Reboot required** advisory. A cold provider power cycle
was needed to apply those pending settings. After it finished, the flag was
false, and EFI reported `BootCurrent: 000A` with `BootOrder` beginning at
`000A` (the TrueNAS disk entry); the CD-ROM was no longer ahead of the disk.
The host
returned root SSH without console input 26 seconds after the task finished.

### Boot-order incident follow-up (2026-10-15)

The Netcup event log for the initial run recorded: power cycle at 07:12;
CD-ROM attach and detach at 08:11; power cycle at 08:16; boot-order change and
CD-ROM detach at 08:22; then another power cycle at 09:25. The UEFI setup
screen appeared during this sequence; choosing **Continue** reached the
installed disk's TrueNAS GRUB menu. The disk image and EFI loader were usable.
The controlled fresh restore below reproduced the boot-selection failure when
the CD-ROM was first in provider boot order, then booted unattended after the
ISO was detached, HDD-first order restored, and the provider cold-cycled. This
points to the provider boot-order/virtual-media transition as the cause, not a
bad disk image. I did not have a VNC view during that repeat, so I cannot
confirm whether its no-SSH interval displayed the same EDK2 screen.

I repeated the reboot checks with v3 attached while leaving provider boot order
at `HDD,NETWORK,CDROM`:

1. Linux `CDROMEJECT` ioctl (`0x5309`) returned success, but SCP still reported
   `isoAttached: true`; the guest still saw the 4.1 GiB ISO. Guest-side eject
   does not detach Netcup's virtual media.
2. A TrueNAS guest reboot with the ISO still attached and provider boot order
   unchanged returned to SSH. `boot-pool` was ONLINE on `vda3`.
3. I detached the ISO through SCP without changing boot order. SCP reported
   `isoAttached: false` and `configChanged: false`. A guest reboot again
   returned to SSH with `boot-pool` ONLINE on `vda3`.

These checks show that the ISO being attached is not sufficient to cause the
failure when HDD remains first, and that guest-side eject does not remove the
provider media. The remaining difference in the initial run was Netcup's
CDROM-first boot-order change.

Repeat the final cold cycle after detaching the ISO and restoring provider
HDD-first order. Use the supported SCP commands from `scripts/netcup`:

```sh
cd /workspaces/vbpub/scripts/netcup
./.venv/bin/python scp-api.py server-details 804027 --json
./.venv/bin/python scp-api.py iso-attached 804027 --json
./.venv/bin/python scp-api.py boot-order 804027 --json
./.venv/bin/python scp-api.py power cycle 804027 --yes --json
# Watch the task UUID returned by power:
./.venv/bin/python monitor-task.py watch TASK_UUID --poll 2 --progress plain
```

The live test used the root Ed25519 key and verified the host key against the
key recorded by the golden image. After the cold cycle, `id -u` returned `0`,
`middlewared` was active, `boot-pool` was ONLINE on `vda3` with no known errors,
and HTTPS returned a redirect to `/ui/`. The guest received its Netcup IPv4
address through DHCP. The Netcup **QEMU Guest Agent is not running** advisory
is expected because this image does not install that optional agent.

### Fresh unattended restore replay

On 2026-10-15, I exercised the actual image-write path on v1001. It already had
the golden image, so before the test I backed up its GPT and randomized only
the disk and partition GUIDs. Partition starts and ends stayed unchanged and
`boot-pool` remained ONLINE. These commands were run over verified root SSH on
this disposable, single-disk test host; they are only needed to force a second
write to an already-restored test disk. For a new blank target, do not
randomize its table; the installer writes the image directly.

I attached the ISO and waited for the attach task to finish:

```sh
cd /workspaces/vbpub/scripts/netcup
./.venv/bin/python scp-api.py attach-iso 804027 \
  --user-iso-name truenas-27-unattended-restore-v3.iso \
  --change-boot-device-to-cdrom --yes --json
./.venv/bin/python monitor-task.py watch ATTACH_TASK_UUID \
  --poll 1 --progress plain
```

After attach and before reboot, run these commands on the TrueNAS host over
verified root SSH. They change only disk/partition GUIDs, not partition
geometry; `boot-pool` remained ONLINE:

```sh
sgdisk --backup=/root/vda-gpt-before-unattended-test.bin /dev/vda
sgdisk --randomize-guids /dev/vda
sgdisk --verify /dev/vda
```

Then cold-cycle Netcup and watch its task:

```sh
./.venv/bin/python scp-api.py power cycle 804027 --yes --json
./.venv/bin/python monitor-task.py watch POWER_TASK_UUID \
  --poll 2 --progress plain
```

For this replay the kernel reported that its old GPT remained active until
reboot. SCP showed
`CDROM,HDD,NETWORK` and `configChanged: false` after that cycle. Netcup disk
metrics recorded a large read/write burst, consistent with the restore and
read-back; SSH did not return while the ISO was attached and CD-ROM remained
first.

I then detached the ISO through SCP, set `HDD,NETWORK,CDROM`, and cold-cycled
again. Root SSH returned about 25 seconds after the provider task completed.
EFI reported `BootCurrent: 0009` and `BootOrder` beginning with `0009` (the
TrueNAS disk entry). `boot-pool` was ONLINE on `vda3`, with 3.94 GiB allocated
and 11.1 GiB free. GPT verification found no errors. Partition 3 remained
sectors `1054720` through `33554398` (15.5 GiB); no ZFS partition or pool
growth occurred. Netcup then reported RUNNING, ISO detached, HDD-first order,
and `configChanged: false`.

These were the recovery commands. Watch each task UUID returned by the
mutating command before starting the next provider action:

```sh
./.venv/bin/python scp-api.py iso-attached 804027 detach --yes
./.venv/bin/python scp-api.py boot-order 804027 set HDD,NETWORK,CDROM \
  --yes --json
./.venv/bin/python monitor-task.py watch BOOT_ORDER_TASK_UUID \
  --poll 1 --progress plain
./.venv/bin/python scp-api.py power cycle 804027 --yes --json
./.venv/bin/python monitor-task.py watch POWER_TASK_UUID \
  --poll 2 --progress plain
```

During the historical v3 Netcup test, the restored image did not yet contain
the current API key, so I created a new 30-day `truenas_admin` key afterward,
validated it through the WebSocket client, and saved it to the ignored
mode-0600 `scripts/truenas/.env`. The current step 1 now creates and validates
the key before capture and writes it to the mode-0600 sibling secrets file.
The v5 image therefore carries that API-key record; the v5 live test has not
yet confirmed it on v1001. Pool, dataset, and VM list queries against the
historical one-disk test host returned empty arrays, as expected.

`scripts/truenas/truenas.toml` points the CLI at the host; TLS verification is
disabled there only because this test host presents a self-signed certificate.
The project client authenticated over WebSocket and `SCRAM`. VM creation was
not attempted: this Netcup server has no nested virtualization and no data
disk or data pool.

For a time-limited key created with `midclt call api_key.create`, encode
`expires_at` as TrueNAS EJSON `{"$date": <UTC epoch milliseconds>}`. The
method schema displays a date-time string, but `midclt` rejected a plain ISO
string and accepted the EJSON date object. The key is returned only once; keep
that response out of shell history and logs, and write the key into the
mode-0600 `.env` file.

The raw image contains the generated test administrator password hash, root
public key, and VM SSH host keys. Its private root key and cleartext admin
password remain in ignored local artifacts. Do not distribute this exact
image as a shared golden template.


## Current scripted Netcup install

Run the provider workflow from `scripts/netcup`. It uploads the ISO in parts,
verifies the remote size, checkpoints the current boot order, attaches the ISO,
and power-cycles into the restore environment:

```sh
cd /workspaces/vbpub/scripts/netcup
./.venv/bin/python scp-api.py servers
./.venv/bin/python install-restore-iso.py start 804027 \
  --iso ../truenas/golden-iso/artifacts/truenas-27-20GiB-unattended-restore.iso \
  --yes --progress plain
./.venv/bin/python install-restore-iso.py status 804027
```

Wait for Mattermost's restore-complete notification, then detach the provider
ISO, restore the saved boot order, cold-cycle the server, and verify SSH and
`boot-pool` before declaring success:

```sh
./.venv/bin/python install-restore-iso.py finish 804027 \
  --restore-complete \
  --ssh-host v1001.vxxu.de \
  --ssh-host-key-alias '[127.0.0.1]:2223' \
  --ssh-identity-file ../truenas/golden-iso/vm/root_ed25519 \
  --ssh-known-hosts ../truenas/golden-iso/vm/restore-known_hosts \
  --yes --progress plain
```

`--restore-complete` is an explicit operator assertion. A running provider task,
disk activity, or matching GPT identifiers do not prove the restore completed.
Guest `CDROMEJECT` does not detach Netcup's ISO. The provider workflow owns
attach/detach, boot order, and power controls; see the
[Netcup restore guide](../../../netcup/CONSUMERS.md#restore-a-truenas-golden-disk).
