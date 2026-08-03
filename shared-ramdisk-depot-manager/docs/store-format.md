# The release store — on-disk format

Everything here is versioned from day one, so a v1 `host-bind` deployment's
store stays readable by the binary that will later flip it to `provider`
mode.

## Layout

```
/var/lib/srdm/
├── store/
│   ├── releases/<release-id>/
│   │   ├── root/            the content
│   │   ├── manifest.json    per-file SHA-256, versioned
│   │   └── COMPLETE         written last, fsync'd
│   ├── channels/<profile>/<channel> -> ../../releases/<release-id>
│   ├── tx/<operation-id>/   transactions in flight
│   └── quarantine/          what recovery could not adopt
└── journal/
    ├── events.jsonl         append-only, one record per phase boundary
    └── operations/<id>.json durable per-operation record
```

`manifest.json` and `COMPLETE` sit **beside** `root/`, never inside it, so
they never appear in the tree they describe.

The channel symlink is **relative**, so the whole store can be moved or
mounted at a different path without rewriting anything.

## Promotion, in order

```
transaction → classification → per-file SHA-256 manifest → probes →
ownership normalization → manifest persisted → fsync'd COMPLETE →
rename into releases/ → atomic channel flip
```

Each step is journaled before it runs and after it settles. Two orderings
are load-bearing:

**Classification comes first, alone.** It is the cheapest phase and the one
most likely to refuse, so an unclassified path costs an operator a directory
walk rather than a full hash of a multi-gigabyte tree.

**`COMPLETE` is last.** Everything before it can be interrupted and leaves no
trace on any channel; everything after it is a rename. That is what lets
recovery use the file's presence as the *only* signal, and why the content is
fsync'd before it is written — otherwise `COMPLETE` could survive a power cut
that the bytes it vouches for did not.

One consequence worth knowing: ownership normalization runs *after* the
manifest is built, and chowning a non-directory strips setuid and setgid. So
the recorded modes are refreshed (re-stat, no re-hash — chown and chmod do
not touch bytes) before the manifest is persisted. Without that, every later
verification would fail on a mode the store itself changed.

## `manifest.json`

```json
{
  "schema_version": 1,
  "hash": "sha256",
  "profile": "soulmask",
  "entries": [
    {"path": "Engine", "type": "dir", "mode": "00755", "class": "code"},
    {"path": "Engine/lib.so", "type": "file", "mode": "00644",
     "size": 7, "sha256": "…", "class": "code"},
    {"path": "Engine/lib.so.1", "type": "symlink", "mode": "00777",
     "target": "lib.so", "class": "code"}
  ],
  "content_digest": "sha256:…"
}
```

**There is no mtime, and nothing is keyed on size.** A manifest keyed on
either cannot tell a rewritten file from an untouched one — which is the
2026-07-21 incident's failure mode, where a validate pass reported "Success!"
over content that had never been replaced.

`mode` is the traditional four-digit Unix word, so setuid, setgid and sticky
are recorded. Go keeps those outside `FileMode.Perm()`, and a manifest built
from `Perm()` alone would be blind to exactly the bits that matter most.

`content_digest` covers every entry's type, path, mode, class and
hash-or-link-target. Two byte-identical transactions produce the same value;
any single changed byte changes it.

A release may contain only regular files, directories and symlinks. Devices,
sockets and fifos are refused: they have no place in shared immutable
content, and their behaviour under a bind mount into a container is not
something to discover in production.

## `COMPLETE`

```json
{
  "schema_version": 1,
  "release_id": "rel-2026w31",
  "profile": "soulmask",
  "operation_id": "rel-2026w31",
  "content_digest": "sha256:…",
  "manifest_sha256": "…",
  "provenance": {"kind": "staged", "source": "manual-2026-08-03"}
}
```

`manifest_sha256` pins the exact manifest bytes, so swapping in a different
valid manifest is caught rather than accepted. `provenance.kind` is `staged`
or, once `harvest` lands, `harvested` — a release adopted from an in-place
update is a first-class release, but it says where it came from.

## Recovery

On restart, `srdm store recover`:

- **adopts** a transaction that reached `COMPLETE` and still verifies — it is
  by definition whole and durable, so discarding it would throw away finished
  work for no safety gain;
- **quarantines** anything else, rather than deleting it: a store that
  silently erases a half-written transaction erases the evidence of why it
  was half-written;
- **never repoints a channel.** Recovery decides what exists; an operator
  decides what is active.

It then journals, per channel, which release it resolves to — durably, where
someone debugging at 03:00 will look.
