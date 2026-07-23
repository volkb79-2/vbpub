// Package dockersrc is the real Docker implementation of
// manager.ContainerSource.
package dockersrc

import (
	"context"
	"log/slog"
	"strings"

	"github.com/docker/docker/api/types/container"
	"github.com/docker/docker/api/types/events"
	"github.com/docker/docker/api/types/filters"
	"github.com/docker/docker/client"

	"wings-slice-manager/internal/manager"
)

// Source lists containers and streams change events from the Docker daemon.
type Source struct {
	cli *client.Client
	log *slog.Logger
}

// New connects to the Docker daemon from the standard environment
// (DOCKER_HOST etc. or the default unix socket).
func New(log *slog.Logger) (*Source, error) {
	cli, err := client.NewClientWithOpts(client.FromEnv, client.WithAPIVersionNegotiation())
	if err != nil {
		return nil, err
	}
	return &Source{cli: cli, log: log}, nil
}

// Close releases the Docker client.
func (s *Source) Close() error {
	return s.cli.Close()
}

// Ping verifies daemon connectivity.
func (s *Source) Ping(ctx context.Context) error {
	_, err := s.cli.Ping(ctx)
	return err
}

// List returns all containers (running and stopped) with the fields the
// manager needs. Containers that disappear between list and inspect are
// skipped silently.
func (s *Source) List(ctx context.Context) ([]manager.ContainerInfo, error) {
	list, err := s.cli.ContainerList(ctx, container.ListOptions{All: true})
	if err != nil {
		return nil, err
	}
	out := make([]manager.ContainerInfo, 0, len(list))
	for _, c := range list {
		insp, err := s.cli.ContainerInspect(ctx, c.ID)
		if err != nil {
			if client.IsErrNotFound(err) {
				continue
			}
			s.log.Debug("inspect failed; skipping container", "id", c.ID, "error", err)
			continue
		}
		info := manager.ContainerInfo{
			ID:   c.ID,
			Name: strings.TrimPrefix(insp.Name, "/"),
		}
		if insp.Config != nil {
			info.Env = insp.Config.Env
		}
		if insp.HostConfig != nil {
			info.CgroupParent = insp.HostConfig.CgroupParent
		}
		if insp.State != nil {
			info.Running = insp.State.Running
		}
		out = append(out, info)
	}
	return out, nil
}

// Events returns a coalesced notification channel for container lifecycle
// events. The channel is closed when the underlying stream ends.
func (s *Source) Events(ctx context.Context) (<-chan struct{}, error) {
	f := filters.NewArgs()
	f.Add("type", "container")
	for _, action := range []string{"create", "start", "die", "stop", "destroy"} {
		f.Add("event", action)
	}
	msgs, errs := s.cli.Events(ctx, events.ListOptions{Filters: f})

	out := make(chan struct{}, 1)
	go func() {
		defer close(out)
		for {
			select {
			case <-ctx.Done():
				return
			case <-msgs:
				select {
				case out <- struct{}{}:
				default: // a notification is already pending; coalesce
				}
			case err := <-errs:
				if err != nil && ctx.Err() == nil {
					s.log.Warn("docker event stream ended", "error", err)
				}
				return
			}
		}
	}()
	return out, nil
}
