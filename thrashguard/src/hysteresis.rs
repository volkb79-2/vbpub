//! The trigger/release state machine (design §2, D6/L1).
//!
//! The trigger is host memory PSI `full avg10 >= trigger_avg10` held for
//! `hold_s`; the release is `full avg10 <= trigger_avg10 / release_avg10_factor`
//! held for `release_s`. The factor-of-two hysteresis is what stops flapping: a
//! host hovering at the trigger cannot alternate between park and thaw every tick.
//!
//! ## Two facts, kept apart on purpose
//!
//! - **The guard's mode** ([`Hysteresis::is_parking`]): the trigger has fired and
//!   pressure has not yet released. While it is set, units may be parked.
//! - **The hold timer** ([`Phase`]): which condition is currently accumulating
//!   seconds. A hold is only satisfied by a *continuous* interval, so one quiet
//!   tick inside the window restarts it.
//!
//! Keeping them apart is what makes the dead band between the release point and
//! the trigger behave: in that band the guard stays in its mode (already parked
//! work stays parked) while every half-formed hold is abandoned. A single
//! `Phase` field could not express that — it loses "was triggered" exactly when
//! the release hold begins.
//!
//! - **`unknown` is not calm.** It clears both facts and asks for a thaw, so
//!   nothing stays frozen on the strength of a failed read.
//! - **A decision is a pure function of (mode, hold, reading, clock).** The
//!   caller supplies the monotonic instant, so any test cadence is legal and no
//!   assertion depends on host speed.

use crate::psi::Reading;

/// Which hold is currently accumulating, for reporting a tick.
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum Phase {
    /// No hold in progress.
    Idle,
    /// The trigger is satisfied and has not yet held for `hold_s`.
    Armed {
        /// Monotonic nanoseconds when the hold began.
        since_ns: u64,
    },
    /// Pressure is below the release point and has not yet held for `release_s`.
    Releasing {
        /// Monotonic nanoseconds when the release condition began.
        since_ns: u64,
    },
}

/// The action a decision demands of the caller this tick.
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum Action {
    /// Keep doing what the mode says.
    None,
    /// The trigger has just held: the guard entered parking mode (P2 acts; P1 journals).
    Freeze,
    /// Pressure released, or became `unknown` while the guard was parking: thaw.
    Thaw,
    /// Pressure became `unknown` while the guard held nothing: report it, act on nothing.
    Unknown,
}

/// What one observation decided.
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub struct Decision {
    /// Which hold is accumulating after this observation.
    pub phase: Phase,
    /// Whether the guard is in parking mode after this observation.
    pub parking: bool,
    /// What the caller must do now.
    pub action: Action,
}

/// The hysteresis machine: a mode, a hold timer and the two configured holds.
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub struct Hysteresis {
    /// The trigger threshold in hundredths.
    pub trigger_avg10: u32,
    /// The release threshold in hundredths (`trigger / factor`).
    pub release_avg10: u32,
    /// The trigger hold in nanoseconds.
    pub hold_ns: u64,
    /// The release hold in nanoseconds.
    pub release_ns: u64,
    phase: Phase,
    parked_since_ns: Option<u64>,
}

impl Hysteresis {
    /// A machine that holds nothing and no hold in progress.
    pub fn new(trigger_avg10: u32, release_avg10: u32, hold_s: u64, release_s: u64) -> Self {
        Self {
            trigger_avg10,
            release_avg10,
            hold_ns: hold_s.saturating_mul(1_000_000_000),
            release_ns: release_s.saturating_mul(1_000_000_000),
            phase: Phase::Idle,
            parked_since_ns: None,
        }
    }

    /// Which hold is accumulating now.
    pub fn phase(&self) -> Phase {
        self.phase
    }

    /// Whether the guard is in parking mode (the trigger fired and has not released).
    pub fn is_parking(&self) -> bool {
        self.parked_since_ns.is_some()
    }

    /// The monotonic instant the trigger completed, while the guard is parking.
    ///
    /// This is the instant the guard *started acting*, which is what D11's
    /// oldest-first thaw ordering needs; a park's own age is the daemon's fact.
    pub fn parking_since_ns(&self) -> Option<u64> {
        self.parked_since_ns
    }

    /// Observe one reading at `now_ns` (monotonic) and decide.
    pub fn observe(&mut self, reading: &Reading, now_ns: u64) -> Decision {
        let Some(avg10) = reading.avg10() else {
            // Unreadable pressure is not zero pressure. A hold cannot survive a
            // gap, and a guard that is parking thaws what it froze.
            let was_parking = self.parked_since_ns.is_some();
            self.parked_since_ns = None;
            self.phase = Phase::Idle;
            return Decision {
                phase: self.phase,
                parking: false,
                action: if was_parking { Action::Thaw } else { Action::Unknown },
            };
        };
        if avg10 >= self.trigger_avg10 {
            let since = match self.phase {
                Phase::Armed { since_ns } => since_ns,
                Phase::Idle | Phase::Releasing { .. } => now_ns,
            };
            if now_ns.saturating_sub(since) >= self.hold_ns {
                let was_parking = self.parked_since_ns.is_some();
                self.parked_since_ns = Some(self.parked_since_ns.unwrap_or(now_ns));
                self.phase = Phase::Idle;
                return Decision {
                    phase: self.phase,
                    parking: true,
                    action: if was_parking { Action::None } else { Action::Freeze },
                };
            }
            self.phase = Phase::Armed { since_ns: since };
            return Decision { phase: self.phase, parking: self.is_parking(), action: Action::None };
        }
        if avg10 <= self.release_avg10 {
            let since = match self.phase {
                Phase::Releasing { since_ns } => since_ns,
                Phase::Idle | Phase::Armed { .. } => now_ns,
            };
            if now_ns.saturating_sub(since) >= self.release_ns {
                let was_parking = self.parked_since_ns.take().is_some();
                self.phase = Phase::Idle;
                return Decision {
                    phase: self.phase,
                    parking: false,
                    action: if was_parking { Action::Thaw } else { Action::None },
                };
            }
            self.phase = Phase::Releasing { since_ns: since };
            return Decision { phase: self.phase, parking: self.is_parking(), action: Action::None };
        }
        // The dead band: neither condition is satisfied. The guard keeps its mode
        // (parked work stays parked — that is the point of hysteresis) and every
        // half-formed hold is abandoned, because a hold must be continuous.
        self.phase = Phase::Idle;
        Decision { phase: self.phase, parking: self.is_parking(), action: Action::None }
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::psi::{Pressure, Row};

    const S: u64 = 1_000_000_000;

    fn reading(hundredths: u32) -> Reading {
        Reading::Known(Pressure { some: Row::default(), full: Row { avg10: hundredths, ..Row::default() } })
    }

    fn unreadable() -> Reading {
        Reading::Unknown(crate::psi::Unreadable::Malformed("gone".into()))
    }

    /// Trigger 3.00 (300), release at half (150), hold 10 s, release 60 s.
    fn machine() -> Hysteresis {
        Hysteresis::new(300, 150, 10, 60)
    }

    #[test]
    fn a_trigger_holds_before_it_fires_and_a_gap_re_arms_it() {
        let mut machine = machine();
        assert_eq!(machine.observe(&reading(299), 0).phase, Phase::Idle);
        // Held from t=0, but only 5 s of trigger: armed, not fired.
        assert_eq!(machine.observe(&reading(300), 0).phase, Phase::Armed { since_ns: 0 });
        assert!(!machine.is_parking());
        assert_eq!(machine.observe(&reading(700), 5 * S).action, Action::None);
        // One quiet tick inside the hold window re-arms the clock: the trigger
        // is "held for hold_s", not "seen hold_s apart".
        assert_eq!(machine.observe(&reading(299), 8 * S).phase, Phase::Idle);
        assert_eq!(machine.observe(&reading(300), 9 * S).phase, Phase::Armed { since_ns: 9 * S });
        assert_eq!(machine.observe(&reading(300), 18 * S).action, Action::None);
        // 10 s after the re-arm: it fires, once.
        let decision = machine.observe(&reading(310), 19 * S);
        assert_eq!(decision.action, Action::Freeze);
        assert!(decision.parking);
        assert_eq!(machine.parking_since_ns(), Some(19 * S));
        // A further high tick does not fire again: the guard is already parking.
        assert_eq!(machine.observe(&reading(400), 20 * S).action, Action::None);
        assert!(machine.is_parking());
    }

    #[test]
    fn a_release_holds_before_it_thaws_and_the_dead_band_keeps_the_park() {
        let mut machine = machine();
        machine.observe(&reading(300), 0);
        machine.observe(&reading(300), 15 * S);
        assert!(machine.is_parking(), "10 s of trigger fired at the first tick past the hold");
        // Below the release point (150) for 30 s: releasing, no thaw yet, still parking.
        let decision = machine.observe(&reading(100), 30 * S);
        assert_eq!(decision.phase, Phase::Releasing { since_ns: 30 * S });
        assert!(decision.parking);
        assert_eq!(decision.action, Action::None);
        // Rising into the dead band abandons the release hold and KEEPS the park.
        let decision = machine.observe(&reading(200), 50 * S);
        assert_eq!(decision.phase, Phase::Idle);
        assert!(decision.parking);
        assert_eq!(decision.action, Action::None);
        // A fresh release interval has to start again: 59 s is not 60.
        machine.observe(&reading(100), 60 * S);
        assert_eq!(machine.observe(&reading(100), 119 * S).action, Action::None);
        let decision = machine.observe(&reading(100), 120 * S);
        assert_eq!(decision.action, Action::Thaw);
        assert!(!decision.parking);
        assert_eq!(machine.parking_since_ns(), None);
    }

    #[test]
    fn unreadable_pressure_thaws_a_parking_guard_and_only_reports_a_calm_one() {
        let mut machine = machine();
        let decision = machine.observe(&unreadable(), 0);
        assert_eq!(decision.action, Action::Unknown);
        assert_eq!(decision.phase, Phase::Idle);
        machine.observe(&reading(300), S);
        machine.observe(&reading(300), 12 * S);
        assert!(machine.is_parking());
        let decision = machine.observe(&unreadable(), 13 * S);
        assert_eq!(decision.action, Action::Thaw);
        assert!(!decision.parking);
        // And an `unknown` inside an armed hold cancels it: a hold must be continuous.
        machine.observe(&reading(300), 20 * S);
        assert_eq!(machine.phase(), Phase::Armed { since_ns: 20 * S });
        machine.observe(&unreadable(), 25 * S);
        assert_eq!(machine.phase(), Phase::Idle);
    }

    #[test]
    fn the_thresholds_are_exact_at_the_boundaries() {
        let mut machine = Hysteresis::new(300, 150, 0, 0);
        // With zero holds the trigger fires on the first satisfied reading, and
        // exactly at the threshold counts as satisfied (>=).
        let decision = machine.observe(&reading(300), 0);
        assert_eq!(decision.action, Action::Freeze);
        // Released at exactly the release point (<=), not one hundredth above it.
        let decision = machine.observe(&reading(151), 1);
        assert_eq!(decision.action, Action::None, "151 is in the dead band");
        assert!(decision.parking);
        let decision = machine.observe(&reading(150), 2);
        assert_eq!(decision.action, Action::Thaw);
        assert!(!decision.parking);
    }

    #[test]
    fn the_default_holds_and_factor_describe_the_shipped_shape() {
        let config = crate::config::Config::defaults();
        let machine = Hysteresis::new(
            config.trigger_avg10,
            config.release_avg10(),
            config.hold_s,
            config.release_s,
        );
        assert_eq!(machine.trigger_avg10, 300, "trigger 3.00 by default (design §2)");
        assert_eq!(machine.release_avg10, 150, "release at 1.50: the factor-of-two hysteresis");
        assert_eq!(machine.hold_ns, 10 * S);
        assert_eq!(machine.release_ns, 60 * S);
        assert!(!machine.is_parking());
    }
}
