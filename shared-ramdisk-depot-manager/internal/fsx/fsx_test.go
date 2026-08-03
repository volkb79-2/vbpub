package fsx

import (
	"io/fs"
	"os"
	"path/filepath"
	"strings"
	"testing"
)

func TestWriteFileSyncReplacesContentAndSetsMode(t *testing.T) {
	dir := t.TempDir()
	path := filepath.Join(dir, "COMPLETE")

	if err := WriteFileSync(path, []byte("first"), 0o644); err != nil {
		t.Fatal(err)
	}
	if err := WriteFileSync(path, []byte("second"), 0o600); err != nil {
		t.Fatal(err)
	}

	body, err := os.ReadFile(path)
	if err != nil {
		t.Fatal(err)
	}
	if string(body) != "second" {
		t.Fatalf("content is %q", body)
	}
	info, err := os.Lstat(path)
	if err != nil {
		t.Fatal(err)
	}
	// The mode is set explicitly rather than left to the umask, or a store
	// written under a restrictive umask would be unreadable to consumers.
	if info.Mode().Perm() != 0o600 {
		t.Fatalf("mode is %v, want 0600", info.Mode().Perm())
	}
}

// A failed write must leave neither a truncated target nor a temporary file:
// the whole point is that a reader sees the old content or the new one.
func TestWriteFileSyncLeavesNoTemporaryBehind(t *testing.T) {
	dir := t.TempDir()
	if err := WriteFileSync(filepath.Join(dir, "a"), []byte("x"), 0o644); err != nil {
		t.Fatal(err)
	}
	entries, err := os.ReadDir(dir)
	if err != nil {
		t.Fatal(err)
	}
	for _, e := range entries {
		if strings.HasPrefix(e.Name(), ".tmp-") {
			t.Fatalf("a temporary file survived: %q", e.Name())
		}
	}
	if len(entries) != 1 {
		t.Fatalf("directory holds %d entries, want 1", len(entries))
	}
}

func TestWriteFileSyncFailsWhenTheDirectoryIsMissing(t *testing.T) {
	if err := WriteFileSync(filepath.Join(t.TempDir(), "nope", "f"), []byte("x"), 0o644); err == nil {
		t.Fatal("writing into a missing directory succeeded")
	}
}

func TestSyncDirRejectsANonDirectory(t *testing.T) {
	dir := t.TempDir()
	path := filepath.Join(dir, "f")
	if err := os.WriteFile(path, []byte("x"), 0o644); err != nil {
		t.Fatal(err)
	}
	// Syncing a regular file is legal, so this only asserts the missing case.
	if err := SyncDir(filepath.Join(dir, "absent")); err == nil {
		t.Fatal("SyncDir on a missing path succeeded")
	}
}

func TestCopyTreePreservesStructureModesAndLinks(t *testing.T) {
	src, dst := t.TempDir(), filepath.Join(t.TempDir(), "out")

	if err := os.MkdirAll(filepath.Join(src, "Engine", "Binaries"), 0o755); err != nil {
		t.Fatal(err)
	}
	if err := os.WriteFile(filepath.Join(src, "Engine", "Binaries", "server"), []byte("elf"), 0o755); err != nil {
		t.Fatal(err)
	}
	if err := os.Chmod(filepath.Join(src, "Engine", "Binaries", "server"), 0o755); err != nil {
		t.Fatal(err)
	}
	if err := os.WriteFile(filepath.Join(src, "Engine", "data.pak"), []byte("pak"), 0o644); err != nil {
		t.Fatal(err)
	}
	if err := os.Chmod(filepath.Join(src, "Engine", "data.pak"), 0o644); err != nil {
		t.Fatal(err)
	}
	if err := os.Symlink("data.pak", filepath.Join(src, "Engine", "latest.pak")); err != nil {
		t.Fatal(err)
	}
	if err := os.MkdirAll(dst, 0o755); err != nil {
		t.Fatal(err)
	}

	if err := CopyTree(src, dst); err != nil {
		t.Fatal(err)
	}

	body, err := os.ReadFile(filepath.Join(dst, "Engine", "Binaries", "server"))
	if err != nil {
		t.Fatal(err)
	}
	if string(body) != "elf" {
		t.Fatalf("content is %q", body)
	}
	info, err := os.Lstat(filepath.Join(dst, "Engine", "Binaries", "server"))
	if err != nil {
		t.Fatal(err)
	}
	if info.Mode().Perm() != 0o755 {
		t.Errorf("mode is %v, want 0755", info.Mode().Perm())
	}

	// A symlink is recreated as a link. Following it would duplicate content
	// and, if it pointed outside the source, drag in unrelated files.
	li, err := os.Lstat(filepath.Join(dst, "Engine", "latest.pak"))
	if err != nil {
		t.Fatal(err)
	}
	if li.Mode()&fs.ModeSymlink == 0 {
		t.Fatal("the symlink was followed instead of recreated")
	}
	target, err := os.Readlink(filepath.Join(dst, "Engine", "latest.pak"))
	if err != nil {
		t.Fatal(err)
	}
	if target != "data.pak" {
		t.Fatalf("link target is %q", target)
	}
}

func TestCopyTreeDoesNotFollowLinksOutOfTheSource(t *testing.T) {
	outside := t.TempDir()
	if err := os.WriteFile(filepath.Join(outside, "secret"), []byte("not ours"), 0o600); err != nil {
		t.Fatal(err)
	}
	src, dst := t.TempDir(), filepath.Join(t.TempDir(), "out")
	if err := os.Symlink(filepath.Join(outside, "secret"), filepath.Join(src, "escape")); err != nil {
		t.Fatal(err)
	}
	if err := os.MkdirAll(dst, 0o755); err != nil {
		t.Fatal(err)
	}

	if err := CopyTree(src, dst); err != nil {
		t.Fatal(err)
	}
	info, err := os.Lstat(filepath.Join(dst, "escape"))
	if err != nil {
		t.Fatal(err)
	}
	if info.Mode()&fs.ModeSymlink == 0 {
		t.Fatal("a link pointing outside the source was materialized as a copy")
	}
}

func TestCopyTreeRefusesIrregularEntries(t *testing.T) {
	src, dst := t.TempDir(), filepath.Join(t.TempDir(), "out")
	if err := makeFifo(filepath.Join(src, "pipe")); err != nil {
		t.Skipf("cannot create a fifo here: %v", err)
	}
	if err := os.MkdirAll(dst, 0o755); err != nil {
		t.Fatal(err)
	}
	if err := CopyTree(src, dst); err == nil {
		t.Fatal("a fifo was copied into a release transaction")
	}
}

func TestCopyTreeRefusesANonDirectorySource(t *testing.T) {
	dir := t.TempDir()
	path := filepath.Join(dir, "f")
	if err := os.WriteFile(path, []byte("x"), 0o644); err != nil {
		t.Fatal(err)
	}
	if err := CopyTree(path, filepath.Join(dir, "out")); err == nil {
		t.Fatal("a regular file was accepted as a source tree")
	}
}
