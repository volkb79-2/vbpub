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

## What the skip-diff found (MDTC-02 T8) and where each answer lives
Running every tester-unified lane on the old image (built from the cockpit) and on one built from
this base showed which implicit dependencies the cockpit had been supplying. Each is now explicit,
and none of them belongs in the minimal base:

| Found | Answer | Why not in tester-base |
|---|---|---|
| topos shells out to `systemctl` unguarded (49 `topos-suite` failures without it) | a harmless `systemctl` stub in **tester-unified**; product bug filed in topos' backlog (`TP-SYSTEMCTL`), stub to be deleted with it | a fake systemd is a test accommodation, not base userland (T2: no systemctl shim) |
| `test_path_hook.py` drops its zsh cases at *collection* when zsh is absent (42 -> 32 tests, silently) | `zsh` installed in **tester-unified** | the base carries no shells beyond the python image's |
| tester-base runs as root; run-gate's `host` environment bind-mounts the worktree, so `py_compile` left root-owned `__pycache__` that uid-1003 lanes could not write | `PYTHONPYCACHEPREFIX=/tmp/pycache` in **tester-base**; tester-unified (a real user) resets it to empty | a base that runs as root must not write into a bind-mounted tree; this is the base's own property |
| the OCI-push and bake-file tests need buildx/crane/regctl | moved to the mdt-cockpit `oci-push` lane (T6), required there | T2 rejects buildx/crane/regctl |
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
