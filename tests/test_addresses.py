"""Approved browser origins and local/online address selection."""

import importlib.util
from pathlib import Path
import sys
import types
import unittest
from unittest.mock import Mock, patch

root = Path(__file__).parents[1] / "custom_components" / "clm"
package = types.ModuleType("clm_addresses_test")
package.__path__ = [str(root)]
sys.modules[package.__name__] = package
for name in ("const", "client", "addresses"):
    spec = importlib.util.spec_from_file_location(f"{package.__name__}.{name}", root / f"{name}.py")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
addresses = sys.modules[f"{package.__name__}.addresses"]
network = types.ModuleType("homeassistant.helpers.network")
network.NoURLAvailableError = type("NoURLAvailableError", (Exception,), {})


class AddressTests(unittest.TestCase):
    def setUp(self):
        self.data = {
            "ha_origin": "http://192.168.1.10:8123",
            "ha_origins": ["http://192.168.1.10:8123", "https://ha.example"],
            "internal_ha_origin": "http://192.168.1.10:8123",
            "workspace_origin": "https://clm.example",
            "workspace_local_origin": "http://192.168.1.20:4200",
        }

    def test_browser_switches_addresses_without_changing_transport(self):
        self.assertEqual((self.data["ha_origin"], self.data["workspace_local_origin"]), addresses.browser_connection(self.data, self.data["ha_origin"]))
        self.assertEqual(("https://ha.example", "https://clm.example"), addresses.browser_connection(self.data, "https://ha.example"))
        self.assertEqual("https://clm.example", addresses.browser_connection(self.data, self.data["ha_origin"], "online")[1])

    def test_unapproved_origins_and_https_mixed_content_are_denied(self):
        for audience, mode in (("https://attacker.example", "auto"), ("https://ha.example", "local"), (self.data["ha_origin"], "arbitrary")):
            with self.assertRaises(ValueError):
                addresses.browser_connection(self.data, audience, mode)

    def test_https_internal_address_falls_back_to_online_without_downgrading(self):
        self.data["internal_ha_origin"] = "https://ha.example"
        self.assertEqual("https://clm.example", addresses.browser_connection(self.data, "https://ha.example")[1])

    def test_legacy_entries_keep_the_original_exact_audience(self):
        legacy = {"ha_origin": "https://ha.example", "workspace_origin": "https://clm.example"}
        self.assertEqual(("https://ha.example", "https://clm.example"), addresses.browser_connection(legacy))
        with self.assertRaises(ValueError):
            addresses.browser_connection(legacy, "https://other.example")

    def test_discovery_uses_internal_external_and_cloud_urls(self):
        network.get_url = Mock(side_effect=["http://192.168.1.10:8123/", "https://ha.example/", "https://cloud.example/"])
        with patch.dict(sys.modules, {"homeassistant.helpers.network": network}):
            origins, internal = addresses.discover_origins(object())
        self.assertEqual(["http://192.168.1.10:8123", "https://ha.example", "https://cloud.example"], origins)
        self.assertEqual(origins[0], internal)

    def test_absent_discovery_requires_an_explicit_override(self):
        network.get_url = Mock(side_effect=network.NoURLAvailableError)
        with patch.dict(sys.modules, {"homeassistant.helpers.network": network}):
            self.assertEqual(([], None), addresses.discover_origins(object()))

    def test_local_dns_fallback_and_default_are_ordered_and_exactly_approved(self):
        self.data["workspace_local_origin"]="http://clm.local"
        self.data["workspace_local_fallback_origin"]="http://192.168.1.20:4200"
        self.assertEqual(["http://clm.local","http://192.168.1.20:4200","https://clm.example"],addresses.workspace_origins(self.data,self.data["ha_origin"]))
        self.assertEqual("http://192.168.1.20:4200",addresses.browser_connection(self.data,self.data["ha_origin"],"auto","http://192.168.1.20:4200")[1])
        with self.assertRaises(ValueError):
            addresses.browser_connection(self.data,self.data["ha_origin"],"auto","http://other-server")
        with self.assertRaises(ValueError):
            addresses.browser_connection(self.data,"https://ha.example","online","http://192.168.1.20:4200")

    def test_https_never_offers_a_plain_http_ip_fallback(self):
        self.data["internal_ha_origin"]="https://ha.example"
        self.data["workspace_local_origin"]="https://clm.local"
        self.data["workspace_local_fallback_origin"]="http://192.168.1.20:4200"
        self.assertEqual(["https://clm.local","https://clm.example"],addresses.workspace_origins(self.data,"https://ha.example"))

    def test_public_ha_address_does_not_prevent_reachable_local_workspace_selection(self):
        self.data["workspace_local_origin"]="https://clm.local"
        self.data["workspace_local_fallback_origin"]="https://192.168.1.20:8443"
        self.assertEqual(["https://clm.local","https://192.168.1.20:8443","https://clm.example"],addresses.workspace_origins(self.data,"https://ha.example"))
