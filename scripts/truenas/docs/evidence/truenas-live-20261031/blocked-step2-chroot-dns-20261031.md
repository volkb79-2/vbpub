<!-- origin: 2026-10-31; status: evidence; copied from the controller program dir 2026-11-10 -->
<!-- source: evidence/truenas-live-20261031/blocked-step2-chroot-dns-20261031.md -->
<!-- relation: blocked TrueNAS step-2 acceptance evidence; verbatim source text follows -->
<!-- Evidence scrub 2026-11-10: environment-specific home, checkout, worktree, and per-run temporary paths were normalized to neutral placeholders; command output and verdict text are otherwise unchanged. -->

# TrueNAS 20 GiB source rebuild — Step 2 blocked (2026-10-31)

## Completed locally

- Vanilla TrueNAS 27.0.0-RC.1 installer:
  TrueNAS-27.0.0-RC.1.iso, SHA-256
  515a0252e72f1a6c86d603fa4d1661333029fe9a4804d0a7a28d24fe4f63498e.
- Installed on a 20 GiB QEMU TCG disk through the managed TCG runner. The
  first three admission attempts were refused because memory PSI some avg10
  in /dev.slice/dev-gates.slice was 22.57%, 30.72%, and 38.47%, above the
  10% limit. A later attempt passed after pressure subsided. The admission
  check was not bypassed.
- First boot: boot-pool size 19 GiB, allocated 3.94 GiB, free 15.1 GiB.
  This satisfies the >=12 GiB free requirement.
- Step 1 completed: protected sidecar password applied, root Ed25519 SSH
  configured with SSH password authentication disabled, 30-day API key created
  and validated, and powered-off QCOW2 captured and compared sector-for-sector.
- Raw image: 21,474,836,480 bytes,
  SHA-256 721a8b22698f7be66d853c3719351849dacfc59271d023734a846615ebc6b1d7.
  The secrets sidecar mode is 0600.
- Debian Live 13.7.0 standard ISO detached signature and SHA-512 checksum
  verified before both build attempts.

## Step 2 failure

The documented builder was run twice with the same signed Debian base, raw
image, sidecar, and output path. Both commands exited 2 during its chroot
installation of sgdisk, efibootmgr, and eject. Both logs ended with:

    Temporary failure resolving 'deb.debian.org'
    E: Unable to fetch some archives, maybe run apt-get update or try with --fix-missing?

Attempt 1 log: step2-build-20g.log.
Attempt 2 log: step2-build-20g-retry1.log.

During diagnosis, host getent ahosts deb.debian.org returned
<host-address> and an HTTP HEAD request to
http://deb.debian.org/debian/dists/trixie/InRelease returned HTTP 200. The
failure was observed in the Debian chroot; its root cause remains unresolved.
No resolver or package-download shim was added.

## Stop point

No unattended restore ISO was produced. No local restore replay, v1001
provider operation, new provider snapshot, or 20 GiB acceptance run was
started. The existing provider base tn-v27rc1-stock-ssh-ready-20261027
remains unchanged. Resume with the documented Step 2 builder when its chroot
can resolve the Debian repository, then complete local replay before any
provider installation.

The source VM's initial ssh.update response exposed generated SSH host
private-key fields in the interactive output. Before Step 1, those stored host
keys were cleared and the VM rebooted; TrueNAS middleware regenerated fresh
host keys on boot and persisted them when SSH started. The replacement
ED25519 key fingerprint was
SHA256:R/D/E67H/YJHgJaYHikZzUS8vPEZv9Jux+6jLWRpBVs; the capture workflow's
strict known-hosts pin matched it. The exposed keys are not in the raw capture.
No screenshots were saved in the repository.

## Registered unit lane

The first run-gate invocation returned NOT_RUN because the worktree was dirty;
no tests ran in that attempt. The registered lane was then run once with the
supported --allow-dirty option. Result: PASS, 645 passed and 45 subtests passed
in 6.13 seconds, exit code 0. Run-Gate profile session:
s-20261031T013537Z-e238. Full log:
run-gate-unit-allow-dirty.log. Static git diff check also passed.

## Worktree delivery

Branch truenas-restore-iso contains commit
c1a81b8920bfd9324520671731ddaf103d7843be and was pushed to origin. The
commit contains only the explicit golden-image implementation, test, and
documentation paths. The operator's scripts/truenas/TODO.md edit and untracked
scripts/truenas/libeatmydata1_131-2+b1_amd64.deb remain uncommitted.
