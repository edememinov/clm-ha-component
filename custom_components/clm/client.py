"""Bounded companion HTTPS transport with neutral errors."""

import asyncio
import json
from urllib.parse import urlsplit
from aiohttp import ClientError
from yarl import URL

from .const import MAX_RESPONSE, REQUEST_TIMEOUT


class CompanionError(Exception):
    """Never attach a remote body, URL, token or arguments to this error."""

    def __init__(self, reason="cannot_connect"):
        super().__init__()
        self.reason = reason


def web_origin(value: str, *, allow_http: bool = False) -> str:
    if any(ord(character) <= 32 for character in value):
        raise ValueError("Use a full web address without whitespace.")
    parsed = urlsplit(value)
    if (parsed.scheme not in (("https", "http") if allow_http else ("https",)) or not parsed.hostname or parsed.username or parsed.password
            or parsed.path not in ("", "/") or parsed.query or parsed.fragment):
        raise ValueError("Use a full web address without paths or credentials.")
    if parsed.port is not None and not 1 <= parsed.port <= 65535:
        raise ValueError("Use a valid port.")
    return str(URL(value).origin())


def https_origin(value: str) -> str:
    return web_origin(value)


class CompanionClient:
    """Credentials are supplied per call; no shared owner fallback exists."""

    def __init__(self, session, api_origin: str):
        self.session = session
        self.origin = web_origin(api_origin, allow_http=True)

    async def check_transport(self):
        """Probe the new POST-only route without creating any installation state."""
        try:
            async with asyncio.timeout(REQUEST_TIMEOUT):
                async with self.session.get(f"{self.origin}/api/companion-transport/installations/origins",
                                            allow_redirects=False) as response:
                    if response.status == 404:
                        raise CompanionError("unsupported_server")
                    if response.status != 405 or "POST" not in {value.strip() for value in response.headers.get("Allow", "").split(",")}:
                        raise CompanionError("server_denied" if response.status in (401, 403) else "not_companion_api")
        except CompanionError:
            raise
        except (TimeoutError, OSError, ClientError):
            raise CompanionError() from None

    async def request(self, route: str, payload: dict | list, credential: str | None = None) -> dict | list:
        if route not in {"installations", "installations/origins", "challenges", "challenges/exchange", "capabilities",
                         "catalog/query", "bindings", "bindings/list", "bindings/invoke",
                         "ui-sessions", "revoke", "external-automations", "external-events", "dispatches/poll", "dispatches/wait", "dispatches/acknowledge",
                         "native/actions", "native/devices",
                         "entities/sync", "entities/states", "entities/decision", "entities/offer", "entities/withdraw"}:
            raise CompanionError()
        headers = {"Authorization": f"Bearer {credential}"} if credential else {}
        try:
            async with asyncio.timeout(330 if route == "external-events" else REQUEST_TIMEOUT):
                async with self.session.post(f"{self.origin}/api/companion-transport/{route}",
                                             json=payload, headers=headers, allow_redirects=False) as response:
                    if response.status != 200:
                        reason = ("not_companion_api" if response.status in (404, 405) else
                            "server_denied" if response.status in (401, 403) else
                            "server_redirect" if 300 <= response.status < 400 else
                            "server_error" if response.status >= 500 else "server_rejected")
                        raise CompanionError(reason)
                    raw = bytearray()
                    async for block in response.content.iter_chunked(16384):
                        raw.extend(block)
                        if len(raw) > MAX_RESPONSE:
                            raise CompanionError()
                    try:
                        result = json.loads(raw)
                    except ValueError:
                        raise CompanionError("invalid_server_response") from None
                    if not isinstance(result, (dict, list)):
                        raise CompanionError("invalid_server_response")
                    return result
        except CompanionError:
            raise
        except (TimeoutError, OSError, ValueError, ClientError):
            raise CompanionError() from None
