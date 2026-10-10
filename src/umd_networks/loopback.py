"""Sockets that stay on IPv4 loopback, plus the thread that serves them.

The laboratory binds and connects to ``127.0.0.1`` only. A routable address is
rejected before a socket is created, so a mistaken host cannot leave the
machine. Servers own one daemon thread and join it on close.
"""

from __future__ import annotations

import socket
import threading
from abc import ABC, abstractmethod
from collections.abc import Callable
from typing import Self

from umd_networks.errors import ProtocolError

LOOPBACK_HOST = "127.0.0.1"
_MAX_MESSAGE_LIMIT = 1_048_576


def ensure_loopback(host: str) -> str:
    """Return ``127.0.0.1`` or reject the host.

    ``localhost`` is rewritten to the IPv4 loopback address so every socket in
    the lab uses the same family.
    """
    if not isinstance(host, str):
        raise TypeError("host must be a string")
    if host == "localhost":
        return LOOPBACK_HOST
    if host != LOOPBACK_HOST:
        raise ValueError(
            f"refusing non-loopback host {host!r}; this laboratory uses 127.0.0.1 only"
        )
    return host


def require_port(port: int, *, allow_ephemeral: bool) -> int:
    """Validate a TCP or UDP port.

    Port ``0`` means "ask the kernel for a free port" and is only allowed when
    binding. Clients must name the port the server actually received.
    """
    if type(port) is not int or not 0 <= port <= 65535:
        raise ValueError("port must be an integer from 0 through 65535")
    if port == 0 and not allow_ephemeral:
        raise ValueError("port 0 is only valid when requesting an ephemeral port")
    return port


def require_timeout(timeout: float) -> float:
    """Return a positive socket timeout in seconds."""
    if isinstance(timeout, bool) or not isinstance(timeout, (int, float)) or timeout <= 0:
        raise ValueError("timeout must be a positive number of seconds")
    return float(timeout)


def require_max_message(size: int) -> int:
    """Limit an application payload so a bad length cannot reserve a huge buffer."""
    if type(size) is not int or not 1 <= size <= _MAX_MESSAGE_LIMIT:
        raise ValueError(
            f"maximum message size must be an integer from 1 through {_MAX_MESSAGE_LIMIT}"
        )
    return size


def tune_tcp(sock: socket.socket) -> None:
    """Disable Nagle's algorithm so small laboratory writes are not delayed."""
    sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)


def endpoint(sock: socket.socket) -> tuple[str, int]:
    """Return the IPv4 address and port of a bound or connected socket."""
    address = sock.getsockname()
    host, port = address[0], address[1]
    if not isinstance(host, str) or not isinstance(port, int):
        raise OSError("expected an IPv4 socket name")
    return host, port


def open_bound_socket(kind: socket.SocketKind, host: str, port: int) -> socket.socket:
    """Create a socket, bind it to loopback, and close it if bind fails."""
    bind_host = ensure_loopback(host)
    require_port(port, allow_ephemeral=True)
    sock = socket.socket(socket.AF_INET, kind)
    try:
        if kind == socket.SOCK_STREAM:
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            tune_tcp(sock)
        sock.bind((bind_host, port))
    except Exception:
        sock.close()
        raise
    return sock


def close_gracefully(sock: socket.socket, *, linger: float = 0.05) -> None:
    """Send FIN, drain bytes the peer already sent, then close.

    Closing with unread data often produces a reset. The half-close gives the
    peer an end-of-stream, which is the graceful outcome the lab tests for.
    The socket is always closed, including when shutdown fails.
    """
    try:
        try:
            sock.shutdown(socket.SHUT_WR)
        except OSError:
            return
        sock.settimeout(linger)
        try:
            while sock.recv(4096):
                continue
        except (TimeoutError, OSError):
            return
    finally:
        sock.close()


class OwnedThread:
    """One daemon thread that is joined before its owner is discarded."""

    def __init__(self, name: str) -> None:
        self.stop = threading.Event()
        self._name = name
        self._thread: threading.Thread | None = None
        self._lock = threading.Lock()
        self._failure: BaseException | None = None

    @property
    def started(self) -> bool:
        return self._thread is not None

    def start(self, target: Callable[[], None]) -> None:
        if self._thread is not None:
            raise RuntimeError(f"{self._name} is already running")

        def run() -> None:
            try:
                target()
            except Exception as exc:
                with self._lock:
                    if self._failure is None:
                        self._failure = exc

        thread = threading.Thread(target=run, name=self._name, daemon=True)
        self._thread = thread
        thread.start()

    def join(self, timeout: float) -> None:
        thread = self._thread
        if thread is None or thread is threading.current_thread():
            return
        thread.join(timeout=timeout)
        if thread.is_alive():
            raise RuntimeError(f"{self._name} did not stop within {timeout:.1f}s")

    def check(self) -> None:
        with self._lock:
            failure = self._failure
        if failure is not None:
            raise failure


class BackgroundAcceptServer(ABC):
    """A loopback TCP listener that handles one connection at a time.

    The listening socket uses a short timeout so :meth:`close` can wake the
    thread. Each accepted socket uses the request timeout instead. Subclasses
    implement the application protocol and then return; this class half-closes
    the socket.
    """

    def __init__(self, *, name: str, host: str, port: int, timeout: float) -> None:
        self.host = ensure_loopback(host)
        self._requested_port = require_port(port, allow_ephemeral=True)
        self._timeout = require_timeout(timeout)
        self._bound_port: int | None = None
        self._listener: socket.socket | None = None
        self._worker = OwnedThread(name)
        self._lock = threading.Lock()
        self._closed = False
        self._failure: BaseException | None = None
        self._protocol_errors: list[str] = []
        self._exchanges = 0

    def __enter__(self) -> Self:
        if self._closed or self._worker.started:
            raise RuntimeError("server is already running or closed")
        try:
            listener = open_bound_socket(socket.SOCK_STREAM, self.host, self._requested_port)
            try:
                listener.listen(16)
                listener.settimeout(0.1)
            except Exception:
                listener.close()
                raise
            self._listener = listener
            self.host, self._bound_port = endpoint(listener)
            self._worker.start(self._accept_loop)
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
        """The kernel-assigned port. Available once the server has been entered."""
        if self._bound_port is None:
            raise RuntimeError("server is not bound")
        return self._bound_port

    @property
    def exchanges(self) -> int:
        """How many application exchanges finished, including HTTP error responses."""
        with self._lock:
            return self._exchanges

    @property
    def protocol_errors(self) -> tuple[str, ...]:
        """Clear descriptions of frames or requests the server rejected."""
        with self._lock:
            return tuple(self._protocol_errors)

    def record_exchange(self) -> None:
        with self._lock:
            self._exchanges += 1

    def record_protocol_error(self, message: str) -> None:
        with self._lock:
            self._protocol_errors.append(message)

    def record_failure(self, exc: BaseException) -> None:
        with self._lock:
            if self._failure is None:
                self._failure = exc

    def check(self) -> None:
        """Raise an unexpected server-thread failure, if one was stored."""
        self._worker.check()
        with self._lock:
            failure = self._failure
        if failure is not None:
            raise failure

    def close(self) -> None:
        """Stop accepting, close the listener, and join the server thread."""
        if self._closed and not self._worker.started:
            return
        self._closed = True
        self._worker.stop.set()
        listener = self._listener
        self._listener = None
        if listener is not None:
            listener.close()
        self._worker.join(timeout=self._timeout + 1.0)

    def _accept_loop(self) -> None:
        while not self._worker.stop.is_set():
            listener = self._listener
            if listener is None:
                break
            try:
                connection, _peer = listener.accept()
            except TimeoutError:
                # TimeoutError subclasses OSError. An idle listener is not a failure.
                continue
            except OSError:
                break
            self._serve_one(connection)

    def _serve_one(self, connection: socket.socket) -> None:
        try:
            tune_tcp(connection)
            connection.settimeout(self._timeout)
            self.handle_connection(connection)
        except ProtocolError as exc:
            self.record_protocol_error(str(exc))
        except Exception as exc:
            self.record_failure(exc)
        finally:
            try:
                still_open = connection.fileno() != -1
            except OSError:
                still_open = False
            if still_open:
                close_gracefully(connection)

    @abstractmethod
    def handle_connection(self, connection: socket.socket) -> None:
        """Speak the application protocol on an accepted, timeout-armed socket."""
