# Using thrashguard

Worked examples for the people who run it and the clients that talk to it. The
contract is [`docs/design/thrashguard/DESIGN.md`](../../docs/design/thrashguard/DESIGN.md);
this file shows what you paste, not why it is shaped that way (that is
[DESIGN-GUIDE.md](DESIGN-GUIDE.md)).

## 1. Standalone host: a static unit in `/etc`, no registrant

A host with no fleetd and no run-gate registrant still gets a complete service.
`/etc/thrashguard/thrashguard.toml`:

```toml
mode = "report"                 # off | report | enforce (enforce is refused in P1)
tick_s = 5
trigger_avg10 = 3.00            # host memory PSI `full` avg10 >= 3 held hold_s
hold_s = 10
release_avg10_factor = 2        # release at trigger / 2 held release_s
release_s = 60
victim_rule = "newest"          # newest | refault | smallest-enough (P3 implements the last two)
max_parked = 2
max_park_s = 1800
never_park_last = true
thaw_grace_s = 30
heartbeat_s = 30
missed_heartbeats = 3
reclaim_chunk_bytes = 268435456
reclaim_max_chunks = 8
boot_grace_s = 60
allowed_ancestors = ["dev-gates.slice", "dev-background.slice", "dev-buildkitd.slice"]
protected = ["dev-interactive.slice", "system.slice", "user.slice"]
state_dir = "/run/thrashguard"
registrant = true

[[static_unit]]
name = "nightly-r2-campaign"
cgroup = "dev-buildkitd.slice"
```

Drop-ins are merged lexically: `/etc/thrashguard/thrashguard.toml.d/10-local.toml`,
`20-...toml`, and so on, exactly the shape fleetd already reads. Unknown keys are
refused by name, not ignored.

Check what the config means before starting anything:

```bash
sudo thrashguard status --config /etc/thrashguard/thrashguard.toml
sudo thrashguard candidates --config /etc/thrashguard/thrashguard.toml
```

`status` prints the mode, the pressure reading (or `unknown` with the reason), the
candidate order for the next tick and every parked unit. In P1 nothing is parked,
so `frozen[]` is empty and every would-be action appears in `events` as a
`would-freeze` line.

## 2. Running the two sockets under systemd

```bash
sudo install -d -m 0755 /run/thrashguard
sudo groupadd --system thrashguard-registrant
sudo groupadd --system thrashguard-reader
sudo cp deploy/systemd/*.service deploy/systemd/*.socket /etc/systemd/system/
sudo cp deploy/tmpfiles.d/thrashguard.conf /etc/tmpfiles.d/
sudo systemd-tmpfiles --create /etc/tmpfiles.d/thrashguard.conf
sudo systemctl daemon-reload
sudo systemctl enable --now thrashguard-registrant.socket thrashguard-reader.socket
```

`Accept=no` on both sockets means one long-lived daemon owns the listener, so a
restart never refuses a client that connects during it.

## 3. A registrant writes one line per verb

Wire is newline-delimited JSON, one request one reply, unknown keys refused. The
client names a **container**, never a cgroup path.

```bash
cid=$(docker run -d --cgroup-parent dev-gates.slice ...)
printf '%s\n' "{\"v\":\"thrashguard-registration/1\",\"verb\":\"register\",\"id\":\"rg-abc123\",\
\"container\":\"$cid\",\"score\":7,\"priority\":\"freezable\",\"kind\":\"gate\",\
\"owner\":\"run-gate\",\"run\":\"selftest-r7\",\"heartbeat_s\":30}" \
  | socat - UNIX-CONNECT:/run/thrashguard/registrant/thrashguard.sock
# {"v":"thrashguard-registration/1","ok":true,"id":"rg-abc123","cgroup":"docker-<id>.scope","cgroup_id":4242,"ttl_s":90}

printf '%s\n' '{"v":"thrashguard-registration/1","verb":"heartbeat","id":"rg-abc123"}' \
  | socat - UNIX-CONNECT:/run/thrashguard/registrant/thrashguard.sock && sleep 30  # repeat

printf '%s\n' '{"v":"thrashguard-registration/1","verb":"deregister","id":"rg-abc123"}' \
  | socat - UNIX-CONNECT:/run/thrashguard/registrant/thrashguard.sock
```

A refused registration stores nothing and says which refusal it is:

```bash
# {"v":"thrashguard-registration/1","ok":false,"id":"x","reason":"none","detail":"no docker-<id>.scope under the allowed ancestors"}
```

`reason` is one of `none` (no scope found), `several` (more than one depth
matches), `protected` (the only match is under a protected ancestor), `malformed`
(the request itself) or `duplicate` (this id is already registered).

## 4. A reader asks what the guard sees

```bash
printf '%s\n' '{"v":"thrashguard-state/1","verb":"status"}' \
  | socat - UNIX-CONNECT:/run/thrashguard/reader/thrashguard.sock

printf '%s\n' '{"v":"thrashguard-event/1","verb":"events","since_seq":17}' \
  | socat - UNIX-CONNECT:/run/thrashguard/reader/thrashguard.sock
```

## 5. What an operator should expect from P1

- **Nothing is ever frozen.** `mode = report` journals `would-freeze` and
  `would-thaw`; `mode = enforce` is refused with a one-line reason until P2.
- **`unknown` is reported, not `0.00`.** Remove or unreadable pressure gives
  `psi: {"v": null, "reason": ...}` and a `unknown-pressure` event.
- **A frozen interval is never recorded** because no interval can be frozen yet.
  The journal holds decisions, with `seconds: 0` and an explicit `simulated` field.

## 6. Consumers

| Name | Today |
|---|---|
| `thrashguard status` / `candidates` | works, no daemon socket needed when given `--config` |
| run-gate (registrant) | P4; until then run-gate does not register anything and behaves exactly as today |
| assay | reads freeze events through hostobs, never this socket (design §4b) |
| fleetd / agctl | read through hostobs and one estate snapshot, never a host socket (design §6) |
