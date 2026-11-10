<!-- origin: 2026-10-30; status: evidence; copied from the controller program dir 2026-11-10 -->
<!-- source: evidence/truenas-live-20261031/blocked-local-tcg-20261030.md -->
<!-- relation: blocked TrueNAS local-TCG acceptance evidence; verbatim source text follows -->

# TrueNAS 20 GiB base: local TCG preflight stopped

- Checked out worktree: `/workspaces/vbpub/.worktrees/truenas-restore-iso`
- Branch HEAD: `31d4bda4df04ff6598aae0ee4887e5d871bfcc8d`
- RC.1 installer SHA-256: `515a0252e72f1a6c86d603fa4d1661333029fe9a4804d0a7a28d24fe4f63498e`; the publisher's `.sha256` digest matched.
- Installer ISO is retained in the ignored `scripts/truenas/golden-iso/artifacts/` directory.

## Why VM creation stopped

No `debian-install-vm` image was present. The repository's `run-vm-harness.sh --daemon` exited 1 before building it, reporting:

```text
run-vm-harness: cgroup parent 'dev-gates.slice' is not an installed, live host slice; refusing to start the VM runner
```

The host cgroup inspector's D-Bus query subsequently reported `LoadState=loaded` for `dev-gates.slice`, `dev-background.slice`, and `dev-interactive.slice`; the harness check therefore does not recognize the loaded `dev-gates.slice` in this environment. No workaround to that refusal was applied.

A build through the already managed `mdt-managed` BuildKit builder, using the repository's `scripts/debian-install-v2/testing/vm/Dockerfile` and explicit image tag `debian-install-vm:acef5c6be782`, then failed while resolving the Docker Hub base image:

```text
error getting credentials - err: exit status 255
Dockerfile:28: FROM debian:trixie-slim
```

The base image could not be resolved, so the QEMU runner image could not be built. At the final check, the host inspector reported `dev-background.slice` PSI some/full avg10 as `0.0/0.0`; `dev-gates.slice` was `1.22/1.22`, above the TCG/gate full-pressure admission limit. These are recorded observations, not a VM launch attempt.

## Actions not taken

- No QEMU/TCG source VM was launched; installer console automation and the 20 GiB boot-pool size remain unverified.
- Step 1 and step 2 were not run; no golden raw image or restore ISO was produced.
- No provider command was run, `<controller-program-dir>/v1001.lock` was not acquired, and v1001 was not inspected or changed.
- No provider snapshot was created; no acceptance scenario or matrix cell ran.
- No test gate was run. The attempted script and BuildKit errors are retained in the neighboring text logs.

The temporary code edits were reverted. The only worktree changes left are the operator's pre-existing `scripts/truenas/TODO.md` edit and untracked `.deb`; neither was modified or staged.
