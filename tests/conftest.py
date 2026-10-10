"""Keep the suite on the machine.

Name resolution of anything other than a literal address or ``localhost`` is
refused. Unclosed sockets fail the test that leaked them.
"""

from __future__ import annotations

import ipaddress
import socket
import warnings
from collections.abc import Iterator

import pytest


@pytest.fixture(autouse=True)
def _unclosed_sockets_fail() -> Iterator[None]:
    warnings.simplefilter("error", ResourceWarning)
    yield


@pytest.fixture(autouse=True)
def _no_live_dns(monkeypatch: pytest.MonkeyPatch) -> None:
    original = socket.getaddrinfo

    def guarded(host: object, *args: object, **kwargs: object) -> object:
        text = "" if host is None else str(host)
        if text not in {"", "localhost", "127.0.0.1", "::1"}:
            try:
                ipaddress.ip_address(text)
            except ValueError as exc:
                raise RuntimeError(f"tests refuse live name resolution for {text!r}") from exc
        return original(host, *args, **kwargs)

    monkeypatch.setattr(socket, "getaddrinfo", guarded)
