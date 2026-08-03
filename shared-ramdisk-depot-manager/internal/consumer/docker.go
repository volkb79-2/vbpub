package consumer

import (
	"context"
	"encoding/json"
	"fmt"
	"net"
	"net/http"
	"os"
	"strings"
	"time"
)

// DefaultDockerSocket is where the daemon listens.
const DefaultDockerSocket = "/var/run/docker.sock"

// Container is the part of a Docker container srdm cares about.
type Container struct {
	ID   string
	Name string
	// Mounts are the binds it was created with, with Source resolved in the
	// HOST namespace — which is what makes them comparable to srdm's own
	// paths at all.
	Mounts []Mount
}

// Mount is one configured bind.
type Mount struct {
	Source      string
	Destination string
	ReadOnly    bool
	// Propagation is how host-side mount events travel into the container:
	// rslave, rprivate, and so on. Empty for mounts where it does not apply.
	Propagation string
}

// Docker is a minimal client for the parts of the Engine API srdm needs.
//
// Written rather than imported, because srdm has no third-party
// dependencies and the whole surface here is one GET returning JSON. The
// docker SDK would pull in a dependency tree larger than srdm for that.
type Docker struct {
	socket string
	client *http.Client
}

// NewDocker returns a client for the socket. The empty path means the
// default.
func NewDocker(socket string) *Docker {
	if socket == "" {
		socket = DefaultDockerSocket
	}
	return &Docker{
		socket: socket,
		client: &http.Client{
			Transport: &http.Transport{
				DialContext: func(ctx context.Context, _, _ string) (net.Conn, error) {
					var d net.Dialer
					return d.DialContext(ctx, "unix", socket)
				},
			},
			// A bound, not an oracle: srdm asks a local daemon over a unix
			// socket, so a request that has not answered in this long is a
			// daemon that is not answering. Its expiry is reported as a
			// degraded check, never as "nothing is holding this".
			Timeout: 10 * time.Second,
		},
	}
}

// Available reports whether the socket is even there, with an error that
// says what to do about it.
func (d *Docker) Available() error {
	st, err := os.Stat(d.socket)
	if err != nil {
		return fmt.Errorf("the Docker socket %s is not reachable (%w); srdm can still see "+
			"mounts that hold a generation, but cannot name the containers behind them",
			d.socket, err)
	}
	if st.Mode()&os.ModeSocket == 0 {
		return fmt.Errorf("%s is not a socket", d.socket)
	}
	return nil
}

// RunningContainers lists the running containers and their configured binds.
//
// Running only, deliberately, and it is the narrower of two rules in the
// design. Teardown safety is about what is holding pages right now; the
// stricter "no labeled container in ANY state" rule governs generation GC,
// where a stopped definition still pins a generation it will need on its
// next start. Conflating them would refuse teardown for a container that
// holds nothing.
func (d *Docker) RunningContainers(ctx context.Context) ([]Container, error) {
	if err := d.Available(); err != nil {
		return nil, err
	}
	// The host part is ignored by the dialer above but has to be well-formed.
	req, err := http.NewRequestWithContext(ctx, http.MethodGet,
		"http://docker/containers/json", nil)
	if err != nil {
		return nil, err
	}
	resp, err := d.client.Do(req)
	if err != nil {
		return nil, fmt.Errorf("listing containers: %w", err)
	}
	defer resp.Body.Close()
	if resp.StatusCode != http.StatusOK {
		return nil, fmt.Errorf("listing containers: the Docker daemon answered %s", resp.Status)
	}

	// Deliberately permissive about unknown fields: this is somebody else's
	// schema and it grows between versions. srdm reads the four things it
	// needs and ignores the rest, so a new Docker release does not turn a
	// safety check into an error.
	var raw []struct {
		ID     string   `json:"Id"`
		Names  []string `json:"Names"`
		Mounts []struct {
			Source      string `json:"Source"`
			Destination string `json:"Destination"`
			RW          bool   `json:"RW"`
			Propagation string `json:"Propagation"`
		} `json:"Mounts"`
	}
	if err := json.NewDecoder(resp.Body).Decode(&raw); err != nil {
		return nil, fmt.Errorf("decoding the container list: %w", err)
	}

	out := make([]Container, 0, len(raw))
	for _, c := range raw {
		out = append(out, Container{
			ID:     c.ID,
			Name:   containerName(c.Names),
			Mounts: toMounts(c.Mounts),
		})
	}
	return out, nil
}

// containerName picks the display name. Docker returns them with a leading
// slash, which is an artefact of its own naming and not part of the name an
// operator would type.
func containerName(names []string) string {
	if len(names) == 0 {
		return ""
	}
	return strings.TrimPrefix(names[0], "/")
}

func toMounts(in []struct {
	Source      string `json:"Source"`
	Destination string `json:"Destination"`
	RW          bool   `json:"RW"`
	Propagation string `json:"Propagation"`
}) []Mount {
	out := make([]Mount, 0, len(in))
	for _, m := range in {
		out = append(out, Mount{Source: m.Source, Destination: m.Destination,
			ReadOnly: !m.RW, Propagation: m.Propagation})
	}
	return out
}

// BindPropagation reports how a running container binds a host path, and
// which container that is.
//
// It answers the first host-bind precondition: the Wings container must
// carry rslave on the volume tree, or nothing srdm mounts is visible to it
// and nothing srdm unmounts goes away. Exactly one container is expected to
// bind that path; two would mean srdm cannot tell which one Wings is, and
// refusing beats picking.
func (d *Docker) BindPropagation(ctx context.Context, hostPath string) (string, string, error) {
	containers, err := d.RunningContainers(ctx)
	if err != nil {
		return "", "", err
	}
	var (
		propagation string
		name        string
		matches     int
	)
	for _, c := range containers {
		for _, m := range c.Mounts {
			if m.Source != hostPath {
				continue
			}
			matches++
			propagation, name = m.Propagation, c.Name
		}
	}
	switch matches {
	case 0:
		return "", "", fmt.Errorf("no running container binds %s, so srdm cannot tell "+
			"whether Wings would see anything it mounts there", hostPath)
	case 1:
		return propagation, name, nil
	default:
		return "", "", fmt.Errorf("%d running containers bind %s; srdm will not guess "+
			"which one is Wings", matches, hostPath)
	}
}
