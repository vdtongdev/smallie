import socket

import pytest

from app.core.config import get_settings
from app.core.endpoint import UnsafeEndpoint, check_endpoint


def resolve_to(monkeypatch, *addresses):
    def fake(host, port, *args, **kwargs):
        return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", (a, port or 0)) for a in addresses]
    monkeypatch.setattr(socket, "getaddrinfo", fake)


def test_public_https_endpoint_is_allowed(monkeypatch):
    resolve_to(monkeypatch, "104.18.0.1")
    check_endpoint("https://api.deepseek.com/v1")


def test_http_is_refused(monkeypatch):
    resolve_to(monkeypatch, "104.18.0.1")
    with pytest.raises(UnsafeEndpoint):
        check_endpoint("http://api.deepseek.com/v1")


def test_userinfo_is_refused(monkeypatch):
    resolve_to(monkeypatch, "104.18.0.1")
    with pytest.raises(UnsafeEndpoint):
        check_endpoint("https://a:b@api.deepseek.com/v1")


@pytest.mark.parametrize("address", [
    "10.0.0.5", "172.16.0.1", "192.168.1.1", "127.0.0.1", "169.254.169.254", "0.0.0.0",
    "224.0.0.1", "::1", "fd00::1", "fe80::1", "::ffff:10.0.0.1",
])
def test_private_and_internal_addresses_are_refused(monkeypatch, address):
    resolve_to(monkeypatch, address)
    with pytest.raises(UnsafeEndpoint):
        check_endpoint("https://models.example.com/v1")


def test_any_private_address_among_several_is_refused(monkeypatch):
    resolve_to(monkeypatch, "104.18.0.1", "10.0.0.5")
    with pytest.raises(UnsafeEndpoint):
        check_endpoint("https://models.example.com/v1")


def test_unresolvable_host_is_refused(monkeypatch):
    def fail(*args, **kwargs):
        raise socket.gaierror("no such host")
    monkeypatch.setattr(socket, "getaddrinfo", fail)
    with pytest.raises(UnsafeEndpoint):
        check_endpoint("https://nope.invalid/v1")


def test_private_allowed_in_dev(monkeypatch):
    monkeypatch.setattr(get_settings(), "allow_private_model_urls", True)
    resolve_to(monkeypatch, "127.0.0.1")
    check_endpoint("http://localhost:11434/v1")
