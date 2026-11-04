# Custom boot-pool datasets as VM and container storage

## Scope and current status

This investigation is about using a user-created dataset such as boot-pool/appdata as a general storage location for VM disks and container paths. It is separate from configuring boot-pool as the managed Virt/Incus or Docker service pool.

The July reports concern a TrueNAS 25.10.4 host. They say boot-pool/appdata had canmount=on, could be mounted manually, and had mountpoint /mnt/boot-pool/appdata. The latest operator report says all expected UI actions did not work. No successful end-to-end VM or container UI flow is verified. A careful PWMCP-assisted reproduction is pending.

## Candidate blockers recorded in the reports

- The VM ZVol Locations form was reported to use pool.filesystem_choices(), whose internal-dataset filtering removes boot-pool datasets.
- The container source browser was described as mount-driven, while path validation was reported to check whether a path belongs to a registered storage.volume. Those are separate display and validation steps.
- One report found an explicit boot-pool ZVol rejection in the legacy vm.* storage-device path. The other report treats legacy vm.* and modern virt.* as separate paths. The actual UI/API path for the intended VM workflow must be identified before changing validation.
- The reports disagree about whether zfs-mount.service is disabled/inactive or runs zfs mount -a. A dataset being mounted when inspected does not establish that it mounts automatically after reboot.
- Both reports describe boot-pool filtering in dataset queries, but differ in how they describe the query layers and defaults. Distinguish raw ZFS visibility from middleware queries that apply internal-dataset filters.

These are hypotheses and observations from separate reports, not settled facts.

## Disagreements to preserve

| Topic | One recorded claim | Conflicting claim or limitation | Needed evidence |
|---|---|---|---|
| Automatic mount | The Codex report says zfs-mount.service was disabled and inactive, with no generic boot-pool/appdata mount path. | The Copi/DSFL report says zfs-mount.service runs zfs mount -a, then attributes the missing boot-time mount to boot-environment behavior. | Capture systemctl status/show, unit contents, dataset properties, and mount state before and after reboot on the exact host. |
| Dataset filtering | The Codex report says the VM chooser calls pool.filesystem_choices() and applies internal filters. | The Copi/DSFL report says both pool.dataset.query and zfs.dataset.query hide boot-pool by default. | Capture the exact API calls, arguments, query defaults, and responses used by the UI. |
| VM ZVol validation | The Copi/DSFL report identifies a boot-pool rejection in a vm.* storage-device validator. | The Codex report says this is a legacy path separate from modern virt.* and does not establish that the modern UI uses it. | Trace the UI form submission through middleware calls and identify the validator reached. |
| Container paths | The Codex report says the source tree is mount-driven and may show mounted paths. | The Copi/DSFL report says path validation can reject them because boot-pool is absent from storage.volume. | Test browsing and submission independently; record both UI contents and validation response. |
| UI outcome | The older README and patch review contain claims that pool settings were selectable/applied and datasets created. | The latest operator report says all expected UI actions did not work. | Reproduce each intended action with PWMCP and preserve screenshots, API results, and middleware logs. |
| Patch readiness | The earlier review calls the patch ready to apply. | The independent review noted migration/mountpoint caveats, and the latest UI report is negative. | Treat readiness as withdrawn until the relevant tests pass. |

Do not collapse these differences into one explanation. The source reports may be wrong; keep the claim, its provenance, and what would settle it until rechecked.

## Candidate approaches, not decisions

The reports suggest several possible approaches, none accepted yet:

- Add a targeted boot-time mount for boot-pool/appdata, or enable a broader ZFS mount service. The scope and interaction with middleware-managed mounts need validation.
- Add an opt-in boot-pool path for the VM ZVol picker while retaining default filtering elsewhere, or broaden the internal-dataset filters. Broad filter changes risk exposing system datasets.
- Adjust the VM ZVol validator for an explicitly allowed user dataset, if the intended UI actually reaches the legacy vm.* validator.
- Adjust container path validation or register an approved boot-pool path as a volume, if the validator is confirmed to require storage.volume membership.

Do not implement one of these based solely on the reports.

## PWMCP test plan — pending

When the live UI test is available:

1. Record the exact TrueNAS version/build and current pool/dataset state. Confirm pv1 remains untouched.
2. Capture each relevant UI form before and after interaction. Record whether boot-pool or boot-pool/appdata appears, whether save/apply completes, and any visible error.
3. In parallel, capture the corresponding middleware API calls and responses. Separate list/choice APIs from update and create calls.
4. Check boot-pool/appdata properties and mount state before reboot, after reboot, and after any middleware service restart.
5. Test VM storage selection, ZVol creation, disk attachment, and VM start as separate steps.
6. Test the container source browser, path validation, volume creation, and container start as separate steps.
7. Preserve middleware logs and relevant ZFS output for failures. Record each action as pass, fail, or not reached; do not report a whole workflow as successful because a picker displayed an item.
8. Recheck all resulting datasets and service configuration, and verify pv1 was not changed.

This is future acceptance work, not a test that has been performed by this consolidation.
