# P104-SELFREVIEW — Adversarial self-review

- **No hollow tests**: Each test asserts exact status dict fields, exception
  messages, tar member names, or function return values.
- **No over-mocking**: Only os.environ patched for dir test; _zstd patched for
  no-zstd test. All other tests use real function calls.
- **No non-None-only, no weak ranges, no assertion-free bodies.**
- **No pragma, no product edit, no sleep, no host proc.**
- **git diff --check**: Clean.
- **Parity**: Two gate runs identical.
- **Both files 100%**: enrich.py and bundle.py — 0 missing, 0 missing branches.
