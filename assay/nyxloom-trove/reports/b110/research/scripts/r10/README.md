# R10 probe outputs

The files in `strace-outputs/` are outputs of `strace --seccomp-bpf -w -f -c -e trace=fsync,fdatasync,sync_file_range,syncfs,sync,msync` runs made by the R10 probe (`probe.py`), as described in `../../R10-snapshot-structural.md` §3.2.

`strace -c` writes an empty file when no traced syscall occurs. The zero-byte `strace-cand-*.txt` files are therefore consistent with "0 fsync calls". The per-run exit statuses were not retained, so an early-dying traced command cannot be excluded from the files alone.

| File | Content |
|---|---|
| `strace-cand-full.txt`, `strace-cand-nofsync.txt`, `strace-cand-refresh.txt` | Per-candidate snapshot path traces. Zero bytes: no traced syscall (see above). |
| `sanity.txt` | Python sanity trace. It shows the method captures `fsync` and `sync`. |
| `sanity2.txt` | Empty. Its run was not documented (unexplained). |
| `strace-clone-full.txt` | Trace of `git clone --no-local --bare`: 3 fsyncs. |
