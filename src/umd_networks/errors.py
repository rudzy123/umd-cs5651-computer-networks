"""Error types that distinguish bad input from ordinary socket timeouts.

Timeouts stay ``TimeoutError``. A ``ProtocolError`` means the bytes that did
arrive broke an agreement the peers had already made, such as a length prefix
or a DNS name. Keeping those separate is what lets a caller retry a slow peer
without treating a corrupt frame as a delay.
"""

from __future__ import annotations


class NetworkLabError(Exception):
    """Base for laboratory failures that are not socket timeouts."""


class ProtocolError(NetworkLabError):
    """A frame or message violated the application protocol."""


class IPv4AddressError(NetworkLabError, ValueError):
    """An IPv4 address, mask, or prefix was not valid."""


class DnsFormatError(NetworkLabError, ValueError):
    """DNS bytes did not follow the message format used by this laboratory."""


class HttpError(NetworkLabError):
    """An HTTP request was malformed or cannot be handled.

    ``status`` and ``reason`` are the response the server should return.
    """

    def __init__(self, status: int, reason: str) -> None:
        super().__init__(f"{status} {reason}")
        self.status = status
        self.reason = reason
