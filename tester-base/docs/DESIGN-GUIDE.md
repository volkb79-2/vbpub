# tester-base — design guide (WHY)

## Why a dedicated base
`tester-unified` was built `FROM` the MDT developer cockpit image (8 GB; tester-unified ended at
9.6 GB) of which it used Python 3.14, git and the docker client. Two properties made that a
liability, not just a size problem:

1. **The cockpit's floating `latest` tag changed the gate environment without a commit** and
   invalidated every tester layer on each cockpit release.
2. **The cockpit bakes the release CLIs (`ciu`, `cmru`, `nyxloom`, `topos`) readable on PATH.**
   A gate runs the code under test; an installed release of the same tool on PATH can shadow it.

`tester-base` keeps the shared bottom layers (`python:3.14-trixie`) and nothing else.

## Decisions
- **T1 base.** `python:3.14-trixie`, not `-slim` (gcc/g++ must stay: `ruptures` has no cp314 wheel).
  Pinned by the INDEX digest. Freshness rule: the digest must be at least 14 days old when adopted;
  the tag, digest, created date and how they were read are recorded in the Dockerfile header.
  (The AI-CLI tools in the cockpit follow a different policy, user-updatable latest; that does not
  apply to a gate base, where an unvetted fresh base is exactly the risk the buffer is for.)
- **T2 contents.** Only `docker-ce-cli`, `minisign` and `shellcheck` are added. The docker apt
  repository key is accepted only by fingerprint; shellcheck by the sha256 of the upstream asset
  (version = what the MDT image shipped when the base was cut). `--no-install-recommends` keeps
  buildx and compose out. Consumer images add their own userland on top; that does not change this
  base or its `contract` lane (see "Consumer additions" below).
- **T3 bootstrap from the repository alone.** The build needs only public upstream inputs: no
  estate artifact, no cmru. The consumers' base ARGs (`TESTER_UNIFIED_BASE_IMAGE` in tester-unified, `WHEEL_BUILDER_BASE_IMAGE` in
  wheel-builder) and run-gate's `[environments.host].image` are `tester-base:local` until the first CMRU
  release; there is no
  other fallback (a missing base fails the build rather than substituting another image).
- **The user identity stays out.** tester-base creates no user; the uid-1003 identity and HOME/XDG
  live in `tester-unified` and `wheel-builder`, each with its own passwd entry, so a base consumer
  that needs a different identity is not fighting a baked one.

## Rejected alternatives
| Rejected | Why |
|---|---|
| Keep building FROM the cockpit | floating tag, shadowing CLIs, 8 GB |
| `python:3.14-slim` | drops gcc/g++/curl/openssh-client; the survey needs them, re-adding them reproduces the buildpack-deps layer by hand |
| `docker-buildx-plugin`, `docker-compose-plugin` | no gate lane of tester-base uses them; buildx only appears in image-producing lanes, which run in the mdt-cockpit image. (tester-unified still installs Debian's `docker-compose` by capability, see below.) |
| `crane`, `regctl` | needed only by the OCI-push test, which moved to the mdt-cockpit `oci-push` lane |
| `iproute2` (`tc`), `systemd-tmpfiles`, `fdisk`/`sfdisk` | tests that execute real low-level OS binaries run in the QEMU VM lane (`os_integration` marker), not in a container. tester-unified's `systemd` package (below) does put `systemd-tmpfiles` on its PATH; the one test that skips without it, `test_lt_mem.py`, is `os_integration`-marked and every lane that runs that suite deselects the marker, so nothing changes silently |
| baking a user | see above |

## Consumer additions (INT-01)
The `contract` lane describes tester-base only. The consumer layer `tester-unified` adds, in its own
Dockerfile and with its own tests: `zsh` and Node.js (MDTC-02 skip-diff), the **real `systemctl` client**
(Debian's `systemd` package; D-001 still forbids a stub) and Debian `docker-compose` (installed because
tester-unified's capability check requires `docker compose version` and T2 keeps the plugin out of the
base). The `systemctl` client exists for the cgroup-profiler placement probe
(`scripts/cgroup-profiler/tools/gate.sh`, `lib/access.py` `_PLACEMENT_PROBE`), which runs
`systemctl show <slice>` inside the image against the HOST system bus mounted read-only: with the bus
socket and `/run/systemd/system` present it answers for the host; in a plain container it exits 1
(`Failed to connect to system scope bus`, or `System has not been booted with systemd` when
`/run/systemd/system` is absent). No systemd runs in the container. See tester-unified's DESIGN-GUIDE.

## One build, one image
A release that built twice - once for the `contract` lane, once for the push, each from floating
inputs (`apt-get update`, a registry index) - published something nobody had tested. The release is
now ONE build, and the version is applied late because cmru's order is prepare -> gate -> (VERSION
written, committed, tagged) -> build step -> push:

1. `prepare` runs `release_image.py build`: it builds the image once under a PRIVATE per-release tag
   `tester-base:release-<source-commit>` and records the tag and image id. `tester-base:local` is the
   developer bootstrap tag that other sessions rebuild, so a release never tests or pushes it (a record
   naming it is refused). Nothing versioned exists yet, so nothing here knows a version.
2. `run-tests` runs `release_image.py gate`: the `contract` lane against the recorded private tag
   (`TESTER_BASE_IMAGE` is derived from the record, never typed).
3. The `build` step is the first one after cmru wrote `VERSION`: `release_image.py tag` reads it (it
   FAILS when the file is absent or not `x.y.z` - there is no default anywhere), re-checks that the
   private tag is still the recorded id, `docker tag`s that same image `:<VERSION>` and `:latest`, and
   re-verifies the ids after tagging.
4. `push` authenticates with the step's own credentials (`GITHUB_USERNAME` / `GITHUB_PUSH_PAT`, the PAT on
   stdin into an isolated docker config, never on argv, never from ambient credentials), pushes `:latest`
   and then `:<VERSION>` LAST, and requires that the registry serves the digest that was pushed. A version
   tag is never overwritten: only the registry's own "does not exist" answer (`manifest unknown`,
   `MANIFEST_UNKNOWN`, `NAME_UNKNOWN`, an explicit 404) means the tag is free - a bare "not found" would also
   match `executable file not found in $PATH`, so any other failure aborts. If the version tag already
   exists and serves THIS image (same image id, compared through the manifest or index), the release is
   treated as already published: the push step logs it and runs only the final digest checks, so a re-run
   after a partial publish completes instead of refusing forever; a tag that serves another image stops
   the release before anything moves.

**Partial publish.** `:latest` is a moving alias; the immutable `:<VERSION>` is the commit point of a
release. If a push fails after `:latest` moved, `:latest` already points at the new, tested image and
`:<VERSION>` does not exist yet; nothing else changed, and re-running `push` is safe (same image, same
digests). A failure before `:latest` leaves the registry untouched.

**Re-run after a partial publish, and when the re-run refuses.** The "already published" decision compares the
local `docker image inspect .Id` with the config digest the registry serves for the version tag. On a
classic image store these are the same value. On a containerd image store `.Id` can be the manifest or
index digest instead, so a re-run finds them different and REFUSES (fail-closed: nothing is pushed over the
version tag). The same refusal happens when the version tag and `:latest` were published wrapped differently
(one as an image manifest, the other as an index with attestations) and the first wrapper cannot be followed
to a linux/amd64 config. Recovery: inspect what the registry serves
(`docker buildx imagetools inspect ghcr.io/volkb79-2/tester-base:<VERSION> --raw`), and if its image config
is the tested image, finish by hand with the same isolated-config login and `docker push` of `:latest`; if it
is another image, the version number is spent: bump `VERSION` and release again.

**Private candidate tags are left behind.** `tester-base:release-<source-commit>` is created by `prepare`
and kept until the release is done; an abandoned retry leaves its tag (and the image) on that machine.
Remove them by exact tag; the cleanup command is in CONSUMERS.

`docker-ce-cli` is pinned to an exact recorded version for the same reason. The cmru handlers
`oci-image-build` / `oci-image-push` are not used; see `mdt-cockpit/handoff/CMRU-GAPS-2026-10-19.md`.

## Version pins
- **Base digest.** `python:3.14-trixie@sha256:...` in the Dockerfile `FROM` is the digest
  `cmru versions resolve tester-base` records for target `oci.python.314.trixie` under the estate's
  14-day age window, and the node stage's digest in `tester-unified/Dockerfile` likewise for
  `oci.node.26.trixie`; `tests/test_pins.py` asserts that every Dockerfile digest literal equals its
  record in `versions/oci-images.json`, so a hand-edited digest cannot drift from what cmru vetted. Both
  targets live in this project's `cmru.toml` because tester-base is the image family's only cmru project
  (tester-unified has no cmru project of its own to own them).
- **Why rolling targets on literal patch tags.** Measured with the installed cmru (6.1.1): a rolling
  target on the family tag (`3.14-trixie`) is refused while that tag's current digest is younger than the
  window and cannot pick an older digest; a semver target (`{version}-trixie`) picks the newest old-enough
  patch tag but its record carries no digest, and for `docker.io/library/node` it fails ("listed more than
  5000 tags"); both enumerate tags against Docker Hub's 100-per-hour anonymous limit. A rolling target on
  a *literal* patch tag (`3.14.7-trixie`, `26.9.0-trixie`) is the shape that records the digest, enforces the
  age window on the chosen tag and needs one manifest request. Choosing the tag is therefore a reviewed edit
  (bump the tag in `cmru.toml` and the Dockerfile together; `cmru versions resolve tester-base` then
  refuses a tag younger than 14 days), and the maintainer picks the newest patch tag at or past the window
  from Docker Hub's tag list. The remaining gap is reported as cmru backlog.

## What the skip-diff found (MDTC-02 T8) and where each answer lives
Running every tester-unified lane on the old image (built from the cockpit) and on one built from
this base showed which implicit dependencies the cockpit had been supplying. Each is now explicit,
and none of them belongs in the minimal base:

| Found | Answer | Why not in tester-base |
|---|---|---|
| topos shelled out to `systemctl` unguarded (49 `topos-suite` failures without it) | **fixed in topos**: the runner reports "systemctl is not available" (status 127) and the collector degrades; test seen RED. All 49 were collector/CLI tests on fixture roots (class a); none needs real systemd (class b). No stub anywhere. A VM lane for the real-systemd/cgroup/DAMON side is proposed in topos' backlog (`TP-VMLANE`), not built | a fake systemd would hide the product bug |
| `test_path_hook.py` dropped its zsh cases at *collection* when zsh was absent (42 -> 32 tests, silently) | the cases are always collected; a missing shell skips them on a developer machine and FAILS them under `MDT_REQUIRE_SHELLS=1` (the smoke lane); `zsh` installed in **tester-unified** | the base carries no shells beyond the python image's |
| two pwmcp runtime-gateway tests skip without `node` | node, npm and npx copied from the digest-pinned official `node:26.9.0-trixie` image (node 26 no longer ships corepack) into **tester-unified** (Debian's nodejs is too old) | not every gate needs a JavaScript runtime |
| tester-base runs as root with no identity; run-gate's `host` environment bind-mounts the worktree, so `py_compile` there left root-owned `__pycache__` that uid-1003 lanes could not write | no lane uses `host` any more (the two that did, topos `py-compile` and plesk `smoke`, run in tester-unified, which has the identity); a test forbids new `host` users. The `host` environment itself stays declared as tester-base. run-gate has no per-environment `user` key (only `RUN_GATE_HOST_IMAGE` derives `--user` from the invoker): see D-007 in the MDTC-02 report | a base that creates no user cannot also give the run-uid an identity (estate rule) |
| the OCI-push and bake-file tests need buildx/crane/regctl | moved to the mdt-cockpit `oci-push` lane (T6), required there; the smoke lane deselects the bake test explicitly | T2 rejects buildx/crane/regctl |
| tests that execute real tc/systemd-tmpfiles/sfdisk/nsenter | `os_integration` marker (T7) | T2 rejects those binaries |

## Container-versus-VM placement rule
A test that needs a real low-level OS binary (tc, systemd-tmpfiles, sfdisk, losetup, nsenter) is
marked `os_integration` and runs only where that OS exists for real: the owning project's QEMU VM
lane. Gate containers deselect it explicitly. Docker-driving tests (SQL qualification, tester-gate
DinD, ciu enroll) are not low-level OS tests; they need CPU the TCG VM cannot give and stay in
containers with the docker client from this base.

## Verification
The `contract` lane runs the candidate image and asserts the positive list (python 3.14 at
`/usr/local/bin/python3.14`, docker, minisign, shellcheck = pinned version, git 2.47, gcc/g++,
`LANG=C.UTF-8`) and the negative list above, so adding a tool to the base is a visible change of
contract rather than a convenience.
