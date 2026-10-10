"""Installable integration metadata and native UI resources."""

import json
from pathlib import Path
import unittest

root = Path(__file__).parents[1]
component = root / "custom_components" / "clm"


class MetadataTests(unittest.TestCase):
    def test_domain_baseline_and_native_flow_resources(self):
        manifest = json.loads((component / "manifest.json").read_text(encoding="utf-8"))
        self.assertEqual(component.name, manifest["domain"])
        self.assertTrue(manifest["config_flow"])
        self.assertIn("websocket_api", manifest["dependencies"])
        self.assertEqual("2026.10.0", json.loads((root / "hacs.json").read_text(encoding="utf-8"))["homeassistant"])
        strings = json.loads((component / "strings.json").read_text(encoding="utf-8"))
        self.assertEqual(strings, json.loads((component / "translations" / "en.json").read_text(encoding="utf-8")))
        self.assertEqual({"api_origin", "workspace_origin", "ha_origin"}, set(strings["config"]["step"]["user"]["data"]))
