//! Container -> cgroup scope resolution, and the candidate store (design §3, D7/D12).
//!
//! Two jobs live here because they share one object, the *unit*:
//!
//! 1. **[`resolve_container`]**: a registrant names a **container**, never a
//!    cgroup path (D12), because a client in its own private cgroup namespace
//!    reads `0::/` and cannot know the host path. The guard walks the injected
//!    cgroup root, finds the `docker-<id>.scope` directory under an
//!    `allowed_ancestors` slice, and **refuses with a named reason** when it
//!    finds none, several, a protected match, or a tree it cannot read. Nothing
//!    is stored on a refusal, so resolution bounds the target cgroup rather than
//!    trusting a client path. It does not bind an id to its registrant (TG-12).
//! 2. **[`Store`]**: the registered candidate set with heartbeat liveness. Its
//!    expiry is `missed_heartbeats x heartbeat_s`, derived by the guard from
//!    config keys only — never a job duration (M2/D13).
//!
//! ## Why the walk is hand-rolled, and what it deliberately does not do
//!
//! No third-party crate (design §5), so no `walkdir`. The walk is bounded
//! ([`MAX_DEPTH`]), does not follow symlinks (a symlink inside an allowed slice
//! would otherwise escape the containment argument), and never opens a cgroup
//! *file*: P1 needs the directory and its inode, nothing more.
//!
//! ## The one reconciliation this module makes
//!
//! Design §3 says the guard "takes the *shallowest* match"; `docs/CONSUMERS.md`
//! says the refusal `several` means "more than one depth matches". Both are
//! honoured: the shallowest admissible match wins when it is unique, any deeper
//! match is recorded in [`Scope::shadowed`] so the daemon can journal it (never
//! silently dropped), and `several` is refused only when two admissible matches
//! compete at the same shallowest depth — where no rule can choose between them.
//! See the report's deviations list.

use std::collections::BTreeMap;
use std::fmt;
use std::os::unix::fs::MetadataExt;
use std::path::{Path, PathBuf};

use crate::config::{Config, StaticUnit};

/// The docker scope name prefix (`docker-<container>.scope`).
pub const SCOPE_PREFIX: &str = "docker-";
/// The scope name suffix.
pub const SCOPE_SUFFIX: &str = ".scope";
/// How deep the walk descends from the injected root.
///
/// A real hierarchy is `slice[/slice]/scope` at most four components deep; the
/// bound is what stops a pathological or adversarial tree from costing the guard
/// a tick, and it is a *refusal* (the scope is then not found) rather than a
/// silently truncated search: [`Refusal::NoneFound`] carries the depth it
/// searched to.
pub const MAX_DEPTH: usize = 16;

/// A cgroup scope the guard resolved, ready for the candidate store.
#[derive(Clone, Debug, PartialEq, Eq)]
pub struct Scope {
    /// The scope's own name, e.g. `docker-0123abcd.scope`.
    pub name: String,
    /// The path under the injected cgroup root, with `/` separators.
    pub relative: String,
    /// The absolute path (`<root>/<relative>`).
    pub path: PathBuf,
    /// The directory inode, so a recycled path is detectable (design §4).
    pub cgroup_id: u64,
    /// Deeper admissible matches that lost to the shallowest one. Reported, never
    /// written to the wire in P1: a non-empty list means the guard is acting on a
    /// shallower cgroup than another match of the same name (design §3 D1).
    pub shadowed: Vec<String>,
}

/// Why a container could not be resolved to exactly one scope.
///
/// The [`Self::reason`] word is the stable wire/directive vocabulary; the detail
/// is for a human reading a refusal line.
#[derive(Clone, Debug, PartialEq, Eq)]
pub enum Refusal {
    /// No `docker-<id>.scope` directory under any allowed ancestor.
    NoneFound {
        /// The container the client named.
        container: String,
        /// The slices that were searched.
        searched: Vec<String>,
    },
    /// Two admissible matches at the same shallowest depth: no rule can choose.
    Several {
        /// The container the client named.
        container: String,
        /// The competing paths, relative to the root.
        matches: Vec<String>,
    },
    /// The only match lives under a `protected` ancestor.
    Protected {
        /// The container the client named.
        container: String,
        /// The protected path, relative to the root.
        path: String,
    },
    /// The client's container name is not a container id or a docker scope name.
    Malformed {
        /// What the client sent.
        container: String,
        /// Why it was refused.
        detail: String,
    },
    /// The tree could not be read, so "not found" would be a claim the guard
    /// cannot make.
    Unreadable {
        /// The directory that failed.
        path: String,
        /// The operating system's reason.
        detail: String,
    },
}

impl Refusal {
    /// The one-word reason the wire and the journal use.
    pub fn reason(&self) -> &'static str {
        match self {
            Self::NoneFound { .. } => "none",
            Self::Several { .. } => "several",
            Self::Protected { .. } => "protected",
            Self::Malformed { .. } => "malformed",
            Self::Unreadable { .. } => "unreadable",
        }
    }

    /// The container the client named, when the refusal knows it.
    pub fn container(&self) -> &str {
        match self {
            Self::NoneFound { container, .. }
            | Self::Several { container, .. }
            | Self::Protected { container, .. }
            | Self::Malformed { container, .. } => container,
            Self::Unreadable { .. } => "",
        }
    }

    /// One line for a journal entry, a WARN or a `candidates` row.
    pub fn detail(&self) -> String {
        match self {
            Self::NoneFound {
                container,
                searched,
            } => format!(
                "no {}{SCOPE_SUFFIX} for container {container:?} under any of {searched:?}",
                SCOPE_PREFIX
            ),
            Self::Several { container, matches } => format!(
                "container {container:?} resolves to {} scopes at one depth: {}",
                matches.len(),
                matches.join(", ")
            ),
            Self::Protected { container, path } => {
                format!("container {container:?} resolves only under the protected path {path}")
            }
            Self::Malformed { container, detail } => {
                format!("{container:?} is not a container: {detail}")
            }
            Self::Unreadable { path, detail } => format!("{path} cannot be read: {detail}"),
        }
    }
}

impl fmt::Display for Refusal {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        write!(f, "{}: {}", self.reason(), self.detail())
    }
}

impl std::error::Error for Refusal {}

/// The scope name a client's `container` field names.
///
/// Accepted: a bare docker container id or name (wrapped as `docker-<it>.scope`),
/// or an already-formed `docker-<id>.scope`. Anything else is refused: a client
/// naming a *path* is exactly what D12 exists to prevent.
pub fn scope_name(container: &str) -> Result<String, Refusal> {
    let container = container.trim();
    let malformed = |detail: &str| Refusal::Malformed {
        container: container.to_string(),
        detail: detail.to_string(),
    };
    if container.is_empty() {
        return Err(malformed("an empty container name"));
    }
    let body = match container.strip_prefix(SCOPE_PREFIX) {
        // `docker-<id>` with no `.scope`: the client may have sent a container
        // *name* that happens to start with `docker-`, or half a scope name. The
            // two are indistinguishable, so it is refused rather than guessed.
        Some(rest) => match rest.strip_suffix(SCOPE_SUFFIX) {
            Some(id) => id,
            None => {
                return Err(malformed(
                    "a `docker-` name must be a complete `docker-<id>.scope` scope name or a bare container id",
                ))
            }
        },
        None => {
            if container.ends_with(SCOPE_SUFFIX) {
                return Err(malformed("a scope name starts with `docker-`"));
            }
            container
        }
    };
    if body.is_empty() {
        return Err(malformed("the scope name has an empty container id"));
    }
    if body
        .chars()
        .any(|c| !(c.is_ascii_alphanumeric() || c == '_' || c == '.' || c == '-'))
    {
        return Err(malformed(
            "a container id or name is [A-Za-z0-9_.-] only; a path is not accepted",
        ));
    }
    if body.contains("..") || body == "." {
        return Err(malformed("`.` and `..` are not container ids"));
    }
    Ok(format!("{SCOPE_PREFIX}{body}{SCOPE_SUFFIX}"))
}

/// One directory in the walked tree that matched the scope name.
#[derive(Clone, Debug)]
struct Found {
    components: Vec<String>,
    path: PathBuf,
    cgroup_id: u64,
    under_allowed: bool,
    under_protected: bool,
}

impl Found {
    fn relative(&self) -> String {
        self.components.join("/")
    }
}

/// Resolve a registered container to exactly one scope under `allowed_ancestors`.
///
/// `root` is the injected cgroup root: production passes the host's cgroup root,
/// tests pass a temp tree (design §5, D9). Nothing here writes anything.
pub fn resolve_container(root: &Path, container: &str, config: &Config) -> Result<Scope, Refusal> {
    let name = scope_name(container)?;
    let found = walk(root, &name, &config.allowed_ancestors, &config.protected)?;
    decide(container, found, &config.allowed_ancestors)
}

/// Resolve a `/etc`-supplied candidate for a host with no registrant (design §6d).
///
/// Two forms are accepted, both documented in `docs/CONSUMERS.md`: a scope name
/// (`docker-<id>.scope`, resolved exactly like a registration) and a path
/// relative to the cgroup root (`dev-buildkitd.slice/runner.scope`).
///
/// A path is the operator's own `/etc` choice, so it is **not** held to
/// `allowed_ancestors` — a standalone host has no docker scopes at all and
/// `[[static_unit]]` is its only candidate source. It is still refused under a
/// `protected` ancestor: an operator cannot park the cockpit by accident.
pub fn resolve_static_unit(
    root: &Path,
    unit: &StaticUnit,
    config: &Config,
) -> Result<Scope, Refusal> {
    // A cgroup path contains `/`; a scope *name* does not. So a dotted path such
    // as `dev-gates.slice/runner.scope` is a path, and only a bare
    // `docker-<id>.scope` is treated as a registration would be.
    if !unit.cgroup.contains('/') && unit.cgroup.ends_with(SCOPE_SUFFIX) {
        return resolve_container(root, &unit.cgroup, config).map_err(|refusal| match refusal {
            // A static unit's refusal names the /etc entry, not a container.
            Refusal::NoneFound { searched, .. } => Refusal::NoneFound {
                container: unit.name.clone(),
                searched,
            },
            Refusal::Several { matches, .. } => Refusal::Several {
                container: unit.name.clone(),
                matches,
            },
            Refusal::Protected { path, .. } => Refusal::Protected {
                container: unit.name.clone(),
                path,
            },
            other => other,
        });
    }
    let components = path_components(&unit.cgroup).map_err(|detail| Refusal::Malformed {
        container: unit.name.clone(),
        detail,
    })?;
    let relative = components.join("/");
    let path = root.join(&relative);
    let mut checked_path = root.to_path_buf();
    let mut metadata = None;
    for (index, component) in components.iter().enumerate() {
        checked_path.push(component);
        let current = match std::fs::symlink_metadata(&checked_path) {
            Ok(metadata) => metadata,
            Err(error) if error.kind() == std::io::ErrorKind::NotFound => {
                return Err(Refusal::NoneFound {
                    container: unit.name.clone(),
                    searched: vec![relative],
                })
            }
            // An existing but unreadable path is NOT "absent": that would be a claim
            // the guard cannot make from a failed read.
            Err(error) => {
                return Err(Refusal::Unreadable {
                    path: checked_path.display().to_string(),
                    detail: error.to_string(),
                })
            }
        };
        if current.file_type().is_symlink() {
            return Err(Refusal::Malformed {
                container: unit.name.clone(),
                detail: format!(
                    "[[static_unit]] {} crosses symlink component {}",
                    unit.name,
                    checked_path.display()
                ),
            });
        }
        if index + 1 < components.len() && !current.is_dir() {
            return Err(Refusal::Malformed {
                container: unit.name.clone(),
                detail: format!(
                    "[[static_unit]] {} has non-directory path component {}",
                    unit.name,
                    checked_path.display()
                ),
            });
        }
        metadata = Some(current);
    }
    let metadata = metadata.expect("a validated path has at least one component");
    if !metadata.is_dir() {
        return Err(Refusal::Malformed {
            container: unit.name.clone(),
            detail: format!(
                "[[static_unit]] {} names a cgroup, and {relative} is not a directory",
                unit.name
            ),
        });
    }
    if let Some(protected) = components
        .iter()
        .find(|component| config.protected.iter().any(|p| p == *component))
    {
        return Err(Refusal::Protected {
            container: unit.name.clone(),
            path: format!("{relative} is under the protected ancestor {protected}"),
        });
    }
    Ok(Scope {
        name: components.last().cloned().unwrap_or_default(),
        relative,
        path,
        cgroup_id: metadata.ino(),
        shadowed: Vec::new(),
    })
}

/// Split a `[[static_unit]]` cgroup path without accepting an escape.
fn path_components(text: &str) -> Result<Vec<String>, String> {
    if text.trim().is_empty() {
        return Err("an empty cgroup path".to_string());
    }
    if text.starts_with('/') {
        return Err("a cgroup path is relative to the cgroup root, not absolute".to_string());
    }
    if text.contains('\0') {
        return Err("a cgroup path contains a NUL".to_string());
    }
    let mut components = Vec::new();
    for component in text.split('/') {
        if component.is_empty() || component == "." || component == ".." {
            return Err(format!("{component:?} is not a cgroup path component"));
        }
        components.push(component.to_string());
    }
    Ok(components)
}

/// Walk the tree under `root` and collect every directory named `scope`.
///
/// Bounded by [`MAX_DEPTH`], no symlink is followed, and a directory that cannot
/// be read is reported rather than read as empty (an unreadable tree must not be
/// mistaken for "nothing there"). A `NotFound` below `root` means a cgroup was
/// removed during the walk, so that vanished branch cannot hide a live match.
fn walk(
    root: &Path,
    scope: &str,
    allowed: &[String],
    protected: &[String],
) -> Result<Vec<Found>, Refusal> {
    let mut read_dir = |directory: &Path| std::fs::read_dir(directory);
    walk_using(root, scope, allowed, protected, &mut read_dir)
}

fn walk_using<F>(
    root: &Path,
    scope: &str,
    allowed: &[String],
    protected: &[String],
    read_dir: &mut F,
) -> Result<Vec<Found>, Refusal>
where
    F: FnMut(&Path) -> std::io::Result<std::fs::ReadDir>,
{
    let mut found = Vec::new();
    let mut errors: Vec<(String, String)> = Vec::new();
    let mut stack: Vec<(PathBuf, Vec<String>)> = vec![(root.to_path_buf(), Vec::new())];
    while let Some((directory, ancestors)) = stack.pop() {
        if ancestors.len() >= MAX_DEPTH {
            continue;
        }
        let entries = match read_dir(&directory) {
            Ok(entries) => entries,
            Err(error)
                if directory != root && error.kind() == std::io::ErrorKind::NotFound =>
            {
                continue;
            }
            Err(error) => {
                errors.push((directory.display().to_string(), error.to_string()));
                continue;
            }
        };
        for entry in entries {
            let entry = match entry {
                Ok(entry) => entry,
                Err(error) if error.kind() == std::io::ErrorKind::NotFound => continue,
                Err(error) => {
                    errors.push((directory.display().to_string(), error.to_string()));
                    continue;
                }
            };
            let file_type = match entry.file_type() {
                Ok(file_type) => file_type,
                Err(error) if error.kind() == std::io::ErrorKind::NotFound => continue,
                Err(error) => {
                    errors.push((entry.path().display().to_string(), error.to_string()));
                    continue;
                }
            };
            // A cgroupfs hierarchy has no symlinks; following one could escape
            // the ancestors this resolution is defined by, so it is skipped.
            if !file_type.is_dir() {
                continue;
            }
            let name = entry.file_name().to_string_lossy().into_owned();
            let mut components = ancestors.clone();
            components.push(name.clone());
            let under_allowed = ancestors
                .iter()
                .any(|component| allowed.iter().any(|a| a == component));
            let under_protected = ancestors
                .iter()
                .any(|component| protected.iter().any(|p| p == component));
            if name == scope {
                let cgroup_id = match entry.metadata() {
                    Ok(metadata) => metadata.ino(),
                    Err(error) if error.kind() == std::io::ErrorKind::NotFound => continue,
                    Err(error) => {
                        errors.push((entry.path().display().to_string(), error.to_string()));
                        continue;
                    }
                };
                found.push(Found {
                    components,
                    path: entry.path(),
                    cgroup_id,
                    under_allowed,
                    under_protected,
                });
                // The scope is a leaf: nothing below it is another match of the
                // same unit that could beat it, and descending would only cost time.
                continue;
            }
            stack.push((entry.path(), components));
        }
    }
    if let Some((path, _)) = errors.first() {
        let detail = errors
            .iter()
            .map(|(path, error)| format!("{path}: {error}"))
            .collect::<Vec<_>>()
            .join("; ");
        return Err(Refusal::Unreadable {
            path: path.clone(),
            detail,
        });
    }
    Ok(found)
}

/// Choose the one scope a match list describes, or name the refusal.
fn decide(container: &str, found: Vec<Found>, allowed: &[String]) -> Result<Scope, Refusal> {
    let admissible: Vec<&Found> = found
        .iter()
        .filter(|f| f.under_allowed && !f.under_protected)
        .collect();
    if admissible.is_empty() {
        if let Some(protected) = found.iter().find(|f| f.under_protected) {
            return Err(Refusal::Protected {
                container: container.to_string(),
                path: protected.relative(),
            });
        }
        return Err(Refusal::NoneFound {
            container: container.to_string(),
            searched: allowed.to_vec(),
        });
    }
    let shallowest = admissible
        .iter()
        .map(|f| f.components.len())
        .min()
        .expect("a non-empty list has a minimum");
    let ties: Vec<&&Found> = admissible
        .iter()
        .filter(|f| f.components.len() == shallowest)
        .collect();
    if ties.len() > 1 {
        return Err(Refusal::Several {
            container: container.to_string(),
            matches: ties.iter().map(|f| f.relative()).collect(),
        });
    }
    let winner = ties.first().copied().copied().expect("exactly one tie");
    let shadowed = admissible
        .iter()
        .filter(|f| f.components.len() != shallowest)
        .map(|f| f.relative())
        .collect();
    Ok(Scope {
        name: winner
            .components
            .last()
            .cloned()
            .expect("a match has components"),
        relative: winner.relative(),
        path: winner.path.clone(),
        cgroup_id: winner.cgroup_id,
        shadowed,
    })
}

/// A candidate's `priority` flag (design §3, Q3).
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum Priority {
    /// A normal candidate.
    Freezable,
    /// Parked only when nothing else is available.
    LastResort,
}

impl Priority {
    /// The spelling the wire and the configuration use.
    pub fn as_str(self) -> &'static str {
        match self {
            Self::Freezable => "freezable",
            Self::LastResort => "last-resort",
        }
    }

    /// The flag a client sent, or `None` for an unknown spelling.
    pub fn from_name(name: &str) -> Option<Self> {
        match name {
            "freezable" => Some(Self::Freezable),
            "last-resort" => Some(Self::LastResort),
            _ => None,
        }
    }

    /// The order the victim rule sorts by: `freezable` before `last-resort`.
    pub fn rank(self) -> u8 {
        match self {
            Self::Freezable => 0,
            Self::LastResort => 1,
        }
    }
}

/// Where a stored candidate came from, which controls heartbeat expiry.
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum RegistrationSource {
    /// A live registrant whose heartbeat may go silent.
    Registrant,
    /// A static `[[static_unit]]` configured by the host; it has no heartbeat.
    Static,
}

/// What a registrant sends to `register`, minus the facts the guard derives.
#[derive(Clone, Debug, PartialEq, Eq)]
pub struct Registration {
    /// The registrant's own unit id (unique in the store).
    pub id: String,
    /// The container id or scope name the client named.
    pub container: String,
    /// The resolved scope; nothing is stored without it.
    pub scope: Scope,
    /// The registrant's own ranking fact (design §3 Q3: the guard never computes it).
    pub score: i64,
    /// The registrant's priority flag.
    pub priority: Priority,
    /// The kind of unit (`gate`, `campaign`, ...), free text.
    pub kind: String,
    /// The source that determines whether heartbeat expiry applies.
    pub source: RegistrationSource,
    /// The owning tool.
    pub owner: String,
    /// The run/lane id the frozen interval belongs to.
    pub run: String,
    /// The cadence the client declared, when it did. Recorded for reporting; the
    /// expiry window comes from config keys, never from this (M2/D13).
    pub heartbeat_s: Option<u64>,
    /// Registration order: higher is more recent, and `newest` sorts by it.
    pub registered_seq: u64,
    /// Monotonic instant of the last heartbeat (or of the registration).
    pub last_heartbeat_ns: u64,
}

impl Registration {
    /// Seconds since the last heartbeat at `now_ns` (monotonic).
    pub fn silent_s(&self, now_ns: u64) -> u64 {
        crate::clock::seconds_of_ns(now_ns.saturating_sub(self.last_heartbeat_ns))
    }
}

/// Why a verb could not be applied to the store.
#[derive(Clone, Debug, PartialEq, Eq)]
pub enum StoreError {
    /// An id that is already registered.
    Duplicate {
        /// The id.
        id: String,
    },
    /// An id this store does not hold.
    Missing {
        /// The id.
        id: String,
    },
}

impl fmt::Display for StoreError {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        match self {
            Self::Duplicate { id } => write!(f, "duplicate: {id:?} is already registered"),
            Self::Missing { id } => write!(f, "missing: {id:?} is not registered"),
        }
    }
}

impl std::error::Error for StoreError {}

impl StoreError {
    /// The one-word reason the wire uses (`duplicate`, `missing`).
    pub fn reason(&self) -> &'static str {
        match self {
            Self::Duplicate { .. } => "duplicate",
            Self::Missing { .. } => "missing",
        }
    }
}

/// The registered candidate set: what the guard may park, and how it ages.
///
/// The store holds no parked state in P1: nothing can be frozen, so `frozen` is
/// the daemon's fact and arrives with P2. What it does hold is the liveness rule
/// the design fixes: `ttl_s = missed_heartbeats x heartbeat_s`, both config keys.
#[derive(Clone, Debug)]
pub struct Store {
    ttl_s: u64,
    next_seq: u64,
    entries: BTreeMap<String, Registration>,
}

impl Store {
    /// An empty store whose expiry is `ttl_s` seconds of silence.
    pub fn new(ttl_s: u64) -> Self {
        Self {
            ttl_s,
            next_seq: 0,
            entries: BTreeMap::new(),
        }
    }

    /// A store whose expiry follows the configuration (design §3, M2).
    pub fn from_config(config: &Config) -> Self {
        Self::new(config.ttl_s())
    }

    /// The expiry window in seconds.
    pub fn ttl_s(&self) -> u64 {
        self.ttl_s
    }

    /// How many candidates are registered.
    pub fn len(&self) -> usize {
        self.entries.len()
    }

    /// Whether the store holds nothing.
    pub fn is_empty(&self) -> bool {
        self.entries.is_empty()
    }

    /// The newest registration sequence number handed out so far (0 when empty).
    pub fn last_seq(&self) -> u64 {
        self.next_seq
    }

    /// One registration by id.
    pub fn get(&self, id: &str) -> Option<&Registration> {
        self.entries.get(id)
    }

    /// Every registration, in id order.
    pub fn iter(&self) -> impl Iterator<Item = &Registration> {
        self.entries.values()
    }

    /// Store one registration, assigning its sequence number.
    ///
    /// A duplicate id is refused rather than overwritten: two units that share an
    /// id would share a heartbeat and one of them would expire silently.
    pub fn register(&mut self, mut registration: Registration) -> Result<Registration, StoreError> {
        if self.entries.contains_key(&registration.id) {
            return Err(StoreError::Duplicate {
                id: registration.id.clone(),
            });
        }
        self.next_seq = self.next_seq.saturating_add(1);
        registration.registered_seq = self.next_seq;
        self.entries
            .insert(registration.id.clone(), registration.clone());
        Ok(registration)
    }

    /// Apply `update {id, score, priority}` (design §3).
    pub fn update(
        &mut self,
        id: &str,
        score: Option<i64>,
        priority: Option<Priority>,
    ) -> Result<&Registration, StoreError> {
        let entry = self
            .entries
            .get_mut(id)
            .ok_or_else(|| StoreError::Missing { id: id.to_string() })?;
        if let Some(score) = score {
            entry.score = score;
        }
        if let Some(priority) = priority {
            entry.priority = priority;
        }
        Ok(entry)
    }

    /// Record a heartbeat. A heartbeat from an unknown id is refused, not created:
    /// registration is the act that resolves a scope.
    pub fn heartbeat(&mut self, id: &str, now_ns: u64) -> Result<&Registration, StoreError> {
        let entry = self
            .entries
            .get_mut(id)
            .ok_or_else(|| StoreError::Missing { id: id.to_string() })?;
        entry.last_heartbeat_ns = now_ns;
        Ok(entry)
    }

    /// Remove a registration the registrant itself ended (M3: this case thaws).
    pub fn deregister(&mut self, id: &str) -> Result<Registration, StoreError> {
        self.entries
            .remove(id)
            .ok_or_else(|| StoreError::Missing { id: id.to_string() })
    }

    /// Remove every registration whose last heartbeat is older than `ttl_s`.
    ///
    /// M3/D14: silent expiry removes the *candidate*, it does not thaw the unit —
    /// a frozen scope keeps its park until pressure release, aging or a restart.
    /// P1 has nothing frozen, so this only ever shortens the candidate set; the
    /// caller journals one `expired` line per returned registration.
    pub fn expire(&mut self, now_ns: u64) -> Vec<Registration> {
        let expired_ids: Vec<String> = self
            .entries
            .values()
            .filter(|entry| {
                entry.source == RegistrationSource::Registrant
                    && entry.silent_s(now_ns) >= self.ttl_s
            })
            .map(|entry| entry.id.clone())
            .collect();
        expired_ids
            .into_iter()
            .filter_map(|id| self.entries.remove(&id))
            .collect()
    }

    /// The candidate order for the next tick: `freezable` before `last-resort`,
    /// then newest registration first (design §2 D3, `newest`).
    ///
    /// `candidates` prints exactly this list, so the guard's order is inspectable
    /// before it ever acts (design §2 D4).
    pub fn newest_first(&self) -> Vec<&Registration> {
        let mut ordered: Vec<&Registration> = self.entries.values().collect();
        ordered.sort_by(|left, right| {
            left.priority
                .rank()
                .cmp(&right.priority.rank())
                .then(right.registered_seq.cmp(&left.registered_seq))
        });
        ordered
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::testutil::TempTree;

    fn config() -> Config {
        Config::defaults()
    }

    /// Build `<root>/dev.slice/dev-gates.slice/docker-<id>.scope` and return the id.
    fn gate_scope(tree: &TempTree, ancestor: &str, id: &str) -> String {
        std::fs::create_dir_all(tree.at(&format!(
            "dev.slice/{ancestor}/{SCOPE_PREFIX}{id}{SCOPE_SUFFIX}"
        )))
        .expect("create scope");
        id.to_string()
    }

    #[test]
    fn a_container_resolves_to_the_shallowest_scope_under_an_allowed_ancestor() {
        let tree = TempTree::new("units-resolve");
        let id = gate_scope(&tree, "dev-gates.slice", "0123abcd");
        let config = config();
        let scope = resolve_container(tree.path(), &id, &config).expect("resolves");
        assert_eq!(scope.name, "docker-0123abcd.scope");
        assert_eq!(
            scope.relative,
            "dev.slice/dev-gates.slice/docker-0123abcd.scope"
        );
        assert!(scope.path.is_dir());
        assert_eq!(
            scope.cgroup_id,
            std::fs::metadata(&scope.path).expect("metadata").ino(),
            "the inode is the identity a recycled path must not reuse"
        );
        assert!(scope.shadowed.is_empty());

        // The scope name itself is accepted: a client may send either form.
        let scope = resolve_container(
            tree.path(),
            &format!("{SCOPE_PREFIX}{id}{SCOPE_SUFFIX}"),
            &config,
        )
        .expect("scope names resolve too");
        assert_eq!(
            scope.relative,
            "dev.slice/dev-gates.slice/docker-0123abcd.scope"
        );
    }

    #[test]
    fn the_deeper_match_loses_and_is_reported_not_dropped() {
        let tree = TempTree::new("units-shadow");
        let id = gate_scope(&tree, "dev-gates.slice", "55aa");
        // A second, deeper cgroup of the same name: D1 says the shallowest wins,
        // and the loser is recorded so the daemon can journal it.
        std::fs::create_dir_all(tree.at(&format!(
            "dev.slice/dev-gates.slice/nested.slice/{SCOPE_PREFIX}{id}{SCOPE_SUFFIX}"
        )))
        .expect("create nested scope");
        let scope =
            resolve_container(tree.path(), &id, &config()).expect("resolves to the shallow one");
        assert_eq!(
            scope.relative,
            "dev.slice/dev-gates.slice/docker-55aa.scope"
        );
        assert_eq!(
            scope.shadowed,
            vec!["dev.slice/dev-gates.slice/nested.slice/docker-55aa.scope".to_string()]
        );
    }

    #[test]
    fn two_matches_at_the_same_depth_are_refused_as_several() {
        let tree = TempTree::new("units-several");
        let id = gate_scope(&tree, "dev-gates.slice", "77bb");
        gate_scope(&tree, "dev-background.slice", "77bb");
        let refusal = resolve_container(tree.path(), &id, &config()).expect_err("ambiguous");
        assert_eq!(refusal.reason(), "several");
        let text = refusal.detail();
        assert!(text.contains("dev-gates.slice"), "{text}");
        assert!(text.contains("dev-background.slice"), "{text}");
    }

    #[test]
    fn nothing_under_the_allowed_ancestors_is_a_refusal_that_names_what_it_searched() {
        let tree = TempTree::new("units-none");
        let id = gate_scope(&tree, "dev-buildkitd.slice", "99cc");
        // A real scope, but under a slice this guard may not park.
        let mut config = config();
        config.allowed_ancestors = vec!["dev-gates.slice".to_string()];
        let refusal = resolve_container(tree.path(), &id, &config)
            .expect_err("not under an allowed ancestor");
        assert_eq!(refusal.reason(), "none");
        assert!(
            refusal.detail().contains("dev-gates.slice"),
            "{}",
            refusal.detail()
        );
        assert!(
            !refusal.detail().contains("dev-buildkitd"),
            "the searched list is the refusal's evidence"
        );
    }

    #[test]
    fn a_match_under_a_protected_ancestor_is_refused_as_protected() {
        let tree = TempTree::new("units-protected");
        let id = gate_scope(&tree, "dev-interactive.slice", "abcd");
        let refusal =
            resolve_container(tree.path(), &id, &config()).expect_err("the cockpit is protected");
        assert_eq!(refusal.reason(), "protected");
        assert!(
            refusal.detail().contains("dev-interactive.slice"),
            "{}",
            refusal.detail()
        );
    }

    #[test]
    fn an_allowed_match_wins_over_a_protected_one_but_a_protected_only_is_refused() {
        let tree = TempTree::new("units-both");
        let id = gate_scope(&tree, "dev-gates.slice", "1234");
        gate_scope(&tree, "dev-interactive.slice", "1234");
        let scope = resolve_container(tree.path(), &id, &config()).expect("the allowed scope wins");
        assert_eq!(
            scope.relative,
            "dev.slice/dev-gates.slice/docker-1234.scope"
        );
    }

    #[test]
    fn an_unreadable_tree_is_not_mistaken_for_an_empty_one() {
        let tree = TempTree::new("units-unreadable");
        let root = tree.path().join("cgroup");
        std::fs::create_dir_all(&root).expect("create root");
        // A readable but genuinely empty root is "none": the walk searched it and
        // found nothing, which is a fact about the tree, not about readability.
        let refusal = resolve_container(&root, "0000", &config()).expect_err("no scope");
        assert_eq!(refusal.reason(), "none", "{}", refusal.detail());
        // A root that does not exist cannot be searched, so "none" would be a
        // claim the guard cannot make: it is `unreadable`, never a silent empty.
        let refusal = resolve_container(&tree.path().join("absent"), "0000", &config())
            .expect_err("an absent root is a refusal");
        assert_eq!(refusal.reason(), "unreadable", "{}", refusal.detail());
    }

    #[test]
    fn a_partial_walk_is_unreadable_even_after_finding_one_candidate() {
        let tree = TempTree::new("units-partial-walk");
        let root = tree.path().join("cgroup");
        std::fs::create_dir_all(root.join("dev.slice/dev-gates.slice/docker-abc.scope"))
            .expect("readable candidate");
        std::fs::create_dir_all(root.join("dev.slice/dev-background.slice"))
            .expect("unreadable sibling");
        let config = config();
        let mut rejected_branch = false;
        let refusal = walk_using(
            &root,
            "docker-abc.scope",
            &config.allowed_ancestors,
            &config.protected,
            &mut |directory| {
                if directory.ends_with("dev-background.slice") {
                    rejected_branch = true;
                    Err(std::io::Error::from(std::io::ErrorKind::PermissionDenied))
                } else {
                    std::fs::read_dir(directory)
                }
            },
        )
        .expect_err("a partial search cannot prove uniqueness");
        assert!(
            rejected_branch,
            "the injected unreadable branch was visited"
        );
        assert_eq!(refusal.reason(), "unreadable");
        assert!(refusal.detail().contains("dev-background.slice"));
    }

    #[test]
    fn a_vanished_subtree_does_not_hide_a_unique_live_match() {
        let tree = TempTree::new("units-vanished-branch");
        let root = tree.path();
        let id = gate_scope(&tree, "dev-gates.slice", "abc");
        let vanished = tree.at("dev.slice/vanished.slice");
        std::fs::create_dir_all(&vanished).expect("transient subtree");
        let config = config();
        let found = walk_using(
            root,
            "docker-abc.scope",
            &config.allowed_ancestors,
            &config.protected,
            &mut |directory| {
                if directory == vanished {
                    Err(std::io::Error::from(std::io::ErrorKind::NotFound))
                } else {
                    std::fs::read_dir(directory)
                }
            },
        )
        .expect("a removed branch cannot hide a live scope");
        let scope = decide(&id, found, &config.allowed_ancestors).expect("unique live match");
        assert_eq!(scope.relative, "dev.slice/dev-gates.slice/docker-abc.scope");
    }

    #[test]
    fn malformed_container_names_are_refused_rather_than_looked_up() {
        let tree = TempTree::new("units-malformed");
        let root = tree.path();
        for container in [
            "",
            "   ",
            "/dev.slice/x",
            "docker-a/../b",
            "..",
            "a b",
            "docker-",
            "abc.scope",
        ] {
            let refusal = resolve_container(root, container, &config()).expect_err("refused");
            assert_eq!(
                refusal.reason(),
                "malformed",
                "{container:?} -> {refusal:?}"
            );
            assert_eq!(
                refusal.container(),
                container.trim(),
                "the refusal echoes what the client sent"
            );
        }
    }

    #[test]
    fn a_static_unit_resolves_as_a_path_or_as_a_scope_name() {
        let tree = TempTree::new("units-static");
        std::fs::create_dir_all(tree.at("dev-buildkitd.slice/runner.scope")).expect("create path");
        let mut config = config();
        config.static_units = vec![
            StaticUnit {
                name: "nightly".to_string(),
                cgroup: "dev-buildkitd.slice".to_string(),
            },
            StaticUnit {
                name: "runner".to_string(),
                cgroup: "dev-buildkitd.slice/runner.scope".to_string(),
            },
        ];
        let slice = resolve_static_unit(tree.path(), &config.static_units[0], &config)
            .expect("the slice exists");
        assert_eq!(slice.relative, "dev-buildkitd.slice");
        assert_eq!(slice.name, "dev-buildkitd.slice");
        assert!(slice.path.is_dir());
    }

    #[test]
    fn a_static_path_refuses_a_symlink_before_protected_ancestor_checks() {
        let tree = TempTree::new("units-static-symlink");
        std::fs::create_dir_all(tree.at("system.slice/ssh.service")).expect("protected target");
        let link = tree.at("dev-gates.slice/ssh.service");
        std::os::unix::fs::symlink("../system.slice/ssh.service", &link).expect("symlink");
        let unit = StaticUnit {
            name: "not-ssh".to_string(),
            cgroup: "dev-gates.slice/ssh.service".to_string(),
        };
        let refusal = resolve_static_unit(tree.path(), &unit, &config()).expect_err("symlink");
        assert_eq!(refusal.reason(), "malformed", "{refusal}");
        assert!(refusal.detail().contains("symlink"), "{refusal}");
    }

    #[test]
    fn a_static_unit_is_still_refused_under_a_protected_ancestor_and_for_a_bad_path() {
        let tree = TempTree::new("units-static-refused");
        std::fs::create_dir_all(tree.at("dev-interactive.slice")).expect("create protected path");
        let config = config();
        // `allowed_ancestors` is deliberately NOT consulted for a path form (a
        // standalone host has no docker scopes), but `protected` always is.
        let unit = StaticUnit {
            name: "cockpit".to_string(),
            cgroup: "dev-interactive.slice".to_string(),
        };
        let refusal = resolve_static_unit(tree.path(), &unit, &config).expect_err("protected");
        assert_eq!(refusal.reason(), "protected");
        assert_eq!(
            refusal.container(),
            "cockpit",
            "the /etc entry is named, not a container"
        );

        for cgroup in ["../escape", "/absolute", "a//b", ""] {
            let unit = StaticUnit {
                name: "bad".to_string(),
                cgroup: cgroup.to_string(),
            };
            let refusal =
                resolve_static_unit(tree.path(), &unit, &config).expect_err("malformed path");
            assert_eq!(refusal.reason(), "malformed", "{cgroup:?} -> {refusal:?}");
        }
    }

    fn registration(id: &str, scope: Scope, priority: Priority) -> Registration {
        Registration {
            id: id.to_string(),
            container: format!("container-{id}"),
            scope,
            score: 1,
            priority,
            kind: "gate".to_string(),
            source: RegistrationSource::Registrant,
            owner: "run-gate".to_string(),
            run: format!("run-{id}"),
            heartbeat_s: None,
            registered_seq: 0,
            last_heartbeat_ns: 0,
        }
    }

    fn scope_for(id: &str) -> Scope {
        Scope {
            name: format!("docker-{id}.scope"),
            relative: format!("dev.slice/dev-gates.slice/docker-{id}.scope"),
            path: PathBuf::from(format!(
                "/cgroup/dev.slice/dev-gates.slice/docker-{id}.scope"
            )),
            cgroup_id: 42,
            shadowed: Vec::new(),
        }
    }

    #[test]
    fn registration_order_is_the_newest_rule_and_priority_sorts_first() {
        let mut store = Store::new(90);
        store
            .register(registration("a", scope_for("a"), Priority::Freezable))
            .expect("register a");
        store
            .register(registration("b", scope_for("b"), Priority::LastResort))
            .expect("register b");
        store
            .register(registration("c", scope_for("c"), Priority::Freezable))
            .expect("register c");
        assert_eq!(store.last_seq(), 3);
        let order: Vec<&str> = store
            .newest_first()
            .iter()
            .map(|entry| entry.id.as_str())
            .collect();
        assert_eq!(
            order,
            vec!["c", "a", "b"],
            "freezable newest-first, then last-resort"
        );
        assert_eq!(store.newest_first()[0].registered_seq, 3);
    }

    #[test]
    fn a_duplicate_id_is_refused_and_an_unknown_one_is_missing() {
        let mut store = Store::new(90);
        store
            .register(registration("a", scope_for("a"), Priority::Freezable))
            .expect("register");
        let refusal = store
            .register(registration("a", scope_for("a2"), Priority::Freezable))
            .expect_err("duplicate");
        assert_eq!(refusal.reason(), "duplicate");
        assert_eq!(store.len(), 1, "a refused registration stores nothing");
        let refusal = store.heartbeat("nope", 1).expect_err("missing");
        assert_eq!(refusal.reason(), "missing");
        let refusal = store.deregister("nope").expect_err("missing");
        assert_eq!(refusal.reason(), "missing");
    }

    #[test]
    fn update_changes_score_and_priority_and_nothing_else() {
        let mut store = Store::new(90);
        store
            .register(registration("a", scope_for("a"), Priority::Freezable))
            .expect("register");
        let entry = store
            .update("a", Some(9), Some(Priority::LastResort))
            .expect("update");
        assert_eq!(entry.score, 9);
        assert_eq!(entry.priority, Priority::LastResort);
        assert_eq!(
            entry.registered_seq, 1,
            "an update is not a re-registration"
        );
        let entry = store
            .update("a", None, None)
            .expect("an update may carry neither fact");
        assert_eq!(entry.score, 9);
    }

    #[test]
    fn silence_past_the_derived_ttl_expires_the_registration_and_a_heartbeat_saves_it() {
        const S: u64 = 1_000_000_000;
        let config = Config::defaults();
        assert_eq!(
            config.ttl_s(),
            90,
            "missed_heartbeats x heartbeat_s from config keys only"
        );
        let mut store = Store::from_config(&config);
        store
            .register(registration("a", scope_for("a"), Priority::Freezable))
            .expect("register");
        // 89 s of silence is inside the window; 90 s is not.
        assert_eq!(store.expire(89 * S).len(), 0);
        store.heartbeat("a", 89 * S).expect("heartbeat");
        assert_eq!(
            store.expire(178 * S).len(),
            0,
            "89 + 90 = 179: still inside"
        );
        let expired = store.expire(179 * S);
        assert_eq!(expired.len(), 1);
        assert_eq!(expired[0].id, "a");
        assert!(store.is_empty());
    }

    #[test]
    fn a_client_declared_heartbeat_cadence_never_changes_the_expiry_window() {
        // Design §3 M2/D13: the window comes from config keys. A client's own
        // `heartbeat_s` is recorded for reporting so a mismatch is visible, and it
        // must not extend or shorten the guard's rule.
        let mut store = Store::new(90);
        let mut entry = registration("slow", scope_for("slow"), Priority::Freezable);
        entry.heartbeat_s = Some(3600);
        store.register(entry).expect("register");
        assert_eq!(store.get("slow").expect("stored").heartbeat_s, Some(3600));
        assert_eq!(store.ttl_s(), 90);
        assert_eq!(
            store.get("slow").expect("stored").silent_s(1_000_000_000),
            1
        );
        assert_eq!(
            store.expire(90 * 1_000_000_000).len(),
            1,
            "the config window still applies"
        );
    }
}
