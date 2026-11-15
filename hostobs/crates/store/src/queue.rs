//! The bounded writer queue: one writer, a byte budget, and drops that are **counted**.
//!
//! Plan v4 §A.4 bounds the writer queue at `writer_queue_bytes` (8 MiB proposal) and states the
//! loss rule: *"drops counted"*. The sampler got this right (`topos/sampler/src/writer.rs`):
//! the sampling thread never waits for the disk, the queue is bounded in bytes, and the oldest
//! job loses its frame — counted — when the queue is over its bound. What this module adds is
//! the part the sampler left to prose: every count is a field a caller can assert on, every
//! loss carries the number of records and bytes it cost, and the queue **cannot** grow.
//!
//! # Contracts
//!
//! * **The byte bound is checked after every push and on every pop**, so the queue's own
//!   reported size is never above the bound (`QueueStats::bytes <= QueueStats::capacity`). A
//!   job larger than the entire bound is refused at submission
//!   ([`StoreError::JobTooLarge`]) — a queue that accepts a job it must immediately drop is a
//!   lie about its capacity.
//! * **Dropping is explicit and counted.** [`WriterQueue::submit`] returns a [`Submit`] naming
//!   exactly what was dropped (count, records, bytes) so the caller can log it; the counters
//!   ([`QueueStats`]) accumulate over the queue's life, and a *silent* drop is not representable
//!   — the only way a job leaves without being written is through `Submit::dropped`.
//! * **One writer.** [`WriterQueue::take_next`] hands out one job at a time and there is no way
//!   to take two; the queue holds no threads of its own, so the caller decides how a writer runs
//!   (`store` documents the policy: the writer thread is the caller's, the queue is bounded
//!   regardless).
//! * **Flush and seal are explicit decisions, not timers.** [`FlushPolicy`] states the two
//!   rules a writer applies — flush every `flush_every` jobs, and seal a segment when its
//!   `segment_bytes` or `segment_s` bound is reached — and [`FlushPolicy::segment_full`] /
//!   [`FlushPolicy::segment_aged`] answer those two bounds in one place so the policy cannot
//!   drift between the writer and its tests.
//!
//! # Why the queue is synchronous
//!
//! A blocking queue is a thread and a `Mutex`; this crate's contract is that *its* logic is
//! testable without either, so the queue is a plain data structure with a byte budget. The
//! probe's package owns the thread (`bins/probe`), and the property this module guarantees —
//! that the budget holds — does not depend on who calls it.

use std::collections::VecDeque;

use crate::error::StoreError;

/// What one queued job carries: a block payload and the facts that let the store append it.
#[derive(Clone, Debug, Eq, PartialEq)]
pub struct Job {
    /// The payload to append (a `proto` BLOCK frame, byte-identical to what will be stored).
    pub payload: Vec<u8>,
    /// The real time of the block's end.
    pub real_ns: i64,
    /// The incarnation ids the block names (the record's id table).
    pub incarnations: Vec<u64>,
}

impl Job {
    /// A job for `payload` at `real_ns`.
    #[must_use]
    pub fn new(payload: Vec<u8>, real_ns: i64, incarnations: Vec<u64>) -> Job {
        Job {
            payload,
            real_ns,
            incarnations,
        }
    }

    /// The bytes this job occupies in the queue: the payload plus a fixed estimate for the
    /// id vector and the job's own header, so a queue of empty payloads still costs something.
    ///
    /// The estimate is deliberately *monotone in what is held* (the sampler's `Job::bytes`):
    /// it counts the payload, eight bytes per id, and [`JOB_OVERHEAD`].
    #[must_use]
    pub fn bytes(&self) -> u64 {
        let payload = u64::try_from(self.payload.len()).unwrap_or(u64::MAX);
        let ids = u64::try_from(self.incarnations.len())
            .unwrap_or(u64::MAX)
            .saturating_mul(8);
        payload.saturating_add(ids).saturating_add(JOB_OVERHEAD)
    }
}

/// The fixed per-job estimate a queue charges (two `i64`/`u64` fields and a `Vec` header).
pub const JOB_OVERHEAD: u64 = 64;

/// What a submission cost: how much was queued and how much was dropped to make room.
#[derive(Clone, Copy, Debug, Default, Eq, PartialEq)]
pub struct Submit {
    /// The bytes this job occupies.
    pub queued_bytes: u64,
    /// Jobs dropped to stay inside the bound (never more than the queue held before).
    pub dropped_jobs: u64,
    /// Records lost with those jobs (each job carried one record).
    pub dropped_records: u64,
    /// Bytes lost with those jobs.
    pub dropped_bytes: u64,
}

impl Submit {
    /// True when this submission dropped something.
    #[must_use]
    pub const fn dropped_anything(&self) -> bool {
        self.dropped_jobs > 0
    }
}

/// The queue's counters, as of the last operation.
#[derive(Clone, Copy, Debug, Default, Eq, PartialEq)]
pub struct QueueStats {
    /// How many jobs are queued now.
    pub jobs: u64,
    /// Their estimated bytes (`<= capacity`).
    pub bytes: u64,
    /// The configured byte bound.
    pub capacity: u64,
    /// Jobs accepted since the queue was built.
    pub accepted: u64,
    /// Jobs handed to a writer since the queue was built.
    pub written: u64,
    /// Jobs dropped since the queue was built.
    pub dropped: u64,
    /// Records lost with dropped jobs.
    pub dropped_records: u64,
    /// Bytes lost with dropped jobs.
    pub dropped_bytes: u64,
}

impl QueueStats {
    /// True when nothing was ever dropped.
    #[must_use]
    pub const fn lossless(&self) -> bool {
        self.dropped == 0
    }

    /// The queue's fill as a fraction in `[0, 1]` (0 when the capacity is zero, which a
    /// configuration refuses anyway).
    #[must_use]
    pub fn fill(&self) -> f64 {
        if self.capacity == 0 {
            return 0.0;
        }
        (self.bytes as f64 / self.capacity as f64).min(1.0)
    }
}

/// The explicit flush/seal policy (§A.4 "flush/seal policy explicit").
#[derive(Clone, Copy, Debug, Eq, PartialEq)]
pub struct FlushPolicy {
    /// Flush after this many written jobs (never zero).
    pub flush_every: u32,
    /// Seal a segment when it reaches this many bytes.
    pub segment_bytes: u64,
    /// Seal a segment after this many seconds of real time.
    pub segment_s: u64,
}

impl FlushPolicy {
    /// A policy, refusing the values that would make it meaningless.
    ///
    /// # Errors
    ///
    /// [`StoreError::Config`] for `flush_every == 0`, `segment_bytes == 0` or `segment_s == 0`.
    pub fn new(
        flush_every: u32,
        segment_bytes: u64,
        segment_s: u64,
    ) -> Result<FlushPolicy, StoreError> {
        if flush_every == 0 {
            return Err(StoreError::Config {
                key: "flush_every",
                reason: "must be at least one job",
            });
        }
        if segment_bytes == 0 {
            return Err(StoreError::Config {
                key: "segment_bytes",
                reason: "must be at least one byte",
            });
        }
        if segment_s == 0 {
            return Err(StoreError::Config {
                key: "segment_s",
                reason: "must be at least one second",
            });
        }
        Ok(FlushPolicy {
            flush_every,
            segment_bytes,
            segment_s,
        })
    }

    /// True when a segment has reached its byte bound.
    #[must_use]
    pub const fn segment_full(&self, bytes: u64) -> bool {
        bytes >= self.segment_bytes
    }

    /// True when a segment has reached its time bound, given the segment's first and a
    /// candidate record's real time.
    #[must_use]
    pub fn segment_aged(&self, first_real_ns: i64, candidate_real_ns: i64) -> bool {
        let elapsed_ns = candidate_real_ns.saturating_sub(first_real_ns);
        let bound_ns = match self.segment_s.checked_mul(1_000_000_000) {
            Some(value) => i64::try_from(value).unwrap_or(i64::MAX),
            None => i64::MAX,
        };
        elapsed_ns >= bound_ns
    }

    /// The bucket a record's real time belongs to (its segment's start second), so a segment
    /// is named by an aligned time rather than by the first record's exact nanosecond.
    pub fn segment_start_s(real_ns: i64, segment_s: u64) -> Result<u64, StoreError> {
        if real_ns < 0 {
            return Err(StoreError::NegativeTime { ns: real_ns });
        }
        let seconds = u64::try_from(real_ns / 1_000_000_000)
            .map_err(|_| StoreError::NegativeTime { ns: real_ns })?;
        let bucket = seconds / segment_s.max(1);
        bucket
            .checked_mul(segment_s.max(1))
            .ok_or(StoreError::LengthOverflow)
    }
}

/// The bounded, single-writer job queue.
#[derive(Debug)]
pub struct WriterQueue {
    jobs: VecDeque<(Job, u64)>,
    bytes: u64,
    capacity: u64,
    accepted: u64,
    written: u64,
    dropped: u64,
    dropped_records: u64,
    dropped_bytes: u64,
}

impl WriterQueue {
    /// A queue bounded by `capacity` bytes.
    ///
    /// # Errors
    ///
    /// [`StoreError::Config`] for a capacity of zero (a queue that can hold nothing cannot
    /// carry the writer's work; a caller that wants no history disables the store instead).
    pub fn new(capacity: u64) -> Result<WriterQueue, StoreError> {
        if capacity == 0 {
            return Err(StoreError::Config {
                key: "writer_queue_bytes",
                reason: "must be at least one byte",
            });
        }
        Ok(WriterQueue {
            jobs: VecDeque::new(),
            bytes: 0,
            capacity,
            accepted: 0,
            written: 0,
            dropped: 0,
            dropped_records: 0,
            dropped_bytes: 0,
        })
    }

    /// The byte bound.
    #[must_use]
    pub const fn capacity(&self) -> u64 {
        self.capacity
    }

    /// The bytes currently queued.
    #[must_use]
    pub const fn bytes(&self) -> u64 {
        self.bytes
    }

    /// The jobs currently queued.
    #[must_use]
    pub fn len(&self) -> usize {
        self.jobs.len()
    }

    /// True when no job is queued.
    #[must_use]
    pub fn is_empty(&self) -> bool {
        self.jobs.is_empty()
    }

    /// The counters, as of now.
    #[must_use]
    pub const fn stats(&self) -> QueueStats {
        QueueStats {
            jobs: 0,
            bytes: self.bytes,
            capacity: self.capacity,
            accepted: self.accepted,
            written: self.written,
            dropped: self.dropped,
            dropped_records: self.dropped_records,
            dropped_bytes: self.dropped_bytes,
        }
    }

    /// The counters with the queue length filled in.
    ///
    /// `QueueStats` is `const fn`-friendly above, so the job count is reported separately: it
    /// is the one field that needs the allocation's own length.
    #[must_use]
    pub fn full_stats(&self) -> QueueStats {
        QueueStats {
            jobs: u64::try_from(self.jobs.len()).unwrap_or(u64::MAX),
            accepted: self.accepted,
            written: self.written,
            dropped: self.dropped,
            dropped_records: self.dropped_records,
            dropped_bytes: self.dropped_bytes,
            ..self.stats()
        }
    }

    /// Queue one job, dropping the **oldest** queued jobs to stay inside the bound.
    ///
    /// # Errors
    ///
    /// [`StoreError::JobTooLarge`] when the job alone exceeds the whole capacity.
    pub fn submit(&mut self, job: Job) -> Result<Submit, StoreError> {
        let bytes = job.bytes();
        if bytes > self.capacity {
            return Err(StoreError::JobTooLarge {
                len: bytes,
                max: self.capacity,
            });
        }
        let mut report = Submit {
            queued_bytes: bytes,
            ..Submit::default()
        };
        // Make room *before* the push, so the queue is never reported above its bound — a
        // bound that holds only after the next pop is not a bound.
        while let Some((oldest, oldest_bytes)) = self.jobs.front() {
            if self.bytes.saturating_add(bytes) <= self.capacity {
                break;
            }
            let _ = oldest;
            let dropped_bytes = *oldest_bytes;
            self.jobs.pop_front();
            self.bytes = self.bytes.saturating_sub(dropped_bytes);
            self.dropped = self.dropped.saturating_add(1);
            self.dropped_records = self.dropped_records.saturating_add(1);
            self.dropped_bytes = self.dropped_bytes.saturating_add(dropped_bytes);
            report.dropped_jobs = report.dropped_jobs.saturating_add(1);
            report.dropped_records = report.dropped_records.saturating_add(1);
            report.dropped_bytes = report.dropped_bytes.saturating_add(dropped_bytes);
        }
        self.jobs.push_back((job, bytes));
        self.bytes = self.bytes.saturating_add(bytes);
        self.accepted = self.accepted.saturating_add(1);
        Ok(report)
    }

    /// Take the oldest job for the one writer.
    ///
    /// The name is `take_next`, not `next`, so a caller cannot confuse it with
    /// `Iterator::next`: this pops a *bounded* queue's head and counts it as written.
    #[must_use]
    pub fn take_next(&mut self) -> Option<Job> {
        let (job, bytes) = self.jobs.pop_front()?;
        self.bytes = self.bytes.saturating_sub(bytes);
        self.written = self.written.saturating_add(1);
        Some(job)
    }

    /// Take every queued job (the shutdown drain the caller owns).
    #[must_use]
    pub fn drain(&mut self) -> Vec<Job> {
        let mut drained = Vec::with_capacity(self.jobs.len());
        while let Some(job) = self.take_next() {
            drained.push(job);
        }
        drained
    }
}

#[cfg(test)]
mod tests {
    use super::{FlushPolicy, Job, QueueStats, Submit, WriterQueue, JOB_OVERHEAD};
    use crate::error::StoreError;

    fn job(payload_len: usize) -> Job {
        Job::new(vec![0u8; payload_len], 1_000, vec![1, 2])
    }

    #[test]
    fn a_job_is_charged_for_what_it_holds() {
        let small = job(0);
        assert_eq!(small.bytes(), JOB_OVERHEAD + 16);
        assert_eq!(job(100).bytes(), JOB_OVERHEAD + 16 + 100);
        assert_eq!(
            Job::new(vec![1, 2, 3], 5, Vec::new()).bytes(),
            JOB_OVERHEAD + 3
        );
        assert_eq!(small.payload.len(), 0);
        assert_eq!(small.real_ns, 1_000);
    }

    #[test]
    fn the_queue_drops_the_oldest_job_first_and_counts_every_drop() {
        let mut queue = WriterQueue::new(3 * (JOB_OVERHEAD + 16)).expect("queue");
        assert!(queue.is_empty());
        assert_eq!(queue.capacity(), 3 * (JOB_OVERHEAD + 16));
        let report = queue.submit(job(0)).expect("submit");
        assert_eq!(
            report,
            Submit {
                queued_bytes: JOB_OVERHEAD + 16,
                ..Submit::default()
            }
        );
        assert!(!report.dropped_anything());
        assert_eq!(queue.len(), 1);
        queue.submit(job(0)).expect("submit");
        queue.submit(job(0)).expect("submit");
        assert_eq!(queue.len(), 3);
        assert_eq!(queue.bytes(), 3 * (JOB_OVERHEAD + 16));
        // A fourth job cannot fit: the oldest leaves and the submission says so.
        let report = queue.submit(job(0)).expect("submit");
        assert!(report.dropped_anything());
        assert_eq!(report.dropped_jobs, 1);
        assert_eq!(report.dropped_bytes, JOB_OVERHEAD + 16);
        assert_eq!(queue.len(), 3);
        assert!(queue.bytes() <= queue.capacity());
        let stats = queue.full_stats();
        assert_eq!(stats.accepted, 4);
        assert_eq!(stats.dropped, 1);
        assert_eq!(stats.dropped_records, 1);
        assert_eq!(stats.jobs, 3);
        assert!(!stats.lossless());
        assert_eq!(stats.fill(), 1.0);
        // The four jobs that remain are the newest three plus the one just pushed.
        assert_eq!(queue.take_next().map(|job| job.real_ns), Some(1_000));
        assert_eq!(queue.take_next().map(|job| job.real_ns), Some(1_000));
        assert_eq!(queue.len(), 1);
        assert_eq!(queue.full_stats().written, 2);
        assert!(queue.drain().len() == 1);
        assert!(queue.is_empty());
    }

    #[test]
    fn the_oldest_job_is_what_a_drop_removes_not_the_newest() {
        // Each job costs JOB_OVERHEAD + 8 (one id) + 8 (payload) = 80, so a 160-byte queue
        // holds exactly two of them and a third must evict the oldest.
        let capacity = 2 * (JOB_OVERHEAD + 16);
        let mut queue = WriterQueue::new(capacity).expect("queue");
        let old = Job::new(vec![1u8; 8], 10, vec![1]);
        let new = Job::new(vec![2u8; 8], 20, vec![2]);
        queue.submit(old).expect("submit old");
        queue.submit(new).expect("submit new");
        assert_eq!(queue.len(), 2);
        let third = Job::new(vec![3u8; 8], 30, vec![3]);
        let report = queue.submit(third).expect("submit third");
        assert_eq!(report.dropped_jobs, 1);
        assert_eq!(queue.len(), 2);
        // The *oldest* (real_ns 10) was dropped, so the queue's head is time 20.
        assert_eq!(queue.take_next().map(|job| job.real_ns), Some(20));
        assert_eq!(queue.take_next().map(|job| job.real_ns), Some(30));
        assert_eq!(queue.full_stats().dropped, 1);
        assert_eq!(queue.full_stats().dropped_bytes, JOB_OVERHEAD + 16);
    }

    #[test]
    fn a_job_larger_than_the_whole_queue_is_refused_rather_than_queued_and_dropped() {
        let exact = JOB_OVERHEAD + 16;
        let mut queue = WriterQueue::new(exact).expect("queue");
        assert_eq!(
            queue.submit(job(0)).map(|_| ()),
            Ok(()),
            "a job of exactly the capacity fits"
        );
        assert_eq!(
            queue.submit(job(1)).map(|_| ()),
            Err(StoreError::JobTooLarge {
                len: exact + 1,
                max: exact
            })
        );
        assert_eq!(queue.len(), 1, "the refused job changed nothing");
        assert_eq!(
            WriterQueue::new(0).map(|_| ()),
            Err(StoreError::Config {
                key: "writer_queue_bytes",
                reason: "must be at least one byte",
            })
        );
    }

    #[test]
    fn the_bound_holds_after_every_single_operation() {
        let capacity = 5 * (JOB_OVERHEAD + 16);
        let mut queue = WriterQueue::new(capacity).expect("queue");
        for round in 0..64u64 {
            let report = queue.submit(job(0)).expect("submit");
            assert!(
                queue.bytes() <= capacity,
                "round {round}: {} bytes in a {capacity}-byte queue",
                queue.bytes()
            );
            let stats = queue.full_stats();
            assert_eq!(
                stats.dropped,
                stats.accepted.saturating_sub(stats.jobs) - stats.written,
                "round {round}: every accepted job is queued, written or counted as dropped"
            );
            assert_eq!(
                stats.dropped,
                stats
                    .accepted
                    .saturating_sub(stats.jobs)
                    .saturating_sub(stats.written),
                "round {round}: every accepted job is accounted for"
            );
            assert!(
                report.dropped_jobs <= 1,
                "a push evicts at most one job when one fits"
            );
            if round % 17 == 0 {
                let _ = queue.take_next();
            }
        }
    }

    #[test]
    fn a_queue_that_never_overflows_is_lossless() {
        let mut queue = WriterQueue::new(1 << 20).expect("queue");
        for _ in 0..100 {
            let report = queue.submit(job(10)).expect("submit");
            assert!(!report.dropped_anything());
        }
        let stats = queue.full_stats();
        assert!(stats.lossless());
        assert_eq!(stats.dropped, 0);
        assert_eq!(stats.jobs, 100);
        assert_eq!(QueueStats::default().fill(), 0.0);
        let _ = queue.stats();
    }

    #[test]
    fn a_flush_policy_refuses_what_would_make_it_meaningless() {
        let policy = FlushPolicy::new(4, 1 << 20, 60).expect("policy");
        assert!(policy.segment_full(1 << 20));
        assert!(!policy.segment_full((1 << 20) - 1));
        assert!(!policy.segment_aged(0, 59_000_000_000));
        assert!(policy.segment_aged(0, 60_000_000_000));
        assert!(
            policy.segment_aged(i64::MIN, i64::MAX),
            "a saturating delta still fires"
        );
        assert_eq!(
            FlushPolicy::new(0, 1, 1).map(|_| ()),
            Err(StoreError::Config {
                key: "flush_every",
                reason: "must be at least one job",
            })
        );
        assert_eq!(
            FlushPolicy::new(1, 0, 1).map(|_| ()),
            Err(StoreError::Config {
                key: "segment_bytes",
                reason: "must be at least one byte",
            })
        );
        assert_eq!(
            FlushPolicy::new(1, 1, 0).map(|_| ()),
            Err(StoreError::Config {
                key: "segment_s",
                reason: "must be at least one second",
            })
        );
    }

    #[test]
    fn a_segment_start_is_aligned_to_its_own_step() {
        assert_eq!(FlushPolicy::segment_start_s(0, 60), Ok(0));
        assert_eq!(
            FlushPolicy::segment_start_s(119_000_000_000, 60),
            Ok(60),
            "a record at 119 s belongs to the segment aligned at 60 s"
        );
        assert_eq!(FlushPolicy::segment_start_s(120_000_000_000, 60), Ok(120));
        assert_eq!(
            FlushPolicy::segment_start_s(-1, 60),
            Err(StoreError::NegativeTime { ns: -1 })
        );
        assert_eq!(FlushPolicy::segment_start_s(5, 0), Ok(0));
    }
}
