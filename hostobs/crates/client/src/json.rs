//! Serde-free JSON text helpers (WIRE.md §4.2).
//!
//! The envelope scanner of `hostobs-proto` validates a payload and returns byte ranges; this
//! module reads the few values the client needs out of those ranges: strings (unescaped),
//! non-negative integers, nested objects and arrays of strings. Every function takes text that
//! the scanner already judged to be one valid JSON object, and none of them panics on any
//! input: a value that is not what was asked for is `None`.

use core::str::Chars;

use hostobs_proto::envelope::{check_req, members_up_to, EnvelopeError, Kind, Member};

use crate::error::ClientError;

/// The `proto` value of a `hello` request (WIRE.md §4.4): the client's own major and minor.
const HELLO_PROTO: &str = "{\"major\":1,\"minor\":2}";

/// The most bytes a verb may have.
const MAX_VERB_BYTES: usize = 64;

/// The most keys an object the client reads by key may have: the `hello` result has 9 and its
/// `limits` object at most 25 (WIRE.md §10); the bound keeps the duplicate-key check, which is
/// quadratic in the key count, finite on hostile input.
pub(crate) const MAX_OBJECT_MEMBERS: usize = 64;

/// The members of one JSON object and the text they index into.
pub(crate) struct Object<'a> {
    text: &'a str,
    members: Vec<Member>,
}

impl<'a> Object<'a> {
    /// The members of `text`, which must be exactly one JSON object of at most
    /// [`MAX_OBJECT_MEMBERS`] keys.
    pub(crate) fn parse(text: &'a str) -> Result<Object<'a>, EnvelopeError> {
        let members = members_up_to(text.as_bytes(), MAX_OBJECT_MEMBERS)?;
        Ok(Object { text, members })
    }

    /// The raw text and type of the value of `key`.
    fn find(&self, key: &str) -> Option<(&'a str, Kind)> {
        let member = self
            .members
            .iter()
            .find(|member| self.text.get(member.key.clone()) == Some(key))?;
        let raw = self.text.get(member.value.clone())?;
        Some((raw, member.kind))
    }

    /// The raw text of the value of `key` when it has type `kind`.
    fn of_kind(&self, key: &str, kind: Kind) -> Option<&'a str> {
        let (raw, found) = self.find(key)?;
        (found == kind).then_some(raw)
    }

    /// The number of members.
    pub(crate) fn len(&self) -> usize {
        self.members.len()
    }

    /// The type of the value of `key`.
    pub(crate) fn kind(&self, key: &str) -> Option<Kind> {
        self.find(key).map(|(_, kind)| kind)
    }

    /// The raw text of the value of `key`, whatever its type.
    pub(crate) fn raw(&self, key: &str) -> Option<&'a str> {
        self.find(key).map(|(raw, _)| raw)
    }

    /// The string value of `key`, unescaped.
    pub(crate) fn string(&self, key: &str) -> Option<String> {
        self.of_kind(key, Kind::Str).map(unescape)
    }

    /// The value of `key` as an exact non-negative integer.
    pub(crate) fn u64(&self, key: &str) -> Option<u64> {
        self.of_kind(key, Kind::Number)?.parse::<u64>().ok()
    }

    /// The value of `key` as an exact `u32`.
    pub(crate) fn u32(&self, key: &str) -> Option<u32> {
        u32::try_from(self.u64(key)?).ok()
    }

    /// The object value of `key`.
    pub(crate) fn object(&self, key: &str) -> Option<Object<'a>> {
        Object::parse(self.of_kind(key, Kind::Object)?).ok()
    }

    /// The array value of `key` when every element is a string.
    pub(crate) fn strings(&self, key: &str) -> Option<Vec<String>> {
        array_strings(self.of_kind(key, Kind::Array)?)
    }
}

/// The UTF-16 unit an escape sequence stands for; `chars` is positioned after the backslash.
/// The scanner only lets valid escapes through, so the fallbacks are formalities.
fn escape_unit(chars: &mut Chars<'_>) -> u16 {
    match chars.next() {
        Some('b') => 0x08,
        Some('f') => 0x0C,
        Some('n') => 0x0A,
        Some('r') => 0x0D,
        Some('t') => 0x09,
        Some('u') => {
            let mut value: u16 = 0;
            for _ in 0..4 {
                let digit = chars.next().and_then(|c| c.to_digit(16));
                value = value * 16 + u16::try_from(digit.unwrap_or(0)).unwrap_or(0);
            }
            value
        }
        Some(other) => u16::try_from(u32::from(other)).unwrap_or(0xFFFD),
        None => 0xFFFD,
    }
}

/// The text of a JSON string value `quoted` (quotes included) with its escapes resolved. A
/// lone surrogate escape becomes U+FFFD.
pub(crate) fn unescape(quoted: &str) -> String {
    let end = quoted.len().saturating_sub(1);
    let inner = quoted.get(1..end).unwrap_or_default();
    let mut units: Vec<u16> = Vec::with_capacity(inner.len());
    let mut chars = inner.chars();
    while let Some(c) = chars.next() {
        if c == '\\' {
            units.push(escape_unit(&mut chars));
        } else {
            let mut buffer = [0u16; 2];
            units.extend_from_slice(c.encode_utf16(&mut buffer));
        }
    }
    String::from_utf16_lossy(&units)
}

/// The index of the first non-whitespace byte at or after `at`.
fn skip_ws(bytes: &[u8], mut at: usize) -> usize {
    while matches!(bytes.get(at), Some(b' ' | b'\t' | b'\n' | b'\r')) {
        at += 1;
    }
    at
}

/// The index just past the string that opens at `open`, or `None` when it does not end.
fn string_end(bytes: &[u8], open: usize) -> Option<usize> {
    let mut at = open + 1;
    loop {
        match bytes.get(at)? {
            b'\\' => at += 2,
            b'"' => return Some(at + 1),
            _ => at += 1,
        }
    }
}

/// The elements of the JSON array `raw` when every element is a string.
fn array_strings(raw: &str) -> Option<Vec<String>> {
    let bytes = raw.as_bytes();
    let mut out = Vec::new();
    let mut at = skip_ws(bytes, 1);
    if bytes.get(at) == Some(&b']') {
        return Some(out);
    }
    loop {
        if bytes.get(at) != Some(&b'"') {
            return None;
        }
        let end = string_end(bytes, at)?;
        out.push(unescape(raw.get(at..end)?));
        at = skip_ws(bytes, end);
        match bytes.get(at) {
            Some(b',') => at = skip_ws(bytes, at + 1),
            Some(b']') => return Some(out),
            _ => return None,
        }
    }
}

/// The request payload `{"verb": verb, "args": args_json}` after the local checks.
///
/// # Errors
///
/// `Usage("bad-verb")` when `verb` is not 1..=64 bytes of `[a-z0-9_.-]`, and
/// `Usage("bad-args")` when the payload is not one JSON object that the envelope rules of
/// WIRE.md §4.2 accept (a text error, nesting deeper than 8 counting the envelope, `args` not an
/// object, or anything outside the two envelope keys).
pub(crate) fn request_payload(verb: &str, args_json: &str) -> Result<String, ClientError> {
    let name_ok = verb
        .bytes()
        .all(|b| matches!(b, b'a'..=b'z' | b'0'..=b'9' | b'_' | b'.' | b'-'));
    if verb.is_empty() || verb.len() > MAX_VERB_BYTES || !name_ok {
        return Err(ClientError::Usage("bad-verb"));
    }
    let payload = format!("{{\"verb\":\"{verb}\",\"args\":{args_json}}}");
    check_req(payload.as_bytes(), false).map_err(|_| ClientError::Usage("bad-args"))?;
    Ok(payload)
}

/// The `args` of the `hello` request for the client name `name` (already validated: no
/// control character, so only `"` and `\` need an escape).
pub(crate) fn hello_args(name: &str) -> String {
    let mut quoted = String::with_capacity(name.len() + 2);
    for c in name.chars() {
        if c == '"' || c == '\\' {
            quoted.push('\\');
        }
        quoted.push(c);
    }
    format!("{{\"client\":\"{quoted}\",\"proto\":{HELLO_PROTO}}}")
}

#[cfg(test)]
mod tests {
    use super::{
        array_strings, hello_args, request_payload, skip_ws, string_end, unescape, Object,
        HELLO_PROTO, MAX_VERB_BYTES,
    };
    use crate::error::ClientError;
    use hostobs_proto::envelope::Kind;
    use hostobs_proto::frame::{PROTO_MAJOR, PROTO_MINOR};
    use std::sync::mpsc;
    use std::time::Duration;

    /// A JSON unicode escape built at run time: the source never spells one out.
    fn u_escape(unit: u16) -> String {
        format!("{}u{:04x}", '\\', unit)
    }

    #[test]
    fn the_hello_proto_text_names_the_crates_own_version() {
        let expected = format!("{{\"major\":{PROTO_MAJOR},\"minor\":{PROTO_MINOR}}}");
        assert_eq!(HELLO_PROTO, expected);
    }

    #[test]
    fn unescape_resolves_every_escape() {
        let bs = '\\';
        let text = format!("\"a{bs}\"{bs}{bs}{bs}/{bs}b{bs}f{bs}n{bs}r{bs}t\"");
        assert_eq!(unescape(&text), "a\"\\/\u{8}\u{c}\n\r\t");
        let pair = format!("\"{}{}\"", u_escape(0xD83D), u_escape(0xDE00));
        assert_eq!(unescape(&pair), "\u{1F600}");
        let lone = format!("\"x{}y\"", u_escape(0xD800));
        assert_eq!(unescape(&lone), "x\u{FFFD}y");
        let bmp = format!("\"{}\"", u_escape(0x0076));
        assert_eq!(unescape(&bmp), "v");
        assert_eq!(unescape("\"\u{e9}\""), "\u{e9}");
        assert_eq!(unescape("\"\""), "");
        assert_eq!(unescape(""), "");
    }

    #[test]
    fn unescape_survives_malformed_escapes() {
        let bs = '\\';
        assert_eq!(unescape(&format!("\"{bs}\"")), "\u{FFFD}");
        assert_eq!(unescape(&format!("\"{bs}u12\"")).chars().count(), 1);
        assert_eq!(unescape(&format!("\"{bs}\u{1F600}\"")), "\u{FFFD}");
        assert_eq!(unescape(&format!("\"{bs}\u{20ac}\"")), "\u{20ac}");
    }

    #[test]
    fn object_accessors_return_typed_values() {
        let text = "{\"s\":\"v\",\"n\":42,\"big\":18446744073709551615,\"f\":1.5,\
                    \"o\":{\"k\":1},\"a\":[\"x\",\"y\"],\"t\":true}";
        let object = Object::parse(text).expect("object");
        assert_eq!(object.len(), 7);
        assert_eq!(object.string("s").as_deref(), Some("v"));
        assert_eq!(object.string("n"), None);
        assert_eq!(object.u64("n"), Some(42));
        assert_eq!(object.u64("big"), Some(u64::MAX));
        assert_eq!(object.u32("n"), Some(42));
        assert_eq!(object.u32("big"), None);
        assert_eq!(object.u64("f"), None);
        assert_eq!(object.u64("missing"), None);
        assert_eq!(object.object("o").and_then(|o| o.u32("k")), Some(1));
        assert!(object.object("a").is_none());
        assert_eq!(object.kind("t"), Some(Kind::True));
        assert_eq!(object.raw("o"), Some("{\"k\":1}"));
        assert_eq!(object.kind("zz"), None);
        let list = object.strings("a").expect("strings");
        assert_eq!(list, ["x", "y"]);
        assert_eq!(object.strings("s"), None);
        assert!(Object::parse("[1]").is_err());
    }

    #[test]
    fn array_strings_accepts_strings_only() {
        assert_eq!(array_strings("[]"), Some(Vec::new()));
        assert_eq!(array_strings("[ ]"), Some(Vec::new()));
        let spaced = array_strings("[ \"a\" , \"b\\\"c\" ]").expect("two");
        assert_eq!(spaced, ["a", "b\"c"]);
        assert_eq!(array_strings("[1]"), None);
        assert_eq!(array_strings("[\"a\",]"), None);
        assert_eq!(array_strings("[\"a\" \"b\"]"), None);
        assert_eq!(array_strings("[\"a"), None);
        assert_eq!(array_strings("[\"a\\"), None);
        assert_eq!(array_strings("[\"a\""), None);
    }

    #[test]
    fn request_payload_checks_verb_and_args() {
        let ok = request_payload("session.start", "{\"a\":1}").expect("ok");
        assert_eq!(ok, "{\"verb\":\"session.start\",\"args\":{\"a\":1}}");
        for verb in ["", "Status", "a b", "x\"y"] {
            let error = request_payload(verb, "{}");
            assert_eq!(error, Err(ClientError::Usage("bad-verb")));
        }
        assert_eq!(MAX_VERB_BYTES, 64);
        let long = "a".repeat(65);
        assert_eq!(
            request_payload(&long, "{}"),
            Err(ClientError::Usage("bad-verb"))
        );
        let longest = "a".repeat(64);
        assert!(request_payload(&longest, "{}").is_ok());
        let bad_args = ["[]", "{", "{} x", "{},\"x\":1", "{\"a\":nul}", ""];
        for args in bad_args {
            let error = request_payload("status", args);
            assert_eq!(error, Err(ClientError::Usage("bad-args")), "{args}");
        }
    }

    #[test]
    fn request_payload_bounds_the_nesting_depth() {
        let nest = |depth: usize| format!("{}1{}", "{\"a\":".repeat(depth), "}".repeat(depth));
        assert!(request_payload("status", &nest(7)).is_ok());
        assert!(request_payload("status", &nest(8)).is_err());
    }

    #[test]
    fn hello_args_escapes_quote_and_backslash() {
        assert_eq!(
            hello_args("probe"),
            "{\"client\":\"probe\",\"proto\":{\"major\":1,\"minor\":2}}"
        );
        let escaped = hello_args("a\"b\\c");
        assert!(escaped.starts_with("{\"client\":\"a\\\"b\\\\c\""));
        assert!(Object::parse(&escaped).is_ok());
    }

    #[test]
    fn skip_ws_always_makes_progress() {
        let (sender, receiver) = mpsc::channel();
        std::thread::spawn(move || {
            let _ = sender.send(skip_ws(b"  x", 0));
        });
        let result = receiver
            .recv_timeout(Duration::from_secs(2))
            .expect("skip_ws did not return");
        assert_eq!(result, 2);
    }

    #[test]
    fn string_end_always_makes_progress() {
        let (sender, receiver) = mpsc::channel();
        std::thread::spawn(move || {
            let _ = sender.send(string_end(b"\"a\"", 0));
        });
        let result = receiver
            .recv_timeout(Duration::from_secs(2))
            .expect("string_end did not return");
        assert_eq!(result, Some(3));
    }
}
