"""Setup/reconfigure behavior with narrow HA platform adapters."""

import importlib.util
from pathlib import Path
import sys
import types
import unittest
from unittest.mock import AsyncMock, Mock, patch


class FlowAdapter:
    def __init_subclass__(cls, **kwargs):
        super().__init_subclass__()
    def async_show_form(self, **kwargs):
        return {"type": "form", **kwargs}
    def async_create_entry(self, **kwargs):
        return {"type": "create_entry", **kwargs}
    def async_abort(self, **kwargs):
        return {"type": "abort", **kwargs}
    def add_suggested_values_to_schema(self, schema, values):
        return schema


root = Path(__file__).parents[1] / "custom_components" / "clm"
package = types.ModuleType("clm_flow_test")
package.__path__ = [str(root)]
sys.modules[package.__name__] = package
ha = types.ModuleType("homeassistant")
ha.config_entries = types.SimpleNamespace(ConfigFlow=FlowAdapter)
helpers = types.ModuleType("homeassistant.helpers")
helpers.selector = types.SimpleNamespace(
    TextSelector=Mock(side_effect=lambda config: str),
    TextSelectorConfig=Mock(side_effect=lambda **kwargs: kwargs),
    TextSelectorType=types.SimpleNamespace(URL="url"),
)
http = types.ModuleType("homeassistant.helpers.aiohttp_client")
http.async_get_clientsession = Mock(return_value=object())
with patch.dict(sys.modules, {"homeassistant": ha, "homeassistant.helpers": helpers, "homeassistant.helpers.aiohttp_client": http}):
    for name in ("const", "client", "addresses", "config_flow"):
        spec = importlib.util.spec_from_file_location(f"{package.__name__}.{name}", root / f"{name}.py")
        module = importlib.util.module_from_spec(spec)
        sys.modules[spec.name] = module
        spec.loader.exec_module(module)
    flow_module = module


class SetupTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.flow = flow_module.ClmConfigFlow()
        self.flow.hass = object()
        self.flow._async_current_entries = Mock(return_value=[])
        self.flow.async_set_unique_id = AsyncMock()
        self.flow._abort_if_unique_id_configured = Mock()
        self.input = {"api_origin": "http://clm-api:8080", "workspace_origin": "https://clm.example", "workspace_local_origin": "http://192.168.1.20:4200"}
        self.origins = ["http://192.168.1.10:8123", "https://ha.example"]
        self.client = Mock(request=AsyncMock(return_value={"version": 1, "audiences": self.origins, "installation": "installation", "credential": "transport"}))
        self.discovery = patch.object(flow_module, "discover_origins", return_value=(self.origins, self.origins[0]))
        self.transport = patch.object(flow_module, "CompanionClient", return_value=self.client)
        self.discovery.start(); self.transport.start()
        self.addCleanup(self.discovery.stop); self.addCleanup(self.transport.stop)

    async def test_setup_discovers_both_ha_addresses_without_a_required_ha_field(self):
        result = await self.flow.async_step_user(self.input)
        self.assertEqual("create_entry", result["type"])
        self.assertEqual(self.origins, result["data"]["ha_origins"])
        self.assertEqual(self.input["workspace_local_origin"], result["data"]["workspace_local_origin"])
        self.client.request.assert_awaited_once_with("installations", {"audience": self.origins[0], "audiences": self.origins})

    async def test_reconfigure_keeps_installation_credentials_and_updates_only_addresses(self):
        entry = types.SimpleNamespace(data={**self.input, "auth_token": "existing-transport", "installation": "existing-installation", "ha_origin": "https://ha.example"})
        self.flow._get_reconfigure_entry = Mock(return_value=entry)
        self.flow.async_update_reload_and_abort = Mock(return_value={"type": "abort", "reason": "reconfigure_successful"})
        result = await self.flow.async_step_reconfigure(self.input)
        self.assertEqual("reconfigure_successful", result["reason"])
        self.client.request.assert_awaited_once_with("installations/origins", {"audiences": self.origins}, "existing-transport")
        updates = self.flow.async_update_reload_and_abort.call_args.kwargs["data_updates"]
        self.assertNotIn("auth_token", updates)
        self.assertNotIn("installation", updates)

    async def test_no_discovered_address_produces_helpful_error_without_registering(self):
        with patch.object(flow_module, "discover_origins", return_value=([], None)):
            result = await self.flow.async_step_user(self.input)
        self.assertEqual("no_ha_url", result["errors"]["base"])
        self.client.request.assert_not_called()

    async def test_old_backend_is_rejected_instead_of_silently_ignoring_multiple_addresses(self):
        self.client.request.return_value = {"version": 1, "installation": "installation", "credential": "transport"}
        result = await self.flow.async_step_user(self.input)
        self.assertEqual("unsupported_version", result["reason"])
