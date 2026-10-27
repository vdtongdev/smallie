"""Safety check for a user's OpenAI-compatible model server URL (SSRF guard)."""
import ipaddress
import socket
from urllib.parse import urlparse

from app.core.config import get_settings


class UnsafeEndpoint(ValueError):
    """The URL is not allowed as a model server; the message says why."""


# shortcut: checks resolution at build time only; DNS rebinding between check and request is not caught, pin the resolved IP in the transport if that matters
def check_endpoint(url: str) -> None:
    dev = get_settings().allow_private_model_urls
    parts = urlparse(url)
    if parts.scheme != "https" and not (dev and parts.scheme == "http"):
        raise UnsafeEndpoint("The address must start with https://")
    if not parts.hostname:
        raise UnsafeEndpoint("The address has no host name")
    if parts.username is not None or parts.password is not None:
        raise UnsafeEndpoint("The address must not contain a user name or password")
    if dev:
        return
    try:
        infos = socket.getaddrinfo(parts.hostname, parts.port or 443)
    except socket.gaierror:
        raise UnsafeEndpoint("The host name could not be resolved") from None
    for info in infos:
        addr = ipaddress.ip_address(info[4][0])
        if addr.version == 6 and addr.ipv4_mapped:
            addr = addr.ipv4_mapped
        if not addr.is_global or addr.is_multicast:
            raise UnsafeEndpoint("The address points to a private or internal network")
