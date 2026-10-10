# UMD CS 5651 network programming laboratory

A localhost laboratory for IPv4 subnet arithmetic, framed TCP, UDP, a minimal
HTTP/1.1 server, and DNS message fixtures. Automated tests bind to
`127.0.0.1`, take a free port from the kernel, and do not resolve names.

The stack notes are in [docs/tcp-ip-stack.md](docs/tcp-ip-stack.md). Subnet
arithmetic is in [docs/subnetting.md](docs/subnetting.md). DNS and HTTP are in
[docs/dns-http.md](docs/dns-http.md). Sockets, timeouts, and framing are in
[docs/protocol-design.md](docs/protocol-design.md). The journal prompts are in
[docs/learning-log.md](docs/learning-log.md).

## Run the quality gates

Python 3.12 or newer. From the repository root:

```bash
python3.12 -m venv .venv
source .venv/bin/activate
python -m pip install -e ".[dev]"
ruff check src tests
ruff format --check src tests
mypy
pytest
```

`pytest` runs the unit tests and the localhost integration tests. Nothing in
that run dials a routable address or asks a recursive resolver for a name.

## Tour

Subnet of a host address:

```python
from umd_networks import IPv4Network, same_subnet

network = IPv4Network.parse("192.168.1.55/26")
print(network, network.usable_host_range, network.usable_host_count)
print(same_subnet("192.168.10.1", "192.168.10.200", 25))
```

TCP echo. The server's port is ephemeral. Each `request` is one length-prefixed
frame, and one connection can carry several:

```python
from umd_networks import TcpClient, TcpServer

with TcpServer() as server:
    with TcpClient(server.port) as client:
        print(client.request(b"hello"))
```

Why those frames exist:

```python
from umd_networks import demonstrate_message_boundaries

demo = demonstrate_message_boundaries((b"HELLO", b"WORLD"))
print(demo.unframed_stream)
print(demo.naive_reads)
print(demo.framed_messages)
```

UDP echo on loopback. A timeout means no datagram came back. A datagram longer
than the server buffer is reported, not quietly shortened:

```python
from umd_networks import UdpClient, UdpEchoServer

with UdpEchoServer() as server:
    with UdpClient(server.address) as client:
        print(client.exchange(b"ping"))
```

HTTP, parsed without a socket, then served on one:

```python
from umd_networks import HttpServer, handle_http_request

raw = b"GET /health HTTP/1.1\r\nHost: localhost\r\n\r\n"
print(handle_http_request(raw).split(b"\r\n")[0])

with HttpServer() as httpd:
    print(httpd.host, httpd.port)
```

`GET /health` returns `ok`. `GET /echo?message=hello%20world` returns the
decoded message as the body.

DNS fixtures. This does not use the network:

```python
from umd_networks import build_query, parse_message

message = parse_message(build_query("example.com"))
print(message.questions[0].name, message.header.recursion_desired)
```

The optional resolver command may use the network. The test suite does not
call it except behind a fake `getaddrinfo`:

```bash
umd-dns-lookup example.com
```

## Layout

| Path | Role |
| --- | --- |
| `src/umd_networks/ipv4.py` | Dotted decimal, masks, network, broadcast, usable range |
| `src/umd_networks/framing.py` | 4-byte length prefixes and the boundary demonstration |
| `src/umd_networks/tcp.py` | Loopback TCP client and server |
| `src/umd_networks/udp.py` | Loopback UDP echo, including delivery limits |
| `src/umd_networks/http_proto.py` | HTTP/1.1 head parser and the two GET routes |
| `src/umd_networks/dns.py` | DNS name codec, message parser, optional CLI |
| `tests/` | Subnet edges, framing, and localhost integration |
| `.github/workflows/ci.yml` | Lint, format, types, and tests on Python 3.12 |

## Safety

Servers and clients accept `127.0.0.1` and `localhost` only. `localhost` is
rewritten to `127.0.0.1` so the lab stays on IPv4. Port `0` on a bind asks for
a free port. Tests additionally refuse `getaddrinfo` for any hostname, so a
mistake cannot become a live lookup. The DNS CLI is the only caller of the
system resolver, and it is not part of pytest.
