# Sockets, ports, timeouts, and framing

A socket is the operating system's handle for one end of a conversation. The
address names a machine. The port names a process on that machine. This lab
uses `127.0.0.1` for the machine, so every packet is delivered by the local
stack and never forwarded.

## Ports without collisions

Ports are integers from 0 through 65535. Binding to port 0 does not mean "port
zero." It asks the kernel for a free ephemeral port. The server then reads that
port back with `getsockname` and the client connects to it.

```python
with TcpServer() as server:          # bound to 127.0.0.1 and a free port
    with TcpClient(server.port) as client:
        client.request(b"hello")
```

Two servers started together receive two ports. Tests never hard-code `8080`
or `53`. A host other than `127.0.0.1` or `localhost` is rejected before
`socket.socket` is called.

TCP sockets are `SOCK_STREAM`. UDP sockets are `SOCK_DGRAM`. The TCP server
`listen`s and `accept`s. The UDP server never accepts, because a datagram
carries its peer address on every `recvmsg`.

## Timeouts

A socket with no timeout blocks until the kernel has something to say, which
may be never. Every socket in this lab has a deadline.

The deadline means different things at different layers:

| Situation | What a timeout means |
| --- | --- |
| TCP `recv` before any byte of a new frame | The peer is idle. The server half-closes. |
| TCP `recv` after a frame has started | The frame is truncated. `ProtocolError`. |
| TCP client waiting for a response | The wait is over. The client closes, because the stream may now be mid-frame. |
| UDP `recv` | No datagram arrived. Loss and silence look the same. |
| HTTP `recv` before any byte | `408 Request Timeout`. |
| HTTP `recv` after some bytes, still no blank line | `400`, the head is incomplete. |

`TimeoutError` is not a `ProtocolError`. One means "try again, or give up
waiting." The other means "the bytes broke the agreement." `TimeoutError` is a
subclass of `OSError` in Python, so the handlers catch it first on purpose.

## Partial reads

`recv(n)` may return any positive number of bytes up to `n`. It returns
`b""` only when the peer has finished the stream. A loop that assumes one
`recv` equals one `send` will eventually mis-parse a message, even if it
passes on a quiet laptop.

`FrameReader` is that loop, written once:

1. Read exactly 4 bytes, however many `recv` calls that takes.
2. Interpret them as an unsigned big-endian length `N`.
3. If `N` is larger than the configured limit, stop. Do not allocate `N`.
4. Read exactly `N` more bytes.
5. That slice is one application message. Leftover bytes stay in the buffer
   for the next message.

A clean close before step 1 is the end of the connection. A clean close during
step 1 or step 4 is a protocol error: the sender promised bytes it did not
deliver.

## Why a raw TCP read drops the boundary

Run the demonstration:

```python
from umd_networks import demonstrate_message_boundaries

demo = demonstrate_message_boundaries((b"HELLO", b"WORLD"))
print(demo.unframed_stream)    # b'HELLOWORLD'
print(demo.naive_reads)        # (b'HELL', b'OWOR', b'LD') when sliced every 4 bytes
print(demo.framed_messages)    # (b'HELLO', b'WORLD')
```

The unframed send writes both payloads into one byte stream. After the bytes
arrive, nothing records that five of them were the first message. Slicing the
stream every four bytes, which is what a fixed-size `recv` can do, yields
pieces that match neither message.

The framed send writes this instead:

```text
00 00 00 05  48 45 4c 4c 4f     HELLO
00 00 00 05  57 4f 52 4c 44     WORLD
```

The recovery half of the demonstration reads that blob **one byte at a time**
over a real `127.0.0.1` TCP connection. The messages come back intact. The
length prefix, not the packet boundary, is what defined them.

`TcpServer` speaks only this framing. A handler receives `b"HELLO"`, not the
4-byte length. Several requests on one connection are several frames. The
server answers each one before reading the next. When the client half-closes,
the next read sees end-of-stream and the server half-closes too.

## HTTP frames with a blank line instead

HTTP does not use a 4-byte length in front of the headers. The header block
ends at the first blank line (`CRLF CRLF`). `Content-Length` then tells the
client how many body bytes follow. `read_http_head` is the same idea as
`FrameReader`: keep the bytes you have, read again, stop at the delimiter, and
refuse to buffer more than the limit.

Both designs exist because TCP itself will not choose one for you. The
application has to say where a message ends. A length prefix is easy to parse
and safe against a missing delimiter. A delimiter is easy to type in a
terminal and awkward when the delimiter can appear inside the data. HTTP uses
the delimiter for headers and a length for the body so that it can have both.
