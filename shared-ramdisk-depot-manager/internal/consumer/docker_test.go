package consumer

import (
	"context"
	"net"
	"net/http"
	"os"
	"path/filepath"
	"strings"
	"testing"
)

// stubDocker serves one canned response over a real unix socket, so the
// transport, the request and the decoding are all exercised — the parts that
// would otherwise only ever run against a daemon the gate does not have.
func stubDocker(t *testing.T, handler http.HandlerFunc) *Docker {
	t.Helper()
	// Kept short: a unix socket path is capped at ~108 bytes, and a Go test
	// temp dir plus a long name gets close enough to matter.
	sock := filepath.Join(t.TempDir(), "d.sock")
	l, err := net.Listen("unix", sock)
	if err != nil {
		t.Fatalf("listening on %s: %v", sock, err)
	}
	srv := &http.Server{Handler: handler}
	go func() { _ = srv.Serve(l) }()
	t.Cleanup(func() { _ = srv.Close() })
	return NewDocker(sock)
}

const containersJSON = `[
  {
    "Id": "3f2b1a9c8d7e6f504132231415161718192021222324252627282930313233ab",
    "Names": ["/soulmask-01"],
    "State": "running",
    "SomeFieldSrdmHasNeverHeardOf": {"nested": true},
    "Mounts": [
      {"Type": "bind", "Source": "/var/lib/pterodactyl/volumes/abc",
       "Destination": "/home/container", "RW": true},
      {"Type": "bind", "Source": "/run/srdm/testgame/a1b2c3d4/pak",
       "Destination": "/home/container/paks", "RW": false}
    ]
  }
]`

func TestRunningContainersReadsIDNameAndMounts(t *testing.T) {
	var gotPath string
	d := stubDocker(t, func(w http.ResponseWriter, r *http.Request) {
		gotPath = r.URL.Path
		w.Header().Set("Content-Type", "application/json")
		_, _ = w.Write([]byte(containersJSON))
	})

	cs, err := d.RunningContainers(context.Background())
	if err != nil {
		t.Fatal(err)
	}
	if gotPath != "/containers/json" {
		t.Errorf("asked for %q, want /containers/json", gotPath)
	}
	if len(cs) != 1 {
		t.Fatalf("got %d containers, want 1", len(cs))
	}
	c := cs[0]
	// Docker returns names with a leading slash; that is its own naming
	// artefact and not what an operator types.
	if c.Name != "soulmask-01" {
		t.Errorf("Name = %q, want soulmask-01", c.Name)
	}
	if !strings.HasPrefix(c.ID, "3f2b1a9c") {
		t.Errorf("ID = %q", c.ID)
	}
	if len(c.Mounts) != 2 {
		t.Fatalf("Mounts = %+v, want both binds", c.Mounts)
	}
	paks := c.Mounts[1]
	if paks.Source != "/run/srdm/testgame/a1b2c3d4/pak" {
		t.Errorf("Source = %q — the source is resolved in the HOST namespace, which is "+
			"the only thing that makes it comparable to srdm's own paths", paks.Source)
	}
	// RW inverts into ReadOnly, and getting that backwards would misreport
	// every published exposure, since all of them are read-only.
	if !paks.ReadOnly {
		t.Errorf("the read-only bind decoded as writable")
	}
	if c.Mounts[0].ReadOnly {
		t.Errorf("the writable volume decoded as read-only")
	}
}

// Docker's schema grows between versions. A field srdm has never heard of
// must not turn a safety check into an error — the JSON above carries one on
// purpose.
func TestUnknownFieldsDoNotBreakTheCheck(t *testing.T) {
	d := stubDocker(t, func(w http.ResponseWriter, _ *http.Request) {
		_, _ = w.Write([]byte(`[{"Id":"x","Names":["/y"],"Whatever":[1,2,3],"Mounts":[]}]`))
	})
	if _, err := d.RunningContainers(context.Background()); err != nil {
		t.Fatalf("an unrecognised field broke the container listing: %v", err)
	}
}

func TestANonOKAnswerIsAnError(t *testing.T) {
	d := stubDocker(t, func(w http.ResponseWriter, _ *http.Request) {
		http.Error(w, "no", http.StatusInternalServerError)
	})
	_, err := d.RunningContainers(context.Background())
	if err == nil {
		t.Fatal("a 500 from the daemon was read as an empty container list")
	}
	if !strings.Contains(err.Error(), "500") {
		t.Errorf("the error does not say what the daemon answered: %v", err)
	}
}

func TestMalformedJSONIsAnError(t *testing.T) {
	d := stubDocker(t, func(w http.ResponseWriter, _ *http.Request) {
		_, _ = w.Write([]byte(`{not json}`))
	})
	if _, err := d.RunningContainers(context.Background()); err == nil {
		t.Fatal("an unparseable answer was read as an empty container list")
	}
}

// A missing socket must say what it means for the check, not just fail. On a
// host without Docker this is the normal state, and the operator needs to
// know srdm can still see mounts.
func TestAMissingSocketExplainsWhatIsLost(t *testing.T) {
	d := NewDocker(filepath.Join(t.TempDir(), "absent.sock"))
	err := d.Available()
	if err == nil {
		t.Fatal("a socket that is not there reported as available")
	}
	if !strings.Contains(err.Error(), "absent.sock") {
		t.Errorf("the error does not name the socket: %v", err)
	}
	if !strings.Contains(err.Error(), "mounts") {
		t.Errorf("the error does not say what srdm can still do: %v", err)
	}
	// And listing goes through the same check rather than producing a bare
	// dial error.
	if _, err := d.RunningContainers(context.Background()); err == nil {
		t.Fatal("listing against a missing socket succeeded")
	}
}

func TestAPathThatIsNotASocketIsRejected(t *testing.T) {
	path := filepath.Join(t.TempDir(), "regular-file")
	if err := os.WriteFile(path, []byte("not a socket"), 0o644); err != nil {
		t.Fatal(err)
	}
	if err := NewDocker(path).Available(); err == nil {
		t.Fatal("a regular file was accepted as the Docker socket")
	}
}

func TestTheDefaultSocketIsDockersOwn(t *testing.T) {
	if NewDocker("").socket != DefaultDockerSocket {
		t.Fatalf("the default socket is %q, want %q", NewDocker("").socket, DefaultDockerSocket)
	}
}
