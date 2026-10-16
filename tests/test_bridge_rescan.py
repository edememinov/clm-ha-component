"""A shared device's triggers read again when CLM asks: forgotten, then exchanged; never for a device not shared."""
import ast
import sys
import types
from pathlib import Path
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, Mock

source = Path(__file__).parents[1] / "custom_components" / "clm" / "bridge.py"
tree = ast.parse(source.read_text(encoding="utf-8"))
nodes = [node for node in tree.body if isinstance(node, ast.AsyncFunctionDef) and node.name == "dispatch"]
# dispatch imports linked_user from its package; a rescan never needs it.
package = types.ModuleType("clm_rescan_test")
package.__path__ = []
package.linked_user = AsyncMock()
sys.modules[package.__name__] = package


class RescanTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.synced = AsyncMock()
        self.forgotten = Mock()
        self.scope = {
            "__name__": "clm_rescan_test.bridge", "__package__": "clm_rescan_test",
            "devices": SimpleNamespace(is_device=lambda reference: reference.startswith("device."), device_id=lambda reference: reference[len("device."):]),
            "entities": SimpleNamespace(granted=lambda runtime, actor, entity: entity in runtime.grants.get(actor, ())),
            "sync_entities": self.synced,
        }
        exec(compile(ast.Module(body=nodes, type_ignores=[]), str(source), "exec"), self.scope)
        self.runtime = SimpleNamespace(grants={"actor": {"device.abc"}}, devices=SimpleNamespace(forget=self.forgotten))
        self.entry = SimpleNamespace(runtime_data=self.runtime)

    async def test_a_shared_device_is_read_again_then_exchanged(self):
        await self.scope["dispatch"](None, self.entry, {"kind": "rescan", "actor": "actor", "entity": "device.abc"})
        self.forgotten.assert_called_once_with("abc")
        self.synced.assert_awaited_once_with(None, self.entry, "actor")

    async def test_a_device_not_shared_or_an_entity_is_refused(self):
        for delivery in ({"kind": "rescan", "actor": "actor", "entity": "device.other"}, {"kind": "rescan", "actor": "actor", "entity": "light.desk"},
                         {"kind": "rescan", "actor": "someone", "entity": "device.abc"}):
            with self.assertRaises(ValueError):
                await self.scope["dispatch"](None, self.entry, delivery)
        self.forgotten.assert_not_called()
        self.synced.assert_not_awaited()


if __name__ == "__main__":
    unittest.main()
