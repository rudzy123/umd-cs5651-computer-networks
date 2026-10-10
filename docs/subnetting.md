# IPv4 addresses and subnets

An IPv4 address is a 32-bit integer. Dotted decimal is only a way to write it:
the integer `0xC0A80137` is `192.168.1.55` because those four bytes are 192,
168, 1, and 55. `parse_ipv4` accepts exactly four decimal octets, each from 0
through 255, and rejects leading zeros so `01` is not mistaken for an octal
number.

## The mask

A subnet mask has the same width as the address. The prefix length is how many
leading bits are ones. `/26` means 26 ones and 6 zeros:

```text
255.255.255.192    11111111.11111111.11111111.11000000
```

The ones are the **network** bits. The zeros are the **host** bits. Two
addresses are on the same subnet when they agree on the network bits.
`same_subnet("192.168.10.1", "192.168.10.200", 24)` is true. The same pair with
prefix 25 is false, because `/25` splits the last octet at 128.

The implementation is one bitwise AND:

```text
network = address AND mask
```

`IPv4Network.parse` allows host bits in the address you type. It then clears
them. `192.168.1.55/26` is the subnet `192.168.1.0/26`, not a different network
that starts at .55.

## Network, broadcast, and usable hosts

For a prefix of `/30` or shorter, two host numbers are reserved:

* all host bits zero: the network address, which names the subnet
* all host bits one: the directed broadcast address

Every remaining number can be assigned to an interface. The usable count is
`2^(32 - prefix) - 2`.

`/31` is the exception in RFC 3021. A point-to-point link has two addresses and
no broadcast. Both are usable. `/32` names a single interface. `broadcast_address`
in this package is still the bitwise top of the range, and `usable_host_range`
documents when that top address is assignable.

The whole IPv4 space is `0.0.0.0/0`: 2^32 numbers, of which two are the
all-zero and all-ones values. The code computes that case; it does not allocate
it.

## A /26, worked by hand

Take `192.168.1.55/26`.

```text
address   192.168.1.55     last octet 0011 0111
mask      255.255.255.192  last octet 1100 0000
AND       192.168.1.0      last octet 0000 0000     network
host bits                    6 bits, values 0..63
broadcast 192.168.1.63     last octet 0011 1111
usable    192.168.1.1 through 192.168.1.62
count     64 - 2 = 62
```

Check it with the library:

```python
from umd_networks import IPv4Network

network = IPv4Network.parse("192.168.1.55/26")
print(network)                    # 192.168.1.0/26
print(network.netmask)            # 255.255.255.192
print(network.broadcast_address)  # 192.168.1.63
print(network.usable_host_range)  # 192.168.1.1-192.168.1.62
print(network.usable_host_count)  # 62
```

Another one worth doing on paper before trusting the function: `203.0.113.77/27`
lands in `203.0.113.64` through `203.0.113.95`, with usable hosts `.65` through
`.94` (30 of them). `203.0.113.0/24` is a documentation range. Arithmetic on it
does not send a packet.

## A /12, which is not "the second octet"

`172.16.0.0/12` does not end on an octet boundary. The mask is
`255.240.0.0`. The second octet's top four bits are network bits, so the
subnet runs from `172.16.0.0` through `172.31.255.255`. `172.16.5.10` and
`172.31.255.255` are together. `172.32.0.1` is not, because `32 & 240` is 32,
not 16.

## What the tests pin down

`tests/test_ipv4.py` covers malformed text (too few octets, 256, leading zeros,
whitespace, a slash stuffed into an address), prefixes outside `0..32`, `/0`,
`/30`, `/31`, `/32`, and a host address whose host bits are not zero. Several
cases are checked against Python's `ipaddress` module so the bitwise code has
an independent oracle. That module is a good production tool. The lab
reimplements the mask so the shift and the AND stay visible.
