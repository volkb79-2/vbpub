<!-- origin: 2026-10-27; status: decided; copied from the controller program dir 2026-11-10 -->
<!-- source: iogov-install-record.md -->
<!-- relation: install acceptance record; not installed -->

# iogov v1 host install record (2026-10-27)

STATUS: NOT INSTALLED. Stopped at preconditions (see below). Nothing was created, enabled or started on the host.

Host changes made during investigation: none persistent. A temporary /root/iogov-plan.toml was written and removed
(three times) to run `python3 -m iogov plan` / `config` from the repo checkout with PYTHONDONTWRITEBYTECODE=1.
Each `host-escape` call also ran `mdt doctor` (reported "cgroup2 OK", no repair).

## Findings
1. `setfacl` (package `acl`) is not installed on the host; the udev rule in README step 3 needs /usr/bin/setfacl.
   `apt-cache policy acl`: candidate 2.3.2-2+b1. Installing a package is outside the README: operator decision.
2. data_paths detection fails on this host: the game volumes are btrfs on LVM (st_dev 0:48, anonymous), so
   `plan` says "no block device behind it". Workaround that stays inside the documented config:
   `devices = ["/dev/disk/by-id/pci-0000:00:10.0"]` (the single virtio disk vda; LVM PV vda5 holds root ext4 and host-services btrfs).
3. With that explicit devices entry `plan` prints `size ? B  block-node=False` for the by-id symlink: unresolved (symlink not followed? or read as non-root?). Needs a look before enabling the tick.
4. Scratch dir /var/lib/iogov is on root ext4 (same vda): fine for the scratch-same-disk assumption.
5. Game-server cgroups (explicit): /sys/fs/cgroup/wings.slice/docker-<64hex>.scope, found via `docker inspect` of the two exact container names (CgroupParent wings.slice); both scopes exist on the host.
