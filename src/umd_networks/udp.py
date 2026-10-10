"""A localhost UDP echo exchange, and what UDP deliberately does not promise.

UDP preserves datagram boundaries and adds very little header overhead. It
does not create a connection, retransmit, or keep order. Those are application
choices. DNS uses UDP because a query and its reply fit in one datagram each
and the client can simply try again. A file transfer usually should not.

The server below binds to ``127.0.0.1`` and echoes each datagram that fits in
its buffer. A larger datagram is not silently shortened into a successful
echo: on Linux the truncated receive is reported and the server answers with
an error datagram instead.
"""

from __future__ import annotations

import socket
import threading
from typing import Self

from umd_networks.errors import ProtocolError
from umd_networks.loopback import (
    LOOPBACK_HOST,
    OwnedThread,
    endpoint,
    ensure_loopback,
    open_bound_socket,
    require_port,
    require_timeout,
)

# IPv4 header (20) + UDP header (8) leaves this much payload in a 65535-byte datagram.
_MAX_UDP_PAYLOAD = 65_507

DELIVERY_LIMITATIONS = """
UDP offers a best-effort datagram service. A datagram may be lost, reordered,
or duplicated, and the network will not repair that for you. There is no
handshake, no sequence number, no retransmission, and no congestion window in
the protocol itself. The checksum detects many damaged packets, but a missing
packet looks the same as a packet that has not arrived yet: the socket waits
until its timeout.

What UDP does preserve is the datagram boundary. One sendto corresponds to one
recvfrom, up to the buffer you supplied. If the buffer is shorter than the
datagram, the extra bytes are discarded. That is the opposite of TCP, which
hides packet boundaries and may return a partial message or several messages
in one read. Use UDP when one independent message fits in one datagram and the
application can tolerate loss. Use TCP when the byte stream must arrive
complete and in order.
""".strip()


def _require_packet_limit(size: int) -> int:
    if type(size) is not int or not 1 <= size <= _MAX_UDP_PAYLOAD:
        raise ValueError(f"UDP payload limit must be an integer from 1 through {_MAX_UDP_PAYLOAD}")
    return size


class UdpEchoServer:
    """Echo UDP datagrams on ``127.0.0.1``.

    A receive timeout means no datagram arrived during that interval. The
    server keeps listening. It does not invent a reply, because UDP has no
    connection on which to report idle time.
    """

    def __init__(
        self,
        host: str = LOOPBACK_HOST,
        port: int = 0,
        timeout: float = 0.1,
        max_packet: int = 1024,
    ) -> None:
        self.host = ensure_loopback(host)
        self._requested_port = require_port(port, allow_ephemeral=True)
        self.timeout = require_timeout(timeout)
        self.max_packet = _require_packet_limit(max_packet)
        self._bound_port: int | None = None
        self._socket: socket.socket | None = None
        self._worker = OwnedThread("udp-lab-server")
        self._lock = threading.Lock()
        self._echoes = 0
        self._truncated = 0
        self._closed = False

    def __enter__(self) -> Self:
        if self._closed or self._worker.started:
            raise RuntimeError("server is already running or closed")
        try:
            sock = open_bound_socket(socket.SOCK_DGRAM, self.host, self._requested_port)
            sock.settimeout(self.timeout)
            self._socket = sock
            self.host, self._bound_port = endpoint(sock)
            self._worker.start(self._serve)
        except Exception:
            self.close()
            raise
        return self

    def __exit__(self, exc_type: object, exc: object, traceback: object) -> None:
        self.close()
        if exc_type is None:
            self.check()

    @property
    def port(self) -> int:
        if self._bound_port is None:
            raise RuntimeError("server is not bound")
        return self._bound_port

    @property
    def address(self) -> tuple[str, int]:
        """The ``(host, port)`` pair clients pass to :class:`UdpClient`."""
        return (self.host, self.port)

    @property
    def echoes(self) -> int:
        with self._lock:
            return self._echoes

    @property
    def truncated_packets(self) -> int:
        with self._lock:
            return self._truncated

    def check(self) -> None:
        self._worker.check()

    def close(self) -> None:
        """Close the socket so a blocked ``recvmsg`` returns, then join the thread."""
        self._closed = True
        self._worker.stop.set()
        sock = self._socket
        self._socket = None
        if sock is not None:
            sock.close()
        self._worker.join(timeout=self.timeout + 1.0)

    def _serve(self) -> None:
        sock = self._socket
        if sock is None:
            return
        while not self._worker.stop.is_set():
            try:
                data, _ancillary, flags, address = sock.recvmsg(self.max_packet)
            except TimeoutError:
                # TimeoutError subclasses OSError. No datagram this interval is normal.
                continue
            except OSError:
                break
            peer = _peer(address)
            if peer is None:
                continue
            if flags & socket.MSG_TRUNC:
                with self._lock:
                    self._truncated += 1
                reply = f"ERR packet exceeds {self.max_packet} byte limit".encode("ascii")
            else:
                with self._lock:
                    self._echoes += 1
                reply = data
            try:
                sock.sendto(reply, peer)
            except OSError:
                break


class UdpClient:
    """Exchange one datagram with a loopback UDP echo server."""

    def __init__(
        self,
        address: tuple[str, int],
        timeout: float = 2.0,
        max_packet: int = 1024,
    ) -> None:
        host, port = address
        self.host = ensure_loopback(host)
        self.port = require_port(port, allow_ephemeral=False)
        self.timeout = require_timeout(timeout)
        self.max_packet = _require_packet_limit(max_packet)
        self._socket: socket.socket | None = None

    def __enter__(self) -> Self:
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        try:
            sock.settimeout(self.timeout)
            sock.connect((self.host, self.port))
        except Exception:
            sock.close()
            raise
        self._socket = sock
        return self

    def __exit__(self, exc_type: object, exc: object, traceback: object) -> None:
        self.close()

    def exchange(self, payload: bytes) -> bytes:
        """Send one datagram and return the reply.

        ``TimeoutError`` means nothing came back before the deadline. A lost
        UDP datagram and a peer that chooses not to answer look the same.
        """
        if not isinstance(payload, bytes):
            raise TypeError("payload must be bytes")
        if len(payload) > self.max_packet:
            raise ProtocolError(f"packet length {len(payload)} exceeds limit {self.max_packet}")
        sock = self._socket
        if sock is None:
            raise ProtocolError("client is closed")
        try:
            sock.send(payload)
            data, _ancillary, flags, _peer_address = sock.recvmsg(self.max_packet)
        except OSError as exc:
            # TimeoutError subclasses OSError. A quiet peer is a timeout, not a broken frame.
            if isinstance(exc, TimeoutError):
                raise
            raise ProtocolError("datagram exchange failed") from exc
        if flags & socket.MSG_TRUNC:
            raise ProtocolError("response was truncated")
        return data

    def close(self) -> None:
        sock = self._socket
        self._socket = None
        if sock is not None:
            sock.close()


def _peer(address: object) -> tuple[str, int] | None:
    if not isinstance(address, tuple) or len(address) < 2:
        return None
    host, port = address[0], address[1]
    if not isinstance(host, str) or not isinstance(port, int):
        return None
    return (host, port)
