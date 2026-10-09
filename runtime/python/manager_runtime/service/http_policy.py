from __future__ import annotations

from typing import Any

from . import gateway as _gateway
from .api import ApiError


class HardenedGatewayHandler(_gateway._Handler):
    """Connection/framing policy layered over the production gateway handler.

    `BaseHTTPRequestHandler` will otherwise leave bodies on GET/HEAD or unknown
    methods unread. On an HTTP/1.1 persistent connection, attacker-controlled
    bytes could then be parsed as a second request. This policy either proves a
    bodyless request has no body or closes the connection before reuse.
    """

    def _reject_body_on_bodyless_method(self) -> None:
        if self.headers.get_all("Transfer-Encoding"):
            self.close_connection = True
            raise ApiError(
                "unexpected_request_body",
                "request method does not accept a body",
                status=400,
            )
        lengths = self.headers.get_all("Content-Length") or []
        if not lengths:
            return
        if len(lengths) != 1:
            self.close_connection = True
            raise ApiError(
                "invalid_content_length",
                "Content-Length is invalid",
                status=400,
            )
        raw = lengths[0]
        if len(raw) > 20 or not raw.isascii() or not raw.isdigit():
            self.close_connection = True
            raise ApiError(
                "invalid_content_length",
                "Content-Length is invalid",
                status=400,
            )
        if int(raw) != 0:
            self.close_connection = True
            raise ApiError(
                "unexpected_request_body",
                "request method does not accept a body",
                status=400,
            )

    def _subject(self) -> str:
        values = self.headers.get_all("Authorization") or []
        if self.context.settings.auth_mode == "bearer":
            if len(values) != 1:
                raise ApiError(
                    "unauthorized",
                    "authentication is required",
                    status=401,
                )
            authorization = values[0]
        else:
            # Authentication is disabled only in development/testing. Ignore a
            # single client credential rather than accidentally promoting it to
            # identity, but reject ambiguous duplicates at the parser boundary.
            if len(values) > 1:
                raise ApiError(
                    "invalid_request",
                    "duplicate Authorization headers are not permitted",
                    status=400,
                )
            authorization = values[0] if values else None
        subject = self.context.authenticator.authenticate(authorization)
        if subject is None:
            raise ApiError(
                "unauthorized",
                "authentication is required",
                status=401,
            )
        return subject

    def do_GET(self) -> None:
        request_id = self._request_id()
        try:
            self._header_bounds()
            self._reject_body_on_bodyless_method()
        except ApiError as exc:
            extra = {"WWW-Authenticate": "Bearer"} if exc.status == 401 else None
            self._reply(
                exc.status,
                exc.envelope(request_id),
                request_id=request_id,
                extra_headers=extra,
            )
            return
        super().do_GET()

    def _method_not_allowed(self) -> None:
        # Unsupported methods have no parser contract in this service. Closing
        # avoids treating an unread body as another request on the connection.
        self.close_connection = True
        super()._method_not_allowed()


def install_http_policy() -> None:
    """Install once after the gateway module is loaded by the package."""

    if _gateway._Handler is not HardenedGatewayHandler:
        _gateway._Handler = HardenedGatewayHandler
