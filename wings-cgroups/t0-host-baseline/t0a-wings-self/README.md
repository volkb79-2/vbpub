# T0a — cgroup limits for Wings itself (zero code changes)

Puts the Wings **management process** under its own resource-controlled systemd
slice. This is independent of everything else in this project and satisfies the
"cgroup limits for wings itself" goal outright. Do this unconditionally.

## Compose deployment (`<wings-compose-dir>/docker-compose.yml`)

`<wings-compose-dir>` is wherever the node keeps the Wings compose file
(commonly `/etc/pterodactyl` or a dedicated `ptero-wings` directory).

1. Install the slice unit and load it:

   ```bash
   cp wings-mgmt.slice /etc/systemd/system/
   systemctl daemon-reload
   systemctl start wings-mgmt.slice
   systemctl show wings-mgmt.slice -p FragmentPath -p MemoryHigh -p MemoryMax   # pre-flight
   ```

2. Add `cgroup_parent` to the wings service (see
   `docker-compose.override.yml` — drop it next to the main compose file, or
   merge the key into it):

   ```bash
   cp docker-compose.override.yml <wings-compose-dir>/
   cd <wings-compose-dir> && docker compose up -d --force-recreate wings
   ```

3. Verify placement:

   ```bash
   docker inspect --format '{{.HostConfig.CgroupParent}}' wings
   cat /proc/$(docker inspect --format '{{.State.Pid}}' wings)/cgroup
   # expect: .../wings-mgmt.slice/docker-<id>.scope
   ```

## Native systemd deployment (wings installed as a service)

```bash
systemctl edit wings.service
```

```ini
[Service]
Slice=wings-mgmt.slice
```

(or put `MemoryHigh=`/`MemoryMax=`/`CPUWeight=` directly on `wings.service`).

## Notes

- Only the Wings daemon lands here. Game/installer containers are separate
  Docker scopes and are governed by T0b/T1/T2 placement.
- Sizing: Wings itself is small (file ops, websockets, backups). Backup
  compression is the peak load — size `MemoryHigh` for that, not for idle.
