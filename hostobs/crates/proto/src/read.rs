//! A bounded little-endian cursor over untrusted bytes.
//!
//! Every read is fallible and returns [`ProtoError::Truncated`] with the *context* of the
//! structure being read, so a corrupt frame reports which part was short instead of
//! panicking on a slice index. There is no unchecked indexing anywhere in this module:
//! lengths come from the input, so they are hostile until a bound has been checked.

use crate::error::ProtoError;

/// A read position into a byte slice.
#[derive(Clone, Copy, Debug)]
pub struct Cursor<'a> {
    bytes: &'a [u8],
    pos: usize,
    context: &'static str,
}

impl<'a> Cursor<'a> {
    /// A cursor over `bytes`; `context` names the structure for truncation messages.
    #[must_use]
    pub const fn new(bytes: &'a [u8], context: &'static str) -> Cursor<'a> {
        Cursor {
            bytes,
            pos: 0,
            context,
        }
    }

    /// Bytes consumed so far.
    #[must_use]
    pub const fn position(&self) -> usize {
        self.pos
    }

    /// Bytes not consumed yet.
    #[must_use]
    pub const fn remaining(&self) -> usize {
        self.bytes.len().saturating_sub(self.pos)
    }

    /// The bytes not consumed yet.
    #[must_use]
    pub fn rest(&self) -> &'a [u8] {
        self.bytes.get(self.pos..).unwrap_or(&[])
    }

    /// True when everything has been consumed.
    #[must_use]
    pub const fn is_empty(&self) -> bool {
        self.remaining() == 0
    }

    fn take(&mut self, len: usize) -> Result<&'a [u8], ProtoError> {
        let end = self
            .pos
            .checked_add(len)
            .ok_or(ProtoError::LengthOverflow)?;
        let slice = self.bytes.get(self.pos..end).ok_or(ProtoError::Truncated {
            context: self.context,
            need: end,
            have: self.bytes.len(),
        })?;
        self.pos = end;
        Ok(slice)
    }

    /// Consume exactly `len` bytes.
    ///
    /// # Errors
    ///
    /// [`ProtoError::Truncated`] or [`ProtoError::LengthOverflow`].
    pub fn take_bytes(&mut self, len: usize) -> Result<&'a [u8], ProtoError> {
        self.take(len)
    }

    /// Consume one byte.
    ///
    /// # Errors
    ///
    /// [`ProtoError::Truncated`].
    pub fn u8(&mut self) -> Result<u8, ProtoError> {
        let slice = self.take(1)?;
        Ok(slice.first().copied().unwrap_or(0))
    }

    /// Consume a little-endian `u16`.
    ///
    /// # Errors
    ///
    /// [`ProtoError::Truncated`].
    pub fn u16(&mut self) -> Result<u16, ProtoError> {
        let slice = self.take(2)?;
        Ok(u16::from_le_bytes([
            slice.first().copied().unwrap_or(0),
            slice.get(1).copied().unwrap_or(0),
        ]))
    }

    /// Consume a little-endian `u32`.
    ///
    /// # Errors
    ///
    /// [`ProtoError::Truncated`].
    pub fn u32(&mut self) -> Result<u32, ProtoError> {
        let slice = self.take(4)?;
        let mut raw = [0u8; 4];
        raw.copy_from_slice(slice);
        Ok(u32::from_le_bytes(raw))
    }

    /// Consume a little-endian `i64`.
    ///
    /// # Errors
    ///
    /// [`ProtoError::Truncated`].
    pub fn i64(&mut self) -> Result<i64, ProtoError> {
        let slice = self.take(8)?;
        let mut raw = [0u8; 8];
        raw.copy_from_slice(slice);
        Ok(i64::from_le_bytes(raw))
    }

    /// Consume a little-endian `u64`.
    ///
    /// # Errors
    ///
    /// [`ProtoError::Truncated`].
    pub fn u64(&mut self) -> Result<u64, ProtoError> {
        let slice = self.take(8)?;
        let mut raw = [0u8; 8];
        raw.copy_from_slice(slice);
        Ok(u64::from_le_bytes(raw))
    }

    /// Consume padding up to the next multiple of `align`; a non-zero pad byte is corrupt.
    ///
    /// # Errors
    ///
    /// [`ProtoError::LengthOverflow`], [`ProtoError::Truncated`] or
    /// [`ProtoError::NonZeroReserved`].
    pub fn pad_to_end(&mut self, align: usize) -> Result<(), ProtoError> {
        if align == 0 {
            return Err(ProtoError::LengthOverflow);
        }
        let rem = self.pos % align;
        let pad = if rem == 0 { 0 } else { align - rem };
        for _ in 0..pad {
            let byte = self.u8()?;
            if byte != 0 {
                return Err(ProtoError::NonZeroReserved(u16::from(byte)));
            }
        }
        if !self.is_empty() {
            return Err(ProtoError::TrailingBytes {
                extra: self.remaining(),
            });
        }
        Ok(())
    }

    /// Consume `len` zero padding bytes; a non-zero pad byte is a corrupt frame.
    ///
    /// # Errors
    ///
    /// [`ProtoError::Truncated`] or [`ProtoError::NonZeroReserved`].
    pub fn zeros(&mut self, len: usize) -> Result<(), ProtoError> {
        for _ in 0..len {
            let byte = self.u8()?;
            if byte != 0 {
                return Err(ProtoError::NonZeroReserved(u16::from(byte)));
            }
        }
        Ok(())
    }
}

/// `value` rounded up to the next multiple of `align` (8), overflowing as a typed error.
///
/// # Errors
///
/// [`ProtoError::LengthOverflow`].
pub fn align_up(value: usize, align: usize) -> Result<usize, ProtoError> {
    if align == 0 {
        return Err(ProtoError::LengthOverflow);
    }
    let rem = value % align;
    if rem == 0 {
        return Ok(value);
    }
    value
        .checked_add(align - rem)
        .ok_or(ProtoError::LengthOverflow)
}

#[cfg(test)]
mod tests {
    use super::{align_up, Cursor};
    use crate::error::ProtoError;

    const CONTEXT: &str = "test cursor";

    #[test]
    fn every_scalar_reader_consumes_its_bytes_and_refuses_a_short_buffer() {
        let bytes = [
            0x01, 0x02, 0x03, 0x04, 0x05, 0x06, 0x07, 0x08, 0x09, 0x0a, 0x0b, 0x0c, 0x0d, 0x0e,
            0x0f,
        ];
        let mut cursor = Cursor::new(&bytes, CONTEXT);
        cursor.take_bytes(3).expect("three bytes");
        assert_eq!(cursor.position(), 3);
        assert_eq!(cursor.remaining(), 12);
        assert_eq!(cursor.rest().len(), 12);

        let mut cursor = Cursor::new(&[0x7f], CONTEXT);
        assert_eq!(cursor.u8(), Ok(0x7f));
        assert_eq!(
            cursor.u8(),
            Err(ProtoError::Truncated {
                context: CONTEXT,
                need: 2,
                have: 1
            })
        );
        assert_eq!(cursor.position(), 1, "a refused read does not advance");

        let mut cursor = Cursor::new(&[0x34, 0x12], CONTEXT);
        assert_eq!(cursor.u16(), Ok(0x1234));
        let mut cursor = Cursor::new(&[0x78, 0x56, 0x34, 0x12], CONTEXT);
        assert_eq!(cursor.u32(), Ok(0x1234_5678));
        let mut cursor = Cursor::new(&[0xff, 0xff, 0xff, 0xff, 0xff, 0xff, 0xff, 0xff], CONTEXT);
        assert_eq!(cursor.i64(), Ok(-1));
        let mut cursor = Cursor::new(&[1, 0, 0, 0, 0, 0, 0, 0], CONTEXT);
        assert_eq!(cursor.u64(), Ok(1));
        assert!(cursor.is_empty());
        assert_eq!(cursor.rest(), &[] as &[u8]);
    }

    #[test]
    fn truncation_names_the_context_and_the_bounds() {
        let mut cursor = Cursor::new(&[1, 2], CONTEXT);
        assert_eq!(
            cursor.take_bytes(5),
            Err(ProtoError::Truncated {
                context: CONTEXT,
                need: 5,
                have: 2
            })
        );
        let mut cursor = Cursor::new(&[], CONTEXT);
        assert_eq!(
            cursor.u16(),
            Err(ProtoError::Truncated {
                context: CONTEXT,
                need: 2,
                have: 0
            })
        );
        assert_eq!(
            Cursor::new(&[0; 7], CONTEXT).u64(),
            Err(ProtoError::Truncated {
                context: CONTEXT,
                need: 8,
                have: 7
            })
        );
        let mut cursor = Cursor::new(&[0, 0], CONTEXT);
        assert_eq!(
            cursor.take_bytes(usize::MAX),
            Err(ProtoError::Truncated {
                context: CONTEXT,
                need: usize::MAX,
                have: 2
            }),
            "a length larger than the buffer is refused before it is used"
        );
        let mut cursor = Cursor::new(&[0, 0], CONTEXT);
        cursor.u8().expect("one byte");
        assert_eq!(
            cursor.take_bytes(usize::MAX),
            Err(ProtoError::LengthOverflow),
            "a length that overflows the position arithmetic is refused"
        );
    }

    #[test]
    fn zero_padding_is_checked_byte_by_byte() {
        let mut cursor = Cursor::new(&[0, 0, 0, 0], CONTEXT);
        assert_eq!(cursor.zeros(4), Ok(()));
        let mut cursor = Cursor::new(&[0, 7, 0], CONTEXT);
        assert_eq!(cursor.zeros(3), Err(ProtoError::NonZeroReserved(7)));
    }

    #[test]
    fn pad_to_end_requires_a_section_body_to_end_exactly_after_its_padding() {
        // A body of 3 content bytes carries 5 padding bytes and is consumed entirely.
        let mut cursor = Cursor::new(&[1, 2, 3, 0, 0, 0, 0, 0], CONTEXT);
        cursor.take_bytes(3).expect("content");
        assert_eq!(cursor.pad_to_end(8), Ok(()));
        assert!(cursor.is_empty());

        // A body that is already aligned needs no padding.
        let mut cursor = Cursor::new(&[1, 2, 3, 4, 5, 6, 7, 8], CONTEXT);
        cursor.take_bytes(8).expect("content");
        assert_eq!(cursor.pad_to_end(8), Ok(()));

        // A non-zero pad byte is corrupt.
        let mut cursor = Cursor::new(&[1, 2, 3, 0, 0, 9, 0, 0], CONTEXT);
        cursor.take_bytes(3).expect("content");
        assert_eq!(cursor.pad_to_end(8), Err(ProtoError::NonZeroReserved(9)));

        // Bytes left over after zero padding are a corrupt body, never ignored.
        let mut bytes = vec![1u8, 2, 3, 0, 0, 0, 0, 0];
        bytes.extend_from_slice(&[9; 5]);
        let mut cursor = Cursor::new(&bytes, CONTEXT);
        cursor.take_bytes(3).expect("content");
        assert_eq!(
            cursor.pad_to_end(8),
            Err(ProtoError::TrailingBytes { extra: 5 })
        );

        // A body that is already aligned but carries bytes past its content has those
        // bytes checked too: alignment is not an exemption from the end-of-body rule.
        let bytes = [1u8; 16];
        let mut cursor = Cursor::new(&bytes, CONTEXT);
        cursor.take_bytes(8).expect("content");
        assert_eq!(
            cursor.pad_to_end(8),
            Err(ProtoError::TrailingBytes { extra: 8 }),
            "no padding, still trailing"
        );

        // An alignment of zero is refused rather than divided by.
        let mut cursor = Cursor::new(&[0, 0, 0, 0], CONTEXT);
        assert_eq!(cursor.pad_to_end(0), Err(ProtoError::LengthOverflow));
    }

    #[test]
    fn alignment_rounds_up_and_refuses_overflow() {
        assert_eq!(align_up(0, 8), Ok(0));
        assert_eq!(align_up(1, 8), Ok(8));
        assert_eq!(align_up(8, 8), Ok(8));
        assert_eq!(align_up(9, 8), Ok(16));
        assert_eq!(align_up(usize::MAX, 8), Err(ProtoError::LengthOverflow));
        assert_eq!(align_up(4, 0), Err(ProtoError::LengthOverflow));
    }
}
