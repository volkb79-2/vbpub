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
- `mounttrace.py`: Python ≥ 3.9, standard library only, a single file.
- Tests: `python3 tests/test_mounttrace.py` (unprivileged, user namespaces). The full lane, as root
  with real btrfs and Docker, runs in a disposable QEMU guest: `testing/vm/setup-vm-lab.sh` once,
  then `testing/vm/run-vm-tests.sh` (guide §9.6/§9.7).
- `artifacts/vm-lab` is an ignored symlink into the btrfs CoW area (`$MDT_COW_ROOT`), created by
  `setup-vm-lab.sh`.
