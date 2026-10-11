"""Bounded companion HTTPS transport with neutral errors."""

import asyncio
import json
from urllib.parse import urlsplit
from aiohttp import ClientError
from yarl import URL

from .const import MAX_RESPONSE, REQUEST_TIMEOUT


class CompanionError(Exception):
    """Never attach a remote body, URL, token or arguments to this error."""


def web_origin(value: str, *, allow_http: bool = False) -> str:
    if any(ord(character) <= 32 for character in value):
        raise ValueError("Use an HTTPS origin.")
    parsed = urlsplit(value)
    if (parsed.scheme not in (("https", "http") if allow_http else ("https",)) or not parsed.hostname or parsed.username or parsed.password
            or parsed.path not in ("", "/") or parsed.query or parsed.fragment):
        raise ValueError("Use an HTTPS origin.")
    if parsed.port is not None and not 1 <= parsed.port <= 65535:
        raise ValueError("Use an HTTPS origin.")
    return str(URL(value).origin())


def https_origin(value: str) -> str:
    return web_origin(value)


class CompanionClient:
    """Credentials are supplied per call; no shared owner fallback exists."""

    def __init__(self, session, api_origin: str):
        self.session = session
        self.origin = web_origin(api_origin, allow_http=True)

    async def request(self, route: str, payload: dict | list, credential: str | None = None) -> dict | list:
        if route not in {"installations", "installations/origins", "challenges", "challenges/exchange", "capabilities",
                         "catalog/query", "bindings", "bindings/list", "bindings/invoke",
                         "ui-sessions", "revoke", "external-automations", "dispatches/poll", "dispatches/acknowledge"}:
            raise CompanionError()
        headers = {"Authorization": f"Bearer {credential}"} if credential else {}
        try:
            async with asyncio.timeout(REQUEST_TIMEOUT):
                async with self.session.post(f"{self.origin}/api/companion-transport/{route}",
                                             json=payload, headers=headers, allow_redirects=False) as response:
                    if response.status != 200:
                        raise CompanionError()
                    raw = bytearray()
                    async for block in response.content.iter_chunked(16384):
                        raw.extend(block)
                        if len(raw) > MAX_RESPONSE:
                            raise CompanionError()
                    result = json.loads(raw)
                    if not isinstance(result, (dict, list)):
                        raise CompanionError()
                    return result
        except CompanionError:
            raise
        except (TimeoutError, OSError, ValueError, ClientError):
            raise CompanionError() from None
