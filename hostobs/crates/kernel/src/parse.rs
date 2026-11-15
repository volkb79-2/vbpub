//! Bounded parsing of kernel text files: line, field and number helpers.
//!
//! Kernel pseudo-files are line- and space-oriented text with a handful of shapes: one
//! number per line, `key value`, `device key=value...`, `some avg10=… total=…`. This
//! module is the one place those shapes are split, and every split is bounded so a hostile
//! or corrupt file cannot allocate without limit.
//!
//! # Contracts
//!
//! - **Every parse failure is typed** ([`ParseError`]) and maps to a quality code; nothing
//!   returns a zero on failure, and nothing unwraps.
//! - **A negative number is refused where an unsigned one is required, not wrapped.**
//!   `-1` in a `u64` field is [`ParseError::Negative`], never `u64::MAX` (the sampler's
//!   `parse_u64` reached the same answer by rejecting the sign; a `wrapping_neg` here would
//!   be an invented reading).
//! - **A line count is bounded** ([`lines`] with a `max_lines` argument). A file longer
//!   than the bound is [`ParseError::TooManyLines`] with the bound, not a truncated read:
//!   a reader that silently kept the first N lines of a corrupt file would report a
//!   plausible subset as if it were the whole fact.
//! - **Non-UTF-8 text is not an error for a number parse.** A `comm` is read as bytes;
//!   only the numeric helpers insist on ASCII digits.

use core::fmt;

/// Why a piece of kernel text could not be parsed.
#[derive(Clone, Copy, Debug, Eq, PartialEq)]
pub enum ParseError {
    /// The text is empty where a value was required.
    Empty,
    /// A required character is not an ASCII digit (or is outside the accepted set).
    NotANumber {
        /// The offending byte (as read from the file).
        byte: u8,
        /// The zero-based byte offset inside the field.
        offset: usize,
    },
    /// A number field carries a leading sign where an unsigned value is required.
    Negative,
    /// A decimal number does not fit its type.
    Overflow,
    /// The file carries more lines than the bound allows.
    TooManyLines {
        /// The bound that was refused.
        max_lines: usize,
    },
    /// A line that a reader requires (`key value`) does not have two fields.
    MissingField {
        /// The zero-based line number (blank lines counted).
        line: usize,
    },
    /// A `key=value` pair has no `=` or an empty key.
    BadPair {
        /// The zero-based offset inside the line.
        offset: usize,
    },
    /// A leading/trailing separator was found where a value was required.
    ValueMissing,
}

impl fmt::Display for ParseError {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        match self {
            ParseError::Empty => f.write_str("the text is empty"),
            ParseError::NotANumber { byte, offset } => {
                write!(
                    f,
                    "byte {byte:#04x} at offset {offset} is not a number character"
                )
            }
            ParseError::Negative => {
                f.write_str("a negative number where an unsigned one is required")
            }
            ParseError::Overflow => f.write_str("a number does not fit"),
            ParseError::TooManyLines { max_lines } => {
                write!(f, "the file carries more than {max_lines} lines")
            }
            ParseError::MissingField { line } => {
                write!(
                    f,
                    "line {line} does not have the two fields its shape requires"
                )
            }
            ParseError::BadPair { offset } => {
                write!(f, "the key=value pair at offset {offset} is malformed")
            }
            ParseError::ValueMissing => f.write_str("the value field is empty"),
        }
    }
}

impl core::error::Error for ParseError {}

/// The quality code a parse failure maps to: the fact exists but could not be read.
///
/// It is always [`QualityCode::UNREADABLE`](crate::quality::QualityCode::UNREADABLE): the kernel offered a file and this crate
/// could not turn it into a value, which is different from "the controller is absent"
/// (the caller's `absent` code) and different from "the kernel lacks the feature"
/// ([`QualityCode::UNSUPPORTED`](crate::quality::QualityCode::UNSUPPORTED)).
#[must_use]
pub const fn parse_failure_quality() -> crate::quality::QualityCode {
    crate::quality::QualityCode::UNREADABLE
}

/// Split a byte buffer into non-empty lines, refusing more than `max_lines` of them.
///
/// Trailing `\r` is stripped so a CRLF fixture reads like the kernel's `\n` files.
///
/// # Errors
///
/// [`ParseError::TooManyLines`] when the file carries more lines than the bound.
pub fn lines(bytes: &[u8], max_lines: usize) -> Result<Vec<&[u8]>, ParseError> {
    let mut out = Vec::new();
    for line in bytes.split(|byte| *byte == b'\n') {
        if line.is_empty() {
            continue;
        }
        if out.len() == max_lines {
            return Err(ParseError::TooManyLines { max_lines });
        }
        out.push(line.strip_suffix(b"\r").unwrap_or(line));
    }
    Ok(out)
}

/// Split one line on spaces and tabs, dropping empty fields.
#[must_use]
pub fn fields(line: &[u8]) -> Vec<&[u8]> {
    line.split(|byte| *byte == b' ' || *byte == b'\t')
        .filter(|field| !field.is_empty())
        .collect()
}

/// Trim ASCII whitespace (including the trailing `\n` a pseudo-file writes).
#[must_use]
pub fn trim(bytes: &[u8]) -> &[u8] {
    let start = bytes
        .iter()
        .position(|byte| !byte.is_ascii_whitespace())
        .unwrap_or(bytes.len());
    let end = bytes
        .iter()
        .rposition(|byte| !byte.is_ascii_whitespace())
        .map_or(start, |index| index + 1);
    bytes.get(start..end).unwrap_or(&[])
}

/// Parse an unsigned decimal number; a leading sign, an inner space or a non-digit is
/// refused.
///
/// # Errors
///
/// [`ParseError::Empty`], [`ParseError::Negative`], [`ParseError::NotANumber`] and
/// [`ParseError::Overflow`].
pub fn parse_u64(bytes: &[u8]) -> Result<u64, ParseError> {
    let text = trim(bytes);
    if text.is_empty() {
        return Err(ParseError::Empty);
    }
    // A sign on an unsigned field is a corrupt or misunderstood file, never a value to
    // reinterpret: `-1` as `u64::MAX` would be an invented reading, and the kernel never
    // writes a leading `+` at all.
    if text.starts_with(b"-") {
        return Err(ParseError::Negative);
    }
    if text.starts_with(b"+") {
        return Err(ParseError::NotANumber {
            byte: b'+',
            offset: 0,
        });
    }
    let digits = text;
    let mut value: u64 = 0;
    for (offset, byte) in digits.iter().enumerate() {
        if !byte.is_ascii_digit() {
            return Err(ParseError::NotANumber {
                byte: *byte,
                offset,
            });
        }
        let digit = u64::from(*byte - b'0');
        value = value
            .checked_mul(10)
            .and_then(|value| value.checked_add(digit))
            .ok_or(ParseError::Overflow)?;
    }
    Ok(value)
}

/// Parse a signed decimal number.
///
/// # Errors
///
/// [`ParseError::Empty`], [`ParseError::NotANumber`] and [`ParseError::Overflow`].
pub fn parse_i64(bytes: &[u8]) -> Result<i64, ParseError> {
    let text = trim(bytes);
    if text.is_empty() {
        return Err(ParseError::Empty);
    }
    let (negative, digits) = match text.split_first() {
        Some((b'-', rest)) => (true, rest),
        Some((b'+', rest)) => (false, rest),
        Some(_) => (false, text),
        None => return Err(ParseError::Empty),
    };
    let magnitude = parse_u64(digits)?;
    if negative {
        // `-9223372036854775808` is `i64::MIN`, whose magnitude (2^63) does not fit `i64`
        // but does fit `u64`; it is the one negative value that must not be refused.
        if magnitude == 9_223_372_036_854_775_808 {
            return Ok(i64::MIN);
        }
        let magnitude = i64::try_from(magnitude).map_err(|_| ParseError::Overflow)?;
        return magnitude.checked_neg().ok_or(ParseError::Overflow);
    }
    i64::try_from(magnitude).map_err(|_| ParseError::Overflow)
}

/// Parse a decimal number as `f64` (a PSI `avg10`, a load average, an `io.stat` `vrate`).
///
/// `max` and other sentinels are *not* accepted here: a caller that expects one of them
/// (`memory.max`) checks for it explicitly, so a text this function accepts is a number.
///
/// # Errors
///
/// [`ParseError::Empty`], [`ParseError::NotANumber`] and [`ParseError::Overflow`].
pub fn parse_f64(bytes: &[u8]) -> Result<f64, ParseError> {
    let text = trim(bytes);
    if text.is_empty() {
        return Err(ParseError::Empty);
    }
    let mut seen_digit = false;
    let mut seen_dot = false;
    let mut seen_exponent = false;
    for (offset, byte) in text.iter().enumerate() {
        match *byte {
            b'0'..=b'9' => seen_digit = true,
            b'.' if !seen_dot && !seen_exponent => seen_dot = true,
            b'e' | b'E' if !seen_exponent && seen_digit => seen_exponent = true,
            b'+' | b'-' if offset == 0 => {}
            b'+' | b'-' if seen_exponent && matches!(text.get(offset - 1), Some(b'e' | b'E')) => {}
            _ => {
                return Err(ParseError::NotANumber {
                    byte: *byte,
                    offset,
                })
            }
        }
    }
    if !seen_digit {
        return Err(ParseError::NotANumber {
            byte: text[0],
            offset: 0,
        });
    }
    // Re-parse through `str::parse` for correct rounding; the character loop above exists
    // so a hostile byte sequence is refused before the float parser sees it.
    let parsed: f64 = core::str::from_utf8(text)
        .map_err(|_| ParseError::NotANumber {
            byte: text[0],
            offset: 0,
        })?
        .parse()
        .map_err(|_| ParseError::Overflow)?;
    if parsed.is_infinite() && !text.contains(&b'i') {
        return Err(ParseError::Overflow);
    }
    Ok(parsed)
}

/// Split one `key=value` field at its first `=`.
///
/// # Errors
///
/// [`ParseError::BadPair`] for a missing `=` or an empty key.
pub fn split_pair(field: &[u8], offset: usize) -> Result<(&[u8], &[u8]), ParseError> {
    match field.iter().position(|byte| *byte == b'=') {
        Some(at) if at > 0 => Ok((&field[..at], &field[at + 1..])),
        _ => Err(ParseError::BadPair { offset }),
    }
}

/// Split one `key value` line at its first space or tab.
///
/// # Errors
///
/// [`ParseError::MissingField`] for a line with one field.
pub fn split_key_value(line: &[u8], number: usize) -> Result<(&[u8], &[u8]), ParseError> {
    match line.iter().position(|byte| *byte == b' ' || *byte == b'\t') {
        Some(at) => Ok((&line[..at], trim(&line[at + 1..]))),
        None => Err(ParseError::MissingField { line: number }),
    }
}

#[cfg(test)]
mod tests {
    use super::{
        fields, lines, parse_f64, parse_i64, parse_u64, split_key_value, split_pair, trim,
        ParseError,
    };
    use crate::quality::QualityCode;

    #[test]
    fn an_unsigned_number_accepts_digits_and_refuses_a_sign_a_space_and_an_overflow() {
        assert_eq!(parse_u64(b"12\n"), Ok(12));
        assert_eq!(parse_u64(b" 4294967296 "), Ok(4_294_967_296));
        assert_eq!(parse_u64(b"18446744073709551615"), Ok(u64::MAX));
        assert_eq!(parse_u64(b"-1"), Err(ParseError::Negative));
        assert_eq!(parse_u64(b"-0"), Err(ParseError::Negative));
        assert_eq!(
            parse_u64(b"+7"),
            Err(ParseError::NotANumber {
                byte: b'+',
                offset: 0
            })
        );
        assert_eq!(parse_u64(b""), Err(ParseError::Empty));
        assert_eq!(parse_u64(b"  \n"), Err(ParseError::Empty));
        assert_eq!(
            parse_u64(b"12a"),
            Err(ParseError::NotANumber {
                byte: b'a',
                offset: 2
            })
        );
        assert_eq!(
            parse_u64(b"18446744073709551616"),
            Err(ParseError::Overflow),
            "one past u64::MAX overflows instead of wrapping"
        );
        assert_eq!(
            parse_u64(b"99999999999999999999999"),
            Err(ParseError::Overflow)
        );
    }

    #[test]
    fn a_signed_number_keeps_its_sign_and_takes_the_extremes() {
        assert_eq!(parse_i64(b"-1"), Ok(-1));
        assert_eq!(parse_i64(b"+3"), Ok(3));
        assert_eq!(parse_i64(b"0"), Ok(0));
        assert_eq!(
            parse_i64(b"-9223372036854775808"),
            Ok(i64::MIN),
            "i64::MIN is the magnitude 2^63 negated, not an overflow"
        );
        assert_eq!(parse_i64(b"9223372036854775807"), Ok(i64::MAX));
        assert_eq!(parse_i64(b"9223372036854775808"), Err(ParseError::Overflow));
        assert_eq!(parse_i64(b"-"), Err(ParseError::Empty));
        assert_eq!(
            parse_i64(b"--1"),
            Err(ParseError::Negative),
            "the magnitude parser refuses a second sign"
        );
    }

    #[test]
    fn a_float_refuses_markup_and_keeps_exponents() {
        assert_eq!(parse_f64(b"1.00"), Ok(1.0));
        assert_eq!(parse_f64(b"0.25\n"), Ok(0.25));
        assert_eq!(parse_f64(b"-0.5"), Ok(-0.5));
        assert_eq!(parse_f64(b"1e3"), Ok(1000.0));
        assert_eq!(parse_f64(b"1.5E+2"), Ok(150.0));
        assert_eq!(
            parse_f64(b"max"),
            Err(ParseError::NotANumber {
                byte: b'm',
                offset: 0
            }),
            "`max` is a word the caller checks for, never a number here"
        );
        assert_eq!(
            parse_f64(b"default"),
            Err(ParseError::NotANumber {
                byte: b'd',
                offset: 0
            })
        );
        assert_eq!(parse_f64(b""), Err(ParseError::Empty));
        assert_eq!(
            parse_f64(b"."),
            Err(ParseError::NotANumber {
                byte: b'.',
                offset: 0
            })
        );
        assert_eq!(
            parse_f64(b"1.2.3"),
            Err(ParseError::NotANumber {
                byte: b'.',
                offset: 3
            })
        );
        assert_eq!(parse_f64(b"1e"), Err(ParseError::Overflow));
    }

    #[test]
    fn lines_are_bounded_trimmed_and_counted() {
        let text = b"a 1\nb 2\r\n\nc 3\n";
        let parsed = lines(text, 8).expect("three lines");
        assert_eq!(parsed.len(), 3);
        assert_eq!(parsed[1], b"b 2", "a trailing CR is stripped");
        assert_eq!(
            lines(b"1\n2\n3\n", 2),
            Err(ParseError::TooManyLines { max_lines: 2 }),
            "a file past the line bound is refused, not truncated"
        );
        assert!(lines(b"\n\n", 0)
            .expect("blank lines are not lines")
            .is_empty());
        assert_eq!(trim(b"  x y \n"), b"x y");
        assert_eq!(trim(b"   "), b"");
        assert_eq!(trim(b"\n"), b"");
        assert_eq!(
            fields(b"8:0  rbytes=1\twbytes=2"),
            vec![&b"8:0"[..], &b"rbytes=1"[..], &b"wbytes=2"[..]]
        );
        assert!(fields(b"   ").is_empty());
    }

    #[test]
    fn pair_and_field_splitting_refuse_their_malformed_shapes() {
        assert_eq!(split_pair(b"rbytes=1", 0), Ok((&b"rbytes"[..], &b"1"[..])));
        assert_eq!(split_pair(b"=1", 0), Err(ParseError::BadPair { offset: 0 }));
        assert_eq!(
            split_pair(b"rbytes", 2),
            Err(ParseError::BadPair { offset: 2 })
        );
        assert_eq!(
            split_key_value(b"usage_usec 5", 0),
            Ok((&b"usage_usec"[..], &b"5"[..]))
        );
        assert_eq!(
            split_key_value(b"usage_usec\t7\n", 0),
            Ok((&b"usage_usec"[..], &b"7"[..]))
        );
        assert_eq!(
            split_key_value(b"usage_usec", 4),
            Err(ParseError::MissingField { line: 4 })
        );
    }

    #[test]
    fn a_parse_failure_names_its_fact_and_maps_to_unreadable() {
        for error in [
            ParseError::Empty,
            ParseError::NotANumber {
                byte: b'x',
                offset: 0,
            },
            ParseError::Negative,
            ParseError::Overflow,
            ParseError::TooManyLines { max_lines: 1 },
            ParseError::MissingField { line: 2 },
            ParseError::BadPair { offset: 3 },
            ParseError::ValueMissing,
        ] {
            assert!(!error.to_string().is_empty(), "{error:?}");
        }
        assert_eq!(super::parse_failure_quality(), QualityCode::UNREADABLE);
    }
}
