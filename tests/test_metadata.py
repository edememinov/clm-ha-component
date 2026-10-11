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
        fields = {"api_origin", "workspace_origin", "workspace_local_origin", "workspace_local_fallback_origin", "extra_ha_origins"}
        for step in ("user", "reconfigure"):
            self.assertEqual(fields, set(strings["config"]["step"][step]["data"]))
            self.assertEqual(fields, set(strings["config"]["step"][step]["data_description"]))

    def test_every_native_action_has_translated_fields_and_an_icon(self):
        strings=json.loads((component / "strings.json").read_text(encoding="utf-8"))
        icons=json.loads((component / "icons.json").read_text(encoding="utf-8"))
        expected={"invoke":{"config_entry_id","binding"},"run_workflow":{"config_entry_id","binding"},
                  "list_bindings":{"config_entry_id","workflows_only"},"run_automation":{"config_entry_id","automation"}}
        common={"config_entry_id","account_user_id","device","pin","incognito"}
        expected.update({"start_session":common|{"preset_name"},"open_selkies":common|{"session_name","match"},
                         "open_clm":common,"list_devices":{"config_entry_id","account_user_id"}})
        self.assertEqual(set(expected),set(strings["services"]))
        self.assertEqual(set(expected),set(icons["services"]))
        for name,fields in expected.items():
            self.assertEqual(fields,set(strings["services"][name]["fields"]))
            self.assertTrue(strings["services"][name]["description"])
