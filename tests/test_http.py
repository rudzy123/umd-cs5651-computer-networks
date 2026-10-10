"""HTTP/1.1 request grammar and the two localhost routes."""

from __future__ import annotations

import socket

import pytest

from umd_networks.http_proto import HttpServer, handle_http_request, parse_http_request


def _request(
    target: str,
    *,
    method: str = "GET",
    version: str = "HTTP/1.1",
    host: str | None = "localhost",
    extra: tuple[tuple[str, str], ...] = (),
) -> bytes:
    lines = [f"{method} {target} {version}"]
    if host is not None:
        lines.append(f"Host: {host}")
    lines.extend(f"{name}: {value}" for name, value in extra)
    return ("\r\n".join(lines) + "\r\n\r\n").encode("ascii")


def _response(raw: bytes) -> tuple[str, dict[str, str], bytes]:
    head, body = raw.split(b"\r\n\r\n", 1)
    assert b"\n" not in head.replace(b"\r\n", b"")
    lines = head.decode("ascii").split("\r\n")
    headers: dict[str, str] = {}
    for line in lines[1:]:
        name, value = line.split(":", 1)
        headers[name.lower()] = value.strip()
    assert int(headers["content-length"]) == len(body)
    assert headers["connection"] == "close"
    assert headers["content-type"].startswith("text/plain")
    assert "gmt" in headers["date"].lower()
    return lines[0], headers, body


def _exchange(port: int, payload: bytes, *, shutdown: bool = False) -> bytes:
    with socket.create_connection(("127.0.0.1", port), timeout=2) as sock:
        sock.settimeout(2)
        sock.sendall(payload)
        if shutdown:
            sock.shutdown(socket.SHUT_WR)
        chunks: list[bytes] = []
        while True:
            chunk = sock.recv(4096)
            if chunk == b"":
                return b"".join(chunks)
            chunks.append(chunk)


def test_health_and_echo_are_compliant_responses() -> None:
    status, _headers, body = _response(handle_http_request(_request("/health")))
    assert status == "HTTP/1.1 200 OK"
    assert body == b"ok\n"

    status, _headers, body = _response(handle_http_request(_request("/echo?message=hello%20world")))
    assert status == "HTTP/1.1 200 OK"
    assert body == b"hello world"


def test_query_decoding_and_exact_body_bytes() -> None:
    cafe = handle_http_request(_request("/echo?message=caf%C3%A9"))
    _status, headers, body = _response(cafe)
    assert body == "café".encode()
    assert headers["content-length"] == str(len(body))
    assert _response(handle_http_request(_request("/echo?message=hello+net")))[2] == b"hello net"
    assert _response(handle_http_request(_request("/echo?message=a%26b")))[2] == b"a&b"
    assert _response(handle_http_request(_request("/echo?message=")))[2] == b""


def test_user_bytes_stay_in_the_body() -> None:
    raw = handle_http_request(_request("/echo?message=a%0D%0AX-Injected:%20yes"))
    head, body = raw.split(b"\r\n\r\n", 1)
    assert b"X-Injected" not in head
    assert body == b"a\r\nX-Injected: yes"


def test_percent_decoded_path_selects_the_route() -> None:
    status, _headers, body = _response(handle_http_request(_request("/%68ealth")))
    assert status == "HTTP/1.1 200 OK"
    assert body == b"ok\n"


@pytest.mark.parametrize(
    ("target", "status_prefix"),
    [
        ("/missing", "HTTP/1.1 404 "),
        ("/health/", "HTTP/1.1 404 "),
        ("/Health", "HTTP/1.1 404 "),
        ("/echo", "HTTP/1.1 400 "),
        ("/echo?message=a&message=b", "HTTP/1.1 400 "),
        ("/echo?message=a&other=1", "HTTP/1.1 400 "),
    ],
)
def test_routing_errors(target: str, status_prefix: str) -> None:
    status, _headers, _body = _response(handle_http_request(_request(target)))
    assert status.startswith(status_prefix)


def test_method_and_version_errors() -> None:
    status, headers, _body = _response(handle_http_request(_request("/health", method="POST")))
    assert status == "HTTP/1.1 405 Method Not Allowed"
    assert headers["allow"] == "GET"
    status, _headers, _body = _response(
        handle_http_request(_request("/health", version="HTTP/1.0"))
    )
    assert status == "HTTP/1.1 505 HTTP Version Not Supported"
    status, _headers, _body = _response(handle_http_request(_request("/health", version="HTTP/9")))
    assert status.startswith("HTTP/1.1 400 ")


@pytest.mark.parametrize(
    "raw",
    [
        b"",
        b"GET /health HTTP/1.1\nHost: localhost\n\n",
        b"GET /health HTTP/1.1\r\nHost: localhost\r\n",
        b"GET /health HTTP/1.1\r\n\r\n",
        b"NOT A REQUEST\r\n\r\n",
        b"GET  /health HTTP/1.1\r\nHost: localhost\r\n\r\n",
        b"GET /health HTTP/1.1\r\nHost\r\n\r\n",
        b"GET /health HTTP/1.1\r\n Host: localhost\r\n\r\n",
        b"GET /health HTTP/1.1\r\nHost: localhost\r\nHost: example\r\n\r\n",
        b"GET /health HTTP/1.1\r\nHost: localhost\r\n\r\ntrailing",
        b"GET http://127.0.0.1/health HTTP/1.1\r\nHost: localhost\r\n\r\n",
        b"GET /health HTTP/1.1\r\nHost: localhost\r\nTransfer-Encoding: chunked\r\n\r\n",
        b"GET /health HTTP/1.1\r\nHost: localhost\r\nContent-Length: 5\r\n\r\n",
        b"GET /echo?message=a%00b HTTP/1.1\r\nHost: localhost\r\n\r\n",
        "GET /health HTTP/1.1\r\nHost: café\r\n\r\n".encode(),
    ],
)
def test_malformed_requests_are_400(raw: bytes) -> None:
    status, _headers, _body = _response(handle_http_request(raw))
    assert status.startswith("HTTP/1.1 400 ")


def test_request_size_limit_is_inclusive() -> None:
    prefix = b"GET /health HTTP/1.1\r\nHost: localhost\r\nX-Pad: "
    suffix = b"\r\n\r\n"
    pad = 8192 - len(prefix) - len(suffix)
    exact = prefix + (b"p" * pad) + suffix
    assert len(exact) == 8192
    status, _headers, body = _response(handle_http_request(exact, max_size=8192))
    assert status == "HTTP/1.1 200 OK"
    assert body == b"ok\n"
    status, _headers, _body = _response(handle_http_request(exact + b"p", max_size=8192))
    assert status.startswith("HTTP/1.1 413 ")


def test_parser_exposes_headers_and_the_query() -> None:
    request = parse_http_request(_request("/echo?message=hi", extra=(("X-Lab", "yes"),)))
    assert request.method == "GET"
    assert request.path == "/echo"
    assert request.version == "HTTP/1.1"
    assert request.header("x-lab") == "yes"
    assert request.header("Host") == "localhost"
    assert request.query["message"] == ("hi",)


def test_server_reassembles_a_split_request_and_rejects_garbage() -> None:
    with HttpServer(read_size=1) as server:
        assert server.host == "127.0.0.1"
        assert server.port != 0
        raw = _request("/health")
        with socket.create_connection(("127.0.0.1", server.port), timeout=2) as sock:
            sock.settimeout(2)
            sock.sendall(raw[:8])
            sock.sendall(raw[8:])
            chunks: list[bytes] = []
            while True:
                chunk = sock.recv(4096)
                if chunk == b"":
                    break
                chunks.append(chunk)
        status, _headers, body = _response(b"".join(chunks))
        assert status == "HTTP/1.1 200 OK"
        assert body == b"ok\n"
        status, _headers, _body = _response(_exchange(server.port, b"nope", shutdown=True))
        assert status.startswith("HTTP/1.1 400 ")
        assert server.exchanges == 2


def test_idle_http_connection_gets_408() -> None:
    with HttpServer(timeout=0.2) as server:
        status, _headers, _body = _response(_exchange(server.port, b""))
        assert status.startswith("HTTP/1.1 408 ")


def test_http_server_stops_reading_past_its_limit() -> None:
    with HttpServer(max_request_size=64, timeout=1) as server:
        status, _headers, _body = _response(_exchange(server.port, b"A" * 200, shutdown=True))
        assert status.startswith("HTTP/1.1 413 ")


def test_http_server_refuses_a_routable_host(monkeypatch: pytest.MonkeyPatch) -> None:
    def explode(*_args: object, **_kwargs: object) -> socket.socket:
        raise AssertionError("socket created for a non-loopback host")

    monkeypatch.setattr(socket, "socket", explode)
    with pytest.raises(ValueError, match="loopback"):
        HttpServer(host="203.0.113.50")
