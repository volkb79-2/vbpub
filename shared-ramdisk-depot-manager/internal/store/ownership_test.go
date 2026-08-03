package store

import (
	"io/fs"
	"os"
	"path/filepath"
	"testing"

	"srdm/internal/config"
)

// needsChown carries a measured, non-obvious property: a root chown with
// identical uid and gid is not a no-op — it strips setuid, setgid and file
// capabilities from non-directories. A naive "skip when the ids match"
// therefore removes hardening silently, so the predicate has to be narrower.
func TestNeedsChownPredicate(t *testing.T) {
	dir := t.TempDir()

	plain := filepath.Join(dir, "plain")
	if err := os.WriteFile(plain, []byte("x"), 0o644); err != nil {
		t.Fatal(err)
	}
	setuid := filepath.Join(dir, "setuid")
	if err := os.WriteFile(setuid, []byte("x"), 0o644); err != nil {
		t.Fatal(err)
	}
	if err := os.Chmod(setuid, 0o755|fs.ModeSetuid); err != nil {
		t.Fatal(err)
	}
	setgid := filepath.Join(dir, "setgid")
	if err := os.WriteFile(setgid, []byte("x"), 0o644); err != nil {
		t.Fatal(err)
	}
	if err := os.Chmod(setgid, 0o755|fs.ModeSetgid); err != nil {
		t.Fatal(err)
	}
	setgidDir := filepath.Join(dir, "setgid-dir")
	if err := os.Mkdir(setgidDir, 0o755); err != nil {
		t.Fatal(err)
	}
	if err := os.Chmod(setgidDir, 0o755|fs.ModeSetgid); err != nil {
		t.Fatal(err)
	}

	self := os.Getuid()
	group := os.Getgid()

	cases := []struct {
		name       string
		path       string
		uid, gid   int
		wantChown  bool
		wantReason string
	}{
		{
			name: "already correct plain file", path: plain, uid: self, gid: group,
			wantChown: false,
			wantReason: "a chown that changes nothing is skipped: it costs a syscall per " +
				"entry and buys nothing",
		},
		{
			name: "wrong uid", path: plain, uid: self + 1, gid: group,
			wantChown: true, wantReason: "ownership actually differs",
		},
		{
			name: "wrong gid", path: plain, uid: self, gid: group + 1,
			wantChown: true, wantReason: "ownership actually differs",
		},
		{
			name: "already correct setuid file", path: setuid, uid: self, gid: group,
			wantChown: true,
			wantReason: "skipping here would preserve a setuid bit that an unconditional " +
				"walk strips; the store must not quietly become more permissive than the " +
				"behaviour it replaces",
		},
		{
			name: "already correct setgid file", path: setgid, uid: self, gid: group,
			wantChown: true, wantReason: "same as setuid",
		},
		{
			name: "already correct setgid directory", path: setgidDir, uid: self, gid: group,
			wantChown: false,
			wantReason: "a directory keeps its setgid across a chown, so there is nothing " +
				"to preserve and nothing to do",
		},
	}

	for _, tc := range cases {
		t.Run(tc.name, func(t *testing.T) {
			info, err := os.Lstat(tc.path)
			if err != nil {
				t.Fatal(err)
			}
			if got := needsChown(info, tc.uid, tc.gid); got != tc.wantChown {
				t.Fatalf("needsChown = %v, want %v — %s", got, tc.wantChown, tc.wantReason)
			}
		})
	}
}

// The claim the predicate rests on, verified on this kernel rather than
// recalled: a directory keeps setgid across a chown and a file does not.
// If a future kernel changed that, this test says so instead of the store
// silently doing the wrong thing.
func TestChownStripsSetuidOnFilesButNotDirectories(t *testing.T) {
	if os.Getuid() != 0 {
		t.Skip("changing ownership requires root; the predicate itself is covered above")
	}
	dir := t.TempDir()

	file := filepath.Join(dir, "f")
	if err := os.WriteFile(file, []byte("x"), 0o644); err != nil {
		t.Fatal(err)
	}
	if err := os.Chmod(file, 0o755|fs.ModeSetuid); err != nil {
		t.Fatal(err)
	}
	sub := filepath.Join(dir, "d")
	if err := os.Mkdir(sub, 0o755); err != nil {
		t.Fatal(err)
	}
	if err := os.Chmod(sub, 0o755|fs.ModeSetgid); err != nil {
		t.Fatal(err)
	}

	// Chown to the owner it already has.
	for _, p := range []string{file, sub} {
		info, err := os.Lstat(p)
		if err != nil {
			t.Fatal(err)
		}
		uid, gid, ok := ownerOf(info)
		if !ok {
			t.Skip("this platform does not report ownership")
		}
		if err := os.Lchown(p, uid, gid); err != nil {
			t.Fatal(err)
		}
	}

	fi, err := os.Lstat(file)
	if err != nil {
		t.Fatal(err)
	}
	if fi.Mode()&fs.ModeSetuid != 0 {
		t.Error("this kernel kept setuid on a file across an identical chown; " +
			"needsChown's narrow predicate is built on the opposite behaviour")
	}
	di, err := os.Lstat(sub)
	if err != nil {
		t.Fatal(err)
	}
	if di.Mode()&fs.ModeSetgid == 0 {
		t.Error("this kernel stripped setgid from a directory across an identical chown")
	}
}

func TestNormalizeOwnershipAppliesModePolicyAndSkipsCorrectEntries(t *testing.T) {
	dir := t.TempDir()
	if err := os.MkdirAll(filepath.Join(dir, "sub"), 0o700); err != nil {
		t.Fatal(err)
	}
	if err := os.WriteFile(filepath.Join(dir, "sub", "f"), []byte("x"), 0o600); err != nil {
		t.Fatal(err)
	}
	if err := os.Symlink("f", filepath.Join(dir, "sub", "link")); err != nil {
		t.Fatal(err)
	}

	var chowned []string
	own := config.Ownership{UID: os.Getuid(), GID: os.Getgid(), DirMode: 0o755, FileMode: 0o644}
	changed, err := normalizeOwnership(dir, own, func(name string, uid, gid int) error {
		chowned = append(chowned, name)
		return nil
	})
	if err != nil {
		t.Fatal(err)
	}
	if changed == 0 {
		t.Fatal("normalization reported no changes over a tree that needed several")
	}

	// Every entry already had the right owner, and none carried setuid or
	// setgid, so nothing should have been chowned at all.
	if len(chowned) != 0 {
		t.Errorf("chowned %v; every entry already had the target ownership", chowned)
	}

	fi, err := os.Lstat(filepath.Join(dir, "sub", "f"))
	if err != nil {
		t.Fatal(err)
	}
	if fi.Mode().Perm() != 0o644 {
		t.Errorf("file mode is %v, want 0644", fi.Mode().Perm())
	}
	di, err := os.Lstat(filepath.Join(dir, "sub"))
	if err != nil {
		t.Fatal(err)
	}
	if di.Mode().Perm() != 0o755 {
		t.Errorf("directory mode is %v, want 0755", di.Mode().Perm())
	}
}

// Mode normalization must not undo the care needsChown takes: a setuid file
// that keeps its ownership keeps its bit.
func TestNormalizeOwnershipPreservesSetuidWhenSettingModes(t *testing.T) {
	dir := t.TempDir()
	target := filepath.Join(dir, "server")
	if err := os.WriteFile(target, []byte("x"), 0o644); err != nil {
		t.Fatal(err)
	}
	if err := os.Chmod(target, 0o700|fs.ModeSetuid); err != nil {
		t.Fatal(err)
	}

	own := config.Ownership{UID: -1, GID: -1, DirMode: 0o755, FileMode: 0o644}
	if _, err := normalizeOwnership(dir, own, func(string, int, int) error { return nil }); err != nil {
		t.Fatal(err)
	}

	fi, err := os.Lstat(target)
	if err != nil {
		t.Fatal(err)
	}
	if fi.Mode()&fs.ModeSetuid == 0 {
		t.Error("the mode policy dropped setuid; it governs permission bits, not those")
	}
	if fi.Mode().Perm() != 0o644 {
		t.Errorf("permission bits are %v, want 0644", fi.Mode().Perm())
	}
}
