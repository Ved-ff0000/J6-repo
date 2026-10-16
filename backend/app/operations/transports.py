"""Server-configured transports. Signed webhooks pin public DNS results and never follow redirects."""

import asyncio
import hashlib
import hmac
import ipaddress
import json
import smtplib
import socket
import ssl
from email.message import EmailMessage
from typing import Protocol
from urllib.parse import urlsplit

import httpx

from app.core.config import Settings


class DeliveryError(Exception):
    pass


class EmailTransport(Protocol):
    async def send(self, to: str, payload: dict[str, object]) -> None: ...


class SMTPTransport:
    def __init__(self, settings: Settings):
        self.settings = settings

    async def send(self, to: str, payload: dict[str, object]) -> None:
        cfg = self.settings
        if not cfg.email_enabled:
            raise DeliveryError("EMAIL_DISABLED")
        message = EmailMessage()
        message["From"] = cfg.smtp_sender
        message["To"] = to
        message["Subject"] = "LeaseGuard workspace event"
        message.set_content(json.dumps(payload, sort_keys=True))

        def deliver() -> None:
            with smtplib.SMTP(cfg.smtp_host, cfg.smtp_port, timeout=10) as client:
                client.starttls(context=ssl.create_default_context())
                if cfg.smtp_username and cfg.smtp_password:
                    client.login(cfg.smtp_username, cfg.smtp_password.get_secret_value())
                client.send_message(message)

        await asyncio.to_thread(deliver)


def signature(secret: str, timestamp: str, body: bytes) -> str:
    return hmac.new(secret.encode(), timestamp.encode() + b"." + body, hashlib.sha256).hexdigest()


async def public_destination(url: str) -> tuple[str, str]:
    parsed = urlsplit(url)
    if (
        parsed.scheme != "https"
        or not parsed.hostname
        or parsed.username
        or parsed.password
        or parsed.fragment
        or parsed.port not in (None, 443)
    ):
        raise DeliveryError("WEBHOOK_URL_POLICY")
    addresses = await asyncio.to_thread(
        socket.getaddrinfo, parsed.hostname, 443, type=socket.SOCK_STREAM
    )
    ips = sorted({str(address[4][0]) for address in addresses})
    if not ips or any(not ipaddress.ip_address(ip).is_global for ip in ips):
        raise DeliveryError("WEBHOOK_DESTINATION_NOT_PUBLIC")
    return parsed.hostname, ips[0]


class SignedWebhook:
    def __init__(self, settings: Settings):
        self.settings = settings

    async def send(self, payload: dict[str, object], timestamp: str) -> None:
        cfg = self.settings
        if not cfg.webhook_enabled or not cfg.webhook_url or not cfg.webhook_signing_key:
            raise DeliveryError("WEBHOOK_DISABLED")
        url = cfg.webhook_url.get_secret_value()
        hostname, address = await public_destination(url)
        body = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
        # Connect to the checked IP, with original Host/SNI for certificate verification.
        pinned = httpx.URL(url).copy_with(host=address)
        async with httpx.AsyncClient(timeout=10, follow_redirects=False, trust_env=False) as client:
            response = await client.post(
                pinned,
                content=body,
                headers={
                    "Host": hostname,
                    "Content-Type": "application/json",
                    "X-LeaseGuard-Timestamp": timestamp,
                    "X-LeaseGuard-Signature": "v1="
                    + signature(cfg.webhook_signing_key.get_secret_value(), timestamp, body),
                    "Idempotency-Key": str(payload["delivery_id"]),
                },
                extensions={"sni_hostname": hostname},
            )
        if not 200 <= response.status_code < 300:
            raise DeliveryError("WEBHOOK_NON_SUCCESS")
