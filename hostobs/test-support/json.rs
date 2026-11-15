//! A tiny JSON reader for the golden fixtures (test support only).
//!
//! The workspace may not add a `serde_json` *dev-dependency* to `hostobs-proto`: the lane
//! image caches exactly the committed `Cargo.lock`, and a new dependency edge would
//! change it and demand an image rebuild (assay Rust methodology, dependency model).
//! `serde_json` is therefore not available to these tests, and the fixtures are parsed by
//! this module instead. It is a **test helper**, not a product component: it is not part
//! of the codec contract and nothing in `hostobs-proto`'s library depends on it.
//!
//! It parses the JSON subset the fixtures use — objects, arrays, strings with `\u`
//! escapes, integers, booleans and `null` — and every malformed input is a typed error
//! rather than a panic.

use std::collections::BTreeMap;

#[derive(Clone, Debug, PartialEq)]
pub enum Json {
    Null,
    Bool(bool),
    Number(i128),
    String(String),
    Array(Vec<Json>),
    Object(BTreeMap<String, Json>),
}

#[derive(Clone, Debug, Eq, PartialEq)]
pub struct JsonError {
    pub position: usize,
    pub message: &'static str,
}

impl Json {
    pub fn get(&self, key: &str) -> Option<&Json> {
        match self {
            Json::Object(map) => map.get(key),
            _ => None,
        }
    }

    pub fn as_str(&self) -> Option<&str> {
        match self {
            Json::String(text) => Some(text),
            _ => None,
        }
    }

    pub fn as_i128(&self) -> Option<i128> {
        match self {
            Json::Number(value) => Some(*value),
            _ => None,
        }
    }

    pub fn as_u64(&self) -> Option<u64> {
        u64::try_from(self.as_i128()?).ok()
    }

    #[allow(dead_code, reason = "only the fixture readers that need it call this")]
    pub fn as_usize(&self) -> Option<usize> {
        usize::try_from(self.as_i128()?).ok()
    }

    pub fn as_array(&self) -> Option<&[Json]> {
        match self {
            Json::Array(items) => Some(items),
            _ => None,
        }
    }

    pub fn field(&self, key: &str) -> &Json {
        static NULL: Json = Json::Null;
        self.get(key).unwrap_or(&NULL)
    }
}

struct Reader<'a> {
    bytes: &'a [u8],
    pos: usize,
}

impl<'a> Reader<'a> {
    fn error(&self, message: &'static str) -> JsonError {
        JsonError {
            position: self.pos,
            message,
        }
    }

    fn peek(&self) -> Option<u8> {
        self.bytes.get(self.pos).copied()
    }

    fn skip_ws(&mut self) {
        while matches!(self.peek(), Some(b' ' | b'\t' | b'\n' | b'\r')) {
            self.pos += 1;
        }
    }

    fn expect(&mut self, byte: u8) -> Result<(), JsonError> {
        if self.peek() != Some(byte) {
            return Err(self.error("unexpected character"));
        }
        self.pos += 1;
        Ok(())
    }

    fn value(&mut self) -> Result<Json, JsonError> {
        self.skip_ws();
        match self.peek() {
            Some(b'{') => self.object(),
            Some(b'[') => self.array(),
            Some(b'"') => self.string().map(Json::String),
            Some(b't') => self.literal("true", Json::Bool(true)),
            Some(b'f') => self.literal("false", Json::Bool(false)),
            Some(b'n') => self.literal("null", Json::Null),
            Some(b'-' | b'0'..=b'9') => self.number(),
            _ => Err(self.error("unexpected token")),
        }
    }

    fn literal(&mut self, text: &str, value: Json) -> Result<Json, JsonError> {
        let end = self.pos.saturating_add(text.len());
        if self.bytes.get(self.pos..end).map(<[u8]>::to_vec) != Some(text.as_bytes().to_vec()) {
            return Err(self.error("unexpected literal"));
        }
        self.pos = end;
        Ok(value)
    }

    fn object(&mut self) -> Result<Json, JsonError> {
        self.expect(b'{')?;
        let mut map = BTreeMap::new();
        self.skip_ws();
        if self.peek() == Some(b'}') {
            self.pos += 1;
            return Ok(Json::Object(map));
        }
        loop {
            self.skip_ws();
            let key = self.string()?;
            self.skip_ws();
            self.expect(b':')?;
            let value = self.value()?;
            map.insert(key, value);
            self.skip_ws();
            match self.peek() {
                Some(b',') => self.pos += 1,
                Some(b'}') => {
                    self.pos += 1;
                    return Ok(Json::Object(map));
                }
                _ => return Err(self.error("expected , or }")),
            }
        }
    }

    fn array(&mut self) -> Result<Json, JsonError> {
        self.expect(b'[')?;
        let mut items = Vec::new();
        self.skip_ws();
        if self.peek() == Some(b']') {
            self.pos += 1;
            return Ok(Json::Array(items));
        }
        loop {
            items.push(self.value()?);
            self.skip_ws();
            match self.peek() {
                Some(b',') => self.pos += 1,
                Some(b']') => {
                    self.pos += 1;
                    return Ok(Json::Array(items));
                }
                _ => return Err(self.error("expected , or ]")),
            }
        }
    }

    fn string(&mut self) -> Result<String, JsonError> {
        self.expect(b'"')?;
        // Bytes are accumulated raw and decoded as UTF-8 at the end: a multi-byte character
        // must survive as one character, not as one character per byte.
        let mut out: Vec<u8> = Vec::new();
        loop {
            let Some(byte) = self.peek() else {
                return Err(self.error("unterminated string"));
            };
            self.pos += 1;
            match byte {
                b'"' => {
                    return String::from_utf8(out)
                        .map_err(|_| self.error("invalid UTF-8 in string"))
                }
                b'\\' => {
                    let Some(escape) = self.peek() else {
                        return Err(self.error("unterminated escape"));
                    };
                    self.pos += 1;
                    match escape {
                        b'"' => out.push(b'"'),
                        b'\\' => out.push(b'\\'),
                        b'/' => out.push(b'/'),
                        b'n' => out.push(b'\n'),
                        b't' => out.push(b'\t'),
                        b'r' => out.push(b'\r'),
                        b'b' => out.push(8),
                        b'f' => out.push(12),
                        b'u' => {
                            let digits = self
                                .bytes
                                .get(self.pos..self.pos + 4)
                                .ok_or_else(|| self.error("short \\u escape"))?;
                            let mut code = 0u32;
                            for digit in digits {
                                code = code * 16
                                    + match digit {
                                        b'0'..=b'9' => u32::from(*digit - b'0'),
                                        b'a'..=b'f' => u32::from(*digit - b'a') + 10,
                                        b'A'..=b'F' => u32::from(*digit - b'A') + 10,
                                        _ => return Err(self.error("bad \\u digit")),
                                    };
                            }
                            self.pos += 4;
                            let character =
                                char::from_u32(code).ok_or_else(|| self.error("bad code point"))?;
                            let mut encoded = [0u8; 4];
                            let bytes = character.encode_utf8(&mut encoded).as_bytes();
                            out.extend_from_slice(bytes);
                        }
                        _ => return Err(self.error("unknown escape")),
                    }
                }
                other => out.push(other),
            }
        }
    }

    fn number(&mut self) -> Result<Json, JsonError> {
        let start = self.pos;
        if self.peek() == Some(b'-') {
            self.pos += 1;
        }
        while matches!(self.peek(), Some(b'0'..=b'9')) {
            self.pos += 1;
        }
        if self.peek() == Some(b'.') {
            return Err(self.error("the fixture format uses integers only"));
        }
        let digits = self
            .bytes
            .get(start..self.pos)
            .ok_or_else(|| self.error("short number"))?;
        let text = String::from_utf8_lossy(digits);
        text.parse::<i128>()
            .map(Json::Number)
            .map_err(|_| self.error("number out of range"))
    }
}

/// Parse a JSON document.
pub fn parse(bytes: &[u8]) -> Result<Json, JsonError> {
    let mut reader = Reader { bytes, pos: 0 };
    let value = reader.value()?;
    reader.skip_ws();
    if reader.pos != bytes.len() {
        return Err(reader.error("trailing bytes after the document"));
    }
    Ok(value)
}
