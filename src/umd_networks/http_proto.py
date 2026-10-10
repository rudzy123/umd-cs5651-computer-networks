"""A minimal HTTP/1.1 parser and localhost server.

The server understands two GET targets:

* ``/health`` returns ``200`` and the body ``ok``.
* ``/echo?message=`` returns ``200`` and the percent-decoded message as the body.

Anything else is a normal status: ``400`` for a malformed request, ``404`` for
an unknown path, ``405`` for a method other than GET, ``408`` when the client
connects and sends nothing, ``413`` when the head exceeds the size limit, and
``505`` for an HTTP version this lab does not speak.

HTTP frames a request with a blank line (``CRLF CRLF``) after the headers.
That is a different boundary rule from the 4-byte length prefix used by the
TCP echo service, and it is why a partial ``recv`` must keep reading until
that blank line shows up. This lab does not read a message body.
"""

from __future__ import annotations

import re
import socket
from collections.abc import Mapping
from dataclasses import dataclass
from email.utils import formatdate
from types import MappingProxyType
from urllib.parse import parse_qs, unquote, urlsplit

from umd_networks.errors import HttpError
from umd_networks.loopback import LOOPBACK_HOST, BackgroundAcceptServer, require_max_message

DEFAULT_MAX_REQUEST = 8192
HEALTH_PATH = "/health"
ECHO_PATH = "/echo"

_METHOD = re.compile(r"[A-Z][A-Z0-9!#$%&'*+.^_`|~-]*")
_HEADER_NAME = re.compile(r"[!#$%&'*+.^_`|~0-9A-Za-z-]+")
_HTTP_VERSION = re.compile(r"HTTP/\d\.\d")
_MAX_HEADERS = 32


@dataclass(frozen=True)
class HttpRequest:
    """The request line and headers of one HTTP/1.1 message.

    Header names are stored in lowercase. The path is percent-decoded.
    ``query`` maps each parameter to every value it was given, in order.
    """

    method: str
    target: str
    path: str
    query: Mapping[str, tuple[str, ...]]
    version: str
    headers: Mapping[str, str]

    def header(self, name: str) -> str | None:
        """Return one header value, ignoring the case of ``name``."""
        return self.headers.get(name.lower())


def build_http_response(
    status: int,
    reason: str,
    body: bytes | str = b"",
    *,
    extra_headers: Mapping[str, str] | None = None,
    date: str | None = None,
) -> bytes:
    """Build one HTTP/1.1 response that declares its length and closes.

    ``body`` is encoded as UTF-8 when it is a string. ``Content-Length`` is the
    number of bytes after the header blank line, not the number of characters.
    """
    if type(status) is not int or not 100 <= status <= 599:
        raise ValueError("HTTP status must be an integer from 100 through 599")
    if not isinstance(reason, str) or not reason.isascii() or not reason.strip():
        raise ValueError("HTTP reason phrase must be ASCII text")
    if any(ord(char) < 32 or ord(char) == 127 for char in reason):
        raise ValueError("HTTP reason phrase contains a control character")
    payload = body.encode("utf-8") if isinstance(body, str) else body
    if not isinstance(payload, bytes):
        raise TypeError("HTTP body must be bytes or str")
    header_date = formatdate(usegmt=True) if date is None else date
    _reject_header_text("Date", header_date)
    lines = [
        f"HTTP/1.1 {status} {reason}",
        f"Date: {header_date}",
        "Content-Type: text/plain; charset=utf-8",
        f"Content-Length: {len(payload)}",
        "Connection: close",
    ]
    for name, value in (extra_headers or {}).items():
        _reject_header_text(name, value)
        lines.append(f"{name}: {value}")
    head = "\r\n".join(lines).encode("ascii")
    return head + b"\r\n\r\n" + payload


def parse_http_request(data: bytes, *, max_size: int = DEFAULT_MAX_REQUEST) -> HttpRequest:
    """Parse one HTTP/1.1 request head. The caller must not include a body.

    The request is rejected when it is larger than ``max_size``, uses line
    endings other than CRLF, omits the blank line, or breaks the request-line
    and header grammar.
    """
    limit = require_max_message(max_size)
    if not isinstance(data, bytes):
        raise TypeError("HTTP request must be bytes")
    if len(data) > limit:
        raise HttpError(413, "Content Too Large")
    if b"\x00" in data:
        raise HttpError(400, "Bad Request")
    separator = b"\r\n\r\n"
    if separator not in data:
        raise HttpError(400, "Bad Request")
    head, _marker, tail = data.partition(separator)
    if tail:
        raise HttpError(400, "Bad Request")
    if b"\n" in head.replace(b"\r\n", b""):
        raise HttpError(400, "Bad Request")
    try:
        text = head.decode("ascii")
    except UnicodeDecodeError as exc:
        raise HttpError(400, "Bad Request") from exc
    lines = text.split("\r\n")
    if len(lines) > _MAX_HEADERS + 1:
        raise HttpError(400, "Bad Request")
    method, target, version = _request_line(lines[0])
    headers = _headers(lines[1:])
    if "host" not in headers or headers["host"] == "":
        raise HttpError(400, "Bad Request")
    if "transfer-encoding" in headers:
        raise HttpError(400, "Bad Request")
    if "content-length" in headers and headers["content-length"] != "0":
        raise HttpError(400, "Bad Request")
    path, query = _target(target)
    return HttpRequest(
        method=method,
        target=target,
        path=path,
        query=query,
        version=version,
        headers=headers,
    )


def handle_http_request(data: bytes, *, max_size: int = DEFAULT_MAX_REQUEST) -> bytes:
    """Return the response bytes for one request, including error responses."""
    try:
        request = parse_http_request(data, max_size=max_size)
    except HttpError as exc:
        return build_http_response(exc.status, exc.reason, f"{exc.reason}\n")
    if request.method != "GET":
        return build_http_response(
            405,
            "Method Not Allowed",
            "Method Not Allowed\n",
            extra_headers={"Allow": "GET"},
        )
    if request.path == HEALTH_PATH:
        return build_http_response(200, "OK", "ok\n")
    if request.path == ECHO_PATH:
        return _echo(request)
    return build_http_response(404, "Not Found", "Not Found\n")


def read_http_head(
    connection: socket.socket,
    *,
    max_size: int = DEFAULT_MAX_REQUEST,
    read_size: int = 4096,
) -> bytes:
    """Read until the header blank line, refusing to buffer more than ``max_size``."""
    limit = require_max_message(max_size)
    if type(read_size) is not int or read_size < 1:
        raise ValueError("read_size must be a positive integer")
    data = bytearray()
    while b"\r\n\r\n" not in data:
        if len(data) >= limit:
            raise HttpError(413, "Content Too Large")
        try:
            chunk = connection.recv(min(read_size, limit - len(data)))
        except TimeoutError as exc:
            if data:
                raise HttpError(400, "Bad Request") from exc
            raise HttpError(408, "Request Timeout") from exc
        if chunk == b"":
            raise HttpError(400, "Bad Request")
        data.extend(chunk)
    end = data.find(b"\r\n\r\n") + 4
    return bytes(data[:end])


class HttpServer(BackgroundAcceptServer):
    """Serve ``GET /health`` and ``GET /echo`` on ``127.0.0.1``."""

    def __init__(
        self,
        host: str = LOOPBACK_HOST,
        port: int = 0,
        timeout: float = 2.0,
        max_request_size: int = DEFAULT_MAX_REQUEST,
        read_size: int = 4096,
    ) -> None:
        super().__init__(name="http-lab-server", host=host, port=port, timeout=timeout)
        self._max_request_size = require_max_message(max_request_size)
        if type(read_size) is not int or read_size < 1:
            raise ValueError("read_size must be a positive integer")
        self._read_size = read_size

    def handle_connection(self, connection: socket.socket) -> None:
        try:
            raw = read_http_head(
                connection,
                max_size=self._max_request_size,
                read_size=self._read_size,
            )
            response = handle_http_request(raw, max_size=self._max_request_size)
        except HttpError as exc:
            self.record_protocol_error(str(exc))
            response = build_http_response(exc.status, exc.reason, f"{exc.reason}\n")
        connection.sendall(response)
        self.record_exchange()


def _request_line(line: str) -> tuple[str, str, str]:
    parts = line.split(" ")
    if len(parts) != 3:
        raise HttpError(400, "Bad Request")
    method, target, version = parts
    if _METHOD.fullmatch(method) is None or target == "":
        raise HttpError(400, "Bad Request")
    if version == "HTTP/1.1":
        return method, target, version
    if _HTTP_VERSION.fullmatch(version) is not None:
        raise HttpError(505, "HTTP Version Not Supported")
    raise HttpError(400, "Bad Request")


def _headers(lines: list[str]) -> Mapping[str, str]:
    headers: dict[str, str] = {}
    for line in lines:
        if not line or line[0] in " \t":
            raise HttpError(400, "Bad Request")
        name, separator, value = line.partition(":")
        if separator == "" or _HEADER_NAME.fullmatch(name) is None:
            raise HttpError(400, "Bad Request")
        if any(ord(char) < 32 or ord(char) == 127 for char in value):
            raise HttpError(400, "Bad Request")
        key = name.lower()
        if key in headers:
            raise HttpError(400, "Bad Request")
        headers[key] = value.strip(" \t")
    return MappingProxyType(headers)


def _target(target: str) -> tuple[str, Mapping[str, tuple[str, ...]]]:
    parts = urlsplit(target)
    if parts.scheme or parts.netloc or parts.fragment or not parts.path.startswith("/"):
        raise HttpError(400, "Bad Request")
    try:
        path = unquote(parts.path, errors="strict")
        parsed = parse_qs(parts.query, keep_blank_values=True, strict_parsing=bool(parts.query))
    except (UnicodeDecodeError, ValueError) as exc:
        raise HttpError(400, "Bad Request") from exc
    query = {key: tuple(values) for key, values in parsed.items()}
    if "\x00" in path or any("\x00" in value for values in query.values() for value in values):
        raise HttpError(400, "Bad Request")
    return path, MappingProxyType(query)


def _echo(request: HttpRequest) -> bytes:
    values = request.query.get("message")
    if set(request.query) != {"message"} or values is None or len(values) != 1:
        return build_http_response(400, "Bad Request", "message query parameter must appear once\n")
    return build_http_response(200, "OK", values[0])


def _reject_header_text(name: str, value: str) -> None:
    if _HEADER_NAME.fullmatch(name) is None:
        raise ValueError(f"invalid header name: {name!r}")
    if not isinstance(value, str) or not value.isascii():
        raise ValueError(f"invalid header value: {value!r}")
    if any(ord(char) < 32 or ord(char) == 127 for char in value):
        raise ValueError(f"invalid header value: {value!r}")
