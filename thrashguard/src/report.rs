//! What the guard *would* do, and why — the report-mode decision (design §2 D3/D4,
//! §5 row P1: "report mode (what would be done, and why)").
//!
//! P1 compiles no act path, so the only output of a trigger is a decision plus a
//! reason word. Keeping that decision in its own module and free of I/O, clock and
//! journal is deliberate: it is the part a later cut must keep identical when the
//! action becomes real, and it is testable without a fake host.
//!
//! ## The reasons are a closed vocabulary
//!
//! A reason is what a reader sees in `status` and `events`, so it is a word, not a
//! sentence: `pressure` (the trigger held), `no-candidates`, `max-parked`,
//! `never-park-last`, `thaw-grace`, `rule-unknown` and `nothing-to-do`. A
//! **`rule-unknown`** is not a fallback to zero: design §8 records that `refault`
//! and `smallest-enough` have no readable input yet, and the rule must say so
//! while ordering by `newest` rather than inventing a rate.

use crate::config::{Config, VictimRule};
use crate::units::{Registration, Store};

/// The two invariants that decide whether one more unit may be parked (design §2 D4).
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub struct ParkingState {
    /// How many units are parked right now (P1: how many it would have parked).
    pub parked: usize,
    /// How many candidates the store holds.
    pub candidates: usize,
}

/// Why nothing is parked this tick.
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum Why {
    /// The trigger held; one unit is chosen.
    Pressure,
    /// No registered or static candidate exists.
    NoCandidates,
    /// `max_parked` is reached.
    MaxParked,
    /// `never_park_last` would leave nothing running.
    NeverParkLast,
    /// The chosen unit was thawed too recently (`thaw_grace_s`).
    ThawGrace,
    /// Nothing is triggered and nothing is parked.
    NothingToDo,
}

impl Why {
    /// The word the wire and the journal carry.
    pub fn as_str(self) -> &'static str {
        match self {
            Self::Pressure => "pressure",
            Self::NoCandidates => "no-candidates",
            Self::MaxParked => "max-parked",
            Self::NeverParkLast => "never-park-last",
            Self::ThawGrace => "thaw-grace",
            Self::NothingToDo => "nothing-to-do",
        }
    }
}

/// What one tick would do to one unit.
#[derive(Clone, Debug, PartialEq, Eq)]
pub enum Would {
    /// One unit would be parked (P2 freezes it).
    Park {
        /// The unit's id.
        id: String,
        /// The unit name.
        unit: String,
        /// The scope name.
        cgroup: String,
        /// The scope's inode.
        cgroup_id: u64,
        /// Why this unit: the rule that put it first.
        rule: VictimRule,
    },
    /// Nothing would be parked, and this is the reason.
    Hold {
        /// Why.
        why: Why,
    },
    /// A unit would be thawed (P2 thaws it).
    Thaw {
        /// The unit's id.
        id: String,
        /// The unit name.
        unit: String,
        /// The scope name.
        cgroup: String,
    },
}

/// The victim rule this build can honour, and the word for one it cannot.
///
/// `newest` needs only registration order. `refault` needs `memory.stat` refault
/// counters that nothing reads yet, and `smallest-enough` needs a reclaim
/// estimate; design §8 makes "say `unknown`, never a zero rate" the rule, and
/// this build therefore reports `rule-unknown` and orders by `newest`.
pub fn effective_rule(config: &Config) -> (VictimRule, Option<&'static str>) {
    match config.victim_rule {
        VictimRule::Newest => (VictimRule::Newest, None),
        VictimRule::Refault | VictimRule::SmallestEnough => (VictimRule::Newest, Some("rule-unknown")),
    }
}

/// The next unit to park, or the reason nothing is.
///
/// `thawed_at_ns` maps a unit id to the monotonic instant it was last thawed, so
/// `thaw_grace_s` is enforced without the planner reading a clock.
pub fn choose_victim(
    config: &Config,
    store: &Store,
    state: ParkingState,
    now_ns: u64,
    thawed_at_ns: &dyn Fn(&str) -> Option<u64>,
) -> Would {
    if state.candidates == 0 {
        return Would::Hold { why: Why::NoCandidates };
    }
    let cap = usize::try_from(config.max_parked).unwrap_or(usize::MAX);
    let invariant_cap = if config.never_park_last {
        state.candidates.saturating_sub(1)
    } else {
        usize::MAX
    };
    if state.parked >= cap && cap != usize::MAX {
        return Would::Hold { why: Why::MaxParked };
    }
    if state.parked >= invariant_cap {
        return Would::Hold { why: Why::NeverParkLast };
    }
    let grace_ns = config.thaw_grace_s.saturating_mul(1_000_000_000);
    for candidate in store.newest_first() {
        if let Some(thawed) = thawed_at_ns(&candidate.id) {
            if now_ns.saturating_sub(thawed) < grace_ns {
                continue;
            }
        }
        return Would::Park {
            id: candidate.id.clone(),
            unit: unit_of(candidate),
            cgroup: candidate.scope.name.clone(),
            cgroup_id: candidate.scope.cgroup_id,
            rule: effective_rule(config).0,
        };
    }
    Would::Hold { why: Why::ThawGrace }
}

/// The unit name a candidate is reported under: the registrant's `run` when it
/// sent one, else its `id`. The design's events carry `unit` and `run` separately,
/// and P1 has one name per candidate, so the more specific one is used.
fn unit_of(candidate: &Registration) -> String {
    if candidate.run.is_empty() {
        candidate.id.clone()
    } else {
        candidate.run.clone()
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::units::{Priority, Scope, Store};
    use std::collections::BTreeMap;
    use std::path::PathBuf;

    fn scope(id: &str, inode: u64) -> Scope {
        Scope {
            name: format!("docker-{id}.scope"),
            relative: format!("dev.slice/dev-gates.slice/docker-{id}.scope"),
            path: PathBuf::from("/cgroup"),
            cgroup_id: inode,
            shadowed: Vec::new(),
        }
    }

    fn store_with(ids: &[&str]) -> Store {
        let mut config = Config::defaults();
        config.heartbeat_s = 30;
        let mut store = Store::from_config(&config);
        for (index, id) in ids.iter().enumerate() {
            store
                .register(Registration {
                    id: id.to_string(),
                    container: format!("container-{id}"),
                    scope: scope(id, 100 + index as u64),
                    score: 1,
                    priority: Priority::Freezable,
                    kind: "gate".to_string(),
                    owner: "run-gate".to_string(),
                    run: format!("run-{id}"),
                    heartbeat_s: None,
                    registered_seq: 0,
                    last_heartbeat_ns: 0,
                })
                .expect("register");
        }
        store
    }

    fn nothing_thawed(_id: &str) -> Option<u64> {
        None
    }

    #[test]
    fn the_newest_rule_picks_the_most_recently_registered_unit() {
        let config = Config::defaults();
        let store = store_with(&["old", "middle", "newest"]);
        let state = ParkingState { parked: 0, candidates: 3 };
        let would = choose_victim(&config, &store, state, 0, &nothing_thawed);
        let Would::Park { id, unit, cgroup, cgroup_id, rule } = would else { panic!("{would:?}") };
        assert_eq!(id, "newest");
        assert_eq!(unit, "run-newest", "the run id is the unit name the events carry");
        assert_eq!(cgroup, "docker-newest.scope");
        assert_eq!(cgroup_id, 102, "the inode comes from the resolved scope");
        assert_eq!(rule, VictimRule::Newest);
    }

    #[test]
    fn an_empty_candidate_set_holds_with_a_reason_that_is_not_a_silent_noop() {
        let config = Config::defaults();
        let store = store_with(&[]);
        let state = ParkingState { parked: 0, candidates: 0 };
        assert_eq!(choose_victim(&config, &store, state, 0, &nothing_thawed), Would::Hold { why: Why::NoCandidates });
    }

    #[test]
    fn max_parked_bounds_the_parks_and_never_park_last_keeps_one_running() {
        let mut config = Config::defaults();
        config.max_parked = 2;
        let store = store_with(&["a", "b", "c"]);
        let at_cap = ParkingState { parked: 2, candidates: 3 };
        assert_eq!(
            choose_victim(&config, &store, at_cap, 0, &nothing_thawed),
            Would::Hold { why: Why::MaxParked },
            "two parked is the configured maximum"
        );
        // `never_park_last` is the tighter bound when the candidate set is small:
        // of two candidates only one may be parked, whatever `max_parked` allows.
        let small = store_with(&["a", "b"]);
        config.max_parked = 8;
        let one_of_two = ParkingState { parked: 1, candidates: 2 };
        assert_eq!(
            choose_victim(&config, &small, one_of_two, 0, &nothing_thawed),
            Would::Hold { why: Why::NeverParkLast },
            "something must always be able to run"
        );
        // With the invariant disabled, that same state parks the second unit.
        config.never_park_last = false;
        let second = choose_victim(&config, &small, one_of_two, 0, &nothing_thawed);
        assert!(matches!(second, Would::Park { .. }), "{second:?}");
    }

    #[test]
    fn a_just_thawed_unit_is_skipped_until_the_grace_has_passed() {
        let mut config = Config::defaults();
        config.thaw_grace_s = 30;
        let store = store_with(&["older", "fresh"]);
        let state = ParkingState { parked: 0, candidates: 2 };
        const S: u64 = 1_000_000_000;
        let thawed = |id: &str| if id == "fresh" { Some(0) } else { None };
        // One second after the thaw the grace (30 s) still holds; at exactly 30 s
        // it is over, so the newer unit wins again.
        let would = choose_victim(&config, &store, state, S, &thawed);
        let Would::Park { id, .. } = would else { panic!("{would:?}") };
        assert_eq!(id, "older", "a unit inside its grace window is not the victim");
        let would = choose_victim(&config, &store, state, 30 * S, &thawed);
        let Would::Park { id, .. } = would else { panic!("{would:?}") };
        assert_eq!(id, "fresh", "the newer unit wins once its grace has passed");
        // Every candidate inside its grace is a named hold, never a panic or a park.
        let all_thawed = |_id: &str| Some(0);
        assert_eq!(
            choose_victim(&config, &store, state, 5 * S, &all_thawed),
            Would::Hold { why: Why::ThawGrace }
        );
    }

    #[test]
    fn a_rule_with_no_readable_input_says_unknown_and_orders_by_newest() {
        let mut config = Config::defaults();
        config.victim_rule = VictimRule::Refault;
        assert_eq!(effective_rule(&config), (VictimRule::Newest, Some("rule-unknown")));
        let store = store_with(&["a", "b"]);
        let state = ParkingState { parked: 0, candidates: 2 };
        let would = choose_victim(&config, &store, state, 0, &nothing_thawed);
        let Would::Park { id, rule, .. } = would else { panic!("{would:?}") };
        assert_eq!(id, "b", "never a zero refault rate: the newest order still decides");
        assert_eq!(rule, VictimRule::Newest);
        config.victim_rule = VictimRule::SmallestEnough;
        assert_eq!(effective_rule(&config).1, Some("rule-unknown"));
        // `newest` is the only rule with a known input, and it says nothing extra.
        config.victim_rule = VictimRule::Newest;
        assert_eq!(effective_rule(&config), (VictimRule::Newest, None));
    }

    #[test]
    fn a_last_resort_unit_is_chosen_only_after_the_freezable_ones() {
        let config = Config::defaults();
        let mut store = store_with(&["freezable-1"]);
        store
            .register(Registration {
                id: "last".to_string(),
                container: "c".to_string(),
                scope: scope("last", 9),
                score: 0,
                priority: Priority::LastResort,
                kind: "gate".to_string(),
                owner: "run-gate".to_string(),
                run: String::new(),
                heartbeat_s: None,
                registered_seq: 0,
                last_heartbeat_ns: 0,
            })
            .expect("register");
        let state = ParkingState { parked: 0, candidates: 2 };
        let would = choose_victim(&config, &store, state, 0, &nothing_thawed);
        let Would::Park { id, .. } = would else { panic!("{would:?}") };
        assert_eq!(id, "freezable-1", "the last-resort unit is not first");
        // A unit without a `run` is reported under its id, not under an empty name.
        let mut thaw_map: BTreeMap<&str, u64> = BTreeMap::new();
        thaw_map.insert("freezable-1", 0);
        let thawed = |candidate: &str| thaw_map.get(candidate).copied();
        let would = choose_victim(&config, &store, state, 100, &thawed);
        let Would::Park { id, unit, .. } = would else { panic!("{would:?}") };
        assert_eq!(id, "last");
        assert_eq!(unit, "last");
    }
}
