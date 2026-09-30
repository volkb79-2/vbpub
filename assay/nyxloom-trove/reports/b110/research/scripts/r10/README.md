# R10 probe outputs

The files in `strace-outputs/` are outputs of `strace --seccomp-bpf -w -f -c -e trace=fsync,fdatasync,sync_file_range,syncfs,sync,msync` runs, as described in `../../R10-snapshot-structural.md` §3.2. `probe.py` itself never invokes strace. The outputs come from three sources:
- `strace-cand-{full,nofsync,refresh}.txt`: strace-wrapped `probe.py <seed> <commit> <scratch> <variant> <reps>` runs, one per variant;
- `strace-clone-full.txt`: a direct `git clone --no-local --bare` trace;
- `sanity*.txt`: traces of a Python sanity script.

`strace -c` writes an empty file when no traced syscall occurs. The zero-byte `strace-cand-*.txt` files are therefore consistent with "0 fsync calls". The per-run exit statuses were not retained, so an early-dying traced command cannot be excluded from the files alone.

| File | Content |
|---|---|
| `strace-cand-full.txt`, `strace-cand-nofsync.txt`, `strace-cand-refresh.txt` | Per-candidate snapshot path traces. Zero bytes: no traced syscall (see above). |
| `sanity.txt` | Python sanity trace. It shows the method captures `fsync` and `sync`. |
| `sanity2.txt` | Empty. Its run was not documented (unexplained). |
| `strace-clone-full.txt` | Trace of `git clone --no-local --bare`: 3 fsyncs. |
