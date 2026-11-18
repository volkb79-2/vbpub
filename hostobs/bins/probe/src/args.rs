//! The strict request-argument reader (design R4, R14 to R17) and the `hello` and `status`
//! argument schemas (WIRE.md sections 4.2, 4.4, 4.5, 8.3).
//!
//! Serde is not used for requests: it accepts duplicate keys in map-typed objects, its
//! recursion limit is not 8 and `detail.key` would have to be scraped from error text. The
//! reader sits on [`hostobs_proto::envelope::members_up_to`], which already refuses invalid
//! UTF-8, grammar defects, nesting beyond 8 and duplicate keys per object, and adds:
//!
//! - at most [`MAX_MEMBERS`] members per object and [`MAX_ELEMENTS`] elements per array
//!   (duplicate detection is quadratic per object, R19);
//! - a key containing a backslash is an unknown key (R15), so escaped spellings never need to
//!   be compared with plain ones;
//! - strings are unescaped here, and a lone or wrongly paired surrogate is refused, never
//!   replaced (R4: WIRE.md section 4.2 forbids U+FFFD substitution server-side);
//! - integers are digits only, at most 20 of them, with checked arithmetic and the field's own
//!   range (R16): a leading `-` on an unsigned field, `-0`, a fraction or an exponent is
//!   refused.
//!
//! Every refusal is an [`ArgError`] carrying the schema path of the offending member (R14): a
//! name from the schema, or an unknown or duplicate key echoed only when it is at most 64 bytes
//! of `[A-Za-z0-9_.-]`; otherwise the path stops at the parent. No input makes this module
//! panic or allocate beyond the request it was given.

use core::ops::Range;
use core::str::Chars;

use hostobs_proto::envelope::{members_up_to, EnvelopeError, Kind, Member};

/// Most members of any request object (the largest request object has 7).
pub const MAX_MEMBERS: usize = 16;

/// Most elements of any request array.
pub const MAX_ELEMENTS: usize = 64;

/// Longest decoded `hello.client`.
pub const MAX_CLIENT_BYTES: usize = 64;

/// Longest echoed key (R14).
pub const MAX_KEY_ECHO: usize = 64;

/// Most digits of an integer (`u64::MAX` has 20).
const MAX_DIGITS: usize = 20;

/// A refused argument: the schema path of the offending member, empty for a text defect.
#[derive(Clone, Debug, Eq, PartialEq)]
pub struct ArgError {
    /// The dotted path, for example `proto.minor`.
    pub key: String,
}

fn fail(path: &str) -> ArgError {
    ArgError {
        key: path.to_string(),
    }
}

fn join(path: &str, name: &str) -> String {
    if path.is_empty() {
        name.to_string()
    } else {
        format!("{path}.{name}")
    }
}

/// `key` as text when it may be echoed (R14): 1 to 64 bytes of `[A-Za-z0-9_.-]`.
#[must_use]
pub fn bounded_key(key: &[u8]) -> Option<&str> {
    let plain = |byte: &u8| byte.is_ascii_alphanumeric() || matches!(byte, b'_' | b'.' | b'-');
    if key.is_empty() || key.len() > MAX_KEY_ECHO || !key.iter().all(plain) {
        return None;
    }
    core::str::from_utf8(key).ok()
}

/// The path of an unknown or duplicate key under `path`: the key when it may be echoed, else
/// the parent.
fn key_path(path: &str, key: &[u8]) -> String {
    match bounded_key(key) {
        Some(name) => join(path, name),
        None => path.to_string(),
    }
}

fn from_envelope(text: &[u8], path: &str, error: EnvelopeError) -> ArgError {
    match error {
        EnvelopeError::Json => fail(path),
        EnvelopeError::MissingKey(name) => fail(&join(path, name)),
        EnvelopeError::WrongType(name) => fail(&join(path, name)),
        EnvelopeError::DuplicateKey { key } => {
            let name = text.get(key).unwrap_or_default();
            fail(&key_path(path, name))
        }
        EnvelopeError::UnknownKey { key } => {
            let name = text.get(key).unwrap_or_default();
            fail(&key_path(path, name))
        }
    }
}

/// An object whose members are known, at most [`MAX_MEMBERS`], without duplicate or escaped
/// keys.
#[derive(Debug)]
pub struct Obj<'a> {
    text: &'a [u8],
    members: Vec<Member>,
    path: String,
}

impl<'a> Obj<'a> {
    /// Read `text`, which must be exactly one JSON object; `path` names it in errors.
    ///
    /// # Errors
    ///
    /// An [`ArgError`] for a text defect, a duplicate key, more than [`MAX_MEMBERS`] members or a
    /// key with a backslash.
    pub fn parse(text: &'a [u8], path: &str) -> Result<Obj<'a>, ArgError> {
        let members = match members_up_to(text, MAX_MEMBERS) {
            Ok(members) => members,
            Err(error) => return Err(from_envelope(text, path, error)),
        };
        for member in &members {
            let key = text.get(member.key.clone()).unwrap_or_default();
            if key.contains(&b'\\') {
                return Err(fail(path));
            }
        }
        Ok(Obj {
            text,
            members,
            path: path.to_string(),
        })
    }

    /// The number of members.
    #[must_use]
    pub fn len(&self) -> usize {
        self.members.len()
    }

    /// Whether the object has no member.
    #[must_use]
    pub fn is_empty(&self) -> bool {
        self.members.is_empty()
    }

    fn key_of(&self, member: &Member) -> &'a [u8] {
        self.text.get(member.key.clone()).unwrap_or_default()
    }

    fn raw(&self, member: &Member) -> &'a [u8] {
        self.text.get(member.value.clone()).unwrap_or_default()
    }

    /// Every member's key must be one of `allowed`.
    ///
    /// # Errors
    ///
    /// The first unknown key, as an [`ArgError`] (echoed under R14).
    pub fn only(&self, allowed: &[&str]) -> Result<(), ArgError> {
        for member in &self.members {
            let name = self.key_of(member);
            if !allowed.iter().any(|label| label.as_bytes() == name) {
                return Err(fail(&key_path(&self.path, name)));
            }
        }
        Ok(())
    }

    /// The member `name`, present and of type `kind`.
    ///
    /// # Errors
    ///
    /// An [`ArgError`] naming the field when it is missing or of another type.
    pub fn get(&self, name: &str, kind: Kind) -> Result<&Member, ArgError> {
        let found = self
            .members
            .iter()
            .find(|member| self.key_of(member) == name.as_bytes());
        match found {
            Some(member) if member.kind == kind => Ok(member),
            _ => Err(fail(&join(&self.path, name))),
        }
    }

    /// The string member `name`, unescaped, at most `max` decoded bytes.
    ///
    /// # Errors
    ///
    /// An [`ArgError`] when the member is missing, not a string, too long or holds a
    /// surrogate escape that is not a valid pair.
    pub fn string(&self, name: &str, max: usize) -> Result<String, ArgError> {
        let member = self.get(name, Kind::Str)?;
        match unescape(self.raw(member), max) {
            Some(text) => Ok(text),
            None => Err(fail(&join(&self.path, name))),
        }
    }

    /// The member `name` as an exact unsigned integer of at most `max`.
    ///
    /// # Errors
    ///
    /// An [`ArgError`] for a missing or non-numeric member, a sign, a fraction, an exponent,
    /// more than 20 digits or a value above `max`.
    pub fn uint(&self, name: &str, max: u64) -> Result<u64, ArgError> {
        let member = self.get(name, Kind::Number)?;
        match parse_uint(self.raw(member)) {
            Some(value) if value <= max => Ok(value),
            _ => Err(fail(&join(&self.path, name))),
        }
    }

    /// The member `name` as an exact `u16`.
    ///
    /// # Errors
    ///
    /// As [`Obj::uint`], with 65,535 as the bound.
    pub fn uint16(&self, name: &str) -> Result<u16, ArgError> {
        let value = self.uint(name, u64::from(u16::MAX))?;
        Ok(u16::try_from(value).unwrap_or(u16::MAX))
    }

    /// The member `name` as an exact `i64` (`-0` is refused).
    ///
    /// # Errors
    ///
    /// An [`ArgError`] for a missing or non-numeric member or a value that is not an exact
    /// `i64`.
    pub fn int(&self, name: &str) -> Result<i64, ArgError> {
        let member = self.get(name, Kind::Number)?;
        match parse_int(self.raw(member)) {
            Some(value) => Ok(value),
            None => Err(fail(&join(&self.path, name))),
        }
    }

    /// The object member `name`, read with the same rules.
    ///
    /// # Errors
    ///
    /// An [`ArgError`] for a missing or non-object member or any defect of the inner object
    /// (its path is `parent.name`).
    pub fn object(&self, name: &str) -> Result<Obj<'a>, ArgError> {
        let member = self.get(name, Kind::Object)?;
        Obj::parse(self.raw(member), &join(&self.path, name))
    }

    /// The element ranges of the array member `name`, relative to this object's text, at most
    /// [`MAX_ELEMENTS`].
    ///
    /// # Errors
    ///
    /// An [`ArgError`] for a missing or non-array member, an empty element or more than
    /// [`MAX_ELEMENTS`] elements.
    pub fn array(&self, name: &str) -> Result<Vec<Range<usize>>, ArgError> {
        let member = self.get(name, Kind::Array)?;
        match split_array(self.text, member.value.clone()) {
            Some(elements) => Ok(elements),
            None => Err(fail(&join(&self.path, name))),
        }
    }
}

/// An exact unsigned integer: digits only, at most 20, no leading zero, no overflow.
#[must_use]
pub fn parse_uint(raw: &[u8]) -> Option<u64> {
    if raw.is_empty() || raw.len() > MAX_DIGITS {
        return None;
    }
    if raw.len() > 1 && raw.first() == Some(&b'0') {
        return None;
    }
    let mut value: u64 = 0;
    for byte in raw {
        if !byte.is_ascii_digit() {
            return None;
        }
        let digit = u64::from(*byte - b'0');
        value = value.checked_mul(10)?.checked_add(digit)?;
    }
    Some(value)
}

/// An exact signed integer: an optional `-` and then [`parse_uint`] digits; `-0` is refused.
#[must_use]
pub fn parse_int(raw: &[u8]) -> Option<i64> {
    match raw.split_first() {
        Some((&b'-', digits)) => {
            let magnitude = parse_uint(digits)?;
            if magnitude == 0 {
                return None;
            }
            0i64.checked_sub_unsigned(magnitude)
        }
        _ => i64::try_from(parse_uint(raw)?).ok(),
    }
}

fn hex4(chars: &mut Chars<'_>) -> Option<u32> {
    let mut value = 0u32;
    for _ in 0..4 {
        value = value * 16 + chars.next()?.to_digit(16)?;
    }
    Some(value)
}

/// The scalar value of a `\uXXXX` escape whose four digits are `unit`; a high surrogate must be
/// followed by a low one, a low surrogate may not stand alone.
fn scalar(unit: u32, chars: &mut Chars<'_>) -> Option<char> {
    match unit {
        0xD800..=0xDBFF => {
            if chars.next()? != '\\' || chars.next()? != 'u' {
                return None;
            }
            let low = hex4(chars)?;
            if !(0xDC00..=0xDFFF).contains(&low) {
                return None;
            }
            char::from_u32(0x1_0000 + ((unit - 0xD800) << 10) + (low - 0xDC00))
        }
        0xDC00..=0xDFFF => None,
        _ => char::from_u32(unit),
    }
}

/// The character a backslash sequence stands for; `chars` is positioned after the backslash.
fn escape(chars: &mut Chars<'_>) -> Option<char> {
    match chars.next()? {
        '"' => Some('"'),
        '\\' => Some('\\'),
        '/' => Some('/'),
        'b' => Some(char::from(8u8)),
        'f' => Some(char::from(12u8)),
        'n' => Some('\n'),
        'r' => Some('\r'),
        't' => Some('\t'),
        'u' => {
            let unit = hex4(chars)?;
            scalar(unit, chars)
        }
        _ => None,
    }
}

/// The text of a JSON string value `quoted` (quotes included) with its escapes resolved, or
/// `None` for a surrogate escape that is not a valid pair, a malformed escape, invalid UTF-8
/// or a decoded length above `max`.
#[must_use]
pub fn unescape(quoted: &[u8], max: usize) -> Option<String> {
    let end = quoted.len().checked_sub(1)?;
    let inner = quoted.get(1..end)?;
    let text = core::str::from_utf8(inner).ok()?;
    let mut out = String::new();
    let mut chars = text.chars();
    while let Some(c) = chars.next() {
        let decoded = if c == '\\' { escape(&mut chars)? } else { c };
        out.push(decoded);
        if out.len() > max {
            return None;
        }
    }
    Some(out)
}

fn is_space(byte: u8) -> bool {
    matches!(byte, b' ' | b'\t' | b'\n' | b'\r')
}

fn skip_space(text: &[u8], mut at: usize) -> usize {
    while text.get(at).copied().is_some_and(is_space) {
        at += 1;
    }
    at
}

/// The end of the array element that starts at `from`: the index of the `,` or of the closing
/// bracket `end`, and whether it is the closing bracket.
fn element_end(text: &[u8], from: usize, end: usize) -> Option<(usize, bool)> {
    let mut depth = 0usize;
    let mut in_string = false;
    let mut escaped = false;
    let mut at = from;
    while at <= end {
        let byte = *text.get(at)?;
        if in_string {
            if escaped {
                escaped = false;
            } else if byte == b'\\' {
                escaped = true;
            } else if byte == b'"' {
                in_string = false;
            }
        } else {
            match byte {
                b'"' => in_string = true,
                b'[' | b'{' => depth += 1,
                b']' | b'}' => {
                    if depth == 0 {
                        return (at == end).then_some((at, true));
                    }
                    depth -= 1;
                }
                b',' if depth == 0 => return Some((at, false)),
                _ => {}
            }
        }
        at += 1;
    }
    None
}

/// The element ranges of the array that is exactly `span` of `text` (both brackets included).
fn split_array(text: &[u8], span: Range<usize>) -> Option<Vec<Range<usize>>> {
    let end = span.end.checked_sub(1)?;
    if text.get(span.start) != Some(&b'[') || text.get(end) != Some(&b']') {
        return None;
    }
    let mut out = Vec::new();
    let mut at = skip_space(text, span.start + 1);
    if at == end {
        return Some(out);
    }
    loop {
        let (stop, last) = element_end(text, at, end)?;
        let mut finish = stop;
        while finish > at && text.get(finish - 1).copied().is_some_and(is_space) {
            finish -= 1;
        }
        if finish == at || out.len() >= MAX_ELEMENTS {
            return None;
        }
        out.push(at..finish);
        if last {
            return Some(out);
        }
        at = skip_space(text, stop + 1);
    }
}

/// The checked arguments of `hello` (WIRE.md section 4.4).
#[derive(Clone, Debug, Eq, PartialEq)]
pub struct HelloArgs {
    /// The free client label (never an identity, R23).
    pub client: String,
    /// `proto.major` as sent.
    pub major: u16,
    /// `proto.minor` as sent: the client's own highest minor C (R7).
    pub minor: u16,
}

fn is_control(c: char) -> bool {
    u32::from(c) < 0x20 || u32::from(c) == 0x7F
}

/// Read the `hello` arguments `{"client": str, "proto": {"major": u16, "minor": u16}}`: both
/// members required, `client` 0 to 64 decoded bytes without a control character below 0x20 or
/// 0x7F (R17).
///
/// # Errors
///
/// An [`ArgError`] naming the member: `client`, `proto`, `proto.major`, `proto.minor`, or an
/// unknown member under R14.
pub fn hello(args: &[u8]) -> Result<HelloArgs, ArgError> {
    let object = Obj::parse(args, "")?;
    object.only(&["client", "proto"])?;
    let client = object.string("client", MAX_CLIENT_BYTES)?;
    if client.chars().any(is_control) {
        return Err(fail("client"));
    }
    let proto = object.object("proto")?;
    proto.only(&["major", "minor"])?;
    let major = proto.uint16("major")?;
    let minor = proto.uint16("minor")?;
    Ok(HelloArgs {
        client,
        major,
        minor,
    })
}

/// Read the `status` arguments: exactly `{}`.
///
/// # Errors
///
/// An [`ArgError`] for any member.
pub fn status(args: &[u8]) -> Result<(), ArgError> {
    Obj::parse(args, "")?.only(&[])
}

/// The negotiated minor: `min(client minor C, server minor)` (WIRE.md section 1.4, R7).
#[must_use]
pub fn negotiate(client_minor: u16, server_minor: u16) -> u16 {
    client_minor.min(server_minor)
}

#[cfg(test)]
mod tests {
    use super::{
        bounded_key, hello, negotiate, parse_int, parse_uint, split_array, status, unescape,
        ArgError, HelloArgs, Obj, MAX_ELEMENTS, MAX_MEMBERS,
    };

    const PROTO: &str = "{\"major\":1,\"minor\":1}";

    /// `~` stands for a backslash, so no backslash-u sequence appears in this source.
    fn bs(text: &str) -> String {
        text.replace('~', &char::from(0x5c).to_string())
    }

    fn hello_text(client: &str, proto: &str) -> String {
        bs(&format!("{{\"client\":\"{client}\",\"proto\":{proto}}}"))
    }

    fn client_of(raw: &str) -> Result<String, ArgError> {
        let text = hello_text(raw, PROTO);
        hello(text.as_bytes()).map(|args| args.client)
    }

    fn err(key: &str) -> ArgError {
        ArgError {
            key: key.to_string(),
        }
    }

    fn refused(text: &str) -> String {
        let text = bs(text);
        match hello(text.as_bytes()) {
            Ok(args) => panic!("accepted {args:?}"),
            Err(error) => error.key,
        }
    }

    fn minor_of(raw: &str) -> Result<u16, ArgError> {
        let proto = format!("{{\"major\":1,\"minor\":{raw}}}");
        let text = hello_text("a", &proto);
        hello(text.as_bytes()).map(|args| args.minor)
    }

    #[test]
    fn a_valid_hello_is_read() {
        let text = hello_text("probe", PROTO);
        let expect = HelloArgs {
            client: "probe".to_string(),
            major: 1,
            minor: 1,
        };
        assert_eq!(hello(text.as_bytes()), Ok(expect));
        let spaced = " { \"proto\" : { \"minor\" : 0 , \"major\" : 1 } , \"client\" : \"\" } ";
        let spaced = spaced.trim();
        let args = hello(spaced.as_bytes()).expect("spaced");
        assert_eq!((args.client.as_str(), args.major, args.minor), ("", 1, 0));
    }

    #[test]
    fn escaped_spellings_of_values_decode() {
        assert_eq!(client_of("~u0041b"), Ok("Ab".to_string()));
        assert_eq!(client_of("a~/b~\\"), Ok("a/b\\".to_string()));
        let pair = client_of("~uD83D~uDE00").expect("a valid pair decodes");
        assert_eq!(pair, "\u{1F600}");
        assert_eq!(client_of("~u00e9"), Ok("\u{e9}".to_string()));
    }

    #[test]
    fn surrogates_are_refused_never_replaced() {
        assert_eq!(client_of("~uD83D~u0041"), Err(err("client")));
        assert!(client_of("~uDE00").is_err());
        assert!(client_of("~uD83D").is_err());
        assert!(client_of("~uD83Dx").is_err());
        assert!(client_of("~uDE00~uD83D").is_err());
        assert!(client_of("~uD83D~uD83D").is_err());
    }

    #[test]
    fn controls_in_client_are_refused() {
        for raw in ["~u0000", "~u001f", "~u007F", "~n", "~t", "a~u0007"] {
            assert_eq!(refused(&hello_text(raw, PROTO)), "client", "{raw}");
        }
        assert!(client_of("~u0020").is_ok());
    }

    #[test]
    fn the_client_bound_is_on_decoded_bytes() {
        let sixty_four = "a".repeat(64);
        assert!(client_of(&sixty_four).is_ok());
        assert!(client_of(&"a".repeat(65)).is_err());
        let escaped = "~u0061".repeat(64);
        assert_eq!(client_of(&escaped), Ok(sixty_four));
        assert!(client_of(&"~u0061".repeat(65)).is_err());
        assert!(client_of(&"\u{e9}".repeat(32)).is_ok());
        assert!(client_of(&"\u{e9}".repeat(33)).is_err());
    }

    #[test]
    fn member_and_element_caps() {
        let object = |count: usize| {
            let members: Vec<String> = (0..count).map(|i| format!("\"k{i}\":1")).collect();
            format!("{{{}}}", members.join(","))
        };
        let sixteen = object(MAX_MEMBERS);
        assert_eq!(Obj::parse(sixteen.as_bytes(), "").map(|o| o.len()), Ok(16));
        let seventeen = object(MAX_MEMBERS + 1);
        let error = Obj::parse(seventeen.as_bytes(), "").expect_err("17 members");
        assert_eq!(error.key, "k16");
        let array = |count: usize| {
            let items: Vec<String> = (0..count).map(|i| i.to_string()).collect();
            format!("{{\"a\":[{}]}}", items.join(","))
        };
        let full = array(MAX_ELEMENTS);
        let object = Obj::parse(full.as_bytes(), "").expect("object");
        assert_eq!(object.array("a").map(|a| a.len()), Ok(64));
        let over = array(MAX_ELEMENTS + 1);
        let object = Obj::parse(over.as_bytes(), "").expect("object");
        assert_eq!(object.array("a"), Err(err("a")));
    }

    #[test]
    fn arrays_split_into_trimmed_element_ranges() {
        let text = "{\"a\":[ 1 , [2,3] , {\"b\":[4]} , \"x,]\" ],\"e\":[],\"f\":[ ]}";
        let object = Obj::parse(text.as_bytes(), "").expect("object");
        let ranges = object.array("a").expect("array");
        let parts: Vec<&str> = ranges.iter().map(|r| &text[r.clone()]).collect();
        assert_eq!(parts, ["1", "[2,3]", "{\"b\":[4]}", "\"x,]\""]);
        assert_eq!(object.array("e"), Ok(Vec::new()));
        assert_eq!(object.array("f"), Ok(Vec::new()));
        assert!(object.array("missing").is_err());
        assert!(object.get("a", super::Kind::Str).is_err());
        assert!(object.get("a", super::Kind::Array).is_ok());
    }

    #[test]
    fn malformed_arrays_are_refused_by_the_splitter() {
        for text in ["", "[", "[1,]", "[,1]", "[1}", "[}]", "[{]", "{]", "[1]]"] {
            let bytes = text.as_bytes();
            assert_eq!(split_array(bytes, 0..bytes.len()), None, "{text}");
        }
        let first = std::iter::once(1..2).collect::<Vec<_>>();
        assert_eq!(split_array(b"[1]", 0..3), Some(first));
        let quoted = std::iter::once(1..7).collect::<Vec<_>>();
        assert_eq!(split_array(b"[\"a\\\"]\"]", 0..8), Some(quoted));
    }

    #[test]
    fn duplicates_are_per_object_at_every_depth() {
        let top = "{\"client\":\"a\",\"client\":\"b\",\"proto\":{\"major\":1,\"minor\":1}}";
        assert_eq!(refused(top), "client");
        let deep = "{\"client\":\"a\",\"proto\":{\"major\":1,\"major\":2,\"minor\":1}}";
        assert_eq!(refused(deep), "proto.major");
        let same_name = "{\"client\":\"a\",\"proto\":{\"major\":1,\"minor\":1},\"major\":1}";
        assert_eq!(refused(same_name), "major");
    }

    #[test]
    fn a_key_with_a_backslash_is_unknown_even_as_a_duplicate_spelling() {
        let plain = "{\"cl~u0069ent\":\"a\",\"proto\":{\"major\":1,\"minor\":1}}";
        assert_eq!(refused(plain), "");
        let dup = "{\"client\":\"a\",\"cl~u0069ent\":\"b\",\"proto\":{\"major\":1,\"minor\":1}}";
        assert_eq!(refused(dup), "");
        let inner = "{\"client\":\"a\",\"proto\":{\"maj~u006fr\":1,\"minor\":1}}";
        assert_eq!(refused(inner), "proto");
    }

    #[test]
    fn missing_wrong_typed_and_unknown_members_name_their_path() {
        assert_eq!(refused("{\"client\":\"a\"}"), "proto");
        assert_eq!(refused("{\"proto\":{\"major\":1,\"minor\":1}}"), "client");
        assert_eq!(
            refused("{\"client\":\"a\",\"proto\":{\"major\":1}}"),
            "proto.minor"
        );
        assert_eq!(
            refused("{\"client\":\"a\",\"proto\":{\"minor\":1}}"),
            "proto.major"
        );
        for bad in ["true", "null", "5", "[]", "{}"] {
            let text = format!("{{\"client\":{bad},\"proto\":{PROTO}}}");
            assert_eq!(refused(&text), "client", "{bad}");
        }
        assert_eq!(refused("{\"client\":\"a\",\"proto\":[1]}"), "proto");
        assert_eq!(refused("{\"client\":\"a\",\"proto\":\"1\"}"), "proto");
        let nested = "{\"client\":\"a\",\"proto\":{\"major\":true,\"minor\":1}}";
        assert_eq!(refused(nested), "proto.major");
        let nested = "{\"client\":\"a\",\"proto\":{\"major\":1,\"minor\":null}}";
        assert_eq!(refused(nested), "proto.minor");
        let nested = "{\"client\":\"a\",\"proto\":{\"major\":1,\"minor\":\"1\"}}";
        assert_eq!(refused(nested), "proto.minor");
        let extra = "{\"client\":\"a\",\"proto\":{\"major\":1,\"minor\":1,\"x\":1}}";
        assert_eq!(refused(extra), "proto.x");
        assert_eq!(refused("[]"), "");
        assert_eq!(refused(""), "");
        assert_eq!(refused("{"), "");
    }

    #[test]
    fn r23_keys_naming_a_uid_a_pid_or_a_cgroup_are_unknown() {
        for key in ["uid", "pid", "cgroup", "gid", "peer"] {
            let text = format!("{{\"client\":\"a\",\"{key}\":0,\"proto\":{PROTO}}}");
            assert_eq!(refused(&text), key);
        }
    }

    #[test]
    fn r14_echo_is_bounded_to_plain_short_keys() {
        let long = "k".repeat(30_000);
        let text = format!("{{\"client\":\"a\",\"{long}\":1,\"proto\":{PROTO}}}");
        assert_eq!(refused(&text), "");
        let sixty_four = "k".repeat(64);
        let text = format!("{{\"client\":\"a\",\"{sixty_four}\":1,\"proto\":{PROTO}}}");
        assert_eq!(refused(&text), sixty_four);
        let text = format!("{{\"client\":\"a\",\"{sixty_four}k\":1,\"proto\":{PROTO}}}");
        assert_eq!(refused(&text), "");
        let text = format!("{{\"client\":\"a\",\"a b\":1,\"proto\":{PROTO}}}");
        assert_eq!(refused(&text), "");
        let text = format!("{{\"client\":\"a\",\"\":1,\"proto\":{PROTO}}}");
        assert_eq!(refused(&text), "");
        let text = format!("{{\"client\":\"a\",\"\u{e9}\":1,\"proto\":{PROTO}}}");
        assert_eq!(refused(&text), "");
        assert_eq!(bounded_key(b"a.b-c_9"), Some("a.b-c_9"));
        assert_eq!(bounded_key(b""), None);
        assert_eq!(bounded_key(&[0xFF]), None);
    }

    #[test]
    fn r16_integers_are_digits_only_and_in_range() {
        assert_eq!(minor_of("0"), Ok(0));
        assert_eq!(minor_of("65535"), Ok(65535));
        for bad in [
            "65536",
            "-0",
            "-1",
            "1.0",
            "1e2",
            "1E2",
            "0.0",
            "18446744073709551616",
        ] {
            assert_eq!(minor_of(bad), Err(err("proto.minor")), "{bad}");
        }
        assert_eq!(minor_of("\"1\""), Err(err("proto.minor")));
        let leading_zero = hello_text("a", "{\"major\":01,\"minor\":1}");
        assert_eq!(refused(&leading_zero), "");
    }

    #[test]
    fn exact_integer_parsers_cover_the_edges() {
        assert_eq!(parse_uint(b"0"), Some(0));
        assert_eq!(parse_uint(b"18446744073709551615"), Some(u64::MAX));
        assert_eq!(parse_uint(b"18446744073709551616"), None);
        assert_eq!(parse_uint(b"99999999999999999999"), None);
        assert_eq!(parse_uint(b"000000000000000000001"), None);
        assert_eq!(parse_uint(b"007"), None);
        assert_eq!(parse_uint(b""), None);
        assert_eq!(parse_uint(b"1 "), None);
        assert_eq!(parse_uint(b"+1"), None);
        assert_eq!(parse_int(b"9223372036854775807"), Some(i64::MAX));
        assert_eq!(parse_int(b"9223372036854775808"), None);
        assert_eq!(parse_int(b"-9223372036854775808"), Some(i64::MIN));
        assert_eq!(parse_int(b"-9223372036854775809"), None);
        assert_eq!(parse_int(b"-0"), None);
        assert_eq!(parse_int(b"-"), None);
        assert_eq!(parse_int(b"0"), Some(0));
        assert_eq!(parse_int(b"-1"), Some(-1));
        assert_eq!(parse_int(b"1.5"), None);
    }

    #[test]
    fn typed_getters_name_their_field() {
        let text = "{\"a\":-5,\"b\":7,\"c\":\"s\",\"d\":{\"e\":1}}";
        let object = Obj::parse(text.as_bytes(), "p").expect("object");
        assert!(!object.is_empty());
        assert_eq!(object.int("a"), Ok(-5));
        assert_eq!(object.uint("b", 7), Ok(7));
        assert_eq!(object.uint("b", 6), Err(err("p.b")));
        assert_eq!(object.uint("a", 99), Err(err("p.a")));
        assert_eq!(object.int("c"), Err(err("p.c")));
        assert_eq!(object.string("c", 1), Ok("s".to_string()));
        assert_eq!(object.string("c", 0), Err(err("p.c")));
        assert_eq!(object.uint16("b"), Ok(7));
        let inner = object.object("d").expect("inner");
        assert_eq!(inner.uint("e", 1), Ok(1));
        assert_eq!(inner.uint("f", 1), Err(err("p.d.f")));
        assert!(object.object("c").is_err());
        let empty = Obj::parse(b"{}", "").expect("empty");
        assert!(empty.is_empty());
    }

    #[test]
    fn depth_is_bounded_at_eight() {
        let nested = |depth: usize| {
            let open = "[".repeat(depth);
            let close = "]".repeat(depth);
            format!("{{\"a\":{open}1{close}}}")
        };
        assert!(Obj::parse(nested(7).as_bytes(), "").is_ok());
        let deep = nested(8);
        assert_eq!(
            Obj::parse(deep.as_bytes(), "").map(|o| o.len()),
            Err(err(""))
        );
    }

    #[test]
    fn unescape_refuses_what_it_cannot_decode() {
        assert_eq!(unescape(b"\"\"", 0), Some(String::new()));
        assert_eq!(unescape(b"", 9), None);
        assert_eq!(unescape(b"\"", 9), None);
        assert_eq!(unescape(&bs("\"~x\"").into_bytes(), 9), None);
        assert_eq!(unescape(&bs("\"~u12\"").into_bytes(), 9), None);
        assert_eq!(unescape(&bs("\"~u12zz\"").into_bytes(), 9), None);
        assert_eq!(unescape(&[b'"', 0xFF, b'"'], 9), None);
        let every = bs("\"~\"~~~/~b~f~n~r~t\"");
        let expect = bs("\"~/") + "\u{8}\u{c}\n\r\t";
        assert_eq!(unescape(every.as_bytes(), 64), Some(expect));
    }

    #[test]
    fn status_takes_exactly_the_empty_object() {
        assert_eq!(status(b"{}"), Ok(()));
        assert_eq!(status(b"{ }"), Ok(()));
        assert_eq!(status(b"{\"a\":1}"), Err(err("a")));
        assert_eq!(status(b"[]"), Err(err("")));
        assert_eq!(status(b""), Err(err("")));
        let escaped = bs("{\"~u0061\":1}").into_bytes();
        assert_eq!(status(&escaped), Err(err("")));
    }

    #[test]
    fn negotiation_is_the_smaller_minor() {
        assert_eq!(negotiate(1, 5), 1);
        assert_eq!(negotiate(5, 1), 1);
        assert_eq!(negotiate(0, 1), 0);
        assert_eq!(negotiate(7, 7), 7);
    }
}
