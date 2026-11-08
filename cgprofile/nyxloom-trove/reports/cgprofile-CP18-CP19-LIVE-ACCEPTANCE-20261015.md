# CGProfile CP-18 / CP-19 live acceptance — 2026-10-15

Candidate source revision: `9943239c29f460fd7a59eb85a93a05fc1bea44cc`.
The daemon was deployed through CIU from the local candidate image
`sha256:1e91fd8af71e01f54ff1c7c4b48e97e1abe2732a7d586abbbfc4a1f359c9b044`,
whose `org.opencontainers.image.revision` label matches that source revision.

## CP-18: DAMON session startup

The daemon accepted a real 14-second session and collected 14 ordinary
resource samples. DAMON reported
`unavailable:OSError: [Errno 16] Device or resource busy`; no `damon.jsonl`
series was produced. The session stopped normally and the daemon remained
healthy.

Read-only host checks found `nr_kdamonds=0` and
`/sys/module/damon_stat/parameters/enabled=Y`. The consumer guide documents
that Linux's special-purpose DAMON_STAT module can be active while the
sysfs kdamond registry is empty and that its global DAMON use can make a
session return `EBUSY`. The module was left enabled. This probe therefore
does not satisfy CP-18's successful-DAMON-sample oracle; CP-18 remains open
until that oracle can be exercised on a compatible host.

Session: `s-20261015T193156Z-8c06`, started `2026-10-15T19:31:56Z`, ended
`2026-10-15T19:32:10Z`, duration 14 seconds, 14 samples. The target was the
existing dstdns test-runner container; it was observed only and not changed.

## CP-19: placement refusal and restoration

Both probes used disposable Debian containers in the declared
`dev-gates.slice`, capped at 0.25 CPU, 128 MiB memory, 256 MiB memory-plus-swap,
and 8 PIDs. DAMON was disabled for these probes.

### One-process source refusal

Container `66d47e2b4363c6ef2ce41cadf76eb05ff67f536b199f179929b7f2849a056e48`
had exactly one token-matched PID in its Docker scope. Start returned
`place-refused:origin-scope-would-empty`, `pids_moved=0`, and `leaf=null`.
The ordinary observation session completed (2 seconds, one sample), the
container remained running, and the probe container was then removed.

Session: `s-20261015T193456Z-8d71`.

### Shared-scope move and exact restoration

Container `31299f1c7300d8534c7a5423d4acaf110226fbdd1791d860830ee054094bc67f`
had a `sleep infinity` process in its source scope and one token-matched
`sleep 120` worker. The worker's host `/proc/<pid>/cgroup` was read before,
during, and after placement:

| Point | Cgroup |
|---|---|
| Before | `/dev.slice/dev-gates.slice/docker-31299f1c7300d8534c7a5423d4acaf110226fbdd1791d860830ee054094bc67f.scope` |
| During | `/dev.slice/dev-gates.slice/rg-profile-cp19-restore-verify-20261015.scope/rg-cp19-restore-verify-20261015` |
| After stop | `/dev.slice/dev-gates.slice/docker-31299f1c7300d8534c7a5423d4acaf110226fbdd1791d860830ee054094bc67f.scope` |

Start moved exactly one PID and returned no placement error. Stop completed
normally (4 seconds, 3 samples); the worker's cgroup exactly matched its
pre-move cgroup. The sibling `sleep infinity` remained in the source scope.
The probe container was removed after verification.

Session: `s-20261015T193600Z-d2da`.

The first successful run was also recorded as session
`s-20261015T193501Z-f692`; it moved one PID, stopped without a placement
error, and left both container processes alive. The follow-up above adds the
before/during/after cgroup identity check.

CP-19's live acceptance passes. Keep its backlog entry open until final exact-tip
registered gates and independent review are complete.
