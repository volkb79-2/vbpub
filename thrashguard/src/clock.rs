//! The two clocks a guard needs, and why they are distinct.
//!
//! Design §4 (D10): every recorded duration is boot-relative monotonic time —
//! the kernel keeps it running while a cgroup is frozen — and wall time only
//! *stamps* a line so a human can find it. A wall clock steps, so a wall
//! difference is never authoritative.
//!
//! The guard never reads a clock itself: it asks a [`Clock`]. Production uses
//! [`SystemClock`]; every test uses [`ManualClock`] and advances it by hand, so
//! no assertion depends on host speed or load (design §5, D9).
//!
//! `SystemClock` seeds `Instant` from `/proc/uptime`, so its reading is
//! boot-relative and remains comparable across a daemon restart. The wall
//! reading still comes from `SystemTime` and is never authoritative.

use core::fmt;
use std::path::Path;
use std::sync::Mutex;
use std::time::{Duration, Instant, SystemTime, UNIX_EPOCH};

/// The kernel's boot-relative uptime source.
pub const UPTIME_PATH: &str = "/proc/uptime";

/// A clock the guard reads. Monotonic nanoseconds are the only source of a
/// duration; wall nanoseconds exist to stamp a line.
pub trait Clock: fmt::Debug + Send + Sync {
    /// Monotonic nanoseconds, the only basis of an interval.
    fn mono_ns(&self) -> u64;
    /// Wall-clock nanoseconds since the Unix epoch (a stamp, never authoritative).
    fn wall_ns(&self) -> u64;
}

/// The real clocks.
#[derive(Debug)]
pub struct SystemClock {
    boot_ns: u64,
    start: Instant,
}

impl SystemClock {
    /// A clock whose readings are boot-relative, not process-relative.
    pub fn new() -> std::io::Result<Self> {
        Self::from_uptime_path(Path::new(UPTIME_PATH))
    }

    /// Read a boot-relative base from `path`; injectable so tests need no host clock file.
    pub fn from_uptime_path(path: &Path) -> std::io::Result<Self> {
        let text = std::fs::read_to_string(path)?;
        let boot_ns = parse_uptime_ns(&text)?;
        Ok(Self {
            boot_ns,
            start: Instant::now(),
        })
    }
}

fn parse_uptime_ns(text: &str) -> std::io::Result<u64> {
    let value = text.split_whitespace().next().ok_or_else(|| {
        std::io::Error::new(std::io::ErrorKind::InvalidData, "uptime has no first field")
    })?;
    let (seconds, fraction) = value.split_once('.').unwrap_or((value, ""));
    if fraction.len() > 9 || !fraction.bytes().all(|byte| byte.is_ascii_digit()) {
        return Err(std::io::Error::new(
            std::io::ErrorKind::InvalidData,
            "uptime fraction is not a decimal nanosecond value",
        ));
    }
    let seconds = seconds
        .parse::<u64>()
        .map_err(|error| std::io::Error::new(std::io::ErrorKind::InvalidData, error.to_string()))?;
    let mut nanos = if fraction.is_empty() {
        0
    } else {
        fraction.parse::<u64>().map_err(|error| {
            std::io::Error::new(std::io::ErrorKind::InvalidData, error.to_string())
        })?
    };
    for _ in fraction.len()..9 {
        nanos *= 10;
    }
    seconds
        .checked_mul(1_000_000_000)
        .and_then(|whole| whole.checked_add(nanos))
        .ok_or_else(|| std::io::Error::new(std::io::ErrorKind::InvalidData, "uptime overflow"))
}

impl Clock for SystemClock {
    fn mono_ns(&self) -> u64 {
        self.boot_ns
            .saturating_add(self.start.elapsed().as_nanos().min(u128::from(u64::MAX)) as u64)
    }

    fn wall_ns(&self) -> u64 {
        SystemTime::now()
            .duration_since(UNIX_EPOCH)
            .unwrap_or(Duration::ZERO)
            .as_nanos()
            .min(u128::from(u64::MAX)) as u64
    }
}

/// A clock a test advances by hand.
#[derive(Debug)]
pub struct ManualClock {
    state: Mutex<(u64, u64)>,
}

impl ManualClock {
    /// A clock at the given monotonic and wall readings (nanoseconds).
    pub fn at(mono_ns: u64, wall_ns: u64) -> Self {
        Self {
            state: Mutex::new((mono_ns, wall_ns)),
        }
    }

    /// A clock at zero on both readings, plus the given wall stamp.
    pub fn new(wall_ns: u64) -> Self {
        Self::at(0, wall_ns)
    }

    /// Advance both readings by `seconds`.
    pub fn advance_s(&self, seconds: u64) {
        self.advance_ns(seconds.saturating_mul(1_000_000_000));
    }

    /// Advance both readings by `nanos`.
    ///
    /// Both move together on purpose: a test that wants a *wall* jump (an NTP
    /// step with no monotonic progress) sets it explicitly with [`Self::set_wall_ns`].
    pub fn advance_ns(&self, nanos: u64) {
        let mut state = self.state.lock().expect("manual clock mutex");
        state.0 = state.0.saturating_add(nanos);
        state.1 = state.1.saturating_add(nanos);
    }

    /// Move the wall reading alone, which is what an NTP step looks like.
    pub fn set_wall_ns(&self, wall_ns: u64) {
        self.state.lock().expect("manual clock mutex").1 = wall_ns;
    }
}

impl Clock for ManualClock {
    fn mono_ns(&self) -> u64 {
        self.state.lock().expect("manual clock mutex").0
    }

    fn wall_ns(&self) -> u64 {
        self.state.lock().expect("manual clock mutex").1
    }
}

/// Format a monotonic nanosecond reading as whole seconds, for a journal line.
pub fn seconds_of_ns(nanos: u64) -> u64 {
    nanos / 1_000_000_000
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn the_manual_clock_moves_only_when_a_test_says_so() {
        let clock = ManualClock::new(1_700_000_000_000_000_000);
        assert_eq!(clock.mono_ns(), 0);
        clock.advance_s(5);
        assert_eq!(clock.mono_ns(), 5_000_000_000);
        assert_eq!(clock.wall_ns(), 1_700_000_005_000_000_000);
        // A wall step leaves the monotonic reading alone: no duration comes from wall time.
        clock.set_wall_ns(1_600_000_000_000_000_000);
        assert_eq!(clock.mono_ns(), 5_000_000_000);
        assert_eq!(seconds_of_ns(clock.mono_ns()), 5);
    }

    #[test]
    fn the_system_clock_only_moves_forward() {
        let clock = SystemClock::new().expect("kernel uptime");
        let first = clock.mono_ns();
        let second = clock.mono_ns();
        assert!(second >= first);
        assert!(clock.wall_ns() > 1_600_000_000_000_000_000);
    }

    #[test]
    fn system_clock_keeps_the_injected_boot_relative_base() {
        let tree = crate::testutil::TempTree::new("clock-boot-base");
        let uptime = tree.write("proc/uptime", "123456.789 987.654\n");
        let clock = SystemClock::from_uptime_path(&uptime).expect("injected uptime");
        assert!(
            clock.mono_ns() >= 123_456_789_000_000,
            "monotonic time includes the boot-relative base, not just process elapsed time"
        );
    }

    #[test]
    fn uptime_is_parsed_as_boot_relative_nanoseconds() {
        assert_eq!(
            parse_uptime_ns("12345.067 888.00\n").expect("uptime"),
            12_345_067_000_000
        );
        assert_eq!(parse_uptime_ns("7 1\n").expect("uptime"), 7_000_000_000);
        assert!(parse_uptime_ns(" ").is_err());
    }
}
