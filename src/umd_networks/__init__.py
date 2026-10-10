"""Localhost network-programming laboratory for UMD CS 5651.

The public names are the pieces a lab report should call. Servers bind to
``127.0.0.1`` and tests select a free port by binding to port 0.
"""

from umd_networks.dns import (
    CLASS_IN,
    TYPE_A,
    TYPE_AAAA,
    TYPE_CNAME,
    TYPE_NS,
    DnsHeader,
    DnsMessage,
    DnsQuestion,
    DnsRecord,
    build_query,
    decode_name,
    encode_name,
    lookup_hostname,
    parse_message,
    type_name,
)
from umd_networks.errors import (
    DnsFormatError,
    HttpError,
    IPv4AddressError,
    NetworkLabError,
    ProtocolError,
)
from umd_networks.framing import (
    BoundaryDemonstration,
    FrameReader,
    demonstrate_message_boundaries,
    encode_frame,
    local_tcp_pair,
    write_frame,
)
from umd_networks.http_proto import (
    ECHO_PATH,
    HEALTH_PATH,
    HttpRequest,
    HttpServer,
    build_http_response,
    handle_http_request,
    parse_http_request,
)
from umd_networks.ipv4 import (
    HostRange,
    IPv4Address,
    IPv4Network,
    format_ipv4,
    parse_ipv4,
    same_subnet,
)
from umd_networks.tcp import TcpClient, TcpServer, echo_handler
from umd_networks.udp import DELIVERY_LIMITATIONS, UdpClient, UdpEchoServer

__all__ = [
    "CLASS_IN",
    "DELIVERY_LIMITATIONS",
    "ECHO_PATH",
    "HEALTH_PATH",
    "TYPE_A",
    "TYPE_AAAA",
    "TYPE_CNAME",
    "TYPE_NS",
    "BoundaryDemonstration",
    "DnsFormatError",
    "DnsHeader",
    "DnsMessage",
    "DnsQuestion",
    "DnsRecord",
    "FrameReader",
    "HostRange",
    "HttpError",
    "HttpRequest",
    "HttpServer",
    "IPv4Address",
    "IPv4AddressError",
    "IPv4Network",
    "NetworkLabError",
    "ProtocolError",
    "TcpClient",
    "TcpServer",
    "UdpClient",
    "UdpEchoServer",
    "build_http_response",
    "build_query",
    "decode_name",
    "demonstrate_message_boundaries",
    "echo_handler",
    "encode_frame",
    "encode_name",
    "format_ipv4",
    "handle_http_request",
    "local_tcp_pair",
    "lookup_hostname",
    "parse_http_request",
    "parse_ipv4",
    "parse_message",
    "same_subnet",
    "type_name",
    "write_frame",
]
