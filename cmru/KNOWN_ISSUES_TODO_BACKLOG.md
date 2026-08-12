# CMRU — Known Issues, TODO & Backlog

> **This is the canonical CMRU issue tracker.** File CMRU bugs and enhancements **here**, in
> the CMRU product repo — not in consumer repos. Consumers (e.g. dstdns) that discover a CMRU
> gap while building/operating a stack should report it here and keep only a pointer on their
> side. Each issue is fixed in this repo with **code + tests + spec + docs** in lockstep.
>
> Normative behaviour is defined in [`docs/SPEC.md`](docs/SPEC.md) (`S-xx` IDs). When an issue
> changes behaviour, the SPEC change is part of the fix, and the SPEC ID is cited below.

---

## Landed

### FIX-01 — Containerized wheel build lost git history in a release worktree — *shipped*
**Status:** landed (code + tests + spec in lockstep). **Root cause of the original
`ciu-v2.0.0`/`cmru-v0.2.0` orphan-tag mystery** (see git history around 2026-07-28/29) —
superseding the earlier "concurrent stale process" theory, which was wrong.
**SPEC:** `S9.3a`.
**Why:** `cmd_wheel_build` (`cmru/src/cmru/handlers.py`), when `CMRU_WHEEL_BUILDER_IMAGE`
is set, bind-mounted only the project's worktree subtree into the builder container. A
release worktree's `.git` is a *file* pointing to an absolute path OUTSIDE that subtree
(`gitdir: <repo_root>/.git/worktrees/<name>`) — reproduced directly:
`git rev-parse --show-toplevel` inside such a container fails with `fatal: not a git
repository ... Stopping at filesystem boundary`. `setuptools_scm` does not error on
this — it silently falls back to `pyproject.toml`'s `fallback_version` (`"2.0.0"` for
ciu, `"0.2.0"` for cmru — exactly the orphan tag versions found earlier), and that wrong,
static version gets baked into the wheel and published as if it were real.
**Fix:** `_wheel_builder_git_mount_args()` additionally bind-mounts the checkout's real
git common directory (`git rev-parse --git-common-dir`, translated to its host path the
same way the existing subtree mount already is) at its own absolute path inside the
container — a no-op for an ordinary non-worktree checkout, where that directory is
already covered by the existing mount. Verified empirically: `git describe --tags`
inside the container went from failing closed-ish (silent `fallback_version`) to
correctly reporting `ciu-v4.9.0-156-gb57a4fc1`.

### FEAT-02 — mdt `load` flow: single-build, digest-verified OCI publish — *shipped*
**Status:** landed (code + tests + spec doc in lockstep). Delegated script only —
does **not** enable the built-in OCI handler; see `KI-02`.
**Why:** `RELEASE_IMAGE_FLOW=load` (mdt's default) built the image privately once
(`--load`) to extract the manifest, then built it **again, independently**
(`registry_bake()`, `type=registry`) at push time. Nothing compared the two, so the
manifest committed to `package-manifests-versioned/` documented a different build
than what actually reached GHCR — a silent build-on-push fallback, exactly what
`S14.3.6` forbids for the (still-unbuilt) built-in handler, just not yet enforced
for this delegated script.
**Fix:** `oci_layout_bake()` (`modern-debian-tools-python-debug/scripts/release-bake.sh`)
builds once to a local OCI layout (`type=oci,dest=...`) per bake target.
`extract_manifests()` reads the manifest straight out of that layout via
`regctl image get-file ocidir://<dir>:<tag> <path>` — no daemon load, no second
build. `_push_oci_layouts()` (`build-push.py`) pushes each target's layout directly
via `crane push <dir> <tag>` and asserts the registry-reported digest
(`crane digest`) equals the pre-push local digest (`regctl manifest digest
ocidir://...`); a mismatch fails the release closed instead of publishing silently.
Validated end-to-end against a real local `registry:2` container (not mocked) in
`modern-debian-tools-python-debug/scripts/test_oci_layout_push.py`. The download-level
artifact cache (`stage_tool_artifacts.py`) is unaffected — it already never caches a
`/latest` URL and always re-resolves "latest" live before deciding whether to reuse a
version-pinned download; this change only removes the redundant *second BuildKit
invocation*, it doesn't touch that cache.
**Resolved gap:** `test_oci_layout_push.py` is now wired into mdt's `run-tests` gate
(`cmru.toml`) and runs for real there. `cmru/src/cmru/tester_gate.py`'s
`tester-gate --enable-docker` gives that gate container its own ephemeral, fully
isolated nested Docker daemon (`dind_sidecar()` — a `docker:dind` sidecar,
`--privileged`, polled for readiness via `docker exec <sidecar> docker version`
before use) rather than the HOST's real daemon: the gate container attaches to the
sidecar's network namespace (`--network container:<sidecar>`) and points its Docker
CLI at it (`DOCKER_HOST=tcp://localhost:2375`). Chosen over the simpler host-socket
bind-mount alternative specifically to avoid giving a sandboxed test gate
root-equivalent host access — everything the gate does lives inside the disposable
sidecar and disappears when it stops (`docker stop`, `--rm`). Deliberately opt-in per
project step, not a default: only mdt's `run-tests` step passes it — every other
project's gate is unaffected. `tester-unified`'s image ships
`docker`/`buildx`/`crane`/`regctl`/`jq` (inherited from its
`modern-debian-tools-python-debug-vsc-devcontainer` base image); the sidecar image
(`docker:dind`) supplies `dockerd`/`containerd`/`runc` itself — none of that is in
`tester-unified`'s own image, nor needs to be.

Validated end-to-end, including registry networking (the nested daemon's own bridge
network — `test_oci_layout_push.py`'s `registry:2` fixture is reachable at plain
`127.0.0.1:<published-port>` from the gate container, no gateway-IP fallback needed,
since it shares netns with the sidecar) and `docker buildx create --driver
docker-container` + `--output type=oci` working inside the nested daemon. Also
rebuilt `tester-unified:local` (a stale local cache — built before crane/regctl
landed in the base image — had neither; `docker build -f tester-unified/Dockerfile
-t tester-unified:local .` from repo root picks up the current base), then ran all
30 mdt gate tests for real, in-container, through the sidecar.

### FEAT-01 — Multi-variant `bundle`/`tarball` publish + variant-selecting installer — *shipped*
**Status:** landed (code + tests + spec in lockstep).
**SPEC:** `S-REL.6`, `S1.6`, `S2` (`[[project.<name>.variants]]`), `S5.3` (latest.json
`variants`), `S6.12` (installer `--variant`), `V22`.
**Why:** an artifact that carries version-locked C-extension wheels can't serve two host
interpreters from one archive (e.g. netcup-api-filter's `py39` vs `py311`). One release tag
now publishes one asset per named variant (`<tag>-<variant>.tar.xz` + `.sha256`, and for
`bundle` a per-variant `manifest.json` + `.minisig`), recorded in `latest.json`. The operator
selects one explicitly at install time (`get.py --variant NAME`) — the webhoster has no
interpreter to auto-detect, so there is no silent default; the choice is remembered for
`update`. Zero declared variants ⇒ the single-asset path is unchanged (a separate keystone,
`publish_versioned_variants`, leaves `publish_versioned` byte-for-byte intact).
**Resolution** is by `(tag, variant)`: `find_artifact(..., variant=, suffix=)` narrows a
multi-variant `dist/` to one file, so the old ">1 match" guard no longer fires spuriously.

---

## Known Issues

### KI-03 — S2's single strict configuration contract is not used by `release` — *open*
**Status:** specification/code mismatch; do not rely on `cmru get` validation as proof that a
release will parse identically. **SPEC:** `S2.1`, `S2.3`, `V09`.
**Evidence:** `cmru/src/cmru/config.py` is explicitly the strict reader used by `cmru get`,
while `cmru/src/cmru/cli.py:load_config()` is the release/status reader and deliberately
lenient. It accepts legacy `[projects]`, `github.username`, and `[registry]` forms (covered by
`tests/test_cli_dispatch.py::test_load_config_legacy_keys_still_accepted`) and does not
reject unknown top-level/project/release keys. Therefore a misspelled release key can be
silently ignored even though S2.3 says unknown keys MUST be rejected on startup.
**Decision required:** either make the release path use one strict shared schema (and publish a
bounded migration/removal policy for legacy configs), or relax S2.1/S2.3 to specify the two
parsers and their intentionally different guarantees. Do not silently tighten the parser: it
would be a potentially breaking config migration.

### KI-04 — S7 delegated-tool configuration is unreachable and has incompatible shapes — *open*
**Status:** specification/code mismatch; `delegated` config is not a working release feature.
**SPEC:** `S7.1`–`S7.4`.
**Evidence:** `cmru/src/cmru/delegated.py:run_delegated_config()` expects nested mappings such
as `changelog = { enabled = true, required = true }`, but the strict S2 model reduces
`[project.X.delegated]` values to booleans. No production CLI/release path calls
`run_delegated_config()` at all. Existing tests exercise the helper directly, not an actual
release transaction. Consequently `cosign`, `syft`/`grype`, `git-cliff`, `nfpm`, and minisign
cannot be enabled through the documented project config during `cmru release`.
**Decision required:** wire a single typed delegated schema into the release lifecycle with an
artifact-selection contract and end-to-end gate, or withdraw the advertised S7 config surface
until it is designed. This must not be "fixed" by guessing which artifact/path each tool should
receive.

### KI-05 — S-CLI.4 says legacy release configuration is removed, while the runtime preserves it — *open*
**Status:** specification/code mismatch. **SPEC:** `S-CLI.4`.
**Evidence:** `cmru/src/cmru/runner.py:run_step()` falls back from `cmru.toml` to
`release.toml`, and CLI help still labels `--config` as a release.toml path. The lenient release
loader also accepts legacy table/key spellings (KI-03). This contradicts “no legacy remains.”
**Decision required:** remove the fallback and aliases in a deliberate breaking release, or
amend S-CLI.4 with a supported compatibility window and retirement date. The audit leaves the
behavior unchanged.

### KI-06 — Retained release resume does not satisfy the documented already-tagged idempotency — *open*
**Status:** potential publication-recovery defect. **SPEC:** `S-CLI.1`, `S-CLI.5`.
**Evidence:** `detect_changed_projects()` reports no change once a version tag is on HEAD;
`cli.main()` then produces an empty `release_names` list. A failure after tag creation but before
artifact publication retains a worktree, yet `--resume` re-enters the same tag-based selection
and does not select that half-finished project for build/publish. This does not implement the
promise that re-running on a HEAD already carrying the tag reuses it to finish the release.
**Decision required:** add durable per-project publication checkpoints and a tested resume path,
or narrow S-CLI.1 to the transaction states that are actually resumable. Do not retag, move, or
republish an existing tag speculatively.

### KI-07 — Runner log location conflicts with S3.4 — *open*
**Status:** specification/code mismatch. **SPEC:** `S3.4`.
**Evidence:** S3.4 requires `<log_dir>/<project>/<step>.log`, while
`runner.execute_step()` writes `<log_dir>/<step>-<UTC timestamp>.log`; the orchestrator passes
one shared repository `logs/` directory. Project identity is absent from the filename, so the
spec's stable per-project path and the implementation's timestamped collision-avoidance design
are different interfaces.
**Decision required:** choose stable project-scoped logs (and define retention/overwrite), or
amend S3.4 to the timestamped shared-log contract. Do not change paths without deciding how
existing operators and automation locate logs.

### KI-08 — S4 overstates what the `cmru publish` verb implements — *open*
**Status:** specification/code mismatch. **SPEC:** `S4.1`–`S4.4`.
**Evidence:** the CLI's `publish` verb selects each project's `push` step and runs it;
it does not itself discover an artifact, calculate a sidecar, create a Release, or update
`latest.json`. Those actions occur only when the selected project uses a built-in handler or
when its project-owned push command implements them. This follows S-REL.3's project-owned
artifact mechanics, but contradicts the unconditional language in S4.1–S4.4.
**Decision required:** either make a generic publish profile/config truly own those operations,
or scope S4's MUSTs to the built-in wheel/tarball handlers and describe custom push commands
as responsible for equivalent publication guarantees. Do not silently make arbitrary custom
projects publish from guessed `dist/` paths.

### KI-02 — Built-in OCI repack is disabled pending production equivalence — *fail-closed*
**Status:** guarded; do not enable for production releases.
**SPEC:** `S14.3`, `V21`.
**Symptom:** the prototype used shared `/tmp/oci-src` and `/tmp/oci-dst` paths, blurred
OCI layout-directory and archive/build-context semantics, and its push branch could
fall back to a second bake rather than proving that the validated repacked artifact
was the object published.
**Guard:** `[project.X.oci].repack = true` and direct built-in `--repack` invocations
fail with exit 2 before authentication, Docker execution, or scratch mutation. Normal
non-repack OCI builds and pushes are unaffected.
**Enablement gate:** unique scratch lifecycle; explicit OCI tar/layout handling;
governed builder resources and concurrency; structural plus runtime validation; final
registry digest verification; and ideally a single-build flow. See `S14.3` for the
normative definition of done.
**Related:** `FEAT-02` validates the single-build + digest-verification mechanism
(`type=oci` layout build → `crane push` the layout directly → `crane digest`/`regctl
manifest digest` equality check) end-to-end against a real registry, for mdt's
*delegated* script. The same mechanism is a candidate building block for eventually
closing this item for the built-in handler — it is not itself that closure.

### KI-01 — GHCR package visibility cannot be set via API (platform limitation) — *worked around*
**Status:** worked around (cmru no longer fails the release); full automation is upstream-blocked.
**SPEC:** `S4.7` (amended MUST → best-effort).
**Symptom:** `cmru release` for an OCI project (mdt, pwmcp) pushed the image fine but then aborted with
`[ERROR] set GHCR package visibility … HTTP 404`, failing the whole release after a successful push.
**Root cause (verified 2026-06-21):** GitHub exposes **no REST or GraphQL API** to change a container
package's visibility. `PATCH …/users/<owner>/packages/container/<name>` **and** `…/user/packages/
container/<name>` both return `404` — the route does not exist (not a permission mask). Classic PATs have
**no `admin:packages`** scope (only `read:`/`write:`/`delete:packages`); **fine-grained PATs cannot use
the Packages API at all** ([github/roadmap#558](https://github.com/github/roadmap/issues/558)). So **no
token of any kind** can do this programmatically.
**Fix shipped:** `cmru/src/cmru/ghcr.py` now raises a typed `PackageVisibilityApiUnsupported`;
`mirror_package_visibility` catches it and logs a **non-fatal `[WARN]`** with the one-time UI remediation,
then returns the current visibility. A successful image push no longer fails the release on visibility.
**Operator action (one-time per package):** *Your packages → `<pkg>` → Package settings → Danger Zone →
Change visibility → Public*. Visibility **persists across all future pushes**, so it is never repeated.
**Re-check upstream:** if fine-grained PATs gain Packages API support (roadmap#558), or GitHub adds a
visibility endpoint, restore fully-automatic sync and re-tighten `S4.7` to MUST.
