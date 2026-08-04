package publish

import (
	"fmt"
	"os"
	"path/filepath"
	"strings"
	"testing"

	"srdm/internal/profile"
	"srdm/internal/store"
)

func sizingTestProfile() *profile.Profile {
	return &profile.Profile{
		SchemaVersion: profile.SchemaVersion,
		ID:            "sizing-test",
		Classes: []profile.Class{
			{Name: "pak", Kind: profile.KindManaged, Paths: []string{"WS/Content/Paks"}},
			{Name: "code", Kind: profile.KindManaged, Paths: []string{"Engine"}},
			{Name: "state", Kind: profile.KindExcluded, Paths: []string{"WS/Saved"}},
		},
	}
}

func TestGenerationBytesSumsOnlyManagedClasses(t *testing.T) {
	rel := &store.Release{
		Manifest: &store.Manifest{Entries: []store.Entry{
			{Path: "WS/Content/Paks/game.pak", Type: store.EntryFile, Size: 10 << 20, Class: "pak"},
			{Path: "Engine/libengine.so", Type: store.EntryFile, Size: 5 << 20, Class: "code"},
			{Path: "WS/Saved/world.db", Type: store.EntryFile, Size: 999 << 20, Class: "state"},
		}},
	}
	got := GenerationBytes(rel, sizingTestProfile())

	// Both managed classes round up to at least one granularity step each
	// (SizeGranularity, 64 MiB) and the excluded class must never be
	// counted — its 999 MiB would dwarf the real answer if it leaked in.
	if got < 2*SizeGranularity {
		t.Errorf("GenerationBytes() = %d, want at least two classes' worth of tmpfs (%d)",
			got, 2*SizeGranularity)
	}
	if got >= 900<<20 {
		t.Errorf("GenerationBytes() = %d, the excluded class's size leaked in", got)
	}
}

func TestGenerationBytesOfAnEmptyReleaseIsStillOneGranularityStepPerClass(t *testing.T) {
	rel := &store.Release{Manifest: &store.Manifest{}}
	got := GenerationBytes(rel, sizingTestProfile())
	if want := int64(2 * SizeGranularity); got != want {
		t.Errorf("GenerationBytes() = %d, want %d (ClassSize's floor, twice)", got, want)
	}
}

func writeSizingMemInfo(t *testing.T, availableBytes int64) string {
	t.Helper()
	path := filepath.Join(t.TempDir(), "meminfo")
	body := fmt.Sprintf("MemTotal:       20000000 kB\nMemAvailable:   %d kB\n", availableBytes/1024)
	if err := os.WriteFile(path, []byte(body), 0o644); err != nil {
		t.Fatal(err)
	}
	return path
}

func TestAvailableHostBytesParsesMemAvailable(t *testing.T) {
	path := writeSizingMemInfo(t, 8<<30)
	got, err := AvailableHostBytes(path)
	if err != nil {
		t.Fatal(err)
	}
	if got != 8<<30 {
		t.Errorf("AvailableHostBytes() = %d, want %d", got, 8<<30)
	}
}

func TestAvailableHostBytesRejectsAFileWithNoMemAvailableLine(t *testing.T) {
	path := filepath.Join(t.TempDir(), "meminfo")
	if err := os.WriteFile(path, []byte("MemTotal: 16384000 kB\n"), 0o644); err != nil {
		t.Fatal(err)
	}
	if _, err := AvailableHostBytes(path); err == nil {
		t.Fatal("a meminfo file with no MemAvailable line was accepted")
	} else if !strings.Contains(err.Error(), "MemAvailable") {
		t.Errorf("error does not name what is missing: %v", err)
	}
}

func TestAvailableHostBytesReportsAMissingFile(t *testing.T) {
	if _, err := AvailableHostBytes(filepath.Join(t.TempDir(), "does-not-exist")); err == nil {
		t.Fatal("a missing meminfo path was accepted")
	}
}
