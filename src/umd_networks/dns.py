"""Offline DNS message encoding and decoding, plus an optional resolver CLI.

Tests in this laboratory use fixture bytes only. They never ask the network
for a name. ``lookup_hostname`` is the one function that calls
``socket.getaddrinfo``, and only the command-line entry point uses it. That
call can contact the resolver configured on the machine, so it stays out of
the automated suite.

A DNS name is not a C string. Each label is a length byte followed by that
many octets, and the name ends with a zero byte. A pointer (the top two bits
set) replaces a suffix with a name that already appeared earlier in the same
message. The parser refuses pointer loops.
"""

from __future__ import annotations

import argparse
import ipaddress
import socket
import struct
import sys
from dataclasses import dataclass

from umd_networks.errors import DnsFormatError
from umd_networks.ipv4 import format_ipv4

TYPE_A = 1
TYPE_NS = 2
TYPE_CNAME = 5
TYPE_AAAA = 28
CLASS_IN = 1

_TYPE_NAMES = {TYPE_A: "A", TYPE_NS: "NS", TYPE_CNAME: "CNAME", TYPE_AAAA: "AAAA"}
_HEADER = struct.Struct("!6H")
_QUESTION_TAIL = struct.Struct("!HH")
_RECORD_TAIL = struct.Struct("!HHIH")
_MAX_LABELS = 128
_MAX_SECTION = 1024
_POINTER_MASK = 0xC0


def type_name(code: int) -> str:
    """Return the mnemonic for a common type, or the decimal code."""
    return _TYPE_NAMES.get(code, str(code))


def encode_name(name: str) -> bytes:
    """Encode a dotted DNS name as length-prefixed labels.

    A trailing dot is optional. ``.`` is the root name. Labels are ASCII in
    this laboratory; international names must already be in punycode.
    """
    if not isinstance(name, str):
        raise TypeError("DNS name must be a string")
    if name in {".", ""}:
        return b"\x00"
    trimmed = name[:-1] if name.endswith(".") else name
    if not trimmed or not trimmed.isascii() or trimmed.startswith(".") or ".." in trimmed:
        raise DnsFormatError(f"invalid DNS name: {name!r}")
    encoded = bytearray()
    for label in trimmed.split("."):
        if not label:
            raise DnsFormatError(f"DNS name contains an empty label: {name!r}")
        try:
            raw = label.encode("ascii")
        except UnicodeError as exc:
            raise DnsFormatError("DNS name must be ASCII in this laboratory") from exc
        if len(raw) > 63:
            raise DnsFormatError("DNS label is longer than 63 octets")
        if any(byte < 33 or byte > 126 for byte in raw):
            raise DnsFormatError(f"DNS label contains an illegal character: {label!r}")
        encoded.append(len(raw))
        encoded.extend(raw)
    encoded.append(0)
    if len(encoded) > 255:
        raise DnsFormatError("DNS name is longer than 255 octets")
    return bytes(encoded)


def decode_name(packet: bytes, offset: int) -> tuple[str, int]:
    """Decode the name at ``offset``.

    The returned integer is the first byte after this name field. Compression
    pointers do not move that cursor to the target of the pointer; the field
    itself is only two bytes once a pointer appears.
    """
    if not isinstance(packet, bytes):
        raise TypeError("DNS message must be bytes")
    if type(offset) is not int or offset < 0 or offset >= len(packet):
        raise DnsFormatError("DNS name offset is outside the message")
    labels: list[str] = []
    cursor = offset
    end = offset
    jumped = False
    visited: set[int] = set()
    while True:
        if cursor in visited:
            raise DnsFormatError("DNS compression pointer loop")
        visited.add(cursor)
        if cursor >= len(packet):
            raise DnsFormatError("truncated DNS name")
        length = packet[cursor]
        if length == 0:
            cursor += 1
            if not jumped:
                end = cursor
            break
        if length & _POINTER_MASK == _POINTER_MASK:
            if cursor + 1 >= len(packet):
                raise DnsFormatError("truncated DNS compression pointer")
            pointer = ((length & 0x3F) << 8) | packet[cursor + 1]
            if pointer >= len(packet):
                raise DnsFormatError("DNS compression pointer is outside the message")
            if pointer >= cursor:
                raise DnsFormatError("DNS compression pointer must refer to an earlier octet")
            if not jumped:
                end = cursor + 2
            jumped = True
            cursor = pointer
            continue
        if length & _POINTER_MASK:
            raise DnsFormatError("unsupported DNS label type")
        start = cursor + 1
        stop = start + length
        if stop > len(packet):
            raise DnsFormatError("truncated DNS label")
        try:
            label = packet[start:stop].decode("ascii")
        except UnicodeDecodeError as exc:
            raise DnsFormatError("DNS label is not ASCII") from exc
        if any(ord(char) < 33 or ord(char) > 126 for char in label):
            raise DnsFormatError("DNS label contains an illegal character")
        labels.append(label)
        if len(labels) > _MAX_LABELS:
            raise DnsFormatError("DNS name has too many labels")
        cursor = stop
        if not jumped:
            end = cursor
    name = ".".join(labels) if labels else "."
    if name != "." and len(name) > 253:
        raise DnsFormatError("DNS name is too long")
    return name, end


@dataclass(frozen=True)
class DnsHeader:
    """The fixed 12-byte DNS header, with the flag bits split out."""

    transaction_id: int
    flags: int
    question_count: int
    answer_count: int
    authority_count: int
    additional_count: int

    @property
    def is_response(self) -> bool:
        """True when this message answers a query (QR = 1)."""
        return bool(self.flags & 0x8000)

    @property
    def opcode(self) -> int:
        return (self.flags >> 11) & 0x0F

    @property
    def authoritative(self) -> bool:
        return bool(self.flags & 0x0400)

    @property
    def truncated(self) -> bool:
        """True when the UDP datagram did not hold the whole message (TC = 1)."""
        return bool(self.flags & 0x0200)

    @property
    def recursion_desired(self) -> bool:
        return bool(self.flags & 0x0100)

    @property
    def recursion_available(self) -> bool:
        return bool(self.flags & 0x0080)

    @property
    def response_code(self) -> int:
        return self.flags & 0x000F


@dataclass(frozen=True)
class DnsQuestion:
    """One question: the name being looked up, its type, and its class."""

    name: str
    qtype: int
    qclass: int


@dataclass(frozen=True)
class DnsRecord:
    """One resource record. ``rdata`` is the raw type-specific tail."""

    name: str
    rtype: int
    rclass: int
    ttl: int
    rdata: bytes

    @property
    def presentation(self) -> str | None:
        """Dotted IPv4 or compressed IPv6 text for A and AAAA records."""
        if self.rtype == TYPE_A:
            return format_ipv4(int.from_bytes(self.rdata, "big"))
        if self.rtype == TYPE_AAAA:
            return str(ipaddress.IPv6Address(self.rdata))
        return None


@dataclass(frozen=True)
class DnsMessage:
    """A parsed DNS message. Section tuples are empty when the count was zero."""

    header: DnsHeader
    questions: tuple[DnsQuestion, ...]
    answers: tuple[DnsRecord, ...]
    authorities: tuple[DnsRecord, ...]
    additionals: tuple[DnsRecord, ...]


def build_query(
    name: str,
    *,
    qtype: int = TYPE_A,
    transaction_id: int = 0x1234,
    recursion_desired: bool = True,
) -> bytes:
    """Build a single-question query with no resource records."""
    if type(transaction_id) is not int or not 0 <= transaction_id <= 0xFFFF:
        raise DnsFormatError("transaction id must be an integer from 0 through 65535")
    if type(qtype) is not int or not 0 <= qtype <= 0xFFFF:
        raise DnsFormatError("query type must be an integer from 0 through 65535")
    flags = 0x0100 if recursion_desired else 0
    header = _HEADER.pack(transaction_id, flags, 1, 0, 0, 0)
    return header + encode_name(name) + _QUESTION_TAIL.pack(qtype, CLASS_IN)


def parse_message(packet: bytes) -> DnsMessage:
    """Parse a complete DNS message from fixture bytes.

    Extra trailing bytes are rejected so a fixture cannot silently contain a
    second message. A and AAAA records must have their standard rdata lengths.
    """
    if not isinstance(packet, bytes):
        raise TypeError("DNS message must be bytes")
    if len(packet) < _HEADER.size:
        raise DnsFormatError("DNS message is shorter than the 12-byte header")
    if len(packet) > 65535:
        raise DnsFormatError("DNS message exceeds 65535 octets")
    transaction_id, flags, qdcount, ancount, nscount, arcount = _HEADER.unpack_from(packet)
    if any(count > _MAX_SECTION for count in (qdcount, ancount, nscount, arcount)):
        raise DnsFormatError("DNS section count is unreasonably large")
    header = DnsHeader(transaction_id, flags, qdcount, ancount, nscount, arcount)
    offset = _HEADER.size
    questions: list[DnsQuestion] = []
    for _ in range(qdcount):
        name, offset = decode_name(packet, offset)
        if offset + _QUESTION_TAIL.size > len(packet):
            raise DnsFormatError("truncated DNS question")
        qtype, qclass = _QUESTION_TAIL.unpack_from(packet, offset)
        offset += _QUESTION_TAIL.size
        questions.append(DnsQuestion(name, qtype, qclass))
    answers, offset = _records(packet, offset, ancount)
    authorities, offset = _records(packet, offset, nscount)
    additionals, offset = _records(packet, offset, arcount)
    if offset != len(packet):
        raise DnsFormatError("DNS message contains trailing bytes")
    return DnsMessage(header, tuple(questions), answers, authorities, additionals)


def lookup_hostname(hostname: str) -> tuple[str, ...]:
    """Resolve ``hostname`` with the operating-system resolver.

    This calls ``socket.getaddrinfo`` and therefore may use the network. The
    automated tests do not call it. Duplicate addresses are removed and the
    resolver's order is preserved.
    """
    if not isinstance(hostname, str) or not hostname or hostname != hostname.strip():
        raise ValueError("hostname must be a non-empty string")
    if any(char.isspace() for char in hostname):
        raise ValueError("hostname must be a non-empty string")
    infos = socket.getaddrinfo(hostname, None, type=socket.SOCK_STREAM)
    found: list[str] = []
    for info in infos:
        sockaddr = info[4]
        address = sockaddr[0]
        if isinstance(address, str) and address not in found:
            found.append(address)
    return tuple(found)


def main(argv: list[str] | None = None) -> int:
    """Command-line wrapper around :func:`lookup_hostname`."""
    parser = argparse.ArgumentParser(
        prog="umd-dns-lookup",
        description=(
            "Resolve a hostname with the operating-system resolver "
            "(socket.getaddrinfo). The automated tests never call this command."
        ),
    )
    parser.add_argument("hostname", help="Name to resolve, for example example.com")
    args = parser.parse_args(argv)
    try:
        addresses = lookup_hostname(args.hostname)
    except socket.gaierror as exc:
        print(f"lookup failed: {exc}", file=sys.stderr)
        return 1
    except ValueError as exc:
        print(str(exc), file=sys.stderr)
        return 1
    if not addresses:
        print("lookup failed: no addresses returned", file=sys.stderr)
        return 1
    for address in addresses:
        print(address)
    return 0


def _records(packet: bytes, offset: int, count: int) -> tuple[tuple[DnsRecord, ...], int]:
    records: list[DnsRecord] = []
    for _ in range(count):
        name, offset = decode_name(packet, offset)
        if offset + _RECORD_TAIL.size > len(packet):
            raise DnsFormatError("truncated DNS record")
        rtype, rclass, ttl, rdlength = _RECORD_TAIL.unpack_from(packet, offset)
        offset += _RECORD_TAIL.size
        if offset + rdlength > len(packet):
            raise DnsFormatError("truncated DNS rdata")
        rdata = packet[offset : offset + rdlength]
        offset += rdlength
        if rtype == TYPE_A and len(rdata) != 4:
            raise DnsFormatError("A record rdata must be 4 octets")
        if rtype == TYPE_AAAA and len(rdata) != 16:
            raise DnsFormatError("AAAA record rdata must be 16 octets")
        records.append(DnsRecord(name, rtype, rclass, ttl, rdata))
    return tuple(records), offset


if __name__ == "__main__":
    raise SystemExit(main())
