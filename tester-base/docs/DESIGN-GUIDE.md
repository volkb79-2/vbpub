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
  buildx and compose out.
- **T3 bootstrap from the repository alone.** The build needs only public upstream inputs: no
  estate artifact, no cmru. The consumers' `ARG TESTER_BASE` default and run-gate's
  `[environments.host].image` are `tester-base:local` until the first CMRU release; there is no
  other fallback (a missing base fails the build rather than substituting another image).
- **The user identity stays out.** tester-base creates no user; the uid-1003 identity and HOME/XDG
  live in `tester-unified` and `wheel-builder`, each with its own passwd entry, so a base consumer
  that needs a different identity is not fighting a baked one.

## Rejected alternatives
| Rejected | Why |
|---|---|
| Keep building FROM the cockpit | floating tag, shadowing CLIs, 8 GB |
| `python:3.14-slim` | drops gcc/g++/curl/openssh-client; the survey needs them, re-adding them reproduces the buildpack-deps layer by hand |
| `docker-buildx-plugin`, `docker-compose-plugin` | no gate uses them; buildx only appears in image-producing lanes, which run in the mdt-cockpit image |
| `crane`, `regctl` | needed only by the OCI-push test, which moved to the mdt-cockpit `oci-push` lane |
| `iproute2` (`tc`), `systemd-tmpfiles`, `fdisk`/`sfdisk` | tests that execute real low-level OS binaries run in the QEMU VM lane (`os_integration` marker), not in a container |
| baking a user | see above |

## One build, one image
A release that built twice - once for the `contract` lane, once for the push, each from floating
inputs (`apt-get update`, a registry index) - published something nobody had tested. The release is
now ONE build, and the version is applied late because cmru's order is prepare -> gate -> (VERSION
written, committed, tagged) -> build step -> push:

1. `prepare` builds `tester-base:local` once and `release_image.py record` stores its image id. Nothing
   versioned exists yet, so nothing here knows a version.
2. The `contract` lane tests `tester-base:local`.
3. The `build` step is the first one after cmru wrote `VERSION`: `release_image.py tag` reads it (it
   FAILS when the file is absent or not `x.y.z` - there is no default anywhere), re-checks that
   `tester-base:local` is still the recorded id, `docker tag`s that same image `:<VERSION>` and
   `:latest`, and re-verifies the ids after tagging.
4. `push` authenticates with the step's own credentials (`GITHUB_USERNAME` / `GITHUB_PUSH_PAT`, the PAT on
   stdin into an isolated docker config, never on argv, never from ambient credentials), refuses a
   version tag that already exists in the registry (only a definite "not found" counts as free; an
   auth or network error aborts), pushes `:latest` and then `:<VERSION>` LAST, and requires that the
   registry serves the digest that was pushed.

**Partial publish.** `:latest` is a moving alias; the immutable `:<VERSION>` is the commit point of a
release. If a push fails after `:latest` moved, `:latest` already points at the new, tested image and
`:<VERSION>` does not exist yet; nothing else changed, and re-running `push` is safe (same image, same
digests). A failure before `:latest` leaves the registry untouched.

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
