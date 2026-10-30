# Wings v1.13.3 to v1.13.4 forward-port report

Date: 2026-10-30

## Upstream pins

The [official Wings releases page](https://github.com/pterodactyl/wings/releases)
lists v1.13.4. The official tag was fetched and resolved to
`747bc4393daf5d95fdf61d76382f5e23749cffe9`. The prior v1.13.3 pin is
`6987d5e6f0612133e031255a99655e822d30579a`.

The forward-port applied the 11 committed v1.13.3 patches individually with
`git am` onto the pinned v1.13.4 tree. Existing Change-Ids remain unchanged.
Thirteen patches are now in the v1.13.4 series: those 11 plus two narrow
v1.13.4-specific guards discovered while validating the port.

## Patch application record

| Patch / Change-Id | v1.13.4 result |
| --- | --- |
| 0001 `wings-node-cgroup-parent` | Applied with `git am --3way`; no manual content edit. |
| 0002 `wings-server-cgroup-parent` | Applied with `git am --3way`; no manual content edit. |
| 0003 `wings-docker-placement-tests` | Applied cleanly. |
| 0004 `wings-scope-resource-control` | Resolved `go.mod`/`go.sum` conflicts manually. Kept v1.13.4's newer dependency versions and added only the two required D-Bus modules; ran `go mod tidy` in the capped Go 1.27.2 container. |
| 0005 `wings-resource-value-logging` | Applied cleanly. |
| 0006 `wings-startup-resource-staging` | Adapted the `server/listeners.go` conflict to retain v1.13.4's removal of stop-command console-state detection while adding the slice matchers and phase events. The first package test exposed a nil process-configuration read in the new matcher; patch 0013 guards it. |
| 0007 `wings-config-key-diagnostics` | Preserved v1.13.4's new `config.Load` API. Diagnostics are collected after its permissive YAML unmarshal so `FromFile` and callers retain upstream behavior. |
| 0008 `wings-memory-current-ramp-fix` | Applied cleanly. |
| 0009 `wings-dependent-startup` | Merged the child deferral into v1.13.4's shared boot liveness snapshot. Initial compilation caught the upstream `cerrdefs.IsNotFound` name and the attach timeout context; patch 0012 preserves both. |
| 0010 `wings-sftp-auth-rejection-log` | Adapted to v1.13.4's separate handshake/connection-limit flow. Authentication failures use the warning classifier; errors after an authenticated handshake keep the upstream error path. |
| 0011 `wings-sftp-handshake-drop-log` | Adapted the classifier to that same handshake path, retaining distinct warning messages for auth rejection and a pre-auth network disconnect. |
| 0012 `wings-dependent-startup-v1-13-4-boot-fix` | New port guard: retains the upstream not-found check and per-server context used to attach to a running environment. |
| 0013 `wings-startup-resource-staging-v1-13-4-config-guard` | New port guard: avoids dereferencing an absent process configuration when the steady matcher processes console output. |

`patchseries check` reports `OK: 13 changes`. `patchseries delta` finds all 11
original Change-Ids and two added guards, with none removed. The existing IDs
show as changed because patch bytes are release-specific. The image build
workflow re-applied all 13 exported patches to the pinned upstream tree and
confirmed the resulting tree hash matched the reviewed forward-port branch.

## Validation

Validation ran in the configured `golang:1.27.2-alpine` container with
`CGROUP_PARENT_DEV_GATES` supplied and a three-CPU limit. `test.sh pterodactyl`
passed:

- `go build ./...`
- `go vet` excluding the configured packages with pre-existing upstream vet
  findings
- `go test -count=1 ./config/... ./environment/... ./server/... ./internal/cgroups/... ./sftp/...`
- Docker and systemd integration-tag vet/compile checks

The first patched test run failed in
`TestConsoleOutputIsCheckedAsItIsRead`: the new ANSI matcher dereferenced a nil
process configuration. The same `go test -count=1 ./server/...` passed on
vanilla v1.13.4. After adding patch 0013, the full patched workflow passed,
including `server` and `sftp`.

A later full rerun had one non-reproducing failure in
`TestPerUserLimitUsesAuthenticatedAccount`, which expected a third same-account
SFTP connection to close. Vanilla v1.13.4's `go test -count=1 ./sftp/...`
passed, the patched SFTP package passed on immediate rerun, and the next full
patched workflow passed. The cause of that single connection-limit failure was
not established.

## Running v1.13.3 image and SHA comparison

The exact production container inspected was
`ptero-wings-patched-133-wings-1`, using
`wings-local:1.13.3-cgroup.6`, image ID
`sha256:abd25f6ee6fadd865ba06d2b2192bfb466b5b5e0280751b78de76f273abf5468`.
It reports `wings v1.13.3-cgroup.6`; the binary SHA-256 is
`8d41e704662c815c67ef3427dc92757a2c25c8685afb1db0346aca0e893a080f`. The
image itself has no OCI labels. These are the labels on the running container:

| Label | Value |
| --- | --- |
| `com.docker.compose.config-hash` | `612fa2cf3358c8a2ba2f1104373d9c4549dd3a891b34788feed6f08413612019` |
| `com.docker.compose.container-number` | `1` |
| `com.docker.compose.depends_on` | empty |
| `com.docker.compose.image` | `sha256:abd25f6ee6fadd865ba06d2b2192bfb466b5b5e0280751b78de76f273abf5468` |
| `com.docker.compose.oneoff` | `False` |
| `com.docker.compose.project` | `ptero-wings-patched-133` |
| `com.docker.compose.project.config_files` | `/root/ptero-wings-patched-13.3/docker-compose.yml` |
| `com.docker.compose.project.working_dir` | `/root/ptero-wings-patched-13.3` |
| `com.docker.compose.service` | `wings` |
| `com.docker.compose.version` | `5.6.1` |
| `traefik.enable` | `true` |
| `traefik.http.routers.wings-api.entrypoints` | `websecure` |
| `traefik.http.routers.wings-api.rule` | ``Host(`wings.gstammtisch.dchive.de`)`` |
| `traefik.http.services.wings-api.loadbalancer.server.port` | `443` |

Embedded build information reports Go 1.24.11, `-trimpath=true`,
`CGO_ENABLED=0`, Linux/amd64, `GOAMD64=v1`, and no `-tags`, `vcs.revision`, or
`vcs.modified` entries. Go build ID:

```text
YQeepmblHt9LKs8Pj8-r/VlrLPcLvpqPICo_xxYOf/QT43GrEutBAt-yW0dsDP/faMGyBEUoUyPOc3YfaEb
```

ELF build ID (SHA-1): `3f05a32859285b51541ce2e8bacc2bccecc6bd38`.

A build of the current v1.13.3 patched source with the Dockerfile's Go 1.24.11
toolchain and flags, and
`-X github.com/pterodactyl/wings/system.Version=1.13.3-cgroup.6`, reproduced
the running binary SHA byte-for-byte. This demonstrates that the current `.3`
series and build recipe can produce the executable in `.6`; it does not recover
the original image's missing labels or series digest.

The new local `.3` image is `wings-local:1.13.3-cgroup.7`. Its source digest
has been recomputed using the current definition, which hashes the pinned
upstream commit and ordered patch bytes. The existing image label still holds
the digest written by the earlier definition; both values are recorded:

- Image ID: `sha256:eb10b720d51694acbfe8e777020ba83c65172caf3bdd710757e6f3f6a5797e97`
- Recomputed source series digest: `3f9df8045b72c0346a0a2bc9aeac7fa1af1fb033e3e395d7c568ab22fae2558b`
- Existing image-label series digest: `358e0116e1ceca1296daa156ea2b4e82ebf0719197d9731c6ee950523d028d66`
- Binary version: `wings v1.13.3-cgroup.7`
- Binary SHA-256: `0af71bce2de452a9a515b99ee5eac5318332c46e55fc5a542f2b15e106222941`
- Provenance labels include version `1.13.3-cgroup.7`, upstream commit
  `6987d5e6f0612133e031255a99655e822d30579a`, patchseries `0.1.0`, and build
  date `2026-10-30T12:28:42Z`.

Its binary SHA is not expected to match `.6`: the embedded version value
changed from `1.13.3-cgroup.6` to `1.13.3-cgroup.7`. The remaining Go build
settings and dependencies matched the `.6` executable's build information.

## v1.13.4 local image

The local tag is `wings-local:1.13.4-cgroup.1`. Its source digest has been
recomputed using the current definition, while its existing image label keeps
the earlier value:

- Image ID: `sha256:a4ceb4632f3e93b8c1c87b7e01d71211de590d27a90b5df954d5890cc4f554ec`
- Build date label: `2026-10-30T12:47:03Z`
- Recomputed source series digest: `fbe1fcddd19c59127f45d3afb923c532a15d054dfdd23f7d3f6c398c48838dec`
- Existing image-label series digest: `923d9296e08ab5dcbaaec6a722625c99f0e14a968f424685e8c48dfc452257c7`
- Binary version: `wings v1.13.4-cgroup.1`
- Binary SHA-256: `ff35d939a2986b7813a6c37c7f5acdf6406a4df14e08f7a8b3c513c3c0ccb54c`
- Go toolchain: 1.27.2; `-trimpath=true`; `CGO_ENABLED=0`; Linux/amd64; `GOAMD64=v1`
- Go build ID: `r6r2PhZqS88VVGtPczVo/7Vn70xlsxqESKc5ZJE6K/yExQTqVHQA0cXPtVkHGP/ZT5QwDwVu_e8teFmoc0k`
- Build command from the upstream Dockerfile: `CGO_ENABLED=0 go build -ldflags="-s -w -X github.com/pterodactyl/wings/system.Version=1.13.4-cgroup.1" -trimpath -o wings wings.go`
- Builder base: `golang:1.27.2-alpine@sha256:f92b6ef800e499660581efdabdf25d9d817a9d124eaf900924f0504e7e27e12d`
- Runtime base: `gcr.io/distroless/static:latest@sha256:15f8637509792e05b97d83451b0b6ccfa4abd548f997e406ac1e72c87fe1fa78`

The OCI labels are `org.opencontainers.image.version=1.13.4-cgroup.1`,
`org.opencontainers.image.created=2026-10-30T12:47:03Z`,
`org.vbpub.wings.series-digest=923d9296e08ab5dcbaaec6a722625c99f0e14a968f424685e8c48dfc452257c7`,
`org.vbpub.wings.upstream-commit=747bc4393daf5d95fdf61d76382f5e23749cffe9`,
`org.vbpub.wings.patchseries-version=0.1.0`, and
`org.vbpub.wings.build-date=2026-10-30T12:47:03Z`.

The build used `mdt-managed`, whose BuildKit service runs in the host's
`dev-buildkitd.slice` with a 5-CPU quota and 8-GiB memory limit. The build
command ran under `nice -n 19 ionice -c 3`; no other image build ran
concurrently.

The image was imported into the local daemon with BuildKit's `--load` output;
it was not pushed or deployed. The production Wings container and Compose
service were not changed. Later deployment steps are documented in
[`CONSUMERS.md`](../CONSUMERS.md#rebuild-and-deploy-later).

## Open items

- Deploy the local `.4.1` tag only during a separately planned maintenance
  window; this task did not restart the production service.
- The `.3.7` and `.4.1` image labels retain the earlier digest definition; the
  ledger and README separately record each recomputed source digest and its
  existing label value. No rebuild was made to change those labels.
- Update the existing Wings CI workflow matrix from the `.3` series to `.4` in
  a separate workflow-scoped change.
- Monitor `TestPerUserLimitUsesAuthenticatedAccount` if its one transient
  failure recurs; the vanilla package and patched retries passed.
- The original `.6` image provenance labels and series digest remain
  unrecoverable; only its executable was reproduced.
