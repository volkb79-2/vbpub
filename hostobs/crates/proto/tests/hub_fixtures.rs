//! The send-hub golden test (WIRE.md §11.4 hub cases, §5.3).
//!
//! Every `fixtures/stream/hub-*.json` is replayed against `SendHub` with a fake clock (`t_ms`
//! becomes `now_ns`): each step's outputs (outcome, pools of the watched connections, uids and
//! the global pool, the pinned head, END counts, `query_room`, `head_partial`, the connections
//! `poll` closes) must equal the fixture's hand-derived expectation exactly. The fixtures are
//! written by `python/reference/make_hub_fixtures.py` from the rules of §5.3, not by running a
//! hub. Every block is a 2-section BLOCK of the stated length: `corr` at 8, `dropped` at 104,
//! the tail from 112 filled with the step's `tag` (default 160).

// The fixture parser is shared with `golden_fixtures.rs` as source (see that file).
#[path = "../../../test-support/json.rs"]
mod json;

use std::cell::Cell;
use std::collections::{BTreeMap, HashMap};
use std::path::PathBuf;
use std::rc::Rc;
use std::sync::Arc;

use hostobs_proto::hub::{ConnId, LiveOutcome, QueryOutcome, QueueLimits, SendHub, TickForms};
use json::Json;

const STREAM_DIR: &str = concat!(env!("CARGO_MANIFEST_DIR"), "/../../fixtures/stream");
const NS_PER_MS: u64 = 1_000_000;
const HUB_CASES: usize = 20;
const DEFAULT_TAG: u8 = 160;
const PREFIX_LEN: usize = 112;

/// A BLOCK frame of `len` bytes (at least 112): two sections, sentinels in `corr` and
/// `dropped`, the tail filled with `tag`.
fn block(len: usize, tag: u8) -> Arc<[u8]> {
    let mut out = vec![tag; len];
    out[..PREFIX_LEN].fill(0);
    out[8..12].fill(0xEE);
    out[12..14].copy_from_slice(&2u16.to_le_bytes());
    out[104..108].fill(0xEE);
    Arc::from(out)
}

struct Tick {
    generation: u32,
    bare: Option<Arc<[u8]>>,
    dict: Arc<[u8]>,
    dict_calls: Cell<u32>,
}

impl TickForms for Tick {
    fn generation(&self) -> u32 {
        self.generation
    }

    fn bare(&self) -> Option<Arc<[u8]>> {
        self.bare.clone()
    }

    fn dict_form(&self) -> Arc<[u8]> {
        self.dict_calls.set(self.dict_calls.get() + 1);
        Arc::clone(&self.dict)
    }
}

fn number(value: &Json, what: &str) -> usize {
    let Some(raw) = value.as_u64() else {
        panic!("{what} is a number");
    };
    let Ok(size) = usize::try_from(raw) else {
        panic!("{what} fits usize");
    };
    size
}

fn small(value: &Json, what: &str) -> u32 {
    let Ok(small) = u32::try_from(number(value, what)) else {
        panic!("{what} fits u32");
    };
    small
}

fn join_numbers(items: &[Json], what: &str, sep: &str) -> String {
    let mut parts = Vec::new();
    for item in items {
        parts.push(number(item, what).to_string());
    }
    parts.join(sep)
}

fn tag_of(step: &Json) -> u8 {
    match step.get("tag") {
        Some(tag) => u8::try_from(number(tag, "tag")).expect("tag fits u8"),
        None => DEFAULT_TAG,
    }
}

fn make_tick(step: &Json) -> Tick {
    let tag = tag_of(step);
    let bare = match step.field("bare") {
        Json::Null => None,
        other => Some(block(number(other, "bare"), tag)),
    };
    Tick {
        generation: small(step.field("gen"), "gen"),
        bare,
        dict: block(number(step.field("dict"), "dict"), tag),
        dict_calls: Cell::new(0),
    }
}

fn limits_of(spec: &Json) -> QueueLimits {
    let ns = |key: &str| u64::try_from(number(spec.field(key), key)).expect("ms") * NS_PER_MS;
    let bytes = |key: &str| number(spec.field(key), key);
    QueueLimits {
        client_queue_bytes: bytes("client_queue_bytes"),
        uid_live_bytes: bytes("uid_live_bytes"),
        global_live_bytes: bytes("global_live_bytes"),
        query_queue_bytes: bytes("query_queue_bytes"),
        uid_query_bytes: bytes("uid_query_bytes"),
        global_query_bytes: bytes("global_query_bytes"),
        control_queue_bytes: bytes("control_queue_bytes"),
        stall_ns: ns("stall_ms"),
        frame_completion_ns: ns("frame_completion_ms"),
    }
}

fn flag(value: &Json) -> bool {
    match value {
        Json::Bool(on) => *on,
        other => panic!("expected a bool, got {other:?}"),
    }
}

fn word(bytes: &[u8], at: usize) -> u32 {
    let raw = bytes.get(at..at + 4).expect("four bytes");
    u32::from_le_bytes(raw.try_into().expect("four bytes"))
}

fn live_text(outcome: LiveOutcome) -> &'static str {
    match outcome {
        LiveOutcome::Queued => "queued",
        LiveOutcome::Coalesced => "coalesced",
        LiveOutcome::DroppedIncoming => "dropped-incoming",
    }
}

fn head_text(hub: &mut SendHub, id: ConnId, now: u64, is_block: bool) -> String {
    let Some([prefix, tail]) = hub.head(id, now) else {
        return "head:none".to_string();
    };
    let len = prefix.len() + tail.len();
    if !is_block {
        return format!("head:len={len}");
    }
    let tag = tail.first().map_or(-1, |byte| i64::from(*byte));
    let (corr, dropped) = (word(prefix, 8), word(prefix, 104));
    format!("head:len={len},corr={corr},dropped={dropped},tail_tag={tag}")
}

/// The replay state of one case.
struct Replay {
    hub: SendHub,
    ids: Vec<ConnId>,
    uids: Vec<u32>,
    shared: HashMap<String, Rc<Tick>>,
}

impl Replay {
    fn conn(&self, step: &Json) -> ConnId {
        self.ids[number(step.field("c"), "c")]
    }

    fn pools_text(&self, watch: &[Json]) -> String {
        let mut rows: BTreeMap<String, String> = BTreeMap::new();
        for key in watch {
            let key = key.as_str().expect("watch key");
            let pools = if let Some(index) = key.strip_prefix('c') {
                self.hub
                    .queued(self.ids[index.parse::<usize>().expect("conn index")])
            } else if let Some(uid) = key.strip_prefix('u') {
                self.hub.uid_pools(uid.parse::<u32>().expect("uid"))
            } else {
                assert_eq!(key, "g", "watch key");
                self.hub.global_pools()
            };
            let text = format!("{}/{}/{}", pools.live, pools.query, pools.control);
            rows.insert(key.to_string(), text);
        }
        pools_line(&rows)
    }

    fn push_live(&mut self, step: &Json, now: u64) -> String {
        let id = self.conn(step);
        let stream = small(step.field("s"), "s");
        let tick: Rc<Tick> = match step.get("share") {
            Some(key) => {
                let key = key.as_str().expect("share key").to_string();
                let slot = self.shared.entry(key);
                Rc::clone(slot.or_insert_with(|| Rc::new(make_tick(step))))
            }
            None => Rc::new(make_tick(step)),
        };
        let before = tick.dict_calls.get();
        let outcome = self.hub.push_live(id, stream, &*tick, now);
        let calls = tick.dict_calls.get() - before;
        assert!(calls <= 1, "dict_form is called at most once per push");
        live_text(outcome).to_string()
    }

    fn run(&mut self, step: &Json) -> Vec<String> {
        let now = u64::try_from(number(step.field("t_ms"), "t_ms")).expect("t_ms") * NS_PER_MS;
        let op = step.field("op").as_str().expect("op");
        let mut out = Vec::new();
        match op {
            "push_live" => out.push(self.push_live(step, now)),
            "push_query" => {
                let id = self.conn(step);
                let stream = small(step.field("s"), "s");
                let frame = block(number(step.field("len"), "len"), DEFAULT_TAG);
                let outcome = self.hub.push_query(id, stream, frame, now);
                out.push(match outcome {
                    QueryOutcome::Queued => "queued".to_string(),
                    QueryOutcome::Backpressure(_) => "backpressure".to_string(),
                });
            }
            "push_control" => {
                let id = self.conn(step);
                let len = number(step.field("len"), "len");
                let frame: Arc<[u8]> = Arc::from(vec![DEFAULT_TAG; len]);
                let result = self.hub.push_control(id, frame, now);
                let text = if result.is_ok() { "ok" } else { "close" };
                out.push(text.to_string());
            }
            "query_room" => {
                let room = self
                    .hub
                    .query_room(self.conn(step), number(step.field("len"), "len"));
                out.push(format!("room:{room}"));
            }
            "end_stream" => {
                let counts = self
                    .hub
                    .end_stream(self.conn(step), small(step.field("s"), "s"));
                out.push(format!("counts:{}/{}", counts.blocks, counts.dropped));
            }
            "head" => {
                let id = self.conn(step);
                let is_block = flag(step.field("block"));
                out.push(head_text(&mut self.hub, id, now, is_block));
            }
            "head_partial" => {
                out.push(format!(
                    "partial:{}",
                    self.hub.head_partial(self.conn(step))
                ));
            }
            "wrote" => self
                .hub
                .wrote(self.conn(step), number(step.field("n"), "n"), now),
            "poll" => {
                let closed = self.hub.poll(now);
                let list: Vec<String> = closed
                    .iter()
                    .map(|(id, _)| self.ids.iter().position(|known| known == id).expect("id"))
                    .map(|index| index.to_string())
                    .collect();
                out.push(format!("closed:[{}]", list.join(",")));
            }
            "drain_unpinned" => self.hub.drain_unpinned(self.conn(step)),
            "remove" => self.hub.remove(self.conn(step)),
            other => panic!("unknown op {other}"),
        }
        if let Some(watch) = step.get("watch") {
            let keys = watch.as_array().expect("watch list");
            out.push(self.pools_text(keys));
        }
        out
    }
}

fn pools_line(rows: &BTreeMap<String, String>) -> String {
    let mut parts = Vec::new();
    for (key, text) in rows {
        parts.push(format!("{key}={text}"));
    }
    format!("pools:{}", parts.join(";"))
}

fn render_expected(output: &Json) -> String {
    match output {
        Json::Null => "head:none".to_string(),
        Json::String(text) => text.clone(),
        object => render_object(object),
    }
}

fn render_object(object: &Json) -> String {
    if let Some(Json::Object(map)) = object.get("pools") {
        let mut rows: BTreeMap<String, String> = BTreeMap::new();
        for (key, value) in map {
            let items = value.as_array().expect("pool triple");
            rows.insert(key.clone(), join_numbers(items, "pool", "/"));
        }
        return pools_line(&rows);
    }
    if let Some(head) = object.get("head") {
        let len = number(head.field("len"), "len");
        if head.get("corr").is_none() {
            return format!("head:len={len}");
        }
        let corr = number(head.field("corr"), "corr");
        let dropped = number(head.field("dropped"), "dropped");
        let tag = number(head.field("tail_tag"), "tail_tag");
        return format!("head:len={len},corr={corr},dropped={dropped},tail_tag={tag}");
    }
    if let Some(counts) = object.get("counts") {
        let blocks = number(counts.field("blocks"), "blocks");
        return format!(
            "counts:{blocks}/{}",
            number(counts.field("dropped"), "dropped")
        );
    }
    if let Some(closed) = object.get("closed") {
        let items = closed.as_array().expect("closed list");
        return format!("closed:[{}]", join_numbers(items, "conn", ","));
    }
    if let Some(room) = object.get("room") {
        return format!("room:{}", flag(room));
    }
    if let Some(partial) = object.get("partial") {
        return format!("partial:{}", flag(partial));
    }
    panic!("unknown output {object:?}")
}

fn replay(name: &str, spec: &Json) {
    let mut hub = SendHub::new(limits_of(spec.field("limits")));
    let mut ids = Vec::new();
    let mut uids = Vec::new();
    for uid in spec.field("conns").as_array().expect("conns") {
        let uid = small(uid, "uid");
        ids.push(hub.add(uid, 0));
        uids.push(uid);
    }
    let mut state = Replay {
        hub,
        ids,
        uids,
        shared: HashMap::new(),
    };
    assert!(!state.uids.is_empty(), "{name}: at least one connection");
    let steps = spec.field("steps").as_array().expect("steps");
    let expect = spec.field("expect").as_array().expect("expect");
    assert_eq!(
        steps.len(),
        expect.len(),
        "{name}: one expectation per step"
    );
    for (index, (step, want)) in steps.iter().zip(expect).enumerate() {
        let want: Vec<String> = want
            .as_array()
            .expect("outputs")
            .iter()
            .map(render_expected)
            .collect();
        let got = state.run(step);
        assert_eq!(got, want, "{name}: step {index} ({:?})", step.field("op"));
    }
}

fn hub_fixture_paths() -> Vec<PathBuf> {
    let mut paths: Vec<PathBuf> = std::fs::read_dir(STREAM_DIR)
        .unwrap_or_else(|error| panic!("read {STREAM_DIR}: {error}"))
        .filter_map(|entry| entry.ok())
        .map(|entry| entry.path())
        .filter(|path| {
            let name = path.file_name().map(|n| n.to_string_lossy().into_owned());
            name.is_some_and(|n| n.starts_with("hub-") && n.ends_with(".json"))
        })
        .collect();
    paths.sort();
    paths
}

#[test]
fn every_hub_fixture_replays_against_the_send_hub() {
    let paths = hub_fixture_paths();
    assert_eq!(
        paths.len(),
        HUB_CASES,
        "the hub fixture set is exactly {HUB_CASES} cases"
    );
    for path in paths {
        let label = path.display().to_string();
        let bytes = std::fs::read(&path).unwrap_or_else(|error| panic!("read {label}: {error}"));
        let spec = json::parse(&bytes).unwrap_or_else(|error| panic!("parse {label}: {error:?}"));
        let name = spec.field("case").as_str().expect("case name");
        let stem = path.file_stem().map(|s| s.to_string_lossy().into_owned());
        assert_eq!(stem.as_deref(), Some(name), "file name equals case name");
        replay(name, &spec);
    }
}
