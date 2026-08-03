package store

import (
	"crypto/sha256"
	"encoding/hex"
	"encoding/json"
	"fmt"
	"io"
	"io/fs"
	"os"
	"path"
	"path/filepath"
	"sort"
	"strings"

	"srdm/internal/profile"
)

// ManifestSchemaVersion is the manifest document version. Versioned from
// day one so a v1 host-bind deployment's store stays readable by the binary
// that will later flip it to provider mode.
const ManifestSchemaVersion = 1

// File names inside a release (and inside the transaction that becomes one).
const (
	// RootDir holds the content itself, so manifest.json and COMPLETE can
	// live beside it without ever appearing in the tree they describe.
	RootDir = "root"
	// ManifestFile is the per-file content-addressed manifest.
	ManifestFile = "manifest.json"
	// CompleteFile is written last and fsync'd. Its presence is the only
	// signal that a release is whole.
	CompleteFile = "COMPLETE"
)

// EntryType is the kind of a manifest entry.
type EntryType string

const (
	EntryFile    EntryType = "file"
	EntryDir     EntryType = "dir"
	EntrySymlink EntryType = "symlink"
)

// Entry describes one path in a release.
//
// There is deliberately no mtime here, and the content digest below is
// keyed on the per-file SHA-256 rather than on size: a manifest keyed on
// size or timestamps cannot tell a rewritten file from an untouched one
// (oracle O4).
type Entry struct {
	Path string    `json:"path"`
	Type EntryType `json:"type"`
	// Mode is the traditional four-digit octal Unix mode: setuid, setgid
	// and sticky, then the permission bits.
	Mode string `json:"mode"`
	// Size and SHA256 are set for regular files only.
	Size   int64  `json:"size,omitempty"`
	SHA256 string `json:"sha256,omitempty"`
	// Target is set for symlinks only.
	Target string `json:"target,omitempty"`
	// Class is the profile class that claimed this path.
	Class string `json:"class"`
}

// Manifest is the complete description of a release's content.
type Manifest struct {
	SchemaVersion int `json:"schema_version"`
	// Hash names the digest algorithm, so a future migration is a data
	// change rather than a guess about what the hex strings mean.
	Hash    string  `json:"hash"`
	Profile string  `json:"profile"`
	Entries []Entry `json:"entries"`
	// ContentDigest is a digest over every entry's identity, so two
	// byte-identical transactions produce the same value and any single
	// changed byte changes it.
	ContentDigest string `json:"content_digest"`
}

// HashName is the only digest srdm writes today.
const HashName = "sha256"

// BuildManifest walks root, classifies every path against p, and returns the
// content-addressed manifest.
//
// Classification failure is returned as *profile.UnclassifiedError so the
// caller can journal a refusal rather than a fault.
func BuildManifest(root string, p *profile.Profile) (*Manifest, error) {
	entries := make([]Entry, 0, 256)

	err := filepath.WalkDir(root, func(path string, d fs.DirEntry, err error) error {
		if err != nil {
			return err
		}
		rel, err := filepath.Rel(root, path)
		if err != nil {
			return err
		}
		if rel == "." {
			return nil
		}
		rel = filepath.ToSlash(rel)

		class, err := p.Classify(rel)
		if err != nil {
			return err
		}

		info, err := d.Info()
		if err != nil {
			return err
		}
		if err := checkStructural(rel, class, d.IsDir()); err != nil {
			return err
		}
		e := Entry{Path: rel, Mode: formatMode(info.Mode()), Class: class.Name}

		switch {
		case info.Mode()&fs.ModeSymlink != 0:
			target, err := os.Readlink(path)
			if err != nil {
				return err
			}
			e.Type = EntrySymlink
			e.Target = target
		case d.IsDir():
			e.Type = EntryDir
		case info.Mode().IsRegular():
			sum, err := hashFile(path)
			if err != nil {
				return err
			}
			e.Type = EntryFile
			e.Size = info.Size()
			e.SHA256 = sum
		default:
			// Devices, sockets and fifos have no place in shared, immutable,
			// read-only content, and their semantics under a bind mount into
			// a container are not something to discover in production.
			return fmt.Errorf("store: %q is a %s; releases may contain only "+
				"regular files, directories and symlinks", rel, describeMode(info.Mode()))
		}
		entries = append(entries, e)
		return nil
	})
	if err != nil {
		return nil, err
	}

	sort.Slice(entries, func(i, j int) bool { return entries[i].Path < entries[j].Path })

	m := &Manifest{
		SchemaVersion: ManifestSchemaVersion,
		Hash:          HashName,
		Profile:       p.ID,
		Entries:       entries,
	}
	m.ContentDigest = m.computeDigest()
	return m, nil
}

// computeDigest hashes a canonical rendering of every entry. NUL separators
// keep a path containing the separator from colliding with a different
// entry, and the trailing newline terminates each record.
func (m *Manifest) computeDigest() string {
	h := sha256.New()
	fmt.Fprintf(h, "srdm-manifest-v%d\x00%s\x00%s\n", m.SchemaVersion, m.Hash, m.Profile)
	for _, e := range m.Entries {
		payload := e.SHA256
		if e.Type == EntrySymlink {
			payload = e.Target
		}
		fmt.Fprintf(h, "%s\x00%s\x00%s\x00%s\x00%s\n",
			e.Type, e.Path, e.Mode, e.Class, payload)
	}
	return HashName + ":" + hex.EncodeToString(h.Sum(nil))
}

// RefreshModes re-stats every entry and updates the recorded modes, then
// recomputes the content digest.
//
// Ownership normalization runs after the manifest is built (the master
// plan's order), and it can legitimately change a mode: chowning a
// non-directory strips setuid and setgid. Without this refresh the
// persisted manifest would describe the tree as it was *before*
// normalization, and every later verification would fail on a mode the
// store itself changed. Content hashes cannot change here — chown and chmod
// do not touch bytes — so this re-stats without re-hashing.
func (m *Manifest) RefreshModes(root string) error {
	for i := range m.Entries {
		e := &m.Entries[i]
		info, err := os.Lstat(filepath.Join(root, filepath.FromSlash(e.Path)))
		if err != nil {
			return fmt.Errorf("store: refresh %q: %w", e.Path, err)
		}
		if got := entryTypeOf(info.Mode()); got != e.Type {
			return fmt.Errorf("store: %q changed from %s to %s during promotion", e.Path, e.Type, got)
		}
		e.Mode = formatMode(info.Mode())
	}
	m.ContentDigest = m.computeDigest()
	return nil
}

func entryTypeOf(m fs.FileMode) EntryType {
	switch {
	case m&fs.ModeSymlink != 0:
		return EntrySymlink
	case m.IsDir():
		return EntryDir
	default:
		return EntryFile
	}
}

// Verify re-walks root and asserts it matches the manifest exactly — no
// missing entries, no extra ones, no changed content.
func (m *Manifest) Verify(root string) error {
	if m.Hash != HashName {
		return fmt.Errorf("store: manifest uses unsupported hash %q", m.Hash)
	}
	if got := m.computeDigest(); got != m.ContentDigest {
		return fmt.Errorf("store: manifest content_digest is %s but its entries hash to %s",
			m.ContentDigest, got)
	}

	want := make(map[string]Entry, len(m.Entries))
	for _, e := range m.Entries {
		want[e.Path] = e
	}
	seen := make(map[string]bool, len(want))

	err := filepath.WalkDir(root, func(path string, d fs.DirEntry, err error) error {
		if err != nil {
			return err
		}
		rel, err := filepath.Rel(root, path)
		if err != nil {
			return err
		}
		if rel == "." {
			return nil
		}
		rel = filepath.ToSlash(rel)

		e, ok := want[rel]
		if !ok {
			return fmt.Errorf("store: %q is present but not in the manifest", rel)
		}
		seen[rel] = true

		info, err := d.Info()
		if err != nil {
			return err
		}
		if got := formatMode(info.Mode()); got != e.Mode {
			return fmt.Errorf("store: %q has mode %s, manifest says %s", rel, got, e.Mode)
		}

		switch e.Type {
		case EntrySymlink:
			if info.Mode()&fs.ModeSymlink == 0 {
				return fmt.Errorf("store: %q is not a symlink, manifest says it is", rel)
			}
			target, err := os.Readlink(path)
			if err != nil {
				return err
			}
			if target != e.Target {
				return fmt.Errorf("store: symlink %q points at %q, manifest says %q", rel, target, e.Target)
			}
		case EntryDir:
			if !d.IsDir() {
				return fmt.Errorf("store: %q is not a directory, manifest says it is", rel)
			}
		case EntryFile:
			if !info.Mode().IsRegular() {
				return fmt.Errorf("store: %q is not a regular file, manifest says it is", rel)
			}
			if info.Size() != e.Size {
				return fmt.Errorf("store: %q is %d bytes, manifest says %d", rel, info.Size(), e.Size)
			}
			sum, err := hashFile(path)
			if err != nil {
				return err
			}
			if sum != e.SHA256 {
				return fmt.Errorf("store: %q hashes to %s, manifest says %s", rel, sum, e.SHA256)
			}
		default:
			return fmt.Errorf("store: manifest entry %q has unknown type %q", rel, e.Type)
		}
		return nil
	})
	if err != nil {
		return err
	}

	if len(seen) != len(want) {
		missing := make([]string, 0, len(want)-len(seen))
		for p := range want {
			if !seen[p] {
				missing = append(missing, p)
			}
		}
		sort.Strings(missing)
		return fmt.Errorf("store: %d manifest entr%s missing from the tree, first: %q",
			len(missing), plural(len(missing), "y", "ies"), missing[0])
	}
	return nil
}

// ClassEntries returns the manifest entries belonging to one class, plus
// the structural directories on the path to them.
//
// The ancestors matter: a class path of "WS/Content/Paks" cannot be
// populated without "WS" and "WS/Content" existing first, and those are
// classified as structure rather than as the class.
func (m *Manifest) ClassEntries(class string) []Entry {
	want := make(map[string]bool)
	for _, e := range m.Entries {
		if e.Class != class {
			continue
		}
		want[e.Path] = true
		for p := path.Dir(e.Path); p != "." && p != "/"; p = path.Dir(p) {
			want[p] = true
		}
	}
	out := make([]Entry, 0, len(want))
	for _, e := range m.Entries {
		if want[e.Path] {
			out = append(out, e)
		}
	}
	return out
}

// ClassBytes is the total size of a class's regular files.
func (m *Manifest) ClassBytes(class string) int64 {
	var total int64
	for _, e := range m.ClassEntries(class) {
		if e.Type == EntryFile {
			total += e.Size
		}
	}
	return total
}

// VerifyClass checks a populated class tree against the manifest, comparing
// CONTENT but not permission bits.
//
// Modes are deliberately excluded. Publication populates the tree and then
// makes it read-only (`chmod -R a-w`), so the published copy's modes differ
// from the release's by design — comparing them would fail on the very
// hardening that makes publication safe. What must match is what the pages
// actually are: type, size, digest, and a symlink's target.
func (m *Manifest) VerifyClass(root, class string) error {
	entries := m.ClassEntries(class)
	if len(entries) == 0 {
		return fmt.Errorf("store: manifest has no entries for class %q", class)
	}
	seen := make(map[string]bool, len(entries))
	for _, e := range entries {
		full := filepath.Join(root, filepath.FromSlash(e.Path))
		info, err := os.Lstat(full)
		if err != nil {
			return fmt.Errorf("store: class %q: %q is missing from the populated tree: %w",
				class, e.Path, err)
		}
		seen[e.Path] = true

		switch e.Type {
		case EntrySymlink:
			if info.Mode()&fs.ModeSymlink == 0 {
				return fmt.Errorf("store: class %q: %q is not a symlink", class, e.Path)
			}
			target, err := os.Readlink(full)
			if err != nil {
				return err
			}
			if target != e.Target {
				return fmt.Errorf("store: class %q: symlink %q points at %q, manifest says %q",
					class, e.Path, target, e.Target)
			}
		case EntryDir:
			if !info.IsDir() {
				return fmt.Errorf("store: class %q: %q is not a directory", class, e.Path)
			}
		case EntryFile:
			if !info.Mode().IsRegular() {
				return fmt.Errorf("store: class %q: %q is not a regular file", class, e.Path)
			}
			if info.Size() != e.Size {
				return fmt.Errorf("store: class %q: %q is %d bytes, manifest says %d",
					class, e.Path, info.Size(), e.Size)
			}
			sum, err := hashFile(full)
			if err != nil {
				return err
			}
			if sum != e.SHA256 {
				return fmt.Errorf("store: class %q: %q hashes to %s, manifest says %s",
					class, e.Path, sum, e.SHA256)
			}
		default:
			return fmt.Errorf("store: class %q: entry %q has unknown type %q", class, e.Path, e.Type)
		}
	}

	// Anything present that the manifest does not name is content nobody
	// hashed — the same hole VerifyClass's caller would otherwise leave open.
	return filepath.WalkDir(root, func(p string, d fs.DirEntry, err error) error {
		if err != nil {
			return err
		}
		rel, err := filepath.Rel(root, p)
		if err != nil {
			return err
		}
		if rel == "." {
			return nil
		}
		if !seen[filepath.ToSlash(rel)] {
			return fmt.Errorf("store: class %q: %q is present in the populated tree "+
				"but not in the manifest", class, filepath.ToSlash(rel))
		}
		return nil
	})
}

func plural(n int, one, many string) string {
	if n == 1 {
		return one
	}
	return many
}

// Marshal renders the manifest as the bytes written to disk.
func (m *Manifest) Marshal() ([]byte, error) {
	b, err := json.MarshalIndent(m, "", "  ")
	if err != nil {
		return nil, err
	}
	return append(b, '\n'), nil
}

// ParseManifest decodes a manifest document.
func ParseManifest(b []byte) (*Manifest, error) {
	var m Manifest
	dec := json.NewDecoder(strings.NewReader(string(b)))
	dec.DisallowUnknownFields()
	if err := dec.Decode(&m); err != nil {
		return nil, fmt.Errorf("store: decode manifest: %w", err)
	}
	if m.SchemaVersion != ManifestSchemaVersion {
		return nil, fmt.Errorf("store: manifest schema_version %d is not supported (want %d)",
			m.SchemaVersion, ManifestSchemaVersion)
	}
	return &m, nil
}

func hashFile(path string) (string, error) {
	f, err := os.Open(path)
	if err != nil {
		return "", err
	}
	defer f.Close()
	h := sha256.New()
	if _, err := io.Copy(h, f); err != nil {
		return "", err
	}
	return hex.EncodeToString(h.Sum(nil)), nil
}

// unixMode renders a Go FileMode as the traditional Unix permission word:
// setuid, setgid and sticky above the nine permission bits. Go keeps those
// three outside Perm(), so a manifest built from Perm() alone would be blind
// to exactly the bits that matter most.
func unixMode(m fs.FileMode) uint32 {
	out := uint32(m.Perm())
	if m&fs.ModeSetuid != 0 {
		out |= 0o4000
	}
	if m&fs.ModeSetgid != 0 {
		out |= 0o2000
	}
	if m&fs.ModeSticky != 0 {
		out |= 0o1000
	}
	return out
}

// formatMode renders the Unix mode word as a fixed-width octal string with a
// leading zero, so 00755 and 04755 stay visually distinct and sort stably.
func formatMode(m fs.FileMode) string {
	return fmt.Sprintf("0%04o", unixMode(m))
}

func describeMode(m fs.FileMode) string {
	switch {
	case m&fs.ModeDevice != 0 && m&fs.ModeCharDevice != 0:
		return "character device"
	case m&fs.ModeDevice != 0:
		return "block device"
	case m&fs.ModeNamedPipe != 0:
		return "named pipe"
	case m&fs.ModeSocket != 0:
		return "socket"
	case m&fs.ModeIrregular != 0:
		return "irregular file"
	default:
		return "unsupported file type"
	}
}
