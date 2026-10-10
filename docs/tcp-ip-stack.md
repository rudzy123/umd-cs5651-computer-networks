# The TCP/IP stack

This laboratory builds the parts of the stack you can see from a socket: IP
addresses, UDP datagrams, TCP byte streams, DNS messages, and HTTP. The models
below are maps of that work. They are not extra headers you have to parse.

## OSI and TCP/IP

The OSI model is a teaching decomposition with seven layers. TCP/IP is the
protocol suite that actually runs the Internet. It is usually drawn with four
layers, or with a fifth "physical" layer when a textbook wants a place to put
the cable. The names do not line up one-to-one.

| Concern | OSI layer | TCP/IP (4-layer) | What this lab touches |
| --- | --- | --- | --- |
| Bits on a medium | 1 Physical | Link (bottom half) | Not programmed here |
| One hop, local delivery | 2 Data link | Link | ARP, described below, not sent |
| Host-to-host addressing | 3 Network | Internet | IPv4 addresses and prefixes |
| Process-to-process delivery | 4 Transport | Transport | TCP and UDP sockets |
| Dialog control | 5 Session | Application | Folded into the application |
| Data representation | 6 Presentation | Application | UTF-8 HTTP bodies, DNS labels |
| The user's protocol | 7 Application | Application | HTTP and DNS messages |

A useful rule: OSI is a vocabulary, TCP/IP is a deployment. When someone says
"layer 4," they mean the port and the transport protocol. When they say "layer
3," they mean the IP address. Session and presentation are real jobs, but in
TCP/IP they live inside the application. HTTP's `Content-Type` and DNS's label
encoding are presentation work wearing an application name.

## Encapsulation

Each layer treats the layer above it as an opaque payload and adds its own
header. Sending an HTTP request over Ethernet looks like this, from the inside
out:

```text
HTTP request bytes
  inside a TCP segment     (source port, destination port, sequence number, flags)
    inside an IPv4 datagram (source IP, destination IP, protocol = TCP)
      inside an Ethernet frame (source MAC, destination MAC, EtherType = IPv4)
        as bits on the wire
```

The receiver strips headers in the opposite order. The Ethernet card accepts
the frame, IP accepts the datagram, TCP accepts the segment, and the socket
delivers the HTTP bytes to the process. Nothing in the HTTP parser in this
repository sees the IP header. That blindness is the point of layering: the
HTTP code can be tested on `127.0.0.1` without building a frame.

Encapsulation is also why a "packet" is an ambiguous word. At the link layer
the unit is a frame, at the internet layer a datagram, at the transport layer a
segment (TCP) or a datagram (UDP), and at the application a message. The code
uses those words on purpose.

## ARP, one hop at a time

IPv4 addresses name interfaces in a network. Ethernet delivers frames to MAC
addresses, which are local to a link. ARP (Address Resolution Protocol) is the
directory that connects them on a single LAN.

A host that wants to send to an IPv4 address on its own subnet broadcasts an
ARP request: "Who has 192.168.1.10? Tell 192.168.1.20." The owner replies with
its MAC address, usually unicast. The sender caches that answer and writes it
into the Ethernet header. Hosts that are not on the subnet are not ARPed for.
The sender ARPs for the default gateway and lets the router forward the IP
datagram.

This laboratory never transmits ARP and never opens a raw socket. On
`127.0.0.1` the kernel delivers the datagram to the local stack, so there is no
link-layer lookup to perform. The useful lesson is the boundary: IP gets a
packet across networks, ARP only answers "which machine on this wire?"

## TCP: handshake, byte stream, reliability

TCP is connection-oriented. Before application data moves, the peers complete a
three-way handshake:

1. The client sends SYN with an initial sequence number.
2. The server sends SYN-ACK, acknowledging that number and offering its own.
3. The client sends ACK. The connection is established.

`socket.create_connection` in `TcpClient` hides those three segments. After it
returns, both sides may write. Sequence numbers then count **bytes**, not
messages. Each byte has a place in the stream. The receiver acknowledges the
next byte it expects. If an acknowledgment does not arrive, the sender
retransmits. A checksum discards damaged segments. The receive window tells the
sender how much buffer remains (flow control). A separate congestion window
slows the sender when the path is overloaded.

That reliability is why a TCP `recv` is not a message API. The stack is allowed
to merge two writes into one read, or to return half of one write. Order is
preserved. Boundaries are not. `docs/protocol-design.md` and
`umd_networks.framing` show the consequence and the usual fix, a length prefix.

Closing is its own handshake. `close_gracefully` shuts down the write side,
which sends FIN, then reads until the peer finishes. An application that
`close`s a socket while unread data is sitting in it often provokes a reset
instead of a FIN. The tests treat a clean end-of-stream (`recv` returns
`b""`) as the graceful outcome.

## UDP: what you give up, and what you keep

UDP adds a source port, a destination port, a length, and a checksum. There is
no handshake, no sequence number, no retransmission, and no congestion window.
A datagram may be lost, reordered, or duplicated. A timeout cannot tell "this
was dropped" from "the reply has not arrived."

What UDP keeps is the boundary. One `sendto` is one `recvfrom`, unless the
receive buffer is shorter than the datagram, in which case the tail is
discarded. `UdpEchoServer` treats that truncation as an error rather than as a
successful short echo. The constant `DELIVERY_LIMITATIONS` in
`umd_networks.udp` is the same contract, in prose.

Choose UDP when each message stands alone and fits in one datagram, and the
application can retry. DNS queries are the textbook case. Choose TCP when the
receiver must see every byte, in order, and is willing to pay for the
connection and the copies that reliability requires.
