# Consumer examples

Run mounttrace from the project directory or copy its standalone script to a
host with Python 3.9 source syntax or newer. Runtime compatibility with Python
3.9 is unverified; the available runtime is Python 3.14.

Compare a host volume tree with the view of a running container:

```bash
pid=$(docker inspect -f '{{.State.Pid}}' mycontainer)
sudo python3 mounttrace.py /var/lib/pterodactyl/volumes \
    --pid "$pid" --audit-prefix /var/lib/pterodactyl/volumes
```

For a single container path mapped to a host path:

```bash
pid=$(docker inspect -f '{{.State.Pid}}' mycontainer)
sudo python3 mounttrace.py /home/container \
    --pid "$pid" --host-path /var/lib/pterodactyl/volumes/instance
```

Terminal controls in names and options appear as visible `\xNN` sequences;
bidi and invisible formatting characters appear as `\uNNNN`. Pass the
original path to the command as usual.

## Run the tests

From `scripts/mounttrace`, run the unprivileged tests directly when the host
allows user and mount namespaces. The registered gate runs the namespace-free
subset in `tester-unified`; the QEMU lane runs the full suite, including
root-only btrfs and Docker scenarios.

```bash
cd scripts/mounttrace
python3 tests/test_mounttrace.py
run-gate suite
testing/vm/setup-vm-lab.sh   # once per checkout, on a configured CoW host
testing/vm/run-vm-tests.sh
```
