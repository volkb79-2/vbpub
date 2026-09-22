---
kind: backlog-entry
schema_version: 1
id: CP-4
title: "cmd_targets passes HERE as both repo_dir and out_dir to build_helper_spec, causing Docker to refuse a duplicate mount point"
status: open
type: "bugfix"
severity: "medium"
provenance: "dstdns controller session, 2026-09-22"
filed_date: "2026-09-22"
---

## Observed mechanism

`./cgprofile targets --target slice:dev-background.slice ...` (helper mode, the default
when run from inside a devcontainer with no host cgroup view) fails every invocation with:

```
docker: Error response from daemon: Duplicate mount point: /workspaces/vbpub/scripts/cgroup-profiler
```

Reproduced live, 2026-09-22, from `dstdns-devcontainer-vb`, both with and without an
explicit `--out-dir` override (the override has no effect on this error, which was the
tell that pointed away from the CLI's `--out-dir` argument).

## Root cause, confirmed in source

`cmd_targets` (`cgprofile.py:711`) calls:

```python
spec = access.build_helper_spec(HERE, HERE, args.helper_image,
                               getattr(args, "helper_cgroup_parent", None))
```

passing `HERE` (the profiler's own directory, `os.path.dirname(__file__)`) as **both**
`repo_dir` and `out_dir`. `build_helper_spec()` (`lib/access.py`) sets
`repo_mount_path=repo_dir` and `out_mount_path=out_dir` unconditionally, and
`HelperSpec.docker_args()` unconditionally emits two `-v` flags:

```python
"-v", f"{self.repo_host_path}:{self.repo_mount_path}:ro",
"-v", f"{self.out_host_path}:{self.out_mount_path}:rw",
```

With `repo_dir == out_dir == HERE`, both flags target the identical container-side
destination (`HERE`), which Docker refuses as a duplicate mount point. Every other call
site (`cgprofile.py:392`, using `os.path.dirname(run_path)`; `cgprofile.py:769`, using
`DEFAULT_OUT`) passes a genuinely distinct `out_dir`, so only `cmd_targets`'s helper-mode
path is affected. `--out-dir` has no effect because `cmd_targets` never reads
`args.out_dir` at all -- it hardcodes `HERE` for both parameters.

`cmd_targets` never writes anything (its own docstring: "print what would be sampled,
without sampling"), so the writable out-dir mount it's requesting doesn't serve any
purpose here in the first place -- the bug is a copy-paste-shaped placeholder, not a
deliberate design choice that just happens to collide.

## Why this is `cgprofile`'s bug, not a caller error

No caller-supplied argument can work around it (confirmed: `--out-dir` is ignored by this
code path entirely). The tool is unusable in helper mode for the `targets` subcommand from
any devcontainer that lacks host cgroup access -- exactly the situation `cgprofile`'s own
"Gotchas" section says it handles transparently.

## Proposed fix

Either:
(a) give `cmd_targets` a genuinely distinct throwaway `out_dir` (e.g. `tempfile.mkdtemp()`,
    or reuse `DEFAULT_OUT` like the other two call sites), or
(b) since `targets` never writes, add a read-only/no-output-mount mode to `HelperSpec`/
    `build_helper_spec()` so a caller that has nothing to write doesn't need to fabricate
    a second mount at all -- the more correct fix, since it removes an unnecessary rw bind
    mount from a command that only reads and prints.

## Oracle before implementation

`cgprofile targets --mode helper --target slice:<any>` (or whatever forces helper mode)
must succeed and print resolved targets when run from a container with no host cgroup
view, without requiring the caller to pass any workaround flag. A regression test should
assert `cmd_targets`'s constructed `HelperSpec` never has `repo_mount_path == out_mount_path`.
