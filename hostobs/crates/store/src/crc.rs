//! CRC-32 (the reflected IEEE polynomial, `0xEDB8_8320`), computed without a dependency.
//!
//! Plan v4 §C.5 asks for a checksum **per block** on disk. The wire codec (`proto`) has
//! none: a wire frame is validated by its own lengths, types and directory, and a stream
//! is not a file that a crash can truncate. A segment file *is* such a file, so `store`
//! wraps every record in its own envelope carrying this checksum, and a torn tail is
//! therefore a *detected* fact rather than a lucky parse.
//!
//! # Why a bitwise loop instead of a table
//!
//! A 256-entry table costs a kilobyte of code for a factor of about eight on a path that
//! runs once per record write and once per record read, over records of a few kilobytes.
//! Speed is not the criterion here (the estate's rule is that no test result may depend on
//! host speed); exactness is, and a bitwise definition is the one that is easy to re-derive
//! from the polynomial. A test pins the standard check value, so a wrong implementation is
//! red rather than "a different checksum that also agrees with itself".
//!
//! The same definition is used by `python/reference/hostobs_segment_ref.py` (written from
//! the format document, not from this code) and by this crate's fixtures, so a change to
//! the polynomial turns both languages red.

use crate::error::{length_overflow, RegionOutOfBounds, StoreError};

/// The reflected polynomial of CRC-32/ISO-HDLC (the zlib/PNG one).
const POLYNOMIAL: u32 = 0xEDB8_8320;

/// A CRC-32 accumulator.
#[derive(Clone, Copy, Debug, Eq, PartialEq)]
pub struct Crc32 {
    state: u32,
}

impl Crc32 {
    /// An empty accumulator.
    #[must_use]
    pub const fn new() -> Crc32 {
        Crc32 { state: 0xFFFF_FFFF }
    }

    /// Absorb one byte.
    pub fn update_byte(&mut self, byte: u8) {
        let mut value = self.state ^ u32::from(byte);
        for _ in 0..8 {
            value = if value & 1 == 1 {
                (value >> 1) ^ POLYNOMIAL
            } else {
                value >> 1
            };
        }
        self.state = value;
    }

    /// Absorb a slice.
    pub fn update(&mut self, bytes: &[u8]) {
        for byte in bytes {
            self.update_byte(*byte);
        }
    }

    /// The final checksum.
    #[must_use]
    pub const fn finish(self) -> u32 {
        self.state ^ 0xFFFF_FFFF
    }
}

impl Default for Crc32 {
    fn default() -> Self {
        Crc32::new()
    }
}

/// The CRC-32 of one slice.
#[must_use]
pub fn crc32(bytes: &[u8]) -> u32 {
    let mut crc = Crc32::new();
    crc.update(bytes);
    crc.finish()
}

/// The CRC-32 of the concatenation of two slices, without copying either of them.
///
/// A footer covers "everything written before it" and a header covers its own 60 bytes;
/// both callers have the prefix in a buffer already, so this is the two-argument form the
/// readers use for a range check rather than an allocation.
#[must_use]
pub fn crc32_pair(first: &[u8], second: &[u8]) -> u32 {
    let mut crc = Crc32::new();
    crc.update(first);
    crc.update(second);
    crc.finish()
}

/// The CRC-32 of a bounded region of one buffer, reported as a typed refusal when the
/// region does not exist.
///
/// # Errors
///
/// [`StoreError::RegionOutOfBounds`] for a range past the buffer.
pub fn crc32_region(bytes: &[u8], start: usize, end: usize) -> Result<u32, StoreError> {
    let region = bytes
        .get(start..end)
        .ok_or(StoreError::RegionOutOfBounds(RegionOutOfBounds {
            need: end,
            have: bytes.len(),
        }))?;
    Ok(crc32(region))
}

/// A region length, exported so a caller can reuse the store's one arithmetic rule.
///
/// A `start > end` region is **zero bytes**, not an error: this is the "nothing to checksum"
/// case. A caller that must refuse it uses [`checked_region_len`], and nothing in this crate
/// calls this one.
#[must_use]
pub fn region_len(start: usize, end: usize) -> usize {
    end.saturating_sub(start)
}

/// A checked length, reported as the store's own overflow refusal.
///
/// # Errors
///
/// [`StoreError::LengthOverflow`] when `start > end`.
pub fn checked_region_len(start: usize, end: usize) -> Result<usize, StoreError> {
    length_overflow(start, end)
}

#[cfg(test)]
mod tests {
    use super::{checked_region_len, crc32, crc32_pair, crc32_region, region_len, Crc32};
    use crate::error::StoreError;

    #[test]
    fn the_standard_check_value_is_reproduced() {
        // CRC-32/ISO-HDLC's check value: the CRC of "123456789" is 0xCBF4_3926.
        assert_eq!(crc32(b"123456789"), 0xCBF4_3926);
        assert_eq!(crc32(b""), 0);
        assert_eq!(crc32(b"a"), 0xE8B7_BE43);
    }

    #[test]
    fn an_accumulator_is_the_same_as_one_slice() {
        let bytes = b"hostobs segment record";
        let mut accumulator = Crc32::new();
        for byte in bytes {
            accumulator.update_byte(*byte);
        }
        assert_eq!(accumulator.finish(), crc32(bytes));
        let mut twice = Crc32::new();
        twice.update(&bytes[..7]);
        twice.update(&bytes[7..]);
        assert_eq!(twice.finish(), crc32(bytes));
        assert_eq!(Crc32::default(), Crc32::new());
    }

    #[test]
    fn a_pair_is_the_crc_of_the_concatenation() {
        let first = b"HOBSEG\x01";
        let second = b"header bytes";
        let mut joined = Vec::new();
        joined.extend_from_slice(first);
        joined.extend_from_slice(second);
        assert_eq!(crc32_pair(first, second), crc32(&joined));
    }

    #[test]
    fn one_bit_changes_the_checksum() {
        let mut bytes = vec![0u8; 64];
        let base = crc32(&bytes);
        for index in 0..bytes.len() {
            for bit in 0..8 {
                bytes[index] = 1 << bit;
                let changed = crc32(&bytes);
                assert_ne!(
                    changed, base,
                    "byte {index} bit {bit} left the CRC unchanged"
                );
                bytes[index] = 0;
            }
        }
    }

    #[test]
    fn a_region_outside_the_buffer_is_a_typed_refusal() {
        let bytes = [1u8, 2, 3, 4];
        assert_eq!(
            crc32_region(&bytes, 1, 3).map(|crc| crc == crc32(&[2, 3])),
            Ok(true)
        );
        assert_eq!(
            crc32_region(&bytes, 2, 5),
            Err(StoreError::RegionOutOfBounds(
                crate::error::RegionOutOfBounds { need: 5, have: 4 }
            ))
        );
        assert_eq!(
            crc32_region(&bytes, 3, 1),
            Err(StoreError::RegionOutOfBounds(
                crate::error::RegionOutOfBounds { need: 1, have: 4 }
            ))
        );
        assert_eq!(region_len(1, 3), 2);
        assert_eq!(region_len(3, 1), 0);
        assert_eq!(checked_region_len(1, 3), Ok(2));
        assert_eq!(checked_region_len(3, 1), Err(StoreError::LengthOverflow));
    }
}
