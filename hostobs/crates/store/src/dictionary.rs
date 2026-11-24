//! The active incarnation index: bounded in RAM, epoch-scoped, never reusing an id, and
//! retiring an entry only when no stored segment can still reference it.
//!
//! Plan v4 §A.4 bounds the dictionary by `dict_active_max` and states the loss rule: *"inactive
//! incarnations retire with the segments that reference them"*. §C.1 says an incarnation id is
//! minted once per `(mount id, cgroup inode)`, the path is only a label, and a retired key that
//! reappears is minted again as a **fresh** id.
//!
//! # What that means concretely
//!
//! * **An id is issued once per epoch and never reused.** [`ActiveDictionary::next_inc`] only
//!   grows. Adopting an id that was issued and is no longer held is
//!   [`StoreError::IncarnationReused`], so a segment read from disk can never resurrect a
//!   retired id and silently join two lifetimes (that is exactly the sampler's path-keyed
//!   defect in id form).
//! * **Retirement is driven by segment reachability, not by a timer.** A cgroup that disappears
//!   is still referenced by the segments that hold its data, so it stays in the index until
//!   those segments are evicted. [`ActiveDictionary::retire_unreachable`] takes the set of ids
//!   that any stored record names and removes everything else — and it removes nothing else,
//!   because an id still named by a segment must resolve for as long as that segment is readable.
//! * **The index is bounded.** [`ActiveDictionary::mint`] refuses at `max_entries`
//!   ([`StoreError::DictionaryFull`]) instead of growing: a bounded structure that silently
//!   grows is the failure this crate exists to prevent. Retirement is what frees a slot.
//! * **A label is not an identity.** Minting the same label twice produces two ids (a path can
//!   be reused by a different cgroup, §C.1); minting for the same id with a new label keeps the
//!   id and updates the label.
//! * **An unknown id is a typed refusal**, never a default entry
//!   ([`StoreError::UnknownIncarnation`]).
//!
//! # Why this is not `model::IncarnationRegistry`
//!
//! The model registry mints from `(mount id, inode)` keys and retires only after consecutive
//! scan rounds; this index is the epoch's stored-id table and retires against segment
//! reachability. They answer different state-machine questions even though both ultimately
//! issue incarnation ids. The M2 probe owns the boundary between the two (plan v4 §A.3:
//! `bins/probe` is where the registry and the store meet).

use std::collections::{BTreeMap, BTreeSet};

use crate::error::StoreError;

/// The default bound on live entries (plan v4 §A.4's `dict_active_max` proposal).
pub const DEFAULT_ACTIVE_MAX: usize = 65_536;

/// The longest label the index will hold, in bytes (plan v4 §B.3's entry text bound).
pub const MAX_LABEL: usize = 256;

/// One live incarnation.
#[derive(Clone, Debug, Eq, PartialEq)]
pub struct Incarnation {
    /// The id, issued once for this epoch.
    pub inc: u64,
    /// The label for humans (a cgroup path), never an identity.
    pub label: String,
}

/// What one retirement pass removed.
#[derive(Clone, Debug, Default, Eq, PartialEq)]
pub struct Retired {
    /// The ids no stored segment references any more, in id order.
    pub ids: Vec<u64>,
}

impl Retired {
    /// How many entries this pass removed.
    #[must_use]
    pub fn len(&self) -> usize {
        self.ids.len()
    }

    /// True when the pass removed nothing.
    #[must_use]
    pub fn is_empty(&self) -> bool {
        self.ids.is_empty()
    }
}

/// The active incarnation index of one epoch.
#[derive(Clone, Debug)]
pub struct ActiveDictionary {
    epoch: u64,
    max_entries: usize,
    next_inc: u64,
    entries: BTreeMap<u64, Incarnation>,
    retired: u64,
}

impl ActiveDictionary {
    /// An empty index for `epoch`, bounded by `max_entries`.
    ///
    /// # Errors
    ///
    /// [`StoreError::Config`] when `max_entries` is zero (an index that can hold nothing is a
    /// caller defect, not a bound).
    pub fn new(epoch: u64, max_entries: usize) -> Result<ActiveDictionary, StoreError> {
        if max_entries == 0 {
            return Err(StoreError::Config {
                key: "dict_active_max",
                reason: "must be at least one entry",
            });
        }
        Ok(ActiveDictionary {
            epoch,
            max_entries,
            next_inc: 1,
            entries: BTreeMap::new(),
            retired: 0,
        })
    }

    /// An empty index with the default bound.
    ///
    /// # Errors
    ///
    /// Never in practice: the default bound is not zero.
    pub fn with_default_bound(epoch: u64) -> Result<ActiveDictionary, StoreError> {
        ActiveDictionary::new(epoch, DEFAULT_ACTIVE_MAX)
    }

    /// The epoch this index belongs to.
    #[must_use]
    pub const fn epoch(&self) -> u64 {
        self.epoch
    }

    /// The entry bound.
    #[must_use]
    pub const fn max_entries(&self) -> usize {
        self.max_entries
    }

    /// The number of live entries.
    #[must_use]
    pub fn len(&self) -> usize {
        self.entries.len()
    }

    /// True when the index holds nothing.
    #[must_use]
    pub fn is_empty(&self) -> bool {
        self.entries.is_empty()
    }

    /// The highest id this epoch has issued (`0` before the first mint).
    #[must_use]
    pub const fn highest_issued(&self) -> u64 {
        self.next_inc.saturating_sub(1)
    }

    /// How many entries this index has retired since it was built.
    #[must_use]
    pub const fn retired_count(&self) -> u64 {
        self.retired
    }

    /// The next id this index would issue.
    #[must_use]
    pub const fn next_inc(&self) -> u64 {
        self.next_inc
    }

    /// Mint a fresh id for `label`.
    ///
    /// # Errors
    ///
    /// [`StoreError::DictionaryFull`] at the entry bound, [`StoreError::BadName`] for a label
    /// that is too long or carries a control character, and [`StoreError::LengthOverflow`]
    /// when the id space is exhausted.
    pub fn mint(&mut self, label: &str) -> Result<u64, StoreError> {
        check_label(label)?;
        if self.entries.len() >= self.max_entries {
            return Err(StoreError::DictionaryFull {
                max: self.max_entries,
                live: self.entries.len(),
            });
        }
        let inc = self.next_inc;
        let next = inc.checked_add(1).ok_or(StoreError::LengthOverflow)?;
        self.next_inc = next;
        self.entries.insert(
            inc,
            Incarnation {
                inc,
                label: label.to_string(),
            },
        );
        Ok(inc)
    }

    /// Adopt an id read from a segment (a restart rebuilding the index from disk).
    ///
    /// A fresh id is accepted and raises the issuer; an id already held is accepted with its
    /// stored label; an id **below** the issuer that is not held any more is refused, because
    /// it was issued once and retired — adopting it would reuse an id.
    ///
    /// # Errors
    ///
    /// [`StoreError::IncarnationReused`] for a retired id, [`StoreError::DictionaryFull`],
    /// [`StoreError::BadName`] and [`StoreError::LengthOverflow`].
    pub fn adopt(&mut self, inc: u64, label: &str) -> Result<(), StoreError> {
        check_label(label)?;
        if inc == 0 {
            return Err(StoreError::IncarnationReused {
                epoch: self.epoch,
                inc,
                highest: self.highest_issued(),
            });
        }
        if self.entries.contains_key(&inc) {
            return Ok(());
        }
        if inc <= self.highest_issued() {
            return Err(StoreError::IncarnationReused {
                epoch: self.epoch,
                inc,
                highest: self.highest_issued(),
            });
        }
        if self.entries.len() >= self.max_entries {
            return Err(StoreError::DictionaryFull {
                max: self.max_entries,
                live: self.entries.len(),
            });
        }
        self.next_inc = inc.checked_add(1).ok_or(StoreError::LengthOverflow)?;
        self.entries.insert(
            inc,
            Incarnation {
                inc,
                label: label.to_string(),
            },
        );
        Ok(())
    }

    /// Relabel a live incarnation, keeping its id.
    ///
    /// A rename keeps the id (`model`'s rule, §C.1: the path is a label). An id the index does
    /// not hold is [`StoreError::UnknownIncarnation`], so a caller can never relabel a
    /// lifetime it is not holding.
    ///
    /// # Errors
    ///
    /// [`StoreError::UnknownIncarnation`], [`StoreError::BadName`].
    pub fn relabel(&mut self, inc: u64, label: &str) -> Result<(), StoreError> {
        check_label(label)?;
        let entry = self
            .entries
            .get_mut(&inc)
            .ok_or(StoreError::UnknownIncarnation {
                epoch: self.epoch,
                inc,
            })?;
        entry.label = label.to_string();
        Ok(())
    }

    /// The entry for one id.
    ///
    /// # Errors
    ///
    /// [`StoreError::UnknownIncarnation`] when the index does not hold it.
    pub fn lookup(&self, inc: u64) -> Result<&Incarnation, StoreError> {
        self.entries
            .get(&inc)
            .ok_or(StoreError::UnknownIncarnation {
                epoch: self.epoch,
                inc,
            })
    }

    /// The label of one id (a convenience over [`ActiveDictionary::lookup`]).
    ///
    /// # Errors
    ///
    /// [`StoreError::UnknownIncarnation`].
    pub fn label_of(&self, inc: u64) -> Result<&str, StoreError> {
        Ok(self.lookup(inc)?.label.as_str())
    }

    /// True when the index holds `inc`.
    #[must_use]
    pub fn holds(&self, inc: u64) -> bool {
        self.entries.contains_key(&inc)
    }

    /// All live entries, in id order.
    pub fn entries(&self) -> impl Iterator<Item = &Incarnation> {
        self.entries.values()
    }

    /// Retire every entry no stored record names any more.
    ///
    /// `reach` decides whether an id is still referenced, and it is called once per **live**
    /// entry. That is bounded by construction: the index cannot hold more than `max_entries`
    /// entries, so a pass is O(bound) and always finishes (an earlier draft carried a
    /// `truncated` flag for a budget that could never be reached — a status reachable only from
    /// nowhere is not a status).
    ///
    /// This is the **only** way an entry leaves the index, and it never removes an id that a
    /// record still names: a segment must resolve every id it carries for as long as it is
    /// readable (§A.4 "inactive incarnations retire with the segments that reference them").
    pub fn retire_unreachable(&mut self, mut reach: impl FnMut(u64) -> bool) -> Retired {
        let mut retired = Retired::default();
        let mut doomed: Vec<u64> = Vec::new();
        for inc in self.entries.keys().copied() {
            if !reach(inc) {
                doomed.push(inc);
            }
        }
        for inc in doomed {
            if self.entries.remove(&inc).is_some() {
                retired.ids.push(inc);
                self.retired = self.retired.saturating_add(1);
            }
        }
        retired
    }

    /// Retire against an explicit set of reachable ids (the common case: the union of every
    /// record's id table in the tiers that are still readable).
    pub fn retire_with_set(&mut self, reachable: &BTreeSet<u64>) -> Retired {
        self.retire_unreachable(|inc| reachable.contains(&inc))
    }

    /// Retire against the ids any record in the given segment id tables names.
    ///
    /// A segment's records carry their own id tables (`format`), so the caller collects them
    /// without decoding a payload; this convenience is what [`crate::tier`] uses.
    pub fn retire_with_segments(&mut self, reachable: &[BTreeSet<u64>]) -> Retired {
        self.retire_unreachable(|inc| reachable.iter().any(|set| set.contains(&inc)))
    }
}

/// Check a label against the index's bounds.
///
/// # Errors
///
/// [`StoreError::BadName`] for a label longer than [`MAX_LABEL`] or carrying a control byte
/// (a label travels into an operator report and a wire body, so a newline or a NUL would
/// break both).
pub fn check_label(label: &str) -> Result<(), StoreError> {
    if label.len() > MAX_LABEL {
        return Err(StoreError::BadName {
            name: label.chars().take(32).collect(),
        });
    }
    if label.bytes().any(|byte| byte < 0x20 || byte == 0x7f) {
        return Err(StoreError::BadName {
            name: label.chars().take(32).collect(),
        });
    }
    Ok(())
}

#[cfg(test)]
mod tests {
    use super::{check_label, ActiveDictionary, Incarnation, DEFAULT_ACTIVE_MAX, MAX_LABEL};
    use crate::error::StoreError;
    use std::collections::BTreeSet;

    fn dictionary() -> ActiveDictionary {
        ActiveDictionary::new(7, 8).expect("bounded index")
    }

    #[test]
    fn minting_returns_an_ascending_id_per_label_and_never_reuses_one() {
        let mut index = dictionary();
        assert_eq!(index.epoch(), 7);
        assert_eq!(index.highest_issued(), 0);
        assert_eq!(index.next_inc(), 1);
        let a = index.mint("/system.slice").expect("mint");
        let b = index.mint("/system.slice").expect("mint");
        assert_eq!((a, b), (1, 2), "one label can be two lifetimes");
        assert_eq!(index.len(), 2);
        assert!(!index.is_empty());
        assert_eq!(index.highest_issued(), 2);
        assert_eq!(index.label_of(1), Ok("/system.slice"));
        assert_eq!(
            index.label_of(3),
            Err(StoreError::UnknownIncarnation { epoch: 7, inc: 3 })
        );
        assert!(index.holds(1));
        assert!(!index.holds(3));
        assert_eq!(
            index.entries().map(|entry| entry.inc).collect::<Vec<_>>(),
            vec![1, 2]
        );
        assert_eq!(index.retired_count(), 0, "nothing has retired yet");
    }

    #[test]
    fn relabelling_keeps_the_id_and_an_unknown_id_cannot_be_relabelled() {
        let mut index = dictionary();
        let inc = index.mint("/docker/abc").expect("mint");
        index.relabel(inc, "/docker/xyz").expect("relabel");
        assert_eq!(index.label_of(inc), Ok("/docker/xyz"));
        assert_eq!(index.len(), 1);
        assert_eq!(
            index.relabel(9, "/x"),
            Err(StoreError::UnknownIncarnation { epoch: 7, inc: 9 })
        );
        assert_eq!(
            index.lookup(1),
            Ok(&Incarnation {
                inc: 1,
                label: "/docker/xyz".into()
            })
        );
    }

    #[test]
    fn an_index_at_its_bound_refuses_instead_of_growing() {
        let mut index = ActiveDictionary::new(3, 2).expect("bounded");
        index.mint("/a").expect("mint");
        index.mint("/b").expect("mint");
        assert_eq!(
            index.mint("/c"),
            Err(StoreError::DictionaryFull { max: 2, live: 2 })
        );
        assert_eq!(index.len(), 2);
        assert_eq!(
            ActiveDictionary::new(1, 0).map(|_| ()),
            Err(StoreError::Config {
                key: "dict_active_max",
                reason: "must be at least one entry",
            })
        );
        let default = ActiveDictionary::with_default_bound(1).expect("default");
        assert_eq!(default.max_entries(), DEFAULT_ACTIVE_MAX);
    }

    #[test]
    fn an_id_that_was_issued_and_retired_is_never_adopted_again() {
        let mut index = dictionary();
        assert_eq!(index.adopt(5, "/a").map(|_| index.highest_issued()), Ok(5));
        assert_eq!(index.adopt(5, "/b"), Ok(()), "a held id is not a reuse");
        assert_eq!(
            index.label_of(5),
            Ok("/a"),
            "adoption keeps the stored label"
        );
        let reach = BTreeSet::new();
        assert_eq!(index.retire_with_set(&reach).ids, vec![5]);
        assert_eq!(
            index.adopt(5, "/a"),
            Err(StoreError::IncarnationReused {
                epoch: 7,
                inc: 5,
                highest: 5
            }),
            "a retired id must never come back, or two lifetimes join"
        );
        assert_eq!(
            index.adopt(0, "/a"),
            Err(StoreError::IncarnationReused {
                epoch: 7,
                inc: 0,
                highest: 5
            }),
            "0 is reserved for UNASSIGNED"
        );
        assert_eq!(index.adopt(6, "/b"), Ok(()));
        // Retirement frees capacity, which is what keeps the bound live rather than terminal:
        // with id 5 gone the index holds one entry, so ids 6..=13 fill the other seven.
        for inc in 7..=13u64 {
            index.adopt(inc, "/c").expect("adopt under the bound");
        }
        assert_eq!(index.len(), 8);
        assert_eq!(
            index.adopt(14, "/d"),
            Err(StoreError::DictionaryFull { max: 8, live: 8 })
        );
    }

    #[test]
    fn retirement_removes_only_ids_no_segment_still_names() {
        let mut index = dictionary();
        for (inc, label) in [(1u64, "/a"), (2, "/b"), (3, "/c")] {
            index.adopt(inc, label).expect("adopt");
        }
        let reachable: BTreeSet<u64> = [1u64, 3].into_iter().collect();
        let retired = index.retire_with_set(&reachable);
        assert_eq!(retired.ids, vec![2]);
        assert!(!retired.is_empty());
        assert_eq!(retired.len(), 1);
        assert_eq!(index.len(), 2);
        assert_eq!(index.retired_count(), 1);
        assert!(index.holds(1) && index.holds(3) && !index.holds(2));
        assert_eq!(index.label_of(1), Ok("/a"));
        // A pass with nothing to do is empty and reports it.
        let again = index.retire_with_set(&reachable);
        assert!(again.is_empty());
        assert_eq!(again.len(), 0);
    }

    #[test]
    fn a_retirement_pass_calls_reach_once_per_live_entry_and_finishes() {
        let mut index = ActiveDictionary::new(1, 8).expect("bounded");
        for inc in 1..=5u64 {
            index.adopt(inc, "/x").expect("adopt");
        }
        let mut seen: Vec<u64> = Vec::new();
        let retired = index.retire_unreachable(|inc| {
            seen.push(inc);
            inc == 2
        });
        assert_eq!(seen, vec![1, 2, 3, 4, 5]);
        assert_eq!(retired.ids, vec![1, 3, 4, 5]);
        assert_eq!(index.len(), 1);
        assert_eq!(index.retired_count(), 4);
        assert!(index.is_empty().eq(&false));
        index.retire_unreachable(|_| false);
        assert!(index.is_empty());
        assert_eq!(index.retired_count(), 5);
    }

    #[test]
    fn a_label_is_bounded_and_carries_no_control_byte() {
        assert_eq!(check_label("/system.slice"), Ok(()));
        assert_eq!(check_label(&"a".repeat(MAX_LABEL)), Ok(()));
        assert_eq!(
            check_label(&"a".repeat(MAX_LABEL + 1)),
            Err(StoreError::BadName {
                name: "a".repeat(32)
            })
        );
        assert_eq!(
            check_label("/a\nb"),
            Err(StoreError::BadName {
                name: "/a\nb".into()
            })
        );
        assert_eq!(
            check_label("/a\u{7f}b"),
            Err(StoreError::BadName {
                name: "/a\u{7f}b".into()
            })
        );
        let mut index = dictionary();
        assert_eq!(
            index.mint("/a\0b").map(|_| ()),
            Err(StoreError::BadName {
                name: "/a\0b".into()
            })
        );
        assert_eq!(
            index.adopt(1, "\u{1f}").map(|_| ()),
            Err(StoreError::BadName {
                name: "\u{1f}".into()
            })
        );
    }
}
