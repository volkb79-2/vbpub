//! The checked, canonical selection of WIRE.md section 4.2.
//!
//! A [`Selection`] is the stream key: entity selectors are sorted by their derived
//! [`Ord`] (incarnation, path, prefix, all) and metric names bytewise, with no
//! duplicates. Only [`Selection::new`] constructs one, so every value it returns
//! satisfies the decoded-length, control-character, count and uniqueness rules.

/// Most entity selectors in one selection.
pub const MAX_ENTITIES: usize = 16;

/// Most metric names in one selection.
pub const MAX_METRICS: usize = 64;

/// Longest decoded path or prefix.
pub const MAX_PATH_BYTES: usize = 512;

/// Longest decoded metric name.
pub const MAX_NAME_BYTES: usize = 64;

/// One entity selector.
///
/// The variant declaration order is the canonical order: `Inc` before `Path`,
/// `Path` before `Prefix`, and `Prefix` before `All`.
#[derive(Clone, Debug, Eq, Hash, Ord, PartialEq, PartialOrd)]
pub enum Sel {
    /// Match one incarnation.
    Inc(u64),
    /// Match one path.
    Path(String),
    /// Match the paths with one prefix.
    Prefix(String),
    /// Match every entity.
    All,
}

/// A canonical selection: sorted entity selectors, then sorted metric names.
#[derive(Clone, Debug, Eq, Hash, Ord, PartialEq, PartialOrd)]
pub struct Selection {
    entities: Vec<Sel>,
    metrics: Vec<String>,
}

/// A refused selection, carrying the schema path of the first defect.
#[derive(Clone, Copy, Debug, Eq, PartialEq)]
pub struct SelectionError {
    /// The dotted schema path.
    pub key: &'static str,
}

const ENTITY_KEY: &str = "selection.entities";
const METRIC_KEY: &str = "selection.metrics";
const PATH_KEY: &str = "selection.entities.path";
const PREFIX_KEY: &str = "selection.entities.prefix";

const fn error(key: &'static str) -> SelectionError {
    SelectionError { key }
}

fn is_control(c: char) -> bool {
    u32::from(c) < 0x20 || u32::from(c) == 0x7F
}

fn valid_text(text: &str, max: usize, key: &'static str) -> Result<(), SelectionError> {
    if text.is_empty() || text.len() > max || text.chars().any(is_control) {
        return Err(error(key));
    }
    Ok(())
}

/// Whether `text` matches `^[a-z][a-z0-9_.-]{0,63}$`.
#[must_use]
pub fn is_metric_name(text: &str) -> bool {
    if text.is_empty() || text.len() > MAX_NAME_BYTES {
        return false;
    }
    let Some(first) = text.as_bytes().first() else {
        return false;
    };
    if !first.is_ascii_lowercase() {
        return false;
    }
    text.as_bytes().iter().skip(1).all(|byte| {
        byte.is_ascii_lowercase() || byte.is_ascii_digit() || matches!(byte, b'_' | b'.' | b'-')
    })
}

impl Selection {
    /// Build and canonicalize a selection.
    ///
    /// # Errors
    ///
    /// [`SelectionError`] naming the first breach of the count, decoded-length,
    /// control-character, metric-name or duplicate rules.
    pub fn new(entities: Vec<Sel>, metrics: Vec<String>) -> Result<Selection, SelectionError> {
        if !(1..=MAX_ENTITIES).contains(&entities.len()) {
            return Err(error(ENTITY_KEY));
        }
        for entity in &entities {
            match entity {
                Sel::Inc(_) | Sel::All => {}
                Sel::Path(text) => valid_text(text, MAX_PATH_BYTES, PATH_KEY)?,
                Sel::Prefix(text) => valid_text(text, MAX_PATH_BYTES, PREFIX_KEY)?,
            }
        }
        if !(1..=MAX_METRICS).contains(&metrics.len()) {
            return Err(error(METRIC_KEY));
        }
        for metric in &metrics {
            if !is_metric_name(metric) {
                return Err(error(METRIC_KEY));
            }
        }

        let mut entities = entities;
        entities.sort();
        if entities
            .iter()
            .zip(entities.iter().skip(1))
            .any(|(a, b)| a == b)
        {
            return Err(error(ENTITY_KEY));
        }
        let mut metrics = metrics;
        metrics.sort();
        if metrics
            .iter()
            .zip(metrics.iter().skip(1))
            .any(|(a, b)| a == b)
        {
            return Err(error(METRIC_KEY));
        }
        Ok(Selection { entities, metrics })
    }

    /// The canonical entity selectors.
    #[must_use]
    pub fn entities(&self) -> &[Sel] {
        &self.entities
    }

    /// The canonical metric names.
    #[must_use]
    pub fn metrics(&self) -> &[String] {
        &self.metrics
    }
}

#[cfg(test)]
mod tests {
    use super::{
        is_metric_name, Sel, Selection, SelectionError, MAX_ENTITIES, MAX_METRICS, MAX_NAME_BYTES,
        MAX_PATH_BYTES,
    };
    use crate::args::{selection, ArgError, Obj};

    fn bs(text: &str) -> String {
        text.replace('~', &char::from(0x5c).to_string())
    }

    fn parse(json: &str) -> Result<Selection, ArgError> {
        let text = bs(&format!("{{\"selection\":{json}}}"));
        let object = Obj::parse(text.as_bytes(), "").expect("outer object");
        selection(&object, "selection")
    }

    fn entities(items: &[&str]) -> String {
        format!("{{\"entities\":[{}],\"metrics\":[\"m\"]}}", items.join(","))
    }

    fn metrics(items: &[&str]) -> String {
        format!(
            "{{\"entities\":[{{\"all\":true}}],\"metrics\":[{}]}}",
            items.join(",")
        )
    }

    fn entity_error(items: &[&str]) -> String {
        match parse(&entities(items)) {
            Err(error) => error.key,
            Ok(value) => panic!("accepted {value:?}"),
        }
    }

    fn metric_error(items: &[&str]) -> String {
        match parse(&metrics(items)) {
            Err(error) => error.key,
            Ok(value) => panic!("accepted {value:?}"),
        }
    }

    #[test]
    fn selection_canonical_sorts_variants_then_values() {
        let text = entities(&[
            "{\"all\":true}",
            "{\"prefix\":\"p\"}",
            "{\"path\":\"a\"}",
            "{\"path\":\"B\"}",
            "{\"inc\":10}",
            "{\"inc\":2}",
        ]);
        let selection = parse(&text).expect("selection");
        let found = selection.entities();
        assert_eq!(found.first(), Some(&Sel::Inc(2)));
        assert_eq!(found.get(1), Some(&Sel::Inc(10)));
        assert_eq!(found.get(2), Some(&Sel::Path("B".to_string())));
        assert_eq!(found.get(3), Some(&Sel::Path("a".to_string())));
        assert_eq!(found.get(4), Some(&Sel::Prefix("p".to_string())));
        assert_eq!(found.get(5), Some(&Sel::All));
        let text = metrics(&["\"z\"", "\"a-b.c_d\"", "\"b\""]);
        let selection = parse(&text).expect("selection");
        assert_eq!(
            selection.metrics(),
            ["a-b.c_d".to_string(), "b".to_string(), "z".to_string()]
        );
    }

    #[test]
    fn selection_decoded_duplicates_are_refused() {
        assert_eq!(
            entity_error(&["{\"path\":\"a\"}", "{\"path\":\"~u0061\"}"]),
            "selection.entities"
        );
        assert_eq!(
            entity_error(&["{\"inc\":5}", "{\"inc\":5}"]),
            "selection.entities"
        );
        assert_eq!(metric_error(&["\"a\"", "\"~u0061\""]), "selection.metrics");
    }

    #[test]
    fn selection_inc_and_path_are_distinct() {
        let entity = Selection::new(
            vec![Sel::Inc(5), Sel::Path("inc-5".to_string())],
            vec!["m".to_string()],
        )
        .expect("distinct selectors");
        assert_eq!(entity.entities().len(), 2);
        assert_ne!(
            Selection::new(vec![Sel::Inc(5)], vec!["m".to_string()]),
            Selection::new(vec![Sel::Path("5".to_string())], vec!["m".to_string()])
        );
    }

    #[test]
    fn selection_counts_0_1_16_17_entities_and_0_1_64_65_metrics() {
        let inc = |count: usize| {
            (0..count)
                .map(|value| format!("{{\"inc\":{value}}}"))
                .collect::<Vec<_>>()
        };
        for (count, ok) in [(0, false), (1, true), (16, true), (17, false)] {
            let items = inc(count);
            let refs: Vec<&str> = items.iter().map(String::as_str).collect();
            assert_eq!(parse(&entities(&refs)).is_ok(), ok, "{count} entities");
        }
        let valid_names: Vec<String> = (0..65).map(|value| format!("\"m{value}\"")).collect();
        for (count, ok) in [(0, false), (1, true), (64, true), (65, false)] {
            let refs: Vec<&str> = valid_names.iter().take(count).map(String::as_str).collect();
            assert_eq!(parse(&metrics(&refs)).is_ok(), ok, "{count} metrics");
        }
    }

    #[test]
    fn selection_path_512_513_decoded_and_escaped() {
        let accepted = "a".repeat(MAX_PATH_BYTES);
        let refused = "a".repeat(MAX_PATH_BYTES + 1);
        let escaped = "~u0061".repeat(MAX_PATH_BYTES);
        let escaped_over = "~u0061".repeat(MAX_PATH_BYTES + 1);
        for key in ["path", "prefix"] {
            let yes = format!("{{\"{key}\":\"{accepted}\"}}");
            let no = format!("{{\"{key}\":\"{refused}\"}}");
            assert!(parse(&entities(&[&yes])).is_ok(), "{key} 512");
            assert_eq!(entity_error(&[&no]), format!("selection.entities.{key}"));
            let yes = bs(&format!("{{\"{key}\":\"{escaped}\"}}"));
            let no = bs(&format!("{{\"{key}\":\"{escaped_over}\"}}"));
            assert!(parse(&entities(&[&yes])).is_ok(), "{key} escaped 512");
            assert_eq!(entity_error(&[&no]), format!("selection.entities.{key}"));
        }
    }

    #[test]
    fn selection_empty_path_refused() {
        for key in ["path", "prefix"] {
            let text = bs(&format!("{{\"{key}\":\"\"}}"));
            assert_eq!(entity_error(&[&text]), format!("selection.entities.{key}"));
            assert_eq!(
                Selection::new(vec![Sel::Path(String::new())], vec!["m".to_string()]),
                Err(SelectionError {
                    key: "selection.entities.path"
                })
            );
        }
    }

    #[test]
    fn selection_control_and_del_in_path_refused() {
        for escaped in ["~u0000", "~u001f", "~u007f", "~n"] {
            let path = bs(&format!("{{\"path\":\"{escaped}\"}}"));
            assert_eq!(entity_error(&[&path]), "selection.entities.path");
        }
        for control in ['\u{0}', '\u{1f}', '\u{7f}'] {
            let text = format!("path{control}tail");
            assert_eq!(
                Selection::new(vec![Sel::Prefix(text)], vec!["m".to_string()]),
                Err(SelectionError {
                    key: "selection.entities.prefix"
                })
            );
        }
    }

    #[test]
    fn selection_lone_and_misordered_surrogates_refused() {
        for escaped in [
            "~uD800",
            "~uDC00",
            "~uD800~u0041",
            "~uDC00~uD800",
            "~uD800~uDBFF",
        ] {
            let text = bs(&format!("{{\"path\":\"{escaped}\"}}"));
            assert_eq!(entity_error(&[&text]), "selection.entities.path");
        }
        let valid = bs("{\"path\":\"~uD83D~uDE00\"}");
        assert!(parse(&entities(&[&valid])).is_ok());
    }

    #[test]
    fn selection_all_must_be_true() {
        for value in ["false", "1", "\"true\"", "null"] {
            let entity = format!("{{\"all\":{value}}}");
            assert_eq!(entity_error(&[&entity]), "selection.entities.all");
        }
        assert!(parse(&entities(&["{\"all\":true}"])).is_ok());
    }

    #[test]
    fn selection_element_with_two_members_refused() {
        assert_eq!(
            entity_error(&["{\"inc\":1,\"path\":\"a\"}", "{\"inc\":2}"]),
            "selection.entities"
        );
    }

    #[test]
    fn selection_unknown_selector_key_echo_bounded() {
        let long = "k".repeat(30_000);
        let entity = format!("{{\"{long}\":1}}");
        let error = entity_error(&[&entity]);
        assert_eq!(error, "selection.entities");
        assert!(error.len() <= 2048);
        let short = "k".repeat(64);
        let entity = format!("{{\"{short}\":1}}");
        let error = entity_error(&[&entity]);
        assert_eq!(error, format!("selection.entities.{short}"));
    }

    #[test]
    fn selection_metric_name_grammar() {
        for name in ["a", "a-b.c_d", "m0", "~u0061bc"] {
            let item = if name.starts_with('~') {
                bs(&format!("\"{name}\""))
            } else {
                format!("\"{name}\"")
            };
            assert!(parse(&metrics(&[&item])).is_ok(), "{name}");
        }
        for name in ["A", "1a", &"a".repeat(MAX_NAME_BYTES + 1)] {
            let item = format!("\"{name}\"");
            assert_eq!(metric_error(&[&item]), "selection.metrics");
        }
        assert!(is_metric_name(&"a".repeat(MAX_NAME_BYTES)));
        assert!(!is_metric_name(&"a".repeat(MAX_NAME_BYTES + 1)));
        for item in ["true", "null", "false", "1e5", "12", "[]", "{}"] {
            assert_eq!(metric_error(&[item]), "selection.metrics", "{item}");
        }
    }

    #[test]
    fn selection_entity_elements_of_every_non_object_kind() {
        for item in ["\"x\"", "5", "true", "null", "[]"] {
            assert_eq!(entity_error(&[item]), "selection.entities", "{item}");
        }
    }

    #[test]
    fn selection_new_rechecks_every_rule() {
        let m = || vec!["m".to_string()];
        let cases = [
            Selection::new(Vec::new(), m()),
            Selection::new(vec![Sel::All; MAX_ENTITIES + 1], m()),
            Selection::new(vec![Sel::Path(String::new())], m()),
            Selection::new(vec![Sel::Path("a".repeat(MAX_PATH_BYTES + 1))], m()),
            Selection::new(vec![Sel::Path("a\u{1}".to_string())], m()),
            Selection::new(vec![Sel::Inc(1), Sel::Inc(1)], m()),
            Selection::new(vec![Sel::Inc(1)], Vec::new()),
            Selection::new(vec![Sel::Inc(1)], vec!["A".to_string()]),
            Selection::new(vec![Sel::Inc(1)], vec!["a".to_string(), "a".to_string()]),
            Selection::new(vec![Sel::Inc(1)], vec!["a".repeat(MAX_NAME_BYTES + 1)]),
            Selection::new(
                vec![Sel::Inc(1)],
                (0..=MAX_METRICS).map(|value| format!("m{value}")).collect(),
            ),
        ];
        assert!(cases.iter().all(Result::is_err));
    }
}
