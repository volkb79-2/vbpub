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

## M2 management live checks (authored, not run)

The M2 acceptance checks below are manual review steps; this implementation does not contact a TrueNAS host. Use a
disposable v27 host and data pool, inspect every dry-run plan first, and remove only the test share/task/app/API key
created for that check.

- Shares: create a disposable existing directory under the data pool; dry-run SMB and NFS create payloads; confirm
  ACL data appears for SMB; delete each export with the exact typed share name/path and verify the directory remains.
- Data Protection: create a periodic snapshot task for a disposable dataset, verify its cron/retention/naming fields,
  run it once, disable it, then delete only the task using its typed dataset/ID target. Inspect replication source and
  target in the plan; do not run a task with `allow_from_scratch` unless destination snapshot removal is intended.
  Run only a disposable cloud-sync task; verify credential fields stay masked and treat `MOVE` as source deletion.
- Network: run `tnt network show --json`; compare `interfaces`, `addresses`, `routes`, `dns`, and
  `default_gateways` with System > Network in the web UI, then confirm the TUI screen exposes no write controls. This
  check is authored only and has not been run.
- Apps: list installed apps and upgrade-available state; start/stop/restart only a disposable app; uninstall only an
  app with disposable data and keep the delete-data-volumes choice at NO unless data deletion is explicitly intended.
- Audit and API Keys: page through audit results with time/user/event filters; create a temporary API key, copy it from
  the one-time display, then revoke it after the acceptance check.
- SSH hand-off: use a host profile with a configured known_hosts file and verify both interactive and one-command
  sessions return to the TUI/CLI. Never bypass host-key verification.
- System actions require the additional explicit gate `TNT_LIVE_ALLOW_SYSTEM=1`. Only on a maintenance host with an
  approved outage window, inspect the running-job/VM/container/SMB-session preflight, then separately verify reboot,
  shutdown, and update check/apply/reconnect. Without that environment variable, live system-action checks must refuse
  before issuing any action call. Config export should be written to a new local file and verified as mode `0600`.
