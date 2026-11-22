//! The verb table of WIRE.md section 4.3 as `const` data: one row per verb with its level
//! mask, its reply shape, whether this package serves it and whether it is reserved (4.13).
//!
//! A verb that is reserved (M5/M6) or known but not served yet answers `unknown-verb` and is
//! absent from `hello.verbs`; `forbidden` and `binary-required` are still judged from the full
//! table (design R11), so the table carries every verb, served or not. P5b-2d serves `current`,
//! `subscribe` and `unsubscribe` at observe and operator.

use crate::limits::Level;

/// How a verb answers.
#[derive(Clone, Copy, Debug, Eq, PartialEq)]
pub enum Shape {
    /// One REP.
    Json,
    /// REP, BLOCK frames, END: refused in JSON mode (`binary-required`).
    Block,
}

/// One row of the verb table.
#[derive(Clone, Copy, Debug, Eq, PartialEq)]
pub struct Verb {
    /// The verb's name.
    pub name: &'static str,
    /// The levels that know the verb, as a mask of [`Level::bit`].
    pub levels: u8,
    /// The reply shape.
    pub shape: Shape,
    /// Whether this package serves it.
    pub served: bool,
    /// Whether the verb is reserved by an M5/M6 package (WIRE.md section 4.13).
    pub reserved: bool,
}

impl Verb {
    /// Whether `level` knows the verb.
    #[must_use]
    pub const fn at(&self, level: Level) -> bool {
        self.levels & level.bit() != 0
    }
}

const OBSERVE: u8 = Level::Observe.bit();
const OPERATOR: u8 = Level::Operator.bit();
const OBSERVER: u8 = Level::Observer.bit();
const CONTROL: u8 = Level::Control.bit();
const TELEMETRY: u8 = OBSERVE | OPERATOR;
const EVERY: u8 = TELEMETRY | OBSERVER | CONTROL;

const fn row(name: &'static str, levels: u8, shape: Shape, served: bool, reserved: bool) -> Verb {
    Verb {
        name,
        levels,
        shape,
        served,
        reserved,
    }
}

/// The number of rows of [`VERBS`].
pub const VERB_COUNT: usize = 25;

/// The verb table (WIRE.md section 4.3, in the table's order).
pub static VERBS: [Verb; VERB_COUNT] = [
    row("hello", EVERY, Shape::Json, true, false),
    row("status", TELEMETRY | CONTROL, Shape::Json, true, false),
    row("current", TELEMETRY, Shape::Block, true, false),
    row("subscribe", TELEMETRY, Shape::Block, true, false),
    row("unsubscribe", TELEMETRY, Shape::Json, true, false),
    row("snapshot_at", TELEMETRY, Shape::Block, true, false),
    row("series_batch", TELEMETRY, Shape::Block, true, false),
    row("events", TELEMETRY | CONTROL, Shape::Json, false, false),
    row("spans", TELEMETRY | CONTROL, Shape::Json, false, false),
    row("procs", TELEMETRY, Shape::Json, true, false),
    row("annotate", OBSERVER | CONTROL, Shape::Json, false, false),
    row("export", OPERATOR, Shape::Block, false, true),
    row("resource-limits", OBSERVER, Shape::Json, false, true),
    row("window-open", OBSERVER, Shape::Json, false, true),
    row("window-close", OBSERVER, Shape::Json, false, true),
    row("version", CONTROL, Shape::Json, false, true),
    row("host", CONTROL, Shape::Json, false, true),
    row("session.start", CONTROL, Shape::Json, false, true),
    row("session.stop", CONTROL, Shape::Json, false, true),
    row("session.status", CONTROL, Shape::Json, false, true),
    row("session.watch", CONTROL, Shape::Json, false, true),
    row("session.list", CONTROL, Shape::Json, false, true),
    row("session.spans", CONTROL, Shape::Json, false, true),
    row("gc", CONTROL, Shape::Json, false, true),
    row("reload", CONTROL, Shape::Json, false, true),
];

/// The row named `name` (the verb's bytes between its quotes), if the table has one.
#[must_use]
pub fn find(name: &[u8]) -> Option<&'static Verb> {
    VERBS.iter().find(|verb| verb.name.as_bytes() == name)
}

/// The verbs served at `level` now (`hello.verbs`): known at the level, served, not reserved.
#[must_use]
pub fn served_at(level: Level) -> Vec<&'static str> {
    let mut names = Vec::new();
    for verb in &VERBS {
        if verb.served && !verb.reserved && verb.at(level) {
            names.push(verb.name);
        }
    }
    names
}

#[cfg(test)]
mod tests {
    use super::{find, served_at, Shape, VERBS, VERB_COUNT};
    use crate::limits::Level;

    const OBS: u8 = Level::Observe.bit();
    const OPR: u8 = Level::Operator.bit();
    const OBR: u8 = Level::Observer.bit();
    const CTL: u8 = Level::Control.bit();
    const TEL: u8 = OBS | OPR;
    const ALL: u8 = TEL | OBR | CTL;

    fn in_charset(byte: u8) -> bool {
        matches!(byte, b'a'..=b'z' | b'_' | b'.' | b'-')
    }

    #[test]
    fn all_25_rows_are_pinned_independently_of_the_static_array() {
        let expected: [(&str, u8, Shape, bool, bool); 25] = [
            ("hello", ALL, Shape::Json, true, false),
            ("status", TEL | CTL, Shape::Json, true, false),
            ("current", TEL, Shape::Block, true, false),
            ("subscribe", TEL, Shape::Block, true, false),
            ("unsubscribe", TEL, Shape::Json, true, false),
            ("snapshot_at", TEL, Shape::Block, true, false),
            ("series_batch", TEL, Shape::Block, true, false),
            ("events", TEL | CTL, Shape::Json, false, false),
            ("spans", TEL | CTL, Shape::Json, false, false),
            ("procs", TEL, Shape::Json, true, false),
            ("annotate", OBR | CTL, Shape::Json, false, false),
            ("export", OPR, Shape::Block, false, true),
            ("resource-limits", OBR, Shape::Json, false, true),
            ("window-open", OBR, Shape::Json, false, true),
            ("window-close", OBR, Shape::Json, false, true),
            ("version", CTL, Shape::Json, false, true),
            ("host", CTL, Shape::Json, false, true),
            ("session.start", CTL, Shape::Json, false, true),
            ("session.stop", CTL, Shape::Json, false, true),
            ("session.status", CTL, Shape::Json, false, true),
            ("session.watch", CTL, Shape::Json, false, true),
            ("session.list", CTL, Shape::Json, false, true),
            ("session.spans", CTL, Shape::Json, false, true),
            ("gc", CTL, Shape::Json, false, true),
            ("reload", CTL, Shape::Json, false, true),
        ];
        assert_eq!(VERBS.len(), expected.len());
        let rows = VERBS.iter().zip(expected.iter());
        for (verb, &(name, levels, shape, served, reserved)) in rows {
            assert_eq!(verb.name, name);
            assert_eq!(verb.levels, levels, "{name}");
            assert_eq!(verb.shape, shape, "{name}");
            assert_eq!(verb.served, served, "{name}");
            assert_eq!(verb.reserved, reserved, "{name}");
        }
    }

    #[test]
    fn the_table_has_unique_names_in_the_documented_charset() {
        assert_eq!(VERBS.len(), VERB_COUNT);
        for (index, verb) in VERBS.iter().enumerate() {
            let name = verb.name;
            assert!(name.len() <= 64 && !name.is_empty());
            assert!(name.bytes().all(in_charset));
            assert!(VERBS[..index].iter().all(|other| other.name != name));
            assert_ne!(verb.levels, 0);
            assert_eq!(verb.levels & !15, 0);
        }
    }

    #[test]
    fn find_is_exact_over_bytes() {
        assert_eq!(find(b"hello").map(|v| v.name), Some("hello"));
        let watch = find(b"session.watch").map(|v| v.name);
        assert_eq!(watch, Some("session.watch"));
        assert!(find(b"Hello").is_none());
        assert!(find(b"hello ").is_none());
        assert!(find(b"").is_none());
        assert!(find(&[0xFF, 0xFE]).is_none());
    }

    #[test]
    fn the_level_masks_follow_section_4_3() {
        let hello = find(b"hello").expect("hello");
        assert!(Level::ALL.iter().all(|level| hello.at(*level)));
        let status = find(b"status").expect("status");
        assert!(status.at(Level::Observe));
        assert!(status.at(Level::Operator));
        assert!(status.at(Level::Control));
        assert!(!status.at(Level::Observer));
        let annotate = find(b"annotate").expect("annotate");
        assert!(annotate.at(Level::Observer));
        assert!(annotate.at(Level::Control));
        assert!(!annotate.at(Level::Observe));
        assert!(!annotate.at(Level::Operator));
        let export = find(b"export").expect("export");
        assert!(export.at(Level::Operator));
        assert!(!export.at(Level::Observe));
        let procs = find(b"procs").expect("procs");
        assert!(!procs.at(Level::Control));
        assert!(!procs.at(Level::Observer));
        let gc = find(b"gc").expect("gc");
        assert!(gc.at(Level::Control));
        assert!(!gc.at(Level::Observer));
    }

    #[test]
    fn the_shapes_and_flags_follow_the_table() {
        let blocks = [
            "current",
            "subscribe",
            "snapshot_at",
            "series_batch",
            "export",
        ];
        for verb in &VERBS {
            let expect = if blocks.contains(&verb.name) {
                Shape::Block
            } else {
                Shape::Json
            };
            assert_eq!(verb.shape, expect, "{}", verb.name);
            let served = matches!(
                verb.name,
                "hello"
                    | "status"
                    | "current"
                    | "subscribe"
                    | "unsubscribe"
                    | "snapshot_at"
                    | "series_batch"
                    | "procs"
            );
            assert_eq!(verb.served, served, "{}", verb.name);
            assert!(!(verb.served && verb.reserved));
        }
        let reserved = VERBS.iter().filter(|verb| verb.reserved).count();
        assert_eq!(reserved, 14);
    }

    #[test]
    fn served_at_lists_the_eight_p5b_verbs_where_known() {
        let block = [
            "hello",
            "status",
            "current",
            "subscribe",
            "unsubscribe",
            "snapshot_at",
            "series_batch",
            "procs",
        ];
        assert_eq!(served_at(Level::Observe), block);
        assert_eq!(served_at(Level::Operator), block);
        assert_eq!(served_at(Level::Observer), ["hello"]);
        assert_eq!(served_at(Level::Control), ["hello", "status"]);
    }
}
