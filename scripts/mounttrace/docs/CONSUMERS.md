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
pass the original path to the command as usual.
