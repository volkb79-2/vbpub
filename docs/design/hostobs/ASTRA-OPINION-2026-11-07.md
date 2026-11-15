<!-- origin: 2026-11-07; status: design; copied from the controller program dir 2026-11-10 -->
<!-- source: reports/hostobs-ASTRA-OPINION.md -->
<!-- relation: hostobs independent opinion -->
<!-- Evidence scrub 2026-11-10: environment-specific home, checkout, worktree, and per-run temporary paths were normalized to neutral placeholders; command output and verdict text are otherwise unchanged. -->

# hostobs: independent second opinion

2026-11-07 · Architecture, evidence semantics, Linux correctness, protocol and consumer review

**Recommendation: build hostobs, but revise the evidence model and resource bounds before freezing the wire format.** Rust is a sound choice; replacing repeated consumer-side discovery with one observer is valuable. The largest risks are incorrect evidence that looks authoritative, unbounded bookkeeping, and an actuator whose authority leaks into the probe.
The binding addendum is the specification: two processes immediately; binary bulk paths; Python ctl; optional Python library with explicit require_profiling; enrichment and DAMON in v1. I am not reopening those decisions. A useful first milestone can precede the complete v1 without weakening them.

## Reading basis and limits

[C] means a finding verified by reading code; [D] means a document or published specification; [I] means my inference or recommendation; [U] means unmeasured here. Unlabelled recommendations are [I], not benchmark results.
C: paths are repository-relative at commit c3fea720846cf5921d7f184f1a56f135d0189c0d, the origin/main revision inspected. The main checkout HEAD was 8b398ac56831284d04b195513edd039981d1f97e; I did not mix its consumer files into C.
S: paths are relative to topos/ in the sampler worktree, HEAD 193bf601f83515507145d30123b03c995f126593. P: paths are relative to topos/poc/ratatui-tui/ in the ratatui worktree, HEAD 636dfb9557c0fcb1f24e48f7666a8ee4c6bb9b30. Relevant inspected worktree paths were clean.
R: means <controller-program-dir>/reports/. Plan means R:hostobs-PLAN-v3.md, read together with its binding hostobs-DECISIONS-20261107.md and both telemetry-platform predecessors.
The console plan was read from C:topos/docs/PLAN-2026-11-CONSOLE.md; its detailed interaction contract is C:topos/TUI-SPEC.md. The named console-plan file was absent from the sampler worktree.
I read the requested consumer implementations and design documents, plus relevant estate doctrine. Source anchors below identify the particular implementation behind a finding; they do not assert that every repository file was audited.
No hosts were contacted; no packages, tests, workloads, gates or benchmarks were run. Public upstream documentation was read. Small capacity calculations are arithmetic, not measurements. This report is the sole intentional file write.
**Blinding caveat:** starting ideas were fixed after the binding addendum and an opening scope skim, before the detailed architecture review. That skim included some v3 recommendations, so this was not a formally blinded exercise.

## A. Starting architecture ideas, then comparison

My starting design would have these properties, independently of the detailed recommendations:

1. One inexpensive host/cgroup observation loop; targeted process and session work are subscriptions with explicit budgets, not additional whole-host scans.
2. A measurement is an identity, value, interval, source and quality state. A naked number is insufficient evidence.
3. Host boot identity, daemon epoch, cgroup incarnation and process start identity are separate concepts; display names never identify a lifetime.
4. Keep raw cumulative counters as integers. Derive display rates from actual observation intervals, while retaining the inputs needed to audit them.
5. Separate ordinary telemetry, evidence windows and destructive authority. A useful chart and a valid resource-limit proof have different completeness requirements.
6. The actuator owns every privileged kernel mutation, including mutations needed to measure something. It accepts a small typed request vocabulary, not arbitrary paths or commands.
7. A bounded hot ring plus bounded segment files provides recent history and focus recording. The incident bundle uses that same data model.
8. Presentation clients choose layouts, sorting, explanations and comparisons. The daemon supplies facts and provenance rather than UI-specific row strings.
9. Loss is representable and attributable: sampling loss, storage loss, transport loss and missing privilege are different states.
10. Overload sheds optional work before evidence work, then explicitly refuses work it cannot support. There is no promise of perfect observation under arbitrary starvation.
11. Session annotations and links to external run IDs create more value than a large built-in analytics framework.
12. Use a small language-neutral binary specification, with independent reference fixtures, and optimize only the paths whose total cost has been measured.

**Convergence:** the plan's central sampler, tiers, endpoint separation, explicit missing states, Python API and binary volume path fit this architecture. The addendum fixes the most concerning original privilege and v1-scope choices.
**Divergence:** I would not transplant the current store codec unchanged, identify history by reusable path IDs, keep all session data until stop, or treat four queued frames as a memory bound.
I would also add absolute-time batch history queries, a bounded process-detail query, and authenticated session annotations to the core contract. Those are direct consumer needs, not speculative platform features.
Keep the predecessor's freshness and independent-actuator checks. Remove its unsupported promise that read/reset peak buckets are seamlessly exact, and do not bring back crash-safe session recovery.

## B. Critique: the five most dangerous assumptions

### 1. “The sampler's stored representation already has the evidence semantics”

[C] S:sampler/src/store.rs:118 stores numeric values as f64; :869 takes the display value rather than the collector's raw counter. The collector does retain raw u64 values and subtract them before conversion (S:sampler/src/collect.rs:331).
[C] C:scripts/cgroup-profiler/src/cgprofile/summary.py:590 and C:run-gate-project/nyxloom-trove/RG55-INTERFACE-CONTRACT.md:416 require container-lifetime absolute counters under RW21, whereas shared sessions use deltas.
Thus Plan:146 and :173 cannot make the existing delta-oriented store body the complete evidence representation. A lifetime counter cannot generally be reconstructed from a stream that starts late or has missing samples.
[C] S:sampler/src/collect.rs:688 detects a replaced cgroup inode and resets collector state, but :699 obtains its dictionary identity from the same pathname. S:sampler/src/dict.rs:101 interns that name. Historical series can therefore join distinct lifetimes.
**Fix:** retain typed raw counters and explicit incarnation IDs; carry display deltas as optional derived fields. Make evidence schemas deliberate fresh contracts, not accidental consequences of the plotting codec.

### 2. “No crash-safe sessions makes stop and storage simple”

Plan's F2 and §1.3 defer sample output until stop and remove stop-incomplete. This trades persistence complexity for potentially unbounded memory and finalization latency.
[C] The current summary is not constant-memory: its exact percentile tree allocates per distinct value (C:scripts/cgroup-profiler/src/cgprofile/summary.py:1). Exact percentiles are a real memory choice even without retained raw samples.
[C] Current sessions append samples during recording (C:scripts/cgroup-profiler/src/cgprofile/serve.py:1646 and :2046); run-gate explicitly handles incomplete stop and records cleanup failures (C:run-gate-project/run_gate.py:3025, :3912).
**Fix:** append bounded best-effort sample blocks while recording; seal a summary at stop. A restart still means session-lost: no recovery protocol, durability promise or crash-safe continuation is required.
Separate “measurement sealed” from “placement/DAMON cleanup confirmed.” A stop response can be pending with an idempotent status token; removing the old spelling does not remove the operational state.
A 4 Hz session averaging 1 KiB per sample produces about 4.61 GiB in fourteen days. That illustrative calculation alone rules out duration-independent RAM assumptions.
For exact contract percentiles, bound accepted duration/sample count or compute from the spool with bounded external sorting. An approximate sketch must advertise a changed statistic and error contract.

### 3. “The measured small host predicts churn and process-scale behavior”

[D] The useful baseline is approximately 84 cgroups, 1.71% of one core and 10.5 MB sampler peak RSS. The rendering numbers exclude terminal writes and were collected under unequal load conditions (C:topos/docs/PLAN-2026-11-CONSOLE.md:123, :131).
The reported 8–9 KB process list is a selected list. P:README.md:73 documents a sampler cap of 64; “expand all processes sent” is not “enumerate all processes in this cgroup.”
[C] Dictionaries retain identities for the longest history tier; names are cloned into snapshots on dictionary changes (S:sampler/src/dict.rs:8; S:sampler/src/serve.rs:123). Churn makes both retention and copying expensive.
At ten unique observed cgroups per second, fourteen days contains 12,096,000 incarnations. Even 128 bytes each is 1.44 GiB before maps, allocator overhead and snapshot copies.
**Fix:** retire inactive metadata into self-contained disk segments; keep only bounded active/recent indexes in RAM. Benchmark churn independently from a large static tree.
At 50,000 PIDs, reading one stat file per PID already implies roughly 150,000 open/read/close calls per sweep, before thread lists, IO, command lines or enrichment. A compact 128-byte row would still mean 6.4 MB per complete frame.
Use fair, budgeted background scanning plus explicit targeted detail. State scan age and coverage. A 1 Hz poll can miss whole short-lived cgroups; do not claim lifecycle completeness without an event source.

### 4. “A read-only probe can receive privileged measurement descriptors safely”

The split is right, but “measurement” is not synonymous with “read.” Resetting memory.peak, configuring DAMON and refreshing DAMON results mutate kernel interfaces.
Passing an O_RDWR peak descriptor to the probe gives it the ability to write that file; a read-only mount does not revoke authority already present in a descriptor.
Duplicated or SCM_RIGHTS-transferred descriptors refer to the same open file description. They do not provide independent peak-reset windows. [Linux UNIX-socket semantics](https://man7.org/linux/man-pages/man7/unix.7.html).
**Fix:** the actuator opens and owns each resettable peak window, performs its narrowly specified operations, and returns bounded measurements. Keep the probe/actuator transport typed and independently authenticated.
The actuator must derive and pin the target from an authenticated caller, allowed subtree and current kernel identity. A probe-supplied pathname, token or docker label is a locator, never sufficient authorization.
Where a broker relays an accepted client socket, re-read its kernel peer credentials; where it relays claims, use an actuator-issued grant bound to identity, purpose, target incarnation, expiry and resource budget.
The predecessor's independent action confirmation remains essential: a compromised or stalled probe must not authorize killing work, enabling IO policy or changing unrelated host state.

### 5. “Four frames, undroppable metadata and a timeout bound every client”

Plan:155 limits frame count, not retained bytes; :157 exempts DICT/REP/EVT from dropping. At the stated maximum, four 16 MiB frames for 24 telemetry clients could retain 1.5 GiB without sharing.
A peer that accepts a byte periodically defeats a “no bytes for five seconds” rule. Low event frequency does not bound a permanently slow client's backlog (Plan:160).
Dictionary ordering alone does not make reused IDs safe for cached history, pending replies and independent subscriptions (Plan:153). Epoch and dictionary-generation semantics are necessary.
**Fix:** per-client and global byte budgets, bounded outstanding replies, bounded event backlogs, and a maximum age for completing a frame. Disconnect clients that cannot preserve mandatory ordering within those bounds.
Share immutable payloads when subscriptions match; cap distinct selections. A hundred custom filters must not imply a hundred sampling loops or unbounded encoding jobs.
Dropped value frames need recoverable semantics. Do not introduce baseline-dependent compression until resynchronization is explicit and tested.

### Specific plan/code disagreements to resolve

| Claim or implication | Verified implementation | Required correction |
|---|---|---|
| Transport replacement leaves consumer validators unchanged, Plan:210 | C:assay/src/assay/resource_limits.py:1181 validates an exact key set and contract 2 | Specify a transport-envelope unwrap and new validator contract; extra proto/epoch keys are not silently compatible. |
| Series columns adequately carry the store result, Plan:148 | S:sampler/src/store.rs:1184 returns timing/coverage metadata | Preserve actual duration, coverage and aggregation identity in SERIES; nominal step is insufficient. |
| Current process list is intrinsically small, Plan:147 | P:README.md:73 describes a cap; C:topos/TUI-SPEC.md:75 requires expansion | Add bounded targeted process enumeration; do not call truncated output complete. |
| Session statistics can be copied as general telemetry statistics | S:sampler/src/store.rs:938 averages samples; C:scripts/cgroup-profiler/src/cgprofile/summary.py:326 uses exact nearest-rank percentiles | Name sample-weighted versus time-weighted statistics; never silently substitute one. |
| Existing clock handling is a reusable freshness oracle | S:sampler/src/serve.rs:166 uses wall time for watchdog age | Use monotonic freshness; wall-clock rollback must not conceal a stalled sampler. |
| Control capability is irrelevant to observer clients | C:run-gate-project/run_gate.py:8899 performs observer preflight through control version | Move observer discovery onto observer hello; update the consumer deliberately. |

The original v3 root daemon and omitted DAMON/enrichment are superseded decisions, not unresolved defects. Rewrite the document to remove the contradiction before implementation.

### Linux accounting and timing traps

**Peak windows:** memory.peak's reset state is per open file description; reset establishes the current usage as a floor, not “bytes allocated since open.” Hierarchical charges and existing memory remain included. Feature-detect the operation and distinguish permission denied, unsupported and controller absent. [Kernel cgroup documentation](https://www.kernel.org/doc/html/latest/admin-guide/cgroup-v2.html?highlight=freezer).
The v6.12 implementation contains the per-open peak machinery; the stated 7.2.x estate floor is plausible, but I did not inspect those hosts. Version strings alone are not runtime evidence. [v6.12 memcontrol source](https://raw.githubusercontent.com/torvalds/linux/v6.12/mm/memcontrol.c).
Reading a peak and then resetting it is not an atomic exchange: a transient peak between the two operations can disappear from both adjacent buckets. Advertise boundary uncertainty; do not promise exact contiguous bucket maxima.
An independent open/reset per explicit evidence window is useful. Its begin/end operations still have timing spans, and “exact cgroup high-water mark” does not prove exclusive ownership of that cgroup by a workload.
[C] Existing windows correctly open O_RDWR and reset/read through the same descriptor (C:scripts/cgroup-profiler/src/cgprofile/observer_windows.py:199); preserve that behavior in the actuator, with clearer failure states.
**Cgroup identity:** pin a directory handle where possible and record mount identity plus inode and a daemon-assigned incarnation. An inode alone is reusable; a path alone is mutable. Missing on rescan is not automatically proof of deletion.
**Hierarchy:** do not sum inclusive parent counters and their descendants into host totals. Distinguish local/hierarchical events, configured/effective constraints, and cgroup memory charges/process RSS.
**Memory protections:** memory.min/low are protection policies, not reservations or simple available-byte credits; overcommit depends on usage below the configured boundaries. CPU weights are relative shares, not hard quotas. [Kernel controller semantics](https://www.kernel.org/doc/html/latest/admin-guide/cgroup-v2.html?highlight=freezer).
[C] mdt_capacity's “effective_memory_min” scales configured sibling minima without their usage (C:mdt-cockpit/host-setup/mdt_capacity.py:649). Treat that as an estimate, not a kernel-certified effective guarantee.
**CPU:** subtract raw usage_usec as integers, divide by actual elapsed time, and expose cores separately from percentage of available capacity. Capacity normalization needs cpuset, ancestor quota and quota-period provenance.
A lifetime CPU numerator divided by only the attached session's duration is misleading when attachment is late. Keep lifetime evidence and interval rates separate; RW21 does not make their time bounds interchangeable (C:scripts/cgroup-profiler/src/cgprofile/summary.py:619).
A sampling interval can straddle a limit change. Show its limit history or mark the normalization mixed; do not apply today's quota retroactively.
**PSI:** total deltas support interval stall fractions; avg10/60/300 are kernel moving averages. “Full” means all non-idle tasks are stalled, while host CPU full is undefined/zero. Never add some+full. [Kernel PSI documentation](https://cdn.kernel.org/doc/html/latest/accounting/psi.html).
PSI reports experienced stall, not a causal attribution to another cgroup. Kernel triggers can cheaply wake an incident detector, but registering them is another privileged write and trigger semantics differ from an arbitrary UI threshold.
**IO:** parse keyed fields and device IDs without order assumptions; treat absent keys separately from zero. Resolve stacked-device topology before summing physical totals.
[C] S:sampler/src/collect.rs:434 falls back to “bottom device” when topology cannot be read. That silently invents a fact; retain an unknown-topology state instead.
Do not derive per-cgroup latency or device saturation from io.stat byte rates alone. Disk in-flight is a gauge; most disk statistics are cumulative, and utilization is not a universal SSD saturation measure. [Kernel IO-statistics documentation](https://www.kernel.org/doc/html/latest/admin-guide/iostats.html).
**Processes:** key by boot/PID/starttime, optionally pin with pidfd for targeted actions. PID and cgroup namespace translations belong to the host-side observer, not client-provided paths.
RSS sums double-count shared pages; process IO misses vanished children; parent CPU accounting may include waited-for children. Keep source/lower-bound labels and attribution tests rather than renaming everything “usage.”
[C] C:scripts/cgroup-profiler/src/cgprofile/observer_windows.py:585 builds one process table per due round. Preserve this sharing; a separate full PID scan per window would be disastrous.
**Rates under jitter:** timestamp each sampling group, preferably with read-start/read-end bounds. A global tick timestamp does not make thousands of file reads simultaneous.
Use CLOCK_MONOTONIC for intervals, deadlines and freshness; realtime is a display/index correlation. MONOTONIC excludes suspend; record BOOTTIME as well if suspend must be identified, and explicitly rebaseline or classify the gap. [Linux clock semantics](https://man7.org/linux/man-pages/man2/clock_gettime.2.html).
[C] The sampler already uses Instant for tick duration (S:sampler/src/main.rs:165); the defect is not “all timing is wall time.” Existing cgprofile summary duration does subtract wall timestamps (C:scripts/cgroup-profiler/src/cgprofile/summary.py:555).
A fresh hostobs summary should fix duration semantics with a documented schema decision; “byte-for-byte old results” must not preserve wrong elapsed time across wall-clock adjustments.
On a lower counter, distinguish a known-width wrap from reset/recreation; otherwise emit reset and establish a new baseline. Never turn unsigned underflow into a spectacular usage spike.
Do not assume “first discovered after startup” means “born after startup.” The sampler currently uses that shortcut (S:sampler/src/collect.rs:704); visibility loss can make an old counter look newly born.
After missing samples, a counter delta spans the full actual interval. It cannot honestly be assigned entirely to the final nominal bucket. Zero elapsed time yields no rate.
For gauges, choose and document sample mean or time-weighted estimate. For counters, aggregate sums and covered duration. A percentile of bucket percentiles is not a percentile of the observations.
Events inferred from counter changes have an observation interval and count, not an exact occurrence timestamp; the sampler's OOM counter detection cannot identify an unobserved victim (S:sampler/src/collect.rs:672).

### Privilege, overload and retention

Probe capabilities such as DAC_READ_SEARCH or SYS_PTRACE still expose sensitive information. Prefer narrowly granted reads; describe any retained capability honestly rather than calling the process harmless.
Do not give the probe the Docker socket merely to obtain names: it is powerful daemon authority. Put a narrowly implemented metadata fetch in the trusted boundary, return only allowlisted fields, and cache it off the sampling path.
Systemd/unit names and docker labels need expiry, source and identity binding; stale enrichment must never relabel a replacement cgroup. Limit attacker-controlled string lengths before allocation and transmission.
Cmdline and especially environ access should be targeted and operator-authorized; neither is a safe general-purpose token-discovery mechanism. Export an explicit field allowlist rather than promising perfect heuristic redaction.
DAMON v1 should preserve stat-only behavior and ownership of allocated slots. Configuring, refreshing and stopping monitoring are actuator operations; failed stop/readback must quarantine the owned slot.
Placement also needs its own safety contract: verified delegated scope, allowed ancestry, controller enablement without violating cgroup v2 topology, and restoration only to the original still-valid target. Preserve the ownership reasoning in C:scripts/cgroup-profiler/src/cgprofile/placement.py:1; a generic move-PID verb is too much authority.
No crash-safe recording does not mean forgotten kernel side effects are safe. On actuator restart, reconcile only unambiguously owned scopes/slots; otherwise expose cleanup-required and refuse reuse. Never infer ownership from an arbitrary matching pathname.
[C] C:scripts/cgroup-profiler/src/cgprofile/damon.py:855 selects the stat action; :898 refreshes tried regions; :957 confirms cleanup. Preserve the last check even though sessions are not crash-safe.
DAMON results are adaptive sampling estimates, with region geometry and sampling configuration; do not present them as exact working-set bytes. Bound regions, targets, updates and kernel monitoring overhead. [DAMON administrative interface](https://www.kernel.org/doc/html/v7.0/admin-guide/mm/damon/usage.html).
A service's RSS or CPU quota does not necessarily account for all kernel work it induces. Measure kdamond and metadata/page-cache costs as well as hostobs userspace.
Set explicit caps for open FDs, active subjects/windows, distinct subscriptions, queued bytes, pending queries, metadata, decompressed blocks and session bytes. Derive host limits from host policy; the plan's 256 MiB/35% examples are not portable sizing evidence.
Use fixed worker pools and bounded request admission. Timing out an await does not cancel a blocked filesystem read; repeated replacement workers can exhaust the process. The sampler already acknowledges this hazard (S:sampler/README.md:106).
Under pressure: stop admitting optional work, decimate process/enrichment work, stop new DAMON sessions, reduce discretionary retention work, then report evidence gaps/refusals. Protect the inexpensive core, while acknowledging it can still be starved.
An old observation must never become proof of an idle job. Actuator freshness and independent confirmation are mandatory even when the probe is throttled or its queue is full.
Use separate storage byte quotas for history, active recordings and sealed evidence, plus free-space headroom and explicit eviction order. Age/count retention alone does not bound bytes; enforce quotas during sessions, not only at stop.
Reserve capacity before accepting required recordings; a reservation is a service budget, not a claim that the filesystem can never fail. Disk-full and short-write outcomes remain explicit.
Keep stored dictionaries with their segments and garbage-collect by reachability. A long retention horizon must not force all expired process/cgroup labels into the hot index.
For no-crash-safety v1, partial segments may simply be discarded on restart. Clients infer loss from disconnect/epoch change; a dead daemon cannot reliably emit a final daemon-restart event.
Do not add a database, durable session transaction log or generalized scheduler to solve these boundedness problems.

## C. Wire protocol: choose typed columns, not a transplanted varint object stream

**Live choice:** a small documented fixed-width little-endian column format in length-delimited sections, with JSON only for small control messages. **History choice:** bounded self-contained segments using the same typed columns, independently compressed blocks, checksums and a small time index.
This is a custom format, with a real maintenance obligation. Keep it deliberately narrower than a serialization framework; do not describe it as “about 80 lines” once validation, evolution and hostile inputs are included.

| Option | Assessment for this service |
|---|---|
| Hand-written fixed-layout columns | Recommended: predictable bulk decoding, explicit integer widths/states, cheap Python struct/memoryview access; requires a written schema and independent conformance suite. |
| postcard | Stable documented format since 1.0, compact and good Rust ergonomics; Python still needs schema-specific decoding and many varint operations. Reject on fit, not a blanket claim of unstable serialization. [Official format](https://docs.rs/postcard/latest/postcard/). |
| bincode | Reasonable Rust typed encoding with explicit configuration/version choices; weak fit for the desired Python bulk views and evolving cross-language schema. Do not rely on the plan's unsupported “no stable spec” dismissal. [Official project](https://github.com/bincode-org/bincode). |
| rkyv | Strong Rust archive access; format controls exist for endianness/alignment. Python access and schema evolution become your problem. Useful internally only if measured, not the public wire. [Format controls](https://rkyv.org/format.html). |
| FlatBuffers | Best off-the-shelf runner-up: scalar vectors, evolvable tables and generated Rust/Python access. A schema is useful here; the real tradeoff is code generation/runtime dependencies versus custom-code ownership. [Internals](https://flatbuffers.dev/internals/). |
| Cap'n Proto | Efficient segment/pointer format, but Python integration and word-oriented representation add machinery for simple columns. A compiled client is not universally forbidden; it is unnecessary for this stdlib-oriented API. [Encoding](https://capnproto.org/encoding.html). |
| Protobuf | Good control/config interoperability and evolution; field decoding/materialization is less suitable for direct column access. An acceptable alternative if ecosystem integration overtakes minimum footprint. [Encoding guide](https://protobuf.dev/programming-guides/encoding/). |
| MessagePack / CBOR | Useful typed debug documents, but generic nested objects recreate much of the allocation/dispatch cost. Prefer JSON for debugging and columns for volume. [MessagePack spec](https://raw.githubusercontent.com/msgpack/msgpack/master/spec.md), [CBOR RFC](https://www.rfc-editor.org/rfc/rfc8949.html). |
| Arrow IPC | Excellent analytical column interchange, validity and dictionaries; substantial ecosystem/schema machinery for a small daemon and a stdlib Python decoder. Offer offline export later. [Columnar format](https://arrow.apache.org/docs/format/Columnar.html). |
| Parquet | Excellent sealed analytical export, poor live-tail primitive: row groups and end-of-file metadata complicate continuously readable recordings. Do not run a second authoritative history implementation in v1. [File format](https://parquet.apache.org/docs/file-format/). |

### Concrete live contract

Keep one length-prefixed framing layer. A fixed header should identify payload length, message kind, flags and correlation ID; the initial hello negotiates protocol major/minor, schema features, limits and daemon/boot identities.
Specify integer widths, signedness, units, little-endian order, maximum counts, UTF-8 rules and whether reserved bits must be zero. Validate multiplication/addition before allocating or slicing.
A bounded section directory gives each section a type, version and length. Unknown optional sections can be skipped; unknown required semantics fail explicitly. Skipping an unknown frame must not silently skip required dictionary or baseline state.
For each metric column, carry metric ID, scalar type (u64/i64/f64), entity IDs or an explicit shared row index, values and quality bytes. A metric definition includes unit, kind, scope, source and permitted aggregation.
Preserve u64 cumulative counters. Above 2^53, f64 cannot represent every integer; JSON representations also need a documented exact-integer policy for clients that use binary64 numbers.
Carry sample sequence, observation interval, sampling-group identity, boot/daemon epoch, dictionary generation and loss counters. Series additionally carries covered duration, sample count and aggregation kind per bucket where applicable.
Do not send only nominal step_s. Record missing, stale, reset and incomplete explicitly; zero and empty arrays are values, not absence sentinels.
Use full independently interpretable value snapshots for the selected fields initially. Absolute counters let a consumer bridge a dropped display frame without requiring a compression baseline.
A per-interval delta is not the same as delta encoding against the previous wire frame; the former loses interval detail when dropped, while the latter can make the next frame undecodable. Specify both distinctions.
Dictionary updates can be incremental within a generation. IDs must never change meaning within that generation; a reset sends a complete new dictionary before values and invalidates affected client caches.
Separate the long-lived entity incarnation from the compact connection-local dictionary index. Query replies and exported segments carry their own dictionary context.
Use bounded dictionary generations rather than retaining every dead name in every subscriber. No client may pin unlimited old generations by never finishing a request.
Put numeric process columns on the binary path too; names/comm/unit/cgroup strings use dictionaries. Full command lines remain separate bounded detail replies. The current small PROCS JSON array is not a scaling contract.
Stream fresh views; retain poll for scripts and diagnostics. Reuse sampled immutable batches across clients; field selection affects encoding/query work, never collection identity or independent kernel reads.
Never discard the remainder of a partially written frame and continue with another: either finish it or close the stream. Drop only entirely unsent replaceable frames.
Budget bytes, outstanding requests and frame age. If mandatory metadata/events exceed those budgets, close with an observable reason; “never dropped” must mean disconnect on overflow, not unbounded storage.
Make control/status and observer operations usable even when a telemetry subscriber is slow. Large history replies should be paged/chunked under a separate query budget.
Default live transport to uncompressed local frames. Negotiate independent block compression only when measured savings exceed CPU/allocation cost; cap expanded size and never require a long compression history to reconnect.

### Cheap decoding, history and conformance

Rust should use checked slices and explicit little-endian loads, not cast arbitrary network bytes into native structs. Alignment and padding must not become ABI dependencies.
Python memoryview can retain column slices without copying; struct.unpack_from reads explicit endianness. array.frombytes copies and uses native byte order, so the plan's example is neither automatically zero-copy nor endian-independent.
“Zero-copy” should mean avoiding bulk reconstruction where useful, not pretending that framing, strings, validation and UI rows allocate nothing.
Store sealed segments with boot/epoch, time correlations, dictionary snapshot, typed data blocks, CRCs and a bounded index. Compress each block independently with zstd; choose block sizes from measured query/CPU tradeoffs.
Raw blocks and rollups must identify their distinct statistical semantics. Preserve raw inputs for evidence horizons; longer console retention can retain explicitly lossy rollups.
Parquet belongs in an optional offline export command for release comparisons and notebooks. Exporting does not make it a second live store, and the daemon need not depend on Arrow.
A recording can remain best-effort and session-lost on restart while still bounding RAM and enabling ordinary in-session history reads.
Ship one language-neutral fixture corpus: canonical encoded bytes, decoded typed values, dictionaries, quality states and independently calculated expected rates/summaries.
Cover u64 boundaries, negative signed values, empty selections, Unicode, missing vs zero, unknown optional fields, required-feature rejection, truncated/oversized frames, invalid lengths and malformed dictionary references.
Cross-check Rust encode → Python decode and Python reference encode → Rust decode. Do not generate all expected outputs with the production implementation and call that independent validation.
Add deterministic stream scenarios: dropped full frames, replacement of queued frames, partial writes, reconnect/epoch change, dictionary rollover, interleaved query replies, a client that dribbles bytes, and decompression limits.
Use model/property tests with fake clocks for jitter, counter resets, wall-clock jumps, suspend classification and time-weighted aggregation; kernel semantics need dedicated VM lanes.
Measure bytes, allocations, CPU, total frame construction and tail service latency on realistic 100/1,000-cgroup and 5k/50k-PID shapes. Those are characterization results, not host-load-dependent correctness gates.

## D. What Topos actually needs

The source of the interaction contract is C:topos/TUI-SPEC.md:75, :109, :113, :129 and :169, plus the console redesign and P:README.md. The Rust TUI remains the operator's open choice; hostobs should not bake in ratatui widgets.

| Screen / interaction | Daemon provision | Client responsibility |
|---|---|---|
| Single live view | One coherent selection snapshot: host, devices, cgroup incarnations, process highlights, freshness | Column priorities, layout, hot-row emphasis, terminal adaptation |
| Sorting and filtering | Typed metrics, names and state; server selector for bounded large result sets | Stable visible sort, filter syntax, preservation of selected identity |
| Cgroup tree and noise collapse | Parent incarnation, creation/disappearance observations, temporal labels | Expand/collapse, hidden-child summaries without double-counting |
| PSI heat and pressure strip | PSI totals/averages, actual intervals, threshold events with observed bounds | Colors, heat history, explanations; correlation must not imply causation |
| Per-process expansion/drill-down | Targeted paged process snapshot, start identity, cgroup binding, scan coverage, operator detail | Drill-down navigation, ranking, explicit truncation/age display |
| Side pane | Effective/configured limits with origin, events, per-device IO, enrichment status | Explain constraint path and show unavailable evidence clearly |
| Freeze / step recent frames | Stable sequence IDs and frame timestamps | Local bounded frame cache, freeze without stalling socket consumption |
| Scrub older history | Absolute t0/t1 queries, batch metrics/entities, as-of topology/labels and available-resolution metadata | Cursor, interpolation policy: show gaps, never fabricate continuity |
| Replay incident recording | Self-contained bundle and seekable segment index | Same view renderer over a file-backed client interface |
| Compare snapshots / “what changed” | Stable identities, raw counters, limits/label versions, presence at each time | Separate new/gone/changed entities, interval differences, sortable deltas |
| DAMON pane in v1 | Regions, ages/access statistics, address-space mode, sample configuration and target identity | Heat visualization, zoom, estimate labels and coverage |
| BPF pane later | Explicit unavailable capability now; future bounded typed enrichment | Hide or explain unavailable pane; never display synthetic empty success |
| Lane/session overlays | Sanitized read-only session index, run IDs, phase annotations and target associations | Timeline bands, selection linking and tool-specific names |
| Record / bookmark incident | Bounded focus request, pre-existing hot-ring slice, annotation, export handle | Choose target/window and make an honest retention/capacity preview |

[C] Existing series selects one entity/metric/tier and a relative window (S:sampler/src/store.rs:1166). It is not an as-of snapshot API. Reconstructing a large historical table via one RPC per cell is an avoidable design mistake.
Add two bounded query shapes first: series_batch(selection, metrics, t0, t1, resolution) and snapshot_at(time, selection, fields). Return actual available intervals and a stable pagination token tied to a snapshot.
Do not promise retrospective process detail when no process history was retained. Record selected process histories during focus sessions; the UI distinguishes “not recorded” from “no processes.”
Expose temporal limits and labels alongside numeric history, or a replay will explain yesterday's pressure using today's configuration.
A useful inexpensive UX is “pin this incident”: preserve a bounded pre-trigger ring plus a bounded future interval, with a user note and the active lane IDs. It need not introduce crash recovery.
Another is an evidence drawer: for any red cell, show raw inputs, interval, source, constraints, age and missing reasons. This makes the observability system auditable by humans.
Add a “what began near this pressure rise?” list of newly observed cgroups, limit changes and lane phases, explicitly described as correlation.
Shareable bundles should be field-allowlisted and self-contained. A recipient should replay them without access to the original host, daemon, Docker or global dictionaries.
Perfetto timelines and OpenMetrics export are useful client-side adapters. pprof requires actual stack samples from a profiler; aggregate CPU counters cannot manufacture a meaningful flame graph.

## E. Other consumers: preserve their actual contracts

| Consumer | Necessary service behavior | Gap / recommendation |
|---|---|---|
| run-gate profiling | Session boundaries before container removal; phase/run metadata, per-target evidence, cleanup status | Preserve finish-before-remove ordering (C:run-gate-project/run_gate.py:3936); late stop must not resolve a replacement pathname. |
| run-gate control/watch | Bounded progress/freshness events, reconnectable status, independent action authority | Keep policy and waiting decisions in run-gate; hostobs supplies facts and scoped acts, not a second admission scheduler. |
| run-gate worker profiles | Peer-bound process-tree open/close, source/missing/context fields, 250 ms requested cadence | C:run-gate-project/run_gate_profile.py:77, :100 records NO_MEASUREMENT without changing test verdict; required-evidence policy needs a distinct infrastructure outcome. |
| assay evidence and limits | Host-derived namespace/ancestor limits, exact identity binding, window quality and failure states | Preserve strict validation and anti-migration checks; process-tree lower bounds are not exclusive cgroup measurements. |
| iogovmon | Device topology, raw counters/pressure, observation intervals, owner/quality metadata | It retains IO policy ownership and independent enabling checks; hostobs must not quietly become an io.cost controller. |
| iobench | Correlation to run IDs, stable disk scope and intervals, exportable contextual observations | Benchmark/calibration/ceiling stores remain iobench-owned evidence; ambient IO is not a calibration proof. |
| MDT capacity / admission | Explicit host facts, ancestor constraints, protection estimates and observation age | MDT owns policy/context publication; never turn missing telemetry into zero usage or invented available capacity. |
| Humans / bash launchers | Tiny Python ctl commands with JSON output, stable exit statuses and env-only endpoints | Proposed verbs: status, capabilities, limits --self, current --fields, record start/stop, bookmark/export; no manual socket protocol required. |

[C] C:assay/src/assay/resource_limits.py:1068 invokes the observer when ancestors are hidden; C:scripts/cgroup-profiler/src/cgprofile/resource_observer.py:424 rejects unsafe writable migration circumstances. A plain minimum-of-visible-memory.max API is not an adequate replacement.
[C] C:assay/src/assay/observer_windows.py:776 distinguishes unsupported, repeated refusal and lost-window outcomes; :800 chooses process-tree when jobs are concurrent. Retain that semantic separation in the new client.
[C] C:assay/tools/tester-unified-gate.sh:483 verifies socket-group access. Endpoint publication must provide host numeric GIDs and directory mounts through the owning installer, not guesses inside a container.
[C] C:run-gate-project/run_gate.py:5970 implements a wait-then-proceed profiling pressure policy. Do not reinterpret that as a transactional capacity reservation supplied by the observer.
[C] C:libraries/iobench/result.py:119 and :141 separates stable disk identity from observed PARTUUID/device numbers; C:libraries/iobench/ceilings.py:14 distinguishes benchmark/governor/probe evidence. Reuse those links, not a second disk-identity registry.
[C] C:mdt-cockpit/host-setup/mdt_capacity.py:824 substitutes zero for missing probe values; hostobs should report missing and require the caller's explicit policy.
The optional library decision needs four visible states: package absent, endpoint unset/unreachable, capability refused, and measurement lost/incomplete after start. Do not catch all of them as “profiling unavailable” without cause.
require_profiling=true should refuse before work when prerequisites are missing, and mark required evidence invalid if it is lost later. Preserve the workload's separate exit/test result; resource starvation must not masquerade as a test assertion failure.
Resolve endpoints solely from the documented environment. The installer/hostcfg owns /etc configuration, groups, systemd units and endpoint publication; Rust owns config validation. A Python wizard should invoke that validation rather than duplicate its rules.
Standalone install/uninstall should use the hostcfg ownership manifest and exact owned units/artifacts. Uninstall must distinguish removing the service from purging retained evidence, and report unresolved owned kernel state rather than silently deleting its record.
R:host-endpoints-and-phase2-fastpath-DESIGN.md:17 and :185 and R:hostcfg-AUGEAS-AND-BOUNDARIES-DESIGN.md:231 support this boundary. Do not add guessed socket constants, a new config database or runtime config mutation verbs.
Use one small common record model: subject identity; run/session/window IDs; time bounds; typed observations; sources and quality; annotations; artifact references. Keep tool-specific test/mutant/build semantics outside the daemon.
Preserve phase/mark use cases from C:scripts/cgroup-profiler/src/cgprofile/phases.py:1. A generic timestamped annotation with issuer and bounded attributes is sufficient; daemon HTML generation is unnecessary.
This enables flaky-test forensics, OOM context bundles, peak-memory comparisons across releases, and per-lane historical budgeting without teaching hostobs what a test or release is.
Resource budgets inferred from history must include sample coverage, workload/config identity and host normalization. A p95 from ten successful short runs is not a safe memory limit for all future runs.
For noisy-neighbour analysis, correlate peer cgroups on the controller with shared device/PSI changes; rank suspects without claiming causality. Deployment to that host is a separate operator decision.
For OOM post-mortems, retain last observed topology, constraints, events and process highlights. Counter changes alone cannot reliably name the victim or the exact kill instant; expose that limitation.

## F. Build versus reuse

The strongest nearby reference is **below**, already cited by the console plan. Study its Linux collectors, identity handling, time-travel and snapshot/export UX before implementing equivalent machinery; that is useful reuse research, not a reason to abandon this service.
Licenses below are upstream project-level signals, not a completed per-file/transitive dependency audit. Any copied collector or BPF program needs its own SPDX/dependency review.

| Project | Concrete reuse / interoperability | Boundary and footprint decision |
|---|---|---|
| below | Rust cgroup/process collection and record/replay/snapshot ideas; evaluate small modules for extraction | Apache-2.0; closest architecture reference, but its whole app does not supply these peer-bound windows and actuator grants. [Repository](https://github.com/facebookincubator/below). |
| cAdvisor | Container accounting edge cases, labels and comparison data | Apache-2.0, Go; do not embed a second container observer or runtime-dependent daemon. [Repository](https://github.com/google/cadvisor). |
| node_exporter | Metric naming and optional OpenMetrics mapping; cross-check interpretations | Apache-2.0, Go; scrape telemetry is not a session/window evidence authority. [Repository](https://github.com/prometheus/node_exporter). |
| Netdata | Pressure/history interaction ideas and supported collector behavior | GPL-3.0 core; broad product/dependency surface. Do not import its database/dashboard stack for this scope. [Repository](https://github.com/netdata/netdata). |
| Parca / Polar Signals agent | Optional future stack profiles correlated with hostobs intervals | Apache-2.0 project; specialized profiling machinery is worth reusing, not rebuilding inside v1. [Repository](https://github.com/parca-dev/parca-agent). |
| OTel eBPF profiler | Future profiler integration, existing runtime unwinders | Apache-2.0 userspace and GPL-2.0 BPF code; treat as a separate optional profiler with its own costs/capabilities. [Repository](https://github.com/open-telemetry/opentelemetry-ebpf-profiler). |
| libbpf-rs / libbpf | Later reuse of established CO-RE C programs and loaders | libbpf-rs has LGPL-2.1-only OR BSD-2-Clause licensing; linking/build dependencies need accounting. Prefer it when program reuse dominates. [Wrapper](https://raw.githubusercontent.com/libbpf/libbpf-rs/master/README.md), [license](https://github.com/libbpf/libbpf-rs/blob/master/LICENSE). |
| Aya | Alternative Rust eBPF loader/program stack if it removes measured integration cost | MIT/Apache-2.0; no libbpf dependency. Choose by program maturity and packaging, not Rust purity. No BPF in v1. [Repository](https://raw.githubusercontent.com/aya-rs/aya/main/README.md). |
| bpftrace / BCC | Diagnostic prototypes in a VM and validation of future BPF measurements | bpftrace is Apache-2.0; BCC's LLVM/tooling and per-program licensing need separate review. Do not spawn these per tick. [bpftrace](https://raw.githubusercontent.com/bpftrace/bpftrace/master/README.md), [BCC](https://raw.githubusercontent.com/iovisor/bcc/master/README.md). |
| systemd-cgtop / perf / PSI | Accounting/UI reference; optional external perf diagnostics; kernel PSI directly | Reuse kernel interfaces, not repeated subprocess parsing. cgtop is a useful simple comparison surface, not a retained evidence service. [systemd manual source](https://raw.githubusercontent.com/systemd/systemd/main/man/systemd-cgtop.xml). |
| atop / sar / PCP | Incident replay conventions, process exit accounting lessons, optional external data export | atop/sysstat use GPL-family licenses; PCP libraries LGPL and other components GPL. Mature archives, but adopting their full services adds a second model/control plane. [atop](https://raw.githubusercontent.com/atoptool/atop/master/README), [sysstat](https://raw.githubusercontent.com/sysstat/sysstat/master/COPYING), [PCP](https://raw.githubusercontent.com/performancecopilot/pcp/main/COPYING). |
| Pixie | Reference for later rich BPF correlation | Apache-2.0, Kubernetes/application observability scope; excessive infrastructure for this host-local service. [Repository](https://github.com/pixie-io/pixie). |
| Vector / OTel Collector / Grafana Alloy | Optional external export pipelines after hostobs produces stable data | MPL-2.0 / Apache-2.0 / Apache-2.0 respectively; avoid mandatory agents, config languages and buffers inside hostobs. [Vector](https://github.com/vectordotdev/vector), [Collector](https://github.com/open-telemetry/opentelemetry-collector), [Alloy](https://github.com/grafana/alloy). |

**Build ours:** evidence contracts, peer/namespace attribution, grants and cleanup, temporal identity model, bounded query/stream API, optional Python client and installer integration.
**Reuse selectively:** Linux parsing patterns, well-maintained Rust dependencies, compression/checksum libraries, systemd interfaces, existing native test/fuzz tooling and later mature BPF programs.
**Interoperate rather than embed:** OpenMetrics, Perfetto, pprof providers and offline Parquet. No reuse candidate eliminates the unusual combination of strict observer evidence and constrained host actions.
Do not fork a large collector solely to save a few parsers. First inspect its ownership model, missing-value semantics, allocation profile and license boundaries; porting a parser can be cheaper than inheriting its runtime.

## G. Simplification and sequencing

**First useful milestone:** one installable build with both processes, host/cgroup sampling, binary live view, bounded recent history, static focus recording, bookmark/export, minimal Python ctl/client and a Rust TUI consumer. This is an explicitly incomplete 0.x milestone, not “v1 without DAMON.”
The actuator exists immediately and exposes only implemented narrow operations. A peak-window vertical slice exercises the hard privilege boundary early; do not leave the split as two binaries with no validated authority contract.
Finish enrichment and stat-only DAMON before calling the release v1. They may follow the first useful view, but the spike must happen early because privilege, ownership and overhead could alter the design.
Then deliver observer limits/windows and one real run-gate/assay integration; follow with complete session/placement/control semantics. Keep releasing cgprofile until these consumer gates are satisfied, as directed.
Static focus selectors are enough initially if the operator accepts them. A bounded “record this selected cgroup” session can be a control request without introducing runtime configuration editing.
Defer all-PID continuous history, arbitrary BPF, remote multi-host transport, a plugin framework, automatic remediation, SQL queries, daemon HTML, durable session recovery and native Parquet storage.
Do not defer explicit missing states, bounded queues/spools, incarnation IDs, raw counters, temporal labels or readable error remedies. Those become expensive incompatible repairs after consumers depend on them.
Use cargo test, clippy, coverage, cargo-mutants and cross-language fixtures in registered tester/VM lanes; the assay Rust adapter remains a 1.0 prerequisite. Do not run these tests in the cockpit.
The predecessor's fixed p99 lateness threshold must not become a pass/fail test under arbitrary host load. Inject delayed reads and virtual time to assert behavior; report real latency distributions separately.
Keep user docs synchronized: README capability, DESIGN-GUIDE rationale, CONSUMERS pasteable endpoint/client examples. New schemas and required-profiling behavior belong in that same change.

### Risk-ordered spikes

Effort below is rough focused engineer time, excluding review/queue time; none of these experiments was executed for this opinion.

| Order / question | Method in a registered lane or disposable VM | Success criterion | Effort |
|---|---|---|---|
| 1. Can the split measure safely? | Exercise peer binding, cgroup recreation, independent peak opens, dup/SCM_RIGHTS sharing, permission failures and actuator restart | Probe receives no writable peak FD; wrong identities are refused; results name timing/cleanup uncertainty | 2–3 days |
| 2. Is identity/clock evidence correct? | Synthetic file model and fake clocks covering resets, late discovery, churn, wall jumps and missing intervals | No lifetime joins, invented zeros or invalid rates; Rust/Python fixtures agree with hand-calculated expectations | 2 days |
| 3. Is the system bounded under pressure? | Model adversarial clients/churn plus constrained VM runs, blocked readers and disk-full injection | Every retained structure has a measured/configured bound; loss/refusal is explicit; no accumulating workers | 2–4 days |
| 4. Does the wire help end to end? | Encode/decode and full-view replay over realistic captured/synthetic distributions; compare fixed columns, current codec and FlatBuffers | Published bytes/allocations/CPU, correct drop/reconnect behavior, acceptable agreed service budget; no guessed 5 KB promise | 1–2 days |
| 5. Can DAMON fit the boundary? | Stat-only target setup/update/cleanup, denied access, target exit, owned-slot quarantine and region-limit stress | No unrelated slot writes; cleanup is confirmed or explicitly failed; whole-system overhead characterized | 2–3 days |
| 6. Can process detail scale? | 5k/50k-PID fixtures and VM workloads with targeted queries and scan decimation | Detail remains bounded/paged; coverage is honest; core collection is independent of stalled metadata | 2 days |
| 7. Does history serve the actual UI? | Replay a synthetic incident with cgroup replacement, label/limit changes and gaps; use snapshot_at plus batch series | Single-view scrub and comparison need no per-cell RPC fan-out; exported bundle reproduces the interpretation | 2 days |
| 8. Are consumers truly complete? | One observer-required assay lane, run-gate worker profiles, session stop before removal, missing-library and mid-run loss cases | Correct prerequisite/evidence/workload statuses; source/quality preserved; no dependence on hidden old validators | 2–3 days |

## H. Ranked suggestions, operator decisions and first questions

### Top ten suggestions, ranked by value / effort

1. **Make identity, raw counters and quality the schema foundation.** Very high value / medium effort; prevents plausible but false evidence throughout every consumer.
2. **Keep peak descriptors and DAMON writes inside the actuator.** Very high value / medium effort; makes the binding split meaningful.
3. **Bound bytes and work everywhere, including session spools and dictionaries.** Very high value / medium effort; protects the observer precisely when the host is unhealthy.
4. **Add a cross-language, independently calculated fixture corpus before optimizing.** High value / low-to-medium effort; catches both wire defects and semantic drift.
5. **Provide batch history and as-of snapshots from the start.** High value / medium effort; makes the promised single-view replay practical.
6. **Provide targeted process detail with coverage, not global all-PID streaming.** High value / medium effort; resolves the existing expand-all mismatch without enormous ongoing cost.
7. **Add generic annotations and a sanitized lane/session index.** High value / low effort; gives Topos, assay, run-gate and iobench a shared incident timeline.
8. **Make bookmark → bounded incident bundle → offline replay one small workflow.** High value / medium effort; turns a live console into useful forensics.
9. **Expose an evidence drawer and explicit required-profiling outcomes.** High value / low effort once the model is correct; makes missing/stale data actionable rather than misleading.
10. **Study below and reuse exporters/profilers at the boundary.** Medium-to-high value / low initial effort; avoids rebuilding mature UI and profiling work without importing a second platform.

### Decisions still worth making — eight maximum

1. **Required evidence lost after launch:** recommend a separate infrastructure/evidence-invalid outcome, retaining the actual workload verdict and reason.
2. **Peak measurement ownership:** recommend actuator-owned descriptors and narrow window operations, rather than an exception that gives writable descriptors to the probe.
3. **First history scope:** recommend host/cgroup history plus selected process history during focus sessions; no promise of retrospective arbitrary PID detail.
4. **Retention budget and eviction priority:** recommend explicit byte/time limits for hot history, active recordings and sealed evidence, with admission refusal before required evidence is displaced.
5. **Wire/history implementation:** recommend typed fixed-width columns and native bounded segments; FlatBuffers is the fallback if the small-format spike shows custom maintenance costs exceed its dependency cost.
6. **Exact summary statistics:** recommend preserving required exact nearest-rank semantics under explicit session limits/spooling; choose a separately named approximate statistic only by explicit contract change.
7. **Clock/identity schema correction:** recommend fresh monotonic duration and incarnation-aware evidence schemas even where old fixtures encode wall-time or lifetime ambiguities; no legacy compatibility layer.
8. **Authority and disclosure:** recommend direct authenticated control grants, a sanitized read-only overlay API, allowlisted enrichment, and command-line inclusion only by explicit operator/export policy.

### Five questions I would ask the operator first

1. What is the largest required live target: cgroups, PIDs, concurrent windows, session duration and client count, including the controller's expected churn?
2. Which evidence must remain valid for a run to count, and what should the operator see when that evidence is lost after the workload has already succeeded?
3. How many bytes and how much time should be reserved for incident replay versus sealed release evidence, and which may be evicted first?
4. Does “expand all processes” mean a current paged inspection, selected-process recording, or full retrospective history of every process?
5. What authority should a compromised run-gate/assay client retain, and which actors may view/export command lines, labels and session annotations?

