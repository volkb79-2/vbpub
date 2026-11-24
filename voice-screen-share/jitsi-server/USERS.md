# Jitsi users and credentials

Authentication is Jitsi secure-domain with prosody **internal** accounts. Nobody joins without
logging in (`jitsi.auth.guests = false`). Accounts are declared in `ciu.defaults.toml.j2`
(`[[jitsi.users]]`): `admin`, `alice`, and `bob`, all registered on every `ciu up`. Jitsi internal auth
has no separate admin role: `admin` is simply a normal account kept for the operator.

## Hostdir permissions

Since stable-11248 the images run as the unprivileged user `s6` (uid 1000). All hostdirs
(`vol-*-config`, plus `vol-prosody-data` = `/var/lib/prosody`, the account store) are `1000:1000`,
`0750`: no group-write or world access. Rendered configs live in tmpfs (`/run/prosody/config`), so
the account records are under `vol-prosody-data/data/`. On a ciu run in a devcontainer where the
S6.5 root helper cannot verify its cgroup slice, create the dirs once with a one-shot root
container and `chown 1000:1000` (ciu then accepts them, S6.3).

## Where the credentials live

Each password is one ciu `GEN_LOCAL` secret: a mode-0600 file in a 0700 directory, git-ignored under
`**/.ciu/`. ciu generates each value on first materialization and reuses it on later runs. The hook writes no
aggregate credentials file and removes an obsolete `.ciu/credentials.txt` copy left by an older checkout. From
the repository root, the exact commands are:

```bash
cat voice-screen-share/jitsi-server/.ciu/secrets/jitsi/user_admin
cat voice-screen-share/jitsi-server/.ciu/secrets/jitsi/user_alice
cat voice-screen-share/jitsi-server/.ciu/secrets/jitsi/user_bob
ciu secrets list --dir voice-screen-share/jitsi-server
```

`ciu secrets list` shows names and store paths without values. The three service secrets
(`jicofo_auth_password`, `jvb_auth_password`, `jicofo_component_secret`) live next to the account files.
The Jitsi images take those service secrets from the container environment, so anyone with docker access can
read them there.

The login name in the Jitsi UI is the bare username (`admin`), not `admin@meet.jitsi`.

## How accounts are created (idempotent)

After the stack is healthy, the `post_compose` hook reads all three password files through `ctx.secret_file()`.
It then runs `prosodyctl register <user> meet.jitsi` for each inside the prosody container, with the password on
stdin. Registering an existing user sets its password to the stored value, so every `ciu up` converges to the
same state. Within the container, for the instant that process runs, the password is an argument of
`prosodyctl`; it is never on the host command line.

## Rotate one user

```bash
ciu secrets reset --dir voice-screen-share/jitsi-server --name user_alice -y
ciu up --dir voice-screen-share/jitsi-server --yes
```

ciu generates a new password and the hook applies it. Existing sessions of that user stay valid until they end.

## Rotate the service secrets

Delete `jicofo_auth_password`, `jvb_auth_password` and `jicofo_component_secret` together, remove
`vol-prosody-data/data/auth%2emeet%2ejitsi/accounts/{focus,jvb}.dat`, then `ciu up` (the
containers are recreated and re-register).

## Allowing guests

`jitsi.auth.guests = true` lets anyone with a room link join once a logged-in user has
started the room. Do not enable it on a public host unless that is intended; remote control
is only offered between participants of the same room anyway.
