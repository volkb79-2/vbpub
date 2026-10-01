# P108 log — exact host-network coverage

Eight exact tests were added without source changes. Parser cases use literal
strings; provider cases use worker-unique `tmp_path`, virtual paths, fixed time,
and a deterministic command runner.

Focused xdist:

```text
8 passed in 6.37s
```

Full receipt runs:

```text
run 1: 2070 passed in 71.55s; diff-coverage OK; exit 0
run 2: 2070 passed in 66.54s; diff-coverage OK; exit 0
```

Both complete target records were empty and hashed to:

```text
ae4ee2ac63f9496ba38d20c5da5180c61431c50dafb81b381da69cb8747f0f3f
```
