# Python client packaging

`python/pyproject.toml` defines the `hostobs-client` distribution. It installs the
`hostobs_client` package and the `hostobs-mark` command, whose target is
`hostobs_client.mark:main`. The client has no runtime dependencies; it uses only
the Python standard library and remains importable from a zip archive.

The `conformance` lane runs `python/tests/test_packaging.py` as part of its
standard-library unittest discovery. That test builds a zipapp with Python's
stdlib `zipapp` module in the lane, then imports the package with `python3 -I`
and verifies that the declared `hostobs-mark` target resolves and runs its help
path. The lane image `tester-unified-rust` has no pip or ensurepip (HOB-44), so
the lane does not build or install a wheel.

The distributable wheel is built by `cmru handler wheel-build` from
`hostobs/python/` as part of the A1 release build. That handler uses its
wheel-builder image and the build requirements declared above. The conformance
zipapp is lane evidence; it is not the release wheel.

The version has one source: `setuptools-scm` reads the matching `hostobs-v*`
Git tag. A1's CMRU release contract uses the same SCM strategy and tag prefix;
on the tagged release commit CMRU supplies that derived version to the wheel
build. There is no literal or fallback version in this project, and A2 assigns
no release version or tag.
