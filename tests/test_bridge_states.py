"""State forwarding: only entities CLM's workflows use go, at once; every press goes; new or removed entities wake the exchange."""
import ast
import asyncio
import itertools
from pathlib import Path
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock

source = Path(__file__).parents[1] / "custom_components" / "clm" / "bridge.py"
tree = ast.parse(source.read_text(encoding="utf-8"))
nodes = [node for node in tree.body if isinstance(node, ast.AsyncFunctionDef) and node.name in ("forward_states", "send_states")]
nodes += [node for node in tree.body if isinstance(node, ast.FunctionDef) and node.name == "separate"]


class CompanionError(Exception):
    pass


def state(entity_id, value):
    return SimpleNamespace(entity_id=entity_id, state=value)


class ForwardStatesTests(unittest.IsolatedAsyncioTestCase):
    async def start(self, watch):
        self.sent_marks = []
        self.fired = {}
        devices = SimpleNamespace(PREFIX="device.", EVENT_FIRED="clm_device_trigger",
                                  snapshot=lambda hass, reference, fired: {"id": reference, "state": fired["at"], "attributes": {"trigger": fired["key"]}})
        scope = {"asyncio": asyncio, "itertools": itertools, "CompanionError": CompanionError, "callback": lambda method: method, "devices": devices,
                 "EVENT_STATE_CHANGED": "state_changed", "SEND_GAP": 0.25,
                 "entities": SimpleNamespace(
                     DOMAINS=("light", "sensor", "event", "binary_sensor"), domain_of=lambda entity: entity.split(".")[0],
                     watched=lambda runtime, actor, entity: entity in watch.get(actor, ()),
                     snapshot=lambda value: {"id": value.entity_id, "state": value.state},
                     remember_sent=lambda runtime, actor, snaps: self.sent_marks.extend(snap["id"] for snap in snaps))}
        exec(compile(ast.Module(body=nodes, type_ignores=[]), str(source), "exec"), scope)
        listeners = {}
        hass = SimpleNamespace(bus=SimpleNamespace(async_listen=lambda event, handler: listeners.setdefault(event, handler) and (lambda: listeners.pop(event))))
        self.runtime = SimpleNamespace(closed=False, links={"actor": "token"}, grants={"actor": {"light.desk", "sensor.power", "event.remote", "sensor.desk_action", "device.d1"}},
                                       wake=asyncio.Event(), client=SimpleNamespace(request=AsyncMock(return_value={"ok": True})), devices=SimpleNamespace(last=self.fired, mains={}))
        self.task = asyncio.create_task(scope["forward_states"](hass, SimpleNamespace(runtime_data=self.runtime)))
        await asyncio.sleep(0)
        self.listeners = listeners
        return listeners["state_changed"]

    async def asyncTearDown(self):
        self.task.cancel()
        with self.assertRaises(asyncio.CancelledError):
            await self.task

    async def test_only_watched_changes_are_sent_at_once_and_an_appearing_entity_wakes_the_exchange(self):
        changed = await self.start({"actor": {"light.desk"}})
        changed(SimpleNamespace(data={"entity_id": "sensor.power", "old_state": state("sensor.power", "1"), "new_state": state("sensor.power", "2")}))
        changed(SimpleNamespace(data={"entity_id": "light.desk", "old_state": state("light.desk", "off"), "new_state": state("light.desk", "on")}))
        self.assertFalse(self.runtime.wake.is_set())
        # No waiting for a batch: the change is on its way within the same turn of the loop.
        await asyncio.sleep(0.05)
        self.runtime.client.request.assert_awaited_once_with("entities/states", [{"id": "light.desk", "state": "on"}], "token")
        self.assertEqual(["light.desk"], self.sent_marks)
        changed(SimpleNamespace(data={"entity_id": "light.new", "old_state": None, "new_state": state("light.new", "off")}))
        self.assertTrue(self.runtime.wake.is_set())

    async def test_a_burst_goes_together_after_the_gap_keeping_every_press(self):
        changed = await self.start({"actor": {"sensor.power", "event.remote"}})
        changed(SimpleNamespace(data={"entity_id": "sensor.power", "old_state": state("sensor.power", "1"), "new_state": state("sensor.power", "2")}))
        await asyncio.sleep(0.05)
        self.assertEqual(1, self.runtime.client.request.await_count)
        # Within the gap: a reading changing twice goes once, as it is now; two presses go as two.
        for value in ("3", "4"):
            changed(SimpleNamespace(data={"entity_id": "sensor.power", "old_state": state("sensor.power", "2"), "new_state": state("sensor.power", value)}))
        for value in ("t1", "t2"):
            changed(SimpleNamespace(data={"entity_id": "event.remote", "old_state": state("event.remote", "t0"), "new_state": state("event.remote", value)}))
        await asyncio.sleep(0.1)
        self.assertEqual(1, self.runtime.client.request.await_count)
        await asyncio.sleep(0.25)
        self.assertEqual(2, self.runtime.client.request.await_count)
        self.assertEqual([{"id": "sensor.power", "state": "4"}, {"id": "event.remote", "state": "t1"}, {"id": "event.remote", "state": "t2"}],
                         self.runtime.client.request.await_args.args[1])


    async def test_an_action_sensor_set_and_cleared_at_once_keeps_its_press(self):
        """Zigbee2MQTT's action sensor reads "4_hold" and is cleared a moment later; merged, the press would be lost."""
        changed = await self.start({"actor": {"sensor.desk_action", "sensor.power"}})
        changed(SimpleNamespace(data={"entity_id": "sensor.power", "old_state": state("sensor.power", "1"), "new_state": state("sensor.power", "2")}))
        await asyncio.sleep(0.05)
        for before, after in (("", "4_hold"), ("4_hold", "")):
            changed(SimpleNamespace(data={"entity_id": "sensor.desk_action", "old_state": state("sensor.desk_action", before), "new_state": state("sensor.desk_action", after)}))
        await asyncio.sleep(0.35)
        self.assertEqual([{"id": "sensor.desk_action", "state": "4_hold"}, {"id": "sensor.desk_action", "state": ""}],
                         self.runtime.client.request.await_args.args[1])


    async def test_every_firing_of_a_shared_devices_trigger_is_sent(self):
        await self.start({"actor": {"device.d1"}})
        fired = self.listeners["clm_device_trigger"]
        for at, key in (("t1", "action_4_hold"), ("t2", "action_4_hold")):
            self.fired["d1"] = {"at": at, "key": key}
            fired(SimpleNamespace(data={"device_id": "d1"}))
        fired(SimpleNamespace(data={"device_id": "d2"}))  # Not shared: never sent.
        await asyncio.sleep(0.35)
        sent = [snap for call in self.runtime.client.request.await_args_list for snap in call.args[1]]
        self.assertEqual([("device.d1", "t1"), ("device.d1", "t2")], [(snap["id"], snap["state"]) for snap in sent])


if __name__ == "__main__":
    unittest.main()
