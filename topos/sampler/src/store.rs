//! Tiered store: raw/rollup frames appended to segment files. tmpfs tiers are plain, the
//! disk tier is a sequence of independent zstd frames.
//!
//! Every frame on disk is `magic "TSF1" | u32 len | u32 crc32 | u64 ts_ms | payload`; the
//! crc covers `ts_ms` and the payload (S-01). A reader stops at the first bad frame of a
//! segment, counts it and reports the gap; the writer rolls a failed write back, and a
//! restart truncates a torn tail to the last good frame (or starts a new segment).
//!
//! Frame body: u64 ts_ms | u32 dt_ms | varint n_ents | per entity: varint id, varint n,
//! n x value. A value is varint (metric<<2 | kind): kind 0 zigzag-varint int, kind 1
//! 8-byte f64, kind 2 one state-code byte (see `util::stc`). Counter deltas of zero are
//! stored (a zero is a measurement); a metric absent from a frame was not measured.
//! Rollup companions of gauge `m` (max, min, p95) are stored under `dict::rollup_ids(m)`.
use crate::cfg::{Config, Tier};
use crate::collect::Val;
use crate::dict::{cover_id, rollup_ids, Dict};
use crate::fsafe::{self, Dir, Kind, O_APPEND, O_CREAT, O_EXCL, O_RDONLY, O_WRONLY};
use crate::util::*;
use std::collections::{HashMap, VecDeque};
use std::fs::File;
use std::io::{BufRead, BufReader, Read, Write};
use std::os::unix::fs::FileExt;
use std::sync::atomic::{AtomicU64, Ordering};
use std::sync::{Arc, Mutex};

pub const MAGIC: [u8; 4] = *b"TSF1";
pub const HDR: usize = 20;
/// Largest stored payload a reader accepts.
const MAX_STORED: u32 = 64 << 20;
/// Largest decompressed frame a reader accepts: one frame is the unit of memory of a query.
pub const MAX_DECODED: usize = 24 << 20;

// ---------------------------------------------------------------- counters shared with status

#[derive(Default)]
pub struct Counters {
    /// Torn or corrupt frames found (and cut off) when opening the store.
    pub torn_open: AtomicU64,
    /// Corrupt frames hit by readers (each request that crosses one counts it again).
    pub corrupt_reads: AtomicU64,
    /// Failed writes (frame, dictionary, event); each one lost data and is visible here.
    pub write_errors: AtomicU64,
    /// Jobs whose frames were not written because the dictionary could not be persisted.
    pub frames_skipped: AtomicU64,
}

pub static CTR: Counters = Counters {
    torn_open: AtomicU64::new(0),
    corrupt_reads: AtomicU64::new(0),
    write_errors: AtomicU64::new(0),
    frames_skipped: AtomicU64::new(0),
};
static LAST_ERR: Mutex<String> = Mutex::new(String::new());
/// What start-up found that the operator should know (tmpfs check, foreign files); shown in `status`.
static NOTES: Mutex<Vec<String>> = Mutex::new(Vec::new());
/// Result of the tmpfs check: 0 not checked, 1 tmpfs, 2 not tmpfs.
static TMPFS_STATE: std::sync::atomic::AtomicU8 = std::sync::atomic::AtomicU8::new(0);

pub fn startup_notes() -> Vec<String> {
    NOTES.lock().unwrap_or_else(|e| e.into_inner()).clone()
}

/// `None` when the store is disabled or the check did not run.
pub fn tmpfs_state() -> Option<bool> {
    match TMPFS_STATE.load(Ordering::Relaxed) {
        1 => Some(true),
        2 => Some(false),
        _ => None,
    }
}

fn add_note(msg: String) {
    let mut g = NOTES.lock().unwrap_or_else(|e| e.into_inner());
    if g.len() < 16 {
        g.push(msg);
    }
}

pub fn note_error(msg: String) {
    CTR.write_errors.fetch_add(1, Ordering::Relaxed);
    *LAST_ERR.lock().unwrap_or_else(|e| e.into_inner()) = msg;
}

pub fn last_error() -> String {
    LAST_ERR.lock().unwrap_or_else(|e| e.into_inner()).clone()
}

// ---------------------------------------------------------------- varints and values

#[derive(Clone, Copy, Debug, PartialEq)]
pub enum Sv {
    V(f64),
    S(u8),
}

fn put_varint(o: &mut Vec<u8>, mut v: u64) {
    while v >= 0x80 {
        o.push((v as u8) | 0x80);
        v >>= 7;
    }
    o.push(v as u8);
}
fn get_varint(b: &[u8], p: &mut usize) -> Option<u64> {
    let mut v = 0u64;
    let mut sh = 0;
    loop {
        let c = *b.get(*p)?;
        *p += 1;
        v |= ((c & 0x7f) as u64) << sh;
        if c < 0x80 {
            return Some(v);
        }
        sh += 7;
        if sh > 63 {
            return None;
        }
    }
}
fn zz(i: i64) -> u64 {
    ((i << 1) ^ (i >> 63)) as u64
}
fn unzz(u: u64) -> i64 {
    ((u >> 1) as i64) ^ -((u & 1) as i64)
}
fn put_val(o: &mut Vec<u8>, m: u32, v: Sv) {
    match v {
        Sv::S(c) => {
            put_varint(o, ((m as u64) << 2) | 2);
            o.push(c);
        }
        Sv::V(v) if v.fract() == 0.0 && v.abs() < 9.0e15 => {
            put_varint(o, (m as u64) << 2);
            put_varint(o, zz(v as i64));
        }
        Sv::V(v) => {
            put_varint(o, ((m as u64) << 2) | 1);
            o.extend_from_slice(&v.to_bits().to_le_bytes());
        }
    }
}
fn get_val(b: &[u8], p: &mut usize) -> Option<(u32, Sv)> {
    let h = get_varint(b, p)?;
    let m = (h >> 2) as u32;
    match h & 3 {
        0 => Some((m, Sv::V(unzz(get_varint(b, p)?) as f64))),
        1 => {
            let s = b.get(*p..*p + 8)?;
            *p += 8;
            Some((m, Sv::V(f64::from_bits(u64::from_le_bytes(s.try_into().ok()?)))))
        }
        2 => {
            let c = *b.get(*p)?;
            *p += 1;
            Some((m, Sv::S(c)))
        }
        _ => None,
    }
}
fn head(o: &mut Vec<u8>, ts: u64, dt: u32, nents: usize) {
    o.extend_from_slice(&ts.to_le_bytes());
    o.extend_from_slice(&dt.to_le_bytes());
    put_varint(o, nents as u64);
}

// ---------------------------------------------------------------- frames

pub fn encode_frame(ts_ms: u64, payload: &[u8]) -> Vec<u8> {
    let tsb = ts_ms.to_le_bytes();
    let crc = crc32_parts(&[&tsb, payload]);
    let mut f = Vec::with_capacity(HDR + payload.len());
    f.extend_from_slice(&MAGIC);
    f.extend_from_slice(&(payload.len() as u32).to_le_bytes());
    f.extend_from_slice(&crc.to_le_bytes());
    f.extend_from_slice(&tsb);
    f.extend_from_slice(payload);
    f
}

fn read_full<R: Read>(r: &mut R, b: &mut [u8]) -> usize {
    let mut n = 0;
    while n < b.len() {
        match r.read(&mut b[n..]) {
            Ok(0) => break,
            Ok(k) => n += k,
            Err(e) if e.kind() == std::io::ErrorKind::Interrupted => {}
            Err(_) => break,
        }
    }
    n
}

/// Sequential frame reader. Stops at the first bad frame; a short tail is `partial` (a write in
/// flight, or a torn one), a bad magic/length/crc is counted in `bad`.
pub struct Frames<R: Read> {
    r: R,
    len: u64,
    pub buf: Vec<u8>,
    pub ts: u64,
    /// Offset just after the last good frame.
    pub good_end: u64,
    pub bad: u64,
    pub partial: bool,
    done: bool,
}

impl<R: Read> Frames<R> {
    pub fn new(r: R, len: u64) -> Self {
        Frames { r, len, buf: Vec::new(), ts: 0, good_end: 0, bad: 0, partial: false, done: false }
    }

    /// Next good frame: payload in `buf`, timestamp in `ts`.
    pub fn next_frame(&mut self) -> bool {
        if self.done {
            return false;
        }
        let mut h = [0u8; HDR];
        let n = read_full(&mut self.r, &mut h);
        if n == 0 {
            self.done = true;
            return false;
        }
        if n < HDR {
            self.partial = true;
            self.done = true;
            return false;
        }
        let plen = u32::from_le_bytes([h[4], h[5], h[6], h[7]]);
        if h[0..4] != MAGIC || plen > MAX_STORED {
            self.bad += 1;
            self.done = true;
            return false;
        }
        if plen as u64 > self.len.saturating_sub(self.good_end + HDR as u64) {
            self.partial = true;
            self.done = true;
            return false;
        }
        self.buf.clear();
        self.buf.resize(plen as usize, 0);
        if read_full(&mut self.r, &mut self.buf) < plen as usize {
            self.partial = true;
            self.done = true;
            return false;
        }
        let crc = u32::from_le_bytes([h[8], h[9], h[10], h[11]]);
        if crc32_parts(&[&h[12..20], &self.buf]) != crc {
            self.bad += 1;
            self.done = true;
            return false;
        }
        self.ts = u64::from_le_bytes(h[12..20].try_into().unwrap());
        self.good_end += HDR as u64 + plen as u64;
        true
    }
}

/// Offset after the last good frame of a segment file, and the file length.
fn scan_good_end(dir: &Dir, name: &str) -> std::io::Result<(u64, u64)> {
    let f = dir.open_file(name, O_RDONLY, 0)?;
    let len = f.metadata()?.len();
    let mut fr = Frames::new(BufReader::with_capacity(64 * 1024, f), len);
    while fr.next_frame() {}
    Ok((fr.good_end, len))
}

fn inflate(src: &[u8], out: &mut Vec<u8>) -> bool {
    out.clear();
    match ruzstd::decoding::StreamingDecoder::new(src) {
        Ok(d) => d.take(MAX_DECODED as u64 + 1).read_to_end(out).is_ok() && out.len() <= MAX_DECODED,
        Err(_) => false,
    }
}

fn open_append(dir: &Dir, name: &str) -> std::io::Result<File> {
    dir.open_file(name, O_WRONLY | O_APPEND | O_CREAT, 0o600)
}

/// Append dictionary lines and keep the count in the file's fixed-width header line in step
/// (`dict::header`). The lines are durable first, then the count is updated in place: a crash
/// in between leaves a header BEHIND the lines (accepted at load), never ahead of them.
fn append_counted(dir: &Dir, name: &str, bytes: &[u8], total: usize) -> std::io::Result<()> {
    let mut f = open_append(dir, name)?;
    let len0 = f.metadata()?.len();
    let mut out = Vec::with_capacity(bytes.len() + crate::dict::HEADER_LEN);
    if len0 == 0 {
        out.extend_from_slice(crate::dict::header(total).as_bytes());
    }
    out.extend_from_slice(bytes);
    let r = f.write_all(&out).and_then(|_| f.sync_data());
    if let Err(e) = r {
        let _ = f.set_len(len0);
        return Err(e);
    }
    if len0 == 0 {
        dir.sync();
        return Ok(());
    }
    // The header is rewritten through a second descriptor: O_APPEND ignores the offset of a pwrite.
    let h = crate::dict::header(total);
    if let Err(e) = dir.open_file(name, O_WRONLY, 0).and_then(|hf| hf.write_all_at(h.as_bytes(), 0).and_then(|_| hf.sync_data())) {
        note_error(format!("{name}: header count not updated: {e}"));
    }
    Ok(())
}

fn rewrite_synced(dir: &Dir, name: &str, bytes: &[u8]) -> std::io::Result<()> {
    let tmp = format!("{name}.tmp");
    // A leftover (or planted) tmp name is removed itself; it is then created with O_EXCL, so
    // nothing but a fresh file of ours is ever written.
    match dir.unlink(&tmp) {
        Ok(()) => {}
        Err(e) if e.kind() == std::io::ErrorKind::NotFound => {}
        Err(e) => return Err(e),
    }
    {
        let mut f = dir.open_file(&tmp, O_WRONLY | O_CREAT | O_EXCL, 0o600)?;
        f.write_all(bytes)?;
        f.sync_all()?;
    }
    dir.rename(&tmp, name)?;
    dir.sync();
    Ok(())
}

/// A line file whose last line lost its newline (torn append) is cut back to the last
/// complete line, so the next append can not merge with it. Returns false if it could not.
fn heal_lines(dir: &Dir, name: &str) -> bool {
    let data = match dir.read_all(name, 1 << 30) {
        Ok(d) => d,
        Err(e) => return e.kind() == std::io::ErrorKind::NotFound,
    };
    if data.is_empty() || data.last() == Some(&b'\n') {
        return true;
    }
    let keep = data.iter().rposition(|&c| c == b'\n').map(|p| p + 1).unwrap_or(0);
    CTR.torn_open.fetch_add(1, Ordering::Relaxed);
    dir.open_file(name, O_WRONLY, 0).and_then(|f| f.set_len(keep as u64)).is_ok()
}

/// A dictionary written before the count header existed gets one (atomically rewritten).
fn upgrade_dict_header(dir: &Dir, name: &str) {
    let data = match dir.read_all(name, 1 << 30) {
        Ok(d) => d,
        Err(_) => return,
    };
    if data.is_empty() || data.starts_with(crate::dict::HEADER_PREFIX.as_bytes()) {
        return;
    }
    let n = data.iter().filter(|&&c| c == b'\n').count();
    let mut out = crate::dict::header(n).into_bytes();
    out.extend_from_slice(&data);
    if let Err(e) = rewrite_synced(dir, name, &out) {
        note_error(format!("{name}: header not added: {e}"));
    }
}

// ---------------------------------------------------------------- tiers

struct Acc {
    n: u32,
    ns: u32,
    last_st: u8,
    sum: f64,
    min: f64,
    max: f64,
    counter: bool,
    samples: Vec<f64>,
}

struct Cur {
    aligned: u64,
    start: u64,
    file: File,
    len: u64,
}

struct TierState {
    t: Tier,
    dir: Arc<Dir>,
    disk: bool,
    raw: bool,
    per: u64,
    n: u64,
    dt_sum: u64,
    acc: HashMap<(u32, u32), Acc>,
    cur: Option<Cur>,
    segs: VecDeque<(u64, u64)>, // (start_s, bytes), sorted by start
    total: u64,
    last_write_s: u64,
    /// The open segment could not be rolled back after a failed write: start a new file.
    force_new: bool,
}

#[derive(Clone, Debug, Default)]
pub struct TierInfo {
    pub name: String,
    pub medium: String,
    pub segments: usize,
    pub bytes: u64,
    pub oldest_s: u64,
    pub newest_s: u64,
    pub keep_s: u64,
    pub fill: f64,
}

pub struct Store {
    tiers: Vec<TierState>,
    cold: Option<Arc<Dir>>,
    cap: u64,
    w_mets: usize,
    w_ents: usize,
    ev_file: Option<File>,
    ev_dir: Arc<Dir>,
    ev_name: String,
    ev_size: u64,
    ev_rot_size: u64,
    ev_max: u64,
    ev_in_cold: bool,
    dict_ok: bool,
    pub w_tmpfs: u64,
    pub w_cold: u64,
    pub w_other: u64,
    enabled: bool,
}

fn seg_name(name: &str, start: u64) -> String {
    format!("{name}-{start}.seg")
}

/// Directories the sampler writes, validated once at start-up (`Dirs::open`) and then used
/// through their descriptors only (X-01).
pub struct Dirs {
    pub ev: Arc<Dir>,
    pub ev_name: String,
    pub tmpfs: Option<Arc<Dir>>,
    pub cold: Option<Arc<Dir>>,
}

/// Everything a running sampler holds before it touches a file of the store.
pub struct Started {
    pub inst: fsafe::Instance,
    pub dirs: Dirs,
}

/// Start-up in the only safe order (X-02): the instance lock first, so a second instance exits
/// having changed nothing; then the data directories are validated (X-01) and checked (X-03).
pub fn start(cfg: &Config) -> Result<Started, PErr> {
    let inst = fsafe::Instance::acquire(&cfg.sampler.socket_path)?;
    let dirs = Dirs::open(cfg)?;
    Ok(Started { inst, dirs })
}

fn split_file(path: &str) -> Result<(String, String), PErr> {
    let p = std::path::Path::new(path);
    let name = p.file_name().and_then(|n| n.to_str()).ok_or_else(|| perr("path_invalid", format!("{path} has no file name")))?.to_string();
    let dir = match p.parent() {
        Some(x) if !x.as_os_str().is_empty() => x.to_string_lossy().into_owned(),
        _ => ".".into(),
    };
    Ok((dir, name))
}

impl Dirs {
    /// Validate and open every data directory. Nothing in them is read, truncated or deleted
    /// here beyond the symlink scan; the caller holds the instance lock (X-02).
    pub fn open(cfg: &Config) -> Result<Dirs, PErr> {
        let s = &cfg.store;
        let (cold, tmpfs) = if s.enabled {
            let cold = Arc::new(fsafe::open_checked(&s.cold_dir, "store.cold_dir")?);
            let tm = Arc::new(fsafe::open_checked(&s.tmpfs_dir, "store.tmpfs_dir")?);
            let chk = fsafe::check_tmpfs(&tm, s.require_tmpfs)?;
            TMPFS_STATE.store(if chk.is_tmpfs { 1 } else { 2 }, Ordering::Relaxed);
            if !chk.is_tmpfs {
                let msg = format!("WARNING store.tmpfs_dir: {}", chk.detail);
                eprintln!("topos-sampler: {msg}");
                add_note(msg);
            }
            (Some(cold), Some(tm))
        } else {
            (None, None)
        };
        let (ev_dir_path, ev_name) = split_file(&cfg.events.events_path)?;
        let ev = match &cold {
            Some(c) if c.path.trim_end_matches('/') == ev_dir_path.trim_end_matches('/') => c.clone(),
            _ => Arc::new(fsafe::open_checked(&ev_dir_path, "events.events_path directory")?),
        };
        let d = Dirs { ev, ev_name, tmpfs, cold };
        d.refuse_links()?;
        Ok(d)
    }

    /// A symlink (or other non-regular name we would not create) in a data directory is refused
    /// and reported: the directories are ours (0700), so a link there is somebody's planted trap.
    fn refuse_links(&self) -> Result<(), PErr> {
        let mut seen: Vec<&Arc<Dir>> = Vec::new();
        for d in [Some(&self.ev), self.tmpfs.as_ref(), self.cold.as_ref()].into_iter().flatten() {
            if seen.iter().any(|x| x.path == d.path) {
                continue;
            }
            seen.push(d);
            let list = d.list().map_err(|e| perr("dir_io", format!("{}: {e}", d.path)))?;
            for (n, k) in list {
                if k == Kind::Symlink {
                    return Err(perr("symlink_in_data_dir", format!("{}/{n} is a symlink; refusing to start (symlinks are never followed, remove it)", d.path)));
                }
            }
        }
        Ok(())
    }
}

fn parse_seg_name(n: &str) -> Option<(&str, u64)> {
    let r = n.strip_suffix(".seg")?;
    let (name, num) = r.rsplit_once('-')?;
    Some((name, num.parse().ok()?))
}

/// True when the file is a regular file that starts with the sampler's frame magic: only such
/// a file may be deleted as an orphan segment (X-09).
fn is_sampler_segment(dir: &Dir, name: &str) -> bool {
    let mut m = [0u8; 4];
    match dir.open_file(name, O_RDONLY, 0) {
        Ok(f) => f.read_at(&mut m, 0).map(|n| n == 4).unwrap_or(false) && m == MAGIC,
        Err(_) => false,
    }
}

fn open_events(dir: &Dir, name: &str) -> std::io::Result<(File, u64)> {
    let f = dir.open_file(name, fsafe::O_RDWR | O_APPEND | O_CREAT, 0o600)?;
    let len = f.metadata()?.len();
    if len > 0 {
        let mut last = [0u8; 1];
        if f.read_at(&mut last, len - 1).map(|n| n == 1).unwrap_or(false) && last[0] != b'\n' {
            // a torn line: end it, so the next event can not merge with it
            (&f).write_all(b"\n")?;
            return Ok((f, len + 1));
        }
    }
    Ok((f, len))
}

impl Store {
    /// Open with freshly validated directories (tests). The daemon validates first, under
    /// the instance lock, and calls `open_with`.
    #[cfg(test)]
    pub fn open(cfg: &Config, loaded_mets: usize, loaded_ents: usize) -> Result<Store, PErr> {
        let dirs = Dirs::open(cfg)?;
        Store::open_with(cfg, &dirs, loaded_mets, loaded_ents)
    }

    pub fn open_with(cfg: &Config, dirs: &Dirs, loaded_mets: usize, loaded_ents: usize) -> Result<Store, PErr> {
        let s = &cfg.store;
        let io = |what: &str, e: std::io::Error| perr("store_io", format!("{what}: {e}"));
        let (ev_file, ev_size) = open_events(&dirs.ev, &dirs.ev_name).map_err(|e| io(&format!("{}/{}", dirs.ev.path, dirs.ev_name), e))?;
        let ev_rot_size = dirs.ev.len_of(&format!("{}.1", dirs.ev_name)).unwrap_or(0);
        let mut dict_ok = true;
        let mut tiers = Vec::new();
        if let (true, Some(cold), Some(tmpfs)) = (s.enabled, dirs.cold.as_ref(), dirs.tmpfs.as_ref()) {
            for f in ["mets.dict", "ents.dict"] {
                dict_ok &= heal_lines(cold, f);
                upgrade_dict_header(cold, f);
            }
            // Segments of tiers that are not configured (renamed, removed, moved to another
            // medium) would count against nothing and never be deleted: remove them. Only a
            // regular file that starts with the sampler's frame magic is ours to delete (X-09);
            // anything else with a segment-like name is left alone and reported.
            let tier_dir = |t: &Tier| if t.medium == "disk" { cold.clone() } else { tmpfs.clone() };
            let mut scan: Vec<&Arc<Dir>> = vec![tmpfs];
            if tmpfs.path != cold.path {
                scan.push(cold);
            }
            for d in scan {
                for (n, kind) in d.list().unwrap_or_default() {
                    if let Some((name, _)) = parse_seg_name(&n) {
                        if cfg.tier.iter().any(|t| t.name == name && tier_dir(t).path == d.path) {
                            continue;
                        }
                        if kind == Kind::File && is_sampler_segment(d, &n) {
                            let _ = d.unlink(&n);
                        } else {
                            add_note(format!("foreign file {}/{n} looks like a segment but is not the sampler's; left untouched", d.path));
                        }
                    }
                }
            }
            for t in &cfg.tier {
                let disk = t.medium == "disk";
                let dir = tier_dir(t);
                let mut segs: Vec<(u64, u64)> = Vec::new();
                for (n, kind) in dir.list().unwrap_or_default() {
                    if kind != Kind::File {
                        continue;
                    }
                    if let Some((name, st)) = parse_seg_name(&n) {
                        if name == t.name {
                            segs.push((st, dir.entry_len(&n)));
                        }
                    }
                }
                segs.sort();
                let mut force_new = false;
                // A torn tail (crash, ENOSPC, kill during a write) is cut back to the last
                // good frame before anything is appended behind it.
                if let Some(last) = segs.last_mut() {
                    let sname = seg_name(&t.name, last.0);
                    if let Ok((good, len)) = scan_good_end(&dir, &sname) {
                        if good < len {
                            CTR.torn_open.fetch_add(1, Ordering::Relaxed);
                            match dir.open_file(&sname, O_WRONLY, 0).and_then(|f| f.set_len(good)) {
                                Ok(()) => last.1 = good,
                                Err(e) => {
                                    note_error(format!("truncate {}/{sname}: {e}", dir.path));
                                    force_new = true;
                                }
                            }
                        }
                    }
                }
                let total = segs.iter().map(|x| x.1).sum();
                tiers.push(TierState {
                    t: t.clone(),
                    dir,
                    disk,
                    raw: t.step_s * 1000 == cfg.sampler.tick_ms,
                    per: t.step_s * 1000 / cfg.sampler.tick_ms,
                    n: 0,
                    dt_sum: 0,
                    acc: HashMap::new(),
                    cur: None,
                    segs: segs.into(),
                    total,
                    last_write_s: 0,
                    force_new,
                });
            }
        }
        Ok(Store {
            tiers,
            cold: dirs.cold.clone(),
            cap: s.cold_cap_bytes,
            w_mets: loaded_mets,
            w_ents: loaded_ents,
            ev_file: Some(ev_file),
            ev_dir: dirs.ev.clone(),
            ev_name: dirs.ev_name.clone(),
            ev_size,
            ev_rot_size,
            ev_max: cfg.events.max_bytes,
            ev_in_cold: dirs.cold.as_ref().map(|c| std::path::Path::new(&dirs.ev.path).starts_with(&c.path)).unwrap_or(false),
            dict_ok,
            w_tmpfs: 0,
            w_cold: 0,
            w_other: 0,
            enabled: s.enabled,
        })
    }

    /// Test hook: swap the open segment of the first tier for another file (for example
    /// `/dev/full`, which fails every write with ENOSPC).
    #[cfg(test)]
    pub fn break_current_segment_for_test(&mut self, path: &str) {
        for t in &mut self.tiers {
            if let Some(c) = t.cur.as_mut() {
                c.file = std::fs::OpenOptions::new().write(true).open(path).unwrap();
                return;
            }
        }
    }

    pub fn sizes(&self) -> (u64, u64) {
        let mut tm = 0;
        let mut cold = 0;
        for t in &self.tiers {
            if t.disk {
                cold += t.total
            } else {
                tm += t.total
            }
        }
        (tm, cold)
    }

    /// Everything else in the cold directory that counts against the cap (S-11): both
    /// dictionaries and, when it lives there, the events file and its rotated predecessor.
    pub fn other_bytes(&self) -> u64 {
        let mut n = 0;
        if let Some(c) = &self.cold {
            for f in ["mets.dict", "ents.dict", "ents.dict.tmp"] {
                n += c.len_of(f).unwrap_or(0);
            }
        }
        if self.ev_in_cold {
            n += self.ev_size + self.ev_rot_size;
        }
        n
    }

    pub fn tier_info(&self) -> Vec<TierInfo> {
        self.tiers
            .iter()
            .map(|t| {
                let oldest = t.segs.front().map(|s| s.0).unwrap_or(0);
                let newest = t.last_write_s;
                let fill = if t.segs.is_empty() || t.t.keep_s == 0 {
                    0.0
                } else {
                    (newest.saturating_sub(oldest) as f64 / t.t.keep_s as f64).min(1.0)
                };
                TierInfo {
                    name: t.t.name.clone(),
                    medium: t.t.medium.clone(),
                    segments: t.segs.len(),
                    bytes: t.total,
                    oldest_s: oldest,
                    newest_s: newest,
                    keep_s: t.t.keep_s,
                    fill,
                }
            })
            .collect()
    }

    /// Persist dictionary changes: new names are appended and fsynced; a freed or reused
    /// entity slot rewrites the entity file atomically (tmp + fsync + rename + dir fsync).
    /// Returns false when something could not be made durable: the caller must not write
    /// frames that reference ids the dictionary files do not hold (S-09).
    pub fn sync_dict(&mut self, dict: &mut Dict) -> bool {
        if !self.enabled {
            return true;
        }
        if !self.dict_ok {
            note_error("dictionary files could not be repaired".into());
            return false;
        }
        let cold = match &self.cold {
            Some(c) => c.clone(),
            None => return true,
        };
        let mut ok = true;
        if self.w_mets < dict.mets.len() {
            let mut add = String::new();
            for (n, c) in &dict.mets[self.w_mets..] {
                add.push_str(if *c { "C " } else { "G " });
                add.push_str(n);
                add.push('\n');
            }
            match append_counted(&cold, "mets.dict", add.as_bytes(), dict.mets.len()) {
                Ok(()) => {
                    self.w_other += add.len() as u64;
                    self.w_mets = dict.mets.len();
                }
                Err(e) => {
                    note_error(format!("mets.dict: {e}"));
                    ok = false;
                }
            }
        }
        if dict.ents_dirty {
            let mut all = crate::dict::header(dict.ents.len());
            for n in &dict.ents {
                all.push_str(n);
                all.push('\n');
            }
            match rewrite_synced(&cold, "ents.dict", all.as_bytes()) {
                Ok(()) => {
                    self.w_other += all.len() as u64;
                    self.w_ents = dict.ents.len();
                    dict.ents_dirty = false;
                }
                Err(e) => {
                    note_error(format!("ents.dict: {e}"));
                    ok = false;
                }
            }
        } else if self.w_ents < dict.ents.len() {
            let mut add = String::new();
            for n in &dict.ents[self.w_ents..] {
                add.push_str(n);
                add.push('\n');
            }
            match append_counted(&cold, "ents.dict", add.as_bytes(), dict.ents.len()) {
                Ok(()) => {
                    self.w_other += add.len() as u64;
                    self.w_ents = dict.ents.len();
                }
                Err(e) => {
                    note_error(format!("ents.dict: {e}"));
                    ok = false;
                }
            }
        }
        ok
    }

    pub fn append_events(&mut self, evs: &[String]) {
        for e in evs {
            if self.ev_size > self.ev_max {
                self.ev_file = None;
                let _ = self.ev_dir.rename(&self.ev_name, &format!("{}.1", self.ev_name));
                self.ev_rot_size = self.ev_size;
                self.ev_size = 0;
            }
            if self.ev_file.is_none() {
                match open_events(&self.ev_dir, &self.ev_name) {
                    Ok((f, len)) => {
                        self.ev_file = Some(f);
                        self.ev_size = len;
                    }
                    Err(err) => {
                        note_error(format!("events: {err}"));
                        continue;
                    }
                }
            }
            let mut line = String::with_capacity(e.len() + 1);
            line.push_str(e);
            line.push('\n');
            let f = self.ev_file.as_mut().unwrap();
            match f.write_all(line.as_bytes()) {
                Ok(()) => {
                    self.ev_size += line.len() as u64;
                    self.w_other += line.len() as u64;
                }
                Err(err) => {
                    note_error(format!("events: {err}"));
                    if f.set_len(self.ev_size).is_err() {
                        self.ev_file = None;
                    }
                }
            }
        }
    }

    pub fn push(&mut self, frame: &[(u32, Vec<Val>)], ts_ms: u64, dt_ms: u64, dict: &Dict) {
        if !self.enabled {
            return;
        }
        let cap = self.cap;
        let other = if self.tiers.iter().any(|t| t.disk) { self.other_bytes() } else { 0 };
        let mut body = Vec::with_capacity(64 * 1024);
        let sv = |x: &Val| if x.st != stc::VALUE { Sv::S(x.st) } else { Sv::V(x.v) };
        for ti in 0..self.tiers.len() {
            let Store { tiers, w_tmpfs, w_cold, .. } = self;
            let t = &mut tiers[ti];
            if t.raw {
                body.clear();
                head(&mut body, ts_ms, dt_ms as u32, frame.len());
                for (id, vals) in frame {
                    put_varint(&mut body, *id as u64);
                    put_varint(&mut body, vals.len() as u64);
                    for x in vals {
                        put_val(&mut body, x.m, sv(x));
                    }
                }
                t.write(ts_ms, &body, cap, other, w_tmpfs, w_cold);
                continue;
            }
            for (id, vals) in frame {
                for x in vals {
                    let a = t.acc.entry((*id, x.m)).or_insert_with(|| Acc {
                        n: 0,
                        ns: 0,
                        last_st: 0,
                        sum: 0.0,
                        min: f64::MAX,
                        max: f64::MIN,
                        counter: dict.mets.get(x.m as usize).map(|m| m.1).unwrap_or(false),
                        samples: Vec::new(),
                    });
                    if x.st != stc::VALUE {
                        a.ns += 1;
                        a.last_st = x.st;
                        continue;
                    }
                    a.n += 1;
                    a.sum += x.v;
                    if !a.counter {
                        a.min = a.min.min(x.v);
                        a.max = a.max.max(x.v);
                        a.samples.push(x.v);
                    }
                }
            }
            t.n += 1;
            t.dt_sum += dt_ms;
            if t.n < t.per {
                continue;
            }
            // flush the bucket
            let mut keys: Vec<((u32, u32), &mut Acc)> =
                t.acc.iter_mut().filter(|(_, a)| a.n > 0 || a.ns > 0).map(|(k, a)| (*k, a)).collect();
            keys.sort_unstable_by_key(|(k, _)| *k);
            let mut ents: Vec<(u32, Vec<(u32, Sv)>)> = Vec::new();
            for ((e, m), a) in keys {
                if ents.last().map(|l| l.0) != Some(e) {
                    ents.push((e, Vec::new()));
                }
                let out = &mut ents.last_mut().unwrap().1;
                if a.n == 0 {
                    out.push((m, Sv::S(a.last_st)));
                } else if a.counter {
                    out.push((m, Sv::V(a.sum)));
                    out.push((cover_id(m), Sv::V(a.n as f64)));
                } else {
                    out.push((cover_id(m), Sv::V(a.n as f64)));
                    let ids = rollup_ids(m);
                    a.samples.sort_unstable_by(|x, y| x.total_cmp(y));
                    let rank = ((a.samples.len() as f64) * 0.95).ceil() as usize;
                    let p95 = a.samples[rank.saturating_sub(1).min(a.samples.len() - 1)];
                    out.push((m, Sv::V(a.sum / a.n as f64)));
                    out.push((ids[0], Sv::V(a.max)));
                    out.push((ids[1], Sv::V(a.min)));
                    out.push((ids[2], Sv::V(p95)));
                }
            }
            body.clear();
            head(&mut body, ts_ms, t.dt_sum as u32, ents.len());
            for (id, vs) in &ents {
                put_varint(&mut body, *id as u64);
                put_varint(&mut body, vs.len() as u64);
                for &(m, v) in vs {
                    put_val(&mut body, m, v);
                }
            }
            t.acc.retain(|_, a| {
                let keep = a.n > 0 || a.ns > 0;
                a.n = 0;
                a.ns = 0;
                a.sum = 0.0;
                a.min = f64::MAX;
                a.max = f64::MIN;
                a.samples.clear();
                keep
            });
            t.n = 0;
            t.dt_sum = 0;
            t.write(ts_ms, &body, cap, other, w_tmpfs, w_cold);
        }
    }
}

impl TierState {
    fn write(&mut self, ts_ms: u64, body: &[u8], cap: u64, other: u64, w_tmpfs: &mut u64, w_cold: &mut u64) {
        let comp;
        let payload: &[u8] = if self.disk {
            comp = ruzstd::encoding::compress_to_vec(body, ruzstd::encoding::CompressionLevel::Fastest);
            &comp
        } else {
            body
        };
        let frame = encode_frame(ts_ms, payload);
        let now_s = ts_ms / 1000;
        self.last_write_s = now_s;
        let aligned = now_s / self.t.segment_s * self.t.segment_s;
        let mut rolled = false;
        if self.cur.as_ref().map(|c| c.aligned) != Some(aligned) {
            let start = if self.force_new { aligned.max(self.segs.back().map(|s| s.0 + 1).unwrap_or(0)) } else { aligned };
            let path = seg_name(&self.t.name, start);
            match open_append(&self.dir, &path).and_then(|f| f.metadata().map(|m| (f, m.len()))) {
                Ok((file, len)) => {
                    self.force_new = false;
                    self.cur = Some(Cur { aligned, start, file, len });
                    let pos = self.segs.partition_point(|s| s.0 < start);
                    if self.segs.get(pos).map(|s| s.0) != Some(start) {
                        self.segs.insert(pos, (start, len));
                    }
                    rolled = true;
                }
                Err(e) => {
                    note_error(format!("open {}/{path}: {e}", self.dir.path));
                    return;
                }
            }
        }
        let mut failed = false;
        if let Some(c) = self.cur.as_mut() {
            match c.file.write_all(&frame) {
                Ok(()) => {
                    c.len += frame.len() as u64;
                    let start = c.start;
                    if let Some(l) = self.segs.iter_mut().rev().find(|s| s.0 == start) {
                        l.1 += frame.len() as u64;
                    }
                    self.total += frame.len() as u64;
                    if self.disk {
                        *w_cold += frame.len() as u64;
                    } else {
                        *w_tmpfs += frame.len() as u64;
                    }
                }
                Err(e) => {
                    note_error(format!("write {}-{}: {e}", self.t.name, c.start));
                    // Roll the partial frame back, or abandon the segment: nothing may be
                    // appended behind a torn frame.
                    failed = c.file.set_len(c.len).is_err();
                }
            }
        }
        if failed {
            self.cur = None;
            self.force_new = true;
        }
        if rolled || self.disk {
            self.prune(now_s, if self.disk { Some(cap) } else { None }, other);
        }
    }

    /// Remove expired segments and, on the disk tier, the oldest data until the byte cap
    /// (segments plus the other files of the directory) holds. The cap is strict: when only
    /// the open segment is left and it alone exceeds the cap, it is dropped too.
    fn prune(&mut self, now_s: u64, cap: Option<u64>, other: u64) {
        let cur_start = self.cur.as_ref().map(|c| c.start);
        let (dir, name, seg_len, keep_s) = (&self.dir, self.t.name.as_str(), self.t.segment_s, self.t.keep_s);
        let mut total = self.total;
        // Expired segments anywhere in the list (a clock step can leave future-dated ones).
        self.segs.retain(|&(s, sz)| {
            if s + seg_len + keep_s < now_s && cur_start != Some(s) {
                let _ = dir.unlink(&seg_name(name, s));
                total = total.saturating_sub(sz);
                false
            } else {
                true
            }
        });
        self.total = total;
        loop {
            let (s, sz) = match self.segs.front() {
                Some(&x) => x,
                None => break,
            };
            let is_cur = self.cur.as_ref().map(|c| c.start) == Some(s);
            let over = cap.map_or(false, |c| self.total + other > c);
            if !over {
                break;
            }
            if is_cur {
                self.cur = None;
            }
            let _ = self.dir.unlink(&seg_name(&self.t.name, s));
            self.total = self.total.saturating_sub(sz);
            self.segs.pop_front();
        }
    }
}

// ---------------------------------------------------------------- query side

/// Name tables a query resolves ids with (the live sampler's, from the published snapshot).
pub struct Dicts<'a> {
    pub mets: &'a [(String, bool)],
    pub ents: &'a [String],
}

/// Frames of one segment, one at a time: `f(payload)` returns false to stop. Frames before
/// `since_ms` are skipped from the header alone (no decompression). Returns the number of
/// corrupt frames hit; reading stops at the first one.
fn read_segment(dir: &Dir, name: &str, disk: bool, since_ms: u64, mut f: impl FnMut(&[u8]) -> bool) -> u64 {
    let file = match dir.open_file(name, O_RDONLY, 0) {
        Ok(f) => f,
        Err(e) => {
            if e.raw_os_error() == Some(40) {
                add_note(format!("{}/{name} is a symlink; not read", dir.path));
            }
            return 0;
        }
    };
    let len = file.metadata().map(|m| m.len()).unwrap_or(0);
    let mut fr = Frames::new(BufReader::with_capacity(64 * 1024, file), len);
    let mut dec = Vec::new();
    let mut bad = 0;
    while fr.next_frame() {
        if fr.ts < since_ms {
            continue;
        }
        if disk {
            if !inflate(&fr.buf, &mut dec) {
                bad += 1;
                break;
            }
            if !f(&dec) {
                break;
            }
        } else if !f(&fr.buf) {
            break;
        }
    }
    bad += fr.bad;
    if bad > 0 {
        CTR.corrupt_reads.fetch_add(bad, Ordering::Relaxed);
    }
    bad
}

fn decode(b: &[u8], want_ent: u32, want: &[u32]) -> Option<(u64, u32, Vec<(u32, Sv)>)> {
    let ts = u64::from_le_bytes(b.get(0..8)?.try_into().ok()?);
    let dt = u32::from_le_bytes(b.get(8..12)?.try_into().ok()?);
    let mut p = 12;
    let n = get_varint(b, &mut p)?;
    let mut res = Vec::new();
    for _ in 0..n {
        let id = get_varint(b, &mut p)? as u32;
        let k = get_varint(b, &mut p)?;
        for _ in 0..k {
            let (m, v) = get_val(b, &mut p)?;
            if id == want_ent && want.contains(&m) {
                res.push((m, v));
            }
        }
        if id == want_ent {
            break; // an entity appears once per frame: the rest is not ours
        }
    }
    Some((ts, dt, res))
}

fn push_sv(o: &mut String, v: Sv) {
    match v {
        Sv::V(x) => jnum(o, x),
        Sv::S(c) => {
            o.push('{');
            jstate(o, c);
            o.push('}');
        }
    }
}

pub struct Series {
    /// Fields of the response object, without the surrounding braces.
    pub body: String,
    pub truncated: bool,
    pub window_s: u64,
}

/// Query one metric of one entity from one tier. A point is `[ts_ms, ...fields]`; a
/// state point is `[ts_ms, {"s":..,"why":..}]` (counters: with `dt_ms` last). The answer
/// stops at `max_bytes` with `truncated` set (newest points are kept out, oldest first).
/// Memory: one decoded frame (at most `MAX_DECODED`) plus the answer; segments are streamed.
pub fn series(cfg: &Config, dicts: &Dicts, entity: &str, metric: &str, tier: &str, window_s: u64, max_bytes: usize) -> Result<Series, PErr> {
    if !cfg.store.enabled {
        return Err(perr("store_disabled", "store disabled"));
    }
    let t = cfg.tier.iter().find(|t| t.name == tier).ok_or_else(|| perr("unknown_tier", format!("unknown tier {tier}")))?;
    let window_s = window_s.min(t.keep_s + t.segment_s);
    let since_ms = wall_ms().saturating_sub(window_s * 1000);
    let eid = match dicts.ents.iter().position(|e| !e.is_empty() && e == entity) {
        Some(p) => p as u32,
        None => return Err(perr("unknown_entity", format!("unknown entity {entity}"))),
    };
    let mi = dicts.mets.iter().position(|(n, _)| n == metric).ok_or_else(|| perr("unknown_metric", format!("unknown metric {metric}")))?;
    let counter = dicts.mets[mi].1;
    let rollup = t.step_s * 1000 != cfg.sampler.tick_ms;
    let mut want = vec![mi as u32];
    if rollup && !counter {
        want.extend_from_slice(&rollup_ids(mi as u32));
    }
    // X-13: a rollup bucket also stores how many of its ticks held a value; the rest were
    // states (reset, first sample, read error), so the sum/avg covers fewer ticks than `dt_ms`.
    let ncols = want.len();
    if rollup {
        want.push(cover_id(mi as u32));
    }
    let bucket_ticks = t.step_s * 1000 / cfg.sampler.tick_ms.max(1);
    let (mut valid, mut partial) = (String::new(), 0u64);
    let disk = t.medium == "disk";
    let dir_path = if disk { &cfg.store.cold_dir } else { &cfg.store.tmpfs_dir };
    let dir = Dir::open_plain(dir_path).map_err(|e| perr("store_io", format!("{dir_path}: {e}")))?;
    let mut segs: Vec<u64> = Vec::new();
    for (n, kind) in dir.list().unwrap_or_default() {
        if kind != Kind::File {
            continue; // a symlink is never read
        }
        if let Some((name, s)) = parse_seg_name(&n) {
            if name == tier && (s + t.segment_s) * 1000 >= since_ms {
                segs.push(s);
            }
        }
    }
    segs.sort();
    let fields: &[&str] = match (rollup, counter) {
        (false, false) => &["ts_ms", "v"],
        (false, true) => &["ts_ms", "delta", "dt_ms"],
        (true, false) => &["ts_ms", "avg", "max", "min", "p95"],
        (true, true) => &["ts_ms", "sum", "dt_ms"],
    };
    let mut o = String::new();
    o.push_str("\"entity\":");
    jstr(&mut o, entity);
    o.push_str(",\"metric\":");
    jstr(&mut o, metric);
    o.push_str(&format!(",\"tier\":\"{}\",\"step_s\":{},\"kind\":\"{}\",\"fields\":{:?},\"points\":[", tier, t.step_s, if counter { "counter" } else { "gauge" }, fields));
    let mut first = true;
    let mut truncated = false;
    let mut corrupt = 0u64;
    for s in segs {
        let bad = read_segment(&dir, &seg_name(tier, s), disk, since_ms, |body| {
            let (ts, dt, vals) = match decode(body, eid, &want) {
                Some(x) => x,
                None => return true,
            };
            if ts < since_ms {
                return true;
            }
            let get = |m: u32| vals.iter().find(|(k, _)| *k == m).map(|x| x.1);
            let main = match get(want[0]) {
                Some(v) => v,
                None => return true, // not measured in this frame: a gap, not a zero
            };
            let mut row = String::new();
            row.push('[');
            row.push_str(&ts.to_string());
            match main {
                Sv::S(_) => {
                    row.push(',');
                    push_sv(&mut row, main);
                }
                Sv::V(_) if counter => {
                    row.push(',');
                    push_sv(&mut row, main);
                    row.push_str(&format!(",{dt}"));
                }
                Sv::V(_) if rollup && !counter => {
                    for &m in &want[..ncols] {
                        row.push(',');
                        match get(m) {
                            Some(v) => push_sv(&mut row, v),
                            None => row.push_str("null"),
                        }
                    }
                }
                Sv::V(_) => {
                    row.push(',');
                    push_sv(&mut row, main);
                }
            }
            if let (true, Sv::S(_)) = (counter, main) {
                row.push_str(&format!(",{dt}"));
            }
            row.push(']');
            // valid ticks of this bucket: 0 for an all-state bucket, null when the frame predates the count
            let v = match main {
                Sv::S(_) => Some(0.0),
                Sv::V(_) => match get(*want.last().unwrap()) {
                    Some(Sv::V(n)) => Some(n),
                    _ => None,
                },
            };
            let vtxt = match (rollup, v) {
                (false, _) => String::new(),
                (true, Some(n)) => format!("{}", n as u64),
                (true, None) => "null".to_string(),
            };
            if o.len() + valid.len() + row.len() + vtxt.len() + 96 + 64 > max_bytes {
                truncated = true;
                return false;
            }
            if !first {
                o.push(',');
            }
            if rollup {
                if !first {
                    valid.push(',');
                }
                valid.push_str(&vtxt);
                if v.map_or(false, |n| (n as u64) < bucket_ticks) {
                    partial += 1;
                }
            }
            first = false;
            o.push_str(&row);
            true
        });
        corrupt += bad;
        if truncated {
            break;
        }
    }
    o.push_str(&format!("],\"corrupt_frames\":{corrupt}"));
    if rollup {
        // parallel to `points`: ticks of the bucket that held a value, out of `bucket_ticks`
        o.push_str(&format!(",\"bucket_ticks\":{bucket_ticks},\"valid_ticks\":[{valid}],\"partial_points\":{partial}"));
    }
    Ok(Series { body: o, truncated, window_s })
}

/// Events in the window (newest `limit` of them, oldest first), from the events file and
/// its rotated predecessor, streamed line by line. Bounded by `max_bytes`; invalid UTF-8 and
/// a torn last line are tolerated.
pub fn events(cfg: &Config, entity: &str, window_s: u64, limit: usize, max_bytes: usize) -> (String, bool) {
    let since_ms = wall_ms().saturating_sub(window_s.saturating_mul(1000));
    let mut keep: VecDeque<String> = VecDeque::new();
    let mut bytes = 0usize;
    let mut truncated = false;
    let (ev_dir, ev_name) = match split_file(&cfg.events.events_path).and_then(|(d, n)| Dir::open_plain(&d).map(|d| (d, n)).map_err(|e| perr("store_io", e.to_string()))) {
        Ok(x) => x,
        Err(_) => return ("[]".to_string(), false),
    };
    for p in [format!("{ev_name}.1"), ev_name.clone()] {
        let f = match ev_dir.open_file(&p, O_RDONLY, 0) {
            Ok(f) => f,
            Err(_) => continue,
        };
        let mut r = BufReader::new(f);
        let mut buf = Vec::new();
        loop {
            buf.clear();
            match (&mut r).take(64 * 1024).read_until(b'\n', &mut buf) {
                Ok(0) | Err(_) => break,
                Ok(_) => {}
            }
            if buf.last() != Some(&b'\n') {
                continue; // torn or over-long line
            }
            buf.pop();
            let l = String::from_utf8_lossy(&buf);
            let v: serde_json::Value = match serde_json::from_str(&l) {
                Ok(v) => v,
                Err(_) => continue,
            };
            if v["ts_ms"].as_u64().unwrap_or(0) < since_ms || (!entity.is_empty() && v["entity"].as_str() != Some(entity)) {
                continue;
            }
            bytes += l.len() + 1;
            keep.push_back(l.into_owned());
            while keep.len() > limit || bytes + 16 > max_bytes {
                match keep.pop_front() {
                    Some(x) => bytes -= x.len() + 1,
                    None => break,
                }
                truncated = true;
            }
        }
    }
    let mut o = String::from("[");
    for (i, l) in keep.iter().enumerate() {
        if i > 0 {
            o.push(',');
        }
        o.push_str(l);
    }
    o.push(']');
    (o, truncated)
}
