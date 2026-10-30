# Wings patch series

`wings-patchstack/` is the canonical home for Wings patch files, metadata, and
workflow scripts. The active Pterodactyl series targets `v1.13.4` in
[`patches/pterodactyl-v1.13.4/`](patches/pterodactyl-v1.13.4/). The prior
`v1.13.3` series remains available at
[`patches/pterodactyl-v1.13.3/`](patches/pterodactyl-v1.13.3/) as the recorded
base for the running image. The separate F1 filesystem series is under
`patches/filesystem/pterodactyl-v1.13.1/`. Every patch carries `Change-Id`,
`Capability`, `Role`, `Risk`, and `Requires` trailers checked by the shared
[patchseries library](../libraries/patchseries/README.md).

The former `wings-cgroups/v1-legacy/patchstack/` layout was consolidated here.
The original deployed v1 patch bytes and configuration remain under
[`archive/deployed-v1/`](archive/README.md). The stale Pelican series and earlier
v2 proposal snapshot are also retained in the archive; active scripts refuse
to build the stale Pelican series. The F1 filesystem series remains a proposal.

The capability and role inventory below is rendered from patch trailers.
Design decisions and the v1.13.4 forward-port are in the
[design guide](DESIGN-GUIDE.md#forward-porting-and-image-builds); adopter
commands and later deployment steps are in the
[consumer guide](CONSUMERS.md#rebuild-and-deploy-later). The detailed comparison
and build evidence are in the
[forward-port report](reports/forward-port-v1.13.3-to-v1.13.4.md).

## Series inventory

<!-- patchseries: inventory:start -->
## Capabilities

| Capability | pterodactyl-v1.13.3 | pterodactyl-v1.13.4 | filesystem-pterodactyl-v1.13.1 |
| --- | --- | --- | --- |
| configuration-diagnostics | 1 change (+135/-1) | 1 change (+122/-1) | — |
| dependent-startup | 1 change (+665/-23) | 2 changes (+668/-28) | — |
| node-placement | 2 changes (+233/-2) | 2 changes (+233/-2) | — |
| readonly-volume-startup | — | — | 1 change (+40/-0) |
| scope-resource-control | 2 changes (+2021/-34) | 2 changes (+2019/-32) | — |
| server-placement | 1 change (+186/-9) | 1 change (+186/-9) | — |
| sftp-log-hygiene | 2 changes (+244/-13) | 2 changes (+176/-4) | — |
| startup-resource-staging | 2 changes (+2219/-63) | 3 changes (+2220/-65) | — |

## Roles

| Role | pterodactyl-v1.13.3 | pterodactyl-v1.13.4 | filesystem-pterodactyl-v1.13.1 |
| --- | --- | --- | --- |
| enable | 5 changes (+5106/-110) | 5 changes (+5104/-111) | 1 change (+40/-0) |
| guard | 5 changes (+532/-20) | 7 changes (+455/-15) | — |
| plumbing | 1 change (+65/-15) | 1 change (+65/-15) | — |
<!-- patchseries: inventory:end -->

Patch authoring uses the closed trailer vocabulary in `vocabulary.toml`:
`Capability` is `configuration-diagnostics`, `dependent-startup`,
`node-placement`, `readonly-volume-startup`, `scope-control`,
`scope-resource-control`, `server-placement`, `sftp-log-hygiene`, or
`startup-resource-staging`; `Role` is `enable`, `guard`, or `plumbing`; and
`Risk` is `low`, `medium`, or `high`. Series statuses are `active`, `proposal`,
and `archived`. The `upstream` values are `pterodactyl` and `pelican`; active
scripts currently refuse the archived Pelican series.

## Pinned upstream and provenance

The active target is the official [Pterodactyl Wings v1.13.4 release](https://github.com/pterodactyl/wings/releases), pinned in `stack.conf` to commit
`747bc4393daf5d95fdf61d76382f5e23749cffe9`. The preceding `v1.13.3` series is
pinned to `6987d5e6f0612133e031255a99655e822d30579a`. Clone, apply, and image
build workflows verify a fetched ref against the corresponding full commit SHA.

`build-image.sh` derives each `cgroup.N` suffix from the ordered patch filenames
and bytes, upstream commit SHA, and `patchseries` version, then records the
successful image ID and build date in [`image-ledger.json`](image-ledger.json).
The UTC build date is an image label and is excluded from the series digest.
The `.4` port retains all 11 original Change-Ids; two v1.13.4-specific guard
patches preserve upstream boot and console behavior.

## Images observed and built on 2026-10-30

| Image | Image ID | Wings binary SHA-256 | Series digest |
| --- | --- | --- | --- |
| Running `wings-local:1.13.3-cgroup.6` | `sha256:abd25f6ee6fadd865ba06d2b2192bfb466b5b5e0280751b78de76f273abf5468` | `8d41e704662c815c67ef3427dc92757a2c25c8685afb1db0346aca0e893a080f` | Unknown; original image has no provenance labels |
| Local `wings-local:1.13.3-cgroup.7` | `sha256:eb10b720d51694acbfe8e777020ba83c65172caf3bdd710757e6f3f6a5797e97` | `0af71bce2de452a9a515b99ee5eac5318332c46e55fc5a542f2b15e106222941` | `358e0116e1ceca1296daa156ea2b4e82ebf0719197d9731c6ee950523d028d66` |
| Local `wings-local:1.13.4-cgroup.1` | `sha256:a4ceb4632f3e93b8c1c87b7e01d71211de590d27a90b5df954d5890cc4f554ec` | `ff35d939a2986b7813a6c37c7f5acdf6406a4df14e08f7a8b3c513c3c0ccb54c` | `923d9296e08ab5dcbaaec6a722625c99f0e14a968f424685e8c48dfc452257c7` |

The running `.6` binary was reproduced byte-for-byte from the current `.3`
patch series with Go 1.24.11 and the recorded Dockerfile recipe. The `.7` binary
has a different SHA because its embedded version changed from
`1.13.3-cgroup.6` to `1.13.3-cgroup.7`. The `.4.1` image was built locally and
was not deployed or pushed. Full build settings, patch outcomes, and test
results are in the [forward-port report](reports/forward-port-v1.13.3-to-v1.13.4.md).

## Checks

```bash
PYTHONPATH=../libraries/patchseries/src python3 -m patchseries check \
  patches/pterodactyl-v1.13.4 --vocabulary vocabulary.toml
scripts/test.sh pterodactyl
```

`test.sh` runs the Go build, vet, affected package tests, and integration-tag
compile checks inside the configured capped Go container. `build-image.sh`
re-applies the committed series to the pinned upstream commit and requires the
resulting source tree to match the checkout before it builds. The historical
[`prove-v1-equivalence.sh`](scripts/prove-v1-equivalence.sh) proves only that
the archived deployed `.3` patches and their canonical `.3` series produce the
same tree; it does not claim source-tree equivalence across upstream releases.
