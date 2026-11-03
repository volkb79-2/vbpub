# TrueNAS 25.10.4 boot-pool investigation

**Status: experimental; the intended UI workflow is not verified.**

This work records an investigation and patch for TrueNAS 25.10.4. The source system had an SSD boot pool named boot-pool and a four-disk RAIDZ1 data pool named pv1. The original constraint was to leave pv1 untouched.

## Current result

The latest operator report says **all expected actions through the UI did not work**. No successful end-to-end UI workflow is certified. The historical notes contain narrower claims of pool selection, a saved setting, and datasets being created; those claims conflict with the latest report and need careful reproduction.

A future test with PWMCP is pending. Until that test records the exact UI steps and resulting API/ZFS state, treat the patch as a code investigation, not a working TrueNAS feature.

## Two separate scopes

1. [SERVICE-POOL-PATCH.md](SERVICE-POOL-PATCH.md) covers the middleware changes intended to let Virt/Incus and Docker use boot-pool for their service storage.
2. [CUSTOM-DATASET-STORAGE.md](CUSTOM-DATASET-STORAGE.md) covers a user dataset such as boot-pool/appdata as a location for VM disks and container paths.

The service-pool patch does not by itself make an arbitrary boot-pool dataset selectable in VM or container forms.

## Retained artifacts

- `truenas-25.10.4-boot-pool-virt-docker.patch` is the proposed unified patch.
- `SERVICE-POOL-PATCH.md` and `CUSTOM-DATASET-STORAGE.md` record the consolidated
  analysis and unresolved claims.

The nested Git metadata, prompt files, duplicate reports, and source snapshots
from the original TrueNAS host repository were removed during consolidation.
The exact upstream source can be recovered from the TrueNAS 25.10.4 middleware
commit `e36f4f8318` and compared with the unified patch.

## Scope and caution

The patch targets middleware from TrueNAS 25.10.4 only. Do not assume it applies to TrueNAS 27 or later. Boot-pool use is intentionally unsupported upstream, and middleware updates can overwrite local changes. The boot SSD has no redundancy in the recorded setup, and VM/container workloads can add writes or fill the pool.

The old reports include static checks and observations from the source system, but the expected UI behavior is currently reported as failing. Revalidate the source version, middleware behavior, rollback procedure, and all UI/API flows before considering any deployment.
