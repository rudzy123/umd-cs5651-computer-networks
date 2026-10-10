"""Loopback TCP: sequential requests, timeouts, and clean closes."""

from __future__ import annotations

import socket
import struct
import threading
import time

import pytest

from umd_networks.errors import ProtocolError
from umd_networks.tcp import TcpClient, TcpServer


def test_kernel_selects_distinct_loopback_ports() -> None:
    with TcpServer() as first, TcpServer() as second:
        assert first.host == "127.0.0.1"
        assert second.host == "127.0.0.1"
        assert first.port != 0
        assert first.port != second.port


def test_routable_hosts_are_refused_before_a_socket_is_created(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def explode(*_args: object, **_kwargs: object) -> socket.socket:
        raise AssertionError("socket created for a non-loopback host")

    monkeypatch.setattr(socket, "socket", explode)
    with pytest.raises(ValueError, match="loopback"):
        TcpServer(host="203.0.113.10")
    with pytest.raises(ValueError, match="loopback"):
        TcpClient(80, host="203.0.113.10")


def test_one_connection_carries_several_requests() -> None:
    with TcpServer(read_size=1) as server:
        with TcpClient(server.port) as client:
            assert client.request(b"one") == b"one"
            assert client.request(b"two\x00three") == b"two\x00three"
            assert client.request(b"") == b""
        with TcpClient(server.port) as client:
            assert client.request(b"next") == b"next"
        assert server.exchanges == 4
        assert server.protocol_errors == ()


def test_idle_connection_ends_with_end_of_stream() -> None:
    with TcpServer(timeout=0.2) as server:
        with socket.create_connection(("127.0.0.1", server.port), timeout=2) as sock:
            sock.settimeout(2)
            assert sock.recv(16) == b""
        assert server.protocol_errors == ()


def test_truncated_frame_records_a_protocol_error() -> None:
    with TcpServer(timeout=1) as server:
        with socket.create_connection(("127.0.0.1", server.port), timeout=2) as sock:
            sock.settimeout(2)
            sock.sendall(struct.pack("!I", 5) + b"hi")
            sock.shutdown(socket.SHUT_WR)
            assert sock.recv(32) == b""
        assert server.exchanges == 0
        assert any("middle of a frame" in message for message in server.protocol_errors)


def test_declared_length_above_the_limit_is_rejected() -> None:
    with TcpServer(max_message_size=32, timeout=1) as server:
        with socket.create_connection(("127.0.0.1", server.port), timeout=2) as sock:
            sock.settimeout(2)
            sock.sendall(struct.pack("!I", 10_000_000))
            sock.shutdown(socket.SHUT_WR)
            assert sock.recv(32) == b""
        assert any("exceeds limit" in message for message in server.protocol_errors)


def test_client_refuses_an_oversized_payload_and_stays_usable() -> None:
    with TcpServer(max_message_size=64) as server:
        with TcpClient(server.port, max_message_size=8) as client:
            with pytest.raises(ProtocolError, match="exceeds limit 8"):
                client.request(b"x" * 9)
            assert client.request(b"ok") == b"ok"


def test_server_limit_closes_a_frame_the_client_considered_legal() -> None:
    with TcpServer(max_message_size=8, timeout=1) as server:
        with TcpClient(server.port, max_message_size=64, timeout=1) as client:
            with pytest.raises(ProtocolError, match="closed"):
                client.request(b"x" * 20)
        assert any("exceeds limit" in message for message in server.protocol_errors)


def test_request_timeout_closes_the_client() -> None:
    release = threading.Event()

    def slow(message: bytes) -> bytes:
        release.wait(timeout=2)
        return message

    with TcpServer(slow, timeout=2) as server:
        with TcpClient(server.port, timeout=0.15) as client:
            started = time.monotonic()
            with pytest.raises(TimeoutError):
                client.request(b"ping")
            assert time.monotonic() - started < 1
            with pytest.raises(ProtocolError, match="closed"):
                client.request(b"again")
        release.set()


def test_handler_bug_is_not_swallowed() -> None:
    def bad(_message: bytes) -> bytes:
        raise RuntimeError("boom")

    with pytest.raises(RuntimeError, match="boom"), TcpServer(bad, timeout=1) as server:
        with TcpClient(server.port, timeout=1) as client, pytest.raises(ProtocolError):
            client.request(b"x")
        assert server.exchanges == 0
