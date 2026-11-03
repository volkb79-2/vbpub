# Jitsi users and credentials

Authentication is Jitsi secure-domain with prosody **internal** accounts. Nobody joins without
logging in (`jitsi.auth.guests = false`). Accounts are declared in `ciu.defaults.toml.j2`
(`[[jitsi.users]]`): `admin`, `dummy1`, `dummy2`, `dummy3`. Jitsi internal auth has no separate
admin role: `admin` is simply a normal account you keep for yourself.

## Where the credentials live

- Source of truth: one file per user, `voice-screen-share/jitsi-server/.ciu/secrets/jitsi/user_<name>`
  (mode 0600, directory 0700, generated randomly by ciu `GEN_LOCAL` on first `ciu up`).
- Convenience copy: `voice-screen-share/jitsi-server/.ciu/credentials.txt` (0600, rebuilt on every
  `ciu up`; tab-separated `user<TAB>password`).
- `.ciu/` is gitignored; nothing in a tracked file or in a log contains a password. `ciu up`
  prints only the path of the credentials file. Read it yourself: `cat <path>`.
- The three service secrets (`jicofo_auth_password`, `jvb_auth_password`, `jicofo_component_secret`)
  live next to them. The Jitsi images take them only from the container environment, so they
  are readable by anyone with docker access on the host.

The login name in the Jitsi UI is the bare username (`admin`), not `admin@meet.jitsi`.

## How accounts are created (idempotent)

After the stack is healthy, the `post_compose` hook runs `prosodyctl register <user> meet.jitsi`
for every declared user inside the prosody container, password on stdin. Registering an
existing user sets its password to the stored value, so every `ciu up` converges to the same
state. (Within the container, for the instant that process runs, the password is an argument
of `prosodyctl`; it is never on the host command line.)

## Rotate one user

```bash
rm voice-screen-share/jitsi-server/.ciu/secrets/jitsi/user_dummy1   # exact path, one file
ciu up --dir voice-screen-share/jitsi-server -y
```

ciu generates a new password, the hook applies it, `credentials.txt` is rebuilt. Existing
sessions of that user stay valid until they end.

## Rotate the service secrets

Delete `jicofo_auth_password`, `jvb_auth_password` and `jicofo_component_secret` together, remove
`vol-prosody-config/data/auth%2emeet%2ejitsi/accounts/{focus,jvb}.dat`, then `ciu up` (the
containers are recreated and re-register).

## Add or remove a user

Add a `[[jitsi.users]]` entry and a matching `user_<name>` secret (same shape as the others)
in a sparse override, `ciu up`. Removing the entry does not delete the account; delete it with
`docker exec <prefix>-prosody prosodyctl --config /config/prosody.cfg.lua unregister <user> meet.jitsi`
(and the secret file).

## Allowing guests

`jitsi.auth.guests = true` lets anyone with a room link join once a logged-in user has
started the room. Do not enable it on a public host unless that is intended; remote control
is only offered between participants of the same room anyway.
