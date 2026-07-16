# P11 — property tests + crash drills (SPEC §14.1/§14.4)

> Tier: sonnet (adversarial test design) · Depends-on: frozen core only;
> wrapper drills skip cleanly until P04 merges · Read first:
> handoff/STANDING.md, docs/SPEC.md §14, src/nyxloom/{types,storage,
> leases}.py, src/nyxloom/wrapper.py docstring.

## Owned files
- `tests/test_properties.py`
- `tests/test_crash.py`

## Oracles — properties (hypothesis; max_examples<=50, deadline=None)
1. **Transition soundness**: for every (state, next) pair NOT in
   TASK_TRANSITIONS[state], check_task_transition raises TransitionError;
   for every pair in it, it does not. Same for attempts. (Exhaustive
   loops, not hypothesis — the graph is small; assert also that terminal
   states have empty successor sets and every state is a key.)
2. **Serde round-trip fuzz**: hypothesis strategies building valid
   TaskStateFile objects (nested attempts with optional receipt/usage,
   gate_results, blocker; text from st.text printable, sizes bounded) →
   `TaskStateFile.from_dict(json.loads(json.dumps(x.to_dict()))).to_dict()
   == x.to_dict()`. Same for Event and Frontmatter. Plus: from_dict with
   an injected unknown key raises ValueError (fuzz the key name).
3. **Replay determinism**: generate a random VALID lifecycle as an event
   sequence — walk TASK_TRANSITIONS from CARVED via random.choice(sorted)
   with a hypothesis-drawn path length, interleaving ATTEMPT_* upserts
   with consistent attempt states — apply via storage.append_and_apply on
   a fresh tmp state root per example (tmp_path_factory), then assert
   storage.replay(project)[t].to_dict() == the incrementally saved
   statefile's dict. Include PROGRESS_RECORDED/LEASE_* events in the mix.
4. **Sequence integrity under concurrency**: multiprocessing, 4 processes
   × 25 append_event each (same project, NYXLOOM_STATE env inherited)
   → events.jsonl has exactly 100 lines, sequences == set(1..100), file
   parses line-by-line (no interleaved writes).
5. **apply_event tolerance**: ATTEMPT_STARTED for an attempt_id never
   created → upserted (appears once); event for an unknown task_id →
   no-op, no exception; TASK_TRANSITIONED violating the graph → raises
   TransitionError (replay is strict on semantics).

## Oracles — crash drills
6. **append-without-save heals**: create task via append_and_apply; then
   raw storage.append_event a TASK_TRANSITIONED (CARVED→QUEUED) WITHOUT
   apply/save (simulating a crash between the two) → on-disk statefile
   says CARVED, replay says QUEUED; after save_state(replay result), disk
   agrees. (This is the SPEC §5.6 'event wins' guarantee.)
7. **statefile atomicity**: no .tmp file survives a save_state; a reader
   (thread) hammering load_state during 200 saves never sees a partial
   json (JSONDecodeError) or a missing file.
8. **flock release on SIGKILL**: child process (multiprocessing.Process
   with a function that acquires leases.acquire('drill') and sleeps) —
   after p.kill()+join, acquire('drill') in the parent succeeds within
   3s (poll). holder_info flips from held to free.
9. **wrapper SIGKILL drill** (skip-guarded): if wrapper.launch_detached
   raises NotImplementedError → pytest.skip('P04 pending'). Else: launch
   a sleep-30 spec, SIGKILL the wrapper pid → no receipt.json; every spec
   lease free within 3s; events end at ATTEMPT_STARTED (no EXITED) — the
   exact daemon-healing precondition.
10. **event-log fsync visibility**: append_event then immediately read
    the file from a SEPARATE process → the line is present (fork+read).

## Guidance
- Keep strategies in module-level functions; no reuse of tmp_state across
  hypothesis examples (function-scoped fixture conflict — build your own
  state root per example via tmp_path_factory.mktemp + monkeypatch-free
  os.environ set/restore in a contextmanager).
- Drill 8/9 polling: time.monotonic loop, 0.1s step, 3s cap.
- These tests define the release bar — do not weaken an assertion to make
  it pass; a genuine core bug is a BLOCKED report (frozen file), and that
  report is a SUCCESS outcome for this package.
