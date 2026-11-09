# mounttrace

Read-only analyser for layered mounts, btrfs subvolumes, mount namespaces and mount propagation:
*which filesystem does this path really reach, from whose point of view (host, service, container),
and what happens on the next mount or umount?*

```bash
python3 mounttrace.py explain /srv/data/projects --submounts
pid=$(docker inspect -f '{{.State.Pid}}' NAME)
sudo python3 mounttrace.py compare /srv/data / "$pid:/data"
sudo python3 mounttrace.py audit /srv/data "$pid:/data"
sudo python3 mounttrace.py --json --no-probe explain "$pid:/data"
```

- **[mounttrace.md](mounttrace.md)**: the guide (concepts, native `findmnt` recipes, propagation with
  worked and tested examples, btrfs subvolumes, limits, tool reference, Soulmask/Wings case study).
- Five subcommands answer distinct questions: `explain`, `compare`, `audit`, `predict`, and `soulmask`.
  All accept paths in the invoking view or as `PID:/path`; `--json` is the machine interface.
- `soulmask` checks its state file and audits the live volumes root in one report. Its state file
  defaults to `/run/soulmask_tmpfs.state`; see the [design guide](docs/DESIGN-GUIDE.md#cli-and-report)
  for the bundled audit and watch behavior.
- Default human output starts with the question and verdict, uses ALERT/NOTE findings, and fits the
  selected terminal width. `-v` expands collapsed details, prints the full report header, and disables
  truncation. See the
  [design guide](docs/DESIGN-GUIDE.md#cli-and-report) for the interface choices.
- Terminal output renders controls in paths, symlink targets and options as `\xNN`; selected
  bidi/invisible formatting characters use `\uNNNN`. The [design guide](docs/DESIGN-GUIDE.md#terminal-output)
  explains why the inspected values remain unchanged.
- **[Consumer examples](docs/CONSUMERS.md)**: copyable commands for inspecting a process or container.
- `mounttrace.py`: Python 3.9 source syntax, standard library only, a single file. The current
  environment has no Python 3.9 runtime, so runtime compatibility with 3.9 is unverified.
- `run-gate suite` runs the namespace-free subset in `tester-unified`. Topology scenarios skip there
  because the container blocks user namespaces; see the
  [gate design](docs/DESIGN-GUIDE.md#test-gates).
- Full real-kernel checks: run `testing/vm/setup-vm-lab.sh` once, then
  `testing/vm/run-vm-tests.sh` in the disposable QEMU guest (guide §9.6/§9.7).
- `artifacts/vm-lab` is an ignored symlink into the btrfs CoW area (`$MDT_COW_ROOT`), created by
  `setup-vm-lab.sh`.
