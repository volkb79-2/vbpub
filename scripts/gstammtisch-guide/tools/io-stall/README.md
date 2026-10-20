# tools/io-stall — host IO stall instruments

TODO (controller, SMT-02): the generic instrument scripts belong here. They currently live only on
the gstammtisch host in `/var/log/mdt-stall/`:

- `stall_recorder.py` (1 s recorder)
- `dstall.bt` (bpftrace D-state probe)
- `stall_summary.py`
- `ab_matrix.sh`, `ab_matrix_analyze.py`

The controller has not supplied the files yet. What each one answers, and how to read their output,
is in [../../HOST-IO-INTERFERENCE.md](../../HOST-IO-INTERFERENCE.md) section 3.
