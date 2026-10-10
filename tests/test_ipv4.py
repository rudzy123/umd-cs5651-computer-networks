"""Edge cases for dotted-decimal text, prefixes, and subnet arithmetic."""

from __future__ import annotations

import ipaddress

import pytest

from umd_networks.errors import IPv4AddressError
from umd_networks.ipv4 import (
    IPv4Address,
    IPv4Network,
    format_ipv4,
    parse_ipv4,
    same_subnet,
)

_MALFORMED = [
    "",
    "1.2.3",
    "1.2.3.4.5",
    "256.1.1.1",
    "1.2.3.256",
    "1.2.3.4.",
    ".1.2.3.4",
    "01.2.3.4",
    "1.02.3.4",
    "1.2.3.04",
    "1.2.3.00",
    "1.2.3.-1",
    "1.2.3.+1",
    "1.2.3.4 ",
    " 1.2.3.4",
    "1.2.3.4\n",
    "1.2..4",
    "a.b.c.d",
    "192.168.1.1/24",
    "1.2.3.4/32",
    "\u0661.2.3.4",
    "1.2.3.0x1",
    "1.2.3.1e2",
]


@pytest.mark.parametrize("text", _MALFORMED)
def test_malformed_addresses_are_rejected(text: str) -> None:
    with pytest.raises(IPv4AddressError):
        parse_ipv4(text)


@pytest.mark.parametrize(
    "cidr",
    [
        "10.0.0.0/33",
        "10.0.0.0/-1",
        "10.0.0.0/",
        "10.0.0.0/ 24",
        "10.0.0.0/024",
        "10.0.0.0/8/32",
        "10.0.0.0/abc",
        "/24",
        "10.0.0.256/24",
        "10.0.0.0/8 ",
        " 10.0.0.0/8",
        "10.0.0.1/24/24",
        "::1/128",
    ],
)
def test_malformed_cidr_is_rejected(cidr: str) -> None:
    with pytest.raises(IPv4AddressError):
        IPv4Network.parse(cidr)


def test_address_parser_rejects_non_strings() -> None:
    with pytest.raises(TypeError):
        parse_ipv4(0x7F000001)  # type: ignore[arg-type]
    with pytest.raises(TypeError):
        IPv4Network.parse(None)  # type: ignore[arg-type]


def test_boolean_prefix_is_not_an_integer_prefix() -> None:
    with pytest.raises(IPv4AddressError):
        same_subnet("10.0.0.1", "10.0.0.2", True)  # type: ignore[arg-type]


def test_extreme_addresses_round_trip() -> None:
    assert format_ipv4(parse_ipv4("0.0.0.0")) == "0.0.0.0"
    assert format_ipv4(parse_ipv4("255.255.255.255")) == "255.255.255.255"
    assert IPv4Address.parse("127.0.0.1").value == 0x7F000001


@pytest.mark.parametrize(
    ("prefix", "mask"),
    [
        (0, "0.0.0.0"),
        (8, "255.0.0.0"),
        (12, "255.240.0.0"),
        (16, "255.255.0.0"),
        (24, "255.255.255.0"),
        (25, "255.255.255.128"),
        (26, "255.255.255.192"),
        (27, "255.255.255.224"),
        (30, "255.255.255.252"),
        (31, "255.255.255.254"),
        (32, "255.255.255.255"),
    ],
)
def test_prefix_to_mask(prefix: int, mask: str) -> None:
    network = IPv4Network.parse(f"10.1.2.3/{prefix}")
    assert str(network.netmask) == mask


def test_host_bits_are_cleared_from_the_network_address() -> None:
    network = IPv4Network.parse("192.168.1.55/26")
    assert str(network) == "192.168.1.0/26"
    assert str(network.network_address) == "192.168.1.0"
    assert str(network.broadcast_address) == "192.168.1.63"
    assert str(network.netmask) == "255.255.255.192"
    assert network.total_addresses == 64
    assert network.usable_host_count == 62
    assert str(network.usable_host_range) == "192.168.1.1-192.168.1.62"
    assert network.usable_host_range.count == 62
    assert network.contains("192.168.1.62")
    assert network.contains(network.network_address)
    assert network.contains(network.broadcast_address)
    assert not network.contains("192.168.1.64")
    assert network.network_address < network.usable_host_range.first
    assert network.usable_host_range.last < network.broadcast_address


def test_documentation_prefix_27() -> None:
    network = IPv4Network.parse("203.0.113.77/27")
    assert str(network.network_address) == "203.0.113.64"
    assert str(network.broadcast_address) == "203.0.113.95"
    assert str(network.usable_host_range.first) == "203.0.113.65"
    assert str(network.usable_host_range.last) == "203.0.113.94"
    assert network.usable_host_count == 30


def test_slash_30_and_misaligned_host() -> None:
    aligned = IPv4Network.parse("192.168.1.0/30")
    assert (str(aligned.network_address), str(aligned.broadcast_address)) == (
        "192.168.1.0",
        "192.168.1.3",
    )
    assert str(aligned.usable_host_range) == "192.168.1.1-192.168.1.2"
    misaligned = IPv4Network.parse("192.168.1.5/30")
    assert str(misaligned.network_address) == "192.168.1.4"
    assert str(misaligned.broadcast_address) == "192.168.1.7"
    assert str(misaligned.usable_host_range) == "192.168.1.5-192.168.1.6"


def test_entire_space_and_single_host() -> None:
    everything = IPv4Network.parse("203.0.113.5/0")
    assert str(everything.network_address) == "0.0.0.0"
    assert str(everything.broadcast_address) == "255.255.255.255"
    assert everything.total_addresses == 2**32
    assert everything.usable_host_count == 2**32 - 2
    assert str(everything.usable_host_range.first) == "0.0.0.1"
    assert str(everything.usable_host_range.last) == "255.255.255.254"

    single = IPv4Network.parse("255.255.255.255/32")
    assert (
        single.network_address == single.broadcast_address == IPv4Address.parse("255.255.255.255")
    )
    assert single.usable_host_count == 1
    assert str(single.usable_host_range) == "255.255.255.255"


def test_rfc_3021_point_to_point_prefix() -> None:
    link = IPv4Network.parse("10.1.2.3/31")
    assert str(link.network_address) == "10.1.2.2"
    assert str(link.broadcast_address) == "10.1.2.3"
    assert link.total_addresses == 2
    assert link.usable_host_count == 2
    assert str(link.usable_host_range) == "10.1.2.2-10.1.2.3"
    assert link.contains("10.1.2.2")
    assert link.contains("10.1.2.3")
    assert not link.contains("10.1.2.1")


@pytest.mark.parametrize(
    ("left", "right", "prefix", "together"),
    [
        ("192.168.10.1", "192.168.10.254", 24, True),
        ("192.168.10.1", "192.168.11.1", 24, False),
        ("192.168.10.1", "192.168.10.127", 25, True),
        ("192.168.10.1", "192.168.10.200", 25, False),
        ("192.168.10.128", "192.168.10.255", 25, True),
        ("10.0.0.1", "10.255.255.254", 8, True),
        ("10.0.0.1", "11.0.0.1", 8, False),
        ("172.16.5.10", "172.31.255.255", 12, True),
        ("172.16.5.10", "172.32.0.1", 12, False),
        ("203.0.113.10", "198.51.100.20", 0, True),
        ("10.0.0.1", "10.0.0.1", 32, True),
        ("10.0.0.1", "10.0.0.2", 32, False),
    ],
)
def test_same_subnet(left: str, right: str, prefix: int, together: bool) -> None:
    assert same_subnet(left, right, prefix) is together
    parsed = IPv4Network.parse(f"{left}/{prefix}")
    assert parsed.contains(right) is together


@pytest.mark.parametrize(
    "cidr",
    [
        "10.1.2.3/8",
        "172.16.5.9/12",
        "192.168.1.55/26",
        "203.0.113.77/27",
        "192.168.1.5/30",
        "10.1.2.3/31",
        "198.51.100.20/32",
        "0.0.0.0/0",
        "255.255.255.255/32",
    ],
)
def test_results_match_the_stdlib_oracle(cidr: str) -> None:
    ours = IPv4Network.parse(cidr)
    std = ipaddress.ip_network(cidr, strict=False)
    assert str(ours.network_address) == str(std.network_address)
    assert str(ours.broadcast_address) == str(std.broadcast_address)
    assert str(ours.netmask) == str(std.netmask)
    assert ours.total_addresses == std.num_addresses
    if std.prefixlen >= 20:
        hosts = list(std.hosts())
        assert ours.usable_host_count == len(hosts)
        assert str(ours.usable_host_range.first) == str(hosts[0])
        assert str(ours.usable_host_range.last) == str(hosts[-1])
    elif std.prefixlen <= 30:
        assert ours.usable_host_count == std.num_addresses - 2
