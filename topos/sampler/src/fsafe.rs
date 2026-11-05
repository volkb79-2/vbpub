//! Directory-anchored file access for everything the sampler writes (X-01, X-08).
//!
//! The sampler usually runs as root and keeps its history in directories an operator names in
//! the configuration. A symlink planted in such a directory must never redirect a truncate, an
//! append or an unlink to another file. So:
//! - a data directory is validated at start (`open_checked`): a real directory (never a
//!   symlink), owned by the running uid, not group- or world-writable, and the same for its
//!   parent unless the parent is owned by root and not sticky (and not group/world-writable);
//! - every file below it is opened with `openat(dirfd, name, O_NOFOLLOW ...)` and has to be a
//!   regular file; the open fd is what gets truncated, appended to or synced.
//! A symlink is refused and reported, never followed.
use crate::util::{perr, PErr};
use std::ffi::{c_char, CString};
use std::fs::File;
use std::io;
use std::os::fd::{AsRawFd, FromRawFd};
use std::os::unix::fs::{FileTypeExt, MetadataExt};

extern "C" {
    fn openat(dirfd: i32, path: *const c_char, flags: i32, ...) -> i32;
    fn mkdirat(dirfd: i32, path: *const c_char, mode: u32) -> i32;
    fn unlinkat(dirfd: i32, path: *const c_char, flags: i32) -> i32;
    fn renameat(old_dirfd: i32, old: *const c_char, new_dirfd: i32, new: *const c_char) -> i32;
    fn geteuid() -> u32;
    fn fstatfs(fd: i32, buf: *mut u64) -> i32;
    fn umask(mask: u32) -> u32;
}

pub const O_RDONLY: i32 = 0;
pub const O_WRONLY: i32 = 1;
pub const O_RDWR: i32 = 2;
pub const O_CREAT: i32 = 0o100;
pub const O_EXCL: i32 = 0o200;
pub const O_TRUNC: i32 = 0o1000;
pub const O_APPEND: i32 = 0o2000;
const O_NONBLOCK: i32 = 0o4000;
const O_CLOEXEC: i32 = 0o2000000;
#[cfg(any(target_arch = "aarch64", target_arch = "arm"))]
const O_DIRECTORY: i32 = 0o40000;
#[cfg(any(target_arch = "aarch64", target_arch = "arm"))]
const O_NOFOLLOW: i32 = 0o100000;
#[cfg(not(any(target_arch = "aarch64", target_arch = "arm")))]
const O_DIRECTORY: i32 = 0o200000;
#[cfg(not(any(target_arch = "aarch64", target_arch = "arm")))]
const O_NOFOLLOW: i32 = 0o400000;
const AT_FDCWD: i32 = -100;

const ENOENT: i32 = 2;
const ENOTDIR: i32 = 20;
const ELOOP: i32 = 40;
const TMPFS_MAGIC: u64 = 0x0102_1994;

pub fn euid() -> u32 {
    unsafe { geteuid() }
}

fn errno(e: &io::Error) -> i32 {
    e.raw_os_error().unwrap_or(0)
}

fn cstr(s: &str) -> io::Result<CString> {
    CString::new(s).map_err(|_| io::Error::new(io::ErrorKind::InvalidInput, "NUL in path"))
}

fn is_name(n: &str) -> bool {
    !n.is_empty() && n != "." && n != ".." && !n.contains('/')
}

/// An open, validated directory. Files are only ever opened relative to it.
#[derive(Debug)]
pub struct Dir {
    f: File,
    pub path: String,
}

/// Kind of a directory entry, without following links.
#[derive(Debug, PartialEq, Clone, Copy)]
pub enum Kind {
    File,
    Dir,
    Symlink,
    Other,
}

impl Dir {
    /// Open a directory by path; the last component must not be a symlink. No ownership or
    /// mode check (readers of already validated directories use this).
    pub fn open_plain(path: &str) -> io::Result<Dir> {
        let c = cstr(path)?;
        let fd = unsafe { openat(AT_FDCWD, c.as_ptr(), O_RDONLY | O_DIRECTORY | O_NOFOLLOW | O_CLOEXEC) };
        if fd < 0 {
            return Err(io::Error::last_os_error());
        }
        Ok(Dir { f: unsafe { File::from_raw_fd(fd) }, path: path.to_string() })
    }

    fn open_dir_at(&self, name: &str) -> io::Result<Dir> {
        if !is_name(name) {
            return Err(io::Error::new(io::ErrorKind::InvalidInput, "bad directory name"));
        }
        let c = cstr(name)?;
        let fd = unsafe { openat(self.f.as_raw_fd(), c.as_ptr(), O_RDONLY | O_DIRECTORY | O_NOFOLLOW | O_CLOEXEC) };
        if fd < 0 {
            return Err(io::Error::last_os_error());
        }
        Ok(Dir { f: unsafe { File::from_raw_fd(fd) }, path: format!("{}/{name}", self.path.trim_end_matches('/')) })
    }

    fn mkdir(&self, name: &str, mode: u32) -> io::Result<()> {
        let c = cstr(name)?;
        if unsafe { mkdirat(self.f.as_raw_fd(), c.as_ptr(), mode) } < 0 {
            return Err(io::Error::last_os_error());
        }
        Ok(())
    }

    /// Open (or create, with `O_CREAT`) one file of this directory. The name is a single path
    /// component, a symlink is never followed (ELOOP), and the result has to be a regular file
    /// (a FIFO or device planted under a data name is refused, and `O_NONBLOCK` keeps even that
    /// open from blocking).
    pub fn open_file(&self, name: &str, flags: i32, mode: u32) -> io::Result<File> {
        if !is_name(name) {
            return Err(io::Error::new(io::ErrorKind::InvalidInput, "bad file name"));
        }
        let c = cstr(name)?;
        let fd = unsafe { openat(self.f.as_raw_fd(), c.as_ptr(), flags | O_NOFOLLOW | O_CLOEXEC | O_NONBLOCK, mode) };
        if fd < 0 {
            return Err(io::Error::last_os_error());
        }
        let f = unsafe { File::from_raw_fd(fd) };
        if !f.metadata()?.is_file() {
            return Err(io::Error::new(io::ErrorKind::InvalidInput, format!("{}/{name} is not a regular file", self.path)));
        }
        Ok(f)
    }

    pub fn len_of(&self, name: &str) -> io::Result<u64> {
        Ok(self.open_file(name, O_RDONLY, 0)?.metadata()?.len())
    }

    /// Whole small file (dictionaries); at most `max` bytes.
    pub fn read_all(&self, name: &str, max: u64) -> io::Result<Vec<u8>> {
        use std::io::Read;
        let f = self.open_file(name, O_RDONLY, 0)?;
        let mut b = Vec::new();
        f.take(max + 1).read_to_end(&mut b)?;
        if b.len() as u64 > max {
            return Err(io::Error::new(io::ErrorKind::InvalidData, format!("{name} is larger than {max} bytes")));
        }
        Ok(b)
    }

    /// Remove one name (a symlink is removed itself, never its target).
    pub fn unlink(&self, name: &str) -> io::Result<()> {
        if !is_name(name) {
            return Err(io::Error::new(io::ErrorKind::InvalidInput, "bad file name"));
        }
        let c = cstr(name)?;
        if unsafe { unlinkat(self.f.as_raw_fd(), c.as_ptr(), 0) } < 0 {
            return Err(io::Error::last_os_error());
        }
        Ok(())
    }

    pub fn rename(&self, from: &str, to: &str) -> io::Result<()> {
        if !is_name(from) || !is_name(to) {
            return Err(io::Error::new(io::ErrorKind::InvalidInput, "bad file name"));
        }
        let (a, b) = (cstr(from)?, cstr(to)?);
        let fd = self.f.as_raw_fd();
        if unsafe { renameat(fd, a.as_ptr(), fd, b.as_ptr()) } < 0 {
            return Err(io::Error::last_os_error());
        }
        Ok(())
    }

    /// fsync of the directory itself (new names and renames become durable).
    pub fn sync(&self) {
        let _ = self.f.sync_all();
    }

    /// Entries with their kind (no link is followed). Listed through the open directory fd.
    pub fn list(&self) -> io::Result<Vec<(String, Kind)>> {
        let rd = std::fs::read_dir(format!("/proc/self/fd/{}", self.f.as_raw_fd())).or_else(|_| std::fs::read_dir(&self.path))?;
        let mut v = Vec::new();
        for e in rd.flatten() {
            let n = e.file_name().to_string_lossy().into_owned();
            let k = match e.file_type() {
                Ok(t) if t.is_symlink() => Kind::Symlink,
                Ok(t) if t.is_dir() => Kind::Dir,
                Ok(t) if t.is_file() => Kind::File,
                _ => Kind::Other,
            };
            v.push((n, k));
        }
        Ok(v)
    }

    /// Length of a listed regular file (lstat semantics: never through a link).
    pub fn entry_len(&self, name: &str) -> u64 {
        std::fs::symlink_metadata(format!("/proc/self/fd/{}/{name}", self.f.as_raw_fd()))
            .or_else(|_| std::fs::symlink_metadata(format!("{}/{name}", self.path)))
            .map(|m| if m.file_type().is_file() { m.len() } else { 0 })
            .unwrap_or(0)
    }

    /// Filesystem type magic of this directory (`fstatfs`), `None` when the call fails.
    pub fn fs_magic(&self) -> Option<u64> {
        let mut buf = [0u64; 24]; // struct statfs is 120 bytes on the 64-bit Linux targets; f_type is first
        if unsafe { fstatfs(self.f.as_raw_fd(), buf.as_mut_ptr()) } != 0 {
            return None;
        }
        Some(buf[0])
    }

}

/// Read a small file below `dir_path` without following links (dictionary loading).
pub fn read_small(dir_path: &str, name: &str, max: u64) -> io::Result<Vec<u8>> {
    Dir::open_plain(dir_path)?.read_all(name, max)
}

/// Create/truncate one file (`O_NOFOLLOW`) below the parent of `path` and write it. Used for the
/// optional benchmark summary only.
pub fn write_file(path: &str, bytes: &[u8]) -> io::Result<()> {
    use std::io::Write;
    let p = std::path::Path::new(path);
    let name = p.file_name().and_then(|n| n.to_str()).ok_or_else(|| io::Error::new(io::ErrorKind::InvalidInput, "no file name"))?;
    let parent = match p.parent() {
        Some(x) if !x.as_os_str().is_empty() => x.to_string_lossy().into_owned(),
        _ => ".".into(),
    };
    let d = Dir::open_plain(&parent)?;
    let mut f = d.open_file(name, O_WRONLY | O_CREAT | O_TRUNC, 0o600)?;
    f.write_all(bytes)
}

fn classify(role: &str, path: &str, what: &str, e: io::Error) -> PErr {
    let is_link = std::fs::symlink_metadata(path).map(|m| m.file_type().is_symlink()).unwrap_or(false);
    let en = errno(&e);
    if is_link || en == ELOOP {
        return perr("dir_symlink", format!("{role}: {what} {path} is a symlink; symlinks are never followed"));
    }
    match en {
        ENOENT => perr("dir_missing", format!("{role}: {what} {path} does not exist (create it, root-owned, mode 0700)")),
        ENOTDIR => perr("dir_not_directory", format!("{role}: {what} {path} is not a directory")),
        _ => perr("dir_io", format!("{role}: {what} {path}: {e}")),
    }
}

/// Validate a directory's owner and mode. `parent` selects the rule for the parent of a data
/// directory: it may also be owned by root when it is not sticky (a system directory such as
/// /run or /var/lib), but is never accepted when group- or world-writable.
fn check_meta(md: &std::fs::Metadata, role: &str, path: &str, parent: bool) -> Result<(), PErr> {
    let me = euid();
    let mode = md.mode();
    let (code, what) = if parent { ("dir_parent_unsafe", "parent directory of") } else { ("dir_unsafe", "directory") };
    let owner_ok = md.uid() == me || (parent && md.uid() == 0 && mode & 0o1000 == 0);
    if !owner_ok {
        return Err(perr(code, format!("{role}: {what} {path} is owned by uid {} (running as uid {me}{})", md.uid(), if parent { "; a root-owned parent must not be sticky" } else { "" })));
    }
    if mode & 0o022 != 0 {
        return Err(perr(code, format!("{role}: {what} {path} is group- or world-writable (mode {:04o}); use mode 0700", mode & 0o7777)));
    }
    Ok(())
}

/// Open and validate one data directory; a missing leaf is created with mode 0700 (its parent
/// must exist). Returns a typed refusal (`PErr.code`) when it is not safe to use.
pub fn open_checked(path: &str, role: &str) -> Result<Dir, PErr> {
    let p = std::path::Path::new(path);
    let name = p
        .file_name()
        .and_then(|n| n.to_str())
        .ok_or_else(|| perr("dir_path_invalid", format!("{role}: {path} has no final directory name")))?;
    let parent_path = match p.parent() {
        Some(x) if !x.as_os_str().is_empty() => x.to_string_lossy().into_owned(),
        _ => ".".into(),
    };
    let parent = Dir::open_plain(&parent_path).map_err(|e| classify(role, &parent_path, "parent directory", e))?;
    let pmd = parent.f.metadata().map_err(|e| perr("dir_io", format!("{role}: {parent_path}: {e}")))?;
    check_meta(&pmd, role, &parent_path, true)?;
    let dir = match parent.open_dir_at(name) {
        Ok(d) => d,
        Err(e) if errno(&e) == ENOENT => {
            parent.mkdir(name, 0o700).map_err(|e| perr("dir_io", format!("{role}: cannot create {path}: {e}")))?;
            parent.open_dir_at(name).map_err(|e| classify(role, path, "directory", e))?
        }
        Err(e) => return Err(classify(role, path, "directory", e)),
    };
    let md = dir.f.metadata().map_err(|e| perr("dir_io", format!("{role}: {path}: {e}")))?;
    check_meta(&md, role, path, false)?;
    let mut d = dir;
    d.path = path.to_string();
    Ok(d)
}

/// What `check_tmpfs` found.
#[derive(Debug, Clone, PartialEq)]
pub struct TmpfsCheck {
    pub is_tmpfs: bool,
    pub detail: String,
}

/// X-03: the hot and warm tiers write a frame per second; on a disk-backed directory that is
/// constant write traffic on the host the tool is meant to observe. `require` makes it a refusal.
pub fn check_tmpfs(dir: &Dir, require: bool) -> Result<TmpfsCheck, PErr> {
    let magic = dir.fs_magic();
    let is = magic == Some(TMPFS_MAGIC);
    let detail = match magic {
        Some(m) if is => format!("{} is tmpfs (statfs type {m:#x})", dir.path),
        Some(m) => format!("{} is NOT tmpfs (statfs type {m:#x}): the hot and warm tiers would write to a disk-backed filesystem", dir.path),
        None => format!("{}: statfs failed, cannot tell whether it is tmpfs", dir.path),
    };
    if !is && require {
        return Err(perr("tmpfs_required", format!("store.tmpfs_dir {detail}; set store.require_tmpfs = false to run anyway")));
    }
    Ok(TmpfsCheck { is_tmpfs: is, detail })
}

/// Lock file and socket directory of one running sampler (X-02): taken before any file of the
/// store is opened, truncated or deleted.
#[derive(Debug)]
pub struct Instance {
    /// Held (never read) for as long as the instance lives: the flock goes with the descriptor.
    #[allow(dead_code)]
    pub lock: File,
    pub dir: Dir,
    pub sock_name: String,
    pub sock_path: String,
}

impl Instance {
    pub fn acquire(socket_path: &str) -> Result<Instance, PErr> {
        let p = std::path::Path::new(socket_path);
        let name = p
            .file_name()
            .and_then(|n| n.to_str())
            .ok_or_else(|| perr("socket_path_invalid", format!("sampler.socket_path {socket_path} has no file name")))?
            .to_string();
        let sdir = match p.parent() {
            Some(x) if !x.as_os_str().is_empty() => x.to_string_lossy().into_owned(),
            _ => ".".into(),
        };
        let dir = open_checked(&sdir, "sampler.socket_path directory")?;
        let lock = dir
            .open_file(&format!("{name}.lock"), O_WRONLY | O_CREAT, 0o600)
            .map_err(|e| perr("lock_io", format!("{socket_path}.lock: {e}")))?;
        if !crate::util::try_flock(&lock) {
            return Err(perr("instance_running", format!("{socket_path}.lock is held: another topos-sampler is running; nothing was changed")));
        }
        Ok(Instance { lock, dir, sock_name: name, sock_path: socket_path.to_string() })
    }
}

/// Probe for tests: the mode the socket had right after `bind` and before the chmod.
#[cfg(test)]
pub static BIND_PROBE: std::sync::Mutex<Vec<(String, u32)>> = std::sync::Mutex::new(Vec::new());

/// Bind a unix socket so that it is never reachable with a looser mode than 0600 during bind
/// (X-08): the umask is tightened around `bind`, then the configured `mode` (0600 or 0660) is
/// set and verified. The group stays the process's effective group.
pub fn bind_private(inst: &Instance, mode: u32) -> io::Result<std::os::unix::net::UnixListener> {
    use std::os::unix::fs::PermissionsExt;
    match inst.dir.unlink(&inst.sock_name) {
        Ok(()) => {}
        Err(e) if errno(&e) == ENOENT => {}
        Err(e) => return Err(e),
    }
    let old = unsafe { umask(0o177) };
    let l = std::os::unix::net::UnixListener::bind(&inst.sock_path);
    unsafe { umask(old) };
    let l = l?;
    #[cfg(test)]
    {
        if let Ok(m) = std::fs::symlink_metadata(&inst.sock_path) {
            BIND_PROBE.lock().unwrap_or_else(|e| e.into_inner()).push((inst.sock_path.clone(), m.mode() & 0o777));
        }
    }
    // A socket that could not be set to, or verified at, the wanted mode is not left behind.
    let fin = || -> io::Result<()> {
        std::fs::set_permissions(&inst.sock_path, std::fs::Permissions::from_mode(mode))?;
        let md = std::fs::symlink_metadata(&inst.sock_path)?;
        if !md.file_type().is_socket() || md.uid() != euid() || md.mode() & 0o777 != mode {
            return Err(io::Error::new(io::ErrorKind::PermissionDenied, format!("{} is not a socket owned by the sampler with mode {mode:04o}", inst.sock_path)));
        }
        Ok(())
    };
    if let Err(e) = fin() {
        let _ = inst.dir.unlink(&inst.sock_name);
        return Err(e);
    }
    Ok(l)
}
