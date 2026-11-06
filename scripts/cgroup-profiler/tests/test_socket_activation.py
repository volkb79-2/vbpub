"""Systemd socket activation parsing with fake descriptors only."""

from __future__ import annotations

import socket

import pytest

from cgprofile import serve


class FakeSocket:
    def __init__(self, fd, *, family=socket.AF_UNIX, kind=socket.SOCK_STREAM, listening=True):
        self.fd = fd
        self.family = family
        self.type = kind
        self.listening = listening
        self.inheritable = None
        self.closed = False

    def getsockopt(self, _level, _option):
        return int(self.listening)

    def set_inheritable(self, value):
        self.inheritable = value

    def settimeout(self, _timeout):
        pass

    def close(self):
        self.closed = True


def _adopter(monkeypatch, descriptors):
    def fake_socket(*, fileno):
        return descriptors[fileno]

    monkeypatch.setattr(serve.socket, "socket", fake_socket)


def test_no_activation_environment_returns_none_without_changes():
    environ = {"KEEP": "yes"}
    assert serve.adopt_systemd_sockets(environ, pid=31) is None
    assert environ == {"KEEP": "yes"}


def test_named_descriptors_are_adopted_and_activation_environment_is_cleared(monkeypatch):
    control, observer = FakeSocket(3), FakeSocket(4)
    _adopter(monkeypatch, {3: control, 4: observer})
    environ = {"LISTEN_PID": "31", "LISTEN_FDS": "2", "LISTEN_FDNAMES": "control:observer", "KEEP": "yes"}

    adopted = serve.adopt_systemd_sockets(environ, pid=31)

    assert adopted == {"control": control, "observer": observer}
    assert control.inheritable is False and observer.inheritable is False
    assert not control.closed and not observer.closed
    assert environ == {"KEEP": "yes"}


def test_zero_descriptors_are_reported_as_an_explicit_empty_set():
    environ = {"LISTEN_PID": "31", "LISTEN_FDS": "0", "LISTEN_FDNAMES": ""}
    assert serve.adopt_systemd_sockets(environ, pid=31) == {}
    assert not {"LISTEN_PID", "LISTEN_FDS", "LISTEN_FDNAMES"} & set(environ)


@pytest.mark.parametrize("environ, message", [
    ({"LISTEN_FDS": "1", "LISTEN_PID": "32", "LISTEN_FDNAMES": "control"}, "LISTEN_PID"),
    ({"LISTEN_FDS": "x", "LISTEN_PID": "31", "LISTEN_FDNAMES": "control"}, "non-negative integer"),
    ({"LISTEN_FDS": "-1", "LISTEN_PID": "31", "LISTEN_FDNAMES": ""}, "non-negative integer"),
    ({"LISTEN_FDS": "2", "LISTEN_PID": "31", "LISTEN_FDNAMES": "control"}, "names 1 descriptors"),
])
def test_invalid_activation_metadata_is_refused(environ, message):
    with pytest.raises(serve.SocketActivationError, match=message):
        serve.adopt_systemd_sockets(environ, pid=31)


@pytest.mark.parametrize("names, message", [
    ("unknown:observer", "unknown systemd socket descriptor name"),
    ("control:control", "more than one systemd socket descriptor"),
])
def test_unknown_and_duplicate_names_close_every_opened_descriptor(monkeypatch, names, message):
    descriptors = {3: FakeSocket(3), 4: FakeSocket(4)}
    _adopter(monkeypatch, descriptors)
    environ = {"LISTEN_PID": "31", "LISTEN_FDS": "2", "LISTEN_FDNAMES": names}

    with pytest.raises(serve.SocketActivationError, match=message):
        serve.adopt_systemd_sockets(environ, pid=31)

    assert all(descriptor.closed for descriptor in descriptors.values())


@pytest.mark.parametrize("descriptor, message", [
    (FakeSocket(3, family=socket.AF_INET), "not an AF_UNIX stream socket"),
    (FakeSocket(3, kind=socket.SOCK_DGRAM), "not an AF_UNIX stream socket"),
    (FakeSocket(3, listening=False), "is not listening"),
])
def test_non_listening_or_wrong_socket_types_are_closed(monkeypatch, descriptor, message):
    _adopter(monkeypatch, {3: descriptor})
    environ = {"LISTEN_PID": "31", "LISTEN_FDS": "1", "LISTEN_FDNAMES": "control"}

    with pytest.raises(serve.SocketActivationError, match=message):
        serve.adopt_systemd_sockets(environ, pid=31)

    assert descriptor.closed
