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
