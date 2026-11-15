//! Bounded little-endian writers for the canonical byte layout.
//!
//! The writer appends into a caller-owned `Vec<u8>`; it allocates nothing of its own and
//! never truncates or reinterprets a caller's buffer. Every encoder in this crate writes
//! through this module, so the *one* place that decides little-endianness and padding is
//! here — which is what makes a byte-exact cross-language fixture possible at all.

/// A little-endian byte sink.
#[derive(Debug, Default)]
pub struct Writer {
    bytes: Vec<u8>,
}

impl Writer {
    /// An empty writer.
    #[must_use]
    pub fn new() -> Writer {
        Writer::default()
    }

    /// A writer over an existing buffer, appending after its current content.
    #[must_use]
    pub fn with_capacity(capacity: usize) -> Writer {
        Writer {
            bytes: Vec::with_capacity(capacity),
        }
    }

    /// The bytes written so far.
    #[must_use]
    pub fn as_bytes(&self) -> &[u8] {
        &self.bytes
    }

    /// The number of bytes written so far.
    #[must_use]
    pub fn len(&self) -> usize {
        self.bytes.len()
    }

    /// True when nothing has been written.
    #[must_use]
    pub fn is_empty(&self) -> bool {
        self.bytes.is_empty()
    }

    /// The finished bytes.
    #[must_use]
    pub fn finish(self) -> Vec<u8> {
        self.bytes
    }

    /// Append one byte.
    pub fn u8(&mut self, value: u8) {
        self.bytes.push(value);
    }

    /// Append a little-endian `u16`.
    pub fn u16(&mut self, value: u16) {
        self.bytes.extend_from_slice(&value.to_le_bytes());
    }

    /// Append a little-endian `u32`.
    pub fn u32(&mut self, value: u32) {
        self.bytes.extend_from_slice(&value.to_le_bytes());
    }

    /// Append a little-endian `u64`.
    pub fn u64(&mut self, value: u64) {
        self.bytes.extend_from_slice(&value.to_le_bytes());
    }

    /// Append a little-endian `i64`.
    pub fn i64(&mut self, value: i64) {
        self.bytes.extend_from_slice(&value.to_le_bytes());
    }

    /// Append raw bytes.
    pub fn bytes(&mut self, bytes: &[u8]) {
        self.bytes.extend_from_slice(bytes);
    }

    /// Append `len` zero bytes.
    pub fn zeros(&mut self, len: usize) {
        self.bytes.resize(self.bytes.len().saturating_add(len), 0);
    }

    /// Pad with zeros to the next multiple of `align` (8).
    pub fn pad_to(&mut self, align: usize) {
        if align == 0 {
            return;
        }
        let rem = self.bytes.len() % align;
        if rem != 0 {
            self.zeros(align - rem);
        }
    }
}

#[cfg(test)]
mod tests {
    use super::Writer;

    #[test]
    fn integers_are_little_endian_and_scalars_append_in_order() {
        let mut writer = Writer::new();
        writer.u8(0xab);
        writer.u16(0x1234);
        writer.u32(0x1234_5678);
        writer.u64(0x0123_4567_89ab_cdef);
        assert_eq!(
            writer.as_bytes(),
            &[
                0xab, 0x34, 0x12, 0x78, 0x56, 0x34, 0x12, 0xef, 0xcd, 0xab, 0x89, 0x67, 0x45, 0x23,
                0x01
            ]
        );
        assert!(writer.len() >= 15);
        assert!(!writer.is_empty());

        let mut writer = Writer::with_capacity(8);
        writer.i64(-2);
        assert_eq!(
            writer.as_bytes(),
            &[0xfe, 0xff, 0xff, 0xff, 0xff, 0xff, 0xff, 0xff]
        );
        assert_eq!(writer.finish().len(), 8);
    }

    #[test]
    fn padding_is_to_the_next_multiple_and_never_shrinks() {
        let mut writer = Writer::new();
        writer.pad_to(8);
        assert_eq!(writer.len(), 0, "already aligned");
        writer.u8(1);
        writer.pad_to(8);
        assert_eq!(writer.len(), 8);
        writer.pad_to(8);
        assert_eq!(writer.len(), 8);
        writer.u8(2);
        writer.pad_to(0);
        assert_eq!(writer.len(), 9, "alignment 0 disables padding");
        let mut writer = Writer::new();
        writer.zeros(3);
        writer.bytes(&[9, 9]);
        assert_eq!(writer.as_bytes(), &[0, 0, 0, 9, 9]);
        assert!(Writer::new().is_empty());
    }
}
