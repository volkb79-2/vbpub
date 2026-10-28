# Optional live v27 acceptance test

The `tests/live/test_live_workflows.py` probe exercises datasets and snapshots, a local user, a container, and a VM on
an operator-selected TrueNAS v27 host. It creates a unique `tnt-live-<suffix>` namespace and removes only the resources
it created there. It requires a disposable data pool with enough capacity for the container image, VM memory, and the
requested VM zvol. Do not point it at a production system or a pool that cannot safely hold temporary test data.

The default test command excludes the `live` marker through `pytest.ini`; `tests/test_live_lane_config.py` checks that
the exclusion remains configured. The live test is not skipped when selected: missing variables, an absent profile,
or a profile that cannot connect make the test fail. This implementation task wrote the live lane but did not run it.

Before running it, configure a private `tnt.toml` with a profile that is allowed to create and delete temporary
resources on the chosen host. The test uses the same profile loader and TLS rules as the CLI. Supply all required
environment values explicitly:

```sh
TNT_LIVE=1 \
TNT_LIVE_PROFILE=lab \
TNT_LIVE_POOL=tank \
TNT_LIVE_GROUP_ID=1000 \
TNT_LIVE_CONTAINER_IMAGE_NAME=alpine \
TNT_LIVE_CONTAINER_IMAGE_VERSION=latest \
TNT_LIVE_VM_MEMORY_MIB=2048 \
TNT_LIVE_VM_DISK_BYTES=10737418240 \
PYTHONPATH=src nice python -m pytest -q -p no:xdist -m live tests/live
```

Replace the sample values with values that exist on the selected host. `TNT_LIVE_GROUP_ID` is the TrueNAS group entry
ID, not the Unix GID. `TNT_LIVE_VM_MEMORY_MIB` must be at least 20, and the zvol size must be positive. The selected
pool and group are checked before any resource is created, and all generated test resource names begin with
`tnt-live-`.

TLS verification stays enabled by default. For a deliberately disposable host with a self-signed certificate, set
`TNT_LIVE_INSECURE=1` and use a profile with `tls = "insecure"`; both the profile and explicit acknowledgement are
required. Never place credentials in this command line. Keep them in the mode-0600 profile file described in the
[configuration guide](../README.md#configure-a-host).

Cleanup is attempted for every resource even if an earlier cleanup action fails. A cleanup failure fails the test and
names the resource class, so inspect the host for any remaining resource whose name starts with `tnt-live-` before
using that namespace again.
