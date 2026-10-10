# DNS and HTTP

DNS names a host. HTTP names a resource on a host that already has an address.
This lab parses both, and it resolves neither during the test run.

## How a lookup actually proceeds

When an application asks for `www.example.com`, the recursive resolver does
roughly the following. The laboratory CLI stops at step 1 and lets the
operating system do the rest. The parser is how you inspect the messages those
steps exchange, using bytes you already have.

1. The stub resolver (`socket.getaddrinfo`) asks the recursive resolver
   configured on the machine, usually in one UDP datagram to port 53.
2. If that resolver does not know the answer, it asks a root server for the
   `.com` servers.
3. It asks a `.com` server for the `example.com` servers.
4. It asks an authoritative `example.com` server for the record.
5. The record comes back with a TTL. Caches may reuse it until the TTL
   expires. A cached answer is why a second lookup can skip steps 2 through 4.

A response can be truncated (the TC bit). The client then retries over TCP.
Large answers and zone transfers use TCP for the same reason: UDP is one
datagram, and one datagram has a size.

The record types you will see first:

| Type | Value | Meaning |
| --- | --- | --- |
| A | 1 | IPv4 address, 4 octets |
| NS | 2 | Name of a nameserver |
| CNAME | 5 | Alias for another name |
| AAAA | 28 | IPv6 address, 16 octets |

Class `IN` (1) is the Internet. The header's QR bit distinguishes a query (0)
from a response (1). RD asks the server to recurse. RA says it was willing to.
RCODE 0 is "no error."

## The bytes of a name

A DNS name is not a null-terminated C string. Each label is a length byte
followed by that many octets, and a zero byte ends the name.

```text
example.com
07 65 78 61 6d 70 6c 65   03 63 6f 6d   00
   e  x  a  m  p  l  e       c  o  m
```

Messages repeat suffixes, so a later name can point at an earlier one. A
pointer is two bytes whose top two bits are `11`. The other 14 bits are an
offset from the start of the message. In the fixture used by the tests, the
answer name is `c0 0c`, a pointer to offset 12, which is the question name
`example.com`. `decode_name` returns the dotted name and the cursor **after
the pointer**, not after the target. The bytes that follow in the message are
the type and class, and they must not be parsed as another label.

Pointers must aim at an earlier octet. A pointer to itself, or a pair of
pointers that chase each other, is rejected. The tests build these cases from
fixture bytes. They do not send them to a server.

`build_query("example.com")` produces the hand-written query in
`tests/test_dns.py`: id `0x1234`, recursion desired, one IN A question, and no
resource records. `parse_message` reads the 12-byte header, then each section
the counts promise, and rejects trailing bytes.

## The optional CLI

```bash
umd-dns-lookup example.com
```

That command calls `socket.getaddrinfo` and may contact the configured
resolver. Pytest does not run it against the network. A test that calls
`lookup_hostname("example.com")` is failed on purpose. The CLI tests replace
`getaddrinfo` with a function that returns `203.0.113.5`, which is a
documentation address, not a host this process queried.

## HTTP request and response

An HTTP/1.1 exchange on a fresh TCP connection is one request and one
response. The request is ASCII header lines separated by CRLF, ended by a
blank line:

```text
GET /echo?message=hello%20world HTTP/1.1
Host: localhost

```

The request line is method, a space, the target, a space, and the version.
Header names are case-insensitive. HTTP/1.1 requires `Host`. This lab also
rejects folded header lines, duplicate header names, and a `Transfer-Encoding`,
because it does not read a body. `Content-Length` other than `0` is rejected
for the same reason.

The origin-form target must start with `/`. The path is percent-decoded before
routing, so `/%68ealth` selects `/health`. The query string is parsed
separately: `+` and `%20` both become a space, `%26` becomes `&`.

The two routes are:

| Request | Result |
| --- | --- |
| `GET /health` | `200` and the body `ok\n` |
| `GET /echo?message=` exactly once | `200` and the decoded message as the body, with no extra newline |
| Any other path | `404` |
| Any method other than `GET` | `405` and `Allow: GET` |
| `HTTP/1.0` or another `HTTP/x.y` | `505` |
| Malformed bytes, or no bytes before the timeout | `400` or `408` |
| Head larger than the limit (default 8192) | `413` |

A response is compliant enough to parse with the same rules: status line,
headers, a blank line, then exactly `Content-Length` body bytes. `Connection:
close` tells the client not to expect a second request on this socket.
`Date` is an IMF-fix HTTP date in GMT.

User data goes in the body, never into a header line. A message value of
`a\r\nX-Injected: yes` stays in the body, and `Content-Length` counts those
bytes. That is the difference between "the body happened to contain a CRLF"
and "the header block grew a new field."

`HttpServer` binds to `127.0.0.1` and an ephemeral port. It reads until the
blank line, one `read_size` chunk at a time, so a request split across two
`sendall` calls is still one request. Then it writes the response and
half-closes the socket.
