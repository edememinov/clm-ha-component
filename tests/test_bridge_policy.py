"""Test pure bridge policy functions without importing the HA host."""

import ast
from pathlib import Path
import unittest

source = Path(__file__).parents[1] / "custom_components" / "clm" / "bridge.py"
tree = ast.parse(source.read_text(encoding="utf-8"))
nodes = [node for node in tree.body if isinstance(node, ast.Import) and all(alias.name in ("hashlib", "json") for alias in node.names)
         or isinstance(node, ast.FunctionDef) and node.name in ("fingerprint", "bindings_for")]
scope = {}
exec(compile(ast.Module(body=nodes, type_ignores=[]), str(source), "exec"), scope)


class BridgePolicyTests(unittest.TestCase):
    def test_fingerprint_includes_conditions_actions_and_triggers(self):
        fingerprint = scope["fingerprint"]
        config = {"id": "sample", "actions": [], "conditions": [], "triggers": []}
        original = fingerprint(config)
        for field in ("actions", "conditions", "triggers"):
            self.assertNotEqual(original, fingerprint({**config, field: [{"sample": True}]}))
        self.assertEqual(original, fingerprint(dict(reversed(list(config.items())))))

    def test_only_concrete_bindings_from_the_correct_connection_qualify(self):
        select = scope["bindings_for"]
        config = {"actions": [{"action": "clm.invoke", "data": {"config_entry_id": "connection", "binding": "reference"}}]}
        self.assertEqual(["reference"], select(config, "connection"))
        with self.assertRaises(ValueError):
            select(config, "other")
        config["actions"][0]["data"]["binding"] = "{{ forged }}"
        with self.assertRaises(ValueError):
            select(config, "connection")

    def test_native_workflow_action_cannot_enter_the_outbound_bridge(self):
        for actions in ([{"action":"clm.run_workflow","data":{"config_entry_id":"connection","binding":"workflow"}}],
                        [{"choose":[{"conditions":[],"sequence":[{"service":"clm.run_workflow","data":{"config_entry_id":"connection","binding":"workflow"}}]}]}]):
            with self.assertRaises(ValueError):
                scope["bindings_for"]({"actions":actions},"connection")
