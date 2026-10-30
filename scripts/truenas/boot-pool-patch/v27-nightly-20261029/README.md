# TrueNAS 27 nightly adapter — 2026-10-29

This exact-build adapter targets `27.0.0-MASTER+20261029-173417`. The running
package tree was captured after the official updater installed the build. Its
19 target files matched the previous v27 nightly reference byte for byte, and
all six patch files passed zero-fuzz dry-runs against those captured files.
The target manifest SHA-256 is `0a7ab3fd05ac0471cda4f48904fbae5c3221de9fa5eb977dcfaecdb245ccf3c1`.

The registry entry pins this version and target manifest. The next v27 RC
adapter is registered separately; version-prefix matching does not authorize
this adapter on another nightly. Its post-update route accepts only the listed
RC.1 install-bundle digests, the complete target preimage set, and the bounded
Path A/Path B roots.

The latest-build update-survival run passed: the data remained readable by
direct ZFS and content-hash checks, while stock middleware left Path A
unmounted until the matching adapter is reapplied. See the [acceptance
index](../../docs/ACCEPTANCE-RESULTS.md) and [update evidence](../../docs/UPDATE-AND-DRIFT.md#update-mechanics-q1).
