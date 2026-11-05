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
    // names that mention a secret word but hold no secret
    const COUNT_ENDINGS: &[&str] = &["max-tokens", "num-tokens", "n-tokens", "batched-tokens", "max_tokens", "num_tokens", "n_tokens", "batched_tokens"];
    // the ending must be a whole part of the name (`--admin-tokens` ends in `n-tokens` and is a secret),
    // and what comes before it is judged on its own: `--auth-max-tokens` is a secret,
    // `--max-num-batched-tokens` is a count
    for s in COUNT_ENDINGS {
        if let Some(rest) = n.strip_suffix(s) {
            if rest.is_empty() {
                return false;
            }
            if rest.ends_with(['-', '_']) {
                return looks_secret(rest.trim_end_matches(['-', '_']));
            }
        }
    }
    // `tokenizer` is not a secret word, but the rest of the name may hold one: `--tokenizer-secret`,
    // `HF_TOKENIZER_API_KEY`. The name gets shorter on each call, so this ends.
    if n.contains("tokenizer") {
        return looks_secret(&n.replace("tokenizer", ""));
    }
    if n.contains("password-stdin") {
        return false;
    }
    // whole parts, not substrings: `-pw`, `--auth` are secret, `--author`, `--authority` are not
    if n.split(['-', '_']).any(|p| matches!(p, "pw" | "auth" | "creds" | "credential" | "credentials")) {
        return true;
    }
    // KEY=VALUE style keys: also anything ending in "key" or containing "pass" (`rconpass`, `adminpass`)
    let pass = n.contains("pass") && !PASS_NOT_SECRET.iter().any(|w| n.contains(w));
    SECRET_WORDS.iter().any(|w| n.contains(w)) || pass || n.ends_with("_key") || n.ends_with("-key") || n == "key"
}

/// `-H "Authorization: Bearer x"` and `Cookie: a=b`: one word holding a secret header.
fn redact_header(a: &str) -> Option<String> {
    let (name, _) = a.split_once(':')?;
    // `--password=abc:def`, `SECRET_KEY=a:b`: a secret `name=` prefix is the `name=value` branch's
    // business (it hides the whole value); with another prefix (`--header=`) only the rest is judged
    let judged = match name.split_once('=') {
        Some((pre, after)) => {
            if looks_secret(pre) {
                return None;
            }
            after
        }
        None => name,
    };
    let n = judged.trim().to_ascii_lowercase();
    let secret = matches!(n.as_str(), "authorization" | "authentication" | "cookie" | "set-cookie")
        || n.ends_with("-token")
        || n.ends_with("-key")
        || looks_secret(&n);
    if n.contains(' ') || n.is_empty() || !secret {
        return None;
    }
    Some(format!("{}: {REDACTED}", name.trim()))
}

/// `scheme://user:password@host` -> `scheme://user:<redacted>@host`, and a userinfo without a
/// colon (a bare token) is hidden whole: `https://<redacted>@host`. Every `://` in the word counts.
fn redact_url(a: &str) -> Option<String> {
    let mut out = String::new();
    let mut pos = 0;
    let mut changed = false;
    while let Some(off) = a[pos..].find("://") {
        let start = pos + off + 3;
        let rest = &a[start..];
        let end = rest.find(['/', '?', '#', ',']).unwrap_or(rest.len());
        let auth = &rest[..end];
        out.push_str(&a[pos..start]);
        pos = start;
        if let Some(at) = auth.rfind('@') {
            let info = &auth[..at];
            match info.split_once(':') {
                Some((user, _)) => {
                    out.push_str(&format!("{user}:{REDACTED}"));
                    changed = true;
                }
                // a short plain login (`git@host`) is not a secret; anything else is hidden
                None if (1..=16).contains(&info.len()) && info.bytes().all(|b| matches!(b, b'a'..=b'z' | b'0'..=b'9' | b'.' | b'_' | b'-')) => out.push_str(info),
                None => {
                    out.push_str(REDACTED);
                    changed = true;
                }
            }
            pos = start + at;
        }
    }
    out.push_str(&a[pos..]);
    changed.then_some(out)
}

/// `user:password` as `-u` / `--user` / `--proxy-user` take it. `None` when it is visible anyway:
/// both sides digits (`docker run -u 1000:1000`) and the program is a container runtime
/// (`ids_ok`). Identical sides are NOT exempt: `admin:admin` is a password.
fn redact_user_pair(v: &str, ids_ok: bool) -> Option<String> {
    let (user, pw) = v.split_once(':')?;
    let digits = |s: &str| !s.is_empty() && s.bytes().all(|b| b.is_ascii_digit());
    if ids_ok && digits(user) && digits(pw) {
        return None;
    }
    Some(format!("{user}:{REDACTED}"))
}

/// In a word cut at `?`, `&`, `;` and `,`, a segment `name=value` whose name (before the last
/// `=`) looks secret loses its value: `https://x/y?access_token=abc&a=1`.
fn redact_pairs(a: &str) -> String {
    let mut out = String::with_capacity(a.len());
    let mut seg_start = 0;
    let flush = |out: &mut String, seg: &str| match seg.rfind('=') {
        Some(i) if looks_secret(&seg[..i]) => {
            out.push_str(&seg[..=i]);
            out.push_str(REDACTED);
        }
        _ => out.push_str(seg),
    };
    for (i, c) in a.char_indices() {
        if matches!(c, '?' | '&' | ';' | ',') {
            flush(&mut out, &a[seg_start..i]);
            out.push(c);
            seg_start = i + c.len_utf8();
        }
    }
    flush(&mut out, &a[seg_start..]);
    out
}

/// One word. Returns (replacement, true when the NEXT word is this option's value).
fn redact_word(a: &str) -> (String, bool) {
    if a.contains("://") {
        // a URL word is never finished by the userinfo pass: query pairs and nested pairs follow
        let u = redact_url(a);
        let p = redact_pairs(u.as_deref().unwrap_or(a));
        if p != a {
            return (p, false);
        }
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
    // numeric `uid:gid` pairs are only known not to be passwords for container runtimes
    let prog = words.first().map(|p| p.rsplit('/').next().unwrap_or(p)).unwrap_or("");
    let ids_ok = matches!(prog, "docker" | "podman" | "nerdctl" | "runc" | "crun");
    'words: for w in words {
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
            if let Some(p) = redact_user_pair(w, ids_ok) {
                out.push(p);
                continue;
            }
        }
        user_next = matches!(*w, "-u" | "--user" | "--proxy-user");
        for pre in ["--user=", "--proxy-user="] {
            if let Some(p) = w.strip_prefix(pre).and_then(|v| redact_user_pair(v, ids_ok)) {
                out.push(format!("{pre}{p}"));
                continue 'words;
            }
        }
        // attached form `-ubob:pw`
        if w.starts_with("-u") && !w.starts_with("--") {
            if let Some(p) = redact_user_pair(&w[2..], ids_ok) {
                out.push(format!("-u{p}"));
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
    fn a_cut_line_never_shows_half_a_secret() {
        let full = b"psql\0postgres://admin:hunter2@db/x\0-v\0";
        // cut inside the password: the partial word is replaced
        let cut = render(&cut_to_word(full.to_vec(), 26));
        assert_eq!(cut, "psql <truncated>");
        // a single word longer than the cap is just the marker
        assert_eq!(render(&cut_to_word(b"postgres://admin:hunter2@db".to_vec(), 10)), "<truncated>");
        // a line that fits exactly is untouched (cap + 1 bytes are read to tell)
        assert_eq!(render(&cut_to_word(b"a\0b\0".to_vec(), 4)), "a b");
        // and a cut after a complete word keeps it, redacted as usual
        assert_eq!(render(&cut_to_word(b"x\0--password\0hunter2\0y".to_vec(), 14)), "x --password <redacted>");
    }

    #[test]
    fn every_url_in_a_word_and_bare_tokens_are_hidden() {
        let line = r(&["--urls=http://a:p1@x,http://b:p2@y"]);
        assert_eq!(line, "--urls=http://a:<redacted>@x,http://b:<redacted>@y");
        assert_eq!(r(&["https://ghp_TOKEN@github.com/o/r"]), "https://<redacted>@github.com/o/r");
        // no userinfo, and an `@` only in the path, stay as they are
        assert_eq!(r(&["https://github.com/o/r", "https://h/p@q"]), "https://github.com/o/r https://h/p@q");
    }

    #[test]
    fn secret_headers_by_name() {
        for h in ["Token: x", "X-Secret: x", "Authentication: Basic y", "Apikey: x", "Cookie: a=b", "Set-Cookie: a=b", "X-Auth-Token: x", "X-Api-Key: x", "Authorization: Bearer x"] {
            let line = r(&["curl", "-H", h]);
            assert!(line.ends_with(": <redacted>"), "{h} -> {line}");
        }
        assert_eq!(r(&["-H", "Accept: text/plain", "-H", "Content-Type: json"]), "-H Accept: text/plain -H Content-Type: json");
    }

    #[test]
    fn option_names_are_judged_by_whole_parts() {
        assert_eq!(r(&["-pw", "hunter2"]), "-pw <redacted>");
        assert_eq!(r(&["--auth", "user:pw"]), "--auth <redacted>");
        assert_eq!(r(&["--db_creds", "x", "--credentials=y"]), "--db_creds <redacted> --credentials=<redacted>");
        assert_eq!(r(&["--author", "me", "--authority", "x"]), "--author me --authority x");
        assert_eq!(r(&["-pass", "2"]), "-pass <redacted>");
    }

    #[test]
    fn user_forms_hide_the_password_unless_it_is_an_id_pair() {
        assert_eq!(r(&["--proxy-user", "bob:pw"]), "--proxy-user bob:<redacted>");
        assert_eq!(r(&["--proxy-user=bob:pw"]), "--proxy-user=bob:<redacted>");
        assert_eq!(r(&["-ubob:pw"]), "-ubob:<redacted>");
        assert_eq!(r(&["-u", "bob:pw"]), "-u bob:<redacted>");
        assert_eq!(r(&["-u", "1000:pw"]), "-u 1000:<redacted>");
        // identical sides are a password, not an exemption
        assert_eq!(r(&["curl", "-u", "admin:admin", "--user=www-data:www-data"]), "curl -u admin:<redacted> --user=www-data:<redacted>");
        // all-digit pairs are visible only for container runtimes (basename of argv[0])
        assert_eq!(r(&["docker", "run", "-u", "1000:1000", "--user=0:0", "-u1000:1001"]), "docker run -u 1000:1000 --user=0:0 -u1000:1001");
        assert_eq!(r(&["/usr/bin/podman", "run", "-u", "1000:1000"]), "/usr/bin/podman run -u 1000:1000");
        assert_eq!(r(&["curl", "-u", "100:654321"]), "curl -u 100:<redacted>");
        assert_eq!(r(&["docker", "run", "-u", "bob:654321"]), "docker run -u bob:<redacted>");
    }

    #[test]
    fn secret_prefixed_pairs_with_a_colon_are_hidden_whole() {
        for w in ["--password=abc:def", "SECRET_KEY=a:b", "--token=ghp:xyz", "DB_PASSWORD=user:pw"] {
            let out = r(&["x", w]);
            let (k, _) = w.split_once('=').unwrap();
            assert_eq!(out, format!("x {k}=<redacted>"), "{w}");
        }
        // a non-secret prefix: only the part after `=` is judged
        assert_eq!(r(&["curl", "--header=Authorization: Bearer x"]), "curl --header=Authorization: <redacted>");
        assert_eq!(r(&["x", "--note=a:b"]), "x --note=a:b");
    }

    #[test]
    fn token_exemptions_are_exact_count_names() {
        assert_eq!(r(&["--max-tokens", "4096", "--max-num-batched-tokens", "8192", "--n_tokens=3"]), "--max-tokens 4096 --max-num-batched-tokens 8192 --n_tokens=3");
        assert_eq!(r(&["--access-tokens=a,b"]), "--access-tokens=<redacted>");
        assert_eq!(r(&["--admin-tokens=abc", "--session-tokens", "abc"]), "--admin-tokens=<redacted> --session-tokens <redacted>");
        assert_eq!(r(&["SESSION_TOKENS=abc", "--xmax-tokens=abc"]), "SESSION_TOKENS=<redacted> --xmax-tokens=<redacted>");
        // a secret word before the count ending wins
        assert_eq!(r(&["--auth-max-tokens=abc", "AUTH_N_TOKENS=abc"]), "--auth-max-tokens=<redacted> AUTH_N_TOKENS=<redacted>");
        assert_eq!(r(&["--password-num-tokens", "abc"]), "--password-num-tokens <redacted>");
        // `tokenizer` itself is no secret, the rest of the name is still judged
        assert_eq!(r(&["--tokenizer-secret=abc", "HF_TOKENIZER_API_KEY=abc"]), "--tokenizer-secret=<redacted> HF_TOKENIZER_API_KEY=<redacted>");
        assert_eq!(r(&["--tokenizer-password", "abc", "--tokenizer-name", "bert"]), "--tokenizer-password <redacted> --tokenizer-name bert");
        assert_eq!(r(&["--auth-tokens=abc"]), "--auth-tokens=<redacted>");
        assert_eq!(r(&["API_TOKENS=abc"]), "API_TOKENS=<redacted>");
        assert_eq!(r(&["--valid-tokens", "abc"]), "--valid-tokens <redacted>");
    }

    #[test]
    fn bare_userinfo_is_visible_only_when_a_short_login() {
        assert_eq!(r(&["ssh://git@github.com/o/r", "sftp://user@host"]), "ssh://git@github.com/o/r sftp://user@host");
        assert_eq!(r(&["https://ghp_TOKEN@github.com/o/r"]), "https://<redacted>@github.com/o/r");
        assert_eq!(r(&["https://abcdefghijklmnopq@h"]), "https://<redacted>@h", "17 characters is too long for a login");
        assert_eq!(r(&["https://Git@h"]), "https://<redacted>@h", "upper case is outside the login alphabet");
    }

    #[test]
    fn nested_secret_pairs_follow_a_url() {
        assert_eq!(r(&["https://x/y?access_token=abc&a=1"]), "https://x/y?access_token=<redacted>&a=1");
        assert_eq!(r(&["--args=token=abc&u=http://x:y@h"]), "--args=token=<redacted>&u=http://x:<redacted>@h");
        assert_eq!(r(&["https://u:p@h/p?sig=1&password=2;ok=3"]), "https://u:<redacted>@h/p?sig=1&password=<redacted>;ok=3");
        assert_eq!(r(&["https://x/y?a=1&b=2"]), "https://x/y?a=1&b=2");
    }

    #[test]
    fn a_line_of_exactly_the_cap_plus_its_nul_is_not_cut() {
        assert_eq!(render(&cut_to_word(b"abc\0de\0".to_vec(), 6)), "abc de");
        assert_eq!(render(&cut_to_word(b"abc\0de\0f".to_vec(), 6)), "abc <truncated>");
    }

    #[test]
    fn names_that_only_mention_a_secret_stay_visible() {
        assert_eq!(r(&["--tokenizer", "bert", "--max-tokens", "5", "--num_tokens=3", "--password-stdin"]), "--tokenizer bert --max-tokens 5 --num_tokens=3 --password-stdin");
        assert_eq!(r(&["--password", "x"]), "--password <redacted>");
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
    // two bytes more than the cap tell a cut from a line that fits exactly (cap bytes + its NUL)
    f.take(max as u64 + 2).read_to_end(&mut b).map_err(|e| err_state(&e, stc::UN_READ))?;
    Ok(render(&cut_to_word(b, max)))
}

pub const TRUNCATED: &str = "<truncated>";

/// A line longer than `max` is cut back to its last complete word (the last NUL within the cap)
/// and the partial word is replaced by `<truncated>`, so redaction never sees half a secret
/// (`postgres://admin:hunter2` cut before the `@`). One word longer than the cap becomes just
/// `<truncated>`.
fn cut_to_word(mut b: Vec<u8>, max: usize) -> Vec<u8> {
    // a complete line of `max` bytes plus its closing NUL is not a cut
    let content = if b.last() == Some(&0) { b.len() - 1 } else { b.len() };
    if content <= max {
        return b;
    }
    b.truncate(max);
    let keep = b.iter().rposition(|&c| c == 0).map_or(0, |p| p + 1);
    b.truncate(keep);
    b.extend_from_slice(TRUNCATED.as_bytes());
    b
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
