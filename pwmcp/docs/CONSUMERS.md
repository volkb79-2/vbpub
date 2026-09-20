# PWMCP test consumers

Run the release-equivalent suite with:

```bash
./run-gate.py r1
```

This executes in `tester-unified` and installs Assay from the selected vbpub
worktree. The resumable mutation lane is:

```bash
./run-gate.py r2
```

It writes progress and verdict state below the git-ignored `.assay/` directory.
Use the R1 lane for the normal CMRU release gate; run R2 separately when the
long mutation campaign is useful.
