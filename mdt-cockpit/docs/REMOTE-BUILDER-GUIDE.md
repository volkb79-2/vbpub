# Remote BuildKit builder: secure setup guide (mTLS or SSH, nftables allowlist)

Status: DRAFT written 2026-11-17. The commands were assembled from BuildKit/buildx/OpenSSL/nftables
documentation and from a read-only inspection of the dev host. **Verified 2026-11-17:** the section 3 `openssl`
block (chain, serverAuth/clientAuth purposes, and the name constraint rejecting `evil.other.org`)
and `nft -c` syntax of the section 5 ruleset on this host (dry run, nothing loaded). **Not
verified:** the buildkitd TLS listener, the buildx remote-driver connection and the firewall
behaviour on live traffic. Run the section 7 checks before trusting a setup. Companion docs:
[BUILD-ARCHITECTURE.md](BUILD-ARCHITECTURE.md), [BUILDKITD-FLOWS.md](BUILDKITD-FLOWS.md),
`../host-setup/units/mdt-buildkitd.service.in` (the local worker this guide mirrors).

## 1. What this gives you and what it costs

`mdt-managed` is already a Buildx **remote** driver node; its endpoint is a unix socket. A remote
builder is the same node with a `tcp://` (or `ssh://`) endpoint. What you gain: builds, cache and
layer compression leave this host's `/`, CPU and IO. What stays here: the client, the build context
upload, the final `docker pull` of whatever you actually run, repack steps that run in the caller
(`docker-repack`, OCI-layout staging), and `docker load` if you still use `--load`.

Anyone who can talk to the builder's port can run arbitrary `RUN` commands on it. Treat the port as a
remote-code-execution endpoint, not as a web service.

## 2. Pick the transport

| Transport | Auth | Open port | Use when |
|---|---|---|---|
| SSH (`docker-container` driver over an SSH context) | SSH keys you already manage | none new (22) | **default choice**: one trusted builder, few clients |
| TCP + mTLS, over WireGuard/Tailscale | client certs inside a private overlay | only on the overlay address | several clients, or no Docker on the builder host |
| TCP + mTLS, public address + nftables allowlist | client certs + source IP allowlist | public | only as a last resort; the allowlist is defence in depth, never the only control |

Never expose a TCP builder port to the internet on mTLS alone: the TLS handshake and gRPC stack are
reachable pre-authentication, and a valid client key is root-equivalent on the builder's build user.

SSH variant (no PKI at all):

```bash
docker context create builder-ssh --docker "host=ssh://builder@builder.example.org"
docker buildx create --name remote-ssh --driver docker-container --use builder-ssh
docker buildx build --builder remote-ssh --push -t registry.example/img:tag .
```

The rest of this guide covers the mTLS variant.

## 3. Certificates (DNS-name based)

Model: one private CA; one **server** certificate per builder DNS name; one **client** certificate
per client host DNS name. BuildKit verifies chain and extended key usage only (it does not map a
client name to permissions), so per-client names exist for auditing and for revocation by
re-issuing; they are not an authorization layer. If different client groups must not reach each other,
run separate builders or separate CAs.

Use ECDSA P-256, short lifetimes (server and clients 90 days, CA 5 years), and keep `ca.key` on an
offline machine (or a hardware token); copy only `ca.crt` around.

```bash
# --- on the signing machine; umask keeps keys private
umask 077
mkdir -p ~/mdt-ca && cd ~/mdt-ca
BUILD_DOMAIN=build.example.org            # all names below must live under it

# 1. CA, restricted to names under BUILD_DOMAIN (Name Constraints: a stolen CA key still cannot
#    mint certificates for unrelated names; Go TLS, which BuildKit uses, enforces them)
openssl genpkey -algorithm EC -pkeyopt ec_paramgen_curve:P-256 -out ca.key
openssl req -x509 -new -key ca.key -sha256 -days 1825 -subj "/CN=mdt-build CA" \
  -addext "basicConstraints=critical,CA:TRUE,pathlen:0" \
  -addext "keyUsage=critical,keyCertSign,cRLSign" \
  -addext "nameConstraints=critical,permitted;DNS:${BUILD_DOMAIN}" \
  -out ca.crt

# 2. issue(): <name> <serverAuth|clientAuth> <days>
issue() {
  local name=$1 eku=$2 days=${3:-90}
  openssl genpkey -algorithm EC -pkeyopt ec_paramgen_curve:P-256 -out "$name.key"
  openssl req -new -key "$name.key" -subj "/CN=$name" -out "$name.csr"
  openssl x509 -req -in "$name.csr" -CA ca.crt -CAkey ca.key -CAcreateserial \
    -days "$days" -sha256 -out "$name.crt" -extfile <(printf '%s\n' \
      "basicConstraints=critical,CA:FALSE" \
      "keyUsage=critical,digitalSignature" \
      "extendedKeyUsage=$eku" \
      "subjectAltName=DNS:$name")
  rm -f "$name.csr"
}

issue builder.${BUILD_DOMAIN}  serverAuth 90      # the name clients dial
issue gstammtisch.${BUILD_DOMAIN} clientAuth 90   # one per client host

# 3. check what you made
openssl x509 -in builder.${BUILD_DOMAIN}.crt -noout -subject -ext subjectAltName,extendedKeyUsage -dates
openssl verify -CAfile ca.crt -purpose sslserver builder.${BUILD_DOMAIN}.crt
openssl verify -CAfile ca.crt -purpose sslclient gstammtisch.${BUILD_DOMAIN}.crt
```

Distribution: server gets `ca.crt`, `builder.*.crt`, `builder.*.key`; each client gets `ca.crt`, its
own `*.crt` and `*.key` only. Never copy `ca.key`.

There is no CRL support in buildkitd. Revoke by: removing the client IP from the firewall allowlist
(section 5), letting the 90-day certificate expire, and, after a key compromise, rotating the CA and
re-issuing everything.

## 4. The builder (server side)

Run the same rootless container the local worker uses (`mdt-buildkitd.service.in`), with a TCP
listener bound to a **specific private address**, never `0.0.0.0`. `buildkitd.toml`:

```toml
[grpc]
  address = ["tcp://10.8.0.1:1234"]          # overlay or private IP only
[grpc.tls]
  cert = "/etc/buildkit/certs/builder.crt"
  key  = "/etc/buildkit/certs/builder.key"
  ca   = "/etc/buildkit/certs/ca.crt"        # setting ca makes client certificates mandatory

[worker.oci]
  max-parallelism = 2
  gc = true
  gckeepstorage = "50GB"
```

Container changes relative to the local unit: publish the port on the same address
(`-p 10.8.0.1:1234:1234`, so Docker's own rules are the only listener), mount the certificate
directory read-only (`-v /etc/mdt/builder-certs:/etc/buildkit/certs:ro`; the key must be readable by
the container's uid 1000, mode `0640` with matching group, not world-readable), keep
`--cgroup-parent=dev-buildkitd.slice` (plain `docker run`, never a Buildx driver; see
`plan-buildkitd-service.md` for why), and keep the cache on its own btrfs subvolume. Keep
entitlements off (no `--allow-insecure-entitlement`).

Compression for the cache subvolume, applied once on the builder host:

```bash
btrfs property set /srv/<pool>/buildkitd-cache compression zstd   # new writes only
# existing data (breaks reflinks/snapshot sharing, check `btrfs subvolume list` first):
# btrfs filesystem defragment -r -czstd /srv/<pool>/buildkitd-cache
```

## 5. Firewall on a Debian 13 host: nftables, in a separate table

Inspected state of this host (the builder host will have the same setup):

- `iptables` is **iptables-nft 1.8.11** (the nf_tables backend); `nftables` 1.1.3 is installed but the
  `nftables.service` is inactive and `/etc/nftables.conf` is the stock 15-line file.
- The only tables are Docker's `ip/ip6 nat|filter|raw`, marked "managed by iptables-nft, do not
  touch". The `filter` table has **no INPUT chain at all**, so the host currently does no input
  filtering. Listeners on 22, 53, 80, 443, 2022, 8443, 9443 are reachable from wherever the network
  allows. Fix that on any builder host (optional baseline below).

Decision: write new rules with **nft in your own `inet` table**; leave Docker's iptables-nft tables
alone. Do **not** use the stock `/etc/nftables.conf`: it begins with `flush ruleset`, which deletes
Docker's rules. Never run `nft flush ruleset` on this host.

Compatibility (as of Docker 29.9, Tailscale and CrowdSec docs read 2026-11-17):

| Component | nftables status | Recommendation |
|---|---|---|
| Docker | `firewall-backend: nftables` was introduced in 29.0.0 and is documented as **experimental**; it also drops `DOCKER-USER` and does not support Swarm/overlay | stay on iptables-nft for now; protect published ports from your own table on the `forward` hook |
| Tailscale | supports `iptables` and `nftables`; default is `iptables` unless `TS_DEBUG_FIREWALL_MODE=auto\|nftables`; the variable is documented as temporary | leave default; verify with `journalctl -u tailscaled \| grep router:` after installing |
| CrowdSec bouncer | `crowdsec-firewall-bouncer-nftables` is native: its own tables `crowdsec`/`crowdsec6` on the `input` hook | compatible with your own table; to also protect Docker-published ports it needs a forward-hook rule (the docs only describe `DOCKER-USER` for iptables mode), so keep the allowlist below as the primary control |
| iptables-nft | Docker's backend today | coexists with a separate `inet` table; every base chain on a hook is evaluated and any `drop` wins |

Ruleset (`/etc/mdt/builder-fw.nft`). Published container ports are DNAT'ed in `prerouting`, so they
are seen on the **`forward`** hook, not `input`; match the original destination port with
`ct original proto-dst`. An `accept` in this table only ends this table's chain, so Docker's own
filtering still applies afterwards. IPv4 and IPv6 need separate rules, because `ip saddr` in an
`inet` rule silently matches IPv4 only.

```nft
table inet mdt_builder
delete table inet mdt_builder            # idempotent reload

table inet mdt_builder {
  set builder_clients4 {
    type ipv4_addr; flags interval
    elements = { 203.0.113.10, 198.51.100.0/29 }      # whitelisted client addresses
  }
  set builder_clients6 {
    type ipv6_addr; flags interval
    elements = { 2001:db8::10 }
  }

  # builder port reached through a Docker-published port (rootless container, -p IP:1234:1234)
  chain forward {
    type filter hook forward priority -10; policy accept;
    meta l4proto tcp ct original proto-dst 1234 meta nfproto ipv4 ip  saddr @builder_clients4 accept
    meta l4proto tcp ct original proto-dst 1234 meta nfproto ipv6 ip6 saddr @builder_clients6 accept
    meta l4proto tcp ct original proto-dst 1234 drop
  }

  # same port if buildkitd ever listens on the host itself (--network host, native service)
  chain input {
    type filter hook input priority -10; policy accept;
    tcp dport 1234 meta nfproto ipv4 ip  saddr @builder_clients4 accept
    tcp dport 1234 meta nfproto ipv6 ip6 saddr @builder_clients6 accept
    tcp dport 1234 drop
  }
}
```

Load and persist (a oneshot unit, because the Debian `nftables.service` would flush Docker's rules
if you point it at a file that starts with `flush ruleset`):

```ini
# /etc/systemd/system/mdt-builder-fw.service
[Unit]
Description=mdt remote-builder allowlist (own nft table)
After=network-pre.target
Before=network.target docker.service

[Service]
Type=oneshot
RemainAfterExit=yes
ExecStart=/usr/sbin/nft -f /etc/mdt/builder-fw.nft
ExecReload=/usr/sbin/nft -f /etc/mdt/builder-fw.nft
ExecStop=/usr/sbin/nft delete table inet mdt_builder

[Install]
WantedBy=multi-user.target
```

```bash
nft -c -f /etc/mdt/builder-fw.nft          # syntax check, changes nothing
systemctl enable --now mdt-builder-fw.service
nft list table inet mdt_builder
```

Update the allowlist at runtime without touching the file:
`nft add element inet mdt_builder builder_clients4 { 203.0.113.99 }`, then mirror it into the file.

Optional baseline for the `input` hook (the host currently has none). Apply with a rollback timer so
a mistake cannot lock you out of SSH; Docker-published ports are on `forward` and unaffected by an
`input` default drop, but host-listening services (sshd, DNS) are:

```bash
systemd-run --on-active=120 --unit=fw-rollback nft delete table inet mdt_baseline   # safety net
nft -f - <<'EOF'
table inet mdt_baseline {
  chain input {
    type filter hook input priority -5; policy drop;
    ct state established,related accept
    iifname "lo" accept
    ip protocol icmp accept
    ip6 nexthdr ipv6-icmp accept
    tcp dport 22 accept            # narrow this to your admin addresses
    # add every host-listening service you intend to expose; the dev host also listens on
    # 53, 80, 443, 2022, 8443, 9443 (Wings/panel stack): list them deliberately.
  }
}
EOF
# after verifying a NEW ssh session works:
systemctl stop fw-rollback.timer
```

## 6. Client side

```bash
# on the client host (or in the devcontainer; mount the files read-only)
install -d -m 0700 ~/.config/mdt-builder
cp ca.crt gstammtisch.build.example.org.{crt,key} ~/.config/mdt-builder/

docker buildx create --name remote-builder --driver remote \
  --driver-opt cacert=$HOME/.config/mdt-builder/ca.crt,cert=$HOME/.config/mdt-builder/gstammtisch.build.example.org.crt,key=$HOME/.config/mdt-builder/gstammtisch.build.example.org.key,servername=builder.build.example.org \
  tcp://builder.build.example.org:1234

docker buildx inspect remote-builder --bootstrap
docker buildx build --builder remote-builder --push -t ghcr.io/ORG/img:tag .
```

The name in `tcp://` and `servername` must equal a SAN of the server certificate. Prefer a DNS name
that resolves to the overlay address. For MDT callers set `BUILDX_BUILDER=remote-builder` the way
`BUILDX_BUILDER=mdt-managed` is set today; the `mdt-buildkit-guard` approval list needs the new
node name if you do not want it flagged.

Delivery paths (see BUILDKITD-FLOWS section 3): use `--push`; `--output type=oci,dest=-` streams
the whole image through the client; `--load` streams it through the client **and** writes a second
copy into this host's overlay2 on `/`. `FROM <local-tag>` and devcontainer features cannot work on
any remote builder, because it cannot see this host's Docker store.

## 7. Verification checklist

1. `openssl verify` lines in section 3 print `OK`.
2. From an allowlisted client:
   `openssl s_client -connect builder.build.example.org:1234 -CAfile ca.crt -cert CLIENT.crt -key CLIENT.key -verify_return_error -brief </dev/null`
   completes the handshake.
3. Same command without `-cert/-key`: the server must reject it (`certificate required` alert).
4. From a host not in the allowlist: connect times out (dropped, not refused).
   `nft list chain inet mdt_builder forward` shows the drop counter increase.
5. `docker buildx inspect remote-builder --bootstrap` lists the node and platforms.
6. `docker buildx build --builder remote-builder --push` of a trivial image succeeds, and
   `cat /proc/<buildkit runc pid>/cgroup` on the builder shows `dev-buildkitd.slice`.
7. After a reboot of the builder: `nft list table inet mdt_builder` is present before Docker starts
   publishing the port, and the allowlist still blocks an unlisted client.
8. Reissue test: renew a client certificate before expiry and restart buildkitd on the builder after
   renewing the server certificate (it reads the files at start).
