"""Unit tests for isolated HTTPS transport with fake I/O."""

import importlib.util
from pathlib import Path
import sys
import types
import unittest

root = Path(__file__).parents[1] / "custom_components" / "clm"
package = types.ModuleType("clm_transport_test")
package.__path__ = [str(root)]
sys.modules[package.__name__] = package
for name in ("const", "client"):
    spec = importlib.util.spec_from_file_location(f"{package.__name__}.{name}", root / f"{name}.py")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
client = sys.modules[f"{package.__name__}.client"]


class Response:
    def __init__(self, status=200, blocks=None):
        self.status = status
        self.headers = {"Allow":"POST"} if status == 405 else {}
        self.blocks = blocks or [b'{"ok":true}']
        self.content = self
    async def __aenter__(self):
        return self
    async def __aexit__(self, *args):
        return False
    async def iter_chunked(self, size):
        for block in self.blocks:
            yield block


class Session:
    def __init__(self, response):
        self.response = response
        self.calls = []
    def post(self, url, **kwargs):
        self.calls.append((url, kwargs))
        return self.response
    def get(self, url, **kwargs):
        self.calls.append((url, kwargs))
        return self.response


class ClientTests(unittest.IsolatedAsyncioTestCase):
    async def test_installation_request_does_not_inherit_account_credentials(self):
        session = Session(Response())
        transport = client.CompanionClient(session, "https://api.example")
        self.assertEqual({"ok": True}, await transport.request("installations", {"audience": "https://hub.example"}))
        self.assertEqual({}, session.calls[0][1]["headers"])
        self.assertFalse(session.calls[0][1]["allow_redirects"])

    async def test_remote_error_is_neutral_and_never_retried(self):
        session = Session(Response(403, [b'{"detail":"private label"}']))
        with self.assertRaises(client.CompanionError) as error:
            await client.CompanionClient(session, "https://api.example").request("bindings/invoke", {}, "credential")
        self.assertNotIn("private", str(error.exception))
        self.assertEqual(1, len(session.calls))

    async def test_redirects_and_large_responses_are_denied(self):
        for response in (Response(302), Response(200, [b'x' * (1024 * 1024 + 1)])):
            with self.assertRaises(client.CompanionError):
                await client.CompanionClient(Session(response), "https://api.example").request("capabilities", {}, "credential")

    async def test_client_is_not_a_general_api_proxy(self):
        session = Session(Response())
        with self.assertRaises(client.CompanionError):
            await client.CompanionClient(session, "https://api.example").request("../admin", {})
        self.assertEqual([], session.calls)

    def test_origin_rejects_credentials_paths_and_insecure_transport(self):
        for value in ("http://api.example", "https://user:secret@api.example", "https://api.example/path", "https://api.example?token=secret"):
            with self.assertRaises(ValueError):
                client.https_origin(value)

    async def test_internal_docker_transport_keeps_exact_endpoint_and_no_redirects(self):
        session = Session(Response())
        transport = client.CompanionClient(session, "http://clm-api:8080/")
        await transport.request("capabilities", {}, "credential")
        self.assertEqual("http://clm-api:8080/api/companion-transport/capabilities", session.calls[0][0])
        self.assertFalse(session.calls[0][1]["allow_redirects"])

    def test_explicit_http_origins_still_reject_non_web_urls_and_secrets(self):
        for value in ("ftp://server", "http://user:secret@server", "http://server/path", "http://server?token=secret", "http://server/#token", "http://server:99999", "http://server\n"):
            with self.assertRaises(ValueError):
                client.web_origin(value, allow_http=True)

    async def test_compatibility_probe_is_read_only_and_requires_the_new_post_route(self):
        session = Session(Response(405))
        await client.CompanionClient(session,"http://clm-api:8080").check_transport()
        self.assertEqual("http://clm-api:8080/api/companion-transport/installations/origins",session.calls[0][0])
        self.assertNotIn("headers",session.calls[0][1])
        self.assertFalse(session.calls[0][1]["allow_redirects"])
        with self.assertRaises(client.CompanionError) as error:
            await client.CompanionClient(Session(Response(404)),"http://clm-api:8080").check_transport()
        self.assertEqual("unsupported_server",error.exception.reason)

    async def test_invalid_backend_response_and_wrong_service_have_specific_neutral_reasons(self):
        for response, reason in ((Response(404),"not_companion_api"),(Response(200,[b'<html>web app</html>']),"invalid_server_response")):
            with self.assertRaises(client.CompanionError) as error:
                await client.CompanionClient(Session(response),"http://clm-api:8080").request("installations",{})
            self.assertEqual(reason,error.exception.reason)
            self.assertEqual("",str(error.exception))
