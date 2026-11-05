//! `/proc/<pid>/cmdline` is read off the sampling thread (S-03). Reading it takes the target's
//! mmap lock, so for a task stuck in D state the read can block for as long as the stall lasts.
//! A small pool of workers does the reads; the sampler asks, never waits, and shows the
//! command line as unavailable until an answer is in (or after a deadline, as a timeout).
//! A stuck worker stays stuck (the kernel call is not interruptible) but costs only its
//! thread; the queue in front of the pool is bounded and refuses requests when it is full.
//! At most one read per pid is in flight (X-06): a pid whose read is stuck is not asked again,
//! so one stuck task can never occupy more than one worker.
//!
//! Command lines are redacted before they leave this module (`redact`): values of options that
//! look like secrets and `KEY=VALUE` words whose key looks secret are replaced by `<redacted>`.
use std::collections::{HashMap, HashSet};
use std::io::Read;
use std::sync::mpsc::{sync_channel, Receiver, SyncSender, TrySendError};
use std::sync::{Arc, Mutex};

pub const WORKERS: usize = 3;
pub const QUEUE: usize = 64;

type Key = (u32, u64);
type Answers = Arc<Mutex<HashMap<Key, Result<String, u8>>>>;
type InFlight = Arc<Mutex<HashSet<u32>>>;

struct Req {
    key: Key,
    path: String,
    max: usize,
}

pub struct CmdWorkers {
    tx: SyncSender<Req>,
    answers: Answers,
    inflight: InFlight,
}

// ---------------------------------------------------------------- redaction

pub const REDACTED: &str = "<redacted>";

/// Words that make an option name (`--db-password`) or an environment-style key (`API_TOKEN`)
/// look secret. Matched case-insensitively as substrings of the name.
const SECRET_WORDS: &[&str] = &[
    "password", "passwd", "passphrase", "pwd", "secret", "token", "apikey", "api-key", "api_key", "credential", "private-key", "private_key",
    "access-key", "access_key", "secret-key", "authorization", "cookie", "bearer", "psw",
];

/// Words containing "pass" that are not about a credential. Anything else containing "pass" is
/// treated as secret: hiding a harmless value costs little, showing a password does not.
const PASS_NOT_SECRET: &[&str] = &["bypass", "compass", "passive", "passthrough", "pass-through", "passenger", "encompass", "surpass"];

fn looks_secret(name: &str) -> bool {
    let n = name.trim_start_matches(['-', '+']).to_ascii_lowercase();
    if n.is_empty() {
        return false;
    }
    // `--password-file /path`, `--token-dir`, `--no-password`: the name mentions a secret, the value is not one
    const NOT_THE_SECRET_SUFFIX: &[&str] = &["-file", "_file", "-path", "_path", "-dir", "_dir", "-env", "_env", "-url", "_url"];
    const NOT_THE_SECRET_PREFIX: &[&str] = &["no-", "no_", "skip-", "skip_", "without-", "disable-", "disable_"];
    if NOT_THE_SECRET_SUFFIX.iter().any(|s| n.ends_with(s)) || NOT_THE_SECRET_PREFIX.iter().any(|p| n.starts_with(p)) {
        return false;
    }
    // KEY=VALUE style keys: also anything ending in "key" or containing "pass" (`rconpass`, `adminpass`)
    let pass = n.contains("pass") && !PASS_NOT_SECRET.iter().any(|w| n.contains(w));
    SECRET_WORDS.iter().any(|w| n.contains(w)) || pass || n.ends_with("_key") || n.ends_with("-key") || n == "key"
}

/// `-H "Authorization: Bearer x"` and `Cookie: a=b`: one word holding a secret header.
fn redact_header(a: &str) -> Option<String> {
    let (name, _) = a.split_once(':')?;
    let n = name.trim().to_ascii_lowercase();
    if n.contains(' ') || !(n.contains("authorization") || n.contains("cookie") || n.ends_with("-token") || n.ends_with("-key")) {
        return None;
    }
    Some(format!("{}: {REDACTED}", name.trim()))
}

/// `scheme://user:password@host` -> `scheme://user:<redacted>@host`
fn redact_url(a: &str) -> Option<String> {
    let i = a.find("://")?;
    let rest = &a[i + 3..];
    let end = rest.find(['/', '?', '#']).unwrap_or(rest.len());
    let auth = &rest[..end];
    let at = auth.rfind('@')?;
    let (user, _) = auth[..at].split_once(':')?;
    Some(format!("{}://{user}:{REDACTED}@{}", &a[..i], &rest[at + 1..]))
}

/// One word. Returns (replacement, true when the NEXT word is this option's value).
fn redact_word(a: &str) -> (String, bool) {
    if let Some(u) = redact_url(a) {
        return (u, false);
    }
    if let Some(h) = redact_header(a) {
        return (h, false);
    }
    // `name=value` (with or without leading dashes)
    if let Some((k, v)) = a.split_once('=') {
        if looks_secret(k) {
            return (format!("{k}={REDACTED}"), false);
        }
        // `--env=DB_PASSWORD=x`, `--set key.token=x`: the value itself may be a pair
        if v.contains('=') {
            let (inner, _) = redact_word(v);
            if inner != v {
                return (format!("{k}={inner}"), false);
            }
        }
        return (a.to_string(), false);
    }
    // `--password hunter2`, `+rcon_password hunter2`: the next word is the value
    (a.to_string(), (a.starts_with('-') || a.starts_with('+')) && looks_secret(a))
}

/// Redact secrets in the NUL-separated words of a command line.
pub fn redact(words: &[&str]) -> Vec<String> {
    let mut out = Vec::with_capacity(words.len());
    let mut hide_next = false;
    let mut user_next = false;
    for w in words {
        if hide_next {
            hide_next = false;
            // `--password --verbose`: the next word is another option, not a value
            if !w.starts_with('-') {
                out.push(REDACTED.to_string());
                continue;
            }
        }
        // `-u user:password`, as curl takes it
        if std::mem::take(&mut user_next) {
            if let Some((user, _)) = w.split_once(':') {
                out.push(format!("{user}:{REDACTED}"));
                continue;
            }
        }
        user_next = *w == "-u" || *w == "--user";
        if let Some(v) = w.strip_prefix("--user=") {
            if let Some((user, _)) = v.split_once(':') {
                out.push(format!("--user={user}:{REDACTED}"));
                continue;
            }
        }
        let (r, next) = redact_word(w);
        hide_next = next;
        out.push(r);
    }
    out
}

#[cfg(test)]
mod redaction_tests {
    use super::*;

    fn r(words: &[&str]) -> String {
        redact(words).join(" ")
    }

    #[test]
    fn game_server_arguments_are_hidden() {
        let line = r(&["WSServer-Linux-Shipping", "Level01_Main", "-PSW=hunter2", "-adminpsw=hunter3", "-log"]);
        assert!(!line.contains("hunter"), "{line}");
        assert!(line.contains("Level01_Main") && line.contains("-log"), "{line}");
        let line = r(&["srcds", "+rcon_password", "hunter2", "+map", "de_dust"]);
        assert!(!line.contains("hunter"), "{line}");
        assert!(line.contains("+map de_dust"), "{line}");
        let line = r(&["server", "Map?listen?ServerPassword=hunter2?ServerAdminPassword=hunter3"]);
        assert!(!line.contains("hunter"), "{line}");
        assert!(!r(&["x", "-rconpass", "hunter2"]).contains("hunter"));
    }

    #[test]
    fn headers_and_user_pairs_are_hidden() {
        let line = r(&["curl", "-H", "Authorization: Bearer hunter2", "-u", "bob:hunter3", "--user=amy:hunter4", "http://x/"]);
        assert!(!line.contains("hunter"), "{line}");
        assert!(line.contains("bob:") && line.contains("amy:") && line.contains("http://x/"), "{line}");
    }

    #[test]
    fn ordinary_words_stay_readable() {
        let line = r(&["app", "--passive", "--bypass-cache", "-H", "Accept: text/plain", "-u", "root", "--compass=north", "a:b"]);
        assert_eq!(line, "app --passive --bypass-cache -H Accept: text/plain -u root --compass=north a:b");
    }
}

fn read_cmdline(path: &str, max: usize) -> Result<String, u8> {
    use crate::util::{err_state, stc};
    let f = std::fs::File::open(path).map_err(|e| err_state(&e, stc::UN_READ))?;
    let mut b = Vec::new();
    f.take(max as u64).read_to_end(&mut b).map_err(|e| err_state(&e, stc::UN_READ))?;
    Ok(render(&b))
}

/// Raw cmdline bytes (NUL-separated) to the redacted, space-joined text.
pub fn render(b: &[u8]) -> String {
    let text = String::from_utf8_lossy(b);
    let words: Vec<&str> = text.split('\0').collect();
    // a trailing NUL leaves one empty word
    let words: Vec<&str> = if words.last() == Some(&"") { words[..words.len() - 1].to_vec() } else { words };
    redact(&words).join(" ").trim_end().to_string()
}

impl CmdWorkers {
    pub fn new() -> CmdWorkers {
        let (tx, rx) = sync_channel::<Req>(QUEUE);
        let rx: Arc<Mutex<Receiver<Req>>> = Arc::new(Mutex::new(rx));
        let answers: Answers = Arc::new(Mutex::new(HashMap::new()));
        let inflight: InFlight = Arc::new(Mutex::new(HashSet::new()));
        for i in 0..WORKERS {
            let (rx, answers, inflight) = (rx.clone(), answers.clone(), inflight.clone());
            let _ = std::thread::Builder::new().name(format!("cmdline-{i}")).stack_size(256 * 1024).spawn(move || loop {
                let req = match rx.lock().unwrap_or_else(|e| e.into_inner()).recv() {
                    Ok(r) => r,
                    Err(_) => return,
                };
                let res = read_cmdline(&req.path, req.max);
                answers.lock().unwrap_or_else(|e| e.into_inner()).insert(req.key, res);
                inflight.lock().unwrap_or_else(|e| e.into_inner()).remove(&req.key.0);
            });
        }
        CmdWorkers { tx, answers, inflight }
    }

    /// False when the queue is full or a read for this pid is already in flight (the caller
    /// asks again on a later tick, after its own backoff).
    pub fn ask(&self, pid: u32, start: u64, path: String, max: usize) -> bool {
        {
            let mut g = self.inflight.lock().unwrap_or_else(|e| e.into_inner());
            if !g.insert(pid) {
                return false;
            }
        }
        match self.tx.try_send(Req { key: (pid, start), path, max }) {
            Ok(()) => true,
            Err(TrySendError::Full(_)) | Err(TrySendError::Disconnected(_)) => {
                self.inflight.lock().unwrap_or_else(|e| e.into_inner()).remove(&pid);
                false
            }
        }
    }

    /// Pids with a read in flight (queued or running).
    #[cfg(test)]
    pub fn in_flight(&self) -> usize {
        self.inflight.lock().unwrap_or_else(|e| e.into_inner()).len()
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
