# Rust R-0 image examples

These files preserve the disposable R-0 image experiment. They are examples,
not an assay build target, and are not included in any image build guard test.
R-1 will build and own the real image under `tester-unified-rust/`.

The measured archive manifest is [`rust-crate-cache.sha256`](rust-crate-cache.sha256).
It records the 23 archives in the R-0 subject image; R-1 must generate a fresh
manifest from its own locked dependency closure during the image build. The
precheck example is [`verify-crate-cache.sh`](verify-crate-cache.sh). In the
image, install it as `/usr/local/bin/verify-crate-cache`, bake the generated
manifest at `/usr/local/share/rust-crate-cache.sha256`, and invoke it before
each Cargo command:

```sh
/usr/local/bin/verify-crate-cache /usr/local/share/rust-crate-cache.sha256
cargo test --offline --locked
```

The example Dockerfile expects a BuildKit named context called `subject` whose
root contains `topos/`. From the repository root, an illustrative build is:

```sh
docker buildx build --load --resource memory=2g --resource cpu-quota=200000 \
  --build-context subject=/path/to/subject-context \
  --build-arg RUST_R0_BUILD_ID=rust-r0-example \
  -t assay-rust-r0:example -f assay/docs/rust-r0/Dockerfile .
```

The example is not wired into `tester-unified` or the repository's Dockerfile
guard tests. The S1′–S6 measurements and captured raw output are in
[`../RUST-R0-SPIKES.md`](../RUST-R0-SPIKES.md).
The example precheck's manual acceptance and killed-mutation record is in
[`evidence/precheck-manual-check.md`](evidence/precheck-manual-check.md).
