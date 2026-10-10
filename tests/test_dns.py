"""DNS fixtures only. Nothing in this module contacts a resolver."""

from __future__ import annotations

import ipaddress
import socket
import struct

import pytest

from umd_networks.dns import (
    CLASS_IN,
    TYPE_A,
    TYPE_AAAA,
    TYPE_NS,
    build_query,
    decode_name,
    encode_name,
    lookup_hostname,
    main,
    parse_message,
)
from umd_networks.errors import DnsFormatError

# Query id 0x1234, RD=1, one question: example.com IN A.
EXAMPLE_QUERY = bytes.fromhex("1234 0100 0001 0000 0000 0000076578616d706c65 03636f6d 000001 0001")

# Same question, plus an A answer whose name is a pointer back to offset 12.
EXAMPLE_RESPONSE = bytes.fromhex(
    "1234 8180 0001 0001 0000 0000"
    "076578616d706c65 03636f6d 00"
    "0001 0001"
    "c00c"
    "0001 0001"
    "0000003c"
    "0004"
    "5db8d822"
)


def test_query_encoder_matches_the_hand_written_fixture() -> None:
    assert build_query("example.com") == EXAMPLE_QUERY
    assert build_query("example.com.") == EXAMPLE_QUERY
    message = parse_message(EXAMPLE_QUERY)
    assert message.header.transaction_id == 0x1234
    assert message.header.is_response is False
    assert message.header.recursion_desired is True
    assert message.header.recursion_available is False
    assert message.header.response_code == 0
    question = message.questions[0]
    assert question.name == "example.com"
    assert question.qtype == TYPE_A
    assert question.qclass == CLASS_IN
    assert message.answers == ()


def test_compressed_answer_fixture() -> None:
    message = parse_message(EXAMPLE_RESPONSE)
    assert message.header.is_response is True
    assert message.header.authoritative is False
    assert message.header.truncated is False
    assert message.header.recursion_desired is True
    assert message.header.recursion_available is True
    assert message.header.answer_count == 1
    answer = message.answers[0]
    assert answer.name == "example.com"
    assert answer.rtype == TYPE_A
    assert answer.ttl == 60
    assert answer.presentation == "93.184.216.34"

    _name, after_name = decode_name(EXAMPLE_RESPONSE, 12)
    answer_at = after_name + 4
    assert EXAMPLE_RESPONSE[answer_at : answer_at + 2] == b"\xc0\x0c"
    pointed, after_pointer = decode_name(EXAMPLE_RESPONSE, answer_at)
    assert pointed == "example.com"
    assert after_pointer == answer_at + 2


def test_name_round_trip_preserves_case_and_the_root() -> None:
    assert encode_name(".") == b"\x00"
    assert decode_name(b"\x00", 0) == (".", 1)
    encoded = encode_name("Www.CS.Umd.EDU.")
    assert decode_name(encoded, 0) == ("Www.CS.Umd.EDU", len(encoded))
    message = parse_message(
        build_query("www.example.com", qtype=TYPE_NS, transaction_id=7, recursion_desired=False)
    )
    assert message.header.transaction_id == 7
    assert message.header.recursion_desired is False
    assert message.questions[0].name == "www.example.com"
    assert message.questions[0].qtype == TYPE_NS


def test_aaaa_presentation_and_rejected_lengths() -> None:
    name = encode_name("example.com")
    address = ipaddress.IPv6Address("2001:db8::1").packed
    packet = (
        struct.pack("!6H", 1, 0x8180, 1, 1, 0, 0)
        + name
        + struct.pack("!HH", TYPE_AAAA, CLASS_IN)
        + b"\xc0\x0c"
        + struct.pack("!HHIH", TYPE_AAAA, CLASS_IN, 30, 16)
        + address
    )
    answer = parse_message(packet).answers[0]
    assert answer.presentation == "2001:db8::1"

    # The record header still claims 16 octets of rdata, but the packet ends early.
    with pytest.raises(DnsFormatError, match="truncated DNS rdata"):
        parse_message(packet[: -len(address)] + address[:4])


def test_pointer_loops_truncated_messages_and_bad_names() -> None:
    loop = struct.pack("!6H", 1, 0, 1, 0, 0, 0) + bytes.fromhex("c00c00010001")
    with pytest.raises(DnsFormatError, match="earlier"):
        parse_message(loop)
    with pytest.raises(DnsFormatError, match="12-byte"):
        parse_message(b"\x00" * 11)
    with pytest.raises(DnsFormatError, match="trailing"):
        parse_message(EXAMPLE_QUERY + b"\x00")
    with pytest.raises(DnsFormatError, match="63"):
        encode_name("a" * 64 + ".com")
    with pytest.raises(DnsFormatError):
        encode_name("example..com")
    with pytest.raises(DnsFormatError):
        encode_name("ex ample.com")
    short_a = (
        struct.pack("!6H", 1, 0x8180, 0, 1, 0, 0)
        + b"\x00"
        + struct.pack("!HHIH", TYPE_A, CLASS_IN, 1, 3)
        + b"\x01\x02\x03"
    )
    with pytest.raises(DnsFormatError, match="4 octets"):
        parse_message(short_a)


def test_live_resolver_is_unavailable_to_the_suite() -> None:
    with pytest.raises(RuntimeError, match="live name resolution"):
        lookup_hostname("example.com")


def test_cli_prints_a_mocked_resolver_result(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    def fake(host: object, *_args: object, **_kwargs: object) -> list[tuple[object, ...]]:
        assert host == "example.com"
        return [
            (socket.AF_INET, socket.SOCK_STREAM, 6, "", ("203.0.113.5", 0)),
            (socket.AF_INET, socket.SOCK_STREAM, 6, "", ("203.0.113.5", 0)),
            (socket.AF_INET6, socket.SOCK_STREAM, 6, "", ("2001:db8::5", 0, 0, 0)),
        ]

    monkeypatch.setattr(socket, "getaddrinfo", fake)
    assert main(["example.com"]) == 0
    assert capsys.readouterr().out.splitlines() == ["203.0.113.5", "2001:db8::5"]


def test_cli_reports_resolver_failure(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    def fake(*_args: object, **_kwargs: object) -> list[tuple[object, ...]]:
        raise socket.gaierror(socket.EAI_NONAME, "Name or service not known")

    monkeypatch.setattr(socket, "getaddrinfo", fake)
    assert main(["example.com"]) == 1
    assert "lookup failed" in capsys.readouterr().err


def test_cli_requires_a_hostname() -> None:
    with pytest.raises(SystemExit) as caught:
        main([])
    assert caught.value.code == 2
