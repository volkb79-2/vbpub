package opctl

import (
	"os"
	"path/filepath"
	"strings"
	"testing"
)

func TestAvailableHostBytesParsesMemAvailable(t *testing.T) {
	path := filepath.Join(t.TempDir(), "meminfo")
	body := "MemTotal:       16384000 kB\nMemFree:         1000000 kB\n" +
		"MemAvailable:    8000000 kB\nBuffers:          200000 kB\n"
	if err := os.WriteFile(path, []byte(body), 0o644); err != nil {
		t.Fatal(err)
	}
	got, err := availableHostBytes(path)
	if err != nil {
		t.Fatal(err)
	}
	if want := int64(8000000) * 1024; got != want {
		t.Errorf("availableHostBytes() = %d, want %d", got, want)
	}
}

func TestAvailableHostBytesRejectsAFileWithNoMemAvailableLine(t *testing.T) {
	path := filepath.Join(t.TempDir(), "meminfo")
	if err := os.WriteFile(path, []byte("MemTotal: 16384000 kB\n"), 0o644); err != nil {
		t.Fatal(err)
	}
	if _, err := availableHostBytes(path); err == nil {
		t.Fatal("a meminfo file with no MemAvailable line was accepted")
	} else if !strings.Contains(err.Error(), "MemAvailable") {
		t.Errorf("error does not name what is missing: %v", err)
	}
}

func TestAvailableHostBytesReportsAMissingFile(t *testing.T) {
	if _, err := availableHostBytes(filepath.Join(t.TempDir(), "does-not-exist")); err == nil {
		t.Fatal("a missing meminfo path was accepted")
	}
}

func TestGenerationBytesSumsOnlyManagedClasses(t *testing.T) {
	r := newRig(t)
	rel := r.release("rel-headroom")

	got := generationBytes(rel, r.prof)
	if got <= 0 {
		t.Fatalf("generationBytes() = %d, want a positive estimate", got)
	}
	// Two managed classes (pak, code), each rounded up to at least
	// publish.ClassSize's granularity — well below one huge blob, well
	// above zero.
	const oneClassFloor = 64 << 20 // publish.SizeGranularity, restated so a
	// change to that constant is felt here as a comment gone stale rather
	// than silently.
	if got < 2*oneClassFloor {
		t.Errorf("generationBytes() = %d, want at least two classes' worth of tmpfs (%d)",
			got, 2*oneClassFloor)
	}
}

func TestHeadroomErrorNamesTheShortfall(t *testing.T) {
	err := &HeadroomError{NeedBytes: 300, HaveBytes: 100, ShortfallBytes: 200}
	if !strings.Contains(err.Error(), "200") {
		t.Errorf("HeadroomError.Error() does not mention the shortfall: %v", err)
	}
}
