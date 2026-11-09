# Lens inventory

This package-data directory carries Augeas overlay lenses. Every `.aug` lens
must have a matching `tests/test_<lens>.aug` behavioural fixture, and
`augparse` must be available to the registered R0/R1 lane.

`Pam_env.aug` is original Augeaswrap work. It maps records from both
`/etc/security/pam_env.conf` and `/etc/environment` into a shared sequence
shape keyed by the `name` field. It stores quote delimiters in the field value
so a source round-trip preserves the exact spelling.

All new lenses must carry their source and licence notes here. The current
project licence is MIT; any future upstream-derived lens requires a reviewed
licence change before it is added. The current `Pam_env` lens does not copy
upstream Augeas code.
