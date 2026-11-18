//! Byte-level shape checks of the JSON payloads of REQ, REP, END and EVT frames
//! (WIRE.md §2.2, §4.1, §8.2) without a JSON library: `proto` stays dependency-free.
//!
//! A check validates the payload's **text** as far as the envelope needs it (UTF-8, one object,
//! no BOM, no trailing byte, string escapes, number grammar, nesting depth at most 8, no
//! duplicate key among the envelope's own keys) and the **shape** of the envelope (its key set,
//! the type of each value, the `ok` literal, the error envelope's keys, `corr` in JSON mode). It
//! returns byte ranges into the payload, never copies of input-sized data, and the only
//! allocation is the member table, bounded by [`MAX_MEMBERS`] entries.
//!
//! # Contracts
//!
//! - **Scope.** The values of `args`, `result` and `detail` are validated as JSON text but not
//!   inspected: their schemas and duplicate keys below the envelope level are the server's JSON
//!   layer's job (WIRE.md §4.2: "`proto` has no JSON parser").
//! - **Strict.** Whitespace between tokens is allowed; whitespace or any other byte before the
//!   `{` or after the closing `}` is not. Key order is free. A key is compared as written, so an
//!   escaped spelling of a key name is an unknown key. A `verb` containing an escape is refused.
//! - **The caller strips the line terminator.** A JSON-mode line ends in `\n` (WIRE.md §8.1),
//!   which is framing and not part of the JSON text: the check sees the object only, and a
//!   trailing `\n` or `\r` is a trailing byte, refused.
//! - **A lone surrogate escape passes.** The scan checks that an escape is well-formed (four hex
//!   digits), not that it names a scalar value: surrogates are the server's JSON layer's job.
//! - **A violation of a request is `bad-request`** on the wire (`detail.key` `""` for a text
//!   error, the offending key otherwise). A client that checks a REP, END or EVT gets the local
//!   [`EnvelopeError`] (the server is then the faulty peer, WIRE.md §6.5); [`EnvelopeError::kind`]
//!   names every refusal locally.
//! - **No panic, no recursion, no unchecked index.** Nesting is tracked in a fixed array.

use core::ops::Range;

/// Deepest nesting of a request, the envelope object counting as depth 1 (WIRE.md §4.2).
pub const MAX_DEPTH: usize = 8;

/// Most top-level keys a payload may carry: the widest envelope has five.
pub const MAX_MEMBERS: usize = 8;

/// Containers that may be open inside a member value.
const NEST: usize = MAX_DEPTH - 1;

/// The JSON type of a value.
#[derive(Clone, Copy, Debug, Eq, PartialEq)]
pub enum Kind {
    /// `{...}`
    Object,
    /// `[...]`
    Array,
    /// `"..."`
    Str,
    /// A number.
    Number,
    /// `true`
    True,
    /// `false`
    False,
    /// `null`
    Null,
}

/// Why a payload is not a valid envelope.
#[derive(Clone, Debug, Eq, PartialEq)]
pub enum EnvelopeError {
    /// The text is not one valid JSON object within the depth bound.
    Json,
    /// An envelope key appears twice.
    DuplicateKey {
        /// The second occurrence's key, as a byte range of the payload.
        key: Range<usize>,
    },
    /// A key the envelope does not define (or more than [`MAX_MEMBERS`] keys).
    UnknownKey {
        /// The key, as a byte range of the payload.
        key: Range<usize>,
    },
    /// A required key is absent.
    MissingKey(&'static str),
    /// A key's value has the wrong type or form.
    WrongType(&'static str),
}

impl EnvelopeError {
    /// The stable local name of this refusal.
    #[must_use]
    pub const fn kind(&self) -> &'static str {
        match self {
            EnvelopeError::Json => "json-text",
            EnvelopeError::DuplicateKey { .. } => "duplicate-key",
            EnvelopeError::UnknownKey { .. } => "unknown-key",
            EnvelopeError::MissingKey(_) => "missing-key",
            EnvelopeError::WrongType(_) => "wrong-type",
        }
    }
}

/// One top-level member: where its key and value are and what the value is.
#[derive(Clone, Debug, Eq, PartialEq)]
pub struct Member {
    /// The key text between its quotes.
    pub key: Range<usize>,
    /// The value's type.
    pub kind: Kind,
    /// The value's bytes (a string's range includes its quotes).
    pub value: Range<usize>,
}

struct Scan<'a> {
    bytes: &'a [u8],
    at: usize,
}

impl Scan<'_> {
    fn peek(&self) -> Option<u8> {
        self.bytes.get(self.at).copied()
    }

    fn skip_ws(&mut self) {
        while matches!(self.peek(), Some(b' ' | b'\t' | b'\n' | b'\r')) {
            self.at += 1;
        }
    }

    fn kind_here(&self) -> Result<Kind, EnvelopeError> {
        match self.peek() {
            Some(b'{') => Ok(Kind::Object),
            Some(b'[') => Ok(Kind::Array),
            Some(b'"') => Ok(Kind::Str),
            Some(b't') => Ok(Kind::True),
            Some(b'f') => Ok(Kind::False),
            Some(b'n') => Ok(Kind::Null),
            Some(b'-' | b'0'..=b'9') => Ok(Kind::Number),
            _ => Err(EnvelopeError::Json),
        }
    }

    fn word(&mut self, word: &[u8]) -> Result<(), EnvelopeError> {
        let end = self.at + word.len();
        if self.bytes.get(self.at..end) == Some(word) {
            self.at = end;
            Ok(())
        } else {
            Err(EnvelopeError::Json)
        }
    }

    fn digits(&mut self) {
        while matches!(self.peek(), Some(b'0'..=b'9')) {
            self.at += 1;
        }
    }

    fn need_digits(&mut self) -> Result<(), EnvelopeError> {
        let start = self.at;
        self.digits();
        if self.at == start {
            Err(EnvelopeError::Json)
        } else {
            Ok(())
        }
    }

    fn number(&mut self) -> Result<(), EnvelopeError> {
        if self.peek() == Some(b'-') {
            self.at += 1;
        }
        match self.peek() {
            Some(b'0') => self.at += 1,
            Some(b'1'..=b'9') => self.digits(),
            _ => return Err(EnvelopeError::Json),
        }
        if self.peek() == Some(b'.') {
            self.at += 1;
            self.need_digits()?;
        }
        if matches!(self.peek(), Some(b'e' | b'E')) {
            self.at += 1;
            if matches!(self.peek(), Some(b'+' | b'-')) {
                self.at += 1;
            }
            self.need_digits()?;
        }
        Ok(())
    }

    /// A string at its opening quote; the range is the text between the quotes.
    fn string(&mut self) -> Result<Range<usize>, EnvelopeError> {
        self.at += 1;
        let start = self.at;
        loop {
            match self.peek() {
                Some(b'"') => {
                    let end = self.at;
                    self.at += 1;
                    return Ok(start..end);
                }
                Some(b'\\') => {
                    self.at += 1;
                    match self.peek() {
                        Some(byte) if b"\"\\/bfnrt".contains(&byte) => self.at += 1,
                        Some(b'u') => {
                            self.at += 1;
                            for _ in 0..4 {
                                if !self.peek().is_some_and(|byte| byte.is_ascii_hexdigit()) {
                                    return Err(EnvelopeError::Json);
                                }
                                self.at += 1;
                            }
                        }
                        _ => return Err(EnvelopeError::Json),
                    }
                }
                Some(byte) if byte >= 0x20 => self.at += 1,
                _ => return Err(EnvelopeError::Json),
            }
        }
    }

    /// `"key" :` with the whitespace around the colon; the range is the key text.
    fn key_colon(&mut self) -> Result<Range<usize>, EnvelopeError> {
        if self.peek() != Some(b'"') {
            return Err(EnvelopeError::Json);
        }
        let key = self.string()?;
        self.skip_ws();
        if self.peek() != Some(b':') {
            return Err(EnvelopeError::Json);
        }
        self.at += 1;
        self.skip_ws();
        Ok(key)
    }

    /// Validate and skip one value starting at `at` (whitespace already skipped).
    fn skip_value(&mut self) -> Result<Kind, EnvelopeError> {
        let first = self.kind_here()?;
        let mut stack = [0u8; NEST];
        let mut depth = 0usize;
        let mut kind = first;
        loop {
            match kind {
                Kind::Object | Kind::Array => {
                    let closer = if kind == Kind::Object { b'}' } else { b']' };
                    *stack.get_mut(depth).ok_or(EnvelopeError::Json)? = closer;
                    depth += 1;
                    self.at += 1;
                    self.skip_ws();
                    if self.peek() == Some(closer) {
                        self.at += 1;
                        depth -= 1;
                    } else {
                        if closer == b'}' {
                            self.key_colon()?;
                        }
                        kind = self.kind_here()?;
                        continue;
                    }
                }
                Kind::Str => {
                    self.string()?;
                }
                Kind::True => self.word(b"true")?,
                Kind::False => self.word(b"false")?,
                Kind::Null => self.word(b"null")?,
                Kind::Number => self.number()?,
            }
            loop {
                let Some(&top) = depth.checked_sub(1).and_then(|at| stack.get(at)) else {
                    return Ok(first);
                };
                self.skip_ws();
                match self.peek() {
                    Some(b',') => {
                        self.at += 1;
                        self.skip_ws();
                        if top == b'}' {
                            self.key_colon()?;
                        }
                        break;
                    }
                    Some(byte) if byte == top => {
                        self.at += 1;
                        depth -= 1;
                    }
                    _ => return Err(EnvelopeError::Json),
                }
            }
            kind = self.kind_here()?;
        }
    }
}

/// The top-level members of a payload that is exactly one JSON object.
///
/// # Errors
///
/// [`EnvelopeError::Json`] for any text defect, [`EnvelopeError::DuplicateKey`] for a repeated
/// key and [`EnvelopeError::UnknownKey`] when more than [`MAX_MEMBERS`] keys are present.
pub fn members(bytes: &[u8]) -> Result<Vec<Member>, EnvelopeError> {
    if core::str::from_utf8(bytes).is_err() {
        return Err(EnvelopeError::Json);
    }
    object_members(bytes, 0..bytes.len())
}

/// The members of the object that is exactly `span` of `bytes` (valid UTF-8 already), with
/// ranges relative to `bytes`.
fn object_members(bytes: &[u8], span: Range<usize>) -> Result<Vec<Member>, EnvelopeError> {
    let mut scan = Scan {
        bytes,
        at: span.start,
    };
    if scan.peek() != Some(b'{') {
        return Err(EnvelopeError::Json);
    }
    scan.at += 1;
    scan.skip_ws();
    let mut out: Vec<Member> = Vec::new();
    if scan.peek() == Some(b'}') {
        scan.at += 1;
    } else {
        loop {
            let key = scan.key_colon()?;
            let start = scan.at;
            let kind = scan.skip_value()?;
            let value = start..scan.at;
            let name = bytes.get(key.clone());
            if out.iter().any(|seen| bytes.get(seen.key.clone()) == name) {
                return Err(EnvelopeError::DuplicateKey { key });
            }
            if out.len() >= MAX_MEMBERS {
                return Err(EnvelopeError::UnknownKey { key });
            }
            out.push(Member { key, kind, value });
            scan.skip_ws();
            match scan.peek() {
                Some(b',') => {
                    scan.at += 1;
                    scan.skip_ws();
                }
                Some(b'}') => {
                    scan.at += 1;
                    break;
                }
                _ => return Err(EnvelopeError::Json),
            }
        }
    }
    if scan.at == span.end {
        Ok(out)
    } else {
        Err(EnvelopeError::Json)
    }
}

const ANY_BOOL: &[Kind] = &[Kind::True, Kind::False];
const STR: &[Kind] = &[Kind::Str];
const OBJECT: &[Kind] = &[Kind::Object];
const NUMBER: &[Kind] = &[Kind::Number];
const STR_OR_NULL: &[Kind] = &[Kind::Str, Kind::Null];

fn named<'a>(bytes: &[u8], members: &'a [Member], name: &str) -> Option<&'a Member> {
    members
        .iter()
        .find(|member| bytes.get(member.key.clone()) == Some(name.as_bytes()))
}

/// Every member's key is one of `allowed` (all of an envelope's keys are required, so the
/// presence of each is checked by [`field`]).
fn only_keys(bytes: &[u8], members: &[Member], allowed: &[&str]) -> Result<(), EnvelopeError> {
    for member in members {
        let name = bytes.get(member.key.clone());
        if !allowed.iter().any(|label| name == Some(label.as_bytes())) {
            return Err(EnvelopeError::UnknownKey {
                key: member.key.clone(),
            });
        }
    }
    Ok(())
}

/// The member `label`, present and of one of `kinds`.
fn field<'a>(
    bytes: &[u8],
    members: &'a [Member],
    label: &'static str,
    kinds: &[Kind],
) -> Result<&'a Member, EnvelopeError> {
    let member = named(bytes, members, label).ok_or(EnvelopeError::MissingKey(label))?;
    if kinds.contains(&member.kind) {
        Ok(member)
    } else {
        Err(EnvelopeError::WrongType(label))
    }
}

/// `base` plus `corr` in JSON mode.
fn allow(base: &[&'static str], json_mode: bool) -> Vec<&'static str> {
    let mut all = base.to_vec();
    if json_mode {
        all.push("corr");
    }
    all
}

/// The text between the quotes of a string member's value range.
fn unquoted(range: &Range<usize>) -> Range<usize> {
    range.start + 1..range.end - 1
}

fn u32_of(bytes: &[u8], member: &Member, label: &'static str) -> Result<u32, EnvelopeError> {
    bytes
        .get(member.value.clone())
        .and_then(|text| core::str::from_utf8(text).ok())
        .and_then(|text| text.parse::<u32>().ok())
        .ok_or(EnvelopeError::WrongType(label))
}

/// The `corr` member of a JSON-mode line (an exact u32), `None` in binary mode where the key
/// is not defined and so is refused by [`only_keys`].
fn corr_of(
    bytes: &[u8],
    members: &[Member],
    json_mode: bool,
) -> Result<Option<u32>, EnvelopeError> {
    if !json_mode {
        return Ok(None);
    }
    let member = field(bytes, members, "corr", NUMBER)?;
    u32_of(bytes, member, "corr").map(Some)
}

fn count_of(bytes: &[u8], members: &[Member], label: &'static str) -> Result<u32, EnvelopeError> {
    let member = field(bytes, members, label, NUMBER)?;
    u32_of(bytes, member, label)
}

fn has_backslash(bytes: &[u8], range: &Range<usize>) -> bool {
    bytes
        .get(range.clone())
        .is_some_and(|text| text.contains(&b'\\'))
}

/// A checked REQ payload `{"verb": str, "args": object}` (JSON mode adds `"corr"`).
#[derive(Clone, Debug, Eq, PartialEq)]
pub struct Req {
    /// The verb's text between its quotes (no escape inside).
    pub verb: Range<usize>,
    /// The `args` object.
    pub args: Range<usize>,
    /// JSON mode: the request's `corr`, never 0.
    pub corr: Option<u32>,
}

/// Check a REQ payload (WIRE.md §2.2, §8.2).
///
/// # Errors
///
/// An [`EnvelopeError`]; a `verb` with a backslash and a JSON-mode `corr` of 0 are
/// [`EnvelopeError::WrongType`].
pub fn check_req(bytes: &[u8], json_mode: bool) -> Result<Req, EnvelopeError> {
    let found = members(bytes)?;
    only_keys(bytes, &found, &allow(&["verb", "args"], json_mode))?;
    let verb = field(bytes, &found, "verb", STR)?;
    let args = field(bytes, &found, "args", OBJECT)?;
    let corr = corr_of(bytes, &found, json_mode)?;
    if corr == Some(0) {
        return Err(EnvelopeError::WrongType("corr"));
    }
    if has_backslash(bytes, &verb.value) {
        return Err(EnvelopeError::WrongType("verb"));
    }
    Ok(Req {
        verb: unquoted(&verb.value),
        args: args.value.clone(),
        corr,
    })
}

/// The error envelope's inner object `{"code": str, "message": str, "detail": object}`.
fn check_error_object(bytes: &[u8], range: &Range<usize>) -> Result<(), EnvelopeError> {
    let found = object_members(bytes, range.clone())?;
    only_keys(bytes, &found, &["code", "message", "detail"])?;
    field(bytes, &found, "code", STR)?;
    field(bytes, &found, "message", STR)?;
    field(bytes, &found, "detail", OBJECT)?;
    Ok(())
}

/// A checked REP payload.
#[derive(Clone, Debug, Eq, PartialEq)]
pub struct Rep {
    /// The `ok` literal.
    pub ok: bool,
    /// The `result` object (`ok` true) or the `error` object (`ok` false).
    pub body: Range<usize>,
    /// JSON mode: the reply's `corr` (0 for a server-originated line).
    pub corr: Option<u32>,
}

fn ok_of(bytes: &[u8], members: &[Member]) -> Result<bool, EnvelopeError> {
    let member = field(bytes, members, "ok", ANY_BOOL)?;
    Ok(member.kind == Kind::True)
}

/// Check a REP payload: `{"ok": true, "result": object}` or the error envelope of §4.1
/// (`{"ok": false, "error": {"code", "message", "detail"}}`), JSON mode adding `"corr"`.
///
/// # Errors
///
/// An [`EnvelopeError`].
pub fn check_rep(bytes: &[u8], json_mode: bool) -> Result<Rep, EnvelopeError> {
    let found = members(bytes)?;
    let ok = ok_of(bytes, &found)?;
    let label = if ok { "result" } else { "error" };
    only_keys(bytes, &found, &allow(&["ok", label], json_mode))?;
    let body = field(bytes, &found, label, OBJECT)?;
    if !ok {
        check_error_object(bytes, &body.value)?;
    }
    Ok(Rep {
        ok,
        body: body.value.clone(),
        corr: corr_of(bytes, &found, json_mode)?,
    })
}

/// A checked END payload (binary mode only: JSON mode has no block verbs).
#[derive(Clone, Debug, Eq, PartialEq)]
pub struct End {
    /// The `ok` literal.
    pub ok: bool,
    /// BLOCK frames of the stream sent before the END.
    pub blocks: u32,
    /// Blocks dropped after the last pinned one.
    pub dropped: u32,
    /// `ok` true: the `reason` text between its quotes.
    pub reason: Option<Range<usize>>,
    /// `ok` true: the continuation cursor's text, `None` for `null`.
    pub next: Option<Range<usize>>,
}

/// Check an END payload (WIRE.md §2.2): `{"ok": true, "blocks": u32, "dropped": u32, "next":
/// str|null, "reason": str}` or `{"ok": false, "blocks": u32, "dropped": u32, "error": {...}}`.
///
/// # Errors
///
/// An [`EnvelopeError`].
pub fn check_end(bytes: &[u8]) -> Result<End, EnvelopeError> {
    let found = members(bytes)?;
    let ok = ok_of(bytes, &found)?;
    let allowed: &[&str] = if ok {
        &["ok", "blocks", "dropped", "next", "reason"]
    } else {
        &["ok", "blocks", "dropped", "error"]
    };
    only_keys(bytes, &found, allowed)?;
    let blocks = count_of(bytes, &found, "blocks")?;
    let dropped = count_of(bytes, &found, "dropped")?;
    if !ok {
        let error = field(bytes, &found, "error", OBJECT)?;
        check_error_object(bytes, &error.value)?;
        return Ok(End {
            ok,
            blocks,
            dropped,
            reason: None,
            next: None,
        });
    }
    let next = field(bytes, &found, "next", STR_OR_NULL)?;
    let reason = field(bytes, &found, "reason", STR)?;
    Ok(End {
        ok,
        blocks,
        dropped,
        reason: Some(unquoted(&reason.value)),
        next: (next.kind == Kind::Str).then(|| unquoted(&next.value)),
    })
}

/// A checked EVT payload.
#[derive(Clone, Debug, Eq, PartialEq)]
pub struct Evt {
    /// The event name between its quotes.
    pub event: Range<usize>,
}

/// Check an EVT payload: an object with a string `event`; the only event of 1.0, `closing`,
/// carries exactly `{"event": "closing", "reason": str}` (WIRE.md §2.2). Other events keep
/// their own keys (at most [`MAX_MEMBERS`]).
///
/// # Errors
///
/// An [`EnvelopeError`].
pub fn check_evt(bytes: &[u8]) -> Result<Evt, EnvelopeError> {
    let found = members(bytes)?;
    let member = field(bytes, &found, "event", STR)?;
    let event = unquoted(&member.value);
    if bytes.get(event.clone()) == Some(b"closing".as_slice()) {
        only_keys(bytes, &found, &["event", "reason"])?;
        field(bytes, &found, "reason", STR)?;
    }
    Ok(Evt { event })
}

#[cfg(test)]
mod tests {
    use super::{check_end, check_evt, check_rep, check_req, members, EnvelopeError, Kind};

    fn kind<T>(result: Result<T, EnvelopeError>) -> &'static str {
        match result {
            Ok(_) => "ok",
            Err(error) => error.kind(),
        }
    }

    fn req_kind(payload: &str) -> &'static str {
        kind(check_req(payload.as_bytes(), false))
    }

    /// A JSON unicode escape built at run time: the source never spells one out.
    fn unicode_escape(hex: &str) -> String {
        format!("{}u{hex}", '\\')
    }

    /// A valid request whose `args.a` string holds `value` verbatim.
    fn with_arg(value: &str) -> String {
        format!(r#"{{"verb":"x","args":{{"a":"{value}"}}}}"#)
    }

    fn text(bytes: &[u8], range: &core::ops::Range<usize>) -> String {
        String::from_utf8_lossy(bytes.get(range.clone()).unwrap_or(&[])).into_owned()
    }

    #[test]
    fn a_valid_request_returns_its_ranges() {
        let bytes = r#"{"args": {"a": [1, 2.5e3, "xé"]}, "verb": "status"}"#.as_bytes();
        let req = check_req(bytes, false).expect("valid");
        assert_eq!(text(bytes, &req.verb), "status");
        assert_eq!(text(bytes, &req.args), r#"{"a": [1, 2.5e3, "xé"]}"#);
        assert_eq!(req.corr, None);
        let json = br#"{"corr": 7, "verb": "hello", "args": {}}"#;
        assert_eq!(check_req(json, true).expect("valid").corr, Some(7));
    }

    #[test]
    fn corr_belongs_to_json_mode_only() {
        let with = br#"{"corr": 7, "verb": "x", "args": {}}"#;
        assert_eq!(kind(check_req(with, false)), "unknown-key");
        let without = br#"{"verb": "x", "args": {}}"#;
        assert_eq!(kind(check_req(without, true)), "missing-key");
        let zero = br#"{"corr": 0, "verb": "x", "args": {}}"#;
        assert_eq!(check_req(zero, true), Err(EnvelopeError::WrongType("corr")));
        let big = br#"{"corr": 4294967296, "verb": "x", "args": {}}"#;
        assert_eq!(check_req(big, true), Err(EnvelopeError::WrongType("corr")));
    }

    #[test]
    fn the_request_table_of_hostile_payloads() {
        let table: [(&[u8], &str); 35] = [
            (b"", "json-text"),
            (b" ", "json-text"),
            (b"[]", "json-text"),
            (b"{", "json-text"),
            (b"{}", "missing-key"),
            (br#"{"verb":"x","args":{}} "#, "json-text"),
            (br#" {"verb":"x","args":{}}"#, "json-text"),
            (b"\xEF\xBB\xBF{\"verb\":\"x\",\"args\":{}}", "json-text"),
            (b"{\"verb\":\"\xFF\",\"args\":{}}", "json-text"),
            (br#"{"verb":"x","args":{}}{"#, "json-text"),
            (br#"{"verb":"x","args":{},}"#, "json-text"),
            (br#"{"verb":"x" "args":{}}"#, "json-text"),
            (br#"{"verb":"x","args"{}}"#, "json-text"),
            (br#"{verb:"x","args":{}}"#, "json-text"),
            (br#"{"verb":"x","args":{},"verb":"y"}"#, "duplicate-key"),
            (br#"{"verb":"x","verb":"x","args":{}}"#, "duplicate-key"),
            (br#"{"verb":"x","args":{},"extra":1}"#, "unknown-key"),
            (br#"{"verb":"x"}"#, "missing-key"),
            (br#"{"args":{}}"#, "missing-key"),
            (br#"{"verb":1,"args":{}}"#, "wrong-type"),
            (br#"{"verb":null,"args":{}}"#, "wrong-type"),
            (br#"{"verb":"x","args":[]}"#, "wrong-type"),
            (br#"{"verb":"x","args":null}"#, "wrong-type"),
            (br#"{"verb":"x\n","args":{}}"#, "wrong-type"),
            (br#"{"verb":"x\q","args":{}}"#, "json-text"),
            (br#"{"verb":"x\u12G4","args":{}}"#, "json-text"),
            (b"{\"verb\":\"x\ty\",\"args\":{}}", "json-text"),
            (br#"{"verb":"x","args":{"n":01}}"#, "json-text"),
            (br#"{"verb":"x","args":{"n":1.}}"#, "json-text"),
            (br#"{"verb":"x","args":{"n":-}}"#, "json-text"),
            (br#"{"verb":"x","args":{"n":1e}}"#, "json-text"),
            (br#"{"verb":"x","args":{"n":tru}}"#, "json-text"),
            (br#"{"verb":"x","args":{"n":[1,]}}"#, "json-text"),
            (br#"{"verb":"x","args":{"n":nulll}}"#, "json-text"),
            (br#"{"verb":"x","args":{"a":{"b":}}}"#, "json-text"),
        ];
        for (payload, expected) in table {
            assert_eq!(
                kind(check_req(payload, false)),
                expected,
                "{}",
                String::from_utf8_lossy(payload)
            );
        }
    }

    #[test]
    fn nesting_is_bounded_at_eight_with_the_envelope_object_as_one() {
        let nest = |levels: usize| {
            let inner = format!("{}{}", "[".repeat(levels), "]".repeat(levels));
            format!(r#"{{"verb":"x","args":{{"a":{inner}}}}}"#)
        };
        // args object is depth 2, so 6 arrays reach depth 8; a seventh is depth 9.
        assert_eq!(kind(check_req(nest(6).as_bytes(), false)), "ok");
        assert_eq!(kind(check_req(nest(7).as_bytes(), false)), "json-text");
        let deep = format!("{}1{}", "[".repeat(100_000), "]".repeat(100_000));
        let bomb = format!(r#"{{"verb":"x","args":{{"a":{deep}}}}}"#);
        assert_eq!(kind(check_req(bomb.as_bytes(), false)), "json-text");
    }

    #[test]
    fn more_than_eight_keys_and_nested_duplicates_are_the_documented_scope() {
        let nine = br#"{"a":1,"b":1,"c":1,"d":1,"e":1,"f":1,"g":1,"h":1,"i":1}"#;
        assert_eq!(kind(members(nine).map(|_| ())), "unknown-key");
        let nested = br#"{"verb":"x","args":{"k":1,"k":2}}"#;
        assert_eq!(kind(check_req(nested, false)), "ok");
        let found = members(br#"{ "a" : [ ] , "b":{ } }"#).expect("whitespace is allowed");
        assert_eq!(found.len(), 2);
        assert_eq!(found.first().map(|member| member.kind), Some(Kind::Array));
    }

    #[test]
    fn replies_follow_the_ok_literal() {
        let ok = br#"{"ok":true,"result":{"a":1}}"#;
        let rep = check_rep(ok, false).expect("valid");
        assert!(rep.ok);
        assert_eq!(text(ok, &rep.body), r#"{"a":1}"#);
        let err = br#"{"ok":false,"error":{"code":"x","message":"m","detail":{}},"corr":0}"#;
        let rep = check_rep(err, true).expect("valid");
        assert!(!rep.ok);
        assert_eq!(rep.corr, Some(0));
        let table: [(&[u8], bool, &str); 13] = [
            (br#"{"ok":true}"#, false, "missing-key"),
            (br#"{"ok":true,"error":{}}"#, false, "unknown-key"),
            (br#"{"ok":false,"result":{}}"#, false, "unknown-key"),
            (br#"{"ok":"true","result":{}}"#, false, "wrong-type"),
            (br#"{"ok":1,"result":{}}"#, false, "wrong-type"),
            (br#"{"result":{}}"#, false, "missing-key"),
            (br#"{"ok":true,"result":[]}"#, false, "wrong-type"),
            (
                br#"{"ok":false,"error":{"code":"x","message":"m"}}"#,
                false,
                "missing-key",
            ),
            (
                br#"{"ok":false,"error":{"code":1,"message":"m","detail":{}}}"#,
                false,
                "wrong-type",
            ),
            (
                br#"{"ok":false,"error":{"code":"x","message":"m","detail":{},"x":1}}"#,
                false,
                "unknown-key",
            ),
            (
                br#"{"ok":false,"error":{"code":"x","code":"y","message":"m","detail":{}}}"#,
                false,
                "duplicate-key",
            ),
            (br#"{"ok":true,"result":{}}"#, true, "missing-key"),
            (br#"{"ok":true,"result":{},"corr":1.5}"#, true, "wrong-type"),
        ];
        for (payload, json_mode, expected) in table {
            assert_eq!(
                kind(check_rep(payload, json_mode)),
                expected,
                "{}",
                String::from_utf8_lossy(payload)
            );
        }
    }

    #[test]
    fn ends_carry_their_counts_and_reason_or_error() {
        let ok = br#"{"ok":true,"blocks":3,"dropped":1,"next":"c1","reason":"page"}"#;
        let end = check_end(ok).expect("valid");
        assert_eq!((end.blocks, end.dropped, end.ok), (3, 1, true));
        let reason = end.reason.map(|range| text(ok, &range));
        assert_eq!(reason, Some("page".to_string()));
        let next = end.next.map(|range| text(ok, &range));
        assert_eq!(next, Some("c1".to_string()));
        let done = br#"{"reason":"complete","next":null,"dropped":0,"blocks":0,"ok":true}"#;
        assert_eq!(check_end(done).expect("valid").next, None);
        let bad =
            br#"{"ok":false,"blocks":2,"dropped":0,"error":{"code":"","message":"","detail":{}}}"#;
        let end = check_end(bad).expect("valid");
        assert!(!end.ok && end.reason.is_none());
        let table: [(&[u8], &str); 12] = [
            (
                br#"{"ok":true,"blocks":1,"dropped":0,"reason":"complete"}"#,
                "missing-key",
            ),
            (
                br#"{"ok":true,"blocks":-1,"dropped":0,"next":null,"reason":"x"}"#,
                "wrong-type",
            ),
            (
                br#"{"ok":true,"blocks":1.0,"dropped":0,"next":null,"reason":"x"}"#,
                "wrong-type",
            ),
            (
                br#"{"ok":true,"blocks":4294967296,"dropped":0,"next":null,"reason":"x"}"#,
                "wrong-type",
            ),
            (
                br#"{"ok":true,"blocks":"1","dropped":0,"next":null,"reason":"x"}"#,
                "wrong-type",
            ),
            (
                br#"{"ok":true,"blocks":1,"dropped":0,"next":1,"reason":"x"}"#,
                "wrong-type",
            ),
            (
                br#"{"ok":true,"blocks":1,"dropped":0,"next":null,"reason":"x","error":{}}"#,
                "unknown-key",
            ),
            (
                br#"{"ok":false,"blocks":1,"dropped":0,"reason":"x"}"#,
                "unknown-key",
            ),
            (
                br#"{"ok":false,"blocks":1,"dropped":0,"error":{"code":"x"}}"#,
                "missing-key",
            ),
            (
                br#"{"ok":true,"blocks":1,"next":null,"reason":"x"}"#,
                "missing-key",
            ),
            (
                br#"{"ok":true,"blocks":1,"dropped":0,"next":null,"reason":1}"#,
                "wrong-type",
            ),
            (br#"{"ok":false,"blocks":1,"dropped":0}"#, "missing-key"),
        ];
        for (payload, expected) in table {
            assert_eq!(
                kind(check_end(payload)),
                expected,
                "{}",
                String::from_utf8_lossy(payload)
            );
        }
    }

    #[test]
    fn events_need_a_string_event_and_closing_has_exactly_a_reason() {
        let closing = br#"{"event":"closing","reason":"idle"}"#;
        let evt = check_evt(closing).expect("valid");
        assert_eq!(text(closing, &evt.event), "closing");
        let other = br#"{"event":"span","a":[1],"b":{}}"#;
        assert_eq!(kind(check_evt(other)), "ok");
        let table: [(&[u8], &str); 6] = [
            (br#"{"reason":"idle"}"#, "missing-key"),
            (br#"{"event":1}"#, "wrong-type"),
            (br#"{"event":"closing"}"#, "missing-key"),
            (br#"{"event":"closing","reason":1}"#, "wrong-type"),
            (
                br#"{"event":"closing","reason":"x","more":1}"#,
                "unknown-key",
            ),
            (
                br#"{"event":"closing","event":"closing","reason":"x"}"#,
                "duplicate-key",
            ),
        ];
        for (payload, expected) in table {
            assert_eq!(
                kind(check_evt(payload)),
                expected,
                "{}",
                String::from_utf8_lossy(payload)
            );
        }
    }

    #[test]
    fn an_escaped_key_spelling_is_an_unknown_key_and_names_are_exact() {
        let key = format!("{}erb", unicode_escape("0076"));
        let escaped = format!(r#"{{"{key}":"x","args":{{}}}}"#);
        assert!(escaped.contains('\\'), "has an escape");
        assert_eq!(req_kind(&escaped), "unknown-key");
        // Keys are compared as written, so the escaped spelling is no duplicate of `verb`.
        let twice = format!(r#"{{"verb":"x","{key}":"y","args":{{}}}}"#);
        assert!(twice.contains('\\'), "has an escape");
        assert_eq!(req_kind(&twice), "unknown-key");
        let plain = r#"{"verb":"x","verb":"y","args":{}}"#;
        assert_eq!(req_kind(plain), "duplicate-key");
        let upper = r#"{"Verb":"x","args":{}}"#;
        assert_eq!(req_kind(upper), "unknown-key");
    }

    #[test]
    fn whitespace_strings_and_escapes_follow_the_json_grammar() {
        let spaced = "{\t\"verb\"\t:\r\n\"x y\" ,\n\"args\" : { } \r\n}";
        let req = check_req(spaced.as_bytes(), false).expect("valid");
        assert_eq!(text(spaced.as_bytes(), &req.verb), "x y");
        let escapes = r#"{"verb":"x","args":{"a":"\"\\\/\b\f\n\r\t"}}"#;
        assert_eq!(req_kind(escapes), "ok");
        let hex = ["09af", "AF09", "aAfF"].map(unicode_escape).concat();
        assert_eq!(req_kind(&with_arg(&hex)), "ok");
        let short = unicode_escape("123");
        assert_eq!(req_kind(&with_arg(&short)), "json-text");
        // A lone surrogate passes the scan: the server's JSON layer owns it.
        let lone = unicode_escape("d800");
        assert_eq!(req_kind(&with_arg(&lone)), "ok");
        // The caller strips the line terminator (JSON mode, WIRE.md 8.1) before the check.
        let line = "{\"verb\":\"x\",\"args\":{}}\n";
        assert_eq!(req_kind(line), "json-text");
    }

    #[test]
    fn numbers_follow_the_json_grammar_and_nothing_wider() {
        let valid = br#"{"verb":"x","args":{"n":[-0,0.5,1E+2,2e-2,10,-1.25e10]}}"#;
        assert_eq!(kind(check_req(valid, false)), "ok");
        let bad_exponent = br#"{"verb":"x","args":{"n":1e+}}"#;
        assert_eq!(kind(check_req(bad_exponent, false)), "json-text");
        let leading_plus = br#"{"verb":"x","args":{"n":+1}}"#;
        assert_eq!(kind(check_req(leading_plus, false)), "json-text");
    }

    #[test]
    fn an_error_object_reports_ranges_into_the_whole_payload() {
        let payload = br#"{"ok":false,"error":{"code":"x","message":"m","detail":{},"extra":1}}"#;
        match check_rep(payload, false) {
            Err(EnvelopeError::UnknownKey { key }) => assert_eq!(text(payload, &key), "extra"),
            other => panic!("expected an unknown key, got {other:?}"),
        }
        let twice = br#"{"ok":false,"error":{"code":"x","code":"y","message":"m","detail":{}}}"#;
        match check_rep(twice, false) {
            Err(EnvelopeError::DuplicateKey { key }) => {
                assert_eq!(text(twice, &key), "code");
                assert!(key.start > 30, "the second occurrence, past the first");
            }
            other => panic!("expected a duplicate key, got {other:?}"),
        }
    }
}
