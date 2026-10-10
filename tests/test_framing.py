"""TCP is a byte stream. Length prefixes put the message boundaries back."""

from __future__ import annotations

import socket
import struct

import pytest

from umd_networks.errors import ProtocolError
from umd_networks.framing import (
    FrameReader,
    demonstrate_message_boundaries,
    encode_frame,
    local_tcp_pair,
    write_frame,
)


def test_unframed_stream_is_the_messages_concatenated() -> None:
    demo = demonstrate_message_boundaries((b"HELLO", b"WORLD"))
    assert demo.unframed_stream == b"HELLOWORLD"
    assert demo.naive_reads == (b"HELL", b"OWOR", b"LD")
    assert b"HELLO" not in demo.naive_reads
    assert demo.framed_messages == (b"HELLO", b"WORLD")
    assert demo.boundaries_lost


def test_two_frames_in_one_send_survive_short_reads() -> None:
    blob = encode_frame(b"HELLO") + encode_frame(b"WORLD")
    with local_tcp_pair() as (client, server):
        client.sendall(blob)
        client.shutdown(socket.SHUT_WR)
        reader = FrameReader(server, read_size=3)
        assert reader.read_frame() == b"HELLO"
        assert reader.read_frame() == b"WORLD"
        assert reader.read_frame() is None


def test_one_byte_reads_reassemble_a_binary_frame() -> None:
    payload = b"a\x00b\x00c"
    with local_tcp_pair() as (client, server):
        write_frame(client, payload)
        client.shutdown(socket.SHUT_WR)
        reader = FrameReader(server, read_size=1)
        assert reader.read_frame() == payload
        assert reader.read_frame() is None


def test_empty_frame_is_a_real_message() -> None:
    with local_tcp_pair() as (client, server):
        write_frame(client, b"")
        client.shutdown(socket.SHUT_WR)
        reader = FrameReader(server)
        assert reader.read_frame() == b""
        assert reader.read_frame() is None


def test_huge_length_is_rejected_without_buffering_the_body() -> None:
    with local_tcp_pair() as (client, server):
        client.sendall(struct.pack("!I", 10_000_000))
        client.shutdown(socket.SHUT_WR)
        reader = FrameReader(server, max_message_size=64)
        with pytest.raises(ProtocolError, match="exceeds limit 64"):
            reader.read_frame()


def test_peer_that_closes_mid_frame_is_a_protocol_error() -> None:
    with local_tcp_pair() as (client, server):
        client.sendall(struct.pack("!I", 5) + b"hi")
        client.shutdown(socket.SHUT_WR)
        reader = FrameReader(server)
        with pytest.raises(ProtocolError, match="middle of a frame"):
            reader.read_frame()


def test_timeout_before_a_frame_is_not_a_protocol_error() -> None:
    with local_tcp_pair(timeout=0.2) as (_client, server):
        reader = FrameReader(server)
        with pytest.raises(TimeoutError):
            reader.read_frame()


def test_timeout_after_a_partial_header_is_a_protocol_error() -> None:
    with local_tcp_pair(timeout=0.2) as (client, server):
        client.sendall(b"\x00\x00")
        reader = FrameReader(server)
        with pytest.raises(ProtocolError, match="timed out"):
            reader.read_frame()


def test_oversized_payload_is_refused_before_it_is_sent() -> None:
    with pytest.raises(ProtocolError, match="exceeds"):
        encode_frame(b"x" * 8, max_message_size=4)
