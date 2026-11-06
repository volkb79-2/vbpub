# Adopting iogovmon

`iogovmon` is the Python distribution and import package. Installing its wheel provides two console scripts: `iogovmond` for service work and
`iogovmonctl` for operator inspection and administration. The full command mapping and the design rationale are in the [README](../README.md#executables)
and [DESIGN-GUIDE](DESIGN-GUIDE.md#2-decision-log-what-was-dropped-from-the-blind-opinions-and-why).

## Install the wheel and inspect a host

From a directory containing the wheel:

```sh
python3 -m pip install ./iogovmon-*.whl
iogovmond --version
iogovmonctl --version
iogovmonctl preflight
```

The version commands report installed wheel metadata. `install.sh` stamps the source version from `pyproject.toml` and, when Git can resolve the
checkout, its short commit id into the root-owned package copy. The journal records the same version on each run; see the
[version provenance decision](DESIGN-GUIDE.md#version-provenance).

To check the configured disks and the read-only plan:

```sh
iogovmonctl --config /etc/iogovmon/iogovmon.toml devices
iogovmonctl --config /etc/iogovmon/iogovmon.toml plan
```

`iogovmonctl` also provides `status`, `analyze`, `config`, `annotate`/`note`, `quiet-experiment`, and `prune`. The daemon commands are `tick`,
`watch`, `bench`, and `cleanup`; for example, `iogovmond tick --dry-run` prints the tick plan without probing a device.

## Install the host service from a source checkout

The source-tree `install.sh` installs the package copy, command launchers, systemd units, and the read-only udev rule. Use an actual whole-disk node
and its exact udev `ID_PATH`:

```sh
cd iogovmon
DISK_NODE=/dev/disk/by-id/REPLACE_WITH_WHOLE_DISK_ID
ID_PATH=REPLACE_WITH_ITS_UDEV_ID_PATH
sudo ./install.sh --dry-run --disk-node "$DISK_NODE" --id-path "$ID_PATH"
sudo ./install.sh --disk-node "$DISK_NODE" --id-path "$ID_PATH"
```

Edit `/etc/iogovmon/iogovmon.toml`, then inspect it as the service user and run one manual tick:

```sh
sudoedit /etc/iogovmon/iogovmon.toml
sudo -u iogovmon iogovmonctl --config /etc/iogovmon/iogovmon.toml devices
sudo -u iogovmon iogovmonctl --config /etc/iogovmon/iogovmon.toml plan
sudo systemctl start iogovmond-tick.service
sudo journalctl -u iogovmond-tick.service
```

After checking the manual tick, enable the periodic read-only tick:

```sh
sudo ./install.sh --enable-tick --disk-node "$DISK_NODE" --id-path "$ID_PATH"
```

The optional resident watcher is `iogovmond.service`; the bounded write bench runs from `iogovmond-bench.timer` only after it is enabled. Review the
README's [install steps](../README.md#install-steps-the-installer-helper-above-does-these-the-list-is-the-reference) before enabling either.

There is no `iogovmon` console-script alias. `python -m iogovmon` remains a combined developer entry point. The wheel-only `iogovmonctl install` verb
is future work; use the source-tree `install.sh` for host integration in this release.
