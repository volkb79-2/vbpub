package store

import (
	"io/fs"
	"os"
	"path/filepath"
	"reflect"
	"strings"
	"testing"
	"time"
)

// --- O4: the manifest is per-file and content-addressed -------------------

func TestManifestIsIdenticalForByteIdenticalContent(t *testing.T) {
	p := testProfile(t)
	a, b := t.TempDir(), t.TempDir()
	writeTree(t, a, contentA)
	writeTree(t, b, contentA)

	ma, err := BuildManifest(a, p)
	if err != nil {
		t.Fatal(err)
	}
	mb, err := BuildManifest(b, p)
	if err != nil {
		t.Fatal(err)
	}

	if !reflect.DeepEqual(ma, mb) {
		t.Fatalf("two byte-identical trees produced different manifests:\n%+v\n%+v", ma, mb)
	}
	if ma.ContentDigest == "" || !strings.HasPrefix(ma.ContentDigest, HashName+":") {
		t.Fatalf("content digest %q is not a %s digest", ma.ContentDigest, HashName)
	}
}

// The negative for "keyed on mtime": touching a file changes nothing the
// manifest records.
func TestManifestIgnoresModificationTime(t *testing.T) {
	p := testProfile(t)
	dir := t.TempDir()
	writeTree(t, dir, contentA)

	before, err := BuildManifest(dir, p)
	if err != nil {
		t.Fatal(err)
	}

	target := filepath.Join(dir, "WS", "Content", "Paks", "game.pak")
	// A fixed, far-past timestamp: the assertion is about the manifest, not
	// about what time it is.
	stamp := time.Date(2001, 2, 3, 4, 5, 6, 0, time.UTC)
	if err := os.Chtimes(target, stamp, stamp); err != nil {
		t.Fatal(err)
	}

	after, err := BuildManifest(dir, p)
	if err != nil {
		t.Fatal(err)
	}
	if !reflect.DeepEqual(before, after) {
		t.Fatal("changing a file's mtime changed the manifest; it is keyed on time, not content")
	}
}

// The negative for "keyed on size": a same-size byte flip must change
// exactly one entry, and it must be that entry's hash.
func TestManifestFlippingOneByteChangesExactlyOneEntry(t *testing.T) {
	p := testProfile(t)
	dir := t.TempDir()
	writeTree(t, dir, contentA)

	before, err := BuildManifest(dir, p)
	if err != nil {
		t.Fatal(err)
	}

	target := filepath.Join(dir, "WS", "Content", "Paks", "game.pak")
	body, err := os.ReadFile(target)
	if err != nil {
		t.Fatal(err)
	}
	body[0] ^= 0xff
	if err := os.WriteFile(target, body, 0o644); err != nil {
		t.Fatal(err)
	}

	after, err := BuildManifest(dir, p)
	if err != nil {
		t.Fatal(err)
	}
	if len(before.Entries) != len(after.Entries) {
		t.Fatalf("entry count changed: %d -> %d", len(before.Entries), len(after.Entries))
	}

	var changed []string
	for i := range before.Entries {
		if !reflect.DeepEqual(before.Entries[i], after.Entries[i]) {
			changed = append(changed, before.Entries[i].Path)
		}
	}
	if len(changed) != 1 {
		t.Fatalf("a one-byte change touched %d entries: %v", len(changed), changed)
	}
	if changed[0] != "WS/Content/Paks/game.pak" {
		t.Fatalf("the wrong entry changed: %q", changed[0])
	}

	var bi, ai Entry
	for _, e := range before.Entries {
		if e.Path == changed[0] {
			bi = e
		}
	}
	for _, e := range after.Entries {
		if e.Path == changed[0] {
			ai = e
		}
	}
	if bi.Size != ai.Size {
		t.Fatalf("test setup: the flip changed the size (%d -> %d)", bi.Size, ai.Size)
	}
	if bi.SHA256 == ai.SHA256 {
		t.Fatal("the entry's hash did not change; the manifest is not content-addressed")
	}
	if before.ContentDigest == after.ContentDigest {
		t.Fatal("the content digest did not change for changed content")
	}
}

// --- what a manifest may describe -----------------------------------------

func TestManifestRecordsSymlinksAsLinks(t *testing.T) {
	p := testProfile(t)
	dir := t.TempDir()
	writeTree(t, dir, contentA)
	if err := os.Symlink("libengine.so", filepath.Join(dir, "Engine", "libengine.so.1")); err != nil {
		t.Fatal(err)
	}

	m, err := BuildManifest(dir, p)
	if err != nil {
		t.Fatal(err)
	}
	for _, e := range m.Entries {
		if e.Path != "Engine/libengine.so.1" {
			continue
		}
		if e.Type != EntrySymlink {
			t.Fatalf("symlink recorded as %q", e.Type)
		}
		if e.Target != "libengine.so" {
			t.Fatalf("symlink target recorded as %q", e.Target)
		}
		if e.SHA256 != "" {
			t.Fatal("a symlink must not carry a content hash; it has no content of its own")
		}
		return
	}
	t.Fatal("the symlink is missing from the manifest")
}

func TestManifestRecordsSetuidBits(t *testing.T) {
	p := testProfile(t)
	dir := t.TempDir()
	writeTree(t, dir, contentA)

	target := filepath.Join(dir, "WS", "Binaries", "server")
	if err := os.Chmod(target, 0o755|fs.ModeSetuid); err != nil {
		t.Fatal(err)
	}

	m, err := BuildManifest(dir, p)
	if err != nil {
		t.Fatal(err)
	}
	for _, e := range m.Entries {
		if e.Path != "WS/Binaries/server" {
			continue
		}
		// Go keeps setuid outside Perm(), so a manifest built from Perm()
		// alone would be blind to exactly the bit that matters most.
		if e.Mode != "04755" {
			t.Fatalf("setuid file recorded with mode %q, want 04755", e.Mode)
		}
		return
	}
	t.Fatal("the file is missing from the manifest")
}

func TestManifestRefusesIrregularFiles(t *testing.T) {
	p := testProfile(t)
	dir := t.TempDir()
	writeTree(t, dir, contentA)

	fifo := filepath.Join(dir, "Engine", "pipe")
	if err := makeFifo(fifo); err != nil {
		t.Skipf("cannot create a fifo here: %v", err)
	}

	_, err := BuildManifest(dir, p)
	if err == nil {
		t.Fatal("a fifo was accepted into a release manifest")
	}
	if !strings.Contains(err.Error(), "named pipe") {
		t.Fatalf("the error does not say what it found: %v", err)
	}
}

// --- RefreshModes ---------------------------------------------------------

func TestRefreshModesPicksUpAModeChangeAndRedigests(t *testing.T) {
	p := testProfile(t)
	dir := t.TempDir()
	writeTree(t, dir, contentA)

	m, err := BuildManifest(dir, p)
	if err != nil {
		t.Fatal(err)
	}
	before := m.ContentDigest

	if err := os.Chmod(filepath.Join(dir, "WS", "Binaries", "server"), 0o700); err != nil {
		t.Fatal(err)
	}
	if err := m.RefreshModes(dir); err != nil {
		t.Fatal(err)
	}
	if m.ContentDigest == before {
		t.Fatal("the digest did not change after a recorded mode changed")
	}
	if err := m.Verify(dir); err != nil {
		t.Fatalf("a refreshed manifest does not verify against the tree it was refreshed from: %v", err)
	}
}
