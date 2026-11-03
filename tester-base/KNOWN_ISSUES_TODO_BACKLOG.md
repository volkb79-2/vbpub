# tester-base: known issues and backlog

## TB-01 - unpinned Debian `docker-compose` on top of tester-base: CLOSED 2026-10-19 (INT-01 D-006)

Closed by tester-unified installing Docker's own `docker-compose-plugin` at an exact version with a pinned
sha256 (never Debian's `docker-compose`); a test refuses an unpinned or Debian compose install. See
tester-unified/docs/DESIGN-GUIDE.md "Userland added on top of the base".

## TB-02 - the release graph has no tester-base gate-prerequisite edge (INT-01 A-12, 2026-10-19)

`cmru.orchestration.toml` lists `tester-base <- none` and nothing depends on it, although every gate image is
`FROM tester-base`. Harmless while the default is the local tag. When the defaults switch to the published
digest (tester-base/docs/CONSUMERS.md "After the first release"), the release order must put tester-base
before run-gate-project and cmru; add the edge with that switch.

Addendum 2026-10-27 (tester-stack review): the edge that matters is not between two releases but the manual
"rebuild tester-unified on the published Run-Gate" step between run-gate and its consumers, which the graph has no
node for (TB-09). The same switch would also have turned tester-base's own contract lane red: `tests/test_docs_in_sync.py`
recognised a consumer only by `=tester-base:local`; it now accepts the published digest form as well, so the switch
commit the runbook asks for can pass the lane.

## TB-03 - a dedicated cgroup-probe image would let tester-unified drop systemd (INT-01 D-004, 2026-10-19)

tester-unified keeps the real `systemctl` client (Debian's `systemd` package) because the cgroup-profiler
placement probe (`scripts/cgroup-profiler/tools/gate.sh`, `lib/access.py` `_PLACEMENT_PROBE`) runs
`systemctl show <slice>` inside the tester image against the host system bus. Decision: keep it for now. The
alternative is a small dedicated probe image carrying only the systemctl/busctl client, used by the
cgroup probes (gate.sh, access.py, shared-ramdisk cgroup-parent.sh), after which tester-unified could drop
`systemd` (and the `systemd-tmpfiles` it puts on PATH). Needs a decision on who builds and pins that image.

## TB-04 - the tester-base source hash must cover every build input (INT-01 A-20, 2026-10-19)

**Status: FIXED 2026-11-04 (09eb4f4bb).** `tester-base/tests/test_source_hash.py` fails on any COPY/ADD/`--mount`/`--build-context`/bake `contexts` while the hash producer hashes the Dockerfile alone; DESIGN-GUIDE "Source hash scope" records that unpinned apt content is not captured.

`org.vbpub.tester-base.source-hash` is the sha256 of `tester-base/Dockerfile`, justified as its "only build
input" (no COPY, ADD, mount or build context today). Nothing asserts that: a future `COPY`, a
`--mount=type=bind` or a `--build-context` would change the image without changing the hash, and the stale
local tag would be reused. Fix: a test that fails when the Dockerfile contains COPY, ADD, `--mount` or
`--build-context` unless the hash is widened to cover it (for example the sorted `git ls-tree -r HEAD
tester-base` of the context files). The hash is also only *source*-stale: unpinned apt content (minisign)
fetched at build time is not captured; the DESIGN-GUIDE wording should say so.

## TB-05 - verify the compose .deb pins against Docker's signed index (INT-01 A-21, 2026-10-19)

The per-architecture sha256s of the `docker-compose-plugin` 5.6.0 packages in `tester-unified/Dockerfile` are of
the files downloaded over TLS at pin time; they were not compared with anything signed, so a tampered first
download would be pinned. Cheap one-time fix at the next bump: compare each sum with the `SHA256:` field of the
`docker-compose-plugin` stanza in `https://download.docker.com/linux/debian/dists/trixie/stable/binary-<arch>/Packages`
(signed through InRelease) and record that in the Dockerfile comment; or, on tester-base (which keeps Docker's apt
source and key), install `docker-compose-plugin=<exact version>` with apt for signature verification and keep the
pinned `.deb` only for bases without that source. The cmru side of the freshness gap is KI-85.

## TB-06 - the compose .deb on the upstream bootstrap base is never built for real (INT-01 B-12, 2026-10-19)

Only the tester-base path was built (`tester-unified:int01`, `Docker Compose version v5.6.0`); the MDT and upstream
Python bases are covered by shim tests. The package's control data lists only `Recommends: docker-buildx-plugin`
(`dpkg-deb -f`: no `Depends`, so `dpkg -i` next to Debian's `docker-cli` should not fail), but one real build with
`--base-image mcr.microsoft.com/devcontainers/python:3.14-trixie` would confirm the documented zero-release path.

## TB-07 - build-image.sh classifies base references by the presence of a slash (INT-01 A-23, 2026-10-19)

**Status: FIXED 2026-11-04 (88c851870).** A registry host component (dot, colon or `localhost` in the first path component) decides pulling; Docker Hub references need `--pull-base`; a bare local tag is never pulled.

A Docker Hub library reference without a slash (`python:3.14-trixie`) is treated as registry-less and refused rather than
pulled; a local-only tag that contains a slash (`myorg/base:local`) would be pulled from Docker Hub if absent. Both fail
closed or pull an explicitly named input. Better: classify by a registry host component, or take an explicit
`--pull-base` flag. Owner: `tester-unified/build-image.sh`.

## TB-08 - wheel-builder's run-time build-backend installs are outside the 14-day window (tester-stack review, 2026-10-27)

**Status: FIXED 2026-11-04 (88c851870), operator decision D3: the window applies to build backends.** `ENV PIP_UPLOADED_PRIOR_TO=${PIP_MIN_RELEASE_AGE}` in `wheel-builder/Dockerfile`; a young backend pin fails the wheel build until it ages. Per-build override: `CMRU_WHEEL_BUILD_AGE_OVERRIDE_REASON="<reason>"` (cmru `cmd_wheel_build` passes `-e PIP_UPLOADED_PRIOR_TO=P0D` and logs the reason; blank is refused). The first-party guard test now also asserts no `[build-system].requires` names a first-party package. Operator note: the exact pins named below (setuptools 84.0.0, wheel 0.47.0, setuptools_scm 10.0.5) must be at least 14 days old at the next wheel build, or the build fails.

`wheel-builder/Dockerfile` ages the two installs baked into the image (`pip`, `build`). The backends that
`python -m build` installs into its isolated environment at every `cmru handler wheel-build` run (`[build-system].requires`)
come from the index at run time with no `--uploaded-prior-to`, and two projects declare floors rather than pins
(pwmcp-client `setuptools>=77`, cgroup-profiler `setuptools>=68`), so a release wheel can be built by a backend released
an hour earlier. Fix (one line plus a test): `ENV PIP_UPLOADED_PRIOR_TO=${PIP_MIN_RELEASE_AGE}` after the `ARG` in
`wheel-builder/Dockerfile`; pip reads `PIP_*` for every invocation, including the one `build` runs for its isolated
environment, and the base's pip (26.2.1, bundled by ensurepip as well) accepts the option. Decision needed before
enabling: an exact backend pin younger than 14 days (setuptools 84.0.0 in assay and run-gate-project, 82.0.1 elsewhere,
wheel 0.47.0, setuptools_scm 10.0.5) then fails a release wheel build, which is the rule applied but new on the
release path; check those upload dates first. The env must not catch a first-party name (operator decision
2026-10-27: no age window for our own tools): today no `[build-system].requires` names one and `cmd_wheel_build`
installs nothing first-party from an index, and `test_the_age_window_never_applies_to_a_first_party_package`
should be extended to the env if it is adopted. Owner: `wheel-builder/Dockerfile`, cmru `cmd_wheel_build`.

## TB-09 - nothing enforces "rebuild tester-unified after a Run-Gate release" for the non-cmru consumers (2026-10-27)

Only cmru's release gate (`cmru/tools/run_release_gate.py::_check_tester_image_identity`) reads
`org.vbpub.run-gate.version` / `.wheel-sha256`; no code in `run-gate-project/src`, `ciu/`, `assay/` or the launchers does.
ciu's preflight (`ciu/scripts/check_run_gate_wheel.py`) checks the opposite direction (installed modules equal the
checkout), so a published-wheel image gates ciu only while `run-gate-project/` is at the released commit. The release
graph (`cmru.orchestration.toml`) has no node for the rebuild; `tester-unified/docs/CONSUMERS.md` ("Where the rebuild
sits in the release order") is its only record. Proposals: (a) run-gate's `tester-unified` environment compares the
selected image's `org.vbpub.run-gate.version` label with its own `--version` and refuses a mismatch unless an explicit
override names the reason; (b) `cmru release` of every project runs the same label check cmru's own gate runs; (c) a
`rebuild-tester-unified` entry in the orchestration order between run-gate-project and its dependents. Supersedes the
release-order half of TB-02.

## TB-10 - rollback tags collide and promotion goes by mutable tag (2026-10-27)

**Status: FIXED 2026-11-04 (88c851870).** `tester-unified/promote-image.sh` promotes by immutable id, keeps `<image>:prev-<date>`, refuses to overwrite an existing `prev-*` tag and records the replaced/promoted ids under `.assay/promotions/`. It is a separate script, not a `build-image.sh --promote` flag.

The runbook keeps the rollback as `tester-unified:prev-<date>`; a second promotion on the same day overwrites it
silently, and the previous `wheel-builder:local` was not kept at all (the runbook now says to keep both and to check the
prev tag is free). Better: a `build-image.sh --promote <candidate>` step that records the replaced image ids in a small
file under `.assay/` and refuses to overwrite an existing `prev-*` tag; gates should keep selecting the candidate by
immutable id (`RUN_GATE_TESTER_UNIFIED_IMAGE=$(docker image inspect --format '{{.Id}}' ...)`), as the README already
does and the runbook now does.

## TB-11 - tester-unified inherits tester-base's identity labels (2026-10-27)

**Status: FIXED 2026-11-04 (88c851870).** Both consumer Dockerfiles reset `org.vbpub.tester-base.kind` and `.source-hash` to empty; `cmru/build-initial-standalone.sh` refuses a `tester-base:local` carrying `org.vbpub.tester-unified.base-image`; the runbook's base build passes `TESTER_BASE_SOURCE_HASH` or points at the bootstrap script.

OCI labels are inherited: `tester-unified:wave-20261027` carries `org.vbpub.tester-base.kind=tester-base` and
`org.vbpub.tester-base.source-hash=""` (its base was a plain `docker buildx build` of `tester-base/`, which leaves the
hash empty; `cmru/build-initial-standalone.sh` reads that as stale and rebuilds). Consequences: a tester-unified image
mistakenly tagged `tester-base:local` passes the bootstrap script's kind check, and a base built by the runbook's own
step 3 command is always "stale" to the bootstrap script. Fixes: tester-unified/wheel-builder reset the inherited
`org.vbpub.tester-base.*` labels to empty (or set their own `.kind`), the bootstrap script also requires
`org.vbpub.tester-unified.base-image` to be absent, and the runbook's base build passes `TESTER_BASE_SOURCE_HASH`
the way the bootstrap script does (or the runbook points at the bootstrap script for the base).

## TB-12 - fetch-run-gate-wheel.py: no response size cap, no retry, and the builder trusts the staged file (2026-10-27)

**Status: FIXED 2026-11-04 (88c851870).** Reads capped at 64 MiB (`ResponseTooLarge`, never retried), three attempts with 1 s / 2 s backoff for transient `OSError` (HTTP 4xx not retried), the fetcher stages `<wheel>.sha256` and `build-image.sh --run-gate-published` refuses a missing or differing digest.

`_urlopen_bytes` reads the whole body into memory before hashing (a misbehaving redirect target on an allowlisted host
could stream gigabytes), uses one 60 s timeout per request and never retries; a transient failure is a clean exit 2
and a rerun. `build-image.sh` re-hashes the staged wheel through `run-gate-wheel-version.py` but never compares that
digest with the one the fetcher verified against pointer and sidecar; the 0700 `mktemp -d` directory is the only barrier
between the two steps. Cheap tightening: the fetcher writes `<wheel>.sha256` beside the wheel and the builder asserts
equality; cap reads at 64 MiB. Low: same-uid write access to the staging directory is the precondition.

## TB-13 - tester-unified becomes uid-agnostic: read-only `/opt/tester-venv`, no baked uid 1003 (lane-uid phase 2) (2026-11-04)

**Problem.** `tester-unified/Dockerfile:405-423` creates `tester:1003:1003`, sets `HOME=/home/tester`, `USER 1003` and makes `/opt/tester-venv`
owned by 1003. The reasons are `pwd.getpwuid` (topos `daemon/deploy.py:311,329`, `procs/sampler.py:316`, `actions/execute.py:122`),
`getpass.getuser` (nyxloom `control_auth.py:218`), a writable HOME and doctrine (`nyxloom/reference/DOCTRINE.md:28-37`). The uid is asserted in
`tester-unified/run:259,305` and `assay/tools/self-qualification-container.sh:601`. Every host must use uid 1003 or lanes fail (RG-150, NC-04,
MDT-052).

**Decision record (operator, 2026-11-04, binding).** Lanes run as the checkout owner with a generated read-only passwd/group overlay
(run-gate RG-151), a per-lane HOME and a read-only tester venv; the baked uid 1003 rule retires. This entry is phase 2 of four.

**Design.**

- The image no longer requires uid 1003. It keeps a `tester` entry only as a default for plain `docker run` use, and nothing in the image
  assumes that uid; `/opt/tester-venv` is root-owned and read-only by contract (mode 0755/0644, no group write).
- Audit and move every write into the venv: each `pip install` or `--editable` in gate scripts (assay editable install, run-gate wheel,
  cmru, topos, nyxloom gate scripts) goes to a per-lane prefix: `PIP_PREFIX`/`PYTHONUSERBASE` under `$HOME` (the per-lane HOME), or a lane
  venv created with `--system-site-packages`. The audit result (a list of every writer) is recorded in `tester-unified/docs/DESIGN-GUIDE.md`.
- `tester-unified/run` and the self-qualification script assert `== owner` (the lane's resolved uid) instead of `== 1003`.
- Lanes that use Docker derive the socket gid by `stat` of the socket they mount (`tester-unified/run:125-233` already does; keep it as the
  reference pattern and add the explicit non-mdt case below).
- One CONSUMERS statement of the lane contract: user is the checkout owner, `HOME` is per-lane and writable, `/opt/tester-venv` is read-only,
  installs go to the lane prefix.

**Acceptance criteria.**

- A lane run with `--user <arbitrary uid>` plus the overlay passes the contract lanes of tester-unified; a write to `/opt/tester-venv`
  fails (read-only) and no gate script attempts one.
- The image history shows no `chown 1003` and `USER 1003` any more; a test greps the Dockerfile and scripts.
- `run` and the self-qualification script pass on a host where the owner uid is not 1003.
- **Non-mdt path (operator criterion, 2026-11-04).** `tester-unified/run` on a plain Debian host or another devcontainer: the docker gid comes from
  `stat` of the socket actually mounted (`DOCKER_HOST` or `/var/run/docker.sock`), the user needs only that environment's normal docker access,
  and no test or script mentions `mdt-docker`/`mdt` groups as a precondition. Add a test with a fake socket of an arbitrary gid.

**Test plan.** Dockerfile static tests; a tester-lane contract run under a foreign uid; the pip-writer audit as a test that runs the
gate scripts' install commands with a read-only venv.

**Dependencies and order.** After run-gate RG-151 (the overlay exists). Phase 3 (assay B211, cmru KI-114) runs in parallel afterwards, then
RG-153. Folds into the single image change with TB-14's base digest if both land in the same wave.

## TB-14 - tester-base exports an OCI layout; tester-unified and wheel-builder consume it by digest (closes TB-04 and TB-11 in design) (2026-11-04)

**Problem.** Local tester bases are identified by a mutable tag (`tester-base:local`, TB-11) and the derived builds bypass the governed
builder (docker driver) because the remote `mdt-managed` builder cannot see dockerd's store: `cmru/build-initial-standalone.sh:312,320`,
`tester-unified/build-image.sh` with a local base (`docs/CONSUMERS.md:13,23`), `wheel-builder/Dockerfile:35`. Bypass loses the slice, cache and GC
and the base is never part of the derived image's source hash (TB-04).

**Decision record (operator, 2026-11-04).** `mdt-managed` stays the only builder; local bases travel as OCI layouts and named build contexts
pinned by digest: `--build-context <ref>=oci-layout://<dir>@sha256:<digest>`. The bypass remains only for the fresh-host bootstrap
(`--builder default`, never by environment). Platform memo section 1 (option A). Owner mdt-cockpit entry: MDT-056.

**Design.**

- tester-base build exports `--output type=oci,dest=<project>/artifacts/oci/tester-base,tar=false` (gitignored under `artifacts/`), plus `--load`
  only when the image is `docker run`; the layout's index digest is printed and written next to it.
- `build-image.sh` and wheel-builder take `--base-oci <dir>` and map `tester-base:local` to `oci-layout://<dir>@sha256:<digest>`; the base
  digest enters the derived image's source hash and its labels (closing TB-04) and replaces inheritance of the base's identity labels (TB-11:
  reset `org.vbpub.tester-base.*` in the derived image).
- tester-unified itself keeps `--load`, because lanes `docker run` it.
- Pilot check first: a context key with a tag (`tester-base:local`) must map a tagged `FROM tester-base:local`.
- srdm (`go-base.sh`, gate, canary) and cmru (`build_contexts`, KI-113) follow the same pattern in their own entries.

**Acceptance criteria.**

- The tester chain builds with no docker-driver bypass; the BuildKit slice accounts the work; second build hits the cache.
- Changing the base changes the derived source hash; a retagged wrong image cannot masquerade as the base (digest mismatch refuses).
- **Non-mdt path.** The same scripts build on a plain host with its ordinary builder (any buildx builder accepting named contexts) or classic
  docker; a test checks argument construction without mdt.

**Test plan.** Script argument tests, label tests, one live pilot comparing wall time and slice accounting against the bypass.

**Dependencies and order.** Package 1 of MDT-056 (tester-chain pilot), then wheel-builder, then srdm and cmru.

