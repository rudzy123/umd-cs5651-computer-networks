"""A length-prefixed TCP echo service bound to loopback.

Each connection carries a sequence of frames. The server reads one frame,
passes the payload to a handler, and writes one response frame. It then waits
for another request on the same connection. The connection ends when the
client half-closes, when the idle timeout expires before a new frame, or when
a frame is truncated or too long.

The default handler echoes the payload. Handlers see application bytes, never
the 4-byte length, so the framing rule stays in one place.
"""

from __future__ import annotations

import socket
from collections.abc import Callable
from typing import Self

from umd_networks.errors import ProtocolError
from umd_networks.framing import (
    DEFAULT_MAX_MESSAGE,
    FrameReader,
    finish_tcp,
    write_frame,
)
from umd_networks.loopback import (
    LOOPBACK_HOST,
    BackgroundAcceptServer,
    ensure_loopback,
    require_max_message,
    require_port,
    require_timeout,
    tune_tcp,
)


def echo_handler(message: bytes) -> bytes:
    """Return the request payload unchanged."""
    return message


class TcpServer(BackgroundAcceptServer):
    """Serve sequential length-prefixed requests on ``127.0.0.1``.

    Use the server as a context manager. The port is chosen by the kernel when
    ``port`` is 0, which keeps tests from colliding with a fixed number.
    """

    def __init__(
        self,
        handler: Callable[[bytes], bytes] | None = None,
        host: str = LOOPBACK_HOST,
        port: int = 0,
        timeout: float = 2.0,
        max_message_size: int = DEFAULT_MAX_MESSAGE,
        read_size: int = 4096,
    ) -> None:
        super().__init__(name="tcp-lab-server", host=host, port=port, timeout=timeout)
        self._handler = echo_handler if handler is None else handler
        self._max_message_size = require_max_message(max_message_size)
        if type(read_size) is not int or read_size < 1:
            raise ValueError("read_size must be a positive integer")
        self._read_size = read_size

    def handle_connection(self, connection: socket.socket) -> None:
        reader = FrameReader(
            connection,
            max_message_size=self._max_message_size,
            read_size=self._read_size,
        )
        while not self._worker.stop.is_set():
            try:
                message = reader.read_frame()
            except TimeoutError:
                return
            if message is None:
                return
            response = self._handler(message)
            if not isinstance(response, bytes):
                raise ProtocolError("handler must return bytes")
            try:
                write_frame(connection, response, max_message_size=self._max_message_size)
            except OSError:
                return
            self.record_exchange()


class TcpClient:
    """Send one length-prefixed request at a time to a loopback server.

    A timeout or a broken frame closes the client. The byte stream is no
    longer aligned to a frame boundary after that, so the connection is not
    reused.
    """

    def __init__(
        self,
        port: int,
        host: str = LOOPBACK_HOST,
        timeout: float = 2.0,
        max_message_size: int = DEFAULT_MAX_MESSAGE,
    ) -> None:
        self.host = ensure_loopback(host)
        self.port = require_port(port, allow_ephemeral=False)
        self._timeout = require_timeout(timeout)
        self._max_message_size = require_max_message(max_message_size)
        self._socket: socket.socket | None = None
        self._reader: FrameReader | None = None

    def __enter__(self) -> Self:
        sock = socket.create_connection((self.host, self.port), timeout=self._timeout)
        try:
            tune_tcp(sock)
            sock.settimeout(self._timeout)
        except Exception:
            sock.close()
            raise
        self._socket = sock
        self._reader = FrameReader(sock, max_message_size=self._max_message_size)
        return self

    def __exit__(self, exc_type: object, exc: object, traceback: object) -> None:
        self.close()

    def request(self, payload: bytes) -> bytes:
        """Write one frame and return the response payload."""
        if not isinstance(payload, bytes):
            raise TypeError("payload must be bytes")
        if len(payload) > self._max_message_size:
            raise ProtocolError(
                f"frame length {len(payload)} exceeds limit {self._max_message_size}"
            )
        sock = self._socket
        reader = self._reader
        if sock is None or reader is None:
            raise ProtocolError("client is closed")
        try:
            write_frame(sock, payload, max_message_size=self._max_message_size)
            response = reader.read_frame()
        except TimeoutError:
            self.close()
            raise
        except ProtocolError:
            self.close()
            raise
        except OSError as exc:
            self.close()
            raise ProtocolError("connection failed while exchanging a frame") from exc
        if response is None:
            self.close()
            raise ProtocolError("connection closed before a response arrived")
        return response

    def close(self) -> None:
        """Half-close the socket so the server observes end-of-stream."""
        sock = self._socket
        self._socket = None
        self._reader = None
        if sock is not None:
            finish_tcp(sock)
