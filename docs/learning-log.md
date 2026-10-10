# Learning log

Use this file as the lab journal. Each entry should be short enough to reread
before an exam and specific enough that a classmate could repeat the
experiment from it. Write what you observed, the bytes or numbers that justify
it, and the mistake the observation rules out.

Keep the sample entry. Add your own under it. Do not paste a transcript of an
entire test run in place of a sentence.

## Sample entry: a /26 is not "the first three octets"

**Question.** Which subnet contains `192.168.1.55/26`, and which addresses on
it can be assigned to hosts?

**Calculation.** The prefix keeps 26 bits, so the mask is `255.255.255.192`.
The last octet of the address is `0011 0111`. AND with `1100 0000` clears the
host bits and leaves `0`. The network is `192.168.1.0`. Six host bits count
from 0 through 63, so the broadcast is `192.168.1.63`. Reserving those two
endpoints leaves `.1` through `.62`, which is 62 hosts.

**Check.** `IPv4Network.parse("192.168.1.55/26")` prints the same four facts.
`same_subnet("192.168.1.1", "192.168.1.62", 26)` is true and
`same_subnet("192.168.1.1", "192.168.1.64", 26)` is false.

**What I would have got wrong.** Treating `/26` like `/24` and saying "anything
in `192.168.1`." `.64` is the next subnet, `192.168.1.64/26`.

## Entry template

Copy this block for each exercise.

```text
Question:

What I predicted:

What I ran (function or command, and the input bytes or addresses):

What came back:

Which rule explains that result:

What this rules out:
```

## Prompts

Answer these in your own entries. The documentation next to each prompt is the
reading, not the answer to paste.

1. **Encapsulation.** Draw an HTTP GET inside a TCP segment inside an IPv4
   datagram. Name one field each header contributes, and name one field the
   HTTP parser in this repository is not allowed to see. Reading:
   `docs/tcp-ip-stack.md`.

2. **ARP.** A host `10.0.0.5/24` sends to `10.0.0.9` and then to `10.0.1.9`.
   Which destination causes an ARP request for the destination itself, and
   which causes an ARP request for the gateway? Why does the loopback lab
   never send ARP?

3. **Subnet boundaries.** Compute network, broadcast, and usable range for
   `203.0.113.77/27` by hand, then compare with `IPv4Network.parse`. Do one
   prefix that is not on an octet boundary (`/12`, `/26`, `/27`, or `/31`).
   Reading: `docs/subnetting.md`.

4. **TCP handshake and close.** From a successful `TcpClient.request`, what
   has the kernel already finished before your handler runs? After the `with`
   block exits, what should a blocked `recv` on the other side return, and why
   is that different from a reset?

5. **Message boundaries.** Run `demonstrate_message_boundaries`. Record
   `unframed_stream`, `naive_reads`, and `framed_messages`. Explain why a
   four-byte slice of `HELLOWORLD` is not an application message, and why a
   one-byte `recv` loop still returns `HELLO` and `WORLD` after framing.
   Reading: `docs/protocol-design.md`.

6. **Timeouts versus protocol errors.** Name one TCP case that raises
   `TimeoutError` and one that raises `ProtocolError`. Why is a length of
   10,000,000 rejected before that many bytes are read?

7. **UDP.** What does a client timeout prove, and what does it not prove?
   Why is a 64-byte datagram into a 32-byte server buffer an error instead of
   the first 32 bytes echoed back?

8. **DNS.** Decode the answer name in the compressed fixture by hand (the
   pointer is `c0 0c`). What is offset 12 in that message? Why must the cursor
   after the pointer stay just after the pointer? Reading: `docs/dns-http.md`.

9. **HTTP.** Send `GET /echo?message=hello%20world` and account for every
   byte of the status line, the `Content-Length`, and the body. Then explain
   why a message containing CRLF is still one body and not a second header.

## Checklist before you submit the log

- [ ] One subnet worked by hand, including a prefix that is not `/8`, `/16`, or `/24`.
- [ ] The framing demonstration, with the three printed values written down.
- [ ] A sentence distinguishing an idle timeout from a truncated frame.
- [ ] A sentence distinguishing UDP loss from TCP retransmission.
- [ ] The compressed DNS name, with the offset arithmetic.
- [ ] One HTTP response whose `Content-Length` you checked against the body.
- [ ] No live scan, and no DNS query, was required to produce the evidence.
