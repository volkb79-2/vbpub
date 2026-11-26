//! A strict, dependency-free JSON reader and writer for the wire vocabulary.
//!
//! Why hand-rolled: thrashguard declares no third-party crate (design §5), and
//! the Rust gate image caches only hostobs's registry set, so a `serde_json`
//! dependency would need an image rebuild. The wire needs no more than this:
//! newline-delimited objects whose keys are known and whose values are
//! `string`, `integer`, `bool`, `null`, `array` and `object`.
//!
//! Contracts:
//!
//! - **Unknown keys are rejected by the caller, not silently kept.** This
//!   module returns every member of an object; [`Value::into_object`] enforces
//!   the closed key set (design §3: unknown keys are refused).
//! - **No panic on untrusted bytes.** Every failure is a [`JsonError`].
//! - **Output is deterministic.** Object members are written in the order the
//!   caller supplies them, so a test can compare bytes.

use core::fmt;

/// The largest accepted input document (one wire line or one config value).
pub const MAX_DOCUMENT_BYTES: usize = 64 * 1024;
/// The maximum number of nested arrays/objects accepted in a document.
pub const MAX_DEPTH: usize = 32;

/// A JSON value.
#[derive(Clone, Debug, PartialEq, Eq)]
pub enum Value {
    /// `null`.
    Null,
    /// `true` or `false`.
    Bool(bool),
    /// A signed 64-bit integer (the only number form this wire uses).
    Integer(i64),
    /// A string, already unescaped.
    String(String),
    /// An array of values.
    Array(Vec<Value>),
    /// An object, in source order.
    Object(Vec<(String, Value)>),
}

/// Why a document could not be read.
#[derive(Clone, Debug, PartialEq, Eq)]
pub enum JsonError {
    /// The document exceeded [`MAX_DOCUMENT_BYTES`].
    TooLarge {
        /// The observed length.
        bytes: usize,
    },
    /// A token was malformed at this byte offset.
    Syntax {
        /// Byte offset in the document.
        at: usize,
        /// What was found instead of the expected token.
        detail: String,
    },
    /// The document ended before the value was complete.
    UnexpectedEnd,
    /// A member was expected but the object was not closed.
    TrailingData {
        /// Byte offset of the first unexpected byte.
        at: usize,
    },
}

impl fmt::Display for JsonError {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        match self {
            Self::TooLarge { bytes } => write!(
                f,
                "document is {bytes} bytes, above the {MAX_DOCUMENT_BYTES} byte bound"
            ),
            Self::Syntax { at, detail } => write!(f, "malformed JSON at byte {at}: {detail}"),
            Self::UnexpectedEnd => f.write_str("JSON document ended before the value was complete"),
            Self::TrailingData { at } => {
                write!(f, "trailing data at byte {at} after the JSON value")
            }
        }
    }
}

/// A refusal from [`Value::into_object`]: the object carries a key the caller does not know.
#[derive(Clone, Debug, PartialEq, Eq)]
pub struct UnknownKey(pub String);

impl fmt::Display for UnknownKey {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        write!(f, "unknown key {:?}", self.0)
    }
}

impl Value {
    /// Read one complete document. Trailing non-whitespace bytes are an error.
    pub fn parse(text: &str) -> Result<Self, JsonError> {
        if text.len() > MAX_DOCUMENT_BYTES {
            return Err(JsonError::TooLarge { bytes: text.len() });
        }
        let mut reader = Reader {
            bytes: text.as_bytes(),
            at: 0,
        };
        let value = reader.value(0)?;
        reader.skip_whitespace();
        if reader.at != reader.bytes.len() {
            return Err(JsonError::TrailingData { at: reader.at });
        }
        Ok(value)
    }

    /// A borrowed view of the string, or `None` for another kind.
    pub fn as_str(&self) -> Option<&str> {
        match self {
            Self::String(value) => Some(value),
            _ => None,
        }
    }

    /// The integer, or `None` for another kind.
    pub fn as_i64(&self) -> Option<i64> {
        match self {
            Self::Integer(value) => Some(*value),
            _ => None,
        }
    }

    /// The boolean, or `None` for another kind.
    pub fn as_bool(&self) -> Option<bool> {
        match self {
            Self::Bool(value) => Some(*value),
            _ => None,
        }
    }

    /// The array items, or `None` for another kind.
    pub fn as_array(&self) -> Option<&[Value]> {
        match self {
            Self::Array(items) => Some(items),
            _ => None,
        }
    }

    /// The object members, or `None` for another kind.
    pub fn members(&self) -> Option<&[(String, Value)]> {
        match self {
            Self::Object(pairs) => Some(pairs),
            _ => None,
        }
    }

    /// Reject any member not named in `known`, then hand back the members.
    ///
    /// The first refusal names the offending key, so a client learns which key
    /// it must not send rather than only that its request was refused.
    pub fn into_object(self, known: &[&str]) -> Result<Vec<(String, Value)>, UnknownKey> {
        match self {
            Self::Object(pairs) => {
                for (key, _) in &pairs {
                    if !known.contains(&key.as_str()) {
                        return Err(UnknownKey(key.clone()));
                    }
                }
                Ok(pairs)
            }
            other => Err(UnknownKey(format!("<not an object: {other:?}>"))),
        }
    }

    /// Look a key up in an object value.
    pub fn get(&self, key: &str) -> Option<&Value> {
        self.members()?
            .iter()
            .find(|(name, _)| name == key)
            .map(|(_, value)| value)
    }

    /// The JSON text of this value, with members in the given (source) order.
    pub fn to_json(&self) -> String {
        let mut out = String::new();
        self.write(&mut out);
        out
    }

    fn write(&self, out: &mut String) {
        match self {
            Self::Null => out.push_str("null"),
            Self::Bool(true) => out.push_str("true"),
            Self::Bool(false) => out.push_str("false"),
            Self::Integer(value) => out.push_str(&value.to_string()),
            Self::String(value) => write_string(value, out),
            Self::Array(items) => {
                out.push('[');
                for (index, item) in items.iter().enumerate() {
                    if index > 0 {
                        out.push(',');
                    }
                    item.write(out);
                }
                out.push(']');
            }
            Self::Object(pairs) => {
                out.push('{');
                for (index, (key, value)) in pairs.iter().enumerate() {
                    if index > 0 {
                        out.push(',');
                    }
                    write_string(key, out);
                    out.push(':');
                    value.write(out);
                }
                out.push('}');
            }
        }
    }
}

fn write_string(value: &str, out: &mut String) {
    out.push('"');
    for character in value.chars() {
        match character {
            '"' => out.push_str("\\\""),
            '\\' => out.push_str("\\\\"),
            '\n' => out.push_str("\\n"),
            '\r' => out.push_str("\\r"),
            '\t' => out.push_str("\\t"),
            '\u{8}' => out.push_str("\\b"),
            '\u{c}' => out.push_str("\\f"),
            other if other < '\u{20}' => out.push_str(&format!("\\u{:04x}", other as u32)),
            other => out.push(other),
        }
    }
    out.push('"');
}

struct Reader<'a> {
    bytes: &'a [u8],
    at: usize,
}

fn push_utf8(out: &mut String, bytes: &[u8], start: usize, end: usize) -> Result<(), JsonError> {
    let slice = bytes.get(start..end).ok_or(JsonError::UnexpectedEnd)?;
    let text = core::str::from_utf8(slice).map_err(|error| JsonError::Syntax {
        at: start + error.valid_up_to(),
        detail: "string is not UTF-8".into(),
    })?;
    out.push_str(text);
    Ok(())
}

impl Reader<'_> {
    fn skip_whitespace(&mut self) {
        while let Some(byte) = self.bytes.get(self.at) {
            if matches!(byte, b' ' | b'\t' | b'\r' | b'\n') {
                self.at += 1;
            } else {
                break;
            }
        }
    }

    fn syntax(&self, detail: impl Into<String>) -> JsonError {
        JsonError::Syntax {
            at: self.at,
            detail: detail.into(),
        }
    }

    fn peek(&self) -> Option<u8> {
        self.bytes.get(self.at).copied()
    }

    fn expect(&mut self, literal: &str) -> Result<(), JsonError> {
        let end = self.at + literal.len();
        if self.bytes.get(self.at..end) == Some(literal.as_bytes()) {
            self.at = end;
            Ok(())
        } else {
            Err(self.syntax(format!("expected {literal:?}")))
        }
    }

    fn value(&mut self, depth: usize) -> Result<Value, JsonError> {
        self.skip_whitespace();
        match self.peek() {
            None => Err(JsonError::UnexpectedEnd),
            Some(b'{') => self.object(depth + 1),
            Some(b'[') => self.array(depth + 1),
            Some(b'"') => Ok(Value::String(self.string()?)),
            Some(b't') => {
                self.expect("true")?;
                Ok(Value::Bool(true))
            }
            Some(b'f') => {
                self.expect("false")?;
                Ok(Value::Bool(false))
            }
            Some(b'n') => {
                self.expect("null")?;
                Ok(Value::Null)
            }
            Some(b'-' | b'0'..=b'9') => self.integer(),
            Some(byte) => Err(self.syntax(format!("unexpected byte {byte:#04x}"))),
        }
    }

    fn object(&mut self, depth: usize) -> Result<Value, JsonError> {
        self.check_depth(depth)?;
        self.expect("{")?;
        let mut pairs = Vec::new();
        self.skip_whitespace();
        if self.peek() == Some(b'}') {
            self.at += 1;
            return Ok(Value::Object(pairs));
        }
        loop {
            self.skip_whitespace();
            let key = self.string()?;
            self.skip_whitespace();
            self.expect(":")?;
            let value = self.value(depth)?;
            if pairs.iter().any(|(existing, _)| existing == &key) {
                return Err(self.syntax(format!("duplicate key {key:?}")));
            }
            pairs.push((key, value));
            self.skip_whitespace();
            match self.peek() {
                Some(b',') => self.at += 1,
                Some(b'}') => {
                    self.at += 1;
                    return Ok(Value::Object(pairs));
                }
                None => return Err(JsonError::UnexpectedEnd),
                Some(byte) => {
                    return Err(self.syntax(format!("expected ',' or '}}', found {byte:#04x}")))
                }
            }
        }
    }

    fn array(&mut self, depth: usize) -> Result<Value, JsonError> {
        self.check_depth(depth)?;
        self.expect("[")?;
        let mut items = Vec::new();
        self.skip_whitespace();
        if self.peek() == Some(b']') {
            self.at += 1;
            return Ok(Value::Array(items));
        }
        loop {
            items.push(self.value(depth)?);
            self.skip_whitespace();
            match self.peek() {
                Some(b',') => self.at += 1,
                Some(b']') => {
                    self.at += 1;
                    return Ok(Value::Array(items));
                }
                None => return Err(JsonError::UnexpectedEnd),
                Some(byte) => {
                    return Err(self.syntax(format!("expected ',' or ']', found {byte:#04x}")))
                }
            }
        }
    }

    fn check_depth(&self, depth: usize) -> Result<(), JsonError> {
        if depth > MAX_DEPTH {
            Err(self.syntax(format!("deeper than {MAX_DEPTH} levels")))
        } else {
            Ok(())
        }
    }

    fn string(&mut self) -> Result<String, JsonError> {
        self.expect("\"")?;
        let mut out = String::new();
        // The raw bytes between two escapes are taken as one UTF-8 slice: converting a
        // multi-byte character byte by byte would mangle it, and a wire line is UTF-8 by
        // contract (design §3: newline-delimited JSON, read by a person as well as a client).
        let mut chunk_start = self.at;
        loop {
            let Some(byte) = self.peek() else {
                return Err(JsonError::UnexpectedEnd);
            };
            self.at += 1;
            match byte {
                b'"' => {
                    push_utf8(&mut out, self.bytes, chunk_start, self.at - 1)?;
                    return Ok(out);
                }
                b'\\' => {
                    push_utf8(&mut out, self.bytes, chunk_start, self.at - 1)?;
                    let Some(escape) = self.peek() else {
                        return Err(JsonError::UnexpectedEnd);
                    };
                    self.at += 1;
                    match escape {
                        b'"' => out.push('"'),
                        b'\\' => out.push('\\'),
                        b'/' => out.push('/'),
                        b'n' => out.push('\n'),
                        b'r' => out.push('\r'),
                        b't' => out.push('\t'),
                        b'b' => out.push('\u{8}'),
                        b'f' => out.push('\u{c}'),
                        b'u' => out.push(self.unicode_escape()?),
                        other => return Err(self.syntax(format!("unknown escape \\{other}"))),
                    }
                    chunk_start = self.at;
                }
                0x00..=0x1f => return Err(self.syntax("raw control character in a string")),
                _ => {}
            }
        }
    }

    fn unicode_escape(&mut self) -> Result<char, JsonError> {
        let digits = self
            .bytes
            .get(self.at..self.at + 4)
            .ok_or(JsonError::UnexpectedEnd)?;
        let text = core::str::from_utf8(digits).map_err(|_| self.syntax("non-UTF-8 escape"))?;
        let code = u32::from_str_radix(text, 16)
            .map_err(|_| self.syntax(format!("bad \\u escape {text:?}")))?;
        self.at += 4;
        char::from_u32(code).ok_or_else(|| self.syntax(format!("invalid code point U+{code:04X}")))
    }

    fn integer(&mut self) -> Result<Value, JsonError> {
        let start = self.at;
        if self.peek() == Some(b'-') {
            self.at += 1;
        }
        while matches!(self.peek(), Some(b'0'..=b'9')) {
            self.at += 1;
        }
        let text = core::str::from_utf8(&self.bytes[start..self.at])
            .map_err(|_| self.syntax("non-UTF-8 number"))?;
        text.parse::<i64>()
            .map(Value::Integer)
            .map_err(|_| JsonError::Syntax {
                at: start,
                detail: format!("integer {text:?} is out of range or has a fraction"),
            })
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn round_trips_every_wire_shape() {
        let text = r#"{"v":"x/1","n":-12,"b":true,"nil":null,"a":["s",1],"o":{"k":"v"}}"#;
        let value = Value::parse(text).expect("parses");
        assert_eq!(value.to_json(), text);
    }

    #[test]
    fn escapes_survive_a_round_trip() {
        let value = Value::Object(vec![("k".into(), Value::String("q\"b\\c\nd\te".into()))]);
        assert_eq!(Value::parse(&value.to_json()).expect("parses"), value);
        let unicode = Value::Object(vec![("k".into(), Value::String("\u{1f600}".into()))]);
        assert_eq!(Value::parse(&unicode.to_json()).expect("parses"), unicode);
    }

    #[test]
    fn refuses_trailing_data_duplicates_and_oversize_documents() {
        assert_eq!(Value::parse("{} x"), Err(JsonError::TrailingData { at: 3 }));
        assert!(matches!(
            Value::parse(r#"{"a":1,"a":2}"#),
            Err(JsonError::Syntax { .. })
        ));
        let big = format!("[\"{}\"]", "x".repeat(MAX_DOCUMENT_BYTES));
        assert!(matches!(
            Value::parse(&big),
            Err(JsonError::TooLarge { .. })
        ));
    }

    #[test]
    fn nesting_at_32_levels_parses_but_33_levels_is_refused() {
        let at_limit = format!("{}0{}", "[".repeat(32), "]".repeat(32));
        assert!(Value::parse(&at_limit).is_ok());
        let too_deep = format!("{}0{}", "[".repeat(33), "]".repeat(33));
        assert!(matches!(
            Value::parse(&too_deep),
            Err(JsonError::Syntax { ref detail, .. }) if detail.contains("deeper than 32 levels")
        ));
    }

    #[test]
    fn unknown_keys_are_named() {
        let value = Value::parse(r#"{"id":"a","ttl_s":3}"#).expect("parses");
        assert_eq!(value.into_object(&["id"]), Err(UnknownKey("ttl_s".into())));
    }
}
