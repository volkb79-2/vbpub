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
cgroup = "dev.slice/dev-buildkitd.slice"
```

`cgroup` is relative to the cgroup v2 root and includes its parent slice. This
example targets the estate's `dev.slice/dev-buildkitd.slice`; on another host,
replace it with an existing cgroup path relative to the cgroup v2 root, or remove
the `[[static_unit]]` block when no static candidate is wanted. Static paths are
not constrained by `allowed_ancestors`, but a path under a protected ancestor is
refused.

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

## 2. Install and start under systemd

From the project root, obtain the release binary and save the TOML example in
§1 as `thrashguard.toml`. Then install the binary, config, both groups, all four
unit files, and the tmpfiles rules:

```bash
sudo install -D -m 0755 target/release/thrashguard /usr/local/bin/thrashguard
sudo install -D -m 0640 thrashguard.toml /etc/thrashguard/thrashguard.toml
sudo groupadd --force --system thrashguard-registrant
sudo groupadd --force --system thrashguard-reader
sudo install -m 0644 deploy/systemd/thrashguard.service deploy/systemd/thrashguard-reader.socket deploy/systemd/thrashguard-registrant.socket deploy/systemd/thrashguard.slice /etc/systemd/system/
sudo install -D -m 0644 deploy/tmpfiles.d/thrashguard.conf /etc/tmpfiles.d/thrashguard.conf
sudo systemd-tmpfiles --create /etc/tmpfiles.d/thrashguard.conf
sudo systemctl daemon-reload
```

`thrashguard.service` requires and orders itself after the reader socket. Both
shipped socket units use `Accept=no` and name that same service with `Service=`;
systemd passes the active listeners to it. Start the service itself so it begins
observing at boot even if no client ever connects.

For `registrant = true`, install this service drop-in before reloading systemd:

```bash
sudo install -d -m 0755 /etc/systemd/system/thrashguard.service.d
sudoedit /etc/systemd/system/thrashguard.service.d/registrant.conf
```

The complete drop-in content is:

```ini
[Unit]
Requires=thrashguard-registrant.socket
After=thrashguard-registrant.socket
```

Then enable both sockets and the service:

```bash
sudo systemctl daemon-reload
sudo systemctl enable --now thrashguard-reader.socket thrashguard-registrant.socket thrashguard.service
```

For `registrant = false`, set `registrant = false` in the installed config,
do not install that drop-in, and keep the optional registrant socket disabled.
For a fresh install, enable only the reader socket and the service:

```bash
sudoedit /etc/thrashguard/thrashguard.toml
sudo systemctl disable --now thrashguard-registrant.socket
sudo systemctl enable --now thrashguard-reader.socket thrashguard.service
```

When changing an existing true-mode installation to false mode, first remove
the exact drop-in if it exists and reload systemd:

```bash
sudo rm -- /etc/systemd/system/thrashguard.service.d/registrant.conf
sudo systemctl daemon-reload
```

The service then adopts only the reader descriptor, as required by the
configuration. Check the service status and journal after start; a missing or
unreadable static cgroup is a configuration refusal, not an empty candidate.

`Accept=no` on both sockets means one long-lived daemon owns the listener, so a
restart never refuses a client that connects during it. Group gating controls
which socket a process can reach; it does not bind a registration id to the peer
that created it. Any process that can reach the registrant socket can update,
heartbeat or deregister any id. Container root can bypass socket DAC, and scope
resolution constrains which cgroup is named but does not establish registration
ownership. That ownership check is not implemented in P1 and blocks P2 enforce
(TG-12).

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

`reason` is one of `none` (no scope found), `several` (more than one match at
the shallowest matching depth), `protected` (the only match is under a protected
ancestor), `malformed` (the request itself) or `duplicate` (this id is already
registered). A deeper same-name match loses to the shallowest match and appears
in the `registered` event's `shadowed` paths.
`priority` is exactly `freezable` or `last-resort`. Other wire refusals use the
closed reason vocabulary `unreadable`, `missing`, `wrong-socket`, `unknown-key`,
`unknown-verb`, `version`, `too-large`, `too-many-sessions` and `value`; readers
should preserve these names when reporting a refusal.

## 4. A reader asks what the guard sees

```bash
printf '%s\n' '{"v":"thrashguard-state/1","verb":"status"}' \
  | socat - UNIX-CONNECT:/run/thrashguard/reader/thrashguard.sock

printf '%s\n' '{"v":"thrashguard-event/1","verb":"events","since_seq":17}' \
  | socat - UNIX-CONNECT:/run/thrashguard/reader/thrashguard.sock
```

The local `events` command reads the rotated generation before the current one.
It returns at most 1024 rows and a 64 KiB JSON line; `truncated: true` means the
requested window has a missing prefix or the page hit one of those bounds. Page
again using the last returned row's `seq` as `--since-seq`. A single row too large
to fit is refused with a named error.

In `status.psi`, the `some.avg10` and `full.avg10` values are integer hundredths:
`300` means `3.00`; `total_us` is microseconds.

## 5. What an operator should expect from P1

- **Nothing is ever frozen.** `mode = report` journals `would-freeze` and
  `would-thaw`; `mode = enforce` is refused with a one-line reason until P2.
- **`unknown` is reported, not `0.00`.** Remove or unreadable pressure gives
  `psi: {"v": null, "reason": ...}` and a `unknown-pressure` event.
- **A frozen interval is never recorded** because no interval can be frozen yet.
  The journal holds decisions, with `seconds: 0` and an explicit `simulated` field.
- **The journal holds transitions.** A trigger that holds writes one
  `would-freeze` (or one `would-hold` naming `no-candidates`, `max-parked`,
  `never-park-last` or `thaw-grace`); a release writes one `would-thaw`; silent
  registrations write one `expired` each. The same decision is not repeated every
  tick — ask `status`/`candidates` for the current one.
- **A `registered` row may include `shadowed`.** It lists deeper same-name
  scopes that lost to the selected shallow scope, as paths relative to the
  injected cgroup root.
- **`rule-unknown` means the configured rule has no readable input.** `refault`
  and `smallest-enough` are P3; until then the replies carry
  `"rule":"newest","rule_reason":"rule-unknown"` rather than a zero rate.
- **`serve` starts the daemon; it is not a client verb.** With systemd it adopts
  the reader descriptor and, when `registrant = true`, the registrant descriptor;
  run by hand it binds `--registrant-socket` and
  `--reader-socket` (or the default paths). `mode = off` prints one line and
  exits 0 without binding; `mode = enforce` is refused with exit code 2.
- **Foreground socket permissions depend on the caller.** The by-hand fallback
  creates parent directories with the process identity and uses its umask; it
  does not set the systemd socket groups or modes. The documented access table
  applies to the systemd/tmpfiles path. If a bind reports an existing path,
  first confirm no daemon owns it; then remove that exact stale socket and retry.

## 6. Consumers

| Name | Today |
|---|---|
| `thrashguard status` / `candidates` | works, no daemon socket needed when given `--config` |
| run-gate (registrant) | P4; until then run-gate does not register anything and behaves exactly as today |
| assay | reads freeze events through hostobs, never this socket (design §4b) |
| fleetd / agctl | read through hostobs and one estate snapshot, never a host socket (design §6) |

## 7. Fresh-host acceptance remains open

No fresh-host systemd run has happened for P1. The following nine checks are
still open and must be run on a disposable fresh systemd and cgroup v2 host;
file-backed tests do not substitute for them:

1. Install the binary, the config from the corrected example above, both groups,
   all four units and the tmpfiles file. Run `systemd-tmpfiles --create`, then
   `systemd-analyze verify`.
2. `thrashguard status --config ...` exits 0 with every static unit resolved.
3. Enable both sockets and the service, then reboot. With no client contact, the
   service is active in `thrashguard.slice`, with no namespace or activation
   errors and no restart loop.
4. Directories are `0755` and `0750` with the right groups; sockets are `0660`
   `root:<group>`.
5. As unprivileged group members: reader `status` works with non-null PSI, a real
   container under `dev-gates.slice` registers, and each group and a non-member
   get EACCES on the sockets they should not reach.
6. A `would-*` or `registered` row appears. After `systemctl restart`, clients
   still connect, seq continues, and `events --socket` with `since_seq=0` is
   honest.
7. With `registrant = false` and the registrant socket disabled, the service
   starts and serves only the reader.
8. A bad config exits 2 without looping.
9. Every `cgroup.freeze` stays 0 throughout.
