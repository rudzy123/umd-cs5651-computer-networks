"""IPv4 addresses, prefix masks, and subnet arithmetic.

Addresses are 32-bit integers written as four decimal octets. A prefix length
says how many of those bits identify the subnet. The remaining bits identify a
host inside that subnet. Every calculation below is that split, done with
bitwise AND, OR, and shifts.
"""

from __future__ import annotations

from dataclasses import dataclass

from umd_networks.errors import IPv4AddressError

_IPV4_MAX = 0xFFFFFFFF
_PREFIX_MAX = 32


def _require_prefix(prefix_length: int) -> int:
    if type(prefix_length) is not int or not 0 <= prefix_length <= _PREFIX_MAX:
        raise IPv4AddressError("prefix length must be an integer from 0 through 32")
    return prefix_length


def parse_ipv4(text: str) -> int:
    """Return the 32-bit value of a dotted-decimal IPv4 address.

    Leading zeros are rejected so ``01`` cannot be confused with an octal
    number. Each octet must be a decimal integer from 0 through 255.
    """
    if not isinstance(text, str):
        raise TypeError("IPv4 address must be a string")
    if not text or not text.isascii() or text != text.strip():
        raise IPv4AddressError(f"invalid IPv4 address: {text!r}")
    parts = text.split(".")
    if len(parts) != 4:
        raise IPv4AddressError(f"IPv4 address must have four octets: {text!r}")
    octets: list[int] = []
    for part in parts:
        if not part.isascii() or not part.isdigit():
            raise IPv4AddressError(f"invalid IPv4 octet: {part!r}")
        if len(part) > 1 and part.startswith("0"):
            raise IPv4AddressError(f"IPv4 octet has a leading zero: {part!r}")
        value = int(part)
        if value > 255:
            raise IPv4AddressError(f"IPv4 octet is outside 0..255: {part!r}")
        octets.append(value)
    return (octets[0] << 24) | (octets[1] << 16) | (octets[2] << 8) | octets[3]


def format_ipv4(value: int) -> str:
    """Render a 32-bit IPv4 value in dotted decimal."""
    if type(value) is not int or not 0 <= value <= _IPV4_MAX:
        raise IPv4AddressError("IPv4 address integer is out of range")
    return ".".join(str((value >> shift) & 0xFF) for shift in (24, 16, 8, 0))


def netmask_value(prefix_length: int) -> int:
    """Return the mask with ``prefix_length`` leading one-bits.

    ``/24`` is ``255.255.255.0`` because the first 24 bits are ones. ``/0``
    keeps no network bits, so the mask is all zeros. ``/32`` keeps every bit.
    """
    prefix_length = _require_prefix(prefix_length)
    if prefix_length == 0:
        return 0
    return (_IPV4_MAX << (_PREFIX_MAX - prefix_length)) & _IPV4_MAX


def network_value(address: int, prefix_length: int) -> int:
    """Clear the host bits, leaving the subnet identifier."""
    return address & netmask_value(prefix_length)


def broadcast_value(address: int, prefix_length: int) -> int:
    """Set every host bit, producing the directed-broadcast address.

    For ``/31`` and ``/32`` this top address is still the bitwise result.
    Those two prefixes do not reserve it as a broadcast; see
    :meth:`IPv4Network.usable_host_range`.
    """
    prefix_length = _require_prefix(prefix_length)
    host_bits = 0 if prefix_length == _PREFIX_MAX else (1 << (_PREFIX_MAX - prefix_length)) - 1
    return network_value(address, prefix_length) | host_bits


def total_address_count(prefix_length: int) -> int:
    """Return how many IPv4 numbers fall inside the prefix, including reserved ones."""
    prefix_length = _require_prefix(prefix_length)
    return 1 << (_PREFIX_MAX - prefix_length)


def usable_host_count(prefix_length: int) -> int:
    """Return how many addresses may be assigned to interfaces.

    Prefixes of ``/30`` and shorter reserve the all-zero host field as the
    network address and the all-ones host field as the broadcast address.
    ``/31`` is a point-to-point link (RFC 3021) and uses both numbers.
    ``/32`` names exactly one interface.
    """
    prefix_length = _require_prefix(prefix_length)
    total = total_address_count(prefix_length)
    if prefix_length >= 31:
        return total
    return total - 2


@dataclass(frozen=True, order=True)
class IPv4Address:
    """One IPv4 address stored as an integer in ``0 .. 2**32 - 1``."""

    value: int

    def __post_init__(self) -> None:
        if type(self.value) is not int or not 0 <= self.value <= _IPV4_MAX:
            raise IPv4AddressError("IPv4 address integer is out of range")

    @classmethod
    def parse(cls, text: str) -> IPv4Address:
        """Parse dotted decimal such as ``192.168.1.10``."""
        return cls(parse_ipv4(text))

    def __str__(self) -> str:
        return format_ipv4(self.value)

    def __repr__(self) -> str:
        return f"IPv4Address('{self}')"


@dataclass(frozen=True)
class HostRange:
    """Inclusive span of addresses that may be assigned to hosts."""

    first: IPv4Address
    last: IPv4Address
    count: int

    def __str__(self) -> str:
        if self.first == self.last:
            return str(self.first)
        return f"{self.first}-{self.last}"


@dataclass(frozen=True)
class IPv4Network:
    """An IPv4 address interpreted under a CIDR prefix length.

    Host bits in ``address`` are accepted. ``network_address`` masks them
    off, which is the usual way to answer "which subnet is this host on?"
    """

    address: IPv4Address
    prefix_length: int

    def __post_init__(self) -> None:
        _require_prefix(self.prefix_length)
        if not isinstance(self.address, IPv4Address):
            raise TypeError("address must be an IPv4Address")

    @classmethod
    def parse(cls, cidr: str) -> IPv4Network:
        """Parse ``address/prefix``, for example ``192.168.1.55/26``."""
        if not isinstance(cidr, str):
            raise TypeError("CIDR must be a string")
        if not cidr or not cidr.isascii() or cidr != cidr.strip() or cidr.count("/") != 1:
            raise IPv4AddressError(f"invalid CIDR: {cidr!r}")
        address_text, prefix_text = cidr.split("/", 1)
        if (
            not prefix_text.isascii()
            or not prefix_text.isdigit()
            or (len(prefix_text) > 1 and prefix_text.startswith("0"))
        ):
            raise IPv4AddressError(f"invalid prefix length: {prefix_text!r}")
        return cls(IPv4Address.parse(address_text), int(prefix_text))

    @property
    def netmask(self) -> IPv4Address:
        """The subnet mask as an address, for example ``255.255.255.192``."""
        return IPv4Address(netmask_value(self.prefix_length))

    @property
    def network_address(self) -> IPv4Address:
        """The first number in the subnet, with the host bits cleared."""
        return IPv4Address(network_value(self.address.value, self.prefix_length))

    @property
    def broadcast_address(self) -> IPv4Address:
        """The last number in the subnet, with the host bits set."""
        return IPv4Address(broadcast_value(self.address.value, self.prefix_length))

    @property
    def total_addresses(self) -> int:
        """Count of numbers in the subnet, including network and broadcast."""
        return total_address_count(self.prefix_length)

    @property
    def usable_host_count(self) -> int:
        """How many interfaces can be numbered from this subnet."""
        return usable_host_count(self.prefix_length)

    @property
    def usable_host_range(self) -> HostRange:
        """Inclusive addresses that can be assigned to interfaces.

        For a normal subnet the range excludes the network and broadcast
        numbers. ``/31`` uses both of its addresses. ``/32`` uses its only
        address.
        """
        network = self.network_address.value
        if self.prefix_length == 32:
            first = last = network
        elif self.prefix_length == 31:
            first = network
            last = network + 1
        else:
            first = network + 1
            last = self.broadcast_address.value - 1
        return HostRange(IPv4Address(first), IPv4Address(last), self.usable_host_count)

    def contains(self, address: IPv4Address | str) -> bool:
        """Return whether ``address`` is inside this subnet."""
        value = address.value if isinstance(address, IPv4Address) else parse_ipv4(address)
        return network_value(value, self.prefix_length) == self.network_address.value

    def __str__(self) -> str:
        return f"{self.network_address}/{self.prefix_length}"

    def __repr__(self) -> str:
        return f"IPv4Network.parse('{self.address}/{self.prefix_length}')"


def same_subnet(first: str, second: str, prefix_length: int) -> bool:
    """Return whether two dotted addresses share a subnet of the given prefix.

    Both addresses are masked with the same prefix. They are on one subnet
    when the surviving network bits are equal. ``192.168.10.1`` and
    ``192.168.10.200`` share a ``/24`` and do not share a ``/25``.
    """
    mask = netmask_value(prefix_length)
    return (parse_ipv4(first) & mask) == (parse_ipv4(second) & mask)
