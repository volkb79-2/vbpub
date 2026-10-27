# IO governor: second opinion (Codex GPT-6-Astra, high effort), 2026-10-27

Companion to [IO-GOVERNOR-DESIGN.md](IO-GOVERNOR-DESIGN.md) and [IO-GOVERNOR-CANDIDATES.md](IO-GOVERNOR-CANDIDATES.md).
An outside model was given the problem and facts only (round 1, blind: no candidate designs, no file access), then our own
design for critique (round 2). The raw answers are below, unedited; the problem statement and our design summary that were
sent are in the two prompt sections. Treat the answers as opinion to test, not as measurements.

**Session (reusable):** `01a17c21-bbad-7993-8638-0da66084ee8f`. Resume with
`CODEX_SQLITE_HOME="$HOME/.codex/sqlite-shared" CODEX_HOME="$HOME/.codex2" codex exec resume 01a17c21-bbad-7993-8638-0da66084ee8f -m gpt-6-astra -c model_reasoning_effort='"high"' --skip-git-repo-check -`
(prompt on stdin). The model slug is `gpt-6-astra`; the display name `GPT-6-Astra` is rejected.

## Round 1 prompt (problem and facts only)
You are being asked for a second opinion on an engineering problem. Do NOT read files or run commands; answer from reasoning only. Do not ask me questions; work with what is here and state assumptions explicitly.

## Context
A Linux host (Debian, kernel 7.x, cgroup v2) is a VM on a commercial hypervisor/provider with a virtual disk (virtio, /dev/vda, ext4/LVM, ~1 TB). The same VM runs (a) a production latency-sensitive game server (the "protected" workload; it also uses swap, and swap IO is charged to its cgroup) and (b) a "governed" dev tier: container builds, test gates, dev containers, CPU/IO hungry, bursty. We control the guest only; we cannot see or control the provider's storage layer.

We use cgroup v2 `io.cost` (iocost) for weight-based IO control: a cost model (rbps/rseqiops/.../wbps etc.) plus QoS (`rpct/rlat/wpct/wlat/min/max`, i.e. vrate clamp), and `io.max` caps on the governed tier's slice. The model is calibrated once per disk identity by a benchmark; the baseline is treated as valid until the disk's identity changes.

## The problem
The disk's identity does not change, but its real sustained write ceiling moves by more than 20x in both directions over days, driven by provider-side allocation state that the guest cannot observe:
- 2026-07-08: sustained write 516 MB/s (58.7k IOPS), read 2149 MB/s; burst figures of 4.3 GB/s were hypervisor cache and misleading.
- 2026-10-20: 14-18 MB/s sustained write (builds saw a ~6 MB/s floor). The provider UI then showed 1.1 TB used of 1 TB with a snapshot active, which fits a fixed allocation plus an active snapshot forcing inefficient writes.
- 2026-10-23: after the operator freed space, ran fstrim and ran the provider's storage optimisation (guest at ~50% fill): 330-350 MB/s sustained (fio direct+fsync, 120 s), 4k random write 46.5k IOPS at 0.34 ms.
The guest can see: its own filesystem/VG fill, discard (fstrim) history, and the throughput/latency that results. It cannot see a snapshot or the provider's allocation. Annotations by a human are possible.

Effects of a stale figure, both seen within one week:
- Too tight: a static cap pinned at the old 10 MB/s floor made a container image build take 4694 s versus ~1100-1200 s uncapped (IO pressure 30-48% vs 1-9%).
- With an io.cost model 23x too low, the kernel's `vrate` climbed to 2000-2800 to compensate; it only works while the clamp `max` allows it.
- Too loose: a cap above the real device rate protects nothing.

Measured behaviour of the workloads: demand is bursty and far below even the low ceilings most of the time (average ~35 MB/s write, peak ~143 MB/s, latency 1-3 ms, device busy up to 65-89%, io pressure `some` avg ~1%, max ~9-24%).
Measured kernel behaviour: with a correct model, `vrate` sat at the QoS floor (50) in almost every period. A tracepoint (`iocost:iocost_ioc_vrate_adj`, 636 events over 815 s) showed the kernel lowering vrate because request-slot waits exceeded 5% (our own deliberate setting `nr_requests=16`, `max_sectors_kb=64`, mq-deadline, which cut the game server's worst D-state stall from 26.9 s to 0.14 s); latency targets (rlat 5 ms, wlat 25 ms) were almost never missed and cgroups were rarely short of budget. So "vrate at a clamp" is not by itself a staleness signal on this host.

Constraints we currently believe in (challenge them if you think they are wrong): protecting the game server's latency beats governed-tier throughput; running our own benchmark/probe IO on the production disk is disruptive and should be rare or never; the host has 8 cores and ~15 GB RAM and the tooling should be small and low-risk (a systemd service reading procfs/sysfs/cgroup files is fine); settings can be changed at runtime (io.max, io.cost.model, io.cost.qos, io.weight, nr_requests, per-cgroup limits).

## What I want from you (round 1: blind ideas)
1. First restate in your own words what you think the real problem is (2-4 sentences), and name the one or two facts above you consider most decisive or most suspicious.
2. Then give at least 12 distinct IDEAS for how a system could cope with a backing store whose capability changes unobservably: from conventional to unusual, including ones that avoid estimating the ceiling at all, ones that change what is being controlled, ones that use information outside the guest, and ones that reduce the need for the control. For each: a one-line mechanism, the signals it needs, and the main way it would fail on this host.
3. Finally list what you would want to measure or learn FIRST before building anything.
Be concrete and opinionated. Do not converge on one answer yet.

## Round 1 answer (blind ideas)

The real problem is protecting a latency-sensitive workload on storage whose service capacity can change without any reliable advance signal. Disk identity is therefore a poor boundary for calibration validity, and a once-measured ceiling cannot support a durable cap. You also have an identification problem: low throughput can mean low demand, your own throttling, constrained request concurrency, or a slow backing store.

The two most decisive facts are the **greater-than-20× capacity change without an identity change**, and the trace showing **vrate reductions driven by request-slot waits rather than missed latency targets**. The latter makes vrate a particularly ambiguous diagnostic here. The snapshot/allocation explanation is plausible, but the recovery followed several interventions, so it does not establish which one caused the improvement.

I assume the measurements describe different operating periods, the governed tier can be delayed or stopped, and the primary objective is a measurable game-server latency target. I treat the kernel behavior you reported as evidence; the mechanisms below are design ideas, not claims about unverified kernel details.

Here are 18 distinct approaches worth considering.

1. **Control directly against game-server latency.**  
   **Mechanism:** Reduce governed IO allowance when tick overruns, response latency, or protected IO stalls worsen; relax it cautiously during healthy periods. This avoids estimating storage capacity.  
   **Signals:** Game latency distribution, protected cgroup pressure, governed IO activity.  
   **Main failure:** CPU, networking, or game logic can trigger false throttling. Storage latency may also deteriorate faster than the controller can react.

2. **Use a fast emergency brake alongside a slow controller.**  
   **Mechanism:** On severe protected stalls, immediately stop admitting builds and sharply restrict governed IO; require a sustained healthy interval before recovery.  
   **Signals:** Protected stall duration, tick overruns, recent governed submissions, recovery history.  
   **Main failure:** Already-issued requests and accumulated dirty data keep draining after the brake. It can limit damage without preventing the first stall.

3. **Search for safe operating headroom using ordinary work.**  
   **Mechanism:** Apply additive increases and multiplicative decreases to governed caps while real work is pending; judge success by protected latency and incremental useful throughput.  
   **Signals:** Governed backlog, achieved throughput, cap-binding status, protected latency.  
   **Main failure:** Every increase is an experiment on production, even though it generates no synthetic IO. Bursty demand makes comparisons noisy.

4. **Control outstanding work rather than bytes per second.**  
   **Mechanism:** Restrict concurrent IO-producing jobs, application queue depth, or outstanding write batches so the governed tier cannot build a long queue.  
   **Signals:** Job concurrency, outstanding requests where observable, queueing and completion latency.  
   **Main failure:** A single build or filesystem operation can generate substantial fan-out, and guest queue depth does not reveal provider-side queues. There may be no convenient universal per-cgroup knob.

5. **Budget storage time rather than throughput.**  
   **Mechanism:** Give governed work a budget expressed in estimated IO service time or measured occupancy, allowing the byte rate to vary with observed cost.  
   **Signals:** Completion latency, request size/type, outstanding IO, device activity.  
   **Main failure:** Concurrent request durations overlap, and device busy time is not a reliable measure of backend utilization. Accounting needs care to avoid treating all latency as consumed capacity.

6. **Use weighted sharing with only a broad absolute guardrail.**  
   **Mechanism:** Let iocost weights allocate contention preferentially to the protected workload, using `io.max` only to contain extreme governed bursts.  
   **Signals:** Protected latency, cgroup demand and consumption, iocost budget/adjustment diagnostics.  
   **Main failure:** Weighted sharing does not guarantee tail latency, and model errors or clamp limits can impair adaptation. Your small request queue can become the binding constraint before budget competition appears.

7. **Make caps expire instead of treating them as permanent facts.**  
   **Mechanism:** Store each cap with its provenance, confidence, and validation conditions; invalidate or reconsider it after contradictory observations or a maximum age.  
   **Signals:** Cap-binding periods, protected health, observed performance changes, configuration and operator events.  
   **Main failure:** Expiration tells you the old value is untrusted; it does not identify a safe replacement. A conservative fallback can recreate the 10 MB/s trap.

8. **Estimate capacity only from qualifying production intervals.**  
   **Mechanism:** Learn from windows with sustained queued demand, known controller limitations, comparable IO mix, and enough duration to expose cache effects; otherwise report “unknown.”  
   **Signals:** Backlog, throttle state, request mix, throughput, latency, sustained-window duration.  
   **Main failure:** This host may rarely produce qualifying windows. Observed throughput is usually evidence of delivered service, not proof of a ceiling.

9. **Keep several performance regimes instead of one calibration.**  
   **Mechanism:** Maintain candidate models for slow, intermediate, and fast behavior, with change detection and hysteresis between them.  
   **Signals:** Comparable workload windows, throughput/latency relationships, controller settings, optional operator annotations.  
   **Main failure:** Workload changes can resemble storage changes. Classification may arrive only after the protected workload has suffered, and real capacity may vary continuously.

10. **Treat read, write, and durability costs separately.**  
    **Mechanism:** Adapt write restrictions independently and distinguish bulk writes from small synchronous or metadata-heavy activity wherever practical.  
    **Signals:** Read/write bytes and operations, request sizes, sync/flush behavior, workload phase.  
    **Main failure:** These activities still contend for shared resources, and attribution may be incomplete. A byte cap that looks safe for bulk writes can be unsuitable for synchronous writes.

11. **Shape bursts explicitly.**  
    **Mechanism:** Combine a modest sustained allowance with a small, bounded burst allowance, implemented through available IO controls or job admission.  
    **Signals:** Short-window governed IO, protected latency, backlog and burst history.  
    **Main failure:** A burst acceptable at 350 MB/s can be disastrous at 15 MB/s. An adaptive average rate does not make a large burst budget safe.

12. **Schedule governed work around protected activity.**  
    **Mechanism:** Admit heavy builds during low-player or low-load periods, with bounded run intervals and drain periods before anticipated game activity.  
    **Signals:** Player count, tick load, work queue, dirty/writeback state, observed drain time.  
    **Main failure:** Storage effects can outlast the scheduled interval, and player arrivals are unpredictable. Pausing a process does not instantly clear its IO consequences.

13. **Control admission by workload class.**  
    **Mechanism:** Serialize image exports, large link steps, database tests, or other destructive phases while allowing lighter development work to proceed.  
    **Signals:** Job identity, build phases, historical resource profiles, current protected health.  
    **Main failure:** Profiles drift and opaque tools can change behavior. Still, this can be easier to reason about than deriving one correct disk ceiling.

14. **Bring provider and operator information into the policy.**  
    **Mechanism:** Use available provider APIs, monitoring exports, or human annotations to flag snapshots, allocation pressure, optimization, migrations, and performance incidents.  
    **Signals:** External events and metrics, plus timestamped annotations.  
    **Main failure:** Information may be unavailable, delayed, or weakly related to performance. Use it to change confidence or operating mode, not as a guaranteed capacity measurement.

15. **Allow rare calibration during an explicit maintenance window.**  
    **Mechanism:** Run a bounded representative benchmark when the protected workload is absent or deliberately quiesced, particularly after suspected backend changes.  
    **Signals:** Maintenance state, representative IO profiles, time-series performance and latency.  
    **Main failure:** The result can become stale immediately afterward, and the benchmark may change backend cache or allocation state. It is evidence about that window, not a new permanent truth.

16. **Move governed IO elsewhere.**  
    **Mechanism:** Send builds and gates to remote workers, or use storage with verified independent performance resources.  
    **Signals:** Build turnaround, transfer volume, remote availability, evidence of storage isolation.  
    **Main failure:** A second virtual disk may share the same bottleneck; remote execution adds cost and operational dependencies. Moving whole build jobs may be more effective than moving only their scratch directory.

17. **Reduce IO generated per unit of useful work.**  
    **Mechanism:** Reuse build artifacts, avoid redundant image unpacking/export, coalesce duplicate gates, cancel superseded builds, and bound cache churn.  
    **Signals:** Bytes and operations per successful build, cache hit rates, repeated work, invalidation causes.  
    **Main failure:** Caches consume space and can create eviction storms. Cold builds still need a policy, but reducing demand helps in every storage regime.

18. **Protect memory headroom to reduce urgent disk dependence.**  
    **Mechanism:** Limit governed memory growth and concurrency, preserve protected working-set headroom, and use memory-backed scratch only within a measured safe budget.  
    **Signals:** Protected swap-in and major-fault rates, memory pressure, working-set size, dirty pages, governed memory usage.  
    **Main failure:** With roughly 15 GB RAM, aggressive caching or tmpfs can worsen reclaim and swap. Memory protection cannot eliminate necessary persistence IO, and poorly chosen limits can cause failures.

I would challenge two assumptions before implementation. **Rare production benchmarking is a sensible preference, but passive adaptation still needs excitation:** either ordinary demand exposes changed capability, or you deliberately permit more work and observe the result. You cannot safely infer unused capacity from an idle disk. Also, **no guest controller can guarantee protected latency through an arbitrary backend slowdown** if the protected workload itself requires urgent storage service; that limitation matters when deciding how much isolation or memory headroom is necessary.

Before building anything, I would measure or establish these, in this order:

1. **The actual protection target and its causal path.** Define acceptable tick/response latency and stall duration. Align game overruns with protected swap-ins, IO pressure, CPU contention, and governed IO. Device latency alone is an insufficient objective.

2. **The actuator’s stopping distance.** During naturally occurring incidents or an approved quiet period, measure how long protected latency takes to recover after sharply reducing governed work. Record outstanding IO, dirty data, and writeback. This determines whether a reactive controller can act quickly enough.

3. **Whether governance currently reaches all relevant IO.** Confirm the effective block-device layer through LVM, cgroup hierarchy, swap charging, and attribution of buffered writes, container storage operations, and shared filesystem activity. Distinguish configured limits from limits demonstrably affecting traffic.

4. **What is limiting each observed interval.** Put workload backlog, achieved IO, cap/throttle state, iocost adjustment reasons, request-slot waits, and protected latency on one timeline. In particular, separate “disk became slow” from “we constrained submission” and “nothing wanted to run.”

5. **The protected workload’s own storage requirements.** Measure its burst sizes, read/write mix, synchronous operations, swap dependence, and performance during governed-idle periods. This establishes how much of the problem remains even after perfect suppression of development work.

6. **Comparability of the existing performance evidence.** Reconcile request sizes, queue depths, direct versus buffered IO, sync semantics, working-set sizes, test duration, and competing workloads across dates. The scale of change is compelling; its precise magnitude for the production workload remains uncertain.

7. **The value and cost of the current queue settings.** Preserve the evidence behind the 26.9 s to 0.14 s improvement. Establish which settings changed together and whether request-slot waits predict actual harm. I would not relax them merely to make vrate look healthier.

8. **Which governed work produces the worst interference per useful result.** Identify phases responsible for large dirty backlogs, synchronous write storms, reclaim, or repeated artifact churn. This could justify a much smaller admission or workload-change solution than a continuously recalibrated storage model.

The first implementation decision should depend especially on stopping distance and causal attribution: they determine whether feedback can protect the game in time, or whether prevention through admission, memory headroom, and isolation must carry most of the burden.

## Round 2 prompt (our design, for critique)

Thanks. Round 2: our own ideas, for you to critique and discuss. Still no files or commands; reason from what is here. Be direct about what you think is wrong or missing, and rank things.

## What we have already written down (a design, not yet built; "nothing implemented")
A small systemd service (Python, reads procfs/sysfs/cgroup only, writes through the one existing apply path; modes off | observe | enforce-down | enforce, default observe; fail-open to last good values; every change journalled with before/after/rule/evidence). Objective: protect a declared "protect list" of cgroups (the game server), governed side (builds/gates/dev containers) gives way first. Per-target signals: the protected cgroup's `io.pressure` (some/full deltas), its `io.stat` rates, count of its threads in D state sampled each tick, device `/sys/block/<dev>/stat`, swap latency; optional bpftrace per-cgroup block latency, off by default and time-boxed. Covariates: filesystem fill (a table fill-bucket -> best proven bound from history, seeded with the two known points), discard history (read only), change-point events (`ceiling_shift`), human annotations via `note`.
Decision every 5 min and on events, asymmetric trust: lowering is cheap so one pair of windows (plateau below the model by >25% in two consecutive windows) suffices; raising needs a proven saturated window (device busy >= 85% or vrate at max, latency under target) and never exceeds the configured ceiling. Steps at most 25%, 30 min cooldown per direction, model never below 0.7x the worst recorded plateau or above the best proven plateau; dev caps stay a fixed percentage of the model. Harm to a protected target (io.pressure stall above threshold >1 min or persistent blocked threads) freezes all increases and steps governed caps down regardless of any capacity estimate. No probe IO, ever; increases wait for the workload to prove capacity; the operator can run the benchmark by hand.
Candidate estimators (we want to pick by measurement in a VM whose disk we control: a throttleable virtual disk where we inject ceiling changes):
- A. `vrate` edge: lower the model when vrate sits at the floor for N periods, raise at the ceiling. Simplest; but vrate sits at the floor on this host with healthy latency (request-slot waits, our own nr_requests), so bare A spirals to the rail. A+ = act only if the floor coincides with missed latency targets / rising queue depth, raise only when demand was held back.
- B. Quiet-window service time: keep only windows with ~1 request in flight, track per-request service time by direction and size band, compare last 30 min against last 7 days, act on a sustained 3x change. Needs no saturation, unaffected by our own throttling; but a provider bandwidth/IOPS cap may not change single-request time (it may miss exactly our failure); needs days of history.
- C. Saturated-window ceiling: keep only windows where the disk was the limit (latency rising with queue depth, delivered rate not near our own cap, demand present); delivered rate there is a lower bound on capability; track a high percentile with decay and fit the model to it. Needs load to learn; stale on a quiet host (must say so and hold).
- D. Hybrid: B as early warning, C as the number; model changes only when C confirms (or B overwhelming and persistent).
- Controls: E static baseline (today), F "price the worst plateau" with a wide QoS max so vrate climbs by itself, G oracle (test only).
Phases: P0 observe-only for two weeks and replay the 2026-10-20 -> 10-23 shift; P1 surface "recalibrate recommended" in the doctor/wizard; P2 enforce-down; P3 enforce with increases only on saturated windows.
Current stop-gap: dev tier capped at ~70% of the measured sustained ceiling (700M read/250M write B/s, 50k/30k IOPS) and a hand-set io.cost model and QoS; re-measure after any storage change.

## Where your own answers touch this (facts we have, for your use)
- Stopping distance: NOT measured. We have never timed recovery of protected latency after sharply cutting governed IO.
- Causal attribution: partly. The 2026-10-20 incident was reconstructed with bpftrace (D-state stacks of the game process; writeback delay of write()); the game thread's worst D-state stall was 26.9 s before we limited nr_requests/max_sectors_kb/scheduler and 0.14 s after (settings changed together, not separately).
- The protected workload's own game-tick latency is not an input today; we only see its cgroup pressure and D-state threads.
- Swap IO is charged to the page owner's cgroup; buffered-write attribution to cgroups depends on writeback cgroup support on this filesystem/stack (unverified for LVM+ext4 here).

## What I want from you now
1. Pick your TOP 3 of your 18 ideas for this host and explain why, then say which of A-D (and A+) you would build, in which order, and what you would drop. Do you agree with 'asymmetric trust' and the 25%/30-min/0.7x rules, or are they cargo cult? 
2. Attack the design: the 5 weakest assumptions, with the failure scenario for each. Is 'estimate the ceiling and fit io.cost to it' the right framing at all, given your round-1 point about identification (low throughput = low demand vs our throttling vs request-slot limits vs slow backend)?
3. What would you add or change in the VM test plan so that it can discriminate between the estimators, including how to inject provider-like behaviour (snapshot-style write amplification, flat bandwidth caps vs IOPS caps vs latency inflation, cache bursts that look like capacity)?
4. If you could only keep ONE safety mechanism, which, and what is the smallest first deliverable (one week of work) that gives real protection?
Be concrete; disagree where you disagree.

## Round 2 answer

I would build a **protected-workload safety controller first and a capacity estimator second**. Your service architecture is sensible; the proposed inference and control rules are where I disagree. In particular, automatically lowering the model and keeping caps proportional to it can create a self-reinforcing throughput collapse.

I assume governed work can tolerate temporary severe throttling, the existing apply path can update caps reliably, and the game server has some measurable definition of acceptable latency—even though you do not yet collect it.

**1. My ranking and estimator choices**

My top three from round 1, ranked by implementation priority:

1. **#2: A fast emergency brake.** You have evidence of serious protected stalls and no measurement of how quickly throttling relieves them. A capacity estimate is unnecessary for the basic action: when protection fails, reduce governed interference. Measure stopping distance before choosing its timing.
2. **#1: Control against actual game latency.** Cgroup pressure and D-state samples are useful supporting evidence, but neither establishes whether players are experiencing unacceptable latency. This is the objective the controller should eventually use.
3. **#13: Admission by workload class.** Preventing several IO-heavy build phases from overlapping can limit queued work before a reactive cap takes effect. It also remains useful when the backing store changes and when capacity is unknown.

For A–D, I would build them in this order:

| Candidate | Decision | Reason |
|---|---|---|
| **C** | First, observe-only | Most defensible source of numerical performance evidence, provided it can abstain and recognizes controller-limited intervals. |
| **B** | Second, diagnostic only | Useful for detecting some latency changes and distinguishing regimes; insufficient for estimating bandwidth or IOPS ceilings. |
| **D** | Later, with a narrower role | Let B invalidate confidence or request review; let qualified C observations inform numerical changes. Remove “B overwhelming and persistent” as permission to resize the model. |
| **A+** | Retain as diagnostic evidence | Floor plus latency/queue trouble indicates distress, not its cause or the correct model adjustment. |
| **A** | Drop as an estimator | Your own trace already provides a counterexample to its central inference. |

C’s output should initially be something like: “This workload mix delivered this rate for this duration under these settings,” with confidence and exclusions. Calling that a device ceiling grants it more generality than the evidence supports.

Keep **E** as the baseline, **G** as a reference with privileged knowledge, and **F** as a deliberately challenged comparator. F tests whether the existing kernel controller can absorb much of the variability. But a wide vrate range does not solve fixed caps, and a single vrate multiplier cannot correct changes that affect writes, reads, bandwidth, and IOPS differently.

On the numerical rules:

- **Asymmetric trust:** Agree for protective actions; disagree for capacity inference. Protected harm can justify reducing governed activity without proving a capacity drop. Low throughput alone cannot justify declaring the disk slower. Lowering is reversible, but it is not cheap—you already demonstrated a roughly fourfold build-time penalty.
- **25% maximum steps:** Potentially reasonable for exploratory increases, after measurement. Inappropriate as an emergency reduction limit.
- **30-minute cooldown:** Reasonable to test for recovery or model changes; inappropriate as a lockout on further protective action. If applied to every reduction, accommodating a 20× collapse takes roughly five hours.
- **0.7× worst recorded plateau:** Drop. An anomalous or demand-limited window can poison it, and a historical minimum cannot bound tomorrow’s slowdown.
- **Best proven plateau as an upper bound:** Keep only as a confidence-labelled historical reference. If it is an absolute learning limit, the system cannot discover improved capacity. A separately configured operator maximum is a legitimate policy limit.

Those constants are currently hypotheses, not established engineering margins.

**2. The five weakest assumptions**

1. **“A low plateau is evidence that the model is too high.”**

   Failure scenario: the host has capacity, but a build enters a CPU-heavy phase, hits your cap, or cannot maintain concurrency through the small request queue. Two windows show low delivered throughput. You lower the model, then lower proportional caps, producing an even lower plateau.

   **Change:** Require independent evidence of pending IO demand and identify binding controls. Treat ambiguous windows as unknown. Separate protective cap reductions from model updates.

2. **“Busy ≥85% or vrate at max proves saturation and permits increases.”**

   Failure scenario: the device spends most of its time with a request outstanding but still has substantial parallel capacity; alternatively, vrate reaches max because of model normalization. Neither proves a sustainable ceiling. Conversely, a bandwidth limiter can constrain throughput without satisfying your expected queue/latency pattern.

   There is also a learning deadlock: after a cap drops, production cannot demonstrate capacity above that cap. Requiring proof before every increase can strand you indefinitely.

   **Change:** Use a conjunction of demand, controller state, request mix, and sustained performance—not this OR rule. Explicitly choose between bounded increases using ordinary work and operator-only recovery. The first involves experimentation; the second sacrifices autonomous recovery.

3. **“Fill history predicts the hidden provider state.”**

   Failure scenario: two observations at the same guest fill have different snapshot state, allocation state, backend contention, or cache credit. The fill-bucket table confidently recommends the wrong bound. Your two historical points also differ in several interventions.

   **Change:** Store fill, discard, and annotations as covariates. Do not seed a causal capacity lookup table from them. A `ceiling_shift` produced by your own estimator is an inference, not independent corroboration.

4. **“One ceiling can anchor the cost model and fixed-percentage caps.”**

   Failure scenario: sequential write bandwidth recovers while synchronous-write latency remains poor, or random-write IOPS collapse while large-request bandwidth stays healthy. Rescaling the model and all caps from one number misprices the actual workload.

   Even a correctly measured 70% cap leaves no guaranteed latency reserve: the protected workload is bursty, and bytes, operations, queue occupancy, and durability costs are different constraints.

   **Change:** Decouple policy caps from model calibration. Keep workload-specific performance evidence. Refit model components only when observations actually identify those components.

5. **“The detection and actuation loop is fast enough to protect the game.”**

   Failure scenario: a build queues substantial writeback, the game stalls, the condition persists for a minute, and the controller finally lowers caps. Previously issued work continues draining. A five-minute decision interval or cooldown makes this worse.

   Sparse D-state samples can miss short important stalls or overemphasize blocked noncritical workers. Unverified writeback attribution adds uncertainty about what the cap will actually control.

   **Change:** Measure stopping distance and attribution first. Give protective action its own faster loop and escalation policy. Define exactly how “swap latency” will be obtained; charged swap bytes alone do not provide that latency measurement.

So, **“estimate the ceiling and fit iocost to it” is too narrow as the primary framing**. There are three separate tasks:

- Detect whether protection is failing.
- Change governed activity effectively and promptly.
- Maintain a useful accounting model.

The third can improve efficiency. It should not be a prerequisite for the first two.

Also, “fail-open to last good values” needs a more precise name and contract. Retaining settings is **hold-last**, and “last successfully applied” does not mean “currently safe.” Define behavior for stale telemetry, daemon failure during emergency throttling, partial application, and restart.

**3. Changes to the VM test plan**

A throttleable virtual disk is a good start, but a single throughput knob will favor estimators that assume exactly that failure mode. Test independent dimensions and their combinations:

| Injected behavior | What the test must distinguish |
|---|---|
| Flat bandwidth cap, fixed IOPS cap, and both together | Whether an estimator mistakes workload mix for capacity changes. Sweep request size and direction. |
| Added completion latency with ample bandwidth | Whether B incorrectly translates latency inflation into a bandwidth/model reduction. |
| Token-bucket bandwidth/IOPS limits | Whether quiet-window spacing lets credits refill and hides the sustained limit from B. |
| Finite fast cache followed by slow draining | Whether C promotes a burst rate into a sustained capability claim. Include bursts longer than its qualification window. |
| Write amplification over a constrained backend | Whether write-specific degradation causes inappropriate read/model changes. |
| Intermittent backend contention and long latency tails | Whether a percentile-based estimate ignores periods that determine game performance. |
| Guest request-slot restrictions with healthy backend capacity | Whether A+ and C confuse your deliberate queue policy with backend degradation. |

For snapshot-like behavior, use two levels of fidelity:

- A controlled synthetic backend where one guest write consumes multiple backend operations, with optional metadata and flush costs.
- A real copy-on-write snapshot arrangement over a constrained test backend, with varying overwrite locality and allocation pressure.

Neither reproduces your provider exactly. They exercise plausible mechanisms. A throughput limiter alone reproduces the resulting rate but misses amplification, metadata costs, and tail behavior.

The workload and experiment design matter just as much:

- **Include a protected workload with deadlines.** Prefer a representative game workload; otherwise use a periodic task combining critical reads/writes, memory pressure, and observable deadline misses. Device latency alone is not the score.
- **Run capacity changes during idle, unsaturated demand, sustained demand, and self-throttling.** Include a recovery while governed caps remain low. This exposes the autonomous-recovery deadlock.
- **Hold backend capacity constant while changing workload mix and guest queue settings.** A valid estimator must avoid declaring capacity shifts in these negative controls.
- **Exercise abrupt and gradual changes in both directions, including ≥20× steps.** Add rapid oscillation to test hysteresis and ratcheting.
- **Explicitly measure stopping distance.** Reduce governed activity at several dirty-backlog and queue-depth levels; measure time until protected service recovers.
- **Test controller failures.** Missing samples, cgroup recreation, counter resets, daemon restart, and partial apply failures belong in the experiment.

Rank results first by protected deadline misses and worst stall, then recovery time, governed useful work, false interventions, and time spent unnecessarily restricted. Estimation error is secondary. Also measure how often each estimator correctly says **unknown**.

For G, provide the true injected parameters, but do not assume that a fixed fraction of known capacity is an optimal policy. Include a protected-only run: if it fails under an injected regime, no governed-tier controller can fully solve that case.

Finally, you cannot faithfully replay October 20–23 from a few benchmark summaries. You can simulate a scenario inspired by those measurements. A historical replay requires sufficiently detailed time-aligned demand, controls, and observations; even then it cannot reveal the counterfactual response to different controls.

**4. One safety mechanism and the first week**

If I could keep one, it would be **a protected-harm-triggered emergency brake on governed IO, with measured stopping distance and slow recovery**.

It should make a substantial reduction when severe harm persists, escalate if recovery does not occur, and allow further reductions without waiting for a model cooldown. It cannot prevent the first stall after an abrupt backend collapse; prevention depends on how much work was already allowed into the system.

The smallest first deliverable would be:

- Verify that the existing apply path actually controls the governed IO involved in the incident.
- Collect protected pressure deltas and blocked-thread observations on a short interval; add a game-tick or existing application heartbeat metric if readily available.
- Implement **normal / restricted / recovering** states with a preconfigured, finite emergency cap profile. Freeze model changes.
- Make entry depend on sustained protected distress, with a separate severe-event path. Select timing from controlled stopping-distance tests, not from the proposed one-minute rule.
- Recover gradually only after sustained health; immediately retreat on renewed harm.
- Journal detection, action, recovery time, and whether the action helped. Test restart and stale-telemetry behavior.

Run it against controlled degradation before enabling enforcement on the host. If reducing caps does not restore protected service promptly, the first-week finding should redirect work toward admission control and reducing queued writeback.

Two weeks of observation can improve understanding, but **observation alone adds no protection**. I would spend the first week establishing and deploying one verified protective action, while C runs beside it as an explicitly uncertain observer.

---

# Third opinion: Fable 5.1 (high effort), blind first, then disclosure — 2026-10-27

**Session (reusable):** `873cc1ff-e3cf-4143-952b-93193510fb7f`. It was run headless with no tools from an empty directory (blind to the repo), model `claude-fable-5-1`, effort high.
Resume (use the same flags; the prompt on stdin):
`claude -p --resume 873cc1ff-e3cf-4143-952b-93193510fb7f --model claude-fable-5-1 --effort high --tools "" --output-format json < prompt.md`
Order of events: (1) the same problem statement as the Codex round-1 prompt, blind; (2) disclosure of Codex's two rounds plus the shared facts. **Known slip:** the written text of OUR design (Part 1) was accidentally omitted from that disclosure, so Fable saw it only through Codex's recap; (3) a correction round that sent the missing text and asked what changes. Answers below are verbatim; read round 3 as the final position.

## Round 1 answer (blind)

## 1. What I think the real problem is

You built an absolute-capacity controller (iocost model + a static `io.max`) on top of a volume whose capacity is not a property of the volume but of the provider's thin-provisioning/CoW state, and that state swings ~20-30x without any guest-visible identity change. Worse, the error is asymmetric: a model/cap that is too **low** costs you a 4x longer build (visible, annoying), while a model that is too **high** gives you *no protection at all* (iocost thinks there is slack, admits everything, the 16-slot queue fills with governed-tier writes, `vrate` can only fall to the `min` clamp — a 2x correction against a 23x error). So the current design degrades silently in exactly the direction you care about.

Most decisive facts, in my reading:

- **Reads stayed at ~2.1 GB/s while writes collapsed to ~15 MB/s.** That is a *shape* change (snapshot CoW penalises first-writes, not reads), and a scalar `vrate` can only scale the whole model. No amount of clamp tuning makes a scalar track a vector. The write side of the model is the only part that is actually unstable.
- **The recovery was caused by guest-side hygiene** (free space, fstrim, provider "optimise") plus the UI showing 1.1 TB used of 1 TB at ~50% guest fill. That means ~half a terabyte of never-discarded blocks plus a snapshot. The degradation is at least partly *preventable*, which changes the problem from "estimate the ceiling" to "keep the ceiling up and notice quickly when it falls".
- Most suspicious: **`vrate` pinned at the floor (50) with latency targets met and no cgroup short of budget, driven by `rq_wait > 5%`.** That is your own `nr_requests=16` poisoning iocost's saturation detector. iocost is throttling the governed tier by half *for no reason the protected workload can feel*, and you correctly concluded "vrate at a clamp is not a staleness signal" — but the stronger conclusion is that iocost's primary feedback loop is disabled on this host. You are running iocost open-loop.
- Second suspicion: **"device busy 65-89%" at 35 MB/s average** is meaningless with virtio + a trickle of sync writes (io_ticks counts any in-flight request). Don't let it into any estimator.

Assumptions I'm making: the game server's IO is mostly small synchronous writes (saves/logs) plus swap-in; builds are mostly buffered writes via overlayfs plus occasional fsync storms (apt/dpkg, pip); the virtio-blk device has a driver queue depth well above 16; kernel 7.x mq-deadline has the per-cgroup priority classes (`io.prio.class`, present since 5.13).

## 2. Ideas (not converging — ranked loosely by "how much I'd bet on it", but read them all)

**A. Stop estimating the ceiling; control the thing you actually care about**

1. **Latency canary in the protected cgroup.** One preallocated, rotating 4 KiB `fdatasync` write per second (≈350 MB/day), issued from the game server's cgroup, timed. Not a bandwidth probe — it measures *what the game server experiences*, including queueing behind governed IO. Drive everything else (cap, model state) from its p99.
   Signals: own timing only; optionally compare against `io.stat` of the governed cgroup. Failure: hypervisor write cache makes it look fine until the cache is full — but that is also what the game server sees, so it's the "right" lie; also rewriting the same blocks never pays the CoW penalty, hence the rotation requirement.

2. **AIMD on the governed cap, driven by protected-side latency/pressure, no device model at all.** Raise `io.max` (or `io.cost.qos max`) by ~10%/min while canary p99 and protected `io.pressure` are under target; halve it on a miss; floor it at something that keeps builds finishing. Classic TCP-style, re-implements `io.latency` in userspace but with memory and hysteresis.
   Signals: #1 or protected cgroup `io.pressure some/full`, per-cgroup latency via eBPF. Failure: the protected signal is sparse (game server IO is a trickle), so the loop reacts in seconds while stalls are sub-second; a burst from a new build will land a stall before the controller halves. Needs a conservative starting point after every quiet period.

3. **`io.prio.class` + mq-deadline priority classes — model-free priority.** Set the protected cgroup to `promote-to-rt`, the governed tier to `idle` (or `restrict-to-be`). mq-deadline dispatches RT before BE before IDLE with an aging timer (`prio_aging_expire`), independent of what the device can do. No rate figure anywhere.
   Signals: none needed. Failure: it orders dispatch *within* the scheduler's 16 slots; if all 16 are already governed writes the RT request still waits for a slot (so raise `nr_requests` modestly or keep iocost weight for admission); flusher-thread writeback is attributed to the originating cgroup under cgroup-v2 writeback, but verify that the prio class actually reaches it; aging can let a starving build dump 10 s of pent-up IO at once.

4. **Make the model pessimistic on writes and let `vrate` climb.** Set `wbps/wseqiops/wrandiops` to the *degraded* floor, keep the read side at the healthy figure, set `qos max` to its upper bound (vrate can go up to 10000%). iocost's upward adjustment is exponential (percent-steps per 50 ms period), so a 20x climb takes a couple of seconds, not minutes — your 2000-2800 observation shows it works. Errors are now only in the safe direction.
   Signals: `iocost_ioc_vrate_adj` tracepoint for monitoring only. Failure: in healthy state reads become 20x "too cheap" and effectively uncontrolled (probably acceptable, reads were never the bottleneck); `vrate` also drifts up while idle and over-admits for the first period of a burst; and it still needs the rq_wait signal to be honest (see #6) or it will sit at the floor again.

5. **Two (or three) named profiles with hysteresis instead of a continuous estimate.** "healthy" / "degraded" model+qos+io.max sets; switch on a change detector on per-request write service time (CUSUM on `write_ticks/writes` deltas from `/sys/block/vda/stat`, which with `max_sectors_kb=64` is nearly a direct capability measurement because every request is ≤64 KiB), plus human annotation to force a state.
   Signals: `/sys/block/vda/stat`, annotation file. Failure: the CoW penalty is bimodal (only first-writes to a block since the snapshot pay it), so service time is a mixture, not a shift; more than two states exist; rewriting `io.cost.model` at runtime resets accounting transiently.

6. **Fix the feedback you already have: stop `nr_requests=16` from lying to iocost.** Either raise `nr_requests` to the smallest value at which `rq_wait_pct` stays under 5% during normal load, or move the stall protection into iocost itself (lower weight + `io.cost.qos` targets) and let the queue be deeper. Then `vrate` becomes a usable staleness signal again.
   Signals: tracepoint reasons, `io.stat` `cost.wait_us` per cgroup. Failure: you traded 26.9 s stalls for 0.14 s with the small queue; a deeper queue reopens that in degraded state unless iocost admission really holds (and iocost's admission is exactly what's stale). The two protections fight.

**B. Change what is being controlled**

7. **Throttle dirty memory, not block IO, for the governed tier.** Tight `memory.high` on the governed slice (cgroup-v2 writeback-aware dirty throttling) so builds can never have hundreds of MB of dirty pages queued for a device that may only do 15 MB/s; bursts become back-pressure in `write(2)` rather than a writeback storm the protected cgroup must queue behind.
   Signals: `memory.stat` dirty/writeback per cgroup. Failure: does nothing for reads or direct IO; with 15 GB total RAM a tight `memory.high` on builds can push them into reclaim thrash; fsync storms still hit the device at full depth.

8. **Remove the protected workload's dependence on the disk.** Swap for the game server to zram (`memory.swap.max=0` on the disk swap for that cgroup, or zram-only swap system-wide), `memory.min` guarantee, assets preloaded/mlocked, saves batched. If the 26.9 s stall was swap-in behind a writeback storm — which I'd bet on — this removes the whole class.
   Signals: game server's swap in/out rate, major faults, fsync count. Failure: if its working set genuinely exceeds RAM you cannot; zram compresses game state poorly; synchronous saves remain disk-bound.

9. **Put the governed tier on a different volume that is never snapshotted.** A second provider disk (or at minimum a separate thin pool / LV with its own discard cadence) for build scratch, overlay layers, and test-gate data. Snapshots are per-volume: the volume that gets CoW'd is the one you don't do bulk writes to.
   Signals: none. Failure: the provider may back both volumes with the same pool, so throughput interference persists (but CoW penalty wouldn't); cost; container runtime root relocation is fiddly; tmpfs is out at 15 GB.

10. **Control concurrency, not rate: BFQ with weights.** BFQ is proportional-share with no device model — it divides *time*, so a 20x slower device just means 20x less data per slice, protection unchanged. Low-latency heuristics favour the sync trickle of the game server automatically.
    Signals: none. Failure: BFQ's idling costs throughput on the healthy 500 MB/s state (it tops out in the few-hundred-MB/s range on single-queue dispatch); less deterministic than mq-deadline for worst-case stalls; must drop iocost (two rq_qos policies fighting) and you lose `io.max`-style hard caps.

**C. Use information from outside the guest**

11. **Poll the provider API for the state you "can't see".** The UI showed "1.1 TB of 1 TB used" and "snapshot active"; if the UI can see it, an API almost certainly can. A systemd timer polls used-bytes and snapshot presence and flips the profile from #5, or just alerts a human.
    Signals: provider API (credentials inside the VM — scope them read-only). Failure: not every provider exposes this; the "optimise" state may not be exposed; rate limits; provider-initiated backup snapshots may not appear as "your" snapshots.

12. **Human annotation as a first-class input.** An `/etc/iogov/state` file or a one-line CLI: "snapshot taken", "snapshot deleted", "optimise ran", "bulk delete". The daemon treats it as a prior and resets its estimate. Cheap, honest about what is actually unobservable.
    Signals: a human. Failure: humans forget, and provider-initiated events have no human on this side.

13. **Prevent the degradation instead of detecting it.** Daily `fstrim` (plus `discard` on LVM so thin-pool unmap propagates), keep fill under ~60%, treat any snapshot as a budgeted, time-boxed object (create → copy off → delete within hours), run the provider's optimise after every bulk delete. Make the "healthy" state the maintained invariant.
    Signals: fill, `fstrim -v` output, snapshot age. Failure: provider backup policy may keep snapshots you don't own; fstrim on a CoW'd volume is itself slow and bursty; the 1.1 TB figure suggests discards were not propagating at all, which may be a provider property you cannot fix.

**D. Reduce the need for control**

14. **Shrink the governed tier's demand per unit of work.** `eatmydata`/`--no-sync` for apt, dpkg `force-unsafe-io`, pip/npm cache on a separate slice, buildkit cache mounts, avoid overlay copy-up of big trees, don't `fsync` in test gates that get thrown away. The question of the ceiling matters less if the build writes 1/3 as much and never fsyncs.
    Signals: fsync counts (eBPF or strace on a sample build), bytes written per build. Failure: some gates need durability semantics; reduces average, not necessarily peak depth.

15. **Time-share rather than rate-share.** Run builds only when the game server is idle (player count, tick-time, or a calendar), and `cgroup.freeze` the governed slice when the canary (#1) misses. Coarse, no model.
    Signals: game server's own metrics; canary. Failure: freezing does not stop already-dirty pages from being written back; builds queue up and become wall-clock bound; "idle" may never arrive on a 24/7 game server.

16. **Trailing-maximum cap with slow decay.** If you insist on a cap: cap = k × the highest sustained throughput observed from *real* traffic in the last N hours, decaying slowly, never below a floor. Uses the builds as the benchmark.
    Signals: `/sys/block/vda/stat` throughput and latency windows. Failure: ratchets up only when something saturates, which is rare (demand is far below ceiling); the hypervisor write cache makes short peaks look like capability (your 4.3 GB/s lesson); after a downward step the cap stays high until latency shows it — which is the unsafe direction.

Two constraints I'd push back on: "never probe" is too strong — a 4 KB/s latency canary is not a benchmark and a 3-second saturation probe issued *inside the governed cgroup under its normal weight* is indistinguishable from a small build; what you must avoid is cache-defeating sustained probes. And "iocost is the tool" deserves a hard look: its design premise is a device with a stable cost structure, and this volume violates that in the one component (write) that matters. The combination #3 + #8 + #13 might give you most of the protection with no model to go stale.

## 3. What I'd want to measure or learn first

1. **What the game server's IO actually is.** IOPS, sync fraction, swap in/out rate, and — crucially — what the 26.9 s stall was waiting on (swap-in? `fsync` behind a writeback storm? overlayfs?). If it was swap, idea #8 solves the problem class and the rest is tuning.
2. **Whether the game server was harmed at all during the uncapped build on the healthy disk** (pressure 1-9%, 1100 s). If tick-time/p99 was fine, the cap is pure cost in the healthy state and the real job is only the degraded state — where the device throttles everyone and the only thing worth controlling is queue-slot share.
3. **Retrospective per-request write service time vs read service time across the three dated states**, from whatever metrics you already keep. Does `w_await` separate 15 MB/s from 350 MB/s cleanly while `r_await` stays flat? That decides between the scalar-vrate family (#4) and the shape-aware family (#5).
4. **The hypervisor write-cache absorption curve**: how many MB of burst get absorbed before latency rises, and how fast it drains. This sizes any canary/probe and tells you whether your 143 MB/s peaks ever touch the real device.
5. **Whether `rq_wait > 5%` is purely `nr_requests=16`**: raise to 32 for ten minutes under normal load and watch the tracepoint's reasons and `io.stat` `cost.wait_us` for the governed cgroup. This tells you whether iocost has a working loop at all.
6. **How often `io.max` actually engages** (governed `io.pressure`, throttle stats) versus iocost admission. If the cap only binds during the pathological stale case, delete it.
7. **What the provider API exposes**: used bytes, snapshot list/ages, optimise status, and whether discards propagate (`fstrim -v` bytes vs the provider's "used" afterwards). One afternoon with the docs.
8. **Whether mq-deadline prio classes are live on this kernel** (`/sys/block/vda/queue/iosched/prio_aging_expire` exists; `io.prio.class` accepted on the cgroups) and whether cgroup writeback carries the class — a five-minute check that gates idea #3.
9. **Who else writes**: journald, the container runtime's own layer extraction, log rotation — anything not in either slice that lands in the root cgroup and is invisible to both iocost weights and your caps.
## Round 2 answer (after disclosure of Codex rounds; our design text was missing)

## 1. Synergy

### Where all three agree (treat as settled)
- Disk identity is the wrong validity boundary for a calibration; one scalar ceiling cannot carry both the iocost model and the caps, because the collapse is write-specific (reads stayed at 2 GB/s).
- `vrate` at a clamp is not a staleness signal here, because `nr_requests=16` keeps `rq_wait > 5%` whenever the governed tier runs. Your estimator **A** is dead; Codex and I both killed it independently.
- Protective action and capacity estimation are different tasks; the first must not depend on the second. Codex's "safety controller first, estimator second" is right.
- Stopping distance is unmeasured and gates every reactive design.
- Buffered-write attribution to cgroups is unverified; until it is, you do not know what `io.max` actually controls.
- Provider state, fill, discard history, annotations: covariates and priors, never a lookup table. Drop `0.7× worst plateau`; drop "best plateau as learning ceiling"; drop the 30-min cooldown for protective moves.

### The thing all three of us walked past
Do the arithmetic on your own 26.9 s → 0.14 s result. Worst-case slot wait for the game's request ≈ **bytes in flight below the scheduler ÷ device write rate**. Before: Debian/virtio defaults are roughly `nr_requests=256`, `max_sectors_kb≈512-1280` → 128-320 MB in flight; at the ~6 MB/s build floor that is 21-53 s. After: 16 × 64 KB = 1 MB → ~0.17 s at 6 MB/s. Both numbers land on your measurements. (Assumption: the bpftrace stacks were in tag allocation — `blk_mq_get_tag`/`__get_request` — not in `balance_dirty_pages` or a journal commit. Check that; it is the single most informative thing you already have on disk.)

If that holds, the consequence is large: **you already have a model-free, self-scaling stall bound.** It needs no ceiling; it gets *tighter* as the device gets slower in time terms (constant in bytes). The queue settings are the primary protection, iocost is secondary, and the whole "estimate the ceiling" program is about efficiency in the governed tier, not safety of the game. Nobody — your design, Codex, or my blind round — stated this.

A second consequence I have to own: with `nr_requests=16` below the virtio hardware queue depth (typically 128-256), the scheduler never actually queues anything; every request is dispatched immediately and the wait is for a tag held by an in-flight request. So **my blind idea #3 (mq-deadline `io.prio.class`) is dead on this host** — priority ordering within an always-empty scheduler queue does nothing. Retracted. Same structural reason kills **my #4 (pessimistic write model + wide vrate range)**: vrate can only rise while `rq_wait < 5%`, and the governed tier will fill 16 slots whenever it is busy, so vrate pins at `min` and the pessimistic model becomes the 4694 s build. The only way to make #4 work is to relax the queue bound, which trades away the one mechanism that is proven. Codex was right to say "don't relax the queue to make vrate look healthy"; I'd now say it more strongly: iocost's feedback loop is structurally unusable here, so run it **open-loop by design** (`min=max`), as a weighted share of a *policy* budget, and stop treating its model as a device model.

### Genuine conflicts
| Issue | Us | Codex | Me | Who's right |
|---|---|---|---|---|
| Primary signal for the brake | cgroup pressure + D-state | game tick latency | storage canary (4 KB `fdatasync`/s from the protected cgroup) | Me for the *trigger*, Codex for the *scorecard*. Tick latency conflates CPU/net/GC with storage and will cause false throttling; the canary isolates storage and gives dense samples where the game's own IO is a trickle. Collect tick latency anyway to grade outcomes. |
| Where the brake lives | userspace daemon (us, Codex) | — | kernel `io.latency` on the protected cgroup, fed by the canary | Try mine first: it's one line, 100 ms windows, acts in `rq_qos` before tag allocation (exactly where the slot-wait happens), reduces *peer queue depth*, no model. Unknown: coexistence with iocost. If it misbehaves in the VM, Codex's userspace state machine is the fallback, not the first choice. |
| Fate of estimator C | numerical input via D | observe-only, can abstain | logger only; nothing consumes it | Codex and I agree; I go further: if the dynamic cap goes away, there is no stale number left to estimate, so C has no customer for now. |
| Standing dynamic cap | yes, proportional to model | broad guardrail only | drop it or freeze it high | A cap below the real rate is the only way the degraded state *stays* expensive after recovery (the learning deadlock Codex named). No cap → no deadlock. |
| Swap | accounted, charged to game | memory headroom | zram or `memory.min` for the game | Mine is the cheapest change on the table: game swap-ins are sync reads competing for the same 16 tags. Remove the dependency instead of controlling it. |

### What each missed that another caught
- **You missed:** the stall-bound arithmetic; that the small queue disables iocost's loop; `io.latency`; zram; admission by workload class (Codex #13 — cheap and robust).
- **Codex missed:** the arithmetic; `io.latency`; dirty-backlog bounding as the stopping-distance reducer (it had memory headroom, not the writeback angle); that attribution failure means the flusher's IO lands in root and *no* cgroup actuator touches it.
- **I missed (blind):** the identification problem ("low throughput = demand? throttle? slow disk?") stated as cleanly as Codex did; cap provenance/expiry (#7); the learning deadlock; the whole VM test matrix, which is excellent and I'd adopt wholesale, especially the negative controls (constant backend, changed workload mix) and "protected-only run".

### Ranking of mechanisms
1. **Queue bound as the recognised primary stall limit** (deployed; formalise it, consider 8×32 KB in a degraded profile).
2. **Game disk-independence**: zram swap + `memory.min` on the game cgroup.
3. **Canary + `io.latency`** (≈ Codex's emergency brake, in-kernel). Codex's userspace brake is the same rank as fallback.
4. **Dirty-backlog bound**: `memory.high` on the governed slice + modest `vm.dirty_bytes`.
5. Admission by workload class (Codex #13).
6. Your C as a logger; B as a 1-bit regime classifier on `w_await`.
7. Pessimistic model / prio classes: retracted. A, A+, D: drop.

## 2. The simple solution

Give up tracking the ceiling. Bound bytes, not rate.

**Components (five):**
1. **Queue bound** — `nr_requests=16`, `max_sectors_kb=64`, mq-deadline (as now). Optional two-profile switch: if `w_await` from `/sys/block/vda/stat` exceeds ~5 ms over 60 s, go to `nr_requests=8`, `max_sectors_kb=32` (256 KB in flight → ~40 ms at 6 MB/s); return after 10 min under 2 ms. This is the only "estimation" and it's one bit with hysteresis.
2. **Game disk-independence** — zram swap sized ~4 GB with `memory.swap.max=0` on the game's cgroup for disk swap (or simpler: `memory.min` = game RSS × 1.2 and no swap for it at all). `fstrim.timer` weekly, `issue_discards=1` in lvm.conf, fill alarm at 60%.
3. **Canary** — a 20-line systemd service in the protected cgroup: one 4 KiB `pwrite`+`fdatasync` per second into a rotating 512 MB preallocated file (rotation so it keeps paying any CoW penalty), logging latency. Signals: canary latency, protected `io.pressure full avg10`, `/sys/block/vda/stat`.
4. **Brake** — `io.latency target=20000` (20 ms) on the protected cgroup, siblings being the governed slice (and a third slice for journald/container runtime/anything else that writes — root-cgroup writers are invisible to every actuator). Fallback if `io.latency` and iocost don't coexist: userspace loop at 100 ms, trip when canary p95 over 3 s > 25 ms or `full avg10 > 5%`, set governed `io.max` to `rbps=8M wbps=2M wiops=200` (near-stop, not freeze — tests with timeouts stay alive), hold until 30 s clean, release by doubling every 30 s, re-trip resets. States normal/restricted/recovering, journaled, daemon death ⇒ cap stays (hold-last, named as such).
5. **Backlog bound** — `memory.high` ≈ 6 GB on the governed slice; `vm.dirty_bytes=256M`, `vm.dirty_background_bytes=64M`. Keeps the post-brake drain short and bounds `balance_dirty_pages` stalls if attribution turns out imperfect.

**iocost**: keep weights (1000/100) for healthy-state fairness, model = the healthy calibration, `qos min=max=100` so vrate never moves and never misleads. No dynamic cap; a static `io.max` at ~50% of healthy write (e.g. 200 MB/s) only to leave hypervisor-cache headroom; it protects nothing in the degraded state and that is fine, because 1 and 4 do.

**Not covered:** backend events where a single 4 KB write takes seconds (nothing in the guest helps); the game's own `fsync` storms (saves) if those are the stall; CPU/network latency; a provider snapshot you never learn about — you'll just see the canary rise and the brake trip more often, which is the correct behaviour; false trips when the game itself causes its latency (costs builds ~30-60 s, acceptable); any writer outside the three slices.

**Cost to the governed tier:** healthy — essentially zero (no binding cap; 1 MB in flight supports ~47k IOPS at 0.34 ms, which is what you measured). Degraded — it gets the disk minus brake periods, i.e. 10-15 MB/s builds taking as long as physics says. No controller fixes that; the estimator program never would have either.

**Build and prove:** 1-4 are config plus ~300 lines; one week including the VM rig. Proof: VM with the governed slice running a real image build and the protected slice running a synthetic game (periodic 4 KB fdatasync + 200 MB/s of its own reads + deadline tracker), backend switched from ~400 MB/s to ~10 MB/s mid-build via QEMU `throttling.bps-write` *and* a dm-delay/qcow2-snapshot variant for the CoW shape; Codex's negative controls included. Pass: protected worst stall < 250 ms and p99 < 30 ms in the degraded regime, brake stopping distance < 1 s, build time within 1.3× uncapped in healthy. Abandon if: stalls remain seconds-long despite the queue bound (⇒ the stall isn't a slot wait and the whole model of the problem is wrong), or the brake doesn't move the device write rate within ~1 s (⇒ attribution broken; fix that before anything else), or trips oscillate so builds in the degraded state collapse below the disk's own rate.

**Is it good enough?** For the protected objective, yes, and I'd bet on it. The estimator program buys only one thing: a smooth, binding governed rate in the degraded state instead of a bang-bang brake. Upgrade only if the VM run shows the brake tripping more than a few times a minute during a degraded build (then build AIMD on the *governed cap* driven by the canary — still no device ceiling), or if the canary shows the game's slot wait in the degraded state is itself above target even without trips (then and only then does iocost admission accuracy matter, and C becomes a customer-bearing estimator).

## 3. Next week, in order

1. **Re-read the 2026-10-20 bpftrace stacks and classify the stall site** (tag wait vs `balance_dirty_pages` vs journal/`fsync` vs swap-in), then **measure stopping distance on the VM rig**: backend throttled to 10 MB/s, build running, apply the emergency `io.max`, time until canary latency and in-flight count recover, with D-state stacks captured. This one experiment simultaneously tests the stall-bound hypothesis, buffered-write attribution (if the device write rate doesn't drop within a second, `io.max` doesn't reach the flusher), and whether a reactive brake can work at all. It most reduces uncertainty because every other decision branches on it.
2. **Ship the zero-risk set to production**: zram/`memory.min` for the game; a third slice for journald and the container runtime; freeze the dynamic cap (static, high); `io.cost.qos min=max=100`; `fstrim.timer`, `issue_discards`, fill alarm; the canary as observe-only logger; an annotations file and, if the provider API exposes it, a timer that logs snapshot/used-bytes. None of this needs the week-1 result.
3. **Try `io.latency` on the protected cgroup in the VM**, alongside iocost, under the same degraded-build scenario. If it works: ship with the canary. If it fights iocost: implement the userspace brake exactly as Codex's state machine with my canary trigger, timing taken from step 1, and ship that. Either way, leave C running as a logger with abstention, and don't write a single line of D.
## Round 3 prompt: the omitted design text and the follow-up questions

Correction: my previous message was meant to contain PART 1 (our own written design) but it was accidentally omitted, so you only saw it second-hand through Codex's recap. Here it is verbatim.

# PART 1 - Our existing design and candidate estimators (not yet built)
## What we have already written down (a design, not yet built; "nothing implemented")
A small systemd service (Python, reads procfs/sysfs/cgroup only, writes through the one existing apply path; modes off | observe | enforce-down | enforce, default observe; fail-open to last good values; every change journalled with before/after/rule/evidence). Objective: protect a declared "protect list" of cgroups (the game server), governed side (builds/gates/dev containers) gives way first. Per-target signals: the protected cgroup's `io.pressure` (some/full deltas), its `io.stat` rates, count of its threads in D state sampled each tick, device `/sys/block/<dev>/stat`, swap latency; optional bpftrace per-cgroup block latency, off by default and time-boxed. Covariates: filesystem fill (a table fill-bucket -> best proven bound from history, seeded with the two known points), discard history (read only), change-point events (`ceiling_shift`), human annotations via `note`.
Decision every 5 min and on events, asymmetric trust: lowering is cheap so one pair of windows (plateau below the model by >25% in two consecutive windows) suffices; raising needs a proven saturated window (device busy >= 85% or vrate at max, latency under target) and never exceeds the configured ceiling. Steps at most 25%, 30 min cooldown per direction, model never below 0.7x the worst recorded plateau or above the best proven plateau; dev caps stay a fixed percentage of the model. Harm to a protected target (io.pressure stall above threshold >1 min or persistent blocked threads) freezes all increases and steps governed caps down regardless of any capacity estimate. No probe IO, ever; increases wait for the workload to prove capacity; the operator can run the benchmark by hand.
Candidate estimators (we want to pick by measurement in a VM whose disk we control: a throttleable virtual disk where we inject ceiling changes):
- A. `vrate` edge: lower the model when vrate sits at the floor for N periods, raise at the ceiling. Simplest; but vrate sits at the floor on this host with healthy latency (request-slot waits, our own nr_requests), so bare A spirals to the rail. A+ = act only if the floor coincides with missed latency targets / rising queue depth, raise only when demand was held back.
- B. Quiet-window service time: keep only windows with ~1 request in flight, track per-request service time by direction and size band, compare last 30 min against last 7 days, act on a sustained 3x change. Needs no saturation, unaffected by our own throttling; but a provider bandwidth/IOPS cap may not change single-request time (it may miss exactly our failure); needs days of history.
- C. Saturated-window ceiling: keep only windows where the disk was the limit (latency rising with queue depth, delivered rate not near our own cap, demand present); delivered rate there is a lower bound on capability; track a high percentile with decay and fit the model to it. Needs load to learn; stale on a quiet host (must say so and hold).
- D. Hybrid: B as early warning, C as the number; model changes only when C confirms (or B overwhelming and persistent).
- Controls: E static baseline (today), F "price the worst plateau" with a wide QoS max so vrate climbs by itself, G oracle (test only).
Phases: P0 observe-only for two weeks and replay the 2026-10-20 -> 10-23 shift; P1 surface "recalibrate recommended" in the doctor/wizard; P2 enforce-down; P3 enforce with increases only on saturated windows.
Current stop-gap: dev tier capped at ~70% of the measured sustained ceiling (700M read/250M write B/s, 50k/30k IOPS) and a hand-set io.cost model and QoS; re-measure after any storage change.


Please (a) say whether anything in your previous answer changes now that you have the actual text, being explicit about corrections to what you attributed to us or to Codex; (b) re-check specifically the parts of our design you may not have seen: the protect-list/target-by-cgroup signal table, the covariates (fill table, discard history, change points), the 'device-only mode', the 5-minute decide loop and the 25%/30-min/0.7x rules, the phases P0-P3, and the stop-gap static caps (700M read / 250M write, 50k/30k IOPS, io.cost model '1000000000 70000 70000 340000000 40000 40000', QOS '95 5000 90 25000 50 400'): do the stop-gap numbers interact badly with your simple design (e.g. with qos min=max=100 and the static io.max at ~50% of healthy write)? (c) Give a final one-paragraph recommendation: what to build first, what to defer, what to kill.

## Round 3 answer (final position)

## (a) What changes now that I've seen the real text

Codex's recap was faithful; nothing I attributed to *Codex* needs correcting. Three things I attributed to *you* do:

1. **You already have a brake.** "Harm to a protected target … freezes all increases and steps governed caps down regardless of any capacity estimate." I wrote as if you had none. Correction: you have the seed of one, but as written it cannot do the job — detection needs `>1 min` of stall, the loop runs every 5 min "and on events" (unspecified), and unless the harm path is explicitly exempt from the 25%/30-min rules, a 20× collapse takes ~5 h to accommodate. My round-2 recommendation stands with sharper wording: don't add a brake, *promote* the harm path into one with its own 100 ms sampler, a near-stop emergency profile, and no step/cooldown limits on the way down.

2. **"Pessimistic write model + wide vrate range" was not my idea — it's your control F.** I claimed novelty; wrong. And my retraction applies to F in full: on this host vrate cannot climb while the governed tier is active because `rq_wait > 5%` is produced by `nr_requests=16`, and iocost raises vrate only when cgroups are short *and* the device looks unbusy. F will lose in the VM test for a structural reason that is stronger than Codex's "one multiplier can't fix shape" — and only if the VM reproduces the small-queue effect (Codex's "guest request-slot restrictions with healthy backend" row is therefore mandatory, not optional).

3. **"No probe IO, ever"** is stricter than I assumed. As written it forbids the canary. I'd amend it to "no *capacity* probing; a protected-side latency sentinel at ≤4 KB/s is permitted". Without it, your signal table has no dense storage-only latency signal: `io.pressure some` fires on any worker thread, `full` almost never fires on a multithreaded server, D-state counts per tick miss sub-tick stalls, and "swap latency" has no procfs source at all (Codex's point; confirmed by the text — it can only come from the time-boxed bpftrace or the canary).

Everything else from round 2 holds: the stall-bound arithmetic, the retraction of prio classes, `io.latency` as the first thing to try, zram/`memory.min`, dirty-backlog bounding, admission by class. Your text confirms you had none of those; it also confirms Codex's praise of the service architecture (modes, journal, single apply path) — keep all of that; it's the inference and the constants that go.

## (b) Part-by-part re-check

**Protect-list / per-target signals.** Good shape, wrong resolution. Keep `io.pressure some` deltas and protected `io.stat` (they tell you "did it want IO"), but the harm trigger should be (i) the canary's latency and (ii) the D-state of *the named tick thread*, not a count across all threads — a blocked log-writer is not harm, and a 5-second tick stall is one sample at a 1 s tick. Sample those two at 100 ms; everything else can stay at 5 min.

**Covariates.** Fill-bucket → best-proven-bound table: kill as a lookup (two confounded points, different snapshot states, three interventions on 10-23). Keep fill as a logged covariate with a 60% alarm driving hygiene. Discard history: feeds the `fstrim` timer, not the estimator. `ceiling_shift`: a journal entry produced by your own estimator; it cannot corroborate that estimator. `note`: keep, first-class.

**Device-only mode.** It isn't named in the text you pasted; I take it to mean "no protect list declared, regulate from `/sys/block` stats alone". In my framing it has no objective to serve, so it should be observe/doctor-only. The single device-only action with a safe sign is the two-profile queue switch (`w_await` > 5 ms sustained → `nr_requests=8`, `max_sectors_kb=32`; hysteresis back), because its failure mode is a slightly slower governed tier, never a less-protected game.

**5-min loop, 25% steps, 30-min cooldown, 0.7× floor, best-plateau ceiling.** Agree with Codex on all five, with two additions. The 0.7× floor is inert anyway: your worst recorded plateau is the ~6 MB/s build floor, so the model floor is 4.2 MB/s. And the "configured ceiling" on raises is the one constant worth keeping — rename it *policy ceiling* so nobody later treats it as a capacity fact.

**Phases.** P0: fine, but run the canary logger inside it, and call the 10-20→10-23 thing a *scenario*, not a replay (Codex is right: you have summaries, not time-aligned demand/controls/observations). P1 "recalibrate recommended" is the most valuable thing an estimator can ever output on this host — a human-facing hint — keep it. P2 enforce-down *is* the brake, but with the fast loop. P3 (raises on saturated windows) should be deferred indefinitely: with no dynamic cap there is nothing to raise, and the learning deadlock disappears with it.

**Stop-gap numbers vs the simple design.** Decoded: model `rbps=1 GB/s, rseqiops=rrandiops=70k, wbps=340 MB/s, wseqiops=wrandiops=40k`; QoS `rpct=95 rlat=5ms wpct=90 wlat=25ms min=50 max=400`; `io.max` on the dev slice `700M/250M B/s, 50k/30k IOPS`.

- *Today, iocost — not `io.max` — is the binding control in the healthy state.* vrate is pinned at `min=50`, so the governed tier's effective budget is ~170 MB/s write / 500 MB/s read, under the 250M/700M caps. The caps are dead weight. Nobody had noticed this because nothing exceeds 143 MB/s anyway.
- *Switching to `min=max=100`* makes the budget 340/1000 and the `io.max` caps become binding at 250/700. Net effect: the governed tier gains ~1.5× headroom it almost never uses; the game loses nothing, because the 2× "margin" from vrate=50 was never protection in the degraded state (20× error minus 2× is still 10× over-admission; the queue bound and the brake carry that). Keep your 250M/700M rather than my ~200M; the exact number is unimportant and yours has provenance. If you want to be conservative, `min=max=50` is also fine — the point is only that vrate must stop *moving*, so it stops being read as a signal.
- *IOPS terms are nearly inert.* With `max_sectors_kb=64` every request is ≤64 KB, so 250 MB/s sequential is ~3.9k IOPS and the 30k/40k numbers bind only for small-random phases. Don't spend estimator effort on them; `rbps/wbps` are the only components that ever need touching.
- *QoS latency targets* (5/25 ms) are device-wide, not per cgroup; with a 1 MB in-flight bound at 15 MB/s, write latency is ~70 ms, so `wlat` is missed in the degraded state and vrate goes to `min` — the same pinning by a different route. With `min=max` they become no-ops; leave them for documentation.
- *`io.latency` on the protected cgroup* is the one real unknown: it co-registers in `rq_qos` with iocost and acts on sibling depth, while iocost acts on vtime; I expect coexistence but have not seen it proven. VM test before production.
- *Fail-open to last good values* in `enforce-down`: if the daemon dies after stepping down, caps stay low indefinitely. Hold-last is the correct default; add a watchdog that alerts after N minutes in a restricted state with the daemon absent.

## (c) Final recommendation

**Build first:** the queue bound as the formal invariant (plus the one-bit `w_await` profile switch), zram/`memory.min` for the game, a third slice for root-cgroup writers, `memory.high` and `vm.dirty_bytes` on the governed side, the 4 KB/s canary in the protected cgroup, and the brake — `io.latency` on the protected cgroup if the VM shows it coexists with iocost, otherwise your harm path rewritten as a 100 ms loop with a near-stop `io.max` profile, no step limit downward, slow doubling back; freeze iocost at `min=max` with the current model and keep your 250M/700M caps as a policy ceiling. Prove it on the VM rig with Codex's matrix, scored by protected worst stall and stopping distance, and record the 10-20 D-state stack site before anything else. **Defer:** estimator C as an abstaining logger feeding only P1's "recalibrate recommended"; B as a regime classifier; P3 and any automatic model raise until a measured case shows the brake oscillating or the game's slot wait exceeding target without trips. **Kill:** A, A+, F, D, the fill-bucket table, `ceiling_shift` as evidence, the 25%/30-min/0.7×/best-plateau constants, the dynamic cap proportional to the model, "no probe IO ever" in its current wording, and the idea that the service's job is to know the ceiling — its job is to bound bytes in flight, notice protected harm in under a second, and stop the governed tier when it sees it.