# Example cache precheck manual check

This was a disposable-container check of the committed example script, not a
registered gate. The container used the reconstituted R-0 image with Docker
`--network none --memory 2g --cpus 2 --cgroup-parent "$CGROUP_PARENT_DEV_GATES"`
and was removed by its exact name after the checks.

Container: `assay-rust-r0-precheck-check-20261110`.

Commands:

```sh
docker run -d --name assay-rust-r0-precheck-check-20261110 --network none --memory 2g --cpus 2 --cgroup-parent "$CGROUP_PARENT_DEV_GATES" assay-rust-r0:local sleep infinity
docker cp assay/docs/rust-r0/verify-crate-cache.sh assay-rust-r0-precheck-check-20261110:/tmp/verify-crate-cache.sh
docker cp assay/docs/rust-r0/rust-crate-cache.sha256 assay-rust-r0-precheck-check-20261110:/tmp/rust-crate-cache.sha256
```

The intact manifest passed:

```text
exit_status=0
```

The checksum predicate was then hand-mutated from `!=` to `=` and the
mutated script was copied into the container. Running it against the intact
cache printed a mismatch for each of the 23 archives even though every
expected digest equaled the actual digest, then returned 1. This killed the
mutation. The predicate was restored before the normal script was recopied.

With the restored script, the intact manifest returned 0. Appending one byte
to `equivalent-1.0.3.crate` produced:

```text
ERROR: cached crate checksum mismatch: equivalent-1.0.3.crate expected 00d174d5400e5e8fd687ad1049e2f578285fa914201b1af7e8b112a4546bd826 got 8da44fd3206d7772d53e8a57302d3483a0d3d67b80b8cbd4a015fda63d865a66
tampered_exit_status=1
```

Fail-closed input probes returned:

```text
ERROR: crate cache manifest is missing: /tmp/manifest-missing
missing_manifest_exit_status=1
ERROR: crate cache manifest is empty: /tmp/manifest-empty
empty_manifest_exit_status=1
ERROR: cached crate is missing: no-such-crate.crate
missing_crate_exit_status=1
ERROR: malformed crate cache manifest entry: malformed entry
malformed_entry_exit_status=1
```

The relevant `docker exec` invocations ran the script with each of those
manifest paths and captured its exit status. Container cleanup:

```sh
docker rm -f assay-rust-r0-precheck-check-20261110
```

The removal command returned 0.
