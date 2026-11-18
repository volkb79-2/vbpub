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
//! - **A violation is `bad-request`** on the wire (`detail.key` `""` for a text error, the
//!   offending key otherwise); [`EnvelopeError::kind`] names it locally.
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
                if depth == 0 {
                    return Ok(first);
                }
                self.skip_ws();
                let top = stack.get(depth - 1).copied().unwrap_or(0);
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
    let mut scan = Scan { bytes, at: 0 };
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
    if scan.at == bytes.len() {
        Ok(out)
    } else {
        Err(EnvelopeError::Json)
    }
}

/// One key an envelope defines: its name, the accepted value types and whether it is required.
type KeySpec = (&'static str, &'static [Kind], bool);

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

/// Every member is a defined key of an accepted type and every required key is present.
fn shape(bytes: &[u8], members: &[Member], keys: &[KeySpec]) -> Result<(), EnvelopeError> {
    for member in members {
        let name = bytes.get(member.key.clone());
        let spec = keys.iter().find(|spec| name == Some(spec.0.as_bytes()));
        let Some(&(label, kinds, _)) = spec else {
            return Err(EnvelopeError::UnknownKey {
                key: member.key.clone(),
            });
        };
        if !kinds.contains(&member.kind) {
            return Err(EnvelopeError::WrongType(label));
        }
    }
    for &(label, _, required) in keys {
        if required && named(bytes, members, label).is_none() {
            return Err(EnvelopeError::MissingKey(label));
        }
    }
    Ok(())
}

fn u32_of(bytes: &[u8], member: &Member, label: &'static str) -> Result<u32, EnvelopeError> {
    bytes
        .get(member.value.clone())
        .and_then(|text| core::str::from_utf8(text).ok())
        .and_then(|text| text.parse::<u32>().ok())
        .ok_or(EnvelopeError::WrongType(label))
}

/// The `corr` member of a JSON-mode line (an exact u32), `None` in binary mode where the key
/// is not defined and so is refused by [`shape`].
fn corr_of(
    bytes: &[u8],
    members: &[Member],
    json_mode: bool,
) -> Result<Option<u32>, EnvelopeError> {
    match named(bytes, members, "corr") {
        Some(member) if json_mode => u32_of(bytes, member, "corr").map(Some),
        _ => Ok(None),
    }
}

fn with_corr(keys: &[KeySpec], json_mode: bool) -> Vec<KeySpec> {
    let mut all = keys.to_vec();
    if json_mode {
        all.push(("corr", NUMBER, true));
    }
    all
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
    let keys = with_corr(&[("verb", STR, true), ("args", OBJECT, true)], json_mode);
    shape(bytes, &found, &keys)?;
    let corr = corr_of(bytes, &found, json_mode)?;
    if corr == Some(0) {
        return Err(EnvelopeError::WrongType("corr"));
    }
    let verb = range_of(bytes, &found, "verb");
    let args = range_of(bytes, &found, "args");
    let text = bytes.get(verb.clone()).unwrap_or(&[]);
    if text.contains(&b'\\') {
        return Err(EnvelopeError::WrongType("verb"));
    }
    Ok(Req {
        verb: verb.start + 1..verb.end - 1,
        args,
        corr,
    })
}

/// The error envelope's inner object `{"code": str, "message": str, "detail": object}`.
fn check_error_object(bytes: &[u8], range: &Range<usize>) -> Result<(), EnvelopeError> {
    let inner = bytes.get(range.clone()).unwrap_or(&[]);
    let found = members(inner)?;
    let keys: [KeySpec; 3] = [
        ("code", STR, true),
        ("message", STR, true),
        ("detail", OBJECT, true),
    ];
    shape(inner, &found, &keys)
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
    match named(bytes, members, "ok") {
        Some(member) if member.kind == Kind::True => Ok(true),
        Some(member) if member.kind == Kind::False => Ok(false),
        Some(_) => Err(EnvelopeError::WrongType("ok")),
        None => Err(EnvelopeError::MissingKey("ok")),
    }
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
    let body_key: KeySpec = if ok {
        ("result", OBJECT, true)
    } else {
        ("error", OBJECT, true)
    };
    let keys = with_corr(&[("ok", ANY_BOOL, true), body_key], json_mode);
    shape(bytes, &found, &keys)?;
    let body = range_of(bytes, &found, body_key.0);
    if !ok {
        check_error_object(bytes, &body)?;
    }
    Ok(Rep {
        ok,
        body,
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
    let common: [KeySpec; 3] = [
        ("ok", ANY_BOOL, true),
        ("blocks", NUMBER, true),
        ("dropped", NUMBER, true),
    ];
    let mut keys = common.to_vec();
    if ok {
        keys.push(("next", STR_OR_NULL, true));
        keys.push(("reason", STR, true));
    } else {
        keys.push(("error", OBJECT, true));
    }
    shape(bytes, &found, &keys)?;
    let blocks = count_of(bytes, &found, "blocks")?;
    let dropped = count_of(bytes, &found, "dropped")?;
    if let Some(error) = named(bytes, &found, "error") {
        check_error_object(bytes, &error.value)?;
    }
    Ok(End {
        ok,
        blocks,
        dropped,
        reason: text_of(bytes, &found, "reason"),
        next: text_of(bytes, &found, "next"),
    })
}

fn count_of(bytes: &[u8], members: &[Member], label: &'static str) -> Result<u32, EnvelopeError> {
    named(bytes, members, label)
        .ok_or(EnvelopeError::MissingKey(label))
        .and_then(|member| u32_of(bytes, member, label))
}

/// The value range of a key `shape` proved present (the empty range is never used).
fn range_of(bytes: &[u8], members: &[Member], label: &str) -> Range<usize> {
    named(bytes, members, label)
        .map_or(0..0, |member| member.value.clone())
}

fn text_of(bytes: &[u8], members: &[Member], label: &str) -> Option<Range<usize>> {
    named(bytes, members, label)
        .filter(|member| member.kind == Kind::Str)
        .map(|member| member.value.start + 1..member.value.end - 1)
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
    let Some(member) = named(bytes, &found, "event") else {
        return Err(EnvelopeError::MissingKey("event"));
    };
    if member.kind != Kind::Str {
        return Err(EnvelopeError::WrongType("event"));
    }
    let event = member.value.start + 1..member.value.end - 1;
    if bytes.get(event.clone()) == Some(b"closing".as_slice()) {
        let keys: [KeySpec; 2] = [("event", STR, true), ("reason", STR, true)];
        shape(bytes, &found, &keys)?;
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

    fn text(bytes: &[u8], range: &core::ops::Range<usize>) -> String {
        String::from_utf8_lossy(bytes.get(range.clone()).unwrap_or(&[])).into_owned()
    }

    #[test]
    fn a_valid_request_returns_its_ranges() {
        let bytes = br#"{"args": {"a": [1, 2.5e3, "xé"]}, "verb": "status"}"#;
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
        let table: [(&[u8], bool, &str); 12] = [
            (br#"{"ok":true}"#, false, "missing-key"),
            (br#"{"ok":true,"error":{}}"#, false, "unknown-key"),
            (br#"{"ok":false,"result":{}}"#, false, "unknown-key"),
            (br#"{"ok":"true","result":{}}"#, false, "wrong-type"),
            (br#"{"ok":1,"result":{}}"#, false, "wrong-type"),
            (br#"{"result":{}}"#, false, "missing-key"),
            (br#"{"ok":true,"result":[]}"#, false, "wrong-type"),
            (br#"{"ok":false,"error":{"code":"x","message":"m"}}"#, false, "missing-key"),
            (br#"{"ok":false,"error":{"code":1,"message":"m","detail":{}}}"#, false, "wrong-type"),
            (br#"{"ok":false,"error":{"code":"x","message":"m","detail":{},"x":1}}"#, false, "unknown-key"),
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
        assert_eq!(end.reason.map(|range| text(ok, &range)), Some("page".to_string()));
        assert_eq!(end.next.map(|range| text(ok, &range)), Some("c1".to_string()));
        let done = br#"{"reason":"complete","next":null,"dropped":0,"blocks":0,"ok":true}"#;
        assert_eq!(check_end(done).expect("valid").next, None);
        let bad = br#"{"ok":false,"blocks":2,"dropped":0,"error":{"code":"internal","message":"m","detail":{}}}"#;
        let end = check_end(bad).expect("valid");
        assert!(!end.ok && end.reason.is_none());
        let table: [(&[u8], &str); 9] = [
            (br#"{"ok":true,"blocks":1,"dropped":0,"reason":"complete"}"#, "missing-key"),
            (br#"{"ok":true,"blocks":-1,"dropped":0,"next":null,"reason":"x"}"#, "wrong-type"),
            (br#"{"ok":true,"blocks":1.0,"dropped":0,"next":null,"reason":"x"}"#, "wrong-type"),
            (br#"{"ok":true,"blocks":4294967296,"dropped":0,"next":null,"reason":"x"}"#, "wrong-type"),
            (br#"{"ok":true,"blocks":"1","dropped":0,"next":null,"reason":"x"}"#, "wrong-type"),
            (br#"{"ok":true,"blocks":1,"dropped":0,"next":1,"reason":"x"}"#, "wrong-type"),
            (br#"{"ok":true,"blocks":1,"dropped":0,"next":null,"reason":"x","error":{}}"#, "unknown-key"),
            (br#"{"ok":false,"blocks":1,"dropped":0,"reason":"x"}"#, "unknown-key"),
            (br#"{"ok":false,"blocks":1,"dropped":0,"error":{"code":"x"}}"#, "missing-key"),
        ];
        for (payload, expected) in table {
            assert_eq!(kind(check_end(payload)), expected, "{}", String::from_utf8_lossy(payload));
        }
    }

    #[test]
    fn events_need_a_string_event_and_closing_has_exactly_a_reason() {
        let closing = br#"{"event":"closing","reason":"idle"}"#;
        assert_eq!(text(closing, &check_evt(closing).expect("valid").event), "closing");
        let other = br#"{"event":"span","a":[1],"b":{}}"#;
        assert_eq!(kind(check_evt(other)), "ok");
        let table: [(&[u8], &str); 6] = [
            (br#"{"reason":"idle"}"#, "missing-key"),
            (br#"{"event":1}"#, "wrong-type"),
            (br#"{"event":"closing"}"#, "missing-key"),
            (br#"{"event":"closing","reason":1}"#, "wrong-type"),
            (br#"{"event":"closing","reason":"x","more":1}"#, "unknown-key"),
            (br#"{"event":"closing","event":"closing","reason":"x"}"#, "duplicate-key"),
        ];
        for (payload, expected) in table {
            assert_eq!(kind(check_evt(payload)), expected, "{}", String::from_utf8_lossy(payload));
        }
    }

    #[test]
    fn an_escaped_key_spelling_is_an_unknown_key_and_names_are_exact() {
        let escaped = br#"{"verb":"x","args":{}}"#;
        assert_eq!(kind(check_req(escaped, false)), "unknown-key");
        let upper = br#"{"Verb":"x","args":{}}"#;
        assert_eq!(kind(check_req(upper, false)), "unknown-key");
    }
}
