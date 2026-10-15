"""Entities CLM may use: what is offered, how requests and revocations reconcile, and what control accepts."""
import asyncio
from datetime import datetime, timezone
import importlib.util
from pathlib import Path
import sys
import types
import unittest
from unittest.mock import AsyncMock, Mock, patch


class Unauthorized(Exception):
    pass


class CompanionError(Exception):
    pass


class Subentry:
    def __init__(self, *, data, subentry_type, title, unique_id, subentry_id=None):
        self.data, self.subentry_type, self.title, self.unique_id = dict(data), subentry_type, title, unique_id
        self.subentry_id = subentry_id or f"sub-{unique_id}"


root = Path(__file__).parents[1] / "custom_components" / "clm"
package = types.ModuleType("clm_entities_test")
package.__path__ = [str(root)]
package.linked_user = AsyncMock(side_effect=lambda hass, runtime, actor: runtime.links[actor])
issues = types.ModuleType("homeassistant.helpers.issue_registry")
modules = {
    "homeassistant.auth.permissions.const": types.SimpleNamespace(POLICY_CONTROL="control", POLICY_READ="read"),
    "homeassistant.config_entries": types.SimpleNamespace(ConfigSubentry=Subentry),
    "homeassistant.core": types.SimpleNamespace(Context=lambda **kwargs: types.SimpleNamespace(**kwargs)),
    "homeassistant.exceptions": types.SimpleNamespace(Unauthorized=Unauthorized),
    "homeassistant.helpers": types.SimpleNamespace(issue_registry=issues),
    "homeassistant.helpers.issue_registry": issues,
}
sys.modules[package.__name__] = package
with patch.dict(sys.modules, modules):
    for name in ("const", "entities"):
        spec = importlib.util.spec_from_file_location(f"{package.__name__}.{name}", root / f"{name}.py")
        module = importlib.util.module_from_spec(spec)
        sys.modules[spec.name] = module
        spec.loader.exec_module(module)
entities = module


def state(entity_id, value="on", name=None, **attributes):
    return types.SimpleNamespace(entity_id=entity_id, domain=entity_id.split(".")[0], state=value, name=name or entity_id,
                                 attributes=attributes, last_changed=datetime(2026, 10, 13, tzinfo=timezone.utc))


class Registry:
    def __init__(self):
        self.issues = {}


class EntityAccessTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.registry = Registry()
        issues.async_get = lambda hass: self.registry
        issues.async_delete_issue = lambda hass, domain, issue_id: self.registry.issues.pop((domain, issue_id), None)
        self.allowed = {"light.desk", "sensor.temperature", "button.doorbell", "automation.evening"}
        self.user = types.SimpleNamespace(is_active=True, name="Sam",
                                          permissions=types.SimpleNamespace(check_entity=Mock(side_effect=lambda entity, policy: entity in self.allowed)))
        self.states = {value.entity_id: value for value in (
            state("light.desk", name="Desk lamp", brightness=120, rgb_color=(255, 120, 0), entity_picture=None, effect_list=["a"] * 10),
            state("sensor.temperature", "21.5", name="Temperature", unit_of_measurement="°C"),
            state("button.doorbell", "2026-10-13T08:00:00+00:00", name="Doorbell"),
            state("light.hidden", name="Hidden lamp"),
            state("lock.front", "locked", name="Front door"))}
        self.client = types.SimpleNamespace(request=AsyncMock())
        self.runtime = types.SimpleNamespace(closed=False, links={"actor": "credential"}, grants={}, fresh={}, asked=set(),
                                             grant_lock=asyncio.Lock(), client=self.client)
        self.entry = types.SimpleNamespace(entry_id="entry", data={}, subentries={}, runtime_data=self.runtime)
        def add_subentry(entry, sub):
            entry.subentries = {**entry.subentries, sub.subentry_id: sub}
        def remove_subentry(entry, subentry_id):
            entry.subentries = {key: value for key, value in entry.subentries.items() if key != subentry_id}
        def update_entry(entry, data):
            entry.data = data
        self.fired = []
        self.hass = types.SimpleNamespace(
            auth=types.SimpleNamespace(async_get_user=AsyncMock(return_value=self.user)),
            states=types.SimpleNamespace(async_all=lambda domains: [value for value in self.states.values() if value.domain in domains],
                                         get=lambda entity: self.states.get(entity)),
            config_entries=types.SimpleNamespace(async_add_subentry=add_subentry, async_remove_subentry=remove_subentry, async_update_entry=update_entry),
            bus=types.SimpleNamespace(async_fire=lambda event, data: self.fired.append((event, data))),
            services=types.SimpleNamespace(async_call=AsyncMock()))

    def grant(self, grant, entity):
        sub = Subentry(data={"grant": grant, "actor": "actor", "entity": entity}, subentry_type="access", title=entity, unique_id=grant)
        self.entry.subentries = {**self.entry.subentries, sub.subentry_id: sub}
        entities.refresh(self.entry)

    def answer_sync(self, grants):
        async def request(route, payload, credential):
            self.sent.append((route, payload))
            return {"grants": grants} if route == "entities/sync" else {"ok": True}
        self.sent = []
        self.client.request.side_effect = request

    def test_offer_lists_only_permitted_supported_entities_by_name(self):
        rows = entities.catalogue(self.hass, self.user)
        self.assertEqual(["Desk lamp", "Doorbell", "Temperature"], [row["label"] for row in rows])
        self.assertTrue(all(set(row) - {"traits"} == {"id", "label", "domain"} for row in rows))
        # What a thing can do travels with the offer; readings never do.
        self.assertEqual({"unit": "°C"}, rows[2]["traits"])
        # A light's effects are something it can do, so they are offered as choices.
        self.assertEqual({"effect_list": ["a"] * 10}, rows[0]["traits"])

    def test_traits_say_what_a_light_or_sensor_can_do(self):
        lamp = state("light.strip", supported_color_modes={"color_temp", "rgb"}, min_color_temp_kelvin=2000, max_color_temp_kelvin=6500, brightness=3)
        self.assertEqual({"modes": ["color_temp", "rgb"], "min_kelvin": 2000, "max_kelvin": 6500}, entities.traits(lamp))
        self.assertEqual({"modes": ["onoff"]}, entities.traits(state("light.plain", supported_color_modes=["onoff"])))
        washer = state("sensor.washer", "idle", device_class="enum", options=["idle", "washing", "done"], friendly_name="Washer")
        self.assertEqual({"device_class": "enum", "options": ["idle", "washing", "done"]}, entities.traits(washer))
        self.assertEqual({"device_class": "door"}, entities.traits(state("binary_sensor.front", "off", device_class="door")))
        self.assertEqual({}, entities.traits(state("button.doorbell", "2026-10-13T08:00:00+00:00")))

    def test_snapshot_keeps_scalars_and_short_number_lists(self):
        snapshot = entities.snapshot(self.states["light.desk"])
        self.assertEqual({"brightness": 120, "rgb_color": [255, 120, 0], "entity_picture": None}, snapshot["attributes"])
        self.assertEqual("2026-10-13T00:00:00+00:00", snapshot["changed"])

    def test_service_options_are_checked_again(self):
        self.assertEqual({"brightness_pct": 30, "transition": 1.5}, entities.service_data("light", "turn_on", {"brightness_pct": 30, "transition": 1.5}))
        self.assertEqual({"transition": 2, "flash": "short"}, entities.service_data("light", "turn_off", {"transition": 2, "flash": "short"}))
        self.assertEqual({}, entities.service_data("button", "press", None))
        self.assertEqual({}, entities.service_data("switch", "toggle", None))
        self.assertEqual({"position": 40}, entities.service_data("cover", "set_cover_position", {"position": 40}))
        self.assertEqual({"option": "Movie"}, entities.service_data("input_select", "select_option", {"option": "Movie"}))
        for domain, service, data in (("light", "turn_off", {"brightness_pct": 1}), ("light", "turn_on", {"brightness_pct": 101}),
                                      ("light", "turn_on", {"entity_id": "light.other"}), ("light", "turn_on", {"rgb_color": [1, 2]}),
                                      ("light", "turn_on", {"brightness_pct": True}), ("light", "turn_on", {"rgb_color": [1, 2, 3], "color_temp_kelvin": 3000}),
                                      ("switch", "turn_on", {"brightness_pct": 50}), ("switch", "press", None), ("sensor", "turn_on", None),
                                      ("select", "select_option", {"option": {"nested": 1}}), ("media_player", "volume_set", {"volume_level": 3})):
            with self.assertRaises(ValueError):
                entities.service_data(domain, service, data)

    def test_traits_carry_what_services_choose_from(self):
        fan = state("fan.ceiling", "on", supported_features=9, preset_modes=["breeze", "sleep"], percentage_step=25, percentage=50)
        self.assertEqual({"features": 9, "preset_modes": ["breeze", "sleep"], "percentage_step": 25}, entities.traits(fan))
        dial = state("input_number.volume", "4", min=0, max=10, step=0.5, unit_of_measurement="dB", mode="slider")
        self.assertEqual({"min": 0, "max": 10, "step": 0.5, "unit": "dB"}, entities.traits(dial))
        self.assertEqual({}, entities.traits(state("switch.kettle", "off")))
        self.assertIn("switch", entities.DOMAINS)

    async def test_pending_request_is_announced_once_and_old_repairs_items_go(self):
        # A Repairs item left from an earlier version: requests are answered in the CLM card now.
        self.registry.issues[("clm", "access_old")] = types.SimpleNamespace(data={"entry_id": "entry"}, active=False)
        self.answer_sync([{"id": "g1", "entity": "light.desk", "label": "Desk lamp", "status": "pending", "carried": False}])
        await entities.sync(self.hass, self.entry, "actor", "credential")
        await entities.sync(self.hass, self.entry, "actor", "credential")
        self.assertEqual([("clm_access_requested", {"entity_id": "light.desk", "account": "Sam", "config_entry_id": "entry"})], self.fired)
        self.assertEqual({}, self.registry.issues)
        route, payload = self.sent[0]
        self.assertEqual("entities/sync", route)
        self.assertEqual({"entities", "states", "statesComplete", "grants", "migrate", "hash"}, set(payload))
        self.assertEqual([], payload["states"])
        # Waiting for the person: nothing is decided or shared yet.
        self.assertNotIn("entities/decision", [route for route, _ in self.sent])
        self.assertEqual({}, entities.held(self.entry))

    async def test_request_for_something_the_account_cannot_use_is_refused(self):
        self.answer_sync([{"id": "g2", "entity": "light.hidden", "label": "Hidden", "status": "pending", "carried": False},
                          {"id": "g2b", "entity": "light.hidden", "label": "Hidden", "status": "confirming", "carried": False}])
        await entities.sync(self.hass, self.entry, "actor", "credential")
        self.assertIn(("entities/decision", {"grant": "g2", "allow": False}), self.sent)
        self.assertIn(("entities/decision", {"grant": "g2b", "allow": False}), self.sent)
        self.assertEqual([], self.fired)
        self.assertEqual({}, entities.held(self.entry))

    async def test_allowed_in_the_card_is_confirmed_here_then_a_deletion_revokes_it(self):
        self.answer_sync([{"id": "g6", "entity": "light.desk", "label": "Desk lamp", "status": "confirming", "carried": False}])
        await entities.sync(self.hass, self.entry, "actor", "credential")
        self.assertEqual(("entities/decision", {"grant": "g6", "allow": True}), self.sent[1])
        self.assertEqual("Desk lamp · Sam", entities.held(self.entry)["g6"].title)
        self.assertEqual({"actor": {"light.desk"}}, self.runtime.grants)
        # Covered only until its subentry exists; deleting it afterwards is a revocation.
        self.assertNotIn("g6", self.runtime.fresh)
        self.entry.subentries = {}
        entities.refresh(self.entry)
        self.answer_sync([])
        await entities.sync(self.hass, self.entry, "actor", "credential")
        self.assertEqual([], self.sent[0][1]["grants"])

    async def test_grant_withdrawn_in_clm_is_removed_here_and_states_go_only_for_held_grants(self):
        self.grant("g3", "sensor.temperature")
        self.grant("g4", "light.desk")
        self.entry.data = {"grants_migrated": ["actor"]}
        self.answer_sync([{"id": "g3", "entity": "sensor.temperature", "label": "Temperature", "status": "approved", "carried": False}])
        await entities.sync(self.hass, self.entry, "actor", "credential")
        payload = self.sent[0][1]
        self.assertEqual(["g3", "g4"], payload["grants"])
        self.assertFalse(payload["migrate"])
        self.assertEqual({"light.desk", "sensor.temperature"}, {value["id"] for value in payload["states"]})
        self.assertEqual({"g3"}, set(entities.held(self.entry)))
        self.assertEqual({"actor": {"sensor.temperature"}}, self.runtime.grants)

    async def test_an_unchanged_offer_and_unchanged_states_are_not_sent_again(self):
        self.grant("g3", "sensor.temperature")
        self.grant("g4", "light.desk")
        self.entry.data = {"grants_migrated": ["actor"]}
        approved = [{"id": "g3", "entity": "sensor.temperature", "label": "Temperature", "status": "approved", "carried": False},
                    {"id": "g4", "entity": "light.desk", "label": "Desk lamp", "status": "approved", "carried": False}]
        self.answer_sync(approved)
        await entities.sync(self.hass, self.entry, "actor", "credential")
        first = self.sent[0][1]
        self.assertIn("entities", first)
        self.assertTrue(first["statesComplete"])
        self.assertEqual(2, len(first["states"]))

        self.answer_sync(approved)
        await entities.sync(self.hass, self.entry, "actor", "credential")
        second = self.sent[0][1]
        self.assertNotIn("entities", second)
        self.assertEqual(first["hash"], second["hash"])
        self.assertEqual(([], False), (second["states"], second["statesComplete"]))

        # Only what changed in between goes with the next exchange; a new entity changes the offer.
        self.states["sensor.temperature"] = state("sensor.temperature", "22.0", name="Temperature", unit_of_measurement="°C")
        self.states["light.new"] = state("light.new", name="New lamp")
        self.allowed.add("light.new")
        self.answer_sync(approved)
        await entities.sync(self.hass, self.entry, "actor", "credential")
        third = self.sent[0][1]
        self.assertEqual(["sensor.temperature"], [value["id"] for value in third["states"]])
        self.assertIn("New lamp", [row["label"] for row in third["entities"]])

    async def test_a_clm_that_lost_what_it_held_gets_everything_once_more(self):
        self.grant("g3", "sensor.temperature")
        self.entry.data = {"grants_migrated": ["actor"]}
        approved = [{"id": "g3", "entity": "sensor.temperature", "label": "Temperature", "status": "approved", "carried": False}]
        self.answer_sync(approved)
        await entities.sync(self.hass, self.entry, "actor", "credential")
        answers = iter([{"grants": approved, "need": ["entities", "states"]}, {"grants": approved, "need": [], "watch": ["sensor.temperature"]}])
        async def request(route, payload, credential):
            self.sent.append((route, payload))
            return next(answers) if route == "entities/sync" else {"ok": True}
        self.sent = []
        self.client.request.side_effect = request
        await entities.sync(self.hass, self.entry, "actor", "credential")
        light, full = self.sent[0][1], self.sent[1][1]
        self.assertNotIn("entities", light)
        self.assertIn("entities", full)
        self.assertEqual((True, ["sensor.temperature"]), (full["statesComplete"], [value["id"] for value in full["states"]]))
        self.assertEqual(2, len(self.sent))
        self.assertTrue(entities.watched(self.runtime, "actor", "sensor.temperature"))

    async def test_only_entities_clms_workflows_use_are_watched(self):
        self.grant("g3", "sensor.temperature")
        self.grant("g4", "light.desk")
        self.assertTrue(entities.watched(self.runtime, "actor", "light.desk"))
        self.answer_sync([])
        self.client.request.side_effect = None
        self.client.request.return_value = {"grants": [
            {"id": "g3", "entity": "sensor.temperature", "label": "Temperature", "status": "approved", "carried": False},
            {"id": "g4", "entity": "light.desk", "label": "Desk lamp", "status": "approved", "carried": False}], "watch": ["light.desk"]}
        self.entry.data = {"grants_migrated": ["actor"]}
        await entities.sync(self.hass, self.entry, "actor", "credential")
        self.assertTrue(entities.watched(self.runtime, "actor", "light.desk"))
        self.assertFalse(entities.watched(self.runtime, "actor", "sensor.temperature"))
        # Never an entity that is not allowed, whatever CLM names.
        self.assertFalse(entities.watched(self.runtime, "other", "light.desk"))

    async def test_carried_approvals_are_adopted_on_the_first_exchange_only(self):
        carried = [{"id": "g5", "entity": "automation.evening", "label": "Evening", "status": "approved", "carried": True}]
        self.answer_sync(carried)
        await entities.sync(self.hass, self.entry, "actor", "credential")
        self.assertTrue(self.sent[0][1]["migrate"])
        self.assertEqual({"g5"}, set(entities.held(self.entry)))
        self.assertEqual(["actor"], self.entry.data["grants_migrated"])
        self.entry.subentries = {}
        entities.refresh(self.entry)
        self.answer_sync(carried)
        await entities.sync(self.hass, self.entry, "actor", "credential")
        self.assertFalse(self.sent[0][1]["migrate"])
        self.assertEqual({}, entities.held(self.entry))

    async def test_control_needs_a_grant_a_supported_service_and_permission(self):
        self.grant("g9", "light.desk")
        delivery = {"actor": "actor", "entity": "light.desk", "service": "turn_on", "data": {"brightness_pct": 40}, "expires": 2**40}
        await entities.control(self.hass, self.entry, delivery)
        call = self.hass.services.async_call.await_args
        self.assertEqual(("light", "turn_on", {"entity_id": "light.desk", "brightness_pct": 40}), call.args)
        self.assertEqual("actor", call.kwargs["context"].user_id)
        for change in ({"entity": "sensor.temperature"}, {"service": "unlock"}, {"expires": 0}, {"data": {"brightness_pct": 400}}):
            with self.assertRaises(ValueError):
                await entities.control(self.hass, self.entry, {**delivery, **change})
        self.allowed.discard("light.desk")
        with self.assertRaises(ValueError):
            await entities.control(self.hass, self.entry, delivery)
        self.assertEqual(1, self.hass.services.async_call.await_count)


if __name__ == "__main__":
    unittest.main()
