"""Run CLM item: what the pickers list, and the command a run sends as the linked account."""
import importlib.util
from pathlib import Path
import sys
import types
import unittest
from unittest.mock import AsyncMock, patch


class CompanionError(Exception):
    pass


class HomeAssistantError(Exception):
    pass


class Runtime:
    def __init__(self, links, request):
        self.links, self.items, self.operations = links, {}, {}
        self.client = types.SimpleNamespace(request=request)


root = Path(__file__).parents[1] / "custom_components" / "clm"
package = types.ModuleType("clm_items_test")
package.__path__ = [str(root)]
package.Runtime = Runtime
described = {}
registered = {}
modules = {
    "probatio": types.SimpleNamespace(Schema=lambda value: value, Required=lambda key: key, Optional=lambda key: key,
                                      All=lambda *values: values, Length=lambda **kwargs: kwargs),
    "homeassistant.core": types.SimpleNamespace(SupportsResponse=types.SimpleNamespace(OPTIONAL="optional")),
    "homeassistant.exceptions": types.SimpleNamespace(HomeAssistantError=HomeAssistantError),
    "homeassistant.helpers.service": types.SimpleNamespace(
        async_set_service_schema=lambda hass, domain, service, schema: described.__setitem__(service, schema)),
}
sys.modules[package.__name__] = package
with patch.dict(sys.modules, modules):
    for name in ("const", "client", "items"):
        if name == "client":
            sys.modules[f"{package.__name__}.client"] = types.SimpleNamespace(CompanionError=CompanionError)
            continue
        spec = importlib.util.spec_from_file_location(f"{package.__name__}.{name}", root / f"{name}.py")
        module = importlib.util.module_from_spec(spec)
        sys.modules[spec.name] = module
        spec.loader.exec_module(module)
items = module


def row(kind, identifier, label, *operations):
    return {"reference": {"kind": kind, "id": identifier}, "label": label, "operations": list(operations)}


class RunItemTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        described.clear()
        self.sent = []
        async def request(route, payload, credential):
            self.sent.append((route, payload, credential))
            if route == "capabilities":
                return {"version": 1, "operations": [{"id": "session.start", "label": "Start"}, {"id": "pipeline.rerun", "label": "Retry preparation"}]}
            if route == "catalog/query":
                if payload["offset"] == 0:
                    return {"items": [row("game-preset", "7", "Racing", "session.start"), {"label": "broken"}], "offset": 0, "more": True}
                return {"items": [row("prepare-job", "j1", "Puzzle game", "pipeline.rerun"), row("plugin-action", "x", "Unrunnable")], "offset": 50, "more": False}
            if route == "commands":
                return self.answer
            raise AssertionError(route)
        self.answer = {"id": "run", "status": "accepted", "code": 202, "duplicate": False}
        self.runtime = Runtime({"actor": "credential"}, AsyncMock(side_effect=request))
        self.entry = types.SimpleNamespace(runtime_data=self.runtime)
        self.hass = types.SimpleNamespace(
            config_entries=types.SimpleNamespace(async_entries=lambda domain: [self.entry]),
            services=types.SimpleNamespace(async_register=lambda domain, service, handler, **kwargs: registered.__setitem__(service, handler)))

    async def test_pickers_list_runnable_items_by_name_and_action_labels(self):
        await items.refresh(self.hass, self.entry)
        fields = described["run_item"]["fields"]
        self.assertEqual([{"value": "prepare-job:j1", "label": "Puzzle game (Prepare job)"}, {"value": "game-preset:7", "label": "Racing (Game preset)"}],
                         fields["item"]["selector"]["select"]["options"])
        self.assertEqual([{"value": "pipeline.rerun", "label": "Retry preparation"}, {"value": "session.start", "label": "Start"}],
                         fields["operation"]["selector"]["select"]["options"])
        # A link that went away takes its items out of the pickers.
        self.runtime.links = {}
        await items.refresh(self.hass, self.entry)
        self.assertEqual([], described["run_item"]["fields"]["item"]["selector"]["select"]["options"])

    async def test_run_sends_the_command_as_the_linked_account(self):
        package._runtime = lambda hass, entry_id: self.runtime
        sys.modules[f"{package.__name__}.native_actions"] = types.SimpleNamespace(
            action_account=AsyncMock(side_effect=lambda hass, runtime, context, requested: runtime.links["actor"]))
        items.register(self.hass)
        call = types.SimpleNamespace(context=types.SimpleNamespace(user_id=None), data={
            "config_entry_id": "entry", "item": "game-preset:7", "operation": "session.start", "inputs": {"level": 2}})
        self.assertEqual(self.answer, await registered["run_item"](call))
        route, payload, credential = self.sent[-1]
        self.assertEqual(("commands", "credential"), (route, credential))
        self.assertEqual({"operation": "session.start", "target": {"kind": "game-preset", "id": "7"}, "arguments": {"values": {"level": 2}}}, payload["value"])
        self.answer = {"id": "run", "status": "failed", "code": 500, "duplicate": False}
        with self.assertRaises(HomeAssistantError):
            await registered["run_item"](call)
        with self.assertRaises(HomeAssistantError):
            await registered["run_item"](types.SimpleNamespace(context=call.context, data={**call.data, "item": "no-separator"}))


if __name__ == "__main__":
    unittest.main()
