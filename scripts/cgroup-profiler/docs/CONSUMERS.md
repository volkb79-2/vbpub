# cgroup-profiler consumers

The supported operator front door is the repository shell shim:

```bash
./cgprofile --version
# cgprofile 0.1.0
```

The probe prints the version sourced from `pyproject.toml` to stdout, exits 0,
emits no stderr, and is exactly one identity line. The shim routes `--version`
directly to `cgprofile.py`, so this check does not require the analysis/reporting
venv. Help, usage, and configuration diagnostics at every subcommand depth
begin with the CGPROFILE headline as line 1; normal profiling output is
unchanged. Use
`ATTACH-GUIDE.md` for the complete gate integration recipe.
