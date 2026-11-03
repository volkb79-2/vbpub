# TrueNAS 27 nightly adapter

This adapter is pinned to **27.0.0-MASTER+20261027-173422** from the
`TrueNAS-27-Nightlies` train. Its 19 middleware target hashes were captured
from the running v1001 guest after update and are listed in `targets.sha256`.
The target-list SHA-256 is
`0a7ab3fd05ac0471cda4f48904fbae5c3221de9fa5eb977dcfaecdb245ccf3c1`. No
neighboring nightly is implied.

The update changed `pool_/dataset.py`'s promote and rename context and inserted
an import in `container/container.py`; those source hunks were adapted to the
exact captured files. All patches are regenerated as zero-fuzz diffs from that
source snapshot. The pool shim accepts only a reserved partition that is
unused, ZFS GPT typed, labeled `boot-disk-data`, numbered 4 or later, and on
the boot-pool parent disk. The final middleware patch also guards
`boot.attach` before `boot.format`: it refuses that reserved partition as a
boot-pool member before the stock attach path can format it.

The official feed listed this as its latest v27 nightly on 2026-10-30. The
re-apply path accepts only the recorded RC.1 install manifest, a complete match
for these 19 pristine source hashes, and a bounded `boot-pool/user-data` root.
See the parent patch README and consumer procedure for live Path A/B matrix,
reboot/import, and post-update recovery evidence.
