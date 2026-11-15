//! `/proc/<pid>/cmdline`: bounded read, word-boundary truncation, redaction.
//!
//! Ported from `topos/sampler/src/cmdline.rs` (read-only: the worker pool and the
//! request/answer channel are *not* ported, because the kernel crate does no I/O beyond
//! [`Fs`] and owns no threads; the caller decides how to schedule the reads and what to do
//! when one stalls — the sampler's answer was a three-worker pool with a 64-slot queue and
//! one in-flight read per pid, which is a scheduling decision, not a reading).
//!
//! What *is* ported, because it is the part with the privacy contract:
//!
//! - [`CmdlineBound`] — the sampler's default cap (`cmdline_max`, 256 bytes) and the two extra
//!   bytes read to tell "exactly full" from "cut".
//! - [`cut_to_word`] — a line longer than the cap is cut at the last complete NUL within
//!   the cap, and the partial word becomes [`TRUNCATED`], so redaction never sees half
//!   a secret (`postgres://admin:hunter2` cut before its `@`).
//! - [`redact`] and [`render`] — the sampler's redaction rules, unchanged in behaviour:
//!   secret-looking option names, `KEY=VALUE` words, `Authorization`/`Cookie`-style
//!   headers, `scheme://user:password@host` userinfo, `-u user:password` and
//!   `--user=`/`-uuser:pw` forms, and `?access_token=…` query pairs.
//!
//! # The three rules that make this a *reader*, not a re-encoder
//!
//! 1. **The cap is a bound, not a default.** [`ReadCmdline`] states the cap it applied and
//!    whether the line was cut, so a consumer of this crate can record "the command line was
//!    truncated" instead of assuming the text is complete.
//! 2. **A cut is at a word boundary, never mid-word.** That is the sampler's fix for a
//!    line cut inside a password: half a secret would not be recognised by the redactor
//!    (the `user:password@` shape is gone), so it would be shown in full.
//! 3. **A refusal is a typed value, never an empty string.** A process that exits between
//!    `dirent` and `open`, a permission failure and an over-long file are three different
//!    states; an empty `String` would collapse them into "the process has no arguments".

use core::fmt;
use std::path::Path;

use crate::fs::{Fs, FsError};
use crate::quality::QualityCode;

/// The marker for a word cut by the cap.
pub const TRUNCATED: &str = "<truncated>";

/// The marker a redacted value becomes.
pub const REDACTED: &str = "<redacted>";

/// The sampler's default `cmdline_max` (bytes).
pub const DEFAULT_MAX_BYTES: usize = 256;

/// The bound one command-line read applies.
#[derive(Clone, Copy, Debug, Eq, PartialEq)]
pub struct CmdlineBound {
    /// How many bytes of the line are kept at most (`cmdline_max`, 256 by default).
    pub max_bytes: usize,
}

impl CmdlineBound {
    /// The sampler's default.
    pub const SAMPLER: CmdlineBound = CmdlineBound {
        max_bytes: DEFAULT_MAX_BYTES,
    };

    /// A bound with another cap.
    ///
    /// # Errors
    ///
    /// [`CmdlineError::BoundZero`]: a cap of 0 would report every line as `<truncated>`.
    pub const fn new(max_bytes: usize) -> Result<CmdlineBound, CmdlineError> {
        if max_bytes == 0 {
            return Err(CmdlineError::BoundZero);
        }
        Ok(CmdlineBound { max_bytes })
    }

    /// The cap plus the two bytes [`read`] asks for to tell an exact fit from a cut.
    #[must_use]
    pub const fn read_bytes(self) -> usize {
        // `saturating_add` because a cap near `usize::MAX` must not wrap to a small read.
        self.max_bytes.saturating_add(2)
    }
}

/// Why a command line could not be read.
#[derive(Clone, Debug, Eq, PartialEq)]
pub enum CmdlineError {
    /// The process has no `cmdline` (a kernel thread) or it vanished: nothing to read.
    Missing,
    /// The read failed (permission, I/O).
    Read(FsError),
    /// A cap of 0 asks a question with no meaningful answer.
    BoundZero,
}

impl CmdlineError {
    /// The quality code this refusal maps to.
    #[must_use]
    pub fn quality(&self) -> QualityCode {
        match self {
            // A kernel thread genuinely has an empty command line; that is an absence,
            // not a failure.
            CmdlineError::Missing => QualityCode::ABSENT,
            CmdlineError::Read(_) => QualityCode::UNREADABLE,
            CmdlineError::BoundZero => QualityCode::NOT_SAMPLED,
        }
    }
}

impl fmt::Display for CmdlineError {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        match self {
            CmdlineError::Missing => f.write_str("the process has no command line"),
            CmdlineError::Read(error) => write!(f, "{error}"),
            CmdlineError::BoundZero => f.write_str("a command-line bound of 0 keeps nothing"),
        }
    }
}

impl core::error::Error for CmdlineError {}

impl From<FsError> for CmdlineError {
    fn from(error: FsError) -> CmdlineError {
        match error {
            FsError::NotFound | FsError::NotAFile => CmdlineError::Missing,
            other => CmdlineError::Read(other),
        }
    }
}

/// One command-line read: the redacted text, whether it was cut, and the cap used.
#[derive(Clone, Debug, Eq, PartialEq)]
pub struct ReadCmdline {
    /// The redacted, space-joined text (never empty when the line had words).
    pub text: String,
    /// True when the raw line was longer than the cap and was cut at a word boundary.
    pub truncated: bool,
    /// The cap that was applied.
    pub bound: CmdlineBound,
    /// How many raw bytes were read (at most `bound.read_bytes()`).
    pub raw_bytes: usize,
}

/// Read one `/proc/<pid>/cmdline` through [`Fs`] and render it.
///
/// # Errors
///
/// [`CmdlineError::Missing`] for a vanished process or a kernel thread,
/// [`CmdlineError::Read`] for a permission or I/O failure.
pub fn read<F: Fs>(fs: &F, path: &Path, bound: CmdlineBound) -> Result<ReadCmdline, CmdlineError> {
    // A *prefix* read: the two bytes past the cap are what tell "exactly full" from "cut",
    // and a longer line is a prefix rather than a refusal (see `Fs::read_prefix`).
    let raw = fs
        .read_prefix(path, bound.read_bytes())
        .map_err(CmdlineError::from)?;
    // An empty read is a process that vanished between `readdir` and `open` in the sampler;
    // here it is either an empty file (a kernel thread writes nothing) or a cut we cannot
    // render. Both are "no command line", which is what the caller must be able to tell
    // apart from an empty `String`.
    if raw.is_empty() {
        return Err(CmdlineError::Missing);
    }
    let raw_bytes = raw.len();
    let content = if raw.last() == Some(&0) {
        raw.len() - 1
    } else {
        raw.len()
    };
    let truncated = content > bound.max_bytes;
    let cut = cut_to_word(raw, bound.max_bytes);
    Ok(ReadCmdline {
        text: render(&cut),
        truncated,
        bound,
        raw_bytes,
    })
}

/// A line longer than `max` is cut back to its last complete word and the partial word
/// replaced by [`TRUNCATED`]; one word longer than the cap becomes just that marker.
///
/// A complete line of `max` bytes plus its closing NUL is **not** a cut.
#[must_use]
pub fn cut_to_word(mut bytes: Vec<u8>, max: usize) -> Vec<u8> {
    let content = if bytes.last() == Some(&0) {
        bytes.len().saturating_sub(1)
    } else {
        bytes.len()
    };
    if content <= max {
        return bytes;
    }
    bytes.truncate(max);
    let keep = bytes
        .iter()
        .rposition(|byte| *byte == 0)
        .map_or(0, |position| position + 1);
    bytes.truncate(keep);
    bytes.extend_from_slice(TRUNCATED.as_bytes());
    bytes
}

/// Raw NUL-separated command-line bytes to the redacted, space-joined text.
#[must_use]
pub fn render(bytes: &[u8]) -> String {
    let text = String::from_utf8_lossy(bytes);
    let split: Vec<&str> = text.split('\0').collect();
    // A trailing NUL leaves one empty word, which is not an argument.
    let words: Vec<&str> = if split.last() == Some(&"") {
        split[..split.len() - 1].to_vec()
    } else {
        split
    };
    redact(&words).join(" ").trim_end().to_string()
}

/// Words that make an option name or an environment-style key look secret.
/// Matched case-insensitively as substrings of the name.
const SECRET_WORDS: &[&str] = &[
    "password",
    "passwd",
    "passphrase",
    "pwd",
    "secret",
    "token",
    "apikey",
    "api-key",
    "api_key",
    "credential",
    "private-key",
    "private_key",
    "access-key",
    "access_key",
    "secret-key",
    "authorization",
    "cookie",
    "bearer",
    "psw",
];

/// Words containing "pass" that are not about a credential. Anything else containing "pass"
/// is treated as secret: hiding a harmless value costs little, showing a password does not.
const PASS_NOT_SECRET: &[&str] = &[
    "bypass",
    "compass",
    "passive",
    "passthrough",
    "pass-through",
    "passenger",
    "encompass",
    "surpass",
];

fn looks_secret(name: &str) -> bool {
    let n = name.trim_start_matches(['-', '+']).to_ascii_lowercase();
    if n.is_empty() {
        return false;
    }
    // `--password-file /path`, `--token-dir`, `--no-password`: the name mentions a secret,
    // the value is not one.
    const NOT_THE_SECRET_SUFFIX: &[&str] = &[
        "-file", "_file", "-path", "_path", "-dir", "_dir", "-env", "_env", "-url", "_url",
    ];
    const NOT_THE_SECRET_PREFIX: &[&str] = &[
        "no-", "no_", "skip-", "skip_", "without-", "disable-", "disable_",
    ];
    if NOT_THE_SECRET_SUFFIX
        .iter()
        .any(|suffix| n.ends_with(suffix))
        || NOT_THE_SECRET_PREFIX
            .iter()
            .any(|prefix| n.starts_with(prefix))
    {
        return false;
    }
    // Names that mention a secret word but hold no secret.
    const COUNT_ENDINGS: &[&str] = &[
        "max-tokens",
        "num-tokens",
        "n-tokens",
        "batched-tokens",
        "max_tokens",
        "num_tokens",
        "n_tokens",
        "batched_tokens",
    ];
    // The ending must be a whole part of the name (`--admin-tokens` ends in `n-tokens` and is
    // a secret), and what comes before it is judged on its own.
    for suffix in COUNT_ENDINGS {
        if let Some(rest) = n.strip_suffix(suffix) {
            if rest.is_empty() {
                return false;
            }
            if rest.ends_with(['-', '_']) {
                return looks_secret(rest.trim_end_matches(['-', '_']));
            }
        }
    }
    // `tokenizer` is not a secret word, but the rest of the name may hold one:
    // `--tokenizer-secret`, `HF_TOKENIZER_API_KEY`. A `-` in its place keeps the parts apart.
    // The name gets shorter on each call, so this ends.
    if n.contains("tokenizer") {
        return looks_secret(&n.replace("tokenizer", "-"));
    }
    // `docker login --password-stdin` takes no value; `--password-stdin-secret=x` is still judged.
    if n.strip_suffix("password-stdin")
        .is_some_and(|rest| rest.is_empty() || rest.ends_with(['-', '_']))
    {
        return false;
    }
    // Whole parts, not substrings: `-pw` and `--auth` are secret, `--author` is not.
    if n.split(['-', '_'])
        .any(|part| matches!(part, "pw" | "auth" | "creds" | "credential" | "credentials"))
    {
        return true;
    }
    let pass = n.contains("pass") && !PASS_NOT_SECRET.iter().any(|word| n.contains(word));
    SECRET_WORDS.iter().any(|word| n.contains(word))
        || pass
        || n.ends_with("_key")
        || n.ends_with("-key")
        || n == "key"
}

/// `-H "Authorization: Bearer x"` and `Cookie: a=b`: one word holding a secret header.
fn redact_header(word: &str) -> Option<String> {
    let (name, _) = word.split_once(':')?;
    // `--password=abc:def`, `SECRET_KEY=a:b`: a secret `name=` prefix is the `name=value`
    // branch's business (it hides the whole value); with another prefix only the rest is judged.
    let judged = match name.split_once('=') {
        Some((prefix, after)) => {
            if looks_secret(prefix) {
                return None;
            }
            after
        }
        None => name,
    };
    let n = judged.trim().to_ascii_lowercase();
    let secret = matches!(
        n.as_str(),
        "authorization" | "authentication" | "cookie" | "set-cookie"
    ) || n.ends_with("-token")
        || n.ends_with("-key")
        || looks_secret(&n);
    if n.contains(' ') || n.is_empty() || !secret {
        return None;
    }
    Some(format!("{}: {REDACTED}", name.trim()))
}

/// `scheme://user:password@host` becomes `scheme://user:<redacted>@host`; a userinfo without a
/// colon (a bare token) is hidden whole. Every `://` in the word counts.
fn redact_url(word: &str) -> Option<String> {
    let mut out = String::new();
    let mut pos = 0;
    let mut changed = false;
    while let Some(offset) = word[pos..].find("://") {
        let start = pos + offset + 3;
        let rest = &word[start..];
        let end = rest.find(['/', '?', '#', ',']).unwrap_or(rest.len());
        let auth = &rest[..end];
        out.push_str(&word[pos..start]);
        pos = start;
        if let Some(at) = auth.rfind('@') {
            let info = &auth[..at];
            match info.split_once(':') {
                Some((user, _)) => {
                    out.push_str(&format!("{user}:{REDACTED}"));
                    changed = true;
                }
                // A short plain login (`git@host`) is not a secret; anything else is hidden.
                None if (1..=16).contains(&info.len())
                    && info.bytes().all(
                        |byte| matches!(byte, b'a'..=b'z' | b'0'..=b'9' | b'.' | b'_' | b'-'),
                    ) =>
                {
                    out.push_str(info)
                }
                None => {
                    out.push_str(REDACTED);
                    changed = true;
                }
            }
            pos = start + at;
        }
    }
    out.push_str(&word[pos..]);
    changed.then_some(out)
}

/// `user:password` as `-u` / `--user` / `--proxy-user` take it. `None` when it is visible
/// anyway: both sides digits (`docker run -u 1000:1000`) on a container runtime.
/// Identical sides are NOT exempt: `admin:admin` is a password.
fn redact_user_pair(value: &str, ids_ok: bool) -> Option<String> {
    let (user, password) = value.split_once(':')?;
    let digits = |part: &str| !part.is_empty() && part.bytes().all(|byte| byte.is_ascii_digit());
    if ids_ok && digits(user) && digits(password) {
        return None;
    }
    Some(format!("{user}:{REDACTED}"))
}

/// In a word cut at `?`, `&`, `;` and `,`, a segment `name=value` whose name (before the
/// last `=`) looks secret loses its value.
fn redact_pairs(word: &str) -> String {
    let mut out = String::with_capacity(word.len());
    let mut segment_start = 0;
    fn flush(out: &mut String, segment: &str) {
        match segment.rfind('=') {
            Some(index) if looks_secret(&segment[..index]) => {
                out.push_str(&segment[..=index]);
                out.push_str(REDACTED);
            }
            _ => out.push_str(segment),
        }
    }
    for (index, character) in word.char_indices() {
        if matches!(character, '?' | '&' | ';' | ',') {
            flush(&mut out, &word[segment_start..index]);
            out.push(character);
            segment_start = index + character.len_utf8();
        }
    }
    flush(&mut out, &word[segment_start..]);
    out
}

/// One word. Returns the replacement and whether the NEXT word is this option's value.
fn redact_word(word: &str) -> (String, bool) {
    if word.contains("://") {
        // A URL word is never finished by the userinfo pass: query pairs and nested pairs
        // follow.
        let url = redact_url(word);
        let pairs = redact_pairs(url.as_deref().unwrap_or(word));
        if pairs != word {
            return (pairs, false);
        }
    }
    if let Some(header) = redact_header(word) {
        return (header, false);
    }
    // `name=value`, with or without leading dashes.
    if let Some((key, value)) = word.split_once('=') {
        if looks_secret(key) {
            return (format!("{key}={REDACTED}"), false);
        }
        // `--env=DB_PASSWORD=x`, `--set key.token=x`: the value itself may be a pair.
        if value.contains('=') {
            let (inner, _) = redact_word(value);
            if inner != value {
                return (format!("{key}={inner}"), false);
            }
        }
        return (word.to_string(), false);
    }
    // `--password hunter2`, `+rcon_password hunter2`: the next word is the value.
    (
        word.to_string(),
        (word.starts_with('-') || word.starts_with('+')) && looks_secret(word),
    )
}

/// Redact secrets in the NUL-separated words of a command line.
#[must_use]
pub fn redact(words: &[&str]) -> Vec<String> {
    let mut out = Vec::with_capacity(words.len());
    let mut hide_next = false;
    let mut user_next = false;
    // Numeric `uid:gid` pairs are only known not to be passwords for container runtimes.
    let program = words
        .first()
        .map(|word| word.rsplit('/').next().unwrap_or(word))
        .unwrap_or("");
    let ids_ok = matches!(program, "docker" | "podman" | "nerdctl" | "runc" | "crun");
    'words: for word in words {
        if hide_next {
            hide_next = false;
            // `--password --verbose`: the next word is another option, not a value.
            if !word.starts_with('-') {
                out.push(REDACTED.to_string());
                continue;
            }
        }
        // `-u user:password`, as curl takes it.
        if core::mem::take(&mut user_next) {
            if let Some(pair) = redact_user_pair(word, ids_ok) {
                out.push(pair);
                continue;
            }
        }
        user_next = matches!(*word, "-u" | "--user" | "--proxy-user");
        for prefix in ["--user=", "--proxy-user="] {
            if let Some(pair) = word
                .strip_prefix(prefix)
                .and_then(|value| redact_user_pair(value, ids_ok))
            {
                out.push(format!("{prefix}{pair}"));
                continue 'words;
            }
        }
        // Attached form `-ubob:pw`.
        if word.starts_with("-u") && !word.starts_with("--") {
            if let Some(pair) = redact_user_pair(&word[2..], ids_ok) {
                out.push(format!("-u{pair}"));
                continue;
            }
        }
        let (replacement, next) = redact_word(word);
        hide_next = next;
        out.push(replacement);
    }
    out
}

#[cfg(test)]
mod tests {
    use super::{
        cut_to_word, read, redact, render, CmdlineBound, CmdlineError, ReadCmdline,
        DEFAULT_MAX_BYTES, REDACTED, TRUNCATED,
    };
    use crate::fs::MemFs;
    use crate::quality::QualityCode;
    use std::path::Path;

    fn r(words: &[&str]) -> String {
        redact(words).join(" ")
    }

    #[test]
    fn game_server_arguments_are_hidden() {
        let line = r(&[
            "WSServer-Linux-Shipping",
            "Level01_Main",
            "-PSW=hunter2",
            "-adminpsw=hunter3",
            "-log",
        ]);
        assert!(!line.contains("hunter"), "{line}");
        assert!(
            line.contains("Level01_Main") && line.contains("-log"),
            "{line}"
        );
        let line = r(&["srcds", "+rcon_password", "hunter2", "+map", "de_dust"]);
        assert!(!line.contains("hunter"), "{line}");
        assert!(line.contains("+map de_dust"), "{line}");
        let line = r(&[
            "server",
            "Map?listen?ServerPassword=hunter2?ServerAdminPassword=hunter3",
        ]);
        assert!(!line.contains("hunter"), "{line}");
        assert!(!r(&["x", "-rconpass", "hunter2"]).contains("hunter"));
    }

    #[test]
    fn headers_and_user_pairs_are_hidden() {
        let line = r(&[
            "curl",
            "-H",
            "Authorization: Bearer hunter2",
            "-u",
            "bob:hunter3",
            "--user=amy:hunter4",
            "http://x/",
        ]);
        assert!(!line.contains("hunter"), "{line}");
        assert!(
            line.contains("bob:") && line.contains("amy:") && line.contains("http://x/"),
            "{line}"
        );
        // A container runtime's `uid:gid` is not a password, and a non-container program's
        // `1000:1000` is judged as one (the sampler's rule, kept).
        assert_eq!(
            r(&["docker", "run", "-u", "1000:1000", "img"]),
            "docker run -u 1000:1000 img"
        );
        assert_eq!(r(&["app", "-u", "1000:1000"]), "app -u 1000:<redacted>");
    }

    #[test]
    fn a_cut_line_never_shows_half_a_secret() {
        let full = b"psql\0postgres://admin:hunter2@db/x\0-v\0";
        // Cut inside the password: the partial word is replaced.
        let cut = render(&cut_to_word(full.to_vec(), 26));
        assert_eq!(cut, format!("psql {TRUNCATED}"));
        // A single word longer than the cap is just the marker.
        assert_eq!(
            render(&cut_to_word(b"postgres://admin:hunter2@db".to_vec(), 10)),
            TRUNCATED
        );
        // A line that fits exactly is untouched (cap + 1 bytes are read to tell).
        assert_eq!(render(&cut_to_word(b"a\0b\0".to_vec(), 4)), "a b");
        // And a cut after a complete word keeps it, redacted as usual.
        assert_eq!(
            render(&cut_to_word(b"x\0--password\0hunter2\0y".to_vec(), 14)),
            "x --password <redacted>"
        );
        // A NUL exactly at the cap keeps the word before it; the marker is appended.
        assert_eq!(
            render(&cut_to_word(b"abc\0de\0f".to_vec(), 6)),
            format!("abc {TRUNCATED}")
        );
        assert_eq!(render(&cut_to_word(b"abc\0de\0".to_vec(), 6)), "abc de");
    }

    #[test]
    fn every_url_in_a_word_and_bare_tokens_are_hidden() {
        let line = r(&["--urls=http://a:p1@x,http://b:p2@y"]);
        assert_eq!(
            line, "--urls=http://a:<redacted>@x,http://b:<redacted>@y",
            "{line}"
        );
        assert_eq!(
            r(&["https://ghp_TOKEN@github.com/o/r"]),
            "https://<redacted>@github.com/o/r"
        );
        // No userinfo, and an `@` only in the path, stay as they are.
        assert_eq!(
            r(&["https://github.com/o/r", "https://h/p@q"]),
            "https://github.com/o/r https://h/p@q"
        );
    }

    #[test]
    fn secret_headers_by_name() {
        for header in [
            "Token: x",
            "X-Secret: x",
            "Authentication: Basic y",
            "Apikey: x",
            "Cookie: a=b",
            "Set-Cookie: a=b",
            "X-Auth-Token: x",
            "X-Api-Key: x",
            "Authorization: Bearer x",
        ] {
            let line = r(&["curl", "-H", header]);
            assert!(line.ends_with(": <redacted>"), "{header} -> {line}");
        }
        assert_eq!(
            r(&["-H", "Accept: text/plain", "-H", "Content-Type: json"]),
            "-H Accept: text/plain -H Content-Type: json"
        );
    }

    #[test]
    fn option_names_are_judged_by_whole_parts() {
        assert_eq!(r(&["-pw", "hunter2"]), "-pw <redacted>");
        assert_eq!(r(&["--auth", "user:pw"]), "--auth <redacted>");
        assert_eq!(
            r(&["--db_creds", "x", "--credentials=y"]),
            "--db_creds <redacted> --credentials=<redacted>"
        );
    }

    #[test]
    fn names_that_only_mention_a_secret_stay_visible() {
        assert_eq!(
            r(&[
                "--tokenizer",
                "bert",
                "--max-tokens",
                "5",
                "--num_tokens=3",
                "--password-stdin"
            ]),
            "--tokenizer bert --max-tokens 5 --num_tokens=3 --password-stdin"
        );
        assert_eq!(r(&["--password", "x"]), "--password <redacted>");
    }

    #[test]
    fn ordinary_words_stay_readable() {
        let line = r(&[
            "app",
            "--passive",
            "--bypass-cache",
            "-H",
            "Accept: text/plain",
            "-u",
            "root",
            "--compass=north",
            "a:b",
        ]);
        assert_eq!(
            line,
            "app --passive --bypass-cache -H Accept: text/plain -u root --compass=north a:b"
        );
    }

    #[test]
    fn a_command_line_is_read_through_the_root_abstraction_within_its_cap() {
        let mut fs = MemFs::new();
        fs.directory("/proc/1", 1);
        fs.file(
            "/proc/1/cmdline",
            b"demo-worker\0--serve\0--port=8080\0".to_vec(),
            2,
        );
        let read_back: ReadCmdline =
            read(&fs, Path::new("/proc/1/cmdline"), CmdlineBound::SAMPLER).expect("cmdline");
        assert_eq!(read_back.text, "demo-worker --serve --port=8080");
        assert!(!read_back.truncated);
        assert_eq!(read_back.bound.max_bytes, DEFAULT_MAX_BYTES);
        assert_eq!(read_back.bound.read_bytes(), DEFAULT_MAX_BYTES + 2);
        assert_eq!(read_back.raw_bytes, 32);
    }

    #[test]
    fn a_long_line_is_cut_at_a_word_and_says_so() {
        let mut fs = MemFs::new();
        fs.directory("/proc/2", 1);
        let long = "app\0--flag\0a-very-long-value-that-does-not-fit-in-256-bytes\0";
        fs.file("/proc/2/cmdline", long.as_bytes().to_vec(), 2);
        let bound = CmdlineBound::new(16).expect("bound");
        // The fixture has more bytes than the read bound, so `Fs::read_prefix` returns the
        // first bytes and the reader reports the cut instead of refusing the fact.
        let read_back = read(&fs, Path::new("/proc/2/cmdline"), bound).expect("cmdline");
        assert!(read_back.truncated, "the line was longer than the cap");
        assert_eq!(read_back.bound.max_bytes, 16);

        // The cut itself, applied to bytes a caller already has in hand (the preflight's
        // path, where a bounded read is done by the caller and the text is what is bounded).
        let words = cut_to_word(b"app\0--flag\0a-very-long-value\0".to_vec(), 10);
        assert_eq!(render(&words), format!("app {TRUNCATED}"));
    }

    #[test]
    fn a_refusal_is_never_an_empty_string_and_never_a_zero() {
        let mut fs = MemFs::new();
        fs.directory("/proc/3", 1);
        fs.file("/proc/3/cmdline", Vec::new(), 2);
        assert_eq!(
            read(&fs, Path::new("/proc/3/cmdline"), CmdlineBound::SAMPLER),
            Err(CmdlineError::Missing)
        );
        assert_eq!(
            read(
                &fs,
                Path::new("/proc/nowhere/cmdline"),
                CmdlineBound::SAMPLER
            ),
            Err(CmdlineError::Missing)
        );
        assert_eq!(CmdlineError::Missing.quality(), QualityCode::ABSENT);
        assert_eq!(CmdlineError::BoundZero.quality(), QualityCode::NOT_SAMPLED);
        assert_eq!(CmdlineBound::new(0), Err(CmdlineError::BoundZero));
        // The two refusals render different texts.
        assert_ne!(
            CmdlineError::Missing.to_string(),
            CmdlineError::BoundZero.to_string()
        );
        assert_ne!(REDACTED, TRUNCATED);
    }
}
