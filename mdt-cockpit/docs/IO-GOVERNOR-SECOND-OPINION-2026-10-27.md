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
