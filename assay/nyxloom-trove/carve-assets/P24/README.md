# P24 locked distribution packet

These files are carver-owned inputs and independent acceptance evidence for
P24. Implementers and reviewers must not edit them.

`wheelhouse/`, `build-requirements.txt`, and `wheelhouse-manifest.json` are the
complete Python >=3.11 offline build closure. The requirements file gives pip
the same hashes through `--require-hashes`; the JSON adds exact filenames and
byte counts for independent audit. The five packages are also the exact five requirements
P24 must declare in `pyproject.toml`; this makes transitive resolver choices
explicit instead of relying on whatever compatible `packaging` or
`vcs-versioning` happens to be ambient. `setuptools==84.0.0` supersedes the
provisional 82.0.1 pin because the latter has a published vulnerability fixed
in 83.0.0. Every file was fetched from its official PyPI project release and
is bound here by filename, byte count, and sha256.

`fixtures/assay-1.2.3-py3-none-any.whl` is a positive release-verifier fixture,
not an Assay release. It was built twice in independent source roots from only
the tracked `assay/pyproject.toml` and `assay/src/**` files at anchor
`7c52ecc2f9f500991d2ba74689458ae1e6644a18`, with the prospective setuptools
84 pin, fixed Git identity/timestamps, lightweight tag `assay-v1.2.3`, and
`SOURCE_DATE_EPOCH=946684800`. The two bytestrings were identical. The exact
synthetic commit and dirty/no-VCS witnesses are in `probe-results.json`.

`fixtures/release-manifest.json` is the positive closed manifest for that
wheel. Its one-line canonical JSON is deliberate. Tests mutate one dimension
at a time and never regenerate this expected file with production code.

Apply `skeleton.patch` once at the P24 input revision. It freezes the exact
build requirements and public standalone helper interface, but leaves the
release-manifest parser, wheel metadata reader, and hash-bound verification as
explicit implementation work. Run the locked suite from the repository root:

```text
python -m pytest assay/nyxloom-trove/carve-assets/P24/test_acceptance.py -q
```

The post-implementation target is every test green. A pre-implementation run
without the skeleton fails mechanically because the helper does not exist; the
skeleton run is the controlled red recorded in the JIT report.
