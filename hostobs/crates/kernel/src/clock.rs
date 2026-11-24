//! The clocks a sampler needs, and the reason they are distinct.
//!
//! Plan v4 §C.3 and §B.2: monotonic time drives every interval (a rate, a window, a
//! duration), and wall time only *stamps* a record so a human can find it. A wall clock
//! steps: NTP can move it backwards, and then a wall-clock difference between two samples
//! is negative, huge, or zero for a real gap. A monotonic clock cannot go backwards, but it
//! does not survive a reboot, so a pair of readings is only comparable inside `(boot,
//! epoch)`.
//!
//! [`Clock::mono_ns`] is absolute `CLOCK_MONOTONIC` (it stops while the system is
//! suspended), [`Clock::boot_ns`] is absolute `CLOCK_BOOTTIME` (it continues through
//! suspend), and [`Clock::wall_ns`] is `CLOCK_REALTIME`. The boot clock is the source of a
//! process epoch: it advances through every earlier restart of the same boot.
//!
//! # Contracts
//!
//! - **An interval comes from the monotonic clock only.** [`Clock::mono_ns`] is the only
//!   source of a duration; a caller that passed a wall reading here would have to convert
//!   it, which no type in this crate allows.
//! - **A wall jump is visible, not hidden.** [`Clock::wall_ns`] may go backwards between
//!   two calls, and [`Interval`] keeps that fact instead of clamping it to zero: a
//!   negative wall delta is reported as a jump so a consumer can widen its own interval
//!   rather than record a zero.
//! - **A missing interval is stated.** [`ClockError::NotComparable`] is what a
//!   cross-generation pair (a different boot or epoch) produces: SP-2 requires that no
//!   rate and no lifetime joins across such a boundary, and the check is here rather than
//!   in prose.
//! - **An injected clock makes a test deterministic.** [`ManualClock`] is a [`Clock`] a
//!   test advances by hand, so no assertion depends on host speed or load.

use core::fmt;

/// How deeply a monotonic and a wall reading belong to one another.
///
/// The pair is identified by the boot it was taken in and by the daemon epoch; both are
/// facts of the boot/process, not of the clock, so the caller supplies them and the
/// comparison cannot invent one.
#[derive(Clone, Copy, Debug, Eq, PartialEq, Ord, PartialOrd)]
pub struct Timeline {
    /// The boot the readings belong to (16 bytes, `/proc/sys/kernel/random/boot_id`).
    pub boot: [u8; 16],
    /// The daemon epoch the readings belong to (a probe restart starts a new epoch).
    pub epoch: u64,
}

impl Timeline {
    /// A timeline for a boot and an epoch.
    #[must_use]
    pub const fn new(boot: [u8; 16], epoch: u64) -> Timeline {
        Timeline { boot, epoch }
    }
}

/// One pair of readings taken as close together as the caller managed.
#[derive(Clone, Copy, Debug, Eq, PartialEq)]
pub struct ClockReading {
    /// Monotonic nanoseconds (`CLOCK_MONOTONIC`).
    pub mono_ns: u64,
    /// Wall nanoseconds since the Unix epoch (`CLOCK_REALTIME`).
    pub wall_ns: i64,
    /// Which boot/epoch this pair belongs to.
    pub timeline: Timeline,
}

/// Why two clock readings cannot be compared.
#[derive(Clone, Copy, Debug, Eq, PartialEq)]
pub enum ClockError {
    /// The later reading is earlier on the monotonic clock. This is a caller defect
    /// (the readings were swapped) and never a real interval.
    MonoWentBackwards,
    /// The two readings belong to different boots or epochs, so their monotonic clocks
    /// have different origins and a difference is meaningless.
    NotComparable {
        /// The earlier reading's timeline.
        previous: Timeline,
        /// The later reading's timeline.
        current: Timeline,
    },
}

impl fmt::Display for ClockError {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        match self {
            ClockError::MonoWentBackwards => {
                f.write_str("the later reading is earlier on the monotonic clock")
            }
            ClockError::NotComparable { previous, current } => write!(
                f,
                "readings from boot {:?} epoch {} and boot {:?} epoch {} cannot be compared",
                previous.boot, previous.epoch, current.boot, current.epoch
            ),
        }
    }
}

impl core::error::Error for ClockError {}

/// A usable interval between two readings of one timeline.
#[derive(Clone, Copy, Debug, Eq, PartialEq)]
pub struct Interval {
    /// The monotonic length of the interval, nanoseconds (never negative).
    pub mono_ns: u64,
    /// The wall-clock change over the same pair: negative when the wall clock jumped
    /// backwards.
    pub wall_ns: i128,
}

impl Interval {
    /// True when the wall clock moved backwards over this interval.
    ///
    /// A sampler must not treat such an interval as "no elapsed time": the readings are a
    /// real elapsed monotonic interval apart and only the *label* is wrong.
    #[must_use]
    pub const fn wall_jumped_backwards(self) -> bool {
        self.wall_ns < 0
    }

    /// True when the wall clock moved forwards by more than `tolerance_ns` over a
    /// monotonic interval `mono_ns` long — an NTP step forward, not clock skew.
    #[must_use]
    pub const fn wall_jumped_forwards(self, tolerance_ns: i128) -> bool {
        self.wall_ns - self.mono_ns as i128 > tolerance_ns
    }
}

/// Compute the interval between two readings of one timeline.
///
/// # Errors
///
/// [`ClockError::NotComparable`] when the boots or epochs differ, and
/// [`ClockError::MonoWentBackwards`] when the later monotonic reading is earlier.
pub fn interval_between(
    previous: ClockReading,
    current: ClockReading,
) -> Result<Interval, ClockError> {
    if previous.timeline != current.timeline {
        return Err(ClockError::NotComparable {
            previous: previous.timeline,
            current: current.timeline,
        });
    }
    let mono = current
        .mono_ns
        .checked_sub(previous.mono_ns)
        .ok_or(ClockError::MonoWentBackwards)?;
    Ok(Interval {
        mono_ns: mono,
        // Widened subtraction: a wall step backwards is a negative `i128`, never an
        // underflowed `u64` and never clamped to zero.
        wall_ns: i128::from(current.wall_ns) - i128::from(previous.wall_ns),
    })
}

/// The clock a reader uses.
pub trait Clock: fmt::Debug {
    /// Absolute `CLOCK_MONOTONIC` nanoseconds.
    fn mono_ns(&self) -> u64;
    /// Absolute `CLOCK_BOOTTIME` nanoseconds.
    fn boot_ns(&self) -> u64;
    /// Wall nanoseconds since the Unix epoch; may step in either direction.
    fn wall_ns(&self) -> i64;
    /// The boot/epoch this clock currently belongs to.
    fn timeline(&self) -> Timeline;

    /// Take both readings for one record.
    fn read(&self) -> ClockReading {
        ClockReading {
            mono_ns: self.mono_ns(),
            wall_ns: self.wall_ns(),
            timeline: self.timeline(),
        }
    }
}

// ---------------------------------------------------------------- the real clocks

/// `CLOCK_MONOTONIC`, `CLOCK_BOOTTIME` and `CLOCK_REALTIME` read directly from the kernel.
///
/// The absolute clocks have no per-instance origin: two clocks created at different times
/// report readings on the same timeline. The boot id travels beside them so a pair that
/// spans a restart is refused rather than silently mis-subtracted.
#[derive(Clone, Copy, Debug)]
pub struct SystemClock {
    timeline: Timeline,
}

impl SystemClock {
    /// A clock for the given boot and epoch.
    ///
    /// The boot id and the epoch are read (or minted) by the caller; a consumer that
    /// compares readings from two clocks must give them the same pair (or expect
    /// [`ClockError::NotComparable`], which is the point).
    #[must_use]
    pub fn new(timeline: Timeline) -> SystemClock {
        SystemClock { timeline }
    }
}

fn timespec_ns(value: rustix::time::Timespec) -> u64 {
    let nanos = i128::from(value.tv_sec)
        .saturating_mul(1_000_000_000)
        .saturating_add(i128::from(value.tv_nsec));
    u64::try_from(nanos).unwrap_or(u64::MAX)
}

impl Clock for SystemClock {
    fn mono_ns(&self) -> u64 {
        timespec_ns(rustix::time::clock_gettime(
            rustix::time::ClockId::Monotonic,
        ))
    }

    fn boot_ns(&self) -> u64 {
        timespec_ns(rustix::time::clock_gettime(rustix::time::ClockId::Boottime))
    }

    fn wall_ns(&self) -> i64 {
        let value = rustix::time::clock_gettime(rustix::time::ClockId::Realtime);
        let nanos = i128::from(value.tv_sec)
            .saturating_mul(1_000_000_000)
            .saturating_add(i128::from(value.tv_nsec));
        i64::try_from(nanos).unwrap_or(if nanos.is_negative() {
            i64::MIN
        } else {
            i64::MAX
        })
    }

    fn timeline(&self) -> Timeline {
        self.timeline
    }
}

// ---------------------------------------------------------------- the injected clock

/// A clock a test (or an SP-2 experiment) advances explicitly.
///
/// The monotonic side only moves forward — `advance` refuses to move it backwards — while
/// the wall side can be stepped in either direction, which is exactly the asymmetry the
/// real clocks have and the reason a wall delta is reported rather than trusted.
#[derive(Clone, Debug)]
pub struct ManualClock {
    mono_ns: u64,
    boot_ns: u64,
    wall_ns: i64,
    timeline: Timeline,
}

impl ManualClock {
    /// A clock at `mono_ns` / `wall_ns`.
    #[must_use]
    pub const fn new(timeline: Timeline, mono_ns: u64, wall_ns: i64) -> ManualClock {
        ManualClock {
            mono_ns,
            boot_ns: mono_ns,
            wall_ns,
            timeline,
        }
    }

    /// Move all clocks forward by `mono_ns` (monotonic, boot and wall together).
    ///
    /// Advancing can only move the monotonic reading forward, so this cannot fail; an
    /// advance past `u64::MAX` (584 years of nanoseconds) saturates, which is still a
    /// forward reading and never a wrapped one.
    pub fn advance(&mut self, mono_ns: u64) -> &mut ManualClock {
        self.mono_ns = self.mono_ns.saturating_add(mono_ns);
        self.boot_ns = self.boot_ns.saturating_add(mono_ns);
        self.wall_ns = self
            .wall_ns
            .saturating_add(i64::try_from(mono_ns).unwrap_or(i64::MAX));
        self
    }

    /// Advance only the boot clock, modelling a system suspend.
    pub fn suspend(&mut self, delta_ns: u64) {
        self.boot_ns = self.boot_ns.saturating_add(delta_ns);
    }

    /// Step the wall clock by `delta_ns` without touching the monotonic clock: an NTP
    /// step, in either direction.
    pub fn step_wall(&mut self, delta_ns: i64) {
        self.wall_ns = self.wall_ns.saturating_add(delta_ns);
    }

    /// Switch to another boot/epoch (a restart), leaving the readings where they are.
    pub fn restart(&mut self, timeline: Timeline) {
        self.timeline = timeline;
    }
}

impl Clock for ManualClock {
    fn mono_ns(&self) -> u64 {
        self.mono_ns
    }

    fn boot_ns(&self) -> u64 {
        self.boot_ns
    }

    fn wall_ns(&self) -> i64 {
        self.wall_ns
    }

    fn timeline(&self) -> Timeline {
        self.timeline
    }
}

#[cfg(test)]
mod tests {
    use super::{interval_between, Clock, ClockError, ManualClock, SystemClock, Timeline};

    const BOOT_A: [u8; 16] = [0x11; 16];
    const BOOT_B: [u8; 16] = [0x22; 16];
    const WALL: i64 = 1_700_000_000_000_000_000;

    fn timeline(epoch: u64) -> Timeline {
        Timeline::new(BOOT_A, epoch)
    }

    #[test]
    fn a_monotonic_interval_is_exact_and_a_wall_jump_keeps_its_sign() {
        let mut clock = ManualClock::new(timeline(1), 1_000, WALL);
        let first = clock.read();
        clock.advance(2_000_000_000);
        let second = clock.read();
        let interval = interval_between(first, second).expect("one timeline");
        assert_eq!(interval.mono_ns, 2_000_000_000);
        assert_eq!(interval.wall_ns, 2_000_000_000);
        assert!(!interval.wall_jumped_backwards());
        assert!(!interval.wall_jumped_forwards(1_000_000));

        // An NTP step backwards is still a two-second monotonic interval: the *duration*
        // is real and only the wall label moved, so the delta is reported negative.
        clock.step_wall(-3_000_000_000);
        let third = clock.read();
        let jumped = interval_between(second, third).expect("same boot");
        assert_eq!(jumped.mono_ns, 0, "no monotonic time passed");
        assert_eq!(jumped.wall_ns, -3_000_000_000);
        assert!(jumped.wall_jumped_backwards());

        clock.advance(1_000_000_000);
        let fourth = clock.read();
        let after = interval_between(third, fourth).expect("same boot");
        assert_eq!(after.mono_ns, 1_000_000_000);
        assert!(
            !after.wall_jumped_backwards(),
            "an ordinary advance moves both clocks forward"
        );
        assert_eq!(
            after.wall_ns, 1_000_000_000,
            "the advance moved the wall clock forward from its stepped position"
        );

        // A forward step larger than the elapsed monotonic time is a jump forward too.
        clock.step_wall(10_000_000_000);
        let fifth = clock.read();
        let forward = interval_between(fourth, fifth).expect("same boot");
        assert_eq!(forward.mono_ns, 0);
        assert!(forward.wall_jumped_forwards(1_000_000));
        assert_eq!(forward.wall_ns, 10_000_000_000);
        assert!(!forward.wall_jumped_backwards());
    }

    #[test]
    fn readings_across_a_restart_are_refused_and_never_subtracted() {
        let mut clock = ManualClock::new(timeline(1), 5_000, WALL);
        let before = clock.read();
        clock.restart(timeline(2));
        clock.restart(Timeline::new(BOOT_B, 2));
        let after = clock.read();
        assert_eq!(
            interval_between(before, after),
            Err(ClockError::NotComparable {
                previous: timeline(1),
                current: Timeline::new(BOOT_B, 2),
            })
        );
        let rendered = ClockError::NotComparable {
            previous: timeline(1),
            current: Timeline::new(BOOT_B, 2),
        }
        .to_string();
        assert!(rendered.contains("cannot be compared"), "{rendered}");
    }

    #[test]
    fn a_swapped_pair_is_refused_and_a_missing_interval_is_stated() {
        let mut clock = ManualClock::new(timeline(1), 10, WALL);
        let later = clock.read();
        let earlier = super::ClockReading {
            mono_ns: 5,
            ..later
        };
        assert_eq!(
            interval_between(later, earlier),
            Err(ClockError::MonoWentBackwards)
        );
        assert_eq!(
            ClockError::MonoWentBackwards.to_string(),
            "the later reading is earlier on the monotonic clock"
        );
        // An interval of exactly zero is legal: two readings at one instant. It is the
        // rate code that refuses to divide by it, not this layer.
        assert_eq!(
            interval_between(earlier, earlier).map(|interval| interval.mono_ns),
            Ok(0)
        );
        clock.advance(0);
        assert_eq!(clock.mono_ns(), 10, "advancing by nothing changes nothing");
    }

    #[test]
    fn the_manual_clock_never_moves_its_monotonic_reading_backwards() {
        let mut clock = ManualClock::new(timeline(1), u64::MAX - 1, WALL);
        // The addition saturates at the end of the range instead of wrapping; a wrapped
        // monotonic clock would produce a huge negative interval.
        clock.advance(10);
        assert_eq!(
            clock.mono_ns(),
            u64::MAX,
            "an advance past u64::MAX saturates rather than wrapping"
        );
        assert_eq!(clock.mono_ns(), u64::MAX);
        assert_eq!(clock.wall_ns(), WALL.saturating_add(10));
    }

    #[test]
    fn manual_advance_moves_boot_and_suspend_moves_only_boot() {
        let mut clock = ManualClock::new(timeline(1), 10, WALL);
        assert_eq!(clock.boot_ns(), clock.mono_ns());
        clock.advance(5);
        assert_eq!(clock.mono_ns(), 15);
        assert_eq!(clock.boot_ns(), 15);
        clock.suspend(7);
        assert_eq!(clock.mono_ns(), 15);
        assert_eq!(clock.boot_ns(), 22);
        assert_eq!(clock.wall_ns(), WALL.saturating_add(5));
    }

    #[test]
    fn system_clocks_read_one_absolute_monotonic_timeline() {
        let first = SystemClock::new(Timeline::new(BOOT_A, 9));
        let first_ns = first.mono_ns();
        let second = SystemClock::new(Timeline::new(BOOT_A, 9));
        assert!(second.mono_ns() >= first_ns);
        let mono_ns = second.mono_ns();
        let boot_ns = second.boot_ns();
        assert!(
            boot_ns >= mono_ns,
            "boot time includes suspended time: boot={boot_ns}, mono={mono_ns}"
        );
    }

    #[test]
    fn the_system_clock_moves_forward_and_reports_its_timeline() {
        let clock: SystemClock = SystemClock::new(Timeline::new(BOOT_A, 9));
        assert_eq!(clock.timeline(), Timeline::new(BOOT_A, 9));
        let first = clock.read();
        let second = clock.read();
        let interval = interval_between(first, second).expect("one boot");
        assert_eq!(
            interval.mono_ns, interval.mono_ns,
            "a monotonic interval is unsigned"
        );
        assert!(
            interval.wall_ns <= 1_000_000_000_000_000,
            "two reads are microseconds apart, not decades"
        );
        assert!(first.mono_ns > 0, "monotonic readings are absolute");
        assert!(
            clock.boot_ns() >= first.mono_ns,
            "boot time is at least monotonic time"
        );
        assert!(first.wall_ns > 1_600_000_000_000_000_000, "after 2020");
    }
}
