"""Device triggers that become presses of a "Button press" event entity, and the event types they get."""
import ast
from pathlib import Path
import unittest

source = Path(__file__).parents[1] / "custom_components" / "clm" / "event.py"
tree = ast.parse(source.read_text(encoding="utf-8"))
nodes = [node for node in tree.body if isinstance(node, ast.FunctionDef) and node.name in ("press_name", "presses")
         or isinstance(node, ast.Assign) and any(getattr(target, "id", "") == "_NOT_PRESSES" for target in node.targets)]
scope: dict = {}
exec(compile(ast.Module(body=nodes, type_ignores=[]), str(source), "exec"), scope)


def trigger(kind, subtype=None, **extra):
    return {"platform": "device", "domain": "zha", "device_id": "d1", "type": kind, **({"subtype": subtype} if subtype else {}), **extra}


class ButtonPressTests(unittest.TestCase):
    def test_four_button_remote_presses_read_as_button_and_press(self):
        triggers = [trigger(kind, f"button_{number}") for number in range(1, 5)
                    for kind in ("remote_button_short_press", "remote_button_double_press", "remote_button_long_press")]
        found = scope["presses"](triggers)
        self.assertEqual(12, len(found))
        self.assertEqual(["button_1_short_press", "button_1_double_press", "button_1_long_press"], list(found)[:3])
        self.assertEqual("button_4_long_press", list(found)[-1])
        self.assertEqual(triggers[0], found["button_1_short_press"])

    def test_action_triggers_keep_the_action_name(self):
        self.assertEqual("1_single", scope["press_name"]({"platform": "device", "domain": "mqtt", "device_id": "d1", "type": "action", "subtype": "1_single"}))
        self.assertEqual("turn_on_short_press", scope["press_name"](trigger("remote_button_short_press", "turn_on")))

    def test_entity_state_and_offline_triggers_are_not_presses(self):
        self.assertIsNone(scope["press_name"](trigger("turned_on", entity_id="light.desk")))
        self.assertIsNone(scope["press_name"](trigger("device_offline", "device_offline")))
        self.assertIsNone(scope["press_name"]({"platform": "event", "type": "x"}))
        self.assertEqual({}, scope["presses"]([trigger("turned_on", entity_id="light.desk")]))


if __name__ == "__main__":
    unittest.main()
