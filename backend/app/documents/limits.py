"""Bound multipart requests before application-level file validation."""

from fastapi import HTTPException, Request
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from app.core.errors import error_response


class UploadLimitMiddleware:
    def __init__(self, app: ASGIApp, max_body_bytes: int):
        self.app = app
        self.limit = max_body_bytes

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if (
            scope["type"] != "http"
            or scope["method"] != "POST"
            or not scope["path"].startswith("/api/v1/documents")
        ):
            await self.app(scope, receive, send)
            return
        headers = dict(scope.get("headers", []))
        length = headers.get(b"content-length")
        try:
            too_large = length is not None and int(length) > self.limit
        except ValueError:
            await error_response(Request(scope), 400, "BAD_REQUEST", "Invalid content length")(
                scope, receive, send
            )
            return
        if too_large:
            await error_response(
                Request(scope),
                413,
                "UPLOAD_TOO_LARGE",
                "Request exceeds the configured upload limit",
            )(scope, receive, send)
            return
        consumed = 0

        async def bounded_receive() -> Message:
            nonlocal consumed
            message = await receive()
            if message["type"] == "http.request":
                consumed += len(message.get("body", b""))
                if consumed > self.limit:
                    raise HTTPException(413, "Request exceeds the configured upload limit")
            return message

        await self.app(scope, bounded_receive, send)
