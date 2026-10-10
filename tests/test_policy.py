"""Portable unit tests for authority; no running HA instance required."""

import importlib.util
from pathlib import Path
import sys
import unittest

path = Path(__file__).parents[1] / "custom_components" / "clm" / "policy.py"
spec = importlib.util.spec_from_file_location("clm_policy", path)
module = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = module
spec.loader.exec_module(module)
Authority, Denied = module.Authority, module.Denied


class AuthorityTests(unittest.TestCase):
    def setUp(self):
        self.now = 1.0
        self.authority = Authority(lambda: self.now)

    def test_userless_context_is_not_owner_authority(self):
        for parent in (None, "forged-root"):
            with self.assertRaises(Denied):
                self.authority.resolve(None, parent, "binding")

    def test_another_authenticated_user_never_inherits_registered_owner(self):
        self.authority.register("root", "owner", ["binding"])
        self.assertEqual("other-user", self.authority.resolve("other-user", "root", "binding"))

    def test_registered_parent_is_binding_scoped_expiring_and_revocable(self):
        self.authority.register("root", "owner", ["binding"])
        self.assertEqual("owner", self.authority.resolve(None, "root", "binding"))
        with self.assertRaises(Denied):
            self.authority.resolve(None, "root", "other-binding")
        self.now += 61
        with self.assertRaises(Denied):
            self.authority.resolve(None, "root", "binding")
        self.authority.register("new-root", "owner", ["binding"])
        self.authority.remove("new-root")
        with self.assertRaises(Denied):
            self.authority.resolve(None, "new-root", "binding")

    def test_scope_capacity_does_not_grow_unbounded(self):
        for number in range(100):
            self.authority.register(str(number), "owner", ["binding"])
        with self.assertRaises(Denied):
            self.authority.register("overflow", "owner", ["binding"])

    def test_registered_run_has_a_total_action_budget(self):
        self.authority.register("root", "owner", ["binding"])
        for _ in range(20):
            self.authority.resolve(None, "root", "binding")
        with self.assertRaises(Denied):
            self.authority.resolve(None, "root", "binding")


if __name__ == "__main__":
    unittest.main()
