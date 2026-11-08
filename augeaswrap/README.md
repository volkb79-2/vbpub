# Augeaswrap

Augeaswrap is a generic host configuration utility. Its first shipped command
group manages host endpoint records: service installers publish their resolved
paths and host facts, and consumers read those values from the process
environment. It does not choose service defaults.

The public command is `augeaswrap endpoints` with `publish`, `withdraw`,
`check`, and `show` verbs. The module API is `augeaswrap.endpoints`. Endpoint
file paths, marker lines, and formats remain compatible with existing hosts.

The endpoint publisher writes `/etc/host-endpoints.d/<owner>.env`, the shared
`/etc/host-endpoints.env`, its managed block in `/etc/environment`, and
`/etc/profile.d/host-endpoints.sh`. See the
[consumer guide](docs/CONSUMERS.md) for usage and the
[endpoint specification](SPEC.md) for the record contract. The
[design guide](docs/DESIGN-GUIDE.md) explains the ownership and concurrency
choices.

The lens package-data directory is empty until an overlay lens is required.
The current milestone is endpoint records; the generic guarded applier and
edit engine are separate follow-on work.

## Development

Install Augeaswrap and its first-party CLI dependency from the local checkout:

```bash
python3 -m pip install --editable ./libraries/cli-extended --editable ./augeaswrap
```

Run its registered gates from this project directory:

```bash
run-gate r0-r1
run-gate r3
```
