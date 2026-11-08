# mounttrace

Read-only analyser for layered mounts, btrfs subvolumes, mount namespaces and mount propagation:
*which filesystem does this path really reach, from whose point of view (host, service, container),
and what happens on the next mount or umount?*

```bash
python3 mounttrace.py /srv/data/projects --submounts
sudo python3 mounttrace.py /data --pid "$(docker inspect -f '{{.State.Pid}}' NAME)" --host-path /srv/data
```

- **[mounttrace.md](mounttrace.md)**: the guide (concepts, native `findmnt` recipes, propagation with
  worked and tested examples, btrfs subvolumes, limits, tool reference, Soulmask/Wings case study).
- Terminal output renders control characters in paths, symlink targets, and options as `\xNN` and
  bidi/invisible formatting characters as `\uNNNN`; see
  [the design guide](docs/DESIGN-GUIDE.md#terminal-output) for why raw lookup values stay unchanged.
- **[Consumer examples](docs/CONSUMERS.md)**: copyable commands for inspecting a process or container.
- `mounttrace.py`: Python 3.9 source syntax, standard library only, a single file. The current
  environment has no Python 3.9 runtime, so runtime compatibility with 3.9 is unverified.
- Unprivileged checks: from this directory, `python3 tests/test_mounttrace.py` runs the tests that do
  not need root; topology scenarios run when user + mount namespaces are available.
- `run-gate suite` runs the namespace-free subset (10 tests) in `tester-unified`. Topology scenarios
  skip there because the container blocks user namespaces; see the
  [gate design](docs/DESIGN-GUIDE.md#test-gates).
- Full real-kernel checks: run `testing/vm/setup-vm-lab.sh` once, then
  `testing/vm/run-vm-tests.sh` in the disposable QEMU guest (guide §9.6/§9.7).
- `artifacts/vm-lab` is an ignored symlink into the btrfs CoW area (`$MDT_COW_ROOT`), created by
  `setup-vm-lab.sh`.
