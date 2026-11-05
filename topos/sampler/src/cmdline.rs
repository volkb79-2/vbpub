//! `/proc/<pid>/cmdline` is read off the sampling thread (S-03). Reading it takes the target's
//! mmap lock, so for a task stuck in D state the read can block for as long as the stall lasts.
//! A small pool of workers does the reads; the sampler asks, never waits, and shows the
//! command line as unavailable until an answer is in (or after a deadline, as a timeout).
//! A stuck worker stays stuck (the kernel call is not interruptible) but costs only its
//! thread; the queue in front of the pool is bounded and refuses requests when it is full.
use std::collections::HashMap;
use std::io::Read;
use std::sync::mpsc::{sync_channel, Receiver, SyncSender, TrySendError};
use std::sync::{Arc, Mutex};

pub const WORKERS: usize = 3;
pub const QUEUE: usize = 64;

type Key = (u32, u64);
type Answers = Arc<Mutex<HashMap<Key, Result<String, u8>>>>;

struct Req {
    key: Key,
    path: String,
    max: usize,
}

pub struct CmdWorkers {
    tx: SyncSender<Req>,
    answers: Answers,
}

fn read_cmdline(path: &str, max: usize) -> Result<String, u8> {
    use crate::util::{err_state, stc};
    let f = std::fs::File::open(path).map_err(|e| err_state(&e, stc::UN_READ))?;
    let mut b = Vec::new();
    f.take(max as u64).read_to_end(&mut b).map_err(|e| err_state(&e, stc::UN_READ))?;
    let s: Vec<u8> = b.iter().map(|&c| if c == 0 { b' ' } else { c }).collect();
    Ok(String::from_utf8_lossy(&s).trim_end().to_string())
}

impl CmdWorkers {
    pub fn new() -> CmdWorkers {
        let (tx, rx) = sync_channel::<Req>(QUEUE);
        let rx: Arc<Mutex<Receiver<Req>>> = Arc::new(Mutex::new(rx));
        let answers: Answers = Arc::new(Mutex::new(HashMap::new()));
        for i in 0..WORKERS {
            let (rx, answers) = (rx.clone(), answers.clone());
            let _ = std::thread::Builder::new().name(format!("cmdline-{i}")).stack_size(256 * 1024).spawn(move || loop {
                let req = match rx.lock().unwrap_or_else(|e| e.into_inner()).recv() {
                    Ok(r) => r,
                    Err(_) => return,
                };
                let res = read_cmdline(&req.path, req.max);
                answers.lock().unwrap_or_else(|e| e.into_inner()).insert(req.key, res);
            });
        }
        CmdWorkers { tx, answers }
    }

    /// False when the queue is full (the caller retries on a later tick).
    pub fn ask(&self, pid: u32, start: u64, path: String, max: usize) -> bool {
        match self.tx.try_send(Req { key: (pid, start), path, max }) {
            Ok(()) => true,
            Err(TrySendError::Full(_)) | Err(TrySendError::Disconnected(_)) => false,
        }
    }

    /// Answers that arrived since the last call.
    pub fn take_answers(&self) -> Vec<(Key, Result<String, u8>)> {
        let mut g = self.answers.lock().unwrap_or_else(|e| e.into_inner());
        g.drain().collect()
    }
}

/// Synchronous read, for tests and for the `--once` single-shot path.
pub fn read_now(path: &str, max: usize) -> Result<String, u8> {
    read_cmdline(path, max)
}
