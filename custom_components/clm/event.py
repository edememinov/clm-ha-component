"""Button presses that Home Assistant reports only as device triggers, as event entities CLM can be given.

Remotes and wall switches paired through ZHA, deCONZ and some other integrations report a press only as a device
trigger ("Button 1 short press"), never as an entity, so nothing about them could be shared with CLM. Each such device
gets one "Button press" event entity here, attached to that device: every press of the device fires it with the press
as its event type, which CLM workflows start on like any other device. Devices that already have an event entity
(Zigbee2MQTT, Matter, Hue, Shelly and most current integrations) are left alone; theirs is used instead.
"""

import logging

import probatio as vol
from homeassistant.components.device_automation import DeviceAutomationType, async_get_device_automations
from homeassistant.components.event import EventDeviceClass, EventEntity
from homeassistant.const import EVENT_HOMEASSISTANT_STARTED
from homeassistant.core import CoreState, callback
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers import device_registry as dr, entity_registry as er
from homeassistant.helpers.trigger import async_initialize_triggers, async_validate_trigger_config

from .const import DOMAIN

_LOGGER = logging.getLogger(__name__)
# Device triggers that say something about the device rather than a press of it.
_NOT_PRESSES = {"device_offline", "device_online", "connected", "disconnected"}


def press_name(trigger: dict) -> str | None:
    """The event type a device trigger becomes: "button_1_short_press" for ZHA's remote_button_short_press on
    button_1, "1_single" for an action trigger. None for a trigger that is not a press: one that watches an entity
    (whose state CLM reads directly) or reports the device going offline."""
    kind, subtype = str(trigger.get("type") or ""), str(trigger.get("subtype") or "")
    if "entity_id" in trigger or trigger.get("platform") != "device" or not kind or kind in _NOT_PRESSES:
        return None
    kind = kind.removeprefix("remote_button_")
    parts = [subtype] if kind in ("action", "event", "button") and subtype else [subtype, kind]
    name = "_".join(part for part in parts if part).replace(" ", "_").lower()
    return name[:64] or None


def presses(triggers: list[dict]) -> dict[str, dict]:
    """A device's press triggers by event type; the first trigger wins when two read the same."""
    found: dict[str, dict] = {}
    for trigger in triggers:
        if (name := press_name(trigger)) and name not in found:
            found[name] = trigger
    return dict(list(found.items())[:64])


async def async_setup_entry(hass, entry, async_add_entities):
    known: set[str] = set()

    async def scan(device_ids=None):
        devices, registry = dr.async_get(hass), er.async_get(hass)
        with_events = {item.device_id for item in registry.entities.values() if item.domain == "event" and item.device_id and item.platform != DOMAIN}
        wanted = {device.id for device in devices.devices if device.id not in known and device.disabled_by is None
                  and device.id not in with_events and (device_ids is None or device.id in device_ids)}
        if not wanted:
            return
        try:
            # Asked for every device, Home Assistant skips the ones without triggers; asked for one, it raises.
            found = await async_get_device_automations(hass, DeviceAutomationType.TRIGGER, device_ids)
        except HomeAssistantError:
            return
        added = []
        for device_id, triggers in found.items():
            if device_id in wanted and (device := devices.async_get(device_id)) and (kinds := presses(triggers)):
                known.add(device_id)
                added.append(ButtonPresses(device, kinds))
        if added:
            async_add_entities(added)

    @callback
    def changed(event):
        if event.data.get("action") == "remove":
            known.discard(event.data.get("device_id"))
        elif event.data.get("device_id"):
            # A device paired or changed: only it is looked at, never the whole registry again.
            entry.async_create_background_task(hass, scan({event.data["device_id"]}), "clm button presses")

    entry.async_on_unload(hass.bus.async_listen(dr.EVENT_DEVICE_REGISTRY_UPDATED, changed))
    # Integrations such as ZHA list their devices' triggers only once they are set up.
    if hass.state is CoreState.running:
        await scan()
    else:
        async def started(_event):
            await scan()
        entry.async_on_unload(hass.bus.async_listen_once(EVENT_HOMEASSISTANT_STARTED, started))


class ButtonPresses(EventEntity):
    """One device's presses: fired with the press as the event type, each time the device reports one."""

    _attr_has_entity_name = True
    _attr_translation_key = "button_press"
    _attr_device_class = EventDeviceClass.BUTTON
    _attr_should_poll = False

    def __init__(self, device, kinds: dict[str, dict]):
        self._triggers = kinds
        self._attr_unique_id = f"button_press_{device.id}"
        self._attr_event_types = list(kinds)
        # Attached to the device as it is: the device stays its own integration's, this only adds an entity to it.
        self.device_entry = device

    async def async_added_to_hass(self) -> None:
        await super().async_added_to_hass()
        configs = []
        for name, trigger in self._triggers.items():
            try:
                configs.extend(await async_validate_trigger_config(self.hass, [{**trigger, "id": name}]))
            except (vol.Invalid, HomeAssistantError) as error:
                _LOGGER.debug("Skipping press %s of %s: %s", name, self.entity_id, error)
        remove = await async_initialize_triggers(self.hass, configs, self._pressed, DOMAIN, self.entity_id or "button press", self._log) if configs else None
        if remove:
            self.async_on_remove(remove)

    @callback
    def _pressed(self, variables, context=None) -> None:
        name = (variables.get("trigger") or {}).get("id")
        if name in self._triggers:
            self._trigger_event(name)
            self.async_write_ha_state()

    @staticmethod
    def _log(level, message, **kwargs) -> None:
        _LOGGER.log(level, message, **kwargs)
