"""Length-prefix framing for a TCP byte stream.

TCP delivers an ordered sequence of bytes. It does not deliver a sequence of
messages. One ``recv`` may return a fragment of a message, exactly one
message, or several messages that the sender wrote separately. The 4-byte
big-endian length in front of each payload puts the boundary back:

    offset 0: unsigned 32-bit length N
    offset 4: N payload bytes

``FrameReader`` keeps the bytes that arrived early and loops until the length
it was promised is actually in hand. A peer that closes before the first byte
of a frame is a normal end of stream. A peer that closes after that, or a
length larger than the configured limit, is a protocol error.
"""

from __future__ import annotations

import socket
import struct
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass

from umd_networks.errors import ProtocolError
from umd_networks.loopback import (
    LOOPBACK_HOST,
    close_gracefully,
    endpoint,
    open_bound_socket,
    require_max_message,
    require_timeout,
    tune_tcp,
)

_LENGTH = struct.Struct("!I")
DEFAULT_MAX_MESSAGE = 65_536


def encode_frame(payload: bytes, *, max_message_size: int = DEFAULT_MAX_MESSAGE) -> bytes:
    """Prefix ``payload`` with its 4-byte big-endian length."""
    if not isinstance(payload, bytes):
        raise TypeError("frame payload must be bytes")
    limit = require_max_message(max_message_size)
    if len(payload) > limit:
        raise ProtocolError(f"frame length {len(payload)} exceeds limit {limit}")
    return _LENGTH.pack(len(payload)) + payload


def write_frame(
    connection: socket.socket,
    payload: bytes,
    *,
    max_message_size: int = DEFAULT_MAX_MESSAGE,
) -> None:
    """Send one length-prefixed frame. ``sendall`` may still use several packets."""
    connection.sendall(encode_frame(payload, max_message_size=max_message_size))


class FrameReader:
    """Reassemble length-prefixed frames from partial TCP reads.

    The reader is not safe to share across threads. One connection should have
    one reader so leftover bytes stay in front of the next frame.
    """

    def __init__(
        self,
        connection: socket.socket,
        *,
        max_message_size: int = DEFAULT_MAX_MESSAGE,
        read_size: int = 4096,
    ) -> None:
        self._connection = connection
        self._max_message_size = require_max_message(max_message_size)
        if type(read_size) is not int or read_size < 1:
            raise ValueError("read_size must be a positive integer")
        self._read_size = read_size
        self._buffer = bytearray()

    def read_frame(self) -> bytes | None:
        """Return the next payload, or ``None`` when the peer closed cleanly.

        ``TimeoutError`` is raised only when the socket times out before any
        byte of a new frame. A timeout or close after the frame has started
        raises :class:`ProtocolError`.
        """
        header = self._read_exact(_LENGTH.size, start_of_frame=True)
        if header is None:
            return None
        (length,) = _LENGTH.unpack(header)
        if length > self._max_message_size:
            raise ProtocolError(f"frame length {length} exceeds limit {self._max_message_size}")
        if length == 0:
            return b""
        body = self._read_exact(length, start_of_frame=False)
        if body is None:
            raise ProtocolError("connection closed in the middle of a frame")
        return body

    def _read_exact(self, size: int, *, start_of_frame: bool) -> bytes | None:
        while len(self._buffer) < size:
            try:
                chunk = self._connection.recv(min(self._read_size, size - len(self._buffer)))
            except TimeoutError as exc:
                if start_of_frame and not self._buffer:
                    raise
                raise ProtocolError("timed out while reading a partial frame") from exc
            if chunk == b"":
                if start_of_frame and not self._buffer:
                    return None
                raise ProtocolError("connection closed in the middle of a frame")
            self._buffer.extend(chunk)
        exact = bytes(self._buffer[:size])
        del self._buffer[:size]
        return exact


@contextmanager
def local_tcp_pair(*, timeout: float = 2.0) -> Iterator[tuple[socket.socket, socket.socket]]:
    """Yield ``(client, server)`` sockets connected through ``127.0.0.1``.

    The listening port is chosen by the kernel. Both sockets are closed when
    the context exits.
    """
    timeout = require_timeout(timeout)
    listener = open_bound_socket(socket.SOCK_STREAM, LOOPBACK_HOST, 0)
    client: socket.socket | None = None
    server: socket.socket | None = None
    try:
        listener.listen(1)
        listener.settimeout(timeout)
        _host, port = endpoint(listener)
        client = socket.create_connection((LOOPBACK_HOST, port), timeout=timeout)
        tune_tcp(client)
        server, _peer = listener.accept()
        tune_tcp(server)
        client.settimeout(timeout)
        server.settimeout(timeout)
        yield client, server
    finally:
        listener.close()
        if client is not None:
            client.close()
        if server is not None:
            server.close()


def _carry_until_eof(payload: bytes, *, timeout: float) -> bytes:
    """Send ``payload`` on a fresh loopback connection and collect the stream."""
    with local_tcp_pair(timeout=timeout) as (client, server):
        client.sendall(payload)
        client.shutdown(socket.SHUT_WR)
        chunks: list[bytes] = []
        while True:
            chunk = server.recv(4096)
            if chunk == b"":
                return b"".join(chunks)
            chunks.append(chunk)


def _recover_frames(blob: bytes, *, timeout: float) -> tuple[bytes, ...]:
    """Decode frames from a blob that is delivered one byte at a time."""
    with local_tcp_pair(timeout=timeout) as (client, server):
        client.sendall(blob)
        client.shutdown(socket.SHUT_WR)
        reader = FrameReader(server, read_size=1)
        messages: list[bytes] = []
        while True:
            message = reader.read_frame()
            if message is None:
                return tuple(messages)
            messages.append(message)


@dataclass(frozen=True)
class BoundaryDemonstration:
    """A side-by-side look at a raw TCP stream and the same bytes framed.

    ``unframed_stream`` is the concatenation of the application messages.
    Nothing in that buffer records where one message ended. ``naive_reads``
    slices that stream at a fixed size, the way a loop of ``recv(n)`` can.
    ``framed_messages`` are the payloads recovered after a 4-byte length was
    written in front of each one and the reader pulled a single byte at a time.
    """

    application_messages: tuple[bytes, ...]
    unframed_stream: bytes
    naive_reads: tuple[bytes, ...]
    framed_messages: tuple[bytes, ...]

    @property
    def boundaries_lost(self) -> bool:
        """True when the raw stream is only the messages joined together."""
        joined = b"".join(self.application_messages)
        distinct = len(self.application_messages) > 1 and all(self.application_messages)
        return (
            distinct
            and self.unframed_stream == joined
            and self.naive_reads != self.application_messages
        )


def demonstrate_message_boundaries(
    messages: tuple[bytes, ...] = (b"HELLO", b"WORLD"),
    *,
    naive_read_size: int = 4,
    timeout: float = 2.0,
) -> BoundaryDemonstration:
    """Show that TCP preserves bytes and order, not application messages.

    The unframed send writes every payload into one stream. A reader that takes
    fixed-size bites cannot recover the original list. The framed send writes
    the same payloads with length prefixes, and a one-byte reader still
    returns each payload intact.
    """
    if len(messages) < 2:
        raise ValueError("at least two application messages are required")
    if type(naive_read_size) is not int or naive_read_size < 1:
        raise ValueError("naive_read_size must be a positive integer")
    for message in messages:
        if not isinstance(message, bytes):
            raise TypeError("messages must be bytes")
    stream = _carry_until_eof(b"".join(messages), timeout=timeout)
    framed = b"".join(encode_frame(message) for message in messages)
    recovered = _recover_frames(framed, timeout=timeout)
    naive = tuple(
        stream[index : index + naive_read_size] for index in range(0, len(stream), naive_read_size)
    )
    return BoundaryDemonstration(
        application_messages=messages,
        unframed_stream=stream,
        naive_reads=naive,
        framed_messages=recovered,
    )


def finish_tcp(sock: socket.socket) -> None:
    """Half-close a TCP socket. Exposed for clients that are done sending."""
    close_gracefully(sock)
