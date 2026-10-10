"""Loopback UDP echo, timeouts, and short receive buffers."""

from __future__ import annotations

import socket

import pytest

from umd_networks.errors import ProtocolError
from umd_networks.udp import DELIVERY_LIMITATIONS, UdpClient, UdpEchoServer


def test_delivery_limitations_are_stated() -> None:
    text = DELIVERY_LIMITATIONS.lower()
    assert "datagram" in text
    assert "lost" in text
    assert "reordered" in text
    assert "duplicated" in text


def test_echo_round_trip_uses_an_ephemeral_port() -> None:
    with UdpEchoServer() as first, UdpEchoServer() as second:
        assert first.host == "127.0.0.1"
        assert first.port != 0
        assert first.port != second.port
        with UdpClient(first.address, timeout=1) as client:
            assert client.exchange(b"ping") == b"ping"
            assert client.exchange(b"a\x00b") == b"a\x00b"
            assert client.exchange(b"") == b""
        assert first.echoes == 3
        assert first.truncated_packets == 0


def test_client_timeout_when_no_reply_is_sent() -> None:
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as blackhole:
        blackhole.bind(("127.0.0.1", 0))
        with UdpClient(blackhole.getsockname(), timeout=0.2) as client:
            with pytest.raises(TimeoutError):
                client.exchange(b"anybody?")


def test_closed_udp_port_is_an_error_not_a_hang() -> None:
    holder = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    holder.bind(("127.0.0.1", 0))
    address = holder.getsockname()
    holder.close()
    client = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        client.settimeout(0.5)
        client.connect(address)
        with pytest.raises(ConnectionRefusedError):
            client.send(b"hi")
            client.recv(32)
    finally:
        client.close()


def test_client_rejects_a_payload_above_its_own_limit() -> None:
    with UdpEchoServer(max_packet=64) as server:
        with UdpClient(server.address, max_packet=8, timeout=1) as client:
            with pytest.raises(ProtocolError, match="exceeds limit 8"):
                client.exchange(b"x" * 9)
            assert client.exchange(b"ok") == b"ok"


def test_datagram_larger_than_the_server_buffer_is_not_a_short_echo() -> None:
    with UdpEchoServer(max_packet=32) as server:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as raw:
            raw.settimeout(1)
            raw.sendto(b"x" * 64, server.address)
            reply, _peer = raw.recvfrom(256)
        assert reply.startswith(b"ERR packet exceeds 32")
        assert reply != b"x" * 32
        assert server.truncated_packets == 1
        assert server.echoes == 0


def test_routable_peer_is_refused(monkeypatch: pytest.MonkeyPatch) -> None:
    def explode(*_args: object, **_kwargs: object) -> socket.socket:
        raise AssertionError("socket created for a non-loopback host")

    monkeypatch.setattr(socket, "socket", explode)
    with pytest.raises(ValueError, match="loopback"):
        UdpEchoServer(host="203.0.113.10")
    with pytest.raises(ValueError, match="loopback"):
        UdpClient(("203.0.113.10", 9))
