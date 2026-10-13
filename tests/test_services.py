"""Exercise native action handlers with linked-account and response boundaries."""
import importlib.util
from pathlib import Path
import sys
import types
import unittest
from unittest.mock import AsyncMock, Mock, patch


class Unauthorized(Exception):
    def __init__(self, *args, **kwargs):
        super().__init__(*args)


class HomeAssistantError(Exception):
    pass


root = Path(__file__).parents[1] / "custom_components" / "clm"
package_name = "clm_service_test"
spec = importlib.util.spec_from_file_location(package_name, root / "__init__.py", submodule_search_locations=[str(root)])
module = importlib.util.module_from_spec(spec)
sys.modules[package_name] = module
core = types.ModuleType("homeassistant.core")
core.Context = lambda **kwargs: types.SimpleNamespace(id="root", **kwargs)
core.SupportsResponse = types.SimpleNamespace(OPTIONAL="optional", ONLY="only")
exceptions = types.ModuleType("homeassistant.exceptions")
exceptions.HomeAssistantError = HomeAssistantError
exceptions.Unauthorized = Unauthorized
http = types.ModuleType("homeassistant.helpers.aiohttp_client")
http.async_get_clientsession = Mock()
storage = types.ModuleType("homeassistant.helpers.storage")
storage.Store = Mock()
permissions = types.ModuleType("homeassistant.auth.permissions.const")
permissions.POLICY_CONTROL = "control"
websocket = types.ModuleType(f"{package_name}.websocket")
websocket.register = Mock()
with patch.dict(sys.modules, {"homeassistant.core": core, "homeassistant.exceptions": exceptions,
                              "homeassistant.helpers.aiohttp_client": http, "homeassistant.helpers.storage": storage,
                              "homeassistant.auth.permissions.const": permissions,
                              websocket.__name__: websocket}):
    spec.loader.exec_module(module)


class ServicesTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.calls = {}
        self.user = types.SimpleNamespace(is_active=True)
        self.client = types.SimpleNamespace(request=AsyncMock())
        self.runtime = module.Runtime(self.client, Mock(), {"actor": "individual-credential"})
        entry = types.SimpleNamespace(domain="clm", runtime_data=self.runtime)
        self.hass = types.SimpleNamespace(
            auth=types.SimpleNamespace(async_get_user=AsyncMock(return_value=self.user)),
            config_entries=types.SimpleNamespace(async_get_entry=Mock(return_value=entry)),
            services=types.SimpleNamespace(async_register=lambda domain, name, handler, **kwargs: self.calls.update({name:(handler, kwargs)})),
        )
        with patch.dict(sys.modules, {websocket.__name__: websocket, "homeassistant.core":core, "homeassistant.exceptions":exceptions}):
            await module.async_setup(self.hass, {})
        self.result = {"id":"run-1", "status":"completed", "code":200, "duplicate":False, "private":"must not return"}
        self.bindings = [{"id":"workflow-binding", "operation":"automation.run", "personal":False, "expires":123},
                         {"id":"private-binding", "operation":"automation.run", "personal":True, "expires":123},
                         {"id":"leaf-binding", "operation":"server.start", "personal":False, "expires":123}]
        async def request(route, payload, token):
            self.assertEqual("individual-credential", token)
            return self.bindings if route == "bindings/list" else self.result
        self.client.request.side_effect = request

    def call(self, binding="workflow-binding", actor="actor", parent=None, **data):
        return types.SimpleNamespace(data={"config_entry_id":"connection", "binding":binding, **data},
                                     context=types.SimpleNamespace(user_id=actor, parent_id=parent))

    async def test_ha_triggered_native_action_sends_pin_for_one_link_without_a_clm_workflow(self):
        payloads = []
        async def request(route, payload, token):
            self.assertEqual("native/actions", route)
            self.assertEqual("individual-credential", token)
            payloads.append(dict(payload))
            return {"id":"native-request", "status":"accepted", "duplicate":False, "urls":["private"]}
        self.client.request.side_effect = request
        result = await self.calls["start_session"][0](self.call(actor=None, device="Living room", pin="1234", preset_name="Sample", incognito=True))
        self.assertEqual({"id":"native-request", "status":"accepted", "duplicate":False}, result)
        self.assertEqual("1234", payloads[0]["pin"])
        self.assertEqual("Sample", payloads[0]["name"])
        self.assertTrue(payloads[0]["incognito"])
        self.assertEqual("start_session", payloads[0]["operation"])
        self.assertNotIn("pin", self.client.request.call_args.args[1])

    async def test_unattended_multi_account_calls_never_choose_an_owner_implicitly(self):
        self.runtime.links["second"] = "second-credential"
        with self.assertRaises(HomeAssistantError):
            await self.calls["open_clm"][0](self.call(actor=None, device="Living room", pin="1234"))
        self.client.request.assert_not_awaited()

    async def test_interactive_native_action_cannot_borrow_another_account(self):
        with self.assertRaises(Unauthorized):
            await self.calls["open_clm"][0](self.call(device="Living room", pin="1234", account_user_id="other"))
        self.client.request.assert_not_awaited()

    async def test_workflow_uses_linked_credential_once_and_returns_only_outcome(self):
        result = await self.calls["run_workflow"][0](self.call())
        self.assertEqual({key:self.result[key] for key in ("id", "status", "code", "duplicate")}, result)
        requests = self.client.request.call_args_list
        self.assertEqual(["bindings/list", "bindings/invoke"], [call.args[0] for call in requests])
        self.assertEqual("workflow-binding", requests[-1].args[1]["binding"])
        from uuid import UUID
        UUID(requests[-1].args[1]["key"])
        self.assertEqual("optional", self.calls["run_workflow"][1]["supports_response"])

    async def test_notification_workflow_keeps_push_intent_and_credentials_inside_clm(self):
        self.result.update(status="accepted", code=202, endpoint="private-endpoint", title="private-title", pin="private-pin")
        result = await self.calls["run_workflow"][0](self.call())
        self.assertEqual({"id", "status", "code", "duplicate"}, set(result))
        invoked = self.client.request.call_args_list[-1]
        self.assertEqual({"binding", "key"}, set(invoked.args[1]))
        self.assertNotIn("notification", self.calls)
        self.assertNotIn("endpoint", repr(result))

    async def test_wrong_kind_personal_and_unknown_bindings_cannot_run(self):
        for binding in ("leaf-binding", "private-binding", "unknown"):
            with self.assertRaises(Unauthorized):
                await self.calls["run_workflow"][0](self.call(binding))
        self.assertTrue(all(call.args[0] == "bindings/list" for call in self.client.request.call_args_list))

    async def test_anonymous_workflow_calls_are_denied_even_with_registered_parent(self):
        self.runtime.authority.register("approved-parent", "actor", ["workflow-binding"])
        for parent in (None, "approved-parent"):
            with self.assertRaises(Unauthorized):
                await self.calls["run_workflow"][0](self.call(actor=None, parent=parent))
        self.client.request.assert_not_awaited()

    async def test_unlinked_inactive_closed_and_unloaded_connections_are_denied(self):
        for state in ("unlinked", "inactive", "closed", "unloaded"):
            with self.subTest(state=state):
                self.runtime.links={"actor":"individual-credential"}; self.user.is_active=True; self.runtime.closed=False
                self.hass.config_entries.async_get_entry.return_value=types.SimpleNamespace(domain="clm",runtime_data=self.runtime)
                if state == "unlinked": self.runtime.links.clear()
                if state == "inactive": self.user.is_active=False
                if state == "closed": self.runtime.closed=True
                if state == "unloaded": self.hass.config_entries.async_get_entry.return_value=None
                with self.assertRaises((Unauthorized,HomeAssistantError)):
                    await self.calls["run_workflow"][0](self.call())
        self.client.request.assert_not_awaited()

    async def test_failed_workflow_surfaces_failure_without_retry(self):
        self.result.update(status="failed",code=409)
        with self.assertRaisesRegex(HomeAssistantError,"CLM workflow failed"):
            await self.calls["run_workflow"][0](self.call())
        self.assertEqual(1,sum(call.args[0]=="bindings/invoke" for call in self.client.request.call_args_list))

    async def test_listing_defaults_to_nonpersonal_workflows_and_returns_no_details(self):
        self.bindings[0]["target"]="private target details"
        result=await self.calls["list_bindings"][0](self.call())
        self.assertEqual({"bindings":[{"id":"workflow-binding","operation":"automation.run","expires":123,"pending":False}]},result)
        result=await self.calls["list_bindings"][0](self.call(workflows_only=False))
        self.assertEqual(["workflow-binding","leaf-binding"],[item["id"] for item in result["bindings"]])
        self.assertEqual("only",self.calls["list_bindings"][1]["supports_response"])

    async def test_workflow_waiting_for_approval_in_clm_is_not_run(self):
        self.bindings[0]["pending"]=True
        with self.assertRaisesRegex(HomeAssistantError,"Allow this in CLM first"):
            await self.calls["run_workflow"][0](self.call())
        self.assertEqual(["bindings/list"],[call.args[0] for call in self.client.request.await_args_list])

    async def test_anonymous_listing_is_denied(self):
        with self.assertRaises(Unauthorized):
            await self.calls["list_bindings"][0](self.call(actor=None))
        self.client.request.assert_not_awaited()

    async def test_remote_failure_is_neutral_and_not_retried(self):
        self.client.request.side_effect=module.CompanionError()
        with self.assertRaisesRegex(HomeAssistantError,"CLM request failed"):
            await self.calls["run_workflow"][0](self.call())
        self.assertEqual(1,self.client.request.await_count)

    async def test_existing_invoke_keeps_bounded_bridge_leaf_execution(self):
        self.runtime.authority.register("approved-parent","actor",["leaf-binding"])
        result=await self.calls["invoke"][0](self.call("leaf-binding",actor=None,parent="approved-parent"))
        self.assertEqual("completed",result["status"])
        self.assertEqual("bindings/invoke",self.client.request.call_args.args[0])

    def test_service_schemas_reject_arbitrary_user_or_pin_fields(self):
        for name in ("run_workflow","list_bindings","invoke"):
            with self.assertRaises(Exception):
                self.calls[name][1]["schema"]({"config_entry_id":"connection","binding":"workflow-binding","user_id":"other","pin":"1234"})
