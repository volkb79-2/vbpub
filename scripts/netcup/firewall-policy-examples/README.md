# Firewall policy examples

These files are `FirewallPolicySave` request bodies. From `scripts/netcup/`,
validate and create one with:

```bash
SERVER_ID=799611
./scp-api.py firewall-policies create \
  --policy-file firewall-policy-examples/public-ssh-whitelist.json \
  --yes --json
```

Use `--yes` only after reviewing the policy and checking the server's current
`ingressImplicitRule`/`egressImplicitRule`. A policy assignment replaces the
complete user/copied policy assignment on the selected interface:

```bash
./scp-api.py firewall "$SERVER_ID" get --consistency-check
./scp-api.py firewall "$SERVER_ID" set --user-policy-id POLICY_ID --active
```

Capture the policy ID from the JSON response before the assignment, then
replace `POLICY_ID` with that value. Omit the firewall MAC only when the server
has exactly one interface; for multiple interfaces use
`firewall "$SERVER_ID" MAC get` and pass the same MAC to `set`.

The addresses in these examples are documentation ranges except for the
private ranges; replace them with the real administrator, VPN, and resolver
addresses.

The SCP firewall is attached to a Netcup server interface identified by its
MAC. A WireGuard `wg0` interface inside the guest is not a separate SCP
interface. `wireguard-management.json` therefore permits only the public UDP
handshake and drops the other provider-level ingress protocols; the guest
firewall must additionally allow SSH/services only on `wg0` and deny them on
the public guest interface. If WireGuard is provided by a separate Netcup NIC,
assign an appropriate policy to that NIC's MAC as well.

The egress example assumes ordered rules and an initially permissive egress
implicit rule: allowed web/DNS/NTP traffic appears before protocol-wide drops.
Verify the applied result with `--consistency-check` and test from an
out-of-band console before relying on a restrictive policy.
