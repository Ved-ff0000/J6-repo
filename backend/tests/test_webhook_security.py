import asyncio
from unittest.mock import patch

import pytest

from app.operations.transports import DeliveryError, public_destination, signature


@pytest.mark.parametrize(
    "url",
    [
        "http://example.com",
        "https://user:secret@example.com",
        "https://example.com:444/path",
        "https://example.com/#frag",
    ],
)
def test_webhook_url_policy(url):
    with pytest.raises(DeliveryError):
        asyncio.run(public_destination(url))


@pytest.mark.parametrize("address", ["127.0.0.1", "169.254.169.254", "10.0.0.1", "::1"])
def test_webhook_rejects_private_loopback_metadata_and_ipv6(address):
    with (
        patch("socket.getaddrinfo", return_value=[(2, 1, 6, "", (address, 443))]),
        pytest.raises(DeliveryError),
    ):
        asyncio.run(public_destination("https://example.com/events"))


def test_webhook_signature_binds_timestamp_and_body():
    assert signature("test-key", "100", b"one") != signature("test-key", "101", b"one")
    assert signature("test-key", "100", b"one") != signature("test-key", "100", b"two")


def test_enabled_external_transports_require_explicit_configuration():
    from pydantic import ValidationError

    from app.core.config import Settings

    with pytest.raises(ValidationError):
        Settings(webhook_enabled=True)
    with pytest.raises(ValidationError):
        Settings(email_enabled=True)
    with pytest.raises(ValidationError):
        Settings(webhook_enabled=True, webhook_url="http://127.0.0.1", webhook_signing_key="x" * 32)
