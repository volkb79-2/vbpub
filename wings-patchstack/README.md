# Wings patch series

`wings-patchstack/` is the sole canonical home for Wings patch files,
metadata, and patch workflow scripts. The active v1 Pterodactyl series
is under `patches/pterodactyl-v1.13.3/`; the separate F1 filesystem series is
under `patches/filesystem/pterodactyl-v1.13.1/`. Every active patch is one
logical change and carries `Change-Id`, `Capability`, `Role`, `Risk`, and
`Requires` trailers checked by the shared [patchseries library](../libraries/patchseries/README.md).

The former `wings-cgroups/v1-legacy/patchstack/` layout was consolidated here.
The original deployed v1 patch bytes, old stack configuration, and former
README are preserved under [`archive/deployed-v1/`](archive/README.md). The
stale Pelican series and the earlier v2 proposal snapshot are also retained in
the archive; the scripts refuse to build the stale Pelican series.

The capability and role inventory below is rendered from patch trailers by
`patchseries`. Design choices are in the [design guide](DESIGN-GUIDE.md#canonical-layout-and-provenance);
commands for consumers are in the [consumer guide](CONSUMERS.md#apply-the-pinned-series).

## Series inventory

<!-- patchseries: inventory:start -->
## Capabilities

| Capability | pterodactyl-v1.13.3 | filesystem-pterodactyl-v1.13.1 |
| --- | --- | --- |
| configuration-diagnostics | 1 change (+135/-1) | — |
| dependent-startup | 1 change (+665/-23) | — |
| node-placement | 2 changes (+233/-2) | — |
| readonly-volume-startup | — | 1 change (+40/-0) |
| scope-resource-control | 2 changes (+2021/-34) | — |
| server-placement | 1 change (+186/-9) | — |
| sftp-log-hygiene | 2 changes (+244/-13) | — |
| startup-resource-staging | 2 changes (+2219/-63) | — |

## Roles

| Role | pterodactyl-v1.13.3 | filesystem-pterodactyl-v1.13.1 |
| --- | --- | --- |
| enable | 5 changes (+5106/-110) | 1 change (+40/-0) |
| guard | 5 changes (+532/-20) | — |
| plumbing | 1 change (+65/-15) | — |
<!-- patchseries: inventory:end -->

## Pinned upstream and provenance

`stack.conf` pins Pterodactyl `v1.13.3` to commit
`6987d5e6f0612133e031255a99655e822d30579a`. Clone and apply both verify the
remote ref against that full SHA. The reusable
[`prove-v1-equivalence.sh`](scripts/prove-v1-equivalence.sh) applies the
archived deployed series and the metadata-bearing series to that exact base,
then requires equal Git tree hashes and an empty `git diff --stat`.

`build-image.sh` derives the `cgroup.N` suffix from the series digest and the
committed [`image-ledger.json`](image-ledger.json). The digest covers the
ordered patch filenames and bytes, upstream commit SHA, and `patchseries`
version. The build date is an image label and is excluded from the digest.
Each successful image build records its suffix, digest, image ID, and build
date. The prior `1.13.3-cgroup.4` image predates this ledger; its suffix is
recorded as a legacy floor, with unknown digest and image ID called out rather
than invented.

## Checks

The patch series metadata check and README table use the same source patches:

```bash
PYTHONPATH=../libraries/patchseries/src python3 -m patchseries check \
  patches/pterodactyl-v1.13.3 --vocabulary vocabulary.toml
scripts/prove-v1-equivalence.sh
```

The equivalence proof is a local Git operation and does not build an image or
contact Docker.
