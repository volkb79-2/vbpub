# CIU Proposal — Remote Host Enrollment (`ciu host enroll`) — revision 2

**Status:** PROPOSAL, accepted in shape by the operator on 2026-09-03 for **both lines**: a backport package on ciu v7 (`SPEC.md` S14.7) and the same verb in v8 (`SPEC-V8.md` draft.7 S7.2.4, carve row V8-29). Filed as **CIU-93**; the cmru half is **KI-24**. Not implemented.
**Revision 2 (2026-09-03)** replaces revision 1's token-authenticated bootstrap URL, callback and tls-edge endpoint with a two-step flow that needs no infrastructure: the control host prints a one-liner carrying the public key; the target's installer installs ciu and the key and prints its host-key fingerprint; the control host pins the fingerprint the operator read. The operator's refinement, recorded in §9.
**Author:** dstdns controller session (rev 1, from decision D-097/D-358); ciu v8 design session (rev 2).
**Relationship to other documents:** additive to `SPEC.md` S14 (v7) and `SPEC-V8.md` S7.2/S17 (v8); it happens **before** any of that machinery is reachable and requires of either schema only "write one host-inventory row and one key file".

---

## 0. Where this comes from

dstdns decision **D-097** (2026-08-19) designed and deferred "self-hosted `get.py --bootstrap-url --token` (behind tls-edge, GitHub-independent)" as the enrollment story of the remote-deployment transport. dstdns-P171's carve (2026-09-02/03) confirmed live that enrolling a fresh host is still fully manual (`ssh-keygen`, out-of-band key distribution, `ssh-keyscan`, a hand-written TOML row). Decision **D-358** (2026-09-03) formalized revision 1 of this proposal upstream as CIU-93. The operator reviewed it the same day and asked for the simpler shape this revision specifies.

## 1. The gap (unchanged)

Both spec lines fully specify transport, push and activation for an **already-trusted** host — a working `ssh_key`, a pinned `known_host`, a reachable `ssh_host` in the inventory (v7 S14.1–S14.6; v8 S7.2, S17.1–S17.4) — and neither says how a host that has never spoken to this checkout gets its first inventory row. The v8 activation state machine additionally *requires* the `ciu` executable on every target (`prepare` runs `ciu instance init --host` and `ciu check` there, S17.4.1), so "enroll" must also mean "install ciu".

## 2. The design in one paragraph

`ciu host enroll <name>` on the control host generates an ed25519 key pair into the project's own state (the private half never moves) and prints one command for the target's admin. That command fetches ciu's own `get.py` installer at a pinned version and runs its `enroll` subcommand with the **public** key and the controller's name: the installer installs ciu transactionally, refuses without an SSH server, creates or confirms the deploy user, appends the key to that user's `authorized_keys`, and prints the host's SSH host-key fingerprint together with the addresses it sees. The operator then runs `ciu host enroll <name> --ssh-host <addr> --fingerprint <as printed>`: ciu keyscans the host, refuses unless the fingerprint matches what the admin read on the console, connects with the key, runs `ciu version` on the target as proof, writes the inventory row with the pinned `known_host`, and prints it. No token, no callback, no listener, no new trust primitive, no cmru download backend.

## 3. Step 1 — the control host: `ciu host enroll <name>`

```
ciu host enroll <name> [--user USER] [--port N] [--controller FQDN] [--from PATTERN] [--docker]
                       [--installer-url URL] [--global] [--replace]
```

1. **Key generation, control-side.** An ed25519 key pair is generated with the platform's `ssh-keygen` (never a hand-rolled implementation) into the project's own state: **v8** `<state root>/ciu-ssh/<name>` and `<name>.pub` (directory 0700, key 0600; `ciu-ssh/` joins the state-root and gitignore lists, S2.6/S2.3.1, and the backup set, S14.8.1); **v7** `<repo>/.ciu/secrets/hosts/<name>/ssh_key` and `ssh_key.pub` (the S14.3a host-scoped namespace, 0700/0600). The key comment is `ciu@<controller>:<project>`. The private half is written once and read only by SSH; it is never printed, logged, copied into a bundle, or sent anywhere.
2. **The controller name.** `--controller` defaults to the control host's declared name — v8: the `fqdn` of the `local = true` host (S7.2); v7: `topology.external.public_fqdn` when declared — and is **required** when neither exists. It is data for the key comment and for the printed line; it is never a callback address.
3. **The installer URL.** The one-liner names ciu's own `get.py` (rendered by `cmru get-py --project ciu`, committed at `ciu/get.py` and shipped as a release asset) **pinned to the control host's own ciu version**, `https://github.com/<owner>/<repo>/releases/download/ciu-v<version>/get.py`, so control and target run the same ciu; `--installer-url` overrides it (a self-hosted mirror, a commit-pinned raw URL). The owner/repo come from the constants cmru bakes into the package at release time; nothing is guessed at run time.
4. **Print, then stop.** The verb prints the key location, the public key, and two commands — the target's one-liner (§4) and the control host's completion command (§5) — and exits 0. Nothing is written to the inventory yet; a partial enrollment leaves only the key files, which `ciu host enroll <name> --abort` removes. There is no polling and no listener: the admin may run the one-liner an hour later.
5. **Refusals** (`[S7.2]` in v8, `[S14.7]` in v7): `<name>` already in the inventory without `--replace`; no controller name; `ssh-keygen` absent; the key directory not writable. `--replace` regenerates the key and, at step 2, overwrites `ssh_key` and `known_host` of the existing row — the rotation path.

The printed one-liner:

```
curl -fsSL https://github.com/<owner>/vbpub/releases/download/ciu-v8.0.0/get.py \
  | sudo python3 - enroll --authorized-key 'ssh-ed25519 AAAA… ciu@gstammtisch.dchive.de:dstdns' \
      --controller gstammtisch.dchive.de --user ops --name rs1002
```

`--from PATTERN` adds an `authorized_keys` `from="PATTERN"` restriction to the printed key line; it is **opt-in** because OpenSSH matches `from=` against the client's source address or reverse-DNS name, which differs from the controller's FQDN behind NAT and on a mesh. `--docker` asks the installer to add the deploy user to the `docker` group (needed on every host that will run `ciu up --host`, not on a `docker_optional` host).

## 4. The target: `get.py enroll` (a cmru template subcommand, KI-24)

`get.py` is the estate's existing transactional installer (`cmru get-py`, proven live in `vbpub/tls-edge/get.py`: `releases/<tag>` + atomic `current`, SHA256 + minisign manifest verification, prerequisite checks before any network I/O, an exclusive flock, a `bootstrap|apply|health|rollback` adapter seam). Revision 2 adds one subcommand to `cmru/templates/get.py.tmpl`, available to **every** project that renders `get.py`:

```
get.py [--manifest-pubkey …] enroll --authorized-key 'KEY' --controller FQDN
       [--user USER] [--name NAME] [--from PATTERN] [--docker] [--no-install] [--scope system]
```

In order, fail-fast, and idempotent on re-run:

1. **Prerequisites before any network I/O** (the template's existing `check_prerequisites` posture): Linux; root (or `sudo`); an SSH server present (`sshd` on `PATH` or `/usr/sbin/sshd`) — absent → `EXIT_PREREQ` naming the package (`openssh-server`); the key line parses as `<type> <base64>[ <comment>]` with `type ∈ ssh-ed25519 | ecdsa-sha2-* | sk-* | ssh-rsa` → else `EXIT_CONFIG`. The installer never installs system packages.
2. **Install** the project exactly as `get.py install --scope system` does (transaction, verification, `current` switch); `--no-install` skips it for a host that only needs the key. For ciu this puts the `ciu` executable on the target, which v8's `prepare` requires (S17.4.1) and v7's `ciu up --host` render-on-target requires (S14.2).
3. **Deploy user**: `--user` (default `ciu`) is created when absent (`useradd --create-home --shell /bin/bash`), left untouched when present; with `--docker`, added to the `docker` group when that group exists, refused with `EXIT_PREREQ` when it does not.
4. **Authorized key**: `~USER/.ssh/` 0700 and `authorized_keys` 0600, both owned by the user; the key line (with the `from=` prefix when `--from` was given) is appended **once** — an identical line already present is reported, not duplicated; a line with the same key and a different prefix is refused (`EXIT_CONFIG`, "key present with different options; remove it by hand").
5. **Report**: the fingerprint of every host key in `/etc/ssh/ssh_host_*_key.pub` (`ssh-keygen -lf`, SHA256 form), the addresses the host sees (`hostname -I`, best effort, labelled as unconfirmed), the deploy user, the installed project version, and the exact completion command for the controller: `ciu host enroll <NAME> --ssh-host <address> --fingerprint SHA256:<ed25519 fingerprint>` (`<NAME>` from `--name`, else a placeholder the operator fills in).

What `enroll` deliberately does **not** do: generate a key pair, call anything back, open a listener, install packages, run deploy logic, or touch `sshd_config`. Its blast radius is one user and one `authorized_keys` line plus the project install.

## 5. Step 2 — the control host: `ciu host enroll <name> --ssh-host ADDR --fingerprint FP`

```
ciu host enroll <name> --ssh-host ADDR [--port N] [--user USER] --fingerprint SHA256:… [--global] [--replace]
```

1. **Keyscan** the host (`ssh-keyscan -p N -t ed25519,ecdsa,rsa ADDR`); compute the SHA256 fingerprint of each returned key; refuse unless one equals `--fingerprint` (`[S7.2] rs1002.dchive.de presents SHA256:Q7… ; expected SHA256:M2… — not the host the admin enrolled, or a man in the middle`). Without `--fingerprint` the verb prints the scanned fingerprints and asks for confirmation on a TTY; non-interactive without the flag is a refusal. This is TOFU with a second channel — the fingerprint the admin read on the console — the same trust model the specs already accept (v7 S14.4a, v8 S7.2's `known_host`), automated and logged rather than manual.
2. **Prove the key and the install**: connect as USER with the generated key and the scanned host key (pinned for this connection only), run `ciu version`; a refused login or a missing `ciu` is an ERROR naming which (`[S7.2] rs1002: key accepted but ciu is not installed; run get.py enroll again without --no-install`).
3. **Write the row** with a round-trip writer that preserves the operator's other tables and comments — never a rewrite of the whole file — into the inventory: **v8** `ciu.hosts.toml` in the checkout root (or `~/.config/ciu/hosts.toml` with `--global`, S17.1): `[hosts.<name>] ssh_host, ssh_user, ssh_port (only when ≠ 22), ssh_key = "<state root>/ciu-ssh/<name>", known_host = "<algo> <base64>"` (the `[ADDR]:N` form for a non-default port); **v7** `.ciu.hosts.toml`: the same keys under `[deploy.hosts.<name>]` with `ssh_key = "<repo>/.ciu/secrets/hosts/<name>/ssh_key"`. Nothing else is written — `bundle_dir`, `docker_optional`, `[activate]`, addresses and `fqdn` stay the operator's to add, exactly as for a hand-written row.
4. **Print the row** and exit 0. From here every existing verb works: `ciu ssh <name>`, `ciu up --host <name>`, v8 `push`/`activate`.

## 6. The v7 backport and the v8 form are the same design

| | v7 (`SPEC.md` S14.7, backport package) | v8 (`SPEC-V8.md` S7.2.4, V8-29) |
|---|---|---|
| verb | `ciu host enroll` (a `host` group beside the flat `host-secrets`) | `ciu host enroll` |
| key location | `.ciu/secrets/hosts/<name>/ssh_key[.pub]` (S14.3a namespace) | `<state root>/ciu-ssh/<name>[.pub]` (S2.6) |
| inventory row | `[deploy.hosts.<name>]` in `.ciu.hosts.toml` | `[hosts.<name>]` in `ciu.hosts.toml` |
| pinning | `known_host`, S14.4a fail-closed, S14.4c port form | `known_host`, S7.2 |
| installer | ciu's `get.py` (new: `cmru get-py --project ciu`, committed + released) | the same, pinned to the control's ciu 8.x |
| what the target needs ciu for | `ciu up --host` render-on-target (S14.2); `--thin` hosts may use the installed `get.py` as their `activate` entrypoint (S14.6) | `prepare`/`apply` (S17.4.1); `docker_optional` hosts alike |

## 7. Security posture

- The private key is generated where it is used and never leaves the control host's state; the public key and the controller's name are the only values that cross the wire, both public by nature.
- `curl | python3` is the estate's existing installer posture; the printed URL is **version-pinned** (a release asset of the control's own ciu), never `latest`, and `--installer-url` lets an estate serve the same file from its own mirror. Manifest verification (SHA256 + minisign) covers the installed release exactly as for every other `get.py` install.
- Host-key trust is TOFU confirmed through a second channel (the console fingerprint); the verb refuses on mismatch and writes nothing before the match. `CIU_SSH_INSECURE_TOFU=1` remains the documented escape and is never set by this verb.
- The target-side blast radius is one user, one `authorized_keys` line and the project install; `from=` restrictions are opt-in and explicit.
- Nothing listens: no token, no endpoint, no window in which a third party can present itself as the target.

## 8. Oracles for the carve (both lines)

- **O1** Step 1 creates the key pair with modes 0700/0600, prints a one-liner that contains the exact public key, the pinned installer URL and the completion command, and writes no inventory row; a second step 1 without `--replace` is refused.
- **O2** `get.py enroll` in a fixture container with `openssh-server`: creates the user, appends the key once (a re-run reports "already present" and leaves one line), sets modes and ownership, and prints a SHA256 fingerprint equal to `ssh-keygen -lf /etc/ssh/ssh_host_ed25519_key.pub`.
- **O3** `get.py enroll` in a fixture without an SSH server exits `EXIT_PREREQ` naming `openssh-server` and performs no network I/O (assert no download happened).
- **O4** Step 2 with a wrong `--fingerprint` refuses and writes nothing; with the right one it writes exactly the specified row (round-trip: the operator's other tables and comments survive byte-for-byte) and `ciu ssh <name> -- ciu version` then succeeds.
- **O5** Controlled wrong implementations that must fail: one that writes the row before the fingerprint check (O4); one that prints or logs private-key material (a grep oracle over stdout/stderr/logs for the private key's first line); one that rewrites the inventory file whole (O4's byte-for-byte comparison).
- **O6** The cmru template: `cmru get-py --project ciu` renders a `get.py` whose `enroll --help` lists exactly the §4 flags, and the rendered script is byte-identical to the committed `ciu/get.py` at release.

## 9. Operator direction 2026-09-03 (what revision 2 changed and why)

The operator read revision 1 and proposed the printed one-liner carrying the public key and the controller's name, "not using bootstrap". Applied with three refinements: the target-side mode is `enroll`, separate from the `bootstrap|apply|health` activation verbs; `--controller` names the key comment and never a callback (`from=` restrictions opt-in); the URL is version-pinned. Consequences: revision 1's §3.2 (token-authenticated bootstrap `get.py` variant), §3.3 (the callback), §6 (the cmru self-hosted download backend as a prerequisite) and §7's questions 1–3 (interactive vs async, `--host-hint`, the endpoint's location) are withdrawn — a two-step verb has no polling, the operator supplies the address, and no endpoint exists. D-097's "GitHub-independent" wish is met by `--installer-url` and a self-hosted mirror, not by a new download backend; a self-hosted backend for the *wheel* download stays an optional cmru item. Question 4 is settled: CIU-93. The decision to backport to v7 is the operator's (2026-09-03), an explicit exception to v7's maintenance-only posture because dstdns needs remote placement before ciu8 ships.

## 10. What this proposal does not do

It does not touch push/activate mechanics in either line, does not add inventory keys, does not introduce Vault into the pre-trust path (D-097), and does not auto-configure `bundle_dir`, `docker_optional` or `[activate]`. Enrollment ends at "ciu can SSH in, and ciu is installed there".
