"""Home Assistant devices CLM may use, by the triggers Home Assistant offers for them and what they are.

A remote paired through Zigbee2MQTT or ZHA lists every press as a device trigger ("4_hold" action, "First button"
pressed), as Home Assistant's own Device trigger shows them; its entities say less (an action sensor is cleared at
once, an event entity may not list every press). So a device can be shared with CLM as well as an entity. It travels
through the same grants as an entity, as `device.<device id>`. CLM is told when one of its triggers fires, by the
`trigger` and `triggered` attributes. A device that is a light, a switch, a cover… stays one: its main entity's kind,
abilities and state travel with it, and CLM's commands for the device go to that entity.

Only the triggers of devices CLM's workflows use are attached, and only while they do.
"""

import hashlib
import json
import logging
import re
from datetime import UTC, datetime

import probatio as vol
from homeassistant.auth.permissions.const import POLICY_READ
from homeassistant.components.device_automation import DeviceAutomationType, async_get_device_automations
from homeassistant.core import callback
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers import device_registry as dr, entity_registry as er
from homeassistant.helpers.translation import async_get_translations
from homeassistant.helpers.trigger import async_initialize_triggers, async_validate_trigger_config

from .const import DOMAIN

_LOGGER = logging.getLogger(__name__)
PREFIX = "device."
# Fired on the bus when a shared device's trigger fires; the bridge sends it to CLM.
EVENT_FIRED = "clm_device_trigger"
MAX_TRIGGERS = 32
_ID = re.compile(r"^[0-9a-f]{20,64}$")


def is_device(reference: str) -> bool:
    return reference.startswith(PREFIX)


def device_id(reference: str) -> str:
    return reference[len(PREFIX):]


def reference(device) -> str | None:
    """The id CLM grants a device under; None for a device id CLM could not hold."""
    return PREFIX + device.id if _ID.match(device.id or "") else None


def permitted(hass, user, reference_id: str) -> bool:
    """A device counts as the account's when it may read one of the device's entities, or is an administrator."""
    if user is None or not user.is_active or not is_device(reference_id):
        return False
    device = dr.async_get(hass).async_get(device_id(reference_id))
    if device is None or device.disabled_by is not None:
        return False
    if user.is_admin:
        return True
    return any(user.permissions.check_entity(entry.entity_id, POLICY_READ)
               for entry in er.async_entries_for_device(er.async_get(hass), device.id))


def main_entity(hass, reference_id: str) -> str | None:
    """The entity that says what the device is: the first, in the order CLM's services are listed (lights first), that
    is not a configuration or diagnostic entity. None for a device that only reports (a remote)."""
    from .entities import SERVICES
    entries = [entry for entry in er.async_entries_for_device(er.async_get(hass), device_id(reference_id))
               if entry.disabled_by is None and entry.entity_category is None and entry.domain in SERVICES]
    for domain in SERVICES:
        for entry in entries:
            if entry.domain == domain:
                return entry.entity_id
    return None


def label(hass, reference_id: str, fallback: str | None = None) -> str:
    device = dr.async_get(hass).async_get(device_id(reference_id))
    name = (device.name_by_user or device.name) if device else None
    return (name or fallback or "")[:128].strip() or "Device"


def trigger_key(config: dict) -> str:
    """A stable name for one trigger of a device, as CLM saves it: "action_4_hold", "battery_level_<entity>"."""
    parts = [str(config.get(name) or "") for name in ("type", "subtype")]
    if config.get("entity_id"):
        parts.append(str(config["entity_id"]).split(".", 1)[-1])
    key = re.sub(r"[^a-z0-9_]+", "_", "_".join(part for part in parts if part).lower()).strip("_")[:64]
    if not key:
        key = hashlib.sha256(repr(sorted(config.items())).encode()).hexdigest()[:16]
    return key


async def _label(hass, config: dict, translations: dict[str, str]) -> str:
    """The words Home Assistant's Device trigger shows: its integration's translation, with the subtype and entity
    name filled in, or else "<subtype>" <type>, as its frontend falls back to."""
    domain, kind, subtype = config.get("domain", ""), str(config.get("type") or ""), str(config.get("subtype") or "")
    subtype_text = translations.get(f"component.{domain}.device_automation.trigger_subtype.{subtype}", subtype)
    template = translations.get(f"component.{domain}.device_automation.trigger_type.{kind}")
    entity_name = ""
    if config.get("entity_id"):
        # Device triggers name the entity by its registry id; the registry knows both.
        entry = er.async_get(hass).async_get(config["entity_id"])
        state = hass.states.get(entry.entity_id if entry else config["entity_id"])
        entity_name = (state.name if state else None) or (entry.name or entry.original_name if entry else None) or (entry.entity_id if entry else config["entity_id"])
    if template:
        text = template.replace("{subtype}", subtype_text).replace("{entity_name}", entity_name)
    elif subtype:
        text = f'"{subtype_text}" {kind.replace("_", " ")}'
    else:
        text = " ".join(filter(None, [entity_name, kind.replace("_", " ")]))
    return text.strip()[:96] or kind


class DeviceTriggers:
    """Each device's triggers, read once and again when the registries change, and the ones attached for CLM."""

    def __init__(self, hass):
        self.hass = hass
        self._found: dict[str, list[dict]] = {}
        self._attached: dict[str, object] = {}
        # Per device: the trigger that fired last since starting, and when, as CLM is sent it.
        self.last: dict[str, dict] = {}
        # Main entity → device reference, for the devices attached.
        self.mains: dict[str, str] = {}

    def forget(self, device: str | None = None) -> None:
        """Read the triggers again next time, for one device or all: a trigger Home Assistant learnt since (Zigbee2MQTT
        lists one the first time it fires) is then offered and listened to. The next exchange attaches them again."""
        for known in ([device] if device else list(self._found)):
            # A device not read yet costs nothing to forget; one read is read again on its own next time.
            self._found.pop(known, None)
            if (remove := self._attached.pop(known, None)) is not None:
                remove()

    async def triggers(self, device_ids) -> dict[str, list[dict]]:
        """Device id → [{key, label, config}], for those with triggers. Unknown ones are read in one pass."""
        wanted = [device for device in device_ids if device not in self._found]
        if wanted:
            try:
                # Asked for every device, Home Assistant skips those without triggers; asked for one, it raises.
                found = await async_get_device_automations(self.hass, DeviceAutomationType.TRIGGER, None if len(wanted) > 1 else wanted)
            except HomeAssistantError:
                found = {}
            domains = {config.get("domain") for configs in found.values() for config in configs if config.get("domain")}
            translations = await async_get_translations(self.hass, self.hass.config.language or "en", "device_automation", domains) if domains else {}
            for device in {*wanted, *found}:
                rows, keys = [], set()
                for config in found.get(device, [])[:MAX_TRIGGERS * 2]:
                    key = trigger_key(config)
                    if key not in keys:
                        keys.add(key)
                        rows.append({"key": key, "label": await _label(self.hass, config, translations), "config": dict(config)})
                self._found[device] = rows[:MAX_TRIGGERS]
        return {device: self._found.get(device, []) for device in device_ids}

    async def catalogue(self, user) -> list[dict]:
        """The devices this account could use: those with triggers, named as in Home Assistant, with their triggers."""
        devices = [device for device in dr.async_get(self.hass).devices if device.disabled_by is None and reference(device)]
        found = await self.triggers([device.id for device in devices])
        rows = []
        for device in devices:
            triggers, ref = found.get(device.id) or [], reference(device)
            if not triggers or not permitted(self.hass, user, ref):
                continue
            rows.append({"id": ref, "label": label(self.hass, ref), "domain": "device", "traits": self.traits(ref, triggers)})
        return rows

    def traits(self, ref: str, triggers: list[dict]) -> dict:
        """Its triggers, and what its main entity is and can do (a light's colour modes, a cover's features)."""
        from .entities import traits as entity_traits
        found: dict = {}
        if (entity := main_entity(self.hass, ref)) and (state := self.hass.states.get(entity)):
            found = {**entity_traits(state), "kind": state.domain}
        listed = [{"key": trigger["key"], "label": trigger["label"]} for trigger in triggers]
        # CLM keeps at most 2 KB of what a device can do: the triggers give way first.
        while listed and len(json.dumps({**found, "triggers": listed})) > 1900:
            listed.pop()
        return {**found, "triggers": listed}

    async def attach(self, references: set[str]) -> None:
        """Listens to the triggers of exactly these devices; a device no longer named is let go."""
        wanted = {device_id(ref) for ref in references if is_device(ref)}
        # A device's main entity changing (a light switched) changes the device as CLM reads it.
        self.mains = {entity: PREFIX + device for device in wanted if (entity := main_entity(self.hass, PREFIX + device))}
        for device in list(self._attached):
            if device not in wanted:
                self._attached.pop(device)()
        found = await self.triggers(sorted(wanted - set(self._attached)))
        for device, triggers in found.items():
            configs = []
            for trigger in triggers:
                try:
                    configs.extend(await async_validate_trigger_config(self.hass, [{**trigger["config"], "id": trigger["key"]}]))
                except (vol.Invalid, HomeAssistantError) as error:
                    _LOGGER.debug("Skipping trigger %s of device %s: %s", trigger["key"], device, error)
            labels = {trigger["key"]: trigger["label"] for trigger in triggers}

            @callback
            def fired(variables, context=None, device=device, labels=labels):
                key = (variables.get("trigger") or {}).get("id")
                if key in labels:
                    self.last[device] = {"at": datetime.now(UTC).isoformat(), "key": key, "label": labels[key]}
                    self.hass.bus.async_fire(EVENT_FIRED, {"device_id": device})

            remove = await async_initialize_triggers(self.hass, configs, fired, DOMAIN, f"CLM device {device}", self._log) if configs else None
            if remove:
                self._attached[device] = remove

    def detach(self) -> None:
        for remove in self._attached.values():
            remove()
        self._attached.clear()

    @staticmethod
    def _log(level, message, **kwargs) -> None:
        _LOGGER.log(level, message, **kwargs)


def snapshot(hass, reference_id: str, fired: dict | None) -> dict:
    """A device as CLM reads it: its main entity's state and attributes (a light's on and brightness), and which trigger
    fired last since starting and when. A device with no main entity has the time it last fired as its state."""
    from .entities import snapshot as entity_snapshot
    entity = main_entity(hass, reference_id)
    state = hass.states.get(entity) if entity else None
    main = entity_snapshot(state) if state else None
    attributes = {**(main["attributes"] if main else {}), **({"trigger": fired["key"], "label": fired["label"], "triggered": fired["at"]} if fired else {})}
    while len(json.dumps(attributes, default=str)) > 4000 and main and main["attributes"]:
        main["attributes"].popitem()
        attributes = {**main["attributes"], **({"trigger": fired["key"], "label": fired["label"], "triggered": fired["at"]} if fired else {})}
    value = main["state"] if main else fired["at"] if fired else "unknown"
    changed = max(filter(None, [main["changed"] if main else None, fired["at"] if fired else None]), default=datetime.now(UTC).isoformat())
    return {"id": reference_id, "state": value, "attributes": attributes, "changed": changed}
