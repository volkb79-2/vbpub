//! Small, test-only helpers shared by the module tests.
//!
//! Design §5 (D9): every test works on a temp tree it creates and removes, never
//! on the real `/sys/fs/cgroup` and never on another test's directory. The
//! directory name is unique per process and per call, because nextest runs each
//! test in its own process and several calls inside one test must not collide.

use std::path::{Path, PathBuf};
use std::sync::atomic::{AtomicU64, Ordering};

static COUNTER: AtomicU64 = AtomicU64::new(0);

/// A directory under the OS temp root that removes itself on [`Drop`].
#[derive(Debug)]
pub struct TempTree {
    path: PathBuf,
}

impl TempTree {
    /// Create a fresh, uniquely named temporary directory.
    pub fn new(label: &str) -> Self {
        let serial = COUNTER.fetch_add(1, Ordering::Relaxed);
        let name = format!("thrashguard-{label}-{}-{serial}", std::process::id());
        let path = std::env::temp_dir().join(name);
        std::fs::create_dir_all(&path).expect("create temp tree");
        Self { path }
    }

    /// The directory itself.
    pub fn path(&self) -> &Path {
        &self.path
    }

    /// A path inside the directory (the parent is created; the leaf is not).
    pub fn at(&self, relative: &str) -> PathBuf {
        let path = self.path.join(relative);
        if let Some(parent) = path.parent() {
            std::fs::create_dir_all(parent).expect("create temp parent");
        }
        path
    }

    /// Write a file inside the directory and return its path.
    pub fn write(&self, relative: &str, text: &str) -> PathBuf {
        let path = self.at(relative);
        std::fs::write(&path, text).expect("write fixture");
        path
    }
}

impl Drop for TempTree {
    fn drop(&mut self) {
        let _ = std::fs::remove_dir_all(&self.path);
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn two_trees_never_share_a_path_and_are_removed() {
        let first = TempTree::new("probe");
        let path = first.at("a/b");
        std::fs::write(&path, "x").expect("write");
        assert_eq!(std::fs::read_to_string(&path).expect("read"), "x");
        let second = TempTree::new("probe");
        assert_ne!(first.path(), second.path());
        let kept = first.path().to_path_buf();
        drop(first);
        assert!(!kept.exists());
    }
}
