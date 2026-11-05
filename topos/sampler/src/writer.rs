//! The writer side of the sampler (S-03): the sampling thread only reads and computes rates,
//! then hands a `Job` to this thread through a bounded queue. All disk and tmpfs IO (tiers,
//! dictionary, events) happens here, so a stalled disk delays the writer, never the ticks.
//!
//! When the writer falls behind and the queue is full, the OLDEST job loses its frame (counted
//! in `dropped_frames`); its dictionary delta and its events are merged into the next job, so
//! ids stay consistent and hard events are never dropped.
use crate::collect::Val;
use crate::dict::{Delta, Dict};
use crate::store::{Store, TierInfo};
use std::collections::VecDeque;
use std::panic::{catch_unwind, AssertUnwindSafe};
use std::sync::atomic::{AtomicU64, Ordering};
use std::sync::{Arc, Condvar, Mutex};
use std::time::Instant;

pub struct Job {
    pub ts_ms: u64,
    pub dt_ms: u64,
    pub frame: Arc<Vec<(u32, Vec<Val>)>>,
    /// Event JSON lines, without the newline.
    pub events: Vec<String>,
    pub delta: Delta,
}

#[derive(Clone, Default)]
pub struct WInfo {
    pub tiers: Vec<TierInfo>,
    pub tmpfs_bytes: u64,
    pub cold_bytes: u64,
    pub cold_other_bytes: u64,
    pub last_job_us: u64,
    pub written_ts_ms: u64,
}

struct Inner {
    q: VecDeque<Job>,
    closed: bool,
}

pub struct Shared {
    inner: Mutex<Inner>,
    cv: Condvar,
    cap: usize,
    pub dropped_frames: AtomicU64,
    pub submitted_ts_ms: AtomicU64,
    pub info: Mutex<WInfo>,
}

impl Shared {
    pub fn new(cap: usize) -> Arc<Shared> {
        Arc::new(Shared {
            inner: Mutex::new(Inner { q: VecDeque::new(), closed: false }),
            cv: Condvar::new(),
            cap: cap.max(2),
            dropped_frames: AtomicU64::new(0),
            submitted_ts_ms: AtomicU64::new(0),
            info: Mutex::new(WInfo::default()),
        })
    }

    /// Never blocks on the writer: at worst the oldest queued frame is dropped and counted.
    pub fn submit(&self, job: Job) {
        self.submitted_ts_ms.store(job.ts_ms, Ordering::SeqCst);
        let mut g = self.inner.lock().unwrap_or_else(|e| e.into_inner());
        g.q.push_back(job);
        while g.q.len() > self.cap {
            let old = g.q.pop_front().unwrap();
            self.dropped_frames.fetch_add(1, Ordering::SeqCst);
            if let Some(next) = g.q.front_mut() {
                // the frame is lost; the dictionary ids and the events it carried are not
                next.delta.prepend(old.delta);
                let mut ev = old.events;
                ev.append(&mut next.events);
                next.events = ev;
            }
        }
        drop(g);
        self.cv.notify_one();
    }

    pub fn queue_len(&self) -> u64 {
        self.inner.lock().unwrap_or_else(|e| e.into_inner()).q.len() as u64
    }

    pub fn close(&self) {
        self.inner.lock().unwrap_or_else(|e| e.into_inner()).closed = true;
        self.cv.notify_all();
    }

    pub fn next(&self) -> Option<Job> {
        let mut g = self.inner.lock().unwrap_or_else(|e| e.into_inner());
        loop {
            if let Some(j) = g.q.pop_front() {
                return Some(j);
            }
            if g.closed {
                return None;
            }
            g = self.cv.wait(g).unwrap_or_else(|e| e.into_inner());
        }
    }

    pub fn info(&self) -> WInfo {
        self.info.lock().unwrap_or_else(|e| e.into_inner()).clone()
    }

    /// Milliseconds of data between the newest submitted tick and the newest written one.
    pub fn lag_ms(&self) -> u64 {
        let w = self.info.lock().unwrap_or_else(|e| e.into_inner()).written_ts_ms;
        self.submitted_ts_ms.load(Ordering::SeqCst).saturating_sub(w)
    }
}

/// Persist one job: dictionary first (fsynced), then the frames that may reference its ids.
pub fn process(store: &mut Store, mirror: &mut Dict, job: &Job) {
    mirror.apply(&job.delta);
    let synced = store.sync_dict(mirror);
    store.append_events(&job.events);
    if synced {
        store.push(&job.frame, job.ts_ms, job.dt_ms, mirror);
    } else {
        crate::store::CTR.frames_skipped.fetch_add(1, Ordering::Relaxed);
    }
}

fn publish(sh: &Shared, store: &Store, ts_ms: u64, us: u64) {
    let (tm, cold) = store.sizes();
    let w = WInfo { tiers: store.tier_info(), tmpfs_bytes: tm, cold_bytes: cold, cold_other_bytes: store.other_bytes(), last_job_us: us, written_ts_ms: ts_ms };
    *sh.info.lock().unwrap_or_else(|e| e.into_inner()) = w;
}

pub fn spawn(sh: Arc<Shared>, mut store: Store, mut mirror: Dict) -> std::io::Result<std::thread::JoinHandle<()>> {
    std::thread::Builder::new().name("writer".into()).spawn(move || {
        while let Some(job) = sh.next() {
            let t = Instant::now();
            let r = catch_unwind(AssertUnwindSafe(|| process(&mut store, &mut mirror, &job)));
            if r.is_err() {
                crate::store::note_error("writer job panicked".into());
            }
            publish(&sh, &store, job.ts_ms, t.elapsed().as_micros() as u64);
        }
    })
}
