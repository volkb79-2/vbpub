//! The byte transport and the clock a [`Client`](crate::Client) runs over (WIRE.md §5, §7.3).
//!
//! Both are injected: [`Client::connect`](crate::Client::connect) supplies a `UnixStream` and
//! the monotonic [`MonotonicClock`], the tests supply scripted fakes. The client reads the clock
//! only through [`Clock::now_ns`] and derives every transport timeout from it, so each deadline
//! of the protocol is a fake-clock test.

use std::io::{self, Read, Write};
use std::net::Shutdown;
use std::os::unix::net::UnixStream;
use std::time::{Duration, Instant};

/// A connected, ordered, reliable byte stream with per-call timeouts.
pub trait Transport {
    /// Read at most `buf.len()` bytes, waiting at most `timeout` (never zero).
    ///
    /// `Ok(0)` is the peer's end of stream; an error of kind `WouldBlock`, `TimedOut` or
    /// `Interrupted` means that no byte arrived in time and the call may be repeated.
    ///
    /// # Errors
    ///
    /// Any I/O error of the underlying stream.
    fn read(&mut self, buf: &mut [u8], timeout: Duration) -> io::Result<usize>;

    /// Write all of `data`, giving up after `timeout` of no progress (never zero).
    ///
    /// # Errors
    ///
    /// Any I/O error of the underlying stream; `WouldBlock` or `TimedOut` when the peer does
    /// not take the bytes in time.
    fn write_all(&mut self, data: &[u8], timeout: Duration) -> io::Result<()>;

    /// Close both directions; a failure to close is of no interest to the caller.
    fn close(&mut self);
}

impl Transport for UnixStream {
    fn read(&mut self, buf: &mut [u8], timeout: Duration) -> io::Result<usize> {
        self.set_read_timeout(Some(timeout))?;
        Read::read(self, buf)
    }

    fn write_all(&mut self, data: &[u8], timeout: Duration) -> io::Result<()> {
        self.set_write_timeout(Some(timeout))?;
        Write::write_all(self, data)
    }

    fn close(&mut self) {
        let _ = self.shutdown(Shutdown::Both);
    }
}

/// A monotonic clock in nanoseconds (WIRE.md §5: every time comes from an injected clock).
pub trait Clock {
    /// Nanoseconds on a monotonic scale; the origin is the clock's own.
    fn now_ns(&self) -> u64;
}

/// The default [`Clock`]: nanoseconds since its own creation, from [`Instant`].
#[derive(Clone, Copy, Debug)]
pub struct MonotonicClock {
    origin: Instant,
}

impl MonotonicClock {
    /// A clock whose zero is now.
    #[must_use]
    pub fn new() -> MonotonicClock {
        MonotonicClock {
            origin: Instant::now(),
        }
    }
}

impl Default for MonotonicClock {
    fn default() -> MonotonicClock {
        MonotonicClock::new()
    }
}

impl Clock for MonotonicClock {
    fn now_ns(&self) -> u64 {
        let nanos = self.origin.elapsed().as_nanos();
        u64::try_from(nanos).unwrap_or(u64::MAX)
    }
}

#[cfg(test)]
mod tests {
    use super::{Clock, MonotonicClock, Transport};
    use std::io::ErrorKind;
    use std::os::unix::net::UnixStream;
    use std::time::Duration;

    const WAIT: Duration = Duration::from_millis(5);

    #[test]
    fn unix_stream_moves_bytes_times_out_and_closes() {
        let (mut near, mut far) = UnixStream::pair().expect("pair");
        Transport::write_all(&mut near, b"hello", WAIT).expect("write");
        let mut buf = [0u8; 16];
        let got = Transport::read(&mut far, &mut buf, WAIT).expect("read");
        assert_eq!(buf.get(..got), Some(b"hello".as_slice()));
        let idle = Transport::read(&mut far, &mut buf, WAIT);
        let kind = idle.expect_err("nothing to read").kind();
        assert!(matches!(kind, ErrorKind::WouldBlock | ErrorKind::TimedOut));
        Transport::close(&mut near);
        assert_eq!(Transport::read(&mut far, &mut buf, WAIT).expect("eof"), 0);
    }

    #[test]
    fn closed_peer_fails_a_write() {
        let (mut near, far) = UnixStream::pair().expect("pair");
        drop(far);
        let big = vec![0u8; 1 << 20];
        let error = Transport::write_all(&mut near, &big, WAIT).expect_err("peer is gone");
        assert!(matches!(
            error.kind(),
            ErrorKind::BrokenPipe | ErrorKind::ConnectionReset
        ));
    }

    #[test]
    fn monotonic_clock_never_goes_back() {
        let clock = MonotonicClock::default();
        let first = clock.now_ns();
        let second = clock.now_ns();
        assert!(second >= first);
    }
}
