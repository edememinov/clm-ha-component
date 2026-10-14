"""State forwarding: only entities CLM's workflows use go at once; new or removed entities wake the exchange."""
import ast
import asyncio
from pathlib import Path
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock

source = Path(__file__).parents[1] / "custom_components" / "clm" / "bridge.py"
tree = ast.parse(source.read_text(encoding="utf-8"))
nodes = [node for node in tree.body if isinstance(node, ast.AsyncFunctionDef) and node.name == "forward_states"]


class CompanionError(Exception):
    pass


def state(entity_id, value):
    return SimpleNamespace(entity_id=entity_id, state=value)


class ForwardStatesTests(unittest.IsolatedAsyncioTestCase):
    async def test_only_watched_changes_are_sent_and_an_appearing_entity_wakes_the_exchange(self):
        watch = {"actor": {"light.desk"}}
        sent_marks = []
        scope = {"asyncio": asyncio, "CompanionError": CompanionError, "callback": lambda method: method, "EVENT_STATE_CHANGED": "state_changed",
                 "entities": SimpleNamespace(
                     DOMAINS=("light", "sensor"), domain_of=lambda entity: entity.split(".")[0],
                     watched=lambda runtime, actor, entity: entity in watch.get(actor, ()),
                     snapshot=lambda value: {"id": value.entity_id, "state": value.state},
                     remember_sent=lambda runtime, actor, snaps: sent_marks.extend(snap["id"] for snap in snaps))}
        exec(compile(ast.Module(body=nodes, type_ignores=[]), str(source), "exec"), scope)
        listeners = {}
        hass = SimpleNamespace(bus=SimpleNamespace(async_listen=lambda event, handler: listeners.setdefault(event, handler) and (lambda: listeners.pop(event))))
        runtime = SimpleNamespace(closed=False, links={"actor": "token"}, grants={"actor": {"light.desk", "sensor.power"}},
                                  wake=asyncio.Event(), client=SimpleNamespace(request=AsyncMock(return_value={"ok": True})))
        task = asyncio.create_task(scope["forward_states"](hass, SimpleNamespace(runtime_data=runtime)))
        await asyncio.sleep(0)
        changed = listeners["state_changed"]
        changed(SimpleNamespace(data={"entity_id": "sensor.power", "old_state": state("sensor.power", "1"), "new_state": state("sensor.power", "2")}))
        changed(SimpleNamespace(data={"entity_id": "light.desk", "old_state": state("light.desk", "off"), "new_state": state("light.desk", "on")}))
        self.assertFalse(runtime.wake.is_set())
        await asyncio.sleep(1.2)
        runtime.client.request.assert_awaited_once_with("entities/states", [{"id": "light.desk", "state": "on"}], "token")
        self.assertEqual(["light.desk"], sent_marks)
        changed(SimpleNamespace(data={"entity_id": "light.new", "old_state": None, "new_state": state("light.new", "off")}))
        self.assertTrue(runtime.wake.is_set())
        task.cancel()
        with self.assertRaises(asyncio.CancelledError):
            await task
