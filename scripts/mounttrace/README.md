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
- Terminal output renders control characters in paths, symlink targets, and options as `\xNN`; see
  [the design guide](docs/DESIGN-GUIDE.md#terminal-output) for why raw lookup values stay unchanged.
- **[Consumer examples](docs/CONSUMERS.md)**: copyable commands for inspecting a process or container.
- `mounttrace.py`: Python 3.9 source syntax, standard library only, a single file. The current
  environment has no Python 3.9 runtime, so runtime compatibility with 3.9 is unverified.
- Tests: the suite runs in its disposable QEMU guest: `testing/vm/setup-vm-lab.sh` once, then
  `testing/vm/run-vm-tests.sh` (guide §9.6/§9.7). `tester-unified` blocks the user-namespace setup,
  so this project has no registered Run-Gate lane and no gate covers the suite.
- `artifacts/vm-lab` is an ignored symlink into the btrfs CoW area (`$MDT_COW_ROOT`), created by
  `setup-vm-lab.sh`.
