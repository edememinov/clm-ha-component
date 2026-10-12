"""Exercise event ingress, prompt delivery and cancellation without a live HA host."""
import ast
import asyncio
from pathlib import Path
from time import perf_counter
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, Mock

source = Path(__file__).parents[1] / "custom_components" / "clm" / "bridge.py"
tree = ast.parse(source.read_text(encoding="utf-8"))
names = {"bridge", "forward_events", "forward_event", "receive_dispatches", "deliver", "fingerprint"}
nodes = [node for node in tree.body if isinstance(node, (ast.AsyncFunctionDef, ast.FunctionDef)) and node.name in names
         or isinstance(node, ast.Import) and all(alias.name in {"asyncio", "hashlib", "json"} for alias in node.names)]


class CompanionError(Exception):
    pass


class BridgeDeliveryTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.scope = {"CompanionError": CompanionError, "HomeAssistantError": CompanionError,
                      "callback": lambda method: method, "DATA_COMPONENT": "automation", "POLICY_CONTROL": "control"}
        exec(compile(ast.Module(body=nodes, type_ignores=[]), str(source), "exec"), self.scope)
        self.user = SimpleNamespace(is_active=True, permissions=SimpleNamespace(check_entity=Mock(return_value=True)))
        self.runtime = SimpleNamespace(closed=False, links={"actor": "account-token"},
                                       authority=SimpleNamespace(contains=Mock(return_value=False)), client=SimpleNamespace(request=AsyncMock()))
        self.entry = SimpleNamespace(runtime_data=self.runtime, data={"auth_token": "transport-token"})
        self.context = SimpleNamespace(id="event-1", parent_id=None)
        self.entity = SimpleNamespace(is_on=True, entity_id="automation.sample", context=self.context, raw_config={"actions": []})
        self.listeners = {}
        def listen(event, handler):
            self.listeners[event] = handler
            return lambda: self.listeners.pop(event, None)
        self.hass = SimpleNamespace(auth=SimpleNamespace(async_get_user=AsyncMock(return_value=self.user)),
                                    data={"automation": SimpleNamespace(get_entity=Mock(return_value=self.entity))},
                                    bus=SimpleNamespace(async_listen=listen))

    async def test_real_automation_start_is_forwarded_in_milliseconds_and_unsubscribes(self):
        forwarded = asyncio.Event()
        async def request(route, payload, token):
            self.assertEqual("external-events", route)
            self.assertEqual("account-token", token)
            self.assertEqual({"automation", "fingerprint", "key"}, set(payload))
            self.assertEqual("event-1", payload["key"])
            forwarded.set()
        self.runtime.client.request.side_effect = request
        task = asyncio.create_task(self.scope["forward_events"](self.hass, self.entry))
        await asyncio.sleep(0)
        start = perf_counter()
        self.listeners["automation_triggered"](SimpleNamespace(context=self.context, data={"entity_id": self.entity.entity_id, "name": "never forwarded"}))
        await asyncio.wait_for(forwarded.wait(), 1)
        elapsed = (perf_counter() - start) * 1000
        print(f"HA event to outbound request: {elapsed:.3f} ms")
        self.assertLess(elapsed, 100)
        task.cancel()
        with self.assertRaises(asyncio.CancelledError):
            await task
        self.assertEqual({}, self.listeners)

    async def test_synthetic_disabled_and_bridge_events_are_not_forwarded(self):
        task = asyncio.create_task(self.scope["forward_events"](self.hass, self.entry))
        await asyncio.sleep(0)
        listener = self.listeners["automation_triggered"]
        listener(SimpleNamespace(context=SimpleNamespace(id="forged", parent_id=None), data={"entity_id": self.entity.entity_id}))
        self.entity.is_on = False
        listener(SimpleNamespace(context=self.context, data={"entity_id": self.entity.entity_id}))
        self.entity.is_on = True
        self.runtime.authority.contains.return_value = True
        listener(SimpleNamespace(context=self.context, data={"entity_id": self.entity.entity_id}))
        await asyncio.sleep(0)
        self.runtime.client.request.assert_not_awaited()
        task.cancel()
        with self.assertRaises(asyncio.CancelledError):
            await task

    async def test_unlinked_inactive_and_unpermitted_accounts_never_forward(self):
        for condition in ("closed", "missing", "inactive", "unpermitted"):
            self.runtime.closed = condition == "closed"
            self.hass.auth.async_get_user.return_value = None if condition == "missing" else self.user
            self.user.is_active = condition != "inactive"
            self.user.permissions.check_entity.return_value = condition != "unpermitted"
            await self.scope["forward_event"](self.hass, self.runtime, {"automation": "automation.sample"})
        self.runtime.client.request.assert_not_awaited()

    async def test_one_accounts_long_workflow_does_not_delay_another_accounts_event(self):
        self.runtime.links["second"] = "second-token"
        release = asyncio.Event()
        sent = asyncio.Event()
        async def request(route, payload, credential):
            if credential == "account-token":
                await release.wait()
            else:
                sent.set()
        self.runtime.client.request.side_effect = request
        task = asyncio.create_task(self.scope["forward_event"](self.hass, self.runtime, {"automation": "automation.sample"}))
        await asyncio.wait_for(sent.wait(), 1)
        self.assertFalse(task.done())
        release.set()
        await task

    async def test_wait_response_dispatches_without_a_sleep_and_does_not_block_on_a_long_run(self):
        delivered = asyncio.Event()
        release = asyncio.Event()
        calls = 0
        async def request(route, payload, token):
            nonlocal calls
            if route == "dispatches/wait":
                self.assertEqual("transport-token", token)
                calls += 1
                if calls < 3:
                    return [{"id": calls}]
                await release.wait()
                return []
            return {}
        async def dispatch(hass, entry, delivery):
            if delivery["id"] == 1:
                await release.wait()
            else:
                delivered.set()
        self.runtime.client.request.side_effect = request
        self.scope["dispatch"] = dispatch
        start = perf_counter()
        task = asyncio.create_task(self.scope["receive_dispatches"](self.hass, self.entry))
        await asyncio.wait_for(delivered.wait(), 1)
        elapsed = (perf_counter() - start) * 1000
        print(f"HA wait response to concurrent dispatch: {elapsed:.3f} ms")
        self.assertLess(elapsed, 100)
        task.cancel()
        with self.assertRaises(asyncio.CancelledError):
            await task

    async def test_failed_acknowledgement_is_not_retried(self):
        self.scope["dispatch"] = AsyncMock()
        self.runtime.client.request.side_effect = CompanionError()
        await self.scope["deliver"](self.hass, self.entry, {"id": "run"})
        self.runtime.client.request.assert_awaited_once()
