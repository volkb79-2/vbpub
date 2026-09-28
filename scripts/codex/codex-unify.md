# Dual-profile Codex shared-state design

This document explains the intended storage and runtime design for running two Codex profiles concurrently while allowing sessions to move freely between them.

The companion migration script is:

- `codex-unify.sh`

The target use case is:

- two separate `CODEX_HOME` values;
- separate authentication and profile configuration;
- one shared rollout/session corpus;
- one shared SQLite runtime/state store;
- one shared thread-writer lock namespace;
- separate app-server daemons for the two profiles;
- concurrent Codex use from both profiles.

---

## 1. Goals

The design should provide all of the following:

1. Sessions created under either profile are immediately visible to the other profile.
2. An old session can be resumed from either profile without manually moving its rollout file.
3. Codex's SQLite thread catalog should not diverge between profiles.
4. Concurrent Codex processes should coordinate thread writes through one lock namespace.
5. Authentication, provider configuration, feature flags, MCP configuration, and other profile-specific settings remain independent.
6. Each profile keeps its own app-server daemon and control socket.
7. Existing rollout files must never be overwritten during migration.
8. Existing SQLite databases are retained as backups rather than destructively merged.
9. The final layout should be easy to inspect and recover.

---

## 2. Target architecture

The intended filesystem layout is approximately:

```text
~/.codex/
├── auth.json                         # profile 1
├── config.toml                       # profile 1
├── sessions/                         # canonical/shared rollout tree
├── archived_sessions/                # canonical/shared archived rollouts
├── thread-writer-locks/              # canonical/shared writer locks
├── session_index.jsonl               # profile-local physical file
├── sqlite-shared/                    # canonical shared SQLite home
│   ├── state_5.sqlite
│   └── other Codex SQLite DBs
├── app-server-control/               # profile 1 only
├── app-server-daemon/                # profile 1 only
└── ...

~/.codex2/
├── auth.json                         # profile 2
├── config.toml                       # profile 2
├── sessions -> ../.codex/sessions
├── archived_sessions -> ../.codex/archived_sessions
├── thread-writer-locks -> ../.codex/thread-writer-locks
├── session_index.jsonl               # profile-local physical file
├── app-server-control/               # profile 2 only
├── app-server-daemon/                # profile 2 only
└── ...
```

Both profiles use:

```text
sqlite_home = "/home/vscode/.codex/sqlite-shared"
```

or equivalently at process launch:

```bash
export CODEX_SQLITE_HOME="$HOME/.codex/sqlite-shared"
```

The important conceptual split is:

```text
                         shared
                 ┌──────────────────┐
                 │ sessions         │
                 │ archived_sessions│
                 │ writer locks     │
                 │ SQLite state     │
                 └────────┬─────────┘
                          │
              ┌───────────┴───────────┐
              │                       │
          profile 1               profile 2
          ~/.codex                ~/.codex2
              │                       │
         config/auth A            config/auth B
              │                       │
        app-server A             app-server B
```

---

## 3. Why `sessions/` is shared

Codex rollout JSONL files contain the persisted conversation/event history.

They are the durable per-thread transcript/event stream and are what makes an old session resumable.

Historically, manually moving a rollout JSONL from one `CODEX_HOME` to another allowed the second profile to resume it. That demonstrates the basic portability of the rollout files, but doing this manually causes the surrounding indexes and state databases to drift.

Sharing the directory eliminates that drift at the file level:

```text
~/.codex2/sessions -> ~/.codex/sessions
```

Both profiles now see the same rollout corpus immediately.

The migration script first copies and verifies files, and only then replaces the second profile's session directory with a symlink.

It deliberately refuses to overwrite an existing rollout.

---

## 4. Why `archived_sessions/` is also shared

An archived session is still part of the logical common session corpus.

If active sessions are common but archived sessions remain profile-specific, moving a thread between active and archived state can make it disappear from the other profile.

For that reason the same design is applied to:

```text
~/.codex2/archived_sessions -> ~/.codex/archived_sessions
```

Even if no archived sessions currently exist, setting up the shared path now avoids future asymmetry.

---

## 5. Why the SQLite state is shared

Codex uses SQLite for much more than raw conversation text.

The state database contains the operational catalog around rollout files, including thread metadata such as:

- thread ID;
- rollout path;
- creation/update/recency timestamps;
- provider/model information;
- cwd;
- source/origin;
- title/name/preview;
- archive state;
- git metadata;
- project/section metadata;
- other runtime/UI metadata.

A setup with shared rollout files but two independent state databases would look like:

```text
shared JSONL corpus
      │
      ├── ~/.codex/state_5.sqlite
      └── ~/.codex2/state_5.sqlite
```

The databases can then disagree about the same underlying rollout corpus.

That is exactly the kind of drift this design is intended to eliminate.

Instead, both profiles use one SQLite home:

```bash
CODEX_SQLITE_HOME="$HOME/.codex/sqlite-shared"
```

or the persistent equivalent in both `config.toml` files:

```toml
sqlite_home = "/home/vscode/.codex/sqlite-shared"
```

This makes the SQLite state transactional and common to all Codex processes from either profile.

---

## 6. Why the old SQLite databases are not merged

The migration does **not** perform an SQL-level merge of the two historical `state_5.sqlite` databases.

That is intentional.

Reasons include:

- Codex schema evolves across releases.
- Multiple supporting tables exist beyond `threads`.
- Foreign relationships and migration bookkeeping can change.
- Some tables contain runtime rather than purely derived data.
- A naïve `INSERT ... SELECT` can produce a superficially valid but internally inconsistent DB.

Instead, the procedure:

1. backs up all old SQLite files;
2. unifies the rollout corpus;
3. creates a new empty shared SQLite home;
4. starts Codex once against the unified corpus;
5. lets Codex backfill/reconstruct its state;
6. validates the result with `codex doctor`.

The old databases remain available for forensic recovery of metadata that is not reconstructible from rollout JSONL.

---

## 7. DB-only metadata caveat

Some presentation/runtime metadata may exist only in SQLite.

Examples can include:

- custom thread names;
- pins;
- section/project organization;
- some UI state.

Therefore the migration script preserves:

- the original SQLite files;
- exported `threads` table CSVs;
- the original `session_index.jsonl` files;
- the original second-profile rollout directories under timestamped backup names.

Do not delete those backups immediately after migration.

Keep them until the new setup has been exercised and any desired DB-only metadata has been recovered.

---

## 8. Why `thread-writer-locks/` must be shared

This is an important concurrency detail.

Codex maintains per-thread writer locks under `CODEX_HOME`.

If the two profiles shared the same rollout files but used different lock directories, this could happen:

```text
profile 1:
  ~/.codex/thread-writer-locks/thread-X.lock

profile 2:
  ~/.codex2/thread-writer-locks/thread-X.lock
```

Both processes could then independently believe they have the writer lock for the same logical thread.

That defeats the purpose of the lock.

Therefore the second profile points at the canonical lock directory:

```text
~/.codex2/thread-writer-locks -> ../.codex/thread-writer-locks
```

This gives both profiles one lock namespace.

The intended result is:

```text
profile 1 process ─┐
                   ├── shared thread-X writer lock
profile 2 process ─┘
```

This is a stronger concurrency model than sharing `sessions/` alone.

---

## 9. Why `session_index.jsonl` is *not* symlinked

`session_index.jsonl` is an auxiliary append/rewrite index for thread names/discovery.

It is not the authoritative conversation store.

Its update synchronization is not designed as a shared transactional cross-process database in the same way SQLite is.

The migration therefore:

1. merges both historical `session_index.jsonl` files once;
2. installs the same merged starting content into both profiles;
3. keeps them as two separate physical files.

That avoids making two independently running Codex daemons concurrently rewrite the same non-transactional JSONL index.

The shared SQLite state is the important common catalog going forward.

---

## 10. Why the app-server daemons stay separate

Each profile keeps a separate app-server daemon.

This is deliberate.

An app-server daemon inherits the profile/environment under which it starts, including the corresponding `CODEX_HOME` and associated configuration.

A single shared daemon would blur the distinction between:

```text
profile 1:
  ~/.codex/config.toml
  ~/.codex/auth...

profile 2:
  ~/.codex2/config.toml
  ~/.codex2/auth...
```

The final design is therefore:

```text
profile 1 app-server
    CODEX_HOME=~/.codex

profile 2 app-server
    CODEX_HOME=~/.codex2
```

Both daemons see:

- the same rollouts;
- the same archived rollouts;
- the same writer locks;
- the same SQLite state.

But each daemon retains its own:

- auth context;
- config;
- control socket;
- updater/runtime files;
- managed daemon lifecycle.

This preserves the reason for having two profiles in the first place.

---

## 11. App-server advantages

The app-server provides a long-lived local Codex backend rather than making each client entirely self-contained.

Useful properties include:

- persistent live thread ownership;
- reconnect/rejoin behavior;
- event streaming;
- shared backend for clients using the same profile;
- centralized tool/runtime handling for that profile;
- daemon lifecycle independent of one terminal.

With two profiles, there are two such runtimes:

```text
terminal / client A
        │
        ▼
profile 1 app-server
        │
        ├── shared rollout corpus
        └── shared SQLite state


terminal / client B
        │
        ▼
profile 2 app-server
        │
        ├── same rollout corpus
        └── same SQLite state
```

They should have different control socket paths.

---

## 12. Concurrency model

The expected normal state is:

- many Codex processes may run concurrently;
- processes may come from either profile;
- both profiles use the same SQLite runtime;
- both profiles use the same rollout corpus;
- both profiles use the same writer lock namespace;
- each profile uses its own app-server daemon.

Even with shared writer locks, operationally it remains sensible not to intentionally drive the exact same thread from two independent app-server daemons at the same moment unless that behavior has specifically been tested for the installed Codex release.

The safe mental model is:

> Threads are portable between profiles, but a live thread should normally have one active owner at a time.

---

## 13. Terminal usage

### Profile 1

A shell that should operate as profile 1 needs:

```bash
export CODEX_HOME="$HOME/.codex"
```

If `sqlite_home` has been persisted in `~/.codex/config.toml`, no additional SQLite environment variable is required.

You can then run:

```bash
codex
```

or:

```bash
codex resume <THREAD-ID>
```

### Profile 2

A shell that should operate as profile 2 needs:

```bash
export CODEX_HOME="$HOME/.codex2"
```

Again, if `sqlite_home` is persisted in `~/.codex2/config.toml`, this is enough:

```bash
codex
```

### Explicit form

If you want to make the shared SQLite location explicit regardless of config:

```bash
export CODEX_HOME="$HOME/.codex"
export CODEX_SQLITE_HOME="$HOME/.codex/sqlite-shared"
codex
```

and:

```bash
export CODEX_HOME="$HOME/.codex2"
export CODEX_SQLITE_HOME="$HOME/.codex/sqlite-shared"
codex
```

`CODEX_SQLITE_HOME` should be identical for both profiles.

---

## 14. Which environment variables are actually required?

After a successful migration, assuming `sqlite_home` was written into both config files:

### Required per shell

Only:

```bash
CODEX_HOME="$HOME/.codex"
```

or:

```bash
CODEX_HOME="$HOME/.codex2"
```

### Optional but explicit

You may additionally set:

```bash
CODEX_SQLITE_HOME="$HOME/.codex/sqlite-shared"
```

This is useful in:

- wrappers;
- systemd/service definitions;
- debugging;
- scripts where you want the intended state store to be obvious.

### Must be consistent

Never point the two profiles at different SQLite homes after migration unless you intentionally want them to diverge again.

---

## 15. Recommended shell wrappers

It is convenient to avoid repeatedly typing environment variables.

For example:

```bash
mkdir -p "$HOME/bin"
```

Profile 1:

```bash
cat > "$HOME/bin/codex1" <<'EOF'
#!/bin/sh
export CODEX_HOME="$HOME/.codex"
export CODEX_SQLITE_HOME="$HOME/.codex/sqlite-shared"
exec codex "$@"
EOF

chmod +x "$HOME/bin/codex1"
```

Profile 2:

```bash
cat > "$HOME/bin/codex2" <<'EOF'
#!/bin/sh
export CODEX_HOME="$HOME/.codex2"
export CODEX_SQLITE_HOME="$HOME/.codex/sqlite-shared"
exec codex "$@"
EOF

chmod +x "$HOME/bin/codex2"
```

Usage:

```bash
codex1
codex2
```

or:

```bash
codex1 resume <THREAD-ID>
codex2 resume <THREAD-ID>
```

If `$HOME/bin` is not already on `PATH`:

```bash
export PATH="$HOME/bin:$PATH"
```

---

## 16. App-server daemon usage

Each profile gets its own daemon.

### Profile 1 daemon

```bash
CODEX_HOME="$HOME/.codex" \
CODEX_SQLITE_HOME="$HOME/.codex/sqlite-shared" \
codex app-server daemon start
```

### Profile 2 daemon

```bash
CODEX_HOME="$HOME/.codex2" \
CODEX_SQLITE_HOME="$HOME/.codex/sqlite-shared" \
codex app-server daemon start
```

Check them independently:

```bash
CODEX_HOME="$HOME/.codex" \
CODEX_SQLITE_HOME="$HOME/.codex/sqlite-shared" \
codex app-server daemon version | jq
```

```bash
CODEX_HOME="$HOME/.codex2" \
CODEX_SQLITE_HOME="$HOME/.codex/sqlite-shared" \
codex app-server daemon version | jq
```

Expected:

- both report `status: running`;
- both use the same Codex version;
- their `socketPath` values differ.

---

## 17. Normal verification commands

Check symlink targets:

```bash
readlink -f "$HOME/.codex/sessions"
readlink -f "$HOME/.codex2/sessions"

readlink -f "$HOME/.codex/thread-writer-locks"
readlink -f "$HOME/.codex2/thread-writer-locks"
```

Each corresponding pair should resolve to the same path.

Check profile 1:

```bash
CODEX_HOME="$HOME/.codex" codex doctor
```

Check profile 2:

```bash
CODEX_HOME="$HOME/.codex2" codex doctor
```

Because `sqlite_home` is persisted in config, both should inspect the same state DB.

Useful thread-state invariants are:

```text
missing active rows       0
missing archived rows     0
stale rows                0
archive mismatches        0
duplicate thread IDs      0
duplicate DB paths        0
scan errors               0
malformed file names      0
```

The exact number of rollout files and DB rows will naturally grow over time.

---

## 18. Verify SQLite directly

Find the shared state DB:

```bash
find "$HOME/.codex/sqlite-shared" \
    -maxdepth 1 \
    -name 'state_*.sqlite' \
    -print
```

Run SQLite integrity checking:

```bash
sqlite3 "$HOME/.codex/sqlite-shared/state_5.sqlite" \
    'PRAGMA quick_check;'
```

Expected:

```text
ok
```

If the state DB filename changes in a future Codex release, use the actual `state_*.sqlite` file present in the shared SQLite home.

---

## 19. Upgrade procedure

Codex upgrades deserve a little care because multiple processes may attempt schema migrations at startup.

Recommended sequence after upgrading the Codex binary:

1. Stop both app-server daemons.
2. Ensure no Codex processes remain.
3. Start **one** profile/app-server first.
4. Let it complete SQLite migration/backfill.
5. Run `codex doctor`.
6. Start the second profile daemon.
7. Verify both daemon versions match the CLI version.

Example:

```bash
CODEX_HOME="$HOME/.codex" \
codex app-server daemon stop

CODEX_HOME="$HOME/.codex2" \
codex app-server daemon stop
```

Check:

```bash
pgrep -af codex
```

Then initialize profile 1 first:

```bash
CODEX_HOME="$HOME/.codex" \
CODEX_SQLITE_HOME="$HOME/.codex/sqlite-shared" \
codex app-server daemon start
```

Verify:

```bash
CODEX_HOME="$HOME/.codex" codex doctor
```

Then start profile 2:

```bash
CODEX_HOME="$HOME/.codex2" \
CODEX_SQLITE_HOME="$HOME/.codex/sqlite-shared" \
codex app-server daemon start
```

Finally:

```bash
CODEX_HOME="$HOME/.codex" \
codex app-server daemon version | jq

CODEX_HOME="$HOME/.codex2" \
codex app-server daemon version | jq
```

---

## 20. If one daemon becomes stale

A stale daemon control socket can look like:

```text
failed to connect to .../app-server-control.sock
Connection refused
```

First inspect:

```bash
CODEX_HOME="$HOME/.codex" \
codex app-server daemon version
```

or:

```bash
CODEX_HOME="$HOME/.codex2" \
codex app-server daemon version
```

Also inspect listeners:

```bash
ss -xlpn | grep -E 'codex|app-server'
```

And running processes:

```bash
pgrep -af codex
```

Do not assume one profile's daemon is blocking the other. Each profile normally has its own control socket.

If a daemon is stale, prefer the managed daemon lifecycle commands before manually deleting runtime state.

---

## 21. Rollback strategy

The migration script is intentionally conservative.

It retains:

- timestamped backup copies of old SQLite files;
- exported thread-table CSVs;
- merged-index audit files;
- the old `.codex2/sessions` tree under a timestamped name;
- the old `.codex2/archived_sessions` tree when present;
- logs from bootstrap, doctor, rsync, and daemon startup.

Do not remove those immediately.

A rollback generally means:

1. stop all Codex processes;
2. remove the new `.codex2` symlinks;
3. rename the retained pre-unification directories back;
4. restore the relevant old config/SQLite files from the backup directory.

Because the script copies before replacing and does not destructively SQL-merge the old DBs, rollback remains practical.

---

## 22. What should remain profile-specific

Do not casually share the entire `CODEX_HOME`.

The following should generally remain separate:

```text
config.toml
auth/profile state
app-server-control/
app-server-daemon/
managed daemon package/runtime state
profile-specific MCP/provider configuration
```

The purpose of the two-home design is precisely to keep those contexts distinct.

---

## 23. What is intentionally shared

The final shared surfaces are:

```text
sessions/
archived_sessions/
thread-writer-locks/
SQLite home
```

These represent:

```text
conversation corpus
archive corpus
thread write coordination
transactional runtime/catalog state
```

This is the minimum coherent shared layer for two profiles that should behave as though they operate on one common set of Codex threads.

---

## 24. `session_index.jsonl` role

`session_index.jsonl` should be considered an auxiliary discovery/name log, not the canonical thread body.

The actual conversation lives in rollout JSONL.

The shared SQLite DB is the stronger common thread catalog.

Therefore:

```text
sessions                  shared
state SQLite              shared
session_index.jsonl       copied/merged once, then profile-local
```

This avoids introducing a new non-transactional shared-write hotspot.

---

## 25. Expected behavior after migration

A session created with:

```bash
CODEX_HOME="$HOME/.codex" codex
```

should produce a rollout under the common:

```text
~/.codex/sessions/
```

The second profile sees the same physical rollout because:

```text
~/.codex2/sessions -> ~/.codex/sessions
```

and it sees the same catalog entry because both profiles use:

```text
~/.codex/sqlite-shared/
```

Therefore a later command such as:

```bash
CODEX_HOME="$HOME/.codex2" \
codex resume <THREAD-ID>
```

can resume the thread without moving the rollout file.

The reverse direction works the same way.

---

## 26. Suggested post-migration smoke tests

Create or identify one old thread from profile 1 and one from profile 2.

Test:

```bash
CODEX_HOME="$HOME/.codex" codex resume <PROFILE-1-THREAD>
CODEX_HOME="$HOME/.codex2" codex resume <PROFILE-1-THREAD>
```

Then:

```bash
CODEX_HOME="$HOME/.codex2" codex resume <PROFILE-2-THREAD>
CODEX_HOME="$HOME/.codex" codex resume <PROFILE-2-THREAD>
```

Do not run both resumptions of the same thread simultaneously during this test.

Also verify:

```bash
CODEX_HOME="$HOME/.codex" codex doctor
CODEX_HOME="$HOME/.codex2" codex doctor
```

The rollout/state inventories should agree.

---

## 27. Backup retention

Keep the migration backup until at least all of the following are true:

- both profiles have been used successfully;
- several historical sessions from each original home have been resumed;
- new sessions created by each profile appear in the other;
- both app-server daemons survive a restart;
- at least one Codex upgrade has been completed cleanly, if long-term confidence is desired;
- no missing custom names/pins or other DB-only metadata has been noticed.

After that, the large pre-unification rollout copy can be removed separately from the smaller SQLite/config forensic backup if disk space is important.

---

## 28. Operational summary

For normal terminal usage:

```bash
# profile 1
export CODEX_HOME="$HOME/.codex"
codex
```

```bash
# profile 2
export CODEX_HOME="$HOME/.codex2"
codex
```

If you want the shared DB path explicit:

```bash
export CODEX_SQLITE_HOME="$HOME/.codex/sqlite-shared"
```

For daemons, run one per profile.

Do not merge the app-server daemon layer.

Do not split the SQLite state again.

Do not split `thread-writer-locks`.

Treat the canonical `.codex/sessions` tree as the one physical session corpus.

---

## 29. Design invariant checklist

The setup is in the intended state when all of these are true:

```text
[ ] ~/.codex/sessions is a physical directory
[ ] ~/.codex2/sessions resolves to ~/.codex/sessions

[ ] ~/.codex/archived_sessions is a physical directory
[ ] ~/.codex2/archived_sessions resolves to it

[ ] ~/.codex/thread-writer-locks is a physical directory
[ ] ~/.codex2/thread-writer-locks resolves to it

[ ] both config.toml files specify the same sqlite_home
[ ] both profiles pass codex doctor with no rollout/DB divergence

[ ] profile 1 app-server is running under CODEX_HOME=~/.codex
[ ] profile 2 app-server is running under CODEX_HOME=~/.codex2
[ ] the two app-server socket paths differ
[ ] both daemon versions match the CLI version

[ ] session_index.jsonl is not symlinked
[ ] auth/config remain separate
[ ] pre-unification backups still exist until validation is complete
```


